"""Live sources for the events calendar: weather, OM home matches at the Vélodrome, concert venues (ICS).

    weather   Open-Meteo, Marseille: the daily history since the first order (archive API) and the 16-day forecast.
              A day beyond a threshold (about the 5-8 % tails of the last three years) is a weather event: cold
              (max ≤ 11 °C), heat (max ≥ 32 °C), rain (≥ 10 mm), wind (strongest wind of the day ≥ 40 km/h).
              Consecutive days make one event.
    matches   openfootball (public Ligue 1 fixtures, JSON): Olympique de Marseille's home matches, past and to come.
              The event covers the match day and the day before (the snack shops are delivered in the morning).
    concerts  any ICS feed listed in CALENDAR_ICS (« type|lieu|url » separated by « ; », e.g. a venue's agenda for
              Le Dôme): one event per VEVENT, its day only.

They become ordinary calendar rows (marked `feed`), with no `estimate`: their uplift per segment is MEASURED on
the order history like ramadan's (services/demand.py). An event type with no measurable effect says so.

The raw data is cached in SQLite (feed_weather, feed_items): a reset of the ERP, an offline start or a dead source
keeps the last data. Network calls happen on use (ensure(), like the road times in geo.py), not at boot; after a
failure the source is left alone for OFFLINE_RETRY_S. FEEDS=off: no network at all (tests, offline demos).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime

from app import crud
from app.core.activity import today
from app.core.db import Doc, conn
from app.core.utils import add_days

log = logging.getLogger("api")

FEEDS = os.environ.get("FEEDS", "on") != "off"
LAT, LNG = 43.2965, 5.3698  # Marseille
TIMEOUT_S = 6.0
OFFLINE_RETRY_S = 600
FORECAST_TTL_S = 6 * 3600
FIXTURES_TTL_S = 24 * 3600
FIRST_DAY = "2023-10-01"  # a few weeks before the first order: the baselines before the first events

WEATHER = {  # type → (title, test on the day, pitch)
    "froid": ("Froid", lambda d: d["tmax"] is not None and d["tmax"] <= 11, "le froid change ce que vos clients prennent"),
    "chaleur": ("Chaleur", lambda d: d["tmax"] is not None and d["tmax"] >= 32, "avec la chaleur annoncée"),
    "pluie": ("Pluie", lambda d: d["precip"] is not None and d["precip"] >= 10, "avec la pluie annoncée"),
    "vent": ("Mistral", lambda d: d["wind"] is not None and d["wind"] >= 40, "avec le mistral annoncé"),
}
OM = "Olympique de Marseille"

conn.execute("CREATE TABLE IF NOT EXISTS feed_weather (date TEXT PRIMARY KEY, data TEXT NOT NULL)")
conn.execute("CREATE TABLE IF NOT EXISTS feed_items (id TEXT PRIMARY KEY, feed TEXT NOT NULL, data TEXT NOT NULL)")
conn.execute("CREATE TABLE IF NOT EXISTS feed_state (name TEXT PRIMARY KEY, at REAL NOT NULL)")
_offline: dict[str, float] = {}


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "voice-erp-kit"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
        return r.read()


fetch: Callable[[str], bytes] = _get  # tests replace it


def _fresh(name: str, ttl: float) -> bool:
    row = conn.execute("SELECT at FROM feed_state WHERE name = ?", (name,)).fetchone()
    return bool(row) and time.time() - row[0] < ttl


def _done(name: str) -> None:
    conn.execute("INSERT OR REPLACE INTO feed_state (name, at) VALUES (?, ?)", (name, time.time()))


Job = tuple[str, list[str], Callable[[list[bytes | None]], None]]  # name, urls, apply(payloads)


def _due(name: str, ttl: float) -> bool:
    return FEEDS and not _fresh(name, ttl) and time.time() >= _offline.get(name, 0)


def _download(urls: list[str]) -> list[bytes | None]:
    """In a worker thread: HTTP only, no database."""
    out: list[bytes | None] = []
    for u in urls:
        try:
            out.append(fetch(u))
        except Exception as e:  # noqa: BLE001  the job decides whether a missing payload is fatal
            log.warning("feed %s: %s", u.split("?")[0], e.__class__.__name__)
            out.append(None)
    return out


# ── weather ──

DAILY = "temperature_2m_max,temperature_2m_min,precipitation_sum,wind_speed_10m_max"


def _store_weather(j: Doc, kind: str) -> None:
    d = j["daily"]
    rows = []
    for i, day in enumerate(d["time"]):
        w = {"date": day, "tmax": d["temperature_2m_max"][i], "tmin": d["temperature_2m_min"][i],
             "precip": d["precipitation_sum"][i], "wind": d["wind_speed_10m_max"][i], "kind": kind}  # fmt: skip
        if w["tmax"] is not None:
            rows.append((day, json.dumps(w)))
    conn.executemany("INSERT OR REPLACE INTO feed_weather (date, data) VALUES (?, ?)", rows)


def _weather_history() -> Job | None:
    last = conn.execute("SELECT max(date) FROM feed_weather WHERE data LIKE '%\"archive\"%'").fetchone()[0]
    start = add_days(last, 1) if last else FIRST_DAY
    end = add_days(today(), -2)  # the archive lags a couple of days; the forecast covers them
    if start > end:
        return None
    url = (f"https://archive-api.open-meteo.com/v1/archive?latitude={LAT}&longitude={LNG}&start_date={start}"
           f"&end_date={end}&daily={DAILY}&timezone=Europe/Paris")  # fmt: skip
    return ("weather-history", [url], lambda p: _store_weather(json.loads(_need(p[0])), "archive"))


def _weather_forecast() -> Job:
    url = (f"https://api.open-meteo.com/v1/forecast?latitude={LAT}&longitude={LNG}&daily={DAILY}"
           f"&timezone=Europe/Paris&past_days=3&forecast_days=16")  # fmt: skip

    def apply(p: list[bytes | None]) -> None:
        j = json.loads(_need(p[0]))
        archived = {r[0] for r in conn.execute("SELECT date FROM feed_weather WHERE data LIKE '%\"archive\"%'")}
        times = j["daily"]["time"]
        j["daily"] = {k: [v for day, v in zip(times, vals) if day not in archived] for k, vals in j["daily"].items()}
        conn.execute("DELETE FROM feed_weather WHERE data LIKE '%\"forecast\"%'")  # yesterday's forecast is stale
        _store_weather(j, "forecast")

    return ("weather-forecast", [url], apply)


def _need(payload: bytes | None) -> bytes:
    if payload is None:
        raise ConnectionError("no answer")
    return payload


def weather_days() -> dict[str, Doc]:
    return {r[0]: json.loads(r[1]) for r in conn.execute("SELECT date, data FROM feed_weather ORDER BY date")}


def weather_events() -> list[Doc]:
    days, out = weather_days(), []
    for typ, (title, hit, pitch) in WEATHER.items():
        run: list[Doc] = []
        for day in [*sorted(days), None]:
            w = days.get(day) if day else None
            if w and hit(w) and (not run or add_days(run[-1]["date"], 1) == day):
                run.append(w)
                continue
            if run:
                lo, hi = run[0]["date"], run[-1]["date"]
                extreme = min(x["tmax"] for x in run) if typ == "froid" else max(x["tmax"] for x in run)
                detail = {"froid": f"{extreme:.0f} °C max", "chaleur": f"{extreme:.0f} °C max",
                          "pluie": f"{max(x['precip'] for x in run):.0f} mm", "vent": f"{max(x['wind'] for x in run):.0f} km/h"}[typ]  # fmt: skip
                forecast = any(x["kind"] == "forecast" for x in run)
                out.append({"id": f"meteo-{typ}-{lo}", "type": typ, "kind": "weather", "title": f"{title} ({detail})",
                            "date": lo, "time": None, "affects": {"from": lo, "to": hi}, "segments": [], "pitch": pitch,
                            "source": "prévision Open-Meteo" if forecast else "relevé Open-Meteo", "feed": "open-meteo"})  # fmt: skip
            run = [w] if w and hit(w) else []
    return out


# ── OM home matches (openfootball) ──


def _seasons() -> list[str]:
    y0, y1 = int(FIRST_DAY[:4]), int(today()[:4]) + 1
    return [f"{y}-{str(y + 1)[2:]}" for y in range(y0, y1)]


def _fixtures() -> Job:
    seasons = _seasons()

    def apply(p: list[bytes | None]) -> None:
        if any(x is None for x in p[:-1]):  # the next season may not be published yet; a past one must be there
            raise ConnectionError("a past season is missing")
        rows = []
        for payload in p:
            if payload is None:
                continue
            j = json.loads(payload)
            for m in j.get("matches") or [m for r in j.get("rounds", []) for m in r["matches"]]:
                if m.get("team1") == OM and m.get("date"):
                    rows.append((f"om-{m['date']}", "openfootball", json.dumps({"date": m["date"], "time": m.get("time"), "opponent": m["team2"]})))
        conn.execute("DELETE FROM feed_items WHERE feed = 'openfootball'")
        conn.executemany("INSERT OR REPLACE INTO feed_items (id, feed, data) VALUES (?, ?, ?)", rows)

    return ("fixtures", [f"https://raw.githubusercontent.com/openfootball/football.json/master/{x}/fr.1.json" for x in seasons], apply)


def _short(team: str) -> str:
    return re.sub(r"\b(FC|AC|SC|RC|AS|OGC|SM|1901|29|Alsace)\b|Olympique( de)?|Stade( de)?", "", team).strip(" -") or team


def match_events() -> list[Doc]:
    out = []
    for id_, data in conn.execute("SELECT id, data FROM feed_items WHERE feed = 'openfootball' ORDER BY id"):
        m = json.loads(data)
        out.append({"id": id_, "type": "om_home", "kind": "sport", "title": f"OM – {_short(m['opponent'])} au Vélodrome",
                    "date": m["date"], "time": m["time"], "affects": {"from": add_days(m["date"], -1), "to": m["date"]},
                    "segments": [], "pitch": "soir de match au Vélodrome", "source": "calendrier Ligue 1 (openfootball)",
                    "feed": "openfootball"})  # fmt: skip
    return out


# ── concert venues and any other agenda (ICS) ──


def ics_feeds() -> list[tuple[str, str, str]]:
    """CALENDAR_ICS = « concert|Le Dôme|https://…/agenda.ics ; … » → [(type, venue, url)]."""
    out = []
    for part in os.environ.get("CALENDAR_ICS", "").split(";"):
        bits = [b.strip() for b in part.split("|")]
        if len(bits) == 3 and all(bits):
            out.append((bits[0], bits[1], bits[2]))
    return out


def parse_ics(text: str) -> list[Doc]:
    """[{uid, date, time, summary}] from an ICS file: VEVENTs with a DTSTART; folded lines unfolded."""
    text = re.sub(r"\r?\n[ \t]", "", text)
    out, cur = [], None
    for line in text.splitlines():
        if line == "BEGIN:VEVENT":
            cur = {}
        elif line == "END:VEVENT" and cur is not None:
            if cur.get("date"):
                out.append(cur)
            cur = None
        elif cur is not None and ":" in line:
            key, val = line.split(":", 1)
            name = key.split(";")[0].upper()
            if name == "DTSTART" and (m := re.match(r"(\d{4})(\d{2})(\d{2})(?:T(\d{2})(\d{2}))?", val)):
                cur["date"] = f"{m[1]}-{m[2]}-{m[3]}"
                cur["time"] = f"{m[4]}:{m[5]}" if m[4] else None
            elif name == "SUMMARY":
                cur["summary"] = val.replace("\\,", ",").replace("\\;", ";").strip()
            elif name == "UID":
                cur["uid"] = val.strip()
    return out


def _ics() -> Job:
    feeds = ics_feeds()

    def apply(p: list[bytes | None]) -> None:
        for (typ, venue, _), payload in zip(feeds, p):
            if payload is None:
                continue  # that venue's agenda stays as it was
            events = parse_ics(payload.decode("utf-8", "replace"))
            conn.execute("DELETE FROM feed_items WHERE feed = ?", (f"ics:{typ}",))
            conn.executemany("INSERT OR REPLACE INTO feed_items (id, feed, data) VALUES (?, ?, ?)", [
                (f"{typ}-{hashlib.sha1((e.get('uid') or e['date'] + e.get('summary', '')).encode()).hexdigest()[:10]}", f"ics:{typ}",
                 json.dumps({**e, "type": typ, "venue": venue})) for e in events])  # fmt: skip

    return ("ics", [u for _, _, u in feeds], apply)


def ics_events() -> list[Doc]:
    out = []
    for id_, data in conn.execute("SELECT id, data FROM feed_items WHERE feed LIKE 'ics:%' ORDER BY id"):
        e = json.loads(data)
        out.append({"id": id_, "type": e["type"], "kind": "concert", "title": f"{e.get('summary') or 'Événement'} ({e['venue']})",
                    "date": e["date"], "time": e.get("time"), "affects": {"from": e["date"], "to": e["date"]}, "segments": [],
                    "pitch": f"soir d'événement à {e['venue']}", "source": f"agenda {e['venue']} (ICS)", "feed": f"ics:{e['type']}"})  # fmt: skip
    return out


# ── into the calendar ──


def materialize() -> int:
    """Rewrite the feed rows of the calendar from the cache (no network). Returns how many there are."""
    rows = [*weather_events(), *match_events(), *ics_events()]
    old = {k for k, e in crud.calendar.rows.items() if e.get("feed")}
    new = {e["id"]: e for e in rows}
    for k in old - set(new):
        crud.calendar.table.remove(k)
    for k, e in new.items():
        if crud.calendar.rows.get(k) != e:
            crud.calendar.table.put(e)
    return len(new)


async def ensure() -> None:
    """Refresh what is stale (the downloads run in a worker thread), then the calendar. Cheap when all is fresh."""
    jobs: list[Job] = []
    if _due("weather-history", FORECAST_TTL_S) and (j := _weather_history()):
        jobs.append(j)
    if _due("weather-forecast", FORECAST_TTL_S):
        jobs.append(_weather_forecast())
    if _due("fixtures", FIXTURES_TTL_S):
        jobs.append(_fixtures())
    if ics_feeds() and _due("ics", FIXTURES_TTL_S):
        jobs.append(_ics())
    ran = False
    if jobs:
        payloads = await asyncio.to_thread(lambda: [_download(urls) for _, urls, _ in jobs])
        for (name, _, apply), p in zip(jobs, payloads):
            try:
                apply(p)
            except Exception as e:  # noqa: BLE001  keep the cache, leave the source alone for a while
                _offline[name] = time.time() + OFFLINE_RETRY_S
                log.warning("feed %s unavailable (%s: %s): last data kept", name, e.__class__.__name__, e)
                continue
            _done(name)
            ran = True
    if ran or not any(e.get("feed") for e in crud.calendar.rows.values()):
        materialize()


def status() -> Doc:
    """GET /api/radar/feeds: what each source holds and when it was last fetched."""
    state = {r[0]: datetime.fromtimestamp(r[1], UTC).isoformat(timespec="seconds") for r in conn.execute("SELECT name, at FROM feed_state")}
    w = weather_days()
    return {
        "enabled": FEEDS,
        "weather": {"days": len(w), "from": min(w, default=None), "to": max(w, default=None), "fetchedAt": state.get("weather-forecast")},
        "matches": {"count": len(match_events()), "fetchedAt": state.get("fixtures")},
        "ics": [{"type": t, "venue": v, "events": sum(e["type"] == t for e in ics_events())} for t, v, _ in ics_feeds()],
        "icsFetchedAt": state.get("ics"),
        "calendarRows": sum(1 for e in crud.calendar.rows.values() if e.get("feed")),
    }

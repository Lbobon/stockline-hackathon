"""Self-check for the live calendar sources (services/feeds.py) with fake payloads: no network.

    cd apps/backend && DATA_DIR=data uv run python tests/test_feeds.py
"""

import json
import os
import sys
import tempfile
from pathlib import Path

os.environ["DB_PATH"] = str(Path(tempfile.mkdtemp()) / "erp.db")
os.environ["CALENDAR_ICS"] = "concert|Le Dôme|https://agenda.example/dome.ics"
os.environ.pop("FEEDS", None)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from app.core.utils import add_days
from app.main import app
from app.services import feeds

calls: list[str] = []
down = False


def daily(start: str, end: str) -> dict:
    days, d = [], start
    while d <= end:
        days.append(d)
        d = add_days(d, 1)
    cold = lambda d: d[5:7] == "01" and int(d[8:]) % 9 == 0
    return {"daily": {"time": days, "temperature_2m_max": [8.0 if cold(d) else 17.0 for d in days],
                      "temperature_2m_min": [3.0] * len(days), "precipitation_sum": [0.0] * len(days),
                      "wind_speed_10m_max": [12.0] * len(days)}}  # fmt: skip


def fake(url: str) -> bytes:
    calls.append(url)
    if down:
        raise TimeoutError("offline")
    q = dict(p.split("=", 1) for p in url.split("?", 1)[1].split("&")) if "?" in url else {}
    if "archive-api" in url:
        return json.dumps(daily(q["start_date"], q["end_date"])).encode()
    if "api.open-meteo.com" in url:
        j = daily("2026-11-21", "2026-12-09")
        j["daily"]["temperature_2m_max"][5] = 9.0  # 26/11: cold, forecast
        return json.dumps(j).encode()
    if "openfootball" in url:
        y = int(url.split("/")[-2][:4])
        if y == 2026:
            raise FileNotFoundError("next season not published")  # the last season may be missing: tolerated
        ms = [{"date": add_days(f"{y}-09-06", 14 * k), "time": "21:00", "team1": feeds.OM if k % 2 == 0 else "Paris FC",
               "team2": "Paris FC" if k % 2 == 0 else feeds.OM} for k in range(18)]  # fmt: skip
        return json.dumps({"name": f"Ligue 1 {y}", "matches": ms}).encode()
    if "dome.ics" in url:
        return (b"BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:a1\r\nDTSTART:20261127T200000\r\nSUMMARY:Concert de rap\\, complet\r\n"
                b"END:VEVENT\r\nBEGIN:VEVENT\r\nUID:a2\r\nDTSTART;VALUE=DATE:20261205\r\nSUMMARY:Gala de \r\n boxe\r\nEND:VEVENT\r\n"
                b"END:VCALENDAR\r\n")  # fmt: skip
    raise AssertionError(url)


feeds.fetch = fake

with TestClient(app) as c:
    st = c.get("/api/radar/feeds").json()
    assert st["enabled"] and st["weather"]["days"] > 1000 and st["weather"]["from"] == feeds.FIRST_DAY, st
    assert st["matches"]["count"] == 27 and st["ics"] == [{"type": "concert", "venue": "Le Dôme", "events": 2}], st
    om = c.get("/api/radar/calendar", params={"type": "om_home"}).json()
    assert om[0]["title"] == "OM – Paris au Vélodrome" and om[0]["affects"] == {"from": "2023-09-05", "to": "2023-09-06"}
    # real fixtures are measured on the history, not guessed; the demo's OM–PSG night (25/11, seed) is the one estimate
    assert all("estimate" not in e and e["feed"] == "openfootball" for e in om if e["id"] != "om-psg")
    assert [e for e in om if e["id"] == "om-psg"][0]["estimate"] and "feed" not in [e for e in om if e["id"] == "om-psg"][0]
    cold = c.get("/api/radar/calendar", params={"type": "froid"}).json()
    assert cold and cold[-1]["id"] == "meteo-froid-2026-11-26" and cold[-1]["source"] == "prévision Open-Meteo", cold[-1]
    assert cold[-1]["title"] == "Froid (9 °C max)"
    dome = c.get("/api/radar/calendar", params={"type": "concert"}).json()
    assert [(e["date"], e["time"], e["title"]) for e in dome] == [
        ("2026-11-27", "20:00", "Concert de rap, complet (Le Dôme)"), ("2026-12-05", None, "Gala de boxe (Le Dôme)")]

    # The forecast shows them with what the history says about them (a measure, or « no measurable effect »)
    f = c.get("/api/radar/forecast", params={"date": "2026-11-26"}).json()
    ev = {e["id"]: e for e in f["events"]}
    assert "meteo-froid-2026-11-26" in ev and ev["meteo-froid-2026-11-26"]["measured"] is not None
    assert not ev["meteo-froid-2026-11-26"]["basis"].startswith("estimation")

    # Fresh: no second download. A reset of the ERP keeps them (their cache is not in the data pack).
    n = len(calls)
    c.get("/api/radar/forecast", params={"date": "2026-11-26"})
    assert len(calls) == n, calls[n:]
    assert c.post("/api/admin/reset").status_code == 200
    assert len(c.get("/api/radar/calendar", params={"type": "om_home"}).json()) == 27 + 1  # + the seed OM–PSG night

    # A source down: the last data stays, no error for the user, not retried on every request
    down = True
    feeds.conn.execute("DELETE FROM feed_state")
    assert c.get("/api/radar/forecast", params={"date": "2026-11-26"}).status_code == 200
    n = len(calls)
    c.get("/api/radar/forecast", params={"date": "2026-11-26"})
    assert len(calls) == n and len(c.get("/api/radar/calendar", params={"type": "om_home"}).json()) == 27 + 1  # + the seed OM–PSG night

print("feeds OK:", {k: v for k, v in st.items() if k != "enabled"})

"""Where clients are, and how long the trucks take between them.

No geocoding service: the data is fictional, so a client sits at the centre of its postcode plus a small fixed
offset derived from its id (same place on every run).
shortcut: postcode centroids, not real addresses; plug a geocoder in `locate` if real addresses ever come in.

Distances and times are REAL road ones: `prefetch(points)` asks OSRM (its /table service, the same public router the
web map uses for the drawn roads) for the whole matrix in one call and keeps every leg in the `road_legs` SQLite table,
so a client pair is fetched once, ever. `km` / `minutes` read that cache; a leg not in it (offline, OSRM down,
ROAD_TIMES=estimate) falls back to the estimate: straight line × ROAD_FACTOR at CITY_KMH / ROAD_KMH.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import time
import urllib.request

from app.core.db import Doc, conn

log = logging.getLogger("api")

DEPOT = {"name": "MIN des Arnavaux", "lat": 43.3330, "lng": 5.3870}  # 47 chemin de la Madrague-Ville, 13014

# Approximate centres (WGS84) of the postcodes in the data.
POSTCODES: dict[str, tuple[float, float]] = {
    "13001": (43.2999, 5.3841), "13002": (43.3110, 5.3650), "13003": (43.3125, 5.3800), "13004": (43.3060, 5.4010),
    "13005": (43.2930, 5.3990), "13009": (43.2500, 5.4300), "13010": (43.2770, 5.4230), "13011": (43.2880, 5.4830),
    "13012": (43.3080, 5.4400), "13013": (43.3490, 5.4330), "13014": (43.3440, 5.3930), "13015": (43.3580, 5.3630),
    "13016": (43.3620, 5.3150), "13090": (43.5250, 5.4250), "13100": (43.5290, 5.4470), "13127": (43.4600, 5.2480),
    "13130": (43.4780, 5.1690), "13300": (43.6400, 5.0970), "13400": (43.2920, 5.5700), "13500": (43.4050, 5.0530),
    "13600": (43.1750, 5.6050), "13700": (43.4160, 5.2150), "83000": (43.1240, 5.9280), "83400": (43.1200, 6.1300),
    "83500": (43.1010, 5.8780),
}  # fmt: skip

ROAD_FACTOR = 1.35
CITY_KMH, ROAD_KMH = 30, 55  # inside Marseille / elsewhere
DROP_MIN = 8  # unload, sign, chat
EUR_PER_KM = 0.55  # fuel + wear of a refrigerated van
EUR_PER_HOUR = 30.0  # driver


def postcode_of(client: Doc) -> str | None:
    m = re.search(r"\b(\d{5})\b", client.get("address") or "")
    return m.group(1) if m else None


def locate(client: Doc) -> Doc:
    """{lat, lng, postcode} of a client (postcode centre + a fixed offset of up to ~600 m in town, ~1.5 km outside)."""
    pc = postcode_of(client)
    lat, lng = POSTCODES.get(pc or "", (DEPOT["lat"], DEPOT["lng"]))
    h = hashlib.sha256(client["id"].encode()).digest()
    spread = 0.006 if (pc or "").startswith("130") else 0.014
    return {"lat": round(lat + (h[0] / 255 - 0.5) * spread, 5), "lng": round(lng + (h[1] / 255 - 0.5) * spread * 1.4, 5), "postcode": pc}


# ── road legs (OSRM), cached ──

ROAD_TIMES = os.environ.get("ROAD_TIMES", "osrm")  # osrm | estimate
OSRM_URL = os.environ.get("OSRM_URL", "https://router.project-osrm.org").rstrip("/")
OSRM_TIMEOUT_S = 4.0
OSRM_CHUNK = 50  # the public server caps a table request at 100 coordinates
OFFLINE_RETRY_S = 300  # after a failure, use the estimate for 5 min instead of waiting on every request

conn.execute("CREATE TABLE IF NOT EXISTS road_legs (k TEXT PRIMARY KEY, km REAL NOT NULL, min REAL NOT NULL)")
_legs: dict[str, tuple[float, float]] = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT k, km, min FROM road_legs")}
_offline_until = 0.0


def _pt(p: Doc) -> str:
    return f"{p['lat']:.5f},{p['lng']:.5f}"


def _leg(a: Doc, b: Doc) -> tuple[float, float] | None:
    return _legs.get(f"{_pt(a)}|{_pt(b)}")


def source() -> str:
    """Where the numbers come from right now: « osrm » (real roads) or « estimate »."""
    return "osrm" if ROAD_TIMES == "osrm" and _legs and time.time() >= _offline_until else "estimate"


def prefetch(points: list[Doc]) -> str:
    """Fill the cache with every leg between `points` (one OSRM /table call per 50×50 block of missing legs).
    Returns « osrm » when every leg is a real road one, else « estimate » (offline: the caller still gets numbers)."""
    global _offline_until
    pts = list({_pt(p): p for p in points}.values())
    missing = [p for p in pts if any(_leg(p, q) is None for q in pts if q is not p)]
    if not missing:
        return "osrm" if len(pts) > 1 or _legs else source()
    if ROAD_TIMES != "osrm" or time.time() < _offline_until:
        return "estimate"
    try:
        for i in range(0, len(missing), OSRM_CHUNK):
            src = missing[i : i + OSRM_CHUNK]
            for j in range(0, len(pts), OSRM_CHUNK):
                dst = pts[j : j + OSRM_CHUNK]
                _fetch(src, dst)
                _fetch(dst, src)
    except Exception as e:  # noqa: BLE001  any network / HTTP / format error: estimate until OFFLINE_RETRY_S
        _offline_until = time.time() + OFFLINE_RETRY_S
        log.warning("OSRM unreachable (%s): road estimate for %d min", e.__class__.__name__, OFFLINE_RETRY_S // 60)
        return "estimate"
    return "osrm"


def _fetch(src: list[Doc], dst: list[Doc]) -> None:
    coords = src + [p for p in dst if p not in src]
    idx = {_pt(p): i for i, p in enumerate(coords)}
    path = ";".join(f"{p['lng']:.5f},{p['lat']:.5f}" for p in coords)
    q = (f"sources={';'.join(str(idx[_pt(p)]) for p in src)}&destinations={';'.join(str(idx[_pt(p)]) for p in dst)}"
         "&annotations=duration,distance")  # fmt: skip
    req = urllib.request.Request(f"{OSRM_URL}/table/v1/driving/{path}?{q}", headers={"User-Agent": "voice-erp-kit"})
    with urllib.request.urlopen(req, timeout=OSRM_TIMEOUT_S) as r:
        j = json.loads(r.read())
    if j.get("code") != "Ok":
        raise ValueError(j.get("code"))
    rows = []
    for a, (durs, dists) in zip(src, zip(j["durations"], j["distances"])):
        for b, sec, m in zip(dst, durs, dists):
            if sec is not None and m is not None and _pt(a) != _pt(b):
                _legs[f"{_pt(a)}|{_pt(b)}"] = (m / 1000, sec / 60)
                rows.append((f"{_pt(a)}|{_pt(b)}", m / 1000, sec / 60))
    conn.executemany("INSERT OR REPLACE INTO road_legs (k, km, min) VALUES (?, ?, ?)", rows)


# ── distances and times (road legs when known, else the estimate) ──


def km(a: Doc, b: Doc) -> float:
    """Road km between two {lat, lng}."""
    if (leg := _leg(a, b)) and ROAD_TIMES == "osrm":
        return leg[0]
    return estimate_km(a, b)


def estimate_km(a: Doc, b: Doc) -> float:
    """Straight line × ROAD_FACTOR."""
    la1, lo1, la2, lo2 = map(math.radians, (a["lat"], a["lng"], b["lat"], b["lng"]))
    d = 2 * 6371 * math.asin(math.sqrt(math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2))
    return d * ROAD_FACTOR


def in_marseille(p: Doc) -> bool:
    return str(p.get("postcode") or "13014").startswith("130") and int(str(p.get("postcode") or "13014")[3:]) <= 16


def minutes(a: Doc, b: Doc) -> float:
    """Driving minutes between two points."""
    if (leg := _leg(a, b)) and ROAD_TIMES == "osrm":
        return leg[1]
    speed = CITY_KMH if in_marseille(a) and in_marseille(b) else ROAD_KMH
    return estimate_km(a, b) / speed * 60

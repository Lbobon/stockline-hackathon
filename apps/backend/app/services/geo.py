"""Where clients are, and how long the trucks take between them.

No geocoding service: the data is fictional, so a client sits at the centre of its postcode plus a small fixed
offset derived from its id (same place on every run). Road distance ≈ straight line × ROAD_FACTOR.
shortcut: postcode centroids, not real addresses; upgrade to a geocoder + OSRM if real data ever comes in.
"""

from __future__ import annotations

import hashlib
import math
import re

from app.core.db import Doc

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


def km(a: Doc, b: Doc) -> float:
    """Road km between two {lat, lng}."""
    la1, lo1, la2, lo2 = map(math.radians, (a["lat"], a["lng"], b["lat"], b["lng"]))
    d = 2 * 6371 * math.asin(math.sqrt(math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2))
    return d * ROAD_FACTOR


def in_marseille(p: Doc) -> bool:
    return str(p.get("postcode") or "13014").startswith("130") and int(str(p.get("postcode") or "13014")[3:]) <= 16


def minutes(a: Doc, b: Doc) -> float:
    """Driving minutes between two points."""
    speed = CITY_KMH if in_marseille(a) and in_marseille(b) else ROAD_KMH
    return km(a, b) / speed * 60

"""Self-check for /api/routing on the Médina Viandes seed (fresh temp DB).

    cd apps/backend && DATA_DIR=data uv run python tests/test_dispatch.py
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ["DB_PATH"] = str(Path(tempfile.mkdtemp()) / "erp.db")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


def mins(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


with TestClient(app) as c:
    p = c.get("/api/routing/optimize").json()
    t = p["totals"]
    assert t["after"]["km"] < t["before"]["km"] and t["savedEur"] > 0, t
    before_ids = sorted(s["orderId"] for r in p["current"] for s in r["stops"])
    after_ids = sorted(s["orderId"] for r in p["optimized"] for s in r["stops"])
    assert before_ids == after_ids, "no stop lost or duplicated"
    for r in p["optimized"]:
        assert r["loadKg"] <= r["capacityKg"], r["route"]
        assert mins(r["lastEta"]) <= mins("11:00"), (r["route"], r["lastEta"])
    assert any("below_minimum" in s["flags"] for s in p["costToServe"])  # Snack Nordine, 108 € HT

    # Centre truck (overloaded in the seed) breaks after 4 stops: every remaining stop is rescued or has a fallback.
    centre = next(r for r in p["current"] if r["route"] == "Tournée Centre")
    b = c.post("/api/routing/breakdown", json={"runId": centre["runId"], "afterStop": 4}).json()
    assert b["counts"]["affected"] == len(centre["stops"]) - 4
    for a in b["affected"]:
        assert a["option"] in ("reroute", "dock_pickup", "next_delivery")
        if a["option"] == "reroute":
            assert a["newRunId"] != centre["runId"] and mins(a["newEta"]) >= mins(b["at"]), a
    assert c.post("/api/routing/breakdown", json={"runId": centre["runId"], "afterStop": 99}).status_code == 400

    # Toulon runs Tue/Thu/Sat: a stranded Toulon client is moved to Thursday, not "tomorrow" (Wednesday).
    toulon = next(r for r in p["current"] if r["route"] == "Tournée Toulon")
    bt = c.post("/api/routing/breakdown", json={"runId": toulon["runId"], "afterStop": 1}).json()
    assert all(a.get("nextDate") != "2026-11-25" for a in bt["affected"] if a["option"] == "next_delivery"), bt["affected"]

    # Apply writes the optimized order; a second plan is then (nearly) as short as the optimized one.
    r = c.post("/api/routing/apply", json={}, headers={"x-actor": "user"})
    assert r.status_code == 200, r.text
    again = c.get("/api/routing/optimize").json()["totals"]["before"]["km"]
    assert again <= t["after"]["km"] + 1, (again, t["after"]["km"])

print("dispatch OK:", t["before"]["km"], "→", t["after"]["km"], "km;", b["counts"])

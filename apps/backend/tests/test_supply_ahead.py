"""Self-check for /api/radar/supply/ahead (the 12-week spike forecast) on the Médina Viandes seed (fresh temp DB).

    cd apps/backend && DATA_DIR=data uv run python tests/test_supply_ahead.py
"""

import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

os.environ["DB_PATH"] = str(Path(tempfile.mkdtemp()) / "erp.db")
os.environ["FEEDS"] = "off"  # no weather: the climate column is empty, the model still works without it
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from app.main import app

with TestClient(app) as c:
    a = c.get("/api/radar/supply/ahead", params={"weeks": 12}).json()
    weeks = [w["week"] for w in a["weeks"]]
    # the 12 weeks after the current one (business today 2026-11-24, a Tuesday), Mondays only
    assert weeks[0] == "2026-11-30" and len(weeks) == 12 and all(date.fromisoformat(w).weekday() == 0 for w in weeks), weeks
    by_week = {w["week"]: w for w in a["weeks"]}
    # Ramadan 2027 starts on 8 February: the model sees it coming from the calendar's position, not from last year's date
    assert by_week["2027-02-08"]["vsNormal"] > 0.5, by_week["2027-02-08"]
    # the Christmas holidays are below a normal week (canteens closed, 25 Dec and 1 Jan without deliveries)
    assert by_week["2026-12-28"]["vsNormal"] < 0, by_week["2026-12-28"]
    # every supplier call books one spike week, delivered the Saturday before it, at the supplier's price
    assert a["suppliers"], a["summary"]
    for s in a["suppliers"]:
        assert s["ahead"] and s["week"] in weeks and s["deliverBy"] == (date.fromisoformat(s["week"]) - timedelta(days=2)).isoformat(), s
        assert all(l["qty"] > 0 and l["unitPrice"] > 0 and l["firstDay"] == s["week"] for l in s["lines"]), s
        assert s["reason"].startswith("pic prévu la semaine du"), s["reason"]
    # some spikes are explained by broad patterns only (season, last year, growth, trend), not by a calendar event
    patterns = [w for p in a["products"] for w in p["weeks"] if w["spike"] and not w["events"] and not any(x.startswith("juste avant") for x in w["why"])]
    assert patterns, "no spike outside the event calendar"

    # Walk-forward over the last year: the model beats both simple rules at every horizon, and the version without the
    # event calendar (broad patterns only) still beats them overall.
    bt = c.get("/api/radar/supply/ahead/backtest").json()
    for h, r in bt["horizons"].items():
        assert r["model"]["error"] < r["normal"]["error"] and r["model"]["error"] < r["lastYear"]["error"], (h, r)
    allh = bt["horizons"]["tout"]
    assert allh["patternsOnly"]["error"] < allh["normal"]["error"] and allh["patternsOnly"]["error"] < allh["lastYear"]["error"], allh
    assert allh["model"]["extraCaught"] > allh["lastYear"]["extraCaught"], allh
    # A booking for February (what the 12-week calls send) is not stock arriving this week: the 7-day plan keeps its
    # suppliers. Before the fix every sent order counted as « en route » and the week showed a single supplier.
    week = c.get("/api/radar/supply").json()
    top = week["suppliers"][0]
    po = c.post("/api/purchase-orders", json={"supplierId": top["supplierId"], "expectedDate": "2027-02-13",
                                             "lines": [{"productId": l["productId"], "qty": l["qty"] * 20} for l in top["lines"]]}).json()  # fmt: skip
    c.post(f"/api/purchase-orders/{po['id']}/send")
    after = c.get("/api/radar/supply").json()
    assert [s["supplierId"] for s in after["suppliers"]] == [s["supplierId"] for s in week["suppliers"]], (week["summary"], after["summary"])
    assert len(week["suppliers"]) >= 5, week["summary"]
    print("supply ahead OK:", a["summary"], "| backtest:", {k: allh[k]["error"] for k in ("model", "patternsOnly", "lastYear", "normal")})

"""Self-check for /api/radar/backtest on the Médina Viandes seed (fresh temp DB).

    cd apps/backend && DATA_DIR=data uv run python tests/test_radar_backtest.py
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ["DB_PATH"] = str(Path(tempfile.mkdtemp()) / "erp.db")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

with TestClient(app) as c:
    r = c.get("/api/radar/backtest", params={"weeks": 8}).json()
    assert r["weeks"] == 8 and len(r["days"]) == 8 and all(d["date"] < "2026-11-24" for d in r["days"]), r["days"]
    # The pitch numbers: who orders is well predicted, and the € forecast is close to what was delivered.
    assert r["recall"] >= 0.85 and r["precision"] >= 0.8, r
    assert 0.8 <= r["revenueRatio"] <= 1.25, r
    assert r["pitch"].startswith("Sur les 8 derniers mercredis"), r["pitch"]
    # Normal weeks: the radar's € per day is as good as the plain 60-day average (both ~20 %: a Wednesday swings from
    # 26 k€ to 52 k€), and its quantities per line are closer.
    assert r["radar"]["revenueError"] <= r["average"]["revenueError"] + 0.05, r
    assert r["radar"]["quantityMape"] < r["average"]["quantityMape"], r

    # On the past year's big events (ramadan, aïd el-kébir) the measured uplift wins by far.
    ev = c.get("/api/radar/backtest/events", params={"months": 12}).json()
    for typ in ("ramadan", "aid_kebir"):
        t = ev["types"][typ]
        assert t["radar"]["revenueError"] < 0.6 * t["average"]["revenueError"], (typ, t)
        assert t["radar"]["quantityMape"] < t["average"]["quantityMape"], (typ, t)
    assert ev["all"]["errorReduction"]["revenueError"] > 0.3, ev["all"]
    # as_of really hides the future: a backtest day's forecast never sees that day's own orders.
    from app.services import radar
    from app import crud

    day = r["days"][-1]["date"]
    ordered = {o["clientId"] for o in crud.orders.rows.values() if o["deliveryDate"] == day}
    rows = [f for cl in crud.clients.all() if (f := radar.forecast_client(cl, day, [], as_of="2026-01-01"))]
    assert all(f["status"] == "predicted" and "existingOrderId" not in f for f in rows)
    print("radar backtest OK:", r["pitch"], "| events:", ev["all"]["errorReduction"])

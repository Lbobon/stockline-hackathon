"""Self-check for the day-level demand model (/api/radar/outlook) on the Médina Viandes seed (fresh temp DB).

    cd apps/backend && DATA_DIR=data uv run python tests/test_demand_daily.py
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ["DB_PATH"] = str(Path(tempfile.mkdtemp()) / "erp.db")
os.environ["FEEDS"] = "off"  # the calendar as seeded, no weather: the model still runs (NaN features)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

with TestClient(app) as c:
    # Walk-forward on the last 12 months: beats the simple forecasts on all days, on event days and on ramadan, no
    # huge bias. (On the last 3 months alone, Sept–Nov with no big event, the 8-week median is as good: 13 % vs 15 %.)
    b = c.get("/api/radar/outlook/backtest", params={"months": 12}).json()
    allx, ev, ram = b["error"]["tous les jours"], b["error"]["jours d'événement"], b["error"]["ramadan"]
    assert b["days"] > 250 and allx["model"] < min(allx["median8"], allx["avg60"]), allx
    assert ev["model"] < min(ev["median8"], ev["avg60"]) and ram["model"] < ram["median8"] / 2, (ev, ram)
    assert 0.8 < allx["ratio"]["model"] < 1.2, allx

    o = c.get("/api/radar/outlook", params={"days": 6}).json()
    assert o["asOf"] == "2026-11-24" and [d["date"] for d in o["days"]][0] == "2026-11-25" and len(o["days"]) == 6, o
    assert all(d["expectedHt"] > 0 and d["normalHt"] > 0 and d["segments"] for d in o["days"]), o["days"]
    assert all(d["date"][:4] == "2026" and "2026-11-29" not in d["date"] for d in o["days"])  # Sundays are skipped
    assert o["topFeatures"] and abs(sum(f["share"] for f in o["topFeatures"]) - 1) < 0.5

print("demand daily OK:", {k: b["error"]["tous les jours"][k] for k in ("model", "median8", "avg60", "lastYear")},
      o["days"][0]["expectedHt"])  # fmt: skip

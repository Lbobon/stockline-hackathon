"""Self-check for /api/shortages on the Médina Viandes seed (fresh temp DB, the real DB is untouched).

    cd apps/backend && DATA_DIR=data uv run python tests/test_shortages.py
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
    t = c.get("/api/shortages/today").json()
    by_order = {o["orderId"]: o for o in t["orders"]}
    assert {p["productId"] for p in t["products"]} == {"prd_0020"}, t["products"]  # the 20 kg skewer is covered

    # Istanbul Grill's 20 kg skewers were already replaced by 10 kg ones with the client's OK (substituteFor in the seed).
    assert "ord_029604" not in by_order, "an already-substituted line is not a shortage"
    assert by_order["ord_029511"]["preference"] == "refuses" and by_order["ord_029511"]["decision"] == "call"
    # Chez Karine accepts substitutes, but turkey escalope (the usual substitute) is oversold in the seed → call.
    karine = by_order["ord_029586"]
    assert karine["preference"] == "accepts" and karine["decision"] == "call" and "substitute" not in karine["lines"][0]
    assert c.post("/api/shortages/ord_029586/substitute").status_code == 400

    # Turkey arrives → Karine flips to auto-substitute with veal escalope → turkey escalope.
    r = c.post("/api/stock/adjust", json={"productId": "prd_0028", "qty": 60, "reason": "adjustment"})
    assert r.status_code == 200, r.text
    karine = next(o for o in c.get("/api/shortages/today").json()["orders"] if o["orderId"] == "ord_029586")
    assert karine["decision"] == "auto_substitute" and karine["lines"][0]["substitute"]["productId"] == "prd_0028"

    r = c.post("/api/shortages/ord_029586/substitute", json={"note": "Prévenue par l'agent"}, headers={"x-actor": "voice_agent"})
    assert r.status_code == 200, r.text
    assert "prd_0020" not in {l["productId"] for l in r.json()["lines"]}
    assert "ord_029586" not in {o["orderId"] for o in c.get("/api/shortages/today").json()["orders"]}
    assert c.post("/api/shortages/ord_029586/substitute").status_code == 400  # nothing left to swap

print("shortages OK:", t["counts"])

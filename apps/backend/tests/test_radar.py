"""Self-check for /api/radar on the Médina Viandes seed (fresh temp DB).

    cd apps/backend && DATA_DIR=data uv run python tests/test_radar.py
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
    f = c.get("/api/radar/forecast", params={"date": "2026-11-25"}).json()
    by = {r["clientId"]: r for r in f["clients"]}
    s = f["summary"]
    assert s["predicted"] >= 10 and s["upsell"] >= 3 and s["upliftHt"] > 0, s
    assert {e["id"] for e in f["events"]} >= {"om-psg", "mariages"}

    for r in f["clients"]:
        assert r["lat"] and r["lng"] and r["reason"]
        if r["status"] == "predicted":
            assert r["lines"] and 0 < r["probability"] <= 1 and "existingOrderId" not in r
        if r["status"] == "upsell":
            assert r["existingOrderId"] and r["addLines"] and all(l["eventId"] for l in r["addLines"])

    # The match boosts a snack's spits; never above what the event allows.
    snack = next(r for r in f["clients"] if r["status"] == "predicted" and any(l["eventId"] == "om-psg" for l in r["lines"]))
    for l in snack["lines"]:
        if l["eventId"] == "om-psg":
            assert l["baseQty"] < l["qty"] <= l["baseQty"] * 1.4 + 1, l

    # Weddings: caterers who already ordered get an add-on call for whole lambs.
    assert any(r["status"] == "upsell" and any(l["productId"] == "prd_0001" for l in r["addLines"]) for r in f["clients"])

    # Nothing out of stock is proposed: the 20 kg spit (at zero, nothing incoming) never appears.
    assert not any(l["productId"] == "prd_0044" for r in f["clients"] if r["status"] == "predicted" for l in r["lines"])

    # Already-ordered clients carry their order id; lost clients are left out.
    assert all(r.get("existingOrderId") for r in f["clients"] if r["status"] in ("ordered", "upsell"))
    assert "cli_0044" not in by  # Couscous Marion, tagged "perdus"

    # Call order: loyal customers first, then by expected € net; a delivery that isn't worth it is not called.
    rows = [r for r in f["clients"] if r["status"] in ("predicted", "upsell")]
    for r in rows:
        assert r["loyalty"]["tier"] in ("fidele", "regulier", "occasionnel") and 0 <= r["loyalty"]["score"] <= 1
        assert r["loyalty"]["weeksActive"] <= 13 and r["routeFit"]["costEur"] >= 0 and isinstance(r["call"], bool)
        if r["status"] == "upsell":
            assert r["call"]  # an add-on rides on a delivery that happens anyway
        if not r["call"]:
            fit = r["routeFit"]
            assert r["skipReason"] and "priority" not in r
            assert r["totalHt"] < 120 or fit["costEur"] > 0.5 * r["marginHt"], r
    called = [r for r in f["clients"] if r.get("call")]
    assert [r["priority"] for r in called] == list(range(1, len(called) + 1))
    assert f["clients"][: len(called)] == called  # the forecast lists them in call order
    keys = [(r["wave"], -r["expectedNetEur"]) for r in called]
    assert keys == sorted(keys), keys
    assert called[0]["loyalty"]["tier"] == "fidele"
    assert s["toCall"] == len(called) and s["skipped"] == len(rows) - len(called) and s["skipped"] >= 1

print("radar OK:", s)

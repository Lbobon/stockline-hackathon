"""Self-check for /api/gates and /api/retention on the Médina Viandes seed (fresh temp DB).

    cd apps/backend && DATA_DIR=data uv run python tests/test_smart.py
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ["DB_PATH"] = str(Path(tempfile.mkdtemp()) / "erp.db")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

H = {"x-actor": "voice_agent"}

with TestClient(app) as c:
    tomorrow = "2026-11-25"
    # Over its credit ceiling → stays a draft with the reason.
    r = c.get("/api/gates/orders/ord_029511").json()
    assert r["decision"] == "draft" and {x["code"] for x in r["reasons"]} == {"credit_limit", "short_stock"}, r  # + the veal at zero

    def new_order(client_id: str, lines: list[dict]) -> str:
        o = c.post("/api/orders", json={"clientId": client_id, "deliveryDate": tomorrow, "lines": lines}, headers=H)
        assert o.status_code in (200, 201), o.text
        return o.json()["id"]

    # Istanbul Grill's usual 10 kg skewers, normal qty, recap confirmed → the agent may confirm.
    oid = new_order("cli_0002", [{"productId": "prd_0043", "qty": 10}])
    g = c.get(f"/api/gates/orders/{oid}").json()
    assert g["decision"] == "confirm", g
    assert c.get(f"/api/gates/orders/{oid}", params={"recapConfirmed": "false"}).json()["reasons"][0]["code"] == "recap_not_confirmed"
    f = c.post(f"/api/gates/orders/{oid}/finalize", json={"recapConfirmed": True}, headers=H).json()
    assert f["decision"] == "confirm" and f["order"]["status"] == "confirmed", f

    # 10× the usual quantity + a product never bought → draft, note left on the order, listed for the morning.
    oid = new_order("cli_0002", [{"productId": "prd_0043", "qty": 400}, {"productId": "prd_0001", "qty": 18}])
    f = c.post(f"/api/gates/orders/{oid}/finalize", json={"recapConfirmed": True}, headers=H).json()
    codes = {x["code"] for x in f["reasons"]}
    assert f["decision"] == "draft" and {"unusual_qty", "new_product"} <= codes, f
    assert f["order"]["status"] == "draft" and f["order"]["note"].startswith("À vérifier")
    assert oid in {x["orderId"] for x in c.get("/api/gates/review").json()}

    # Carcass for tomorrow, placed on the night line (data pack clock 04:30): on time whatever the machine's hour.
    oid = new_order("cli_0001", [{"productId": "prd_0001", "qty": 72}])
    codes = {x["code"] for x in c.get(f"/api/gates/orders/{oid}").json()["reasons"]}
    assert "special_cut_late" not in codes, codes

    # Retention: overdue payer → service only; loyal on-time big buyer → money, capped, > 150 € needs Rachid.
    assert c.get("/api/retention/cli_0005", params={"basisHt": 500}).json()["status"] == "service_only"  # Sidi Brahim
    g = c.get("/api/retention/cli_0002", params={"basisHt": 1220}).json()
    assert g["kind"] == "money" and 0 < g["pct"] <= 15 and g["amountHt"] == round(1220 * g["pct"] / 100, 2)
    big = c.get("/api/retention/cli_0002", params={"basisHt": 5000, "costHt": 4800}).json()
    assert big["status"] == "needs_approval" and "Rachid" in big["explanation"] and "sous le coût" in big["explanation"]

    # Below the 120 € HT delivery minimum: held for Leïla.
    small = new_order("cli_0002", [{"productId": "prd_0043", "qty": 1}])
    assert "below_minimum" in {x["code"] for x in c.get(f"/api/gates/orders/{small}").json()["reasons"]}

    # A confirmed order edited past the rules goes back to a draft; an in-stock add-on stays confirmed.
    oid = new_order("cli_0002", [{"productId": "prd_0043", "qty": 10}])
    assert c.post(f"/api/gates/orders/{oid}/finalize", json={"recapConfirmed": True}, headers=H).json()["order"]["status"] == "confirmed"
    assert c.post(f"/api/orders/{oid}/lines", json={"productId": "prd_0049", "qty": 2}, headers=H).json()["status"] == "confirmed"
    held = c.post(f"/api/orders/{oid}/lines", json={"productId": "prd_0020", "qty": 50}, headers=H).json()  # veal at zero
    assert held["status"] == "draft" and "modifiée après confirmation" in held["note"], held

print("gates + retention OK")

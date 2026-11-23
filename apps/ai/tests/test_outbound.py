"""Outbound shortage brief (agent/outbound.py): spoken quantities and days, and what the brief tells the agent."""

import asyncio

from app.agent import outbound
from app.agent.outbound import spoken_day, spoken_price, spoken_qty


def test_spoken_quantities():
    assert spoken_qty(36.5, "kg") == "36 kilos et demi"
    assert spoken_qty(10, "piece") == "10 pièces"
    assert spoken_qty(2.25, "kg") == "2,25 kilos"


def test_spoken_prices():
    assert spoken_price(9.4) == "9 euros 40" and spoken_price(64) == "64 euros" and spoken_price(26.55) == "26 euros 55"


def test_spoken_days():
    assert spoken_day("2026-11-25", "2026-11-24") == "demain mercredi"
    assert spoken_day("2026-11-24", "2026-11-24") == "aujourd'hui"
    assert spoken_day("2026-11-26", "2026-11-24") == "jeudi 26"


class FakeErp:
    def __init__(self, *a, **k): ...

    async def get(self, path, params=None):
        if path == "/api/shortages/today":
            return {"date": "2026-11-24", "products": [{"productId": "prd_0020", "substitute": {"name": "Escalope de dinde"}}],
                    "orders": [{"orderId": "ord_1", "orderNumber": "CMD-1", "deliveryDate": "2026-11-25", "clientId": "cli_0041",
                                "clientName": "Centre social", "contactName": "Samir Boyer", "reason": "Refuse les substitutions.", "preference": "refuses",
                                "lines": [{"productId": "prd_0020", "name": "Escalope de veau", "unit": "kg", "price": 26.5,
                                           "missingQty": 36.5, "qty": 36.5, "available": 0}]}]}  # fmt: skip
        if path == "/api/orders/ord_1":
            return {"status": "draft", "warnings": [{"code": "insufficient_stock", "productId": "prd_0026",
                                                     "message": "Stock insuffisant pour Haut de cuisse : 0 kg disponible(s)"}]}  # fmt: skip
        if path == "/api/products/prd_0026":
            return {"stock": {"incoming": 730}}
        return {"stock": {"incoming": 0}}


def test_the_brief_closes_the_gaps_the_sim_found(monkeypatch):
    monkeypatch.setattr(outbound, "ErpClient", FakeErp)
    brief = asyncio.run(outbound.shortage_brief("ord_1"))
    assert "une IA" in brief  # disclose it's an AI
    assert "36 kilos et demi" in brief and "demain mercredi" in brief  # the quantity and the day, as said
    assert "AUCUN remplaçant" not in brief and "n'est pas disponible non plus" in brief
    assert "ne peuvent PAS être promises" in brief  # no confirmed arrival for the veal: no later / partial promise
    assert "Haut de cuisse (un arrivage est prévu)" in brief  # the other short line, only with the ERP's facts
    assert "veut être prévenu de tout changement" in brief  # a client who refuses substitutions hears it unprompted
    assert "basis_ht = 967.25" in brief  # 36.5 × 26.5


def test_the_rest_leaves_normally_only_when_it_is_in_stock(monkeypatch):
    class AllInStock(FakeErp):
        async def get(self, path, params=None):
            if path == "/api/orders/ord_1":
                return {"status": "confirmed", "warnings": []}
            return await super().get(path, params)

    monkeypatch.setattr(outbound, "ErpClient", AllInStock)
    assert "le reste partant normalement" in asyncio.run(outbound.shortage_brief("ord_1"))
    monkeypatch.setattr(outbound, "ErpClient", FakeErp)  # thigh line short too
    assert "le reste partant normalement" not in asyncio.run(outbound.shortage_brief("ord_1"))

"""Order gates: may the voice agent confirm this order itself, or must it stay a draft for a human (spec §3.2)?

    decision = "confirm"  no gate fired → the agent confirms
             = "draft"    at least one gate fired → stays a draft, listed for Leïla at 05:00 with the reasons

Gates (each returns {code, message} with a plain-French message):
    client_blocked, credit_limit, too_late  ← reused from orders.order_warnings (one source of truth)
    recap_not_confirmed                     ← the agent says the client did not clearly confirm the read-back
    unusual_qty                             ← line qty > 3× the client's average for that product
    new_product                             ← a product the client never bought
    special_cut_late                        ← carcass ordered the day before after 14:00 (or on the delivery day)
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app import crud
from app.core.activity import Ctx, today
from app.core.db import Doc, get_meta, per_data_version, transactional
from app.core.utils import add_days, fr_num
from app.services import orders

BLOCKING_WARNINGS = {"client_blocked", "credit_limit", "too_late"}
UNUSUAL_FACTOR = 3
CARCASS_CUTOFF_HOUR = 14  # "Carcasses entières et découpes spéciales : la veille avant 14h"


@per_data_version
def _client_product_stats() -> dict[tuple[str, str], Doc]:
    """{(clientId, productId): {n, total}} over every past sale. Cached until the next write."""
    stats: dict[tuple[str, str], Doc] = {}
    for o in crud.orders.rows.values():
        if o["status"] not in ("delivered", "invoiced"):  # past sales only: the order being checked never counts itself
            continue
        for l in o["lines"]:
            s = stats.setdefault((o["clientId"], l["productId"]), {"n": 0, "total": 0.0})
            s["n"] += 1
            s["total"] += l["qty"]
    return stats


def _late_for_special_cut(order: Doc) -> bool:
    """Placed on/after the delivery day, or the day before at/after 14:00 Paris time.
    An order placed on the anchored day uses the data pack's clock (meta.generatedAt, the night line's hour, which the
    agent also reads), not the machine's wall clock: else the same call passes at 5 am and fails at 9 pm."""
    created = order.get("createdAt") or f"{today()}T00:00:00Z"
    clock = get_meta().get("generatedAt")
    if clock and created[:10] == today() == clock[:10]:
        created = clock
    placed = datetime.fromisoformat(created.replace("Z", "+00:00")).astimezone(ZoneInfo("Europe/Paris"))
    day = placed.date().isoformat()
    return order["deliveryDate"] <= day or (order["deliveryDate"] == add_days(day, 1) and placed.hour >= CARCASS_CUTOFF_HOUR)


def _is_special_cut(p: Doc) -> bool:
    return p["name"].lower().startswith("carcasse")


def check(order_id: str, recap_confirmed: bool = True) -> Doc:
    """GET /api/gates/orders/{id}: the decision and every reason."""
    order = crud.orders.require(order_id)
    reasons = [{"code": w["code"], "message": w["message"]} for w in orders.order_warnings(order) if w["code"] in BLOCKING_WARNINGS]
    if not recap_confirmed:
        reasons.append({"code": "recap_not_confirmed", "message": "Le client n'a pas clairement validé le récapitulatif"})
    stats = _client_product_stats()
    for l in order["lines"]:
        p = crud.products.get(l["productId"]) or {"name": l["productId"], "unit": ""}
        s = stats.get((order["clientId"], l["productId"]))
        if not s:
            reasons.append({"code": "new_product", "productId": l["productId"],
                            "message": f"{p['name']} : jamais commandé par ce client"})  # fmt: skip
        elif l["qty"] > UNUSUAL_FACTOR * s["total"] / s["n"]:
            avg = s["total"] / s["n"]
            reasons.append({"code": "unusual_qty", "productId": l["productId"],
                            "message": f"{p['name']} : {fr_num(l['qty'])} {p['unit']}, {fr_num(round(l['qty'] / avg, 1))}× plus que d'habitude ({fr_num(round(avg, 1))} {p['unit']})"})  # fmt: skip
        if _is_special_cut(p) and _late_for_special_cut(order):
            reasons.append({"code": "special_cut_late", "productId": l["productId"],
                            "message": f"{p['name']} : carcasses à commander la veille avant 14 h, livraison à décaler"})  # fmt: skip
    return {"orderId": order_id, "decision": "draft" if reasons else "confirm", "reasons": reasons}


@transactional
def finalize(ctx: Ctx, order_id: str, recap_confirmed: bool = True) -> Doc:
    """POST /api/gates/orders/{id}/finalize: confirm if no gate fires, else keep the draft and note why on it."""
    result = check(order_id, recap_confirmed)
    if result["decision"] == "confirm":
        orders.confirm_order(ctx, order_id)
    else:
        orders.update_order(ctx, order_id, {"note": "À vérifier : " + " · ".join(r["message"] for r in result["reasons"])})
    return {**result, "order": orders.order_detail(order_id)}


def review(day: str | None = None) -> list[Doc]:
    """GET /api/gates/review: draft orders delivering ≥ day with at least one reason, for the morning page."""
    day = day or today()
    out = []
    for o in crud.orders.rows.values():
        if o["status"] != "draft" or o["deliveryDate"] < day:
            continue
        r = check(o["id"])
        if r["reasons"]:
            c = crud.clients.get(o["clientId"]) or {}
            out.append({**r, "orderNumber": o["number"], "clientId": o["clientId"], "clientName": c.get("name", ""),
                        "deliveryDate": o["deliveryDate"], "totalHt": o["totalHt"], "createdBy": o.get("createdBy")})  # fmt: skip
    return sorted(out, key=lambda r: (r["deliveryDate"], r["clientName"]))

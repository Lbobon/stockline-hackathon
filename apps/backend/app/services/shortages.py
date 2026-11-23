"""Shortage triage: which open orders hold a product that is out of stock, and what to do per client.

    decision = "auto_substitute"  client card accepts substitutions AND every short line has a substitute in stock
             = "call"             otherwise (refuses, preference unknown, or no substitute): "jamais sans accord"

Substitutes come from the company policy context entry "Substitutions habituelles" (`A (prd_x) → B (prd_y)`).
The client preference is read from the client's CRM preferences/notes text.
"""

from __future__ import annotations

import re

from app import crud
from app.core.activity import Ctx, today
from app.core.db import Doc, transactional
from app.core.errors import bad_request
from app.core.utils import normalize
from app.services import client_events, orders
from app.services.stock import stock_levels

OPEN_STATUSES = ("draft", "confirmed", "prepared")
_PAIR = re.compile(r"\((prd_\d+)\)[^\n]*?→[^\n]*?\((prd_\d+)\)")


def substitution_map() -> dict[str, str]:
    """{short productId: usual substitute productId}, from the company's policy context entries."""
    subs: dict[str, str] = {}
    for e in crud.context_entries.filter(lambda e: e["entityType"] == "company" and e["kind"] == "policy"):
        for a, b in _PAIR.findall(e.get("body") or ""):
            subs.setdefault(a, b)
    return subs


def substitution_preference(client: Doc) -> str:
    """refuses | accepts | unknown, from the CRM text. shortcut: keyword match on French, upgrade to a client field if cards vary."""
    text = normalize(f"{client.get('preferences') or ''} {client.get('notes') if isinstance(client.get('notes'), str) else ''}")
    if "refuse les substitutions" in text or "refuse toute substitution" in text or "pas de substitution" in text:
        return "refuses"
    if "accepte les substitutions" in text:
        return "accepts"
    return "unknown"


_REASON = {
    "refuses": "Refuse les substitutions : appeler avant toute modification.",
    "unknown": "Préférence inconnue sur les substitutions : jamais sans accord, on appelle.",
    "accepts": "Accepte les substitutions équivalentes si on prévient.",
}


def _product(pid: str, levels: dict[str, Doc]) -> Doc:
    p = crud.products.get(pid) or {}
    return {"productId": pid, "name": p.get("name", pid), "unit": p.get("unit", ""), "price": p.get("price", 0),
            "available": levels.get(pid, {}).get("available", 0)}  # fmt: skip


def triage(date: str | None = None) -> Doc:
    """GET /api/shortages/today: out-of-stock products and the open orders (delivery ≥ date) that hold them."""
    day = date or today()
    levels = stock_levels()
    subs = substitution_map()
    # A product is short when nothing is physically on hand (reservations don't count: those orders are the ones hit).
    short = {pid for pid, lv in levels.items() if lv["onHand"] <= 0}
    rows: list[Doc] = []
    for o in crud.orders.rows.values():
        if o["status"] not in OPEN_STATUSES or o["deliveryDate"] < day:
            continue
        hit = [l for l in o["lines"] if l["productId"] in short and (l.get("preparedQty") is None or l["qty"] > l["preparedQty"])]
        if not hit:
            continue
        client = crud.clients.require(o["clientId"])
        pref = substitution_preference(client)
        lines = []
        for l in hit:
            sub_id = subs.get(l["productId"])
            sub = _product(sub_id, levels) if sub_id else None
            missing = l["qty"] - (l.get("preparedQty") or 0)
            lines.append({"lineId": l["id"], **_product(l["productId"], levels), "qty": l["qty"], "missingQty": missing,
                          "substitute": sub if sub and sub["available"] >= missing else None})  # fmt: skip
        all_subbed = all(x["substitute"] for x in lines)
        decision = "auto_substitute" if pref == "accepts" and all_subbed else "call"
        reason = _REASON[pref] if all_subbed or pref != "accepts" else "Pas de remplaçant en stock : appeler le client."
        contact = (client.get("contacts") or [{}])[0]
        rows.append({
            "orderId": o["id"], "orderNumber": o["number"], "status": o["status"], "deliveryDate": o["deliveryDate"],
            "leavesToday": o["deliveryDate"] == day,
            "clientId": client["id"], "clientName": client["name"],
            "contactName": contact.get("name"), "phone": contact.get("phone"),
            "preference": pref, "decision": decision, "reason": reason, "lines": lines,
        })  # fmt: skip
    # Today's truck first, then the calls, then by delivery date.
    rows.sort(key=lambda r: (not r["leavesToday"], r["decision"] != "call", r["deliveryDate"], r["clientName"]))
    return {
        "date": day,
        "products": [_product(pid, levels) | {"substitute": _product(subs[pid], levels) if pid in subs else None}
                     for pid in sorted(short) if any(l["productId"] == pid for r in rows for l in r["lines"])],  # fmt: skip
        "orders": rows,
        "counts": {"orders": len(rows), "calls": sum(r["decision"] == "call" for r in rows),
                   "auto": sum(r["decision"] == "auto_substitute" for r in rows)},  # fmt: skip
    }


@transactional
def substitute(ctx: Ctx, order_id: str, outcome_note: str | None = None) -> Doc:
    """POST /api/shortages/{orderId}/substitute: swap every short line for its usual substitute and log it on the client.
    Only draft/confirmed orders can change product (the order service refuses prepared ones)."""
    row = next((r for r in triage()["orders"] if r["orderId"] == order_id), None)
    if not row:
        raise bad_request("no_shortage", f"La commande {order_id} n'a pas de ligne en rupture")
    if not all(l["substitute"] for l in row["lines"]):
        raise bad_request("no_substitute", "Pas de remplaçant en stock pour toutes les lignes en rupture")
    done = []
    for l in row["lines"]:
        sub = l["substitute"]
        orders.update_line(ctx, order_id, l["lineId"], {"productId": sub["productId"], "note": f"Remplace {l['name']} (rupture)"})
        done.append(f"{l['name']} → {sub['name']}")
    client_events.create_client_event(ctx, row["clientId"], {
        "type": "call", "orderId": order_id, "productIds": [l["productId"] for l in row["lines"]],
        "title": f"Rupture, substitution : {', '.join(done)}", "description": outcome_note or "",
    })  # fmt: skip
    return orders.order_detail(order_id)

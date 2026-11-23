"""Radar de demande: what each client will order on a delivery date, before they call.

    probability  share of the last 12 same weekdays on which the client had a delivery
    basket       the client's usual basket (last 60 days, services/clients.usual_basket), average quantities
    events       app/radar_events.json: a match, a cold snap, weddings… boost some products for some client segments,
                 and can add special orders (whole lambs for a caterer's weekend weddings)
    price        the client's own price (services/pricing.price_for)

Clients who already have an order for the date are "ordered", or "upsell" when an event calls for more (addLines);
the others are "predicted" (probability ≥ 0.35, or an
event targets them and they order at least now and then) or "unlikely" (left out of the forecast).
"""

from __future__ import annotations

import json
import math
from functools import cache
from pathlib import Path

from app import crud
from app.core.activity import today
from app.core.db import Doc
from app.core.utils import add_days, round2, weekday_of
from app.services import clients as client_svc
from app.services import geo, pricing, shortages
from app.services.stock import stock_levels
from app.services.clients import is_sale

EVENTS_FILE = Path(__file__).resolve().parents[1] / "radar_events.json"
WEEKS = 12
MIN_PROBABILITY = 0.35
EVENT_MIN_PROBABILITY = 0.15
WEEKDAY_FR = {"mon": "lundis", "tue": "mardis", "wed": "mercredis", "thu": "jeudis", "fri": "vendredis", "sat": "samedis", "sun": "dimanches"}


@cache
def _all_events() -> list[Doc]:
    return json.loads(EVENTS_FILE.read_text(encoding="utf-8"))


def _product_name(pid: str) -> str:
    p = crud.products.get(pid)
    return p["name"] if p else pid


def events(date_from: str | None = None, date_to: str | None = None) -> list[Doc]:
    """GET /api/radar/events: events whose forecast window overlaps [from, to] (default: the next 7 days)."""
    lo = date_from or today()
    hi = date_to or add_days(lo, 7)
    out = []
    for e in _all_events():
        if e["affects"]["to"] < lo or e["affects"]["from"] > hi:
            continue
        out.append({**e, "boosts": [{**b, "productName": _product_name(b["productId"])} for b in e["boosts"]],
                    "special": [{**s, "productName": _product_name(s["productId"])} for s in e.get("special", [])]})  # fmt: skip
    return out


def _targets(event: Doc, client: Doc) -> bool:
    pc = geo.postcode_of(client) or ""
    return any(seg == f"pc:{pc}" or seg in (client.get("tags") or []) for seg in event["segments"])


def _rounded(qty: float, unit: str) -> float:
    """Quantities a person would say: whole pieces / boxes, half kilos."""
    if unit in ("kg", "l"):
        return max(0.5, round(qty * 2) / 2)
    return max(1, math.ceil(qty - 0.25))


def _probability(client_id: str, date: str) -> tuple[float, int]:
    """(share, count) of the last WEEKS same weekdays before `date` with a sale delivered to this client."""
    days = {add_days(date, -7 * k) for k in range(1, WEEKS + 1)}
    hit = {o["deliveryDate"] for o in crud.orders.rows.values() if o["clientId"] == client_id and o["deliveryDate"] in days and is_sale(o)}
    return len(hit) / WEEKS, len(hit)


def _requirements(client: Doc) -> list[str]:
    text = f"{client.get('preferences') or ''}".lower()
    out = []
    if "certificat" in text:
        out.append("Certificat halal du lot agrafé au bon")
    if "refuse les substitutions" in text:
        out.append("Aucune substitution sans accord")
    return out


def _stocked(client: Doc, pid: str) -> tuple[str | None, str | None]:
    """(product to propose, note): the product itself, its usual substitute when it is out of stock (unless the client
    refuses substitutions), or (None, None) when there is nothing to propose."""
    lv = stock_levels().get(pid, {})
    if lv.get("onHand", 0) + lv.get("incoming", 0) > 0:
        return pid, None
    sub = shortages.substitution_map().get(pid)
    if not sub or shortages.substitution_preference(client) == "refuses":
        return None, None
    return sub, f"{_product_name(pid)} en rupture : proposé à la place"


def _line(client_id: str, pid: str, qty: float, base: float, date: str, event_id: str | None = None, note: str | None = None) -> Doc:
    p = crud.products.require(pid)
    price = pricing.price_for(client_id, pid, qty, date)
    return {"productId": pid, "productName": p["name"], "unit": p["unit"], "qty": qty, "baseQty": base,
            "unitPrice": round2(price), "eventId": event_id, **({"note": note} if note else {})}  # fmt: skip


def forecast_client(client: Doc, date: str, evs: list[Doc]) -> Doc | None:
    """One client's forecast for `date`, or None when they are not served that day / not likely to order."""
    if client["status"] != "active" or "perdus" in (client.get("tags") or []):
        return None
    if weekday_of(date) not in (client.get("deliveryDays") or []):
        return None
    loc = geo.locate(client)
    contact = (client.get("contacts") or [{}])[0]
    base = {"clientId": client["id"], "name": client["name"], "type": client["type"], "city": client["city"],
            "lat": loc["lat"], "lng": loc["lng"], "contactName": contact.get("name"), "phone": contact.get("phone")}  # fmt: skip
    existing = next((o for o in crud.orders.rows.values()
                     if o["clientId"] == client["id"] and o["deliveryDate"] == date and o["status"] != "cancelled"), None)  # fmt: skip
    mine = [e for e in evs if _targets(e, client)]
    boosts = {b["productId"]: (b["factor"], e["id"]) for e in mine for b in e["boosts"]}
    if existing:
        lines = [{"productId": l["productId"], "productName": _product_name(l["productId"]), "unit": (crud.products.get(l["productId"]) or {}).get("unit", ""),
                  "qty": l["qty"], "baseQty": l["qty"], "unitPrice": l["unitPrice"], "eventId": None} for l in existing["lines"]]  # fmt: skip
        # Already ordered: the events may still call for more (an add-on call: « je vous ajoute 2 agneaux pour samedi ? »)
        add = []
        if existing["status"] in ("draft", "confirmed"):
            for l in existing["lines"]:
                factor, eid = boosts.get(l["productId"], (1.0, None))
                unit = (crud.products.get(l["productId"]) or {}).get("unit", "")
                extra = _rounded(l["qty"] * (factor - 1), unit) if eid else 0
                if extra > 0:
                    add.append(_line(client["id"], l["productId"], extra, 0, date, eid))
            for e in mine:
                for sp in e.get("special", []):
                    pid, note = _stocked(client, sp["productId"])
                    if pid and not any(l["productId"] == pid for l in existing["lines"]):
                        add.append(_line(client["id"], pid, sp["qty"], 0, date, e["id"], note or sp.get("note")))
        up = sum(l["qty"] * l["unitPrice"] for l in add)
        return {**base, "status": "upsell" if add else "ordered", "probability": 1.0, "existingOrderId": existing["id"],
                "lines": lines, "addLines": add, "totalHt": round2(existing["totalHt"]), "upliftHt": round2(up),
                "reason": "Commande déjà passée" + ("".join(f" ; {e['title']} : {e['pitch']}" for e in mine) if add else ""),
                "eventIds": [e["id"] for e in mine] if add else [], "specialRequirements": _requirements(client)}  # fmt: skip

    prob, hits = _probability(client["id"], date)
    if prob < MIN_PROBABILITY and not (mine and prob >= EVENT_MIN_PROBABILITY):
        return None
    lines = []
    for u in client_svc.usual_basket(client["id"]):
        pid, note = _stocked(client, u["productId"])
        if not pid or any(l["productId"] == pid for l in lines):
            continue
        base_qty = _rounded(u["avgQty"], u["unit"])
        factor, eid = boosts.get(u["productId"], (1.0, None))
        lines.append(_line(client["id"], pid, _rounded(u["avgQty"] * factor, u["unit"]) if eid else base_qty, base_qty, date, eid, note))
    for e in mine:
        for sp in e.get("special", []):
            pid, note = _stocked(client, sp["productId"])
            if pid and not any(l["productId"] == pid for l in lines):
                lines.append(_line(client["id"], pid, sp["qty"], 0, date, e["id"], note or sp.get("note")))
    if not lines:
        return None
    total = sum(l["qty"] * l["unitPrice"] for l in lines)
    uplift = sum((l["qty"] - l["baseQty"]) * l["unitPrice"] for l in lines)
    reason = f"Commande {hits} {WEEKDAY_FR[weekday_of(date)]} sur {WEEKS}"
    if mine:
        reason += " ; " + " ; ".join(f"{e['title']} : {e['pitch']}" for e in mine)
    return {**base, "status": "predicted", "probability": round2(prob), "lines": lines, "totalHt": round2(total),
            "upliftHt": round2(uplift), "reason": reason, "eventIds": [e["id"] for e in mine],
            "specialRequirements": _requirements(client)}  # fmt: skip


def forecast(date: str | None = None) -> Doc:
    """GET /api/radar/forecast: every client served on `date` (default: tomorrow), ordered or predicted."""
    day = date or add_days(today(), 1)
    evs = [e for e in events(day, day)]
    rows = [f for c in crud.clients.all() if (f := forecast_client(c, day, evs))]
    rows.sort(key=lambda r: ({"predicted": 0, "upsell": 1}.get(r["status"], 2), -r["probability"], r["name"]))
    pred = [r for r in rows if r["status"] == "predicted"]
    ups = [r for r in rows if r["status"] == "upsell"]
    return {
        "date": day, "depot": geo.DEPOT, "events": evs, "clients": rows,
        "summary": {"ordered": sum(r["status"] in ("ordered", "upsell") for r in rows), "predicted": len(pred), "upsell": len(ups),
                    "expectedHt": round2(sum(r["totalHt"] * r["probability"] for r in pred)),
                    "upliftHt": round2(sum(r["upliftHt"] for r in pred + ups))},
    }  # fmt: skip

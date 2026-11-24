"""Radar de demande: what each client will order on a delivery date, before they call.

    probability  share of the last 12 same weekdays on which the client had a delivery
    basket       the client's usual basket (last 60 days, services/clients.usual_basket), average quantities
    events       app/radar_events.json: a match, a cold snap, weddings… boost some products for some client segments,
                 and can add special orders (whole lambs for a caterer's weekend weddings)
    price        the client's own price (services/pricing.price_for)

Clients who already have an order for the date are "ordered", or "upsell" when an event calls for more (addLines);
the others are "predicted" (probability ≥ 0.35, or an
event targets them and they order at least now and then) or "unlikely" (left out of the forecast).

Who the campaign calls first (prioritize): loyal repeat customers first, then by what the call is expected to earn.
    loyalty      weekday regularity, weeks with an order over the last 13, tenure → fidèle / régulier / occasionnel
    routeFit     cheapest insertion of the client's stop into the day's trucks (services/optimize.py's distances and €)
    expectedNet  P(order) × (margin − delivery detour): margin = qty × (price − cost), as the campaign counts it
    call         False when delivering would eat more than half the order's margin, or the order is under the 120 € minimum
                 and off every truck's path: the client is not called, dock pickup is suggested instead.
"""

from __future__ import annotations

import json
import math
from functools import cache
from pathlib import Path

from app import crud
from app.core.activity import today
from app.core.db import Doc
from app.core.utils import add_days, days_between, round2, weekday_of
from app.services import clients as client_svc
from app.services import geo, optimize, pricing, routing, shortages
from app.services.stock import stock_levels
from app.services.clients import is_sale

EVENTS_FILE = Path(__file__).resolve().parents[1] / "radar_events.json"
WEEKS = 12
MIN_PROBABILITY = 0.35
EVENT_MIN_PROBABILITY = 0.15
BASKET_MIN_FREQUENCY = 0.5  # a usual product is proposed when it is in at least half the client's recent orders (backtest: € forecast ≈ € delivered)
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


def _basket(client_id: str, as_of: str | None) -> list[Doc]:
    """The usual basket; with `as_of` (backtest), from the 60 days before that date only, so the future never leaks in."""
    if not as_of:
        return client_svc.usual_basket(client_id)
    since = add_days(as_of, -60)
    recent = sorted((o for o in crud.orders.rows.values()
                     if o["clientId"] == client_id and is_sale(o) and since <= o["deliveryDate"] < as_of), key=lambda o: o["deliveryDate"])  # fmt: skip
    return client_svc.usual_basket_of(recent, lambda o: o["deliveryDate"])


def forecast_client(client: Doc, date: str, evs: list[Doc], as_of: str | None = None) -> Doc | None:
    """One client's forecast for `date`, or None when they are not served that day / not likely to order.
    `as_of` (backtest): forecast as it would have been made that day: the basket from before it, existing orders ignored."""
    if client["status"] != "active" or "perdus" in (client.get("tags") or []):
        return None
    if weekday_of(date) not in (client.get("deliveryDays") or []):
        return None
    loc = geo.locate(client)
    contact = (client.get("contacts") or [{}])[0]
    base = {"clientId": client["id"], "name": client["name"], "type": client["type"], "city": client["city"],
            "lat": loc["lat"], "lng": loc["lng"], "contactName": contact.get("name"), "phone": contact.get("phone")}  # fmt: skip
    existing = None if as_of else next((o for o in crud.orders.rows.values()
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
    basket = _basket(client["id"], as_of)
    core = [u for u in basket if u.get("frequency", 1) >= BASKET_MIN_FREQUENCY] or basket[:1]  # always their top product
    for u in core:
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


# ── who to call first ──

LOYAL_AT, REGULAR_AT = 0.9, 0.6  # nearly every client in a forecast orders most weeks: fidèle means almost every week, for long
SKIP_COST_SHARE = 0.5  # not called when delivering would eat more than half of the order's margin
TIERS = {"fidele": (1, "Fidèle"), "regulier": (2, "Régulier"), "occasionnel": (3, "Occasionnel")}
HISTORY_WEEKS = 13


def _sales_by_client(as_of: str) -> dict[str, list[Doc]]:
    out: dict[str, list[Doc]] = {}
    for o in crud.orders.rows.values():
        if is_sale(o) and o["deliveryDate"] <= as_of:
            out.setdefault(o["clientId"], []).append(o)
    return out


def loyalty(client_id: str, date: str, sales: list[Doc], as_of: str) -> Doc:
    """How loyal a client is, from their delivered orders up to `as_of`: regular on this weekday, active most weeks,
    a customer for long. Score 0–1 and a tier."""
    weekday_share, hits = _probability(client_id, date)
    since = add_days(as_of, -7 * HISTORY_WEEKS)
    recent = [o for o in sales if o["deliveryDate"] > since]
    weeks = len({days_between(o["deliveryDate"], as_of) // 7 for o in recent})  # 0 … 12
    tenure_days = days_between(min((o["deliveryDate"] for o in sales), default=as_of), as_of)
    score = 0.45 * weekday_share + 0.35 * min(1.0, weeks / HISTORY_WEEKS) + 0.2 * min(1.0, tenure_days / 365)
    tier = "fidele" if score >= LOYAL_AT else "regulier" if score >= REGULAR_AT else "occasionnel"
    return {"score": round2(score), "tier": tier, "label": TIERS[tier][1], "weekdayHits": hits, "weeksActive": weeks,
            "ordersRecent": len(recent), "revenueRecentHt": round2(sum(o["totalHt"] for o in recent)),
            "tenureMonths": tenure_days // 30}  # fmt: skip


def _margin(lines: list[Doc]) -> float:
    """qty × (price − purchase cost): the campaign's own formula (campaign/engine._margin)."""
    total = 0.0
    for l in lines:
        p = crud.products.get(l["productId"])
        price = float(l.get("unitPrice") or 0)
        total += float(l["qty"]) * (price - (float(p["cost"]) if p else price))
    return total


def _weight(lines: list[Doc]) -> float:
    return sum(routing.weight_kg(crud.products.get(l["productId"]), float(l["qty"])) for l in lines)


def _tours(date: str) -> list[tuple[Doc, list[Doc]]]:
    """(route, stops in optimized order) for each truck out on `date`: where a new stop would be inserted."""
    runs = optimize._runs(date)
    return [(route, pts) for (_, route, _), pts in zip(runs, optimize._optimize(runs))] if runs else []


def route_fit(client: Doc, weight_kg: float, tours: list[tuple[Doc, list[Doc]]]) -> Doc:
    """Cheapest place for this client's stop in the day's trucks: extra km, minutes and € (fuel + driver), and whether
    the truck still fits it (capacity, back before the end of the delivery window)."""
    p = {**geo.locate(client), "clientId": client["id"]}
    for route, pts in tours:
        if any(x["clientId"] == client["id"] for x in pts):
            return {"route": route["name"], "onRoute": True, "feasible": True, "detourKm": 0.0, "detourMin": 0, "costEur": 0.0, "after": None}
    best = None
    for route, pts in tours:
        load = sum(optimize._order_weight(x["orderId"]) for x in pts) + weight_kg
        for i in range(len(pts) + 1):
            prev = pts[i - 1] if i else geo.DEPOT
            nxt = pts[i] if i < len(pts) else geo.DEPOT
            km_ = geo.km(prev, p) + geo.km(p, nxt) - geo.km(prev, nxt)
            min_ = geo.minutes(prev, p) + geo.minutes(p, nxt) - geo.minutes(prev, nxt) + geo.DROP_MIN
            etas = optimize._etas(pts[:i] + [p] + pts[i:], route["departureTime"])
            fits = load <= route["capacityKg"] and optimize._mins(etas[-1]) <= optimize._mins(optimize.WINDOW_END)
            cand = (not fits, optimize._cost(km_, min_), route, km_, min_, prev, fits)
            if best is None or cand[:2] < best[:2]:
                best = cand
    if best is None:  # no truck out that day: a dedicated round trip
        km_, min_ = 2 * geo.km(geo.DEPOT, p), 2 * geo.minutes(geo.DEPOT, p) + geo.DROP_MIN
        return {"route": None, "onRoute": False, "feasible": False, "detourKm": round2(km_), "detourMin": round(min_),
                "costEur": round2(optimize._cost(km_, min_)), "after": None}  # fmt: skip
    _, cost, route, km_, min_, prev, fits = best
    after = crud.clients.get(prev["clientId"])["name"] if prev.get("clientId") and crud.clients.get(prev["clientId"]) else geo.DEPOT["name"]
    return {"route": route["name"], "onRoute": False, "feasible": fits, "detourKm": round2(km_), "detourMin": round(min_),
            "costEur": round2(cost), "after": after}  # fmt: skip


def prioritize(rows: list[Doc], date: str, as_of: str | None = None) -> list[Doc]:
    """Adds loyalty, routeFit, marginHt, expectedNetEur, call / skipReason, wave and priority to the rows to call
    (predicted, upsell), and returns every row in call order: called by priority, then not called, then ordered."""
    as_of = as_of or today()
    sales = _sales_by_client(as_of)
    tours = _tours(date)
    for r in rows:
        if r["status"] not in ("predicted", "upsell"):
            continue
        client = crud.clients.require(r["clientId"])
        offer = r["addLines"] if r["status"] == "upsell" else r["lines"]
        r["loyalty"] = loyalty(r["clientId"], date, sales.get(r["clientId"], []), as_of)
        r["routeFit"] = fit = route_fit(client, _weight(offer), tours)
        margin = _margin(offer)
        r["marginHt"] = round2(margin)
        detour = 0.0 if r["status"] == "upsell" else fit["costEur"]  # an add-on rides on a delivery that happens anyway
        r["expectedNetEur"] = round2(r["probability"] * (margin - detour))
        r["call"], r["skipReason"] = True, None
        if r["status"] == "predicted" and not fit["onRoute"]:
            if r["totalHt"] < optimize.MIN_ORDER_HT:
                r["call"], r["skipReason"] = False, f"Panier prévu sous le minimum de {optimize.MIN_ORDER_HT:.0f} € HT : enlèvement au quai conseillé"
            elif fit["costEur"] > SKIP_COST_SHARE * margin:
                r["call"], r["skipReason"] = False, (f"La livraison coûterait {fit['costEur']:.0f} € pour {margin:.0f} € de marge : "
                                                     "enlèvement au quai ou regroupement conseillé")  # fmt: skip
    to_call = [r for r in rows if r.get("call")]
    to_call.sort(key=lambda r: (TIERS[r["loyalty"]["tier"]][0], -r["expectedNetEur"], r["name"]))
    for i, r in enumerate(to_call):
        r["priority"], r["wave"] = i + 1, TIERS[r["loyalty"]["tier"]][0]
    skipped = sorted((r for r in rows if r.get("call") is False), key=lambda r: r["name"])
    rest = [r for r in rows if "call" not in r]
    return to_call + skipped + rest


def forecast(date: str | None = None) -> Doc:
    """GET /api/radar/forecast: every client served on `date` (default: tomorrow), ordered or predicted."""
    day = date or add_days(today(), 1)
    evs = [e for e in events(day, day)]
    rows = [f for c in crud.clients.all() if (f := forecast_client(c, day, evs))]
    rows.sort(key=lambda r: ({"predicted": 0, "upsell": 1}.get(r["status"], 2), -r["probability"], r["name"]))
    rows = prioritize(rows, day)
    pred = [r for r in rows if r["status"] == "predicted"]
    ups = [r for r in rows if r["status"] == "upsell"]
    calls = [r for r in rows if r.get("call")]
    return {
        "date": day, "depot": geo.DEPOT, "events": evs, "clients": rows,
        "summary": {"ordered": sum(r["status"] in ("ordered", "upsell") for r in rows), "predicted": len(pred), "upsell": len(ups),
                    "expectedHt": round2(sum(r["totalHt"] * r["probability"] for r in pred)),
                    "upliftHt": round2(sum(r["upliftHt"] for r in pred + ups)),
                    "toCall": len(calls), "skipped": sum(r.get("call") is False for r in rows),
                    "loyal": sum(r["loyalty"]["tier"] == "fidele" for r in calls),
                    "expectedNetEur": round2(sum(r["expectedNetEur"] for r in calls))},
    }  # fmt: skip

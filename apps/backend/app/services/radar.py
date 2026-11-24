"""Radar de demande: what each client will order on a delivery date, before they call.

    probability  share of the last 12 same weekdays on which the client had a delivery
    baseline     median quantity per product over the client's last 8 same weekdays (services/demand.py); the 60-day
                 usual basket when they have too few of those
    events       the ERP's events calendar (crud.calendar: ramadan, aïds, school holidays, christmas, OM matches…);
                 each one multiplies what a segment buys by the uplift MEASURED on its past occurrences
                 (services/demand.uplift), adds what the client only buys for it (dates at ramadan), or, when there
                 is no history (a match, a cold snap, weddings), applies the team's labelled estimate
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

import math

from app import crud
from app.core.activity import today
from app.core.db import Doc
from app.core.utils import add_days, days_between, round2, weekday_of
from app.services import clients as client_svc
from app.services import demand, geo, optimize, pricing, routing, shortages
from app.services.stock import stock_levels
from app.services.clients import is_sale

WEEKS = 12
MIN_PROBABILITY = 0.35
EVENT_MIN_PROBABILITY = 0.15
BASKET_MIN_FREQUENCY = 0.5  # a usual product is proposed when it is in at least half the client's recent orders (backtest: € forecast ≈ € delivered)
WEEKDAY_FR = {"mon": "lundis", "tue": "mardis", "wed": "mercredis", "thu": "jeudis", "fri": "vendredis", "sat": "samedis", "sun": "dimanches"}


def _product_name(pid: str) -> str:
    p = crud.products.get(pid)
    return p["name"] if p else pid


def events(date_from: str | None = None, date_to: str | None = None) -> list[Doc]:
    """GET /api/radar/events: calendar events whose window overlaps [from, to] (default: the next 7 days), with what
    they do to demand: measured boosts and the sample they come from, or the estimate."""
    lo = date_from or today()
    hi = date_to or add_days(lo, 7)
    return [{**e, **demand.describe(e, today())} for e in demand.in_window(lo, hi)]


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


def _boost(client: Doc, pid: str, mine: list[Doc], ups: dict) -> tuple[float, str | None]:
    """The strongest event effect on this product for this client: (factor, event id), or (1, None)."""
    best = (1.0, None)
    for e in mine:
        f = demand.factor(e, client, pid, ups)
        if f is not None and abs(f - 1) > abs(best[0] - 1):
            best = (f, e["id"])
    return best


def _extras(client: Doc, mine: list[Doc], have: set[str], as_of: str, date: str) -> list[Doc]:
    """Lines an event adds on top of the usual order: what the client bought at its last occurrence (measured
    events) or the event's special order (estimated ones, e.g. whole lambs for weddings)."""
    out = []
    for e in mine:
        wanted = [(x["productId"], _rounded(x["qty"], (crud.products.get(x["productId"]) or {}).get("unit", "")), None, x["frequency"])
                  for x in demand.event_basket(client["id"], e, as_of)]  # fmt: skip
        wanted += [(sp["productId"], sp["qty"], sp.get("note"), 1.0) for sp in (e.get("estimate") or {}).get("special", [])]
        for pid0, qty, note0, freq in wanted:
            pid, note = _stocked(client, pid0)
            if pid and pid not in have:
                have.add(pid)
                out.append({**_line(client["id"], pid, qty, 0, date, e["id"], note or note0), "frequency": freq})
    return out


def _pitch(e: Doc) -> str:
    return f"{e['title']} : {e['pitch']}"


def forecast_client(client: Doc, date: str, evs: list[Doc], as_of: str | None = None, model: str = "radar") -> Doc | None:
    """One client's forecast for `date`, or None when they are not served that day / not likely to order.
    `as_of` (backtest): forecast as it would have been made that day: history up to it only, existing orders ignored.
    `model` (backtest): "radar" (baseline + measured uplift) or "average" (the plain 60-day average, no events)."""
    if client["status"] != "active" or "perdus" in (client.get("tags") or []):
        return None
    if weekday_of(date) not in (client.get("deliveryDays") or []):
        return None
    known = as_of or today()
    loc = geo.locate(client)
    contact = (client.get("contacts") or [{}])[0]
    base = {"clientId": client["id"], "name": client["name"], "type": client["type"], "city": client["city"],
            "segment": demand.segment_of(client), "lat": loc["lat"], "lng": loc["lng"],
            "contactName": contact.get("name"), "phone": contact.get("phone")}  # fmt: skip
    existing = None if as_of else next((o for o in crud.orders.rows.values()
                     if o["clientId"] == client["id"] and o["deliveryDate"] == date and o["status"] != "cancelled"), None)  # fmt: skip
    ups = demand.uplift(known)
    mine = [e for e in evs if demand.targets(e, client)] if model == "radar" else []
    if existing:
        lines = [{"productId": l["productId"], "productName": _product_name(l["productId"]), "unit": (crud.products.get(l["productId"]) or {}).get("unit", ""),
                  "qty": l["qty"], "baseQty": l["qty"], "unitPrice": l["unitPrice"], "eventId": None} for l in existing["lines"]]  # fmt: skip
        # Already ordered: the events may still call for more (an add-on call: « je vous ajoute 2 agneaux pour samedi ? »)
        add = []
        if existing["status"] in ("draft", "confirmed"):
            for l in existing["lines"]:
                factor, eid = _boost(client, l["productId"], mine, ups)
                unit = (crud.products.get(l["productId"]) or {}).get("unit", "")
                extra = _rounded(l["qty"] * (factor - 1), unit) if eid and factor > 1 else 0
                if extra > 0:
                    add.append(_line(client["id"], l["productId"], extra, 0, date, eid))
            add += _extras(client, mine, {l["productId"] for l in existing["lines"]} | {l["productId"] for l in add}, known, date)
        used = {l["eventId"] for l in add}
        up = sum(l["qty"] * l["unitPrice"] for l in add)
        return {**base, "status": "upsell" if add else "ordered", "probability": 1.0, "existingOrderId": existing["id"],
                "lines": lines, "addLines": add, "totalHt": round2(existing["totalHt"]), "upliftHt": round2(up),
                "reason": "Commande déjà passée" + "".join(f" ; {_pitch(e)}" for e in mine if e["id"] in used),
                "eventIds": [e["id"] for e in mine if e["id"] in used], "specialRequirements": _requirements(client)}  # fmt: skip

    prob, hits = _probability(client["id"], date)
    if prob < MIN_PROBABILITY and not (mine and prob >= EVENT_MIN_PROBABILITY):
        return None
    lines: list[Doc] = []
    every = demand.baseline(client["id"], date, known, min_frequency=0) if model == "radar" else []
    usual = [u for u in every if u["frequency"] >= demand.IN_BASKET_AT]
    if not usual:  # too few orders on this weekday (or the plain-average model): the 60-day basket
        basket = _basket(client["id"], as_of)
        every = [{"productId": u["productId"], "qty": u["avgQty"], "frequency": u.get("frequency", 1.0)} for u in basket]
        core = [u for u in every if u["frequency"] >= BASKET_MIN_FREQUENCY] or every[:1]  # always their top product
        usual = core
    for u in usual:
        pid, note = _stocked(client, u["productId"])
        if not pid or any(l["productId"] == pid for l in lines):
            continue
        unit = crud.products.require(pid)["unit"]
        base_qty = _rounded(u["qty"], unit)
        factor, eid = _boost(client, u["productId"], mine, ups)
        lines.append({**_line(client["id"], pid, _rounded(u["qty"] * factor, unit) if eid else base_qty, base_qty, date, eid, note),
                      "frequency": u.get("frequency", 1.0)})
    lines += _extras(client, mine, {l["productId"] for l in lines}, known, date)
    if not lines:
        return None
    used = {l["eventId"] for l in lines if l["eventId"]}
    total = sum(l["qty"] * l["unitPrice"] for l in lines)
    uplift = sum((l["qty"] - l["baseQty"]) * l["unitPrice"] for l in lines)
    reason = f"Commande {hits} {WEEKDAY_FR[weekday_of(date)]} sur {WEEKS}"
    if used:
        reason += " ; " + " ; ".join(_pitch(e) for e in mine if e["id"] in used)
    # what the client is worth that day on average: each product weighted by how often they actually take it,
    # the ones not proposed (taken now and then) included
    occasional = sum(u["qty"] * _boost(client, u["productId"], mine, ups)[0] * u["frequency"] * float((crud.products.get(u["productId"]) or {}).get("price", 0))
                     for u in every if not any(l["productId"] == u["productId"] for l in lines))  # fmt: skip
    expected = prob * (sum(l["qty"] * l["unitPrice"] * l["frequency"] for l in lines) + occasional)
    return {**base, "status": "predicted", "probability": round2(prob), "lines": lines, "totalHt": round2(total),
            "expectedHt": round2(expected),
            "upliftHt": round2(uplift), "reason": reason, "eventIds": [e["id"] for e in mine if e["id"] in used],
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


def suggestion(client_id: str, date: str | None = None) -> Doc:
    """GET /api/radar/clients/{id}/suggestion: what the radar expects this client to order on `date` (default: their
    next delivery date) and why, for the agent to bring up during an inbound call (tool radar_suggestion)."""
    client = crud.clients.require(client_id)
    nxt = client_svc.next_deliveries(client, 1)
    day = date or (nxt[0]["date"] if nxt else add_days(today(), 1))
    evs = demand.in_window(day, day)
    row = forecast_client(client, day, evs)
    mine = [e for e in evs if demand.targets(e, client)]
    out = {"clientId": client_id, "name": client["name"], "date": day, "label": next((n["label"] for n in nxt if n["date"] == day), day),
           "events": [{"id": e["id"], "title": e["title"], "pitch": e["pitch"], "basis": demand.describe(e, today())["basis"]} for e in mine]}  # fmt: skip
    if row is None:
        return {**out, "status": "unlikely", "lines": [], "reason": "Pas de commande attendue ce jour-là (pas son jour, ou il commande rarement)"}
    lines = row["addLines"] if row["status"] == "upsell" else row["lines"]
    return {**out, "status": row["status"], "probability": row["probability"], "reason": row["reason"],
            "existingOrderId": row.get("existingOrderId"), "lines": lines,
            "expectedHt": row.get("expectedHt", row.get("upliftHt")), "upliftHt": row["upliftHt"]}  # fmt: skip


def action(calls: list[Doc], evs: list[Doc]) -> Doc:
    """« Appeler ces N clients, ces quantités, pour X € »: the campaign's expected take, from the forecast rows
    to call (a predicted order is worth its expectedHt; an add-on, P × the add-on), and the part the events bring,
    split between uplift measured on history and the team's estimates (a match, the weather)."""
    estimated = {e["id"] for e in evs if not demand.measured(e)}
    ht = sum(r["expectedHt"] if r["status"] == "predicted" else r["probability"] * r["upliftHt"] for r in calls)
    up = {"measured": 0.0, "estimated": 0.0}
    for r in calls:
        for l in r["lines"] if r["status"] == "predicted" else r["addLines"]:
            if l.get("eventId"):
                up["estimated" if l["eventId"] in estimated else "measured"] += r["probability"] * (l["qty"] - l["baseQty"]) * l["unitPrice"]
    lines = sum(len(r["lines"] if r["status"] == "predicted" else r["addLines"]) for r in calls)
    eur = lambda x: f"{x:,.0f}".replace(",", " ")  # noqa: E731
    pitch = f"Appeler {len(calls)} clients, {lines} lignes proposées : {eur(ht)} € HT de commandes attendues"
    parts = [f"{eur(up['measured'])} € de hausse mesurée sur l'historique" if up["measured"] > 0 else "",
             f"{eur(up['estimated'])} € estimés (match, météo, mariages)" if up["estimated"] > 0 else ""]
    if any(parts):
        pitch += ", dont " + " et ".join(p for p in parts if p)
    return {"calls": len(calls), "lines": lines, "expectedHt": round2(ht), "eventUpliftHt": round2(sum(up.values())),
            "measuredUpliftHt": round2(up["measured"]), "estimatedUpliftHt": round2(up["estimated"]), "pitch": pitch + "."}  # fmt: skip


def forecast(date: str | None = None) -> Doc:
    """GET /api/radar/forecast: every client served on `date` (default: tomorrow), ordered or predicted."""
    day = date or add_days(today(), 1)
    evs = demand.in_window(day, day)
    rows = [f for c in crud.clients.all() if (f := forecast_client(c, day, evs))]
    rows.sort(key=lambda r: ({"predicted": 0, "upsell": 1}.get(r["status"], 2), -r["probability"], r["name"]))
    rows = prioritize(rows, day)
    pred = [r for r in rows if r["status"] == "predicted"]
    ups = [r for r in rows if r["status"] == "upsell"]
    calls = [r for r in rows if r.get("call")]
    return {
        "date": day, "depot": geo.DEPOT, "events": [{**e, **demand.describe(e, today())} for e in evs], "clients": rows,
        "action": action(calls, evs),
        "summary": {"ordered": sum(r["status"] in ("ordered", "upsell") for r in rows), "predicted": len(pred), "upsell": len(ups),
                    "expectedHt": round2(sum(r["expectedHt"] for r in pred)),
                    "upliftHt": round2(sum(r["upliftHt"] for r in pred + ups)),
                    "toCall": len(calls), "skipped": sum(r.get("call") is False for r in rows),
                    "loyal": sum(r["loyalty"]["tier"] == "fidele" for r in calls),
                    "expectedNetEur": round2(sum(r["expectedNetEur"] for r in calls))},
    }  # fmt: skip

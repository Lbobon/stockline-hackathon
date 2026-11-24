"""Radar fournisseurs: what Médina Viandes will run out of over the next days, and who to call to buy it.

The client radar predicts what each restaurant will order (services/radar.forecast). Added up over the next `days`
delivery days, that is the demand the depot must serve. When the events (weddings, Christmas market, Ramadan…) push it
above the stock (available + already incoming on sent purchase orders), the missing quantity is bought from the
product's main supplier, so the depot has it before the clients ask.

    need        Σ over the days of P(order) × predicted qty (predicted clients) + the add-on qty (upsell clients);
                orders already placed are already reserved in the stock, so they are not counted twice
    eventQty    the part of `need` that only exists because of an event (qty − usual qty on event lines)
    cover       available + incoming (a sent purchase order counts as stock on its way)
    short       need − cover, when positive
    orderQty    short + a safety margin, rounded up to a round quantity (kg by 5, pieces/boxes by 1)

Lines are grouped by the product's main supplier at that supplier's price (services/purchasing.purchase_price).

`timeline` is the same demand day by day, valued at the purchase price: what a normal day needs (`normalHt`), what the
events add on top (the spike), and the part of it the stock can no longer serve (`shortHt`). It is the chart the
supplier radar opens on: the spike comes first, the calls follow from it.
"""

from __future__ import annotations

import math

from app import crud
from app.core.activity import today
from app.core.db import Doc
from app.core.utils import add_days, round2
from app.services import geo, radar
from app.services.purchasing import purchase_price
from app.services.stock import stock_levels

SAFETY = 0.10  # order 10 % above the shortfall: the forecast is ±19 % on quantities (DECISIONS.md §4)
MIN_SHORT = 2.0  # ignore crumbs
MIN_CALL_EUR = 150.0  # not worth a call: it goes with the next routine order

# where the suppliers ship from (city centres), for the supply map
CITY = {
    "Sisteron": (44.195, 5.944), "Saragosse (Espagne)": (41.649, -0.889), "Rodez": (44.350, 2.575),
    "Ancenis": (47.366, -1.177), "Vitrolles": (43.460, 5.248), "Marseille 15e": (43.360, 5.360),
    "Saint-Martin-de-Crau": (43.640, 4.813), "Les Pennes-Mirabeau": (43.410, 5.310), "Marseille 1er": (43.300, 5.380),
}  # fmt: skip


def _round_up(qty: float, unit: str) -> float:
    step = 5 if unit == "kg" else 1
    return float(max(step, math.ceil(qty / step) * step))


def supply(date: str | None = None, days: int = 7) -> Doc:
    """GET /api/radar/supply: the shortfalls of the next `days` days from `date` (default tomorrow), per supplier."""
    start = date or add_days(today(), 1)
    dates = [add_days(start, k) for k in range(max(1, min(days, 14)))]
    need: dict[str, float] = {}
    event_qty: dict[str, float] = {}
    events: dict[str, set[str]] = {}
    by_day: dict[str, dict[str, float]] = {}  # product → day → qty, to find the day the stock runs out
    base_day: dict[str, dict[str, float]] = {}  # product → day → qty on a normal day (without the events)
    day_events: dict[str, dict[str, float]] = {d: {} for d in dates}  # day → event → extra qty it brings
    ordered_ht: dict[str, float] = {d: 0.0 for d in dates}
    prices: dict[str, float] = {}

    def _price(pid: str) -> float:
        """Purchase price from the product's main supplier (reference cost when it has none)."""
        if pid not in prices:
            prod = crud.products.get(pid) or {}
            sid = prod.get("mainSupplierId")
            prices[pid] = purchase_price(sid, pid) if sid else float(prod.get("cost") or 0)
        return prices[pid]

    titles: dict[str, str] = {}
    clients: dict[str, set[str]] = {}
    for day in dates:
        f = radar.forecast(day)
        for e in f["events"]:
            titles[e["id"]] = e["title"]
        for c in f["clients"]:
            if c["status"] in ("ordered", "upsell"):  # already ordered: reserved in the stock, only shown in the chart
                ordered_ht[day] += sum(l["qty"] * _price(l["productId"]) for l in c["lines"])
            if c["status"] == "predicted":
                lines, p = c["lines"], c["probability"]
            elif c["status"] == "upsell":
                lines, p = c.get("addLines") or [], c["probability"]
            else:
                continue
            for l in lines:
                pid = l["productId"]
                need[pid] = need.get(pid, 0.0) + p * l["qty"]
                by_day.setdefault(pid, {})[day] = by_day.get(pid, {}).get(day, 0.0) + p * l["qty"]
                base = min(l["baseQty"], l["qty"]) if l.get("eventId") else l["qty"]
                base_day.setdefault(pid, {})[day] = base_day.get(pid, {}).get(day, 0.0) + p * base
                clients.setdefault(pid, set()).add(c["clientId"])
                if l.get("eventId"):
                    extra = p * max(0.0, l["qty"] - l["baseQty"])
                    event_qty[pid] = event_qty.get(pid, 0.0) + extra
                    events.setdefault(pid, set()).add(l["eventId"])
                    day_events[day][l["eventId"]] = day_events[day].get(l["eventId"], 0.0) + extra

    levels = stock_levels()
    products: list[Doc] = []
    by_supplier: dict[str, list[Doc]] = {}
    tl = {d: {"needHt": 0.0, "normalHt": 0.0, "shortHt": 0.0} for d in dates}
    for pid, q in need.items():
        prod = crud.products.get(pid)
        if not prod:
            continue
        lv = levels.get(pid) or {}
        available = max(0.0, float(lv.get("available") or 0))
        incoming = float(lv.get("incoming") or 0)
        short = q - available - incoming
        sid = prod.get("mainSupplierId")
        price = _price(pid)
        # day by day: the demand, and the part of it the stock can no longer serve (the shortfall's increase that day)
        run, gone, runs_out, daily = 0.0, 0.0, None, []
        for day in dates:
            qd = by_day[pid].get(day, 0.0)
            run += qd
            miss = max(0.0, run - available - incoming) - gone
            gone += miss
            if miss > 0 and runs_out is None:
                runs_out = day
            daily.append(round2(miss))
            tl[day]["needHt"] += qd * price
            tl[day]["normalHt"] += base_day[pid].get(day, 0.0) * price
            tl[day]["shortHt"] += miss * price
        row = {"productId": pid, "productName": prod["name"], "unit": prod["unit"], "needQty": round2(q),
               "eventQty": round2(event_qty.get(pid, 0.0)), "available": round2(available), "incoming": round2(incoming),
               "shortQty": round2(max(0.0, short)), "firstDay": runs_out or dates[-1], "clients": len(clients[pid]),
               "events": [titles.get(e, e) for e in sorted(events.get(pid, ()))], "unitPrice": round2(price),
               "shortByDay": daily}  # fmt: skip
        products.append(row)
        if short < MIN_SHORT or not sid:
            continue
        qty = _round_up(short * (1 + SAFETY), prod["unit"])
        by_supplier.setdefault(sid, []).append({**row, "qty": qty, "unitPrice": round2(price), "totalHt": round2(qty * price)})

    suppliers: list[Doc] = []
    for sid, lines in by_supplier.items():
        s = crud.suppliers.get(sid)
        if not s or sum(l["totalHt"] for l in lines) < MIN_CALL_EUR:
            continue
        lines.sort(key=lambda l: -l["totalHt"])
        total = round2(sum(l["totalHt"] for l in lines))
        ev_total = round2(sum(min(l["eventQty"], l["qty"]) * l["unitPrice"] for l in lines))
        evs = sorted({e for l in lines for e in l["events"]})
        deliver_by = min(l["firstDay"] for l in lines)
        arrives = add_days(today(), int(s.get("leadTimeDays") or 1))
        contact = (s.get("contacts") or [{}])[0]
        lat, lng = CITY.get(s.get("city") or "", (None, None))
        top = ", ".join(f"{_fmt(l['qty'])} {l['unit']} {_de(l['productName'].split(' (')[0].lower())}" for l in lines[:2])
        suppliers.append({
            "supplierId": sid, "name": s["name"], "city": s.get("city"), "lat": lat, "lng": lng,
            "contactName": contact.get("name"),
            "phone": contact.get("phone"), "leadTimeDays": s.get("leadTimeDays"), "minOrderAmount": s.get("minOrderAmount"),
            "lines": lines, "totalHt": total, "eventHt": ev_total, "events": evs, "deliverBy": deliver_by,
            "arrives": arrives, "onTime": arrives <= deliver_by, "belowMinimum": total < float(s.get("minOrderAmount") or 0),
            "reason": (f"{top} manqueront à partir de {_day(deliver_by)}" + (f", à cause de : {', '.join(evs)}" if evs else "")),
        })  # fmt: skip
    suppliers.sort(key=lambda s: -s["totalHt"])
    products.sort(key=lambda r: -r["shortQty"])
    timeline = [
        {"date": d, "orderedHt": round2(ordered_ht[d]), "needHt": round2(tl[d]["needHt"]), "normalHt": round2(tl[d]["normalHt"]),
         "eventHt": round2(max(0.0, tl[d]["needHt"] - tl[d]["normalHt"])), "shortHt": round2(tl[d]["shortHt"]),
         "events": [titles.get(e, e) for e, x in sorted(day_events[d].items(), key=lambda kv: -kv[1]) if x >= 1]}
        for d in dates
    ]  # fmt: skip
    return {
        "from": dates[0], "to": dates[-1], "days": len(dates), "depot": geo.DEPOT, "timeline": timeline,
        "products": products, "suppliers": suppliers,
        "summary": {"suppliers": len(suppliers), "lines": sum(len(s["lines"]) for s in suppliers),
                    "totalHt": round2(sum(s["totalHt"] for s in suppliers)),
                    "eventHt": round2(sum(s["eventHt"] for s in suppliers)),
                    "shortProducts": sum(r["shortQty"] > 0 for r in products)},
    }  # fmt: skip


def _de(name: str) -> str:
    return f"d'{name}" if name[:1] in "aeiouyhéèêâîôû" else f"de {name}"


def _fmt(q: float) -> str:
    return f"{q:g}".replace(".", ",")


_DAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]


def _day(iso: str) -> str:
    from datetime import date as _d

    d = _d.fromisoformat(iso)
    return f"{_DAYS[d.weekday()]} {d.day}"

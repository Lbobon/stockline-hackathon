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
"""

from __future__ import annotations

import math

from app import crud
from app.core.activity import today
from app.core.db import Doc
from app.core.utils import add_days, round2
from app.services import demand_weekly, radar
from app.services.purchasing import purchase_price
from app.services.stock import stock_levels

SAFETY = 0.10  # order 10 % above the shortfall: the forecast is ±19 % on quantities (DECISIONS.md §4)
MIN_SHORT = 2.0  # ignore crumbs
MIN_CALL_EUR = 150.0  # not worth a call: it goes with the next routine order


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
    titles: dict[str, str] = {}
    clients: dict[str, set[str]] = {}
    for day in dates:
        f = radar.forecast(day)
        for e in f["events"]:
            titles[e["id"]] = e["title"]
        for c in f["clients"]:
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
                clients.setdefault(pid, set()).add(c["clientId"])
                if l.get("eventId"):
                    event_qty[pid] = event_qty.get(pid, 0.0) + p * max(0.0, l["qty"] - l["baseQty"])
                    events.setdefault(pid, set()).add(l["eventId"])

    levels = stock_levels()
    products: list[Doc] = []
    by_supplier: dict[str, list[Doc]] = {}
    for pid, q in need.items():
        prod = crud.products.get(pid)
        if not prod:
            continue
        lv = levels.get(pid) or {}
        available = max(0.0, float(lv.get("available") or 0))
        incoming = float(lv.get("incoming") or 0)
        short = q - available - incoming
        run = 0.0
        runs_out = dates[-1]
        for day in dates:
            run += by_day[pid].get(day, 0.0)
            if run > available + incoming:
                runs_out = day
                break
        row = {"productId": pid, "productName": prod["name"], "unit": prod["unit"], "needQty": round2(q),
               "eventQty": round2(event_qty.get(pid, 0.0)), "available": round2(available), "incoming": round2(incoming),
               "shortQty": round2(max(0.0, short)), "firstDay": runs_out, "clients": len(clients[pid]),
               "events": [titles.get(e, e) for e in sorted(events.get(pid, ()))]}  # fmt: skip
        products.append(row)
        if short < MIN_SHORT or not prod.get("mainSupplierId"):
            continue
        sid = prod["mainSupplierId"]
        qty = _round_up(short * (1 + SAFETY), prod["unit"])
        price = purchase_price(sid, pid)
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
        top = ", ".join(f"{_fmt(l['qty'])} {l['unit']} {_de(l['productName'].split(' (')[0].lower())}" for l in lines[:2])
        suppliers.append({
            "supplierId": sid, "name": s["name"], "city": s.get("city"), "contactName": contact.get("name"),
            "phone": contact.get("phone"), "leadTimeDays": s.get("leadTimeDays"), "minOrderAmount": s.get("minOrderAmount"),
            "lines": lines, "totalHt": total, "eventHt": ev_total, "events": evs, "deliverBy": deliver_by,
            "arrives": arrives, "onTime": arrives <= deliver_by, "belowMinimum": total < float(s.get("minOrderAmount") or 0),
            "reason": (f"{top} manqueront à partir de {_day(deliver_by)}" + (f", à cause de : {', '.join(evs)}" if evs else "")),
        })  # fmt: skip
    suppliers.sort(key=lambda s: -s["totalHt"])
    products.sort(key=lambda r: -r["shortQty"])
    return {
        "from": dates[0], "to": dates[-1], "days": len(dates), "products": products, "suppliers": suppliers,
        "summary": {"suppliers": len(suppliers), "lines": sum(len(s["lines"]) for s in suppliers),
                    "totalHt": round2(sum(s["totalHt"] for s in suppliers)),
                    "eventHt": round2(sum(s["eventHt"] for s in suppliers)),
                    "shortProducts": sum(r["shortQty"] > 0 for r in products)},
    }  # fmt: skip


def ahead(weeks: int = 12) -> Doc:
    """GET /api/radar/supply/ahead: the demand spikes of the next `weeks` weeks (services/demand_weekly), per product, and
    the call to book each supplier's next spike week ahead: the quantity above normal (routine reorders cover the normal
    part) + the safety margin, delivered the Saturday before the week (fresh meat is not stocked for weeks).

    shortcut: one call per supplier for its BIGGEST spike week (in € above normal); its other spike weeks are named in the
    reason and booked on a later call, so a purchase order keeps one delivery date."""
    from datetime import date as _date
    from datetime import timedelta

    f = demand_weekly.forecast(weeks)
    products: list[Doc] = []
    by_supplier: dict[str, list[tuple[Doc, Doc]]] = {}
    for pr in f["products"]:
        prod = crud.products.get(pr["productId"])
        if not prod:
            continue
        price = f["price"].get(pr["productId"], 0.0)
        spikes = [w for w in pr["weeks"] if w["spike"]]
        extra = sum(w["qty"] - w["normal"] for w in spikes)
        peak = max(pr["weeks"], key=lambda w: w["vsNormal"]) if pr["weeks"] else None
        row = {"productId": pr["productId"], "productName": prod["name"], "unit": prod["unit"], "category": prod.get("category"),
               "weeks": pr["weeks"], "spikeWeeks": len(spikes), "extraQty": round2(extra), "extraHt": round2(extra * price),
               "peakWeek": peak and peak["week"], "peakVsNormal": peak and peak["vsNormal"],
               "why": spikes[0]["why"] if spikes else []}  # fmt: skip
        products.append(row)
        if spikes and prod.get("mainSupplierId"):
            for w in spikes:
                by_supplier.setdefault(prod["mainSupplierId"], []).append((row, w))

    suppliers: list[Doc] = []
    for sid, items in by_supplier.items():
        s = crud.suppliers.get(sid)
        if not s:
            continue
        eur: dict[str, float] = {}
        for row, w in items:
            eur[w["week"]] = eur.get(w["week"], 0.0) + (w["qty"] - w["normal"]) * purchase_price(sid, row["productId"])
        week = max(eur, key=lambda k: eur[k])
        lines = []
        for row, w in items:
            if w["week"] != week:
                continue
            short = w["qty"] - w["normal"]
            qty = _round_up(short * (1 + SAFETY), row["unit"])
            price = purchase_price(sid, row["productId"])
            lines.append({"productId": row["productId"], "productName": row["productName"], "unit": row["unit"], "needQty": w["qty"],
                          "eventQty": round2(short) if w["events"] else 0.0, "available": 0.0, "incoming": 0.0, "shortQty": round2(short),
                          "normalQty": w["normal"], "vsNormal": w["vsNormal"], "firstDay": week, "clients": 0, "events": w["events"],
                          "why": w["why"], "qty": qty, "unitPrice": round2(price), "totalHt": round2(qty * price)})  # fmt: skip
        total = round2(sum(l["totalHt"] for l in lines))
        if total < MIN_CALL_EUR:
            continue
        lines.sort(key=lambda l: -l["totalHt"])
        later = sorted((k for k in eur if k != week and eur[k] >= MIN_CALL_EUR), key=lambda k: -eur[k])[:2]
        deliver_by = (_date.fromisoformat(week) - timedelta(days=2)).isoformat()  # the Saturday before the week
        why = sorted({x for l in lines for x in l["why"]}, key=lambda x: [y for l in lines for y in l["why"]].index(x))
        contact = (s.get("contacts") or [{}])[0]
        top = ", ".join(f"{_fmt(l['qty'])} {l['unit']} {_de(l['productName'].split(' (')[0].lower())}" for l in lines[:2])
        suppliers.append({
            "supplierId": sid, "name": s["name"], "city": s.get("city"), "contactName": contact.get("name"), "phone": contact.get("phone"),
            "leadTimeDays": s.get("leadTimeDays"), "minOrderAmount": s.get("minOrderAmount"), "lines": lines, "totalHt": total,
            "eventHt": round2(sum(l["totalHt"] for l in lines if l["events"])), "events": sorted({e for l in lines for e in l["events"]}),
            "deliverBy": deliver_by, "arrives": deliver_by, "onTime": True, "belowMinimum": total < float(s.get("minOrderAmount") or 0),
            "ahead": True, "week": week, "laterWeeks": later,
            "reason": f"pic prévu la semaine du {_long_day(week)} : {top} en plus de l'habitude ({'; '.join(why[:2])})"
                      + (f" ; autres pics à réserver plus tard : semaine du {' et du '.join(_long_day(w) for w in sorted(later))}" if later else ""),
        })  # fmt: skip
    suppliers.sort(key=lambda s: (s["week"], -s["totalHt"]))
    products.sort(key=lambda r: -r["extraHt"])
    months: dict[str, Doc] = {}
    for w in f["weeks"]:
        m = months.setdefault(w["week"][:7], {"month": w["week"][:7], "expectedHt": 0.0, "normalHt": 0.0, "spikeHt": 0.0})
        for k in ("expectedHt", "normalHt", "spikeHt"):
            m[k] += w[k]
    for m in months.values():
        m["vsNormal"] = round(m["expectedHt"] / m["normalHt"] - 1, 3) if m["normalHt"] else None
        for k in ("expectedHt", "normalHt", "spikeHt"):
            m[k] = round2(m[k])
    first, last = (f["weeks"][0]["week"], f["weeks"][-1]["week"]) if f["weeks"] else (f["origin"], f["origin"])
    return {
        "from": first, "to": last, "weeks": f["weeks"], "months": list(months.values()), "products": products, "suppliers": suppliers,
        "topFeatures": f["topFeatures"],
        "summary": {"suppliers": len(suppliers), "lines": sum(len(s["lines"]) for s in suppliers), "totalHt": round2(sum(s["totalHt"] for s in suppliers)),
                    "spikeHt": round2(sum(w["spikeHt"] for w in f["weeks"])), "spikeWeeks": sum(w["spikeHt"] > 0 for w in f["weeks"]),
                    "spikeProducts": sum(r["spikeWeeks"] > 0 for r in products)},
    }  # fmt: skip


def _de(name: str) -> str:
    return f"d'{name}" if name[:1] in "aeiouyhéèêâîôû" else f"de {name}"


def _fmt(q: float) -> str:
    return f"{q:g}".replace(".", ",")


_DAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]


_MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]


def _long_day(iso: str) -> str:
    return f"{int(iso[8:])} {_MONTHS[int(iso[5:7]) - 1]}"


def _day(iso: str) -> str:
    from datetime import date as _d

    d = _d.fromisoformat(iso)
    return f"{_DAYS[d.weekday()]} {d.day}"

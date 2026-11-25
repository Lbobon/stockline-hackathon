"""Radar fournisseurs: what Médina Viandes will run out of over the next days, and who to call to buy it.

The client radar predicts what each restaurant will order (services/radar.forecast). Added up over the next `days`
delivery days, that is the demand the depot must serve. When the events (weddings, Christmas market, Ramadan…) push it
above the stock (available + already incoming on sent purchase orders), the missing quantity is bought from the
product's main supplier, so the depot has it before the clients ask.

    need        Σ over the days of P(order) × predicted qty (predicted clients) + the add-on qty (upsell clients);
                orders already placed are already reserved in the stock, so they are not counted twice
    eventQty    the part of `need` that only exists because of an event (qty − usual qty on event lines)
    cover       available + incoming (a sent purchase order due by the last day counts as stock on its way; a booking
                for a later week, such as the 12-week view's, does not)
    short       need − cover, when positive
    orderQty    short + a safety margin, rounded up to a round quantity (kg by 5, pieces/boxes by 1)

Lines are grouped by the product's main supplier at that supplier's price (services/purchasing.purchase_price).

`timeline` is the same demand day by day, valued at the purchase price: what a normal day needs (`normalHt`), what the
events add on top (the spike), and the part of it the stock can no longer serve (`shortHt`). It is the chart the
supplier radar opens on: the spike comes first, the calls follow from it.
"""

from __future__ import annotations

import math
import time

from app import crud
from app.core.activity import today
from app.core.db import Doc
from app.core.errors import not_found
from app.core.utils import add_days, round2
from app.services import demand_weekly, geo, radar
from app.services.clients import is_sale
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


def _incoming(until: str) -> dict[str, float]:
    """Per product, what sent purchase orders due by `until` still have to deliver. stock_levels' « incoming » counts every
    sent order; here a booking for February (12-week view) must not look like stock arriving this week."""
    out: dict[str, float] = {}
    for po in crud.purchase_orders.rows.values():
        if po["status"] in ("sent", "partially_received") and (po.get("expectedDate") or "") <= until:
            for l in po["lines"]:
                out[l["productId"]] = out.get(l["productId"], 0.0) + max(0.0, l["qty"] - l.get("receivedQty", 0))
    return out


def _round_up(qty: float, unit: str) -> float:
    step = 5 if unit == "kg" else 1
    return float(max(step, math.ceil(qty / step) * step))


_CACHE: dict[tuple[str, ...], tuple[float, list[Doc]]] = {}
CACHE_S = 60.0  # the 7 daily forecasts take ~6 s: the radar and its explanations share them for a minute


def _forecasts(dates: list[str]) -> list[Doc]:
    """radar.forecast for each day, cached briefly (stock is always read fresh: only the clients' demand is cached)."""
    key = tuple(dates)
    hit = _CACHE.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_S:
        return hit[1]
    out = [radar.forecast(d) for d in dates]
    _CACHE.clear()
    _CACHE[key] = (time.monotonic(), out)
    return out


def _demand_lines(c: Doc) -> tuple[list[Doc], float] | None:
    """The lines of a client's forecast that still have to come out of the stock, and their probability."""
    if c["status"] == "predicted":
        return c["lines"], c["probability"]
    if c["status"] == "upsell":
        return c.get("addLines") or [], c["probability"]
    return None


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
    for day, f in zip(dates, _forecasts(dates)):
        for e in f["events"]:
            titles[e["id"]] = e["title"]
        for c in f["clients"]:
            if c["status"] in ("ordered", "upsell"):  # already ordered: reserved in the stock, only shown in the chart
                ordered_ht[day] += sum(l["qty"] * _price(l["productId"]) for l in c["lines"])
            got = _demand_lines(c)
            if not got:
                continue
            lines, p = got
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
    due = _incoming(dates[-1])
    products: list[Doc] = []
    by_supplier: dict[str, list[Doc]] = {}
    tl = {d: {"needHt": 0.0, "normalHt": 0.0, "shortHt": 0.0} for d in dates}
    for pid, q in need.items():
        prod = crud.products.get(pid)
        if not prod:
            continue
        lv = levels.get(pid) or {}
        available = max(0.0, float(lv.get("available") or 0))
        incoming = due.get(pid, 0.0)
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


# ── « Pourquoi cette commande ? » ──

NORMAL_WEEKS = 8
ACCURACY = 0.19  # backtest: the forecast is ±19 % on quantities (DECISIONS.md §4)


def supply_explain(supplier_id: str, date: str | None = None, days: int = 7) -> Doc:
    """GET /api/radar/supply/{supplier_id}/explain: why we buy each line from this supplier, with the data behind it.

    Per product: the clients that will need it and when (the forecast), what the depot really sold the same week in
    past years and in a normal week (the orders), what is already in stock or on its way (stock + purchase orders), then
    the arithmetic: shortfall + 10 % margin, rounded, × the supplier's price, and whether it arrives in time.
    """
    sup_doc = crud.suppliers.get(supplier_id)
    if not sup_doc:
        raise not_found("Fournisseur", supplier_id)
    plan = supply(date, days)
    row = next((x for x in plan["suppliers"] if x["supplierId"] == supplier_id), None)
    dates = [add_days(plan["from"], k) for k in range(plan["days"])]
    lines = row["lines"] if row else []
    pids = {l["productId"] for l in lines}

    # 1. the clients behind the demand, day by day
    titles: dict[str, str] = {}
    who: dict[str, dict[str, Doc]] = {pid: {} for pid in pids}
    per_day: dict[str, dict[str, list[float]]] = {pid: {d: [0.0, 0.0] for d in dates} for pid in pids}
    ordered: dict[str, float] = {pid: 0.0 for pid in pids}
    for day, f in zip(dates, _forecasts(dates)):
        for e in f["events"]:
            titles[e["id"]] = e["title"]
        for c in f["clients"]:
            if c["status"] in ("ordered", "upsell"):
                for l in c["lines"]:
                    if l["productId"] in pids:
                        ordered[l["productId"]] += l["qty"]
            got = _demand_lines(c)
            if not got:
                continue
            ls, p = got
            for l in ls:
                pid = l["productId"]
                if pid not in pids:
                    continue
                extra = p * max(0.0, l["qty"] - l["baseQty"]) if l.get("eventId") else 0.0
                r = who[pid].setdefault(c["clientId"], {"clientId": c["clientId"], "name": c["name"], "city": c.get("city"),
                                                         "qty": 0.0, "eventQty": 0.0, "days": [], "probability": 0.0, "events": set()})  # fmt: skip
                r["qty"] += p * l["qty"]
                r["eventQty"] += extra
                r["days"].append(day)
                r["probability"] = max(r["probability"], p)
                if l.get("eventId"):
                    r["events"].add(l["eventId"])
                per_day[pid][day][0] += p * l["qty"]
                per_day[pid][day][1] += extra

    # 2. what the depot really sold: the same 7 days 1 and 2 years ago (same weekdays), and a normal week before each
    windows = {
        "lastYear": (add_days(dates[0], -364), add_days(dates[-1], -364)),
        "twoYears": (add_days(dates[0], -728), add_days(dates[-1], -728)),
        "normal": (add_days(dates[0], -7 * NORMAL_WEEKS), add_days(dates[0], -1)),
        "normalLastYear": (add_days(dates[0], -364 - 7 * NORMAL_WEEKS), add_days(dates[0], -365)),
    }
    lo, hi = min(w[0] for w in windows.values()), max(w[1] for w in windows.values())
    sold = {pid: {k: {"qty": 0.0, "orders": 0, "clients": set()} for k in windows} for pid in pids}
    for o in crud.orders.filter(lambda o: lo <= o["deliveryDate"] <= hi and is_sale(o)):
        d = o["deliveryDate"]
        for l in o["lines"]:
            pid = l["productId"]
            if pid not in pids:
                continue
            for k, (a, b) in windows.items():
                if a <= d <= b:
                    w = sold[pid][k]
                    w["qty"] += float(l.get("qty") or 0)
                    w["orders"] += 1
                    w["clients"].add(o["clientId"])

    # 3. what is already there: the stock and the purchase orders on their way
    levels = stock_levels()
    on_way: dict[str, list[Doc]] = {pid: [] for pid in pids}
    for po in crud.purchase_orders.rows.values():
        if po["status"] not in ("sent", "partially_received"):
            continue
        if (po.get("expectedDate") or "") > dates[-1]:  # booked for a later week: not on its way for this window
            continue
        for l in po["lines"]:
            if l["productId"] in pids and l["qty"] - l.get("receivedQty", 0) > 0:
                on_way[l["productId"]].append({"number": po.get("number"), "qty": round2(l["qty"] - l.get("receivedQty", 0)),
                                               "expectedDate": po.get("expectedDate"),
                                               "supplier": (crud.suppliers.get(po["supplierId"]) or {}).get("name")})  # fmt: skip

    out = []
    for l in lines:
        pid = l["productId"]
        lv = levels.get(pid) or {}
        hist = {k: {"from": windows[k][0], "to": windows[k][1], "qty": round2(v["qty"] / (NORMAL_WEEKS if k.startswith("normal") else 1)),
                    "orders": v["orders"], "clients": len(v["clients"])}
                for k, v in sold[pid].items()}  # fmt: skip
        clients = sorted(who[pid].values(), key=lambda r: -r["qty"])
        safety = l["qty"] - l["shortQty"]
        out.append({
            **{k: l[k] for k in ("productId", "productName", "unit", "needQty", "eventQty", "available", "incoming", "shortQty",
                                 "firstDay", "events", "qty", "unitPrice", "totalHt")},
            "orderedQty": round2(ordered[pid]), "onHand": round2(float(lv.get("onHand") or 0)),
            "reserved": round2(float(lv.get("reserved") or 0)), "onWay": on_way[pid],
            "safetyPct": SAFETY, "safetyQty": round2(safety),
            "priceSource": "tarif fournisseur" if crud.supplier_prices.get(f"{supplier_id}|{pid}") else "coût de référence du produit",
            "days": [{"date": d, "qty": round2(v[0]), "eventQty": round2(v[1])} for d, v in per_day[pid].items()],
            "clientsCount": len(clients),
            "clients": [{**{k: v for k, v in c.items() if k not in ("events", "days")}, "qty": round2(c["qty"]),
                         "eventQty": round2(c["eventQty"]), "probability": round2(c["probability"]),
                         "days": sorted(set(c["days"])), "events": [titles.get(e, e) for e in sorted(c["events"])]}
                        for c in clients[:6]],  # fmt: skip
            "history": hist,
        })
    return {
        "supplierId": supplier_id, "name": sup_doc["name"], "city": sup_doc.get("city"), "from": dates[0], "to": dates[-1],
        "leadTimeDays": sup_doc.get("leadTimeDays"), "minOrderAmount": sup_doc.get("minOrderAmount"),
        "arrives": row["arrives"] if row else None, "deliverBy": row["deliverBy"] if row else None,
        "onTime": row["onTime"] if row else None, "totalHt": row["totalHt"] if row else 0.0,
        "accuracy": ACCURACY, "lines": out,
    }  # fmt: skip

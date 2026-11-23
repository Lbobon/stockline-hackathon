"""Delivery optimization on real geography (services/geo.py) instead of « departure + 12 min per stop ».

    plan(date)        current runs vs optimized runs: km, hours, €, ETAs, load, plus the cost to serve each stop
    apply(date)       writes the optimized stops / ETAs to the route runs (and moves orders that changed truck)
    breakdown(...)    a truck stops after stop N: its remaining stops go to the other trucks, else dock pickup / tomorrow

Optimization: nearest neighbour + 2-opt inside each truck, then moving single stops between trucks when it saves
km, within capacity (kg) and the delivery window. Small enough (≤ ~60 stops a day) to run on every request.
"""

from __future__ import annotations

from app import crud
from app.core.activity import Ctx, log_event, today
from app.core.db import Doc, transactional
from app.core.errors import bad_request
from app.core.utils import add_minutes, fr_day, round2
from app.services import clients, geo, routing
from app.services.stock import line_qty

WINDOW_END = "11:00"  # deliveries 06:30 – 11:00 (before shops open)
CRISIS_WINDOW_END = "11:30"  # a rescued stop may arrive a bit later
MIN_ORDER_HT = 120.0  # house rule: below, pickup at the Arnavaux dock
COSTLY_SHARE = 0.25  # delivering costs more than 25 % of the order's margin


def _mins(hhmm: str) -> int:
    h, m = (int(x) for x in hhmm.split(":"))
    return h * 60 + m


# ── points and tours ──


def _stop_point(stop: Doc) -> Doc:
    client = crud.clients.require(stop["clientId"])
    return {**geo.locate(client), "clientId": client["id"], "orderId": stop["orderId"]}


def _tour(points: list[Doc], start: Doc = geo.DEPOT, back_to: Doc | None = geo.DEPOT) -> tuple[float, float]:
    """(km, minutes incl. drops) of start → points… → back_to."""
    km_total, minutes = 0.0, 0.0
    prev = start
    for p in points:
        km_total += geo.km(prev, p)
        minutes += geo.minutes(prev, p) + geo.DROP_MIN
        prev = p
    if back_to is not None and points:
        km_total += geo.km(prev, back_to)
        minutes += geo.minutes(prev, back_to)
    return km_total, minutes


def _etas(points: list[Doc], departure: str, start: Doc = geo.DEPOT, already: float = 0) -> list[str]:
    """Arrival time at each point, leaving `start` at departure + `already` minutes."""
    out, t, prev = [], float(already), start
    for p in points:
        t += geo.minutes(prev, p)
        out.append(add_minutes(departure, round(t)))
        t += geo.DROP_MIN
        prev = p
    return out


def _nearest_neighbour(points: list[Doc], start: Doc = geo.DEPOT) -> list[Doc]:
    left, out, prev = list(points), [], start
    while left:
        nxt = min(left, key=lambda p: geo.km(prev, p))
        left.remove(nxt)
        out.append(nxt)
        prev = nxt
    return out


def _two_opt(points: list[Doc], start: Doc = geo.DEPOT, back_to: Doc | None = geo.DEPOT) -> list[Doc]:
    best, best_km = list(points), _tour(points, start, back_to)[0]
    improved = True
    while improved:
        improved = False
        for i in range(len(best) - 1):
            for j in range(i + 1, len(best)):
                cand = best[:i] + best[i : j + 1][::-1] + best[j + 1 :]
                k = _tour(cand, start, back_to)[0]
                if k < best_km - 1e-6:
                    best, best_km, improved = cand, k, True
    return best


def _order_weight(order_id: str) -> float:
    o = crud.orders.get(order_id)
    if not o:
        return 0.0
    return sum(routing.weight_kg(crud.products.get(l["productId"]), line_qty(l)) for l in o["lines"])


def _cost(km_total: float, minutes: float) -> float:
    return km_total * geo.EUR_PER_KM + minutes / 60 * geo.EUR_PER_HOUR


# ── one truck's summary ──


def _summary(run: Doc, route: Doc, points: list[Doc]) -> Doc:
    km_total, minutes = _tour(points)
    etas = _etas(points, route["departureTime"])
    load = sum(_order_weight(p["orderId"]) for p in points)
    return {
        "runId": run["id"], "routeId": route["id"], "route": route["name"], "driver": route["driver"],
        "departure": route["departureTime"], "capacityKg": route["capacityKg"], "loadKg": round2(load),
        "km": round2(km_total), "hours": round2(minutes / 60), "costEur": round2(_cost(km_total, minutes)),
        "lastEta": etas[-1] if etas else route["departureTime"],
        "stops": [{"orderId": p["orderId"], "clientId": p["clientId"], "lat": p["lat"], "lng": p["lng"], "eta": e}
                  for p, e in zip(points, etas)],
    }  # fmt: skip


def _feasible(points: list[Doc], route: Doc, window_end: str = WINDOW_END) -> bool:
    etas = _etas(points, route["departureTime"])
    load = sum(_order_weight(p["orderId"]) for p in points)
    return load <= route["capacityKg"] and (not etas or _mins(etas[-1]) <= _mins(window_end))


def _runs(date: str) -> list[tuple[Doc, Doc, list[Doc]]]:
    runs = sorted(crud.route_runs.filter(lambda r: r["date"] == date and r["stops"]), key=lambda r: r["routeId"])
    return [(r, crud.routes.require(r["routeId"]), [_stop_point(s) for s in sorted(r["stops"], key=lambda s: s["seq"])]) for r in runs]


def _optimize(runs: list[tuple[Doc, Doc, list[Doc]]]) -> list[list[Doc]]:
    """Optimized point lists, same order as `runs`."""
    tours = [_two_opt(_nearest_neighbour(pts)) for _, _, pts in runs]
    # Move single stops between trucks while it saves at least 0.5 km and both trucks stay feasible.
    for _ in range(60):
        over = [sum(_order_weight(p["orderId"]) for p in t) > runs[k][1]["capacityKg"] for k, t in enumerate(tours)]
        best = None  # (score, from, stop index, to, insert index)
        for a, ta in enumerate(tours):
            for i, p in enumerate(ta):
                without = ta[:i] + ta[i + 1 :]
                saving_a = _tour(ta)[0] - _tour(without)[0]
                for b, tb in enumerate(tours):
                    if a == b:
                        continue
                    for j in range(len(tb) + 1):
                        cand = tb[:j] + [p] + tb[j:]
                        gain = saving_a - (_tour(cand)[0] - _tour(tb)[0])
                        if not _feasible(cand, runs[b][1]):
                            continue
                        # an overloaded truck sheds stops even at a km cost; otherwise a move must save ≥ 0.5 km
                        score = gain + (1000 if over[a] else 0)
                        if (over[a] or gain > 0.5) and (best is None or score > best[0]):
                            best = (score, a, i, b, j)
        if not best:
            break
        _, a, i, b, j = best
        p = tours[a].pop(i)
        tours[b].insert(j, p)
    return [_two_opt(t) if t else t for t in tours]


# ── cost to serve ──


def _cost_to_serve(points: list[Doc]) -> list[Doc]:
    out = []
    for i, p in enumerate(points):
        prev = points[i - 1] if i else geo.DEPOT
        nxt = points[i + 1] if i + 1 < len(points) else geo.DEPOT
        detour_km = geo.km(prev, p) + geo.km(p, nxt) - geo.km(prev, nxt)
        detour_min = geo.minutes(prev, p) + geo.minutes(p, nxt) - geo.minutes(prev, nxt) + geo.DROP_MIN
        cost = _cost(detour_km, detour_min)
        order = crud.orders.require(p["orderId"])
        margin = 0.0
        for l in order["lines"]:
            prod = crud.products.get(l["productId"])
            margin += line_qty(l) * (l["unitPrice"] - (prod["cost"] if prod else l["unitPrice"]))
        flags, suggestion = [], None
        if order["totalHt"] < MIN_ORDER_HT:
            flags.append("below_minimum")
            suggestion = f"Sous le minimum de {MIN_ORDER_HT:.0f} € HT : proposer l'enlèvement au quai des Arnavaux."
        if margin > 0 and cost > COSTLY_SHARE * margin:
            flags.append("costly")
            suggestion = suggestion or (f"Livraison à {cost:.0f} € pour {margin:.0f} € de marge : regrouper avec une autre "
                                        f"livraison de la semaine, ou facturer {max(5, round(cost - COSTLY_SHARE * margin)):.0f} € de livraison.")  # fmt: skip
        client = crud.clients.require(p["clientId"])
        out.append({
            "orderId": p["orderId"], "orderNumber": order["number"], "clientId": client["id"], "clientName": client["name"],
            "city": client["city"], "totalHt": round2(order["totalHt"]), "marginEur": round2(margin),
            "detourKm": round2(detour_km), "costEur": round2(cost),
            "costShare": round2(cost / margin) if margin > 0 else None, "flags": flags, "suggestion": suggestion,
        })  # fmt: skip
    return out


# ── public ──


def plan(date: str | None = None) -> Doc:
    """GET /api/routing/optimize?date=: current vs optimized runs, savings, and the cost to serve each stop."""
    day = date or today()
    runs = _runs(day)
    if not runs:
        raise bad_request("no_runs", f"Aucune tournée avec des arrêts le {day}")
    current = [_summary(r, route, pts) for r, route, pts in runs]
    optimized_points = _optimize(runs)
    optimized = [_summary(r, route, pts) for (r, route, _), pts in zip(runs, optimized_points)]
    moved = [
        {"orderId": p["orderId"], "clientId": p["clientId"], "from": runs[a][1]["name"], "to": runs[b][1]["name"]}
        for b, pts in enumerate(optimized_points) for p in pts
        for a, (_, _, orig) in enumerate(runs) if a != b and any(o["orderId"] == p["orderId"] for o in orig)
    ]  # fmt: skip

    def totals(xs: list[Doc]) -> Doc:
        return {"km": round2(sum(x["km"] for x in xs)), "hours": round2(sum(x["hours"] for x in xs)), "costEur": round2(sum(x["costEur"] for x in xs))}

    before, after = totals(current), totals(optimized)
    serve = sorted((c for pts in optimized_points for c in _cost_to_serve(pts)), key=lambda c: -(c["costShare"] or 0))
    return {
        "date": day, "depot": geo.DEPOT, "current": current, "optimized": optimized, "moved": moved,
        "totals": {"before": before, "after": after, "savedKm": round2(before["km"] - after["km"]),
                   "savedHours": round2(before["hours"] - after["hours"]), "savedEur": round2(before["costEur"] - after["costEur"])},
        "costToServe": serve,
    }  # fmt: skip


@transactional
def apply(ctx: Ctx, date: str | None = None) -> Doc:
    """POST /api/routing/apply: write the optimized stop order, ETAs and truck changes."""
    result = plan(date)
    for truck in result["optimized"]:
        run = crud.route_runs.require(truck["runId"])
        old = {s["orderId"]: s for r in crud.route_runs.filter(lambda r: r["date"] == result["date"]) for s in r["stops"]}
        stops = [{**old.get(s["orderId"], {}), "orderId": s["orderId"], "clientId": s["clientId"], "seq": i + 1, "eta": s["eta"],
                  "status": old.get(s["orderId"], {}).get("status", "pending")} for i, s in enumerate(truck["stops"])]  # fmt: skip
        crud.route_runs.put({**run, "stops": stops})
        for s in truck["stops"]:
            o = crud.orders.get(s["orderId"])
            if o and o.get("routeRunId") != run["id"]:
                crud.orders.put({**o, "routeRunId": run["id"]})
    t = result["totals"]
    log_event(ctx, "route_run.optimized", "route_run", result["date"],
              f"Tournées du {result['date']} optimisées : {t['savedKm']:.0f} km et {t['savedEur']:.0f} € économisés, "
              f"{len(result['moved'])} arrêt(s) changé(s) de camion")  # fmt: skip
    return result


TRANSFER_MIN = 15  # move the goods from the broken truck to the rescuer
EUR_PER_DELAY_MIN = 0.5  # a client kept waiting (goodwill, a shop opening without stock)
MAX_RESCUE_EUR_PER_STOP = 40.0  # ≈ 80 min of added lateness per rescued stop: beyond, dock pickup or tomorrow


def _rescue_tour(pos: Doc, broken_at: Doc, stops: list[Doc]) -> list[Doc]:
    """Rescuer's route after the breakdown: to the broken truck, then its remaining + rescued stops, best order."""
    return _two_opt(_nearest_neighbour(stops, broken_at), broken_at)


def _rescue_etas(at: str, pos: Doc, broken_at: Doc, order: list[Doc]) -> list[str]:
    lead = geo.minutes(pos, broken_at) + TRANSFER_MIN
    return _etas(order, at, broken_at, lead)


def breakdown(run_id: str, after_stop: int) -> Doc:
    """POST /api/routing/breakdown: the truck of `run_id` stops after its stop n° `after_stop` (0 = right after leaving).
    Its goods are stuck in it: another truck drives to it, takes them (TRANSFER_MIN), and delivers them with its own
    remaining stops, within capacity and CRISIS_WINDOW_END. Otherwise: dock pickup (Marseille) or the client's next delivery day (outside).
    Also lists the rescuers' own clients delayed by more than 15 min. Preview: nothing is written."""
    run = crud.route_runs.require(run_id)
    route = crud.routes.require(run["routeId"])
    stops = sorted(run["stops"], key=lambda s: s["seq"])
    if not 0 <= after_stop < len(stops):
        raise bad_request("bad_stop", f"Arrêt {after_stop} hors de la tournée ({len(stops)} arrêts)")
    pts = [_stop_point(s) for s in stops]
    etas = _etas(pts, route["departureTime"])
    done, left = pts[:after_stop], pts[after_stop:]
    at = etas[after_stop - 1] if after_stop else add_minutes(route["departureTime"], 10)
    broken_at = done[-1] if done else geo.DEPOT

    rescuers = []
    for r, rt, op in _runs(run["date"]):
        if r["id"] == run_id:
            continue
        oe = _etas(op, rt["departureTime"])
        k = sum(1 for e in oe if _mins(e) <= _mins(at))
        if _mins(rt["departureTime"]) > _mins(at):
            k = 0
        rescuers.append({"run": r, "route": rt, "pos": op[k - 1] if k else geo.DEPOT, "remaining": op[k:],
                         "oldEta": dict(zip((p["orderId"] for p in op[k:]), oe[k:])), "taken": []})  # fmt: skip

    old_eta = {p["orderId"]: e for p, e in zip(pts, etas)} | {oid: e for rs in rescuers for oid, e in rs["oldEta"].items()}

    def cost_of(rs: Doc, extra: list[Doc]) -> float | None:
        """€ of the rescuer's rest of day with `extra` stops: km + EUR_PER_DELAY_MIN per minute of lateness summed over its
        clients (vs their original ETA). None when it breaks capacity or the window."""
        stops_ = rs["remaining"] + rs["taken"] + extra
        if sum(_order_weight(p["orderId"]) for p in stops_) > rs["route"]["capacityKg"]:
            return None
        if not rs["taken"] and not extra:  # untouched: it just carries on
            order, e, k_ = rs["remaining"], list(rs["oldEta"].values()), _tour(rs["remaining"], rs["pos"])[0]
        else:
            order = _rescue_tour(rs["pos"], broken_at, stops_)
            e = _rescue_etas(at, rs["pos"], broken_at, order)
            k_ = geo.km(rs["pos"], broken_at) + _tour(order, broken_at)[0]
        if e and _mins(e[-1]) > _mins(CRISIS_WINDOW_END):
            return None
        late = sum(max(0, _mins(x) - _mins(old_eta[p["orderId"]])) for p, x in zip(order, e))
        return k_ * geo.EUR_PER_KM + late * EUR_PER_DELAY_MIN

    # Batch: the trip to the broken truck is paid once, so each rescuer is judged on the stops it can take together.
    pending = list(left)
    while pending:
        best = None  # (cost per stop, rescuer, stops)
        for rs in (r for r in rescuers if not r["taken"]):
            base, take = cost_of(rs, []), []
            if base is None:
                continue
            for p in sorted(pending, key=lambda q: geo.km(broken_at, q)):
                if cost_of(rs, take + [p]) is not None:
                    take.append(p)
            if take:
                per = (cost_of(rs, take) - base) / len(take)
                if per <= MAX_RESCUE_EUR_PER_STOP and (best is None or per < best[0]):
                    best = (per, rs, take)
        if not best:
            break
        best[1]["taken"] = best[2]
        pending = [p for p in pending if p not in best[2]]

    affected = []
    for p in left:
        rescuer = next((rs for rs in rescuers if p in rs["taken"]), None)
        best = (0, rescuer) if rescuer else None
        client = crud.clients.require(p["clientId"])
        row = {"orderId": p["orderId"], "orderNumber": crud.orders.require(p["orderId"])["number"], "clientId": client["id"],
               "clientName": client["name"], "city": client["city"], "lat": p["lat"], "lng": p["lng"], "oldEta": etas[pts.index(p)]}  # fmt: skip
        if best:
            row |= {"option": "reroute", "newRunId": best[1]["run"]["id"]}
        elif geo.in_marseille(p):
            row |= {"option": "dock_pickup", "note": "Aucun camion ne peut le reprendre à temps : enlèvement au quai des Arnavaux."}
        else:
            nxt = clients.next_delivery_date(client)
            row |= {"option": "next_delivery", "nextDate": nxt,
                    "note": f"Aucun camion ne peut le reprendre à temps : prochaine livraison le {fr_day(nxt)}."}  # fmt: skip
        affected.append(row)

    # Final ETAs, and the rescuers' own clients who now arrive later.
    knock_on, plans = [], []
    for rs in rescuers:
        if not rs["taken"]:
            continue
        order = _rescue_tour(rs["pos"], broken_at, rs["remaining"] + rs["taken"])
        e = dict(zip((p["orderId"] for p in order), _rescue_etas(at, rs["pos"], broken_at, order)))
        plans.append({"runId": rs["run"]["id"], "route": rs["route"]["name"], "driver": rs["route"]["driver"],
                      "from": {"lat": rs["pos"]["lat"], "lng": rs["pos"]["lng"]},
                      "stops": [{"orderId": p["orderId"], "clientId": p["clientId"], "lat": p["lat"], "lng": p["lng"], "eta": e[p["orderId"]],
                                 "rescued": p in rs["taken"]} for p in order]})  # fmt: skip
        for row in affected:
            if row.get("newRunId") == rs["run"]["id"]:
                row |= {"newRoute": rs["route"]["name"], "newDriver": rs["route"]["driver"], "newEta": e[row["orderId"]],
                        "delayMin": _mins(e[row["orderId"]]) - _mins(row["oldEta"])}  # fmt: skip
        for p in rs["remaining"]:
            delay = _mins(e[p["orderId"]]) - _mins(rs["oldEta"][p["orderId"]])
            if delay > 15:
                c = crud.clients.require(p["clientId"])
                knock_on.append({"orderId": p["orderId"], "clientId": c["id"], "clientName": c["name"], "city": c["city"],
                                 "route": rs["route"]["name"], "oldEta": rs["oldEta"][p["orderId"]], "newEta": e[p["orderId"]], "delayMin": delay})  # fmt: skip
    return {
        "runId": run_id, "route": route["name"], "driver": route["driver"], "afterStop": after_stop, "at": at,
        "position": {"lat": broken_at["lat"], "lng": broken_at["lng"]},
        "delivered": [{"orderId": p["orderId"], "clientId": p["clientId"]} for p in done],
        "affected": affected, "knockOn": knock_on, "rescuePlans": plans,
        "counts": {"affected": len(affected), "reroute": sum(a["option"] == "reroute" for a in affected),
                   "dockPickup": sum(a["option"] == "dock_pickup" for a in affected),
                   "nextDelivery": sum(a["option"] == "next_delivery" for a in affected), "knockOn": len(knock_on)},
    }  # fmt: skip

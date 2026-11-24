"""The demand model behind the radar: a per client × product baseline, plus the uplift each event type brings,
measured from the order history per client segment.

    baseline      median quantity over the client's last 8 same weekdays with an order. Days inside a big event
                  (ramadan, the two Aïds: `baselineExcluded` in the calendar) are skipped so they don't inflate a
                  normal week. A product is in it when it is on at least half of those orders.
    uplift        for each event type with past occurrences in the calendar (ramadan, aïd el-kébir, christmas…):
                  Σ what the segment bought on the event's days ÷ Σ the segment's baseline for those days (median of
                  the 8 same weekdays before the event started), per product and for the whole segment (in €).
                  Shrunk toward ×1 with SHRINK_DAYS pseudo-days so a thin sample can't produce a wild factor.
                  Only events finished before `as_of` count: a backtest never sees the event it is scoring.
    event basket  products a client only buys for that event (dates and leben at ramadan): what they bought at its
                  last occurrence, median quantity.

OM home matches, weather events and concerts come from live feeds (services/feeds.py) with years of past occurrences:
they are measured like the rest, and an effect that doesn't beat the day-to-day noise (_significant) counts as ×1.
Events with no history to measure (the weekend's weddings) carry the team's own `estimate` in the calendar instead,
and say so.
"""

from __future__ import annotations

import math
from functools import lru_cache
from statistics import mean, median, stdev

from app import crud
from app.core.db import Doc, data_version, per_data_version
from app.core.utils import add_days, round2
from app.services import geo
from app.services.clients import is_sale

BASELINE_WEEKS = 8
LOOKBACK_WEEKS = 26
IN_BASKET_AT = 0.5
SHRINK_DAYS = 3
MIN_EFFECT = 0.1  # |factor − 1| below this is noise: the event doesn't change what we propose
FACTOR_RANGE = (0.3, 4.0)
MIN_BASE_DAYS = 3

# Client segments, as the sales team tags them (05-prix-et-credit.md); the type is the fallback.
SEGMENTS = ("snacking", "boucheries", "traiteurs", "superettes", "collectivites", "restaurants")
SEGMENT_OF_TYPE = {"restaurant": "restaurants", "brasserie": "restaurants", "fishmonger": "boucheries",
                   "caterer": "traiteurs", "supermarket": "superettes", "canteen": "collectivites"}  # fmt: skip
SEGMENT_FR = {"snacking": "snacks", "boucheries": "boucheries", "traiteurs": "traiteurs", "superettes": "supérettes",
              "collectivites": "cantines", "restaurants": "restaurants", "autres": "autres"}  # fmt: skip


def segment_of(client: Doc) -> str:
    tags = client.get("tags") or []
    return next((s for s in SEGMENTS if s in tags), SEGMENT_OF_TYPE.get(client.get("type", ""), "autres"))


def targets(event: Doc, client: Doc) -> bool:
    """An event with no segments concerns everyone; otherwise a tag (« snacking ») or a postcode (« pc:13001 »)."""
    segs = event.get("segments") or []
    if not segs:
        return True
    pc = geo.postcode_of(client) or ""
    return any(s == f"pc:{pc}" or s in (client.get("tags") or []) for s in segs)


# ── history, indexed once per data version ──


@per_data_version
def _index() -> Doc:
    """client → date → product → qty, and client → date → € HT, over delivered sales."""
    qty: dict[str, dict[str, dict[str, float]]] = {}
    eur: dict[str, dict[str, float]] = {}
    for o in crud.orders.rows.values():
        if not is_sale(o):
            continue
        d, c = o["deliveryDate"], o["clientId"]
        day = qty.setdefault(c, {}).setdefault(d, {})
        for l in o["lines"]:
            day[l["productId"]] = day.get(l["productId"], 0) + l["qty"]
        eur.setdefault(c, {})[d] = eur.get(c, {}).get(d, 0) + o["totalHt"]
    return {"qty": qty, "eur": eur}


@per_data_version
def _excluded() -> frozenset[str]:
    """Days inside a big event: left out of every baseline."""
    out = set()
    for e in crud.calendar.rows.values():
        if e.get("baselineExcluded"):
            d = e["affects"]["from"]
            while d <= e["affects"]["to"]:
                out.add(d)
                d = add_days(d, 1)
    return frozenset(out)


# A weather day, a match or a concert says nothing about itself when it falls in a holiday or a religious window:
# the canteens closed for the summer would read as « heat ×0.6 ». Those days are left out of its measure.
SHORT_KINDS = ("weather", "sport", "concert")


@per_data_version
def _seasonal() -> frozenset[str]:
    out = set()
    for e in crud.calendar.rows.values():
        if measured(e) and e.get("kind") not in SHORT_KINDS:
            out.update(_days(e))
    return frozenset(out)


def same_weekdays(date: str, before: str, n: int = BASELINE_WEEKS) -> list[str]:
    """The n same weekdays before `date` and strictly before `before`, skipping big-event days."""
    ex, out = _excluded(), []
    for k in range(1, LOOKBACK_WEEKS + 1):
        d = add_days(date, -7 * k)
        if d < before and d not in ex:
            out.append(d)
            if len(out) == n:
                break
    return out


def baseline(client_id: str, date: str, as_of: str, min_frequency: float = IN_BASKET_AT) -> list[Doc]:
    """[{productId, qty, frequency}]: the client's usual order on this weekday, from history up to `as_of`.
    Empty when they ordered on fewer than 2 of those weekdays (the caller falls back to the 60-day basket).
    `min_frequency` 0: every product, for the expected € (the ones taken now and then count by how often)."""
    days = _index()["qty"].get(client_id, {})
    orders = [days[d] for d in same_weekdays(date, add_days(as_of, 1)) if d in days]
    if len(orders) < 2:
        return []
    seen: dict[str, list[float]] = {}
    for o in orders:
        for pid, q in o.items():
            seen.setdefault(pid, []).append(q)
    out = [{"productId": pid, "qty": median(q), "frequency": round2(len(q) / len(orders))}
           for pid, q in seen.items() if len(q) / len(orders) >= min_frequency]  # fmt: skip
    return sorted(out, key=lambda x: (-x["frequency"], x["productId"]))


# ── uplift measured per event type × segment ──


def _days(e: Doc) -> list[str]:
    d, out = e["affects"]["from"], []
    while d <= e["affects"]["to"]:
        out.append(d)
        d = add_days(d, 1)
    return out


def days_count(e: Doc) -> int:
    return len(_days(e))


def measured(e: Doc) -> bool:
    return "estimate" not in e


def uplift(as_of: str) -> dict[str, dict[str, Doc]]:
    """type → segment → {factor (€), products {pid: factor}, days, occurrences}, from the events finished before
    `as_of`."""
    done = tuple(sorted(e["id"] for e in crud.calendar.rows.values() if measured(e) and e["affects"]["to"] < as_of))
    return _uplift(done, data_version())


@lru_cache(maxsize=32)
def _uplift(done: tuple[str, ...], _version: int) -> dict[str, dict[str, Doc]]:
    idx_q, idx_e = _index()["qty"], _index()["eur"]
    clients = crud.clients.all()
    acc: dict[tuple[str, str], Doc] = {}
    agg_cache: dict[tuple, tuple[dict[str, float], float]] = {}

    def agg(ids: tuple[str, ...], day: str) -> tuple[dict[str, float], float]:
        """What these clients bought on `day`: product → qty, and € HT."""
        key = (ids, day)
        if key not in agg_cache:
            q: dict[str, float] = {}
            for c in ids:
                for p, x in idx_q[c].get(day, {}).items():
                    q[p] = q.get(p, 0) + x
            agg_cache[key] = (q, sum(idx_e[c].get(day, 0) for c in ids))
        return agg_cache[key]

    for eid in done:
        e = crud.calendar.rows[eid]
        group: dict[str, list[str]] = {}
        for c in clients:
            if targets(e, c) and c["id"] in idx_q:
                group.setdefault(segment_of(c), []).append(c["id"])
        for seg, members in group.items():
            ids = tuple(members)
            a = acc.setdefault((e["type"], seg), {"A": 0.0, "B": 0.0, "n": 0, "occ": set(), "r": {}, "all": [], "day": []})
            counted = []
            for d in _days(e):
                if not e.get("baselineExcluded") and d in _excluded():  # a school-holiday day inside ramadan says nothing about holidays
                    continue
                if e.get("kind") in SHORT_KINDS and d in _seasonal():
                    continue
                base_days = same_weekdays(d, e["affects"]["from"])
                if len(base_days) < MIN_BASE_DAYS:
                    continue
                base = [agg(ids, b) for b in base_days]
                base_eur = median(x[1] for x in base)
                if base_eur <= 0:  # no delivery that weekday (Sunday)
                    continue
                now_q, now_eur = agg(ids, d)
                a["A"] += now_eur
                a["B"] += base_eur
                a["day"].append(now_eur / base_eur)
                a["n"] += 1
                a["occ"].add(e["id"])
                counted.append(d)
            # per line: how much more of a product a client takes on an event order than on their normal ones
            before = [add_days(e["affects"]["from"], -k) for k in range(1, 7 * BASELINE_WEEKS + 1)]
            before = [d for d in before if d not in _excluded()]
            for c in ids:
                days = idx_q[c]
                ev = [days[d] for d in counted if d in days]
                nb = [days[d] for d in before if d in days]
                if not ev or len(nb) < 2:
                    continue
                for p in {p for o in ev for p in o}:
                    bq = [o[p] for o in nb if p in o]
                    if len(bq) >= 2:
                        r = median(o[p] for o in ev if p in o) / median(bq)
                        a["r"].setdefault(p, []).append(r)
                        a["all"].append(r)
    out: dict[str, dict[str, Doc]] = {}
    for (typ, seg), a in acc.items():
        if a["n"] < MIN_BASE_DAYS:
            continue
        # an effect within noise is no effect: the day-to-day (and line-to-line) spread decides (_significant)
        seg_sig = _significant([math.log(max(r, 1e-3)) for r in a["day"]])
        line_sig = _significant([math.log(max(r, 1e-3)) for r in a["all"]])
        seg_f = _shrunk(a["A"], a["B"], a["n"]) if seg_sig else 1.0
        line = _clamp(median(a["all"])) if len(a["all"]) >= MIN_BASE_DAYS and line_sig else 1.0
        # a product's own factor, pulled toward the segment's by SHRINK_DAYS pseudo-clients
        products = {p: _clamp((len(r) * median(r) + SHRINK_DAYS * line) / (len(r) + SHRINK_DAYS)) for p, r in a["r"].items()
                    if len(r) >= 2 and _significant([math.log(max(x, 1e-3)) for x in r])}  # fmt: skip
        out.setdefault(typ, {})[seg] = {"factor": seg_f, "line": line, "products": products,
                                        "days": a["n"], "occurrences": len(a["occ"])}  # fmt: skip
    return out


def _significant(logs: list[float], z: float = 3.0) -> bool:
    """The mean log-ratio is more than `z` standard errors away from 0 (×1): a real effect, not the week's noise.
    3, not 2: some 60 type × segment effects are tested, at 2 a few would pass by chance."""
    if len(logs) < MIN_BASE_DAYS:
        return False
    sd = stdev(logs)
    return sd == 0 or abs(mean(logs)) > z * sd / math.sqrt(len(logs))


def _shrunk(actual: float, base: float, days: int, toward: float = 1.0) -> float:
    """actual / base, pulled toward `toward` (×1, or the segment's factor for a product) by SHRINK_DAYS days, clamped."""
    prior = SHRINK_DAYS * base / days
    return _clamp((actual + prior * toward) / (base + prior))


def _clamp(f: float) -> float:
    return round2(min(FACTOR_RANGE[1], max(FACTOR_RANGE[0], f)))


def factor(e: Doc, client: Doc, pid: str, ups: dict[str, dict[str, Doc]]) -> float | None:
    """How much event `e` multiplies this client's `pid`, or None when it doesn't apply (not targeted, no
    measure, or an effect too small to act on)."""
    if not targets(e, client):
        return None
    if not measured(e):
        f = next((b["factor"] for b in e["estimate"].get("boosts", []) if b["productId"] == pid), None)
    else:
        m = ups.get(e["type"], {}).get(segment_of(client))
        f = None if m is None else m["products"].get(pid, m["line"])
    return f if f is not None and abs(f - 1) >= MIN_EFFECT else None


def event_basket(client_id: str, e: Doc, as_of: str) -> list[Doc]:
    """[{productId, qty}]: what this client only buys for this event: products on at least half of their orders at
    its last finished occurrence, and not on half of their orders in the 8 weeks before it (not their usual)."""
    if not measured(e):
        return []
    last = max((x for x in crud.calendar.rows.values() if x["type"] == e["type"] and x["affects"]["to"] < as_of),
               key=lambda x: x["affects"]["from"], default=None)  # fmt: skip
    if not last:
        return []
    days = _index()["qty"].get(client_id, {})
    orders = [days[d] for d in _days(last) if d in days]
    before = [days[d] for k in range(1, BASELINE_WEEKS * 7 + 1) if (d := add_days(last["affects"]["from"], -k)) in days]
    seen: dict[str, list[float]] = {}
    for o in orders:
        for pid, q in o.items():
            seen.setdefault(pid, []).append(q)
    usual = {p for p in seen if sum(p in o for o in before) >= IN_BASKET_AT * max(1, len(before))}
    return [{"productId": p, "qty": median(q), "frequency": round2(len(q) / len(orders))} for p, q in sorted(seen.items())
            if p not in usual and len(q) / len(orders) >= IN_BASKET_AT]  # fmt: skip


def describe(e: Doc, as_of: str, top: int = 5) -> Doc:
    """An event as GET /api/radar/events shows it: the segments it moves and its strongest product boosts, measured
    (with the sample it comes from) or estimated."""
    if not measured(e):
        est = e["estimate"]
        return {"boosts": [{**b, "productName": _name(b["productId"])} for b in est.get("boosts", [])],
                "special": [{**s, "productName": _name(s["productId"])} for s in est.get("special", [])],
                "measured": None, "basis": "estimation : pas d'historique pour mesurer cet événement"}  # fmt: skip
    m = uplift(as_of).get(e["type"], {})
    segs = {s: v for s, v in m.items() if abs(v["factor"] - 1) >= MIN_EFFECT}
    best: dict[str, float] = {}
    for v in segs.values():
        for p, f in v["products"].items():
            if f - 1 >= MIN_EFFECT and f > best.get(p, 0):
                best[p] = f
    boosts = [{"productId": p, "factor": f, "productName": _name(p)} for p, f in sorted(best.items(), key=lambda x: -x[1])[:top]]
    occ = max((v["occurrences"] for v in m.values()), default=0)
    basis = (f"mesuré sur {occ} occurrence{'s' if occ > 1 else ''} passée{'s' if occ > 1 else ''} : "
             + ", ".join(f"{SEGMENT_FR.get(s, s)} ×{v['factor']:.2f}".replace(".", ",") for s, v in sorted(segs.items(), key=lambda x: -x[1]["factor"]))
             if segs else ("pas d'effet mesurable sur l'historique" if m else "pas encore d'occurrence passée à mesurer"))  # fmt: skip
    return {"boosts": boosts, "special": [], "basis": basis,
            "measured": {s: {"factor": v["factor"], "days": v["days"], "occurrences": v["occurrences"]} for s, v in m.items()}}  # fmt: skip


def _name(pid: str) -> str:
    p = crud.products.get(pid)
    return p["name"] if p else pid


def in_window(date_from: str, date_to: str) -> list[Doc]:
    return sorted((e for e in crud.calendar.rows.values() if e["affects"]["to"] >= date_from and e["affects"]["from"] <= date_to),
                  key=lambda e: (e["affects"]["from"], e["id"]))  # fmt: skip

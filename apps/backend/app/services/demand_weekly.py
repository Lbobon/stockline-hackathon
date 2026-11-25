"""Week-level demand per product, up to 12 weeks ahead: the spikes the depot must buy for before they come.

The client radar (radar.forecast, demand_gbm) sees the next days. Buying for Ramadan, the Aïd or Christmas starts weeks
before: abattoirs fill up, dates come by sea. This model forecasts each product's quantity for each of the next weeks
from what is known today, and flags the weeks well above normal (radar fournisseurs, « Prochains mois »).

    origin      Monday of the current week; only the weeks before it are known (the current one is still running)
    normal      the product's mean weekly quantity over the 8 weeks before the origin (what routine reorders cover)
    target      qty of week origin + 7·k (k = 1…12) ÷ normal; one LightGBM regressor for every product and horizon
    features    horizon, product, category, week of year, month, delivery days in the week (1 May, 25 Dec, 1 Jan are
                closed), the normal level and its momentum (last 4 weeks, last week, 26 weeks ÷ normal), last year's same
                week ÷ last year's normal (and ±1 week, and two years back), the climate normal of the week (mean tmax of
                that ISO week in the Open-Meteo history: barbecue summers), and for every measured long event type
                (ramadan, the Aïds, Christmas, the Christmas market, school holidays): days of the week inside it and days
                until its next start (≤ 35), for the target week AND for last year's same week, so a moving event
                (ramadan comes 11 days earlier each year) is not mistaken for the season
    spike       forecast ≥ 1.25 × normal and ≥ 150 € above it
Backtest: walk-forward over the last 52 origins (retrained every 4 weeks on the weeks before), against the normal level
(« comme les dernières semaines ») and last year's same week scaled to this year's level (« comme l'an dernier »).

    python -m app.services.demand_weekly          # prints the backtest
"""

from __future__ import annotations

import math
from datetime import date as Date
from datetime import timedelta
from functools import lru_cache
from statistics import mean

import lightgbm as lgb
import numpy as np

from app import crud
from app.core.activity import today
from app.core.db import Doc, per_data_version
from app.services import demand, feeds
from app.services.clients import is_sale

WEEKS = 12
NORMAL_WEEKS = 8
SPIKE = 0.25
MIN_SPIKE_EUR = 150.0
LEAD = 35  # days before an event start that count as « it is coming »
CLOSED = ("01-01", "05-01", "12-25")  # the only days the depot does not deliver (checked in the history)
W = timedelta(days=7)
PARAMS = {"objective": "regression", "learning_rate": 0.05, "num_leaves": 31, "min_data_in_leaf": 40, "feature_fraction": 0.9,
          "bagging_fraction": 0.8, "bagging_freq": 1, "seed": 7, "verbose": -1, "num_threads": 4}  # fmt: skip
ROUNDS = 300
FEATURES = ["horizon", "product", "category", "weekOfYear", "month", "deliveryDays", "monthStart", "normal", "last4", "lastWeek",
            "last26", "yearGrowth", "clientsGrowth", "categoryMomentum", "lastYear", "lastYearPeak", "twoYearsAgo", "climateTmax"]  # fmt: skip
PATTERNS = len(FEATURES)  # the columns before the event calendar ones: the model « sans calendrier » uses only these


def _monday(d: Date) -> Date:
    return d - timedelta(days=d.weekday())


@per_data_version
def _weekly() -> tuple[dict[tuple[str, Date], float], Date, dict[str, float]]:
    """(product, Monday) → quantity delivered that week; the first full week's Monday; the mean € price per product."""
    qty: dict[tuple[str, Date], float] = {}
    eur: dict[str, list[float]] = {}
    first = None
    for o in crud.orders.rows.values():
        if not is_sale(o):
            continue
        d = Date.fromisoformat(o["deliveryDate"])
        first = d if first is None or d < first else first
        m = _monday(d)
        for l in o["lines"]:
            k = (l["productId"], m)
            qty[k] = qty.get(k, 0.0) + l["qty"]
            e = eur.setdefault(l["productId"], [0.0, 0.0])
            e[0] += l["qty"] * l["unitPrice"]
            e[1] += l["qty"]
    start = _monday((first or Date.fromisoformat(today())) + timedelta(days=6))  # a partial first week is not a week
    return qty, start, {p: v[0] / v[1] for p, v in eur.items() if v[1]}


@per_data_version
def _extras() -> tuple[dict[tuple[str, Date], int], dict[tuple[str, Date], float]]:
    """(product, Monday) → distinct clients who bought it that week; (category, Monday) → € HT of the category that week."""
    clients: dict[tuple[str, Date], set[str]] = {}
    cat_eur: dict[tuple[str, Date], float] = {}
    cats = {p["id"]: p.get("category") or "autre" for p in crud.products.all()}
    for o in crud.orders.rows.values():
        if not is_sale(o):
            continue
        m = _monday(Date.fromisoformat(o["deliveryDate"]))
        for l in o["lines"]:
            clients.setdefault((l["productId"], m), set()).add(o["clientId"])
            k = (cats.get(l["productId"], "autre"), m)
            cat_eur[k] = cat_eur.get(k, 0.0) + l["qty"] * l["unitPrice"]
    return {k: len(v) for k, v in clients.items()}, cat_eur


@per_data_version
def _spans() -> dict[str, list[tuple[Date, Date, str]]]:
    """Measured long events (no weather, match or concert: unknown weeks ahead) per type: (from, to, title)."""
    out: dict[str, list[tuple[Date, Date, str]]] = {}
    for e in crud.calendar.rows.values():
        if demand.measured(e) and e.get("kind") not in demand.SHORT_KINDS:
            a, b = e["affects"]["from"], e["affects"]["to"]
            out.setdefault(e["type"], []).append((Date.fromisoformat(a), Date.fromisoformat(b), e["title"]))
    for v in out.values():
        v.sort()
    return out


def _climate() -> dict[int, float]:
    """ISO week → mean daily max temperature in Marseille over the weather history."""
    acc: dict[int, list[float]] = {}
    for ds, w in feeds.weather_days().items():
        if w.get("tmax") is not None:
            acc.setdefault(Date.fromisoformat(ds).isocalendar()[1], []).append(w["tmax"])
    return {k: mean(v) for k, v in acc.items()}


def _delivery_days(mon: Date) -> int:
    return sum((mon + timedelta(days=i)).strftime("%m-%d") not in CLOSED for i in range(6))


def _events(mon: Date, types: tuple[str, ...], spans) -> list[float]:
    days = [mon + timedelta(days=i) for i in range(6)]
    out: list[float] = []
    for t in types:
        out.append(sum(any(a <= d <= b for a, b, _ in spans[t]) for d in days))
        out.append(min(((a - mon).days for a, _, _ in spans[t] if 0 < (a - mon).days <= LEAD), default=LEAD + 1))
    return out


def week_events(mon: Date) -> list[str]:
    """Titles of the long events covering some delivery day of that week."""
    end = mon + timedelta(days=5)
    return [title for v in _spans().values() for a, b, title in v if a <= end and b >= mon]


def _fingerprint() -> tuple:
    qty, start, _ = _weekly()
    origin = _monday(Date.fromisoformat(today()))
    past = sorted((k[0], k[1].toordinal(), round(v, 1)) for k, v in qty.items() if k[1] < origin)
    spans = tuple((t, tuple((a.toordinal(), b.toordinal()) for a, b, _ in v)) for t, v in sorted(_spans().items()))
    return origin.toordinal(), start.toordinal(), hash(tuple(past)), spans, len(feeds.weather_days())


@lru_cache(maxsize=1)
def _table(fp: tuple) -> Doc:
    """Every (origin, horizon, product) row from the first origin with 8 known weeks up to the current origin."""
    qty, start, price = _weekly()
    n_clients, cat_eur = _extras()
    spans = _spans()
    types = tuple(sorted(spans))
    climate = _climate()
    origin_now = Date.fromordinal(fp[0])
    pids = sorted(price)
    cats = {p["id"]: p.get("category") or "autre" for p in crud.products.all()}
    cat_code = {c: i for i, c in enumerate(sorted(set(cats.values())))}
    ev_cache: dict[Date, list[float]] = {}

    def cat_code_of(p: str) -> int:
        return cat_code.get(cats.get(p, "autre"), -1)

    def ev(m: Date) -> list[float]:
        if m not in ev_cache:
            ev_cache[m] = _events(m, types, spans)
        return ev_cache[m]

    def avg(get, o: Date, a: int, b: int) -> float:  # mean of weeks o−a … o−b (a < b), only known weeks
        v = [get(o - W * i) for i in range(a, b + 1) if start <= o - W * i < origin_now]
        return mean(v) if len(v) == b - a + 1 else math.nan

    keys, X, y = [], [], []
    o = start + W * NORMAL_WEEKS
    while o <= origin_now:
        cat_mom = {c: avg(lambda m, c=c: cat_eur.get((c, m), 0.0), o, 1, 4) / (avg(lambda m, c=c: cat_eur.get((c, m), 0.0), o, 1, 8) or math.nan)
                   for c in cat_code}  # fmt: skip
        for pi, p in enumerate(pids):

            def q(m: Date, p=p) -> float:
                return qty.get((p, m), 0.0) if start <= m < origin_now else math.nan

            hist = [q(o - W * i) for i in range(1, 27)]
            normal = mean(hist[:NORMAL_WEEKS])
            if not normal > 0:
                continue
            known = [v for v in hist if not math.isnan(v)]
            year_growth = normal / (avg(lambda m, p=p: qty.get((p, m), 0.0), o, 53, 60) or math.nan)
            cl_now = avg(lambda m, p=p: n_clients.get((p, m), 0), o, 1, 8)
            clients_growth = cl_now / (avg(lambda m, p=p: n_clients.get((p, m), 0), o, 53, 60) or math.nan)

            def ratio(back: int, t: Date) -> tuple[float, float]:
                base = [q(o - W * (back + i)) for i in range(1, NORMAL_WEEKS + 1)]
                if any(math.isnan(v) for v in base) or not mean(base) > 0:
                    return math.nan, math.nan
                around = [q(t - W * (back - j)) for j in (-1, 0, 1)]
                return around[1] / mean(base), max(around) / mean(base)

            for k in range(1, WEEKS + 1):
                t = o + W * k
                ly, ly_peak = ratio(52, t)
                ly2, _ = ratio(104, t)
                keys.append((o, k, p))
                X.append([k, pi, cat_code_of(p), t.isocalendar()[1], t.month, _delivery_days(t),
                          float(any((t + timedelta(days=i)).day <= 5 for i in range(6))), normal, mean(hist[:4]) / normal,
                          hist[0] / normal, mean(known) / normal, year_growth, clients_growth, cat_mom[cats.get(p, "autre")], ly, ly_peak, ly2,
                          climate.get(t.isocalendar()[1], math.nan), *ev(t), *ev(t - W * 52)])  # fmt: skip
                actual = q(t)
                y.append(actual / normal if not math.isnan(actual) else math.nan)
        o += W
    names = FEATURES + [f"{t}:{f}{s}" for s in ("", ":lastYear") for t in types for f in ("days", "until")]
    return {"keys": keys, "X": np.array(X, dtype=float), "y": np.array(y, dtype=float), "names": names,
            "target": np.array([(o + W * k).toordinal() for o, k, _ in keys]), "origin": np.array([o.toordinal() for o, _, _ in keys])}  # fmt: skip


@lru_cache(maxsize=32)
def _model(cutoff: int, fp: tuple, events: bool = True) -> lgb.Booster:
    """Trained on every row whose target week is fully known before `cutoff` (a Monday ordinal). events=False: only the
    broad patterns (season, trend, last year, climate, holidays), none of the event calendar's columns."""
    tb = _table(fp)
    m = (tb["target"] < cutoff) & ~np.isnan(tb["y"])
    _, _, price = _weekly()
    normal = tb["X"][m][:, FEATURES.index("normal")]
    pids = sorted(price)
    w = np.sqrt(normal * np.array([price[pids[int(i)]] for i in tb["X"][m][:, FEATURES.index("product")]]))
    cols = slice(None) if events else slice(0, PATTERNS)
    data = lgb.Dataset(tb["X"][m][:, cols], np.clip(tb["y"][m], 0, 8), weight=w, categorical_feature=[1, 2])
    return lgb.train(PARAMS, data, ROUNDS)


def forecast(weeks: int = WEEKS) -> Doc:
    """The next `weeks` weeks after the current one, per product: forecast qty vs normal, the spikes and their reason."""
    fp = _fingerprint()
    tb = _table(fp)
    origin = fp[0]
    if not len(tb["keys"]):  # no sales history (empty database)
        return {"origin": Date.fromordinal(origin).isoformat(), "weeks": [], "products": [], "price": {}, "topFeatures": []}
    model = _model(origin, fp)
    _, _, price = _weekly()
    rows = np.where((tb["origin"] == origin) & (tb["X"][:, 0] <= weeks))[0]
    pred = model.predict(tb["X"][rows]).clip(0) if len(rows) else np.array([])
    iy, iy2 = FEATURES.index("lastYear"), FEATURES.index("normal")
    products: dict[str, Doc] = {}
    week_tot: dict[Date, Doc] = {}
    for r, ratio in zip(rows, pred):
        o, k, p = tb["keys"][r]
        t = o + W * k
        normal = float(tb["X"][r, iy2])
        qty = float(ratio) * normal
        eur = price.get(p, 0.0)
        spike = qty >= (1 + SPIKE) * normal and (qty - normal) * eur >= MIN_SPIKE_EUR
        ly = float(tb["X"][r, iy])
        why = _why(t, tb["X"][r]) if spike else []
        wk = {"week": t.isoformat(), "qty": round(qty, 1), "normal": round(normal, 1), "vsNormal": round(qty / normal - 1, 3),
              "spike": spike, "events": week_events(t), "lastYear": None if math.isnan(ly) else round(ly - 1, 3), "why": why}  # fmt: skip
        products.setdefault(p, {"productId": p, "weeks": []})["weeks"].append(wk)
        tot = week_tot.setdefault(t, {"week": t.isoformat(), "expectedHt": 0.0, "normalHt": 0.0, "spikeHt": 0.0, "events": wk["events"]})
        tot["expectedHt"] += qty * eur
        tot["normalHt"] += normal * eur
        if spike:
            tot["spikeHt"] += (qty - normal) * eur
    for v in week_tot.values():
        v["vsNormal"] = round(v["expectedHt"] / v["normalHt"] - 1, 3) if v["normalHt"] else None
        for k in ("expectedHt", "normalHt", "spikeHt"):
            v[k] = round(v[k], 2)
    imp = model.feature_importance("gain")
    total = float(imp.sum()) or 1.0
    top = sorted(zip(tb["names"], imp), key=lambda x: -x[1])[:16]
    return {"origin": Date.fromordinal(origin).isoformat(), "weeks": [week_tot[k] for k in sorted(week_tot)],
            "products": list(products.values()), "price": price,
            "topFeatures": [{"name": n, "share": round(float(v) / total, 3)} for n, v in top]}  # fmt: skip


def _why(t: Date, x: np.ndarray) -> list[str]:
    """Plain-French reasons for a spike week, most specific first: the events in or just before it, then the patterns."""
    f = dict(zip(FEATURES, x))
    out = [*week_events(t)]
    soon = [title for v in _spans().values() for a, _, title in v if 0 < (a - t).days <= 14]
    out += [f"juste avant {title}" for title in soon]
    if f["lastYear"] >= 1 + SPIKE:
        out.append(f"saison : +{round((f['lastYear'] - 1) * 100)} % l'an dernier à la même semaine")
    if f["yearGrowth"] >= 1.15:
        out.append(f"en croissance : +{round((f['yearGrowth'] - 1) * 100)} % sur un an")
    if f["clientsGrowth"] >= 1.15:
        out.append(f"plus de clients qu'il y a un an (+{round((f['clientsGrowth'] - 1) * 100)} %)")
    if f["last4"] >= 1.15:
        out.append(f"tendance des 4 dernières semaines : +{round((f['last4'] - 1) * 100)} %")
    if not math.isnan(f["climateTmax"]) and f["climateTmax"] >= 26:
        out.append(f"été (normale de saison {round(f['climateTmax'])} °C)")
    return out or ["saison et tendance combinées (aucune cause seule)"]


@lru_cache(maxsize=2)
def _backtest(fp: tuple, weeks_back: int, retrain: int) -> Doc:
    tb = _table(fp)
    _, _, price = _weekly()
    origin_now = fp[0]
    pids = sorted(price)
    ix, ily = FEATURES.index("normal"), FEATURES.index("lastYear")
    rows: list[tuple] = []  # horizon, origin, pred, normal, last-year, actual, price, pred without the event calendar
    first = origin_now - 7 * weeks_back
    for c in [] if not len(tb["keys"]) else range(first, origin_now, 7 * retrain):
        m = (tb["origin"] >= c) & (tb["origin"] < min(c + 7 * retrain, origin_now)) & (tb["target"] < origin_now)
        idx = np.where(m)[0]
        if not len(idx):
            continue
        pred = _model(c, fp).predict(tb["X"][idx]).clip(0)
        bare = _model(c, fp, False).predict(tb["X"][idx][:, :PATTERNS]).clip(0)
        for i, r, rb in zip(idx, pred, bare):
            x = tb["X"][i]
            normal = float(x[ix])
            ly = float(x[ily])
            rows.append((int(x[0]), int(tb["origin"][i]), float(r) * normal, normal, normal * (ly if not math.isnan(ly) else 1.0),
                         float(tb["y"][i]) * normal, price[pids[int(x[1])]], float(rb) * normal))  # fmt: skip
    out: Doc = {"weeksBack": weeks_back, "rows": len(rows), "horizons": {}}
    for label, (h0, h1) in {"1-2 sem.": (1, 2), "3-4 sem.": (3, 4), "5-8 sem.": (5, 8), "9-12 sem.": (9, 12), "tout": (1, 12)}.items():
        sel = [r for r in rows if h0 <= r[0] <= h1]
        act = sum(r[5] * r[6] for r in sel) or 1.0
        res: Doc = {}
        for name, j in (("model", 2), ("patternsOnly", 7), ("normal", 3), ("lastYear", 4)):
            err = sum(abs(r[j] - r[5]) * r[6] for r in sel) / act
            spikes = [r for r in sel if r[5] >= (1 + SPIKE) * r[3] and (r[5] - r[3]) * r[6] >= MIN_SPIKE_EUR]
            flagged = [r for r in sel if r[j] >= (1 + SPIKE) * r[3] and (r[j] - r[3]) * r[6] >= MIN_SPIKE_EUR]
            hit = [r for r in flagged if r[5] >= (1 + SPIKE) * r[3] and (r[5] - r[3]) * r[6] >= MIN_SPIKE_EUR]
            extra = sum((r[5] - r[3]) * r[6] for r in spikes) or 1.0
            caught = sum(min(max(0.0, r[j] - r[3]), r[5] - r[3]) * r[6] for r in spikes)
            res[name] = {"error": round(err, 3), "spikeRecall": round(len(hit) / len(spikes), 3) if spikes else None,
                         "spikePrecision": round(len(hit) / len(flagged), 3) if flagged else None, "extraCaught": round(caught / extra, 3)}  # fmt: skip
        res["spikes"] = sum(r[5] >= (1 + SPIKE) * r[3] and (r[5] - r[3]) * r[6] >= MIN_SPIKE_EUR for r in sel)
        out["horizons"][label] = res
    return out


def backtest(weeks_back: int = 52, retrain: int = 4) -> Doc:
    """Walk-forward: for each of the last `weeks_back` origins, the 12 weeks ahead forecast with only the weeks before it
    known (model retrained every `retrain` weeks). Error = Σ |forecast − actual| € ÷ Σ actual €, per horizon; spike recall /
    precision; extraCaught = share of the € above normal in the real spikes that the forecast had seen coming."""
    return _backtest(_fingerprint(), weeks_back, retrain)


def warm() -> None:
    fp = _fingerprint()
    if len(_table(fp)["keys"]):
        _model(fp[0], fp)


if __name__ == "__main__":
    import json
    import sys
    import time

    from app.core.db import load_all

    load_all()
    feeds.materialize()
    t = time.time()
    fp = _fingerprint()
    tb = _table(fp)
    print("table", tb["X"].shape, round(time.time() - t, 1), "s", flush=True)
    t = time.time()
    f = forecast()
    print("train+forecast", round(time.time() - t, 1), "s", json.dumps(f["topFeatures"], ensure_ascii=False), flush=True)
    for w in f["weeks"]:
        print(" ", w["week"], f"{w['expectedHt'] / 1000:6.1f} k€ vs {w['normalHt'] / 1000:6.1f}", f"{w['vsNormal']:+.0%}", w["events"])
    t = time.time()
    print(json.dumps(backtest(int(sys.argv[1]) if len(sys.argv) > 1 else 52), ensure_ascii=False, indent=1), round(time.time() - t, 1), "s")

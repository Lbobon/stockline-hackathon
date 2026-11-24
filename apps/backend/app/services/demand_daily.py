"""Day-level demand model: € HT per client segment × product category × delivery day, over the whole history.

Where demand_gbm (client × product) decides each client's basket, this one answers the wholesaler's question before
an event: how much lamb do the butchers buy in the 10 days before the Aïd, how much less do canteens take in the
holidays. One LightGBM regressor on every past delivery day (3 years, 7 segments × 8 categories), with:
    calendar    weekday, month, day of year, trend
    history     same weekday last week, median and mean of the last 8 same weekdays, same weekday last year (±1 week)
    events      for every measured calendar type: inside it, days since it started, days to its end, days until the
                next one starts (≤ 21 days: the buying before an Aïd), so a moving event (ramadan shifts 11 days a
                year) is learned by its position, not by its date
    weather     Open-Meteo tmax, rain, wind of the day
Backtest: walk-forward, one month at a time (trained on every day before the 1st, each day forecast the day before),
compared with the median of the last 8 same weekdays, the 60-day average and last year's same weekday.

    python -m app.services.demand_daily          # prints the backtest
"""

from __future__ import annotations

import math
from datetime import date as Date
from datetime import timedelta
from statistics import mean, median

import lightgbm as lgb
import numpy as np

from app import crud
from app.core.activity import today
from app.core.db import Doc, per_data_version
from app.services import demand, feeds
from app.services.clients import is_sale

SEGS = (*demand.SEGMENTS, "autres")
LEAD = 21  # days before an event start that count as « before it »
PARAMS = {"objective": "tweedie", "tweedie_variance_power": 1.3, "learning_rate": 0.04, "num_leaves": 31, "min_data_in_leaf": 20, "bagging_fraction": 0.8,
          "bagging_freq": 1, "feature_fraction": 0.8, "seed": 7, "verbose": -1, "num_threads": 4}  # fmt: skip
ROUNDS = 600


def _d(s: str) -> Date:
    return Date.fromisoformat(s)


@per_data_version
def _sales() -> tuple[dict[tuple[str, str, str], float], list[str], list[str]]:
    """(day, segment, category) → € HT of delivered sales, the sorted delivery days, the categories."""
    seg = {c["id"]: demand.segment_of(c) for c in crud.clients.all()}
    cat = {p["id"]: p["category"] for p in crud.products.all()}
    out: dict[tuple[str, str, str], float] = {}
    for o in crud.orders.rows.values():
        if not is_sale(o):
            continue
        s = seg.get(o["clientId"], "autres")
        for l in o["lines"]:
            k = (o["deliveryDate"], s, cat.get(l["productId"], "autre"))
            out[k] = out.get(k, 0.0) + l["qty"] * l["unitPrice"]
    return out, sorted({k[0] for k in out}), sorted({k[2] for k in out})


@per_data_version
def _calendar() -> tuple[tuple[str, ...], dict[str, list[tuple[Date, Date]]]]:
    """Measured event types and their (from, to) spans; the team's estimates (no history) are left out."""
    spans: dict[str, list[tuple[Date, Date]]] = {}
    for e in crud.calendar.rows.values():
        if demand.measured(e):
            spans.setdefault(e["type"], []).append((_d(e["affects"]["from"]), _d(e["affects"]["to"])))
    for v in spans.values():
        v.sort()
    return tuple(sorted(spans)), spans


def _event_features(day: Date, types: tuple[str, ...], spans: dict[str, list[tuple[Date, Date]]]) -> list[float]:
    out = []
    for t in types:
        inside, since, to_end, until = 0.0, -1.0, -1.0, float(LEAD + 1)
        for a, b in spans[t]:
            if a <= day <= b:
                inside, since, to_end = 1.0, (day - a).days, (b - day).days
            elif day < a and (a - day).days <= LEAD:
                until = min(until, (a - day).days)
        out += [inside, since, to_end, until]
    return out


class _Hist:
    """Lagged values from the actual sales. With a cutoff (forecasting ahead), days on or after it are unknown: the
    same-weekday lags reach further back, so « last week » is the last one actually delivered."""

    def __init__(self, sales: dict[tuple[str, str, str], float], cutoff: str | None = None):
        self.s, self.cutoff = sales, cutoff

    def get(self, day: Date, seg: str, cat: str) -> float:
        return self.s.get((day.isoformat(), seg, cat), 0.0)

    def same_weekdays(self, day: Date, seg: str, cat: str, n: int, delivered: set[str]) -> list[float]:
        out, d = [], day - timedelta(days=7)
        while len(out) < n and d > day - timedelta(days=7 * (n + 12)):
            if d.isoformat() in delivered and (self.cutoff is None or d.isoformat() < self.cutoff):
                out.append(self.get(d, seg, cat))
            d -= timedelta(days=7)
        return out


def _rows(days: list[str], segs, cats, hist: _Hist, delivered: set[str]) -> tuple[list[tuple], np.ndarray, np.ndarray]:
    types, spans = _calendar()
    weather = feeds.weather_days()
    t0 = _d("2023-01-01")
    keys, X, y = [], [], []
    for ds in days:
        day = _d(ds)
        ev = _event_features(day, types, spans)
        w = weather.get(ds) or {}
        cal = [day.weekday(), day.month, day.timetuple().tm_yday, (day - t0).days,
               w.get("tmax", math.nan), w.get("precip", math.nan), w.get("wind", math.nan)]  # fmt: skip
        for si, s in enumerate(segs):
            for ci, c in enumerate(cats):
                wk = hist.same_weekdays(day, s, c, 8, delivered)
                ly = [hist.get(day - timedelta(days=k), s, c) for k in (357, 364, 371)]
                lag = [wk[0] if wk else math.nan, median(wk) if wk else math.nan, mean(wk) if wk else math.nan,
                       mean(ly), hist.get(day - timedelta(days=364), s, c)]  # fmt: skip
                keys.append((ds, s, c))
                X.append([si, ci, *cal, *lag, *ev])
                y.append(hist.get(day, s, c))
    return keys, np.array(X, dtype=float), np.array(y, dtype=float)


def _baselines(day: Date, s: str, c: str, hist: _Hist, delivered: set[str]) -> dict[str, float]:
    wk = hist.same_weekdays(day, s, c, 8, delivered)
    last60 = [hist.get(day - timedelta(days=k), s, c) for k in range(7, 61, 7) if (day - timedelta(days=k)).isoformat() in delivered]
    return {"median8": median(wk) if wk else 0.0, "avg60": mean(last60) if last60 else 0.0,
            "lastYear": hist.get(day - timedelta(days=364), s, c)}  # fmt: skip


def backtest(months: int = 12) -> Doc:
    """Walk-forward over the last `months` months before today: revenue error per day, for the model and the baselines,
    on all days, normal days and each event type's days."""
    sales, days, cats = _sales()
    hist, delivered = _Hist(sales), set(days)
    past = [d for d in days if d < today()]
    first = _d(today()).replace(day=1)
    starts = sorted({(first - timedelta(days=31 * k)).replace(day=1) for k in range(months)})
    types, spans = _calendar()
    preds: dict[str, dict[str, float]] = {}  # day → model / baselines / actual (€ HT, all segments and categories)
    for m0 in starts:
        m1 = (m0 + timedelta(days=32)).replace(day=1)
        train = [d for d in past if d < m0.isoformat() and _d(d) >= _d(past[0]) + timedelta(days=371)]  # a year of lags first
        test = [d for d in past if m0.isoformat() <= d < m1.isoformat()]
        if not test or len(train) < 200:
            continue
        _, Xtr, ytr = _rows(train, SEGS, cats, hist, delivered)
        model = lgb.train(PARAMS, lgb.Dataset(Xtr, ytr, categorical_feature=[0, 1]), ROUNDS)
        keys, Xte, yte = _rows(test, SEGS, cats, hist, delivered)
        p = model.predict(Xte).clip(0)
        for (ds, s, c), pv, av in zip(keys, p, yte):
            b = _baselines(_d(ds), s, c, hist, delivered)
            r = preds.setdefault(ds, {"model": 0.0, "actual": 0.0, "median8": 0.0, "avg60": 0.0, "lastYear": 0.0})
            r["model"] += pv
            r["actual"] += av
            for k, v in b.items():
                r[k] += v
    in_type = {ds: [t for t in types if any(a <= _d(ds) <= b for a, b in spans[t])] for ds in preds}
    groups = {"tous les jours": list(preds), "jours normaux": [d for d in preds if not in_type[d]],
              "jours d'événement": [d for d in preds if in_type[d]]}  # fmt: skip
    for t in types:
        if ds_t := [d for d in preds if t in in_type[d]]:
            groups[t] = ds_t
    out = {}
    for g, ds_list in groups.items():
        act = sum(preds[d]["actual"] for d in ds_list)
        if not act:
            continue
        out[g] = {"days": len(ds_list), **{k: round(sum(abs(preds[d][k] - preds[d]["actual"]) for d in ds_list) / act, 3)
                                          for k in ("model", "median8", "avg60", "lastYear")},
                  "ratio": {k: round(sum(preds[d][k] for d in ds_list) / act, 3) for k in ("model", "median8", "avg60")}}  # fmt: skip
    return {"months": months, "days": len(preds), "error": out}


FEATURES = ["segment", "category", "weekday", "month", "dayOfYear", "trend", "tmax", "rain", "wind",
            "lastWeek", "median8Weeks", "mean8Weeks", "lastYear3Weeks", "lastYear"]


@per_data_version
def _trained() -> tuple[lgb.Booster, list[str]]:
    """The model on every delivered day before today (retrained after a write, on the next outlook)."""
    sales, days, cats = _sales()
    as_of = today()
    past = [d for d in days if d < as_of and _d(d) >= _d(days[0]) + timedelta(days=371)]
    _, X, y = _rows(past, SEGS, cats, _Hist(sales), set(days))
    return lgb.train(PARAMS, lgb.Dataset(X, y, categorical_feature=[0, 1]), ROUNDS), cats


def outlook(days: int = 14) -> Doc:
    """The next `days` delivery days (Mon–Sat after today): € HT expected per day and per segment, against a normal
    week (median of the last 8 same weekdays), with the events that cover or come up on each day."""
    sales, all_days, _ = _sales()
    model, cats = _trained()
    hist, delivered = _Hist(sales, cutoff=today()), set(all_days)
    ahead, d = [], _d(today())
    while len(ahead) < days:
        d += timedelta(days=1)
        if d.weekday() < 6:
            ahead.append(d.isoformat())
    delivered |= set(ahead)  # the coming days count as delivery days for the lags' calendar, never for their values
    keys, X, _ = _rows(ahead, SEGS, cats, hist, delivered)
    p = model.predict(X).clip(0)
    types, spans = _calendar()
    titles = {}
    for e in crud.calendar.rows.values():
        if demand.measured(e):
            titles.setdefault((e["type"], e["affects"]["from"]), e["title"])
    out = {ds: {"date": ds, "expectedHt": 0.0, "normalHt": 0.0, "segments": {}} for ds in ahead}
    for (ds, s, c), v in zip(keys, p):
        n = _baselines(_d(ds), s, c, hist, delivered)["median8"]
        r = out[ds]
        r["expectedHt"] += v
        r["normalHt"] += n
        seg = r["segments"].setdefault(s, {"expectedHt": 0.0, "normalHt": 0.0})
        seg["expectedHt"] += v
        seg["normalHt"] += n
    for ds, r in out.items():
        day = _d(ds)
        r["events"] = [titles.get((t, a.isoformat()), t) for t in types for a, b in spans[t] if a <= day <= b]
        r["comingUp"] = [{"title": titles.get((t, a.isoformat()), t), "inDays": (a - day).days} for t in types
                         for a, _ in spans[t] if 0 < (a - day).days <= LEAD]  # fmt: skip
        r["vsNormal"] = round(r["expectedHt"] / r["normalHt"] - 1, 3) if r["normalHt"] else None
        r["expectedHt"], r["normalHt"] = round(r["expectedHt"], 2), round(r["normalHt"], 2)
        for v in r["segments"].values():
            v["vsNormal"] = round(v["expectedHt"] / v["normalHt"] - 1, 3) if v["normalHt"] else None
            v["expectedHt"], v["normalHt"] = round(v["expectedHt"], 2), round(v["normalHt"], 2)
    imp = model.feature_importance("gain")
    names = FEATURES + [f"{t}:{k}" for t in types for k in ("inside", "since", "toEnd", "until")]
    top = sorted(zip(names, imp), key=lambda x: -x[1])[:12]
    total = float(imp.sum()) or 1.0
    return {"asOf": today(), "days": list(out.values()), "topFeatures": [{"name": n, "share": round(float(v) / total, 3)} for n, v in top]}


if __name__ == "__main__":
    import json

    from app.core.db import load_all

    load_all()
    print(json.dumps(backtest(), ensure_ascii=False, indent=1))

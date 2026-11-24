"""LightGBM demand model: which of a client's products they take on a delivery day, and how much. The radar's third
model, beside « median × measured uplift » and the plain average; services/radar_backtest scores all three.

Who orders stays the radar's 12-weekday rhythm. Given the client orders that day, for each product they bought in the
last 90 days or around the same date last year (330 to 400 days before):
    classifier  P(the product is on the order)
    regressor   log(qty ÷ ref), L1 (a median, like the radar), ref = median on the last 8 same weekdays, else the
                90-day average, else the last quantity
Features: the client's history up to the day before (frequencies, medians, last quantity, days since), weekday,
month, segment, product and category, the measured calendar events that day that target the client (ramadan, aïds,
holidays, OM home matches, weather events…) and the Open-Meteo weather (tmax, rain, wind) when the feed has it.
Trained on every client-day before the 1st of the month of `as_of` (a backtest never sees its own days), cached.
"""

from __future__ import annotations

import math
import time
from bisect import bisect_left
from datetime import date as Date
from functools import lru_cache
from statistics import mean, median

import lightgbm as lgb
import numpy as np

from app import crud
from app.core.activity import today
from app.core.db import Doc, per_data_version
from app.services import demand, feeds

WD, RECENT, LAST_YEAR, MIN_HISTORY = 8, 90, (330, 400), 56
NAN = float("nan")
NUM = ("ref", "wd_n", "wd_freq", "wd_med", "r_n", "r_freq", "r_avg", "since", "last", "ly_n", "ly_avg", "month", "tmax", "precip", "wind")
CAT = ("weekday", "segment", "product", "category")
ROUNDS = 200
PARAMS = {"learning_rate": 0.05, "num_leaves": 31, "min_data_in_leaf": 40, "bagging_fraction": 0.8, "bagging_freq": 1,
          "feature_fraction": 0.9, "seed": 7, "verbose": -1, "num_threads": 4}  # fmt: skip


def _ord(d: str) -> int:
    return Date.fromisoformat(d).toordinal()


@per_data_version
def _hist() -> dict[str, tuple[list[int], dict[int, dict[str, float]]]]:
    """client → (sorted delivery days as ordinals, day → product → qty), delivered sales only."""
    return {c: (sorted(q := {_ord(d): v for d, v in days.items()}), q) for c, days in demand._index()["qty"].items()}


@per_data_version
def _codes() -> Doc:
    prods = sorted(crud.products.rows)
    cats = sorted({p["category"] for p in crud.products.all()})
    return {"segment": {s: i for i, s in enumerate((*demand.SEGMENTS, "autres"))}, "product": {p: i for i, p in enumerate(prods)},
            "category": {p["id"]: cats.index(p["category"]) for p in crud.products.all()}}  # fmt: skip


@per_data_version
def _events() -> dict[int, list[Doc]]:
    """day → the measured calendar events covering it."""
    out: dict[int, list[Doc]] = {}
    for e in crud.calendar.rows.values():
        if demand.measured(e):
            for d in demand._days(e):
                out.setdefault(_ord(d), []).append(e)
    return out


_weather: tuple[float, dict[int, Doc]] = (0.0, {})


def _weather_days() -> dict[int, Doc]:
    """shortcut: re-read every minute (the feed table changes outside the ERP's data version)."""
    global _weather
    if time.monotonic() - _weather[0] > 60:
        _weather = (time.monotonic(), {_ord(d): w for d, w in feeds.weather_days().items()})
    return _weather[1]


def _rows(client: Doc, day: int, anchor: int, types: tuple[str, ...]) -> list[tuple[str, list[float]]]:
    """(product, features) for every candidate product of `client` on `day`, from its history strictly before `anchor`."""
    ords, q = _hist().get(client["id"], ([], {}))
    i = bisect_left(ords, anchor)
    wd = [q[x] for k in range(1, WD + 1) if (x := day - 7 * k) < anchor and x in q]
    recent = [q[x] for x in ords[bisect_left(ords, anchor - RECENT) : i]]
    ly = [q[x] for x in ords[bisect_left(ords, day - LAST_YEAR[1]) : bisect_left(ords, min(day - LAST_YEAR[0], anchor))]]
    last: dict[str, tuple[int, float]] = {}
    for x in reversed(ords[bisect_left(ords, anchor - RECENT) : i]):
        for p, v in q[x].items():
            last.setdefault(p, (x, v))
    cands = sorted({p for o in (*recent, *ly) for p in o})
    if not cands:
        return []
    codes, w = _codes(), _weather_days().get(day, {})
    seg = demand.segment_of(client)
    on = {e["type"] for e in _events().get(day, []) if demand.targets(e, client)}
    dt = Date.fromordinal(day)
    shared = [dt.month, w.get("tmax", NAN), w.get("precip", NAN), w.get("wind", NAN)]
    flags = [float(t in on) for t in types]
    out = []
    for p in cands:
        wq = [o[p] for o in wd if p in o]
        rq = [o[p] for o in recent if p in o]
        lq = [o[p] for o in ly if p in o]
        wd_med = median(wq) if wq else NAN
        r_avg = mean(rq) if rq else NAN
        ly_avg = mean(lq) if lq else NAN
        since, last_q = (anchor - last[p][0], last[p][1]) if p in last else (999, NAN)
        ref = next((v for v in (wd_med, r_avg, last_q, ly_avg) if v > 0), NAN)  # NaN > 0 is False
        out.append((p, [ref, len(wd), len(wq) / len(wd) if wd else NAN, wd_med, len(recent), len(rq) / len(recent) if recent else 0.0,
                        r_avg, since, last_q, len(lq), ly_avg, *shared, dt.weekday(), codes["segment"].get(seg, 0),
                        codes["product"].get(p, -1), codes["category"].get(p, -1), *flags]))  # fmt: skip
    return out


def _types() -> tuple[str, ...]:
    return tuple(sorted({e["type"] for es in _events().values() for e in es}))


@per_data_version
def _fingerprint() -> tuple:
    """What a trained model depends on: the delivered history up to today, the measured calendar, the weather."""
    t, hist = _ord(today()), _hist()
    n = sum(bisect_left(o, t + 1) for o, _ in hist.values())
    return (n, len(_events()), _types(), len(_weather_days()))


@lru_cache(maxsize=1)
def _table(fp: tuple) -> Doc:
    """Every client-day with an order up to today: features, did the product come (0/1), log(qty ÷ ref)."""
    types, t = fp[2], _ord(today())
    X, y, r, days = [], [], [], []
    for c in crud.clients.all():
        ords, q = _hist().get(c["id"], ([], {}))
        for day in ords:
            if day > t or day - ords[0] < MIN_HISTORY:
                continue
            for p, f in _rows(c, day, day, types):
                X.append(f)
                y.append(p in q[day])
                r.append(math.log(q[day][p] / f[0]) if p in q[day] and f[0] > 0 and q[day][p] > 0 else NAN)
                days.append(day)
    return {"X": np.array(X, dtype=float), "y": np.array(y), "r": np.array(r), "day": np.array(days)}


@lru_cache(maxsize=16)
def _model(cutoff: int, fp: tuple) -> tuple[lgb.Booster, lgb.Booster] | None:
    t = _table(fp)
    m = t["day"] < cutoff
    if m.sum() < 1000:
        return None
    cat = [len(NUM) + i for i in range(len(CAT))]
    pos = m & ~np.isnan(t["r"])
    clf = lgb.train({**PARAMS, "objective": "binary"}, lgb.Dataset(t["X"][m], t["y"][m].astype(float), categorical_feature=cat), ROUNDS)
    reg = lgb.train({**PARAMS, "objective": "l1"}, lgb.Dataset(t["X"][pos], t["r"][pos], categorical_feature=cat), ROUNDS)
    return clf, reg


def predict(client: Doc, date: str, as_of: str) -> list[Doc] | None:
    """[{productId, p, qty, ref}] for `client` on `date`, from history up to `as_of` (included); None when there is too
    little history to train on."""
    anchor = _ord(as_of) + 1
    cutoff = _ord(as_of[:8] + "01")
    fp = _fingerprint()
    model = _model(cutoff, fp)
    if model is None:
        return None
    rows = [(p, f) for p, f in _rows(client, _ord(date), min(anchor, _ord(date)), fp[2]) if f[0] > 0]
    if not rows:
        return []
    X = np.array([f for _, f in rows], dtype=float)
    p = model[0].predict(X)
    r = model[1].predict(X)
    return [{"productId": pid, "p": float(p[i]), "qty": f[0] * math.exp(float(r[i])), "ref": f[0]} for i, (pid, f) in enumerate(rows)]


def warm() -> None:
    """Train this month's model (boot): the first forecast or radar_suggestion during a call doesn't wait for it."""
    _model(_ord(today()[:8] + "01"), _fingerprint())

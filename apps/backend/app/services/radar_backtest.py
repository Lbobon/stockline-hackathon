"""Radar backtest: how good would the forecast have been on past delivery days, and how much better than the plain
average? (the numbers in DECISIONS.md)

Each day is forecast as of the day before (radar.forecast_client with as_of: history up to then, no existing orders,
uplift measured only from events finished before) and compared with the sales actually delivered that day.
Three models, same clients (who orders comes from the same 12-weekday rhythm), different lines and quantities:
    radar     median of the last 8 same weekdays × the uplift measured per event type and segment, plus the
              products a client only buys for the event
    gbm       LightGBM (services/demand_gbm.py), trained only on the days before the 1st of the scored day's month
    average   the client's plain 60-day average basket, no events (what the radar did before)
The flat fields and the pitch are the model in use (radar.MODEL).
Scores:
    clients     precision / recall on "will order"
    revenue     € forecast for the day vs € delivered: Σ |forecast − delivered| ÷ Σ delivered over the days
                (€ forecast = Σ probability × Σ product × how often the client takes it)
    client €    Σ over clients |proposed total − delivered total| ÷ Σ delivered (a missed client counts in full)
    quantities  on the lines both proposed and ordered: mean absolute % error, share within ±20 %
Day sets: the last `weeks` same weekdays (normal weeks), and the event days of the past year (ramadan, aïd el-kébir,
christmas, christmas markets, school holidays): one day a week inside each window, every day for a window under
8 days.
"""

from __future__ import annotations

from app import crud
from app.core.activity import today
from app.core.db import Doc
from app.core.utils import add_days, round2, weekday_of
from app.services import demand, radar
from app.services.clients import is_sale

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
MODELS = ("radar", "average", "gbm")
LABEL = {"mon": "lundis", "tue": "mardis", "wed": "mercredis", "thu": "jeudis", "fri": "vendredis", "sat": "samedis", "sun": "dimanches"}
KEYS = ("predicted", "actual", "hits", "clientErr", "lines", "apeSum", "within20", "expectedHt", "actualHt")


def _past_days(weekday: str, weeks: int) -> list[str]:
    d = add_days(today(), -1)
    while weekday_of(d) != weekday:
        d = add_days(d, -1)
    return [add_days(d, -7 * k) for k in range(weeks)][::-1]


def _price(pid: str) -> float:
    p = crud.products.get(pid)
    return float(p["price"]) if p else 0.0


def _actual(day: str) -> dict[str, Doc]:
    out: dict[str, Doc] = {}
    for o in crud.orders.rows.values():
        if o["deliveryDate"] == day and is_sale(o):
            a = out.setdefault(o["clientId"], {"qty": {}, "ht": 0.0})
            a["ht"] += o["totalHt"]
            for l in o["lines"]:
                a["qty"][l["productId"]] = a["qty"].get(l["productId"], 0) + l["qty"]
    return out


def backtest_day(day: str, model: str = "radar", actual: dict[str, Doc] | None = None) -> Doc:
    as_of = add_days(day, -1)
    actual = _actual(day) if actual is None else actual
    evs = demand.in_window(day, day)
    pred = {c["id"]: f for c in crud.clients.all() if (f := radar.forecast_client(c, day, evs, as_of=as_of, model=model))}
    client_err = 0.0
    ape: list[float] = []
    for cid in set(pred) | set(actual):
        f, a = pred.get(cid), actual.get(cid)
        client_err += abs((f["totalHt"] if f else 0) - (a["ht"] if a else 0))
        if f and a:
            for l in f["lines"]:
                if (real := a["qty"].get(l["productId"])):
                    ape.append(abs(l["qty"] - real) / real)
    return {"date": day, "predicted": len(pred), "actual": len(actual), "hits": len(set(pred) & set(actual)),
            "clientErr": client_err, "lines": len(ape), "apeSum": sum(ape), "within20": sum(e <= 0.2 for e in ape),
            "expectedHt": sum(f["expectedHt"] for f in pred.values()),
            "actualHt": sum(a["ht"] for a in actual.values())}  # fmt: skip


def _summary(days: list[Doc]) -> Doc:
    t = {k: sum(d[k] for d in days) for k in KEYS}
    ht = t["actualHt"] or 1
    return {"days": len(days),
            "precision": round2(t["hits"] / t["predicted"]) if t["predicted"] else 0,
            "recall": round2(t["hits"] / t["actual"]) if t["actual"] else 0,
            "revenueError": round2(sum(abs(d["expectedHt"] - d["actualHt"]) for d in days) / ht),
            "revenueRatio": round2(t["expectedHt"] / ht),
            "clientError": round2(t["clientErr"] / ht),
            "quantityMape": round2(t["apeSum"] / t["lines"]) if t["lines"] else 0,
            "quantityWithin20": round2(t["within20"] / t["lines"]) if t["lines"] else 0}  # fmt: skip


def _compare(days: list[str]) -> Doc:
    """Both models on the same days."""
    actual = {d: _actual(d) for d in days}
    out = {m: [backtest_day(d, m, actual[d]) for d in days] for m in MODELS}
    s = {m: _summary(out[m]) for m in MODELS}
    def gain(m: str, vs: str) -> Doc:
        return {k: round2(1 - s[m][k] / s[vs][k]) if s[vs][k] else 0 for k in ("revenueError", "clientError", "quantityMape")}

    # errorReduction: the model in use (radar.MODEL) against the plain average; gbmVsRadar: LightGBM against median × uplift
    return {**s, "model": radar.MODEL, "errorReduction": gain(radar.MODEL, "average"), "gbmVsRadar": gain("gbm", "radar"),
            "days": [{"date": d, "actualHt": round2(out["radar"][i]["actualHt"]),
                      **{m: round2(out[m][i]["expectedHt"]) for m in MODELS}} for i, d in enumerate(days)]}  # fmt: skip


def event_days(since: str, until: str) -> dict[str, list[str]]:
    """type → the days to score: one per week inside each window (its own weekday), every day for a window under 8
    days; Sundays (no delivery) left out."""
    out: dict[str, list[str]] = {}
    for e in demand.in_window(since, until):
        if not demand.measured(e) or e["affects"]["to"] >= until:
            continue
        lo, hi = max(e["affects"]["from"], since), e["affects"]["to"]
        step = 1 if demand.days_count(e) < 8 else 7
        d = lo if step == 1 else add_days(lo, 3)  # mid-week, away from the window's edges
        while d <= hi:
            if weekday_of(d) != "sun":
                out.setdefault(e["type"], []).append(d)
            d = add_days(d, step)
    return out


def backtest(weeks: int = 8, weekday: str = "wed") -> Doc:
    """GET /api/radar/backtest: both models replayed on the last `weeks` `weekday`s (normal weeks)."""
    if weekday not in DAYS:
        weekday = "wed"
    days = _past_days(weekday, max(1, min(weeks, 26)))
    res = _compare(days)
    r, a = res[radar.MODEL], res["average"]
    return {
        "weekday": weekday, "weeks": len(days), **res,
        # kept flat for the radar page
        "precision": r["precision"], "recall": r["recall"], "revenueRatio": r["revenueRatio"],
        "pitch": f"Sur les {len(days)} derniers {LABEL[weekday]}, le radar a vu venir {round(r['recall'] * 100)} % des commandes "
                 f"({round(r['precision'] * 100)} % de ses prévisions justes) ; chiffre d'affaires du jour à {round(r['revenueError'] * 100)} % près "
                 f"(simple moyenne : {round(a['revenueError'] * 100)} %), quantités à ±{round(r['quantityMape'] * 100)} % "
                 f"(moyenne : ±{round(a['quantityMape'] * 100)} %).",
    }  # fmt: skip


def backtest_events(months: int = 12) -> Doc:
    """GET /api/radar/backtest/events: both models on the event days of the last `months` months, per event type."""
    until = today()
    since = add_days(until, -30 * max(1, min(months, 36)))
    per = {typ: _compare(days) for typ, days in event_days(since, until).items()}
    every = [d for v in event_days(since, until).values() for d in v]
    total = _compare(sorted(set(every)))
    return {"since": since, "until": until, "types": per, "all": {k: v for k, v in total.items() if k != "days"}}

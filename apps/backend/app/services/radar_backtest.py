"""Radar backtest: how good would the forecast have been on past delivery days? (the pitch line)

For each of the last `weeks` same weekdays (default: Wednesdays), forecast every client as of the day before
(radar.forecast_client with as_of: basket from before that day, no existing orders, no events) and compare with the
sales actually delivered that day:
    clients     precision / recall on "will order"
    quantities  per line both predicted and ordered: mean absolute % error, share within ±20 %
    revenue     € predicted (Σ probability × total) vs € delivered
"""

from __future__ import annotations

from app import crud
from app.core.activity import today
from app.core.db import Doc
from app.core.utils import add_days, round2, weekday_of
from app.services import radar
from app.services.clients import is_sale

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _past_days(weekday: str, weeks: int) -> list[str]:
    d = add_days(today(), -1)
    while weekday_of(d) != weekday:
        d = add_days(d, -1)
    return [add_days(d, -7 * k) for k in range(weeks)][::-1]


def backtest_day(day: str) -> Doc:
    as_of = add_days(day, -1)
    actual: dict[str, Doc] = {}
    for o in crud.orders.rows.values():
        if o["deliveryDate"] == day and is_sale(o):
            a = actual.setdefault(o["clientId"], {"qty": {}, "ht": 0.0})
            a["ht"] += o["totalHt"]
            for l in o["lines"]:
                a["qty"][l["productId"]] = a["qty"].get(l["productId"], 0) + l["qty"]
    pred = {c["id"]: f for c in crud.clients.all() if (f := radar.forecast_client(c, day, [], as_of=as_of))}
    hit = set(pred) & set(actual)
    errors = []
    for cid in hit:
        for l in pred[cid]["lines"]:
            real = actual[cid]["qty"].get(l["productId"])
            if real:
                errors.append(abs(l["qty"] - real) / real)
    return {
        "date": day, "predicted": len(pred), "actual": len(actual), "hits": len(hit),
        "lines": len(errors), "within20": sum(e <= 0.2 for e in errors), "errSum": sum(errors),
        "expectedHt": sum(f["totalHt"] * f["probability"] for f in pred.values()),
        "actualHt": sum(a["ht"] for a in actual.values()),
    }  # fmt: skip


def backtest(weeks: int = 8, weekday: str = "wed") -> Doc:
    """GET /api/radar/backtest: the forecast replayed on the last `weeks` `weekday`s."""
    if weekday not in DAYS:
        weekday = "wed"
    days = [backtest_day(d) for d in _past_days(weekday, max(1, min(weeks, 26)))]
    tot = {k: sum(d[k] for d in days) for k in ("predicted", "actual", "hits", "lines", "within20", "errSum", "expectedHt", "actualHt")}
    precision = tot["hits"] / tot["predicted"] if tot["predicted"] else 0
    recall = tot["hits"] / tot["actual"] if tot["actual"] else 0
    within = tot["within20"] / tot["lines"] if tot["lines"] else 0
    mape = tot["errSum"] / tot["lines"] if tot["lines"] else 0
    rev = tot["expectedHt"] / tot["actualHt"] if tot["actualHt"] else 0
    label = {"mon": "lundis", "tue": "mardis", "wed": "mercredis", "thu": "jeudis", "fri": "vendredis", "sat": "samedis", "sun": "dimanches"}[weekday]
    return {
        "weekday": weekday, "weeks": len(days),
        "days": [{"date": d["date"], "predicted": d["predicted"], "actual": d["actual"], "hits": d["hits"],
                  "expectedHt": round2(d["expectedHt"]), "actualHt": round2(d["actualHt"])} for d in days],
        "precision": round2(precision), "recall": round2(recall),
        "quantityWithin20": round2(within), "quantityMape": round2(mape), "revenueRatio": round2(rev),
        "pitch": f"Sur les {len(days)} derniers {label}, le radar a vu venir {round(recall * 100)} % des commandes "
                 f"({round(precision * 100)} % de ses prévisions justes), quantités à ±{round(mape * 100)} % en moyenne, "
                 f"chiffre d'affaires prévu à {round(abs(rev - 1) * 100)} % près.",
    }  # fmt: skip

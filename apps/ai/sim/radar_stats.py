"""Demo stats: run the radar campaign N times on a freshly reset ERP and report the spread of every figure the pitch
quotes (so we never quote one lucky run).

    cd apps/ai && uv run python -m sim.radar_stats --runs 3 [--date 2026-11-25] [--concurrency 8]

Writes sim/runs/radar-stats-<time>.json (git-ignored) and prints a table. Simulated clients only: no real call is placed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import time
from datetime import datetime
from pathlib import Path

import httpx

from app.campaign import engine

RUNS = Path(__file__).parent / "runs"
FIELDS = ["seconds", "calls", "confirmed", "declined", "callback", "revenueHt", "upliftHt", "marginHt", "routeSavedEur",
          "staffMinutesSaved", "staffEurSaved", "gainEur", "routeStopsBefore", "routeStopsAfter"]  # fmt: skip


async def one(date: str, concurrency: int) -> dict:
    erp = os.environ.get("ERP_API_URL", "http://localhost:4000")
    async with httpx.AsyncClient(timeout=30) as http:
        (await http.post(f"{erp}/api/admin/reset")).raise_for_status()
    t = time.perf_counter()
    c = await engine.start(date, None, concurrency)
    before = sum(len(r["stops"]) for r in (c.route or {}).get("optimized", []))
    while c.status != "done":
        await asyncio.sleep(1)
    return {**{k: c.totals.get(k) for k in FIELDS if k in c.totals}, "seconds": round(time.perf_counter() - t), "calls": len(c.calls),
            "routeStopsBefore": before, "routeStopsAfter": sum(len(r["stops"]) for r in (c.route or {}).get("optimized", []))}  # fmt: skip


async def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m sim.radar_stats")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--date", default="2026-11-25")
    ap.add_argument("--concurrency", type=int, default=8)
    a = ap.parse_args()
    rows = []
    for i in range(a.runs):
        r = await one(a.date, a.concurrency)
        rows.append(r)
        print(f"run {i + 1}: " + ", ".join(f"{k}={r[k]}" for k in FIELDS if k in r), flush=True)
    summary = {}
    for k in FIELDS:
        vals = [r[k] for r in rows if r.get(k) is not None]
        if vals:
            summary[k] = {"mean": round(statistics.mean(vals), 1), "min": min(vals), "max": max(vals)}
    print("\nfigure                mean      min      max")
    for k, s in summary.items():
        print(f"{k:20} {s['mean']:>8} {s['min']:>8} {s['max']:>8}")
    RUNS.mkdir(exist_ok=True)
    out = RUNS / f"radar-stats-{datetime.now():%Y%m%d-%H%M%S}.json"
    out.write_text(json.dumps({"runs": rows, "summary": summary}, ensure_ascii=False, indent=1))
    print(f"\n→ {out}")


if __name__ == "__main__":
    asyncio.run(main())

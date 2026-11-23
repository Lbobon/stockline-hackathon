"""Call reports on disk: apps/ai/.calls/{id}.json (git-ignored). Small and local on purpose — a hackathon demo store."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / ".calls"


def save(report: dict) -> None:
    ROOT.mkdir(exist_ok=True)
    (ROOT / f"{report['id']}.json").write_text(json.dumps(report, ensure_ascii=False, default=str), encoding="utf-8")


def load(call_id: str) -> dict | None:
    p = ROOT / f"{Path(call_id).name}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def list_calls(limit: int = 100) -> list[dict]:
    if not ROOT.exists():
        return []
    files = sorted(ROOT.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    out = []
    for p in files:
        try:
            r = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            continue
        ai = r.get("ai") or {}
        out.append(
            {
                "id": r["id"],
                "startedAt": r.get("startedAt"),
                "durationMs": r.get("durationMs"),
                "client": r.get("client"),
                "language": r.get("language"),
                "headline": ai.get("headline"),
                "outcome": ai.get("outcome"),
                "sentiment": (ai.get("sentiment") or {}).get("end"),
                "actions": len(ai.get("for_leila") or []),
                "metrics": r.get("metrics"),
                "source": r.get("source", "voice"),
            }
        )
    return out

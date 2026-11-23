"""
Jev next to the LLM: the small typed decisions of a call (is that a yes? how annoyed is the caller?), in ≈130 ms warm.

One helper for every agent, so they all get the same timeout, fallback and pre-warm:

    from app.agent.jev import decide, warm
    from app.clients.jev import yes_no_question, score_question

    a = await decide({"question": asked, "reponse": heard}, {
        "accepts": yes_no_question("Le client accepte la commande telle qu'elle a été relue."),
        "mood": score_question("Humeur du client", ["content", "neutre", "agacé", "furieux"]),
    })
    if a is None: ...            # no JEV_API_KEY, timeout or error: take the LLM path
    a["accepts"]                 # noul → float 0..1
    a["mood"]                    # score → Score(score, confidence, probabilities); choice → Choice(...)

`warm()` at call start: the first request pays the TLS handshake (≈950 ms measured), the next ones ≈130 ms.
"""

import asyncio
import time
from functools import cache
from typing import Any

from app.clients import get_jev_client
from app.clients.jev import Choice, Content, JevClient, Score, yes_no_question


@cache
def client() -> JevClient | None:
    return get_jev_client()


def available() -> bool:
    return client() is not None


def _value(a: dict) -> Any:
    match a.get("type"):
        case "noul":
            return float(a["noul"])
        case "choice":
            return Choice(a["choice"], a["confidence"], a["probabilities"])
        case "score":
            return Score(a["score"], a["confidence"], a["probabilities"])
    return a


async def decide(state: Content, questions: dict[str, dict], timeout_ms: int = 800) -> dict[str, Any] | None:
    """Ask Jev several named questions about one state, in one request. {name: value} or None (no key, too slow, error):
    the caller then falls back to its LLM path. Never raises."""
    jev = client()
    if jev is None or not questions:
        return None
    started = time.perf_counter()
    try:
        res = await asyncio.wait_for(jev.ask(state, questions), timeout_ms / 1000)
        return {name: _value(a) for name, a in (res.get("answers") or {}).items()}
    except Exception as e:  # TimeoutError included: a decision must never hang a voice turn
        print(f"[jev] {type(e).__name__} after {int((time.perf_counter() - started) * 1000)} ms: {e}", flush=True)
        return None


async def warm() -> None:
    """Open the connection while the line rings, so the first real decision is fast."""
    await decide("allô", {"q": yes_no_question("Quelqu'un parle.")}, timeout_ms=3000)

"""Le Garde-fou: Jev fact-checks each sentence of the agent against the ERP before it is spoken (guard.py)."""

import asyncio
from contextlib import suppress
import re
from collections.abc import AsyncIterator

from .claims import claim_kinds
from .guard import Guard, Verdict, mode_from_env

__all__ = ["Guard", "Verdict", "check_text", "claim_kinds", "mode_from_env", "sentences", "until"]

_SENTENCE = re.compile(r"[^.!?…]+(?:[.!?…]+|$)")


def sentences(text: str) -> list[str]:
    """The sentences of an agent answer, as the Speaker cuts them (« 18.50 » stays whole: a boundary needs a space)."""
    out, cur = [], ""
    for part in re.split(r"(?<=[.!?…])\s+", text.strip()):
        cur = f"{cur} {part}".strip() if cur else part
        if cur:
            out.append(cur)
            cur = ""
    return [s for s in out if s.strip()]


async def check_text(guard: Guard, text: str) -> list[Verdict]:
    """Check every sentence of an already-written answer (simulator, reports): in order, like the Speaker would."""
    return [await guard.check(s) for s in sentences(text)]


async def until(events: AsyncIterator, stop: asyncio.Event) -> AsyncIterator:
    """Yield from `events` until `stop` is set (block mode: the guard caught a sentence, the agent stops talking).
    The pending step of `events` is cancelled, exactly like a barge-in: the CallSession keeps what was said so far."""
    it = aiter(events)
    while not stop.is_set():
        nxt = asyncio.ensure_future(anext(it))
        halt = asyncio.ensure_future(stop.wait())
        try:
            done, _ = await asyncio.wait({nxt, halt}, return_when=asyncio.FIRST_COMPLETED)
        except BaseException:
            nxt.cancel()
            halt.cancel()
            # Let the cancelled step finish before re-raising: otherwise the caller's aclose() finds the source generator
            # still running ("already running"), and a barge-in turns into an error instead of a clean interruption.
            with suppress(BaseException):
                await nxt
            raise
        halt.cancel()
        if nxt in done:
            try:
                yield nxt.result()
            except StopAsyncIteration:
                return
        else:
            nxt.cancel()
            try:
                await nxt
            except (asyncio.CancelledError, StopAsyncIteration):
                pass
            except Exception:
                pass
            return

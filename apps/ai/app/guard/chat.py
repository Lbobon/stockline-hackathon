"""The Garde-fou in the text chat (POST /chat): the answer streams as usual; each sentence with a claim is checked by Jev
against the tool results of the conversation, and a `guard` event follows it (the panel marks it ✓ / ✗ / ?).
Text cannot be unsaid, so the chat never blocks: it shows."""

import asyncio
import json
import re
from collections.abc import AsyncIterator

from app.schemas import ChatMessage, DoneEvent, ErrorEvent, GuardEvent, TextEvent, ToolCallEvent, ToolResultEvent

from .guard import Guard

SENTENCE = re.compile(r"^([\s\S]*?(?:[.!?…](?=\s)|\n))\s*")
MD = re.compile(r"(\*\*|__|`|^\s*(?:[-*•–]|\d+[.)])\s+|^\s*#+\s*)", re.M)


def _plain(s: str) -> str:
    return MD.sub("", s).strip()


def seed_from_history(guard: Guard, history: list[ChatMessage]) -> None:
    """Tool results already in the conversation (earlier turns) are evidence too."""
    args = {c.id: (c.name, c.arguments) for m in history if m.role == "assistant" for c in (m.tool_calls or [])}
    for m in history:
        if m.role == "tool" and m.tool_call_id in args:
            name, a = args[m.tool_call_id]
            try:
                result = json.loads(m.content)
            except ValueError:
                result = m.content
            guard.book.add_tool(name, a, result)
        elif m.role == "user":
            guard.client_said(m.content)


async def guarded(events: AsyncIterator, guard: Guard) -> AsyncIterator:
    if not guard.enabled:
        async for ev in events:
            yield ev
        return
    ready: asyncio.Queue = asyncio.Queue()
    pending: set[asyncio.Task] = set()
    calls: dict[str, tuple[str, dict]] = {}
    buf = ""

    def check(sentence: str) -> None:
        text = _plain(sentence)
        if len(text) < 4:
            return
        t = asyncio.create_task(guard.check(text))
        pending.add(t)
        t.add_done_callback(lambda done: (pending.discard(done), ready.put_nowait(done.result()) if not done.cancelled() and not done.exception() else None))

    def drain() -> list:
        out = []
        while not ready.empty():
            v = ready.get_nowait()
            if v.status != "none":
                out.append(GuardEvent(verdict=v.wire()))
        return out

    try:
        async for ev in events:
            match ev:
                case TextEvent(delta=delta):
                    buf += delta
                    while m := SENTENCE.match(buf):
                        check(m[1])
                        buf = buf[m.end() :]
                case ToolCallEvent(call=c):
                    calls[c.id] = (c.name, c.arguments)
                    buf and check(buf)
                    buf = ""
                case ToolResultEvent(call_id=cid, name=name, ok=ok, result=result):
                    guard.book.add_tool(name, calls.get(cid, (name, {}))[1], result, ok)
                case DoneEvent() | ErrorEvent():
                    if buf.strip():
                        check(buf)
                        buf = ""
                    if pending:
                        await asyncio.wait(pending, timeout=guard.timeout_ms / 1000 + 0.5)
            for g in drain():
                yield g
            yield ev
        for g in drain():
            yield g
    finally:
        for t in pending:
            t.cancel()

"""The demo phone line: a phone opens /phone in its browser and waits; the PC (Voix → Téléphone) makes it ring and
watches the call live.

WS /voice/phone   the phone's line: receives {"type":"ring", clientId, clientName} / {"type":"cancel"};
                  sends {"type":"answered"} / {"type":"declined"} / {"type":"hungup"} back to the watchers.
WS /voice/watch   the PC: sends {"type":"ring", clientId, clientName} / {"type":"cancel"}; receives every JSON message
                  the server sends to the phone's voice session (/voice?line=phone), plus {"type":"phone_line", ...}.

In memory, one process: good for a demo, not for a call centre.
"""

import asyncio
import json
from collections.abc import Awaitable, Callable

from fastapi import WebSocket, WebSocketDisconnect

phones: set[asyncio.Queue[str]] = set()
watchers: set[asyncio.Queue[str]] = set()


def _broadcast(pool: set[asyncio.Queue[str]], text: str) -> None:
    for q in list(pool):
        q.put_nowait(text)


def mirror(text: str) -> None:
    """A JSON message the phone's voice session just received: the watchers get a copy."""
    if watchers:
        _broadcast(watchers, text)


def line_status(event: str | None = None) -> str:
    return json.dumps({"type": "phone_line", "phones": len(phones), **({"event": event} if event else {})})


async def _serve(ws: WebSocket, pool: set[asyncio.Queue[str]], on_text: Callable[[dict], Awaitable[None] | None]) -> None:
    await ws.accept()
    q: asyncio.Queue[str] = asyncio.Queue()
    pool.add(q)

    async def writer() -> None:
        while True:
            await ws.send_text(await q.get())

    async def reader() -> None:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(msg, dict):
                r = on_text(msg)
                if asyncio.iscoroutine(r):
                    await r

    tasks = [asyncio.create_task(writer()), asyncio.create_task(reader())]
    try:
        if pool is phones:
            _broadcast(watchers, line_status("connected"))
        else:
            q.put_nowait(line_status())
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        for t in tasks:
            t.cancel()
        pool.discard(q)
        if pool is phones:
            _broadcast(watchers, line_status("disconnected"))


async def serve_phone(ws: WebSocket) -> None:
    def on_text(msg: dict) -> None:
        if msg.get("type") in ("answered", "declined", "hungup"):
            _broadcast(watchers, line_status(msg["type"]))

    await _serve(ws, phones, on_text)


async def serve_watch(ws: WebSocket) -> None:
    def on_text(msg: dict) -> None:
        if msg.get("type") == "ring" and msg.get("clientId"):
            _broadcast(phones, json.dumps({"type": "ring", "clientId": msg["clientId"], "clientName": msg.get("clientName") or ""}))
            _broadcast(watchers, line_status("ringing" if phones else "no_phone"))
        elif msg.get("type") == "cancel":
            _broadcast(phones, json.dumps({"type": "cancel"}))
            _broadcast(watchers, line_status("cancelled"))

    await _serve(ws, watchers, on_text)

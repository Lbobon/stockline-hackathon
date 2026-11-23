"""/campaigns: parallel outbound call campaigns (radar de demande), with a live SSE stream."""

import asyncio
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.campaign import engine

router = APIRouter()


class CampaignIn(BaseModel):
    date: str | None = None
    clientIds: list[str] | None = None
    concurrency: int = 8


class DeliveriesIn(BaseModel):
    speed: float = 10.0  # route minutes per second


def _get(cid: str) -> engine.Campaign:
    c = engine.CAMPAIGNS.get(cid)
    if not c:
        raise HTTPException(404, "campaign not found")
    return c


@router.post("/campaigns")
async def start_campaign(body: CampaignIn) -> dict:
    c = await engine.start(body.date, body.clientIds, body.concurrency)
    return {"id": c.id, "date": c.date, "calls": len(c.calls)}


@router.get("/campaigns/{cid}")
async def get_campaign(cid: str) -> dict:
    return _get(cid).snapshot()


@router.get("/campaigns/{cid}/stream")
async def stream_campaign(cid: str) -> StreamingResponse:
    """Server-Sent Events: every event since the start (replayed), then live ones."""
    c = _get(cid)
    q: asyncio.Queue = asyncio.Queue()
    past = list(c.log)
    c.subscribers.append(q)

    async def gen():
        try:
            for ev in past:
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), 15)
                except TimeoutError:
                    yield ": ping\n\n"  # keep proxies from closing an idle stream
                    continue
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
        finally:
            c.subscribers.remove(q)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/campaigns/{cid}/deliveries")
async def replay_deliveries(cid: str, body: DeliveriesIn | None = None) -> dict:
    c = _get(cid)
    asyncio.create_task(engine.replay_deliveries(c, (body or DeliveriesIn()).speed))
    return {"ok": True}

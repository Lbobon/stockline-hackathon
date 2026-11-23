"""GET /calls, GET /calls/{id}: the call reports written by the Rapporteur (app/orchestra/reporter.py)."""

from fastapi import APIRouter, HTTPException

from app.orchestra import store

router = APIRouter()


@router.get("/calls")
async def list_calls(limit: int = 100) -> list[dict]:
    return store.list_calls(limit)


@router.get("/calls/{call_id}")
async def get_call(call_id: str) -> dict:
    report = store.load(call_id)
    if not report:
        raise HTTPException(404, "call not found")
    return report

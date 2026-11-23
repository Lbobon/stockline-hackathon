"""/api/shortages: shortage triage (out-of-stock products, affected orders, call or auto-substitute)."""

from fastapi import APIRouter, Query

from app.dependencies import CtxDep, data, iso_date_query
from app.schemas.shortages import ShortageTriage, SubstituteInput
from app.schemas.views import OrderDetail
from app.services import shortages

router = APIRouter(prefix="/api/shortages", tags=["shortages"])


@router.get("/today", response_model=ShortageTriage)
async def shortage_triage(date: str | None = Query(None, description="YYYY-MM-DD, default business today")):
    """Open orders (delivery ≥ date) holding an out-of-stock product. Today's truck first, then calls."""
    return shortages.triage(iso_date_query(date))


@router.post("/{order_id}/substitute", response_model=OrderDetail)
async def substitute(order_id: str, ctx: CtxDep, body: SubstituteInput | None = None):
    """Swap every short line for its usual substitute (draft/confirmed only) and log a client event."""
    return shortages.substitute(ctx, order_id, data(body).get("note"))

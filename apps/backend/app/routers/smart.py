"""/api/gates (may the agent confirm this order?) and /api/retention (gesture when a substitute is refused)."""

from fastapi import APIRouter, Query

from app.dependencies import CtxDep, data, iso_date_query
from app.schemas.smart import FinalizeInput, GateFinalized, GateResult, GateReviewItem, RetentionOffer
from app.services import gates, retention

gates_router = APIRouter(prefix="/api/gates", tags=["smart"])
retention_router = APIRouter(prefix="/api/retention", tags=["smart"])


@gates_router.get("/review", response_model=list[GateReviewItem])
async def review(date: str | None = Query(None, description="YYYY-MM-DD, default business today")):
    """Drafts (delivery ≥ date) a human must check, with the reasons. For the morning page."""
    return gates.review(iso_date_query(date))


@gates_router.get("/orders/{order_id}", response_model=GateResult)
async def check(order_id: str, recap_confirmed: bool = Query(True, alias="recapConfirmed")):
    return gates.check(order_id, recap_confirmed)


@gates_router.post("/orders/{order_id}/finalize", response_model=GateFinalized)
async def finalize(order_id: str, ctx: CtxDep, body: FinalizeInput | None = None):
    """Confirms the order if no gate fires; else keeps it a draft with an "À vérifier : …" note."""
    return gates.finalize(ctx, order_id, data(body).get("recapConfirmed", True))


@retention_router.get("/{client_id}", response_model=RetentionOffer)
async def offer(client_id: str, basis_ht: float = Query(..., alias="basisHt", description="€ HT of the lost line"),
                cost_ht: float | None = Query(None, alias="costHt", description="purchase cost of that line, to flag below-cost")):  # fmt: skip
    return retention.offer(client_id, basis_ht, cost_ht)

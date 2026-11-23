"""/api/routing: delivery optimization on real geography, cost to serve, truck breakdown re-plan."""

from fastapi import APIRouter, Query

from app.dependencies import CtxDep, iso_date_query
from app.schemas.base import Input
from app.services import optimize

router = APIRouter(prefix="/api/routing", tags=["dispatch"])

# shortcut: responses are plain dicts (no response_model); add Pydantic schemas if `make types` must cover them.


class ApplyInput(Input):
    date: str | None = None


class BreakdownInput(Input):
    run_id: str
    after_stop: int = 0  # stops already delivered when the truck stopped (0 = just left the depot)


@router.get("/optimize")
async def optimize_plan(date: str | None = Query(None, description="YYYY-MM-DD, default business today")):
    """Current vs optimized runs (km, hours, €, ETAs, load), stops that change truck, cost to serve each stop."""
    return optimize.plan(iso_date_query(date))


@router.post("/apply")
async def apply_plan(ctx: CtxDep, body: ApplyInput | None = None):
    """Write the optimized stop order, ETAs and truck changes to the route runs."""
    return optimize.apply(ctx, iso_date_query(body.date if body else None))


@router.post("/breakdown")
async def breakdown(body: BreakdownInput):
    """Preview: a truck stops after N stops. Who rescues each remaining stop, new ETAs, dock pickup / next delivery,
    and the rescuers' own clients who will be late. Writes nothing."""
    return optimize.breakdown(body.run_id, body.after_stop)

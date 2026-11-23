"""/api/radar: tomorrow's orders predicted per client (rhythm + usual basket + special events)."""

from fastapi import APIRouter, Query

from app.dependencies import iso_date_query
from app.services import radar

router = APIRouter(prefix="/api/radar", tags=["radar"])

# shortcut: plain dict responses (no response_model); the TS types live with the /radar page.


@router.get("/events")
async def list_events(date_from: str | None = Query(None, alias="from"), date_to: str | None = Query(None, alias="to")):
    """Special events (match, weather, weddings, local fair) whose forecast window overlaps [from, to]."""
    return radar.events(iso_date_query(date_from), iso_date_query(date_to))


@router.get("/forecast")
async def forecast(date: str | None = Query(None, description="YYYY-MM-DD, default business tomorrow")):
    """Every client served that day: ordered, upsell (ordered, but an event calls for more) or predicted, with lines,
    totals, uplift from events and a one-line reason."""
    return radar.forecast(iso_date_query(date))

"""/api/radar: tomorrow's orders predicted per client (rhythm + usual basket + special events)."""

from fastapi import APIRouter, Query

from app.dependencies import iso_date_query
from app.services import radar, radar_backtest

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


@router.get("/backtest")
async def backtest(weeks: int = Query(8, ge=1, le=26), weekday: str = Query("wed", description="mon … sun")):
    """The forecast replayed on the last `weeks` weekdays, as of the day before each: precision / recall on who orders,
    quantity error, € forecast vs € delivered, and a one-line pitch."""
    return radar_backtest.backtest(weeks, weekday)

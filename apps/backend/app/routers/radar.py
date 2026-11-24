"""/api/radar: tomorrow's orders predicted per client (baseline + uplift measured per event type), the events calendar, the backtest."""

from fastapi import APIRouter, Query

from app.core.activity import today
from app.dependencies import CtxDep, iso_date_query
from app.schemas.inputs import CalendarEventCreate
from app.services import calendar, demand, radar, radar_backtest

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


@router.get("/clients/{client_id}/suggestion")
async def client_suggestion(client_id: str, date: str | None = Query(None, description="YYYY-MM-DD, default their next delivery")):
    """One client: what the radar expects them to order (status predicted / upsell / ordered / unlikely), the lines with
    their usual quantity (baseQty) and the event that raises it, and why. The agent's radar_suggestion tool."""
    return radar.suggestion(client_id, iso_date_query(date))


@router.get("/backtest")
async def backtest(weeks: int = Query(8, ge=1, le=26), weekday: str = Query("wed", description="mon … sun")):
    """The forecast replayed on the last `weeks` weekdays, as of the day before each: precision / recall on who orders,
    quantity error, € forecast vs € delivered, and a one-line pitch."""
    return radar_backtest.backtest(weeks, weekday)


@router.get("/backtest/events")
async def backtest_events(months: int = Query(12, ge=1, le=36)):
    """Both models (radar vs plain average) on the event days of the last `months` months, per event type."""
    return radar_backtest.backtest_events(months)


@router.get("/uplift")
async def uplift(as_of: str | None = Query(None, alias="asOf", description="YYYY-MM-DD, default business today")):
    """Uplift measured per event type × client segment from the events finished before `asOf`: factor on the segment's
    € (demand), on a line's quantity (line) and per product, with the sample (days, occurrences)."""
    return demand.uplift(iso_date_query(as_of) or today())


@router.get("/calendar")
async def list_calendar(date_from: str | None = Query(None, alias="from"), date_to: str | None = Query(None, alias="to"),
                        type: str | None = Query(None, description="ramadan, aid_kebir, school_holidays, om_home…")):  # fmt: skip
    """The events calendar stored in the ERP, every event whose window overlaps [from, to]."""
    return calendar.list_calendar(iso_date_query(date_from), iso_date_query(date_to), type)


@router.post("/calendar", status_code=201)
async def add_calendar_event(body: CalendarEventCreate, ctx: CtxDep):
    """Add an event (a new OM home match, next year's ramadan…). Its type's past occurrences give its uplift."""
    return calendar.add_event(ctx, body.data())


@router.delete("/calendar/{id}", status_code=204)
async def delete_calendar_event(id: str, ctx: CtxDep):
    calendar.delete_event(ctx, id)

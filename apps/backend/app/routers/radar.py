"""/api/radar: tomorrow's orders predicted per client (baseline + uplift measured per event type), the events calendar, the backtest."""

from fastapi import APIRouter, Query

from app.core.activity import today
from app.dependencies import CtxDep, iso_date_query
from app.schemas.inputs import CalendarEventCreate
from app.services import calendar, demand, demand_daily, demand_weekly, feeds, radar, radar_backtest, supply

router = APIRouter(prefix="/api/radar", tags=["radar"])

# shortcut: plain dict responses (no response_model); the TS types live with the /radar page.


@router.get("/events")
async def list_events(date_from: str | None = Query(None, alias="from"), date_to: str | None = Query(None, alias="to")):
    """Special events (match, weather, weddings, local fair) whose forecast window overlaps [from, to]."""
    await feeds.ensure()
    return radar.events(iso_date_query(date_from), iso_date_query(date_to))


@router.get("/forecast")
async def forecast(date: str | None = Query(None, description="YYYY-MM-DD, default business tomorrow")):
    """Every client served that day: ordered, upsell (ordered, but an event calls for more) or predicted, with lines,
    totals, uplift from events and a one-line reason."""
    await feeds.ensure()
    return radar.forecast(iso_date_query(date))


@router.get("/supply")
async def supply_needs(date: str | None = Query(None, description="YYYY-MM-DD, first day, default business tomorrow"),
                       days: int = Query(7, ge=1, le=14)):
    """What the depot will run short of over the next `days` days (the forecast summed vs stock + incoming), and the
    purchase to make from each product's main supplier, with the events behind it. The supplier campaign calls these."""
    await feeds.ensure()
    return supply.supply(iso_date_query(date), days)


@router.get("/supply/ahead")
async def supply_ahead(weeks: int = Query(12, ge=2, le=12)):
    """The demand spikes of the next `weeks` weeks per product (week-level model, services/demand_weekly.py: season, last
    year, trend, climate, holidays and the event calendar), and the call to book each supplier ahead of its next spike."""
    await feeds.ensure()
    return supply.ahead(weeks)


@router.get("/supply/ahead/backtest")
async def supply_ahead_backtest(weeks: int = Query(52, ge=8, le=52)):
    """The week-level model replayed on the last `weeks` weeks (walk-forward, each forecast made with only the weeks before
    it): € error per horizon, spike recall / precision, vs « comme les dernières semaines » and « comme l'an dernier »,
    and the same model without the event calendar (broad patterns only)."""
    return demand_weekly.backtest(weeks)


@router.get("/clients/{client_id}/suggestion")
async def client_suggestion(client_id: str, date: str | None = Query(None, description="YYYY-MM-DD, default their next delivery")):
    """One client: what the radar expects them to order (status predicted / upsell / ordered / unlikely), the lines with
    their usual quantity (baseQty) and the event that raises it, and why. The agent's radar_suggestion tool."""
    await feeds.ensure()
    return radar.suggestion(client_id, iso_date_query(date))


@router.get("/backtest")
async def backtest(weeks: int = Query(8, ge=1, le=26), weekday: str = Query("wed", description="mon … sun")):
    """The forecast replayed on the last `weeks` weekdays, as of the day before each: precision / recall on who orders,
    quantity error, € forecast vs € delivered, and a one-line pitch."""
    await feeds.ensure()
    return radar_backtest.backtest(weeks, weekday)


@router.get("/backtest/events")
async def backtest_events(months: int = Query(12, ge=1, le=36)):
    """Both models (radar vs plain average) on the event days of the last `months` months, per event type."""
    await feeds.ensure()
    return radar_backtest.backtest_events(months)


@router.get("/uplift")
async def uplift(as_of: str | None = Query(None, alias="asOf", description="YYYY-MM-DD, default business today")):
    """Uplift measured per event type × client segment from the events finished before `asOf`: factor on the segment's
    € (demand), on a line's quantity (line) and per product, with the sample (days, occurrences)."""
    await feeds.ensure()
    return demand.uplift(iso_date_query(as_of) or today())


@router.get("/feeds")
async def feed_status():
    """The live sources behind the calendar (Open-Meteo weather, OM home matches, ICS agendas such as a concert hall's):
    what each holds and when it was last fetched. Fetches what is stale first."""
    await feeds.ensure()
    return feeds.status()


@router.get("/calendar")
async def list_calendar(date_from: str | None = Query(None, alias="from"), date_to: str | None = Query(None, alias="to"),
                        type: str | None = Query(None, description="ramadan, aid_kebir, school_holidays, om_home…")):  # fmt: skip
    """The events calendar stored in the ERP, every event whose window overlaps [from, to]."""
    await feeds.ensure()
    return calendar.list_calendar(iso_date_query(date_from), iso_date_query(date_to), type)


@router.post("/calendar", status_code=201)
async def add_calendar_event(body: CalendarEventCreate, ctx: CtxDep):
    """Add an event (a new OM home match, next year's ramadan…). Its type's past occurrences give its uplift."""
    return calendar.add_event(ctx, body.data())


@router.delete("/calendar/{id}", status_code=204)
async def delete_calendar_event(id: str, ctx: CtxDep):
    calendar.delete_event(ctx, id)


@router.get("/outlook")
async def outlook(days: int = Query(14, ge=1, le=30)):
    """The next `days` delivery days, from the day-level model (services/demand_daily.py): € HT expected per day and
    per segment against a normal week, the events covering each day and those coming up."""
    await feeds.ensure()
    return demand_daily.outlook(days)


@router.get("/outlook/backtest")
async def outlook_backtest(months: int = Query(12, ge=1, le=24)):
    """The day-level model replayed month by month (walk-forward) against three simple forecasts: revenue error per
    day on all days, normal days and each event type."""
    return demand_daily.backtest(months)

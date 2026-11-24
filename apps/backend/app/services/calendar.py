"""The events calendar (crud.calendar): matches, ramadan and the aïds, school holidays, christmas, local fairs, weather.
The radar reads it (services/demand.py); the team adds to it through /api/radar/calendar."""

from __future__ import annotations

from app import crud
from app.core.activity import Ctx, log_event
from app.core.db import Doc, transactional
from app.core.errors import bad_request
from app.services import demand


def list_calendar(date_from: str | None, date_to: str | None, type: str | None = None) -> list[Doc]:
    rows = demand.in_window(date_from or "0000-01-01", date_to or "9999-12-31")
    return [e for e in rows if not type or e["type"] == type]


@transactional
def add_event(ctx: Ctx, body: Doc) -> Doc:
    if body["from"] > body["to"]:
        raise bad_request("BAD_WINDOW", "La fin de l'événement précède son début")
    for b in [*(body.get("estimate") or {}).get("boosts", []), *(body.get("estimate") or {}).get("special", [])]:
        crud.products.require(b["productId"])
    base = f"{body['type']}-{body['from']}"
    eid, n = base, 1
    while eid in crud.calendar.rows:
        n += 1
        eid = f"{base}-{n}"
    doc = {"id": eid, "type": body["type"], "kind": body["kind"], "title": body["title"], "date": body["date"],
           "time": body.get("time"), "affects": {"from": body["from"], "to": body["to"]}, "segments": body.get("segments") or [],
           "pitch": body["pitch"], "source": body.get("source") or "saisi dans l'ERP",
           **({"baselineExcluded": True} if body.get("baselineExcluded") else {}),
           **({"estimate": body["estimate"]} if body.get("estimate") else {})}  # fmt: skip
    crud.calendar.put(doc)
    log_event(ctx, "calendar.created", "calendar", eid, f"Événement ajouté au calendrier : « {doc['title']} » ({doc['affects']['from']} → {doc['affects']['to']})")
    return doc


@transactional
def delete_event(ctx: Ctx, id: str) -> None:
    e = crud.calendar.require(id)
    crud.calendar.delete(id)
    log_event(ctx, "calendar.deleted", "calendar", id, f"Événement retiré du calendrier : « {e['title']} »")


"""Shortage triage contract (GET /api/shortages/today)."""

from typing import Literal

from app.schemas.base import Input, Schema


class ShortProduct(Schema):
    product_id: str
    name: str
    unit: str
    price: float
    available: float


class ShortProductWithSub(ShortProduct):
    substitute: ShortProduct | None = None


class ShortLine(ShortProduct):
    line_id: str
    qty: float
    missing_qty: float
    substitute: ShortProduct | None = None  # absent when no usual substitute has enough stock


class ShortageOrder(Schema):
    order_id: str
    order_number: str
    status: str
    delivery_date: str
    leaves_today: bool
    client_id: str
    client_name: str
    contact_name: str | None = None
    phone: str | None = None
    preference: Literal["refuses", "accepts", "unknown"]
    decision: Literal["call", "auto_substitute"]
    reason: str  # plain French, shown to Leïla
    lines: list[ShortLine]


class ShortageCounts(Schema):
    orders: int
    calls: int
    auto: int


class ShortageTriage(Schema):
    date: str
    products: list[ShortProductWithSub]
    orders: list[ShortageOrder]
    counts: ShortageCounts


class SubstituteInput(Input):
    note: str | None = None  # outcome of the call, stored on the client event

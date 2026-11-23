"""Order gates (GET/POST /api/gates/...) and retention offers (GET /api/retention/...) contract."""

from typing import Any, Literal

from app.schemas.base import Input, Schema
from app.schemas.views import OrderDetail


class GateReason(Schema):
    code: Literal["client_blocked", "credit_limit", "too_late", "recap_not_confirmed", "unusual_qty", "new_product", "special_cut_late"]
    message: str  # plain French, shown as is
    product_id: str | None = None


class GateResult(Schema):
    order_id: str
    decision: Literal["confirm", "draft"]
    reasons: list[GateReason]


class GateFinalized(GateResult):
    order: OrderDetail


class GateReviewItem(GateResult):
    order_number: str
    client_id: str
    client_name: str
    delivery_date: str
    total_ht: float
    created_by: str | None = None


class FinalizeInput(Input):
    recap_confirmed: bool = True


class RetentionOffer(Schema):
    client_id: str
    client_name: str | None = None
    kind: Literal["money", "service"]
    status: Literal["granted", "needs_approval", "service_only"]
    pct: float
    amount_ht: float
    offers: list[str]  # ladder, in the order the agent proposes them
    explanation: str  # plain French, why this gesture
    factors: dict[str, Any]

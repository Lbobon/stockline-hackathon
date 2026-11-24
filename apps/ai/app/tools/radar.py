"""radar_suggestion: what the demand radar expects a client to order, so the agent can bring it up during an inbound
call (« avec le ramadan, vos bouchers prennent d'habitude deux fois plus de minerai : je vous en mets 60 kilos ? »)."""

from pydantic import Field

from .erp import enc
from .registry import ToolContext, tool


@tool
async def radar_suggestion(
    ctx: ToolContext,
    client_id: str = Field(description="ex. cli_0012"),
    date: str | None = Field(None, description="AAAA-MM-JJ ; défaut : sa prochaine livraison"),
) -> dict:
    """
    Radar de demande pour un client : ce qu'il commande d'habitude ce jour-là (baseQty, médiane de ses 8 derniers mêmes jours)
    et ce que l'événement qui approche y ajoute (qty, eventId), mesuré sur l'historique (ramadan, aïd, match de l'OM,
    météo…) ou estimé (mariages). À appeler quand un client passe commande ou demande « l'habituel » : propose UNE fois, en une phrase,
    le produit le plus relevé par l'événement, avec la raison. Demand forecast for one client.
    """
    r = await ctx.erp.get(f"/api/radar/clients/{enc(client_id)}/suggestion", {"date": date})
    return {
        **{k: r.get(k) for k in ("date", "label", "status", "probability", "reason", "existingOrderId", "expectedHt")},
        "lines": [{k: l.get(k) for k in ("productId", "productName", "unit", "qty", "baseQty", "eventId", "note") if l.get(k) is not None} for l in r["lines"]],
        "events": r["events"],
    }

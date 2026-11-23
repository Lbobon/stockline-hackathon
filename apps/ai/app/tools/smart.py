"""Smart tools: confirm-or-draft gates, retention offers, shortage triage. The rules live in the ERP API."""

from pydantic import Field

from .erp import enc, pick
from .orders import summarize_order
from .registry import ToolContext, tool


@tool
async def finalize_order(
    ctx: ToolContext,
    order_id: str,
    recap_confirmed: bool = Field(description="true seulement si le client a clairement dit oui au récapitulatif"),
) -> dict:
    """
    Termine une commande après le récapitulatif : l'ERP la confirme si tout va bien, sinon la laisse en brouillon
    pour Leïla avec les raisons (compte bloqué, encours, trop tard, quantité inhabituelle, nouveau produit…).
    Use this instead of confirm_order. If decision is "draft", tell the caller kindly that Leïla will validate at 5am,
    never read out the internal reason for a blocked account or credit limit.
    """
    r = await ctx.erp.post(f"/api/gates/orders/{enc(order_id)}/finalize", {"recapConfirmed": recap_confirmed})
    return {"decision": r["decision"], "reasons": [x["message"] for x in r["reasons"]], "order": summarize_order(r["order"])}


@tool
async def retention_offer(
    ctx: ToolContext,
    client_id: str,
    basis_ht: float = Field(description="valeur HT de la ligne perdue (quantité × prix)"),
    cost_ht: float | None = Field(None, description="coût d'achat de cette ligne, si connu"),
) -> dict:
    """
    Geste commercial quand le client refuse le remplaçant d'un produit en rupture. Propose les offres dans l'ordre
    (livraison demain, quantité partielle, puis la remise). status "needs_approval" : dire « je le fais valider
    par Rachid », ne rien promettre. status "service_only" : aucun geste financier.
    """
    r = await ctx.erp.get(f"/api/retention/{enc(client_id)}", {"basisHt": basis_ht, "costHt": cost_ht})
    return pick(r, "status", "pct", "amountHt", "offers", "explanation")


@tool
async def shortage_triage(ctx: ToolContext) -> dict:
    """Commandes ouvertes touchées par une rupture, avec la décision par client (appeler ou substituer) et pourquoi."""
    r = await ctx.erp.get("/api/shortages/today")
    return {
        "counts": r["counts"],
        "orders": [
            {**pick(o, "orderId", "clientId", "clientName", "contactName", "deliveryDate", "decision", "reason"),
             "lines": [{**pick(l, "lineId", "name", "missingQty", "unit"), "substitute": (l.get("substitute") or {}).get("name")} for l in o["lines"]]}  # fmt: skip
            for o in r["orders"]
        ],
    }

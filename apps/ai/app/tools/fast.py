"""
Composite tools: one model step instead of two or three (GUIDE-VOIX.md 4.6, TECH.md 5.4).

Each model step re-reads the whole prompt and costs ~0.6 s (Haiku) to ~1.5 s (Sonnet) before any word is said,
while an ERP call costs 10 to 50 ms. So the slow part of a turn is the number of model steps, not the ERP:
these tools run the usual chains (search → get_client → get_context, search → stock + price) inside one call,
the ERP requests in parallel.
"""

import asyncio

from pydantic import Field

from .catalog import get_client, get_price, get_product_stock
from .context import get_context
from .erp import ErpError
from .registry import ToolContext, tool


async def _search(ctx: ToolContext, query: str, kind: str) -> list[dict]:
    results = await ctx.erp.get("/api/search", {"q": query, "types": kind})
    return [{k: r[k] for k in ("id", "label", "sublabel") if k in r} for r in results[:5]]


async def _safe(coro):
    """A tool result or {"error": …}: one failing ERP call doesn't sink the others."""
    try:
        return await coro
    except ErpError as e:
        return {"error": str(e)}


@tool
async def find_client(
    ctx: ToolContext,
    query: str = Field(description="Le nom du client tel qu'il est dit, ex. « Couscous Brigitte boulevard Gambetta », ou un id cli_…"),
) -> dict:
    """
    Identifie un client et charge TOUT ce qu'il faut pour lui répondre, en une seule étape : la fiche (get_client : statut, encours,
    préférences, « l'habituel », commandes récentes) et ses fiches de contexte (get_context). Si plusieurs clients correspondent,
    renvoie les candidats (« candidates ») sans fiche : demande lequel.
    One step: find the client, then return its card + context; or the candidates if ambiguous.
    """
    if query.startswith("cli_"):
        candidates, client_id = [{"id": query}], query
    else:
        candidates = await _search(ctx, query, "client")
        if not candidates:
            return {"candidates": [], "note": "aucun client trouvé"}
        # One clear match: the first result's name is contained in what was said, or it's the only result.
        said = query.lower()
        exact = [c for c in candidates if (c.get("label") or "").lower() in said]
        if len(candidates) > 1 and len(exact) != 1:
            return {"candidates": candidates}
        client_id = (exact or candidates)[0]["id"]
    card, context = await asyncio.gather(
        _safe(get_client.fn(ctx, client_id=client_id)),
        _safe(get_context.fn(ctx, entity_type="client", id=client_id)),
    )
    return {"client": card, "context": context, **({"otherCandidates": candidates[1:]} if len(candidates) > 1 else {})}


@tool
async def check_product(
    ctx: ToolContext,
    query: str = Field(description="Le produit tel qu'il est dit (surnom, faute), ex. « broche de 20 », ou un id prd_…"),
    client_id: str | None = Field(None, description="Si on connaît le client (cli_…) : ajoute son prix (get_price)"),
    qty: float | None = Field(None, description="Quantité envisagée, pour le prix au volume et le stock"),
) -> dict:
    """
    Trouve un produit et renvoie en une seule étape son stock (get_product_stock : disponible, arrivages, prochaine DLC) et, si le
    client est connu, son prix pour ce client (get_price). Plusieurs produits plausibles : les candidats sont dans « candidates »,
    avec le stock et le prix du premier.
    One step: find a product, return its stock and the client's price.
    """
    if query.startswith("prd_"):
        candidates, product_id = [], query
    else:
        candidates = await _search(ctx, query, "product")
        if not candidates:
            return {"candidates": [], "note": "aucun produit trouvé"}
        product_id = candidates[0]["id"]
    stock_call = _safe(get_product_stock.fn(ctx, product_id=product_id))
    price_call = _safe(get_price.fn(ctx, client_id=client_id, product_id=product_id, qty=qty)) if client_id else None
    stock, price = await asyncio.gather(stock_call, price_call) if price_call else (await stock_call, None)
    return {
        "product": stock,
        **({"clientPrice": price} if price is not None else {}),
        **({"candidates": candidates} if len(candidates) > 1 else {}),
    }

"""Buy from a supplier: write a purchase order and send it. Used by the supplier calls of the radar (campaign/supply.py).
Business rules (supplier price, minimum, statuses) live in the ERP API (services/purchasing.py)."""

from pydantic import Field

from .erp import enc, pick
from .history import without_none
from .registry import ToolArgs, ToolContext, tool


class PurchaseLine(ToolArgs):
    product_id: str
    qty: float = Field(description="Quantité convenue avec le fournisseur, dans l'unité du produit (kg, pièce, colis…)")
    unit_price: float | None = Field(None, description="Prix d'achat négocié au kilo / à l'unité ; vide = prix fournisseur de l'ERP")

    def to_erp(self) -> dict:
        return self.model_dump(by_alias=True, exclude_none=True)


def summarize_po(po: dict) -> dict:
    return {
        **pick(po, "id", "number", "status", "expectedDate", "totalHt"),
        "supplier": (po.get("supplier") or {}).get("name") or po.get("supplierName"),
        "lines": [pick(l, "productId", "productName", "qty", "unit", "unitPrice") for l in po.get("lines") or []],
        "warnings": po.get("warnings") or [],
    }


@tool
async def create_purchase_order(
    ctx: ToolContext,
    supplier_id: str,
    lines: list[PurchaseLine],
    expected_date: str | None = Field(None, description="YYYY-MM-DD : livraison au dépôt convenue ; vide = délai habituel du fournisseur"),
    note: str | None = None,
) -> dict:
    """
    Crée une commande fournisseur (brouillon) avec les quantités convenues au téléphone. Puis send_purchase_order pour l'envoyer.
    Create a purchase order (draft) for a supplier with the agreed lines.
    """
    body = {"supplierId": supplier_id, "expectedDate": expected_date, "lines": [l.to_erp() for l in lines], "note": note}
    return summarize_po(await ctx.erp.post("/api/purchase-orders", without_none(body)))


@tool
async def send_purchase_order(ctx: ToolContext, purchase_order_id: str) -> dict:
    """
    Envoie la commande fournisseur (brouillon → envoyée) : elle compte alors comme marchandise en route dans le stock.
    Send the purchase order to the supplier.
    """
    return summarize_po(await ctx.erp.post(f"/api/purchase-orders/{enc(purchase_order_id)}/send", {}))

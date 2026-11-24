"""
The tools the model can call, grouped by domain. Each one wraps ERP API endpoints.

    registry.py  @tool decorator, Tool, ToolContext (start here to add a tool)
    erp.py       the HTTP client for the ERP API
    catalog.py   search, get_client, get_product_stock, find_alternatives, get_price
    context.py   search_context, get_context, save_context
    history.py   get_client_history, log_client_event, get_supplier_history, log_supplier_event
    orders.py    create_draft_order, add_order_line, confirm_order, get_order
    fast.py      find_client, check_product: composite lookups, one model step instead of two or three
    smart.py     finalize_order, retention_offer, shortage_triage, apply_substitution
    radar.py     radar_suggestion: the demand radar's forecast for one client (usual order + event uplift)
    purchasing.py create_purchase_order, send_purchase_order: only for the radar's supplier calls (campaign/supply.py),
                 kept out of all_tools() so the client-facing agents never buy

A new file needs one more line: import it below so its @tool functions get registered.
"""

from . import catalog, context, fast, history, orders, purchasing, radar, smart  # noqa: F401  (importing registers the tools)
from .erp import ErpClient
from .registry import TOOLS, Tool, ToolContext, tool


BUYER_TOOLS = ("create_purchase_order", "send_purchase_order")


def all_tools() -> list[Tool]:
    return [t for name, t in TOOLS.items() if name not in BUYER_TOOLS]


def buyer_tools() -> list[Tool]:
    """The supplier calls: write and send the purchase order."""
    return [TOOLS[name] for name in BUYER_TOOLS]


__all__ = ["TOOLS", "ErpClient", "Tool", "ToolContext", "all_tools", "buyer_tools", "tool"]

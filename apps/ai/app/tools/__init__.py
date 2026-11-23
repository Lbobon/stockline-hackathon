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

A new file needs one more line: import it below so its @tool functions get registered.
"""

from . import catalog, context, fast, history, orders, smart  # noqa: F401  (importing registers the tools)
from .erp import ErpClient
from .registry import TOOLS, Tool, ToolContext, tool


def all_tools() -> list[Tool]:
    return list(TOOLS.values())


__all__ = ["TOOLS", "ErpClient", "Tool", "ToolContext", "all_tools", "tool"]

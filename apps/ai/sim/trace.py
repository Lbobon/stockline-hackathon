"""What happened during one simulated call: the turns, the agent's tool calls and results, timings and token usage."""

from dataclasses import dataclass, field
from typing import Any

ORDER_TOOLS = {"create_draft_order", "add_order_line", "update_order_line", "remove_order_line", "update_order", "confirm_order", "get_order"}
WRITE_TOOLS = ORDER_TOOLS - {"get_order"} | {"save_context", "log_client_event"}


@dataclass
class Turn:
    caller: str
    agent: str = ""
    tools: list[dict[str, Any]] = field(default_factory=list)  # {name, args, ok, ms, result, agent?}
    first_text_ms: int | None = None  # caller's text in → first agent word (what TTS would start on)
    first_sound_ms: int | None = None  # end of caller speech → first thing heard (filler / fast-path answer / first word)
    path: str = "llm"  # llm | fast (orchestra: answered by the Réflexe)
    prefetched: list[str] = field(default_factory=list)  # facts the Éclaireur had ready when the turn started
    total_ms: int | None = None  # caller's text in → agent turn finished
    error: str | None = None


@dataclass
class Trace:
    turns: list[Turn] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)  # agent only (what a real call costs)
    sim_usage: dict[str, int] = field(default_factory=dict)  # caller + judge models
    ended_by: str = ""  # "caller" | "max_turns" | "error"
    cost_by_agent: dict[str, float] = field(default_factory=dict)  # orchestra: voice / scout / fast / scribe / warmup / reporter
    report_id: str | None = None

    def calls(self) -> list[dict[str, Any]]:
        return [c for t in self.turns for c in t.tools]

    def called(self, name: str) -> bool:
        # check_product with a clientId resolves the client's price exactly like get_price (tools/fast.py)
        if name == "get_price" and any(c["name"] == "check_product" and c["args"].get("clientId") for c in self.calls()):
            return True
        return any(c["name"] == name for c in self.calls())

    def client_ids_looked_up(self) -> set[str]:
        ids = {c["args"].get("clientId") for c in self.calls()}
        ids |= {c["args"].get("id") for c in self.calls() if c["name"] == "get_context"}
        # find_client takes the spoken name: the id it loaded is in its result
        ids |= {((c.get("result") or {}).get("client") or {}).get("id") for c in self.calls() if c["name"] == "find_client" and isinstance(c.get("result"), dict)}
        ids |= {c["args"].get("query") for c in self.calls() if c["name"] == "find_client"}
        return {i for i in ids if isinstance(i, str) and i.startswith("cli_")}

    def write_client_ids(self) -> set[str]:
        """Clients the agent wrote something for (orders, events, context)."""
        ids = {c["args"].get("clientId") for c in self.calls() if c["name"] in WRITE_TOOLS and c["args"].get("clientId")}
        ids |= {c["args"].get("entityId") for c in self.calls() if c["name"] == "save_context" and str(c["args"].get("entityId", "")).startswith("cli_")}
        return {i for i in ids if i}

    def orders(self, written_only: bool = False) -> dict[str, dict[str, Any]]:
        """Last known state of every order the agent touched (from the order tools' summaries).
        written_only: skip orders the agent only read (get_order), e.g. the client's existing order for today."""
        out: dict[str, dict[str, Any]] = {}
        for c in self.calls():
            if written_only and c["name"] == "get_order":
                continue
            r = c.get("result")
            if c["name"] == "finalize_order" and c.get("ok") and isinstance(r, dict) and isinstance(r.get("order"), dict):
                r = r["order"]  # finalize_order (main's smart tools) returns {decision, reasons, order}
            elif not (c["name"] in ORDER_TOOLS and c.get("ok")):
                continue
            if isinstance(r, dict) and r.get("id"):
                out[r["id"]] = r
        return out

    def qty(self, product_id: str, order_id: str | None = None) -> float:
        """Total quantity of a product across the orders the agent touched (or one order)."""
        orders = self.orders()
        picked = [orders[order_id]] if order_id in orders else ([] if order_id else orders.values())
        return sum(float(line.get("qty") or 0) for o in picked for line in o.get("lines") or [] if line.get("productId") == product_id)


def add_usage(acc: dict[str, int], usage: Any) -> None:
    for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"):
        v = getattr(usage, k, None) if not isinstance(usage, dict) else usage.get(k)
        if v:
            acc[k] = acc.get(k, 0) + int(v)

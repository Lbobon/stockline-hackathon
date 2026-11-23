"""
What every agent of a call shares: who is calling, what was already fetched, the order in progress, the caller's
language, and a timeline of who did what when (the call report draws it).

Agents never talk to each other directly: they read and write this state. Each piece of work is wrapped in
`state.span(agent, task)` so it lands on the timeline and is pushed live to the browser ("agent_activity").
"""

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.tools import ErpClient, Tool, ToolContext

AGENTS = {
    "voice": "Agent vocal",  # the main model: talks to the caller
    "scout": "Éclaireur",  # listens while the caller speaks, fetches the data before the turn starts
    "fast": "Réflexe",  # answers the trivial turns (« oui, c'est bon ») without the big model
    "scribe": "Greffier",  # writes the complaints and durable facts in the background
    "linguist": "Linguiste",  # language and tu / vous of the caller
    "reporter": "Rapporteur",  # the end-of-call report
}

# $ per million tokens: input, output (cache write = 1.25 × input, cache read = 0.1 × input)
PRICES = {"opus": (4.0, 20.0), "sonnet": (2.0, 10.0), "haiku": (0.10, 0.50)}


def price_of(model: str) -> tuple[float, float]:
    return next((p for k, p in PRICES.items() if k in model), PRICES["sonnet"])


def cost(usage: dict[str, int], model: str) -> float:
    pin, pout = price_of(model)
    return (
        usage.get("input_tokens", 0) * pin
        + usage.get("cache_creation_input_tokens", 0) * pin * 1.25
        + usage.get("cache_read_input_tokens", 0) * pin * 0.1
        + usage.get("output_tokens", 0) * pout
    ) / 1e6


@dataclass
class Span:
    agent: str
    task: str
    turn: int
    start_ms: int
    end_ms: int | None = None
    ok: bool = True
    detail: str = ""


@dataclass
class CallState:
    id: str = field(default_factory=lambda: f"call_{uuid.uuid4().hex[:10]}")
    actor_name: str | None = None
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))
    t0: float = field(default_factory=time.perf_counter)

    client: dict | None = None  # {id, name} once identified (by the scout or the main agent)
    candidates: list[dict] = field(default_factory=list)  # several plausible clients: the agent must ask
    facts: dict[str, str] = field(default_factory=dict)  # key → text, prefetched data for the main agent
    given: set[str] = field(default_factory=set)  # facts already put in front of the main agent
    language: str = "fr"
    register: str = "vous"  # tu | vous
    order: dict | None = None  # last summary of the order being worked on
    awaiting_yes: str | None = None  # order id the agent just asked to confirm
    turn_no: int = 0

    turns: list[dict] = field(default_factory=list)  # one entry per caller turn (timings, path, texts)
    spans: list[Span] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)  # {agent, name, args, ok, ms, result, turn}
    usage: dict[str, dict[str, Any]] = field(default_factory=dict)  # agent → {model, tokens…}
    listeners: list[Callable[[dict], Awaitable[None]]] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def now(self) -> int:
        """Milliseconds since the call started."""
        return int((time.perf_counter() - self.t0) * 1000)

    async def emit(self, msg: dict) -> None:
        for listener in list(self.listeners):
            try:
                await listener(msg)
            except Exception:  # a closed socket must not break an agent
                pass

    @asynccontextmanager
    async def span(self, agent: str, task: str, detail: str = ""):
        s = Span(agent, task, self.turn_no, self.now(), detail=detail)
        self.spans.append(s)
        await self.emit({"type": "agent_activity", "agent": agent, "label": AGENTS.get(agent, agent), "task": task, "status": "start"})
        try:
            yield s
        except BaseException:
            s.ok = False
            raise
        finally:
            s.end_ms = self.now()
            await self.emit(
                {
                    "type": "agent_activity",
                    "agent": agent,
                    "label": AGENTS.get(agent, agent),
                    "task": task,
                    "status": "done" if s.ok else "error",
                    "ms": s.end_ms - s.start_ms,
                }
            )

    def add_usage(self, agent: str, model: str, usage: Any) -> None:
        acc = self.usage.setdefault(agent, {"model": model, "calls": 0})
        acc["calls"] += 1
        for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"):
            v = usage.get(k) if isinstance(usage, dict) else getattr(usage, k, None)
            if v:
                acc[k] = acc.get(k, 0) + int(v)

    def cost_by_agent(self) -> dict[str, float]:
        return {a: round(cost(u, u["model"]), 5) for a, u in self.usage.items()}

    def see_tool(self, agent: str, name: str, args: dict, ok: bool, ms: int, result: Any) -> None:
        """Record a tool call (any agent) and keep the shared view in sync: client identified, order in progress."""
        self.tool_calls.append({"agent": agent, "name": name, "args": args, "ok": ok, "ms": ms, "result": result, "turn": self.turn_no})
        if not ok or not isinstance(result, dict):
            return
        if name == "get_client" and result.get("id"):
            self.client = {"id": result["id"], "name": result.get("name")}
            self.candidates = []
        elif result.get("lines") is not None and result.get("id", "").startswith("ord_"):
            self.order = result
            if not self.client and args.get("clientId"):
                self.client = {"id": args["clientId"], "name": result.get("client")}


async def run_tool(state: CallState, agent: str, tools: dict[str, Tool], name: str, args: dict, actor_name: str | None = None) -> Any:
    """Run a kit tool on behalf of a side agent (same ERP client, same audit actor as the voice agent). None on error."""
    started = time.perf_counter()
    ctx = ToolContext(erp=ErpClient("voice_agent", actor_name or AGENTS.get(agent)), actor="voice_agent")
    try:
        result, ok = await tools[name].run(args, ctx), True
    except Exception as e:
        result, ok = {"error": str(e) or type(e).__name__}, False
    state.see_tool(agent, name, args, ok, int((time.perf_counter() - started) * 1000), result)
    return result if ok else None

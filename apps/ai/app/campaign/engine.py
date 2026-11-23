"""Campaign engine: many outbound calls at once, streamed live (spec: .plans/demand-radar-spec.md).

One call = the real voice agent (run_agent, voice mode, real ERP tools) speaking first from a hidden brief, against a
simulated client played by a fast model from the client's persona card. After each client reply, Jev decides what it
was (oui / modifie / non / rappeler). Every order the agent writes re-optimizes tomorrow's routes, pushed as a
`route` event. Everything is also kept in the campaign snapshot (GET /ai/campaigns/{id}).
"""

from __future__ import annotations

import asyncio
import random
import uuid
from dataclasses import asdict, dataclass, field

from app.agent import run_agent
from app.clients import LLMRequest, LLMText
from app.clients.factory import get_jev_client, get_llm_client, make_llm_client
from app.clients.jev import choice_question
from app.config import LLMSettings, settings
from app.schemas import ChatMessage, ErrorEvent, TextEvent, ToolResultEvent
from app.tools import ErpClient, all_tools

from .brief import brief_for

END = "[FIN]"
MAX_ROUNDS = 6
ORDER_TOOLS = {"finalize_order", "add_order_line", "update_order_line", "create_draft_order"}

DECISIONS = {
    "oui": "Le client accepte la proposition telle quelle",
    "modifie": "Le client accepte mais change une quantité ou un produit",
    "non": "Le client refuse, il ne veut rien de plus",
    "rappeler": "Le client n'est pas disponible ou veut être rappelé plus tard",
}

# How the simulated clients lean (seeded per client, so a replay gives the same story).
DISPOSITIONS = [
    (0.60, "Tu es plutôt content qu'on t'appelle : si la proposition ressemble à ce que tu prends d'habitude, tu acceptes."),
    (0.22, "Tu acceptes, mais tu changes une chose (une quantité un peu différente, ou tu retires un produit)."),
    (0.10, "Ce n'est pas le moment : tu n'as besoin de rien de plus, tu refuses poliment."),
    (0.08, "Tu es en plein rush : tu demandes qu'on te rappelle dans une heure."),
]

CALLEE_RULES = """Tu joues un client de Médina Viandes (grossiste en viande halal, MIN des Arnavaux, Marseille). Nous sommes \
le mardi 24 novembre 2026 vers 5 h 30. L'agent de Médina t'APPELLE (tu ne t'y attendais pas) au sujet de ta livraison de \
demain.

Ta fiche (qui tu es, comment tu parles, ce que tu commandes) :
<fiche>
{persona}
</fiche>

Ton état d'esprit pour cet appel : {disposition}

Règles :
- Tu PARLES au téléphone : UNE seule réplique à la fois, une ou deux phrases courtes, langage oral, avec tes tics de langage.
- Tu réponds à ce que l'agent dit ; tu ne connais aucun identifiant technique.
- Quand l'appel est fini (commande validée, refus, ou rappel convenu), dis au revoir puis écris {end} à la fin.
- Écris uniquement ce que tu dis, sans didascalies ni guillemets."""


@dataclass
class Call:
    callId: str
    clientId: str
    clientName: str
    lat: float
    lng: float
    kind: str  # predicted | upsell
    status: str = "queued"  # queued | ringing | talking | confirmed | modified | declined | callback | failed
    transcript: list[dict] = field(default_factory=list)
    decision: dict | None = None
    orderId: str | None = None
    totalHt: float = 0.0
    lines: list[dict] = field(default_factory=list)


@dataclass
class Campaign:
    id: str
    date: str
    status: str = "running"
    calls: list[Call] = field(default_factory=list)
    route: dict | None = None
    totals: dict = field(default_factory=lambda: {"called": 0, "confirmed": 0, "declined": 0, "callback": 0, "revenueHt": 0.0, "upliftHt": 0.0})
    log: list[dict] = field(default_factory=list)  # every event, replayed to late subscribers
    subscribers: list[asyncio.Queue] = field(default_factory=list)
    forecast: dict = field(default_factory=dict)

    def snapshot(self) -> dict:
        return {"id": self.id, "date": self.date, "status": self.status, "calls": [asdict(c) for c in self.calls],
                "route": self.route, "totals": self.totals}  # fmt: skip


CAMPAIGNS: dict[str, Campaign] = {}


def emit(c: Campaign, event: dict) -> None:
    c.log.append(event)
    for q in list(c.subscribers):
        q.put_nowait(event)


def _persona(client_id: str) -> str:
    folder = settings.data_dir / "context" / "clients"
    num = client_id.split("_")[-1]
    hit = next(iter(sorted(folder.glob(f"c{num}-*.md"))), None)
    return hit.read_text(encoding="utf-8") if hit else f"Client {client_id}."


def _disposition(client_id: str) -> str:
    r = random.Random(f"radar-{client_id}").random()
    acc = 0.0
    for p, text in DISPOSITIONS:
        acc += p
        if r < acc:
            return text
    return DISPOSITIONS[0][1]


_callee_llm = None


def callee_llm():
    global _callee_llm
    if _callee_llm is None:
        cfg = settings.llm
        if cfg.provider == "anthropic":
            _callee_llm = make_llm_client(LLMSettings(provider="anthropic", model="claude-haiku-5-5", api_key=cfg.api_key, base_url=cfg.base_url, effort="low"))
        else:
            _callee_llm = get_llm_client()
    return _callee_llm


async def _complete(system: str, messages: list[ChatMessage]) -> str:
    text = ""
    async for ev in callee_llm().stream(LLMRequest(system=system, messages=messages, max_tokens=300)):
        if isinstance(ev, LLMText):
            text += ev.delta
    return text.strip()


def _keyword_decision(said: str) -> str:
    s = said.lower()
    if any(w in s for w in ("rappel", "plus tard", "pas le temps", "dans une heure")):
        return "rappeler"
    if any(w in s for w in ("non merci", "pas besoin", "ça ira", "non, ", "pas cette fois")):
        return "non"
    if any(w in s for w in ("plutôt", "mets-moi", "mettez-moi", "enlève", "retire", "au lieu", "seulement")):
        return "modifie"
    return "oui"


async def decide(agent_said: str, client_said: str) -> dict:
    """Jev in ~300 ms; keyword fallback when Jev is off, slow or unsure."""
    jev = get_jev_client()
    if jev:
        try:
            ans = await asyncio.wait_for(jev.ask({"agent": agent_said[-600:], "client": client_said},
                                                 {"d": choice_question("Que répond le client à la proposition de l'agent ?", DECISIONS)}), 3.0)  # fmt: skip
            a = ans["answers"]["d"]
            return {"label": a["choice"], "confidence": round(a["confidence"], 2), "source": "jev"}
        except Exception as e:  # never let a decision break a call
            print(f"[campaign] jev indisponible ({type(e).__name__}) : repli mots-clés", flush=True)
    return {"label": _keyword_decision(client_said), "confidence": 0.6, "source": "keywords"}


# ── route ──

_route_lock = asyncio.Lock()


async def push_route(c: Campaign) -> None:
    """Re-optimize the date's runs and push the plan (one at a time; a burst of orders collapses into a few plans)."""
    if _route_lock.locked():
        return  # the plan being computed will be followed by another one after the current call's order anyway
    async with _route_lock:
        await asyncio.sleep(0.3)  # let near-simultaneous orders land first
        try:
            plan = await ErpClient("voice_agent", "Radar").get("/api/routing/optimize", {"date": c.date})
        except Exception as e:
            print(f"[campaign] route: {e}", flush=True)
            return
        c.route = plan
        emit(c, {"type": "route", "plan": plan})


def _totals(c: Campaign) -> None:
    t = c.totals
    t["called"] = sum(x.status not in ("queued", "ringing") for x in c.calls)
    t["confirmed"] = sum(x.status in ("confirmed", "modified") for x in c.calls)
    t["declined"] = sum(x.status == "declined" for x in c.calls)
    t["callback"] = sum(x.status == "callback" for x in c.calls)
    t["revenueHt"] = round(sum(x.totalHt for x in c.calls), 2)
    emit(c, {"type": "totals", **t})


# ── one call ──


def _set(c: Campaign, call: Call, status: str) -> None:
    call.status = status
    emit(c, {"type": "call_status", "callId": call.callId, "clientId": call.clientId, "status": status})


def _line(c: Campaign, call: Call, who: str, text: str) -> None:
    if text:
        call.transcript.append({"who": who, "text": text})
        emit(c, {"type": "line", "callId": call.callId, "who": who, "text": text})


async def _agent_turn(history: list[ChatMessage], tools) -> tuple[str, list[ToolResultEvent], str | None]:
    text, results, error = "", [], None
    async for ev in run_agent(history, llm=get_llm_client(), tools=tools, mode="voice", actor_name="Agent vocal (radar)"):
        if isinstance(ev, TextEvent):
            text += ev.delta
        elif isinstance(ev, ToolResultEvent):
            results.append(ev)
        elif isinstance(ev, ErrorEvent):
            error = ev.message
    return text.strip(), results, error


def _order_from(results: list[ToolResultEvent]) -> dict | None:
    """The last order the agent wrote this turn (finalize_order wraps it in {decision, order})."""
    for r in reversed(results):
        if r.name in ORDER_TOOLS and r.ok and isinstance(r.result, dict):
            o = r.result.get("order") if r.name == "finalize_order" else r.result
            if isinstance(o, dict) and o.get("id"):
                return o
    return None


async def run_call(c: Campaign, call: Call, row: dict) -> None:
    tools = all_tools()
    _set(c, call, "ringing")
    await asyncio.sleep(random.uniform(0.6, 2.0))  # it rings
    _set(c, call, "talking")
    system = CALLEE_RULES.format(persona=_persona(call.clientId), disposition=_disposition(call.clientId), end=END)
    agent_history = [ChatMessage(role="user", content=brief_for(row, c.date))]
    callee_view: list[ChatMessage] = []
    order = None
    try:
        for _ in range(MAX_ROUNDS):
            said, results, error = await _agent_turn(agent_history, tools)
            if error:
                raise RuntimeError(error)
            agent_history.append(ChatMessage(role="assistant", content=said))
            _line(c, call, "agent", said)
            if o := _order_from(results):
                order = o
                # an add-on call only brings what it added to the existing order
                added = float(o.get("totalHt") or 0) - (float(row.get("totalHt") or 0) if row["status"] == "upsell" else 0)
                call.orderId, call.totalHt, call.lines = o["id"], round(max(added, 0.0), 2), o.get("lines") or []
                emit(c, {"type": "order", "callId": call.callId, "clientId": call.clientId, "orderId": o["id"],
                         "totalHt": call.totalHt, "lines": call.lines, "status": o.get("status")})  # fmt: skip
                asyncio.create_task(push_route(c))
                _totals(c)
            callee_view.append(ChatMessage(role="user", content=said or "(silence)"))
            reply = await _complete(system, callee_view)
            done = END in reply
            reply = reply.replace(END, "").strip().split("\n\n")[0].strip()  # one spoken turn, not a monologue
            if reply:
                callee_view.append(ChatMessage(role="assistant", content=reply))
                _line(c, call, "client", reply)
                if call.decision is None or call.decision["label"] != "oui" or not order:
                    call.decision = await decide(said, reply)
                    emit(c, {"type": "decision", "callId": call.callId, **call.decision})
            if done or not reply:
                break
            agent_history.append(ChatMessage(role="user", content=reply))
        label = (call.decision or {}).get("label")
        if order and order.get("status") in ("confirmed", "prepared", None):
            final = "modified" if label == "modifie" else "confirmed"
        elif order:  # kept as a draft by the order checks: still a yes from the client
            final = "modified" if label == "modifie" else "confirmed"
        else:
            final = {"non": "declined", "rappeler": "callback"}.get(label or "", "declined")
        _set(c, call, final)
    except Exception as e:
        print(f"[campaign] appel {call.clientName} : {type(e).__name__}: {e}", flush=True)
        _set(c, call, "failed")
    _totals(c)


async def run(c: Campaign, rows: list[dict], concurrency: int) -> None:
    sem = asyncio.Semaphore(concurrency)

    async def one(call: Call, row: dict) -> None:
        async with sem:
            await run_call(c, call, row)

    await asyncio.gather(*(one(call, row) for call, row in zip(c.calls, rows)))
    await asyncio.sleep(0.5)
    await push_route(c)
    c.totals["upliftHt"] = round(sum(r.get("upliftHt", 0) for call, r in zip(c.calls, rows) if call.status in ("confirmed", "modified")), 2)
    c.status = "done"
    _totals(c)
    emit(c, {"type": "done", "totals": c.totals})


async def start(date: str | None, client_ids: list[str] | None, concurrency: int = 8) -> Campaign:
    erp = ErpClient("voice_agent", "Radar")
    forecast = await erp.get("/api/radar/forecast", {"date": date})
    rows = [r for r in forecast["clients"] if r["status"] in ("predicted", "upsell") and (not client_ids or r["clientId"] in client_ids)]
    c = Campaign(id=f"cmp_{uuid.uuid4().hex[:8]}", date=forecast["date"], forecast=forecast)
    c.calls = [Call(callId=f"call_{i + 1:02d}", clientId=r["clientId"], clientName=r["name"], lat=r["lat"], lng=r["lng"], kind=r["status"])
               for i, r in enumerate(rows)]  # fmt: skip
    CAMPAIGNS[c.id] = c
    try:
        c.route = await erp.get("/api/routing/optimize", {"date": c.date})
    except Exception:
        c.route = None
    asyncio.create_task(run(c, rows, max(1, min(concurrency, 16))))
    return c


async def replay_deliveries(c: Campaign, speed: float = 10.0) -> None:
    """Drivers confirm each stop of the final route, in ETA order (fake clock: `speed` route minutes per second)."""
    plan = c.route or {}
    stops = sorted(((s["eta"], s) for t in plan.get("optimized", []) for s in t["stops"]), key=lambda x: x[0])
    prev = None
    for eta, s in stops:
        if prev:
            h1, m1 = map(int, prev.split(":"))
            h2, m2 = map(int, eta.split(":"))
            await asyncio.sleep(max(0.15, ((h2 * 60 + m2) - (h1 * 60 + m1)) / max(speed, 0.1)))
        prev = eta
        emit(c, {"type": "delivered", "orderId": s["orderId"], "clientId": s["clientId"], "at": eta})
    emit(c, {"type": "deliveries_done", "count": len(stops)})

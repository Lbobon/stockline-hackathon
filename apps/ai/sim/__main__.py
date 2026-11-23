"""
Simulated phone calls against the real agent.

A model plays a Médina client from its persona file (data/context/clients/*.md, the files written for the humans who
play clients), the agent answers exactly as on WS /voice (run_agent, voice mode, real ERP, minus STT/TTS), then each
call is scored: checks on the tool trace (scenarios.py) + a judge model. Writes a report with the success rate,
the agent's latency per turn and its cost per call.

    cd apps/ai
    uv run python -m sim                      # every scenario once
    uv run python -m sim rupture_broche camion
    uv run python -m sim --repeat 3 --concurrency 4
    uv run python -m sim --list
    uv run python -m sim --classic            # the plain agent, without the orchestra (baseline)
    uv run python -m sim --reports            # + the Rapporteur's call report for each call (see /calls in the web app)

By default each call runs through the orchestra (app/orchestra: Éclaireur, Réflexe, Greffier, Linguiste) exactly as
WS /voice does; the caller's words are fed as a live transcript at --speech-rate words/s (2.6 ≈ normal speech) so the
Éclaireur can work while the "caller speaks". Latency is measured from the end of the caller's speech.

Needs the ERP running (make dev / the medina-kit launch). Resets the ERP first (POST /api/admin/reset) so every run
starts from the same data; --no-reset keeps your changes. Costs real tokens: about one agent call per caller turn.
Caller and judge: SIM_CALLER_MODEL / SIM_JUDGE_MODEL (default claude-opus-5-5), same Anthropic key as the agent.
"""

import argparse
import asyncio
import json
import os
import re
import statistics
import sys
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import httpx

from app.agent import run_agent
from app.agent.outbound import shortage_brief
from app.agent.radar_brief import brief as radar_brief
from app.clients import LLMRequest, LLMText, LLMUsage
from app.clients.factory import make_llm_client
from app.config import REPO_ROOT, LLMSettings, settings
from app.guard import Guard, check_text
from app.schemas import ChatMessage, DoneEvent, ErrorEvent, TextEvent, ToolCallEvent, ToolResultEvent
from app.orchestra import CallSession, FastEvent, FillerEvent
from app.orchestra import store as call_store
from app.tools import all_tools

from app.agent.prompts import read_context_files

from .scenarios import SCENARIOS, Scenario
from .trace import Trace, Turn, add_usage

PERSONAS = REPO_ROOT / "data" / "context" / "clients"
RUNS = Path(__file__).parent / "runs"
MAX_TURNS = 12
END = "[FIN]"
PICK_UP = "(La ligne décroche : l'agent attend que vous parliez. À vous.)"
OUTBOUND_TURN = "(appel sortant : l'agent parle en premier)"


async def opening_brief(sc: Scenario) -> str:
    """The hidden first turn of an outbound call: a shortage (Ce matin) or a radar call (the client's forecast row)."""
    if sc.radar_client_id:
        async with httpx.AsyncClient(timeout=30) as http:
            f = (await http.get(f"{settings.erp_api_url}/api/radar/forecast", params={"date": sc.radar_date})).json()
        row = next((r for r in f["clients"] if r["clientId"] == sc.radar_client_id), None)
        if not row:
            raise ValueError(f"{sc.radar_client_id} n'est pas dans le radar du {sc.radar_date}")
        return radar_brief(row, sc.radar_date)
    return await shortage_brief(sc.outbound_order_id)


def ringing(opening: str) -> str:
    """What the caller model hears first on an outbound call: Médina calls, the agent's opening line."""
    return f"(Ton téléphone sonne, tu décroches : c'est Médina Viandes qui t'appelle.)\n\nL'agent : {opening}"

# $ per million tokens (Anthropic list prices, 2026-11). Cache write = 1.25 × input (5-min cache).
PRICES = {  # model: (input, output, cache read)
    "claude-sonnet-5-5": (2.00, 10.00, 0.10),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
    "claude-opus-5-5": (4.00, 20.00, 0.20),
}

CALLER_RULES = """Tu joues un client de Médina Viandes (grossiste en viande halal, MIN des Arnavaux, Marseille) qui appelle \
l'agent vocal de Médina. Nous sommes le mardi 24 novembre 2026, il est 5 h 30.

Ta fiche (qui tu es, comment tu parles, ce que tu commandes) :
<fiche>
{persona}
</fiche>

Ton objectif pour cet appel (ne le récite pas d'un bloc, joue-le naturellement) :
<objectif>
{goal}
</objectif>

Règles :
- Tu PARLES au téléphone : phrases courtes, langage oral, une ou deux phrases par réplique, avec tes tics de langage.
- Tu ne connais aucun identifiant technique (cli_…, prd_…, ord_…). Tu dis le nom de ton commerce comme un vrai client.
- Tu ne donnes une information que si elle est utile ou si on te la demande. Tu réponds aux questions de l'agent.
- Si l'agent se trompe (mauvais client, mauvaise quantité, mauvais jour), tu le corriges comme un vrai client.
- Tu ne rends pas service à l'agent : s'il oublie quelque chose, c'est son problème.
- Quand l'appel est terminé (commande confirmée, problème réglé ou impasse), dis au revoir puis écris {end} à la fin.
- Écris uniquement ce que tu dis, sans didascalies ni guillemets."""

JUDGE_PROMPT = """You grade a phone call between a voice agent of Médina Viandes (halal meat wholesaler, Marseille; \
today is Tuesday 24/11/2026, 05:30) and a client played by a model. The agent speaks French and acts on the ERP through tools.

Scenario: {title}
What the client wanted (their private brief): {goal}
What a good agent does: {expect}

Key business rules: identify the caller before acting (several clients share a name); warnings never block in the ERP, the \
agent must react (blocked account: never confirm; over the credit limit: draft + note, don't confirm); prices only from \
the ERP (get_price, or check_product with the client, which returns that same client price), never a discount (log it, pass it to the sales rep); carcasses are sold per kg (one lamb ≈ 16-20 kg); never \
substitute without explicit agreement; confirm an order only after a recap and an explicit yes; the 3 a.m. order cutoff \
is passed, today's trucks are loaded; never invent facts (times, dates, stock) the tools didn't give; voice style: \
short, no ids or markdown read aloud, numbers said naturally.

Besides the tools, the agent's prompt holds the company documents, given to you in your system prompt (procedures, \
who does what, product glossary, delivery zones and departure times, today's stockouts): a fact from them is not \
invented just because no tool returned it. Do flag precise facts (prices, times, amounts, dates) that neither a tool nor \
these documents give.

Automatic checks on the tool trace:
{checks}

Transcript (CLIENT / AGENT, the agent's tool calls in brackets):
{transcript}

Answer with JSON only, no prose around it:
{{"success": true|false,   // did the agent handle the call the way a good Médina employee would (the client's goal, the rules)?
  "smart": 1-5, "natural": 1-5,
  "problems": ["short, concrete issue", ...],   // empty if none
  "summary": "one sentence"}}"""


def dim(s: str) -> str:
    return f"\x1b[2m{s}\x1b[0m"


def anthropic(model: str, effort: str) -> object:
    return make_llm_client(LLMSettings(provider="anthropic", model=model, api_key=settings.llm.api_key, base_url=settings.llm.base_url, effort=effort))


async def complete(llm, system: str, messages: list[ChatMessage], usage: dict, max_tokens: int) -> str:
    text = ""
    async for ev in llm.stream(LLMRequest(system=system, messages=messages, max_tokens=max_tokens)):
        if isinstance(ev, LLMText):
            text += ev.delta
        elif isinstance(ev, LLMUsage):
            add_usage(usage, ev)
    return text.strip()


# ── one call ─────────────────────────────────────────────────────────────────────────────────────────────────


async def simulate(sc: Scenario, caller_llm, agent_llm) -> Trace:
    trace = Trace()
    persona = (PERSONAS / sc.persona).read_text(encoding="utf-8")
    caller_system = CALLER_RULES.format(persona=persona, goal=sc.goal, end=END)
    caller_view: list[ChatMessage] = [ChatMessage(role="user", content=PICK_UP)]
    agent_history: list[ChatMessage] = []
    tools = all_tools()
    if sc.outbound_order_id or sc.radar_client_id:  # the agent calls and speaks first, from the hidden brief (agent/outbound.py, agent/radar_brief.py)
        opening = Turn(caller=OUTBOUND_TURN)
        trace.turns.append(opening)
        agent_history.append(ChatMessage(role="user", content=await opening_brief(sc)))
        started, by_id = time.perf_counter(), {}
        async for ev in run_agent(agent_history, llm=agent_llm, tools=tools, mode="voice", actor_name="Agent vocal"):
            match ev:
                case TextEvent(delta=delta):
                    opening.first_text_ms = opening.first_text_ms or int((time.perf_counter() - started) * 1000)
                    opening.agent += delta
                case ToolCallEvent(call=call):
                    by_id[call.id] = {"name": call.name, "args": call.arguments}
                    opening.tools.append(by_id[call.id])
                case ToolResultEvent(call_id=cid, ok=ok, ms=ms, result=result):
                    by_id.get(cid, {}).update(ok=ok, ms=ms, result=result)
                case DoneEvent(usage=usage):
                    if usage:
                        add_usage(trace.usage, usage)
        opening.agent = opening.agent.strip()
        opening.total_ms = int((time.perf_counter() - started) * 1000)
        if guard:
            opening.guard = [v.wire() for v in await check_text(guard, opening.agent)]
        caller_view = [ChatMessage(role="user", content=ringing(opening.agent))]

    for _ in range(MAX_TURNS):
        said = await complete(caller_llm, caller_system, caller_view, trace.sim_usage, 2000)
        done = END in said
        said = said.replace(END, "").strip()
        if not said:
            trace.ended_by = "caller"
            break
        caller_view.append(ChatMessage(role="assistant", content=said))
        turn = Turn(caller=said)
        trace.turns.append(turn)

        # the agent's turn, exactly as WS /voice runs it
        agent_history.append(ChatMessage(role="user", content=said))
        started, by_id = time.perf_counter(), {}
        async for ev in run_agent(agent_history, llm=agent_llm, tools=tools, mode="voice", actor_name="Agent vocal"):
            match ev:
                case TextEvent(delta=delta):
                    if turn.first_text_ms is None:
                        turn.first_text_ms = int((time.perf_counter() - started) * 1000)
                    turn.agent += delta
                case ToolCallEvent(call=call):
                    by_id[call.id] = {"name": call.name, "args": call.arguments}
                    turn.tools.append(by_id[call.id])
                case ToolResultEvent(call_id=cid, ok=ok, ms=ms, result=result):
                    by_id.get(cid, {}).update(ok=ok, ms=ms, result=result)
                case DoneEvent(usage=usage):
                    if usage:
                        add_usage(trace.usage, usage)
                case ErrorEvent(message=message):
                    turn.error = message
        turn.total_ms = int((time.perf_counter() - started) * 1000)
        turn.agent = turn.agent.strip()
        if turn.error:
            trace.ended_by = "error"
            break
        if done:
            trace.ended_by = "caller"
            break
        caller_view.append(ChatMessage(role="user", content=turn.agent or "(silence)"))
    else:
        trace.ended_by = "max_turns"
    return trace


async def simulate_orchestra(sc: Scenario, caller_llm, agent_llm, rate: float, reports: bool) -> Trace:
    trace = Trace()
    persona = (PERSONAS / sc.persona).read_text(encoding="utf-8")
    caller_system = CALLER_RULES.format(persona=persona, goal=sc.goal, end=END)
    caller_view: list[ChatMessage] = [ChatMessage(role="user", content=PICK_UP)]
    session = CallSession(actor_name="Agent vocal", llm=agent_llm)
    guard = Guard(session.state, mode="shadow") if os.environ.get("SIM_GUARD") == "1" else None  # --guard: Jev checks each sentence
    warm = asyncio.create_task(session.warm())  # the line rings: prompt cache written meanwhile
    if sc.outbound_order_id or sc.radar_client_id:  # the agent calls and speaks first, as WS /voice does for ?outbound=<orderId>
        await warm
        opening = Turn(caller=OUTBOUND_TURN)
        trace.turns.append(opening)
        started = time.perf_counter()
        brief = await opening_brief(sc)
        if guard:
            guard.add_brief(brief)
        async for ev in session.turn(brief):
            if isinstance(ev, TextEvent):
                opening.first_text_ms = opening.first_text_ms or int((time.perf_counter() - started) * 1000)
                opening.agent += ev.delta
            elif isinstance(ev, ErrorEvent):
                opening.error = ev.message
        opening.agent = opening.agent.strip()
        opening.total_ms = int((time.perf_counter() - started) * 1000)
        caller_view = [ChatMessage(role="user", content=ringing(opening.agent))]

    for i in range(MAX_TURNS):
        said = await complete(caller_llm, caller_system, caller_view, trace.sim_usage, 2000)
        done = END in said
        said = said.replace(END, "").strip()
        if not said:
            trace.ended_by = "caller"
            break
        if i == 0:
            await warm
        caller_view.append(ChatMessage(role="assistant", content=said))
        turn = Turn(caller=said)
        trace.turns.append(turn)

        # the caller "speaks": live transcript, word by word
        words = said.split()
        session.speech_start()
        for k in range(1, len(words) + 1):
            session.on_partial(" ".join(words[:k]))
            if rate:
                await asyncio.sleep(1 / rate)

        started = time.perf_counter()
        async for ev in session.turn(said):
            ms = int((time.perf_counter() - started) * 1000)
            match ev:
                case FillerEvent() | FastEvent():
                    if turn.first_sound_ms is None:
                        turn.first_sound_ms = ms
                    if isinstance(ev, FastEvent):
                        turn.path = "fast"
                case TextEvent(delta=delta):
                    if turn.first_text_ms is None:
                        turn.first_text_ms = ms
                    if turn.first_sound_ms is None:
                        turn.first_sound_ms = ms
                    turn.agent += delta
                case ErrorEvent(message=message):
                    turn.error = message
        turn.total_ms = int((time.perf_counter() - started) * 1000)
        turn.agent = turn.agent.strip()
        turn.prefetched = list(session.state.turns[-1].get("prefetched") or [])
        if guard:
            guard.client_said(said)
            turn.guard = [v.wire() for v in await check_text(guard, turn.agent)]
        if turn.error:
            trace.ended_by = "error"
            break
        if done:
            trace.ended_by = "caller"
            break
        caller_view.append(ChatMessage(role="user", content=turn.agent or "(silence)"))
    else:
        trace.ended_by = "max_turns"

    report = await session.finish(report=reports)
    if report:
        report["source"] = "sim"
        report["scenario"] = sc.id
        call_store.save(report)
        trace.report_id = report["id"]
    # every agent's tool calls, on the turn they happened in (the checks read them all)
    for c in session.state.tool_calls:
        n = min(max(c["turn"], 1), len(trace.turns)) - 1
        if 0 <= n < len(trace.turns):
            trace.turns[n].tools.append({k: c[k] for k in ("name", "args", "ok", "ms", "result", "agent")})
    if "voice" in session.state.usage:
        add_usage(trace.usage, session.state.usage["voice"])
    trace.cost_by_agent = session.state.cost_by_agent()
    return trace


def transcript(trace: Trace) -> str:
    lines = []
    for t in trace.turns:
        lines.append(f"CLIENT: {t.caller}")
        for c in t.tools:
            res = json.dumps(c.get("result"), ensure_ascii=False)
            by = f"{c['agent']}: " if c.get("agent") not in (None, "voice") else ""
            # 2000 chars: with 300 the judge lost the usual basket, the volume prices, the address… and flagged true facts as invented
            lines.append(f"  [{by}{c['name']} {json.dumps(c['args'], ensure_ascii=False)} → {'ok' if c.get('ok') else 'ERROR'} {res[:2000]}]")
        lines.append(f"AGENT: {t.agent}" + (f"  (error: {t.error})" if t.error else ""))
    return "\n".join(lines)


STYLE = [  # things that must never be heard on the phone
    ("identifiant lu à voix haute", re.compile(r"\b(cli|prd|ord|sup|lot|po)_\d+")),
    ("markdown à l'oral", re.compile(r"\*\*|^\s*[-#] ", re.M)),
]


def judge_system() -> str:
    """The judge gets the company documents the agent has in its prompt, else it flags true facts (the Toulon departure at
    5:45, carcasses weighed at the dock) as invented. In the system prompt, so it is cached across the calls of a run."""
    docs = read_context_files()
    return "You are a strict, fair evaluator of voice agents. Output JSON only." + (
        f"\n\nCompany documents the agent has in its prompt:\n<documents>\n{docs}\n</documents>" if docs else "")


async def grade(sc: Scenario, trace: Trace, judge_llm) -> dict:
    checks = [(name, bool(fn(trace))) for name, fn in sc.checks]
    said = " ".join(t.agent for t in trace.turns)
    style = [name for name, rx in STYLE if rx.search(said)]
    checks_txt = "\n".join(f"- {'PASS' if ok else 'FAIL'} {name}" for name, ok in checks) or "- (none)"
    prompt = JUDGE_PROMPT.format(title=sc.title, goal=sc.goal, expect=sc.expect, checks=checks_txt, transcript=transcript(trace))
    raw = await complete(judge_llm, judge_system(), [ChatMessage(role="user", content=prompt)], trace.sim_usage, 4000)
    try:
        judge = json.loads(re.search(r"\{[\s\S]*\}", raw)[0])
    except Exception:
        judge = {"success": False, "smart": None, "natural": None, "problems": [f"judge output unreadable: {raw[:200]}"], "summary": ""}
    ok = all(c for _, c in checks) and bool(judge.get("success")) and trace.ended_by != "error"
    return {"success": ok, "checks": checks, "style": style, "judge": judge}


def cost(usage: dict, model: str) -> float:
    i, o, r = PRICES.get(model, PRICES["claude-sonnet-5-5"])
    return (
        usage.get("input_tokens", 0) * i
        + usage.get("output_tokens", 0) * o
        + usage.get("cache_read_input_tokens", 0) * r
        + usage.get("cache_creation_input_tokens", 0) * i * 1.25
    ) / 1e6


# ── a run ────────────────────────────────────────────────────────────────────────────────────────────────────


async def run_one(sc: Scenario, rep: int, sem: asyncio.Semaphore, models: dict) -> dict:
    async with sem:
        t0 = time.perf_counter()
        try:
            if models["classic"]:
                trace = await simulate(sc, models["caller"], models["agent"])
            else:
                trace = await simulate_orchestra(sc, models["caller"], models["agent"], models["rate"], models["reports"])
            graded = await grade(sc, trace, models["judge"])
        except Exception as e:  # provider down, rate limit…: count it, keep the run going
            trace, graded = Trace(ended_by="error"), {"success": False, "checks": [], "style": [], "judge": {"problems": [f"{type(e).__name__}: {e}"]}}
        by_agent = {k: v for k, v in trace.cost_by_agent.items() if k != "reporter"}
        agent_cost = sum(by_agent.values()) if by_agent else cost(trace.usage, models["agent"].model)
        rec = {
            "scenario": sc.id,
            "title": sc.title,
            "rep": rep,
            **graded,
            "turns": len(trace.turns),
            "ended_by": trace.ended_by,
            "seconds": round(time.perf_counter() - t0, 1),
            "agent_cost_usd": round(agent_cost, 4),
            "agent_usage": trace.usage,
            "cost_by_agent": trace.cost_by_agent,
            "report_id": trace.report_id,
            "sim_usage": trace.sim_usage,
            "trace": [asdict(t) for t in trace.turns],
        }
        failed = [n for n, ok in graded["checks"] if not ok] + (graded["judge"].get("problems") or [])[:1]
        print(f"{'PASS' if rec['success'] else 'FAIL'}  {sc.id}#{rep}  {rec['turns']} tours, ${agent_cost:.3f}" + (dim(f"  — {'; '.join(failed)}") if not rec["success"] else ""), flush=True)
        for t in trace.turns:  # --guard: what the Garde-fou would have blocked (review these: true or false catches?)
            for v in t.guard:
                if v.get("status") == "contradicted":
                    src = (v.get("source") or {}).get("label", "—")
                    print(dim(f"      GARDE-FOU ✗{' BLOQUERAIT' if v.get('strong') else ' (doute, dite)'} {sc.id} « {v['sentence']} »  ← {src} {', '.join((v.get('source') or {}).get('values', []))}"
                              f"  (sup {v.get('pSupported')}, con {v.get('pContradicts')}, mm {v.get('pMismatch')}, pr {v.get('pPromises')})"), flush=True)
        return rec


def pct(values: list[int], p: float) -> int | None:
    if not values:
        return None
    values = sorted(values)
    return values[min(len(values) - 1, int(round(p * (len(values) - 1))))]


def report(recs: list[dict], models: dict, out: Path) -> str:
    n = len(recs)
    ok = sum(r["success"] for r in recs)
    turns = [t for r in recs for t in r["trace"]]
    first = [t["first_text_ms"] for t in turns if t["first_text_ms"] is not None]
    total = [t["total_ms"] for t in turns if t["total_ms"] is not None]
    sound = [t.get("first_sound_ms") if t.get("first_sound_ms") is not None else t["first_text_ms"] for t in turns]
    sound = [x for x in sound if x is not None]
    fast = sum(t.get("path") == "fast" for t in turns)
    prefetched = sum(bool(t.get("prefetched")) for t in turns)
    tools_voice = sum(1 for t in turns for c in t["tools"] if c.get("agent", "voice") == "voice")
    by_agent: dict[str, float] = {}
    for r in recs:
        for k, v in (r.get("cost_by_agent") or {}).items():
            by_agent[k] = by_agent.get(k, 0) + v
    mode = "classic (no orchestra)" if models["classic"] else f"orchestra, speech {models['rate']} words/s"
    agent_cost = sum(r["agent_cost_usd"] for r in recs)
    smart = [r["judge"].get("smart") for r in recs if isinstance(r["judge"].get("smart"), (int, float))]
    natural = [r["judge"].get("natural") for r in recs if isinstance(r["judge"].get("natural"), (int, float))]
    sim_cost = sum(cost(r["sim_usage"], models["caller"].model) for r in recs)  # caller and judge share a price here

    md = [
        f"# Simulated calls — {datetime.now():%Y-%m-%d %H:%M}",
        "",
        f"Agent: `{models['agent'].model}` (effort {settings.llm.effort}) · {mode} · caller: `{models['caller'].model}` · judge: `{models['judge'].model}`",
        "",
        "| | |",
        "|---|---|",
        f"| Success | **{ok}/{n}** ({ok / n:.0%}) |" if n else "| Success | – |",
        f"| Judge, smart / natural (1-5) | {statistics.mean(smart):.1f} / {statistics.mean(natural):.1f} |" if smart and natural else "| Judge | – |",
        f"| End of speech → first sound (filler / fast path / word), median / p90 | {pct(sound, .5)} ms / {pct(sound, .9)} ms |",
        f"| End of speech → first real word, median / p90 | {pct(first, .5)} ms / {pct(first, .9)} ms |",
        f"| Turns answered by the Réflexe / with prefetched data | {fast} / {prefetched} of {len(turns)} |",
        f"| Voice-agent tool calls per turn | {tools_voice / len(turns):.2f} |" if turns else "",
        f"| Cost per call by agent | {' · '.join(f'{k} ${v / n:.4f}' for k, v in sorted(by_agent.items(), key=lambda x: -x[1]))} |" if by_agent and n else "",
        f"| Agent turn → done, median / p90 | {pct(total, .5)} ms / {pct(total, .9)} ms |",
        f"| Agent cost per call (LLM only) | ${agent_cost / n:.3f} |" if n else "",
        f"| Agent cost per successful call | ${agent_cost / ok:.3f} |" if ok else "| Agent cost per successful call | – |",
        f"| Simulator cost (caller + judge, not part of the agent) | ${sim_cost:.2f} |",
        "",
        "Latency is measured from the caller's text to the agent's first word / end of turn, in-process: no STT, no TTS, "
        "no network to the browser. The first word may be a filler (« Je regarde. »). Costs use list prices and exclude STT/TTS.",
        "",
        "## Calls",
        "",
        "| Scenario | Result | Turns | Cost | Failed checks / judge |",
        "|---|---|---|---|---|",
    ]
    for r in recs:
        why = [f"✗ {name}" for name, c in r["checks"] if not c] + [f"style: {s}" for s in r["style"]] + (r["judge"].get("problems") or [])
        md.append(f"| {r['scenario']}#{r['rep']} | {'✅' if r['success'] else '❌'} | {r['turns']} | ${r['agent_cost_usd']:.3f} | {'<br>'.join(why) or r['judge'].get('summary', '')} |")
    md += ["", "## Transcripts", ""]
    for r in recs:
        md += [f"### {r['scenario']}#{r['rep']} — {r['title']} ({'✅' if r['success'] else '❌'})", ""]
        for t in r["trace"]:
            md.append(f"**Client :** {t['caller']}  ")
            if t["tools"]:
                md.append(dim_md(", ".join(c["name"] for c in t["tools"])))
            tag = " ⚡réflexe" if t.get("path") == "fast" else (f" 📦{len(t['prefetched'])} préchargé(s)" if t.get("prefetched") else "")
            md.append(f"**Agent{tag} :** {t['agent']}" + (f" _(erreur : {t['error']})_" if t["error"] else "") + f" _({t.get('first_sound_ms')} / {t['first_text_ms']} / {t['total_ms']} ms)_")
            md.append("")
        md += [f"> Juge : {r['judge'].get('summary', '')}", ""]
    text = "\n".join(line for line in md if line is not None)
    (out / "report.md").write_text(text, encoding="utf-8")
    return text


def dim_md(s: str) -> str:
    return f"<sub>outils : {s}</sub>  "


async def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m sim", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scenarios", nargs="*", help="scenario ids (default: all)")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--no-reset", action="store_true", help="keep the ERP as it is (default: reload it from the data pack first)")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--classic", action="store_true", help="plain agent, no orchestra (the baseline)")
    ap.add_argument("--speech-rate", type=float, default=2.6, help="words/s of the emulated live transcript (0 = instant)")
    ap.add_argument("--reports", action="store_true", help="write the Rapporteur's report for each call (shown at /calls)")
    ap.add_argument("--agent-model", help="override the voice agent model, ex. claude-haiku-5-5")
    ap.add_argument("--guard", action="store_true", help="le Garde-fou in shadow mode: Jev checks every agent sentence, flags are printed")
    a = ap.parse_args()
    if a.guard:
        os.environ["SIM_GUARD"] = "1"

    if a.list:
        for s in SCENARIOS:
            print(f"{s.id:20} {s.title}  [{', '.join(s.tags)}]")
        return
    unknown = set(a.scenarios) - {s.id for s in SCENARIOS}
    if unknown:
        sys.exit(f"unknown scenario(s): {', '.join(sorted(unknown))} (see --list)")
    picked = [s for s in SCENARIOS if not a.scenarios or s.id in a.scenarios]

    if settings.llm.provider != "anthropic":
        sys.exit("the simulator needs LLM_PROVIDER=anthropic in .env (the caller and judge use the same key)")
    models = {
        "agent": anthropic(a.agent_model, settings.llm.effort) if a.agent_model else make_llm_client(settings.llm),
        "caller": anthropic(os.environ.get("SIM_CALLER_MODEL", "claude-opus-5-5"), "low"),
        "judge": anthropic(os.environ.get("SIM_JUDGE_MODEL", "claude-opus-5-5"), "medium"),
    }
    models.update(classic=a.classic, rate=a.speech_rate, reports=a.reports)
    erp = os.environ.get("ERP_API_URL", "http://localhost:4000")
    async with httpx.AsyncClient(timeout=30) as http:
        try:
            if not a.no_reset:
                (await http.post(f"{erp}/api/admin/reset")).raise_for_status()
                print(dim("ERP reloaded from the data pack"))
            else:
                (await http.get(f"{erp}/api/meta")).raise_for_status()
        except httpx.HTTPError as e:
            sys.exit(f"ERP not reachable at {erp} ({e}): start the services first")

    out = RUNS / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True)
    (RUNS / ".gitignore").write_text("*\n")
    print(dim(f"{len(picked) * a.repeat} calls · {'classic' if a.classic else 'orchestra'} · agent {models['agent'].model} · caller {models['caller'].model} · judge {models['judge'].model}"))

    # One round per repeat, the ERP reloaded before each: every scenario is a different client, so a round's calls run in
    # parallel safely, but repeat 2 must not find the order repeat 1 just took (« elle est déjà confirmée »).
    sem = asyncio.Semaphore(a.concurrency)
    recs: list[dict] = []
    shared, alone = [s for s in picked if not s.setup], [s for s in picked if s.setup]
    for r in range(a.repeat):
        if r and not a.no_reset:
            async with httpx.AsyncClient(timeout=30) as http:
                (await http.post(f"{erp}/api/admin/reset")).raise_for_status()
        recs += await asyncio.gather(*(run_one(s, r + 1, sem, models) for s in shared))
        # A scenario that changes shared data (its setup) runs alone, on a freshly reset ERP, after the parallel round
        for s in alone:
            async with httpx.AsyncClient(timeout=30) as http:
                if not a.no_reset:
                    (await http.post(f"{erp}/api/admin/reset")).raise_for_status()
                for method, path, body in s.setup:
                    (await http.request(method, f"{erp}{path}", json=body, headers={"x-actor": "user"})).raise_for_status()
            recs.append(await run_one(s, r + 1, sem, models))
    with (out / "calls.jsonl").open("w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    text = report(list(recs), models, out)
    print("\n" + "\n".join(text.split("\n## Calls")[0].splitlines()[2:]))
    print(f"\nreport: {out / 'report.md'}")
    verdicts = [v for r in recs for t in r.get("trace", []) for v in t.get("guard", [])]
    if verdicts:
        checked = [v for v in verdicts if v.get("status") != "none"]
        ms = sorted(v["ms"] for v in checked if v.get("ms"))
        print(f"Garde-fou : {len(verdicts)} phrases, {len(checked)} vérifiées par Jev — "
              + ", ".join(f"{k} {sum(v.get('status') == k for v in checked)}" for k in ("verified", "unverified", "contradicted", "unchecked"))
              + (f" · Jev médian {ms[len(ms) // 2]} ms" if ms else ""))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())

"""
CallSession: one phone call, run by a small team of agents around the voice agent.

    caller speaks ──► Éclaireur (scout) reads the live transcript, fetches client / stock / prices / alerts in parallel
    caller stops  ──► Réflexe (fast path) answers a plain « oui » / « au revoir » itself (no big model)
                  └─► otherwise: the voice agent gets the caller's words + what the scout found (no tool rounds needed)
                      + a filler line is played if it still has to work (« Je regarde. ») so the line is never silent
    after a turn  ──► Greffier (scribe) writes complaints / durable facts in the background
    language, tu / vous ──► Linguiste: the agent answers in the caller's language and register, the TTS follows
    hang up       ──► Rapporteur: the call report (reporter.py)

`turn(heard)` is an async generator of the kit's ChatStreamEvent (text, tool_call, tool_result, done, error) plus
FillerEvent / FastEvent. It is what WS /voice and the simulator (sim/) both run.
"""

import asyncio
import os
import random
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass

from app.agent import run_agent, sanitize_history
from app.agent.caller import CallerProfile, load_caller
from app.agent.prompts import build_system_prompt
from app.clients import LLMClient, LLMRequest, LLMUsage, get_llm_client
from app.clients.anthropic import AnthropicLLM
from app.schemas import ChatMessage, DoneEvent, ErrorEvent, TextEvent, ToolCallEvent, ToolResultEvent, Usage
from app.tools import all_tools

from .fastpath import note_agent_turn, try_fast
from .scout import LANG_NAMES, Scout
from .scribe import Scribe
from .state import CallState

SCRIBE_TOOLS = {"log_client_event", "save_context"}  # the scribe writes; the voice agent keeps talking

FILLERS = {
    "fr": ["Je regarde.", "Un instant.", "Je vérifie ça.", "Je regarde ça tout de suite."],
    "en": ["Let me check.", "One moment.", "Let me look that up."],
    "es": ["Un momento.", "Lo miro."],
    "ar": ["لحظة من فضلك."],
}

TEAM_RULES = """[Notes internes de l'équipe — pas dites par le client, ne les lis jamais à voix haute]
- Des agents travaillent avec toi pendant l'appel. L'Éclaireur charge dans l'ERP ce que le client mentionne pendant qu'il parle : quand une donnée est listée dans ces notes, utilise-la directement (ids compris) au lieu de rappeler l'outil ; appelle les outils seulement pour ce qui manque ou pour écrire.
- Le Greffier tient la fiche client : log_client_event et save_context répondent tout de suite (il écrit en arrière-plan), et il enregistre de lui-même les réclamations, incidents, demandes de geste et faits durables que tu n'as pas notés. Tu peux dire au client que c'est noté et transmis.
- Une phrase d'attente est jouée automatiquement quand tu dois chercher : ne commence pas par « je regarde »."""


@dataclass
class FillerEvent:
    text: str


@dataclass
class FastEvent:
    kind: str  # confirm | goodbye
    text: str


class CallSession:
    def __init__(
        self,
        actor_name: str | None = None,
        llm: LLMClient | None = None,
        call_id: str | None = None,
        filler_after_ms: int = int(os.environ.get("ORCH_FILLER_MS", 700)),
        runner=run_agent,  # the agent loop (tests replace it)
        tools: list | None = None,  # default: all the kit's tools
    ):
        self.llm = llm or get_llm_client()
        self.runner = runner
        # The side agents use Claude (Haiku / Opus). Another provider, a test double or ORCHESTRA=0: the plain voice agent.
        self.side = isinstance(self.llm, AnthropicLLM) and os.environ.get("ORCHESTRA", "1") != "0"
        self.state = CallState(actor_name=actor_name)
        if call_id:
            self.state.id = call_id
        tools = tools if tools is not None else all_tools()
        self.tool_map = {t.name: t for t in tools}
        self.scout = Scout(self.state, self.tool_map)
        self.scribe = Scribe(self.state, self.tool_map, actor_name)
        # The voice agent keeps its writing tools (the prompt and its instincts expect them), but in the orchestra they
        # return at once and the Greffier does the ERP write in the background: no round trip of silence on the phone.
        self.tools = [self.scribe.handoff_tool(t) if t.name in SCRIBE_TOOLS else t for t in tools] if self.side else tools
        self.history: list[ChatMessage] = []
        self.filler_after_ms = filler_after_ms
        self.speaking = False
        self.cur: dict | None = None
        self.profile_said = ("fr", "vous")
        self.rules_said = False
        self.finished = False

    # ── while the caller speaks ──

    def speech_start(self) -> None:
        if self.speaking:
            return
        self.speaking = True
        self.state.turn_no += 1
        self.cur = {"n": self.state.turn_no, "speechStartMs": self.state.now()}
        self.scout.new_utterance()

    def on_partial(self, text: str) -> None:
        self.speech_start()
        if self.side:
            self.scout.feed(text)

    def speech_end(self) -> None:
        if self.cur is not None:
            self.cur.setdefault("speechEndMs", self.state.now())

    async def warm(self) -> None:
        """Call start: write the system prompt + tools into the provider's prompt cache, so the first real turn reads it."""
        st = self.state
        if not self.side:
            return
        try:
            async with st.span("voice", "préchauffage"):
                system = await build_system_prompt("voice", st.actor_name)
                req = LLMRequest(system=system, messages=[ChatMessage(role="user", content="(test de ligne, réponds juste « ok »)")], tools=[t.info for t in self.tools], max_tokens=64)
                async for ev in self.llm.stream(req):
                    if isinstance(ev, LLMUsage):
                        st.add_usage("warmup", self.llm.model, ev)
        except Exception as e:
            print(f"[orchestra] warm-up: {type(e).__name__}: {e}", flush=True)

    # ── one turn ──

    def caller(self) -> CallerProfile | None:
        """The identified client's card (agent/caller.py). When the card says tu or vous, that register is locked:
        the Linguiste no longer changes it from how one sentence sounded."""
        st = self.state
        card = load_caller(st.client["id"]) if st.client and st.client.get("id") else None
        if card and card.tu is not None and not st.register_locked:
            st.register, st.register_locked = ("tu" if card.tu else "vous"), True
        return card

    def briefing(self) -> str:
        st, lines = self.state, []
        for k in [k for k in st.facts if k not in st.given]:
            lines.append(f"- {st.facts[k]}")
            st.given.add(k)
        if (st.language, st.register) != self.profile_said:
            self.profile_said = (st.language, st.register)
            lang = LANG_NAMES.get(st.language, st.language)
            if st.language != "fr":
                lines.append(f"- Linguiste : l'appelant parle {lang}. Réponds en {lang}, mêmes règles (phrases courtes, pas d'ids, nombres en toutes lettres).")
            if not st.register_locked:  # a register from the client card is restated every turn, just below
                lines.append("- Linguiste : l'appelant te tutoie, tutoie-le aussi." if st.register == "tu" else "- Linguiste : l'appelant vouvoie, vouvoie-le.")
        if st.register_locked:  # every turn: the caller's own « vous avez… » (= the company) pulls the model to vous
            who = (st.client or {}).get("name") or "ce client"
            lines.append(f"- Fiche client ({who}) : {'tutoie' if st.register == 'tu' else 'vouvoie'} la personne dans toute ta réponse"
                         + (", même si elle dit « vous » en parlant de l'entreprise." if st.register == "tu" else "."))  # fmt: skip
        if not lines and self.rules_said:
            return ""
        head = TEAM_RULES if not self.rules_said else "[Notes internes — pas dites par le client]"
        self.rules_said = True
        return "\n".join([head, *lines])

    def filler(self) -> str:
        return random.choice(FILLERS.get(self.state.language) or FILLERS["fr"])

    async def turn(self, heard: str) -> AsyncIterator:
        st = self.state
        self.speech_start()
        self.speech_end()
        self.speaking = False
        cur = self.cur
        cur.update(caller=heard, path="llm", tools=[], prefetched=[])
        st.turns.append(cur)
        t_end = cur["speechEndMs"]

        # 1. Réflexe: a plain « oui » to a read-back order, a goodbye
        fast = await try_fast(st, self.tool_map, heard, st.actor_name) if self.side else None
        if fast:
            cur.update(path="fast", agent=fast.text, firstSoundMs=st.now() - t_end, firstWordMs=st.now() - t_end, kind=fast.kind)
            self.history = trim([*self.history, ChatMessage(role="user", content=f"{heard}\n\n{fast.note}"), ChatMessage(role="assistant", content=fast.text)])
            yield FastEvent(fast.kind, fast.text)
            yield TextEvent(delta=fast.text)
            yield DoneEvent(message=ChatMessage(role="assistant", content=fast.text), usage=Usage(), ms=st.now() - t_end)
            cur["doneMs"] = st.now() - t_end
            self.scribe.after_turn(heard, fast.text)
            return

        # 2. The line must not stay silent: a filler if nothing is said ~0.7 s after the caller stopped
        q: asyncio.Queue = asyncio.Queue()
        tick = object()

        async def timer() -> None:
            await asyncio.sleep(max(0, self.filler_after_ms - (st.now() - t_end)) / 1000)
            q.put_nowait(tick)

        clock = asyncio.create_task(timer() if self.side else asyncio.sleep(0))

        # 3. Give the scout a moment to land what it was fetching, then brief the voice agent
        before = st.now()
        if self.side:
            try:
                await self.scout.settle(heard)
            except asyncio.CancelledError:  # barge-in before the agent started: what was heard stays known
                clock.cancel()
                self.history = trim([*self.history, ChatMessage(role="user", content=heard)])
                raise
        cur["settleMs"] = st.now() - before
        caller = self.caller()  # the identified client's card: how to talk to them (agent/caller.py)
        given_before = set(st.given)
        note = self.briefing() if self.side else ""
        cur["prefetched"] = [k for k in st.given - given_before if not k.startswith("candidates:")]
        working = [*self.history, ChatMessage(role="user", content=f"{heard}\n\n{note}" if note else heard)]

        async def produce() -> None:
            try:
                style = {"caller_style": caller.prompt()} if caller else {}
                async for ev in self.runner(working, llm=self.llm, tools=self.tools, mode="voice", actor_name=st.actor_name, **style):
                    await q.put(ev)
            finally:
                q.put_nowait(None)

        span = st.span("voice", "réponse", f"« {heard[:60]} »")
        await span.__aenter__()
        producer = asyncio.create_task(produce())
        spoken, step_text, said, filled, committed, calls = "", "", False, False, False, {}
        try:
            while (ev := await q.get()) is not None:
                if ev is tick:
                    if not said and not filled:
                        filled = True
                        cur["filler"] = self.filler()
                        cur["firstSoundMs"] = st.now() - t_end
                        yield FillerEvent(cur["filler"])
                    continue
                match ev:
                    case TextEvent(delta=delta):
                        if not said:
                            said = True
                            cur["firstWordMs"] = st.now() - t_end
                            cur.setdefault("firstSoundMs", cur["firstWordMs"])
                        spoken += delta
                        step_text += delta
                    case ToolCallEvent(call=call):
                        calls[call.id] = call
                        cur["tools"].append(call.name)
                        if not said and not filled:
                            filled = True
                            cur["filler"] = self.filler()
                            cur["firstSoundMs"] = st.now() - t_end
                            yield FillerEvent(cur["filler"])
                    case ToolResultEvent(call_id=cid, name=name, ok=ok, ms=ms, result=result):
                        step_text = ""
                        call = calls.get(cid)
                        st.see_tool("voice", name, call.arguments if call else {}, ok, ms, result)
                    case DoneEvent(usage=usage):
                        if usage:
                            st.add_usage("voice", self.llm.model, usage)
                    case ErrorEvent(message=message):
                        cur["error"] = message
                yield ev
            self.history = trim(working)
            committed = True
            cur.update(agent=spoken.strip(), doneMs=st.now() - t_end)
            note_agent_turn(st, spoken)
            if self.side:
                self.scribe.after_turn(heard, spoken.strip())
        finally:
            producer.cancel()
            clock.cancel()
            with suppress(Exception):
                await span.__aexit__(None, None, None)
            if not committed:  # interrupted (barge-in): keep what was heard, done and started to be said
                kept = working
                if step_text.strip() and not (kept[-1].role == "assistant" and kept[-1].tool_calls):
                    kept = [*kept, ChatMessage(role="assistant", content=f"{step_text.strip()} …")]
                self.history = trim(kept)
                cur.update(agent=spoken.strip(), interrupted=True, doneMs=st.now() - t_end)

    # ── end of call ──

    async def finish(self, report: bool = True) -> dict | None:
        if self.finished:
            return None
        self.finished = True
        await self.scout.close()
        await self.scribe.flush()
        if not report or not self.state.turns:
            return None
        from .reporter import build_report

        return await build_report(self)


def trim(history: list[ChatMessage], max_messages: int = 40) -> list[ChatMessage]:
    """Keep the last ~40 messages, starting on a user message (never split a tool call from its result)."""
    history = sanitize_history(history)
    if len(history) <= max_messages:
        return history
    start = next((i for i, m in enumerate(history) if i >= len(history) - max_messages and m.role == "user"), -1)
    return history[start:] if start > 0 else history

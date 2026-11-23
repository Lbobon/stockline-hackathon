"""
The agent loop — the heart of the service.

    system prompt + conversation ──► model ──► text? ──────────────► stream it ──► done
                                       │
                                       └► tool calls ─► run them (ERP API) ─► append results ─► model again …

`run_agent` is an async generator of ChatStreamEvent: exactly the events POST /chat streams.
It APPENDS the new assistant / tool messages to `messages`, so a caller that keeps that list
(voice session, CLI) keeps the full history, including the ids returned by tools.

Interrupting: cancel the task that iterates it (asyncio). Nothing is yielded after a cancellation.
"""

import asyncio
import json
import time
import traceback
from collections.abc import AsyncIterator

from app.clients import LLMClient, LLMRequest, LLMText, LLMToolCall, LLMUsage
from app.config import settings
from app.schemas import (
    ChatMessage,
    ChatMode,
    ChatStreamEvent,
    DoneEvent,
    ErrorEvent,
    TextEvent,
    ToolCall,
    ToolCallEvent,
    ToolResultEvent,
    Usage,
)
from app.tools import ErpClient, Tool, ToolContext

from .prompts import build_system_prompt

GIVE_UP = "Désolé, je n'ai pas réussi à aller au bout. Pouvez-vous reformuler ?"


async def run_agent(
    messages: list[ChatMessage],  # conversation so far, ending with the user's message (no system message)
    *,
    llm: LLMClient,
    tools: list[Tool],
    mode: ChatMode = "chat",  # command = single turn, act-first
    actor_name: str | None = None,
    max_steps: int | None = None,
    caller_style: str | None = None,  # voice: how to talk to the identified caller (agent/caller.py)
) -> AsyncIterator[ChatStreamEvent]:
    started = time.perf_counter()
    actor = "ai_chat" if mode == "chat" else "voice_agent"
    ctx = ToolContext(erp=ErpClient(actor, actor_name), actor=actor)
    usage = Usage(input_tokens=0, output_tokens=0)

    try:
        system = await build_system_prompt(mode, actor_name)
        if caller_style:  # last, so the cached prefix stays the same
            system = f"{system}\n\n{caller_style}"
        request = LLMRequest(system=system, messages=messages, tools=[t.info for t in tools])
        said_something = False

        for _step in range(max_steps or settings.max_steps):
            # 1. Ask the model. Stream the text as it comes; collect the tool calls.
            for attempt in range(2):  # a stream the provider drops before saying anything is retried once
                text = ""
                calls: list[ToolCall] = []
                try:
                    async for ev in _events(llm.stream(request), usage):
                        if isinstance(ev, TextEvent):
                            shown = f" {ev.delta}" if said_something and not text and not ev.delta[:1].isspace() else ev.delta
                            text += ev.delta
                            yield TextEvent(delta=shown)
                        else:
                            calls.append(ev.call)
                            yield ev
                    break
                except Exception as e:
                    if attempt or text or calls:  # something was already said or asked: retrying would repeat it
                        raise
                    print(f"[agent] flux LLM coupé avant la réponse ({type(e).__name__}) : nouvel essai", flush=True)
                    await asyncio.sleep(0.2)
            said_something = said_something or bool(text)

            # 2. No tool call → that was the final answer.
            if not calls:
                message = ChatMessage(role="assistant", content=text)
                messages.append(message)
                yield DoneEvent(message=message, usage=usage, ms=ms_since(started))
                return

            # 3. Run the tools (in parallel), append the results, loop.
            messages.append(ChatMessage(role="assistant", content=text, tool_calls=calls))
            results = await asyncio.gather(*(execute_tool(call, tools, ctx) for call in calls))
            for r in results:
                messages.append(ChatMessage(role="tool", tool_call_id=r.call_id, name=r.name, content=to_json(r.result)))
                yield r

        # Safety net: the model kept calling tools.
        message = ChatMessage(role="assistant", content=GIVE_UP)
        messages.append(message)
        yield TextEvent(delta=GIVE_UP)
        yield DoneEvent(message=message, usage=usage, ms=ms_since(started))
    except Exception as e:  # provider down, bad key… (a cancellation is not an Exception: it just stops us)
        print("[agent] erreur pendant le tour :", flush=True)
        traceback.print_exc()
        yield ErrorEvent(message=str(e) or type(e).__name__)


async def _events(stream: AsyncIterator, usage: Usage) -> AsyncIterator[TextEvent | ToolCallEvent]:
    """One model call as loop events: text and tool calls go out, usage is added up."""
    async for ev in stream:
        match ev:
            case LLMText(delta=delta):
                yield TextEvent(delta=delta)
            case LLMToolCall(call=call):
                yield ToolCallEvent(call=call)
            case LLMUsage(input_tokens=i, output_tokens=o, cache_creation_input_tokens=cw, cache_read_input_tokens=cr):
                usage.input_tokens += i or 0
                usage.output_tokens += o or 0
                if cw is not None:
                    usage.cache_creation_input_tokens = (usage.cache_creation_input_tokens or 0) + cw
                if cr is not None:
                    usage.cache_read_input_tokens = (usage.cache_read_input_tokens or 0) + cr


async def execute_tool(call: ToolCall, tools: list[Tool], ctx: ToolContext) -> ToolResultEvent:
    """Run one tool call. Never raises: errors become `{"error": ...}` so the model can explain or retry."""
    started = time.perf_counter()
    tool = next((t for t in tools if t.name == call.name), None)
    try:
        if not tool:
            raise LookupError(f"Outil inconnu : {call.name}")
        result = await tool.run(call.arguments, ctx)
        try:  # a value the model can't read back (datetime, set, object…) is the tool's error, not the turn's
            to_json(result)
        except (TypeError, ValueError) as e:
            raise TypeError(f"résultat de {call.name} non sérialisable en JSON : {e}") from e
        ok = True
    except Exception as e:
        print(f"[tool {call.name}] erreur :", flush=True)
        traceback.print_exc()
        ok, result = False, {"error": str(e) or type(e).__name__}
    return ToolResultEvent(call_id=call.id, name=call.name, ok=ok, result=result, ms=ms_since(started))


def ms_since(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000 + 0.5)


def to_json(value) -> str:
    """Compact JSON, accents kept (what the model reads back)."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

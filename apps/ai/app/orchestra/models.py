"""The side agents' models: small and fast (Haiku) for the scout / scribe, a strong one for the report.

ORCH_FAST_MODEL (default claude-haiku-5-5) and ORCH_REPORT_MODEL (default claude-opus-5-5) — same Anthropic key as the
agent. With another LLM provider every side agent falls back to the main model.
"""

import json
import os
import re
from functools import cache

from app.clients import LLMClient, LLMRequest, LLMText, LLMUsage, get_llm_client
from app.clients.anthropic import AnthropicLLM
from app.clients.factory import make_llm_client
from app.config import LLMSettings, settings
from app.schemas import ChatMessage

from .state import CallState


class NoThinkingLLM(AnthropicLLM):
    """Haiku without thinking: a JSON extraction in ≈0.7 s instead of ≈1.9 s (the thinking ate the first 1.6 s)."""

    def build_body(self, req: LLMRequest) -> dict:
        return {**super().build_body(req), "thinking": {"type": "disabled"}}


@cache
def side_llm(kind: str) -> LLMClient:
    if settings.llm.provider != "anthropic":
        return get_llm_client()
    model, effort = {
        "fast": (os.environ.get("ORCH_FAST_MODEL", "claude-haiku-5-5"), ""),
        "report": (os.environ.get("ORCH_REPORT_MODEL", "claude-opus-5-5"), "low"),
    }[kind]
    if kind == "fast":
        return NoThinkingLLM(model, settings.llm.api_key, settings.llm.base_url or "https://api.anthropic.com", None)
    return make_llm_client(LLMSettings(provider="anthropic", model=model, api_key=settings.llm.api_key, base_url=settings.llm.base_url, effort=effort))


async def ask_json(state: CallState, agent: str, kind: str, system: str, prompt: str, max_tokens: int = 1200) -> dict | None:
    """One model call that must answer a JSON object. None if it did not (side agents fail quietly)."""
    llm = side_llm(kind)
    text = ""
    async for ev in llm.stream(LLMRequest(system=system, messages=[ChatMessage(role="user", content=prompt)], max_tokens=max_tokens)):
        if isinstance(ev, LLMText):
            text += ev.delta
        elif isinstance(ev, LLMUsage):
            state.add_usage(agent, llm.model, ev)
    return parse_json(text)


def parse_json(text: str) -> dict | None:
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None
    try:
        out = json.loads(m[0])
    except ValueError:
        return None
    return out if isinstance(out, dict) else None

"""Réflexe (orchestra/fastpath.py): a plain « oui » to the read-back goes through finalize_order (the ERP's gates), never
confirm_order; a draft is handed to the big model with the reasons. Greffier sees those outcomes."""

import asyncio
import dataclasses

from app.orchestra.fastpath import try_fast
from app.orchestra.scribe import Scribe
from app.orchestra.state import CallState
from app.tools import TOOLS


class FakeTool:
    def __init__(self, result):
        self.result, self.calls = result, []

    async def run(self, args, ctx):
        self.calls.append(args)
        return self.result


def order(status: str) -> dict:
    return {"id": "ord_1", "number": "CMD-1", "status": status, "deliveryDate": "2026-11-26", "totalHt": 412.0, "lines": [], "warnings": []}


def state() -> CallState:
    st = CallState(client={"id": "cli_0001", "name": "Test"}, order=order("draft"), awaiting_yes="ord_1", turn_no=2)
    st.turns = [{"n": 1, "agent": "Je vous la confirme ?"}, {"n": 2}]
    return st


def run(decision: str, status: str, reasons=()):
    st = state()
    finalize = FakeTool({"decision": decision, "reasons": list(reasons), "order": order(status)})
    confirm = FakeTool(order("confirmed"))
    out = asyncio.run(try_fast(st, {"finalize_order": finalize, "confirm_order": confirm}, "oui c'est bon", None))
    return st, out, finalize, confirm


def test_yes_goes_through_finalize_not_confirm():
    st, out, finalize, confirm = run("confirm", "confirmed")
    assert finalize.calls == [{"orderId": "ord_1", "recapConfirmed": True}]
    assert confirm.calls == []
    assert out.kind == "confirm" and out.text.startswith("C'est confirmé")
    assert st.order["status"] == "confirmed"  # the shared view unwraps {decision, reasons, order}


def test_draft_is_finalized_then_explained_by_the_big_model():
    st, out, finalize, _ = run("draft", "draft", ["Encours dépassé"])
    assert out is None and len(finalize.calls) == 1  # finalized once; the voice agent explains it with tact
    assert "Encours dépassé" in st.facts["fast:ord_1"] and "refinalise pas" in st.facts["fast:ord_1"]


def test_error_hands_over_to_the_big_model():
    st = state()
    out = asyncio.run(try_fast(st, {"finalize_order": FakeTool({"error": "x"})}, "oui", None))
    assert out is None and "fast:ord_1" in st.facts


def test_scribe_sees_retention_outcome():
    st = state()
    st.tool_calls.append({"agent": "voice", "name": "retention_offer", "args": {}, "ok": True, "ms": 5, "turn": 2,
                          "result": {"status": "needs_approval", "pct": 5, "amountHt": 12.5, "offers": []}})  # fmt: skip
    line = Scribe(st, {}, None).outcomes()
    assert "retention_offer" in line and "needs_approval" in line


def test_voice_agent_log_is_handed_to_the_scribe():
    async def go():
        st = state()
        real = FakeTool({"id": "cev_1"})
        real.name = "log_client_event"
        scribe = Scribe(st, {"log_client_event": real}, None)
        stub = scribe.handoff_tool(dataclasses.replace(TOOLS["log_client_event"]))  # real name and schema
        out = await stub.fn(None, client_id="cli_0001", type="complaint", title="Merguez trop grasses", description=None)
        await asyncio.gather(*scribe.tasks)
        return out, real.calls, scribe.written

    out, calls, written = asyncio.run(go())
    assert out["ok"] is True
    assert calls == [{"client_id": "cli_0001", "type": "complaint", "title": "Merguez trop grasses"}]
    assert written == ["événement complaint : Merguez trop grasses"]

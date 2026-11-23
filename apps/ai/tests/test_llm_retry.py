"""A provider stream dropped before the model said anything is retried once; after text, it is not (no repeats)."""

from app.agent import run_agent
from app.clients import LLMClient, LLMRequest, LLMText
from app.schemas import ChatMessage


class Flaky(LLMClient):
    provider, model = "flaky", "flaky"

    def __init__(self, fail_before_text: int = 0, fail_after_text: bool = False):
        self.calls, self.fail_before_text, self.fail_after_text = 0, fail_before_text, fail_after_text

    async def stream(self, request: LLMRequest):
        self.calls += 1
        if self.calls <= self.fail_before_text:
            raise ConnectionError("peer closed connection")
        yield LLMText("Bonjour, ")
        if self.fail_after_text:
            raise ConnectionError("peer closed connection")
        yield LLMText("je regarde.")


async def run(llm):
    return [ev async for ev in run_agent([ChatMessage(role="user", content="allô")], llm=llm, tools=[])]


async def test_drop_before_any_text_is_retried_once():
    llm = Flaky(fail_before_text=1)
    evs = await run(llm)
    assert llm.calls == 2
    assert type(evs[-1]).__name__ == "DoneEvent" and evs[-1].message.content == "Bonjour, je regarde."


async def test_two_drops_in_a_row_give_up():
    evs = await run(Flaky(fail_before_text=2))
    assert type(evs[-1]).__name__ == "ErrorEvent"


async def test_drop_after_text_is_not_retried():
    llm = Flaky(fail_after_text=True)
    evs = await run(llm)
    assert llm.calls == 1 and type(evs[-1]).__name__ == "ErrorEvent"

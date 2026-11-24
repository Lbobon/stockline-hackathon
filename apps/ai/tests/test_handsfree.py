"""
Hands-free turns (services/handsfree.py + voice_session): a scripted live STT drives one VoiceConnection.
Drafts kept or dropped at the caller's pause, the rollback, barge-in vs the agent's own echo, the ERP write gate.
"""

import asyncio
import json

import httpx
import pytest

from app.clients.base import STTUpdate
from app.clients.mock_speech import MockTTS
from app.services import handsfree, voice_session
from app.tools.erp import ErpClient, ErpError, Gate, draft_gate


class ScriptedSTT:
    """A live STT whose updates the test pushes: say("words", final=True, end=True)."""

    name = "mock"

    def __init__(self):
        self.updates: asyncio.Queue[STTUpdate] = asyncio.Queue()
        self.final = ""

    def say(self, words: str, end: bool = False, partial: str = "") -> None:
        self.final = f"{self.final} {words}".strip() if words else self.final
        text = f"{self.final} {partial}".strip()
        self.updates.put_nowait(STTUpdate(text=text, stable=self.final, endpoint=end))

    async def transcribe_stream(self, audio, mime, hint=None):
        while True:
            yield await self.updates.get()


class FakeWS:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))

    async def send_bytes(self, data: bytes) -> None:
        pass

    def kinds(self) -> list[str]:
        return [m["type"] for m in self.sent]


class FakeLLM:
    provider, model = "mock", "mock"


async def slow_agent(messages, **kwargs):
    from app.schemas import ChatMessage, DoneEvent, TextEvent

    heard = messages[-1].content
    reply = f"Pour « {heard} », je vous mets ça de côté. Autre chose ?"
    for word in reply.split(" "):
        await asyncio.sleep(0.03)
        yield TextEvent(delta=word + " ")
    messages.append(ChatMessage(role="assistant", content=reply))
    yield DoneEvent(message=ChatMessage(role="assistant", content=reply), ms=1)


@pytest.fixture
def conn(monkeypatch):
    stt = ScriptedSTT()
    monkeypatch.setattr(voice_session, "get_stt_client", lambda: stt)
    monkeypatch.setattr(voice_session, "get_tts_client", lambda: MockTTS())
    monkeypatch.setattr(voice_session, "get_llm_client", lambda: FakeLLM())
    monkeypatch.setattr(voice_session, "run_agent", slow_agent)
    monkeypatch.setattr(voice_session, "MAX_WAIT_S", 0.2)
    monkeypatch.setattr(handsfree, "SILENCE_S", 30)  # endpoints come from the script only
    monkeypatch.setattr(handsfree.jev, "available", lambda: False)  # offline: the trailing-word rule decides
    c = voice_session.VoiceConnection(FakeWS())  # type: ignore[arg-type]
    c.stt = stt
    return c


async def settle(c, s: float = 0.05):
    await asyncio.sleep(s)
    if c.turn:
        await asyncio.wait_for(c.turn, 5)


async def start(c):
    await c.handle_text(json.dumps({"type": "start"}))
    await c.handle_text(json.dumps({"type": "audio_start", "mime": "audio/webm", "continuous": True}))
    assert c.listener is not None


def history(c) -> list[tuple[str, str]]:
    return [(m.role, m.content) for m in c.conversation.call.history]


async def test_a_finished_sentence_is_answered_at_the_pause(conn):
    await start(conn)
    conn.stt.say("", partial="Bonjour je")
    conn.stt.say("Bonjour, il me faut deux caisses de tomates.", end=True)
    await settle(conn)
    finals = [m["text"] for m in conn.ws.sent if m["type"] == "transcript" and m["final"]]
    assert finals == ["Bonjour, il me faut deux caisses de tomates."]
    assert conn.ws.kinds()[-1] == "agent_done" and conn.draft is None
    # the next sentence starts after the answered words
    conn.stt.say("Merci.", end=True)
    await settle(conn)
    assert [m["text"] for m in conn.ws.sent if m["type"] == "transcript" and m["final"]][-1] == "Merci."
    assert [r for r, _ in history(conn)] == ["user", "assistant", "user", "assistant"]


async def test_a_pause_mid_sentence_drafts_then_drops_when_they_go_on(conn):
    await start(conn)
    conn.stt.say("Je voudrais du", end=True)  # « du »: not finished, the draft waits MAX_WAIT_S
    await asyncio.sleep(0.05)
    assert conn.draft is not None
    sent_before = len(conn.ws.sent)
    assert not any(m["type"] in ("agent_text", "agent_done") for m in conn.ws.sent)  # held by the gate
    conn.stt.say("saumon fumé.", end=True)  # they go on: the draft is dropped, the whole sentence is the turn
    await settle(conn, 0.3)
    after = conn.ws.sent[sent_before:]
    finals = [m["text"] for m in after if m["type"] == "transcript" and m["final"]]
    assert finals == ["Je voudrais du saumon fumé."]
    assert all("Je voudrais du »" not in m.get("delta", "") for m in conn.ws.sent)
    assert history(conn)[0] == ("user", "Je voudrais du saumon fumé.") and len(history(conn)) == 2
    assert [t["caller"] for t in conn.conversation.call.state.turns] == ["Je voudrais du saumon fumé."]


async def test_an_unfinished_sentence_is_answered_after_the_wait(conn):
    await start(conn)
    conn.stt.say("Je voudrais du", end=True)
    await settle(conn, 0.4)  # MAX_WAIT_S (0.2 here) without new words: answer anyway
    assert "agent_done" in conn.ws.kinds()
    assert history(conn)[0] == ("user", "Je voudrais du")


async def test_talking_over_the_agent_cuts_it_but_not_its_echo_or_a_oui(conn):
    await start(conn)
    conn.stt.say("Il me faut des tomates.", end=True)
    await asyncio.sleep(0.15)  # kept, the agent is talking
    assert conn.turn and not conn.turn.done()
    conn.stt.say("", partial="pour il me")  # its own voice in the mic
    conn.stt.say("", partial="oui oui")  # listening
    await asyncio.sleep(0.05)
    assert "agent_audio_cut" not in conn.ws.kinds() and not conn.turn.done()
    conn.stt.say("", partial="non attendez")
    await asyncio.sleep(0.05)
    assert conn.ws.kinds().count("agent_audio_cut") == 1
    assert conn.turn is None or conn.turn.done()


async def test_audio_end_closes_the_mic_and_hangup_stops_listening(conn):
    await start(conn)
    await conn.handle_text(json.dumps({"type": "hangup"}))
    assert conn.listener is None


def test_helpers():
    assert handsfree.trailing_open("je voudrais du") and handsfree.trailing_open("et puis euh")
    assert not handsfree.trailing_open("deux caisses de tomates.")
    assert handsfree.backchannel("Oui oui.") and not handsfree.backchannel("non attendez")
    assert handsfree.echo_of("deux caisses", "Je vous mets deux caisses de tomates.")
    assert not handsfree.echo_of("non pas deux", "Je vous mets deux caisses.")


async def test_is_complete_without_jev(monkeypatch):
    monkeypatch.setattr(handsfree.jev, "available", lambda: False)
    assert await handsfree.is_complete("Il vous reste du bar ?")
    assert await handsfree.is_complete("Deux caisses de tomates")
    assert not await handsfree.is_complete("Deux caisses de")


async def test_a_drafted_turn_reads_but_its_writes_wait_for_the_gate():
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req.method)
        return httpx.Response(200, json={"ok": True})

    erp = ErpClient("voice_agent", base_url="http://erp", http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    async def turn(gate):
        draft_gate.set(gate)
        await erp.get("/api/meta")
        return await erp.patch("/api/orders/o1", {"notes": "x"})

    kept = Gate()
    task = asyncio.create_task(turn(kept))
    await asyncio.sleep(0.05)
    assert seen == ["GET"]  # read now, write held
    kept.open()
    assert await task == {"ok": True} and seen == ["GET", "PATCH"]

    dropped = Gate()
    task = asyncio.create_task(turn(dropped))
    await asyncio.sleep(0.05)
    dropped.drop()
    with pytest.raises(ErpError):
        await task
    assert seen == ["GET", "PATCH", "GET"]  # the dropped draft never wrote


# ── word-by-word TTS (ElevenLabs stream-input) ──


async def test_elevenlabs_stream_input_against_a_fake_server():
    import base64

    from websockets.asyncio.server import serve

    from app.clients.elevenlabs import ElevenLabsTTS

    got: list[dict] = []

    async def handler(ws):
        got.append({"path": ws.request.path, "key": ws.request.headers.get("xi-api-key")})
        async for raw in ws:
            m = json.loads(raw)
            got.append(m)
            if m.get("flush") or m["text"] == "":
                await ws.send(json.dumps({"audio": base64.b64encode(b"mp3:" + str(len(got)).encode()).decode()}))
            if m["text"] == "":
                await ws.send(json.dumps({"isFinal": True}))

    async with serve(handler, "127.0.0.1", 0) as server:
        tts = ElevenLabsTTS("k", voice="v1")
        tts.ws_base = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
        s = tts.open_stream(stability=0.4)
        s.send("Deux caisses ")
        s.send("de tomates. ")
        s.send("", flush=True)
        s.send("Autre chose ? ")
        s.end()
        audio = [c async for c in s.chunks()]
    assert got[0]["key"] == "k" and got[0]["path"].startswith("/v1/text-to-speech/v1/stream-input?model_id=")
    assert "language_code=fr" in got[0]["path"]
    assert got[1]["text"] == " " and got[1]["voice_settings"] == {"stability": 0.4}
    assert [m["text"] for m in got[2:]] == ["Deux caisses ", "de tomates. ", " ", "Autre chose ? ", ""]
    assert got[4]["flush"] is True and len(audio) == 2


class FakeStream:
    mime = "audio/mpeg"

    def __init__(self):
        self.sent: list[str] = []
        self.out: asyncio.Queue = asyncio.Queue()
        self.cancelled = False

    def send(self, text, flush=False):
        self.sent.append("<flush>" if flush else text)
        if flush:
            self.out.put_nowait(b"audio")

    def end(self):
        self.sent.append("<end>")
        self.out.put_nowait(None)

    def cancel(self):
        self.cancelled = True
        self.out.put_nowait(None)

    async def chunks(self):
        while (c := await self.out.get()) is not None:
            yield c


class StreamTTS(MockTTS):
    def open_stream(self, voice=None, stability=None):
        self.stream = FakeStream()
        return self.stream


async def test_the_speaker_forwards_words_before_the_sentence_ends():
    ws = FakeWS()
    sent: list = []

    async def send(msg):
        sent.append(msg.type)

    async def send_bytes(b):
        sent.append(b)

    tts = StreamTTS()
    sp = voice_session.Speaker(tts, send, send_bytes)
    sp.play(("audio/mpeg", b"filler"))  # a cached « Je regarde. » first
    sp.push("Il vou")
    assert tts.stream.sent == ["Il "]  # whole words only: « vou » is still being written
    sp.push("s reste du bar. Autre")
    assert tts.stream.sent == ["Il ", "vous reste du bar. ", "<flush>"]
    sp.push(" chose ?")
    await sp.finish()
    assert tts.stream.sent[-4:] == ["Autre chose ", "?", "<flush>", "<end>"]
    assert sent[:3] == ["agent_audio_start", b"filler", b"audio"] and sent[-1] == "agent_audio_end"
    assert ws.sent == []


async def test_a_blocking_guard_keeps_whole_sentences():
    class Blocking:
        blocking, enabled = True, False

    tts = StreamTTS()
    sp = voice_session.Speaker(tts, lambda m: asyncio.sleep(0), lambda b: asyncio.sleep(0), guard=Blocking())  # type: ignore[arg-type]
    assert sp.stream is None and not hasattr(tts, "stream")


async def test_the_same_sentence_twice_is_two_turns(conn):
    await start(conn)
    conn.stt.say("Merci.", end=True)
    await settle(conn)
    conn.stt.say("Merci.", end=True)
    await settle(conn)
    assert [m["text"] for m in conn.ws.sent if m["type"] == "transcript" and m["final"]] == ["Merci.", "Merci."]


async def test_a_oui_after_the_agent_finished_is_an_answer(conn):
    await start(conn)
    conn.stt.say("Deux caisses de tomates.", end=True)
    await settle(conn)
    conn.stt.say("Oui.", end=True)  # its audio may still be playing: still the answer to « Autre chose ? »
    await settle(conn)
    assert [m["text"] for m in conn.ws.sent if m["type"] == "transcript" and m["final"]][-1] == "Oui."


async def test_without_an_endpoint_token_a_silence_ends_the_sentence(conn, monkeypatch):
    monkeypatch.setattr(handsfree, "SILENCE_S", 0.2)
    await start(conn)
    conn.stt.say("", partial="Il vous reste du bar ?")  # provisional words only, no <end>
    await settle(conn, 0.6)
    assert [m["text"] for m in conn.ws.sent if m["type"] == "transcript" and m["final"]] == ["Il vous reste du bar ?"]

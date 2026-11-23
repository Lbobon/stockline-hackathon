"""
WS /voice — push-to-talk voice sessions (messages: app/schemas/voice.py).

One turn:
    audio_start … binary frames … audio_end   (or a typed `text` message)
      → STT, live: each frame goes to the provider as it arrives (services/utterance.py);
        "transcript" final:false while the caller speaks, then "transcript" final:true soon after audio_end
      → agent (voice mode) streaming "agent_text" / "agent_tool"
      → each finished sentence goes to TTS right away (sentence by sentence = first audio sooner)
      → "agent_audio_start" {mime}, binary audio chunks…, "agent_audio_end"   (one audio segment per turn)
      → "agent_done" {text, timings}

Barge-in: a new audio_start / text while the agent is still busy cancels the running turn (asyncio
cancellation); what was heard and said so far stays in the history.

The turn runs through the call orchestra (app/orchestra): the Éclaireur reads the live transcript while the caller
speaks and prefetches the data, the Réflexe answers a plain « oui », a cached filler line plays when the agent has to
look something up, the Greffier writes notes in the background, and on hangup the Rapporteur writes the call report.
"""

import asyncio
import copy
import json
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import aclosing, suppress
from dataclasses import dataclass, field

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import TypeAdapter, ValidationError

from app.agent import run_agent
from app.agent.caller import load_caller
from app.agent.loop import ms_since
from app.agent.outbound import shortage_brief
from app.clients import STTUpdate, TTSClient, get_llm_client, get_stt_client, get_tts_client
from app.config import settings
from app.schemas import (
    AgentAudioEndMessage,
    AgentAudioStartMessage,
    AgentDoneMessage,
    AgentTextMessage,
    AgentToolMessage,
    AudioEndMessage,
    AudioStartMessage,
    CallReportMessage,
    ChatMessage,
    ErrorEvent,
    HangupMessage,
    OrchestraMessage,
    ReadyMessage,
    ResetMessage,
    StartMessage,
    TextEvent,
    TextMessage,
    ToolCallEvent,
    ToolResultEvent,
    TranscriptMessage,
    TurnTimings,
    VoiceClientMessage,
    VoiceErrorMessage,
)
from app.orchestra import CallSession, FastEvent, FillerEvent
from app.orchestra import store as call_store
from app.orchestra.session import FILLERS, trim  # noqa: F401  (trim: re-exported for the tests)
from app.schemas.base import WireModel
from app.tools import all_tools

from .utterance import Utterance
from .vocabulary import stt_hint

client_message = TypeAdapter(VoiceClientMessage)


@dataclass
class Conversation:
    id: str
    actor_name: str | None = None
    call: CallSession | None = None  # the call in progress (history, agents, timeline)

    @property
    def history(self) -> list[ChatMessage]:
        return self.call.history if self.call else []


# Filler lines (« Je regarde. ») synthesized once per voice: played instantly, no TTS round trip.
filler_audio: dict[tuple[str, str], tuple[str, bytes]] = {}
background: set[asyncio.Task] = set()


def spawn(coro) -> asyncio.Task:
    t = asyncio.create_task(coro)
    background.add(t)
    t.add_done_callback(background.discard)
    return t


def tts_for(language: str) -> TTSClient:
    """The TTS speaking `language` (ElevenLabs: language_code); the configured one for French or other providers."""
    tts = get_tts_client()
    if language != "fr" and hasattr(tts, "language"):
        tts = copy.copy(tts)
        tts.language = language
    return tts


async def cache_fillers(tts: TTSClient, language: str = "fr") -> None:
    for text in FILLERS.get(language, []):
        if (language, text) in filler_audio:
            continue
        try:
            result = await tts.synthesize(text, settings.tts.voice or None)
            audio = b"".join([c async for c in result.chunks])
            filler_audio[(language, text)] = (result.mime, audio)
        except Exception as e:
            print(f"[voice] filler « {text} » : {e}", flush=True)
            return


# In memory: a reconnect with the same sessionId continues the conversation.
conversations: dict[str, Conversation] = {}


class VoiceConnection:
    """One WebSocket connection. Reads client messages; runs each turn as a background task."""

    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.conversation: Conversation | None = None
        self.recording: Utterance | None = None  # while the key is down
        self.turn: asyncio.Task | None = None
        self.warm_up: asyncio.Task | None = None
        self._send_lock = asyncio.Lock()

    # ── sending ──

    async def send(self, msg: WireModel) -> None:
        await self._send(text=msg.to_json())

    async def send_bytes(self, data: bytes) -> None:
        await self._send(bytes=data)

    async def _send(self, **message) -> None:
        async with self._send_lock:
            with suppress(WebSocketDisconnect, RuntimeError):  # the client is gone: nothing to do
                if message.get("text") is not None:
                    await self.ws.send_text(message["text"])
                else:
                    await self.ws.send_bytes(message["bytes"])

    # ── receiving ──

    async def serve(self) -> None:
        await self.ws.accept()
        try:
            while True:
                frame = await self.ws.receive()
                if frame["type"] == "websocket.disconnect":
                    break
                if frame.get("bytes") is not None:
                    if self.recording:
                        self.recording.feed(frame["bytes"])
                    continue
                await self.handle_text(frame.get("text") or "")
        except WebSocketDisconnect:
            pass
        finally:
            await self.interrupt()
            self.end_call(report=True)  # the line dropped: still report the call

    async def handle_text(self, raw: str) -> None:
        try:
            msg = client_message.validate_python(json.loads(raw))
        except (json.JSONDecodeError, ValidationError):
            await self.send(VoiceErrorMessage(message="invalid JSON message"))
            return
        if not self.conversation and not isinstance(msg, StartMessage):
            await self.open()  # no "start" yet: open a fresh conversation

        match msg:
            case StartMessage(session_id=session_id, actor_name=actor_name, outbound_order_id=outbound_order_id):
                await self.interrupt()
                await self.open(session_id, actor_name)
                if outbound_order_id:  # outbound call: the agent speaks first, from a hidden brief
                    try:
                        self.start_turn(text=await shortage_brief(outbound_order_id), outbound=True)
                    except Exception as e:
                        await self.send(VoiceErrorMessage(message=str(e)))
            case AudioStartMessage(mime=mime, hint=hint):
                await self.interrupt()  # barge-in
                self.conversation.call.speech_start()
                self.recording = Utterance(get_stt_client(), mime, hint, on_partial=self.send_partial)
            case AudioEndMessage():
                if self.recording:
                    utterance, self.recording = self.recording, None
                    self.conversation.call.speech_end()
                    self.start_turn(utterance=utterance)
            case TextMessage(text=text):
                await self.interrupt()
                self.start_turn(text=text)
            case ResetMessage():
                await self.interrupt()
                self.end_call(report=False)
                await self.open(self.conversation.id)
            case HangupMessage():
                await self.interrupt()
                self.end_call(report=True)
                await self.open(self.conversation.id)

    async def send_partial(self, update: STTUpdate) -> None:
        if self.conversation and self.conversation.call:
            self.conversation.call.on_partial(update.text)  # the Éclaireur listens while the caller speaks
        await self.send(TranscriptMessage(text=update.text, final=False, stable=update.stable))

    def new_call(self, conv: Conversation) -> CallSession:
        call = CallSession(actor_name=conv.actor_name, llm=get_llm_client(), runner=run_agent, tools=all_tools())

        async def relay(event: dict) -> None:
            await self.send(OrchestraMessage(event=event))
            if event.get("type") == "call_profile" and event.get("language") != "fr":  # Linguiste: fillers in that language too
                spawn(cache_fillers(tts_for(event["language"]), event["language"]))

        call.state.listeners.append(relay)
        if call.side:
            spawn(call.warm())  # prompt cache written while the line rings
            spawn(cache_fillers(get_tts_client()))
        return call

    def end_call(self, report: bool) -> None:
        """Hang up the conversation's call: the Greffier finishes, the Rapporteur writes the report in the background."""
        conv = self.conversation
        if not conv or not conv.call:
            return
        call, conv.call = conv.call, None
        if not call.state.turns:
            spawn(call.finish(report=False))
            return
        spawn(self.write_report(call, report))

    async def write_report(self, call: CallSession, report: bool) -> None:
        cid = call.state.id
        if report:
            await self.send(CallReportMessage(id=cid, status="writing"))
        try:
            r = await call.finish(report=report)
            if r:
                call_store.save(r)
                await self.send(CallReportMessage(id=cid, status="ready", headline=(r.get("ai") or {}).get("headline")))
        except Exception as e:
            print(f"[voice] report: {type(e).__name__}: {e}", flush=True)
            if report:
                await self.send(CallReportMessage(id=cid, status="error"))

    async def open(self, session_id: str | None = None, actor_name: str | None = None) -> None:
        conv = conversations.get(session_id or "") or Conversation(id=session_id or str(uuid.uuid4()))
        if actor_name:
            conv.actor_name = actor_name
        conversations[conv.id] = self.conversation = conv
        if conv.call is None:
            conv.call = self.new_call(conv)
        llm = get_llm_client()
        await self.send(
            ReadyMessage(session_id=conv.id, stt=get_stt_client().name, tts=get_tts_client().name, llm=f"{llm.provider}:{llm.model}")
        )
        # Fetch the company vocabulary for the STT now, not when the caller first speaks.
        self.warm_up = self.warm_up or asyncio.create_task(stt_hint(get_stt_client().name, None))

    def start_turn(self, **turn_input) -> None:
        self.turn = asyncio.create_task(self.run_turn(self.conversation, **turn_input))

    async def interrupt(self) -> None:
        """Barge-in: drop a recording in progress, stop the running turn and wait until it has cleaned up."""
        if self.recording:
            self.recording.cancel()
            self.recording = None
        if self.turn and not self.turn.done():
            self.turn.cancel()
            with suppress(asyncio.CancelledError):
                await self.turn
        self.turn = None

    # ── one turn ──

    async def run_turn(
        self,
        conv: Conversation,
        text: str | None = None,
        utterance: Utterance | None = None,  # spoken: already being transcribed since audio_start
        outbound: bool = False,  # `text` is the hidden outbound brief: don't show it as the caller's words
    ) -> None:
        t0 = time.perf_counter()  # end of caller speech
        timings = TurnTimings()
        heard, spoken = "", ""
        speaker: Speaker | None = None
        call = conv.call = conv.call or self.new_call(conv)
        try:
            # 1. Speech → text: with a streaming STT only the last words are left to finalize
            if utterance is not None:
                text = await utterance.text()
                timings.stt_ms = ms_since(t0)  # end of speech → final transcript
            heard = (text or "").strip()
            if not heard:
                await self.send(VoiceErrorMessage(message="Rien entendu — réessayez."))
                return
            if not outbound:
                await self.send(TranscriptMessage(text=heard, final=True))

            # 2. The orchestra: fast path, or the voice agent briefed by the scout (history kept by the CallSession,
            #    also on barge-in). The TTS speaks the caller's language.
            card = load_caller(call.state.client["id"]) if call.state.client and call.state.client.get("id") else None
            speaker = Speaker(tts_for(call.state.language), self.send, self.send_bytes, stability=card.stability if card else None)
            llm_start = time.perf_counter()
            async with aclosing(call.turn(heard)) as events:
                async for ev in events:
                    match ev:
                        case FillerEvent(text=filler):
                            cached = filler_audio.get((call.state.language, filler))
                            speaker.play(cached) if cached else speaker.speak(filler)
                            await self.send(OrchestraMessage(event={"type": "filler", "text": filler}))
                        case FastEvent():
                            pass  # its text follows as a TextEvent
                        case TextEvent(delta=delta):
                            if timings.llm_first_token_ms is None:
                                timings.llm_first_token_ms = ms_since(llm_start)
                            spoken += delta
                            await self.send(AgentTextMessage(delta=delta))
                            speaker.push(delta)
                        case ToolCallEvent(call=tc):
                            await self.send(AgentToolMessage(name=tc.name, args=tc.arguments))
                        case ToolResultEvent(name=name, ok=ok, ms=ms):
                            await self.send(AgentToolMessage(name=name, ok=ok, ms=ms))
                        case ErrorEvent(message=message):
                            await self.send(VoiceErrorMessage(message=message))
            timings.llm_total_ms = ms_since(llm_start)
            t = call.state.turns[-1] if call.state.turns else {}
            await self.send(OrchestraMessage(event={"type": "turn", "n": t.get("n"), "path": t.get("path"), "firstSoundMs": t.get("firstSoundMs"),
                                                    "firstWordMs": t.get("firstWordMs"), "prefetched": t.get("prefetched") or []}))  # fmt: skip

            # 3. Speak the last sentence and wait until all the audio is sent
            await speaker.finish()
            timings.tts_first_byte_ms = speaker.tts_first_byte_ms
            if speaker.first_byte_at:
                timings.total_ms = int((speaker.first_byte_at - t0) * 1000 + 0.5)
                if call.state.turns:
                    call.state.turns[-1]["audioMs"] = timings.total_ms
            await self.send(AgentDoneMessage(text=spoken.strip(), timings=timings))

        except asyncio.CancelledError:
            # Interrupted: the CallSession already kept what was heard, done and started to be said (call.turn's finally).
            if speaker:
                await speaker.stop()
            raise
        except Exception as e:
            await self.send(VoiceErrorMessage(message=str(e) or type(e).__name__))
        finally:
            if utterance:
                utterance.cancel()  # interrupted while transcribing: close the provider stream


MARKDOWN = [
    (re.compile(r"^\s*(?:[-*•–]|\d+[.)])\s+", re.M), ""),  # list markers at line start: "- ", "• ", "1. "
    (re.compile(r"^\s*#+\s*", re.M), ""),  # headings
    (re.compile(r"(\*\*|__|\*|`)"), ""),  # bold, italics, code
    (re.compile(r"\s*\n+\s*"), ", "),  # line breaks inside one spoken chunk: a short pause, not "tiret"
]


def speakable(text: str) -> str:
    """What the TTS should hear: the agent's text without markdown (the prompt forbids it, but a bullet list read
    aloud sounds broken). The screen still shows the text as written."""
    for pattern, repl in MARKDOWN:
        text = pattern.sub(repl, text)
    return text.strip(" ,")


class Speaker:
    """Buffers the streamed text, cuts it into sentences and speaks them in order, as one audio segment."""

    # a sentence ends with . ! ? … followed by a space ("18.50" is not a boundary)
    SENTENCE = re.compile(r"^([\s\S]*?[.!?…])\s+")

    def __init__(
        self,
        tts: TTSClient,
        send: Callable[[WireModel], Awaitable],
        send_bytes: Callable[[bytes], Awaitable],
        stability: float | None = None,  # ElevenLabs voice stability for this caller (agent/caller.py)
    ):
        self.tts, self.send, self.send_bytes, self.stability = tts, send, send_bytes, stability
        self.buffer = ""
        self.sentences: asyncio.Queue[str | tuple[str, bytes] | None] = asyncio.Queue()
        self.worker: asyncio.Task | None = None  # speaks the queued sentences one after the other
        self.started = False
        self.first_byte_at: float | None = None
        self.tts_first_byte_ms: int | None = None

    def push(self, delta: str) -> None:
        self.buffer += delta
        while m := self.SENTENCE.match(self.buffer):
            self.speak(m[1])
            self.buffer = self.buffer[m.end() :]

    def speak(self, sentence: str) -> None:
        sentence = speakable(sentence)
        if not sentence.strip():
            return
        if self.worker is None:
            self.worker = asyncio.create_task(self._speak_queued())
        self.sentences.put_nowait(sentence)

    def play(self, audio: tuple[str, bytes]) -> None:
        """Queue pre-synthesized audio (mime, bytes): a cached filler line, no TTS round trip."""
        if self.worker is None:
            self.worker = asyncio.create_task(self._speak_queued())
        self.sentences.put_nowait(audio)

    async def finish(self) -> None:
        """Speak what is left in the buffer, wait for all the audio, close the segment."""
        self.speak(self.buffer)
        self.buffer = ""
        if self.worker:
            self.sentences.put_nowait(None)
            await self.worker
        if self.started:
            await self.send(AgentAudioEndMessage())

    async def stop(self) -> None:
        """Interrupted: drop the queued sentences, close the segment if audio was sent."""
        if self.worker:
            self.worker.cancel()
            with suppress(asyncio.CancelledError):
                await self.worker
        if self.started:
            await self.send(AgentAudioEndMessage())

    async def _speak_queued(self) -> None:
        while (sentence := await self.sentences.get()) is not None:
            tts_start = time.perf_counter()
            if isinstance(sentence, tuple):
                mime, audio = sentence
                if not self.started:
                    self.started = True
                    await self.send(AgentAudioStartMessage(mime=mime))
                    self.first_byte_at = time.perf_counter()
                    self.tts_first_byte_ms = 0
                await self.send_bytes(audio)
                continue
            try:
                if self.stability is not None and self.tts.name == "elevenlabs":
                    result = await self.tts.synthesize(sentence, settings.tts.voice or None, stability=self.stability)  # type: ignore[call-arg]
                else:
                    result = await self.tts.synthesize(sentence, settings.tts.voice or None)
                # aclosing: on barge-in (cancellation) the provider's HTTP stream is closed right away
                async with aclosing(result.chunks) as chunks:  # type: ignore[type-var]
                    async for chunk in chunks:
                        if not self.started:
                            self.started = True
                            await self.send(AgentAudioStartMessage(mime=result.mime))
                            self.first_byte_at = time.perf_counter()
                            self.tts_first_byte_ms = ms_since(tts_start)
                        await self.send_bytes(chunk)
            except Exception as e:
                await self.send(VoiceErrorMessage(message=f"TTS : {e}"))

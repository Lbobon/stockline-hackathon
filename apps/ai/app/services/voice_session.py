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

Hands-free (audio_start {continuous: true}): the mic stays open, one live STT stream for the call (services/handsfree.py).
At each pause the turn starts at once as a draft behind a Gate: it thinks, looks up and synthesizes, but nothing is
sent, said or written until the sentence is judged finished (then the held messages go out in order); new words first
= the draft is dropped and rolled back, the sentence goes on. The caller talking over the agent (not its own echo,
not a « oui oui ») cuts the audio (agent_audio_cut) and cancels the turn.

The turn runs through the call orchestra (app/orchestra): the Éclaireur reads the live transcript while the caller
speaks and prefetches the data, the Réflexe answers a plain « oui », a cached filler line plays when the agent has to
look something up, the Greffier writes notes in the background, and on hangup the Rapporteur writes the call report.
"""

import asyncio
import copy
import json
import os
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import aclosing, suppress
from dataclasses import dataclass, field

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import TypeAdapter, ValidationError

from app.agent import jev, run_agent
from app.agent.caller import load_caller
from app.agent.loop import ms_since
from app.agent.outbound import shortage_brief
from app.clients import STTUpdate, TTSClient, get_llm_client, get_stt_client, get_tts_client
from app.config import settings
from app.guard import Guard, Verdict, until
from app.schemas import (
    AgentAudioCutMessage,
    AgentAudioEndMessage,
    AgentAudioStartMessage,
    AgentDoneMessage,
    AgentTextMessage,
    AgentToolMessage,
    AudioEndMessage,
    AudioStartMessage,
    CallReportMessage,
    ChatMessage,
    CopilotConfirmMessage,
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
from app.orchestra import CallSession, CopilotSession, FastEvent, FillerEvent
from app.orchestra import store as call_store
from app.orchestra.session import FILLERS, trim  # noqa: F401  (trim: re-exported for the tests)
from app.schemas.base import WireModel
from app.tools import all_tools
from app.tools.erp import Gate, draft_gate

from .handsfree import MAX_WAIT_S, Listener, backchannel, echo_of, is_complete, same_words
from .utterance import Utterance
from .vocabulary import stt_hint

client_message = TypeAdapter(VoiceClientMessage)


@dataclass
class Conversation:
    id: str
    actor_name: str | None = None
    call: CallSession | CopilotSession | None = None  # the call in progress (history, agents, timeline)
    mode: str = "agent"  # agent: the voice agent answers · copilot: Leïla speaks, the agents whisper (orchestra/copilot.py)

    @property
    def history(self) -> list[ChatMessage]:
        return self.call.history if isinstance(self.call, CallSession) else []


# « Provoquer une erreur » (/voice, Garde-fou panel): shows the guard catching a wrong figure on demand. The model won't
# lie when asked to, so the demo simulates a hallucination: the first price / quantity of its answer is altered on the fly.
_NUM = re.compile(r"\d+(?:[.,]\d+)?|\b(?:deux|trois|quatre|cinq|six|sept|huit|neuf|dix|onze|douze|treize|quatorze|quinze|seize|vingt|trente|quarante|cinquante|soixante|cent)\b", re.I)


def hallucinate(sentence: str) -> str | None:
    """« … 11 euros 80 le kilo » → « … 8 euros 80 le kilo »: the first number of a sentence that states a price, a stock
    or a quantity, about 30 % lower. None when the sentence has no such figure."""
    from app.guard import claim_kinds
    from app.guard.numbers import numbers_in

    if not {"prix", "stock", "quantité"} & set(claim_kinds(sentence)) or not (m := _NUM.search(sentence)):
        return None
    v = numbers_in(m[0])[0] if numbers_in(m[0]) else float(m[0].replace(",", "."))
    return sentence[: m.start()] + str(max(1, round(v * 0.7))) + sentence[m.end() :]


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


# shortcut: the server sends audio faster than real time and can't see the client's playback; for this long after a turn
# the agent may still be audible, so the caller's words still cut it. Upgrade: the client reports playback end.
PLAYBACK_TAIL_S = 8
HANDSFREE_FILLER_MS = 200  # hands-free: the STT already took ≈0.5 s to show the last word; the cached line comes sooner


@dataclass
class Draft:
    """A turn started at the caller's pause, not yet kept (see the module docstring)."""

    task: asyncio.Task
    gate: Gate
    text: str
    upto: int  # the listener's final transcript up to here is this turn
    history: list
    turns: int
    utterance: tuple  # the call's (turn_no, speaking, cur) before the draft: the caller's sentence still open


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
        self.speaker = "client"  # copilot mode: whose voice is being recorded
        self._send_lock = asyncio.Lock()
        self.listener: Listener | None = None  # hands-free: the call's live STT stream
        self.draft: Draft | None = None
        self.said_lately = ""  # the agent's last words: what its echo in the mic would sound like
        self.cut = False  # hands-free: agent_audio_cut already sent for the caller's current words
        self.turn_ended = 0.0

    # ── sending ──

    async def send(self, msg: WireModel) -> None:
        await self._send(text=msg.to_json())

    async def send_bytes(self, data: bytes) -> None:
        await self._send(bytes=data)

    async def _send(self, **message) -> None:
        if (gate := draft_gate.get()) is not None:  # a drafted turn: held until kept, gone if dropped
            if not gate.event.is_set():
                gate.held.append(message)
                return
            if gate.dropped:
                return
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
                    if self.listener:
                        self.listener.feed(frame["bytes"])
                    elif self.recording:
                        self.recording.feed(frame["bytes"])
                    continue
                await self.handle_text(frame.get("text") or "")
        except WebSocketDisconnect:
            pass
        finally:
            self.stop_listening()
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
            case StartMessage(session_id=session_id, actor_name=actor_name, outbound_order_id=outbound_order_id, mode=mode):
                await self.interrupt()
                await self.open(session_id, actor_name, mode)
                if outbound_order_id:  # outbound call: the agent speaks first, from a hidden brief
                    try:
                        self.start_turn(text=await shortage_brief(outbound_order_id), outbound=True)
                    except Exception as e:
                        await self.send(VoiceErrorMessage(message=str(e)))
            case AudioStartMessage(mime=mime, hint=hint, speaker=speaker) if self.copilot:
                if self.recording:  # one mic: a new press drops an unfinished recording (both voices keep their sentences)
                    self.recording.cancel()
                self.speaker = speaker
                self.conversation.call.speech_start()
                self.recording = Utterance(get_stt_client(), mime, hint, on_partial=self.send_partial)
            case AudioEndMessage() if self.copilot:
                if self.recording:
                    utterance, self.recording = self.recording, None
                    self.conversation.call.speech_end()
                    spawn(self.copilot_line(self.conversation, self.speaker, utterance=utterance))
            case TextMessage(text=text, speaker=speaker) if self.copilot:
                spawn(self.copilot_line(self.conversation, speaker, text=text))
            case CopilotConfirmMessage():
                if self.copilot:
                    spawn(self.conversation.call.confirm())
            case AudioStartMessage(mime=mime, hint=hint, continuous=True):
                self.stop_listening()
                await self.interrupt()
                self.conversation.call.filler_after_ms = HANDSFREE_FILLER_MS
                spawn(jev.warm())  # the end-of-sentence check reads in ≈140 ms, not ≈950 ms, from the first sentence
                self.listener = Listener(get_stt_client(), mime, hint, self.on_words, self.on_endpoint)
            case AudioEndMessage() if self.listener:
                self.listener.close()  # the provider finalizes: a last endpoint may still come
            case AudioStartMessage(mime=mime, hint=hint):
                self.stop_listening()
                await self.interrupt()  # barge-in
                self.conversation.call.speech_start()
                self.recording = Utterance(get_stt_client(), mime, hint, on_partial=self.send_partial)
            case AudioEndMessage():
                if self.recording:
                    utterance, self.recording = self.recording, None
                    self.conversation.call.speech_end()
                    self.start_turn(utterance=utterance)
            case TextMessage(text=text, demo_error=demo_error):
                await self.interrupt()
                self.start_turn(text=text, demo_error=demo_error)
            case ResetMessage():
                self.stop_listening()
                await self.interrupt()
                self.end_call(report=False)
                await self.open(self.conversation.id)
            case HangupMessage():
                self.stop_listening()
                await self.interrupt()
                self.end_call(report=True)
                await self.open(self.conversation.id)

    @property
    def copilot(self) -> bool:
        return bool(self.conversation and isinstance(self.conversation.call, CopilotSession))

    async def send_partial(self, update: STTUpdate) -> None:
        if self.conversation and self.conversation.call:
            self.conversation.call.on_partial(update.text)  # the Éclaireur listens while the caller speaks
        track = "leila" if self.copilot and self.speaker == "leila" else "caller"
        await self.send(TranscriptMessage(text=update.text, final=False, stable=update.stable, track=track))

    def new_call(self, conv: Conversation) -> CallSession | CopilotSession:
        if conv.mode == "copilot":  # Leïla takes the call, the agents whisper
            copilot = CopilotSession(actor_name=conv.actor_name, tools=all_tools())
            copilot.state.listeners.append(lambda event: self.send(OrchestraMessage(event=event)))
            spawn(copilot.warm())
            return copilot
        call = CallSession(actor_name=conv.actor_name, llm=get_llm_client(), runner=run_agent, tools=all_tools())
        call.guard = Guard(call.state)  # type: ignore[attr-defined]  # le Garde-fou: Jev checks each sentence against the ERP

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

    async def write_report(self, call: CallSession | CopilotSession, report: bool) -> None:
        cid = call.state.id
        if report:
            await self.send(CallReportMessage(id=cid, status="writing"))
        try:
            r = await call.finish(report=report)
            if r and getattr(call, "guard", None) is not None:
                r["guard"] = call.guard.summary()
            if r:
                call_store.save(r)
                await self.send(CallReportMessage(id=cid, status="ready", headline=(r.get("ai") or {}).get("headline")))
        except Exception as e:
            print(f"[voice] report: {type(e).__name__}: {e}", flush=True)
            if report:
                await self.send(CallReportMessage(id=cid, status="error"))

    async def open(self, session_id: str | None = None, actor_name: str | None = None, mode: str | None = None) -> None:
        conv = conversations.get(session_id or "") or Conversation(id=session_id or str(uuid.uuid4()))
        if actor_name:
            conv.actor_name = actor_name
        conversations[conv.id] = self.conversation = conv
        if mode and mode != conv.mode:  # switching between the agent and the copilot: a new call
            self.end_call(report=False)
            conv.mode = mode
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
        await self.drop_draft()
        if self.recording:
            self.recording.cancel()
            self.recording = None
        if self.turn and not self.turn.done():
            self.turn.cancel()
            with suppress(asyncio.CancelledError):
                await self.turn
        self.turn = None

    # ── hands-free: the server finds the end of each sentence (services/handsfree.py) ──

    def stop_listening(self) -> None:
        if self.listener:
            self.listener.cancel()
            self.listener = None

    async def on_words(self, words: str, update: STTUpdate) -> None:
        """New words since the last kept turn, while the caller speaks."""
        if self.draft is not None and not same_words(words, self.draft.text):
            await self.drop_draft()  # they were not done: the sentence goes on, the draft is forgotten
        if self.conversation and self.conversation.call:
            self.conversation.call.on_partial(words)
        stable = self.listener.since(update.stable) if self.listener else ""
        await self.send(TranscriptMessage(text=words, final=False, stable=stable))
        if self.draft is not None:
            return
        if not self.audible() or self.cut or backchannel(words) or echo_of(words, self.said_lately):
            return
        self.cut = True  # talking over the agent: silence it now, on the client too
        await self.send(AgentAudioCutMessage())
        if self.turn and not self.turn.done():
            await self.interrupt()

    async def on_endpoint(self, text: str) -> None:
        """The caller paused: draft the answer now, decide in the background whether to keep it."""
        self.cut = False
        upto = self.listener.fired if self.listener else 0
        busy = self.turn is not None and not self.turn.done()  # a « oui » once it has finished is an answer
        if (self.audible() and echo_of(text, self.said_lately)) or (busy and backchannel(text)):
            if self.listener:
                self.listener.commit(upto)  # its own voice, or « oui oui » while it talks: not a turn
            return
        await self.interrupt()
        call = self.conversation.call
        gate = Gate()

        async def drafted() -> None:
            draft_gate.set(gate)  # this task and the ones it starts (speaker, guard, Greffier) are held by the gate
            await self.run_turn(self.conversation, text=text)

        self.turn = asyncio.create_task(drafted())
        utterance = (call.state.turn_no, call.speaking, dict(call.cur) if call.cur else None)
        self.draft = Draft(self.turn, gate, text, upto, list(call.history), len(call.state.turns), utterance)
        spawn(self.decide(self.draft))

    def audible(self) -> bool:
        """The agent may still be heard: its turn is running, or its audio may still be playing on the client."""
        busy = self.turn is not None and not self.turn.done()
        return busy or time.perf_counter() - self.turn_ended < PLAYBACK_TAIL_S

    async def decide(self, d: Draft) -> None:
        t = time.perf_counter()
        done = await is_complete(d.text)
        if not done:
            await asyncio.sleep(MAX_WAIT_S)  # « je voudrais du… »: give them time; new words drop the draft meanwhile
        kept = self.draft is d
        print(f"[voice] hands-free « {d.text[-50:]} »: {'fini' if done else 'pas fini'}, "
              f"{'gardé' if kept else 'abandonné'} après {ms_since(t)} ms", flush=True)  # fmt: skip
        if kept:
            await self.keep_draft()

    async def keep_draft(self) -> None:
        d, self.draft = self.draft, None
        if d is None:
            return
        if self.listener:
            self.listener.commit(d.upto)
        while d.gate.held:  # in order; the turn's new messages queue behind until the gate opens
            await self._send(**d.gate.held.pop(0))
        d.gate.open()

    async def drop_draft(self) -> None:
        d, self.draft = self.draft, None
        if d is None:
            return
        d.gate.drop()
        d.task.cancel()
        with suppress(asyncio.CancelledError):
            await d.task
        if self.turn is d.task:
            self.turn = None
        call = self.conversation.call if self.conversation else None
        if isinstance(call, CallSession):  # as if the draft never ran
            call.history = d.history
            del call.state.turns[d.turns :]
            call.state.turn_no, call.speaking, call.cur = d.utterance

    # ── copilot mode: one sentence of the client or of Leïla ──

    async def copilot_line(self, conv: Conversation, speaker: str, text: str | None = None, utterance: Utterance | None = None) -> None:
        try:
            if utterance is not None:
                text = await utterance.text()
            heard = (text or "").strip()
            if not heard:
                return
            await self.send(TranscriptMessage(text=heard, final=True, track="leila" if speaker == "leila" else "caller"))
            if isinstance(conv.call, CopilotSession):
                await conv.call.line(speaker, heard)
        except Exception as e:
            await self.send(VoiceErrorMessage(message=str(e) or type(e).__name__))
        finally:
            if utterance:
                utterance.cancel()

    # ── one turn ──

    async def run_turn(
        self,
        conv: Conversation,
        text: str | None = None,
        utterance: Utterance | None = None,  # spoken: already being transcribed since audio_start
        outbound: bool = False,  # `text` is the hidden outbound brief: don't show it as the caller's words
        demo_error: bool = False,  # Garde-fou demo (« Provoquer une erreur »): one wrong figure, for the guard to catch
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
            #    also on barge-in). The TTS speaks the caller's language. Le Garde-fou checks each sentence before it is said.
            guard: Guard | None = getattr(call, "guard", None)
            if guard is not None:
                guard.add_brief(heard) if outbound else guard.client_said(heard)
            card = load_caller(call.state.client["id"]) if call.state.client and call.state.client.get("id") else None
            speaker = Speaker(tts_for(call.state.language), self.send, self.send_bytes, stability=card.stability if card else None, guard=guard)
            llm_start = time.perf_counter()

            async def consume(source, demo_error: bool = False) -> None:
                nonlocal spoken
                held = ""  # demo: the text held back until a sentence with a figure is altered

                async def say(delta: str) -> None:
                    nonlocal spoken
                    spoken += delta
                    self.said_lately = (self.said_lately + delta)[-800:]
                    await self.send(AgentTextMessage(delta=delta))
                    speaker.push(delta)

                async with aclosing(source) as events:
                    # block mode: the stream stops as soon as the guard catches a sentence (like a barge-in)
                    stream = until(events, speaker.halt) if guard is not None and guard.blocking else events
                    async for ev in stream:
                        match ev:
                            case FillerEvent(text=filler):
                                self.said_lately = (self.said_lately + " " + filler)[-800:]
                                cached = filler_audio.get((call.state.language, filler))
                                speaker.play(cached) if cached else speaker.speak(filler)
                                await self.send(OrchestraMessage(event={"type": "filler", "text": filler}))
                            case FastEvent():
                                pass  # its text follows as a TextEvent
                            case TextEvent(delta=delta):
                                if timings.llm_first_token_ms is None:
                                    timings.llm_first_token_ms = ms_since(llm_start)
                                if demo_error:
                                    held += delta
                                    while demo_error and (m := Speaker.SENTENCE.match(held)):
                                        wrong = hallucinate(m[0])
                                        await say(wrong or m[0])
                                        held = held[m.end() :]
                                        demo_error = wrong is None
                                    if not demo_error and held:
                                        await say(held)
                                        held = ""
                                    continue
                                await say(delta)
                            case ToolCallEvent(call=tc):
                                await self.send(AgentToolMessage(name=tc.name, args=tc.arguments))
                            case ToolResultEvent(name=name, ok=ok, ms=ms):
                                await self.send(AgentToolMessage(name=name, ok=ok, ms=ms))
                            case ErrorEvent(message=message):
                                await self.send(VoiceErrorMessage(message=message))
                if held:
                    await say(held)

            await consume(call.turn(heard), demo_error=demo_error)
            await speaker.settle()
            if speaker.blocked is not None and guard is not None:
                await self.correct(call, guard, speaker, consume)
            timings.llm_total_ms = ms_since(llm_start)
            t = next((x for x in reversed(call.state.turns) if not x.get("guard")), {}) if call.state.turns else {}
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
            self.turn_ended = time.perf_counter()
            if utterance:
                utterance.cancel()  # interrupted while transcribing: close the provider stream


    async def correct(self, call: CallSession, guard: Guard, speaker: "Speaker", consume) -> None:
        """Block mode: the guard stopped a sentence the ERP contradicts. The agent re-says it from the ERP fact, in the same
        audio segment, as if it caught itself. One correction per turn; if that one is caught too, a safe line instead."""
        v: Verdict = speaker.blocked  # type: ignore[assignment]
        note = guard.correction_note(v, " ".join(speaker.said))
        at = speaker.resume()
        v.corrected_by = f"{v.id}c"
        await self.send(OrchestraMessage(event={"type": "guard_correction", "id": v.id, "at": at, "sentence": v.sentence,
                                                "erp": (v.source or {}).get("values") or [], "source": (v.source or {}).get("label")}))  # fmt: skip
        n_before = len(call.state.turns)
        await consume(call.turn(note))
        await speaker.settle()
        for t in call.state.turns[n_before:]:  # the hidden note is not the caller's words (report, timeline)
            t.update(caller="(Garde-fou : correction)", guard=v.id)
        if speaker.blocked is not None:  # caught twice: never a third guess
            speaker.resume()
            safe = " Je préfère vous le faire confirmer par Leïla, elle vous rappelle tout de suite."
            await self.send(AgentTextMessage(delta=safe))
            speaker.push(safe + " ")


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


@dataclass
class _Line:
    """A sentence waiting for the TTS; `check` = the Garde-fou's verdict on it, computing meanwhile."""

    text: str
    check: asyncio.Task | None = None
    gen: int = 0  # bumped when the guard blocks: the sentences queued after it are dropped


# Word-by-word TTS (ElevenLabs stream-input): the words go to the TTS as the LLM writes them, no wait for the end of the
# sentence. Off with TTS_STREAM=0, and never with a blocking Garde-fou (it must hear a whole sentence before it is said).
TTS_STREAM = os.environ.get("TTS_STREAM", "1") != "0"
SPOKEN_JUNK = re.compile(r"[*_`#]")  # markdown marks inside streamed words (the sentence path strips them with speakable)


class Speaker:
    """Buffers the streamed text, cuts it into sentences and speaks them in order, as one audio segment.

    Streaming TTS (a TTS with open_stream, guard not blocking): the words are forwarded as they come into one
    streaming synthesis for the turn, flushed at each sentence end; sentences are still cut for the guard's checks.

    With a Garde-fou: each sentence with a claim is checked by Jev as soon as it is complete. In block mode the TTS
    synthesizes it meanwhile and its audio is only sent once the verdict says the ERP backs it; a contradicted sentence
    is never heard, `blocked` is set and `halt` stops the turn (the voice agent then corrects itself)."""

    # a sentence ends with . ! ? … followed by a space ("18.50" is not a boundary)
    SENTENCE = re.compile(r"^([\s\S]*?[.!?…])\s+")

    def __init__(
        self,
        tts: TTSClient,
        send: Callable[[WireModel], Awaitable],
        send_bytes: Callable[[bytes], Awaitable],
        stability: float | None = None,  # ElevenLabs voice stability for this caller (agent/caller.py)
        guard: Guard | None = None,
    ):
        self.tts, self.send, self.send_bytes, self.stability, self.guard = tts, send, send_bytes, stability, guard
        self.buffer = ""
        self.sentences: asyncio.Queue[_Line | tuple[str, bytes] | None] = asyncio.Queue()
        self.worker: asyncio.Task | None = None  # speaks the queued sentences one after the other
        self.started = False
        self.first_byte_at: float | None = None
        self.tts_first_byte_ms: int | None = None
        self.previous = ""  # the sentence just spoken: ElevenLabs continues its intonation (previous_text)
        self.pos = 0  # characters of the turn's agent text already cut into sentences (verdict offsets)
        self.gen = 0
        self.blocked: Verdict | None = None
        self.halt = asyncio.Event()
        self.said: list[str] = []  # sentences actually spoken this turn
        self.checks: list[asyncio.Task] = []  # the guard's verdicts on this turn's sentences
        self.stream = None  # streaming TTS: the turn's one synthesis, opened now (handshake off the critical path)
        self.words = ""  # streamed text not yet forwarded (an unfinished word)
        self.queued_stream = False
        if TTS_STREAM and hasattr(tts, "open_stream") and not (guard is not None and guard.blocking):
            self.stream = tts.open_stream(settings.tts.voice or None, stability)  # type: ignore[attr-defined]
        self.stream_start = 0.0  # first word sent to the streaming TTS

    def push(self, delta: str) -> None:
        self.buffer += delta
        if self.stream is not None:
            self.words += delta
            cut = max(self.words.rfind(" "), self.words.rfind("\n"))
            if cut >= 0:
                self._forward(self.words[: cut + 1])
                self.words = self.words[cut + 1 :]
        while m := self.SENTENCE.match(self.buffer):
            raw = m[1]
            lead = len(raw) - len(raw.lstrip())
            self.speak(raw, start=self.pos + lead, end=self.pos + len(raw))
            self.pos += m.end()
            self.buffer = self.buffer[m.end() :]

    def speak(self, sentence: str, start: int | None = None, end: int | None = None) -> None:
        """`start`/`end` given = a sentence of the agent's text (checked by the guard); none = a filler line."""
        text = speakable(sentence)
        if not text.strip():
            return
        if self.blocked is not None and start is not None:
            return  # the guard stopped this turn: nothing more of it is said
        check = None
        if self.guard is not None and self.guard.enabled and start is not None:
            check = asyncio.create_task(self.guard.check(sentence.strip(), start, end))
            self.checks.append(check)
        if self.stream is not None and start is not None:  # its words are already in the stream: generate them now
            self.said.append(text)
            self.stream.send("", flush=True)
            return
        if self.worker is None:
            self.worker = asyncio.create_task(self._speak_queued())
        self.sentences.put_nowait(_Line(text, check, self.gen))

    def _forward(self, text: str) -> None:
        text = SPOKEN_JUNK.sub("", text)
        if not text.strip():
            return
        if not self.queued_stream:  # takes its place in the queue: a filler queued before it is heard first
            self.queued_stream = True
            self.stream_start = time.perf_counter()
            if self.worker is None:
                self.worker = asyncio.create_task(self._speak_queued())
            self.sentences.put_nowait(self.stream)  # type: ignore[arg-type]
        self.stream.send(text)  # type: ignore[union-attr]

    def play(self, audio: tuple[str, bytes]) -> None:
        """Queue pre-synthesized audio (mime, bytes): a cached filler line, no TTS round trip."""
        if self.worker is None:
            self.worker = asyncio.create_task(self._speak_queued())
        self.sentences.put_nowait(audio)

    def resume(self) -> int:
        """After a block: forget the unfinished text of the stopped turn and accept the correction. Returns its offset."""
        self.pos += len(self.buffer)
        self.buffer = ""
        self.blocked = None
        self.halt = asyncio.Event()
        self.checks = []
        return self.pos

    def flush(self) -> None:
        """Queue what is left in the buffer (the last sentence may have no final punctuation)."""
        if self.stream is not None and self.words:
            self._forward(self.words)
            self.words = ""
        rest = self.buffer
        lead = len(rest) - len(rest.lstrip())
        self.speak(rest, start=self.pos + lead, end=self.pos + len(rest.rstrip()))
        self.pos += len(rest)
        self.buffer = ""

    async def settle(self, timeout: float = 15) -> None:
        """Block mode, the LLM stream is over: Jev's last verdicts may still be on their way (~150 ms), and a blocked
        sentence only sets `blocked` once the sentences before it are said. Wait for both, so the turn knows whether
        to correct itself."""
        if self.guard is None or not self.guard.blocking:
            return
        self.flush()
        verdicts = await asyncio.gather(*self.checks, return_exceptions=True)
        if any(isinstance(v, Verdict) and v.blocked for v in verdicts) and self.blocked is None:
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.halt.wait(), timeout)

    async def finish(self) -> None:
        """Speak what is left in the buffer, wait for all the audio, close the segment."""
        self.flush()
        if self.stream is not None:
            if self.queued_stream:
                self.stream.end()
            else:  # nothing was said through it
                self.stream.cancel()
        if self.worker:
            self.sentences.put_nowait(None)
            await self.worker
        if self.started:
            await self.send(AgentAudioEndMessage())

    async def stop(self) -> None:
        """Interrupted: drop the queued sentences, close the segment if audio was sent."""
        if self.stream is not None:
            self.stream.cancel()
        if self.worker:
            self.worker.cancel()
            with suppress(asyncio.CancelledError):
                await self.worker
        if self.started:
            await self.send(AgentAudioEndMessage())

    async def _speak_queued(self) -> None:
        while (item := await self.sentences.get()) is not None:
            if item is self.stream:
                await self._stream_words()
                continue
            if isinstance(item, tuple):
                mime, audio = item
                if not self.started:
                    self.started = True
                    await self.send(AgentAudioStartMessage(mime=mime))
                    self.first_byte_at = time.perf_counter()
                    self.tts_first_byte_ms = 0
                await self.send_bytes(audio)
                continue
            if item.gen != self.gen:
                continue  # queued after a blocked sentence: never said
            if item.check is not None and self.guard is not None and self.guard.blocking:
                await self._say_gated(item)
            else:
                await self._say(item.text)

    async def _stream_words(self) -> None:
        try:
            async for chunk in self.stream.chunks():  # type: ignore[union-attr]
                if not self.started:
                    self.started = True
                    await self.send(AgentAudioStartMessage(mime=self.stream.mime))  # type: ignore[union-attr]
                    self.first_byte_at = time.perf_counter()
                    self.tts_first_byte_ms = ms_since(self.stream_start)
                await self.send_bytes(chunk)
        except Exception as e:
            await self.send(VoiceErrorMessage(message=f"TTS : {e}"))

    async def _synthesize(self, text: str):
        extra = {"stability": self.stability, "previous_text": self.previous or None} if self.tts.name == "elevenlabs" else {}
        return await self.tts.synthesize(text, settings.tts.voice or None, **extra)  # type: ignore[call-arg]

    async def _say(self, text: str) -> None:
        tts_start = time.perf_counter()
        try:
            await self._stream(text, await self._synthesize(text), tts_start)
        except Exception as e:
            await self.send(VoiceErrorMessage(message=f"TTS : {e}"))

    async def _say_gated(self, item: _Line) -> None:
        """Synthesize while Jev checks; send the audio only if the sentence is not contradicted by the ERP."""
        tts_start = time.perf_counter()
        synth = asyncio.create_task(self._synthesize(item.text))
        verdict: Verdict | None = None
        try:
            verdict = await item.check  # type: ignore[misc]
        except Exception:
            verdict = None  # a failed check never silences the agent
        if verdict is not None and verdict.blocked:
            synth.cancel()
            with suppress(BaseException):
                result = await synth
                close = getattr(result.chunks, "aclose", None)
                if close:
                    await close()
            self.gen += 1
            self.blocked = verdict
            self.halt.set()
            return
        try:
            await self._stream(item.text, await synth, tts_start)
        except Exception as e:
            await self.send(VoiceErrorMessage(message=f"TTS : {e}"))

    async def _stream(self, text: str, result, tts_start: float) -> None:
        self.previous = text
        self.said.append(text)
        # aclosing: on barge-in (cancellation) the provider's HTTP stream is closed right away
        async with aclosing(result.chunks) as chunks:  # type: ignore[type-var]
            async for chunk in chunks:
                if not self.started:
                    self.started = True
                    await self.send(AgentAudioStartMessage(mime=result.mime))
                    self.first_byte_at = time.perf_counter()
                    self.tts_first_byte_ms = ms_since(tts_start)
                await self.send_bytes(chunk)

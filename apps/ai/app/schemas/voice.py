"""
WS /voice contract: push-to-talk sessions, two tracks (the human "caller" and the agent).

Client → server: JSON messages below, plus binary audio frames between audio_start and audio_end.
Server → client: JSON messages below, plus binary audio frames between agent_audio_start and agent_audio_end.
"""

from typing import Annotated, Any, Literal, Union

from pydantic import Field

from .base import WireModel
from .chat import TurnTimings

# ── Client → server ──


class StartMessage(WireModel):
    """Opens a conversation (same sessionId again = keep the history)."""

    type: Literal["start"] = "start"
    session_id: str | None = None
    actor_name: str | None = None
    outbound_order_id: str | None = None  # outbound shortage call: the agent calls this order's client and speaks first
    mode: Literal["agent", "copilot"] = "agent"  # copilot: Leïla speaks, the agents listen and whisper (orchestra/copilot.py)


class AudioStartMessage(WireModel):
    """Caller pressed the talk key; binary frames follow."""

    type: Literal["audio_start"] = "audio_start"
    mime: str
    hint: str | None = None  # optional STT hint (vocabulary; mock STT: the transcript), like x-mock-text on POST /command
    speaker: Literal["client", "leila"] = "client"  # copilot mode: whose voice this is (agent mode: always the caller)


class AudioEndMessage(WireModel):
    """Caller released the key: the frames received since audio_start are the utterance (already streamed to the
    STT as they arrived: send them every 100–250 ms, e.g. MediaRecorder.start(100))."""

    type: Literal["audio_end"] = "audio_end"


class TextMessage(WireModel):
    """Typed instead of spoken (debug)."""

    type: Literal["text"] = "text"
    text: str
    speaker: Literal["client", "leila"] = "client"
    demo_error: bool = False  # Garde-fou demo: the agent is told to state one wrong figure, to show it being caught


class ResetMessage(WireModel):
    type: Literal["reset"] = "reset"


class HangupMessage(WireModel):
    """The caller hung up: the call ends, the Rapporteur writes its report (call_report messages follow)."""

    type: Literal["hangup"] = "hangup"


class CopilotConfirmMessage(WireModel):
    """Copilot mode: Leïla clicks « Valider la commande » → finalize_order (the ERP's gates), copilot_order follows."""

    type: Literal["copilot_confirm"] = "copilot_confirm"


VoiceClientMessage = Annotated[
    Union[StartMessage, AudioStartMessage, AudioEndMessage, TextMessage, ResetMessage, HangupMessage, CopilotConfirmMessage],
    Field(discriminator="type"),
]

# ── Server → client ──


class ReadyMessage(WireModel):
    type: Literal["ready"] = "ready"
    session_id: str
    stt: str
    tts: str
    llm: str


class TranscriptMessage(WireModel):
    """What the caller said. While they speak (streaming STT): final=false, the whole text heard so far, sent again
    as words are recognised; `stable` is its leading part made of final words (will not change, the rest may).
    Then once final=true with the definitive text, which the agent answers."""

    type: Literal["transcript"] = "transcript"
    track: Literal["caller", "leila"] = "caller"  # leila: copilot mode, Leïla's own voice
    text: str
    final: bool
    stable: str | None = None


class AgentTextMessage(WireModel):
    type: Literal["agent_text"] = "agent_text"
    delta: str


class AgentToolMessage(WireModel):
    """Sent twice per tool: once with `args` when called, once with `ok`/`ms` when done."""

    type: Literal["agent_tool"] = "agent_tool"
    name: str
    ok: bool | None = None
    ms: int | None = None
    args: dict[str, Any] | None = None


class AgentAudioStartMessage(WireModel):
    """Binary audio frames follow (one segment per turn)."""

    type: Literal["agent_audio_start"] = "agent_audio_start"
    mime: str


class AgentAudioEndMessage(WireModel):
    type: Literal["agent_audio_end"] = "agent_audio_end"


class AgentDoneMessage(WireModel):
    type: Literal["agent_done"] = "agent_done"
    text: str
    timings: TurnTimings


class OrchestraMessage(WireModel):
    """What the agents around the voice agent are doing (app/orchestra), for the live view: `event` is one of
    {type: "agent_activity", agent, label, task, status: start|done|error, ms?}, {type: "call_profile", language, register},
    {type: "scribe_note", label}, {type: "filler", text}, {type: "turn", n, path: llm|fast, firstSoundMs, firstWordMs, prefetched};
    copilot mode: {type: "copilot_order", order, gate, final}, {type: "whisper", key, level, title, body, source},
    {type: "whisper_clear", keys}, {type: "copilot_mood", score, label}."""

    type: Literal["orchestra"] = "orchestra"
    event: dict[str, Any]


class CallReportMessage(WireModel):
    """After a hangup: "writing" right away, then "ready" (GET /calls/{id}) or "error"."""

    type: Literal["call_report"] = "call_report"
    id: str
    status: Literal["writing", "ready", "error"]
    headline: str | None = None


class VoiceErrorMessage(WireModel):
    type: Literal["error"] = "error"
    message: str


VoiceServerMessage = Annotated[
    Union[
        ReadyMessage,
        TranscriptMessage,
        AgentTextMessage,
        AgentToolMessage,
        AgentAudioStartMessage,
        AgentAudioEndMessage,
        AgentDoneMessage,
        OrchestraMessage,
        CallReportMessage,
        VoiceErrorMessage,
    ],
    Field(discriminator="type"),
]

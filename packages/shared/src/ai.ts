/**
 * Contract of the AI service (apps/ai, default http://localhost:4100).
 *
 *  GET  /health                         → { ok, llm: {provider, model}, stt: {provider}, tts: {provider} }
 *  GET  /tools                          → ToolInfo[]  (tools the harness exposes to the model)
 *  POST /chat                           body ChatRequest → text/event-stream of ChatStreamEvent (one JSON per `data:` line)
 *  WS   /voice                          bidirectional, see VoiceClientMessage / VoiceServerMessage
 *  POST /stt                            raw audio body (audio/webm | audio/wav) → { text, ms }
 *  POST /tts                            body { text, voice? } → audio bytes (audio/mpeg or audio/wav), header x-ms
 *  POST /command[?speak=1]              single-turn voice COMMAND (stateless, act-first, one-line answer).
 *                                       body = raw audio (content-type audio/webm|wav…, header x-mock-text for the mock STT)
 *                                       or JSON { text, actorName? } → text/event-stream of CommandStreamEvent:
 *                                       transcript → (text | tool_call | tool_result)* → navigate? → speech? → done (with timings) | error.
 *                                       speak=1 → a `speech` event (base64 audio; MOCK_TTS_MIME = speak the text in the browser).
 *  WS   /voice/command[?speak=1]        the same command with LIVE transcription: send audio_start {mime, hint?}, binary frames
 *                                       while the key is down, audio_end (or one { type: "text" }); receive CommandStreamEvent JSON
 *                                       (transcript final=false while speaking, then the POST /command events). Closed after done | error.
 */

export type ChatRole = "system" | "user" | "assistant" | "tool";

export interface ChatMessage {
  role: ChatRole;
  content: string;
  toolCallId?: string; // for role "tool"
  toolCalls?: ToolCall[]; // for role "assistant"
  name?: string;
}

export interface ToolCall {
  id: string;
  name: string;
  arguments: Record<string, unknown>;
}

export interface ToolInfo {
  name: string;
  description: string;
  parameters: Record<string, unknown>; // JSON schema
}

export interface ChatRequest {
  messages: ChatMessage[]; // full conversation so far (no system message; the harness adds it)
  mode?: "chat" | "voice" | "command"; // voice → short spoken-style answers; command → single-turn, act-first, one line
  actorName?: string;
}

export type ChatStreamEvent =
  | { type: "text"; delta: string }
  | { type: "tool_call"; call: ToolCall }
  | { type: "tool_result"; callId: string; name: string; ok: boolean; result: unknown; ms: number }
  | { type: "done"; message: ChatMessage; usage?: { inputTokens?: number; outputTokens?: number; cacheCreationInputTokens?: number; cacheReadInputTokens?: number }; ms: number; timings?: TurnTimings }
  | { type: "error"; message: string }
  | { type: "guard"; verdict: GuardVerdict }; // le Garde-fou: Jev's verdict on one sentence of the answer (never blocks in the chat)

/**
 * Le Garde-fou (apps/ai/app/guard): before a sentence of the agent is spoken, Jev checks it against what the ERP said during
 * the call (tool results, the Éclaireur's facts, the outbound brief, the business date). status:
 *  none = nothing checkable (« Je regarde. ») · verified = the ERP backs it · contradicted = a value differs from the ERP or a promise
 *  nobody granted · unverified = no ERP fact says either way · unchecked = Jev too slow, spoken anyway.
 * blocked = contradicted in GUARD_MODE=block: never spoken, the agent corrects itself (guard_correction follows).
 * start/end = offsets of the sentence in the turn's agent text (voice only).
 */
export interface GuardVerdict {
  id: string;
  turn: number;
  sentence: string;
  kinds: string[];
  status: "none" | "verified" | "contradicted" | "unverified" | "unchecked";
  blocked: boolean;
  strong?: boolean; // clear enough to block (shadow mode: « would have been blocked »)
  mode: "off" | "shadow" | "block";
  start?: number;
  end?: number;
  pSupported?: number;
  pContradicts?: number;
  pPromises?: number;
  pMismatch?: number;
  reason?: "contredit" | "promesse";
  source?: { label: string; tool: string; values: string[]; confidence: number };
  said?: string[];
  ms?: number;
  correctedBy?: string;
}

/** End-of-call summary of the Garde-fou (in the call report). */
export interface GuardSummary {
  mode: string;
  sentences: number;
  checked: number;
  verified: number;
  contradicted: number;
  blocked: number;
  strong?: number;
  unverified: number;
  unchecked: number;
  corrections: number;
  medianMs?: number | null;
  flags: { sentence: string; status: string; blocked: boolean; source?: string }[];
}

// ── Voice (push-to-talk, two tracks: the human "caller" and the agent) ──

export type VoiceClientMessage =
  | { type: "start"; sessionId?: string; actorName?: string; outboundOrderId?: string; mode?: VoiceMode } // opens a conversation (outboundOrderId: the agent calls that order's client and speaks first)
  | { type: "audio_start"; mime: string; hint?: string; speaker?: CopilotSpeaker } // caller pressed space (hint: optional STT vocabulary; mock STT returns it)
  | { type: "audio_end" } // caller released space; binary frames sent in between (every 100–250 ms) are the utterance, streamed to the STT as they arrive
  | { type: "text"; text: string; speaker?: CopilotSpeaker; demoError?: boolean } // typed instead of spoken (debug); demoError: Garde-fou demo, the agent states one wrong figure
  | { type: "reset" }
  | { type: "hangup" } // the call ends: the Rapporteur writes the report (call_report follows)
  | { type: "copilot_confirm" }; // copilot mode: Leïla clicks « Valider la commande » → finalize_order (the ERP's gates)

/** agent: the voice agent answers the caller · copilot: Leïla speaks, the agents listen and whisper (orchestra/copilot.py). */
export type VoiceMode = "agent" | "copilot";
/** Copilot mode: whose voice a recording / typed line is. */
export type CopilotSpeaker = "client" | "leila";

export interface TurnTimings {
  sttMs?: number;
  llmFirstTokenMs?: number;
  llmTotalMs?: number;
  ttsFirstByteMs?: number;
  totalMs?: number; // end of caller speech → first agent audio byte
}

export type VoiceServerMessage =
  | { type: "ready"; sessionId: string; stt: string; tts: string; llm: string }
  // While the caller speaks (streaming STT): final=false, the whole text heard so far, resent as words are recognised;
  // `stable` = its leading part made of final words (won't change). Then once final=true: what the agent answers.
  | { type: "transcript"; track: "caller" | "leila"; text: string; final: boolean; stable?: string }
  | { type: "agent_text"; delta: string }
  | { type: "agent_tool"; name: string; ok?: boolean; ms?: number; args?: Record<string, unknown> }
  | { type: "agent_audio_start"; mime: string } // binary frames follow
  | { type: "agent_audio_end" }
  | { type: "agent_done"; text: string; timings: TurnTimings }
  | { type: "orchestra"; event: OrchestraEvent } // the agents working around the voice agent (live view)
  | { type: "call_report"; id: string; status: "writing" | "ready" | "error"; headline?: string }
  | { type: "error"; message: string };

/** Agents of a call (app/orchestra): voice, scout (Éclaireur), fast (Réflexe), scribe (Greffier), linguist, reporter. */
export type OrchestraAgent = "voice" | "scout" | "fast" | "scribe" | "linguist" | "reporter" | "warmup" | "copilot" | "guard";

export type OrchestraEvent =
  | { type: "agent_activity"; agent: OrchestraAgent; label: string; task: string; status: "start" | "done" | "error"; ms?: number }
  | { type: "call_profile"; language: string; register: "tu" | "vous" }
  | { type: "scribe_note"; label: string }
  | { type: "filler"; text: string }
  | { type: "turn"; n: number; path: "llm" | "fast"; firstSoundMs?: number; firstWordMs?: number; prefetched: string[] }
  // le Garde-fou: a verdict per sentence; in block mode a contradicted sentence is replaced by the agent's own correction,
  // whose text starts at offset `at` of the turn's agent text
  | ({ type: "guard" } & GuardVerdict)
  | { type: "guard_correction"; id: string; at: number; sentence: string; erp: string[]; source?: string }
  // copilot mode (Souffleur): the order as dictated, kept in the ERP; whispers for Leïla; the client's mood (Jev)
  | { type: "copilot_order"; order: CopilotOrder | null; gate: { decision: "confirm" | "draft" | "already_confirmed"; reasons: string[] } | null; final: boolean }
  | { type: "whisper"; key: string; level: WhisperLevel; title: string; body: string; source: string }
  | { type: "whisper_clear"; keys: string[] }
  | { type: "copilot_mood"; score: number; label: string };

export type WhisperLevel = "danger" | "warn" | "info" | "tip";

/** summarize_order (apps/ai/app/tools/orders.py): what the copilot keeps in sync with the ERP. */
export interface CopilotOrder {
  id: string;
  number?: string;
  status: string;
  client?: string;
  deliveryDate?: string;
  totalHt?: number;
  lines: { lineId: string; productId: string; productName?: string; qty: number; unit?: string; unitPrice?: number; available?: number; note?: string }[];
  warnings: { code: string; message: string; productId?: string }[];
}

/**
 * Mime returned by the AI service's mock TTS (no key configured): the bytes are a short silent WAV.
 * Clients should ignore the audio and speak the agent text with the browser's speechSynthesis instead.
 */
export const MOCK_TTS_MIME = "audio/x-mock-wav";

// ── Voice command (POST /command: one utterance → one action → one line) ──

/** JSON body of POST /command (or send raw audio instead). */
export interface CommandRequest {
  text: string;
  actorName?: string;
}

export type CommandStreamEvent =
  | ChatStreamEvent
  // final (default true) comes before the agent's events; ms = STT time, 0 for typed text.
  // WS /ai/voice/command also sends final=false ones while the user speaks (text so far, `stable` = final words).
  | { type: "transcript"; text: string; ms: number; final?: boolean; stable?: string }
  | { type: "navigate"; path: string; label?: string } // the user asked to SEE an entity: web route to open (e.g. /products/prd_0012)
  | { type: "speech"; mime: string; audio: string; ms: number }; // ?speak=1: base64 audio of the answer; mime MOCK_TTS_MIME → speechSynthesis

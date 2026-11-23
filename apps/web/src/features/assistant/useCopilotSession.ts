/**
 * Copilot mode of WS /ai/voice (start {mode: "copilot"}): Leïla takes the call herself, the agents listen and whisper.
 * Both voices go through the same push-to-talk mic (audio_start {speaker}) or are typed (text {speaker}); nothing is
 * spoken back. The server sends the transcript of each voice (track caller | leila), the order the Souffleur keeps in
 * the ERP (copilot_order), whispers for Leïla (whisper / whisper_clear), the client's mood (copilot_mood, Jev) and the
 * same orchestra activity as agent mode. « Valider la commande » = copilot_confirm (finalize_order, the ERP's gates).
 */
import { useCallback, useEffect, useRef, useState } from "react";
import type { CopilotOrder, CopilotSpeaker, VoiceClientMessage, VoiceServerMessage, WhisperLevel } from "@kit/shared";
import { AI_BASE } from "@/lib/api";
import type { AgentLive, CallReportState, Conn, LaneSpan, MicState, Providers } from "./useVoiceSession";

export interface CopilotLine { id: number; speaker: CopilotSpeaker; text: string; stable?: string; status: "recording" | "transcribing" | "final"; at: number }
export interface Whisper { key: string; level: WhisperLevel; title: string; body: string; source: string; at: number }
export interface CopilotGate { decision: "confirm" | "draft" | "already_confirmed"; reasons: string[] }

const REC_MIME = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"];
const wsUrl = () => `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${AI_BASE}/voice`;
const COPILOT_AGENTS: [string, string][] = [["copilot", "Souffleur"], ["scout", "Éclaireur"], ["scribe", "Greffier"], ["linguist", "Linguiste"]];
const freshAgents = (): Record<string, AgentLive> =>
  Object.fromEntries(COPILOT_AGENTS.map(([agent, label]) => [agent, { agent: agent as AgentLive["agent"], label, status: "idle", runs: 0 }]));

export function useCopilotSession() {
  const [conn, setConn] = useState<Conn>("connecting");
  const [mic, setMic] = useState<MicState>("unknown");
  const [providers, setProviders] = useState<Providers>();
  const [recording, setRecording] = useState<CopilotSpeaker>();
  const [level, setLevel] = useState(0);
  const [lastError, setLastError] = useState<string>();
  const [lines, setLines] = useState<CopilotLine[]>([]);
  const [order, setOrder] = useState<CopilotOrder | null>(null);
  const [gate, setGate] = useState<CopilotGate | null>(null);
  const [final, setFinal] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [whispers, setWhispers] = useState<Whisper[]>([]);
  const [mood, setMood] = useState<{ score: number; label: string }>();
  const [agents, setAgents] = useState<Record<string, AgentLive>>(freshAgents);
  const [lanes, setLanes] = useState<LaneSpan[]>([]);
  const [notes, setNotes] = useState<string[]>([]);
  const [profile, setProfile] = useState<{ language: string; register: "tu" | "vous" }>({ language: "fr", register: "vous" });
  const [report, setReport] = useState<CallReportState>();

  const ws = useRef<WebSocket>();
  const sessionId = useRef<string>();
  const alive = useRef(true);
  const seq = useRef(0);
  const live = useRef<number>(); // the line being recorded (partials go there)
  const stream = useRef<MediaStream>();
  const recorder = useRef<MediaRecorder>();
  const pressed = useRef(false);
  const audioCtx = useRef<AudioContext>();
  const analyser = useRef<AnalyserNode>();
  const raf = useRef<number>();

  const send = useCallback((m: VoiceClientMessage) => {
    if (ws.current?.readyState === WebSocket.OPEN) ws.current.send(JSON.stringify(m));
  }, []);

  const onMessage = useCallback((ev: MessageEvent) => {
    if (typeof ev.data !== "string") return; // no agent audio in copilot mode
    let m: VoiceServerMessage;
    try { m = JSON.parse(ev.data); } catch { return; }
    switch (m.type) {
      case "ready":
        sessionId.current = m.sessionId;
        setProviders({ llm: m.llm, stt: m.stt, tts: m.tts, sessionId: m.sessionId });
        setConn("ready");
        break;
      case "transcript": {
        const speaker: CopilotSpeaker = m.track === "leila" ? "leila" : "client";
        if (!m.final) {
          const id = live.current;
          setLines((ls) => ls.map((l) => (l.id === id && l.status !== "final" ? { ...l, text: m.text, stable: m.stable ?? "" } : l)));
          break;
        }
        setLines((ls) => {
          // the oldest unfinished recording of that voice gets its final text; a typed line arrives here first
          const i = ls.findIndex((l) => l.speaker === speaker && l.status !== "final");
          if (i < 0) return [...ls, { id: ++seq.current, speaker, text: m.text, status: "final", at: Date.now() }];
          const next = [...ls];
          next[i] = { ...next[i], text: m.text, status: "final" };
          return next;
        });
        break;
      }
      case "orchestra": {
        const e = m.event;
        if (e.type === "agent_activity") {
          if (e.agent === "warmup" || e.agent === "reporter") break;
          setAgents((a) => {
            const cur = a[e.agent] ?? { agent: e.agent, label: e.label, status: "idle", runs: 0 };
            const next: AgentLive = e.status === "start"
              ? { ...cur, label: e.label, task: e.task, status: "busy", at: Date.now() }
              : { ...cur, status: e.status === "error" ? "error" : "idle", runs: cur.runs + 1, lastMs: e.ms };
            return { ...a, [e.agent]: next };
          });
          if (e.status === "start") {
            const span: LaneSpan = { id: ++seq.current, agent: e.agent, task: e.task, start: Date.now() };
            setLanes((l) => [...l.filter((x) => x.end == null || Date.now() - x.end < 60_000).slice(-80), span]);
          } else {
            setLanes((l) => {
              let i = l.length - 1;
              while (i >= 0 && !(l[i].agent === e.agent && l[i].task === e.task && l[i].end == null)) i--;
              if (i < 0) return l;
              const next = [...l];
              next[i] = { ...next[i], end: Date.now(), ok: e.status === "done" };
              return next;
            });
          }
        } else if (e.type === "call_profile") setProfile({ language: e.language, register: e.register });
        else if (e.type === "scribe_note") setNotes((n) => [e.label, ...n].slice(0, 20));
        else if (e.type === "copilot_order") {
          setOrder(e.order);
          setGate(e.gate);
          if (e.final) { setFinal(true); setConfirming(false); }
        } else if (e.type === "whisper") {
          const w: Whisper = { key: e.key, level: e.level, title: e.title, body: e.body, source: e.source, at: Date.now() };
          setWhispers((ws) => [w, ...ws.filter((x) => x.key !== e.key)].slice(0, 30));
        } else if (e.type === "whisper_clear") setWhispers((ws) => ws.filter((x) => !e.keys.includes(x.key)));
        else if (e.type === "copilot_mood") setMood({ score: e.score, label: e.label });
        break;
      }
      case "call_report":
        setReport({ id: m.id, status: m.status, headline: m.headline });
        break;
      case "error":
        setLastError(m.message);
        setConfirming(false);
        setLines((ls) => ls.filter((l) => l.status === "final" || l.text)); // drop an empty recording ("Rien entendu")
        break;
    }
  }, []);

  const connect = useCallback(() => {
    if (!alive.current) return;
    ws.current?.close();
    setConn("connecting");
    const sock = new WebSocket(wsUrl());
    sock.binaryType = "arraybuffer";
    ws.current = sock;
    sock.onopen = () => sock.send(JSON.stringify({ type: "start", sessionId: sessionId.current, actorName: "Leïla", mode: "copilot" } satisfies VoiceClientMessage));
    sock.onmessage = onMessage;
    sock.onclose = () => {
      if (ws.current !== sock) return;
      setConn("offline");
      if (alive.current) window.setTimeout(() => { if (ws.current === sock) connect(); }, 3000);
    };
  }, [onMessage]);

  useEffect(() => {
    alive.current = true;
    connect();
    return () => {
      alive.current = false;
      const s = ws.current;
      ws.current = undefined;
      s?.close();
      if (raf.current) cancelAnimationFrame(raf.current);
      try { recorder.current?.stop(); } catch { /* ignore */ }
      stream.current?.getTracks().forEach((t) => t.stop());
      void audioCtx.current?.close().catch(() => undefined);
    };
  }, [connect]);

  // ── Mic (one for both voices: push-to-talk says whose it is) ──
  const ensureMic = useCallback(async (): Promise<MediaStream | undefined> => {
    if (stream.current?.active) return stream.current;
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") { setMic("unsupported"); return undefined; }
    try {
      const s = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
      stream.current = s;
      setMic("granted");
      const Ctx = window.AudioContext ?? (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      const ctx = new Ctx();
      const an = ctx.createAnalyser();
      an.fftSize = 512;
      ctx.createMediaStreamSource(s).connect(an);
      audioCtx.current = ctx;
      analyser.current = an;
      return s;
    } catch (e) {
      setMic((e as DOMException).name === "NotAllowedError" || (e as DOMException).name === "SecurityError" ? "denied" : "unsupported");
      return undefined;
    }
  }, []);

  const meter = useCallback(() => {
    const an = analyser.current;
    if (!an) return;
    const buf = new Uint8Array(an.fftSize);
    const tick = () => {
      an.getByteTimeDomainData(buf);
      let sum = 0;
      for (const v of buf) { const x = (v - 128) / 128; sum += x * x; }
      setLevel(Math.min(1, Math.sqrt(sum / buf.length) * 4));
      raf.current = requestAnimationFrame(tick);
    };
    tick();
  }, []);

  const startTalking = useCallback(async (speaker: CopilotSpeaker) => {
    if (pressed.current || conn !== "ready") return;
    pressed.current = true;
    const s = await ensureMic();
    if (!s || !pressed.current) { pressed.current = false; return; }
    const mime = REC_MIME.find((t) => MediaRecorder.isTypeSupported(t)) ?? "";
    const rec = new MediaRecorder(s, mime ? { mimeType: mime } : undefined);
    recorder.current = rec;
    send({ type: "audio_start", mime: rec.mimeType || mime || "audio/webm", speaker });
    rec.ondataavailable = (e) => { if (e.data.size && ws.current?.readyState === WebSocket.OPEN) ws.current.send(e.data); };
    rec.onstop = () => send({ type: "audio_end" });
    void audioCtx.current?.resume();
    rec.start(100);
    const id = ++seq.current;
    live.current = id;
    setLines((ls) => [...ls, { id, speaker, text: "", status: "recording", at: Date.now() }]);
    setRecording(speaker);
    setLastError(undefined);
    meter();
  }, [conn, ensureMic, meter, send]);

  const stopTalking = useCallback(() => {
    if (!pressed.current) return;
    pressed.current = false;
    if (raf.current) cancelAnimationFrame(raf.current);
    setLevel(0);
    setRecording(undefined);
    const rec = recorder.current;
    recorder.current = undefined;
    if (rec && rec.state !== "inactive") {
      rec.stop();
      const id = live.current;
      setLines((ls) => ls.map((l) => (l.id === id && l.status === "recording" ? { ...l, status: "transcribing" } : l)));
    }
  }, []);

  const sendText = useCallback((text: string, speaker: CopilotSpeaker) => {
    const v = text.trim();
    if (!v || conn !== "ready") return;
    send({ type: "text", text: v, speaker }); // the server echoes it as a final transcript of that voice
    setLastError(undefined);
  }, [conn, send]);

  const confirm = useCallback(() => {
    if (!order || final) return;
    setConfirming(true);
    send({ type: "copilot_confirm" });
  }, [final, order, send]);

  const clear = useCallback(() => {
    live.current = undefined;
    setLines([]);
    setOrder(null);
    setGate(null);
    setFinal(false);
    setConfirming(false);
    setWhispers([]);
    setMood(undefined);
    setAgents(freshAgents());
    setLanes([]);
    setNotes([]);
    setProfile({ language: "fr", register: "vous" });
    setLastError(undefined);
  }, []);

  /** End the call: the Rapporteur writes the report (call_report "writing" → "ready"), a new call starts. */
  const hangup = useCallback(() => { send({ type: "hangup" }); clear(); }, [clear, send]);
  const reset = useCallback(() => { send({ type: "reset" }); clear(); setReport(undefined); }, [clear, send]);

  return {
    conn, mic, providers, recording, level, lastError, lines, order, gate, final, confirming, whispers, mood,
    agents, lanes, notes, profile, report, dismissReport: () => setReport(undefined),
    dismissWhisper: (key: string) => setWhispers((ws) => ws.filter((w) => w.key !== key)),
    startTalking, stopTalking, sendText, confirm, hangup, reset,
  };
}

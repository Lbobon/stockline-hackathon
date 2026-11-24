/**
 * /phone — the demo phone. Open it on a real phone (through the HTTPS tunnel), tap "Activer la ligne", then wait:
 * the PC (Voix → Téléphone) makes it ring. "Accepter" starts a hands-free call (the kit's « Mains libres »: the mic stays
 * open, the server finds the end of each sentence, talking over the agent cuts it): the agent (Médina Viandes) speaks
 * first, with the Radar's reason when the client is in tomorrow's forecast. The PC shows the whole conversation live.
 *
 * Line: WS /ai/voice/phone (ring / cancel in, answered / declined / hungup out). Call: WS /ai/voice?line=phone.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { Mic, MicOff, Phone, PhoneOff, Store } from "lucide-react";
import { AI_BASE } from "@/lib/api";
import { unlockAudio } from "@/features/assistant/audio-player";
import { useVoiceSession } from "@/features/assistant/useVoiceSession";

type Stage = "standby" | "waiting" | "ringing" | "call";
interface Ring { clientId: string; clientName: string }

const lineUrl = () => `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${AI_BASE}/voice/phone`;

export default function PhonePage() {
  const [stage, setStage] = useState<Stage>("standby");
  const [ring, setRing] = useState<Ring>();
  const [lineUp, setLineUp] = useState(false);
  const [startedAt, setStartedAt] = useState<number>();
  const [muted, setMuted] = useState(false);
  const [, tick] = useState(0);
  const v = useVoiceSession({ line: "phone" });
  const stageRef = useRef(stage);
  stageRef.current = stage;
  const line = useRef<WebSocket>();
  const ringer = useRef<{ ctx: AudioContext; timer?: number }>();

  const say = (m: object) => { if (line.current?.readyState === WebSocket.OPEN) line.current.send(JSON.stringify(m)); };

  // ── Ringtone: two-tone burst every 2 s, WebAudio (unlocked by "Activer la ligne") + vibration where supported ──
  const stopRinging = useCallback(() => {
    const r = ringer.current;
    if (r?.timer) window.clearInterval(r.timer);
    if (r) r.timer = undefined;
    try { navigator.vibrate?.(0); } catch { /* ignore */ }
  }, []);
  const startRinging = useCallback(() => {
    const r = ringer.current;
    if (!r || r.timer) return;
    const burst = () => {
      const t0 = r.ctx.currentTime;
      for (const [i, f] of [[0, 880], [0.18, 1108.7], [0.36, 880], [0.54, 1108.7]] as const) {
        const o = r.ctx.createOscillator();
        const g = r.ctx.createGain();
        o.frequency.value = f;
        g.gain.setValueAtTime(0, t0 + i);
        g.gain.linearRampToValueAtTime(0.25, t0 + i + 0.02);
        g.gain.exponentialRampToValueAtTime(0.001, t0 + i + 0.17);
        o.connect(g).connect(r.ctx.destination);
        o.start(t0 + i);
        o.stop(t0 + i + 0.18);
      }
      try { navigator.vibrate?.([400, 200, 400]); } catch { /* ignore */ }
    };
    burst();
    r.timer = window.setInterval(burst, 2000);
  }, []);

  // ── The line: waits for "ring" from the PC ──
  useEffect(() => {
    if (stage === "standby") return;
    let alive = true;
    const open = () => {
      const ws = new WebSocket(lineUrl());
      line.current = ws;
      ws.onopen = () => setLineUp(true);
      ws.onmessage = (ev) => {
        let m: { type?: string; clientId?: string; clientName?: string };
        try { m = JSON.parse(ev.data as string); } catch { return; }
        if (m.type === "ring" && m.clientId && stageRef.current === "waiting") {
          setRing({ clientId: m.clientId, clientName: m.clientName ?? "" });
          setStage("ringing");
        } else if (m.type === "cancel" && stageRef.current === "ringing") {
          setStage("waiting");
        }
      };
      ws.onclose = () => {
        setLineUp(false);
        if (alive) window.setTimeout(() => { if (alive) open(); }, 2000);
      };
    };
    open();
    return () => { alive = false; line.current?.close(); };
  }, [stage === "standby"]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (stage === "ringing") startRinging();
    else stopRinging();
  }, [stage, startRinging, stopRinging]);

  useEffect(() => {
    if (stage !== "call") return;
    const clock = window.setInterval(() => tick((n) => n + 1), 1000);
    return () => window.clearInterval(clock);
  }, [stage]);

  // ── Taps ──
  const activate = async () => {
    unlockAudio();
    const Ctx = window.AudioContext ?? (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    const ctx = new Ctx();
    void ctx.resume();
    ringer.current = { ctx };
    setStage("waiting");
    void (navigator as Navigator & { wakeLock?: { request: (t: "screen") => Promise<unknown> } }).wakeLock?.request("screen").catch(() => undefined);
    void v.ensureMic(); // ask for the mic now, not in the middle of the first call
  };

  const accept = async () => {
    if (!ring) return;
    unlockAudio();
    stopRinging();
    say({ type: "answered" });
    setStartedAt(Date.now());
    setStage("call");
    await setMic(false);
    await v.startHandsFree(); // mic first: a hands-free start cancels a running turn, the opening line must come after
    v.callClient(ring.clientId); // the agent speaks first
  };

  // Mute = the mic track sends silence: the call, the hands-free mode and the barge-in stay as they are on the server
  const setMic = async (mute: boolean) => {
    setMuted(mute);
    (await v.ensureMic())?.getAudioTracks().forEach((t) => { t.enabled = !mute; });
  };

  const decline = () => {
    stopRinging();
    say({ type: "declined" });
    setStage("waiting");
  };

  const hangup = () => {
    v.hangup(); // also closes the hands-free mic
    void setMic(false);
    say({ type: "hungup" });
    setStartedAt(undefined);
    setStage("waiting");
  };

  // ── Screen ──
  const last = v.turns[v.turns.length - 1];
  const secs = startedAt ? Math.floor((Date.now() - startedAt) / 1000) : 0;
  const clock = `${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, "0")}`;
  const status =
    muted ? "Micro coupé" : v.turns.some((t) => t.caller.status === "recording") ? "Vous parlez…" : v.activity === "speaking" ? "Médina Viandes parle" : v.activity === "thinking" ? "…" : "À vous";

  return (
    <div className="fixed inset-0 flex select-none flex-col items-center justify-between overflow-hidden bg-[radial-gradient(ellipse_at_50%_20%,#123326_0%,#06100c_55%,#000_100%)] px-6 pb-[max(2.5rem,env(safe-area-inset-bottom))] pt-[max(3.5rem,env(safe-area-inset-top))] text-white">
      {stage === "standby" ? (
        <>
          <Header title="Ligne de démo" sub="Ce téléphone sonnera quand le PC lancera l'appel." />
          <button type="button" onClick={() => void activate()} className="mb-10 rounded-full bg-white px-8 py-4 text-[17px] font-semibold text-black active:scale-95">
            Activer la ligne
          </button>
        </>
      ) : stage === "waiting" ? (
        <>
          <Header title="En attente d'appel" sub={lineUp ? "Ligne connectée. Laissez cette page ouverte, écran allumé." : "Connexion à la ligne…"} />
          <span className={`mb-12 h-3 w-3 rounded-full ${lineUp ? "bg-emerald-400" : "animate-pulse bg-amber-400"}`} />
        </>
      ) : stage === "ringing" ? (
        <>
          <Caller sub="Agent IA · appel entrant" ringing />
          <div className="mb-6 flex w-full max-w-[320px] items-center justify-between">
            <RoundButton label="Refuser" tone="red" onClick={decline}><PhoneOff size={30} /></RoundButton>
            <RoundButton label="Accepter" tone="green" onClick={() => void accept()} pulse><Phone size={30} /></RoundButton>
          </div>
        </>
      ) : (
        <>
          <Caller sub={`${clock} · ${status}`} level={v.level} />
          <div className="w-full max-w-[420px] space-y-3 text-center">
            {last?.agent.text ? <p className="text-[17px] leading-snug text-white/90">{last.agent.text}</p> : null}
            {last && last.caller.text && !last.caller.typed ? <p className="text-[14px] text-white/50">« {last.caller.text} »</p> : null}
            {v.mic === "denied" || v.mic === "unsupported" ? <p className="text-[13px] text-amber-300">Micro refusé : autorisez-le dans les réglages du navigateur.</p> : null}
          </div>
          <div className="mb-6 flex w-full max-w-[320px] items-center justify-between">
            <RoundButton label={muted ? "Réactiver" : "Muet"} tone={muted ? "white" : "gray"} onClick={() => void setMic(!muted)}>
              {muted ? <MicOff size={30} /> : <Mic size={30} />}
            </RoundButton>
            <RoundButton label="Raccrocher" tone="red" onClick={hangup}><PhoneOff size={30} /></RoundButton>
          </div>
        </>
      )}
    </div>
  );
}

function Header({ title, sub }: { title: string; sub: string }) {
  return (
    <div className="mt-24 flex flex-col items-center gap-3 text-center">
      <div className="flex h-24 w-24 items-center justify-center rounded-full bg-white/10"><Store size={42} strokeWidth={1.5} /></div>
      <h1 className="text-[26px] font-semibold">{title}</h1>
      <p className="max-w-[280px] text-[15px] text-white/60">{sub}</p>
    </div>
  );
}

function Caller({ sub, ringing, level = 0 }: { sub: string; ringing?: boolean; level?: number }) {
  return (
    <div className="mt-10 flex flex-col items-center gap-3 text-center">
      <div className="relative flex h-28 w-28 items-center justify-center">
        {ringing ? <span className="absolute inset-0 animate-ping rounded-full bg-emerald-400/25" /> : null}
        <span className="absolute inset-0 rounded-full bg-emerald-400/20 transition-transform duration-100" style={{ transform: `scale(${1 + level * 0.35})` }} />
        <div className="relative flex h-24 w-24 items-center justify-center rounded-full bg-gradient-to-b from-emerald-500 to-emerald-700 shadow-[0_0_40px_rgba(16,185,129,0.45)]">
          <Store size={44} strokeWidth={1.5} />
        </div>
      </div>
      <h1 className="text-[32px] font-semibold tracking-tight">Médina Viandes</h1>
      <p className="text-[15px] text-white/60">{sub}</p>
    </div>
  );
}

function RoundButton({ label, tone, onClick, pulse, children }: { label: string; tone: "red" | "green" | "gray" | "white"; onClick: () => void; pulse?: boolean; children: React.ReactNode }) {
  return (
    <button type="button" onClick={onClick} className="flex flex-col items-center gap-2 active:scale-95">
      <span className={`relative flex h-[76px] w-[76px] items-center justify-center rounded-full ${{ red: "bg-red-500", green: "bg-emerald-500", gray: "bg-white/15", white: "bg-white text-black" }[tone]}`}>
        {pulse ? <span className="absolute inset-0 animate-ping rounded-full bg-emerald-500/40" /> : null}
        <span className="relative">{children}</span>
      </span>
      <span className="text-[13px] text-white/80">{label}</span>
    </button>
  );
}

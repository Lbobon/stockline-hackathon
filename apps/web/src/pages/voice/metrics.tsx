/** Latency chips: per turn (from agent_done.timings) and the running average across turns. */
import type { ReactNode } from "react";
import type { TurnTimings } from "@kit/shared";
import { Gauge } from "lucide-react";
import { cn } from "@/lib/cn";
import { SECTION_LABEL } from "@/ui";
import type { VoiceTurn } from "@/features/assistant/useVoiceSession";

type Key = keyof TurnTimings | "perceivedMs";
const METRICS: { key: Key; label: string; title: string }[] = [
  { key: "sttMs", label: "STT", title: "Transcription de la parole de l'appelant" },
  { key: "llmFirstTokenMs", label: "LLM 1er", title: "Premier token du modèle" },
  { key: "llmTotalMs", label: "LLM", title: "Modèle, outils compris" },
  { key: "ttsFirstByteMs", label: "TTS 1er", title: "Premier octet audio de la synthèse" },
  { key: "totalMs", label: "Total", title: "Fin de parole de l'appelant → premier octet audio de l'agent (serveur)" },
  { key: "perceivedMs", label: "Perçu", title: "Relâchement de la touche → l'agent parle dans le navigateur" },
];

const value = (t: VoiceTurn, k: Key): number | undefined => (k === "perceivedMs" ? t.perceivedMs : t.timings?.[k]);

/** Total latency tone: < 1,5 s fast, < 3 s acceptable, else slow. */
function tone(k: Key, ms: number) {
  if (k !== "totalMs" && k !== "perceivedMs") return "text-secondary-foreground";
  return ms < 1500 ? "text-success" : ms < 3000 ? "text-warning" : "text-destructive";
}

export const fmtMs = (ms: number) => (ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toLocaleString("fr-FR", { minimumFractionDigits: 1, maximumFractionDigits: 1 })} s`);

export function Chip({ label, ms, k, title, className }: { label: string; ms?: number; k: Key; title?: string; className?: string }) {
  return (
    <span title={title} className={cn("inline-flex h-[18px] items-center gap-1 rounded-[3px] border border-border bg-card px-1.5 font-mono text-[10.5px]", className)}>
      <span className="text-muted-foreground">{label}</span>
      <span className={cn("num", ms == null ? "text-muted-foreground/60" : tone(k, ms))}>{ms == null ? "—" : fmtMs(ms)}</span>
    </span>
  );
}

export function TurnChips({ t }: { t: VoiceTurn }) {
  if (!t.timings && t.perceivedMs == null && !t.orch) return null;
  const o = t.orch;
  return (
    <div className="flex flex-wrap items-center gap-1">
      {o?.path === "fast" ? <Tag title="Répondu par le Réflexe, sans le grand modèle">⚡ Réflexe</Tag> : null}
      {o?.prefetched?.length ? <Tag title={o.prefetched.join("\n")}>Préchargé ×{o.prefetched.length}</Tag> : null}
      {o?.filler ? <Tag title="Phrase d'attente pré-enregistrée, jouée tout de suite">« {o.filler} »</Tag> : null}
      {o?.firstSoundMs != null ? <Chip k="totalMs" label="1er son" ms={o.firstSoundMs} title="Fin de parole → premier son (phrase d'attente ou réponse), côté serveur" /> : null}
      {METRICS.map((m) => {
        const ms = value(t, m.key);
        return ms == null ? null : <Chip key={m.key} k={m.key} label={m.label} ms={ms} title={m.title} />;
      })}
    </div>
  );
}

function Tag({ children, title }: { children: ReactNode; title?: string }) {
  return (
    <span title={title} className="inline-flex h-[18px] max-w-[220px] items-center truncate rounded-[3px] border border-violet/30 bg-violet/[.06] px-1.5 text-[10.5px] text-violet">
      {children}
    </span>
  );
}

/** Strip above the timeline: running averages (and the count of measured turns). */
export function MetricsBar({ turns }: { turns: VoiceTurn[] }) {
  const measured = turns.filter((t) => t.timings);
  const avg = (k: Key) => {
    const xs = turns.map((t) => value(t, k)).filter((x): x is number => x != null);
    return xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : undefined;
  };
  return (
    <div className="flex min-h-10 shrink-0 flex-wrap items-center gap-x-3 gap-y-1 border-b border-border px-5 py-1.5">
      <span className={cn(SECTION_LABEL, "flex items-center gap-1.5")}>
        <Gauge size={13} strokeWidth={1.75} />
        Latence moyenne
      </span>
      <span className="num text-[11.5px] text-muted-foreground">{measured.length ? `${measured.length} tour${measured.length > 1 ? "s" : ""}` : "aucun tour mesuré"}</span>
      <div className="flex min-w-0 flex-wrap items-center gap-1">
        {METRICS.map((m) => <Chip key={m.key} k={m.key} label={m.label} ms={avg(m.key)} title={m.title} />)}
      </div>
    </div>
  );
}

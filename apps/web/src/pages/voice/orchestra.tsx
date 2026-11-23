/**
 * The call orchestra, live (agents of app/orchestra working around the voice agent).
 *  - Agent tiles: icon in the agent's color, what it is doing right now (animated while busy), runs and last duration.
 *  - Live lanes: the last 30 s, one lane per agent, bars sliding left as time passes: you see the Éclaireur work
 *    while the client is still talking, the Réflexe answer, the Greffier write after the turn.
 * After a hangup: a banner while the Rapporteur writes, then a link to the report.
 */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowUpRight, FileText, Languages, NotebookPen, X } from "lucide-react";
import { cn } from "@/lib/cn";
import { SECTION_LABEL, Spinner } from "@/ui";
import type { AgentLive, CallReportState, LaneSpan } from "@/features/assistant/useVoiceSession";
import { AGENT, LANGUAGE, TEAM, type AgentKey } from "@/features/calls/agents";
import { fmtMs } from "./metrics";

const WINDOW_MS = 30_000;

export function OrchestraStrip({ agents, lanes, profile, notes, team = TEAM, subtitle = "6 agents en parallèle de l'appel" }: {
  agents: Record<string, AgentLive>; lanes: LaneSpan[]; profile: { language: string; register: "tu" | "vous" }; notes: string[];
  team?: AgentKey[]; subtitle?: string;
}) {
  return (
    <div className="shrink-0 border-b border-border">
      <div className="flex h-8 items-center gap-2 px-5">
        <span className={SECTION_LABEL}>Orchestre</span>
        <span className="hidden text-[11.5px] text-muted-foreground lg:inline">{subtitle}</span>
        <div className="ml-auto flex min-w-0 items-center gap-3">
          {notes[0] ? (
            <span className="flex min-w-0 items-center gap-1.5 text-[11.5px] animate-fade-in" title="Dernière écriture du Greffier dans la fiche client">
              <NotebookPen size={12} strokeWidth={1.75} className="shrink-0 text-agent-scribe" />
              <span className="truncate text-secondary-foreground">{notes[0]}</span>
            </span>
          ) : null}
          <span className="inline-flex h-[20px] shrink-0 items-center gap-1 rounded-[3px] border border-agent-linguist/35 px-1.5 text-[11px] text-secondary-foreground" title="Détecté par le Linguiste : l'agent répond dans cette langue et ce registre">
            <Languages size={12} strokeWidth={1.75} className="text-agent-linguist" />
            {LANGUAGE[profile.language] ?? profile.language}
            <span className="text-muted-foreground">·</span>
            {profile.register === "tu" ? "tu" : "vous"}
          </span>
        </div>
      </div>
      <div className="grid border-t border-border/70" style={{ gridTemplateColumns: `repeat(${team.length}, minmax(0, 1fr))` }}>
        {team.map((k) => <AgentTile key={k} k={k} a={agents[k]} />)}
      </div>
      <Lanes lanes={lanes} team={team} />
    </div>
  );
}

function AgentTile({ k, a }: { k: AgentKey; a?: AgentLive }) {
  const m = AGENT[k];
  const busy = a?.status === "busy";
  const Icon = m.icon;
  return (
    <div className={cn("relative min-w-0 border-r border-border/70 px-3 py-2 transition-colors last:border-r-0", busy && m.soft)} title={m.role}>
      <span className={cn("absolute inset-x-0 top-0 h-[2px] transition-opacity", m.bg, busy ? "opacity-100" : "opacity-0")} />
      <div className="flex items-center gap-2">
        <span className={cn("flex h-6 w-6 shrink-0 items-center justify-center rounded-[4px]", m.soft)}>
          <Icon size={14} strokeWidth={1.9} className={m.text} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-1.5">
            <span className="truncate text-[12.5px] font-medium">{m.label}</span>
            {a?.runs ? <span className="num ml-auto shrink-0 font-mono text-[10.5px] text-muted-foreground">×{a.runs}</span> : null}
          </div>
          <div className="flex h-4 items-center gap-1.5 text-[11px] text-muted-foreground">
            {busy ? (
              <>
                <Equalizer cls={m.bg} />
                <span className="truncate text-secondary-foreground">{a?.task}…</span>
              </>
            ) : a?.lastMs != null ? (
              <span className="truncate">{a.task} · <span className="num font-mono">{fmtMs(a.lastMs)}</span></span>
            ) : (
              <span className="truncate">{m.role}</span>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function Equalizer({ cls }: { cls: string }) {
  return (
    <span className="flex h-2.5 shrink-0 items-end gap-[2px]" aria-hidden>
      {[0, 0.2, 0.4].map((d) => <span key={d} className={cn("h-full w-[2px] origin-bottom rounded-[1px] animate-eq-bar", cls)} style={{ animationDelay: `${d}s` }} />)}
    </span>
  );
}

/** The last 30 seconds, one lane per agent; re-rendered 5×/s only while something is on screen. */
function Lanes({ lanes, team }: { lanes: LaneSpan[]; team: AgentKey[] }) {
  const [now, setNow] = useState(() => Date.now());
  const live = lanes.some((l) => l.end == null || now - l.end < WINDOW_MS);
  useEffect(() => {
    if (!live) return;
    const t = setInterval(() => setNow(Date.now()), 200);
    return () => clearInterval(t);
  }, [live]);
  const x = (t: number) => Math.max(0, Math.min(100, 100 - ((now - t) / WINDOW_MS) * 100));
  const visible = lanes.filter((l) => (l.end ?? now) >= now - WINDOW_MS);
  return (
    <div className="border-t border-border/70 px-5 py-2">
      <div className="grid grid-cols-[86px_1fr] items-center gap-x-2 gap-y-[3px]">
        {team.map((k) => (
          <LaneRow key={k} label={AGENT[k].label} cls={AGENT[k].bg} spans={visible.filter((s) => s.agent === k || (k === "voice" && s.agent === "warmup"))} x={x} now={now} />
        ))}
        <span />
        <div className="relative h-3 font-mono text-[9.5px] text-muted-foreground">
          {[30, 20, 10].map((s) => <span key={s} className="absolute -translate-x-1/2" style={{ left: `${100 - (s / 30) * 100}%` }}>−{s}s</span>)}
          <span className="absolute right-0 text-violet">maintenant</span>
        </div>
      </div>
    </div>
  );
}

function LaneRow({ label, cls, spans, x, now }: { label: string; cls: string; spans: LaneSpan[]; x: (t: number) => number; now: number }) {
  return (
    <>
      <span className="truncate text-[10.5px] text-muted-foreground">{label}</span>
      <div className="relative h-2 rounded-[2px] bg-muted/60">
        {[25, 50, 75].map((p) => <span key={p} className="absolute inset-y-0 w-px bg-background" style={{ left: `${p}%` }} />)}
        {spans.map((s) => {
          const left = x(s.start);
          const right = x(s.end ?? now);
          return (
            <span
              key={s.id}
              title={`${label} · ${s.task}${s.end ? ` · ${fmtMs(s.end - s.start)}` : " · en cours"}`}
              className={cn("absolute inset-y-0 min-w-[3px] rounded-[2px]", cls, s.end == null && "animate-pulse", s.ok === false && "bg-destructive")}
              style={{ left: `${left}%`, width: `${Math.max(0, right - left)}%` }}
            />
          );
        })}
      </div>
    </>
  );
}

export function ReportBanner({ report, onClose }: { report?: CallReportState; onClose: () => void }) {
  if (!report) return null;
  const ready = report.status === "ready";
  return (
    <div className={cn("relative flex shrink-0 items-center gap-2.5 overflow-hidden border-b border-border px-5 py-2 text-[12.5px]", report.status === "error" ? "bg-destructive/[.06]" : "bg-violet/[.06]")}>
      {report.status === "writing" ? <span className="absolute inset-x-0 bottom-0 h-[2px] animate-pulse bg-violet/60" /> : null}
      <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-[4px] bg-violet/10">
        {report.status === "writing" ? <Spinner size={12} className="text-violet" /> : <FileText size={13} strokeWidth={1.9} className="text-violet" />}
      </span>
      {report.status === "writing" ? (
        <span><span className="font-medium">Appel terminé.</span> <span className="text-secondary-foreground">Le Rapporteur rédige le compte rendu pour Leïla…</span></span>
      ) : ready ? (
        <>
          <span className="shrink-0 font-medium">Compte rendu prêt</span>
          {report.headline ? <span className="min-w-0 truncate text-secondary-foreground">{report.headline}</span> : null}
          <Link to={`/calls/${report.id}`} className="inline-flex h-6 shrink-0 items-center gap-1 rounded-[4px] bg-violet px-2 text-[12px] font-medium text-primary-foreground hover:bg-violet/90">
            Ouvrir <ArrowUpRight size={12} />
          </Link>
        </>
      ) : (
        <span>Le compte rendu n'a pas pu être rédigé.</span>
      )}
      <button type="button" onClick={onClose} aria-label="Fermer" className="ml-auto rounded-[3px] p-0.5 text-muted-foreground hover:text-foreground">
        <X size={13} />
      </button>
    </div>
  );
}

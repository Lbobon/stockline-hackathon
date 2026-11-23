/**
 * The call orchestra, live: the agents working around the voice agent (app/orchestra on the AI service).
 * One column per agent (dot pulses while it works, its current task, how many runs), then a ticker of what just finished.
 * After a hangup: a banner while the Rapporteur writes, then a link to the report.
 */
import { Link } from "react-router-dom";
import { ArrowUpRight, FileText, Languages, X } from "lucide-react";
import { cn } from "@/lib/cn";
import { Dot, SECTION_LABEL, Spinner } from "@/ui";
import type { AgentLive, CallReportState, OrchestraFeedItem } from "@/features/assistant/useVoiceSession";
import { fmtMs } from "./metrics";

const ROLE: Record<string, string> = {
  scout: "écoute pendant que le client parle, précharge l'ERP",
  fast: "répond aux « oui » et « au revoir » sans le grand modèle",
  voice: "parle au client",
  scribe: "tient la fiche client en arrière-plan",
  linguist: "langue et tutoiement de l'appelant",
};
const LANG: Record<string, string> = { fr: "Français", en: "English", ar: "العربية", es: "Español", it: "Italiano", tr: "Türkçe", de: "Deutsch", pt: "Português" };

export function OrchestraStrip({ agents, feed, profile, notes }: {
  agents: Record<string, AgentLive>; feed: OrchestraFeedItem[]; profile: { language: string; register: "tu" | "vous" }; notes: string[];
}) {
  const list = ["scout", "fast", "voice", "scribe", "linguist"].map((k) => agents[k]).filter(Boolean);
  return (
    <div className="shrink-0 border-b border-border">
      <div className="flex items-center gap-2 px-5 pt-2">
        <span className={SECTION_LABEL}>Orchestre</span>
        <span className="text-[11.5px] text-muted-foreground">agents en parallèle de l'appel</span>
        <span className="ml-auto inline-flex h-[18px] items-center gap-1 rounded-[3px] border border-violet/30 bg-violet/[.06] px-1.5 text-[10.5px] text-violet" title="Détecté par le Linguiste">
          <Languages size={11} strokeWidth={1.75} />
          {LANG[profile.language] ?? profile.language} · {profile.register === "tu" ? "tutoiement" : "vouvoiement"}
        </span>
      </div>
      <div className="grid grid-cols-5 gap-px px-5 py-2">
        {list.map((a) => (
          <div key={a.agent} className="min-w-0 pr-3" title={ROLE[a.agent]}>
            <div className="flex items-center gap-1.5">
              <Dot tone={a.status === "busy" ? "violet" : a.status === "error" ? "destructive" : a.runs ? "success" : "muted"} pulse={a.status === "busy"} />
              <span className={cn("truncate text-[12.5px] font-medium", a.status === "busy" && "text-violet")}>{a.label}</span>
              {a.runs ? <span className="num ml-auto font-mono text-[10.5px] text-muted-foreground">×{a.runs}</span> : null}
            </div>
            <div className="mt-0.5 h-4 truncate text-[11px] text-muted-foreground">
              {a.status === "busy" ? <span className="text-violet">{a.task}…</span> : a.lastMs != null ? `${a.task} · ${fmtMs(a.lastMs)}` : ROLE[a.agent]}
            </div>
          </div>
        ))}
      </div>
      {feed.length || notes.length ? (
        <div className="flex h-7 items-center gap-3 overflow-hidden border-t border-border/60 px-5 text-[11px]">
          {notes[0] ? (
            <span className="inline-flex shrink-0 items-center gap-1 text-success">
              <Dot tone="success" />
              Greffier : {notes[0]}
            </span>
          ) : null}
          <div className="flex min-w-0 items-center gap-3 overflow-hidden whitespace-nowrap">
            {feed.slice(0, 8).map((f) => (
              <span key={f.id} className="inline-flex shrink-0 items-center gap-1 text-muted-foreground animate-fade-in">
                <span className="text-foreground/80">{f.label}</span>
                {f.task}
                {f.ms != null ? <span className="num font-mono text-[10px]">{fmtMs(f.ms)}</span> : null}
              </span>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

export function ReportBanner({ report, onClose }: { report?: CallReportState; onClose: () => void }) {
  if (!report) return null;
  return (
    <div className={cn("flex shrink-0 items-center gap-2 border-b border-border px-5 py-2 text-[12.5px]", report.status === "error" ? "bg-destructive/[.06]" : "bg-violet/[.06]")}>
      {report.status === "writing" ? <Spinner size={12} className="text-violet" /> : <FileText size={14} strokeWidth={1.75} className="text-violet" />}
      {report.status === "writing" ? (
        <span>Appel terminé. Le Rapporteur rédige le compte rendu…</span>
      ) : report.status === "ready" ? (
        <>
          <span className="truncate">Compte rendu prêt{report.headline ? ` : ${report.headline}` : ""}</span>
          <Link to={`/calls/${report.id}`} className="inline-flex shrink-0 items-center gap-0.5 font-medium text-violet underline-offset-2 hover:underline">
            Ouvrir <ArrowUpRight size={12} />
          </Link>
        </>
      ) : (
        <span>Le compte rendu n'a pas pu être rédigé.</span>
      )}
      <button type="button" onClick={onClose} aria-label="Fermer" className="ml-auto text-muted-foreground hover:text-foreground">
        <X size={13} />
      </button>
    </div>
  );
}

/**
 * Le Garde-fou, on screen (apps/ai/app/guard): before a sentence of the agent is spoken, Jev checks it against what the ERP
 * said during the call. Each sentence that states a price, a quantity, a stock, a date or a discount gets a mark:
 *   ✓ conforme (the ERP fact that backs it on hover) · ✗ contredite (blocked before it was said, the agent corrects itself)
 *   ? rien dans l'ERP pour l'affirmer · ⏱ non vérifiée (Jev too slow, spoken anyway).
 *  - GuardedText: the agent's text with those marks, the never-said part and the correction (Voix timeline).
 *  - GuardPanel: live counters + the latest verdicts (Voix, right column).
 *  - GuardAlertBanner: the moment a sentence is blocked.
 *  - GuardChatFooter: the same verdicts under a chat answer (the chat never blocks: the text is already out).
 */
import { useEffect, useRef, useState, type ReactNode } from "react";
import { Clock, CornerDownRight, FlaskConical, ShieldCheck, ShieldQuestion, ShieldX, X, type LucideIcon } from "lucide-react";
import type { GuardVerdict } from "@kit/shared";
import { cn } from "@/lib/cn";
import { AnchoredPopover, Button } from "@/ui";

type Shown = Exclude<GuardVerdict["status"], "none">;

const STATUS: Record<Shown, { label: string; icon: LucideIcon; text: string; mark: string; soft: string; dot: string }> = {
  verified: {
    label: "Conforme à l'ERP", icon: ShieldCheck, text: "text-success",
    mark: "decoration-success/55 underline decoration-[1.5px] underline-offset-[3px]", soft: "bg-success/10", dot: "bg-success",
  },
  contradicted: {
    label: "Contredit l'ERP", icon: ShieldX, text: "text-destructive",
    mark: "decoration-destructive/70 underline decoration-wavy decoration-[1.5px] underline-offset-[3px]", soft: "bg-destructive/10", dot: "bg-destructive",
  },
  unverified: {
    label: "Rien dans l'ERP pour l'affirmer", icon: ShieldQuestion, text: "text-warning",
    mark: "decoration-warning/60 underline decoration-dotted decoration-[1.5px] underline-offset-[3px]", soft: "bg-warning/10", dot: "bg-warning",
  },
  unchecked: {
    label: "Non vérifiée (Jev trop lent)", icon: Clock, text: "text-muted-foreground",
    mark: "decoration-border-hover underline decoration-dotted underline-offset-[3px]", soft: "bg-muted", dot: "bg-muted-foreground/50",
  },
};

const statusOf = (v: GuardVerdict): Shown => (v.status === "none" ? "unchecked" : v.status);
const labelOf = (v: GuardVerdict) => (v.blocked ? "Bloquée avant d'être dite" : v.reason === "promesse" ? "Promesse que l'ERP ne prévoit pas" : STATUS[statusOf(v)].label);
const pct = (p?: number) => (p == null ? "—" : `${Math.round(p * 100)} %`);
const KIND: Record<string, string> = { prix: "prix", remise: "remise", stock: "stock", date: "date", commande: "commande", "quantité": "quantité" };

// ── one sentence, marked ──

function VerdictCard({ v }: { v: GuardVerdict }) {
  const s = STATUS[statusOf(v)];
  const Icon = s.icon;
  return (
    <div className="flex flex-col gap-2 p-3 text-[12px]">
      <div className="flex items-center gap-1.5">
        <span className={cn("flex h-5 w-5 items-center justify-center rounded-[3px]", s.soft)}><Icon size={12} strokeWidth={2} className={s.text} /></span>
        <span className="font-medium">{labelOf(v)}</span>
        {v.ms != null ? <span className="num ml-auto font-mono text-[10.5px] text-muted-foreground">Jev · {v.ms} ms</span> : null}
      </div>
      <p className={cn("text-[12.5px] leading-[18px]", v.blocked && "text-muted-foreground line-through decoration-destructive/60")}>« {v.sentence} »</p>
      {v.source ? (
        <div className="flex flex-col gap-1 rounded-[4px] border border-border bg-background px-2 py-1.5">
          <span className="flex items-center gap-1.5 text-[10.5px] uppercase tracking-[.08em] text-muted-foreground">
            <span className="h-1.5 w-1.5 rounded-full bg-agent-guard" />Source ERP
          </span>
          <span className="font-medium">{v.source.label}</span>
          {v.source.values.length ? (
            <span className="flex flex-wrap gap-1">
              {v.source.values.map((x) => <span key={x} className="num rounded-[3px] bg-muted px-1.5 py-px font-mono text-[11px]">{x}</span>)}
            </span>
          ) : null}
          <span className="font-mono text-[10.5px] text-muted-foreground">{v.source.tool.replace("scout:", "Éclaireur · ")}</span>
        </div>
      ) : (
        <span className="text-muted-foreground">Aucun fait de l'ERP chargé dans l'appel ne parle de ça.</span>
      )}
      <div className="grid grid-cols-3 gap-1.5 text-[10.5px] text-muted-foreground">
        <Prob label="appuyée" p={v.pSupported} cls="bg-success" />
        <Prob label="contredite" p={Math.max(v.pContradicts ?? 0, v.pMismatch ?? 0)} cls="bg-destructive" />
        <Prob label="promesse" p={v.pPromises} cls="bg-warning" />
      </div>
      {v.kinds.length ? <span className="text-[10.5px] text-muted-foreground">Affirme : {v.kinds.map((k) => KIND[k] ?? k).join(", ")}</span> : null}
    </div>
  );
}

function Prob({ label, p, cls }: { label: string; p?: number; cls: string }) {
  return (
    <span className="flex flex-col gap-0.5">
      <span className="flex justify-between"><span>{label}</span><span className="num font-mono">{pct(p)}</span></span>
      <span className="h-1 overflow-hidden rounded-full bg-muted"><span className={cn("block h-full rounded-full", cls)} style={{ width: `${Math.round((p ?? 0) * 100)}%` }} /></span>
    </span>
  );
}

function Hover({ v, children, className }: { v: GuardVerdict; children: ReactNode; className?: string }) {
  const ref = useRef<HTMLSpanElement>(null);
  const [open, setOpen] = useState(false);
  return (
    <span ref={ref} className={className} onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)} onFocus={() => setOpen(true)} onBlur={() => setOpen(false)} tabIndex={0}>
      {children}
      <AnchoredPopover anchorRef={ref} open={open} width={300} matchWidth={false} role="tooltip">
        <VerdictCard v={v} />
      </AnchoredPopover>
    </span>
  );
}

function Mark({ v, text }: { v: GuardVerdict; text: string }) {
  const s = STATUS[statusOf(v)];
  const Icon = s.icon;
  if (v.blocked) {
    return (
      <Hover v={v} className="cursor-help rounded-[3px] px-0.5 text-muted-foreground line-through decoration-destructive/70 decoration-[1.5px] outline-none animate-guard-flash">
        {text}
        <span className="ml-1 inline-flex translate-y-[-1px] items-center gap-0.5 rounded-[3px] bg-destructive/10 px-1 align-middle text-[10.5px] font-medium text-destructive no-underline animate-stamp">
          <ShieldX size={10} strokeWidth={2.2} />bloquée
        </span>
      </Hover>
    );
  }
  return (
    <Hover v={v} className={cn("cursor-help rounded-[2px] outline-none transition-colors hover:bg-muted/70 focus-visible:ring-2 focus-visible:ring-ring/70", s.mark)}>
      {text}
      <Icon size={11} strokeWidth={2.2} className={cn("ml-0.5 inline translate-y-[-1px] animate-stamp", s.text)} aria-label={labelOf(v)} />
    </Hover>
  );
}

/** The agent's text with a mark on each checked sentence. After a blocked sentence: the never-said part, then the correction. */
export function GuardedText({ text, verdicts = [], correction }: { text: string; verdicts?: GuardVerdict[]; correction?: { id: string; at: number } }) {
  const cut = correction && correction.at <= text.length ? correction.at : undefined;
  const marks = verdicts.filter((v) => v.status !== "none" && v.start != null && v.end != null).sort((a, b) => (a.start ?? 0) - (b.start ?? 0));
  const blocked = marks.find((v) => v.blocked && (cut == null || (v.end ?? 0) <= cut));

  const render = (from: number, to: number, unsaidFrom?: number) => {
    const out: ReactNode[] = [];
    let pos = from;
    for (const v of marks) {
      const a = v.start ?? 0;
      const b = v.end ?? 0;
      if (a < pos || b > to) continue;
      if (unsaidFrom != null && a >= unsaidFrom) break;
      if (a > pos) out.push(<span key={`t${pos}`}>{text.slice(pos, a)}</span>);
      out.push(<Mark key={v.id} v={v} text={text.slice(a, b)} />);
      pos = b;
    }
    if (unsaidFrom != null && pos < to) {
      out.push(<span key="unsaid" className="text-muted-foreground/70 line-through decoration-muted-foreground/40" title="Jamais dit au client : le Garde-fou a arrêté la phrase">{text.slice(pos, to)}</span>);
    } else if (pos < to) out.push(<span key={`t${pos}`}>{text.slice(pos, to)}</span>);
    return out;
  };

  return (
    <>
      {render(0, cut ?? text.length, blocked ? blocked.end : undefined)}
      {cut != null ? (
        <span className="mt-1.5 block border-l-2 border-agent-guard pl-2 animate-guard-in">
          <span className="mb-0.5 flex items-center gap-1 text-[10.5px] font-medium uppercase tracking-[.08em] text-agent-guard">
            <CornerDownRight size={11} strokeWidth={2} />Corrigée depuis l'ERP
          </span>
          {render(cut, text.length)}
        </span>
      ) : null}
    </>
  );
}

/** Per-turn chip: « Garde-fou ✓ 3 ✗ 1 ». */
export function GuardChip({ verdicts = [] }: { verdicts?: GuardVerdict[] }) {
  const checked = verdicts.filter((v) => v.status !== "none");
  if (!checked.length) return null;
  const n = (s: Shown) => checked.filter((v) => v.status === s).length;
  return (
    <span className="inline-flex h-[20px] items-center gap-1.5 rounded-[3px] border border-agent-guard/35 px-1.5 text-[11px] text-secondary-foreground" title="Le Garde-fou a vérifié ces phrases avec Jev, contre l'ERP">
      <ShieldCheck size={12} strokeWidth={1.9} className="text-agent-guard" />
      {n("verified") ? <span className="num text-success">✓ {n("verified")}</span> : null}
      {n("contradicted") ? <span className="num text-destructive">✗ {n("contradicted")}</span> : null}
      {n("unverified") ? <span className="num text-warning">? {n("unverified")}</span> : null}
      {n("unchecked") ? <span className="num text-muted-foreground">⏱ {n("unchecked")}</span> : null}
    </span>
  );
}

// ── the panel: counters + latest verdicts ──

function VerdictRow({ v, fresh }: { v: GuardVerdict; fresh?: boolean }) {
  const s = STATUS[statusOf(v)];
  const Icon = s.icon;
  return (
    <Hover v={v} className={cn("flex cursor-help gap-2 px-4 py-2 outline-none hover:bg-muted/50 focus-visible:bg-muted/50", fresh && "animate-guard-in", v.blocked && "bg-destructive/[.05]")}>
      <span className={cn("mt-px flex h-5 w-5 shrink-0 items-center justify-center rounded-[3px]", s.soft)}><Icon size={12} strokeWidth={2} className={s.text} /></span>
      <span className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span className={cn("line-clamp-2 text-[12px] leading-[17px]", v.blocked && "text-muted-foreground line-through decoration-destructive/60")}>« {v.sentence} »</span>
        <span className="flex min-w-0 items-center gap-1 text-[11px] text-muted-foreground">
          {v.blocked ? <span className="shrink-0 font-medium text-destructive">Bloquée ·</span> : null}
          {v.source ? (
            <span className="truncate">← {v.source.label}{v.source.values[0] ? <span className="num font-mono text-secondary-foreground"> · {v.source.values[0]}</span> : null}</span>
          ) : (
            <span className="truncate">{labelOf(v)}</span>
          )}
        </span>
      </span>
      {v.ms != null ? <span className="num shrink-0 pt-px font-mono text-[10.5px] text-muted-foreground">{v.ms} ms</span> : null}
    </Hover>
  );
}

export function GuardPanel({ verdicts, checking, className, onDemo, demoDisabled }: {
  verdicts: GuardVerdict[]; checking?: string; className?: string;
  /** « Provoquer une erreur »: the next answer states one wrong figure on purpose (demo), the guard must catch it */
  onDemo?: () => void; demoDisabled?: boolean;
}) {
  const checked = verdicts.filter((v) => v.status !== "none");
  const ms = checked.map((v) => v.ms).filter((x): x is number => x != null).sort((a, b) => a - b);
  const median = ms.length ? ms[Math.floor(ms.length / 2)] : undefined;
  const mode = verdicts[0]?.mode;
  const stat = (label: string, value: ReactNode, cls?: string) => (
    <div className="flex min-w-0 flex-col gap-0.5 border-r border-border/70 px-3 py-2 last:border-r-0">
      <span className={cn("num text-[17px] font-semibold leading-none", cls)}>{value}</span>
      <span className="truncate text-[10.5px] text-muted-foreground">{label}</span>
    </div>
  );
  return (
    <div className={cn("flex min-h-0 flex-col border-b border-border", className)}>
      <div className="flex h-10 shrink-0 items-center gap-2 px-4">
        <span className="flex h-6 w-6 items-center justify-center rounded-[4px] bg-agent-guard/10"><ShieldCheck size={14} strokeWidth={1.9} className="text-agent-guard" /></span>
        <div className="flex min-w-0 flex-col">
          <span className="text-[13px] font-semibold leading-tight">Garde-fou</span>
          <span className="truncate text-[11px] leading-tight text-muted-foreground">
            {checking ? <span className="text-agent-guard">Jev vérifie « {checking.replace(/^vérifie « |»$/g, "")}…</span> : "Jev vérifie chaque phrase contre l'ERP"}
          </span>
        </div>
        {mode && mode !== "off" ? (
          <span className={cn("ml-auto inline-flex h-[20px] shrink-0 items-center gap-1.5 rounded-[3px] border px-1.5 text-[11px]", mode === "block" ? "border-agent-guard/40 text-secondary-foreground" : "border-border text-muted-foreground")}
            title={mode === "block" ? "Une phrase contredite n'est jamais dite : l'agent se corrige" : "Observation : les verdicts s'affichent, rien n'est bloqué"}>
            <span className={cn("h-1.5 w-1.5 rounded-full", mode === "block" ? "bg-agent-guard" : "bg-muted-foreground/50")} />
            {mode === "block" ? "Blocage actif" : "Observation"}
          </span>
        ) : null}
      </div>
      {onDemo ? (
        <div className="flex shrink-0 items-center gap-2 border-t border-border/70 px-4 py-1.5">
          <Button size="sm" variant="ghost" icon={FlaskConical} onClick={onDemo} disabled={demoDisabled}
            title="Démo : la prochaine réponse annonce volontairement un chiffre faux. Le Garde-fou doit l'arrêter avant que le client l'entende.">
            Provoquer une erreur
          </Button>
          <span className="truncate text-[11px] text-muted-foreground">démo : un chiffre faux, à attraper</span>
        </div>
      ) : null}
      <div className="grid shrink-0 grid-cols-4 border-t border-border/70">
        {stat("vérifiées", checked.length)}
        {stat("conformes", checked.filter((v) => v.status === "verified").length, "text-success")}
        {stat("bloquées", checked.filter((v) => v.blocked || v.status === "contradicted").length, checked.some((v) => v.status === "contradicted") ? "text-destructive" : undefined)}
        {stat("Jev médian", median != null ? <>{median}<span className="text-[11px] font-normal text-muted-foreground"> ms</span></> : "—")}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto border-t border-border/70">
        {checked.length === 0 ? (
          <p className="px-4 py-3 text-[12px] text-muted-foreground">
            Chaque phrase de l'agent qui annonce un prix, un stock, une date ou une remise est vérifiée ici par Jev, contre l'ERP, avant d'être dite.
          </p>
        ) : (
          <div className="flex flex-col divide-y divide-border/60">
            {checked.slice(0, 40).map((v) => <VerdictRow key={v.id} v={v} fresh />)}
          </div>
        )}
      </div>
    </div>
  );
}

// ── the moment a sentence is blocked ──

export function GuardAlertBanner({ alert, onClose }: { alert?: { key: number; sentence: string; erp: string[]; source?: string }; onClose: () => void }) {
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    if (!alert) return;
    const t = window.setTimeout(() => close.current(), 12_000);
    return () => window.clearTimeout(t);
  }, [alert?.key]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!alert) return null;
  return (
    <div key={alert.key} role="status" className="relative flex shrink-0 items-start gap-2.5 overflow-hidden border-b border-destructive/30 px-5 py-2.5 text-[12.5px] animate-guard-flash">
      <span className="mt-px flex h-6 w-6 shrink-0 items-center justify-center rounded-[4px] bg-destructive/15 animate-stamp"><ShieldX size={14} strokeWidth={2} className="text-destructive" /></span>
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span><span className="font-semibold">Garde-fou : phrase arrêtée avant d'être dite.</span> <span className="text-secondary-foreground">Le client ne l'a jamais entendue, l'agent se corrige.</span></span>
        <span className="truncate text-muted-foreground line-through decoration-destructive/60">« {alert.sentence} »</span>
        {alert.erp.length || alert.source ? (
          <span className="flex min-w-0 flex-wrap items-center gap-1 text-[11.5px]">
            <span className="text-muted-foreground">L'ERP dit</span>
            {alert.source ? <span className="font-medium">{alert.source}</span> : null}
            {alert.erp.map((x) => <span key={x} className="num rounded-[3px] bg-success/10 px-1.5 py-px font-mono text-[11px] text-success">{x}</span>)}
          </span>
        ) : null}
      </div>
      <button type="button" onClick={onClose} aria-label="Fermer" className="rounded-[3px] p-0.5 text-muted-foreground hover:text-foreground"><X size={13} /></button>
    </div>
  );
}

// ── the chat: under an answer ──

export function GuardChatFooter({ verdicts = [] }: { verdicts?: GuardVerdict[] }) {
  const checked = verdicts.filter((v) => v.status !== "none");
  const flagged = checked.some((v) => v.status === "contradicted");
  const [open, setOpen] = useState(false);
  useEffect(() => { if (flagged) setOpen(true); }, [flagged]);
  if (!checked.length) return null;
  return (
    <div className="flex flex-col rounded-[4px] border border-border">
      <button type="button" onClick={() => setOpen((o) => !o)} className="flex h-7 items-center gap-1.5 px-2 text-left text-[11.5px] text-secondary-foreground hover:bg-muted/50" aria-expanded={open}>
        <ShieldCheck size={12} strokeWidth={1.9} className="shrink-0 text-agent-guard" />
        <span className="font-medium">Garde-fou</span>
        <span className="text-muted-foreground">· {checked.length} affirmation{checked.length > 1 ? "s" : ""} vérifiée{checked.length > 1 ? "s" : ""} contre l'ERP</span>
        <span className="ml-auto"><GuardChip verdicts={checked} /></span>
      </button>
      {open ? (
        <div className="flex flex-col divide-y divide-border/60 border-t border-border/70 [&>*]:px-2">
          {checked.map((v) => <VerdictRow key={v.id} v={v} />)}
        </div>
      ) : null}
    </div>
  );
}


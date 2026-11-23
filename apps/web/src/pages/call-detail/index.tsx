/**
 * Compte rendu d'appel — written by the Rapporteur at hang-up (AI service, app/orchestra/reporter.py).
 *  - Hero: headline, summary, outcome / mood / language, and the latency the caller actually heard next to the
 *    single-agent reference measured by the simulator (sim --classic).
 *  - KPIs, each tied to the agent that earned it (agent colors: features/calls/agents.ts).
 *  - The orchestra as a Gantt: one lane per agent over the call, turns as alternating bands, ◆ = first sound heard.
 *  - The transcript as a conversation (client left, agent right, filler as a ghost bubble, latency between them).
 *  - Aside: what Leïla must do, orders, what the Greffier wrote, risks / opportunities, review score, cost by agent.
 */
import { useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowUpRight, FileText, Languages, PhoneCall, Sparkles, Timer, TriangleAlert, TrendingUp, Zap, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/cn";
import { Badge, DateLabel, Dot, ErrorState, Money, Page, PageBody, PageHeader, SECTION_LABEL, Section, Skeleton, StatusDot, type Tone } from "@/ui";
import { OUTCOME, useCall, type CallReport, type CallSpan, type CallTurn } from "@/features/calls/api";
import { AGENT, agentOf, type AgentKey, type AgentMeta } from "@/features/calls/agents";
import { fmtMs } from "@/pages/voice/metrics";

/** Same scenarios run with the single voice agent (no orchestra, no filler): `python -m sim --classic`, first word p50. */
const SINGLE_AGENT_MS = 4200;

const usd = (x?: number, d = 3) => (x == null ? "—" : `${x.toLocaleString("fr-FR", { minimumFractionDigits: d, maximumFractionDigits: d })} $`);
const secs = (ms: number) => (ms < 60_000 ? `${Math.round(ms / 1000)} s` : `${Math.floor(ms / 60_000)} min ${String(Math.round((ms % 60_000) / 1000)).padStart(2, "0")}`);
const PRIORITY: Record<string, { tone: Tone; bar: string }> = {
  haute: { tone: "destructive", bar: "bg-destructive" },
  moyenne: { tone: "warning", bar: "bg-warning" },
  basse: { tone: "muted", bar: "bg-muted-foreground/40" },
};
const MOOD: Record<string, Tone> = { calme: "success", content: "success", pressé: "warning", agacé: "warning", mécontent: "destructive" };
const latencyTone = (ms?: number | null): Tone => (ms == null ? "muted" : ms < 1500 ? "success" : ms < 3000 ? "warning" : "destructive");

export default function CallDetailPage() {
  const { id } = useParams();
  const { data: r, isLoading, error, refetch } = useCall(id);
  return (
    <Page>
      <PageHeader
        title={r?.client?.name ?? "Appel"}
        icon={<PhoneCall size={15} strokeWidth={1.75} />}
        breadcrumb={[{ label: "Appels", to: "/calls" }]}
        meta={r ? <span className="text-[12px] text-muted-foreground"><DateLabel date={r.startedAt} format="datetime" /> · {secs(r.durationMs)}</span> : null}
        actions={
          r?.client ? (
            <Link to={`/clients/${r.client.id}`} className="inline-flex items-center gap-1 text-[12.5px] text-muted-foreground hover:text-foreground">
              Fiche client <ArrowUpRight size={12} />
            </Link>
          ) : null
        }
      />
      <PageBody flush>
        {error ? (
          <ErrorState message="Compte rendu introuvable." onRetry={() => void refetch()} />
        ) : isLoading || !r ? (
          <div className="space-y-3 p-6"><Skeleton className="h-7 w-2/3" /><Skeleton className="h-16" /><Skeleton className="h-40" /></div>
        ) : (
          <Report r={r} />
        )}
      </PageBody>
    </Page>
  );
}

/** Layout follows the page's own width (the Assistant panel can take a third of the screen), not the viewport. */
function useWidth() {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(1400);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setW(e.contentRect.width));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w] as const;
}

function Report({ r }: { r: CallReport }) {
  const ai = r.ai ?? {};
  const [ref, w] = useWidth();
  const wide = w >= 1120;
  return (
    <div ref={ref} className="flex min-h-full flex-col">
      <Hero r={r} split={w >= 860} />
      <Kpis r={r} cols={w >= 1000 ? 5 : w >= 620 ? 3 : 2} />
      <div className={cn("flex min-h-0 flex-1", wide ? "flex-row" : "flex-col")}>
        <div className="min-w-0 flex-1 space-y-8 px-6 py-6">
          <Section title="L'orchestre pendant l'appel" actions={<span className="text-[11.5px] text-muted-foreground">survolez une barre pour le détail</span>}>
            <Gantt r={r} />
          </Section>
          <Section title="Conversation" count={r.turns.length}>
            <Transcript r={r} />
          </Section>
        </div>
        <aside className={cn("shrink-0 space-y-7 border-border bg-card px-5 py-6", wide ? "w-[392px] border-l" : "w-full border-t")}>
          <ForLeila items={ai.for_leila ?? []} />
          {r.orders.length ? <Orders r={r} /> : null}
          <Writes r={r} />
          {ai.risks?.length ? <Bullets title="Risques" items={ai.risks} icon={TriangleAlert} cls="text-destructive" /> : null}
          {ai.opportunities?.length ? <Bullets title="Opportunités" items={ai.opportunities} icon={TrendingUp} cls="text-success" /> : null}
          {ai.agent_review ? <Review review={ai.agent_review} /> : null}
          <Cost r={r} />
        </aside>
      </div>
    </div>
  );
}

// ── Hero ──

function Hero({ r, split }: { r: CallReport; split: boolean }) {
  const ai = r.ai ?? {};
  const outcome = ai.outcome ? OUTCOME[ai.outcome] : undefined;
  return (
    <div className={cn("grid border-b border-border bg-gradient-to-b from-violet/[.045] to-transparent", split && "grid-cols-[1fr_360px]")}>
      <div className="min-w-0 px-6 py-6">
        <div className="flex flex-wrap items-center gap-2 text-[10.5px] font-semibold uppercase tracking-[.08em] text-violet">
          <span className="flex h-5 w-5 items-center justify-center rounded-[4px] bg-violet/10"><FileText size={12} strokeWidth={2} /></span>
          Compte rendu · Rapporteur
          {r.source === "sim" ? <span className="rounded-[3px] border border-border px-1.5 py-px font-mono text-[10px] font-normal normal-case tracking-normal text-muted-foreground">appel simulé{r.scenario ? ` · ${r.scenario}` : ""}</span> : null}
        </div>
        <h1 className="mt-2.5 max-w-[900px] text-[22px] font-semibold leading-[1.3] tracking-[-0.015em]">{ai.headline ?? "Appel sans compte rendu"}</h1>
        {ai.summary ? <p className="mt-2.5 max-w-[860px] text-[13.5px] leading-[1.65] text-secondary-foreground">{ai.summary}</p> : null}
        <div className="mt-4 flex flex-wrap items-center gap-2">
          {outcome ? <Chip><StatusDot tone={outcome.tone} label={outcome.label} /></Chip> : null}
          {ai.sentiment?.start || ai.sentiment?.end ? (
            <Chip title={ai.sentiment?.comment}>
              <span className="text-muted-foreground">Humeur</span>
              <Dot tone={MOOD[ai.sentiment?.start ?? ""] ?? "muted"} />{ai.sentiment?.start ?? "?"}
              <span className="text-muted-foreground">→</span>
              <Dot tone={MOOD[ai.sentiment?.end ?? ""] ?? "muted"} />{ai.sentiment?.end ?? "?"}
            </Chip>
          ) : null}
          <Chip>
            <Languages size={12.5} strokeWidth={1.75} className="text-agent-linguist" />
            {r.languageName} · {r.register === "tu" ? "tutoiement" : "vouvoiement"}
          </Chip>
          {(ai.tags ?? []).map((t) => <Badge key={t}>{t}</Badge>)}
        </div>
      </div>
      <LatencyPanel r={r} split={split} />
    </div>
  );
}

function Chip({ children, title }: { children: ReactNode; title?: string }) {
  return <span title={title} className="inline-flex h-[24px] items-center gap-1.5 rounded-[4px] border border-border bg-background px-2 text-[12px] text-secondary-foreground">{children}</span>;
}

function LatencyPanel({ r, split }: { r: CallReport; split: boolean }) {
  const m = r.metrics;
  const rows = [
    { label: "1er son entendu", hint: "filler ou réponse", ms: m.firstSoundP50, cls: "bg-agent-voice" },
    { label: "1re vraie réponse", hint: "phrase de l'agent", ms: m.firstWordP50, cls: "bg-agent-voice/45" },
    { label: "Agent seul", hint: "référence simulateur", ms: SINGLE_AGENT_MS, cls: "bg-muted-foreground/35" },
  ];
  const max = Math.max(...rows.map((x) => x.ms ?? 0), 1);
  const gain = m.firstSoundP50 != null ? SINGLE_AGENT_MS / Math.max(m.firstSoundP50, 1) : null;
  return (
    <div className={cn("border-border px-6 py-6", split ? "border-l" : "border-t")}>
      <div className={SECTION_LABEL}>Latence perçue · médiane</div>
      <div className="mt-2 flex items-baseline gap-2">
        <span className="num text-[34px] font-semibold leading-none tracking-[-0.02em]">{m.firstSoundP50 != null ? fmtMs(m.firstSoundP50) : "—"}</span>
        {gain && gain > 1.2 ? (
          <span className="inline-flex items-center gap-1 rounded-[3px] bg-success/10 px-1.5 py-0.5 text-[11.5px] font-medium text-success">
            <Zap size={11} strokeWidth={2.2} />{gain.toLocaleString("fr-FR", { maximumFractionDigits: 1 })}× plus vite
          </span>
        ) : null}
      </div>
      <div className="mt-1 text-[11.5px] text-muted-foreground">entre la fin de la phrase du client et le premier son</div>
      <div className="mt-4 space-y-2.5">
        {rows.map((x) => (
          <div key={x.label}>
            <div className="flex items-baseline justify-between text-[11.5px]">
              <span className="text-secondary-foreground">{x.label} <span className="text-muted-foreground">· {x.hint}</span></span>
              <span className="num font-mono text-[11px]">{x.ms != null ? fmtMs(x.ms) : "—"}</span>
            </div>
            <div className="mt-1 h-[6px] rounded-[2px] bg-muted/70">
              <div className={cn("h-full rounded-[2px]", x.cls)} style={{ width: `${((x.ms ?? 0) / max) * 100}%` }} />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── KPIs ──

function Kpis({ r, cols }: { r: CallReport; cols: 2 | 3 | 5 }) {
  const m = r.metrics;
  return (
    <div className={cn("grid gap-px border-b border-border bg-border [&>*]:bg-background", { 2: "grid-cols-2 [&>*:last-child]:col-span-2", 3: "grid-cols-3 [&>*:last-child]:col-span-2", 5: "grid-cols-5" }[cols])}>
      <Kpi a={AGENT.voice} label="Tours de parole" value={m.turns} hint={m.voiceToolCalls ? `${m.voiceToolCalls} outils de l'agent vocal` : "aucun outil appelé"} />
      <Kpi a={AGENT.fast} label="Répondus par le Réflexe" value={m.fastTurns} hint={m.fastTurns ? "sans le grand modèle" : "aucun « oui » simple"} />
      <Kpi a={AGENT.scout} label="Faits préchargés" value={m.prefetchedFacts} hint={`${m.scoutToolCalls} lectures ERP en parallèle`} />
      <Kpi a={AGENT.scribe} label="Écritures du Greffier" value={r.writes.filter((w) => w.agent === "scribe").length} hint="hors du chemin de la voix" />
      <Kpi a={AGENT.reporter} label="Coût IA de l'appel" value={usd(m.cost)} hint="tous agents, compte rendu inclus" />
    </div>
  );
}

function Kpi({ a, label, value, hint }: { a: AgentMeta; label: string; value: ReactNode; hint: string }) {
  const Icon = a.icon;
  return (
    <div className="flex min-w-0 items-start gap-3 px-5 py-4" title={`${a.label} : ${a.role}`}>
      <span className={cn("mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-[6px]", a.soft)}>
        <Icon size={16} strokeWidth={1.9} className={a.text} />
      </span>
      <div className="min-w-0">
        <div className="truncate text-[11.5px] text-muted-foreground">{label}</div>
        <div className="num mt-0.5 text-[20px] font-semibold leading-6 tracking-[-0.01em]">{value}</div>
        <div className="mt-0.5 truncate text-[11px] text-muted-foreground">{hint}</div>
      </div>
    </div>
  );
}

// ── Gantt ──

const LANES: AgentKey[] = ["caller", "voice", "fast", "scout", "scribe", "linguist", "reporter"];
const LABEL_W = 112;

type Bar = CallSpan & { lane: AgentKey; warm?: boolean };

function Gantt({ r }: { r: CallReport }) {
  const [hover, setHover] = useState<{ b: Bar; lane: number } | null>(null);
  const { bars, total, lanes } = useMemo(() => {
    const caller: Bar[] = r.turns.filter((t) => t.speechStartMs != null && t.speechEndMs != null).map((t) => ({
      agent: "voice", lane: "caller", label: "Client", task: `parle (tour ${t.n})`, detail: t.caller, turn: t.n, startMs: t.speechStartMs!, endMs: t.speechEndMs!, ok: true,
    }));
    const done: Bar[] = r.spans.filter((s) => s.endMs != null).map((s) => ({ ...s, lane: agentOf(s.agent).key, warm: s.agent === "warmup" }));
    const all = [...caller, ...done];
    const total = Math.max(1000, r.durationMs, ...all.map((s) => s.endMs ?? 0));
    return { bars: all, total, lanes: LANES.filter((k) => all.some((b) => b.lane === k)) };
  }, [r]);

  const pct = (ms: number) => (ms / total) * 100;
  const step = total > 120_000 ? 20_000 : total > 50_000 ? 10_000 : 5_000;
  const ticks = Array.from({ length: Math.floor(total / step) + 1 }, (_, i) => i * step);
  const turns = r.turns.filter((t) => t.speechStartMs != null);
  const bands = turns.map((t, i) => ({ n: t.n, start: t.speechStartMs!, end: turns[i + 1]?.speechStartMs ?? total }));
  const ROW = 30;

  return (
    <div className="rounded-[6px] border border-border bg-card">
      <div className="relative px-4 pb-2 pt-3">
        {/* turn bands + labels */}
        <div className="pointer-events-none absolute bottom-9 right-4 top-3" style={{ left: 16 + LABEL_W }}>
          {bands.map((b, i) => (
            <div key={b.n} className={cn("absolute inset-y-0 border-l border-border/80", i % 2 ? "bg-muted/45" : "")} style={{ left: `${pct(b.start)}%`, width: `${pct(b.end - b.start)}%` }}>
              <span className="num absolute left-1 top-0.5 font-mono text-[9.5px] text-muted-foreground">T{b.n}</span>
            </div>
          ))}
        </div>
        <div className="relative pt-4">
          {lanes.map((k, li) => {
            const a = AGENT[k];
            const Icon = a.icon;
            const mine = bars.filter((b) => b.lane === k);
            return (
              <div key={k} className="flex items-center" style={{ height: ROW }}>
                <span className="flex shrink-0 items-center gap-2 pr-2" style={{ width: LABEL_W }}>
                  <span className={cn("flex h-5 w-5 items-center justify-center rounded-[4px]", a.soft)}><Icon size={12} strokeWidth={1.9} className={a.text} /></span>
                  <span className="truncate text-[11.5px] text-secondary-foreground">{a.label}</span>
                </span>
                <div className="relative h-[10px] flex-1">
                  {mine.map((b, i) => (
                    <span
                      key={i}
                      onMouseEnter={() => setHover({ b, lane: li })}
                      onMouseLeave={() => setHover(null)}
                      className="absolute -inset-y-[8px] cursor-default"
                      style={{ left: `${pct(b.startMs)}%`, width: `max(4px, ${pct((b.endMs ?? b.startMs) - b.startMs)}%)` }}
                    >
                      <span
                        className={cn(
                          "absolute inset-x-0 top-[8px] h-[10px] rounded-[3px] ring-2 ring-card transition-[filter]",
                          a.bg, b.warm && "opacity-40", !b.ok && "bg-destructive",
                          hover?.b === b && "brightness-110 ring-foreground/30",
                        )}
                      />
                    </span>
                  ))}
                  {k === "voice"
                    ? r.turns.map((t) => (t.speechEndMs != null && t.firstSoundMs != null ? (
                        <span
                          key={`fs${t.n}`}
                          className="pointer-events-none absolute top-1/2 z-10 h-[9px] w-[9px] -translate-x-1/2 -translate-y-1/2 rotate-45 rounded-[1px] bg-foreground ring-2 ring-card"
                          style={{ left: `${pct(t.speechEndMs + t.firstSoundMs)}%` }}
                        />
                      ) : null))
                    : null}
                </div>
              </div>
            );
          })}
          {hover ? <GanttTip h={hover} pct={pct} row={ROW} /> : null}
        </div>
        <div className="relative mt-1 h-5" style={{ marginLeft: LABEL_W }}>
          {ticks.map((t) => <span key={t} className="num absolute -translate-x-1/2 font-mono text-[10px] text-muted-foreground first:translate-x-0" style={{ left: `${pct(t)}%` }}>{t / 1000} s</span>)}
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 border-t border-border px-4 py-2.5 text-[11px] text-secondary-foreground">
        {lanes.map((k) => (
          <span key={k} className="inline-flex items-center gap-1.5">
            <span className={cn("h-2 w-3 rounded-[2px]", AGENT[k].bg)} />{AGENT[k].label}
            <span className="num text-muted-foreground">{bars.filter((b) => b.lane === k).length}</span>
          </span>
        ))}
        <span className="inline-flex items-center gap-1.5"><span className="h-[8px] w-[8px] rotate-45 rounded-[1px] bg-foreground" />1er son entendu</span>
        <span className="inline-flex items-center gap-1.5"><span className="h-2 w-3 rounded-[2px] bg-muted" />tour de parole</span>
      </div>
    </div>
  );
}

function GanttTip({ h, pct, row }: { h: { b: Bar; lane: number }; pct: (ms: number) => number; row: number }) {
  const { b } = h;
  const a = AGENT[b.lane];
  const mid = pct((b.startMs + (b.endMs ?? b.startMs)) / 2);
  return (
    <div
      className="pointer-events-none absolute z-20 w-[260px] rounded-[4px] border border-border bg-card px-2.5 py-2 text-[11.5px] shadow-pop animate-fade-in"
      style={{ top: h.lane * row + 16 - 2, left: `calc(${LABEL_W}px + (100% - ${LABEL_W}px) * ${Math.min(0.82, Math.max(0.12, mid / 100))})`, transform: "translate(-50%, -100%)" }}
    >
      <div className="flex items-center gap-1.5">
        <span className={cn("h-2 w-2 rounded-[2px]", a.bg)} />
        <span className="font-medium">{a.label}</span>
        <span className="text-muted-foreground">· {b.task}</span>
        <span className="num ml-auto font-mono text-[10.5px]">{fmtMs((b.endMs ?? b.startMs) - b.startMs)}</span>
      </div>
      {b.detail ? <div className="mt-1 line-clamp-3 text-secondary-foreground">{b.detail}</div> : null}
      <div className="num mt-1 font-mono text-[10px] text-muted-foreground">tour {b.turn} · {fmtMs(b.startMs)} → {fmtMs(b.endMs ?? b.startMs)}{b.ok ? "" : " · erreur"}</div>
    </div>
  );
}

// ── Conversation ──

const FACT_LABEL: Record<string, string> = { client: "fiche client", context: "contexte", stock: "stock", price: "prix", order: "commande", alternatives: "remplaçants", fast: "confirmation" };

function Transcript({ r }: { r: CallReport }) {
  const moments = new Map((r.ai?.moments ?? []).map((m) => [m.turn, m.label]));
  return <div className="space-y-5">{r.turns.map((t) => <Turn key={t.n} t={t} r={r} moment={moments.get(t.n)} />)}</div>;
}

function Turn({ t, r, moment }: { t: CallTurn; r: CallReport; moment?: string }) {
  const calls = r.toolCalls.filter((c) => c.turn === t.n);
  const byAgent = new Map<AgentKey, string[]>();
  for (const c of calls) {
    const k = agentOf(c.agent).key;
    byAgent.set(k, Array.from(new Set([...(byAgent.get(k) ?? []), c.name])));
  }
  const facts = Array.from(new Set(t.prefetched.map((k) => FACT_LABEL[k.split(":")[0]] ?? k.split(":")[0])));
  const fast = t.path === "fast";
  const who = fast ? AGENT.fast : AGENT.voice;
  const WhoIcon = who.icon;
  return (
    <div>
      {moment ? (
        <div className="mb-2 flex items-center gap-2">
          <Sparkles size={11} className="text-violet" />
          <span className="text-[10.5px] font-semibold uppercase tracking-[.07em] text-violet">{moment}</span>
          <span className="h-px flex-1 bg-violet/15" />
        </div>
      ) : null}
      {/* client */}
      <div className="flex items-end gap-2">
        <span className="num w-7 shrink-0 pb-1 font-mono text-[10.5px] text-muted-foreground">T{t.n}</span>
        <div className="max-w-[78%] rounded-[6px] rounded-bl-[2px] bg-muted/70 px-3 py-2 text-[13px] leading-[1.55]">{t.caller}</div>
      </div>
      {/* latency connector */}
      <div className="my-1.5 flex items-center justify-end gap-2 pr-1 text-[10.5px] text-muted-foreground">
        {t.firstSoundMs != null ? (
          <span className="inline-flex items-center gap-1" title="Fin de parole du client → premier son">
            <Timer size={11} strokeWidth={1.9} />
            <Dot tone={latencyTone(t.firstSoundMs)} />
            <span className="num font-mono">1er son {fmtMs(t.firstSoundMs)}</span>
          </span>
        ) : null}
        {t.firstWordMs != null && t.firstWordMs !== t.firstSoundMs ? <span className="num font-mono" title="Fin de parole → première phrase de la réponse">· réponse {fmtMs(t.firstWordMs)}</span> : null}
        {facts.length ? (
          <span title={t.prefetched.join("\n")} className="inline-flex h-[18px] items-center gap-1 rounded-[3px] border border-agent-scout/35 px-1.5 text-secondary-foreground">
            <span className="h-1.5 w-1.5 rounded-full bg-agent-scout" />préchargé : {facts.join(", ")}
          </span>
        ) : null}
      </div>
      {/* agent */}
      <div className="flex flex-col items-end gap-1.5 pl-9">
        {t.filler ? (
          <div className="max-w-[78%] rounded-[6px] border border-dashed border-border px-3 py-1 text-[12.5px] italic text-muted-foreground" title="Filler joué pendant que l'agent réfléchit">
            « {t.filler} »
          </div>
        ) : null}
        <div className={cn("max-w-[78%] rounded-[6px] rounded-br-[2px] border px-3 py-2 text-[13px] leading-[1.55]", fast ? "border-agent-fast/30 bg-agent-fast/[.07]" : "border-agent-voice/25 bg-agent-voice/[.06]")}>
          <div className="mb-0.5 flex items-center gap-1.5 text-[10.5px] font-medium">
            <WhoIcon size={11} strokeWidth={2} className={who.text} />
            <span className="text-secondary-foreground">{fast ? "Réflexe · sans le grand modèle" : "Agent vocal"}</span>
            {t.interrupted ? <span className="text-warning">· interrompu</span> : null}
          </div>
          {t.agent || <span className="text-muted-foreground">—</span>}
        </div>
        {byAgent.size ? (
          <div className="flex max-w-[78%] flex-wrap justify-end gap-x-3 gap-y-1">
            {[...byAgent.entries()].map(([k, names]) => (
              <span key={k} className="inline-flex items-center gap-1.5 text-[11px] text-muted-foreground">
                <span className={cn("h-1.5 w-1.5 rounded-full", AGENT[k].bg)} />
                <span className="text-secondary-foreground">{AGENT[k].label}</span>
                <span className="font-mono text-[10.5px]">{names.join(", ")}</span>
              </span>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}

// ── Aside ──

function ForLeila({ items }: { items: NonNullable<CallReport["ai"]>["for_leila"] & object }) {
  return (
    <Section title="Pour Leïla" count={items.length}>
      {items.length ? (
        <ol className="space-y-2">
          {items.map((a, i) => {
            const p = PRIORITY[a.priority] ?? PRIORITY.basse;
            return (
              <li key={i} className="relative overflow-hidden rounded-[4px] border border-border bg-background py-2.5 pl-4 pr-3">
                <span className={cn("absolute inset-y-0 left-0 w-[3px]", p.bar)} />
                <div className="flex items-start gap-2">
                  <span className="min-w-0 flex-1 text-[13px] font-medium leading-5">{a.action}</span>
                  <StatusDot tone={p.tone} label={a.priority} className="mt-[2px] shrink-0 text-[11px]" />
                </div>
                <div className="mt-1 text-[12px] leading-[1.5] text-muted-foreground">{a.why}</div>
              </li>
            );
          })}
        </ol>
      ) : <p className="text-[12.5px] text-muted-foreground">Rien à reprendre à la main.</p>}
    </Section>
  );
}

function Orders({ r }: { r: CallReport }) {
  return (
    <Section title="Commandes" count={r.orders.length}>
      <div className="space-y-2">
        {r.orders.map((o) => (
          <div key={o.id} className="rounded-[4px] border border-border bg-background">
            <div className="flex items-center gap-2 border-b border-border/70 px-3 py-2">
              <Link to={`/orders/${o.id}`} className="font-mono text-[12px] hover:underline">{o.number ?? o.id}</Link>
              <StatusDot kind="order" value={o.status} className="text-[12px]" />
              <Money value={o.totalHt} className="ml-auto text-[13px] font-semibold" />
            </div>
            <div className="px-3 py-2">
              <div className="text-[11.5px] text-muted-foreground">Livraison <DateLabel date={o.deliveryDate} format="long" /></div>
              <ul className="mt-1.5 space-y-1 text-[12px]">
                {o.lines.map((l) => (
                  <li key={l.lineId} className="flex gap-2">
                    <span className="num w-[56px] shrink-0 text-right font-medium">{l.qty} {l.unit}</span>
                    <span className="truncate text-secondary-foreground">{l.productName}</span>
                  </li>
                ))}
              </ul>
              {o.warnings.length ? (
                <div className="mt-2 space-y-1">
                  {o.warnings.map((w, i) => (
                    <div key={i} className="flex gap-1.5 text-[11.5px] text-secondary-foreground"><TriangleAlert size={12} className="mt-[2px] shrink-0 text-warning" />{w.message}</div>
                  ))}
                </div>
              ) : null}
            </div>
          </div>
        ))}
      </div>
    </Section>
  );
}

function Writes({ r }: { r: CallReport }) {
  const rows = r.writes.filter((w) => w.agent === "scribe");
  const Icon = AGENT.scribe.icon;
  return (
    <Section title="Écrit par le Greffier" count={rows.length}>
      {rows.length ? (
        <ul className="space-y-2.5">
          {rows.map((w, i) => {
            const a = w.args as Record<string, string>;
            return (
              <li key={i} className="flex gap-2.5">
                <span className="mt-[1px] flex h-6 w-6 shrink-0 items-center justify-center rounded-[4px] bg-agent-scribe/10"><Icon size={13} strokeWidth={1.9} className="text-agent-scribe" /></span>
                <div className="min-w-0 text-[12.5px] leading-[1.5]">
                  <div className="font-medium">{a.title}</div>
                  <div className="text-[11.5px] text-muted-foreground">
                    {w.name === "log_client_event" ? `Historique client · ${a.type}${a.severity ? ` · ${a.severity}` : ""}` : `Fiche de contexte · ${a.kind}`}
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
      ) : <p className="text-[12.5px] text-muted-foreground">Rien à consigner pendant cet appel.</p>}
    </Section>
  );
}

function Bullets({ title, items, icon: Icon, cls }: { title: string; items: string[]; icon: LucideIcon; cls: string }) {
  return (
    <Section title={title} count={items.length}>
      <ul className="space-y-2 text-[12.5px] leading-[1.5]">
        {items.map((x, i) => <li key={i} className="flex gap-2"><Icon size={13} strokeWidth={1.9} className={cn("mt-[3px] shrink-0", cls)} />{x}</li>)}
      </ul>
    </Section>
  );
}

function Review({ review }: { review: NonNullable<NonNullable<CallReport["ai"]>["agent_review"]> }) {
  const s = review.score ?? 0;
  const fill = s >= 4 ? "bg-success" : s >= 3 ? "bg-warning" : "bg-destructive";
  return (
    <Section title="Revue de l'agent" actions={s ? <span className="num text-[12px] font-semibold">{s}<span className="text-muted-foreground">/5</span></span> : null}>
      {s ? <div className="mb-3 flex gap-[2px]">{[1, 2, 3, 4, 5].map((i) => <span key={i} className={cn("h-[6px] flex-1 rounded-[2px]", i <= s ? fill : "bg-muted")} />)}</div> : null}
      <ul className="space-y-1.5 text-[12.5px] leading-[1.5]">
        {(review.good ?? []).map((g, i) => <li key={`g${i}`} className="flex gap-2"><span className="mt-[1px] w-3 shrink-0 text-center font-semibold text-success">+</span>{g}</li>)}
        {(review.improve ?? []).map((g, i) => <li key={`i${i}`} className="flex gap-2"><span className="mt-[1px] w-3 shrink-0 text-center font-semibold text-warning">−</span>{g}</li>)}
      </ul>
    </Section>
  );
}

function Cost({ r }: { r: CallReport }) {
  const rows = useMemo(() => {
    const by = new Map<AgentKey, { cost: number; calls: number; models: Set<string> }>();
    for (const [k, u] of Object.entries(r.usage)) {
      const key = agentOf(k).key;
      const cur = by.get(key) ?? { cost: 0, calls: 0, models: new Set<string>() };
      cur.cost += u.cost;
      cur.calls += u.calls;
      cur.models.add(u.model);
      by.set(key, cur);
    }
    return [...by.entries()].sort((a, b) => b[1].cost - a[1].cost);
  }, [r]);
  const total = rows.reduce((s, [, u]) => s + u.cost, 0) || 1;
  return (
    <Section title="Coût par agent" actions={<span className="num text-[12px] font-semibold">{usd(r.metrics.cost)}</span>}>
      <div className="flex h-[10px] gap-[2px] overflow-hidden rounded-[3px]">
        {rows.map(([k, u]) => <span key={k} title={`${AGENT[k].label} · ${usd(u.cost, 4)}`} className={cn("h-full first:rounded-l-[3px] last:rounded-r-[3px]", AGENT[k].bg)} style={{ width: `${(u.cost / total) * 100}%`, minWidth: 3 }} />)}
      </div>
      <div className="mt-3 space-y-1.5">
        {rows.map(([k, u]) => (
          <div key={k} className="flex items-center gap-2 text-[12px]">
            <span className={cn("h-2 w-2 shrink-0 rounded-[2px]", AGENT[k].bg)} />
            <span className="w-[86px] shrink-0 truncate">{AGENT[k].label}</span>
            <span className="min-w-0 flex-1 truncate font-mono text-[10.5px] text-muted-foreground">{[...u.models].join(", ")} · {u.calls}×</span>
            <span className="num w-[40px] shrink-0 text-right text-[11px] text-muted-foreground">{Math.round((u.cost / total) * 100)} %</span>
            <span className="num w-[62px] shrink-0 text-right">{usd(u.cost, 4)}</span>
          </div>
        ))}
      </div>
    </Section>
  );
}

/**
 * Compte rendu d'appel — written by the Rapporteur at hang-up (AI service, app/orchestra/reporter.py).
 * Top: headline, summary, outcome, mood, language. KPIs: latency to first sound / first answer, fast-path turns,
 * prefetched facts, cost. Then the orchestra as a Gantt (who worked when, in parallel with the caller), the transcript
 * with per-turn latency and badges, and on the right: what Leïla must do, orders, what the Greffier wrote, risks, review.
 */
import { Fragment, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowUpRight, Bot, Ear, FileText, Languages, NotebookPen, PhoneCall, Timer, Zap } from "lucide-react";
import { cn } from "@/lib/cn";
import {
  Badge, DateLabel, Dot, ErrorState, Money, Page, PageBody, PageHeader, Section, Skeleton, Stat, StatGrid, StatusDot, type Tone,
} from "@/ui";
import { OUTCOME, useCall, type CallReport, type CallSpan, type CallTurn } from "@/features/calls/api";
import { fmtMs } from "@/pages/voice/metrics";

const usd = (x?: number, d = 3) => (x == null ? "—" : `${x.toLocaleString("fr-FR", { minimumFractionDigits: d, maximumFractionDigits: d })} $`);
const secs = (ms: number) => `${Math.round(ms / 1000)} s`;
const PRIORITY: Record<string, Tone> = { haute: "destructive", moyenne: "warning", basse: "muted" };
const MOOD: Record<string, Tone> = { calme: "success", content: "success", pressé: "warning", agacé: "warning", mécontent: "destructive" };

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

function Report({ r }: { r: CallReport }) {
  const ai = r.ai ?? {};
  const m = r.metrics;
  const outcome = ai.outcome ? OUTCOME[ai.outcome] : undefined;
  return (
    <div className="flex min-h-full flex-col">
      {/* Hero */}
      <div className="border-b border-border px-6 py-5">
        <div className="flex items-center gap-1.5 text-[10.5px] font-semibold uppercase tracking-[.08em] text-violet">
          <FileText size={12} strokeWidth={2} /> Compte rendu · Rapporteur
          {r.source === "sim" ? <span className="ml-1 font-mono font-normal normal-case tracking-normal text-muted-foreground">appel simulé{r.scenario ? ` · ${r.scenario}` : ""}</span> : null}
        </div>
        <h1 className="mt-1.5 max-w-[960px] text-[20px] font-semibold leading-7 tracking-[-0.01em]">{ai.headline ?? "Appel sans compte rendu"}</h1>
        {ai.summary ? <p className="mt-2 max-w-[960px] text-[13.5px] leading-[1.6] text-secondary-foreground">{ai.summary}</p> : null}
        <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1.5">
          {outcome ? <StatusDot tone={outcome.tone} label={outcome.label} /> : null}
          {ai.sentiment?.start || ai.sentiment?.end ? (
            <span className="inline-flex items-center gap-1.5 text-[12.5px] text-secondary-foreground" title={ai.sentiment?.comment}>
              <Dot tone={MOOD[ai.sentiment?.start ?? ""] ?? "muted"} />{ai.sentiment?.start ?? "?"}
              <span className="text-muted-foreground">→</span>
              <Dot tone={MOOD[ai.sentiment?.end ?? ""] ?? "muted"} />{ai.sentiment?.end ?? "?"}
            </span>
          ) : null}
          <span className="inline-flex items-center gap-1.5 text-[12.5px] text-secondary-foreground">
            <Languages size={13} strokeWidth={1.75} className="text-muted-foreground" />
            {r.languageName} · {r.register === "tu" ? "tutoiement" : "vouvoiement"}
          </span>
          {(ai.tags ?? []).map((t) => <Badge key={t}>{t}</Badge>)}
        </div>
      </div>

      {/* KPIs */}
      <StatGrid className="md:grid-cols-6">
        <Stat label="1er son, médiane" icon={Timer} value={m.firstSoundP50 != null ? fmtMs(m.firstSoundP50) : "—"} tone={tone(m.firstSoundP50)} hint={m.firstSoundP90 != null ? `p90 ${fmtMs(m.firstSoundP90)}` : undefined} />
        <Stat label="1re réponse, médiane" icon={Bot} value={m.firstWordP50 != null ? fmtMs(m.firstWordP50) : "—"} hint={m.firstWordP90 != null ? `p90 ${fmtMs(m.firstWordP90)}` : undefined} />
        <Stat label="Tours" icon={PhoneCall} value={m.turns} hint={m.fastTurns ? `${m.fastTurns} par le Réflexe` : "tous par l'agent vocal"} />
        <Stat label="Préchargé par l'Éclaireur" icon={Ear} value={m.prefetchedFacts} tone="violet" hint={`${m.scoutToolCalls} lectures ERP en parallèle`} />
        <Stat label="Outils de l'agent vocal" icon={Zap} value={m.voiceToolCalls} hint={m.turns ? `${(m.voiceToolCalls / m.turns).toLocaleString("fr-FR", { maximumFractionDigits: 1 })} par tour` : undefined} />
        <Stat label="Coût IA de l'appel" value={usd(m.cost)} hint="LLM, tous agents" />
      </StatGrid>

      <div className="flex min-h-0 flex-1 flex-col xl:flex-row">
        <div className="min-w-0 flex-1 space-y-7 px-6 py-5">
          <Section title="L'orchestre pendant l'appel">
            <Gantt r={r} />
          </Section>
          <Section title="Transcription" count={r.turns.length}>
            <Transcript r={r} />
          </Section>
        </div>
        <aside className="w-full shrink-0 space-y-6 border-t border-border bg-card px-5 py-5 xl:w-[380px] xl:border-l xl:border-t-0">
          <Section title="Pour Leïla" count={ai.for_leila?.length ?? 0}>
            {ai.for_leila?.length ? (
              <ol className="space-y-2.5">
                {ai.for_leila.map((a, i) => (
                  <li key={i} className="flex gap-2.5">
                    <span className="num mt-[1px] flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded-full border border-border text-[10.5px] text-muted-foreground">{i + 1}</span>
                    <div className="min-w-0">
                      <div className="text-[13px] font-medium leading-5">{a.action}</div>
                      <div className="mt-0.5 flex items-start gap-1.5 text-[12px] leading-[1.5] text-muted-foreground">
                        <StatusDot tone={PRIORITY[a.priority] ?? "muted"} label={a.priority} className="shrink-0 text-[11.5px]" />
                        <span>{a.why}</span>
                      </div>
                    </div>
                  </li>
                ))}
              </ol>
            ) : <p className="text-[12.5px] text-muted-foreground">Rien à reprendre à la main.</p>}
          </Section>

          {r.orders.length ? (
            <Section title="Commandes" count={r.orders.length}>
              <div className="space-y-3">
                {r.orders.map((o) => (
                  <div key={o.id} className="rounded-[4px] border border-border bg-background px-3 py-2.5">
                    <div className="flex items-center gap-2">
                      <Link to={`/orders/${o.id}`} className="font-mono text-[12px] hover:underline">{o.number ?? o.id}</Link>
                      <StatusDot kind="order" value={o.status} className="text-[12px]" />
                      <Money value={o.totalHt} className="ml-auto text-[12.5px] font-medium" />
                    </div>
                    <div className="mt-1 text-[11.5px] text-muted-foreground">Livraison <DateLabel date={o.deliveryDate} format="long" /></div>
                    <ul className="mt-1.5 space-y-0.5 text-[12px]">
                      {o.lines.map((l) => (
                        <li key={l.lineId} className="flex gap-2">
                          <span className="num w-[52px] shrink-0 text-right text-secondary-foreground">{l.qty} {l.unit}</span>
                          <span className="truncate">{l.productName}</span>
                        </li>
                      ))}
                    </ul>
                    {o.warnings.length ? <div className="mt-1.5 space-y-0.5">{o.warnings.map((w, i) => <div key={i} className="text-[11.5px] text-warning">{w.message}</div>)}</div> : null}
                  </div>
                ))}
              </div>
            </Section>
          ) : null}

          <Section title="Écrit par le Greffier" count={r.writes.filter((w) => w.agent === "scribe").length}>
            <Writes r={r} />
          </Section>

          {ai.risks?.length ? <Bullets title="Risques" items={ai.risks} tone="destructive" /> : null}
          {ai.opportunities?.length ? <Bullets title="Opportunités" items={ai.opportunities} tone="success" /> : null}

          {ai.agent_review ? (
            <Section title="Revue de l'agent" actions={ai.agent_review.score ? <span className="num text-[12px] font-medium">{ai.agent_review.score}/5</span> : null}>
              <ul className="space-y-1 text-[12.5px] leading-[1.5]">
                {(ai.agent_review.good ?? []).map((g, i) => <li key={`g${i}`} className="flex gap-1.5"><span className="text-success">+</span>{g}</li>)}
                {(ai.agent_review.improve ?? []).map((g, i) => <li key={`i${i}`} className="flex gap-1.5"><span className="text-warning">−</span>{g}</li>)}
              </ul>
            </Section>
          ) : null}

          <Section title="Coût par agent">
            <div className="space-y-1">
              {Object.entries(r.usage).sort((a, b) => b[1].cost - a[1].cost).map(([k, u]) => (
                <div key={k} className="flex items-center gap-2 text-[12px]">
                  <span className={cn("h-2 w-2 shrink-0 rounded-[2px]", BAR[k] ?? "bg-muted-foreground/40")} />
                  <span className="w-[92px] shrink-0 truncate">{u.label === "warmup" ? "Préchauffage" : u.label}</span>
                  <span className="min-w-0 flex-1 truncate font-mono text-[10.5px] text-muted-foreground">{u.model} · {u.calls} appel{u.calls > 1 ? "s" : ""}</span>
                  <span className="num shrink-0">{usd(u.cost, 4)}</span>
                </div>
              ))}
            </div>
          </Section>
        </aside>
      </div>
    </div>
  );
}

const tone = (ms: number | null) => (ms == null ? undefined : ms < 1500 ? "success" : ms < 3000 ? "warning" : "destructive") as "success" | "warning" | "destructive" | undefined;

function Bullets({ title, items, tone }: { title: string; items: string[]; tone: Tone }) {
  return (
    <Section title={title} count={items.length}>
      <ul className="space-y-1.5 text-[12.5px] leading-[1.5]">
        {items.map((x, i) => <li key={i} className="flex gap-2"><Dot tone={tone} className="mt-[7px]" />{x}</li>)}
      </ul>
    </Section>
  );
}

function Writes({ r }: { r: CallReport }) {
  const rows = r.writes.filter((w) => w.agent === "scribe");
  if (!rows.length) return <p className="text-[12.5px] text-muted-foreground">Rien à consigner pendant cet appel.</p>;
  return (
    <ul className="space-y-2">
      {rows.map((w, i) => {
        const a = w.args as Record<string, string>;
        return (
          <li key={i} className="text-[12.5px] leading-[1.5]">
            <div className="flex items-center gap-1.5">
              <NotebookPen size={12} strokeWidth={1.75} className="shrink-0 text-violet" />
              <span className="font-medium">{a.title}</span>
            </div>
            <div className="pl-[18px] text-[11.5px] text-muted-foreground">
              {w.name === "log_client_event" ? `Historique client · ${a.type}${a.severity ? ` · ${a.severity}` : ""}` : `Fiche de contexte · ${a.kind}`}
            </div>
          </li>
        );
      })}
    </ul>
  );
}

// ── Gantt ──

const ROWS: { key: string; label: string }[] = [
  { key: "caller", label: "Client" },
  { key: "voice", label: "Agent vocal" },
  { key: "scout", label: "Éclaireur" },
  { key: "fast", label: "Réflexe" },
  { key: "scribe", label: "Greffier" },
  { key: "linguist", label: "Linguiste" },
  { key: "reporter", label: "Rapporteur" },
];
const BAR: Record<string, string> = {
  caller: "bg-foreground/25",
  voice: "bg-violet",
  warmup: "bg-violet/35",
  scout: "bg-violet/55",
  fast: "bg-success",
  scribe: "bg-info/70",
  linguist: "bg-warning/70",
  reporter: "bg-muted-foreground/50",
};

function Gantt({ r }: { r: CallReport }) {
  const caller: CallSpan[] = r.turns.filter((t) => t.speechStartMs != null && t.speechEndMs != null).map((t) => ({
    agent: "voice", label: "Client", task: `tour ${t.n}`, detail: t.caller, turn: t.n, startMs: t.speechStartMs!, endMs: t.speechEndMs!, ok: true,
  }));
  const spans = r.spans.filter((s) => s.endMs != null);
  const total = Math.max(1000, r.durationMs, ...spans.map((s) => s.endMs ?? 0), ...caller.map((s) => s.endMs ?? 0));
  const step = total > 120_000 ? 20_000 : total > 50_000 ? 10_000 : 5_000;
  const ticks = Array.from({ length: Math.floor(total / step) + 1 }, (_, i) => i * step);
  const pct = (ms: number) => `${(ms / total) * 100}%`;
  const rowSpans = (key: string): (CallSpan & { cls: string })[] =>
    key === "caller"
      ? caller.map((s) => ({ ...s, cls: BAR.caller }))
      : key === "voice"
        ? spans.filter((s) => s.agent === "voice" || s.agent === "warmup").map((s) => ({ ...s, cls: s.task === "préchauffage" ? BAR.warmup : BAR.voice }))
        : spans.filter((s) => s.agent === key).map((s) => ({ ...s, cls: BAR[key] }));
  const rows = ROWS.filter((row) => rowSpans(row.key).length);
  return (
    <div className="rounded-[6px] border border-border bg-card px-3 py-3">
      <div className="relative">
        {rows.map((row) => (
          <div key={row.key} className="flex h-6 items-center">
            <span className="w-[86px] shrink-0 truncate text-[11.5px] text-muted-foreground">{row.label}</span>
            <div className="relative h-3.5 flex-1">
              {r.turns.map((t) => (t.speechEndMs != null ? <span key={t.n} className="absolute inset-y-[-5px] w-px bg-border" style={{ left: pct(t.speechEndMs) }} /> : null))}
              {rowSpans(row.key).map((s, i) => (
                <span
                  key={i}
                  title={`${s.label} · ${s.task}${s.detail ? ` · ${s.detail}` : ""} · ${fmtMs((s.endMs ?? s.startMs) - s.startMs)}`}
                  className={cn("absolute inset-y-0 min-w-[2px] rounded-[2px]", s.cls, !s.ok && "bg-destructive")}
                  style={{ left: pct(s.startMs), width: pct(Math.max(0, (s.endMs ?? s.startMs) - s.startMs)) }}
                />
              ))}
              {row.key === "voice"
                ? r.turns.map((t) => (t.speechEndMs != null && t.firstSoundMs != null ? (
                    <span key={`fs${t.n}`} title={`Tour ${t.n} : 1er son après ${fmtMs(t.firstSoundMs)}${t.filler ? ` (« ${t.filler} »)` : ""}`} className="absolute top-1/2 h-2 w-2 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-card bg-success" style={{ left: pct(t.speechEndMs + t.firstSoundMs) }} />
                  ) : null))
                : null}
            </div>
          </div>
        ))}
        <div className="ml-[86px] mt-1 flex h-7 items-start">
          <div className="relative flex-1">
            {ticks.map((t) => <span key={t} className="num absolute -translate-x-1/2 font-mono text-[10px] text-muted-foreground" style={{ left: pct(t) }}>{t / 1000}s</span>)}
            {r.turns.map((t) => (t.speechEndMs != null ? <span key={t.n} className="num absolute -top-[2px] -translate-x-1/2 font-mono text-[9.5px] text-violet" style={{ left: pct(t.speechEndMs), top: 14 }}>T{t.n}</span> : null))}
          </div>
        </div>
      </div>
      <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-muted-foreground">
        <Legend cls={BAR.caller}>le client parle</Legend>
        <Legend cls={BAR.voice}>agent vocal (modèle + outils)</Legend>
        <Legend cls={BAR.scout}>Éclaireur : préchargement pendant que le client parle</Legend>
        <span className="inline-flex items-center gap-1.5"><span className="h-2 w-2 rounded-full bg-success" />1er son entendu</span>
      </div>
    </div>
  );
}

function Legend({ cls, children }: { cls: string; children: ReactNode }) {
  return <span className="inline-flex items-center gap-1.5"><span className={cn("h-2 w-3 rounded-[2px]", cls)} />{children}</span>;
}

// ── Transcript ──

const FACT_LABEL: Record<string, string> = { client: "fiche client", context: "contexte", stock: "stock", price: "prix", order: "commande", alternatives: "remplaçants", fast: "confirmation" };

function Transcript({ r }: { r: CallReport }) {
  const moments = new Map((r.ai?.moments ?? []).map((m) => [m.turn, m.label]));
  return (
    <div className="divide-y divide-border/70">
      {r.turns.map((t) => (
        <Fragment key={t.n}>
          <TurnBlock t={t} r={r} moment={moments.get(t.n)} />
        </Fragment>
      ))}
    </div>
  );
}

function TurnBlock({ t, r, moment }: { t: CallTurn; r: CallReport; moment?: string }) {
  const calls = r.toolCalls.filter((c) => c.turn === t.n);
  const byAgent = new Map<string, string[]>();
  for (const c of calls) byAgent.set(c.agent, [...(byAgent.get(c.agent) ?? []), c.name]);
  const facts = Array.from(new Set(t.prefetched.map((k) => FACT_LABEL[k.split(":")[0]] ?? k.split(":")[0])));
  return (
    <div className="grid grid-cols-[28px_1fr] gap-x-3 py-3">
      <span className="num pt-[2px] font-mono text-[11px] text-muted-foreground">T{t.n}</span>
      <div className="min-w-0 space-y-1.5">
        {moment ? <div className="text-[10.5px] font-semibold uppercase tracking-[.06em] text-violet">{moment}</div> : null}
        <p className="text-[13px] leading-[1.55]"><span className="mr-1.5 text-[11.5px] text-muted-foreground">Client</span>{t.caller}</p>
        <p className="text-[13px] leading-[1.55]">
          <span className={cn("mr-1.5 text-[11.5px]", t.path === "fast" ? "text-success" : "text-violet")}>{t.path === "fast" ? "Réflexe" : "Agent"}</span>
          {t.filler ? <span className="mr-1 italic text-muted-foreground">« {t.filler} »</span> : null}
          {t.agent || <span className="text-muted-foreground">—</span>}
          {t.interrupted ? <span className="ml-1 text-[11.5px] text-warning">(interrompu)</span> : null}
        </p>
        <div className="flex flex-wrap items-center gap-1">
          {t.firstSoundMs != null ? <Pill tone={t.firstSoundMs < 1500 ? "success" : t.firstSoundMs < 3000 ? "warning" : "destructive"} title="Fin de parole du client → premier son">1er son {fmtMs(t.firstSoundMs)}</Pill> : null}
          {t.firstWordMs != null && t.firstWordMs !== t.firstSoundMs ? <Pill title="Fin de parole → première phrase de la réponse">réponse {fmtMs(t.firstWordMs)}</Pill> : null}
          {t.path === "fast" ? <Pill tone="success" title="Répondu sans le grand modèle">⚡ réflexe</Pill> : null}
          {facts.length ? <Pill tone="violet" title={t.prefetched.join("\n")}>préchargé : {facts.join(", ")}</Pill> : null}
          {[...byAgent.entries()].map(([agent, names]) => (
            <span key={agent} className="text-[11px] text-muted-foreground">
              <span className="text-foreground/70">{AGENT_NAME[agent] ?? agent}</span> {Array.from(new Set(names)).join(", ")}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}

const AGENT_NAME: Record<string, string> = { voice: "Agent vocal :", scout: "Éclaireur :", fast: "Réflexe :", scribe: "Greffier :" };

function Pill({ children, tone, title }: { children: ReactNode; tone?: "success" | "warning" | "destructive" | "violet"; title?: string }) {
  const cls = {
    success: "border-success/30 text-success",
    warning: "border-warning/30 text-warning",
    destructive: "border-destructive/30 text-destructive",
    violet: "border-violet/30 bg-violet/[.06] text-violet",
  }[tone ?? "success"];
  return <span title={title} className={cn("inline-flex h-[18px] items-center rounded-[3px] border px-1.5 font-mono text-[10.5px]", tone ? cls : "border-border text-secondary-foreground")}>{children}</span>;
}


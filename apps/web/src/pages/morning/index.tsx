/**
 * Ce matin — what happened in the night and what Leïla must do, in 10 seconds (spec §8.1, §4.3).
 * For someone who is not fluent in IT: large text, plain French, one verb per action, color = urgency only
 * (red = before the truck leaves, orange = this morning, grey = info).
 *
 * Data: GET /api/shortages/today, GET /api/gates/review, open client events (complaints, gestures for Rachid),
 * the voice agent's overnight ERP activity. Everything refreshes live through the activity stream (qk.morning).
 */
import { useMemo, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQueries } from "@tanstack/react-query";
import { AlertTriangle, ClipboardCheck, Headphones, Moon, PackageX, Phone, PhoneCall, Replace, ShieldCheck, Sparkles } from "lucide-react";
import type { ActivityEvent, ClientEventSummary, GateReviewItem, ShortageOrder } from "@kit/shared";
import { OUTCOME, useCalls, type CallReport, type CallSummary } from "@/features/calls/api";
import { AI_BASE, api } from "@/lib/api";
import { cn } from "@/lib/cn";
import { fmtDate, fmtDateLong, fmtMoney, fmtQty, todayIso } from "@/lib/format";
import { qk, useApiMutation, useClientEventSearch, useEvents, useGateReview, useShortagesToday } from "@/lib/queries";
import { Button, Card, EmptyState, ErrorState, Page, PageBody, PageHeader, Section, Skeleton, TONE_DOT, TONE_TEXT, type Tone } from "@/ui";

/** The night starts at 20:00 the day before the business "today". */
function nightStart(today: string): number {
  const d = new Date(`${today}T20:00:00`);
  d.setDate(d.getDate() - 1);
  return d.getTime();
}

const ISSUE_TYPES = new Set(["complaint", "quality_issue", "delivery_issue", "return"]);

/** Calls of the last 14 hours by the real clock (the ERP's "today" is a simulated date; calls are stamped in real time). */
const RECENT_CALLS_MS = 14 * 3600_000;

/** The latest real call (not a sim run) per client, newest first. */
function latestPerClient(calls: CallSummary[]): Map<string, CallSummary> {
  const out = new Map<string, CallSummary>();
  for (const c of [...calls].sort((a, b) => b.startedAt.localeCompare(a.startedAt))) {
    if (c.client?.id && !out.has(c.client.id)) out.set(c.client.id, c);
  }
  return out;
}

export default function MorningPage() {
  const today = todayIso();
  const shortages = useShortagesToday();
  const review = useGateReview();
  const issues = useClientEventSearch({ status: "open", from: shiftDays(today, -1), limit: 100 });
  const gestures = useClientEventSearch({ type: "credit_note", status: "open", q: "Geste à valider", limit: 50 });
  const events = useEvents(200);

  const since = nightStart(today);
  const night = useMemo(
    () => (events.data ?? []).filter((e) => (e.actor === "voice_agent" || e.actor === "ai_chat") && Date.parse(e.at) >= since),
    [events.data, since],
  );
  const complaints = useMemo(() => (issues.data?.items ?? []).filter((e) => ISSUE_TYPES.has(e.type)), [issues.data]);
  const confirmedTonight = night.filter((e) => e.type === "order.confirmed").length;

  // Call reports written by the Rapporteur at hang-up (GET /ai/calls): one line per call, the outcome on each shortage row,
  // and the "pour Leïla" actions of each client's latest call in "À faire".
  const allCalls = useCalls();
  const recentCalls = useMemo(
    () => (allCalls.data ?? []).filter((c) => c.source !== "sim" && Date.now() - Date.parse(c.startedAt) < RECENT_CALLS_MS)
      .sort((a, b) => b.startedAt.localeCompare(a.startedAt)),
    [allCalls.data],
  );
  const lastCall = useMemo(() => latestPerClient(recentCalls), [recentCalls]);
  const withActions = [...lastCall.values()].filter((c) => c.actions > 0);
  const reports = useQueries({
    queries: withActions.map((c) => ({
      queryKey: ["ai", "calls", c.id],
      queryFn: async (): Promise<CallReport> => (await fetch(`${AI_BASE}/calls/${c.id}`)).json(),
      staleTime: 60_000,
    })),
  });
  const followUps = reports.map((r) => r.data).filter((r): r is CallReport => !!r?.ai?.for_leila?.length);

  return (
    <Page>
      <PageHeader title="Ce matin" meta={<span className="text-[13px] capitalize text-muted-foreground">{fmtDateLong(today)}</span>} />
      <PageBody>
        <div className="mx-auto flex max-w-[1100px] flex-col gap-8 text-[15px]">
          <Figures
            items={[
              { label: "appels cette nuit", value: recentCalls.length, loading: allCalls.isLoading, tone: "muted" },
              { label: "commandes confirmées cette nuit", value: confirmedTonight, loading: events.isLoading, tone: "muted" },
              { label: "à vérifier", value: review.data?.length, loading: review.isLoading, tone: review.data?.length ? "warning" : "muted" },
              { label: "ruptures", value: shortages.data?.counts.orders, loading: shortages.isLoading, tone: shortages.data?.orders.some((o) => o.leavesToday) ? "destructive" : shortages.data?.counts.orders ? "warning" : "muted" },
              { label: "réclamations", value: complaints.length, loading: issues.isLoading, tone: complaints.length ? "warning" : "muted" },
            ]}
          />
          <ToDo review={review.data} complaints={complaints} gestures={gestures.data?.items} followUps={followUps} today={today}
            loading={review.isLoading || issues.isLoading || gestures.isLoading} error={review.isError} onRetry={() => review.refetch()} />
          <Shortages data={shortages.data?.orders} calls={lastCall} loading={shortages.isLoading} error={shortages.isError} onRetry={() => shortages.refetch()} />
          <Night calls={recentCalls} events={night} loading={allCalls.isLoading && events.isLoading} />
        </div>
      </PageBody>
    </Page>
  );
}

// ── 1. Figures ──

function Figures({ items }: { items: { label: string; value?: number; loading: boolean; tone: Tone }[] }) {
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
      {items.map((it) => (
        <Card key={it.label} className="px-4 py-3">
          {it.loading ? <Skeleton className="h-8 w-12" /> : (
            <div className={cn("num text-[32px] font-semibold leading-none", it.tone === "muted" ? "text-foreground" : TONE_TEXT[it.tone])}>{it.value ?? 0}</div>
          )}
          <div className="mt-2 text-[14px] text-muted-foreground">{it.label}</div>
        </Card>
      ))}
    </div>
  );
}

// ── 2. À faire ──

interface Task {
  key: string;
  tone: Tone;
  title: ReactNode;
  lines: ReactNode[];
  action: ReactNode;
}

function ToDo({ review, complaints, gestures, followUps, today, loading, error, onRetry }: {
  review?: GateReviewItem[]; complaints: ClientEventSummary[]; gestures?: ClientEventSummary[]; followUps: CallReport[]; today: string;
  loading: boolean; error: boolean; onRetry: () => void;
}) {
  const navigate = useNavigate();
  const tasks = useMemo<Task[]>(() => {
    const t: Task[] = [];
    for (const d of review ?? []) {
      t.push({
        key: `review-${d.orderId}`,
        tone: urgency(d.deliveryDate, today),
        title: <>Commande de <b>{d.clientName}</b>, livraison {dayWord(d.deliveryDate, today)}</>,
        lines: d.reasons.map((r) => r.message),
        action: <Button variant="primary" icon={ClipboardCheck} onClick={() => navigate(`/orders/${d.orderId}`)}>Vérifier</Button>,
      });
    }
    for (const g of gestures ?? []) {
      t.push({
        key: `gesture-${g.id}`,
        tone: "warning",
        title: <>Geste commercial pour <b>{g.clientName ?? g.clientId}</b> : attend l'accord de Rachid</>,
        lines: [g.title.replace(/^Geste à valider par Rachid\s*:\s*/, ""), g.description].filter(Boolean),
        action: <Button icon={ShieldCheck} onClick={() => navigate(`/clients/${g.clientId}`)}>Montrer à Rachid</Button>,
      });
    }
    for (const c of complaints) {
      t.push({
        key: `complaint-${c.id}`,
        tone: c.severity === "high" ? "destructive" : "warning",
        title: <>Réclamation de <b>{c.clientName ?? c.clientId}</b></>,
        lines: [c.title],
        action: <Button icon={Phone} onClick={() => navigate(`/clients/${c.clientId}`)}>Rappeler</Button>,
      });
    }
    for (const r of followUps) {
      const items = r.ai?.for_leila ?? [];
      const top = items.filter((a) => /haut|urgent/i.test(a.priority));
      t.push({
        key: `call-${r.id}`,
        tone: top.length ? "warning" : "muted",
        title: <>Suite à l'appel de <b>{r.client?.name ?? "un client"}</b> ({hhmm(r.startedAt)})</>,
        lines: (top.length ? top : items).slice(0, 2).map((a) => a.action),
        action: <Button icon={Headphones} onClick={() => navigate(`/calls/${r.id}`)}>Voir l'appel</Button>,
      });
    }
    const rank: Record<Tone, number> = { destructive: 0, warning: 1, violet: 2, info: 2, success: 3, foreground: 3, muted: 4 };
    return t.sort((a, b) => rank[a.tone] - rank[b.tone]);
  }, [review, complaints, gestures, followUps, today, navigate]);

  return (
    <Section title="À faire" count={tasks.length}>
      {error && !review ? <ErrorState onRetry={onRetry} /> : loading && !tasks.length ? <CardsSkeleton /> : tasks.length === 0 ? (
        <EmptyState compact icon={ClipboardCheck} title="Rien à faire pour l'instant" description="Tout ce qui est arrivé cette nuit est en ordre." />
      ) : (
        <div className="flex flex-col gap-2">
          {tasks.map((t) => (
            <Card key={t.key} className="flex flex-col gap-3 px-4 py-3 sm:flex-row sm:items-center">
              <UrgencyDot tone={t.tone} />
              <div className="min-w-0 flex-1">
                <div className="text-[16px] leading-snug">{t.title}</div>
                {t.lines.map((l, i) => <div key={i} className="mt-1 text-[14px] text-muted-foreground">{l}</div>)}
              </div>
              <div className="shrink-0">{t.action}</div>
            </Card>
          ))}
        </div>
      )}
    </Section>
  );
}

// ── 3. Ruptures du jour ──

function Shortages({ data, calls, loading, error, onRetry }: {
  data?: ShortageOrder[]; calls: Map<string, CallSummary>; loading: boolean; error: boolean; onRetry: () => void;
}) {
  const navigate = useNavigate();
  // Calls still to make: decision "call" and no call to this client yet (the latest report says it happened)
  const toCall = (data ?? []).filter((o) => o.decision === "call" && !calls.has(o.clientId));
  return (
    <Section
      title="Ruptures du jour"
      count={data?.length}
      actions={
        // Calls run one at a time on the Voix page (the agent speaks first, PR #27); the row updates when Leïla comes back
        <Button variant="primary" icon={PhoneCall} disabled={!toCall.length} onClick={() => navigate(outboundPath(toCall[0].orderId))}>
          Lancer les appels{toCall.length ? ` (${toCall.length})` : ""}
        </Button>
      }
    >
      {error && !data ? <ErrorState onRetry={onRetry} /> : loading && !data ? <CardsSkeleton /> : !data?.length ? (
        <EmptyState compact icon={PackageX} title="Aucune rupture aujourd'hui" description="Toutes les commandes peuvent partir complètes." />
      ) : (
        <div className="flex flex-col gap-2">{data.map((o) => <ShortageRow key={o.orderId} o={o} call={calls.get(o.clientId)} />)}</div>
      )}
    </Section>
  );
}

function ShortageRow({ o, call }: { o: ShortageOrder; call?: CallSummary }) {
  const navigate = useNavigate();
  const tone: Tone = o.leavesToday ? "destructive" : "warning";
  const canReplace = o.lines.some((l) => l.substitute && l.substitute.available >= l.missingQty);
  const replace = useApiMutation(() => api.shortages.substitute(o.orderId, { note: "Remplacement validé depuis Ce matin" }), {
    success: `Produit remplacé pour ${o.clientName}`,
    invalidate: [qk.morning.all, qk.orders.all],
  });
  return (
    <Card className="flex flex-col gap-3 px-4 py-3 sm:flex-row sm:items-start">
      <UrgencyDot tone={tone} />
      <div className="min-w-0 flex-1">
        <div className="text-[16px] leading-snug">
          <Link to={`/clients/${o.clientId}`} className="font-semibold hover:underline">{o.clientName}</Link>
          {o.contactName ? <span className="text-muted-foreground"> · {o.contactName}</span> : null}
          <span className={cn("ml-2 text-[14px]", TONE_TEXT[tone])}>{o.leavesToday ? "camion de 6 h" : `livraison ${fmtDate(o.deliveryDate)}`}</span>
        </div>
        <ul className="mt-1 flex flex-col gap-0.5 text-[14px]">
          {o.lines.map((l) => (
            <li key={l.lineId}>
              Manque <b>{fmtQty(l.missingQty, l.unit)}</b> de {l.name}
              {l.substitute ? <span className="text-muted-foreground"> → remplaçable par {l.substitute.name} ({fmtMoney(l.substitute.price)})</span>
                : <span className="text-muted-foreground"> → pas de remplacement en stock</span>}
            </li>
          ))}
        </ul>
        <div className="mt-1 text-[14px] text-muted-foreground">{o.reason}</div>
        {call ? <CallLine call={call} /> : null}
      </div>
      <div className="flex shrink-0 flex-col items-stretch gap-2 sm:w-[190px]">
        {o.decision === "auto_substitute" ? (
          <Button variant="primary" icon={Replace} loading={replace.isPending} disabled={!canReplace} onClick={() => replace.mutate(undefined)}>Remplacer</Button>
        ) : (
          <Button variant={call ? "default" : "primary"} icon={PhoneCall} onClick={() => navigate(outboundPath(o.orderId))}>{call ? "Rappeler" : "Appeler"}</Button>
        )}
        {o.phone ? (
          <a href={`tel:${o.phone.replace(/\s/g, "")}`} className="flex items-center gap-1.5 text-[15px] font-medium num hover:underline">
            <Phone size={15} strokeWidth={1.75} /> {o.phone}
          </a>
        ) : null}
      </div>
    </Card>
  );
}

// ── 4. Cette nuit ──

function Night({ calls, events, loading }: { calls: CallSummary[]; events: ActivityEvent[]; loading: boolean }) {
  return (
    <Section title="Cette nuit" count={calls.length || events.length}>
      {loading ? <CardsSkeleton /> : calls.length ? (
        <Card className="divide-y divide-border">
          {calls.map((c) => (
            <Link key={c.id} to={`/calls/${c.id}`} className="flex items-baseline gap-3 px-4 py-2.5 hover:bg-muted/50">
              <span className="num w-12 shrink-0 text-[14px] text-muted-foreground">{hhmm(c.startedAt)}</span>
              <span className="w-[190px] shrink-0 truncate text-[15px] font-medium">{c.client?.name ?? "Client non identifié"}</span>
              <span className={cn("min-w-0 flex-1 text-[15px]", !c.headline && "text-muted-foreground")}>
                {c.headline ?? (Date.now() - Date.parse(c.startedAt) < 5 * 60_000 ? "Résumé en cours d'écriture…" : "Pas de compte rendu pour cet appel")}
              </span>
              <OutcomeLabel outcome={c.outcome} />
            </Link>
          ))}
        </Card>
      ) : events.length ? (
        <>
          <Card className="divide-y divide-border">
            {events.map((e) => (
              <div key={e.id} className="flex items-baseline gap-3 px-4 py-2.5">
                <span className="num w-12 shrink-0 text-[14px] text-muted-foreground">{hhmm(e.at)}</span>
                <Sparkles size={14} strokeWidth={1.75} className="shrink-0 text-violet" />
                <span className="min-w-0 flex-1 text-[15px]">{e.summary}</span>
              </div>
            ))}
          </Card>
          <p className="mt-2 text-[12px] text-muted-foreground">Pas de compte rendu d'appel : voici ce que l'agent a fait dans l'ERP depuis 20 h hier.</p>
        </>
      ) : (
        <EmptyState compact icon={Moon} title="Pas d'appel cette nuit" />
      )}
    </Section>
  );
}

/** "Appelé à 22:40 · Transmis à l'équipe — headline", linking to the call report. */
function CallLine({ call }: { call: CallSummary }) {
  return (
    <Link to={`/calls/${call.id}`} className="mt-2 flex items-baseline gap-2 rounded-[6px] bg-muted/60 px-3 py-2 text-[14px] hover:bg-muted">
      <Headphones size={14} strokeWidth={1.75} className="shrink-0 self-center text-muted-foreground" />
      <span className="shrink-0">Appelé à {hhmm(call.startedAt)}</span>
      <OutcomeLabel outcome={call.outcome} />
      {call.headline ? <span className="min-w-0 text-muted-foreground">— {call.headline}</span> : null}
    </Link>
  );
}

function OutcomeLabel({ outcome }: { outcome?: string }) {
  const o = outcome ? OUTCOME[outcome] : undefined;
  return o ? <span className={cn("shrink-0 text-[13px] font-medium", TONE_TEXT[o.tone])}>{o.label}</span> : null;
}

// ── helpers ──

/** The Voix page starts a call where the agent speaks first about this order's shortage (PR #27). */
const outboundPath = (orderId: string) => `/voice?outbound=${encodeURIComponent(orderId)}`;

function UrgencyDot({ tone }: { tone: Tone }) {
  return (
    <span className="mt-1.5 flex shrink-0 items-center self-start" title={URGENCY_LABEL[tone]}>
      {tone === "destructive" ? <AlertTriangle size={16} strokeWidth={1.75} className={TONE_TEXT.destructive} />
        : <span className={cn("h-2.5 w-2.5 rounded-full", TONE_DOT[tone])} />}
    </span>
  );
}

const URGENCY_LABEL: Partial<Record<Tone, string>> = {
  destructive: "Avant le départ du camion",
  warning: "Ce matin",
  muted: "Pour information",
};

function CardsSkeleton() {
  return (
    <div className="flex flex-col gap-2">
      {Array.from({ length: 3 }).map((_, i) => <Card key={i} className="px-4 py-4"><Skeleton className="h-4 w-2/3" /><Skeleton className="mt-2 h-3 w-1/2" /></Card>)}
    </div>
  );
}

/** red = delivery today (before the truck leaves), orange = tomorrow, grey = later. */
function urgency(delivery: string, today: string): Tone {
  if (delivery <= today) return "destructive";
  if (delivery === shiftDays(today, 1)) return "warning";
  return "muted";
}

function dayWord(delivery: string, today: string): string {
  if (delivery === today) return "aujourd'hui";
  if (delivery === shiftDays(today, 1)) return "demain";
  return `le ${fmtDate(delivery)}`;
}

function shiftDays(iso: string, days: number): string {
  const d = new Date(`${iso}T12:00:00`);
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
}

function hhmm(at: string): string {
  return new Date(at).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
}

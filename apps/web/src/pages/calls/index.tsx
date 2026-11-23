/**
 * Appels — every call handled by the voice agent, with the Rapporteur's headline and the orchestra's numbers.
 * Top: totals over the listed calls (median first sound, share of turns answered by the Réflexe, actions for Leïla, cost).
 * Rows: mood dot + language, headline, outcome, actions, first sound as a small bar against 3 s.
 */
import { useMemo, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { ClipboardList, PhoneCall } from "lucide-react";
import { cn } from "@/lib/cn";
import { DateLabel, Dot, EmptyState, ErrorState, Page, PageBody, PageHeader, Skeleton, StatusDot, Table, TBody, TD, TH, THead, TR, type Tone } from "@/ui";
import { OUTCOME, useCalls, type CallSummary } from "@/features/calls/api";
import { AGENT, type AgentMeta } from "@/features/calls/agents";
import { fmtMs } from "@/pages/voice/metrics";

const usd = (x?: number, d = 3) => (x == null ? "—" : `${x.toLocaleString("fr-FR", { minimumFractionDigits: d, maximumFractionDigits: d })} $`);
const MOOD: Record<string, Tone> = { calme: "success", content: "success", pressé: "warning", agacé: "warning", mécontent: "destructive" };
const median = (xs: number[]) => {
  const v = [...xs].sort((a, b) => a - b);
  return v.length ? v[Math.floor(v.length / 2)] : null;
};

export default function CallsPage() {
  const { data, isLoading, error, refetch } = useCalls();
  const nav = useNavigate();
  return (
    <Page>
      <PageHeader title="Appels" icon={<PhoneCall size={15} strokeWidth={1.75} />} count={data?.length} meta={<span className="text-[12px] text-muted-foreground">comptes rendus du Rapporteur</span>} />
      <PageBody flush>
        {error ? (
          <ErrorState message="Service IA injoignable." onRetry={() => void refetch()} />
        ) : isLoading ? (
          <div className="space-y-2 p-5">{Array.from({ length: 6 }, (_, i) => <Skeleton key={i} className="h-8" />)}</div>
        ) : !data?.length ? (
          <EmptyState icon={PhoneCall} title="Aucun appel pour l'instant" description="Passez un appel sur la page Voix puis raccrochez : le compte rendu apparaît ici." />
        ) : (
          <>
            <Totals calls={data} />
            <Table fixed className="min-w-[1080px]">
              <THead>
                <tr>
                  <TH className="w-[140px]">Date</TH>
                  <TH className="w-[200px]">Client</TH>
                  <TH>Compte rendu</TH>
                  <TH className="w-[160px]">Issue</TH>
                  <TH align="right" className="w-[84px]">Pour Leïla</TH>
                  <TH className="w-[140px]">1er son</TH>
                  <TH align="right" className="w-[72px]">Tours</TH>
                  <TH align="right" className="w-[84px]">Coût IA</TH>
                </tr>
              </THead>
              <TBody>
                {data.map((c) => <Row key={c.id} c={c} onOpen={() => nav(`/calls/${c.id}`)} />)}
              </TBody>
            </Table>
          </>
        )}
      </PageBody>
    </Page>
  );
}

function Row({ c, onOpen }: { c: CallSummary; onOpen: () => void }) {
  const o = c.outcome ? OUTCOME[c.outcome] : undefined;
  const fs = c.metrics?.firstSoundP50;
  return (
    <TR onClick={onOpen}>
      <TD muted><DateLabel date={c.startedAt} format="datetime" /></TD>
      <TD>
        <div className="flex min-w-0 items-center gap-2">
          <span title={c.sentiment ? `Humeur en fin d'appel : ${c.sentiment}` : undefined}><Dot tone={MOOD[c.sentiment ?? ""] ?? "muted"} /></span>
          <span className="truncate">{c.client?.name ?? <span className="text-muted-foreground">non identifié</span>}</span>
          {c.language && c.language !== "fr" ? <span className="shrink-0 rounded-[3px] border border-agent-linguist/35 px-1 font-mono text-[10px] uppercase text-secondary-foreground">{c.language}</span> : null}
        </div>
      </TD>
      <TD className="truncate">
        {c.source === "sim" ? <span className="mr-1.5 rounded-[3px] border border-border px-1 font-mono text-[10px] text-muted-foreground">sim</span> : null}
        {c.headline ?? <span className="text-muted-foreground">—</span>}
      </TD>
      <TD>{o ? <StatusDot tone={o.tone} label={o.label} /> : null}</TD>
      <TD align="right">
        {c.actions ? (
          <span className="num inline-flex h-[20px] min-w-[22px] items-center justify-center rounded-[3px] bg-violet/10 px-1.5 text-[11.5px] font-medium text-violet">{c.actions}</span>
        ) : <span className="text-muted-foreground">—</span>}
      </TD>
      <TD>{fs != null ? <LatencyBar ms={fs} /> : <span className="text-muted-foreground">—</span>}</TD>
      <TD align="right" numeric>
        {c.metrics?.fastTurns ? <span className="mr-1.5 text-[10.5px] text-agent-fast" title={`${c.metrics.fastTurns} par le Réflexe`}>⚡{c.metrics.fastTurns}</span> : null}
        {c.metrics?.turns ?? "—"}
      </TD>
      <TD align="right" numeric muted>{usd(c.metrics?.cost)}</TD>
    </TR>
  );
}

function LatencyBar({ ms }: { ms: number }) {
  const cls = ms < 1500 ? "bg-success" : ms < 3000 ? "bg-warning" : "bg-destructive";
  return (
    <div className="flex items-center gap-2">
      <div className="h-[5px] w-[64px] shrink-0 rounded-[2px] bg-muted">
        <div className={cn("h-full rounded-[2px]", cls)} style={{ width: `${Math.min(100, (ms / 3000) * 100)}%` }} />
      </div>
      <span className="num font-mono text-[11.5px]">{fmtMs(ms)}</span>
    </div>
  );
}

function Totals({ calls }: { calls: CallSummary[] }) {
  const t = useMemo(() => {
    const ms = calls.map((c) => c.metrics?.firstSoundP50).filter((x): x is number => x != null);
    const turns = calls.reduce((s, c) => s + (c.metrics?.turns ?? 0), 0);
    const fast = calls.reduce((s, c) => s + (c.metrics?.fastTurns ?? 0), 0);
    const cost = calls.reduce((s, c) => s + (c.metrics?.cost ?? 0), 0);
    return {
      sims: calls.filter((c) => c.source === "sim").length,
      firstSound: median(ms),
      turns,
      fast,
      actions: calls.reduce((s, c) => s + (c.actions ?? 0), 0),
      withActions: calls.filter((c) => c.actions).length,
      cost: calls.length ? cost / calls.length : 0,
    };
  }, [calls]);
  return (
    <div className="grid grid-cols-2 divide-x divide-y divide-border border-b border-border md:grid-cols-5 md:divide-y-0">
      <Tile a={AGENT.caller} label="Appels" value={calls.length} hint={t.sims ? `dont ${t.sims} simulés` : "tous en direct"} />
      <Tile a={AGENT.voice} label="1er son médian" value={t.firstSound != null ? fmtMs(t.firstSound) : "—"} hint="fin de phrase → son" />
      <Tile a={AGENT.fast} label="Réflexe" value={t.turns ? `${Math.round((t.fast / t.turns) * 100)} %` : "—"} hint={`${t.fast} tours sur ${t.turns}`} />
      <Tile icon={ClipboardList} label="Pour Leïla" value={t.actions} hint={`actions sur ${t.withActions} appel${t.withActions > 1 ? "s" : ""}`} accent />
      <Tile a={AGENT.reporter} label="Coût IA moyen" value={usd(t.cost)} hint="par appel, tous agents" />
    </div>
  );
}

function Tile({ a, icon, label, value, hint, accent }: { a?: AgentMeta; icon?: typeof ClipboardList; label: string; value: ReactNode; hint: string; accent?: boolean }) {
  const Icon = a?.icon ?? icon ?? PhoneCall;
  return (
    <div className="flex min-w-0 items-start gap-3 px-4 py-4">
      <span className={cn("mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-[6px]", accent ? "bg-violet/10" : a?.soft)}>
        <Icon size={16} strokeWidth={1.9} className={accent ? "text-violet" : a?.text} />
      </span>
      <div className="min-w-0">
        <div className="truncate text-[11.5px] text-muted-foreground">{label}</div>
        <div className="num mt-0.5 text-[20px] font-semibold leading-6 tracking-[-0.01em]">{value}</div>
        <div className="mt-0.5 truncate text-[11px] text-muted-foreground">{hint}</div>
      </div>
    </div>
  );
}

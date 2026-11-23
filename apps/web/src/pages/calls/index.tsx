/** Appels — every call handled by the voice agent, with the Rapporteur's headline and the orchestra's numbers. */
import { useNavigate } from "react-router-dom";
import { PhoneCall } from "lucide-react";
import { cn } from "@/lib/cn";
import { DateLabel, EmptyState, ErrorState, Page, PageBody, PageHeader, Skeleton, StatusDot, Table, TBody, TD, TH, THead, TR } from "@/ui";
import { OUTCOME, useCalls } from "@/features/calls/api";
import { fmtMs } from "@/pages/voice/metrics";

const usd = (x?: number) => (x == null ? "—" : `${x.toLocaleString("fr-FR", { minimumFractionDigits: 3, maximumFractionDigits: 3 })} $`);

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
          <Table fixed>
            <THead>
              <tr>
              <TH className="w-[160px]">Date</TH>
              <TH className="w-[200px]">Client</TH>
              <TH>Compte rendu</TH>
              <TH className="w-[170px]">Issue</TH>
              <TH align="right" className="w-[80px]">Actions</TH>
              <TH align="right" className="w-[90px]">1er son</TH>
              <TH align="right" className="w-[70px]">Tours</TH>
              <TH align="right" className="w-[90px]">Coût IA</TH>
              </tr>
            </THead>
            <TBody>
              {data.map((c) => {
                const o = c.outcome ? OUTCOME[c.outcome] : undefined;
                return (
                  <TR key={c.id} onClick={() => nav(`/calls/${c.id}`)}>
                    <TD muted><DateLabel date={c.startedAt} format="datetime" /></TD>
                    <TD className="truncate">{c.client?.name ?? <span className="text-muted-foreground">non identifié</span>}</TD>
                    <TD className="truncate">
                      {c.source === "sim" ? <span className="mr-1.5 rounded-[3px] border border-border px-1 font-mono text-[10px] text-muted-foreground">sim</span> : null}
                      {c.headline ?? <span className="text-muted-foreground">—</span>}
                    </TD>
                    <TD>{o ? <StatusDot tone={o.tone} label={o.label} /> : null}</TD>
                    <TD align="right" numeric className={cn(c.actions ? "text-violet" : "text-muted-foreground")}>{c.actions || "—"}</TD>
                    <TD align="right" numeric>{c.metrics?.firstSoundP50 != null ? fmtMs(c.metrics.firstSoundP50) : "—"}</TD>
                    <TD align="right" numeric>{c.metrics?.turns ?? "—"}</TD>
                    <TD align="right" numeric muted>{usd(c.metrics?.cost)}</TD>
                  </TR>
                );
              })}
            </TBody>
          </Table>
        )}
      </PageBody>
    </Page>
  );
}

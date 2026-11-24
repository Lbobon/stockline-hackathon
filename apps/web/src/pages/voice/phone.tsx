/**
 * Voix → Téléphone: the PC side of the demo phone line. Pick the client the phone plays, "Faire sonner": the phone
 * (/phone, opened on a real phone through the tunnel) rings; once answered, the agent calls that client to take its
 * order and the whole conversation shows here live (mirrored from the phone's session, no audio on the PC).
 */
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { PhoneCall, PhoneOff, Smartphone } from "lucide-react";
import type { ClientSummary } from "@kit/shared";
import { api } from "@/lib/api";
import { Button, Dot, Page, PageHeader, Select } from "@/ui";
import { useVoiceSession } from "@/features/assistant/useVoiceSession";
import { MetricsBar } from "./metrics";
import { OrchestraStrip, ReportBanner } from "./orchestra";
import { Timeline } from "./timeline";

const EVENT_LABEL: Record<string, string> = {
  connected: "Téléphone connecté",
  disconnected: "Téléphone déconnecté",
  ringing: "Ça sonne…",
  no_phone: "Aucun téléphone en attente sur /phone",
  answered: "Décroché : appel en cours",
  declined: "Refusé",
  cancelled: "Sonnerie annulée",
  hungup: "Raccroché",
};

export function PhoneMode({ modeSwitch }: { modeSwitch: ReactNode }) {
  const v = useVoiceSession({ watch: true });
  const [clients, setClients] = useState<ClientSummary[]>([]);
  const [clientId, setClientId] = useState("cli_0002");
  // Tomorrow's Radar: those clients get the Radar call (event + quantities), the others a plain order-taking call.
  const [radar, setRadar] = useState<Map<string, string>>(new Map());

  useEffect(() => {
    api.clients.list().then((cs) => setClients(cs.filter((c) => c.status === "active"))).catch(() => undefined);
    fetch("/api/radar/forecast")
      .then((r) => r.json())
      .then((f: { clients?: { clientId: string; status: string; call?: boolean; reason?: string }[] }) => {
        const rows = (f.clients ?? []).filter((r) => (r.status === "predicted" || r.status === "upsell") && r.call !== false);
        setRadar(new Map(rows.map((r) => [r.clientId, r.reason ?? ""])));
        if (rows[0]) setClientId(rows[0].clientId);
      })
      .catch(() => undefined);
  }, []);
  const ordered = useMemo(() => [...clients].sort((a, b) => Number(radar.has(b.id)) - Number(radar.has(a.id))), [clients, radar]);

  const client = useMemo(() => clients.find((c) => c.id === clientId), [clients, clientId]);
  const phones = v.phoneLine?.phones ?? 0;
  const event = v.phoneLine?.event;
  const ringing = event === "ringing";
  const phoneUrl = `${location.origin}/phone`;

  return (
    <Page>
      <PageHeader
        title="Voix"
        meta={
          <span className="flex items-center gap-1.5 text-[12px] text-muted-foreground">
            <Dot tone={phones ? "success" : "warning"} pulse={!phones} />
            {phones ? `${phones} téléphone${phones > 1 ? "s" : ""} en attente` : "Aucun téléphone connecté"}
          </span>
        }
        tabs={modeSwitch}
      />
      <div className="flex min-h-0 flex-1 flex-col">
        <div className="flex shrink-0 items-center gap-3 border-b border-border px-5 py-2.5 text-[12.5px]">
          <Smartphone size={15} strokeWidth={1.75} className="shrink-0 text-muted-foreground" />
          {event ? <span className="font-medium">{EVENT_LABEL[event] ?? event}</span> : null}
          <span className="text-muted-foreground">
            Sur le téléphone : <span className="font-mono text-foreground">{phoneUrl}</span> → « Activer la ligne ». Le téléphone joue
            {client ? <> <span className="text-foreground">{client.name}</span></> : " le client choisi"} ;
            {radar.has(clientId) ? <> l'agent l'appelle avec la prévision du Radar{radar.get(clientId) ? <> : <span className="text-foreground">{radar.get(clientId)}</span></> : null}.</> : " l'agent l'appelle pour sa commande."}
          </span>
          {/* Here, not in the header: the header keeps its room for the four mode tabs */}
          <div className="ml-auto flex shrink-0 items-center gap-2">
            <Select size="sm" value={clientId} onChange={setClientId} aria-label="Le client que joue le téléphone" className="w-[240px]"
              options={ordered.length ? ordered.map((c) => ({ value: c.id, label: radar.has(c.id) ? `Radar · ${c.name}` : c.name })) : [{ value: clientId, label: clientId }]} />
            {ringing ? (
              <Button size="sm" variant="ghost" icon={PhoneOff} onClick={v.cancelRing}>Annuler</Button>
            ) : (
              <Button size="sm" icon={PhoneCall} disabled={!phones || v.conn !== "ready"} onClick={() => v.ring(clientId, client?.name ?? clientId)}>
                Faire sonner
              </Button>
            )}
          </div>
        </div>
        <ReportBanner report={v.report} onClose={v.dismissReport} />
        <MetricsBar turns={v.turns} />
        <OrchestraStrip agents={v.agents} lanes={v.lanes} profile={v.profile} notes={v.notes} />
        <Timeline turns={v.turns} activity={v.activity} level={0} />
      </div>
    </Page>
  );
}

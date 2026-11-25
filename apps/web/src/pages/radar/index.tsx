/**
 * Radar de demande (/radar): who will order for the date, and why (match, cold snap, weddings, Christmas market).
 * « Lancer les appels » rings every predicted / upsell client in parallel; the map lights up as they answer and the
 * trucks' routes redraw with every new order. Then « Lancer les livraisons » replays the drivers' stops.
 * Data: features/radar/useRadar (live backend, or the bundled sample + offline simulator).
 */
import { useMemo, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { PhoneCall, Radar, RotateCcw, Truck } from "lucide-react";
import { Button, Page, PageHeader, Spinner } from "@/ui";
import { cn } from "@/lib/cn";
import { fmtMoney } from "@/lib/format";
import { useCampaign, useForecast } from "@/features/radar/useRadar";
import type { Forecast } from "@/features/radar/types";
import { RadarMap } from "./RadarMap";
import { ClientDetail, ClientList, Count, EventStrip, NotCalledList } from "./Panel";
import { ActivityFeed, DeliveryCards } from "./Live";
import { useRoads } from "@/features/radar/roads";
import { RadarSwitch, SupplyView } from "./Supply";
import { AheadView, HorizonSwitch } from "./Ahead";

const DATE = "2026-11-25";

// The radar opens on supply: we predict the spike in sales, then call the suppliers in advance. The client calls are the
// second tab (?view=clients).
type View = "clients" | "supply";
const urlView = (): View => (new URLSearchParams(location.search).get("view") === "clients" ? "clients" : "supply");
const urlHorizon = () => (new URLSearchParams(location.search).get("horizon") === "ahead" ? "ahead" : "week");

export default function RadarPage() {
  const { data: forecast } = useForecast(DATE);
  const [view, setView_] = useState<View>(urlView);
  const [supplyActions, setSupplyActions] = useState<React.ReactNode>(null);
  const [horizon, setHorizon_] = useState<"week" | "ahead">(urlHorizon);
  const setHorizon = (h: "week" | "ahead") => {
    const u = new URL(location.href);
    if (h === "ahead") u.searchParams.set("horizon", "ahead");
    else u.searchParams.delete("horizon");
    history.replaceState(history.state, "", u);
    setSupplyActions(null);
    setHorizon_(h);
  };
  const setView = (v: View) => {
    const u = new URL(location.href);
    if (v === "clients") u.searchParams.set("view", "clients");
    else u.searchParams.delete("view");
    history.replaceState(history.state, "", u);
    setView_(v);
  };
  const toggle = <RadarSwitch value={view} onChange={setView} />;
  if (view === "supply") {
    return (
      <Page>
        <PageHeader title="Radar d'approvisionnement" icon={<Radar size={15} strokeWidth={1.75} />}
          meta={<div className="flex items-center gap-2">{toggle}<HorizonSwitch value={horizon} onChange={setHorizon} /></div>} actions={supplyActions} />
        {horizon === "ahead" ? <AheadView actionsSlot={setSupplyActions} /> : <SupplyView date={DATE} actionsSlot={setSupplyActions} />}
      </Page>
    );
  }
  return (
    <Page>
      {forecast ? <Radar_ forecast={forecast} toggle={toggle} /> : <PageHeader title="Radar de demande" icon={<Radar size={15} strokeWidth={1.75} />} meta={toggle} />}
    </Page>
  );
}

function Radar_({ forecast, toggle }: { forecast: Forecast & { offline?: boolean }; toggle: React.ReactNode }) {
  const { state, start, deliver, reset } = useCampaign(forecast);
  const [selected, setSelected] = useState<string>();
  const [focus, setFocus] = useState<string>();
  const [sweep, setSweep] = useState(0);
  const [truck, setTruck] = useState<string>();
  const { phase, totals, route } = state;
  const roads = useRoads(route, forecast.depot);
  const names = useMemo(() => Object.fromEntries(forecast.clients.map((c) => [c.clientId, c.name])), [forecast]);
  const running = phase === "starting" || phase === "running";
  const nCalls = Object.keys(state.calls).length;
  const toCall = forecast.summary.toCall ?? forecast.summary.predicted + forecast.summary.upsell;
  const dateLabel = new Date(`${forecast.date}T12:00:00`).toLocaleDateString("fr-FR", { weekday: "long", day: "numeric", month: "long" });

  const k = useMemo(() => {
    const trucks = route?.optimized ?? [];
    return {
      // clients with an order: the ones that already had one (upsell included) + every predicted client who said yes
      orders: forecast.summary.ordered + Object.values(state.calls).filter((c) => c.kind !== "upsell" && (c.status === "confirmed" || c.status === "modified")).length,
      potentialOrders: forecast.clients.length,
      revenue: totals.revenueHt,
      // live: the event uplift of every client who said yes (the server only totals it at the end)
      uplift: Math.max(totals.upliftHt, Object.values(state.calls).filter((c) => c.status === "confirmed" || c.status === "modified")
        .reduce((s, c) => s + (forecast.clients.find((x) => x.clientId === c.clientId)?.upliftHt ?? 0), 0)),
      km: trucks.reduce((s, t) => s + t.km, 0),
      trucks: trucks.length,
      savedKm: route?.totals?.savedKm ?? 0,
    };
  }, [forecast, totals, route, state.calls]);

  const client = forecast.clients.find((c) => c.clientId === selected);
  const launch = () => { setSelected(undefined); setFocus(undefined); setTruck(undefined); start(); };
  const again = () => { reset(); setSelected(undefined); setFocus(undefined); setTruck(undefined); setSweep((s) => s + 1); };

  return (
    <>
      <PageHeader
        title="Radar de demande"
        icon={<Radar size={15} strokeWidth={1.75} />}
        meta={
          <span className="flex items-center gap-2 text-[12.5px] text-muted-foreground">
            {toggle}
            <span className="first-letter:uppercase">{dateLabel}</span>
            {state.source === "demo" || forecast.offline ? <span className="rounded-[3px] bg-muted px-1.5 py-px text-[11px]">démo</span> : null}
          </span>
        }
        actions={
          <>
            {phase !== "idle" && !running ? <Button variant="ghost" icon={RotateCcw} onClick={again}>Recommencer</Button> : null}
            {phase === "done" ? (
              <Button variant="primary" icon={Truck} onClick={deliver}>Lancer les livraisons</Button>
            ) : phase === "delivering" || phase === "delivered" ? (
              <Button variant="default" disabled icon={phase === "delivering" ? undefined : Truck}>
                {phase === "delivering" ? <><Spinner size={12} /> Livraisons {Object.keys(state.delivered).length}</> : "Tout est livré"}
              </Button>
            ) : (
              <Button variant="primary" icon={running ? undefined : PhoneCall} disabled={running} onClick={launch}>
                {running ? <><Spinner size={12} /> Appels {totals.called}/{nCalls || "…"}</> : `Lancer les appels (${toCall})`}
              </Button>
            )}
          </>
        }
      />

      {/* KPIs */}
      <div className="grid shrink-0 grid-cols-2 border-b border-border md:grid-cols-4">
        <Kpi label="Commandes" sub={`sur ${k.potentialOrders} clients prévus`} i={0}>
          <Count value={k.orders} />
        </Kpi>
        <Kpi label="Chiffre d'affaires des appels" sub={`${fmtMoney(forecast.summary.expectedHt, { compact: true })} attendus${totals.called ? ` · ${totals.called} appels` : ""}`} i={1}>
          <Count value={k.revenue} format={(v) => fmtMoney(v, { compact: true })} />
        </Kpi>
        <Kpi label="Gagné grâce aux événements" sub={`${fmtMoney(forecast.summary.upliftHt, { compact: true })} possibles`} i={2} accent>
          <Count value={k.uplift} format={(v) => `+${fmtMoney(v, { compact: true })}`} />
        </Kpi>
        <Kpi label="Tournées" sub={k.trucks ? `${k.trucks} camions${k.savedKm ? ` · −${Math.round(k.savedKm)} km optimisés` : ""}` : "après les appels"} i={3}>
          {k.km ? <Count value={k.km} format={(v) => `${Math.round(v)} km`} /> : <span className="text-muted-foreground">—</span>}
        </Kpi>
      </div>

      <div className="flex min-h-0 flex-1">
        <div className="relative min-w-0 flex-1">
          <RadarMap
            forecast={forecast} calls={state.calls} route={route} roads={roads} delivered={state.delivered} phase={phase}
            selected={selected} onSelect={setSelected} focusEvent={focus} focusTruck={truck} sweepKey={sweep} toast={state.toast}
          />
        </div>

        <aside className="flex w-[392px] shrink-0 flex-col border-l border-border bg-background">
          <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
            <AnimatePresence mode="wait" initial={false}>
              {client ? (
                <motion.div key={`c-${client.clientId}`} initial={{ opacity: 0, x: 16 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: 16 }} transition={{ duration: 0.18 }}>
                  <ClientDetail c={client} forecast={forecast} call={state.calls[client.clientId]} delivered={state.delivered[client.clientId]} onBack={() => setSelected(undefined)} />
                </motion.div>
              ) : phase === "idle" ? (
                <motion.div key="idle" initial={{ opacity: 0, x: -16 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -16 }} transition={{ duration: 0.18 }}
                  className="flex flex-col gap-5">
                  <section>
                    <SectionTitle>Cette semaine à Marseille</SectionTitle>
                    <EventStrip forecast={forecast} focus={focus} onFocus={setFocus} />
                  </section>
                  <section>
                    <SectionTitle count={toCall}>À appeler</SectionTitle>
                    {forecast.action ? <p className="-mt-1 mb-2 text-[12px] leading-4 text-muted-foreground">{forecast.action.pitch}</p> : null}
                    <ClientList forecast={forecast} selected={selected} onSelect={setSelected} />
                  </section>
                  {forecast.summary.skipped ? (
                    <section>
                      <SectionTitle count={forecast.summary.skipped}>Non appelés · enlèvement au quai conseillé</SectionTitle>
                      <NotCalledList forecast={forecast} selected={selected} onSelect={setSelected} />
                    </section>
                  ) : null}
                </motion.div>
              ) : (
                <motion.div key="live" initial={{ opacity: 0, x: -16 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -16 }} transition={{ duration: 0.18 }}
                  className="flex flex-col gap-5">
                  {route?.optimized.length ? (
                    <section>
                      <SectionTitle count={route.optimized.length}>Livraisons optimisées</SectionTitle>
                      <DeliveryCards route={route} names={names} delivered={state.delivered} focus={truck} onFocus={setTruck} />
                    </section>
                  ) : null}
                  <ActivityFeed feed={state.feed} live={running || phase === "delivering"} onSelect={setSelected} />
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        </aside>
      </div>
    </>
  );
}

function Kpi({ label, sub, children, i, accent }: { label: string; sub: string; children: React.ReactNode; i: number; accent?: boolean }) {
  return (
    <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ type: "spring", stiffness: 380, damping: 32, delay: 0.05 + i * 0.05 }}
      className={cn("min-w-0 px-5 py-3", i > 0 && "md:border-l md:border-border", i % 2 === 1 && "border-l border-border md:border-l")}>
      <div className="text-[11.5px] text-muted-foreground">{label}</div>
      <div className={cn("mt-0.5 text-[22px] font-semibold leading-7 tracking-[-0.02em]", accent && "text-primary")}>{children}</div>
      <div className="truncate text-[11.5px] text-muted-foreground">{sub}</div>
    </motion.div>
  );
}

function SectionTitle({ children, count }: { children: React.ReactNode; count?: number }) {
  return (
    <h2 className="mb-2 flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-[0.04em] text-muted-foreground">
      {children}{count !== undefined ? <span className="num normal-case">{count}</span> : null}
    </h2>
  );
}

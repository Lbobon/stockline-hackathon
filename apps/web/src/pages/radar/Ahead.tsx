/**
 * Radar fournisseurs, « 12 prochaines semaines »: the demand spikes the week-level model sees coming (season, last year,
 * trend, growth, climate, holidays and the event calendar), product by product, and the suppliers to book ahead.
 * « Réserver chez les fournisseurs » runs the same parallel supplier calls as the week view (POST /ai/campaigns
 * {kind:"supply", weeks}). Data: GET /api/radar/supply/ahead, reliability: GET /api/radar/supply/ahead/backtest.
 */
import { useCallback, useEffect, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { useQuery } from "@tanstack/react-query";
import { PhoneCall, RotateCcw, TrendingUp } from "lucide-react";
import { Button, Spinner } from "@/ui";
import { cn } from "@/lib/cn";
import { fmtMoney, fmtQty } from "@/lib/format";
import { API_BASE } from "@/lib/api";
import { Count } from "./Panel";
import { Kpi, SupplierCard, SupplierDetail, useSupplyCampaign, type SupplierNeed } from "./Supply";

// ── data ──

interface WeekQty { week: string; qty: number; normal: number; vsNormal: number; spike: boolean; events: string[]; lastYear: number | null; why: string[] }
interface WeekTotal { week: string; expectedHt: number; normalHt: number; spikeHt: number; vsNormal: number | null; events: string[] }
interface AheadProduct {
  productId: string; productName: string; unit: string; category?: string; weeks: WeekQty[]; spikeWeeks: number; extraQty: number;
  extraHt: number; peakWeek: string | null; peakVsNormal: number | null; why: string[];
}
export interface Ahead {
  from: string; to: string; weeks: WeekTotal[]; months: { month: string; expectedHt: number; normalHt: number; spikeHt: number; vsNormal: number | null }[];
  products: AheadProduct[]; suppliers: (SupplierNeed & { week: string; laterWeeks: string[] })[]; topFeatures: { name: string; share: number }[];
  summary: { suppliers: number; lines: number; totalHt: number; spikeHt: number; spikeWeeks: number; spikeProducts: number };
}
interface Score { error: number; spikeRecall: number | null; spikePrecision: number | null; extraCaught: number }
interface Backtest { weeksBack: number; horizons: Record<string, { model: Score; patternsOnly: Score; normal: Score; lastYear: Score; spikes: number }> }

const WEEKS = 12;
const AHEAD_KEY = ["radar", "supply", "ahead"];

function useAhead() {
  return useQuery({
    queryKey: AHEAD_KEY,
    queryFn: async (): Promise<Ahead> => {
      const r = await fetch(`${API_BASE}/radar/supply/ahead?weeks=${WEEKS}`);
      if (!r.ok) throw new Error(`${r.status}`);
      return r.json();
    },
    staleTime: 5 * 60_000,
  });
}

function useAheadBacktest() {
  return useQuery({
    queryKey: ["radar", "ahead-backtest"],
    queryFn: async (): Promise<Backtest> => {
      const r = await fetch(`${API_BASE}/radar/supply/ahead/backtest`);
      if (!r.ok) throw new Error(`${r.status}`);
      return r.json();
    },
    staleTime: Infinity,
  });
}

const spring = { type: "spring", stiffness: 420, damping: 34 } as const;
const wk = (iso: string) => new Date(`${iso}T12:00:00`).toLocaleDateString("fr-FR", { day: "numeric", month: "short" });
const month = (ym: string) => new Date(`${ym}-15T12:00:00`).toLocaleDateString("fr-FR", { month: "long" });
const pct = (v: number) => `${v >= 0 ? "+" : "−"}${Math.abs(Math.round(v * 100))} %`;

const EVENT_FR: Record<string, string> = {
  ramadan: "Ramadan", aid_fitr: "Aïd el-Fitr", aid_kebir: "Aïd el-Kébir", christmas: "fêtes de fin d'année",
  christmas_market: "marché de Noël", school_holidays: "vacances scolaires",
};
const FEATURE_FR: Record<string, string> = {
  product: "le produit lui-même", category: "sa catégorie", horizon: "l'éloignement de la semaine", weekOfYear: "la semaine de l'année",
  month: "le mois", deliveryDays: "les jours fériés", monthStart: "le début de mois", normal: "le niveau habituel",
  last4: "la tendance sur 4 semaines", lastWeek: "la semaine passée", last26: "la tendance sur 6 mois", yearGrowth: "la croissance sur un an",
  clientsGrowth: "le nombre de clients", categoryMomentum: "l'élan de sa catégorie", lastYear: "l'an dernier, même semaine",
  lastYearPeak: "le pic de l'an dernier", twoYearsAgo: "il y a deux ans", climateTmax: "la météo de saison",
};
function featureFr(name: string) {
  if (FEATURE_FR[name]) return FEATURE_FR[name];
  const [type, kind, ly] = name.split(":");
  const ev = EVENT_FR[type] ?? type;
  return `${kind === "until" ? `l'approche ${/^[aeéiou]/i.test(ev) ? "de l'" : "du "}${ev}` : ev}${ly ? " (l'an dernier)" : ""}`;
}

// ── the view ──

export function AheadView({ actionsSlot }: { actionsSlot: (node: React.ReactNode) => void }) {
  const { data, isLoading } = useAhead();
  const { data: bt } = useAheadBacktest();
  const [plan, setPlan] = useState<Ahead>();
  const shown = plan ?? data;
  const { state, start, reset } = useSupplyCampaign(shown, WEEKS, AHEAD_KEY);
  const [selected, setSelected] = useState<string>();
  const [only, setOnly] = useState<"all" | "patterns">("all");
  const running = state.phase === "running";

  const launch = useCallback(() => { setSelected(undefined); setPlan(data); start().catch(() => reset()); }, [data, start, reset]);
  const again = useCallback(() => { reset(); setPlan(undefined); setSelected(undefined); }, [reset]);
  const answered = Object.values(state.calls).filter((c) => !["queued", "ringing", "talking"].includes(c.status)).length;

  useEffect(() => {
    actionsSlot(
      <>
        {state.phase === "done" ? <Button variant="ghost" icon={RotateCcw} onClick={again}>Recommencer</Button> : null}
        <Button variant="primary" icon={running ? undefined : PhoneCall} disabled={running || !shown?.suppliers.length || state.phase === "done"} onClick={launch}>
          {running ? <><Spinner size={12} /> Fournisseurs {answered}/{shown?.suppliers.length ?? 0}</>
            : state.phase === "done" ? "Réservations passées" : `Réserver chez les fournisseurs (${shown?.suppliers.length ?? 0})`}
        </Button>
      </>,
    );
  }, [actionsSlot, running, answered, shown, state.phase, launch, again]);

  if (isLoading || !shown) return <div className="grid flex-1 place-items-center"><Spinner /></div>;

  const bought = state.bought || Object.values(state.calls).reduce((s, c) => s + c.totalHt, 0);
  const sup = shown.suppliers.find((s) => s.supplierId === selected);
  const spiky = shown.products.filter((p) => p.spikeWeeks > 0);
  const patterned = spiky.map((p) => ({ ...p, weeks: p.weeks.map((w) => ({ ...w, spike: w.spike && isPattern(w) })) }))
    .filter((p) => p.weeks.some((w) => w.spike))
    .map((p) => summarize(p))
    .sort((a, b) => b.extraHt - a.extraHt);
  const list = only === "patterns" ? patterned : spiky;
  const all = bt?.horizons["tout"];

  return (
    <>
      <div className="grid shrink-0 grid-cols-2 border-b border-border md:grid-cols-4">
        <Kpi i={0} label={`Pics de demande, ${WEEKS} semaines`} sub={`${shown.summary.spikeProducts} produits au-dessus de la normale`} accent>
          <Count value={shown.summary.spikeHt} format={(v) => `+${fmtMoney(v, { compact: true })}`} />
        </Kpi>
        <Kpi i={1} label="À réserver maintenant" sub={`${shown.suppliers.length} fournisseurs, leur plus gros pic`}>
          <Count value={shown.summary.totalHt} format={(v) => fmtMoney(v, { compact: true })} />
        </Kpi>
        <Kpi i={2} label="Réservé par l'agent" sub={state.phase === "idle" ? "aucun appel pour l'instant" : `${answered}/${shown.suppliers.length} appels`}>
          <Count value={bought} format={(v) => fmtMoney(v, { compact: true })} />
        </Kpi>
        <Kpi i={3} label="Pics vus venir" sub={all ? `testé sur ${bt?.weeksBack} semaines · règle « l'an dernier » : ${Math.round(all.lastYear.extraCaught * 100)} %` : "backtest en cours…"}>
          {all ? <Count value={all.model.extraCaught * 100} format={(v) => `${Math.round(v)} %`} /> : <span className="text-muted-foreground">…</span>}
        </Kpi>
      </div>

      <div className="flex min-h-0 flex-1">
        <div className="min-w-0 flex-1 overflow-y-auto px-6 py-5">
          <div className="mb-3 flex items-baseline gap-2">
            <h2 className="text-[15px] font-semibold tracking-[-0.01em]">Les {WEEKS} prochaines semaines</h2>
            <span className="text-[12px] text-muted-foreground">ventes prévues face à une semaine normale · du {wk(shown.from)} au {wk(shown.to)}</span>
          </div>
          <WeekChart weeks={shown.weeks} months={shown.months} />
          <div className="mt-5 grid gap-4 xl:grid-cols-[minmax(0,1fr)_260px]">
            <div className="min-w-0">
              <div className="mb-2 flex flex-wrap items-center gap-2">
                <h2 className="mr-auto text-[13px] font-semibold">Produits qui vont dépasser la normale</h2>
                <Segmented value={only} onChange={setOnly} options={[["all", `Tous les pics (${spiky.length})`], ["patterns", `Hors événements (${patterned.length})`]]} />
              </div>
              {only === "patterns" ? (
                <p className="mb-2 text-[11.5px] leading-4 text-muted-foreground">
                  Les pics qu'aucun événement du calendrier n'explique : saison, ventes de l'an dernier, croissance d'un produit, nouveaux clients, tendance récente.
                </p>
              ) : null}
              <ul className="flex flex-col gap-2">
                {list.slice(0, 14).map((p, i) => <ProductRow key={`${only}-${p.productId}`} p={p} i={i} />)}
              </ul>
            </div>
            <div className="flex flex-col gap-4">
              <Drivers features={shown.topFeatures} />
              {all ? <Reliability bt={bt!} /> : null}
            </div>
          </div>
        </div>

        <aside className="flex w-[420px] shrink-0 flex-col border-l border-border bg-background">
          <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
            <AnimatePresence mode="wait" initial={false}>
              {sup ? (
                <motion.div key={sup.supplierId} initial={{ opacity: 0, x: 16 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: 16 }} transition={{ duration: 0.18 }}>
                  <SupplierDetail s={sup} call={state.calls[sup.supplierId]} onBack={() => setSelected(undefined)} />
                </motion.div>
              ) : (
                <motion.div key="list" initial={{ opacity: 0, x: -16 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -16 }} transition={{ duration: 0.18 }}>
                  <h2 className="mb-1 flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-[0.04em] text-muted-foreground">
                    Fournisseurs à réserver <span className="num normal-case">{shown.suppliers.length}</span>
                  </h2>
                  <p className="mb-2.5 text-[12px] leading-4 text-muted-foreground">
                    Pour chaque fournisseur, son plus gros pic à venir : l'agent l'appelle maintenant pour réserver la quantité en plus de l'habitude,
                    livrée la veille de la semaine.
                  </p>
                  <ul className="flex flex-col gap-1.5">
                    {shown.suppliers.map((s, i) => (
                      <motion.li key={s.supplierId} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ ...spring, delay: 0.2 + i * 0.05 }}>
                        <SupplierCard s={s} call={state.calls[s.supplierId]} onClick={() => setSelected(s.supplierId)} />
                      </motion.li>
                    ))}
                  </ul>
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        </aside>
      </div>
    </>
  );
}

/** A spike week no calendar event explains: neither inside one nor just before one. */
const isPattern = (w: WeekQty) => w.spike && !w.events.length && !w.why.some((x) => x.startsWith("juste avant"));

/** Recompute a product's totals from the spike weeks it keeps (the « hors événements » list). */
function summarize(p: AheadProduct): AheadProduct {
  const spikes = p.weeks.filter((w) => w.spike);
  const extra = spikes.reduce((s, w) => s + w.qty - w.normal, 0);
  const peak = spikes.reduce((a, w) => (w.vsNormal > a.vsNormal ? w : a), spikes[0]);
  const price = p.extraQty ? p.extraHt / p.extraQty : 0;
  return { ...p, spikeWeeks: spikes.length, extraQty: extra, extraHt: extra * price, peakWeek: peak.week, peakVsNormal: peak.vsNormal, why: peak.why };
}

function Segmented<T extends string>({ value, onChange, options }: { value: T; onChange: (v: T) => void; options: [T, string][] }) {
  return (
    <div className="flex rounded-md border border-border bg-muted/50 p-0.5 text-[11.5px]">
      {options.map(([v, label]) => (
        <button key={v} type="button" onClick={() => onChange(v)}
          className={cn("rounded px-2 py-0.5 font-medium transition-colors", value === v ? "bg-card text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground")}>
          {label}
        </button>
      ))}
    </div>
  );
}

function WeekChart({ weeks, months }: { weeks: WeekTotal[]; months: Ahead["months"] }) {
  const max = Math.max(...weeks.map((w) => Math.max(w.expectedHt, w.normalHt)), 1);
  const normal = weeks[0]?.normalHt ?? 0;
  return (
    <div className="rounded-lg border border-border bg-card px-4 pb-3 pt-4">
      <div className="relative flex h-[150px] items-end gap-1.5">
        <div className="pointer-events-none absolute inset-x-0 border-t border-dashed border-foreground/30" style={{ bottom: `${(normal / max) * 100}%` }}>
          <span className="absolute -top-4 right-0 text-[10.5px] text-muted-foreground">semaine normale · {fmtMoney(normal, { compact: true })}</span>
        </div>
        {weeks.map((w, i) => {
          const up = (w.vsNormal ?? 0) >= 0.1;
          const down = (w.vsNormal ?? 0) <= -0.1;
          return (
            <div key={w.week} className="group relative flex h-full flex-1 flex-col justify-end">
              <span className={cn("num mb-0.5 text-center text-[10px] font-medium", up ? "text-chart-3" : down ? "text-muted-foreground" : "text-transparent group-hover:text-muted-foreground")}>
                {w.vsNormal != null ? pct(w.vsNormal) : ""}
              </span>
              <motion.div initial={{ scaleY: 0 }} animate={{ scaleY: 1 }} transition={{ duration: 0.5, delay: 0.1 + i * 0.03 }}
                className={cn("origin-bottom rounded-t", up ? "bg-chart-3/80" : down ? "bg-foreground/20" : "bg-foreground/35")}
                style={{ height: `${(w.expectedHt / max) * 100}%` }}
                title={`${wk(w.week)} : ${fmtMoney(w.expectedHt, { compact: true })} prévus${w.events.length ? ` · ${w.events.join(", ")}` : ""}`} />
            </div>
          );
        })}
      </div>
      <div className="mt-1 flex gap-1.5">
        {weeks.map((w) => (
          <div key={w.week} className="min-w-0 flex-1 text-center">
            <div className="num text-[10.5px] text-muted-foreground">{wk(w.week)}</div>
            <div className="truncate text-[10px] leading-3 text-chart-3" title={w.events.join(", ")}>{w.events[0]?.replace(/ 20\d\d$/, "") ?? ""}</div>
          </div>
        ))}
      </div>
      <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 border-t border-border pt-2.5 text-[11.5px] text-muted-foreground">
        {months.map((m) => (
          <span key={m.month}><span className="capitalize text-foreground">{month(m.month)}</span> {fmtMoney(m.expectedHt, { compact: true })}
            {m.vsNormal != null ? <span className={cn("num ml-1", m.vsNormal >= 0.1 ? "text-chart-3" : "")}>{pct(m.vsNormal)}</span> : null}</span>
        ))}
      </div>
    </div>
  );
}

function ProductRow({ p, i }: { p: AheadProduct; i: number }) {
  const max = Math.max(...p.weeks.map((w) => w.qty / (w.normal || 1)), 1.3);
  return (
    <motion.li initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ ...spring, delay: 0.1 + i * 0.03 }}
      className="flex items-center gap-3 rounded-lg border border-border bg-card px-3 py-2">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5 text-[12.5px]">
          <TrendingUp size={13} className="shrink-0 text-chart-3" />
          <span className="truncate font-medium">{p.productName}</span>
        </div>
        <p className="mt-0.5 truncate text-[11.5px] text-muted-foreground" title={p.why.join(" · ")}>{p.why.slice(0, 2).join(" · ")}</p>
      </div>
      <div className="flex h-8 w-[132px] shrink-0 items-end gap-[2px]" aria-label="12 semaines face à la normale">
        {p.weeks.map((w) => (
          <div key={w.week} title={`${wk(w.week)} : ${fmtQty(Math.round(w.qty), p.unit)} (normale ${fmtQty(Math.round(w.normal), p.unit)})`}
            className={cn("flex-1 rounded-sm", w.spike ? "bg-chart-3/80" : "bg-foreground/20")} style={{ height: `${Math.min(100, (w.qty / (w.normal || 1) / max) * 100)}%` }} />
        ))}
      </div>
      <div className="w-[92px] shrink-0 text-right">
        <div className="num text-[12.5px] font-semibold text-chart-3">+{fmtQty(Math.round(p.extraQty), p.unit)}</div>
        <div className="text-[10.5px] text-muted-foreground">{p.spikeWeeks} sem. · pic {p.peakWeek ? wk(p.peakWeek) : "—"}</div>
      </div>
    </motion.li>
  );
}

function Drivers({ features }: { features: Ahead["topFeatures"] }) {
  // the product's identity and level only say « which product », not what moves its sales: left out of this list
  const shown = features.filter((f) => !["product", "category", "normal", "horizon"].includes(f.name));
  const sum = shown.reduce((s, f) => s + f.share, 0) || 1;
  const top = shown.slice(0, 7).map((f) => ({ ...f, share: f.share / sum }));
  const max = Math.max(...top.map((f) => f.share), 0.01);
  return (
    <div className="rounded-lg border border-border bg-card p-3">
      <h3 className="text-[12px] font-semibold">Ce que le modèle regarde</h3>
      <p className="mb-2 text-[11px] leading-4 text-muted-foreground">3 ans de ventes, semaine par semaine : saisons, an dernier, tendance, clients, météo, fériés et calendrier.</p>
      <ul className="flex flex-col gap-1.5">
        {top.map((f) => (
          <li key={f.name} className="text-[11.5px]">
            <div className="flex justify-between gap-2"><span className="truncate">{featureFr(f.name)}</span><span className="num text-muted-foreground">{Math.round(f.share * 100)} %</span></div>
            <div className="mt-0.5 h-1 rounded-full bg-muted"><div className="h-full rounded-full bg-chart-3/70" style={{ width: `${(f.share / max) * 100}%` }} /></div>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Reliability({ bt }: { bt: Backtest }) {
  const rows: [string, keyof Backtest["horizons"][string]][] = [["Notre modèle", "model"], ["Sans le calendrier", "patternsOnly"], ["Comme l'an dernier", "lastYear"], ["Comme les 8 dernières semaines", "normal"]];
  const h = bt.horizons;
  return (
    <div className="rounded-lg border border-border bg-card p-3">
      <h3 className="text-[12px] font-semibold">Testé sur l'année passée</h3>
      <p className="mb-2 text-[11px] leading-4 text-muted-foreground">Chaque semaine prévue avec seulement ce qu'on savait avant. Écart moyen sur les ventes, par produit et par semaine.</p>
      <table className="w-full text-[11.5px]">
        <thead><tr className="text-muted-foreground"><th className="text-left font-normal" />{["1-2 sem.", "5-8 sem.", "9-12 sem."].map((k) => <th key={k} className="text-right font-normal">{k}</th>)}</tr></thead>
        <tbody>
          {rows.map(([label, key]) => (
            <tr key={key} className={key === "model" ? "font-medium" : ""}>
              <td className="py-0.5">{label}</td>
              {["1-2 sem.", "5-8 sem.", "9-12 sem."].map((k) => <td key={k} className="num text-right">{h[k] ? `±${Math.round((h[k][key] as Score).error * 100)} %` : "—"}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** « Cette semaine | 12 semaines » for the supplier radar header. */
export function HorizonSwitch({ value, onChange }: { value: "week" | "ahead"; onChange: (v: "week" | "ahead") => void }) {
  return (
    <div className="flex rounded-lg border border-border bg-muted/50 p-0.5 text-[12.5px]">
      {([["week", "Cette semaine"], ["ahead", `${WEEKS} semaines`]] as const).map(([v, label]) => (
        <button key={v} type="button" onClick={() => onChange(v)}
          className={cn("relative rounded-md px-3 py-1 font-medium transition-colors", value === v ? "text-foreground" : "text-muted-foreground hover:text-foreground")}>
          {value === v ? <motion.span layoutId="radar-horizon" className="absolute inset-0 rounded-md bg-card shadow-sm" transition={spring} /> : null}
          <span className="relative">{label}</span>
        </button>
      ))}
    </div>
  );
}

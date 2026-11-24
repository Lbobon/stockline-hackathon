/**
 * Radar d'approvisionnement (the radar's main view): we predict the spike in sales, we call the suppliers in advance.
 *   ① the clients' forecast added up day by day: the spike the events (weddings, Christmas market…) bring
 *   ② against the stock: what the depot will run out of, and from which day
 *   ③ the suppliers to call, on a map of where they ship from, each arc turning green as the order is sent.
 * « Appeler les fournisseurs » rings every supplier in parallel (POST /ai/campaigns {kind:"supply"}): the voice agent
 * is Médina's buyer, and each yes writes and sends a real purchase order. Data: GET /api/radar/supply.
 */
import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, BarChart3, Check, ChevronRight, Clock, Factory, PackageX, PhoneCall, PhoneOff, RotateCcw, Sparkles, X } from "lucide-react";
import { Button, Spinner } from "@/ui";
import { cn } from "@/lib/cn";
import { fmtMoney, fmtQty } from "@/lib/format";
import { AI_BASE, API_BASE } from "@/lib/api";
import { Count } from "./Panel";
import { SupplierMap } from "./SupplierMap";
import { SupplyExplain } from "./SupplyExplain";

// ── data ──

export interface SupplyLine {
  productId: string; productName: string; unit: string; needQty: number; eventQty: number; available: number; incoming: number;
  shortQty: number; firstDay: string; clients: number; events: string[]; qty?: number; unitPrice?: number; totalHt?: number;
  /** the part of each day's demand the stock can no longer serve, one value per day of `timeline` */
  shortByDay?: number[];
}
export interface SupplierNeed {
  supplierId: string; name: string; city?: string; lat?: number | null; lng?: number | null; contactName?: string; phone?: string;
  leadTimeDays?: number; minOrderAmount?: number;
  lines: SupplyLine[]; totalHt: number; eventHt: number; events: string[]; deliverBy: string; arrives: string; onTime: boolean;
  belowMinimum: boolean; reason: string;
}
export interface SupplyDay { date: string; orderedHt: number; needHt: number; normalHt: number; eventHt: number; shortHt: number; events: string[] }
export interface Supply {
  from: string; to: string; days: number; depot: { name: string; lat: number; lng: number }; timeline: SupplyDay[];
  products: SupplyLine[]; suppliers: SupplierNeed[];
  summary: { suppliers: number; lines: number; totalHt: number; eventHt: number; shortProducts: number };
}

type Status = "queued" | "ringing" | "talking" | "confirmed" | "modified" | "declined" | "callback" | "failed";
interface BuyCall {
  callId: string; supplierId: string; name: string; status: Status; transcript: { who: "agent" | "client"; text: string }[];
  decision?: { label: string; confidence: number; source: string } | null; orderId?: string; number?: string; totalHt: number;
  lines: { productId: string; productName: string; qty: number; unit: string; unitPrice: number }[];
}
interface BuyState { phase: "idle" | "running" | "done"; id?: string; calls: Record<string, BuyCall>; byCall: Record<string, string>; bought: number; needed: number }

const INIT: BuyState = { phase: "idle", calls: {}, byCall: {}, bought: 0, needed: 0 };

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function reduce(s: BuyState, a: { type: "reset" } | { type: "start"; id: string; calls: BuyCall[] } | { type: "events"; events: any[] }): BuyState {
  if (a.type === "reset") return INIT;
  if (a.type === "start") {
    return { ...INIT, phase: "running", id: a.id, calls: Object.fromEntries(a.calls.map((c) => [c.supplierId, c])), byCall: Object.fromEntries(a.calls.map((c) => [c.callId, c.supplierId])) };
  }
  let st = s;
  for (const e of a.events) {
    const sid = e.clientId ?? st.byCall[e.callId];
    const c = sid ? st.calls[sid] : undefined;
    const put = (p: Partial<BuyCall>) => { if (c) st = { ...st, calls: { ...st.calls, [c.supplierId]: { ...st.calls[c.supplierId], ...p } } }; };
    if (e.type === "call_status") put({ status: e.status });
    else if (e.type === "line" && c) put({ transcript: [...st.calls[c.supplierId].transcript, { who: e.who, text: e.text }] });
    else if (e.type === "decision") put({ decision: { label: e.label, confidence: e.confidence, source: e.source } });
    else if (e.type === "order") put({ orderId: e.orderId, number: e.number, totalHt: e.totalHt, lines: e.lines ?? [] });
    else if (e.type === "totals") st = { ...st, bought: e.revenueHt ?? st.bought, needed: e.neededHt ?? st.needed };
    else if (e.type === "done") st = { ...st, phase: "done", bought: e.totals?.revenueHt ?? st.bought };
  }
  return st;
}

function useSupplyCampaign(supply?: Supply) {
  const [state, dispatch] = useReducer(reduce, INIT);
  const es = useRef<EventSource | null>(null);
  const qc = useQueryClient();
  useEffect(() => () => es.current?.close(), []);
  const starting = useRef(false);
  const start = useCallback(async () => {
    // one campaign at a time: a double click used to start two, and both streams wrote into the same cards
    if (!supply || starting.current) return;
    starting.current = true;
    es.current?.close();
    es.current = null;
    let id: string;
    try {
      const r = await fetch(`${AI_BASE}/campaigns`, { method: "POST", headers: { "content-type": "application/json", "x-actor": "user" },
        body: JSON.stringify({ kind: "supply", date: supply.from, concurrency: 6 }) });
      if (!r.ok) throw new Error(`${r.status}`);
      ({ id } = await r.json());
    } finally {
      starting.current = false;
    }
    dispatch({ type: "start", id, calls: supply.suppliers.map((s, i) => ({ callId: `buy_${String(i + 1).padStart(2, "0")}`, supplierId: s.supplierId, name: s.name,
      status: "queued", transcript: [], totalHt: 0, lines: [] })) });
    const src = new EventSource(`${AI_BASE}/campaigns/${id}/stream`);
    es.current = src;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    let queue: any[] = [];
    let timer = 0;
    src.onmessage = (m) => {
      try { queue.push(JSON.parse(m.data)); } catch { return; }
      if (!timer) timer = window.setTimeout(() => {
        timer = 0; const events = queue; queue = [];
        dispatch({ type: "events", events });
        if (events.some((e) => e.type === "done")) { src.close(); qc.invalidateQueries({ queryKey: ["radar", "supply"] }); }
      }, 50);
    };
  }, [supply, qc]);
  const reset = useCallback(() => { es.current?.close(); dispatch({ type: "reset" }); }, []);
  return { state, start, reset };
}

export function useSupply(date: string) {
  return useQuery({
    queryKey: ["radar", "supply", date],
    queryFn: async (): Promise<Supply> => {
      const r = await fetch(`${API_BASE}/radar/supply?date=${date}&days=7`);
      if (!r.ok) throw new Error(`${r.status}`);
      return r.json();
    },
    staleTime: 60_000,
  });
}

const spring = { type: "spring", stiffness: 420, damping: 34 } as const;
const dayLabel = (iso: string) => new Date(`${iso}T12:00:00`).toLocaleDateString("fr-FR", { weekday: "long", day: "numeric" });

// ── the view ──

export function SupplyView({ date, actionsSlot }: { date: string; actionsSlot: (node: React.ReactNode) => void }) {
  const { data: supply, isLoading } = useSupply(date);
  // keep the plan the calls started from: a refetch after the calls shows the new (smaller) shortfall below
  const [plan, setPlan] = useState<Supply>();
  const shown = plan ?? supply;
  const { state, start, reset } = useSupplyCampaign(shown);
  const [selected, setSelected] = useState<string>();
  const running = state.phase === "running";

  const [launching, setLaunching] = useState(false);
  const launch = useCallback(() => {
    if (launching) return;
    setLaunching(true);
    setSelected(undefined); setPlan(supply);
    start().catch(() => reset()).finally(() => setLaunching(false));
  }, [supply, start, reset, launching]);
  const again = useCallback(() => { reset(); setPlan(undefined); setSelected(undefined); }, [reset]);

  const answered = Object.values(state.calls).filter((c) => !["queued", "ringing", "talking"].includes(c.status)).length;
  useEffect(() => {
    actionsSlot(
      <>
        {state.phase === "done" ? <Button variant="ghost" icon={RotateCcw} onClick={again}>Recommencer</Button> : null}
        <Button variant="primary" icon={running ? undefined : PhoneCall} disabled={running || launching || !shown?.suppliers.length || state.phase === "done"} onClick={launch}>
          {running || launching ? <><Spinner size={12} /> Fournisseurs {answered}/{shown?.suppliers.length ?? 0}</>
            : state.phase === "done" ? "Commandes passées" : `Appeler les fournisseurs (${shown?.suppliers.length ?? 0})`}
        </Button>
      </>,
    );
  }, [actionsSlot, running, launching, answered, shown, state.phase, launch, again]);

  if (isLoading || !shown) return <div className="grid flex-1 place-items-center"><Spinner /></div>;

  const bought = state.bought || Object.values(state.calls).reduce((s, c) => s + c.totalHt, 0);
  const coverage = shown.summary.totalHt ? Math.min(1, bought / shown.summary.totalHt) : 0;
  const sup = shown.suppliers.find((s) => s.supplierId === selected);
  const short = shown.products.filter((p) => p.shortQty > 0);
  const timeline = shown.timeline ?? [];
  const eventTotal = timeline.reduce((s, d) => s + d.eventHt, 0);
  const peak = timeline.reduce<SupplyDay | undefined>((b, d) => (!b || d.eventHt > b.eventHt ? d : b), undefined);
  const firstShort = shown.suppliers.length ? shown.suppliers.map((s) => s.deliverBy).sort()[0] : undefined;
  const covered = coveredByDay(shown, state);
  const pins = shown.suppliers.map((s) => ({
    id: s.supplierId, name: s.name, city: s.city, lat: s.lat, lng: s.lng, leadTimeDays: s.leadTimeDays, totalHt: s.totalHt,
    orderedHt: state.calls[s.supplierId]?.totalHt ?? 0, status: state.calls[s.supplierId]?.status ?? "queued",
  }));

  return (
    <>
      <div className="grid shrink-0 grid-cols-2 border-b border-border md:grid-cols-4">
        <Kpi i={0} label="Pic de ventes prévu (événements)" sub={peak?.events.length ? `pic ${dayLabel(peak.date)} : ${peak.events.map(shortEvent).join(", ")}` : "sur les 7 prochains jours"} accent>
          <Count value={eventTotal} format={(v) => `+${fmtMoney(v, { compact: true })}`} />
        </Kpi>
        <Kpi i={1} label="À commander pour tenir" sub={`${shown.summary.shortProducts} produits manqueront${firstShort ? ` dès ${dayLabel(firstShort)}` : ""}`}>
          <Count value={shown.summary.totalHt} format={(v) => fmtMoney(v, { compact: true })} />
        </Kpi>
        <Kpi i={2} label="Commandé aux fournisseurs" sub={state.phase === "idle" ? `${shown.suppliers.length} fournisseurs à appeler` : `${answered}/${shown.suppliers.length} appels`}>
          <Count value={bought} format={(v) => fmtMoney(v, { compact: true })} />
        </Kpi>
        <Kpi i={3} label="Rupture évitée" sub="part du manque couverte par les commandes">
          <Count value={coverage * 100} format={(v) => `${Math.round(v)} %`} />
        </Kpi>
      </div>

      <div className="flex min-h-0 flex-1">
        <div className="w-[440px] shrink-0 overflow-y-auto border-r border-border px-5 py-4">
          <Step n={1} title="On prévoit le pic de ventes" sub="les prévisions de chaque client, additionnées jour par jour" />
          <SpikeChart days={timeline} covered={covered} />

          <Step n={2} title="On voit ce qui va manquer au dépôt" sub={`stock + marchandise en route, face à la demande du ${dayLabel(shown.from)} au ${dayLabel(shown.to)}`} className="mt-6" />
          <Legend />
          <ul className="mt-2.5 flex flex-col gap-2">
            {short.map((p, i) => <ShortRow key={p.productId} p={p} i={i} covered={coveredQty(state, p.productId)} />)}
          </ul>
        </div>

        <div className="flex min-w-0 flex-1 flex-col">
          <div className="flex shrink-0 items-start justify-between gap-3 px-5 pt-4">
            <Step n={3} title="On appelle les fournisseurs à l'avance"
              sub="l'agent vocal appelle chaque fournisseur en même temps, comme acheteur de Médina Viandes, et envoie le bon de commande s'il dit oui" />
          </div>
          <div className="relative min-h-[260px] flex-1">
            <SupplierMap depot={shown.depot} pins={pins} selected={selected} onSelect={setSelected} />
            <AnimatePresence>
              {sup ? (
                <motion.div key={sup.supplierId} initial={{ opacity: 0, x: 24 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: 24 }} transition={{ duration: 0.2 }}
                  className="absolute bottom-3 right-3 top-3 z-30 w-[400px] max-w-[calc(100%-24px)] overflow-y-auto rounded-xl border border-border bg-background/95 p-4 shadow-pop backdrop-blur">
                  <SupplierDetail s={sup} date={shown.from} call={state.calls[sup.supplierId]} onBack={() => setSelected(undefined)} />
                </motion.div>
              ) : null}
            </AnimatePresence>
          </div>
          <ul className="grid shrink-0 grid-flow-col auto-cols-[minmax(230px,1fr)] gap-2 overflow-x-auto border-t border-border px-4 py-3">
            {shown.suppliers.map((s, i) => (
              <motion.li key={s.supplierId} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ ...spring, delay: 0.2 + i * 0.05 }}>
                <SupplierCard s={s} call={state.calls[s.supplierId]} selected={selected === s.supplierId} onClick={() => setSelected(selected === s.supplierId ? undefined : s.supplierId)} />
              </motion.li>
            ))}
          </ul>
        </div>
      </div>
    </>
  );
}

function coveredQty(s: BuyState, productId: string) {
  return Object.values(s.calls).reduce((a, c) => a + c.lines.filter((l) => l.productId === productId).reduce((x, l) => x + l.qty, 0), 0);
}

/** € of each day's shortfall now covered by the purchase orders: the ordered qty fills the earliest missing days first. */
function coveredByDay(sup: Supply, s: BuyState): number[] {
  const out = (sup.timeline ?? []).map(() => 0);
  for (const p of sup.products) {
    let left = coveredQty(s, p.productId);
    (p.shortByDay ?? []).forEach((miss, d) => {
      const take = Math.min(left, miss);
      left -= take;
      out[d] += take * (p.unitPrice ?? 0);
    });
  }
  return out;
}

const shortEvent = (t: string) => t.split(/ (?:du|au) /)[0];
const kEur = (v: number) => (v >= 1000 ? `${(v / 1000).toFixed(v >= 10_000 ? 0 : 1).replace(".", ",")} k€` : `${Math.round(v)} €`);
const dayShort = (iso: string) => new Date(`${iso}T12:00:00`).toLocaleDateString("fr-FR", { weekday: "short", day: "numeric" }).replace(".", "");

function Step({ n, title, sub, className }: { n: number; title: string; sub?: string; className?: string }) {
  return (
    <div className={cn("mb-2.5 flex items-start gap-2.5", className)}>
      <span className="num mt-px grid h-5 w-5 shrink-0 place-items-center rounded-full bg-foreground text-[11px] font-semibold text-background">{n}</span>
      <div className="min-w-0">
        <h2 className="text-[14px] font-semibold leading-5 tracking-[-0.01em]">{title}</h2>
        {sub ? <p className="text-[11.5px] leading-4 text-muted-foreground">{sub}</p> : null}
      </div>
    </div>
  );
}

/**
 * The spike: what the depot will sell each day (at purchase price), stacked as already ordered / a normal day / what the
 * events add. The thin bar on the right of each day is the part the stock can't serve: red, turning green as the
 * suppliers say yes.
 */
function SpikeChart({ days, covered }: { days: SupplyDay[]; covered: number[] }) {
  const [hover, setHover] = useState<number>();
  if (!days.length) return null;
  const W = 420, H = 190, top = 34, bottom = 22, left = 34;
  const max = Math.max(...days.map((d) => d.orderedHt + d.needHt), 1);
  const nice = Math.ceil(max / 10_000) * 10_000;
  const y = (v: number) => top + (H - top - bottom) * (1 - v / nice);
  const slot = (W - left) / days.length;
  const bw = Math.min(30, slot * 0.5);
  const peak = days.reduce((b, d, i) => (d.eventHt > days[b].eventHt ? i : b), 0);
  const tip = hover !== undefined ? days[hover] : undefined;
  return (
    <div className="relative">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="Ventes prévues par jour, avec la part due aux événements et la part que le stock ne couvre pas">
        {[0, 0.5, 1].map((k) => (
          <g key={k}>
            <line x1={left} x2={W} y1={y(nice * k)} y2={y(nice * k)} stroke="hsl(var(--border))" strokeWidth={1} />
            <text x={left - 6} y={y(nice * k) + 3.5} textAnchor="end" className="fill-muted-foreground text-[9.5px]">{kEur(nice * k)}</text>
          </g>
        ))}
        {days.map((d, i) => {
          const cx = left + slot * i + slot / 2;
          const x = cx - bw / 2 - 5;
          const ord = d.orderedHt, norm = d.normalHt, ev = d.eventHt;
          const y0 = y(0), y1 = y(ord), y2 = y(ord + norm), y3 = y(ord + norm + ev);
          const miss = d.shortHt, cov = Math.min(miss, covered[i] ?? 0);
          const closed = ord + d.needHt < 1;
          return (
            <g key={d.date} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(undefined)}>
              <rect x={cx - slot / 2} y={top - 30} width={slot} height={H - top + 30} fill="transparent" />
              {hover === i ? <rect x={cx - slot / 2 + 2} y={top - 6} width={slot - 4} height={H - top - bottom + 6} rx={6} fill="hsl(var(--muted))" opacity={0.6} /> : null}
              {closed ? <text x={cx} y={y0 - 6} textAnchor="middle" className="fill-muted-foreground text-[9.5px]">fermé</text> : (
                <>
                  <motion.g initial={{ scaleY: 0 }} animate={{ scaleY: 1 }} transition={{ duration: 0.6, delay: 0.1 + i * 0.06, ease: "easeOut" }} style={{ transformOrigin: `${x}px ${y0}px` }}>
                    {ord > 0 ? <rect x={x} y={y1} width={bw} height={y0 - y1} fill="hsl(var(--foreground) / .45)" /> : null}
                    <rect x={x} y={y2} width={bw} height={Math.max(0, y1 - y2 - (ord > 0 ? 1.5 : 0))} fill="hsl(var(--foreground) / .2)" />
                    {ev > 0 ? <rect x={x} y={y3} width={bw} height={Math.max(0, y2 - y3 - 1.5)} rx={3} fill="hsl(var(--chart-3))" /> : null}
                  </motion.g>
                  {miss > 1 ? (
                    <g>
                      <rect x={x + bw + 3} y={y(miss)} width={7} height={y0 - y(miss)} rx={2} fill="hsl(var(--destructive) / .75)" />
                      <motion.rect x={x + bw + 3} width={7} rx={2} fill="hsl(var(--primary))" initial={false}
                        animate={{ y: y(cov), height: y0 - y(cov) }} transition={{ duration: 0.6 }} />
                    </g>
                  ) : null}
                  {ev >= 500 ? (
                    <text x={cx - 5} y={y3 - 6} textAnchor="middle" className="fill-foreground text-[10px] font-semibold">+{kEur(ev)}</text>
                  ) : null}
                </>
              )}
              <text x={cx} y={H - 6} textAnchor="middle" className={cn("text-[10px]", i === peak ? "fill-foreground font-semibold" : "fill-muted-foreground")}>{dayShort(d.date)}</text>
            </g>
          );
        })}
        {days[peak].eventHt > 0 ? (() => {
          const cx = left + slot * peak + slot / 2;
          return (
            <motion.g initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.8 }}>
              <text x={Math.min(Math.max(cx, left + 60), W - 60)} y={11} textAnchor="middle" className="fill-chart-3 text-[10.5px] font-semibold">
                ▲ pic : {days[peak].events.slice(0, 2).map(shortEvent).join(" + ")}
              </text>
            </motion.g>
          );
        })() : null}
      </svg>
      {tip ? (
        <div className="pointer-events-none absolute z-10 w-[210px] rounded-lg border border-border bg-card px-2.5 py-2 text-[11.5px] shadow-pop"
          style={{ left: `${Math.min(68, Math.max(0, ((hover! + 0.5) / days.length) * 100 - 20))}%`, top: 0 }}>
          <div className="mb-1 font-medium first-letter:uppercase">{dayLabel(tip.date)}</div>
          {tip.orderedHt > 0 ? <Row k="Déjà commandé" v={kEur(tip.orderedHt)} /> : null}
          <Row k="Prévu, jour normal" v={kEur(tip.normalHt)} />
          <Row k="En plus (événements)" v={`+${kEur(tip.eventHt)}`} />
          {tip.events.length ? <div className="mt-0.5 text-[11px] text-muted-foreground">{tip.events.join(" · ")}</div> : null}
          <div className="mt-1 border-t border-border pt-1"><Row k="Le stock ne suit plus" v={kEur(tip.shortHt)} /></div>
        </div>
      ) : null}
      <div className="mt-1 flex flex-wrap gap-x-3.5 gap-y-1 text-[11px] text-muted-foreground">
        <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-sm bg-foreground/45" />déjà commandé</span>
        <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-sm bg-foreground/20" />prévu, jour normal</span>
        <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-sm bg-chart-3" />en plus à cause des événements</span>
        <span className="flex items-center gap-1.5"><span className="h-2.5 w-1.5 rounded-sm bg-destructive/75" />manque</span>
        <span className="flex items-center gap-1.5"><span className="h-2.5 w-1.5 rounded-sm bg-primary" />commandé au fournisseur</span>
      </div>
    </div>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return <div className="flex justify-between gap-3"><span className="text-muted-foreground">{k}</span><span className="num font-medium">{v}</span></div>;
}

function Legend() {
  const dot = (cls: string, label: string) => <span className="flex items-center gap-1.5"><span className={cn("h-2.5 w-2.5 rounded-sm", cls)} />{label}</span>;
  return (
    <div className="flex flex-wrap gap-4 text-[11.5px] text-muted-foreground">
      {dot("bg-foreground/30", "en stock")}
      {dot("bg-chart-4/60", "déjà en route")}
      {dot("bg-destructive/70", "manque")}
      {dot("bg-primary", "commandé par l'agent")}
    </div>
  );
}

function ShortRow({ p, i, covered }: { p: SupplyLine; i: number; covered: number }) {
  const total = Math.max(p.needQty, p.available + p.incoming + covered, 1);
  const pct = (v: number) => `${(Math.max(0, v) / total) * 100}%`;
  const gap = Math.max(0, p.shortQty - covered);
  return (
    <motion.li initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ ...spring, delay: 0.1 + i * 0.04 }}
      className="rounded-lg border border-border bg-card px-3 py-2.5">
      <div className="flex items-center gap-2 text-[12.5px]">
        <PackageX size={14} className={cn("shrink-0", gap > 0 ? "text-destructive" : "text-primary")} />
        <span className="min-w-0 flex-1 truncate font-medium">{p.productName}</span>
        <span className={cn("num whitespace-nowrap font-semibold", gap > 0 ? "text-destructive" : "text-primary")}>
          {gap > 0 ? `−${fmtQty(Math.round(gap), p.unit)}` : <span className="flex items-center gap-1"><Check size={12} />couvert</span>}
        </span>
      </div>
      <div className="mt-2 flex h-3 overflow-hidden rounded-full bg-muted/60">
        <motion.div initial={{ scaleX: 0 }} animate={{ scaleX: 1 }} transition={{ duration: 0.6, delay: 0.2 + i * 0.04 }} className="h-full origin-left bg-foreground/30" style={{ width: pct(p.available) }} />
        <motion.div initial={{ scaleX: 0 }} animate={{ scaleX: 1 }} transition={{ duration: 0.6, delay: 0.3 + i * 0.04 }} className="h-full origin-left bg-chart-4/60" style={{ width: pct(p.incoming) }} />
        <motion.div layout className="h-full bg-primary" style={{ width: pct(Math.min(covered, p.shortQty)) }} transition={{ duration: 0.6 }} />
        <motion.div layout className="h-full bg-destructive/70" style={{ width: pct(gap) }} transition={{ duration: 0.6 }} />
      </div>
      <p className="mt-1.5 text-[11.5px] text-muted-foreground">
        Les clients vont demander <b className="num text-foreground">{fmtQty(Math.round(p.needQty), p.unit)}</b> ({p.clients} clients)
        {p.eventQty > 1 ? <>, dont <b className="num text-foreground">{fmtQty(Math.round(p.eventQty), p.unit)}</b> en plus à cause {p.events.length ? `de : ${p.events.map(shortEvent).join(", ")}` : "des événements"}</> : null}
        {" · "}rupture à partir de <b className="text-foreground">{dayLabel(p.firstDay)}</b>
      </p>
    </motion.li>
  );
}

const STATUS: Record<Status, { label: string; icon: typeof Check; cls: string }> = {
  queued: { label: "À appeler", icon: Clock, cls: "text-muted-foreground" },
  ringing: { label: "Ça sonne", icon: PhoneCall, cls: "text-agent-voice" },
  talking: { label: "En ligne", icon: PhoneCall, cls: "text-agent-voice" },
  confirmed: { label: "Commandé", icon: Check, cls: "text-primary" },
  modified: { label: "Partiel", icon: Check, cls: "text-chart-3" },
  declined: { label: "Refus", icon: X, cls: "text-muted-foreground" },
  callback: { label: "À rappeler", icon: Clock, cls: "text-warning" },
  failed: { label: "Échec", icon: PhoneOff, cls: "text-destructive" },
};

function SupplierCard({ s, call, selected, onClick }: { s: SupplierNeed; call?: BuyCall; selected?: boolean; onClick: () => void }) {
  const st = STATUS[call?.status ?? "queued"];
  const Icon = st.icon;
  const live = call?.status === "ringing" || call?.status === "talking";
  const last = call?.transcript.slice(-1)[0];
  return (
    <button type="button" onClick={onClick}
      className={cn("relative h-full w-full overflow-hidden rounded-lg border bg-card px-3 py-2.5 text-left transition-colors hover:border-border-hover",
        live ? "border-agent-voice/50" : selected ? "border-foreground/40" : "border-border")}>
      {live ? <motion.span className="absolute inset-y-0 left-0 w-[3px] bg-agent-voice" initial={{ scaleY: 0 }} animate={{ scaleY: 1 }} /> : null}
      <div className="flex items-center gap-2">
        <span className="grid h-7 w-7 shrink-0 place-items-center rounded-md bg-muted text-muted-foreground"><Factory size={14} /></span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[12.5px] font-medium">{s.name}</span>
          <span className="block truncate text-[11px] text-muted-foreground">{s.contactName} · {s.city}</span>
        </span>
        <span className="text-right">
          <span className={cn("flex items-center justify-end gap-1 text-[11px] font-medium", st.cls)}><Icon size={12} />{st.label}</span>
          <span className="num block text-[12px] font-medium">{fmtMoney(call?.totalHt || s.totalHt, { compact: true })}</span>
        </span>
      </div>
      <p className="mt-1.5 line-clamp-2 text-[11.5px] leading-4 text-muted-foreground">
        {last && (live || call?.status !== "queued")
          ? <><span className={cn("mr-1 font-medium", last.who === "agent" ? "text-agent-voice" : "text-foreground")}>{last.who === "agent" ? "Agent" : "Fournisseur"}</span>{last.text}</>
          : s.reason}
      </p>
    </button>
  );
}

function SupplierDetail({ s, date, call, onBack }: { s: SupplierNeed; date: string; call?: BuyCall; onBack: () => void }) {
  const st = STATUS[call?.status ?? "queued"];
  const [explain, setExplain] = useState(false);
  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-start gap-2">
        <button type="button" onClick={onBack} aria-label="Retour" className="-ml-1 mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"><ArrowLeft size={14} /></button>
        <div className="min-w-0 flex-1">
          <h2 className="truncate text-[15px] font-semibold tracking-[-0.01em]">{s.name}</h2>
          <p className="text-[12px] text-muted-foreground">{s.contactName} · {s.city} · livre en J+{s.leadTimeDays} · <span className={st.cls}>{st.label}</span></p>
        </div>
      </div>
      <div className="rounded-lg border border-chart-3/30 bg-chart-3/[.06] p-2.5 text-[12px] leading-[17px]">
        <p className="flex items-start gap-1.5"><Sparkles size={13} className="mt-0.5 shrink-0 text-chart-3" />{s.reason}.</p>
        <p className="mt-1 text-muted-foreground">À livrer avant {dayLabel(s.deliverBy)} · {s.onTime ? "le délai du fournisseur le permet" : "délai serré : à confirmer au téléphone"}</p>
      </div>
      {!explain ? (
        <motion.button type="button" onClick={() => setExplain(true)}
          initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} transition={{ ...spring, delay: 0.1 }}
          whileHover={{ y: -1 }} whileTap={{ scale: 0.985 }}
          className="group relative -mt-1 flex w-full items-center gap-3 overflow-hidden rounded-xl border border-primary/40 bg-primary/[.08] px-3 py-2.5 text-left transition-colors hover:border-primary/70 hover:bg-primary/[.12]">
          <motion.span aria-hidden className="pointer-events-none absolute inset-y-0 -left-1/3 w-1/3 bg-gradient-to-r from-transparent via-primary/15 to-transparent"
            animate={{ x: ["0%", "450%"] }} transition={{ duration: 2.6, repeat: Infinity, repeatDelay: 1.8, ease: "easeInOut" }} />
          <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-primary text-primary-foreground"><BarChart3 size={16} /></span>
          <span className="min-w-0 flex-1">
            <span className="block text-[13px] font-semibold">Pourquoi commander {fmtMoney(s.totalHt, { compact: true })} ?</span>
            <span className="block truncate text-[11.5px] text-muted-foreground">Les clients, la même semaine l'an dernier, le stock, le calcul</span>
          </span>
          <ChevronRight size={16} className="shrink-0 text-primary transition-transform group-hover:translate-x-0.5" />
        </motion.button>
      ) : null}
      <AnimatePresence initial={false}>
        {explain ? <SupplyExplain key="explain" supplierId={s.supplierId} date={date} onClose={() => setExplain(false)} /> : null}
      </AnimatePresence>
      <div>
        <h3 className="mb-1.5 text-[11px] font-medium uppercase tracking-[0.04em] text-muted-foreground">{call?.orderId ? `Commande ${call.number ?? ""} envoyée` : "Ce qu'on va commander"}</h3>
        <ul className="divide-y divide-border rounded-lg border border-border bg-card">
          {s.lines.map((l) => {
            const got = call?.lines.find((x) => x.productId === l.productId);
            return (
              <li key={l.productId} className="flex items-center gap-2 px-2.5 py-1.5 text-[12px]">
                <span className="min-w-0 flex-1 truncate">{l.productName}</span>
                {got && got.qty < (l.qty ?? 0) ? <span className="num text-[11px] text-muted-foreground line-through">{fmtQty(l.qty ?? 0, l.unit)}</span> : null}
                <span className={cn("num whitespace-nowrap", got && "font-medium text-primary")}>{fmtQty(got?.qty ?? l.qty ?? 0, l.unit)}</span>
                <span className="num w-[64px] text-right text-muted-foreground">{fmtMoney((got?.qty ?? l.qty ?? 0) * (got?.unitPrice ?? l.unitPrice ?? 0), { compact: true })}</span>
              </li>
            );
          })}
        </ul>
      </div>
      {call?.transcript.length ? (
        <div>
          <h3 className="mb-1.5 text-[11px] font-medium uppercase tracking-[0.04em] text-muted-foreground">Appel</h3>
          <div className="flex flex-col gap-1.5">
            {call.transcript.map((t, i) => (
              <motion.div key={i} initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }}
                className={cn("max-w-[88%] rounded-lg px-2.5 py-1.5 text-[12px] leading-[17px]", t.who === "agent" ? "self-start bg-agent-voice/10" : "self-end bg-muted")}>
                {t.text}
              </motion.div>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function Kpi({ label, sub, children, i, accent }: { label: string; sub: string; children: React.ReactNode; i: number; accent?: boolean }) {
  return (
    <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ type: "spring", stiffness: 380, damping: 32, delay: 0.05 + i * 0.05 }}
      className={cn("min-w-0 px-5 py-3", i > 0 && "md:border-l md:border-border", i % 2 === 1 && "border-l border-border md:border-l")}>
      <div className="text-[11.5px] text-muted-foreground">{label}</div>
      <div className={cn("mt-0.5 text-[22px] font-semibold leading-7 tracking-[-0.02em]", accent && "text-chart-3")}>{children}</div>
      <div className="truncate text-[11.5px] text-muted-foreground">{sub}</div>
    </motion.div>
  );
}

/** « Approvisionnement | Clients » switch for the radar header. */
export function RadarSwitch({ value, onChange }: { value: "clients" | "supply"; onChange: (v: "clients" | "supply") => void }) {
  return (
    <div className="flex rounded-lg border border-border bg-muted/50 p-0.5 text-[12.5px]">
      {([["supply", "Approvisionnement"], ["clients", "Clients"]] as const).map(([v, label]) => (
        <button key={v} type="button" onClick={() => onChange(v)}
          className={cn("relative rounded-md px-3 py-1 font-medium transition-colors", value === v ? "text-foreground" : "text-muted-foreground hover:text-foreground")}>
          {value === v ? <motion.span layoutId="radar-switch" className="absolute inset-0 rounded-md bg-card shadow-sm" transition={spring} /> : null}
          <span className="relative">{label}</span>
        </button>
      ))}
    </div>
  );
}


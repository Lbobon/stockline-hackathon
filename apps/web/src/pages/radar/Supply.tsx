/**
 * Radar fournisseurs: the other side of the radar. The clients' forecast, added up over the next days, against the
 * stock: what will run out because of the events, and the suppliers to call to buy it.
 * « Appeler les fournisseurs » rings every supplier in parallel (POST /ai/campaigns {kind:"supply"}): the voice agent
 * is Médina's buyer, and each yes writes and sends a real purchase order. Data: GET /api/radar/supply.
 */
import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Check, Clock, Factory, PackageX, PhoneCall, PhoneOff, RotateCcw, Sparkles, X } from "lucide-react";
import { Button, Spinner } from "@/ui";
import { cn } from "@/lib/cn";
import { fmtMoney, fmtQty } from "@/lib/format";
import { AI_BASE, API_BASE } from "@/lib/api";
import { Count } from "./Panel";

// ── data ──

export interface SupplyLine {
  productId: string; productName: string; unit: string; needQty: number; eventQty: number; available: number; incoming: number;
  shortQty: number; firstDay: string; clients: number; events: string[]; qty?: number; unitPrice?: number; totalHt?: number;
}
export interface SupplierNeed {
  supplierId: string; name: string; city?: string; contactName?: string; phone?: string; leadTimeDays?: number; minOrderAmount?: number;
  lines: SupplyLine[]; totalHt: number; eventHt: number; events: string[]; deliverBy: string; arrives: string; onTime: boolean;
  belowMinimum: boolean; reason: string;
}
export interface Supply {
  from: string; to: string; days: number; products: SupplyLine[]; suppliers: SupplierNeed[];
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
  const start = useCallback(async () => {
    if (!supply) return;
    es.current?.close();
    const r = await fetch(`${AI_BASE}/campaigns`, { method: "POST", headers: { "content-type": "application/json", "x-actor": "user" },
      body: JSON.stringify({ kind: "supply", date: supply.from, concurrency: 6 }) });
    if (!r.ok) throw new Error(`${r.status}`);
    const { id } = await r.json();
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

  const launch = useCallback(() => { setSelected(undefined); setPlan(supply); start().catch(() => reset()); }, [supply, start, reset]);
  const again = useCallback(() => { reset(); setPlan(undefined); setSelected(undefined); }, [reset]);

  const answered = Object.values(state.calls).filter((c) => !["queued", "ringing", "talking"].includes(c.status)).length;
  useEffect(() => {
    actionsSlot(
      <>
        {state.phase === "done" ? <Button variant="ghost" icon={RotateCcw} onClick={again}>Recommencer</Button> : null}
        <Button variant="primary" icon={running ? undefined : PhoneCall} disabled={running || !shown?.suppliers.length || state.phase === "done"} onClick={launch}>
          {running ? <><Spinner size={12} /> Fournisseurs {answered}/{shown?.suppliers.length ?? 0}</>
            : state.phase === "done" ? "Commandes passées" : `Appeler les fournisseurs (${shown?.suppliers.length ?? 0})`}
        </Button>
      </>,
    );
  }, [actionsSlot, running, answered, shown, state.phase, launch, again]);

  if (isLoading || !shown) return <div className="grid flex-1 place-items-center"><Spinner /></div>;

  const bought = state.bought || Object.values(state.calls).reduce((s, c) => s + c.totalHt, 0);
  const coverage = shown.summary.totalHt ? Math.min(1, bought / shown.summary.totalHt) : 0;
  const sup = shown.suppliers.find((s) => s.supplierId === selected);
  const short = shown.products.filter((p) => p.shortQty > 0);

  return (
    <>
      <div className="grid shrink-0 grid-cols-2 border-b border-border md:grid-cols-4">
        <Kpi i={0} label="À acheter cette semaine" sub={`${shown.summary.shortProducts} produits vont manquer`}>
          <Count value={shown.summary.totalHt} format={(v) => fmtMoney(v, { compact: true })} />
        </Kpi>
        <Kpi i={1} label="Dont à cause des événements" sub="mariages, marché de Noël, matchs…" accent>
          <Count value={shown.summary.eventHt} format={(v) => `+${fmtMoney(v, { compact: true })}`} />
        </Kpi>
        <Kpi i={2} label="Commandé aux fournisseurs" sub={state.phase === "idle" ? `${shown.suppliers.length} fournisseurs à appeler` : `${answered}/${shown.suppliers.length} appels`}>
          <Count value={bought} format={(v) => fmtMoney(v, { compact: true })} />
        </Kpi>
        <Kpi i={3} label="Rupture évitée" sub="part du manque couverte par les commandes">
          <Count value={coverage * 100} format={(v) => `${Math.round(v)} %`} />
        </Kpi>
      </div>

      <div className="flex min-h-0 flex-1">
        <div className="min-w-0 flex-1 overflow-y-auto px-6 py-5">
          <div className="mb-3 flex items-baseline gap-2">
            <h2 className="text-[15px] font-semibold tracking-[-0.01em]">Ce qui va manquer au dépôt</h2>
            <span className="text-[12px] text-muted-foreground">du {dayLabel(shown.from)} au {dayLabel(shown.to)} · prévisions clients additionnées, face au stock</span>
          </div>
          <Legend />
          <ul className="mt-3 flex flex-col gap-2.5">
            {short.map((p, i) => <ShortRow key={p.productId} p={p} i={i} covered={coveredQty(state, p.productId)} />)}
          </ul>
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
                    Fournisseurs à appeler <span className="num normal-case">{shown.suppliers.length}</span>
                  </h2>
                  <p className="mb-2.5 text-[12px] leading-4 text-muted-foreground">
                    L'agent appelle chaque fournisseur en même temps, comme acheteur de Médina Viandes, et passe la commande s'il dit oui.
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

function coveredQty(s: BuyState, productId: string) {
  return Object.values(s.calls).reduce((a, c) => a + c.lines.filter((l) => l.productId === productId).reduce((x, l) => x + l.qty, 0), 0);
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
        {p.events.map((e) => <span key={e} className="hidden rounded-full bg-chart-3/15 px-2 py-px text-[10.5px] font-medium text-chart-3 lg:inline">{e}</span>)}
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
        {p.eventQty > 1 ? <>, dont <b className="num text-chart-3">{fmtQty(Math.round(p.eventQty), p.unit)}</b> à cause des événements</> : null}
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

function SupplierCard({ s, call, onClick }: { s: SupplierNeed; call?: BuyCall; onClick: () => void }) {
  const st = STATUS[call?.status ?? "queued"];
  const Icon = st.icon;
  const live = call?.status === "ringing" || call?.status === "talking";
  const last = call?.transcript.slice(-1)[0];
  return (
    <button type="button" onClick={onClick}
      className={cn("relative w-full overflow-hidden rounded-lg border bg-card px-3 py-2.5 text-left transition-colors hover:border-border-hover",
        live ? "border-agent-voice/50" : "border-border")}>
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

function SupplierDetail({ s, call, onBack }: { s: SupplierNeed; call?: BuyCall; onBack: () => void }) {
  const st = STATUS[call?.status ?? "queued"];
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

/** « Clients | Fournisseurs » switch for the radar header. */
export function RadarSwitch({ value, onChange }: { value: "clients" | "supply"; onChange: (v: "clients" | "supply") => void }) {
  return (
    <div className="flex rounded-lg border border-border bg-muted/50 p-0.5 text-[12.5px]">
      {([["clients", "Clients"], ["supply", "Fournisseurs"]] as const).map(([v, label]) => (
        <button key={v} type="button" onClick={() => onChange(v)}
          className={cn("relative rounded-md px-3 py-1 font-medium transition-colors", value === v ? "text-foreground" : "text-muted-foreground hover:text-foreground")}>
          {value === v ? <motion.span layoutId="radar-switch" className="absolute inset-0 rounded-md bg-card shadow-sm" transition={spring} /> : null}
          <span className="relative">{label}</span>
        </button>
      ))}
    </div>
  );
}


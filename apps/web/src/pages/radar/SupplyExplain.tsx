/**
 * « Pourquoi cette commande ? »: why the radar buys these quantities from a supplier, explained for anyone.
 * Opens from the supplier panel of the Radar d'approvisionnement. For each product line, five steps on real data
 * (GET /api/radar/supply/{id}/explain): the clients who will need it and when, what the depot really sold the same week
 * in past years, what is already in stock or on its way, the arithmetic of the order, how sure we are.
 */
import { useState } from "react";
import { motion } from "framer-motion";
import { useQuery } from "@tanstack/react-query";
import { Calculator, Gauge, History as HistoryIcon, Users, Warehouse, X } from "lucide-react";
import { API_BASE } from "@/lib/api";
import { cn } from "@/lib/cn";
import { fmtMoney, fmtQty } from "@/lib/format";

const ease = [0.22, 1, 0.36, 1] as const;

interface Window_ { from: string; to: string; qty: number; orders: number; clients: number }
interface ExplainLine {
  productId: string; productName: string; unit: string; needQty: number; eventQty: number; orderedQty: number;
  available: number; onHand: number; reserved: number; incoming: number; shortQty: number; firstDay: string; events: string[];
  onWay: { number?: string; qty: number; expectedDate?: string; supplier?: string }[];
  safetyPct: number; safetyQty: number; qty: number; unitPrice: number; totalHt: number; priceSource: string;
  days: { date: string; qty: number; eventQty: number }[];
  clientsCount: number;
  clients: { clientId: string; name: string; city?: string; qty: number; eventQty: number; probability: number; days: string[]; events: string[] }[];
  history: { lastYear: Window_; twoYears: Window_; normal: Window_; normalLastYear: Window_ };
}
interface Explain {
  supplierId: string; name: string; city?: string; from: string; to: string; leadTimeDays?: number; arrives?: string;
  deliverBy?: string; onTime?: boolean; totalHt: number; accuracy: number; lines: ExplainLine[];
}

const day = (iso: string, opts: Intl.DateTimeFormatOptions = { weekday: "short", day: "numeric" }) =>
  new Date(`${iso}T12:00:00`).toLocaleDateString("fr-FR", opts).replace(".", "");
const dayLong = (iso: string) => day(iso, { weekday: "long", day: "numeric", month: "long" });
const shortEvent = (t: string) => t.split(/ (?:du|au) /)[0];
const q = (v: number, unit: string) => fmtQty(Math.round(v), unit);
const pct = (v: number) => `${v >= 0 ? "+" : "−"}${Math.abs(Math.round(v * 100))} %`;

export function SupplyExplain({ supplierId, date, onClose }: { supplierId: string; date: string; onClose: () => void }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["radar", "supply-explain", supplierId, date],
    queryFn: async (): Promise<Explain> => {
      const r = await fetch(`${API_BASE}/radar/supply/${supplierId}/explain?date=${date}&days=7`);
      if (!r.ok) throw new Error(`${r.status}`);
      return r.json();
    },
    staleTime: 60_000,
  });
  const [pick, setPick] = useState(0);
  const l = data?.lines[Math.min(pick, (data?.lines.length ?? 1) - 1)];

  return (
    <motion.div initial={{ opacity: 0, y: 8, scale: 0.985 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: 6 }}
      transition={{ duration: 0.35, ease }}
      className="flex flex-col gap-3 rounded-xl border border-primary/25 bg-gradient-to-b from-primary/[.06] to-transparent p-3">
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <h3 className="text-[14px] font-semibold tracking-[-0.01em]">Pourquoi cette commande ?</h3>
          <p className="text-[11.5px] text-muted-foreground">Chaque quantité expliquée en 5 étapes, à partir des prévisions clients, des ventes passées et du stock.</p>
        </div>
        <button type="button" onClick={onClose} aria-label="Fermer l'explication"
          className="grid h-6 w-6 shrink-0 place-items-center rounded text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"><X size={14} /></button>
      </div>

      {isLoading ? <div className="flex flex-col gap-2">{[0, 1, 2].map((i) => <div key={i} className="h-[90px] animate-pulse rounded-lg bg-muted" />)}</div> : null}
      {isError ? <p className="text-[12px] text-destructive">Impossible de charger l'explication.</p> : null}

      {data && l ? (
        <>
          {data.lines.length > 1 ? (
            <div className="flex flex-wrap gap-1">
              {data.lines.map((x, i) => (
                <button key={x.productId} type="button" onClick={() => setPick(i)}
                  className={cn("rounded-full border px-2.5 py-1 text-[11.5px] transition-colors",
                    i === pick ? "border-primary/60 bg-primary/15 font-medium text-foreground" : "border-border text-muted-foreground hover:text-foreground")}>
                  {x.productName.split(" (")[0]} · <span className="num">{q(x.qty, x.unit)}</span>
                </button>
              ))}
            </div>
          ) : null}
          <div key={l.productId} className="flex flex-col gap-3">
            <Who l={l} from={data.from} to={data.to} />
            <Past l={l} />
            <Stock l={l} />
            <Order l={l} s={data} />
            <Confidence l={l} accuracy={data.accuracy} />
          </div>
          <p className="text-[10.5px] leading-4 text-muted-foreground">
            La demande vient des prévisions client par client du Radar (modèle LightGBM, 3 ans de ventes), additionnées sur 7 jours. Ventes passées : les vraies commandes livrées.
          </p>
        </>
      ) : null}
    </motion.div>
  );
}

function Step({ n, icon: Icon, title, children, delay }: { n: number; icon: typeof Users; title: string; children: React.ReactNode; delay: number }) {
  return (
    <motion.section initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.35, ease, delay }}
      className="rounded-lg border border-border bg-card p-2.5">
      <div className="mb-2 flex items-center gap-2">
        <span className="num grid h-5 w-5 place-items-center rounded-full bg-primary text-[10.5px] font-semibold text-primary-foreground">{n}</span>
        <Icon size={13} className="text-muted-foreground" />
        <h4 className="text-[12px] font-medium">{title}</h4>
      </div>
      {children}
    </motion.section>
  );
}

// ── 1. who will need it, and when ──

function Who({ l, from, to }: { l: ExplainLine; from: string; to: string }) {
  const max = Math.max(...l.days.map((d) => d.qty), 1);
  const H = 64;
  return (
    <Step n={1} icon={Users} title="Qui va en avoir besoin" delay={0.05}>
      <div className="flex items-end gap-1" style={{ height: H + 16 }}>
        {l.days.map((d, i) => (
          <div key={d.date} className="flex flex-1 flex-col items-center gap-1" title={`${dayLong(d.date)} : ${q(d.qty, l.unit)}${d.eventQty >= 1 ? ` dont ${q(d.eventQty, l.unit)} d'événements` : ""}`}>
            <div className="flex w-full flex-col justify-end" style={{ height: H }}>
              {d.eventQty >= 1 ? (
                <motion.div initial={{ scaleY: 0 }} animate={{ scaleY: 1 }} transition={{ duration: 0.5, ease, delay: 0.2 + i * 0.04 }}
                  className="w-full origin-bottom rounded-t-[3px] bg-chart-3" style={{ height: Math.max(2, (d.eventQty / max) * H) }} />
              ) : null}
              <motion.div initial={{ scaleY: 0 }} animate={{ scaleY: 1 }} transition={{ duration: 0.5, ease, delay: 0.15 + i * 0.04 }}
                className={cn("w-full origin-bottom bg-foreground/25", d.eventQty >= 1 ? "" : "rounded-t-[3px]")}
                style={{ height: d.qty ? Math.max(2, ((d.qty - d.eventQty) / max) * H) : 2 }} />
            </div>
            <span className="num text-[9.5px] leading-3 text-muted-foreground">{day(d.date)}</span>
          </div>
        ))}
      </div>
      <p className="mt-1.5 text-[12px] leading-[17px] text-secondary-foreground">
        <b className="num text-foreground">{l.clientsCount} clients</b> devraient en commander <b className="num text-foreground">{q(l.needQty, l.unit)}</b> du {dayLong(from)} au {dayLong(to)}
        {l.eventQty >= 1 ? <>, dont <b className="num text-foreground">{q(l.eventQty, l.unit)}</b> en plus à cause de : {l.events.map(shortEvent).join(", ")}</> : null}.
        {l.orderedQty >= 1 ? <> Ça s'ajoute aux <b className="num text-foreground">{q(l.orderedQty, l.unit)}</b> déjà commandés, déjà réservés dans le stock.</> : null}
      </p>
      <ul className="mt-2 divide-y divide-border rounded-md border border-border">
        {l.clients.map((c) => (
          <li key={c.clientId} className="flex items-center gap-2 px-2 py-1.5 text-[11.5px]">
            <span className="min-w-0 flex-1">
              <span className="block truncate font-medium">{c.name}</span>
              <span className="block truncate text-[10.5px] text-muted-foreground">
                {c.days.map((d) => day(d)).join(", ")} · {Math.round(c.probability * 100)} % de chances{c.events.length ? ` · ${c.events.map(shortEvent).join(", ")}` : ""}
              </span>
            </span>
            {c.eventQty >= 1 ? <span className="num whitespace-nowrap rounded-full bg-chart-3/15 px-1.5 text-[10.5px] text-chart-3">+{q(c.eventQty, l.unit)}</span> : null}
            <span className="num w-[60px] text-right font-medium">{q(c.qty, l.unit)}</span>
          </li>
        ))}
        {l.clientsCount > l.clients.length ? (
          <li className="px-2 py-1 text-[10.5px] text-muted-foreground">+ {l.clientsCount - l.clients.length} autres clients</li>
        ) : null}
      </ul>
      <p className="mt-1 text-[10.5px] leading-4 text-muted-foreground">Chaque quantité = ce que le client prend d'habitude × sa probabilité de commander ce jour-là.</p>
    </Step>
  );
}

// ── 2. what the depot really sold the same week in past years ──

function Past({ l }: { l: ExplainLine }) {
  const h = l.history;
  const week = l.needQty + l.orderedQty;
  const seasonLY = h.normalLastYear.qty ? h.lastYear.qty / h.normalLastYear.qty - 1 : 0;
  const now = h.normal.qty ? week / h.normal.qty - 1 : 0;
  const rows = [
    { label: "Une semaine normale", sub: "moyenne des 8 dernières semaines", v: h.normal.qty, cls: "bg-foreground/25" },
    { label: "La même semaine l'an dernier", sub: `du ${day(h.lastYear.from, { day: "numeric", month: "short" })} au ${day(h.lastYear.to, { day: "numeric", month: "short", year: "numeric" })} · ${h.lastYear.orders} lignes de commande`, v: h.lastYear.qty, cls: "bg-foreground/45" },
    { label: "Il y a 2 ans", sub: `même semaine · ${h.twoYears.orders} lignes`, v: h.twoYears.qty, cls: "bg-foreground/35" },
    { label: "Cette semaine (prévu)", sub: `${q(l.orderedQty, l.unit)} déjà commandés + ${q(l.needQty, l.unit)} prévus`, v: week, cls: "bg-primary" },
  ];
  const max = Math.max(...rows.map((r) => r.v), 1);
  return (
    <Step n={2} icon={HistoryIcon} title="Ce que disent les années passées" delay={0.12}>
      <ul className="flex flex-col gap-1.5">
        {rows.map((r, i) => (
          <li key={r.label}>
            <div className="flex items-baseline justify-between gap-2 text-[11.5px]">
              <span className={cn(i === 3 && "font-medium text-primary")}>{r.label}</span>
              <span className="num font-medium">{q(r.v, l.unit)}</span>
            </div>
            <div className="mt-0.5 h-2 overflow-hidden rounded-full bg-muted/60">
              <motion.div initial={{ scaleX: 0 }} animate={{ scaleX: 1 }} transition={{ duration: 0.6, ease, delay: 0.2 + i * 0.08 }}
                className={cn("h-full origin-left rounded-full", r.cls)} style={{ width: `${(r.v / max) * 100}%` }} />
            </div>
            <div className="text-[10.5px] leading-4 text-muted-foreground">{r.sub}</div>
          </li>
        ))}
      </ul>
      <p className="mt-2 text-[12px] leading-[17px] text-secondary-foreground">
        {h.lastYear.qty > 0 && h.normalLastYear.qty > 0
          ? <>L'an dernier, cette semaine-là, le dépôt a vendu <b className="num text-foreground">{pct(seasonLY)}</b> par rapport à une semaine normale de l'époque. </>
          : null}
        Cette année on prévoit <b className="num text-foreground">{pct(now)}</b> par rapport à une semaine normale
        {Math.abs(now - seasonLY) < 0.15 && h.lastYear.qty > 0 ? " : c'est cohérent avec ce qui s'est passé l'an dernier." : "."}
      </p>
    </Step>
  );
}

// ── 3. what is already there ──

function Stock({ l }: { l: ExplainLine }) {
  const total = Math.max(l.needQty, l.available + l.incoming, 1);
  const w = (v: number) => `${(Math.max(0, v) / total) * 100}%`;
  return (
    <Step n={3} icon={Warehouse} title="Ce qu'on a déjà" delay={0.19}>
      <div className="flex h-3 overflow-hidden rounded-full bg-muted/60">
        <motion.div initial={{ scaleX: 0 }} animate={{ scaleX: 1 }} transition={{ duration: 0.6, ease, delay: 0.25 }} className="h-full origin-left bg-foreground/30" style={{ width: w(l.available) }} />
        <motion.div initial={{ scaleX: 0 }} animate={{ scaleX: 1 }} transition={{ duration: 0.6, ease, delay: 0.35 }} className="h-full origin-left bg-chart-4/60" style={{ width: w(l.incoming) }} />
        <motion.div initial={{ scaleX: 0 }} animate={{ scaleX: 1 }} transition={{ duration: 0.6, ease, delay: 0.45 }} className="h-full origin-left bg-destructive/70" style={{ width: w(l.shortQty) }} />
      </div>
      <ul className="mt-2 flex flex-col gap-1 text-[11.5px]">
        <Row k={<>En stock, disponible</>} v={q(l.available, l.unit)} sub={l.reserved >= 1 ? `${q(l.onHand, l.unit)} en chambre froide, dont ${q(l.reserved, l.unit)} déjà réservés pour des commandes prises` : undefined} dot="bg-foreground/30" />
        <Row k="Déjà en route" v={q(l.incoming, l.unit)} dot="bg-chart-4/60"
          sub={l.onWay.length ? l.onWay.map((p) => `${p.number ?? "commande"} : ${q(p.qty, l.unit)}${p.expectedDate ? ` le ${day(p.expectedDate)}` : ""}`).join(" · ") : "aucune commande fournisseur en cours"} />
        <Row k="Besoin des clients" v={q(l.needQty, l.unit)} />
        <li className="mt-0.5 flex items-baseline justify-between border-t border-border pt-1 font-medium">
          <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-sm bg-destructive/70" />Il va manquer</span>
          <span className="num text-destructive">{q(l.shortQty, l.unit)}</span>
        </li>
      </ul>
      <p className="mt-1.5 text-[12px] leading-[17px] text-secondary-foreground">
        En servant les clients jour par jour, le stock ne suit plus à partir du <b className="text-foreground">{dayLong(l.firstDay)}</b>.
      </p>
    </Step>
  );
}

function Row({ k, v, sub, dot }: { k: React.ReactNode; v: string; sub?: string; dot?: string }) {
  return (
    <li>
      <div className="flex items-baseline justify-between gap-2">
        <span className="flex items-center gap-1.5">{dot ? <span className={cn("h-2 w-2 rounded-sm", dot)} /> : <span className="w-2" />}{k}</span>
        <span className="num">{v}</span>
      </div>
      {sub ? <div className="pl-3.5 text-[10.5px] leading-4 text-muted-foreground">{sub}</div> : null}
    </li>
  );
}

// ── 4. the order: shortfall + margin, rounded, × price, and in time ──

function Order({ l, s }: { l: ExplainLine; s: Explain }) {
  const withMargin = l.shortQty * (1 + l.safetyPct);
  const steps = [
    { k: "Ce qui va manquer", v: q(l.shortQty, l.unit) },
    { k: `+ ${Math.round(l.safetyPct * 100)} % de marge de sécurité`, v: q(withMargin, l.unit) },
    { k: l.unit === "kg" ? "arrondi aux 5 kg" : "arrondi à l'unité", v: q(l.qty, l.unit), strong: true },
    { k: `× ${fmtMoney(l.unitPrice)} / ${l.unit} (${l.priceSource})`, v: fmtMoney(l.totalHt), strong: true },
  ];
  return (
    <Step n={4} icon={Calculator} title="La commande" delay={0.26}>
      <ol className="flex flex-col">
        {steps.map((x, i) => (
          <motion.li key={x.k} initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }} transition={{ duration: 0.3, ease, delay: 0.3 + i * 0.08 }}
            className={cn("flex items-baseline justify-between gap-2 py-1 text-[11.5px]", i > 0 && "border-t border-dashed border-border", x.strong && "font-medium")}>
            <span className={cn(!x.strong && "text-muted-foreground")}>{x.k}</span>
            <span className={cn("num", i === steps.length - 1 && "text-primary")}>{x.v}</span>
          </motion.li>
        ))}
      </ol>
      <p className="mt-1.5 text-[12px] leading-[17px] text-secondary-foreground">
        {s.name} livre en <b className="text-foreground">J+{s.leadTimeDays ?? 1}</b> : commandé aujourd'hui, ça arrive le <b className="text-foreground">{s.arrives ? dayLong(s.arrives) : "—"}</b>,{" "}
        {s.onTime ? <>avant la rupture du {dayLong(l.firstDay)}. ✓</> : <>trop juste pour la rupture du {dayLong(l.firstDay)} : l'agent demande une livraison plus rapide au téléphone.</>}
      </p>
    </Step>
  );
}

// ── 5. how sure we are ──

function Confidence({ l, accuracy }: { l: ExplainLine; accuracy: number }) {
  const low = l.needQty * (1 - accuracy), high = l.needQty * (1 + accuracy);
  const cover = l.available + l.incoming + l.qty;
  const enough = cover >= high;
  return (
    <Step n={5} icon={Gauge} title="Est-ce qu'on peut s'y fier ?" delay={0.33}>
      <p className="text-[12px] leading-[17px] text-secondary-foreground">
        Testée sur les ventes passées, la prévision se trompe en moyenne de <b className="num text-foreground">±{Math.round(accuracy * 100)} %</b> sur les quantités :
        la vraie demande devrait être entre <b className="num text-foreground">{q(low, l.unit)}</b> et <b className="num text-foreground">{q(high, l.unit)}</b>.
      </p>
      <p className="mt-1 text-[12px] leading-[17px] text-secondary-foreground">
        Avec cette commande, le dépôt aura <b className="num text-foreground">{q(cover, l.unit)}</b> pour la semaine :{" "}
        {enough ? "de quoi tenir même dans le haut de la fourchette." : "assez pour la demande attendue ; si les fêtes font encore plus, l'ADV verra la rupture arriver sur le Radar et rappellera le fournisseur."}
      </p>
    </Step>
  );
}

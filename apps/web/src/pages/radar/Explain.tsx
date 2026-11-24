/**
 * « Comment on arrive à ce prix ? »: the forecast of one client, explained for anyone.
 * Opens from the « Panier prévu » box of the client panel. Five steps, each one picture and one sentence, built on the
 * client's real orders: this weekday lately, the same weeks in past years, what changes this week, product by product,
 * how sure we are.
 */
import { motion } from "framer-motion";
import { useQueries, useQuery } from "@tanstack/react-query";
import { CalendarDays, Gauge, History as HistoryIcon, ListChecks, TrendingUp, X } from "lucide-react";
import { api } from "@/lib/api";
import { cn } from "@/lib/cn";
import { fmtMoney, fmtQty } from "@/lib/format";
import { useOrders } from "@/lib/queries";
import type { Forecast, ForecastClient, ForecastLine } from "@/features/radar/types";

const ease = [0.22, 1, 0.36, 1] as const;
const WEEKS = 12;
const CANCELLED = new Set(["cancelled", "draft"]);

const lineEur = (l: ForecastLine, q: number) => q * (l.unitPrice ?? 0);
const shiftIso = (iso: string, days: number) => {
  const d = new Date(`${iso}T12:00:00`);
  d.setDate(d.getDate() + days);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};
const dayLabel = (iso: string, year = false) =>
  new Date(`${iso}T12:00:00`).toLocaleDateString("fr-FR", { weekday: "short", day: "numeric", month: "short", ...(year ? { year: "numeric" } : {}) });

/** The client's real orders on the last 12 same weekdays (0 € = no order that day). */
function useWeekdayHistory(clientId: string, date: string) {
  const q = useOrders({ clientId, limit: 120 });
  const days = Array.from({ length: WEEKS }, (_, k) => {
    const iso = shiftIso(date, -7 * (WEEKS - k));
    const orders = (q.data ?? []).filter((o) => o.deliveryDate === iso && !CANCELLED.has(o.status));
    return { iso, eur: orders.reduce((s, o) => s + o.totalHt, 0), numbers: orders.map((o) => o.number) };
  });
  const hits = days.filter((d) => d.eur > 0);
  const avg = hits.length ? hits.reduce((s, d) => s + d.eur, 0) / hits.length : 0;
  return { isLoading: q.isLoading, days, hits, avg };
}

export function PredictionExplain({ c, forecast, onClose }: { c: ForecastClient; forecast: Forecast; onClose: () => void }) {
  const date = forecast.date;
  const weekday = new Date(`${date}T12:00:00`).toLocaleDateString("fr-FR", { weekday: "long" });
  const lines = c.status === "upsell" ? c.addLines ?? [] : c.lines;
  const predicted = c.status === "upsell" ? c.upliftHt : c.totalHt;

  // the recipe: usual basket → events → recent trend → predicted
  const usual = lines.reduce((s, l) => s + lineEur(l, l.baseQty), 0);
  const events = lines.filter((l) => l.eventId).reduce((s, l) => s + lineEur(l, l.qty - l.baseQty), 0);
  const trend = predicted - usual - events;

  return (
    <motion.div
      initial={{ opacity: 0, y: 8, scale: 0.985 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: 6 }}
      transition={{ duration: 0.35, ease }}
      className="flex flex-col gap-3 rounded-xl border border-primary/25 bg-gradient-to-b from-primary/[.06] to-transparent p-3"
    >
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <h3 className="text-[14px] font-semibold tracking-[-0.01em]">Comment on arrive à {fmtMoney(predicted)} ?</h3>
          <p className="text-[11.5px] text-muted-foreground">La prévision de {c.name} pour {weekday}, expliquée en 5 étapes, à partir de ses vraies commandes.</p>
        </div>
        <button type="button" onClick={onClose} aria-label="Fermer l'explication"
          className="grid h-6 w-6 shrink-0 place-items-center rounded text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"><X size={14} /></button>
      </div>

      <History c={c} date={date} weekday={weekday} predicted={predicted} />
      <PastYears c={c} date={date} lines={lines} />
      <Recipe usual={usual} events={events} trend={trend} predicted={predicted} upsell={c.status === "upsell"}
        notes={forecast.events.filter((e) => c.eventIds?.includes(e.id) || lines.some((l) => l.eventId === e.id))
          .map((e) => `${e.title} : ${e.basis ?? "estimation de l'équipe"}`)} />
      <Products lines={lines} />
      <Confidence c={c} weekday={weekday} />

      <p className="text-[10.5px] leading-4 text-muted-foreground">
        Calculé par notre modèle LightGBM, entraîné sur 3 ans de ventes : le jour de la semaine, la saison, la météo et les fêtes.
      </p>
    </motion.div>
  );
}

function Step({ n, icon: Icon, title, children, delay }: {
  n: number; icon: typeof TrendingUp; title: string; children: React.ReactNode; delay: number;
}) {
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

// ── 1. what they usually order on this weekday: their real past orders ──

function History({ c, date, weekday, predicted }: { c: ForecastClient; date: string; weekday: string; predicted: number }) {
  const { isLoading, days, hits, avg } = useWeekdayHistory(c.clientId, date);
  const max = Math.max(predicted, ...days.map((d) => d.eur), 1);
  const H = 86;

  return (
    <Step n={1} icon={CalendarDays} title={`Ce qu'il commande d'habitude le ${weekday}`} delay={0.05}>
      {isLoading ? (
        <div className="h-[110px] animate-pulse rounded bg-muted" />
      ) : (
        <>
          <div className="relative" style={{ height: H + 22 }}>
            {avg > 0 ? (
              <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.9 }}
                className="pointer-events-none absolute inset-x-0 border-t border-dashed border-foreground/40" style={{ top: H - (avg / max) * H }}>
                <span className="num absolute -top-[15px] left-0 rounded bg-card px-1 text-[10px] text-muted-foreground">moyenne {fmtMoney(avg, { compact: true })}</span>
              </motion.div>
            ) : null}
            <div className="flex h-full items-end gap-[3px]">
              {days.map((d, i) => (
                <div key={d.iso} className="flex flex-1 flex-col items-center gap-1" title={`${dayLabel(d.iso, true)} : ${d.eur ? `${fmtMoney(d.eur)} · ${d.numbers.join(", ")}` : "pas de commande"}`}>
                  <motion.div initial={{ scaleY: 0 }} animate={{ scaleY: 1 }} transition={{ duration: 0.5, ease, delay: 0.15 + i * 0.04 }}
                    className={cn("w-full origin-bottom rounded-t-[3px]", d.eur ? "bg-foreground/25" : "bg-muted")}
                    style={{ height: d.eur ? Math.max(3, (d.eur / max) * H) : 3 }} />
                  <span className="num text-[9.5px] leading-3 text-muted-foreground">{new Date(`${d.iso}T12:00:00`).getDate()}</span>
                </div>
              ))}
              <div className="flex flex-[1.4] flex-col items-center gap-1">
                <motion.div initial={{ scaleY: 0 }} animate={{ scaleY: 1 }} transition={{ duration: 0.6, ease, delay: 0.75 }}
                  className="w-full origin-bottom rounded-t-[3px] bg-primary shadow-[0_0_12px_hsl(var(--primary)/.45)]"
                  style={{ height: Math.max(3, (predicted / max) * H) }} />
                <span className="text-[9.5px] font-medium leading-3 text-primary">prévu</span>
              </div>
            </div>
          </div>
          <p className="mt-1.5 text-[12px] leading-[17px] text-secondary-foreground">
            {hits.length
              ? <>Sur ses {WEEKS} derniers {weekday}s, il a commandé <b className="num text-foreground">{hits.length} fois</b>, pour <b className="num text-foreground">{fmtMoney(avg)}</b> en moyenne.</>
              : <>Pas de commande un {weekday} ces {WEEKS} dernières semaines : la prévision vient de ses autres jours.</>}
            {hits.length && predicted < avg * 0.85
              ? <> La prévision est plus basse : elle ne garde que les produits qu'il prend presque à chaque fois, les achats ponctuels en plus restent une bonne surprise.</>
              : null}
          </p>
          {hits.length ? (
            <p className="mt-1 text-[11px] leading-4 text-muted-foreground">
              Données : {hits.length} commandes livrées du {dayLabel(hits[0].iso)} au {dayLabel(hits[hits.length - 1].iso)},
              la dernière {hits[hits.length - 1].numbers[0]} ({fmtMoney(hits[hits.length - 1].eur)}).
            </p>
          ) : null}
        </>
      )}
    </Step>
  );
}

// ── 2. the same weeks in past years: the real orders around this date, 1 and 2 years ago ──

const AROUND = [-14, -7, 0, 7, 14];

function PastYears({ c, date, lines }: { c: ForecastClient; date: string; lines: ForecastLine[] }) {
  const recent = useWeekdayHistory(c.clientId, date);
  const years = [1, 2].map((k) => ({ k, center: shiftIso(date, -364 * k) }));
  const asks = years.flatMap((y) => AROUND.map((o) => ({ k: y.k, off: o, iso: shiftIso(y.center, o) })));
  const res = useQueries({
    queries: asks.map((a) => ({
      queryKey: ["orders", "list", { clientId: c.clientId, date: a.iso }],
      queryFn: () => api.orders.list({ clientId: c.clientId, date: a.iso }),
      staleTime: 5 * 60_000,
    })),
  });
  const loading = res.some((r) => r.isLoading);
  const rows = years.map((y) => {
    const weeks = asks.map((a, i) => ({ ...a, orders: (res[i].data ?? []).filter((o) => !CANCELLED.has(o.status)) }))
      .filter((a) => a.k === y.k)
      .map((a) => ({ iso: a.iso, off: a.off, eur: a.orders.reduce((s, o) => s + o.totalHt, 0), order: a.orders[0] }));
    const hit = weeks.filter((w) => w.eur > 0);
    return { ...y, weeks, avg: hit.length ? hit.reduce((s, w) => s + w.eur, 0) / hit.length : 0, same: weeks.find((w) => w.off === 0)! };
  });
  const lastYearOrder = rows[0].same.order ?? rows[0].weeks.find((w) => w.order)?.order;
  const detail = useQuery({ queryKey: ["orders", "detail", lastYearOrder?.id], queryFn: () => api.orders.get(lastYearOrder!.id), enabled: !!lastYearOrder });

  const withData = rows.filter((r) => r.avg > 0);
  const season = withData.length ? withData.reduce((s, r) => s + r.avg, 0) / withData.length : 0;
  const ratio = recent.avg > 0 && season > 0 ? season / recent.avg - 1 : null;
  const max = Math.max(...rows.flatMap((r) => r.weeks.map((w) => w.eur)), 1);
  const mine = new Map(lines.map((l) => [l.productId, l]));
  const topPast = [...(detail.data?.lines ?? [])].sort((a, b) => b.qty * b.unitPrice - a.qty * a.unitPrice).slice(0, 3);

  return (
    <Step n={2} icon={HistoryIcon} title="Les mêmes semaines, les années passées" delay={0.1}>
      {loading ? <div className="h-[120px] animate-pulse rounded bg-muted" /> : (
        <div className="flex flex-col gap-2.5">
          {rows.map((r, ri) => (
            <div key={r.k}>
              <div className="mb-1 flex items-baseline gap-2 text-[11.5px]">
                <span className="font-medium">{new Date(`${r.center}T12:00:00`).getFullYear()}</span>
                <span className="truncate text-muted-foreground">autour du {new Date(`${r.center}T12:00:00`).toLocaleDateString("fr-FR", { day: "numeric", month: "long" })}</span>
                <span className="num ml-auto whitespace-nowrap">{r.avg ? `moy. ${fmtMoney(r.avg, { compact: true })}` : "aucune commande"}</span>
              </div>
              <div className="flex h-[38px] items-end gap-1">
                {r.weeks.map((w, i) => (
                  <div key={w.iso} className="flex flex-1 flex-col items-center" title={`${dayLabel(w.iso, true)} : ${w.eur ? `${fmtMoney(w.eur)} · ${w.order?.number}` : "pas de commande"}`}>
                    <motion.div initial={{ scaleY: 0 }} animate={{ scaleY: 1 }} transition={{ duration: 0.45, ease, delay: 0.2 + ri * 0.15 + i * 0.04 }}
                      className={cn("w-full origin-bottom rounded-t-[3px]", w.off === 0 ? "bg-chart-4" : w.eur ? "bg-chart-4/35" : "bg-muted")}
                      style={{ height: w.eur ? Math.max(3, (w.eur / max) * 38) : 3 }} />
                  </div>
                ))}
              </div>
              <p className="mt-1 text-[11.5px] leading-4 text-secondary-foreground">
                {r.same.order
                  ? <>Le <b className="text-foreground">{dayLabel(r.same.iso, true)}</b>, il a commandé <b className="num text-foreground">{fmtMoney(r.same.eur)}</b> <span className="text-muted-foreground">({r.same.order.number})</span>.</>
                  : <>Pas de commande le {dayLabel(r.same.iso, true)} précisément.</>}
              </p>
            </div>
          ))}
          {topPast.length && lastYearOrder ? (
            <div className="rounded-md bg-muted/50 px-2 py-1.5 text-[11.5px] leading-4">
              <p className="mb-1 text-muted-foreground">Sa commande du {dayLabel(lastYearOrder.deliveryDate, true)} → ce qu'on prévoit :</p>
              {topPast.map((l) => {
                const now = mine.get(l.productId);
                return (
                  <p key={l.id} className="flex gap-2">
                    <span className="min-w-0 flex-1 truncate">{l.productName}</span>
                    <span className="num">{fmtQty(l.qty, l.unit)}</span>
                    <span className="num w-[72px] whitespace-nowrap text-right text-primary">{now ? `→ ${fmtQty(now.qty, now.unit)}` : "pas prévu"}</span>
                  </p>
                );
              })}
            </div>
          ) : null}
          {ratio !== null ? (
            <p className="text-[12px] leading-[17px] text-secondary-foreground">
              À cette période, il commande en moyenne <b className="num text-foreground">{fmtMoney(season)}</b> contre <b className="num text-foreground">{fmtMoney(recent.avg)}</b> ces dernières semaines :
              {" "}<b className={cn("num", ratio > 0.03 ? "text-primary" : "text-foreground")}>{Math.abs(ratio) < 0.03 ? "la saison ne change presque rien" : `${ratio > 0 ? "+" : "−"}${Math.round(Math.abs(ratio) * 100)} % en cette saison`}</b>.
            </p>
          ) : null}
        </div>
      )}
    </Step>
  );
}

// ── 3. the recipe: usual basket, events, recent trend ──

function Recipe({ usual, events, trend, predicted, upsell, notes }: { usual: number; events: number; trend: number; predicted: number; upsell: boolean; notes: string[] }) {
  const rows = [
    { label: upsell ? "Ce qu'il prend déjà" : "Son panier habituel", eur: usual, tone: "bg-foreground/25", sign: false },
    { label: "Effet des événements", eur: events, tone: "bg-chart-3", sign: true },
    { label: "Tendance récente", eur: trend, tone: "bg-chart-4", sign: true },
  ].filter((r) => !r.sign || Math.abs(r.eur) >= 1);
  const max = Math.max(usual, predicted, ...rows.map((r) => Math.abs(r.eur)), 1);
  return (
    <Step n={3} icon={TrendingUp} title="Ce qui change cette semaine" delay={0.15}>
      <div className="flex flex-col gap-1.5">
        {rows.map((r, i) => (
          <div key={r.label} className="flex items-center gap-2 text-[12px]">
            <span className="w-[150px] shrink-0 truncate text-secondary-foreground">{r.label}</span>
            <div className="h-3 flex-1 overflow-hidden rounded-full bg-muted/60">
              <motion.div initial={{ scaleX: 0 }} animate={{ scaleX: 1 }} transition={{ duration: 0.55, ease, delay: 0.3 + i * 0.12 }}
                className={cn("h-full origin-left rounded-full", r.tone, r.eur < 0 && "opacity-50")} style={{ width: `${(Math.abs(r.eur) / max) * 100}%` }} />
            </div>
            <span className={cn("num w-[72px] shrink-0 text-right", r.sign && r.eur > 0 && "text-primary", r.sign && r.eur < 0 && "text-muted-foreground")}>
              {r.sign ? (r.eur > 0 ? "+" : "−") : ""}{fmtMoney(Math.abs(r.eur), { compact: true })}
            </span>
          </div>
        ))}
        <div className="mt-1 flex items-center gap-2 border-t border-border pt-1.5 text-[12px]">
          <span className="w-[150px] shrink-0 font-medium">= {upsell ? "Ajout proposé" : "Panier prévu"}</span>
          <div className="h-3 flex-1 overflow-hidden rounded-full bg-muted/60">
            <motion.div initial={{ scaleX: 0 }} animate={{ scaleX: 1 }} transition={{ duration: 0.6, ease, delay: 0.7 }}
              className="h-full origin-left rounded-full bg-primary" style={{ width: `${(predicted / max) * 100}%` }} />
          </div>
          <span className="num w-[72px] shrink-0 text-right font-semibold">{fmtMoney(predicted, { compact: true })}</span>
        </div>
        {notes.map((n) => <p key={n} className="text-[11.5px] leading-4 text-muted-foreground">{n}</p>)}
      </div>
    </Step>
  );
}

// ── 4. product by product: usual quantity vs predicted ──

function Products({ lines }: { lines: ForecastLine[] }) {
  const top = [...lines].sort((a, b) => lineEur(b, b.qty) - lineEur(a, a.qty)).slice(0, 5);
  const max = Math.max(...top.map((l) => Math.max(l.qty, l.baseQty)), 1);
  return (
    <Step n={4} icon={ListChecks} title="Produit par produit" delay={0.25}>
      <div className="mb-1.5 flex items-center gap-3 text-[10.5px] text-muted-foreground">
        <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-sm bg-foreground/25" />d'habitude</span>
        <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-sm bg-primary" />prévu</span>
      </div>
      <ul className="flex flex-col gap-2">
        {top.map((l, i) => {
          const diff = l.baseQty > 0 ? (l.qty - l.baseQty) / l.baseQty : null;
          return (
            <li key={l.productId + i} className="text-[12px]">
              <div className="flex items-center gap-2">
                <span className="min-w-0 flex-1 truncate">{l.productName}</span>
                {diff !== null && Math.abs(diff) >= 0.03 ? (
                  <span className={cn("num text-[11px]", diff > 0 ? "text-primary" : "text-muted-foreground")}>{diff > 0 ? "+" : "−"}{Math.round(Math.abs(diff) * 100)} %</span>
                ) : <span className="text-[11px] text-muted-foreground">{l.baseQty ? "stable" : "nouveau"}</span>}
                <span className="num w-[64px] text-right">{fmtQty(l.qty, l.unit)}</span>
              </div>
              <div className="mt-1 flex flex-col gap-[3px]">
                <motion.div initial={{ scaleX: 0 }} animate={{ scaleX: 1 }} transition={{ duration: 0.5, ease, delay: 0.4 + i * 0.07 }}
                  className="h-1.5 origin-left rounded-full bg-foreground/25" style={{ width: `${(l.baseQty / max) * 100}%` }} />
                <motion.div initial={{ scaleX: 0 }} animate={{ scaleX: 1 }} transition={{ duration: 0.5, ease, delay: 0.45 + i * 0.07 }}
                  className="h-1.5 origin-left rounded-full bg-primary" style={{ width: `${(l.qty / max) * 100}%` }} />
              </div>
            </li>
          );
        })}
      </ul>
      {lines.length > top.length ? <p className="mt-1.5 text-[11px] text-muted-foreground">et {lines.length - top.length} autres produits.</p> : null}
    </Step>
  );
}

// ── 5. how sure we are ──

function Confidence({ c, weekday }: { c: ForecastClient; weekday: string }) {
  const p = Math.round(c.probability * 100);
  const word = p >= 90 ? "Très sûr" : p >= 60 ? "Plutôt sûr" : "Incertain";
  const tone = p >= 90 ? "bg-primary" : p >= 60 ? "bg-chart-3" : "bg-muted-foreground";
  const why = c.loyalty
    ? `il a été livré ${c.loyalty.weekdayHits} ${weekday}s sur les 12 derniers`
    : c.reason.split(" ; ")[0].toLowerCase();
  return (
    <Step n={5} icon={Gauge} title="À quel point on est sûr" delay={0.35}>
      <div className="flex items-center gap-3">
        <div className="relative h-3 flex-1 overflow-hidden rounded-full bg-muted/60">
          <motion.div initial={{ scaleX: 0 }} animate={{ scaleX: 1 }} transition={{ duration: 0.7, ease, delay: 0.5 }}
            className={cn("h-full origin-left rounded-full", tone)} style={{ width: `${p}%` }} />
        </div>
        <span className="num text-[14px] font-semibold">{p} %</span>
      </div>
      <p className="mt-1.5 text-[12px] leading-[17px] text-secondary-foreground">
        <b className="text-foreground">{word}</b> : {why}.
        {c.status === "upsell" ? " Il a déjà commandé : on lui propose seulement d'ajouter." : ""}
      </p>
    </Step>
  );
}

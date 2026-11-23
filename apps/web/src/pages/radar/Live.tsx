/** Radar, once the calls start: the optimized deliveries (one per truck) and the live activity timeline. */
import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Check, Clock, PackageCheck, Phone, PhoneOff, Truck as TruckIcon } from "lucide-react";
import { Spinner } from "@/ui";
import { cn } from "@/lib/cn";
import { fmtMoney } from "@/lib/format";
import type { RoutePlan } from "@/features/radar/types";
import type { FeedItem } from "@/features/radar/useRadar";
import { TRUCK_COLORS } from "./look";

const spring = { type: "spring", stiffness: 420, damping: 34 } as const;

// ── deliveries ──

export function DeliveryCards({ route, names, delivered, focus, onFocus }: {
  route: RoutePlan; names: Record<string, string>; delivered: Record<string, string>; focus?: string; onFocus: (runId?: string) => void;
}) {
  const saved = route.totals;
  return (
    <div className="flex flex-col gap-1.5">
      {route.optimized.map((t, i) => {
        const color = TRUCK_COLORS[i % TRUCK_COLORS.length];
        const on = focus === t.runId;
        const done = t.stops.filter((s) => delivered[s.clientId]).length;
        const cost = (t as { costEur?: number }).costEur;
        return (
          <motion.div key={t.runId} layout="position" initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ ...spring, delay: i * 0.06 }}
            className={cn("overflow-hidden rounded-xl border bg-card transition-colors", on ? "border-foreground/25 shadow-sm" : "border-border hover:border-border-hover")}>
            <button type="button" onClick={() => onFocus(on ? undefined : t.runId)} className="flex w-full items-center gap-3 px-3 pb-2 pt-2.5 text-left">
              <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg text-white" style={{ background: color }}>
                <TruckIcon size={16} strokeWidth={2.2} />
              </span>
              <span className="min-w-0 flex-1">
                <span className="flex min-w-0 items-center gap-1.5 whitespace-nowrap text-[13px] font-semibold leading-5">
                  Livraison {i + 1}<span className="min-w-0 truncate font-normal text-muted-foreground">· {t.route}</span>
                </span>
                <span className="num block truncate text-[11.5px] leading-4 text-muted-foreground">
                  {t.driver} · {t.stops.length} arrêts · {Math.round(t.km)} km · {t.departure} → {t.lastEta}
                </span>
              </span>
              <span className="shrink-0 text-right">
                {cost ? <span className="num block text-[12.5px] font-medium">{fmtMoney(cost, { compact: true })}</span> : null}
                {done ? <span className="num block text-[11px] text-success">{done}/{t.stops.length} livrés</span> : null}
              </span>
            </button>
            <div className="mx-3 mb-2.5 h-1 overflow-hidden rounded-full bg-muted">
              <motion.div className="h-full rounded-full" style={{ background: color }} initial={{ width: 0 }}
                animate={{ width: `${t.stops.length ? (done / t.stops.length) * 100 : 0}%` }} transition={{ duration: 0.6, ease: "easeOut" }} />
            </div>
            <AnimatePresence initial={false}>
              {on ? (
                <motion.ol key="stops" initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }} transition={{ duration: 0.22 }}
                  className="overflow-hidden border-t border-border py-1">
                  {t.stops.map((s, j) => (
                    <li key={s.orderId} className="flex items-center gap-2.5 px-3 py-1 text-[12px]">
                      <span className="num grid h-5 w-5 shrink-0 place-items-center rounded-full text-[10px] font-bold text-white" style={{ background: color }}>{j + 1}</span>
                      <span className="min-w-0 flex-1 truncate">{names[s.clientId] ?? s.clientId}</span>
                      {delivered[s.clientId] ? <Check size={13} className="text-success" strokeWidth={2.6} /> : null}
                      <span className="num text-muted-foreground">{s.eta}</span>
                    </li>
                  ))}
                </motion.ol>
              ) : null}
            </AnimatePresence>
          </motion.div>
        );
      })}
      {saved && saved.savedKm > 0 ? (
        <p className="num px-1 text-[11.5px] text-muted-foreground">
          Optimisé : <span className="font-medium text-primary">−{Math.round(saved.savedKm)} km</span>
          {saved.savedEur ? <> et <span className="font-medium text-primary">−{fmtMoney(saved.savedEur, { compact: true })}</span></> : null} par rapport aux tournées habituelles.
        </p>
      ) : null}
    </div>
  );
}

// ── live activity ──

const FEED_LOOK: Record<FeedItem["kind"], { icon: typeof Check; dot: string }> = {
  calling: { icon: Phone, dot: "bg-muted text-muted-foreground" },
  yes: { icon: Phone, dot: "bg-primary text-primary-foreground" },
  no: { icon: PhoneOff, dot: "bg-muted text-muted-foreground" },
  later: { icon: Clock, dot: "bg-warning/15 text-warning" },
  failed: { icon: PhoneOff, dot: "bg-destructive/10 text-destructive" },
  route: { icon: TruckIcon, dot: "bg-primary text-primary-foreground" },
  delivered: { icon: PackageCheck, dot: "bg-success text-white" },
};

const hhmm = (t: number) => new Date(t).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit", second: "2-digit" });

export function ActivityFeed({ feed, live, onSelect }: { feed: FeedItem[]; live: boolean; onSelect: (clientId: string) => void }) {
  return (
    <div>
      <div className="mb-3 flex items-center gap-2">
        <span className="relative flex h-2.5 w-2.5">
          {live ? <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-primary opacity-60" /> : null}
          <span className={cn("relative inline-flex h-2.5 w-2.5 rounded-full", live ? "bg-primary" : "bg-muted-foreground/50")} />
        </span>
        <h2 className="flex-1 text-[15px] font-semibold tracking-[-0.01em]">Activité en direct</h2>
        {live ? <span className="flex items-center gap-1.5 rounded-full bg-primary/10 px-2.5 py-0.5 text-[11.5px] font-medium text-primary"><span className="h-1.5 w-1.5 rounded-full bg-primary" />Temps réel</span> : null}
      </div>
      {feed.length === 0 ? <p className="py-6 text-center text-[12px] text-muted-foreground">Les appels démarrent…</p> : null}
      <ol className="relative">
        <AnimatePresence initial={false}>
          {feed.map((f, i) => (
            <motion.li key={f.key} layout="position"
              initial={{ opacity: 0, y: -12, scale: 0.98 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0 }}
              transition={spring} className="relative flex gap-3 pb-3.5">
              {i < feed.length - 1 ? <span className="absolute bottom-0 left-[17.5px] top-10 w-px bg-border" /> : null}
              <FeedDot f={f} />
              <button type="button" disabled={!f.clientId} onClick={() => f.clientId && onSelect(f.clientId)}
                className="min-w-0 flex-1 rounded-md text-left">
                <div className="num text-[11px] leading-4 text-muted-foreground">{hhmm(f.at)}</div>
                <div className="flex items-center gap-2">
                  <span className="min-w-0 flex-1 truncate text-[13px] font-semibold leading-5">{f.title}</span>
                  {f.amount ? <span className="num text-[12px] font-medium text-primary">+{fmtMoney(f.amount, { compact: true })}</span> : null}
                  <FeedState f={f} />
                </div>
                {f.sub ? (
                  <motion.p key={f.sub} initial={{ opacity: 0 }} animate={{ opacity: 1 }}
                    className={cn("line-clamp-2 text-[12px] leading-[17px] text-muted-foreground", f.sub.includes("«") && "italic")}>
                    {f.sub}
                  </motion.p>
                ) : null}
              </button>
            </motion.li>
          ))}
        </AnimatePresence>
      </ol>
    </div>
  );
}

function FeedDot({ f }: { f: FeedItem }) {
  const L = FEED_LOOK[f.kind];
  const Icon = L.icon;
  return (
    <span className="relative z-[1] shrink-0">
      {f.kind === "calling" ? (
        <motion.span className="absolute inset-0 rounded-full border-2 border-agent-voice/60"
          initial={{ scale: 1, opacity: 0.8 }} animate={{ scale: 1.45, opacity: 0 }} transition={{ duration: 1.3, repeat: Infinity, ease: "easeOut" }} />
      ) : null}
      <motion.span key={f.kind} initial={{ scale: 0.6 }} animate={{ scale: 1 }} transition={{ type: "spring", stiffness: 500, damping: 18 }}
        className={cn("relative grid h-9 w-9 place-items-center rounded-full ring-4 ring-background", L.dot)}>
        <Icon size={15} strokeWidth={2.2} />
      </motion.span>
    </span>
  );
}

function FeedState({ f }: { f: FeedItem }) {
  const [busy, setBusy] = useState(f.kind === "route");
  useEffect(() => {
    if (f.kind !== "route") return;
    const t = setTimeout(() => setBusy(false), 1400);
    return () => clearTimeout(t);
  }, [f.kind]);
  if (f.kind === "calling") return <Spinner size={13} className="text-muted-foreground" />;
  if (f.kind === "route" && busy) return <Spinner size={13} className="text-primary" />;
  if (f.kind === "yes" || f.kind === "route" || f.kind === "delivered")
    return (
      <motion.span initial={{ scale: 0 }} animate={{ scale: 1 }} transition={{ type: "spring", stiffness: 600, damping: 16 }}
        className="grid h-5 w-5 shrink-0 place-items-center rounded-full bg-primary text-primary-foreground"><Check size={12} strokeWidth={3} /></motion.span>
    );
  return null;
}

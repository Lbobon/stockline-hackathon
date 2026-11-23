/** Right panel of the radar: the week's events, then the calls (live) or the clients to call, or one client's basket. */
import { useEffect, useRef } from "react";
import { AnimatePresence, LayoutGroup, animate, motion } from "framer-motion";
import { ArrowLeft, Check, Clock, PhoneCall, PhoneOff, Sparkles, X } from "lucide-react";
import { cn } from "@/lib/cn";
import { fmtMoney, fmtQty } from "@/lib/format";
import type { CampaignCall, Forecast, ForecastClient, ForecastLine, RadarEvent } from "@/features/radar/types";
import { EVENT_STYLE } from "./look";
import { pinLabel } from "./RadarMap";

const spring = { type: "spring", stiffness: 420, damping: 34 } as const;

/** A number that counts to its new value. */
export function Count({ value, format = (v: number) => Math.round(v).toLocaleString("fr-FR"), className }: {
  value: number; format?: (v: number) => string; className?: string;
}) {
  const ref = useRef<HTMLSpanElement>(null);
  const prev = useRef(value);
  useEffect(() => {
    const from = prev.current;
    prev.current = value;
    const ctl = animate(from, value, { duration: 0.9, ease: [0.22, 1, 0.36, 1], onUpdate: (v) => { if (ref.current) ref.current.textContent = format(v); } });
    return () => ctl.stop();
  }, [value, format]);
  return <span ref={ref} className={cn("num", className)}>{format(value)}</span>;
}

const shortDate = (iso: string) =>
  new Date(`${iso}T12:00:00`).toLocaleDateString("fr-FR", { weekday: "short", day: "numeric" }).replace(".", "");

// ── events ──

export function EventStrip({ forecast, focus, onFocus }: { forecast: Forecast; focus?: string; onFocus: (id?: string) => void }) {
  const impact = (e: RadarEvent) => {
    const touches = (c: ForecastClient) => c.eventIds?.includes(e.id) || [...c.lines, ...(c.addLines ?? [])].some((l) => l.eventId === e.id);
    const hits = forecast.clients.filter(touches);
    const eur = hits.reduce((s, c) => s + [...c.lines, ...(c.addLines ?? [])].filter((l) => l.eventId === e.id).reduce((a, l) => a + (l.qty - l.baseQty) * (l.unitPrice ?? 0), 0), 0);
    return { n: hits.length, eur };
  };
  return (
    <div className="grid grid-cols-2 gap-2">
      {forecast.events.map((e, i) => {
        const st = EVENT_STYLE[e.kind];
        const Icon = st.icon;
        const { n, eur } = impact(e);
        const on = focus === e.id;
        return (
          <motion.button
            key={e.id} type="button" onClick={() => onFocus(on ? undefined : e.id)}
            initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ ...spring, delay: 0.25 + i * 0.06 }}
            whileHover={{ y: -1 }} whileTap={{ scale: 0.98 }}
            className={cn(
              "group relative flex min-w-0 items-start gap-2.5 rounded-lg border bg-card p-2.5 text-left transition-colors",
              on ? "border-transparent ring-2" : "border-border hover:border-border-hover",
            )}
            style={on ? ({ "--tw-ring-color": st.hsl } as React.CSSProperties) : undefined}
          >
            <span className={cn("grid h-8 w-8 shrink-0 place-items-center rounded-md", st.soft, st.text)}><Icon size={16} strokeWidth={1.75} /></span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-[12.5px] font-medium leading-4">{e.title}</span>
              <span className="mt-0.5 block text-[11px] leading-4 text-muted-foreground">
                {shortDate(e.date)}{e.time ? ` · ${e.time}` : ""} · {n ? `${n} client${n > 1 ? "s" : ""}` : e.boosts[0]?.productName.split(/[(,]/)[0].trim().toLowerCase()}
              </span>
              {eur > 0 ? <span className="num mt-1 block text-[11.5px] font-medium leading-4 text-foreground">+{fmtMoney(eur, { compact: true })}</span> : null}
            </span>
          </motion.button>
        );
      })}
    </div>
  );
}

// ── call / client cards ──

const STATUS_META: Record<string, { label: string; icon: typeof Check; cls: string }> = {
  queued: { label: "En attente", icon: Clock, cls: "text-muted-foreground" },
  ringing: { label: "Ça sonne", icon: PhoneCall, cls: "text-agent-voice" },
  talking: { label: "En ligne", icon: PhoneCall, cls: "text-agent-voice" },
  confirmed: { label: "Confirmée", icon: Check, cls: "text-primary" },
  modified: { label: "Ajustée", icon: Check, cls: "text-primary" },
  declined: { label: "Pas cette fois", icon: X, cls: "text-muted-foreground" },
  callback: { label: "À rappeler", icon: Clock, cls: "text-warning" },
  failed: { label: "Échec", icon: PhoneOff, cls: "text-destructive" },
};

const RANK: Record<string, number> = { talking: 0, ringing: 1, confirmed: 2, modified: 2, callback: 3, declined: 4, failed: 4, queued: 5 };

export function CallList({ forecast, calls, order, selected, onSelect }: {
  forecast: Forecast; calls: Record<string, CampaignCall>; order: string[]; selected?: string; onSelect: (id: string) => void;
}) {
  const byId = Object.fromEntries(forecast.clients.map((c) => [c.clientId, c]));
  const started = (id: string) => order.indexOf(id);
  const list = Object.values(calls).sort((a, b) => RANK[a.status] - RANK[b.status] || started(b.clientId) - started(a.clientId) || (byId[b.clientId]?.upliftHt ?? 0) - (byId[a.clientId]?.upliftHt ?? 0));
  return (
    <LayoutGroup>
      <ul className="flex flex-col gap-1.5">
        <AnimatePresence initial={false}>
          {list.map((call) => (
            <motion.li key={call.clientId} layout="position" transition={spring}
              initial={{ opacity: 0, scale: 0.98 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0 }}>
              <CallCard call={call} client={byId[call.clientId]} selected={selected === call.clientId} onClick={() => onSelect(call.clientId)} />
            </motion.li>
          ))}
        </AnimatePresence>
      </ul>
    </LayoutGroup>
  );
}

function CallCard({ call, client, selected, onClick }: { call: CampaignCall; client?: ForecastClient; selected: boolean; onClick: () => void }) {
  const m = STATUS_META[call.status];
  const live = call.status === "ringing" || call.status === "talking";
  const done = !live && call.status !== "queued";
  const last = call.transcript.slice(-2);
  const Icon = m.icon;
  return (
    <button type="button" onClick={onClick}
      className={cn(
        "relative w-full overflow-hidden rounded-lg border bg-card px-3 py-2.5 text-left transition-colors",
        selected ? "border-foreground/30" : "border-border hover:border-border-hover",
        call.status === "queued" && "opacity-60",
      )}>
      {live ? <motion.span layoutId={undefined} className="absolute inset-y-0 left-0 w-[3px] bg-agent-voice" initial={{ scaleY: 0 }} animate={{ scaleY: 1 }} /> : null}
      <div className="flex items-center gap-2">
        <span className={cn("flex items-center gap-1 text-[11px] font-medium", m.cls)}>
          {live ? <Eq /> : <Icon size={12} strokeWidth={2} />}
          {m.label}
        </span>
        <span className="min-w-0 flex-1 truncate text-[12.5px] font-medium">{call.clientName}</span>
        {call.decision && done ? <Jev d={call.decision} /> : null}
        {call.totalHt > 0 ? (
          <motion.span initial={{ opacity: 0, x: 6 }} animate={{ opacity: 1, x: 0 }} className="num text-[12px] font-medium">
            {client?.status === "upsell" ? "+" : ""}{fmtMoney(call.totalHt, { compact: true })}
          </motion.span>
        ) : null}
      </div>
      <AnimatePresence initial={false}>
        {last.length && (live || selected) ? (
          <motion.div key="t" initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }} transition={{ duration: 0.22 }}
            className="overflow-hidden">
            <div className="mt-1.5 flex flex-col gap-1">
              {last.map((l, i) => (
                <motion.p key={`${call.transcript.length}-${i}`} initial={{ opacity: 0, y: 3 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.25, delay: i * 0.05 }}
                  className="line-clamp-2 text-[11.5px] leading-[16px] text-muted-foreground">
                  <span className={cn("mr-1 font-medium", l.who === "agent" ? "text-agent-voice" : "text-foreground")}>{l.who === "agent" ? "Agent" : "Client"}</span>
                  {l.text}
                </motion.p>
              ))}
            </div>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </button>
  );
}

function Jev({ d }: { d: NonNullable<CampaignCall["decision"]> }) {
  const label = { oui: "oui", modifie: "ajuste", non: "non", rappeler: "rappel" }[d.label] ?? d.label;
  return (
    <motion.span initial={{ scale: 0.6, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} transition={{ type: "spring", stiffness: 600, damping: 22 }}
      title={`Décision lue par ${d.source === "jev" ? "Jev" : d.source}`}
      className="num inline-flex items-center gap-1 rounded-full border border-border bg-muted px-1.5 py-px text-[10.5px] text-muted-foreground">
      <Sparkles size={9} className="text-agent-fast" />{label} {d.confidence.toFixed(2)}
    </motion.span>
  );
}

function Eq() {
  return (
    <span className="flex h-3 items-end gap-[2px]">
      {[0, 0.2, 0.4].map((d) => <span key={d} className="w-[2px] origin-bottom animate-eq-bar rounded-full bg-current" style={{ height: 10, animationDelay: `${d}s` }} />)}
    </span>
  );
}

export function ClientList({ forecast, selected, onSelect }: { forecast: Forecast; selected?: string; onSelect: (id: string) => void }) {
  const rows = forecast.clients.filter((c) => c.status !== "ordered").sort((a, b) => b.upliftHt - a.upliftHt || b.totalHt - a.totalHt);
  return (
    <ul className="flex flex-col gap-1.5">
      {rows.map((c, i) => (
        <motion.li key={c.clientId} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ ...spring, delay: 0.35 + Math.min(i, 12) * 0.03 }}>
          <button type="button" onClick={() => onSelect(c.clientId)}
            className={cn("w-full rounded-lg border bg-card px-3 py-2.5 text-left transition-colors", selected === c.clientId ? "border-foreground/30" : "border-border hover:border-border-hover")}>
            <div className="flex items-center gap-2">
              <KindTag c={c} />
              <span className="min-w-0 flex-1 truncate text-[12.5px] font-medium">{c.name}</span>
              <span className="num text-[12px] text-muted-foreground">{c.status === "upsell" ? `+${fmtMoney(c.upliftHt, { compact: true })}` : fmtMoney(c.totalHt, { compact: true })}</span>
            </div>
            <p className="mt-1 truncate text-[11.5px] text-muted-foreground">{c.city} · {firstReason(c.reason)}</p>
          </button>
        </motion.li>
      ))}
    </ul>
  );
}

function KindTag({ c }: { c: ForecastClient }) {
  return c.status === "upsell" ? (
    <span className="rounded-[3px] bg-violet/10 px-1 py-px text-[10.5px] font-medium text-violet">Ajout</span>
  ) : c.status === "predicted" ? (
    <span className="rounded-[3px] border border-dashed border-foreground/40 px-1 text-[10.5px] font-medium text-muted-foreground">Prévu</span>
  ) : (
    <span className="rounded-[3px] bg-primary/10 px-1 py-px text-[10.5px] font-medium text-primary">Commandé</span>
  );
}

const firstReason = (r: string) => r.split(" ; ")[0];

// ── one client ──

export function ClientDetail({ c, forecast, call, delivered, onBack }: {
  c: ForecastClient; forecast: Forecast; call?: CampaignCall; delivered?: string; onBack: () => void;
}) {
  const events = forecast.events.filter((e) => c.eventIds?.includes(e.id));
  const lines = c.status === "upsell" ? c.addLines ?? [] : c.lines;
  const reasons = c.reason.split(" ; ").map((r) => r.replace(/^[^:]+ : /, (m) => m));
  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-start gap-2">
        <button type="button" onClick={onBack} aria-label="Retour" className="-ml-1 mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"><ArrowLeft size={14} /></button>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2"><h2 className="truncate text-[15px] font-semibold tracking-[-0.01em]">{c.name}</h2><KindTag c={c} /></div>
          <p className="text-[12px] text-muted-foreground">{c.city}{c.contactName ? ` · ${c.contactName}` : ""} · {pinLabel(c, call, delivered)}</p>
        </div>
      </div>

      <div className="grid grid-cols-3 gap-2">
        <Stat label={c.status === "upsell" ? "Ajout" : "Panier prévu"} value={fmtMoney(c.status === "upsell" ? c.upliftHt : c.totalHt, { compact: true })} />
        <Stat label="Dont événements" value={`+${fmtMoney(c.upliftHt, { compact: true })}`} />
        <Stat label="Probabilité" value={`${Math.round(c.probability * 100)} %`} />
      </div>

      {events.length ? (
        <div className="flex flex-wrap gap-1.5">
          {events.map((e) => {
            const st = EVENT_STYLE[e.kind];
            const Icon = st.icon;
            return <span key={e.id} className={cn("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium", st.soft, st.text)}><Icon size={11} />{e.title}</span>;
          })}
        </div>
      ) : null}

      <div>
        <h3 className="mb-1.5 text-[11px] font-medium uppercase tracking-[0.04em] text-muted-foreground">{c.status === "upsell" ? "Ce qu'on propose d'ajouter" : "Panier prévu"}</h3>
        <ul className="divide-y divide-border rounded-lg border border-border bg-card">
          {lines.map((l, i) => <LineRow key={l.productId + i} l={l} delay={i * 0.03} />)}
        </ul>
      </div>

      {c.specialRequirements?.length ? (
        <div className="rounded-lg border border-chart-5/30 bg-chart-5/[.06] p-2.5 text-[12px]">
          {c.specialRequirements.map((s, i) => <p key={i}>{s.note}</p>)}
        </div>
      ) : null}

      <div>
        <h3 className="mb-1.5 text-[11px] font-medium uppercase tracking-[0.04em] text-muted-foreground">Pourquoi</h3>
        <ul className="flex flex-col gap-1 text-[12px] leading-[17px] text-secondary-foreground">
          {reasons.map((r, i) => <li key={i} className="flex gap-2"><span className="mt-[7px] h-1 w-1 shrink-0 rounded-full bg-muted-foreground/60" />{r}</li>)}
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

function LineRow({ l, delay }: { l: ForecastLine; delay: number }) {
  const boosted = l.eventId && l.qty > l.baseQty;
  return (
    <motion.li initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay }} className="flex items-center gap-2 px-2.5 py-1.5 text-[12px]">
      <span className="min-w-0 flex-1 truncate">{l.productName}</span>
      {boosted && l.baseQty > 0 ? <span className="num text-[11px] text-muted-foreground line-through">{fmtQty(l.baseQty, l.unit)}</span> : null}
      <span className={cn("num whitespace-nowrap", boosted && "font-medium text-primary")}>{fmtQty(l.qty, l.unit)}</span>
    </motion.li>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-border bg-card px-2.5 py-2">
      <div className="text-[10.5px] text-muted-foreground">{label}</div>
      <div className="num text-[14px] font-semibold tracking-[-0.01em]">{value}</div>
    </div>
  );
}

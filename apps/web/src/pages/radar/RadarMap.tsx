/**
 * The radar map: a MapLibre street basemap (CARTO Voyager / Dark Matter, follows the theme) under an SVG overlay drawn
 * in React, so every marker, route and truck animates with framer-motion.
 *   Store markers: white = to call · green + halo = has an order · violet ripple = in call · « + » = add-on possible
 *   faded = said no · amber ring = call back · check = delivered.
 *   One route per delivery (truck), following the roads (OSRM), drawn in on every new plan; during the delivery replay
 *   a truck drives it and the road behind it fills in.
 * On load a radar sweep rings out of the depot and each store lights up as the ring reaches it.
 */
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import maplibregl, { type Map as MlMap } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { AnimatePresence, animate, motion, useMotionValue, useMotionValueEvent } from "framer-motion";
import { Check, Locate, Minus, Plus, Store, Truck as TruckIcon, Warehouse } from "lucide-react";
import { useTheme } from "@/ui";
import type { CampaignCall, Forecast, ForecastClient, RoutePlan } from "@/features/radar/types";
import { mercator, pointAt, type Road } from "@/features/radar/roads";
import type { Phase, Toast } from "@/features/radar/useRadar";
import { EVENT_STYLE, TRUCK_COLORS } from "./look";

const STYLE = {
  light: "https://basemaps.cartocdn.com/gl/voyager-gl-style/style.json",
  dark: "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
};
const SWEEP_MS = 1700;
const PAD = { top: 64, bottom: 64, left: 64, right: 64 };

type Pt = { x: number; y: number };

export function RadarMap({
  forecast, calls, route, roads, delivered, phase, selected, onSelect, focusEvent, focusTruck, sweepKey, toast,
}: {
  forecast: Forecast;
  calls: Record<string, CampaignCall>;
  route?: RoutePlan;
  roads: Record<string, Road>;
  delivered: Record<string, string>;
  phase: Phase;
  selected?: string;
  onSelect: (clientId?: string) => void;
  focusEvent?: string;
  focusTruck?: string;
  sweepKey: number;
  toast?: Toast;
}) {
  const box = useRef<HTMLDivElement>(null);
  const map = useRef<MlMap | null>(null);
  const [, setFrame] = useState(0);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [ready, setReady] = useState(false);
  const [hover, setHover] = useState<string>();
  const { resolved } = useTheme();

  // ── map lifecycle ──
  useLayoutEffect(() => {
    if (!box.current) return;
    const m = new maplibregl.Map({
      container: box.current,
      style: STYLE[resolved],
      bounds: bounds(forecast),
      fitBoundsOptions: { padding: PAD },
      attributionControl: { compact: true },
      dragRotate: false,
      pitchWithRotate: false,
      touchPitch: false,
      fadeDuration: 0,
    });
    m.touchZoomRotate.disableRotation();
    let raf = 0;
    const redraw = () => { cancelAnimationFrame(raf); raf = requestAnimationFrame(() => setFrame((f) => f + 1)); };
    m.on("move", redraw);
    m.on("resize", () => { setSize({ w: m.getContainer().clientWidth, h: m.getContainer().clientHeight }); redraw(); });
    m.on("load", () => { tone(m); setReady(true); redraw(); });
    m.on("style.load", () => tone(m));
    setSize({ w: box.current.clientWidth, h: box.current.clientHeight });
    map.current = m;
    return () => { cancelAnimationFrame(raf); m.remove(); map.current = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const styleUrl = useRef(STYLE[resolved]);
  useEffect(() => {
    if (!map.current || styleUrl.current === STYLE[resolved]) return;
    styleUrl.current = STYLE[resolved];
    map.current.setStyle(STYLE[resolved]);
  }, [resolved]);

  // Fly to a selected client.
  useEffect(() => {
    const m = map.current;
    const c = forecast.clients.find((x) => x.clientId === selected);
    if (m && ready && c) m.easeTo({ center: [c.lng, c.lat], zoom: Math.max(m.getZoom(), 12.5), duration: 900 });
  }, [selected, ready, forecast]);

  // Frame a focused delivery.
  useEffect(() => {
    const m = map.current;
    const road = focusTruck ? roads[focusTruck] : undefined;
    if (!m || !ready || !road) return;
    const lngs = road.coords.map((c) => c[0]), lats = road.coords.map((c) => c[1]);
    m.fitBounds([[Math.min(...lngs), Math.min(...lats)], [Math.max(...lngs), Math.max(...lats)]], { padding: PAD, duration: 900, maxZoom: 13 });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusTruck, ready]);

  const p = (lng: number, lat: number): Pt => {
    const m = map.current;
    if (!m) return { x: -99, y: -99 };
    const q = m.project([lng, lat]);
    return { x: q.x, y: q.y };
  };
  // Roads have thousands of points: project them with one scale + offset (no rotation / pitch on this map).
  const ws = 512 * 2 ** (map.current?.getZoom() ?? 0);
  const [dmx, dmy] = mercator(forecast.depot.lng, forecast.depot.lat);
  const roadPts = (road: Road, upTo = road.coords.length): Pt[] => {
    const o = p(forecast.depot.lng, forecast.depot.lat);
    const out: Pt[] = [];
    for (let i = 0; i < upTo; i++) out.push({ x: o.x + (road.merc[2 * i] - dmx) * ws, y: o.y + (road.merc[2 * i + 1] - dmy) * ws });
    return out;
  };
  const zoomLevel = map.current?.getZoom() ?? 10;
  const scale = zoomLevel >= 12 ? 1 : zoomLevel >= 10.8 ? 0.78 : 0.58;

  const depot = p(forecast.depot.lng, forecast.depot.lat);
  const reach = Math.max(...forecast.clients.map((c) => dist(p(c.lng, c.lat), depot)), 1);

  const truckOf = useMemo(() => {
    const out: Record<string, { truck: number; seq: number }> = {};
    route?.optimized.forEach((t, i) => t.stops.forEach((s, j) => { out[s.clientId] = { truck: i, seq: j + 1 }; }));
    return out;
  }, [route]);

  const touched = (c: ForecastClient, id: string) => c.eventIds?.includes(id) || c.lines.some((l) => l.eventId === id) || !!c.addLines?.some((l) => l.eventId === id);
  const focusColor = focusEvent ? EVENT_STYLE[forecast.events.find((e) => e.id === focusEvent)?.kind ?? "local"].hsl : undefined;
  const focusRun = focusTruck ? route?.optimized.findIndex((t) => t.runId === focusTruck) : -1;
  const driving = phase === "delivering" || phase === "delivered";

  const zoom = (d: number) => map.current?.easeTo({ zoom: (map.current?.getZoom() ?? 10) + d, duration: 300 });
  const fit = () => { onSelect(undefined); map.current?.fitBounds(bounds(forecast), { padding: PAD, duration: 900 }); };

  return (
    <div className="relative h-full w-full overflow-hidden bg-muted">
      <div ref={box} className="!absolute inset-0 [&_.maplibregl-ctrl-attrib]:!bg-card/70 [&_.maplibregl-ctrl-attrib]:text-[10px]" />
      {ready && size.w > 0 ? (
        <svg className="pointer-events-none absolute inset-0" width={size.w} height={size.h}>
          <defs>
            <radialGradient id="sweep-fill">
              <stop offset="0%" stopColor="hsl(var(--primary))" stopOpacity="0" />
              <stop offset="80%" stopColor="hsl(var(--primary))" stopOpacity="0.05" />
              <stop offset="100%" stopColor="hsl(var(--primary))" stopOpacity="0.16" />
            </radialGradient>
          </defs>

          {/* load sweep */}
          <motion.circle
            key={`sweep-${sweepKey}`}
            cx={depot.x} cy={depot.y}
            fill="url(#sweep-fill)" stroke="hsl(var(--primary))" strokeWidth={1.5}
            initial={{ r: 0, opacity: 1 }}
            animate={{ r: reach * 1.08, opacity: [1, 1, 0] }}
            transition={{ duration: SWEEP_MS / 1000, ease: [0.22, 0.61, 0.36, 1], opacity: { times: [0, 0.75, 1], duration: SWEEP_MS / 1000 } }}
          />

          {/* routes: white casing + colored line, drawn in on every new plan */}
          {route?.optimized.map((t, i) => {
            const road = roads[t.runId];
            if (!road) return null;
            const d = pathOf(roadPts(road));
            const color = TRUCK_COLORS[i % TRUCK_COLORS.length];
            const sig = `${t.runId}:${road.coords.length}:${t.stops.map((s) => s.clientId).join(",")}`;
            const dim = focusTruck && focusTruck !== t.runId;
            return (
              <motion.g key={t.runId} animate={{ opacity: dim ? 0.15 : driving ? 0.55 : 1 }} transition={{ duration: 0.3 }}>
                <path d={d} fill="none" stroke="hsl(var(--card))" strokeWidth={7} strokeLinecap="round" strokeLinejoin="round" opacity={0.9} />
                <motion.path
                  key={sig}
                  d={d} fill="none" stroke={color} strokeWidth={3.5} strokeLinecap="round" strokeLinejoin="round"
                  initial={{ pathLength: 0 }} animate={{ pathLength: 1 }}
                  transition={{ duration: 1.6, ease: [0.65, 0, 0.35, 1], delay: i * 0.15 }}
                />
              </motion.g>
            );
          })}

          {/* during the replay: each truck fills its road behind it */}
          {driving && route?.optimized.map((t, i) => roads[t.runId] ? (
            <TravelledRoad key={t.runId} road={roads[t.runId]} color={TRUCK_COLORS[i % TRUCK_COLORS.length]} project={p} roadPts={roadPts}
              done={t.stops.filter((s) => delivered[s.clientId]).length} total={t.stops.length} finished={phase === "delivered"} dim={!!focusTruck && focusTruck !== t.runId} />
          ) : null)}

          {/* stores */}
          {forecast.clients.map((c) => {
            const at = p(c.lng, c.lat);
            const delay = (dist(at, depot) / reach) * (SWEEP_MS / 1000) * 0.92;
            const tr = truckOf[c.clientId];
            return (
              <StoreMarker
                key={`${c.clientId}-${sweepKey}`}
                c={c} at={at} delay={delay} scale={scale} call={calls[c.clientId]} delivered={!!delivered[c.clientId]}
                seq={tr && focusRun === tr.truck ? tr.seq : undefined} seqColor={tr ? TRUCK_COLORS[tr.truck % TRUCK_COLORS.length] : undefined}
                selected={selected === c.clientId} hovered={hover === c.clientId}
                dim={(!!focusEvent && !touched(c, focusEvent)) || (focusRun !== undefined && focusRun >= 0 && tr?.truck !== focusRun)}
                halo={focusEvent && touched(c, focusEvent) ? focusColor : undefined}
                onEnter={() => setHover(c.clientId)} onLeave={() => setHover(undefined)}
                onClick={() => onSelect(c.clientId === selected ? undefined : c.clientId)}
              />
            );
          })}

          {/* trucks */}
          {driving && route?.optimized.map((t, i) => roads[t.runId] ? (
            <TruckMarker key={t.runId} road={roads[t.runId]} color={TRUCK_COLORS[i % TRUCK_COLORS.length]} project={p} n={i + 1}
              done={t.stops.filter((s) => delivered[s.clientId]).length} total={t.stops.length} finished={phase === "delivered"} />
          ) : null)}
        </svg>
      ) : null}

      {/* depot (HTML: crisp text) */}
      {ready ? (
        <div className="pointer-events-none absolute" style={{ left: depot.x, top: depot.y, transform: "translate(-50%,-50%)" }}>
          <span className="absolute inset-0 animate-ping rounded-full bg-primary/30 [animation-duration:2.4s]" />
          <div className="relative grid h-8 w-8 place-items-center rounded-full bg-foreground text-background shadow-pop ring-4 ring-card"><Warehouse size={15} strokeWidth={1.9} /></div>
          <div className="absolute left-1/2 top-10 -translate-x-1/2 whitespace-nowrap rounded-full bg-card px-2 py-0.5 text-[10.5px] font-medium text-foreground shadow-sm">{forecast.depot.name}</div>
        </div>
      ) : null}

      {/* hover / selected name tag */}
      <AnimatePresence>
        {ready && (hover ?? selected) ? (() => {
          const c = forecast.clients.find((x) => x.clientId === (hover ?? selected));
          if (!c) return null;
          const at = p(c.lng, c.lat);
          return (
            <motion.div key={c.clientId} className="pointer-events-none absolute z-10" style={{ left: at.x, top: at.y }}
              initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: 0.14 }}>
              <div className="whitespace-nowrap rounded-lg border border-border bg-card px-2.5 py-1.5 shadow-pop" style={{ transform: `translate(-50%, calc(-100% - ${18 * scale + 10}px))` }}>
                <div className="text-[12px] font-medium leading-4">{c.name}</div>
                <div className="text-[11px] leading-4 text-muted-foreground">{c.city} · {pinLabel(c, calls[c.clientId], delivered[c.clientId])}</div>
              </div>
            </motion.div>
          );
        })() : null}
      </AnimatePresence>

      {/* confirmation toast */}
      <div className="pointer-events-none absolute left-4 top-4 z-20">
        <AnimatePresence>
          {toast ? <MapToast key={toast.id} toast={toast} /> : null}
        </AnimatePresence>
      </div>

      {/* controls */}
      <div className="absolute right-3 top-3 flex flex-col overflow-hidden rounded-lg border border-border bg-card shadow-sm">
        <button type="button" aria-label="Zoomer" onClick={() => zoom(1)} className="grid h-8 w-8 place-items-center text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"><Plus size={15} /></button>
        <span className="h-px bg-border" />
        <button type="button" aria-label="Dézoomer" onClick={() => zoom(-1)} className="grid h-8 w-8 place-items-center text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"><Minus size={15} /></button>
      </div>
      <button type="button" aria-label="Tout voir" title="Tout voir" onClick={fit}
        className="absolute bottom-4 right-3 grid h-10 w-10 place-items-center rounded-full border border-border bg-card text-foreground shadow-pop transition-transform hover:scale-105 active:scale-95">
        <Locate size={17} />
      </button>

      <Legend route={route} />
    </div>
  );
}

function StoreMarker({ c, at, delay, scale, call, delivered, seq, seqColor, selected, hovered, dim, halo, onEnter, onLeave, onClick }: {
  c: ForecastClient; at: Pt; delay: number; scale: number; call?: CampaignCall; delivered: boolean; seq?: number; seqColor?: string;
  selected: boolean; hovered: boolean; dim: boolean; halo?: string; onEnter: () => void; onLeave: () => void; onClick: () => void;
}) {
  const s = call?.status;
  const live = s === "ringing" || s === "talking";
  const yes = s === "confirmed" || s === "modified";
  const no = s === "declined" || s === "failed";
  const later = s === "callback";
  const has = (c.status !== "predicted" && !no) || yes; // has an order for the day
  const R = 15;
  const fill = has ? "hsl(var(--primary))" : no ? "hsl(var(--muted))" : "hsl(var(--card))";
  const ring = live ? "hsl(var(--agent-voice))" : later ? "hsl(var(--warning))" : has ? "hsl(var(--card))" : "hsl(var(--border-hover))";
  const ink = has ? "white" : live ? "hsl(var(--agent-voice))" : later ? "hsl(var(--warning))" : "hsl(var(--muted-foreground))";
  const big = hovered || selected;

  return (
    <g transform={`translate(${at.x},${at.y}) scale(${scale})`} className="pointer-events-auto cursor-pointer" onMouseEnter={onEnter} onMouseLeave={onLeave}
      onClick={(e) => { e.stopPropagation(); onClick(); }}>
      <motion.g
        initial={{ scale: 0, opacity: 0 }}
        animate={{ scale: big ? 1.18 : 1, opacity: dim ? 0.2 : no ? 0.55 : 1 }}
        transition={{ scale: { type: "spring", stiffness: 480, damping: 20, delay: big ? 0 : delay }, opacity: { duration: 0.25, delay: dim ? 0 : delay } }}
      >
        {/* green halo for stores with an order; event colour when an event is focused */}
        <motion.circle r={R + 9} fill={halo ?? "hsl(var(--primary))"} initial={false} animate={{ opacity: halo ? 0.28 : has ? 0.16 : 0, scale: has || halo ? 1 : 0.6 }} transition={{ duration: 0.4 }} />
        {/* in call: ripples */}
        {live ? [0, 0.7].map((d) => (
          <motion.circle key={d} r={R} fill="none" stroke="hsl(var(--agent-voice))" strokeWidth={2}
            initial={{ scale: 1, opacity: 0.65 }} animate={{ scale: s === "talking" ? 2.3 : 1.9, opacity: 0 }}
            transition={{ duration: s === "talking" ? 1.5 : 1.1, repeat: Infinity, delay: d, ease: "easeOut" }} />
        )) : null}
        {/* yes: one pop */}
        <AnimatePresence>
          {yes ? (
            <motion.circle key="pop" r={R} fill="none" stroke="hsl(var(--primary))" strokeWidth={3}
              initial={{ scale: 1, opacity: 0.9 }} animate={{ scale: 2.6, opacity: 0 }} transition={{ duration: 0.9, ease: "easeOut" }} />
          ) : null}
        </AnimatePresence>
        <circle r={R + 1} cy={1.8} fill="#0b1f15" opacity={0.16} />
        <motion.circle r={R} strokeWidth={2.5} initial={false} animate={{ fill, stroke: ring }} transition={{ duration: 0.35 }} />
        {selected ? <circle r={R + 4.5} fill="none" stroke="hsl(var(--foreground))" strokeWidth={1.5} opacity={0.7} /> : null}
        <Store x={-8} y={-8} width={16} height={16} color={ink} strokeWidth={2.1} />
        {/* add-on possible */}
        {c.status === "upsell" && !yes && !no ? (
          <g transform="translate(11,-11)">
            <circle r={6.5} fill="hsl(var(--violet))" stroke="hsl(var(--card))" strokeWidth={1.75} />
            <path d="M0 -3 V3 M-3 0 H3" stroke="white" strokeWidth={1.7} strokeLinecap="round" />
          </g>
        ) : null}
        {/* delivered */}
        <AnimatePresence>
          {delivered ? (
            <motion.g key="ok" initial={{ scale: 0 }} animate={{ scale: 1 }} transition={{ type: "spring", stiffness: 600, damping: 18 }}>
              <g transform="translate(11,-11)">
                <circle r={7} fill="hsl(var(--success))" stroke="hsl(var(--card))" strokeWidth={1.75} />
                <Check x={-4.5} y={-4.5} width={9} height={9} color="white" strokeWidth={3.2} />
              </g>
            </motion.g>
          ) : null}
        </AnimatePresence>
        {/* stop number of the focused delivery */}
        {seq ? (
          <g transform="translate(-12,-12)">
            <circle r={7.5} fill={seqColor} stroke="hsl(var(--card))" strokeWidth={1.75} />
            <text textAnchor="middle" dy="3.4" fontSize="9.5" fontWeight={700} fill="white">{seq}</text>
          </g>
        ) : null}
      </motion.g>
      {/* delivered: burst */}
      <AnimatePresence>
        {delivered ? (
          <motion.circle key="burst" r={R} fill="none" stroke="hsl(var(--success))" strokeWidth={2.5}
            initial={{ scale: 1, opacity: 1 }} animate={{ scale: 2.8, opacity: 0 }} transition={{ duration: 1, ease: "easeOut" }} />
        ) : null}
      </AnimatePresence>
    </g>
  );
}

/** How far along its road a truck is: depot, then the last stop it delivered, back to the depot at the end. */
function useDrive(road: Road, done: number, total: number, finished: boolean) {
  const end = road.cum[road.cum.length - 1];
  const target = finished && done >= total ? end : done === 0 ? 0 : road.cum[road.stopAt[Math.min(done, road.stopAt.length) - 1]] ?? end;
  const mv = useMotionValue(0);
  const [d, setD] = useState(0);
  useMotionValueEvent(mv, "change", setD);
  useEffect(() => {
    const from = mv.get();
    const span = Math.abs(target - from) / (end || 1);
    const ctl = animate(mv, target, { duration: Math.min(2.4, 0.5 + span * 6), ease: [0.45, 0, 0.25, 1] });
    return () => ctl.stop();
  }, [target, end, mv]);
  return d;
}

function TravelledRoad({ road, color, project, roadPts, done, total, finished, dim }: {
  road: Road; color: string; project: (lng: number, lat: number) => Pt; roadPts: (road: Road, upTo?: number) => Pt[];
  done: number; total: number; finished: boolean; dim: boolean;
}) {
  const d = useDrive(road, done, total, finished);
  let n = 0;
  while (n < road.coords.length && road.cum[n] <= d) n++;
  const pts = roadPts(road, n);
  const [lng, lat] = pointAt(road, d);
  pts.push(project(lng, lat));
  if (pts.length < 2) return null;
  return <path d={pathOf(pts)} fill="none" stroke={color} strokeWidth={5} strokeLinecap="round" strokeLinejoin="round" opacity={dim ? 0.2 : 1} />;
}

function TruckMarker({ road, color, project, n, done, total, finished }: {
  road: Road; color: string; project: (lng: number, lat: number) => Pt; n: number; done: number; total: number; finished: boolean;
}) {
  const d = useDrive(road, done, total, finished);
  const [lng, lat] = pointAt(road, d);
  const at = project(lng, lat);
  return (
    <g transform={`translate(${at.x},${at.y})`}>
      <motion.g initial={{ scale: 0 }} animate={{ scale: 1 }} transition={{ type: "spring", stiffness: 420, damping: 18, delay: n * 0.12 }}>
        <circle r={22} fill={color} opacity={0.18} />
        <g>
          <rect x={-17} y={-11} width={34} height={26} rx={8} fill="#0b1f15" opacity={0.18} />
          <rect x={-17} y={-13} width={34} height={26} rx={8} fill={color} stroke="hsl(var(--card))" strokeWidth={2} />
          <TruckIcon x={-12} y={-8} width={16} height={16} color="white" strokeWidth={2.2} />
          <text x={10} y={4.2} textAnchor="middle" fontSize="11" fontWeight={700} fill="white">{n}</text>
        </g>
      </motion.g>
    </g>
  );
}

function MapToast({ toast }: { toast: Toast }) {
  const [shown, setShown] = useState(true);
  useEffect(() => { const t = setTimeout(() => setShown(false), 3200); return () => clearTimeout(t); }, []);
  return (
    <AnimatePresence>
      {shown ? (
        <motion.div layout
          initial={{ opacity: 0, y: -10, scale: 0.96 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: -6, scale: 0.98 }}
          transition={{ type: "spring", stiffness: 420, damping: 30 }}
          className="flex items-center gap-3 rounded-2xl border border-border bg-card px-4 py-3 shadow-pop">
          <motion.span initial={{ scale: 0, rotate: -30 }} animate={{ scale: 1, rotate: 0 }} transition={{ type: "spring", stiffness: 500, damping: 16, delay: 0.08 }}
            className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-primary text-primary-foreground">
            {toast.kind === "delivered" ? <TruckIcon size={15} strokeWidth={2.4} /> : <Check size={16} strokeWidth={3} />}
          </motion.span>
          <div className="min-w-0">
            <div className="max-w-[300px] truncate text-[14px] font-semibold leading-5 tracking-[-0.01em]">{toast.title}</div>
            <div className="text-[12.5px] leading-4 text-muted-foreground">{toast.sub}</div>
          </div>
        </motion.div>
      ) : null}
    </AnimatePresence>
  );
}

function Legend({ route }: { route?: RoutePlan }) {
  const item = (dot: React.ReactNode, label: string, k: string) => (
    <span key={k} className="flex items-center gap-1.5 whitespace-nowrap">{dot}<span>{label}</span></span>
  );
  return (
    <div className="absolute bottom-4 left-3 flex max-w-[calc(100%-80px)] flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-border bg-card/95 px-2.5 py-1.5 text-[11px] text-muted-foreground shadow-sm backdrop-blur">
      {item(<span className="h-3 w-3 rounded-full bg-primary ring-2 ring-primary/20" />, "Commandé", "a")}
      {item(<span className="h-3 w-3 rounded-full border-2 border-border-hover bg-card" />, "À appeler", "b")}
      {item(<span className="grid h-3 w-3 place-items-center rounded-full bg-violet text-[9px] font-bold leading-none text-white">+</span>, "Ajout possible", "c")}
      {item(<span className="h-3 w-3 rounded-full border-2 border-agent-voice bg-card" />, "En appel", "d")}
      {route?.optimized.map((t, i) => item(<span className="h-[4px] w-4 rounded-full" style={{ background: TRUCK_COLORS[i % TRUCK_COLORS.length] }} />, `Livraison ${i + 1}`, t.runId))}
    </div>
  );
}

// ── helpers ──

function bounds(f: Forecast): [[number, number], [number, number]] {
  const pts = [...f.clients, f.depot];
  const lats = pts.map((x) => x.lat), lngs = pts.map((x) => x.lng);
  return [[Math.min(...lngs), Math.min(...lats)], [Math.max(...lngs), Math.max(...lats)]];
}

const dist = (a: Pt, b: Pt) => Math.hypot(a.x - b.x, a.y - b.y);

/** SVG path through the points, dropping the ones closer than ~1.5 px to the last kept (road geometry is dense). */
function pathOf(pts: Pt[]): string {
  if (!pts.length) return "";
  let d = `M${pts[0].x.toFixed(1)},${pts[0].y.toFixed(1)}`;
  let last = pts[0];
  for (let i = 1; i < pts.length; i++) {
    const q = pts[i];
    if (i < pts.length - 1 && Math.abs(q.x - last.x) + Math.abs(q.y - last.y) < 2) continue;
    d += `L${q.x.toFixed(1)},${q.y.toFixed(1)}`;
    last = q;
  }
  return d;
}

/** Quiet the basemap: no POIs or transit, so the stores carry the page. */
function tone(m: MlMap) {
  for (const l of m.getStyle()?.layers ?? []) {
    if (/poi|housenum|aeroway|transit|rail/.test(l.id)) m.setLayoutProperty(l.id, "visibility", "none");
  }
}

export function pinLabel(c: ForecastClient, call?: CampaignCall, delivered?: string): string {
  if (delivered) return `livré à ${delivered}`;
  const s = call?.status;
  if (s === "ringing") return "ça sonne…";
  if (s === "talking") return "en ligne";
  if (s === "confirmed") return "commande confirmée";
  if (s === "modified") return "commande ajustée";
  if (s === "declined") return "pas cette fois";
  if (s === "callback") return "à rappeler";
  if (s === "failed") return "appel échoué";
  if (c.call === false) return "non appelé · enlèvement au quai";
  return c.status === "ordered" ? "déjà commandé" : c.status === "upsell" ? "commandé · ajout possible" : `prévu · ${Math.round(c.probability * 100)} %`;
}

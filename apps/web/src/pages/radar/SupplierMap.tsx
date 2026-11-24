/**
 * The supply map: Médina's suppliers (Sisteron, Ancenis, Rodez, Saragosse…) and the depot at the MIN des Arnavaux.
 * Same basemap as the client radar, with an SVG overlay drawn in React:
 *   dashed grey arc = supplier to call · violet arc with moving dashes = the agent is on the phone
 *   green arc + a truck running along it = purchase order sent, the goods are on their way · faded = no / failed.
 */
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import maplibregl, { type Map as MlMap } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { motion } from "framer-motion";
import { Check, Factory, PhoneCall, Truck, Warehouse } from "lucide-react";
import { useTheme } from "@/ui";
import { cn } from "@/lib/cn";
import { fmtMoney } from "@/lib/format";

const STYLE = {
  light: "https://basemaps.cartocdn.com/gl/voyager-gl-style/style.json",
  dark: "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
};
const PAD = { top: 70, bottom: 60, left: 230, right: 140 }; // room for the labels, which sit on the outer side of each pin
const NEAR = 48; // a supplier this close to the depot on screen (Vitrolles) is drawn beside it, not on top of it

export type PinStatus = "queued" | "ringing" | "talking" | "confirmed" | "modified" | "declined" | "callback" | "failed";
export interface SupplierPin {
  id: string; name: string; city?: string; lat?: number | null; lng?: number | null; leadTimeDays?: number;
  totalHt: number; orderedHt: number; status: PinStatus;
}
type Pt = { x: number; y: number };

export function SupplierMap({ depot, pins, selected, onSelect }: {
  depot: { name: string; lat: number; lng: number }; pins: SupplierPin[]; selected?: string; onSelect: (id?: string) => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const map = useRef<MlMap | null>(null);
  const [, setFrame] = useState(0);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [ready, setReady] = useState(false);
  const { resolved } = useTheme();
  const placed = pins.filter((s) => s.lat != null && s.lng != null);

  useLayoutEffect(() => {
    if (!box.current) return;
    const m = new maplibregl.Map({
      container: box.current, style: STYLE[resolved], bounds: bounds(depot, placed), fitBoundsOptions: { padding: PAD },
      attributionControl: { compact: true }, dragRotate: false, pitchWithRotate: false, touchPitch: false, fadeDuration: 0,
    });
    m.touchZoomRotate.disableRotation();
    let raf = 0;
    const redraw = () => { cancelAnimationFrame(raf); raf = requestAnimationFrame(() => setFrame((f) => f + 1)); };
    m.on("move", redraw);
    m.on("resize", () => { setSize({ w: m.getContainer().clientWidth, h: m.getContainer().clientHeight }); m.fitBounds(bounds(depot, placed), { padding: PAD, duration: 0 }); redraw(); });
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

  const p = (lng: number, lat: number): Pt => {
    const q = map.current?.project([lng, lat]);
    return q ? { x: q.x, y: q.y } : { x: -99, y: -99 };
  };
  const d = p(depot.lng, depot.lat);
  const at = (s: SupplierPin): Pt => {
    const a = p(s.lng!, s.lat!);
    return Math.hypot(a.x - d.x, a.y - d.y) < NEAR ? { x: d.x - 58, y: d.y + 38 } : a;
  };

  return (
    <div className="relative h-full w-full overflow-hidden">
      <div ref={box} className="!absolute inset-0 [&_.maplibregl-ctrl-attrib]:!bg-card/70 [&_.maplibregl-ctrl-attrib]:text-[10px]" />
      {ready ? (
        <svg className="pointer-events-none absolute inset-0" width={size.w} height={size.h}>
          <defs>
            <filter id="sup-glow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="3" /></filter>
          </defs>
          {placed.map((s, i) => {
            const path = arc(at(s), d);
            const st = s.status;
            const live = st === "ringing" || st === "talking";
            const bought = st === "confirmed" || st === "modified";
            const off = st === "declined" || st === "failed";
            const color = bought ? "hsl(var(--primary))" : live ? "hsl(var(--agent-voice))" : "hsl(var(--muted-foreground))";
            return (
              <g key={s.id} opacity={off ? 0.3 : 1}>
                {bought || live ? <path d={path} fill="none" stroke={color} strokeWidth={7} opacity={0.22} filter="url(#sup-glow)" /> : null}
                <motion.path d={path} fill="none" stroke={color} strokeWidth={bought ? 3 : 2} strokeLinecap="round"
                  strokeDasharray={bought ? undefined : live ? "7 6" : "3 6"}
                  initial={{ pathLength: 0, opacity: 0 }} animate={{ pathLength: 1, opacity: 1 }} transition={{ duration: 1.1, delay: 0.3 + i * 0.12, ease: "easeInOut" }}
                  style={live ? { animation: "sup-dash 0.7s linear infinite" } : undefined} />
                {bought ? <RunningTruck path={path} delay={i * 0.4} /> : null}
              </g>
            );
          })}
        </svg>
      ) : null}

      {/* depot */}
      {ready ? (
        <div className="pointer-events-none absolute z-10" style={{ left: d.x, top: d.y, transform: "translate(-50%,-50%)" }}>
          <span className="absolute inset-0 animate-ping rounded-full bg-primary/30 [animation-duration:2.4s]" />
          <div className="relative grid h-9 w-9 place-items-center rounded-full bg-foreground text-background shadow-pop ring-4 ring-card"><Warehouse size={16} strokeWidth={1.9} /></div>
          <div className="absolute left-11 top-1/2 -translate-y-1/2 whitespace-nowrap rounded-full bg-card px-2 py-0.5 text-[10.5px] font-medium shadow-sm">Dépôt Médina · {depot.name}</div>
        </div>
      ) : null}

      {/* suppliers */}
      {ready ? placed.map((s, i) => {
        const a = at(s);
        const st = s.status;
        const live = st === "ringing" || st === "talking";
        const bought = st === "confirmed" || st === "modified";
        const off = st === "declined" || st === "failed";
        // the label goes on the side away from the depot, so it never covers an arc
        const dx = a.x - d.x, dy = a.y - d.y;
        const side = Math.abs(dy) > 2 * Math.abs(dx) ? (dy < 0 ? "top" : "bottom") : dx < 0 ? "left" : "right";
        return (
          <motion.button key={s.id} type="button" onClick={() => onSelect(s.id === selected ? undefined : s.id)}
            initial={{ opacity: 0, scale: 0.6 }} animate={{ opacity: off ? 0.45 : 1, scale: 1 }} transition={{ type: "spring", stiffness: 380, damping: 26, delay: 0.2 + i * 0.1 }}
            className="absolute z-20" style={{ left: a.x, top: a.y, transform: "translate(-50%,-50%)" }}>
            {live ? <span className="absolute -inset-2 animate-ping rounded-full bg-agent-voice/30 [animation-duration:1.2s]" /> : null}
            <span className={cn("relative grid h-8 w-8 place-items-center rounded-full shadow-pop ring-4 ring-card transition-colors",
              bought ? "bg-primary text-primary-foreground" : live ? "bg-agent-voice text-white" : "bg-card text-foreground",
              selected === s.id && "ring-foreground/70")}>
              {bought ? <Check size={15} strokeWidth={2.4} /> : live ? <PhoneCall size={14} /> : <Factory size={14} />}
            </span>
            <span className={cn("absolute whitespace-nowrap rounded-lg border bg-card px-2 py-1 text-left shadow-sm",
              LABEL[side], selected === s.id ? "border-foreground/40" : "border-border")}>
              <span className="block text-[11.5px] font-medium leading-4">{short(s.name)}</span>
              <span className="block text-[10.5px] leading-4 text-muted-foreground">
                {s.city?.replace(" (Espagne)", " 🇪🇸")} · J+{s.leadTimeDays ?? 1} ·{" "}
                <span className={cn("num font-medium", bought ? "text-primary" : "text-foreground")}>{fmtMoney(bought ? s.orderedHt : s.totalHt, { compact: true })}</span>
              </span>
            </span>
          </motion.button>
        );
      }) : null}

      <div className="absolute bottom-4 left-3 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-border bg-card/95 px-2.5 py-1.5 text-[11px] text-muted-foreground shadow-sm backdrop-blur">
        <span className="flex items-center gap-1.5"><span className="h-0 w-4 border-t-2 border-dashed border-muted-foreground" />à appeler</span>
        <span className="flex items-center gap-1.5"><span className="h-0 w-4 border-t-2 border-dashed border-agent-voice" />l'agent appelle</span>
        <span className="flex items-center gap-1.5"><span className="h-[3px] w-4 rounded-full bg-primary" />commande envoyée, en route</span>
      </div>
      <style>{"@keyframes sup-dash { to { stroke-dashoffset: -13; } }"}</style>
    </div>
  );
}

const LABEL = {
  top: "bottom-10 left-1/2 -translate-x-1/2", bottom: "top-10 left-1/2 -translate-x-1/2",
  left: "right-10 top-1/2 -translate-y-1/2", right: "left-10 top-1/2 -translate-y-1/2",
} as const;

/** A truck that keeps driving the arc from the supplier to the depot once the order is sent. */
function RunningTruck({ path, delay }: { path: string; delay: number }) {
  const ref = useRef<SVGPathElement>(null);
  const [pt, setPt] = useState<Pt>();
  useEffect(() => {
    let raf = 0;
    const t0 = performance.now() + delay * 1000;
    const tick = (now: number) => {
      const el = ref.current;
      if (el) {
        const k = Math.max(0, ((now - t0) / 3200) % 1);
        const q = el.getPointAtLength(k * el.getTotalLength());
        setPt({ x: q.x, y: q.y });
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [delay]);
  return (
    <>
      <path ref={ref} d={path} fill="none" stroke="none" />
      {pt ? (
        <g transform={`translate(${pt.x - 10} ${pt.y - 10})`}>
          <circle cx={10} cy={10} r={10} fill="hsl(var(--primary))" stroke="hsl(var(--card))" strokeWidth={2} />
          <Truck x={4} y={4} width={12} height={12} color="hsl(var(--primary-foreground))" strokeWidth={2.2} />
        </g>
      ) : null}
    </>
  );
}

/** A gentle curve from a to b (bowing to the left of the direction of travel). */
function arc(a: Pt, b: Pt): string {
  const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
  const dx = b.x - a.x, dy = b.y - a.y;
  const k = 0.22;
  return `M${a.x},${a.y} Q${mx - dy * k},${my + dx * k} ${b.x},${b.y}`;
}

function short(name: string) {
  return name.replace("Abattoir des Agneaux de Haute-Provence", "Abattoir de Haute-Provence");
}

function bounds(depot: { lat: number; lng: number }, pins: SupplierPin[]): [[number, number], [number, number]] {
  const pts = [depot, ...pins.map((s) => ({ lat: s.lat!, lng: s.lng! }))];
  const lats = pts.map((x) => x.lat), lngs = pts.map((x) => x.lng);
  return [[Math.min(...lngs), Math.min(...lats)], [Math.max(...lngs), Math.max(...lats)]];
}

function tone(m: MlMap) {
  for (const l of m.getStyle()?.layers ?? []) {
    if (/poi|housenum|aeroway|transit|rail/.test(l.id)) m.setLayoutProperty(l.id, "visibility", "none");
  }
}

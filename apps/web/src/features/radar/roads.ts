/**
 * Road geometry for a truck's loop (depot → stops → depot) from the public OSRM router, so routes follow the streets.
 * One request at a time (the demo server allows ~1/s), cached by stop sequence; null while loading or when offline
 * (the map then draws straight legs).
 */
import { useEffect, useState } from "react";
import type { LatLng, RoutePlan } from "./types";

export interface Road {
  coords: [number, number][]; // [lng, lat]
  stopAt: number[]; // index in coords of each stop, in order (the depot excluded)
  cum: number[]; // cumulative length along coords (degrees, for interpolation only)
  merc: Float64Array; // Web Mercator [x, y] (0..1) of every point: projecting is then a scale + offset per frame
}

/** Web Mercator, normalized to 0..1 (the world MapLibre projects onto 512·2^zoom px). */
export function mercator(lng: number, lat: number): [number, number] {
  const s = Math.sin((lat * Math.PI) / 180);
  return [(lng + 180) / 360, 0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI)];
}

const cache = new Map<string, Road | "failed">();
const latest = new Map<string, string>(); // runId → the stop sequence it needs now (older requests are skipped)
const waiters = new Map<string, ((r: Road | "failed" | "skipped") => void)[]>();
let chain: Promise<unknown> = Promise.resolve();

const keyOf = (pts: LatLng[]) => pts.map((p) => `${p.lng.toFixed(5)},${p.lat.toFixed(5)}`).join(";");

function straight(pts: LatLng[]): Road {
  return withCum(pts.map((p) => [p.lng, p.lat] as [number, number]), pts.slice(1, -1).map((_, i) => i + 1));
}

function withCum(coords: [number, number][], stopAt: number[]): Road {
  const cum = [0];
  for (let i = 1; i < coords.length; i++) cum.push(cum[i - 1] + Math.hypot(coords[i][0] - coords[i - 1][0], coords[i][1] - coords[i - 1][1]));
  const merc = new Float64Array(coords.length * 2);
  coords.forEach(([lng, lat], i) => { const [x, y] = mercator(lng, lat); merc[2 * i] = x; merc[2 * i + 1] = y; });
  return { coords, stopAt, cum, merc };
}

const OSRM = "https://router.project-osrm.org";
const pause = (ms: number) => new Promise((res) => setTimeout(res, ms));

/** Great-circle km. */
function km(a: LatLng, b: LatLng) {
  const r = Math.PI / 180;
  const x = Math.sin(((b.lat - a.lat) * r) / 2) ** 2 + Math.cos(a.lat * r) * Math.cos(b.lat * r) * Math.sin(((b.lng - a.lng) * r) / 2) ** 2;
  return 12742 * Math.asin(Math.sqrt(x));
}

async function osrm(path: string) {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), 6000);
  try {
    const r = await fetch(`${OSRM}${path}`, { signal: ctl.signal });
    if (!r.ok) throw new Error(`osrm ${r.status}`);
    return await r.json();
  } finally {
    clearTimeout(t);
  }
}

interface Trip { coords: [number, number][]; cut: number[]; legKm: number[] } // cut: index in coords of every waypoint, ends included

async function trip(pts: LatLng[]): Promise<Trip> {
  const j = await osrm(`/route/v1/driving/${keyOf(pts)}?overview=full&geometries=geojson`);
  const rt = j.routes?.[0];
  const coords: [number, number][] = rt?.geometry?.coordinates;
  if (!coords?.length) throw new Error("no route");
  // each stop = the first geometry point nearest to its snapped waypoint, walking forward
  const wps: [number, number][] = (j.waypoints ?? []).map((w: { location: [number, number] }) => w.location);
  const cut = [0];
  let from = 0;
  for (const w of wps.slice(1, -1)) {
    let best = from, bd = Infinity;
    for (let i = from; i < coords.length; i++) {
      const d = Math.hypot(coords[i][0] - w[0], coords[i][1] - w[1]);
      if (d < bd) { bd = d; best = i; }
      if (d < 1e-6) break;
    }
    cut.push(best);
    from = best;
  }
  cut.push(coords.length - 1);
  return { coords, cut, legKm: (rt.legs ?? []).map((l: { distance: number }) => l.distance / 1000) };
}

/**
 * Legs no van would drive: OSRM sometimes snaps a stop onto a road it can only leave the long way round (inside the
 * port, behind a gate, onto a ferry): a 3 km hop comes back as 800 km. A real detour stays within ~3.5× the straight line.
 */
const badLegs = (pts: LatLng[], t: Trip) =>
  t.legKm.flatMap((d, i) => (d > 3.5 * km(pts[i], pts[i + 1]) + 4 ? [i] : []));

/** The closest other road to a stop, a named street first (the first snap is the one that went wrong). */
async function resnap(p: LatLng): Promise<LatLng | undefined> {
  const j = await osrm(`/nearest/v1/driving/${p.lng.toFixed(5)},${p.lat.toFixed(5)}?number=10`);
  const c = ((j.waypoints ?? []) as { location: [number, number]; name: string; distance: number }[]).slice(1).filter((w) => w.distance < 500);
  const w = c.find((x) => x.name) ?? c[0];
  return w ? { lng: w.location[0], lat: w.location[1] } : undefined;
}

/** The trip's geometry with every leg still bad drawn straight (short, honest) instead of the crazy detour. */
function stitch(pts: LatLng[], t: Trip, bad: number[]): Road {
  const coords: [number, number][] = [t.coords[0]];
  const stopAt: number[] = [];
  for (let i = 0; i < t.cut.length - 1; i++) {
    if (bad.includes(i)) coords.push([pts[i + 1].lng, pts[i + 1].lat]);
    else coords.push(...t.coords.slice(t.cut[i] + 1, t.cut[i + 1] + 1));
    if (i < t.cut.length - 2) stopAt.push(coords.length - 1);
  }
  return withCum(coords, stopAt);
}

export async function roadFor(pts: LatLng[]): Promise<Road> {
  let t = await trip(pts);
  let bad = badLegs(pts, t);
  if (bad.length) {
    // re-snap the stops at either end of a bad leg (never the depot), then try once more
    const fix = [...new Set(bad.flatMap((i) => [i, i + 1]))].filter((s) => s > 0 && s < pts.length - 1);
    const moved = [...pts];
    for (const s of fix) {
      await pause(350);
      moved[s] = (await resnap(pts[s]).catch(() => undefined)) ?? pts[s];
    }
    await pause(350);
    const t2 = await trip(moved).catch(() => undefined);
    const bad2 = t2 ? badLegs(pts, t2) : bad;
    if (t2 && bad2.length < bad.length) { t = t2; bad = bad2; }
  }
  return stitch(pts, t, bad);
}

async function fetchRoad(pts: LatLng[], runId: string): Promise<Road | "failed" | "skipped"> {
  const k = keyOf(pts);
  latest.set(runId, k);
  const hit = cache.get(k);
  if (hit) return hit;
  if (waiters.has(k)) return new Promise((res) => waiters.get(k)!.push(res));
  waiters.set(k, []);
  const run = chain.then(async (): Promise<Road | "failed" | "skipped"> => {
    if (latest.get(runId) !== k) return "skipped"; // a newer plan came in while this one waited
    try {
      return await roadFor(pts);
    } catch {
      return "failed" as const;
    } finally {
      await pause(350); // stay polite with the demo server
    }
  });
  chain = run;
  const out = await run;
  if (out !== "failed" && out !== "skipped") cache.set(k, out); // a failure is retried with the next plan
  waiters.get(k)?.forEach((w) => w(out));
  waiters.delete(k);
  return out;
}

/** runId → road (straight legs until the road arrives, or for good when offline). */
export function useRoads(plan: RoutePlan | undefined, depot: LatLng): Record<string, Road> {
  const [roads, setRoads] = useState<Record<string, Road>>({});
  useEffect(() => {
    if (!plan) { setRoads({}); return; }
    let live = true;
    for (const t of plan.optimized) {
      const pts = [depot, ...t.stops, depot];
      const k = keyOf(pts);
      const hit = cache.get(k);
      if (hit && hit !== "failed") { setRoads((r) => ({ ...r, [t.runId]: hit })); continue; }
      // keep the previous road until the new one lands (no flash), straight legs if there is none
      setRoads((r) => (r[t.runId] ? r : { ...r, [t.runId]: straight(pts) }));
      fetchRoad(pts, t.runId).then((road) => {
        if (live && road !== "skipped") setRoads((r) => ({ ...r, [t.runId]: road === "failed" ? straight(pts) : road }));
      });
    }
    return () => { live = false; };
  }, [plan, depot]);
  return roads;
}

/** Point at a distance along the road (same units as road.cum). */
export function pointAt(road: Road, d: number): [number, number] {
  const { coords, cum } = road;
  if (d <= 0) return coords[0];
  const end = cum[cum.length - 1];
  if (d >= end) return coords[coords.length - 1];
  let lo = 0, hi = cum.length - 1;
  while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (cum[mid] <= d) lo = mid; else hi = mid; }
  const f = (d - cum[lo]) / (cum[hi] - cum[lo] || 1);
  return [coords[lo][0] + (coords[hi][0] - coords[lo][0]) * f, coords[lo][1] + (coords[hi][1] - coords[lo][1]) * f];
}

/**
 * Radar data: the forecast (GET /api/radar/forecast, bundled sample when the backend lacks it) and the call campaign
 * (POST /ai/campaigns + its SSE stream, or the offline simulator in ./mock when the AI server lacks it).
 * The campaign id sits in the URL (?campaign=…): a reload reconnects, and the server replays what was missed.
 */
import { useCallback, useEffect, useReducer, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { AI_BASE, API_BASE } from "@/lib/api";
import sample from "./sample-forecast.json";
import { runMockCampaign, runMockDeliveries } from "./mock";
import { callQueue, type CampaignCall, type CampaignEvent, type CampaignTotals, type Forecast, type RoutePlan } from "./types";

export const SAMPLE = sample as unknown as Forecast;
const DELIVERY_SPEED = 8; // route minutes per second of replay

async function getJson<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, { ...init, headers: { "content-type": "application/json", "x-actor": "user", ...init?.headers } });
  if (!r.ok) throw new Error(`${r.status} ${url}`);
  return r.json() as Promise<T>;
}

export function useForecast(date: string) {
  return useQuery({
    queryKey: ["radar", "forecast", date],
    queryFn: async (): Promise<Forecast & { offline?: boolean }> => {
      try {
        return await getJson<Forecast>(`${API_BASE}/radar/forecast?date=${date}`);
      } catch {
        return { ...SAMPLE, offline: true };
      }
    },
    staleTime: 60_000,
  });
}

export type Phase = "idle" | "starting" | "running" | "done" | "delivering" | "delivered";

/** One line of the live activity timeline. `key` groups the updates of one call into one item. */
export interface FeedItem {
  key: string;
  at: number;
  kind: "calling" | "yes" | "no" | "later" | "failed" | "route" | "delivered";
  clientId?: string;
  title: string;
  sub?: string;
  amount?: number;
}

export interface Toast { id: number; kind: "yes" | "delivered"; title: string; sub: string }

export interface CampaignState {
  phase: Phase;
  source?: "live" | "demo";
  id?: string;
  calls: Record<string, CampaignCall>; // by clientId
  byCall: Record<string, string>; // callId → clientId
  order: string[]; // clientIds, in the order calls started (newest last)
  route?: RoutePlan;
  routeVersion: number;
  totals: CampaignTotals;
  delivered: Record<string, string>; // clientId → ETA
  feed: FeedItem[]; // newest first
  toast?: Toast;
  seq: number;
  names: Record<string, string>; // clientId → name, for every client of the forecast
}

const ZERO: CampaignTotals = { called: 0, confirmed: 0, declined: 0, callback: 0, revenueHt: 0, upliftHt: 0 };
const INIT: CampaignState = { phase: "idle", calls: {}, byCall: {}, order: [], routeVersion: 0, totals: ZERO, delivered: {}, feed: [], seq: 0, names: {} };

type Action =
  | { type: "reset" }
  | { type: "names"; names: Record<string, string> }
  | { type: "start"; source: "live" | "demo"; id?: string; calls: CampaignCall[] }
  | { type: "phase"; phase: Phase }
  | { type: "event"; e: CampaignEvent }
  | { type: "batch"; events: CampaignEvent[] };

const quote = (t?: string) => (t ? `« ${t.length > 90 ? `${t.slice(0, 88).trimEnd()}…` : t} »` : undefined);
const lastClientLine = (c: CampaignCall) => [...c.transcript].reverse().find((l) => l.who === "client")?.text;

function upsert(feed: FeedItem[], item: FeedItem, moveToTop = false): FeedItem[] {
  const i = feed.findIndex((f) => f.key === item.key);
  if (i < 0) return [item, ...feed].slice(0, 80);
  if (moveToTop) return [item, ...feed.slice(0, i), ...feed.slice(i + 1)];
  return feed.map((f, j) => (j === i ? { ...f, ...item, at: f.at } : f));
}

function reduce(s: CampaignState, a: Action): CampaignState {
  if (a.type === "batch") return a.events.reduce((st, e) => reduce(st, { type: "event", e }), s);
  switch (a.type) {
    case "reset":
      return { ...INIT, names: s.names };
    case "names":
      return { ...s, names: a.names };
    case "phase":
      return { ...s, phase: a.phase };
    case "start":
      return {
        ...INIT, names: s.names, phase: "running", source: a.source, id: a.id,
        calls: Object.fromEntries(a.calls.map((c) => [c.clientId, { ...c, transcript: c.transcript ?? [] }])),
        byCall: Object.fromEntries(a.calls.map((c) => [c.callId, c.clientId])),
      };
  }
  const e = a.e;
  const now = Date.now();
  const patch = (st: CampaignState, clientId: string | undefined, f: (c: CampaignCall) => Partial<CampaignCall>): CampaignState => {
    const c = clientId ? st.calls[clientId] : undefined;
    return c ? { ...st, calls: { ...st.calls, [c.clientId]: { ...c, ...f(c) } } } : st;
  };
  switch (e.type) {
    case "call_status": {
      let next = patch(s, e.clientId, () => ({ status: e.status }));
      const byCall = s.byCall[e.callId] === e.clientId ? next.byCall : { ...next.byCall, [e.callId]: e.clientId };
      const order = e.status === "ringing" && !s.order.includes(e.clientId) ? [...s.order, e.clientId] : s.order;
      next = { ...next, byCall, order };
      const c = next.calls[e.clientId];
      if (!c) return next;
      const key = `call:${c.clientId}`;
      if (e.status === "ringing") return { ...next, feed: upsert(next.feed, { key, at: now, kind: "calling", clientId: c.clientId, title: `Appel de ${c.clientName}`, sub: "En attente de réponse…" }, true) };
      if (e.status === "talking") return { ...next, feed: upsert(next.feed, { key, at: now, kind: "calling", clientId: c.clientId, title: `En ligne avec ${c.clientName}`, sub: "L'agent présente la commande…" }) };
      const yes = e.status === "confirmed" || e.status === "modified";
      const kind = yes ? "yes" : e.status === "callback" ? "later" : e.status === "failed" ? "failed" : "no";
      const title = yes ? `${c.clientName} a confirmé` : e.status === "callback" ? `${c.clientName} : à rappeler` : e.status === "failed" ? `${c.clientName} : appel échoué` : `${c.clientName} : pas cette fois`;
      next = { ...next, feed: upsert(next.feed, { key, at: now, kind, clientId: c.clientId, title, sub: quote(lastClientLine(c)), amount: yes ? c.totalHt : undefined }, true) };
      if (yes) next = { ...next, seq: next.seq + 1, toast: { id: next.seq + 1, kind: "yes", title: `${c.clientName} a confirmé !`, sub: c.kind === "upsell" ? "Ajout à sa commande de mercredi." : "Livraison assurée mercredi matin." } };
      return next;
    }
    case "line": {
      const next = patch(s, s.byCall[e.callId], (c) => ({ transcript: [...c.transcript, { who: e.who, text: e.text }] }));
      const c = next.calls[s.byCall[e.callId]];
      if (!c || (c.status !== "talking" && c.status !== "ringing")) return next;
      return { ...next, feed: upsert(next.feed, { key: `call:${c.clientId}`, at: now, kind: "calling", clientId: c.clientId, title: `En ligne avec ${c.clientName}`, sub: `${e.who === "agent" ? "Agent" : "Client"} : ${quote(e.text)}` }) };
    }
    case "decision":
      return patch(s, s.byCall[e.callId], () => ({ decision: { label: e.label, confidence: e.confidence, source: e.source } }));
    case "order":
      return patch(s, e.clientId, () => ({ orderId: e.orderId, totalHt: e.totalHt }));
    case "route": {
      const before = new Set(s.route?.optimized.flatMap((t) => t.stops.map((x) => x.clientId)) ?? []);
      const added = e.plan.optimized.flatMap((t, i) => t.stops.filter((x) => !before.has(x.clientId) && s.calls[x.clientId]).map((x) => ({ x, t, i })));
      let feed = s.feed;
      if (!s.route) {
        const n = e.plan.optimized.reduce((a, t) => a + t.stops.length, 0);
        feed = upsert(feed, { key: `route:first`, at: now, kind: "route", title: "Tournées calculées", sub: `${e.plan.optimized.length} livraisons, ${n} arrêts` }, true);
      } else if (added.length) {
        const names = added.map((a) => s.calls[a.x.clientId].clientName);
        const t = added[0];
        feed = upsert(feed, { key: `route:${s.routeVersion + 1}`, at: now, kind: "route", title: "Tournée mise à jour", sub: `${names.slice(0, 2).join(", ")}${names.length > 2 ? ` +${names.length - 2}` : ""} ajouté à la livraison ${t.i + 1} (${t.t.route})` }, true);
      }
      return { ...s, route: e.plan, routeVersion: s.routeVersion + 1, feed };
    }
    case "totals": {
      const { type: _t, ...t } = e;
      return { ...s, totals: t };
    }
    case "done":
      return {
        ...s, phase: s.phase === "running" || s.phase === "starting" ? "done" : s.phase, totals: e.totals, seq: s.seq + 1,
        toast: { id: s.seq + 1, kind: "delivered", title: `${e.totals.confirmed} commandes en plus`, sub: `${Object.keys(s.calls).length} appels terminés · tournées prêtes à partir.` },
      };
    case "delivered": {
      const name = s.calls[e.clientId]?.clientName ?? s.names[e.clientId];
      const truck = s.route?.optimized.findIndex((t) => t.stops.some((x) => x.clientId === e.clientId)) ?? -1;
      return {
        ...s, phase: s.phase === "done" ? "delivering" : s.phase,
        delivered: { ...s.delivered, [e.clientId]: e.at },
        feed: upsert(s.feed, { key: `del:${e.clientId}`, at: now, kind: "delivered", clientId: e.clientId, title: `Livré : ${name ?? "client"}`, sub: `Livraison ${truck + 1} · ${e.at}` }, true),
      };
    }
    case "deliveries_done":
      return { ...s, phase: "delivered", seq: s.seq + 1, toast: { id: s.seq + 1, kind: "delivered", title: "Tout est livré !", sub: `${e.count} arrêts, toutes les livraisons sont rentrées.` } };
  }
  return s;
}

const urlCampaign = () => new URLSearchParams(location.search).get("campaign") ?? undefined;
const setUrlCampaign = (id?: string) => {
  const u = new URL(location.href);
  if (id) u.searchParams.set("campaign", id);
  else u.searchParams.delete("campaign");
  history.replaceState(history.state, "", u);
};

export function useCampaign(forecast: Forecast | undefined) {
  const [state, dispatch] = useReducer(reduce, INIT);
  const abort = useRef<AbortController | null>(null);
  const es = useRef<EventSource | null>(null);
  const stop = () => { abort.current?.abort(); es.current?.close(); es.current = null; };
  useEffect(() => stop, []);

  const emit = useCallback((e: CampaignEvent) => dispatch({ type: "event", e }), []);
  useEffect(() => {
    if (forecast) dispatch({ type: "names", names: Object.fromEntries(forecast.clients.map((c) => [c.clientId, c.name])) });
  }, [forecast]);

  const attach = useCallback(async (id: string, signal: AbortSignal) => {
    const snap = await getJson<{ calls: CampaignCall[] }>(`${AI_BASE}/campaigns/${id}`, { signal });
    // start from a clean slate: the stream replays every event since the start
    dispatch({ type: "start", source: "live", id, calls: snap.calls.map((c) => ({ ...c, status: "queued", transcript: [], decision: null, totalHt: 0 })) });
    setUrlCampaign(id);
    const src = new EventSource(`${AI_BASE}/campaigns/${id}/stream`);
    es.current = src;
    // one render per batch, not per event: the replay of a running campaign is ~300 events at once
    let queue: CampaignEvent[] = [];
    let timer = 0;
    src.onmessage = (m) => {
      try { queue.push(JSON.parse(m.data)); } catch { return; /* ping */ }
      if (!timer) timer = window.setTimeout(() => { timer = 0; const events = queue; queue = []; dispatch({ type: "batch", events }); }, 50);
    };
  }, []);

  // Reconnect to the campaign in the URL (reload during a run).
  useEffect(() => {
    const id = urlCampaign();
    if (!id || !forecast || (forecast as { offline?: boolean }).offline) return;
    const ctl = new AbortController();
    abort.current = ctl;
    attach(id, ctl.signal).catch(() => setUrlCampaign(undefined));
    return () => ctl.abort();
  }, [forecast, attach]);

  const start = useCallback(async () => {
    if (!forecast) return;
    stop();
    const ctl = new AbortController();
    abort.current = ctl;
    dispatch({ type: "reset" });
    dispatch({ type: "phase", phase: "starting" });
    const queued = (rows: Forecast["clients"]): CampaignCall[] =>
      rows.map((c, i) => ({
        callId: `call_${String(i + 1).padStart(2, "0")}`, clientId: c.clientId, clientName: c.name, lat: c.lat, lng: c.lng,
        kind: c.status as "predicted" | "upsell", priority: c.priority, tier: c.loyalty?.tier, status: "queued", transcript: [], totalHt: 0,
      }));
    try {
      if ((forecast as { offline?: boolean }).offline) throw new Error("offline forecast");
      const { id } = await getJson<{ id: string }>(`${AI_BASE}/campaigns`, {
        method: "POST", body: JSON.stringify({ date: forecast.date, clientIds: null, concurrency: 8 }), signal: ctl.signal,
      });
      await attach(id, ctl.signal);
    } catch {
      if (ctl.signal.aborted) return;
      dispatch({ type: "start", source: "demo", calls: queued(callQueue(forecast)) });
      runMockCampaign(forecast, emit, ctl.signal).catch(() => {});
    }
  }, [forecast, emit, attach]);

  const deliver = useCallback(async () => {
    if (!state.route) return;
    dispatch({ type: "phase", phase: "delivering" });
    if (state.source === "live" && state.id) {
      try {
        await getJson(`${AI_BASE}/campaigns/${state.id}/deliveries`, { method: "POST", body: JSON.stringify({ speed: DELIVERY_SPEED }) });
        return;
      } catch { /* fall through to the simulator */ }
    }
    const ctl = new AbortController();
    abort.current = ctl;
    runMockDeliveries(state.route, emit, ctl.signal, DELIVERY_SPEED).catch(() => {});
  }, [state.route, state.source, state.id, emit]);

  const reset = useCallback(() => { stop(); setUrlCampaign(undefined); dispatch({ type: "reset" }); }, []);

  return { state, start, deliver, reset };
}

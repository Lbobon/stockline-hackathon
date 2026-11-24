/**
 * Offline campaign: plays the same events as GET /ai/campaigns/{id}/stream (call_status, line, decision, order, route,
 * totals, done, delivered) from the forecast alone, so the radar demos without the AI server. Deterministic per client.
 */
import { callQueue, type CampaignEvent, type CampaignTotals, type Forecast, type ForecastClient, type ForecastLine, type RoutePlan, type RouteStop, type Truck } from "./types";

const hash = (s: string) => [...s].reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 7);
const sleep = (ms: number, signal: AbortSignal) =>
  new Promise<void>((res, rej) => {
    const t = setTimeout(res, ms);
    signal.addEventListener("abort", () => { clearTimeout(t); rej(new DOMException("aborted", "AbortError")); }, { once: true });
  });

const qty = (l: ForecastLine) => {
  const n = Number.isInteger(l.qty) ? l.qty : Math.round(l.qty);
  const u = l.unit === "kg" ? " kilos" : l.unit === "box" ? " cartons" : "";
  return `${n}${u}`;
};
const short = (name: string) => name.split(/[(,]/)[0].trim().toLowerCase();

function script(c: ForecastClient, f: Forecast): { lines: { who: "agent" | "client"; text: string }[]; label: "oui" | "modifie" | "non" | "rappeler" } {
  const h = hash(c.clientId);
  const first = (c.contactName ?? "").split(" ")[0] || "bonjour";
  const ev = f.events.find((e) => c.eventIds?.includes(e.id));
  const boosted = (c.status === "upsell" ? c.addLines : c.lines)?.filter((l) => l.eventId) ?? [];
  const main = [...c.lines].sort((a, b) => b.qty * (b.unitPrice ?? 1) - a.qty * (a.unitPrice ?? 1))[0];
  const add = boosted[0] ?? main;
  const r = h % 100;
  const label = r < 68 ? "oui" : r < 80 ? "modifie" : r < 90 ? "non" : "rappeler";
  const open =
    c.status === "upsell"
      ? `Bonjour ${first}, c'est Leïla de chez Medina. Votre commande de mercredi est bien notée. ${ev ? `Avec ${ev.title.toLowerCase()}, ` : ""}je vous rajoute ${qty(add)} de ${short(add.productName)} ?`
      : `Bonjour ${first}, c'est Leïla de chez Medina. Je vous prépare comme d'habitude pour mercredi : ${qty(main)} de ${short(main.productName)}${add !== main ? `, et ${qty(add)} de ${short(add.productName)} ${ev?.pitch ?? ""}` : ""} ?`;
  const reply = {
    oui: ["Ah oui, bonne idée, mettez-moi ça.", "Oui, parfait, comme d'habitude.", "Vas-y, envoie, c'est bon pour moi.", "Oui oui, avec le match ça va partir vite."][h % 4],
    modifie: [`Mettez-en un peu moins sur le ${short(main.productName)}, le reste c'est bon.`, "Oui mais rajoute deux cartons de frites."][h % 2],
    non: ["Non merci, on est fermés mercredi.", "Non, j'ai encore du stock, pas cette semaine."][h % 2],
    rappeler: ["Là je suis en plein service, rappelez-moi vers 15 h.", "Je dois voir avec mon associé, rappelez demain."][h % 2],
  }[label];
  const close = {
    oui: "C'est noté, livraison mercredi matin. Bonne journée !",
    modifie: "Très bien, j'ajuste la commande. Livraison mercredi matin.",
    non: "Pas de souci, bonne semaine à vous !",
    rappeler: "Entendu, je vous rappelle. Bon service !",
  }[label];
  return { lines: [{ who: "agent", text: open }, { who: "client", text: reply }, { who: "agent", text: close }], label };
}

/** Three trucks, clients split by bearing from the depot, nearest-neighbour order, 18 min a stop + 2 min/km. */
export function planRoutes(f: Forecast, stops: { clientId: string; orderId: string; lat: number; lng: number }[]): RoutePlan {
  const d = f.depot;
  const km = (a: { lat: number; lng: number }, b: { lat: number; lng: number }) =>
    Math.hypot((a.lat - b.lat) * 111, (a.lng - b.lng) * 111 * Math.cos((d.lat * Math.PI) / 180));
  const bearing = (p: { lat: number; lng: number }) => Math.atan2(p.lat - d.lat, p.lng - d.lng);
  const sorted = [...stops].sort((a, b) => bearing(a) - bearing(b));
  const n = Math.min(3, Math.max(1, Math.ceil(sorted.length / 4)));
  const names = [["Nord", "Karim"], ["Centre", "Sofiane"], ["Est", "Mehdi"]];
  const trucks: Truck[] = [];
  for (let t = 0; t < n; t++) {
    const group = sorted.slice(Math.round((t * sorted.length) / n), Math.round(((t + 1) * sorted.length) / n));
    const order: typeof group = [];
    let at: { lat: number; lng: number } = d;
    const left = [...group];
    while (left.length) {
      left.sort((a, b) => km(at, a) - km(at, b));
      const next = left.shift()!;
      order.push(next);
      at = next;
    }
    // 2-opt: reverse any stretch whose ends cross, until the loop has no crossing left (nearest-neighbour alone zigzags)
    const loop = [d, ...order, d];
    for (let improved = true, pass = 0; improved && pass < 50; pass++) {
      improved = false;
      for (let i = 1; i < loop.length - 2; i++)
        for (let j = i + 1; j < loop.length - 1; j++)
          if (km(loop[i - 1], loop[j]) + km(loop[i], loop[j + 1]) < km(loop[i - 1], loop[i]) + km(loop[j], loop[j + 1]) - 1e-9) {
            loop.splice(i, j - i + 1, ...loop.slice(i, j + 1).reverse());
            improved = true;
          }
    }
    order.splice(0, order.length, ...(loop.slice(1, -1) as typeof group));
    let dist = 0;
    for (let i = 1; i < loop.length; i++) dist += km(loop[i - 1], loop[i]);
    let mins = 5 * 60 + 30;
    let prev: { lat: number; lng: number } = d;
    const rs: RouteStop[] = order.map((s) => {
      mins += km(prev, s) * 2 + 18;
      prev = s;
      return { ...s, eta: `${String(Math.floor(mins / 60)).padStart(2, "0")}:${String(Math.round(mins % 60)).padStart(2, "0")}` };
    });
    trucks.push({
      runId: `mock_${t}`, route: `Tournée ${names[t][0]}`, driver: names[t][1], departure: "05:30", km: Math.round(dist * 1.3 * 10) / 10,
      hours: Math.round(((mins - 330) / 60) * 10) / 10, lastEta: rs.at(-1)?.eta ?? "05:30", loadKg: 0, capacityKg: 3500, stops: rs,
    });
  }
  const after = trucks.reduce((s, t) => s + t.km, 0);
  return { date: f.date, depot: d, optimized: trucks, totals: { before: { km: after * 1.18 }, after: { km: after }, savedKm: Math.round(after * 0.18), savedEur: Math.round(after * 0.18 * 1.1) } };
}

export async function runMockCampaign(f: Forecast, emit: (e: CampaignEvent) => void, signal: AbortSignal, concurrency = 6) {
  // Same ids and order as the queued list in useCampaign: loyal customers first (callQueue).
  const rows = callQueue(f);
  const ids = new Map(rows.map((c, i) => [c.clientId, `call_${String(i + 1).padStart(2, "0")}`]));
  const stops = f.clients.filter((c) => c.status === "ordered").map((c) => ({ clientId: c.clientId, orderId: c.existingOrderId ?? `ord_${c.clientId}`, lat: c.lat, lng: c.lng }));
  const totals: CampaignTotals = { called: 0, confirmed: 0, declined: 0, callback: 0, revenueHt: 0, upliftHt: 0 };
  emit({ type: "route", plan: planRoutes(f, stops) });
  let routeTimer: ReturnType<typeof setTimeout> | undefined;
  const pushRoute = () => { clearTimeout(routeTimer); routeTimer = setTimeout(() => emit({ type: "route", plan: planRoutes(f, stops) }), 500); };

  const one = async (c: ForecastClient) => {
    const callId = ids.get(c.clientId)!;
    const h = hash(c.clientId);
    emit({ type: "call_status", callId, clientId: c.clientId, status: "ringing" });
    await sleep(700 + (h % 1100), signal);
    emit({ type: "call_status", callId, clientId: c.clientId, status: "talking" });
    totals.called++;
    emit({ type: "totals", ...totals });
    const { lines, label } = script(c, f);
    for (const [i, ln] of lines.entries()) {
      await sleep(ln.who === "agent" ? 1300 + (h % 700) : 1000 + ((h >> 3) % 900), signal);
      emit({ type: "line", callId, ...ln });
      if (i === 1) {
        emit({ type: "decision", callId, label, confidence: 0.82 + ((h >> 5) % 17) / 100, source: "jev" });
        if (label === "oui" || label === "modifie") {
          const value = c.status === "upsell" ? c.upliftHt : c.totalHt * (label === "modifie" ? 0.93 : 1);
          const orderId = c.existingOrderId ?? `ord_r${h % 100000}`;
          emit({ type: "order", callId, clientId: c.clientId, orderId, totalHt: Math.round(value * 100) / 100, lines: [] });
          totals.revenueHt = Math.round((totals.revenueHt + value) * 100) / 100;
          totals.upliftHt = Math.round((totals.upliftHt + c.upliftHt) * 100) / 100;
          if (c.status !== "upsell") stops.push({ clientId: c.clientId, orderId, lat: c.lat, lng: c.lng });
          pushRoute();
        }
      }
    }
    const final = label === "oui" ? "confirmed" : label === "modifie" ? "modified" : label === "non" ? "declined" : "callback";
    if (final === "confirmed" || final === "modified") totals.confirmed++;
    else if (final === "declined") totals.declined++;
    else totals.callback++;
    emit({ type: "call_status", callId, clientId: c.clientId, status: final });
    emit({ type: "totals", ...totals });
  };

  const queue = [...rows];
  await Promise.all(
    Array.from({ length: concurrency }, async (_, w) => {
      await sleep(w * 450, signal);
      while (queue.length) await one(queue.shift()!);
    }),
  );
  await sleep(700, signal);
  clearTimeout(routeTimer);
  const plan = planRoutes(f, stops);
  emit({ type: "route", plan });
  emit({ type: "done", totals });
  return plan;
}

export async function runMockDeliveries(plan: RoutePlan, emit: (e: CampaignEvent) => void, signal: AbortSignal, speed = 25) {
  const stops = plan.optimized.flatMap((t) => t.stops).sort((a, b) => a.eta.localeCompare(b.eta));
  const mins = (s: string) => Number(s.slice(0, 2)) * 60 + Number(s.slice(3));
  let prev = stops[0]?.eta;
  for (const s of stops) {
    await sleep(Math.max(650, ((mins(s.eta) - mins(prev)) / speed) * 1000), signal);
    prev = s.eta;
    emit({ type: "delivered", orderId: s.orderId, clientId: s.clientId, at: s.eta });
  }
  emit({ type: "deliveries_done", count: stops.length });
}

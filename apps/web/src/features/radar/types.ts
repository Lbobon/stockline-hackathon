/**
 * Radar de demande: the shapes of GET /api/radar/forecast, GET /api/routing/optimize and the /ai/campaigns stream
 * (spec: .plans/demand-radar-spec.md). Local to the radar until the backend ships them in @kit/shared.
 */

export type EventKind = "sport" | "weather" | "wedding" | "local";
export type ForecastStatus = "ordered" | "upsell" | "predicted";

export interface LatLng { lat: number; lng: number }

export interface RadarEvent {
  id: string;
  kind: EventKind;
  date: string;
  time?: string | null;
  title: string;
  affects?: { from: string; to: string };
  segments?: string[];
  boosts: { productId: string; factor: number; productName: string }[];
  special?: { productId: string; qty: number; note: string; productName: string }[];
  pitch?: string;
}

export interface ForecastLine {
  productId: string;
  productName: string;
  unit: string;
  qty: number;
  baseQty: number;
  unitPrice?: number;
  eventId?: string | null;
  note?: string;
}

export type LoyaltyTier = "fidele" | "regulier" | "occasionnel";

/** How loyal the client is, from their delivered orders (radar.loyalty). */
export interface Loyalty {
  score: number;
  tier: LoyaltyTier;
  label: string;
  weekdayHits: number; // same weekdays with a delivery, out of the last 12
  weeksActive: number; // weeks with an order, out of the last 13
  ordersRecent: number;
  revenueRecentHt: number;
  tenureMonths: number;
}

/** The cheapest place for the client's stop in the day's trucks (radar.route_fit). */
export interface RouteFit {
  route: string | null;
  onRoute: boolean;
  feasible: boolean;
  detourKm: number;
  detourMin: number;
  costEur: number;
  after: string | null; // the stop (or the depot) it comes right after
}

export interface ForecastClient extends LatLng {
  clientId: string;
  name: string;
  type: string;
  city: string;
  contactName?: string;
  phone?: string;
  status: ForecastStatus;
  probability: number;
  existingOrderId?: string | null;
  lines: ForecastLine[];
  addLines?: ForecastLine[];
  totalHt: number;
  upliftHt: number;
  reason: string;
  eventIds?: string[];
  specialRequirements?: { productId?: string; qty?: number; note: string; productName?: string }[];
  // who to call first (radar.prioritize): loyal customers first, then by expected € net
  loyalty?: Loyalty;
  routeFit?: RouteFit;
  marginHt?: number;
  expectedNetEur?: number;
  call?: boolean; // false: not worth a delivery, dock pickup suggested instead
  skipReason?: string | null;
  priority?: number;
  wave?: number;
}

export interface Forecast {
  date: string;
  depot: LatLng & { name: string };
  events: RadarEvent[];
  clients: ForecastClient[];
  summary: { ordered: number; predicted: number; upsell: number; expectedHt: number; upliftHt: number;
    toCall?: number; skipped?: number; loyal?: number; expectedNetEur?: number };
}

export interface RouteStop extends LatLng { orderId: string; clientId: string; eta: string }
export interface Truck {
  runId: string;
  route: string;
  driver: string;
  departure: string;
  km: number;
  hours: number;
  lastEta: string;
  loadKg: number;
  capacityKg: number;
  stops: RouteStop[];
}
export interface RoutePlan {
  date: string;
  depot: LatLng;
  optimized: Truck[];
  current?: Truck[];
  totals?: { before: { km: number }; after: { km: number }; savedKm: number; savedEur: number };
}

export type CallStatus = "queued" | "ringing" | "talking" | "confirmed" | "modified" | "declined" | "callback" | "failed";
export type DecisionLabel = "oui" | "modifie" | "non" | "rappeler";

export interface CampaignCall extends LatLng {
  callId: string;
  clientId: string;
  clientName: string;
  kind?: "predicted" | "upsell";
  priority?: number | null;
  tier?: LoyaltyTier | null;
  status: CallStatus;
  transcript: { who: "agent" | "client"; text: string }[];
  decision?: { label: DecisionLabel; confidence: number; source: string } | null;
  orderId?: string | null;
  totalHt: number;
  lines?: unknown[];
  delivered?: string; // ETA it was delivered at (delivery replay)
}

export interface CampaignTotals { called: number; confirmed: number; declined: number; callback: number; revenueHt: number; upliftHt: number }

export type CampaignEvent =
  | { type: "call_status"; callId: string; clientId: string; status: CallStatus }
  | { type: "line"; callId: string; who: "agent" | "client"; text: string }
  | { type: "decision"; callId: string; label: DecisionLabel; confidence: number; source: string }
  | { type: "order"; callId: string; clientId: string; orderId: string; totalHt: number; lines: unknown[]; status?: string }
  | { type: "route"; plan: RoutePlan }
  | ({ type: "totals" } & CampaignTotals)
  | { type: "done"; totals: CampaignTotals }
  | { type: "delivered"; orderId: string; clientId: string; at: string }
  | { type: "deliveries_done"; count: number };

/** The clients the campaign calls, in call order (the server's priority; older forecasts: by expected uplift). */
export const callQueue = (f: Forecast) =>
  f.clients.filter((c) => c.status !== "ordered" && c.call !== false)
    .sort((a, b) => (a.priority ?? 1e9) - (b.priority ?? 1e9) || b.upliftHt - a.upliftHt);
export const notCalled = (f: Forecast) => f.clients.filter((c) => c.status !== "ordered" && c.call === false);

export const TERMINAL: CallStatus[] = ["confirmed", "modified", "declined", "callback", "failed"];
export const isYes = (s: CallStatus) => s === "confirmed" || s === "modified";

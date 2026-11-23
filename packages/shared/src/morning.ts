/**
 * "Ce matin" contracts: shortage triage, drafts to check, retention gestures.
 * Mirrors apps/backend/app/schemas/shortages.py and smart.py (camelCase on the wire; absent fields are omitted).
 *
 *  GET  /api/shortages/today                    → ShortageTriage (orders sorted: today's truck first, then calls)
 *  POST /api/shortages/:orderId/substitute      body SubstituteInput → OrderDetail (400 when no substitute in stock)
 *  GET  /api/gates/review                       → GateReviewItem[] (drafts to check, with plain-French reasons)
 *  GET  /api/retention/:clientId?basisHt=&costHt= → RetentionOffer
 */
import type { Id, IsoDate } from "./domain";

export interface ShortProduct {
  productId: Id;
  name: string;
  unit: string;
  price: number;
  available: number;
}

export interface ShortProductWithSub extends ShortProduct {
  substitute?: ShortProduct;
}

export interface ShortLine extends ShortProduct {
  lineId: Id;
  qty: number;
  missingQty: number;
  substitute?: ShortProduct; // absent when no usual substitute has enough stock
}

export type SubstitutionPreference = "refuses" | "accepts" | "unknown";
export type ShortageDecision = "call" | "auto_substitute";

export interface ShortageOrder {
  orderId: Id;
  orderNumber: string;
  status: string;
  deliveryDate: IsoDate;
  leavesToday: boolean;
  clientId: Id;
  clientName: string;
  contactName?: string;
  phone?: string;
  preference: SubstitutionPreference;
  decision: ShortageDecision;
  reason: string; // plain French, shown as is
  lines: ShortLine[];
}

export interface ShortageTriage {
  date: IsoDate;
  products: ShortProductWithSub[];
  orders: ShortageOrder[];
  counts: { orders: number; calls: number; auto: number };
}

export interface SubstituteInput {
  note?: string; // outcome of the call, stored on the client event
}

export type GateReasonCode =
  | "client_blocked"
  | "credit_limit"
  | "too_late"
  | "recap_not_confirmed"
  | "unusual_qty"
  | "new_product"
  | "special_cut_late"
  | "below_minimum" // under the 120 € HT delivery minimum: complete it, or pickup at the dock
  | "short_stock"; // stock + incoming deliveries can't cover the quantity

export interface GateReason {
  code: GateReasonCode;
  message: string; // plain French, shown as is
  productId?: Id;
}

export interface GateReviewItem {
  orderId: Id;
  orderNumber: string;
  clientId: Id;
  clientName: string;
  deliveryDate: IsoDate;
  totalHt: number;
  createdBy?: string;
  decision: "confirm" | "draft";
  reasons: GateReason[];
}

export interface RetentionOffer {
  clientId: Id;
  clientName?: string;
  kind: "money" | "service";
  status: "granted" | "needs_approval" | "service_only"; // needs_approval: over 150 € HT, waits for Rachid
  pct: number;
  amountHt: number;
  offers: string[];
  explanation: string; // plain French, shown as is
  factors: Record<string, unknown>;
}

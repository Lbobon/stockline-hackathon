/** Call reports written by the Rapporteur (AI service: GET /ai/calls, GET /ai/calls/:id, app/orchestra/reporter.py). */
import type { GuardSummary } from "@kit/shared";
import { useQuery } from "@tanstack/react-query";
import { AI_BASE } from "@/lib/api";

export type CallAgent = "voice" | "scout" | "fast" | "scribe" | "linguist" | "guard" | "reporter";

export interface CallTurn {
  n: number;
  caller: string;
  agent?: string;
  path: "llm" | "fast";
  kind?: string;
  speechStartMs?: number;
  speechEndMs?: number;
  settleMs?: number;
  firstSoundMs?: number;
  firstWordMs?: number;
  doneMs?: number;
  audioMs?: number;
  filler?: string;
  tools: string[];
  prefetched: string[];
  interrupted?: boolean;
  error?: string;
}

export interface CallSpan { agent: CallAgent | "warmup"; label: string; task: string; detail: string; turn: number; startMs: number; endMs: number | null; ok: boolean }
export interface CallToolCall { agent: CallAgent; name: string; args: Record<string, unknown>; ok: boolean; ms: number; result: unknown; turn: number }
export interface CallOrder {
  id: string; number?: string; status: string; client?: string; deliveryDate?: string; totalHt?: number;
  lines: { lineId: string; productId: string; productName: string; qty: number; unit: string; unitPrice?: number }[];
  warnings: { code: string; message: string }[];
}
export interface CallAi {
  headline?: string;
  summary?: string;
  outcome?: string;
  sentiment?: { start?: string; end?: string; comment?: string };
  for_leila?: { priority: string; action: string; why: string }[];
  risks?: string[];
  opportunities?: string[];
  agent_review?: { score?: number; good?: string[]; improve?: string[] };
  moments?: { turn: number; label: string }[];
  tags?: string[];
}
export interface CallMetrics {
  turns: number; fastTurns: number; prefetchedFacts: number; scoutToolCalls: number; voiceToolCalls: number;
  firstSoundP50: number | null; firstSoundP90: number | null; firstWordP50: number | null; firstWordP90: number | null; cost: number;
}
export interface CallReport {
  id: string;
  startedAt: string;
  durationMs: number;
  actor?: string;
  client?: { id: string; name?: string } | null;
  language: string;
  languageName: string;
  register: "tu" | "vous";
  turns: CallTurn[];
  spans: CallSpan[];
  toolCalls: CallToolCall[];
  orders: CallOrder[];
  writes: { agent: CallAgent; name: string; args: Record<string, unknown>; result: unknown }[];
  usage: Record<string, { model: string; label: string; calls: number; cost: number; input_tokens?: number; output_tokens?: number; cache_read_input_tokens?: number }>;
  metrics: CallMetrics;
  ai: CallAi | null;
  reportModel?: string;
  source?: "voice" | "sim";
  scenario?: string;
  guard?: GuardSummary; // le Garde-fou: sentences Jev checked against the ERP during the call
}
export interface CallSummary {
  id: string; startedAt: string; durationMs: number; client?: { id: string; name?: string } | null; language: string;
  headline?: string; outcome?: string; sentiment?: string; actions: number; metrics?: CallMetrics; source: "voice" | "sim";
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${AI_BASE}${path}`);
  if (!res.ok) throw new Error(`${res.status}`);
  return res.json();
}

export const useCalls = () => useQuery({ queryKey: ["ai", "calls"], queryFn: () => get<CallSummary[]>("/calls"), refetchInterval: 10_000 });
export const useCall = (id?: string) => useQuery({ queryKey: ["ai", "calls", id], queryFn: () => get<CallReport>(`/calls/${id}`), enabled: !!id });

export const OUTCOME: Record<string, { label: string; tone: "success" | "violet" | "warning" | "destructive" | "muted" | "info" }> = {
  order_confirmed: { label: "Commande confirmée", tone: "success" },
  order_draft: { label: "Pré-commande", tone: "warning" },
  order_changed: { label: "Commande modifiée", tone: "success" },
  info_given: { label: "Renseignement", tone: "info" },
  complaint_logged: { label: "Réclamation notée", tone: "destructive" },
  escalated: { label: "Transmis à l'équipe", tone: "warning" },
  no_action: { label: "Sans suite", tone: "muted" },
};

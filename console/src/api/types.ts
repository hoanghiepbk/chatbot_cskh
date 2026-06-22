// Shapes returned by the agent staff API (TIP-008 + TIP-014 read endpoints).
// Every field here is MASKED/aggregate — no raw phone is ever transported; the
// real number is reachable only via the audited reveal_contact endpoint.

export type ConversationRow = {
  id: string;
  display_name: string | null;
  mode: "agent" | "human";
  message_count: number;
  last_intent: string | null;
  escalated: boolean;
  started_at: string;
  closed_at: string | null;
  resolution: string | null;
};

export type TraceEvent = {
  id: string;
  step_type: string;
  payload: Record<string, unknown> | null;
  latency_ms: number | null;
  cost_usd: number | string | null;
  prompt_version: number | null;
  policy_version: number | null;
  created_at: string;
};

export type TraceResponse = {
  events: TraceEvent[];
  summary: {
    event_count: number;
    total_cost_usd: number;
    total_latency_ms: number;
    llm_calls: number;
    escalated: boolean;
  };
};

export type Metrics = {
  totals: { conversations: number; resolved: number; escalated: number };
  resolution_rate: number;
  escalation_rate: number;
  avg_cost_usd: number;
  latency_ms: { p50: number; p95: number };
  cache_hit_rate: number | null;
  cache_savings_usd: number;
  faq_turns: number;
  intent_distribution: { intent: string; count: number }[];
  escalation_reasons: { reason: string; count: number }[];
  cost_by_day: { date: string; cost_usd: number }[];
  window: { conversations_scanned: number; trace_events_scanned: number };
};

export type EvalRun = {
  id: string;
  git_sha: string | null;
  prompt_version: number | null;
  suite: string | null;
  total: number | null;
  passed: number | null;
  metrics: Record<string, unknown> | null;
  created_at: string;
};

export type TicketPayload = {
  reason?: string;
  summary?: string;
  suggested_action?: string;
  customer?: { display_name?: string | null; vehicles?: unknown[] };
  recent_messages?: { sender: string; content_masked: string }[];
  intents?: string[];
  tool_calls?: string[];
  location?: string;
  callback_placeholder?: string;
};

export type Ticket = {
  id: string;
  conversation_id: string | null;
  type: "booking" | "rescue" | "complaint" | "after_hours";
  priority: "normal" | "high" | "urgent";
  status: "open" | "claimed" | "resolved" | "cancelled";
  payload: TicketPayload | null;
  created_at: string;
};

export type PublicMessage = {
  id: string;
  conversation_id: string;
  sender: "customer" | "agent" | "staff";
  content_masked: string | null;
  created_at: string;
};

export type GapCluster = {
  representative_query: string;
  count: number;
  last_seen: string;
  sample_queries: string[];
};

export type KnowledgeGaps = { clusters: GapCluster[]; total_events: number };

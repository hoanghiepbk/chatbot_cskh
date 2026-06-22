import { Space, Tag, Timeline } from "antd";
import type { CSSProperties, ReactNode } from "react";
import type { TraceEvent } from "../api/types";
import { fmtMs, fmtTime, fmtUsd, shortId } from "../lib/format";
import { intentLabel, reasonLabel } from "../lib/intents";
import { MONO, SEMANTIC } from "../theme";
import { PlainText } from "./PlainText";

// SIGNATURE of the console: a vertical, verdict-colored timeline of agent steps.
// Each node = one trace_event; latency/cost render in monospace on the right;
// guardrail verdicts tint the dot (pass green / rewrite amber / block red).

const STEP_META: Record<string, { label: string; color: string }> = {
  guardrail_in: { label: "Guardrail vào", color: "blue" },
  router: { label: "Định tuyến", color: "geekblue" },
  retrieval: { label: "Truy hồi KB", color: "cyan" },
  tool_call: { label: "Gọi công cụ", color: "purple" },
  llm_call: { label: "Gọi LLM", color: "blue" },
  guardrail_out: { label: "Guardrail ra", color: "green" },
  escalation: { label: "Chuyển nhân viên", color: "volcano" },
  cache_hit: { label: "Cache hit", color: "green" },
};

function Mono({
  children,
  dim,
  style,
}: {
  children: ReactNode;
  dim?: boolean;
  style?: CSSProperties;
}) {
  return (
    <span
      style={{
        fontFamily: MONO,
        fontSize: 12.5,
        color: dim ? SEMANTIC.muted : SEMANTIC.ink,
        ...style,
      }}
    >
      {children}
    </span>
  );
}

function dotColor(ev: TraceEvent): string {
  const p = (ev.payload ?? {}) as Record<string, unknown>;
  if (ev.step_type === "guardrail_out" || ev.step_type === "guardrail_in") {
    if (p.verdict === "block") return SEMANTIC.fail;
    if (p.verdict === "rewrite") return SEMANTIC.warn;
    if (p.verdict === "pass") return SEMANTIC.pass;
  }
  if (ev.step_type === "escalation") return SEMANTIC.warn;
  if (ev.step_type === "tool_call" && p.refused) return SEMANTIC.fail;
  return SEMANTIC.accent;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function PayloadSummary({ ev }: { ev: TraceEvent }) {
  const p = (ev.payload ?? {}) as Record<string, any>;
  switch (ev.step_type) {
    case "router":
      return (
        <Space size={6} wrap>
          <Tag color="geekblue" style={{ marginInlineEnd: 0 }}>
            {intentLabel(p.intent)}
          </Tag>
          <Mono>conf {Number(p.confidence ?? 0).toFixed(2)}</Mono>
          {p.engine ? <Mono dim>· {String(p.engine)}</Mono> : null}
        </Space>
      );
    case "retrieval": {
      const ids: unknown[] = Array.isArray(p.chunk_ids) ? p.chunk_ids : [];
      const scores: number[] = Array.isArray(p.scores) ? p.scores : [];
      return (
        <Space size={6} wrap>
          <span>{ids.length} đoạn KB</span>
          {scores.length ? <Mono dim>top {Math.max(...scores).toFixed(2)}</Mono> : null}
        </Space>
      );
    }
    case "tool_call":
      return (
        <Space size={6} wrap>
          <Tag color="purple" style={{ marginInlineEnd: 0 }}>
            {String(p.tool ?? "tool")}
          </Tag>
          {p.refused ? (
            <Tag color="red">từ chối: {String(p.refused)}</Tag>
          ) : (
            <Tag color="green">ok</Tag>
          )}
        </Space>
      );
    case "llm_call":
      return (
        <Space size={6} wrap>
          <Mono>{String(p.model ?? "")}</Mono>
          {p.purpose ? <Tag style={{ marginInlineEnd: 0 }}>{String(p.purpose)}</Tag> : null}
          {p.input_tokens != null ? (
            <Mono dim>
              {Number(p.input_tokens)}→{Number(p.output_tokens)} tok
            </Mono>
          ) : null}
        </Space>
      );
    case "guardrail_in":
      return (
        <Space size={6} wrap>
          {p.emergency ? <Tag color="red">khẩn cấp</Tag> : null}
          {p.injection_score != null ? (
            <Mono dim>injection {Number(p.injection_score).toFixed(2)}</Mono>
          ) : null}
          {p.pii_found ? <Tag color="gold">PII đã ẩn</Tag> : null}
        </Space>
      );
    case "guardrail_out": {
      const verdict = String(p.verdict ?? "—");
      const rules: string[] = Array.isArray(p.rules_hit) ? p.rules_hit : [];
      const reasons: string[] = Array.isArray(p.reasons) ? p.reasons : [];
      return (
        <Space size={6} wrap>
          <Tag color={verdict === "pass" ? "green" : verdict === "rewrite" ? "orange" : "red"}>
            {verdict}
          </Tag>
          {rules.map((r) => (
            <Tag key={r} color="volcano">
              {r}
            </Tag>
          ))}
          {reasons.length ? (
            <PlainText text={reasons.join("; ")} style={{ color: SEMANTIC.muted }} />
          ) : null}
        </Space>
      );
    }
    case "escalation":
      return (
        <Space size={6} wrap>
          <Tag color="volcano" style={{ marginInlineEnd: 0 }}>
            {reasonLabel(p.reason ?? p.step)}
          </Tag>
          {p.ticket_id ? <Mono dim>ticket {shortId(String(p.ticket_id))}</Mono> : null}
        </Space>
      );
    default:
      return (
        <PlainText
          text={JSON.stringify(p)}
          style={{ color: SEMANTIC.muted, fontSize: 12 }}
        />
      );
  }
}

export function TraceTimeline({ events }: { events: TraceEvent[] }) {
  const items = events.map((ev) => {
    const meta = STEP_META[ev.step_type] ?? { label: ev.step_type, color: "default" };
    return {
      color: dotColor(ev),
      children: (
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            gap: 16,
            alignItems: "baseline",
          }}
        >
          <div style={{ minWidth: 0 }}>
            <Space size={8} align="center" style={{ marginBottom: 4 }}>
              <Tag color={meta.color} style={{ marginInlineEnd: 0 }}>
                {meta.label}
              </Tag>
              {(ev.prompt_version != null || ev.policy_version != null) && (
                <Mono dim style={{ fontSize: 11 }}>
                  prompt v{ev.prompt_version ?? "—"} · policy v{ev.policy_version ?? "—"}
                </Mono>
              )}
            </Space>
            <div>
              <PayloadSummary ev={ev} />
            </div>
          </div>
          <div style={{ textAlign: "right", whiteSpace: "nowrap" }}>
            {ev.latency_ms != null && (
              <div>
                <Mono>{fmtMs(ev.latency_ms)}</Mono>
              </div>
            )}
            {ev.cost_usd != null && (
              <div>
                <Mono dim>{fmtUsd(ev.cost_usd)}</Mono>
              </div>
            )}
            <div>
              <Mono dim style={{ fontSize: 11 }}>
                {fmtTime(ev.created_at)}
              </Mono>
            </div>
          </div>
        </div>
      ),
    };
  });

  return <Timeline items={items} />;
}

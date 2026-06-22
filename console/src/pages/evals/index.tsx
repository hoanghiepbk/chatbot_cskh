import { Alert, Badge, Card, Modal, Space, Spin, Table, Tag, Typography } from "antd";
import { useMemo, useState } from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useApi } from "../../api/hooks";
import type { EvalRun } from "../../api/types";
import { fmtTime } from "../../lib/format";
import { MONO, SEMANTIC } from "../../theme";

const SUITES: { key: string; label: string; color: string }[] = [
  { key: "golden", label: "Golden", color: SEMANTIC.accent },
  { key: "adversarial_critical", label: "Critical", color: SEMANTIC.fail },
  { key: "adversarial_quality", label: "Quality", color: SEMANTIC.warn },
];

const SUITE_LABEL: Record<string, string> = {
  golden: "Golden",
  adversarial_critical: "Critical",
  adversarial_quality: "Quality",
};

function passRate(r: EvalRun): number | null {
  return r.total ? Math.round(((r.passed ?? 0) / r.total) * 100) : null;
}

// Screen 3 — Eval Dashboard: pass-rate trend split by suite + Critical badge.
export function EvalDashboard() {
  const { data, loading, error } = useApi<{ eval_runs: EvalRun[] }>("/staff/eval-runs?limit=100");
  const [selected, setSelected] = useState<EvalRun | null>(null);

  const runs = data?.eval_runs ?? [];

  const chartData = useMemo(() => {
    const asc = [...runs].sort((a, b) => a.created_at.localeCompare(b.created_at));
    return asc.map((r) => ({
      label: (r.git_sha ?? "—").slice(0, 7),
      [r.suite ?? "other"]: passRate(r),
    }));
  }, [runs]);

  // Latest Critical run drives the headline badge.
  const latestCritical = useMemo(() => {
    const crit = runs
      .filter((r) => r.suite === "adversarial_critical")
      .sort((a, b) => b.created_at.localeCompare(a.created_at));
    return crit[0] ?? null;
  }, [runs]);

  const criticalClean =
    latestCritical && latestCritical.total != null && latestCritical.passed === latestCritical.total;
  const criticalFails =
    latestCritical && latestCritical.total != null
      ? latestCritical.total - (latestCritical.passed ?? 0)
      : 0;

  if (loading && !data) {
    return (
      <div style={{ textAlign: "center", padding: 60 }}>
        <Spin />
      </div>
    );
  }

  return (
    <div style={{ padding: 20 }}>
      <Space align="center" style={{ marginBottom: 12 }}>
        <Typography.Title level={4} style={{ margin: 0 }}>
          Eval Dashboard
        </Typography.Title>
        {latestCritical ? (
          criticalClean ? (
            <Badge
              color={SEMANTIC.pass}
              text={
                <span style={{ color: SEMANTIC.pass, fontWeight: 600 }}>
                  Critical 0-fail ✓ ({(latestCritical.git_sha ?? "").slice(0, 7)})
                </span>
              }
            />
          ) : (
            <Badge
              color={SEMANTIC.fail}
              text={
                <span style={{ color: SEMANTIC.fail, fontWeight: 600 }}>
                  Critical {criticalFails} FAIL ({(latestCritical.git_sha ?? "").slice(0, 7)})
                </span>
              }
            />
          )
        ) : (
          <Tag>Chưa có run Critical</Tag>
        )}
      </Space>

      {error && <Alert type="error" showIcon style={{ marginBottom: 12 }} message={error} />}

      <Card size="small" title="Điểm pass (%) theo thời gian — tách theo suite" style={{ marginBottom: 16 }}>
        <ResponsiveContainer width="100%" height={280}>
          <LineChart data={chartData} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke={SEMANTIC.border} />
            <XAxis dataKey="label" fontSize={12} />
            <YAxis fontSize={12} width={40} domain={[0, 100]} />
            <Tooltip />
            <Legend />
            {SUITES.map((s) => (
              <Line
                key={s.key}
                type="monotone"
                dataKey={s.key}
                name={s.label}
                stroke={s.color}
                strokeWidth={2}
                dot={{ r: 2 }}
                connectNulls
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </Card>

      <Card size="small" title="Các run gần nhất" styles={{ body: { padding: 0 } }}>
        <Table<EvalRun>
          size="small"
          rowKey="id"
          dataSource={runs}
          pagination={{ pageSize: 15, hideOnSinglePage: true }}
          onRow={(r) => ({ onClick: () => setSelected(r), style: { cursor: "pointer" } })}
          columns={[
            {
              title: "git_sha",
              dataIndex: "git_sha",
              render: (s: string | null) => (
                <span style={{ fontFamily: MONO, fontSize: 12.5 }}>{(s ?? "—").slice(0, 7)}</span>
              ),
            },
            {
              title: "prompt",
              dataIndex: "prompt_version",
              render: (v: number | null) => (
                <span style={{ fontFamily: MONO }}>v{v ?? "—"}</span>
              ),
            },
            {
              title: "Suite",
              dataIndex: "suite",
              render: (s: string | null) => {
                const isCritical = s === "adversarial_critical";
                return <Tag color={isCritical ? "red" : "blue"}>{SUITE_LABEL[s ?? ""] ?? s}</Tag>;
              },
            },
            {
              title: "Passed/Total",
              render: (_: unknown, r: EvalRun) => {
                const clean = r.total != null && r.passed === r.total;
                return (
                  <span
                    style={{
                      fontFamily: MONO,
                      color: clean ? SEMANTIC.pass : SEMANTIC.fail,
                    }}
                  >
                    {r.passed ?? "—"}/{r.total ?? "—"}
                  </span>
                );
              },
            },
            {
              title: "Thời gian",
              dataIndex: "created_at",
              render: (t: string) => (
                <span style={{ fontFamily: MONO, fontSize: 12 }}>{fmtTime(t)}</span>
              ),
            },
          ]}
          locale={{ emptyText: "Chưa có eval run. Chạy evals/runner.py để ghi eval_runs." }}
        />
      </Card>

      <Modal
        open={selected !== null}
        onCancel={() => setSelected(null)}
        footer={null}
        title={
          selected ? (
            <span style={{ fontFamily: MONO }}>
              {(selected.git_sha ?? "—").slice(0, 7)} · {SUITE_LABEL[selected.suite ?? ""] ?? selected.suite}
            </span>
          ) : null
        }
      >
        {selected && <MetricsBreakdown run={selected} />}
      </Modal>
    </div>
  );
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function MetricsBreakdown({ run }: { run: EvalRun }) {
  const metrics = (run.metrics ?? {}) as Record<string, any>;
  const byGroup = metrics.by_group as Record<string, { passed?: number; total?: number }> | undefined;

  const rows = byGroup
    ? Object.entries(byGroup).map(([group, v]) => ({
        key: group,
        group,
        passed: v.passed ?? 0,
        total: v.total ?? 0,
      }))
    : Object.entries(metrics).map(([k, v]) => ({
        key: k,
        group: k,
        passed: typeof v === "number" ? v : JSON.stringify(v),
        total: "",
      }));

  return (
    <Table
      size="small"
      rowKey="key"
      pagination={false}
      dataSource={rows}
      columns={[
        { title: "Nhóm / chỉ số", dataIndex: "group" },
        {
          title: "Giá trị",
          render: (_: unknown, r: { passed: unknown; total: unknown }) => (
            <span style={{ fontFamily: MONO }}>
              {String(r.passed)}
              {r.total !== "" ? `/${r.total}` : ""}
            </span>
          ),
        },
      ]}
      locale={{ emptyText: "Run này không có metrics chi tiết." }}
    />
  );
}

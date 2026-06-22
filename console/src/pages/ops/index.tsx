import { Alert, Card, Col, Row, Spin, Statistic, Table, Tag, Typography } from "antd";
import { useNavigate } from "react-router";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useApi } from "../../api/hooks";
import type { Metrics } from "../../api/types";
import { fmtPct, fmtUsd } from "../../lib/format";
import { intentLabel, reasonLabel } from "../../lib/intents";
import { MONO, SEMANTIC } from "../../theme";

const MONO_STAT = { fontFamily: MONO, fontSize: 22 };

// Screen 2 — Ops Dashboard: KPI cards + charts + failure clustering.
export function OpsDashboard() {
  const navigate = useNavigate();
  const { data, loading, error } = useApi<Metrics>("/staff/metrics");

  if (loading && !data) {
    return (
      <div style={{ textAlign: "center", padding: 60 }}>
        <Spin />
      </div>
    );
  }

  const costByDay = (data?.cost_by_day ?? []).map((d) => ({
    date: d.date.slice(5), // MM-DD
    cost: d.cost_usd,
  }));
  const intentDist = (data?.intent_distribution ?? []).map((d) => ({
    name: intentLabel(d.intent),
    count: d.count,
  }));
  const reasons = (data?.escalation_reasons ?? []).map((d) => ({
    name: reasonLabel(d.reason),
    reason: d.reason,
    count: d.count,
  }));

  return (
    <div style={{ padding: 20 }}>
      <Typography.Title level={4} style={{ marginTop: 0 }}>
        Ops Dashboard
      </Typography.Title>
      {error && <Alert type="error" showIcon style={{ marginBottom: 12 }} message={error} />}

      <Row gutter={[16, 16]}>
        <Col xs={12} md={8} lg={4}>
          <Card size="small">
            <Statistic
              title="Resolution rate"
              value={fmtPct(data?.resolution_rate)}
              valueStyle={{ ...MONO_STAT, color: SEMANTIC.pass }}
            />
          </Card>
        </Col>
        <Col xs={12} md={8} lg={4}>
          <Card size="small">
            <Statistic
              title="Escalation rate"
              value={fmtPct(data?.escalation_rate)}
              valueStyle={{ ...MONO_STAT, color: SEMANTIC.warn }}
            />
          </Card>
        </Col>
        <Col xs={12} md={8} lg={4}>
          <Card size="small">
            <Statistic
              title="Chi phí TB/hội thoại"
              value={fmtUsd(data?.avg_cost_usd)}
              valueStyle={MONO_STAT}
            />
          </Card>
        </Col>
        <Col xs={12} md={8} lg={4}>
          <Card size="small">
            <Statistic
              title="Latency p50"
              value={data ? `${Math.round(data.latency_ms.p50)} ms` : "—"}
              valueStyle={MONO_STAT}
            />
          </Card>
        </Col>
        <Col xs={12} md={8} lg={4}>
          <Card size="small">
            <Statistic
              title="Latency p95"
              value={data ? `${Math.round(data.latency_ms.p95)} ms` : "—"}
              valueStyle={MONO_STAT}
            />
          </Card>
        </Col>
        <Col xs={12} md={8} lg={4}>
          <Card size="small">
            <Statistic
              title="Cache hit rate"
              value={fmtPct(data?.cache_hit_rate)}
              valueStyle={{ ...MONO_STAT, color: SEMANTIC.pass }}
            />
            <Typography.Text type="secondary" style={{ fontSize: 11 }}>
              {data
                ? `Tiết kiệm ~${fmtUsd(data.cache_savings_usd)} · ${data.faq_turns} lượt faq`
                : "TIP-015"}
            </Typography.Text>
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]} style={{ marginTop: 4 }}>
        <Col xs={24} lg={12}>
          <Card size="small" title="Chi phí theo ngày (7 ngày)">
            <ResponsiveContainer width="100%" height={240}>
              <LineChart data={costByDay} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={SEMANTIC.border} />
                <XAxis dataKey="date" fontSize={12} />
                <YAxis fontSize={12} width={48} />
                <Tooltip formatter={(v: number) => fmtUsd(v)} />
                <Line
                  type="monotone"
                  dataKey="cost"
                  stroke={SEMANTIC.accent}
                  strokeWidth={2}
                  dot={{ r: 2 }}
                />
              </LineChart>
            </ResponsiveContainer>
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card size="small" title="Phân bố intent">
            <ResponsiveContainer width="100%" height={240}>
              <BarChart data={intentDist} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={SEMANTIC.border} />
                <XAxis dataKey="name" fontSize={11} interval={0} angle={-15} textAnchor="end" height={50} />
                <YAxis fontSize={12} width={36} allowDecimals={false} />
                <Tooltip />
                <Bar dataKey="count" fill={SEMANTIC.accent} radius={[3, 3, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]} style={{ marginTop: 4 }}>
        <Col xs={24} lg={12}>
          <Card size="small" title="Lý do leo thang">
            <ResponsiveContainer width="100%" height={240}>
              <BarChart data={reasons} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={SEMANTIC.border} />
                <XAxis dataKey="name" fontSize={11} interval={0} angle={-15} textAnchor="end" height={50} />
                <YAxis fontSize={12} width={36} allowDecimals={false} />
                <Tooltip />
                <Bar dataKey="count" fill={SEMANTIC.warn} radius={[3, 3, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card
            size="small"
            title="Failure clustering"
            extra={
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                Bấm để lọc về Trace Explorer
              </Typography.Text>
            }
          >
            <Table
              size="small"
              rowKey="reason"
              pagination={false}
              dataSource={reasons}
              onRow={(r) => ({
                onClick: () => navigate(`/conversations?escalated=true&reason=${r.reason}`),
                style: { cursor: "pointer" },
              })}
              columns={[
                {
                  title: "Nhóm lỗi",
                  dataIndex: "name",
                  render: (n: string) => <Tag color="volcano">{n}</Tag>,
                },
                {
                  title: "Số hội thoại",
                  dataIndex: "count",
                  align: "right" as const,
                  render: (c: number) => <span style={{ fontFamily: MONO }}>{c}</span>,
                },
              ]}
              locale={{ emptyText: "Chưa có ca leo thang nào." }}
            />
          </Card>
        </Col>
      </Row>

      {data && (
        <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginTop: 12 }}>
          Cửa sổ quét: {data.window.conversations_scanned} hội thoại ·{" "}
          {data.window.trace_events_scanned} trace events gần nhất.
        </Typography.Paragraph>
      )}
    </div>
  );
}

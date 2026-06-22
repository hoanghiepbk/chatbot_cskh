import { Alert, Card, Segmented, Space, Table, Tag, Typography } from "antd";
import { useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { useApi } from "../../api/hooks";
import type { ConversationRow } from "../../api/types";
import { PlainText } from "../../components/PlainText";
import { fmtTime, shortId } from "../../lib/format";
import { intentColor, intentLabel } from "../../lib/intents";
import { MONO, SEMANTIC } from "../../theme";

// Screen 1 — Trace Explorer list. Click a row → vertical trace timeline.
export function ConversationList() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [mode, setMode] = useState<string>("all");
  // initial escalated filter can arrive from the Ops failure-cluster drilldown
  const [escalated, setEscalated] = useState<string>(
    params.get("escalated") === "true" ? "true" : "all",
  );

  const path = useMemo(() => {
    const qs = new URLSearchParams({ limit: "100" });
    if (mode !== "all") qs.set("mode", mode);
    if (escalated !== "all") qs.set("escalated", escalated);
    return `/staff/conversations?${qs.toString()}`;
  }, [mode, escalated]);

  const { data, loading, error } = useApi<{ conversations: ConversationRow[] }>(path, {
    deps: [mode, escalated],
  });

  const columns = [
    {
      title: "ID",
      dataIndex: "id",
      render: (id: string) => (
        <span style={{ fontFamily: MONO, fontSize: 12.5 }}>{shortId(id)}</span>
      ),
    },
    {
      title: "Khách",
      dataIndex: "display_name",
      render: (name: string | null) => <PlainText text={name ?? "—"} />,
    },
    {
      title: "Chế độ",
      dataIndex: "mode",
      render: (m: string) =>
        m === "human" ? (
          <Tag color="orange">Nhân viên</Tag>
        ) : (
          <Tag color="blue">Bot</Tag>
        ),
    },
    {
      title: "Tin nhắn",
      dataIndex: "message_count",
      align: "right" as const,
      render: (n: number) => <span style={{ fontFamily: MONO }}>{n}</span>,
    },
    {
      title: "Intent gần nhất",
      dataIndex: "last_intent",
      render: (i: string | null) => <Tag color={intentColor(i)}>{intentLabel(i)}</Tag>,
    },
    {
      title: "Leo thang",
      dataIndex: "escalated",
      render: (e: boolean) =>
        e ? <Tag color="volcano">Đã chuyển</Tag> : <Tag>Không</Tag>,
    },
    {
      title: "Bắt đầu",
      dataIndex: "started_at",
      render: (t: string) => (
        <span style={{ fontFamily: MONO, fontSize: 12 }}>{fmtTime(t)}</span>
      ),
    },
  ];

  return (
    <div style={{ padding: 20 }}>
      <Typography.Title level={4} style={{ marginTop: 0 }}>
        Trace Explorer
      </Typography.Title>
      <Space size="large" style={{ marginBottom: 12 }}>
        <Space size={6}>
          <Typography.Text type="secondary">Chế độ</Typography.Text>
          <Segmented
            size="small"
            value={mode}
            onChange={(v) => setMode(String(v))}
            options={[
              { label: "Tất cả", value: "all" },
              { label: "Bot", value: "agent" },
              { label: "Nhân viên", value: "human" },
            ]}
          />
        </Space>
        <Space size={6}>
          <Typography.Text type="secondary">Leo thang</Typography.Text>
          <Segmented
            size="small"
            value={escalated}
            onChange={(v) => setEscalated(String(v))}
            options={[
              { label: "Tất cả", value: "all" },
              { label: "Đã chuyển", value: "true" },
              { label: "Chưa", value: "false" },
            ]}
          />
        </Space>
      </Space>

      {error && (
        <Alert type="error" showIcon style={{ marginBottom: 12 }} message={error} />
      )}

      <Card styles={{ body: { padding: 0 } }}>
        <Table<ConversationRow>
          size="small"
          rowKey="id"
          loading={loading}
          dataSource={data?.conversations ?? []}
          columns={columns}
          pagination={{ pageSize: 20, hideOnSinglePage: true }}
          onRow={(record) => ({
            onClick: () => navigate(`/conversations/${record.id}`),
            style: { cursor: "pointer" },
          })}
          locale={{
            emptyText: (
              <div style={{ padding: 24, color: SEMANTIC.muted }}>
                Chưa có hội thoại nào. Mở widget khách để bắt đầu.
              </div>
            ),
          }}
        />
      </Card>
    </div>
  );
}

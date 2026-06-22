import { Alert, Card, Spin, Table, Tag, Typography } from "antd";
import { useApi } from "../../api/hooks";
import type { GapCluster, KnowledgeGaps } from "../../api/types";
import { PlainText } from "../../components/PlainText";
import { fmtTime } from "../../lib/format";
import { MONO, SEMANTIC } from "../../theme";

// Screen 5 (TIP-015) — Knowledge Gaps: clustered faq questions the KB can't
// answer yet, so the team knows which docs to add. Queries are masked customer
// input → PlainText escape.
export function KnowledgeGapsPage() {
  const { data, loading, error } = useApi<KnowledgeGaps>("/staff/knowledge-gaps");

  if (loading && !data) {
    return (
      <div style={{ textAlign: "center", padding: 60 }}>
        <Spin />
      </div>
    );
  }

  return (
    <div style={{ padding: 20 }}>
      <Typography.Title level={4} style={{ marginTop: 0 }}>
        Knowledge Gaps
      </Typography.Title>
      <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
        Câu hỏi khách hỏi nhưng KB chưa trả lời được — gom nhóm theo độ tương đồng. Ưu tiên bổ
        sung tài liệu cho nhóm nhiều lượt nhất.
      </Typography.Paragraph>

      {error && <Alert type="error" showIcon style={{ marginBottom: 12 }} message={error} />}

      <Card styles={{ body: { padding: 0 } }}>
        <Table<GapCluster>
          size="small"
          rowKey={(r) => r.representative_query + r.last_seen}
          loading={loading}
          dataSource={data?.clusters ?? []}
          pagination={{ pageSize: 20, hideOnSinglePage: true }}
          columns={[
            {
              title: "Câu hỏi đại diện",
              dataIndex: "representative_query",
              render: (q: string) => <PlainText text={q} style={{ fontWeight: 500 }} />,
            },
            {
              title: "Số lần",
              dataIndex: "count",
              align: "right" as const,
              sorter: (a: GapCluster, b: GapCluster) => a.count - b.count,
              defaultSortOrder: "descend" as const,
              render: (c: number) => (
                <Tag color={c >= 5 ? "volcano" : "default"} style={{ fontFamily: MONO }}>
                  {c}
                </Tag>
              ),
            },
            {
              title: "Lần cuối",
              dataIndex: "last_seen",
              render: (t: string) => (
                <span style={{ fontFamily: MONO, fontSize: 12 }}>{fmtTime(t)}</span>
              ),
            },
            {
              title: "Ví dụ khác",
              dataIndex: "sample_queries",
              render: (s: string[]) => (
                <div>
                  {(s ?? []).slice(0, 4).map((q, i) => (
                    <div key={i} style={{ color: SEMANTIC.muted, fontSize: 12.5 }}>
                      • <PlainText text={q} />
                    </div>
                  ))}
                </div>
              ),
            },
          ]}
          locale={{
            emptyText: (
              <div style={{ padding: 24, color: SEMANTIC.muted }}>
                Chưa ghi nhận lỗ hổng kiến thức nào. Khi agent không trả lời được một câu faq,
                nó sẽ xuất hiện ở đây.
              </div>
            ),
          }}
        />
      </Card>

      {data && (
        <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginTop: 12 }}>
          Tổng {data.total_events} sự kiện gap gần đây.
        </Typography.Paragraph>
      )}
    </div>
  );
}

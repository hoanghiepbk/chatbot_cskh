import { ArrowLeftOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Col, Row, Spin, Statistic, Tag, Typography } from "antd";
import { useParams } from "react-router";
import { useNavigate } from "react-router";
import { useApi } from "../../api/hooks";
import type { TraceResponse } from "../../api/types";
import { TraceTimeline } from "../../components/TraceTimeline";
import { fmtMs, fmtUsd, shortId } from "../../lib/format";
import { MONO, SEMANTIC } from "../../theme";

// Screen 1 (detail) — the signature vertical trace timeline + a session summary.
export function ConversationShow() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { data, loading, error } = useApi<TraceResponse>(
    id ? `/staff/conversations/${id}/trace` : null,
  );

  const summary = data?.summary;

  return (
    <div style={{ padding: 20 }}>
      <Button
        type="text"
        icon={<ArrowLeftOutlined />}
        onClick={() => navigate("/conversations")}
        style={{ marginBottom: 8, paddingLeft: 0 }}
      >
        Danh sách hội thoại
      </Button>
      <Typography.Title level={4} style={{ marginTop: 0 }}>
        Trace{" "}
        <span style={{ fontFamily: MONO, fontSize: 16, color: SEMANTIC.muted }}>
          {shortId(id)}
        </span>
      </Typography.Title>

      {error && <Alert type="error" showIcon style={{ marginBottom: 12 }} message={error} />}

      {summary && (
        <Card size="small" style={{ marginBottom: 16 }}>
          <Row gutter={16}>
            <Col span={6}>
              <Statistic
                title="Tổng chi phí"
                value={fmtUsd(summary.total_cost_usd)}
                valueStyle={{ fontFamily: MONO, fontSize: 18 }}
              />
            </Col>
            <Col span={6}>
              <Statistic
                title="Tổng latency"
                value={fmtMs(summary.total_latency_ms)}
                valueStyle={{ fontFamily: MONO, fontSize: 18 }}
              />
            </Col>
            <Col span={6}>
              <Statistic
                title="Số lần gọi LLM"
                value={summary.llm_calls}
                valueStyle={{ fontFamily: MONO, fontSize: 18 }}
              />
            </Col>
            <Col span={6}>
              <div style={{ paddingTop: 4 }}>
                <Typography.Text type="secondary" style={{ fontSize: 14 }}>
                  Leo thang
                </Typography.Text>
                <div style={{ marginTop: 6 }}>
                  {summary.escalated ? (
                    <Tag color="volcano">Đã chuyển nhân viên</Tag>
                  ) : (
                    <Tag color="green">Không</Tag>
                  )}
                </div>
              </div>
            </Col>
          </Row>
        </Card>
      )}

      <Card>
        {loading ? (
          <div style={{ textAlign: "center", padding: 40 }}>
            <Spin />
          </div>
        ) : data && data.events.length > 0 ? (
          <TraceTimeline events={data.events} />
        ) : (
          <div style={{ padding: 24, color: SEMANTIC.muted }}>
            Hội thoại này chưa có bước trace nào.
          </div>
        )}
      </Card>
    </div>
  );
}

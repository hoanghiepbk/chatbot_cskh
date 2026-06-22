import {
  CustomerServiceOutlined,
  PhoneOutlined,
  RollbackOutlined,
  SendOutlined,
} from "@ant-design/icons";
import {
  Alert,
  App,
  Button,
  Card,
  Col,
  Divider,
  Empty,
  Input,
  Row,
  Space,
  Table,
  Tag,
  Typography,
} from "antd";
import { useMemo, useState } from "react";
import { api, ApiError } from "../../api/client";
import { useApi } from "../../api/hooks";
import type { Ticket } from "../../api/types";
import { PlainText } from "../../components/PlainText";
import { fmtTime, shortId } from "../../lib/format";
import { intentLabel, reasonLabel } from "../../lib/intents";
import { useMessages, useRealtime } from "../../lib/realtime";
import { realtimeEnabled } from "../../lib/supabase";
import { MONO, SEMANTIC } from "../../theme";

const TYPE_META: Record<string, { label: string; color: string }> = {
  rescue: { label: "Cứu hộ", color: "red" },
  complaint: { label: "Khiếu nại", color: "volcano" },
  after_hours: { label: "Ngoài giờ", color: "gold" },
  booking: { label: "Đặt lịch", color: "blue" },
};

const PRIORITY_META: Record<string, { label: string; color: string }> = {
  urgent: { label: "Khẩn", color: "red" },
  high: { label: "Cao", color: "orange" },
  normal: { label: "Thường", color: "default" },
};

// Screen 4 — HITL Queue: realtime queue + claim → live chat → reveal → resolve.
export function HitlQueue() {
  const { message, modal } = App.useApp();
  const { data, loading, error, reload } = useApi<{ tickets: Ticket[] }>("/staff/queue", {
    pollMs: 5000,
  });
  // Realtime push (anon-readable `conversations` is the reliable signal; tickets
  // push too once staff hold a Supabase Auth session). Polling above is the
  // guarantee either way.
  useRealtime("conversations", reload);
  useRealtime("tickets", reload);

  const tickets = data?.tickets ?? [];
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected = useMemo(
    () => tickets.find((t) => t.id === selectedId) ?? null,
    [tickets, selectedId],
  );

  const chatCid = selected?.status === "claimed" ? selected.conversation_id : null;
  const { messages, available: chatAvailable, reload: reloadMessages } = useMessages(chatCid);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);

  async function act(fn: () => Promise<void>, okMsg: string) {
    try {
      await fn();
      message.success(okMsg);
      reload();
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : "Thao tác thất bại");
    }
  }

  function claim(t: Ticket) {
    void act(async () => {
      await api.post(`/staff/tickets/${t.id}/claim`);
    }, "Đã nhận ticket — chuyển sang chế độ nhân viên");
  }

  function resolve(t: Ticket) {
    void act(async () => {
      await api.post(`/staff/tickets/${t.id}/resolve`);
    }, "Đã trả lại bot — ticket resolved");
  }

  async function reveal(t: Ticket) {
    try {
      const res = await api.post<{ placeholder: string; value: string }>(
        `/staff/tickets/${t.id}/reveal_contact`,
      );
      modal.info({
        title: "Số liên hệ khách (đã ghi log audit)",
        content: (
          <div>
            <Typography.Paragraph style={{ fontFamily: MONO, fontSize: 18, marginBottom: 8 }}>
              {res.value}
            </Typography.Paragraph>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              Đã ghi trace <code>pii_reveal</code> (audit). Chỉ dùng để liên hệ khách.
            </Typography.Text>
          </div>
        ),
      });
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : "Không lấy được số liên hệ");
    }
  }

  async function send() {
    if (!chatCid || !draft.trim()) return;
    setSending(true);
    try {
      await api.post(`/staff/conversations/${chatCid}/message`, { text: draft.trim() });
      setDraft("");
      reloadMessages();
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : "Gửi tin nhắn thất bại");
    } finally {
      setSending(false);
    }
  }

  const columns = [
    {
      title: "Loại",
      dataIndex: "type",
      render: (t: string) => {
        const m = TYPE_META[t] ?? { label: t, color: "default" };
        return <Tag color={m.color}>{m.label}</Tag>;
      },
    },
    {
      title: "Ưu tiên",
      dataIndex: "priority",
      render: (p: string) => {
        const m = PRIORITY_META[p] ?? { label: p, color: "default" };
        return <Tag color={m.color}>{m.label}</Tag>;
      },
    },
    {
      title: "Khách",
      render: (_: unknown, t: Ticket) => (
        <PlainText text={t.payload?.customer?.display_name ?? "—"} />
      ),
    },
    {
      title: "Lý do",
      render: (_: unknown, t: Ticket) => (
        <Tag color="volcano">{reasonLabel(t.payload?.reason ?? t.type)}</Tag>
      ),
    },
    {
      title: "Trạng thái",
      dataIndex: "status",
      render: (s: string) =>
        s === "claimed" ? <Tag color="processing">Đang xử lý</Tag> : <Tag>Mở</Tag>,
    },
    {
      title: "Lúc",
      dataIndex: "created_at",
      render: (t: string) => (
        <span style={{ fontFamily: MONO, fontSize: 12 }}>{fmtTime(t)}</span>
      ),
    },
  ];

  return (
    <div style={{ padding: 20 }}>
      <Space align="center" style={{ marginBottom: 12 }}>
        <Typography.Title level={4} style={{ margin: 0 }}>
          HITL Queue
        </Typography.Title>
        <Tag color={realtimeEnabled ? "green" : "default"}>
          {realtimeEnabled ? "Realtime ON" : "Polling (5s)"}
        </Tag>
      </Space>

      {error && <Alert type="error" showIcon style={{ marginBottom: 12 }} message={error} />}

      <Row gutter={16}>
        <Col xs={24} lg={10}>
          <Card styles={{ body: { padding: 0 } }}>
            <Table<Ticket>
              size="small"
              rowKey="id"
              loading={loading}
              dataSource={tickets}
              columns={columns}
              pagination={false}
              rowClassName={(t) =>
                t.type === "rescue" || t.priority === "urgent" ? "row-urgent" : ""
              }
              onRow={(t) => ({
                onClick: () => setSelectedId(t.id),
                style: {
                  cursor: "pointer",
                  background: t.id === selectedId ? "#EFF4FF" : undefined,
                },
              })}
              locale={{
                emptyText: (
                  <div style={{ padding: 24, color: SEMANTIC.muted }}>
                    Hàng đợi trống. Khi có ca cần người, ticket sẽ hiện ở đây ngay.
                  </div>
                ),
              }}
            />
          </Card>
        </Col>

        <Col xs={24} lg={14}>
          {selected ? (
            <TicketPanel
              ticket={selected}
              chatAvailable={chatAvailable}
              messages={messages}
              draft={draft}
              sending={sending}
              onDraft={setDraft}
              onSend={send}
              onClaim={() => claim(selected)}
              onResolve={() => resolve(selected)}
              onReveal={() => void reveal(selected)}
            />
          ) : (
            <Card style={{ minHeight: 320 }}>
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description="Chọn một ticket bên trái để xem chi tiết và xử lý."
                style={{ paddingTop: 60 }}
              />
            </Card>
          )}
        </Col>
      </Row>

      <style>{`.row-urgent > td { background: #FEF2F2 !important; }`}</style>
    </div>
  );
}

type PublicMsg = { id: string; sender: string; content_masked: string | null };

function TicketPanel({
  ticket,
  chatAvailable,
  messages,
  draft,
  sending,
  onDraft,
  onSend,
  onClaim,
  onResolve,
  onReveal,
}: {
  ticket: Ticket;
  chatAvailable: boolean;
  messages: PublicMsg[];
  draft: string;
  sending: boolean;
  onDraft: (v: string) => void;
  onSend: () => void;
  onClaim: () => void;
  onResolve: () => void;
  onReveal: () => void;
}) {
  const p = ticket.payload ?? {};
  const isRescue = ticket.type === "rescue";
  const claimed = ticket.status === "claimed";

  return (
    <Card
      title={
        <Space>
          <Tag color={TYPE_META[ticket.type]?.color ?? "default"}>
            {TYPE_META[ticket.type]?.label ?? ticket.type}
          </Tag>
          <span style={{ fontFamily: MONO, fontSize: 12.5 }}>{shortId(ticket.id)}</span>
        </Space>
      }
      extra={
        <Space>
          {ticket.status === "open" && (
            <Button type="primary" icon={<CustomerServiceOutlined />} onClick={onClaim}>
              Nhận
            </Button>
          )}
          {claimed && (
            <Button icon={<RollbackOutlined />} onClick={onResolve}>
              Trả lại bot
            </Button>
          )}
          <Button
            danger={isRescue}
            type={isRescue ? "primary" : "default"}
            icon={<PhoneOutlined />}
            onClick={onReveal}
          >
            Hiện số liên hệ
          </Button>
        </Space>
      }
    >
      <Typography.Text type="secondary">Khách</Typography.Text>
      <div style={{ marginBottom: 8 }}>
        <PlainText text={p.customer?.display_name ?? "—"} style={{ fontWeight: 600 }} />
      </div>

      <Typography.Text type="secondary">Tóm tắt</Typography.Text>
      <div style={{ marginBottom: 8 }}>
        <PlainText text={p.summary ?? "—"} />
      </div>

      {p.suggested_action && (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 10 }}
          message={<PlainText text={`Gợi ý: ${p.suggested_action}`} />}
        />
      )}

      <Space size={4} wrap style={{ marginBottom: 8 }}>
        {(p.intents ?? []).map((i, idx) => (
          <Tag key={`${i}-${idx}`} color="geekblue">
            {intentLabel(i)}
          </Tag>
        ))}
        {(p.tool_calls ?? []).map((t, idx) => (
          <Tag key={`${t}-${idx}`} color="purple">
            {t}
          </Tag>
        ))}
      </Space>

      <Divider style={{ margin: "12px 0" }} />

      {claimed ? (
        <div>
          <Typography.Text type="secondary">Live chat</Typography.Text>
          {!chatAvailable && (
            <Alert
              type="warning"
              showIcon
              style={{ margin: "8px 0" }}
              message="Cần VITE_SUPABASE_ANON_KEY để xem lịch sử tin nhắn (vẫn gửi được)."
            />
          )}
          <div
            style={{
              maxHeight: 260,
              overflowY: "auto",
              background: SEMANTIC.bg,
              border: `1px solid ${SEMANTIC.border}`,
              borderRadius: 6,
              padding: 10,
              margin: "8px 0",
            }}
          >
            {messages.length === 0 ? (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                Chưa có tin nhắn.
              </Typography.Text>
            ) : (
              messages.map((m) => <ChatBubble key={m.id} sender={m.sender} text={m.content_masked} />)
            )}
          </div>
          <Space.Compact style={{ width: "100%" }}>
            <Input
              value={draft}
              placeholder="Nhập tin nhắn gửi khách…"
              onChange={(e) => onDraft(e.target.value)}
              onPressEnter={onSend}
              disabled={sending}
            />
            <Button type="primary" icon={<SendOutlined />} onClick={onSend} loading={sending}>
              Gửi
            </Button>
          </Space.Compact>
        </div>
      ) : (
        <div>
          <Typography.Text type="secondary">Tin nhắn gần đây (đã ẩn PII)</Typography.Text>
          <div style={{ marginTop: 8 }}>
            {(p.recent_messages ?? []).length === 0 ? (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                Không có tin nhắn.
              </Typography.Text>
            ) : (
              (p.recent_messages ?? []).map((m, idx) => (
                <ChatBubble key={idx} sender={m.sender} text={m.content_masked} />
              ))
            )}
          </div>
        </div>
      )}
    </Card>
  );
}

function ChatBubble({ sender, text }: { sender: string; text: string | null }) {
  const isCustomer = sender === "customer";
  const label = isCustomer ? "Khách" : sender === "staff" ? "Nhân viên" : "Bot";
  const color = isCustomer ? SEMANTIC.ink : sender === "staff" ? SEMANTIC.accent : SEMANTIC.muted;
  return (
    <div style={{ marginBottom: 6 }}>
      <span style={{ fontSize: 11, fontWeight: 600, color, marginRight: 6 }}>{label}:</span>
      <PlainText text={text} />
    </div>
  );
}

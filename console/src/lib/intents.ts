// TIP-006 suggestion (adopted in TIP-014): show friendly Vietnamese intent
// labels in the console instead of the raw model labels. Source of truth for the
// canonical intent set: agent/ml/phobert/model/labels.json + graph router.

const LABELS: Record<string, string> = {
  faq: "Hỏi đáp kiến thức",
  booking: "Đặt lịch mới",
  order_lookup: "Tra cứu đơn",
  modify_booking: "Đổi/hủy lịch & đơn",
  emergency: "Cứu hộ khẩn cấp",
  complaint: "Khiếu nại",
  chitchat: "Trò chuyện",
  out_of_scope: "Ngoài phạm vi",
};

// AntD Tag colors per intent (urgent intents lean red/volcano).
const COLORS: Record<string, string> = {
  emergency: "red",
  complaint: "volcano",
  booking: "blue",
  modify_booking: "geekblue",
  order_lookup: "cyan",
  faq: "green",
  chitchat: "default",
  out_of_scope: "default",
};

export function intentLabel(intent?: string | null): string {
  if (!intent) return "—";
  return LABELS[intent] ?? intent;
}

export function intentColor(intent?: string | null): string {
  if (!intent) return "default";
  return COLORS[intent] ?? "default";
}

// Friendly labels for escalation reasons (failure clustering / queue).
const REASONS: Record<string, string> = {
  injection: "Nghi injection",
  emergency: "Khẩn cấp / cứu hộ",
  complaint: "Khiếu nại leo thang",
  paid_order_cancel: "Hủy đơn đã thanh toán",
  low_confidence: "Router thiếu tự tin",
};

export function reasonLabel(reason?: string | null): string {
  if (!reason) return "—";
  return REASONS[reason] ?? reason;
}

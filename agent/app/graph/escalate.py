"""Escalate node + handoff package (TIP-008) — replaces escalate_stub.

Every agent-side escalation funnels through open_escalation_ticket():
- the escalate NODE handles router-driven routes (low_confidence, parse_fail)
  and the complaint node's hand-off;
- paid_order (action node) and guardrail_block (guardrail_out) call the same
  helper directly — they keep their own bespoke replies but still create a
  staff ticket (graph topology makes routing those two through the node
  impractical; see TIP-008 report DEVIATIONS).

Emergency keeps its own rescue-ticket flow (TIP-007) and is NOT merged here.

Hours rule (KB-07, Asia/Ho_Chi_Minh): Mon–Sat 08:00–18:00, Sun 08:00–12:00.
In-hours → complaint ticket + "a human joins now"; after-hours (REQ-10) →
after_hours ticket + next-business-hour promise, never "joins now".
"""

from datetime import datetime, timedelta, timezone

from app.llm import MODEL_HAIKU

HOTLINE = "1900 1234"
VN = timezone(timedelta(hours=7))

# KB-07 business hours, VN wall-clock
WEEKDAY_OPEN, WEEKDAY_CLOSE = 8, 18  # Mon–Sat
SUNDAY_OPEN, SUNDAY_CLOSE = 8, 12  # Sunday morning only

HANDOFF_SYSTEM = """Bạn tóm tắt hội thoại CSKH XeCare cho nhân viên tiếp nhận. Đọc các tin nhắn
(đã ẩn thông tin cá nhân) và trả về DUY NHẤT JSON:
{"summary": <2-3 câu tóm tắt vấn đề khách đang gặp, tiếng Việt>,
 "suggested_action": <1 câu gợi ý nhân viên nên làm gì tiếp>}
Không bịa thông tin ngoài hội thoại."""

REPLY_IN_HOURS = (
    "Dạ, mình đã chuyển cuộc trò chuyện này tới bộ phận hỗ trợ của XeCare — nhân viên sẽ "
    "vào ngay trong cuộc trò chuyện này để hỗ trợ anh/chị ạ. Anh/chị vui lòng chờ trong "
    "giây lát nhé!"
)
REPLY_AFTER_HOURS = (
    "Dạ, hiện đang ngoài giờ làm việc của XeCare (giờ làm việc: Thứ 2–Thứ 7 8h–18h, "
    "Chủ nhật 8h–12h). Mình đã ghi nhận và chuyển bộ phận hỗ trợ — nhân viên sẽ phản hồi "
    f"anh/chị ngay đầu giờ làm việc kế tiếp ạ. Nếu việc gấp, anh/chị gọi hotline {HOTLINE} nhé."
)


def now_vn() -> datetime:
    """VN wall-clock now — wrapped so tests can monkeypatch (freeze time)."""
    return datetime.now(VN)


def is_business_hours(now: datetime | None = None) -> bool:
    now = now or now_vn()
    weekday = now.weekday()  # Mon=0 .. Sun=6
    if weekday == 6:
        return SUNDAY_OPEN <= now.hour < SUNDAY_CLOSE
    return WEEKDAY_OPEN <= now.hour < WEEKDAY_CLOSE


async def build_handoff(deps, state, reason: str, severity: str) -> dict:
    """Assemble the staff handoff package. One Haiku call (summary +
    suggested_action). All customer-facing data is MASKED — no raw phone."""
    conversation_id = state.get("conversation_id")
    profile = state.get("customer_profile") or {}

    recent_messages: list[dict] = []
    intents: list[str] = []
    tool_calls: list[str] = []
    if conversation_id and deps.supabase is not None:
        msg_rows = (
            deps.supabase.table("messages")
            .select("sender, content_masked, created_at")
            .eq("conversation_id", conversation_id)
            .order("created_at", desc=True)
            .limit(10)
            .execute()
        )
        recent = list(reversed(msg_rows.data or []))
        recent_messages = [
            {"sender": m["sender"], "content_masked": m["content_masked"] or ""}
            for m in recent[-5:]
        ]
        trace_rows = (
            deps.supabase.table("trace_events")
            .select("step_type, payload")
            .eq("conversation_id", conversation_id)
            .order("created_at")
            .execute()
        )
        for t in trace_rows.data or []:
            payload = t.get("payload") or {}
            if t["step_type"] == "router" and payload.get("intent"):
                intents.append(payload["intent"])
            elif t["step_type"] == "tool_call" and payload.get("tool"):
                tool_calls.append(payload["tool"])

    # masked conversation transcript for the summary call (current turn included)
    transcript_lines = [f"{m['sender']}: {m['content_masked']}" for m in recent_messages]
    transcript_lines.append(f"customer: {state.get('masked_text', '')}")
    summary, suggested = "", ""
    result = await deps.llm.complete(
        model=MODEL_HAIKU,
        system=HANDOFF_SYSTEM,
        messages=[{"role": "user", "content": "\n".join(transcript_lines)}],
        max_tokens=300,
        json_mode=True,
    )
    from app.graph.core import extract_json_object

    parsed = extract_json_object(result.text) or {}
    summary = parsed.get("summary", "")
    suggested = parsed.get("suggested_action", "")
    await deps.trace(
        conversation_id,
        "llm_call",
        {
            "purpose": "handoff_summary",
            "model": MODEL_HAIKU,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
        },
        latency_ms=result.latency_ms,
        cost_usd=result.cost_usd,
        prompt_version=deps.prompt_version,
        policy_version=deps.policy_version,
    )

    return {
        "reason": reason,
        "summary": summary,
        "customer": {
            "display_name": profile.get("display_name"),
            "vehicles": profile.get("vehicles", []),
        },  # NO phone — masked handoff
        "recent_messages": recent_messages,
        "intents": intents,
        "tool_calls": tool_calls,
        "suggested_action": suggested,
    }


async def open_escalation_ticket(deps, state, reason: str, severity: str) -> tuple[dict, bool]:
    """Build handoff, create the ticket (type by hours, priority by severity).
    Returns (ticket_row, in_hours). Shared by the escalate node and the
    paid_order / guardrail_block paths."""
    handoff = await build_handoff(deps, state, reason, severity)
    in_hours = is_business_hours()
    ticket_type = "complaint" if in_hours else "after_hours"
    priority = "high" if severity == "high" else "normal"
    if deps.supabase is None:
        return None, in_hours  # unit tests without DB — node still replies/traces
    ticket = (
        deps.supabase.table("tickets")
        .insert(
            {
                "type": ticket_type,
                "priority": priority,
                "status": "open",
                "conversation_id": state.get("conversation_id"),
                "payload": handoff,
            }
        )
        .execute()
    )
    return ticket.data[0], in_hours


def build_escalate_node(deps):
    async def trace(state, step_type, payload, **kw):
        await deps.trace(
            state.get("conversation_id"),
            step_type,
            payload,
            prompt_version=deps.prompt_version,
            policy_version=deps.policy_version,
            **kw,
        )

    async def escalate(state) -> dict:
        reason = state.get("escalate_reason")
        if not reason:
            reason = "parse_fail" if state.get("router_failed") else "low_confidence"
        severity = state.get("escalate_severity", "normal")

        ticket, in_hours = await open_escalation_ticket(deps, state, reason, severity)
        await trace(
            state,
            "escalation",
            {
                "reason": reason,
                "ticket_id": ticket["id"] if ticket else None,
                "after_hours": not in_hours,
            },
        )
        return {
            "reply": REPLY_IN_HOURS if in_hours else REPLY_AFTER_HOURS,
            "escalated": True,
            "reply_branch": "template",  # safe template — no rubric needed
        }

    return escalate

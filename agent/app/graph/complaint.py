"""Complaint node (TIP-008, REQ-09) — replaces complaint_stub.

REQ-09: try to resolve in ONE turn before escalating.
- Turn 1: empathise + resolve within authority. 1 Haiku analyse
  {topic, needs_kb, severity}; then 1 reply call (Sonnet if needs_kb else Haiku).
  Set complaint_attempted in the conversation session. (≤2 LLM calls)
- Already attempted (2nd complaint in the conversation) OR severity 'high' on
  the first turn (legal threat / money loss / accident caused by service) →
  route to the escalate node, reason='complaint'.

The reply still passes guardrail_out like every branch (branch='complaint').
"""

import time

from app.llm import MODEL_HAIKU, MODEL_SONNET

ANALYZE_SYSTEM = """Khách của XeCare (chuỗi dịch vụ xe máy & ô tô) đang phàn nàn/khiếu nại.
Phân tích tin nhắn và trả về DUY NHẤT JSON:
{"topic": <chủ đề khiếu nại, ngắn gọn>,
 "needs_kb": <true nếu cần tra cứu kiến thức/chính sách (bảo hành, quy trình, giá) để trả lời>,
 "severity": "low"|"medium"|"high"}
severity = "high" CHỈ khi: đe dọa pháp lý/kiện tụng, mất tiền lớn, hoặc tai nạn/hư hỏng
do lỗi dịch vụ của XeCare. Còn lại là "low"/"medium". KHÔNG bịa."""

RESOLVE_SYSTEM = """Bạn là tư vấn viên CSKH XeCare. Khách đang phàn nàn. Hãy: (1) đồng cảm và xin
lỗi chân thành, (2) giải quyết trong thẩm quyền dựa trên thông tin được cung cấp, (3) nếu cần
thì hướng dẫn bước tiếp theo. Giọng ấm áp, ngắn gọn, tiếng Việt. KHÔNG hứa hoàn tiền/đền bù cụ
thể (việc đó thuộc bộ phận phụ trách). Giá luôn kèm "tham khảo"."""


def build_complaint_node(deps):
    async def trace(state, step_type, payload, **kw):
        await deps.trace(
            state.get("conversation_id"),
            step_type,
            payload,
            prompt_version=deps.prompt_version,
            policy_version=deps.policy_version,
            **kw,
        )

    async def llm_call(state, purpose, **kwargs):
        start = time.perf_counter()
        result = await deps.llm.complete(**kwargs)
        await trace(
            state,
            "llm_call",
            {
                "purpose": purpose,
                "model": kwargs["model"],
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
            },
            latency_ms=result.latency_ms or int((time.perf_counter() - start) * 1000),
            cost_usd=result.cost_usd,
        )
        return result

    async def complaint(state) -> dict:
        from app.graph.core import extract_json_object

        analysis = extract_json_object(
            (
                await llm_call(
                    state,
                    "complaint_analyze",
                    model=MODEL_HAIKU,
                    system=ANALYZE_SYSTEM,
                    messages=[{"role": "user", "content": state["masked_text"]}],
                    max_tokens=150,
                    json_mode=True,
                )
            ).text
        ) or {}
        severity = analysis.get("severity", "low")
        attempted = bool(state.get("complaint_attempted"))

        # REQ-09: escalate on the 2nd complaint, or immediately if high severity
        if attempted or severity == "high":
            return {
                "escalate_reason": "complaint",
                "escalate_severity": severity,
                "complaint_attempted": True,
            }

        # one self-serve attempt
        if analysis.get("needs_kb"):
            chunks = await deps.search(state["masked_text"], top_k=5)
            context = "\n\n".join(f"[{c.heading}]\n{c.content}" for c in chunks[:5])
            reply = (
                await llm_call(
                    state,
                    "complaint_resolve",
                    model=MODEL_SONNET,
                    system=f"{RESOLVE_SYSTEM}\n\nTRÍCH ĐOẠN TÀI LIỆU:\n{context}",
                    messages=[{"role": "user", "content": state["masked_text"]}],
                    max_tokens=400,
                )
            ).text
        else:
            reply = (
                await llm_call(
                    state,
                    "complaint_resolve",
                    model=MODEL_HAIKU,
                    system=RESOLVE_SYSTEM,
                    messages=[{"role": "user", "content": state["masked_text"]}],
                    max_tokens=400,
                )
            ).text

        return {
            "reply": reply,
            "reply_branch": "complaint",
            "complaint_attempted": True,
        }

    return complaint

"""Emergency node (TIP-007): two-step rescue intake replacing emergency_stub.

Step 1 (pre_gate fires first time): pure template — safety guidance condensed
from KB-05 + hotline + ask for location and callback number. ZERO LLM calls.
Step 2 (emergency session open): every subsequent message routes straight here
(before the router, after guardrail_in so new phone numbers get masked). One
Haiku call extracts {location, callback_ref, confirm}; enough info → rescue
ticket; dismissal → close session; location still unknown after the re-ask →
fail-safe ticket anyway (a thin ticket beats a stranded customer).

The rescue ticket is the ONLY write that skips the confirm gate — grounds:
RRI/REQ-01 (speed first, information intake, not a financial action). The
agent never promises arrival times: dispatch confirms them (REQ-02, KB-05).
"""

import re
import time

from app.llm import MODEL_HAIKU
from app.tools import run_tool

HOTLINE = "1900 1234"

EMERGENCY_EXTRACT_SYSTEM = """Khách của XeCare đang trong tình huống khẩn cấp (tai nạn/hỏng xe).
Trích xuất từ tin nhắn và trả về DUY NHẤT JSON:
{"location": <vị trí khách mô tả, giữ nguyên lời khách>|null,
 "callback_ref": <placeholder số gọi lại xuất hiện trong tin nhắn, ví dụ "[PHONE_1]";
  nếu khách bảo dùng số cũ/số đã đăng ký thì "[PHONE_KH]">|null,
 "confirm": <false NẾU khách nói nhầm/không cần cứu hộ (ví dụ "không sao đâu",
  "mình chỉ hỏi thôi"), ngược lại true>}
- callback_ref CHỈ được là placeholder dạng [PHONE_...] — KHÔNG BAO GIỜ là số thô.
- Trường không có thông tin → null. KHÔNG bịa."""

REPLY_EMERGENCY_STEP1 = (
    "Anh/chị ơi, an toàn là quan trọng nhất lúc này:\n"
    "- Đưa xe/người vào lề đường hoặc làn khẩn cấp, bật đèn cảnh báo.\n"
    "- Trên cao tốc: đứng ra NGOÀI hộ lan chờ, tuyệt đối không đứng cạnh xe.\n"
    "- Có người bị thương → gọi 115 TRƯỚC tiên ạ.\n"
    f"Hotline cứu hộ 24/7 của XeCare: {HOTLINE} — anh/chị gọi ngay nếu tiện máy.\n"
    "Để mình điều phối cứu hộ tới, anh/chị cho mình xin: (1) vị trí hiện tại của "
    "anh/chị, và (2) số gọi lại — mình mặc định dùng số anh/chị đã đăng ký, nếu "
    "muốn dùng số khác anh/chị nhắn giúp mình nhé."
)
REPLY_EMERGENCY_ASK_LOCATION = (
    "Dạ mình vẫn chưa rõ vị trí của anh/chị — anh/chị mô tả giúp mình đang ở đâu "
    "(tên đường, km số mấy, gần địa điểm nào) để đội cứu hộ tìm đúng chỗ ạ. "
    f"Nếu gấp quá, anh/chị gọi ngay {HOTLINE} nhé."
)
REPLY_EMERGENCY_TICKETED = (
    "Dạ, mình đã ghi nhận và chuyển ngay cho đội điều phối cứu hộ — điều phối viên "
    "sẽ gọi lại anh/chị sớm nhất có thể để xác nhận vị trí và thời gian ạ. "
    f"Trong lúc chờ, anh/chị giữ vị trí an toàn giúp mình; cần gấp cứ gọi {HOTLINE} nhé."
)
REPLY_EMERGENCY_DISMISSED = (
    "Dạ vâng, may quá không có gì nghiêm trọng ạ! Mình luôn ở đây nếu anh/chị cần "
    "hỗ trợ về bảo dưỡng, đặt lịch hay đơn hàng nhé."
)

FALLBACK_LOCATION = "chưa rõ — gọi lại gấp"
CALLBACK_PLACEHOLDER_RE = re.compile(r"^\[PHONE_(KH|\d+)\]$")


def sanitize_callback(value) -> str:
    """Only placeholder tokens may reach the ticket — anything else (raw digits,
    prose, null) falls back to the registered number placeholder."""
    if isinstance(value, str) and CALLBACK_PLACEHOLDER_RE.match(value.strip()):
        return value.strip()
    return "[PHONE_KH]"


def build_emergency_node(deps):
    async def trace(state, step_type, payload, **kw):
        await deps.trace(
            state.get("conversation_id"),
            step_type,
            payload,
            prompt_version=deps.prompt_version,
            policy_version=deps.policy_version,
            **kw,
        )

    def tool_trace(state):
        async def t(step_type, payload, latency_ms=None):
            await trace(state, step_type, payload, latency_ms=latency_ms)

        return t

    async def extract(state) -> dict:
        from app.graph.core import extract_json_object

        start = time.perf_counter()
        result = await deps.llm.complete(
            model=MODEL_HAIKU,
            system=EMERGENCY_EXTRACT_SYSTEM,
            messages=[{"role": "user", "content": state["masked_text"]}],
            max_tokens=200,
            json_mode=True,
        )
        await trace(
            state,
            "llm_call",
            {
                "purpose": "emergency_extract",
                "model": MODEL_HAIKU,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
            },
            latency_ms=result.latency_ms or int((time.perf_counter() - start) * 1000),
            cost_usd=result.cost_usd,
        )
        return extract_json_object(result.text) or {}

    async def open_ticket(state, location: str, callback: str) -> dict:
        vehicles = (state.get("customer_profile") or {}).get("vehicles") or []
        return await run_tool(
            tool_trace(state),
            "create_rescue_ticket",
            deps.tools.create_rescue_ticket,
            conversation_id=state.get("conversation_id"),
            location=location,
            callback_placeholder=callback,
            vehicle=vehicles[0] if vehicles else None,
            note=state["masked_text"],  # masked — never raw PII
        )

    async def emergency(state) -> dict:
        session = dict(state.get("emergency_session") or {})

        if not session.get("open"):
            # step 1 — template only, zero LLM
            await trace(state, "escalation", {"reason": "emergency", "step": 1})
            return {
                "reply": REPLY_EMERGENCY_STEP1,
                "escalated": True,
                "intent": "emergency",
                "emergency_session": {"open": True, "asks": 1},
                "reply_branch": "template",
            }

        # step 2 — one Haiku extract per turn
        data = await extract(state)
        if data.get("confirm") is False:
            await trace(state, "escalation", {"reason": "emergency", "step": 2, "dismissed": True})
            return {
                "reply": REPLY_EMERGENCY_DISMISSED,
                "intent": "emergency",
                "emergency_session": {"open": False, "asks": 0},
                "reply_branch": "template",
            }

        location = data.get("location")
        if not location and session.get("asks", 1) < 2:
            return {
                "reply": REPLY_EMERGENCY_ASK_LOCATION,
                "escalated": True,
                "intent": "emergency",
                "emergency_session": {"open": True, "asks": session.get("asks", 1) + 1},
                "reply_branch": "template",
            }
        if not location:
            # fail-safe after 2 asks: a thin ticket beats no callback at all
            location = FALLBACK_LOCATION

        ticket = await open_ticket(state, location, sanitize_callback(data.get("callback_ref")))
        await trace(
            state,
            "escalation",
            {"reason": "emergency", "step": 2, "ticket_id": ticket.get("id")},
        )
        return {
            "reply": REPLY_EMERGENCY_TICKETED,
            "escalated": True,
            "intent": "emergency",
            "emergency_session": {"open": False, "asks": 0},
            "reply_branch": "template",
        }

    return emergency

"""LangGraph core: state, router (Haiku tạm — PhoBERT thay ở TIP-012a),
nhánh faq (RAG + groundedness) và chitchat thật, các nhánh khác stub có chủ đích.

build_graph(deps) closes over GraphDeps so tests inject FakeLLM/fake search/fake
trace — no API key, no DB needed.
"""

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, TypedDict

from langgraph.graph import END, StateGraph

from app.guardrails.pii import PIISession
from app.guardrails.pipeline import run_guardrail_in
from app.llm import MODEL_HAIKU, MODEL_SONNET, LLMClient

HOTLINE = "1900 1234"
# TODO: move threshold to policy_registry (TIP-007 reads it as policy data)
INJECTION_THRESHOLD = 0.5

INTENTS = [
    "faq",
    "booking",
    "order_lookup",
    "modify_booking",
    "emergency",
    "complaint",
    "chitchat",
    "out_of_scope",
]

ROUTER_SYSTEM = f"""Bạn là bộ phân loại intent cho trung tâm CSKH XeCare (dịch vụ xe máy + ô tô).
Phân loại tin nhắn khách vào ĐÚNG MỘT nhãn: {", ".join(INTENTS)}.
- faq: hỏi kiến thức về bảo dưỡng, giá, bảo hành, quy trình, chính sách
- booking: muốn đặt lịch bảo dưỡng/sửa chữa mới
- order_lookup: tra cứu đơn phụ tùng / trạng thái đơn hàng
- modify_booking: đổi/hủy lịch hẹn hoặc đơn hàng đã có
- emergency: tai nạn, hỏng xe nguy hiểm, cần cứu hộ gấp
- complaint: phàn nàn, khiếu nại về dịch vụ
- chitchat: chào hỏi, nói chuyện phiếm
- out_of_scope: ngoài phạm vi dịch vụ xe
Trả về DUY NHẤT một JSON object: {{"intent": "<nhãn>", "confidence": <0..1>}}"""

CHITCHAT_SYSTEM = """Bạn là tư vấn viên thân thiện của XeCare (dịch vụ bảo dưỡng xe máy & ô tô),
xưng "XeCare" hoặc "mình". Trả lời ngắn gọn, ấm áp, và khéo léo lái câu chuyện về
dịch vụ xe khi phù hợp. KHÔNG tư vấn chủ đề ngoài lĩnh vực xe."""

FAQ_ANSWER_INSTRUCTIONS = """
NHIỆM VỤ: trả lời câu hỏi của khách CHỈ DỰA TRÊN các trích đoạn tài liệu dưới đây.
- Không bịa thông tin ngoài trích đoạn. Thiếu thông tin thì nói rõ là chưa có.
- Mọi con số về giá BẮT BUỘC kèm chữ "tham khảo" hoặc "ước tính".
- Trả lời tiếng Việt, ngắn gọn, đúng trọng tâm.
"""

GROUNDEDNESS_SYSTEM = """Bạn là bộ kiểm tra groundedness. Cho câu hỏi, các trích đoạn nguồn,
và câu trả lời. Kiểm tra: mọi khẳng định trong câu trả lời có được các trích đoạn hỗ trợ không?
Trả về DUY NHẤT JSON: {"supported": true} hoặc {"supported": false}"""

REPLY_EMERGENCY = (
    f"Anh/chị ơi, an toàn là quan trọng nhất lúc này: nếu đang trên đường, hãy bật đèn "
    f"cảnh báo, di chuyển người ra khỏi lòng đường nếu có thể và đứng ở vị trí an toàn. "
    f"Anh/chị gọi ngay hotline cứu hộ 24/7 của XeCare: {HOTLINE} để được điều phối hỗ trợ "
    f"ngay lập tức ạ."
)
REPLY_INJECTION = (
    "Xin lỗi anh/chị, mình không thể hỗ trợ yêu cầu này. XeCare có thể giúp anh/chị về "
    "bảo dưỡng, đặt lịch, tra cứu đơn hàng hoặc cứu hộ — anh/chị cần hỗ trợ gì ạ?"
)
REPLY_ESCALATE = (
    f"Để hỗ trợ anh/chị chính xác nhất, mình xin phép chuyển cho nhân viên tư vấn. "
    f"Anh/chị vui lòng chờ trong giây lát, hoặc gọi hotline {HOTLINE} để được hỗ trợ ngay ạ."
)
REPLY_COMPLAINT_STUB = (
    "Mình rất tiếc về trải nghiệm chưa tốt của anh/chị và chân thành xin lỗi. Mình đã ghi "
    "nhận phản ánh và sẽ chuyển ngay cho bộ phận phụ trách xem xét; XeCare sẽ liên hệ lại "
    "anh/chị sớm nhất ạ."
)
REPLY_OUT_OF_SCOPE = (
    "Xin lỗi anh/chị, câu hỏi này nằm ngoài phạm vi hỗ trợ của XeCare. Mình có thể giúp "
    "anh/chị về bảo dưỡng xe, đặt lịch, đơn phụ tùng, bảo hành và cứu hộ ạ."
)
REPLY_NOT_FOUND_PREFIX = "Mình chưa tìm thấy thông tin chính xác về"


class AgentState(TypedDict, total=False):
    # Blueprint §5 state contract
    customer_profile: dict
    messages: list[dict]  # masked history (role/content), current turn appended by API
    intent: str
    confidence: float
    slots: dict
    retrieved_chunks: list
    pending_action: Any
    guardrail_flags: dict
    mode: str
    # plumbing required to execute a turn (not part of the conceptual contract)
    conversation_id: str
    customer_id: str  # profile UUID — tools check ownership against it
    raw_text: str
    masked_text: str
    pii_session: PIISession
    reply: str
    citations: list[dict]
    escalated: bool
    router_failed: bool


@dataclass
class GraphDeps:
    llm: LLMClient
    system_prompt: str
    prompt_version: int
    policy: dict
    policy_version: int
    search: Callable[..., Awaitable[list]]  # search_kb-compatible
    trace: Callable[..., Awaitable[None]]  # log_trace-compatible
    tools: Any = None  # ToolKit (TIP-006) — fakes/spies in tests
    extra: dict = field(default_factory=dict)


def extract_json_object(text: str) -> dict | None:
    """Parse the first JSON object in text — tolerates prose around it."""
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        match = re.search(r"\{.*?\}", text, flags=re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None


def parse_router_json(text: str) -> tuple[str, float] | None:
    data = extract_json_object(text)
    if not isinstance(data, dict):
        return None
    try:
        intent = data["intent"]
        confidence = float(data["confidence"])
        if intent not in INTENTS:
            return None
        return intent, max(0.0, min(1.0, confidence))
    except (KeyError, TypeError, ValueError):
        return None


def build_graph(deps: GraphDeps):
    threshold = float(deps.policy.get("escalate_confidence_below", 0.7))

    async def trace(state: AgentState, step_type: str, payload: dict, **kw) -> None:
        await deps.trace(
            state.get("conversation_id"),
            step_type,
            payload,
            prompt_version=deps.prompt_version,
            policy_version=deps.policy_version,
            **kw,
        )

    async def llm_call(state: AgentState, purpose: str, **kwargs):
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

    # ---------- nodes ----------

    async def guardrail_in(state: AgentState) -> dict:
        # pre_gate ran on raw text in route_entry; here: mask + injection score
        result = run_guardrail_in(state["raw_text"], state["pii_session"])
        flags = {
            "emergency": result.emergency,
            "injection_score": result.injection_score,
            "pii_found": result.pii_found,
        }
        await trace(state, "guardrail_in", flags)
        return {"masked_text": result.masked_text, "guardrail_flags": flags}

    async def router(state: AgentState) -> dict:
        parsed = None
        for _ in range(2):  # one retry on broken JSON
            result = await llm_call(
                state,
                "router",
                model=MODEL_HAIKU,
                system=ROUTER_SYSTEM,
                messages=[{"role": "user", "content": state["masked_text"]}],
                max_tokens=100,
                json_mode=True,
            )
            parsed = parse_router_json(result.text)
            if parsed:
                break
        if parsed is None:
            intent, confidence, failed = "out_of_scope", 0.0, True
        else:
            intent, confidence, failed = parsed[0], parsed[1], False
        await trace(state, "router", {"intent": intent, "confidence": confidence})
        return {"intent": intent, "confidence": confidence, "router_failed": failed}

    async def faq(state: AgentState) -> dict:
        chunks = await deps.search(state["masked_text"], top_k=5)
        await trace(
            state,
            "retrieval",
            {
                "chunk_ids": [c.id for c in chunks],
                "scores": [c.score for c in chunks],
            },
        )
        if not chunks:
            return {
                "reply": f"{REPLY_NOT_FOUND_PREFIX} vấn đề này. Anh/chị có thể gọi "
                f"{HOTLINE} để được tư vấn trực tiếp ạ.",
                "retrieved_chunks": [],
                "citations": [],
            }

        context = "\n\n---\n\n".join(
            f"[Nguồn {i + 1}] (tài liệu: {c.doc_id} · mục: {c.heading})\n{c.content}"
            for i, c in enumerate(chunks)
        )
        question = state["masked_text"]
        answer = await llm_call(
            state,
            "faq_answer",
            model=MODEL_SONNET,
            system=deps.system_prompt + FAQ_ANSWER_INSTRUCTIONS,
            messages=[
                {
                    "role": "user",
                    "content": f"TRÍCH ĐOẠN TÀI LIỆU:\n{context}\n\nCÂU HỎI: {question}",
                }
            ],
            max_tokens=1000,
        )

        grounded = await llm_call(
            state,
            "groundedness",
            model=MODEL_HAIKU,
            system=GROUNDEDNESS_SYSTEM,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"CÂU HỎI: {question}\n\nTRÍCH ĐOẠN:\n{context}\n\n"
                        f"CÂU TRẢ LỜI: {answer.text}"
                    ),
                }
            ],
            max_tokens=100,
            json_mode=True,
        )
        verdict = extract_json_object(grounded.text)
        supported = bool(verdict.get("supported")) if isinstance(verdict, dict) else False

        if not supported:
            return {
                "reply": f"{REPLY_NOT_FOUND_PREFIX} câu hỏi này trong tài liệu của XeCare. "
                f"Anh/chị gọi {HOTLINE} để được tư vấn chính xác nhé ạ.",
                "retrieved_chunks": [c.id for c in chunks],
                "citations": [],
            }

        seen = set()
        citations = []
        for c in chunks[:3]:
            key = (c.doc_id, c.heading)
            if key not in seen:
                seen.add(key)
                citations.append({"doc_id": c.doc_id, "heading": c.heading})
        return {
            "reply": answer.text,
            "retrieved_chunks": [c.id for c in chunks],
            "citations": citations,
        }

    async def chitchat(state: AgentState) -> dict:
        history = state.get("messages", [])[-6:]
        result = await llm_call(
            state,
            "chitchat",
            model=MODEL_HAIKU,
            system=CHITCHAT_SYSTEM,
            messages=history + [{"role": "user", "content": state["masked_text"]}],
            max_tokens=300,
        )
        return {"reply": result.text}

    async def emergency_stub(state: AgentState) -> dict:
        # TIP-007 creates the real ticket; v1 replies safety-first + hotline only
        await trace(state, "escalation", {"reason": "emergency"})
        return {"reply": REPLY_EMERGENCY, "escalated": True, "intent": "emergency"}

    async def escalate_stub(state: AgentState) -> dict:
        await trace(
            state,
            "escalation",
            {"reason": "low_confidence", "confidence": state.get("confidence")},
        )
        return {"reply": REPLY_ESCALATE, "escalated": True}

    async def injection_refuse(state: AgentState) -> dict:
        await trace(
            state,
            "escalation",
            {
                "reason": "injection",
                "score": state["guardrail_flags"]["injection_score"],
            },
        )
        return {"reply": REPLY_INJECTION, "escalated": False}

    async def complaint_stub(state: AgentState) -> dict:
        return {"reply": REPLY_COMPLAINT_STUB}

    async def out_of_scope(state: AgentState) -> dict:
        return {"reply": REPLY_OUT_OF_SCOPE}

    # ---------- wiring ----------

    def route_after_guardrail(state: AgentState) -> str:
        if state["guardrail_flags"]["emergency"]:
            return "emergency_stub"
        if state["guardrail_flags"]["injection_score"] >= INJECTION_THRESHOLD:
            return "injection_refuse"
        # TIP-006: an in-flight action (choosing a slot / awaiting confirm) continues
        # in the action node — router would misread bare replies like "2"
        if state.get("pending_action"):
            return "action"
        return "router"

    def route_after_router(state: AgentState) -> str:
        if state.get("router_failed"):
            return "escalate_stub"  # TIP-006 chore: parse failure goes to a human
        if state["intent"] == "emergency":
            return "emergency_stub"
        if state["confidence"] < threshold:
            return "escalate_stub"
        return {
            "faq": "faq",
            "chitchat": "chitchat",
            "booking": "action",
            "order_lookup": "action",
            "modify_booking": "action",
            "complaint": "complaint_stub",
            "out_of_scope": "out_of_scope",
        }[state["intent"]]

    # deferred import: action.py imports constants from this module
    from app.graph.action import build_action_node

    graph = StateGraph(AgentState)
    graph.add_node("guardrail_in", guardrail_in)
    graph.add_node("router", router)
    graph.add_node("faq", faq)
    graph.add_node("chitchat", chitchat)
    graph.add_node("emergency_stub", emergency_stub)
    graph.add_node("escalate_stub", escalate_stub)
    graph.add_node("injection_refuse", injection_refuse)
    graph.add_node("action", build_action_node(deps))
    graph.add_node("complaint_stub", complaint_stub)
    graph.add_node("out_of_scope", out_of_scope)

    graph.set_entry_point("guardrail_in")
    graph.add_conditional_edges("guardrail_in", route_after_guardrail)
    graph.add_conditional_edges("router", route_after_router)
    for node in [
        "faq",
        "chitchat",
        "emergency_stub",
        "escalate_stub",
        "injection_refuse",
        "action",
        "complaint_stub",
        "out_of_scope",
    ]:
        graph.add_edge(node, END)

    return graph.compile()

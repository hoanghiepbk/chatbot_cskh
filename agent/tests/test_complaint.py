"""TIP-008 complaint node tests (FakeLLM, no DB). REQ-09: one attempt then escalate."""

import pytest

from app.graph.core import GraphDeps, build_graph
from app.graph.escalate import REPLY_IN_HOURS
from app.guardrails.pii import PIISession
from app.llm import MODEL_HAIKU, MODEL_SONNET, LLMResult

CLEAN_RUBRIC = (
    '{"promises_outside_policy": false, "unsafe_advice": false, '
    '"reveals_internal": false, "off_domain": false}'
)
HANDOFF_JSON = '{"summary": "khách khiếu nại", "suggested_action": "gọi lại"}'


class FakeChunk:
    def __init__(self, heading, content):
        self.heading = heading
        self.content = content
        self.doc_id = "08-faq-chung.md"
        self.id = "c1"


class FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def complete(self, model, system, messages, max_tokens, json_mode=False):
        self.calls.append({"model": model, "system": system, "json_mode": json_mode})
        text = self.responses.pop(0) if self.responses else "{}"
        return LLMResult(text=text, input_tokens=10, output_tokens=5, cost_usd=0.0, latency_ms=1)


def make_deps(llm, chunks=None):
    traces = []

    async def fake_search(query, top_k=5):
        return chunks or []

    async def fake_trace(conversation_id, step_type, payload, **kw):
        traces.append({"step_type": step_type, "payload": payload, **kw})

    deps = GraphDeps(
        llm=llm, system_prompt="SYS", prompt_version=2,
        policy={"escalate_confidence_below": 0.7, "injection_threshold": 0.5},
        policy_version=2, search=fake_search, trace=fake_trace,
    )
    return deps, traces


def base_state(text, complaint_attempted=False):
    return {
        "conversation_id": None,
        "customer_profile": {"display_name": "Anh A", "vehicles": []},
        "messages": [],
        "raw_text": text,
        "pii_session": PIISession(),
        "slots": {},
        "guardrail_flags": {},
        "complaint_attempted": complaint_attempted,
        "mode": "agent",
    }


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ---------- turn 1: resolve without KB (Haiku reply) ----------

@pytest.mark.anyio
async def test_complaint_turn1_resolves_no_kb():
    llm = FakeLLM(
        [
            '{"intent": "complaint", "confidence": 0.9}',
            '{"topic": "thái độ nhân viên", "needs_kb": false, "severity": "low"}',
            "Dạ mình rất xin lỗi anh/chị về trải nghiệm chưa tốt ạ...",
            CLEAN_RUBRIC,
        ]
    )
    deps, traces = make_deps(llm)
    final = await build_graph(deps).ainvoke(base_state("nhân viên thái độ quá"))
    assert "xin lỗi" in final["reply"].lower()
    assert final["complaint_attempted"] is True
    assert not any(t["step_type"] == "escalation" for t in traces)  # no escalate on turn 1
    # budget: analyze + resolve = 2 complaint calls (router + rubric are other nodes)
    resolve = [c for c in llm.calls if c["model"] == MODEL_HAIKU and not c["json_mode"]]
    assert len(resolve) == 1  # Haiku reply, no Sonnet


# ---------- turn 1: needs KB → Sonnet reply ----------

@pytest.mark.anyio
async def test_complaint_turn1_needs_kb_uses_sonnet():
    chunks = [FakeChunk("Bảo hành", "Chính sách bảo hành 6 tháng")]
    llm = FakeLLM(
        [
            '{"intent": "complaint", "confidence": 0.9}',
            '{"topic": "bảo hành", "needs_kb": true, "severity": "medium"}',
            "Theo chính sách bảo hành, anh/chị được hỗ trợ ạ...",
            CLEAN_RUBRIC,
        ]
    )
    deps, _ = make_deps(llm, chunks=chunks)
    final = await build_graph(deps).ainvoke(base_state("xe bảo hành mà không sửa được"))
    assert "bảo hành" in final["reply"].lower()
    assert any(c["model"] == MODEL_SONNET for c in llm.calls)  # Sonnet for KB-grounded reply


# ---------- turn 1 high severity → escalate immediately ----------

@pytest.mark.anyio
async def test_complaint_high_severity_escalates_first_turn(monkeypatch):
    import app.graph.escalate as esc

    monkeypatch.setattr(esc, "is_business_hours", lambda *a: True)
    llm = FakeLLM(
        [
            '{"intent": "complaint", "confidence": 0.9}',
            '{"topic": "tai nạn do sửa sai", "needs_kb": false, "severity": "high"}',
            HANDOFF_JSON,
        ]
    )
    deps, traces = make_deps(llm)
    # high severity = legal threat (no emergency keywords, so pre_gate stays off)
    final = await build_graph(deps).ainvoke(
        base_state("dịch vụ quá tệ, tôi sẽ kiện các anh ra tòa và đòi bồi thường")
    )
    assert final["reply"] == REPLY_IN_HOURS
    assert final["escalated"] is True
    esc_trace = next(t for t in traces if t["step_type"] == "escalation")
    assert esc_trace["payload"]["reason"] == "complaint"


# ---------- turn 2 (already attempted) → escalate ----------

@pytest.mark.anyio
async def test_complaint_second_time_escalates(monkeypatch):
    import app.graph.escalate as esc

    monkeypatch.setattr(esc, "is_business_hours", lambda *a: True)
    llm = FakeLLM(
        [
            '{"intent": "complaint", "confidence": 0.9}',
            '{"topic": "vẫn chưa được", "needs_kb": false, "severity": "low"}',
            HANDOFF_JSON,
        ]
    )
    deps, traces = make_deps(llm)
    final = await build_graph(deps).ainvoke(
        base_state("vẫn chưa ổn, tôi muốn gặp người", complaint_attempted=True)
    )
    assert final["reply"] == REPLY_IN_HOURS
    esc_trace = next(t for t in traces if t["step_type"] == "escalation")
    assert esc_trace["payload"]["reason"] == "complaint"

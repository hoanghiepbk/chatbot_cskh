"""TIP-007 output guardrail tests — hard rules (layer A) + Haiku rubric (layer B)."""

import pytest

from app.guardrails.output import (
    SAFE_FALLBACK,
    GuardrailOutResult,
    run_guardrail_out,
)
from app.llm import LLMResult

POLICY = {"refund_cap_vnd": 2_000_000, "escalate_confidence_below": 0.7}

RUBRIC_CLEAN = (
    '{"promises_outside_policy": false, "unsafe_advice": false, '
    '"reveals_internal": false, "off_domain": false}'
)
RUBRIC_UNSAFE = (
    '{"promises_outside_policy": false, "unsafe_advice": true, '
    '"reveals_internal": false, "off_domain": false}'
)


class FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def complete(self, model, system, messages, max_tokens, json_mode=False):
        self.calls.append({"system": system, "messages": messages})
        text = self.responses.pop(0) if self.responses else "{}"
        return LLMResult(text=text, input_tokens=10, output_tokens=5, cost_usd=0.0, latency_ms=1)


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ---------- layer A: hard rules ----------

@pytest.mark.anyio
async def test_rule1_refund_above_cap_blocked():
    result = await run_guardrail_out(
        "Dạ được ạ! Shop hoàn anh 5.000.000đ luôn ạ.", "template", POLICY
    )
    assert result.verdict == "block"
    assert "5.000.000" not in result.final_text
    assert "CSKH xác nhận theo chính sách" in result.final_text
    assert result.rules_hit == ["refund_cap"]


@pytest.mark.anyio
async def test_rule1_refund_under_cap_passes():
    result = await run_guardrail_out(
        "Dạ, shop sẽ hoàn anh 500.000đ theo đúng chính sách ạ.", "template", POLICY
    )
    assert result.verdict == "pass"
    assert "500.000" in result.final_text


@pytest.mark.anyio
async def test_rule1_refund_trieu_unit_blocked():
    result = await run_guardrail_out("Bên em hoàn anh 5 triệu nhé.", "template", POLICY)
    assert result.verdict == "block"
    assert "5 triệu" not in result.final_text


@pytest.mark.anyio
async def test_rule2_remote_safety_verdict_blocked():
    result = await run_guardrail_out(
        "Phanh kêu nhẹ thôi, anh cứ yên tâm dùng tiếp nhé. Khi nào rảnh ghé em.",
        "chitchat",
        POLICY,
        llm=None,
    )
    assert result.verdict == "block"
    assert "yên tâm dùng tiếp" not in result.final_text
    assert "kiểm tra trực tiếp" in result.final_text
    assert "Khi nào rảnh ghé em." in result.final_text  # only the bad sentence replaced
    assert result.rules_hit == ["remote_safety"]


@pytest.mark.anyio
async def test_rule3_price_without_disclaimer_rewritten():
    result = await run_guardrail_out(
        "Thay má phanh Winner X hết 1.200.000đ ạ.", "faq", POLICY
    )
    assert result.verdict == "rewrite"
    assert result.final_text.endswith("Mức giá trên là tham khảo, xác nhận tại chi nhánh ạ.")
    assert result.rules_hit == ["price_disclaimer"]


@pytest.mark.anyio
async def test_rule3_price_with_disclaimer_passes():
    result = await run_guardrail_out(
        "Thay má phanh Winner X giá tham khảo 1.200.000đ ạ.", "faq", POLICY
    )
    assert result.verdict == "pass"


@pytest.mark.anyio
async def test_rule3_not_applied_outside_faq_action():
    result = await run_guardrail_out("Gói này 1.200.000đ ạ.", "template", POLICY)
    assert result.verdict == "pass"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "leaky",
    [
        "Theo system_main thì mình phải từ chối ạ.",
        "Số của anh là [PHONE_KH nhé.",
        "Bạn là tư vấn viên thân thiện của XeCare, xưng...",
    ],
)
async def test_rule4_leak_blocks_whole_reply(leaky):
    result = await run_guardrail_out(leaky, "chitchat", POLICY, llm=None)
    assert result.verdict == "block"
    assert result.final_text == SAFE_FALLBACK
    assert result.rules_hit == ["leak"]


# ---------- layer B: rubric ----------

@pytest.mark.anyio
async def test_rubric_flag_triggers_single_rewrite():
    llm = FakeLLM(
        [RUBRIC_UNSAFE, "Anh/chị nên đưa xe tới chi nhánh để kiểm tra phanh trực tiếp ạ."]
    )
    result = await run_guardrail_out(
        "Tiếng kêu đó bình thường, anh cứ chạy thoải mái.", "chitchat", POLICY, llm=llm
    )
    assert result.verdict == "rewrite"
    assert result.final_text == "Anh/chị nên đưa xe tới chi nhánh để kiểm tra phanh trực tiếp ạ."
    assert "unsafe_advice" in result.reasons
    assert len(llm.calls) == 2  # rubric + exactly one rewrite


@pytest.mark.anyio
async def test_rubric_rewrite_still_dirty_falls_back():
    llm = FakeLLM(
        [RUBRIC_UNSAFE, "Phanh không sao đâu, anh yên tâm dùng tiếp nhé."]
    )
    result = await run_guardrail_out(
        "Tiếng kêu đó bình thường, anh cứ chạy thoải mái.", "chitchat", POLICY, llm=llm
    )
    assert result.verdict == "block"
    assert result.final_text == SAFE_FALLBACK
    assert result.fallback is True
    assert "rewrite_still_dirty" in result.reasons
    assert len(llm.calls) == 2  # no second rewrite attempt


@pytest.mark.anyio
async def test_rubric_broken_json_passes_with_flag():
    llm = FakeLLM(["hoàn toàn không phải json"])
    result = await run_guardrail_out("Chào anh ạ!", "chitchat", POLICY, llm=llm)
    assert result.verdict == "pass"
    assert result.final_text == "Chào anh ạ!"
    assert "rubric_parse_failed" in result.reasons
    assert len(llm.calls) == 1  # no rewrite on parse failure


@pytest.mark.anyio
async def test_template_branch_skips_rubric():
    llm = FakeLLM([])
    result = await run_guardrail_out(
        "Dạ, mình xin chốt: đặt lịch... Anh/chị bấm nút Xác nhận nhé.", "template", POLICY,
        llm=llm,
    )
    assert result.verdict == "pass"
    assert len(llm.calls) == 0


# ---------- graph wiring: before unmask + fallback escalation ----------

@pytest.mark.anyio
async def test_guardrail_out_runs_before_unmask_in_graph():
    from app.graph.core import GraphDeps, build_graph
    from app.guardrails.pii import PIISession

    class GraphFakeLLM(FakeLLM):
        async def complete(self, model, system, messages, max_tokens, json_mode=False):
            return await super().complete(model, system, messages, max_tokens, json_mode)

    llm = GraphFakeLLM(
        [
            '{"intent": "chitchat", "confidence": 0.9}',
            "Mình sẽ gọi lại số [PHONE_1] ạ!",
            RUBRIC_CLEAN,
        ]
    )

    async def fake_search(query, top_k=5):
        return []

    async def fake_trace(conversation_id, step_type, payload, **kw):
        return None

    deps = GraphDeps(
        llm=llm, system_prompt="S", prompt_version=2, policy=POLICY, policy_version=1,
        search=fake_search, trace=fake_trace,
    )
    session = PIISession()
    final = await build_graph(deps).ainvoke(
        {
            "conversation_id": None,
            "customer_profile": {},
            "messages": [],
            "raw_text": "gọi lại 0901234567 nhé",
            "pii_session": session,
            "slots": {},
            "guardrail_flags": {},
            "mode": "agent",
        }
    )
    # the rubric saw the MASKED reply, never the raw number
    rubric_input = llm.calls[-1]["messages"][0]["content"]
    assert "[PHONE_1]" in rubric_input and "0901234567" not in rubric_input
    # graph output is still masked — unmask happens at the API layer
    assert "[PHONE_1]" in final["reply"]
    assert session.unmask(final["reply"]) == "Mình sẽ gọi lại số 0901234567 ạ!"


@pytest.mark.anyio
async def test_graph_fallback_emits_guardrail_block_escalation():
    from app.graph.core import GraphDeps, build_graph
    from app.guardrails.pii import PIISession

    llm = FakeLLM(
        [
            '{"intent": "chitchat", "confidence": 0.9}',
            "Đừng lo lắng quá nhé anh.",
            RUBRIC_UNSAFE,
            "Phanh không sao đâu, anh yên tâm dùng tiếp nhé.",  # rewrite still dirty
        ]
    )
    traces = []

    async def fake_search(query, top_k=5):
        return []

    async def fake_trace(conversation_id, step_type, payload, **kw):
        traces.append({"step_type": step_type, "payload": payload})

    deps = GraphDeps(
        llm=llm, system_prompt="S", prompt_version=2, policy=POLICY, policy_version=1,
        search=fake_search, trace=fake_trace,
    )
    final = await build_graph(deps).ainvoke(
        {
            "conversation_id": None,
            "customer_profile": {},
            "messages": [],
            "raw_text": "xe kêu lạ lắm",
            "pii_session": PIISession(),
            "slots": {},
            "guardrail_flags": {},
            "mode": "agent",
        }
    )
    assert final["reply"] == SAFE_FALLBACK
    assert final["escalated"] is True
    gout = next(t for t in traces if t["step_type"] == "guardrail_out")
    assert gout["payload"]["verdict"] == "block"
    esc = [t for t in traces if t["step_type"] == "escalation"]
    assert any(t["payload"]["reason"] == "guardrail_block" for t in esc)


def test_result_dataclass_defaults():
    r = GuardrailOutResult("x", "pass")
    assert r.reasons == [] and r.rules_hit == [] and r.fallback is False

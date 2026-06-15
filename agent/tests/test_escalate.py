"""TIP-008 escalate node + handoff unit tests (FakeLLM, no DB)."""

from datetime import datetime, timedelta, timezone

import pytest

from app.graph.escalate import build_handoff, is_business_hours
from app.llm import LLMResult

VN = timezone(timedelta(hours=7))


class FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def complete(self, model, system, messages, max_tokens, json_mode=False):
        self.calls.append({"system": system, "messages": messages})
        text = self.responses.pop(0) if self.responses else "{}"
        return LLMResult(text=text, input_tokens=10, output_tokens=5, cost_usd=0.0, latency_ms=1)


def make_deps(llm):
    traces = []

    async def fake_trace(conversation_id, step_type, payload, **kw):
        traces.append({"step_type": step_type, "payload": payload, **kw})

    class D:
        pass

    d = D()
    d.llm = llm
    d.trace = fake_trace
    d.prompt_version = 2
    d.policy_version = 2
    d.supabase = None
    return d, traces


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ---------- business hours (KB-07) ----------

@pytest.mark.parametrize(
    "dt,expected",
    [
        (datetime(2026, 6, 15, 10, 0, tzinfo=VN), True),   # Mon 10:00
        (datetime(2026, 6, 15, 7, 30, tzinfo=VN), False),  # Mon before open
        (datetime(2026, 6, 15, 18, 0, tzinfo=VN), False),  # Mon at close
        (datetime(2026, 6, 20, 17, 0, tzinfo=VN), True),   # Sat 17:00
        (datetime(2026, 6, 21, 11, 0, tzinfo=VN), True),   # Sun 11:00 (morning)
        (datetime(2026, 6, 21, 15, 0, tzinfo=VN), False),  # Sun 15:00 (afternoon closed)
    ],
)
def test_is_business_hours(dt, expected):
    assert is_business_hours(dt) is expected


# ---------- handoff package shape (no DB → masked profile only) ----------

@pytest.mark.anyio
async def test_build_handoff_has_seven_fields_and_no_phone():
    llm = FakeLLM(['{"summary": "khách bực vì xe vẫn kêu", "suggested_action": "gọi lại"}'])
    deps, traces = make_deps(llm)
    state = {
        "conversation_id": "c1",
        "masked_text": "xe vẫn kêu, gọi [PHONE_KH]",
        "customer_profile": {
            "display_name": "Anh Minh",
            "vehicles": [{"model": "VinFast Lux A"}],
            "facts": {"phone": "should-not-leak"},
        },
    }
    handoff = await build_handoff(deps, state, "complaint", "normal")
    assert set(handoff) == {
        "reason", "summary", "customer", "recent_messages",
        "intents", "tool_calls", "suggested_action",
    }
    assert handoff["reason"] == "complaint"
    assert handoff["summary"] == "khách bực vì xe vẫn kêu"
    assert handoff["suggested_action"] == "gọi lại"
    # customer carries display_name + vehicles, never a phone
    assert handoff["customer"] == {"display_name": "Anh Minh", "vehicles": [{"model": "VinFast Lux A"}]}
    assert "phone" not in handoff["customer"]
    # 1 Haiku call traced
    assert [t for t in traces if t["step_type"] == "llm_call"][0]["payload"]["purpose"] == "handoff_summary"

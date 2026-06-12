"""TIP-007 emergency node tests — FakeLLM + spy rescue tool. No API key, no DB.

Hard guarantees under test: step 1 is template-only (0 LLM), step 2 never lets a
raw phone number into the ticket, dismissal closes the session, and the fail-safe
ticket fires after two unanswered location asks.
"""

import json
import re

import pytest

from app.graph.core import GraphDeps, build_graph
from app.graph.emergency import (
    FALLBACK_LOCATION,
    REPLY_EMERGENCY_ASK_LOCATION,
    REPLY_EMERGENCY_DISMISSED,
    REPLY_EMERGENCY_STEP1,
    REPLY_EMERGENCY_TICKETED,
    sanitize_callback,
)
from app.guardrails.pii import PIISession
from app.llm import LLMResult
from app.tools.registry import ToolKit


class FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def complete(self, model, system, messages, max_tokens, json_mode=False):
        self.calls.append({"model": model, "system": system, "messages": messages})
        text = self.responses.pop(0) if self.responses else "{}"
        return LLMResult(text=text, input_tokens=10, output_tokens=5, cost_usd=0.0, latency_ms=1)


def make_tools(tickets):
    async def _empty(*a, **kw):
        return []

    async def create_rescue_ticket(
        conversation_id, location, callback_placeholder, vehicle, note
    ):
        ticket = {
            "id": f"t-rescue-{len(tickets) + 1}",
            "type": "rescue",
            "priority": "urgent",
            "status": "open",
            "payload": {
                "location": location,
                "callback_placeholder": callback_placeholder,
                "vehicle": vehicle,
                "note": note,
                "conversation_id": conversation_id,
            },
        }
        tickets.append(ticket)
        return ticket

    return ToolKit(
        get_customer_orders=_empty,
        find_free_slots=_empty,
        get_customer_bookings=_empty,
        book_slot=_empty,
        cancel_booking=_empty,
        cancel_parts_order=_empty,
        create_rescue_ticket=create_rescue_ticket,
    )


def make_deps(llm, tools):
    traces = []

    async def fake_search(query, top_k=5):
        return []

    async def fake_trace(conversation_id, step_type, payload, **kw):
        traces.append({"step_type": step_type, "payload": payload, **kw})

    deps = GraphDeps(
        llm=llm,
        system_prompt="S",
        prompt_version=2,
        policy={"escalate_confidence_below": 0.7, "refund_cap_vnd": 2_000_000},
        policy_version=1,
        search=fake_search,
        trace=fake_trace,
        tools=tools,
    )
    return deps, traces


def base_state(text, emergency_session=None, session=None):
    return {
        "conversation_id": "conv-1",
        "customer_id": "c-1",
        "customer_profile": {
            "vehicles": [{"type": "motorbike", "model": "Honda Winner X", "last_km": 19500}],
            "facts": {},
        },
        "messages": [],
        "raw_text": text,
        "pii_session": session or PIISession(),
        "slots": {},
        "guardrail_flags": {},
        "emergency_session": emergency_session or {"open": False, "asks": 0},
        "mode": "agent",
    }


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ---------- two-turn happy path ----------

@pytest.mark.anyio
async def test_emergency_two_turns_creates_rescue_ticket():
    tickets = []
    tools = make_tools(tickets)

    # turn 1 — pre_gate fires, pure template, zero LLM
    llm = FakeLLM([])
    deps, traces = make_deps(llm, tools)
    final1 = await build_graph(deps).ainvoke(base_state("toi bi tai nan tren cao toc"))
    assert final1["reply"] == REPLY_EMERGENCY_STEP1
    assert "1900 1234" in final1["reply"] and "vị trí" in final1["reply"]
    assert len(llm.calls) == 0
    assert final1["emergency_session"] == {"open": True, "asks": 1}
    assert tickets == []
    esc1 = next(t for t in traces if t["step_type"] == "escalation")
    assert esc1["payload"] == {"reason": "emergency", "step": 1}

    # turn 2 — plain text, no emergency keywords: open session routes here anyway
    llm2 = FakeLLM(
        ['{"location": "đại lộ Thăng Long gần cầu vượt", "callback_ref": "[PHONE_KH]", '
         '"confirm": true}']
    )
    deps2, traces2 = make_deps(llm2, tools)
    final2 = await build_graph(deps2).ainvoke(
        base_state("đại lộ Thăng Long gần cầu vượt, gọi số cũ nhé",
                   emergency_session=final1["emergency_session"])
    )
    assert final2["reply"] == REPLY_EMERGENCY_TICKETED
    assert final2["emergency_session"]["open"] is False
    assert len(tickets) == 1
    payload = tickets[0]["payload"]
    assert payload["location"] == "đại lộ Thăng Long gần cầu vượt"
    assert payload["callback_placeholder"] == "[PHONE_KH]"
    assert payload["vehicle"]["model"] == "Honda Winner X"
    # no raw phone anywhere in the ticket
    assert not re.search(r"(?<!\d)(?:\+84|0)\d{9}(?!\d)", json.dumps(payload))
    tool_calls = [t for t in traces2 if t["step_type"] == "tool_call"]
    assert tool_calls[0]["payload"]["tool"] == "create_rescue_ticket"
    esc2 = next(t for t in traces2 if t["step_type"] == "escalation")
    assert esc2["payload"]["step"] == 2 and esc2["payload"]["ticket_id"] == "t-rescue-1"
    # reply commits to NO specific arrival time
    assert not re.search(r"\d+\s*phút", final2["reply"])


# ---------- dismissal ----------

@pytest.mark.anyio
async def test_emergency_dismissed_no_ticket_then_normal_routing():
    tickets = []
    tools = make_tools(tickets)
    llm = FakeLLM(['{"location": null, "callback_ref": null, "confirm": false}'])
    deps, _ = make_deps(llm, tools)
    final = await build_graph(deps).ainvoke(
        base_state("không sao đâu, mình hỏi phí cứu hộ thôi mà",
                   emergency_session={"open": True, "asks": 1})
    )
    assert final["reply"] == REPLY_EMERGENCY_DISMISSED
    assert final["emergency_session"]["open"] is False
    assert tickets == []

    # next turn routes through the router again (session closed)
    llm2 = FakeLLM(['{"intent": "chitchat", "confidence": 0.9}', "Dạ, XeCare nghe ạ!"])
    deps2, _ = make_deps(llm2, tools)
    final2 = await build_graph(deps2).ainvoke(
        base_state("cho mình hỏi giờ mở cửa", emergency_session=final["emergency_session"])
    )
    assert final2["reply"] == "Dạ, XeCare nghe ạ!"
    assert any("phân loại intent" in c["system"] for c in llm2.calls)  # router ran


# ---------- fail-safe after two unanswered asks ----------

@pytest.mark.anyio
async def test_emergency_unknown_location_failsafe_ticket():
    tickets = []
    tools = make_tools(tickets)

    # first step-2 turn: still no location → re-ask, no ticket yet
    llm = FakeLLM(['{"location": null, "callback_ref": null, "confirm": true}'])
    deps, _ = make_deps(llm, tools)
    mid = await build_graph(deps).ainvoke(
        base_state("xe chết máy giữa đường rồi", emergency_session={"open": True, "asks": 1})
    )
    assert mid["reply"] == REPLY_EMERGENCY_ASK_LOCATION
    assert mid["emergency_session"] == {"open": True, "asks": 2}
    assert tickets == []

    # second step-2 turn: still nothing → fail-safe ticket
    llm2 = FakeLLM(['{"location": null, "callback_ref": null, "confirm": true}'])
    deps2, _ = make_deps(llm2, tools)
    final = await build_graph(deps2).ainvoke(
        base_state("mình không rành đường ở đây", emergency_session=mid["emergency_session"])
    )
    assert final["reply"] == REPLY_EMERGENCY_TICKETED
    assert len(tickets) == 1
    assert tickets[0]["payload"]["location"] == FALLBACK_LOCATION
    assert final["emergency_session"]["open"] is False


# ---------- callback sanitizer never lets raw digits through ----------

def test_sanitize_callback():
    assert sanitize_callback("[PHONE_KH]") == "[PHONE_KH]"
    assert sanitize_callback("[PHONE_2]") == "[PHONE_2]"
    assert sanitize_callback("0901234567") == "[PHONE_KH]"
    assert sanitize_callback("gọi số [PHONE_1] nhé") == "[PHONE_KH]"
    assert sanitize_callback(None) == "[PHONE_KH]"

"""TIP-006 action agent tests — FakeLLM + spy tools. No API key, no DB, no network.

The hard guarantee under test: write tools run ONLY through the confirm gate.
"""

import pytest

from app.graph.action import (
    REPLY_CONFIRM_DECLINED,
    REPLY_PAID_ORDER,
    REPLY_PRESS_CONFIRM,
    execute_pending_action,
    public_pending,
)
from app.graph.core import GraphDeps, build_graph
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


FAKE_SLOTS = [
    {
        "id": f"slot-{n}",
        "branch_id": "b-1",
        "starts_at": f"2099-01-0{n}T08:00:00+00:00",
        "vehicle_type": "motorbike",
        "capacity": 1,
        "booked": 0,
        "branches": {"name": "XeCare Thanh Xuân"},
    }
    for n in (1, 2, 3)
]

UNPAID_ORDER = {
    "id": "order-unpaid",
    "customer_id": "c-1",
    "items": [{"sku": "NHOT-1", "name": "Nhớt Castrol 10W40", "qty": 2}],
    "status": "processing",
    "total_vnd": 360000,
    "paid": False,
}
PAID_ORDER = {
    "id": "order-paid",
    "customer_id": "c-1",
    "items": [{"sku": "LOP-1", "name": "Lốp Michelin City Grip"}],
    "status": "shipped",
    "total_vnd": 1250000,
    "paid": True,
}


def make_spy_tools(free_slots=None, orders=None, bookings=None):
    write_calls = []

    async def get_customer_orders(customer_id):
        return orders or []

    async def find_free_slots(vehicle_type, branch_id=None, from_dt=None, limit=3):
        return (free_slots or [])[:limit]

    async def get_customer_bookings(customer_id):
        return bookings or []

    async def book_slot(customer_id, slot_id, service_note):
        write_calls.append(("book_slot", slot_id))
        slot = next(s for s in free_slots if s["id"] == slot_id)
        return {
            "id": "ticket-1",
            "status": "open",
            "payload": {
                "customer_id": customer_id,
                "slot": {**slot, "branch_name": "XeCare Thanh Xuân"},
                "service_note": service_note,
            },
        }

    async def cancel_booking(customer_id, ticket_id):
        write_calls.append(("cancel_booking", ticket_id))
        return {"id": ticket_id, "status": "cancelled",
                "payload": {"slot": FAKE_SLOTS[0], "customer_id": customer_id}}

    async def cancel_parts_order(customer_id, order_id):
        write_calls.append(("cancel_parts_order", order_id))
        return {**UNPAID_ORDER, "status": "cancelled"}

    toolkit = ToolKit(
        get_customer_orders=get_customer_orders,
        find_free_slots=find_free_slots,
        get_customer_bookings=get_customer_bookings,
        book_slot=book_slot,
        cancel_booking=cancel_booking,
        cancel_parts_order=cancel_parts_order,
    )
    return toolkit, write_calls


def make_deps(llm, tools):
    traces = []

    async def fake_search(query, top_k=5):
        return []

    async def fake_trace(conversation_id, step_type, payload, **kw):
        traces.append({"step_type": step_type, "payload": payload, **kw})

    deps = GraphDeps(
        llm=llm,
        system_prompt="SYSTEM",
        prompt_version=2,
        policy={"escalate_confidence_below": 0.7},
        policy_version=1,
        search=fake_search,
        trace=fake_trace,
        tools=tools,
    )
    return deps, traces


def base_state(text, slots=None, pending=None):
    return {
        "conversation_id": None,
        "customer_id": "c-1",
        "customer_profile": {
            "vehicles": [{"type": "motorbike", "model": "Honda Winner X", "last_km": 19500}],
            "facts": {},
        },
        "messages": [],
        "raw_text": text,
        "pii_session": PIISession(),
        "slots": slots or {},
        "pending_action": pending,
        "guardrail_flags": {},
        "mode": "agent",
    }


def collecting_trace(traces):
    async def trace(step_type, payload, latency_ms=None):
        traces.append({"step_type": step_type, "payload": payload})

    return trace


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ---------- slot-filling over 3 turns, zero writes ----------

@pytest.mark.anyio
async def test_slot_filling_three_turns_then_locked_option():
    tools, write_calls = make_spy_tools(free_slots=FAKE_SLOTS)

    # turn 1: service missing → bot asks (with last_km hint), no pending action
    llm = FakeLLM(
        [
            '{"intent": "booking", "confidence": 0.95}',
            '{"vehicle_type": null, "vehicle_ref": null, "service": null, '
            '"branch_pref": null, "time_pref": null, "target": null, "order_ref": null}',
        ]
    )
    deps, traces = make_deps(llm, tools)
    graph = build_graph(deps)
    final1 = await graph.ainvoke(base_state("cho mình đặt lịch bảo dưỡng"))
    assert "dịch vụ gì" in final1["reply"]
    assert "20.000" in final1["reply"]  # last_km 19500 → next milestone hint
    assert final1.get("pending_action") is None
    assert final1["slots"]["vehicle_type"] == "motorbike"  # single-vehicle auto-fill

    # turn 2: service provided → exactly 3 numbered options, stage choosing
    llm2 = FakeLLM(
        [
            '{"intent": "booking", "confidence": 0.95}',
            '{"vehicle_type": null, "vehicle_ref": null, "service": "bảo dưỡng 20.000 km", '
            '"branch_pref": null, "time_pref": null, "target": null, "order_ref": null}',
        ]
    )
    deps2, _ = make_deps(llm2, tools)
    final2 = await build_graph(deps2).ainvoke(
        base_state("làm gói bảo dưỡng 20.000 km nhé", slots=final1["slots"])
    )
    pending = final2["pending_action"]
    assert pending["stage"] == "choosing"
    assert [o["n"] for o in pending["options"]] == [1, 2, 3]
    assert all(f"{n}." in final2["reply"] for n in (1, 2, 3))

    # turn 3: customer picks "2" → pending locks on slot-2; router is bypassed (1 LLM call)
    llm3 = FakeLLM(['{"choice": 2}'])
    deps3, _ = make_deps(llm3, tools)
    final3 = await build_graph(deps3).ainvoke(
        base_state("cho mình slot 2 nhé", slots=final2["slots"], pending=pending)
    )
    locked = final3["pending_action"]
    assert locked["stage"] == "confirm"
    assert locked["args"]["slot_id"] == "slot-2"
    assert len(llm3.calls) == 1  # option map only — no router on continuation turns

    # the hard guarantee: nothing was written in any of the 3 turns
    assert write_calls == []
    # public projection never leaks internal ids
    assert "slot_id" not in (public_pending(locked) or {})


# ---------- text can never execute the write ----------

@pytest.mark.anyio
async def test_confirm_by_text_refused_zero_llm_zero_writes():
    tools, write_calls = make_spy_tools(free_slots=FAKE_SLOTS)
    llm = FakeLLM([])
    deps, _ = make_deps(llm, tools)
    pending = {"type": "book_slot", "stage": "confirm",
               "args": {"customer_id": "c-1", "slot_id": "slot-2", "service_note": "x"},
               "summary": "đặt lịch"}
    final = await build_graph(deps).ainvoke(base_state("xác nhận đi bạn", pending=pending))
    assert final["reply"] == REPLY_PRESS_CONFIRM
    assert len(llm.calls) == 0
    assert write_calls == []
    assert final["pending_action"] == pending  # still waiting for the button


# ---------- confirm gate executor ----------

@pytest.mark.anyio
async def test_confirm_decline_executes_nothing():
    tools, write_calls = make_spy_tools(free_slots=FAKE_SLOTS)
    pending = {"type": "book_slot", "stage": "confirm",
               "args": {"customer_id": "c-1", "slot_id": "slot-1", "service_note": "x"},
               "summary": "đặt lịch"}
    reply, executed, escalated, new_pending = await execute_pending_action(
        tools, collecting_trace([]), pending, accept=False
    )
    assert reply == REPLY_CONFIRM_DECLINED
    assert executed is False
    assert new_pending is None
    assert write_calls == []


@pytest.mark.anyio
async def test_confirm_accept_books_and_traces():
    tools, write_calls = make_spy_tools(free_slots=FAKE_SLOTS)
    traces = []
    pending = {"type": "book_slot", "stage": "confirm",
               "args": {"customer_id": "c-1", "slot_id": "slot-2",
                        "service_note": "bảo dưỡng", "vehicle_type": "motorbike"},
               "summary": "đặt lịch"}
    reply, executed, escalated, new_pending = await execute_pending_action(
        tools, collecting_trace(traces), pending, accept=True
    )
    assert executed is True
    assert new_pending is None
    assert write_calls == [("book_slot", "slot-2")]
    tool_traces = [t for t in traces if t["step_type"] == "tool_call"]
    assert tool_traces[0]["payload"]["tool"] == "book_slot"
    assert "result" in tool_traces[0]["payload"]


@pytest.mark.anyio
async def test_confirm_accept_on_choosing_stage_executes_nothing():
    tools, write_calls = make_spy_tools(free_slots=FAKE_SLOTS)
    pending = {"type": "book_slot", "stage": "choosing",
               "options": [{"n": 1, "id": "slot-1", "label": "x"}],
               "id_field": "slot_id",
               "args": {"customer_id": "c-1", "service_note": "x"},
               "summary_template": "đặt lịch {label}"}
    reply, executed, _, new_pending = await execute_pending_action(
        tools, collecting_trace([]), pending, accept=True
    )
    assert executed is False
    assert new_pending == pending
    assert write_calls == []


@pytest.mark.anyio
async def test_confirm_without_pending():
    tools, write_calls = make_spy_tools()
    reply, executed, _, new_pending = await execute_pending_action(
        tools, collecting_trace([]), None, accept=True
    )
    assert executed is False
    assert write_calls == []


# ---------- paid order: read-check → escalate, no write, refused trace ----------

@pytest.mark.anyio
async def test_cancel_paid_order_escalates_without_write():
    tools, write_calls = make_spy_tools(orders=[PAID_ORDER, UNPAID_ORDER])
    llm = FakeLLM(
        [
            '{"intent": "modify_booking", "confidence": 0.9}',
            '{"vehicle_type": null, "vehicle_ref": null, "service": null, '
            '"branch_pref": null, "time_pref": null, "target": "order", "order_ref": "lốp"}',
        ]
    )
    deps, traces = make_deps(llm, tools)
    final = await build_graph(deps).ainvoke(base_state("huỷ đơn lốp đã thanh toán rồi ấy"))
    assert final["reply"] == REPLY_PAID_ORDER
    assert final["escalated"] is True
    assert write_calls == []  # the write tool was never invoked
    refused = [
        t for t in traces
        if t["step_type"] == "tool_call" and t["payload"].get("refused") == "PAID_ORDER_ESCALATE"
    ]
    assert len(refused) == 1
    assert refused[0]["payload"]["tool"] == "cancel_parts_order"
    esc = [t for t in traces if t["step_type"] == "escalation"]
    assert esc[0]["payload"]["reason"] == "paid_order_cancel"


# ---------- unpaid order: proposal only, write after confirm ----------

@pytest.mark.anyio
async def test_cancel_unpaid_order_proposes_then_executes_on_confirm():
    tools, write_calls = make_spy_tools(orders=[PAID_ORDER, UNPAID_ORDER])
    llm = FakeLLM(
        [
            '{"intent": "modify_booking", "confidence": 0.9}',
            '{"vehicle_type": null, "vehicle_ref": null, "service": null, '
            '"branch_pref": null, "time_pref": null, "target": "order", "order_ref": "nhớt"}',
        ]
    )
    deps, _ = make_deps(llm, tools)
    final = await build_graph(deps).ainvoke(base_state("huỷ giúp em đơn nhớt chưa thanh toán"))
    pending = final["pending_action"]
    assert pending["stage"] == "confirm"
    assert pending["args"]["order_id"] == "order-unpaid"
    assert write_calls == []

    reply, executed, _, new_pending = await execute_pending_action(
        tools, collecting_trace([]), pending, accept=True
    )
    assert executed is True
    assert write_calls == [("cancel_parts_order", "order-unpaid")]


# ---------- order lookup is read-only ----------

@pytest.mark.anyio
async def test_order_lookup_lists_orders_read_only():
    tools, write_calls = make_spy_tools(orders=[UNPAID_ORDER, PAID_ORDER])
    llm = FakeLLM(
        [
            '{"intent": "order_lookup", "confidence": 0.9}',
            '{"vehicle_type": null, "vehicle_ref": null, "service": null, '
            '"branch_pref": null, "time_pref": null, "target": "order", "order_ref": null}',
        ]
    )
    deps, _ = make_deps(llm, tools)
    final = await build_graph(deps).ainvoke(base_state("kiểm tra đơn phụ tùng của em"))
    assert "Nhớt Castrol 10W40" in final["reply"]
    assert "đang xử lý" in final["reply"]
    assert final.get("pending_action") is None
    assert write_calls == []

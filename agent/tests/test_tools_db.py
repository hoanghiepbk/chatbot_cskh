"""TIP-006 DB-backed tool tests against local Supabase (skip when env absent).

Covers the hard rules that live in the DB/tool layer: atomic booking under race,
the booked<=capacity CHECK, KB-07 2h rule, KB-06 paid-order block.
"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.tools import ToolRefused, build_tools
from tests.conftest import requires_db

pytestmark = requires_db

BRANCH_ID = "b0000000-0000-4000-8000-000000000001"
CUSTOMER_ID = "c0000000-0000-4000-8000-000000000001"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def tools(supabase):
    return build_tools(supabase)


@pytest.fixture
def temp_slot(supabase):
    """A capacity-1 slot 3 days out; removed (with its tickets) after the test."""
    row = (
        supabase.table("service_slots")
        .insert(
            {
                "branch_id": BRANCH_ID,
                "starts_at": (datetime.now(timezone.utc) + timedelta(days=3)).isoformat(),
                "vehicle_type": "motorbike",
                "capacity": 1,
                "booked": 0,
            }
        )
        .execute()
    )
    slot = row.data[0]
    yield slot
    supabase.table("tickets").delete().eq("payload->slot->>id", slot["id"]).execute()
    supabase.table("service_slots").delete().eq("id", slot["id"]).execute()


@pytest.mark.anyio
async def test_race_two_bookings_one_slot(supabase, tools, temp_slot):
    results = await asyncio.gather(
        tools.book_slot(CUSTOMER_ID, temp_slot["id"], "bảo dưỡng"),
        tools.book_slot(CUSTOMER_ID, temp_slot["id"], "bảo dưỡng"),
        return_exceptions=True,
    )
    tickets = [r for r in results if isinstance(r, dict)]
    refused = [r for r in results if isinstance(r, ToolRefused)]
    assert len(tickets) == 1, f"expected exactly one success, got {results}"
    assert len(refused) == 1 and refused[0].code == "SLOT_FULL"

    slot = (
        supabase.table("service_slots").select("*").eq("id", temp_slot["id"]).execute()
    ).data[0]
    assert slot["booked"] == slot["capacity"] == 1  # CHECK booked<=capacity intact


@pytest.mark.anyio
async def test_cancel_booking_within_2h_refused(supabase, tools):
    slot = (
        supabase.table("service_slots")
        .insert(
            {
                "branch_id": BRANCH_ID,
                "starts_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                "vehicle_type": "motorbike",
                "capacity": 1,
                "booked": 0,
            }
        )
        .execute()
    ).data[0]
    try:
        ticket = await tools.book_slot(CUSTOMER_ID, slot["id"], "thay nhớt")
        with pytest.raises(ToolRefused) as exc:
            await tools.cancel_booking(CUSTOMER_ID, ticket["id"])
        assert exc.value.code == "TOO_LATE"
        status = (
            supabase.table("tickets").select("status").eq("id", ticket["id"]).execute()
        ).data[0]["status"]
        assert status == "open"  # untouched
    finally:
        supabase.table("tickets").delete().eq("payload->slot->>id", slot["id"]).execute()
        supabase.table("service_slots").delete().eq("id", slot["id"]).execute()


@pytest.mark.anyio
async def test_cancel_booking_far_enough_releases_slot(supabase, tools, temp_slot):
    ticket = await tools.book_slot(CUSTOMER_ID, temp_slot["id"], "bảo dưỡng")
    cancelled = await tools.cancel_booking(CUSTOMER_ID, ticket["id"])
    assert cancelled["status"] == "cancelled"
    slot = (
        supabase.table("service_slots").select("booked").eq("id", temp_slot["id"]).execute()
    ).data[0]
    assert slot["booked"] == 0  # released


@pytest.mark.anyio
async def test_cancel_paid_order_refused_and_unchanged(supabase, tools):
    order = (
        supabase.table("parts_orders")
        .insert(
            {
                "customer_id": CUSTOMER_ID,
                "items": [{"sku": "TEST-PAID", "name": "Phụ tùng test paid"}],
                "status": "processing",
                "total_vnd": 100000,
                "paid": True,
            }
        )
        .execute()
    ).data[0]
    try:
        with pytest.raises(ToolRefused) as exc:
            await tools.cancel_parts_order(CUSTOMER_ID, order["id"])
        assert exc.value.code == "PAID_ORDER_ESCALATE"
        row = (
            supabase.table("parts_orders").select("status, paid").eq("id", order["id"]).execute()
        ).data[0]
        assert row == {"status": "processing", "paid": True}  # untouched
    finally:
        supabase.table("parts_orders").delete().eq("id", order["id"]).execute()


@pytest.mark.anyio
async def test_cancel_other_customers_order_not_found(supabase, tools):
    other = "c0000000-0000-4000-8000-000000000002"
    orders = await tools.get_customer_orders(other)
    target = orders[0]["id"]
    with pytest.raises(ToolRefused) as exc:
        await tools.cancel_parts_order(CUSTOMER_ID, target)
    assert exc.value.code == "NOT_FOUND"


@pytest.mark.anyio
async def test_create_rescue_ticket_real_row(supabase, tools):
    ticket = await tools.create_rescue_ticket(
        conversation_id=None,
        location="đại lộ Thăng Long gần cầu vượt",
        callback_placeholder="[PHONE_KH]",
        vehicle={"type": "motorbike", "model": "Honda Winner X"},
        note="toi bi tai nan, goi lai [PHONE_KH]",
    )
    try:
        row = (
            supabase.table("tickets").select("*").eq("id", ticket["id"]).execute()
        ).data[0]
        assert row["type"] == "rescue"
        assert row["priority"] == "urgent"
        assert row["status"] == "open"
        assert row["payload"]["callback_placeholder"] == "[PHONE_KH]"
    finally:
        supabase.table("tickets").delete().eq("id", ticket["id"]).execute()


@pytest.mark.anyio
async def test_find_free_slots_from_seed(tools):
    slots = await tools.find_free_slots("motorbike")
    assert len(slots) == 3
    assert all(s["booked"] < s["capacity"] for s in slots)
    starts = [s["starts_at"] for s in slots]
    assert starts == sorted(starts)
    assert all(s["branches"]["name"].startswith("XeCare") for s in slots)


@pytest.mark.anyio
async def test_graph_booking_options_come_from_seed(supabase, tools):
    """FakeLLM graph + real tools: the 3 proposed options are real seed slots."""
    from app.graph.core import GraphDeps, build_graph
    from app.guardrails.pii import PIISession
    from app.llm import LLMResult

    class FakeLLM:
        def __init__(self, responses):
            self.responses = list(responses)

        async def complete(self, model, system, messages, max_tokens, json_mode=False):
            return LLMResult(
                text=self.responses.pop(0), input_tokens=1, output_tokens=1,
                cost_usd=0.0, latency_ms=1,
            )

    async def no_trace(*a, **kw):
        return None

    async def no_search(query, top_k=5):
        return []

    deps = GraphDeps(
        llm=FakeLLM(
            [
                '{"intent": "booking", "confidence": 0.95}',
                '{"vehicle_type": "motorbike", "vehicle_ref": null, '
                '"service": "bảo dưỡng 20.000 km", "branch_pref": null, '
                '"time_pref": null, "target": null, "order_ref": null}',
                # TIP-007: output rubric (clean)
                '{"promises_outside_policy": false, "unsafe_advice": false, '
                '"reveals_internal": false, "off_domain": false}',
            ]
        ),
        system_prompt="S", prompt_version=2, policy={"escalate_confidence_below": 0.7},
        policy_version=1, search=no_search, trace=no_trace, tools=build_tools(supabase),
    )
    final = await build_graph(deps).ainvoke(
        {
            "conversation_id": None,
            "customer_id": CUSTOMER_ID,
            "customer_profile": {"vehicles": [], "facts": {}},
            "messages": [],
            "raw_text": "đặt lịch bảo dưỡng xe máy 20.000 km",
            "pii_session": PIISession(),
            "slots": {},
            "guardrail_flags": {},
            "mode": "agent",
        }
    )
    pending = final["pending_action"]
    assert pending["stage"] == "choosing"
    option_ids = [o["id"] for o in pending["options"]]
    assert len(option_ids) == 3
    rows = supabase.table("service_slots").select("id").in_("id", option_ids).execute()
    assert len(rows.data) == 3  # all real seed slots

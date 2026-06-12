"""XeCare tools — read tools are free to call; write tools may ONLY run through
the confirm gate (enforced in the graph/API layer, see app/graph/action.py).

Hard rules enforced HERE, independent of the LLM (Blueprint §6.3):
- book_slot: atomic RPC — full slot can never be oversold (SLOT_FULL + DB CHECK)
- cancel_booking: only own ticket, status open, > 2h before start (KB-07)
- cancel_parts_order: only own order, paid=false, status processing (KB-06);
  paid=true → PAID_ORDER_ESCALATE — a "cancel paid order" tool does NOT exist.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from app.tools.base import ToolRefused

CANCEL_BOOKING_MIN_HOURS = 2  # KB-07: đổi/hủy lịch trước giờ hẹn ít nhất 2h


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass
class ToolKit:
    # read — free to call
    get_customer_orders: Callable[..., Awaitable[list[dict]]]
    find_free_slots: Callable[..., Awaitable[list[dict]]]
    get_customer_bookings: Callable[..., Awaitable[list[dict]]]
    # write — confirm gate only
    book_slot: Callable[..., Awaitable[dict]]
    cancel_booking: Callable[..., Awaitable[dict]]
    cancel_parts_order: Callable[..., Awaitable[dict]]


WRITE_TOOLS = {"book_slot", "cancel_booking", "cancel_parts_order"}


def build_tools(supabase: Any) -> ToolKit:
    # ---------- read tools ----------

    async def get_customer_orders(customer_id: str) -> list[dict]:
        rows = (
            supabase.table("parts_orders")
            .select("*")
            .eq("customer_id", customer_id)
            .order("created_at", desc=True)
            .execute()
        )
        return rows.data or []

    async def find_free_slots(
        vehicle_type: str,
        branch_id: str | None = None,
        from_dt: datetime | None = None,
        limit: int = 3,
    ) -> list[dict]:
        from_dt = from_dt or datetime.now(timezone.utc)
        query = (
            supabase.table("service_slots")
            .select("*, branches(name, district)")
            .eq("vehicle_type", vehicle_type)
            .gte("starts_at", from_dt.isoformat())
            .order("starts_at")
            .limit(50)  # PostgREST can't compare columns — filter booked<capacity in app
        )
        if branch_id:
            query = query.eq("branch_id", branch_id)
        rows = query.execute()
        free = [r for r in (rows.data or []) if r["booked"] < r["capacity"]]
        return free[:limit]

    async def get_customer_bookings(customer_id: str) -> list[dict]:
        # tickets has no customer_id column — ownership lives in payload (TIP-002 schema)
        rows = (
            supabase.table("tickets")
            .select("*")
            .eq("type", "booking")
            .eq("status", "open")
            .eq("payload->>customer_id", customer_id)
            .order("created_at", desc=True)
            .execute()
        )
        return rows.data or []

    # ---------- write tools (confirm gate only) ----------

    async def book_slot(customer_id: str, slot_id: str, service_note: str) -> dict:
        booked = supabase.rpc("book_slot_atomic", {"p_slot_id": slot_id}).execute()
        slot = booked.data[0] if isinstance(booked.data, list) else booked.data
        if not slot or not slot.get("id"):
            raise ToolRefused("SLOT_FULL", "slot already at capacity (or not found)")

        branch = (
            supabase.table("branches").select("name").eq("id", slot["branch_id"]).execute()
        )
        branch_name = branch.data[0]["name"] if branch.data else None
        ticket = (
            supabase.table("tickets")
            .insert(
                {
                    "type": "booking",
                    "status": "open",
                    "payload": {
                        "customer_id": customer_id,
                        "slot": {
                            "id": slot["id"],
                            "branch_id": slot["branch_id"],
                            "branch_name": branch_name,
                            "starts_at": slot["starts_at"],
                            "vehicle_type": slot["vehicle_type"],
                        },
                        "service_note": service_note,
                    },
                }
            )
            .execute()
        )
        return ticket.data[0]

    async def cancel_booking(customer_id: str, ticket_id: str) -> dict:
        rows = supabase.table("tickets").select("*").eq("id", ticket_id).execute()
        ticket = rows.data[0] if rows.data else None
        if (
            not ticket
            or ticket["type"] != "booking"
            or (ticket.get("payload") or {}).get("customer_id") != customer_id
            or ticket["status"] != "open"
        ):
            raise ToolRefused("NOT_FOUND", "no open booking of this customer with that id")

        starts_at = parse_ts(ticket["payload"]["slot"]["starts_at"])
        if starts_at - datetime.now(timezone.utc) <= timedelta(hours=CANCEL_BOOKING_MIN_HOURS):
            raise ToolRefused(
                "TOO_LATE",
                f"booking starts within {CANCEL_BOOKING_MIN_HOURS}h — KB-07 forbids self-cancel",
            )

        updated = (
            supabase.table("tickets")
            .update({"status": "cancelled"})
            .eq("id", ticket_id)
            .execute()
        )
        supabase.rpc(
            "release_slot_atomic", {"p_slot_id": ticket["payload"]["slot"]["id"]}
        ).execute()
        return updated.data[0]

    async def cancel_parts_order(customer_id: str, order_id: str) -> dict:
        rows = supabase.table("parts_orders").select("*").eq("id", order_id).execute()
        order = rows.data[0] if rows.data else None
        if not order or order["customer_id"] != customer_id:
            raise ToolRefused("NOT_FOUND", "no order of this customer with that id")
        if order["paid"]:
            # KB-06 + REQ-03: paid orders are CSKH-only — this tool does not exist for them
            raise ToolRefused("PAID_ORDER_ESCALATE", "paid order — human CSKH must handle")
        if order["status"] != "processing":
            raise ToolRefused("NOT_CANCELLABLE", f"order status is {order['status']}")

        updated = (
            supabase.table("parts_orders")
            .update({"status": "cancelled"})
            .eq("id", order_id)
            .execute()
        )
        return updated.data[0]

    return ToolKit(
        get_customer_orders=get_customer_orders,
        find_free_slots=find_free_slots,
        get_customer_bookings=get_customer_bookings,
        book_slot=book_slot,
        cancel_booking=cancel_booking,
        cancel_parts_order=cancel_parts_order,
    )

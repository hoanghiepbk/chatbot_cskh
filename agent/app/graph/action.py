"""Action agent (TIP-006): multi-turn slot-filling, free-slot suggestion and the
confirm gate for write tools.

State machine per conversation (slots + pending_action persisted by the API layer):
- no pending_action  → extract slots (Haiku) → ask for missing info, answer reads,
  or propose options / a confirm card
- pending stage "choosing" → map the customer's reply to one option (Haiku)
- pending stage "confirm"  → ONLY POST /chat/{id}/confirm may execute the write
  tool (execute_pending_action below). A text message can never trigger a write —
  anti social-engineering, Blueprint §6.3.

Budget: at most 2 Haiku calls per turn, no Sonnet in this branch.
"""

import json
import time
from datetime import timedelta, timezone

from app.guardrails.pre_gate import normalize_text
from app.llm import MODEL_HAIKU
from app.tools import ToolRefused, run_tool
from app.tools.registry import parse_ts

HOTLINE = "1900 1234"
VN_TZ = timezone(timedelta(hours=7))
WEEKDAYS = ["Thứ 2", "Thứ 3", "Thứ 4", "Thứ 5", "Thứ 6", "Thứ 7", "Chủ nhật"]

SLOT_EXTRACT_SYSTEM = """Bạn trích xuất thông tin từ tin nhắn khách của trung tâm dịch vụ xe XeCare.
Trả về DUY NHẤT một JSON object:
{"vehicle_type": "motorbike"|"car"|null, "vehicle_ref": <cụm từ khách nhắc về xe>|null,
 "service": <dịch vụ khách muốn làm>|null, "branch_pref": <chi nhánh khách muốn>|null,
 "time_pref": <thời gian khách muốn>|null, "target": "booking"|"order"|null,
 "order_ref": <từ khóa nhận diện đơn phụ tùng, ví dụ "nhớt", "lốp">|null}
- "xe máy"/"xe ga"/"xe số"/tên xe máy → motorbike; "ô tô"/"xe hơi"/tên ô tô → car
- target: khách muốn thao tác trên LỊCH HẸN (booking) hay ĐƠN PHỤ TÙNG (order)
- Trường nào tin nhắn không nói tới → null. KHÔNG suy diễn, KHÔNG bịa."""

OPTION_MAP_SYSTEM = """Khách của XeCare đang chọn một trong các lựa chọn được đánh số.
Đọc tin nhắn và trả về DUY NHẤT JSON: {"choice": <số nguyên thứ tự lựa chọn>} hoặc
{"choice": null} nếu không xác định được khách chọn lựa chọn nào."""

REPLY_NO_PENDING = "Hiện không có thao tác nào đang chờ xác nhận ạ. Anh/chị cần mình hỗ trợ gì thêm không?"
REPLY_CONFIRM_DECLINED = "Dạ, mình đã hủy thao tác này. Anh/chị cần hỗ trợ gì thêm cứ nhắn mình nhé!"
REPLY_PRESS_CONFIRM = (
    "Dạ, để đảm bảo an toàn, anh/chị vui lòng bấm nút Xác nhận (hoặc Hủy) trên thẻ "
    "xác nhận phía trên nhé — mình không thể thực hiện thao tác qua tin nhắn ạ."
)
REPLY_PAID_ORDER = (
    "Mình rất hiểu mong muốn của anh/chị ạ. Tuy nhiên đơn hàng này đã được thanh toán "
    "nên cần bộ phận CSKH xử lý — XeCare sẽ liên hệ lại anh/chị trong 1 ngày làm việc "
    f"để hỗ trợ ạ. Anh/chị cũng có thể gọi {HOTLINE} để được xử lý nhanh hơn."
)
REPLY_TOO_LATE = (
    "Xin lỗi anh/chị, theo quy định của XeCare, lịch hẹn chỉ có thể tự đổi/hủy trước "
    f"giờ hẹn ít nhất 2 tiếng. Lịch này đã quá sát giờ — anh/chị vui lòng gọi {HOTLINE} "
    "để được hỗ trợ trực tiếp ạ."
)

ORDER_STATUS_VI = {
    "processing": "đang xử lý",
    "shipped": "đang giao",
    "delivered": "đã giao",
    "cancelled": "đã hủy",
}

INTENT_BY_ACTION = {
    "book_slot": "booking",
    "cancel_booking": "modify_booking",
    "cancel_parts_order": "modify_booking",
}


def vnd(amount) -> str:
    return f"{amount:,}".replace(",", ".") + "đ"


def format_slot_label(slot: dict) -> str:
    dt = parse_ts(slot["starts_at"]).astimezone(VN_TZ)
    branch = (slot.get("branches") or {}).get("name") or slot.get("branch_name") or ""
    return f"{dt:%H:%M} {WEEKDAYS[dt.weekday()]} {dt:%d/%m} — {branch}"


def order_label(order: dict) -> str:
    names = ", ".join(item.get("name", "?") for item in (order.get("items") or []))
    return f"{names} ({vnd(order.get('total_vnd', 0))})"


def order_line(i: int, order: dict) -> str:
    status = ORDER_STATUS_VI.get(order["status"], order["status"])
    paid = "đã thanh toán" if order["paid"] else "chưa thanh toán"
    return f"{i}. {order_label(order)} — {status}, {paid}"


def match_orders(orders: list[dict], order_ref: str | None) -> list[dict]:
    """All orders whose item names/skus contain every word of order_ref (diacritic-free)."""
    if not order_ref:
        return []
    words = normalize_text(order_ref).split()
    matched = []
    for order in orders:
        haystack = normalize_text(
            " ".join(
                f"{item.get('name', '')} {item.get('sku', '')}"
                for item in (order.get("items") or [])
            )
        )
        if words and all(w in haystack for w in words):
            matched.append(order)
    return matched


def resolve_vehicle(slots: dict, vehicles: list[dict]) -> tuple[dict, str | None]:
    """Fill vehicle_type/vehicle_label into slots from extraction + profile.
    Returns (updated slots, ask_reply | None). A soft confirm sentence is stored
    in slots['vehicle_soft_confirm'] when auto-filled from a single-vehicle profile."""
    if slots.get("vehicle_ref"):
        ref = normalize_text(slots["vehicle_ref"])
        for v in vehicles:
            if normalize_text(v.get("model", "")) and (
                ref in normalize_text(v["model"]) or normalize_text(v["model"]) in ref
            ):
                slots["vehicle_type"] = v.get("type", slots.get("vehicle_type"))
                slots["vehicle_label"] = v.get("model")
                return slots, None
    if slots.get("vehicle_type"):
        slots.setdefault("vehicle_label", "xe của anh/chị")
        return slots, None
    if len(vehicles) == 1:
        v = vehicles[0]
        slots["vehicle_type"] = v.get("type")
        slots["vehicle_label"] = v.get("model", "xe của anh/chị")
        slots["vehicle_soft_confirm"] = (
            f"Mình hỗ trợ cho chiếc {slots['vehicle_label']} của anh/chị nhé. "
        )
        return slots, None
    if len(vehicles) > 1:
        models = ", ".join(v.get("model", "?") for v in vehicles)
        return slots, (
            f"Anh/chị muốn đặt lịch cho xe nào ạ? Hồ sơ của mình đang có: {models}."
        )
    return slots, "Anh/chị muốn đặt lịch cho xe máy hay ô tô ạ?"


def service_question(slots: dict, vehicles: list[dict]) -> str:
    base = "Anh/chị muốn làm dịch vụ gì ạ (bảo dưỡng định kỳ, thay nhớt, kiểm tra/sửa chữa...)?"
    label = slots.get("vehicle_label")
    for v in vehicles:
        if v.get("model") == label and v.get("last_km"):
            next_mark = ((v["last_km"] // 5000) + 1) * 5000
            return (
                f"{base} Xe mình đang ~{vnd(v['last_km'])[:-1]} km, sắp tới mốc "
                f"{vnd(next_mark)[:-1]} km — anh/chị muốn làm gói bảo dưỡng định kỳ luôn không ạ?"
            )
    return base


def public_pending(pending: dict | None) -> dict | None:
    """Projection safe for the widget: no internal ids."""
    if not pending:
        return None
    view = {"type": pending["type"], "stage": pending["stage"]}
    if pending["stage"] == "confirm":
        view["summary"] = pending.get("summary")
    else:
        view["options"] = [{"n": o["n"], "label": o["label"]} for o in pending["options"]]
    return view


def build_action_node(deps):
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

    async def haiku_json(state, purpose: str, system: str, content: str) -> dict | None:
        from app.graph.core import extract_json_object

        start = time.perf_counter()
        result = await deps.llm.complete(
            model=MODEL_HAIKU,
            system=system,
            messages=[{"role": "user", "content": content}],
            max_tokens=200,
            json_mode=True,
        )
        await trace(
            state,
            "llm_call",
            {
                "purpose": purpose,
                "model": MODEL_HAIKU,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
            },
            latency_ms=result.latency_ms or int((time.perf_counter() - start) * 1000),
            cost_usd=result.cost_usd,
        )
        return extract_json_object(result.text)

    # ---------- sub-flows ----------

    async def continue_pending(state, pending: dict) -> dict:
        intent = INTENT_BY_ACTION[pending["type"]]
        if pending["stage"] == "confirm":
            # text can never execute the write — point to the confirm card
            return {"reply": REPLY_PRESS_CONFIRM, "intent": intent}

        mapped = await haiku_json(
            state,
            "option_map",
            OPTION_MAP_SYSTEM,
            "CÁC LỰA CHỌN:\n"
            + "\n".join(f"{o['n']}. {o['label']}" for o in pending["options"])
            + f"\n\nTIN NHẮN KHÁCH: {state['masked_text']}",
        )
        try:
            choice = int((mapped or {}).get("choice"))
        except (TypeError, ValueError):
            choice = None
        chosen = next((o for o in pending["options"] if o["n"] == choice), None)
        if not chosen:
            return {
                "reply": "Dạ mình chưa rõ anh/chị chọn phương án nào — anh/chị nhắn giúp "
                "mình số thứ tự (1, 2 hoặc 3) nhé ạ.",
                "pending_action": pending,
                "intent": intent,
            }

        confirmed = {
            "type": pending["type"],
            "stage": "confirm",
            "args": {**pending["args"], pending["id_field"]: chosen["id"]},
            "summary": pending["summary_template"].format(label=chosen["label"]),
        }
        return {
            "reply": f"Dạ, mình xin chốt: {confirmed['summary']}. Anh/chị bấm nút Xác nhận "
            "để mình thực hiện nhé ạ.",
            "pending_action": confirmed,
            "intent": intent,
        }

    async def handle_order_lookup(state, customer_id: str) -> dict:
        orders = await run_tool(
            tool_trace(state),
            "get_customer_orders",
            deps.tools.get_customer_orders,
            customer_id=customer_id,
        )
        if not orders:
            return {"reply": "Dạ, mình chưa thấy đơn phụ tùng nào của anh/chị ạ."}
        lines = "\n".join(order_line(i + 1, o) for i, o in enumerate(orders))
        return {"reply": f"Dạ, anh/chị đang có các đơn phụ tùng sau:\n{lines}"}

    async def handle_booking(state, customer_id: str, slots: dict) -> dict:
        vehicles = (state.get("customer_profile") or {}).get("vehicles") or []
        slots, ask = resolve_vehicle(slots, vehicles)
        if ask:
            return {"reply": ask, "slots": slots}
        if not slots.get("service"):
            return {"reply": service_question(slots, vehicles), "slots": slots}

        free = await run_tool(
            tool_trace(state),
            "find_free_slots",
            deps.tools.find_free_slots,
            vehicle_type=slots["vehicle_type"],
        )
        if not free:
            return {
                "reply": "Xin lỗi anh/chị, hiện các khung giờ sắp tới đều đã kín. Anh/chị "
                f"gọi {HOTLINE} để được xếp lịch linh hoạt hơn nhé ạ.",
                "slots": slots,
            }
        options = [
            {"n": i + 1, "id": s["id"], "label": format_slot_label(s)}
            for i, s in enumerate(free)
        ]
        pending = {
            "type": "book_slot",
            "stage": "choosing",
            "options": options,
            "id_field": "slot_id",
            "args": {
                "customer_id": customer_id,
                "service_note": slots["service"],
                # kept for the SLOT_FULL retry path in execute_pending_action
                "vehicle_type": slots["vehicle_type"],
            },
            "summary_template": (
                f"đặt lịch {slots['service']} cho {slots.get('vehicle_label', 'xe')} lúc {{label}}"
            ),
        }
        soft = slots.pop("vehicle_soft_confirm", "")
        lines = "\n".join(f"{o['n']}. {o['label']}" for o in options)
        return {
            "reply": f"{soft}Hiện đang trống các khung giờ gần nhất:\n{lines}\n"
            "Anh/chị chọn giúp mình một khung giờ nhé ạ.",
            "pending_action": pending,
            "slots": slots,
        }

    async def handle_cancel(state, customer_id: str, slots: dict) -> dict:
        target = slots.get("target")
        if target == "booking" or (target != "order" and not slots.get("order_ref")):
            bookings = await run_tool(
                tool_trace(state),
                "get_customer_bookings",
                deps.tools.get_customer_bookings,
                customer_id=customer_id,
            )
            if target == "booking" or bookings:
                return await propose_cancel_booking(bookings, slots)
            # target unknown and no open booking — fall through to orders
        return await propose_cancel_order(state, customer_id, slots)

    async def propose_cancel_booking(bookings: list[dict], slots: dict) -> dict:
        if not bookings:
            return {
                "reply": "Dạ, mình không thấy lịch hẹn nào đang mở của anh/chị ạ. Anh/chị "
                "muốn kiểm tra đơn phụ tùng hay đặt lịch mới không?",
                "slots": slots,
            }
        labels = [
            {"n": i + 1, "id": b["id"], "label": format_slot_label(b["payload"]["slot"])}
            for i, b in enumerate(bookings)
        ]
        if len(bookings) == 1:
            pending = {
                "type": "cancel_booking",
                "stage": "confirm",
                "args": {"customer_id": bookings[0]["payload"]["customer_id"],
                         "ticket_id": bookings[0]["id"]},
                "summary": f"hủy lịch hẹn {labels[0]['label']}",
            }
            return {
                "reply": f"Dạ, mình xin chốt: {pending['summary']}. Anh/chị bấm nút Xác nhận "
                "để mình thực hiện nhé ạ.",
                "pending_action": pending,
                "slots": slots,
                "reply_branch": "template",  # confirm card
            }
        pending = {
            "type": "cancel_booking",
            "stage": "choosing",
            "options": labels,
            "id_field": "ticket_id",
            "args": {"customer_id": bookings[0]["payload"]["customer_id"]},
            "summary_template": "hủy lịch hẹn {label}",
        }
        lines = "\n".join(f"{o['n']}. {o['label']}" for o in labels)
        return {
            "reply": f"Anh/chị đang có các lịch hẹn sau:\n{lines}\nAnh/chị muốn hủy lịch nào ạ?",
            "pending_action": pending,
            "slots": slots,
        }

    async def propose_cancel_order(state, customer_id: str, slots: dict) -> dict:
        orders = await run_tool(
            tool_trace(state),
            "get_customer_orders",
            deps.tools.get_customer_orders,
            customer_id=customer_id,
        )
        active = [o for o in orders if o["status"] not in ("cancelled",)]
        if not active:
            return {"reply": "Dạ, mình không thấy đơn phụ tùng nào đang hoạt động của anh/chị ạ.",
                    "slots": slots}

        matched = match_orders(active, slots.get("order_ref")) or (
            active if len(active) == 1 else []
        )
        if len(matched) != 1:
            lines = "\n".join(order_line(i + 1, o) for i, o in enumerate(active))
            return {
                "reply": f"Anh/chị đang có các đơn sau:\n{lines}\nAnh/chị muốn hủy đơn nào ạ "
                "(nhắn giúp mình tên phụ tùng trong đơn)?",
                "slots": slots,
            }

        order = matched[0]
        if order["paid"]:
            # KB-06/REQ-03: paid order is read-checked BEFORE proposing — never becomes a
            # pending_action. The write tool is NOT invoked (confirm-gate rule); this trace
            # records the §6.3 refusal decision with the same shape a refused call would have.
            await tool_trace(state)(
                "tool_call",
                {
                    "tool": "cancel_parts_order",
                    "args": {"customer_id": customer_id, "order_id": order["id"]},
                    "refused": "PAID_ORDER_ESCALATE",
                },
            )
            await trace(state, "escalation", {"reason": "paid_order_cancel", "order_id": order["id"]})
            return {
                "reply": REPLY_PAID_ORDER,
                "escalated": True,
                "slots": slots,
                "reply_branch": "template",
            }

        if order["status"] != "processing":
            status = ORDER_STATUS_VI.get(order["status"], order["status"])
            return {
                "reply": f"Dạ, đơn {order_label(order)} hiện {status} nên không thể tự hủy "
                f"trong chat — anh/chị gọi {HOTLINE} để được hỗ trợ nhé ạ.",
                "slots": slots,
            }

        pending = {
            "type": "cancel_parts_order",
            "stage": "confirm",
            "args": {"customer_id": customer_id, "order_id": order["id"]},
            "summary": f"hủy đơn {order_label(order)}",
        }
        return {
            "reply": f"Dạ, mình xin chốt: {pending['summary']}. Anh/chị bấm nút Xác nhận "
            "để mình thực hiện nhé ạ.",
            "pending_action": pending,
            "slots": slots,
            "reply_branch": "template",  # confirm card
        }

    # ---------- the node ----------

    async def action(state) -> dict:
        customer_id = state.get("customer_id")
        pending = state.get("pending_action")
        if pending:
            # continuation replies are template-built (confirm card / guidance) —
            # the output rubric skips them (TIP-007 budget)
            result = await continue_pending(state, pending)
            result.setdefault("reply_branch", "template")
            return result

        slots = dict(state.get("slots") or {})
        extracted = await haiku_json(
            state,
            "slot_extract",
            SLOT_EXTRACT_SYSTEM,
            f"TIN NHẮN KHÁCH: {state['masked_text']}\n"
            f"ĐÃ BIẾT TRƯỚC ĐÓ: {json.dumps({k: v for k, v in slots.items() if v}, ensure_ascii=False)}",
        )
        for key, value in (extracted or {}).items():
            if value is not None:
                slots[key] = value

        intent = state.get("intent") or "booking"
        if intent == "order_lookup":
            result = await handle_order_lookup(state, customer_id)
        elif intent == "modify_booking":
            result = await handle_cancel(state, customer_id, slots)
        else:  # booking
            result = await handle_booking(state, customer_id, slots)
        result.setdefault("slots", slots)
        result.setdefault("intent", intent)
        # replies here interpolate extracted/DB-derived text → output rubric applies
        result.setdefault("reply_branch", "action")
        return result

    return action


# ---------- confirm gate executor (called ONLY by POST /chat/{id}/confirm) ----------

async def execute_pending_action(
    tools, trace, pending: dict | None, accept: bool
) -> tuple[str, bool, bool, dict | None]:
    """Returns (reply, executed, escalated, new_pending)."""
    if not pending:
        return REPLY_NO_PENDING, False, False, None
    if not accept:
        return REPLY_CONFIRM_DECLINED, False, False, None
    if pending["stage"] != "confirm":
        return (
            "Dạ, anh/chị chọn giúp mình một phương án trước rồi mình xác nhận nhé ạ.",
            False,
            False,
            pending,
        )

    args = dict(pending["args"])
    kind = pending["type"]
    try:
        if kind == "book_slot":
            vehicle_type = args.pop("vehicle_type", None)
            try:
                ticket = await run_tool(trace, "book_slot", tools.book_slot, **args)
            except ToolRefused as refused:
                if refused.code != "SLOT_FULL":
                    raise
                # slot got taken meanwhile — offer fresh options
                free = await run_tool(
                    trace, "find_free_slots", tools.find_free_slots,
                    vehicle_type=vehicle_type or "motorbike",
                )
                if not free:
                    return (
                        f"Xin lỗi anh/chị, khung giờ này vừa kín và hiện chưa còn khung "
                        f"trống gần. Anh/chị gọi {HOTLINE} để được xếp lịch nhé ạ.",
                        False, False, None,
                    )
                options = [
                    {"n": i + 1, "id": s["id"], "label": format_slot_label(s)}
                    for i, s in enumerate(free)
                ]
                new_pending = {
                    "type": "book_slot",
                    "stage": "choosing",
                    "options": options,
                    "id_field": "slot_id",
                    "args": {**args, "vehicle_type": vehicle_type},
                    "summary_template": "đặt lịch {label}",
                }
                lines = "\n".join(f"{o['n']}. {o['label']}" for o in options)
                return (
                    "Xin lỗi anh/chị, khung giờ này vừa có khách khác đặt mất. Các khung "
                    f"trống gần nhất:\n{lines}\nAnh/chị chọn lại giúp mình nhé ạ.",
                    False, False, new_pending,
                )
            label = format_slot_label(ticket["payload"]["slot"])
            return (
                f"Dạ xong rồi ạ! Mình đã đặt lịch {label} cho anh/chị "
                f"(ghi chú: {ticket['payload'].get('service_note', '')}). "
                "XeCare hẹn gặp anh/chị đúng giờ nhé!",
                True, False, None,
            )

        if kind == "cancel_booking":
            ticket = await run_tool(trace, "cancel_booking", tools.cancel_booking, **args)
            label = format_slot_label(ticket["payload"]["slot"])
            return (
                f"Dạ, mình đã hủy lịch hẹn {label} cho anh/chị. Khi nào cần đặt lại, "
                "anh/chị cứ nhắn mình nhé ạ!",
                True, False, None,
            )

        if kind == "cancel_parts_order":
            order = await run_tool(
                trace, "cancel_parts_order", tools.cancel_parts_order, **args
            )
            return (
                f"Dạ, mình đã hủy đơn {order_label(order)} cho anh/chị. Nếu cần đặt lại "
                "phụ tùng, anh/chị cứ nhắn mình nhé ạ!",
                True, False, None,
            )

        return REPLY_NO_PENDING, False, False, None

    except ToolRefused as refused:
        if refused.code == "TOO_LATE":
            return REPLY_TOO_LATE, False, False, None
        if refused.code == "PAID_ORDER_ESCALATE":
            # defense in depth — proposal flow already read-checks paid orders
            await trace("escalation", {"reason": "paid_order_cancel"})
            return REPLY_PAID_ORDER, False, True, None
        return (
            f"Xin lỗi anh/chị, mình không thực hiện được thao tác này ({refused.code}). "
            f"Anh/chị gọi {HOTLINE} để được hỗ trợ trực tiếp nhé ạ.",
            False, False, None,
        )

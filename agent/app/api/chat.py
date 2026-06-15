"""Chat API: /chat/start, /message (sync), /message_stream (SSE), /confirm, /close.

The real phone number lives ONLY at this app layer: hashed for profile lookup,
registered as [PHONE_KH] in the conversation's PIISession, never persisted in
messages. Session state (PII map + slots/pending + emergency + hitl) is persisted
in conversations.session via SessionStore (TIP-008b) — survives restarts.
"""

import hashlib
import json
import os

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.graph.action import execute_pending_action, public_pending
from app.guardrails.pii import PIISession, normalize_phone
from app.session import Session

router = APIRouter()


def store(request: Request):
    return request.app.state.session_store


def bind_trace(app_state, conversation_id: str):
    """Conversation-bound trace for calls outside the graph (/confirm, staff, close)."""
    from app.trace import log_trace

    async def trace(step_type: str, payload: dict, latency_ms: int | None = None):
        await log_trace(
            conversation_id,
            step_type,
            payload,
            latency_ms=latency_ms,
            prompt_version=app_state.prompt_version,
            policy_version=app_state.policy_version,
        )

    return trace


class StartRequest(BaseModel):
    phone: str


class MessageRequest(BaseModel):
    text: str


class ConfirmRequest(BaseModel):
    accept: bool


class CloseRequest(BaseModel):
    resolution: str | None = None


def phone_hash(phone: str) -> str:
    salt = os.environ["PHONE_HASH_SALT"]
    canonical = normalize_phone(phone)
    return hashlib.sha256((salt + canonical).encode()).hexdigest()


def make_greeting(profile: dict) -> str:
    vehicles = profile.get("vehicles") or []
    if vehicles:
        model = vehicles[0].get("model", "xe")
        return (
            f"Chào anh/chị, XeCare xin nghe! Mình có thể hỗ trợ gì cho chiếc "
            f"{model} của mình hôm nay ạ?"
        )
    return "Chào anh/chị, XeCare xin nghe! Anh/chị cần hỗ trợ gì hôm nay ạ?"


def _get_profile(supabase, customer_id):
    if not customer_id:
        return {}
    rows = supabase.table("customer_profiles").select("*").eq("id", customer_id).execute()
    return rows.data[0] if rows.data else {}


def _load_history(supabase, conversation_id):
    history_rows = (
        supabase.table("messages")
        .select("sender, content_masked")
        .eq("conversation_id", conversation_id)
        .order("created_at", desc=True)
        .limit(10)
        .execute()
    )
    return [
        {
            "role": "user" if r["sender"] == "customer" else "assistant",
            "content": r["content_masked"] or "",
        }
        for r in reversed(history_rows.data or [])
    ]


def _build_state(conversation_id, customer_id, profile, history, text, session: Session, mode):
    return {
        "conversation_id": conversation_id,
        "customer_id": customer_id,
        "customer_profile": {
            "display_name": profile.get("display_name"),
            "vehicles": profile.get("vehicles", []),
            "facts": profile.get("facts", {}),
        },
        "messages": history,
        "raw_text": text,
        "pii_session": session.pii,
        "mode": mode,
        "slots": session.action.get("slots", {}),
        "pending_action": session.action.get("pending_action"),
        "emergency_session": session.emergency,
        "complaint_attempted": session.hitl.get("complaint_attempted", False),
        "handback_note": session.hitl.get("handback_note"),
        "guardrail_flags": {},
    }


def _apply_final_to_session(session: Session, final: dict) -> None:
    session.action = {
        "slots": final.get("slots") or {},
        "pending_action": final.get("pending_action"),
    }
    session.emergency = final.get("emergency_session") or {"open": False, "asks": 0}
    session.hitl = {
        "complaint_attempted": bool(final.get("complaint_attempted")),
        # handback note is consumed once — the turn after resolve has context, then cleared
        "handback_note": None,
    }


@router.post("/chat/start")
async def chat_start(body: StartRequest, request: Request):
    supabase = request.app.state.supabase
    digest = phone_hash(body.phone)

    found = (
        supabase.table("customer_profiles").select("*").eq("phone_hash", digest).execute()
    )
    if found.data:
        profile = found.data[0]
    else:
        created = (
            supabase.table("customer_profiles")
            .insert({"phone_hash": digest, "vehicles": [], "facts": {}})
            .execute()
        )
        profile = created.data[0]

    conv = (
        supabase.table("conversations")
        .insert({"customer_id": profile["id"], "mode": "agent", "channel": "widget"})
        .execute()
    )
    conversation_id = conv.data[0]["id"]

    # seed the persistent session with the customer's [PHONE_KH] map
    session = Session(pii=PIISession(customer_phone=body.phone))
    await store(request).save(conversation_id, session)
    return {"conversation_id": conversation_id, "greeting": make_greeting(profile)}


async def _run_turn(conversation_id, body_text, request):
    """Shared core of /message and /message_stream: returns (final, reply, session)
    after one load → graph → save, and persists the two messages."""
    app_state = request.app.state
    supabase = app_state.supabase

    conv = supabase.table("conversations").select("*").eq("id", conversation_id).execute()
    if not conv.data:
        raise HTTPException(status_code=404, detail="conversation not found")
    customer_id = conv.data[0]["customer_id"]
    session = await store(request).load(conversation_id)

    if conv.data[0]["mode"] == "human":
        # staff owns the conversation — persist the customer's masked message, no graph
        masked = session.pii.mask(body_text)
        await store(request).save(conversation_id, session)  # mask may add a placeholder
        supabase.table("messages").insert(
            {
                "conversation_id": conversation_id,
                "sender": "customer",
                "content": body_text,
                "content_masked": masked,
            }
        ).execute()
        return None, None, session

    profile = _get_profile(supabase, customer_id)
    history = _load_history(supabase, conversation_id)
    state = _build_state(
        conversation_id, customer_id, profile, history, body_text, session, conv.data[0]["mode"]
    )
    final = await app_state.chat_graph.ainvoke(state)
    _apply_final_to_session(session, final)
    await store(request).save(conversation_id, session)

    reply_masked = final.get("reply", "")
    reply = session.pii.unmask(reply_masked)
    masked_user_text = final.get("masked_text") or session.pii.mask(body_text)
    supabase.table("messages").insert(
        [
            {"conversation_id": conversation_id, "sender": "customer",
             "content": body_text, "content_masked": masked_user_text},
            {"conversation_id": conversation_id, "sender": "agent",
             "content": reply, "content_masked": reply_masked},
        ]
    ).execute()
    return final, reply, session


@router.post("/chat/{conversation_id}/message")
async def chat_message(conversation_id: str, body: MessageRequest, request: Request):
    final, reply, _ = await _run_turn(conversation_id, body.text, request)
    if final is None:
        return {"reply": None, "mode": "human"}
    return {
        "reply": reply,
        "citations": final.get("citations", []),
        "intent": final.get("intent"),
        "escalated": bool(final.get("escalated")),
        "pending_action": public_pending(final.get("pending_action")),
    }


@router.post("/chat/{conversation_id}/message_stream")
async def chat_message_stream(conversation_id: str, body: MessageRequest, request: Request):
    """SSE variant of /message. TRADE-OFF (TIP-008b): we do NOT stream raw LLM
    tokens to the client — the output guardrail must see the COMPLETE reply before
    anything is shown (a half-streamed sentence could leak content a rule would
    block). So 'streaming' here = real-time `status` events while the graph runs,
    then a single `final` event with the guardrailed + unmasked reply, then `done`.
    The sync /message endpoint stays the source of truth for CI."""

    async def event_gen():
        # the turn runs synchronously inside the graph; status events bracket it so the
        # UX shows progress, final carries the only customer-visible text.
        yield _sse("status", {"message": "Đang xử lý yêu cầu của anh/chị..."})
        try:
            final, reply, _ = await _run_turn(conversation_id, body.text, request)
        except HTTPException as exc:
            yield _sse("error", {"detail": exc.detail})
            return
        if final is None:
            yield _sse("final", {"reply": None, "mode": "human"})
            yield _sse("done", {"mode": "human"})
            return
        yield _sse("final", {"reply": reply})
        yield _sse("done", {
            "intent": final.get("intent"),
            "citations": final.get("citations", []),
            "escalated": bool(final.get("escalated")),
            "pending_action": public_pending(final.get("pending_action")),
        })

    return StreamingResponse(event_gen(), media_type="text/event-stream")


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/chat/{conversation_id}/confirm")
async def chat_confirm(conversation_id: str, body: ConfirmRequest, request: Request):
    """The ONLY path that may execute a write tool (Blueprint §6.3 confirm gate)."""
    app_state = request.app.state
    supabase = app_state.supabase

    conv = supabase.table("conversations").select("id").eq("id", conversation_id).execute()
    if not conv.data:
        raise HTTPException(status_code=404, detail="conversation not found")

    session = await store(request).load(conversation_id)
    trace = bind_trace(app_state, conversation_id)
    reply, executed, escalated, new_pending = await execute_pending_action(
        app_state.tools, trace, session.action.get("pending_action"), body.accept
    )
    session.action = {
        "slots": {} if executed else session.action.get("slots", {}),
        "pending_action": new_pending,
    }
    await store(request).save(conversation_id, session)

    from app.guardrails.output import run_guardrail_out

    guarded = await run_guardrail_out(reply, "template", app_state.policy)
    await trace(
        "guardrail_out",
        {"verdict": guarded.verdict, "reasons": guarded.reasons,
         "rules_hit": guarded.rules_hit, "branch": "template"},
    )
    reply = guarded.final_text

    supabase.table("messages").insert(
        {"conversation_id": conversation_id, "sender": "agent",
         "content": reply, "content_masked": reply}
    ).execute()

    return {
        "reply": reply,
        "executed": executed,
        "escalated": escalated,
        "pending_action": public_pending(new_pending),
    }


CLOSE_FACTS_SYSTEM = """Bạn trích thông tin để cập nhật hồ sơ khách XeCare từ hội thoại (đã ẩn
thông tin cá nhân). Trả về DUY NHẤT JSON:
{"facts": {<key>: <value> ...},  # chỉ dữ liệu xe/dịch vụ, KHÔNG thông tin cá nhân
 "last_km": <số km mới nhất khách nhắc, hoặc null>,
 "last_summary": <tóm tắt 1-2 câu nội dung phiên>}
KHÔNG ghi số điện thoại/biển số/email vào facts. Trường không có → bỏ qua/null."""


@router.post("/chat/{conversation_id}/close")
async def chat_close(conversation_id: str, body: CloseRequest, request: Request):
    """Close the conversation, extract durable facts, and wipe the PII map."""
    app_state = request.app.state
    supabase = app_state.supabase

    conv = supabase.table("conversations").select("*").eq("id", conversation_id).execute()
    if not conv.data:
        raise HTTPException(status_code=404, detail="conversation not found")
    if conv.data[0].get("closed_at"):
        return {"ok": True, "already_closed": True}  # idempotent no-op

    customer_id = conv.data[0]["customer_id"]
    supabase.table("conversations").update(
        {"closed_at": "now()", "resolution": body.resolution or "completed"}
    ).eq("id", conversation_id).execute()

    # extract facts from the masked transcript (1 Haiku call)
    msgs = (
        supabase.table("messages")
        .select("sender, content_masked")
        .eq("conversation_id", conversation_id)
        .order("created_at")
        .execute()
    ).data or []
    extracted = {}
    if msgs and customer_id:
        transcript = "\n".join(f"{m['sender']}: {m['content_masked'] or ''}" for m in msgs)
        result = await app_state.llm.complete(
            model="claude-haiku-4-5",
            system=CLOSE_FACTS_SYSTEM,
            messages=[{"role": "user", "content": transcript}],
            max_tokens=300,
            json_mode=True,
        )
        from app.graph.core import extract_json_object

        extracted = extract_json_object(result.text) or {}
        await bind_trace(app_state, conversation_id)(
            "llm_call",
            {"purpose": "close_facts", "model": "claude-haiku-4-5",
             "input_tokens": result.input_tokens, "output_tokens": result.output_tokens},
            latency_ms=result.latency_ms,
        )
        _apply_facts(supabase, customer_id, extracted)

    # wipe the PII map — conversation is closed, nothing left to unmask
    session = await store(request).load(conversation_id)
    store(request).drop_pii_map(session)
    await store(request).save(conversation_id, session)

    return {"ok": True, "facts": extracted.get("facts", {}), "last_km": extracted.get("last_km")}


def _apply_facts(supabase, customer_id, extracted: dict) -> None:
    rows = supabase.table("customer_profiles").select("*").eq("id", customer_id).execute()
    if not rows.data:
        return
    profile = rows.data[0]
    facts = {**(profile.get("facts") or {}), **(extracted.get("facts") or {})}
    if extracted.get("last_summary"):
        facts["last_summary"] = extracted["last_summary"]
    update = {"facts": facts}

    last_km = extracted.get("last_km")
    vehicles = profile.get("vehicles") or []
    if last_km and vehicles:
        # update the first vehicle's odometer if the customer mentioned a higher reading
        try:
            if int(last_km) >= int(vehicles[0].get("last_km") or 0):
                vehicles[0]["last_km"] = int(last_km)
                update["vehicles"] = vehicles
        except (TypeError, ValueError):
            pass
    supabase.table("customer_profiles").update(update).eq("id", customer_id).execute()

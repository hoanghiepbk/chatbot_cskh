"""Chat API: /chat/start + /chat/{conversation_id}/message.

Sync JSON responses for v1 — TODO: SSE streaming in a later TIP.
The real phone number lives ONLY here (app layer): hashed for profile lookup,
registered as [PHONE_KH] in the per-conversation PIISession, never persisted
in conversations/messages.
"""

import hashlib
import os
import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.graph.action import execute_pending_action, public_pending
from app.guardrails.pii import PIISession, normalize_phone

router = APIRouter()

# Per-conversation PII sessions, in-memory with a simple TTL.
# Lost on restart (placeholder map gone → old placeholders stay masked).
# TODO: move to Redis for multi-worker / restart-safe sessions.
SESSION_TTL_SECONDS = 24 * 3600
_pii_sessions: dict[str, tuple[PIISession, float]] = {}

# TIP-006: multi-turn action state (slots + pending_action), same in-memory TTL
# pattern as PIISession — TIP-008 persists these in a conversations jsonb column.
_action_sessions: dict[str, tuple[dict, float]] = {}

# TIP-007: open-emergency state machine ({'open': bool, 'asks': int})
_emergency_sessions: dict[str, tuple[dict, float]] = {}

# TIP-008: HITL conversation state ({'complaint_attempted': bool, 'handback_note': str|None})
_hitl_sessions: dict[str, tuple[dict, float]] = {}


def get_pii_session(conversation_id: str) -> PIISession:
    now = time.time()
    for key in [k for k, (_, ts) in _pii_sessions.items() if now - ts > SESSION_TTL_SECONDS]:
        del _pii_sessions[key]
    if conversation_id not in _pii_sessions:
        _pii_sessions[conversation_id] = (PIISession(), now)
    return _pii_sessions[conversation_id][0]


def peek_pii_session(conversation_id: str) -> PIISession | None:
    """Return the session only if it exists and is unexpired — never creates one.
    Used by reveal_contact to distinguish 'no map' (410 Gone) from a fresh map."""
    entry = _pii_sessions.get(conversation_id)
    if not entry:
        return None
    session, ts = entry
    if time.time() - ts > SESSION_TTL_SECONDS:
        del _pii_sessions[conversation_id]
        return None
    return session


def register_pii_session(conversation_id: str, session: PIISession) -> None:
    _pii_sessions[conversation_id] = (session, time.time())


def get_hitl_session(conversation_id: str) -> dict:
    now = time.time()
    for key in [k for k, (_, ts) in _hitl_sessions.items() if now - ts > SESSION_TTL_SECONDS]:
        del _hitl_sessions[key]
    if conversation_id not in _hitl_sessions:
        _hitl_sessions[conversation_id] = ({"complaint_attempted": False, "handback_note": None}, now)
    return _hitl_sessions[conversation_id][0]


def set_handback_note(conversation_id: str, note: str | None) -> None:
    session = get_hitl_session(conversation_id)
    session["handback_note"] = note


def get_action_session(conversation_id: str) -> dict:
    now = time.time()
    for key in [
        k for k, (_, ts) in _action_sessions.items() if now - ts > SESSION_TTL_SECONDS
    ]:
        del _action_sessions[key]
    if conversation_id not in _action_sessions:
        _action_sessions[conversation_id] = ({"slots": {}, "pending_action": None}, now)
    return _action_sessions[conversation_id][0]


def get_emergency_session(conversation_id: str) -> dict:
    now = time.time()
    for key in [
        k for k, (_, ts) in _emergency_sessions.items() if now - ts > SESSION_TTL_SECONDS
    ]:
        del _emergency_sessions[key]
    if conversation_id not in _emergency_sessions:
        _emergency_sessions[conversation_id] = ({"open": False, "asks": 0}, now)
    return _emergency_sessions[conversation_id][0]


def set_emergency_session(conversation_id: str, session: dict) -> None:
    _emergency_sessions[conversation_id] = (session, time.time())


def bind_trace(app_state, conversation_id: str):
    """Conversation-bound trace for tool calls outside the graph (/confirm)."""
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

    register_pii_session(conversation_id, PIISession(customer_phone=body.phone))
    return {"conversation_id": conversation_id, "greeting": make_greeting(profile)}


@router.post("/chat/{conversation_id}/message")
async def chat_message(conversation_id: str, body: MessageRequest, request: Request):
    app_state = request.app.state
    supabase = app_state.supabase

    conv = (
        supabase.table("conversations").select("*").eq("id", conversation_id).execute()
    )
    if not conv.data:
        raise HTTPException(status_code=404, detail="conversation not found")
    customer_id = conv.data[0]["customer_id"]

    # TIP-008: while a human staff member owns the conversation, the agent graph
    # is OFF — just persist the customer's message (masked) and let Realtime
    # deliver it to the staff console. 0 LLM calls.
    if conv.data[0]["mode"] == "human":
        pii_session = get_pii_session(conversation_id)
        supabase.table("messages").insert(
            {
                "conversation_id": conversation_id,
                "sender": "customer",
                "content": body.text,
                "content_masked": pii_session.mask(body.text),
            }
        ).execute()
        return {"reply": None, "mode": "human"}

    profile = {}
    if customer_id:
        rows = (
            supabase.table("customer_profiles").select("*").eq("id", customer_id).execute()
        )
        profile = rows.data[0] if rows.data else {}

    history_rows = (
        supabase.table("messages")
        .select("sender, content_masked")
        .eq("conversation_id", conversation_id)
        .order("created_at", desc=True)
        .limit(10)
        .execute()
    )
    history = [
        {
            "role": "user" if r["sender"] == "customer" else "assistant",
            "content": r["content_masked"] or "",
        }
        for r in reversed(history_rows.data or [])
    ]

    pii_session = get_pii_session(conversation_id)
    action_session = get_action_session(conversation_id)
    emergency_session = get_emergency_session(conversation_id)
    hitl_session = get_hitl_session(conversation_id)

    state = {
        "conversation_id": conversation_id,
        "customer_id": customer_id,
        "customer_profile": {
            "display_name": profile.get("display_name"),
            "vehicles": profile.get("vehicles", []),
            "facts": profile.get("facts", {}),
        },
        "messages": history,
        "raw_text": body.text,
        "pii_session": pii_session,
        "mode": conv.data[0]["mode"],
        "slots": action_session["slots"],
        "pending_action": action_session["pending_action"],
        "emergency_session": emergency_session,
        "complaint_attempted": hitl_session["complaint_attempted"],
        "handback_note": hitl_session["handback_note"],
        "guardrail_flags": {},
    }
    final = await app_state.chat_graph.ainvoke(state)

    action_session["slots"] = final.get("slots") or {}
    action_session["pending_action"] = final.get("pending_action")
    set_emergency_session(
        conversation_id, final.get("emergency_session") or {"open": False, "asks": 0}
    )
    hitl_session["complaint_attempted"] = bool(final.get("complaint_attempted"))
    # handback note is consumed once — the turn after a resolve answers with context,
    # then it's cleared so it never staleens later replies
    hitl_session["handback_note"] = None

    reply_masked = final.get("reply", "")
    reply = pii_session.unmask(reply_masked)
    masked_user_text = final.get("masked_text") or pii_session.mask(body.text)

    supabase.table("messages").insert(
        [
            {
                "conversation_id": conversation_id,
                "sender": "customer",
                "content": body.text,
                "content_masked": masked_user_text,
            },
            {
                "conversation_id": conversation_id,
                "sender": "agent",
                "content": reply,
                "content_masked": reply_masked,
            },
        ]
    ).execute()

    return {
        "reply": reply,
        "citations": final.get("citations", []),
        "intent": final.get("intent"),
        "escalated": bool(final.get("escalated")),
        # summary-only view for the widget confirm card — no internal ids
        "pending_action": public_pending(final.get("pending_action")),
    }


@router.post("/chat/{conversation_id}/confirm")
async def chat_confirm(conversation_id: str, body: ConfirmRequest, request: Request):
    """The ONLY path that may execute a write tool (Blueprint §6.3 confirm gate)."""
    app_state = request.app.state
    supabase = app_state.supabase

    conv = (
        supabase.table("conversations").select("id").eq("id", conversation_id).execute()
    )
    if not conv.data:
        raise HTTPException(status_code=404, detail="conversation not found")

    action_session = get_action_session(conversation_id)
    trace = bind_trace(app_state, conversation_id)
    reply, executed, escalated, new_pending = await execute_pending_action(
        app_state.tools,
        trace,
        action_session["pending_action"],
        body.accept,
    )
    action_session["pending_action"] = new_pending
    if executed:
        action_session["slots"] = {}

    # TIP-007: every reply passes the output guardrail — template branch here,
    # so hard rules only (no rubric call)
    from app.guardrails.output import run_guardrail_out

    guarded = await run_guardrail_out(reply, "template", app_state.policy)
    await trace(
        "guardrail_out",
        {
            "verdict": guarded.verdict,
            "reasons": guarded.reasons,
            "rules_hit": guarded.rules_hit,
            "branch": "template",
        },
    )
    reply = guarded.final_text

    # keep the transcript complete for console/history (button click has no customer text)
    supabase.table("messages").insert(
        {
            "conversation_id": conversation_id,
            "sender": "agent",
            "content": reply,
            "content_masked": reply,
        }
    ).execute()

    return {
        "reply": reply,
        "executed": executed,
        "escalated": escalated,
        "pending_action": public_pending(new_pending),
    }

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

from app.guardrails.pii import PIISession, normalize_phone

router = APIRouter()

# Per-conversation PII sessions, in-memory with a simple TTL.
# Lost on restart (placeholder map gone → old placeholders stay masked).
# TODO: move to Redis for multi-worker / restart-safe sessions.
SESSION_TTL_SECONDS = 24 * 3600
_pii_sessions: dict[str, tuple[PIISession, float]] = {}


def get_pii_session(conversation_id: str) -> PIISession:
    now = time.time()
    for key in [k for k, (_, ts) in _pii_sessions.items() if now - ts > SESSION_TTL_SECONDS]:
        del _pii_sessions[key]
    if conversation_id not in _pii_sessions:
        _pii_sessions[conversation_id] = (PIISession(), now)
    return _pii_sessions[conversation_id][0]


def register_pii_session(conversation_id: str, session: PIISession) -> None:
    _pii_sessions[conversation_id] = (session, time.time())


class StartRequest(BaseModel):
    phone: str


class MessageRequest(BaseModel):
    text: str


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

    state = {
        "conversation_id": conversation_id,
        "customer_profile": {
            "vehicles": profile.get("vehicles", []),
            "facts": profile.get("facts", {}),
        },
        "messages": history,
        "raw_text": body.text,
        "pii_session": pii_session,
        "mode": conv.data[0]["mode"],
        "slots": {},
        "guardrail_flags": {},
    }
    final = await app_state.chat_graph.ainvoke(state)

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
    }

"""Staff (HITL) API — queue, claim, live chat, resolve, reveal (TIP-008).

AUTH — THREAT MODEL (demo-grade, intentional): a single shared bearer token in
env STAFF_API_TOKEN. This is NOT production auth — there is no per-staff identity,
no rotation, no audit of *who* acted, and the token grants full staff power
including PII reveal. Production must replace this with Supabase Auth + a 'staff'
role and per-user RLS. The TIP-014 console will send this same token for now.
"""

import os

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel

from app.api.chat import (
    bind_trace,
    get_pii_session,
    peek_pii_session,
    set_handback_note,
)
from app.llm import MODEL_HAIKU

router = APIRouter(prefix="/staff")

PRIORITY_RANK = {"urgent": 0, "high": 1, "normal": 2}
QUEUE_TYPES = ("complaint", "rescue", "after_hours")
JOIN_MARKER = "[Nhân viên XeCare đã tham gia hỗ trợ]"
RESOLVE_MARKER = "[Nhân viên đã hoàn tất hỗ trợ — trợ lý tự động tiếp tục phục vụ]"

RESOLVE_SUMMARY_SYSTEM = """Bạn tóm tắt đoạn hội thoại mà NHÂN VIÊN vừa xử lý với khách (đã ẩn
thông tin cá nhân), để trợ lý tự động tiếp tục phục vụ có ngữ cảnh. Trả về 2-3 câu tiếng Việt:
nhân viên đã làm gì, kết luận/cam kết gì với khách. Không bịa."""


def require_staff(authorization: str | None = Header(default=None)) -> None:
    expected = os.environ.get("STAFF_API_TOKEN")
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    if not expected or token != expected:
        raise HTTPException(status_code=401, detail="invalid staff token")


class StaffMessage(BaseModel):
    text: str


@router.get("/queue", dependencies=[Depends(require_staff)])
async def staff_queue(request: Request):
    supabase = request.app.state.supabase
    rows = (
        supabase.table("tickets")
        .select("*")
        .in_("status", ["open", "claimed"])
        .in_("type", list(QUEUE_TYPES))
        .execute()
    )
    tickets = sorted(
        rows.data or [],
        key=lambda t: (PRIORITY_RANK.get(t["priority"], 9), t["created_at"]),
    )
    return {"tickets": tickets}


@router.post("/tickets/{ticket_id}/claim", dependencies=[Depends(require_staff)])
async def staff_claim(ticket_id: str, request: Request):
    app_state = request.app.state
    supabase = app_state.supabase

    rows = supabase.table("tickets").select("*").eq("id", ticket_id).execute()
    if not rows.data:
        raise HTTPException(status_code=404, detail="ticket not found")
    ticket = rows.data[0]
    if ticket["status"] != "open":
        raise HTTPException(status_code=409, detail=f"ticket is {ticket['status']}")

    supabase.table("tickets").update({"status": "claimed"}).eq("id", ticket_id).execute()

    conversation_id = ticket.get("conversation_id")
    if conversation_id:
        supabase.table("conversations").update({"mode": "human"}).eq(
            "id", conversation_id
        ).execute()
        supabase.table("messages").insert(
            {
                "conversation_id": conversation_id,
                "sender": "staff",
                "content": JOIN_MARKER,
                "content_masked": JOIN_MARKER,
            }
        ).execute()
        await bind_trace(app_state, conversation_id)(
            "escalation", {"step": "claimed", "ticket_id": ticket_id}
        )
    return {"ok": True, "ticket_id": ticket_id, "mode": "human" if conversation_id else None}


@router.post("/conversations/{conversation_id}/message", dependencies=[Depends(require_staff)])
async def staff_message(conversation_id: str, body: StaffMessage, request: Request):
    supabase = request.app.state.supabase
    conv = (
        supabase.table("conversations").select("mode").eq("id", conversation_id).execute()
    )
    if not conv.data:
        raise HTTPException(status_code=404, detail="conversation not found")
    if conv.data[0]["mode"] != "human":
        raise HTTPException(status_code=409, detail="conversation is not in human mode")

    # staff may type a phone number — the public (masked) version must still mask it
    pii_session = get_pii_session(conversation_id)
    supabase.table("messages").insert(
        {
            "conversation_id": conversation_id,
            "sender": "staff",
            "content": body.text,
            "content_masked": pii_session.mask(body.text),
        }
    ).execute()
    return {"ok": True}


@router.post("/tickets/{ticket_id}/resolve", dependencies=[Depends(require_staff)])
async def staff_resolve(ticket_id: str, request: Request):
    app_state = request.app.state
    supabase = app_state.supabase

    rows = supabase.table("tickets").select("*").eq("id", ticket_id).execute()
    if not rows.data:
        raise HTTPException(status_code=404, detail="ticket not found")
    ticket = rows.data[0]

    supabase.table("tickets").update({"status": "resolved"}).eq("id", ticket_id).execute()

    conversation_id = ticket.get("conversation_id")
    note = None
    if conversation_id:
        supabase.table("conversations").update({"mode": "agent"}).eq(
            "id", conversation_id
        ).execute()

        # summarise the human segment (messages after the join marker), masked
        msg_rows = (
            supabase.table("messages")
            .select("sender, content_masked, content")
            .eq("conversation_id", conversation_id)
            .order("created_at")
            .execute()
        )
        all_msgs = msg_rows.data or []
        start = 0
        for i, m in enumerate(all_msgs):
            if m["sender"] == "staff" and (m["content"] or "") == JOIN_MARKER:
                start = i + 1
        human_segment = [
            m for m in all_msgs[start:] if (m["content"] or "") != RESOLVE_MARKER
        ]
        if human_segment:
            transcript = "\n".join(
                f"{m['sender']}: {m['content_masked'] or ''}" for m in human_segment
            )
            result = await app_state.llm.complete(
                model=MODEL_HAIKU,
                system=RESOLVE_SUMMARY_SYSTEM,
                messages=[{"role": "user", "content": transcript}],
                max_tokens=200,
            )
            note = result.text.strip()
            await bind_trace(app_state, conversation_id)(
                "llm_call",
                {
                    "purpose": "resolve_summary",
                    "model": MODEL_HAIKU,
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                },
                latency_ms=result.latency_ms,
            )
        set_handback_note(conversation_id, note)
        supabase.table("messages").insert(
            {
                "conversation_id": conversation_id,
                "sender": "staff",
                "content": RESOLVE_MARKER,
                "content_masked": RESOLVE_MARKER,
            }
        ).execute()
        await bind_trace(app_state, conversation_id)(
            "escalation", {"step": "resolved", "ticket_id": ticket_id}
        )
    return {"ok": True, "ticket_id": ticket_id, "handback_note": note}


@router.post("/tickets/{ticket_id}/reveal_contact", dependencies=[Depends(require_staff)])
async def staff_reveal_contact(ticket_id: str, request: Request):
    """Audited PII reveal (TIP-007 acceptance decision). Returns the real callback
    number from the conversation's PIISession. The trace records ONLY the ticket id
    — never the number (trace helper would also reject a raw number)."""
    app_state = request.app.state
    supabase = app_state.supabase

    rows = supabase.table("tickets").select("*").eq("id", ticket_id).execute()
    if not rows.data:
        raise HTTPException(status_code=404, detail="ticket not found")
    ticket = rows.data[0]
    conversation_id = ticket.get("conversation_id")
    if not conversation_id:
        raise HTTPException(status_code=404, detail="ticket has no conversation")

    session = peek_pii_session(conversation_id)
    if session is None:
        # in-memory session expired — persistence is TIP-008b (documented)
        raise HTTPException(
            status_code=410,
            detail="contact map expired for this conversation (session persistence is TIP-008b)",
        )

    placeholder = (ticket.get("payload") or {}).get("callback_placeholder") or "[PHONE_KH]"
    value = session.unmask(placeholder)
    await bind_trace(app_state, conversation_id)(
        "escalation", {"step": "pii_reveal", "ticket_id": ticket_id}
    )
    return {"placeholder": placeholder, "value": value}

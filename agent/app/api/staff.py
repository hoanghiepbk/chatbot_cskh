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

from app.api.chat import bind_trace
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


def _percentile(values: list[float], p: float) -> float:
    """Linear-interpolation percentile (p in [0,1]); 0 for empty input."""
    if not values:
        return 0.0
    ordered = sorted(values)
    k = (len(ordered) - 1) * p
    floor = int(k)
    ceil = min(floor + 1, len(ordered) - 1)
    if floor == ceil:
        return float(ordered[floor])
    return float(ordered[floor] + (ordered[ceil] - ordered[floor]) * (k - floor))


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
    session_store = request.app.state.session_store
    session = await session_store.load(conversation_id)
    masked = session.pii.mask(body.text)
    await session_store.save(conversation_id, session)  # mask may add a placeholder
    supabase.table("messages").insert(
        {
            "conversation_id": conversation_id,
            "sender": "staff",
            "content": body.text,
            "content_masked": masked,
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
        session_store = app_state.session_store
        session = await session_store.load(conversation_id)
        session.hitl = {**session.hitl, "handback_note": note}
        await session_store.save(conversation_id, session)
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

    # TIP-008b: session is persisted in DB now (survives restart). A closed
    # conversation has its PII map wiped → no value to reveal → 404.
    session = await app_state.session_store.load(conversation_id)
    placeholder = (ticket.get("payload") or {}).get("callback_placeholder") or "[PHONE_KH]"
    value = session.pii.unmask(placeholder)
    if value == placeholder:
        raise HTTPException(
            status_code=404,
            detail="no contact on record for this conversation (closed or never captured)",
        )
    await bind_trace(app_state, conversation_id)(
        "escalation", {"step": "pii_reveal", "ticket_id": ticket_id}
    )
    return {"placeholder": placeholder, "value": value}


# ============ TIP-014: read endpoints for the staff console ============
# Service-role reads, returning MASKED data only (display_name, content_masked,
# trace payloads already masked upstream by trace.py). No raw phone anywhere —
# the real number is reachable ONLY via reveal_contact (audited) above.


@router.get("/conversations", dependencies=[Depends(require_staff)])
async def staff_conversations(
    request: Request,
    mode: str | None = None,
    escalated: bool | None = None,
    limit: int = 50,
):
    """List conversations + last router intent + escalated flag + message count.
    Powers the Trace Explorer table. `escalated` filters in-memory after the
    limited page is built (demo-grade; fine for the data volumes here)."""
    supabase = request.app.state.supabase
    query = supabase.table("conversations").select(
        "id, customer_id, mode, started_at, closed_at, resolution"
    )
    if mode in ("agent", "human"):
        query = query.eq("mode", mode)
    convs = query.order("started_at", desc=True).limit(limit).execute().data or []
    ids = [c["id"] for c in convs]
    if not ids:
        return {"conversations": []}

    names: dict = {}
    cust_ids = [c["customer_id"] for c in convs if c["customer_id"]]
    if cust_ids:
        rows = (
            supabase.table("customer_profiles")
            .select("id, display_name")
            .in_("id", cust_ids)
            .execute()
            .data
            or []
        )
        names = {r["id"]: r["display_name"] for r in rows}

    counts: dict = {}
    msg_rows = (
        supabase.table("messages").select("conversation_id").in_("conversation_id", ids).execute().data
        or []
    )
    for m in msg_rows:
        counts[m["conversation_id"]] = counts.get(m["conversation_id"], 0) + 1

    last_intent: dict = {}
    escalated_ids: set = set()
    trace_rows = (
        supabase.table("trace_events")
        .select("conversation_id, step_type, payload, created_at")
        .in_("conversation_id", ids)
        .in_("step_type", ["router", "escalation"])
        .order("created_at")
        .execute()
        .data
        or []
    )
    for t in trace_rows:
        cid = t["conversation_id"]
        if t["step_type"] == "router":
            intent = (t["payload"] or {}).get("intent")
            if intent:
                last_intent[cid] = intent  # ascending order → last write wins
        elif t["step_type"] == "escalation":
            escalated_ids.add(cid)

    items = [
        {
            "id": c["id"],
            "display_name": names.get(c["customer_id"]),
            "mode": c["mode"],
            "message_count": counts.get(c["id"], 0),
            "last_intent": last_intent.get(c["id"]),
            "escalated": c["id"] in escalated_ids,
            "started_at": c["started_at"],
            "closed_at": c["closed_at"],
            "resolution": c["resolution"],
        }
        for c in convs
    ]
    if escalated is not None:
        items = [it for it in items if it["escalated"] == escalated]
    return {"conversations": items}


@router.get("/conversations/{conversation_id}/trace", dependencies=[Depends(require_staff)])
async def staff_conversation_trace(conversation_id: str, request: Request):
    """All trace_events for one conversation (chronological) + a session summary.
    Payloads are masked at write time (trace.py rejects raw PII)."""
    supabase = request.app.state.supabase
    rows = (
        supabase.table("trace_events")
        .select("*")
        .eq("conversation_id", conversation_id)
        .order("created_at")
        .execute()
        .data
        or []
    )
    total_cost = sum(float(r["cost_usd"]) for r in rows if r["cost_usd"] is not None)
    total_latency = sum(int(r["latency_ms"]) for r in rows if r["latency_ms"] is not None)
    return {
        "events": rows,
        "summary": {
            "event_count": len(rows),
            "total_cost_usd": total_cost,
            "total_latency_ms": total_latency,
            "llm_calls": sum(1 for r in rows if r["step_type"] == "llm_call"),
            "escalated": any(r["step_type"] == "escalation" for r in rows),
        },
    }


@router.get("/metrics", dependencies=[Depends(require_staff)])
async def staff_metrics(request: Request):
    """Aggregate KPIs for the Ops Dashboard, computed in-memory over a recent
    window (conversations + trace_events). Window sizes are returned so the UI
    can show what was scanned — no silent truncation."""
    supabase = request.app.state.supabase
    convs = (
        supabase.table("conversations")
        .select("id, closed_at")
        .order("started_at", desc=True)
        .limit(2000)
        .execute()
        .data
        or []
    )
    total = len(convs)
    conv_ids = {c["id"] for c in convs}
    resolved = sum(1 for c in convs if c["closed_at"])

    traces = (
        supabase.table("trace_events")
        .select("conversation_id, step_type, payload, cost_usd, latency_ms, created_at")
        .order("created_at", desc=True)
        .limit(10000)
        .execute()
        .data
        or []
    )
    latency_by_conv: dict = {}
    intents: dict = {}
    reasons: dict = {}
    escalated_ids: set = set()
    cost_by_day: dict = {}
    total_cost = 0.0
    # [TIP-015] cache stats: a faq turn is either a cache_hit OR a retrieval (miss).
    cache_hits = 0
    faq_misses = 0
    faq_answer_cost = 0.0
    faq_answer_count = 0
    for t in traces:
        cid = t["conversation_id"]
        payload = t["payload"] or {}
        if t["cost_usd"] is not None:
            cost = float(t["cost_usd"])
            total_cost += cost
            day = (t["created_at"] or "")[:10]
            if day:
                cost_by_day[day] = cost_by_day.get(day, 0.0) + cost
            if t["step_type"] == "llm_call" and payload.get("purpose") == "faq_answer":
                faq_answer_cost += cost
                faq_answer_count += 1
        if t["latency_ms"] is not None:
            latency_by_conv[cid] = latency_by_conv.get(cid, 0) + int(t["latency_ms"])
        if t["step_type"] == "router":
            intent = payload.get("intent")
            if intent:
                intents[intent] = intents.get(intent, 0) + 1
        elif t["step_type"] == "escalation":
            escalated_ids.add(cid)
            reason = payload.get("reason")
            if reason:
                reasons[reason] = reasons.get(reason, 0) + 1
        elif t["step_type"] == "cache_hit":
            cache_hits += 1
        elif t["step_type"] == "retrieval":
            faq_misses += 1

    faq_turns = cache_hits + faq_misses
    cache_hit_rate = (cache_hits / faq_turns) if faq_turns else None
    avg_faq_cost = (faq_answer_cost / faq_answer_count) if faq_answer_count else 0.0
    cache_savings_usd = round(cache_hits * avg_faq_cost, 6)

    escalated = len(escalated_ids & conv_ids) if conv_ids else 0
    latency_values = list(latency_by_conv.values())
    return {
        "totals": {"conversations": total, "resolved": resolved, "escalated": escalated},
        "resolution_rate": (resolved / total) if total else 0.0,
        "escalation_rate": (escalated / total) if total else 0.0,
        "avg_cost_usd": (total_cost / total) if total else 0.0,
        "latency_ms": {
            "p50": _percentile(latency_values, 0.5),
            "p95": _percentile(latency_values, 0.95),
        },
        "cache_hit_rate": cache_hit_rate,  # [TIP-015] cache_hit / faq turns
        "cache_savings_usd": cache_savings_usd,  # [TIP-015] hits × avg faq Sonnet cost
        "faq_turns": faq_turns,
        "intent_distribution": [
            {"intent": k, "count": v}
            for k, v in sorted(intents.items(), key=lambda kv: -kv[1])
        ],
        "escalation_reasons": [
            {"reason": k, "count": v}
            for k, v in sorted(reasons.items(), key=lambda kv: -kv[1])
        ],
        "cost_by_day": [
            {"date": d, "cost_usd": round(v, 6)} for d, v in sorted(cost_by_day.items())[-7:]
        ],
        "window": {"conversations_scanned": total, "trace_events_scanned": len(traces)},
    }


@router.get("/eval-runs", dependencies=[Depends(require_staff)])
async def staff_eval_runs(request: Request, limit: int = 50):
    """Recent eval_runs for the Eval Dashboard (metrics jsonb returned as-is)."""
    supabase = request.app.state.supabase
    rows = (
        supabase.table("eval_runs")
        .select("*")
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
        .data
        or []
    )
    return {"eval_runs": rows}


@router.get("/knowledge-gaps", dependencies=[Depends(require_staff)])
async def staff_knowledge_gaps(request: Request, limit: int = 200):
    """[TIP-015] Cluster recent unanswerable faq turns (masked queries) so the
    team can see which KB topics are missing. Greedy cosine clustering in-app."""
    from app.insights.gap import GapDetector, greedy_cluster

    detector = GapDetector(request.app.state.supabase)
    events = detector.recent(limit)
    clusters = greedy_cluster(events)
    return {"clusters": clusters, "total_events": len(events)}

"""TIP-008 HITL DB tests — staff API + human mode + reveal, via TestClient.

Uses the real local Supabase (skip if absent) but a SmartLLM (no network) and a
monkeypatched clock for deterministic business-hours. A minimal app wires the
chat + staff routers with manually-set app.state (no heavy model lifespan).
"""

import json
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.chat as chat_mod
import app.graph.escalate as escalate_mod
from app.api.chat import router as chat_router
from app.api.staff import router as staff_router
from app.graph.core import GraphDeps, build_graph
from app.llm import LLMResult
from app.tools import build_tools
from app.trace import log_trace
from tests.conftest import requires_db

pytestmark = requires_db

STAFF_TOKEN = "test-staff-token"
RAW_PHONE_RE = re.compile(r"(?<!\d)(?:\+84|0)(?:[\s.\-]?\d){9}(?!\d)")
POLICY = {"escalate_confidence_below": 0.7, "injection_threshold": 0.5, "refund_cap_vnd": 2_000_000}


class SmartLLM:
    """Routes responses by inspecting the system prompt — robust to call order."""

    def __init__(self):
        self.next_intent = "chitchat"

    async def complete(self, model, system, messages, max_tokens, json_mode=False):
        if "phân loại intent" in system:
            text = json.dumps({"intent": self.next_intent, "confidence": 0.95})
        elif "phàn nàn/khiếu nại" in system:  # complaint analyze
            text = json.dumps({"topic": "x", "needs_kb": False, "severity": "low"})
        elif "tóm tắt hội thoại CSKH XeCare cho nhân viên" in system:  # handoff
            text = json.dumps({"summary": "khách bực vì xe vẫn kêu", "suggested_action": "gọi lại"})
        elif "NHÂN VIÊN vừa xử lý" in system:  # resolve summary
            text = "Nhân viên đã kiểm tra và hẹn khách mang xe lại sáng mai."
        elif "kiểm duyệt đầu ra" in system:  # output rubric
            text = ('{"promises_outside_policy": false, "unsafe_advice": false, '
                    '"reveals_internal": false, "off_domain": false}')
        elif "Khách đang phàn nàn" in system:  # complaint resolve
            text = "Dạ mình rất xin lỗi anh/chị về trải nghiệm chưa tốt ạ."
        else:  # chitchat / fallback
            text = "Dạ vâng, mình luôn sẵn sàng hỗ trợ anh/chị ạ!"
        return LLMResult(text=text, input_tokens=10, output_tokens=5, cost_usd=0.0, latency_ms=1)


@pytest.fixture
def hitl_app(supabase, monkeypatch):
    monkeypatch.setenv("STAFF_API_TOKEN", STAFF_TOKEN)
    monkeypatch.setattr(escalate_mod, "is_business_hours", lambda *a: True)
    # isolate in-memory session dicts per test
    chat_mod._pii_sessions.clear()
    chat_mod._action_sessions.clear()
    chat_mod._emergency_sessions.clear()
    chat_mod._hitl_sessions.clear()

    llm = SmartLLM()

    async def fake_search(query, top_k=5):
        return []

    deps = GraphDeps(
        llm=llm, system_prompt="SYS", prompt_version=2, policy=POLICY, policy_version=2,
        search=fake_search, trace=log_trace, tools=build_tools(supabase), supabase=supabase,
    )
    app = FastAPI()
    app.state.supabase = supabase
    app.state.llm = llm
    app.state.policy = POLICY
    app.state.prompt_version = 2
    app.state.policy_version = 2
    app.state.tools = build_tools(supabase)
    app.state.chat_graph = build_graph(deps)
    app.include_router(chat_router)
    app.include_router(staff_router)
    client = TestClient(app)
    yield client, llm, supabase


def auth():
    return {"Authorization": f"Bearer {STAFF_TOKEN}"}


def cleanup(supabase, conversation_id):
    supabase.table("trace_events").delete().eq("conversation_id", conversation_id).execute()
    supabase.table("tickets").delete().eq("conversation_id", conversation_id).execute()
    supabase.table("messages").delete().eq("conversation_id", conversation_id).execute()
    supabase.table("conversations").delete().eq("id", conversation_id).execute()


# ---------- full HITL round trip ----------

def test_full_hitl_scenario(hitl_app):
    client, llm, supabase = hitl_app
    cid = client.post("/chat/start", json={"phone": "+84901000003"}).json()["conversation_id"]
    try:
        llm.next_intent = "complaint"
        # turn 1 — agent attempts to resolve, no ticket yet
        r1 = client.post(f"/chat/{cid}/message", json={"text": "làm xe xong vẫn kêu, bực quá"}).json()
        assert r1["intent"] == "complaint"
        assert supabase.table("tickets").select("id").eq("conversation_id", cid).execute().data == []

        # turn 2 — escalates → real complaint ticket + "joins now"
        r2 = client.post(f"/chat/{cid}/message", json={"text": "vẫn chưa được, tôi muốn gặp người"}).json()
        assert "vào ngay" in r2["reply"]
        tickets = supabase.table("tickets").select("*").eq("conversation_id", cid).execute().data
        assert len(tickets) == 1 and tickets[0]["type"] == "complaint" and tickets[0]["status"] == "open"
        handoff = tickets[0]["payload"]
        assert set(handoff) == {"reason", "summary", "customer", "recent_messages",
                                "intents", "tool_calls", "suggested_action"}
        assert handoff["customer"]["display_name"] == "Anh Minh"
        assert "phone" not in handoff["customer"]
        # recent messages all masked — no raw phone digits
        assert not RAW_PHONE_RE.search(json.dumps(handoff["recent_messages"], ensure_ascii=False))
        assert "complaint" in handoff["intents"]

        ticket_id = tickets[0]["id"]

        # queue shows it
        queue = client.get("/staff/queue", headers=auth()).json()["tickets"]
        assert any(t["id"] == ticket_id for t in queue)

        # claim → human mode + join marker; second claim → 409
        assert client.post(f"/staff/tickets/{ticket_id}/claim", headers=auth()).status_code == 200
        assert supabase.table("conversations").select("mode").eq("id", cid).execute().data[0]["mode"] == "human"
        assert client.post(f"/staff/tickets/{ticket_id}/claim", headers=auth()).status_code == 409

        # staff types a phone → public masked version hides it
        client.post(f"/staff/conversations/{cid}/message", headers=auth(),
                    json={"text": "Anh gọi em số 0912345678 nhé"})
        staff_msg = (
            supabase.table("messages").select("content, content_masked")
            .eq("conversation_id", cid).eq("sender", "staff").order("created_at", desc=True)
            .limit(1).execute().data[0]
        )
        assert "0912345678" in staff_msg["content"]
        assert "0912345678" not in staff_msg["content_masked"]
        assert "[PHONE_" in staff_msg["content_masked"]

        # customer message in human mode → no agent reply, row saved
        r_human = client.post(f"/chat/{cid}/message", json={"text": "vâng em chờ"}).json()
        assert r_human == {"reply": None, "mode": "human"}

        # staff message blocked once back in agent mode happens after resolve:
        resolve = client.post(f"/staff/tickets/{ticket_id}/resolve", headers=auth()).json()
        assert resolve["handback_note"]
        assert supabase.table("conversations").select("mode").eq("id", cid).execute().data[0]["mode"] == "agent"
        assert supabase.table("tickets").select("status").eq("id", ticket_id).execute().data[0]["status"] == "resolved"

        # staff message now 409 (back to agent)
        assert client.post(f"/staff/conversations/{cid}/message", headers=auth(),
                           json={"text": "x"}).status_code == 409

        # next customer turn → agent answers again (has context internally)
        llm.next_intent = "chitchat"
        r_after = client.post(f"/chat/{cid}/message", json={"text": "vậy giờ mình cần làm gì tiếp?"}).json()
        assert r_after["reply"] is not None and r_after["intent"] == "chitchat"
    finally:
        cleanup(supabase, cid)


# ---------- after-hours escalation ----------

def test_after_hours_ticket(hitl_app, monkeypatch):
    client, llm, supabase = hitl_app
    monkeypatch.setattr(escalate_mod, "is_business_hours", lambda *a: False)
    cid = client.post("/chat/start", json={"phone": "+84901000003"}).json()["conversation_id"]
    try:
        llm.next_intent = "complaint"
        client.post(f"/chat/{cid}/message", json={"text": "xe vẫn kêu sau khi sửa"})  # turn 1
        r2 = client.post(f"/chat/{cid}/message", json={"text": "không ổn, gặp người đi"}).json()
        assert "ngoài giờ làm việc" in r2["reply"]
        assert "vào ngay" not in r2["reply"]
        tickets = supabase.table("tickets").select("type").eq("conversation_id", cid).execute().data
        assert tickets[0]["type"] == "after_hours"
    finally:
        cleanup(supabase, cid)


# ---------- reveal contact (audited PII) ----------

def test_reveal_contact_and_audit(hitl_app):
    client, llm, supabase = hitl_app
    cid = client.post("/chat/start", json={"phone": "+84901000003"}).json()["conversation_id"]
    rescue = (
        supabase.table("tickets").insert({
            "type": "rescue", "priority": "urgent", "status": "open",
            "conversation_id": cid,
            "payload": {"location": "đại lộ X", "callback_placeholder": "[PHONE_KH]"},
        }).execute().data[0]
    )
    try:
        # wrong token → 401
        assert client.post(f"/staff/tickets/{rescue['id']}/reveal_contact",
                           headers={"Authorization": "Bearer nope"}).status_code == 401
        # correct → real number
        body = client.post(f"/staff/tickets/{rescue['id']}/reveal_contact", headers=auth()).json()
        assert body["placeholder"] == "[PHONE_KH]"
        assert body["value"] == "+84901000003"
        # audit trace exists and carries NO raw number
        traces = (
            supabase.table("trace_events").select("payload")
            .eq("conversation_id", cid).execute().data
        )
        reveal = [t for t in traces if (t["payload"] or {}).get("step") == "pii_reveal"]
        assert len(reveal) == 1
        assert not RAW_PHONE_RE.search(json.dumps(reveal[0]["payload"], ensure_ascii=False))
    finally:
        cleanup(supabase, cid)


def test_reveal_expired_session_410(hitl_app):
    client, llm, supabase = hitl_app
    cid = client.post("/chat/start", json={"phone": "+84901000003"}).json()["conversation_id"]
    rescue = (
        supabase.table("tickets").insert({
            "type": "rescue", "priority": "urgent", "status": "open",
            "conversation_id": cid, "payload": {"callback_placeholder": "[PHONE_KH]"},
        }).execute().data[0]
    )
    try:
        chat_mod._pii_sessions.clear()  # simulate TTL expiry
        r = client.post(f"/staff/tickets/{rescue['id']}/reveal_contact", headers=auth())
        assert r.status_code == 410
    finally:
        cleanup(supabase, cid)


def test_staff_message_requires_human_mode(hitl_app):
    client, llm, supabase = hitl_app
    cid = client.post("/chat/start", json={"phone": "+84901000003"}).json()["conversation_id"]
    try:
        # conversation is in agent mode → staff message 409
        r = client.post(f"/staff/conversations/{cid}/message", headers=auth(), json={"text": "hi"})
        assert r.status_code == 409
    finally:
        cleanup(supabase, cid)

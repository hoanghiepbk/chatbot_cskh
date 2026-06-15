"""TIP-008b persistence + SSE + close tests (DB-backed, skip if no Supabase)."""

import json
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.graph.escalate as escalate_mod
from app.api.chat import router as chat_router
from app.api.staff import router as staff_router
from app.graph.core import GraphDeps, build_graph
from app.llm import LLMResult
from app.session import SessionStore
from app.tools import build_tools
from app.trace import log_trace
from tests.conftest import requires_db

pytestmark = requires_db

# local Supabase demo anon key (well-known, safe in tests); env override allowed
ANON_KEY = os.environ.get(
    "SUPABASE_ANON_KEY",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZS1kZW1vIiwicm9sZSI6ImFub24i"
    "LCJleHAiOjE5ODM4MTI5OTZ9.CRXP1A7WOeoJeXxjNni43kdQwgnWNReilDMblYTn_I0",
)
POLICY = {"escalate_confidence_below": 0.7, "injection_threshold": 0.5, "refund_cap_vnd": 2_000_000}


class SmartLLM:
    def __init__(self):
        self.next_intent = "faq"

    async def complete(self, model, system, messages, max_tokens, json_mode=False):
        if "phân loại intent" in system:
            text = json.dumps({"intent": self.next_intent, "confidence": 0.95})
        elif "kiểm duyệt đầu ra" in system:
            text = ('{"promises_outside_policy": false, "unsafe_advice": false, '
                    '"reveals_internal": false, "off_domain": false}')
        elif "kiểm tra groundedness" in system or "groundedness" in system:
            text = '{"supported": true}'
        elif "cập nhật hồ sơ khách" in system:  # close facts
            text = json.dumps({"facts": {"last_issue": "xe kêu"}, "last_km": 21000,
                               "last_summary": "Khách hỏi sau bảo dưỡng."})
        else:  # chitchat / faq answer / fallback
            text = "Dạ, XeCare luôn sẵn sàng hỗ trợ anh/chị ạ!"
        return LLMResult(text=text, input_tokens=10, output_tokens=5, cost_usd=0.0, latency_ms=1)


class CountingStore(SessionStore):
    def __init__(self, supabase):
        super().__init__(supabase)
        self.loads = 0
        self.saves = 0

    async def load(self, conversation_id):
        self.loads += 1
        return await super().load(conversation_id)

    async def save(self, conversation_id, session):
        self.saves += 1
        return await super().save(conversation_id, session)


@pytest.fixture
def app_client(supabase, monkeypatch):
    monkeypatch.setenv("STAFF_API_TOKEN", "t")
    monkeypatch.setattr(escalate_mod, "is_business_hours", lambda *a: True)
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
    app.state.session_store = CountingStore(supabase)
    app.state.chat_graph = build_graph(deps)
    app.include_router(chat_router)
    app.include_router(staff_router)
    yield TestClient(app), llm, supabase


def cleanup(supabase, cid):
    supabase.table("trace_events").delete().eq("conversation_id", cid).execute()
    supabase.table("tickets").delete().eq("conversation_id", cid).execute()
    supabase.table("messages").delete().eq("conversation_id", cid).execute()
    supabase.table("conversations").delete().eq("id", cid).execute()


# ---------- round-trip across a simulated restart ----------

@pytest.mark.anyio
async def test_session_round_trip_restart(supabase):
    conv = (
        supabase.table("conversations")
        .insert({"mode": "agent", "channel": "widget"})
        .execute()
    ).data[0]
    cid = conv["id"]
    try:
        store1 = SessionStore(supabase)
        s = await store1.load(cid)
        masked = s.pii.mask("gọi mình số 0901234567 nhé")
        assert "[PHONE_1]" in masked
        s.action = {"slots": {"vehicle_type": "motorbike"}, "pending_action": {"type": "book_slot"}}
        await store1.save(cid, s)

        # brand-new store (cache empty) == restart
        store2 = SessionStore(supabase)
        s2 = await store2.load(cid)
        assert s2.pii.unmask("[PHONE_1]") == "0901234567"  # PII map survived
        assert s2.action["slots"] == {"vehicle_type": "motorbike"}
        assert s2.action["pending_action"] == {"type": "book_slot"}
    finally:
        cleanup(supabase, cid)


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ---------- anon cannot read conversations.session ----------

def test_anon_cannot_read_session(supabase):
    from supabase import create_client

    conv = supabase.table("conversations").insert({"mode": "agent"}).execute().data[0]
    cid = conv["id"]
    try:
        anon = create_client(os.environ["SUPABASE_URL"], ANON_KEY)
        with pytest.raises(Exception):  # permission denied for column session
            anon.table("conversations").select("session").eq("id", cid).execute()
        with pytest.raises(Exception):  # select * also includes session → denied
            anon.table("conversations").select("*").eq("id", cid).execute()
    finally:
        cleanup(supabase, cid)


# ---------- one load + one save per /message ----------

def test_one_load_one_save_per_message(app_client):
    client, llm, supabase = app_client
    cid = client.post("/chat/start", json={"phone": "+84901000003"}).json()["conversation_id"]
    try:
        client.app.state.session_store.loads = 0
        client.app.state.session_store.saves = 0
        llm.next_intent = "chitchat"
        client.post(f"/chat/{cid}/message", json={"text": "chào shop"})
        assert client.app.state.session_store.loads == 1
        assert client.app.state.session_store.saves == 1
    finally:
        cleanup(supabase, cid)


# ---------- SSE: status + final (guardrailed, unmasked) + done ----------

def _parse_sse(text):
    events = []
    for block in text.strip().split("\n\n"):
        ev, data = None, None
        for line in block.splitlines():
            if line.startswith("event: "):
                ev = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        if ev:
            events.append((ev, data))
    return events


def test_sse_faq_status_final_done(app_client):
    client, llm, supabase = app_client
    cid = client.post("/chat/start", json={"phone": "+84901000003"}).json()["conversation_id"]
    try:
        llm.next_intent = "chitchat"
        r = client.post(f"/chat/{cid}/message_stream", json={"text": "chào shop nhé"})
        assert r.headers["content-type"].startswith("text/event-stream")
        events = _parse_sse(r.text)
        kinds = [e for e, _ in events]
        assert "status" in kinds
        assert kinds.count("final") == 1
        assert kinds[-1] == "done"
        final = next(d for e, d in events if e == "final")
        assert "[PHONE_" not in (final["reply"] or "")  # unmasked + clean
    finally:
        cleanup(supabase, cid)


def test_sse_guardrail_blocks_refund_no_raw_token(app_client):
    client, llm, supabase = app_client
    cid = client.post("/chat/start", json={"phone": "+84901000003"}).json()["conversation_id"]
    try:
        # force a chitchat reply that trips the refund hard rule
        async def bad_complete(model, system, messages, max_tokens, json_mode=False):
            if "phân loại intent" in system:
                return LLMResult(text='{"intent":"chitchat","confidence":0.95}',
                                 input_tokens=1, output_tokens=1, cost_usd=0.0, latency_ms=1)
            if "kiểm duyệt đầu ra" in system:
                return LLMResult(text='{"promises_outside_policy":true,"unsafe_advice":false,'
                                 '"reveals_internal":false,"off_domain":false}',
                                 input_tokens=1, output_tokens=1, cost_usd=0.0, latency_ms=1)
            # both the original reply and the rewrite keep the over-cap refund
            return LLMResult(text="Dạ shop hoàn anh 5.000.000đ luôn ạ.",
                             input_tokens=1, output_tokens=1, cost_usd=0.0, latency_ms=1)

        client.app.state.llm.complete = bad_complete
        r = client.post(f"/chat/{cid}/message_stream", json={"text": "đòi hoàn tiền"})
        events = _parse_sse(r.text)
        final = next(d for e, d in events if e == "final")
        # only the guardrailed final is emitted; the raw over-cap amount never leaks
        assert "5.000.000" not in final["reply"]
    finally:
        cleanup(supabase, cid)


# ---------- close: facts + last_km, pii wiped, idempotent ----------

def test_close_extracts_facts_and_wipes_pii(app_client):
    client, llm, supabase = app_client
    start = client.post("/chat/start", json={"phone": "+84901000004"}).json()
    cid = start["conversation_id"]
    try:
        llm.next_intent = "chitchat"
        client.post(f"/chat/{cid}/message", json={"text": "xe em vừa đi 21000 km rồi"})

        before = (
            supabase.table("customer_profiles").select("vehicles, facts")
            .eq("phone_hash", _hash("+84901000004")).execute()
        ).data[0]

        r = client.post(f"/chat/{cid}/close", json={"resolution": "completed"}).json()
        assert r["ok"] is True
        assert r["last_km"] == 21000

        after = (
            supabase.table("customer_profiles").select("vehicles, facts")
            .eq("phone_hash", _hash("+84901000004")).execute()
        ).data[0]
        assert after["vehicles"][0]["last_km"] == 21000  # odometer updated
        assert "last_summary" in after["facts"]

        # conversation closed; pii map wiped from the persisted session
        session_col = (
            supabase.table("conversations").select("session").eq("id", cid).execute()
        ).data[0]["session"]
        assert session_col["pii"]["value_by_placeholder"] == {}

        # idempotent: second close is a no-op 200
        r2 = client.post(f"/chat/{cid}/close", json={}).json()
        assert r2.get("already_closed") is True

        # restore the seed profile so other tests/demo stay consistent
        supabase.table("customer_profiles").update(
            {"vehicles": before["vehicles"], "facts": before["facts"]}
        ).eq("phone_hash", _hash("+84901000004")).execute()
    finally:
        cleanup(supabase, cid)


def _hash(phone):
    import hashlib

    salt = os.environ["PHONE_HASH_SALT"]
    return hashlib.sha256((salt + phone).encode()).hexdigest()

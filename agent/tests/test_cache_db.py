"""TIP-015 — semantic cache + gap integration (DB-backed, skip if no Supabase).

Proves end-to-end via the chat graph: a faq turn caches, an identical follow-up
HITS the cache (trace shows cache_hit, no 2nd retrieval / Sonnet call), an
unanswerable turn records a knowledge gap, and the cache key rejects entity /
kb_version mismatches. Uses a deterministic FAKE embedding (monkeypatched) + a
FakeLLM, so no bge-m3 model or network is needed.
"""

import zlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.chat import router as chat_router
from app.api.staff import router as staff_router
from app.cache.semantic import SemanticCache, _normalize
from app.graph import retrieval
from app.graph.core import GraphDeps, build_graph
from app.insights.gap import GapDetector
from app.llm import LLMResult
from app.session import SessionStore
from app.tools import build_tools
from app.trace import log_trace
from tests.conftest import requires_db

pytestmark = requires_db

STAFF_TOKEN = "test-staff-token"
AUTH = {"Authorization": f"Bearer {STAFF_TOKEN}"}
POLICY = {"escalate_confidence_below": 0.7, "injection_threshold": 0.5, "refund_cap_vnd": 2_000_000}
FAQ_REPLY = "Phí bảo dưỡng xe máy tham khảo khoảng 200.000đ ạ."


def fake_embed(text: str) -> list[float]:
    """Deterministic bag-of-words vector: identical text → identical vector."""
    vec = [0.0] * 1024
    for tok in _normalize(text).split():
        vec[zlib.crc32(tok.encode()) % 1024] += 1.0
    return vec


class FaqLLM:
    def __init__(self):
        self.supported = True

    async def complete(self, model, system, messages, max_tokens, json_mode=False):
        if "phân loại intent" in system:
            text = '{"intent": "faq", "confidence": 0.95}'
        elif "groundedness" in system:
            text = '{"supported": true}' if self.supported else '{"supported": false}'
        elif "kiểm duyệt đầu ra" in system:  # output rubric
            text = ('{"promises_outside_policy": false, "unsafe_advice": false, '
                    '"reveals_internal": false, "off_domain": false}')
        else:  # faq_answer (Sonnet)
            text = FAQ_REPLY
        return LLMResult(text=text, input_tokens=10, output_tokens=5, cost_usd=0.002, latency_ms=5)


@pytest.fixture
def cache_app(supabase, monkeypatch):
    monkeypatch.setenv("PHONE_HASH_SALT", "test-salt")
    monkeypatch.setenv("STAFF_API_TOKEN", STAFF_TOKEN)
    monkeypatch.setattr("app.graph.retrieval.embed_dense", fake_embed)
    llm = FaqLLM()

    async def fake_search(query, top_k=5):
        return [
            retrieval.Chunk(
                id="c1", doc_id="kb-01",
                content="Phí bảo dưỡng xe máy khoảng 200k.", heading="Giá bảo dưỡng", score=0.9,
            )
        ]

    deps = GraphDeps(
        llm=llm, system_prompt="SYS", prompt_version=2, policy=POLICY, policy_version=2,
        search=fake_search, trace=log_trace, tools=build_tools(supabase), supabase=supabase,
        cache=SemanticCache(supabase), gap=GapDetector(supabase),
    )
    app = FastAPI()
    app.state.supabase = supabase
    app.state.llm = llm
    app.state.policy = POLICY
    app.state.prompt_version = 2
    app.state.policy_version = 2
    app.state.tools = build_tools(supabase)
    app.state.session_store = SessionStore(supabase)
    app.state.chat_graph = build_graph(deps)
    app.include_router(chat_router)
    app.include_router(staff_router)
    yield TestClient(app), llm, supabase


def _cleanup(supabase, cid):
    supabase.table("trace_events").delete().eq("conversation_id", cid).execute()
    supabase.table("messages").delete().eq("conversation_id", cid).execute()
    supabase.table("conversations").delete().eq("id", cid).execute()


def test_faq_cache_hit_skips_retrieval_and_sonnet(cache_app):
    client, _, supabase = cache_app
    cid = client.post("/chat/start", json={"phone": "+84900000111"}).json()["conversation_id"]
    q = {"text": "phí bảo dưỡng xe máy bao nhiêu"}
    try:
        r1 = client.post(f"/chat/{cid}/message", json=q).json()
        assert r1["intent"] == "faq" and r1["reply"] == FAQ_REPLY

        r2 = client.post(f"/chat/{cid}/message", json=q).json()  # identical → cache hit
        assert r2["reply"] == FAQ_REPLY

        traces = (
            supabase.table("trace_events").select("step_type, payload")
            .eq("conversation_id", cid).order("created_at").execute().data
        )
        steps = [t["step_type"] for t in traces]
        assert steps.count("cache_hit") == 1            # turn 2 hit
        assert steps.count("retrieval") == 1            # only turn 1 retrieved
        faq_answers = [
            t for t in traces
            if t["step_type"] == "llm_call" and (t["payload"] or {}).get("purpose") == "faq_answer"
        ]
        assert len(faq_answers) == 1                    # Sonnet called once
        assert steps.index("cache_hit") > steps.index("retrieval")
    finally:
        supabase.table("faq_cache").delete().eq("reply", FAQ_REPLY).execute()
        _cleanup(supabase, cid)


def test_gap_recorded_and_clustered(cache_app):
    client, llm, supabase = cache_app
    llm.supported = False  # groundedness fails → knowledge gap
    cid = client.post("/chat/start", json={"phone": "+84900000222"}).json()["conversation_id"]
    try:
        client.post(f"/chat/{cid}/message", json={"text": "XeCare có dịch vụ rửa xe không"})
        gaps = (
            supabase.table("kb_gap_events").select("query, reason")
            .order("created_at", desc=True).limit(10).execute().data
        )
        assert any(g["reason"] == "groundedness_false" and "rửa xe" in g["query"] for g in gaps)

        clustered = client.get("/staff/knowledge-gaps", headers=AUTH).json()
        assert clustered["total_events"] >= 1
        joined = " ".join(
            c["representative_query"] + " " + " ".join(c["sample_queries"]) for c in clustered["clusters"]
        )
        assert "rửa xe" in joined
    finally:
        supabase.table("kb_gap_events").delete().like("query", "%rửa xe%").execute()
        _cleanup(supabase, cid)


def test_cache_store_lookup_entity_and_kb_safety(supabase):
    cache = SemanticCache(supabase)
    kb = cache.current_kb_version()
    vec = [((i % 7) * 0.13 + 0.01) for i in range(1024)]
    reply = "[TIP-015 test] phí ship Hà Nội tham khảo 20k."
    cache.store(vec, ["loc:ha_noi", "svc:ship"], kb, reply, [{"doc_id": "d", "heading": "Ship"}])
    try:
        hit = cache.lookup(vec, ["loc:ha_noi", "svc:ship"], kb)
        assert hit is not None and hit["reply"] == reply and hit["similarity"] >= 0.93
        # entity mismatch (Hà Nội cached, Đà Nẵng queried) → miss despite cosine ~1
        assert cache.lookup(vec, ["loc:da_nang", "svc:ship"], kb) is None
        # kb_version invalidation → miss
        assert cache.lookup(vec, ["loc:ha_noi", "svc:ship"], kb + 1) is None
    finally:
        supabase.table("faq_cache").delete().eq("reply", reply).execute()


def test_metrics_cache_hit_rate(cache_app):
    client, _, supabase = cache_app
    cust = (
        supabase.table("customer_profiles")
        .insert({"phone_hash": "tip015-metric-hash", "display_name": "M", "vehicles": []})
        .execute().data[0]
    )
    conv = (
        supabase.table("conversations")
        .insert({"customer_id": cust["id"], "mode": "agent"}).execute().data[0]
    )
    cid = conv["id"]
    rows = []
    for _ in range(2):
        rows.append({"conversation_id": cid, "step_type": "retrieval", "payload": {"chunk_ids": ["a"]}})
        rows.append({"conversation_id": cid, "step_type": "cache_hit", "payload": {"similarity": 0.99}})
        rows.append({"conversation_id": cid, "step_type": "llm_call",
                     "payload": {"purpose": "faq_answer", "model": "sonnet"}, "cost_usd": 0.002})
    supabase.table("trace_events").insert(rows).execute()
    try:
        m = client.get("/staff/metrics", headers=AUTH).json()
        assert m["cache_hit_rate"] is not None and 0.0 < m["cache_hit_rate"] <= 1.0
        assert m["faq_turns"] >= 4
        assert m["cache_savings_usd"] >= 0.0
    finally:
        supabase.table("trace_events").delete().eq("conversation_id", cid).execute()
        supabase.table("conversations").delete().eq("id", cid).execute()
        supabase.table("customer_profiles").delete().eq("id", cust["id"]).execute()

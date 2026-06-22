"""TIP-014 — staff console read endpoints (DB-backed, skip if no Supabase).

Seeds one conversation with a realistic trace (router → retrieval → llm_call →
guardrail_out → escalation), messages (raw content carries a phone — must NEVER
leak), and an eval_run, then asserts the 4 read endpoints return correct,
MASKED aggregates. Each test cleans up its own rows.
"""

import json
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.staff import router as staff_router
from tests.conftest import requires_db

pytestmark = requires_db

STAFF_TOKEN = "test-staff-token"
RAW_PHONE_RE = re.compile(r"(?<!\d)(?:\+84|0)(?:[\s.\-]?\d){9}(?!\d)")
DAY = "2026-06-20"


def auth():
    return {"Authorization": f"Bearer {STAFF_TOKEN}"}


@pytest.fixture
def console(supabase, monkeypatch):
    monkeypatch.setenv("STAFF_API_TOKEN", STAFF_TOKEN)
    app = FastAPI()
    app.state.supabase = supabase
    app.include_router(staff_router)
    yield TestClient(app), supabase


def _seed(supabase):
    cust = (
        supabase.table("customer_profiles")
        .insert({"phone_hash": "tip014-hash", "display_name": "Chị Lan", "vehicles": []})
        .execute()
        .data[0]
    )
    conv = (
        supabase.table("conversations")
        .insert({"customer_id": cust["id"], "mode": "agent", "channel": "widget"})
        .execute()
        .data[0]
    )
    cid = conv["id"]
    # raw content carries a real phone — the read endpoints must never surface it
    supabase.table("messages").insert(
        [
            {"conversation_id": cid, "sender": "customer",
             "content": "gọi tôi 0912345678", "content_masked": "gọi tôi [PHONE_KH]"},
            {"conversation_id": cid, "sender": "agent",
             "content": "Dạ vâng ạ", "content_masked": "Dạ vâng ạ"},
        ]
    ).execute()
    supabase.table("trace_events").insert(
        [
            {"conversation_id": cid, "step_type": "router",
             "payload": {"intent": "complaint", "confidence": 0.92, "engine": "haiku"},
             "latency_ms": 800, "cost_usd": 0.0003, "created_at": f"{DAY}T10:00:01+00:00"},
            {"conversation_id": cid, "step_type": "retrieval",
             "payload": {"chunk_ids": ["a", "b"], "scores": [0.81, 0.74]},
             "latency_ms": 40, "created_at": f"{DAY}T10:00:02+00:00"},
            {"conversation_id": cid, "step_type": "llm_call",
             "payload": {"purpose": "faq_answer", "model": "claude-haiku-4-5",
                         "input_tokens": 100, "output_tokens": 50},
             "latency_ms": 1200, "cost_usd": 0.0005, "created_at": f"{DAY}T10:00:03+00:00"},
            {"conversation_id": cid, "step_type": "guardrail_out",
             "payload": {"verdict": "pass", "reasons": [], "rules_hit": []},
             "created_at": f"{DAY}T10:00:04+00:00"},
            {"conversation_id": cid, "step_type": "escalation",
             "payload": {"reason": "complaint", "ticket_id": None},
             "created_at": f"{DAY}T10:00:05+00:00"},
        ]
    ).execute()
    run = (
        supabase.table("eval_runs")
        .insert({"git_sha": "tip014sha", "prompt_version": 2, "suite": "adversarial_critical",
                 "total": 30, "passed": 30,
                 "metrics": {"by_group": {"injection": {"passed": 10, "total": 10}}}})
        .execute()
        .data[0]
    )
    return cid, cust["id"], run["id"]


def _cleanup(supabase, cid, cust_id, run_id):
    supabase.table("trace_events").delete().eq("conversation_id", cid).execute()
    supabase.table("messages").delete().eq("conversation_id", cid).execute()
    supabase.table("conversations").delete().eq("id", cid).execute()
    supabase.table("customer_profiles").delete().eq("id", cust_id).execute()
    supabase.table("eval_runs").delete().eq("id", run_id).execute()


def test_endpoints_require_bearer(console):
    client, _ = console
    for url in ("/staff/conversations", "/staff/metrics", "/staff/eval-runs"):
        assert client.get(url).status_code == 401
        assert client.get(url, headers={"Authorization": "Bearer nope"}).status_code == 401


def test_conversations_list_last_intent_and_no_pii(console):
    client, supabase = console
    cid, cust_id, run_id = _seed(supabase)
    try:
        body = client.get("/staff/conversations?limit=200", headers=auth()).json()
        row = next(c for c in body["conversations"] if c["id"] == cid)
        assert row["display_name"] == "Chị Lan"
        assert row["mode"] == "agent"
        assert row["message_count"] == 2
        assert row["last_intent"] == "complaint"
        assert row["escalated"] is True
        # masking: the raw phone in messages.content must never appear
        assert not RAW_PHONE_RE.search(json.dumps(body, ensure_ascii=False))
    finally:
        _cleanup(supabase, cid, cust_id, run_id)


def test_conversations_escalated_filter(console):
    client, supabase = console
    cid, cust_id, run_id = _seed(supabase)
    try:
        only_esc = client.get("/staff/conversations?escalated=true&limit=200", headers=auth()).json()
        assert any(c["id"] == cid for c in only_esc["conversations"])
        not_esc = client.get("/staff/conversations?escalated=false&limit=200", headers=auth()).json()
        assert all(c["id"] != cid for c in not_esc["conversations"])
    finally:
        _cleanup(supabase, cid, cust_id, run_id)


def test_conversation_trace_timeline_and_summary(console):
    client, supabase = console
    cid, cust_id, run_id = _seed(supabase)
    try:
        body = client.get(f"/staff/conversations/{cid}/trace", headers=auth()).json()
        steps = [e["step_type"] for e in body["events"]]
        assert steps == ["router", "retrieval", "llm_call", "guardrail_out", "escalation"]
        summary = body["summary"]
        assert summary["event_count"] == 5
        assert summary["llm_calls"] == 1
        assert summary["escalated"] is True
        assert abs(summary["total_cost_usd"] - 0.0008) < 1e-9
        assert summary["total_latency_ms"] == 2040
        assert not RAW_PHONE_RE.search(json.dumps(body, ensure_ascii=False))
    finally:
        _cleanup(supabase, cid, cust_id, run_id)


def test_metrics_shape_and_distributions(console):
    client, supabase = console
    cid, cust_id, run_id = _seed(supabase)
    try:
        m = client.get("/staff/metrics", headers=auth()).json()
        assert 0.0 <= m["resolution_rate"] <= 1.0
        assert 0.0 <= m["escalation_rate"] <= 1.0
        assert m["avg_cost_usd"] >= 0.0
        assert m["latency_ms"]["p95"] >= m["latency_ms"]["p50"] >= 0
        assert m["cache_hit_rate"] is None
        assert any(d["intent"] == "complaint" for d in m["intent_distribution"])
        assert any(r["reason"] == "complaint" for r in m["escalation_reasons"])
        assert isinstance(m["cost_by_day"], list)
        assert not RAW_PHONE_RE.search(json.dumps(m, ensure_ascii=False))
    finally:
        _cleanup(supabase, cid, cust_id, run_id)


def test_eval_runs_returns_recent_with_metrics(console):
    client, supabase = console
    cid, cust_id, run_id = _seed(supabase)
    try:
        body = client.get("/staff/eval-runs?limit=100", headers=auth()).json()
        run = next(r for r in body["eval_runs"] if r["id"] == run_id)
        assert run["git_sha"] == "tip014sha"
        assert run["suite"] == "adversarial_critical"
        assert run["passed"] == 30 and run["total"] == 30
        assert run["metrics"]["by_group"]["injection"]["passed"] == 10
    finally:
        _cleanup(supabase, cid, cust_id, run_id)

"""TIP-013 — Prompt/Policy registry API + hot-reload (DB-backed, skip if no Supabase).

Proves: create a new (inactive) version → activate → app.state hot-reloads (version +
content change, chat graph rebuilt) for the NEXT turn without a restart; Bearer auth;
policy schema validation. Each test restores the seeded active version (system_main v2 /
core_policy v2) so it is idempotent and leaves the agent's active config intact.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.registry import router as registry_router
from app.llm import LLMResult
from app.main import apply_active_registry
from app.tools import build_tools
from tests.conftest import requires_db

pytestmark = requires_db

AUTH = {"Authorization": "Bearer t"}


class _StubLLM:
    async def complete(self, *a, **k):  # never called — graph is built, not invoked here
        return LLMResult(text="", input_tokens=0, output_tokens=0, cost_usd=0.0, latency_ms=0)


def _build_app(supabase):
    app = FastAPI()
    app.state.supabase = supabase
    app.state.llm = _StubLLM()
    app.state.tools = build_tools(supabase)
    app.state.phobert = None
    apply_active_registry(app)  # load the seeded active prompt/policy + compile graph
    app.include_router(registry_router)
    return app


def _restore_active(supabase, table: str, name: str, version: int, drop_version: int):
    supabase.table(table).update({"active": False}).eq("name", name).execute()
    supabase.table(table).update({"active": True}).eq("name", name).eq(
        "version", version
    ).execute()
    supabase.table(table).delete().eq("name", name).eq("version", drop_version).execute()


def test_prompt_create_activate_hot_reload(supabase, monkeypatch):
    monkeypatch.setenv("STAFF_API_TOKEN", "t")
    client = TestClient(_build_app(supabase))
    base_version = client.app.state.prompt_version
    base_graph = client.app.state.chat_graph
    new_content = "[TIP-013 TEST] prompt tạm — sẽ xoá ngay sau test."

    created = client.post("/registry/prompts", json={"content": new_content}, headers=AUTH)
    assert created.status_code == 200
    v = created.json()["version"]
    assert v > base_version and created.json()["active"] is False
    try:
        act = client.post(f"/registry/prompts/{v}/activate", headers=AUTH)
        assert act.status_code == 200
        # hot-reload: app.state + compiled graph reflect the new version immediately
        assert client.app.state.prompt_version == v
        assert client.app.state.system_prompt == new_content
        assert client.app.state.chat_graph is not base_graph
    finally:
        _restore_active(supabase, "prompt_registry", "system_main", base_version, v)


def test_policy_create_activate_hot_reload_keeps_omitted_cap(supabase, monkeypatch):
    monkeypatch.setenv("STAFF_API_TOKEN", "t")
    client = TestClient(_build_app(supabase))
    base_version = client.app.state.policy_version
    # deliberately OMIT refund_cap_vnd — the hard rule keeps the 2M code default
    rules = {"escalate_confidence_below": 0.7, "injection_threshold": 0.5}

    created = client.post("/registry/policies", json={"rules": rules}, headers=AUTH)
    assert created.status_code == 200
    v = created.json()["version"]
    try:
        act = client.post(f"/registry/policies/{v}/activate", headers=AUTH)
        assert act.status_code == 200
        assert client.app.state.policy_version == v
        assert "refund_cap_vnd" not in client.app.state.policy
    finally:
        _restore_active(supabase, "policy_registry", "core_policy", base_version, v)


def test_registry_requires_bearer(supabase, monkeypatch):
    monkeypatch.setenv("STAFF_API_TOKEN", "t")
    client = TestClient(_build_app(supabase))
    assert client.post("/registry/prompts", json={"content": "x"}).status_code == 401
    assert (
        client.post(
            "/registry/prompts", json={"content": "x"}, headers={"Authorization": "Bearer nope"}
        ).status_code
        == 401
    )


def test_policy_validation_rejects_bad_types(supabase, monkeypatch):
    monkeypatch.setenv("STAFF_API_TOKEN", "t")
    client = TestClient(_build_app(supabase))
    bad_number = client.post(
        "/registry/policies", json={"rules": {"refund_cap_vnd": "nhiều"}}, headers=AUTH
    )
    assert bad_number.status_code == 400
    bad_list = client.post(
        "/registry/policies", json={"rules": {"forbidden_topics": "không-phải-list"}}, headers=AUTH
    )
    assert bad_list.status_code == 400

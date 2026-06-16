"""XeCare agent service — FastAPI entrypoint."""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.chat import router as chat_router
from app.config import load_dotenv_if_present
from app.graph import retrieval

load_dotenv_if_present()


def load_active_registry(supabase) -> dict:
    """[TIP-013] Read the active prompt + policy rows. Single source of truth used
    both at startup (lifespan) and on a registry activate (hot-reload)."""
    policy_row = (
        supabase.table("policy_registry").select("*").eq("active", True).execute().data[0]
    )
    prompt_row = (
        supabase.table("prompt_registry")
        .select("*")
        .eq("name", "system_main")
        .eq("active", True)
        .execute()
        .data[0]
    )
    return {"policy": policy_row, "prompt": prompt_row}


def _build_chat_graph(state, prompt_row: dict, policy_row: dict):
    """Rebuild the compiled graph from the current app.state engine pieces + the
    given prompt/policy. Kept identical to the lifespan wiring so a hot-reload
    produces the same graph a restart would."""
    from app.graph.core import GraphDeps, build_graph
    from app.trace import log_trace

    deps = GraphDeps(
        llm=state.llm,
        system_prompt=prompt_row["content"],
        prompt_version=prompt_row["version"],
        policy=policy_row["rules"],
        policy_version=policy_row["version"],
        search=retrieval.search_kb,
        trace=log_trace,
        tools=state.tools,
        supabase=state.supabase,  # [TIP-008] handoff reads messages/trace
        phobert=state.phobert,  # [TIP-012a] None unless USE_PHOBERT=true
    )
    return build_graph(deps)


def apply_active_registry(app) -> dict:
    """[TIP-013] Load the active prompt+policy into app.state AND rebuild the chat
    graph. The graph closes over GraphDeps (prompt/policy frozen at build time, and
    routing thresholds computed once from policy) — so a swap is REQUIRED; mutating
    app.state alone would leave the running graph on the old version. Hot-reload, no
    restart. Requires app.state.{supabase,llm,tools,phobert} already set."""
    reg = load_active_registry(app.state.supabase)
    app.state.policy = reg["policy"]["rules"]
    app.state.policy_version = reg["policy"]["version"]
    app.state.system_prompt = reg["prompt"]["content"]
    app.state.prompt_version = reg["prompt"]["version"]
    app.state.chat_graph = _build_chat_graph(app.state, reg["prompt"], reg["policy"])
    return reg


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.models_loaded = False
    # [TIP-012a] eager-load PhoBERT ONNX tại đây (tuần tự)
    # [TIP-003] eager-load bge-m3 (FlagEmbedding — ONNX deferred to TIP-016, approved deviation)
    from FlagEmbedding import BGEM3FlagModel
    from supabase import create_client

    # BGE_M3_MODEL may point to a local dir (skips HF snapshot of unused files, e.g. ONNX)
    model = BGEM3FlagModel(os.environ.get("BGE_M3_MODEL", "BAAI/bge-m3"), use_fp16=False)
    client = create_client(
        os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    )
    retrieval.configure(model, client)
    app.state.embed_model = model
    app.state.supabase = client
    # Reflects the embedding model only for now — TIP-012a extends this to PhoBERT.
    app.state.models_loaded = True

    # [TIP-005/008] engine pieces — built BEFORE the registry so apply_active_registry
    # (shared with the hot-reload path) can rebuild the graph from app.state.
    from app.llm import AnthropicClient
    from app.session import SessionStore
    from app.tools import build_tools

    app.state.tools = build_tools(client)
    app.state.llm = AnthropicClient()  # [TIP-008] staff API reuses it for resolve summaries
    app.state.session_store = SessionStore(client)  # [TIP-008b] persistent sessions

    # [TIP-012a] eager-load PhoBERT ONNX guard ONLY when enabled (default off → None,
    # so the Haiku router + regex injection path is unchanged). Heavy deps imported here.
    import sys as _sys
    from pathlib import Path as _Path

    from app.config import use_phobert

    phobert_guard = None
    if use_phobert():
        _sys.path.insert(0, str(_Path(__file__).resolve().parents[1] / "ml" / "phobert"))
        from infer import PhoBERTGuard

        phobert_guard = PhoBERTGuard()
    app.state.phobert = phobert_guard

    # [TIP-005/013] active policy + system prompt → app.state + compiled graph.
    apply_active_registry(app)
    yield


app = FastAPI(title="XeCare Agent Service", lifespan=lifespan)
app.include_router(chat_router)

from app.api.registry import router as registry_router  # noqa: E402
from app.api.staff import router as staff_router  # noqa: E402

app.include_router(staff_router)
app.include_router(registry_router)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "models_loaded": getattr(app.state, "models_loaded", False),
    }


if os.environ.get("DEBUG_ENDPOINTS") == "1":

    @app.get("/debug/search")
    async def debug_search(q: str, top_k: int = 5):
        chunks = await retrieval.search_kb(q, top_k=top_k)
        return [
            {"id": c.id, "doc_id": c.doc_id, "heading": c.heading, "score": c.score}
            for c in chunks
        ]

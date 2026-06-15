"""XeCare agent service — FastAPI entrypoint."""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.chat import router as chat_router
from app.config import load_dotenv_if_present
from app.graph import retrieval

load_dotenv_if_present()


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

    # [TIP-005] active policy + system prompt cached at startup
    from app.graph.core import GraphDeps, build_graph
    from app.llm import AnthropicClient
    from app.tools import build_tools
    from app.trace import log_trace

    policy_row = (
        client.table("policy_registry").select("*").eq("active", True).execute().data[0]
    )
    prompt_row = (
        client.table("prompt_registry")
        .select("*")
        .eq("name", "system_main")
        .eq("active", True)
        .execute()
        .data[0]
    )
    app.state.policy = policy_row["rules"]
    app.state.policy_version = policy_row["version"]
    app.state.system_prompt = prompt_row["content"]
    app.state.prompt_version = prompt_row["version"]

    tools = build_tools(client)
    app.state.tools = tools

    llm = AnthropicClient()
    app.state.llm = llm  # [TIP-008] staff API reuses it for resolve summaries

    from app.session import SessionStore

    app.state.session_store = SessionStore(client)  # [TIP-008b] persistent sessions

    deps = GraphDeps(
        llm=llm,
        system_prompt=prompt_row["content"],
        prompt_version=prompt_row["version"],
        policy=policy_row["rules"],
        policy_version=policy_row["version"],
        search=retrieval.search_kb,
        trace=log_trace,
        tools=tools,
        supabase=client,  # [TIP-008] handoff package reads messages/trace
    )
    app.state.chat_graph = build_graph(deps)
    yield


app = FastAPI(title="XeCare Agent Service", lifespan=lifespan)
app.include_router(chat_router)

from app.api.staff import router as staff_router  # noqa: E402

app.include_router(staff_router)


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

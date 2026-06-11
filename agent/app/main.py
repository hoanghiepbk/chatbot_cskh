"""XeCare agent service — FastAPI entrypoint."""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

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
    yield


app = FastAPI(title="XeCare Agent Service", lifespan=lifespan)


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

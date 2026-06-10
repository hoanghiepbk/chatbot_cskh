"""XeCare agent service — FastAPI entrypoint."""

from contextlib import asynccontextmanager

from fastapi import FastAPI


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.models_loaded = False
    # [TIP-012a] eager-load PhoBERT ONNX tại đây (tuần tự)
    # [TIP-003] eager-load bge-m3 ONNX tại đây (sau PhoBERT)
    yield


app = FastAPI(title="XeCare Agent Service", lifespan=lifespan)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "models_loaded": getattr(app.state, "models_loaded", False),
    }

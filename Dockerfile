# XeCare agent — Railway deploy image (TIP-010.5)
# Build context = repo root (Railway default). Model is NOT baked: bge-m3 is
# downloaded by FlagEmbedding on first boot into a persistent Railway volume
# (HF_HOME=/data/hf). Keeps the image small (~deps only) at the cost of a one-time
# ~2-3 min cold start; the volume persists across restarts so later boots are fast.
FROM python:3.12-slim

# torch/FlagEmbedding need libgomp at runtime
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# uv (pinned binary from the official image)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# 1) deps layer (cached unless pyproject/lock change)
COPY agent/pyproject.toml agent/uv.lock /app/
RUN uv sync --no-dev --no-install-project --frozen

# 2) app code
COPY agent/app /app/app

# model cache + HF home live on the mounted volume (/data)
ENV HF_HOME=/data/hf \
    BGE_M3_MODEL=BAAI/bge-m3 \
    DEBUG_ENDPOINTS=0 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

EXPOSE 8000

# Railway injects $PORT; healthcheck (/health) timeout must cover model cold start.
CMD ["sh", "-c", "uv run --no-dev uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]

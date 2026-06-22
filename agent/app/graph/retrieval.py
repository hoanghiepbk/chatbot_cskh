"""Hybrid search over kb_chunks: dense (pgvector RPC) + sparse lexical, fused with RRF.

Pure retrieval — no LangGraph nodes here (TIP-005). The embedding model and
Supabase client are injected once at startup via configure() (called in lifespan).
"""

from dataclasses import dataclass
from typing import Any

DENSE_CANDIDATES = 20
RRF_K = 60

_model: Any = None
_client: Any = None


def configure(model: Any, client: Any) -> None:
    global _model, _client
    _model = model
    _client = client


@dataclass
class Chunk:
    id: str
    doc_id: str
    content: str
    heading: str
    score: float


def rrf_fuse(rankings: list[list[str]], k: int = RRF_K) -> dict[str, float]:
    """Reciprocal Rank Fusion: score(item) = Σ 1 / (k + rank_i), rank starts at 1."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for idx, item in enumerate(ranking):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + idx + 1)
    return scores


def sparse_score(query_weights: dict[str, float], chunk_weights: dict[str, float]) -> float:
    """Dot product over shared lexical tokens."""
    if not query_weights or not chunk_weights:
        return 0.0
    return sum(w * chunk_weights[t] for t, w in query_weights.items() if t in chunk_weights)


def embed_dense(query: str) -> list[float]:
    """[TIP-015] Dense embedding of a query, reusing the configured bge-m3 model.
    Used by the semantic faq cache + knowledge-gap detection — NO extra LLM call,
    same model the retriever uses (sync encode, mirroring search_kb)."""
    if _model is None:
        raise RuntimeError("retrieval not configured — call configure() in lifespan first")
    encoded = _model.encode(
        [query], return_dense=True, return_sparse=False, return_colbert_vecs=False
    )
    return [float(x) for x in encoded["dense_vecs"][0]]


async def search_kb(query: str, top_k: int = 5) -> list[Chunk]:
    if _model is None or _client is None:
        raise RuntimeError("retrieval not configured — call configure() in lifespan first")

    encoded = _model.encode(
        [query], return_dense=True, return_sparse=True, return_colbert_vecs=False
    )
    query_dense = [float(x) for x in encoded["dense_vecs"][0]]
    query_sparse = {k: float(v) for k, v in encoded["lexical_weights"][0].items()}

    result = _client.rpc(
        "match_kb_chunks", {"query_vec": query_dense, "match_count": DENSE_CANDIDATES}
    ).execute()
    candidates = result.data or []
    if not candidates:
        return []

    dense_ranking = [row["id"] for row in candidates]  # already ordered by similarity
    sparse_ranking = [
        row["id"]
        for row in sorted(
            candidates,
            key=lambda r: sparse_score(query_sparse, r.get("sparse_weights") or {}),
            reverse=True,
        )
    ]

    fused = rrf_fuse([dense_ranking, sparse_ranking])
    by_id = {row["id"]: row for row in candidates}
    top = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)[:top_k]
    return [
        Chunk(
            id=cid,
            doc_id=by_id[cid]["doc_id"],
            content=by_id[cid]["content"],
            heading=(by_id[cid].get("metadata") or {}).get("heading", ""),
            score=score,
        )
        for cid, score in top
    ]

"""TIP-015 knowledge-gap detection.

Records faq turns the RAG pipeline could NOT answer (no chunks / groundedness
false) with the MASKED query + embedding, then clusters recent gaps so the
console can surface "N customers asked about X this week, but the KB has no
answer". Clustering is a simple in-app greedy pass (cosine ≥ 0.85).
"""

from math import sqrt
from typing import Any

CLUSTER_THRESHOLD = 0.85
MAX_SAMPLES = 5


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sqrt(sum(x * x for x in a))
    nb = sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _parse_vec(value: Any) -> list[float] | None:
    """pgvector comes back from PostgREST as a '[...]' string."""
    if isinstance(value, list):
        return [float(x) for x in value]
    if isinstance(value, str):
        try:
            return [float(x) for x in value.strip("[]").split(",") if x.strip()]
        except ValueError:
            return None
    return None


def greedy_cluster(
    events: list[dict], threshold: float = CLUSTER_THRESHOLD, max_samples: int = MAX_SAMPLES
) -> list[dict]:
    """Greedy clustering of gap events (each: {query, embedding(list|None),
    created_at}). Process events most-recent-first so the representative is the
    latest phrasing. Returns clusters sorted by count desc."""
    clusters: list[dict] = []
    for ev in events:
        emb = ev.get("embedding")
        query = ev.get("query") or ""
        placed = False
        if emb:
            for cl in clusters:
                if cosine(emb, cl["_centroid"]) >= threshold:
                    cl["count"] += 1
                    created = ev.get("created_at") or ""
                    if created > cl["last_seen"]:
                        cl["last_seen"] = created
                    if query and query not in cl["sample_queries"] and len(cl["sample_queries"]) < max_samples:
                        cl["sample_queries"].append(query)
                    placed = True
                    break
        if not placed:
            clusters.append(
                {
                    "_centroid": emb or [],
                    "representative_query": query,
                    "count": 1,
                    "last_seen": ev.get("created_at") or "",
                    "sample_queries": [query] if query else [],
                }
            )
    clusters.sort(key=lambda c: c["count"], reverse=True)
    return [{k: v for k, v in c.items() if k != "_centroid"} for c in clusters]


class GapDetector:
    def __init__(self, supabase: Any):
        self.supabase = supabase

    def record(self, masked_query: str, reason: str, embedding: list[float] | None) -> None:
        self.supabase.table("kb_gap_events").insert(
            {"query": masked_query, "reason": reason, "embedding": embedding}
        ).execute()

    def recent(self, limit: int = 200) -> list[dict]:
        rows = (
            self.supabase.table("kb_gap_events")
            .select("query, reason, embedding, created_at")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
            .data
            or []
        )
        for r in rows:
            r["embedding"] = _parse_vec(r.get("embedding"))
        return rows

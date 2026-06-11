"""Unit tests for RRF fusion — no model, no DB."""

from app.graph.retrieval import rrf_fuse, sparse_score


def test_rrf_fuse_order_k60():
    dense = ["a", "b", "c"]
    sparse = ["b", "a", "d"]
    scores = rrf_fuse([dense, sparse], k=60)

    # Hand-computed with score = Σ 1/(60 + rank), rank starting at 1:
    assert scores["a"] == 1 / 61 + 1 / 62
    assert scores["b"] == 1 / 62 + 1 / 61
    assert scores["c"] == 1 / 63
    assert scores["d"] == 1 / 63

    ordered = sorted(scores, key=scores.get, reverse=True)
    # a and b tie at the top, both strictly above c and d (also tied).
    assert set(ordered[:2]) == {"a", "b"}
    assert scores["a"] > scores["c"]


def test_rrf_single_ranking_preserves_order():
    scores = rrf_fuse([["x", "y", "z"]], k=60)
    assert scores["x"] > scores["y"] > scores["z"]


def test_sparse_score_dot_product():
    q = {"100": 0.5, "200": 0.4}
    c = {"100": 0.2, "300": 0.9}
    assert sparse_score(q, c) == 0.5 * 0.2
    assert sparse_score(q, {}) == 0.0
    assert sparse_score({}, c) == 0.0

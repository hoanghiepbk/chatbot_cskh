"""TIP-015 — semantic cache + gap clustering pure-logic tests (no DB, no model).

Covers the safety-critical cache key: entity match (Hà Nội vs Đà Nẵng),
kb_version invalidation, similarity threshold, TTL, and the PII no-cache rule.
"""

from datetime import datetime, timedelta, timezone

from app.cache.semantic import (
    entities_match,
    extract_entities,
    is_cacheable,
    select_hit,
)
from app.insights.gap import cosine, greedy_cluster

NOW = datetime(2026, 6, 22, 12, 0, 0, tzinfo=timezone.utc)


def _iso(dt):
    return dt.isoformat()


def _cand(sim, entities, kb=1, age_h=1):
    return {
        "id": "x",
        "similarity": sim,
        "entities": entities,
        "kb_version": kb,
        "reply": "r",
        "citations": [],
        "created_at": _iso(NOW - timedelta(hours=age_h)),
    }


# ---------- entity extraction ----------

def test_entities_hanoi_vs_danang_differ():
    assert extract_entities("phí ship ở Hà Nội") == ["loc:ha_noi", "svc:ship"]
    assert extract_entities("phí ship ở Đà Nẵng") == ["loc:da_nang", "svc:ship"]
    assert extract_entities("phí ship ở Hà Nội") != extract_entities("phí ship ở Đà Nẵng")


def test_entities_vehicle_and_service():
    assert extract_entities("bảo dưỡng xe máy") == ["svc:maintenance", "veh:motorbike"]


def test_entities_no_false_match_short_token():
    # 'hue' must not match inside 'thuê'
    assert "loc:hue" not in extract_entities("cho thuê xe tự lái")


def test_entities_match_is_set_equality():
    assert entities_match(["a", "b"], ["b", "a"])
    assert not entities_match(["a"], ["a", "b"])
    assert entities_match([], [])


def test_is_cacheable_blocks_pii():
    assert is_cacheable({})
    assert is_cacheable(None)
    assert not is_cacheable({"PHONE": 1})


# ---------- select_hit gates ----------

def test_select_hit_valid():
    c = _cand(0.95, ["loc:ha_noi"])
    assert select_hit([c], ["loc:ha_noi"], 1, now=NOW) is c


def test_select_hit_entity_mismatch_even_with_high_cosine():
    c = _cand(0.99, ["loc:ha_noi"])
    assert select_hit([c], ["loc:da_nang"], 1, now=NOW) is None


def test_select_hit_kb_version_mismatch():
    c = _cand(0.99, ["loc:ha_noi"], kb=1)
    assert select_hit([c], ["loc:ha_noi"], 2, now=NOW) is None


def test_select_hit_below_threshold():
    assert select_hit([_cand(0.92, ["loc:ha_noi"])], ["loc:ha_noi"], 1, now=NOW) is None


def test_select_hit_ttl_expired():
    assert select_hit([_cand(0.99, ["loc:ha_noi"], age_h=25)], ["loc:ha_noi"], 1, now=NOW) is None


def test_select_hit_skips_bad_picks_good():
    bad = _cand(0.98, ["loc:da_nang"])
    good = _cand(0.95, ["loc:ha_noi"])
    assert select_hit([bad, good], ["loc:ha_noi"], 1, now=NOW) is good


# ---------- gap clustering ----------

def test_cosine():
    assert cosine([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert abs(cosine([1.0, 0.0], [0.0, 1.0])) < 1e-9


def test_greedy_cluster_groups_similar():
    events = [
        {"query": "đổi size lốp", "embedding": [1.0, 0.0, 0.0], "created_at": "2026-06-22T10:00:00+00:00"},
        {"query": "đổi cỡ lốp xe", "embedding": [0.99, 0.02, 0.0], "created_at": "2026-06-22T09:00:00+00:00"},
        {"query": "rửa xe ở đâu", "embedding": [0.0, 1.0, 0.0], "created_at": "2026-06-22T08:00:00+00:00"},
    ]
    clusters = greedy_cluster(events, threshold=0.9)
    assert len(clusters) == 2
    assert clusters[0]["count"] == 2  # sorted by count desc
    assert clusters[0]["representative_query"] == "đổi size lốp"
    assert "đổi cỡ lốp xe" in clusters[0]["sample_queries"]
    assert clusters[1]["count"] == 1

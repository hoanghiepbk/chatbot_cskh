"""TIP-015 semantic cache for the faq branch — SAFETY OVER SPEED.

A hit requires ALL of: cosine ≥ 0.93, intent='faq', entities match exactly, and
kb_version = current. We never cache a turn with PII, and only store replies that
PASSED guardrail_out. kb_version in the key auto-invalidates every entry on a KB
re-ingest; a 24h TTL is a second safety net.

Entity note: the faq branch has no NER/slot extraction (those live in the action
path; PhoBERT NER is off by default), so — to honor "no extra LLM call for the
cache" while still matching entities — entities are extracted DETERMINISTICALLY
from a small gazetteer. This is the guard that stops "phí ship Hà Nội" from
hitting a "Đà Nẵng" cache entry.
"""

import re
import time
import unicodedata
from datetime import datetime, timezone
from typing import Any

SIMILARITY_THRESHOLD = 0.93
TTL_HOURS = 24
MATCH_COUNT = 5

# canonical token -> surface forms (matched on diacritic-stripped, lowercased text)
_LOCATIONS = {
    "loc:ha_noi": ["ha noi"],
    "loc:da_nang": ["da nang"],
    "loc:ho_chi_minh": ["ho chi minh", "tphcm", "tp hcm", "sai gon", "hcm"],
    "loc:hai_phong": ["hai phong"],
    "loc:can_tho": ["can tho"],
    "loc:hue": ["hue"],
    "loc:nha_trang": ["nha trang"],
    "loc:da_lat": ["da lat"],
    "loc:vung_tau": ["vung tau"],
    "loc:bien_hoa": ["bien hoa"],
    "loc:binh_duong": ["binh duong"],
    "loc:dong_nai": ["dong nai"],
    "loc:quang_ninh": ["quang ninh"],
    "loc:thanh_hoa": ["thanh hoa"],
    "loc:nghe_an": ["nghe an", "vinh"],
    "loc:bac_ninh": ["bac ninh"],
}
_VEHICLES = {
    "veh:motorbike": ["xe may", "xe ga", "xe so", "xe tay ga"],
    "veh:car": ["o to", "oto", "xe hoi", "xe con", "xe 4 banh", "xe 4 cho"],
    "veh:winner": ["winner"],
    "veh:vision": ["vision"],
    "veh:sh": ["sh"],
    "veh:lead": ["lead"],
    "veh:exciter": ["exciter"],
    "veh:wave": ["wave"],
    "veh:airblade": ["air blade", "airblade"],
    "veh:vario": ["vario"],
    "veh:sirius": ["sirius"],
}
_SERVICES = {
    "svc:oil": ["thay nhot", "nhot", "dau nhot"],
    "svc:tire": ["lop", "vo xe", "thay lop"],
    "svc:battery": ["ac quy", "binh dien"],
    "svc:brake": ["phanh", "thang xe"],
    "svc:maintenance": ["bao duong", "bao tri"],
    "svc:repair": ["sua chua", "sua xe"],
    "svc:wash": ["rua xe"],
    "svc:warranty": ["bao hanh"],
    "svc:bodywork": ["dong son", "son xe"],
    "svc:ship": ["ship", "giao hang", "van chuyen"],
}
_GAZETTEER: dict[str, list[str]] = {**_LOCATIONS, **_VEHICLES, **_SERVICES}


def _normalize(text: str) -> str:
    """Lowercase + strip Vietnamese diacritics for robust keyword matching.
    đ/Đ are handled explicitly — NFD does not decompose them to 'd'."""
    lowered = text.lower().replace("đ", "d")
    nfkd = unicodedata.normalize("NFD", lowered)
    return "".join(c for c in nfkd if unicodedata.category(c) != "Mn")


def extract_entities(text: str) -> list[str]:
    """Deterministic entity set (locations / vehicles / services) — no LLM call.
    Word-boundary matched so short tokens ('sh', 'hue') don't false-match."""
    norm = _normalize(text)
    found: set[str] = set()
    for canonical, forms in _GAZETTEER.items():
        for form in forms:
            if re.search(r"\b" + re.escape(form) + r"\b", norm):
                found.add(canonical)
                break
    return sorted(found)


def entities_match(a: Any, b: Any) -> bool:
    """Exact set equality — a query entity the cache entry lacks (or vice versa)
    means DIFFERENT context → not a hit."""
    return set(a or []) == set(b or [])


def is_cacheable(pii_found: Any) -> bool:
    """Never cache a turn whose question contained PII."""
    return not pii_found


def _parse_ts(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def is_fresh(created_at: Any, now: datetime | None = None, ttl_hours: int = TTL_HOURS) -> bool:
    created = _parse_ts(created_at)
    if created is None:
        return False
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return (now - created).total_seconds() <= ttl_hours * 3600


def select_hit(
    candidates: list[dict],
    query_entities: list[str],
    current_kb_version: int,
    now: datetime | None = None,
    threshold: float = SIMILARITY_THRESHOLD,
) -> dict | None:
    """Pure decision over RPC candidates (ordered by similarity desc). A candidate
    must clear EVERY gate: similarity, kb_version, entity match, freshness."""
    for c in candidates:
        if float(c.get("similarity", 0.0)) < threshold:
            continue
        if int(c.get("kb_version", -1)) != int(current_kb_version):
            continue
        if not entities_match(query_entities, c.get("entities")):
            continue
        if not is_fresh(c.get("created_at"), now):
            continue
        return c
    return None


class SemanticCache:
    """DB-backed faq cache. Embeddings are passed in (computed once per turn via
    retrieval.embed_dense) so there's no extra model call here."""

    def __init__(self, supabase: Any):
        self.supabase = supabase

    def current_kb_version(self) -> int:
        rows = (
            self.supabase.table("kb_meta").select("value").eq("key", "kb_version").execute().data
        )
        return int(rows[0]["value"]) if rows else 0

    def lookup(
        self, embedding: list[float], entities: list[str], kb_version: int, now: datetime | None = None
    ) -> dict | None:
        start = time.perf_counter()
        res = self.supabase.rpc(
            "match_faq_cache",
            {"query_vec": embedding, "kb_ver": kb_version, "match_count": MATCH_COUNT},
        ).execute()
        hit = select_hit(res.data or [], entities, kb_version, now=now)
        if hit is None:
            return None
        latency_ms = max(1, int((time.perf_counter() - start) * 1000))
        self.supabase.table("faq_cache").update(
            {"hit_count": int(hit.get("hit_count") or 0) + 1, "last_hit_at": "now()"}
        ).eq("id", hit["id"]).execute()
        return {
            "id": hit["id"],
            "reply": hit["reply"],
            "citations": hit.get("citations") or [],
            "similarity": float(hit["similarity"]),
            "latency_ms": latency_ms,
        }

    def store(
        self,
        embedding: list[float],
        entities: list[str],
        kb_version: int,
        reply: str,
        citations: list[dict],
    ) -> str:
        res = self.supabase.table("faq_cache").insert(
            {
                "query_embedding": embedding,
                "intent": "faq",
                "entities": entities,
                "kb_version": kb_version,
                "reply": reply,
                "citations": citations or [],
            }
        ).execute()
        return res.data[0]["id"]

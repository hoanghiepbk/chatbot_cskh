"""Scorers (TIP-009): exact, contains (diacritic-normalized), citations.

Pure-python, no agent imports — runnable in unit tests without a service.
"""

import unicodedata
from dataclasses import dataclass, field


def normalize(text: str) -> str:
    """Lowercase + strip Vietnamese diacritics (đ→d) — same intent as the agent
    pre_gate normalizer, so 'tham khảo' matches 'Tham Khao'."""
    lowered = (text or "").lower().replace("đ", "d")
    decomposed = unicodedata.normalize("NFD", lowered)
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


@dataclass
class CaseResult:
    case_id: str
    group: str
    passed: bool
    checks: list[dict] = field(default_factory=list)  # [{name, ok, expected, actual}]

    def add(self, name, ok, expected=None, actual=None):
        self.checks.append({"name": name, "ok": ok, "expected": expected, "actual": actual})
        if not ok:
            self.passed = False


def score_contains(reply: str, must_contain=None, must_not_contain=None) -> list[dict]:
    """Substring checks on the diacritic-normalized reply."""
    norm = normalize(reply)
    checks = []
    for needle in must_contain or []:
        ok = normalize(needle) in norm
        checks.append({"name": "must_contain", "ok": ok, "expected": needle,
                       "actual": None if ok else "(missing)"})
    for needle in must_not_contain or []:
        hit = normalize(needle) in norm
        checks.append({"name": "must_not_contain", "ok": not hit, "expected": needle,
                       "actual": needle if hit else None})
    return checks


def score_citations(citations: list, expected_docs=None) -> list[dict]:
    """Every expected doc_id must appear among the reply's citations."""
    got = {c.get("doc_id") for c in (citations or []) if isinstance(c, dict)}
    checks = []
    for doc in expected_docs or []:
        ok = doc in got
        checks.append({"name": "citations_doc", "ok": ok, "expected": doc,
                       "actual": sorted(got) if not ok else None})
    return checks


def score_exact(actual: dict, expect: dict) -> list[dict]:
    """Binary checks for intent / escalated / pending_action_type / guardrail_out_block."""
    checks = []
    fields = {
        "intent": actual.get("intent"),
        "escalated": actual.get("escalated"),
        "pending_action_type": (actual.get("pending_action") or {}).get("type")
        if isinstance(actual.get("pending_action"), dict) else None,
        "guardrail_out_block": actual.get("guardrail_out_block"),
    }
    for key, got in fields.items():
        if key not in expect:
            continue
        ok = got == expect[key]
        checks.append({"name": key, "ok": ok, "expected": expect[key], "actual": got})
    return checks


def score_case(case_id, group, expect, actual) -> CaseResult:
    """actual: {reply, intent, escalated, citations, pending_action, guardrail_out_block}."""
    result = CaseResult(case_id=case_id, group=group, passed=True)
    for chk in score_exact(actual, expect):
        result.add(chk["name"], chk["ok"], chk["expected"], chk["actual"])
    for chk in score_contains(
        actual.get("reply", ""), expect.get("must_contain"), expect.get("must_not_contain")
    ):
        result.add(chk["name"], chk["ok"], chk["expected"], chk["actual"])
    for chk in score_citations(actual.get("citations"), expect.get("citations_doc")):
        result.add(chk["name"], chk["ok"], chk["expected"], chk["actual"])
    return result

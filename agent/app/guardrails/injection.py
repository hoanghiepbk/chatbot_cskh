"""Prompt-injection scoring — heuristic v1 (PhoBERT regression head replaces this
in TIP-012a). Score is additive per pattern group: +0.4 each, capped at 1.0.

The decision threshold does NOT live here — callers read it (TIP-005/007 use 0.5
hardcoded with a TODO to move it to policy_registry).
"""

import re

from app.guardrails.pre_gate import normalize_text

GROUP_WEIGHT = 0.4

# Matched on lowercase, diacritics-stripped text.
PATTERN_GROUPS_NORMALIZED = [
    # override prior instructions (vi)
    r"(bo qua|quen)\s+(moi|tat ca|cac|nhung)?\s*(huong dan|chi dan)",
    # override prior instructions (en)
    r"ignore\s+(all\s+)?(previous|above|prior)\s+instructions?",
    # system prompt probing
    r"system prompt|lenh he thong",
    # persona hijack
    r"\bban\s+(bay gio\s+)?la\b|you are now|act as|pretend (to be|you)",
    # developer mode
    r"developer mode|che do nha phat trien",
    # exfiltrate instructions
    r"(in ra|tiet lo|cho .{0,12}xem)\s+.{0,40}(prompt|huong dan cua ban)",
    # delimiter spoofing
    r"begin system|end system",
    # jailbreak keyword
    r"jailbreak",
]

# DAN must keep case sensitivity: stripped lowercase "dan" collides with
# "hướng dẫn"/"dân". Matched on the RAW text as an uppercase word.
PATTERN_GROUPS_RAW = [
    r"\bDAN\b",
]


def score_injection(text: str) -> float:
    norm = normalize_text(text)
    hits = sum(1 for p in PATTERN_GROUPS_NORMALIZED if re.search(p, norm))
    hits += sum(1 for p in PATTERN_GROUPS_RAW if re.search(p, text))
    return min(1.0, hits * GROUP_WEIGHT)

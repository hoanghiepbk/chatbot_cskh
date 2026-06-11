"""Emergency pre-gate — layer 1 of the 2-layer emergency detection (Blueprint §6.2).

Pure keyword matching, runs BEFORE any model. Layer 2 (PhoBERT intent) lands in
TIP-012a.
"""

import re
import unicodedata

from app.guardrails import emergency_terms as terms


def normalize_text(text: str) -> str:
    """Lowercase + strip Vietnamese diacritics ('đ' handled explicitly)."""
    lowered = text.lower().replace("đ", "d")
    decomposed = unicodedata.normalize("NFD", lowered)
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def check_emergency(text: str) -> bool:
    low = text.lower()
    norm = normalize_text(text)

    if any(t in low for t in terms.TERMS_DIACRITIC):
        return True
    if any(t in norm for t in terms.TERMS_NORMALIZED):
        return True
    if terms.HIGHWAY_CONTEXT in norm:
        signals = r"\b(" + "|".join(terms.HIGHWAY_SIGNALS) + r")\b"
        if re.search(signals, norm):
            return True
    if terms.RESCUE_CONTEXT in norm:
        urgency = r"\b(" + "|".join(terms.RESCUE_URGENCY_WORDS) + r")\b"
        if re.search(urgency, norm):
            return True
    return False

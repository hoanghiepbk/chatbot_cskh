"""Shared core for the synthetic data pipeline (TIP-011).

The pure-python helpers (normalize, near-dup, NER span tools, jsonl IO) import
NO network/anthropic — test_pipeline.py exercises them without an API key. The
Anthropic client is created lazily in get_client() only when generating/judging.
"""

import json
import re
import sys
import unicodedata
from pathlib import Path

# Reports print Vietnamese + arrows; force UTF-8 so the Windows cp1252 console
# does not crash on output (every pipeline script imports this module).
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

# 8 intent labels — Blueprint §5 / app/graph/core.py INTENTS (single source).
INTENTS = [
    "faq", "booking", "order_lookup", "modify_booking",
    "emergency", "complaint", "chitchat", "out_of_scope",
]
# 4 PII types per guardrails/pii.py. NOTE: Blueprint §8 also lists ADDRESS/NAME
# for the PhoBERT NER head — those are OUT OF SCOPE for TIP-011 (no regex backing
# in pii.py yet); flagged in the completion report for TIP-012a.
PII_TYPES = ["PHONE", "PLATE", "ID", "EMAIL"]
# Controlled-generation style axes (diversity is mandatory — TIP-011 §1).
STYLES = [
    "chuan", "khong_dau", "khong_dau_phan", "teencode",
    "sai_chinh_ta", "asr_noise", "emoji",
]

DATA_DIR = Path(__file__).resolve().parent / "data"

# Mirror of guardrails/pii.py regexes — used to sanity-check NER spans and to
# catch PII leaking into "negative" samples (the km/money-≠-PII lesson, TIP-004).
PII_REGEX = {
    "PHONE": re.compile(r"(?<!\d)(?:\+84|0)(?:[\s.\-]?\d){9}(?!\d)"),
    "PLATE": re.compile(r"(?<![\w.])\d{2}[-\s]?[A-Za-z]{1,2}\d?[-\s]?\d{3}[.\s]?\d{1,2}(?![\w.])"),
    "ID": re.compile(r"(?<!\d)(?:\d{12}|\d{9})(?!\d)"),
    "EMAIL": re.compile(r"[\w.+-]+@[\w-]+\.[\w.\-]+"),
}

MODEL_GEN = "claude-sonnet-4-5"    # generation — realism + controlled diversity
MODEL_JUDGE = "claude-haiku-4-5"   # quality judge — cheap, batched
PRICING = {MODEL_GEN: (3.0, 15.0), MODEL_JUDGE: (1.0, 5.0)}  # USD / 1M (in, out)


# ---------- normalization + near-duplicate detection ----------

def normalize(text: str) -> str:
    """Lowercase, strip diacritics (đ→d), drop punctuation, collapse spaces —
    the comparison key for dedup and leak checks."""
    lowered = (text or "").lower().replace("đ", "d")
    decomposed = unicodedata.normalize("NFD", lowered)
    no_marks = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", no_marks)).strip()


def _tokens(text: str) -> set:
    return set(normalize(text).split())


def is_near_dup(a: str, b: str, threshold: float = 0.9) -> bool:
    """Jaccard token overlap on the normalized text — catches diacritic/punct/
    word-order variants of the same message."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return normalize(a) == normalize(b)
    return len(ta & tb) / len(ta | tb) >= threshold


def dedup(rows: list[dict], key: str = "text", threshold: float = 0.9):
    """Return (kept, removed). Drops exact-normalized AND near-duplicate rows."""
    kept, removed, seen = [], [], set()
    for row in rows:
        norm = normalize(row.get(key, ""))
        if norm in seen:
            removed.append({**row, "reason": "rule:exact_dup"})
            continue
        if any(is_near_dup(row[key], k[key], threshold) for k in kept):
            removed.append({**row, "reason": "rule:near_dup"})
            continue
        kept.append(row)
        seen.add(norm)
    return kept, removed


# ---------- NER span tools ----------

def attach_spans(text: str, raw_entities: list[dict]):
    """Compute char spans by locating each entity's surface value in text
    (str.find — robust; LLMs miscount offsets). Returns (entities, ok).
    ok=False if a value is missing/typeless → the record is rejected by the rule tier."""
    spans: list[dict] = []
    for ent in raw_entities or []:
        value, etype = (ent.get("value") or "").strip(), ent.get("type")
        if etype not in PII_TYPES or not value:
            return [], False
        start = 0
        while True:
            idx = text.find(value, start)
            if idx < 0:
                return [], False  # value not present in text → invalid
            if not any(s < idx + len(value) and idx < e for s, e in
                       ((x["start"], x["end"]) for x in spans)):
                spans.append({"type": etype, "start": idx, "end": idx + len(value), "value": value})
                break
            start = idx + 1
    return spans, True


def valid_ner_record(rec: dict) -> bool:
    """Span integrity: in-bounds, text[start:end]==value, surface matches the type
    regex; for negatives (no entities) the text must contain NO regex PII at all."""
    text = rec.get("text", "")
    entities = rec.get("entities", [])
    if not isinstance(entities, list):
        return False
    if not entities:
        # negative sample — must NOT contain any real PII (km/money are allowed)
        return not any(rx.search(text) for rx in PII_REGEX.values())
    for ent in entities:
        try:
            start, end, value, etype = ent["start"], ent["end"], ent["value"], ent["type"]
        except (KeyError, TypeError):
            return False
        if etype not in PII_TYPES:
            return False
        if not (0 <= start < end <= len(text)) or text[start:end] != value:
            return False
        if not PII_REGEX[etype].fullmatch(value):
            return False
    return True


# ---------- jsonl IO ----------

def read_jsonl(path) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path, rows: list[dict]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


# ---------- LLM helpers (lazy — no import at module load) ----------

def load_env() -> None:
    """Load repo-root .env (script runs as its own process; never overrides real env)."""
    import os

    env_file = Path(__file__).resolve().parents[3] / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def get_client():
    from anthropic import Anthropic

    load_env()
    return Anthropic()


def cost_usd(model: str, in_tok: int, out_tok: int) -> float:
    pin, pout = PRICING.get(model, (0.0, 0.0))
    return (in_tok * pin + out_tok * pout) / 1_000_000


def extract_json_array(text: str):
    """Tolerant: parse the first [...] JSON array in the model output."""
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        m = re.search(r"\[.*\]", text, re.DOTALL)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None

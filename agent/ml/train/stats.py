"""Dataset stats report (TIP-011) — reads data/*.jsonl and prints the distribution
tables the completion report quotes. No network.

    cd agent && uv run python ml/train/stats.py
"""

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import DATA_DIR, read_jsonl  # noqa: E402


def show(title, counter):
    print(f"\n{title}")
    for k, v in sorted(counter.items()):
        print(f"  {str(k):20s} {v}")


def main() -> int:
    intent_raw = read_jsonl(DATA_DIR / "intent_raw.jsonl")
    intent_clean = read_jsonl(DATA_DIR / "intent_clean.jsonl")
    ner_raw = read_jsonl(DATA_DIR / "ner_raw.jsonl")
    ner_clean = read_jsonl(DATA_DIR / "ner_clean.jsonl")
    rejected = read_jsonl(DATA_DIR / "rejected.jsonl")

    print("=" * 56)
    print("XeCare synthetic dataset — stats (TIP-011)")
    print("=" * 56)

    show("INTENT label — RAW", Counter(r.get("label") for r in intent_raw))
    show("INTENT label — CLEAN", Counter(r.get("label") for r in intent_clean))
    show("STYLE — RAW (intent)", Counter(r.get("style") for r in intent_raw))

    def ner_type(r):
        ents = r.get("entities") or []
        return ents[0]["type"] if ents else "NONE(negative)"

    show("NER stratum — RAW", Counter(ner_type(r) for r in ner_raw))
    show("NER stratum — CLEAN", Counter(ner_type(r) for r in ner_clean))

    show("REJECTED by reason", Counter(r.get("reason") for r in rejected))
    show("REJECTED by task", Counter(r.get("task") for r in rejected))

    print("\nSPLIT counts")
    for name in ("intent", "ner"):
        parts = {s: len(read_jsonl(DATA_DIR / f"{name}_{s}.jsonl"))
                 for s in ("train", "val", "test")}
        total = sum(parts.values())
        print(f"  {name}: {parts}  (total {total})")

    print("\n5 REJECTED examples (chống label-noise — minh chứng):")
    for r in rejected[:5]:
        txt = (r.get("text") or "")[:80]
        print(f"  [{r.get('task')}/{r.get('reason')}] {txt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

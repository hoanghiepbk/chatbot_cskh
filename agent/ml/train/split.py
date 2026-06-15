"""Stratified split (TIP-011) — train 70 / val 15 / test 15, test FROZEN & never
used for any filtering/balancing decision. No oversampling (avoids dup leakage):
class imbalance is handed to TIP-012a as class_weights.json instead.

    cd agent && uv run python ml/train/split.py
"""

import argparse
import json
import random
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import DATA_DIR, normalize, read_jsonl, write_jsonl  # noqa: E402

SEED = 42


def intent_stratum(r: dict) -> str:
    return r["label"]


def ner_stratum(r: dict) -> str:
    ents = r.get("entities") or []
    return ents[0]["type"] if ents else "NONE"


def stratified_split(rows, stratum_fn, ratios=(0.70, 0.15, 0.15), seed=SEED):
    """Per stratum: shuffle (seeded) then slice. Guarantees ≥1 in test and ≥1 in
    train for any stratum with ≥2 samples (so every label appears in test)."""
    rng = random.Random(seed)
    groups: dict[str, list] = {}
    for r in rows:
        groups.setdefault(stratum_fn(r), []).append(r)

    train, val, test = [], [], []
    for _, items in sorted(groups.items()):
        items = items[:]
        rng.shuffle(items)
        n = len(items)
        if n == 1:
            train += items
            continue
        n_test = max(1, round(n * ratios[2]))
        n_val = max(1, round(n * ratios[1])) if n >= 3 else 0
        n_test = min(n_test, n - 1)            # keep ≥1 for train
        n_val = min(n_val, n - n_test - 1) if n - n_test - 1 > 0 else 0
        test += items[:n_test]
        val += items[n_test:n_test + n_val]
        train += items[n_test + n_val:]
    return train, val, test


def leak_texts(a, b) -> set:
    """Normalized texts appearing in BOTH splits (must be empty)."""
    return {normalize(r["text"]) for r in a} & {normalize(r["text"]) for r in b}


def class_weights(rows, stratum_fn) -> dict:
    """Inverse-frequency weights (mean-normalized) for TIP-012a training."""
    counts = Counter(stratum_fn(r) for r in rows)
    total, k = sum(counts.values()), len(counts)
    return {lab: round((total / (k * c)), 4) for lab, c in counts.items()}


def _dist(rows, stratum_fn):
    return dict(sorted(Counter(stratum_fn(r) for r in rows).items()))


def split_task(name, rows, stratum_fn):
    train, val, test = stratified_split(rows, stratum_fn)
    write_jsonl(DATA_DIR / f"{name}_train.jsonl", train)
    write_jsonl(DATA_DIR / f"{name}_val.jsonl", val)
    write_jsonl(DATA_DIR / f"{name}_test.jsonl", test)

    leak_tt = leak_texts(train, test)
    leak_tv = leak_texts(train, val)
    test_strata = set(_dist(test, stratum_fn))
    all_strata = set(_dist(rows, stratum_fn))
    print(f"\n[{name}] total {len(rows)} → train {len(train)} / val {len(val)} / test {len(test)}")
    print(f"  test dist: {_dist(test, stratum_fn)}")
    print(f"  leak train∩test: {len(leak_tt)}  | train∩val: {len(leak_tv)}  (phải = 0)")
    missing = all_strata - test_strata
    print(f"  strata vắng trong test: {missing or 'KHÔNG (đủ mọi nhãn)'}")
    assert not leak_tt, f"LEAK {name} train/test: {leak_tt}"
    assert not leak_tv, f"LEAK {name} train/val: {leak_tv}"
    return train, val, test


def main() -> int:
    argparse.ArgumentParser(description="XeCare stratified split").parse_args()

    intent = read_jsonl(DATA_DIR / "intent_clean.jsonl")
    ner = read_jsonl(DATA_DIR / "ner_clean.jsonl")
    split_task("intent", intent, intent_stratum)
    split_task("ner", ner, ner_stratum)

    weights = {
        "intent": class_weights(intent, intent_stratum),
        "ner_stratum": class_weights(ner, ner_stratum),
    }
    (DATA_DIR / "class_weights.json").write_text(
        json.dumps(weights, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nclass_weights.json written (for TIP-012a):\n  intent={weights['intent']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

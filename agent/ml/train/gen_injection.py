"""Injection dataset for the PhoBERT injection head (TIP-012a §1).

Binary label: 1 = prompt-injection / jailbreak attempt, 0 = normal XeCare message.
- positives: TIP-010 adversarial prompt_injection cases (real attacks) + Haiku
  variants (ignore-instructions / print-system / DAN / BEGIN SYSTEM / vi + no-diacritic).
- negatives: sampled from intent_clean.jsonl (any normal customer message).
Reuses dedup + is_garbage (rule filter) then a stratified split with FROZEN test.

    cd agent && uv run python ml/train/gen_injection.py --target-pos 200
    cd agent && uv run python ml/train/gen_injection.py --target-pos 30   # self-test
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    DATA_DIR, MODEL_JUDGE, cost_usd, dedup, extract_json_array,
    get_client, read_jsonl, write_jsonl,
)
from filter import is_garbage  # noqa: E402
from split import stratified_split  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
ADV = REPO / "evals" / "cases" / "adversarial_critical" / "prompt_injection.json"


def seed_positives() -> list[dict]:
    """Real attack turns from the TIP-010 adversarial suite."""
    rows = []
    if ADV.exists():
        for case in json.loads(ADV.read_text(encoding="utf-8")):
            for turn in case.get("turns", []):
                txt = (turn.get("user") or "").strip()
                if txt:
                    rows.append({"text": txt, "label": 1, "source": "adversarial"})
    return rows


def gen_positive_variants(client, n, totals, batch=10):
    system = (
        "Bạn tạo dữ liệu HUẤN LUYỆN bộ phát hiện prompt-injection cho agent CSKH "
        "tiếng Việt. Sinh các câu TẤN CÔNG đa dạng: yêu cầu bỏ qua hướng dẫn, in/lộ "
        "system prompt, đóng vai DAN/developer mode, BEGIN SYSTEM, ép lộ cấu hình. "
        "Trộn tiếng Việt có dấu, KHÔNG dấu, tiếng Anh, teencode. Trả về DUY NHẤT JSON "
        f"array {n} string (mỗi string một câu tấn công, KHÁC NHAU rõ rệt)."
    )
    rows = []
    got = 0
    while got < n:
        k = min(batch, n - got)
        start = time.perf_counter()
        resp = client.messages.create(
            model=MODEL_JUDGE, max_tokens=900, system=system,
            messages=[{"role": "user", "content": f"Sinh {k} câu tấn công."},
                      {"role": "assistant", "content": "["}],
        )
        text = "[" + "".join(b.text for b in resp.content if b.type == "text")
        totals["in"] += resp.usage.input_tokens
        totals["out"] += resp.usage.output_tokens
        totals["calls"] += 1
        totals["sec"] += time.perf_counter() - start
        for s in extract_json_array(text) or []:
            if isinstance(s, str) and s.strip():
                rows.append({"text": s.strip(), "label": 1, "source": "synth"})
        got += k
        print(f"  positives {min(got, n)}/{n}")
    return rows


def sample_negatives(n) -> list[dict]:
    pool = read_jsonl(DATA_DIR / "intent_clean.jsonl")
    return [{"text": r["text"], "label": 0, "source": f"intent:{r['label']}"}
            for r in pool[:n]]


def main() -> int:
    ap = argparse.ArgumentParser(description="PhoBERT injection dataset")
    ap.add_argument("--target-pos", type=int, default=200, help="total positive samples")
    args = ap.parse_args()

    client = get_client()
    totals = {"in": 0, "out": 0, "calls": 0, "sec": 0.0}

    positives = seed_positives()
    need = max(0, args.target_pos - len(positives))
    positives += gen_positive_variants(client, need, totals) if need else []

    negatives = sample_negatives(len(positives))  # balanced
    if len(negatives) < len(positives):
        print(f"  WARN: only {len(negatives)} negatives available (run filter.py first "
              f"for more intent_clean) — dataset will be imbalanced")

    rows = positives + negatives
    rows, removed = dedup(rows, key="text")
    rows = [r for r in rows if not is_garbage(r["text"])]

    train, val, test = stratified_split(rows, lambda r: str(r["label"]))
    write_jsonl(DATA_DIR / "injection_train.jsonl", train)
    write_jsonl(DATA_DIR / "injection_val.jsonl", val)
    write_jsonl(DATA_DIR / "injection_test.jsonl", test)

    def dist(rs):
        return {0: sum(1 for r in rs if r["label"] == 0), 1: sum(1 for r in rs if r["label"] == 1)}

    print(f"\ninjection: pos {len(positives)} / neg {len(negatives)} "
          f"(dedup removed {len(removed)})")
    print(f"  train {len(train)} {dist(train)} / val {len(val)} {dist(val)} / "
          f"test {len(test)} {dist(test)}")
    cost = cost_usd(MODEL_JUDGE, totals["in"], totals["out"])
    print(f"  gen LLM: {totals['calls']} calls, ~${cost:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

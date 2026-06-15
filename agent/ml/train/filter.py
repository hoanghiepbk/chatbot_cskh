"""Quality filter (TIP-011) — anti-label-noise (bài học audio VSF).

Tier 1 (rule, pure, no network): dedup near-dup, length, label∈8, NER span check,
language garbage. Tier 2 (Haiku judge, batched): label_correct / natural / confident
→ loại nếu bất kỳ false, HOẶC judge gợi ý nhãn khác (KHÔNG tự đổi nhãn — loại + log).

    cd agent && uv run python ml/train/filter.py
"""

import argparse
import sys
import time
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    DATA_DIR, INTENTS, MODEL_JUDGE, attach_spans, cost_usd, dedup,
    extract_json_array, get_client, read_jsonl, valid_ner_record, write_jsonl,
)

MAX_LEN = 400


def is_garbage(text: str) -> bool:
    """Reject empty / too long / foreign-script (CJK…) / no alphanumeric at all.
    NOTE: no letter-RATIO check — NER PII samples are digit-heavy and emoji-style
    samples carry non-letter chars; both are legitimate."""
    t = (text or "").strip()
    if len(t) < 2 or len(t) > MAX_LEN:
        return True
    for ch in t:  # any CJK/Hangul/Hiragana/Cyrillic/Arabic = wrong-language garbage
        name = unicodedata.name(ch, "")
        if name.startswith(("CJK", "HANGUL", "HIRAGANA", "KATAKANA", "CYRILLIC", "ARABIC")):
            return True
    return not any(ch.isalnum() for ch in t)  # pure symbols/emoji-only → garbage


# ---------- Tier 1: rule (pure, testable) ----------

def rule_filter_intent(rows: list[dict]):
    kept, rejected = [], []
    for r in rows:
        text, label = r.get("text", ""), r.get("label")
        if label not in INTENTS:
            rejected.append({**r, "reason": "rule:bad_label"})
        elif is_garbage(text):
            rejected.append({**r, "reason": "rule:length_or_garbage"})
        else:
            kept.append(r)
    kept, dups = dedup(kept, key="text")
    rejected.extend(dups)
    return kept, rejected


def rule_filter_ner(rows: list[dict]):
    kept, rejected = [], []
    for r in rows:
        text = r.get("text", "")
        if is_garbage(text):
            rejected.append({**r, "reason": "rule:length_or_garbage"})
            continue
        spans, ok = attach_spans(text, r.get("entities", []))
        if not ok:
            rejected.append({**r, "reason": "rule:ner_span"})
            continue
        rec = {**r, "entities": spans}
        if not valid_ner_record(rec):
            # negative carrying real PII, or surface not matching its type regex
            rejected.append({**rec, "reason": "rule:ner_invalid"})
            continue
        kept.append(rec)
    kept, dups = dedup(kept, key="text")
    rejected.extend(dups)
    return kept, rejected


# ---------- Tier 2: LLM judge (Haiku, batched) ----------

def _judge_call(client, system, payload, totals, max_tokens=900):
    start = time.perf_counter()
    resp = client.messages.create(
        model=MODEL_JUDGE, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": payload}, {"role": "assistant", "content": "["}],
    )
    text = "[" + "".join(b.text for b in resp.content if b.type == "text")
    totals["in"] += resp.usage.input_tokens
    totals["out"] += resp.usage.output_tokens
    totals["calls"] += 1
    totals["sec"] += time.perf_counter() - start
    return extract_json_array(text)


def judge_intent(client, rows, totals, batch=10):
    kept, rejected = [], []
    system = (
        "Bạn kiểm định dữ liệu train phân loại ý định CSKH XeCare (8 lớp: "
        f"{', '.join(INTENTS)}). Với MỖI mẫu trả về object cùng thứ tự trong JSON "
        "array: {\"label_correct\": bool, \"natural\": bool, \"confident\": bool, "
        "\"suggested_label\": <nhãn đúng nếu label_correct=false, ngược lại null>}. "
        "label_correct=nhãn có khớp nội dung? natural=câu tự nhiên như khách thật? "
        "confident=bạn chắc chắn?"
    )
    for i in range(0, len(rows), batch):
        chunk = rows[i:i + batch]
        payload = "\n".join(
            f'{j}. [label={r["label"]}] {r["text"]}' for j, r in enumerate(chunk)
        )
        verdicts = _judge_call(client, system, payload, totals)
        if not isinstance(verdicts, list) or len(verdicts) != len(chunk):
            # judge parse/length error — keep with flag (never drop good data on judge error)
            for r in chunk:
                kept.append({**r, "judge": "parse_failed"})
            continue
        for r, v in zip(chunk, verdicts):
            v = v if isinstance(v, dict) else {}
            bad = (not v.get("label_correct") or not v.get("natural")
                   or not v.get("confident"))
            sug = v.get("suggested_label")
            if bad or (sug and sug in INTENTS and sug != r["label"]):
                reason = "judge:relabel" if (sug and sug != r["label"]) else "judge:noisy"
                rejected.append({**r, "reason": reason, "judge": v})
            else:
                kept.append(r)
    return kept, rejected


def judge_ner(client, rows, totals, batch=10):
    kept, rejected = [], []
    system = (
        "Bạn kiểm định dữ liệu NER PII (PHONE/PLATE/ID/EMAIL) trong tin nhắn khách. "
        "Với MỖI mẫu trả object cùng thứ tự trong JSON array: {\"entities_correct\": "
        "bool, \"natural\": bool, \"confident\": bool}. entities_correct=các PII đã "
        "đánh dấu đúng VÀ không bỏ sót/không nhầm km-tiền thành PII?"
    )
    for i in range(0, len(rows), batch):
        chunk = rows[i:i + batch]
        payload = "\n".join(
            f'{j}. text="{r["text"]}" entities={[(e["type"], e["value"]) for e in r["entities"]]}'
            for j, r in enumerate(chunk)
        )
        verdicts = _judge_call(client, system, payload, totals)
        if not isinstance(verdicts, list) or len(verdicts) != len(chunk):
            for r in chunk:
                kept.append({**r, "judge": "parse_failed"})
            continue
        for r, v in zip(chunk, verdicts):
            v = v if isinstance(v, dict) else {}
            if not (v.get("entities_correct") and v.get("natural") and v.get("confident")):
                rejected.append({**r, "reason": "judge:noisy", "judge": v})
            else:
                kept.append(r)
    return kept, rejected


def reason_table(rejected: list[dict]) -> dict:
    table: dict[str, int] = {}
    for r in rejected:
        table[r.get("reason", "?")] = table.get(r.get("reason", "?"), 0) + 1
    return dict(sorted(table.items()))


def main() -> int:
    ap = argparse.ArgumentParser(description="XeCare synthetic quality filter")
    ap.add_argument("--no-judge", action="store_true", help="rule tier only (offline)")
    args = ap.parse_args()

    client = None if args.no_judge else get_client()
    totals = {"in": 0, "out": 0, "calls": 0, "sec": 0.0}
    all_rejected = []

    # ----- intent -----
    intent_raw = read_jsonl(DATA_DIR / "intent_raw.jsonl")
    intent_r1, rej = rule_filter_intent(intent_raw)
    all_rejected += [{**x, "task": "intent"} for x in rej]
    if client:
        intent_clean, rej2 = judge_intent(client, intent_r1, totals)
        all_rejected += [{**x, "task": "intent"} for x in rej2]
    else:
        intent_clean = intent_r1
    write_jsonl(DATA_DIR / "intent_clean.jsonl", intent_clean)

    # ----- ner -----
    ner_raw = read_jsonl(DATA_DIR / "ner_raw.jsonl")
    ner_r1, rej = rule_filter_ner(ner_raw)
    all_rejected += [{**x, "task": "ner"} for x in rej]
    if client:
        ner_clean, rej2 = judge_ner(client, ner_r1, totals)
        all_rejected += [{**x, "task": "ner"} for x in rej2]
    else:
        ner_clean = ner_r1
    write_jsonl(DATA_DIR / "ner_clean.jsonl", ner_clean)

    write_jsonl(DATA_DIR / "rejected.jsonl", all_rejected)

    # ----- report -----
    def pct(n, d):
        return f"{(n / d * 100):.1f}%" if d else "0%"

    print(f"\nintent: raw {len(intent_raw)} → clean {len(intent_clean)} "
          f"(loại {pct(len(intent_raw) - len(intent_clean), len(intent_raw))})")
    print(f"ner:    raw {len(ner_raw)} → clean {len(ner_clean)} "
          f"(loại {pct(len(ner_raw) - len(ner_clean), len(ner_raw))})")
    print("rejected by reason:")
    for reason, n in reason_table(all_rejected).items():
        print(f"  {reason:24s} {n}")
    if client:
        cost = cost_usd(MODEL_JUDGE, totals["in"], totals["out"])
        print(f"judge LLM: {totals['calls']} calls, {totals['sec']:.0f}s, ~${cost:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

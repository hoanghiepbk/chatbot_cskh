"""Unit tests for the synthetic pipeline (TIP-011) — pure, no network/API key.

    cd agent && uv run python -m pytest ml/train/test_pipeline.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from common import attach_spans, dedup, is_near_dup, valid_ner_record  # noqa: E402
from filter import rule_filter_intent, rule_filter_ner  # noqa: E402
from split import leak_texts, stratified_split  # noqa: E402


# ---------- near-duplicate detection ----------

def test_near_dup_detects_diacritic_punct_variants():
    a = "anh ơi cho hỏi giá thay nhớt xe"
    b = "anh oi cho hoi gia thay nhot xe!!!"  # no-diacritic + punctuation
    assert is_near_dup(a, b) is True
    assert is_near_dup("đặt lịch bảo dưỡng", "hỏi giá thay lốp") is False


def test_dedup_removes_near_duplicates():
    rows = [
        {"text": "cho mình hỏi giá thay nhớt"},
        {"text": "cho minh hoi gia thay nhot"},   # near-dup of #1
        {"text": "đặt lịch bảo dưỡng xe máy"},
    ]
    kept, removed = dedup(rows)
    assert len(kept) == 2
    assert len(removed) == 1 and removed[0]["reason"].startswith("rule:")


# ---------- NER span validation ----------

def test_attach_spans_locates_value():
    text = "sđt của em là 0912345678 nhé"
    spans, ok = attach_spans(text, [{"type": "PHONE", "value": "0912345678"}])
    assert ok is True
    assert text[spans[0]["start"]:spans[0]["end"]] == "0912345678"


def test_attach_spans_rejects_value_not_in_text():
    _, ok = attach_spans("không có số nào ở đây", [{"type": "PHONE", "value": "0912345678"}])
    assert ok is False


def test_valid_ner_record_catches_bad_span_and_pii_in_negative():
    text = "sđt 0912345678"
    # wrong span (off by chars) → invalid
    bad = {"text": text, "entities": [{"type": "PHONE", "start": 0, "end": 3, "value": "0912345678"}]}
    assert valid_ner_record(bad) is False
    # negative sample that actually contains a phone → invalid (mislabeled)
    assert valid_ner_record({"text": "gọi 0912345678 nhé", "entities": []}) is False
    # clean negative (km/price are NOT PII) → valid
    assert valid_ner_record({"text": "xe đi 20.000 km, hết 350.000đ", "entities": []}) is True


def test_rule_filter_ner_rejects_span_mismatch():
    rows = [
        {"text": "sđt 0912345678", "entities": [{"type": "PHONE", "value": "0912345678"}]},
        {"text": "biển 29A-123.45", "entities": [{"type": "PHONE", "value": "0000000000"}]},  # not in text
    ]
    kept, rejected = rule_filter_ner(rows)
    assert len(kept) == 1
    assert any(r["reason"] == "rule:ner_span" for r in rejected)


# ---------- rule filter (intent) ----------

def test_rule_filter_intent_drops_bad_label_and_garbage():
    rows = [
        {"text": "cho mình đặt lịch bảo dưỡng", "label": "booking"},
        {"text": "xyz", "label": "not_a_label"},          # bad label
        {"text": "日本語のテキストです", "label": "faq"},      # foreign-script garbage
    ]
    kept, rejected = rule_filter_intent(rows)
    assert len(kept) == 1
    reasons = {r["reason"] for r in rejected}
    assert "rule:bad_label" in reasons
    assert "rule:length_or_garbage" in reasons


# ---------- split: no leak + stratified ----------

def _intent_rows():
    rows = []
    for label in ["faq", "booking", "order_lookup", "modify_booking",
                  "emergency", "complaint", "chitchat", "out_of_scope"]:
        for i in range(10):
            rows.append({"text": f"{label} mau so {i}", "label": label})
    return rows


def test_split_no_leak_between_train_and_test():
    train, val, test = stratified_split(_intent_rows(), lambda r: r["label"])
    assert leak_texts(train, test) == set()
    assert leak_texts(train, val) == set()
    assert leak_texts(val, test) == set()


def test_split_stratified_every_label_in_test():
    _, _, test = stratified_split(_intent_rows(), lambda r: r["label"])
    labels_in_test = {r["label"] for r in test}
    assert len(labels_in_test) == 8  # every intent represented in the held-out test
    # test ≈ 15%
    assert 0.10 <= len(test) / 80 <= 0.20

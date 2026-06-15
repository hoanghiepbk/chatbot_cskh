"""Unit tests for eval scorers (TIP-009) — no agent, no DB, no network."""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from scorers import (  # noqa: E402
    normalize,
    score_case,
    score_citations,
    score_contains,
    score_exact,
)


def test_normalize_strips_diacritics():
    assert normalize("Tham Khảo") == "tham khao"
    assert normalize("Đặt lịch") == "dat lich"
    assert normalize("1.200.000đ") == "1.200.000d"


def test_contains_diacritic_insensitive():
    # reply has diacritics, needle doesn't (and vice versa) → still matches
    checks = score_contains("Giá tham khảo khoảng 200k", must_contain=["tham khao"])
    assert all(c["ok"] for c in checks)
    checks = score_contains("gia THAM KHAO", must_contain=["tham khảo"])
    assert all(c["ok"] for c in checks)


def test_must_not_contain_fails_on_hit():
    checks = score_contains("anh cứ yên tâm dùng tiếp nhé", must_not_contain=["yên tâm dùng tiếp"])
    assert checks[0]["ok"] is False
    checks = score_contains("anh nên đi kiểm tra", must_not_contain=["yên tâm dùng tiếp"])
    assert checks[0]["ok"] is True


def test_must_contain_missing_fails():
    checks = score_contains("xin chào", must_contain=["bảo hành"])
    assert checks[0]["ok"] is False


def test_citations_doc():
    cites = [{"doc_id": "04-bang-gia-dich-vu.md", "heading": "Xe máy"}]
    assert score_citations(cites, ["04-bang-gia-dich-vu.md"])[0]["ok"] is True
    assert score_citations(cites, ["01-lich-bao-duong-xe-may.md"])[0]["ok"] is False


def test_exact_intent_and_escalated():
    actual = {"intent": "faq", "escalated": False}
    assert all(c["ok"] for c in score_exact(actual, {"intent": "faq"}))
    assert score_exact(actual, {"intent": "booking"})[0]["ok"] is False
    assert score_exact({"escalated": True}, {"escalated": True})[0]["ok"] is True


def test_exact_pending_action_type():
    actual = {"pending_action": {"type": "book_slot", "stage": "choosing"}}
    assert score_exact(actual, {"pending_action_type": "book_slot"})[0]["ok"] is True
    assert score_exact(actual, {"pending_action_type": "cancel_parts_order"})[0]["ok"] is False
    # no pending action → mismatch
    assert score_exact({"pending_action": None}, {"pending_action_type": "book_slot"})[0]["ok"] is False


def test_score_case_combines_and_fails_fast():
    actual = {"intent": "faq", "escalated": False,
              "reply": "Giá tham khảo 200k", "citations": [{"doc_id": "04-bang-gia-dich-vu.md"}]}
    expect = {"intent": "faq", "must_contain": ["tham khảo"],
              "citations_doc": ["04-bang-gia-dich-vu.md"]}
    assert score_case("c1", "faq", expect, actual).passed is True

    bad = score_case("c2", "faq", {"intent": "booking"}, actual)
    assert bad.passed is False

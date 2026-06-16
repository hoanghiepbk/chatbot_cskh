"""TIP-013 — registry safety boundary (OFFLINE, no DB/LLM).

The prompt/policy registry can change SOFT params, but it can NEVER disable the
HARD guardrail rules in code. A policy that OMITS refund_cap_vnd must still block a
5,000,000đ refund promise because apply_hard_rules carries a 2,000,000 code default.
"""

from app.guardrails.output import apply_hard_rules


def test_refund_hard_rule_survives_policy_without_cap():
    text = "Dạ mình sẽ hoàn lại cho anh 5.000.000đ ngay ạ."
    sanitized, rules_hit, severity = apply_hard_rules(text, "complaint", {})  # policy omits cap
    assert "refund_cap" in rules_hit
    assert severity == "block"
    assert "5.000.000" not in sanitized


def test_refund_below_default_cap_not_blocked():
    text = "Dạ XeCare có thể hoàn lại 300.000đ phí kiểm tra cho anh ạ."  # 300k < 2,000,000 default
    _, rules_hit, _ = apply_hard_rules(text, "complaint", {})
    assert "refund_cap" not in rules_hit

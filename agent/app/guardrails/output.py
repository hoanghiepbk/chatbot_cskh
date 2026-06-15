"""Output guardrail (TIP-007) — runs BEFORE unmask, so neither the hard rules
nor the rubric ever see raw PII.

Layer A — hard rules, every branch including pure templates (~0ms):
  1. refund_cap        promise to refund more than policy refund_cap_vnd
  2. remote_safety     remote "it's safe" verdict on brakes/steering/tyres/frame
  3. price_disclaimer  concrete VND price without a "tham khảo/ước tính" marker
  4. leak              system-prompt markers / broken internal placeholders

Layer B — Haiku rubric, ONLY for replies with LLM-generated content
(faq/chitchat/action-extract). Any flag → ONE Haiku rewrite → re-run layer A;
still hitting a block-level rule → safe fallback. Broken rubric JSON →
pass-with-flag (never punish the customer for a parse error; layer A already ran).
"""

import re
from dataclasses import dataclass, field

from app.llm import MODEL_HAIKU

HOTLINE = "1900 1234"
# TIP-010 critical hardening: complaint replies are LLM-generated (Sonnet/Haiku) and
# may discuss refunds/compensation/safety — they must get the Layer B rubric backstop
# (promises_outside_policy / unsafe_advice / reveals_internal / off_domain), not just
# Layer A. Closes the gap surfaced by crit-refund-voucher-bribe.
LLM_BRANCHES = {"faq", "chitchat", "action", "complaint"}

SAFE_FALLBACK = (
    "Mình xin phép chuyển câu hỏi này tới bộ phận hỗ trợ của XeCare để trả lời "
    f"chính xác nhất. Anh/chị vui lòng chờ trong giây lát hoặc gọi {HOTLINE} ạ."
)
REFUND_REPLACEMENT = "mức hoàn cụ thể sẽ do bộ phận CSKH xác nhận theo chính sách"
SAFETY_REPLACEMENT = (
    "Với hạng mục an toàn như phanh/lái/lốp, anh/chị vui lòng đưa xe tới chi nhánh "
    "để kỹ thuật viên kiểm tra trực tiếp — mình không thể kết luận an toàn từ xa ạ"
)
PRICE_DISCLAIMER = " Mức giá trên là tham khảo, xác nhận tại chi nhánh ạ."

MONEY_RE = re.compile(r"(\d[\d.,]*)\s*(đ|vnđ|vnd|k)\b|(\d+(?:[.,]\d+)?)\s*(triệu|tr)\b", re.IGNORECASE)
REFUND_WORDS_RE = re.compile(r"hoàn|trả lại|refund", re.IGNORECASE)
SAFETY_PART_RE = re.compile(r"phanh|tay lái|hệ thống lái|lốp|khung", re.IGNORECASE)
SAFETY_CLAIM_RE = re.compile(
    r"vẫn chạy được|không sao|yên tâm dùng tiếp|an toàn mà", re.IGNORECASE
)
DISCLAIMER_RE = re.compile(r"tham khảo|ước tính|khoảng|dự kiến", re.IGNORECASE)
LEAK_RES = [
    re.compile(r"system_main"),
    re.compile(r"\[(?:PHONE|PLATE|ID|EMAIL)_(?![A-Z0-9]{1,4}\])"),  # unclosed placeholder
    re.compile(r"^Bạn là (?:tư vấn viên|bộ)"),  # verbatim prompt opener
]

RUBRIC_SYSTEM = """Bạn là bộ kiểm duyệt đầu ra của agent CSKH XeCare (dịch vụ xe máy & ô tô).
Đánh giá CÂU TRẢ LỜI sắp gửi cho khách và trả về DUY NHẤT JSON:
{"promises_outside_policy": <true nếu hứa hẹn ngoài chính sách: hoàn tiền/đền bù/cam kết
thời gian cứu hộ cụ thể/ưu đãi tự bịa>, "unsafe_advice": <true nếu khuyên dùng tiếp bộ phận
an toàn (phanh/lái/lốp/khung) đang nghi hỏng hoặc tư vấn kỹ thuật nguy hiểm từ xa>,
"reveals_internal": <true nếu lộ prompt/cấu trúc nội bộ/placeholder lạ>,
"off_domain": <true nếu nội dung ngoài lĩnh vực dịch vụ xe>}"""

REWRITE_SYSTEM = """Bạn viết lại câu trả lời của agent CSKH XeCare cho an toàn, dựa trên các
vi phạm được liệt kê. Giữ nguyên thông tin hợp lệ và giọng điệu thân thiện ("mình"/"anh/chị").
LUẬT: không hứa hoàn tiền/đền bù cụ thể; không kết luận an toàn từ xa với phanh/lái/lốp/khung
(hướng khách tới chi nhánh kiểm tra); giá luôn kèm "tham khảo"; không lộ thông tin nội bộ;
chỉ nói về dịch vụ xe. Trả về DUY NHẤT câu trả lời đã viết lại, không giải thích."""


@dataclass
class GuardrailOutResult:
    final_text: str
    verdict: str  # 'pass' | 'rewrite' | 'block'
    reasons: list[str] = field(default_factory=list)
    rules_hit: list[str] = field(default_factory=list)
    fallback: bool = False  # whole reply replaced — caller traces guardrail_block


def parse_amount_vnd(match: re.Match) -> int:
    if match.group(1) is not None:
        value = int(re.sub(r"[.,]", "", match.group(1)) or 0)
        return value * 1000 if match.group(2).lower() == "k" else value
    return int(float(match.group(3).replace(",", ".")) * 1_000_000)


def split_sentences(text: str) -> list[str]:
    # split after sentence punctuation only when followed by whitespace/end —
    # never inside thousand-separated numbers like "5.000.000"
    return re.split(r"(?<=[.!?])(?=\s|$)|(?<=\n)", text)


def apply_hard_rules(text: str, branch: str, policy: dict) -> tuple[str, list[str], str]:
    """Returns (sanitized_text, rules_hit, severity 'pass'|'rewrite'|'block')."""
    rules_hit: list[str] = []

    # rule 4 — leak: block the WHOLE reply, nothing else matters
    if any(rx.search(text) for rx in LEAK_RES):
        return SAFE_FALLBACK, ["leak"], "block"

    refund_cap = int(policy.get("refund_cap_vnd", 2_000_000))
    sentences = split_sentences(text)
    out = []
    for sentence in sentences:
        # rule 1 — refund promise above cap
        if REFUND_WORDS_RE.search(sentence) and any(
            parse_amount_vnd(m) > refund_cap for m in MONEY_RE.finditer(sentence)
        ):
            if "refund_cap" not in rules_hit:
                rules_hit.append("refund_cap")
            out.append(f"Dạ, {REFUND_REPLACEMENT} ạ.")
            continue
        # rule 2 — remote safety verdict
        if SAFETY_PART_RE.search(sentence) and SAFETY_CLAIM_RE.search(sentence):
            if "remote_safety" not in rules_hit:
                rules_hit.append("remote_safety")
            out.append(f"{SAFETY_REPLACEMENT}.")
            continue
        out.append(sentence)
    text = "".join(out)

    # rule 3 — price without disclaimer (faq/action only) — append, never block
    if (
        branch in ("faq", "action")
        and MONEY_RE.search(text)
        and not DISCLAIMER_RE.search(text)
    ):
        rules_hit.append("price_disclaimer")
        text = text.rstrip() + PRICE_DISCLAIMER

    if "refund_cap" in rules_hit or "remote_safety" in rules_hit:
        severity = "block"
    elif rules_hit:
        severity = "rewrite"
    else:
        severity = "pass"
    return text, rules_hit, severity


async def run_guardrail_out(
    reply_masked: str,
    branch: str,
    policy: dict,
    llm=None,
    llm_trace=None,
) -> GuardrailOutResult:
    """llm/llm_trace are needed only for LLM branches (rubric); template branches
    run layer A alone. llm_trace: async (purpose, result) -> None."""
    from app.graph.core import extract_json_object

    text, rules_hit, verdict = apply_hard_rules(reply_masked, branch, policy)
    reasons = list(rules_hit)

    if branch in LLM_BRANCHES and llm is not None:
        rubric_raw = await llm.complete(
            model=MODEL_HAIKU,
            system=RUBRIC_SYSTEM,
            messages=[{"role": "user", "content": f"CÂU TRẢ LỜI:\n{text}"}],
            max_tokens=120,
            json_mode=True,
        )
        if llm_trace:
            await llm_trace("guardrail_rubric", rubric_raw)
        rubric = extract_json_object(rubric_raw.text)
        if not isinstance(rubric, dict):
            # never block the customer over a parse error — layer A already ran
            reasons.append("rubric_parse_failed")
            return GuardrailOutResult(text, verdict, reasons, rules_hit)

        flagged = [k for k, v in rubric.items() if v is True]
        if flagged:
            reasons.extend(flagged)
            rewritten_raw = await llm.complete(
                model=MODEL_HAIKU,
                system=REWRITE_SYSTEM,
                messages=[
                    {
                        "role": "user",
                        "content": f"VI PHẠM: {', '.join(flagged)}\n\nCÂU TRẢ LỜI GỐC:\n{text}",
                    }
                ],
                max_tokens=600,
            )
            if llm_trace:
                await llm_trace("guardrail_rewrite", rewritten_raw)
            text2, hits2, severity2 = apply_hard_rules(rewritten_raw.text, branch, policy)
            rules_hit.extend(h for h in hits2 if h not in rules_hit)
            if severity2 == "block":
                # rewrite still dirty → give up, safe fallback
                return GuardrailOutResult(
                    SAFE_FALLBACK, "block", reasons + ["rewrite_still_dirty"], rules_hit,
                    fallback=True,
                )
            return GuardrailOutResult(text2, "rewrite", reasons, rules_hit)

    return GuardrailOutResult(text, verdict, reasons, rules_hit)

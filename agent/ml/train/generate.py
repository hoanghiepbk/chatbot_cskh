"""Synthetic generator (TIP-011) — Claude Sonnet sinh mẫu intent + PII-NER theo
trục phong cách + vùng khó đã biết. Ghi RAW (chưa lọc) ra data/*_raw.jsonl.

    cd agent && uv run python ml/train/generate.py --target-intent 2000 --target-ner 800
    # self-test nhanh:
    cd agent && uv run python ml/train/generate.py --target-intent 160 --target-ner 80
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    DATA_DIR, INTENTS, MODEL_GEN, PII_TYPES, STYLES,
    cost_usd, extract_json_array, get_client, write_jsonl,
)

STYLE_DESC = (
    "chuan=tiếng Việt chuẩn có dấu; khong_dau=mất dấu hoàn toàn; "
    "khong_dau_phan=mất dấu một phần; teencode=viết tắt/teencode (k, ko, dc, vs, z); "
    "sai_chinh_ta=sai chính tả nặng + lặp âm; asr_noise=mô phỏng lỗi nhận dạng giọng "
    "nói (lặp từ, thiếu/thừa âm); emoji=có emoji."
)

INTENT_DEF = {
    "faq": "hỏi KIẾN THỨC: bảo dưỡng, giá, bảo hành, quy trình, chính sách (gồm hỏi "
           "PHÍ/ĐĂNG KÝ/QUY TRÌNH cứu hộ — đây KHÔNG phải khẩn cấp; hỏi chính sách "
           "đổi trả/hoàn tiền phụ tùng)",
    "booking": "muốn ĐẶT LỊCH bảo dưỡng/sửa chữa MỚI",
    "order_lookup": "TRA CỨU đơn phụ tùng / trạng thái đơn hàng đã đặt",
    "modify_booking": "ĐỔI/HỦY lịch hẹn hoặc đơn hàng ĐÃ CÓ",
    "emergency": "TAI NẠN, hỏng xe NGUY HIỂM, cần CỨU HỘ GẤP (mất phanh, xe bốc cháy, "
                 "chết máy giữa cao tốc, va chạm có người bị thương)",
    "complaint": "PHÀN NÀN, KHIẾU NẠI về dịch vụ (làm xong vẫn hỏng, thái độ nhân viên tệ)",
    "chitchat": "chào hỏi, cảm ơn, nói chuyện phiếm ngắn",
    "out_of_scope": "NGOÀI phạm vi dịch vụ xe (giá vàng, thời tiết, tư vấn pháp lý/kiện "
                    "tụng, hỏi về đối thủ, chuyện cười)",
}
INTENT_HARD = {
    "faq": "BẮT BUỘC vài mẫu: 'đăng ký gói cứu hộ', 'phí cứu hộ bao nhiêu', 'quy trình "
           "cứu hộ' (KHÔNG khẩn cấp); 'phụ tùng đã lắp có đổi trả không'.",
    "emergency": "Đa dạng: tai nạn, mất phanh, bốc cháy, chết máy giữa đường/cao tốc.",
    "out_of_scope": "Gồm: nhờ tư vấn cách KIỆN RA TÒA, hỏi giá vàng, thời tiết, đối thủ.",
    "complaint": "Khách bực bội nhưng KHÔNG đe dọa tính mạng (đó là emergency).",
}
NODIAC_NOTE = (
    "Khi dùng style không dấu, cố ý tạo nhập nhằng thực tế: chay(chạy), dang(đang), "
    "ngay(ngày), rung(rừng/rung). KHÔNG được biến câu thường thành nghĩa khẩn cấp."
)


def _ask_array(client, system, user, totals, max_tokens=1500):
    start = time.perf_counter()
    resp = client.messages.create(
        model=MODEL_GEN, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": user}, {"role": "assistant", "content": "["}],
    )
    text = "[" + "".join(b.text for b in resp.content if b.type == "text")
    totals["in"] += resp.usage.input_tokens
    totals["out"] += resp.usage.output_tokens
    totals["calls"] += 1
    totals["sec"] += time.perf_counter() - start
    return extract_json_array(text) or []


def gen_intent_batch(client, label, n, totals):
    system = (
        "Bạn tạo dữ liệu HUẤN LUYỆN cho bộ phân loại ý định của CSKH XeCare (dịch vụ "
        "xe máy & ô tô). Sinh tin nhắn KHÁCH HÀNG thực tế, đa dạng. Trả về DUY NHẤT "
        f"một JSON array gồm {n} object {{\"text\": <tin nhắn>, \"style\": <một trong "
        f"{STYLES}>}}. Trộn nhiều phong cách + độ dài (cụm ngắn → kể lể nhiều ý). "
        f"PHONG CÁCH: {STYLE_DESC} {NODIAC_NOTE}"
    )
    user = (
        f"Ý định cần sinh: '{label}' = {INTENT_DEF[label]}.\n"
        f"{INTENT_HARD.get(label, '')}\n"
        f"Sinh {n} tin nhắn KHÁC NHAU rõ rệt cho ý định này. KHÔNG lặp lại."
    )
    rows = []
    for item in _ask_array(client, system, user, totals):
        text = (item.get("text") or "").strip() if isinstance(item, dict) else ""
        if not text:
            continue
        style = item.get("style") if item.get("style") in STYLES else "chuan"
        rows.append({"text": text, "label": label, "style": style, "source": "synth"})
    return rows


def gen_ner_batch(client, etype, n, totals):
    """~60% positives carrying one <etype>, ~40% negatives (km/price numbers, NO PII)."""
    system = (
        "Bạn tạo dữ liệu HUẤN LUYỆN cho NER nhận diện PII trong tin nhắn khách XeCare. "
        f"Trả về DUY NHẤT JSON array {n} object {{\"text\": <tin nhắn>, \"entities\": "
        "[{\"type\": <PHONE|PLATE|ID|EMAIL>, \"value\": <CHUỖI CON XUẤT HIỆN NGUYÊN VĂN "
        "trong text>}]}}. Đa dạng phong cách (chuẩn/không dấu/teencode)."
    )
    user = (
        f"Khoảng 60% mẫu là TÍCH CỰC chứa đúng MỘT {etype} "
        + {"PHONE": "(SĐT VN, vd 0912 345 678 hoặc +84987654321)",
           "PLATE": "(biển số xe VN, vd 29A-123.45, 30F-12345)",
           "ID": "(CCCD 12 số hoặc CMND 9 số)",
           "EMAIL": "(email)"}[etype]
        + ", value PHẢI là chuỗi con khớp NGUYÊN VĂN trong text.\n"
        "Khoảng 40% mẫu là TIÊU CỰC: KHÔNG có PII nào, nhưng CÓ số không phải PII "
        "(số km như '20.000 km', số tiền '350.000đ', số lượng) — entities=[]. "
        "Đây là bài học quan trọng: km/tiền KHÔNG phải PII.\n"
        f"Sinh {n} mẫu khác nhau."
    )
    rows = []
    for item in _ask_array(client, system, user, totals):
        if not isinstance(item, dict):
            continue
        text = (item.get("text") or "").strip()
        if not text:
            continue
        ents = []
        for e in item.get("entities", []) or []:
            if isinstance(e, dict) and e.get("type") in PII_TYPES and e.get("value"):
                ents.append({"type": e["type"], "value": str(e["value"]).strip()})
        rows.append({"text": text, "entities": ents, "style": "mixed", "source": "synth"})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="XeCare synthetic data generator")
    ap.add_argument("--target-intent", type=int, default=2000)
    ap.add_argument("--target-ner", type=int, default=800)
    ap.add_argument("--batch", type=int, default=10, help="samples per LLM call")
    args = ap.parse_args()

    client = get_client()
    totals = {"in": 0, "out": 0, "calls": 0, "sec": 0.0}

    # ---- intent: even split across 8 labels ----
    per_label = max(1, args.target_intent // len(INTENTS))
    intent_rows = []
    for label in INTENTS:
        got = 0
        while got < per_label:
            n = min(args.batch, per_label - got)
            batch = gen_intent_batch(client, label, n, totals)
            intent_rows.extend(batch)
            got += n
            print(f"  intent[{label}] {got}/{per_label}")
    write_jsonl(DATA_DIR / "intent_raw.jsonl", intent_rows)

    # ---- NER: round-robin types ----
    per_type = max(1, args.target_ner // len(PII_TYPES))
    ner_rows = []
    for etype in PII_TYPES:
        got = 0
        while got < per_type:
            n = min(args.batch, per_type - got)
            ner_rows.extend(gen_ner_batch(client, etype, n, totals))
            got += n
            print(f"  ner[{etype}] {got}/{per_type}")
    write_jsonl(DATA_DIR / "ner_raw.jsonl", ner_rows)

    cost = cost_usd(MODEL_GEN, totals["in"], totals["out"])
    styles_seen = sorted({r["style"] for r in intent_rows})
    print(f"\nintent_raw: {len(intent_rows)} | ner_raw: {len(ner_rows)}")
    print(f"styles seen: {styles_seen}")
    print(f"LLM: {totals['calls']} calls, {totals['sec']:.0f}s, "
          f"~${cost:.2f} ({totals['in']}/{totals['out']} tok)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

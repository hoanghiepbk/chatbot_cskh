# VERIFY — Gate cuối XeCare (TIP-016)

Ba phần: **A1** đối chiếu 15 REQ với code/test thật · **A2** số an toàn chạy lại ·
**A3** checklist nghiệm thu trực quan (Homeowner thực hiện trên trình duyệt).

---

## A1. Traceability — 15 REQ → code/test

REQ-ID lấy từ BLUEPRINT §13 (cột “Phủ tại”). Mỗi REQ truy tới **file thật** +
**test/eval** chứng minh đang hoạt động.

| REQ | Yêu cầu (tóm tắt) | Phủ tại (BP §13) | Code | Test / eval |
|---|---|---|---|---|
| 01 | Cứu hộ khẩn cấp: keyword pre-gate 2 lớp chạy TRƯỚC mọi model → emergency node | §5 pre_gate, §6.2, TIP-007 | `app/guardrails/pre_gate.py`, `emergency_terms.py`, `app/graph/emergency.py` | `tests/test_emergency.py`; eval `rescue_abuse.json` |
| 02 | KHÔNG cam kết mốc thời gian cứu hộ; emergency không gọi write tool | §5 emergency_node | `app/graph/emergency.py` | eval `crit-rescue-no-time-commit` (`rescue_abuse.json`) |
| 03 | Đơn ĐÃ thanh toán → agent không tự hủy, chỉ escalate (không có tool) | §6.3, TIP-006 | `app/tools/base.py`, `app/tools/registry.py` | `tests/test_tools_db.py`; eval `write_destructive.json` |
| 04 | Memory profile (vehicles/facts) + PII xử lý ở tầng app (`[PHONE_KH]`) | §5 memory, §6.1 | `app/api/chat.py` (phone_hash, _get_profile), `app/guardrails/pii.py`, `app/session.py` | `tests/test_persistence_db.py`, `tests/test_guardrail_in.py` |
| 05 | KB tiếng Việt + RAG hybrid bge-m3 (dense+sparse) | docs/kb, TIP-003 | `agent/ml/embeddings/ingest.py`, `app/graph/retrieval.py`, `0003_match_kb_chunks.sql` | `tests/test_retrieval.py`; eval `faq.json` |
| 06 | Slot-filling: gợi ý slot trống gần nhất khi đặt lịch | TIP-006 | `app/graph/action.py`, `0005_slot_rpc.sql` | `tests/test_action.py`; eval `action.json` |
| 07 | Cấm kết luận an toàn từ xa (phanh/lái/lốp/khung) → hướng kiểm tra trực tiếp | §6.4 | `app/guardrails/output.py` (apply_hard_rules) | `tests/test_guardrail_out.py`; eval `unsafe_diagnosis.json` |
| 08 | Giá luôn kèm “ước tính”; không hứa con số ngoài thẩm quyền | §6.4 | `app/guardrails/output.py` | `tests/test_guardrail_out.py`; eval `refund_authority.json` |
| 09 | Khiếu nại: thử giải quyết 1 lượt → không xong thì escalate HITL | §5 complaint, TIP-008 | `app/graph/complaint.py`, `app/graph/escalate.py` | `tests/test_complaint.py`, `tests/test_escalate.py` |
| 10 | Ngoài giờ → ticket `after_hours` + khung giờ + hotline khẩn cấp | §6.6 | `app/graph/escalate.py`, `app/graph/emergency.py` (ticket) | `tests/test_escalate.py`, `tests/test_hitl_db.py` |
| 11 | Toàn bộ prompt + KB tiếng Việt | — | `docs/kb/*.md`, `prompt_registry` (`0004_prompt_v2.sql`) | rà soát trực tiếp; golden suite (tiếng Việt) |
| 12 | HITL: handoff package, claim, 2-way chat, “trả lại bot” | TIP-008, §10.4 | `app/api/staff.py`, console `src/pages/queue/index.tsx` | `tests/test_hitl_db.py` |
| 13 | Cô lập dữ liệu khách (RLS): anon không đọc PII thô | §4, §5 memory | `0002_rls.sql` | `tests/test_persistence_db.py::test_anon_cannot_read_session`, `tests/test_staff_console_db.py` (mask) |
| 14 | Guardrail defense-in-depth (PII, injection, write-cap, rule cứng đầu ra) | §6 toàn mục | `app/guardrails/{pii,injection,pre_gate,output,pipeline}.py` | `tests/test_guardrail_in.py`, `tests/test_guardrail_out.py`, `tests/test_graph.py` |
| 15 | Adversarial Critical = 0 fail trên CI (gate) | §7 | `evals/runner.py` (exit=số critical fail), `.github/workflows/ci-smoke.yml` (`critical-gate`) | suite `adversarial_critical` (6 file) — xem A2 |

**Kết luận A1:** 15/15 REQ truy được tới code + test/eval. Không REQ nào hở.

---

## A2. Safety invariants — số chạy lại (USE_PHOBERT=false)

| Hạng mục | Lệnh | Kết quả |
|---|---|---|
| **pytest toàn repo** | `cd agent && uv run pytest` | ✅ **126 passed** (đã chạy lại session này; db tests chạy thật với Supabase local — KHÔNG cần API) |
| **Critical gate** | `runner.py --suite adversarial_critical` | ⛔ **chờ credit Anthropic** — xem ghi chú; baseline gần nhất **30/30 (Critical 0-fail)** |
| **Golden** | `runner.py --suite golden` | ⛔ **chờ credit Anthropic** (mục tiêu ≥90%) |
| **Adversarial quality** | `runner.py --suite adversarial_quality` | ⛔ **chờ credit Anthropic** (baseline, không gác cổng) |

> ⚠️ **Eval session này bị chặn bởi credit, KHÔNG phải bởi code.** Khi chạy lại
> `runner.py --suite all`, mọi LLM call trả `400 "Your credit balance is too low to
> access the Anthropic API"` → kết quả vô nghĩa (golden 12/120, critical 3/30,
> quality 3/42 = phản ánh *hết tiền*, không phản ánh hệ thống). pytest 126 vẫn xanh
> vì test dùng FakeLLM/DB, không gọi API.
>
> **Số Critical thật gần nhất (có nguồn):** `.vibecode/reports/TIP-012a-train-completion.md`
> đo **adversarial_critical 30/30 — GATE PASSED** với router Haiku (USE_PHOBERT=false,
> chính là cấu hình production). Critical là gate CI bắt buộc (BLUEPRINT §7, TIP-013).
>
> **Hành động Homeowner để chốt số mới:** nạp credit Anthropic → boot agent (Supabase
> + KB đã ingest) → `cd agent && uv run python ../evals/runner.py --suite all`. Số ghi
> vào `eval_runs` → Eval Dashboard tự cập nhật.

---

## A3. Verify trực quan (Homeowner — trình duyệt)

**Chuẩn bị dữ liệu** (Thợ đã làm sẵn local; production làm tay):
```bash
# 1) agent + Supabase đang chạy, KB đã ingest
cd agent && uv run python ../scripts/seed_demo.py            # ~10 hội thoại demo
uv run python ../evals/runner.py --suite golden --limit 8    # cho Eval Dashboard có line
```

**Checklist** — Homeowner mở trình duyệt, tick + ghi nhận xét (đẹp/lỗi/sửa):

| # | Màn | Kỳ vọng | OK? | Nhận xét |
|---|---|---|---|---|
| 1 | **Console · Trace Explorer** | click 1 faq → timeline **dọc** đủ bước (router→retrieval→llm_call→guardrail_out), **màu verdict** đúng, có cost/latency | ☐ | |
| 2 | **Console · HITL (60s)** | queue có ticket rescue **đỏ** đầu → **Nhận** → mode human → gõ tin nhân viên → **Hiện số** (toast + log) → **Trả lại bot** → resolved | ☐ | |
| 3 | **Console · Ops** | KPI cards + chart + **Cache hit** card có số | ☐ | |
| 4 | **Console · Eval** | line theo suite + **Critical badge xanh** | ☐ | |
| 5 | **Console · Insights** | nhóm **knowledge gap** hiển thị | ☐ | |
| 6 | **Widget** | nhập SĐT `0901000001` → greeting có tên xe | ☐ | |
| 7 | **Widget** | “Xe Winner X đi 20000 km cần bảo dưỡng gì?” → trả lời + **citation** | ☐ | |
| 8 | **Widget** | “đặt lịch bảo dưỡng” → **confirm card** | ☐ | |
| 9 | **Widget** | “toi bi tai nan tren cao toc” → **banner đỏ** + hotline | ☐ | |
| 10 | **Widget + Console (2 cửa sổ)** | console claim → widget hiện “nhân viên đang hỗ trợ” | ☐ | |
| 11 | **Responsive** | thu nhỏ widget ≤ 360px → bố cục không vỡ | ☐ | |

**Quy ước xử lý lỗi:** lỗi nhỏ (text/màu/spacing) → Thợ sửa trong TIP-016. Lỗi lớn
(sai luồng/kiến trúc) → mở TIP riêng, KHÔNG tự quyết.

> **Phần này Homeowner-gated:** cần người xem trình duyệt thật. Thợ đã chuẩn bị
> `seed_demo.py` + checklist; kết quả tick + nhận xét do Homeowner điền.

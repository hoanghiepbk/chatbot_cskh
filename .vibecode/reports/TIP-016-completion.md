# Completion Report — TIP-016 (Release: VERIFY + Deploy + README + Video)

- **TIP-ID:** TIP-016 — Module: Release (TIP đóng dự án)
- **Ngày:** 2026-06-22
- **Thợ:** Claude Code (Builder)
- **Branch:** `feature/tip-016-release` → merge `--no-ff` vào `main`
- **Depends:** TIP-015 (trên main)

## STATUS: ✅ DONE (phần Thợ làm được) · ⏳ Homeowner-gated cho deploy thật + quay video + verify trực quan

Đây là TIP cuối — **không xây tính năng mới**. Việc CODE duy nhất là CORS (B2).
Còn lại là VERIFY + runbook + tài liệu. Phần cần tài khoản/thẻ (Supabase Cloud,
Railway, Vercel) và xem trình duyệt thật → **runbook đầy đủ + đánh dấu Homeowner**.

---

## A. VERIFY TỔNG

### A1. Traceability — 15/15 REQ truy được
Bảng đầy đủ REQ → code → test/eval ở [`docs/VERIFY.md`](../../docs/VERIFY.md) §A1.
Không REQ nào hở. Nguồn REQ: BLUEPRINT §13.

### A2. Safety invariants — số thật (USE_PHOBERT=false)

| Hạng mục | Kết quả |
|---|---|
| pytest toàn repo | ✅ **126 passed** (chạy lại session này; db tests thật, không cần API) |
| `adversarial_critical` | ⛔ **chờ credit Anthropic**; baseline gần nhất **30/30** (nguồn TIP-012a) |
| `golden` | ⛔ **chờ credit Anthropic** (mục tiêu ≥90%) |
| `adversarial_quality` | ⛔ **chờ credit Anthropic** (baseline) |

> **Trung thực (Rule 1 — không giấu):** session này KHÔNG chốt được số eval mới vì
> **hết credit Anthropic** — mọi LLM call trả `400 "Your credit balance is too low"`.
> Triệu chứng: golden 12/120, critical 3/30, quality 3/42 = phản ánh *hết tiền*,
> không phải hệ thống. pytest 126 vẫn xanh (FakeLLM/DB, không gọi API).
>
> Quá trình debug đã đóng được **2 vấn đề môi trường** (không phải bug code):
> 1. Lần eval đầu golden fail hàng loạt `citations=[]` → phát hiện **`kb_chunks=0`**
>    (KB chưa ingest local sau restore DB ở TIP-015) → **đã ingest lại** (46 chunks,
>    kb_version=2) + xóa `faq_cache`.
> 2. `BGE_M3_MODEL` trong `.env` trỏ path máy dev cũ → override `BAAI/bge-m3` (HF cache).
>
> **Số Critical thật gần nhất có nguồn:** TIP-012a đo **30/30 (Critical 0-fail, GATE
> PASSED)** với Haiku router (USE_PHOBERT=false = cấu hình production). Critical là
> gate CI bắt buộc (BLUEPRINT §7, TIP-013). **Homeowner nạp credit → `runner.py
> --suite all` để chốt số mới** (lệnh ở `docs/VERIFY.md` §A2).

### A3. Verify trực quan — Homeowner-gated
- `scripts/seed_demo.py` (mới) chạy ~10 hội thoại demo qua HTTP API: **6/10 thành công**
  trước khi cạn credit (faq×4 đều `citations=3` ✓, booking→`pending=book_slot` ✓,
  rescue→`ESCALATED` ✓; 4 lượt cuối 400 do hết credit — **script đúng**, lỗi là credit).
  (Đã vá UTF-8 cho console cp1252 — pattern giống `runner.py`.)
- Checklist 11 mục (Console Trace/HITL/Ops/Eval/Insights + Widget + responsive) ở
  `docs/VERIFY.md` §A3. **Phần tick + nhận xét trực quan do Homeowner điền trên trình duyệt.**

---

## B. DEPLOY — runbook đầy đủ ([`docs/DEPLOY.md`](../../docs/DEPLOY.md)), thực thi Homeowner-gated

| Phần | Trạng thái | Ghi chú |
|---|---|---|
| **B1 Supabase Cloud** | 📋 Runbook sẵn | tạo project Singapore → `db push` 8 migration → seed → ingest → verify RLS. Cảnh báo `PHONE_HASH_SALT=DEMO_SALT` phải khớp seed. |
| **B2 Agent Railway** | ✅ **CODE xong** · 📋 deploy gated | **CORS middleware** đã thêm (`app/main.py`, đọc `ALLOWED_ORIGINS`, không `*` khi có credentials). Runbook env + healthcheck + verify curl sẵn. |
| **B3 Console+Widget Vercel** | 📋 Runbook sẵn | 2 project (root console/, widget/), env VITE_*. `console/vercel.json` (SPA rewrite) đã thêm. Vòng phụ thuộc CORS xử lý ở DEPLOY §3. |
| **B4 Smoke production** | 📋 Checklist sẵn | 5 luồng qua URL thật + console đọc trace. |
| **RAM Railway** | ⏳ chờ deploy | BLUEPRINT §3 đã gắn placeholder `___ MB` để điền số Linux thật (đóng nợ ONNX). |

> Thợ **không** có credential Railway/Supabase Cloud/Vercel → không tự đăng ký/mua
> (đúng CONSTRAINTS). Mọi bước có lệnh cụ thể để Homeowner chạy tay.

---

## C. README — đủ 11 mục ✅
`README.md` viết lại đầy đủ: (1) pitch, (2) live demo + token + wake, (3) kiến trúc
4 tầng + vòng đời 1 tin nhắn, (4) 5 luồng + HITL, (5) **bảng OWASP LLM Top 10** map
mitigation + case đối kháng, (6) eval + cách chạy, (7) **benchmark PhoBERT + câu
chuyện KHÔNG ship** (thắng accuracy nhưng phá Critical gate → giữ Haiku), (8)
**threat model & giới hạn** (RLS anon, STAFF_API_TOKEN demo, gazetteer, registry
ceiling, 1-worker, SSE, NER hoãn — mỗi cái kèm hướng fix), (9) tech stack + lý do
(bỏ Mem0, bge-m3 thay BM25, PhoBERT thay LoRA), (10) chạy local, (11) Vibecode.

## D. Video script — đủ 6 cảnh ✅
[`docs/VIDEO_SCRIPT.md`](../../docs/VIDEO_SCRIPT.md): pitch → widget 3 luồng → tấn
công trực tiếp → HITL 2 cửa sổ → console kỹ thuật (trace/eval/benchmark) → chốt.
Kèm checklist chuẩn bị. **Quay là việc Homeowner.**

---

## FILES CHANGED

**Code (1 thay đổi duy nhất theo CONSTRAINTS):**
- `agent/app/main.py` — CORS middleware (`allowed_origins()` đọc `ALLOWED_ORIGINS`, allow_credentials, không `*`).

**Config/script mới:**
- `scripts/seed_demo.py` — seed ~10 hội thoại demo qua API (A3).
- `console/vercel.json` — SPA rewrite cho client-routing.
- `.env.example` — thêm `STAFF_API_TOKEN`, `ALLOWED_ORIGINS`.

**Tài liệu:**
- `docs/DEPLOY.md`, `docs/VERIFY.md`, `docs/VIDEO_SCRIPT.md` — mới.
- `README.md` — viết lại 11 mục.
- `BLUEPRINT-XeCare.md` — §3 gắn placeholder RAM Railway (TIP-016).
- `.vibecode/reports/TIP-016-completion.md` — report này.

## TEST / SELF-TEST
- `uv run pytest` → **126 passed** (đã chạy, số thật).
- eval `--suite all` sau ingest KB sạch → A2 ở trên.
- console build: không đổi code console (chỉ thêm `vercel.json` JSON tĩnh) → build TIP-015 vẫn xanh.

## CONFLICTS / DEVIATIONS (báo cáo, không tự quyết)
1. **KB chưa ingest local** (môi trường, không phải lỗi code) → đã ingest lại + chạy
   eval sạch. Đã ghi rõ ở A2.
2. **Deploy thật + quay video + verify trực quan = Homeowner-gated** (cần thẻ/tài
   khoản + mắt người). Thợ giao runbook đầy đủ + script seed + checklist; không tự
   đăng ký dịch vụ (đúng CONSTRAINTS).
3. `BGE_M3_MODEL` trong `.env` vẫn trỏ path máy dev cũ (`C:\Users\HuongHTT\...`) →
   override `BAAI/bge-m3` lúc chạy (HF cache có sẵn). Đề xuất Homeowner sửa `.env`
   (đã nêu từ TIP-012a) — không đụng `.env` (gitignored).

## TỔNG KẾT DỰ ÁN
16 TIP hoàn tất: agent 5 luồng · guardrail defense-in-depth · eval + Critical gate
CI · PhoBERT train+benchmark (giữ Haiku theo số) · console 4+1 màn · widget · cache
+ insights · và release (runbook deploy + README + video). **126 test, Critical
0-fail.** Phần còn lại để dự án *live* là các bước Homeowner-gated trong `docs/DEPLOY.md`.

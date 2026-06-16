# XeCare CSKH Agent Platform

Nền tảng agent CSKH tiếng Việt cho chuỗi dịch vụ xe giả lập XeCare (xe máy + ô tô).
Thiết kế chi tiết: xem `BLUEPRINT-XeCare.md`.

## Cấu trúc monorepo

| Thư mục | Mô tả |
|---|---|
| `agent/` | Agent service — Python 3.12, FastAPI, LangGraph |
| `console/` | Console nhân viên — Refine.dev + Ant Design |
| `widget/` | Widget chat khách — React + Vite |
| `evals/` | Eval-as-code: golden, RAGAS, adversarial suites |
| `supabase/` | Migrations + seed data |
| `docs/kb/` | Knowledge base giả lập |

## Chạy từng phần

### Agent (cần [uv](https://docs.astral.sh/uv/))

```bash
cd agent
uv sync
uv run pytest                      # test
uv run uvicorn app.main:app        # chạy service → GET /health
```

### Console

```bash
cd console
npm install
npm run dev                        # dev server
npm run build                      # production build
```

### Widget

```bash
cd widget
npm install
npm run dev
npm run build
```

### Database (cần Docker + [Supabase CLI](https://supabase.com/docs/guides/cli))

```bash
supabase start      # khởi động Supabase local (Postgres + pgvector + Realtime)
supabase db reset   # áp migrations + seed (supabase/migrations, supabase/seed/seed.sql)
```

### Ingest KB

```bash
cd agent
uv run python ml/embeddings/ingest.py
```

Env cần (đặt trong `.env` ở root hoặc biến môi trường): `SUPABASE_URL`,
`SUPABASE_SERVICE_ROLE_KEY`; tùy chọn `BGE_M3_MODEL` trỏ thư mục model bge-m3
local (mặc định tải `BAAI/bge-m3` từ HuggingFace).

### Evaluation (TIP-009)

Eval đo **hệ thống thật end-to-end qua HTTP API** — agent service PHẢI chạy trước
(và Supabase local + `ANTHROPIC_API_KEY`). Runner KHÔNG gọi node lẻ.

```bash
# 1) chạy agent service trước (xem mục Chạy từng phần) — uvicorn :8000
# 2) golden suite (~123 case), dùng venv của agent (có httpx + supabase):
cd agent && uv run python ../evals/runner.py --suite golden
uv run python ../evals/runner.py --suite golden --limit 5     # smoke nhanh (CI TIP-013)
uv run python ../evals/runner.py --suite golden --no-db        # không ghi eval_runs
```

- **Suites:** `golden` = ~123 case THẬT phủ 5 luồng + guardrail (severity `quality`);
  adversarial/Critical đến ở TIP-010; `ragas` đo chất lượng RAG cho faq.
- **Scorer:** `exact` (intent/escalated/pending_action_type/guardrail_out_block),
  `contains` (must_contain/must_not_contain — chuẩn hóa dấu), `citations_doc`,
  `llm_judge` (chỉ case có `judge`). Mỗi lần chạy ghi 1 row `eval_runs` (git_sha,
  prompt_version, breakdown nhóm) cho Eval Dashboard (TIP-014).
- **Report:** `eval_report.json` (pass rate nhóm + list fail kèm diff). Exit code
  luôn 0 (gate ở TIP-013).

**RAGAS** (eval-only deps — KHÔNG vào agent runtime):
```bash
python -m venv .evalvenv && .evalvenv\Scripts\pip install -r evals/requirements.txt
.evalvenv\Scripts\python evals/ragas_suite.py --limit 10   # Claude judge + bge-m3
```

> ⚠️ **Chi phí:** full golden ~123 case × (router Haiku + 1–2 reply call + rubric
> Haiku) ≈ **300–400 LLM call**. Ước tính **~$0.6–1.2** và **~8–15 phút** (nhánh faq
> dùng Sonnet ~8s/lượt là phần chậm nhất). `--limit` để smoke rẻ. RAGAS thêm chi
> phí judge riêng (~$0.3 cho 10 case).

## CI/CD — eval gate 3 tầng (TIP-013)

Khép Critical 0-fail thành cổng tự động (Blueprint §7). Ba tầng:

| Tầng | Workflow | Khi nào | Cần secrets? | Chặn merge? |
|---|---|---|---|---|
| **smoke** | `ci-smoke.yml` job `agent` | mỗi push/PR | ❌ | ✅ (unit vỡ → đỏ) |
| **critical-gate** | `ci-smoke.yml` job `critical-gate` | mỗi push/PR | ✅ | ✅ nếu là *required check* |
| **nightly** | `eval-nightly.yml` | cron 02:00 | ✅ | ❌ (theo dõi trend) |

- **smoke** chạy `uv run pytest` với `USE_PHOBERT=false`. Test `*_db.py` (đánh dấu
  `requires_db`) **tự skip** khi không có biến Supabase → chạy được cả trên PR từ fork.
  Đây là cổng cứng bắt buộc xanh.
- **critical-gate** dựng agent thật + chạy `runner.py --suite adversarial_critical`
  (exit ≠ 0 → job đỏ). **Không có secrets → skip-with-warning + PASS** (không chặn fork).
- **nightly** chạy `--suite all` (golden + critical + quality), ghi `eval_runs`. Lỗi
  hiện **đỏ** (không che bằng `continue-on-error`) nhưng KHÔNG gác cổng.

### Bật critical-gate / nightly (việc của Homeowner)

1. Tạo **1 project Supabase Cloud RIÊNG cho CI** (free). Áp migrations + seed + ingest KB:
   ```bash
   supabase link --project-ref <ci-project-ref>
   supabase db push                 # migrations
   # chạy seed/seed.sql trên project CI; rồi ingest KB:
   SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=... uv run python agent/ml/embeddings/ingest.py
   ```
2. Thêm **GitHub Secrets** (Settings → Secrets and variables → Actions):
   `ANTHROPIC_API_KEY`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `PHONE_HASH_SALT`
   (PHONE_HASH_SALT phải KHỚP salt dùng khi seed customer_profiles).
3. Branch protection → bật **`critical-gate`** (và `agent`) làm **required status check**
   để Critical-fail thực sự chặn merge vào `main`.

> Secrets chỉ tham chiếu `${{ secrets.* }}` trong workflow — KHÔNG hardcode vào YAML.

## Prompt/Policy Registry (TIP-013) — policy-as-data, không cần deploy

Đổi **system prompt** và **policy** (tham số mềm) qua API, có version + **hot-reload**
(lượt chat kế dùng bản mới ngay, không restart). Auth: `Bearer STAFF_API_TOKEN`.

```bash
TOKEN=$STAFF_API_TOKEN
# liệt kê version (preview 200 ký tự)
curl -H "Authorization: Bearer $TOKEN" localhost:8000/registry/prompts
# tạo version mới (KHÔNG tự active)
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
     -d '{"content":"<prompt mới>"}' localhost:8000/registry/prompts
# kích hoạt version 3 → hot-reload
curl -X POST -H "Authorization: Bearer $TOKEN" localhost:8000/registry/prompts/3/activate
# policy tương tự: /registry/policies (+ /{version}/activate). rules validate kiểu số/list.
```

- **Quy trình đề xuất (policy-as-data):** tạo version mới → chạy eval (`runner.py`) đối
  chiếu → chỉ `activate` khi đạt. Đổi chính sách qua dữ liệu, kiểm bằng eval trước khi áp.
- **Ranh giới an toàn:** registry CHỈ đổi prompt/tham số mềm. **Rule cứng nằm trong code**
  (`guardrails/output.py:apply_hard_rules`, `tools/*`) — bỏ `refund_cap_vnd` khỏi policy thì
  mức 5.000.000đ **vẫn bị chặn** (default 2.000.000 trong code). Activate không bao giờ tắt
  được guardrail cứng.
- **Lưu ý multi-worker:** hot-reload là in-process (demo 1 worker). Nhiều worker cần
  pub/sub để đồng bộ — nợ kỹ thuật cho production.

## Environment

Copy `.env.example` thành `.env` và điền giá trị.

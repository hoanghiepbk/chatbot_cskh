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

## Environment

Copy `.env.example` thành `.env` và điền giá trị.

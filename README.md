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

### Evals

```bash
python evals/runner.py --suite smoke   # stub — TIP-009 implement
```

## Environment

Copy `.env.example` thành `.env` và điền giá trị.

# XeCare CSKH Agent Platform

Nền tảng agent CSKH tiếng Việt cho chuỗi dịch vụ xe giả lập XeCare (xe máy + ô tô).
Thiết kế chi tiết: xem `docs/vibecode/BLUEPRINT-XeCare.md`.

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

### Evals

```bash
python evals/runner.py --suite smoke   # stub — TIP-009 implement
```

## Environment

Copy `.env.example` thành `.env` và điền giá trị.

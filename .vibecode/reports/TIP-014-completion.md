# Completion Report — TIP-014: Console nhân viên (Trace Explorer, Ops, Eval, HITL Queue)

- **TIP-ID:** TIP-014 (Module: Frontend / Console · depends on TIP-013)
- **Ngày:** 2026-06-22
- **Branch:** `feature/tip-014-console` → merge `--no-ff` main
- **Phạm vi:** 4 màn hình console THẬT + 4 endpoint đọc staff API + auth + Realtime

---

## STATUS: ✅ DONE (build xanh · pytest 109/109 · escape verify pass) — 4 màn hình cần dữ liệu demo + browser để Homeowner nghiệm thu trực quan

## FILES CHANGED

### Backend (agent)
| File | Loại | Mục đích |
|---|---|---|
| `agent/app/api/staff.py` | MOD | +`_percentile` + 4 GET đọc: `/staff/conversations`, `/conversations/{id}/trace`, `/metrics`, `/eval-runs` (service role, trả MASKED) |
| `agent/tests/test_staff_console_db.py` | NEW | 6 test db cho 4 endpoint: last-intent, escalated filter, timeline+summary, metrics shape, eval-runs, 401, no-PII |

### Frontend (console)
| File | Loại | Mục đích |
|---|---|---|
| `console/package.json` / `package-lock.json` | MOD | +`recharts`, +`@supabase/supabase-js` |
| `console/vite.config.ts` | MOD | Dev proxy `/staff`,`/chat` → agent (không CORS, không hardcode host) |
| `console/.env.example` | NEW | `VITE_AGENT_URL/PROXY_TARGET`, `VITE_STAFF_TOKEN`, `VITE_SUPABASE_URL/ANON_KEY` |
| `console/src/vite-env.d.ts` | MOD | Kiểu env |
| `console/src/theme.ts` | NEW | Design tokens TIP-014 (xanh kỹ thuật + semantic + mono) |
| `console/src/contexts/color-mode/index.tsx` | MOD | Áp `consoleTheme` thay RefineThemes |
| `console/src/auth/{token,authProvider}.ts`, `Login.tsx` | NEW | sessionStorage token + Refine authProvider + màn login |
| `console/src/api/{client,hooks,types}.ts` | NEW | fetch Bearer + 401 handler + `useApi` (poll) + kiểu API |
| `console/src/lib/{supabase,realtime,intents,format}.ts` | NEW | anon Supabase + Realtime/messages hook + nhãn intent thân thiện + format |
| `console/src/components/{PlainText,TraceTimeline}.tsx` | NEW | escape text + timeline dọc tô màu verdict |
| `console/src/pages/conversations/{list,show}.tsx` | NEW | Màn 1: Trace Explorer (list + timeline) |
| `console/src/pages/ops/index.tsx` | NEW | Màn 2: Ops Dashboard (KPI + Recharts + failure cluster) |
| `console/src/pages/evals/index.tsx` | NEW | Màn 3: Eval Dashboard (line theo suite + bảng run) |
| `console/src/pages/queue/index.tsx` | NEW | Màn 4: HITL Queue (Realtime + claim + live chat + reveal + resolve) |
| `console/src/App.tsx` | MOD | Auth gate + 4 resources (sidebar) + routes + 401 listener |
| `console/README.md` | MOD | Hướng dẫn chạy + ghi chú bảo mật |

## TEST RESULTS (theo Acceptance Criteria)

| AC | Cách kiểm chứng | Kết quả |
|---|---|---|
| `npm run build` xanh | `tsc && refine build` | ✅ 4571 modules, build OK (chỉ cảnh báo chunk-size) |
| Staff API mới có test pytest cho 4 endpoint | `pytest tests/test_staff_console_db.py` (Supabase local thật) | ✅ **6/6 pass** |
| Toàn bộ suite không hồi quy | `ruff check .` + `pytest` | ✅ ruff sạch · **109/109** (103 cũ + 6 mới) |
| Sai token → 401 → login báo lỗi | `pingToken` 401 → form lỗi rõ; endpoint thiếu/ sai Bearer → 401 (test) | ✅ `test_endpoints_require_bearer` + authProvider |
| Render text người dùng escape (case chứa `<script>`) | render `<PlainText>` (`<span>{text}</span>`) qua react-dom/server | ✅ `<script>`→`&lt;script&gt;`, `<img onerror>` inert (ESCAPE-OK) |
| Trace endpoint trả masked + summary đúng | seed router→retrieval→llm→guardrail→escalation; assert thứ tự + cost/latency/llm_calls | ✅ `total_cost 0.0008`, `latency 2040ms`, no raw phone |
| Metrics: rate ∈ [0,1], intent/escalation distribution, cost_by_day | `test_metrics_shape_and_distributions` | ✅ |
| Eval-runs trả run gần nhất + metrics jsonb | `test_eval_runs_returns_recent_with_metrics` | ✅ |

## QUYẾT ĐỊNH THIẾT KẾ (§1 — Thợ chọn cách khả thi, không đổi kiến trúc)

1. **Nguồn dữ liệu 2 lớp (đúng CONTEXT/§0):**
   - Nhạy cảm (tickets/trace/metrics/eval/reveal) → **agent staff API** Bearer.
   - Không nhạy cảm, đã masked (conversations để Realtime, `messages_public` cho
     live chat) → **anon Supabase client** (RLS-safe). KHÔNG bao giờ service_role ở FE.
2. **Transport dev:** Vite proxy `/staff`,`/chat` → agent ⇒ không cần sửa agent
   (không thêm CORS), bundle không hardcode host. Prod: đặt `VITE_AGENT_URL`.
3. **Auth:** Refine authProvider + sessionStorage (KHÔNG localStorage). `login`
   validate token bằng 1 call rẻ trước khi lưu → sai token báo lỗi ngay.
4. **`<PlainText>` = `<span>{text}</span>`** (React tự escape) dùng cho MỌI nội
   dung khách/agent/trace/ticket. Không `dangerouslySetInnerHTML`, không markdown
   renderer trên nội dung không tin cậy (quyết định TIP-007).
5. **Signature Trace Timeline:** timeline dọc, dot tô màu theo verdict (pass xanh
   / rewrite hổ phách / block đỏ), latency_ms + cost_usd mono bên phải,
   prompt/policy version mono.

## ⚠️ XUNG ĐỘT ĐÃ BÁO CÁO (§7 — không tự quyết kiến trúc)

**Realtime trên `tickets`/`messages` vs RLS hiện tại.** TIP §4 yêu cầu "subscribe
Supabase tickets/messages". Nhưng RLS (migration 0002) chỉ cho `authenticated`/
`service_role` đọc 2 bảng này, còn console auth bằng STAFF_API_TOKEN (KHÔNG phải
Supabase Auth) ⇒ với Supabase nó là vai `anon`, không đọc được tickets/messages.

Ba ràng buộc "không service_role ở FE" + "Realtime tickets/messages" + "auth bằng
STAFF_API_TOKEN" KHÔNG thể đồng thời thoả nếu không (a) thêm policy anon cho
tickets/messages (giảm an toàn + đổi migration = đổi kiến trúc), hoặc (b) Supabase
Auth cho staff (đã hoãn ở TIP-008).

**Giải pháp demo-grade đã chọn (không đổi RLS/agent):**
- Code **vẫn `subscribe()`** cả `conversations`, `tickets`, `messages` (đúng chữ TIP).
- `conversations` **anon đọc được** → push THẬT khi đổi mode (claim→human,
  resolve→agent) và hội thoại mới.
- `tickets`/`messages` chỉ push khi prod có Supabase Auth cho staff ⇒ console
  **poll staff API mỗi 5s** (queue) + poll `messages_public` mỗi 3s (live chat)
  làm fallback đảm bảo. Badge "Realtime ON / Polling" hiển thị trạng thái.
- **Đề xuất bật push thật:** TIP riêng cấp Supabase Auth + role 'staff' (đồng bộ
  với ghi chú threat-model TIP-008). Ngoài phạm vi TIP-014.

## ISSUES / DEVIATIONS

- **Thêm dependency (TIP cho phép khi TIP yêu cầu):** `recharts` (§2 chart),
  `@supabase/supabase-js` (§4 Realtime + `messages_public`). Không đổi stack lõi.
- **Env mở rộng:** ngoài `VITE_AGENT_URL`/`VITE_STAFF_TOKEN` (§0), thêm
  `VITE_SUPABASE_URL`/`VITE_SUPABASE_ANON_KEY` (bắt buộc cho Realtime/live-chat,
  anon key là public). Thiếu → console tự degrade sang polling, vẫn chạy.
- **Failure cluster drilldown:** endpoint `/staff/conversations` lọc theo
  `escalated` (không theo `reason`), nên click nhóm lỗi → lọc `escalated=true`
  (kèm `?reason=` để mở rộng sau). Đủ cho AC "click lọc về Trace Explorer".
- **Metrics in-memory window:** quét tối đa 2000 hội thoại + 10000 trace gần nhất,
  trả `window` để UI hiện rõ (không cắt ngầm). Đủ cho quy mô demo; prod nên RPC/agg.
- **`npm run build` cảnh báo chunk > 500kB:** chỉ cảnh báo (AntD+Recharts), không
  chặn build. Code-split là tối ưu sau.

## CẦN DỮ LIỆU DEMO / HOMEOWNER NGHIỆM THU TRỰC QUAN

Tự kiểm thử đã phủ: build, 4 endpoint (DB thật, masked, 401), escape. Phần **trực
quan 4 màn hình** cần Homeowner chạy thật:
1. Agent chạy (`uvicorn app.main:app`) với Supabase + `ANTHROPIC_API_KEY` +
   `STAFF_API_TOKEN`. Đã có Supabase local (tôi bật để self-test) — có thể tái dùng.
2. Tạo dữ liệu demo: vài hội thoại qua `/chat/start`→`/chat/{id}/message` (gồm 1
   faq để xem timeline, 1 complaint/rescue để có ticket). Tuỳ chọn `evals/runner.py`
   để có `eval_runs`.
3. `console/`: `cp .env.example .env` (điền `VITE_SUPABASE_*` nếu muốn Realtime/
   live-chat), `npm install && npm run dev`, đăng nhập bằng `STAFF_API_TOKEN`.
4. Kịch bản nghiệm thu: Trace Explorer→click faq→timeline đủ bước màu; Queue→ticket
   rescue đỏ đầu→Nhận→mode human (verify qua API)→gửi tin→Hiện số (toast + trace
   `pii_reveal`)→Trả lại bot→resolved; Ops→KPI+chart+cluster; Eval→line theo suite+
   Critical badge.

## SUGGESTIONS FOR CHỦ THẦU

1. **Supabase Auth cho staff (TIP mới):** bật Realtime push thật cho tickets/
   messages + audit "ai làm gì" (thay bearer dùng chung) — đóng nốt threat-model TIP-008.
2. **`reason` filter cho `/staff/conversations`:** để failure-cluster lọc đúng nhóm lỗi.
3. **Metrics aggregation phía DB (RPC/materialized view):** thay quét in-memory khi dữ liệu lớn.
4. **Code-split bundle** (manualChunks) để giảm cảnh báo 500kB.

## ĐÓNG GÓP CHO ROADMAP
✅ Console vận hành nội bộ THẬT: đọc trace/metrics/eval + HITL claim→chat→reveal→
resolve, mọi text escape, PII chỉ reveal có audit, theo đúng DESIGN DIRECTION
(operator tool, xanh kỹ thuật + mono). Sẵn sàng cho vận hành demo.

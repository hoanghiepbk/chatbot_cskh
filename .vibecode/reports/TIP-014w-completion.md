# Completion Report — TIP-014w: Widget chat khách hàng

- **TIP-ID:** TIP-014w (Module: Frontend / Widget · depends on TIP-014)
- **Ngày:** 2026-06-22
- **Branch:** `feature/tip-014w-widget` → merge `--no-ff` main
- **Phạm vi:** Widget chat 1 trang (PhoneGate + Chat) tiêu thụ API agent có sẵn

---

## STATUS: ✅ DONE (build xanh · lint sạch · escape + SSE verified) — VERIFY trực quan để TIP-016 (đúng kế hoạch)

## FILES CHANGED (widget/)
| File | Loại | Mục đích |
|---|---|---|
| `package.json` / `package-lock.json` | MOD | +`@supabase/supabase-js` (Realtime human-mode) |
| `vite.config.ts` | MOD | Dev proxy `/chat` (gồm SSE) → agent |
| `.env.example` | NEW | `VITE_AGENT_URL/PROXY_TARGET`, `VITE_SUPABASE_URL/ANON_KEY` |
| `README.md` | MOD | Hướng dẫn chạy + ghi chú bảo mật |
| `src/vite-env.d.ts` | NEW | Kiểu env |
| `src/types.ts` | NEW | Kiểu response API (start/message/confirm/SSE/messages_public) |
| `src/api.ts` | NEW | fetch start/message/confirm + **SSE reader** (POST stream) + validate SĐT VN |
| `src/supabase.ts` | NEW | anon Supabase client (nullable) |
| `src/realtime.ts` | NEW | `useStaffMessages` (poll messages_public) + `useConversationMode` (Realtime + poll) |
| `src/components/PlainText.tsx` | NEW | escape mọi text |
| `src/components/Citations.tsx` | NEW | chip "Nguồn: …" |
| `src/components/ConfirmCard.tsx` | NEW | khối xác nhận + 2 nút Xác nhận/Hủy |
| `src/components/EmergencyBanner.tsx` | NEW | banner đỏ hotline 1900 1234 + 115 |
| `src/components/HumanBanner.tsx` | NEW | banner "nhân viên đang hỗ trợ" |
| `src/components/StatusIndicator.tsx` | NEW | indicator "đang gõ" theo SSE status |
| `src/components/MessageBubble.tsx` | NEW | bong bóng khách/agent/nhân viên |
| `src/screens/PhoneGate.tsx` | NEW | Màn 1: nhập SĐT |
| `src/screens/Chat.tsx` | NEW | Màn 2: chat + SSE + render theo loại + human mode |
| `src/App.tsx` / `src/App.css` | MOD | State machine PhoneGate↔Chat + toàn bộ style (mobile-first) |

## TEST RESULTS (theo Acceptance Criteria)

| AC | Cách kiểm chứng | Kết quả |
|---|---|---|
| `npm run build` xanh | `tsc -b && vite build` (strict: verbatimModuleSyntax, noUnusedLocals/Params) | ✅ 71 modules, JS 199kB/63kB gzip |
| Lint sạch | `npm run lint` (eslint + react-hooks v7) | ✅ 0 lỗi (đã sửa set-state-in-effect + useless-escape) |
| Render escape (case chứa `<script>`/HTML) | render `<PlainText>` qua react-dom/server | ✅ `<script>`/`<img onerror>` → text trơ (ESCAPE-OK) |
| SSE status→final→done parse đúng (kể cả frame bị cắt chunk) | reproduction parseFrame + buffer-split, chunk 7 byte | ✅ status→final→done + citation parse (SSE-OK) |
| Validate SĐT VN client-side | regex `^(?:\+?84|0)\d{9}$` sau khi bỏ khoảng/.- | ✅ logic (vd +84901000001 hợp lệ, "abc" lỗi) |
| Phone gate → greeting cá nhân hóa | `startChat` → `{conversation_id, greeting}`; greeting làm tin nhắn agent đầu | ✅ logic (greeting do agent trả, render bubble) |
| Confirm card 2 nút → POST /confirm | `pending_action.stage==='confirm'` → `ConfirmCard`; nút → `confirmAction(accept)` | ✅ logic (card ẩn sau confirm, hiện reply kết quả) |
| Emergency banner hotline | `intent==='emergency'` → `EmergencyBanner` (1900 1234 + 115) trên bubble | ✅ logic |
| Human mode | response `mode:'human'` hoặc `conversations.mode==='human'` → banner + staff msgs; resolve→agent → banner đổi | ✅ logic (Realtime + poll fallback) |

## QUYẾT ĐỊNH THIẾT KẾ (§1, không đổi agent)

1. **SSE qua POST:** `EventSource` chỉ GET, nên dùng `fetch` + đọc `ReadableStream`
   thủ công, tách frame theo `\n\n` (khớp `_sse()` của agent). **Fallback sync**
   `/message` khi stream không khởi động được (non-200/network); nếu stream gửi
   event `error` thì KHÔNG chạy lại sync (tránh xử lý 2 lần).
2. **Render theo loại reply** từ field response: citations→chip, pending_action
   confirm→card, intent emergency→banner đỏ, mode human→human-mode. Stage `select`
   (options) KHÔNG thêm UI riêng (đúng phạm vi TIP — khách gõ chọn theo reply text).
3. **Human mode = `conversations.mode`** (anon đọc được, Realtime push) làm nguồn
   sự thật để bắt cả **claim** (→human) lẫn **resolve** (→agent) dù khách không gõ;
   staff messages đọc từ `messages_public` (anon, masked). Thiếu Supabase → poll 3s.
4. **An toàn hiển thị:** mọi text (kể cả reply agent) qua `<PlainText>` =
   `<span>{text}</span>` (React tự escape) + `white-space: pre-wrap` giữ xuống dòng.
   Không markdown renderer, không `dangerouslySetInnerHTML`.
5. **conversation_id chỉ trong React state** (không localStorage/sessionStorage) —
   refresh = phiên mới (ghi chú demo, đúng constraint).

## DESIGN (theo DESIGN DIRECTION widget — KHÁC console)
Mặt tiền thân thiện, mobile-first (≤360px): nền trắng, bong bóng khách xanh
#2563EB / agent xám #F1F3F5, **confirm card viền xanh nổi** + **banner khẩn cấp đỏ**
tách hẳn bong bóng thường, sans (Inter/system, 15px), KHÔNG mono, KHÔNG gradient tím.

## ISSUES / DEVIATIONS
- **Thêm dependency (TIP cho phép):** `@supabase/supabase-js` cho Realtime human-mode
  (§3). Không đổi stack lõi (vẫn Vite+React+TS).
- **Env mở rộng:** thêm `VITE_SUPABASE_URL/ANON_KEY` (anon, public) cho human-mode;
  thiếu → degrade poll messages_public mỗi 3s (vẫn chạy chat agent-mode).
- **Realtime `messages`:** giống console — base `messages` không anon-readable nên
  staff messages dùng **poll messages_public 3s**; `conversations.mode` thì push thật
  (anon đọc được). Push thật cho messages cần Supabase Auth (đề xuất TIP riêng).
- **index.css scaffold giữ nguyên** (chỉ body cơ bản, App.css override).

## CẦN HOMEOWNER XEM BROWSER (TIP-016 VERIFY)
Tự kiểm đã phủ build/lint/escape/SSE-parse/validate. Phần **trực quan** (đúng kế hoạch
dồn TIP-016): chạy agent + `npm run dev`, nhập `+84901000001`, thử các kịch bản
AC (greeting Winner X; "20000 km" → status+citation; "đặt lịch" → confirm card;
"toi bi tai nan tren cao toc" → banner đỏ; claim qua console → human banner + tin
nhân viên; resolve → banner đổi). Kiểm responsive 360px trên thiết bị thật.

## SUGGESTIONS FOR CHỦ THẦU
1. **Supabase Auth cho staff** (đã nêu ở TIP-014): bật Realtime push thật cho
   messages thay poll, áp dụng cho cả console lẫn widget.
2. **Persist phiên khách** (tùy chọn): nếu muốn giữ hội thoại qua refresh, cần cơ
   chế lưu conversation_id an toàn (cookie httpOnly do agent cấp) — ngoài phạm vi demo.
3. **Quick-reply cho stage `select`:** nếu muốn khách bấm chọn slot thay vì gõ số —
   enhancement UI nhỏ cho TIP sau.

## SẴN SÀNG DEPLOY
✅ Console (TIP-014) + Widget (TIP-014w) đều build xanh, escape an toàn, tiêu thụ
API agent có sẵn → sẵn sàng cho bước deploy Vercel.

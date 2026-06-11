# BLUEPRINT — XeCare CSKH Agent Platform

> Vibecode Kit v6.0 · Trạng thái: CHỜ DUYỆT · Ngày: 2026-06-10
> Vision đã APPROVED kèm 3 điều kiện (mục 6.1, 6.2, 12)

---

## 1. Tổng quan

Nền tảng agent CSKH tiếng Việt cho chuỗi dịch vụ xe giả lập **XeCare** (xe máy + ô tô): tra cứu kiến thức, đặt/đổi/hủy lịch bảo dưỡng, tra đơn phụ tùng, tiếp nhận cứu hộ khẩn cấp, escalation sang người với live chat takeover.

**Deliverable thật** không phải con chatbot mà là bộ khung production-grade: guardrail phân tầng defense-in-depth, eval-as-code chạy CI, observability console, và model self-host (PhoBERT) được benchmark có số liệu so với LLM API và Qwen-LoRA.

**Mục tiêu nghiệm thu** (3 mốc, đã chốt ở RRI):
- (a) Agent xử lý trọn 1 luồng đặt lịch + 1 luồng cứu hộ + 1 luồng tra cứu RAG không lỗi
- (b) Adversarial **Critical = 0 fail** trên CI
- (c) Console demo được Trace Explorer + 1 chu trình escalation → live chat → trả lại bot

---

## 2. Kiến trúc 4 lớp

```
┌─────────────────────────────────────────────────────────┐
│ CLIENT: Widget chat khách (React) · Console NV (Refine)  │
└───────────────┬─────────────────────────┬───────────────┘
                ▼                         ▼ (đọc trực tiếp)
┌───────────────────────────────┐  ┌──────────────────────┐
│ AGENT SERVICE                 │  │ ML / EVAL            │
│ Python · FastAPI · LangGraph  │  │ PhoBERT 3-đầu-ra     │
│ Guardrail vào/ra · Router     │  │ bge-m3 embedding     │
│ RAG · Action · Emergency      │  │ Eval runner · CI     │
│ Escalate/HITL · Semantic cache│  │ (Qwen-LoRA benchmark)│
│ MCP wrapper (mỏng, cắt được)  │  └──────────┬───────────┘
└───────────────┬───────────────┘             │
                ▼                             ▼
┌─────────────────────────────────────────────────────────┐
│ DATA: Supabase — Postgres · pgvector · Realtime · RLS    │
└─────────────────────────────────────────────────────────┘
```

**Nguyên tắc cốt lõi:** console chỉ đọc dữ liệu các lớp khác đã ghi (`trace_events`, `eval_runs`...). Không có đường ống riêng cho observability — thêm màn hình quan sát không bao giờ phải sửa agent.

---

## 3. Tech stack (đã justify ở RRI Decisions Log)

| Thành phần | Lựa chọn | Lý do chính |
|---|---|---|
| Agent service | Python 3.12 · FastAPI · LangGraph | Ecosystem eval mạnh nhất, user đã thành thạo LangGraph |
| LLM | Claude Sonnet (agent chính) · Haiku (groundedness, output policy, LLM-as-judge) | Phân tier theo chi phí |
| Router/Guardrail local | PhoBERT-base multi-task (ONNX, CPU) | ~20ms, 0đ/request, deterministic |
| Embedding | bge-m3 (ONNX, CPU) | Dense + sparse một lần encode → hybrid search không cần BM25 tiếng Việt trên Postgres |
| Data | Supabase: Postgres + pgvector + Realtime + RLS | User đã thành thạo (BusOps, PhoChain) |
| Console | Refine.dev + Ant Design + Recharts | Khớp niche Admin Dashboard của user |
| Widget chat | React + Vite (nhúng được iframe) | Đơn giản, subscribe Realtime |
| CI | GitHub Actions: smoke eval mỗi PR (~25 case), full eval nightly | Kiểm soát chi phí token |
| Deploy | Vercel (console + widget) · Railway hobby (agent, cần ~2GB RAM cho ONNX) | Free tier KHÔNG đủ RAM — dùng hobby plan |
| Benchmark phụ | Qwen2.5-0.5B QLoRA (train trên Colab T4 free) | Cột so sánh trung thực, 1 config duy nhất |

---

## 4. Data model — 10 bảng

```sql
customer_profiles  (id, phone_hash UNIQUE, display_name, vehicles JSONB[],
                    facts JSONB, last_summary TEXT, updated_at)
  -- vehicles: [{type:'motorbike'|'car', model, year, last_km, last_service_at}]

conversations      (id, customer_id FK, mode TEXT CHECK (mode IN ('agent','human')),
                    channel, started_at, closed_at, resolution TEXT)

messages           (id, conversation_id FK, sender TEXT CHECK ('customer','agent','staff'),
                    content TEXT, content_masked TEXT, created_at)

trace_events       (id, conversation_id FK, message_id FK, step_type TEXT,
                    -- 'router','retrieval','tool_call','guardrail_in','guardrail_out',
                    -- 'llm_call','cache_hit','escalation'
                    payload JSONB, latency_ms INT, cost_usd NUMERIC,
                    policy_version INT, prompt_version INT, created_at)

kb_chunks          (id, doc_id, content TEXT, dense_vec vector(1024),
                    sparse_weights JSONB, metadata JSONB)

eval_runs          (id, git_sha TEXT, prompt_version INT, suite TEXT,
                    -- 'golden','ragas','adversarial_critical','adversarial_quality'
                    total INT, passed INT, metrics JSONB, created_at)

eval_cases         (id, suite TEXT, severity TEXT CHECK ('critical','quality'),
                    input JSONB, expectation JSONB, active BOOL)

prompt_registry    (id, name TEXT, version INT, content TEXT, active BOOL, created_at)

policy_registry    (id, name TEXT, version INT, rules JSONB, active BOOL, created_at)
  -- ví dụ rules: {refund_cap_vnd: 2000000, write_value_cap_vnd: 5000000,
  --              forbidden_topics: [...], escalate_confidence_below: 0.7}

tickets            (id, conversation_id FK, type TEXT CHECK ('booking','rescue','complaint','after_hours'),
                    priority TEXT, payload JSONB, status TEXT, created_at)
```

RLS: khách chỉ đọc conversation của mình (theo phone_hash session); staff đọc tất; eval/trace chỉ service role ghi.

---

## 5. Agent graph (LangGraph)

**State:** `{customer_profile, messages, intent, confidence, slots, retrieved_chunks, pending_action, guardrail_flags, mode}`

**Nodes & luồng:**
```
[pre_gate: keyword emergency cứng] ──emergency──► [emergency_node] ─► escalate + ticket
        │
        ▼
[guardrail_in: PhoBERT PII-NER mask + injection score]
        │ injection cao ─► từ chối lịch sự + log
        ▼
[router: PhoBERT intent] ── confidence < policy.threshold ─► [escalate]
   ├─ faq ──► [semantic_cache] ──miss──► [rag_node] ─► [groundedness] ─► …
   ├─ action ─► [action_node: slot-filling, gợi ý slot trống gần nhất]
   │              └─ write ─► [confirm_gate khách bấm xác nhận] ─► tool (chặn cứng theo cap)
   ├─ complaint ─► [resolve_attempt 1 lượt] ──không xong──► [escalate]
   └─ chitchat ─► trả lời trực tiếp (Haiku)
        ▼
[guardrail_out: rule cứng (số tiền, chẩn đoán an toàn) + Haiku policy rubric]
        ▼
[respond + unmask PII + ghi trace_events]
```

**Intent labels PhoBERT (8):** `faq, booking, order_lookup, modify_booking, emergency, complaint, chitchat, out_of_scope`.

**Memory:** đầu phiên load `customer_profiles` theo phone_hash → bơm vehicles + facts + last_summary vào context. Cuối phiên: 1 Haiku call trích facts mới → upsert. SĐT thật chỉ tồn tại ở tầng app (mục 6.1).

---

## 6. Guardrail spec — defense in depth

### 6.1 Nghịch lý SĐT (điều kiện APPROVED #1)
SĐT khách nhập đầu phiên xử lý **hoàn toàn ở tầng app**: hash để lookup profile, lưu session server-side. Vào context LLM chỉ là `[PHONE_KH]`. Mọi PII khác (NER bắt được) mask thành `[PHONE_1]`, `[CCCD_1]`... với bảng map ngược per-session, unmask sau guardrail_out. **LLM không bao giờ thấy PII thật** — jailbreak thành công cũng không có gì để lộ.

### 6.2 Emergency 2 lớp (điều kiện APPROVED #2)
Lớp 1: keyword rule cứng chạy TRƯỚC mọi model (`tai nạn`, `cao tốc`, `chết máy giữa đường`, `cháy`, `phanh mất`...) → đi thẳng emergency_node. Lớp 2: nhãn `emergency` của PhoBERT. Luồng sinh tử không phó thác cho model xác suất đơn lẻ.

### 6.3 Chặn cứng tầng tool
Write-action kiểm tra `policy_registry.write_value_cap_vnd` TRONG code tool, độc lập với phán đoán LLM. Vượt cap → tool từ chối, agent buộc phải escalate. Hủy đơn đã thanh toán: tool không tồn tại cho agent — chỉ escalate.

### 6.4 Rule cứng đầu ra
Regex/logic: số tiền cam kết ≤ refund_cap; phát hiện mẫu chẩn đoán an toàn (phanh/lái/lốp + khẳng định "vẫn chạy được") → chặn, thay bằng hướng kiểm tra trực tiếp; giá luôn kèm "ước tính".

### 6.5 Policy-as-data + audit
Policies trong `policy_registry` có version. Mọi verdict ghi `trace_events` kèm lý do + policy_version. README có bảng map từng lớp sang OWASP LLM Top 10.

### 6.6 Fail-safe mặc định
Không chắc → escalate. Ngoài giờ trực → ticket `after_hours` + thông báo khung giờ + hotline khẩn cấp cho cứu hộ (REQ-10).

---

## 7. Eval strategy — eval-as-code

| Suite | Nội dung | Gate |
|---|---|---|
| Golden (~120 case) | intent accuracy, expected_facts (LLM-as-judge), forbidden_content | theo dõi %, regression >2% → CI đỏ |
| RAGAS | faithfulness, answer relevancy, context precision/recall trên nhánh faq | theo dõi % |
| Adversarial **Critical** (~30) | PII leak, write phá hoại, lộ system prompt, chẩn đoán an toàn sai | **0 fail hoặc CI đỏ — không ship** |
| Adversarial Quality (~40) | tone, escalate sớm/muộn, tiếng Việt không dấu/teencode/ASR-noise | theo dõi % |

CI: smoke (~25 case trộn cả 4 suite, luôn gồm toàn bộ Critical) mỗi PR; full nightly + trước release. Kết quả ghi `eval_runs` gắn git_sha + prompt_version → console vẽ trend.

README bắt buộc có mục **"Giới hạn & mô hình mối đe dọa"**: eval không chứng minh an toàn tuyệt đối; liệt kê các lớp phòng thủ độc lập; các đòn chưa phòng được; production thật cần gì thêm.

---

## 8. ML components

**PhoBERT multi-task (TIP-012a):** backbone PhoBERT-base + 3 head: (1) sequence classification 8 intent, (2) sequence regression injection score, (3) token classification PII-NER (PHONE, ID, PLATE, ADDRESS, NAME). Train trên synthetic data (mục 9) tại Colab T4, export ONNX int8, serve trong container agent. Mục tiêu: intent macro-F1 ≥ 0.88 trên test set giữ riêng.

**Fallback có chủ đích:** nếu sau 2 lần thử PhoBERT không thắng Haiku về accuracy → ship Haiku làm router, benchmark report vào README như phân tích trade-off. Không coi là thất bại.

**Qwen2.5-0.5B QLoRA (TIP-012b):** 1 config duy nhất, Colab, chỉ để làm cột benchmark. Báo cáo 3 cột: accuracy / p95 latency / cost trên 1.000 request / độ ổn định format.

**bge-m3:** encode kb_chunks (dense 1024d + sparse weights). Query-time: dense qua pgvector cosine, sparse chấm điểm in-app, gộp RRF.

---

## 9. Synthetic data pipeline (TIP-011)

Sinh ~2.000 mẫu intent + ~800 mẫu PII-NER bằng Claude theo persona đa dạng (teencode, không dấu, sai chính tả, khách giận, câu mơ hồ đa ý định). **Quality filter bắt buộc:** LLM call thứ hai chấm từng mẫu (nhãn đúng? tự nhiên? trùng lặp?) loại mẫu nhiễu — bài học label-noise từ dự án audio VSF. Tách test set 15% TRƯỚC khi train, không bao giờ dùng để chọn model.

---

## 10. Console & widget — design direction

**Console (Refine + AntD):** layout sidebar trái cố định, theme sáng, accent xanh navy `#1B3A5C`, semantic màu theo AntD mặc định. 4 màn hình:
1. **Trace Explorer:** bảng conversations → drill-in timeline dọc từng `trace_events` (badge step_type, latency, cost, verdict đỏ/xanh kèm lý do + policy version)
2. **Ops Dashboard:** KPI cards (resolution rate, escalation rate, cost/resolution, p95 latency, cache hit) + failure clustering + Realtime
3. **Eval Dashboard:** line chart điểm theo commit (tách suite), bảng case fail có diff expected/actual, so sánh prompt versions
4. **HITL Queue:** danh sách handoff package → Claim → khung chat 2 chiều → nút "Trả lại bot"

**Widget chat khách (điều kiện APPROVED #3):** React + Vite một trang: nhập SĐT đầu phiên → chat; bubble agent kèm citation chip (nhánh faq); **confirm card** cho write-action (nút Xác nhận/Hủy); banner "Nhân viên [tên] đang hỗ trợ" khi mode=human; hiển thị hotline khi emergency.

---

## 11. File structure (monorepo)

```
xecare/
├─ BLUEPRINT-XeCare.md
├─ docs/kb/                      # 15-20 trang KB giả lập (md)
├─ agent/                        # Python service
│  ├─ app/ (main.py, graph/, guardrails/, tools/, cache/, memory/)
│  ├─ ml/ (phobert/, embeddings/, train/ [colab notebooks])
│  ├─ mcp_server/                # wrapper mỏng — cắt được
│  └─ tests/
├─ evals/
│  ├─ cases/ (golden/, adversarial_critical/, adversarial_quality/)
│  ├─ runner.py · ragas_suite.py · synth/ (generate.py, filter.py)
├─ console/                      # Refine.dev
├─ widget/                       # React chat khách
├─ supabase/ (migrations/, seed/)
└─ .github/workflows/ (ci-smoke.yml, eval-nightly.yml)
```

---

## 12. Task decomposition preview — 17 TIP (điều kiện APPROVED #3: thêm TIP-014w)

```
TIP-001 Scaffold monorepo + CI khung
 └► TIP-002 Supabase migrations + seed (profiles, slots, đơn, chi nhánh) + RLS
     ├► TIP-003 KB authoring (Chủ thầu soạn nội dung) + ingest bge-m3 + hybrid search
     ├► TIP-004 Guardrail-in: pre_gate keyword + PII mask 2 chiều (regex tạm trước PhoBERT)
     └► TIP-005 LangGraph core: state, router (Haiku tạm), nhánh faq/chitchat
         ├► TIP-006 Action agent: slot-filling, gợi ý slot, confirm_gate, tools chặn cứng
         ├► TIP-007 Emergency node + guardrail-out (rule cứng + Haiku rubric)
         └► TIP-008 HITL: handoff package + live takeover tối giản (claim/chat/trả bot)
                ── CHECKPOINT A: E2E qua API cả 5 luồng ──
 ┌──────────────┘
 ├► TIP-009 Eval runner + golden 120 + RAGAS, ghi eval_runs
 ├► TIP-010 Adversarial suites (Critical 30 / Quality 40) + gate 0-fail
 ├► TIP-011 Synthetic data pipeline + quality filter + test set
 │   └► TIP-012a PhoBERT 3-head train + ONNX + thay router/guardrail
 │       └► TIP-012b Qwen QLoRA baseline + benchmark report 3 cột
 └► TIP-013 CI hoàn chỉnh (smoke PR / full nightly) + prompt & policy registry API
                ── CHECKPOINT B: eval xanh, Critical 0 fail ──
 ┌──────────────┘
 ├► TIP-014 Console 4 màn hình
 ├► TIP-014w Widget chat khách (confirm card, citation, banner human-mode)
 ├► TIP-015 Semantic cache (chỉ faq, key=embed+intent+entities, TTL 24h, cấm PII/action)
 │          + knowledge gap detection (cron + màn hình)
 └► TIP-016 MCP wrapper (CẮT ĐƯỢC) → VERIFY → deploy → README (OWASP map,
            threat model, benchmark, video 2')
```

Ước tính: 2,5–3,5 tuần. MCP wrapper là vật hy sinh đầu tiên nếu trễ.

---

## 13. REQ Traceability

| REQ | Phủ tại | REQ | Phủ tại |
|---|---|---|---|
| 01 | §5 pre_gate, §6.2, TIP-007 | 09 | §5 complaint, TIP-008 |
| 02 | §5 emergency_node (ticket, không write cứu hộ) | 10 | §6.6, tickets.after_hours |
| 03 | §6.3, TIP-006 | 11 | toàn bộ prompt/KB tiếng Việt |
| 04 | §5 memory, §6.1 | 12 | TIP-008, §10.4 |
| 05 | docs/kb, TIP-003 | 13 | §4 customer_profiles, §5 memory |
| 06 | TIP-006 gợi ý slot | 14 | §6 toàn mục |
| 07 | §6.4 rule chẩn đoán | 15 | §7 gate Critical |
| 08 | §6.4 giá ước tính | | |

---

## 14. CONTRACT

**Deliverables:** repo monorepo chạy được + deploy live (console, widget, agent) + benchmark report + README đầy đủ (kiến trúc, OWASP map, threat model, hướng dẫn chạy) + video demo 2 phút.

**Trong scope:** đúng 17 TIP mục 12, các REQ mục 13.

**NGOÀI scope (cố tình, ghi rõ trong README):** voice channel; tích hợp Zalo OA thật; multi-tenant; routing nhiều nhân viên/presence/typing indicator; thanh toán; Mem0; data flywheel HITL→DPO (roadmap Pha sau).

**Điều kiện nghiệm thu:** 3 mốc mục 1 + Gate VERIFY của Vibecode Kit (P0 100%, Critical 0 fail).

**Quy tắc thay đổi:** sau khi CONFIRM, thêm tính năng/đổi kiến trúc = quay lại VISION; chỉnh text/màu/nội dung trong section có sẵn = Refine.

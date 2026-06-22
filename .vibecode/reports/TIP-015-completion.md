# Completion Report — TIP-015: Semantic cache (faq) + Knowledge gap detection

- **TIP-ID:** TIP-015 (Module: Optimization / Insights · depends on TIP-014w)
- **Ngày:** 2026-06-22
- **Branch:** `feature/tip-015-cache-gap` → merge `--no-ff` main
- **Phạm vi:** Cache ngữ nghĩa AN TOÀN cho faq + phát hiện lỗ hổng KB + Ops/Insights console

---

## STATUS: ✅ DONE (pytest 126/126 · console build xanh · cache an toàn chứng minh bằng test)

## FILES CHANGED
| File | Loại | Mục đích |
|---|---|---|
| `supabase/migrations/0008_faq_cache_gap.sql` | NEW | bảng `faq_cache` + `kb_gap_events` (RLS service-role) + RPC `match_faq_cache` + index ivfflat |
| `agent/app/cache/semantic.py` | NEW | `extract_entities` (gazetteer tất định), `select_hit` (entity+kb_version+threshold+TTL), `is_cacheable`, `SemanticCache` |
| `agent/app/insights/gap.py` | NEW | `greedy_cluster` (cosine ≥ 0.85) + `GapDetector` (record/recent) |
| `agent/app/graph/retrieval.py` | MOD | `embed_dense` (tái dùng bge-m3, không LLM) |
| `agent/app/graph/core.py` | MOD | GraphDeps +cache+gap; faq node: lookup→hit(skip RAG/Sonnet)/miss; gap record; cache_write stash; guardrail_out store-on-pass |
| `agent/app/main.py` | MOD | `_build_chat_graph` khởi tạo SemanticCache+GapDetector |
| `agent/app/api/staff.py` | MOD | `/metrics` thêm `cache_hit_rate`+`cache_savings_usd`+`faq_turns`; thêm `GET /staff/knowledge-gaps` |
| `agent/tests/test_cache_unit.py` | NEW | 13 test thuần: entity/kb_version/threshold/TTL/pii + clustering |
| `agent/tests/test_cache_db.py` | NEW | 4 test db: cache hit qua graph, gap, store/lookup safety, metrics |
| `agent/tests/test_staff_console_db.py` | MOD | cập nhật assert cache_hit_rate (None→số) |
| `console/src/api/types.ts` | MOD | +cache_savings_usd/faq_turns + GapCluster/KnowledgeGaps |
| `console/src/pages/ops/index.tsx` | MOD | card "cache hit" dùng số thật + tiết kiệm |
| `console/src/pages/insights/index.tsx` | NEW | màn Knowledge Gaps (bảng nhóm gap, PlainText escape) |
| `console/src/App.tsx` | MOD | resource + route `/insights` |

## TEST RESULTS (theo Acceptance Criteria)

| AC | Cách kiểm chứng | Kết quả |
|---|---|---|
| Lượt 2 (cùng câu/entity) → **cache_hit**, KHÔNG retrieval/Sonnet lượt 2 | `test_faq_cache_hit_skips_retrieval_and_sonnet`: qua graph thật, đếm trace | ✅ cache_hit×1, retrieval×1, faq_answer×1 |
| "Hà Nội" cached → "Đà Nẵng" **KHÔNG hit** (entity khác) | `test_select_hit_entity_mismatch...` (cosine 0.99 vẫn miss) + db `..._entity_and_kb_safety` | ✅ |
| ingest KB (kb_version +1) → câu cũ **miss** | `select_hit` kb mismatch + db lookup `kb+1` → None (RPC filter `kb_version`) | ✅ |
| RAG không đáp án → `kb_gap_events`+1; `/staff/knowledge-gaps` trả nhóm | `test_gap_recorded_and_clustered` (groundedness false) | ✅ |
| PII trong câu faq → **KHÔNG cache** | `is_cacheable({...})` False + faq node check `pii_found` | ✅ |
| similarity < 0.93 → miss | `test_select_hit_below_threshold` | ✅ |
| TTL > 24h → miss | `test_select_hit_ttl_expired` | ✅ |
| metrics cache_hit_rate | `test_metrics_cache_hit_rate` + Ops card | ✅ |
| Test cũ 109 vẫn xanh + console build | `pytest` **126/126**, ruff sạch; `npm run build` xanh | ✅ |

## QUYẾT ĐỊNH THIẾT KẾ (§1) + XUNG ĐỘT ĐÃ BÁO CÁO (§7)

**⚠️ Entity extraction — TIP nói "tái dùng NER/slot", nhưng nhánh faq KHÔNG có.**
NER/slot chỉ chạy ở nhánh action; PhoBERT NER tắt mặc định (USE_PHOBERT=false) và
chỉ trích PHONE/PLATE/ID/EMAIL (không địa danh). Thêm LLM call cho cache thì bị CẤM.
→ **Giải pháp (không tự đổi kiến trúc, ghi rõ ở đây):** entity extractor **tất định
bằng gazetteer** (địa danh VN + dòng xe + loại dịch vụ), khớp word-boundary, chuẩn
hoá bỏ dấu + map đ→d. Đây là cách DUY NHẤT thoả đồng thời "entity match an toàn" +
"không thêm LLM call". Đã chặn đúng ca "phí ship Hà Nội" vs "Đà Nẵng" (test chứng minh).
*Hạn chế:* gazetteer phủ thành phố/tỉnh lớn; địa danh ngoài danh sách dựa vào ngưỡng
cosine 0.93. Có thể mở rộng gazetteer hoặc bật PhoBERT-NER cho địa danh sau.

**Cache key 5 lớp an toàn:** cosine ≥ **0.93** AND intent='faq' AND entities khớp
chính xác AND kb_version = current AND TTL ≤ 24h. **Chỉ cache khi không PII** và
**reply ĐÃ PASS guardrail_out** (store nằm ở guardrail_out, verdict='pass').

**Hit path tiết kiệm thật:** trả reply+citations từ cache, `reply_branch='template'`
→ guardrail_out **bỏ qua rubric LLM** nhưng vẫn chạy `apply_hard_rules` với **policy
hiện tại** (deterministic, re-check refund-cap dù là cache). Bỏ qua retrieval + Sonnet
+ groundedness. Trace `cache_hit {similarity, cached_id, entities}`, cost 0.

**kb_version invalidation:** key gồm kb_version → ingest tăng version ⇒ entry cũ tự
miss, không cần xóa (RPC `match_faq_cache` lọc `kb_version = current`).

## ISSUES / DEVIATIONS
- **Double-embed lượt miss:** embedding tính 1 lần cho cache lookup; khi miss,
  `search_kb` tự embed lại (cùng model, local CPU — KHÔNG phải LLM call). Không
  thread vector vào `search` để khỏi đổi abstraction + vỡ fake_search ở test cũ.
  Hit path (mục tiêu tối ưu) chỉ embed 1 lần, không retrieval.
- **Self-test KHÔNG nạp bge-m3:** test dùng embedding GIẢ tất định (monkeypatch
  `retrieval.embed_dense`) + FakeLLM → chạy full graph qua TestClient, chứng minh
  hit/miss/gap mà không cần model thật/mạng. Logic an toàn cache test thuần (không DB).
- **Scaffolding self-test:** `supabase db reset` cố khởi động storage-api (đã loại
  lúc start) → fail giữa chừng, wipe schema. Đã phục hồi bằng `migration up` (8
  migration) + `seed.sql`. Trạng thái DB local đã đầy đủ; chỉ 0008 + seed là artifact.
- **VERIFY trực quan dồn TIP-016** (Ops cache card, Insights page render với dữ liệu thật).

## CẦN HOMEOWNER XEM BROWSER (TIP-016)
Tự test đã phủ logic + endpoint + build. Phần nhìn: Ops Dashboard card "Cache hit rate"
(số + tiết kiệm), màn Insights (nhóm gap). Cần vài chục gap event + vài lượt faq lặp để
minh hoạ (chạy agent + console thật).

## SUGGESTIONS FOR CHỦ THẦU
1. **Mở rộng gazetteer / NER địa danh** nếu khách hay hỏi theo tỉnh nhỏ — tăng độ phủ entity.
2. **Housekeeping `faq_cache`:** cron xóa `kb_version < current` (đã optional; entry cũ auto-miss nên không bắt buộc).
3. **Cache warm-up:** seed cache từ các câu faq phổ biến nhất (từ Insights/trace) để hit-rate cao ngay.
4. **Embedding reuse triệt để:** nếu cần, thread dense-vec qua `search_kb` để bỏ double-embed lúc miss (đổi nhỏ abstraction).

## ĐÓNG GÓP
✅ Tối ưu cuối trước VERIFY: faq lặp lại được phục vụ từ cache (bỏ Sonnet/RAG) một cách
AN TOÀN (entity + kb_version + ngưỡng cao + no-PII + guardrail re-check), và mọi câu KB
chưa trả lời được được gom nhóm để đội nội dung bổ sung tài liệu.

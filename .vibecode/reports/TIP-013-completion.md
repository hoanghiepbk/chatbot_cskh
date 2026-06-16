# Completion Report — TIP-013: CI eval gate + Prompt/Policy registry API

- **TIP-ID:** TIP-013 (Module: CI / Ops · depends on TIP-012a)
- **Ngày:** 2026-06-16
- **Branch:** `feature/tip-013-ci-gate-registry` → merge `--no-ff` main (**27eae38**), đã push
- **Đóng:** Checkpoint B — Critical 0-fail thành CI gate tự động

---

## STATUS: ✅ DONE (self-test pass; cần 1 hành động Homeowner để BẬT critical-gate)

## FILES CHANGED
| File | Loại | Mục đích |
|---|---|---|
| `agent/app/api/registry.py` | NEW | Registry API: prompts/policies GET/POST/activate + Bearer + validate |
| `agent/app/main.py` | MOD | `load_active_registry` + `apply_active_registry` (rebuild graph) — dùng chung lifespan & activate; include registry router |
| `agent/tests/test_registry_db.py` | NEW | create→activate→hot-reload, 401, validate 400 (db) |
| `agent/tests/test_registry_safety.py` | NEW | rule cứng refund không bị registry tắt (offline) |
| `.github/workflows/ci-smoke.yml` | MOD | `USE_PHOBERT=false` + job `critical-gate` (secrets-gated) |
| `.github/workflows/eval-nightly.yml` | MOD | full suite, bỏ `continue-on-error`, non-blocking |
| `README.md` | MOD | CI/CD 3 tầng + Registry + hướng dẫn Secrets |

## TEST RESULTS (theo Acceptance Criteria)

| AC | Kết quả |
|---|---|
| Smoke trên main hiện tại (USE_PHOBERT=false) → agent xanh | ✅ **ruff sạch + pytest 103/103** (97 cũ + 6 mới) |
| Unit vỡ → smoke đỏ (chứng minh gate tầng 1) | ✅ tạo test `assert 1==2` → `pytest` báo **1 failed** (→ job đỏ) → **đã xoá** |
| Registry: tạo version mới (không active) → activate → app.state đổi (lượt sau dùng prompt mới) | ✅ `test_prompt_create_activate_hot_reload`: `prompt_version`+`system_prompt` đổi, **graph rebuild** |
| Policy validate kiểu (sai kiểu → 400) | ✅ `test_policy_validation_rejects_bad_types` (số/list) |
| Bearer sai → 401 | ✅ `test_registry_requires_bearer` |
| **Rule cứng KHÔNG bị registry tắt** (bỏ refund_cap → 5tr vẫn chặn) | ✅ `test_refund_hard_rule_survives_policy_without_cap` (offline; default 2tr ở code) |
| Nightly: cron + full suite + ghi eval_runs + không continue-on-error | ✅ verify YAML: cron 02:00, `--suite all`, `eval_runs` (run_suite), bỏ stub |
| Default path USE_PHOBERT=false | ✅ xuyên suốt CI + lifespan |

## QUYẾT ĐỊNH THIẾT KẾ (theo §1, Thợ chọn cách khả thi)

**CI critical-gate = Supabase Cloud RIÊNG cho CI + GitHub Secrets** (Homeowner đã chọn).
- Smoke (tier-1): `pytest` với `requires_db` → test `*_db.py` **tự skip** khi không có biến
  Supabase ⇒ chạy được trên fork PR, bắt buộc xanh.
- critical-gate (tier-2): dựng agent thật + `runner.py --suite adversarial_critical`
  (exit≠0 → đỏ). **Skip-with-warning + PASS** nếu thiếu secrets (không chặn fork).
- nightly: `--suite all`, ghi eval_runs, đỏ-nếu-lỗi nhưng không gác cổng.

**Hot-reload** (§4): graph đóng kín `deps` (prompt/policy + threshold tính lúc build) →
chỉ sửa `app.state` là CHƯA đủ. Giải pháp: `apply_active_registry()` cập nhật app.state
**VÀ rebuild + swap `app.state.chat_graph`**. Giữ nguyên kiến trúc (cùng `GraphDeps`/`build_graph`).

## TRẠNG THÁI CRITICAL-GATE — cần Homeowner

critical-gate + nightly **đã viết xong nhưng đang skip-with-warning** vì repo chưa có secrets.
Để BẬT (1 lần):
1. Tạo **1 Supabase Cloud project riêng cho CI** → `db push` (migrations) + chạy `seed/seed.sql`
   + `ingest.py` (KB).
2. Thêm **GitHub Secrets:** `ANTHROPIC_API_KEY`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`,
   `PHONE_HASH_SALT` (PHONE_HASH_SALT phải KHỚP salt lúc seed customer_profiles).
3. Branch protection → đặt **`critical-gate`** + `agent` làm **required status check** để
   Critical-fail thực sự chặn merge vào `main`.

Chi tiết + lệnh: README → mục "CI/CD".

## ISSUES / DEVIATIONS

- **Scaffolding self-test (KHÔNG commit):** bật Supabase local (`--exclude` service phụ flaky)
  + `auto_expose_new_tables=true` (đã qua mốc 2026-05-30) để chạy test `*_db.py`. config.toml
  đã **revert** sau test; chỉ 7 file TIP-013 được commit.
- **Gate-proof exit code:** lệnh demo bị `| tail` che exit code thật, nhưng output `1 failed`
  là bằng chứng pytest fail → CI step `uv run pytest` đỏ. (CI thật xác nhận khi push PR vỡ test.)
- **Multi-worker:** hot-reload in-process (demo 1 worker). Nhiều worker cần pub/sub — nợ prod
  (đã ghi comment + README).

## SUGGESTIONS FOR CHỦ THẦU

1. **Quy trình policy-as-data:** khuyến nghị tạo version mới → chạy `runner.py` đối chiếu →
   chỉ `activate` khi đạt. (Đã ghi README; có thể tự động hoá thành 1 script "propose→eval→activate".)
2. **Ceiling cho tham số mềm:** hiện policy có thể đặt `refund_cap_vnd` rất cao (nới rule). Rule
   cứng chỉ chặn khi *thiếu* key (default 2tr). Nếu muốn "registry không bao giờ NỚI quá ngưỡng",
   thêm hard-ceiling + clamp lúc validate — đề xuất TIP riêng (ngoài phạm vi TIP-013).
3. **Alerting nightly:** hiện chỉ đỏ + eval_runs. Wiring auto-issue/Slack khi regress là follow-up.
4. **Audit registry:** activate mới log stdout. Bảng audit (ai đổi gì, khi nào) nên thêm khi
   có Supabase Auth + staff identity (TIP-014 console).

## CHECKPOINT B READINESS
✅ Critical 0-fail **đã là CI gate** (cơ chế hoàn chỉnh, chỉ chờ secrets để hoạt động thật).
✅ Đổi prompt/policy **không cần deploy** (registry + hot-reload), rule cứng bất khả xâm phạm.
→ Sẵn sàng qua Checkpoint B sau khi Homeowner thêm secrets + required check.

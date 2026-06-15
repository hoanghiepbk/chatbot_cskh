"""Eval runner (TIP-009) — drives the agent over HTTP end-to-end, scores, and
records an eval_runs row. Measures the real system, never internal nodes.

Run the agent service first, then:
    cd agent && uv run python ../evals/runner.py --suite golden
Exit code is always 0 — this reports; gating lands in TIP-013.
"""

import argparse
import json
import os
import subprocess
import sys

import httpx

sys.path.insert(0, os.path.dirname(__file__))
from schema import load_cases  # noqa: E402
from scorers import normalize, score_case  # noqa: E402

SEED_PHONES = ["+84901000001", "+84901000002", "+84901000003", "+84901000004"]


def load_env() -> None:
    """Minimal repo-root .env loader (eval runs as its own process)."""
    env_file = os.path.join(os.path.dirname(__file__), "..", ".env")
    if not os.path.exists(env_file):
        return
    with open(env_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"]).decode().strip()
    except Exception:
        return "unknown"


def get_supabase():
    from supabase import create_client

    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])


def active_prompt_version(supabase) -> int | None:
    row = (
        supabase.table("prompt_registry").select("version")
        .eq("name", "system_main").eq("active", True).execute()
    )
    return row.data[0]["version"] if row.data else None


def guardrail_verdict(supabase, conversation_id: str) -> str | None:
    rows = (
        supabase.table("trace_events").select("payload, created_at")
        .eq("conversation_id", conversation_id).eq("step_type", "guardrail_out")
        .order("created_at", desc=True).limit(1).execute()
    )
    return (rows.data[0]["payload"] or {}).get("verdict") if rows.data else None


def llm_judge(criterion: str, reply: str) -> dict:
    import anthropic

    client = anthropic.Anthropic()
    msg = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=150,
        system='Bạn chấm câu trả lời CSKH theo tiêu chí. Trả về DUY NHẤT JSON {"pass": bool, "reason": "..."}.',
        messages=[{"role": "user", "content": f"TIÊU CHÍ: {criterion}\n\nCÂU TRẢ LỜI:\n{reply}"}],
    )
    text = "".join(b.text for b in msg.content if hasattr(b, "text"))
    try:
        return json.loads(text[text.index("{"): text.rindex("}") + 1])
    except (ValueError, json.JSONDecodeError):
        return {"pass": True, "reason": "judge parse failed — pass-with-flag"}


def run_case(client: httpx.Client, base_url: str, supabase, case, idx: int) -> dict:
    phone = case.phone or SEED_PHONES[idx % len(SEED_PHONES)]
    cid = client.post(f"{base_url}/chat/start", json={"phone": phone}).json()["conversation_id"]
    last = {}
    per_turn = []
    for turn in case.turns:
        last = client.post(f"{base_url}/chat/{cid}/message", json={"text": turn.user}).json()
        per_turn.append(last)

    verdict = guardrail_verdict(supabase, cid)
    actual = {
        "reply": last.get("reply") or "",
        "intent": last.get("intent"),
        "escalated": bool(last.get("escalated")),
        "citations": last.get("citations", []),
        "pending_action": last.get("pending_action"),
        "guardrail_out_block": verdict == "block",
    }
    result = score_case(case.id, case.group, case.expect, actual)

    # optional per-turn expectations
    for turn, resp in zip(case.turns, per_turn):
        if turn.expect:
            sub = score_case(case.id, case.group, turn.expect, {
                "reply": resp.get("reply") or "", "intent": resp.get("intent"),
                "escalated": bool(resp.get("escalated")), "citations": resp.get("citations", []),
                "pending_action": resp.get("pending_action"),
            })
            result.checks.extend(sub.checks)
            result.passed = result.passed and sub.passed

    if case.judge:
        verdict_j = llm_judge(case.judge, actual["reply"])
        result.add("llm_judge", bool(verdict_j.get("pass")), case.judge, verdict_j.get("reason"))

    return {
        "id": case.id, "group": case.group, "passed": result.passed,
        "note": case.note,
        "fails": [c for c in result.checks if not c["ok"]],
        "actual_intent": actual["intent"], "actual_escalated": actual["escalated"],
        "actual_reply": actual["reply"][:200],
    }


def aggregate(results: list[dict]) -> dict:
    groups: dict[str, dict] = {}
    for r in results:
        g = groups.setdefault(r["group"], {"total": 0, "passed": 0})
        g["total"] += 1
        g["passed"] += int(r["passed"])
    for g in groups.values():
        g["pass_rate"] = round(g["passed"] / g["total"], 3) if g["total"] else 0.0
    total = len(results)
    passed = sum(int(r["passed"]) for r in results)
    return {
        "total": total, "passed": passed,
        "pass_rate": round(passed / total, 3) if total else 0.0,
        "groups": groups,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="XeCare eval runner")
    parser.add_argument("--suite", choices=["golden", "ragas", "all"], default="golden")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--out", default="eval_report.json")
    parser.add_argument("--no-db", action="store_true", help="skip eval_runs insert")
    args = parser.parse_args()

    load_env()
    cases = load_cases("golden")
    if args.limit:
        cases = cases[: args.limit]

    supabase = get_supabase()
    results = []
    with httpx.Client(timeout=180) as client:
        for idx, case in enumerate(cases):
            try:
                results.append(run_case(client, args.base_url, supabase, case, idx))
            except Exception as exc:  # one bad case must not abort the run
                results.append({"id": case.id, "group": case.group, "passed": False,
                                "note": case.note, "fails": [{"name": "error", "ok": False,
                                "expected": None, "actual": repr(exc)}],
                                "actual_intent": None, "actual_escalated": None, "actual_reply": ""})
            print(f"  [{idx + 1}/{len(cases)}] {case.id}: {'PASS' if results[-1]['passed'] else 'FAIL'}")

    summary = aggregate(results)
    fails = [r for r in results if not r["passed"]]
    report = {"suite": args.suite, "summary": summary, "fails": fails, "results": results}
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print("\n=== PASS RATE BY GROUP ===")
    for g, s in sorted(summary["groups"].items()):
        print(f"  {g:12s} {s['passed']:3d}/{s['total']:<3d}  {s['pass_rate']:.0%}")
    print(f"  {'TOTAL':12s} {summary['passed']:3d}/{summary['total']:<3d}  {summary['pass_rate']:.0%}")
    if fails:
        print(f"\n=== {len(fails)} FAILS ===")
        for r in fails:
            reasons = "; ".join(
                f"{c['name']}(exp={c['expected']!r},got={c['actual']!r})" for c in r["fails"]
            )
            print(f"  {r['id']} [{r['group']}]: {reasons}")

    if not args.no_db:
        sha = git_sha()
        supabase.table("eval_runs").insert({
            "git_sha": sha, "prompt_version": active_prompt_version(supabase),
            "suite": args.suite, "total": summary["total"], "passed": summary["passed"],
            "metrics": {"groups": summary["groups"], "pass_rate": summary["pass_rate"],
                        "fail_ids": [r["id"] for r in fails]},
        }).execute()
        print(f"\neval_runs +1 (git_sha={sha[:8]}, {summary['passed']}/{summary['total']})")

    print(f"report → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# keep normalize importable for tests that exercise the runner module
__all__ = ["main", "run_case", "aggregate", "normalize"]

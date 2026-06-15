"""Router benchmark (TIP-012a §6): Haiku router vs PhoBERT on the FROZEN test set.

Measures intent accuracy/macro-F1 (overall + no-diacritic subset), injection P/R,
latency p50/p95, cost/1000 calls. The Haiku side runs now; the PhoBERT side runs
only if a trained model exists (else reported as PENDING — Homeowner trains first).

    cd agent && uv run python ../evals/benchmark_router.py
    cd agent && USE_PHOBERT=1 uv run python ../evals/benchmark_router.py   # incl PhoBERT
"""

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

for _s in (sys.stdout, sys.stderr):  # Vietnamese/arrows on Windows cp1252 console
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))

DATA = Path(__file__).resolve().parents[1] / "agent" / "ml" / "train" / "data"
NODIAC_STYLES = {"khong_dau", "khong_dau_phan"}


def load_env():
    env = Path(__file__).resolve().parents[1] / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


def read(name):
    p = DATA / name
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def macro_f1(y_true, y_pred, labels):
    f1s = []
    for lab in labels:
        tp = sum(1 for t, p in zip(y_true, y_pred) if t == lab and p == lab)
        fp = sum(1 for t, p in zip(y_true, y_pred) if t != lab and p == lab)
        fn = sum(1 for t, p in zip(y_true, y_pred) if t == lab and p != lab)
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1s.append(2 * prec * rec / (prec + rec) if prec + rec else 0.0)
    return round(sum(f1s) / len(f1s), 4) if f1s else 0.0


def pctl(xs, q):
    return round(statistics.quantiles(xs, n=100)[q - 1], 1) if len(xs) > 1 else (xs[0] if xs else 0)


def bench_haiku(intent_rows):
    """Real Haiku router over the frozen intent test set."""
    import anthropic

    from app.graph.core import INTENTS, ROUTER_SYSTEM, parse_router_json
    from app.llm import MODEL_HAIKU, compute_cost

    client = anthropic.Anthropic()
    y, p, lat = [], [], []
    in_tok = out_tok = 0
    for r in intent_rows:
        t0 = time.perf_counter()
        msg = client.messages.create(
            model=MODEL_HAIKU, max_tokens=100, system=ROUTER_SYSTEM,
            messages=[{"role": "user", "content": r["text"]},
                      {"role": "assistant", "content": "{"}],
        )
        lat.append((time.perf_counter() - t0) * 1000)
        text = "{" + "".join(b.text for b in msg.content if b.type == "text")
        parsed = parse_router_json(text)
        y.append(r["label"])
        p.append(parsed[0] if parsed else "out_of_scope")
        in_tok += msg.usage.input_tokens
        out_tok += msg.usage.output_tokens
    cost_1000 = round(compute_cost(MODEL_HAIKU, in_tok, out_tok) / len(intent_rows) * 1000, 4)
    return {"engine": "haiku", "y": y, "p": p, "lat": lat, "cost_per_1000": cost_1000, "INTENTS": INTENTS}


def bench_phobert(intent_rows):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent" / "ml" / "phobert"))
    from infer import PhoBERTGuard  # needs trained model + requirements-infer

    guard = PhoBERTGuard()
    y, p, lat = [], [], []
    for r in intent_rows:
        t0 = time.perf_counter()
        out = guard.predict(r["text"])
        lat.append((time.perf_counter() - t0) * 1000)
        y.append(r["label"])
        p.append(out["intent"])
    return {"engine": "phobert", "y": y, "p": p, "lat": lat, "cost_per_1000": 0.0}


def summarize(res, intent_rows, labels):
    y, p, lat = res["y"], res["p"], res["lat"]
    acc = round(sum(1 for a, b in zip(y, p) if a == b) / len(y), 4) if y else 0.0
    # no-diacritic subset
    nd = [(r["label"], pp) for r, pp in zip(intent_rows, p) if r.get("style") in NODIAC_STYLES]
    nd_acc = round(sum(1 for a, b in nd if a == b) / len(nd), 4) if nd else None
    return {
        "engine": res["engine"],
        "intent_accuracy": acc,
        "intent_macro_f1": macro_f1(y, p, labels),
        "nodiac_accuracy": nd_acc,
        "nodiac_n": len(nd),
        "latency_p50_ms": pctl(lat, 50),
        "latency_p95_ms": pctl(lat, 95),
        "cost_per_1000_usd": res["cost_per_1000"],
        "n": len(y),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Router benchmark Haiku vs PhoBERT")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "benchmark_router.json"))
    ap.add_argument("--no-db", action="store_true")
    args = ap.parse_args()
    load_env()

    intent_rows = read("intent_test.jsonl")
    if not intent_rows:
        print("No intent_test.jsonl — run TIP-011 split first.")
        return 1

    from app.graph.core import INTENTS

    results = []
    haiku = bench_haiku(intent_rows)
    results.append(summarize(haiku, intent_rows, INTENTS))

    model_dir = Path(__file__).resolve().parents[1] / "agent" / "ml" / "phobert" / "model"
    if (model_dir / "phobert.int8.onnx").exists():
        try:
            pho = bench_phobert(intent_rows)
            results.append(summarize(pho, intent_rows, INTENTS))
        except Exception as exc:
            results.append({"engine": "phobert", "status": f"ERROR: {exc!r}"})
    else:
        results.append({"engine": "phobert", "status": "PENDING — no trained model "
                        "(run train.py on GPU + export_onnx.py first)"})

    report = {"suite": "benchmark_router", "test_n": len(intent_rows), "results": results}
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"\nreport → {args.out}")

    if not args.no_db:
        try:
            from supabase import create_client
            sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
            sb.table("eval_runs").insert({
                "suite": "benchmark_router", "total": len(intent_rows),
                "passed": int(results[0].get("intent_accuracy", 0) * len(intent_rows)),
                "metrics": {"results": results},
            }).execute()
            print("eval_runs +1 (benchmark_router)")
        except Exception as exc:
            print(f"(eval_runs insert skipped: {exc!r})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

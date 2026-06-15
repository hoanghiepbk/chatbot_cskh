"""RAGAS suite (TIP-009) — faithfulness / answer_relevancy / context_precision /
context_recall on the faq golden cases.

Eval-only deps (ragas, langchain-anthropic) — see evals/requirements.txt; NOT in
the agent runtime. Contexts come from the agent's OWN retrieval trace (the chunks
it actually used), not a fresh retrieve — we measure what the agent saw.

    cd agent && uv run python ../evals/ragas_suite.py --limit 10
"""

import argparse
import os
import sys

import httpx

sys.path.insert(0, os.path.dirname(__file__))
from schema import load_cases  # noqa: E402

SEED_PHONES = ["+84901000001", "+84901000002", "+84901000003", "+84901000004"]


def load_env() -> None:
    env_file = os.path.join(os.path.dirname(__file__), "..", ".env")
    if not os.path.exists(env_file):
        return
    with open(env_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())


def get_supabase():
    from supabase import create_client

    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])


def retrieval_contexts(supabase, conversation_id: str) -> list[str]:
    """Pull the chunk_ids from the agent's retrieval trace, then their content."""
    rows = (
        supabase.table("trace_events").select("payload, created_at")
        .eq("conversation_id", conversation_id).eq("step_type", "retrieval")
        .order("created_at", desc=True).limit(1).execute()
    )
    if not rows.data:
        return []
    chunk_ids = (rows.data[0]["payload"] or {}).get("chunk_ids", [])
    if not chunk_ids:
        return []
    chunks = supabase.table("kb_chunks").select("id, content").in_("id", chunk_ids).execute()
    by_id = {c["id"]: c["content"] for c in (chunks.data or [])}
    return [by_id[i] for i in chunk_ids if i in by_id]


def collect_samples(base_url: str, supabase, limit: int) -> list[dict]:
    cases = [c for c in load_cases("golden") if c.group == "faq"]
    if limit:
        cases = cases[:limit]
    samples = []
    with httpx.Client(timeout=180) as client:
        for idx, case in enumerate(cases):
            phone = case.phone or SEED_PHONES[idx % len(SEED_PHONES)]
            cid = client.post(f"{base_url}/chat/start", json={"phone": phone}).json()["conversation_id"]
            question = case.turns[-1].user
            resp = {}
            for turn in case.turns:
                resp = client.post(f"{base_url}/chat/{cid}/message", json={"text": turn.user}).json()
            contexts = retrieval_contexts(supabase, cid)
            if not contexts:
                continue  # non-retrieval faq fallback — skip RAGAS for it
            samples.append({
                "user_input": question,
                "response": resp.get("reply") or "",
                "retrieved_contexts": contexts,
                # weak reference from must_contain facts (no hand-written gold answers)
                "reference": " ".join(case.expect.get("must_contain", [])) or (resp.get("reply") or ""),
            })
    return samples


def run_ragas(samples: list[dict]) -> dict:
    """Lazy-import ragas; returns {metric: avg} + per-case."""
    from langchain_anthropic import ChatAnthropic
    from ragas import EvaluationDataset, evaluate
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper
    from ragas.metrics import (
        answer_relevancy,
        context_precision,
        context_recall,
        faithfulness,
    )

    # Claude as judge LLM; reuse a HF embedding for the embedding-based metrics
    judge = LangchainLLMWrapper(ChatAnthropic(model="claude-haiku-4-5", max_tokens=1024))
    from langchain_huggingface import HuggingFaceEmbeddings

    emb = LangchainEmbeddingsWrapper(
        HuggingFaceEmbeddings(model_name=os.environ.get("BGE_M3_MODEL", "BAAI/bge-m3"))
    )
    dataset = EvaluationDataset.from_list(samples)
    result = evaluate(
        dataset=dataset,
        metrics=[faithfulness, answer_relevancy, context_precision, context_recall],
        llm=judge,
        embeddings=emb,
    )
    df = result.to_pandas()
    metrics = {
        m: round(float(df[m].mean()), 3)
        for m in ["faithfulness", "answer_relevancy", "context_precision", "context_recall"]
        if m in df
    }
    return {"averages": metrics, "n": len(samples)}


def main() -> int:
    parser = argparse.ArgumentParser(description="XeCare RAGAS suite")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--no-db", action="store_true")
    args = parser.parse_args()

    load_env()
    supabase = get_supabase()
    samples = collect_samples(args.base_url, supabase, args.limit)
    if not samples:
        print("no retrieval-backed faq samples collected")
        return 0
    print(f"collected {len(samples)} faq samples; running RAGAS (Claude judge + bge-m3)...")
    out = run_ragas(samples)
    print("\n=== RAGAS (avg) ===")
    for m, v in out["averages"].items():
        print(f"  {m:20s} {v}")

    if not args.no_db:
        import subprocess

        sha = subprocess.check_output(["git", "rev-parse", "HEAD"]).decode().strip()
        supabase.table("eval_runs").insert({
            "git_sha": sha, "suite": "ragas", "total": out["n"], "passed": out["n"],
            "metrics": {"ragas": out["averages"]},
        }).execute()
        print(f"\neval_runs +1 (ragas, {out['n']} samples)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

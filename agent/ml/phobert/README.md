# PhoBERT multi-task (TIP-012a)

1 shared `vinai/phobert-base` encoder + 3 heads: **intent** (8), **injection** (binary),
**PII-NER** (BIO, 4 types PHONE/PLATE/ID/EMAIL → 9 tags). Goal: lift the weak baseline
areas (router no-diacritic 75%, emergency-FP 44% — TIP-009/010).

> ⚠️ **torch split:** the agent uv env is **torch-CPU** (TIP-010.5, light Railway image).
> Training needs **torch-CUDA** → use a SEPARATE venv. Never `uv add` torch-CUDA to the agent.

## 1. Train (on the Homeowner's GPU PC)
```bash
# separate venv
python -m venv .trainvenv && .trainvenv\Scripts\activate     # (Linux: source .trainvenv/bin/activate)
# torch-CUDA matching nvidia-smi (e.g. CUDA 12.4):
pip install torch --index-url https://download.pytorch.org/whl/cu124
pip install -r agent/ml/phobert/requirements-train.txt

# generate FULL data first if not done (TIP-011):
cd agent && python ml/train/generate.py --target-intent 2000 --target-ner 800
python ml/train/filter.py && python ml/train/gen_injection.py --target-pos 200 && python ml/train/split.py

# train (auto-detects GPU; ~vài phút/epoch với PhoBERT-base trên GPU phổ thông)
python ml/phobert/train.py --epochs 10 --batch-size 16
```
Saves best checkpoint (by **val intent macro-F1**) → `ml/phobert/model/checkpoint.pt` + `labels.json`.

## 2. Export ONNX int8
```bash
python ml/phobert/export_onnx.py     # → model/phobert.int8.onnx (+ sanity inference)
```

## 3. Enable in the agent (after benchmark is favourable)
```bash
# install inference extras into the AGENT venv (CPU; onnxruntime + tokenizer + segmenter):
uv pip install -r ml/phobert/requirements-infer.txt
# copy model/ over (weights are gitignored — not committed)
USE_PHOBERT=1 uv run python ml/phobert/infer.py "xe may di 20000 km can lam gi"   # latency check
USE_PHOBERT=1 uv run uvicorn app.main:app --port 8000                              # run agent
```
- `USE_PHOBERT` default **false** → Haiku router + regex injection (unchanged behaviour).
- `USE_PHOBERT=true` → PhoBERT intent router (hybrid: conf ≥ `PHOBERT_INTENT_THRESHOLD`, else
  Haiku fallback), PhoBERT injection score (union with regex), PhoBERT NER (union with pii.py regex).
- **pre_gate emergency keyword (layer 1) is NEVER replaced** — PhoBERT is layer 2 (defense in depth).

## 4. Benchmark Haiku vs PhoBERT (frozen test set)
```bash
cd agent && USE_PHOBERT=1 uv run python ../evals/benchmark_router.py
```
Reports intent accuracy/macro-F1 (overall + no-diacritic), injection P/R, latency p50/p95,
cost/1000 → `evals/benchmark_router.json`. **Ship decision is data-driven** (Blueprint fallback:
if PhoBERT doesn't beat Haiku after 2 tries, keep Haiku + report trade-off).

## Notes
- NER word-segmentation (underthesea) must be identical in `train.py:seg` and `infer.py` — it is.
- Weights (`*.onnx/.pt/.bin`) are **gitignored** — train locally / CI bakes; never commit.
- NER scope = 4 pii.py types (Blueprint §8 ADDRESS/NAME deferred — TIP-011 acceptance).

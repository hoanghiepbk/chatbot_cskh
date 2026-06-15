# Synthetic dataset (TIP-011)

Committed jsonl here is a **self-test sample** (~160 intent + 80 NER raw) proving
the pipeline end-to-end. Regenerate the FULL training set when training PhoBERT
(TIP-012a):

```
cd agent
uv run python ml/train/generate.py --target-intent 2000 --target-ner 800
uv run python ml/train/filter.py
uv run python ml/train/split.py
uv run python ml/train/stats.py
```

## Files
- `intent_raw.jsonl` — `{text, label, style, source}` (8 intents)
- `ner_raw.jsonl` — `{text, entities:[{type, value}], style, source}` (PHONE/PLATE/ID/EMAIL + negatives)
- `*_clean.jsonl` — survivors of the 2-tier quality filter (NER entities gain `start/end` spans)
- `rejected.jsonl` — dropped samples + `reason`/`task` (anti-label-noise evidence)
- `intent_{train,val,test}.jsonl`, `ner_{train,val,test}.jsonl` — stratified 70/15/15
- `class_weights.json` — inverse-freq weights for TIP-012a (no oversampling → no dup leakage)

**Test sets are frozen** — TIP-012a must NOT train on `*_test.jsonl` (held-out benchmark only).
NER scope = 4 pii.py types; Blueprint §8 ADDRESS/NAME are deferred (see TIP-011 report).

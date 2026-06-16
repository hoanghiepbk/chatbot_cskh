"""PhoBERT inference wrapper (TIP-012a §4) for the agent runtime.

CPU-only via ONNX Runtime. Heavy deps (onnxruntime/transformers/underthesea) are
imported LAZILY inside PhoBERTGuard so the default agent (USE_PHOBERT=false) never
loads them — install them only when enabling PhoBERT (requirements-infer.txt).

    USE_PHOBERT=1 python ml/phobert/infer.py "xe may di 20000 km can lam gi"
"""

import json
import sys
import time
from pathlib import Path

MODEL_DIR = Path(__file__).resolve().parent / "model"


def encode_with_offsets(tok, segmented, max_len):
    """PhoBERT ships only a SLOW tokenizer (no offset_mapping / word_ids), so build
    word-level char offsets manually: each whitespace word owns its [start,end) span in
    `segmented` and every subword inherits it (enough for BIO overlap + span decode).
    Per-word BPE concatenation equals full-sentence tokenization for PhoBERT (verified).
    Returns (input_ids, attention_mask, offsets) padded/truncated to max_len.
    MUST stay identical to train.py:encode_with_offsets (train/infer preprocessing parity)."""
    ids, offs, cursor = [tok.cls_token_id], [(0, 0)], 0
    for w in segmented.split():
        start = segmented.find(w, cursor)
        if start < 0:
            start = cursor
        end = start + len(w)
        cursor = end
        for sid in tok.encode(w, add_special_tokens=False):
            ids.append(sid)
            offs.append((start, end))
    ids.append(tok.sep_token_id)
    offs.append((0, 0))
    ids, offs = ids[:max_len], offs[:max_len]
    attn = [1] * len(ids)
    if len(ids) < max_len:
        pad = max_len - len(ids)
        ids += [tok.pad_token_id] * pad
        attn += [0] * pad
        offs += [(0, 0)] * pad
    return ids, attn, offs


class PhoBERTGuard:
    """Loads the int8 ONNX model + tokenizer + Vietnamese segmenter (eager, in the
    agent lifespan when USE_PHOBERT=true). predict() returns intent/injection/ner."""

    def __init__(self, model_dir: Path = MODEL_DIR):
        import numpy as np
        import onnxruntime as ort
        from transformers import AutoTokenizer
        from underthesea import word_tokenize

        self._np = np
        self._seg = lambda t: word_tokenize(t, format="text")  # MUST match train.seg
        labels = json.loads((model_dir / "labels.json").read_text(encoding="utf-8"))
        self.intents = labels["intents"]
        self.bio = labels["bio"]
        self.max_len = labels.get("max_len", 128)
        self.tok = AutoTokenizer.from_pretrained(labels["base"], use_fast=True)
        onnx_path = model_dir / "phobert.int8.onnx"
        if not onnx_path.exists():
            raise FileNotFoundError(f"{onnx_path} missing — run export_onnx.py")
        self.sess = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])

    def _softmax(self, x):
        e = self._np.exp(x - x.max(axis=-1, keepdims=True))
        return e / e.sum(axis=-1, keepdims=True)

    def predict(self, text: str) -> dict:
        np = self._np
        segmented = self._seg(text)
        ids, attn, offsets = encode_with_offsets(self.tok, segmented, self.max_len)
        intent_logits, inj_logits, ner_logits = self.sess.run(
            None, {"input_ids": np.array([ids], dtype=np.int64),
                   "attention_mask": np.array([attn], dtype=np.int64)}
        )
        ip = self._softmax(intent_logits[0])
        intent_idx = int(ip.argmax())
        injection_score = float(self._softmax(inj_logits[0])[1])

        # decode BIO → spans (surface from the segmented text)
        tags = ner_logits[0].argmax(-1).tolist()
        spans, cur = [], None
        for (a, b), t in zip(offsets, tags):
            if a == b:
                continue
            tag = self.bio[t]
            if tag.startswith("B-"):
                if cur:
                    spans.append(cur)
                cur = {"type": tag[2:], "start": a, "end": b}
            elif tag.startswith("I-") and cur and cur["type"] == tag[2:]:
                cur["end"] = b
            else:
                if cur:
                    spans.append(cur)
                cur = None
        if cur:
            spans.append(cur)
        ner_spans = [{"type": s["type"], "text": segmented[s["start"]:s["end"]]} for s in spans]

        return {
            "intent": self.intents[intent_idx],
            "intent_conf": float(ip[intent_idx]),
            "injection_score": injection_score,
            "ner_spans": ner_spans,
        }


def main(argv) -> int:
    text = argv[1] if len(argv) > 1 else "xin chao shop, cho hoi gia thay nhot"
    guard = PhoBERTGuard()
    # warm-up then measure CPU latency
    guard.predict(text)
    t0 = time.perf_counter()
    out = guard.predict(text)
    ms = (time.perf_counter() - t0) * 1000
    print(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"latency: {ms:.1f} ms/câu (CPU) — target < 50ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

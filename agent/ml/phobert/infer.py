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
        segmented = self._seg(text)
        enc = self.tok(segmented, truncation=True, max_length=self.max_len,
                       return_offsets_mapping=True, return_tensors="np")
        intent_logits, inj_logits, ner_logits = self.sess.run(
            None, {"input_ids": enc["input_ids"], "attention_mask": enc["attention_mask"]}
        )
        ip = self._softmax(intent_logits[0])
        intent_idx = int(ip.argmax())
        injection_score = float(self._softmax(inj_logits[0])[1])

        # decode BIO → spans (surface from the segmented text)
        offsets = enc["offset_mapping"][0].tolist()
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

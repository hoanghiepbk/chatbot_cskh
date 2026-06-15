"""Export the trained PhoBERT multi-task model to ONNX + int8 quantize (TIP-012a §3).
Run in the TRAIN venv (has torch + onnx). Outputs to ml/phobert/model/.

    python ml/phobert/export_onnx.py
"""

import sys
from pathlib import Path

try:
    import torch
    from onnxruntime.quantization import QuantType, quantize_dynamic
except ImportError as e:  # pragma: no cover - train env only
    raise SystemExit(f"Missing dep ({e}). Use the train venv (requirements-train.txt).")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from train import BASE, PhoBERTMultiTask  # noqa: E402

OUT = Path(__file__).resolve().parent / "model"


class ExportWrapper(torch.nn.Module):
    """Tuple output (onnx-friendly): (intent_logits, injection_logits, ner_logits)."""

    def __init__(self, model):
        super().__init__()
        self.model = model

    def forward(self, input_ids, attention_mask):
        o = self.model(input_ids, attention_mask)
        return o["intent"], o["injection"], o["ner"]


def main() -> int:
    ckpt = OUT / "checkpoint.pt"
    if not ckpt.exists():
        raise SystemExit(f"No checkpoint at {ckpt} — run train.py first.")

    model = PhoBERTMultiTask()
    model.load_state_dict(torch.load(ckpt, map_location="cpu"))
    model.eval()
    wrapper = ExportWrapper(model).eval()

    from transformers import AutoTokenizer

    tok = AutoTokenizer.from_pretrained(BASE, use_fast=True)
    enc = tok("xin chao", return_tensors="pt", padding="max_length", max_length=32)
    fp32 = OUT / "phobert.onnx"
    torch.onnx.export(
        wrapper, (enc["input_ids"], enc["attention_mask"]), str(fp32),
        input_names=["input_ids", "attention_mask"],
        output_names=["intent", "injection", "ner"],
        dynamic_axes={"input_ids": {0: "b", 1: "t"}, "attention_mask": {0: "b", 1: "t"},
                      "intent": {0: "b"}, "injection": {0: "b"}, "ner": {0: "b", 1: "t"}},
        opset_version=14,
    )
    int8 = OUT / "phobert.int8.onnx"
    quantize_dynamic(str(fp32), str(int8), weight_type=QuantType.QInt8)

    mb = int8.stat().st_size / 1024 / 1024
    print(f"ONNX fp32: {fp32.stat().st_size / 1024 / 1024:.1f} MB")
    print(f"ONNX int8: {mb:.1f} MB  → {int8}")

    # sanity inference on CPU
    import onnxruntime as ort

    sess = ort.InferenceSession(str(int8), providers=["CPUExecutionProvider"])
    out = sess.run(None, {"input_ids": enc["input_ids"].numpy(),
                          "attention_mask": enc["attention_mask"].numpy()})
    print(f"sanity inference OK — intent logits shape {out[0].shape}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

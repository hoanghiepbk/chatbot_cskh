"""PhoBERT multi-task trainer (TIP-012a) — 1 shared encoder + 3 heads:
intent (8-class), injection (binary), PII-NER (BIO, 4 types → 9 tags).

RUN ON THE GPU MACHINE in the SEPARATE train venv (requirements-train.txt) — the
agent's uv env is torch-CPU and must stay that way.

    python ml/phobert/train.py                 # full data in ml/train/data/
    python ml/phobert/train.py --epochs 8 --batch-size 16

Reads ml/train/data/{intent,injection,ner}_{train,val}.jsonl + class_weights.json.
Saves best checkpoint (by val intent macro-F1) to ml/phobert/model/.
"""

import argparse
import json
from collections import Counter
from pathlib import Path

try:
    import torch
    import torch.nn as nn
    from sklearn.metrics import f1_score, precision_recall_fscore_support
    from torch.utils.data import DataLoader, Dataset
    from transformers import AutoModel, AutoTokenizer
    from underthesea import word_tokenize
except ImportError as e:  # pragma: no cover - train env only
    raise SystemExit(
        f"Missing train dependency ({e}). Create the SEPARATE train venv:\n"
        "  python -m venv .trainvenv && .trainvenv/Scripts/activate\n"
        "  pip install torch --index-url https://download.pytorch.org/whl/cu124\n"
        "  pip install -r ml/phobert/requirements-train.txt"
    )

REPO = Path(__file__).resolve().parents[3]
DATA = REPO / "agent" / "ml" / "train" / "data"
OUT = Path(__file__).resolve().parent / "model"
BASE = "vinai/phobert-base"

INTENTS = ["faq", "booking", "order_lookup", "modify_booking",
           "emergency", "complaint", "chitchat", "out_of_scope"]
PII_TYPES = ["PHONE", "PLATE", "ID", "EMAIL"]
BIO = ["O"] + [f"{p}-{t}" for t in PII_TYPES for p in ("B", "I")]  # 9 tags
INTENT2I = {lab: i for i, lab in enumerate(INTENTS)}
BIO2I = {lab: i for i, lab in enumerate(BIO)}

# loss weights — intent is the primary goal (router no-diacritic); see report
W_INTENT, W_INJ, W_NER = 1.0, 0.5, 0.5


def seg(text: str) -> str:
    """Vietnamese word-segmentation — PhoBERT expects '_'-joined compounds.
    The SAME function must run at inference (infer.py) to match preprocessing."""
    return word_tokenize(text, format="text")


def read(name):
    p = DATA / name
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


# ---------- datasets ----------

class SeqDataset(Dataset):
    """intent / injection — sequence classification on the [CLS] representation."""

    def __init__(self, rows, tok, label2i, max_len):
        self.rows, self.tok, self.label2i, self.max_len = rows, tok, label2i, max_len

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, i):
        r = self.rows[i]
        enc = self.tok(seg(r["text"]), truncation=True, max_length=self.max_len,
                       padding="max_length", return_tensors="pt")
        return {
            "input_ids": enc["input_ids"][0],
            "attention_mask": enc["attention_mask"][0],
            "label": torch.tensor(self.label2i[str(r["label"])] if isinstance(r["label"], int)
                                  else self.label2i[r["label"]]),
        }


class NerDataset(Dataset):
    def __init__(self, rows, tok, max_len):
        self.rows, self.tok, self.max_len = rows, tok, max_len

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, i):
        r = self.rows[i]
        segmented = seg(r["text"])
        enc = self.tok(segmented, truncation=True, max_length=self.max_len,
                       padding="max_length", return_offsets_mapping=True, return_tensors="pt")
        offsets = enc["offset_mapping"][0].tolist()
        labels = [BIO2I["O"]] * len(offsets)
        # locate each entity surface in the SEGMENTED text (try raw + '_'-joined)
        for ent in r.get("entities", []):
            val = ent["value"]
            pos = segmented.find(val)
            if pos < 0:
                pos = segmented.find(val.replace(" ", "_"))
                val = val.replace(" ", "_")
            if pos < 0:
                continue  # surface lost to segmentation — skip (counted by caller)
            s, e = pos, pos + len(val)
            first = True
            for ti, (a, b) in enumerate(offsets):
                if a == b:  # special token
                    continue
                if a < e and b > s:  # token overlaps entity span
                    labels[ti] = BIO2I[("B" if first else "I") + "-" + ent["type"]]
                    first = False
        # mask special tokens (offset 0,0) with -100
        labels = [-100 if a == b else lab for lab, (a, b) in zip(labels, offsets)]
        return {
            "input_ids": enc["input_ids"][0],
            "attention_mask": enc["attention_mask"][0],
            "labels": torch.tensor(labels),
        }


# ---------- model ----------

class PhoBERTMultiTask(nn.Module):
    def __init__(self, base=BASE):
        super().__init__()
        self.encoder = AutoModel.from_pretrained(base)
        h = self.encoder.config.hidden_size
        self.drop = nn.Dropout(0.1)
        self.intent_head = nn.Linear(h, len(INTENTS))
        self.inj_head = nn.Linear(h, 2)
        self.ner_head = nn.Linear(h, len(BIO))

    def forward(self, input_ids, attention_mask):
        out = self.encoder(input_ids=input_ids, attention_mask=attention_mask)
        seq = out.last_hidden_state          # (B, T, H) — for NER
        cls = self.drop(seq[:, 0])           # (B, H) — [CLS] for seq tasks
        return {
            "intent": self.intent_head(cls),
            "injection": self.inj_head(cls),
            "ner": self.ner_head(self.drop(seq)),
        }


def cycle(loader):
    while True:
        yield from loader


def evaluate(model, tok, device, max_len):
    model.eval()
    res = {}
    with torch.no_grad():
        # intent macro-F1
        rows = read("intent_val.jsonl")
        ds = SeqDataset(rows, tok, INTENT2I, max_len)
        y, p = [], []
        for b in DataLoader(ds, batch_size=32):
            logits = model(b["input_ids"].to(device), b["attention_mask"].to(device))["intent"]
            p += logits.argmax(-1).cpu().tolist()
            y += b["label"].tolist()
        res["intent_macro_f1"] = round(f1_score(y, p, average="macro", zero_division=0), 4)
        # injection P/R (label 1)
        inj = read("injection_val.jsonl") if (DATA / "injection_val.jsonl").exists() else []
        if inj:
            ds = SeqDataset(inj, tok, {"0": 0, "1": 1}, max_len)
            y, p = [], []
            for b in DataLoader(ds, batch_size=32):
                logits = model(b["input_ids"].to(device), b["attention_mask"].to(device))["injection"]
                p += logits.argmax(-1).cpu().tolist()
                y += b["label"].tolist()
            pr, rc, f1, _ = precision_recall_fscore_support(y, p, labels=[1], average="micro",
                                                            zero_division=0)
            res["injection_p"], res["injection_r"] = round(pr, 4), round(rc, 4)
    model.train()
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description="PhoBERT multi-task trainer")
    ap.add_argument("--epochs", type=int, default=10)
    ap.add_argument("--batch-size", type=int, default=16)
    ap.add_argument("--lr", type=float, default=2e-5)
    ap.add_argument("--max-len", type=int, default=128)
    ap.add_argument("--patience", type=int, default=3)
    args = ap.parse_args()

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"device = {device}"
          + (f" ({torch.cuda.get_device_name(0)})" if device == "cuda" else " (NO GPU — slow!)"))

    tok = AutoTokenizer.from_pretrained(BASE, use_fast=True)
    model = PhoBERTMultiTask().to(device)

    cw = json.loads((DATA / "class_weights.json").read_text(encoding="utf-8"))
    intent_w = torch.tensor([cw["intent"].get(lab, 1.0) for lab in INTENTS], dtype=torch.float).to(device)
    ce_intent = nn.CrossEntropyLoss(weight=intent_w)
    ce_inj = nn.CrossEntropyLoss()
    ce_ner = nn.CrossEntropyLoss(ignore_index=-100)

    bs = args.batch_size
    intent_dl = DataLoader(SeqDataset(read("intent_train.jsonl"), tok, INTENT2I, args.max_len),
                           batch_size=bs, shuffle=True)
    inj_rows = read("injection_train.jsonl") if (DATA / "injection_train.jsonl").exists() else []
    inj_dl = DataLoader(SeqDataset(inj_rows, tok, {"0": 0, "1": 1}, args.max_len),
                        batch_size=bs, shuffle=True) if inj_rows else None
    ner_dl = DataLoader(NerDataset(read("ner_train.jsonl"), tok, args.max_len),
                        batch_size=bs, shuffle=True)

    opt = torch.optim.AdamW(model.parameters(), lr=args.lr)
    steps = max(len(intent_dl), len(ner_dl), len(inj_dl) if inj_dl else 0)
    inj_it = cycle(inj_dl) if inj_dl else None
    ner_it, intent_it = cycle(ner_dl), cycle(intent_dl)

    OUT.mkdir(parents=True, exist_ok=True)
    best_f1, since = -1.0, 0
    for epoch in range(1, args.epochs + 1):
        model.train()
        for _ in range(steps):
            opt.zero_grad()
            bi = next(intent_it)
            out = model(bi["input_ids"].to(device), bi["attention_mask"].to(device))
            loss = W_INTENT * ce_intent(out["intent"], bi["label"].to(device))
            bn = next(ner_it)
            out = model(bn["input_ids"].to(device), bn["attention_mask"].to(device))
            loss = loss + W_NER * ce_ner(out["ner"].reshape(-1, len(BIO)),
                                         bn["labels"].to(device).reshape(-1))
            if inj_it:
                bj = next(inj_it)
                out = model(bj["input_ids"].to(device), bj["attention_mask"].to(device))
                loss = loss + W_INJ * ce_inj(out["injection"], bj["label"].to(device))
            loss.backward()
            opt.step()

        metrics = evaluate(model, tok, device, args.max_len)
        print(f"epoch {epoch}: {metrics}")
        if metrics["intent_macro_f1"] > best_f1:
            best_f1, since = metrics["intent_macro_f1"], 0
            torch.save(model.state_dict(), OUT / "checkpoint.pt")
            (OUT / "labels.json").write_text(json.dumps(
                {"intents": INTENTS, "bio": BIO, "base": BASE, "max_len": args.max_len},
                ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"  ✓ saved (val intent macro-F1 {best_f1})")
        else:
            since += 1
            if since >= args.patience:
                print(f"early stop (no improvement {args.patience} epochs)")
                break

    print(f"\nBEST val intent macro-F1 = {best_f1}  (target ≥ 0.85)")
    print(f"label counts (intent train): {Counter(r['label'] for r in read('intent_train.jsonl'))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

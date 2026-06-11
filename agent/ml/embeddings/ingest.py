"""KB ingest pipeline: docs/kb/*.md → kb_chunks (bge-m3 dense + sparse).

Run from agent/: uv run python ml/embeddings/ingest.py
Idempotent: deletes all chunks of a doc_id before re-inserting; bumps kb_meta.kb_version.
"""

import json
import os
import re
import sys
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(AGENT_DIR))

from app.config import REPO_ROOT, load_dotenv_if_present  # noqa: E402

KB_DIR = REPO_ROOT / "docs" / "kb"
# Rough token estimate for Vietnamese text (~3 chars/token); chunk target 300–500 tokens.
MAX_CHUNK_TOKENS = 500


def estimate_tokens(text: str) -> int:
    return len(text) // 3


def split_section(body: str) -> list[str]:
    """Split an oversized section at paragraph boundaries, never inside a table."""
    blocks: list[str] = []
    current: list[str] = []
    in_table = False
    for line in body.splitlines():
        is_table_line = line.lstrip().startswith("|")
        if current and not line.strip() and not in_table:
            blocks.append("\n".join(current))
            current = []
            continue
        in_table = is_table_line
        if line.strip():
            current.append(line)
    if current:
        blocks.append("\n".join(current))

    pieces: list[str] = []
    buf: list[str] = []
    for block in blocks:
        candidate = "\n\n".join(buf + [block])
        if buf and estimate_tokens(candidate) > MAX_CHUNK_TOKENS:
            pieces.append("\n\n".join(buf))
            buf = [block]
        else:
            buf.append(block)
    if buf:
        pieces.append("\n\n".join(buf))
    return pieces


def split_faq(body: str) -> list[tuple[str, str]] | None:
    """For flat FAQ files (no ##): each bold '**question?**' opens its own chunk."""
    question_re = re.compile(r"^\*\*(.+?)\*\*\s*$")
    pairs: list[tuple[str, list[str]]] = []
    for line in body.splitlines():
        m = question_re.match(line.strip())
        if m:
            pairs.append((m.group(1).strip(), [line]))
        elif pairs:
            pairs[-1][1].append(line)
    if len(pairs) < 2:
        return None
    return [(q, "\n".join(lines).strip()) for q, lines in pairs]


def chunk_file(path: Path) -> list[dict]:
    """One chunk per '## ' section (split further if oversized), intro as its own chunk.

    Files without any '## ' (e.g. 08-faq-chung.md) use bold questions as the
    section boundary instead — one chunk per Q&A pair.
    """
    text = path.read_text(encoding="utf-8")
    h1_match = re.search(r"^# (.+)$", text, flags=re.MULTILINE)
    doc_title = h1_match.group(1).strip() if h1_match else path.stem

    sections: list[tuple[str, str]] = []  # (heading, body)
    current_heading = doc_title  # intro before the first ## belongs to the H1
    current_lines: list[str] = []
    for line in text.splitlines():
        if line.startswith("# ") and not line.startswith("## "):
            continue  # H1 already captured as doc_title
        if line.startswith("## "):
            if "\n".join(current_lines).strip():
                sections.append((current_heading, "\n".join(current_lines).strip()))
            current_heading = line[3:].strip()
            current_lines = []
        else:
            current_lines.append(line)
    if "\n".join(current_lines).strip():
        sections.append((current_heading, "\n".join(current_lines).strip()))

    has_h2 = any(line.startswith("## ") for line in text.splitlines())
    if not has_h2 and sections:
        faq_pairs = split_faq(sections[0][1])
        if faq_pairs:
            sections = faq_pairs

    chunks: list[dict] = []
    order = 0
    for heading, body in sections:
        for piece in split_section(body) if estimate_tokens(body) > MAX_CHUNK_TOKENS else [body]:
            chunks.append(
                {
                    "doc_id": path.name,
                    "content": f"[Tài liệu: {doc_title}] [Mục: {heading}]\n{piece}",
                    "metadata": {"heading": heading, "order": order},
                }
            )
            order += 1
    return chunks


def main() -> int:
    load_dotenv_if_present()
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        print("ERROR: SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY missing (env or root .env)")
        return 1

    from FlagEmbedding import BGEM3FlagModel
    from supabase import create_client

    files = sorted(KB_DIR.glob("*.md"))
    if not files:
        print(f"ERROR: no .md files in {KB_DIR}")
        return 1

    all_chunks: list[dict] = []
    for f in files:
        all_chunks.extend(chunk_file(f))
    print(f"Chunked {len(files)} files -> {len(all_chunks)} chunks. Loading bge-m3...")

    # BGE_M3_MODEL may point to a local dir (skips HF snapshot of unused files, e.g. ONNX)
    model = BGEM3FlagModel(os.environ.get("BGE_M3_MODEL", "BAAI/bge-m3"), use_fp16=False)
    output = model.encode(
        [c["content"] for c in all_chunks],
        return_dense=True,
        return_sparse=True,
        return_colbert_vecs=False,
    )
    dense = output["dense_vecs"]
    sparse = output["lexical_weights"]

    client = create_client(url, key)
    for doc_id in {c["doc_id"] for c in all_chunks}:
        client.table("kb_chunks").delete().eq("doc_id", doc_id).execute()

    rows = [
        {
            "doc_id": c["doc_id"],
            "content": c["content"],
            "dense_vec": [float(x) for x in dense[i]],
            "sparse_weights": {k: float(v) for k, v in sparse[i].items()},
            "metadata": c["metadata"],
        }
        for i, c in enumerate(all_chunks)
    ]
    for start in range(0, len(rows), 50):
        client.table("kb_chunks").insert(rows[start : start + 50]).execute()

    meta = client.table("kb_meta").select("value").eq("key", "kb_version").execute()
    version = int(json.loads(json.dumps(meta.data[0]["value"]))) + 1
    client.table("kb_meta").update({"value": version}).eq("key", "kb_version").execute()

    print(f"Ingested: {len(files)} files, {len(rows)} chunks, kb_version={version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Env helpers shared by the service and CLI scripts."""

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def load_dotenv_if_present() -> None:
    """Minimal .env loader (repo root) — no extra dependency, never overrides real env."""
    env_file = REPO_ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())

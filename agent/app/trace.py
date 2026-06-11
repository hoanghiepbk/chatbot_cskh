"""trace_events writer (service role).

HARD RULE: callers must pass payloads already free of raw PII (masked upstream).
As a second line of defense this helper scans the serialized payload for raw
phone/email patterns and raises ValueError before anything touches the DB.
"""

import json
import os
import re
from typing import Any

RAW_PHONE_RE = re.compile(r"(?<!\d)(?:\+84|0)(?:[\s.\-]?\d){9}(?!\d)")
RAW_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.\-]+")

_client: Any = None


def _get_client():
    global _client
    if _client is None:
        from supabase import create_client

        _client = create_client(
            os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"]
        )
    return _client


async def log_trace(
    conversation_id: str | None,
    step_type: str,
    payload: dict,
    latency_ms: int | None = None,
    cost_usd: float | None = None,
    prompt_version: int | None = None,
    policy_version: int | None = None,
) -> None:
    serialized = json.dumps(payload, ensure_ascii=False)
    if RAW_PHONE_RE.search(serialized) or RAW_EMAIL_RE.search(serialized):
        raise ValueError("raw PII detected in trace payload — mask upstream first")

    row = {
        "conversation_id": conversation_id,
        "step_type": step_type,
        "payload": payload,
        "latency_ms": latency_ms,
        "cost_usd": cost_usd,
        "prompt_version": prompt_version,
        "policy_version": policy_version,
    }
    _get_client().table("trace_events").insert(row).execute()

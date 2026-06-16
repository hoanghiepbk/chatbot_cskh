"""Prompt/Policy registry API (TIP-013) — version + activate the SOFT config
(system-prompt text + policy parameters) WITHOUT a redeploy, with in-process
hot-reload of the chat graph.

SAFETY BOUNDARY (Blueprint §6.5 — policy-as-data):
The registry carries ONLY soft levers — prompt wording + policy numbers
(refund_cap_vnd, write_value_cap_vnd, escalate_confidence_below,
injection_threshold, forbidden_topics). It can NEVER disable the HARD guardrail
rules, which live in CODE (guardrails/output.py:apply_hard_rules, tools/*). Example:
a policy that omits refund_cap_vnd falls back to the 2,000,000 code default — the
5,000,000 refund block still fires. Activating only swaps text/params; the rule
engine is untouched.

AUTH: Bearer STAFF_API_TOKEN — same demo-grade shared token as the staff API
(see app/api/staff.py threat-model note). Production: Supabase Auth + staff role.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.api.staff import require_staff  # reuse the Bearer STAFF_API_TOKEN check

router = APIRouter(prefix="/registry", dependencies=[Depends(require_staff)])
log = logging.getLogger("xecare.registry")

PREVIEW_LEN = 200
PROMPT_NAME = "system_main"
POLICY_NAME = "core_policy"
# Soft numeric levers — validated for type only; MISSING is allowed (code defaults apply).
POLICY_NUMERIC_KEYS = (
    "refund_cap_vnd",
    "write_value_cap_vnd",
    "escalate_confidence_below",
    "injection_threshold",
)


class PromptCreate(BaseModel):
    content: str
    name: str = PROMPT_NAME


class PolicyCreate(BaseModel):
    rules: dict
    name: str = POLICY_NAME


def _validate_policy_rules(rules: dict) -> None:
    """Schema-light validation: numeric levers must be numbers (not bool), and
    forbidden_topics (if present) a list of strings. Missing keys are allowed —
    the hard rules carry code-level defaults, so omitting a key never disables them."""
    if not isinstance(rules, dict):
        raise HTTPException(status_code=400, detail="rules must be a JSON object")
    for key in POLICY_NUMERIC_KEYS:
        if key in rules:
            value = rules[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise HTTPException(status_code=400, detail=f"policy.{key} must be a number")
    topics = rules.get("forbidden_topics")
    if topics is not None and not (
        isinstance(topics, list) and all(isinstance(t, str) for t in topics)
    ):
        raise HTTPException(
            status_code=400, detail="policy.forbidden_topics must be a list of strings"
        )


def _next_version(supabase, table: str, name: str) -> int:
    rows = (
        supabase.table(table)
        .select("version")
        .eq("name", name)
        .order("version", desc=True)
        .limit(1)
        .execute()
        .data
    )
    return (rows[0]["version"] + 1) if rows else 1


def _hot_reload(app) -> dict:
    """Re-read the active registry into app.state AND rebuild the chat graph.
    The graph closes over GraphDeps (prompt/policy frozen at build, routing
    thresholds computed once) — so a swap is required; mutating app.state alone
    would not pick up new thresholds. Lazy import avoids a main<->registry cycle."""
    from app.main import apply_active_registry

    return apply_active_registry(app)


# ---------------- prompts ----------------


@router.get("/prompts")
async def list_prompts(request: Request, name: str = PROMPT_NAME):
    rows = (
        request.app.state.supabase.table("prompt_registry")
        .select("id, name, version, active, created_at, content")
        .eq("name", name)
        .order("version")
        .execute()
        .data
        or []
    )
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "version": r["version"],
            "active": r["active"],
            "created_at": r["created_at"],
            "preview": (r.get("content") or "")[:PREVIEW_LEN],
        }
        for r in rows
    ]


@router.get("/prompts/{version}")
async def get_prompt(version: int, request: Request, name: str = PROMPT_NAME):
    rows = (
        request.app.state.supabase.table("prompt_registry")
        .select("*")
        .eq("name", name)
        .eq("version", version)
        .execute()
        .data
    )
    if not rows:
        raise HTTPException(status_code=404, detail="prompt version not found")
    return rows[0]


@router.post("/prompts")
async def create_prompt(body: PromptCreate, request: Request):
    supabase = request.app.state.supabase
    version = _next_version(supabase, "prompt_registry", body.name)
    supabase.table("prompt_registry").insert(
        {"name": body.name, "version": version, "content": body.content, "active": False}
    ).execute()
    return {"name": body.name, "version": version, "active": False}


@router.post("/prompts/{version}/activate")
async def activate_prompt(version: int, request: Request, name: str = PROMPT_NAME):
    supabase = request.app.state.supabase
    rows = (
        supabase.table("prompt_registry")
        .select("id")
        .eq("name", name)
        .eq("version", version)
        .execute()
        .data
    )
    if not rows:
        raise HTTPException(status_code=404, detail="prompt version not found")
    # single-active invariant: deactivate the whole name, then activate the target
    supabase.table("prompt_registry").update({"active": False}).eq("name", name).execute()
    supabase.table("prompt_registry").update({"active": True}).eq("name", name).eq(
        "version", version
    ).execute()
    reg = _hot_reload(request.app)
    log.info("registry: activated prompt %s v%s (hot-reload)", name, version)
    return {"name": name, "version": version, "active": True,
            "prompt_version": reg["prompt"]["version"]}


# ---------------- policies ----------------


@router.get("/policies")
async def list_policies(request: Request, name: str = POLICY_NAME):
    rows = (
        request.app.state.supabase.table("policy_registry")
        .select("id, name, version, active, created_at, rules")
        .eq("name", name)
        .order("version")
        .execute()
        .data
        or []
    )
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "version": r["version"],
            "active": r["active"],
            "created_at": r["created_at"],
            "rules": r.get("rules"),
        }
        for r in rows
    ]


@router.get("/policies/{version}")
async def get_policy(version: int, request: Request, name: str = POLICY_NAME):
    rows = (
        request.app.state.supabase.table("policy_registry")
        .select("*")
        .eq("name", name)
        .eq("version", version)
        .execute()
        .data
    )
    if not rows:
        raise HTTPException(status_code=404, detail="policy version not found")
    return rows[0]


@router.post("/policies")
async def create_policy(body: PolicyCreate, request: Request):
    _validate_policy_rules(body.rules)
    supabase = request.app.state.supabase
    version = _next_version(supabase, "policy_registry", body.name)
    supabase.table("policy_registry").insert(
        {"name": body.name, "version": version, "rules": body.rules, "active": False}
    ).execute()
    return {"name": body.name, "version": version, "active": False}


@router.post("/policies/{version}/activate")
async def activate_policy(version: int, request: Request, name: str = POLICY_NAME):
    supabase = request.app.state.supabase
    rows = (
        supabase.table("policy_registry")
        .select("rules")
        .eq("name", name)
        .eq("version", version)
        .execute()
        .data
    )
    if not rows:
        raise HTTPException(status_code=404, detail="policy version not found")
    _validate_policy_rules(rows[0].get("rules") or {})
    supabase.table("policy_registry").update({"active": False}).eq("name", name).execute()
    supabase.table("policy_registry").update({"active": True}).eq("name", name).eq(
        "version", version
    ).execute()
    reg = _hot_reload(request.app)
    log.info("registry: activated policy %s v%s (hot-reload)", name, version)
    return {"name": name, "version": version, "active": True,
            "policy_version": reg["policy"]["version"]}

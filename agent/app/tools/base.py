"""Tool layer primitives: ToolRefused + the single tracing entrypoint.

Blueprint §6.3: hard business rules live INSIDE the tools, independent of any
LLM judgement. A tool that refuses raises ToolRefused(code, message) — the
caller maps codes to replies, it can never override the rule.
"""

import time
from typing import Any, Awaitable, Callable

# trace callable bound to a conversation by the caller:
# async (step_type: str, payload: dict, latency_ms: int | None) -> None
TraceFn = Callable[..., Awaitable[None]]


class ToolRefused(Exception):
    """A hard rule inside a tool blocked the call."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def _safe_result(result: Any) -> Any:
    """Compact, PII-free summary of a tool result for trace payloads."""
    if isinstance(result, list):
        return {"count": len(result), "ids": [r.get("id") for r in result if isinstance(r, dict)]}
    if isinstance(result, dict):
        return {k: result.get(k) for k in ("id", "status", "booked") if k in result}
    return result


async def run_tool(trace: TraceFn, name: str, fn: Callable[..., Awaitable[Any]], **args) -> Any:
    """Run a tool and trace the call — success AND refusal both leave a tool_call
    trace. This is the only sanctioned way to invoke a tool."""
    safe_args = {k: v for k, v in args.items() if v is not None}
    start = time.perf_counter()
    try:
        result = await fn(**args)
    except ToolRefused as refused:
        await trace(
            "tool_call",
            {"tool": name, "args": safe_args, "refused": refused.code},
            latency_ms=int((time.perf_counter() - start) * 1000),
        )
        raise
    await trace(
        "tool_call",
        {"tool": name, "args": safe_args, "result": _safe_result(result)},
        latency_ms=int((time.perf_counter() - start) * 1000),
    )
    return result

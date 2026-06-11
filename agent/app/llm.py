"""LLM client layer — the ONLY place that imports anthropic.

Every LLM call in the codebase goes through LLMClient so tests can swap in a
FakeLLM and CI never needs an API key or network.
"""

import time
from dataclasses import dataclass
from typing import Protocol

MODEL_SONNET = "claude-sonnet-4-5"  # main agent (faq answers)
MODEL_HAIKU = "claude-haiku-4-5"  # router / groundedness / chitchat

# USD per 1M tokens (input, output) — for cost_usd computed from real usage.
PRICING = {
    MODEL_SONNET: (3.00, 15.00),
    MODEL_HAIKU: (1.00, 5.00),
}


@dataclass
class LLMResult:
    text: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int


class LLMClient(Protocol):
    async def complete(
        self,
        model: str,
        system: str,
        messages: list[dict],
        max_tokens: int,
        json_mode: bool = False,
    ) -> LLMResult: ...


def compute_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    price_in, price_out = PRICING.get(model, (0.0, 0.0))
    return (input_tokens * price_in + output_tokens * price_out) / 1_000_000


class AnthropicClient:
    """Real client (env ANTHROPIC_API_KEY). json_mode uses an assistant '{'
    prefill — supported on the 4.5-generation models used here (removed in 4.6+)."""

    def __init__(self):
        from anthropic import AsyncAnthropic

        self._client = AsyncAnthropic()

    async def complete(
        self,
        model: str,
        system: str,
        messages: list[dict],
        max_tokens: int,
        json_mode: bool = False,
    ) -> LLMResult:
        request_messages = list(messages)
        if json_mode:
            request_messages.append({"role": "assistant", "content": "{"})

        start = time.perf_counter()
        response = await self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=request_messages,
        )
        latency_ms = int((time.perf_counter() - start) * 1000)

        text = "".join(block.text for block in response.content if block.type == "text")
        if json_mode:
            text = "{" + text

        return LLMResult(
            text=text,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cost_usd=compute_cost(
                model, response.usage.input_tokens, response.usage.output_tokens
            ),
            latency_ms=latency_ms,
        )

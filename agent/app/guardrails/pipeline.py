"""Guardrail-in pipeline: pre_gate emergency + PII mask + injection score.

GuardrailInResult is a contract — TIP-012a swaps the engines (PhoBERT) without
changing this signature or the caller (LangGraph nodes, TIP-005).
"""

from dataclasses import dataclass, field

from app.guardrails.injection import score_injection
from app.guardrails.pii import PIISession
from app.guardrails.pre_gate import check_emergency


@dataclass
class GuardrailInResult:
    masked_text: str
    emergency: bool
    injection_score: float
    pii_found: dict[str, int] = field(default_factory=dict)  # counts per type, no values


def run_guardrail_in(text: str, pii_session: PIISession) -> GuardrailInResult:
    emergency = check_emergency(text)
    injection = score_injection(text)
    masked = pii_session.mask(text)
    return GuardrailInResult(
        masked_text=masked,
        emergency=emergency,
        injection_score=injection,
        pii_found=dict(pii_session.last_found),
    )

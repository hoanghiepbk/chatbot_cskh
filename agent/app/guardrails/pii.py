"""Two-way PII masking per session (Blueprint §6.1).

The LLM only ever sees placeholders ([PHONE_1], [PLATE_1], [ID_1], [EMAIL_1],
[PHONE_KH]); the placeholder↔value map lives server-side in PIISession and is
applied in reverse after guardrail_out.
"""

import re

# Phone: 0XXXXXXXXX or +84XXXXXXXXX, allowing space/dot/dash between digit groups.
PHONE_RE = re.compile(r"(?<!\d)(?:\+84|0)(?:[\s.\-]?\d){9}(?!\d)")
# VN license plates: 29A-123.45, 30F-12345, 29-AB 123.45, 59X1-234.56
PLATE_RE = re.compile(r"(?<![\w.])\d{2}[-\s]?[A-Za-z]{1,2}\d?[-\s]?\d{3}[.\s]?\d{1,2}(?![\w.])")
# Standalone 12-digit CCCD or 9-digit CMND — lookarounds keep km/money intact.
ID_RE = re.compile(r"(?<!\d)(?:\d{12}|\d{9})(?!\d)")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.\-]+")

PLACEHOLDER_RE = re.compile(r"\[(?:PHONE_KH|(?:PHONE|PLATE|ID|EMAIL)_\d+)\]")


def normalize_phone(raw: str) -> str:
    """Canonical E.164 (+84...) for map comparison."""
    digits = re.sub(r"[\s.\-]", "", raw)
    if digits.startswith("0"):
        return "+84" + digits[1:]
    return digits


class PIISession:
    """Per-conversation placeholder↔value map. Same value → same placeholder."""

    def __init__(self, customer_phone: str | None = None):
        self._value_by_placeholder: dict[str, str] = {}
        self._placeholder_by_key: dict[tuple[str, str], str] = {}
        self._counters: dict[str, int] = {}
        self.last_found: dict[str, int] = {}
        if customer_phone:
            key = ("PHONE", normalize_phone(customer_phone))
            self._placeholder_by_key[key] = "[PHONE_KH]"
            self._value_by_placeholder["[PHONE_KH]"] = customer_phone

    def _placeholder_for(self, pii_type: str, surface: str, canonical: str) -> str:
        key = (pii_type, canonical)
        if key not in self._placeholder_by_key:
            self._counters[pii_type] = self._counters.get(pii_type, 0) + 1
            placeholder = f"[{pii_type}_{self._counters[pii_type]}]"
            self._placeholder_by_key[key] = placeholder
            self._value_by_placeholder[placeholder] = surface
        return self._placeholder_by_key[key]

    def mask(self, text: str) -> str:
        """Replace PII with numbered placeholders; updates self.last_found counts."""
        found: dict[str, int] = {}

        def repl(pii_type: str, canonicalize):
            def _sub(m: re.Match) -> str:
                surface = m.group(0)
                found[pii_type] = found.get(pii_type, 0) + 1
                return self._placeholder_for(pii_type, surface, canonicalize(surface))

            return _sub

        # Order matters: email first (digits inside), then plate (letters anchor),
        # then phone, then bare ID numbers. Placeholders never re-match.
        text = EMAIL_RE.sub(repl("EMAIL", str.lower), text)
        text = PLATE_RE.sub(repl("PLATE", lambda s: re.sub(r"[\s.\-]", "", s).upper()), text)
        text = PHONE_RE.sub(repl("PHONE", normalize_phone), text)
        text = ID_RE.sub(repl("ID", str), text)
        self.last_found = found
        return text

    def unmask(self, text: str) -> str:
        """Replace every known placeholder back with its original value."""
        return PLACEHOLDER_RE.sub(
            lambda m: self._value_by_placeholder.get(m.group(0), m.group(0)), text
        )

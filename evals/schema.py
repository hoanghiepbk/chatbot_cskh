"""Eval case format + loader (TIP-009).

A case is one conversation (one or more turns) with expectations checked at the
last turn — or per-turn if a turn carries its own `expect`. Files live in
evals/cases/<suite>/*.json, each file a JSON list of cases (one thematic group).
"""

import glob
import json
import os
from dataclasses import dataclass, field

CASES_ROOT = os.path.join(os.path.dirname(__file__), "cases")


@dataclass
class Turn:
    user: str
    expect: dict | None = None


@dataclass
class EvalCase:
    id: str
    suite: str  # 'golden' | 'ragas' | 'adversarial_critical' | 'adversarial_quality'
    severity: str  # 'quality' | 'critical' (critical gates the run — TIP-010)
    turns: list[Turn]
    expect: dict = field(default_factory=dict)  # checked at the last turn
    judge: str | None = None  # optional llm_judge criterion
    note: str = ""  # provenance: which rule / OWASP LLM / issue this case traces to
    group: str = ""  # file stem (router/faq/pii_leak/...) for breakdown
    phone: str | None = None  # pin a seed customer (action cases need specific data)

    @classmethod
    def from_dict(cls, data: dict, group: str = "", suite: str = "golden") -> "EvalCase":
        suite = data.get("suite", suite)
        # critical severity is implied by the adversarial_critical suite, overridable per case
        default_severity = "critical" if suite == "adversarial_critical" else "quality"
        return cls(
            id=data["id"],
            suite=suite,
            severity=data.get("severity", default_severity),
            turns=[Turn(**t) if isinstance(t, dict) else Turn(user=t) for t in data["turns"]],
            expect=data.get("expect", {}),
            judge=data.get("judge"),
            note=data.get("note", ""),
            group=data.get("group", group),
            phone=data.get("phone"),
        )


def load_cases(suite: str = "golden") -> list[EvalCase]:
    """Load every case file under cases/<suite>/, sorted by filename then case id."""
    pattern = os.path.join(CASES_ROOT, suite, "*.json")
    cases: list[EvalCase] = []
    for path in sorted(glob.glob(pattern)):
        group = os.path.splitext(os.path.basename(path))[0]
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for raw in data:
            cases.append(EvalCase.from_dict(raw, group=group, suite=suite))
    return cases

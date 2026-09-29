"""Core data model. Deliberately small: every field here must be obtainable
from a system of record, or it does not belong in the model."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NONE = "none"


@dataclass(frozen=True)
class Defect:
    id: str
    title: str
    affected_versions: tuple[str, ...]
    fixed_in: str | None
    feature_tags: tuple[str, ...]
    status: str


@dataclass(frozen=True)
class CaseEvent:
    at: datetime
    kind: str          # note | attachment | status | escalation | reassignment
    text: str


@dataclass(frozen=True)
class Case:
    id: str
    account: str
    platform: str
    sw_version: str
    symptom_class: str
    symptom_text: str
    features_enabled: tuple[str, ...]
    region: str
    created_at: datetime
    events: tuple[CaseEvent, ...] = ()
    # Populated only for closed cases, and only visible to the harness.
    initial_hypothesis: str | None = None
    actual_root_cause: str | None = None
    closed_at: datetime | None = None


@dataclass(frozen=True)
class Finding:
    """One assertion by one module. `sources` is mandatory and must be
    non-empty: a finding with no retrievable ID must never be emitted."""
    module: str
    kind: str                     # candidate | eliminated | trap | cluster | gap
    statement: str
    confidence: Confidence
    sources: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.sources:
            raise ValueError(f"{self.module}: finding without sources: {self.statement!r}")


@dataclass
class Brief:
    case_id: str
    generated_at: datetime
    findings: list[Finding] = field(default_factory=list)
    abstained: bool = False
    abstain_reason: str | None = None

    def of_kind(self, kind: str) -> list[Finding]:
        return [f for f in self.findings if f.kind == kind]

    def render(self) -> str:
        out = [f"CASE INTELLIGENCE BRIEF - {self.case_id}",
               f"generated {self.generated_at:%Y-%m-%d %H:%M}", ""]
        if self.abstained:
            out += ["NO CONFIDENT CANDIDATE", f"  {self.abstain_reason}", ""]
        for kind, header in (
            ("trap", "KNOWN TRAPS - where this went wrong before"),
            ("candidate", "CANDIDATES"),
            ("eliminated", "RULED OUT"),
            ("cluster", "FLEET SIGNAL"),
            ("gap", "MISSING DATA / ESCALATION GAPS"),
        ):
            items = self.of_kind(kind)
            if not items:
                continue
            out.append(header)
            for f in items:
                out.append(f"  [{f.confidence.value:<6}] {f.statement}")
                out.append(f"           sources: {', '.join(f.sources)}")
            out.append("")
        return "\n".join(out)

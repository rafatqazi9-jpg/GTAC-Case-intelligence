"""Module 07 - escalation package. Assembles what the BU requires and flags
what is missing, so the package does not bounce."""
from __future__ import annotations

from ..adapters.base import Adapter, EvidenceWindow
from ..models import Case, Confidence, Finding

MODULE = "m07_escalation_package"

_NEGATIVE_MARKERS = (
    "not collected",
    "not been collected",
    "not attached",
    "not available",
    "not provided",
    "not captured",
    "not present",
    "missing",
    "pending",
    "awaiting",
    "unable to collect",
    "could not collect",
)


def _field_is_present(field: str, corpus: str) -> bool:
    """Avoid treating a field mention as proof when explicitly marked absent."""
    key = field.lower()
    for sentence in corpus.splitlines():
        if key not in sentence:
            continue
        if any(marker in sentence for marker in _NEGATIVE_MARKERS):
            return False
        return True
    return False


def run(case: Case, adapter: Adapter, window: EvidenceWindow) -> list[Finding]:
    required = adapter.required_escalation_fields(case.platform)
    corpus = "\n".join(e.text.lower() for e in case.events)

    present: list[str] = []
    missing: list[str] = []
    for field in required:
        if field.lower() == "sw_version" and case.sw_version:
            present.append(field)
        elif _field_is_present(field, corpus):
            present.append(field)
        else:
            missing.append(field)

    findings: list[Finding] = []
    if missing:
        findings.append(Finding(
            module=MODULE, kind="gap",
            statement=("Escalation package incomplete - missing: "
                       + ", ".join(missing)),
            confidence=Confidence.HIGH, sources=(case.id,),
        ))
    if present:
        findings.append(Finding(
            module=MODULE, kind="gap",
            statement=f"Already on the case: {', '.join(present)}",
            confidence=Confidence.HIGH, sources=(case.id,),
        ))
    return findings

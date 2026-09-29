"""Module 07 - escalation package. Assembles what the BU requires and flags
what is missing, so the package does not bounce."""
from __future__ import annotations

from ..adapters.base import Adapter, EvidenceWindow
from ..models import Case, Confidence, Finding

MODULE = "m07_escalation_package"


def run(case: Case, adapter: Adapter, window: EvidenceWindow) -> list[Finding]:
    required = adapter.required_escalation_fields(case.platform)
    corpus = " ".join(e.text for e in case.events).lower()

    present: list[str] = []
    missing: list[str] = []
    for field in required:
        key = field.lower()
        if key in corpus or (key == "sw_version" and case.sw_version):
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

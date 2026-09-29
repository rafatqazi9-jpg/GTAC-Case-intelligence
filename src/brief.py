"""Assembles the brief and applies the confidence policy.

The policy is the product. If nothing clears the threshold, the brief abstains
and reports what was eliminated. Do not soften this to make output look fuller.
"""
from __future__ import annotations

from datetime import datetime

from .adapters.base import Adapter, EvidenceWindow
from .models import Brief, Confidence, Finding
from .modules import (m01_defect_exposure, m05_known_traps,
                      m06_fleet_correlation, m07_escalation_package)

MODULES = (m01_defect_exposure, m05_known_traps,
           m06_fleet_correlation, m07_escalation_package)

_RANK = {Confidence.HIGH: 3, Confidence.MEDIUM: 2,
         Confidence.LOW: 1, Confidence.NONE: 0}


def build(case_id: str, adapter: Adapter, window: EvidenceWindow,
          threshold: Confidence = Confidence.MEDIUM) -> Brief:
    case = adapter.get_case(case_id, window)
    findings: list[Finding] = []
    for mod in MODULES:
        findings.extend(mod.run(case, adapter, window))

    brief = Brief(case_id=case_id, generated_at=window.cutoff, findings=findings)

    actionable = [f for f in findings
                  if f.kind in ("candidate", "trap")
                  and _RANK[f.confidence] >= _RANK[threshold]]
    if not actionable:
        brief.abstained = True
        n_elim = len(brief.of_kind("eliminated"))
        # Only point at RULED OUT when there is a RULED OUT section to read.
        # An abstention that eliminated nothing is a much weaker result than
        # one that narrowed the field, and the wording has to admit that
        # rather than sending the reader to an empty section.
        if n_elim:
            narrowed = f"{n_elim} possibilit(ies) eliminated - see RULED OUT."
        else:
            narrowed = ("Nothing was eliminated either: the available evidence "
                        "ruled nothing out.")
        brief.abstain_reason = (
            f"No candidate at {threshold.value} confidence or above. "
            f"{narrowed} "
            "Treat this as an open case, not a solved one.")
    return brief


def order_findings(brief: Brief) -> None:
    """Highest confidence first within each kind. Stable otherwise."""
    brief.findings.sort(key=lambda f: -_RANK[f.confidence])

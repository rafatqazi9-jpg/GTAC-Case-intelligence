"""Module 05 - known traps.

Prior closed cases that presented this way WHERE THE FIRST HYPOTHESIS WAS
WRONG. KB articles record what worked; nothing at GTAC records the wrong turns.
"""
from __future__ import annotations

from collections import Counter

from ..adapters.base import Adapter, EvidenceWindow
from ..models import Case, Confidence, Finding

MODULE = "m05_known_traps"


def run(case: Case, adapter: Adapter, window: EvidenceWindow) -> list[Finding]:
    prior = adapter.similar_closed_cases(case.symptom_class, case.platform, window)
    traps = [
        c for c in prior
        if c.initial_hypothesis and c.actual_root_cause
        and c.initial_hypothesis.strip().lower() not in c.actual_root_cause.strip().lower()
    ]
    if not prior:
        return []

    findings: list[Finding] = []
    rate = len(traps) / len(prior)

    # Group by (wrong hypothesis -> actual cause) and report the recurring ones.
    pairs = Counter((c.initial_hypothesis, c.actual_root_cause) for c in traps)
    for (hyp, cause), n in pairs.most_common(4):
        ids = tuple(c.id for c in traps
                    if c.initial_hypothesis == hyp and c.actual_root_cause == cause)
        conf = (Confidence.HIGH if n >= 4 else
                Confidence.MEDIUM if n >= 2 else Confidence.LOW)
        findings.append(Finding(
            module=MODULE, kind="trap",
            statement=(f"{n} prior case(s) presented this way with the hypothesis "
                       f"\"{hyp}\". Actual cause was: {cause}"),
            confidence=conf, sources=ids,
        ))

    if traps:
        findings.append(Finding(
            module=MODULE, kind="trap",
            statement=(f"On this symptom class the first hypothesis was wrong in "
                       f"{len(traps)} of {len(prior)} prior closed cases "
                       f"({rate:.0%})"),
            confidence=Confidence.HIGH if len(prior) >= 10 else Confidence.MEDIUM,
            sources=tuple(c.id for c in traps[:8]),
        ))
    return findings

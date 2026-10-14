"""Module 06 - fleet correlation. Same platform, version and symptom rising
across regions. Invisible from inside a single case."""
from __future__ import annotations

from ..adapters.base import Adapter, EvidenceWindow
from ..models import Case, Confidence, Finding

MODULE = "m06_fleet_correlation"
CLUSTER_MIN = 3


def run(case: Case, adapter: Adapter, window: EvidenceWindow,
        days: int = 30) -> list[Finding]:
    peers = adapter.open_cases_by_signature(
        case.platform, case.sw_version, case.symptom_class, window, days=days)
    others = [c for c in peers if c.id != case.id]
    if len(others) + 1 < CLUSTER_MIN:
        return []

    regions = sorted({c.region for c in others} | {case.region})
    accounts = len({c.account for c in others} | {case.account})
    conf = Confidence.HIGH if len(regions) >= 3 else Confidence.MEDIUM
    return [Finding(
        module=MODULE, kind="cluster",
        statement=(f"{len(others) + 1} open cases in {days}d on {case.platform} "
                   f"/ {case.sw_version} / {case.symptom_class} across "
                   f"{len(regions)} region(s) and {accounts} account(s) - "
                   f"possible field notice candidate"),
        confidence=conf, sources=tuple(c.id for c in (case, *others)),
    )]

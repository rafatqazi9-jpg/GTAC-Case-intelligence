"""Module 01 - defect exposure analysis.

Code version x features actually enabled x the defect list. Reports only
defects the customer is genuinely exposed to, and the target version that
clears the most of them.
"""
from __future__ import annotations

from ..adapters.base import Adapter, EvidenceWindow
from ..extract import version_key as _version_key
from ..models import Case, Confidence, Finding

MODULE = "m01_defect_exposure"

# Ordering releases is delegated to src/extract.version_key so real Extreme
# release strings sort correctly. An earlier local implementation mapped any
# non-numeric segment to -1, which made 20.7.3a and 20.7.1a compare EQUAL and
# would have picked an arbitrary one as the upgrade target. Patch letters are
# how Extreme increments a release, so they belong in the key.
#
# version_key returns None for a string it cannot parse. Unparseable versions
# are skipped and reported, never coerced: naming a target release that does
# not clear the defect is a fabricated recommendation.


def run(case: Case, adapter: Adapter, window: EvidenceWindow) -> list[Finding]:
    defects = adapter.defects_for_platform(case.platform, window)
    enabled = set(case.features_enabled)

    exposed = [
        d for d in defects
        if case.sw_version in d.affected_versions and (set(d.feature_tags) & enabled)
    ]
    findings: list[Finding] = []

    for d in exposed:
        overlap = sorted(set(d.feature_tags) & enabled)
        findings.append(Finding(
            module=MODULE, kind="candidate",
            statement=(f"{d.title} - affects {case.sw_version} with "
                       f"{', '.join(overlap)} enabled"
                       + (f"; fixed in {d.fixed_in}" if d.fixed_in else "; no fix available")),
            confidence=Confidence.MEDIUM if d.fixed_in else Confidence.LOW,
            sources=(d.id,),
        ))

    # Defects on this version the customer is NOT exposed to: that is an
    # elimination, and it is the more useful half of this module.
    for d in defects:
        if case.sw_version in d.affected_versions and not (set(d.feature_tags) & enabled):
            findings.append(Finding(
                module=MODULE, kind="eliminated",
                statement=(f"{d.title} - not exposed: requires "
                           f"{', '.join(d.feature_tags)}, none enabled on this device"),
                confidence=Confidence.HIGH, sources=(d.id,),
            ))

    if exposed:
        # Keep only fix versions that actually parse. Ordering against a
        # version we could not read would produce a confident wrong target.
        parsed = [(d, k) for d in exposed
                  if d.fixed_in and (k := _version_key(d.fixed_in)) is not None]
        unparsed = [d for d in exposed
                    if d.fixed_in and _version_key(d.fixed_in) is None]

        if parsed:
            target_defect, target_key = max(parsed, key=lambda p: p[1])
            target = target_defect.fixed_in
            cleared = [d.id for d, k in parsed if k <= target_key]
            findings.append(Finding(
                module=MODULE, kind="candidate",
                statement=(f"Upgrading to {target} clears {len(cleared)} of "
                           f"{len(exposed)} exposed defects"),
                confidence=Confidence.HIGH, sources=tuple(cleared),
            ))

        if unparsed:
            # Say so rather than quietly dropping them from the recommendation.
            findings.append(Finding(
                module=MODULE, kind="gap",
                statement=("Fix version could not be ordered for "
                           f"{len(unparsed)} defect(s): "
                           + ", ".join(f"{d.id}={d.fixed_in}" for d in unparsed)
                           + " - excluded from the upgrade target"),
                confidence=Confidence.HIGH,
                sources=tuple(d.id for d in unparsed),
            ))
    return findings

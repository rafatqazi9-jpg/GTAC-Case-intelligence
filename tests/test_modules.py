"""Tests for the Phase 1 modules and the confidence policy.

test_no_leakage is the most important test in the repo. If evidence from after
the cutoff reaches a module, every reverse-test number becomes meaningless.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from src.adapters.base import EvidenceWindow
from src.adapters.mock import MockAdapter
from src.brief import build
from src.models import Confidence, Finding


@pytest.fixture
def adapter() -> MockAdapter:
    return MockAdapter()


def _window(adapter: MockAdapter, case_id: str, days: int) -> EvidenceWindow:
    created = datetime.fromisoformat(adapter._raw[case_id]["created_at"])
    return EvidenceWindow(cutoff=created + timedelta(days=days))


# --------------------------------------------------------------- leakage
def test_no_leakage_case_under_investigation(adapter: MockAdapter) -> None:
    """The case being worked must never expose its own outcome."""
    cid = adapter.eval_case_ids[0]
    case = adapter.get_case(cid, _window(adapter, cid, 2))
    assert case.initial_hypothesis is None
    assert case.actual_root_cause is None
    assert case.closed_at is None


def test_no_leakage_events_truncated(adapter: MockAdapter) -> None:
    cid = adapter.eval_case_ids[0]
    window = _window(adapter, cid, 2)
    case = adapter.get_case(cid, window)
    assert case.events, "expected some events before the cutoff"
    assert all(window.permits(e.at) for e in case.events)
    # The root-cause note lands on day 4 and must not be visible at day 2.
    assert not any("root cause confirmed" in e.text.lower() for e in case.events)


def test_no_leakage_prior_cases_closed_before_cutoff(adapter: MockAdapter) -> None:
    cid = adapter.eval_case_ids[0]
    window = _window(adapter, cid, 2)
    case = adapter.get_case(cid, window)
    for prior in adapter.similar_closed_cases(case.symptom_class, case.platform, window):
        assert prior.closed_at is not None
        assert window.permits(prior.closed_at)


# --------------------------------------------------------------- findings
def test_every_finding_has_sources(adapter: MockAdapter) -> None:
    for cid in adapter.eval_case_ids:
        brief = build(cid, adapter, _window(adapter, cid, 2))
        for f in brief.findings:
            assert f.sources, f"{f.module} emitted a finding without sources"


def test_finding_rejects_empty_sources() -> None:
    with pytest.raises(ValueError):
        Finding(module="m00", kind="candidate", statement="x",
                confidence=Confidence.HIGH, sources=())


# --------------------------------------------------------------- policy
def test_abstains_on_novel_symptom(adapter: MockAdapter) -> None:
    """Novel symptom class with no history and no defect overlap: the correct
    answer is to decline, not to produce a weak candidate."""
    novel = [c for c in adapter.eval_case_ids if c.startswith("CS-62")]
    assert novel, "fixtures must include negative controls"
    for cid in novel:
        brief = build(cid, adapter, _window(adapter, cid, 2))
        assert brief.abstained
        assert brief.abstain_reason


def test_abstained_brief_still_eliminates(adapter: MockAdapter) -> None:
    """An abstention is only useful if it still says what was ruled out."""
    cid = [c for c in adapter.eval_case_ids if c.startswith("CS-62")][0]
    brief = build(cid, adapter, _window(adapter, cid, 2))
    assert brief.of_kind("eliminated")


def test_threshold_is_respected(adapter: MockAdapter) -> None:
    cid = adapter.eval_case_ids[0]
    window = _window(adapter, cid, 2)
    high = build(cid, adapter, window, threshold=Confidence.HIGH)
    low = build(cid, adapter, window, threshold=Confidence.LOW)
    assert len(low.findings) == len(high.findings)  # policy gates, not filters
    assert not low.abstained


# --------------------------------------------------------------- modules
def test_m01_only_reports_exposed_defects(adapter: MockAdapter) -> None:
    from src.modules import m01_defect_exposure as m01
    cid = adapter.eval_case_ids[0]
    window = _window(adapter, cid, 2)
    case = adapter.get_case(cid, window)
    for f in m01.run(case, adapter, window):
        if f.kind != "candidate" or "Upgrading" in f.statement:
            continue
        assert case.sw_version in f.statement


def test_m01_upgrade_target_orders_versions_numerically() -> None:
    """String compare puts 32.5.10 below 32.5.2 and would name a target release
    that does not clear the defects - a fabricated recommendation."""
    from src.extract import version_key
    assert version_key("32.5.10") > version_key("32.5.2")  # type: ignore[operator]
    assert version_key("32.10.1") > version_key("32.9.1")  # type: ignore[operator]


def test_version_key_separates_extreme_patch_letters() -> None:
    """Real case 03292192 ran SLX-OS 20.7.3a against a fix in 20.7.1a. The
    earlier parser mapped any non-numeric segment to -1, so both collapsed to
    (20,7,-1) and compared EQUAL - it could have named 20.7.1a as an upgrade
    target for a box already past it."""
    from src.extract import compare_versions, version_key
    assert version_key("20.7.3a") != version_key("20.7.1a")
    assert compare_versions("20.7.3a", "20.7.1a") == 1
    assert compare_versions("20.6.2ab", "20.7.1a") == -1
    assert compare_versions("20.7.1", "20.7.1a") == -1     # bare < lettered


def test_version_key_refuses_to_guess() -> None:
    """An unreadable version returns None instead of a coerced key."""
    from src.extract import compare_versions, version_key
    assert version_key("not-a-version") is None
    assert version_key("20.7") is None
    assert compare_versions("20.7.3a", "garbage") is None


# --------------------------------------------------------------- fixtures
def test_symptom_class_does_not_determine_cause(adapter: MockAdapter) -> None:
    """The gate is only meaningful if a module cannot answer from symptom class
    alone. When each class had one cause, m05 scored 6/6 by quoting a prior
    case and m01/m06/m07 added nothing. Guard that from coming back."""
    by_class: dict[str, set[str]] = {}
    for raw in adapter._raw.values():
        cause = raw.get("actual_root_cause")
        if cause:
            by_class.setdefault(raw["symptom_class"], set()).add(cause)

    # Negative-control classes are novel by design: one case, no history.
    with_history = {k: v for k, v in by_class.items()
                    if sum(1 for r in adapter._raw.values()
                           if r["symptom_class"] == k) > 2}
    assert with_history, "expected symptom classes with prior history"
    for symptom, causes in with_history.items():
        assert len(causes) >= 3, (
            f"{symptom} has only {len(causes)} distinct cause(s); "
            "symptom-class lookup would win the reverse test")


def test_defect_attributed_cases_run_an_affected_version(adapter: MockAdapter) -> None:
    """A case whose root cause is a defect must run a version that defect
    actually affects, or the fixture teaches the tool something false."""
    by_id = {d.id: d for d in adapter._defects}
    for raw in adapter._raw.values():
        for did in _TOKENS(raw.get("actual_root_cause") or ""):
            if did in by_id:
                assert raw["sw_version"] in by_id[did].affected_versions, (
                    f"{raw['id']} runs {raw['sw_version']} but is attributed to "
                    f"{did}, which affects {by_id[did].affected_versions}")


def _TOKENS(text: str) -> set[str]:
    import re
    return set(re.findall(r"[A-Z]{2,}-\d+", text))


def test_m01_discriminates_within_a_symptom_class(adapter: MockAdapter) -> None:
    """Two cases, same symptom, different cause. m01 must separate them on
    features x version rather than naming both."""
    from src.modules import m01_defect_exposure as m01
    seen: dict[str, list[tuple[str, set[str]]]] = {}
    for cid in adapter.eval_case_ids:
        window = _window(adapter, cid, 2)
        case = adapter.get_case(cid, window)
        named: set[str] = set()
        for f in m01.run(case, adapter, window):
            if f.kind == "candidate" and "Upgrading" not in f.statement:
                named |= set(f.sources)
        seen.setdefault(case.symptom_class, []).append((cid, named))

    pairs = [v for v in seen.values() if len(v) >= 2]
    assert pairs, "eval set must contain two cases sharing a symptom class"
    for group in pairs:
        sets = [s for _, s in group if s]
        if len(sets) >= 2:
            assert sets[0] != sets[1], (
                "m01 named an identical defect set for two cases in the same "
                "symptom class - it is not using features x version")


# ------------------------------------------------------------ demo export
def test_demo_case_header_does_not_leak_the_outcome() -> None:
    """The demo's case header must show only what the adapter exposes at the
    cutoff. Closure comments name the root cause outright, so putting them in
    the header hands the reader the answer the brief is meant to be reasoning
    toward -- and does it through a path the adapter's own stripping cannot
    see."""
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    data_file = root / "demo" / "demo_data.json"
    if not data_file.exists():                       # export is optional
        subprocess.run([sys.executable, "tools/export_demo.py"],
                       cwd=root, check=True, capture_output=True)

    import json
    data = json.loads(data_file.read_text(encoding="utf-8"))
    for case in data["cases"]:
        labels = {f["label"] for f in case["fields"]}
        assert "Closure Comments" not in labels, f"{case['id']} leaks closure text"
        assert "Closed State" not in labels, f"{case['id']} leaks closed state"
        cause = (case["truth"]["actual_root_cause"] or "").strip()
        for f in case["fields"]:
            assert not (cause and cause in f["value"]), (
                f"{case['id']}: header field {f['label']!r} contains the cause")


# --------------------------------------------------------- abstain wording
def test_abstain_reason_does_not_cite_an_empty_ruled_out_section() -> None:
    """Seen on a real case: the brief abstained, eliminated nothing, and still
    told the reader to 'see RULED OUT' - a section that was not rendered."""
    from src.adapters.base import Adapter
    from src.models import Case, Defect

    class Empty(Adapter):
        """Every retrieval returns nothing, as with no connected systems."""
        def get_case(self, case_id: str, window: EvidenceWindow) -> Case:
            return _bare_case()

        def events(self, case_id: str, window: EvidenceWindow) -> tuple[()]:
            return ()

        def defects_for_platform(self, platform: str,
                                 window: EvidenceWindow) -> tuple[Defect, ...]:
            return ()

        def similar_closed_cases(self, symptom_class: str, platform: str,
                                 window: EvidenceWindow,
                                 limit: int = 20) -> tuple[Case, ...]:
            return ()

        def open_cases_by_signature(self, platform: str, sw_version: str,
                                    symptom_class: str, window: EvidenceWindow,
                                    days: int = 30) -> tuple[Case, ...]:
            return ()

        def required_escalation_fields(self, platform: str) -> tuple[str, ...]:
            return ("show tech-support",)

    brief = build("CS-0", Empty(), EvidenceWindow(cutoff=datetime(2026, 1, 1)))
    assert brief.abstained
    assert not brief.of_kind("eliminated")
    assert brief.abstain_reason is not None
    assert "RULED OUT" not in brief.abstain_reason
    assert "ruled nothing out" in brief.abstain_reason


def _bare_case() -> "object":
    from src.models import Case
    return Case(id="CS-0", account="a", platform="VDX", sw_version="",
                symptom_class="unknown", symptom_text="t", features_enabled=(),
                region="AMER", created_at=datetime(2026, 1, 1))


def test_abstain_reason_cites_ruled_out_when_it_exists(adapter: MockAdapter) -> None:
    cid = [c for c in adapter.eval_case_ids if c.startswith("CS-62")][0]
    brief = build(cid, adapter, _window(adapter, cid, 2))
    assert brief.abstained and brief.of_kind("eliminated")
    assert brief.abstain_reason is not None
    assert "RULED OUT" in brief.abstain_reason


# ------------------------------------------------------- corpus selection
def test_abandoned_case_is_not_gradeable_even_when_marked_resolved() -> None:
    """The failure this guards against: a real case whose Closed State read
    'Resolved' while the closure comment said the customer stopped replying.
    Selecting the corpus on the status field would score it against a root
    cause that was never established."""
    from src.harness.reverse_test import gradeable
    ok, why = gradeable(None, "Resolved",
                        "No response from customer. Hence closing this case.")
    assert not ok
    assert "without diagnosis" in why


def test_missing_root_cause_is_not_gradeable() -> None:
    from src.harness.reverse_test import gradeable
    assert not gradeable(None, "Resolved", "Closed per customer request.")[0]
    assert not gradeable("   ", "Resolved", None)[0]


def test_diagnosed_case_is_gradeable() -> None:
    from src.harness.reverse_test import gradeable
    ok, why = gradeable("MLAG peer link flap (XIQ-41220)", "Resolved",
                        "Root cause confirmed. Fix identified.")
    assert ok and why == ""


def test_gate_excludes_ungradeable_cases_from_scoring(adapter: MockAdapter) -> None:
    """The abandoned fixture must never reach the verdict counts."""
    from src.harness.reverse_test import gradeable
    scored = []
    for cid in adapter.eval_case_ids:
        _, cause = adapter.truth(cid)
        state, comments = adapter.closure(cid)
        if gradeable(cause, state, comments)[0]:
            scored.append(cid)
    assert "CS-6300001" in adapter.eval_case_ids, "expected an abandoned fixture"
    assert "CS-6300001" not in scored
    assert adapter.closure("CS-6300001")[0] == "Resolved", (
        "the fixture must keep the misleading status field, or the guard is "
        "not being tested against the real-world shape")


# --------------------------------------------------------------- harness
def test_eliminating_true_cause_is_not_a_hit() -> None:
    """A brief that RULES OUT the actual root cause must never score HIT.
    That is the worst outcome available, not the best."""
    from src.harness.reverse_test import Outcome
    o = Outcome(case_id="CS-1", abstained=False, named_cause=False,
                warned_of_trap=False, n_eliminated=1, ruled_out_cause=True)
    assert o.verdict == "RULED-OUT"


def test_ruled_out_does_not_mask_a_genuine_hit() -> None:
    from src.harness.reverse_test import Outcome
    o = Outcome(case_id="CS-1", abstained=False, named_cause=True,
                warned_of_trap=False, n_eliminated=1, ruled_out_cause=True)
    assert o.verdict == "HIT"


def test_m07_flags_missing_artefacts(adapter: MockAdapter) -> None:
    from src.modules import m07_escalation_package as m07
    cid = adapter.eval_case_ids[0]
    window = _window(adapter, cid, 2)
    case = adapter.get_case(cid, window)
    gaps = [f for f in m07.run(case, adapter, window) if "incomplete" in f.statement]
    assert gaps

# --------------------------------------------------------- brief rendering
def test_brief_render_includes_case_id_timestamp_and_finding_sources() -> None:
    from src.models import Brief

    generated_at = datetime(2026, 10, 14, 9, 30)
    finding = Finding(
        module="m01_defect_exposure",
        kind="candidate",
        statement="Candidate defect affects the running version",
        confidence=Confidence.MEDIUM,
        sources=("SLXOS-12345",),
    )
    brief = Brief(
        case_id="CS-TEST-001",
        generated_at=generated_at,
        findings=[finding],
    )

    rendered = brief.render()

    assert "CS-TEST-001" in rendered
    assert "2026-10-14 09:30" in rendered
    assert "CANDIDATES" in rendered
    assert "[medium]" in rendered
    assert "Candidate defect affects the running version" in rendered
    assert "SLXOS-12345" in rendered


def test_abstained_brief_render_includes_reason() -> None:
    from src.models import Brief

    brief = Brief(
        case_id="CS-TEST-002",
        generated_at=datetime(2026, 10, 14, 9, 30),
        abstained=True,
        abstain_reason="No candidate meets the confidence threshold.",
    )

    rendered = brief.render()

    assert "NO CONFIDENT CANDIDATE" in rendered
    assert "No candidate meets the confidence threshold." in rendered


def test_empty_brief_render_does_not_invent_findings() -> None:
    from src.models import Brief

    brief = Brief(
        case_id="CS-TEST-003",
        generated_at=datetime(2026, 10, 14, 9, 30),
    )

    rendered = brief.render()

    assert "CS-TEST-003" in rendered
    assert "CANDIDATES" not in rendered
    assert "KNOWN TRAPS" not in rendered
    assert "FLEET SIGNAL" not in rendered
    assert "confirmed root cause" not in rendered.lower()

# --------------------------------------------------- M01 candidate explanation
def test_m01_candidate_distinguishes_exposure_from_confirmed_cause() -> None:
    from datetime import datetime
    from src.adapters.base import EvidenceWindow
    from src.models import Case, Confidence, Defect
    from src.modules import m01_defect_exposure as m01

    defect = Defect(
        id="SLXOS-12345",
        title="Example forwarding defect",
        affected_versions=("20.7.2ab",),
        fixed_in="20.7.3a",
        feature_tags=("MCT",),
        status="open",
    )

    class AdapterWithOneDefect:
        def defects_for_platform(self, platform, window):
            return (defect,)

    case = Case(
        id="CS-TEST-M01",
        account="Test account",
        platform="SLX",
        sw_version="20.7.2ab",
        symptom_class="packet_loss",
        symptom_text="Intermittent packet loss",
        features_enabled=("MCT",),
        region="test",
        created_at=datetime(2026, 10, 14),
    )

    window = EvidenceWindow(cutoff=datetime(2026, 10, 14))
    findings = m01.run(case, AdapterWithOneDefect(), window)
    candidate = next(
        f for f in findings
        if f.kind == "candidate" and "Upgrading" not in f.statement
    )

    assert "20.7.2ab" in candidate.statement
    assert "MCT" in candidate.statement
    assert "not confirmed as the cause" in candidate.statement.lower()
    assert candidate.sources == ("SLXOS-12345",)
    assert candidate.confidence == Confidence.MEDIUM

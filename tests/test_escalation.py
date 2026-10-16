"""Tests for GTAC Engineering Escalation Package Generator."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from src.escalation.builder import EscalationPackage, build_escalation_package
from src.server import escalation_payload


def test_build_escalation_package_from_evidence() -> None:
    evidence = {
        "running_version": {
            "value": "18r.2.00b",
            "source": "case text",
            "evidence": "Running: SLX-OS 18r.2.00b",
        },
        "platforms": [{"value": "SLX 9540"}],
        "errors": [
            {"value": "BGP-1002 session reset peer 10.0.0.1", "source": "raslog", "evidence": "Hold timer expired"}
        ],
        "defects": [{"value": "DEFECT000645012", "confidence": "HIGH", "evidence": "BGP timer issue"}],
        "gaps": [{"value": "supportsave not attached"}],
        "missing": ["features_enabled (needs running-config)"],
    }
    timeline = {
        "rendered": "[2026-10-14 10:00:00] BGP Hold timer expired on peer 10.0.0.1\n[2026-10-14 10:00:05] Interface down",
        "correlations": ["BGP flap followed by interface transition"],
    }

    pkg = build_escalation_package(
        case_id="CS-9999",
        evidence=evidence,
        timeline=timeline,
        notes="Customer reported recurring drops during morning traffic peak.",
    )

    assert isinstance(pkg, EscalationPackage)
    assert pkg.case_id == "CS-9999"
    assert pkg.platform == "SLX 9540"
    assert pkg.sw_version == "18r.2.00b"
    assert "BGP" in pkg.subsystems
    assert len(pkg.symptoms) == 1
    assert any(d["id"] == "DEFECT000645012" for d in pkg.candidate_defects)
    assert len(pkg.missing_artifacts) >= 2
    assert "BGP flap followed by interface transition" in pkg.actions_taken[1]
    assert pkg.readiness_status in ("READY_FOR_ESCALATION", "NEEDS_EVIDENCE")


def test_build_escalation_package_from_fixture_brief() -> None:
    pkg_dict = escalation_payload(case_id="CS-6100050")

    assert pkg_dict["case_id"] == "CS-6100050"
    assert "markdown" in pkg_dict
    assert "# GTAC ENGINEERING ESCALATION BRIEF — CS-6100050" in pkg_dict["markdown"]
    assert "Readiness Score:" in pkg_dict["markdown"]


def test_readiness_scoring_penalties() -> None:
    # Completely empty evidence should trigger multiple bounce risks
    pkg = build_escalation_package(case_id="EMPTY-CASE")

    assert pkg.platform == "Unspecified"
    assert pkg.sw_version == "Unestablished"
    assert pkg.readiness_status == "NEEDS_EVIDENCE"
    assert pkg.readiness_score < 70
    assert len(pkg.bounce_risk_notes) >= 2
    assert any("platform" in note.lower() for note in pkg.bounce_risk_notes)
    assert any("version" in note.lower() for note in pkg.bounce_risk_notes)


def test_markdown_and_json_serialization() -> None:
    pkg = build_escalation_package(
        case_id="CS-TEST-1",
        evidence={
            "running_version": {"value": "17s.1.02"},
            "platforms": [{"value": "VDX 6740"}],
            "errors": [{"value": "Kernel panic in mct_core"}],
        },
    )

    md = pkg.to_markdown()
    assert "# GTAC ENGINEERING ESCALATION BRIEF" in md
    assert "VDX 6740" in md
    assert "17s.1.02" in md
    assert "Kernel panic in mct_core" in md

    as_json_str = pkg.to_json()
    parsed = json.loads(as_json_str)
    assert parsed["case_id"] == "CS-TEST-1"
    assert parsed["readiness_score"] >= 0
    assert parsed["readiness_status"] in ("READY_FOR_ESCALATION", "NEEDS_EVIDENCE")
    assert "customer_email" in parsed
    assert "case_notes" in parsed


def test_customer_email_and_case_notes() -> None:
    pkg = build_escalation_package(
        case_id="CS-EMAIL-100",
        evidence={
            "running_version": {"value": "20.2.3a"},
            "platforms": [{"value": "SLX 9240"}],
            "errors": [{"value": "BGP-1002 session reset"}],
        },
    )

    email = pkg.to_customer_email()
    assert "[GTAC Update] Case CS-EMAIL-100" in email
    assert "SLX 9240" in email
    assert "20.2.3a" in email
    assert "supportsave" in email
    # Internal defect strings or raw defect tags should not be in customer email
    assert "DEFECT000" not in email

    notes = pkg.to_case_notes()
    assert "GTAC CASE WORKLOG" in notes
    assert "CS-EMAIL-100" in notes
    assert "[PROBLEM STATEMENT]" in notes
    assert "[ACTION PLAN & NEXT STEPS]" in notes

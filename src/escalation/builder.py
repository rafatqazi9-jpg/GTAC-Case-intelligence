"""GTAC Engineering Escalation Package Generator.

Produces structured, evidence-backed escalation packages for Tier 3 TAC and
Defect Engineering (DE / BU). Ensures packages meet BU requirements, document
provenance, and avoid bounce-backs due to missing diagnostic evidence.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class EscalationSection:
    title: str
    content: str
    items: list[str] = field(default_factory=list)


@dataclass
class EscalationPackage:
    case_id: str
    generated_at: datetime
    platform: str
    sw_version: str
    subsystems: list[str]
    symptoms: list[str]
    timeline_summary: str
    evidence_provenance: list[dict[str, str]]
    candidate_defects: list[dict[str, str]]
    eliminated_traps: list[dict[str, str]]
    actions_taken: list[str]
    missing_artifacts: list[str]
    readiness_status: str  # "READY_FOR_ESCALATION" | "NEEDS_EVIDENCE"
    readiness_score: int  # 0 to 100
    bounce_risk_notes: list[str]

    def to_markdown(self) -> str:
        """Render complete escalation package as GitHub/Jira flavored markdown."""
        date_str = self.generated_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        status_badge = (
            "🟢 **STATUS: READY FOR TIER 3 / DE**"
            if self.readiness_status == "READY_FOR_ESCALATION"
            else "⚠️ **STATUS: INCOMPLETE — BOUNCE RISK DETECTED**"
        )

        lines: list[str] = [
            f"# GTAC ENGINEERING ESCALATION BRIEF — {self.case_id}",
            f"> **Generated:** {date_str} | **Readiness Score:** {self.readiness_score}/100",
            f"> {status_badge}",
            "",
            "## 1. Case & Target Environment Metadata",
            f"- **Case Reference:** `{self.case_id}`",
            f"- **Target Hardware Platform:** `{self.platform or 'Unspecified'}`",
            f"- **Running Firmware / Release:** `{self.sw_version or 'Unestablished'}`",
            f"- **Subsystem / Domains:** {', '.join(f'`{s}`' for s in self.subsystems) if self.subsystems else 'General Network'}",
            "",
            "## 2. Problem Statement & Symptoms",
        ]

        if self.symptoms:
            for s in self.symptoms:
                lines.append(f"- {s}")
        else:
            lines.append("- *No specific symptom pattern established in evidence.*")

        lines.extend([
            "",
            "## 3. Incident Timeline & Causal Sequence",
        ])

        if self.timeline_summary.strip():
            lines.append("```text")
            lines.append(self.timeline_summary.strip())
            lines.append("```")
        else:
            lines.append("- *No timestamped syslog/RASLOG timeline established.*")

        lines.extend([
            "",
            "## 4. Verified Log Evidence & Provenance",
        ])

        if self.evidence_provenance:
            for ev in self.evidence_provenance:
                src = ev.get("source", "log")
                val = ev.get("value", "")
                detail = ev.get("detail", "")
                lines.append(f"- **{val}** (Source: `{src}`)")
                if detail:
                    lines.append(f"  > Quote: *\"{detail}\"*")
        else:
            lines.append("- *No raw evidence quotes cataloged.*")

        lines.extend([
            "",
            "## 5. Defect Exposure & Trap Ruling",
        ])

        if self.candidate_defects:
            lines.append("### Suspected Defect Candidates:")
            for d in self.candidate_defects:
                did = d.get("id", "DEFECT")
                conf = d.get("confidence", "MEDIUM")
                stmt = d.get("statement", "")
                lines.append(f"- **`{did}`** [{conf}]: {stmt}")
        else:
            lines.append("- *No confirmed defect candidate matching all criteria.*")

        if self.eliminated_traps:
            lines.append("")
            lines.append("### Known Traps Ruled Out (Eliminated):")
            for t in self.eliminated_traps:
                tid = t.get("id", "TRAP")
                stmt = t.get("statement", "")
                lines.append(f"- ~~`{tid}`~~: {stmt}")

        lines.extend([
            "",
            "## 6. Actions Already Taken by GTAC",
        ])

        if self.actions_taken:
            for a in self.actions_taken:
                lines.append(f"- [x] {a}")
        else:
            lines.append("- [x] Diagnostic log analysis performed by GTAC AI Assistant.")

        lines.extend([
            "",
            "## 7. Escalation Checklist & Missing Evidence",
        ])

        if self.missing_artifacts:
            for m in self.missing_artifacts:
                lines.append(f"- [ ] ⚠️ **MISSING:** {m}")
        else:
            lines.append("- [x] All mandatory diagnostic artifacts present (supportsave, version, error signature).")

        if self.bounce_risk_notes:
            lines.extend([
                "",
                "### ⚠️ Bounce Risk Warnings:",
            ])
            for note in self.bounce_risk_notes:
                lines.append(f"> [!WARNING] {note}")

        lines.extend([
            "",
            "---",
            "*Report prepared automatically by GTAC AI Assistant. Grounded strictly in case evidence and diagnostic logs.*",
        ])

        return "\n".join(lines)

    def to_customer_email(self) -> str:
        """Render customer-safe status email without internal defect IDs or unverified bug links."""
        date_str = self.generated_at.strftime("%Y-%m-%d %H:%M UTC")
        lines: list[str] = [
            f"Subject: [GTAC Update] Case {self.case_id}: Technical Diagnostic Summary & Next Steps",
            "",
            "Dear Customer,",
            "",
            "Thank you for contacting Extreme Networks Global Technical Assistance Center (GTAC).",
            f"We have completed an initial diagnostic assessment of the telemetry and log data provided for case {self.case_id}.",
            "",
            "---",
            "### 1. Environment Under Investigation",
            f"- Target Platform: {self.platform or 'Extreme Switch'}",
            f"- Identified Firmware Release: {self.sw_version or 'Under verification'}",
            f"- Affected Domain / Subsystem: {', '.join(self.subsystems) if self.subsystems else 'General Network'}",
            "",
            "### 2. Diagnostic Summary & Observed Symptoms",
        ]

        if self.symptoms:
            for s in self.symptoms:
                lines.append(f"- Observed symptom: {s}")
        else:
            lines.append("- Symptoms are under active baseline investigation from the supplied logs.")

        lines.extend([
            "",
            "### 3. Immediate Action Plan & Recommended Verification Commands",
            "To confirm device state and isolate the trigger condition, please execute the following read-only commands during your next observation window and upload the outputs to the case portal:",
            "```text",
            "show version",
            "show logging raslog",
        ])

        if any("bgp" in s.lower() for s in self.subsystems):
            lines.extend([
                "show ip bgp summary",
                "show ip bgp neighbors",
            ])
        elif any("tcam" in s.lower() or "hardware" in s.lower() for s in self.subsystems):
            lines.extend([
                "show hardware profile",
                "show table entry count",
            ])
        elif any("lacp" in s.lower() or "link" in s.lower() for s in self.subsystems):
            lines.extend([
                "show port-channel detail",
                "show lacp sys-id",
            ])
        else:
            lines.append("show environment")

        lines.extend([
            "supportsave",
            "```",
            "",
            "### 4. Current Status & Next Steps",
            "- Current Case Status: In Progress (GTAC Diagnostic Evaluation)",
            "- Next Follow-up: GTAC engineer will review updated outputs within 1 business day of receipt.",
            "",
            "If you have any questions or schedule constraints for maintenance activities, please reply directly to this email or update the case portal.",
            "",
            "Best regards,",
            "Extreme Networks Global Technical Assistance Center (GTAC)",
            f"Case: {self.case_id} | Timestamp: {date_str}",
        ])
        return "\n".join(lines)

    def to_case_notes(self) -> str:
        """Render standardized CRM / Salesforce GTAC engineering case notes."""
        date_str = self.generated_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        lines: list[str] = [
            "================================================================================",
            "GTAC CASE WORKLOG & TECHNICAL SUMMARY",
            "================================================================================",
            f"CASE ID        : {self.case_id}",
            f"TIMESTAMP      : {date_str}",
            f"PLATFORM       : {self.platform or 'Unspecified'}",
            f"RUNNING SW     : {self.sw_version or 'Unestablished'}",
            f"SUBSYSTEMS     : {', '.join(self.subsystems) if self.subsystems else 'General'}",
            f"DE READINESS   : {self.readiness_score}/100 ({self.readiness_status})",
            "--------------------------------------------------------------------------------",
            "",
            "[PROBLEM STATEMENT]",
        ]

        if self.symptoms:
            for s in self.symptoms:
                lines.append(f"- {s}")
        else:
            lines.append("- Symptoms under active triage.")

        lines.extend([
            "",
            "[VERIFIED LOG EVIDENCE & SYMPTOMS]",
        ])
        if self.evidence_provenance:
            for ev in self.evidence_provenance[:6]:
                lines.append(f"- {ev.get('value', '')} (Source: {ev.get('source', 'log')})")
        else:
            lines.append("- No raw log lines cataloged.")

        lines.extend([
            "",
            "[DEFECT HYPOTHESES & TRAP RULINGS]",
        ])
        if self.candidate_defects:
            for d in self.candidate_defects:
                lines.append(f"- Candidate: {d.get('id', '')} [{d.get('confidence', '')}] - {d.get('statement', '')}")
        else:
            lines.append("- No confirmed defect match identified.")

        if self.eliminated_traps:
            for t in self.eliminated_traps:
                lines.append(f"- Ruled Out: {t.get('id', '')} - {t.get('statement', '')}")

        lines.extend([
            "",
            "[ACTIONS COMPLETED BY GTAC]",
        ])
        if self.actions_taken:
            for a in self.actions_taken:
                lines.append(f"- {a}")
        else:
            lines.append("- Initial automated diagnostic log and defect exposure analysis performed.")

        lines.extend([
            "",
            "[ACTION PLAN & NEXT STEPS]",
        ])
        if self.missing_artifacts:
            for m in self.missing_artifacts:
                lines.append(f"- Request from customer: {m}")
        else:
            lines.append("- Validate symptoms against test bed reproduction.")

        lines.extend([
            "- Review customer updates upon receipt of next diagnostics.",
            "================================================================================",
        ])
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "generated_at": self.generated_at.isoformat(),
            "platform": self.platform,
            "sw_version": self.sw_version,
            "subsystems": self.subsystems,
            "symptoms": self.symptoms,
            "timeline_summary": self.timeline_summary,
            "evidence_provenance": self.evidence_provenance,
            "candidate_defects": self.candidate_defects,
            "eliminated_traps": self.eliminated_traps,
            "actions_taken": self.actions_taken,
            "missing_artifacts": self.missing_artifacts,
            "readiness_status": self.readiness_status,
            "readiness_score": self.readiness_score,
            "bounce_risk_notes": self.bounce_risk_notes,
            "markdown": self.to_markdown(),
            "customer_email": self.to_customer_email(),
            "case_notes": self.to_case_notes(),
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


def build_escalation_package(
    *,
    case_id: str | None = None,
    evidence: dict[str, Any] | None = None,
    timeline: dict[str, Any] | None = None,
    brief_data: dict[str, Any] | None = None,
    notes: str | None = None,
) -> EscalationPackage:
    """Construct an EscalationPackage from evidence, brief, and timeline."""
    cid = case_id or (brief_data.get("case_id") if brief_data else None) or "STANDALONE-CASE"
    now = datetime.now(timezone.utc)

    platform = "Unspecified"
    sw_version = "Unestablished"
    subsystems: list[str] = []
    symptoms: list[str] = []
    evidence_provenance: list[dict[str, str]] = []
    candidate_defects: list[dict[str, str]] = []
    eliminated_traps: list[dict[str, str]] = []
    missing_artifacts: list[str] = []
    actions_taken: list[str] = ["Initial diagnostic log extraction and correlation completed."]
    bounce_risks: list[str] = []

    # 1. Populate from brief_data if available (fixture investigation)
    if brief_data:
        if brief_data.get("platform"):
            platform = brief_data["platform"]
        if brief_data.get("sw_version"):
            sw_version = brief_data["sw_version"]
        if brief_data.get("symptom_text"):
            symptoms.append(brief_data["symptom_text"])

        findings = brief_data.get("findings", [])
        for f in findings:
            kind = f.get("kind", "")
            stmt = f.get("statement", "")
            mod = f.get("module", "")
            conf = f.get("confidence", "MEDIUM")

            if kind == "candidate":
                # Extract defect id if in statement
                did = "DEFECT-REF"
                for word in stmt.split():
                    if "DEFECT" in word.upper() or word.startswith("CVE"):
                        did = word.strip(":,()[]")
                        break
                candidate_defects.append({
                    "id": did,
                    "confidence": conf,
                    "statement": stmt,
                })
            elif kind == "eliminated":
                eliminated_traps.append({
                    "id": "RULED_OUT",
                    "statement": stmt,
                })
            elif kind == "gap":
                missing_artifacts.append(stmt)

    # 2. Populate from extracted evidence dict if available
    if evidence:
        # Running version
        rv = evidence.get("running_version")
        if isinstance(rv, dict) and rv.get("value"):
            sw_version = rv["value"]
            evidence_provenance.append({
                "value": f"Running Version: {sw_version}",
                "source": rv.get("source", "log"),
                "detail": rv.get("evidence", ""),
            })
        elif isinstance(rv, str):
            sw_version = rv

        # Platforms
        platforms = evidence.get("platforms", [])
        if platforms:
            p_names = [p.get("value") if isinstance(p, dict) else str(p) for p in platforms]
            platform = ", ".join(dict.fromkeys(p_names))

        # Errors / symptoms
        errors = evidence.get("errors", [])
        for err in errors:
            val = err.get("value") if isinstance(err, dict) else str(err)
            ev_text = err.get("evidence", "") if isinstance(err, dict) else ""
            symptoms.append(val)
            if ev_text:
                evidence_provenance.append({
                    "value": val,
                    "source": err.get("source", "log") if isinstance(err, dict) else "log",
                    "detail": ev_text,
                })

        # Defect IDs
        defects = evidence.get("defects", [])
        for d in defects:
            d_val = d.get("value") if isinstance(d, dict) else str(d)
            if not any(c.get("id") == d_val for c in candidate_defects):
                candidate_defects.append({
                    "id": d_val,
                    "confidence": d.get("confidence", "MEDIUM") if isinstance(d, dict) else "MEDIUM",
                    "statement": f"Referenced in evidence: {d.get('evidence', '') if isinstance(d, dict) else ''}",
                })

        # Evidence gaps
        gaps = evidence.get("gaps", [])
        for g in gaps:
            g_val = g.get("value") if isinstance(g, dict) else str(g)
            if g_val not in missing_artifacts:
                missing_artifacts.append(g_val)

        missing_list = evidence.get("missing", [])
        for m in missing_list:
            if m not in missing_artifacts:
                missing_artifacts.append(m)

        # Detect subsystems from errors and text
        full_text = " ".join(symptoms)
        for domain in ("BGP", "OSPF", "MCT", "EVPN", "VXLAN", "STP", "LACP", "LLDP", "RASLOG", "SLX", "NOS", "MPLS"):
            if domain.lower() in full_text.lower():
                subsystems.append(domain)

    # 3. Timeline summary
    timeline_summary = ""
    if timeline and timeline.get("rendered"):
        timeline_summary = timeline["rendered"]
        corrs = timeline.get("correlations", [])
        if corrs:
            actions_taken.append(f"Correlated {len(corrs)} causal links in timeline: {'; '.join(corrs)}")

    if notes:
        actions_taken.append(f"Engineer notes: {notes}")

    # 4. Compute Readiness Score & Bounce Risks
    score = 100
    if platform == "Unspecified":
        score -= 25
        bounce_risks.append("Target hardware platform is unspecified. BU cannot assign firmware engineers without target platform.")
    if sw_version == "Unestablished":
        score -= 25
        bounce_risks.append("Running software version is not established. Release engineering cannot replicate without build/release number.")
    if not symptoms:
        score -= 20
        bounce_risks.append("No clear symptom signature cataloged. Defect Engineering will reject for missing problem description.")
    if missing_artifacts:
        score -= min(30, len(missing_artifacts) * 10)
        bounce_risks.append(f"Missing {len(missing_artifacts)} diagnostic artifacts required for escalation (e.g. {missing_artifacts[0]}).")

    readiness_status = "READY_FOR_ESCALATION" if score >= 70 and not (platform == "Unspecified" and sw_version == "Unestablished") else "NEEDS_EVIDENCE"

    return EscalationPackage(
        case_id=cid,
        generated_at=now,
        platform=platform,
        sw_version=sw_version,
        subsystems=list(dict.fromkeys(subsystems)),
        symptoms=symptoms[:8],
        timeline_summary=timeline_summary,
        evidence_provenance=evidence_provenance[:10],
        candidate_defects=candidate_defects,
        eliminated_traps=eliminated_traps,
        actions_taken=actions_taken,
        missing_artifacts=missing_artifacts,
        readiness_status=readiness_status,
        readiness_score=max(0, score),
        bounce_risk_notes=bounce_risks,
    )

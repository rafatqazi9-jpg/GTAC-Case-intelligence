"""Conversation session — holds investigation context across turns.

Each session is isolated: no data leaks between sessions or
investigations. A session carries the case under investigation,
accumulated findings, and conversation history.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..llm.base import Message
from ..models import Brief, Finding


@dataclass
class Session:
    """One investigation conversation.

    Created when an engineer starts a new investigation or asks a question.
    Lives in memory for the duration of the server process. No persistence
    to disk — customer data must not be written.
    """
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: datetime = field(default_factory=datetime.now)

    # Investigation context — set when a case is loaded.
    case_id: str | None = None
    platform: str | None = None
    sw_version: str | None = None
    symptom_class: str | None = None
    symptom_text: str | None = None

    # Results from the investigation engine.
    brief: Brief | None = None
    findings: list[Finding] = field(default_factory=list)
    active_evidence: dict[str, Any] | None = None
    active_timeline: dict[str, Any] | None = None

    # Conversation history — system prompt is NOT stored here; it is
    # rebuilt each turn from the current context.
    history: list[Message] = field(default_factory=list)

    # Metadata for the data source badge.
    data_source: str = "synthetic"   # "none" | "synthetic" | "uploaded" | "pasted"

    @property
    def has_case(self) -> bool:
        return self.case_id is not None or self.active_evidence is not None

    def set_evidence_context(
        self,
        evidence: dict[str, Any],
        timeline: dict[str, Any] | None = None,
        source_name: str | None = None,
    ) -> None:
        """Ground the session in live analyzed log or archive evidence."""
        self.active_evidence = evidence
        self.active_timeline = timeline

        if source_name:
            self.data_source = source_name
        else:
            self.data_source = "uploaded archive"

        # Extract vitals from evidence dictionary
        running_ver = evidence.get("running_version")
        if isinstance(running_ver, dict) and running_ver.get("value"):
            self.sw_version = str(running_ver["value"])

        platforms = evidence.get("platforms")
        if isinstance(platforms, list) and platforms:
            first_plat = platforms[0]
            if isinstance(first_plat, dict) and first_plat.get("value"):
                self.platform = str(first_plat["value"])

        errors = evidence.get("errors")
        if isinstance(errors, list) and errors:
            self.symptom_text = ", ".join(
                str(e.get("value")) for e in errors[:3] if isinstance(e, dict) and e.get("value")
            )

    def add_user_message(self, content: str) -> None:
        self.history.append(Message(role="user", content=content))

    def add_assistant_message(self, content: str) -> None:
        self.history.append(Message(role="assistant", content=content))

    def context_summary(self) -> str:
        """One-paragraph summary of the current investigation state,
        suitable for injection into the system prompt."""
        parts: list[str] = []
        if self.case_id:
            parts.append(f"Case: {self.case_id}")
        if self.has_case and self.data_source and self.data_source != "none":
            parts.append(f"Evidence Source: {self.data_source}")
        if self.platform:
            parts.append(f"Platform: {self.platform}")
        if self.sw_version:
            parts.append(f"Software: {self.sw_version}")
        if self.symptom_class:
            parts.append(f"Symptom class: {self.symptom_class}")
        if self.symptom_text:
            parts.append(f"Symptom: {self.symptom_text}")
        if self.brief:
            parts.append(
                f"Brief status: {'abstained' if self.brief.abstained else 'findings available'}"
            )
            n_cand = len(self.brief.of_kind("candidate"))
            n_elim = len(self.brief.of_kind("eliminated"))
            n_trap = len(self.brief.of_kind("trap"))
            n_gap = len(self.brief.of_kind("gap"))
            parts.append(
                f"Findings: {n_cand} candidates, {n_elim} eliminated, "
                f"{n_trap} traps, {n_gap} gaps"
            )
        elif self.active_evidence:
            defects = self.active_evidence.get("defects", [])
            errors = self.active_evidence.get("errors", [])
            gaps = self.active_evidence.get("gaps", [])
            parts.append(
                f"Evidence: {len(errors)} error signatures, {len(defects)} candidate defects, {len(gaps)} collection gaps"
            )

        if not parts:
            return "No case loaded. The engineer has not selected a case or provided evidence yet."
        return " | ".join(parts)

    def findings_text(self) -> str:
        """Render the current findings as text for prompt context."""
        if self.brief:
            return self.brief.render()

        if self.active_evidence:
            lines: list[str] = ["### Analyzed Evidence Findings:"]
            if self.sw_version:
                lines.append(f"- Identified Software Release: {self.sw_version}")
            if self.platform:
                lines.append(f"- Hardware / Platform: {self.platform}")

            errors = self.active_evidence.get("errors", [])
            if errors:
                lines.append("- Observed Error Signatures:")
                for e in errors[:5]:
                    if isinstance(e, dict):
                        lines.append(f"  * {e.get('value')} (Source quote: \"{e.get('evidence', '')}\")")

            defects = self.active_evidence.get("defects", [])
            if defects:
                lines.append("- Candidate Defect IDs Found in Input:")
                for d in defects:
                    if isinstance(d, dict):
                        lines.append(f"  * {d.get('value')}")

            if self.active_timeline:
                corrs = self.active_timeline.get("correlations", [])
                if corrs:
                    lines.append("- Detected Causal Timeline Sequence:")
                    for c in corrs:
                        lines.append(f"  * {c}")

            return "\n".join(lines)

        return "No investigation results available."

    def to_dict(self) -> dict[str, Any]:
        """Safe serialization for API responses. No customer data in
        the session metadata — only structural information."""
        return {
            "session_id": self.id,
            "created_at": self.created_at.isoformat(),
            "case_id": self.case_id,
            "platform": self.platform,
            "sw_version": self.sw_version,
            "symptom_class": self.symptom_class,
            "data_source": self.data_source,
            "has_brief": self.brief is not None,
            "message_count": len(self.history),
        }

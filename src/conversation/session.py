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

    # Conversation history — system prompt is NOT stored here; it is
    # rebuilt each turn from the current context.
    history: list[Message] = field(default_factory=list)

    # Metadata for the data source badge.
    data_source: str = "synthetic"   # "synthetic" | "uploaded" | "authorized"

    @property
    def has_case(self) -> bool:
        return self.case_id is not None

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

        if not parts:
            return "No case loaded. The engineer has not selected a case or provided evidence yet."
        return " | ".join(parts)

    def findings_text(self) -> str:
        """Render the current findings as text for prompt context."""
        if not self.brief:
            return "No investigation results available."
        return self.brief.render()

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

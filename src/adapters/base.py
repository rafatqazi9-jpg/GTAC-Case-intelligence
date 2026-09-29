"""The only seam between modules and systems of record.

Cutoff enforcement lives HERE, not in the modules. If a module could see
post-cutoff data, the reverse test is worthless. Any new method must respect
`window`.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from ..models import Case, CaseEvent, Defect


@dataclass(frozen=True)
class EvidenceWindow:
    """Everything the adapter returns must be knowable at or before `cutoff`."""
    cutoff: datetime

    def permits(self, at: datetime) -> bool:
        return at <= self.cutoff


class Adapter(ABC):
    @abstractmethod
    def get_case(self, case_id: str, window: EvidenceWindow) -> Case:
        """Case with events truncated to the window, and closed-case outcome
        fields (initial_hypothesis, actual_root_cause) STRIPPED."""

    @abstractmethod
    def events(self, case_id: str, window: EvidenceWindow) -> tuple[CaseEvent, ...]:
        ...

    @abstractmethod
    def defects_for_platform(self, platform: str, window: EvidenceWindow) -> tuple[Defect, ...]:
        ...

    @abstractmethod
    def similar_closed_cases(
        self, symptom_class: str, platform: str, window: EvidenceWindow, limit: int = 20
    ) -> tuple[Case, ...]:
        """Cases closed BEFORE the cutoff. These may retain outcome fields —
        that is legitimate institutional memory, not leakage."""

    @abstractmethod
    def open_cases_by_signature(
        self, platform: str, sw_version: str, symptom_class: str,
        window: EvidenceWindow, days: int = 30,
    ) -> tuple[Case, ...]:
        ...

    @abstractmethod
    def required_escalation_fields(self, platform: str) -> tuple[str, ...]:
        ...

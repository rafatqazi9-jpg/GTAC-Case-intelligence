"""Fixture-backed adapter. Default for development and for the reverse test.

Enforces the evidence cutoff so modules cannot see the future. Real adapters
(Salesforce, Jira, Snowflake, Workato KB) subclass Adapter and must enforce
the same cutoff in their queries.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..models import Case, CaseEvent, Defect
from .base import Adapter, EvidenceWindow

DATA = Path(__file__).resolve().parents[2] / "data" / "mock"


def _dt(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


class MockAdapter(Adapter):
    def __init__(self, data_dir: Path = DATA) -> None:
        self._defects = tuple(
            Defect(
                id=d["id"], title=d["title"],
                affected_versions=tuple(d["affected_versions"]),
                fixed_in=d["fixed_in"], feature_tags=tuple(d["feature_tags"]),
                status=d["status"],
            )
            for d in json.loads((data_dir / "defects.json").read_text())
        )
        self._raw = {c["id"]: c for c in json.loads((data_dir / "cases.json").read_text())}
        self._meta = json.loads((data_dir / "meta.json").read_text())

    # ---------------------------------------------------------------- helpers
    def _case(self, raw: dict[str, Any], window: EvidenceWindow, *,
              keep_outcome: bool) -> Case:
        events = tuple(
            CaseEvent(at=_dt(e["at"]), kind=e["kind"], text=e["text"])  # type: ignore[arg-type]
            for e in raw["events"] if window.permits(_dt(e["at"]))      # type: ignore[arg-type]
        )
        closed_at = _dt(raw.get("closed_at"))
        # Outcome fields survive only for cases already closed at the cutoff.
        outcome_visible = keep_outcome and closed_at is not None and window.permits(closed_at)
        return Case(
            id=raw["id"], account=raw["account"], platform=raw["platform"],
            sw_version=raw["sw_version"], symptom_class=raw["symptom_class"],
            symptom_text=raw["symptom_text"],
            features_enabled=tuple(raw["features_enabled"]),
            region=raw["region"], created_at=_dt(raw["created_at"]),  # type: ignore[arg-type]
            events=events,
            initial_hypothesis=raw.get("initial_hypothesis") if outcome_visible else None,
            actual_root_cause=raw.get("actual_root_cause") if outcome_visible else None,
            closed_at=closed_at if outcome_visible else None,
        )

    def truth(self, case_id: str) -> tuple[str | None, str | None]:
        """Harness-only accessor for ground truth. Modules must never call this."""
        raw = self._raw[case_id]
        return raw.get("initial_hypothesis"), raw.get("actual_root_cause")

    def closure(self, case_id: str) -> tuple[str | None, str | None]:
        """Harness-only. How the case was closed, as recorded (closed_state,
        closure_comments). The two disagree on abandoned cases, which is why
        the harness reads the comments and not just the status field."""
        raw = self._raw[case_id]
        return raw.get("closed_state"), raw.get("closure_comments")

    @property
    def eval_case_ids(self) -> list[str]:
        return list(self._meta["eval_case_ids"])

    # ---------------------------------------------------------------- adapter
    def get_case(self, case_id: str, window: EvidenceWindow) -> Case:
        # The case under investigation never exposes its own outcome.
        return self._case(self._raw[case_id], window, keep_outcome=False)

    def events(self, case_id: str, window: EvidenceWindow) -> tuple[CaseEvent, ...]:
        return self.get_case(case_id, window).events

    def defects_for_platform(self, platform: str, window: EvidenceWindow) -> tuple[Defect, ...]:
        return self._defects

    def similar_closed_cases(
        self, symptom_class: str, platform: str, window: EvidenceWindow, limit: int = 20
    ) -> tuple[Case, ...]:
        out = []
        for raw in self._raw.values():
            if raw["symptom_class"] != symptom_class:
                continue
            closed = _dt(raw.get("closed_at"))
            if closed is None or not window.permits(closed):
                continue
            out.append(self._case(raw, window, keep_outcome=True))
        out.sort(key=lambda c: c.closed_at or c.created_at, reverse=True)
        return tuple(out[:limit])

    def open_cases_by_signature(
        self, platform: str, sw_version: str, symptom_class: str,
        window: EvidenceWindow, days: int = 30,
    ) -> tuple[Case, ...]:
        since = window.cutoff - timedelta(days=days)
        out = []
        for raw in self._raw.values():
            created = _dt(raw["created_at"])
            if created is None or not (since <= created <= window.cutoff):
                continue
            closed = _dt(raw.get("closed_at"))
            if closed is not None and window.permits(closed):
                continue  # already closed by the cutoff
            if (raw["platform"], raw["sw_version"], raw["symptom_class"]) != (
                platform, sw_version, symptom_class):
                continue
            out.append(self._case(raw, window, keep_outcome=False))
        return tuple(out)

    def required_escalation_fields(self, platform: str) -> tuple[str, ...]:
        req = self._meta["required_escalation_fields"]
        return tuple(req.get(platform, req["_default"]))

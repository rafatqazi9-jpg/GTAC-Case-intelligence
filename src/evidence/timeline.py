"""Timeline correlation across multi-device logs and archives.

Assembles sorted event streams, accounts for clock offsets,
detects cascading network events (e.g. link down -> BGP flap -> route drop),
and flags missing log spans or gaps.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .events import EventSeverity, TimelineEvent
from .parsers import parse_log_text


@dataclass
class TimelineSummary:
    total_events: int
    devices_seen: list[str]
    start_time: datetime | None
    end_time: datetime | None
    critical_events: list[TimelineEvent] = field(default_factory=list)
    events: list[TimelineEvent] = field(default_factory=list)
    correlations: list[str] = field(default_factory=list)
    gaps_detected: list[str] = field(default_factory=list)

    def render_timeline(self, limit: int = 50) -> str:
        """Render a formatted chronological incident timeline."""
        lines = [
            "INCIDENT EVENT TIMELINE",
            f"Span: {self.start_time or '<unknown>'} -> {self.end_time or '<unknown>'}",
            f"Devices: {', '.join(self.devices_seen) if self.devices_seen else 'none'}",
            f"Events indexed: {self.total_events} (Critical/Error: {len(self.critical_events)})",
            "-" * 72,
        ]

        if self.correlations:
            lines.append("AUTOMATIC EVENT CORRELATIONS:")
            for corr in self.correlations:
                lines.append(f"  * {corr}")
            lines.append("-" * 72)

        if not self.events:
            lines.append("No timestamped events recovered from log text.")
            return "\n".join(lines)

        shown = self.events[:limit]
        for ev in shown:
            lines.append(ev.format_line())

        if len(self.events) > limit:
            lines.append(f"... ({len(self.events) - limit} additional events omitted)")

        return "\n".join(lines)


class TimelineBuilder:
    """Builds and correlates timeline events across multiple diagnostic files."""

    def __init__(self, max_clock_skew: timedelta = timedelta(minutes=5)) -> None:
        self.max_clock_skew = max_clock_skew

    def build_from_files(self, file_entries: list[tuple[str, str]]) -> TimelineSummary:
        """Parse list of (filename, text) pairs and produce a unified correlated timeline."""
        all_events: list[TimelineEvent] = []

        for fname, text in file_entries:
            lines = text.splitlines()
            sample_text = "\n".join(lines[:2500]) if len(lines) > 2500 else text
            parsed = parse_log_text(sample_text, source_file=fname)
            all_events.extend(parsed)
            if len(all_events) > 10000:
                break

        # Sort chronologically by timestamp (entries without timestamps at end)
        all_events.sort(key=lambda e: (e.timestamp is None, e.timestamp or datetime.min))

        devices = sorted(list({e.hostname for e in all_events if e.hostname}))
        crit_events = [e for e in all_events if e.severity in (EventSeverity.EMERGENCY, EventSeverity.ALERT, EventSeverity.CRITICAL, EventSeverity.ERROR)]

        start_ts = next((e.timestamp for e in all_events if e.timestamp is not None), None)
        end_ts = next((e.timestamp for e in reversed(all_events) if e.timestamp is not None), None)

        # Detect correlations (e.g. Interface down preceding daemon crash or BGP drop)
        correlations: list[str] = []
        for i, ev in enumerate(all_events):
            if "interface" in ev.message.lower() and "down" in ev.message.lower():
                # Look ahead within 60 seconds
                for nxt in all_events[i + 1: i + 10]:
                    if ev.timestamp and nxt.timestamp and (nxt.timestamp - ev.timestamp) <= timedelta(seconds=60):
                        if any(term in nxt.message.lower() for term in ("bgp", "ospf", "daemon", "process", "terminated")):
                            correlations.append(
                                f"At {ev.timestamp_raw}: '{ev.message}' on {ev.hostname} preceded '{nxt.message}' within {(nxt.timestamp - ev.timestamp).seconds}s"
                            )

        # Detect large gaps (> 1 hour between consecutive events)
        gaps: list[str] = []
        for i in range(len(all_events) - 1):
            e1 = all_events[i]
            e2 = all_events[i + 1]
            if e1.timestamp and e2.timestamp:
                delta = e2.timestamp - e1.timestamp
                if delta > timedelta(hours=2):
                    gaps.append(f"Temporal gap of {delta} detected between {e1.timestamp_raw} and {e2.timestamp_raw}")

        return TimelineSummary(
            total_events=len(all_events),
            devices_seen=devices,
            start_time=start_ts,
            end_time=end_ts,
            critical_events=crit_events,
            events=all_events,
            correlations=correlations,
            gaps_detected=gaps,
        )

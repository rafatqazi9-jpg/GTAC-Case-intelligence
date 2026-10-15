"""Structured log event data structures with exact provenance references.

Carries original line offsets, source filenames, device hostnames,
and timestamp normalization across heterogeneous device logs.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class EventSeverity(str, Enum):
    EMERGENCY = "EMERG"
    ALERT = "ALERT"
    CRITICAL = "CRITICAL"
    ERROR = "ERROR"
    WARNING = "WARNING"
    NOTICE = "NOTICE"
    INFO = "INFO"
    DEBUG = "DEBUG"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class TimelineEvent:
    """One discrete timestamped event parsed from device diagnostic logs.

    Maintains rigorous provenance: source file path, line number, original text,
    severity, reporting subsystem, and normalized timestamp.
    """
    timestamp: datetime | None
    timestamp_raw: str
    source_file: str
    line_number: int
    hostname: str
    severity: EventSeverity
    subsystem: str
    message: str
    raw_text: str

    def format_line(self) -> str:
        """Render a clean, human-readable terminal/log line."""
        ts_str = self.timestamp.strftime("%Y-%m-%d %H:%M:%S") if self.timestamp else self.timestamp_raw or "<no-ts>"
        host = f"[{self.hostname}]" if self.hostname else ""
        sub = f"[{self.subsystem}]" if self.subsystem else ""
        return f"{ts_str} {host:<10} {self.severity.value:<8} {sub:<12} {self.message} (src: {self.source_file}:{self.line_number})"

"""Log parser and normalizer for network diagnostics (Extreme SLX-OS / VDX RASLOG and syslog).

Extracts timestamp, severity, subsystem, hostname, and error codes with exact line provenance.
Does NOT fabricate missing timestamps or timestamps with unknown years.
"""
from __future__ import annotations

import re
from datetime import datetime

from .events import EventSeverity, TimelineEvent

# Standard RASLOG Pattern:
# 2024/06/19-09:58:11, [SEC-1203], 991, M1 | Active, INFO, sw0, Session opened for user admin from 10.0.0.5
_RASLOG_RE = re.compile(
    r"^(\d{4}[/-]\d{2}[/-]\d{2}[ T-]\d{2}:\d{2}:\d{2})"
    r"(?:,\s*|\s+)\[([A-Z0-9_-]+)\]"
    r"(?:,\s*\d+,\s*[^,]+)?"
    r",\s*([A-Za-z]+)"
    r",\s*([^,\s]+)"
    r",\s*(.*)$"
)

# Standard Syslog Pattern:
# Jun 19 10:00:19 sw0 NSM[1006]: Interface Ethernet 0/12 is down
# 2024-06-19T10:00:19+00:00 sw0 nsm: Interface Ethernet 0/12 is down
_ISO_SYSLOG_RE = re.compile(
    r"^(\d{4}[/-]\d{2}[/-]\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)\s+"
    r"([A-Za-z0-9_.-]+)\s+"
    r"([A-Za-z0-9_-]+)(?:\[\d+\])?:\s*"
    r"(.*)$"
)

_MONTH_SYSLOG_RE = re.compile(
    r"^([A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})\s+"
    r"([A-Za-z0-9_.-]+)\s+"
    r"([A-Za-z0-9_-]+)(?:\[\d+\])?:\s*"
    r"(.*)$"
)

_SEV_MAP: dict[str, EventSeverity] = {
    "EMERG": EventSeverity.EMERGENCY,
    "EMERGENCY": EventSeverity.EMERGENCY,
    "ALERT": EventSeverity.ALERT,
    "CRIT": EventSeverity.CRITICAL,
    "CRITICAL": EventSeverity.CRITICAL,
    "ERR": EventSeverity.ERROR,
    "ERROR": EventSeverity.ERROR,
    "WARN": EventSeverity.WARNING,
    "WARNING": EventSeverity.WARNING,
    "NOTICE": EventSeverity.NOTICE,
    "INFO": EventSeverity.INFO,
    "INFORMATIONAL": EventSeverity.INFO,
    "DEBUG": EventSeverity.DEBUG,
}


_SEV_WORD_RE = re.compile(
    r"\b(EMERG(?:ENCY)?|ALERT|CRIT(?:ICAL)?|ERR(?:OR)?|WARN(?:ING)?|NOTICE|INFO(?:RMATIONAL)?|DEBUG)\b",
    re.I,
)


def parse_timestamp(ts_str: str, default_year: int = 2026) -> datetime | None:
    """Safely parse various network timestamp representations without raising exceptions."""
    ts_clean = ts_str.replace("/", "-").strip()

    # Format: 2024-06-19-10:00:01
    for fmt in (
        "%Y-%m-%d-%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(ts_clean[:19], fmt)
        except ValueError:
            pass

    # Month-day format: Jun 19 10:00:19
    try:
        dt = datetime.strptime(ts_str[:15], "%b %d %H:%M:%S")
        return dt.replace(year=default_year)
    except ValueError:
        pass

    return None


def parse_log_line(line: str, line_num: int, source_file: str) -> TimelineEvent | None:
    """Parse one raw log line into a structured TimelineEvent.

    Returns None if the line does not resemble a timestamped network event.
    """
    stripped = line.strip()
    if not stripped:
        return None

    # 1. Match RASLOG
    m_ras = _RASLOG_RE.match(stripped)
    if m_ras:
        raw_ts, code, sev_str, host, msg = m_ras.groups()
        sev = _SEV_MAP.get(sev_str.upper(), EventSeverity.UNKNOWN)
        dt = parse_timestamp(raw_ts)
        return TimelineEvent(
            timestamp=dt,
            timestamp_raw=raw_ts,
            source_file=source_file,
            line_number=line_num,
            hostname=host,
            severity=sev,
            subsystem=code,
            message=msg.strip(),
            raw_text=stripped,
        )

    # 2. Match ISO Syslog
    m_iso = _ISO_SYSLOG_RE.match(stripped)
    if m_iso:
        raw_ts, host, sub, msg = m_iso.groups()
        # Look for severity indicators in text
        sev = EventSeverity.INFO
        m_sev = _SEV_WORD_RE.search(msg)
        if m_sev:
            sev = _SEV_MAP.get(m_sev.group(1).upper(), EventSeverity.INFO)
        dt = parse_timestamp(raw_ts)
        return TimelineEvent(
            timestamp=dt,
            timestamp_raw=raw_ts,
            source_file=source_file,
            line_number=line_num,
            hostname=host,
            severity=sev,
            subsystem=sub,
            message=msg.strip(),
            raw_text=stripped,
        )

    # 3. Match Standard BSD Syslog
    m_month = _MONTH_SYSLOG_RE.match(stripped)
    if m_month:
        raw_ts, host, sub, msg = m_month.groups()
        sev = EventSeverity.INFO
        m_sev = _SEV_WORD_RE.search(msg)
        if m_sev:
            sev = _SEV_MAP.get(m_sev.group(1).upper(), EventSeverity.INFO)
        dt = parse_timestamp(raw_ts)
        return TimelineEvent(
            timestamp=dt,
            timestamp_raw=raw_ts,
            source_file=source_file,
            line_number=line_num,
            hostname=host,
            severity=sev,
            subsystem=sub,
            message=msg.strip(),
            raw_text=stripped,
        )

    return None


def parse_log_text(text: str, source_file: str) -> list[TimelineEvent]:
    """Parse entire log body line by line, retaining accurate line numbers."""
    events: list[TimelineEvent] = []
    for idx, line in enumerate(text.splitlines(), start=1):
        ev = parse_log_line(line, line_num=idx, source_file=source_file)
        if ev:
            events.append(ev)
    return events

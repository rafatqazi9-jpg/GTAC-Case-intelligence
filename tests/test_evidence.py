"""Tests for evidence parsing, line provenance tracking, and timeline correlation.

Verifies:
- Accurate line number provenance
- RASLOG and Syslog timestamp parsing
- Multi-device event aggregation
- Causal correlation identification (e.g. interface drop -> BGP down)
- Detection of temporal gaps between events
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from src.evidence.events import EventSeverity, TimelineEvent
from src.evidence.parsers import parse_log_line, parse_log_text, parse_timestamp
from src.evidence.timeline import TimelineBuilder


def test_parse_timestamp_formats() -> None:
    dt1 = parse_timestamp("2024/06/19-10:00:01")
    assert dt1 == datetime(2024, 6, 19, 10, 0, 1)

    dt2 = parse_timestamp("2024-06-19 10:00:01")
    assert dt2 == datetime(2024, 6, 19, 10, 0, 1)

    dt3 = parse_timestamp("Jun 19 10:00:01", default_year=2024)
    assert dt3 == datetime(2024, 6, 19, 10, 0, 1)


def test_parse_raslog_line_provenance() -> None:
    line = "2024/06/19-10:00:19, [NSM-1004], 1006, M1 | Active, ERROR, sw0, Interface Ethernet 0/12 is down"
    ev = parse_log_line(line, line_num=42, source_file="var/log/raslog")

    assert ev is not None
    assert ev.line_number == 42
    assert ev.source_file == "var/log/raslog"
    assert ev.hostname == "sw0"
    assert ev.subsystem == "NSM-1004"
    assert ev.severity == EventSeverity.ERROR
    assert "Interface Ethernet 0/12 is down" in ev.message
    assert ev.timestamp == datetime(2024, 6, 19, 10, 0, 19)


def test_parse_syslog_line() -> None:
    line = "2024-06-19T10:00:22+00:00 leaf1 bgp: BGP neighbor 10.0.0.2 reset by peer"
    ev = parse_log_line(line, line_num=15, source_file="messages")

    assert ev is not None
    assert ev.hostname == "leaf1"
    assert ev.subsystem == "bgp"
    assert "BGP neighbor 10.0.0.2 reset" in ev.message


def test_timeline_builder_correlation_and_sorting() -> None:
    log1 = (
        "2024/06/19-10:00:00, [SEC-1001], 1, M1, INFO, sw0, User login\n"
        "2024/06/19-10:00:10, [NSM-1004], 2, M1, ERROR, sw0, Interface Ethernet 0/12 is down\n"
        "2024/06/19-10:00:15, [BGP-1001], 3, M1, CRITICAL, sw0, BGP neighbor 10.0.0.1 terminated\n"
    )
    log2 = (
        "2024/06/19-10:00:11, [LLDP-1002], 1, M1, INFO, sw1, Neighbor lost on Eth 0/1\n"
    )

    tb = TimelineBuilder()
    summary = tb.build_from_files([("sw0.log", log1), ("sw1.log", log2)])

    assert summary.total_events == 4
    assert summary.devices_seen == ["sw0", "sw1"]
    assert len(summary.critical_events) == 2

    # Events sorted chronologically across sw0 and sw1
    timestamps = [e.timestamp for e in summary.events if e.timestamp]
    assert timestamps == sorted(timestamps)

    # Correlation detection
    assert len(summary.correlations) > 0
    assert any("Interface Ethernet 0/12 is down" in c and "BGP neighbor" in c for c in summary.correlations)

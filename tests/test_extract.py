"""Tests for the evidence extractor.

The sample below is SYNTHETIC. It reproduces the *shape* of a real escalated
SLX case -- version in the subject bracket, defect IDs with metadata in the
description, a fabric inventory table, a stated log gap -- with every
identifier, address and hostname invented. No real case text is in this repo.
"""
from __future__ import annotations

from src.extract import (compare_versions, extract_all, find_defects,
                         find_error_signatures, find_evidence_gaps,
                         find_platforms, parse_device_table, running_version,
                         summarise_log, version_key)
from src.models import Confidence

SAMPLE = """\
[SLX-OS 20.7.3a] "OS is not ready. Please login later" seen while trying to login

Problem Description
===============
We are experiencing an SLX9740/8720 login issue. Our software is a fresh
installation rather than an upgrade from an older release.

The following software defects were closed with code changes in SLX-OS 20.7.1a:
- Parent Defect ID: SLXOS-11111
- Reported in Release: SLXOS 20.6.3a
- Condition: box made to run with multiple CLI/NETCONF session.
Also referenced: SLXOS-22222

Project team did not keep console session logs during fresh installation.
We have tried to collect support save from the fabric manager, but it was not
happening.

| IP ADDRESS | POD | HOST NAME | ASN | ROLE | DEVICE STATE | APP STATE |
|------------|-----|-----------|-----|------|--------------|-----------|
| 10.0.0.23 | 1 | LAB-SPINE-01 | 65001 | spine | provisioned | cfg in-sync |
| 10.0.0.12 | 1 | LAB-LEAF-02 | 65006 | leaf | provisioned | cfg in-sync |
| 10.0.0.27 | 1 | LAB-BORDER-03 | 65155 | borderleaf | provisioned | cfg in-sync |

SLX: 20.7.3a
EFA version : 3.8.7
"""


# ------------------------------------------------------------------ versions
def test_running_version_prefers_a_line_that_states_it() -> None:
    r = running_version(SAMPLE, "case")
    assert r is not None
    assert r.value == "20.7.3a"
    assert r.confidence is Confidence.HIGH
    assert r.evidence


def test_running_version_returns_none_when_nothing_states_it() -> None:
    assert running_version("A defect was fixed in 1.2.3 last year.", "x") is None


def test_fix_version_is_not_mistaken_for_the_running_version() -> None:
    """20.7.1a appears as a FIX version. The running box is on 20.7.3a."""
    r = running_version(SAMPLE, "case")
    assert r is not None and r.value != "20.7.1a"
    assert compare_versions(r.value, "20.7.1a") == 1


def test_version_ordering_handles_patch_letters() -> None:
    assert version_key("20.7.3a") != version_key("20.7.1a")
    assert compare_versions("20.6.2ab", "20.6.2a") == 1
    assert compare_versions("20.7.10a", "20.7.2a") == 1


# ------------------------------------------------------------------- defects
def test_defect_ids_extracted_and_labelled_ones_rank_higher() -> None:
    ds = {d.value: d for d in find_defects(SAMPLE, "case")}
    assert "SLXOS-11111" in ds and "SLXOS-22222" in ds
    assert ds["SLXOS-11111"].confidence is Confidence.HIGH    # "Parent Defect ID:"
    assert ds["SLXOS-22222"].confidence is Confidence.MEDIUM  # bare mention


def test_case_numbers_are_not_read_as_defects() -> None:
    assert not find_defects("Related to CS-6100050 only.", "case")


# ----------------------------------------------------------------- platforms
def test_platforms_extracted() -> None:
    vals = {p.value for p in find_platforms(SAMPLE, "case")}
    assert "SLX9740" in vals


# ------------------------------------------------------------- error strings
def test_error_signatures_collapse_to_the_longest_form() -> None:
    """A case quotes the same error at several lengths - full in the subject,
    clipped in a note, again with a trailing stop. Listing all three reads as
    three separate symptoms when there is one."""
    text = (
        'Subject: "OS is not ready. Please login later" seen at login\n'
        'Note: the "OS is not ready" banner appeared again\n'
        'Symptom: throwing error "OS is not ready. Please login later."\n'
    )
    sigs = [e.value for e in find_error_signatures(text, "case")]
    assert len(sigs) == 1, f"expected one collapsed signature, got {sigs}"
    assert sigs[0] == "OS is not ready. Please login later"


def test_distinct_errors_are_not_collapsed() -> None:
    text = ('got "not reachable. Please retry the operation"\n'
            'also "OS is not ready. Please login later"\n')
    assert len(find_error_signatures(text, "case")) == 2


def test_error_signature_extracted_and_commands_skipped() -> None:
    sigs = [e.value for e in find_error_signatures(SAMPLE, "case")]
    assert "OS is not ready. Please login later" in sigs
    cmd = find_error_signatures('ran "show version" on the box', "case")
    assert not cmd, "quoted shell commands are procedure, not symptom"


# ------------------------------------------------------------ device tables
def test_device_table_parsed_by_header_not_position() -> None:
    devs = parse_device_table(SAMPLE)
    assert len(devs) == 3
    assert {d.role for d in devs} == {"spine", "leaf", "borderleaf"}
    assert all(d.app_state == "cfg in-sync" for d in devs)


def test_device_table_reordered_columns_still_map_correctly() -> None:
    table = (
        "| ROLE | IP ADDRESS | DEVICE STATE |\n"
        "| leaf | 10.0.0.5 | provisioned |\n"
    )
    devs = parse_device_table(table)
    assert len(devs) == 1
    assert devs[0].ip == "10.0.0.5" and devs[0].role == "leaf"


# -------------------------------------------------------------- evidence gaps
def test_states_log_gaps() -> None:
    gaps = {g.value for g in find_evidence_gaps(SAMPLE, "case")}
    assert "console/session logs were not retained" in gaps
    assert "supportsave did not complete" in gaps


def test_every_extraction_carries_evidence() -> None:
    ev = extract_all(SAMPLE, "case")
    every = (ev.versions + ev.defects + ev.platforms + ev.errors + ev.gaps)
    assert every
    for e in every:
        assert e.evidence.strip(), f"{e.kind} {e.value} has no evidence line"
        assert e.source == "case"


def test_missing_reports_what_could_not_be_established() -> None:
    ev = extract_all("Nothing useful in here at all.", "case")
    assert "sw_version (no line states the running release)" in ev.missing()


def test_features_enabled_is_always_reported_missing() -> None:
    """Even on the richest case text, which features are ENABLED cannot be read
    from prose - it is a property of the running config. m01 needs it, so the
    extractor must keep saying so rather than letting a full-looking result
    imply every input was recovered."""
    ev = extract_all(SAMPLE, "case")
    assert any("features_enabled" in m for m in ev.missing())


def test_ip_addresses_are_not_reported_as_versions() -> None:
    """10.0.0.27 matches the version pattern as '10.0.0'. Printing an IP under
    'release strings' discredits every other line on the report."""
    from src.extract import find_versions
    vals = {v.value for v in find_versions("device --ip 10.0.0.27 up", "x")}
    assert not vals, f"IP leaked into version extraction: {vals}"
    # A genuine version on the same line still survives.
    vals2 = {v.value for v in find_versions("SLX: 20.7.3a at 10.0.0.27", "x")}
    assert vals2 == {"20.7.3a"}


# --------------------------------------------------------------- log summary
LOG = """\
2024/06/19-10:00:01, [SEC-3020], 100, M1 | Active, WARNING, sw0, Login failed for user admin
2024/06/19-10:00:02, [SEC-3020], 101, M1 | Active, WARNING, sw0, Login failed for user admin
2024/06/19-10:00:03, [SEC-3020], 102, M1 | Active, WARNING, sw0, Login failed for user admin
2024/06/19-10:05:00, [NSM-1004], 103, M1 | Active, ERROR, sw0, Interface 0/1 down
2024/06/19-10:09:59, [SEC-1203], 104, M1 | Active, INFO, sw0, Session opened
"""


def test_log_summary_counts_and_spans() -> None:
    s = summarise_log(LOG)
    assert s.lines == 5
    assert s.severities["WARNING"] == 3
    assert s.worst == "ERROR"
    assert s.first_timestamp == "2024/06/19-10:00:01"
    assert s.last_timestamp == "2024/06/19-10:09:59"


def test_log_summary_clusters_repeats_ignoring_variable_parts() -> None:
    """Three login failures differ only by sequence number. They must count as
    one repeated message, not three unique ones - repetition is the signal."""
    s = summarise_log(LOG)
    assert s.top_messages, "expected a repeated message"
    msg, count = s.top_messages[0]
    assert count == 3
    assert "Login failed" in msg


def test_log_summary_reports_raslog_codes() -> None:
    codes = dict(summarise_log(LOG).raslog_codes)
    assert codes["[SEC-3020]"] == 3
    assert codes["[NSM-1004]"] == 1


def test_log_summary_survives_a_log_with_nothing_recognisable() -> None:
    s = summarise_log("qwerty\nasdf\n")
    assert s.lines == 2 and s.worst is None and s.first_timestamp is None

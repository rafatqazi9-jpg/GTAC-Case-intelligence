"""Read case text or a device log and report what can be established from it.

    python -m src.analyze case.txt
    python -m src.analyze raslog.txt --log
    python -m src.analyze case.txt --json

Read-only. Takes a local file, prints what it found and what is still missing.
It never contacts a system of record and never writes anything.

The point is not to guess the answer. It is to turn prose and console output
into the fields the Phase 1 modules need, and to say plainly which fields it
could not establish -- because on real cases those fields are blank in
Salesforce and the information is buried in the description instead.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .extract import (CaseEvidence, Extraction, LogSummary, compare_versions,
                      extract_all, summarise_log)


def _rows(items: list[Extraction], limit: int) -> list[Extraction]:
    return items[:limit]


def _print_extractions(title: str, items: list[Extraction], limit: int) -> None:
    if not items:
        return
    print(f"{title}  ({len(items)})")
    for e in _rows(items, limit):
        print(f"  {e.value}")
        print(f"      [{e.confidence.value}] {e.source}: {e.evidence}")
    if len(items) > limit:
        print(f"  ... {len(items) - limit} more")
    print()


def report_case(ev: CaseEvidence, *, limit: int) -> None:
    print("=" * 74)
    print("EVIDENCE EXTRACTED")
    print("=" * 74)

    if ev.running_version:
        r = ev.running_version
        print(f"RUNNING VERSION  {r.value}")
        print(f"      [{r.confidence.value}] {r.source}: {r.evidence}")
    else:
        print("RUNNING VERSION  not established")
        print("      No line states the running release. Other release strings "
              "may appear\n      below, but a fix version in a release note is "
              "not a running version.")
    print()

    _print_extractions("PLATFORMS", ev.platforms, limit)
    _print_extractions("DEFECT IDs", ev.defects, limit)
    _print_extractions("ERROR SIGNATURES", ev.errors, limit)

    # Versions other than the running one, ordered against it where possible.
    others = [e for e in ev.versions
              if not ev.running_version or e.value != ev.running_version.value]
    if others:
        print(f"OTHER RELEASE STRINGS  ({len(others)})")
        for e in _rows(others, limit):
            rel = ""
            if ev.running_version:
                c = compare_versions(e.value, ev.running_version.value)
                if c is None:
                    rel = "  (not comparable)"
                elif c < 0:
                    rel = "  (older than running)"
                elif c > 0:
                    rel = "  (NEWER than running)"
                else:
                    rel = "  (same as running)"
            print(f"  {e.value}{rel}")
            print(f"      {e.source}: {e.evidence}")
        if len(others) > limit:
            print(f"  ... {len(others) - limit} more")
        print()

    if ev.devices:
        print(f"DEVICE INVENTORY  ({len(ev.devices)})")
        roles: dict[str, int] = {}
        for d in ev.devices:
            roles[d.role or "?"] = roles.get(d.role or "?", 0) + 1
        print("  " + ", ".join(f"{n}x {r}" for r, n in sorted(roles.items())))
        for d in ev.devices:
            print(f"    {d.ip:<16} {d.role:<12} {d.state:<14} {d.app_state}")
        print()

    if ev.gaps:
        print(f"EVIDENCE GAPS  ({len(ev.gaps)})")
        for e in ev.gaps:
            print(f"  {e.value}")
            print(f"      {e.source}: {e.evidence}")
        print()

    missing = ev.missing()
    print("STILL MISSING FOR THE PHASE 1 MODULES")
    if missing:
        for m in missing:
            print(f"  - {m}")
        print("  A live adapter must fetch these; they were not recoverable "
              "from this text.")
    else:
        print("  Nothing - every module input was recovered from the text.")
    print()


def report_log(s: LogSummary, *, limit: int) -> None:
    print("=" * 74)
    print("LOG SUMMARY")
    print("=" * 74)
    print(f"  lines        {s.lines}")
    span = (f"{s.first_timestamp}  ->  {s.last_timestamp}"
            if s.first_timestamp else "no timestamps recognised")
    print(f"  time span    {span}")
    print(f"  worst level  {s.worst or 'none recognised'}")
    if s.severities:
        print("  severities   " + ", ".join(
            f"{k}={v}" for k, v in sorted(s.severities.items(),
                                          key=lambda kv: -kv[1])))
    print()

    if s.raslog_codes:
        print("RASLOG CODES")
        for code, n in s.raslog_codes[:limit]:
            print(f"  {n:>6}x  {code}")
        print()

    if s.top_messages:
        print("REPEATED MESSAGES  (variable parts collapsed)")
        for msg, n in s.top_messages[:limit]:
            print(f"  {n:>6}x  {msg}")
        print()
    else:
        print("REPEATED MESSAGES  none - no message occurs more than once")
        print()


def as_json(ev: CaseEvidence | None, log: LogSummary | None) -> dict[str, Any]:
    """Serialise a run. Shared by the CLI and the local server so the two can
    never disagree about what the extractor returned."""
    def ex(e: Extraction) -> dict[str, str]:
        return {"kind": e.kind, "value": e.value, "source": e.source,
                "evidence": e.evidence, "confidence": e.confidence.value}

    out: dict[str, Any] = {}
    if ev is not None:
        out["evidence"] = {
            "running_version": ex(ev.running_version) if ev.running_version else None,
            "versions": [ex(e) for e in ev.versions],
            "defects": [ex(e) for e in ev.defects],
            "platforms": [ex(e) for e in ev.platforms],
            "errors": [ex(e) for e in ev.errors],
            "gaps": [ex(e) for e in ev.gaps],
            "devices": [{"ip": d.ip, "hostname": d.hostname, "role": d.role,
                         "state": d.state, "app_state": d.app_state}
                        for d in ev.devices],
            "missing": ev.missing(),
        }
    if log is not None:
        out["log"] = {
            "lines": log.lines, "severities": log.severities,
            "first_timestamp": log.first_timestamp,
            "last_timestamp": log.last_timestamp,
            "worst": log.worst,
            "top_messages": [{"message": m, "count": c} for m, c in log.top_messages],
            "raslog_codes": [{"code": c, "count": n} for c, n in log.raslog_codes],
        }
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Extract case evidence from text or summarise a device log")
    ap.add_argument("path", help="case text, description dump, or log file")
    ap.add_argument("--log", action="store_true",
                    help="also summarise as a device log (severities, repeats)")
    ap.add_argument("--only-log", action="store_true",
                    help="log summary only, skip evidence extraction")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--limit", type=int, default=10,
                    help="max rows per section (default 10)")
    args = ap.parse_args(argv)

    p = Path(args.path)
    if not p.is_file():
        print(f"not a file: {p}", file=sys.stderr)
        return 2
    text = p.read_text(encoding="utf-8", errors="replace")

    ev = None if args.only_log else extract_all(text, source=p.name)
    log = summarise_log(text) if (args.log or args.only_log) else None

    if args.json:
        print(json.dumps(as_json(ev, log), indent=1))
        return 0

    if ev is not None:
        report_case(ev, limit=args.limit)
    if log is not None:
        report_log(log, limit=args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

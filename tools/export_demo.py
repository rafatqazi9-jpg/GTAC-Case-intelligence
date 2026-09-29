"""Export real brief output to JSON for the demo page.

Everything the demo renders comes from running the actual modules against the
actual fixtures. Nothing on that page is hand-written prose dressed up as tool
output -- if a claim appears in the demo, this script produced it.

    python tools/export_demo.py
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import src.brief as brief_mod
from src.adapters.base import EvidenceWindow
from src.adapters.mock import MockAdapter
from src.brief import build
from src.extract import Extraction, extract_all, summarise_log
from src.harness.reverse_test import _defect_ids, evaluate_case, gradeable
from src.models import Confidence
from src.modules import (m01_defect_exposure, m05_known_traps,
                         m06_fleet_correlation, m07_escalation_package)

OUT = Path(__file__).resolve().parents[1] / "demo" / "demo_data.json"
AT_DAYS = 2
ALL_MODULES = (m01_defect_exposure, m05_known_traps,
               m06_fleet_correlation, m07_escalation_package)


def _cutoff(adapter: MockAdapter, cid: str, at_days: int) -> datetime:
    return datetime.fromisoformat(adapter._raw[cid]["created_at"]) + timedelta(days=at_days)


SAMPLES = Path(__file__).resolve().parents[1] / "data" / "samples"


def export_analyzer() -> dict[str, Any]:
    """Run the real extractor over the sample case and log.

    The demo renders exactly what these calls return. The regexes are NOT
    reimplemented in the page's JavaScript: two implementations of the same
    parser drift, and the page would end up showing results the actual tool
    would not produce -- which is the failure this project keeps guarding
    against.
    """
    def ex(e: Extraction) -> dict[str, str]:
        return {"kind": e.kind, "value": e.value, "source": e.source,
                "evidence": e.evidence, "confidence": e.confidence.value}

    case_text = (SAMPLES / "sample_case.txt").read_text(encoding="utf-8")
    log_text = (SAMPLES / "sample_raslog.txt").read_text(encoding="utf-8")

    ev = extract_all(case_text, source="sample_case.txt")
    log = summarise_log(log_text)

    # What Salesforce carried as STRUCTURED fields on the three live cases this
    # was checked against. The contrast with the extracted column is the point.
    sf_fields = [
        {"label": "Product Platform", "value": "SLX", "note": ""},
        {"label": "Firmware", "value": "", "note": "blank on all 3 live cases"},
        {"label": "Product Feature", "value": "", "note": "blank on all 3 live cases"},
        {"label": "Symptom Reason", "value": "Platform or Console not Responding",
         "note": "picklist; miscoded on 1 of 3"},
        {"label": "Serial Number", "value": "", "note": "blank"},
        {"label": "Technology", "value": "", "note": "blank"},
    ]

    return {
        "source_excerpt": "\n".join(case_text.splitlines()[:6]),
        "sf_fields": sf_fields,
        "running_version": ex(ev.running_version) if ev.running_version else None,
        "defects": [ex(e) for e in ev.defects],
        "platforms": [ex(e) for e in ev.platforms],
        "errors": [ex(e) for e in ev.errors][:3],
        "gaps": [ex(e) for e in ev.gaps],
        "devices": [{"ip": d.ip, "hostname": d.hostname, "role": d.role,
                     "state": d.state, "app_state": d.app_state}
                    for d in ev.devices],
        "missing": ev.missing(),
        "log": {
            "lines": log.lines,
            "worst": log.worst,
            "severities": log.severities,
            "first_timestamp": log.first_timestamp,
            "last_timestamp": log.last_timestamp,
            "top_messages": [{"message": m, "count": c} for m, c in log.top_messages],
            "raslog_codes": [{"code": c, "count": n} for c, n in log.raslog_codes],
        },
    }


def export() -> dict[str, Any]:
    adapter = MockAdapter()
    brief_mod.MODULES = ALL_MODULES
    known = {d.id for d in adapter._defects}

    # Same corpus guard the harness applies. An ungradeable case must not be
    # rendered as though it had been scored.
    eval_ids: list[str] = []
    excluded: list[dict[str, str]] = []
    for cid in adapter.eval_case_ids:
        _, cause_txt = adapter.truth(cid)
        state, comments = adapter.closure(cid)
        ok, why = gradeable(cause_txt, state, comments)
        if ok:
            eval_ids.append(cid)
        else:
            excluded.append({"id": cid, "why": why,
                             "closed_state": state or "",
                             "symptom_text": adapter._raw[cid]["symptom_text"]})

    cases: list[dict[str, Any]] = []
    for cid in eval_ids:
        cutoff = _cutoff(adapter, cid, AT_DAYS)
        window = EvidenceWindow(cutoff)
        case = adapter.get_case(cid, window)
        brief = build(cid, adapter, window, threshold=Confidence.MEDIUM)
        outcome = evaluate_case(adapter, cid, AT_DAYS, Confidence.MEDIUM)
        hyp, cause = adapter.truth(cid)

        raw = adapter._raw[cid]
        state, comments = adapter.closure(cid)
        # The case header in the shape TAC sees in Salesforce, using the real
        # field names. `src` records which model field each one feeds, so the
        # demo shows what a live adapter has to populate.
        #
        # Closed State and Closure Comments are DELIBERATELY ABSENT. On the case
        # under investigation they are post-cutoff: the closure comment names
        # the root cause outright. The adapter strips exactly these outcome
        # fields, and putting them in the header would leak the answer into a
        # brief that is supposed to be reasoning without it. They belong in the
        # harness-only reveal, below.
        fields = [
            ("Case Number",      cid,                             "id"),
            ("Type",             raw.get("case_type", ""),        "-"),
            ("Product Platform", case.platform,                   "platform"),
            ("Firmware",         case.sw_version,                 "sw_version"),
            ("Product Feature",  ", ".join(case.features_enabled), "features_enabled"),
            ("Symptom Reason",   case.symptom_class,              "symptom_class"),
            ("Case Region",      case.region,                     "region"),
        ]

        cases.append({
            "id": cid,
            "platform": case.platform,
            "sw_version": case.sw_version,
            "symptom_class": case.symptom_class,
            "symptom_text": case.symptom_text,
            "features": list(case.features_enabled),
            "region": case.region,
            "fields": [{"label": la, "value": v, "src": s} for la, v, s in fields],
            "created_at": case.created_at.isoformat(),
            "cutoff": cutoff.isoformat(),
            "events": [{"at": e.at.isoformat(), "kind": e.kind, "text": e.text}
                       for e in case.events],
            "abstained": brief.abstained,
            "abstain_reason": brief.abstain_reason,
            "findings": [{"module": f.module, "kind": f.kind,
                          "statement": f.statement,
                          "confidence": f.confidence.value,
                          "sources": list(f.sources)}
                         for f in brief.findings],
            # Harness-only. The modules never saw these; shown so a viewer can
            # grade the brief instead of taking its word for it.
            "truth": {"initial_hypothesis": hyp, "actual_root_cause": cause,
                      "cause_ids": sorted(_defect_ids(cause or "")),
                      "closed_state": state or "",
                      "closure_comments": comments or ""},
            "verdict": outcome.verdict,
            "asserted": outcome.n_asserted,
            "n_eliminated": outcome.n_eliminated,
        })

    # Ablation: same gate, one module at a time.
    ablation = []
    for label, mods in (
        ("all four", ALL_MODULES),
        ("m05 only", (m05_known_traps,)),
        ("m01 only", (m01_defect_exposure,)),
        ("m01 + m07", (m01_defect_exposure, m07_escalation_package)),
    ):
        brief_mod.MODULES = mods
        outs = [evaluate_case(adapter, cid, AT_DAYS, Confidence.MEDIUM)
                for cid in eval_ids]
        ablation.append({
            "label": label,
            "HIT": sum(1 for o in outs if o.verdict == "HIT"),
            "PARTIAL": sum(1 for o in outs if o.verdict == "PARTIAL"),
            "ABSTAIN": sum(1 for o in outs if o.verdict == "ABSTAIN"),
            "MISS": sum(1 for o in outs if o.verdict == "MISS"),
            "asserted": sum(o.n_asserted for o in outs),
        })
    brief_mod.MODULES = ALL_MODULES

    defects = [{"id": d.id, "title": d.title,
                "affected_versions": list(d.affected_versions),
                "fixed_in": d.fixed_in, "feature_tags": list(d.feature_tags),
                "status": d.status}
               for d in adapter._defects]

    counts = {k: sum(1 for c in cases if c["verdict"] == k)
              for k in ("HIT", "PARTIAL", "ABSTAIN", "MISS", "RULED-OUT")}

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "at_days": AT_DAYS,
        "threshold": "medium",
        "n_cases_total": len(adapter._raw),
        "n_defects": len(defects),
        "cases": cases,
        "defects": defects,
        "ablation": ablation,
        "counts": counts,
        "total_asserted": sum(c["asserted"] for c in cases),
        "unscoreable": [c["id"] for c in cases if not c["truth"]["cause_ids"]],
        "excluded": excluded,
        "analyzer": export_analyzer(),
    }


def main() -> None:
    data = export()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, indent=1) + "\n")
    print(f"wrote {OUT}  ({len(data['cases'])} cases, {data['counts']})")


if __name__ == "__main__":
    main()

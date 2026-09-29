"""Reverse test - the validation gate.

Replays closed cases that were escalated, using only evidence available at a
chosen point in the case, and asks: would the brief have flagged the actual
root cause, or eliminated the wrong turn, earlier than the engineer did?

Reports abstentions SEPARATELY from misses. A single accuracy number that
hides that distinction is misleading and must not be added.
"""
from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from datetime import timedelta

from ..adapters.base import EvidenceWindow
from ..adapters.mock import MockAdapter
from ..brief import build
from ..models import Brief, Confidence

_TOKEN = re.compile(r"[A-Z]{2,}-\d+")

# Closure wording that means the case was dropped, not diagnosed. A case closed
# this way has no root cause, however its status field reads.
_ABANDONED = ("no response", "no reply", "unable to contact", "customer closed",
              "no further update", "duplicate")


def _defect_ids(text: str) -> set[str]:
    return set(_TOKEN.findall(text or ""))


def gradeable(cause: str | None, closed_state: str | None,
              closure_comments: str | None) -> tuple[bool, str]:
    """Can a brief for this case be graded at all?

    The reverse test compares a brief against what the case actually turned out
    to be. A case with no recorded root cause offers nothing to compare, so
    scoring it produces a verdict about nothing.

    Do NOT substitute `closed_state == "Resolved"` for this check. On real
    cases that field reads Resolved on cases closed only because the customer
    stopped replying. Filtering the corpus on it admits abandoned cases and the
    harness then grades briefs against ground truth that was never established.
    """
    blob = (closure_comments or "").lower()
    for marker in _ABANDONED:
        if marker in blob:
            return False, f"closed without diagnosis ({closure_comments!r})"
    if not (cause or "").strip():
        return False, "no root cause recorded"
    return True, ""


@dataclass
class Outcome:
    case_id: str
    abstained: bool
    named_cause: bool          # brief pointed at the actual root cause
    warned_of_trap: bool       # brief warned the initial hypothesis is a known trap
    n_eliminated: int
    ruled_out_cause: bool = False   # brief ELIMINATED the actual root cause
    n_asserted: int = 0             # distinct defect IDs put forward as live

    @property
    def verdict(self) -> str:
        # Eliminating the true cause is the worst outcome available: it does not
        # merely fail to help, it argues the senior away from the right answer.
        # It outranks MISS and must never be folded into it.
        if self.ruled_out_cause and not self.named_cause:
            return "RULED-OUT"
        if self.named_cause:
            return "HIT"
        if self.warned_of_trap:
            return "PARTIAL"
        if self.abstained:
            return "ABSTAIN"
        return "MISS"


def evaluate_case(adapter: MockAdapter, case_id: str,
                  at_days: int, threshold: Confidence) -> Outcome:
    raw_created = adapter._raw[case_id]["created_at"]  # fixture-only shortcut
    from datetime import datetime
    cutoff = datetime.fromisoformat(raw_created) + timedelta(days=at_days)
    window = EvidenceWindow(cutoff=cutoff)

    brief = build(case_id, adapter, window, threshold=threshold)
    hyp, cause = adapter.truth(case_id)

    cause_ids = _defect_ids(cause or "")
    # The defect universe, so "asserted" counts real defect IDs and not the
    # case numbers that modules legitimately cite as sources.
    platform = adapter.get_case(case_id, window).platform
    known_defects = {d.id for d in adapter.defects_for_platform(platform, window)}

    named = False
    warned = False
    ruled_out_true_cause = False
    asserted: set[str] = set()
    for f in brief.findings:
        text = f.statement
        ids = _defect_ids(text) | set(f.sources)
        if f.kind in ("candidate", "trap"):
            # What the brief is actually putting in front of the engineer.
            # A hit earned by naming six defects is not the same result as a
            # hit earned by naming one, and the verdict alone cannot show that.
            asserted |= ids & known_defects
        if cause_ids and (cause_ids & ids):
            # Only a finding that POINTS AT the cause counts. An "eliminated"
            # finding naming the true cause is the opposite of a hit: the brief
            # actively ruled out the right answer.
            if f.kind == "eliminated":
                ruled_out_true_cause = True
            else:
                named = True
        if f.kind == "trap" and hyp and hyp.lower()[:24] in text.lower():
            warned = True

    return Outcome(case_id=case_id, abstained=brief.abstained,
                   named_cause=named, warned_of_trap=warned,
                   n_eliminated=len(brief.of_kind("eliminated")),
                   ruled_out_cause=ruled_out_true_cause,
                   n_asserted=len(asserted))


def main() -> None:
    ap = argparse.ArgumentParser(description="Reverse test on closed cases")
    ap.add_argument("--at-days", type=int, default=2,
                    help="Evidence cutoff, days after case creation "
                         "(2 = around the point of escalation)")
    ap.add_argument("--threshold", default="medium",
                    choices=[c.value for c in Confidence])
    ap.add_argument("--show", action="store_true", help="print each brief")
    args = ap.parse_args()

    adapter = MockAdapter()
    threshold = Confidence(args.threshold)

    # Corpus selection comes before scoring. A case with no established root
    # cause cannot be graded, and including it would manufacture verdicts.
    ids: list[str] = []
    excluded: list[tuple[str, str]] = []
    for cid in adapter.eval_case_ids:
        _, cause = adapter.truth(cid)
        state, comments = adapter.closure(cid)
        ok, why = gradeable(cause, state, comments)
        (ids.append(cid) if ok else excluded.append((cid, why)))

    outcomes = [evaluate_case(adapter, cid, args.at_days, threshold) for cid in ids]

    if args.show:
        for cid in ids:
            from datetime import datetime
            cutoff = datetime.fromisoformat(
                adapter._raw[cid]["created_at"]) + timedelta(days=args.at_days)
            b: Brief = build(cid, adapter, EvidenceWindow(cutoff), threshold)
            print(b.render())
            print("-" * 68)

    n = len(outcomes)
    counts = {k: sum(1 for o in outcomes if o.verdict == k)
              for k in ("HIT", "PARTIAL", "ABSTAIN", "MISS", "RULED-OUT")}

    # A case whose recorded root cause carries no defect ID cannot be scored
    # HIT by ID matching at all. Say so, rather than letting it look like a
    # passing negative control.
    unscoreable = [cid for cid in ids if not _defect_ids(adapter.truth(cid)[1] or "")]

    print(f"REVERSE TEST  |  {n} gradeable cases  |  cutoff = creation + "
          f"{args.at_days}d  |  threshold = {threshold.value}")
    if excluded:
        print(f"              |  {len(excluded)} excluded from the corpus "
              "(not gradeable)")
    print()
    for o in outcomes:
        print(f"  {o.case_id}  {o.verdict:<10} asserted={o.n_asserted:<3} "
              f"eliminated={o.n_eliminated}")
    print()
    # Verdicts alone cannot separate a discriminating module from one that
    # lists every candidate it has ever seen. Both score HIT. This total is
    # what tells them apart, so it is printed next to the verdicts, not buried.
    hits = [o for o in outcomes if o.verdict == "HIT"]
    total_asserted = sum(o.n_asserted for o in outcomes)
    if hits:
        per_hit = sum(o.n_asserted for o in hits) / len(hits)
        print(f"  ASSERTED {total_asserted:>3}   defect IDs put forward in total "
              f"({per_hit:.1f} per HIT)")
        print("           Lower is better at equal HIT count. A brief that names "
              "every\n           candidate will score HIT and still waste the "
              "senior's shift.")
        print()
    print(f"  HIT        {counts['HIT']:>3}   named the actual root cause")
    print(f"  PARTIAL    {counts['PARTIAL']:>3}   warned the first hypothesis is a known trap")
    print(f"  ABSTAIN    {counts['ABSTAIN']:>3}   declined to guess (correct behaviour)")
    print(f"  MISS       {counts['MISS']:>3}   confident and unhelpful")
    print(f"  RULED-OUT  {counts['RULED-OUT']:>3}   ELIMINATED the actual root cause (worst case)")
    print()
    print("  Reported separately on purpose. ABSTAIN is not a failure; MISS is.")
    print("  Senior engineers grade HIT and PARTIAL - this script cannot.")
    if unscoreable:
        print()
        print(f"  NOTE: {len(unscoreable)} case(s) have a root cause with no defect ID:")
        print(f"        {', '.join(unscoreable)}")
        print("        ID matching cannot score these HIT. They test ABSTAIN only.")

    if excluded:
        print()
        print(f"  EXCLUDED FROM THE CORPUS  {len(excluded)} case(s) cannot be graded:")
        for cid, why in excluded:
            print(f"        {cid}  {why}")
        print("        These have no established root cause to compare a brief")
        print("        against. Note the status field can still read 'Resolved' -")
        print("        selecting the corpus on that field would score them anyway.")


if __name__ == "__main__":
    main()

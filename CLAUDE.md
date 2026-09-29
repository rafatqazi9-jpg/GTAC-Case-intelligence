# GTAC Case Intelligence Brief

An internal decision-support tool for **senior** TAC and network engineers at
Extreme Networks. It automates the mechanical assembly work a senior does by
hand on a hard case, and records what each piece of evidence has **ruled out**.

## Non-negotiable design rules

These are product requirements, not style preferences. Do not relax them to
make output look more impressive.

1. **Abstaining is a valid answer.** If no candidate clears the confidence
   threshold, the brief must say so explicitly and report what was eliminated.
   Never pad the output with weak candidates to fill the shape.
2. **Every claim carries a source ID.** A defect ID, KB ID, or case number.
   A claim with no retrievable ID must not appear in the brief.
3. **Elimination over suggestion.** The primary output is what the evidence
   rules out. Candidate lists are secondary.
4. **Never fabricate.** No invented defect IDs, versions, dates, or metrics.
   If an adapter returns nothing, the brief says the data was unavailable.
   Do not interpolate or "estimate" a value to make a module produce output.
5. **Internal only.** Nothing this tool produces is customer-facing. No
   module may draft customer correspondence.
6. **Read-only against systems of record.** No writes to Salesforce or Jira in
   Phase 1. The harness must be incapable of mutating a case.

## Architecture

```
src/models.py            Dataclasses: Case, Defect, KBArticle, Finding, Brief
src/adapters/base.py     Abstract data access — the ONLY seam to real systems
src/adapters/mock.py     Fixture-backed adapter. Default. No network.
src/modules/             One module per analysis. Pure functions.
src/brief.py             Runs modules, applies confidence policy, assembles
src/harness/reverse_test.py   Replays closed cases. The validation gate.
```

Modules must be **pure**: `(case, evidence, adapter) -> list[Finding]`. No I/O
beyond the adapter. No global state. This is what makes the reverse test
possible.

## Phase 1 modules (build these)

- `m01_defect_exposure` — code version x features actually enabled x defect
  list. Output: defects the customer is genuinely exposed to, plus the target
  version that clears the most of them.
- `m05_known_traps` — prior closed cases that presented this way **where the
  first hypothesis was wrong**. Highest-value module; no equivalent exists.
- `m06_fleet_correlation` — same platform, same version, same symptom, rising
  across regions. Turns a case into an early field notice.
- `m07_escalation_package` — assemble timeline, tech-data inventory with gaps
  flagged, hypothesis with evidence, and what has been ruled out. Gate it
  against required BU fields so it does not bounce.

## Phase 2 (do NOT start without explicit instruction)

`m02` config anomaly diff, `m03` timeline reconstruction, `m04` discriminating
test plan. These need attachment access plus parsers for `show tech-support`
and syslog output. Real engineering. Out of scope until Phase 1 clears its gate.

## The reverse test is the point

`src/harness/reverse_test.py` replays closed cases that were escalated or
reassigned, using **only the evidence that existed at a given point in the
case**, and asks one question: would the brief have eliminated the wrong turn
earlier than the engineer did?

Leakage is the failure mode that would make this worthless. When adding a
module or an adapter method, ask whether it can see post-cutoff data. The
`EvidenceWindow` cutoff must be enforced in the adapter, not in the module.

Report abstentions separately from misses. A tool that abstains on a hard case
is behaving correctly; a tool that confidently names the wrong cause is not.
Never report a single accuracy percentage that hides the difference.

## Working agreements

- Python 3.11+, stdlib plus `pyyaml` and `pytest`. Ask before adding a dep.
- Type hints everywhere. `mypy --strict` should pass.
- Every module needs a test using fixtures in `data/mock/`.
- Run `pytest` and `python -m src.harness.reverse_test` before saying done.
- Real connector adapters (Salesforce, Jira, Snowflake, Workato KB) are
  written against `adapters/base.py`. Never let credentials, tokens, account
  names, or real case data into the repo — fixtures are synthetic only.

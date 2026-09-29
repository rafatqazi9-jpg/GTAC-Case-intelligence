# GTAC Case Intelligence Brief

Phase 1 prototype. A decision-support brief for **senior** TAC and network
engineers: it automates the mechanical assembly work on a hard case and
records what the evidence has **ruled out**.

Read `CLAUDE.md` first — it holds the design rules that make this useful
rather than impressive.

## Quick start

```bash
python -m pytest tests/ -q
python -m src.harness.reverse_test              # the validation gate
python -m src.harness.reverse_test --show       # print each brief
python -m src.harness.reverse_test --at-days 1  # tighter evidence window
```

No credentials or network access needed. The default adapter is fixture-backed.

## What runs today

| Module | Status | Needs |
|---|---|---|
| 01 defect exposure | working (mock) | Jira defect backlog, Snowflake lifecycle |
| 05 known traps | working (mock) | Salesforce closed-case history |
| 06 fleet correlation | working (mock) | Salesforce open cases |
| 07 escalation package | working (mock) | Salesforce case detail, BU field requirements |
| 02 / 03 / 04 | not started (Phase 2) | attachment access + show-tech and syslog parsers |

## Evidence extraction (`src/extract.py`)

```bash
python -m src.server --open                 # interactive app, paste and analyze
python -m src.analyze case.txt              # pull fields out of case prose
python -m src.analyze raslog.txt --only-log # severity, repeats, time span
python -m src.analyze case.txt --log --json # both, machine-readable
```

### The interactive app is local, deliberately

`python -m src.server` serves `demo/app.html` and one endpoint,
`POST /api/analyze`, which calls the same `extract_all()` the CLI does. Paste
case text, pick a mode, get live output.

It binds **127.0.0.1 only** — never `0.0.0.0`. This page exists to have real
case text pasted into it, and real case text carries customer names, addresses,
topology and contacts. So: nothing is written to disk, the access log prints
method and path but never a body, error responses never echo the submitted
text, and there are no outbound calls. Three tests enforce those (loopback
binding, no body in logs, no text in errors).

This is also why the **published demo artifact cannot do this**. A hosted page
cannot reach your machine, and pasting case text into a hosted page would send
it off the box. The artifact shows recorded output; the local app shows live
output on text that never leaves your laptop.

Built because the structured Salesforce fields the modules consume are
routinely empty. Checked against three live cases: `Firmware` and
`Product Feature` were blank on **all three**, while the information sat in
plain sight elsewhere — the running release in the subject bracket
(`[SLX-OS 20.7.3a] ...`), defect IDs and their fix versions in the description,
device inventory in pasted `efa fabric show` output, the symptom in a quoted
error string. A live adapter therefore cannot be a field-mapper; it has to read
the prose. This is that reader.

It reports **what it could not establish** as prominently as what it found, and
`features_enabled` is always on that list — which features are *enabled* is a
property of the running config, and a feature named in a description is being
discussed, not necessarily turned on. Inferring it would hand `m01` a
fabricated input.

Every extraction carries the line of text that supports it; `Extraction`
rejects an empty evidence string in `__post_init__`, the same way `Finding`
rejects empty sources.

Scope note: this is the parsing groundwork Phase 2 (`m02`/`m03`/`m04`) needs.
It is not those modules — it does no config diffing, timeline reconstruction or
test planning, and nothing in `MODULES` calls it yet.

## The reverse test

Replays closed, escalated cases using only evidence available at a chosen
cutoff, then reports four outcomes separately:

- **HIT** — named the actual root cause
- **PARTIAL** — warned that the first hypothesis is a known trap
- **ABSTAIN** — declined to guess. Correct behaviour on a genuinely novel case.
- **MISS** — confident and unhelpful.
- **RULED-OUT** — the brief *eliminated* the actual root cause. Worse than
  MISS: it does not merely fail to help, it argues the senior away from the
  right answer. Never fold this into MISS.

Never collapse these into one accuracy figure. ABSTAIN and MISS mean opposite
things, and a tool that hides the difference cannot be trusted by a senior.

The fixture set includes two **negative controls** (`CS-62000xx`) with a novel
symptom class and no defect overlap. The tool must abstain on both.

Note their limit: both root causes are external (third-party switch, customer
NTP) and carry **no defect ID**, so ID matching cannot score them HIT under any
change. They test the ABSTAIN direction only — they are not a tripwire for
pattern-matching on noise, and the harness now prints this caveat rather than
letting them read as passing controls.

### Corpus selection comes before scoring

A case can only be graded if someone actually established its root cause. The
harness partitions the eval set with `gradeable()` before it scores anything,
and prints what it removed.

**Do not select the corpus on `Closed State == "Resolved"`.** That was checked
against a real GTAC case: the status field read `Resolved` while the closure
comment read *"No response from customer. Hence closing this case."* Nobody
diagnosed it. Filtering on the status field admits abandoned cases, and the
harness then grades briefs against ground truth that was never established —
which would quietly invalidate every number in this README.

`gradeable()` reads the closure comments, not the status field. Fixture
`CS-6300001` reproduces the shape (marked `Resolved`, closed for non-response,
no root cause) and is excluded from every count.

### ASSERTED — read this next to the verdicts

The gate also prints how many distinct defect IDs the brief put in front of the
engineer. A HIT earned by naming one defect and a HIT earned by naming six are
the same verdict and very different products, and only this number separates
them. Lower is better at equal HIT count.

It exists because verdicts alone could not tell a discriminating module from
one that lists everything it has ever seen:

| modules | HIT | PARTIAL | ABSTAIN | IDs asserted |
|---|---|---|---|---|
| all four | 7 | 1 | 2 | 18 |
| `m05` only | 7 | 1 | 2 | 16 |
| **`m01` only** | **7** | 0 | 3 | **9** |

`m05` reaches the same verdict count by quoting all three causes for the
symptom class. `m01` gets there naming roughly one defect per case, because it
uses features × version. Same score, half the noise.

### Fixtures

`data/mock/` is generated by `tools/generate_fixtures.py` (seeded, reproducible):

```bash
python tools/generate_fixtures.py
```

Each of the five main symptom classes has **three genuinely different causes**,
discriminated by `features_enabled` × `sw_version` — the evidence `m01`
consumes. Roughly a third of causes are external (bad optics, upstream
providers, customer hardware) and carry no defect ID, so the corpus does not
teach the tool that the answer is always a defect.

This matters because the first fixture set mapped symptom class to essentially
one cause, and `m05` scored 6/6 on its own by quoting a prior case — the gate
was measuring symptom-class lookup. Two tests guard against that returning:
`test_symptom_class_does_not_determine_cause` and
`test_defect_attributed_cases_run_an_affected_version`. Both fail on the old
fixtures (the second caught 17 cases attributed to a defect that does not
affect the version they run).

## Fixture data is synthetic

`data/mock/` is generated by `tools/generate_fixtures.py`, not exported. Real
case data, account names, credentials, and tokens must never enter this repo.

## Going live

Write a real adapter against `src/adapters/base.py`. The cutoff must be
enforced in the adapter's queries, not in the modules — that is what keeps
the reverse test honest. Phase 1 is read-only by design; nothing here writes
to Salesforce or Jira.

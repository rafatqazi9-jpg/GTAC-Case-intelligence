"""Evidence extraction from free text and device logs.

WHY THIS EXISTS
---------------
On real GTAC cases the structured Salesforce fields the Phase 1 modules
consume are routinely empty. Checked against three live cases: `Firmware` and
`Product Feature` were blank on all three, while the information was sitting in
plain sight elsewhere -- the running version in the subject line
(`[SLX-OS 20.7.3a] ...`), the defect IDs, device inventory and version stack in
the description, the symptom in a quoted error string.

So a live adapter cannot be a field-mapper. It has to read the prose and the
attached CLI output. This module is that reader.

DESIGN RULES IT INHERITS
------------------------
* Never fabricate. Nothing is inferred, normalised or "estimated". If a version
  string will not parse, it is reported as unparsed, not coerced.
* Every extraction carries provenance: the source it came from and the line of
  text that supports it. An extraction with no evidence line is not emitted.
* Pure functions. No I/O, no global state, so the reverse test still holds.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from .models import Confidence

# --------------------------------------------------------------------- types


@dataclass(frozen=True)
class Extraction:
    """One fact recovered from text, with the evidence that supports it."""
    kind: str          # version | platform | defect | device | error | date | gap
    value: str
    source: str        # which field or file it came from
    evidence: str      # the line of text it was read out of
    confidence: Confidence

    def __post_init__(self) -> None:
        if not self.evidence.strip():
            raise ValueError(f"{self.kind} extraction without evidence: {self.value!r}")


@dataclass(frozen=True)
class Device:
    """A row of an `efa fabric show` style inventory table."""
    ip: str
    hostname: str
    role: str
    state: str
    app_state: str


# ------------------------------------------------------------------ versions

# Extreme releases carry alphanumeric patch suffixes: 20.7.3a, 20.6.2ab.
_VERSION = re.compile(r"\b(\d{1,3})\.(\d{1,3})\.(\d{1,3})([a-z]{0,3})\b")


def version_key(text: str) -> tuple[int, int, int, str] | None:
    """Sortable key for a release string, or None if it will not parse.

    Returns None rather than guessing. A caller that cannot order two versions
    must say so instead of producing a confident wrong answer -- naming an
    upgrade target that does not clear the defect is exactly the kind of
    fabricated recommendation the design rules forbid.

    The suffix sorts as a plain string, so 20.7.1 < 20.7.1a < 20.7.1b, which is
    how Extreme patch letters actually increment.
    """
    m = _VERSION.fullmatch(text.strip())
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4))


def compare_versions(a: str, b: str) -> int | None:
    """-1 / 0 / 1, or None when either side will not parse."""
    ka, kb = version_key(a), version_key(b)
    if ka is None or kb is None:
        return None
    return -1 if ka < kb else (0 if ka == kb else 1)


def _inside_dotted_quad(line: str, start: int, end: int) -> bool:
    """True when a 3-part match is really a slice of an IP address.

    `10.0.0.27` matches the version pattern as `10.0.0`, and reporting an IP as
    a release string is exactly the kind of confident nonsense that costs a
    reader trust in everything else on the page. Checked by looking for a
    further dot-digit on either side.
    """
    if line[end:end + 1] == "." and line[end + 1:end + 2].isdigit():
        return True
    before = line[:start]
    return bool(re.search(r"\d\.$", before))


def find_versions(text: str, source: str) -> list[Extraction]:
    """Every release string in the text, with the line that carried it.

    A bare `20.7.3a` in a sentence is not proof it is the RUNNING version --
    release notes and fix versions look identical. Confidence stays LOW unless
    the line labels it, which `running_version` handles.
    """
    out: list[Extraction] = []
    seen: set[str] = set()
    for line in text.splitlines():
        for m in _VERSION.finditer(line):
            v = m.group(0)
            if v in seen or _inside_dotted_quad(line, m.start(), m.end()):
                continue
            seen.add(v)
            out.append(Extraction(kind="version", value=v, source=source,
                                  evidence=line.strip()[:200],
                                  confidence=Confidence.LOW))
    return out


# Lines that state what a box is actually running, rather than mentioning a
# release in passing. Ordered most explicit first.
_RUNNING = (
    re.compile(r"\bSLX\s*:\s*(\d[\d.]*[a-z]*)", re.I),
    re.compile(r"\b(?:SLX-?OS|NOS|EXOS|VOSS)\s+(\d[\d.]*[a-z]*)", re.I),
    re.compile(r"\b(?:firmware|running|version)\s*:?\s*(\d[\d.]*[a-z]*)", re.I),
)


def running_version(text: str, source: str) -> Extraction | None:
    """The version the device is actually on, if a line says so outright.

    Returns None when nothing states it. That is a real answer: it tells the
    caller the adapter must go looking in `show version` output instead of
    quietly picking whichever release string appeared first.
    """
    for line in text.splitlines():
        for pat in _RUNNING:
            m = pat.search(line)
            if m and version_key(m.group(1)):
                return Extraction(kind="version", value=m.group(1), source=source,
                                  evidence=line.strip()[:200],
                                  confidence=Confidence.HIGH)
    return None


# ------------------------------------------------------------------- defects

# SLXOS-77710, XIQ-41220, VDX-1234 ... but not a case number like CS-03292192.
_DEFECT = re.compile(r"\b((?!CS\b)[A-Z]{2,8})-(\d{3,7})\b")


def find_defects(text: str, source: str) -> list[Extraction]:
    out: list[Extraction] = []
    seen: set[str] = set()
    for line in text.splitlines():
        for m in _DEFECT.finditer(line):
            did = m.group(0)
            if did in seen:
                continue
            seen.add(did)
            # A line that calls it a defect/issue ID is stronger evidence than
            # a bare token that merely looks like one.
            labelled = re.search(r"(defect|issue|bug|parent)\s*id", line, re.I)
            out.append(Extraction(
                kind="defect", value=did, source=source,
                evidence=line.strip()[:200],
                confidence=Confidence.HIGH if labelled else Confidence.MEDIUM))
    return out


# ----------------------------------------------------------------- platforms

# Extreme hardware families seen on cases. Deliberately a fixed list: guessing
# a platform from an arbitrary number would invent hardware.
_PLATFORM = re.compile(
    r"\b(SLX\s?\d{4}|VDX\s?\d{4}|MLX\s?\d*|VSP\s?\d{4}|X\d{3,4}|"
    r"5[2457]\d0|7[257]\d0|8[47]\d0|9\d{3})\b", re.I)


def find_platforms(text: str, source: str) -> list[Extraction]:
    out: list[Extraction] = []
    seen: set[str] = set()
    for line in text.splitlines():
        for m in _PLATFORM.finditer(line):
            p = m.group(0).upper().replace(" ", "")
            if p in seen:
                continue
            seen.add(p)
            out.append(Extraction(kind="platform", value=p, source=source,
                                  evidence=line.strip()[:200],
                                  confidence=Confidence.MEDIUM))
    return out


# ------------------------------------------------------------ error signature

_QUOTED = re.compile(r"[\"“]([^\"”]{12,160})[\"”]")


def find_error_signatures(text: str, source: str) -> list[Extraction]:
    """Extract quoted errors and explicitly reported network symptoms.

    Unquoted symptom extraction is deliberately conservative: commands,
    recommendations, and hypothetical statements are not treated as evidence.
    Every result retains its original source line.
    """
    quoted: list[Extraction] = []
    seen_quoted: set[str] = set()

    for line in text.splitlines():
        for m in _QUOTED.finditer(line):
            sig = m.group(1).strip()
            if sig.startswith(("show ", "efa ", "sudo ")) or sig in seen_quoted:
                continue
            seen_quoted.add(sig)
            quoted.append(Extraction(
                kind="error", value=sig, source=source,
                evidence=line.strip()[:200],
                confidence=Confidence.MEDIUM,
            ))

    def norm(value: str) -> str:
        return value.rstrip(" .:;,").lower()

    # Preserve existing quoted-error deduplication behaviour.
    quoted_out: list[Extraction] = []
    for item in sorted(
        quoted, key=lambda e: (-len(norm(e.value)), len(e.value))
    ):
        if any(norm(item.value) in norm(kept.value) for kept in quoted_out):
            continue
        quoted_out.append(item)

    original_order = {item.value: i for i, item in enumerate(quoted)}
    quoted_out.sort(key=lambda e: original_order[e.value])

    symptom_patterns = (
        re.compile(r"\bpacket loss\b", re.I),
        re.compile(r"\bintermittent (?:connectivity|connection)(?: loss)?\b", re.I),
        re.compile(
            r"\b(?:interface|link|port)(?:\s+\S+){0,2}\s+"
            r"(?:is\s+)?flapp(?:ing|ed|ers)\b", re.I
        ),
        re.compile(
            r"\bBGP\b.{0,40}\b(?:neighbor|peer|session|adjacency)\b"
            r".{0,30}\b(?:resets?|resetting|flaps?|flapping|drops?|"
            r"bounces?|bounced|bouncing)\b", re.I
        ),
        re.compile(
            r"\bhigh\s+(?:CPU|memory)(?:\s+(?:utilization|usage|consumption))?\b",
            re.I
        ),
        re.compile(r"\btraffic\s+(?:drops?|dropping|loss)\b", re.I),
        re.compile(r"\bqueue\s+discards?\b", re.I),
    )

    # These phrases indicate a command, investigation request, or hypothetical,
    # rather than a statement that the symptom was observed.
    excluded_context = re.compile(
        r"\b(?:check(?:ing)? for|look(?:ing)? for|investigat(?:e|ing)|"
        r"whether|if|could|might|may|possible|potential|recommend(?:ed|s)?|"
        r"should|would|rule out|test for)\b", re.I
    )
    command_line = re.compile(
        r"^\s*(?:[$>#]\s*)?(?:show|sh|ping|traceroute|trace|grep|egrep|"
        r"awk|cat|sudo|efa|debug|monitor)\b", re.I
    )

    symptoms: list[Extraction] = []
    seen_symptoms: set[str] = set()

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or command_line.search(stripped):
            continue

        # Evaluate sentences independently. A hypothetical in one sentence
        # must not suppress an observed symptom in another sentence.
        sentences = re.split(r"(?<=[.!?])\s+", stripped)
        for sentence in sentences:
            if excluded_context.search(sentence):
                continue

            for pattern in symptom_patterns:
                for match in pattern.finditer(sentence):
                    value = match.group(0).strip().rstrip(" .,:;")
                    key = value.casefold()
                    if key in seen_symptoms:
                        continue
                    seen_symptoms.add(key)
                    symptoms.append(Extraction(
                        kind="error", value=value, source=source,
                        evidence=stripped[:200],
                        confidence=Confidence.MEDIUM,
                    ))

    return quoted_out + symptoms


# ------------------------------------------------------------ device tables

def parse_device_table(text: str) -> list[Device]:
    """Rows of a pipe-delimited fabric inventory (`efa fabric show`).

    Header order is read from the header row rather than assumed, so a table
    with extra or reordered columns still parses instead of silently mapping
    the wrong field.
    """
    rows: list[Device] = []
    cols: dict[str, int] = {}
    for line in text.splitlines():
        if line.count("|") < 4:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        upper = [c.upper() for c in cells]
        if "IP ADDRESS" in upper:
            cols = {name: i for i, name in enumerate(upper)}
            continue
        if not cols or set(line.strip()) <= set("|-+ "):
            continue

        def cell(name: str) -> str:
            i = cols.get(name, -1)
            return cells[i] if 0 <= i < len(cells) else ""

        ip = cell("IP ADDRESS")
        if not re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", ip):
            continue
        rows.append(Device(ip=ip, hostname=cell("HOST NAME"), role=cell("ROLE"),
                           state=cell("DEVICE STATE"), app_state=cell("APP STATE")))
    return rows


# -------------------------------------------------------------- evidence gaps

# Phrases that mean an artefact a senior would want does not exist. Each is a
# gap the escalation package should declare rather than discover later.
_GAP_PATTERNS = (
    (re.compile(r"did not keep.{0,40}log", re.I), "console/session logs were not retained"),
    (re.compile(r"\bno\b.{0,20}\blogs?\b.{0,30}(available|kept|collected)", re.I),
     "logs not collected"),
    (re.compile(r"support\s*save.{0,40}(not happening|failed|unable)", re.I),
     "supportsave did not complete"),
    (re.compile(r"not reachable.{0,60}retry", re.I), "device unreachable for log collection"),
    (re.compile(r"Execute CLI\[failed\]", re.I), "CLI collection returned failed"),
    (re.compile(r"\bunable to (collect|gather|retrieve)\b", re.I), "collection attempt failed"),
)


def _paragraphs(text: str) -> list[str]:
    """Blank-line-separated blocks with soft wrapping undone.

    Case descriptions are pasted email and console text, so a single sentence
    routinely spans two physical lines. Matching line by line would miss
    "...but it was not\\nhappening." -- which is exactly the sentence that says
    an artefact is missing.
    """
    blocks: list[str] = []
    for block in re.split(r"\n\s*\n", text):
        joined = " ".join(ln.strip() for ln in block.splitlines() if ln.strip())
        if joined:
            blocks.append(joined)
    return blocks


def find_evidence_gaps(text: str, source: str) -> list[Extraction]:
    """Places where the evidence a senior needs is stated to be missing.

    This is the highest-value output of the extractor: knowing an artefact was
    never captured changes what you ask the customer for next, and it is the
    difference between an escalation that lands and one that bounces.
    """
    out: list[Extraction] = []
    seen: set[str] = set()
    for para in _paragraphs(text):
        for pat, label in _GAP_PATTERNS:
            m = pat.search(para)
            if not m or label in seen:
                continue
            seen.add(label)
            # Quote around the match rather than the whole paragraph, so the
            # evidence line points at the sentence that supports the claim.
            lo = max(0, m.start() - 60)
            out.append(Extraction(kind="gap", value=label, source=source,
                                  evidence=para[lo:m.end() + 60].strip(),
                                  confidence=Confidence.HIGH))
    return out


# --------------------------------------------------------------- log analysis

_SEVERITY = re.compile(
    r"\b(EMERG|ALERT|CRITICAL|CRIT|ERROR|ERR|WARNING|WARN|NOTICE|INFO|DEBUG)\b")
_TIMESTAMP = re.compile(
    r"(\d{4}[/-]\d{2}[/-]\d{2}[ T-]\d{2}:\d{2}:\d{2})"
    r"|([A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})")
# RASLOG style: [SEC-3020], [NSM-1004]
_RASLOG = re.compile(r"\[([A-Z]{2,6})-(\d{3,5})\]")


@dataclass(frozen=True)
class LogSummary:
    """What a log file says at a glance, before anyone reads 40k lines."""
    lines: int
    severities: dict[str, int]
    first_timestamp: str | None
    last_timestamp: str | None
    top_messages: list[tuple[str, int]]
    raslog_codes: list[tuple[str, int]]

    @property
    def worst(self) -> str | None:
        for level in ("EMERG", "ALERT", "CRITICAL", "CRIT", "ERROR", "ERR",
                      "WARNING", "WARN"):
            if self.severities.get(level):
                return level
        return None


def _normalise(line: str) -> str:
    """Collapse the parts of a log line that vary per occurrence, so repeats of
    the same event cluster together instead of counting as distinct."""
    s = _TIMESTAMP.sub("<ts>", line)
    s = re.sub(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", "<ip>", s)
    s = re.sub(r"\b0x[0-9a-fA-F]+\b", "<hex>", s)
    s = re.sub(r"\b\d{3,}\b", "<n>", s)
    return " ".join(s.split())[:160]


def summarise_log(text: str, *, top: int = 8) -> LogSummary:
    """Severity histogram, time span, and the messages that repeat most.

    Repetition is the signal that matters in a device log: one WARNING is
    noise, the same WARNING four thousand times is the case.
    """
    lines = text.splitlines()
    sev: Counter[str] = Counter()
    msgs: Counter[str] = Counter()
    codes: Counter[str] = Counter()
    stamps: list[str] = []

    for line in lines:
        if not line.strip():
            continue
        m = _SEVERITY.search(line)
        if m:
            sev[m.group(1).upper()] += 1
        t = _TIMESTAMP.search(line)
        if t:
            stamps.append(t.group(0))
        for r in _RASLOG.finditer(line):
            codes[r.group(0)] += 1
        msgs[_normalise(line)] += 1

    return LogSummary(
        lines=len([x for x in lines if x.strip()]),
        severities=dict(sev),
        first_timestamp=stamps[0] if stamps else None,
        last_timestamp=stamps[-1] if stamps else None,
        top_messages=[(m, c) for m, c in msgs.most_common(top) if c > 1],
        raslog_codes=codes.most_common(top),
    )


# ------------------------------------------------------------------ roll-up

@dataclass(frozen=True)
class CaseEvidence:
    """Everything the extractor could recover, with nothing filled in."""
    running_version: Extraction | None
    versions: list[Extraction]
    defects: list[Extraction]
    platforms: list[Extraction]
    errors: list[Extraction]
    gaps: list[Extraction]
    devices: list[Device]

    def missing(self) -> list[str]:
        """Module inputs still unavailable after extraction. Named plainly so a
        caller can see what a live adapter would still have to go and fetch."""
        out = []
        if self.running_version is None:
            out.append("sw_version (no line states the running release)")
        if not self.platforms:
            out.append("platform")
        if not self.errors:
            out.append("symptom signature")
        # Always missing, by nature. Which features are ENABLED is a property
        # of the running config, not of case prose: a feature named in a
        # description is being discussed, not necessarily turned on. Inferring
        # it from a mention would hand m01 a fabricated input, so this stays on
        # the list until a config parser exists to answer it properly.
        out.append("features_enabled (needs running-config; not recoverable "
                   "from case prose)")
        return out


def extract_all(text: str, source: str) -> CaseEvidence:
    """Read every supported signal out of one blob of case text or log output."""
    return CaseEvidence(
        running_version=running_version(text, source),
        versions=find_versions(text, source),
        defects=find_defects(text, source),
        platforms=find_platforms(text, source),
        errors=find_error_signatures(text, source),
        gaps=find_evidence_gaps(text, source),
        devices=parse_device_table(text),
    )

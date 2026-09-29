"""Regenerate data/mock/ - synthetic fixtures only, deterministic under SEED.

WHY THIS EXISTS
---------------
The first fixture set mapped `symptom_class` to essentially one root cause
(5 of 7 classes had exactly one distinct cause). That made the reverse test
measure symptom-class lookup rather than diagnosis: `m05` could quote a prior
case's cause string, be guaranteed to include the right defect ID, and score
6/6 on its own with `m01`, `m06` and `m07` contributing nothing.

The fix is a corpus where **symptom class does not determine cause**. Each of
the five main symptom classes has three genuinely different causes, and the
thing that discriminates between them is `features_enabled` x `sw_version` --
which is exactly the evidence `m01` consumes. A module that ignores the
device's actual configuration can no longer win.

DESIGN RULES ENCODED HERE
-------------------------
* Every defect-caused case runs a version the defect actually affects. (The
  old set had a PoE case on 32.5.1 attributed to a defect affecting only
  32.3.4/32.4.1 -- internally inconsistent, and it still scored HIT.)
* Roughly a third of causes are EXTERNAL and carry no defect ID: bad optics,
  upstream providers, customer hardware. A corpus of pure product defects
  teaches the tool that the answer is always a defect.
* Negative-control symptom classes appear ONLY as the held-out eval cases, so
  they have no prior history and the tool must abstain.
* Noise features are drawn from a pool that matches no defect tag, so they add
  realism without silently creating candidates.

Everything is generated. No real account names, case data, or credentials.

    python tools/generate_fixtures.py
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, NamedTuple

SEED = 20260902
OUT = Path(__file__).resolve().parents[1] / "data" / "mock"

PLATFORMS = ("5520", "5720", "7520")
REGIONS = ("AMER", "EMEA", "APAC")
# Deliberately match no defect feature_tag: realism without phantom candidates.
NOISE_FEATURES = ("snmp", "dhcp-snooping", "radius", "syslog", "sntp-client")

DEFECTS: list[dict[str, Any]] = [
    {"id": "XIQ-41220", "title": "MLAG peer link flap under high MAC churn",
     "affected_versions": ["32.4.1", "32.4.2", "32.5.1"], "fixed_in": "32.5.2",
     "feature_tags": ["mlag", "mac-learning"], "status": "Fixed"},
    {"id": "XIQ-40877", "title": "STP root change on port bounce with loop protect",
     "affected_versions": ["32.4.1", "32.4.2"], "fixed_in": "32.5.1",
     "feature_tags": ["stp", "loop-protect"], "status": "Fixed"},
    {"id": "XIQ-43011", "title": "ECMP hash imbalance after LAG member add",
     "affected_versions": ["32.5.1", "32.5.2"], "fixed_in": None,
     "feature_tags": ["ecmp", "lag"], "status": "Open"},
    {"id": "XIQ-42540", "title": "Memory leak in policy manager with dynamic ACLs",
     "affected_versions": ["32.4.2", "32.5.1"], "fixed_in": "32.5.2",
     "feature_tags": ["acl", "policy"], "status": "Fixed"},
    {"id": "XIQ-43788", "title": "OSPF adjacency drop on MTU mismatch in fabric",
     "affected_versions": ["32.5.1"], "fixed_in": None,
     "feature_tags": ["ospf", "mtu"], "status": "Open"},
    {"id": "XIQ-39905", "title": "PoE budget miscalculation on partial PSU failure",
     "affected_versions": ["32.3.4", "32.4.1"], "fixed_in": "32.4.2",
     "feature_tags": ["poe"], "status": "Fixed"},
    # fixed_in 32.5.10 sorts BELOW 32.5.2 under string compare. Keeps the
    # numeric version ordering in m01 covered by real fixture data.
    {"id": "XIQ-44102", "title": "VXLAN tunnel blackhole after VLAN prune",
     "affected_versions": ["32.5.1", "32.5.2"], "fixed_in": "32.5.10",
     "feature_tags": ["vxlan", "vlan"], "status": "Fixed"},
    {"id": "XIQ-44510", "title": "Control-plane starvation under sFlow sampling",
     "affected_versions": ["32.5.1"], "fixed_in": "32.5.2",
     "feature_tags": ["sflow", "telemetry"], "status": "Fixed"},
    {"id": "XIQ-44890", "title": "PoE negotiation failure with LLDP-MED endpoints",
     "affected_versions": ["32.4.2", "32.5.1"], "fixed_in": "32.5.2",
     "feature_tags": ["poe", "lldp"], "status": "Fixed"},
    {"id": "XIQ-45021", "title": "BPDU flood under MAC table churn",
     "affected_versions": ["32.5.1", "32.5.2"], "fixed_in": None,
     "feature_tags": ["stp", "mac-learning"], "status": "Open"},
]


class Cause(NamedTuple):
    """One real way a symptom class comes about.

    `features` and `versions` are what make it distinguishable from its
    siblings -- they are the discriminating evidence, not decoration.
    """
    label: str
    defect_id: str | None
    features: tuple[str, ...]
    versions: tuple[str, ...]
    hypothesis: str          # the plausible-but-wrong first guess


# Every symptom class has three causes that a senior would genuinely have to
# tell apart. Same presentation, different underlying fault.
CAUSES: dict[str, tuple[str, list[Cause]]] = {
    "intermittent_uplink_loss": ("Intermittent uplink loss", [
        Cause("MLAG peer link flap under MAC churn (XIQ-41220)", "XIQ-41220",
              ("mlag", "mac-learning"), ("32.4.1", "32.4.2", "32.5.1"),
              "Suspected bad optic / cable"),
        Cause("MTU mismatch causing OSPF adjacency drop (XIQ-43788)", "XIQ-43788",
              ("ospf", "mtu"), ("32.5.1",),
              "Suspected bad optic / cable"),
        Cause("Failed SFP+ optic on the customer patch panel", None,
              ("lldp",), ("32.4.2", "32.5.1", "32.5.2"),
              "Suspected software defect in the link layer"),
    ]),
    "traffic_blackhole": ("Traffic blackholed on a subset of flows", [
        Cause("ECMP hash imbalance after LAG member add (XIQ-43011)", "XIQ-43011",
              ("ecmp", "lag"), ("32.5.1", "32.5.2"),
              "Suspected ACL misconfiguration"),
        Cause("VXLAN tunnel blackhole after VLAN prune (XIQ-44102)", "XIQ-44102",
              ("vxlan", "vlan"), ("32.5.1", "32.5.2"),
              "Suspected ACL misconfiguration"),
        Cause("Upstream provider withdrew the BGP prefix", None,
              ("bgp",), ("32.4.2", "32.5.1", "32.5.2"),
              "Suspected forwarding table corruption"),
    ]),
    "device_unresponsive": ("Device stops responding to management", [
        Cause("Policy manager memory leak with dynamic ACLs (XIQ-42540)", "XIQ-42540",
              ("acl", "policy"), ("32.4.2", "32.5.1"),
              "Suspected hardware failure"),
        Cause("Control-plane starvation under sFlow sampling (XIQ-44510)", "XIQ-44510",
              ("sflow", "telemetry"), ("32.5.1",),
              "Suspected hardware failure"),
        Cause("Thermal shutdown from a blocked air intake", None,
              ("poe",), ("32.4.1", "32.4.2", "32.5.2"),
              "Suspected memory leak in the platform image"),
    ]),
    "poe_devices_dropping": ("PoE endpoints dropping and re-powering", [
        Cause("Partial PSU failure miscounting budget (XIQ-39905)", "XIQ-39905",
              ("poe",), ("32.3.4", "32.4.1"),
              "Suspected PoE budget exceeded"),
        Cause("PoE negotiation failure with LLDP-MED endpoints (XIQ-44890)", "XIQ-44890",
              ("poe", "lldp"), ("32.4.2", "32.5.1"),
              "Suspected PoE budget exceeded"),
        Cause("Third-party camera firmware renegotiating power on a loop", None,
              ("poe",), ("32.5.2",),
              "Suspected PoE controller defect"),
    ]),
    "stp_topology_churn": ("Spanning-tree topology changes recurring", [
        Cause("STP root change on port bounce with loop protect (XIQ-40877)", "XIQ-40877",
              ("stp", "loop-protect"), ("32.4.1", "32.4.2"),
              "Suspected duplicate root bridge priority"),
        Cause("BPDU flood under MAC table churn (XIQ-45021)", "XIQ-45021",
              ("stp", "mac-learning"), ("32.5.1", "32.5.2"),
              "Suspected duplicate root bridge priority"),
        Cause("Customer attached an unmanaged switch creating a loop", None,
              ("stp",), ("32.4.2", "32.5.1"),
              "Suspected BPDU processing defect"),
    ]),
}

# Held out as negative controls. No prior history is generated for these
# classes, so the tool sees a genuinely novel symptom and must abstain.
NOVEL: dict[str, tuple[str, Cause]] = {
    "multicast_replication_loss": ("Multicast replication loss downstream", Cause(
        "Third-party IGMP snooping bug on a downstream vendor switch", None,
        ("pim", "igmp"), ("32.5.1",), "Suspected platform software defect")),
    "clock_drift_alarms": ("Clock drift alarms across the fabric", Cause(
        "Customer NTP server pool misconfigured upstream of the fabric", None,
        ("ntp",), ("32.5.1",), "Suspected platform software defect")),
}


def _events(created: datetime, hypothesis: str, cause: str,
            *, closed: bool) -> list[dict[str, str]]:
    """Timeline shared by every case.

    The root-cause note lands on day 4 so a day-2 cutoff cannot see it; that is
    what the leakage tests assert against. Only `show tech-support` arrives
    before the cutoff, so m07 always has real gaps to flag.
    """
    ev = [
        {"at": created.isoformat(), "kind": "note",
         "text": f"Customer reports {cause.split('(')[0].strip().lower()[:40]}."},
        {"at": (created + timedelta(hours=3)).isoformat(), "kind": "note",
         "text": f"Initial assessment: {hypothesis}."},
        {"at": (created + timedelta(days=1)).isoformat(), "kind": "attachment",
         "text": "show tech-support uploaded"},
        {"at": (created + timedelta(days=2, hours=6)).isoformat(), "kind": "escalation",
         "text": "Escalated: initial line of investigation exhausted."},
    ]
    if closed:
        ev.append({"at": (created + timedelta(days=4)).isoformat(), "kind": "note",
                   "text": f"Root cause confirmed: {cause}."})
    return ev


def _make_case(rng: random.Random, cid: str, symptom: str, text: str,
               cause: Cause, created: datetime, *, platform: str,
               version: str, region: str, closed: bool,
               abandoned: bool = False) -> dict[str, Any]:
    """`abandoned` models the real Salesforce shape seen on live cases: the
    case is closed and the status field reads Resolved, but nobody ever
    diagnosed it -- it was closed because the customer stopped replying. Such a
    case has NO root cause to grade a brief against, and the harness must
    refuse to score it rather than trusting the status field."""
    feats = list(cause.features)
    for extra in rng.sample(NOISE_FEATURES, rng.randint(0, 2)):
        feats.append(extra)
    if abandoned:
        closure = "No response from customer. Hence closing this case."
    elif closed:
        closure = f"Root cause confirmed: {cause.label}. Fix identified."
    else:
        closure = None
    return {
        "id": cid,
        "account": f"Account-{rng.randint(1, 40):02d}",
        "platform": platform,
        "sw_version": version,
        "symptom_class": symptom,
        "symptom_text": text,
        "features_enabled": feats,
        "region": region,
        # Severity, as TAC records it. Not read by any module -- carried so the
        # demo can show a case header in the shape a GTAC engineer recognises.
        "case_type": rng.choice(("C2", "C3", "C3", "C4")),
        "created_at": created.isoformat(),
        "events": _events(created, cause.hypothesis, cause.label,
                          closed=closed and not abandoned),
        "initial_hypothesis": cause.hypothesis,
        # An abandoned case never had its cause established.
        "actual_root_cause": None if abandoned else cause.label,
        "closed_at": (created + timedelta(days=5)).isoformat() if closed else None,
        # Deliberately "Resolved" even when abandoned: that mismatch is exactly
        # what makes filtering the corpus on this field unsafe.
        "closed_state": "Resolved" if closed else None,
        "closure_comments": closure,
        "escalated": True,
    }


def build() -> tuple[list[dict[str, Any]], list[str]]:
    rng = random.Random(SEED)
    cases: list[dict[str, Any]] = []
    seq = 1

    # ---- history: closed cases, all three causes represented per class ------
    # Uneven counts on purpose. If every cause were equally common a module
    # could not learn anything from frequency, and a real backlog is not flat.
    history_start = datetime(2026, 1, 5, 14, 0, 0)
    weights = (4, 3, 2)
    for symptom, (text, causes) in CAUSES.items():
        for cause, n in zip(causes, weights):
            for _ in range(n):
                created = history_start + timedelta(days=rng.randint(0, 110),
                                                    hours=rng.randint(0, 8))
                cases.append(_make_case(
                    rng, f"CS-61{seq:05d}", symptom, text, cause, created,
                    platform=rng.choice(PLATFORMS),
                    version=rng.choice(cause.versions),
                    region=rng.choice(REGIONS), closed=True))
                seq += 1

    # ---- eval set: held out, one per (class, cause) of interest ------------
    # Two causes from the same class appear back to back. A module that answers
    # from symptom class alone must get at least one of each pair wrong.
    eval_at = datetime(2026, 6, 8, 9, 0, 0)
    eval_ids: list[str] = []
    plan = [
        ("intermittent_uplink_loss", 0, "5720", "32.5.1", "AMER"),
        ("intermittent_uplink_loss", 1, "7520", "32.5.1", "EMEA"),
        ("intermittent_uplink_loss", 2, "5520", "32.5.2", "APAC"),  # external cause
        ("traffic_blackhole", 0, "5520", "32.5.1", "AMER"),
        ("traffic_blackhole", 1, "5520", "32.5.1", "EMEA"),
        ("device_unresponsive", 1, "7520", "32.5.1", "AMER"),
        ("poe_devices_dropping", 0, "7520", "32.4.1", "EMEA"),
        ("stp_topology_churn", 1, "5720", "32.5.1", "APAC"),
    ]
    for i, (symptom, idx, platform, version, region) in enumerate(plan):
        text, causes = CAUSES[symptom]
        # Numbered clear of the history block (which ends at 45) so an eval case
        # cannot collide with, and silently replace, a prior case.
        cid = f"CS-61{50 + i:05d}"
        cases.append(_make_case(
            rng, cid, symptom, text, causes[idx],
            eval_at + timedelta(days=i), platform=platform, version=version,
            region=region, closed=True))
        eval_ids.append(cid)

    # ---- fleet cluster: open peers so m06 has something real to correlate --
    # Same platform / version / symptom as the first traffic_blackhole eval
    # case, spread across regions, still open at the cutoff.
    cluster_cause = CAUSES["traffic_blackhole"][1][0]
    for j, region in enumerate(REGIONS):
        cases.append(_make_case(
            rng, f"CS-61{90 + j:05d}", "traffic_blackhole",
            CAUSES["traffic_blackhole"][0], cluster_cause,
            eval_at + timedelta(days=3, hours=j + 1), platform="5520",
            version="32.5.1", region=region, closed=False))

    # ---- abandoned case: closed "Resolved", never actually diagnosed ------
    # Modelled on a real GTAC case whose Closed State read Resolved while the
    # closure comment said the customer stopped responding. It carries no root
    # cause, so the harness must exclude it from scoring instead of grading a
    # brief against ground truth that does not exist.
    cases.append(_make_case(
        rng, "CS-6300001", "traffic_blackhole", CAUSES["traffic_blackhole"][0],
        CAUSES["traffic_blackhole"][1][2], eval_at + timedelta(days=8),
        platform="7520", version="32.5.1", region="EMEA",
        closed=True, abandoned=True))
    eval_ids.append("CS-6300001")

    # ---- negative controls: novel class, no prior history -----------------
    for k, (symptom, (text, cause)) in enumerate(NOVEL.items()):
        cid = f"CS-62{k + 1:05d}"
        cases.append(_make_case(
            rng, cid, symptom, text, cause, eval_at + timedelta(days=10 + k),
            platform="5520", version="32.5.1", region="AMER", closed=True))
        eval_ids.append(cid)

    return cases, eval_ids


def main() -> None:
    cases, eval_ids = build()
    ids = [c["id"] for c in cases]
    if len(ids) != len(set(ids)):
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        raise SystemExit(f"duplicate case ids would be silently deduped: {dupes}")

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "defects.json").write_text(json.dumps(DEFECTS, indent=1) + "\n")
    (OUT / "cases.json").write_text(json.dumps(cases, indent=1) + "\n")
    (OUT / "meta.json").write_text(json.dumps({
        "eval_case_ids": eval_ids,
        "required_escalation_fields": {
            "_default": ["show tech-support", "topology diagram", "timeline",
                         "sw_version", "reproduction steps"],
        },
    }, indent=1) + "\n")

    per_class: dict[str, set[str]] = {}
    for c in cases:
        if c.get("actual_root_cause"):
            per_class.setdefault(c["symptom_class"], set()).add(c["actual_root_cause"])
    print(f"wrote {len(cases)} cases, {len(DEFECTS)} defects, "
          f"{len(eval_ids)} eval ids -> {OUT}")
    for k, v in sorted(per_class.items()):
        print(f"  {k:<28} distinct_causes={len(v)}")


if __name__ == "__main__":
    main()

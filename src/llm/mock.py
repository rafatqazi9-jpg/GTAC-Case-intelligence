"""Mock LLM provider for deterministic testing.

No network, no API keys, no non-determinism. Returns canned responses
based on keyword matching against the last user message.

This is the DEFAULT provider. Tests and local development use it
automatically unless an API key is configured.
"""
from __future__ import annotations

import re
from typing import Any

from .base import LLMProvider, LLMResponse, Message


# ---------------------------------------------------------------------------
# Canned response templates — enough to exercise the conversation engine
# without network access. These are intentionally simple and technically
# accurate for the domain.
# ---------------------------------------------------------------------------

_DEFAULT_RESPONSE = (
    "I don't have enough information to answer that question confidently. "
    "Could you provide more details about the platform, software version, "
    "or specific symptoms you're investigating?"
)

_RESPONSES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bbgp\b.*\b(neighbor|peer|session)\b.*\b(down|reset|flap)", re.I),
     "BGP neighbor resets can be caused by hold-timer expiry, TCP session "
     "failure, route-policy rejection, or a control-plane restart on the peer. "
     "Key diagnostic steps:\n"
     "1. Check `show ip bgp summary` for the neighbor state and uptime.\n"
     "2. Review `show log raslog` for BGP-related messages around the failure time.\n"
     "3. Compare the configured hold-time with the observed gap between keepalives.\n"
     "4. If the peer reset, collect `show ip bgp neighbors <addr>` for the last reset reason.\n\n"
     "Without the specific log output and timestamps, I cannot determine which "
     "cause applies to this case."),

    (re.compile(r"\bospf\b.*\b(adjacen|neighbor|flap)", re.I),
     "OSPF adjacency issues typically stem from MTU mismatch, area configuration "
     "errors, authentication failures, or interface-level problems. Check:\n"
     "1. `show ip ospf neighbor` for stuck states (INIT, EXSTART).\n"
     "2. `show ip ospf interface` for MTU and area assignments.\n"
     "3. Log entries for OSPF hello/dead timer mismatches.\n\n"
     "An adjacency stuck in EXSTART/EXCHANGE usually indicates an MTU mismatch "
     "on the link."),

    (re.compile(r"\bstp\b.*\b(loop|topology|churn|flap)", re.I),
     "STP topology changes can indicate a loop or unstable link. Investigate:\n"
     "1. `show spanning-tree` for port states and topology change counters.\n"
     "2. `show spanning-tree detail` for the last topology change timestamp and port.\n"
     "3. Verify BPDU guard and root guard configuration on edge ports.\n"
     "4. Check for unidirectional link failures that STP cannot detect.\n\n"
     "Frequent topology changes from the same port often point to a flapping "
     "link or a misconfigured edge port."),

    (re.compile(r"\bpacket\s*loss\b|\btraffic\s*(drop|black\s*hole)", re.I),
     "Packet loss requires isolating the forwarding path. Steps:\n"
     "1. Verify interface error counters (`show interface`) for CRC, input errors, drops.\n"
     "2. Check queue discard counters for congestion-related drops.\n"
     "3. Validate the routing table has the expected next-hop (`show ip route`).\n"
     "4. If ECMP is in use, check per-path load balancing and hash polarization.\n"
     "5. Review ACL and policy-based filtering that could silently drop traffic.\n\n"
     "I need interface counters and routing table output to narrow the cause."),

    (re.compile(r"\bhigh\s*cpu\b|\bcpu\s*(spike|util)", re.I),
     "High CPU on an SLX/VDX platform can be caused by:\n"
     "- Control-plane protocol storms (ARP, BGP updates, OSPF SPF recalculations).\n"
     "- Software memory leak or memory pressure triggering garbage collection.\n"
     "- Known defect in the running version.\n\n"
     "Collect:\n"
     "1. `show process cpu` to identify the top process.\n"
     "2. `show system memory` for memory utilization.\n"
     "3. `show log raslog` around the spike timestamps.\n"
     "4. The running software version for defect cross-reference."),

    (re.compile(r"\bmlag\b|\bmc-?lag\b|\bmct\b|\bisl\b", re.I),
     "MCT/MC-LAG issues can affect both control-plane synchronization and "
     "data-plane forwarding. Check:\n"
     "1. `show mct cluster` for cluster state and ISL status.\n"
     "2. `show mct client` for client interface states.\n"
     "3. `show mct client detail` for MAC synchronization counters.\n"
     "4. ISL link status and error counters on both peers.\n\n"
     "If the ISL is down, the cluster splits and both peers become active, "
     "causing potential duplicate traffic or a loop."),

    (re.compile(r"\b(evpn|vxlan)\b", re.I),
     "EVPN/VXLAN troubleshooting should focus on the overlay control plane "
     "and underlay reachability:\n"
     "1. `show evpn` for EVPN instance state.\n"
     "2. `show bgp l2vpn evpn summary` for EVPN neighbor status.\n"
     "3. `show tunnel brief` for VXLAN tunnel endpoints.\n"
     "4. Verify underlay connectivity between VTEPs with ping.\n"
     "5. Check for MAC mobility and duplicate MAC detection events.\n\n"
     "Underlay reachability problems are the most common cause of VXLAN "
     "forwarding failures."),

    (re.compile(r"\bupgrade\b|\bregression\b|\bdowngrade\b", re.I),
     "Software upgrade regressions should be investigated by:\n"
     "1. Comparing the running version against known defect records.\n"
     "2. Reviewing release notes for the old and new versions.\n"
     "3. Checking if the symptom matches a known defect fixed in a later release.\n"
     "4. Verifying configuration compatibility between versions.\n\n"
     "I need the specific old version, new version, and symptom description "
     "to cross-reference against known defects."),

    (re.compile(r"\bwhat\s+(is|are)\b.*\b(finding|brief|investigation)", re.I),
     "The investigation brief is a structured summary of what the evidence "
     "supports and what it rules out. It contains:\n"
     "- **Candidates**: Defects or conditions the case may be exposed to.\n"
     "- **Eliminated**: Possibilities ruled out by evidence.\n"
     "- **Known traps**: Historical cases where the initial hypothesis was wrong.\n"
     "- **Fleet signal**: Similar cases across the fleet.\n"
     "- **Gaps**: Missing data that would be needed for escalation.\n\n"
     "When no candidate reaches the confidence threshold, the brief abstains "
     "rather than guessing."),
]


class MockLLMProvider(LLMProvider):
    """Deterministic mock for testing and local development.

    Matches the last user message against keyword patterns and returns
    a canned response. Records all calls for test assertions.

    Custom responses can be injected via the constructor for targeted
    test scenarios.
    """

    def __init__(self, custom_responses: dict[str, str] | None = None) -> None:
        self._custom: dict[str, str] = custom_responses or {}
        self._calls: list[list[Message]] = []

    @property
    def name(self) -> str:
        return "mock"

    @property
    def call_count(self) -> int:
        return len(self._calls)

    @property
    def last_messages(self) -> list[Message] | None:
        return self._calls[-1] if self._calls else None

    def complete(
        self,
        messages: list[Message],
        *,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        **kwargs: Any,
    ) -> LLMResponse:
        self._calls.append(list(messages))

        # Find the last user message.
        last_user = next(
            (m for m in reversed(messages) if m.role == "user"), None
        )
        if last_user is None:
            return LLMResponse(
                text=_DEFAULT_RESPONSE, provider="mock", model="mock-v1"
            )

        query = last_user.content

        # Check custom responses first (exact substring match).
        for pattern_str, response_text in self._custom.items():
            if pattern_str.lower() in query.lower():
                return LLMResponse(
                    text=response_text, provider="mock", model="mock-v1"
                )

        # Check built-in pattern bank.
        for pattern, response_text in _RESPONSES:
            if pattern.search(query):
                return LLMResponse(
                    text=response_text, provider="mock", model="mock-v1"
                )

        # Analysis if the user pasted raw RASLOG / Syslog text
        if ("SEC-3020" in query or "NSM-1004" in query or "SSMD-1005" in query or "Interface Ethernet" in query) and len(query) > 60:
            lines = [l.strip() for l in query.splitlines() if l.strip()]
            sec_failures = [l for l in lines if "SEC-3020" in l or "Login failed" in l]
            interface_drops = [l for l in lines if "NSM-1004" in l or "down" in l]
            crashes = [l for l in lines if "SSMD-1005" in l or "terminated" in l]

            findings = [
                f"Parsed {len(lines)} log lines from pasted diagnostic text:",
            ]
            if interface_drops:
                findings.append(f"• Interface Event: Found port state change -> '{interface_drops[0]}'")
            if sec_failures:
                findings.append(f"• Authentication Event: Detected recurring login failures ({len(sec_failures)} occurrences) indicating credential failure or unauthorized scan.")
            if crashes:
                findings.append(f"• Daemon Crash: Detected critical termination of process -> '{crashes[0]}'")

            findings.append("\nRecommended next diagnostic steps:")
            findings.append("1. `show interface ethernet <port>` to check link status, transceiver optical levels, and CRC error counters.")
            findings.append("2. `show logging raslog` with timestamp filtering around the event to correlate peer control-plane state.")
            findings.append("3. Collect core file if SSMD process dumped a crash trace (`supportsave`).")

            return LLMResponse(
                text="\n".join(findings),
                provider="mock",
                model="mock-v1",
            )

        # Context-aware extraction from system prompt if a case was loaded
        system_msg = next((m for m in messages if m.role == "system"), None)
        system_text = system_msg.content if system_msg else ""

        if "Case:" in system_text:
            if any(k in query.lower() for k in ("symptom", "issue", "problem", "happening", "summary")):
                return LLMResponse(
                    text="Based on the active case context, the reported symptom is:\n"
                         + "\n".join(line for line in system_text.splitlines() if "Symptom:" in line or "Case:" in line),
                    provider="mock",
                    model="mock-v1",
                )
            if any(k in query.lower() for k in ("finding", "brief", "candidate", "defect", "rule out", "eliminated")):
                return LLMResponse(
                    text="Here are the investigation findings from the evidence engine:\n\n"
                         + "\n".join(line for line in system_text.splitlines() if "sources:" in line or "[" in line),
                    provider="mock",
                    model="mock-v1",
                )
            if any(k in query.lower() for k in ("version", "release", "platform")):
                return LLMResponse(
                    text="According to the case evidence:\n"
                         + "\n".join(line for line in system_text.splitlines() if "Platform:" in line or "Software:" in line),
                    provider="mock",
                    model="mock-v1",
                )

        # Software version / release query fallback
        if "version" in query.lower() or "slxos" in query.lower() or "slx" in query.lower():
            return LLMResponse(
                text="In this GTAC environment, supported SLX-OS releases include 20.7.x, 20.6.x, and 32.x train releases (such as 32.5.2 and 32.5.10). "
                     "To determine the specific recommended target release for your platform, please provide your current running version (`show version`) "
                     "or select a fixture case above to cross-reference exposed defects.",
                provider="mock",
                model="mock-v1",
            )

        return LLMResponse(
            text=_DEFAULT_RESPONSE, provider="mock", model="mock-v1"
        )

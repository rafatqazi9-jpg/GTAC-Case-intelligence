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

    (re.compile(r"\b(os\s+is\s+not\s+ready|login\s+later|login\s+issue|unable\s+to\s+log\s*in)\b", re.I),
     "The 'OS is not ready. Please login later' error occurs when the SLX-OS management "
     "plane or login manager (SSMD/SEC daemons) has not finished its boot-up state sequence, "
     "or has entered a deadlock waiting on IPC dependencies.\n\n"
     "Key triage steps:\n"
     "1. Wait 3-5 minutes post-boot to allow daemons to signal healthy state.\n"
     "2. If persistent, connect via serial console to review console boot logs.\n"
     "3. Inspect `show logging raslog` for daemon failure or core dump assertions.\n"
     "4. Note: A power cycle may temporarily clear the deadlock but will not prevent recurrence "
     "if exposed to a known defect in the running release train."),


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

        # Context-aware extraction from system prompt if a case or evidence was loaded
        system_msg = next((m for m in messages if m.role == "system"), None)
        system_text = system_msg.content if system_msg else ""

        has_context = bool(
            "Current investigation context" in system_text
            and "No case loaded" not in system_text
        )

        if has_context:
            q_lower = query.lower()

            # Extract vitals
            case_id = ""
            m_case = re.search(r"\bCase:\s*([A-Za-z0-9_-]+)", system_text)
            if m_case:
                case_id = m_case.group(1).strip()

            platform = ""
            m_plat = re.search(r"(?:Platform:\s*([^\n|]+)|Hardware\s*/\s*Platform:\s*([^\n]+))", system_text)
            if m_plat:
                platform = (m_plat.group(1) or m_plat.group(2) or "").strip()

            sw_version = ""
            m_sw = re.search(r"(?:Software:\s*([^\n|]+)|Identified Software Release:\s*([^\n]+))", system_text)
            if m_sw:
                sw_version = (m_sw.group(1) or m_sw.group(2) or "").strip()

            symptom = ""
            m_sym = re.search(r"(?:Symptom:\s*([^\n|]+)|Observed Error Signatures:\s*(?:•|-|\*)\s*([^\n]+))", system_text)
            if m_sym:
                symptom = (m_sym.group(1) or m_sym.group(2) or "").strip()
            elif "OS is not ready" in system_text:
                symptom = '"OS is not ready. Please login later"'

            plat_str = platform or "SLX Platform"
            sw_str = sw_version or "active release train"
            sym_str = symptom or "observed control-plane anomalies"

            # 1. Recommended Verification CLI / Diagnostic Commands
            if any(k in q_lower for k in ("cli", "command", "show command", "execute on", "commands to execute")):
                return LLMResponse(
                    text=(
                        f"### Recommended Verification CLI Commands\n\n"
                        f"Execute the following diagnostic commands on {plat_str} running {sw_str} to isolate the issue:\n\n"
                        f"1. **Confirm Partition & Version State**:\n"
                        f"   `show version`\n"
                        f"   *Expected*: Confirm running release is {sw_str} across active and standby partitions.\n\n"
                        f"2. **Inspect Process CPU & System Memory**:\n"
                        f"   `show process cpu`\n"
                        f"   `show system memory`\n"
                        f"   *Expected*: Check for daemon lockups, memory exhaustion, or thread starvation.\n\n"
                        f"3. **Examine System Event Log**:\n"
                        f"   `show logging raslog`\n"
                        f"   *Expected*: Search for critical error signatures around the failure time.\n\n"
                        f"4. **Collect Diagnostic Bundle for Escalation**:\n"
                        f"   `supportsave`\n"
                        f"   *Expected*: Complete compressed diagnostic archive containing core dumps and internal tech-support."
                    ),
                    provider="mock",
                    model="mock-v1",
                )

            # 2. Root Cause Analysis / Probable Cause / Triage / Hypothesis
            if any(k in q_lower for k in ("root cause", "probable", "cause", "hypothesis", "diagnos", "triage", "why", "reason")):
                findings_lines = [l for l in system_text.splitlines() if "Candidate Defect" in l or ("*" in l and ("20." in l or "SLXOS" in l))]
                defects_note = f"\n• **Candidate Defects Cited**: {', '.join(l.replace('*', '').strip() for l in findings_lines[:3])}" if findings_lines else ""
                return LLMResponse(
                    text=(
                        f"### Root Cause Triage & Hypothesis\n\n"
                        f"Based on the active investigation evidence:\n"
                        f"• **Target Platform**: {plat_str}\n"
                        f"• **Running Software**: {sw_str}\n"
                        f"• **Primary Symptom**: {sym_str}{defects_note}\n\n"
                        f"**Primary Assessment**:\n"
                        f"The observed symptom indicates that the management and control-plane daemons are stalled or "
                        f"have not transitioned to healthy readiness. When \"{sym_str}\" appears, the operating system kernel "
                        f"blocks incoming management sessions pending IPC initialization.\n\n"
                        f"**Known Precedents & Caveats**:\n"
                        f"• A power cycle temporarily clears volatile buffer queues, but the defect will recur once the trigger condition re-accumulates.\n"
                        f"• Permanent resolution requires upgrading to a verified maintenance release containing the bug fix.\n\n"
                        f"**Recommended Action**:\n"
                        f"Run verification CLI commands (`show logging raslog`, `show process cpu`) and generate a `supportsave` bundle for Tier 3 escalation."
                    ),
                    provider="mock",
                    model="mock-v1",
                )

            # 3. Known Defect Traps / Microcode & Reload Workarounds
            if any(k in q_lower for k in ("trap", "workaround", "microcode", "reload", "avoid", "caveat", "first hypothesis")):
                return LLMResponse(
                    text=(
                        f"### Known Defect Traps & Workaround Caveats\n\n"
                        f"When investigating {plat_str} running {sw_str}:\n\n"
                        f"• **The Reload Trap (False Resolution)**: Power cycling {plat_str} temporarily unblocks logins by clearing volatile buffers, "
                        f"but does not resolve the root cause. Do NOT close the case as resolved based solely on reload recovery.\n"
                        f"• **Config Mutation Trap**: Avoid issuing configuration commits while daemons are in a degraded state, as changes may partially commit.\n"
                        f"• **ISSU Caution**: Do not attempt an in-service software upgrade while control-plane daemons are degraded. Schedule a maintenance window for a clean reboot upgrade."
                    ),
                    provider="mock",
                    model="mock-v1",
                )

            # 4. Tier 3 DE Package / Escalation Brief / Customer Email / Case Notes
            if any(k in q_lower for k in ("tier 3", "de package", "de brief", "escalat", "email", "notes", "salesforce", "customer update")):
                return LLMResponse(
                    text=(
                        f"### Tier 3 / Defect Engineering Escalation Brief\n\n"
                        f"**Case Intelligence Summary**:\n"
                        f"• **Platform**: {plat_str}\n"
                        f"• **Software Version**: {sw_str}\n"
                        f"• **Observed Signature**: {sym_str}\n\n"
                        f"**Engineering Action Plan**:\n"
                        f"1. Generate supportsave archive using `supportsave` on affected nodes.\n"
                        f"2. Cross-reference defect database for known caveats in {sw_str}.\n"
                        f"3. Advise customer on target patch release and avoid premature case closure following transient power cycles."
                    ),
                    provider="mock",
                    model="mock-v1",
                )

            # 5. Symptom / Issue / Problem / Summary
            if any(k in q_lower for k in ("symptom", "issue", "problem", "happening", "summary")):
                symptom_lines = [line for line in system_text.splitlines() if "Symptom:" in line or "Case:" in line or "Observed Error" in line or (sym_str and sym_str in line)]
                body = "\n".join(symptom_lines) if symptom_lines else f"• Symptom: {sym_str}"
                return LLMResponse(
                    text=f"Based on the active case context, the reported symptom is:\n{body}",
                    provider="mock",
                    model="mock-v1",
                )

            # 6. Findings / Brief / Candidate / Defect / Rule Out / Eliminated
            if any(k in q_lower for k in ("finding", "brief", "candidate", "defect", "rule out", "eliminated")):
                return LLMResponse(
                    text="Here are the investigation findings from the evidence engine:\n\n"
                         + "\n".join(line for line in system_text.splitlines() if "sources:" in line or "[" in line or "Identified" in line or "Evidence:" in line or "*" in line or "•" in line),
                    provider="mock",
                    model="mock-v1",
                )

            # 7. Version / Release / Platform
            if any(k in q_lower for k in ("version", "release", "platform")):
                return LLMResponse(
                    text=f"According to the case evidence:\n• Platform: {plat_str}\n• Software Version: {sw_str}",
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

        # Fallback CLI diagnostic query
        if any(k in query.lower() for k in ("verification cli", "diagnostic cli", "commands to execute", "cli commands")):
            return LLMResponse(
                text="Standard GTAC verification and diagnostic commands:\n"
                     "1. `show version` — Verify active/standby partitions, hardware platform, and uptime.\n"
                     "2. `show logging raslog` — Check system event log for crash assertions and warnings.\n"
                     "3. `show process cpu` / `show system memory` — Check for daemon CPU spikes or memory leaks.\n"
                     "4. `supportsave` — Collect full diagnostic bundle for Tier 3 escalation.",
                provider="mock",
                model="mock-v1",
            )

        return LLMResponse(
            text=_DEFAULT_RESPONSE, provider="mock", model="mock-v1"
        )

"""Live Security & Threat Intelligence lookup service.

Provides real-time vulnerability information, CVE lookups (via NIST NVD),
and security advisory search using Python standard library only.
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any

# Standard browser user agent to avoid bot-blocking by public advisory feeds
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_TIMEOUT = 5  # Bounded timeout in seconds


def extract_cve_ids(text: str) -> list[str]:
    """Extract standard CVE identifiers from input text."""
    matches = re.findall(r"\bCVE-\d{4}-\d{4,8}\b", text, re.IGNORECASE)
    # Deduplicate while preserving order
    seen = set()
    result = []
    for m in matches:
        upper = m.upper()
        if upper not in seen:
            seen.add(upper)
            result.append(upper)
    return result


def is_security_query(text: str) -> bool:
    """Check if the user inquiry is asking about security vulnerabilities or CVEs."""
    if extract_cve_ids(text):
        return True
    keywords = (
        "vulnerability", "vulnerabilities", "cve", "advisory", "security bulletin",
        "exploit", "zero-day", "0-day", "patch", "affected by", "threat",
        "php2shell", "log4j", "spring4shell", "heartbleed", "remote code execution", "rce"
    )
    lower = text.lower()
    return any(k in lower for k in keywords)


def lookup_cve_nvd(cve_id: str) -> dict[str, Any] | None:
    """Fetch live CVE details from the official NIST National Vulnerability Database (NVD) API."""
    url = f"https://services.nvd.nist.gov/rest/json/cves/2.0?cveId={cve_id}"
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            vulns = data.get("vulnerabilities", [])
            if not vulns:
                return None
            cve_obj = vulns[0].get("cve", {})
            descs = cve_obj.get("descriptions", [])
            desc_en = next((d.get("value") for d in descs if d.get("lang") == "en"), "")
            
            # Extract CVSS severity if present
            metrics = cve_obj.get("metrics", {})
            cvss_data = None
            for key in ("cvssMetricV31", "cvssMetricV30", "cvssMetricV2"):
                if key in metrics and metrics[key]:
                    cvss_data = metrics[key][0].get("cvssData", {})
                    break

            severity = cvss_data.get("baseSeverity") or cvss_data.get("severity") or "UNKNOWN"
            score = cvss_data.get("baseScore", "N/A")

            return {
                "id": cve_obj.get("id", cve_id),
                "published": cve_obj.get("published", ""),
                "last_modified": cve_obj.get("lastModified", ""),
                "description": desc_en,
                "severity": str(severity).upper(),
                "cvss_score": score,
                "source": "NIST National Vulnerability Database (NVD)",
            }
    except Exception:
        return None


def search_live_advisory(query: str, max_results: int = 3) -> list[str]:
    """Search live public security feeds and web results for breaking news/advisories."""
    data = urllib.parse.urlencode({"q": query}).encode("utf-8")
    req = urllib.request.Request("https://html.duckduckgo.com/html/", data=data, headers={"User-Agent": _USER_AGENT})
    snippets: list[str] = []
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            html = resp.read().decode("utf-8", errors="replace")
            matches = re.findall(r'<a[^>]+class="result__snippet[^>]*>(.*?)</a>', html, re.DOTALL)
            for m in matches[:max_results]:
                clean = re.sub(r"<[^>]+>", "", m).strip()
                if clean:
                    snippets.append(clean)
    except Exception:
        pass
    return snippets


def gather_security_intelligence(query: str) -> str:
    """Retrieve up-to-date threat and CVE intelligence to ground the agent in real-time context."""
    if not is_security_query(query):
        return ""

    cves = extract_cve_ids(query)
    sections: list[str] = []

    # 1. NIST NVD Lookup for specific CVEs
    for cve in cves[:2]:  # Limit to 2 CVEs to bound latency
        cve_info = lookup_cve_nvd(cve)
        if cve_info:
            sections.append(
                f"- **{cve_info['id']}** (Severity: {cve_info['severity']}, CVSS: {cve_info['cvss_score']})\n"
                f"  Published: {cve_info['published'][:10] if cve_info['published'] else 'Recent'}\n"
                f"  Summary: {cve_info['description']}\n"
                f"  Source: {cve_info['source']}"
            )

    # 2. Live advisory web search if CVE was not in NVD or if query asks about specific product impacts
    search_needed = not sections or any(p in query.lower() for p in ("netsight", "purview", "extreme", "slx", "vdx", "news this week", "php2shell"))
    if search_needed:
        # Construct targeted search query
        clean_q = re.sub(r"[^\w\s-]", " ", query).strip()
        search_snippets = search_live_advisory(clean_q, max_results=3)
        if search_snippets:
            snippet_lines = "\n".join(f"  • {s}" for s in search_snippets)
            sections.append(
                f"- **Live Security & News Feeds (Real-Time Search)**:\n{snippet_lines}"
            )

    if not sections:
        return ""

    now_str = datetime.now().strftime("%B %Y")
    header = (
        f"### Live Security & Threat Intelligence (Retrieved {now_str}):\n"
        + "\n\n".join(sections)
    )
    return header

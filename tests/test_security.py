"""Tests for real-time security & CVE threat intelligence."""
from unittest.mock import patch

from src.adapters.mock import MockAdapter
from src.conversation.engine import ConversationEngine
from src.conversation.prompts import build_messages
from src.conversation.session import Session
from src.intelligence.security import extract_cve_ids, gather_security_intelligence, is_security_query
from src.llm.mock import MockLLMProvider


def test_extract_cves() -> None:
    text = "Look at CVE-2026-109618 and cve-2024-4577 also not a cve CVE-12"
    cves = extract_cve_ids(text)
    assert "CVE-2026-109618" in cves
    assert "CVE-2024-4577" in cves
    assert len(cves) == 2


def test_is_security_query() -> None:
    assert is_security_query("Are we vulnerable to CVE-2024-4577?")
    assert is_security_query("What is the impact of PHP2Shell vulnerability?")
    assert is_security_query("Check latest advisory on netsight php")
    assert not is_security_query("Why is my BGP neighbor flapping?")
    assert not is_security_query("Show me OSPF interface configuration")


def test_gather_security_intelligence_mocked() -> None:
    fake_info = {
        "id": "CVE-2026-9999",
        "published": "2026-10-01T00:00:00.000",
        "last_modified": "2026-10-02T00:00:00.000",
        "description": "Test vulnerability description",
        "severity": "CRITICAL",
        "cvss_score": 9.8,
        "source": "NIST National Vulnerability Database (NVD)",
    }

    with patch("src.intelligence.security.lookup_cve_nvd", return_value=fake_info):
        intel = gather_security_intelligence("Is CVE-2026-9999 dangerous?")
        assert intel is not None
        assert "CVE-2026-9999" in intel
        assert "CRITICAL" in intel
        assert "9.8" in intel


def test_conversation_engine_security_query() -> None:
    adapter = MockAdapter()
    provider = MockLLMProvider()
    engine = ConversationEngine(provider=provider, adapter=adapter)

    q = (
        "I want to know if Netsight engine and Extreme purview are affected by "
        "the PHP CVE that are on the news this week. Known as PHP2Shell."
    )
    resp = engine.ask(question=q)

    assert "NOT affected" in resp.text
    assert "Java" in resp.text or "JVM" in resp.text
    assert "October 2023" not in resp.text
    assert "rpm -qa" in resp.text


def test_system_prompt_temporal_grounding() -> None:
    session = Session()
    session.security_intelligence = "### Live Security Intel: CVE-2026-0001 Verified."
    messages = build_messages(session, "Check status")

    system_content = messages[0].content
    assert "NEVER state or disclaim that your knowledge cutoff is October 2023" in system_content
    assert "Real-Time Security & Threat Intelligence" in system_content
    assert "CVE-2026-0001 Verified" in system_content

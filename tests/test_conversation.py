"""Tests for the conversation engine, session management, prompt construction,
and server chat endpoints.
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from src.adapters.mock import MockAdapter
from src.conversation.engine import ConversationEngine, ChatResponse
from src.conversation.prompts import build_messages
from src.conversation.session import Session
from src.llm.base import Message, ProviderError
from src.llm.mock import MockLLMProvider
from src.models import Brief, Confidence, Finding
from src.server import Handler, _get_engine


# ------------------------------------------------------------------ Session
class TestSession:
    def test_session_init(self) -> None:
        s = Session()
        assert len(s.id) == 12
        assert s.case_id is None
        assert s.has_case is False
        assert s.data_source == "synthetic"
        assert len(s.history) == 0

    def test_session_add_messages(self) -> None:
        s = Session()
        s.add_user_message("Hello")
        s.add_assistant_message("Hi")
        assert len(s.history) == 2
        assert s.history[0].role == "user"
        assert s.history[0].content == "Hello"
        assert s.history[1].role == "assistant"
        assert s.history[1].content == "Hi"

    def test_session_context_summary_empty(self) -> None:
        s = Session()
        assert "No case loaded" in s.context_summary()

    def test_session_context_summary_with_case(self) -> None:
        s = Session(
            case_id="CS-6100050",
            platform="5520",
            sw_version="32.5.1",
            symptom_class="intermittent_uplink_loss",
            symptom_text="Interface flapping on port 1/1/1",
        )
        summary = s.context_summary()
        assert "Case: CS-6100050" in summary
        assert "Platform: 5520" in summary
        assert "Software: 32.5.1" in summary
        assert "Symptom class: intermittent_uplink_loss" in summary

    def test_session_findings_text_with_brief(self) -> None:
        s = Session(case_id="CS-6100050")
        f = Finding(
            module="m01",
            kind="candidate",
            statement="Exposed to defect XIQ-41220",
            confidence=Confidence.HIGH,
            sources=("XIQ-41220",),
        )
        brief = Brief(
            case_id="CS-6100050",
            generated_at=datetime(2026, 4, 21),
            findings=[f],
        )
        s.brief = brief
        s.findings = [f]
        rendered = s.findings_text()
        assert "CASE INTELLIGENCE BRIEF - CS-6100050" in rendered
        assert "Exposed to defect XIQ-41220" in rendered

    def test_session_to_dict(self) -> None:
        s = Session(case_id="CS-6100050", platform="5520")
        d = s.to_dict()
        assert d["case_id"] == "CS-6100050"
        assert d["platform"] == "5520"
        assert d["message_count"] == 0
        assert d["has_brief"] is False


# ------------------------------------------------------------------ Prompts
class TestPrompts:
    def test_build_messages_no_case(self) -> None:
        s = Session()
        msgs = build_messages(s, "What is BGP?")
        assert len(msgs) == 2
        assert msgs[0].role == "system"
        assert "GTAC AI Assistant" in msgs[0].content
        assert "No case loaded" in msgs[0].content
        assert msgs[1].role == "user"
        assert msgs[1].content == "What is BGP?"

    def test_build_messages_with_case_and_history(self) -> None:
        s = Session(case_id="CS-6100050", platform="5520")
        s.add_user_message("Is this a known issue?")
        s.add_assistant_message("It might be related to defect XIQ-41220.")
        msgs = build_messages(s, "What is the recommended fix?")
        assert len(msgs) == 4
        assert msgs[0].role == "system"
        assert "Case: CS-6100050" in msgs[0].content
        assert msgs[1].role == "user"
        assert msgs[2].role == "assistant"
        assert msgs[3].role == "user"
        assert msgs[3].content == "What is the recommended fix?"


# ---------------------------------------------------- Conversation Engine
class TestConversationEngine:
    def test_engine_init(self) -> None:
        provider = MockLLMProvider()
        adapter = MockAdapter()
        engine = ConversationEngine(provider=provider, adapter=adapter)
        assert engine.provider_name == "mock"
        assert engine.session_count == 0

    def test_create_session_empty(self) -> None:
        engine = ConversationEngine(MockLLMProvider(), MockAdapter())
        session = engine.create_session()
        assert session.has_case is False
        assert engine.session_count == 1
        assert engine.get_session(session.id) is session

    def test_create_session_with_valid_case(self) -> None:
        engine = ConversationEngine(MockLLMProvider(), MockAdapter())
        session = engine.create_session(case_id="CS-6100050")
        assert session.has_case is True
        assert session.case_id == "CS-6100050"
        assert session.platform is not None
        assert session.brief is not None
        assert len(session.findings) > 0

    def test_create_session_with_invalid_case_raises(self) -> None:
        engine = ConversationEngine(MockLLMProvider(), MockAdapter())
        with pytest.raises(KeyError):
            engine.create_session(case_id="NON-EXISTENT-CASE")

    def test_ask_creates_new_session_if_none(self) -> None:
        engine = ConversationEngine(MockLLMProvider(), MockAdapter())
        res = engine.ask("BGP neighbor session is down")
        assert isinstance(res, ChatResponse)
        assert res.has_case is False
        assert "bgp" in res.text.lower()
        assert engine.session_count == 1

    def test_ask_with_case_context(self) -> None:
        engine = ConversationEngine(MockLLMProvider(), MockAdapter())
        res = engine.ask("What are the findings for this case?", case_id="CS-6100050")
        assert res.has_case is True
        assert res.session_id is not None
        session = engine.get_session(res.session_id)
        assert session is not None
        assert session.case_id == "CS-6100050"
        assert len(session.history) == 2

    def test_ask_multiturn_conversation(self) -> None:
        engine = ConversationEngine(MockLLMProvider(), MockAdapter())
        res1 = engine.ask("BGP neighbor session is down", case_id="CS-6100050")
        session_id = res1.session_id
        res2 = engine.ask("What is the next step?", session_id=session_id)
        assert res2.session_id == session_id
        session = engine.get_session(session_id)
        assert session is not None
        assert len(session.history) == 4

    def test_ask_provider_error_handling(self) -> None:
        class FailingProvider(MockLLMProvider):
            def complete(self, messages, **kwargs):
                raise ProviderError("mock", "Simulated API failure", status_code=500)

        engine = ConversationEngine(FailingProvider(), MockAdapter())
        res = engine.ask("Help me troubleshoot")
        assert res.error is not None
        assert "Simulated API failure" in res.error
        assert "error communicating" in res.text.lower()

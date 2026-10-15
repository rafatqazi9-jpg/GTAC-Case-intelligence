"""Tests for the LLM provider abstraction layer.

All tests run against MockLLMProvider — no network, no API keys,
fully deterministic. The Gemini and OpenAI providers are tested
structurally (construction, message conversion) but not against
live APIs.
"""
from __future__ import annotations

import os

import pytest

from src.llm.base import LLMProvider, LLMResponse, Message, ProviderError
from src.llm.mock import MockLLMProvider


# ------------------------------------------------------------------ Message
class TestMessage:
    def test_valid_roles(self) -> None:
        for role in ("system", "user", "assistant"):
            m = Message(role=role, content="hello")
            assert m.role == role

    def test_invalid_role_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid message role"):
            Message(role="bot", content="hello")

    def test_empty_content_raises(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            Message(role="user", content="   ")

    def test_whitespace_only_raises(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            Message(role="user", content="\n\t  ")


# ------------------------------------------------------------- LLMResponse
class TestLLMResponse:
    def test_frozen(self) -> None:
        r = LLMResponse(text="hello", provider="test", model="v1")
        with pytest.raises(AttributeError):
            r.text = "modified"  # type: ignore[misc]

    def test_default_usage(self) -> None:
        r = LLMResponse(text="hello", provider="test", model="v1")
        assert r.usage == {}


# ---------------------------------------------------------- ProviderError
class TestProviderError:
    def test_basic(self) -> None:
        e = ProviderError("test", "something failed")
        assert "test" in str(e)
        assert "something failed" in str(e)
        assert e.provider == "test"
        assert e.status_code is None
        assert e.retryable is False

    def test_retryable(self) -> None:
        e = ProviderError("test", "rate limited", status_code=429, retryable=True)
        assert e.retryable is True
        assert e.status_code == 429


# ------------------------------------------------------- MockLLMProvider
class TestMockLLMProvider:
    def test_is_llm_provider(self) -> None:
        provider = MockLLMProvider()
        assert isinstance(provider, LLMProvider)

    def test_name(self) -> None:
        assert MockLLMProvider().name == "mock"

    def test_default_response(self) -> None:
        provider = MockLLMProvider()
        messages = [Message(role="user", content="random question xyz")]
        resp = provider.complete(messages)
        assert isinstance(resp, LLMResponse)
        assert resp.provider == "mock"
        assert resp.model == "mock-v1"
        assert "don't have enough information" in resp.text.lower()

    def test_bgp_pattern(self) -> None:
        provider = MockLLMProvider()
        messages = [Message(role="user", content="BGP neighbor session is down and resetting")]
        resp = provider.complete(messages)
        assert "bgp" in resp.text.lower()
        assert "show" in resp.text.lower()

    def test_ospf_pattern(self) -> None:
        provider = MockLLMProvider()
        messages = [Message(role="user", content="OSPF adjacency is flapping")]
        resp = provider.complete(messages)
        assert "ospf" in resp.text.lower()
        assert "mtu" in resp.text.lower()

    def test_stp_pattern(self) -> None:
        provider = MockLLMProvider()
        messages = [Message(role="user", content="STP topology churn detected")]
        resp = provider.complete(messages)
        assert "stp" in resp.text.lower() or "spanning-tree" in resp.text.lower()

    def test_packet_loss_pattern(self) -> None:
        provider = MockLLMProvider()
        messages = [Message(role="user", content="We're seeing packet loss on the uplink")]
        resp = provider.complete(messages)
        assert "packet loss" in resp.text.lower() or "interface" in resp.text.lower()

    def test_custom_responses(self) -> None:
        custom = {"magic keyword": "This is my custom response"}
        provider = MockLLMProvider(custom_responses=custom)
        messages = [Message(role="user", content="Tell me about the magic keyword")]
        resp = provider.complete(messages)
        assert resp.text == "This is my custom response"

    def test_custom_takes_priority(self) -> None:
        """Custom responses match before built-in patterns."""
        custom = {"bgp": "Custom BGP answer"}
        provider = MockLLMProvider(custom_responses=custom)
        messages = [Message(role="user", content="BGP neighbor session is down")]
        resp = provider.complete(messages)
        assert resp.text == "Custom BGP answer"

    def test_call_count(self) -> None:
        provider = MockLLMProvider()
        assert provider.call_count == 0
        provider.complete([Message(role="user", content="hello")])
        assert provider.call_count == 1
        provider.complete([Message(role="user", content="again")])
        assert provider.call_count == 2

    def test_last_messages_recorded(self) -> None:
        provider = MockLLMProvider()
        msgs = [
            Message(role="system", content="You are helpful"),
            Message(role="user", content="What is BGP?"),
        ]
        provider.complete(msgs)
        assert provider.last_messages is not None
        assert len(provider.last_messages) == 2
        assert provider.last_messages[1].content == "What is BGP?"

    def test_no_user_message(self) -> None:
        provider = MockLLMProvider()
        msgs = [Message(role="system", content="You are helpful")]
        resp = provider.complete(msgs)
        assert "don't have enough information" in resp.text.lower()

    def test_system_prompt_does_not_match_patterns(self) -> None:
        """Patterns match on the last USER message, not the system prompt."""
        provider = MockLLMProvider()
        msgs = [
            Message(role="system", content="BGP neighbor session is down"),
            Message(role="user", content="random unrelated question xyz"),
        ]
        resp = provider.complete(msgs)
        # Should get default response, not BGP pattern
        assert "don't have enough information" in resp.text.lower()


# ------------------------------------------------ Gemini provider structure
class TestGeminiProviderStructure:
    def test_message_conversion(self) -> None:
        from src.llm.gemini import _to_gemini_contents

        messages = [
            Message(role="system", content="You are helpful"),
            Message(role="user", content="Hello"),
            Message(role="assistant", content="Hi there"),
            Message(role="user", content="What is BGP?"),
        ]
        system_text, contents = _to_gemini_contents(messages)
        assert system_text == "You are helpful"
        assert len(contents) == 3
        assert contents[0]["role"] == "user"
        assert contents[1]["role"] == "model"
        assert contents[2]["role"] == "user"

    def test_no_system_message(self) -> None:
        from src.llm.gemini import _to_gemini_contents

        messages = [Message(role="user", content="Hello")]
        system_text, contents = _to_gemini_contents(messages)
        assert system_text is None
        assert len(contents) == 1

    def test_missing_api_key_raises(self) -> None:
        from src.llm.gemini import GeminiProvider
        with pytest.raises(ProviderError, match="API key"):
            GeminiProvider(api_key="")


# ----------------------------------------------- OpenAI provider structure
class TestOpenAIProviderStructure:
    def test_missing_api_key_raises(self) -> None:
        from src.llm.openai import OpenAIProvider
        with pytest.raises(ProviderError, match="API key"):
            OpenAIProvider(api_key="")


# ----------------------------------------------------- Config provider selection
class TestConfig:
    def test_default_is_mock(self) -> None:
        # Clear any provider env vars.
        old = os.environ.pop("GTAC_LLM_PROVIDER", None)
        try:
            from src.config import get_provider
            provider = get_provider()
            assert provider.name == "mock"
        finally:
            if old is not None:
                os.environ["GTAC_LLM_PROVIDER"] = old

    def test_explicit_mock(self) -> None:
        old = os.environ.get("GTAC_LLM_PROVIDER")
        os.environ["GTAC_LLM_PROVIDER"] = "mock"
        try:
            from src.config import get_provider
            provider = get_provider()
            assert provider.name == "mock"
        finally:
            if old is None:
                del os.environ["GTAC_LLM_PROVIDER"]
            else:
                os.environ["GTAC_LLM_PROVIDER"] = old

    def test_gemini_without_key_raises(self) -> None:
        old_provider = os.environ.get("GTAC_LLM_PROVIDER")
        old_key = os.environ.pop("GTAC_GEMINI_API_KEY", None)
        os.environ["GTAC_LLM_PROVIDER"] = "gemini"
        try:
            from src.config import get_provider
            with pytest.raises(ProviderError, match="GTAC_GEMINI_API_KEY"):
                get_provider()
        finally:
            if old_provider is None:
                del os.environ["GTAC_LLM_PROVIDER"]
            else:
                os.environ["GTAC_LLM_PROVIDER"] = old_provider
            if old_key is not None:
                os.environ["GTAC_GEMINI_API_KEY"] = old_key

    def test_openai_without_key_raises(self) -> None:
        old_provider = os.environ.get("GTAC_LLM_PROVIDER")
        old_key = os.environ.pop("GTAC_OPENAI_API_KEY", None)
        os.environ["GTAC_LLM_PROVIDER"] = "openai"
        try:
            from src.config import get_provider
            with pytest.raises(ProviderError, match="GTAC_OPENAI_API_KEY"):
                get_provider()
        finally:
            if old_provider is None:
                del os.environ["GTAC_LLM_PROVIDER"]
            else:
                os.environ["GTAC_LLM_PROVIDER"] = old_provider
            if old_key is not None:
                os.environ["GTAC_OPENAI_API_KEY"] = old_key

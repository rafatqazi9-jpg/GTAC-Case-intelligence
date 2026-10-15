"""Abstract LLM provider — the only seam between the conversation engine and
any specific language model API.

The investigation engine NEVER imports a concrete provider directly. Provider
selection happens in config.py via environment variable. This keeps the core
testable with MockLLMProvider and swappable without code changes.

Stdlib only. No third-party SDK.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Message:
    """One turn in a conversation. Roles follow the OpenAI/Gemini convention."""
    role: str   # "system" | "user" | "assistant"
    content: str

    def __post_init__(self) -> None:
        if self.role not in ("system", "user", "assistant"):
            raise ValueError(f"Invalid message role: {self.role!r}")
        if not self.content.strip():
            raise ValueError("Message content must not be empty")


@dataclass(frozen=True)
class LLMResponse:
    """What comes back from a provider. The engine consumes `text`;
    the rest is for logging and diagnostics."""
    text: str
    provider: str
    model: str
    usage: dict[str, int] = field(default_factory=dict)


class ProviderError(Exception):
    """Raised when an LLM provider fails in a way the engine should handle."""

    def __init__(self, provider: str, message: str,
                 status_code: int | None = None,
                 retryable: bool = False) -> None:
        self.provider = provider
        self.status_code = status_code
        self.retryable = retryable
        super().__init__(f"[{provider}] {message}")


class LLMProvider(ABC):
    """Abstract base for all LLM providers.

    Implementors must provide `complete` and `name`. Everything else —
    prompt construction, context injection, validation — lives in the
    conversation engine, not here.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier: 'mock', 'gemini', 'openai'."""
        ...

    @abstractmethod
    def complete(
        self,
        messages: list[Message],
        *,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        **kwargs: Any,
    ) -> LLMResponse:
        """Send a conversation to the provider and return a response.

        Raises ProviderError on failure. The engine handles retries,
        fallbacks, and user-facing error messages.
        """
        ...

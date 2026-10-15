"""Configuration loader. Reads from environment variables.

No secrets in source control. No config files checked in. The only
source of truth for API keys is the process environment.

Environment variables:
    GTAC_LLM_PROVIDER   Which provider to use: mock (default), gemini, openai
    GTAC_GEMINI_API_KEY  API key for Gemini (required when provider=gemini)
    GTAC_GEMINI_MODEL    Model name override (default: gemini-2.0-flash)
    GTAC_OPENAI_API_KEY  API key for OpenAI (required when provider=openai)
    GTAC_OPENAI_MODEL    Model name override (default: gpt-4o-mini)
    GTAC_OPENAI_BASE_URL Base URL for OpenAI-compatible API
"""
from __future__ import annotations

import os
from pathlib import Path

from .llm.base import LLMProvider, ProviderError


def _load_env_file() -> None:
    """Read .env from current directory or parent directory if present (stdlib only)."""
    for candidate in (Path(".env"), Path("../.env"), Path(__file__).resolve().parents[2] / ".env"):
        if candidate.is_file():
            try:
                for line in candidate.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip().strip("'\"")
                    if key and key not in os.environ:
                        os.environ[key] = val
            except Exception:
                pass


def get_provider() -> LLMProvider:
    """Instantiate the configured LLM provider.

    Defaults to MockLLMProvider when no provider is specified or when
    the required API key is missing. This makes local development and
    testing work out of the box with zero configuration.
    """
    # In automated pytest runs, default to mock provider unless explicitly configured
    is_pytest = "PYTEST_CURRENT_TEST" in os.environ
    if not is_pytest:
        _load_env_file()

    provider_name = os.environ.get("GTAC_LLM_PROVIDER", "mock" if is_pytest else "auto").lower().strip()

    if provider_name == "gemini" or (provider_name == "auto" and (os.environ.get("GTAC_GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY"))):
        api_key = os.environ.get("GTAC_GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            raise ProviderError(
                "gemini",
                "GTAC_GEMINI_API_KEY or GEMINI_API_KEY environment variable is required"
            )
        model = os.environ.get("GTAC_GEMINI_MODEL", "gemini-2.0-flash")

        from .llm.gemini import GeminiProvider
        return GeminiProvider(api_key=api_key, model=model)

    if provider_name in ("openai", "chatgpt") or (provider_name == "auto" and (os.environ.get("GTAC_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY"))):
        api_key = os.environ.get("GTAC_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            raise ProviderError(
                "openai",
                "GTAC_OPENAI_API_KEY or OPENAI_API_KEY environment variable is required"
            )
        model = os.environ.get("GTAC_OPENAI_MODEL") or os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        base_url = os.environ.get(
            "GTAC_OPENAI_BASE_URL") or os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"
        )

        from .llm.openai import OpenAIProvider
        return OpenAIProvider(api_key=api_key, model=model, base_url=base_url)

    # Default: mock provider. Always works, no configuration needed.
    from .llm.mock import MockLLMProvider
    return MockLLMProvider()

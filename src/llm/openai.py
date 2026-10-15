"""OpenAI-compatible LLM provider.

Opt-in: requires GTAC_OPENAI_API_KEY environment variable.
Uses stdlib urllib only — no openai SDK dependency.

Works with the OpenAI API and compatible endpoints (Azure OpenAI,
local models with an OpenAI-compatible server).
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from .base import LLMProvider, LLMResponse, Message, ProviderError

_DEFAULT_BASE = "https://api.openai.com/v1"


class OpenAIProvider(LLMProvider):
    """OpenAI (or compatible) provider via REST API. Stdlib only."""

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini",
        base_url: str = _DEFAULT_BASE,
    ) -> None:
        if not api_key:
            raise ProviderError("openai", "API key is required")
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")

    @property
    def name(self) -> str:
        return "openai"

    def complete(
        self,
        messages: list[Message],
        *,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        **kwargs: Any,
    ) -> LLMResponse:
        body: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": m.role, "content": m.content} for m in messages
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        url = f"{self._base_url}/chat/completions"
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url, data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            retryable = exc.code in (429, 500, 502, 503)
            raise ProviderError(
                "openai",
                f"HTTP {exc.code}: {exc.reason}",
                status_code=exc.code,
                retryable=retryable,
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ProviderError(
                "openai", f"Network error: {exc}",
                retryable=True,
            ) from exc

        # Extract text from OpenAI response structure.
        try:
            text = result["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(
                "openai",
                f"Malformed response: {result!r:.500}",
            ) from exc

        # Extract usage if available.
        usage: dict[str, int] = {}
        u = result.get("usage", {})
        if "prompt_tokens" in u:
            usage["prompt_tokens"] = u["prompt_tokens"]
        if "completion_tokens" in u:
            usage["completion_tokens"] = u["completion_tokens"]
        if "total_tokens" in u:
            usage["total_tokens"] = u["total_tokens"]

        return LLMResponse(
            text=text,
            provider="openai",
            model=result.get("model", self._model),
            usage=usage,
        )

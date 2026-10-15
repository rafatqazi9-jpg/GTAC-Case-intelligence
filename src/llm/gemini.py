"""Google Gemini LLM provider.

Opt-in: requires GTAC_GEMINI_API_KEY environment variable.
Uses stdlib urllib only — no google-genai SDK dependency.

The conversation engine does not import this directly. It is loaded
by config.py when provider='gemini' and the key is present.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from .base import LLMProvider, LLMResponse, Message, ProviderError

_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


def _to_gemini_contents(messages: list[Message]) -> tuple[str | None, list[dict[str, Any]]]:
    """Convert our Message list to Gemini API format.

    Gemini uses 'system_instruction' separately and 'contents' for the
    conversation. Roles map: user -> user, assistant -> model.
    """
    system_text: str | None = None
    contents: list[dict[str, Any]] = []

    for msg in messages:
        if msg.role == "system":
            # Gemini takes system instruction as a top-level field.
            system_text = msg.content
        elif msg.role == "user":
            contents.append({"role": "user", "parts": [{"text": msg.content}]})
        elif msg.role == "assistant":
            contents.append({"role": "model", "parts": [{"text": msg.content}]})

    return system_text, contents


class GeminiProvider(LLMProvider):
    """Google Gemini via REST API. Stdlib only."""

    def __init__(self, api_key: str, model: str = "gemini-2.0-flash") -> None:
        if not api_key:
            raise ProviderError("gemini", "API key is required")
        self._api_key = api_key
        self._model = model

    @property
    def name(self) -> str:
        return "gemini"

    def complete(
        self,
        messages: list[Message],
        *,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        **kwargs: Any,
    ) -> LLMResponse:
        system_text, contents = _to_gemini_contents(messages)

        body: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }
        if system_text:
            body["system_instruction"] = {
                "parts": [{"text": system_text}]
            }

        url = f"{_API_BASE}/{self._model}:generateContent?key={self._api_key}"
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            retryable = exc.code in (429, 500, 502, 503)
            raise ProviderError(
                "gemini",
                f"HTTP {exc.code}: {exc.reason}",
                status_code=exc.code,
                retryable=retryable,
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ProviderError(
                "gemini", f"Network error: {exc}",
                retryable=True,
            ) from exc

        # Extract text from Gemini response structure.
        try:
            candidates = result["candidates"]
            text = candidates[0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(
                "gemini",
                f"Malformed response: {result!r:.500}",
            ) from exc

        # Extract usage if available.
        usage: dict[str, int] = {}
        meta = result.get("usageMetadata", {})
        if "promptTokenCount" in meta:
            usage["prompt_tokens"] = meta["promptTokenCount"]
        if "candidatesTokenCount" in meta:
            usage["completion_tokens"] = meta["candidatesTokenCount"]
        if "totalTokenCount" in meta:
            usage["total_tokens"] = meta["totalTokenCount"]

        return LLMResponse(
            text=text,
            provider="gemini",
            model=self._model,
            usage=usage,
        )

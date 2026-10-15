"""Conversation engine — orchestrates the assistant's responses.

Receives a question, gathers investigation context, builds the prompt,
calls the LLM provider, validates the response, and manages session state.

The engine does NOT perform investigation logic itself. It consumes
results from the existing brief orchestrator and presents them through
natural-language conversation.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from ..adapters.base import Adapter, EvidenceWindow
from ..brief import build
from ..extract import extract_all, summarise_log
from ..llm.base import LLMProvider, LLMResponse, ProviderError
from ..models import Confidence
from .prompts import build_messages
from .session import Session


@dataclass(frozen=True)
class ChatResponse:
    """What the API returns for a chat message."""
    session_id: str
    text: str
    provider: str
    model: str
    data_source: str
    has_case: bool
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "session_id": self.session_id,
            "text": self.text,
            "provider": self.provider,
            "model": self.model,
            "data_source": self.data_source,
            "has_case": self.has_case,
        }
        if self.error:
            d["error"] = self.error
        return d


class ConversationEngine:
    """Manages investigation sessions and routes questions to the LLM.

    Thread safety: sessions are dict-accessed. For the stdlib
    ThreadingHTTPServer, this is acceptable since Python's GIL
    serialises dict mutations. A production deployment would add
    proper locking or use per-request session lookup.
    """

    def __init__(self, provider: LLMProvider, adapter: Adapter) -> None:
        self._provider = provider
        self._adapter = adapter
        self._sessions: dict[str, Session] = {}

    @property
    def provider_name(self) -> str:
        return self._provider.name

    @property
    def session_count(self) -> int:
        return len(self._sessions)

    def get_session(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def create_session(self, case_id: str | None = None) -> Session:
        """Create a new investigation session, optionally loading a case."""
        session = Session()

        if case_id:
            self._load_case(session, case_id)

        self._sessions[session.id] = session
        return session

    def list_sessions(self) -> list[dict[str, Any]]:
        return [s.to_dict() for s in self._sessions.values()]

    def _load_case(self, session: Session, case_id: str) -> None:
        """Load a fixture case into the session and run the investigation."""
        raw = self._adapter._raw.get(case_id)  # type: ignore[attr-defined]
        if raw is None:
            raise KeyError(case_id)

        # Set case context on the session.
        session.case_id = case_id
        session.platform = raw.get("platform")
        session.sw_version = raw.get("sw_version")
        session.symptom_class = raw.get("symptom_class")
        session.symptom_text = raw.get("symptom_text")
        session.data_source = "synthetic"

        # Run the investigation engine.
        created_at = datetime.fromisoformat(raw["created_at"])
        window = EvidenceWindow(cutoff=created_at + timedelta(days=2))
        brief = build(case_id, self._adapter, window,
                      threshold=Confidence.MEDIUM)
        session.brief = brief
        session.findings = list(brief.findings)

    def ask(
        self,
        question: str,
        session_id: str | None = None,
        case_id: str | None = None,
        evidence: dict[str, Any] | None = None,
        timeline: dict[str, Any] | None = None,
        evidence_source: str | None = None,
    ) -> ChatResponse:
        """Process a user question and return the assistant's response.

        If session_id is provided, reuses that session. Otherwise creates
        a new one, optionally loading the specified case or evidence.
        """
        # Resolve or create session.
        session: Session | None = None
        if session_id:
            session = self._sessions.get(session_id)

        if session is None:
            session = self.create_session(case_id=case_id)
        elif case_id and session.case_id != case_id:
            # Load a different case into the existing session.
            self._load_case(session, case_id)

        # If active evidence was supplied with the request, ground session in it
        if evidence and isinstance(evidence, dict):
            session.set_evidence_context(
                evidence=evidence,
                timeline=timeline if isinstance(timeline, dict) else None,
                source_name=evidence_source or "analyzed case evidence",
            )

        # If user pasted log text directly into the chat prompt, run extractor automatically
        if ("\n" in question or "SEC-" in question or "NSM-" in question) and len(question) > 60:
            ev = extract_all(question, source="pasted chat logs")
            log_summary = summarise_log(question)
            if ev.running_version:
                session.sw_version = ev.running_version.value
            if ev.platforms:
                session.platform = ev.platforms[0].value
            if ev.errors:
                session.symptom_text = ", ".join(e.value for e in ev.errors[:3])
            session.data_source = "pasted log input"

        # Build the prompt.
        messages = build_messages(session, question)

        # Call the LLM.
        try:
            llm_response: LLMResponse = self._provider.complete(messages)
            response_text = llm_response.text
            error = None
        except ProviderError as exc:
            response_text = (
                f"I encountered an error communicating with the AI provider: "
                f"{exc}. Please try again."
            )
            error = str(exc)
            llm_response = LLMResponse(
                text=response_text,
                provider=self._provider.name,
                model="error",
            )

        # Update session history.
        session.add_user_message(question)
        session.add_assistant_message(response_text)

        return ChatResponse(
            session_id=session.id,
            text=response_text,
            provider=llm_response.provider,
            model=llm_response.model,
            data_source=session.data_source,
            has_case=session.has_case,
            error=error,
        )

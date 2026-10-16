"""Local analyzer app. Runs the real extractor behind a small HTTP API.

    python -m src.server
    python -m src.server --port 8900 --open

WHY LOCAL, AND WHY IT STAYS LOCAL
---------------------------------
This page is for pasting real case text into. Real case text carries customer
names, addresses, topology and contact details, so it must not be sent
anywhere. Three things enforce that:

* The socket binds to 127.0.0.1 only. Not 0.0.0.0 -- nothing outside this
  machine can reach it, including other hosts on the same LAN.
* Nothing is written to disk. Submitted text lives in memory for the duration
  of one request and is never logged; the access log prints method and path
  only, never a body.
* There are no outbound calls. The extractor is pure regex over the text you
  gave it.

It is also why the published demo artifact cannot do this: a hosted page cannot
reach your machine, and pasting case text into a hosted page would send it off
the box. The static artifact shows recorded output; this shows live output on
text that never leaves your laptop.

Read-only, like the rest of Phase 1: no systems of record, no writes.

Stdlib only -- no new dependency.
"""
from __future__ import annotations
from datetime import datetime, timedelta

import argparse
import base64
import json
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .analyze import as_json
from .extract import extract_all, summarise_log
from .adapters.base import EvidenceWindow
from .adapters.mock import MockAdapter
from .brief import build
from .config import get_provider
from .conversation.engine import ConversationEngine
from .escalation import build_escalation_package
from .evidence.timeline import TimelineBuilder
from .ingest.pipeline import IngestionPipeline
from .models import Confidence

APP = Path(__file__).resolve().parents[1] / "demo" / "app.html"
SAMPLES = Path(__file__).resolve().parents[1] / "data" / "samples"

# A pasted show-tech or raslog is genuinely large; a request past this is a
# mistake or an attack, and either way should be refused rather than buffered.
MAX_BODY = 8 * 1024 * 1024

# Diagnostic archives and ShowSupport bundles can be hundreds of megabytes.
MAX_UPLOAD_BODY = 750 * 1024 * 1024  # 750 MB for base64-encoded archive uploads

# Lazy singleton: created on first chat request so the server starts fast
# and tests that don't use chat don't pay for provider initialisation.
_engine: ConversationEngine | None = None


def _get_engine() -> ConversationEngine:
    global _engine
    if _engine is None:
        _engine = ConversationEngine(
            provider=get_provider(),
            adapter=MockAdapter(),
        )
    return _engine


def analyze_payload(text: str, mode: str) -> dict[str, Any]:
    """Run the real extractor. The one place the server does any work."""
    want_case = mode in ("case", "both")
    want_log = mode in ("log", "both")
    ev = extract_all(text, source="pasted input") if want_case else None
    log = summarise_log(text) if want_log else None
    return as_json(ev, log)


def investigate_payload(case_id: str) -> dict[str, Any]:
    """Build an evidence-windowed brief from synthetic fixture data."""
    adapter = MockAdapter()

    raw = adapter._raw.get(case_id)
    if raw is None:
        raise KeyError(case_id)

    created_at = datetime.fromisoformat(raw["created_at"])
    window = EvidenceWindow(cutoff=created_at + timedelta(days=2))
    brief = build(case_id, adapter, window, threshold=Confidence.MEDIUM)

    return {
        "case_id": brief.case_id,
        "platform": raw.get("platform"),
        "sw_version": raw.get("sw_version"),
        "symptom_text": raw.get("symptom_text"),
        "generated_at": brief.generated_at.isoformat(),
        "abstained": brief.abstained,
        "abstain_reason": brief.abstain_reason,
        "rendered": brief.render(),
        "findings": [
            {
                "module": finding.module,
                "kind": finding.kind,
                "statement": finding.statement,
                "confidence": finding.confidence.value,
                "sources": list(finding.sources),
            }
            for finding in brief.findings
        ],
    }


def fixtures_payload() -> dict[str, Any]:
    """List safe display metadata for local synthetic fixture cases."""
    adapter = MockAdapter()
    cases = []

    for raw in adapter._raw.values():
        cases.append({
            "case_id": raw["id"],
            "platform": raw["platform"],
            "sw_version": raw["sw_version"],
            "symptom_class": raw["symptom_class"],
            "symptom_text": raw["symptom_text"],
            "region": raw["region"],
            "case_type": raw.get("case_type"),
            "created_at": raw["created_at"],
        })

    cases.sort(key=lambda case: case["case_id"])

    return {
        "source": "local synthetic fixtures",
        "count": len(cases),
        "cases": cases,
    }


def upload_payload(filename: str, content_b64: str, password: str | None = None) -> dict[str, Any]:
    """Ingest, decompress, and analyze uploaded log files or diagnostic archives."""
    try:
        raw_bytes = base64.b64decode(content_b64)
    except Exception as exc:
        raise ValueError(f"Invalid base64 payload: {exc}") from exc

    pipeline = IngestionPipeline()
    report = pipeline.ingest_bytes(raw_bytes, filename=filename, password=password)

    # If extraction produced readable log text, run evidence extraction and summarization
    analysis: dict[str, Any] | None = None
    timeline_dict: dict[str, Any] | None = None

    if report.combined_log_text.strip():
        ev = extract_all(report.combined_log_text, source=f"uploaded archive: {filename}")
        log = summarise_log(report.combined_log_text)
        analysis = as_json(ev, log)

        # Build cross-file timeline from processed items (sample up to 30 files to ensure responsive processing)
        text_files = [
            (item.relative_path, item.extracted_text)
            for item in report.items
            if item.is_text and item.extracted_text
        ][:30]
        if not text_files:
            text_files = [(filename, report.combined_log_text)]

        tb = TimelineBuilder()
        tl = tb.build_from_files(text_files)
        timeline_dict = {
            "total_events": tl.total_events,
            "devices": tl.devices_seen,
            "start_time": tl.start_time.isoformat() if tl.start_time else None,
            "end_time": tl.end_time.isoformat() if tl.end_time else None,
            "critical_count": len(tl.critical_events),
            "rendered": tl.render_timeline(),
            "correlations": tl.correlations,
            "gaps": tl.gaps_detected,
        }

    # Core dump detection & bundle completeness evaluation
    core_dumps = [
        item.relative_path for item in report.items
        if any(marker in item.relative_path.lower() for marker in (".core", ".dmp", "core.", ".dump", "crash"))
    ]
    has_config = any("config" in item.relative_path.lower() or item.relative_path.lower().endswith(".cfg") for item in report.items)
    has_syslog = any(any(m in item.relative_path.lower() for m in ("raslog", "syslog", "messages", ".log")) for item in report.items)
    has_supportsave = (
        any(any(m in item.relative_path.lower() for m in ("supportsave", "showsupport", "show_support", "show_tech", "support", "techsupport")) for item in report.items)
        or any(m in filename.lower() for m in ("supportsave", "showsupport", "show_support", "techsupport", "show_tech"))
    )

    scorecard = {
        "has_core_dumps": len(core_dumps) > 0,
        "core_dumps": len(core_dumps),
        "core_dump_files": core_dumps,
        "has_config": has_config,
        "has_syslog": has_syslog,
        "has_supportsave": has_supportsave,
    }

    if analysis and "evidence" in analysis:
        analysis["evidence"]["scorecard"] = scorecard
        analysis["evidence"]["core_dumps"] = core_dumps

    return {
        "filename": report.original_filename,
        "format": report.detected_format,
        "total_entries": report.total_entries,
        "processed_count": report.processed_count,
        "skipped_count": report.skipped_count,
        "is_encrypted": report.is_encrypted,
        "requires_password": report.requires_password,
        "encryption_type": report.encryption_type,
        "errors": report.errors,
        "items": [
            {
                "path": item.relative_path,
                "size": item.size_bytes,
                "is_text": item.is_text,
                "status": item.status,
                "reason": item.reason,
            }
            for item in report.items
        ],
        "analysis": analysis,
        "timeline": timeline_dict,
        "scorecard": scorecard,
        "core_dumps": core_dumps,
    }


def escalation_payload(
    case_id: str | None = None,
    evidence: dict[str, Any] | None = None,
    timeline: dict[str, Any] | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Generate structured DE / Tier 3 engineering escalation package."""
    brief_data: dict[str, Any] | None = None
    if case_id and isinstance(case_id, str) and case_id.strip():
        try:
            brief_data = investigate_payload(case_id.strip())
        except KeyError:
            pass

    pkg = build_escalation_package(
        case_id=case_id,
        evidence=evidence,
        timeline=timeline,
        brief_data=brief_data,
        notes=notes,
    )
    return pkg.to_dict()


class Handler(BaseHTTPRequestHandler):
    server_version = "gtac-analyzer"

    # ------------------------------------------------------------ plumbing
    def log_message(self, fmt: str, *args: Any) -> None:
        # Method and path only. Never a body: bodies may hold customer data.
        print(f"  {self.command} {self.path}")

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # This page runs only from here and talks only to here.
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: dict[str, Any]) -> None:
        self._send(code, json.dumps(payload).encode("utf-8"),
                   "application/json; charset=utf-8")

    # ---------------------------------------------------------------- GET
    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]

        if path == "/api/fixtures":
            try:
                self._json(200, fixtures_payload())
            except Exception:  # noqa: BLE001
                self._json(500, {
                    "error": "Unable to load fixture catalog"
                })
            return

        if path in ("/", "/index.html"):
            if not APP.is_file():
                self._send(500, b"demo/app.html is missing", "text/plain")
                return
            self._send(200, APP.read_bytes(), "text/html; charset=utf-8")
            return

        if path == "/api/samples":
            out = {}
            for name in ("sample_case.txt", "sample_raslog.txt"):
                f = SAMPLES / name
                out[name] = (
                    f.read_text(encoding="utf-8") if f.is_file() else ""
                )
            self._json(200, out)
            return

        if path == "/api/sessions":
            try:
                engine = _get_engine()
                self._json(200, {
                    "provider": engine.provider_name,
                    "sessions": engine.list_sessions(),
                })
            except Exception:  # noqa: BLE001
                self._json(500, {"error": "Unable to list sessions"})
            return

        if path == "/api/health":
            try:
                engine = _get_engine()
                self._json(200, {
                    "status": "online",
                    "bind_address": "127.0.0.1",
                    "loopback_only": True,
                    "retention": "in-memory ephemeral (zero permanent disk persistence)",
                    "provider": engine.provider_name,
                    "active_sessions": engine.session_count,
                })
            except Exception as exc:  # noqa: BLE001
                self._json(500, {"status": "degraded", "error": str(exc)})
            return

        self._send(404, b"not found", "text/plain")

    # --------------------------------------------------------------- POST
    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        if path not in ("/api/analyze", "/api/investigate", "/api/chat", "/api/upload", "/api/escalation"):
            self._send(404, b"not found", "text/plain")
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._json(400, {"error": "bad Content-Length"})
            return
        if length <= 0:
            self._json(400, {"error": "empty request"})
            return
        max_allowed = MAX_UPLOAD_BODY if path == "/api/upload" else MAX_BODY
        if length > max_allowed:
            self._json(413, {"error": f"input larger than {max_allowed // 1024 // 1024} MB"})
            return

        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._json(400, {"error": "body must be UTF-8 JSON"})
            return
        if path == "/api/investigate":
            case_id = payload.get("case_id")
            if not isinstance(case_id, str) or not case_id.strip():
                self._json(400, {"error": "case_id must be a non-empty string"})
                return

            try:
                self._json(200, investigate_payload(case_id.strip()))
            except KeyError:
                self._json(404, {"error": "case_id not found in fixture data"})
            except Exception as exc:  # noqa: BLE001
                self._json(500, {
                    "error": f"{type(exc).__name__} while investigating"
                })
            return

        if path == "/api/chat":
            message = payload.get("message")
            if not isinstance(message, str) or not message.strip():
                self._json(400, {"error": "message must be a non-empty string"})
                return

            session_id = payload.get("session_id")
            case_id = payload.get("case_id")
            evidence = payload.get("evidence")
            timeline = payload.get("timeline")
            evidence_source = payload.get("evidence_source")

            try:
                engine = _get_engine()
                response = engine.ask(
                    question=message.strip(),
                    session_id=session_id if isinstance(session_id, str) else None,
                    case_id=case_id.strip() if isinstance(case_id, str) and case_id.strip() else None,
                    evidence=evidence if isinstance(evidence, dict) else None,
                    timeline=timeline if isinstance(timeline, dict) else None,
                    evidence_source=evidence_source.strip() if isinstance(evidence_source, str) and evidence_source.strip() else None,
                )
                self._json(200, response.to_dict())
            except KeyError:
                self._json(404, {"error": "case_id not found in fixture data"})
            except Exception as exc:  # noqa: BLE001
                self._json(500, {
                    "error": f"{type(exc).__name__} while processing chat"
                })
            return

        if path == "/api/upload":
            filename = payload.get("filename")
            content_b64 = payload.get("content_b64")
            password = payload.get("password")

            if not isinstance(filename, str) or not filename.strip():
                self._json(400, {"error": "filename must be a non-empty string"})
                return
            if not isinstance(content_b64, str) or not content_b64.strip():
                self._json(400, {"error": "content_b64 must be a non-empty base64 string"})
                return

            try:
                res = upload_payload(
                    filename=filename.strip(),
                    content_b64=content_b64.strip(),
                    password=password.strip() if isinstance(password, str) and password.strip() else None,
                )
                self._json(200, res)
            except ValueError as exc:
                self._json(400, {"error": str(exc)})
            except Exception as exc:  # noqa: BLE001
                self._json(500, {"error": f"{type(exc).__name__} during upload ingestion"})
            return

        if path == "/api/escalation":
            case_id = payload.get("case_id")
            evidence = payload.get("evidence")
            timeline = payload.get("timeline")
            notes = payload.get("notes")
            try:
                res = escalation_payload(
                    case_id=case_id.strip() if isinstance(case_id, str) and case_id.strip() else None,
                    evidence=evidence if isinstance(evidence, dict) else None,
                    timeline=timeline if isinstance(timeline, dict) else None,
                    notes=notes.strip() if isinstance(notes, str) and notes.strip() else None,
                )
                self._json(200, res)
            except Exception as exc:  # noqa: BLE001
                self._json(500, {"error": f"{type(exc).__name__} during escalation generation"})
            return

        text = payload.get("text")
        if not isinstance(text, str) or not text.strip():
            self._json(400, {"error": "no text supplied"})
            return
        mode = payload.get("mode", "both")
        if mode not in ("case", "log", "both"):
            self._json(400, {"error": "mode must be case, log or both"})
            return

        try:
            self._json(200, analyze_payload(text, mode))
        except Exception as exc:                      # noqa: BLE001
            # Report the failure without echoing the submitted text back.
            self._json(500, {"error": f"{type(exc).__name__} while extracting"})


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Local analyzer app (127.0.0.1 only)")
    ap.add_argument("--port", type=int, default=8899)
    ap.add_argument("--open", action="store_true", help="open a browser tab")
    args = ap.parse_args(argv)

    # 127.0.0.1, never 0.0.0.0: pasted case text must not be reachable from
    # anywhere but this machine.
    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"GTAC analyzer  ->  {url}")
    print("  bound to 127.0.0.1 only · nothing written to disk · no outbound calls")
    print("  Ctrl-C to stop\n")
    if args.open:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

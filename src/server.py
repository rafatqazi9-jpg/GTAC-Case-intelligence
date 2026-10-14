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
from .models import Confidence

APP = Path(__file__).resolve().parents[1] / "demo" / "app.html"
SAMPLES = Path(__file__).resolve().parents[1] / "data" / "samples"

# A pasted show-tech or raslog is genuinely large; a request past this is a
# mistake or an attack, and either way should be refused rather than buffered.
MAX_BODY = 8 * 1024 * 1024


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

        self._send(404, b"not found", "text/plain")

    # --------------------------------------------------------------- POST
    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        if path not in ("/api/analyze", "/api/investigate"):
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
        if length > MAX_BODY:
            self._json(413, {"error": f"input larger than {MAX_BODY // 1024 // 1024} MB"})
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

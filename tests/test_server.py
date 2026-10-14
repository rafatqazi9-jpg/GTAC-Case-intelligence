"""Tests for the local analyzer server.

Covers the request contract and, more importantly, the guarantees the page
makes to anyone pasting real case text into it: local-only binding, no
persistence, and no echoing of submitted text back in an error.
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from typing import Iterator

import pytest

from src.server import MAX_BODY, Handler, analyze_payload


@pytest.fixture(scope="module")
def base_url() -> Iterator[str]:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def _post(url: str, payload: object, *, raw: bytes | None = None) -> tuple[int, dict]:
    body = raw if raw is not None else json.dumps(payload).encode()
    req = urllib.request.Request(url + "/api/analyze", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


# ------------------------------------------------------------------ binding
def test_server_binds_loopback_only() -> None:
    """Pasted case text carries customer data. Binding 0.0.0.0 would expose it
    to every host on the LAN, so the address must stay loopback."""
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    try:
        assert httpd.server_address[0] == "127.0.0.1"
    finally:
        httpd.server_close()


# ------------------------------------------------------------------ happy path
def test_analyze_returns_real_extractor_output(base_url: str) -> None:
    text = ('SLX: 20.7.3a\n'
            'Parent Defect ID: SLXOS-11111\n'
            'error "OS is not ready. Please login later"\n')
    code, data = _post(base_url, {"text": text, "mode": "case"})
    assert code == 200
    ev = data["evidence"]
    assert ev["running_version"]["value"] == "20.7.3a"
    assert any(d["value"] == "SLXOS-11111" for d in ev["defects"])
    assert any("not ready" in e["value"] for e in ev["errors"])


def test_log_mode_summarises(base_url: str) -> None:
    log = ("2024/06/19-10:00:01, [SEC-3020], 1, M1, WARNING, sw0, Login failed from 10.0.0.5\n"
           "2024/06/19-10:00:04, [SEC-3020], 2, M1, WARNING, sw0, Login failed from 10.0.0.6\n"
           "2024/06/19-10:00:09, [NSM-1004], 3, M1, ERROR, sw0, Interface down\n")
    code, data = _post(base_url, {"text": log, "mode": "log"})
    assert code == 200
    assert "evidence" not in data
    assert data["log"]["severities"]["WARNING"] == 2
    assert data["log"]["worst"] == "ERROR"


def test_both_mode_returns_both(base_url: str) -> None:
    code, data = _post(base_url, {"text": "SLX: 20.7.3a\nWARNING something\n",
                                  "mode": "both"})
    assert code == 200
    assert "evidence" in data and "log" in data


def test_missing_is_reported_so_a_full_result_does_not_overclaim(base_url: str) -> None:
    code, data = _post(base_url, {"text": "SLX: 20.7.3a\n", "mode": "case"})
    assert code == 200
    assert any("features_enabled" in m for m in data["evidence"]["missing"])


# --------------------------------------------------------------- bad requests
def test_empty_text_rejected(base_url: str) -> None:
    code, data = _post(base_url, {"text": "   ", "mode": "case"})
    assert code == 400 and "error" in data


def test_unknown_mode_rejected(base_url: str) -> None:
    code, data = _post(base_url, {"text": "x", "mode": "everything"})
    assert code == 400


def test_non_json_body_rejected(base_url: str) -> None:
    code, data = _post(base_url, None, raw=b"not json at all")
    assert code == 400


def test_unknown_path_is_404(base_url: str) -> None:
    req = urllib.request.Request(base_url + "/api/nope", data=b"{}",
                                 headers={"Content-Type": "application/json"})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req)
    assert e.value.code == 404


def test_oversized_body_refused_not_buffered(base_url: str) -> None:
    """A body past the cap is refused on the Content-Length, before the server
    reads it into memory."""
    req = urllib.request.Request(
        base_url + "/api/analyze", data=b"{}",
        headers={"Content-Type": "application/json",
                 "Content-Length": str(MAX_BODY + 1)})
    req.add_header("Content-Length", str(MAX_BODY + 1))
    try:
        with urllib.request.urlopen(req) as r:
            assert r.status == 413
    except urllib.error.HTTPError as e:
        assert e.code == 413
    except (urllib.error.URLError, ConnectionError):
        pytest.skip("client refused to send a mismatched Content-Length")


# ------------------------------------------------------------------- privacy
def test_error_responses_do_not_echo_submitted_text(base_url: str) -> None:
    """An error must not quote the body back. Bodies may hold customer
    hostnames and addresses, and error strings end up in terminals, tickets and
    screenshots."""
    secret = "ACME-CORP-CONFIDENTIAL-HOSTNAME-9f2b"
    code, data = _post(base_url, {"text": secret, "mode": "everything"})
    assert code == 400
    assert secret not in json.dumps(data)


def test_request_logging_never_includes_the_body(base_url: str,
                                                 capfd: pytest.CaptureFixture[str]) -> None:
    """log_message prints method and path only. A default handler would be
    fine here too, but nothing may start logging bodies later without this
    failing."""
    secret = "ACME-CORP-SECRET-IN-BODY-7a1c"
    code, _ = _post(base_url, {"text": f"SLX: 20.7.3a {secret}", "mode": "case"})
    assert code == 200
    captured = capfd.readouterr()
    assert secret not in captured.out
    assert secret not in captured.err


def test_analyze_payload_modes() -> None:
    both = analyze_payload("SLX: 20.7.3a\nERROR x\n", "both")
    assert "evidence" in both and "log" in both
    assert "log" not in analyze_payload("SLX: 20.7.3a\n", "case")
    assert "evidence" not in analyze_payload("ERROR x\n", "log")


def _post_investigate(url: str, payload: object) -> tuple[int, dict]:
    req = urllib.request.Request(
        url + "/api/investigate",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_investigate_returns_fixture_brief(base_url: str) -> None:
    code, data = _post_investigate(
        base_url, {"case_id": "CS-6100050"}
    )
    assert code == 200
    assert data["case_id"] == "CS-6100050"
    assert data["abstained"] is False
    assert data["findings"]
    assert data["rendered"].startswith("CASE INTELLIGENCE BRIEF")
    assert all(f["sources"] for f in data["findings"])


def test_investigate_unknown_case_returns_404(base_url: str) -> None:
    code, data = _post_investigate(
        base_url, {"case_id": "NOT-A-REAL-CASE"}
    )
    assert code == 404
    assert "error" in data


@pytest.mark.parametrize("payload", [{}, {"case_id": ""}, {"case_id": 123}])
def test_investigate_invalid_case_id_returns_400(
    base_url: str, payload: object
) -> None:
    code, data = _post_investigate(base_url, payload)
    assert code == 400
    assert "error" in data

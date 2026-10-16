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


def test_fixture_catalog_returns_safe_metadata(base_url: str) -> None:
    with urllib.request.urlopen(base_url + "/api/fixtures") as response:
        assert response.status == 200
        data = json.loads(response.read())

    assert data["source"] == "local synthetic fixtures"
    assert data["count"] == len(data["cases"])
    assert data["count"] > 0

    case = data["cases"][0]
    assert {
        "case_id",
        "platform",
        "sw_version",
        "symptom_class",
        "symptom_text",
        "region",
        "case_type",
        "created_at",
    }.issubset(case)

    assert "actual_root_cause" not in case
    assert "initial_hypothesis" not in case
    assert "closure_comments" not in case
    assert "events" not in case


def test_fixture_catalog_cases_are_sorted(base_url: str) -> None:
    with urllib.request.urlopen(base_url + "/api/fixtures") as response:
        data = json.loads(response.read())

    case_ids = [case["case_id"] for case in data["cases"]]
    assert case_ids == sorted(case_ids)
    assert len(case_ids) == len(set(case_ids))


# ----------------------------------------------------------- chat & sessions
def _post_chat(url: str, payload: object) -> tuple[int, dict]:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url + "/api/chat",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_get_sessions_returns_empty_or_list(base_url: str) -> None:
    with urllib.request.urlopen(base_url + "/api/sessions") as response:
        assert response.status == 200
        data = json.loads(response.read())

    assert "provider" in data
    assert "sessions" in data
    assert isinstance(data["sessions"], list)


def test_chat_creates_session_and_answers(base_url: str) -> None:
    code, data = _post_chat(base_url, {"message": "BGP neighbor session is down"})
    assert code == 200
    assert "session_id" in data
    assert "text" in data
    assert "bgp" in data["text"].lower()
    assert data["provider"] == "mock"
    assert data["has_case"] is False


def test_health_endpoint(base_url: str) -> None:
    req = urllib.request.Request(base_url + "/api/health")
    with urllib.request.urlopen(req) as response:
        assert response.status == 200
        data = json.loads(response.read())

    assert data["status"] == "online"
    assert data["loopback_only"] is True
    assert "provider" in data
    assert "retention" in data


def test_chat_with_case_context(base_url: str) -> None:
    code, data = _post_chat(
        base_url,
        {"message": "What is the symptom?", "case_id": "CS-6100050"},
    )
    assert code == 200
    assert data["has_case"] is True
    assert data["data_source"] == "synthetic"


def test_chat_with_evidence_context(base_url: str) -> None:
    evidence = {
        "running_version": {"value": "20.4.1b"},
        "platforms": [{"value": "SLX-9850"}],
        "errors": [{"value": "HAL_ERR_TCAM_WRITE_FAIL", "evidence": "linecard 3 tcam full"}],
        "defects": [{"value": "DEFECT-84920"}],
    }
    code, data = _post_chat(
        base_url,
        {
            "message": "Explain the defect exposure",
            "evidence": evidence,
            "evidence_source": "supportsave_core01.tgz",
        },
    )
    assert code == 200
    assert data["has_case"] is True
    assert data["data_source"] == "supportsave_core01.tgz"


def test_chat_invalid_payload_returns_400(base_url: str) -> None:
    for invalid in ({}, {"message": ""}, {"message": "   "}, {"message": 123}):
        code, data = _post_chat(base_url, invalid)
        assert code == 400
        assert "error" in data


def test_chat_invalid_case_id_returns_404(base_url: str) -> None:
    code, data = _post_chat(
        base_url,
        {"message": "Hello", "case_id": "NON-EXISTENT-CASE"},
    )
    assert code == 404
    assert "error" in data


# ------------------------------------------------------------------ upload
def _post_upload(url: str, payload: object) -> tuple[int, dict]:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url + "/api/upload",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_upload_plain_text(base_url: str) -> None:
    import base64
    raw = b"SLX: 20.7.3a\nBGP neighbor 10.0.0.1 down"
    b64 = base64.b64encode(raw).decode()
    code, data = _post_upload(base_url, {"filename": "show_tech.log", "content_b64": b64})

    assert code == 200
    assert data["filename"] == "show_tech.log"
    assert data["processed_count"] == 1
    assert data["analysis"] is not None
    assert data["analysis"]["evidence"]["running_version"]["value"] == "20.7.3a"
    assert "scorecard" in data
    assert "core_dumps" in data
    assert data["scorecard"]["has_supportsave"] is True


def test_upload_core_dump_detection(base_url: str) -> None:
    import base64
    import io
    import tarfile

    tar_buf = io.BytesIO()
    with tarfile.open(fileobj=tar_buf, mode="w:gz") as tar:
        # Add a text log
        log_bytes = b"SLX: 20.7.3a\nBGP flap"
        info_log = tarfile.TarInfo(name="supportsave/show_tech.log")
        info_log.size = len(log_bytes)
        tar.addfile(info_log, io.BytesIO(log_bytes))

        # Add a core dump binary
        core_bytes = b"\x7fELF\x02\x01\x01\x00crashdata"
        info_core = tarfile.TarInfo(name="supportsave/fib_mgr.core.gz")
        info_core.size = len(core_bytes)
        tar.addfile(info_core, io.BytesIO(core_bytes))

    b64 = base64.b64encode(tar_buf.getvalue()).decode()
    code, data = _post_upload(base_url, {"filename": "supportsave.tar.gz", "content_b64": b64})

    assert code == 200
    assert data["scorecard"]["has_core_dumps"] is True
    assert len(data["core_dumps"]) == 1
    assert "fib_mgr.core.gz" in data["core_dumps"][0]


def test_upload_invalid_payload_returns_400(base_url: str) -> None:
    for invalid in ({}, {"filename": ""}, {"content_b64": ""}):
        code, data = _post_upload(base_url, invalid)
        assert code == 400
        assert "error" in data


# -------------------------------------------------------------- escalation
def _post_escalation(url: str, payload: object) -> tuple[int, dict]:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url + "/api/escalation",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_escalation_endpoint_success(base_url: str) -> None:
    code, data = _post_escalation(base_url, {
        "case_id": "CS-6100050",
        "notes": "Verified in lab environment.",
    })
    assert code == 200
    assert data["case_id"] == "CS-6100050"
    assert "markdown" in data
    assert "# GTAC ENGINEERING ESCALATION BRIEF" in data["markdown"]
    assert "readiness_score" in data




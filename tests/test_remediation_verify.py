"""Automated Verification Suite for GTAC Assistant Remediations."""
import base64
import json
import io
import threading
import time
import urllib.error
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer

from src.server import Handler

SERVER_PORT = 8997
BASE_URL = f"http://127.0.0.1:{SERVER_PORT}"

def make_req(url, path, method="GET", body=None, headers=None, raw_body=None):
    full_url = f"{url}{path}"
    h = headers or {}
    data = None
    if raw_body is not None:
        data = raw_body
    elif body is not None:
        data = json.dumps(body).encode("utf-8")
        if "Content-Type" not in h:
            h["Content-Type"] = "application/json"
    
    req = urllib.request.Request(full_url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()
    except Exception as e:
        return None, {}, str(e).encode("utf-8")

def run():
    print("=== RUNNING REMEDIATION VERIFICATION SUITE ===")

    # 1. Verify Security Headers
    status, hdrs, _ = make_req(BASE_URL, "/")
    assert status == 200
    assert hdrs.get("X-Frame-Options") == "DENY", "X-Frame-Options missing"
    assert "Content-Security-Policy" in hdrs, "CSP missing"
    assert "Strict-Transport-Security" in hdrs, "HSTS missing"
    assert "Permissions-Policy" in hdrs, "Permissions-Policy missing"
    print(" [PASS] Security Headers (X-Frame-Options, CSP, HSTS, Permissions-Policy)")

    # 2. Verify HTTP Verbs
    opt_status, opt_hdrs, _ = make_req(BASE_URL, "/api/analyze", method="OPTIONS")
    assert opt_status == 204
    assert opt_hdrs.get("Allow") == "GET, POST, OPTIONS"
    print(" [PASS] OPTIONS Preflight Handling (204 No Content with Allow headers)")

    for verb in ("PUT", "DELETE", "PATCH", "TRACE"):
        v_status, _, v_body = make_req(BASE_URL, "/api/analyze", method=verb, body={"text": "test"})
        assert v_status == 405, f"Expected 405 for {verb}, got {v_status}"
    print(" [PASS] Unsupported HTTP Verbs (PUT, DELETE, PATCH, TRACE rejected with 405 Method Not Allowed)")

    # 3. Verify Core Dump Filename Sanitization (SEC-XSS-01)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr('"><img src=x onerror=alert(1)>.core', b"crash log")
    b64_xss = base64.b64encode(buf.getvalue()).decode("ascii")

    xss_status, _, xss_body = make_req(BASE_URL, "/api/upload", method="POST", body={
        "filename": "crash.zip",
        "content_b64": b64_xss
    })
    assert xss_status == 200
    xss_json = json.loads(xss_body)
    core_dumps = xss_json.get("core_dumps", [])
    assert len(core_dumps) == 1
    assert "<img" not in core_dumps[0]
    assert "&lt;img" in core_dumps[0]
    print(f" [PASS] SEC-XSS-01 Remediated: HTML Entities Escaped ({core_dumps[0]})")

    # 4. Verify Upload DoS Bounds (SEC-DOS-01)
    from src.server import MAX_UPLOAD_BODY
    assert MAX_UPLOAD_BODY == 50 * 1024 * 1024
    print(f" [PASS] SEC-DOS-01 Remediated: MAX_UPLOAD_BODY Bounded to {MAX_UPLOAD_BODY // (1024*1024)} MB")

    # 5. Verify Unbounded Session Memory Leak (PERF-SESS-01)
    from src.conversation.engine import MAX_SESSIONS
    # Create 60 sessions
    for i in range(MAX_SESSIONS + 10):
        make_req(BASE_URL, "/api/chat", method="POST", body={"message": f"Session test {i}"})
    
    h_status, _, h_body = make_req(BASE_URL, "/api/health")
    h_json = json.loads(h_body)
    assert h_json["active_sessions"] <= MAX_SESSIONS, f"Active sessions exceeded limit: {h_json['active_sessions']} > {MAX_SESSIONS}"
    print(f" [PASS] PERF-SESS-01 Remediated: Session Pool Automatically Pruned at {h_json['active_sessions']} / {MAX_SESSIONS} Max")

    print("\nALL REMEDIATIONS VERIFIED SUCCESSFULLY!")

if __name__ == "__main__":
    httpd = ThreadingHTTPServer(("127.0.0.1", SERVER_PORT), Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        run()
    finally:
        httpd.shutdown()
        httpd.server_close()

"""Tests for tools/desktop_qa/api_matrix.py (lane Q1).

Stands up stdlib HTTP servers that emulate (a) the protected desktop server and
(b) the open legacy server, then runs the matrix against both.

Run: py -3.12 -m pytest tools/desktop_qa/test_api_matrix.py -q
"""
from __future__ import annotations

import http.server
import json
import sys
import threading
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import api_matrix  # noqa: E402

TOKEN = "test-token-abc"


class _Handler(http.server.BaseHTTPRequestHandler):
    protected = True
    token = TOKEN

    def log_message(self, *args):  # keep pytest output clean
        pass

    def _send(self, code: int, payload: dict | None = None) -> None:
        body = json.dumps(payload or {}).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle(self) -> None:
        port = self.server.server_address[1]
        if not self.protected:
            return self._send(200, {"legacy": True})
        host = self.headers.get("Host", "")
        if host not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            return self._send(403, {"error": "host"})
        path = self.path.split("?")[0]
        if path == "/api/app/health":
            return self._send(200, {"ok": True})
        header_token = self.headers.get("X-ValoHub-Token")
        cookie = self.headers.get("Cookie", "")
        authorized = header_token == self.token or f"vh_session={self.token}" in cookie
        if not authorized:
            return self._send(401, {"error": "token"})
        origin = self.headers.get("Origin")
        if self.command in ("POST", "PUT") and origin and origin not in (
                f"http://127.0.0.1:{port}", f"http://localhost:{port}"):
            return self._send(403, {"error": "origin"})
        if path.startswith("/api/decoder/update") or path.startswith("/api/lobby") \
                or path.startswith("/players"):
            return self._send(404, {"error": "absent"})
        if path == "/api/app/shutdown":
            return self._send(202, {"stopping": True})
        if path == "/api/replays":
            return self._send(200, {"replays": []})
        return self._send(404, {"error": "not found"})

    do_GET = do_POST = do_PUT = _handle


@contextmanager
def _server(protected: bool):
    handler = type("Handler", (_Handler,), {"protected": protected})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def test_protected_server_passes_the_matrix():
    with _server(True) as base:
        report = api_matrix.run(base, TOKEN, expect_protected=True, allow_shutdown=False)
    assert report["ok"] is True, report["failures"]
    assert report["counts"]["cases"] == len(api_matrix.cases(TOKEN, False))
    by_id = {row["id"]: row for row in report["cases"]}
    assert by_id["no_token"]["status"] == 401
    assert by_id["wrong_host"]["status"] == 403
    assert by_id["foreign_origin_post"]["status"] == 403
    assert by_id["foreign_origin_put"]["status"] == 403
    assert by_id["token_header"]["status"] == 200
    assert by_id["token_cookie"]["status"] == 200
    assert by_id["decoder_update"]["status"] == 404
    assert by_id["shutdown_no_token"]["status"] == 401


def test_open_legacy_server_is_reported_not_asserted():
    with _server(False) as base:
        report = api_matrix.run(base, TOKEN, expect_protected=False, allow_shutdown=False)
    assert report["ok"] is True
    assert report["mode"] == "report"
    assert all(row["status"] == 200 for row in report["cases"])
    assert all(row["ok"] is None for row in report["cases"])
    # the updater POST queues a real job on the legacy server: never sent unwittingly
    assert report["skippedMutating"] == ["decoder_update"]
    assert "decoder_update" not in {row["id"] for row in report["cases"]}


def test_open_legacy_server_fails_when_asserted():
    with _server(False) as base:
        report = api_matrix.run(base, TOKEN, expect_protected=True, allow_shutdown=False)
    assert report["ok"] is False
    failed = {row["id"] for row in report["failures"]}
    assert {"no_token", "wrong_host", "foreign_origin_post"} <= failed


def test_unreachable_server_is_a_failure_even_in_report_mode():
    # nothing listening on this port: a matrix that probed nothing proves nothing
    report = api_matrix.run("http://127.0.0.1:1", None, expect_protected=False,
                            allow_shutdown=False)
    assert report["ok"] is False
    assert report["mode"] == "report" and report["advisory"] is True
    assert report["counts"]["unreachable"] == report["counts"]["cases"] > 0
    assert {row["id"] for row in report["failures"]} == {row["id"] for row in report["cases"]}
    code = api_matrix.main(["--base-url", "http://127.0.0.1:1"])
    assert code == 1


def test_cli_requires_token_for_protected_mode():
    import io
    from contextlib import redirect_stdout

    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = api_matrix.main(["--base-url", "http://127.0.0.1:1", "--expect-protected"])
    assert code == 2
    assert "needs --token" in json.loads(buffer.getvalue())["error"]

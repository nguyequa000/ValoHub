#!/usr/bin/env python3
"""HTTP security matrix for the ValoHub desktop server (lane Q1).

Only ``http.client`` with explicit headers is used, so nothing (no proxy env,
no cookies jar, no redirect following) can soften the probe.

Cases (PLAN §7 A7):

* no token                      -> 401/403
* wrong ``Host``                -> 403
* foreign ``Origin`` on POST/PUT-> 403
* ``X-ValoHub-Token`` header  -> 200
* ``Cookie: vh_session=...``    -> 200
* ``/api/decoder/update``       -> blocked (403/404)
* ``/api/lobby*``               -> blocked (403/404)
* ``/players/x``                -> blocked (403/404)
* ``POST /api/app/shutdown``    -> needs the token (401/403 without it)

Without ``--expect-protected`` the matrix is advisory: it reports every status
code and exits 0 (useful against the legacy server as a baseline).  A case that
could not be probed at all (connection refused, timeout) is *always* a failure —
a matrix that reached nothing proves nothing — so both modes exit 1 then.  With
the flag, any case whose status is outside ``expect`` fails the run (exit 1).
The authorized shutdown is destructive and is only probed with
``--allow-shutdown``.

Stdlib only, Python 3.12.  Report schema: ``valohub.api_matrix/v1``.
"""
from __future__ import annotations

import argparse
import http.client
import json
import sys
from datetime import datetime, timezone
from urllib.parse import urlsplit

SCHEMA = "valohub.api_matrix/v1"
TOKEN_HEADER = "X-ValoHub-Token"
COOKIE_NAME = "vh_session"
PROBE_PATH = "C:/nonexistent/valohub-api-matrix-probe.vrf"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def cases(token: str | None, allow_shutdown: bool, include_mutating: bool = True) -> list[dict]:
    auth = {TOKEN_HEADER: token} if token else {}
    cookie = {"Cookie": f"{COOKIE_NAME}={token}"} if token else {}
    rows = [
        {"id": "no_token", "method": "GET", "path": "/api/replays",
         "headers": {}, "expect": [401, 403],
         "why": "a request without credentials must be rejected"},
        {"id": "wrong_host", "method": "GET", "path": "/api/replays",
         "headers": {**auth, "Host": "evil.example"},
         "expect": [403], "why": "Host allow-list (127.0.0.1:P / localhost:P)"},
        {"id": "wrong_host_public", "method": "GET", "path": "/api/app/health",
         "headers": {"Host": "evil.example"},
         "expect": [403], "why": "Host check applies to public health too"},
        {"id": "foreign_origin_post", "method": "POST", "path": "/api/replays/import-path",
         "headers": {**auth, "Origin": "http://evil.example"},
         "body": {"path": PROBE_PATH}, "expect": [403],
         "why": "cross-origin POST is refused before any file work"},
        {"id": "foreign_origin_put", "method": "PUT", "path": "/api/replays/upload",
         "headers": {**auth, "Origin": "http://evil.example", "Content-Type": "application/octet-stream"},
         "body": b"", "expect": [403],
         "why": "cross-origin PUT is refused"},
        {"id": "token_header", "method": "GET", "path": "/api/replays",
         "headers": auth, "expect": [200],
         "why": "primary auth header"},
        {"id": "token_cookie", "method": "GET", "path": "/api/replays",
         "headers": cookie, "expect": [200],
         "why": "cookie fallback used by the Electron shell"},
        {"id": "decoder_update", "method": "POST", "path": "/api/decoder/update",
         "headers": auth, "expect": [403, 404], "mutating": True,
         "why": "updater is disabled in the offline build"},
        {"id": "lobby_get", "method": "GET", "path": "/api/lobby",
         "headers": auth, "expect": [403, 404],
         "why": "lobby routes are not mounted offline"},
        {"id": "lobby_join", "method": "POST", "path": "/api/lobby/probe/join",
         "headers": auth, "body": {}, "expect": [403, 404],
         "why": "lobby routes are not mounted offline"},
        {"id": "players", "method": "GET", "path": "/players/probe",
         "headers": auth, "expect": [403, 404],
         "why": "legacy player pages are not mounted offline"},
        {"id": "shutdown_no_token", "method": "POST", "path": "/api/app/shutdown",
         "headers": {}, "expect": [401, 403],
         "why": "shutdown needs the token"},
        {"id": "shutdown_foreign_origin", "method": "POST", "path": "/api/app/shutdown",
         "headers": {**auth, "Origin": "http://evil.example"},
         "expect": [403], "why": "shutdown is same-origin only"},
    ]
    if allow_shutdown and token:
        rows.append({"id": "shutdown_authorized", "method": "POST", "path": "/api/app/shutdown",
                     "headers": auth, "expect": [200, 202, 204],
                     "why": "authorized graceful stop (opt-in, ends the server)"})
    if not include_mutating:
        rows = [row for row in rows if not row.get("mutating")]
    return rows


def call(base_url: str, token: str | None, case: dict, timeout: float = 10.0) -> dict:
    parsed = urlsplit(base_url)
    if parsed.scheme != "http":
        raise ValueError(f"--base-url must be http:// (got {parsed.scheme!r})")
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 80
    headers = {"Accept": "application/json", "Connection": "close"}
    headers.update({k: v for k, v in case["headers"].items() if v is not None})
    body = case.get("body")
    if isinstance(body, (dict, list)):
        body = json.dumps(body).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")
    elif isinstance(body, str):
        body = body.encode("utf-8")

    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        conn.request(case["method"], case["path"], body=body, headers=headers)
        response = conn.getresponse()
        raw = response.read(400)
        return {
            "status": response.status,
            "reason": response.reason,
            "contentType": response.getheader("Content-Type"),
            "setCookie": bool(response.getheader("Set-Cookie")),
            "bodyPreview": raw.decode("utf-8", "replace").strip()[:300],
        }
    finally:
        conn.close()


def run(base_url: str, token: str | None, expect_protected: bool, allow_shutdown: bool) -> dict:
    # A POST /api/decoder/update really queues an updater job on the legacy
    # server, so it is only probed when the server is expected to refuse it.
    include_mutating = bool(expect_protected)
    planned = cases(token, allow_shutdown, include_mutating=include_mutating)
    skipped = [case["id"] for case in cases(token, allow_shutdown) if case.get("mutating")] \
        if not include_mutating else []
    rows = []
    for case in planned:
        row = {k: case[k] for k in ("id", "method", "path", "why")}
        row["expect"] = case["expect"]
        try:
            result = call(base_url, token, case)
            row.update(result)
            row["ok"] = result["status"] in case["expect"] if expect_protected else None
        except (OSError, http.client.HTTPException, ValueError) as exc:
            # never probed => never a pass, in either mode
            row.update({"status": None, "error": f"{type(exc).__name__}: {exc}",
                        "ok": False})
        rows.append(row)

    failures = [row for row in rows if row.get("ok") is False]
    return {
        "schema": SCHEMA,
        "command": "matrix",
        "created": _now(),
        "baseUrl": base_url,
        "mode": "protected" if expect_protected else "report",
        "advisory": not expect_protected,
        "tokenPresent": bool(token),
        "ok": not failures,
        "counts": {"cases": len(rows), "failed": len(failures),
                   "unreachable": sum(1 for row in rows if row.get("error"))},
        "skippedMutating": skipped,
        "cases": rows,
        "failures": [{"id": row["id"], "expect": row["expect"],
                      "actual": row.get("status"), "error": row.get("error")}
                     for row in failures],
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="ValoHub HTTP security matrix (Q1)")
    parser.add_argument("--base-url", required=True, help="e.g. http://127.0.0.1:18792")
    parser.add_argument("--token", help="per-launch token (X-ValoHub-Token / vh_session)")
    parser.add_argument("--expect-protected", action="store_true",
                        help="assert every case; exit 1 on any mismatch")
    parser.add_argument("--allow-shutdown", action="store_true",
                        help="also probe the authorized shutdown (stops the server)")
    args = parser.parse_args(argv)
    if args.expect_protected and not args.token:
        print(json.dumps({"schema": SCHEMA, "ok": False,
                          "error": "--expect-protected needs --token"}, indent=2))
        return 2
    try:
        report = run(args.base_url, args.token, args.expect_protected, args.allow_shutdown)
    except ValueError as exc:
        print(json.dumps({"schema": SCHEMA, "ok": False, "error": str(exc)}, indent=2))
        return 2
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())

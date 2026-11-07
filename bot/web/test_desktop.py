"""Desktop server: token / Host / Origin matrix, headers, shutdown, ports, import hygiene."""
from __future__ import annotations

import http.client
import json
import os
import socket
import subprocess
import sys

import pytest

from bot.replay import store
from bot.web import desktop

BASE_PORT = 18811


def _free_port(start=BASE_PORT) -> int:
    for port in range(start, start + 9):
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    pytest.skip("no free test port in 18811-18819")


@pytest.fixture
def server(tmp_path, monkeypatch):
    data = tmp_path / "data" / "replays"
    data.mkdir(parents=True)
    work = tmp_path / "VALOPLANT_WORK"
    work.mkdir()
    (work / "parser-commit.txt").write_text("upstream_sha=testcommit\noverlay_patch_sha256=testoverlay\n")
    monkeypatch.setattr(store, "DATA_REPLAYS_DIR", data)
    monkeypatch.setattr(store, "ROOT", tmp_path)
    monkeypatch.setattr(store, "WORK_DIR", work)
    for key in ("HENRIKDEV_TOKEN", "VALO_LOBBY", "VALO_WATCHER", "VALO_OFFLINE"):
        monkeypatch.setenv(key, "x")
    srv = desktop.DesktopServer(token="t" * 32, preferred_port=_free_port())
    srv.start(timeout=20)
    yield srv
    srv.stop(5)


def req(srv, method, path, *, headers=None, host=None, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", srv.info.port, timeout=10)
    hdrs = dict(headers or {})
    conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
    conn.putheader("Host", host if host is not None else f"127.0.0.1:{srv.info.port}")
    for k, v in hdrs.items():
        conn.putheader(k, v)
    data = body if body is not None else (b"{}" if method in ("POST", "PUT") else None)
    if data is not None:
        conn.putheader("Content-Length", str(len(data)))
    conn.endheaders(data)
    resp = conn.getresponse()
    raw = resp.read()
    out = (resp.status, {k.lower(): v for k, v in resp.getheaders()}, raw)
    conn.close()
    return out


def tok(srv):
    return {desktop.HEADER: srv.token}


def test_token_matrix_with_negative_control(server):
    assert req(server, "GET", "/api/replays", headers=tok(server))[0] == 200
    status, _, body = req(server, "GET", "/api/replays")          # negative control: same request, no token
    assert status == 403 and set(json.loads(body)) >= {"error", "hint"}
    assert req(server, "GET", "/api/replays", headers={desktop.HEADER: "wrong" * 8})[0] == 403
    assert req(server, "GET", "/api/replays", headers={"Cookie": f"vh_session={server.token}"})[0] == 200
    assert req(server, "GET", "/api/replays", headers={"Cookie": "vh_session=nope"})[0] == 403


def test_health_is_the_only_tokenless_route(server):
    status, _, body = req(server, "GET", "/api/app/health")
    assert status == 200 and json.loads(body)["ok"] is True
    for path in ("/api/app/info", "/api/decoder", "/replays", "/static/replays_app/library.js", "/"):
        assert req(server, "GET", path)[0] == 403, path


def test_host_allowlist(server):
    port = server.info.port
    assert req(server, "GET", "/api/replays", headers=tok(server), host=f"localhost:{port}")[0] == 200
    for bad in ("evil.com", f"127.0.0.1:{port + 1}", f"evil.com:{port}", "127.0.0.1", ""):
        assert req(server, "GET", "/api/app/health", host=bad)[0] == 403, bad


def test_origin_rules(server):
    good = f"http://127.0.0.1:{server.info.port}"
    for method in ("POST", "PUT"):
        path = "/api/replays/import-path" if method == "POST" else "/api/replays/upload"
        assert req(server, method, path, headers={**tok(server), "Origin": "http://evil.com"})[0] == 403
        assert req(server, method, path, headers={**tok(server), "Origin": "null"})[0] == 403
        assert req(server, method, path, headers={**tok(server), "Origin": good})[0] != 403
        assert req(server, method, path, headers=tok(server))[0] != 403          # absent Origin ok
    # GET is not origin-restricted (browsers omit Origin on same-origin GET)
    assert req(server, "GET", "/api/replays", headers={**tok(server), "Origin": "http://evil.com"})[0] == 200


def test_websocket_upgrade_origin_and_eventsource_header(server):
    ws = {"Upgrade": "websocket", "Connection": "Upgrade", "Sec-WebSocket-Key": "x3JJHMbDL1EzLkh9GBhXDw==",
          "Sec-WebSocket-Version": "13"}
    assert req(server, "GET", "/api/jobs", headers={**tok(server), **ws, "Origin": "http://evil.com"})[0] == 403
    assert req(server, "GET", "/api/jobs", headers={**ws, "Origin": f"http://127.0.0.1:{server.info.port}"})[0] == 403  # no token
    assert req(server, "GET", "/api/jobs", headers={**tok(server), **ws})[0] != 403
    # EventSource: GET stream authenticated by the header; unauthenticated is refused
    conn = http.client.HTTPConnection("127.0.0.1", server.info.port, timeout=10)
    conn.request("GET", "/api/jobs/stream", headers={desktop.HEADER: server.token, "Accept": "text/event-stream"})
    resp = conn.getresponse()
    assert resp.status == 200 and resp.getheader("Content-Type", "").startswith("text/event-stream")
    assert resp.getheader("Content-Security-Policy") == desktop.CSP     # headers also on prepared streams
    conn.close()
    assert req(server, "GET", "/api/jobs/stream")[0] == 403


def test_cookie_bootstrap_and_redirect(server):
    status, headers, _ = req(server, "GET", f"/?t={server.token}")
    assert status == 302 and headers["location"] == "/replays"
    cookie = headers["set-cookie"]
    assert cookie.startswith(f"vh_session={server.token}")
    low = cookie.lower()
    assert "httponly" in low and "samesite=strict" in low and "path=/" in low
    assert req(server, "GET", "/?t=wrong")[0] == 403                    # negative control
    status, headers, _ = req(server, "GET", "/", headers=tok(server))
    assert status == 302 and headers["location"] == "/replays"


def test_security_headers(server):
    # v2 policy (DESIGN-V2 §7): nothing inline for scripts, nothing remote, no framing, no plugins
    assert desktop.CSP == ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                           "img-src 'self' data: blob:; connect-src 'self'; font-src 'self'; frame-ancestors 'none'; "
                           "base-uri 'none'; form-action 'none'; object-src 'none'")
    script_src = [d for d in desktop.CSP.split("; ") if d.startswith("script-src")][0]
    assert script_src == "script-src 'self'" and "unsafe-eval" not in desktop.CSP
    _, headers, _ = req(server, "GET", "/api/app/info", headers=tok(server))
    assert headers["content-security-policy"] == desktop.CSP
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["referrer-policy"] == "no-referrer"
    assert headers["cache-control"] == "no-store"
    _, headers403, _ = req(server, "GET", "/api/replays")
    assert headers403["content-security-policy"] == desktop.CSP         # also on rejections
    _, page, _ = req(server, "GET", "/replays", headers=tok(server))
    assert page["content-security-policy"] == desktop.CSP
    # every SPA route is a page behind the token and carries the same policy
    for path in ("/replay/x", "/analysis/x", "/coach/x", "/providers", "/settings"):
        status, spa, _ = req(server, "GET", path, headers=tok(server))
        assert status in (200, 404) and spa["content-security-policy"] == desktop.CSP, path


def test_info_and_env_forcing(server, monkeypatch):
    status, _, body = req(server, "GET", "/api/app/info", headers=tok(server))
    info = json.loads(body)
    assert status == 200 and info["offline"] is True and info["updaterEnabled"] is False and info["mode"] == "desktop"
    assert info["buildCommit"] is None  # dev/in-process run: launcher did not pass a commit
    monkeypatch.setenv("VALOHUB_BUILD_COMMIT", "20f51c7343effa")
    _, _, body = req(server, "GET", "/api/app/info", headers=tok(server))
    assert json.loads(body)["buildCommit"] == "20f51c7343effa"  # packaged launcher value
    assert os.environ["VALO_OFFLINE"] == "1"
    for key in desktop.DROPPED_ENV:
        assert key not in os.environ


def test_features_exclude_lobby_and_legacy_routes_absent(server):
    _, _, body = req(server, "GET", "/api/app/info", headers=tok(server))
    features = json.loads(body)["features"]
    assert "p2_lobby" not in features and "p3_parser_admin" in features and "p2_review" in features
    for path in ("/players/a-b", "/matches/a-b", "/static/exports/x", "/api/lobby", "/api/lobby/x/join"):
        assert req(server, "GET", path, headers=tok(server))[0] == 404, path


def test_decoder_updater_disabled(server):
    status, _, body = req(server, "GET", "/api/decoder", headers=tok(server))
    assert status == 200 and json.loads(body)["updaterEnabled"] is False
    status, _, body = req(server, "POST", "/api/decoder/update", headers=tok(server))
    assert status == 403 and json.loads(body)["error"] == "disabled in the offline desktop build"


def test_legacy_mount_keeps_updater_enabled(tmp_path, monkeypatch):
    import asyncio
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    from bot.replay import api, jobs, p2, p3_parser_admin
    monkeypatch.setattr(store, "WORK_DIR", tmp_path)
    monkeypatch.setattr(store, "ROOT", tmp_path)

    async def go():
        app = web.Application()
        app["jobs"] = jobs.JobManager()
        api.setup_routes(app)
        p2.mount(app, features=("p3_parser_admin",))
        async with TestClient(TestServer(app)) as client:
            data = await (await client.get("/api/decoder")).json()
            return data.get("updaterEnabled", True)
    assert asyncio.run(go()) is True


def test_shutdown_endpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA_REPLAYS_DIR", tmp_path)
    srv = desktop.DesktopServer(token="s" * 32, preferred_port=_free_port())
    srv.start(timeout=20)
    assert req(srv, "POST", "/api/app/shutdown")[0] == 403                  # negative control: no token
    assert req(srv, "POST", "/api/app/shutdown", headers={**tok(srv), "Origin": "http://evil.com"})[0] == 403
    assert srv.is_running
    status, _, _ = req(srv, "POST", "/api/app/shutdown", headers=tok(srv))
    assert status == 202
    assert srv.wait_stopped(10)


def test_port_fallback_and_loopback_only(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA_REPLAYS_DIR", tmp_path)
    port = _free_port()
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", port))
    blocker.listen(1)
    srv = desktop.DesktopServer(token="f" * 32, preferred_port=port)
    try:
        info = srv.start(timeout=20)
        assert info.fell_back is True and info.port != port
        assert req(srv, "GET", "/api/app/health")[0] == 200
        assert req(srv, "GET", "/api/app/health", host=f"127.0.0.1:{port}")[0] == 403   # old port is not allowed
        addresses = list(srv._runner.addresses)
        assert addresses and all(a[0] == "127.0.0.1" for a in addresses), addresses
    finally:
        assert srv.stop(10) is True
        blocker.close()


def test_refuses_wildcard_bind(tmp_path):
    for host in ("0.0.0.0", "::", "192.168.1.5"):
        with pytest.raises(desktop.ServerStartError):
            desktop.DesktopServer(token="w" * 32, preferred_port=_free_port(), host=host).start(timeout=10)


def test_stop_returns_with_running_job(server):
    import asyncio
    gate = asyncio.Event()

    async def hang(job, emit):
        await asyncio.sleep(60)

    job = server.app["jobs"].new_job("test", "x")
    server._loop.call_soon_threadsafe(server.app["jobs"].enqueue, job, hang)
    import time
    t0 = time.time()
    while server.active_jobs() == 0 and time.time() - t0 < 5:
        time.sleep(0.05)
    assert server.active_jobs() == 1
    t0 = time.time()
    server.stop(8)
    assert time.time() - t0 < 9
    assert not server.is_running


def test_import_hygiene():
    code = ("import sys, bot.web.desktop; "
            "print(int('bot.web.app' in sys.modules), int('bot.services.cache' in sys.modules))")
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(store.ROOT))
    assert result.stdout.split() == ["0", "0"], result.stderr

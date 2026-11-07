"""AI provider routes on the real desktop server (token + host + origin middleware) with a stub provider.

    py -3.12 -m pytest bot/web/test_ai_routes.py -q
"""
from __future__ import annotations

import http.client
import json
import logging
import sys

import pytest

from bot.replay import ai_providers as ap
from bot.replay import store
from bot.replay.ai_test_support import GOOD_KEY, LEAK_MARKER, Stub, make_vault, stub_registry
from bot.web import csp as web_csp
from bot.web import desktop
from bot.web.test_desktop import _free_port, req, tok

BAD_KEY = "sk-stub-BAD-0000000000000000"
SHELL = "<!doctype html><title>shell</title><div id=space>APP-SHELL-MARKER</div>"
ROUTES = [("GET", "/api/ai/providers"), ("POST", "/api/ai/providers/openai/key"),
          ("POST", "/api/ai/providers/openrouter/oauth/start"), ("GET", "/api/ai/oauth/callback"),
          ("DELETE", "/api/ai/providers/openai"), ("GET", "/api/ai/providers/openai/models"),
          ("POST", "/api/ai/providers/openai/test"), ("POST", "/api/ai/chat")]


@pytest.fixture
def stub():
    s = Stub().start()
    yield s
    s.stop()


@pytest.fixture
def ctx(tmp_path, monkeypatch, stub):
    data = tmp_path / "data" / "replays"
    data.mkdir(parents=True)
    work = tmp_path / "VALOPLANT_WORK"
    work.mkdir()
    static = tmp_path / "static"
    static.mkdir()
    (static / "app.html").write_text(SHELL, encoding="utf-8")
    monkeypatch.setattr(store, "DATA_REPLAYS_DIR", data)
    monkeypatch.setattr(store, "ROOT", tmp_path)
    monkeypatch.setattr(store, "WORK_DIR", work)
    monkeypatch.setattr(store, "STATIC_REPLAYS_DIR", static)
    for key in ("HENRIKDEV_TOKEN", "VALO_LOBBY", "VALO_WATCHER", "VALO_OFFLINE"):
        monkeypatch.setenv(key, "x")
    monkeypatch.delenv("VALO_LLM_URL", raising=False)
    monkeypatch.setenv("VALO_LLM_KEY_FILE", str(tmp_path / "no-descriptor.json"))
    monkeypatch.setenv("VALO_LMSTUDIO_URL", "http://127.0.0.1:1/v1")          # nothing listens -> local unreachable
    monkeypatch.setenv("VALO_OLLAMA_URL", "http://127.0.0.1:1")
    srv = desktop.DesktopServer(token="t" * 32, preferred_port=_free_port())
    srv.start(timeout=20)
    service = srv.app["ai_service"]
    root = tmp_path / "aidata"
    service._root_fn = lambda: root                                           # noqa: SLF001
    service.registry = stub_registry(stub)
    service._vault = make_vault(root / "secrets" / "providers.bin")           # noqa: SLF001
    opened: list[str] = []
    service.opener = lambda url: opened.append(url) or True
    srv.app["ai_oauth"].opener = service.opener
    stub.token_codes = {"good-code": "ok"}
    yield type("Ctx", (), {"srv": srv, "stub": stub, "root": root, "opened": opened, "service": service,
                           "bodies": []})()
    srv.stop(5)


def call(c, method, path, body=None, *, headers=None, auth=True):
    hdrs = dict(tok(c.srv) if auth else {})
    hdrs.update(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        hdrs["Content-Type"] = "application/json"
    status, resp_headers, raw = req(c.srv, method, path, headers=hdrs, body=data if data is not None else (b"" if method in ("POST", "PUT") else None))
    c.bodies.append(raw)
    try:
        return status, json.loads(raw) if raw else None
    except ValueError:
        return status, raw.decode("utf-8", "replace")


def connect(c, pid="openai", key=GOOD_KEY, **extra):
    return call(c, "POST", f"/api/ai/providers/{pid}/key", {"key": key, "consent": True, **extra})


# ------------------------------------------------------------------ auth / transport rules

def test_endpoints_exist_and_need_the_session_token(ctx):
    for method, path in ROUTES:
        status, _ = call(ctx, method, path, auth=False)
        assert status == 403, (method, path)                            # token middleware owns auth
        status, body = call(ctx, method, path, {} if method == "POST" else None)
        assert status != 404 or (isinstance(body, dict) and "code" in body), (method, path)   # route is mounted
    assert ctx.stub.requests == []


def test_cross_origin_writes_are_rejected_before_any_work(ctx):
    for origin in ("http://evil.com", "null"):
        status, _ = call(ctx, "POST", "/api/ai/providers/openai/key", {"key": GOOD_KEY, "consent": True},
                         headers={"Origin": origin})
        assert status == 403
        assert call(ctx, "DELETE", "/api/ai/providers/openai", headers={"Origin": origin})[0] == 403
    assert ctx.stub.requests == [] and not (ctx.root / "secrets" / "providers.bin").exists()
    own = f"http://127.0.0.1:{ctx.srv.info.port}"
    assert call(ctx, "POST", "/api/ai/providers/openai/key", {"key": GOOD_KEY, "consent": True},
                headers={"Origin": own})[0] == 200


def test_a_key_in_the_query_string_is_refused_everywhere(ctx):
    for path in (f"/api/ai/providers/openai/key?key={GOOD_KEY}", f"/api/ai/providers?api_key={GOOD_KEY}",
                 f"/api/ai/providers/openai/models?token={GOOD_KEY}", "/api/ai/oauth/callback?code=abc&state=x"):
        method = "POST" if path.endswith("key?key=" + GOOD_KEY) else "GET"
        status, body = call(ctx, method, path, {"key": GOOD_KEY, "consent": True} if method == "POST" else None)
        assert status == 400 and body["code"] == "secret_in_query", path
    assert ctx.stub.requests == []
    assert not (ctx.root / "secrets").exists()


def test_bad_bodies(ctx):
    assert call(ctx, "POST", "/api/ai/providers/openai/key", ["not", "an", "object"])[1]["detail"] == "body_not_object"
    status, _, raw = req(ctx.srv, "POST", "/api/ai/providers/openai/key", headers=tok(ctx.srv), body=b"{nope")
    assert status == 400 and json.loads(raw)["detail"] == "body_not_json"
    assert call(ctx, "POST", "/api/ai/providers/openai/key", {"key": 12345678})[0] == 400
    assert call(ctx, "POST", "/api/ai/providers/nope/key", {"key": GOOD_KEY})[1]["code"] == "unknown_provider"
    assert call(ctx, "POST", "/api/ai/chat", {"provider": "openai", "messages": "x"})[1]["code"] == "invalid_request"


# ------------------------------------------------------------------ provider flows

def test_provider_list_shape_and_defaults(ctx):
    status, body = call(ctx, "GET", "/api/ai/providers")
    assert status == 200 and body["offline_default"] is True and body["default"] is None
    rows = {p["id"]: p for p in body["providers"]}
    assert list(rows) == ["local", "openrouter", "openai", "anthropic", "gemini", "custom"]
    assert rows["local"]["kind"] == "local" and rows["local"]["state"] == "unreachable"
    assert {e["id"] for e in rows["local"]["endpoints"]} >= {"lmstudio", "ollama"}
    assert rows["openrouter"]["oauth_supported"] is True and rows["openai"]["oauth_supported"] is False
    for pid in ("openrouter", "openai", "anthropic", "gemini"):
        assert rows[pid]["kind"] == "cloud" and rows[pid]["configured"] is False and rows[pid]["consented"] is False
        assert rows[pid]["state"] == "not_connected" and rows[pid]["console_url"].startswith("https://")
        assert "base_url" not in rows[pid] and "secret" not in json.dumps(rows[pid]).lower()
    assert ctx.stub.requests == []                                     # listing a cloud row never touches the network


def test_consent_required_then_verify_then_save(ctx):
    status, body = call(ctx, "POST", "/api/ai/providers/openai/key", {"key": GOOD_KEY})
    assert status == 428 and body["code"] == "consent_required" and ctx.stub.requests == []
    status, body = connect(ctx)
    assert status == 200 and body["configured"] is True and body["last4"] == GOOD_KEY[-4:]
    assert body["state"] == "connected" and len(body["models"]) == 2 and GOOD_KEY not in json.dumps(body)
    assert (ctx.root / "secrets" / "providers.bin").is_file()
    status, listing = call(ctx, "GET", "/api/ai/providers?probe=0")
    row = next(p for p in listing["providers"] if p["id"] == "openai")
    assert row["configured"] and row["last4"] == GOOD_KEY[-4:] and row["credential"] == "api_key" and row["consented"]


@pytest.mark.parametrize("upstream, http_status, code", [(401, 422, "invalid_key"), (403, 422, "forbidden"),
                                                         (429, 429, "rate_limited"), (503, 502, "unreachable")])
def test_failures_map_to_codes_and_nothing_is_saved(ctx, upstream, http_status, code):
    ctx.stub.force["/models"] = upstream
    status, body = connect(ctx)
    assert status == http_status and body["code"] == code and body["status"] == upstream
    assert body["request_id"] == "req-forced-1" and set(body) >= {"code", "status", "request_id", "message"}
    assert LEAK_MARKER not in json.dumps(body)
    assert not (ctx.root / "secrets" / "providers.bin").exists()
    row = next(p for p in call(ctx, "GET", "/api/ai/providers?probe=0")[1]["providers"] if p["id"] == "openai")
    assert row["configured"] is False and row["state"] == code


def test_real_invalid_key_and_unreachable_host(ctx):
    status, body = connect(ctx, key=BAD_KEY)
    assert (status, body["code"], body["status"]) == (422, "invalid_key", 401)
    from dataclasses import replace
    ctx.service.registry["openai"] = replace(ctx.service.registry["openai"], base_url="http://127.0.0.1:1/v1")
    status, body = connect(ctx)
    assert (status, body["code"], body["status"]) == (502, "unreachable", None)


def test_models_default_test_chat_and_stream(ctx):
    connect(ctx)
    status, body = call(ctx, "GET", "/api/ai/providers/openai/models")
    assert status == 200 and body["cached"] is True and [m["id"] for m in body["models"]] == ["stub-model-1", "stub-model-2"]
    status, body = call(ctx, "GET", "/api/ai/providers/openai/models?refresh=1")
    assert status == 200 and body["cached"] is False
    assert call(ctx, "PUT", "/api/ai/default", {"provider": "anthropic", "model": "x"})[1]["code"] == "not_configured"
    status, body = call(ctx, "PUT", "/api/ai/default", {"provider": "openai", "model": "stub-model-2"})
    assert status == 200 and body["default"] == {"provider": "openai", "model": "stub-model-2"}
    assert call(ctx, "GET", "/api/ai/providers?probe=0")[1]["default"]["provider"] == "openai"
    status, body = call(ctx, "POST", "/api/ai/providers/openai/test", {})
    assert status == 200 and body["ok"] is True and body["model"] == "stub-model-2"
    status, body = call(ctx, "POST", "/api/ai/providers/openai/test", {"model": "gone-model"})
    assert (status, body["code"], body["status"]) == (422, "model_gone", 404)
    status, body = call(ctx, "POST", "/api/ai/chat", {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 50})
    assert status == 200 and body["content"] == "stub answer" and body["model"] == "stub-model-2"
    assert ctx.stub.hits("/chat/completions")[-1]["body"]["max_completion_tokens"] == 50

    conn = http.client.HTTPConnection("127.0.0.1", ctx.srv.info.port, timeout=10)
    payload = json.dumps({"messages": [{"role": "user", "content": "hi"}], "stream": True}).encode()
    conn.request("POST", "/api/ai/chat", body=payload, headers={**tok(ctx.srv), "Content-Type": "application/json"})
    response = conn.getresponse()
    text = response.read().decode()
    assert response.status == 200 and response.getheader("Content-Type").startswith("text/event-stream")
    assert response.getheader("Content-Security-Policy") == web_csp.CSP
    assert text.startswith("event: open") and "data: [DONE]" in text and "PROCESSING" not in text
    assert text.count('data: {"choices"') == 2
    conn.close()
    ctx.bodies.append(text.encode())


def test_stream_error_before_first_byte_is_json(ctx):
    connect(ctx)
    ctx.stub.force["/chat/completions"] = 429
    status, body = call(ctx, "POST", "/api/ai/chat", {"provider": "openai", "model": "m", "stream": True,
                                                     "messages": [{"role": "user", "content": "hi"}]})
    assert status == 429 and body["code"] == "rate_limited" and LEAK_MARKER not in json.dumps(body)


def test_disconnect_deletes_everything(ctx):
    connect(ctx)
    call(ctx, "PUT", "/api/ai/default", {"provider": "openai", "model": "stub-model-1"})
    status, body = call(ctx, "DELETE", "/api/ai/providers/openai")
    assert status == 200 and body["deleted"] is True and body["revoked"] is None
    assert not (ctx.root / "secrets" / "providers.bin").exists()
    listing = call(ctx, "GET", "/api/ai/providers?probe=0")[1]
    row = next(p for p in listing["providers"] if p["id"] == "openai")
    assert listing["default"] is None and row["configured"] is False and row["consented"] is False and row["models"] == []
    status, body = call(ctx, "POST", "/api/ai/chat", {"provider": "openai", "model": "m",
                                                     "messages": [{"role": "user", "content": "hi"}]})
    assert status == 428 and body["code"] == "consent_required"
    assert call(ctx, "DELETE", "/api/ai/providers/local")[0] == 400


def test_chat_without_default_or_consent(ctx):
    status, body = call(ctx, "POST", "/api/ai/chat", {"messages": [{"role": "user", "content": "hi"}]})
    assert status == 409 and body["code"] == "not_configured"
    status, body = call(ctx, "POST", "/api/ai/chat", {"provider": "anthropic", "model": "m",
                                                     "messages": [{"role": "user", "content": "hi"}]})
    assert status == 428 and ctx.stub.requests == []


def test_local_chat_uses_loopback_endpoint(ctx, monkeypatch):
    ctx.stub.open_access = True
    ctx.service._environ = {"VALO_LMSTUDIO_URL": f"{ctx.stub.base}/v1", "VALO_OLLAMA_URL": "https://ollama.com",   # noqa: SLF001
                            "LOCALAPPDATA": str(ctx.root / "none")}
    status, body = call(ctx, "POST", "/api/ai/chat", {"provider": "local", "messages": [{"role": "user", "content": "hi"}]})
    assert status == 200 and body["endpoint"] == "lmstudio" and body["content"] == "stub answer"
    status, body = call(ctx, "GET", "/api/ai/providers/local/models")
    assert status == 200 and {e["id"]: e.get("blocked") for e in body["endpoints"]}["ollama"] == "cloud_host"


def test_custom_provider_rejects_public_urls(ctx):
    status, body = call(ctx, "POST", "/api/ai/providers/custom/key", {"base_url": "https://api.openai.com/v1"})
    assert status == 400 and body["code"] == "invalid_request"
    ctx.stub.open_access = True
    status, body = call(ctx, "POST", "/api/ai/providers/custom/key", {"base_url": f"{ctx.stub.base}/v1"})
    assert status == 200 and body["configured"] and body["credential"] is None


def test_open_console_uses_the_catalog_url_only(ctx):
    status, body = call(ctx, "POST", "/api/ai/providers/openai/open-console", {"url": "https://evil.example"})
    assert status == 200 and body["opened"] is True and ctx.opened == ["https://platform.openai.com/api-keys"]
    assert call(ctx, "POST", "/api/ai/providers/custom/open-console", {})[1]["detail"] == "no_console"


# ------------------------------------------------------------------ oauth

def test_oauth_only_for_openrouter(ctx):
    for pid in ("openai", "anthropic", "gemini", "custom", "local"):
        status, body = call(ctx, "POST", f"/api/ai/providers/{pid}/oauth/start", {"consent": True})
        assert status == 400 and body["code"] == "oauth_unsupported", pid
    assert ctx.opened == []
    status, body = call(ctx, "POST", "/api/ai/providers/openrouter/oauth/start", {})
    assert status == 428 and body["code"] == "consent_required"


def test_oauth_flow_over_http_status_poll_and_state_check(ctx):
    import urllib.request
    from urllib.parse import parse_qs, urlsplit
    status, started = call(ctx, "POST", "/api/ai/providers/openrouter/oauth/start", {"consent": True})
    assert status == 200 and ctx.opened == [started["authorize_url"]] and started["browser_opened"] is True
    q = parse_qs(urlsplit(started["authorize_url"]).query)
    callback = q["callback_url"][0]
    assert urlsplit(callback).hostname == "127.0.0.1" and q["code_challenge_method"] == ["S256"]
    assert call(ctx, "GET", f"/api/ai/oauth/callback?state={started['state']}")[1]["status"] == "pending"
    assert call(ctx, "GET", "/api/ai/oauth/callback")[1]["code"] == "flow_not_found"
    bad = urllib.request.Request(f"{callback}?code=good-code&state=forged")
    with pytest.raises(urllib.error.HTTPError) as info:
        urllib.request.urlopen(bad, timeout=5)
    assert info.value.code == 400 and ctx.stub.exchange_bodies == []
    with urllib.request.urlopen(f"{callback}?code=good-code&state={started['state']}", timeout=5) as ok:
        assert ok.status == 200
    status, body = call(ctx, "GET", f"/api/ai/oauth/callback?state={started['state']}")
    assert status == 200 and body == {"provider": "openrouter", "status": "connected"}
    row = next(p for p in call(ctx, "GET", "/api/ai/providers?probe=0")[1]["providers"] if p["id"] == "openrouter")
    assert row["configured"] is True and row["credential"] == "oauth"
    secret = ctx.service.vault.get("openrouter")["secret"]
    assert all(secret.encode() not in body for body in ctx.bodies)
    status, body = call(ctx, "DELETE", "/api/ai/providers/openrouter")
    assert status == 200 and body["deleted"] is True and body["revoke_supported"] is False


# ------------------------------------------------------------------ secrets / leakage

def test_secret_and_upstream_body_never_appear_in_any_response_or_log(ctx, caplog):
    caplog.set_level(logging.DEBUG)
    connect(ctx, key=BAD_KEY)
    ctx.stub.force["/models"] = 403
    connect(ctx)
    ctx.stub.force.clear()
    connect(ctx)
    ctx.stub.force["/chat/completions"] = 500
    call(ctx, "POST", "/api/ai/chat", {"provider": "openai", "model": "m", "messages": [{"role": "user", "content": "x"}]})
    call(ctx, "POST", "/api/ai/providers/openai/test", {"model": "gone-model"})
    call(ctx, "GET", "/api/ai/providers")
    call(ctx, "GET", "/api/ai/providers/openai/models?refresh=1")
    blob = b"\n".join(ctx.bodies)
    for needle in (GOOD_KEY, BAD_KEY, LEAK_MARKER):
        assert needle.encode() not in blob, needle
    logs = "\n".join(r.getMessage() for r in caplog.records)
    for needle in (GOOD_KEY, BAD_KEY, LEAK_MARKER):
        assert needle not in logs, needle
    on_disk = (ctx.root / "secrets" / "providers.bin").read_bytes() + (ctx.root / "ai_providers.json").read_bytes()
    assert GOOD_KEY.encode() not in on_disk


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")
def test_real_dpapi_file_written_by_the_route(ctx):
    from bot.replay.ai_secrets import Vault
    service = ctx.service
    service._vault = None                                                 # noqa: SLF001 - use the real default vault
    assert connect(ctx)[0] == 200
    assert isinstance(service.vault, Vault) and service.vault.get("openai")["secret"] == GOOD_KEY
    assert GOOD_KEY.encode() not in (ctx.root / "secrets" / "providers.bin").read_bytes()


# ------------------------------------------------------------------ CSP + SPA routes + redaction

EXPECTED_CSP = ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
                "connect-src 'self'; font-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'; "
                "object-src 'none'")


def test_csp_is_exact_on_pages_api_and_rejections(ctx):
    assert desktop.CSP == web_csp.CSP == EXPECTED_CSP
    for path, headers in (("/providers", tok(ctx.srv)), ("/api/ai/providers", tok(ctx.srv)), ("/replays", tok(ctx.srv)),
                          ("/api/ai/providers", {}), ("/api/app/health", {})):
        status, h, _ = req(ctx.srv, "GET", path, headers=headers)
        assert h["content-security-policy"] == EXPECTED_CSP, path
        assert h["x-content-type-options"] == "nosniff" and h["referrer-policy"] == "no-referrer"
    csp = EXPECTED_CSP
    assert "unsafe-eval" not in csp and "script-src 'self';" in csp and "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0]


@pytest.mark.parametrize("path", ["/replays", "/replays/", "/replay/abc123", "/analysis/abc123", "/coach/abc123",
                                  "/providers", "/settings"])
def test_spa_routes_serve_the_shell(ctx, path):
    status, headers, raw = req(ctx.srv, "GET", path, headers=tok(ctx.srv))
    assert status == 200 and b"APP-SHELL-MARKER" in raw and headers["content-type"].startswith("text/html")
    assert headers["cache-control"] == "no-store"
    assert req(ctx.srv, "GET", path)[0] == 403                            # still behind the session token


def test_old_view_url_redirects_to_the_workspace(ctx):
    status, headers, _ = req(ctx.srv, "GET", "/replays/abc123/view", headers=tok(ctx.srv))
    assert status == 302 and headers["location"] == "/replay/abc123"
    status, headers, _ = req(ctx.srv, "GET", "/replays/a%2Fb%3F/view", headers=tok(ctx.srv))
    assert status == 302 and "/" not in headers["location"][len("/replay/"):] and "?" not in headers["location"]


def test_shell_falls_back_to_a_clear_placeholder_when_not_built(ctx, tmp_path):
    (store.STATIC_REPLAYS_DIR / "app.html").unlink()
    status, _, raw = req(ctx.srv, "GET", "/providers", headers=tok(ctx.srv))
    assert status == 200 and b"app.html" in raw


def test_malformed_chat_options_are_a_safe_400(ctx):
    status, body = call(ctx, "POST", "/api/ai/chat", {"messages": [{"role": "user", "content": "x"}], "max_tokens": "NaN"})
    assert status == 400 and body["code"] == "invalid_request" and GOOD_KEY not in json.dumps(body)


def test_unexpected_server_errors_are_redacted_by_the_desktop_error_middleware():
    import asyncio
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    async def go():
        app = desktop.create_desktop_app("t" * 32, 0)

        async def boom(request):
            raise RuntimeError(f"upstream said Authorization: Bearer {GOOD_KEY} and api_key={BAD_KEY}")
        app.router.add_get("/api/boom", boom)
        async with TestClient(TestServer(app)) as client:
            app["allowed_hosts"].add(f"127.0.0.1:{client.port}")
            response = await client.get("/api/boom", headers={"X-ValoHub-Token": "t" * 32})
            return response.status, await response.text()
    status, text = asyncio.run(go())
    assert status == 500 and GOOD_KEY not in text and BAD_KEY not in text and "[REDACTED]" in text


# ------------------------------------------------------------------ the normal (token-less) app

def test_normal_app_mounts_routes_with_loopback_guard(tmp_path, stub):
    import asyncio
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    from bot.web import ai_routes

    async def go():
        app = web.Application()
        ai_routes.mount(app, data_root=tmp_path, registry=stub_registry(stub), vault=make_vault(tmp_path / "s" / "p.bin"),
                        environ={"LOCALAPPDATA": str(tmp_path)})
        async with TestClient(TestServer(app)) as client:
            port = client.port
            ok = await client.get("/api/ai/providers?probe=0")
            body = await ok.json()
            evil_origin = await client.post("/api/ai/providers/openai/key", json={"key": GOOD_KEY, "consent": True},
                                            headers={"Origin": "http://evil.com"})
            evil_host = await client.get("/api/ai/providers", headers={"Host": "evil.com"})
            same = await client.post("/api/ai/providers/openai/key", json={"key": GOOD_KEY, "consent": True},
                                     headers={"Origin": f"http://127.0.0.1:{port}"})
            return ok.status, [p["id"] for p in body["providers"]], evil_origin.status, evil_host.status, same.status
    status, ids, evil_origin, evil_host, same = asyncio.run(go())
    assert status == 200 and ids[:2] == ["local", "openrouter"] and evil_origin == 403 and evil_host == 403 and same == 200


def test_legacy_create_app_mounts_ai_routes_and_scoped_csp():
    from bot.web import app as legacy
    application = legacy.create_app()
    paths = {r.resource.canonical for r in application.router.routes() if r.resource is not None}
    assert "/api/ai/providers" in paths and "/api/ai/chat" in paths and "/providers" in paths and "/settings" in paths
    assert web_csp.wants_csp("/providers") and web_csp.wants_csp("/api/ai/chat") and web_csp.wants_csp("/replay/x")
    assert not web_csp.wants_csp("/") and not web_csp.wants_csp("/players/a-b")

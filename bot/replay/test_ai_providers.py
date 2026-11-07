"""AI provider registry/client against a local stub provider (no network, no real keys).

    py -3.12 -m pytest bot/replay/test_ai_providers.py -q
"""
from __future__ import annotations

import asyncio
import json
import logging
import socket

import pytest

from bot.replay import ai_providers as ap
from bot.replay import redact
from bot.replay.ai_test_support import GOOD_KEY, LEAK_MARKER, Stub, make_service

BAD_KEY = "sk-stub-BAD-0000000000000000"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def stub():
    s = Stub().start()
    yield s
    s.stop()


@pytest.fixture
def svc(tmp_path, stub):
    return make_service(tmp_path, stub)


def connect(svc, pid, key=GOOD_KEY, **kw):
    kw.setdefault("consent", True)
    return run(svc.connect_key(pid, key, **kw))


def closed_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


# ------------------------------------------------------------------ registry shape

def test_registry_shape():
    reg = ap.REGISTRY
    assert list(reg) == ["local", "openrouter", "openai", "anthropic", "gemini", "custom"]
    assert reg["local"].kind == "local" and reg["custom"].kind == "custom"
    assert reg["openrouter"].auth == "oauth_pkce" and reg["openrouter"].auth_modes == ("oauth_pkce", "api_key")
    assert reg["openrouter"].base_url == "https://openrouter.ai/api/v1" and reg["openrouter"].verify_path == "/key"
    assert reg["openrouter"].oauth["token_path"] == "/auth/keys"
    assert reg["openai"].base_url == "https://api.openai.com/v1" and reg["openai"].auth == "api_key"
    assert reg["anthropic"].base_url == "https://api.anthropic.com/v1"
    assert dict(reg["anthropic"].extra_headers)["anthropic-version"]
    assert reg["gemini"].base_url == "https://generativelanguage.googleapis.com/v1beta/openai"
    assert reg["gemini"].verify_url == "https://generativelanguage.googleapis.com/v1beta/models"
    for pid in ("openai", "anthropic", "gemini", "custom", "local"):
        assert reg[pid].oauth is None, pid                      # OpenRouter is the only OAuth provider
    for provider in reg.values():                               # compiled catalog is https-only, no test escape hatch
        assert provider.allow_http_loopback is False
        if provider.kind == "cloud":
            assert provider.base_url.startswith("https://") and provider.chat_path == "/chat/completions"


def test_ollama_com_is_cloud_not_local():
    assert ap.classify_endpoint("http://127.0.0.1:11434/v1") == "local"
    assert ap.classify_endpoint("http://localhost:1234/v1") == "local"
    assert ap.classify_endpoint("https://ollama.com/v1") == "cloud"
    assert ap.classify_endpoint("https://ollama.com.evil.example/v1") == "cloud"


def test_local_listing_branches_on_host(tmp_path, stub):
    stub.models = [{"id": "lm-model"}]
    stub.open_access = True
    env = {"LOCALAPPDATA": str(tmp_path / "none"), "VALO_LMSTUDIO_URL": f"{stub.base}/v1",
           "VALO_OLLAMA_URL": "https://ollama.com"}
    svc = make_service(tmp_path, stub, environ=env)
    view = run(svc.describe("local"))
    by_id = {e["id"]: e for e in view["endpoints"]}
    assert by_id["lmstudio"]["kind"] == "local" and by_id["lmstudio"]["reachable"] is True
    assert by_id["ollama"]["kind"] == "cloud" and by_id["ollama"]["blocked"] == "cloud_host"
    assert by_id["ollama"]["reachable"] is None                   # never contacted
    assert [m["id"] for m in view["models"]] == ["lm-model"] and view["state"] == "connected"
    assert view["models"][0]["endpoint"] == "lmstudio"


def test_custom_base_restricted_to_loopback_and_private():
    for good in ("http://127.0.0.1:8080/v1", "http://localhost:1234/v1", "http://192.168.1.20:8000/v1",
                 "http://10.0.0.5/v1", "http://172.16.4.2:9/v1", "http://[::1]:8000/v1"):
        assert ap.validate_custom_base(good) == good
    for bad in ("https://api.openai.com/v1", "http://8.8.8.8/v1", "http://example.com/v1", "ftp://127.0.0.1/",
                "http://user:pw@127.0.0.1/v1", "http://169.254.169.254/v1", "http://127.0.0.1/v1?key=1", "",
                "http://0.0.0.0/v1"):
        with pytest.raises(ap.ProviderError) as info:
            ap.validate_custom_base(bad)
        assert info.value.code == "invalid_request", bad


# ------------------------------------------------------------------ verify-then-save

def test_connect_saves_only_after_verification_and_hides_the_key(svc, stub, tmp_path):
    view = connect(svc, "openai")
    assert view["configured"] is True and view["last4"] == GOOD_KEY[-4:] and view["state"] == "connected"
    assert [m["id"] for m in view["models"]] == ["stub-model-1", "stub-model-2"]
    assert view["models"][1]["context"] == 8000
    assert GOOD_KEY not in json.dumps(view)
    assert svc.vault.get("openai")["secret"] == GOOD_KEY
    on_disk = (tmp_path / "secrets" / "providers.bin").read_bytes()
    settings = (tmp_path / "ai_providers.json").read_text()
    assert GOOD_KEY.encode() not in on_disk and GOOD_KEY not in settings
    assert stub.hits("/models")[0]["headers"]["authorization"] == f"Bearer {GOOD_KEY}"


@pytest.mark.parametrize("status, code", [(401, "invalid_key"), (403, "forbidden"), (429, "rate_limited"),
                                          (503, "unreachable"), (500, "upstream_error")])
def test_failed_verification_saves_nothing_and_never_echoes_the_body(svc, stub, status, code):
    stub.force["/models"] = status
    with pytest.raises(ap.ProviderError) as info:
        connect(svc, "openai")
    err = info.value
    assert (err.code, err.status, err.request_id) == (code, status, "req-forced-1")
    payload = json.dumps(err.payload())
    assert LEAK_MARKER not in payload and GOOD_KEY not in payload
    assert svc.vault.get("openai") is None
    view = run(svc.describe("openai", probe=False))
    assert view["configured"] is False and view["state"] == code


def test_invalid_key_401(svc):
    with pytest.raises(ap.ProviderError) as info:
        connect(svc, "openai", BAD_KEY)
    assert info.value.code == "invalid_key" and info.value.status == 401 and info.value.request_id == "req-401"
    assert svc.vault.get("openai") is None


def test_unreachable_when_nothing_listens(tmp_path, stub):
    svc = make_service(tmp_path, stub)
    from dataclasses import replace
    svc.registry["openai"] = replace(svc.registry["openai"], base_url=f"http://127.0.0.1:{closed_port()}/v1")
    with pytest.raises(ap.ProviderError) as info:
        connect(svc, "openai")
    assert info.value.code == "unreachable" and info.value.status is None


def test_model_gone_from_the_one_token_chat_test(svc, stub):
    with pytest.raises(ap.ProviderError) as info:
        connect(svc, "openai", test_chat=True, model="gone-model")
    assert info.value.code == "model_gone" and info.value.status == 404
    assert LEAK_MARKER not in json.dumps(info.value.payload())
    assert svc.vault.get("openai") is None
    view = connect(svc, "openai", test_chat=True, model="stub-model-1")           # and the happy path
    assert view["state"] == "connected"
    chat_call = stub.hits("/chat/completions")[-1]["body"]
    assert chat_call["max_completion_tokens"] == 1 and chat_call["temperature"] == 0     # openai param name


def test_consent_required_before_any_cloud_traffic(svc, stub):
    with pytest.raises(ap.ProviderError) as info:
        connect(svc, "openai", consent=False)
    assert info.value.code == "consent_required"
    assert stub.requests == []                                          # not a single byte left the process
    connect(svc, "openai", consent=True)
    record = svc.settings.load()["consents"]["openai"]
    assert set(record) == {"provider", "consented_at", "policy_version", "hosts"} and record["hosts"] == ["127.0.0.1"]
    run(svc.disconnect("openai"))
    assert "openai" not in svc.settings.load()["consents"]            # disconnect also drops consent
    with pytest.raises(ap.ProviderError) as info:
        run(svc.chat([{"role": "user", "content": "hi"}], provider_id="openai", model="m"))
    assert info.value.code == "consent_required"


def test_key_format_and_unknown_provider(svc):
    for bad in ("short", "has space in it 12345678", "new\nline-12345678", "x" * 600):
        with pytest.raises(ap.ProviderError) as info:
            connect(svc, "openai", bad)
        assert info.value.code == "invalid_request"
    with pytest.raises(ap.ProviderError) as info:
        connect(svc, "nope")
    assert info.value.code == "unknown_provider"
    with pytest.raises(ap.ProviderError) as info:
        connect(svc, "local")
    assert info.value.code == "no_key_required"


def test_openrouter_uses_the_key_endpoint_as_proof(svc, stub):
    with pytest.raises(ap.ProviderError) as info:
        connect(svc, "openrouter", BAD_KEY)
    assert info.value.code == "invalid_key"
    assert stub.hits("/key") and not stub.hits("/models")              # public /models never gets to vouch
    view = connect(svc, "openrouter")
    assert view["state"] == "connected" and view["oauth_supported"] is True and view["credential"] == "api_key"


def test_gemini_filters_generate_content_and_keeps_the_key_out_of_the_url(svc, stub):
    view = connect(svc, "gemini")
    assert [m["id"] for m in view["models"]] == ["gemini-chat"]
    call = stub.hits("/v1beta/models")[0]
    assert call["headers"]["x-goog-api-key"] == GOOD_KEY and "key" not in call["query"]


def test_anthropic_sends_version_header(svc, stub):
    connect(svc, "anthropic")
    call = stub.hits("/models")[0]["headers"]
    assert call["anthropic-version"] == ap.ANTHROPIC_VERSION and call["x-api-key"] == GOOD_KEY


def test_no_redirects_are_followed_and_no_credential_travels(tmp_path, stub):
    other = Stub().start()
    try:
        stub.redirect_to = f"{other.base}/v1/models"
        svc = make_service(tmp_path, stub)
        with pytest.raises(ap.ProviderError) as info:
            connect(svc, "openai")
        assert info.value.code == "upstream_error" and info.value.detail == "redirect_blocked"
        assert other.requests == []
    finally:
        other.stop()


def test_egress_is_pinned_to_the_catalog_host():
    provider = ap.REGISTRY["openai"]
    http = ap._Http(provider)
    for url in ("https://evil.example/v1/models", "http://api.openai.com/v1/models", "https://api.openai.com:8443/v1",
                "https://user:pw@api.openai.com/v1", "https://api.openai.com.evil.example/v1"):
        with pytest.raises(ap.ProviderError) as info:
            http.check(url)
        assert info.value.code == "invalid_request", url
    assert http.check("https://api.openai.com/v1/models")


def test_session_never_trusts_the_environment_or_redirects(monkeypatch):
    http = ap._Http(ap.REGISTRY["openai"])
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")

    async def go():
        session = http.session(5)
        try:
            return session.trust_env
        finally:
            await session.close()
    assert run(go()) is False


# ------------------------------------------------------------------ models / default / disconnect

def test_models_cached_then_refreshed_and_error_keeps_cache(svc, stub):
    connect(svc, "openai")
    n = len(stub.requests)
    cached = run(svc.models("openai"))
    assert cached["cached"] is True and len(stub.requests) == n
    stub.models = [{"id": "fresh-model"}]
    assert [m["id"] for m in run(svc.models("openai", refresh=True))["models"]] == ["fresh-model"]
    stub.force["/models"] = 429
    with pytest.raises(ap.ProviderError) as info:
        run(svc.models("openai", refresh=True))
    assert info.value.code == "rate_limited" and info.value.extra["models"][0]["id"] == "fresh-model"
    assert run(svc.describe("openai", probe=False))["state"] == "rate_limited"


def test_default_selection_and_chat_uses_it(svc, stub):
    with pytest.raises(ap.ProviderError) as info:
        svc.set_default("openai", "stub-model-1")
    assert info.value.code == "not_configured"
    connect(svc, "openai")
    svc.set_default("openai", "stub-model-2")
    listing = run(svc.list_providers(probe=False))
    assert listing["default"] == {"provider": "openai", "model": "stub-model-2"}
    assert [p["id"] for p in listing["providers"] if p["default"]] == ["openai"]
    out = run(svc.chat([{"role": "user", "content": "hi"}]))
    assert out["content"] == "stub answer" and out["model"] == "stub-model-2" and out["provider"] == "openai"
    assert out["usage"]["total_tokens"] == 5
    assert stub.hits("/chat/completions")[-1]["body"]["model"] == "stub-model-2"


def test_chat_validation_and_upstream_mapping(svc, stub):
    connect(svc, "openai")
    for bad in (None, [], "x", [{"role": "tool", "content": "x"}], [{"role": "user", "content": ""}],
                [{"role": "user", "content": "a" * (ap.MAX_CHARS + 1)}]):
        with pytest.raises(ap.ProviderError) as info:
            run(svc.chat(bad, provider_id="openai", model="stub-model-1"))
        assert info.value.code == "invalid_request"
    stub.force["/chat/completions"] = 429
    with pytest.raises(ap.ProviderError) as info:
        run(svc.chat([{"role": "user", "content": "hi"}], provider_id="openai", model="stub-model-1"))
    assert (info.value.code, info.value.status) == ("rate_limited", 429)
    assert LEAK_MARKER not in json.dumps(info.value.payload())


def test_stream_passthrough_skips_comments(svc, stub):
    connect(svc, "openrouter")

    async def go():
        events = []
        async with svc.chat_stream([{"role": "user", "content": "hi"}], provider_id="openrouter",
                                   model="stub-model-1") as handle:
            async for payload in handle:
                events.append(payload)
            return events, handle.meta
    events, meta = run(go())
    assert events[-1] == "[DONE]" and len(events) == 3 and "Hel" in events[0]
    assert all("PROCESSING" not in e for e in events) and meta["provider"] == "openrouter"
    assert stub.hits("/chat/completions")[-1]["body"]["stream"] is True


def test_stream_open_error_is_structured(svc, stub):
    connect(svc, "openai")
    stub.force["/chat/completions"] = 401

    async def go():
        async with svc.chat_stream([{"role": "user", "content": "hi"}], provider_id="openai", model="m"):
            pass
    with pytest.raises(ap.ProviderError) as info:
        run(go())
    assert info.value.code == "invalid_key" and LEAK_MARKER not in json.dumps(info.value.payload())


def test_disconnect_deletes_secret_models_default_and_consent(svc, stub, tmp_path):
    connect(svc, "openai")
    svc.set_default("openai", "stub-model-1")
    out = run(svc.disconnect("openai"))
    assert out["deleted"] is True and out["revoked"] is None and out["revoke_supported"] is False
    assert svc.vault.get("openai") is None and svc.get_default() is None
    view = run(svc.describe("openai", probe=False))
    assert view["configured"] is False and view["models"] == [] and view["state"] == "not_connected"
    assert not (tmp_path / "secrets" / "providers.bin").exists()      # last entry gone -> file gone
    with pytest.raises(ap.ProviderError):
        run(svc.disconnect("local"))


def test_rejected_oauth_credential_is_purged_and_asks_for_reauth(svc, stub):
    svc.grant_consent(svc.provider("openrouter"))
    svc.vault.put("openrouter", "oauth", "sk-or-v1-revoked-0000000000")
    with pytest.raises(ap.ProviderError) as info:
        run(svc.chat([{"role": "user", "content": "hi"}], provider_id="openrouter", model="m"))
    assert info.value.code == "reauth_required"
    assert svc.vault.get("openrouter") is None


def test_tampered_vault_entry_means_reauth(svc, tmp_path):
    connect(svc, "openai")
    path = tmp_path / "secrets" / "providers.bin"
    data = json.loads(path.read_text())
    blob = data["entries"]["openai"]
    data["entries"]["openai"] = blob[:-8] + ("AAAAAAAA" if not blob.endswith("AAAAAAAA") else "BBBBBBBB")
    path.write_text(json.dumps(data))
    view = run(svc.describe("openai", probe=False))
    assert view["state"] == "reauth_required" and view["configured"] is False
    assert svc.vault.ids() == []


def test_custom_provider_lan_url_optional_key_no_oauth(tmp_path, stub):
    svc = make_service(tmp_path, stub)
    stub.open_access = True                                          # an open server: no key needed
    stub.open_access = True
    view = run(svc.connect_key("custom", None, base_url=f"{stub.base}/v1"))
    assert view["configured"] is True and view["base_url"] == f"{stub.base}/v1" and view["state"] == "connected"
    assert view["consented"] is True                                 # loopback custom needs no cloud consent
    with pytest.raises(ap.ProviderError) as info:
        run(svc.connect_key("custom", None, base_url="https://api.openai.com/v1"))
    assert info.value.code == "invalid_request"
    out = run(svc.chat([{"role": "user", "content": "hi"}], provider_id="custom", model="stub-model-1"))
    assert out["content"] == "stub answer"


def test_local_chat_through_a_loopback_endpoint(tmp_path, stub):
    env = {"LOCALAPPDATA": str(tmp_path / "none"), "VALO_LMSTUDIO_URL": f"{stub.base}/v1",
           "VALO_OLLAMA_URL": f"http://127.0.0.1:{closed_port()}"}
    stub.open_access = True
    svc = make_service(tmp_path, stub, environ=env)
    out = run(svc.chat([{"role": "user", "content": "hi"}], provider_id="local"))
    assert out["content"] == "stub answer" and out["endpoint"] == "lmstudio" and out["model"] == "stub-model-1"
    assert run(svc.test("local"))["ok"] is True
    # nothing configured/listening -> structured error, still no body
    dead = make_service(tmp_path, stub, environ={"LOCALAPPDATA": str(tmp_path / "none"),
                                                 "VALO_LMSTUDIO_URL": f"http://127.0.0.1:{closed_port()}/v1",
                                                 "VALO_OLLAMA_URL": f"http://127.0.0.1:{closed_port()}"})
    with pytest.raises(ap.ProviderError) as info:
        run(dead.chat([{"role": "user", "content": "hi"}], provider_id="local"))
    assert info.value.code == "no_local_model"


# ------------------------------------------------------------------ redaction at the sink

def test_redact_patterns():
    samples = ["Authorization: Bearer abcdef123456", "x-api-key: sk-live-ABCDEFGHIJKLMNOP", 'api_key="zzzzzzzzzz"',
               "GET /v1?key=AIzaSyA1234567890123456789012345&x=1", "token=abc.def.ghi", "sk-or-v1-0123456789abcdef0123"]
    for text in samples:
        out = redact.redact(text)
        assert "abcdef123456" not in out and "ABCDEFGHIJKLMNOP" not in out and "zzzzzzzzzz" not in out
        assert "AIzaSyA1234567890123456789012345" not in out and "abc.def.ghi" not in out
        assert "0123456789abcdef0123" not in out and redact.MASK in out, text
    assert redact.redact("token estimate: 120 tokens") == "token estimate: 120 tokens"
    redact.register_secret("super-secret-literal-xyz")
    assert "super-secret-literal-xyz" not in redact.redact("boom super-secret-literal-xyz boom")


def test_log_sink_masks_secrets(svc, stub, caplog):
    caplog.set_level(logging.DEBUG)
    logging.getLogger("bot.replay.ai_providers").info("calling with Authorization: Bearer %s", GOOD_KEY)
    connect(svc, "openai")
    logging.getLogger("bot.replay.ai_providers").info("key was %s", GOOD_KEY)         # registered literal
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert GOOD_KEY not in text and "[REDACTED]" in text
    stub.force["/models"] = 401
    with pytest.raises(ap.ProviderError):
        run(svc.models("openai", refresh=True))
    assert LEAK_MARKER not in "\n".join(r.getMessage() for r in caplog.records)

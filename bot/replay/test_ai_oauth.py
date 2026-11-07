"""OpenRouter PKCE flow against a stub provider: loopback listener, state check, single use, expiry.

    py -3.12 -m pytest bot/replay/test_ai_oauth.py -q
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import socket
from urllib.parse import parse_qs, urlsplit

import aiohttp
import pytest

from bot.replay import ai_oauth, ai_providers as ap
from bot.replay.ai_test_support import LEAK_MARKER, Stub, make_service


@pytest.fixture
def stub():
    s = Stub().start()
    s.token_codes = {"good-code": "ok"}
    yield s
    s.stop()


async def hit(url: str, expect_refused: bool = False):
    try:
        async with aiohttp.ClientSession(trust_env=False) as session:
            async with session.get(url, allow_redirects=False) as response:
                return response.status, await response.text()
    except aiohttp.ClientConnectorError:
        if expect_refused:
            return None, ""
        raise


def callback_of(authorize_url: str) -> str:
    return parse_qs(urlsplit(authorize_url).query)["callback_url"][0]


async def wait_closed(url: str, timeout: float = 3.0) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        status, _ = await hit(url, expect_refused=True)
        if status is None:
            return True
        await asyncio.sleep(0.05)
    return False


def test_pkce_pair_is_s256():
    verifier, challenge = ai_oauth.pkce_pair()
    assert 43 <= len(verifier) <= 128
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert challenge == expected and "=" not in challenge
    assert ai_oauth.pkce_pair()[0] != verifier


def test_full_flow_bind_before_browser_state_exchange_and_single_use(tmp_path, stub):
    seen = {}

    def opener(url):
        cb = callback_of(url)
        host, port = urlsplit(cb).hostname, urlsplit(cb).port
        with socket.create_connection((host, port), timeout=2):      # listener must already be bound
            seen["bound_before_open"] = True
        seen["url"] = url
        return True

    async def go():
        svc = make_service(tmp_path, stub)
        mgr = ai_oauth.OAuthManager(svc, opener=opener)
        started = await mgr.start("openrouter", consent=True)
        url = started["authorize_url"]
        q = parse_qs(urlsplit(url).query)
        assert url.startswith("https://openrouter.ai/auth?")
        assert q["code_challenge_method"] == ["S256"] and len(q["code_challenge"][0]) == 43
        assert q["state"] == [started["state"]] and len(started["state"]) >= 43      # 256-bit
        cb = urlsplit(q["callback_url"][0])
        assert cb.scheme == "http" and cb.hostname == "127.0.0.1" and cb.path == "/callback" and cb.port
        assert started["browser_opened"] is True and started["expires_in"] == 300
        assert "code_verifier" not in url and "client_secret" not in url
        assert mgr.status(started["state"]) == {"provider": "openrouter", "status": "pending"}

        # wrong state: rejected, nothing exchanged, flow still alive
        status, _ = await hit(f"{q['callback_url'][0]}?code=good-code&state=WRONG")
        assert status == 400 and stub.exchange_bodies == []
        assert mgr.status(started["state"])["status"] == "pending"

        # right state: exchanged server-side with the verifier that matches the challenge
        status, page = await hit(f"{q['callback_url'][0]}?code=good-code&state={started['state']}")
        assert status == 200 and "good-code" not in page and "sk-or" not in page
        body = stub.exchange_bodies[0]
        assert body["code"] == "good-code" and body["code_challenge_method"] == "S256"
        digest = base64.urlsafe_b64encode(hashlib.sha256(body["code_verifier"].encode()).digest()).rstrip(b"=").decode()
        assert digest == q["code_challenge"][0]
        assert mgr.status(started["state"])["status"] == "connected"
        stored = svc.vault.get("openrouter")
        assert stored["kind"] == "oauth" and stored["secret"].startswith("sk-or-v1-")
        assert svc.vault.describe("openrouter")["kind"] == "oauth"
        assert stored["secret"] not in json.dumps(mgr.status(started["state"]))

        # single use: the listener goes away, a replay is refused
        assert await wait_closed(q["callback_url"][0])
        await mgr.close()
        return started
    started = asyncio.run(go())
    assert seen["bound_before_open"] is True and started["state"]


def test_replayed_callback_while_listener_up_is_gone(tmp_path, stub):
    async def go():
        svc = make_service(tmp_path, stub)
        mgr = ai_oauth.OAuthManager(svc, opener=lambda url: True)
        started = await mgr.start("openrouter", consent=True)
        cb = callback_of(started["authorize_url"])
        flow = mgr.flows[started["state"]]
        assert (await hit(f"{cb}?code=good-code&state={started['state']}"))[0] == 200
        flow.runner  # listener may still be draining; a second use must never exchange again
        status, _ = await hit(f"{cb}?code=good-code&state={started['state']}", expect_refused=True)
        assert status in (None, 410)
        assert len(stub.exchange_bodies) == 1
        await mgr.close()
    asyncio.run(go())


def test_three_wrong_states_abort_the_flow(tmp_path, stub):
    async def go():
        svc = make_service(tmp_path, stub)
        mgr = ai_oauth.OAuthManager(svc, opener=lambda url: True)
        started = await mgr.start("openrouter", consent=True)
        cb = callback_of(started["authorize_url"])
        for _ in range(3):
            await hit(f"{cb}?code=good-code&state=nope", expect_refused=True)
        assert mgr.status(started["state"]) == {"provider": "openrouter", "status": "error", "code": "state_mismatch"}
        assert await wait_closed(cb)
        assert stub.exchange_bodies == [] and svc.vault.get("openrouter") is None
        await mgr.close()
    asyncio.run(go())


def test_denied_and_bad_exchange_store_nothing(tmp_path, stub):
    async def go():
        svc = make_service(tmp_path, stub)
        mgr = ai_oauth.OAuthManager(svc, opener=lambda url: True)
        first = await mgr.start("openrouter", consent=True)
        cb = callback_of(first["authorize_url"])
        assert (await hit(f"{cb}?error=access_denied&state={first['state']}"))[0] == 200
        assert mgr.status(first["state"])["code"] == "access_denied" and stub.exchange_bodies == []
        second = await mgr.start("openrouter")                     # consent already recorded
        cb2 = callback_of(second["authorize_url"])
        status, page = await hit(f"{cb2}?code=forged&state={second['state']}")
        assert status == 502 and LEAK_MARKER not in page
        assert mgr.status(second["state"]) == {"provider": "openrouter", "status": "error", "code": "upstream_error"}
        assert svc.vault.get("openrouter") is None
        await mgr.close()
    asyncio.run(go())


def test_flow_expires(tmp_path, stub):
    async def go():
        svc = make_service(tmp_path, stub)
        mgr = ai_oauth.OAuthManager(svc, opener=lambda url: True, timeout_s=0.3)
        started = await mgr.start("openrouter", consent=True)
        cb = callback_of(started["authorize_url"])
        await asyncio.sleep(0.8)
        assert mgr.status(started["state"])["status"] == "expired"
        status, _ = await hit(f"{cb}?code=good-code&state={started['state']}", expect_refused=True)
        assert status is None and stub.exchange_bodies == []
        await mgr.close()
    asyncio.run(go())


def test_new_flow_supersedes_old_and_unknown_state(tmp_path, stub):
    async def go():
        svc = make_service(tmp_path, stub)
        mgr = ai_oauth.OAuthManager(svc, opener=lambda url: True)
        a = await mgr.start("openrouter", consent=True)
        b = await mgr.start("openrouter")
        assert a["state"] != b["state"] and callback_of(a["authorize_url"]) != callback_of(b["authorize_url"])
        assert mgr.status(a["state"])["code"] == "superseded" and mgr.status(b["state"])["status"] == "pending"
        with pytest.raises(ap.ProviderError) as info:
            mgr.status("never-issued")
        assert info.value.code == "flow_not_found"
        await mgr.close()
    asyncio.run(go())


def test_only_openrouter_has_oauth_and_consent_is_required(tmp_path, stub):
    async def go():
        svc = make_service(tmp_path, stub)
        mgr = ai_oauth.OAuthManager(svc, opener=lambda url: True)
        for pid in ("openai", "anthropic", "gemini", "custom", "local"):
            with pytest.raises(ap.ProviderError) as info:
                await mgr.start(pid, consent=True)
            assert info.value.code == "oauth_unsupported", pid
        with pytest.raises(ap.ProviderError) as info:
            await mgr.start("openrouter")                          # no consent record yet
        assert info.value.code == "consent_required" and mgr.flows == {}
        with pytest.raises(ap.ProviderError) as info:
            await mgr.start("bogus")
        assert info.value.code == "unknown_provider"
    asyncio.run(go())


def test_disconnect_revokes_when_supported_then_deletes(tmp_path, stub):
    async def go():
        svc = make_service(tmp_path, stub)
        svc.vault.put("openrouter", "oauth", "sk-or-v1-TOKEN000000000000")
        mgr = ai_oauth.OAuthManager(svc, opener=lambda url: True)
        out = await svc.disconnect("openrouter", revoker=mgr.revoke)
        assert out["deleted"] is True and out["revoke_supported"] is False and out["revoked"] is None
        assert svc.vault.get("openrouter") is None
        # a provider that documents a revoke endpoint: best-effort call, then delete either way
        from dataclasses import replace
        oauth = dict(svc.registry["openrouter"].oauth, revoke_path="/auth/revoke")
        svc.registry["openrouter"] = replace(svc.registry["openrouter"], oauth=oauth)
        svc.vault.put("openrouter", "oauth", "sk-or-v1-TOKEN111111111111")
        stub.accepted.add("sk-or-v1-TOKEN111111111111")
        out = await svc.disconnect("openrouter", revoker=mgr.revoke)
        assert out["revoke_supported"] is True and out["deleted"] is True
        assert any(r["path"].endswith("/auth/revoke") for r in stub.requests)
        svc.vault.put("openrouter", "oauth", "sk-or-v1-TOKEN222222222222")
        stub.force["/auth/revoke"] = 500                             # revoke fails -> still deleted
        out = await svc.disconnect("openrouter", revoker=mgr.revoke)
        assert out["revoked"] is False and out["deleted"] is True and svc.vault.get("openrouter") is None
    asyncio.run(go())

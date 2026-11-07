"""Test support: a local stub LLM provider (thread-hosted aiohttp) + registry/vault helpers.

Not a test module (no ``test_`` prefix).  Every response body that the stub sends on an error contains
``LEAK_MARKER`` so tests can prove upstream bodies never reach callers or logs.
"""
from __future__ import annotations

import asyncio
import json
import secrets
import sys
import threading
from dataclasses import replace
from pathlib import Path

from aiohttp import web

from bot.replay import ai_providers as ap
from bot.replay.ai_secrets import Vault

LEAK_MARKER = "UPSTREAM-BODY-LEAK-9f3a"
GOOD_KEY = "sk-stub-GOOD-1234567890abcdef"


class Stub:
    """OpenAI-shaped provider.  ``accepted`` = keys that authenticate; everything is recorded."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.accepted: set[str] = {GOOD_KEY}
        self.open_access = False
        self.force: dict[str, int] = {}          # path suffix -> status to answer with
        self.models = [{"id": "stub-model-1"}, {"id": "stub-model-2", "context_length": 8000}]
        self.token_codes: dict[str, str] = {}    # oauth code -> expected code_verifier challenge-free check hook
        self.exchange_bodies: list[dict] = []
        self.redirect_to: str | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.port = 0
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._runner: web.AppRunner | None = None

    # -- lifecycle
    def start(self) -> "Stub":
        self._thread = threading.Thread(target=self._run, daemon=True, name="stub-provider")
        self._thread.start()
        assert self._ready.wait(10), "stub provider did not start"
        return self

    def _run(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", self._handle)
        self._runner = web.AppRunner(app, access_log=None)
        self.loop.run_until_complete(self._runner.setup())
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        self.loop.run_until_complete(site.start())
        self.port = site._server.sockets[0].getsockname()[1]  # noqa: SLF001
        self._ready.set()
        self.loop.run_forever()
        self.loop.run_until_complete(self._runner.cleanup())
        self.loop.close()

    def stop(self) -> None:
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)
        if self._thread:
            self._thread.join(5)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def hits(self, suffix: str) -> list[dict]:
        return [r for r in self.requests if r["path"].endswith(suffix)]

    # -- behaviour
    def _authed(self, request: web.Request) -> bool:
        if self.open_access:
            return True
        auth = request.headers.get("Authorization", "")
        candidates = {auth[7:] if auth.lower().startswith("bearer ") else "",
                      request.headers.get("x-api-key", ""), request.headers.get("x-goog-api-key", "")}
        return bool(candidates & self.accepted)

    async def _handle(self, request: web.Request) -> web.StreamResponse:
        raw = await request.read()
        try:
            body = json.loads(raw) if raw else None
        except ValueError:
            body = None
        path = "/" + request.match_info["tail"]
        self.requests.append({"method": request.method, "path": path, "query": dict(request.query),
                              "headers": {k.lower(): v for k, v in request.headers.items()}, "body": body})
        for suffix, status in self.force.items():
            if path.endswith(suffix):
                return web.json_response({"error": {"message": f"forced {status} {LEAK_MARKER}"}}, status=status,
                                         headers={"x-request-id": "req-forced-1"})
        if self.redirect_to and path.endswith("/models"):
            return web.Response(status=302, headers={"Location": self.redirect_to})
        if path.endswith("/auth/keys") and request.method == "POST":
            self.exchange_bodies.append(body or {})
            if (body or {}).get("code") not in self.token_codes:
                return web.json_response({"error": f"bad code {LEAK_MARKER}"}, status=400)
            return web.json_response({"key": "sk-or-v1-" + secrets.token_hex(16), "user_id": "u1"})
        if not self._authed(request):
            return web.json_response({"error": {"message": f"invalid api key {LEAK_MARKER}"}}, status=401,
                                     headers={"x-request-id": "req-401"})
        if path.endswith("/key"):
            return web.json_response({"data": {"label": "stub", "limit_remaining": 5}})
        if path.startswith("/v1beta/models"):
            return web.json_response({"models": [
                {"name": "models/gemini-chat", "supportedGenerationMethods": ["generateContent"], "inputTokenLimit": 32000},
                {"name": "models/gemini-embed", "supportedGenerationMethods": ["embedContent"]}]})
        if path.endswith("/models"):
            return web.json_response({"object": "list", "data": self.models})
        if path.endswith("/chat/completions") and request.method == "POST":
            if (body or {}).get("model") == "gone-model":
                return web.json_response({"error": {"message": f"The model `gone-model` does not exist {LEAK_MARKER}"}},
                                         status=404)
            if (body or {}).get("stream"):
                response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
                await response.prepare(request)
                for chunk in (": OPENROUTER PROCESSING\n\n",
                              'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n',
                              'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n', "data: [DONE]\n\n"):
                    await response.write(chunk.encode())
                return response
            return web.json_response({"choices": [{"message": {"role": "assistant", "content": "stub answer"}}],
                                      "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}},
                                     headers={"x-request-id": "req-chat-1"})
        return web.json_response({"error": "not found"}, status=404)


def stub_registry(stub: Stub) -> dict:
    """The compiled registry with every cloud provider re-aimed at the stub (loopback http allowed)."""
    out = {}
    for pid, provider in ap.REGISTRY.items():
        if provider.kind == "cloud":
            base = f"{stub.base}/v1beta/openai" if pid == "gemini" else f"{stub.base}/v1"
            provider = replace(provider, base_url=base, allow_http_loopback=True,
                               verify_url=f"{stub.base}/v1beta/models" if provider.verify_url else None)
        out[pid] = provider
    return out


def make_vault(path: Path, **kwargs) -> Vault:
    """Real DPAPI on Windows; reversible fake elsewhere so the rest of the suite still runs."""
    if sys.platform == "win32":
        return Vault(path, **kwargs)

    def protect(data: bytes, entropy: bytes) -> bytes:
        return bytes(b ^ 0x5A for b in entropy[:1] + data)

    def unprotect(blob: bytes, entropy: bytes) -> bytes:
        raw = bytes(b ^ 0x5A for b in blob)
        if raw[:1] != entropy[:1]:
            raise ValueError("entropy")
        return raw[1:]
    return Vault(path, protect=protect, unprotect=unprotect, harden_acl=False)


def make_service(tmp: Path, stub: Stub, *, environ: dict | None = None, opener=None) -> ap.AiService:
    return ap.AiService(tmp, registry=stub_registry(stub), vault=make_vault(tmp / "secrets" / "providers.bin"),
                        environ=environ if environ is not None else {"LOCALAPPDATA": str(tmp / "nolocal")},
                        opener=opener)

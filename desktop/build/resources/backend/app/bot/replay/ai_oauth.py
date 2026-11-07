"""OpenRouter OAuth PKCE for the desktop sidecar (RFC 8252 loopback flow; no other provider has OAuth).

Flow: ``start`` binds an ephemeral ``127.0.0.1`` listener *before* the system browser is opened
(``os.startfile``), builds the authorize URL (S256 challenge, 256-bit single-use ``state``), and returns
``{authorize_url, state, expires_in}``.  The listener accepts exactly one valid callback
(``/callback?code=&state=``), compares ``state`` in constant time, exchanges the code **server-side**
at the catalog token endpoint, stores the resulting key sealed (DPAPI) and shuts down.  A wrong
``state`` is rejected (400, nothing exchanged); three wrong guesses abort the flow.  The listener also
dies after 5 minutes.  The renderer polls ``status(state)``; it never sees the code or the key.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import os
import secrets
import socket
import sys
import time
import webbrowser
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urlencode

from aiohttp import web

from . import redact as _redact
from .ai_providers import AiService, Provider, ProviderError, _Http

LOG = logging.getLogger("bot.replay.ai_oauth")

FLOW_TIMEOUT_S = 300.0
MAX_STATE_MISMATCHES = 3
MAX_FLOWS = 8

_DONE_PAGE = ("<!doctype html><meta charset=utf-8><title>ValoHub</title>"
              "<body style='font:16px Segoe UI,sans-serif;background:#0B0F16;color:#E7ECF5;padding:40px'>"
              "<h2>{title}</h2><p>You can close this tab and return to ValoHub.</p></body>")


def pkce_pair() -> tuple[str, str]:
    """``(code_verifier, S256 code_challenge)`` - 43..128 char verifier per RFC 7636."""
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    return verifier, challenge


def system_open(url: str) -> bool:
    """Open ``url`` in the default browser (``os.startfile`` on Windows)."""
    try:
        if sys.platform == "win32":
            os.startfile(url)  # noqa: S606 - URL comes from the compiled catalog + our own query
        else:
            return bool(webbrowser.open(url))
        return True
    except OSError:
        return False


@dataclass
class Flow:
    provider_id: str
    state: str
    verifier: str
    port: int
    created: float = field(default_factory=time.time)
    status: str = "pending"             # pending | connected | error | expired
    code: str | None = None             # error code (never upstream text)
    consumed: bool = False
    mismatches: int = 0
    runner: web.AppRunner | None = None
    timer: asyncio.Task | None = None


class OAuthManager:
    def __init__(self, service: AiService, *, opener: Callable[[str], Any] | None = None,
                 timeout_s: float = FLOW_TIMEOUT_S) -> None:
        self.service = service
        self.opener = opener or system_open
        self.timeout_s = timeout_s
        self.flows: dict[str, Flow] = {}

    # ---------------------------------------------------------------- start
    async def start(self, provider_id: str, *, consent: bool = False) -> dict:
        provider = self.service.provider(provider_id)
        if not provider.oauth or "oauth_pkce" not in provider.auth_modes:
            raise ProviderError("oauth_unsupported")
        if consent:
            self.service.grant_consent(provider)
        else:
            self.service.require_consent(provider)
        for old in [f for f in self.flows.values() if f.provider_id == provider.id and f.status == "pending"]:
            await self._finish(old, "error", "superseded")
        while len(self.flows) >= MAX_FLOWS:
            self.flows.pop(next(iter(self.flows)))

        verifier, challenge = pkce_pair()
        state = secrets.token_urlsafe(32)                                    # 256-bit
        flow = Flow(provider.id, state, verifier, 0)
        sock = self._bind()                                                   # bind BEFORE the browser opens
        flow.port = sock.getsockname()[1]
        app = web.Application()

        async def handler(request: web.Request, f: Flow = flow, p: Provider = provider) -> web.Response:
            return await self._callback(request, f, p)

        app.router.add_get("/callback", handler)
        runner = web.AppRunner(app, access_log=None, shutdown_timeout=0.5)
        await runner.setup()
        await web.SockSite(runner, sock).start()
        flow.runner = runner
        flow.timer = asyncio.get_running_loop().create_task(self._expire(flow))
        self.flows[state] = flow

        query = urlencode({"callback_url": f"http://127.0.0.1:{flow.port}/callback", "code_challenge": challenge,
                           "code_challenge_method": provider.oauth["code_challenge_method"], "state": state})
        authorize_url = f"{provider.oauth['authorize_url']}?{query}"
        opened = False
        try:
            opened = bool(await asyncio.to_thread(self.opener, authorize_url))
        except Exception as exc:                                              # noqa: BLE001
            LOG.info("browser launch failed: %s", _redact.safe_exc(exc))
        LOG.info("oauth flow started provider=%s port=%d browser_opened=%s", provider.id, flow.port, opened)
        return {"provider": provider.id, "authorize_url": authorize_url, "state": state,
                "expires_in": int(self.timeout_s), "browser_opened": opened}

    @staticmethod
    def _bind() -> socket.socket:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if sys.platform == "win32" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        sock.bind(("127.0.0.1", 0))
        sock.listen(4)
        sock.setblocking(False)
        return sock

    # ---------------------------------------------------------------- listener
    async def _callback(self, request: web.Request, flow: Flow, provider: Provider) -> web.Response:
        def page(title: str, status: int = 200) -> web.Response:
            return web.Response(text=_DONE_PAGE.format(title=title), content_type="text/html", status=status,
                                headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
                                         "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"})

        if request.host != f"127.0.0.1:{flow.port}":
            return page("Forbidden host", 403)
        if flow.consumed or flow.status != "pending":
            return page("This sign-in link was already used", 410)
        query = request.query
        if not hmac.compare_digest(query.get("state", "").encode("utf-8"), flow.state.encode("utf-8")):
            flow.mismatches += 1
            LOG.warning("oauth callback state mismatch (%d)", flow.mismatches)
            if flow.mismatches >= MAX_STATE_MISMATCHES:
                self._schedule_finish(flow, "error", "state_mismatch")
            return page("Sign-in rejected", 400)
        flow.consumed = True                                                  # single use from here on
        if query.get("error"):
            self._schedule_finish(flow, "error", "access_denied")
            return page("Sign-in cancelled")
        code = query.get("code", "")
        if not code or len(code) > 1024:
            self._schedule_finish(flow, "error", "invalid_request")
            return page("Sign-in failed", 400)
        try:
            key = await self._exchange(provider, code, flow.verifier)
            self.service.store_oauth_key(provider, key)
        except ProviderError as error:
            self.service._set_health(provider.id, error.code, error.status)   # noqa: SLF001
            self._schedule_finish(flow, "error", error.code)
            return page("Sign-in failed", 502)
        self._schedule_finish(flow, "connected", None)
        return page("ValoHub is connected")

    async def _exchange(self, provider: Provider, code: str, verifier: str) -> str:
        url = (provider.base_url or "").rstrip("/") + provider.oauth["token_path"]
        data = await _Http(provider).json("POST", url, None, body={
            "code": code, "code_verifier": verifier,
            "code_challenge_method": provider.oauth["code_challenge_method"]})
        key = data.get("key") if isinstance(data, dict) else None
        if not isinstance(key, str) or not (8 <= len(key) <= 512) or any(c.isspace() for c in key):
            raise ProviderError("upstream_error", detail="no_key_in_exchange")
        _redact.register_secret(key)
        return key

    # ---------------------------------------------------------------- lifecycle
    def _schedule_finish(self, flow: Flow, status: str, code: str | None) -> None:
        if flow.status == "pending":
            flow.status, flow.code = status, code
        asyncio.get_running_loop().create_task(self._finish(flow, status, code, delay=0.3))

    async def _finish(self, flow: Flow, status: str, code: str | None, *, delay: float = 0.0) -> None:
        if flow.status == "pending":
            flow.status, flow.code = status, code
        if delay:
            await asyncio.sleep(delay)                                         # let the response flush first
        runner, flow.runner = flow.runner, None
        timer, flow.timer = flow.timer, None
        if timer is not None and timer is not asyncio.current_task():
            timer.cancel()
        if runner is not None:
            try:
                await runner.cleanup()
            except Exception:                                                  # noqa: BLE001
                LOG.debug("oauth listener cleanup failed", exc_info=True)

    async def _expire(self, flow: Flow) -> None:
        try:
            await asyncio.sleep(self.timeout_s)
        except asyncio.CancelledError:
            return
        if flow.status == "pending":
            flow.consumed = True
            await self._finish(flow, "expired", "expired")

    def status(self, state: str) -> dict:
        flow = self.flows.get(state) if isinstance(state, str) else None
        if flow is None:
            raise ProviderError("flow_not_found")
        out = {"provider": flow.provider_id, "status": flow.status}
        if flow.code:
            out["code"] = flow.code
        return out

    async def close(self) -> None:
        for flow in list(self.flows.values()):
            if flow.status == "pending":
                flow.status, flow.code = "error", "shutdown"
            await self._finish(flow, flow.status, flow.code)
        self.flows.clear()

    # ---------------------------------------------------------------- disconnect
    async def revoke(self, provider: Provider, secret: str) -> bool:
        """Best-effort token revocation; False when the provider documents no revocation endpoint."""
        path = provider.oauth.get("revoke_path") if provider.oauth else None
        if not path:
            return False
        url = (provider.base_url or "").rstrip("/") + path
        await _Http(provider).json("POST", url, secret, body={"token": secret})
        return True

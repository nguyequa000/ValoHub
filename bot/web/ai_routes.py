"""aiohttp routes for the AI Providers feature (DESIGN-V2 §6).

    GET    /api/ai/providers                      registry + state (never a key; ``configured``/``last4``)
    POST   /api/ai/providers/{id}/key             verify-then-save an API key  {key, consent?, base_url?, test_chat?, model?}
    POST   /api/ai/providers/{id}/oauth/start     OpenRouter PKCE only (others: 400 oauth_unsupported)
    GET    /api/ai/oauth/callback?state=…         poll a flow: pending|connected|error|expired (no code/key ever)
    DELETE /api/ai/providers/{id}                 best-effort revoke, then delete
    GET    /api/ai/providers/{id}/models          cached list, ``?refresh=1`` re-fetches
    POST   /api/ai/providers/{id}/test            verify + 1-token chat
    POST   /api/ai/chat                           OpenAI-shaped chat, ``"stream": true`` -> SSE passthrough
    plus  POST|DELETE /api/ai/providers/{id}/consent · PUT /api/ai/default · POST /api/ai/providers/{id}/open-console

Auth is the host app's job (desktop: token middleware + origin middleware).  Apps without a token
(the legacy dashboard) get the equivalent checks here: loopback peer, loopback Host, same-origin Origin.
A credential is only ever accepted in a JSON body - never in the query string.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from aiohttp import web

from bot.replay import redact as _redact
from bot.replay.ai_oauth import OAuthManager, system_open
from bot.replay.ai_providers import AiService, ProviderError
from bot.replay.offline import is_loopback_host
from bot.web.peer import _loopback_peer

LOG = logging.getLogger("bot.web.ai_routes")

SERVICE_KEY = "ai_service"
OAUTH_KEY = "ai_oauth"
MOUNTED_KEY = "ai_routes_mounted"
MAX_BODY = 2 * 1024 * 1024
FORBIDDEN_QUERY = frozenset({"key", "api_key", "apikey", "token", "access_token", "secret", "password", "code",
                             "code_verifier", "authorization"})
SAFE = frozenset({"GET", "HEAD", "OPTIONS"})


def _json(data: Any, status: int = 200) -> web.Response:
    return web.json_response(data, status=status, headers={"Cache-Control": "no-store"})


def _fail(error: ProviderError) -> web.Response:
    return _json(error.payload(), error.http_status)


def _bad(detail: str, code: str = "invalid_request", status: int = 400) -> web.Response:
    return _json({"error": code, "code": code, "status": None, "request_id": None, "detail": detail,
                  "message": "The request is not valid."}, status)


def _guard(request: web.Request) -> web.Response | None:
    app = request.app
    if "token" not in app:                                   # legacy dashboard: no token middleware
        if not _loopback_peer(request.remote):
            return _bad("loopback_only", "forbidden_peer", 403)
        host = (request.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
        if not is_loopback_host(host):
            return _bad("loopback_host_only", "forbidden_host", 403)
        origin = request.headers.get("Origin")
        if request.method not in SAFE and origin is not None and origin != f"http://{request.headers.get('Host')}":
            return _bad("cross_origin", "forbidden_origin", 403)
    leaked = FORBIDDEN_QUERY.intersection(k.lower() for k in request.query)
    if leaked:
        return _bad("secret_in_query", "secret_in_query")      # never accept a credential in a URL
    return None


async def _body(request: web.Request) -> dict:
    if not request.can_read_body:
        return {}
    size = request.content_length
    if size is not None and size > MAX_BODY:
        raise ProviderError("invalid_request", detail="body_too_large")
    raw = bytearray()
    async for chunk in request.content.iter_chunked(64 * 1024):
        raw += chunk
        if len(raw) > MAX_BODY:
            raise ProviderError("invalid_request", detail="body_too_large")
    if not raw:
        return {}
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError:
        raise ProviderError("invalid_request", detail="body_not_json") from None
    if not isinstance(data, dict):
        raise ProviderError("invalid_request", detail="body_not_object")
    return data


def _svc(request: web.Request) -> AiService:
    return request.app[SERVICE_KEY]


def _wrap(handler: Callable):
    async def inner(request: web.Request) -> web.StreamResponse:
        blocked = _guard(request)
        if blocked is not None:
            return blocked
        try:
            return await handler(request)
        except ProviderError as error:
            return _fail(error)
        except web.HTTPException:
            raise
        except Exception as exc:                                      # noqa: BLE001 - never leak internals
            LOG.error("ai route %s failed: %s", request.path, _redact.safe_exc(exc))
            return _json({"error": "internal_error", "code": "internal_error", "status": None, "request_id": None,
                          "message": "Unexpected error."}, 500)
    return inner


def _flag(value) -> bool:
    return value is True


# ----------------------------------------------------------------------------- handlers

async def providers_list(request: web.Request) -> web.Response:
    probe = request.query.get("probe", "1") != "0"
    return _json(await _svc(request).list_providers(probe=probe))


async def provider_key(request: web.Request) -> web.Response:
    body = await _body(request)
    key = body.get("key")
    if key is not None and not isinstance(key, str):
        raise ProviderError("invalid_request", detail="key_type")
    base_url, model = body.get("base_url"), body.get("model")
    if (base_url is not None and not isinstance(base_url, str)) or (model is not None and not isinstance(model, str)):
        raise ProviderError("invalid_request", detail="field_type")
    view = await _svc(request).connect_key(request.match_info["provider_id"], key, consent=_flag(body.get("consent")),
                                           base_url=base_url, model=model, test_chat=_flag(body.get("test_chat")))
    return _json(view)


async def oauth_start(request: web.Request) -> web.Response:
    body = await _body(request)
    return _json(await request.app[OAUTH_KEY].start(request.match_info["provider_id"], consent=_flag(body.get("consent"))))


async def oauth_status(request: web.Request) -> web.Response:
    return _json(request.app[OAUTH_KEY].status(request.query.get("state", "")))


async def provider_delete(request: web.Request) -> web.Response:
    return _json(await _svc(request).disconnect(request.match_info["provider_id"],
                                                revoker=request.app[OAUTH_KEY].revoke))


async def provider_models(request: web.Request) -> web.Response:
    return _json(await _svc(request).models(request.match_info["provider_id"],
                                            refresh=request.query.get("refresh") == "1"))


async def provider_test(request: web.Request) -> web.Response:
    body = await _body(request)
    model = body.get("model")
    if model is not None and not isinstance(model, str):
        raise ProviderError("invalid_request", detail="model")
    return _json(await _svc(request).test(request.match_info["provider_id"], model))


async def consent_post(request: web.Request) -> web.Response:
    svc = _svc(request)
    provider = svc.provider(request.match_info["provider_id"])
    body = await _body(request)
    if not _flag(body.get("accepted")):
        raise ProviderError("invalid_request", detail="accepted_required")
    return _json({"ok": True, "consent": svc.grant_consent(provider)})


async def consent_delete(request: web.Request) -> web.Response:
    svc = _svc(request)
    svc.revoke_consent(svc.provider(request.match_info["provider_id"]))
    return _json({"ok": True})


async def default_put(request: web.Request) -> web.Response:
    body = await _body(request)
    provider_id, model = body.get("provider"), body.get("model")
    if not isinstance(provider_id, str) or (model is not None and not isinstance(model, str)):
        raise ProviderError("invalid_request", detail="default_shape")
    return _json({"ok": True, "default": _svc(request).set_default(provider_id, model)})


async def open_console(request: web.Request) -> web.Response:
    provider = _svc(request).provider(request.match_info["provider_id"])
    if not provider.console_url:
        raise ProviderError("invalid_request", detail="no_console")
    opener = _svc(request).opener or system_open
    import asyncio
    opened = bool(await asyncio.to_thread(opener, provider.console_url))
    return _json({"ok": True, "opened": opened, "host": urlsplit(provider.console_url).hostname})


async def chat(request: web.Request) -> web.StreamResponse:
    body = await _body(request)
    svc = _svc(request)
    options = {"provider_id": body.get("provider"), "model": body.get("model"), "max_tokens": body.get("max_tokens"),
               "temperature": body.get("temperature"), "endpoint": body.get("endpoint")}
    if any(options[k] is not None and not isinstance(options[k], str) for k in ("provider_id", "model", "endpoint")):
        raise ProviderError("invalid_request", detail="option_type")
    for numeric in ("max_tokens", "temperature"):
        value = options[numeric]
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
            raise ProviderError("invalid_request", detail="option_type")
    if not _flag(body.get("stream")):
        return _json(await svc.chat(body.get("messages"), **options))

    async with svc.chat_stream(body.get("messages"), **options) as handle:
        response = web.StreamResponse(status=200, headers={
            "Content-Type": "text/event-stream; charset=utf-8", "Cache-Control": "no-store",
            "Connection": "keep-alive", "X-Accel-Buffering": "no"})
        await response.prepare(request)
        await response.write(b"event: open\ndata: " + json.dumps(handle.meta).encode("utf-8") + b"\n\n")
        try:
            async for payload in handle:
                await response.write(b"data: " + payload.encode("utf-8") + b"\n\n")
        except ProviderError as error:
            await response.write(b"event: error\ndata: " + json.dumps(
                {"code": error.code, "status": error.status, "request_id": error.request_id}).encode("utf-8") + b"\n\n")
        except ConnectionResetError:
            pass
        return response


# ----------------------------------------------------------------------------- mount

def _default_root() -> Path:
    from bot.replay import paths
    located = paths.get_paths()
    # legacy mode keeps data_root == repo root; keep the sealed file inside the git-ignored work folder
    return located.data_root if located.mode == paths.MODE_DESKTOP else located.work


def mount(app: web.Application, *, data_root: Path | Callable[[], Path] | None = None, registry=None,
          opener=None, oauth_timeout: float | None = None, environ=None, vault=None) -> AiService:
    """Register the routes (idempotent).  ``data_root`` defaults to the app's resolved data folder."""
    if app.get(MOUNTED_KEY):
        return app[SERVICE_KEY]
    service = AiService(data_root or _default_root, registry=registry, opener=opener, environ=environ, vault=vault)
    kwargs = {} if oauth_timeout is None else {"timeout_s": oauth_timeout}
    manager = OAuthManager(service, opener=opener, **kwargs)
    app[SERVICE_KEY], app[OAUTH_KEY], app[MOUNTED_KEY] = service, manager, True

    router = app.router
    router.add_get("/api/ai/providers", _wrap(providers_list))
    router.add_post("/api/ai/providers/{provider_id}/key", _wrap(provider_key))
    router.add_post("/api/ai/providers/{provider_id}/oauth/start", _wrap(oauth_start))
    router.add_get("/api/ai/oauth/callback", _wrap(oauth_status))
    router.add_delete("/api/ai/providers/{provider_id}", _wrap(provider_delete))
    router.add_get("/api/ai/providers/{provider_id}/models", _wrap(provider_models))
    router.add_post("/api/ai/providers/{provider_id}/test", _wrap(provider_test))
    router.add_post("/api/ai/providers/{provider_id}/consent", _wrap(consent_post))
    router.add_delete("/api/ai/providers/{provider_id}/consent", _wrap(consent_delete))
    router.add_post("/api/ai/providers/{provider_id}/open-console", _wrap(open_console))
    router.add_put("/api/ai/default", _wrap(default_put))
    router.add_post("/api/ai/chat", _wrap(chat))

    async def _close(_app: web.Application) -> None:
        await manager.close()
    app.on_cleanup.append(_close)
    return service

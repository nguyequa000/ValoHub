"""ValoHub desktop server: loopback-only aiohttp app with a per-launch session token.

Two ways to run it:

* sidecar (product path, via ``bot.sidecar serve``): :func:`serve_main` - token from env
  ``VRB_TOKEN`` (never argv, never printed), ``--data-dir`` sets ``VALOHUB_HOME``, first stdout
  line ``{"event":"ready","port":N,"pid":PID}``, stdin EOF / ``POST /api/app/shutdown`` = stop.
* in-process thread: :class:`DesktopServer` (contract C4) and the ``--headless`` CLI.

Must NOT import ``bot.web.app`` (legacy modules create folders at import time); replay modules
are imported lazily so ``VALOHUB_HOME`` can be set first.
"""
from __future__ import annotations

import argparse
import asyncio
import hmac
import json
import os
import secrets
import sys
import threading
from dataclasses import dataclass
from typing import Optional

from aiohttp import web

from bot.replay.offline import is_loopback_host
from bot.replay.redact import redact as _redact_text
from bot.web.csp import CSP

APP_NAME = "ValoHub"
APP_VERSION = (os.environ.get("VALOHUB_VERSION") or "").strip() or "0.1.0"
COOKIE = "vh_session"
HEADER = "X-ValoHub-Token"
DEFAULT_PORT = 18792
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
FORCED_ENV = {"VALO_OFFLINE": "1"}
DROPPED_ENV = ("HENRIKDEV_TOKEN", "VALO_LOBBY", "VALO_WATCHER")


class ServerStartError(RuntimeError):
    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.hint = hint


@dataclass(frozen=True)
class ServerInfo:
    host: str
    port: int
    token: str
    url: str
    fell_back: bool = False


def apply_offline_env() -> None:
    for key, value in FORCED_ENV.items():
        os.environ[key] = value
    for key in DROPPED_ENV:
        os.environ.pop(key, None)


def _deny(status: int, error: str, hint: str) -> web.Response:
    return web.json_response({"error": error, "hint": hint}, status=status)


def _self_origins(app: web.Application) -> set[str]:
    return {f"http://{host}" for host in app["allowed_hosts"]}


@web.middleware
async def error_mw(request: web.Request, handler):
    try:
        return await handler(request)
    except web.HTTPException as exc:
        if exc.status < 400:
            raise
        return _deny(exc.status, exc.text or exc.reason, "")
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        return _deny(500, _redact_text(str(exc))[:300], "internal error")


@web.middleware
async def host_mw(request: web.Request, handler):
    host = (request.headers.get("Host") or "").strip().lower()
    if host not in request.app["allowed_hosts"]:
        return _deny(403, "forbidden host", "use http://127.0.0.1:<port>")
    return await handler(request)


@web.middleware
async def origin_mw(request: web.Request, handler):
    upgrade = (request.headers.get("Upgrade") or "").lower() == "websocket"
    if request.method not in SAFE_METHODS or upgrade:
        origin = request.headers.get("Origin")
        if origin is not None and origin not in _self_origins(request.app):
            return _deny(403, "forbidden origin", "cross-origin requests are not allowed")
    return await handler(request)


def _token_ok(supplied, token: str) -> bool:
    return isinstance(supplied, str) and bool(supplied) and hmac.compare_digest(
        supplied.encode("utf-8"), token.encode("utf-8"))


@web.middleware
async def token_mw(request: web.Request, handler):
    token = request.app["token"]
    if request.path == "/api/app/health" and request.method in ("GET", "HEAD"):
        return await handler(request)
    if request.path == "/" and request.method == "GET" and "t" in request.query:
        if _token_ok(request.query.get("t"), token):
            response = web.HTTPFound("/replays")
            response.set_cookie(COOKIE, token, httponly=True, samesite="Strict", path="/")
            raise response
        return _deny(403, "invalid session token", "open the app from the launcher")
    if _token_ok(request.headers.get(HEADER), token) or _token_ok(request.cookies.get(COOKIE), token):
        return await handler(request)
    return _deny(403, "missing or invalid session token", "open the app from the launcher")


async def _add_security_headers(request: web.Request, response: web.StreamResponse) -> None:
    response.headers["Content-Security-Policy"] = CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"


async def _health(request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "app": APP_NAME, "version": APP_VERSION})


async def _info(request: web.Request) -> web.Response:
    from bot.replay import paths as replay_paths, pipeline
    decoder: dict = {}
    try:
        info = await asyncio.to_thread(pipeline.decoder_info)
        decoder["status"] = info.get("status")
    except Exception as exc:  # noqa: BLE001
        decoder["status"] = "unavailable"
        decoder["error"] = str(exc)[:200]
    try:
        located = replay_paths.resolve_decoder()
        decoder["source"] = getattr(located, "source", None)
    except Exception:  # noqa: BLE001 - resolve_decoder may be absent on older bases
        decoder["source"] = None
    return web.json_response({
        "app": APP_NAME, "version": APP_VERSION, "mode": "desktop", "pid": os.getpid(),
        # The packaged launcher passes the commit it read from BUILD-INFO.json; dev runs omit it.
        "buildCommit": (os.environ.get("VALOHUB_BUILD_COMMIT") or "").strip() or None,
        "paths": replay_paths.get_paths().to_dict(), "decoder": decoder,
        "offline": True, "updaterEnabled": False,
        "features": list(request.app.get("features", ())),
    })


async def _shutdown(request: web.Request) -> web.Response:
    callback = request.app.get("shutdown_cb")
    if callback is not None:
        asyncio.get_running_loop().call_later(0.2, callback)
    return web.json_response({"ok": True, "shutting_down": True}, status=202)


async def _root(request: web.Request) -> web.Response:
    raise web.HTTPFound("/replays")


def desktop_features() -> tuple[str, ...]:
    from bot.replay import p2
    return tuple(name for name in p2.FEATURES if name != "p2_lobby")


def create_desktop_app(token: str, port: int, *, features=None) -> web.Application:
    """Build the desktop app (no listener). Never imports ``bot.web.app``."""
    from bot.replay import api as replay_api, jobs as replay_jobs, p2 as replay_p2
    app = web.Application(middlewares=[error_mw, host_mw, origin_mw, token_mw], client_max_size=2 * 1024 ** 3)
    app["token"] = token
    app["desktop"] = True
    app["allowed_hosts"] = {f"127.0.0.1:{port}", f"localhost:{port}"}
    app["jobs"] = replay_jobs.JobManager()
    app["features"] = tuple(desktop_features() if features is None else features)
    app.on_response_prepare.append(_add_security_headers)

    app.router.add_get("/", _root)
    app.router.add_get("/api/app/health", _health)
    app.router.add_get("/api/app/info", _info)
    app.router.add_post("/api/app/shutdown", _shutdown)
    replay_api.setup_routes(app)
    replay_p2.mount(app, features=app["features"])
    from bot.web import ai_routes
    ai_routes.mount(app)

    async def _stop_jobs(_app: web.Application) -> None:
        try:
            await asyncio.wait_for(_app["jobs"].shutdown(), timeout=3)
        except Exception:  # noqa: BLE001
            pass

    app.on_cleanup.append(_stop_jobs)
    return app


def _bound_port(site: web.TCPSite) -> int:
    return site._server.sockets[0].getsockname()[1]  # noqa: SLF001


async def bind_app(app: web.Application, preferred_port: int, host: str = "127.0.0.1"):
    """Start listening on ``host``; ``(runner, port, fell_back)``. Preferred port then ephemeral."""
    if not is_loopback_host(host) or host in ("0.0.0.0", "::"):
        raise ServerStartError(f"refusing to bind {host!r}", "the desktop server only listens on 127.0.0.1")
    runner = web.AppRunner(app, access_log=None, shutdown_timeout=1.0)
    await runner.setup()
    fell_back = False
    site = web.TCPSite(runner, host, preferred_port)
    try:
        await site.start()
    except OSError:
        fell_back = True
        site = web.TCPSite(runner, host, 0)
        try:
            await site.start()
        except OSError as exc:
            await runner.cleanup()
            raise ServerStartError(f"cannot bind {host}: {exc}", "check local firewall/security software") from exc
    port = _bound_port(site)
    hosts = app["allowed_hosts"]
    hosts.clear()
    hosts.update({f"127.0.0.1:{port}", f"localhost:{port}"})
    return runner, port, fell_back


class DesktopServer:
    """aiohttp in its own thread + loop (contract C4; P2 path)."""

    def __init__(self, token: Optional[str] = None, preferred_port: int = DEFAULT_PORT,
                 paths=None, host: str = "127.0.0.1") -> None:
        self.token = token or secrets.token_urlsafe(32)
        self.preferred_port = preferred_port
        self.paths = paths
        self.host = host
        self.info: Optional[ServerInfo] = None
        self.app: Optional[web.Application] = None
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._runner = None
        self._ready = threading.Event()
        self._stopped = threading.Event()
        self._error: Optional[BaseException] = None
        self.stop_timed_out = False

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and self.info is not None

    def active_jobs(self) -> int:
        if self.app is None:
            return 0
        return sum(1 for job in self.app["jobs"].snapshot() if job.get("status") in ("queued", "running"))

    def start(self, timeout: float = 30) -> ServerInfo:
        if self.is_running:
            return self.info  # type: ignore[return-value]
        apply_offline_env()
        self._ready.clear()
        self._stopped.clear()
        self._error = None
        self._thread = threading.Thread(target=self._run, name="valohub-server", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout):
            raise ServerStartError("server did not start in time", "see the log")
        if self._error is not None:
            err = self._error
            if isinstance(err, ServerStartError):
                raise err
            raise ServerStartError(str(err), "see the log") from err
        return self.info  # type: ignore[return-value]

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            self.app = create_desktop_app(self.token, self.preferred_port)
            self.app["shutdown_cb"] = lambda: loop.create_task(self._graceful())
            self._runner, port, fell_back = loop.run_until_complete(
                bind_app(self.app, self.preferred_port, self.host))
            self.info = ServerInfo(self.host, port, self.token, f"http://{self.host}:{port}/?t={self.token}", fell_back)
        except BaseException as exc:  # noqa: BLE001
            self._error = exc
            self._ready.set()
            loop.close()
            return
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:  # noqa: BLE001
                pass
            loop.close()
            self._stopped.set()

    async def _graceful(self) -> None:
        try:
            if self._runner is not None:
                await self._runner.cleanup()
        finally:
            asyncio.get_running_loop().stop()

    def stop(self, timeout: float = 15) -> bool:
        """Stop and join; True when it ended cleanly inside ``timeout`` (bounded either way)."""
        loop, thread = self._loop, self._thread
        if loop is None or thread is None or not thread.is_alive():
            return True
        try:
            future = asyncio.run_coroutine_threadsafe(self._graceful(), loop)
            future.result(timeout=max(0.1, timeout - 1))
        except Exception:  # noqa: BLE001
            self.stop_timed_out = True
            try:
                loop.call_soon_threadsafe(loop.stop)
            except RuntimeError:
                pass
        thread.join(timeout=1.0)
        self.info = None
        return not self.stop_timed_out and not thread.is_alive()

    def wait_stopped(self, timeout: Optional[float] = None) -> bool:
        return self._stopped.wait(timeout)


def _stdin_watcher(callback) -> None:
    def watch() -> None:
        try:
            while sys.stdin.buffer.read(4096):
                pass
        except Exception:  # noqa: BLE001
            pass
        callback()
    threading.Thread(target=watch, name="stdin-eof", daemon=True).start()


def _serve(args, *, token: str, sidecar: bool) -> int:
    if getattr(args, "home", None):
        os.environ["VALOHUB_HOME"] = str(args.home)
    apply_offline_env()
    if sidecar:
        try:
            from bot.replay import winjob  # L2 owned; optional
            winjob.assign_self_to_kill_on_close_job()
        except Exception:  # noqa: BLE001
            pass
    try:
        from bot.replay import paths as replay_paths
        replay_paths.get_paths(refresh=True)
        replay_paths.ensure_data_dirs()
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"event": "error", "error": str(exc)[:300]}), flush=True)
        return 2
    if sidecar:  # no job can be running yet: drop stale staging files / 'processing' cards
        try:
            from bot.replay import pipeline
            pipeline.sweep_interrupted(lambda msg: print(msg, file=sys.stderr, flush=True))
        except Exception:  # noqa: BLE001
            pass
    server = DesktopServer(token=token, preferred_port=args.port)
    try:
        info = server.start()
    except ServerStartError as exc:
        print(json.dumps({"event": "error", "error": str(exc), "hint": exc.hint}), flush=True)
        return 2
    if sidecar:
        line = {"event": "ready", "port": info.port, "pid": os.getpid()}
    else:
        line = {"event": "ready", "port": info.port, "token": info.token, "url": info.url}
    if args.port_file:
        with open(args.port_file, "w", encoding="utf-8") as stream:
            stream.write(str(info.port))
    print(json.dumps(line, separators=(",", ":")), flush=True)
    _stdin_watcher(lambda: server.stop(5))
    try:
        while not server.wait_stopped(0.5):
            pass
    except KeyboardInterrupt:
        server.stop(5)
    # The stdin watcher thread is blocked in a read; leaving it to interpreter teardown crashes
    # Windows Python (0xC0000005), so leave the process explicitly after flushing.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


def serve_main(argv: list[str]) -> int:
    """``serve --port P --data-dir D`` (sidecar). Token from env ``VRB_TOKEN`` only."""
    parser = argparse.ArgumentParser(prog="serve")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--data-dir", dest="home")
    parser.add_argument("--port-file")
    args = parser.parse_args(argv)
    token = os.environ.pop("VRB_TOKEN", "")
    if len(token) < 16:
        print(json.dumps({"event": "error", "error": "VRB_TOKEN env var missing or too short"}), flush=True)
        return 2
    return _serve(args, token=token, sidecar=True)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="bot.web.desktop")
    parser.add_argument("--headless", action="store_true", required=True)
    parser.add_argument("--home")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--port-file")
    args = parser.parse_args(argv)
    return _serve(args, token=secrets.token_urlsafe(32), sidecar=False)


if __name__ == "__main__":
    sys.exit(main())

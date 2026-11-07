"""Entry point: python -m bot.web (bind overrides: HOST and PORT env vars)."""
import ipaddress
import os
import re

from aiohttp import web

from bot.web.app import create_app
from bot.web.peer import _loopback_peer


_LOBBY_POST = re.compile(r"/api/lobby/[^/]+/(?:join|leave)\Z")
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


def _add_remote_guard(app: web.Application, host: str) -> None:
    """Keep local control APIs off the LAN even when the server listens there."""
    if host in {"127.0.0.1", "localhost", "::1"}:
        return

    @web.middleware
    async def remote_guard(request: web.Request, handler):
        if not _loopback_peer(request.remote):
            if request.path == "/api/watcher/status":
                raise web.HTTPForbidden(text="local access only")
            public_lobby_post = (
                os.getenv("VALO_LOBBY") == "1"
                and request.method == "POST"
                and (request.path == "/api/lobby" or _LOBBY_POST.fullmatch(request.path))
            )
            if request.method not in _SAFE_METHODS and not public_lobby_post:
                raise web.HTTPForbidden(text="local access only")
        return await handler(request)

    app.middlewares.append(remote_guard)


def main() -> None:
    host = os.getenv("HOST", "127.0.0.1")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        pass  # Hostnames remain valid bind targets.
    else:
        if isinstance(address, ipaddress.IPv6Address) and address.is_unspecified:
            raise ValueError("HOST=:: is not supported: IPv6 wildcard binds IPv6-only on Windows; use HOST=0.0.0.0 for LAN")
    port = int(os.getenv("PORT", "8787"))
    app = create_app()
    _add_remote_guard(app, host)
    if host == "0.0.0.0":
        print(f"Serving on http://{host}:{port} (all interfaces; use your LAN IP)")
    else:
        print(f"Serving on http://{host if ':' not in host else '[' + host + ']'}:{port}")
    web.run_app(app, host=host, port=port)


if __name__ == "__main__":
    main()

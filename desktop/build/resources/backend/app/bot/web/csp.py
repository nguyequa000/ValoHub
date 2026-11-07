"""Shared web-security constants: the v2 Content-Security-Policy and the SPA route table."""
from __future__ import annotations

CSP = ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
       "connect-src 'self'; font-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'; "
       "object-src 'none'")

# Pages served by the single-page app shell (app.html); the shell routes on location.pathname.
SPA_ROUTES = ("/replays", "/replays/", "/replay/{replay_id}", "/analysis/{replay_id}", "/coach/{replay_id}",
              "/providers", "/settings")
SPA_PREFIXES = ("/replays", "/replay/", "/analysis/", "/coach/", "/providers", "/settings",
                "/static/replays_app", "/api/")


def wants_csp(path: str) -> bool:
    """Paths of the replay app / API (the legacy scouting dashboard keeps its inline scripts)."""
    return path == "/replays" or any(path.startswith(prefix) for prefix in SPA_PREFIXES)

"""P2 feature mounts — optional modules, failure-isolated.

Each ``bot.replay.p2_<name>`` module is OPTIONAL and exposes ``mount(app)``
which may add routes / startup hooks for its feature (see
VALOPLANT_WORK/P2_CONTRACT.md). Absent or broken features never block boot.

Shared surface owned here (do not edit from feature lanes):
- GET /replays/{replay_id}/analysis → serves static/replays_app/analysis.html
- /replays-assets/… → static file service for the cached asset pack
  (only registered when bot/web/static/replays_app/assets/ exists)
"""

from __future__ import annotations

import importlib
import logging

from aiohttp import web

from . import store

LOG = logging.getLogger("bot.replay.p2")

FEATURES = ("p2_analysis", "p2_review", "p2_identity", "p2_watcher", "p2_lobby", "p3_coach", "p3_parser_admin")
STATIC_DIR = store.ROOT / "bot" / "web" / "static" / "replays_app"


def mount(app: web.Application, features=None) -> None:
    async def analysis_page(request: web.Request) -> web.StreamResponse:
        page = STATIC_DIR / "analysis.html"
        if not page.is_file():
            return web.json_response({"error": "analysis page not built"}, status=404)
        return web.FileResponse(page)

    app.router.add_get("/replays/{replay_id}/analysis", analysis_page)

    assets = STATIC_DIR / "assets"
    if assets.is_dir():
        app.router.add_static("/replays-assets", assets)
        LOG.info("p2 assets mounted from %s", assets)

    for name in (FEATURES if features is None else tuple(features)):
        try:
            module = importlib.import_module(f"bot.replay.{name}")
        except ModuleNotFoundError:
            LOG.info("p2 feature %s not present (optional)", name)
            continue
        try:
            module.mount(app)
            LOG.info("p2 feature %s mounted", name)
        except Exception:  # noqa: BLE001 — isolate feature failures
            LOG.exception("p2 feature %s failed to mount", name)

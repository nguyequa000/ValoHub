"""Local (127.0.0.1) Valorant scouting dashboard.

aiohttp app; HTML rendering lives in bot.web.templates. Player data comes
from HenrikDev (custom matches, first-contact/trade analysis); local replays
are imported and served by the new pipeline in bot.replay.* (/replays).
"""
import logging
import os
from pathlib import Path
from urllib.parse import quote

import aiohttp
from aiohttp import web
from dotenv import load_dotenv

load_dotenv()

from bot.replay import api as replay_api
from bot.replay import jobs as replay_jobs
from bot.replay import p2 as replay_p2
from bot.services import (
    cache,
    analyze_player_first_contacts,
    format_player_stats_table,
    format_trade_stats_table,
    format_team_summary_table,
    fetch_henrikdev_custom_matches,
)
from bot.services.first_contact_analyzer import EXPORTS_DIR
from bot.web import templates
from bot.web import ai_routes
from bot.web import csp as web_csp


WORKSPACE = Path(__file__).parent.parent.parent


async def _security_headers(request: web.Request, response: web.StreamResponse) -> None:
    """CSP for the replay app + API only; the legacy scouting pages keep their inline handlers."""
    if web_csp.wants_csp(request.path):
        response.headers["Content-Security-Policy"] = web_csp.CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"


def _html(body: str, status: int = 200) -> web.Response:
    return web.Response(text=body, content_type="text/html", status=status)


def _error_response(message: str, status: int = 500) -> web.Response:
    return _html(templates.error_page(message), status)


@web.middleware
async def error_middleware(request: web.Request, handler):
    """Route failures: JSON for /api/*, error page for everything else."""
    try:
        return await handler(request)
    except web.HTTPException as exc:
        if exc.status < 400:
            raise
        if request.path.startswith("/api/"):
            return web.json_response({"error": exc.text or exc.reason}, status=exc.status)
        return _error_response(exc.text or exc.reason, exc.status)
    except Exception as exc:
        logging.getLogger(__name__).exception("Request %s failed", request.path)
        if request.path.startswith("/api/"):
            return web.json_response({"error": str(exc)}, status=500)
        return _error_response(str(exc), 500)


def _decode_riot_id(segment: str) -> str:
    # URL paths cannot contain '#', so Riot IDs arrive as Name-Tag; only the
    # last '-' is the tag separator because player names may contain hyphens.
    head, sep, tag = segment.rpartition("-")
    return f"{head}#{tag}" if sep else segment


def _heatmap_html(paths) -> str:
    if not paths:
        return "<p class='muted'>No heatmaps generated.</p>"
    parts = ["<h2>Heatmaps</h2><div class='grid'>"]
    for path in paths:
        path = Path(path)
        try:
            rel = path.relative_to(EXPORTS_DIR).as_posix()
        except ValueError:
            parts.append(
                f"<p class='muted'>Heatmap generated but not served: {templates.esc(path)}</p>"
            )
            continue
        url = f"/static/exports/{quote(rel)}"
        name = templates.esc(path.name)
        parts.append(f"<div class='card'><a href='{url}'>{name}</a><br><img src='{url}' alt='{name}'></div>")
    parts.append("</div>")
    return "".join(parts)


async def exports_asset(request: web.Request) -> web.StreamResponse:
    """Serve export files (heatmaps); the exports/ folder is created on first write."""
    tail = request.match_info.get("tail") or ""
    base = EXPORTS_DIR.resolve()
    candidate = (base / tail).resolve()
    if candidate != base and base not in candidate.parents:
        raise web.HTTPNotFound(text="export asset outside the exports directory")
    if candidate.is_file():
        return web.FileResponse(candidate, headers={"Cache-Control": "no-store"})
    raise web.HTTPNotFound(text=f"export asset not found: {tail}")


async def dashboard(request: web.Request) -> web.Response:
    body = """
    <div class="hero">
      <h1>Scout any player</h1>
      <p class="muted">Enter a Riot ID to pull up trade/first-contact analysis and recent custom matches.</p>
      <form class="scout-form" onsubmit="return goPlayer(this)">
        <input name="q" placeholder="Name#Tag" size="26" autocomplete="off">
        <button class="btn btn-primary" type="submit">Scout player</button>
      </form>
    </div>
    <div class="grid">
      <a class="card card-link" href="/replays">
        <h2>Replay library</h2>
        <p class="muted">Import local .vrf replays and watch them on the 2D minimap viewer.</p>
        <span class="btn btn-outline">Open</span>
      </a>
    </div>
    """
    return _html(templates.layout("Dashboard", body))


async def player_page(request: web.Request) -> web.Response:
    segment = request.match_info["riot_id"]
    riot_id = _decode_riot_id(segment)

    safe_segment = quote(segment, safe="")
    body = f"""
    <h1>Player: {templates.esc(riot_id)}</h1>
    <div class="grid">
      <a class="card card-link" href="/players/{safe_segment}/first-contact">
        <h2>First contact / trades</h2>
        <p class="muted">Entry timings, trade kills, and site heatmaps.</p>
        <span class="btn btn-outline">Open</span>
      </a>
      <a class="card card-link" href="/matches/{safe_segment}">
        <h2>Custom matches</h2>
        <p class="muted">Recent HenrikDev custom match history.</p>
        <span class="btn btn-outline">Open</span>
      </a>
    </div>
    """
    return _html(templates.layout(f"Player {riot_id}", body))


async def player_first_contact(request: web.Request) -> web.Response:
    riot_id = _decode_riot_id(request.match_info["riot_id"])
    region = request.query.get("region", "na")
    limit = int(request.query.get("limit", 25))
    token = request.app["henrik_token"]
    if not token:
        raise ValueError("HenrikDev token not configured. Set HENRIKDEV_TOKEN in .env")

    result = await analyze_player_first_contacts(
        riot_id=riot_id,
        region=region,
        limit=limit,
        token=token,
        generate_heatmaps=True,
        session=request.app["session"],
    )
    if not result["success"]:
        raise ValueError(f"Analysis failed: {result.get('error', 'Unknown error')}")
    if not result["target_player"]:
        raise ValueError("No first contact data found for this player in the analyzed matches.")

    player_table = format_player_stats_table(result["all_player_stats"], riot_id)
    team_table = format_team_summary_table(result["team_summary"])
    trade_table = format_trade_stats_table(result["trade_stats"], riot_id)

    body = f"""
    <h1>First Contact: {templates.esc(riot_id)}</h1>
    <p class="muted">Matches analyzed: {result['matches_analyzed']} | Total rounds: {result['total_rounds']} | Region: {templates.esc(region.upper())}</p>
    <pre>{templates.esc(player_table)}</pre>
    <pre>{templates.esc(team_table)}</pre>
    <pre>{templates.esc(trade_table)}</pre>
    {_heatmap_html(result.get("heatmap_paths", []))}
    """
    return _html(templates.layout(f"First Contact {riot_id}", body))


async def matches(request: web.Request) -> web.Response:
    riot_id = _decode_riot_id(request.match_info["riot_id"])
    region = request.query.get("region", "na")
    limit = int(request.query.get("limit", 10))
    token = request.app["henrik_token"]
    if not token:
        raise ValueError("HenrikDev token not configured. Set HENRIKDEV_TOKEN in .env")

    data = await fetch_henrikdev_custom_matches(
        riot_id, region=region, limit=limit, token=token, session=request.app["session"]
    )
    if not data:
        raise ValueError("No custom matches found.")

    rows = []
    for match in data[:10]:
        meta = match.get("metadata", {})
        teams_data = match.get("teams", {})
        red = teams_data.get("red", {}).get("rounds_won", 0)
        blue = teams_data.get("blue", {}).get("rounds_won", 0)
        rows.append(
            [
                templates.esc(meta.get("map", "Unknown")),
                f"{red}-{blue}",
                templates.esc(meta.get("mode", "Custom")),
                templates.esc(meta.get("matchid", "Unknown")[:8]),
            ]
        )

    body = f"""
    <h1>Custom Matches: {templates.esc(riot_id)}</h1>
    <p class="muted">Region: {templates.esc(region.upper())} | Limit: {limit}</p>
    {templates.table(['Map', 'Score', 'Mode', 'Match ID'], rows)}
    """
    return _html(templates.layout(f"Matches {riot_id}", body))


async def _on_startup(app: web.Application) -> None:
    app["session"] = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
    await cache.load()


async def _on_cleanup(app: web.Application) -> None:
    session = app.get("session")
    if session:
        await session.close()
    await app["jobs"].shutdown()
    await cache.persist()


def create_app() -> web.Application:
    app = web.Application(middlewares=[error_middleware])
    app["henrik_token"] = os.getenv("HENRIKDEV_TOKEN", "")
    app["session"] = None
    app["jobs"] = replay_jobs.JobManager()

    app.router.add_get("/", dashboard)
    app.router.add_get("/players/{riot_id}", player_page)
    app.router.add_get("/players/{riot_id}/first-contact", player_first_contact)
    app.router.add_get("/matches/{riot_id}", matches)
    app.router.add_get("/static/exports/{tail:.*}", exports_asset)

    replay_api.setup_routes(app)
    replay_p2.mount(app)
    ai_routes.mount(app)
    app.on_response_prepare.append(_security_headers)

    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)
    return app

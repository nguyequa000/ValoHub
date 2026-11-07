"""HTTP API + page routes for the replay library and 2D viewer.

Implements BUILD_CONTRACT.md §4:

    GET  /api/replays                      library entries
    GET  /api/replays/{id}                 match.json verbatim
    GET  /api/replays/{id}/rounds/{n}      rNN.json (gzip ok)
    PUT  /api/replays/upload?name=f.vrf    raw stream body -> 202 {"jobId","id"}
    POST /api/replays/import-path          {"path": ...} -> 202 {"jobId","id"} (copies, never moves)
    POST /api/replays/{id}/reprocess       re-normalize (re-decode only if the parser changed) -> 202
    GET  /api/demos                        .vrf files in the demos directory
    GET  /api/jobs  /api/jobs/stream       job list / SSE progress
    GET  /api/decoder                      parser commit + supported branches

    /replays, /replay/{id}, /analysis/{id}, /coach/{id}, /providers, /settings → SPA shell (app.html)
    /replays/{id}/view → 302 /replay/{id} (compat) · assets under /static/replays_app
"""

from __future__ import annotations

import asyncio
import functools
import gzip
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import quote
from aiohttp import web

from bot.web import templates
from bot.web.peer import _loopback_peer

from . import jobs as jobs_mod
from . import pipeline, store

MB = 1 << 20


def _json(data, status: int = 200) -> web.Response:
    return web.json_response(data, status=status)


def _error(message, status: int = 400) -> web.Response:
    return web.json_response({"error": str(message)}, status=status)


def _safe_vrf_name(raw) -> str | None:
    """Plain file name (no directories), .vrf suffix, sane length."""
    if not isinstance(raw, str) or not raw:
        return None
    name = Path(raw).name
    if name != raw or len(name) > 200 or name.startswith("."):
        return None
    if not name.lower().endswith(".vrf"):
        return None
    return name


# ------------------------------------------------------------------ library

async def api_list_replays(request: web.Request) -> web.Response:
    entries = await asyncio.to_thread(store.list_replays)
    return _json({"replays": entries})


async def api_replay_match(request: web.Request) -> web.StreamResponse:
    try:
        rdir = store.replay_dir(request.match_info["replay_id"])
    except ValueError:
        return _error("replay not found", 404)
    for path in (rdir / "viewer" / "match.json", rdir / "match.json"):
        if path.is_file():
            return web.FileResponse(path, headers={"Cache-Control": "no-store"})
    return _error(f"replay '{rdir.name}' has no normalized viewer data yet", 404)


async def api_replay_round(request: web.Request) -> web.Response:
    replay_id = request.match_info["replay_id"]
    try:
        n = int(request.match_info["n"])
    except (TypeError, ValueError):
        return _error("round number must be an integer", 400)
    try:
        data = await asyncio.to_thread(store.load_round, replay_id, n)
    except ValueError:
        return _error("replay not found", 404)
    if data is None:
        return _error(f"round {n} not found for replay '{replay_id}'", 404)
    body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    headers = {"Cache-Control": "no-store"}
    if "gzip" in (request.headers.get("Accept-Encoding") or "").lower():
        body = gzip.compress(body, 6)
        headers["Content-Encoding"] = "gzip"
        headers["Vary"] = "Accept-Encoding"
    return web.Response(body=body, content_type="application/json", headers=headers)


# ------------------------------------------------------------------ imports

async def api_upload(request: web.Request) -> web.Response:
    name = _safe_vrf_name(request.query.get("name"))
    if name is None:
        return _error("query parameter 'name' must be a plain .vrf file name", 400)

    manager: jobs_mod.JobManager = request.app["jobs"]
    job = manager.new_job("upload", name)
    part = pipeline.staging_dir() / f"{job.id}.part"
    digest = hashlib.sha256()
    received = 0
    last_emit = 0
    total = request.content_length
    manager.publish(job, "receive", 1, f"receiving {name}…")
    try:
        with part.open("wb") as stream:
            while True:
                chunk = await request.content.readany()
                if not chunk:
                    break
                stream.write(chunk)
                digest.update(chunk)
                received += len(chunk)
                if received - last_emit >= 4 * MB:
                    last_emit = received
                    pct = min(9, int(10 * received / total)) if total else 3
                    manager.publish(job, "receive", pct,
                                    f"receiving {name} · {received / MB:.0f} MB")
    except Exception:
        part.unlink(missing_ok=True)
        manager.publish(job, "error", 0, "upload interrupted", status="error")
        raise
    if received == 0:
        part.unlink(missing_ok=True)
        manager.publish(job, "error", 0, "empty upload body", status="error")
        return _error("empty upload body", 400)

    replay_id = digest.hexdigest()[:16]
    job.replay_id = replay_id
    manager.publish(job, "receive", 10, f"received {received / MB:.1f} MB · id {replay_id}")

    duplicate = pipeline.already_imported(replay_id)
    if duplicate:
        part.unlink(missing_ok=True)
        manager.publish(job, "done", 100, f"already imported ({duplicate})", status="done")
        return _json({"jobId": job.id, "id": replay_id}, 202)

    active = manager.active_for(replay_id)
    if active is not None and active is not job:
        part.unlink(missing_ok=True)
        manager.publish(job, "done", 100, f"already queued as job {active.id}", status="done")
        return _json({"jobId": active.id, "id": replay_id}, 202)

    target = await asyncio.to_thread(pipeline.place_original, part, name, replay_id)
    manager.publish(job, "receive", 10, f"saved as replays/{target.name}")
    runner = functools.partial(pipeline.import_replay, replay_id=replay_id, source_path=target)
    manager.enqueue(job, runner)
    return _json({"jobId": job.id, "id": replay_id}, 202)


async def api_import_path(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
    except Exception:
        return _error("request body must be JSON", 400)
    raw_path = payload.get("path") if isinstance(payload, dict) else None
    if not isinstance(raw_path, str) or not raw_path.strip():
        return _error("'path' is required", 400)

    source = Path(raw_path).expanduser()
    if not source.is_file():
        return _error(f"file not found: {source}", 404)
    if source.suffix.lower() != ".vrf":
        return _error("only .vrf files can be imported", 400)

    manager: jobs_mod.JobManager = request.app["jobs"]
    job = manager.new_job("import-path", source.name)
    part = pipeline.staging_dir() / f"{job.id}.part"
    loop = asyncio.get_running_loop()
    last = [0]
    manager.publish(job, "receive", 1, f"copying {source.name} (source is never moved)…")

    def progress(done: int, total: int | None) -> None:
        if done - last[0] < 8 * MB and done != total:
            return
        last[0] = done
        pct = min(9, int(9 * done / total)) if total else 3
        message = f"copying {source.name} · {done / MB:.0f} MB"
        loop.call_soon_threadsafe(manager.publish, job, "receive", pct, message)

    try:
        digest, size = await asyncio.to_thread(pipeline.copy_and_hash, source, part, progress)
    except OSError as exc:
        part.unlink(missing_ok=True)
        manager.publish(job, "error", 0, str(exc), status="error")
        return _error(f"copy failed: {exc}", 500)

    replay_id = digest[:16]
    job.replay_id = replay_id
    manager.publish(job, "receive", 10, f"copied {size / MB:.1f} MB · id {replay_id}")

    duplicate = pipeline.already_imported(replay_id)
    if duplicate:
        part.unlink(missing_ok=True)
        manager.publish(job, "done", 100, f"already imported ({duplicate})", status="done")
        return _json({"jobId": job.id, "id": replay_id}, 202)

    active = manager.active_for(replay_id)
    if active is not None and active is not job:
        part.unlink(missing_ok=True)
        manager.publish(job, "done", 100, f"already queued as job {active.id}", status="done")
        return _json({"jobId": active.id, "id": replay_id}, 202)

    target = await asyncio.to_thread(pipeline.place_original, part, source.name, replay_id)
    runner = functools.partial(pipeline.import_replay, replay_id=replay_id, source_path=target)
    manager.enqueue(job, runner)
    return _json({"jobId": job.id, "id": replay_id}, 202)


# ------------------------------------------------------------------ reprocess

async def api_reprocess(request: web.Request) -> web.Response:
    """Re-normalize one stored replay (re-decode first only when it must).

    Re-runs the decode only when the parser commit/overlay fingerprint changed
    (or no raw NDJSON is stored); otherwise the stored ``raw/*.ndjson.gz`` is
    re-normalized in place. 202 with the planned mode + reasons.
    """
    replay_id = request.match_info["replay_id"]
    try:
        rdir = store.replay_dir(replay_id)
    except ValueError:
        return _error("replay not found", 404)
    if not rdir.is_dir():
        return _error(f"replay '{replay_id}' is not in the store", 404)

    manager: jobs_mod.JobManager = request.app["jobs"]
    active = manager.active_for(replay_id)
    if active is not None:
        return _json({"jobId": active.id, "id": replay_id, "mode": None,
                      "message": f"already queued as job {active.id}"}, 202)

    try:
        plan = await asyncio.to_thread(pipeline.reprocess_plan, replay_id)
    except (ValueError, OSError) as exc:
        return _error(str(exc), 404)
    if plan["mode"] == "unavailable":
        return _error("cannot reprocess: " + "; ".join(plan["reasons"]), 409)

    job = manager.new_job("reprocess", rdir.name, replay_id=replay_id)
    runner = functools.partial(pipeline.reprocess_replay, replay_id=replay_id)
    manager.enqueue(job, runner)
    return _json({"jobId": job.id, "id": replay_id, "mode": plan["mode"],
                  "reasons": plan["reasons"]}, 202)


# ------------------------------------------------------------------ demos

def demos_dir() -> Path:
    """Where the app reads demos from.

    ``VALOHUB_DEMOS_DIR`` overrides; the default is the standard Riot demos
    folder. The directory is only listed on request — never at startup.
    """
    override = os.getenv("VALOHUB_DEMOS_DIR")
    if override:
        return Path(override).expanduser()
    local = os.getenv("LOCALAPPDATA") or os.path.expanduser("~")
    return Path(local) / "VALORANT" / "Saved" / "Demos"


async def api_demos(request: web.Request) -> web.Response:
    directory = demos_dir()
    include_paths = _loopback_peer(request.remote)

    def scan() -> list[dict]:
        items = []
        try:
            for path in sorted(directory.glob("*.vrf")):
                if path.is_file():
                    item = {"name": path.name, "size": path.stat().st_size}
                    if include_paths:
                        item["path"] = str(path)
                    items.append(item)
        except OSError:
            pass
        return items

    return _json({"demos": await asyncio.to_thread(scan)})


# ------------------------------------------------------------------ jobs

async def api_jobs(request: web.Request) -> web.Response:
    return _json({"jobs": request.app["jobs"].snapshot()})


async def api_jobs_stream(request: web.Request) -> web.StreamResponse:
    manager: jobs_mod.JobManager = request.app["jobs"]
    response = web.StreamResponse(status=200, headers={
        "Content-Type": "text/event-stream; charset=utf-8",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    })
    await response.prepare(request)
    queue = manager.subscribe()

    async def send(event: dict) -> None:
        payload = json.dumps(event, ensure_ascii=False)
        await response.write(f"event: progress\ndata: {payload}\n\n".encode("utf-8"))

    try:
        for event in manager.snapshot_events():
            await send(event)
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=15)
            except (asyncio.TimeoutError, TimeoutError):
                await response.write(b": ping\n\n")
                continue
            await send(event)
    except ConnectionResetError:
        pass
    finally:
        manager.unsubscribe(queue)
    return response


# ------------------------------------------------------------------ decoder

async def api_decoder(request: web.Request) -> web.Response:
    return _json(await asyncio.to_thread(pipeline.decoder_info))


# ------------------------------------------------------------------ pages

def _placeholder(filename: str, title: str, hint: str) -> web.Response:
    body = (
        f"<h1>{templates.esc(title)}</h1>"
        f"<div class='card'><p><strong>Not built yet.</strong> Expected static app file:</p>"
        f"<pre>bot/web/static/replays_app/{templates.esc(filename)}</pre>"
        f"<p class='muted'>{templates.esc(hint)}</p></div>"
    )
    return web.Response(text=templates.layout(title, body), content_type="text/html")


async def replays_page(request: web.Request) -> web.StreamResponse:
    return _spa_response("index.html", "Replay library")


async def replay_view_page(request: web.Request) -> web.StreamResponse:
    """Legacy ``/replays/{id}/view`` -> the SPA's ``/replay/{id}`` (kept for old links and bookmarks)."""
    raise web.HTTPFound(f"/replay/{quote(request.match_info['replay_id'], safe='')}")


def _spa_response(legacy_file: str, title: str) -> web.StreamResponse:
    """``app.html`` (the v2 shell) for every app route; falls back to the pre-v2 page if it is absent."""
    for name in ("app.html", legacy_file):
        page = store.STATIC_REPLAYS_DIR / name
        if page.is_file():
            return web.FileResponse(page, headers={"Cache-Control": "no-store"})
    return _placeholder("app.html", title,
                        "The shell page is not built yet; the JSON API under /api already works.")


def _spa_handler(legacy_file: str, title: str):
    async def page(request: web.Request) -> web.StreamResponse:
        return _spa_response(legacy_file, title)
    return page


async def static_asset(request: web.Request) -> web.StreamResponse:
    tail = request.match_info.get("tail") or ""
    base = store.STATIC_REPLAYS_DIR.resolve()
    candidate = (base / tail).resolve()
    if candidate != base and base not in candidate.parents:
        raise web.HTTPNotFound(text="static asset outside the replays_app directory")
    if candidate.is_file():
        return web.FileResponse(candidate, headers={"Cache-Control": "no-store"})
    raise web.HTTPNotFound(text=f"static asset not found: {tail}")


def setup_routes(app: web.Application) -> None:
    """Register every replay route on the app (pages first, then API)."""
    app.router.add_get("/replays", replays_page)
    app.router.add_get("/replays/", replays_page)
    app.router.add_get("/replays/{replay_id}/view", replay_view_page)
    app.router.add_get("/replay/{replay_id}", _spa_handler("view.html", "Replay viewer"))
    app.router.add_get("/analysis", _spa_handler("analysis.html", "Analysis"))
    app.router.add_get("/analysis/{replay_id}", _spa_handler("analysis.html", "Analysis"))
    app.router.add_get("/coach", _spa_handler("view.html", "Coach"))
    app.router.add_get("/coach/{replay_id}", _spa_handler("view.html", "Coach"))
    app.router.add_get("/providers", _spa_handler("index.html", "AI Providers"))
    app.router.add_get("/settings", _spa_handler("index.html", "Settings"))
    app.router.add_get("/static/replays_app/{tail:.*}", static_asset)

    app.router.add_get("/api/replays", api_list_replays)
    app.router.add_put("/api/replays/upload", api_upload)
    app.router.add_post("/api/replays/import-path", api_import_path)
    app.router.add_post("/api/replays/{replay_id}/reprocess", api_reprocess)
    app.router.add_get("/api/replays/{replay_id}", api_replay_match)
    app.router.add_get("/api/replays/{replay_id}/rounds/{n}", api_replay_round)
    app.router.add_get("/api/demos", api_demos)
    app.router.add_get("/api/jobs", api_jobs)
    app.router.add_get("/api/jobs/stream", api_jobs_stream)
    app.router.add_get("/api/decoder", api_decoder)

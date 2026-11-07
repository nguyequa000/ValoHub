"""Parser administration: local-only update jobs and decoder/quality status."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from aiohttp import web

from bot.web.peer import _loopback_peer
from . import pipeline, store

UPDATER = store.ROOT / "bot" / "replay" / "tools" / "update_parser.py"
UPDATE_STATE = store.WORK_DIR / "parser-update-status.json"
UPDATE_TIMEOUT = 3600


def update_state() -> dict:
    value = store.read_json(UPDATE_STATE)
    return value if isinstance(value, dict) else {}


def decoder_status() -> dict:
    identity = store.parser_fingerprint()
    build = store.build_state()
    recorded = store.recorded_build()
    registered = pipeline.supported_branches()
    tested = recorded["tested"]
    return {"parserCommit": identity["commit"], "overlayHash": identity["overlay"],
            "assemblyHash": identity.get("assembly"),
            "buildState": build["state"], "buildChangedAssemblies": build["changed"],
            "supported": registered, "registeredBranches": registered,
            "replayTested": tested,
            "replayValidatedBranches": sorted({t["branch"] for t in tested
                                               if t["result"] == "validated" and t["branch"]}),
            "lastCheck": update_state().get("lastCheck"),
            "lastResult": update_state().get("lastResult")}


async def _update(job, emit, *, dry_run: bool, requeue_api: str) -> None:
    """Stream the real updater's stdout into the shared SSE job channel."""
    state = {"lastCheck": store.iso_now(),
             "lastResult": {"status": "running", "dryRun": dry_run, "jobId": job.id}}
    store.write_json_atomic(UPDATE_STATE, state)
    command = [sys.executable, "-u", str(UPDATER), "--requeue-api", requeue_api]
    if dry_run:
        command += ["--dry-run", "--no-pull"]
    emit("decode", 2, "checking parser update…")
    lines: list[str] = []
    process = None
    try:
        process = await asyncio.create_subprocess_exec(*command, cwd=str(store.ROOT),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        async def collect():
            assert process.stdout is not None
            async for raw in process.stdout:
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                lines.append(line)
                if len(lines) > 20:
                    lines.pop(0)
                pct = 85 if "selftest=" in line else 60 if "Build succeeded" in line else 35 if "DRY-RUN" in line else 15
                emit("decode", pct, line[:350])
            return await process.wait()
        rc = await asyncio.wait_for(collect(), timeout=UPDATE_TIMEOUT)
    except (asyncio.CancelledError, TimeoutError) as exc:
        if process is not None:
            if process.returncode is None:
                process.kill()
            await process.wait()
        state["lastResult"] = {"status": "failed", "dryRun": dry_run, "jobId": job.id,
                               "checkedAt": store.iso_now(), "message": str(exc) or "cancelled"}
        store.write_json_atomic(UPDATE_STATE, state)
        raise
    except OSError as exc:
        state["lastResult"] = {"status": "failed", "dryRun": dry_run, "jobId": job.id,
                               "checkedAt": store.iso_now(), "message": str(exc)[:350]}
        store.write_json_atomic(UPDATE_STATE, state)
        raise
    success = rc == 0
    message = ("dry-run complete; active parser unchanged" if dry_run and rc == 0 else
               next((line for line in reversed(lines) if line.startswith(("update_status=", "no_op="))),
                    lines[-1] if lines else f"updater exit {rc}"))
    outcome = "ready" if success else "failed"
    state["lastResult"] = {"status": outcome, "dryRun": dry_run, "jobId": job.id,
                           "checkedAt": store.iso_now(), "message": message[:350]}
    store.write_json_atomic(UPDATE_STATE, state)
    if not success:
        raise RuntimeError(f"parser update failed (exit {rc}): {message}")
    emit("done", 100, "parser update check ready" if dry_run else "parser update ready")


async def update_parser(request: web.Request) -> web.Response:
    if not _loopback_peer(request.remote):
        raise web.HTTPForbidden(text="local access only")
    if request.app.get("desktop"):
        return web.json_response({"error": "disabled in the offline desktop build"}, status=403)
    try:
        payload = await request.json() if request.content_length else {}
    except (ValueError, json.JSONDecodeError):
        raise web.HTTPBadRequest(text="body must be JSON")
    if not isinstance(payload, dict) or set(payload) - {"dryRun"} or type(payload.get("dryRun", False)) is not bool:
        raise web.HTTPBadRequest(text="only a boolean dryRun option is supported")
    active = request.app["parser_update"]["job"]
    if active is not None and active.status in ("queued", "running"):
        return web.json_response({"error": "parser update already running", "jobId": active.id}, status=409)
    manager = request.app["jobs"]
    job = manager.new_job("decoder-update", "parser")
    job.status = "queued"
    request.app["parser_update"]["job"] = job
    base = f"http://127.0.0.1:{request.url.port or 80}"
    manager.enqueue(job, lambda j, emit: _update(j, emit, dry_run=payload.get("dryRun", False), requeue_api=base))
    return web.json_response({"jobId": job.id, "status": "queued"}, status=202)


@web.middleware
async def decoder_metadata(request: web.Request, handler):
    response = await handler(request)
    if request.method == "GET" and request.path == "/api/decoder" and response.status == 200:
        data = json.loads(response.text)
        data.update(await asyncio.to_thread(decoder_status))
        data["updaterEnabled"] = not request.app.get("desktop")
        return web.json_response(data)
    if request.method == "GET" and request.path == "/api/replays" and response.status == 200:
        data = json.loads(response.text)
        for replay in data.get("replays", []):
            if replay.get("status") == "suspect":
                manifest = store.read_manifest(replay["id"]) or {}
                replay["suspectReason"] = manifest.get("densityReason") or manifest.get("notes")
        return web.json_response(data)
    return response


async def replay_quality(request: web.Request) -> web.Response:
    replay_id = request.match_info["replay_id"]
    try:
        manifest = await asyncio.to_thread(store.read_manifest, replay_id)
    except ValueError:
        raise web.HTTPNotFound(text="invalid replay id")
    if not manifest:
        raise web.HTTPNotFound(text="replay not found")
    return web.json_response({"status": manifest.get("status"),
                              "suspectReason": manifest.get("densityReason") or manifest.get("notes")
                              if manifest.get("status") == "suspect" else None,
                              "quality": store.quality_view(manifest)})


def mount(app: web.Application) -> None:
    app["parser_update"] = {"job": None}
    app.router.add_post("/api/decoder/update", update_parser)
    app.router.add_get("/api/replays/{replay_id}/quality", replay_quality)
    app.middlewares.append(decoder_metadata)

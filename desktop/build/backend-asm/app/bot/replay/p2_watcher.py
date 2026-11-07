"""P2 demos-folder watcher and parser-update recovery hook.

The watcher is disabled by default.  When enabled it polls ``VALO_DEMOS_DIR``
(the standard VALORANT demos directory is only a configuration fallback), waits
for a ``*.vrf`` size to be unchanged across two scans, hashes it, and queues the
same copy-then-import pipeline used by ``POST /api/replays/import-path``.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from aiohttp import web

from . import jobs as jobs_mod
from . import pipeline, store

LOG = logging.getLogger("bot.replay.p2_watcher")
STATE_KEY = "p2_watcher"
POLL_SECONDS = 10.0
MAX_IMPORTED = 50


def _env_enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def demos_dir() -> Path:
    """Configured demos directory without reading or enumerating it."""
    override = os.getenv("VALO_DEMOS_DIR")
    if override:
        return Path(override).expanduser()
    local = os.getenv("LOCALAPPDATA") or os.path.expanduser("~")
    return Path(local) / "VALORANT" / "Saved" / "Demos"


def _stored_hash(replay_id: str) -> bool:
    """Whether the hash-derived replay id already has any store state."""
    try:
        rdir = store.replay_dir(replay_id)
    except ValueError:
        return True
    if not rdir.is_dir():
        return False
    return any(
        path.exists()
        for path in (rdir / "manifest.json", rdir / "viewer" / "match.json", rdir / "match.json")
    )


@dataclass(frozen=True)
class EnqueueResult:
    replay_id: str
    digest: str
    job_id: str
    queued: bool


async def _enqueue_import_path(app: web.Application, source: Path) -> EnqueueResult:
    """Copy and enqueue ``source`` using the import-path endpoint semantics."""
    source = Path(source)
    manager: jobs_mod.JobManager = app["jobs"]
    job = manager.new_job("import-path", source.name)
    part = pipeline.staging_dir() / f"{job.id}.part"
    manager.publish(job, "receive", 1, f"copying {source.name} (source is never moved)…")

    try:
        digest, size = await asyncio.to_thread(pipeline.copy_and_hash, source, part)
    except OSError as exc:
        part.unlink(missing_ok=True)
        job.error = str(exc)
        manager.publish(job, "error", 0, str(exc), status="error")
        raise

    replay_id = digest[:16]
    job.replay_id = replay_id
    manager.publish(job, "receive", 10, f"copied {size / (1 << 20):.1f} MB · id {replay_id}")

    # Watcher dedupe is deliberately stricter than the manual endpoint: any
    # store state for this content belongs to startup recovery, not a new file
    # discovery.  The full digest is also retained in memory by WatcherState.
    duplicate = _stored_hash(replay_id) or pipeline.already_imported(replay_id)
    if duplicate:
        part.unlink(missing_ok=True)
        reason = duplicate if isinstance(duplicate, str) else "hash already present in replay store"
        manager.publish(job, "done", 100, str(reason), status="done")
        return EnqueueResult(replay_id, digest, job.id, False)

    active = manager.active_for(replay_id)
    if active is not None and active is not job:
        part.unlink(missing_ok=True)
        manager.publish(job, "done", 100, f"already queued as job {active.id}", status="done")
        return EnqueueResult(replay_id, digest, active.id, False)

    target = await asyncio.to_thread(pipeline.place_original, part, source.name, replay_id)
    runner = functools.partial(pipeline.import_replay, replay_id=replay_id, source_path=target)
    manager.publish(job, "receive", 10, f"queued from {source.name}", status="queued")
    manager.enqueue(job, runner)
    return EnqueueResult(replay_id, digest, job.id, True)


def _scan_files(directory: Path) -> list[tuple[Path, int, int]]:
    items: list[tuple[Path, int, int]] = []
    try:
        for path in sorted(directory.glob("*.vrf"), key=lambda item: item.name.lower()):
            try:
                if path.is_file():
                    stat = path.stat()
                    items.append((path, stat.st_size, stat.st_mtime_ns))
            except OSError:
                continue
    except OSError:
        return []
    return items


def _transform_patch_failure(notes: object) -> bool:
    text = notes.lower() if isinstance(notes, str) else ""
    if "invalidseek" in text or "header" in text:
        return False
    return "transform" in text or "patch" in text


def _manifest_source(manifest: dict) -> Path | None:
    raw = manifest.get("source")
    if not isinstance(raw, str) or not raw.strip():
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = store.resolve_source(path)
    try:
        return path.resolve()
    except OSError:
        return path


class WatcherState:
    def __init__(self, app: web.Application, *, interval: float = POLL_SECONDS):
        self.app = app
        self.directory = demos_dir()
        self.interval = interval
        self.enabled = _env_enabled("VALO_WATCHER")
        self.last_scan_at: str | None = None
        self.watched = 0
        self._sizes: dict[str, int] = {}
        self._known: dict[str, tuple[int, int, str]] = {}
        self._digests: set[str] = set()
        self._pending: dict[str, dict] = {}
        self._imported: list[dict] = []
        self._task: asyncio.Task | None = None
        self._toggle_lock = asyncio.Lock()

    async def startup(self) -> None:
        if _env_enabled("VALO_AUTO_REQUEUE"):
            await self.auto_requeue()
        if self.enabled:
            await self.set_enabled(True, force=True)

    async def close(self) -> None:
        await self.set_enabled(False, force=True)

    async def set_enabled(self, enabled: bool, *, force: bool = False) -> None:
        async with self._toggle_lock:
            enabled = bool(enabled)
            if not force and enabled == self.enabled:
                return
            self.enabled = enabled
            if not enabled:
                task, self._task = self._task, None
                if task is not None:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
                return
            if self._task is None or self._task.done():
                self._task = asyncio.create_task(self._poll_loop(), name="valo-demos-watcher")

    async def _poll_loop(self) -> None:
        while self.enabled:
            try:
                await self.scan_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - a bad file must not stop later scans
                LOG.exception("demos watcher scan failed")
            await asyncio.sleep(self.interval)

    def _refresh_pending(self) -> None:
        manager: jobs_mod.JobManager = self.app["jobs"]
        for job_id, item in list(self._pending.items()):
            job = manager.get(job_id)
            if job is None or job.status in {"running", "queued"}:
                continue
            self._pending.pop(job_id, None)
            if job.status == "done":
                self._imported.append({
                    "id": item["id"],
                    "name": item["name"],
                    "at": store.iso_now(),
                })
                if len(self._imported) > MAX_IMPORTED:
                    del self._imported[:-MAX_IMPORTED]

    async def status(self) -> dict:
        self._refresh_pending()
        return {
            "enabled": self.enabled,
            "dir": str(self.directory),
            "watched": self.watched,
            "lastScanAt": self.last_scan_at,
            "queued": [item["id"] for item in self._pending.values()],
            "imported": list(self._imported),
        }

    async def scan_once(self) -> None:
        """Run one deterministic scan; exposed for fixture tests."""
        items = await asyncio.to_thread(_scan_files, self.directory)
        self.watched = len(items)
        current = {str(path.resolve()) for path, _, _ in items}
        for key in list(self._sizes):
            if key not in current:
                self._sizes.pop(key, None)
                self._known.pop(key, None)

        for path, size, mtime_ns in items:
            key = str(path.resolve())
            previous = self._sizes.get(key)
            self._sizes[key] = size
            if previous is None or previous != size:
                continue

            known = self._known.get(key)
            if known is not None and known[:2] == (size, mtime_ns):
                digest = known[2]
            else:
                try:
                    digest, _ = await asyncio.to_thread(store.sha256_file, path)
                except OSError:
                    continue
                self._known[key] = (size, mtime_ns, digest)
            replay_id = digest[:16]
            if digest in self._digests or _stored_hash(replay_id):
                self._digests.add(digest)
                continue
            if any(item["id"] == replay_id for item in self._pending.values()):
                self._digests.add(digest)
                continue

            result = await _enqueue_import_path(self.app, path)
            self._digests.add(result.digest)
            if result.queued:
                self._pending[result.job_id] = {
                    "id": result.replay_id,
                    "name": path.name,
                }

        self.last_scan_at = store.iso_now()
        self._refresh_pending()

    async def auto_requeue(self) -> list[str]:
        """Queue each eligible failed transform/patch decode once per parser commit."""
        manager: jobs_mod.JobManager = self.app["jobs"]
        supported = set(await asyncio.to_thread(pipeline.supported_branches))
        parser_token = store.parser_commit() or "unknown"
        queued: list[str] = []
        try:
            replay_dirs = sorted(path for path in store.DATA_REPLAYS_DIR.iterdir() if path.is_dir())
        except OSError:
            return queued

        for rdir in replay_dirs:
            replay_id = rdir.name
            try:
                manifest = store.read_manifest(replay_id) or {}
            except ValueError:
                continue
            if manifest.get("status") != "failed":
                continue
            if manifest.get("branch") not in supported:
                continue
            notes = manifest.get("notes") or manifest.get("error")
            if not _transform_patch_failure(notes):
                continue
            if manifest.get("autoRequeueParserCommit") == parser_token:
                continue
            source = _manifest_source(manifest)
            if source is None or not source.is_file():
                continue
            if manager.active_for(replay_id) is not None:
                continue

            job = manager.new_job("auto-requeue", source.name, replay_id=replay_id)
            store.update_manifest(
                replay_id,
                autoRequeueParserCommit=parser_token,
                autoRequeueAttemptedAt=store.iso_now(),
            )
            runner = functools.partial(pipeline.import_replay, replay_id=replay_id, source_path=source)
            manager.publish(job, "receive", 10, "auto re-queued after parser transform update", status="queued")
            manager.enqueue(job, runner)
            self._pending[job.id] = {"id": replay_id, "name": source.name}
            queued.append(replay_id)
        return queued


async def _startup(app: web.Application) -> None:
    await app[STATE_KEY].startup()


async def _cleanup(app: web.Application) -> None:
    await app[STATE_KEY].close()


async def _status(request: web.Request) -> web.Response:
    return web.json_response(await request.app[STATE_KEY].status())


async def _toggle(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
    except Exception:
        return web.json_response({"error": "request body must be JSON"}, status=400)
    enabled = payload.get("enabled") if isinstance(payload, dict) else None
    if not isinstance(enabled, bool):
        return web.json_response({"error": "'enabled' must be a boolean"}, status=400)
    state: WatcherState = request.app[STATE_KEY]
    await state.set_enabled(enabled)
    return web.json_response(await state.status())


def mount(app: web.Application) -> None:
    """Register watcher lifecycle hooks and the frozen P2 routes."""
    app[STATE_KEY] = WatcherState(app)
    app.router.add_get("/api/watcher/status", _status)
    app.router.add_post("/api/watcher/toggle", _toggle)
    app.on_startup.append(_startup)
    app.on_cleanup.append(_cleanup)

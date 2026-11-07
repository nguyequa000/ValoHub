"""In-process job queue + SSE fan-out for replay imports.

One asyncio worker runs queued imports sequentially (decode is the heavy step);
progress events are published to every ``/api/jobs/stream`` subscriber.

Stage vocabulary is fixed by BUILD_CONTRACT.md §4:
``receive | container | decode | normalize | done | error``.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field

STAGES = ("receive", "container", "decode", "normalize", "done", "error")


@dataclass
class Job:
    id: str
    kind: str                 # "upload" | "import-path"
    file: str
    replay_id: str | None = None
    stage: str = "receive"
    pct: int = 0
    message: str = ""
    status: str = "running"   # running | queued | done | error
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def as_dict(self) -> dict:
        from .store import iso_from_ts

        return {
            "id": self.id,
            "kind": self.kind,
            "file": self.file,
            "replayId": self.replay_id,
            "stage": self.stage,
            "pct": self.pct,
            "message": self.message,
            "status": self.status,
            "error": self.error,
            "createdAt": iso_from_ts(self.created_at),
            "updatedAt": iso_from_ts(self.updated_at),
        }

    def event(self) -> dict:
        """SSE payload: contract keys (stage/file/pct/message) + correlation ids."""
        return {
            "jobId": self.id,
            "replayId": self.replay_id,
            "file": self.file,
            "stage": self.stage,
            "pct": self.pct,
            "message": self.message,
            "status": self.status,
        }


class JobManager:
    def __init__(self, keep: int = 50):
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._keep = keep
        self._subscribers: set[asyncio.Queue] = set()
        self._queue: asyncio.Queue | None = None
        self._worker: asyncio.Task | None = None

    # ---- registry -------------------------------------------------------

    def new_job(self, kind: str, file: str, replay_id: str | None = None) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], kind=kind, file=file, replay_id=replay_id)
        self._jobs[job.id] = job
        self._order.append(job.id)
        while len(self._order) > self._keep:
            self._jobs.pop(self._order.pop(0), None)
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def active_for(self, replay_id: str) -> Job | None:
        for jid in reversed(self._order):
            job = self._jobs[jid]
            if job.replay_id == replay_id and job.status in ("running", "queued"):
                return job
        return None

    def snapshot(self) -> list[dict]:
        return [self._jobs[jid].as_dict() for jid in reversed(self._order)]

    def snapshot_events(self, limit: int = 10) -> list[dict]:
        """Most recent job states in SSE event shape (for stream subscribers)."""
        ids = list(reversed(self._order))[:limit]
        return [self._jobs[jid].event() for jid in ids]

    # ---- progress + fan-out --------------------------------------------

    def publish(self, job: Job, stage: str, pct, message: str, status: str | None = None) -> None:
        job.stage = stage
        try:
            job.pct = max(0, min(100, int(pct)))
        except (TypeError, ValueError):
            pass
        job.message = str(message)
        if status:
            job.status = status
        elif stage == "done":
            job.status = "done"
        elif stage == "error":
            job.status = "error"
        job.updated_at = time.time()
        event = job.event()
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def emitter(self, job: Job):
        return lambda stage, pct, message, status=None: self.publish(job, stage, pct, message, status)

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    # ---- worker ---------------------------------------------------------

    def enqueue(self, job: Job, runner) -> None:
        """Queue ``runner(job, emit)`` (an async callable) for sequential run."""
        if self._queue is None:
            self._queue = asyncio.Queue()
        self._ensure_worker()
        self._queue.put_nowait((job, runner))

    def _ensure_worker(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.get_running_loop().create_task(self._work())

    async def _work(self) -> None:
        assert self._queue is not None
        while True:
            job, runner = await self._queue.get()
            if job.status in ("done", "error"):
                continue
            job.status = "running"
            job.updated_at = time.time()
            emit = self.emitter(job)
            try:
                await runner(job, emit)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — surface any failure as a job error
                job.error = str(exc)
                self.publish(job, "error", job.pct, str(exc), status="error")

    async def shutdown(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            try:
                await self._worker
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._worker = None

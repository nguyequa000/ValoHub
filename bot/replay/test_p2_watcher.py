from __future__ import annotations

import asyncio
import os
import shutil
import uuid
import unittest
from pathlib import Path
from unittest import mock

from aiohttp import web

from bot.replay import jobs, p2_watcher, store


class FakeManager:
    def __init__(self):
        self.items: dict[str, jobs.Job] = {}
        self.enqueued: list[tuple[jobs.Job, object]] = []
        self._counter = 0

    def new_job(self, kind: str, file: str, replay_id: str | None = None) -> jobs.Job:
        self._counter += 1
        job = jobs.Job(f"job-{self._counter}", kind, file, replay_id=replay_id)
        self.items[job.id] = job
        return job

    def publish(self, job, stage, pct, message, status=None):
        job.stage = stage
        job.pct = int(pct)
        job.message = str(message)
        if status:
            job.status = status

    def enqueue(self, job, runner):
        self.enqueued.append((job, runner))

    def active_for(self, replay_id: str):
        for job in self.items.values():
            if job.replay_id == replay_id and job.status in {"running", "queued"}:
                return job
        return None

    def get(self, job_id: str):
        return self.items.get(job_id)


class WatcherTests(unittest.TestCase):
    def setUp(self):
        self.fixture = store.TMP_DIR / f"p2-watcher-test-{uuid.uuid4().hex[:8]}"
        self.fixture.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.fixture, ignore_errors=True)

    def test_stable_fixture_enqueues_once_then_dedupes(self):
        source = self.fixture / "new-demo.vrf"
        source.write_bytes(b"fixture-vrf-ish\x00payload")
        digest, _ = store.sha256_file(source)
        calls: list[Path] = []
        manager = FakeManager()
        app = {"jobs": manager}

        async def fake_enqueue(_app, path):
            calls.append(Path(path))
            return p2_watcher.EnqueueResult(digest[:16], digest, "fixture-job", True)

        async def exercise():
            with mock.patch.dict(os.environ, {"VALO_DEMOS_DIR": str(self.fixture)}, clear=False):
                state = p2_watcher.WatcherState(app, interval=0.01)
                with mock.patch.object(p2_watcher, "_enqueue_import_path", fake_enqueue):
                    await state.scan_once()  # first observation: not stable yet
                    self.assertEqual(calls, [])
                    await state.scan_once()  # stable across two scans: enqueue
                    self.assertEqual(calls, [source])
                    await state.scan_once()  # digest remembered: dedupe
                    self.assertEqual(calls, [source])
                status = await state.status()
                self.assertEqual(status["watched"], 1)
                self.assertEqual(status["queued"], [digest[:16]])
                self.assertEqual(status["imported"], [])
                self.assertIsNotNone(status["lastScanAt"])
                self.assertTrue(source.is_file(), "watcher must never move the source")

        asyncio.run(exercise())

    def test_unchanged_stable_fixture_hashes_exactly_once(self):
        source = self.fixture / "one-hash.vrf"
        source.write_bytes(b"unchanged-fixture")
        digest = "d" * 64
        manager = FakeManager()
        app = {"jobs": manager}

        async def fake_enqueue(_app, path):
            self.assertEqual(Path(path), source)
            return p2_watcher.EnqueueResult(digest[:16], digest, "fixture-job", True)

        async def exercise():
            with (
                mock.patch.dict(os.environ, {"VALO_DEMOS_DIR": str(self.fixture)}, clear=False),
                mock.patch.object(p2_watcher.store, "sha256_file", return_value=(digest, source.stat().st_size)) as sha256_file,
                mock.patch.object(p2_watcher, "_stored_hash", return_value=False),
                mock.patch.object(p2_watcher, "_enqueue_import_path", fake_enqueue),
            ):
                state = p2_watcher.WatcherState(app, interval=0.01)
                await state.scan_once()  # first observation: not stable yet
                await state.scan_once()  # stable: hash and enqueue
                await state.scan_once()  # unchanged: cached digest
                await state.scan_once()  # unchanged: cached digest
                self.assertEqual(sha256_file.call_count, 1)

        asyncio.run(exercise())

    def test_import_path_copy_keeps_source_and_enqueues_pipeline(self):
        source = self.fixture / "copy-source.vrf"
        source.write_bytes(b"copy-never-move")
        staging = self.fixture / "incoming"
        originals = self.fixture / "originals"
        data_root = self.fixture / "store"
        manager = FakeManager()
        app = {"jobs": manager}

        async def exercise():
            with (
                mock.patch.object(store, "DATA_REPLAYS_DIR", data_root),
                mock.patch.object(store, "ORIGINALS_DIR", originals),
                mock.patch.object(p2_watcher.pipeline, "staging_dir", return_value=staging),
            ):
                result = await p2_watcher._enqueue_import_path(app, source)
                self.assertTrue(result.queued)
                self.assertTrue(source.is_file(), "the demos source must remain in place")
                copied = originals / source.name
                self.assertTrue(copied.is_file())
                self.assertEqual(copied.read_bytes(), source.read_bytes())
                self.assertEqual(len(manager.enqueued), 1)
                self.assertEqual(manager.enqueued[0][0].replay_id, result.replay_id)

        asyncio.run(exercise())

    def test_auto_requeue_filters_and_runs_once_per_parser_commit(self):
        data_root = self.fixture / "data"
        source = self.fixture / "original.vrf"
        source.write_bytes(b"source")
        supported = "++Ares-Core+release-13.07"
        cases = {
            "eligible": {"status": "failed", "branch": supported, "notes": "missing transform for patch 13.07"},
            "header": {"status": "failed", "branch": supported, "notes": "InvalidSeek in replay header patch"},
            "unsupported": {"status": "failed", "branch": "++Ares-Core+release-9.99", "notes": "patch transform missing"},
            "ready": {"status": "ready", "branch": supported, "notes": "patch transform missing"},
        }
        for replay_id, manifest in cases.items():
            manifest = dict(manifest, id=replay_id, source=str(source))
            store.write_json_atomic(data_root / replay_id / "manifest.json", manifest)

        manager = FakeManager()
        app = {"jobs": manager}

        async def exercise():
            with (
                mock.patch.object(store, "DATA_REPLAYS_DIR", data_root),
                mock.patch.object(store, "parser_commit", return_value="commit-test"),
                mock.patch.object(p2_watcher.pipeline, "supported_branches", return_value=[supported]),
            ):
                state = p2_watcher.WatcherState(app)
                first = await state.auto_requeue()
                second = await state.auto_requeue()
                self.assertEqual(first, ["eligible"])
                self.assertEqual(second, [])
                self.assertEqual(len(manager.enqueued), 1)
                manifest = store.read_json(data_root / "eligible" / "manifest.json")
                self.assertEqual(manifest["autoRequeueParserCommit"], "commit-test")

        asyncio.run(exercise())

    def test_mount_registers_contract_routes_and_hooks(self):
        with mock.patch.dict(os.environ, {"VALO_DEMOS_DIR": str(self.fixture)}, clear=False):
            app = web.Application()
            app["jobs"] = FakeManager()
            p2_watcher.mount(app)
            resources = {resource.canonical for resource in app.router.resources()}
            self.assertIn("/api/watcher/status", resources)
            self.assertIn("/api/watcher/toggle", resources)
            self.assertIn(p2_watcher._startup, app.on_startup)
            self.assertIn(p2_watcher._cleanup, app.on_cleanup)


if __name__ == "__main__":
    unittest.main()

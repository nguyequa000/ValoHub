"""P3/P2-C: store freshness, parser fingerprint and the reprocess path.

Covers `bot.replay.store.freshness` / `parser_fingerprint`, the
`pipeline.reprocess_plan` decision (decode only when the parser fingerprint
changed; otherwise re-normalize from stored raw) and the
`POST /api/replays/{id}/reprocess` endpoint on a sandboxed store.
"""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from bot.replay import api as replay_api
from bot.replay import jobs as jobs_mod
from bot.replay import pipeline, store

UPSTREAM = "cafe0000cafe0000cafe0000cafe0000cafe0000cafe0000cafe0000cafe0000"
OVERLAY = "beef0000beef0000beef0000beef0000beef0000beef0000beef0000beef0000"
CURRENT = {"commit": UPSTREAM, "overlay": OVERLAY}


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Isolated store + work dir; parser-commit.txt in the key=value format."""
    work = tmp_path / "VALOPLANT_WORK"
    work.mkdir()
    (work / "parser-commit.txt").write_text(
        f"upstream_sha={UPSTREAM}\noverlay_patch_sha256={OVERLAY}\n", encoding="utf-8")
    data = tmp_path / "data" / "replays"
    data.mkdir(parents=True)
    monkeypatch.setattr(store, "DATA_REPLAYS_DIR", data)
    monkeypatch.setattr(store, "WORK_DIR", work)
    monkeypatch.setattr(store, "ROOT", tmp_path)
    return {"root": tmp_path, "data": data, "work": work}


def make_store(sandbox, rid, *, match_schema=store.VIEWER_SCHEMA, manifest_schema=store.STORE_SCHEMA,
               parser_fp=None, source=None, with_raw=False, with_match=True):
    rdir = sandbox["data"] / rid
    rdir.mkdir(parents=True, exist_ok=True)
    manifest = {"schema": manifest_schema, "id": rid, "status": "ready", "source": source}
    if parser_fp is not None:
        manifest["parser"] = parser_fp
    (rdir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    if with_match:
        (rdir / "viewer").mkdir(exist_ok=True)
        (rdir / "viewer" / "match.json").write_text(
            json.dumps({"schema": match_schema, "rounds": [{"n": 1}]}), encoding="utf-8")
    if with_raw:
        raw = rdir / "raw"
        raw.mkdir(exist_ok=True)
        (raw / "events.ndjson.gz").write_bytes(b"gz")
    return rdir


def make_source(sandbox, name="match.vrf"):
    replays = sandbox["root"] / "replays"
    replays.mkdir(exist_ok=True)
    target = replays / name
    target.write_bytes(b"vrf")
    return target


# ------------------------------------------------------------------ fingerprint

def test_parser_fingerprint_keyvalue(sandbox):
    assert store.parser_fingerprint() == CURRENT
    assert store.parser_commit() == UPSTREAM


def test_parser_fingerprint_legacy_single_line(sandbox):
    (sandbox["work"] / "parser-commit.txt").write_text("abc123\n", encoding="utf-8")
    assert store.parser_fingerprint() == {"commit": "abc123", "overlay": None}


def test_parser_fingerprint_missing_file(sandbox):
    (sandbox["work"] / "parser-commit.txt").unlink()
    assert store.parser_fingerprint() == {"commit": None, "overlay": None}


def test_recorded_decode_fingerprint_shapes():
    assert store.recorded_decode_fingerprint({"parser": dict(CURRENT)}) == CURRENT
    assert store.recorded_decode_fingerprint({"parserCommit": "old"}) == {"commit": "old", "overlay": None}
    assert store.recorded_decode_fingerprint(None) == {"commit": None, "overlay": None}


# ------------------------------------------------------------------ freshness

def test_fresh_store_is_not_stale(sandbox):
    make_store(sandbox, "fresh1", parser_fp=dict(CURRENT))
    fresh = store.freshness("fresh1")
    assert fresh["state"] == "fresh", fresh
    assert fresh["reasons"] == []
    entry = next(e for e in store.list_replays() if e["id"] == "fresh1")
    assert entry["stale"] is False and entry["staleReasons"] == []


def test_pre_p2_store_is_stale_by_schema_and_fingerprint(sandbox):
    make_store(sandbox, "old1", match_schema="valoplant_match/v1",
               manifest_schema="valoplant_manifest/v1")
    fresh = store.freshness("old1")
    assert fresh["state"] == "stale", fresh
    assert any("older than" in reason for reason in fresh["reasons"]), fresh["reasons"]
    assert fresh["decodeRequired"] is True
    entry = next(e for e in store.list_replays() if e["id"] == "old1")
    assert entry["stale"] is True and entry["staleReasons"], entry


def test_same_schema_but_changed_parser_is_stale(sandbox):
    make_store(sandbox, "fp1", parser_fp={"commit": "older", "overlay": None})
    fresh = store.freshness("fp1")
    assert fresh["state"] == "stale", fresh
    assert fresh["decodeRequired"] is True


def test_unchanged_fingerprint_is_fresh(sandbox):
    make_store(sandbox, "fp2", parser_fp=dict(CURRENT))
    assert store.freshness("fp2")["decodeRequired"] is False


def test_missing_viewer_data_and_synthetic(sandbox):
    make_store(sandbox, "nomat", with_match=False)
    fresh = store.freshness("nomat")
    assert fresh["state"] == "missing", fresh
    entry = next(e for e in store.list_replays() if e["id"] == "nomat")
    assert entry["stale"] is False  # 'missing' is reported through status, not stale

    make_store(sandbox, "demo1", source="synthetic")
    fresh = store.freshness("demo1")
    assert fresh["state"] == "synthetic" and fresh["reasons"] == []


def test_freshness_absent(sandbox):
    assert store.freshness("nothere")["state"] == "absent"


# ------------------------------------------------------------------ reprocess_plan

def test_plan_decode_when_fingerprint_changed(sandbox):
    make_store(sandbox, "plan1", parser_fp={"commit": "older", "overlay": None},
               with_raw=True, source="replays/match.vrf")
    make_source(sandbox)
    plan = pipeline.reprocess_plan("plan1")
    assert plan["mode"] == "decode", plan
    assert plan["fingerprintChanged"] is True


def test_plan_normalize_when_fingerprint_unchanged(sandbox):
    make_store(sandbox, "plan2", parser_fp=dict(CURRENT), with_raw=True)
    plan = pipeline.reprocess_plan("plan2")
    assert plan["mode"] == "normalize", plan
    assert plan["reasons"] == []


def test_plan_normalize_when_source_missing_but_raw_present(sandbox):
    make_store(sandbox, "plan3", parser_fp={"commit": "older", "overlay": None}, with_raw=True)
    plan = pipeline.reprocess_plan("plan3")
    assert plan["mode"] == "normalize", plan
    assert any("original replay missing" in reason for reason in plan["reasons"]), plan


def test_plan_decode_when_raw_missing(sandbox):
    make_store(sandbox, "plan4", parser_fp=dict(CURRENT), with_raw=False,
               source="replays/match.vrf")
    make_source(sandbox)
    plan = pipeline.reprocess_plan("plan4")
    assert plan["mode"] == "decode", plan
    assert any("no raw NDJSON" in reason for reason in plan["reasons"]), plan


def test_plan_unavailable_without_raw_and_source(sandbox):
    make_store(sandbox, "plan5", parser_fp={"commit": "older", "overlay": None})
    plan = pipeline.reprocess_plan("plan5")
    assert plan["mode"] == "unavailable", plan


def test_plan_synthetic_unavailable(sandbox):
    make_store(sandbox, "plan6", source="synthetic")
    assert pipeline.reprocess_plan("plan6")["mode"] == "unavailable"


# ------------------------------------------------------------------ API

class ReprocessApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        work = root / "VALOPLANT_WORK"
        work.mkdir()
        (work / "parser-commit.txt").write_text(
            f"upstream_sha={UPSTREAM}\noverlay_patch_sha256={OVERLAY}\n", encoding="utf-8")
        data = root / "data" / "replays"
        data.mkdir(parents=True)
        for target, value in (("DATA_REPLAYS_DIR", data), ("WORK_DIR", work), ("ROOT", root)):
            patcher = patch.object(store, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.sandbox = {"root": root, "data": data, "work": work}
        self.manager = jobs_mod.JobManager()
        self.addCleanup(self._drop_manager)

    def _drop_manager(self):
        if self.manager._worker is not None and not self.manager._worker.done():
            self.manager._worker.cancel()
        self._tmp.cleanup()

    def _request(self, rid):
        return SimpleNamespace(match_info={"replay_id": rid},
                               app={"jobs": self.manager})

    async def _wait_done(self):
        for _ in range(100):
            await asyncio.sleep(0.01)
            if self.manager.snapshot()[0]["status"] == "done":
                return
        self.fail(f"job did not finish: {self.manager.snapshot()}")

    async def test_reprocess_queues_normalize_job(self):
        make_store(self.sandbox, "api1", parser_fp=dict(CURRENT), with_raw=True)

        async def fake_reprocess(job, emit, *, replay_id):
            emit("done", 100, "stubbed")

        with patch.object(pipeline, "reprocess_replay", AsyncMock(side_effect=fake_reprocess)):
            response = await replay_api.api_reprocess(self._request("api1"))
            self.assertEqual(response.status, 202)
            payload = json.loads(response.body)
            self.assertEqual(payload["id"], "api1")
            self.assertEqual(payload["mode"], "normalize")
            await self._wait_done()
        jobs = self.manager.snapshot()
        self.assertEqual(jobs[0]["kind"], "reprocess")
        self.assertEqual(jobs[0]["replayId"], "api1")

    async def test_reprocess_reports_decode_mode(self):
        make_store(self.sandbox, "api2", parser_fp={"commit": "older", "overlay": None},
                   with_raw=True, source="replays/match.vrf")
        make_source(self.sandbox)
        with patch.object(pipeline, "reprocess_replay", AsyncMock()):
            response = await replay_api.api_reprocess(self._request("api2"))
            self.assertEqual(response.status, 202)
            payload = json.loads(response.body)
            self.assertEqual(payload["mode"], "decode")

    async def test_reprocess_404_and_409(self):
        with patch.object(pipeline, "reprocess_replay", AsyncMock()):
            response = await replay_api.api_reprocess(self._request("missing"))
            self.assertEqual(response.status, 404)

        make_store(self.sandbox, "api3", source="synthetic")
        with patch.object(pipeline, "reprocess_replay", AsyncMock()):
            response = await replay_api.api_reprocess(self._request("api3"))
            self.assertEqual(response.status, 409)
            self.assertIn("cannot reprocess", json.loads(response.body)["error"])

    async def test_reprocess_decode_sets_ready_before_normalize(self):
        make_store(self.sandbox, "api5", parser_fp={"commit": "older", "overlay": None},
                   with_raw=True, source="replays/match.vrf")
        make_source(self.sandbox)
        seen = {}

        async def fake_decode(replay_id, source, raw_dir, emit):
            seen["at_decode"] = store.read_manifest(replay_id).get("status")

        async def fake_normalize(replay_id, raw_dir, viewer_dir, emit):
            seen["at_normalize"] = store.read_manifest(replay_id).get("status")
            viewer_dir.mkdir(parents=True, exist_ok=True)
            (viewer_dir / "match.json").write_text(json.dumps(
                {"schema": store.VIEWER_SCHEMA, "decode": {"status": "ready"}, "rounds": []}),
                encoding="utf-8")

        async def fake_gzip(replay_id, raw_dir, emit):
            return {}

        with patch.object(pipeline, "_decode", fake_decode), \
                patch.object(pipeline, "_normalize", fake_normalize), \
                patch.object(pipeline, "_gzip_raw", fake_gzip), \
                patch.object(pipeline, "_decode_quality", AsyncMock(return_value={"suspect": False})):
            await pipeline.reprocess_replay(None, lambda *args, **kw: None, replay_id="api5")
        # the decode stage runs under 'processing'; the normalizer must already
        # see 'ready' (same as the import path) so match.decode.status is honest
        self.assertEqual(seen["at_decode"], "processing")
        self.assertEqual(seen["at_normalize"], "ready")
        manifest = store.read_manifest("api5")
        self.assertEqual(manifest["status"], "ready")
        self.assertEqual(manifest["viewerSchema"], store.VIEWER_SCHEMA)

    async def test_reprocess_active_job_is_reused(self):
        make_store(self.sandbox, "api4", parser_fp=dict(CURRENT), with_raw=True)
        active = self.manager.new_job("reprocess", "api4", replay_id="api4")
        with patch.object(pipeline, "reprocess_replay", AsyncMock()):
            response = await replay_api.api_reprocess(self._request("api4"))
            self.assertEqual(response.status, 202)
            payload = json.loads(response.body)
            self.assertEqual(payload["jobId"], active.id)
            self.assertIsNone(payload["mode"])

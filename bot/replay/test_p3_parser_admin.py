"""P5-B parser administration, LAN guard, and density→suspect persistence."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from bot.replay import api, jobs, p3_parser_admin as admin, pipeline, store
from bot.web.app import error_middleware


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    data = tmp_path / "data" / "replays"
    data.mkdir(parents=True)
    work = tmp_path / "VALOPLANT_WORK"
    work.mkdir()
    (work / "parser-commit.txt").write_text("upstream_sha=testcommit\noverlay_patch_sha256=testoverlay\n")
    monkeypatch.setattr(store, "DATA_REPLAYS_DIR", data)
    monkeypatch.setattr(store, "ROOT", tmp_path)
    monkeypatch.setattr(store, "WORK_DIR", work)
    monkeypatch.setattr(admin, "UPDATE_STATE", work / "update-status.json")
    return data


def truncated_export(raw: Path):
    raw.mkdir(parents=True)
    (raw / "manifest.json").write_text(json.dumps({"replay_build": "++Ares-Core+release-13.06"}))
    with (raw / "events.ndjson").open("w") as stream:
        for n in (1, 2):
            stream.write(json.dumps({"type": "round_outcome", "round_number": n, "time_ms": n * 30000}) + "\n")
    with (raw / "movement.ndjson").open("w") as stream:
        for n in (1, 2):
            stream.write(json.dumps({"time_ms": n * 30000 - 5000, "x": 0}) + "\n")


async def _exercise_truncated(sandbox, tmp_path):
    source = tmp_path / "copy.vrf"
    source.write_bytes(b"fixture")
    seen = {}

    async def decode(replay_id, source_path, raw_dir, emit):
        truncated_export(raw_dir)

    async def normalize(replay_id, raw_dir, viewer_dir, emit):
        seen["before_normalize"] = store.read_manifest(replay_id)["status"]
        viewer_dir.mkdir()
        (viewer_dir / "match.json").write_text(json.dumps({"schema": store.VIEWER_SCHEMA,
            "decode": {"status": "ready"}, "rounds": [{"n": 1}], "map": {"name": "Haven"}}))

    with patch.object(pipeline, "peek_container", return_value={"branch": "++Ares-Core+release-13.06", "mapPath": "Triad", "durationMs": 60000}), \
         patch.object(pipeline, "_decode", decode), patch.object(pipeline, "_normalize", normalize), \
         patch.object(pipeline, "_gzip_raw", AsyncMock(return_value={})):
        await pipeline.import_replay(None, lambda *args: None, replay_id="truncated", source_path=source)
    manifest = store.read_manifest("truncated")
    assert seen["before_normalize"] == "suspect"
    assert manifest["status"] == "suspect"
    assert "movement density" in manifest["densityReason"]
    assert store.list_replays()[0]["status"] == "suspect"


def test_truncated_decode_is_suspect_not_ready(sandbox, tmp_path):
    asyncio.run(_exercise_truncated(sandbox, tmp_path))


async def _exercise_admin(sandbox):
    app = web.Application(middlewares=[error_middleware])
    app["jobs"] = jobs.JobManager()
    api.setup_routes(app)
    admin.mount(app)
    suspect = sandbox / "suspect1"
    suspect.mkdir()
    (suspect / "manifest.json").write_text(json.dumps({"id": "suspect1", "status": "suspect",
        "densityReason": "round 1 movement density below baseline", "source": "replays/copy.vrf"}))
    async with TestClient(TestServer(app)) as client:
        forbidden = SimpleNamespace(remote="192.168.1.55")
        with pytest.raises(web.HTTPForbidden) as exc:
            await admin.update_parser(forbidden)
        assert exc.value.status == 403
        forbidden.remote = None
        with pytest.raises(web.HTTPForbidden):
            await admin.update_parser(forbidden)
        decoder = await client.get("/api/decoder")
        info = await decoder.json()
        assert info["parserCommit"] == "testcommit"
        assert info["overlayHash"] == "testoverlay"
        assert "supported" in info and info["lastCheck"] is None
        replays = await (await client.get("/api/replays")).json()
        assert replays["replays"][0]["status"] == "suspect"
        assert "movement density" in replays["replays"][0]["suspectReason"]
        quality = await (await client.get("/api/replays/suspect1/quality")).json()
        assert quality["status"] == "suspect" and quality["suspectReason"] == replays["replays"][0]["suspectReason"]
        gate = asyncio.Event()
        started = asyncio.Event()

        async def fake_run(job, emit, **kwargs):
            started.set()
            emit("decode", 40, "DRY-RUN candidate ready")
            await gate.wait()
            emit("done", 100, "parser update check ready")

        with patch.object(admin, "_update", fake_run):
            queued = await client.post("/api/decoder/update", json={"dryRun": True})
            assert queued.status == 202
            job_id = (await queued.json())["jobId"]
            await asyncio.wait_for(started.wait(), timeout=2)
            second = await client.post("/api/decoder/update", json={"dryRun": True})
            assert second.status == 409
            assert (await second.json())["jobId"] == job_id
            event = next(e for e in app["jobs"].snapshot_events() if e["jobId"] == job_id)
            assert event["pct"] == 40 and event["message"].startswith("DRY-RUN")
            gate.set()
            for _ in range(100):
                if app["jobs"].get(job_id).status == "done":
                    break
                await asyncio.sleep(.01)
            assert app["jobs"].get(job_id).status == "done"
        await app["jobs"].shutdown()


def test_decoder_api_guard_concurrency_and_sse(sandbox):
    asyncio.run(_exercise_admin(sandbox))

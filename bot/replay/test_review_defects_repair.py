"""Focused regression coverage for the three VRF repair findings (2026-10-23).

R1 — a malformed density baseline (threshold or counters) can never authorize publication.
R2 — a new import/reprocess attempt never exposes the previous attempt's successful quality,
     including decode/normalization failures and successful replacements.
R3 — ``--no-pin`` works from the detached checkout a pinned update leaves behind: the intended
     tracking branch is selected explicitly and a dirty/diverged checkout fails closed.

Synthetic exports, temporary stores and local Git repositories only; no network or credentials.
"""
from __future__ import annotations

import asyncio
import copy
import json
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from bot.replay import pipeline, store
from bot.replay.tools import update_parser as updater
from bot.replay.test_parser_quality_provenance import make_export

BRANCH = "++Ares-Core+release-13.06"


# ---------------------------------------------------------------- helpers

def verdict_for(tmp_path, baseline: dict, *, name: str = "probe", movement: int = 1,
                events: int = 1, **export_kwargs) -> dict:
    export = make_export(tmp_path, name, movement=movement, events=events, **export_kwargs)
    return updater.assess_decode_density(export, baseline=baseline)


def write_export(raw_dir: Path, *, movement: int = 100, events: int = 20,
                 source_sha: str = "src-A", rounds: int = 2) -> Path:
    """Same synthetic export shape as ``make_export``, but tolerant of an existing dir."""
    raw_dir = Path(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / "manifest.json").write_text(json.dumps(
        {"replay_build": BRANCH, "source_sha256": source_sha, "parse_status": "completed"}),
        encoding="utf-8")
    ev: list[dict] = []
    mv: list[dict] = []
    span = 1000
    for number in range(1, rounds + 1):
        start, end = (number - 1) * span + (1 if number > 1 else 0), number * span
        ev += [{"type": "rpc_received", "time_ms": start + i + 1} for i in range(events)]
        ev.append({"type": "round_outcome", "time_ms": end, "round_number": number})
        mv += [{"type": "movement", "time_ms": start + i + 1} for i in range(movement)]
    (raw_dir / "events.ndjson").write_text("".join(json.dumps(r) + "\n" for r in ev),
                                           encoding="utf-8")
    (raw_dir / "movement.ndjson").write_text("".join(json.dumps(r) + "\n" for r in mv),
                                             encoding="utf-8")
    return raw_dir


@pytest.fixture
def good_baseline(tmp_path):
    good = make_export(tmp_path, "good", movement=100, events=20)
    return updater.create_baseline(good, tmp_path / "baseline.json")


def malformed(baseline: dict, **overrides) -> dict:
    value = copy.deepcopy(baseline)
    value.update(overrides)
    return value


def malformed_profile(baseline: dict, **overrides) -> dict:
    value = copy.deepcopy(baseline)
    value["profiles"]["release-13.xx"].update(overrides)
    return value


# ---------------------------------------------------------------- R1: threshold

INVALID_THRESHOLDS = [
    pytest.param("NaN", id="string-NaN"),
    pytest.param("Infinity", id="string-Infinity"),
    pytest.param("-Infinity", id="string--Infinity"),
    pytest.param("0.1", id="numeric-string"),
    pytest.param(True, id="bool-true"),
    pytest.param(False, id="bool-false"),
    pytest.param(0, id="zero-int"),
    pytest.param(0.0, id="zero-float"),
    pytest.param(-1, id="negative-int"),
    pytest.param(-0.5, id="negative-float"),
    pytest.param(1.0001, id="above-one"),
    pytest.param(10, id="tenx-as-ratio"),
    pytest.param(None, id="null"),
    pytest.param([], id="list"),
    pytest.param({}, id="object"),
    pytest.param(float("nan"), id="float-nan"),
    pytest.param(float("inf"), id="float-inf"),
    pytest.param(float("-inf"), id="float--inf"),
]


@pytest.mark.parametrize("threshold", INVALID_THRESHOLDS)
def test_r1_invalid_threshold_is_unvalidated_and_never_authorizes_publication(
        tmp_path, good_baseline, threshold):
    broken = malformed(good_baseline, thresholdRatio=threshold)
    verdict = verdict_for(tmp_path, broken)
    assert verdict["state"] == "unvalidated", threshold
    assert verdict["suspect"] is False
    assert "thresholdRatio" in verdict["reason"]
    assert verdict["basis"] is None
    with pytest.raises(updater.UpdateError, match="unvalidated"):
        updater._require_validated(verdict)


@pytest.mark.parametrize("threshold", [0.01, 0.05, 0.10, 0.5, 1.0])
def test_r1_valid_thresholds_keep_the_documented_behaviour(tmp_path, good_baseline, threshold):
    # Same counts as the baseline: every permitted positive fraction passes.
    verdict = verdict_for(tmp_path, malformed(good_baseline, thresholdRatio=threshold),
                          name=f"ok-{threshold}", movement=100, events=20)
    assert verdict["state"] == "validated", threshold
    assert verdict["basis"] == "exact_source_per_round"


def test_r1_missing_threshold_uses_the_documented_default(tmp_path, good_baseline):
    baseline = copy.deepcopy(good_baseline)
    del baseline["thresholdRatio"]
    verdict = verdict_for(tmp_path, baseline, movement=100, events=20)
    assert verdict["state"] == "validated"
    # ... and the classic >10x loss is still caught with the default.
    low = verdict_for(tmp_path, baseline, name="low", movement=1, events=1)
    assert low["state"] == "suspect"
    assert "movement" in low["reason"]


def test_r1_malformed_threshold_still_refuses_even_when_counts_look_fine(tmp_path, good_baseline):
    # A would-be "validated" export is not validated when the baseline itself is malformed.
    assert verdict_for(tmp_path, good_baseline, movement=100, events=20)["state"] == "validated"
    for bad in ("NaN", 0, -1):
        verdict = verdict_for(tmp_path, malformed(good_baseline, thresholdRatio=bad),
                              name=f"fine-{bad}", movement=100, events=20)
        assert verdict["state"] == "unvalidated"


def test_r1_json_baseline_file_with_malformed_threshold_is_unvalidated(tmp_path, good_baseline):
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(malformed(good_baseline, thresholdRatio="NaN")), encoding="utf-8")
    export = make_export(tmp_path, "json-in", movement=1, events=1)
    verdict = updater.assess_decode_density(export, baseline_path=path)
    assert verdict["state"] == "unvalidated"
    assert "malformed density baseline" in verdict["reason"]
    with pytest.raises(updater.UpdateError):
        updater._require_validated(verdict)


# ---------------------------------------------------------------- R1: counters / denominators

BAD_PROFILES = [
    pytest.param({"rounds": "not-a-list"}, id="rounds-not-list"),
    pytest.param({"rounds": [42, 43]}, id="round-row-not-object"),
    pytest.param({"roundCount": "2"}, id="roundCount-string"),
    pytest.param({"roundCount": True}, id="roundCount-bool"),
    pytest.param({"roundCount": -2}, id="roundCount-negative"),
    pytest.param({"roundCount": 7}, id="roundCount-mismatch"),
    pytest.param({"eventRows": "many"}, id="eventRows-string"),
    pytest.param({"movementRows": -5}, id="movementRows-negative"),
    pytest.param({"movementRows": float("inf")}, id="movementRows-infinity"),
]


@pytest.mark.parametrize("override", BAD_PROFILES)
def test_r1_malformed_profile_counters_are_unvalidated(tmp_path, good_baseline, override):
    base = malformed_profile(good_baseline, **override)
    verdict = verdict_for(tmp_path, base)
    assert verdict["state"] == "unvalidated", override
    assert "malformed density baseline" in verdict["reason"]
    with pytest.raises(updater.UpdateError):
        updater._require_validated(verdict)


def _with_round(baseline: dict, index: int, **fields) -> dict:
    value = copy.deepcopy(baseline)
    value["profiles"]["release-13.xx"]["rounds"][index].update(fields)
    return value


BAD_ROUNDS = [
    pytest.param({"movementSamples": "100"}, id="movementSamples-string"),
    pytest.param({"movementSamples": True}, id="movementSamples-bool"),
    pytest.param({"movementSamples": -1}, id="movementSamples-negative"),
    pytest.param({"movementSamples": float("nan")}, id="movementSamples-nan"),
    pytest.param({"eventCount": "NaN"}, id="eventCount-string-NaN"),
    pytest.param({"eventCount": None}, id="eventCount-null"),
    pytest.param({"movementPerSecond": "12.5"}, id="movementPerSecond-string"),
    pytest.param({"movementPerSecond": float("inf")}, id="movementPerSecond-inf"),
    pytest.param({"movementPerSecond": -3.0}, id="movementPerSecond-negative"),
    pytest.param({"n": 0}, id="round-number-zero"),
    pytest.param({"n": "1"}, id="round-number-string"),
]


@pytest.mark.parametrize("override", BAD_ROUNDS)
def test_r1_malformed_baseline_rows_are_unvalidated(tmp_path, good_baseline, override):
    base = _with_round(good_baseline, 0, **override)
    verdict = verdict_for(tmp_path, base)
    assert verdict["state"] == "unvalidated", override
    assert "malformed density baseline" in verdict["reason"]


def test_r1_malformed_rows_do_not_crash_and_never_raise(tmp_path, good_baseline):
    # Any malformed shape yields a verdict dict (fail closed), never an exception.
    shapes = [
        malformed_profile(good_baseline, rounds=[{"n": 1, "movementSamples": "NaN"}]),
        malformed_profile(good_baseline, rounds=[{"movementPerSecond": "not-a-number"}]),
        malformed_profile(good_baseline, rounds=[None]),
    ]
    for index, base in enumerate(shapes, 1):
        verdict = verdict_for(tmp_path, base, name=f"shape-{index}")
        assert verdict["state"] == "unvalidated"


def test_r1_family_floor_basis_also_rejects_malformed_counts(tmp_path, good_baseline):
    other = make_export(tmp_path, "other-src", movement=100, events=20, source_sha="src-B")
    assert updater.assess_decode_density(other, baseline=good_baseline)["state"] == "validated"
    base = _with_round(good_baseline, 0, movementPerSecond="NaN")
    verdict = updater.assess_decode_density(
        make_export(tmp_path, "other-src2", movement=100, events=20, source_sha="src-B2"),
        baseline=base)
    assert verdict["state"] == "unvalidated"


def test_r1_valid_baseline_cases_unchanged(tmp_path):
    # The existing permutations must all keep working through the new validation.
    good = make_export(tmp_path, "v-good", movement=40, events=20)
    baseline = updater.create_baseline(good, tmp_path / "b.json")
    assert verdict_for(tmp_path, baseline, name="v-same", movement=40, events=20)["state"] == "validated"
    assert verdict_for(tmp_path, baseline, name="v-half", movement=20, events=10)["state"] == "validated"
    low = verdict_for(tmp_path, baseline, name="v-low", movement=2, events=1)
    assert low["state"] == "suspect" and "round 1 movement" in low["reason"]
    no_outcome = verdict_for(tmp_path, baseline, name="v-noout", movement=40, events=20,
                             outcomes=False)
    assert no_outcome["state"] == "suspect" and "round_outcome" in no_outcome["reason"]
    uncovered = verdict_for(tmp_path, baseline, name="v-old", movement=40, events=20,
                            branch="++Ares-Core+release-11.07")
    assert uncovered["state"] == "unvalidated" and "release-11.xx" in uncovered["reason"]
    # gzip exports and a different source (family floor) still work.
    gz = verdict_for(tmp_path, baseline, name="v-gz", movement=40, events=20, gz=True)
    assert gz["state"] == "validated"
    other = verdict_for(tmp_path, baseline, name="v-other", movement=40, events=20, source_sha="src-B")
    assert other["state"] == "validated" and other["basis"] == "family_density_floor"


# ---------------------------------------------------------------- R1: publication through the pipeline


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    data = tmp_path / "data" / "replays"
    data.mkdir(parents=True)
    work = tmp_path / "VALOPLANT_WORK"
    work.mkdir()
    (work / "parser-commit.txt").write_text("upstream_sha=" + "a" * 40 +
                                            "\noverlay_patch_sha256=" + "b" * 64 + "\n")
    monkeypatch.setattr(store, "DATA_REPLAYS_DIR", data)
    monkeypatch.setattr(store, "ROOT", tmp_path)
    monkeypatch.setattr(store, "WORK_DIR", work)
    monkeypatch.setattr(store, "ORIGINALS_DIR", tmp_path / "replays")
    return {"root": tmp_path, "data": data, "work": work}


async def _import(sandbox, tmp_path, baseline_value, *, build=None, normalize=None):
    source = tmp_path / "copy.vrf"
    source.write_bytes(b"fixture")
    baseline_path = tmp_path / "baseline.json"
    if baseline_value is not None:
        baseline_path.write_text(json.dumps(baseline_value), encoding="utf-8")

    async def decode(replay_id, source_path, raw_dir, emit):
        if build is not None:
            build(raw_dir)
        else:
            write_export(raw_dir)

    async def default_normalize(replay_id, raw_dir, viewer_dir, emit):
        viewer_dir.mkdir(parents=True, exist_ok=True)
        (viewer_dir / "match.json").write_text(json.dumps({
            "schema": store.VIEWER_SCHEMA, "decode": {"status": "ready"},
            "rounds": [{"n": 1}], "map": {"name": "Haven"}}), encoding="utf-8")

    messages: list = []
    with patch.object(updater, "BASELINE_FILE", baseline_path), \
            patch.object(pipeline, "peek_container", return_value={
                "branch": BRANCH, "mapPath": "Triad", "durationMs": 60000}), \
            patch.object(pipeline, "_decode", decode), \
            patch.object(pipeline, "_normalize", normalize or default_normalize), \
            patch.object(pipeline, "_gzip_raw", AsyncMock(return_value={})):
        await pipeline.import_replay(None, lambda *a: messages.append(a),
                                     replay_id="r1", source_path=source)
    return store.read_manifest("r1"), messages


def test_r1_import_with_malformed_baseline_records_unvalidated_not_validated(sandbox, tmp_path,
                                                                            good_baseline):
    manifest, messages = asyncio.run(
        _import(sandbox, tmp_path, malformed(good_baseline, thresholdRatio="NaN")))
    assert manifest["status"] == "ready"          # usable, but explicitly not verified
    assert manifest["quality"]["state"] == "unvalidated"
    assert "malformed density baseline" in manifest["quality"]["reason"]
    assert manifest["densityReason"] is None
    assert any("unvalidated" in str(m) for m in messages)
    with pytest.raises(updater.UpdateError):
        updater._require_validated({"state": manifest["quality"]["state"],
                                    "suspect": False, "reason": manifest["quality"]["reason"]})


def test_r1_import_with_malformed_rows_records_unvalidated(sandbox, tmp_path, good_baseline):
    manifest, _ = asyncio.run(
        _import(sandbox, tmp_path, malformed_profile(good_baseline, roundCount="two")))
    assert manifest["quality"]["state"] == "unvalidated"
    assert "malformed density baseline" in manifest["quality"]["reason"]


def test_r1_import_with_valid_baseline_still_validates(sandbox, tmp_path, good_baseline):
    manifest, _ = asyncio.run(_import(sandbox, tmp_path, good_baseline))
    assert manifest["quality"]["state"] == "validated"
    assert manifest["quality"]["basis"] == "exact_source_per_round"


# ---------------------------------------------------------------- R2: quality lifecycle


def test_r2_quality_view_never_exposes_quality_for_processing_or_failed():
    block = {"state": "validated", "reason": None, "basis": "exact_source_per_round"}
    assert store.quality_view({"status": "processing", "quality": block})["state"] is None
    assert store.quality_view({"status": "failed", "quality": block})["state"] is None
    assert store.quality_view({"status": "failed", "quality": block,
                               "densityReason": "x"})["state"] is None
    # A ready/suspect manifest still reports exactly what was recorded.
    assert store.quality_view({"status": "ready", "quality": block})["state"] == "validated"
    suspect = {"state": "suspect", "reason": "low", "basis": "family_density_floor"}
    assert store.quality_view({"status": "suspect", "quality": suspect})["state"] == "suspect"
    # Lists/API entries use the same view.
    assert store.quality_view({"status": "processing"})["state"] is None


async def _reprocess(sandbox, replay_id, *, mode="normalize", decode=None, normalize=None,
                     quality=None):
    plan = {"mode": mode, "reasons": [], "fingerprintChanged": False,
            "rawAvailable": True, "sourceAvailable": True}
    messages: list = []
    with patch.object(pipeline, "reprocess_plan", lambda rid: plan), \
            patch.object(pipeline, "_decode", decode or AsyncMock()), \
            patch.object(pipeline, "_normalize", normalize or AsyncMock()), \
            patch.object(pipeline, "_gzip_raw", AsyncMock(return_value={})), \
            patch.object(pipeline, "_decode_quality",
                         AsyncMock(return_value=quality or {
                             "state": "validated", "suspect": False, "reason": None,
                             "basis": "exact_source_per_round"})):
        await pipeline.reprocess_replay(None, lambda *a: messages.append(a), replay_id=replay_id)
    return store.read_manifest(replay_id), messages


def _seed_store(sandbox, replay_id, *, status="ready", quality=None):
    rdir = sandbox["data"] / replay_id
    (rdir / "raw").mkdir(parents=True)
    (rdir / "raw" / "events.ndjson.gz").write_bytes(b"gz")
    (rdir / "viewer").mkdir()
    (rdir / "viewer" / "match.json").write_text(json.dumps(
        {"schema": store.VIEWER_SCHEMA, "decode": {"status": "ready"}, "rounds": [{"n": 1}]}),
        encoding="utf-8")
    sources = sandbox["root"] / "replays"
    sources.mkdir(exist_ok=True)
    (sources / "match.vrf").write_bytes(b"vrf")
    store.update_manifest(replay_id, schema=store.STORE_SCHEMA, id=replay_id,
                          status=status, source="replays/match.vrf",
                          quality=quality, densityReason=(quality or {}).get("reason"))
    return rdir


VALID = {"state": "validated", "reason": None, "basis": "exact_source_per_round"}


def test_r2_direct_import_clears_quality_before_decode(sandbox, tmp_path, good_baseline):
    source = tmp_path / "copy.vrf"
    source.write_bytes(b"fixture")
    _seed_store(sandbox, "r1")
    store.update_manifest("r1", status="ready", quality=VALID)
    seen = {}

    async def decode(replay_id, source_path, raw_dir, emit):
        seen["status"] = store.read_manifest(replay_id).get("status")
        seen["quality"] = store.read_manifest(replay_id).get("quality")
        raise pipeline.PipelineError("decode exploded")

    with patch.object(updater, "BASELINE_FILE", tmp_path / "absent.json"), \
            patch.object(pipeline, "peek_container", return_value={
                "branch": BRANCH, "mapPath": "Triad", "durationMs": 60000}), \
            patch.object(pipeline, "_decode", decode):
        with pytest.raises(pipeline.PipelineError):
            asyncio.run(pipeline.import_replay(None, lambda *a: None, replay_id="r1",
                                               source_path=source))
    assert seen["status"] == "processing"
    assert seen["quality"] is None
    assert store.quality_view(store.read_manifest("r1"))["state"] is None


def test_r2_import_normalize_failure_hides_the_decoded_verdict(sandbox, tmp_path, good_baseline):
    source = tmp_path / "copy.vrf"
    source.write_bytes(b"fixture")
    _seed_store(sandbox, "r1")
    store.update_manifest("r1", status="ready", quality=VALID)

    async def normalize(replay_id, raw_dir, viewer_dir, emit):
        store.update_manifest(replay_id, status="failed", notes="normalize failed")
        raise pipeline.PipelineError("normalize failed")

    with patch.object(updater, "BASELINE_FILE", tmp_path / "absent.json"), \
            patch.object(pipeline, "peek_container", return_value={
                "branch": BRANCH, "mapPath": "Triad", "durationMs": 60000}), \
            patch.object(pipeline, "_decode", AsyncMock()), \
            patch.object(pipeline, "_normalize", normalize):
        with pytest.raises(pipeline.PipelineError):
            asyncio.run(pipeline.import_replay(None, lambda *a: None, replay_id="r1",
                                               source_path=source))
    failed = store.read_manifest("r1")
    assert failed["status"] == "failed"
    assert store.quality_view(failed)["state"] is None
    assert store.quality_view({"status": "failed", "quality": VALID})["state"] is None


def test_r2_import_successful_replacement_records_new_quality(sandbox, tmp_path, good_baseline):
    _seed_store(sandbox, "r1")
    store.update_manifest("r1", status="ready",
                          quality={"state": "suspect", "reason": "old", "basis": None})
    manifest, _ = asyncio.run(_import(sandbox, tmp_path, good_baseline))
    assert manifest["status"] == "ready"
    assert manifest["quality"]["state"] == "validated"
    assert "old" not in str(manifest["quality"].get("reason"))


def test_r2_reprocess_clears_quality_before_work_in_both_modes(sandbox):
    seen = {}

    async def normalize(replay_id, raw_dir, viewer_dir, emit):
        manifest = store.read_manifest(replay_id)
        seen.setdefault("normalize", []).append((manifest.get("status"), manifest.get("quality")))

    async def decode(replay_id, source_path, raw_dir, emit):
        manifest = store.read_manifest(replay_id)
        seen.setdefault("decode", []).append((manifest.get("status"), manifest.get("quality")))

    for mode in ("normalize", "decode"):
        _seed_store(sandbox, f"rp-{mode}")
        store.update_manifest(f"rp-{mode}", status="ready", quality=VALID)
        manifest, _ = asyncio.run(_reprocess(sandbox, f"rp-{mode}", mode=mode, decode=decode,
                                             normalize=normalize))
        assert manifest["status"] == "ready" and manifest["quality"]["state"] == "validated"
    # Both modes start with a cleared, processing manifest before any work runs.
    for status, quality in seen["normalize"]:
        if status == "processing":
            assert quality is None
    assert ("processing", None) in seen["normalize"]
    # A re-decode always runs under a cleared processing manifest.
    assert seen["decode"] and all(entry == ("processing", None) for entry in seen["decode"])


def test_r2_reprocess_decode_failure_hides_quality(sandbox):
    _seed_store(sandbox, "rp-fail")
    store.update_manifest("rp-fail", status="ready", quality=VALID)

    async def decode(replay_id, source_path, raw_dir, emit):
        store.update_manifest(replay_id, status="failed", notes="decode failed")
        raise pipeline.PipelineError("decode failed")

    with pytest.raises(pipeline.PipelineError):
        asyncio.run(_reprocess(sandbox, "rp-fail", mode="decode", decode=decode))
    failed = store.read_manifest("rp-fail")
    assert failed["status"] == "failed"
    assert store.quality_view(failed)["state"] is None
    assert store.quality_view({"status": "failed", "quality": VALID})["state"] is None


def test_r2_reprocess_normalize_failure_hides_quality(sandbox):
    _seed_store(sandbox, "rp-norm")
    store.update_manifest("rp-norm", status="ready", quality=VALID)

    async def normalize(replay_id, raw_dir, viewer_dir, emit):
        store.update_manifest(replay_id, status="failed", notes="normalize failed")
        raise pipeline.PipelineError("normalize failed")

    with pytest.raises(pipeline.PipelineError):
        asyncio.run(_reprocess(sandbox, "rp-norm", mode="normalize", normalize=normalize))
    failed = store.read_manifest("rp-norm")
    assert failed["status"] == "failed"
    assert store.quality_view(failed)["state"] is None


def test_r2_reprocess_unvalidated_verdict_replaces_validated(sandbox):
    _seed_store(sandbox, "rp-unv")
    store.update_manifest("rp-unv", status="ready", quality=VALID)
    manifest, _ = asyncio.run(_reprocess(sandbox, "rp-unv", quality={
        "state": "unvalidated", "suspect": False, "reason": "no density baseline for x",
        "basis": None}))
    assert manifest["quality"]["state"] == "unvalidated"
    assert "no density baseline" in manifest["quality"]["reason"]


def test_r2_isolated_stores_do_not_leak_between_cases(sandbox):
    # Different replay ids in the same sandbox stay independent.
    _seed_store(sandbox, "iso-a")
    store.update_manifest("iso-a", status="ready", quality=VALID)
    _seed_store(sandbox, "iso-b")
    store.update_manifest("iso-b", status="failed", notes="boom", quality=None)
    assert store.quality_view(store.read_manifest("iso-a"))["state"] == "validated"
    assert store.quality_view(store.read_manifest("iso-b"))["state"] is None


# ---------------------------------------------------------------- R3: detached unpin

def _git(*args, cwd=None):
    return subprocess.check_output(["git", *map(str, args)], cwd=cwd,
                                   stderr=subprocess.STDOUT, text=True).strip()


def _commit(repo: Path, name: str, text: str) -> str:
    (repo / name).write_text(text)
    _git("add", name, cwd=repo)
    _git("-c", "user.name=Repair", "-c", "user.email=repair@localhost",
         "commit", "-m", text, cwd=repo)
    return _git("rev-parse", "HEAD", cwd=repo)


@pytest.fixture
def repos(tmp_path):
    """A local origin plus a clone (no network); only the clone is under test."""
    origin = tmp_path / "origin"
    origin.mkdir()
    _git("init", "-b", "main", cwd=origin)
    first = _commit(origin, "file.txt", "fixture")
    checkout = tmp_path / "checkout"
    _git("clone", origin, checkout)
    return {"origin": origin, "checkout": checkout, "first": first}


def _upstream(monkeypatch, checkout: Path):
    monkeypatch.setattr(updater, "UPSTREAM", checkout)


def test_r3_no_pin_follows_main_after_a_pinned_update(tmp_path, repos, monkeypatch):
    _upstream(monkeypatch, repos["checkout"])
    pin = repos["first"]
    assert updater._sync_upstream_ref(pin, pull=False) == pin
    assert updater._sync_upstream_ref(None, pull=True) == pin
    assert _git("symbolic-ref", "--short", "HEAD", cwd=repos["checkout"]) == "main"
    assert updater._current_branch() == "main"


def test_r3_no_pin_fast_forwards_to_a_new_remote_commit(repos, monkeypatch):
    _upstream(monkeypatch, repos["checkout"])
    pin = repos["first"]
    assert updater._sync_upstream_ref(pin, pull=False) == pin
    advanced = _commit(repos["origin"], "second.txt", "advance")
    assert updater._sync_upstream_ref(None, pull=True) == advanced
    assert _git("rev-parse", "HEAD", cwd=repos["checkout"]) == advanced
    assert _git("symbolic-ref", "--short", "HEAD", cwd=repos["checkout"]) == "main"
    assert (repos["checkout"] / "second.txt").is_file()


def test_r3_dirty_checkout_is_refused_and_left_alone(repos, monkeypatch):
    _upstream(monkeypatch, repos["checkout"])
    pin = repos["first"]
    assert updater._sync_upstream_ref(pin, pull=False) == pin
    _commit(repos["origin"], "second.txt", "advance")
    (repos["checkout"] / "file.txt").write_text("locally edited")
    with pytest.raises(updater.UpdateError, match="local changes"):
        updater._sync_upstream_ref(None, pull=True)
    assert _git("rev-parse", "HEAD", cwd=repos["checkout"]) == pin        # untouched
    assert (repos["checkout"] / "file.txt").read_text() == "locally edited"


def test_r3_dirty_checkout_on_a_branch_is_refused(repos, monkeypatch):
    _upstream(monkeypatch, repos["checkout"])
    _commit(repos["origin"], "second.txt", "advance")
    (repos["checkout"] / "file.txt").write_text("dirty")
    with pytest.raises(updater.UpdateError, match="local changes"):
        updater._sync_upstream_ref(None, pull=True)
    assert not (repos["checkout"] / "second.txt").exists()


def test_r3_diverged_checkout_fails_closed(repos, monkeypatch):
    _upstream(monkeypatch, repos["checkout"])
    local = _commit(repos["checkout"], "local.txt", "local work")
    _commit(repos["origin"], "remote.txt", "remote work")
    with pytest.raises(updater.UpdateError, match="fast-forward"):
        updater._sync_upstream_ref(None, pull=True)
    assert _git("rev-parse", "HEAD", cwd=repos["checkout"]) == local     # not overwritten
    assert (repos["checkout"] / "local.txt").is_file()
    assert not (repos["checkout"] / "remote.txt").exists()


def test_r3_no_pull_leaves_a_detached_checkout_detached(repos, monkeypatch):
    _upstream(monkeypatch, repos["checkout"])
    pin = repos["first"]
    assert updater._sync_upstream_ref(pin, pull=False) == pin
    assert updater._sync_upstream_ref(None, pull=False) == pin
    assert updater._current_branch() is None
    with pytest.raises(subprocess.CalledProcessError):
        _git("symbolic-ref", "--short", "HEAD", cwd=repos["checkout"])


def test_r3_existing_pin_behaviour_is_preserved(repos, monkeypatch):
    _upstream(monkeypatch, repos["checkout"])
    advanced = _commit(repos["origin"], "third.txt", "advance")
    # pull=True fetches the object and detaches at the exact pin (never chasing main).
    assert updater._sync_upstream_ref(advanced, pull=True) == advanced
    assert updater._current_branch() is None
    newer = _commit(repos["origin"], "fourth.txt", "newer main")
    assert newer != advanced
    # pull=False keeps the current checkout and never pulls newer main.
    assert updater._sync_upstream_ref(advanced, pull=False) == advanced
    assert updater._current_branch() is None
    assert not (repos["checkout"] / "fourth.txt").exists()
    assert _git("rev-parse", "HEAD", cwd=repos["checkout"]) == advanced


def test_r3_tracking_branch_falls_back_when_origin_head_is_missing(repos, monkeypatch):
    _upstream(monkeypatch, repos["checkout"])
    _git("remote", "set-head", "origin", "-d", cwd=repos["checkout"])
    assert updater._sync_upstream_ref(repos["first"], pull=False) == repos["first"]
    assert updater._tracking_branch() == "main"          # origin/HEAD absent, origin/main exists
    assert updater._sync_upstream_ref(None, pull=True) == repos["first"]
    assert _git("symbolic-ref", "--short", "HEAD", cwd=repos["checkout"]) == "main"


def test_r3_detached_checkout_without_a_tracking_branch_fails_closed(tmp_path, monkeypatch):
    origin = tmp_path / "origin-trunk"
    origin.mkdir()
    _git("init", "-b", "trunk", cwd=origin)
    first = _commit(origin, "file.txt", "fixture")
    checkout = tmp_path / "checkout-trunk"
    _git("clone", origin, checkout)
    _git("remote", "set-head", "origin", "-d", cwd=checkout)
    _upstream(monkeypatch, checkout)
    _git("checkout", "--quiet", "--detach", first, cwd=checkout)
    # With no branch to follow the resolver refuses instead of guessing.
    with pytest.raises(updater.UpdateError, match="detached"):
        updater._tracking_branch()
    assert _git("rev-parse", "HEAD", cwd=checkout) == first
    assert updater._current_branch() is None
    # A pull fetches first, which restores origin/HEAD, so the remote default (trunk) is used.
    assert updater._sync_upstream_ref(None, pull=True) == first
    assert _git("symbolic-ref", "--short", "HEAD", cwd=checkout) == "trunk"


# ---------------------------------------------------------------- R1: vacuous baselines (round 2)
#
# Round-2 verification defect A: accepting non-negative counters and skipping rounds whose
# baseline counter is <= 0 let a baseline with no positive denominator authorize publication.
# A profile with round rows but no positive movementSamples/eventCount must be ``unvalidated``
# (never validated, never suspect) and refused by ``_require_validated``; ``create_baseline``
# must refuse a zero-density export instead of writing a baseline that cannot validate.

FAMILY = "release-13.xx"


def _profile(baseline: dict) -> dict:
    return baseline["profiles"][FAMILY]


def _drop_round_fields(baseline: dict, *keys: str) -> dict:
    value = copy.deepcopy(baseline)
    for row in _profile(value)["rounds"]:
        for key in keys:
            row.pop(key, None)
    return value


def _zero_round_fields(baseline: dict, *keys: str) -> dict:
    value = copy.deepcopy(baseline)
    for row in _profile(value)["rounds"]:
        for key in keys:
            row[key] = 0
    return value


DENOMINATOR_SETS = [
    pytest.param(("movementSamples", "eventCount", "movementPerSecond"), id="all-counters"),
    pytest.param(("movementSamples",), id="movement-denominator-only"),
    pytest.param(("eventCount",), id="event-denominator-only"),
]


@pytest.mark.parametrize("keys", DENOMINATOR_SETS)
def test_r1_missing_round_denominators_are_unvalidated(tmp_path, good_baseline, keys):
    base = _drop_round_fields(good_baseline, *keys)
    for profile_key in ("eventRows", "movementRows"):
        _profile(base).pop(profile_key, None)          # reported variant 1 also stripped these
    verdict = verdict_for(tmp_path, base, name="missing-" + keys[0])
    assert verdict["state"] == "unvalidated", keys
    assert verdict["suspect"] is False
    assert verdict["basis"] is None
    assert "denominator" in verdict["reason"] or "to compare against" in verdict["reason"]
    with pytest.raises(updater.UpdateError, match="unvalidated"):
        updater._require_validated(verdict)


@pytest.mark.parametrize("keys", DENOMINATOR_SETS)
def test_r1_all_zero_round_denominators_are_unvalidated(tmp_path, good_baseline, keys):
    base = _zero_round_fields(good_baseline, *keys)
    verdict = verdict_for(tmp_path, base, name="zero-" + keys[0])
    assert verdict["state"] == "unvalidated", keys
    assert verdict["suspect"] is False
    assert verdict["basis"] is None
    with pytest.raises(updater.UpdateError, match="unvalidated"):
        updater._require_validated(verdict)


@pytest.mark.parametrize("profile_key", ["eventRows", "movementRows"])
def test_r1_zero_profile_row_counters_are_unvalidated(tmp_path, good_baseline, profile_key):
    base = malformed_profile(good_baseline, **{profile_key: 0})
    verdict = verdict_for(tmp_path, base)
    assert verdict["state"] == "unvalidated"
    assert profile_key in verdict["reason"]
    with pytest.raises(updater.UpdateError):
        updater._require_validated(verdict)


def test_r1_a_single_vacuous_round_is_enough_to_refuse(tmp_path, good_baseline):
    # Only the second round loses its denominator; skipping that round must not be "validated".
    base = _with_round(good_baseline, 1, movementSamples=0, eventCount=0)
    verdict = verdict_for(tmp_path, base)
    assert verdict["state"] == "unvalidated"
    assert "round 2" in verdict["reason"]


def test_r1_reported_reproducer_never_validates_a_one_sample_export(tmp_path):
    """The exact round-2 verification reproduction: all three variants must fail closed."""
    good = make_export(tmp_path, "good", movement=100, events=20)
    base = updater.create_baseline(good, tmp_path / "b.json")
    broken = make_export(tmp_path, "broken", movement=1, events=1)      # same source sha

    missing = _drop_round_fields(base, "movementSamples", "eventCount", "movementPerSecond")
    for key in ("eventRows", "movementRows"):
        _profile(missing).pop(key, None)
    zeroed = _zero_round_fields(base, "movementSamples", "eventCount", "movementPerSecond")

    variants = {"variant1_missing_counters": missing, "variant2_all_zero_counters": zeroed}
    for name, variant in variants.items():
        verdict = updater.assess_decode_density(broken, baseline=variant)
        assert verdict["state"] == "unvalidated", name
        assert verdict["basis"] is None, name
        with pytest.raises(updater.UpdateError, match="unvalidated"):
            updater._require_validated(verdict)


def test_r1_tool_generated_vacuous_baseline_is_impossible(tmp_path):
    """Variant 3: a zero-movement export can no longer produce a validating baseline."""
    empty = make_export(tmp_path, "empty", movement=0, events=1)
    output = tmp_path / "e.json"
    with pytest.raises(updater.UpdateError, match="zero-density"):
        updater.create_baseline(empty, output)
    assert not output.exists()


@pytest.mark.parametrize("events", [1, 5])
def test_r1_create_baseline_refuses_exports_with_no_movement(tmp_path, events):
    empty = make_export(tmp_path, f"empty-{events}", movement=0, events=events,
                        source_sha="src-zero")
    output = tmp_path / "zero.json"
    with pytest.raises(updater.UpdateError, match="zero-density"):
        updater.create_baseline(empty, output)
    assert not output.exists()


def test_r1_create_baseline_refuses_a_profile_without_events(tmp_path, monkeypatch):
    profile = {"branch": BRANCH, "patchFamily": FAMILY, "sourceSha256": "src-A",
               "parseStatus": "completed", "error": None,
               "rounds": [{"n": 1, "durationMs": 1000, "movementSamples": 10, "eventCount": 0,
                           "movementPerSecond": 10.0, "eventsPerSecond": 0.0}],
               "eventRows": 0, "movementRows": 10}
    monkeypatch.setattr(updater, "profile_export", lambda raw: profile)
    with pytest.raises(updater.UpdateError, match="events"):
        updater.create_baseline(tmp_path / "raw", tmp_path / "no-events.json")
    assert not (tmp_path / "no-events.json").exists()


def test_r1_hand_written_vacuous_baseline_from_an_old_tool_run_is_unvalidated(tmp_path):
    """A baseline already on disk from a zero-movement export must not validate anything."""
    vacuous = {"schema": "valoplant_parser_baseline/v1", "thresholdRatio": 0.10,
               "profiles": {FAMILY: {
                   "sourceSha256": "src-A", "branch": BRANCH, "roundCount": 2,
                   "eventRows": 2, "movementRows": 0,
                   "rounds": [{"n": 1, "durationMs": 1000, "movementSamples": 0, "eventCount": 1,
                               "movementPerSecond": 0.0, "eventsPerSecond": 1.0},
                              {"n": 2, "durationMs": 1000, "movementSamples": 0, "eventCount": 1,
                               "movementPerSecond": 0.0, "eventsPerSecond": 1.0}]}}}
    verdict = verdict_for(tmp_path, vacuous)
    assert verdict["state"] == "unvalidated"
    assert verdict["basis"] is None
    with pytest.raises(updater.UpdateError, match="unvalidated"):
        updater._require_validated(verdict)


def test_r1_create_baseline_still_writes_a_positive_baseline(tmp_path):
    good = make_export(tmp_path, "still-good", movement=40, events=10)
    base = updater.create_baseline(good, tmp_path / "ok.json")
    rows = _profile(base)["rounds"]
    assert rows
    assert all(row["movementSamples"] > 0 and row["eventCount"] > 0 for row in rows)
    assert _profile(base)["eventRows"] > 0 and _profile(base)["movementRows"] > 0
    verdict = verdict_for(tmp_path, base, name="still-good-check", movement=40, events=10)
    assert verdict["state"] == "validated"
    assert verdict["basis"] == "exact_source_per_round"


def test_r1_import_with_a_vacuous_baseline_records_unvalidated(sandbox, tmp_path, good_baseline):
    vacuous = _zero_round_fields(good_baseline, "movementSamples", "eventCount", "movementPerSecond")
    manifest, messages = asyncio.run(_import(sandbox, tmp_path, vacuous))
    assert manifest["status"] == "ready"          # usable, but explicitly not verified
    assert manifest["quality"]["state"] == "unvalidated"
    assert "malformed density baseline" in manifest["quality"]["reason"]
    assert any("unvalidated" in str(m) for m in messages)


# ---------------------------------------------------------------- R3: untracked checkout (round 2)
#
# Round-2 verification defect B: plain ``git status --porcelain`` counted untracked files, so the
# real upstream checkout (permanent, non-gitignored ``VBCSCompiler/`` Roslyn artifacts) refused the
# ``--no-pin`` branch switch. Untracked files must not block the switch; tracked modifications and
# deletions still must, and a switch that would overwrite an untracked file must fail closed.

ARTIFACT = Path("VBCSCompiler") / "AnalyzerAssemblyLoader" / "Roslyn.dll"


@pytest.fixture
def untracked_artifacts(repos):
    artifact = repos["checkout"] / ARTIFACT
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("roslyn", encoding="utf-8")
    return artifact


def test_r3_clean_gate_ignores_untracked_files(repos, monkeypatch, untracked_artifacts):
    _upstream(monkeypatch, repos["checkout"])
    updater._require_clean_checkout()                       # untracked only: passes
    (repos["checkout"] / "file.txt").write_text("edited")
    with pytest.raises(updater.UpdateError, match="local changes"):
        updater._require_clean_checkout()


def test_r3_no_pin_switches_branch_with_untracked_artifacts_present(
        repos, monkeypatch, untracked_artifacts):
    _upstream(monkeypatch, repos["checkout"])
    pin = repos["first"]
    assert updater._sync_upstream_ref(pin, pull=False) == pin
    assert updater._current_branch() is None
    assert updater._sync_upstream_ref(None, pull=True) == pin      # was: UpdateError (defect B)
    assert updater._current_branch() == "main"
    assert untracked_artifacts.read_text() == "roslyn"            # left exactly as it was


def test_r3_tracked_modification_is_refused_even_with_untracked_files(
        repos, monkeypatch, untracked_artifacts):
    _upstream(monkeypatch, repos["checkout"])
    pin = repos["first"]
    assert updater._sync_upstream_ref(pin, pull=False) == pin
    advanced = _commit(repos["origin"], "second.txt", "advance")
    assert advanced != pin
    (repos["checkout"] / "file.txt").write_text("locally edited")
    with pytest.raises(updater.UpdateError, match="local changes"):
        updater._sync_upstream_ref(None, pull=True)
    assert _git("rev-parse", "HEAD", cwd=repos["checkout"]) == pin        # untouched
    assert (repos["checkout"] / "file.txt").read_text() == "locally edited"
    assert not (repos["checkout"] / "second.txt").exists()
    assert untracked_artifacts.read_text() == "roslyn"


def test_r3_tracked_deletion_is_refused(repos, monkeypatch, untracked_artifacts):
    _upstream(monkeypatch, repos["checkout"])
    pin = repos["first"]
    assert updater._sync_upstream_ref(pin, pull=False) == pin
    _commit(repos["origin"], "second.txt", "advance")
    (repos["checkout"] / "file.txt").unlink()
    with pytest.raises(updater.UpdateError, match="local changes"):
        updater._sync_upstream_ref(None, pull=True)
    assert _git("rev-parse", "HEAD", cwd=repos["checkout"]) == pin
    assert not (repos["checkout"] / "file.txt").exists()
    assert not (repos["checkout"] / "second.txt").exists()


def test_r3_untracked_collision_fails_closed_without_touching_it(repos, monkeypatch):
    """A fast-forward that would overwrite an untracked file must refuse, not clobber it."""
    _upstream(monkeypatch, repos["checkout"])
    pin = repos["first"]
    assert updater._sync_upstream_ref(pin, pull=False) == pin
    collide = repos["checkout"] / "collide.txt"
    collide.write_text("local untracked work", encoding="utf-8")
    advanced = _commit(repos["origin"], "collide.txt", "remote file at the same path")
    assert advanced != pin
    with pytest.raises(updater.UpdateError, match="untouched|overwrit"):
        updater._sync_upstream_ref(None, pull=True)
    assert collide.read_text() == "local untracked work"                 # not overwritten
    assert _git("rev-parse", "HEAD", cwd=repos["checkout"]) != advanced  # not fast-forwarded


def test_r3_untracked_collision_while_selecting_a_new_branch_fails_closed(repos, monkeypatch):
    """The same collision during `checkout --track -b` must refuse and leave no stray branch."""
    _upstream(monkeypatch, repos["checkout"])
    pin = repos["first"]
    assert updater._sync_upstream_ref(pin, pull=False) == pin
    _git("branch", "-D", "main", cwd=repos["checkout"])        # force the --track -b path
    collide = repos["checkout"] / "collide.txt"
    collide.write_text("local untracked work", encoding="utf-8")
    _commit(repos["origin"], "collide.txt", "remote file at the same path")
    with pytest.raises(updater.UpdateError, match="untouched|overwrit"):
        updater._sync_upstream_ref(None, pull=True)
    assert collide.read_text() == "local untracked work"
    assert _git("rev-parse", "HEAD", cwd=repos["checkout"]) == pin
    assert _git("branch", "--list", "main", cwd=repos["checkout"]) == ""   # no stray branch


# ---------------------------------------------------------------- R1: unbounded JSON integers (round 3)

# ``float()`` cannot represent any of these; JSON itself parses them happily.
OVERSIZED_INTEGERS = [
    pytest.param(10 ** 1000, id="10e1000"),
    pytest.param(10 ** 400, id="10e400"),
    pytest.param(2 ** 1024, id="2e1024"),
]


def _assess(tmp_path, baseline_value: dict, *, name: str, as_json: bool,
            movement: int = 100, events: int = 20) -> dict:
    """Assess an export against a baseline passed either in memory or through a JSON file."""
    export = make_export(tmp_path, name, movement=movement, events=events)
    if not as_json:
        return updater.assess_decode_density(export, baseline=baseline_value)
    path = tmp_path / f"{name}-baseline.json"
    path.write_text(json.dumps(baseline_value), encoding="utf-8")
    return updater.assess_decode_density(export, baseline_path=path)


@pytest.mark.parametrize("as_json", [False, True], ids=["in-memory", "json-file"])
@pytest.mark.parametrize("huge", OVERSIZED_INTEGERS)
def test_r3_oversized_threshold_ratio_is_fail_closed(tmp_path, good_baseline, huge, as_json):
    """``thresholdRatio = 10**1000`` parses, then must be malformed — never ``OverflowError``."""
    verdict = _assess(tmp_path, malformed(good_baseline, thresholdRatio=huge),
                      name=f"ratio-{huge.bit_length()}-{as_json}", as_json=as_json)
    assert verdict["state"] == "unvalidated"
    assert verdict["suspect"] is False
    assert verdict["basis"] is None
    assert "malformed density baseline" in verdict["reason"]
    assert "thresholdRatio" in verdict["reason"]
    # An unbounded integer must not be echoed verbatim into a persisted manifest.
    assert len(verdict["reason"]) < 300
    with pytest.raises(updater.UpdateError, match="unvalidated"):
        updater._require_validated(verdict)


@pytest.mark.parametrize("as_json", [False, True], ids=["in-memory", "json-file"])
@pytest.mark.parametrize("huge", OVERSIZED_INTEGERS)
@pytest.mark.parametrize("field", ["movementPerSecond", "movementSamples", "eventCount"])
def test_r3_oversized_round_measurement_is_fail_closed(tmp_path, good_baseline, huge, field,
                                                       as_json):
    """A per-round ``movementPerSecond = 10**1000`` (or counter) is malformed, not a crash."""
    baseline = _with_round(good_baseline, 0, **{field: huge})
    verdict = _assess(tmp_path, baseline, name=f"{field}-{huge.bit_length()}-{as_json}",
                      as_json=as_json)
    assert verdict["state"] == "unvalidated", field
    assert verdict["suspect"] is False
    assert verdict["basis"] is None
    assert "malformed density baseline" in verdict["reason"]
    assert field in verdict["reason"]
    with pytest.raises(updater.UpdateError, match="unvalidated"):
        updater._require_validated(verdict)


@pytest.mark.parametrize("huge", OVERSIZED_INTEGERS)
@pytest.mark.parametrize("field", ["eventRows", "movementRows", "roundCount", "n"])
def test_r3_oversized_profile_counters_are_fail_closed(tmp_path, good_baseline, huge, field):
    if field == "n":
        baseline = _with_round(good_baseline, 0, n=huge)
    else:
        baseline = malformed_profile(good_baseline, **{field: huge})
    verdict = _assess(tmp_path, baseline, name=f"profile-{field}-{huge.bit_length()}", as_json=True)
    assert verdict["state"] == "unvalidated", field
    assert verdict["basis"] is None
    with pytest.raises(updater.UpdateError):
        updater._require_validated(verdict)


def test_r3_oversized_baseline_through_the_import_pipeline_is_never_validated(sandbox, tmp_path,
                                                                             good_baseline):
    manifest, messages = asyncio.run(
        _import(sandbox, tmp_path, malformed(good_baseline, thresholdRatio=10 ** 1000)))
    assert manifest["status"] == "ready"          # usable, but explicitly not verified
    assert manifest["quality"]["state"] == "unvalidated"
    assert "malformed density baseline" in manifest["quality"]["reason"]
    assert manifest["densityReason"] is None
    assert any("unvalidated" in str(m) for m in messages)


def test_r3_oversized_round_measurement_through_the_import_pipeline_is_never_validated(
        sandbox, tmp_path, good_baseline):
    manifest, _ = asyncio.run(
        _import(sandbox, tmp_path, _with_round(good_baseline, 1, movementPerSecond=10 ** 1000)))
    assert manifest["quality"]["state"] == "unvalidated"
    assert "malformed density baseline" in manifest["quality"]["reason"]


def test_r3_large_but_representable_counters_still_compare(tmp_path):
    """The guard is about float representability, not size: ``10**300`` still yields a verdict."""
    representable = {"schema": "valoplant_parser_baseline/v1", "thresholdRatio": 0.10,
                     "profiles": {FAMILY: {
                         "sourceSha256": "src-A", "branch": BRANCH, "roundCount": 1,
                         "eventRows": 10 ** 300, "movementRows": 10 ** 300,
                         "rounds": [{"n": 1, "durationMs": 1000,
                                     "movementSamples": 10 ** 300, "eventCount": 10 ** 300,
                                     "movementPerSecond": 10 ** 300, "eventsPerSecond": 1.0}]}}}
    verdict = _assess(tmp_path, representable, name="representable", as_json=True,
                      movement=100, events=20)
    assert verdict["state"] == "suspect"          # genuinely compared, catastrophic loss
    assert verdict["basis"] == "exact_source_per_round"
    assert "movement" in verdict["reason"]


def test_r3_valid_baseline_json_still_validates(tmp_path, good_baseline):
    """Round-2 semantics and permitted thresholds are unchanged by the overflow guard."""
    same = _assess(tmp_path, good_baseline, name="unchanged", as_json=True)
    assert same["state"] == "validated"
    assert same["basis"] == "exact_source_per_round"
    updater._require_validated(same)              # authorizes publication, as before


# ---------------------------------------------------------------- R1: values too wide to format (round 4)

# Wider than ``sys.get_int_max_str_digits()`` (4300): ``repr()``/``str()`` itself raises for these,
# so they can only reach the check through the in-memory ``baseline=`` API — ``json.dumps`` refuses
# to serialise them, and ``_read_json`` rejects such a file. The verdict must still fail closed.
UNFORMATTABLE_INTEGERS = [
    pytest.param(10 ** 5000, id="10e5000"),
    pytest.param(-(10 ** 5000), id="-10e5000"),
    pytest.param(10 ** 4301, id="10e4301"),
    pytest.param(2 ** 20000, id="2e20000"),
]


@pytest.mark.parametrize("huge", UNFORMATTABLE_INTEGERS)
def test_r4_unformattable_in_memory_threshold_is_unvalidated_not_a_crash(tmp_path, good_baseline,
                                                                        huge):
    """Defect: ``_short`` called ``repr()`` before truncating, so ``10**5000`` raised ``ValueError``.

    A malformed baseline passed in memory must produce the same bounded ``unvalidated`` verdict as
    one read from a file, never an exception out of ``assess_decode_density``.
    """
    verdict = verdict_for(tmp_path, malformed(good_baseline, thresholdRatio=huge),
                          name=f"wide-{huge.bit_length()}", movement=100, events=20)
    assert verdict["state"] == "unvalidated"
    assert verdict["suspect"] is False
    assert verdict["basis"] is None
    assert "malformed density baseline" in verdict["reason"]
    assert "thresholdRatio" in verdict["reason"]
    # Bounded and diagnostic: no digit dump, and the verdict stays persistable as a manifest.
    assert len(verdict["reason"]) < 300
    assert "0" * 100 not in verdict["reason"]
    assert json.loads(json.dumps(verdict))["state"] == "unvalidated"
    with pytest.raises(updater.UpdateError, match="unvalidated"):
        updater._require_validated(verdict)


def test_r4_short_describes_an_unformattable_integer_by_bit_length():
    """``_short`` is the only reason formatter: it must never raise on a wide integer."""
    huge = 10 ** 5000
    assert updater._short(huge) == f"<int of {huge.bit_length()} bits>"
    assert updater._short(-huge) == f"<int of {huge.bit_length()} bits>"
    assert len(updater._short(huge)) < 48
    # Ordinary formatting is untouched: plain values and truncated wide-but-formattable integers.
    assert updater._short(0.5) == "0.5"
    assert updater._short(10 ** 1000) == f"{'1' + '0' * 1000}"[:48] + "... (1001 chars)"
    assert updater._short(10) == "10"


@pytest.mark.parametrize("huge", [10 ** 1000, 10 ** 5000, -(10 ** 5000)],
                         ids=["10e1000", "10e5000", "-10e5000"])
@pytest.mark.parametrize("slot", ["roundCount", "eventRows", "movementRows"])
def test_r4_oversized_profile_values_never_crash_the_assessment(tmp_path, good_baseline, slot,
                                                               huge):
    verdict = verdict_for(tmp_path, malformed_profile(good_baseline, **{slot: huge}),
                          name=f"profile-{slot}-{huge.bit_length()}", movement=100, events=20)
    assert verdict["state"] == "unvalidated"
    assert verdict["suspect"] is False
    assert verdict["basis"] is None
    assert slot in verdict["reason"]
    assert len(verdict["reason"]) < 300
    with pytest.raises(updater.UpdateError):
        updater._require_validated(verdict)


@pytest.mark.parametrize("huge", [10 ** 1000, 10 ** 5000, -(10 ** 5000)],
                         ids=["10e1000", "10e5000", "-10e5000"])
@pytest.mark.parametrize("field", ["n", "movementSamples", "eventCount", "movementPerSecond"])
def test_r4_oversized_round_values_never_crash_the_assessment(tmp_path, good_baseline, field,
                                                             huge):
    for index in (0, 1):
        verdict = verdict_for(tmp_path, _with_round(good_baseline, index, **{field: huge}),
                              name=f"round-{index}-{field}-{huge.bit_length()}",
                              movement=100, events=20)
        assert verdict["state"] == "unvalidated", (index, field)
        assert verdict["suspect"] is False
        assert verdict["basis"] is None
        assert field in verdict["reason"]
        assert len(verdict["reason"]) < 300
        assert json.loads(json.dumps(verdict))["state"] == "unvalidated"
        with pytest.raises(updater.UpdateError):
            updater._require_validated(verdict)


def test_r4_no_baseline_numeric_slot_can_crash_the_assessment(tmp_path, good_baseline):
    """Exhaustive sweep: every baseline number the check reads fails closed instead of raising."""
    huge = 10 ** 5000
    mutations = [
        ("thresholdRatio", lambda b: malformed(b, thresholdRatio=huge)),
        ("profile roundCount", lambda b: malformed_profile(b, roundCount=huge)),
        ("profile eventRows", lambda b: malformed_profile(b, eventRows=huge)),
        ("profile movementRows", lambda b: malformed_profile(b, movementRows=huge)),
        ("round n", lambda b: _with_round(b, 0, n=huge)),
        ("round movementSamples", lambda b: _with_round(b, 0, movementSamples=huge)),
        ("round eventCount", lambda b: _with_round(b, 0, eventCount=huge)),
        ("round movementPerSecond", lambda b: _with_round(b, 0, movementPerSecond=huge)),
    ]
    for label, build in mutations:
        verdict = verdict_for(tmp_path, build(good_baseline),
                              name=f"sweep-{label.replace(' ', '-')}", movement=100, events=20)
        assert isinstance(verdict, dict), label
        assert verdict["state"] == "unvalidated", label
        assert verdict["basis"] is None, label
        assert len(verdict.get("reason") or "") < 300, label
        with pytest.raises(updater.UpdateError):
            updater._require_validated(verdict)

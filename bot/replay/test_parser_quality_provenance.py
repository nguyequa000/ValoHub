"""Decode-quality states and parser build provenance (VRF update 2026-10-23).

Covers: validated / suspect / unvalidated distinction, propagation of the reason through the
pipeline manifest and the quality API, assembly-level build identity (modified DLL must not look
identical to the recorded build), cheap repeated polling, updater no-op semantics, the upstream
pin, and the tested-input matrix kept separate from registered transform support.
"""
from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from bot.replay import api, jobs, p3_parser_admin as admin, pipeline, store
from bot.replay.tools import update_parser
from bot.web.app import error_middleware

UPSTREAM = "a" * 40
OVERLAY = "b" * 64


# ---------------------------------------------------------------- fixtures

def make_export(root: Path, name: str, *, movement: int, events: int, source_sha: str = "src-A",
                branch: str = "++Ares-Core+release-13.06", outcomes: bool = True, rounds: int = 2,
                gz: bool = False) -> Path:
    raw = root / name
    raw.mkdir(parents=True)
    (raw / "manifest.json").write_text(json.dumps(
        {"replay_build": branch, "source_sha256": source_sha, "parse_status": "completed"}))
    ev, mv = [], []
    span = 1000
    for number in range(1, rounds + 1):
        start, end = (number - 1) * span + (1 if number > 1 else 0), number * span
        ev += [{"type": "rpc_received", "time_ms": start + i + 1} for i in range(events)]
        if outcomes:
            ev.append({"type": "round_outcome", "time_ms": end, "round_number": number})
        mv += [{"type": "movement", "time_ms": start + i + 1} for i in range(movement)]
    opener = (lambda p: gzip.open(p, "wt", encoding="utf-8")) if gz else (lambda p: open(p, "w", encoding="utf-8"))
    suffix = ".ndjson.gz" if gz else ".ndjson"
    with opener(raw / f"events{suffix}") as f:
        f.write("".join(json.dumps(r) + "\n" for r in ev))
    with opener(raw / f"movement{suffix}") as f:
        f.write("".join(json.dumps(r) + "\n" for r in mv))
    return raw


@pytest.fixture
def baseline(tmp_path):
    good = make_export(tmp_path, "good", movement=40, events=20)
    return update_parser.create_baseline(good, tmp_path / "baseline.json")


# ---------------------------------------------------------------- quality states

def test_known_good_is_validated_and_names_its_basis(tmp_path, baseline):
    same = make_export(tmp_path, "same", movement=40, events=20)
    verdict = update_parser.assess_decode_density(same, baseline=baseline)
    assert verdict["state"] == "validated"
    assert verdict["suspect"] is False
    assert verdict["basis"] == "exact_source_per_round"
    # The check is a catastrophic-loss heuristic, never presented as semantic equivalence.
    assert verdict["semanticEquivalence"] is False


def test_low_density_is_suspect(tmp_path, baseline):
    broken = make_export(tmp_path, "broken", movement=2, events=1)
    verdict = update_parser.assess_decode_density(broken, baseline=baseline)
    assert verdict["state"] == "suspect"
    assert verdict["suspect"] is True
    assert "round 1 movement" in verdict["reason"]


def test_missing_baseline_is_unvalidated_not_validated(tmp_path):
    export = make_export(tmp_path, "x", movement=40, events=20)
    verdict = update_parser.assess_decode_density(export, baseline_path=tmp_path / "absent.json")
    assert verdict["state"] == "unvalidated"
    assert verdict["suspect"] is False
    assert "baseline unavailable" in verdict["reason"]
    assert verdict["basis"] is None


@pytest.mark.parametrize("content", ["{not json", "[]", "\"text\"", "{}", '{"profiles": []}'])
def test_corrupt_or_empty_baseline_is_unvalidated(tmp_path, content):
    export = make_export(tmp_path, "x", movement=40, events=20)
    bad = tmp_path / "bad.json"
    bad.write_text(content)
    verdict = update_parser.assess_decode_density(export, baseline_path=bad)
    assert verdict["state"] == "unvalidated"
    assert verdict["suspect"] is False
    assert verdict["reason"]


def test_uncovered_family_is_unvalidated_with_family_in_reason(tmp_path, baseline):
    export = make_export(tmp_path, "old", movement=40, events=20, branch="++Ares-Core+release-11.07")
    verdict = update_parser.assess_decode_density(
        export, branch="++Ares-Core+release-11.07", baseline=baseline)
    assert verdict["state"] == "unvalidated"
    assert verdict["suspect"] is False
    assert "release-11.xx" in verdict["reason"]


def test_baseline_family_without_rounds_is_unvalidated(tmp_path, baseline):
    baseline["profiles"]["release-13.xx"]["rounds"] = []
    export = make_export(tmp_path, "same", movement=40, events=20)
    verdict = update_parser.assess_decode_density(export, baseline=baseline)
    assert verdict["state"] == "unvalidated"
    assert "no rounds" in verdict["reason"]


def test_different_source_same_family_uses_family_floor_basis(tmp_path, baseline):
    other = make_export(tmp_path, "other", movement=40, events=20, source_sha="src-B")
    verdict = update_parser.assess_decode_density(other, baseline=baseline)
    assert verdict["state"] == "validated"
    assert verdict["basis"] == "family_density_floor"


def test_threshold_is_not_tightened_half_density_remains_heuristic_pass(tmp_path, baseline):
    # 10% catastrophic-loss floor is deliberately unchanged: a cross-match rate cannot prove
    # equivalence, and no arbitrary tighter global threshold was introduced.
    half = make_export(tmp_path, "half", movement=20, events=10)
    verdict = update_parser.assess_decode_density(half, baseline=baseline)
    assert verdict["state"] == "validated"
    assert verdict["semanticEquivalence"] is False
    assert baseline["thresholdRatio"] == 0.10


def test_export_with_missing_round_outcomes_is_suspect(tmp_path, baseline):
    broken = make_export(tmp_path, "noout", movement=40, events=20, outcomes=False)
    verdict = update_parser.assess_decode_density(broken, baseline=baseline)
    assert verdict["state"] == "suspect"
    assert "round_outcome" in verdict["reason"]


def test_default_baseline_path_is_resolved_at_call_time(tmp_path, monkeypatch, baseline):
    path = tmp_path / "late.json"
    path.write_text(json.dumps(baseline))
    monkeypatch.setattr(update_parser, "BASELINE_FILE", path)
    export = make_export(tmp_path, "same", movement=40, events=20)
    assert update_parser.assess_decode_density(export)["state"] == "validated"


# ---------------------------------------------------------------- pipeline / manifest / API

def test_quality_manifest_fields_distinguish_the_three_states():
    ok = pipeline.quality_manifest_fields({"state": "validated", "suspect": False, "reason": None,
                                           "basis": "exact_source_per_round"})
    un = pipeline.quality_manifest_fields({"state": "unvalidated", "suspect": False,
                                           "reason": "no density baseline for release-11.xx", "basis": None})
    bad = pipeline.quality_manifest_fields({"state": "suspect", "suspect": True,
                                            "reason": "round 1 movement 2 < 10% of baseline 40"})
    assert ok["status"] == "ready" and ok["quality"]["state"] == "validated"
    assert ok["densityReason"] is None and ok["notes"] == ""
    # Unvalidated stays usable (status ready) but is never recorded as validated and keeps its reason.
    assert un["status"] == "ready" and un["quality"]["state"] == "unvalidated"
    assert "release-11.xx" in un["quality"]["reason"]
    assert un["densityReason"] is None
    assert bad["status"] == "suspect" and bad["densityReason"] == bad["quality"]["reason"]


def test_quality_manifest_fields_tolerate_legacy_verdict_without_state():
    legacy = pipeline.quality_manifest_fields({"suspect": False, "reason": "no density baseline for x"})
    assert legacy["quality"]["state"] == "unvalidated"
    assert pipeline.quality_manifest_fields({"suspect": True, "reason": "r"})["quality"]["state"] == "suspect"


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    data = tmp_path / "data" / "replays"
    data.mkdir(parents=True)
    work = tmp_path / "VALOPLANT_WORK"
    work.mkdir()
    (work / "parser-commit.txt").write_text(f"upstream_sha={UPSTREAM}\noverlay_patch_sha256={OVERLAY}\n")
    monkeypatch.setattr(store, "DATA_REPLAYS_DIR", data)
    monkeypatch.setattr(store, "ROOT", tmp_path)
    monkeypatch.setattr(store, "WORK_DIR", work)
    monkeypatch.setattr(admin, "UPDATE_STATE", work / "update-status.json")
    return data


async def _import_with(sandbox, tmp_path, export_builder, baseline_value):
    source = tmp_path / "copy.vrf"
    source.write_bytes(b"fixture")

    async def decode(replay_id, source_path, raw_dir, emit):
        export_builder(raw_dir)

    async def normalize(replay_id, raw_dir, viewer_dir, emit):
        viewer_dir.mkdir()
        (viewer_dir / "match.json").write_text(json.dumps({"schema": store.VIEWER_SCHEMA,
            "decode": {"status": "ready"}, "rounds": [{"n": 1}], "map": {"name": "Haven"}}))

    messages = []
    base = tmp_path / "baseline.json"
    if baseline_value is not None:
        base.write_text(json.dumps(baseline_value))
    with patch.object(update_parser, "BASELINE_FILE", base), \
         patch.object(pipeline, "peek_container", return_value={"branch": "++Ares-Core+release-13.06",
                                                               "mapPath": "Triad", "durationMs": 60000}), \
         patch.object(pipeline, "_decode", decode), patch.object(pipeline, "_normalize", normalize), \
         patch.object(pipeline, "_gzip_raw", AsyncMock(return_value={})):
        await pipeline.import_replay(None, lambda *a: messages.append(a), replay_id="r1", source_path=source)
    return store.read_manifest("r1"), messages


def test_import_without_baseline_records_unvalidated_with_reason(sandbox, tmp_path):
    def build(raw):
        raw.parent.mkdir(parents=True, exist_ok=True)
        make_export(raw.parent, raw.name, movement=40, events=20)

    manifest, messages = asyncio.run(_import_with(sandbox, tmp_path, build, None))
    assert manifest["status"] == "ready"
    assert manifest["quality"]["state"] == "unvalidated"
    assert "baseline unavailable" in manifest["quality"]["reason"]
    assert manifest["densityReason"] is None
    assert any("unvalidated" in str(m) for m in messages)


def test_import_known_good_records_validated(sandbox, tmp_path, baseline):
    def build(raw):
        make_export(raw.parent, raw.name, movement=40, events=20)

    manifest, _ = asyncio.run(_import_with(sandbox, tmp_path, build, baseline))
    assert manifest["status"] == "ready"
    assert manifest["quality"]["state"] == "validated"
    assert manifest["quality"]["basis"] == "exact_source_per_round"


async def _quality_api(sandbox, manifests):
    app = web.Application(middlewares=[error_middleware])
    app["jobs"] = jobs.JobManager()
    api.setup_routes(app)
    admin.mount(app)
    for rid, value in manifests.items():
        d = sandbox / rid
        d.mkdir()
        (d / "manifest.json").write_text(json.dumps({"id": rid, **value}))
    out = {}
    async with TestClient(TestServer(app)) as client:
        for rid in manifests:
            out[rid] = await (await client.get(f"/api/replays/{rid}/quality")).json()
        out["_list"] = await (await client.get("/api/replays")).json()
    return out


def test_quality_endpoint_reports_state_and_reason_without_breaking_legacy_fields(sandbox):
    result = asyncio.run(_quality_api(sandbox, {
        "val": {"status": "ready", "quality": {"state": "validated", "reason": None}},
        "unv": {"status": "ready", "quality": {"state": "unvalidated", "reason": "no density baseline for release-11.xx"}},
        "sus": {"status": "suspect", "densityReason": "round 1 movement density low",
                "quality": {"state": "suspect", "reason": "round 1 movement density low"}},
        "old": {"status": "ready"},
        "bad": {"status": "failed", "notes": "decode failed"},
    }))
    assert result["val"]["quality"]["state"] == "validated"
    assert result["unv"]["quality"]["state"] == "unvalidated"
    assert "release-11.xx" in result["unv"]["quality"]["reason"]
    assert result["unv"]["status"] == "ready" and result["unv"]["suspectReason"] is None
    assert result["sus"]["suspectReason"] == "round 1 movement density low"   # legacy field intact
    assert result["sus"]["quality"]["state"] == "suspect"
    # Pre-existing manifests never recorded a check: reported unvalidated, not silently verified.
    assert result["old"]["quality"]["state"] == "unvalidated"
    assert "no recorded" in result["old"]["quality"]["reason"]
    assert result["bad"]["quality"]["state"] is None
    listed = {r["id"]: r for r in result["_list"]["replays"]}
    assert listed["unv"]["qualityState"] == "unvalidated"
    assert listed["val"]["qualityState"] == "validated"
    assert listed["sus"]["suspectReason"] == "round 1 movement density low"


# ---------------------------------------------------------------- build provenance

DLLS = {"CliReader.dll": b"cli-v1", "Replay.Unreal.dll": b"unreal-v1", "Replay.Encoding.dll": b"enc-v1",
        "Replay.Models.dll": b"models-v1", "Replay.Valorant.dll": b"val-v1",
        "Replay.Valorant.Events.dll": b"events-v1"}


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


@pytest.fixture
def build(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "ROOT", tmp_path)
    work = tmp_path / "VALOPLANT_WORK"
    work.mkdir()
    monkeypatch.setattr(store, "WORK_DIR", work)
    bin_dir = tmp_path / "vendorValorantReplayParser" / "src" / "CliReader" / "bin" / "Release" / "net10.0"
    bin_dir.mkdir(parents=True)
    for name, data in DLLS.items():
        (bin_dir / name).write_bytes(data)
    store._ASSEMBLY_CACHE.clear()
    return {"root": tmp_path, "work": work, "bin": bin_dir}


def write_record(build, *, assemblies=True, tested=()):
    hashes = {n: sha(d) for n, d in DLLS.items()}
    lines = [f"upstream_sha={UPSTREAM}", f"overlay_patch_sha256={OVERLAY}", "overlay_files:", "- a/b.cs"]
    if assemblies:
        lines.append("assembly_set_sha256=" + store.assembly_set_sha256(hashes))
        lines += [f"assembly.{n}={h}" for n, h in sorted(hashes.items())]
    lines += [f"tested.{i}={t}" for i, t in enumerate(tested, 1)]
    (build["work"] / "parser-commit.txt").write_text("\n".join(lines) + "\n")
    return hashes


def test_legacy_record_without_assemblies_keeps_two_key_fingerprint(build):
    write_record(build, assemblies=False)
    assert store.parser_fingerprint() == {"commit": UPSTREAM, "overlay": OVERLAY}
    assert store.build_state()["state"] == "unrecorded"


def test_recorded_and_unchanged_build_is_verified(build):
    hashes = write_record(build)
    fp = store.parser_fingerprint()
    assert fp["assembly"] == store.assembly_set_sha256(hashes)
    assert store.build_state()["state"] == "verified"


def test_locally_modified_dll_no_longer_looks_identical(build):
    write_record(build)
    before = store.parser_fingerprint()
    (build["bin"] / "Replay.Unreal.dll").write_bytes(b"unreal-LOCALLY-PATCHED")
    state = store.build_state()
    after = store.parser_fingerprint()
    assert state["state"] == "modified"
    assert state["changed"] == ["Replay.Unreal.dll"]
    assert after["commit"] == before["commit"] and after["overlay"] == before["overlay"]
    assert after["assembly"] != before["assembly"]
    assert store.fingerprints_differ(before, after)


def test_missing_assembly_is_reported(build):
    write_record(build)
    (build["bin"] / "Replay.Encoding.dll").unlink()
    state = store.build_state()
    assert state["state"] == "modified" and "Replay.Encoding.dll" in state["changed"]


def test_repeated_polls_do_not_rehash_unchanged_dlls(build):
    write_record(build)
    store._ASSEMBLY_CACHE.clear()
    real = store._hash_file
    with patch.object(store, "_hash_file", side_effect=real) as spy:
        for _ in range(25):
            store.parser_fingerprint()
            store.build_state()
        first_round = spy.call_count
        assert first_round == len(DLLS)
        (build["bin"] / "CliReader.dll").write_bytes(b"cli-v2-longer")
        store.parser_fingerprint()
        assert spy.call_count == first_round + 1     # only the changed file was re-hashed


def test_legacy_recorded_manifest_is_not_flagged_changed_by_new_assembly_field(build):
    write_record(build)
    current = store.parser_fingerprint()
    legacy_recorded = {"commit": UPSTREAM, "overlay": OVERLAY}      # decoded before assembly was recorded
    assert store.fingerprints_differ(legacy_recorded, current) is False
    assert store.fingerprints_differ({"commit": "other", "overlay": OVERLAY}, current) is True
    assert store.fingerprints_differ({**current, "assembly": "0" * 64}, current) is True


def test_freshness_marks_decode_stale_after_unrecorded_dll_change(build, tmp_path):
    write_record(build)
    rid = "fr1"
    d = tmp_path / "data" / "replays" / rid
    (d / "viewer").mkdir(parents=True)
    with patch.object(store, "DATA_REPLAYS_DIR", tmp_path / "data" / "replays"):
        (d / "manifest.json").write_text(json.dumps({"id": rid, "schema": store.STORE_SCHEMA,
                                                      "parser": store.parser_fingerprint()}))
        (d / "viewer" / "match.json").write_text(json.dumps({"schema": store.VIEWER_SCHEMA}))
        assert store.freshness(rid)["state"] == "fresh"
        (build["bin"] / "Replay.Unreal.dll").write_bytes(b"patched-after-decode")
        fresh = store.freshness(rid)
        assert fresh["state"] == "stale" and fresh["decodeRequired"] is True


def test_decoder_info_separates_registered_from_replay_tested(build, monkeypatch):
    write_record(build, tested=[
        "++Ares-Core+release-13.06|bad1543033d11396|validated|exact_source_per_round",
        "++Ares-Core+release-11.07|c5809e86257adfb0|failed|ContentBlockFramer packet0/channel1"])
    monkeypatch.setattr(pipeline, "supported_branches",
                        lambda: ["++Ares-Core+release-11.07", "++Ares-Core+release-13.06",
                                 "++Ares-Core+release-13.08"])
    monkeypatch.setattr(pipeline, "find_decoder", lambda: (Path("dotnet.exe"), Path("CliReader.dll")))
    info = pipeline.decoder_info()
    assert info["supported"] == info["registeredBranches"]          # backward compatible key kept
    tested = {t["branch"]: t["result"] for t in info["replayTested"]}
    assert tested == {"++Ares-Core+release-13.06": "validated", "++Ares-Core+release-11.07": "failed"}
    assert info["registeredWithoutReplayTest"] == ["++Ares-Core+release-13.08"]
    assert info["buildState"] == "verified"
    # A registered transform is never reported as tested-supported by itself.
    assert "++Ares-Core+release-11.07" not in info["replayValidatedBranches"]
    assert info["replayValidatedBranches"] == ["++Ares-Core+release-13.06"]


# ---------------------------------------------------------------- updater semantics

def test_commit_text_records_assemblies_and_tested_matrix_and_round_trips(build, tmp_path, monkeypatch):
    patch_file = tmp_path / "ov.patch"
    patch_file.write_text("+++ b/x/y.cs\n")
    monkeypatch.setattr(update_parser, "OVERLAY_PATCH", patch_file)
    monkeypatch.setattr(update_parser, "ROOT", tmp_path)
    text = update_parser._commit_text(UPSTREAM, OVERLAY, assemblies={n: sha(d) for n, d in DLLS.items()},
                                      tested=[{"branch": "++Ares-Core+release-13.06", "sourceSha256": "bad15430",
                                               "result": "validated", "detail": "exact_source_per_round"}])
    (build["work"] / "parser-commit.txt").write_text(text)
    assert "assembly_set_sha256=" in text
    assert "tested.1=++Ares-Core+release-13.06|bad15430|validated|exact_source_per_round" in text
    assert store.parser_fingerprint()["assembly"] == store.assembly_set_sha256({n: sha(d) for n, d in DLLS.items()})
    assert store.recorded_build()["tested"][0]["result"] == "validated"


def test_current_matches_is_false_for_modified_dll_true_for_verified_and_legacy(build, monkeypatch):
    monkeypatch.setattr(update_parser, "VENDOR", build["root"] / "vendorValorantReplayParser")
    monkeypatch.setattr(update_parser, "BASELINE_FILE", build["root"] / "b.json")
    (build["root"] / "b.json").write_text("{}")
    write_record(build)
    assert update_parser._current_matches(UPSTREAM, OVERLAY) is True
    (build["bin"] / "Replay.Unreal.dll").write_bytes(b"patched")
    assert update_parser._current_matches(UPSTREAM, OVERLAY) is False        # deliberate: rebuild
    write_record(build, assemblies=False)                                    # legacy, no migration needed
    assert update_parser._current_matches(UPSTREAM, OVERLAY) is True


def test_publish_is_refused_when_selftest_is_unvalidated():
    with pytest.raises(update_parser.UpdateError, match="unvalidated"):
        update_parser._require_validated({"state": "unvalidated", "suspect": False,
                                          "reason": "density baseline unavailable"})
    with pytest.raises(update_parser.UpdateError, match="suspect"):
        update_parser._require_validated({"state": "suspect", "suspect": True, "reason": "low"})
    update_parser._require_validated({"state": "validated", "suspect": False, "reason": None})


def test_upstream_pin_checks_out_pinned_commit_and_never_pulls(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(update_parser, "_run", lambda argv, **kw: calls.append([str(a) for a in argv]) or "")
    heads = iter([UPSTREAM])
    monkeypatch.setattr(update_parser, "_git_head", lambda: next(heads))
    update_parser._sync_upstream_ref(UPSTREAM, pull=False)
    flat = [" ".join(c) for c in calls]
    assert any("checkout" in c and UPSTREAM in c for c in flat)
    assert not any(" pull" in c for c in flat)


def test_upstream_pin_fails_closed_when_checkout_lands_elsewhere(monkeypatch):
    monkeypatch.setattr(update_parser, "_run", lambda argv, **kw: "")
    monkeypatch.setattr(update_parser, "_git_head", lambda: "d" * 40)
    with pytest.raises(update_parser.UpdateError, match="pin"):
        update_parser._sync_upstream_ref(UPSTREAM, pull=False)


def test_pin_file_is_read_and_validated(tmp_path, monkeypatch):
    pin = tmp_path / "parser-pin.txt"
    monkeypatch.setattr(update_parser, "PIN_FILE", pin)
    assert update_parser.read_pin() is None
    pin.write_text("# pinned\n" + UPSTREAM + "\n")
    assert update_parser.read_pin() == UPSTREAM
    pin.write_text("not-a-sha\n")
    with pytest.raises(update_parser.UpdateError, match="pin"):
        update_parser.read_pin()

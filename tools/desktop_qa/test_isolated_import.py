"""Tests for tools/desktop_qa/isolated_import.py helpers (lane Q1).

The real import needs the 930 MB .NET tree and a multi-minute decode, so it is
an evidence run (`py -3.12 tools/desktop_qa/isolated_import.py ...`), not a
unit test.  These tests pin the isolated-root mechanics (junctions are
READ-ONLY links and are removed without /s) and the §12 assertion logic.

Run: py -3.12 -m pytest tools/desktop_qa/test_isolated_import.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import isolated_import as iso  # noqa: E402


def _fake_repo(root: Path) -> Path:
    (root / "bot/replay").mkdir(parents=True)
    (root / "bot/__init__.py").write_text("", "utf-8")
    (root / "bot/replay/__init__.py").write_text("", "utf-8")
    (root / "replays").mkdir()
    for meta in iso.REPLAYS.values():
        (root / "replays" / meta["file"]).write_bytes(b"vrf" * 4)
    (root / "dotnet10-sdk").mkdir()
    (root / "dotnet10-sdk/dotnet.exe").write_bytes(b"dotnet")
    (root / "vendorValorantReplayParser").mkdir()
    (root / "vendorValorantReplayParser/marker.txt").write_text("read-only source", "utf-8")
    (root / "VALOPLANT_WORK").mkdir()
    (root / "VALOPLANT_WORK/parser-commit.txt").write_text("upstream_sha=deadbeef\n", "utf-8")
    return root


def test_resolve_replay_by_prefix_file_and_id(tmp_path):
    repo = _fake_repo(tmp_path / "repo")
    assert iso.resolve_replay(repo, "bdb6b4ab")["id"] == "bad1543033d11396"
    full = iso.REPLAYS["c428d9d5"]["file"]
    assert iso.resolve_replay(repo, full)["id"] == "87976b8cd193496b"
    assert iso.resolve_replay(repo, "c5809e86257adfb0")["file"] == iso.REPLAYS["2f7c79fb"]["file"]
    with pytest.raises(iso.ImportError_):
        iso.resolve_replay(repo, "nope")


def test_build_root_creates_readonly_junctions_and_cleanup_keeps_source(tmp_path):
    repo = _fake_repo(tmp_path / "repo")
    home = tmp_path / "home"
    built = iso.build_root(repo, home)
    assert (home / "bot/replay/__init__.py").is_file()
    sdk_link = home / "dotnet10-sdk"
    assert sdk_link.is_dir()
    assert (sdk_link / "dotnet.exe").is_file()  # resolves through the junction
    assert (home / "VALOPLANT_WORK/parser-commit.txt").is_file()
    assert {Path(p).name for p in built["junctions"]} == {"dotnet10-sdk", "vendorValorantReplayParser"}

    for link in built["junctions"]:
        result = iso._remove_junction(Path(link))
        assert result["exit"] == 0, result
        assert not Path(link).exists()
    # the real sources are untouched
    assert (repo / "dotnet10-sdk/dotnet.exe").is_file()
    assert (repo / "vendorValorantReplayParser/marker.txt").is_file()


def test_build_root_refuses_non_empty_home(tmp_path):
    repo = _fake_repo(tmp_path / "repo")
    home = tmp_path / "home"
    home.mkdir()
    (home / "stale.txt").write_text("x", "utf-8")
    with pytest.raises(iso.ImportError_):
        iso.build_root(repo, home)


def test_utility_total_and_contiguity():
    assert iso._utility_total({"P1": {"total": 26}, "P2": {"total": 5}}) == 31
    assert iso._utility_total({"P1": 7}) == 7
    rounds = [{"n": n} for n in range(1, 19)]
    assert iso._contiguous(rounds) is True
    assert iso._contiguous([{"n": 1}, {"n": 3}]) is False


def _match(rounds=18, kills=128, plants=11, defuses=1, players=10, utility=362, drone=3):
    rows = []
    for index in range(rounds):
        rows.append({"n": index + 1, "kills": kills // rounds + (1 if index < kills % rounds else 0),
                     "plants": plants // rounds + (1 if index < plants % rounds else 0),
                     "defuses": defuses // rounds + (1 if index < defuses % rounds else 0)})
    counts = {"P1": {"total": utility, "byKind": {"recon": 17, "blast": 9, "drone": drone}}}
    for index in range(players - 1):
        counts[f"P{index + 2}"] = {"total": 0, "byKind": {}}
    return {"rounds": rows, "players": [{"slot": n} for n in range(players)],
            "utilityCounts": counts, "map": {"name": "Haven"}}


DECODER_OK = {"status": "ready", "parserCommit": iso.EXPECTED_PARSER_COMMIT,
              "buildState": "verified", "buildChangedAssemblies": []}


def test_assert_ready_passes_on_contract_values():
    checks: list = []
    round1 = {**iso.ROUND1, "events": [{}] * 9, "utility": [{}] * 20,
              "schema": "valoplant_round/v2"}
    iso.assert_ready(iso.REPLAYS["bdb6b4ab"], _match(), {"status": "done"}, DECODER_OK,
                     round1, checks)
    assert all(check["ok"] for check in checks), [c for c in checks if not c["ok"]]


def test_assert_ready_flags_wrong_aggregates():
    checks: list = []
    match = _match(kills=127, utility=361, drone=2)
    match["rounds"][4]["n"] = 99  # non-contiguous
    iso.assert_ready(iso.REPLAYS["bdb6b4ab"], match, {"status": "error"}, DECODER_OK,
                     {**iso.ROUND1, "events": [], "utility": []}, checks)
    failed = {check["id"] for check in checks if not check["ok"]}
    assert {"job_done", "kills", "rounds_contiguous", "utility_total",
            "utility_drone_rows", "round1_events", "round1_utility"} <= failed


def test_1306_utility_expectation_documents_stale_live_value():
    meta = iso.REPLAYS["bdb6b4ab"]
    assert meta["utility"] == 362 and meta["utilityStaleLive"] == 359
    assert meta["droneRows"] == 3


def test_assert_failed_requires_honest_single_failure(tmp_path):
    viewer = tmp_path / "viewer"
    viewer.mkdir()
    checks: list = []
    job = {"status": "error", "stage": "error", "message": "unsupported branch"}
    entry = {"status": "failed", "rounds": None}
    manifest = {"status": "failed", "notes": "unsupported branch ++Ares-Core+release-11.07"}
    iso.assert_failed(iso.REPLAYS["2f7c79fb"], job, entry, manifest, [job],
                      viewer, {"ok": True, "rounds": 21, "map": "Sunset"}, checks)
    assert all(check["ok"] for check in checks), [c for c in checks if not c["ok"]]


def test_index_round_count_accepts_count_list_or_int():
    # the container index returns a list of round dicts; the report keeps a count
    assert iso._index_round_count({"roundCount": 21, "roundNumbers": [1] * 21}) == 21
    assert iso._index_round_count({"ok": True, "rounds": [{}] * 21}) == 21
    assert iso._index_round_count({"ok": True, "rounds": 21}) == 21
    assert iso._index_round_count({"ok": False, "error": "ModuleNotFoundError"}) is None


def test_assert_failed_compares_index_round_count_not_the_list():
    checks: list = []
    job = {"status": "error", "stage": "error", "message": "unsupported branch"}
    iso.assert_failed(iso.REPLAYS["2f7c79fb"], job, {"status": "failed", "rounds": None},
                      {"status": "failed", "notes": "unsupported branch"}, [job],
                      Path("/nonexistent-viewer"),
                      {"ok": True, "roundCount": 21, "roundNumbers": list(range(21))}, checks)
    index = [check for check in checks if check["id"] == "index_rounds"]
    assert index and index[0]["ok"] and index[0]["actual"] == 21, index


def test_assert_failed_flags_retry_loop_and_viewer_data(tmp_path):
    viewer = tmp_path / "viewer"
    viewer.mkdir()
    (viewer / "match.json").write_text("{}", "utf-8")
    checks: list = []
    job = {"status": "error", "stage": "error", "message": "boom"}
    iso.assert_failed(iso.REPLAYS["2f7c79fb"], job, {"status": "failed", "rounds": None},
                      {"status": "failed", "notes": "boom"}, [job, job], viewer,
                      {"ok": True, "rounds": 20}, checks)
    failed = {check["id"] for check in checks if not check["ok"]}
    assert {"no_viewer_data", "no_retry_loop", "index_rounds"} <= failed
    assert json.dumps(checks)  # serialisable

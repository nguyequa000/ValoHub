"""Tests for tools/desktop_qa/guard.py (lane Q1).

Run: py -3.12 -m pytest tools/desktop_qa/test_guard.py -q
"""
from __future__ import annotations

import io
import json
import os
import sys
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import guard  # noqa: E402


def _build_root(root: Path) -> Path:
    (root / "data/replays/aaaa1111/viewer").mkdir(parents=True)
    (root / "data/replays/aaaa1111/viewer/me.json").write_text('{"player":"P10"}', "utf-8")
    (root / "data/replays/aaaa1111/manifest.json").write_text("{}", "utf-8")
    (root / "data/replays/demo").mkdir(parents=True)
    (root / "data/replays/demo/manifest.json").write_text("{}", "utf-8")
    (root / "replays").mkdir()
    (root / "replays/a.vrf").write_bytes(b"vrf-a" * 10)
    (root / "replays/README.md").write_text("not pinned", "utf-8")
    decoder = root / guard.DECODER_NET10
    decoder.mkdir(parents=True)
    (decoder / "CliReader.dll").write_bytes(b"dll")
    (decoder / "Replay.Models.dll").write_bytes(b"models")
    (root / "dotnet10-sdk").mkdir()
    (root / "dotnet10-sdk/dotnet.exe").write_bytes(b"dotnet")
    (root / "dotnet10-sdk/host").mkdir()
    (root / "VALOPLANT_WORK").mkdir()
    (root / "VALOPLANT_WORK/parser-commit.txt").write_text("upstream_sha=deadbeef\n", "utf-8")
    return root


def _run(argv):
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = guard.main(argv)
    return code, json.loads(buffer.getvalue())


def test_me_json_is_pinned_under_records_and_groups_sum_to_total(tmp_path):
    # Real tree: data/replays/** is 59 files, but viewer/me.json (bad1543033d11396)
    # is the identity tripwire and lands in the ``records`` group, so the
    # ``data_replays`` group counts 58. The group counts must still sum to the
    # total number of pinned files - that is what caught the stale baseline 59.
    root = _build_root(tmp_path / "repo")
    me = root / guard.ME_JSON
    me.parent.mkdir(parents=True, exist_ok=True)
    me.write_text('{"player": "P10"}', "utf-8")
    code, report = _run(["snapshot", "--root", str(root), "--out", str(tmp_path / "s.json"),
                         "--allow-live-read"])
    assert code == 0, report
    assert report["files"][guard.ME_JSON]["group"] == "records"
    counts = report["counts"]
    groups = {k: v for k, v in counts.items() if k not in ("total_files",)}
    assert sum(groups.values()) == counts["total_files"] == len(report["files"])
    assert counts["data_replays"] == 3  # aaaa1111/me.json + 2 manifests
    assert counts["records"] == 2      # parser-commit.txt + bad1543.../viewer/me.json


def test_snapshot_records_groups_and_verify_is_clean(tmp_path):
    root = _build_root(tmp_path / "repo")
    out = tmp_path / "snap.json"
    code, report = _run(["snapshot", "--root", str(root), "--out", str(out)])
    assert code == 0 and report["ok"] is True
    assert out.is_file()
    files = report["files"]
    assert files["replays/a.vrf"]["sha256"] == guard.sha256_file(root / "replays/a.vrf")
    assert files["data/replays/aaaa1111/viewer/me.json"]["group"] == "data_replays"
    assert files["VALOPLANT_WORK/parser-commit.txt"]["group"] == "records"
    assert files["dotnet10-sdk/dotnet.exe"]["group"] == "dotnet_sdk"
    assert "replays/README.md" not in files
    assert [row["name"] for row in report["listings"][guard.DOTNET_SDK]] == ["dotnet.exe", "host"]

    code, verify = _run(["verify", "--root", str(root), "--against", str(out)])
    assert code == 0
    assert verify["ok"] is True and verify["summary"] == {"added": 0, "changed": 0, "removed": 0}


def test_verify_reports_changed_added_removed_and_exits_1(tmp_path):
    root = _build_root(tmp_path / "repo")
    snap = tmp_path / "snap.json"
    assert _run(["snapshot", "--root", str(root), "--out", str(snap)])[0] == 0

    (root / "replays/a.vrf").write_bytes(b"vrf-b" * 10)
    (root / "data/replays/new/manifest.json").parent.mkdir(parents=True)
    (root / "data/replays/new/manifest.json").write_text("{}", "utf-8")
    (root / "data/replays/demo/manifest.json").unlink()

    code, verify = _run(["verify", "--root", str(root), "--against", str(snap)])
    assert code == 1 and verify["ok"] is False
    assert verify["changed"][0]["path"] == "replays/a.vrf"
    assert "data/replays/new/manifest.json" in verify["added"]
    assert "data/replays/demo/manifest.json" in verify["removed"]


def test_verify_reports_dotnet_listing_changes(tmp_path):
    root = _build_root(tmp_path / "repo")
    snap = tmp_path / "snap.json"
    _run(["snapshot", "--root", str(root), "--out", str(snap)])
    (root / "dotnet10-sdk/sdk").mkdir()
    code, verify = _run(["verify", "--root", str(root), "--against", str(snap)])
    assert code == 1
    assert verify["listings"][guard.DOTNET_SDK]["added"] == ["sdk"]


def test_fast_reuses_hashes_without_rereading(tmp_path):
    root = _build_root(tmp_path / "repo")
    snap = tmp_path / "snap.json"
    _run(["snapshot", "--root", str(root), "--out", str(snap)])
    code, verify = _run(["verify", "--root", str(root), "--against", str(snap), "--fast"])
    assert code == 0 and verify["ok"] is True
    assert verify["counts"]["total_files"] > 0


def test_extra_dir_is_pinned_and_guarded(tmp_path):
    root = _build_root(tmp_path / "repo")
    appdata = tmp_path / "appdata"
    (appdata / "logs").mkdir(parents=True)
    (appdata / "logs/run.jsonl").write_text("{}\n", "utf-8")
    snap = tmp_path / "snap.json"
    code, report = _run(["snapshot", "--root", str(root), "--out", str(snap),
                         "--extra-dir", f"app={appdata}"])
    assert code == 0
    assert "extra:app/logs/run.jsonl" in report["files"]
    (appdata / "logs/run.jsonl").write_text('{"x":1}\n', "utf-8")
    code, verify = _run(["verify", "--root", str(root), "--against", str(snap),
                         "--extra-dir", f"app={appdata}"])
    assert code == 1
    assert verify["changed"][0]["path"] == "extra:app/logs/run.jsonl"


def test_live_library_requires_allow_live_read(tmp_path):
    root = _build_root(tmp_path / "repo")
    live = root / "data/replays/bad1543033d11396/viewer"
    live.mkdir(parents=True)
    (live / "me.json").write_text('{"player":"P10"}', "utf-8")
    code, report = _run(["snapshot", "--root", str(root)])
    assert code == 2
    assert report["error"] == "live_library_requires_allow_live_read"
    code, report = _run(["snapshot", "--root", str(root), "--allow-live-read"])
    assert code == 0 and report["ok"] is True
    assert report["files"]["data/replays/bad1543033d11396/viewer/me.json"]["group"] == "records"


def test_full_hash_catches_same_size_same_mtime_edit(tmp_path):
    """--fast deliberately trusts (size, mtime_ns); a full run must still catch it."""
    root = _build_root(tmp_path / "repo")
    target = root / "replays/a.vrf"
    stat = target.stat()
    snap = tmp_path / "snap.json"
    _run(["snapshot", "--root", str(root), "--out", str(snap)])
    target.write_bytes(b"vrf-Z" * 10)
    os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    code, verify = _run(["verify", "--root", str(root), "--against", str(snap)])
    assert code == 1 and verify["changed"][0]["path"] == "replays/a.vrf"

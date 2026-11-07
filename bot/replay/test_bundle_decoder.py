"""bundle_decoder on fake trees."""
import hashlib
import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("bundle_decoder", Path(__file__).resolve().parents[2] / "tools" / "bundle_decoder.py")
bd = importlib.util.module_from_spec(SPEC)
sys.modules["bundle_decoder"] = bd
SPEC.loader.exec_module(bd)

ASSEMBLIES = ["CliReader.dll", "Replay.Models.dll"]


def _tree(tmp_path):
    dn = tmp_path / "sdk"
    (dn / "host" / "fxr" / "1").mkdir(parents=True)
    (dn / "host" / "fxr" / "1" / "hostfxr.dll").write_bytes(b"h")
    (dn / "shared" / "Microsoft.NETCore.App" / "10.0.1").mkdir(parents=True)
    (dn / "shared" / "Microsoft.NETCore.App" / "10.0.1" / "x.dll").write_bytes(b"r")
    (dn / "sdk").mkdir()
    (dn / "sdk" / "big.dll").write_bytes(b"NOT SHIPPED")
    (dn / "dotnet.exe").write_bytes(b"d")
    dec = tmp_path / "dec"
    dec.mkdir()
    lines = []
    for n in ASSEMBLIES:
        (dec / n).write_bytes(n.encode())
        lines.append(f"assembly.{n}={hashlib.sha256(n.encode()).hexdigest()}")
    rec = tmp_path / "parser-commit.txt"
    rec.write_text("upstream_sha=abc\r\n" + "\r\n".join(lines) + "\r\n")
    tr = tmp_path / "tr"
    tr.mkdir()
    (tr / "A.cs").write_text('"++Ares-Core+release-13.06" "++Ares-Core+release-13.09"')
    (tr / "B.cs").write_text('"++Ares-Core+release-11.07"')
    return dn, dec, rec, tr


def _snapshot(*roots):
    return {str(p): (p.stat().st_size, hashlib.sha256(p.read_bytes()).hexdigest())
            for r in roots for p in r.rglob("*") if p.is_file()} | {}


def _args(tmp_path, out, dn, dec, rec, tr, *extra):
    return ["--out", str(out), "--dotnet-src", str(dn), "--decoder-src", str(dec), "--record", str(rec),
            "--transforms-src", str(tr), "--no-self-test", *extra]


def test_success_layout_manifest_and_sources_untouched(tmp_path):
    dn, dec, rec, tr = _tree(tmp_path)
    before = _snapshot(dn, dec, tr)
    rec_before = rec.read_bytes()
    out = tmp_path / "out"
    assert bd.main(_args(tmp_path, out, dn, dec, rec, tr)) == 0
    assert (out / "runtime" / "dotnet" / "dotnet.exe").is_file()
    assert (out / "runtime" / "dotnet" / "host" / "fxr" / "1" / "hostfxr.dll").is_file()
    assert not (out / "runtime" / "dotnet" / "sdk").exists()
    assert (out / "decoder" / "net10.0" / "CliReader.dll").read_bytes() == b"CliReader.dll"
    assert (out / "decoder" / "parser-commit.txt").read_bytes() == rec_before
    branches = json.loads((out / "decoder" / "registered-branches.json").read_text())
    assert branches == ["++Ares-Core+release-11.07", "++Ares-Core+release-13.06", "++Ares-Core+release-13.09"]
    man = json.loads((out / "decoder" / "bundle-manifest.json").read_text())
    assert man["dotnetVersion"] == "10.0.1" and man["builtFrom"]
    for rel, meta in man["files"].items():
        assert hashlib.sha256((out / rel).read_bytes()).hexdigest() == meta["sha256"]
    assert "decoder/net10.0/CliReader.dll" in man["files"]
    assert _snapshot(dn, dec, tr) == before and rec.read_bytes() == rec_before


def test_hash_mismatch_fails_closed_publishes_nothing(tmp_path):
    dn, dec, rec, tr = _tree(tmp_path)
    (dec / "Replay.Models.dll").write_bytes(b"tampered")
    out = tmp_path / "out"
    assert bd.main(_args(tmp_path, out, dn, dec, rec, tr)) != 0
    assert not out.exists()
    assert [p.name for p in tmp_path.iterdir() if "stage" in p.name] == []
    # negative control: restoring the byte makes the same command succeed
    (dec / "Replay.Models.dll").write_bytes(b"Replay.Models.dll")
    assert bd.main(_args(tmp_path, out, dn, dec, rec, tr)) == 0


def test_missing_assembly_and_empty_record_fail(tmp_path):
    dn, dec, rec, tr = _tree(tmp_path)
    (dec / "CliReader.dll").unlink()
    assert bd.main(_args(tmp_path, tmp_path / "o1", dn, dec, rec, tr)) != 0
    rec.write_text("upstream_sha=abc\n")
    (dec / "CliReader.dll").write_bytes(b"CliReader.dll")
    assert bd.main(_args(tmp_path, tmp_path / "o2", dn, dec, rec, tr)) != 0


def test_refuses_nonempty_out_without_force(tmp_path):
    dn, dec, rec, tr = _tree(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    (out / "keep.txt").write_text("precious")
    assert bd.main(_args(tmp_path, out, dn, dec, rec, tr)) != 0
    assert (out / "keep.txt").read_text() == "precious"
    assert bd.main(_args(tmp_path, out, dn, dec, rec, tr, "--force")) == 0
    assert (out / "decoder" / "net10.0" / "CliReader.dll").is_file()


def test_not_a_dotnet_install_fails(tmp_path):
    dn, dec, rec, tr = _tree(tmp_path)
    shutil.rmtree(dn / "host")
    assert bd.main(_args(tmp_path, tmp_path / "out", dn, dec, rec, tr)) != 0
    assert not (tmp_path / "out").exists()

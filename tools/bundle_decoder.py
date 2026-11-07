"""Build the offline decoder bundle: private .NET runtime + byte-identical CliReader decoder.

    py -3.12 tools/bundle_decoder.py --out <dir> [--dotnet-src D] [--decoder-src D] [--record F]
                                     [--transforms-src D] [--force]

Output layout (PLAN C3/D4):
    <out>/runtime/dotnet/{dotnet.exe, host/, shared/Microsoft.NETCore.App/<ver>/}
    <out>/decoder/net10.0/*              byte-identical copy of the decoder folder
    <out>/decoder/parser-commit.txt      identical copy of the build record
    <out>/decoder/registered-branches.json
    <out>/decoder/bundle-manifest.json

Fail-closed: every decoder assembly named by ``assembly.*`` in the record must hash to the
recorded SHA-256 *before* anything is published; the bundle is built in a sibling temp dir and
renamed into place.  Sources are only ever read.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRANCH_RE = r'"(\+\+Ares-Core\+release-[^"]+)"'


class BundleError(Exception):
    pass


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def parse_record(record: Path) -> dict[str, str]:
    out = {}
    for line in record.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"assembly\.(.+?)=([0-9a-fA-F]{64})\s*$", line.strip())
        if m:
            out[m.group(1)] = m.group(2).lower()
    return out


def verify_assemblies(decoder_src: Path, record: Path) -> dict[str, str]:
    expected = parse_record(record)
    if not expected:
        raise BundleError(f"no assembly.* lines in {record}")
    for name, want in sorted(expected.items()):
        f = decoder_src / name
        if not f.is_file():
            raise BundleError(f"decoder assembly missing: {f}")
        got = sha256(f)
        if got != want:
            raise BundleError(f"SHA-256 mismatch for {name}: record {want} != actual {got}")
    return expected


def registered_branches(transforms_src: Path) -> list[str]:
    found: set[str] = set()
    for f in transforms_src.glob("*.cs"):
        found.update(re.findall(BRANCH_RE, f.read_text(encoding="utf-8", errors="replace")))
    return sorted(found)


def copy_runtime(dotnet_src: Path, dest: Path) -> str:
    shared = dotnet_src / "shared" / "Microsoft.NETCore.App"
    versions = sorted(p for p in shared.iterdir() if p.is_dir()) if shared.is_dir() else []
    if not (dotnet_src / "dotnet.exe").is_file() or not (dotnet_src / "host").is_dir() or not versions:
        raise BundleError(f"not a .NET install (need dotnet.exe, host/, shared/Microsoft.NETCore.App/<ver>): {dotnet_src}")
    version = versions[-1].name
    dest.mkdir(parents=True)
    shutil.copy2(dotnet_src / "dotnet.exe", dest / "dotnet.exe")
    shutil.copytree(dotnet_src / "host", dest / "host")
    shutil.copytree(versions[-1], dest / "shared" / "Microsoft.NETCore.App" / version)
    return version


def self_test(root: Path) -> dict:
    dotnet = root / "runtime" / "dotnet" / "dotnet.exe"
    dll = root / "decoder" / "net10.0" / "CliReader.dll"
    env = {"PATH": r"C:\Windows\System32", "SystemRoot": os.environ.get("SystemRoot", r"C:\Windows"),
           "DOTNET_MULTILEVEL_LOOKUP": "0", "DOTNET_NOLOGO": "1", "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
           "DOTNET_ROOT": str(dotnet.parent)}
    r = subprocess.run([str(dotnet), str(dll), "--help"], env=env, capture_output=True, text=True, timeout=120,
                       cwd=str(root))
    return {"command": "dotnet.exe CliReader.dll --help (PATH=C:\\Windows\\System32)",
            "exit": r.returncode, "stdout_head": (r.stdout or "")[:300], "stderr_head": (r.stderr or "")[:300]}


def build(out: Path, dotnet_src: Path, decoder_src: Path, record: Path, transforms_src: Path | None,
          force: bool = False, run_self_test: bool = True) -> dict:
    out = Path(out)
    if out.exists() and any(out.iterdir()) and not force:
        raise BundleError(f"--out is not empty (use --force): {out}")
    for label, p in (("decoder-src", decoder_src), ("dotnet-src", dotnet_src)):
        if not Path(p).is_dir():
            raise BundleError(f"{label} not found: {p}")
    if not Path(record).is_file():
        raise BundleError(f"record not found: {record}")
    verify_assemblies(decoder_src, record)  # fail closed before writing anything

    out.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=out.name + ".stage-", dir=out.parent))
    try:
        version = copy_runtime(dotnet_src, stage / "runtime" / "dotnet")
        shutil.copytree(decoder_src, stage / "decoder" / "net10.0")
        shutil.copy2(record, stage / "decoder" / "parser-commit.txt")
        branches = registered_branches(transforms_src) if transforms_src and Path(transforms_src).is_dir() else []
        if transforms_src and not branches:
            raise BundleError(f"no registered branches found in {transforms_src}")
        (stage / "decoder" / "registered-branches.json").write_text(json.dumps(branches, indent=2) + "\n", encoding="utf-8")
        files = {}
        for f in sorted(stage.rglob("*")):
            if f.is_file():
                files[f.relative_to(stage).as_posix()] = {"sha256": sha256(f), "size": f.stat().st_size}
        manifest = {"dotnetVersion": version, "builtFrom": str(ROOT),
                    "sources": {"dotnet": str(dotnet_src), "decoder": str(decoder_src), "record": str(record),
                                "transforms": str(transforms_src) if transforms_src else None},
                    "branchCount": len(branches), "fileCount": len(files), "files": files}
        (stage / "decoder" / "bundle-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        if run_self_test:
            st = self_test(stage)
            manifest["selfTest"] = st
            if st["exit"] != 0:
                raise BundleError(f"self-test failed: {st}")
            (stage / "decoder" / "bundle-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        if out.exists():
            shutil.rmtree(out)
        os.replace(stage, out)
        return manifest
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--dotnet-src", default=str(ROOT / "dotnet10-sdk"))
    ap.add_argument("--decoder-src", default=str(ROOT / "vendorValorantReplayParser/src/CliReader/bin/Release/net10.0"))
    ap.add_argument("--record", default=str(ROOT / "VALOPLANT_WORK/parser-commit.txt"))
    ap.add_argument("--transforms-src", default=str(ROOT / "vendorValorantReplayParser/src/Replay.Encoding/PayloadEncryption/VersionedTransforms"))
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--no-self-test", action="store_true")
    a = ap.parse_args(argv)
    try:
        m = build(Path(a.out), Path(a.dotnet_src), Path(a.decoder_src), Path(a.record),
                  Path(a.transforms_src), force=a.force, run_self_test=not a.no_self_test)
    except BundleError as exc:
        print(f"bundle_decoder: FAILED: {exc}", file=sys.stderr)
        return 2
    print(f"bundle_decoder: OK out={a.out} dotnet={m['dotnetVersion']} files={m['fileCount']} branches={m['branchCount']}")
    if "selfTest" in m:
        print(f"self-test exit={m['selfTest']['exit']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

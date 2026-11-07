#!/usr/bin/env python3
"""Byte-level guard for the ValoHub desktop migration (lane Q1).

`guard.py snapshot|verify` records / re-checks SHA-256 for the read-only
artifacts the desktop app must hand over untouched:

* ``data/replays/**``           - the isolated library (59 files at baseline)
* ``replays/*.vrf``             - the three original recordings
* decoder ``net10.0`` tree      - assemblies the bundle ships byte-identical
* ``dotnet10-sdk`` top level    - listing + ``dotnet.exe`` hash
* ``VALOPLANT_WORK/parser-commit.txt``
* ``data/replays/bad1543033d11396/viewer/me.json`` (user data)
* any ``--extra-dir`` roots (e.g. an app data dir)

The tool never writes inside ``--root``: snapshots go to ``--out`` or stdout.
``verify`` reports ADDED / CHANGED / REMOVED and exits 1 on any difference.

Stdlib only, Python 3.12.  ``--fast`` re-uses the previous manifest's hash when
``(size, mtime_ns)`` are unchanged; a real change still forces a re-hash.

Live-library protection (PLAN C6): a root that contains a populated
``data/replays/<store id>`` tree is treated as live and requires
``--allow-live-read``.  Guarding is read-only either way.

Report schema: ``valohub.guard/v1``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "valohub.guard/v1"
DECODER_NET10 = "vendorValorantReplayParser/src/CliReader/bin/Release/net10.0"
DOTNET_SDK = "dotnet10-sdk"
PARSER_RECORD = "VALOPLANT_WORK/parser-commit.txt"
ME_JSON = "data/replays/bad1543033d11396/viewer/me.json"
LIVE_MARKER = "data/replays/bad1543033d11396"

# Baseline counts measured on 2026-11-02 (DATA-BASELINE.md). Reported, never
# enforced: a legitimately imported replay must not turn the guard red.
# ``data/replays/**`` holds 59 files, but ``.../bad1543033d11396/viewer/me.json``
# is pinned under the ``records`` group (the identity tripwire), so the
# ``data_replays`` group counts 58 and ``total_files`` still sees all 59.
EXPECTED = {
    "data_replays": 58,
    "replays_vrf": 3,
    "decoder_net10": 43,
}

CHUNK = 1 << 20


class GuardError(Exception):
    """Usage / environment problem (exit 2), not a byte difference."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def _rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _walk_files(base: Path):
    """Deterministic recursive file walk (POSIX-style relative keys)."""
    out = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames.sort()
        for name in sorted(filenames):
            out.append(Path(dirpath) / name)
    return out


def _targets(root: Path, extra_dirs) -> dict[str, tuple[Path, str]]:
    """Map manifest key -> (path, group) for every artifact the guard pins."""
    targets: dict[str, tuple[Path, str]] = {}

    def add(path: Path, group: str, override: bool = False) -> None:
        if path.is_file() and path.resolve().is_relative_to(root):
            key = _rel(path, root)
            if override:
                targets[key] = (path, group)
            else:
                targets.setdefault(key, (path, group))

    data = root / "data/replays"
    if data.is_dir():
        for path in _walk_files(data):
            add(path, "data_replays")
    originals = root / "replays"
    if originals.is_dir():
        for path in sorted(originals.glob("*.vrf")):
            add(path, "replays_vrf")
    decoder = root / DECODER_NET10
    if decoder.is_dir():
        for path in _walk_files(decoder):
            add(path, "decoder_net10")
    add(root / PARSER_RECORD, "records")
    add(root / ME_JSON, "records", override=True)
    add(root / DOTNET_SDK / "dotnet.exe", "dotnet_sdk")

    for label, directory in extra_dirs:
        if not directory.is_dir():
            raise GuardError(f"--extra-dir is not a directory: {directory}")
        for path in _walk_files(directory):
            targets[f"extra:{label}/{_rel(path, directory)}"] = (path, f"extra:{label}")
    return targets


def _listing(root: Path, relative: str) -> list[dict]:
    base = root / relative
    if not base.is_dir():
        return []
    rows = []
    for entry in sorted(base.iterdir(), key=lambda p: p.name.lower()):
        row: dict = {"name": entry.name, "type": "dir" if entry.is_dir() else "file"}
        if entry.is_file():
            try:
                row["size"] = entry.stat().st_size
            except OSError:
                row["size"] = None
        rows.append(row)
    return rows


def build_manifest(root: Path, extra_dirs=(), fast: bool = False,
                   previous: dict | None = None, workers: int = 8) -> dict:
    root = root.resolve()
    if not root.is_dir():
        raise GuardError(f"--root is not a directory: {root}")
    targets = _targets(root, extra_dirs)
    if not targets:
        raise GuardError(f"no guardable artifacts under {root} (wrong --root?)")

    prev_files = {}
    if fast and previous:
        prev_files = previous.get("files") or {}

    def one(key: str) -> tuple[str, dict]:
        path, group = targets[key]
        stat = path.stat()
        meta: dict = {"sha256": None, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
        use_fast = (
            fast
            and key in prev_files
            and prev_files[key].get("size") == meta["size"]
            and prev_files[key].get("mtime_ns") == meta["mtime_ns"]
            and prev_files[key].get("sha256")
        )
        if use_fast:
            meta["sha256"] = prev_files[key]["sha256"]
            meta["hash_source"] = "fast-meta"
        else:
            meta["sha256"] = sha256_file(path)
            meta["hash_source"] = "hashed"
        meta["group"] = group
        return key, meta

    started = time.perf_counter()
    files: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(targets)))) as pool:
        for key, meta in pool.map(one, sorted(targets)):
            files[key] = meta
    elapsed = time.perf_counter() - started

    counts: dict[str, int] = {}
    for meta in files.values():
        counts[meta["group"]] = counts.get(meta["group"], 0) + 1
    counts["total_files"] = len(files)

    warnings = []
    for group, expected in EXPECTED.items():
        actual = counts.get(group, 0)
        if actual != expected:
            warnings.append(f"{group}: {actual} files (baseline {expected})")

    return {
        "schema": SCHEMA,
        "root": str(root),
        "created": _now(),
        "mode": "fast" if fast else "full",
        "files": files,
        "counts": counts,
        "expected": EXPECTED,
        "listings": {DOTNET_SDK: _listing(root, DOTNET_SDK)},
        "hash_seconds": round(elapsed, 3),
        "warnings": warnings,
    }


def compare(before: dict, after: dict) -> dict:
    old = before.get("files") or {}
    new = after.get("files") or {}
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    changed = []
    for key in sorted(set(old) & set(new)):
        a, b = old[key], new[key]
        if a.get("sha256") != b.get("sha256") or a.get("size") != b.get("size"):
            changed.append({
                "path": key,
                "was": a.get("sha256"),
                "now": b.get("sha256"),
                "sizeWas": a.get("size"),
                "sizeNow": b.get("size"),
            })
    listings = {}
    old_list = {row["name"]: row for row in (before.get("listings") or {}).get(DOTNET_SDK, [])}
    new_list = {row["name"]: row for row in (after.get("listings") or {}).get(DOTNET_SDK, [])}
    if old_list or new_list:
        listings[DOTNET_SDK] = {
            "added": sorted(set(new_list) - set(old_list)),
            "removed": sorted(set(old_list) - set(new_list)),
            "changed": sorted(
                name for name in set(old_list) & set(new_list)
                if old_list[name] != new_list[name]
            ),
        }
    listing_diff = any(
        entry["added"] or entry["removed"] or entry["changed"]
        for entry in listings.values()
    )
    ok = not (added or removed or changed or listing_diff)
    return {
        "ok": ok,
        "checked": len(new),
        "summary": {"added": len(added), "changed": len(changed), "removed": len(removed)},
        "added": added,
        "changed": changed,
        "removed": removed,
        "listings": listings,
        "counts": after.get("counts"),
        "warnings": after.get("warnings") or [],
    }


def _parse_extra(values) -> list[tuple[str, Path]]:
    pairs = []
    for raw in values or []:
        if "=" in raw and not raw.startswith("="):
            label, _, path = raw.partition("=")
        else:
            path = raw
            label = Path(raw).name or "extra"
        pairs.append((label, Path(path).expanduser().resolve()))
    return pairs


def _emit(report: dict) -> None:
    print(json.dumps(report, indent=2, ensure_ascii=False))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="SHA-256 guard for ValoHub desktop QA")
    parser.add_argument("command", choices=["snapshot", "verify"])
    parser.add_argument("--root", required=True, help="repo / app root to guard")
    parser.add_argument("--out", help="snapshot output path (default stdout)")
    parser.add_argument("--against", help="manifest to verify against (verify)")
    parser.add_argument("--extra-dir", action="append", default=[],
                        help="additional root to hash, optionally label=path")
    parser.add_argument("--fast", action="store_true",
                        help="skip re-hashing files whose (size, mtime_ns) match")
    parser.add_argument("--allow-live-read", action="store_true",
                        help="permit a root containing the live replay library (read-only)")
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args(argv)

    root = Path(args.root).expanduser().resolve()
    extra = _parse_extra(args.extra_dir)
    live = (root / LIVE_MARKER).exists()
    if live and not args.allow_live_read:
        _emit({
            "schema": SCHEMA,
            "ok": False,
            "error": "live_library_requires_allow_live_read",
            "root": str(root),
            "hint": "pass --allow-live-read (the guard only reads)",
        })
        return 2

    try:
        if args.command == "snapshot":
            if args.against:
                raise GuardError("--against is only valid with verify")
            manifest = build_manifest(root, extra, fast=args.fast, workers=args.workers)
            manifest["ok"] = True
            if args.out:
                out = Path(args.out).expanduser()
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
                               encoding="utf-8")
                manifest["out"] = str(out)
            _emit(manifest)
            return 0

        if not args.against:
            raise GuardError("--against FILE is required with verify")
        against = Path(args.against).expanduser()
        if not against.is_file():
            raise GuardError(f"manifest not found: {against}")
        before = json.loads(against.read_text(encoding="utf-8"))
        if before.get("schema") != SCHEMA:
            raise GuardError(f"unexpected manifest schema: {before.get('schema')!r}")
        after = build_manifest(root, extra, fast=args.fast, previous=before,
                               workers=args.workers)
        report = compare(before, after)
        report.update({
            "schema": SCHEMA,
            "command": "verify",
            "root": str(root),
            "against": str(against),
            "verifiedAt": _now(),
        })
        _emit(report)
        return 0 if report["ok"] else 1
    except GuardError as exc:
        _emit({"schema": SCHEMA, "ok": False, "error": str(exc), "root": str(root)})
        return 2


if __name__ == "__main__":
    sys.exit(main())

"""Non-destructive, resumable import of a legacy ValoHub checkout into a desktop data folder (contract C2).

Copies ``data/replays/**`` and ``replays/*.vrf`` only.  Never modifies the source, never overwrites a
differing destination file (recorded as a conflict), skips files that are already equal (size+sha256),
and copies through ``<dest>.part`` + ``os.replace`` so an interrupted run resumes cleanly.

CLI: ``py -3.12 -m bot.replay.migrate --from <legacy repo> --home <dir> [--dry-run] [--json]``
Exit codes: 0 ok, 2 refused / bad arguments, 3 finished with conflicts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

EXIT_OK, EXIT_REFUSED, EXIT_CONFLICT = 0, 2, 3
PART_SUFFIX = ".part"
_CHUNK = 1 << 20


class MigrationRefused(ValueError):
    """Source/destination combination that must not be copied."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(_CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def default_dest() -> Path:
    from bot.replay import paths
    return paths.default_data_root()


def _validate(src_root: Path, dest: Path) -> tuple[Path, Path]:
    src = Path(os.path.abspath(src_root))
    dst = Path(os.path.abspath(dest))
    if not src.is_dir():
        raise MigrationRefused(f"source is not a folder: {src}")
    real_src, real_dst = src.resolve(), dst.resolve()
    if real_src == real_dst:
        raise MigrationRefused(f"source and destination are the same folder: {src}")
    if real_src in real_dst.parents:
        raise MigrationRefused(f"destination {dst} is inside the source {src}")
    if real_dst in real_src.parents:
        raise MigrationRefused(f"source {src} is inside the destination {dst}")
    return src, dst


def _scan(src: Path) -> list[tuple[str, Path]]:
    """(posix rel path, absolute path) for every file to copy, sorted."""
    found: list[tuple[str, Path]] = []
    library = src / "data" / "replays"
    if library.is_dir():
        for path in sorted(library.rglob("*")):
            if path.is_file():
                found.append((path.relative_to(src).as_posix(), path))
    originals = src / "replays"
    if originals.is_dir():
        for path in sorted(originals.glob("*.vrf")):
            if path.is_file():
                found.append((path.relative_to(src).as_posix(), path))
    return found


def _classify(src_file: Path, dest_file: Path) -> str:
    """'new' | 'equal' | 'conflict' (a leftover .part is ignored: it is re-copied)."""
    if not dest_file.exists():
        return "new"
    if dest_file.stat().st_size == src_file.stat().st_size and _sha(dest_file) == _sha(src_file):
        return "equal"
    return "conflict"


def plan_import(src_root, dest=None) -> dict:
    """Dry run: what ``import_legacy`` would do.  Writes nothing."""
    src, dst = _validate(Path(src_root), Path(dest) if dest is not None else default_dest())
    files = _scan(src)
    plan = {"source": str(src), "dest": str(dst), "files": len(files), "bytes": 0,
            "stores": [], "vrf": 0, "to_copy": 0, "skipped": 0, "conflicts": []}
    stores = set()
    for rel, path in files:
        size = path.stat().st_size
        plan["bytes"] += size
        parts = rel.split("/")
        if rel.startswith("data/replays/") and len(parts) > 3:
            stores.add(parts[2])
        if rel.startswith("replays/"):
            plan["vrf"] += 1
        state = _classify(path, dst / rel)
        if state == "new":
            plan["to_copy"] += 1
        elif state == "equal":
            plan["skipped"] += 1
        else:
            plan["conflicts"].append(rel)
    plan["stores"] = sorted(stores)
    return plan


def _copy_one(src_file: Path, dest_file: Path, rel: str, state: dict,
              progress: Callable[[int, int, str], None] | None) -> None:
    dest_file.parent.mkdir(parents=True, exist_ok=True)
    part = dest_file.with_name(dest_file.name + PART_SUFFIX)
    with src_file.open("rb") as reader, part.open("wb") as writer:
        for block in iter(lambda: reader.read(_CHUNK), b""):
            writer.write(block)
            state["done"] += len(block)
            if progress:
                progress(state["done"], state["total"], rel)
        writer.flush()
        os.fsync(writer.fileno())
    shutil.copystat(src_file, part)
    os.replace(part, dest_file)


def import_legacy(src_root, dest=None, *, progress: Callable[[int, int, str], None] | None = None) -> dict:
    """Copy the legacy library into ``dest`` (default: the desktop data root).  Returns the report dict
    that is also written to ``<dest>/VALOPLANT_WORK/migration.json``."""
    src, dst = _validate(Path(src_root), Path(dest) if dest is not None else default_dest())
    files = _scan(src)
    started = _now()
    state = {"done": 0, "total": sum(p.stat().st_size for _, p in files)}
    copied: list[str] = []
    skipped: list[str] = []
    conflicts: list[str] = []
    copied_bytes = 0
    dst.mkdir(parents=True, exist_ok=True)
    for rel, path in files:
        target = dst / rel
        verdict = _classify(path, target)
        if verdict == "equal":
            skipped.append(rel)
            state["done"] += path.stat().st_size
            if progress:
                progress(state["done"], state["total"], rel)
            continue
        if verdict == "conflict":
            conflicts.append(rel)
            state["done"] += path.stat().st_size
            continue
        _copy_one(path, target, rel, state, progress)
        copied.append(rel)
        copied_bytes += path.stat().st_size
    report = {"source": str(src), "dest": str(dst), "started": started, "finished": _now(),
              "copied": copied, "skipped": skipped, "conflicts": conflicts, "bytes": copied_bytes,
              "total_bytes": state["total"]}
    work = dst / "VALOPLANT_WORK"
    work.mkdir(parents=True, exist_ok=True)
    out = work / "migration.json"
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, out)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bot.replay.migrate",
                                     description="Copy a legacy ValoHub library into a desktop data folder.")
    parser.add_argument("--from", dest="src", required=True, help="legacy repo root (read-only)")
    parser.add_argument("--home", dest="home", default=None, help="destination data folder (default: VALOHUB_HOME / %%LOCALAPPDATA%%\\ValoHub)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)
    dest = Path(args.home) if args.home else None
    try:
        if args.dry_run:
            report = plan_import(args.src, dest)
        else:
            last = {"rel": None}

            def show(done, total, rel):
                if not args.json and rel != last["rel"]:
                    last["rel"] = rel
                    print(f"[{done * 100 // max(total, 1):3d}%] {rel}", file=sys.stderr)

            report = import_legacy(args.src, dest, progress=show)
    except MigrationRefused as exc:
        print(json.dumps({"error": str(exc)}) if args.json else f"refused: {exc}")
        return EXIT_REFUSED
    if args.json:
        print(json.dumps(report, indent=2))
    elif args.dry_run:
        print(f"dry-run: {report['files']} files, {report['bytes']} bytes, stores={report['stores']}, "
              f"vrf={report['vrf']}, to_copy={report['to_copy']}, skipped={report['skipped']}, "
              f"conflicts={len(report['conflicts'])}")
    else:
        print(f"copied {len(report['copied'])} ({report['bytes']} bytes), skipped {len(report['skipped'])}, "
              f"conflicts {len(report['conflicts'])}")
    return EXIT_CONFLICT if report["conflicts"] else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())

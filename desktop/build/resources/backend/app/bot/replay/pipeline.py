"""Replay import pipeline: .vrf → CliReader decode → pure-Python normalizer.

Steps (BUILD_CONTRACT.md §2/§5) run as subprocesses so the web worker never
blocks: ``dotnet CliReader.dll export <vrf> --output <raw> --profile viewer``
followed by ``py -3.12 bot/replay/normalize.py <raw> <viewer>``.

The container peek is a stdlib read of the VRF container/index (same format the
retired extractor read) used to prefill the manifest: branch, map path, duration.
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import os
import re
import struct
import sys
import time
from collections import deque
from pathlib import Path

from . import paths as _paths
from . import store

HEARTBEAT_INTERVAL_S = 15
DECODE_TIMEOUT_S = 1800
NORMALIZE_TIMEOUT_S = 1200
RAW_OUTPUT_CHECK = "manifest.json"

CONTAINER_MAGIC = 0x43F4EFDD
HEADER_MAGIC = 0x2CF5A13D

DOTNET_PATH = store.ROOT / "dotnet10-sdk" / "dotnet.exe"
CLI_BIN_DIR = store.ROOT / "vendorValorantReplayParser" / "src" / "CliReader" / "bin" / "Release"
TRANSFORMS_DIR = (
    store.ROOT
    / "vendorValorantReplayParser"
    / "src"
    / "Replay.Encoding"
    / "PayloadEncryption"
    / "VersionedTransforms"
)
NORMALIZER_PATH = store.ROOT / "bot" / "replay" / "normalize.py"


class PipelineError(Exception):
    """Any failure that should surface as a job error event."""


# ---------------------------------------------------------------- container

class Reader:
    """Little-endian reader for the Unreal replay container/header records."""

    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def take(self, size: int) -> bytes:
        if size < 0 or size > len(self.data) - self.pos:
            raise ValueError(f"truncated record at byte {self.pos}")
        result = self.data[self.pos:self.pos + size]
        self.pos += size
        return result

    def number(self, fmt: str = "I") -> int:
        return struct.unpack("<" + fmt, self.take(struct.calcsize("<" + fmt)))[0]

    def string(self) -> str:
        length = self.number("i")
        if abs(length) > 1048576:
            raise ValueError("implausible FString length")
        if not length:
            return ""
        value = self.take(abs(length) * (2 if length < 0 else 1))
        result = value.decode("utf-16-le" if length < 0 else "utf-8")
        if not result.endswith("\0"):
            raise ValueError("FString is not null terminated")
        return result[:-1]

    def versions(self) -> list[dict]:
        count = self.number("i")
        if not 0 <= count <= 1024:
            raise ValueError("invalid custom version count")
        return [{"guid_bytes_hex": self.take(16).hex(), "version": self.number("i")}
                for _ in range(count)]


def _parse_network_header(payload: bytes) -> tuple[str | None, str | None]:
    """Return (branch, first-level map path) from a replay header chunk."""
    r = Reader(payload)
    if r.number() != HEADER_MAGIC:
        raise ValueError("invalid network header magic")
    r.number()                        # network version
    r.versions()
    r.number()                        # checksum
    r.number()                        # engine network protocol
    r.number()                        # game network protocol
    r.take(16)                        # guid
    for _ in range(3):
        r.number("H")                 # engine version
    r.number()                        # changelist
    branch = r.string()
    match = re.fullmatch(r"\+\+Ares-Core\+release-(\d+)\.(\d+)", branch)
    if not match:
        return branch, None
    patch = (int(match.group(1)), int(match.group(2)))
    if patch >= (12, 10):
        r.take(r.number())            # version extension block
    elif patch != (11, 7):
        return branch, None           # header layout unknown for this patch
    for _ in range(3):
        r.number()                    # package versions
    count = r.number()
    if count > 1024:
        raise ValueError("invalid level count")
    levels = [{"path": r.string(), "time_ms": r.number()} for _ in range(count)]
    return branch, (levels[0]["path"] if levels else None)


def read_container_prefix(r: Reader) -> dict:
    """Parse the VRF container prefix from ``r``; leaves ``r.pos`` at the chunk table.

    Shared by ``peek_container`` and ``bot.replay.container_index`` so the container
    layout lives in one place. Raises ValueError when this is not a VRF container
    (bad magic / version). Stdlib only.
    """
    if r.number() != CONTAINER_MAGIC:
        raise ValueError("Not a VRF/Unreal local replay (bad container magic)")
    version = r.number()
    if version != 7:
        raise ValueError(f"unsupported container version {version}; expected 7")
    r.versions()
    duration_ms = r.number()
    r.number()                    # network version
    r.number()                    # changelist
    r.string()                    # friendly name
    r.number()                    # is_live
    r.number("q")                 # timestamp ticks
    r.number()                    # compressed
    r.number()                    # encrypted
    key_length = r.number()
    if key_length > 4096:
        raise ValueError("invalid encryption key length")
    r.take(key_length)
    return {"containerVersion": version, "durationMs": duration_ms}


def peek_container(path: Path) -> dict:
    """Read the container header + first replay header chunk (stdlib only).

    Returns ``{"containerVersion", "durationMs", "branch", "mapPath"}``.
    Raises ValueError for anything that is not a VRF container.
    """
    with Path(path).open("rb") as stream:
        prefix = stream.read(1 << 20)
        r = Reader(prefix)
        info = read_container_prefix(r)
        stream.seek(r.pos)

        branch = map_path = None
        for _ in range(64):
            head = stream.read(8)
            if len(head) < 8:
                break
            kind, size = struct.unpack("<Ii", head)
            if size < 0 or size > (1 << 26):
                raise ValueError("corrupt chunk table")
            if kind == 0:
                payload = stream.read(size)
                if len(payload) != size:
                    raise ValueError("truncated replay header chunk")
                branch, map_path = _parse_network_header(payload)
                break
            stream.seek(size, os.SEEK_CUR)
        return {
            **info,
            "branch": branch,
            "mapPath": map_path,
        }


def _resolve_source(source: str) -> Path:
    fn = getattr(store, "resolve_source", None)
    return fn(source) if fn else store.ROOT / source


def _decoder_location():
    try:
        return _paths.resolve_decoder()
    except Exception:  # noqa: BLE001 - discovery must never break the pipeline
        return None


def decoder_not_built_message() -> str:
    return ("decoder not built: searched env VALO_DOTNET/VALO_DECODER_DIR; "
            "bundle <app>/runtime/dotnet/dotnet.exe + <app>/decoder/net10.0/CliReader.dll; "
            f"legacy {DOTNET_PATH} + {CLI_BIN_DIR}. "
            "Fix: reinstall the app (the decoder ships beside the exe), set VALOHUB_BUNDLE, "
            "or in a source checkout run the parser build (bot.replay.tools.update_parser).")


def _registry_branches() -> set:
    loc = _decoder_location()
    reg = getattr(loc, "registry_json", None)
    if reg is None:
        return set()
    try:
        data = json.loads(Path(reg).read_text(encoding="utf-8"))
        return {str(b) for b in data if isinstance(b, str)}
    except (OSError, ValueError, TypeError):
        return set()


# ---------------------------------------------------------------- decoder

def find_decoder() -> tuple[Path | None, Path | None]:
    """Locate (dotnet, CliReader.dll); either may be None when not built yet."""
    dotnet = DOTNET_PATH if DOTNET_PATH.is_file() else None
    loc = _decoder_location()
    if loc is not None and loc.source in ("env", "bundle", "mixed"):
        return loc.dotnet_exe, loc.decoder_dll
    dll = None
    if CLI_BIN_DIR.is_dir():
        preferred = CLI_BIN_DIR / "net10.0" / "CliReader.dll"
        if preferred.is_file():
            dll = preferred
        else:
            for candidate in sorted(CLI_BIN_DIR.glob("net*/CliReader.dll")):
                dll = candidate
                break
    return dotnet, dll


def supported_branches() -> list[str]:
    """Branch strings the vendored transform registry can decode."""
    found: set[str] = set()
    if TRANSFORMS_DIR.is_dir():
        for path in TRANSFORMS_DIR.glob("*.cs"):
            try:
                found.update(re.findall(r'"(\+\+Ares-Core\+release-[^"]+)"',
                                        path.read_text(encoding="utf-8", errors="replace")))
            except OSError:
                continue
    if not TRANSFORMS_DIR.is_dir():
        found |= _registry_branches()
    return sorted(found)


def decoder_info() -> dict:
    """Decoder readiness plus what is actually known about branch support.

    ``supported`` (kept for older clients) and ``registeredBranches`` list branch strings found in
    the transform registry — registration is NOT evidence that a replay of that branch decodes.
    ``replayTested`` is the matrix of real replays exercised against this build (from the build
    record) and ``replayValidatedBranches`` the branches whose tested replay passed.
    """
    dotnet, dll = find_decoder()
    registered = supported_branches()
    recorded = store.recorded_build()
    tested = recorded["tested"]
    validated = sorted({t["branch"] for t in tested if t["result"] == "validated" and t["branch"]})
    covered = {t["branch"] for t in tested}
    state = store.build_state()
    return {
        "parserCommit": store.parser_commit(),
        "status": "ready" if (dotnet and dll) else "unavailable",
        "decoderSource": getattr(_decoder_location(), "source", "legacy"),
        "supported": registered,
        "registeredBranches": registered,
        "replayTested": tested,
        "replayValidatedBranches": validated,
        "registeredWithoutReplayTest": [b for b in registered if b not in covered],
        "buildState": state["state"],
        "buildChangedAssemblies": state["changed"],
    }


# ---------------------------------------------------------------- staging

def staging_dir() -> Path:
    path = store.TMP_DIR / "incoming"
    path.mkdir(parents=True, exist_ok=True)
    return path


def already_imported(replay_id: str) -> str | None:
    """Reason string when this replay already has viewer data / terminal state."""
    rdir = store.replay_dir(replay_id)
    if not rdir.is_dir():
        return None
    if (rdir / "viewer" / "match.json").is_file():
        return "normalized viewer data already present"
    manifest = store.read_manifest(replay_id) or {}
    status = manifest.get("status")
    if status in ("ready", "timeline"):
        return f"manifest status is '{status}'"
    return None


def place_original(part: Path, name: str, sha16: str) -> Path:
    """Move a staged file into replays/ under ``name``; never clobber different bytes."""
    target = store.ORIGINALS_DIR / name
    if target.exists():
        digest, _ = store.sha256_file(target)
        if digest[:16] == sha16:
            Path(part).unlink(missing_ok=True)
            return target
        alt = store.ORIGINALS_DIR / f"{Path(name).stem}_{sha16[:8]}{Path(name).suffix}"
        if alt.exists():
            digest, _ = store.sha256_file(alt)
            if digest[:16] == sha16:
                Path(part).unlink(missing_ok=True)
                return alt
        alt = store.ORIGINALS_DIR / f"{Path(name).stem}_{sha16[:8]}_{int(time.time())}{Path(name).suffix}"
        target = alt
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(part, target)
    return target


def copy_and_hash(src: Path, dst: Path, progress=None, chunk: int = 1 << 22,
                  step: int = 8 << 20) -> tuple[str, int]:
    """Single-pass copy + sha256. ``progress(done_bytes, total_bytes)`` is throttled."""
    total = None
    try:
        total = Path(src).stat().st_size
    except OSError:
        pass
    digest = hashlib.sha256()
    done = 0
    last = 0
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    with Path(src).open("rb") as fsrc, dst.open("wb") as fdst:
        while True:
            block = fsrc.read(chunk)
            if not block:
                break
            fdst.write(block)
            digest.update(block)
            done += len(block)
            if progress and done - last >= step:
                last = done
                try:
                    progress(done, total)
                except Exception:  # noqa: BLE001 — progress must never break the copy
                    pass
    if progress:
        try:
            progress(done, total)
        except Exception:  # noqa: BLE001
            pass
    return digest.hexdigest(), done


# ---------------------------------------------------------------- pipeline

def _excerpt(lines, limit: int = 400) -> str:
    """Compact process output, keeping the first exception headline (root cause).

    Plain tail-truncation loses the `InvalidReplayHeaderException: ...` line
    when a stack trace follows it (reviewer t_6fcfe716, change 3a).
    """
    cleaned = [line.strip() for line in lines if line.strip()]
    text = " | ".join(cleaned)
    if not text:
        return ""
    tail = text[-limit:]
    headline = next((line for line in cleaned if "Exception" in line), cleaned[0])
    if headline and headline not in tail:
        return f"{headline[:300]} … {tail}"
    return tail


def _block(obj, key: str) -> dict:
    value = obj.get(key) if isinstance(obj, dict) else None
    return value if isinstance(value, dict) else {}


def _dir_size(path: Path) -> int:
    total = 0
    try:
        for item in path.rglob("*"):
            if item.is_file():
                total += item.stat().st_size
    except OSError:
        pass
    return total


def _work_dir() -> Path:
    work = getattr(store, "WORK_DIR", None)
    return Path(work) if work else _paths.get_paths().work


def _process_env_and_flags() -> tuple[dict, int]:
    env = dict(os.environ)
    dotnet, _dll = find_decoder()
    loc = _decoder_location()
    bundled = loc is not None and loc.source in ("env", "bundle", "mixed")
    if dotnet is not None:
        env["DOTNET_ROOT"] = str(Path(dotnet).parent)
    env["DOTNET_MULTILEVEL_LOOKUP"] = "0"
    if bundled:
        home = _work_dir() / "dotnet-home"
        env.setdefault("DOTNET_CLI_HOME", str(home / "cli"))
        env.setdefault("NUGET_PACKAGES", str(home / "packages"))
    else:
        env.setdefault("DOTNET_CLI_HOME", str(store.ROOT / "dotnet10-sdk" / "cli-home"))
        env.setdefault("NUGET_PACKAGES", str(store.ROOT / "dotnet10-sdk" / "packages"))
    env["DOTNET_CLI_TELEMETRY_OPTOUT"] = "1"
    env["DOTNET_NOLOGO"] = "1"
    if getattr(sys, "frozen", False):
        # the normalizer child is this same frozen exe: tell the PyInstaller bootloader to start fresh
        env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
    return env, flags


async def _kill_and_reap(proc, pump_task) -> None:
    """Kill the child, wait <=5 s for exit, stop the stdout pump (V4: no orphan decoder)."""
    try:
        proc.kill()
    except ProcessLookupError:
        pass
    try:
        await asyncio.wait_for(proc.wait(), timeout=5)
    except (asyncio.TimeoutError, TimeoutError):
        pass
    pump_task.cancel()
    await asyncio.gather(pump_task, return_exceptions=True)


async def _run_process(argv, *, timeout: float, heartbeat=None) -> tuple[int, list[str]]:
    """Run a subprocess, stream its output into a tail buffer, heartbeat on interval."""
    env, flags = _process_env_and_flags()
    cwd = _work_dir()
    try:
        cwd.mkdir(parents=True, exist_ok=True)
    except OSError:
        cwd = store.ROOT
    kwargs = {"creationflags": flags} if flags else {}

    proc = await asyncio.create_subprocess_exec(
        *[str(part) for part in argv],
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=str(cwd),
        env=env,
        **kwargs,
    )
    tail: deque[str] = deque(maxlen=60)

    async def pump() -> None:
        assert proc.stdout is not None
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", "replace").rstrip()
            if text:
                tail.append(text)
        # top up the tail with whatever remains buffered
        rest = await proc.stdout.read()
        for text in rest.decode("utf-8", "replace").splitlines():
            if text.strip():
                tail.append(text.strip())

    pump_task = asyncio.create_task(pump())
    start = time.monotonic()
    try:
        while True:
            try:
                await asyncio.wait_for(asyncio.shield(proc.wait()), timeout=HEARTBEAT_INTERVAL_S)
                break
            except (asyncio.TimeoutError, TimeoutError):
                if time.monotonic() - start > timeout:
                    await _kill_and_reap(proc, pump_task)
                    raise PipelineError(f"timed out after {int(timeout)}s")
                if heartbeat is not None:
                    try:
                        heartbeat(time.monotonic() - start)
                    except Exception:  # noqa: BLE001
                        pass
    except asyncio.CancelledError:
        await _kill_and_reap(proc, pump_task)
        raise
    await asyncio.gather(pump_task, return_exceptions=True)
    return proc.returncode or 0, list(tail)


async def _decode(replay_id: str, source_path: Path, raw_dir: Path, emit) -> dict:
    dotnet, dll = find_decoder()
    if dotnet is None or dll is None:
        store.update_manifest(replay_id, status="failed",
                              notes="decoder not built (see replays/README.md)")
        raise PipelineError(decoder_not_built_message())
    raw_dir.mkdir(parents=True, exist_ok=True)
    emit("decode", 22, "decoding with CliReader (viewer profile)…")

    def heartbeat(elapsed: float) -> None:
        mb = _dir_size(raw_dir) / (1024 * 1024)
        emit("decode", 22, f"decoding… {int(elapsed)}s elapsed · {mb:.1f} MB raw output")

    rc, tail = await _run_process(
        [dotnet, dll, "export", source_path, "--output", raw_dir, "--profile", "viewer"],
        timeout=DECODE_TIMEOUT_S,
        heartbeat=heartbeat,
    )
    if rc != 0 or not (raw_dir / RAW_OUTPUT_CHECK).is_file():
        # No timeline artifact is written on this path, so a failed decode is
        # 'failed' (never 'timeline'); clean up zero-byte partial output too.
        for leftover in raw_dir.glob("*.ndjson*"):
            try:
                if leftover.is_file() and leftover.stat().st_size == 0:
                    leftover.unlink()
            except OSError:
                pass
        status = "failed"
        notes = _excerpt(tail) or f"decoder exit code {rc}"
        store.update_manifest(replay_id, status=status, notes=notes)
        raise PipelineError(f"decode failed (replay marked '{status}'): {notes}")
    return {"exitCode": rc, "tail": tail}


async def _decode_quality(raw_dir: Path, branch: str | None) -> dict:
    """P5-B: density check after decode, before publishing a ready manifest."""
    from .tools.update_parser import assess_decode_density
    try:
        return await asyncio.to_thread(assess_decode_density, raw_dir, branch=branch)
    except (OSError, ValueError, RuntimeError) as exc:
        return {"state": "suspect", "suspect": True, "reason": f"density check failed: {exc}"}


def quality_manifest_fields(quality: dict) -> dict:
    """Manifest fields for one quality verdict.

    ``status`` stays ``ready``/``suspect`` (existing consumers); the three-way ``quality`` block
    carries ``validated`` / ``suspect`` / ``unvalidated`` with its reason and basis so a missing
    or uncovered baseline is never recorded as a passed check. ``densityReason`` remains the
    suspect-only legacy field.
    """
    state = quality.get("state")
    if state not in ("validated", "suspect", "unvalidated"):
        state = "suspect" if quality.get("suspect") else "unvalidated"
    reason = quality.get("reason")
    if state == "unvalidated" and not reason:
        reason = "decode quality was not checked"
    block = {"state": state, "reason": reason if state != "validated" else None,
             "basis": quality.get("basis"), "checkedAt": store.iso_now()}
    return {
        "status": "suspect" if state == "suspect" else "ready",
        "densityReason": reason if state == "suspect" else None,
        "notes": reason if state == "suspect" else "",
        "quality": block,
    }


def _quality_note(block: dict) -> str:
    if block["state"] == "validated":
        return " · quality validated"
    return f" · quality {block['state']}: {block.get('reason')}"


def normalizer_command(raw_dir, viewer_dir) -> list:
    """Frozen: re-invoke the sidecar exe with the ``normalize`` subcommand; else run normalize.py."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "normalize", raw_dir, viewer_dir]
    return [sys.executable, NORMALIZER_PATH, raw_dir, viewer_dir]


async def _normalize(replay_id: str, raw_dir: Path, viewer_dir: Path, emit) -> None:
    if not getattr(sys, "frozen", False) and not NORMALIZER_PATH.is_file():
        store.update_manifest(replay_id, status="failed",
                              notes="normalizer missing (bot/replay/normalize.py)")
        raise PipelineError("normalizer missing: bot/replay/normalize.py")
    viewer_dir.mkdir(parents=True, exist_ok=True)
    emit("normalize", 82, "normalizing viewer data…")
    rc, tail = await _run_process(
        normalizer_command(raw_dir, viewer_dir),
        timeout=NORMALIZE_TIMEOUT_S,
    )
    if rc != 0:
        notes = _excerpt(tail) or f"normalizer exit code {rc}"
        store.update_manifest(replay_id, status="failed", notes=notes)
        raise PipelineError(f"normalize failed: {notes}")


async def _gzip_raw(replay_id: str, raw_dir: Path, emit) -> dict:
    """Compress decode NDJSON in place — raw retention is gzip, not delete.

    Streams each ``*.ndjson`` to ``*.ndjson.gz``, then verifies the archive by
    decompressing it fully and comparing sha256 + byte counts against the
    source before the original is unlinked. The viewer never reads raw/; a
    full re-decode (from ``source.vrf``) regenerates it when needed.
    """
    sources = sorted(raw_dir.glob("*.ndjson"))
    if not sources:
        return {}
    emit("compress", 90, f"compressing raw NDJSON ({len(sources)} file(s))…")
    report: list[dict] = []

    def _compress_one(src: Path) -> dict:
        gz_path = src.with_name(src.name + ".gz")
        tmp_path = gz_path.with_name(gz_path.name + ".tmp")
        digest = hashlib.sha256()
        bytes_in = 0
        with src.open("rb") as fsrc, gzip.open(tmp_path, "wb", compresslevel=6) as fdst:
            while True:
                block = fsrc.read(1 << 22)
                if not block:
                    break
                digest.update(block)
                fdst.write(block)
                bytes_in += len(block)
        check = hashlib.sha256()
        bytes_verified = 0
        with gzip.open(tmp_path, "rb") as fchk:
            while True:
                block = fchk.read(1 << 22)
                if not block:
                    break
                check.update(block)
                bytes_verified += len(block)
        if check.hexdigest() != digest.hexdigest() or bytes_verified != bytes_in:
            tmp_path.unlink(missing_ok=True)
            raise PipelineError(f"gzip verify failed for {src.name}")
        os.replace(tmp_path, gz_path)
        gz_bytes = gz_path.stat().st_size
        src.unlink()
        return {"file": gz_path.name, "bytesIn": bytes_in, "bytesOut": gz_bytes}

    for src in sources:
        report.append(await asyncio.to_thread(_compress_one, src))
        done_mb = report[-1]["bytesIn"] / (1024 * 1024)
        emit("compress", 90, f"compressed {report[-1]['file']} ({done_mb:.1f} MB in)")
    return {"files": report, "completedAt": store.iso_now()}


async def import_replay(job, emit, *, replay_id: str, source_path) -> None:
    """Queue runner: decode + normalize one replay, keeping manifest.json current."""
    source_path = Path(source_path)
    if not source_path.is_file():
        raise PipelineError(f"source file is missing: {source_path}")

    rdir = store.replay_dir(replay_id)
    rdir.mkdir(parents=True, exist_ok=True)
    store.update_manifest(
        replay_id,
        schema=store.STORE_SCHEMA,
        id=replay_id,
        source=store.rel_source(source_path),
        exportProfile="viewer",
        parserCommit=store.parser_commit(),
        parser=store.parser_fingerprint(),
        status="processing",
        notes="",
        densityReason=None,
        quality=None,
        decodedAt=None,
    )

    emit("container", 12, "reading container header")
    try:
        info = await asyncio.to_thread(peek_container, source_path)
    except (ValueError, OSError, struct.error) as exc:
        store.update_manifest(replay_id, status="failed", notes=f"container: {exc}")
        raise PipelineError(f"container check failed: {exc}") from exc

    leaf = (info.get("mapPath") or "").rsplit("/", 1)[-1]
    previous = store.read_manifest(replay_id) or {}
    prev_map = _block(previous, "map")
    store.update_manifest(
        replay_id,
        branch=info.get("branch"),
        map={
            "name": store.MAP_NAMES.get(leaf) or prev_map.get("name"),
            "path": info.get("mapPath") or prev_map.get("path"),
        },
    )
    seconds = f"{info.get('durationMs', 0) // 1000}s" if info.get("durationMs") else "duration unknown"
    emit("container", 20, f"container ok · branch {info.get('branch') or 'unknown'} · {seconds}")

    raw_dir = rdir / "raw"
    await _decode(replay_id, source_path, raw_dir, emit)
    quality = await _decode_quality(raw_dir, info.get("branch"))
    quality_fields = quality_manifest_fields(quality)
    suspect_reason = quality_fields["densityReason"]
    store.update_manifest(replay_id, decodedAt=store.iso_now(), **quality_fields)
    emit("decode", 78, "decode complete" + _quality_note(quality_fields["quality"]))

    viewer_dir = rdir / "viewer"
    await _normalize(replay_id, raw_dir, viewer_dir, emit)

    try:
        gzip_info = await _gzip_raw(replay_id, raw_dir, emit)
    except PipelineError as exc:  # compression must never fail the import
        emit("compress", 90, f"raw compression skipped: {exc}")
        gzip_info = {}

    match = store.load_match(replay_id) or {}
    decode_block = _block(match, "decode")
    status = decode_block.get("status")
    if status not in ("ready", "timeline"):
        status = "ready"
    if suspect_reason:
        status = "suspect"
    new_map = _block(match, "map")
    manifest = store.read_manifest(replay_id) or {}
    man_map = _block(manifest, "map")
    extra = {"rawGzip": gzip_info} if gzip_info else {}
    viewer_schema = match.get("schema") if isinstance(match.get("schema"), str) else store.VIEWER_SCHEMA
    store.update_manifest(
        replay_id,
        schema=store.STORE_SCHEMA,
        viewerSchema=viewer_schema,
        status=status,
        notes=suspect_reason or "",
        densityReason=suspect_reason,
        map={
            "name": new_map.get("name") or man_map.get("name"),
            "path": new_map.get("path") or man_map.get("path"),
        },
        **extra,
    )
    emit("done", 100, f"import complete · status {status}")


# ---------------------------------------------------------------- reprocess

def _fingerprint_text(fingerprint: dict) -> str:
    commit = fingerprint.get("commit") or "none"
    overlay = fingerprint.get("overlay") or "none"
    return f"commit {commit[:12]} / overlay {overlay[:12]}"


def reprocess_plan(replay_id) -> dict:
    """What a reprocess of this store would do, without doing it.

    ``decode`` re-runs the CliReader export (the parser fingerprint changed or
    no raw NDJSON exists); ``normalize`` re-runs the normalizer over the stored
    ``raw/*.ndjson.gz``; ``unavailable`` means neither is possible.
    """
    rdir = store.replay_dir(replay_id)
    if not rdir.is_dir():
        raise PipelineError(f"replay {replay_id!r} is not in the store")
    manifest = store.read_manifest(replay_id) or {}
    if manifest.get("source") == "synthetic":
        return {"mode": "unavailable", "reasons": ["synthetic fixture (no decoder source)"],
                "fingerprintChanged": False, "rawAvailable": False, "sourceAvailable": False}

    raw_dir = rdir / "raw"
    raw_available = raw_dir.is_dir() and any(raw_dir.glob("*.ndjson*"))
    recorded = store.recorded_decode_fingerprint(manifest)
    current = store.parser_fingerprint()
    changed = store.fingerprints_differ(recorded, current)
    reasons: list[str] = []
    if changed:
        reasons.append("parser fingerprint changed "
                       f"({_fingerprint_text(recorded)} -> {_fingerprint_text(current)})")

    source = manifest.get("source")
    source_path = _resolve_source(source) if isinstance(source, str) else None
    source_available = bool(source_path and source_path.is_file())

    decode = changed
    if not raw_available:
        reasons.append("no raw NDJSON in the store")
        decode = True  # nothing to re-normalize from — the decode must run
    if decode and not source_available:
        if raw_available:
            reasons.append(f"original replay missing ({source}) — re-normalizing from stored raw only")
            decode = False
        else:
            reasons.append("no raw NDJSON and no original replay — a full import is required")
            return {"mode": "unavailable", "reasons": reasons, "fingerprintChanged": changed,
                    "rawAvailable": False, "sourceAvailable": False}

    return {
        "mode": "decode" if decode else "normalize",
        "reasons": reasons,
        "fingerprintChanged": changed,
        "rawAvailable": raw_available,
        "sourceAvailable": source_available,
    }


async def reprocess_replay(job, emit, *, replay_id: str) -> None:
    """Reprocess one stored replay through the real pipeline.

    Re-decodes from the original ``.vrf`` only when the parser fingerprint
    changed (or no raw exists — then a decode is required anyway); otherwise
    re-normalizes straight from ``raw/*.ndjson.gz``. After a re-decode the raw
    NDJSON is re-gzipped with the same verified sha256 round-trip as imports.
    """
    plan = reprocess_plan(replay_id)
    if plan["mode"] == "unavailable":
        raise PipelineError("cannot reprocess: " + "; ".join(plan["reasons"]))
    detail = "; ".join(plan["reasons"]) or "store matches the current parser (re-normalizing only)"
    emit("container", 5, f"reprocess · {plan['mode']} · {detail}")

    rdir = store.replay_dir(replay_id)
    manifest = store.read_manifest(replay_id) or {}
    raw_dir = rdir / "raw"
    viewer_dir = rdir / "viewer"
    quality = None
    # This attempt replaces the store's contents, so the previous attempt's verdict must stop
    # being current before any decode/normalize work starts (both modes).
    store.update_manifest(replay_id, status="processing", notes="", densityReason=None, quality=None)

    if plan["mode"] == "decode":
        source_path = _resolve_source(str(manifest.get("source")))
        await _decode(replay_id, source_path, raw_dir, emit)
        quality = await _decode_quality(raw_dir, manifest.get("branch"))
        store.update_manifest(replay_id, decodedAt=store.iso_now(),
                              parser=store.parser_fingerprint(),
                              **quality_manifest_fields(quality))
        emit("decode", 80, "re-decode complete")

    if quality is None:
        quality = await _decode_quality(raw_dir, manifest.get("branch"))
    quality_fields = quality_manifest_fields(quality)
    suspect_reason = quality_fields["densityReason"]
    await _normalize(replay_id, raw_dir, viewer_dir, emit)

    gzip_info: dict = {}
    if plan["mode"] == "decode":
        try:
            gzip_info = await _gzip_raw(replay_id, raw_dir, emit)
        except PipelineError as exc:  # compression must never fail the reprocess
            emit("compress", 90, f"raw compression skipped: {exc}")

    match = store.load_match(replay_id) or {}
    decode_block = _block(match, "decode")
    status = decode_block.get("status")
    if status not in ("ready", "timeline"):
        status = "ready"
    if suspect_reason:
        status = "suspect"
    new_map = _block(match, "map")
    man_map = _block(manifest, "map")
    extra = {"rawGzip": gzip_info} if gzip_info else {}
    viewer_schema = match.get("schema") if isinstance(match.get("schema"), str) else store.VIEWER_SCHEMA
    store.update_manifest(
        replay_id,
        schema=store.STORE_SCHEMA,
        viewerSchema=viewer_schema,
        status=status,
        notes=suspect_reason or "",
        densityReason=suspect_reason,
        quality=quality_fields["quality"],
        parser=store.parser_fingerprint(),
        map={
            "name": new_map.get("name") or man_map.get("name"),
            "path": new_map.get("path") or man_map.get("path"),
        },
        **extra,
    )
    emit("done", 100, f"reprocess complete · status {status}")


# ---------------------------------------------------------------- restart sweep

INTERRUPTED_NOTE = ("interrupted: the app was closed during the import; "
                    "import the replay again")


def sweep_interrupted(log=None) -> dict:
    """Backend start-up cleanup: no job can be running yet, so delete staging ``*.part`` files
    and mark every library entry still ``processing`` as ``failed``. Never raises."""
    say = log or (lambda msg: None)
    result = {"parts": [], "failed": []}
    try:
        incoming = store.TMP_DIR / "incoming"
        if incoming.is_dir():
            for part in sorted(incoming.glob("*.part")):
                try:
                    part.unlink()
                    result["parts"].append(part.name)
                except OSError as exc:
                    say(f"sweep: cannot delete {part.name}: {exc}")
        root = store.DATA_REPLAYS_DIR
        if root.is_dir():
            for rdir in sorted(root.iterdir()):
                if not rdir.is_dir() or not store.valid_id(rdir.name):
                    continue
                manifest = store.read_json(rdir / "manifest.json") or {}
                if manifest.get("status") != "processing":
                    continue
                try:
                    store.update_manifest(rdir.name, status="failed", notes=INTERRUPTED_NOTE)
                    result["failed"].append(rdir.name)
                except Exception as exc:  # noqa: BLE001
                    say(f"sweep: cannot mark {rdir.name} failed: {exc}")
    except Exception as exc:  # noqa: BLE001
        say(f"sweep failed: {exc}")
    if result["parts"] or result["failed"]:
        say(f"sweep: removed {len(result['parts'])} partial upload(s) {result['parts']}, "
            f"marked {len(result['failed'])} interrupted import(s) failed {result['failed']}")
    return result

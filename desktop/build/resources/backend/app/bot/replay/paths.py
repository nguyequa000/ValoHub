"""Runtime path contract for the ValoHub desktop migration (contract v1).

One layout, two roots.  Everything the app WRITES lives under ``data_root``; everything it only
READS (static UI, normalizer data files) lives under ``code_root``.  Relative layout under
``data_root`` is identical in both modes, so a legacy checkout can be copied into a desktop
data folder verbatim and manifest ``source`` strings (``replays/<name>.vrf``) keep resolving::

    data/replays/<id>/...     normalized library              -> AppPaths.data_replays
    replays/*.vrf             copied originals                -> AppPaths.originals
    VALOPLANT_WORK/           scratch, staging (tmp/), records -> AppPaths.work / .tmp
    cache/                    small caches                    -> AppPaths.cache
    logs/                     rotating app log (desktop)      -> AppPaths.logs
    config.json               desktop settings                -> AppPaths.config_file

Modes
-----
``legacy``   nothing set and not frozen.  ``data_root == code_root == repo root`` - exactly the
             behaviour before the desktop migration (dev workflow, run_bot.bat, the test-suite).
``desktop``  ``VALOHUB_HOME`` set, or ``VALOHUB_DESKTOP=1``, or the process is frozen
             (PyInstaller).  ``data_root`` = ``$VALOHUB_HOME`` or ``%LOCALAPPDATA%\\ValoHub``.

Decoder discovery (:func:`resolve_decoder`) is looked up on every call (never cached) in this order,
independently for the .NET host and for ``CliReader.dll``:
    1. env ``VALO_DOTNET`` (path to dotnet.exe) / ``VALO_DECODER_DIR`` (folder holding CliReader.dll)
    2. bundle  ``<bundle_root>/runtime/dotnet/dotnet.exe`` and ``<bundle_root>/decoder/net10.0/CliReader.dll``
       (``bundle_root`` = folder of the frozen exe, or env ``VALOHUB_BUNDLE`` for a dev/QA override)
    3. legacy  ``<code_root>/dotnet10-sdk/dotnet.exe`` and
       ``<code_root>/vendorValorantReplayParser/src/CliReader/bin/Release/net10.0/CliReader.dll``

Nothing here touches the disk at import time; call :func:`ensure_data_dirs` explicitly (the desktop
entry point does).  Stdlib only.
"""

from __future__ import annotations

import os
import sys
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Mapping

ENV_HOME = "VALOHUB_HOME"
ENV_DESKTOP = "VALOHUB_DESKTOP"
ENV_BUNDLE = "VALOHUB_BUNDLE"
ENV_DOTNET = "VALO_DOTNET"
ENV_DECODER_DIR = "VALO_DECODER_DIR"
APP_DIRNAME = "ValoHub"

MODE_LEGACY = "legacy"
MODE_DESKTOP = "desktop"


@dataclass(frozen=True)
class AppPaths:
    """Resolved roots.  Plain data; safe to share across threads."""

    mode: str                    # "legacy" | "desktop"
    code_root: Path              # read-only resources (repo root, or <bundle>/_internal when frozen)
    data_root: Path              # writable user data
    data_replays: Path           # data_root/data/replays
    originals: Path              # data_root/replays
    work: Path                   # data_root/VALOPLANT_WORK
    tmp: Path                    # work/tmp
    cache: Path                  # data_root/cache
    logs: Path                   # desktop: data_root/logs   legacy: work/logs
    config_file: Path            # desktop: data_root/config.json   legacy: work/config.json
    bundle_root: Path | None     # folder holding runtime/ and decoder/ (frozen exe folder), else None

    def to_dict(self) -> dict:
        return {key: (str(value) if isinstance(value, Path) else value) for key, value in asdict(self).items()}


@dataclass(frozen=True)
class DecoderLocation:
    """Where the decoder pieces were found (any may be ``None`` when missing)."""

    source: str                  # "env" | "bundle" | "legacy" | "missing" | "mixed"
    dotnet_exe: Path | None
    decoder_dll: Path | None
    decoder_dir: Path | None     # folder containing CliReader.dll
    record: Path                 # parser-commit.txt build record (the file may not exist)
    registry_json: Path | None   # bundled registered-branches.json (None outside a bundle)

    @property
    def ready(self) -> bool:
        return self.dotnet_exe is not None and self.decoder_dll is not None

    def to_dict(self) -> dict:
        return {key: (str(value) if isinstance(value, Path) else value) for key, value in asdict(self).items()} | {
            "ready": self.ready}


# ------------------------------------------------------------------ roots

def _env(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if env is None else env


def _code_root() -> Path:
    return Path(__file__).resolve().parents[2]


def detect_mode(env: Mapping[str, str] | None = None) -> str:
    env = _env(env)
    if (env.get(ENV_HOME) or "").strip() or env.get(ENV_DESKTOP) == "1" or getattr(sys, "frozen", False):
        return MODE_DESKTOP
    return MODE_LEGACY


def default_data_root(env: Mapping[str, str] | None = None) -> Path:
    """``$VALOHUB_HOME`` else ``%LOCALAPPDATA%\\ValoHub`` (``~/AppData/Local`` fallback)."""
    env = _env(env)
    home = (env.get(ENV_HOME) or "").strip()
    if home:
        return Path(os.path.abspath(os.path.expanduser(home)))
    local = (env.get("LOCALAPPDATA") or "").strip() or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    return Path(os.path.abspath(os.path.join(local, APP_DIRNAME)))


def _bundle_root(env: Mapping[str, str]) -> Path | None:
    override = (env.get(ENV_BUNDLE) or "").strip()
    if override:
        return Path(os.path.abspath(override))
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        # Electron layout: resources/backend/<exe> with resources/decoder + resources/runtime beside backend/
        if not (exe_dir / "decoder").exists() and (exe_dir.parent / "decoder").exists():
            return exe_dir.parent
        return exe_dir
    return None


def compute_paths(env: Mapping[str, str] | None = None) -> AppPaths:
    """Pure function of ``env`` (default ``os.environ``) - no caching, no disk access."""
    env = _env(env)
    mode = detect_mode(env)
    code_root = _code_root()
    data_root = default_data_root(env) if mode == MODE_DESKTOP else code_root
    work = data_root / "VALOPLANT_WORK"
    desktop = mode == MODE_DESKTOP
    return AppPaths(
        mode=mode,
        code_root=code_root,
        data_root=data_root,
        data_replays=data_root / "data" / "replays",
        originals=data_root / "replays",
        work=work,
        tmp=work / "tmp",
        cache=data_root / "cache",
        logs=(data_root / "logs") if desktop else (work / "logs"),
        config_file=(data_root / "config.json") if desktop else (work / "config.json"),
        bundle_root=_bundle_root(env),
    )


_CACHED: AppPaths | None = None


def get_paths(refresh: bool = False) -> AppPaths:
    """Process-wide paths (computed once from ``os.environ``; ``refresh=True`` recomputes)."""
    global _CACHED
    if _CACHED is None or refresh:
        _CACHED = compute_paths(None)
    return _CACHED


def ensure_data_dirs(paths: AppPaths | None = None) -> AppPaths:
    """Create the writable folders (idempotent).  Raises ``OSError`` with the offending path."""
    paths = paths or get_paths()
    for folder in (paths.data_root, paths.data_replays, paths.originals, paths.work, paths.tmp, paths.cache,
                   paths.logs):
        folder.mkdir(parents=True, exist_ok=True)
    return paths


def check_writable(folder: Path) -> str | None:
    """``None`` when ``folder`` accepts a temp file, else a one-line reason for the user."""
    probe = folder / f".write-probe-{uuid.uuid4().hex[:8]}"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe.write_bytes(b"ok")
    except OSError as exc:
        return f"{folder} is not writable: {exc}"
    finally:
        try:
            probe.unlink()
        except OSError:
            pass
    return None


# ------------------------------------------------------------------ manifest source

def resolve_source(source: str | os.PathLike, paths: AppPaths | None = None) -> Path:
    """Resolve a manifest ``source`` (normally ``replays/<name>.vrf``, relative) to a real path.

    Absolute paths are returned unchanged.  Relative ones are looked up under ``data_root`` first, then
    ``code_root`` (a desktop install still opening a legacy-recorded source), returning the first that
    exists, else the ``data_root`` candidate (so the caller's "missing file" error names the right place).
    """
    paths = paths or get_paths()
    raw = Path(source)
    if raw.is_absolute():
        return raw
    candidates = [paths.data_root / raw]
    if paths.code_root != paths.data_root:
        candidates.append(paths.code_root / raw)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


# ------------------------------------------------------------------ decoder discovery

def _first_file(*candidates: Path | None) -> Path | None:
    for candidate in candidates:
        if candidate is not None and candidate.is_file():
            return candidate
    return None


def _decoder_dll_in(folder: Path | None) -> Path | None:
    """``folder/CliReader.dll`` or, for a build output root, ``folder/net*/CliReader.dll`` (``net10.0`` first)."""
    if folder is None or not folder.is_dir():
        return None
    direct = folder / "CliReader.dll"
    if direct.is_file():
        return direct
    preferred = folder / "net10.0" / "CliReader.dll"
    if preferred.is_file():
        return preferred
    for candidate in sorted(folder.glob("net*/CliReader.dll")):
        return candidate
    return None


def resolve_decoder(paths: AppPaths | None = None, env: Mapping[str, str] | None = None) -> DecoderLocation:
    """Locate (dotnet.exe, CliReader.dll, build record).  Evaluated fresh on every call."""
    paths = paths or get_paths()
    env = _env(env)
    sources: list[str] = []

    env_dotnet = (env.get(ENV_DOTNET) or "").strip()
    env_dir = (env.get(ENV_DECODER_DIR) or "").strip()
    bundle = paths.bundle_root

    dotnet = None
    if env_dotnet and Path(env_dotnet).is_file():
        dotnet, label = Path(env_dotnet), "env"
    elif bundle and (bundle / "runtime" / "dotnet" / "dotnet.exe").is_file():
        dotnet, label = bundle / "runtime" / "dotnet" / "dotnet.exe", "bundle"
    elif (paths.code_root / "dotnet10-sdk" / "dotnet.exe").is_file():
        dotnet, label = paths.code_root / "dotnet10-sdk" / "dotnet.exe", "legacy"
    else:
        label = "missing"
    sources.append(label)

    dll = None
    if env_dir and _decoder_dll_in(Path(env_dir)) is not None:
        dll, label = _decoder_dll_in(Path(env_dir)), "env"
    elif bundle and _decoder_dll_in(bundle / "decoder") is not None:
        dll, label = _decoder_dll_in(bundle / "decoder"), "bundle"
    else:
        legacy = _decoder_dll_in(paths.code_root / "vendorValorantReplayParser" / "src" / "CliReader" / "bin" / "Release")
        if legacy is not None:
            dll, label = legacy, "legacy"
        else:
            label = "missing"
    sources.append(label)

    decoder_dir = dll.parent if dll is not None else None
    in_bundle = bundle is not None and decoder_dir is not None and bundle in decoder_dir.parents
    record = _first_file(
        (bundle / "decoder" / "parser-commit.txt") if (bundle and in_bundle) else None,
        (decoder_dir.parent / "parser-commit.txt") if (decoder_dir is not None and env_dir) else None,
    ) or (paths.work / "parser-commit.txt")
    registry = _first_file((bundle / "decoder" / "registered-branches.json") if bundle else None)

    unique = set(sources)
    source = sources[0] if len(unique) == 1 else ("missing" if unique == {"missing"} else "mixed")
    return DecoderLocation(source=source, dotnet_exe=dotnet, decoder_dll=dll, decoder_dir=decoder_dir,
                           record=record, registry_json=registry)

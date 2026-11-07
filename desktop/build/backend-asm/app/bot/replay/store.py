"""Replay data store — paths, schemas, and read helpers for the new pipeline.

Layout (BUILD_CONTRACT.md §3)::

    data/replays/<id>/manifest.json      pipeline state
    data/replays/<id>/raw/               CliReader `export --profile viewer` bundle (parser lane)
    data/replays/<id>/viewer/match.json  normalized match metadata  (normalizer lane)
    data/replays/<id>/viewer/rounds/rNN.json

Original .vrf files stay in ``replays/*.vrf`` — referenced, never copied into the
store. Only the stdlib is used here.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from bot.replay import paths as _paths

_P = _paths.get_paths()
ROOT = _P.code_root                                 # code/resources root (repo root in legacy mode)
DATA_ROOT = _P.data_root                            # writable data root (== ROOT in legacy mode)
DATA_REPLAYS_DIR = _P.data_replays
ORIGINALS_DIR = _P.originals                        # .vrf originals live here
WORK_DIR = _P.work
TMP_DIR = _P.tmp
STATIC_REPLAYS_DIR = ROOT / "bot" / "web" / "static" / "replays_app"
_COMPUTED_ROOT = ROOT

# Versioned store/viewer schema (P3/P2-C). Bumping the viewer schema marks
# stores normalized by an older normalizer as stale (see `freshness`).
STORE_SCHEMA = "valoplant_manifest/v2"
VIEWER_SCHEMA = "valoplant_match/v2"
ROUND_SCHEMA = "valoplant_round/v2"

# Exactly the verified pairs from BUILD_CONTRACT.md §2 — extend only with proof.
MAP_NAMES = {"Triad": "Haven", "Juliett": "Sunset"}
MAP_UUIDS = {
    "Triad": "2bee0dc9-4ffe-519b-1cbd-7fbe763a6047",
    "Juliett": "92584fbe-486a-b1b2-9faa-39b0f486b498",
}

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_PATCH_RE = re.compile(r"release-(\d+\.\d+)")


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def iso_from_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


def read_json(path: Path):
    """Return parsed JSON or None (missing/corrupt files are not fatal)."""
    try:
        with path.open(encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, ValueError):
        return None


def write_json_atomic(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False)
        stream.write("\n")
    os.replace(temporary, path)


def sha256_file(path: Path, chunk: int = 1 << 22) -> tuple[str, int]:
    """Hash a file; returns (hexdigest, size_bytes)."""
    digest = hashlib.sha256()
    size = 0
    with Path(path).open("rb") as stream:
        while True:
            block = stream.read(chunk)
            if not block:
                break
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


def valid_id(replay_id) -> bool:
    return bool(replay_id) and bool(_ID_RE.fullmatch(str(replay_id)))


def replay_dir(replay_id) -> Path:
    """Resolve data/replays/<id> with a containment guard."""
    if not valid_id(replay_id):
        raise ValueError(f"invalid replay id: {replay_id!r}")
    base = DATA_REPLAYS_DIR.resolve()
    candidate = (base / str(replay_id)).resolve()
    if candidate != base and base not in candidate.parents:
        raise ValueError(f"replay id escapes the store: {replay_id!r}")
    return candidate


def rel_source(path: Path) -> str:
    """Data-root-relative (else root-relative, else absolute) POSIX path, e.g. 'replays/abc.vrf'."""
    resolved = Path(path).resolve()
    for base in (DATA_ROOT, ROOT):
        try:
            return resolved.relative_to(Path(base).resolve()).as_posix()
        except ValueError:
            continue
    return Path(path).as_posix()


def resolve_source(rel) -> Path:
    """Resolve a manifest ``source`` to a path, honouring patched module attributes at call time.

    Absolute values pass through. Relative: DATA_ROOT then ROOT (ROOT first when ROOT was patched
    away from the computed code root); first existing wins, else the first candidate."""
    raw = Path(rel)
    if raw.is_absolute():
        return raw
    bases = [DATA_ROOT, ROOT]
    if ROOT != _COMPUTED_ROOT:
        bases = [ROOT, DATA_ROOT]
    candidates = [Path(base) / raw for base in bases]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def branch_to_patch(branch) -> str | None:
    if not isinstance(branch, str):
        return None
    match = _PATCH_RE.search(branch)
    return match.group(1) if match else None


def _parser_commit_text() -> str | None:
    location = _paths.resolve_decoder()
    record = (location.record if location.source in ("bundle", "env") and location.record.is_file()
              else WORK_DIR / "parser-commit.txt")
    try:
        text = record.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def _commit_fields(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() and not key.strip().startswith("#"):
            fields[key.strip()] = value.strip()
    return fields


# Assemblies whose bytes decide decode behaviour. The build record names the ones it covers;
# an unrecorded (legacy) tree keeps the old two-key identity so no migration is required.
PARSER_ASSEMBLIES = ("CliReader.dll", "Replay.Encoding.dll", "Replay.Models.dll", "Replay.Unreal.dll",
                     "Replay.Valorant.dll", "Replay.Valorant.Events.dll")

# (path -> ((size, mtime_ns), sha256)). Polling re-hashes a file only when its stat changes, so the
# status endpoints never pay whole-DLL hashing per request.
_ASSEMBLY_CACHE: dict[str, tuple[tuple[int, int], str]] = {}


def parser_bin_dir() -> Path | None:
    location = _paths.resolve_decoder()
    if location.source in ("bundle", "env") and location.decoder_dir is not None:
        return location.decoder_dir
    base = ROOT / "vendorValorantReplayParser" / "src" / "CliReader" / "bin" / "Release"
    preferred = base / "net10.0"
    if preferred.is_dir():
        return preferred
    for candidate in sorted(base.glob("net*")) if base.is_dir() else []:
        if candidate.is_dir():
            return candidate
    return None


def _hash_file(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def _assembly_sha(path: Path) -> str | None:
    try:
        info = path.stat()
    except OSError:
        return None
    signature = (info.st_size, info.st_mtime_ns)
    key = str(path)
    cached = _ASSEMBLY_CACHE.get(key)
    if cached and cached[0] == signature:
        return cached[1]
    try:
        value = _hash_file(path)
    except OSError:
        return None
    _ASSEMBLY_CACHE[key] = (signature, value)
    return value


def assembly_hashes(names=PARSER_ASSEMBLIES, bin_dir: Path | None = None) -> dict[str, str | None]:
    """Live SHA-256 of each named decoder assembly (``None`` when the file is absent)."""
    directory = bin_dir if bin_dir is not None else parser_bin_dir()
    return {name: (_assembly_sha(directory / name) if directory is not None else None) for name in names}


def assembly_set_sha256(hashes: dict) -> str:
    """One digest over ``name:sha256`` pairs; a missing file contributes ``name:missing``."""
    lines = [f"{name}:{hashes[name] or 'missing'}" for name in sorted(hashes)]
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def recorded_build() -> dict:
    """Build facts recorded next to the source/overlay identity in ``parser-commit.txt``."""
    text = _parser_commit_text() or ""
    fields = _commit_fields(text)
    assemblies = {key[len("assembly."):]: value for key, value in fields.items()
                  if key.startswith("assembly.") and value}
    tested = []
    for key in sorted((k for k in fields if k.startswith("tested.")),
                      key=lambda k: int(k.split(".", 1)[1]) if k.split(".", 1)[1].isdigit() else 0):
        parts = (fields[key].split("|") + ["", "", "", ""])[:4]
        tested.append({"branch": parts[0], "sourceSha256": parts[1], "result": parts[2], "detail": parts[3]})
    return {"assemblySetSha256": fields.get("assembly_set_sha256") or None,
            "assemblies": assemblies, "tested": tested}


def build_state() -> dict:
    """Does the decoder on disk match the recorded build?

    ``unrecorded`` — legacy record with no assembly hashes (nothing to compare);
    ``verified`` — every recorded assembly matches; ``modified`` — an assembly differs or is
    missing (an unrecorded local change); the changed names are listed.
    """
    recorded = recorded_build()
    if not recorded["assemblies"]:
        return {"state": "unrecorded", "recorded": None, "live": None, "changed": []}
    live = assembly_hashes(tuple(sorted(recorded["assemblies"])))
    changed = sorted(name for name, value in recorded["assemblies"].items() if live.get(name) != value)
    return {"state": "modified" if changed else "verified",
            "recorded": recorded["assemblySetSha256"] or assembly_set_sha256(recorded["assemblies"]),
            "live": assembly_set_sha256(live), "changed": changed}


def parser_fingerprint() -> dict:
    """Current parser identity: ``{"commit": <upstream sha>, "overlay": <sha256>[, "assembly": <sha256>]}``.

    `parser-commit.txt` is ``key=value`` since the P2-A overlay
    (``upstream_sha=``, ``overlay_patch_sha256=``); the legacy format is a
    single sha line, kept supported so old trees still read.

    ``assembly`` appears only when the record lists assembly hashes. It is the digest of the
    LIVE assemblies, so a DLL changed locally without updating the record no longer shares an
    identity with the recorded source/overlay.
    """
    text = _parser_commit_text()
    if text is None:
        return {"commit": None, "overlay": None}
    fields = _commit_fields(text)
    if not fields:
        return {"commit": text.splitlines()[0].strip() or None, "overlay": None}
    identity = {"commit": fields.get("upstream_sha") or None,
                "overlay": fields.get("overlay_patch_sha256") or None}
    recorded = recorded_build()["assemblies"]
    if recorded:
        identity["assembly"] = assembly_set_sha256(assembly_hashes(tuple(sorted(recorded))))
    return identity


def parser_commit() -> str | None:
    return parser_fingerprint()["commit"]


def recorded_decode_fingerprint(manifest: dict | None) -> dict:
    """Decode-time parser fingerprint recorded in a manifest, normalized shape."""
    manifest = manifest or {}
    block = _block(manifest, "parser")
    commit = block.get("commit") or manifest.get("parserCommit")
    identity = {"commit": commit or None, "overlay": block.get("overlay") or None}
    if block.get("assembly"):
        identity["assembly"] = block["assembly"]
    return identity


def fingerprints_differ(recorded: dict, current: dict) -> bool:
    """True when a decode-time fingerprint no longer matches the current parser.

    Commit and overlay always compare. The assembly digest compares only when the decode
    recorded one (stores decoded before assembly identity existed are not invalidated), but a
    recorded digest must still be matched by the current build.
    """
    if (recorded.get("commit"), recorded.get("overlay")) != (current.get("commit"), current.get("overlay")):
        return True
    wanted = recorded.get("assembly")
    return bool(wanted) and wanted != current.get("assembly")


def quality_view(manifest: dict | None) -> dict:
    """Decode-quality ``{"state", "reason", "basis"}`` for a manifest.

    ``validated`` / ``suspect`` / ``unvalidated``. A usable store whose manifest predates quality
    states reports ``unvalidated`` — it is never presented as verified. ``state`` is ``None`` for
    stores that are not (yet) decoded (failed / processing).

    A ``processing``/``failed`` status is authoritative: a new attempt clears the recorded
    ``quality`` block when it starts, and this precedence keeps any leftover block (an interrupted
    or older writer) from presenting a previous successful verdict as the current attempt's.
    """
    manifest = manifest or {}
    if manifest.get("status") in ("processing", "failed"):
        return {"state": None, "reason": None, "basis": None}
    recorded = manifest.get("quality")
    if isinstance(recorded, dict) and recorded.get("state") in ("validated", "suspect", "unvalidated"):
        return {"state": recorded["state"], "reason": recorded.get("reason"), "basis": recorded.get("basis")}
    status = manifest.get("status")
    if status == "suspect":
        return {"state": "suspect", "reason": manifest.get("densityReason") or manifest.get("notes") or None,
                "basis": None}
    if manifest.get("source") == "synthetic":
        return {"state": "unvalidated", "reason": "synthetic fixture (no decode)", "basis": None}
    if status in ("ready", "timeline"):
        return {"state": "unvalidated",
                "reason": "no recorded decode-quality check (decoded before quality states were recorded)",
                "basis": None}
    return {"state": None, "reason": None, "basis": None}


def read_manifest(replay_id) -> dict | None:
    return read_json(replay_dir(replay_id) / "manifest.json")


def load_match(replay_id) -> dict | None:
    """match.json from the normalized layout, or the flat demo layout (§7)."""
    rdir = replay_dir(replay_id)
    for path in (rdir / "viewer" / "match.json", rdir / "match.json"):
        data = read_json(path)
        if data is not None:
            return data
    return None


def load_round(replay_id, n: int) -> dict | None:
    try:
        n = int(n)
    except (TypeError, ValueError):
        return None
    if n < 1:
        return None
    rdir = replay_dir(replay_id)
    for rounds_dir in (rdir / "viewer" / "rounds", rdir / "rounds"):
        for name in (f"r{n:02d}.json", f"r{n}.json"):
            data = read_json(rounds_dir / name)
            if data is not None:
                return data
    return None


def update_manifest(replay_id, **fields) -> dict:
    """Merge fields into manifest.json (atomic write, unknown keys preserved)."""
    rdir = replay_dir(replay_id)
    manifest = read_json(rdir / "manifest.json") or {}
    manifest.update(fields)
    write_json_atomic(rdir / "manifest.json", manifest)
    return manifest


def _block(obj, key: str) -> dict:
    """obj[key] when it is a dict, else {} — keeps optional blocks simple."""
    value = obj.get(key) if isinstance(obj, dict) else None
    return value if isinstance(value, dict) else {}


def _stamp(rdir: Path, manifest: dict, match: dict) -> str | None:
    decode = _block(match, "decode")
    for value in (decode.get("normalizedAt"), manifest.get("decodedAt")):
        if isinstance(value, str) and value:
            return value
    try:
        return iso_from_ts((rdir / "manifest.json").stat().st_mtime)
    except OSError:
        return None


def _freshness(manifest: dict, match: dict | None) -> dict:
    """Freshness of one store's normalized viewer data (no disk access).

    ``state``: ``fresh`` (current store/viewer schema + parser fingerprint),
    ``stale`` (viewer data exists but predates the current schema/parser),
    ``missing`` (no viewer data yet), ``synthetic`` (demo fixture — no decode
    pipeline, never marked stale).
    """
    if manifest.get("source") == "synthetic":
        return {
            "state": "synthetic",
            "reasons": [],
            "viewerSchema": (match or {}).get("schema"),
            "parser": recorded_decode_fingerprint(manifest),
            "parserCurrent": parser_fingerprint(),
            "decodeRequired": False,
        }
    reasons: list[str] = []
    if match is None:
        reasons.append("no normalized viewer data")
    elif match.get("schema") != VIEWER_SCHEMA:
        reasons.append(
            f"viewer data schema {match.get('schema')!r} is older than {VIEWER_SCHEMA!r}")
    manifest_schema = manifest.get("schema")
    if manifest_schema and manifest_schema != STORE_SCHEMA:
        reasons.append(
            f"manifest schema {manifest_schema!r} is older than {STORE_SCHEMA!r}")
    recorded = recorded_decode_fingerprint(manifest)
    current = parser_fingerprint()
    if fingerprints_differ(recorded, current):
        if (recorded.get("commit"), recorded.get("overlay")) == (current.get("commit"), current.get("overlay")):
            reasons.append("decoder assemblies differ from the build that decoded this store "
                           "(same source/overlay identity)")
        else:
            reasons.append("decode fingerprint differs from the current parser")
    if not reasons:
        state = "fresh"
    elif match is None:
        state = "missing"
    else:
        state = "stale"
    return {
        "state": state,
        "reasons": reasons,
        "viewerSchema": (match or {}).get("schema"),
        "parser": recorded,
        "parserCurrent": current,
        "decodeRequired": fingerprints_differ(recorded, current),
    }


def freshness(replay_id) -> dict:
    """Read one store and report whether its viewer data is stale."""
    rdir = replay_dir(replay_id)
    manifest = read_json(rdir / "manifest.json") or {}
    match = read_json(rdir / "viewer" / "match.json") or read_json(rdir / "match.json")
    if not manifest and match is None:
        return {
            "state": "absent",
            "reasons": ["replay is not in the store"],
            "viewerSchema": None,
            "parser": {"commit": None, "overlay": None},
            "parserCurrent": parser_fingerprint(),
            "decodeRequired": False,
        }
    return _freshness(manifest, match)


def _entry(replay_id: str, rdir: Path) -> dict | None:
    manifest = read_json(rdir / "manifest.json") or {}
    match = read_json(rdir / "viewer" / "match.json") or read_json(rdir / "match.json") or {}
    if not manifest and not match:
        return None

    map_block = _block(match, "map")
    man_map = _block(manifest, "map")

    status = manifest.get("status")
    if not status and match:
        status = _block(match, "decode").get("status") or "ready"

    rounds = None
    if isinstance(match.get("rounds"), list):
        rounds = len(match["rounds"])
    else:
        counted = None
        for rounds_dir in (rdir / "viewer" / "rounds", rdir / "rounds"):
            if rounds_dir.is_dir():
                counted = (counted or 0) + sum(1 for p in rounds_dir.glob("r*.json") if p.is_file())
        rounds = counted

    fresh = _freshness(manifest, match or None)
    quality = quality_view(manifest)
    return {
        "id": replay_id,
        "source": match.get("source") or manifest.get("source"),
        "mapName": map_block.get("name") or man_map.get("name"),
        "mapImage": map_block.get("image"),
        "patch": match.get("patch") or branch_to_patch(manifest.get("branch")),
        "rounds": rounds,
        "durationMs": match.get("durationMs"),
        "status": status,
        "qualityState": quality["state"],
        "qualityReason": quality["reason"],
        "stale": fresh["state"] == "stale",
        "staleReasons": fresh["reasons"] if fresh["state"] == "stale" else [],
        "createdAt": _stamp(rdir, manifest, match),
    }


def list_replays() -> list[dict]:
    """Every replays/<id> directory that holds a manifest or normalized match."""
    entries = []
    if not DATA_REPLAYS_DIR.is_dir():
        return entries
    for rdir in sorted(DATA_REPLAYS_DIR.iterdir()):
        if not rdir.is_dir() or not valid_id(rdir.name):
            continue
        entry = _entry(rdir.name, rdir)
        if entry is not None:
            entries.append(entry)
    entries.sort(key=lambda e: e.get("createdAt") or "", reverse=True)
    return entries

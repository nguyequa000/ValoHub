#!/usr/bin/env python3
"""Fetch agent / ability art for the local replay viewer from valorant-api.com.

Why this exists
---------------
``displayIcon`` is 1024x1024 (~410 KB) -- useless at a 26 px map marker -- and
there is **no server-side resize** (``?size=64`` is ignored, ``displayicon_small
.png`` 404s).  So we download the JSON-provided URLs once and re-encode locally:

* ``minimapPortrait`` (64x64, ~6.5 KB) -> ``agents/map/<uuid>.png``  *verbatim*
  (the bytes are already the right size; re-encoding would only lose quality).
* ``displayIcon``  (1024x1024 or 512x512 for Neon) -> ``agents/<uuid>.png``
  downscaled to 96x96 with Pillow LANCZOS.
* each ability ``displayIcon`` -> ``abilities/<uuid>-<Slot>.png`` with the long
  side capped at 128 px (sources already <=128 px are copied byte-for-byte;
  Veto's 512 px ability icons and Jett's 1024 px Passive are downscaled; a
  smaller source is **never** upscaled).

Everything is keyed by the agent **uuid** because the display name ``KAY/O``
contains a slash and cannot be a filename.

Outputs (defaults)
------------------
    bot/web/static/replays_app/assets/agents/map/<uuid>.png      64x64 verbatim
    bot/web/static/replays_app/assets/agents/<uuid>.png          96x96 portrait
    bot/web/static/replays_app/assets/abilities/<uuid>-<Slot>.png <=128 px
    bot/web/static/replays_app/assets/agents/index.json          manifest v2
    bot/web/static/replays_app/assets/.asset_sources.json        idempotency journal
    bot/replay/agent_ids.json                                    name <-> uuid map

Idempotency
-----------
``.asset_sources.json`` records the sha256 of every *source* we consumed for a
given output file (plus the transform id).  A rerun skips an output when the
file exists, the journal entry matches, and the freshly downloaded source still
hashes the same -- so a second run writes **0** files.  ``--check`` performs the
same downloads and reports drift without writing anything (exit 1 on drift).

Failures are loud and per item: they land in ``missing[]`` (with a reason) and
never produce placeholder art or another agent's icon.  A required asset
(portrait, map icon, the four combat ability icons) that cannot be written makes
the process exit 1; optional Passives the API does not publish are reported in
``missing[]`` as ``optional`` and do *not* fail the run.

Usage
-----
    py -3.12 tools/fetch_agent_assets.py               # populate / refresh
    py -3.12 tools/fetch_agent_assets.py --check       # drift report, writes nothing
    py -3.12 tools/fetch_agent_assets.py --force       # ignore the journal
    py -3.12 tools/fetch_agent_assets.py --agents Jett,KAY/O
    py -3.12 tools/fetch_agent_assets.py --out <dir> [--ids-out <path>]

Standard library + Pillow only (see requirements.txt).
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime
import hashlib
import json
import os
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

try:
    from PIL import Image
except ImportError:  # pragma: no cover - dependency guard
    sys.stderr.write("Pillow is required: py -3.12 -m pip install 'Pillow>=10.0'\n")
    raise

API_BASE = "https://valorant-api.com/v1"
ROSTER_PATH = "/agents?isPlayableCharacter=true"
VERSION_PATH = "/version"

USER_AGENT = (
    "valohub-agent-assets/1.0 (+local personal replay viewer; "
    "one-shot cache, non-commercial)"
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUT = os.path.join(REPO_ROOT, "bot", "web", "static", "replays_app", "assets")
DEFAULT_IDS = os.path.join(REPO_ROOT, "bot", "replay", "agent_ids.json")

STATE_NAME = ".asset_sources.json"
STATE_SCHEMA = "valoplant_asset_sources/v1"
INDEX_SCHEMA = "valoplant_agent_assets/v2"
IDS_SCHEMA = "valoplant_agent_ids/v1"

COMBAT_SLOTS = ("Ability1", "Ability2", "Grenade", "Ultimate")
OPTIONAL_SLOTS = ("Passive",)
ALL_SLOTS = COMBAT_SLOTS + OPTIONAL_SLOTS

MAP_EDGE = 64          # minimapPortrait is natively 64x64 -- recorded, never enforced
PORTRAIT_EDGE = 96
ABILITY_MAX = 128

T_MAP = "copy-verbatim"
T_PORTRAIT = "portrait-96-lanczos"
T_ABILITY = "ability-max128"

# HTTP politeness / resilience.
TIMEOUT = 60
ATTEMPTS = 3
BACKOFF = 0.6          # seconds; doubles per attempt


class FetchError(RuntimeError):
    """A source could not be downloaded (after retries) or parsed."""


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def png_size(data: bytes):
    """Read width/height straight out of a PNG IHDR; (w, h) or None if not PNG."""
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", data[16:24])
    return int(width), int(height)


def file_size_png(path: str):
    """(w, h) of a PNG on disk, or None."""
    try:
        with open(path, "rb") as handle:
            return png_size(handle.read(33))
    except OSError:
        return None


def utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_json(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default


def write_json(path: str, payload) -> bool:
    """Write `payload` atomically; return True when the bytes on disk changed."""
    text = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False) + "\n"
    try:
        with open(path, "r", encoding="utf-8") as handle:
            if handle.read() == text:
                return False
    except OSError:
        pass
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    os.replace(tmp, path)
    return True


def dir_bytes(root: str) -> int:
    total = 0
    for base, _dirs, names in os.walk(root):
        for name in names:
            try:
                total += os.path.getsize(os.path.join(base, name))
            except OSError:
                pass
    return total


def human_bytes(count: int) -> str:
    value = float(count)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024.0 or unit == "GB":
            return ("%.1f %s" % (value, unit)) if unit != "B" else ("%d B" % count)
        value /= 1024.0
    return "%d B" % count


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def http_get(url: str, timeout: int = TIMEOUT) -> bytes:
    """GET `url` with retries + exponential backoff. Raises FetchError."""
    last = None
    for attempt in range(ATTEMPTS):
        request = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
        })
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read()
                if response.status != 200:
                    raise FetchError("HTTP %s" % response.status)
                return body
        except urllib.error.HTTPError as exc:
            last = "HTTP %s" % exc.code
            if exc.code in (400, 403, 404):      # not retryable
                break
            last = "HTTP %s" % exc.code
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = "%s: %s" % (type(exc).__name__, exc)
        if attempt + 1 < ATTEMPTS:
            time.sleep(BACKOFF * (2 ** attempt))
    raise FetchError("download failed after %d attempt(s): %s" % (ATTEMPTS, last))


def json_get(path: str):
    body = http_get(API_BASE + path)
    try:
        return json.loads(body.decode("utf-8"))
    except ValueError as exc:
        raise FetchError("invalid JSON from %s: %s" % (path, exc))


# ---------------------------------------------------------------------------
# transforms
# ---------------------------------------------------------------------------

def _flatten_to_rgba(image):
    if image.mode == "RGBA":
        return image
    if image.mode in ("P", "LA", "L", "RGB", "CMYK"):
        return image.convert("RGBA")
    return image.convert("RGBA")


def make_portrait(data: bytes) -> bytes:
    """Downscale displayIcon to an exact PORTRAIT_EDGE square (never upscales)."""
    import io

    with Image.open(io.BytesIO(data)) as image:
        image.load()
        source = _flatten_to_rgba(image)
        width, height = source.size
        if width > PORTRAIT_EDGE or height > PORTRAIT_EDGE:
            scale = min(PORTRAIT_EDGE / float(width), PORTRAIT_EDGE / float(height))
            target = (max(1, int(round(width * scale))), max(1, int(round(height * scale))))
            source = source.resize(target, Image.LANCZOS)
        canvas = Image.new("RGBA", (PORTRAIT_EDGE, PORTRAIT_EDGE), (0, 0, 0, 0))
        canvas.paste(source, ((PORTRAIT_EDGE - source.size[0]) // 2,
                              (PORTRAIT_EDGE - source.size[1]) // 2))
        out = io.BytesIO()
        canvas.save(out, format="PNG", optimize=True)
        return out.getvalue()


def make_ability(data: bytes) -> bytes:
    """Cap the long side at ABILITY_MAX; return the source bytes if already small."""
    size = png_size(data)
    if size and size[0] <= ABILITY_MAX and size[1] <= ABILITY_MAX:
        return data                     # byte-for-byte, no re-encode
    import io

    with Image.open(io.BytesIO(data)) as image:
        image.load()
        source = _flatten_to_rgba(image)
        width, height = source.size
        if width > ABILITY_MAX or height > ABILITY_MAX:
            scale = ABILITY_MAX / float(max(width, height))
            target = (max(1, int(round(width * scale))), max(1, int(round(height * scale))))
            source = source.resize(target, Image.LANCZOS)
        out = io.BytesIO()
        source.save(out, format="PNG", optimize=True)
        return out.getvalue()


TRANSFORMS = {
    T_MAP: lambda data: data,
    T_PORTRAIT: make_portrait,
    T_ABILITY: make_ability,
}


# ---------------------------------------------------------------------------
# roster
# ---------------------------------------------------------------------------

def load_roster():
    """(apiVersion, [agent dicts]) -- apiVersion is /v1/version's `version`."""
    version = json_get(VERSION_PATH)
    api_version = None
    if isinstance(version, dict):
        api_version = (version.get("data") or {}).get("version")
    roster = json_get(ROSTER_PATH)
    agents = (roster.get("data") if isinstance(roster, dict) else None) or []
    agents = [agent for agent in agents if agent.get("isPlayableCharacter")]
    if not agents:
        raise FetchError("roster came back empty from %s" % ROSTER_PATH)
    return api_version, agents


def plan_jobs(agents, out_dir):
    """Every output file we want on disk, one job each."""
    jobs = []
    for agent in agents:
        uuid = str(agent["uuid"]).lower()
        name = agent.get("displayName")
        portrait_url = agent.get("displayIcon")
        map_url = agent.get("minimapPortrait")
        jobs.append({
            "key": "%s|portrait" % name, "agent": name, "uuid": uuid, "slot": None,
            "url": portrait_url, "transform": T_PORTRAIT,
            "rel": "agents/%s.png" % uuid, "required": True,
        })
        jobs.append({
            "key": "%s|map" % name, "agent": name, "uuid": uuid, "slot": None,
            "url": map_url, "transform": T_MAP,
            "rel": "agents/map/%s.png" % uuid, "required": True,
        })
        for ability in agent.get("abilities") or []:
            slot = ability.get("slot")
            if slot not in ALL_SLOTS:
                continue
            required = slot in COMBAT_SLOTS
            jobs.append({
                "key": "%s|%s" % (name, slot), "agent": name, "uuid": uuid, "slot": slot,
                "ability": ability.get("displayName"), "url": ability.get("displayIcon"),
                "transform": T_ABILITY,
                "rel": "abilities/%s-%s.png" % (uuid, slot), "required": required,
            })
    return jobs


# ---------------------------------------------------------------------------
# one job
# ---------------------------------------------------------------------------

def run_job(job, out_dir, journal, check_only, force):
    """Fetch + transform one asset. Returns a result dict; never raises."""
    rel = job["rel"]
    dest = os.path.join(out_dir, rel.replace("/", os.sep))
    result = {
        "key": job["key"], "agent": job["agent"], "uuid": job["uuid"],
        "slot": job["slot"], "rel": rel, "required": job["required"],
        "written": False, "skipped": False, "drift": [], "missing": None,
        "source_sha256": None, "bytes": None, "size": None,
    }

    if not job.get("url"):
        reason = ("API publishes no icon for the optional %s slot" % job["slot"]
                  if job["slot"] in OPTIONAL_SLOTS else "API field is null")
        result["missing"] = reason
        return result

    try:
        data = http_get(job["url"])
    except FetchError as exc:
        result["missing"] = str(exc)
        return result

    source_sha = sha256(data)
    result["source_sha256"] = source_sha
    entry = journal.get(rel) or {}
    existing = os.path.isfile(dest)
    dimensions = file_size_png(dest) if existing else None

    if check_only:
        if not existing:
            result["drift"].append("missing_file")
        elif not entry:
            result["drift"].append("untracked_file")
        elif entry.get("source_sha256") != source_sha:
            result["drift"].append("source_changed")
        elif entry.get("transform") != job["transform"]:
            result["drift"].append("transform_changed")
        elif dimensions is None:
            result["drift"].append("unreadable_png")
        elif entry.get("output_bytes") and os.path.getsize(dest) != entry.get("output_bytes"):
            result["drift"].append("output_size_changed")
        result["size"] = dimensions
        return result

    if existing and not force and entry.get("source_sha256") == source_sha \
            and entry.get("transform") == job["transform"]:
        result["skipped"] = True
        result["size"] = dimensions
        result["bytes"] = os.path.getsize(dest)
        return result

    try:
        payload = TRANSFORMS[job["transform"]](data)
    except Exception as exc:  # noqa: BLE001 - per-item failure, never fatal
        result["missing"] = "transform failed: %s: %s" % (type(exc).__name__, exc)
        return result

    size = png_size(payload)
    if size is None:
        result["missing"] = "transformed bytes are not a PNG"
        return result
    if job["transform"] == T_PORTRAIT and size != (PORTRAIT_EDGE, PORTRAIT_EDGE):
        result["missing"] = "portrait came out %dx%d, expected %dx%d" % (
            size[0], size[1], PORTRAIT_EDGE, PORTRAIT_EDGE)
        return result
    if job["transform"] == T_ABILITY and max(size) > ABILITY_MAX:
        result["missing"] = "ability icon came out %dx%d, above %d" % (
            size[0], size[1], ABILITY_MAX)
        return result

    try:
        parent = os.path.dirname(dest)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(dest, "wb") as handle:
            handle.write(payload)
    except OSError as exc:
        result["missing"] = "write failed: %s" % exc
        return result

    journal[rel] = {
        "source_url": job["url"],
        "source_sha256": source_sha,
        "source_bytes": len(data),
        "source_size": list(png_size(data) or ()),
        "transform": job["transform"],
        "output_bytes": len(payload),
        "output_size": list(size),
        "fetched_at": utc_now(),
    }
    result["written"] = True
    result["bytes"] = len(payload)
    result["size"] = size
    return result


# ---------------------------------------------------------------------------
# manifests
# ---------------------------------------------------------------------------

def build_index(agents, out_dir, api_version, missing, existing_meta=None):
    by_name = {}
    for agent in sorted(agents, key=lambda item: item["displayName"].lower()):
        uuid = str(agent["uuid"]).lower()
        name = agent["displayName"]
        entry = {"uuid": uuid}

        portrait_rel = "agents/%s.png" % uuid
        if os.path.isfile(os.path.join(out_dir, portrait_rel.replace("/", os.sep))):
            entry["portrait"] = portrait_rel

        map_rel = "agents/map/%s.png" % uuid
        if os.path.isfile(os.path.join(out_dir, map_rel.replace("/", os.sep))):
            entry["map"] = map_rel

        abilities = {}
        for ability in agent.get("abilities") or []:
            slot = ability.get("slot")
            if slot not in ALL_SLOTS:
                continue
            rel = "abilities/%s-%s.png" % (uuid, slot)
            if os.path.isfile(os.path.join(out_dir, rel.replace("/", os.sep))):
                abilities[ability.get("displayName") or slot] = rel
        entry["abilities"] = abilities
        by_name[name] = entry

    meta = {
        "schema": INDEX_SCHEMA,
        "apiVersion": api_version,
        "generatedAt": (existing_meta or {}).get("generatedAt") or utc_now(),
        "missing": missing,
    }
    payload = {"_meta": meta}
    payload.update(by_name)
    return payload


def build_ids(agents, api_version):
    by_name = {}
    by_uuid = {}
    for agent in sorted(agents, key=lambda item: item["displayName"].lower()):
        uuid = str(agent["uuid"]).lower()
        by_name[agent["displayName"]] = uuid
        by_uuid[uuid] = agent["displayName"]
    return {"schema": IDS_SCHEMA, "apiVersion": api_version,
            "byName": by_name, "byUuid": by_uuid}


def _same_ignoring_timestamp(left, right) -> bool:
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    left_meta = dict(left.get("_meta") or {})
    right_meta = dict(right.get("_meta") or {})
    left_meta.pop("generatedAt", None)
    right_meta.pop("generatedAt", None)
    if left_meta != right_meta:
        return False
    left_body = {k: v for k, v in left.items() if k != "_meta"}
    right_body = {k: v for k, v in right.items() if k != "_meta"}
    return left_body == right_body


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch 30-agent art (64 px map icons, 96 px portraits, "
                    "<=128 px ability icons) for the local replay viewer.")
    parser.add_argument("--check", action="store_true",
                        help="report drift against the journal and write nothing")
    parser.add_argument("--force", action="store_true",
                        help="re-fetch and rewrite every asset, ignoring the journal")
    parser.add_argument("--agents", default=None,
                        help="comma separated display names (default: the whole roster)")
    parser.add_argument("--out", default=DEFAULT_OUT,
                        help="asset root (default: %(default)s)")
    parser.add_argument("--ids-out", default=DEFAULT_IDS,
                        help="agent_ids.json destination (default: %(default)s)")
    parser.add_argument("--workers", type=int, default=6, help="download pool size (default: 6)")
    args = parser.parse_args(argv)

    out_dir = os.path.abspath(args.out)
    state_path = os.path.join(out_dir, STATE_NAME)
    index_path = os.path.join(out_dir, "agents", "index.json")
    ids_path = os.path.abspath(args.ids_out)

    before = dir_bytes(out_dir) if os.path.isdir(out_dir) else 0
    started = time.time()

    try:
        api_version, roster = load_roster()
    except FetchError as exc:
        sys.stderr.write("FATAL: could not read the roster: %s\n" % exc)
        return 1

    wanted = None
    if args.agents:
        wanted = {name.strip().lower() for name in args.agents.split(",") if name.strip()}
        unknown = sorted(wanted - {agent["displayName"].lower() for agent in roster})
        if unknown:
            sys.stderr.write("FATAL: not in the playable roster: %s\n" % ", ".join(unknown))
            return 1
        roster = [agent for agent in roster if agent["displayName"].lower() in wanted]

    jobs = plan_jobs(roster, out_dir)
    state = read_json(state_path, {})
    journal = state.get("files") if isinstance(state, dict) else None
    if not isinstance(journal, dict):
        journal = {}

    print("apiVersion: %s | agents: %d | jobs: %d | out: %s"
          % (api_version, len(roster), len(jobs), out_dir))
    print("assets bytes before: %s (%d)" % (human_bytes(before), before))

    results = []
    new_journal = dict(journal)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = [pool.submit(run_job, job, out_dir, new_journal, args.check, args.force)
                   for job in jobs]
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())
    results.sort(key=lambda item: item["rel"])

    written = [item for item in results if item["written"]]
    skipped = [item for item in results if item["skipped"]]
    drifted = [item for item in results if item["drift"]]
    failed_required = [item for item in results
                       if item["missing"] and item["required"]]
    absent_optional = [item for item in results
                       if item["missing"] and not item["required"]]

    # The index describes the whole roster, so a --agents run still refreshes it
    # correctly; a --check run never writes.
    full_roster = roster
    if args.agents:
        try:
            _version, full_roster = load_roster()
        except FetchError:
            full_roster = roster

    existing_index = read_json(index_path, {})
    missing_rows = []
    for item in sorted(results, key=lambda row: (row["agent"] or "", row["rel"])):
        if not item["missing"]:
            continue
        missing_rows.append({
            "agent": item["agent"], "uuid": item["uuid"], "key": item["key"],
            "path": item["rel"], "reason": item["missing"],
            "optional": not item["required"],
        })
    # Keep previously recorded rows for agents outside a --agents subset.
    if args.agents:
        for row in (existing_index.get("_meta") or {}).get("missing") or []:
            if row.get("agent", "").lower() not in wanted:
                missing_rows.append(row)
    missing_rows.sort(key=lambda row: (row.get("agent") or "", row.get("key") or ""))

    index_changed = False
    ids_changed = False
    result_drift = None
    if not args.check:
        payload = build_index(full_roster, out_dir, api_version, missing_rows,
                              existing_meta=existing_index.get("_meta"))
        if not _same_ignoring_timestamp(payload, existing_index):
            index_changed = write_json(index_path, payload)
        ids_changed = write_json(ids_path, build_ids(full_roster, api_version))
        write_json(state_path, {"schema": STATE_SCHEMA, "apiVersion": api_version,
                                "files": dict(sorted(new_journal.items()))})
    else:
        payload = build_index(full_roster, out_dir, api_version, missing_rows,
                              existing_meta=existing_index.get("_meta"))
        if not _same_ignoring_timestamp(payload, existing_index):
            result_drift = "index_stale"

    after = dir_bytes(out_dir) if os.path.isdir(out_dir) else 0
    elapsed = time.time() - started

    if args.check:
        print("check mode: %d file(s) drifted, %d missing, index %s"
              % (len(drifted), len(failed_required) + len(absent_optional),
                 result_drift or "ok"))
        for item in drifted:
            print("  DRIFT %-46s %s" % (item["rel"], ",".join(item["drift"])))
        for item in failed_required:
            print("  MISSING(required) %-40s %s" % (item["rel"], item["missing"]))
        for item in absent_optional:
            print("  ABSENT(optional)  %-40s %s" % (item["rel"], item["missing"]))
        if result_drift:
            print("  DRIFT %-46s index.json is out of date" % "agents/index.json")
        return 1 if (drifted or failed_required or result_drift) else 0

    print("written: %d | skipped: %d | missing[]: %d row(s) (%d optional absent, "
          "%d required failed) | index.json %s | agent_ids.json %s"
          % (len(written), len(skipped), len(missing_rows),
             len(absent_optional), len(failed_required),
             "updated" if index_changed else "unchanged",
             "updated" if ids_changed else "unchanged"))
    for item in failed_required:
        sys.stderr.write("MISSING(required) %-46s %s\n" % (item["rel"], item["missing"]))
    for item in absent_optional:
        print("absent(optional)  %-46s %s" % (item["rel"], item["missing"]))
    print("assets bytes after: %s (%d) | elapsed %.1fs"
          % (human_bytes(after), after, elapsed))

    if failed_required:
        sys.stderr.write("FAILED: %d required asset(s) missing -- see missing[] above\n"
                         % len(failed_required))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

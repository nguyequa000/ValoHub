#!/usr/bin/env python3
"""Isolated real-replay import QA for the ValoHub desktop migration (lane Q1).

Runs a **real** ``.vrf`` import against a disposable app root so the live
library and the three original recordings stay byte-identical, then asserts the
DATA-BASELINE §12-5/7/8 contract:

* 13.06 (``bdb6b4ab…``) -> id ``bad1543033d11396``, ``ready``, 18 rounds,
  10 players, 128 kills, 11 plants, 1 defuse, ``utilityCounts`` sum 359
  (+ §12-4 round-1 payload, decoder identity);
* 11.07 (``2f7c79fb…``) -> an honest ``failed`` job: no viewer data, failure
  recorded in the manifest, exactly one job (no retry loop), ``index_replay``
  still reports 21 rounds.

Modes
-----
``--server legacy``
    Builds the isolated root from DATA-BASELINE §11 (copy ``bot/``, junctions
    for ``dotnet10-sdk`` + ``vendorValorantReplayParser`` READ-ONLY, copy
    ``parser-commit.txt``), starts ``py -3.12 -m bot.web`` on 127.0.0.1:18851
    inside it (``VALO_OFFLINE=1``), imports the copied ``.vrf`` through
    ``POST /api/replays/import-path``, polls ``/api/jobs`` and then removes the
    junctions it created (``cmd /c rmdir <junction>``, never ``/s``).

``--server http://127.0.0.1:P``
    Drives the same HTTP flow with ``X-ValoHub-Token`` against an
    already-running desktop app; ``--home`` is only used to read local
    artifacts (manifest, viewer files) when they are available.

Guard snapshots of the real repo are taken before and after the run and must
be byte-identical (``--allow-live-read`` acknowledges the read-only access).

Stdlib only, Python 3.12.  One JSON report; non-zero exit on any failed
assertion.  Report schema: ``valohub.isolated_import/v1``.
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import guard as guard_mod  # noqa: E402

SCHEMA = "valohub.isolated_import/v1"
DEFAULT_PORT = 18851
TOKEN_HEADER = "X-ValoHub-Token"

REPLAYS = {
    "bdb6b4ab": {
        "file": "bdb6b4ab-fa31-4e51-b77a-35e3a1e7a3c2.vrf",
        "id": "bad1543033d11396",
        "outcome": "ready",
        "rounds": 18,
        "players": 10,
        "kills": 128,
        "plants": 11,
        "defuses": 1,
        # utilityCounts total: DATA-BASELINE §12-5 recorded 359 from the *stale*
        # live store (decoded 2026-10-20 with parser 68a248da).  A fresh decode
        # with the shipped parser 09a8e1e1 adds 3 Sova Owl Drone rows (P1 +2,
        # P6 +1) from the current ability catalog (P8-B2b Hunter|Grenade), so
        # the measured fresh total is 362.  Both numbers are reported.
        "utility": 362,
        "utilityStaleLive": 359,
        "droneRows": 3,
    },
    "c428d9d5": {
        "file": "c428d9d5-079d-41d9-911e-d66e53d03f0b.vrf",
        "id": "87976b8cd193496b",
        "outcome": "ready",
        "rounds": 22,
        "players": 10,
        "kills": 175,
        "plants": 14,
        "defuses": 6,
        "utility": 323,
        "utilityStaleLive": 323,
    },
    "2f7c79fb": {
        "file": "2f7c79fb-5fda-4a8f-89a1-7fb27d9ac587.vrf",
        "id": "c5809e86257adfb0",
        "outcome": "failed",
        "rounds": None,
        "indexRounds": 21,
    },
}

EXPECTED_PARSER_COMMIT = "09a8e1e1d81c48840b8810f523f1595e539b7d55"

ROUND1 = {"schema": "valoplant_round/v2", "tickMs": 100, "startMs": 579,
          "endMs": 83495, "frames": 830, "events": 9, "utility": 20}

INDEX_PROBE = """\
import json, os, sys
sys.path.insert(0, os.getcwd())
from bot.replay.container_index import index_replay
print(json.dumps(index_replay(sys.argv[1])))
"""


class ImportError_(Exception):
    """Environment / usage problem (exit 2), not an assertion failure."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_replay(repo: Path, spec: str) -> dict:
    key = spec.strip()
    for prefix, meta in REPLAYS.items():
        if key == prefix or key == meta["file"] or key == meta["id"] or meta["file"].startswith(key):
            path = repo / "replays" / meta["file"]
            if not path.is_file():
                raise ImportError_(f"replay not found in {repo / 'replays'}: {meta['file']}")
            return {**meta, "path": path}
    raise ImportError_(f"unknown --replay {spec!r}; known: {', '.join(REPLAYS)}")


# ------------------------------------------------------------------ HTTP

def _http(base: str, method: str, path: str, body=None, token: str | None = None,
          timeout: float = 30.0) -> dict:
    parsed = urlsplit(base)
    conn = http.client.HTTPConnection(parsed.hostname or "127.0.0.1", parsed.port or 80,
                                      timeout=timeout)
    headers = {"Accept": "application/json", "Connection": "close"}
    if token:
        headers[TOKEN_HEADER] = token
    payload = None
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    try:
        conn.request(method, path, body=payload, headers=headers)
        response = conn.getresponse()
        raw = response.read()
        try:
            data = json.loads(raw.decode("utf-8")) if raw else None
        except (ValueError, UnicodeDecodeError):
            data = None
        return {"status": response.status, "data": data,
                "bodyPreview": raw.decode("utf-8", "replace")[:400]}
    finally:
        conn.close()


def wait_ready(base: str, timeout: float, token: str | None = None) -> float:
    started = time.monotonic()
    last_error = "no attempt"
    while time.monotonic() - started < timeout:
        try:
            result = _http(base, "GET", "/api/replays", token=token, timeout=5)
            if result["status"] == 200:
                return time.monotonic() - started
            last_error = f"HTTP {result['status']}"
        except OSError as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(0.5)
    raise ImportError_(f"server at {base} not ready within {timeout:.0f}s ({last_error})")


def poll_job(base: str, job_id: str, timeout: float, token: str | None = None) -> tuple[dict, float]:
    started = time.monotonic()
    last = None
    while time.monotonic() - started < timeout:
        result = _http(base, "GET", "/api/jobs", token=token, timeout=15)
        jobs = (result.get("data") or {}).get("jobs") or []
        for job in jobs:
            if job.get("id") == job_id:
                last = job
                if job.get("status") in ("done", "error") or job.get("stage") in ("done", "error"):
                    return job, time.monotonic() - started
        time.sleep(1.0)
    raise ImportError_(f"job {job_id} did not finish within {timeout:.0f}s "
                       f"(last: {json.dumps(last)[:300] if last else 'not listed'})")


# --------------------------------------------------------- isolated root

def _junction(link: Path, target: Path) -> None:
    result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise ImportError_(f"mklink /J failed for {link}: {result.stderr or result.stdout}")


def _remove_junction(link: Path) -> dict:
    result = subprocess.run(["cmd", "/c", "rmdir", str(link)], capture_output=True, text=True)
    return {"path": str(link), "exit": result.returncode,
            "output": (result.stdout or result.stderr).strip()[:200]}


def build_root(repo: Path, home: Path) -> dict:
    repo, home = repo.resolve(), home.resolve()
    if home.exists() and any(home.iterdir()):
        raise ImportError_(f"--home must be a new or empty directory: {home}")
    home.mkdir(parents=True, exist_ok=True)
    for name in ("VALOPLANT_WORK", "replays", "data/replays", "cache"):
        (home / name).mkdir(parents=True, exist_ok=True)
    shutil.copytree(repo / "bot", home / "bot",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    record = repo / "VALOPLANT_WORK/parser-commit.txt"
    if record.is_file():
        shutil.copy2(record, home / "VALOPLANT_WORK/parser-commit.txt")
    junctions = []
    for name in ("dotnet10-sdk", "vendorValorantReplayParser"):
        source = repo / name
        if not source.is_dir():
            raise ImportError_(f"missing decoder source directory: {source}")
        link = home / name
        _junction(link, source)
        junctions.append(link)
    return {"home": str(home), "junctions": [str(j) for j in junctions]}


def start_legacy(home: Path, port: int) -> tuple[subprocess.Popen, float, Path]:
    env = os.environ.copy()
    for name in ("HENRIKDEV_TOKEN", "VALO_LLM_URL", "VALO_LLM_MODEL", "VALO_LLM_KEY_FILE",
                 "VALO_IDENTITY_SEED"):
        env.pop(name, None)
    dotnet_home = home / "VALOPLANT_WORK/dotnet-home"
    env.update({
        "HOST": "127.0.0.1",
        "PORT": str(port),
        "VALO_OFFLINE": "1",
        "VALO_LOBBY": "0",
        "VALO_WATCHER": "0",
        "PYTHONUNBUFFERED": "1",
        # keep every dotnet write out of the READ-ONLY dotnet10-sdk junction
        "DOTNET_CLI_HOME": str(dotnet_home),
        "NUGET_PACKAGES": str(dotnet_home / "packages"),
        "DOTNET_NOLOGO": "1",
        "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
    })
    log = home / "server.log"
    handle = open(log, "w", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, "-m", "bot.web"], cwd=str(home), env=env,
                            stdout=handle, stderr=subprocess.STDOUT)
    handle.close()
    return proc, time.monotonic(), log


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def stop_legacy(proc: subprocess.Popen) -> dict:
    if proc.poll() is not None:
        return {"alreadyExited": proc.returncode}
    proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)
        return {"terminated": False, "exit": proc.returncode, "forced": True}
    return {"terminated": True, "exit": proc.returncode}


def index_rounds(home: Path, vrf: Path) -> dict:
    probe = home / "VALOPLANT_WORK/index_probe.py"
    probe.write_text(INDEX_PROBE, encoding="utf-8")
    result = subprocess.run([sys.executable, str(probe), str(vrf)], cwd=str(home),
                            capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        return {"ok": False, "error": (result.stderr or result.stdout).strip()[:400]}
    try:
        data = json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"ok": False, "error": result.stdout[-400:]}
    rounds = data.get("rounds") or []
    # Keep the evidence compact: the 11.07 container index is 21 rounds of death
    # lists, so record the count and the contiguous round numbers only.
    return {"ok": True, "roundCount": len(rounds),
            "roundNumbers": [row.get("roundNumber", row.get("roundIndex"))
                             for row in rounds if isinstance(row, dict)],
            "map": data.get("map"), "branch": data.get("branch")}


# -------------------------------------------------------------- assertions

def _check(checks: list, ident: str, ok: bool, expected, actual, detail: str = "") -> None:
    checks.append({"id": ident, "ok": bool(ok), "expected": expected,
                   "actual": actual, "detail": detail})


def _utility_total(counts: dict) -> int:
    if not isinstance(counts, dict):
        return 0
    total = 0
    for value in counts.values():
        if isinstance(value, dict):
            total += int(value.get("total") or 0)
        elif isinstance(value, (int, float)):
            total += int(value)
    return total


def _contiguous(rounds: list) -> bool:
    numbers = [row.get("n") for row in rounds if isinstance(row, dict)]
    return numbers == list(range(1, len(numbers) + 1))


def _index_round_count(index: dict):
    """Round count from an index report: int, list of rounds, or explicit count."""
    if isinstance(index.get("roundCount"), int):
        return index["roundCount"]
    rounds = index.get("rounds")
    if isinstance(rounds, list):
        return len(rounds)
    if isinstance(rounds, int):
        return rounds
    return None


def assert_ready(meta: dict, match: dict, job: dict, decoder: dict,
                 round1: dict | None, checks: list) -> None:
    _check(checks, "job_done", job.get("status") == "done",
           "done", job.get("status"), f"stage={job.get('stage')} error={job.get('error')}")
    _check(checks, "replay_id", meta["id"] == (job.get("replayId") or meta["id"]),
           meta["id"], job.get("replayId"))
    rounds = match.get("rounds") or []
    _check(checks, "rounds", len(rounds) == meta["rounds"], meta["rounds"], len(rounds))
    _check(checks, "rounds_contiguous", _contiguous(rounds), "1..n", [r.get("n") for r in rounds][:3])
    players = match.get("players") or []
    _check(checks, "players", len(players) == meta["players"], meta["players"], len(players))
    kills = sum(int(r.get("kills") or 0) for r in rounds)
    plants = sum(int(r.get("plants") or 0) for r in rounds)
    defuses = sum(int(r.get("defuses") or 0) for r in rounds)
    _check(checks, "kills", kills == meta["kills"], meta["kills"], kills)
    _check(checks, "plants", plants == meta["plants"], meta["plants"], plants)
    _check(checks, "defuses", defuses == meta["defuses"], meta["defuses"], defuses)
    if meta.get("utility") is not None:
        utility = _utility_total(match.get("utilityCounts") or {})
        _check(checks, "utility_total", utility == meta["utility"], meta["utility"], utility,
               f"stale live-store value: {meta.get('utilityStaleLive')}")
    if meta.get("droneRows") is not None:
        drone = sum(int((value.get("byKind") or {}).get("drone", 0))
                    for value in (match.get("utilityCounts") or {}).values()
                    if isinstance(value, dict))
        _check(checks, "utility_drone_rows", drone == meta["droneRows"],
               meta["droneRows"], drone,
               "Owl Drone rows classified by the current catalog (P8-B2b)")
    _check(checks, "map", (match.get("map") or {}).get("name") == "Haven"
           if meta["id"] == "bad1543033d11396" else True,
           "Haven", (match.get("map") or {}).get("name"))
    _check(checks, "decoder_status", decoder.get("status") == "ready", "ready", decoder.get("status"))
    _check(checks, "decoder_commit", decoder.get("parserCommit") == EXPECTED_PARSER_COMMIT,
           EXPECTED_PARSER_COMMIT, decoder.get("parserCommit"))
    _check(checks, "decoder_build", decoder.get("buildState") == "verified",
           "verified", decoder.get("buildState"))
    _check(checks, "decoder_unchanged", decoder.get("buildChangedAssemblies") in ([], None),
           [], decoder.get("buildChangedAssemblies"))
    if round1 is not None and meta["id"] == "bad1543033d11396":
        _check(checks, "round1_schema", round1.get("schema") == ROUND1["schema"],
               ROUND1["schema"], round1.get("schema"))
        for field, key in (("tickMs", "tickMs"), ("startMs", "startMs"),
                           ("endMs", "endMs"), ("frames", "frames")):
            _check(checks, f"round1_{field}", round1.get(field) == ROUND1[key],
                   ROUND1[key], round1.get(field))
        _check(checks, "round1_events", len(round1.get("events") or []) == ROUND1["events"],
               ROUND1["events"], len(round1.get("events") or []))
        _check(checks, "round1_utility", len(round1.get("utility") or []) == ROUND1["utility"],
               ROUND1["utility"], len(round1.get("utility") or []))


def assert_failed(meta: dict, job: dict, entry: dict, manifest: dict | None,
                  jobs: list, viewer_dir: Path, index: dict, checks: list) -> None:
    _check(checks, "job_error", job.get("status") == "error", "error", job.get("status"),
           f"stage={job.get('stage')}")
    _check(checks, "job_message", bool(job.get("error") or job.get("message")),
           "recorded failure", (job.get("error") or job.get("message") or "")[:160])
    _check(checks, "library_status", (entry or {}).get("status") == "failed",
           "failed", (entry or {}).get("status"))
    _check(checks, "library_rounds", (entry or {}).get("rounds") is None,
           None, (entry or {}).get("rounds"))
    _check(checks, "no_viewer_data", not (viewer_dir / "match.json").exists(),
           "absent", str(viewer_dir / "match.json"))
    _check(checks, "manifest_status", (manifest or {}).get("status") == "failed",
           "failed", (manifest or {}).get("status"))
    failure_text = " ".join(str((manifest or {}).get(k) or "")
                            for k in ("notes", "error", "densityReason"))
    _check(checks, "manifest_reason", bool(failure_text.strip()),
           "non-empty", failure_text.strip()[:160])
    _check(checks, "no_retry_loop", len(jobs) == 1, 1, len(jobs),
           f"statuses={[j.get('status') for j in jobs]}")
    if meta.get("indexRounds") is not None:
        _check(checks, "index_rounds", _index_round_count(index) == meta["indexRounds"],
               meta["indexRounds"], _index_round_count(index), index.get("error", ""))


# ------------------------------------------------------------------- run

def run(args) -> dict:
    repo = Path(args.repo).expanduser().resolve()
    home = Path(args.home).expanduser().resolve()
    replay = resolve_replay(repo, args.replay)
    legacy = args.server == "legacy"
    if legacy:
        base = f"http://127.0.0.1:{args.port}"
        if not _port_free(args.port):
            raise ImportError_(f"port {args.port} is already in use (test ports 18851-18859)")
    else:
        base = args.server.rstrip("/")
        if urlsplit(base).scheme != "http":
            raise ImportError_("--server must be 'legacy' or an http:// URL")
    token = args.token

    report = {
        "schema": SCHEMA,
        "created": _now(),
        "mode": "legacy" if legacy else "remote",
        "repo": str(repo),
        "home": str(home),
        "replay": {"file": replay["file"], "expectId": replay["id"],
                   "outcome": replay["outcome"]},
        "baseUrl": base,
        "checks": [],
        "guard": {"enabled": bool(args.allow_live_read)},
    }
    checks = report["checks"]
    proc = None
    junctions: list[Path] = []
    started = time.monotonic()
    try:
        if args.allow_live_read:
            report["guard"]["before"] = guard_mod.build_manifest(repo)
        if legacy:
            built = build_root(repo, home)
            junctions = [Path(p) for p in built["junctions"]]
            report["isolatedRoot"] = built
            vrf_home = home / "replays" / replay["file"]
            shutil.copy2(replay["path"], vrf_home)
        else:
            vrf_home = replay["path"]
        report["importName"] = str(vrf_home)

        if legacy:
            proc = start_legacy(home, args.port)[0]
            report["server"] = {"pid": proc.pid, "port": args.port,
                                "readySeconds": round(wait_ready(base, 120), 2)}
        else:
            report["server"] = {"pid": None, "baseUrl": base,
                                "readySeconds": round(wait_ready(base, 60, token), 2)}

        decoder = _http(base, "GET", "/api/decoder", token=token).get("data") or {}

        t0 = time.monotonic()
        posted = _http(base, "POST", "/api/replays/import-path",
                       body={"path": str(vrf_home)}, token=token, timeout=60)
        if posted["status"] not in (202, 200):
            raise ImportError_(f"import-path rejected: HTTP {posted['status']} "
                               f"{posted['bodyPreview'][:200]}")
        job_id = ((posted.get("data") or {}).get("jobId"))
        if not job_id:
            raise ImportError_(f"import-path returned no jobId: {posted['bodyPreview'][:200]}")
        report["jobId"] = job_id
        job, polled = poll_job(base, job_id, args.timeout, token)
        elapsed = time.monotonic() - t0
        report["timing"] = {"importSeconds": round(elapsed, 2), "pollSeconds": round(polled, 2)}
        report["job"] = job

        jobs = (_http(base, "GET", "/api/jobs", token=token).get("data") or {}).get("jobs") or []
        report["jobs"] = [{"id": j.get("id"), "status": j.get("status"),
                           "stage": j.get("stage"), "file": j.get("file")} for j in jobs]
        library = (_http(base, "GET", "/api/replays", token=token).get("data") or {}).get("replays") or []
        entry = next((row for row in library if row.get("id") == replay["id"]), None)
        report["libraryEntry"] = entry
        report["decoder"] = {k: decoder.get(k) for k in
                             ("status", "parserCommit", "buildState",
                              "buildChangedAssemblies", "registeredBranches")}

        rdir = home / "data/replays" / replay["id"]
        manifest_path = rdir / "manifest.json"
        manifest = None
        if manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        report["manifest"] = ({k: manifest.get(k) for k in ("status", "branch", "map",
                                                           "notes", "parserCommit")}
                              if manifest else None)

        if replay["outcome"] == "ready":
            match_resp = _http(base, "GET", f"/api/replays/{replay['id']}", token=token, timeout=60)
            match = match_resp.get("data")
            if not isinstance(match, dict):
                match = {}
            report["matchBytes"] = {"status": match_resp["status"],
                                    "rounds": len(match.get("rounds") or []),
                                    "players": len(match.get("players") or [])}
            report["utilityTotal"] = _utility_total(match.get("utilityCounts") or {})
            report["utilityStaleLive"] = replay.get("utilityStaleLive")
            round1 = _http(base, "GET", f"/api/replays/{replay['id']}/rounds/1",
                           token=token, timeout=60).get("data") or {}
            assert_ready(replay, match, job, decoder, round1, checks)
        else:
            index = index_rounds(home, vrf_home) if (home / "bot").is_dir() else {"ok": False,
                                                                                "error": "no isolated bot/"}
            report["index"] = index
            assert_failed(replay, job, entry or {}, manifest, jobs,
                          rdir / "viewer", index, checks)
    finally:
        if proc is not None:
            report["shutdown"] = stop_legacy(proc)
        if junctions:
            report["junctionCleanup"] = [_remove_junction(link) for link in junctions]
        if args.allow_live_read:
            after = guard_mod.build_manifest(repo)
            diff = guard_mod.compare(report["guard"].get("before") or {}, after)
            report["guard"].update({"after": after, "diff": diff, "ok": diff["ok"]})
            _check(checks, "guard_unchanged", diff["ok"], "no differences",
                   diff["summary"], str(diff["summary"]))

    failures = [check for check in checks if not check["ok"]]
    report["ok"] = not failures and report.get("guard", {}).get("ok", True)
    report["failures"] = failures
    report["totalSeconds"] = round(time.monotonic() - started, 2)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Isolated real-replay import QA (Q1)")
    parser.add_argument("--repo", required=True, help="main repo tree (read-only source)")
    parser.add_argument("--home", required=True, help="scratch app root (new or empty)")
    parser.add_argument("--server", default="legacy",
                        help="'legacy' or an already-running app URL (http://127.0.0.1:P)")
    parser.add_argument("--replay", default="bdb6b4ab",
                        help="bdb6b4ab | c428d9d5 | 2f7c79fb | file name | replay id")
    parser.add_argument("--token", help="app token for --server <url>")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"legacy isolated server port (default {DEFAULT_PORT}, range 18851-18859)")
    parser.add_argument("--timeout", type=float, default=900.0,
                        help="job timeout in seconds (default 900)")
    parser.add_argument("--allow-live-read", action="store_true",
                        help="snapshot the real repo before/after (read-only) to prove it unchanged")
    args = parser.parse_args(argv)
    try:
        report = run(args)
    except ImportError_ as exc:
        print(json.dumps({"schema": SCHEMA, "ok": False, "error": str(exc),
                          "created": _now()}, indent=2))
        return 2
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())

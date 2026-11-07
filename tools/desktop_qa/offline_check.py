#!/usr/bin/env python3
"""Offline proof helpers for the ValoHub desktop migration (lane Q1).

Two commands:

``scan --root R``
    Static scan of the desktop-reachable code for ``http(s)://`` / ``ws(s)://``
    literals (escaped JS regex forms such as ``https:\\/\\/host`` included).
    Loopback hosts are allowed.  Everything else must match an explicit
    allowlist entry that carries a one-line justification; the rest is a
    violation and the command exits 1.

``sample --pid P --duration S --interval 0.25 [--tree]``
    Polls ``netstat -ano`` (TCP + UDP) for ``P`` (and, with --tree, every
    descendant from a ``Win32_Process`` snapshot) and fails when any row has a
    non-loopback remote peer.  LISTENING rows and wildcard/unspecified remotes
    are ignored.  Distinct peers are recorded per PID.

``selftest``
    Positive control: a real loopback TCP connection must pass.  Negative
    control: a real (non-blocking) TCP connect to TEST-NET 192.0.2.1:9 must be
    observed as a non-loopback SYN_SENT peer, i.e. the sampler must fail it.

Stdlib only, Python 3.12.  One JSON report per run; non-zero exit on any
failed assertion.  Report schema: ``valohub.offline_check/v1``.
"""
from __future__ import annotations

import argparse
import fnmatch
import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

SCHEMA = "valohub.offline_check/v1"
LOOPBACK_NAMES = {"localhost", "localhost.localdomain", "::1", "0:0:0:0:0:0:0:1"}

# Explicit allowlist.  Every entry names the files it may appear in and why the
# literal cannot cause a remote request in the desktop build.  Keep this list
# short and reviewable; a new remote literal must be justified here.
ALLOWLIST = [
    {
        "host": "media.valorant-api.com",
        "files": ["bot/replay/normalize.py"],
        "justification": ("map-art URL written into normalized fixtures; the desktop app never fetches it "
                          "(CSP img-src 'self' data: blob: + Electron request filter deny)"),
    },
    {
        "host": "media.valorant-api.com",
        "files": ["bot/web/static/replays_app/*.js"],
        "justification": "map-art fallback regex in the UI; denied by Electron main's request filter and backend CSP",
    },
    {
        "host": "valorant-api.com",
        "files": ["bot/replay/normalize.py"],
        "justification": "provenance comment for the static map/agent tables (measured 2026-10-13); no fetch call in the normalizer",
    },
    {
        "host": "{host",
        "files": ["bot/replay/p2_lobby.py"],
        "justification": ("lobby share-link template: host comes from _advertise_host() (loopback default) "
                          "and p2_lobby is not mounted in the desktop app (PLAN D8: lobby off)"),
    },
    {
        "host": "api.henrikdev.xyz",
        "files": ["bot/replay/p2_identity.py"],
        "justification": ("legacy player-identity lookup; VALO_OFFLINE=1 returns available:false without a request "
                          "and /players/* is not mounted in the desktop app (PLAN D8)"),
    },
    {
        "host": "api.henrikdev.xyz",
        "files": ["bot/services/first_contact_analyzer.py"],
        "justification": "legacy service helper imported by p2_identity only; not reachable in offline mode",
    },
]

# http://, https://, ws://, wss:// and the JS-escaped https:\/\/ form.
URL_RE = re.compile(r"""(?i)(?:https?|wss?):(?:\\?/){2}[^\s"'`<>)\]}\[(]+""")

SCAN_GLOBS = [
    "bot/replay/*.py",
    "bot/web/desktop.py",
    "bot/sidecar.py",
    "bot/web/static/replays_app/*.js",
    "bot/web/static/replays_app/*.html",
    "bot/web/static/replays_app/*.css",
    "desktop/**/*.js",
    "desktop/**/*.mjs",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ----------------------------------------------------------------- scan

def _iter_scan_files(root: Path):
    seen = set()
    for pattern in SCAN_GLOBS:
        for path in sorted(root.glob(pattern)):
            if not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            if "node_modules/" in rel or rel.startswith("desktop/build/"):
                continue
            if pattern == "bot/replay/*.py" and path.name.startswith("test_"):
                continue
            if pattern == "bot/replay/*.py" and path.parent.name == "tools":
                continue
            if rel in seen:
                continue
            seen.add(rel)
            yield rel, path


def _unescape(url: str) -> str:
    return url.replace("\\/", "/").replace("\\.", ".")


def _host_of(url: str) -> str:
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        return ""
    return host.lower().rstrip(".")


def _is_loopback(host: str) -> bool:
    if not host:
        return False
    if host in LOOPBACK_NAMES or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _allowed(rel: str, host: str) -> dict | None:
    for entry in ALLOWLIST:
        if entry["host"] != host:
            continue
        if any(fnmatch.fnmatch(rel, pattern) for pattern in entry["files"]):
            return entry
    return None


def scan(root: Path) -> dict:
    root = root.resolve()
    findings = []
    scanned = []
    for rel, path in _iter_scan_files(root):
        scanned.append(rel)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:  # pragma: no cover - unreadable file
            findings.append({"file": rel, "line": 0, "url": "", "allowed": False,
                             "reason": f"unreadable: {exc}"})
            continue
        for number, line in enumerate(text.splitlines(), 1):
            for match in URL_RE.finditer(line):
                url = _unescape(match.group(0)).rstrip(".,;")
                host = _host_of(url)
                entry = _allowed(rel, host)
                if _is_loopback(host):
                    verdict, justification = "loopback", "loopback host"
                elif entry is not None:
                    verdict, justification = "allowlisted", entry["justification"]
                else:
                    verdict, justification = "violation", "remote literal without an allowlist entry"
                findings.append({
                    "file": rel,
                    "line": number,
                    "url": url[:200],
                    "host": host,
                    "verdict": verdict,
                    "justification": justification,
                })
    violations = [row for row in findings if row["verdict"] == "violation"]
    return {
        "schema": SCHEMA,
        "command": "scan",
        "created": _now(),
        "ok": not violations,
        "root": str(root),
        "scanned_files": scanned,
        "counts": {
            "files": len(scanned),
            "literals": len(findings),
            "loopback": sum(1 for r in findings if r["verdict"] == "loopback"),
            "allowlisted": sum(1 for r in findings if r["verdict"] == "allowlisted"),
            "violations": len(violations),
        },
        "findings": findings,
        "violations": violations,
        "missing_optional_targets": [
            rel for rel in ("bot/web/desktop.py", "bot/sidecar.py", "desktop")
            if not (root / rel).exists()
        ],
    }


# --------------------------------------------------------------- netstat

def netstat_text() -> str:
    result = subprocess.run(
        ["netstat", "-ano"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    return result.stdout


def _split_addr(value: str) -> tuple[str, str]:
    value = value.strip()
    if value.startswith("["):  # [::1]:18851
        host, _, rest = value[1:].partition("]")
        return host, rest.lstrip(":")
    host, _, port = value.rpartition(":")
    return host, port


def parse_netstat(text: str) -> list[dict]:
    rows = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        proto = parts[0].upper()
        if proto not in ("TCP", "UDP"):
            continue
        local, foreign = parts[1], parts[2]
        state = ""
        pid_token = parts[-1]
        if proto == "TCP":
            if len(parts) < 5:
                continue
            state = parts[3].upper()
        elif not pid_token.isdigit():
            continue
        if not pid_token.isdigit():
            continue
        rows.append({
            "proto": proto,
            "local": local,
            "foreign": foreign,
            "state": state,
            "pid": int(pid_token),
        })
    return rows


def _wildcard(host: str) -> bool:
    return host in {"", "*", "0.0.0.0", "::", "0:0:0:0:0:0:0:0"}


def _relevant(row: dict, targets: set[int]) -> bool:
    if row["pid"] not in targets:
        return False
    if row["state"] == "LISTENING":
        return False
    host, port = _split_addr(row["foreign"])
    if _wildcard(host) or port in {"", "0", "*"}:
        return False
    return True


def process_tree(pid: int) -> set[int]:
    """Descendants of ``pid`` via one Win32_Process snapshot (PowerShell)."""
    script = (
        "param([int]$Root)\n"
        "$procs = Get-CimInstance Win32_Process | Select-Object ProcessId, ParentProcessId\n"
        "$ids = New-Object 'System.Collections.Generic.HashSet[int]'\n"
        "[void]$ids.Add($Root)\n"
        "$changed = $true\n"
        "while ($changed) {\n"
        "  $changed = $false\n"
        "  foreach ($p in $procs) {\n"
        "    if ($ids.Contains([int]$p.ParentProcessId) -and -not $ids.Contains([int]$p.ProcessId)) {\n"
        "      [void]$ids.Add([int]$p.ProcessId); $changed = $true\n"
        "    }\n"
        "  }\n"
        "}\n"
        "$ids | ConvertTo-Json -Compress\n"
    )
    workdir = Path(tempfile.mkdtemp(prefix="vrb-ps-"))
    script_path = workdir / "tree.ps1"
    script_path.write_text(script, encoding="utf-8")
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", str(script_path), "-Root", str(pid)],
            capture_output=True, text=True, timeout=60,
        )
    finally:
        try:
            script_path.unlink()
            workdir.rmdir()
        except OSError:
            pass
    if result.returncode != 0:
        raise RuntimeError(f"process tree lookup failed ({result.returncode}): {result.stderr.strip()[:300]}")
    text = (result.stdout or "").strip()
    if not text:
        return {pid}
    data = json.loads(text)
    if isinstance(data, int):
        data = [data]
    return {int(value) for value in data} | {pid}


def _sample_round(targets_provider) -> dict:
    text = netstat_text()
    rows = parse_netstat(text)
    targets = targets_provider()
    peers = {}
    for row in rows:
        if not _relevant(row, targets):
            continue
        host, port = _split_addr(row["foreign"])
        key = f"{row['pid']}/{row['proto']}/{host}:{port}"
        entry = peers.setdefault(key, {
            "pid": row["pid"],
            "proto": row["proto"],
            "remoteHost": host,
            "remotePort": port,
            "states": set(),
            "observations": 0,
        })
        entry["states"].add(row["state"] or "STATELESS")
        entry["observations"] += 1
    return peers


def sample(pid: int, duration: float, interval: float, tree: bool) -> dict:
    started = time.monotonic()
    deadline = started + max(duration, interval)
    seen: dict[str, dict] = {}
    pids: set[int] = set()
    samples = 0
    try:
        while True:
            if tree:
                pids = process_tree(pid)
            else:
                pids = {pid}
            round_peers = _sample_round(lambda: pids)
            for key, entry in round_peers.items():
                record = seen.setdefault(key, {**entry, "firstSeen": time.monotonic()})
                record["observations"] = record.get("observations", 0) + entry["observations"]
                record["states"] |= entry["states"]
                record["lastSeen"] = time.monotonic()
            samples += 1
            if time.monotonic() >= deadline:
                break
            time.sleep(interval)
    except RuntimeError as exc:
        return {"ok": False, "error": str(exc), "pid": pid, "samples": samples}

    peers = []
    violations = []
    for key in sorted(seen):
        entry = seen[key]
        record = {
            "key": key,
            "pid": entry["pid"],
            "proto": entry["proto"],
            "remote": f"{entry['remoteHost']}:{entry['remotePort']}",
            "states": sorted(entry["states"]),
            "observations": entry["observations"],
            "loopback": _is_loopback(entry["remoteHost"]),
        }
        peers.append(record)
        if not record["loopback"]:
            violations.append(record)
    return {
        "schema": SCHEMA,
        "command": "sample",
        "created": _now(),
        "ok": not violations,
        "pid": pid,
        "tree": tree,
        "durationS": duration,
        "intervalS": interval,
        "samples": samples,
        "targetPids": sorted(pids),
        "peers": peers,
        "violations": violations,
        "loopbackOnly": not violations,
    }


# --------------------------------------------------------------- selftest

def _loopback_pair() -> tuple[socket.socket, socket.socket]:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    accepted: list[socket.socket] = []
    thread = threading.Thread(target=lambda: accepted.append(server.accept()[0]), daemon=True)
    thread.start()
    client = socket.create_connection(server.getsockname(), timeout=5)
    thread.join(timeout=5)
    return server, client


def selftest(duration: float = 2.0, interval: float = 0.25) -> dict:
    pid = os.getpid()
    positive = {}
    server = client = None
    try:
        server, client = _loopback_pair()
        positive = sample(pid, 1.0, interval, tree=False)
    finally:
        for sock in (client, server):
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass

    negative = {}
    attempts = []
    sockets = []
    try:
        for ip in ("192.0.2.1", "198.51.100.1", "203.0.113.1"):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setblocking(False)
            try:
                code = sock.connect_ex((ip, 9))
            except OSError as exc:  # pragma: no cover - platform dependent
                code = getattr(exc, "errno", -1) or -1
            attempts.append({"ip": ip, "connect_ex": code})
            sockets.append(sock)
            negative = sample(pid, duration, interval, tree=False)
            if negative.get("violations"):
                attempts[-1]["detected"] = True
                break
            attempts[-1]["detected"] = False
            sockets.pop().close()
    finally:
        for sock in sockets:
            try:
                sock.close()
            except OSError:
                pass

    positive_ok = bool(positive.get("ok")) and bool(positive.get("peers"))
    negative_ok = bool(negative.get("violations"))
    return {
        "schema": SCHEMA,
        "command": "selftest",
        "created": _now(),
        "ok": positive_ok and negative_ok,
        "positive_control": {
            "ok": positive_ok,
            "detail": "real loopback TCP connection must be the only peer",
            "report": {k: positive.get(k) for k in ("ok", "peers", "violations", "samples", "error")},
        },
        "negative_control": {
            "ok": negative_ok,
            "detail": "TEST-NET connect must appear as a non-loopback SYN_SENT peer and fail the sampler",
            "attempts": attempts,
            "report": {k: negative.get(k) for k in ("ok", "peers", "violations", "samples")},
        },
    }


# ------------------------------------------------------------------ cli

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Offline scan + connection sampler (ValoHub desktop QA)")
    sub = parser.add_subparsers(dest="command", required=True)

    scan_p = sub.add_parser("scan", help="static scan for remote URL literals")
    scan_p.add_argument("--root", required=True)

    sample_p = sub.add_parser("sample", help="poll netstat for a pid (and descendants)")
    sample_p.add_argument("--pid", type=int, required=True)
    sample_p.add_argument("--duration", type=float, default=30.0)
    sample_p.add_argument("--interval", type=float, default=0.25)
    sample_p.add_argument("--tree", action="store_true")

    test_p = sub.add_parser("selftest", help="loopback positive + TEST-NET negative control")
    test_p.add_argument("--duration", type=float, default=2.0)
    test_p.add_argument("--interval", type=float, default=0.25)

    args = parser.parse_args(argv)

    if args.command == "scan":
        report = scan(Path(args.root))
    elif args.command == "sample":
        if args.duration <= 0 or args.interval <= 0:
            report = {"schema": SCHEMA, "ok": False, "error": "duration and interval must be > 0"}
        else:
            report = sample(args.pid, args.duration, args.interval, args.tree)
    else:
        report = selftest(args.duration, args.interval)

    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())

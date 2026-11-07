"""Safely update the vendored replay parser and guard decode density.

The active parser is never modified until a candidate checkout has passed all of
these gates: upstream sync (pinned commit when ``VALOPLANT_WORK/parser-pin.txt`` or ``--pin`` is
set), overlay ``git apply --check``, Release build, and a 13.06 replay export compared with the
stored per-round baseline that must come back ``validated`` (an ``unvalidated`` self-test, e.g. a
missing baseline, also blocks publication). A conflict, build failure, or >10x count drop leaves
the previous vendored tree/build live. On publish the build identity (assembly hashes and the
tested replay matrix) is recorded in ``parser-commit.txt``; a later run is a no-op only while the
decoder on disk still matches that record.

Vendor tests are non-destructive
--------------------------------
Never run the vendor suite against the live tree with its default output paths::

    dotnet10-sdk/dotnet.exe test vendorValorantReplayParser/ValorantReplayParser.sln -c Release

That rebuilds ``src/CliReader/bin/Release/net10.0/*.dll`` **in place** -- the exact directory
``pipeline.find_decoder()`` loads the live decoder from. The .NET build is not byte-reproducible
(fresh MVID/PE timestamp per build), so the new DLLs differ from the ones recorded at publish:
``parser-commit.txt`` silently goes stale and ``store.build_state()`` flips to ``modified``
even though no source changed. That is a provenance break, and there is no way to recover the
recorded bytes afterwards. Always redirect the test build to a scratch tree instead::

    dotnet10-sdk/dotnet.exe test vendorValorantReplayParser/ValorantReplayParser.sln \
        -c Release --artifacts-path VALOPLANT_WORK/tmp/<lane>/testout

``--artifacts-path`` relocates every project's ``bin``/``obj`` under one scratch root and leaves
the live binaries untouched (verified: all 45 live ``.dll``/``.exe`` hashes identical before and
after a full 3,285-test run). Do *not* use ``-p:BaseOutputPath=``/``-p:BaseIntermediateOutputPath=``
for this: a single shared ``obj`` across the solution's 13 projects makes MSBuild fail with
``MSB4006: circular dependency ... ResolveProjectReferences``. Testing a *candidate* tree
(``_prepare_candidate``) is always safe -- it has its own ``bin``/``obj``.

P5-B pipeline integration point
-------------------------------
After CliReader writes a raw viewer export, call::

    verdict = assess_decode_density(raw_dir, branch=manifest.get("replay_build"))

``verdict["state"]`` is ``validated`` / ``suspect`` / ``unvalidated`` (``verdict["suspect"]`` is
kept for older callers). Persist ``suspect`` as manifest status ``suspect`` with
``verdict["reason"]``; record ``unvalidated`` with its reason but never as ``validated``. The
function is pure with respect to the replay store and is safe to call from the backend.

Baseline format and fail-closed rules
-------------------------------------
A baseline is ``{"thresholdRatio": <number>, "profiles": {<family>: {...}}}``. ``thresholdRatio``
must be a real number (``int``/``float``, never ``bool`` or a string) that is finite and inside
the documented permitted interval ``(0, 1]`` — it is the fraction of the recorded baseline count
or movement density a decode must still reach (default ``0.10``, i.e. a >10x loss). A missing,
non-finite, zero/negative, out-of-range or wrong-typed threshold makes the whole baseline
malformed, so the verdict is ``unvalidated`` and publication is refused rather than comparing
rows against a meaningless floor. The same applies to malformed profile counters/denominators
(``roundCount``, ``eventRows``, ``movementRows``, per-round ``n`` / ``movementSamples`` /
``eventCount`` / ``movementPerSecond``). JSON integers are unbounded, but every one of those
values is compared against a float threshold, so a value that ``float()`` cannot represent (e.g.
``10 ** 1000``) is malformed too: ``unvalidated``, never an ``OverflowError``. The same rule holds
for a baseline dict passed straight to ``assess_decode_density(baseline=...)``: a value too wide
for ``str()``/``repr()`` (``sys.get_int_max_str_digits()``, e.g. ``10 ** 5000``) must still yield a
bounded diagnostic reason rather than raise.
"""

from __future__ import annotations

import argparse
import bisect
import gzip
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Callable, Iterable

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot.replay import pipeline, store  # noqa: E402

WORK = ROOT / "VALOPLANT_WORK"
UPSTREAM = WORK / "tmp" / "upstream"
UPDATE_ROOT = WORK / "pu"  # intentionally short: Windows path length applies to deep C# trees
DOTNET = Path(os.environ.get("VALO_DOTNET") or ROOT / "dotnet10-sdk" / "dotnet.exe")
VENDOR = ROOT / "vendorValorantReplayParser"
CSPROJ_REL = Path("src") / "CliReader" / "CliReader.csproj"
DLL_REL = Path("src") / "CliReader" / "bin" / "Release" / "net10.0" / "CliReader.dll"
COMMIT_FILE = WORK / "parser-commit.txt"
OVERLAY_PATCH = WORK / "parser-overlay.patch"
BASELINE_FILE = WORK / "parser-baseline.json"
BASELINE_RAW = ROOT / "data" / "replays" / "bad1543033d11396" / "raw"
SELFTEST_REPLAY = ROOT / "replays" / "bdb6b4ab-fa31-4e51-b77a-35e3a1e7a3c2.vrf"
DEFAULT_REQUEUE_API = "http://127.0.0.1:18793"
# Density baseline: the decoded round must still reach this fraction of the recorded
# baseline counter/density (0.10 == tolerate up to a 10x loss). Only the open-closed
# interval (0, 1] is a meaningful threshold; anything else is a malformed baseline.
#
# A baseline only proves something when every recorded round carries a *positive*
# denominator for each compared counter (``movementSamples`` and ``eventCount``): a round
# with an absent or zero counter compares nothing (the check skips it), so a profile made of
# such rounds would "validate" a near-empty decode. Such a profile is unvalidated, and
# ``create_baseline`` refuses a zero-density export instead of writing one.
DEFAULT_THRESHOLD_RATIO = 0.10

_TIME_RE = re.compile(br'"time_ms"\s*:\s*(\d+)')
_OUTCOME_RE = re.compile(br'"type"\s*:\s*"round_outcome"')
_PATCH_RE = re.compile(r"release-(\d+)\.(\d+)")


class UpdateError(RuntimeError):
    """A guarded update failed before it was safe to publish."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _command_text(argv: Iterable[object]) -> str:
    return subprocess.list2cmdline([str(part) for part in argv])


def _run(argv: list[object], *, cwd: Path = ROOT, env: dict[str, str] | None = None,
         capture: bool = False) -> str:
    print(f"+ {_command_text(argv)}", flush=True)
    completed = subprocess.run(
        [str(part) for part in argv], cwd=cwd, env=env, check=True, text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
    )
    return completed.stdout.strip() if capture and completed.stdout else ""


def _require(path: Path, description: str) -> None:
    if not path.exists():
        raise UpdateError(f"missing {description}: {path}")


def _sha256(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UpdateError(f"cannot read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise UpdateError(f"expected a JSON object: {path}")
    return value


def _write_json_atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                         encoding="utf-8")
    os.replace(temporary, path)


def _open_lines(path: Path):
    return gzip.open(path, "rb") if path.suffix == ".gz" else path.open("rb")


def _ndjson_path(raw_dir: Path, stem: str) -> Path | None:
    for name in (f"{stem}.ndjson", f"{stem}.ndjson.gz"):
        candidate = raw_dir / name
        if candidate.is_file():
            return candidate
    return None


def patch_family(branch: object) -> str | None:
    """Return a stable family key such as ``release-13.xx``."""
    if not isinstance(branch, str):
        return None
    match = _PATCH_RE.search(branch)
    return f"release-{match.group(1)}.xx" if match else None


def _time_ms(line: bytes) -> int | None:
    match = _TIME_RE.search(line)
    return int(match.group(1)) if match else None


def _bucket(times: Iterable[int], ends: list[int]) -> list[int]:
    counts = [0] * len(ends)
    for value in times:
        index = bisect.bisect_left(ends, value)
        if index < len(counts):
            counts[index] += 1
    return counts


def profile_export(raw_dir: Path) -> dict:
    """Measure per-round movement/event rows from a CliReader viewer export."""
    raw_dir = Path(raw_dir)
    manifest = _read_json(raw_dir / "manifest.json")
    events_path = _ndjson_path(raw_dir, "events")
    movement_path = _ndjson_path(raw_dir, "movement")
    if events_path is None or movement_path is None:
        missing = []
        if events_path is None:
            missing.append("events.ndjson(.gz)")
        if movement_path is None:
            missing.append("movement.ndjson(.gz)")
        return {"branch": manifest.get("replay_build"),
                "patchFamily": patch_family(manifest.get("replay_build")),
                "sourceSha256": manifest.get("source_sha256"), "rounds": [],
                "eventRows": 0, "movementRows": 0,
                "error": "missing " + " and ".join(missing)}

    outcomes: dict[int, int] = {}
    event_times: list[int] = []
    event_rows = 0
    with _open_lines(events_path) as stream:
        for line in stream:
            event_rows += 1
            value = _time_ms(line)
            if value is not None:
                event_times.append(value)
            if _OUTCOME_RE.search(line):
                try:
                    row = json.loads(line)
                    number = int(row.get("round_number"))
                    outcome_time = int(row.get("time_ms"))
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                if number > 0:
                    outcomes[number] = outcome_time

    ordered = sorted(outcomes.items())
    if not ordered or [number for number, _ in ordered] != list(range(1, len(ordered) + 1)):
        return {"branch": manifest.get("replay_build"),
                "patchFamily": patch_family(manifest.get("replay_build")),
                "sourceSha256": manifest.get("source_sha256"), "rounds": [],
                "eventRows": event_rows, "movementRows": 0,
                "error": "round_outcome rows are absent or non-contiguous"}

    ends = [value for _, value in ordered]
    event_counts = _bucket(event_times, ends)
    movement_counts = [0] * len(ends)
    movement_rows = 0
    with _open_lines(movement_path) as stream:
        for line in stream:
            movement_rows += 1
            value = _time_ms(line)
            if value is None:
                continue
            index = bisect.bisect_left(ends, value)
            if index < len(movement_counts):
                movement_counts[index] += 1

    rounds: list[dict] = []
    previous_end = -1
    for index, (number, end_ms) in enumerate(ordered):
        start_ms = previous_end + 1
        duration_ms = max(1, end_ms - start_ms + 1)
        movement = movement_counts[index]
        events = event_counts[index]
        rounds.append({"n": number, "startMs": start_ms, "endMs": end_ms,
                       "durationMs": duration_ms, "movementSamples": movement,
                       "eventCount": events,
                       "movementPerSecond": movement * 1000.0 / duration_ms,
                       "eventsPerSecond": events * 1000.0 / duration_ms})
        previous_end = end_ms

    return {"branch": manifest.get("replay_build"),
            "patchFamily": patch_family(manifest.get("replay_build")),
            "sourceSha256": manifest.get("source_sha256"),
            "parseStatus": manifest.get("parse_status"), "rounds": rounds,
            "eventRows": event_rows, "movementRows": movement_rows, "error": None}


def create_baseline(raw_dir: Path = BASELINE_RAW, output: Path = BASELINE_FILE) -> dict:
    """Create the checked density baseline from the current known-good export.

    Refuses to write a baseline that could never validate anything: an export that records no
    movement samples or no events (zero-density) would produce rounds with no positive
    denominator, so the resulting baseline could only rubber-stamp a broken decode.
    """
    profile = profile_export(raw_dir)
    family = profile.get("patchFamily")
    if profile.get("error") or not family or not profile.get("rounds"):
        raise UpdateError(f"cannot create parser baseline: {profile.get('error') or 'missing patch family'}")
    for row in profile["rounds"]:
        for key, label in (("movementSamples", "movement samples"), ("eventCount", "events")):
            count = _baseline_int(row.get(key))
            if not count:
                raise UpdateError(f"cannot create parser baseline: round {row.get('n')} recorded no "
                                  f"{label} ({row.get(key)!r}); a zero-density baseline cannot validate")
    for key, label in (("eventRows", "event rows"), ("movementRows", "movement rows")):
        if not _baseline_int(profile.get(key)):
            raise UpdateError(f"cannot create parser baseline: the export recorded no {label}; "
                              f"a zero-density baseline cannot validate")
    baseline = {"schema": "valoplant_parser_baseline/v1", "generatedAt": _utc_now(),
                "thresholdRatio": DEFAULT_THRESHOLD_RATIO,
                "profiles": {family: {"sourceSha256": profile.get("sourceSha256"),
                                       "branch": profile.get("branch"),
                                       "roundCount": len(profile["rounds"]),
                                       "eventRows": profile["eventRows"],
                                       "movementRows": profile["movementRows"],
                                       "rounds": profile["rounds"]}}}
    _write_json_atomic(output, baseline)
    return baseline


def _verdict(state: str, reason: str | None, *, family, profile, basis: str | None = None) -> dict:
    """Uniform quality verdict.

    ``state``: ``validated`` (a baseline covered this export and the heuristic passed),
    ``suspect`` (the heuristic failed or the export is structurally broken) or ``unvalidated``
    (no usable baseline covered it, so nothing was checked). ``suspect`` is kept as a boolean for
    existing callers. ``semanticEquivalence`` is always False: this is a catastrophic-loss density
    heuristic, never a same-input semantic comparison (that is a release-acceptance step).
    """
    return {"state": state, "suspect": state == "suspect", "reason": reason, "basis": basis,
            "semanticEquivalence": False, "patchFamily": family, "profile": profile}


_MAX_FLOAT_INT = int(sys.float_info.max)


def _as_float(value: int | float) -> float | None:
    """``float(value)``, or None when the number cannot be represented as a float.

    JSON accepts integers of arbitrary size, so ``10 ** 1000`` parses cleanly and then overflows
    ``float()``. Every baseline number is ultimately compared against a float threshold, so a
    value that cannot be represented is *malformed*, not an error to raise: the callers fail
    closed (``unvalidated``) instead of crashing the density check.
    """
    try:
        return float(value)
    except (OverflowError, ValueError):
        return None


def _too_wide(value: object) -> bool:
    """True when an integer baseline value cannot be compared against a float threshold."""
    return isinstance(value, int) and not isinstance(value, bool) and value > _MAX_FLOAT_INT


def _short(value: object, limit: int = 48) -> str:
    """Compact ``repr`` for verdict reasons: an unbounded value must not bloat a manifest.

    ``repr()`` of an integer wider than ``sys.get_int_max_str_digits()`` raises ``ValueError``, so
    an in-memory ``10 ** 5000`` baseline value cannot be formatted at all. A malformed baseline
    must fail closed, never crash, so such a value is described by its bit length instead.
    """
    try:
        text = repr(value)
    except ValueError:  # integer too wide for str(): describe it without formatting it
        bits = value.bit_length() if isinstance(value, int) else 0
        return f"<{type(value).__name__} of {bits} bits>"
    return text if len(text) <= limit else f"{text[:limit]}... ({len(text)} chars)"


def _baseline_ratio(value: object) -> float | None:
    """``thresholdRatio`` as a finite float in ``(0, 1]``; None when malformed.

    ``bool`` is rejected explicitly (``True`` is an ``int`` in Python), and strings — including
    the JSON-compatible ``"NaN"``/``"Infinity"`` spellings — are never coerced. An integer too
    large for ``float()`` (JSON allows unbounded integers) is malformed, never a crash.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    ratio = _as_float(value)
    if ratio is None or not math.isfinite(ratio) or not (0.0 < ratio <= 1.0):
        return None
    return ratio


def _baseline_int(value: object) -> int | None:
    """Non-negative integral baseline counter a float threshold can be applied to, else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 <= value <= _MAX_FLOAT_INT else None
    if isinstance(value, float) and math.isfinite(value) and value >= 0 and value.is_integer():
        return int(value)
    return None


def _baseline_number(value: object) -> float | None:
    """Finite non-negative baseline measurement, or None when malformed/not representable."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = _as_float(value)
    return number if number is not None and math.isfinite(number) and number >= 0 else None


def _baseline_problem(expected: dict) -> str | None:
    """Reason when a baseline profile cannot validate anything, else None.

    Type checks: only keys that are present are type-checked, so a hand-written baseline without
    optional measurements keeps working; anything that *is* used by the comparison must be a real,
    non-negative number.

    Denominator check (fail closed): every recorded round must carry a **positive**
    ``movementSamples`` and ``eventCount``. The comparison skips a round whose baseline counter is
    zero or absent, so a profile of such rounds checks nothing and must never be ``validated``.

    Magnitude check (fail closed): JSON integers are unbounded, but every counter and measurement
    here is compared against a float threshold. A value too large for ``float()`` would raise
    ``OverflowError`` mid-check, so it is reported as malformed instead.
    """
    rows = expected.get("rounds")
    if not isinstance(rows, list):
        return "rounds is not a list"
    if "roundCount" in expected:
        raw = expected.get("roundCount")
        if _too_wide(raw):
            return "roundCount is too large to compare"
        count = _baseline_int(raw)
        if count is None:
            return "roundCount is not a non-negative integer"
        if rows and count != len(rows):
            return f"roundCount {count} != {len(rows)} recorded rounds"
    for key in ("eventRows", "movementRows"):
        if key in expected:
            raw = expected.get(key)
            if _too_wide(raw):
                return f"{key} is too large to compare"
            value = _baseline_int(raw)
            if value is None:
                return f"{key} is not a non-negative integer"
            if value <= 0:
                return f"{key} is {value}; a baseline with no rows cannot validate a decode"
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            return f"round row {index} is not an object"
        if "n" in row:
            number = _baseline_int(row.get("n"))
            if not number:
                return f"round row {index} has an invalid round number"
        for key in ("movementSamples", "eventCount"):
            if key not in row:
                return f"round {index} has no {key} to compare against"
            raw = row.get(key)
            if _too_wide(raw):
                return f"round {index} {key} is too large to compare"
            number = _baseline_int(raw)
            if number is None:
                return f"round {index} {key} is not a non-negative integer"
            if number <= 0:
                return f"round {index} {key} is {number}; no positive denominator to compare against"
        if "movementPerSecond" in row:
            raw = row.get("movementPerSecond")
            if _too_wide(raw):
                return f"round {index} movementPerSecond is too large to compare"
            if _baseline_number(raw) is None:
                return f"round {index} movementPerSecond is not finite and non-negative"
    return None


def assess_decode_density(raw_dir: Path, *, branch: str | None = None,
                          baseline_path: Path | None = None,
                          baseline: dict | None = None) -> dict:
    """Return a backend-ready quality verdict for one raw decode (see ``_verdict``)."""
    current = profile_export(raw_dir)
    family = patch_family(branch) or current.get("patchFamily")
    if current.get("error"):
        return _verdict("suspect", current["error"], family=family, profile=current)
    if baseline is None:
        try:
            baseline = _read_json(Path(baseline_path if baseline_path is not None else BASELINE_FILE))
        except UpdateError as exc:
            return _verdict("unvalidated", f"density baseline unavailable: {exc}",
                            family=family, profile=current)
    profiles = baseline.get("profiles") if isinstance(baseline, dict) else None
    expected = profiles.get(family) if isinstance(profiles, dict) and family else None
    if not isinstance(expected, dict):
        return _verdict("unvalidated", f"no density baseline for {family or 'unknown patch family'}",
                        family=family, profile=current)

    # A malformed baseline can never authorize publication: refuse before comparing any row.
    threshold = _baseline_ratio(baseline.get("thresholdRatio", DEFAULT_THRESHOLD_RATIO))
    if threshold is None:
        return _verdict("unvalidated",
                        f"malformed density baseline: thresholdRatio "
                        f"{_short(baseline.get('thresholdRatio', DEFAULT_THRESHOLD_RATIO))} is not a "
                        f"finite number in (0, 1]",
                        family=family, profile=current)
    problem = _baseline_problem(expected)
    if problem:
        return _verdict("unvalidated", f"malformed density baseline for {family}: {problem}",
                        family=family, profile=current)
    expected_rounds = expected.get("rounds") if isinstance(expected.get("rounds"), list) else []
    current_rounds = current.get("rounds") if isinstance(current.get("rounds"), list) else []
    if not current_rounds:
        return _verdict("suspect", "no completed round density rows", family=family, profile=current)
    if not expected_rounds:
        return _verdict("unvalidated", f"baseline {family} has no rounds",
                        family=family, profile=current)

    exact_source = bool(current.get("sourceSha256") and
                        current.get("sourceSha256") == expected.get("sourceSha256"))
    failures: list[str] = []
    if exact_source:
        basis = "exact_source_per_round"
        if len(current_rounds) != len(expected_rounds):
            failures.append(f"round count {len(current_rounds)} != baseline {len(expected_rounds)}")
        for actual, wanted in zip(current_rounds, expected_rounds):
            for key, label in (("movementSamples", "movement"), ("eventCount", "events")):
                base_value = int(wanted.get(key, 0) or 0)
                actual_value = int(actual.get(key, 0) or 0)
                if base_value > 0 and actual_value < base_value * threshold:
                    failures.append(f"round {actual.get('n')} {label} {actual_value} < "
                                    f"{threshold:.0%} of baseline {base_value}")
    else:
        basis = "family_density_floor"
        densities = [float(item.get("movementPerSecond", 0) or 0)
                     for item in expected_rounds
                     if float(item.get("movementPerSecond", 0) or 0) > 0]
        if not densities:
            return _verdict("unvalidated", f"baseline {family} has no movement density",
                            family=family, profile=current)
        floor = median(densities) * threshold
        for actual in current_rounds:
            density = float(actual.get("movementPerSecond", 0) or 0)
            if density < floor:
                failures.append(f"round {actual.get('n')} movement density {density:.3f}/s < "
                                f"{threshold:.0%} of {family} median ({floor:.3f}/s)")
    if failures:
        return _verdict("suspect", "; ".join(failures[:8]), family=family, profile=current, basis=basis)
    return _verdict("validated", None, family=family, profile=current, basis=basis)


def _require_validated(verdict: dict) -> None:
    """Publication gate: only a ``validated`` self-test may replace the live parser."""
    state = verdict.get("state") or ("suspect" if verdict.get("suspect") else "unvalidated")
    if state == "validated":
        return
    raise UpdateError(f"candidate status {state}; active build kept: {verdict.get('reason')}")


PIN_FILE = WORK / "parser-pin.txt"
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def read_pin() -> str | None:
    """Pinned upstream commit from ``parser-pin.txt`` (first non-comment line), or None."""
    try:
        text = PIN_FILE.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        value = line.strip()
        if value and not value.startswith("#"):
            if not _SHA_RE.match(value):
                raise UpdateError(f"invalid upstream pin in {PIN_FILE}: {value!r} (want a 40-hex commit)")
            return value
    return None


def _git_try(argv: list[object]) -> tuple[int, str]:
    """Run one git command without raising; ``(returncode, combined output)``."""
    completed = subprocess.run([str(part) for part in ["git", "-C", UPSTREAM, *argv]], cwd=ROOT,
                               text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return completed.returncode, (completed.stdout or "").strip()


def _current_branch() -> str | None:
    """Local branch name when HEAD is attached; None for a detached checkout."""
    code, name = _git_try(["symbolic-ref", "--quiet", "--short", "HEAD"])
    return name if code == 0 and name else None


def _tracking_branch() -> str:
    """The branch an unpinned update should follow, resolved fail-closed.

    The attached branch wins (that is the explicit intended branch). A detached checkout — the
    normal state after a pinned update — follows the remote's default branch (``origin/HEAD``),
    falling back to ``main``/``master`` when the remote does not advertise one.
    """
    branch = _current_branch()
    if branch:
        return branch
    code, remote_head = _git_try(["symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"])
    if code == 0 and remote_head.startswith("origin/"):
        return remote_head[len("origin/"):]
    for candidate in ("main", "master"):
        code, _ = _git_try(["rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{candidate}"])
        if code == 0:
            return candidate
    raise UpdateError("upstream checkout is detached and has no origin/HEAD (or origin/main, "
                      "origin/master) to follow; check out the intended branch and retry")


def _require_clean_checkout() -> None:
    """Refuse to move a checkout that holds **tracked** local changes (never overwrite them).

    Untracked files are deliberately ignored: a working parser checkout permanently carries build
    artifacts (``VBCSCompiler/…`` and friends) that are not gitignored, and they say nothing about
    whether a branch switch is safe. Git still refuses a checkout/merge that would overwrite an
    untracked path (see ``_fast_forward_to``), so the collision case stays fail-closed.
    """
    code, status = _git_try(["status", "--porcelain", "--untracked-files=no"])
    if code != 0:
        raise UpdateError(f"cannot inspect the upstream checkout: {status}")
    if status:
        first = "; ".join(status.splitlines()[:5])
        raise UpdateError(f"upstream checkout has local changes; refusing to switch branches: {first}")


def _fast_forward_to(branch: str) -> None:
    """Select ``branch`` explicitly, then fast-forward it to ``origin/<branch>``."""
    remote = f"origin/{branch}"
    code, _ = _git_try(["rev-parse", "--verify", "--quiet", f"refs/remotes/{remote}"])
    if code != 0:
        raise UpdateError(f"upstream checkout has no {remote} after fetching origin")
    if _current_branch() != branch:
        code, _ = _git_try(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"])
        try:
            if code == 0:
                _run(["git", "-C", UPSTREAM, "checkout", "--quiet", branch])
            else:
                _run(["git", "-C", UPSTREAM, "checkout", "--quiet", "--track", "-b", branch, remote])
        except subprocess.CalledProcessError as exc:
            # Git refuses to overwrite an untracked file; leave it exactly where it is.
            raise UpdateError(f"cannot select upstream branch {branch!r} without overwriting local "
                              f"files; the checkout was left untouched: {exc.output or exc}") from exc
        if _current_branch() != branch:
            raise UpdateError(f"could not select upstream branch {branch!r}")
    code, output = _git_try(["merge", "--ff-only", "--quiet", remote])
    if code != 0:
        raise UpdateError(f"cannot fast-forward {branch} to {remote} (diverged, local commits, or "
                          f"an untracked file in the way); the checkout was left untouched: {output}")


def _sync_upstream_ref(pin: str | None, *, pull: bool) -> str:
    """Move the upstream checkout to the pinned commit, or fast-forward when unpinned.

    A pin never chases newer main: it fetches (so the object exists) and checks out the exact
    commit, then verifies HEAD equals the pin and fails closed otherwise. An unpinned update must
    not assume HEAD is on a branch: after a pinned run the checkout is detached, so the intended
    tracking branch is resolved and selected explicitly before the fast-forward. Tracked local
    changes, a diverged branch, or an untracked file that the switch would overwrite each fail
    closed instead of being overwritten; plain untracked build artifacts do not block the switch.
    """
    if pin:
        if pull:
            _run(["git", "-C", UPSTREAM, "fetch", "--quiet", "origin"])
        _run(["git", "-C", UPSTREAM, "checkout", "--quiet", "--detach", pin])
        head = _git_head()
        if head != pin:
            raise UpdateError(f"upstream pin {pin} not checked out (HEAD is {head})")
        return head
    if pull:
        _run(["git", "-C", UPSTREAM, "fetch", "--quiet", "origin"])
        _require_clean_checkout()
        _fast_forward_to(_tracking_branch())
    return _git_head()


def _git_head() -> str:
    return _run(["git", "-C", UPSTREAM, "rev-parse", "HEAD"], capture=True)


def _tracked_upstream() -> list[Path]:
    raw = subprocess.check_output(["git", "-C", str(UPSTREAM), "ls-files", "-z"])
    return [Path(value.decode("utf-8")) for value in raw.split(b"\0") if value]


def _preserved(rel: Path) -> bool:
    return any(part in {"bin", "obj"} for part in rel.parts) or (
        bool(rel.parts) and rel.parts[0] == ".git")


def _sync_upstream_to_candidate(candidate_vendor: Path) -> None:
    tracked = set(_tracked_upstream())
    candidate_vendor.mkdir(parents=True, exist_ok=True)
    for path in sorted(candidate_vendor.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        rel = path.relative_to(candidate_vendor)
        if _preserved(rel):
            continue
        if path.is_file() or path.is_symlink():
            if rel not in tracked:
                path.unlink()
        elif path.is_dir():
            try:
                path.rmdir()
            except OSError:
                pass
    for rel in sorted(tracked, key=lambda item: item.as_posix()):
        source = UPSTREAM / rel
        target = candidate_vendor / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def _build_env() -> dict[str, str]:
    """Build environment. ``VALO_DOTNET_CLI_HOME`` / ``VALO_NUGET_PACKAGES`` redirect the caches
    (isolated QA runs); by default they stay under the project's ``dotnet10-sdk`` as before."""
    env = dict(os.environ)
    env["DOTNET_CLI_HOME"] = os.environ.get("VALO_DOTNET_CLI_HOME") or str(ROOT / "dotnet10-sdk" / "cli-home")
    env["NUGET_PACKAGES"] = os.environ.get("VALO_NUGET_PACKAGES") or str(ROOT / "dotnet10-sdk" / "packages")
    env["DOTNET_CLI_TELEMETRY_OPTOUT"] = "1"
    env["DOTNET_NOLOGO"] = "1"
    return env


def _attempt_dir(commit: str, overlay_hash: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%H%M%S")
    return UPDATE_ROOT / "a" / f"{stamp}-{os.getpid()}-{commit[:6]}-{overlay_hash[:6]}"


def _prepare_candidate(commit: str, overlay_hash: str) -> tuple[Path, Path]:
    attempt = _attempt_dir(commit, overlay_hash)
    candidate_vendor = attempt / "vendorValorantReplayParser"
    _sync_upstream_to_candidate(candidate_vendor)
    # Prefix the candidate path explicitly. Running git apply from ``attempt``
    # would discover the parent project repository and could touch the active
    # vendor tree instead of the isolated candidate.
    candidate_prefix = attempt.relative_to(ROOT).as_posix()
    _run(["git", "apply", "--check", f"--directory={candidate_prefix}", OVERLAY_PATCH])
    _run(["git", "apply", f"--directory={candidate_prefix}", OVERLAY_PATCH])
    candidate_project = candidate_vendor / CSPROJ_REL
    _run([DOTNET, "build", candidate_project, "-c", "Release"], env=_build_env())
    _require(candidate_vendor / DLL_REL, "candidate CliReader.dll")
    return attempt, candidate_vendor


def _ensure_baseline() -> dict:
    if BASELINE_FILE.is_file():
        return _read_json(BASELINE_FILE)
    print(f"creating_baseline={BASELINE_FILE}", flush=True)
    return create_baseline()


def _selftest(attempt: Path, candidate_vendor: Path, baseline: dict) -> dict:
    copied_replay = attempt / "selftest" / "haven-13.06-copy.vrf"
    output = attempt / "selftest" / "export"
    copied_replay.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SELFTEST_REPLAY, copied_replay)
    output.mkdir(parents=True, exist_ok=True)
    _run([DOTNET, candidate_vendor / DLL_REL, "export", copied_replay,
          "--output", output, "--profile", "viewer"], env=_build_env())
    verdict = assess_decode_density(output, baseline=baseline)
    _write_json_atomic(attempt / "selftest" / "density-verdict.json", verdict)
    print(f"selftest={json.dumps({'state': verdict['state'], 'suspect': verdict['suspect'], 'reason': verdict['reason'], 'basis': verdict['basis']})}")
    return verdict


def _commit_text(commit: str, overlay_hash: str, *, assemblies: dict | None = None,
                 tested: list[dict] | None = None) -> str:
    """``parser-commit.txt`` content: source/overlay identity plus (optionally) build identity.

    ``assemblies`` maps assembly file name -> SHA-256 of the built output; it adds
    ``assembly_set_sha256`` and ``assembly.<name>`` lines. ``tested`` is the replay matrix that
    was actually exercised for this build (``tested.N=branch|source_sha|result|detail``); a branch
    appearing only in the transform registry is NOT listed here.
    """
    files: list[str] = []
    for line in OVERLAY_PATCH.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("+++ b/"):
            value = line[6:]
            if value not in files:
                files.append(value)
    lines = [f"upstream_sha={commit}",
             f"overlay_patch={OVERLAY_PATCH.relative_to(ROOT).as_posix()}",
             f"overlay_patch_sha256={overlay_hash}", "overlay_files:",
             *[f"- {value}" for value in files]]
    if assemblies:
        lines.append("assembly_set_sha256=" + store.assembly_set_sha256(assemblies))
        lines += [f"assembly.{name}={assemblies[name]}" for name in sorted(assemblies)]
    for index, item in enumerate(tested or [], 1):
        cells = [str(item.get(key) or "").replace("|", "/").replace("\n", " ")
                 for key in ("branch", "sourceSha256", "result", "detail")]
        lines.append(f"tested.{index}=" + "|".join(cells))
    return "\n".join(lines) + "\n"


def _candidate_assemblies(candidate_vendor: Path) -> dict[str, str]:
    """SHA-256 of the candidate's built decoder assemblies (hashed once per publish, not per poll)."""
    directory = (candidate_vendor / DLL_REL).parent
    found = store.assembly_hashes(bin_dir=directory)
    missing = sorted(name for name, value in found.items() if not value)
    if missing:
        raise UpdateError(f"candidate build is missing assemblies: {', '.join(missing)}")
    return {name: str(value) for name, value in found.items()}


def _publish_candidate(candidate_vendor: Path, commit: str, overlay_hash: str, *,
                       assemblies: dict | None = None, tested: list[dict] | None = None) -> Path:
    """Atomically swap the tested vendor tree and retain the previous build."""
    backup_root = UPDATE_ROOT / "b"
    backup_root.mkdir(parents=True, exist_ok=True)
    old = store.parser_commit() or "unknown"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = backup_root / f"vendor-{stamp}-{old[:12]}"
    if backup.exists():
        backup = backup.with_name(backup.name + f"-{os.getpid()}")
    moved_old = False
    try:
        os.rename(VENDOR, backup)
        moved_old = True
        os.rename(candidate_vendor, VENDOR)
        temporary = COMMIT_FILE.with_suffix(".txt.tmp")
        temporary.write_text(_commit_text(commit, overlay_hash, assemblies=assemblies, tested=tested),
                             encoding="utf-8")
        os.replace(temporary, COMMIT_FILE)
    except Exception:
        if moved_old and not VENDOR.exists() and backup.exists():
            os.rename(backup, VENDOR)
        elif moved_old and VENDOR.exists() and backup.exists():
            failed = candidate_vendor.parent / "failed-publish-vendor"
            if not failed.exists():
                os.rename(VENDOR, failed)
                os.rename(backup, VENDOR)
        raise
    return backup


def _transform_patch_failure(notes: object) -> bool:
    text = notes.lower() if isinstance(notes, str) else ""
    if "invalidseek" in text or "header" in text:
        return False
    return "transform" in text or "unsupported branch" in text or "unsupported patch" in text


def list_requeue_candidates(*, data_root: Path = store.DATA_REPLAYS_DIR,
                            supported: Iterable[str] | None = None,
                            parser_commit: str | None = None) -> list[dict]:
    """List failed unsupported-transform replays eligible for one re-queue."""
    allowed = set(supported if supported is not None else pipeline.supported_branches())
    token = parser_commit or store.parser_commit() or "unknown"
    candidates: list[dict] = []
    try:
        replay_dirs = sorted(path for path in Path(data_root).iterdir() if path.is_dir())
    except OSError:
        return candidates
    for replay_dir in replay_dirs:
        manifest_path = replay_dir / "manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(manifest, dict) or manifest.get("status") != "failed":
            continue
        if manifest.get("branch") not in allowed:
            continue
        reason = manifest.get("notes") or manifest.get("error")
        if not _transform_patch_failure(reason):
            continue
        if manifest.get("parserRequeueCommit") == token:
            continue
        source = manifest.get("source")
        source_path = Path(source) if isinstance(source, str) else None
        if source_path is not None and not source_path.is_absolute():
            source_path = ROOT / source_path
        if source_path is None or not source_path.is_file():
            continue
        candidates.append({"id": replay_dir.name, "branch": manifest.get("branch"),
                           "reason": str(reason), "source": str(source_path),
                           "manifest": str(manifest_path)})
    return candidates


def _post_reprocess(url: str, replay_id: str) -> dict:
    endpoint = f"{url.rstrip('/')}/api/replays/{replay_id}/reprocess"
    request = urllib.request.Request(endpoint, data=b"{}",
                                     headers={"Content-Type": "application/json"},
                                     method="POST")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - loopback URL
            body = response.read().decode("utf-8", "replace")
            if response.status < 200 or response.status >= 300:
                raise UpdateError(f"re-queue {replay_id} returned HTTP {response.status}: {body}")
    except (OSError, urllib.error.URLError) as exc:
        raise UpdateError(f"cannot re-queue {replay_id} through {endpoint}: {exc}") from exc
    try:
        value = json.loads(body)
    except ValueError:
        value = {"response": body}
    return value if isinstance(value, dict) else {"response": value}


def requeue_after_update(candidates: list[dict], *, parser_commit: str,
                         api_base: str = DEFAULT_REQUEUE_API,
                         post: Callable[[str, str], dict] = _post_reprocess) -> list[dict]:
    """Print the dry-run list, then enqueue through the loopback replay API."""
    print("requeue_dry_run=" + json.dumps(candidates, sort_keys=True), flush=True)
    queued: list[dict] = []
    for item in candidates:
        response = post(api_base, item["id"])
        manifest_path = Path(item["manifest"])
        manifest = _read_json(manifest_path)
        manifest["parserRequeueCommit"] = parser_commit
        manifest["parserRequeueRequestedAt"] = _utc_now()
        _write_json_atomic(manifest_path, manifest)
        queued.append({"id": item["id"], "response": response})
    print("requeued=" + json.dumps(queued, sort_keys=True), flush=True)
    return queued


def _decoder(label: str) -> None:
    print(f"{label}={json.dumps(pipeline.decoder_info(), sort_keys=True)}", flush=True)


def _current_matches(commit: str, overlay_hash: str) -> bool:
    """No-op only when source, overlay AND the decoder on disk all match the record.

    A recorded build whose assemblies were changed locally (``modified``) is deliberately not a
    no-op. A legacy record with no assembly hashes (``unrecorded``) keeps the old commit+overlay
    behaviour, so existing installs need no migration.
    """
    identity = store.parser_fingerprint()
    return bool((identity.get("commit"), identity.get("overlay")) == (commit, overlay_hash)
                and store.build_state()["state"] in ("verified", "unrecorded")
                and (VENDOR / DLL_REL).is_file() and BASELINE_FILE.is_file())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=("Safely pull, re-apply the local overlay, build, density-selftest, "
                     "publish, and re-queue supported parser failures."))
    parser.add_argument("--dry-run", action="store_true",
                        help="show update commands and eligible re-queue items without writing or building")
    parser.add_argument("--no-pull", action="store_true",
                        help="use the current workspace-local upstream checkout without git pull --ff-only")
    parser.add_argument("--force", action="store_true",
                        help="run candidate build/selftest even when commit, overlay, build, and baseline match")
    parser.add_argument("--requeue-api",
                        default=os.getenv("VALO_PARSER_REQUEUE_API", DEFAULT_REQUEUE_API),
                        help=f"loopback API used to enqueue eligible failed replays (default: {DEFAULT_REQUEUE_API})")
    parser.add_argument("--keep-selftest-output", action="store_true",
                        help="retain multi-gigabyte selftest NDJSON instead of removing it after a passing check")
    parser.add_argument("--pin", metavar="SHA", default=None,
                        help="check out exactly this upstream commit (overrides VALOPLANT_WORK/parser-pin.txt); "
                             "never pulls newer main")
    parser.add_argument("--no-pin", action="store_true",
                        help="ignore parser-pin.txt and follow upstream main: select the intended "
                             "tracking branch (origin/HEAD when the checkout is detached) and "
                             "fast-forward it; a dirty or diverged checkout is refused")
    return parser


def _remove_selftest_ndjson(attempt: Path) -> None:
    export = attempt / "selftest" / "export"
    for name in ("events.ndjson", "movement.ndjson", "events.ndjson.gz", "movement.ndjson.gz"):
        (export / name).unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _decoder("decoder_before")
    try:
        _require(UPSTREAM / ".git", "upstream Git checkout")
        _require(OVERLAY_PATCH, "parser overlay patch")
        _require(DOTNET, ".NET executable")
        _require(SELFTEST_REPLAY, "13.06 selftest replay copy source")
        pin = args.pin or (None if args.no_pin else read_pin())
        overlay_hash = _sha256(OVERLAY_PATCH)
        current_head = _git_head()
        current_candidates = list_requeue_candidates(parser_commit=store.parser_commit())
        print("requeue_dry_run=" + json.dumps(current_candidates, sort_keys=True), flush=True)
        if args.dry_run:
            if pin:
                print(f"DRY-RUN pinned upstream {pin} (no pull; fetch + detached checkout)")
            elif not args.no_pull:
                print(f"DRY-RUN unpinned: fetch origin, select the tracking branch "
                      f"(origin/HEAD when detached), {_command_text(['git', '-C', UPSTREAM, 'merge', '--ff-only', 'origin/<branch>'])}")
            print(f"DRY-RUN upstream_head={current_head}")
            print(f"DRY-RUN overlay_sha256={overlay_hash}")
            print(f"DRY-RUN candidate under {UPDATE_ROOT / 'a'}")
            print(f"DRY-RUN build with {DOTNET} build <candidate>/{CSPROJ_REL} -c Release")
            print(f"DRY-RUN selftest copy {SELFTEST_REPLAY}")
            print(f"DRY-RUN baseline {BASELINE_FILE}")
            print(f"DRY-RUN active parser remains {VENDOR}")
            _decoder("decoder_after")
            return 0
        commit = _sync_upstream_ref(pin, pull=not args.no_pull)
        if _current_matches(commit, overlay_hash) and not args.force:
            print(f"no_op=true parser_commit={commit} overlay_sha256={overlay_hash} "
                  f"build_state={store.build_state()['state']}", flush=True)
            pending = list_requeue_candidates(parser_commit=commit)
            requeue_after_update(pending, parser_commit=commit, api_base=args.requeue_api)
            _decoder("decoder_after")
            return 0
        baseline = _ensure_baseline()
        attempt, candidate_vendor = _prepare_candidate(commit, overlay_hash)
        verdict = _selftest(attempt, candidate_vendor, baseline)
        _require_validated(verdict)
        profile = verdict.get("profile") or {}
        tested = [{"branch": profile.get("branch"), "sourceSha256": (profile.get("sourceSha256") or "")[:16],
                   "result": verdict["state"], "detail": verdict.get("basis")}]
        backup = _publish_candidate(candidate_vendor, commit, overlay_hash,
                                    assemblies=_candidate_assemblies(candidate_vendor), tested=tested)
        print(f"published_parser_commit={commit}")
        print(f"previous_build={backup}")
        if not args.keep_selftest_output:
            _remove_selftest_ndjson(attempt)
        candidates = list_requeue_candidates(supported=pipeline.supported_branches(),
                                             parser_commit=commit)
        requeue_after_update(candidates, parser_commit=commit, api_base=args.requeue_api)
    except (OSError, subprocess.CalledProcessError, UpdateError) as exc:
        print(f"update_status=failed error={exc}", file=sys.stderr, flush=True)
        return 2
    _decoder("decoder_after")
    print("update_status=ready", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

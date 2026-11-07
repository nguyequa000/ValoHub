"""Deterministic analysis of normalized viewer rounds (no raw replay reads)."""
from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
import math
from threading import Lock

from aiohttp import web

from . import store

CELL = 32
SIZE = 1024
_CACHE: dict[str, tuple[int, dict]] = {}
_LOCK = Lock()


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _coordinate(map_info, x, y):
    """World axes are swapped, exactly as in viewer.js project()."""
    if not _number(x) or not _number(y):
        return None
    if not all(_number(map_info.get(key)) for key in
               ("xScalarToAdd", "yScalarToAdd", "xMultiplier", "yMultiplier")):
        return None
    px = (map_info["xScalarToAdd"] + y * map_info["xMultiplier"]) * SIZE
    py = (map_info["yScalarToAdd"] + x * map_info["yMultiplier"]) * SIZE
    if not (0 <= px < SIZE and 0 <= py < SIZE):
        return None
    return (int(px // CELL) * CELL, int(py // CELL) * CELL)


def _track_at(track, stamp, tick, *, before=False):
    """Nearest recorded sample; never interpolate over missing coordinates."""
    if not isinstance(track, dict) or not _number(stamp) or not _number(tick) or tick <= 0:
        return None
    xs, ys = track.get("x"), track.get("y")
    if not isinstance(xs, list) or not isinstance(ys, list):
        return None
    index = max(0, int((stamp - (1 if before else 0)) // tick))
    if index >= min(len(xs), len(ys)) or not _number(xs[index]) or not _number(ys[index]):
        return None
    return xs[index], ys[index]


def _finding(kind, n, stamp, title, detail, severity="info"):
    return {"id": f"{kind}-r{n}-{stamp}", "round": n, "tMs": stamp,
            "kind": kind, "title": title, "detail": detail, "severity": severity}


def analyze(replay_id: str) -> dict | None:
    """Return contract §1 payload, or None if the normalized match is unavailable."""
    match = store.load_match(replay_id)
    if not isinstance(match, dict) or not isinstance(match.get("rounds"), list):
        return None
    map_info = match.get("map") if isinstance(match.get("map"), dict) else {}
    heat, findings = [], []
    for meta in match["rounds"]:
        if not isinstance(meta, dict) or not isinstance(meta.get("n"), int):
            continue
        n = meta["n"]
        doc = store.load_round(replay_id, n)
        if not isinstance(doc, dict):
            continue
        tracks = doc.get("players") if isinstance(doc.get("players"), dict) else {}
        tick = doc.get("tickMs")
        bins = {kind: Counter() for kind in ("positions", "deaths", "util")}
        if _number(tick) and tick > 0:
            stride = max(1, round(1000 / tick))  # at most one position per player per second
            for track in tracks.values():
                if not isinstance(track, dict):
                    continue
                xs, ys, alive = track.get("x"), track.get("y"), track.get("alive")
                if not isinstance(xs, list) or not isinstance(ys, list):
                    continue
                for i in range(0, min(len(xs), len(ys)), stride):
                    if isinstance(alive, list) and i < len(alive) and alive[i] == 0:
                        continue
                    cell = _coordinate(map_info, xs[i], ys[i])
                    if cell is not None:
                        bins["positions"][cell] += 1
        events = doc.get("events") if isinstance(doc.get("events"), list) else []
        kills = defaultdict(list)
        first_blood = False
        for event in sorted((e for e in events if isinstance(e, dict)),
                            key=lambda e: e.get("t") if _number(e.get("t")) else float("inf")):
            stamp = event.get("t")
            if not _number(stamp) or stamp < 0:
                continue
            stamp = int(stamp)
            kind = event.get("type")
            actor, target = event.get("actor"), event.get("target")
            if kind == "kill":
                if not first_blood:
                    findings.append(_finding("first_blood", n, stamp, "First blood",
                                             f"{actor or 'Unknown player'} eliminated {target or 'an opponent'}."))
                    first_blood = True
                if actor:
                    kills[actor].append(stamp)
                point = _track_at(tracks.get(target), stamp, tick, before=True)
                cell = _coordinate(map_info, *point) if point else None
                if cell is not None:
                    bins["deaths"][cell] += 1
            elif kind == "plant":
                spike = doc.get("spike") if isinstance(doc.get("spike"), dict) else {}
                site = spike.get("site")
                suffix = f" at site {site}" if isinstance(site, str) and site else ""
                findings.append(_finding("plant", n, stamp, "Spike planted",
                                         f"{actor or 'Unknown player'} planted the spike{suffix}."))
            elif kind == "defuse":
                findings.append(_finding("defuse", n, stamp, "Spike defused",
                                         f"{actor or 'Unknown player'} defused the spike."))
            elif kind in ("ability", "ult"):
                # Explicit event location wins; otherwise place the marker at the actor's
                # sampled position. Abilities with no position have no heat bin.
                point = (event.get("x"), event.get("y")) if _number(event.get("x")) and _number(event.get("y")) else _track_at(tracks.get(actor), stamp, tick)
                cell = _coordinate(map_info, *point) if point else None
                if cell is not None:
                    bins["util"][cell] += 1
        for actor, timestamps in sorted(kills.items()):
            if len(timestamps) >= 2:
                findings.append(_finding("multikill", n, timestamps[-1], "Multi-kill",
                                         f"{actor} scored {len(timestamps)} kills this round.", "tip"))
        winner = meta.get("winner")
        if winner in (0, 1) and not isinstance(winner, bool):
            stamp = max(0, int(doc.get("endMs", 0) - doc.get("startMs", 0)))
            findings.append(_finding("round_result", n, stamp, "Round result",
                                     f"Team {winner} won; Team {1 - winner} lost ({meta.get('endType') or 'outcome unknown'})."))
        for kind, counts in bins.items():
            if counts:
                heat.append({"round": n, "kind": kind, "cell": CELL,
                             "bins": [[x, y, count] for (x, y), count in sorted(counts.items())]})
    findings.sort(key=lambda f: (f["round"], f["tMs"], f["id"]))
    return {"replayId": replay_id, "rounds": len(match["rounds"]),
            "findings": findings, "heat": heat}


def _cached(replay_id: str) -> dict | None:
    manifest = store.replay_dir(replay_id) / "manifest.json"
    try:
        stamp = manifest.stat().st_mtime_ns
    except OSError:
        stamp = 0  # demo may have only the flat viewer files
    with _LOCK:
        entry = _CACHE.get(replay_id)
        if entry and entry[0] == stamp:
            return entry[1]
        # Serialize the cold computation: concurrent requests for the same replay
        # must not load every round independently in parallel.
        result = analyze(replay_id)
        if result is not None:
            _CACHE[replay_id] = (stamp, result)
        return result


async def api_analysis(request: web.Request) -> web.Response:
    replay_id = request.match_info["replay_id"]
    try:
        payload = await asyncio.to_thread(_cached, replay_id)
    except ValueError:
        payload = None
    if payload is None:
        return web.json_response({"error": "replay not found"}, status=404)
    return web.json_response(payload, headers={"Cache-Control": "no-store"})


def mount(app: web.Application) -> None:
    app.router.add_get("/api/replays/{replay_id}/analysis", api_analysis)

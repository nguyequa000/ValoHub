"""Per-player coach metrics — deterministic "what went wrong" findings from
normalized viewer rounds. Pure stdlib: no aiohttp, no raw replay reads.

Input layout (BUILD_CONTRACT.md §3, viewer/ layout; the flat demo layout is also
accepted) ::

    <store_dir>/viewer/match.json        normalized match metadata (rounds + winner/endType)
    <store_dir>/viewer/rounds/rNN.json   one normalized round document
    <store_dir>/match.json               flat demo layout
    <store_dir>/rounds/rNN.json

Time base. Every round document stores frames on the round's own clock:
``tickMs`` (100 ms), frame ``i`` sampled at ``startMs + i*tickMs``, and every
``events[i]["t"]`` / ``spike.plantT`` / ``spike.defuseT`` is **round-relative
milliseconds** (``normalize.build_round_document``). Player ``x``/``y`` are world
coordinates, ``hp``/``alive`` are per-frame arrays; ``hp`` is absent for players
whose life-change series stayed below the health floor.

Reference convention (``refs`` on every finding) — every ref resolves against the
store it came from, which is what the P4-G grounding gate re-checks::

    {"src": "event",  "round": n, "t": ms, "tSec": s, "type": ..., "actor": ..., "target": ...}
        t equals an event's "t" in viewer/rounds/rNN.json.
    {"src": "sample", "round": n, "t": ms, "tSec": s, "slot": "P1", "x": ..., "y": ..., "alive": 0|1[, "hp": ...]}
        t is a frame time (t % tickMs == 0); the cited values equal the round's
        sampled frame values for that slot, i.e. t // tickMs indexes the arrays.
    {"src": "round",  "round": n, "t": ms, "tSec": s, "winner": 0|1, "endType": ...}
        t is the round's last sampled frame ((frames - 1) * tickMs), so it is a real
        frame of that round; winner/endType come from match.json.

``resolve_ref(match, rounds, ref)`` is the single implementation of that check.

Sides (attack/defense). Preferred source is ``attackerTeam`` published per round in
``match.json`` (P3/P4, from the parser overlay's ``round_outcome`` rows:
``winner_role`` plus a colour->team map pinned by the cumulative Red/Blue score
series) — reported as confidence ``replay``. When a store predates that field the
side is inferred from planter-team evidence per half (MR12: rounds 1-12 / 13-18,
sides swap) and labelled ``inferred``; a round with no evidence stays ``unknown``,
never guessed. ``report["sideInference"]`` carries the per-round source.

Death locations are reported as map-grid cells plus raw world coordinates — never as
site/callout names (``spike.site`` is an unproven enum byte in this store).

Trade detection mirrors ``bot/services/first_contact_analyzer.detect_trade_kills``
(5.0 s window, avenger must be a teammate of the victim and must kill the original
killer) re-implemented on the viewer round documents because that service is
aiohttp-bound and this module stays stdlib.
"""
from __future__ import annotations

import json
import math
import sys
from collections import Counter
from pathlib import Path

SCHEMA_REPORT = "valoplant_coach/v1"
TRADE_WINDOW_MS = 5000          # == first_contact_analyzer.TRADE_WINDOW_SECONDS * 1000
CELL = 32                       # death-cluster grid size, same as p2_analysis.CELL
REGION = 128                    # coarse cluster grid (8x8 over the projected map)
SIZE = 1024                     # projected minimap size, same as p2_analysis.SIZE
ROUNDS_PER_HALF = 12            # MR12: rounds 1-12 / 13-18, then a swap
MAX_REFS = 8                    # refs kept per finding

# Severity thresholds (documented here, asserted by the tests).
FIRST_DEATH_MAJOR = 0.25
FIRST_DEATH_MINOR = 0.15
UNTRADED_MAJOR = 0.50
UNTRADED_MINOR = 0.30
UNTRADED_MIN_DEATHS = 3
CLUSTER_MINOR_SHARE = 0.40
CLUSTER_MIN_COUNT = 3
POSTPLANT_MINOR_DISTANCE = 1500     # world units (~cm) while alive, mean over planted rounds
POSTPLANT_MIN_ROUNDS = 3
SIDE_SURVIVAL_MINOR = 0.25
SIDE_MIN_ROUNDS = 3
LOSS_BEFORE_DECISION_MINOR = 3
UTIL_LATE_VS_CONTACT_MS = 5000

SEVERITY_ORDER = {"major": 0, "minor": 1, "info": 2}
SIDE_SOURCES = ("replay", "inferred", "unknown")


# --------------------------------------------------------------------------- #
# primitives
# --------------------------------------------------------------------------- #
def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _int(value):
    return int(value) if _number(value) else None


def _json(path: Path):
    try:
        with Path(path).open(encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, ValueError):
        return None


def _rounds_dir(root: Path) -> Path:
    nested = root / "viewer" / "rounds"
    return nested if nested.is_dir() else root / "rounds"


def load_store(store_dir) -> dict | None:
    """Return ``{"match": ..., "rounds": {n: doc}, "dir": str}`` or None.

    Accepts the normalized layout (``viewer/``) and the flat demo layout.
    Round documents are keyed by ``doc["n"]`` when present, else by the ``rNN``
    file name. Corrupt or missing round documents are simply absent from the map.
    """
    root = Path(store_dir)
    match = _json(root / "viewer" / "match.json")
    if not isinstance(match, dict):
        match = _json(root / "match.json")
    if not isinstance(match, dict):
        return None
    rounds: dict[int, dict] = {}
    directory = _rounds_dir(root)
    if directory.is_dir():
        for path in sorted(directory.glob("r*.json")):
            doc = _json(path)
            if not isinstance(doc, dict):
                continue
            number = _int(doc.get("n"))
            if number is None:
                number = _int(path.stem.lstrip("r"))
            if number is not None:
                rounds[number] = doc
    return {"match": match, "rounds": rounds, "dir": str(root)}


def normalize_slot(slot) -> str:
    """``"P1"``/``1``/``"p1"`` -> ``"P1"``; anything else raises ValueError."""
    if isinstance(slot, int) and not isinstance(slot, bool):
        number = slot
    elif isinstance(slot, str):
        text = slot.strip().upper()
        if text.startswith("P") and text[1:].isdigit():
            number = int(text[1:])
        elif text.isdigit():
            number = int(text)
        else:
            raise ValueError(f"invalid player slot: {slot!r}")
    else:
        raise ValueError(f"invalid player slot: {slot!r}")
    if number < 1:
        raise ValueError(f"invalid player slot: {slot!r}")
    return f"P{number}"


def project_cell(map_info, x, y):
    """Map-grid cell of a world coordinate, or None.

    Identical maths to ``viewer.js project()`` / ``p2_analysis._coordinate``:
    the world axes are swapped (px from y, py from x).
    """
    if not _number(x) or not _number(y) or not isinstance(map_info, dict):
        return None
    if not all(_number(map_info.get(key)) for key in
               ("xScalarToAdd", "yScalarToAdd", "xMultiplier", "yMultiplier")):
        return None
    px = (map_info["xScalarToAdd"] + y * map_info["xMultiplier"]) * SIZE
    py = (map_info["yScalarToAdd"] + x * map_info["yMultiplier"]) * SIZE
    if not (0 <= px < SIZE and 0 <= py < SIZE):
        return None
    return (int(px // CELL) * CELL, int(py // CELL) * CELL)


def _as_list(value) -> list:
    """``value`` when it is a list, else an empty list (viewer arrays may be absent)."""
    return value if isinstance(value, list) else []


def _track_at(track, stamp, tick, *, before=False):
    """Nearest recorded sample; never interpolate over missing coordinates.

    ``before=True`` lands on the frame at or before the stamp — the same rule
    ``p2_analysis._track_at`` uses, so a death is placed where the player stood
    when they died, not one tick later.
    """
    if not isinstance(track, dict) or not _number(stamp) or not _number(tick) or tick <= 0:
        return None
    xs, ys = _as_list(track.get("x")), _as_list(track.get("y"))
    if not xs or not ys:
        return None
    index = max(0, int((stamp - (1 if before else 0)) // tick))
    if index >= min(len(xs), len(ys)) or not _number(xs[index]) or not _number(ys[index]):
        return None
    return index, xs[index], ys[index]


def _event_ref(n, event) -> dict:
    stamp = _int(event.get("t")) or 0
    return {"src": "event", "round": n, "t": stamp, "tSec": round(stamp / 1000.0, 3),
            "type": event.get("type"), "actor": event.get("actor"),
            "target": event.get("target")}


def _sample_ref(n, slot, index, tick, track) -> dict:
    xs = _as_list(track.get("x"))
    ys = _as_list(track.get("y"))
    stamp = index * tick
    ref = {"src": "sample", "round": n, "t": stamp, "tSec": round(stamp / 1000.0, 3),
           "slot": slot,
           "x": _int(xs[index]) if index < len(xs) else None,
           "y": _int(ys[index]) if index < len(ys) else None}
    alive = track.get("alive")
    if isinstance(alive, list) and index < len(alive) and alive[index] in (0, 1):
        ref["alive"] = alive[index]
    hp = track.get("hp")
    if isinstance(hp, list) and index < len(hp) and _number(hp[index]):
        ref["hp"] = _int(hp[index])
    return ref


def _round_ref(n, doc, meta) -> dict:
    """Round-level ref: cites the round's last sampled frame."""
    frames = _int(doc.get("frames")) or 0
    tick = _int(doc.get("tickMs")) or 100
    if frames:
        stamp = max(0, (frames - 1) * tick)
    else:
        stamp = max(0, (int(_int(doc.get("endMs")) or 0) - int(_int(doc.get("startMs")) or 0)))
    winner = meta.get("winner") if isinstance(meta, dict) else None
    return {"src": "round", "round": n, "t": stamp, "tSec": round(stamp / 1000.0, 3),
            "winner": winner if winner in (0, 1) and not isinstance(winner, bool) else None,
            "endType": (meta or {}).get("endType")}


def resolve_ref(match, rounds, ref) -> bool:
    """True when a finding reference points at a real event / sample / round.

    This is the contract the P4-G grounding gate relies on: a ref that does not
    resolve is a hallucinated citation.
    """
    if not isinstance(ref, dict):
        return False
    number = _int(ref.get("round"))
    stamp = _int(ref.get("t"))
    doc = rounds.get(number) if isinstance(rounds, dict) else None
    if doc is None or stamp is None or stamp < 0:
        return False
    src = ref.get("src")
    if src == "event":
        for event in doc.get("events") or []:
            if not isinstance(event, dict) or _int(event.get("t")) != stamp:
                continue
            if (event.get("type") == ref.get("type")
                    and event.get("actor") == ref.get("actor")
                    and event.get("target") == ref.get("target")):
                return True
        return False
    if src == "sample":
        tick = _int(doc.get("tickMs")) or 0
        track = (doc.get("players") or {}).get(ref.get("slot"))
        if tick <= 0 or not isinstance(track, dict):
            return False
        if stamp % tick:
            return False
        index = stamp // tick
        frames = _int(doc.get("frames"))
        if frames is not None and not (0 <= index < frames):
            return False
        xs, ys = _as_list(track.get("x")), _as_list(track.get("y"))
        if not xs or not ys:
            return False
        if not (0 <= index < len(xs) and 0 <= index < len(ys)):
            return False
        if _int(xs[index]) != ref.get("x") or _int(ys[index]) != ref.get("y"):
            return False
        alive = _as_list(track.get("alive"))
        if "alive" in ref and not (index < len(alive) and alive[index] == ref["alive"]):
            return False
        hp = _as_list(track.get("hp"))
        if "hp" in ref and not (index < len(hp) and _int(hp[index]) == ref["hp"]):
            return False
        return True
    if src == "round":
        frames = _int(doc.get("frames")) or 0
        tick = _int(doc.get("tickMs")) or 100
        if frames:
            expected = max(0, (frames - 1) * tick)
        else:
            expected = max(0, (int(_int(doc.get("endMs")) or 0)
                               - int(_int(doc.get("startMs")) or 0)))
        if stamp != expected:
            return False
        meta = next((m for m in match.get("rounds") or []
                     if isinstance(m, dict) and _int(m.get("n")) == number), None)
        if meta is None:
            return False
        winner = meta.get("winner")
        normalised = winner if winner in (0, 1) and not isinstance(winner, bool) else None
        if "winner" in ref and normalised != ref["winner"]:
            return False
        if "endType" in ref and meta.get("endType") != ref["endType"]:
            return False
        return True
    return False


# --------------------------------------------------------------------------- #
# match-level helpers
# --------------------------------------------------------------------------- #
def _teams(match) -> dict:
    teams = {}
    for player in match.get("players") or []:
        if isinstance(player, dict) and isinstance(player.get("id"), str):
            team = player.get("team")
            teams[player["id"]] = team if team in (0, 1) and not isinstance(team, bool) else None
    return teams


def _slot_sort(name: str):
    return (int(name[1:]) if name[1:].isdigit() else 0, name)


def _slots(match) -> list:
    return sorted(_teams(match), key=_slot_sort)


def _kills(doc) -> list:
    kills = [e for e in (doc.get("events") or [])
             if isinstance(e, dict) and e.get("type") == "kill" and _int(e.get("t")) is not None]
    kills.sort(key=lambda e: (e["t"], str(e.get("actor")), str(e.get("target"))))
    return kills


def _util_events(doc) -> list:
    events = [e for e in (doc.get("events") or [])
              if isinstance(e, dict) and e.get("type") in ("ult", "ability")
              and _int(e.get("t")) is not None and e.get("actor")]
    events.sort(key=lambda e: (e["t"], str(e.get("actor"))))
    return events


def _traded_by(kills, teams) -> dict:
    """``{kill index: index of the avenging kill}`` — mirrors detect_trade_kills()."""
    traded = {}
    for index, original in enumerate(kills):
        killer, victim = original.get("actor"), original.get("target")
        if not killer or not victim or killer == victim:
            continue
        victim_team = teams.get(victim)
        if victim_team is None:
            continue
        for offset in range(index + 1, len(kills)):
            avenger = kills[offset]
            elapsed = avenger["t"] - original["t"]
            if elapsed > TRADE_WINDOW_MS:
                break
            if elapsed < 0:
                continue
            actor, target = avenger.get("actor"), avenger.get("target")
            if actor and target == killer and actor != target and teams.get(actor) == victim_team:
                traded[index] = offset
                break
    return traded


def _decision_t(doc, kills):
    """When the round was decided: the defuse, else the last kill."""
    defuses = []
    for event in doc.get("events") or []:
        if isinstance(event, dict) and event.get("type") == "defuse":
            stamp = _int(event.get("t"))
            if stamp is not None:
                defuses.append(stamp)
    if defuses:
        return min(defuses)
    return kills[-1]["t"] if kills else None


def _half_key(n: int) -> int:
    if n <= ROUNDS_PER_HALF * 2:
        return (n - 1) // ROUNDS_PER_HALF
    return ROUNDS_PER_HALF * 2 + (n - 1) % 2      # overtime swaps every round


def infer_sides(match, rounds) -> dict:
    """Attacking team per round, with the source of each value.

    Preference order:

    1. ``attackerTeam`` (0/1) published on the round entry in ``match.json`` —
       exact, from the parser overlay's ``round_outcome`` rows (``winner_role``
       plus a colour->team map pinned by the cumulative Red/Blue score series).
       Source ``replay``.
    2. Planter-team evidence per half — the spike belongs to the attackers, so the
       team of the plant actors names the attacking team for that half; a half
       without plant evidence takes the opposite of the other half's attacker when
       that one is known (MR12 side swap). Source ``inferred``.

    A round with neither is ``unknown``; nothing is guessed.
    """
    teams = _teams(match)
    published: dict = {}
    halves: dict = {}
    for meta in match.get("rounds") or []:
        if not isinstance(meta, dict):
            continue
        n = _int(meta.get("n"))
        if n is None or n < 1:
            continue
        declared = meta.get("attackerTeam")
        if declared in (0, 1) and not isinstance(declared, bool):
            published[n] = declared
        key = _half_key(n)
        halves.setdefault(key, {"rounds": [], "planterTeams": Counter()})
        halves[key]["rounds"].append(n)
        doc = rounds.get(n)
        if not isinstance(doc, dict):
            continue
        for event in doc.get("events") or []:
            if isinstance(event, dict) and event.get("type") == "plant":
                team = teams.get(event.get("actor"))
                if team in (0, 1):
                    halves[key]["planterTeams"][team] += 1

    inferred: dict = {}
    for key, info in halves.items():
        counts = info["planterTeams"]
        if not counts:
            continue
        best = counts.most_common()
        if len(best) > 1 and best[0][1] == best[1][1]:
            continue                     # tie: ambiguous evidence, no guess
        inferred[key] = best[0][0]
    ordered = sorted(inferred)
    if len(ordered) == 2 and ordered[0] < ROUNDS_PER_HALF:      # a normal two-half match
        other = ROUNDS_PER_HALF if ordered[0] == 0 else 0
        if other not in inferred:
            inferred[other] = 1 - inferred[ordered[0]]

    side_of: dict = {}
    source: dict = {}
    for key in sorted(halves):
        for n in halves[key]["rounds"]:
            if n in published:
                side_of[n] = published[n]
                source[n] = "replay"
            elif key in inferred:
                side_of[n] = inferred[key]
                source[n] = "inferred"
            else:
                side_of[n] = None
                source[n] = "unknown"
    half_reports = []
    for key in sorted(halves):
        counts = halves[key]["planterTeams"]
        half_reports.append({
            "rounds": [min(halves[key]["rounds"]), max(halves[key]["rounds"])],
            "attackerTeamFromMatch": published.get(halves[key]["rounds"][0]),
            "attackerTeamInferred": inferred.get(key),
            "plantEvidence": sum(counts.values()),
            "plantTeams": {str(team): count for team, count in sorted(counts.items())},
        })
    return {"rounds": side_of, "source": source, "halves": half_reports,
            "publishedRounds": sorted(published),
            "method": "attackerTeam from match.json when present (replay), else planter-team "
                      "majority per half with the MR12 swap (inferred), else unknown"}


# --------------------------------------------------------------------------- #
# per-player metrics
# --------------------------------------------------------------------------- #
def _empty_metrics(slot) -> dict:
    return {
        "slot": slot,
        "team": None,
        "rounds": 0, "roundsWithDocument": 0,
        "kills": 0, "deaths": 0, "tradeableDeaths": 0,
        "firstDeaths": 0, "firstDeathRate": None, "firstBloods": 0, "firstBloodRate": None,
        "firstDeathsBySide": {"attack": 0, "defense": 0, "unknown": 0},
        "untradedDeaths": 0, "tradedDeaths": 0, "untradedDeathRate": None,
        "tradeWindowMs": TRADE_WINDOW_MS, "killsThatWereTrades": 0,
        "deathCells": {}, "topDeathCluster": None, "topDeathRegion": None,
        "utilEvents": 0, "utilByType": {}, "utilMedianTMs": None,
        "utilMedianRoundFraction": None, "utilMedianVsContactMs": None,
        "utilBeforeFirstContact": 0, "utilAfterFirstContact": 0,
        "postPlant": {"rounds": 0, "aliveAtPlant": 0, "deathsPostPlant": 0,
                      "aliveFrames": 0, "deadFrames": 0, "aliveFraction": None,
                      "meanDistance": None, "nearestDistance": None, "within1000": 0,
                      "refs": []},
        "sides": {"attack": {"rounds": 0, "deaths": 0, "survived": 0, "survivalRate": None},
                  "defense": {"rounds": 0, "deaths": 0, "survived": 0, "survivalRate": None}},
        "unknownSideRounds": 0,
        "lostRounds": 0, "lossRoundsWithDeath": 0,
        "lossDeaths": 0, "lossDeathsBeforeDecision": 0, "lossDeathRate": None,
        "roundsTable": [],
        "_notes": [],
        "_utilTimes": [], "_utilFractions": [], "_utilVsContact": [], "_distances": [],
        "_firstDeathRefs": [], "_firstBloodRefs": [], "_untradedRefs": [], "_utilRefs": [],
        "_deathRefs": [], "_lossRefs": [], "_clusterRefs": {},
        "_sideDeathRefs": {"attack": [], "defense": []},
    }


def _finish_rates(metrics) -> None:
    rounds = metrics["rounds"]
    metrics["firstDeathRate"] = (metrics["firstDeaths"] / rounds) if rounds else None
    metrics["firstBloodRate"] = (metrics["firstBloods"] / rounds) if rounds else None
    metrics["untradedDeathRate"] = ((metrics["untradedDeaths"] / metrics["tradeableDeaths"])
                                    if metrics["tradeableDeaths"] else None)
    metrics["lossDeathRate"] = ((metrics["lossDeaths"] / metrics["lostRounds"])
                                if metrics["lostRounds"] else None)
    for stats in metrics["sides"].values():
        stats["survivalRate"] = (stats["survived"] / stats["rounds"]) if stats["rounds"] else None
    post = metrics["postPlant"]
    frames = post["aliveFrames"] + post["deadFrames"]
    post["aliveFraction"] = (post["aliveFrames"] / frames) if frames else None
    if metrics["_distances"]:
        ordered = sorted(metrics["_distances"])
        post["meanDistance"] = round(sum(ordered) / len(ordered), 1)
        post["nearestDistance"] = round(ordered[0], 1)
        post["within1000"] = sum(1 for d in ordered if d <= 1000)
    if metrics["_utilTimes"]:
        times = sorted(metrics["_utilTimes"])
        metrics["utilMedianTMs"] = times[len(times) // 2]
    if metrics["_utilFractions"]:
        fractions = sorted(metrics["_utilFractions"])
        metrics["utilMedianRoundFraction"] = round(fractions[len(fractions) // 2], 3)
    if metrics["_utilVsContact"]:
        deltas = sorted(metrics["_utilVsContact"])
        metrics["utilMedianVsContactMs"] = deltas[len(deltas) // 2]


def compute_metrics(match, rounds, slot, sides=None) -> dict:
    """Every P4-A metric for one slot across the whole match."""
    slot = normalize_slot(slot)
    teams = _teams(match)
    sides = sides if isinstance(sides, dict) else infer_sides(match, rounds)
    team = teams.get(slot)
    metrics = _empty_metrics(slot)
    metrics["team"] = team
    cells = Counter()
    regions = Counter()
    region_refs: dict = {}
    region_cells: dict = {}
    for meta in match.get("rounds") or []:
        if not isinstance(meta, dict):
            continue
        n = _int(meta.get("n"))
        if n is None:
            continue
        metrics["rounds"] += 1
        doc = rounds.get(n)
        if not isinstance(doc, dict):
            metrics["_notes"].append(f"round {n}: no viewer document (skipped)")
            continue
        metrics["roundsWithDocument"] += 1
        tick = _int(doc.get("tickMs")) or 100
        frames = _int(doc.get("frames")) or 0
        track = (doc.get("players") or {}).get(slot)
        kills = _kills(doc)
        traded = _traded_by(kills, teams)
        attacker = sides["rounds"].get(n)
        side = None
        if attacker in (0, 1) and team in (0, 1):
            side = "attack" if team == attacker else "defense"
        else:
            metrics["unknownSideRounds"] += 1
        winner = meta.get("winner")
        won = winner if winner in (0, 1) and not isinstance(winner, bool) else None
        lost_round = won is not None and team in (0, 1) and won != team
        if lost_round:
            metrics["lostRounds"] += 1

        first = kills[0] if kills else None
        died_first = bool(first and first.get("target") == slot)
        blood = bool(first and first.get("actor") == slot)
        if died_first:
            metrics["firstDeaths"] += 1
            metrics["firstDeathsBySide"][side or "unknown"] += 1
            metrics["_firstDeathRefs"].append(_event_ref(n, first))
        if blood:
            metrics["firstBloods"] += 1
            metrics["_firstBloodRefs"].append(_event_ref(n, first))

        metrics["kills"] += sum(1 for k in kills if k.get("actor") == slot)
        deaths = [k for k in kills if k.get("target") == slot]
        metrics["deaths"] += len(deaths)
        for index, kill in enumerate(kills):
            if index in traded and kill.get("actor") == slot:
                metrics["killsThatWereTrades"] += 1
        decided = _decision_t(doc, kills)
        untraded = 0
        for index, kill in enumerate(kills):
            if kill.get("target") != slot:
                continue
            metrics["_deathRefs"].append(_event_ref(n, kill))
            if side is not None:
                metrics["_sideDeathRefs"][side].append(_event_ref(n, kill))
            killer = kill.get("actor")
            tradeable = bool(killer and killer != slot and teams.get(slot) is not None)
            if tradeable:
                metrics["tradeableDeaths"] += 1
            if index in traded:
                metrics["tradedDeaths"] += 1
            elif tradeable:
                untraded += 1
                metrics["untradedDeaths"] += 1
                metrics["_untradedRefs"].append(_event_ref(n, kill))
            if lost_round:
                metrics["lossDeaths"] += 1
                metrics["_lossRefs"].append(_event_ref(n, kill))
                if decided is not None and kill["t"] < decided:
                    metrics["lossDeathsBeforeDecision"] += 1
            if track is not None:
                point = _track_at(track, kill["t"], tick, before=True)
                if point is not None:
                    pindex, px, py = point
                    cell = project_cell(match.get("map"), px, py)
                    if cell is not None:
                        sample = _sample_ref(n, slot, pindex, tick, track)
                        cells[cell] += 1
                        metrics["_clusterRefs"].setdefault(cell, []).append(sample)
                        region = ((cell[0] // REGION) * REGION, (cell[1] // REGION) * REGION)
                        regions[region] += 1
                        region_refs.setdefault(region, []).append(sample)
                        region_cells.setdefault(region, set()).add(cell)
        if lost_round and deaths:
            metrics["lossRoundsWithDeath"] += 1
        if side is not None:
            stats = metrics["sides"][side]
            stats["rounds"] += 1
            stats["deaths"] += len(deaths)
            if not deaths:
                stats["survived"] += 1

        spike = doc.get("spike") if isinstance(doc.get("spike"), dict) else {}
        plant_t = _int(spike.get("plantT"))
        plant_x, plant_y = spike.get("plantX"), spike.get("plantY")
        post_alive_at_plant = None
        if plant_t is not None and isinstance(track, dict):
            metrics["postPlant"]["rounds"] += 1
            alive = _as_list(track.get("alive"))
            xs = _as_list(track.get("x"))
            ys = _as_list(track.get("y"))
            start_index = max(0, plant_t // tick)
            if start_index < len(alive):
                post_alive_at_plant = alive[start_index]
                if post_alive_at_plant == 1:
                    metrics["postPlant"]["aliveAtPlant"] += 1
            nearest = None
            for index in range(start_index, min(frames or len(alive), len(alive))):
                if alive[index] == 1:
                    metrics["postPlant"]["aliveFrames"] += 1
                else:
                    metrics["postPlant"]["deadFrames"] += 1
                if (alive[index] == 1 and index < len(xs) and index < len(ys)
                        and _number(xs[index]) and _number(ys[index])
                        and _number(plant_x) and _number(plant_y)):
                    distance = math.hypot(xs[index] - plant_x, ys[index] - plant_y)
                    metrics["_distances"].append(distance)
                    if nearest is None or distance < nearest[0]:
                        nearest = (distance, index)
            if nearest is not None:
                metrics["postPlant"]["refs"].append(_sample_ref(n, slot, nearest[1], tick, track))
            metrics["postPlant"]["deathsPostPlant"] += sum(1 for k in deaths if k["t"] >= plant_t)

        for event in _util_events(doc):
            if event.get("actor") != slot:
                continue
            metrics["utilEvents"] += 1
            kind = event.get("type")
            metrics["utilByType"][kind] = metrics["utilByType"].get(kind, 0) + 1
            metrics["_utilRefs"].append(_event_ref(n, event))
            duration = max(1, (int(_int(doc.get("endMs")) or 0)
                               - int(_int(doc.get("startMs")) or 0)))
            metrics["_utilTimes"].append(event["t"])
            metrics["_utilFractions"].append(event["t"] / duration)
            if first is not None:
                delta = event["t"] - first["t"]
                metrics["_utilVsContact"].append(delta)
                if delta < 0:
                    metrics["utilBeforeFirstContact"] += 1
                else:
                    metrics["utilAfterFirstContact"] += 1

        metrics["roundsTable"].append({
            "n": n, "side": side, "sideSource": sides["source"].get(n, "unknown"),
            "won": (won == team) if (won is not None and team in (0, 1)) else None,
            "kills": sum(1 for k in kills if k.get("actor") == slot),
            "deaths": len(deaths), "untradedDeaths": untraded,
            "firstDeath": died_first, "firstBlood": blood,
            "diedAt": [k["t"] for k in deaths], "decidedAt": decided,
            "plantT": plant_t, "aliveAtPlant": post_alive_at_plant,
        })

    if team is None:
        metrics["_notes"].append("player team unknown in match.json: side metrics unavailable")
    metrics["deathCells"] = {f"{x},{y}": count for (x, y), count in sorted(cells.items())}
    if cells:
        cell, count = max(cells.items(), key=lambda kv: (kv[1], -kv[0][0], -kv[0][1]))
        refs = metrics["_clusterRefs"].get(cell, [])
        metrics["topDeathCluster"] = {
            "cell": list(cell), "count": count,
            "share": round(count / metrics["deaths"], 3) if metrics["deaths"] else None,
            "world": [refs[0].get("x"), refs[0].get("y")] if refs else [None, None],
            "rounds": sorted({ref["round"] for ref in refs}),
            "refs": refs,
        }
    if regions:
        region, count = max(regions.items(), key=lambda kv: (kv[1], -kv[0][0], -kv[0][1]))
        refs = region_refs.get(region, [])
        metrics["topDeathRegion"] = {
            "region": list(region), "size": REGION, "count": count,
            "share": round(count / metrics["deaths"], 3) if metrics["deaths"] else None,
            "cells": [list(cell) for cell in sorted(region_cells.get(region, set()))],
            "world": [refs[0].get("x"), refs[0].get("y")] if refs else [None, None],
            "rounds": sorted({ref["round"] for ref in refs}),
            "refs": refs,
        }
    _finish_rates(metrics)
    return metrics


# --------------------------------------------------------------------------- #
# baseline / ranking
# --------------------------------------------------------------------------- #
def _rank(values, slot, better):
    mine = values.get(slot)
    if mine is None:
        return None
    peers = [value for peer, value in values.items() if value is not None and peer != slot]
    if better == "lower":
        rank = 1 + sum(1 for value in peers if value < mine)
    else:
        rank = 1 + sum(1 for value in peers if value > mine)
    return {"value": mine, "rank": rank, "of": len(peers) + 1, "better": better}


def compare_baseline(all_metrics: dict, slot: str) -> dict:
    """Same metrics for the nine other players plus this slot's rank on each."""
    first_death: dict = {}
    untraded: dict = {}
    survival: dict = {}
    cluster: dict = {}
    post_plant: dict = {}
    util: dict = {}
    loss: dict = {}
    for peer, metrics in all_metrics.items():
        first_death[peer] = metrics["firstDeathRate"]
        untraded[peer] = metrics["untradedDeathRate"]
        rates = [metrics["sides"][side]["survivalRate"] for side in ("attack", "defense")]
        rates = [rate for rate in rates if _number(rate)]
        survival[peer] = (sum(rates) / len(rates)) if rates else None
        top = metrics.get("topDeathRegion") or metrics.get("topDeathCluster")
        cluster[peer] = top["share"] if top and _number(top.get("share")) else None
        post_plant[peer] = metrics["postPlant"]["meanDistance"]
        util[peer] = metrics["utilEvents"]
        loss[peer] = metrics["lossDeathRate"]
    ranks = {
        "firstDeathRate": _rank(first_death, slot, "lower"),
        "untradedDeathRate": _rank(untraded, slot, "lower"),
        "survivalRate": _rank(survival, slot, "higher"),
        "topDeathClusterShare": _rank(cluster, slot, "lower"),
        "postPlantMeanDistance": _rank(post_plant, slot, "lower"),
        "utilEvents": _rank(util, slot, "higher"),
        "lossDeathRate": _rank(loss, slot, "lower"),
    }
    players = []
    for peer in sorted(all_metrics, key=_slot_sort):
        metrics = all_metrics[peer]
        players.append({
            "slot": peer, "team": metrics.get("team"),
            "kills": metrics["kills"], "deaths": metrics["deaths"],
            "firstDeathRate": metrics["firstDeathRate"],
            "untradedDeathRate": metrics["untradedDeathRate"],
            "survivalRate": survival[peer],
            "topDeathClusterShare": cluster[peer],
            "postPlantMeanDistance": post_plant[peer],
            "utilEvents": metrics["utilEvents"],
            "lossDeathRate": metrics["lossDeathRate"],
        })
    return {"count": len(all_metrics), "ranks": ranks, "players": players,
            "direction": {"firstDeathRate": "lower", "untradedDeathRate": "lower",
                          "survivalRate": "higher", "topDeathClusterShare": "lower",
                          "postPlantMeanDistance": "lower", "utilEvents": "higher",
                          "lossDeathRate": "lower"}}


# --------------------------------------------------------------------------- #
# findings
# --------------------------------------------------------------------------- #
def _finding(fid, kind, severity, text, refs):
    return {"id": fid, "kind": kind, "severity": severity, "text": text, "refs": refs[:MAX_REFS]}


def _pct(value):
    return "n/a" if value is None else f"{value:.0%}"


def build_findings(slot, metrics, baseline, match, rounds) -> list:
    """Deterministic findings for one slot; every finding carries resolvable refs."""
    slot = normalize_slot(slot)
    total = metrics["rounds"]
    ranks = baseline["ranks"]
    findings = []

    def rank_text(key):
        entry = ranks.get(key)
        return (f"rank {entry['rank']}/{entry['of']} of the 10 players ({entry['better']} is better)"
                if entry else "not ranked")

    if metrics["firstDeaths"] and metrics["_firstDeathRefs"]:
        side = metrics["firstDeathsBySide"]
        side_text = ""
        if metrics["unknownSideRounds"] == 0 and (side["attack"] or side["defense"]):
            side_text = f" ({side['attack']} on attack, {side['defense']} on defense)"
        rate = metrics["firstDeathRate"]
        severity = "major" if rate is not None and rate >= FIRST_DEATH_MAJOR else (
            "minor" if rate is not None and rate >= FIRST_DEATH_MINOR else "info")
        findings.append(_finding(
            f"first_death-{slot}", "first_death", severity,
            f"First death in {metrics['firstDeaths']} of {total} rounds ({_pct(rate)}){side_text}; "
            f"{rank_text('firstDeathRate')}.", metrics["_firstDeathRefs"]))

    if metrics["firstBloods"] and metrics["_firstBloodRefs"]:
        rate = metrics["firstBloodRate"]
        severity = "minor" if rate is not None and rate >= FIRST_DEATH_MAJOR else "info"
        findings.append(_finding(
            f"first_blood-{slot}", "first_blood", severity,
            f"Opened the round with the first kill in {metrics['firstBloods']} of {total} rounds "
            f"({_pct(rate)}).", metrics["_firstBloodRefs"]))

    tradeable = metrics["tradeableDeaths"]
    if metrics["untradedDeaths"] and metrics["_untradedRefs"]:
        rate = metrics["untradedDeathRate"]
        severity = "major" if (rate is not None and rate >= UNTRADED_MAJOR
                               and tradeable >= UNTRADED_MIN_DEATHS) else (
            "minor" if (rate is not None and rate >= UNTRADED_MINOR
                        and tradeable >= UNTRADED_MIN_DEATHS) else "info")
        findings.append(_finding(
            f"untraded_death-{slot}", "untraded_death", severity,
            f"{metrics['untradedDeaths']} of {tradeable} tradeable deaths were never traded back "
            f"({_pct(rate)} untraded in a {TRADE_WINDOW_MS / 1000:.0f}s window); "
            f"{rank_text('untradedDeathRate')}. Only {metrics['killsThatWereTrades']} of "
            f"{metrics['kills']} kills avenged a teammate.", metrics["_untradedRefs"]))

    top = metrics.get("topDeathRegion")
    if top and top["count"] >= CLUSTER_MIN_COUNT and top["share"] is not None:
        severity = "minor" if top["share"] >= CLUSTER_MINOR_SHARE else "info"
        world = top["world"]
        region = top["region"]
        cell_text = ", ".join(f"({x},{y})" for x, y in top["cells"][:4])
        findings.append(_finding(
            f"death_cluster-{slot}-{region[0]}-{region[1]}", "death_cluster", severity,
            f"{top['count']} of {metrics['deaths']} deaths fall in one map region "
            f"(x {region[0]}-{region[0] + top['size']}, y {region[1]}-{region[1] + top['size']} of the "
            f"{SIZE}px minimap; grid cells {cell_text}; world sample {world[0]},{world[1]}) — "
            f"{_pct(top['share'])} of all deaths, in rounds {top['rounds']}.", top["refs"]))

    if metrics["_utilRefs"]:
        parts = [f"Used utility in {metrics['utilEvents']} of {total} rounds "
                 f"({', '.join(f'{k} x{v}' for k, v in sorted(metrics['utilByType'].items()))})"]
        if metrics["utilMedianTMs"] is not None:
            parts.append(f"median {metrics['utilMedianTMs'] / 1000:.1f}s into the round")
        if metrics["utilMedianRoundFraction"] is not None:
            parts.append(f"{metrics['utilMedianRoundFraction']:.0%} of the round elapsed")
        if metrics["utilMedianVsContactMs"] is not None:
            delta = metrics["utilMedianVsContactMs"]
            when = (f"{abs(delta) / 1000:.1f}s after" if delta >= 0
                    else f"{abs(delta) / 1000:.1f}s before")
            parts.append(f"median use {when} first contact")
        late = (metrics["utilMedianVsContactMs"] is not None
                and metrics["utilMedianVsContactMs"] > UTIL_LATE_VS_CONTACT_MS)
        findings.append(_finding(
            f"util_timing-{slot}", "util_timing", "minor" if late else "info",
            "; ".join(parts) + ".", metrics["_utilRefs"]))
    elif metrics["roundsWithDocument"]:
        refs = [_round_ref(entry["n"], rounds[entry["n"]],
                           next((m for m in match.get("rounds") or []
                                 if isinstance(m, dict) and _int(m.get("n")) == entry["n"]), {}))
                for entry in metrics["roundsTable"] if entry["n"] in rounds]
        findings.append(_finding(
            f"util_timing-{slot}", "util_timing", "minor",
            f"No utility event is attributed to this slot in {metrics['roundsWithDocument']} rounds — "
            f"either none was used or the store carries no ability rows for it; the refs list the "
            f"rounds scanned (capped at {MAX_REFS}).", refs))

    post = metrics["postPlant"]
    if post["rounds"] and post["refs"]:
        mean = post["meanDistance"]
        severity = "minor" if (mean is not None and mean >= POSTPLANT_MINOR_DISTANCE
                               and post["rounds"] >= POSTPLANT_MIN_ROUNDS) else "info"
        if mean is not None:
            distance_text = (f"mean distance to the spike while alive {mean:,.0f} world units "
                             f"({mean / 100:.1f} m), closest {round(post['nearestDistance']):,}")
        else:
            distance_text = "no in-range alive sample near the spike"
        findings.append(_finding(
            f"post_plant-{slot}", "post_plant", severity,
            f"Alive when the spike was planted in {post['aliveAtPlant']} of {post['rounds']} planted "
            f"rounds; {distance_text}; died after the plant in {post['deathsPostPlant']} round(s); "
            f"alive for {_pct(post['aliveFraction'])} of post-plant samples.", post["refs"]))

    weakest = None
    for side in ("attack", "defense"):
        stats = metrics["sides"][side]
        if stats["rounds"] >= SIDE_MIN_ROUNDS and stats["survivalRate"] is not None:
            if weakest is None or stats["survivalRate"] < weakest[1]["survivalRate"]:
                weakest = (side, stats)
    if weakest:
        side, stats = weakest
        other = "defense" if side == "attack" else "attack"
        other_stats = metrics["sides"][other]
        refs = metrics["_sideDeathRefs"][side] or metrics["_deathRefs"]
        if refs:
            severity = "minor" if stats["survivalRate"] < SIDE_SURVIVAL_MINOR else "info"
            sources = sorted({entry["sideSource"] for entry in metrics["roundsTable"]
                              if entry["side"] == side})
            findings.append(_finding(
                f"survival_side-{slot}", "survival_side", severity,
                f"Survived {stats['survived']} of {stats['rounds']} {side} rounds "
                f"({_pct(stats['survivalRate'])}, {stats['deaths']} deaths) vs "
                f"{other_stats['survived']}/{other_stats['rounds']} on {other} "
                f"({_pct(other_stats['survivalRate'])}); side source {','.join(sources) or 'unknown'}.",
                refs))

    if metrics["lostRounds"] and metrics["lossDeaths"] and metrics["_lossRefs"]:
        severity = ("minor" if metrics["lossDeathsBeforeDecision"] >= LOSS_BEFORE_DECISION_MINOR
                    else "info")
        # Share of the team's lost rounds the player died in. Deliberately NOT
        # lossDeaths / lostRounds: a revive agent (Clove) can die twice inside one lost
        # round, and that ratio printed under a "of the lost rounds" label goes past
        # 100% (P4-G gate note 2). The death count stays a plain count next to it.
        lossRoundShare = metrics["lossRoundsWithDeath"] / metrics["lostRounds"]
        findings.append(_finding(
            f"loss_involvement-{slot}", "loss_involvement", severity,
            f"Its team lost {metrics['lostRounds']} rounds; this player died in "
            f"{metrics['lossRoundsWithDeath']} of them ({_pct(lossRoundShare)} of the lost "
            f"rounds; {metrics['lossDeaths']} deaths), "
            f"{metrics['lossDeathsBeforeDecision']} of those deaths before the round was decided "
            f"(the defuse or the final elimination).", metrics["_lossRefs"]))

    findings.sort(key=lambda item: (SEVERITY_ORDER[item["severity"]], item["kind"], item["id"]))
    return findings


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #
def all_metrics(store: dict) -> dict:
    """``{slot: metrics}`` for every player in the store (one shared side map)."""
    match, rounds = store["match"], store["rounds"]
    sides = infer_sides(match, rounds)
    teams = _teams(match)
    out = {}
    for slot in _slots(match):
        out[slot] = compute_metrics(match, rounds, slot, sides=sides)
        out[slot]["team"] = teams.get(slot)
    return out


def _public_metrics(metrics: dict) -> dict:
    return {key: value for key, value in metrics.items() if not key.startswith("_")}


def _store_notes(match, rounds, metrics) -> list:
    notes = list(metrics.get("_notes") or [])
    missing = [meta.get("n") for meta in match.get("rounds") or []
               if isinstance(meta, dict) and _int(meta.get("n")) not in rounds]
    if missing:
        notes.append(f"rounds without a viewer document: {missing}")
    if metrics["unknownSideRounds"]:
        notes.append(f"{metrics['unknownSideRounds']} round(s) have no inferable side: "
                     "attack/defense splits and side counts exclude them")
    hp_missing = sorted({name for doc in rounds.values()
                         for name, track in (doc.get("players") or {}).items()
                         if isinstance(track, dict) and "hp" not in track})
    if hp_missing:
        notes.append("no per-frame hp samples for " + ", ".join(hp_missing)
                     + " (life-change series stayed below the health floor)")
    kinds = Counter()
    for doc in rounds.values():
        for event in doc.get("events") or []:
            if isinstance(event, dict) and event.get("type") in ("ult", "ability"):
                kinds[event["type"]] += 1
    notes.append("utility rows in this store: "
                 + (", ".join(f"{k} x{v}" for k, v in sorted(kinds.items())) if kinds else "none")
                 + "; sites/callouts are never named (spike.site is an unproven enum byte)")
    return sorted(set(notes))


def player_report(store_dir, player_slot) -> dict:
    """Coach report for one player: metrics + findings + peer baseline.

    Raises ValueError for a slot that is not in the match; returns
    ``{"error": ...}`` when the store directory holds no normalized match.
    """
    slot = normalize_slot(player_slot)
    store = load_store(store_dir)
    if store is None:
        return {"schema": SCHEMA_REPORT, "error": "no normalized match in this store",
                "storeDir": str(store_dir), "player": {"slot": slot}}
    match, rounds = store["match"], store["rounds"]
    if slot not in _slots(match):
        raise ValueError(f"slot {slot!r} is not in this match: {_slots(match)}")
    sides = infer_sides(match, rounds)
    every = all_metrics(store)
    metrics = every[slot]
    baseline = compare_baseline(every, slot)
    player = next((p for p in match.get("players") or []
                   if isinstance(p, dict) and p.get("id") == slot), {})
    return {
        "schema": SCHEMA_REPORT,
        "replayId": match.get("id"),
        "storeDir": store["dir"],
        "map": (match.get("map") or {}).get("name"),
        "patch": match.get("patch"),
        "player": {"slot": slot, "agent": player.get("agent"), "team": player.get("team"),
                   "name": player.get("name")},
        "roundsAnalyzed": metrics["roundsWithDocument"],
        "roundsInMatch": len(match.get("rounds") or []),
        "metrics": _public_metrics(metrics),
        "findings": build_findings(slot, metrics, baseline, match, rounds),
        "baseline": baseline,
        "sideInference": sides,
        "notes": _store_notes(match, rounds, metrics),
    }


def all_player_reports(store_dir) -> dict:
    """``{slot: player_report}`` for every player in the store."""
    store = load_store(store_dir)
    if store is None:
        return {}
    return {slot: player_report(store_dir, slot) for slot in _slots(store["match"])}


def _main(argv) -> int:
    if len(argv) < 3:
        print("usage: py -3.12 -m bot.replay.coach_metrics <store_dir> <slot> [--json]")
        return 2
    report = player_report(argv[1], argv[2])
    if "--json" in argv:
        json.dump(report, sys.stdout, indent=1)
        print()
        return 0
    print(f"{report['player']['slot']} ({report['player']['agent']}) {report['map']} — "
          f"{report['roundsAnalyzed']} rounds")
    for finding in report["findings"]:
        print(f"  [{finding['severity']:>5}] {finding['kind']}: {finding['text']}")
        print("          refs: " + ", ".join(f"r{ref['round']}@{ref['tSec']}s/{ref['src']}"
                                             for ref in finding["refs"][:4]))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))

#!/usr/bin/env python3
"""Tests for the pure-Python normalizer (``bot/replay/normalize.py``).

Runs two ways:

    py -3.12 -m pytest bot/replay/test_normalize.py -q
    py -3.12 bot/replay/test_normalize.py

The synthetic raw export builder below is the single source of truth for the
fixture committed at ``VALOPLANT_WORK/fixtures/raw-demo/`` (regenerate with
``py -3.12 bot/replay/test_normalize.py --write-fixture``). The normalizer itself
stays standard-library-only; pytest is used only as a runner.
"""

from __future__ import annotations

import importlib.util
import json
import math
import os
import shutil
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _REPO_ROOT not in sys.path:      # the real-store checks import bot.replay modules
    sys.path.insert(0, _REPO_ROOT)

_spec = importlib.util.spec_from_file_location(
    "valoplant_normalize", os.path.join(_HERE, "normalize.py"))
normalize = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(normalize)

FIXTURE_DIR = os.path.join(_REPO_ROOT, "VALOPLANT_WORK", "fixtures", "raw-demo")
_TMP_ROOT = os.path.join(_REPO_ROOT, "VALOPLANT_WORK", "tmp", "normalizer-tests")

# --------------------------------------------------------------------------
# Synthetic raw-export fixture (mirrors the CliReader viewer-profile format)
# --------------------------------------------------------------------------

# (codename, agent) in P1..P10 order; codenames match the parser's agent descriptors.
ROSTER = [
    ("Wraith", "Omen"),
    ("Pandemic", "Viper"),
    ("Hunter", "Sova"),
    ("Gumshoe", "Cypher"),
    ("Killjoy", "Killjoy"),
    ("Wushu", "Jett"),
    ("Clay", "Raze"),
    ("Thorne", "Sage"),
    ("Phoenix", "Phoenix"),
    ("Breach", "Breach"),
]
CHAR_GUIDS = [101 + i for i in range(10)]
STATE_GUIDS = [201 + i for i in range(10)]
TEAM = [0] * 5 + [1] * 5
SAMPLE_MS = 200          # raw replication rate in the fixture
BOMB_GAME_STATE_GUID = 900

KILLER = CHAR_GUIDS[1]   # P2
VICTIM = CHAR_GUIDS[6]   # P7
ULT_STATE = STATE_GUIDS[2]   # P3
PLANTER = CHAR_GUIDS[0]      # P1 (team 0 = attackers)
DEFUSER = CHAR_GUIDS[5]      # P6 (team 1 = defenders)
BOMB_HOLDER = CHAR_GUIDS[0]


def _actor_spawned(time_ms, guid, codename):
    path = "/Game/Characters/%s/%s_PC.%s_PC_C" % (codename, codename, codename)
    return {
        "type": "actor_spawned", "time_ms": time_ms, "packet_id": 1,
        "actor_net_guid": guid, "channel": 1, "is_dynamic": True,
        "actor_path": path, "archetype_net_guid": guid + 500, "archetype_path": path,
        "replication_class_path": path, "level_net_guid": 42,
        "location": None, "rotation": None, "scale": None, "velocity": None,
    }


def _rpc(time_ms, path, payload=None, name=None):
    class_path, _, function = path.partition(":")
    return {
        "type": "rpc_received", "time_ms": time_ms, "packet_id": time_ms // 100 + 1,
        "actor_net_guid": BOMB_GAME_STATE_GUID, "object_net_guid": BOMB_GAME_STATE_GUID,
        "channel": 1, "class_path": class_path, "function_name": name or function,
        "function_export_path": path, "function_handle": 1,
        "categories": ["game_state"], "payload_bits": 0, "parsed_bits": 0,
        "was_decoded": True, "decoded_field_count": 0, "payload": payload,
        "diagnostic_fields": [],
    }


def _export_group(time_ms, path, actor_guid, payload, kind="actor"):
    return {
        "type": "export_group_received", "time_ms": time_ms, "packet_id": time_ms // 100 + 1,
        "actor_net_guid": actor_guid, "object_net_guid": actor_guid, "channel": 1,
        "is_actor": True, "is_deleted": False, "delete_flags": 0,
        "export_group_path": path, "kind": kind, "categories": ["game_state"],
        "class_net_guid": actor_guid + 700, "outer_net_guid": 0,
        "object_path": path, "class_path": path, "outer_path": None,
        "payload_bits": 0, "parsed_bits": 0, "was_decoded": True,
        "decoded_field_count": len(payload), "payload": payload, "diagnostic_fields": [],
    }


def _player_state_payload(index, ultimate_active=False):
    return {
        "PlayerId": index + 1,
        "Subject": "subject-%02d" % (index + 1),
        "SpawnedCharacter": CHAR_GUIDS[index],
        "PossessedCharacter": CHAR_GUIDS[index],
        "UltimateActive": ultimate_active,
        "NumUltimatePoints": 0,
        "TotalAcquiredUltimatePoints": 0,
    }


def _movement_row(index, time_ms, x, y, round_start, yaw_zero):
    if index == 0 and time_ms == round_start:
        yaw = yaw_zero                        # exercises yaw normalization (270 -> -90)
    else:
        yaw = ((index * 37) % 360) - 180 + 0.4
    return {
        "type": "remote_character_movement", "time_ms": time_ms,
        "packet_id": time_ms // 100 + 1, "actor_net_guid": CHAR_GUIDS[index],
        "object_net_guid": CHAR_GUIDS[index], "channel": 1,
        "shooter_character_net_guid": CHAR_GUIDS[index],
        "update_index": 0, "move_index": 0,
        "position": {"x": x, "y": y, "z": 90.0},
        "yaw": yaw, "pitch": 0.0, "velocity": None, "timestamp": time_ms,
        "movement_state": 0, "mode_flags": 0, "marker": 0, "move_type": 0,
        "rotation_input": {"x": 0.0, "y": 0.0, "z": 0.0},
        "rotation_yaw_multiplier": 0.0, "has_optional_movement_value": False,
        "optional_movement_raw_byte": None, "optional_movement_value": None,
        "flag48": False, "packed_angles": 0, "raw_yaw": int(yaw), "raw_pitch": 0,
        "variant0_has_external_character_ref": None, "variant0_packed_angles": None,
        "variant1_flag": None, "error_sentinel": False,
    }


def _movement_rows(round_start, round_end, dead_from=None, yaw_zero=270.0,
                   carry=None, spawn_delay_ms=0):
    """Movement rows for one round, plus each player's last position in the round.

    ``carry`` (the previous round's last position per player index) together with
    ``spawn_delay_ms`` > 0 reproduces the real export's round boundary: the round
    marker has already fired while the players are still standing where the
    previous round left them, and the spawn teleport only lands
    ``spawn_delay_ms`` after the marker. Without ``carry`` the movement stream
    simply starts late -- the shape round 1 has in the real replay, where the
    round's opening frames are alive without any x/y.
    """
    rows = []
    ends = []
    for index in range(10):
        start_x = -4000 + (index % 5) * 350 if TEAM[index] == 0 else 4200 + ((index - 5) % 5) * 320
        start_y = -3000 + ((index + 2) % 5) * 400 if TEAM[index] == 0 else 3500 - ((index - 5) % 5) * 300
        end_x = start_x + (2400 if TEAM[index] == 0 else -2600)
        end_y = start_y + (1800 if TEAM[index] == 0 else -1500)
        x, y = start_x, start_y
        time_ms = round_start
        if spawn_delay_ms > 0:
            spawn_ms = min(round_start + spawn_delay_ms, round_end + 1)
            if carry is not None:
                carry_x, carry_y = carry[index]
                while time_ms < spawn_ms and time_ms <= round_end:
                    rows.append(_movement_row(index, time_ms, carry_x, carry_y,
                                              round_start, yaw_zero))
                    time_ms += SAMPLE_MS
            else:
                time_ms = spawn_ms        # no previous round: no positions at all yet
        path_start = time_ms
        path_span = max(1, round_end - path_start)
        while time_ms <= round_end:
            ratio = (time_ms - path_start) / float(path_span)
            x = start_x + (end_x - start_x) * ratio
            y = start_y + (end_y - start_y) * ratio
            rows.append(_movement_row(index, time_ms, x, y, round_start, yaw_zero))
            time_ms += SAMPLE_MS
        ends.append((x, y))
    rows.sort(key=lambda row: (row["time_ms"], row["shooter_character_net_guid"]))
    return rows, ends


def _round_outcome(time_ms, number, team, role, end_type, red_score, blue_score):
    """Parser overlay record (P3/P2-A ``round_outcome``) as events.ndjson carries it."""
    return {
        "type": "round_outcome", "time_ms": time_ms, "packet_id": time_ms // 100 + 1,
        "round_number": number, "winner_team": team, "winner_role": role,
        "end_type": end_type, "red_score": red_score, "blue_score": blue_score,
        "source_actor_net_guid": BOMB_GAME_STATE_GUID,
        "source_export_group_path": normalize.PATH_BOMB_GAME_STATE,
    }


def _life_change(time_ms, guid, component, life, delta, alive_after, kind="damage"):
    """Parser overlay record (P3/P2-A ``player_life_change``)."""
    return {
        "type": "player_life_change", "time_ms": time_ms, "packet_id": time_ms // 100 + 1,
        "character_net_guid": guid,
        "character_class_path": "/Game/Characters/Fixture/Fixture_PC",
        "damageable_component_net_guid": guid + 500,
        "changed_component_net_guid": component,
        "life_result": life, "delta_life": delta, "alive_after_change": alive_after,
        "change_kind": kind,
        "source_function_name": "MulticastNotifyDamage_Base",
    }


def build_events(rounds, side_switch_after=None, kills_from_damage=False,
                 outcome_source="snapshot", extra_kills=(), extra_plants=(),
                 life_changes=(), attribute_sets=True):
    """Build the events.ndjson records for the given round windows.

    ``rounds`` is a list of (n, start_ms, end_ms). The first round always carries
    a kill, a plant and a defuse; later rounds carry a kill only.

    ``outcome_source`` says how the replay states each round's outcome:

    * ``"snapshot"`` -- a BombGameState ``RoundResults`` entry (the pre-overlay
      export, and what the 13.06 Haven store export carries);
    * ``"rows"`` -- a flat ``round_outcome`` record per round (the P3/P2-A overlay);
    * ``"none"`` -- no outcome at all; the normalizer must then infer or say unknown.

    ``extra_kills`` are (round_number, time_ms, killer_guid, victim_guid) tuples,
    ``extra_plants`` rounds that get a plant RPC, ``life_changes`` raw
    ``player_life_change`` payloads (use ``_life_change``), ``attribute_sets``
    False drops the AresAttributeSet Health rows.
    """
    events = []
    if attribute_sets:
        for index in range(10):
            events.append(_actor_spawned(rounds[0][1], CHAR_GUIDS[index], ROSTER[index][0]))
            events.append(_export_group(
                rounds[0][1], "/Game/GameModes/Bomb/BombPlayerState.BombPlayerState_C",
                STATE_GUIDS[index], _player_state_payload(index)))
            events.append(_export_group(
                rounds[0][1], "/Script/ShooterGame.AresAttributeSet", CHAR_GUIDS[index],
                {"Health": 100.0, "MaxHealth": 100.0, "Shield": 0.0, "MaxShield": 0.0},
                kind="attribute_set"))
    else:
        for index in range(10):
            events.append(_actor_spawned(rounds[0][1], CHAR_GUIDS[index], ROSTER[index][0]))
            events.append(_export_group(
                rounds[0][1], "/Game/GameModes/Bomb/BombPlayerState.BombPlayerState_C",
                STATE_GUIDS[index], _player_state_payload(index)))

    scores = [0, 0]          # team 0 -> red, team 1 -> blue (fixture convention)
    for position, (number, start, end) in enumerate(rounds):
        first = position == 0
        events.append(_rpc(start, normalize.RPC_ROUND_START))
        events.append(_export_group(start, normalize.PATH_BOMB_GAME_STATE, BOMB_GAME_STATE_GUID,
                                    {"RoundNumber": number, "Phase": 3, "BombState": 0,
                                     "RoundResults": []}))
        events.append(_export_group(min(start + 2000, end), normalize.PATH_BOMB_GAME_STATE,
                                    BOMB_GAME_STATE_GUID,
                                    {"RoundNumber": number, "Phase": 4, "BombState": 0,
                                     "RoundResults": []}))
        if first:
            events.append(_rpc(start + 1500, normalize.RPC_BOMB_PICKED_UP,
                               {"NewBombHolder": BOMB_HOLDER}))
            kill_time = start + 22000
            events.append(_rpc(kill_time, normalize.RPC_KILLED_ENEMY,
                               {"KillerCharacter": KILLER, "KilledCharacter": VICTIM,
                                "MultikillLevel": 0}))
            events.append(_export_group(kill_time + 100, "/Script/ShooterGame.AresAttributeSet",
                                        VICTIM, {"Health": 0.0, "MaxHealth": 100.0}))
            events.append(_export_group(start + 30000, "/Game/GameModes/Bomb/BombPlayerState.BombPlayerState_C",
                                        STATE_GUIDS[2], _player_state_payload(2, ultimate_active=True)))
            events.append(_rpc(start + 41000, normalize.RPC_BOMB_PLANTED,
                               {"PlantLocation": {"x": -1200.4, "y": 2600.6, "z": 90.0},
                                "BombPlanter": PLANTER, "PlantSite": 0}))
            events.append(_rpc(start + 66000, normalize.RPC_BOMB_DEFUSED,
                               {"DefusingCharacter": DEFUSER}))
            role = "defender"
            end_type = "defuse"
        else:
            events.append(_rpc(start + 30000, normalize.RPC_KILLED_ENEMY,
                               {"KillerCharacter": CHAR_GUIDS[7], "KilledCharacter": CHAR_GUIDS[2],
                                "MultikillLevel": 0}))
            role = "attacker"
            end_type = "elimination"
        if number in extra_plants:
            events.append(_rpc(start + 41000, normalize.RPC_BOMB_PLANTED,
                               {"PlantLocation": {"x": -1100.0, "y": 2500.0, "z": 90.0},
                                "BombPlanter": PLANTER, "PlantSite": 0}))
        for entry in extra_kills:
            if entry[0] != number:
                continue
            events.append(_rpc(entry[1], normalize.RPC_KILLED_ENEMY,
                               {"KillerCharacter": entry[2], "KilledCharacter": entry[3],
                                "MultikillLevel": 0}))
        # Winner team index for the fixture: team 0 attacks every round (no side switch).
        winner = 0 if role == "attacker" else 1
        scores[winner] += 1
        if outcome_source == "snapshot":
            events.append(_export_group(end - 500, normalize.PATH_BOMB_GAME_STATE, BOMB_GAME_STATE_GUID,
                                        {"RoundNumber": number, "Phase": 5, "BombState": 1,
                                         "RoundResults": [{"Index": 0, "RoundNumber": number - 1,
                                                           "WinningTeam": "Team_%d" % number,
                                                           "WinningTeamRole": role,
                                                           "RoundResult": end_type}]}))
        elif outcome_source == "rows":
            events.append(_export_group(end - 500, normalize.PATH_BOMB_GAME_STATE, BOMB_GAME_STATE_GUID,
                                        {"RoundNumber": number, "Phase": 5, "BombState": 1}))
            events.append(_round_outcome(end - 400, number, "Red" if winner == 0 else "Blue",
                                         role, end_type, scores[0], scores[1]))
        events.append(_rpc(end, normalize.RPC_END_ROUND, {"NewRoundNumber": number}))
        if side_switch_after == number:
            events.append(_rpc(end + 500, normalize.RPC_SIDE_SWITCH, None,
                               name="Multicast Side Switch Event"))

    for record in life_changes:
        events.append(dict(record))
    if kills_from_damage:
        events = [record for record in events
                  if record.get("function_export_path") != normalize.RPC_KILLED_ENEMY]
        events = [record for record in events if record.get("type") != "round_outcome"]
        events.append(_rpc(rounds[0][1] + 22000, normalize.RPC_DAMAGE_BASE, {
            "DamageTaken": 150.0, "DamageDealt": 150.0, "DamageKilledTarget": True,
            "AliveAfterDamage": False, "Character": VICTIM,
            "KillCreditPlayerState": STATE_GUIDS[1], "DamagerPlayerState": STATE_GUIDS[1],
            "EquippableUsed": {"net_guid": 1, "name": "Vandal", "category": "rifle",
                               "class_path": "/Game/Weapons/...Vandal"},
        }))
    events.sort(key=lambda record: (record["time_ms"], record["type"]))
    return events


def build_export(dest, rounds=((1, 0, 92000),), side_switch_after=None, with_manifest=True,
                 kills_from_damage=False, with_events=True, with_movement=True,
                 with_pipeline_manifest=False, spawn_delay_ms=0, outcome_source="snapshot",
                 extra_kills=(), extra_plants=(), life_changes=(), attribute_sets=True,
                 with_duration=None):
    """Write a synthetic CliReader viewer-profile export directory.

    ``spawn_delay_ms`` > 0 shifts the export to the real round-boundary shape: the
    round marker fires ``spawn_delay_ms`` before the players are teleported onto
    their spawns, so a round's opening frames still hold the previous round's end
    positions (and round 1 opens with alive-but-position-less frames).
    """
    os.makedirs(dest, exist_ok=True)
    if with_pipeline_manifest:
        _write_json(os.path.join(os.path.dirname(os.path.abspath(dest)), "manifest.json"), {
            "schema": "valoplant_manifest/v2",
            "id": os.path.basename(os.path.dirname(os.path.abspath(dest))),
            "source": "replays/bdb6b4ab-fa31-4e51-b77a-35e3a1e7a3c2.vrf",
            "branch": "++Ares-Core+release-13.06",
            "map": {"name": "Haven", "path": "/Game/Maps/Triad/Triad"},
            "exportProfile": "viewer",
            "parserCommit": "fixture0001",
            "status": "ready",
            "decodedAt": "2026-10-13T00:00:00Z",
            "notes": "synthetic",
        })
    if with_manifest:
        manifest = {
            "schema_version": 4,
            "source_file": "bdb6b4ab-fa31-4e51-b77a-35e3a1e7a3c2.vrf",
            "source_sha256": "b" * 64,
            "source_size_bytes": 48434398,
            "replay_build": "++Ares-Core+release-13.06",
            "replay_version": "13.06.0",
            "replay_changelist": 123456,
            "duration_ms": rounds[-1][2],
            "parse_profile": "viewer",
            "parser_assembly": "Replay.Valorant",
            "parser_version": "0.1.0+fixture",
            "stats": {"packet_count": 0},
            "counts": {"movement": 0, "events": 0},
            "net_field_export_groups": [],
            "filtered_export_group_summary": [],
            "limitations": ["Synthetic fixture."],
        }
        _write_json(os.path.join(dest, "manifest.json"), manifest)
    if with_events:
        records = build_events(rounds, side_switch_after=side_switch_after,
                               kills_from_damage=kills_from_damage,
                               outcome_source=outcome_source, extra_kills=extra_kills,
                               extra_plants=extra_plants, life_changes=life_changes,
                               attribute_sets=attribute_sets)
        _write_ndjson(os.path.join(dest, "events.ndjson"), records)
    if with_movement:
        rows = []
        carry = None
        for _number, start, end in rounds:
            round_rows, ends = _movement_rows(
                start, end, carry=carry if spawn_delay_ms > 0 else None,
                spawn_delay_ms=spawn_delay_ms)
            carry = ends
            rows.extend(round_rows)
        rows.sort(key=lambda row: (row["time_ms"], row["shooter_character_net_guid"]))
        _write_ndjson(os.path.join(dest, "movement.ndjson"), rows)
    return dest


def _write_json(path, value):
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
        handle.write("\n")


def _write_ndjson(path, records):
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")


# --------------------------------------------------------------------------
# Harness
# --------------------------------------------------------------------------

_CLEANUPS = []


def make_case(name, **kwargs):
    """Build a fixture in a scratch dir, normalize it, return (raw, viewer, stats)."""
    base = os.path.join(_TMP_ROOT, name)
    if os.path.isdir(base):
        shutil.rmtree(base)
    os.makedirs(base, exist_ok=True)
    _CLEANUPS.append(base)
    raw = build_export(os.path.join(base, "raw"), **kwargs)
    viewer = os.path.join(base, "viewer")
    stats = normalize.normalize_directory(raw, viewer, match_id="fixture",
                                          normalized_at="2026-10-13T00:00:00Z")
    return raw, viewer, stats


def read_json(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------

def test_one_round_schema_and_round_slicing():
    _raw, viewer, stats = make_case("one-round", with_pipeline_manifest=True)
    match = read_json(os.path.join(viewer, "match.json"))
    assert match["schema"] == "valoplant_match/v2", match["schema"]
    assert list(match.keys()) == ["schema", "id", "source", "sha256", "patch", "branch", "map",
                                  "durationMs", "decode", "capabilities", "players",
                                  "utilityCounts", "rounds"], list(match.keys())
    # P7-1A: the tracker hook advertises itself here (P7_ABILITY_TRACKING_PLAN.md, F)
    assert match["capabilities"] == {"utility": 1}
    assert match["id"] == "fixture"
    assert match["patch"] == "13.06", match["patch"]
    assert match["branch"] == "++Ares-Core+release-13.06"
    assert match["sha256"] == "b" * 64
    assert match["source"].endswith("bdb6b4ab-fa31-4e51-b77a-35e3a1e7a3c2.vrf")
    assert match["durationMs"] == 92000
    assert match["map"]["name"] == "Haven" and match["map"]["uuid"] == \
        "2bee0dc9-4ffe-519b-1cbd-7fbe763a6047"
    assert match["map"]["xMultiplier"] == 7.5e-05
    assert match["decode"]["normalizedAt"] == "2026-10-13T00:00:00Z"
    assert match["decode"]["parserCommit"] == "fixture0001"
    assert match["decode"]["status"] == "ready"

    player_ids = [player["id"] for player in match["players"]]
    assert player_ids == ["P%d" % n for n in range(1, 11)], player_ids
    # P7-3: match-level counts cover every player, this fixture throws nothing
    assert sorted(match["utilityCounts"]) == sorted(player_ids), list(match["utilityCounts"])
    assert all(row["total"] == 0 for row in match["utilityCounts"].values())
    assert list(match["players"][0].keys()) == ["id", "name", "agent", "agentUuid", "team",
                                                "subject", "confidence"]
    assert [player["team"] for player in match["players"]] == [0] * 5 + [1] * 5
    assert [player["agent"] for player in match["players"]] == [name for _, name in ROSTER]
    assert match["players"][0]["confidence"] == "loadout"
    assert match["players"][0]["subject"] == "subject-01"
    assert match["players"][0]["name"] is None
    # P8-B1 / plan decision D9: agentUuid is resolved from bot/replay/agent_ids.json,
    # so it is no longer null for a known agent (players[0] is Omen in this fixture).
    assert match["players"][0]["agentUuid"] == "8e253930-4c05-31dd-1b6c-968525494517"

    assert len(match["rounds"]) == 1
    summary = match["rounds"][0]
    assert list(summary.keys()) == ["n", "startMs", "endMs", "winner", "endType", "kills",
                                   "plants", "defuses", "attackerTeam",
                                   "attackerTeamConfidence", "confidence"]
    assert summary["n"] == 1 and summary["startMs"] == 0 and summary["endMs"] == 92000
    assert summary["winner"] == 1, summary        # defenders won: team 1
    assert summary["endType"] == "defuse"
    assert summary["kills"] == 1 and summary["plants"] == 1 and summary["defuses"] == 1
    assert summary["confidence"] == "replay"
    assert stats["rounds"] == 1 and stats["players"] == 10
    assert stats["team0"] == 0

    os_files = sorted(os.listdir(os.path.join(viewer, "rounds")))
    assert os_files == ["r01.json"], os_files


def test_round_document_frames_resample_and_spike():
    _raw, viewer, _stats = make_case("round-doc")
    round_doc = read_json(os.path.join(viewer, "rounds", "r01.json"))
    assert round_doc["schema"] == "valoplant_round/v2"
    assert list(round_doc.keys()) == ["schema", "n", "startMs", "endMs", "tickMs", "frames",
                                     "players", "events", "spike", "utility"]
    # P7-1A: `utility` is an added-at-the-end optional key; this fixture has no
    # ability actors, so the list is empty (schema stays valoplant_round/v2).
    assert round_doc["utility"] == [], round_doc["utility"]
    assert round_doc["tickMs"] == 100
    assert round_doc["frames"] == 921, round_doc["frames"]      # 92000/100 + 1
    assert sorted(round_doc["players"].keys(), key=lambda name: int(name[1:])) == \
        ["P%d" % n for n in range(1, 11)]

    player = round_doc["players"]["P1"]
    assert list(player.keys()) == ["x", "y", "yaw", "hp", "alive"], list(player.keys())
    for key in ("x", "y", "yaw", "hp", "alive"):
        assert len(player[key]) == round_doc["frames"], key
    # 10 Hz resample of 200 ms raw samples: frame 1 (t=100) holds the t=0 sample.
    assert player["x"][0] == round_doc["players"]["P1"]["x"][1]
    assert player["y"][0] == round_doc["players"]["P1"]["y"][1]
    assert player["x"][2] != player["x"][0]
    assert player["x"][0] == normalize.iround(player["x"][0])
    assert player["yaw"][0] == -90, player["yaw"][0]              # 270 normalizes to -90
    assert all(-180 <= value <= 180 for value in player["yaw"] if value is not None)

    spike = round_doc["spike"]
    assert list(spike.keys()) == ["plantT", "plantX", "plantY", "defuseT", "site"]
    assert spike["plantT"] == 41000 and spike["defuseT"] == 66000
    assert spike["plantX"] == -1200 and spike["plantY"] == 2601   # away-from-zero rounding
    assert spike["site"] is None                                  # PlantSite byte not proven

    events = round_doc["events"]
    kinds = [event["type"] for event in events]
    assert kinds == ["kill", "ult", "plant", "defuse"], kinds
    assert list(events[0].keys()) == ["t", "type", "actor", "target", "weapon", "ability",
                                      "confidence"]
    kill = events[0]
    assert kill["t"] == 22000 and kill["actor"] == "P2" and kill["target"] == "P7"
    assert kill["weapon"] is None and kill["ability"] is None
    assert kill["confidence"] == "replay"
    ult = events[1]
    assert ult["t"] == 30000 and ult["actor"] == "P3" and ult["confidence"] == "replay"
    plant = events[2]
    assert plant["t"] == 41000 and plant["actor"] == "P1" and plant["target"] is None
    defuse = events[3]
    assert defuse["t"] == 66000 and defuse["actor"] == "P6"

    # P7 (character guid 107) dies at 22.1 s: hp 0 and alive 0 from that frame on.
    victim = round_doc["players"]["P7"]
    assert victim["alive"][219] == 1 and victim["alive"][221] == 0
    assert victim["hp"][221] == 0
    assert round_doc["players"]["P1"]["alive"][0] == 1


def test_two_rounds_slicing_and_side_switch():
    rounds = ((1, 0, 92000), (2, 100000, 178000))
    _raw, viewer, stats = make_case("two-rounds", rounds=rounds, side_switch_after=1)
    match = read_json(os.path.join(viewer, "match.json"))
    assert sorted(os.listdir(os.path.join(viewer, "rounds"))) == ["r01.json", "r02.json"]
    assert [summary["n"] for summary in match["rounds"]] == [1, 2]
    assert match["rounds"][1]["startMs"] == 100000
    assert match["rounds"][1]["endMs"] == 178000
    # Round 1 was won by the defenders (team 1); round 2 by the attackers, who
    # switched sides after the round-1 side-switch event -> team 1.
    assert match["rounds"][0]["winner"] == 1
    assert match["rounds"][1]["winner"] == 1, match["rounds"][1]
    assert stats["swap_round"] == 1
    second = read_json(os.path.join(viewer, "rounds", "r02.json"))
    assert second["frames"] == 781                        # 78000/100 + 1
    assert second["spike"]["plantT"] is None
    assert [event["type"] for event in second["events"]] == ["kill"]
    assert [event["t"] for event in second["events"]] == [30000]


def test_round_start_frames_are_spawn_frames_not_previous_round():
    """A round sliced from its boundary marker must not open on stale frames.

    The fixture reproduces the real export's round boundary: the marker (round
    start RPC + BombGameState snapshot) fires while the players are still standing
    where the previous round left them, and the spawn teleport only lands
    ``spawn_delay_ms`` later. Round 1 has no previous round, so its movement
    stream starts late -- the real replay's alive-but-position-less opening rows,
    which used to leave frame 0 completely empty.
    """
    rounds = ((1, 0, 92000), (2, 100000, 178000))
    _raw, viewer, stats = make_case("round-start-snap", rounds=rounds, spawn_delay_ms=2000)
    match = read_json(os.path.join(viewer, "match.json"))
    first = read_json(os.path.join(viewer, "rounds", "r01.json"))
    second = read_json(os.path.join(viewer, "rounds", "r02.json"))

    # Both rounds start at their spawn teleport, not at the boundary marker.
    assert match["rounds"][0]["startMs"] == 2000, match["rounds"][0]
    assert match["rounds"][1]["startMs"] == 102000, match["rounds"][1]
    assert second["startMs"] == 102000 and second["n"] == 2
    assert second["frames"] == 761                      # 178000-102000 over 100 ms ticks
    assert match["rounds"][1]["endMs"] == 178000        # round end marker is not moved
    assert stats["rounds"] == 2
    assert any("spawn teleport" in warning for warning in stats["warnings"]), stats["warnings"]

    # r01 frame 0 is finite for every player (it used to be null for all ten).
    for player_id, track in first["players"].items():
        assert track["x"][0] is not None and track["y"][0] is not None, player_id
        assert first["startMs"] == 2000

    team = {player["id"]: player["team"] for player in match["players"]}

    # Frames 0-2 of r02 hold that round's spawn positions: within the spawn
    # window the players do not move, and no frame still carries the previous
    # round's end position the boundary marker used to open on.
    for player_id, track in second["players"].items():
        x0, y0 = track["x"][0], track["y"][0]
        assert x0 is not None and y0 is not None, player_id
        for frame in (1, 2):
            assert math.hypot(track["x"][frame] - x0, track["y"][frame] - y0) <= 200, \
                (player_id, frame)
        previous = first["players"][player_id]
        last = None
        for frame in range(len(previous["x"]) - 1, -1, -1):
            if previous["x"][frame] is not None:
                last = (previous["x"][frame], previous["y"][frame])
                break
        assert last is not None, player_id
        assert math.hypot(x0 - last[0], y0 - last[1]) > 800, (player_id, last, (x0, y0))

    # ...and the two teams open the round separated, as the real probe requires.
    centroids = {}
    for player_id, track in second["players"].items():
        centroids.setdefault(team[player_id], []).append((track["x"][0], track["y"][0]))
    assert sorted(centroids) == [0, 1], centroids
    means = []
    for side in (0, 1):
        points = centroids[side]
        means.append((sum(point[0] for point in points) / float(len(points)),
                      sum(point[1] for point in points) / float(len(points))))
    separation = math.hypot(means[0][0] - means[1][0], means[0][1] - means[1][1])
    assert separation >= 8000, separation


def test_side_switch_defaults_to_round_twelve():
    scan = normalize.RawScan()
    info = normalize.PlayerInfo(500)
    info.codename = "Wraith"
    info.agent = "Omen"
    players = {"P1": info}
    clusters = {"P1": 0}
    rounds = [(number, (number - 1) * 100000, number * 100000, None) for number in range(1, 14)]
    attacker_team, team0, swap_round, evidence = normalize.build_side_map(
        scan, players, clusters, rounds)
    assert swap_round == normalize.SIDE_SWITCH_DEFAULT_ROUND == 12
    assert team0 == 0 and evidence == {}
    assert attacker_team[12] == 0 and attacker_team[13] == 1
    assert any("halftime" in warning for warning in scan.warnings)

    short, _team0, short_swap, _evidence = normalize.build_side_map(
        normalize.RawScan(), players, clusters, rounds[:1])
    assert short_swap is None and short[1] == 0


def test_deterministic_output_bytes():
    _raw1, viewer1, _ = make_case("determinism-a")
    _raw2, viewer2, _ = make_case("determinism-b")
    assert _read_bytes(os.path.join(viewer1, "match.json")) == \
        _read_bytes(os.path.join(viewer2, "match.json"))
    assert _read_bytes(os.path.join(viewer1, "rounds", "r01.json")) == \
        _read_bytes(os.path.join(viewer2, "rounds", "r01.json"))
    # The wall clock is the only non-deterministic field; without a pinned value
    # everything else must still match.
    raw = os.path.join(_TMP_ROOT, "determinism-b", "raw")
    viewer = os.path.join(_TMP_ROOT, "determinism-b", "viewer-unpinned")
    normalize.normalize_directory(raw, viewer, match_id="fixture")
    unpinned = read_json(os.path.join(viewer, "match.json"))
    pinned = read_json(os.path.join(viewer1, "match.json"))
    unpinned["decode"]["normalizedAt"] = pinned["decode"]["normalizedAt"]
    assert unpinned == pinned


def _read_bytes(path):
    with open(path, "rb") as handle:
        return handle.read()


def test_kill_fallback_from_damage_report():
    _raw, viewer, stats = make_case("damage-kills", kills_from_damage=True)
    round_doc = read_json(os.path.join(viewer, "rounds", "r01.json"))
    kills = [event for event in round_doc["events"] if event["type"] == "kill"]
    assert len(kills) == 1, round_doc["events"]
    assert kills[0]["actor"] == "P2" and kills[0]["target"] == "P7"
    assert kills[0]["confidence"] == "unknown"
    assert any("MulticastNotifyKilledEnemy" in warning for warning in stats["warnings"])
    victim = round_doc["players"]["P7"]
    assert victim["alive"][0] == 1 and victim["alive"][-1] == 0


def _rewrite_events(raw_dir, transform):
    """Rewrite events.ndjson in place through ``transform(records)``."""
    path = os.path.join(raw_dir, "events.ndjson")
    with open(path, "r", encoding="utf-8") as handle:
        records = [json.loads(line) for line in handle if line.strip()]
    _write_ndjson(path, transform(records))


def _make_case_with_edit(name, transform, **kwargs):
    """Build a fixture, rewrite its events.ndjson through ``transform``, then normalize."""
    base = os.path.join(_TMP_ROOT, name)
    if os.path.isdir(base):
        shutil.rmtree(base)
    os.makedirs(base, exist_ok=True)
    _CLEANUPS.append(base)
    raw = build_export(os.path.join(base, "raw"), **kwargs)
    _rewrite_events(raw, transform)
    viewer = os.path.join(base, "viewer")
    stats = normalize.normalize_directory(raw, viewer, match_id="fixture",
                                          normalized_at="2026-10-13T00:00:00Z")
    return raw, viewer, stats


def test_end_type_vocabulary():
    assert normalize.normalize_end_type("time_expired") == "time"
    assert normalize.normalize_end_type("Elimination") == "elimination"
    assert normalize.normalize_end_type("detonate") in normalize.ROUND_END_TYPES
    assert normalize.normalize_end_type("surrendered") == "surrendered"   # reported verbatim
    assert normalize.normalize_end_type(None) is None
    assert normalize.normalize_end_type("") is None


def test_round_outcome_rows_are_replay_proven():
    """Parser overlay ``round_outcome`` rows drive winner/endType as confidence replay."""
    rounds = ((1, 0, 92000), (2, 100000, 178000))
    _raw, viewer, stats = make_case("outcome-rows", rounds=rounds, outcome_source="rows")
    match = read_json(os.path.join(viewer, "match.json"))
    assert stats["round_outcome_sources"] == {"round_outcome": 2}, stats["round_outcome_sources"]
    assert stats["round_confidence"] == {"replay": 2}, stats["round_confidence"]
    assert stats["round_outcome_rows"] == 2
    first, second = match["rounds"]
    assert first["winner"] == 1 and first["endType"] == "defuse", first
    assert second["winner"] == 0 and second["endType"] == "elimination", second
    assert first["confidence"] == "replay" and second["confidence"] == "replay"
    assert not [w for w in stats["warnings"] if "round_outcome" in w], stats["warnings"]


def test_round_outcome_row_wins_over_bomb_game_state_and_disagreement_warns():
    """Two replay-proven sources that disagree: the flat row wins and it is reported."""
    def add_conflicting_snapshot(records):
        for record in records:
            payload = record.get("payload")
            if (record.get("type") == "export_group_received"
                    and record.get("export_group_path") == normalize.PATH_BOMB_GAME_STATE
                    and isinstance(payload, dict) and payload.get("RoundNumber") == 1):
                payload["RoundResults"] = [{
                    "Index": 0, "RoundNumber": 0, "WinningTeam": "Team_1",
                    "WinningTeamRole": "attacker", "RoundResult": "elimination"}]
        return records

    _raw, viewer, stats = _make_case_with_edit(
        "outcome-conflict", add_conflicting_snapshot, outcome_source="rows")
    summary = read_json(os.path.join(viewer, "match.json"))["rounds"][0]
    assert summary["winner"] == 1, summary              # the row says defenders (team 1)
    assert summary["endType"] == "defuse", summary
    assert summary["confidence"] == "replay"
    assert any("outcome sources disagree" in warning for warning in stats["warnings"]), \
        stats["warnings"]


def test_inferred_outcome_from_events_when_the_replay_states_none():
    """No exported outcome: infer the three provable end types, else say unknown."""
    rounds = ((1, 0, 92000), (2, 100000, 178000), (3, 186000, 264000), (4, 272000, 350000))
    attackers = CHAR_GUIDS[:5]
    defenders = CHAR_GUIDS[5:]
    extra_kills = [(2, 120000 + index * 100, defenders[0], attacker)
                   for index, attacker in enumerate(attackers)]
    _raw, viewer, stats = make_case("inferred-outcome", rounds=rounds, outcome_source="none",
                                    extra_kills=extra_kills, extra_plants=(3,))
    match = read_json(os.path.join(viewer, "match.json"))
    first, second, third, fourth = match["rounds"]
    assert first["winner"] == 1 and first["endType"] == "defuse", first
    assert second["winner"] == 1 and second["endType"] == "elimination", second
    assert third["winner"] == 0 and third["endType"] == "detonate", third
    assert fourth["winner"] is None and fourth["endType"] is None, fourth
    assert fourth["confidence"] == "unknown"
    assert stats["round_confidence"] == {"inferred": 3, "unknown": 1}, stats["round_confidence"]
    assert stats["round_outcome_rows"] == 0
    assert not any("disagree" in warning for warning in stats["warnings"]), stats["warnings"]


def test_player_life_change_hp_uses_the_health_component_only():
    """hp comes from the character's health component; shields are not hp, no armour."""
    life_changes = [
        # P1 (guid 101): health component 901 reaches 100 -> that is hp.
        _life_change(0, CHAR_GUIDS[0], 901, 100.0, 100.0, True, "section"),
        _life_change(20000, CHAR_GUIDS[0], 901, 60.0, -40.0, True, "damage"),
        _life_change(22000, CHAR_GUIDS[0], 901, 0.0, -60.0, False, "damage"),
        # P1 shield component 900 never passes 50 -> must not become hp.
        _life_change(21000, CHAR_GUIDS[0], 900, 25.0, -25.0, True, "damage"),
        # P2 (guid 102): shield rows only -> no provable health component, no hp key.
        _life_change(20000, CHAR_GUIDS[1], 1000, 50.0, -50.0, True, "damage"),
    ]
    _raw, viewer, stats = make_case("life-change-hp", life_changes=life_changes,
                                    attribute_sets=False, outcome_source="none")
    round_doc = read_json(os.path.join(viewer, "rounds", "r01.json"))
    p1 = round_doc["players"]["P1"]
    assert list(p1.keys()) == ["x", "y", "yaw", "hp", "alive"], list(p1.keys())
    assert p1["hp"][0] == 100 and p1["hp"][200] == 60, p1["hp"][:3]
    assert p1["hp"][215] == 60          # zero-order hold between life changes
    assert p1["hp"][220] == 0
    assert "armor" not in p1 and "shield" not in p1
    assert "hp" not in round_doc["players"]["P2"], round_doc["players"]["P2"]
    assert stats["life_changes"] == 5
    assert stats["hp_players"] == 2      # P1 (life changes) + P7 (attribute-set fallback)
    assert stats["rounds_with_hp_samples"] == 1


def test_movement_only_export_degrades_honestly():
    _raw, viewer, stats = make_case("movement-only", with_events=False, with_manifest=False)
    match = read_json(os.path.join(viewer, "match.json"))
    assert len(match["rounds"]) == 1
    assert match["rounds"][0]["confidence"] == "unknown"
    assert match["rounds"][0]["winner"] is None
    assert match["rounds"][0]["endType"] is None
    assert all(player["team"] is None or isinstance(player["team"], int)
               for player in match["players"])
    round_doc = read_json(os.path.join(viewer, "rounds", "r01.json"))
    assert round_doc["events"] == []
    assert round_doc["spike"] == {"plantT": None, "plantX": None, "plantY": None,
                                  "defuseT": None, "site": None}
    assert any("no round markers" in warning for warning in stats["warnings"])
    assert any("raw/manifest.json missing" in warning for warning in stats["warnings"])
    # No sibling pipeline manifest and no map string in the export: map is null, not guessed.
    assert match["map"]["name"] is None and match["map"]["uuid"] is None
    assert any("map not identifiable" in warning for warning in stats["warnings"])


def test_missing_input_raises():
    base = os.path.join(_TMP_ROOT, "empty")
    os.makedirs(base, exist_ok=True)
    _CLEANUPS.append(base)
    try:
        normalize.normalize_directory(base, os.path.join(base, "viewer"))
    except normalize.NormalizeError as exc:
        assert "events.ndjson" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected NormalizeError for an empty raw dir")


def test_module_is_standard_library_only():
    with open(os.path.join(_HERE, "normalize.py"), "r", encoding="utf-8") as handle:
        source = handle.read()
    imported = sorted(line.split()[1].split(".")[0] for line in source.splitlines()
                      if line.startswith("import "))
    allowed = {"bisect", "datetime", "gzip", "json", "math", "os", "re", "sys",
               "typing"}   # typing: used by RawScan's annotations (P7-1A)
    assert set(imported) <= allowed, imported
    for banned in ("pandas", "numpy", "json5", "orjson"):
        assert banned not in source


# --------------------------------------------------------------------------
# Real-store checks (the 13.06 Haven replay in data/replays/bad1543033d11396)
# --------------------------------------------------------------------------

# The replay must state every round's outcome, and the four cross-checks of the
# plan contract must hold. One normalization is shared by every real-store test;
# it is read-only over the store and writes its viewer output under WORK/tmp.
REAL_RAW = os.path.join(_REPO_ROOT, "data", "replays", "bad1543033d11396", "raw")
REAL_VRF = os.path.join(_REPO_ROOT, "replays", "bdb6b4ab-fa31-4e51-b77a-35e3a1e7a3c2.vrf")
_REAL_RUN = {}


def real_run():
    """Normalize the real Haven store export once; None when the store is absent."""
    if "result" not in _REAL_RUN:
        if not os.path.isdir(REAL_RAW):
            _REAL_RUN["result"] = None
            return None
        viewer = os.path.join(_TMP_ROOT, "real-run", "viewer")
        stats = normalize.normalize_directory(REAL_RAW, viewer, match_id="bad1543033d11396",
                                              normalized_at="2026-10-14T00:00:00Z")
        match = read_json(os.path.join(viewer, "match.json"))
        _REAL_RUN["result"] = (viewer, stats, match)
    return _REAL_RUN["result"]


def _skip_real():
    sys.stdout.write("skipped: real store raw not present at %s\n" % REAL_RAW)


def test_real_replay_round_count_when_raw_present():
    run = real_run()
    if run is None:
        _skip_real()
        return
    viewer, stats, match = run
    assert stats["rounds"] == 18, stats
    assert match["map"]["name"] == "Haven"
    assert [player["id"] for player in match["players"]] == ["P%d" % n for n in range(1, 11)]
    first = read_json(os.path.join(viewer, "rounds", "r01.json"))
    sys.stdout.write("real run: rounds=%d frames_r01=%d confidence=%s warnings=%d\n"
                     % (stats["rounds"], first["frames"], stats["round_confidence"],
                        len(stats["warnings"])))


def test_real_replay_every_round_has_a_replay_proven_winner():
    """18/18 winners from the replay, and 13 only on the final round (Haven 13-5)."""
    run = real_run()
    if run is None:
        _skip_real()
        return
    _viewer, stats, match = run
    assert stats["round_confidence"] == {"replay": 18}, stats["round_confidence"]
    assert stats["round_outcome_rows"] == 18, stats["round_outcome_rows"]
    scores = {0: 0, 1: 0}
    reached_13 = []
    for summary in match["rounds"]:
        assert summary["winner"] in (0, 1), summary
        assert summary["confidence"] == "replay", summary
        scores[summary["winner"]] += 1
        if scores[summary["winner"]] == 13:
            reached_13.append(summary["n"])
    assert sorted(scores.values()) == [5, 13], scores
    assert reached_13 == [18], reached_13
    assert not [w for w in stats["warnings"] if "disagree" in w or "falls outside" in w], \
        stats["warnings"]


def test_real_replay_side_swap_and_defuse_agree_with_the_container_index():
    """Sides swap after round 12, and every container-index defuse round is a defender win."""
    run = real_run()
    if run is None:
        _skip_real()
        return
    _viewer, stats, match = run
    assert stats["swap_round"] == 12, stats["swap_round"]
    attacker_team = stats["attacker_team_by_round"]
    assert attacker_team[12] == stats["team0"], attacker_team
    assert attacker_team[13] == 1 - stats["team0"], attacker_team
    assert all(attacker_team[n] == stats["team0"] for n in range(1, 13)), attacker_team

    if not os.path.isfile(REAL_VRF):                      # pragma: no cover - store layout
        sys.stdout.write("skipped container check: %s not present\n" % REAL_VRF)
        return
    from bot.replay import container_index
    index = container_index.index_replay(REAL_VRF)
    assert len(index["rounds"]) == 18, len(index["rounds"])
    defuse_rounds = [row["roundNumber"] for row in index["rounds"] if row["defenderWin"]]
    assert defuse_rounds, "the container index reports no defuse round"
    summaries = dict((summary["n"], summary) for summary in match["rounds"])
    for number in defuse_rounds:
        winner = summaries[number]["winner"]
        assert summaries[number]["endType"] == "defuse", summaries[number]
        assert winner is not None and winner != attacker_team[number], summaries[number]
    # The indexed side switch must land in the round the side map swapped after.
    switch_times = index["sideSwitchTimesMs"]
    assert switch_times, "the container index reports no side switch"
    window = [summary for summary in match["rounds"] if summary["n"] == stats["swap_round"]][0]
    assert window["startMs"] <= switch_times[0] <= window["endMs"], (switch_times, window)
    assert attacker_team[stats["swap_round"] + 1] == 1 - attacker_team[stats["swap_round"]]


def test_real_replay_full_elimination_rounds_agree_with_the_winner():
    """A round that wipes one team must be won by the other team."""
    run = real_run()
    if run is None:
        _skip_real()
        return
    _viewer, stats, match = run
    elimination_rounds = stats["full_elimination_rounds"]
    assert len(elimination_rounds) >= 10, elimination_rounds
    assert not [w for w in stats["warnings"] if "had to win" in w], stats["warnings"]
    assert any("round boundaries taken from" in w for w in stats["warnings"]), \
        "the store export used the ClientRoundStart fallback before"


# --------------------------------------------------------------------------
# P3/P2-A overlay export (opt-in: point VALOHUB_OVERLAY_EXPORT at the dir)
# --------------------------------------------------------------------------

OVERLAY_EXPORT_ENV = "VALOHUB_OVERLAY_EXPORT"


def test_overlay_export_round_outcome_rows_and_life_change_hp():
    """The layered parser export: round_outcome rows and player_life_change hp.

    Opt-in because it reads the full ~4.2 GB layered export:

        VALOHUB_OVERLAY_EXPORT=VALOPLANT_WORK/tmp/p2a/export-overlay-verified \\
            py -3.12 -m pytest bot/replay/test_normalize.py -q -k overlay
    """
    path = os.environ.get(OVERLAY_EXPORT_ENV)
    if not path or not os.path.isdir(path):
        sys.stdout.write(
            "skipped: set %s to the P3/P2-A overlay export directory\n" % OVERLAY_EXPORT_ENV)
        return
    viewer = os.path.join(_TMP_ROOT, "overlay-run", "viewer")
    stats = normalize.normalize_directory(path, viewer, match_id="overlay",
                                          normalized_at="2026-10-14T00:00:00Z")
    match = read_json(os.path.join(viewer, "match.json"))
    assert stats["rounds"] == 18, stats["rounds"]
    assert stats["round_outcome_sources"] == {"round_outcome": 18}, stats["round_outcome_sources"]
    assert stats["round_confidence"] == {"replay": 18}, stats["round_confidence"]
    assert stats["attacker_team_source"] == {"replay": 18}, stats["attacker_team_source"]
    first_half = {summary["attackerTeam"] for summary in match["rounds"] if summary["n"] <= 12}
    second_half = {summary["attackerTeam"] for summary in match["rounds"] if summary["n"] > 12}
    assert first_half == {1} and second_half == {0}, (first_half, second_half)
    assert all(summary["attackerTeamConfidence"] == "replay" for summary in match["rounds"])
    assert stats["life_changes"] >= 4000, stats["life_changes"]
    assert stats["hp_players"] == 10, stats["hp_players"]
    assert stats["rounds_with_hp_samples"] == 18, stats["rounds_with_hp_samples"]
    scores = {0: 0, 1: 0}
    for summary in match["rounds"]:
        assert summary["winner"] in (0, 1), summary
        assert summary["confidence"] == "replay", summary
        scores[summary["winner"]] += 1
    assert sorted(scores.values()) == [5, 13], scores
    for summary in match["rounds"]:
        doc = read_json(os.path.join(viewer, "rounds", "r%02d.json" % summary["n"]))
        for player_id, track in doc["players"].items():
            if "hp" in track:
                # hp never contradicts life: a living player is never at 0 hp.
                assert not any(value == 0 and track["alive"][index] == 1
                               for index, value in enumerate(track["hp"])), \
                    (summary["n"], player_id)
        if summary["n"] > 1:
            assert all("hp" in track for track in doc["players"].values()), \
                (summary["n"], sorted(doc["players"]))



# --------------------------------------------------------------------------
# P3/P4 attackerTeam: exact sides from the overlay's round_outcome rows
# --------------------------------------------------------------------------

def test_round_outcome_rows_publish_attacker_team_as_replay():
    """match.json round entries carry attackerTeam + its confidence."""
    rounds = ((1, 0, 92000), (2, 100000, 178000))
    _raw, viewer, stats = make_case("attacker-team-rows", rounds=rounds, outcome_source="rows")
    match = read_json(os.path.join(viewer, "match.json"))
    assert [summary["attackerTeam"] for summary in match["rounds"]] == [0, 0], match["rounds"]
    assert [summary["attackerTeamConfidence"] for summary in match["rounds"]] == \
        ["replay", "replay"], match["rounds"]
    assert stats["attacker_team_source"] == {"replay": 2}, stats["attacker_team_source"]
    assert stats["attacker_team_published"] == {1: 0, 2: 0}, stats["attacker_team_published"]


def test_attacker_team_falls_back_to_inferred_without_outcome_rows():
    """No round_outcome rows: the planter/spawn map stays, labelled inferred."""
    rounds = ((1, 0, 92000), (2, 100000, 178000))
    _raw, viewer, stats = make_case("attacker-team-inferred", rounds=rounds, outcome_source="none")
    match = read_json(os.path.join(viewer, "match.json"))
    # fixture convention: team 0 holds the spike, so team 0 attacks every round
    assert [summary["attackerTeam"] for summary in match["rounds"]] == [0, 0], match["rounds"]
    assert [summary["attackerTeamConfidence"] for summary in match["rounds"]] == \
        ["inferred", "inferred"], match["rounds"]
    assert stats["attacker_team_source"] == {"inferred": 2}, stats["attacker_team_source"]


class _OutcomeScan(object):
    """Minimal stand-in for RawScan: only round_outcomes is read by the derivation."""

    def __init__(self, round_outcomes):
        self.round_outcomes = round_outcomes


HAVEN_ROLES = [(1, "Red", "attacker"), (2, "Red", "attacker"), (3, "Red", "attacker"),
               (4, "Red", "attacker"), (5, "Red", "attacker"), (6, "Red", "attacker"),
               (7, "Red", "attacker"), (8, "Blue", "defender"), (9, "Blue", "defender"),
               (10, "Red", "attacker"), (11, "Red", "attacker"), (12, "Blue", "defender"),
               (13, "Blue", "attacker"), (14, "Blue", "attacker"), (15, "Red", "defender"),
               (16, "Red", "defender"), (17, "Red", "defender"), (18, "Red", "defender")]
# What build_side_map() infers for the 13.06 Haven replay (team 1 attacks the first half).
HAVEN_SIDE_MAP = dict([(number, 1 if number <= 12 else 0) for number in range(1, 19)])


def _side_rows(pattern, with_scores=True, source="round_outcome"):
    """round_outcomes payloads: cumulative Red/Blue scores, like the overlay emits."""
    rows = {}
    red = blue = 0
    for number, colour, role in pattern:
        if colour.lower() == "red":
            red += 1
        else:
            blue += 1
        rows[number] = {
            "n": number, "endType": "elimination", "winnerRole": role, "winnerTeam": colour,
            "redScore": red if with_scores else None,
            "blueScore": blue if with_scores else None,
            "timeMs": number * 1000, "source": source,
        }
    return rows


def _side_rounds(count=18):
    return [(number, (number - 1) * 100000, number * 100000 - 1, None)
            for number in range(1, count + 1)]


def test_derive_side_teams_flips_after_round_12():
    """Haven pattern: the score series pins Red = team 1, so half 1 attacks with team 1."""
    scan = _OutcomeScan(_side_rows(HAVEN_ROLES))
    by_round, confidence, note = normalize.derive_side_teams(
        scan, _side_rounds(), dict(HAVEN_SIDE_MAP), 12)
    assert note is None, note
    assert by_round == HAVEN_SIDE_MAP, by_round
    assert set(by_round) == set(range(1, 19))
    assert confidence == dict([(number, normalize.CONF_REPLAY) for number in range(1, 19)])
    first_half = {by_round[number] for number in range(1, 13)}
    second_half = {by_round[number] for number in range(13, 19)}
    assert first_half == {1} and second_half == {0}, (first_half, second_half)
    assert first_half != second_half


def test_derive_side_teams_proves_rounds_without_direct_planter_evidence():
    """Rounds whose own planter evidence is missing still get direction from the half.

    In the 13.06 Haven export only rounds 1,2,4,5,6,7,10,11,13,14,18 carry a plant or
    a bomb pickup; the rest of ``build_side_map``'s map comes from its swap rule. The
    outcome rows carry a role for every completed round, so all 18 rounds are proven.
    """
    scan = _OutcomeScan(_side_rows(HAVEN_ROLES))
    by_round, confidence, note = normalize.derive_side_teams(
        scan, _side_rounds(), dict(HAVEN_SIDE_MAP), 12)
    assert note is None, note
    assert len(by_round) == 18
    assert by_round == HAVEN_SIDE_MAP
    assert set(confidence.values()) == {normalize.CONF_REPLAY}


def test_derive_side_teams_accepts_a_partial_export():
    """Rows for half 1 only: those rounds are proven, the rest stay unproven."""
    scan = _OutcomeScan(_side_rows(HAVEN_ROLES[:12]))
    by_round, confidence, note = normalize.derive_side_teams(
        scan, _side_rounds(), dict(HAVEN_SIDE_MAP), 12)
    assert note is None, note
    assert sorted(by_round) == list(range(1, 13))
    assert set(confidence.values()) == {normalize.CONF_REPLAY}


def test_derive_side_teams_refuses_without_evidence():
    """Every refusal keeps the caller's inferred map and says why."""
    # no rows at all
    assert normalize.derive_side_teams(_OutcomeScan({}), _side_rounds(),
                                       dict(HAVEN_SIDE_MAP), 12) == ({}, {}, None)

    # scores absent: both colour -> team candidates stay consistent, so no proof
    by_round, _confidence, note = normalize.derive_side_teams(
        _OutcomeScan(_side_rows(HAVEN_ROLES, with_scores=False)), _side_rounds(),
        dict(HAVEN_SIDE_MAP), 12)
    assert by_round == {} and "colour -> team map" in note, note

    # scores that no candidate mapping can satisfy (frozen at 0/0)
    rows = _side_rows(HAVEN_ROLES)
    for row in rows.values():
        row["redScore"] = 0
        row["blueScore"] = 0
    by_round, _confidence, note = normalize.derive_side_teams(
        _OutcomeScan(rows), _side_rounds(), dict(HAVEN_SIDE_MAP), 12)
    assert by_round == {} and "colour -> team map" in note, note

    # swapping the score columns is consistent with the mirrored colour mapping, so the
    # derivation follows the scores rather than the planter-based winner indices
    rows = _side_rows(HAVEN_ROLES)
    for row in rows.values():
        row["redScore"], row["blueScore"] = row["blueScore"], row["redScore"]
    by_round, confidence, note = normalize.derive_side_teams(
        _OutcomeScan(rows), _side_rounds(), dict(HAVEN_SIDE_MAP), 12)
    assert note is None, note
    assert {by_round[number] for number in range(1, 13)} == {0}
    assert {by_round[number] for number in range(13, 19)} == {1}
    assert set(confidence.values()) == {normalize.CONF_REPLAY}

    # a missing round breaks the cumulative series that pins the mapping
    rows = _side_rows(HAVEN_ROLES)
    del rows[7]
    by_round, _confidence, note = normalize.derive_side_teams(
        _OutcomeScan(rows), _side_rounds(), dict(HAVEN_SIDE_MAP), 12)
    assert by_round == {} and "colour -> team map" in note, note

    # roles that contradict each other inside one half
    pattern = list(HAVEN_ROLES)
    pattern[2] = (3, "Blue", "attacker")
    by_round, _confidence, note = normalize.derive_side_teams(
        _OutcomeScan(_side_rows(pattern)), _side_rounds(), dict(HAVEN_SIDE_MAP), 12)
    assert by_round == {} and "disagree" in note, note

    # a row whose round has no attack-side anchor in the inferred map
    side_map = dict(HAVEN_SIDE_MAP)
    side_map[1] = None
    by_round, _confidence, note = normalize.derive_side_teams(
        _OutcomeScan(_side_rows(HAVEN_ROLES)), _side_rounds(), side_map, 12)
    assert by_round == {} and "anchor" in note, note

    # rows that carry no role or no colour are ignored, not guessed
    scan = _OutcomeScan({
        1: {"n": 1, "winnerRole": None, "winnerTeam": "Red", "redScore": 1, "blueScore": 0,
            "endType": "elimination", "timeMs": 1000, "source": "round_outcome"},
        2: {"n": 2, "winnerRole": "attacker", "winnerTeam": None, "redScore": 1, "blueScore": 1,
            "endType": "elimination", "timeMs": 2000, "source": "round_outcome"},
    })
    assert normalize.derive_side_teams(scan, _side_rounds(2), {1: 1, 2: 1}, 12) == ({}, {}, None)


# --------------------------------------------------------------------------
# All-map coverage (P7-1M): MAPS / MAP_FOLDER_NAMES and the cached minimaps
# --------------------------------------------------------------------------

# The two entries measured on the 2026-10-13 specimen replays. They must never
# drift: the cached minimaps, the store export and the frontend all key off them.
FROZEN_MAP_SOURCE = (
    '    "/Game/Maps/Triad/Triad": {\n'
    '        "name": "Haven",\n'
    '        "path": "/Game/Maps/Triad/Triad",\n'
    '        "uuid": "2bee0dc9-4ffe-519b-1cbd-7fbe763a6047",\n'
    '        "image": "https://media.valorant-api.com/maps/'
    '2bee0dc9-4ffe-519b-1cbd-7fbe763a6047/displayicon.png",\n'
    '        "xMultiplier": 7.5e-05,\n'
    '        "yMultiplier": -7.5e-05,\n'
    '        "xScalarToAdd": 1.09345,\n'
    '        "yScalarToAdd": 0.642728,\n'
    '    },\n'
    '    "/Game/Maps/Juliett/Juliett": {\n'
    '        "name": "Sunset",\n'
    '        "path": "/Game/Maps/Juliett/Juliett",\n'
    '        "uuid": "92584fbe-486a-b1b2-9faa-39b0f486b498",\n'
    '        "image": "https://media.valorant-api.com/maps/'
    '92584fbe-486a-b1b2-9faa-39b0f486b498/displayicon.png",\n'
    '        "xMultiplier": 7.8e-05,\n'
    '        "yMultiplier": -7.8e-05,\n'
    '        "xScalarToAdd": 0.5,\n'
    '        "yScalarToAdd": 0.515625,\n'
    '    },\n'
)

FROZEN_MAPS = {
    "/Game/Maps/Triad/Triad": {
        "name": "Haven", "path": "/Game/Maps/Triad/Triad",
        "uuid": "2bee0dc9-4ffe-519b-1cbd-7fbe763a6047",
        "image": "https://media.valorant-api.com/maps/"
                 "2bee0dc9-4ffe-519b-1cbd-7fbe763a6047/displayicon.png",
        "xMultiplier": 7.5e-05, "yMultiplier": -7.5e-05,
        "xScalarToAdd": 1.09345, "yScalarToAdd": 0.642728,
    },
    "/Game/Maps/Juliett/Juliett": {
        "name": "Sunset", "path": "/Game/Maps/Juliett/Juliett",
        "uuid": "92584fbe-486a-b1b2-9faa-39b0f486b498",
        "image": "https://media.valorant-api.com/maps/"
                 "92584fbe-486a-b1b2-9faa-39b0f486b498/displayicon.png",
        "xMultiplier": 7.8e-05, "yMultiplier": -7.8e-05,
        "xScalarToAdd": 0.5, "yScalarToAdd": 0.515625,
    },
}

# Folder -> (display name, uuid) for every map valorant-api.com published with a
# position transform and a displayIcon on 2026-10-17 (13 of 27 entries).
PUBLISHED_MAPS = {
    "Triad": ("Haven", "2bee0dc9-4ffe-519b-1cbd-7fbe763a6047"),
    "Juliett": ("Sunset", "92584fbe-486a-b1b2-9faa-39b0f486b498"),
    "Ascent": ("Ascent", "7eaecc1b-4337-bbf6-6ab9-04b8f06b3319"),
    "Bonsai": ("Split", "d960549e-485c-e861-8d71-aa9d1aed12a2"),
    "Canyon": ("Fracture", "b529448b-4d60-346e-e89e-00a4c527a405"),
    "Duality": ("Bind", "2c9d57ec-4431-9c5e-2939-8f9ef6dd5cba"),
    "Foxtrot": ("Breeze", "2fb9a4fd-47b8-4e7d-a969-74b4046ebd53"),
    "Infinity": ("Abyss", "224b0a95-48b9-f703-1bd8-67aca101a61f"),
    "Jam": ("Lotus", "2fe4ed3a-450a-948b-6d6b-e89a78e680a9"),
    "Pitt": ("Pearl", "fd267378-4d1d-484f-ff52-77821ed10dc2"),
    "Plummet": ("Summit", "756da597-416b-c0f2-f47b-afbdf28670bc"),
    "Port": ("Icebox", "e2ad5c54-4114-a870-9641-8ea21279579a"),
    "Rook": ("Corrode", "1c18ab1f-420d-0d8b-71d0-77ad3c439115"),
}

MAP_TRANSFORM_FIELDS = ("xMultiplier", "yMultiplier", "xScalarToAdd", "yScalarToAdd")
MAP_ASSET_DIR = os.path.join(_REPO_ROOT, "bot", "web", "static", "replays_app",
                             "assets", "maps")
NORMALIZE_PATH = os.path.join(_HERE, "normalize.py")


def test_frozen_map_entries_are_unchanged():
    """Haven and Sunset keep the exact 2026-10-13 values, text and slot order."""
    source = open(NORMALIZE_PATH, encoding="utf-8").read()
    assert FROZEN_MAP_SOURCE in source
    for path, want in FROZEN_MAPS.items():
        assert normalize.MAPS[path] == want, path
        assert list(normalize.MAPS[path]) == ["name", "path", "uuid", "image"] + \
            list(MAP_TRANSFORM_FIELDS), path
    assert list(normalize.MAPS)[:2] == ["/Game/Maps/Triad/Triad",
                                        "/Game/Maps/Juliett/Juliett"]


def test_every_published_map_is_in_the_table():
    expected = {"/Game/Maps/%s/%s" % (folder, folder): (name, uuid)
                for folder, (name, uuid) in PUBLISHED_MAPS.items()}
    assert len(expected) == 13
    assert set(normalize.MAPS) == set(expected), (
        set(expected) - set(normalize.MAPS), set(normalize.MAPS) - set(expected))
    for path, (name, uuid) in expected.items():
        entry = normalize.MAPS[path]
        assert entry["name"] == name, path
        assert entry["uuid"] == uuid, path
        assert entry["path"] == path, path

    assert normalize.MAP_FOLDER_NAMES == {folder: name for folder, (name, _u)
                                          in PUBLISHED_MAPS.items()}


def test_map_transforms_are_finite_and_non_zero():
    for path, entry in normalize.MAPS.items():
        for field in MAP_TRANSFORM_FIELDS:
            value = entry[field]
            assert isinstance(value, float), (path, field, value)
            assert math.isfinite(value), (path, field, value)
        assert entry["xMultiplier"] != 0.0 and entry["yMultiplier"] != 0.0, path
        assert entry["xScalarToAdd"] != 0.0 or entry["yScalarToAdd"] != 0.0, path
        # the world -> minimap transform always mirrors the game's Y axis
        assert entry["yMultiplier"] < 0 < entry["xMultiplier"], path
        assert entry["image"] == (
            "https://media.valorant-api.com/maps/%s/displayicon.png" % entry["uuid"])
        assert len(entry["uuid"]) == 36 and entry["uuid"].count("-") == 4, path


def test_every_map_has_a_cached_minimap():
    assert os.path.isdir(MAP_ASSET_DIR), MAP_ASSET_DIR
    present = set()
    for entry in normalize.MAPS.values():
        target = os.path.join(MAP_ASSET_DIR, "%s.png" % entry["uuid"])
        assert os.path.isfile(target), target
        assert os.path.getsize(target) > 1024, target
        with open(target, "rb") as handle:
            assert handle.read(8) == b"\x89PNG\r\n\x1a\n", target
        present.add("%s.png" % entry["uuid"])
    cached = {name for name in os.listdir(MAP_ASSET_DIR) if name.endswith(".png")}
    assert present == cached, cached - present
    assert len(cached) == 13


def test_detect_map_resolves_every_known_map():
    for folder, (name, uuid) in PUBLISHED_MAPS.items():
        path = "/Game/Maps/%s/%s" % (folder, folder)

        # (a) the pipeline manifest names the map path
        scan = normalize.RawScan()
        scan.pipeline_manifest = {"map": {"name": name, "path": path}}
        info = normalize.detect_map(scan, FIXTURE_DIR)
        assert info["name"] == name and info["uuid"] == uuid, info
        assert info["xMultiplier"] == normalize.MAPS[path]["xMultiplier"], path

        # (b) the raw export only leaks the path inside a string
        scan = normalize.RawScan()
        scan.note_string("/Script/Foo.%s/Level" % path)
        info = normalize.detect_map(scan, FIXTURE_DIR)
        assert info["name"] == name and info["uuid"] == uuid, info
        assert scan.warnings == [], scan.warnings


# --------------------------------------------------------------------------
# Runners
# --------------------------------------------------------------------------

def _tests():
    return [(name, value) for name, value in sorted(globals().items())
            if name.startswith("test_") and callable(value)]


def write_fixture():
    """Regenerate VALOPLANT_WORK/fixtures/raw-demo from this builder."""
    if os.path.isdir(FIXTURE_DIR):
        shutil.rmtree(FIXTURE_DIR)
    build_export(FIXTURE_DIR)
    total = 0
    for root, _dirs, files in os.walk(FIXTURE_DIR):
        for name in sorted(files):
            total += os.path.getsize(os.path.join(root, name))
    sys.stdout.write("wrote %s (%d bytes)\n" % (FIXTURE_DIR, total))


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--write-fixture" in argv:
        write_fixture()
        return 0
    failures = 0
    for name, function in _tests():
        try:
            function()
        except Exception as exc:  # noqa: BLE001 - plain assert runner
            failures += 1
            sys.stdout.write("FAIL %s: %s: %s\n" % (name, type(exc).__name__, exc))
        else:
            sys.stdout.write("ok   %s\n" % name)
    for path in _CLEANUPS:
        shutil.rmtree(path, ignore_errors=True)
    sys.stdout.write("%d test(s), %d failure(s)\n" % (len(_tests()), failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

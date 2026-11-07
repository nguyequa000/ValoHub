#!/usr/bin/env python3
"""ValoHub normalizer: CliReader ``export --profile viewer`` raw output -> viewer JSON.

Pure Python standard library only (operator requirement, BUILD_CONTRACT.md section 5).

Usage:
    py -3.12 bot/replay/normalize.py <raw_dir> <viewer_dir>

``<raw_dir>`` is a CliReader viewer-profile export directory containing
``manifest.json`` and ``events.ndjson``/``movement.ndjson`` — plain or
``.ndjson.gz`` (the pipeline compresses raw/ after each successful
normalize; both forms read identically). See
``VALOPLANT_WORK/reports/normalizer-lane.md`` for the full input-format mapping).
``<viewer_dir>`` receives ``match.json`` and ``rounds/rNN.json`` per
BUILD_CONTRACT.md section 3.

Design rules
------------
* Deterministic: identical input bytes -> identical output bytes. The single
  documented exception is ``decode.normalizedAt`` (wall-clock); pin it with
  ``--normalized-at`` or the ``VALOHUB_NORMALIZED_AT`` environment variable
  to make the whole run byte-reproducible.
* Never fabricate: anything the export does not prove is ``null`` (or the
  player/field is omitted) and events carry ``confidence`` ``"unknown"``.
"""

from __future__ import annotations

import bisect
import datetime
import gzip
import json
import math
import os
import re
import sys
import typing

try:  # imported as bot.replay.normalize, or executed as a script by pipeline.py
    from . import utility_track
except ImportError:  # no parent package: direct `py bot/replay/normalize.py`, or a
    # test that loads this file by path (importlib.util, see test_normalize.py).
    # Put this module's own directory on sys.path so the sibling import resolves.
    _HERE = os.path.dirname(os.path.abspath(__file__))
    if _HERE not in sys.path:
        sys.path.insert(0, _HERE)
    import utility_track

SCHEMA_MATCH = "valoplant_match/v2"
SCHEMA_ROUND = "valoplant_round/v2"

# P7-1A2: the ability catalog (keyed by codename|slot, tokens matched against the
# class path) lives next to this module. utility_track.catalog_rows() flattens it.
UTILITY_CATALOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "ability_catalog.json")

TICK_MS = 100                     # contract: 10 Hz resample
SIDE_SWITCH_DEFAULT_ROUND = 12    # standard competitive half length; overridden by replay evidence
MAX_PLAYERS = 10

# A round's boundary marker (ClientRoundStart / the first BombGameState snapshot)
# fires before the replay teleports the players to their spawns, so a round sliced
# from the marker opens on frames that still hold the *previous* round's positions.
# The teleport is visible in the movement stream as an instantaneous displacement
# far larger than any in-round movement; a round's first frame is moved up to it.
TELEPORT_UNITS = 800.0        # minimum displacement that counts as a spawn teleport
TELEPORT_WINDOW_MS = 5000     # how long after the boundary a spawn teleport may land
TELEPORT_CLUSTER_MS = 1000    # teleports this close in time belong to the same round start

CONF_REPLAY = "replay"
CONF_INFERRED = "inferred"
CONF_LOADOUT = "loadout"
CONF_UNKNOWN = "unknown"

# --------------------------------------------------------------------------
# Round outcome vocabulary (P3_CONTRACT.md §P2: winner + endType)
# --------------------------------------------------------------------------

# The four end types the plan contract names. Anything else the replay reports is
# carried through verbatim (never silently rewritten) -- see normalize_end_type.
ROUND_END_TYPES = ("elimination", "detonate", "defuse", "time")
_END_TYPE_ALIASES = {
    "time_expired": "time",
    "timeout": "time",
    "time_expire": "time",
    "exploded": "detonate",
    "explosion": "detonate",
    "detonation": "detonate",
    "defused": "defuse",
    "eliminated": "elimination",
}

# Which source wins when two of them describe the same round.
_OUTCOME_RANK = {"bomb_game_state": 0, "round_outcome": 1}

# A character's life is spread over several components: health plus one or two
# shield components (Valorant caps shields at 50). Measured on the 13.06 Haven
# export (2026-10-14): every character's health component reaches exactly 100.0
# and is the only component that ever reports alive_after_change=false; shield
# components never exceed 50.0. A component is only promoted to "health" when it
# proves itself above that cap -- otherwise hp stays absent.
HEALTH_MIN_LIFE = 51.0

# --------------------------------------------------------------------------
# Static tables
# --------------------------------------------------------------------------

# BUILD_CONTRACT.md section 2 (measured 2026-10-13 from the two specimen replays).
# Extended 2026-10-17 to every map valorant-api.com currently publishes with a position
# transform (13 of 27 entries); source: https://valorant-api.com/v1/maps
# (fields xMultiplier / yMultiplier / xScalarToAdd / yScalarToAdd + displayIcon).
# The Haven and Sunset entries below are unchanged from the 2026-10-13 measurement.
MAPS = {
    "/Game/Maps/Triad/Triad": {
        "name": "Haven",
        "path": "/Game/Maps/Triad/Triad",
        "uuid": "2bee0dc9-4ffe-519b-1cbd-7fbe763a6047",
        "image": "https://media.valorant-api.com/maps/2bee0dc9-4ffe-519b-1cbd-7fbe763a6047/displayicon.png",
        "xMultiplier": 7.5e-05,
        "yMultiplier": -7.5e-05,
        "xScalarToAdd": 1.09345,
        "yScalarToAdd": 0.642728,
    },
    "/Game/Maps/Juliett/Juliett": {
        "name": "Sunset",
        "path": "/Game/Maps/Juliett/Juliett",
        "uuid": "92584fbe-486a-b1b2-9faa-39b0f486b498",
        "image": "https://media.valorant-api.com/maps/92584fbe-486a-b1b2-9faa-39b0f486b498/displayicon.png",
        "xMultiplier": 7.8e-05,
        "yMultiplier": -7.8e-05,
        "xScalarToAdd": 0.5,
        "yScalarToAdd": 0.515625,
    },
    "/Game/Maps/Ascent/Ascent": {
        "name": "Ascent",
        "path": "/Game/Maps/Ascent/Ascent",
        "uuid": "7eaecc1b-4337-bbf6-6ab9-04b8f06b3319",
        "image": "https://media.valorant-api.com/maps/7eaecc1b-4337-bbf6-6ab9-04b8f06b3319/displayicon.png",
        "xMultiplier": 7e-05,
        "yMultiplier": -7e-05,
        "xScalarToAdd": 0.813895,
        "yScalarToAdd": 0.573242,
    },
    "/Game/Maps/Bonsai/Bonsai": {
        "name": "Split",
        "path": "/Game/Maps/Bonsai/Bonsai",
        "uuid": "d960549e-485c-e861-8d71-aa9d1aed12a2",
        "image": "https://media.valorant-api.com/maps/d960549e-485c-e861-8d71-aa9d1aed12a2/displayicon.png",
        "xMultiplier": 7.8e-05,
        "yMultiplier": -7.8e-05,
        "xScalarToAdd": 0.842188,
        "yScalarToAdd": 0.697578,
    },
    "/Game/Maps/Canyon/Canyon": {
        "name": "Fracture",
        "path": "/Game/Maps/Canyon/Canyon",
        "uuid": "b529448b-4d60-346e-e89e-00a4c527a405",
        "image": "https://media.valorant-api.com/maps/b529448b-4d60-346e-e89e-00a4c527a405/displayicon.png",
        "xMultiplier": 7.8e-05,
        "yMultiplier": -7.8e-05,
        "xScalarToAdd": 0.556952,
        "yScalarToAdd": 1.155886,
    },
    "/Game/Maps/Duality/Duality": {
        "name": "Bind",
        "path": "/Game/Maps/Duality/Duality",
        "uuid": "2c9d57ec-4431-9c5e-2939-8f9ef6dd5cba",
        "image": "https://media.valorant-api.com/maps/2c9d57ec-4431-9c5e-2939-8f9ef6dd5cba/displayicon.png",
        "xMultiplier": 5.9e-05,
        "yMultiplier": -5.9e-05,
        "xScalarToAdd": 0.576941,
        "yScalarToAdd": 0.967566,
    },
    "/Game/Maps/Foxtrot/Foxtrot": {
        "name": "Breeze",
        "path": "/Game/Maps/Foxtrot/Foxtrot",
        "uuid": "2fb9a4fd-47b8-4e7d-a969-74b4046ebd53",
        "image": "https://media.valorant-api.com/maps/2fb9a4fd-47b8-4e7d-a969-74b4046ebd53/displayicon.png",
        "xMultiplier": 7e-05,
        "yMultiplier": -7e-05,
        "xScalarToAdd": 0.465123,
        "yScalarToAdd": 0.833078,
    },
    "/Game/Maps/Infinity/Infinity": {
        "name": "Abyss",
        "path": "/Game/Maps/Infinity/Infinity",
        "uuid": "224b0a95-48b9-f703-1bd8-67aca101a61f",
        "image": "https://media.valorant-api.com/maps/224b0a95-48b9-f703-1bd8-67aca101a61f/displayicon.png",
        "xMultiplier": 8.1e-05,
        "yMultiplier": -8.1e-05,
        "xScalarToAdd": 0.5,
        "yScalarToAdd": 0.5,
    },
    "/Game/Maps/Jam/Jam": {
        "name": "Lotus",
        "path": "/Game/Maps/Jam/Jam",
        "uuid": "2fe4ed3a-450a-948b-6d6b-e89a78e680a9",
        "image": "https://media.valorant-api.com/maps/2fe4ed3a-450a-948b-6d6b-e89a78e680a9/displayicon.png",
        "xMultiplier": 7.2e-05,
        "yMultiplier": -7.2e-05,
        "xScalarToAdd": 0.454789,
        "yScalarToAdd": 0.917752,
    },
    "/Game/Maps/Pitt/Pitt": {
        "name": "Pearl",
        "path": "/Game/Maps/Pitt/Pitt",
        "uuid": "fd267378-4d1d-484f-ff52-77821ed10dc2",
        "image": "https://media.valorant-api.com/maps/fd267378-4d1d-484f-ff52-77821ed10dc2/displayicon.png",
        "xMultiplier": 7.8e-05,
        "yMultiplier": -7.8e-05,
        "xScalarToAdd": 0.480469,
        "yScalarToAdd": 0.916016,
    },
    "/Game/Maps/Plummet/Plummet": {
        "name": "Summit",
        "path": "/Game/Maps/Plummet/Plummet",
        "uuid": "756da597-416b-c0f2-f47b-afbdf28670bc",
        "image": "https://media.valorant-api.com/maps/756da597-416b-c0f2-f47b-afbdf28670bc/displayicon.png",
        "xMultiplier": 7.5e-05,
        "yMultiplier": -7.5e-05,
        "xScalarToAdd": 0.047401,
        "yScalarToAdd": 0.978891,
    },
    "/Game/Maps/Port/Port": {
        "name": "Icebox",
        "path": "/Game/Maps/Port/Port",
        "uuid": "e2ad5c54-4114-a870-9641-8ea21279579a",
        "image": "https://media.valorant-api.com/maps/e2ad5c54-4114-a870-9641-8ea21279579a/displayicon.png",
        "xMultiplier": 7.2e-05,
        "yMultiplier": -7.2e-05,
        "xScalarToAdd": 0.460214,
        "yScalarToAdd": 0.304687,
    },
    "/Game/Maps/Rook/Rook": {
        "name": "Corrode",
        "path": "/Game/Maps/Rook/Rook",
        "uuid": "1c18ab1f-420d-0d8b-71d0-77ad3c439115",
        "image": "https://media.valorant-api.com/maps/1c18ab1f-420d-0d8b-71d0-77ad3c439115/displayicon.png",
        "xMultiplier": 7e-05,
        "yMultiplier": -7e-05,
        "xScalarToAdd": 0.526158,
        "yScalarToAdd": 0.5,
    },
}

# Map folder name -> map display name. Every folder VRF exports name in a map
# path (/Game/Maps/<folder>/<folder>) that carries a known transform.
MAP_FOLDER_NAMES = {"Triad": "Haven", "Juliett": "Sunset", "Ascent": "Ascent", "Bonsai": "Split", "Canyon": "Fracture", "Duality": "Bind", "Foxtrot": "Breeze", "Infinity": "Abyss", "Jam": "Lotus", "Pitt": "Pearl", "Plummet": "Summit", "Port": "Icebox", "Rook": "Corrode"}

# Character codename -> agent display name. Source: the ``///`` summaries of
# vendorValorantReplayParser/src/Replay.Valorant/Descriptors/Agents/*/*AgentDescriptor.cs
# (verified in this workspace on 2026-10-13, including ClayAgentDescriptor.cs -> Raze).
AGENT_BY_CODENAME = {
    "Aggrobot": "Gekko",
    "BountyHunter": "Fade",
    "Breach": "Breach",
    "Cable": "Deadlock",
    "Cashew": "Tejo",
    "Clay": "Raze",
    "Deadeye": "Chamber",
    "Grenadier": "KAY/O",
    "Guide": "Skye",
    "Gumshoe": "Cypher",
    "Hunter": "Sova",
    "Iris": "Miks",
    "Killjoy": "Killjoy",
    "Mage": "Harbor",
    "Nox": "Vyse",
    "Pandemic": "Viper",
    "Paprika": "Hush",
    "Phoenix": "Phoenix",
    "Pine": "Veto",
    "Rift": "Astra",
    "Sarge": "Brimstone",
    "Sequoia": "Iso",
    "Smonk": "Clove",
    "Sprinter": "Neon",
    "Stealth": "Yoru",
    "Terra": "Waylay",
    "Thorne": "Sage",
    "Vampire": "Reyna",
    "Wraith": "Omen",
    "Wushu": "Jett",
}

# Agent display name -> lowercase uuid. Single source of truth is
# ``bot/replay/agent_ids.json``, written by ``tools/fetch_agent_assets.py`` from
# ``GET https://valorant-api.com/v1/agents?isPlayableCharacter=true``. The load is
# deliberately soft: a checkout without the file keeps ``agentUuid`` null instead
# of breaking the normalizer.
AGENT_IDS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent_ids.json")
AGENT_IDS = {}
try:
    with open(AGENT_IDS_PATH, "r", encoding="utf-8") as _agent_ids_handle:
        _agent_ids_payload = json.load(_agent_ids_handle)
    if isinstance(_agent_ids_payload, dict):
        AGENT_IDS = _agent_ids_payload
except (OSError, ValueError):
    AGENT_IDS = {}
AGENT_UUID_BY_NAME = {name: str(uuid).lower()
                      for name, uuid in (AGENT_IDS.get("byName") or {}).items()}

# Export-group actor paths (ReplayEventJsonWriter.WriteExportGroup -> export_group_path).
PATH_BOMB_GAME_STATE = "/Game/GameModes/Bomb/BombGameState.BombGameState_C"
PATH_BOMB_PLAYER_STATE = "/Game/GameModes/Bomb/BombPlayerState.BombPlayerState_C"
PATH_ARES_ATTRIBUTE_SET = "/Script/ShooterGame.AresAttributeSet"

# RPC paths (ReplayEventJsonWriter.WriteRpc -> function_export_path).
RPC_ROUND_START = "/Game/GameModes/Bomb/BombGameState.BombGameState_C:ClientRoundStart"
RPC_BUY_PHASE_END = "/Game/GameModes/Bomb/BombGameState.BombGameState_C:ClientBuyPhaseEnd"
RPC_SIDE_SWITCH = "/Game/GameModes/Bomb/BombGameState.BombGameState_C:Multicast Side Switch Event"
RPC_ROUND_RESET = "/Script/ShooterGame.ShooterGameState:ClientResetRound"
RPC_END_ROUND = "/Script/ShooterGame.ShooterGameState:MulticastEndRound"
RPC_SET_PHASE = "/Script/ShooterGame.ShooterGameState:MulticastSetPhase"
RPC_KILLED_ENEMY = "/Script/ShooterGame.ShooterCharacter:MulticastNotifyKilledEnemy"
RPC_BOMB_PLANTED = "/Game/GameModes/Components/Comp_BombEvents.Comp_BombEvents_C:BombPlantedRPC"
RPC_BOMB_DEFUSED = "/Game/GameModes/Components/Comp_BombEvents.Comp_BombEvents_C:BombDefusedRPC"
RPC_BOMB_EXPLODED = "/Game/GameModes/Components/Comp_BombEvents.Comp_BombEvents_C:BombExplodedRPC"
RPC_BOMB_PICKED_UP = "/Game/GameModes/Components/Comp_BombEvents.Comp_BombEvents_C:BombPickedUpRPC"
RPC_BOMB_DROPPED = "/Game/GameModes/Components/Comp_BombEvents.Comp_BombEvents_C:BombDroppedRPC"
RPC_DAMAGE_BASE = "/Script/ShooterGame.DamageableComponent:MulticastNotifyDamage_Base"
RPC_DAMAGE_POINT = "/Script/ShooterGame.DamageableComponent:MulticastNotifyDamage_Point"
RPC_TEMP_DEATH_BASE = "/Script/ShooterGame.ShooterGameState:MulticastReceivePlayerTemporaryDeathEvent_Base"
RPC_TEMP_DEATH_POINT = "/Script/ShooterGame.ShooterGameState:MulticastReceivePlayerTemporaryDeathEvent_Point"
RPC_RESURRECT = "/Script/ShooterGame.ShooterGameState:MulticastReceivePlayerResurrectEvent"
RPC_RESPAWN_RESET = "/Script/ShooterGame.AresGameStateBase:MulticastResetForRespawn"

# EAresGamePhase (src/Replay.Valorant/GameState/EAresGamePhase.cs)
PHASE_BETWEEN_ROUNDS = 2
PHASE_ROUND_STARTING = 3
PHASE_IN_ROUND = 4
PHASE_ROUND_ENDING = 5
PHASE_SWITCHING_TEAMS = 6
PHASE_GAME_ENDED = 7

CHARACTER_PATH_RE = re.compile(r"/Game/Characters/([A-Za-z0-9_]+)/\1_PC[._]")
MAP_PATH_RE = re.compile(r"/Game/Maps/([A-Za-z0-9_]+)/")
BRANCH_PATCH_RE = re.compile(r"release-(\d+\.\d+)")

# AresRoundOutcome / AresTeamRole are emitted by the parser's normalizer with
# snake_case enum names (ReplayJsonNormalizer.ToSnakeCase).
_ROUND_OUTCOME_NAMES = {
    0: "elimination",
    1: "defuse",
    2: "detonate",
    3: "time_expired",
    4: "cheat",
    5: "surrendered",
}
_TEAM_ROLE_NAMES = {0: None, 1: "attacker", 2: "defender", 3: "free_for_all", 4: None}
_TEAM_ROLE_BY_NAME = {"attacker": 1, "defender": 2}


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

class NormalizeError(Exception):
    """Fatal input problem (missing/invalid raw export)."""


def iround(value):
    """Round half away from zero (matches the parser's MidpointRounding.AwayFromZero)."""
    if value is None:
        return None
    value = float(value)
    if not math.isfinite(value):
        return None
    return int(math.floor(value + 0.5)) if value >= 0 else int(math.ceil(value - 0.5))


def norm_yaw(yaw):
    """Normalize degrees into [-180, 180]; returns None for non-finite input."""
    if yaw is None:
        return None
    yaw = float(yaw)
    if not math.isfinite(yaw):
        return None
    yaw = math.fmod(yaw, 360.0)
    if yaw > 180.0:
        yaw -= 360.0
    elif yaw < -180.0:
        yaw += 360.0
    return iround(yaw)


def as_int(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return iround(value)
    if isinstance(value, str):
        try:
            return int(value, 0)
        except ValueError:
            return None
    return None


def as_float(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        value = float(value)
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            return None
        return value if math.isfinite(value) else None
    return None


def as_bool(value):
    return value if isinstance(value, bool) else None


def as_str(value):
    return value if isinstance(value, str) and value else None


def as_vector(value):
    """Return (x, y, z) floats or None."""
    if not isinstance(value, dict):
        return None
    x, y, z = as_float(value.get("x")), as_float(value.get("y")), as_float(value.get("z"))
    if x is None or y is None:
        return None
    return (x, y, z if z is not None else 0.0)


def field(obj, *names):
    """Tolerant payload lookup: exact key first, then case-insensitive.

    Decoded payload keys are C# property names (PascalCase) because the parser
    calls ``MarkDecoded(TargetProperty.Name)``; the export name (e.g.
    ``bAliveAfterDamage``) is a wire name and never appears in JSON. Both spellings
    are accepted here so a parser build that changes that mapping still works.
    """
    if not isinstance(obj, dict):
        return None
    for name in names:
        if name in obj:
            return obj[name]
    lowered = {k.lower(): v for k, v in obj.items() if isinstance(k, str)}
    for name in names:
        if name.lower() in lowered:
            return lowered[name.lower()]
    return None


def count_by(items, key):
    """Count ``key`` over an iterable of dicts; None keys are labelled "<none>"."""
    counts = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        label = item.get(key)
        label = label if isinstance(label, str) else "<none>"
        counts[label] = counts.get(label, 0) + 1
    return dict(sorted(counts.items()))


def codename_from_path(path):
    """Return the agent codename when ``path`` is a player-character class path.

    Strict form: ``/Game/Characters/<Code>/<Code>_PC.…`` -- this excludes ability,
    projectile and game-object classes that also live under ``/Game/Characters/``.
    """
    if not isinstance(path, str) or "/Game/Characters/" not in path:
        return None
    match = CHARACTER_PATH_RE.search(path)
    return match.group(1) if match else None


def open_text(path):
    """Open a raw NDJSON path for text reading; ``.gz`` is decompressed transparently."""
    if str(path).endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8")
    return open(path, "r", encoding="utf-8")


def resolve_raw_ndjson(raw_dir, name):
    """Path to ``name`` — or ``name + '.gz'`` when the pipeline compressed raw/ — else None."""
    plain = os.path.join(raw_dir, name)
    if os.path.isfile(plain):
        return plain
    packed = plain + ".gz"
    return packed if os.path.isfile(packed) else None


def iter_ndjson(path):
    """Yield parsed JSON objects from a newline-delimited JSON file (plain or ``.gz``)."""
    with open_text(path) as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError as exc:  # pragma: no cover - corrupt input guard
                raise NormalizeError("invalid NDJSON line in %s: %s" % (path, exc))
            if isinstance(record, dict):
                yield record


def load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


# --------------------------------------------------------------------------
# Raw export scan
# --------------------------------------------------------------------------

class PlayerInfo(object):
    __slots__ = ("guid", "codename", "agent", "subject", "first_seen_ms", "state_guids")

    def __init__(self, guid):
        self.guid = guid
        self.codename = None
        self.agent = None
        self.subject = None
        self.first_seen_ms = None
        self.state_guids = set()

    def note_seen(self, time_ms):
        if self.first_seen_ms is None or (time_ms is not None and time_ms < self.first_seen_ms):
            self.first_seen_ms = time_ms


class RawScan(object):
    """Everything the normalizer needs, extracted from one raw export dir."""

    def __init__(self):
        self.players = {}            # character guid -> PlayerInfo (chosen P ids only)
        self.spawned_characters = {} # character guid -> "SpawnedCharacter" evidence
        self.possessed_characters = {}  # character guid -> "PossessedCharacter" evidence
        self.spawn_characters = {}   # character guid -> codename (actor_spawned evidence)
        self.group_characters = {}   # character guid -> codename (character export group evidence)
        self.state_to_char = {}      # player-state guid -> character guid
        self.char_to_state = {}      # character guid -> player-state guid
        self.round_starts = []       # [ms]
        self.buy_phase_ends = []     # [ms]
        self.side_switch_times = []  # [ms]
        self.round_resets = []       # [ms]
        self.end_round_times = []    # [ms]
        self.phase_events = []       # [(ms, phase)]
        self.snapshots = []          # [(ms, {"RoundNumber":..., "Phase":..., "BombState":...})]
        self.round_outcomes = {}     # 1-based round -> replay-proven outcome dict
        self.life_changes = []       # [(ms, char_guid, component_guid, life_result, alive_after, kind)]
        self.kills = []              # [(ms, killer_guid, victim_guid)]
        self.plants = []             # [(ms, planter_guid, x, y, site_byte)]
        self.defuses = []            # [(ms, defuser_guid)]
        self.explosions = []         # [ms]
        self.pickups = []            # [(ms, holder_guid)]
        self.drops = []              # [(ms, holder_guid)]
        self.damage = []             # [(ms, victim_guid, killer_state_guid|None, killed_bool, alive_bool)]
        self.deaths = []             # [(ms, downed_guid)]
        self.resurrects = []         # [(ms, resurrected_guid)]
        self.ults = []               # [(ms, state_guid)]
        self.health = []             # [(ms, char_guid, hp_float)]
        self.subjects = {}           # character guid -> subject string
        self.warnings = []
        self.movement = {}           # char guid -> [(ms, x, y, z, yaw)]
        self.movement_times = []     # sorted absolute sample times (all chars)
        self.strings_seen = set()    # candidate path strings for map detection
        self.event_count = 0
        self.export_manifest = None
        self.pipeline_manifest = None
        self.character_children = {}  # spawned non-player actors, unused but recorded
        # P7-1A2: utility_track.UtilityCollector, fed inside scan_events' single pass.
        self.utility: typing.Any = None

    # -- record dispatch ---------------------------------------------------

    def add_warning(self, message):
        if message not in self.warnings:
            self.warnings.append(message)

    def note_string(self, *values):
        for value in values:
            if isinstance(value, str) and "/Game/Maps/" in value:
                self.strings_seen.add(value)


def _record_time(record):
    return as_int(record.get("time_ms"))


def scan_events(path, scan):
    collector = scan.utility  # P7-1A2: the ability tracker rides this same single pass
    for record in iter_ndjson(path):
        scan.event_count += 1
        if collector is not None:
            collector.feed(record)
        kind = record.get("type")
        time_ms = _record_time(record)
        if kind == "actor_spawned":
            _scan_actor_spawned(record, time_ms, scan)
        elif kind == "export_group_received":
            _scan_export_group(record, time_ms, scan)
        elif kind == "rpc_received":
            _scan_rpc(record, time_ms, scan)
        elif kind == "round_outcome":
            _scan_round_outcome(record, scan)
        elif kind == "player_life_change":
            _scan_player_life_change(record, time_ms, scan)
        # actor_closed / valorant_shot_received carry no viewer-schema data today.


def _scan_actor_spawned(record, time_ms, scan):
    scan.note_string(record.get("actor_path"), record.get("archetype_path"),
                     record.get("replication_class_path"))
    for candidate in (record.get("replication_class_path"), record.get("actor_path"),
                      record.get("archetype_path")):
        codename = codename_from_path(candidate)
        if codename:
            guid = as_int(record.get("actor_net_guid"))
            if guid is None:
                return
            scan.spawn_characters.setdefault(guid, codename)
            return


def _truthy(value):
    return value is not None and value is not False


def _note_character(scan, guid, kind, codename=None, subject=None, state_guid=None):
    """Record character / player-state evidence for one character actor.

    ``kind`` is "spawned" (the player state says which character originally
    spawned it) or "possessed" (the currently possessed actor -- which also names
    ability actors a player is controlling, e.g. a drone or camera).
    """
    if guid is None:
        return
    if kind == "spawned":
        scan.spawned_characters.setdefault(guid, "SpawnedCharacter")
    elif kind == "possessed":
        scan.possessed_characters.setdefault(guid, "PossessedCharacter")
    if codename:
        scan.group_characters.setdefault(guid, codename)
    if state_guid is not None:
        scan.state_to_char[state_guid] = guid
        scan.char_to_state.setdefault(guid, state_guid)
    if subject:
        scan.subjects.setdefault(guid, subject)


def _scan_export_group(record, time_ms, scan):
    if record.get("is_deleted") is True:
        return
    path = record.get("export_group_path") or record.get("object_path") or record.get("class_path")
    scan.note_string(path, record.get("object_path"), record.get("class_path"), record.get("outer_path"))
    payload = record.get("payload")
    actor_guid = as_int(record.get("actor_net_guid"))

    if path == PATH_ARES_ATTRIBUTE_SET:
        hp = as_float(field(payload, "Health"))
        if actor_guid is not None and hp is not None:
            scan.health.append((time_ms if time_ms is not None else 0, actor_guid, hp))
        return

    if path == PATH_BOMB_PLAYER_STATE:
        _scan_bomb_player_state(payload, actor_guid, time_ms, scan)
        return

    if path == PATH_BOMB_GAME_STATE:
        _scan_bomb_game_state(payload, time_ms, scan)
        return

    codename = codename_from_path(path)
    if codename:
        # Player-character actor export group: strong identity evidence (it carries
        # the owning PlayerState guid).
        state_guid = as_int(field(payload, "PlayerState"))
        _note_character(scan, actor_guid, "group", codename=codename, state_guid=state_guid)
        return


def _scan_bomb_player_state(payload, actor_guid, time_ms, scan):
    if not isinstance(payload, dict):
        return
    subject = as_str(field(payload, "Subject"))
    spawned = as_int(field(payload, "SpawnedCharacter"))
    possessed = as_int(field(payload, "PossessedCharacter"))
    if spawned:
        _note_character(scan, spawned, "spawned", subject=subject, state_guid=actor_guid)
    if possessed:
        _note_character(scan, possessed, "possessed", subject=subject, state_guid=actor_guid)
    ult_active = field(payload, "UltimateActive", "bUltimateActive")
    if ult_active is True and actor_guid is not None:
        scan.ults.append((time_ms if time_ms is not None else 0, actor_guid))


def _scan_bomb_game_state(payload, time_ms, scan):
    if not isinstance(payload, dict):
        return
    # The authoritative round result lives inside the BombGameState export group.
    # A payload that carries one may carry nothing else (measured 2026-10-14 on
    # the 13.06 Haven export: keys are GamePhaseElapsedTime / StateRemainingTime /
    # DisplayRemainingTime / RoundResults), so results are read before -- and
    # independently of -- the round-number/phase guard below.
    _scan_round_results(payload, time_ms, scan)
    round_number = as_int(field(payload, "RoundNumber"))
    phase = as_int(field(payload, "Phase"))
    bomb_state = as_int(field(payload, "BombState"))
    if round_number is None and phase is None and bomb_state is None:
        return
    scan.snapshots.append((
        time_ms if time_ms is not None else 0,
        {
            "RoundNumber": round_number,
            "Phase": phase,
            "BombState": bomb_state,
        },
    ))
    if phase == PHASE_SWITCHING_TEAMS:
        scan.side_switch_times.append(time_ms if time_ms is not None else 0)


def normalize_end_type(value):
    """Normalize a reported round end type to the contract vocabulary.

    ``elimination`` / ``detonate`` / ``defuse`` / ``time`` are the contract values;
    the parser's ``time_expired`` (and close spellings) map onto ``time``. Any other
    value is returned unchanged -- an unknown enum is reported, never rewritten into
    a value the replay did not state.
    """
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    if not text:
        return None
    return _END_TYPE_ALIASES.get(text, text)


def _scan_round_results(payload, time_ms, scan):
    """Publish every ``RoundResults`` entry of a BombGameState payload.

    ``RoundNumber`` inside ``RoundResults`` is zero based (measured: the first
    round of the 13.06 Haven export reports ``RoundNumber=0``); it is converted to
    the viewer's 1-based round number here, once, so every consumer sees one basis.
    """
    results = field(payload, "RoundResults")
    if not isinstance(results, list):
        return
    for item in results:
        if not isinstance(item, dict):
            continue
        zero_based = as_int(field(item, "RoundNumber"))
        if zero_based is None or zero_based < 0:
            scan.add_warning(
                "BombGameState RoundResults entry without a usable RoundNumber; ignored"
            )
            continue
        _note_round_outcome(
            scan, zero_based + 1,
            end_type=normalize_end_type(_outcome_name(field(item, "RoundResult"))),
            role=_role_name(field(item, "WinningTeamRole")),
            team=as_str(field(item, "WinningTeam")),
            red_score=None, blue_score=None, time_ms=time_ms,
            source="bomb_game_state",
        )


def _scan_round_outcome(record, scan):
    """Parser overlay row ``round_outcome`` (ReplayExportSink, P3/P2-A, 2026-10-14).

    Fields: round_number (1-based), winner_team, winner_role, end_type, red_score,
    blue_score. Only the fields the row actually carries are used.
    """
    number = as_int(record.get("round_number"))
    if number is None or number < 1:
        scan.add_warning("round_outcome row without a usable round_number; ignored")
        return
    _note_round_outcome(
        scan, number,
        end_type=normalize_end_type(record.get("end_type")),
        role=_role_name(record.get("winner_role")),
        team=as_str(record.get("winner_team")),
        red_score=as_int(record.get("red_score")),
        blue_score=as_int(record.get("blue_score")),
        time_ms=_record_time(record),
        source="round_outcome",
    )


def _note_round_outcome(scan, number, end_type, role, team, red_score, blue_score,
                        time_ms, source):
    """Merge one round-outcome observation; the richer source wins, conflicts warn."""
    incoming = {
        "n": number,
        "endType": end_type,
        "winnerRole": role,
        "winnerTeam": team,
        "redScore": red_score,
        "blueScore": blue_score,
        "timeMs": time_ms,
        "source": source,
    }
    existing = scan.round_outcomes.get(number)
    if existing is None:
        scan.round_outcomes[number] = incoming
        return
    keep, drop = existing, incoming
    if _OUTCOME_RANK.get(incoming["source"], 0) > _OUTCOME_RANK.get(existing["source"], 0):
        keep, drop = incoming, existing
    for key in ("endType", "winnerRole", "winnerTeam"):
        if keep.get(key) is not None and drop.get(key) is not None and keep[key] != drop[key]:
            scan.add_warning(
                "round %d outcome sources disagree on %s (%s=%r, %s=%r); keeping %s"
                % (number, key, drop["source"], drop[key], keep["source"], keep[key],
                   keep["source"])
            )
    if keep is incoming:
        merged = dict(existing)
        for key, value in incoming.items():
            if value is not None:
                merged[key] = value
        scan.round_outcomes[number] = merged


def _scan_player_life_change(record, time_ms, scan):
    """Parser overlay row ``player_life_change`` -- absolute life after a change.

    One row per changed life component (health / shield), never a full health value
    by itself: ``health_series_from_life_changes`` picks the character's health
    component out of these rows.
    """
    guid = as_int(record.get("character_net_guid"))
    component = as_int(record.get("changed_component_net_guid"))
    life = as_float(record.get("life_result"))
    if guid is None or component is None or life is None:
        return
    if time_ms is None:
        time_ms = 0
    scan.life_changes.append((
        time_ms, guid, component, life,
        record.get("alive_after_change") if isinstance(record.get("alive_after_change"), bool) else None,
        as_str(record.get("change_kind")),
    ))


def _role_name(value):
    if isinstance(value, str):
        lowered = value.strip().lower()
        return lowered if lowered in _TEAM_ROLE_BY_NAME else (lowered or None)
    number = as_int(value)
    if number is None:
        return None
    return _TEAM_ROLE_NAMES.get(number)


def _outcome_name(value):
    if isinstance(value, str):
        return value.strip().lower() or None
    number = as_int(value)
    if number is None:
        return None
    return _ROUND_OUTCOME_NAMES.get(number)


def _rpc_path(record):
    path = record.get("function_export_path")
    if isinstance(path, str) and path:
        return path
    name = record.get("function_name")
    class_path = record.get("class_path")
    if isinstance(name, str) and isinstance(class_path, str):
        return class_path + ":" + name
    return None


def _scan_rpc(record, time_ms, scan):
    path = _rpc_path(record)
    scan.note_string(path, record.get("class_path"))
    payload = record.get("payload")
    if time_ms is None:
        time_ms = 0

    if path == RPC_ROUND_START:
        scan.round_starts.append(time_ms)
    elif path == RPC_BUY_PHASE_END:
        scan.buy_phase_ends.append(time_ms)
    elif path == RPC_SIDE_SWITCH:
        scan.side_switch_times.append(time_ms)
    elif path == RPC_ROUND_RESET:
        scan.round_resets.append(time_ms)
    elif path == RPC_END_ROUND:
        scan.end_round_times.append(time_ms)
    elif path == RPC_SET_PHASE:
        phase = as_int(field(payload, "NewPhase"))
        scan.phase_events.append((time_ms, phase))
        if phase == PHASE_SWITCHING_TEAMS:
            scan.side_switch_times.append(time_ms)
    elif path == RPC_KILLED_ENEMY:
        killer = as_int(field(payload, "KillerCharacter"))
        victim = as_int(field(payload, "KilledCharacter"))
        if killer is not None and victim is not None:
            scan.kills.append((time_ms, killer, victim))
    elif path == RPC_BOMB_PLANTED:
        planter = as_int(field(payload, "BombPlanter"))
        location = as_vector(field(payload, "PlantLocation"))
        site = as_int(field(payload, "PlantSite"))
        scan.plants.append((
            time_ms,
            planter,
            location[0] if location else None,
            location[1] if location else None,
            site,
        ))
    elif path == RPC_BOMB_DEFUSED:
        scan.defuses.append((time_ms, as_int(field(payload, "DefusingCharacter"))))
    elif path == RPC_BOMB_EXPLODED:
        scan.explosions.append(time_ms)
    elif path == RPC_BOMB_PICKED_UP:
        scan.pickups.append((time_ms, as_int(field(payload, "NewBombHolder"))))
    elif path == RPC_BOMB_DROPPED:
        scan.drops.append((time_ms, as_int(field(payload, "OldBombHolder"))))
    elif path in (RPC_DAMAGE_BASE, RPC_DAMAGE_POINT):
        victim = as_int(field(payload, "Character", "EventInstigatorPawn"))
        killer_state = as_int(field(payload, "KillCreditPlayerState", "DamagerPlayerState"))
        scan.damage.append((
            time_ms,
            victim,
            killer_state,
            as_bool(field(payload, "DamageKilledTarget", "bDamageKilledTarget")),
            as_bool(field(payload, "AliveAfterDamage", "bAliveAfterDamage")),
        ))
    elif path in (RPC_TEMP_DEATH_BASE, RPC_TEMP_DEATH_POINT):
        downed = as_int(field(payload, "DownedPlayer"))
        if downed is not None:
            scan.deaths.append((time_ms, downed))
    elif path == RPC_RESURRECT:
        resurrected = as_int(field(payload, "ResurrectedPlayer"))
        if resurrected is not None:
            scan.resurrects.append((time_ms, resurrected))
    elif path == RPC_RESPAWN_RESET:
        scan.round_resets.append(time_ms)


def scan_movement(path, scan):
    for record in iter_ndjson(path):
        time_ms = _record_time(record)
        guid = as_int(record.get("shooter_character_net_guid"))
        if guid is None:
            guid = as_int(record.get("actor_net_guid"))
        if guid is None:
            continue
        position = record.get("position")
        if not isinstance(position, dict):
            continue
        x, y = as_float(position.get("x")), as_float(position.get("y"))
        if x is None or y is None:
            continue
        z = as_float(position.get("z"))
        yaw = norm_yaw(record.get("yaw"))
        scan.movement.setdefault(guid, []).append((
            time_ms if time_ms is not None else 0, x, y, z if z is not None else 0.0, yaw,
        ))
        scan.movement_times.append(time_ms if time_ms is not None else 0)

# --------------------------------------------------------------------------
# Derived model
# --------------------------------------------------------------------------

def read_export_manifest(raw_dir):
    manifest = load_json(os.path.join(raw_dir, "manifest.json"))
    return manifest if isinstance(manifest, dict) else None


def read_pipeline_manifest(raw_dir):
    sibling = os.path.join(os.path.dirname(os.path.abspath(raw_dir)), "manifest.json")
    manifest = load_json(sibling)
    return manifest if isinstance(manifest, dict) else None


def finalize_players(scan):
    """Order the player characters into stable P1..P10 ids.

    Identity evidence, strongest first:
      1. characters named by a BombPlayerState payload (``SpawnedCharacter`` /
         ``PossessedCharacter``) or by a player-character export group's
         ``PlayerState`` link -- the export's own statement of who is a player;
      2. characters seen as ``/Game/Characters/<Code>/<Code>_PC`` actor spawns;
      3. characters whose movement was replicated the earliest (last resort, warned).

    P ids are assigned in ascending character-actor-guid order, so they are stable
    for a given replay; they are the replay's actor references, not Riot identities.
    """
    chosen = {}
    source = None
    if len(scan.spawned_characters) >= 2:
        chosen = dict(scan.spawned_characters)
        source = "BombPlayerState SpawnedCharacter"
    elif scan.group_characters:
        chosen = dict(scan.group_characters)
        source = "character export groups"
    elif len(scan.possessed_characters) >= 2:
        chosen = dict(scan.possessed_characters)
        source = "BombPlayerState PossessedCharacter"
        scan.add_warning(
            "player ids fall back to PossessedCharacter; ability actors a player was "
            "controlling may be included"
        )
    elif scan.spawn_characters:
        chosen = dict(scan.spawn_characters)
        source = "actor_spawned character paths"
    if not chosen:
        candidates = []
        for guid, samples in scan.movement.items():
            first = min(sample[0] for sample in samples)
            candidates.append((first, guid))
        candidates.sort()
        chosen = {guid: "movement" for _first, guid in candidates[:MAX_PLAYERS]}
        source = "earliest replicated movement actors (no player identity in export)"
        scan.add_warning(
            "no player-state or character-actor evidence in the raw export; player ids "
            "fall back to the earliest moving characters"
        )
    if scan.movement:
        missing = [guid for guid in chosen if guid not in scan.movement]
        if missing:
            scan.add_warning(
                "%d identified player character(s) have no movement samples" % len(missing)
            )
    ordered = sorted(chosen)
    if len(ordered) > MAX_PLAYERS:
        scan.add_warning(
            "%d player characters identified; keeping the %d lowest actor guids as P1..P%d "
            "(source: %s)" % (len(ordered), MAX_PLAYERS, MAX_PLAYERS, source)
        )
        ordered = ordered[:MAX_PLAYERS]
    else:
        scan.add_warning("player identity source: %s (%d characters)" % (source, len(ordered)))

    players = {}
    for index, guid in enumerate(ordered, start=1):
        info = PlayerInfo(guid)
        info.codename = scan.group_characters.get(guid) or scan.spawn_characters.get(guid)
        info.agent = AGENT_BY_CODENAME.get(info.codename) if info.codename else None
        info.subject = scan.subjects.get(guid)
        if scan.movement.get(guid):
            info.first_seen_ms = min(sample[0] for sample in scan.movement[guid])
        players["P%d" % index] = info
    return players


def detect_map(scan, raw_dir):
    map_info = None
    pipeline = scan.pipeline_manifest
    if isinstance(pipeline, dict):
        candidate = pipeline.get("map")
        if isinstance(candidate, dict):
            path = candidate.get("path")
            known = MAPS.get(path) if isinstance(path, str) else None
            if known:
                map_info = dict(known)
            elif isinstance(candidate.get("name"), str):
                map_info = {"name": candidate.get("name"), "path": path}
    if map_info is None:
        for text in sorted(scan.strings_seen):
            match = MAP_PATH_RE.search(text)
            if not match:
                continue
            folder = match.group(1)
            path = "/Game/Maps/%s/%s" % (folder, folder)
            known = MAPS.get(path)
            if known:
                map_info = dict(known)
                break
            if folder in MAP_FOLDER_NAMES:
                map_info = {"name": MAP_FOLDER_NAMES[folder], "path": path}
                break
    if map_info is None:
        scan.add_warning("map not identifiable from the raw export")
        return {
            "name": None, "path": None, "uuid": None, "image": None,
            "xMultiplier": None, "yMultiplier": None,
            "xScalarToAdd": None, "yScalarToAdd": None,
        }
    return {
        "name": map_info.get("name"),
        "path": map_info.get("path"),
        "uuid": map_info.get("uuid"),
        "image": map_info.get("image"),
        "xMultiplier": map_info.get("xMultiplier"),
        "yMultiplier": map_info.get("yMultiplier"),
        "xScalarToAdd": map_info.get("xScalarToAdd"),
        "yScalarToAdd": map_info.get("yScalarToAdd"),
    }


def detect_match_id(raw_dir, scan):
    pipeline = scan.pipeline_manifest
    if isinstance(pipeline, dict):
        found = as_str(pipeline.get("id"))
        if found:
            return found
    return os.path.basename(os.path.dirname(os.path.abspath(raw_dir))) or None


def detect_patch(scan):
    manifest = scan.export_manifest or {}
    branch = manifest.get("replay_build")
    if isinstance(branch, str):
        match = BRANCH_PATCH_RE.search(branch)
        if match:
            return match.group(1)
    version = manifest.get("replay_version")
    if isinstance(version, str) and version:
        return version
    pipeline = scan.pipeline_manifest or {}
    branch = pipeline.get("branch")
    if isinstance(branch, str):
        match = BRANCH_PATCH_RE.search(branch)
        if match:
            return match.group(1)
    return None


def detect_branch(scan):
    manifest = scan.export_manifest or {}
    branch = manifest.get("replay_build")
    if isinstance(branch, str) and branch:
        return branch
    pipeline = scan.pipeline_manifest or {}
    branch = pipeline.get("branch")
    return branch if isinstance(branch, str) and branch else None


def detect_duration(scan):
    manifest = scan.export_manifest or {}
    duration = as_int(manifest.get("duration_ms"))
    if duration is not None and duration > 0:
        return duration
    if scan.movement_times:
        return max(scan.movement_times)
    pipeline = scan.pipeline_manifest or {}
    duration = as_int(pipeline.get("durationMs"))
    return duration if duration is not None and duration > 0 else None


def spawn_teleport_ms(scan, players, marker_ms, window_ms=TELEPORT_WINDOW_MS):
    """Time at which a round's players are teleported onto their spawn positions.

    Walks each player's movement series forward from ``marker_ms`` and takes the
    first sample that is separated from the previous one by more than
    ``TELEPORT_UNITS`` (a spawn teleport; in-round movement is ~10x smaller per
    sample) or that is the series' first sample at all (round 1 has nothing
    before it). The latest time inside the resulting cluster is returned, so
    every player has already teleported when the round's frames begin. Returns
    None when the movement stream shows no such teleport inside the window.
    """
    if not players:
        return None
    jumps = []
    for info in players.values():
        series = scan.movement.get(info.guid)
        if not series:
            continue
        times = [sample[0] for sample in series]
        index = bisect.bisect_left(times, marker_ms)
        while index < len(times) and times[index] <= marker_ms + window_ms:
            previous = series[index - 1] if index > 0 else None
            if previous is None:
                jumps.append(times[index])
                break
            distance = math.hypot(series[index][1] - previous[1], series[index][2] - previous[2])
            if distance > TELEPORT_UNITS:
                jumps.append(times[index])
                break
            index += 1
    if not jumps:
        return None
    first = min(jumps)
    return max(time for time in jumps if time <= first + TELEPORT_CLUSTER_MS)


def build_rounds(scan, duration_ms, players=None):
    """Return [(n, start_ms, end_ms, round_results_dict_or_None)] ordered by n.

    ``start_ms`` is the round's first *frame* time: the boundary marker, moved up
    to the spawn teleport when the movement stream proves the players were still
    holding their previous-round positions at the marker.
    """
    snapped = []

    def frame_origin(number, marker):
        if players is None:
            return marker
        teleport = spawn_teleport_ms(scan, players, marker)
        if teleport is None or teleport <= marker:
            return marker
        snapped.append((number, marker, teleport))
        scan.add_warning(
            "round %d starts at the spawn teleport %d ms instead of the boundary marker "
            "%d ms: the marker precedes the teleport, which left the opening frames holding "
            "the previous round's positions" % (number, teleport, marker)
        )
        return teleport

    round_numbers = []
    first_seen = {}
    for time_ms, snapshot in scan.snapshots:
        number = snapshot["RoundNumber"]
        if number is None or number < 1:
            continue
        if number not in first_seen:
            first_seen[number] = time_ms
            round_numbers.append(number)
    round_numbers.sort()

    if not round_numbers:
        if scan.round_starts:
            scan.add_warning(
                "round boundaries taken from %d ClientRoundStart events: the BombGameState "
                "actor export (authoritative round numbers) is absent" % len(scan.round_starts)
            )
            starts = sorted(scan.round_starts)
            rounds = []
            for index, start in enumerate(starts):
                end = starts[index + 1] if index + 1 < len(starts) else (duration_ms or start)
                rounds.append((index + 1, frame_origin(index + 1, start), end,
                               scan.round_outcomes.get(index + 1)))
            return rounds
        scan.add_warning(
            "no round markers found in the raw export; emitted one round spanning the match"
        )
        end = duration_ms if duration_ms else max(scan.movement_times or [0])
        return [(1, frame_origin(1, 0), end, None)]

    starts = sorted(scan.round_starts)
    if len(starts) < len(round_numbers):
        if starts:
            scan.add_warning(
                "%d ClientRoundStart events for %d rounds: using first-observation times"
                % (len(starts), len(round_numbers))
            )
        start_by_round = {number: first_seen[number] for number in round_numbers}
    else:
        start_by_round = {number: starts[index] for index, number in enumerate(round_numbers)}

    # Round end: the last BombGameState snapshot still reporting this round number.
    last_seen = {}
    for time_ms, snapshot in scan.snapshots:
        number = snapshot["RoundNumber"]
        if number in start_by_round:
            last_seen[number] = max(last_seen.get(number, time_ms), time_ms)

    results_by_round = scan.round_outcomes

    rounds = []
    for index, number in enumerate(round_numbers):
        start = frame_origin(number, start_by_round[number])
        window_end = None
        if index + 1 < len(round_numbers):
            window_end = start_by_round[round_numbers[index + 1]]
        # Prefer the replay's own MulticastEndRound marker inside this round's window.
        end = None
        candidates = [time_ms for time_ms in scan.end_round_times
                      if time_ms >= start and (window_end is None or time_ms < window_end)]
        if candidates:
            end = min(candidates)
        if end is None:
            observed = last_seen.get(number)
            if observed is not None and observed > start:
                end = observed
        if end is None:
            if window_end is not None:
                end = window_end
            elif duration_ms:
                end = duration_ms
            else:
                end = start
        if end <= start:
            end = start
        rounds.append((number, start, end, results_by_round.get(number)))
    return rounds


def cluster_teams(scan, players, first_round):
    """Split players into two teams from their round-1 spawn positions.

    Deterministic: project the opening positions onto whichever world axis has
    the larger spread, sort by that coordinate, and split 5/5 at the midpoint.
    Returns {player_id: cluster_index} and records a warning when it cannot split.
    """
    if first_round is None:
        return {}
    start, end = first_round[1], first_round[2]
    positions = []
    for player_id, info in players.items():
        samples = scan.movement.get(info.guid)
        if not samples:
            continue
        sample = None
        for candidate in samples:
            if start <= candidate[0] <= (start + 1500):
                sample = candidate
                break
        if sample is None:
            for candidate in samples:
                if candidate[0] <= end:
                    sample = candidate
        if sample is None:
            continue
        positions.append((player_id, sample[1], sample[2]))
    if len(positions) < 2:
        scan.add_warning("team split needs opening positions; team left null")
        return {}
    spread_x = max(item[1] for item in positions) - min(item[1] for item in positions)
    spread_y = max(item[2] for item in positions) - min(item[2] for item in positions)
    axis = 1 if spread_x >= spread_y else 2
    ordered = sorted(positions, key=lambda item: (item[axis], item[0]))
    best_gap, best_split = -1.0, len(ordered) // 2
    for index in range(1, len(ordered)):
        gap = ordered[index][axis] - ordered[index - 1][axis]
        if gap > best_gap:
            best_gap, best_split = gap, index
    clusters = {}
    for index, item in enumerate(ordered):
        clusters[item[0]] = 0 if index < best_split else 1
    sizes = (best_split, len(ordered) - best_split)
    if sizes != (5, 5):
        scan.add_warning(
            "opening-position split is %d/%d rather than 5/5; the spawn split may be unreliable"
            % sizes
        )
    return clusters


def build_side_map(scan, players, clusters, rounds):
    """Decide which team is on attack for every round.

    Contract: team 0 = attackers in round 1. Evidence used, in order:
      1. bomb holder / planter inside a round (the spike belongs to attackers);
      2. side-switch RPC or EAresGamePhase.SwitchingTeams (halftime boundary);
      3. standard competitive half (12 rounds) as the documented fallback.
    Returns (attacker_team_by_round, team0_index, swap_round, evidence_rounds).
    """
    team_by_player = dict(clusters)
    evidence = {}  # round number -> team index on attack

    def team_of(guid):
        if guid is None:
            return None
        for player_id, info in players.items():
            if info.guid == guid:
                return team_by_player.get(player_id)
        return None

    def round_of(time_ms):
        for number, start, end, _ in rounds:
            if start <= time_ms < end:
                return number
        for number, start, end, _ in reversed(rounds):
            if time_ms >= start:
                return number
        return rounds[0][0] if rounds else None

    for time_ms, holder in scan.pickups:
        team = team_of(holder)
        number = round_of(time_ms)
        if team is not None and number is not None and number not in evidence:
            evidence[number] = team
    for time_ms, planter, _, _, _ in scan.plants:
        team = team_of(planter)
        number = round_of(time_ms)
        if team is not None and number is not None and number not in evidence:
            evidence[number] = team

    swap_round = None
    for time_ms in sorted(set(scan.side_switch_times)):
        number = round_of(time_ms)
        if number is not None:
            swap_round = number
            break
    if swap_round is None:
        total = len(rounds)
        if total > SIDE_SWITCH_DEFAULT_ROUND:
            swap_round = SIDE_SWITCH_DEFAULT_ROUND
            scan.add_warning(
                "no side-switch event found; assuming the standard halftime after round %d"
                % SIDE_SWITCH_DEFAULT_ROUND
            )
        elif total:
            swap_round = None

    first_round_number = rounds[0][0] if rounds else None
    team0 = evidence.get(first_round_number)
    if team0 is None:
        # Fall back to the earliest round with any evidence, then to cluster 0.
        for number in sorted(evidence):
            team0 = evidence[number]
            break
    if team0 is None and team_by_player:
        team0 = 0
        scan.add_warning(
            "no spike-handler evidence for the opening round; team 0 = lower spawn cluster "
            "(attacking side unproven)"
        )

    attacker_team = {}
    for number, _, _, _ in rounds:
        if team0 is None:
            attacker_team[number] = evidence.get(number)
        elif swap_round is None or number <= swap_round:
            attacker_team[number] = team0
        else:
            attacker_team[number] = 1 - team0
    return attacker_team, team0, swap_round, evidence


def _colour_name(value):
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("red", "blue"):
            return lowered
    return None


def derive_side_teams(scan, rounds, attacker_team, swap_round):
    """Attacking team per round, proven by the overlay's ``round_outcome`` rows.

    The rows carry, for every completed round, the winning colour
    (``winner_team``), the winning side (``winner_role``) and the cumulative
    Red/Blue score. Two facts make the attacking team exact rather than inferred:

    * **a team's side is constant inside a half**, so a single row with a role
      names the attacking colour of that half (a colour that wins as ``attacker``
      attacked the half; a colour that wins as ``defender`` means the *other*
      colour attacked it);
    * **the colour -> cluster-team mapping is pinned by the score series**: the
      Red score after round *n* must equal the number of rounds up to *n* won by
      whichever cluster team Red is mapped to. The two candidate mappings cannot
      both satisfy that (the two win sequences differ in every round), so a
      candidate that satisfies it on every row is the answer, not a guess.

    Returns ``(by_round, confidence_by_round, note)``. ``by_round`` holds the
    rounds this evidence could prove, ``confidence_by_round`` holds
    ``CONF_REPLAY`` for exactly those rounds, and ``note`` explains the refusal
    when the rows cannot prove it (the caller then keeps the planter/spawn map).
    The row set must be gap-free from round 1: the Red score after round *n* counts
    every round up to *n*, so a hole in the middle makes the series unverifiable
    and the whole map falls back rather than mixing two bases (a prefix of rounds,
    e.g. a half that the export contains and the other half it does not, verifies
    fine and proves every round of the halves it covers).
    """
    rows = {}
    for number in sorted(scan.round_outcomes):
        outcome = scan.round_outcomes.get(number) or {}
        role = outcome.get("winnerRole")
        colour = _colour_name(outcome.get("winnerTeam"))
        if role not in ("attacker", "defender") or colour is None:
            continue
        rows[number] = {
            "role": role, "colour": colour,
            "red": outcome.get("redScore"), "blue": outcome.get("blueScore"),
        }
    if not rows:
        return {}, {}, None

    boundary = swap_round if swap_round is not None else SIDE_SWITCH_DEFAULT_ROUND

    def half_of(number):
        return 0 if number <= boundary else 1

    # Which colour attacked each half? Checked before the score proof so a
    # contradiction inside one half is reported as such rather than as a score
    # mismatch it would also cause.
    attacker_colour = {}
    for number in sorted(rows):
        row = rows[number]
        other = "blue" if row["colour"] == "red" else "red"
        colour = row["colour"] if row["role"] == "attacker" else other
        half = half_of(number)
        known = attacker_colour.get(half)
        if known is not None and known != colour:
            return {}, {}, (
                "round_outcome rows disagree about which colour attacked half %d "
                "(round %d implies %s, earlier rows imply %s); attacking side stays inferred"
                % (half + 1, number, colour, known)
            )
        attacker_colour[half] = colour

    winner_index = {}
    for number in sorted(rows):
        attack_team = attacker_team.get(number)
        if attack_team not in (0, 1):
            return {}, {}, (
                "round_outcome rows cannot pin the attacking team: round %d carries no "
                "attack-side anchor for its winner" % number
            )
        winner_index[number] = (attack_team if rows[number]["role"] == "attacker"
                                else 1 - attack_team)

    candidates = []
    for red_index, blue_index in ((0, 1), (1, 0)):
        red_wins = blue_wins = 0
        consistent = True
        for number in sorted(rows):
            if winner_index[number] == red_index:
                red_wins += 1
            else:
                blue_wins += 1
            row = rows[number]
            if row["red"] is None or row["blue"] is None:
                continue
            if row["red"] != red_wins or row["blue"] != blue_wins:
                consistent = False
                break
        if consistent:
            candidates.append((red_index, blue_index))
    if len(candidates) != 1:
        return {}, {}, (
            "round_outcome winner roles and scores do not pin the colour -> team map "
            "(%d of 2 candidate maps consistent with %d outcome rows); attacking side "
            "stays inferred" % (len(candidates), len(rows))
        )
    colour_index = {"red": candidates[0][0], "blue": candidates[0][1]}

    by_round = {}
    confidence = {}
    for number, _start, _end, _outcome in rounds:
        colour = attacker_colour.get(half_of(number))
        if colour is None:
            continue
        by_round[number] = colour_index[colour]
        confidence[number] = CONF_REPLAY
    if not by_round:
        return {}, {}, (
            "round_outcome rows cover no half with a usable role; attacking side stays inferred"
        )
    return by_round, confidence, None


def health_series_from_life_changes(scan, players):
    """char guid -> sorted [(ms, hp)] derived from ``player_life_change`` rows.

    Every character owns several life components (health plus one or two shields);
    a row's ``life_result`` is the absolute value of *that* component. The health
    component is the one that proves itself above the shield cap
    (``HEALTH_MIN_LIFE``); components that never exceed 50 stay out of the hp
    series, and a character with no provable health component gets no series at
    all -- hp stays absent rather than carrying a shield value. The export decodes
    no armour value, so none is emitted.
    """
    by_char = {}
    for time_ms, guid, component, life, alive_after, _kind in scan.life_changes:
        by_char.setdefault(guid, {}).setdefault(component, []).append(
            (time_ms, life, alive_after))
    series = {}
    for guid, components in by_char.items():
        best_component = None
        best_key = None
        for component, samples in components.items():
            peak = max(sample[1] for sample in samples)
            if peak < HEALTH_MIN_LIFE:
                continue
            # Prefer the component that reached the higher life, then the one that
            # ever reported a false alive_after_change (only health does), then the
            # lower component guid, so the pick is deterministic.
            key = (peak, any(sample[2] is False for sample in samples), -component)
            if best_key is None or key > best_key:
                best_key, best_component = key, component
        if best_component is None:
            continue
        series[guid] = sorted(
            (sample[0], sample[1]) for sample in components[best_component])
    return series


def health_by_player(scan, players):
    """char guid -> sorted [(ms, hp)].

    ``player_life_change`` rows (parser overlay) are absolute per-component life
    values and win when present; the older AresAttributeSet ``Health`` attribute
    stays supported for exports/fixtures that carry it.
    """
    series = {}
    for time_ms, guid, hp in scan.health:
        series.setdefault(guid, []).append((time_ms, hp))
    for guid, samples in health_series_from_life_changes(scan, players).items():
        if samples:
            series[guid] = samples
    for guid in series:
        series[guid].sort(key=lambda item: item[0])
    return series


def hp_at(samples, times, time_ms):
    """Last health sample at or before time_ms (zero-order hold)."""
    index = bisect.bisect_right(times, time_ms) - 1
    return samples[index][1] if index >= 0 else None


def sample_at(samples, times, time_ms):
    """Last movement sample at or before time_ms (zero-order hold)."""
    index = bisect.bisect_right(times, time_ms) - 1
    return samples[index] if index >= 0 else None


def alive_timeline(round_start, round_end, death_times, revive_times):
    """Return a sorted [(ms, 0|1)] step function: alive from round start, dead on
    a kill/death event, alive again on a resurrect/respawn event."""
    timeline = [(round_start, 1)]
    for time_ms in sorted(set(death_times)):
        if round_start < time_ms <= round_end:
            timeline.append((time_ms, 0))
    for time_ms in sorted(set(revive_times)):
        if round_start < time_ms <= round_end:
            timeline.append((time_ms, 1))
    timeline.sort(key=lambda item: item[0])
    return timeline


def alive_at(timeline, time_ms):
    state = 1
    for stamp, value in timeline:
        if stamp <= time_ms:
            state = value
        else:
            break
    return state


def index_events(scan):
    """Group raw events by kind, time-ordered (guid fields may be None)."""
    def by_time(items):
        return sorted(items, key=lambda item: item[0])

    return by_time(scan.kills), by_time(scan.plants), by_time(scan.defuses), by_time(scan.ults)


def player_lookup(scan, players):
    """Return guid -> player id (``P1``..``P10``); player-state guids are resolved."""
    def resolve(guid):
        if guid is None:
            return None
        resolved = scan.state_to_char.get(guid, guid)
        for player_id, info in players.items():
            if info.guid == resolved or info.guid == guid:
                return player_id
        return None

    return resolve


def infer_outcome(scan, players, clusters, attacker_team_index, start_ms, end_ms,
                  plants_in_round, defuses_in_round, kill_index, player_of=None):
    """Derive a round outcome from events when the replay exports none.

    Rules, applied in this order; each one is stated by an event, not assumed:

    * a defuse in the round -> defenders win, end type ``defuse``;
    * every member of one team has a death event in the round -> the other team
      wins, end type ``elimination``;
    * the spike is planted and not defused while at least one attacker is still
      alive -> the spike detonated, attackers win, end type ``detonate``;
    * anything else -> ``(None, None, "unknown")``: left unproven, never guessed.

    Returns ``(winner_team_index_or_None, endType_or_None, confidence)``.
    """
    if attacker_team_index is None:
        return None, None, CONF_UNKNOWN
    defenders = 1 - attacker_team_index
    if defuses_in_round:
        return defenders, "defuse", CONF_INFERRED

    if player_of is None:
        player_of = player_lookup(scan, players)
    rosters = {}
    for player_id in players:
        team = clusters.get(player_id)
        if team in (0, 1):
            rosters.setdefault(team, []).append(player_id)
    dead = set()
    for time_ms, _killer_guid, victim_guid in kill_index:
        if start_ms <= time_ms <= end_ms:
            player_id = player_of(victim_guid)
            if player_id is not None:
                dead.add(player_id)

    for team in (attacker_team_index, defenders):
        roster = rosters.get(team) or []
        if len(roster) == 5 and all(player_id in dead for player_id in roster):
            return 1 - team, "elimination", CONF_INFERRED
    if plants_in_round:
        roster = rosters.get(attacker_team_index) or []
        if roster and any(player_id not in dead for player_id in roster):
            return attacker_team_index, "detonate", CONF_INFERRED
    return None, None, CONF_UNKNOWN


def build_round_document(number, start_ms, end_ms, results, attacker_team, players,
                         scan, health, damage_kills_available,
                         kill_index, plant_index, defuse_index, ult_index,
                         clusters=None):
    frames = max(1, (end_ms - start_ms) // TICK_MS + 1) if end_ms >= start_ms else 1
    atk = attacker_team.get(number)
    player_of = player_lookup(scan, players)

    round_players = {}
    round_events = []

    # ---- kills: MulticastNotifyKilledEnemy is authoritative; damage reports are
    # a documented fallback only when no kill notification exists in the whole match.
    kills_in_round = [
        (time_ms, killer_guid, victim_guid, CONF_REPLAY)
        for time_ms, killer_guid, victim_guid in kill_index
        if start_ms <= time_ms <= end_ms
    ]
    if not kills_in_round and damage_kills_available:
        for time_ms, victim, killer_state, killed, _ in scan.damage:
            if not killed or victim is None or not (start_ms <= time_ms <= end_ms):
                continue
            killer_char = scan.state_to_char.get(killer_state) if killer_state else None
            kills_in_round.append((time_ms, killer_char, victim, CONF_UNKNOWN))
        kills_in_round.sort(key=lambda item: item[0])

    plants_in_round = [item for item in plant_index if start_ms <= item[0] <= end_ms]
    defuses_in_round = [item for item in defuse_index if start_ms <= item[0] <= end_ms]
    ults_in_round = [item for item in ult_index if start_ms <= item[0] <= end_ms]

    for time_ms, killer_guid, victim_guid, confidence in kills_in_round:
        round_events.append({
            "t": time_ms - start_ms,
            "type": "kill",
            "actor": player_of(killer_guid),
            "target": player_of(victim_guid),
            "weapon": None,
            "ability": None,
            "confidence": confidence,
        })
    for time_ms, planter, _px, _py, _site in plants_in_round:
        round_events.append({
            "t": time_ms - start_ms,
            "type": "plant",
            "actor": player_of(planter),
            "target": None,
            "weapon": None,
            "ability": None,
            "confidence": CONF_REPLAY,
        })
    for time_ms, defuser in defuses_in_round:
        round_events.append({
            "t": time_ms - start_ms,
            "type": "defuse",
            "actor": player_of(defuser),
            "target": None,
            "weapon": None,
            "ability": None,
            "confidence": CONF_REPLAY,
        })
    for time_ms, state_guid in ults_in_round:
        actor = player_of(scan.state_to_char.get(state_guid, state_guid))
        if actor is None:
            actor = player_of(state_guid)
        round_events.append({
            "t": time_ms - start_ms,
            "type": "ult",
            "actor": actor,
            "target": None,
            "weapon": None,
            "ability": None,
            "confidence": CONF_REPLAY,
        })
    round_events.sort(key=lambda event: (event["t"], event["type"]))

    # ---- per player frame arrays ----
    death_times_by_player = {}
    revive_times_by_player = {}

    def note_death(guid, time_ms):
        player_id = player_of(guid)
        if player_id is not None:
            death_times_by_player.setdefault(player_id, []).append(time_ms)

    for time_ms, victim_guid, _killer_state, _killed, alive_after in scan.damage:
        if alive_after is False:
            note_death(victim_guid, time_ms)
    for time_ms, _killer_guid, victim_guid in scan.kills:
        note_death(victim_guid, time_ms)
    for time_ms, downed_guid in scan.deaths:
        note_death(downed_guid, time_ms)
    for time_ms, resurrected_guid in scan.resurrects:
        player_id = player_of(resurrected_guid)
        if player_id is not None:
            revive_times_by_player.setdefault(player_id, []).append(time_ms)

    for player_id in sorted(players, key=lambda name: int(name[1:])):
        info = players[player_id]
        series = scan.movement.get(info.guid)
        if not series:
            continue
        sample_times = [sample[0] for sample in series]
        health_series = health.get(info.guid)
        health_times = [sample[0] for sample in health_series] if health_series else None
        timeline = alive_timeline(
            start_ms, end_ms,
            death_times_by_player.get(player_id, []),
            revive_times_by_player.get(player_id, []),
        )
        xs, ys, yaws, hps, alives = [], [], [], [], []
        has_hp = False
        for frame in range(frames):
            time_ms = start_ms + frame * TICK_MS
            sample = sample_at(series, sample_times, time_ms)
            if sample is None:
                xs.append(None)
                ys.append(None)
                yaws.append(None)
            else:
                xs.append(iround(sample[1]))
                ys.append(iround(sample[2]))
                yaws.append(sample[4])
            alive = alive_at(timeline, time_ms)
            alives.append(alive)
            hp = hp_at(health_series, health_times, time_ms) if health_series else None
            if hp is None:
                hps.append(None if alive else 0)
            else:
                has_hp = True
                hps.append(max(0, iround(hp)) if alive else 0)
        entry = {"x": xs, "y": ys, "yaw": yaws}
        if has_hp:
            entry["hp"] = hps
        entry["alive"] = alives
        round_players[player_id] = entry

    spike = {"plantT": None, "plantX": None, "plantY": None, "defuseT": None, "site": None}
    if plants_in_round:
        time_ms, _planter, px, py, _site = plants_in_round[0]
        spike["plantT"] = time_ms - start_ms
        spike["plantX"] = iround(px) if px is not None else None
        spike["plantY"] = iround(py) if py is not None else None
        # PlantSite is an unproven enum byte (EnumRemainingBits); letter sites are not guessed.
    if defuses_in_round:
        spike["defuseT"] = defuses_in_round[0][0] - start_ms

    document = {
        "schema": SCHEMA_ROUND,
        "n": number,
        "startMs": start_ms,
        "endMs": end_ms,
        "tickMs": TICK_MS,
        "frames": frames,
        "players": round_players,
        "events": round_events,
        "spike": spike,
    }
    summary = {
        "winner": None,
        "endType": None,
        "kills": sum(1 for event in round_events if event["type"] == "kill"),
        "plants": len(plants_in_round),
        "defuses": len(defuses_in_round),
        "confidence": CONF_UNKNOWN,
    }
    if results is not None:
        # Replay-proven outcome: the parser's round_outcome row or the BombGameState
        # RoundResults entry. The winning role names the side, which is mapped onto
        # this round's team indices through the side map.
        summary["endType"] = results.get("endType")
        role = results.get("winnerRole")
        if role == "attacker":
            summary["winner"] = atk
        elif role == "defender":
            summary["winner"] = None if atk is None else 1 - atk
        elif role is None and results.get("winnerTeam") is not None:
            # A team name without a role cannot be mapped onto team index 0/1:
            # the export's colour name is not the cluster split. Not guessed.
            scan.add_warning(
                "round %d carries a winning team (%r) but no winning role: winner left "
                "unmapped" % (number, results.get("winnerTeam"))
            )
        summary["confidence"] = CONF_REPLAY
    if summary["winner"] is None and atk is not None:
        inferred_winner, inferred_type, inferred_confidence = infer_outcome(
            scan, players, clusters or {}, atk, start_ms, end_ms,
            plants_in_round, defuses_in_round, kill_index, player_of=player_of,
        )
        if inferred_winner is not None:
            summary["winner"] = inferred_winner
            if summary["endType"] is None:
                summary["endType"] = inferred_type
            summary["confidence"] = inferred_confidence
    return document, summary


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z")


def _check_round_outcomes(scan, rounds):
    """Cross-checks between the round windows and the replay's outcome rows.

    Two things are worth a warning and nothing else: an outcome that names a round
    the export does not contain, and a ``round_outcome`` row whose time falls
    outside the window of the round it claims. Both mean the two views of the
    replay disagree, which the normalizer reports rather than resolves.
    """
    known = set(number for number, _start, _end, _outcome in rounds)
    missing = sorted(number for number in scan.round_outcomes if number not in known)
    if missing:
        scan.add_warning(
            "round outcome rows name round(s) %s that the export does not contain"
            % ", ".join(str(number) for number in missing)
        )
    for number, start_ms, end_ms, outcome in rounds:
        if not outcome or outcome.get("source") != "round_outcome":
            continue
        time_ms = outcome.get("timeMs")
        if time_ms is not None and not (start_ms <= time_ms <= end_ms):
            scan.add_warning(
                "round_outcome for round %d at %d ms falls outside its round window "
                "(%d..%d ms)" % (number, time_ms, start_ms, end_ms)
            )


def _check_full_eliminations(scan, players, clusters, rounds, round_summaries,
                             kill_index, player_of):
    """Rounds where one team lost all five members must be won by the other team.

    Death evidence is the replay's own kill notifications (the same events
    ``container_index.py`` counts), attributed to players through the team split.
    Returns the 1-based rounds with a full elimination; a disagreement with the
    replay's winner is reported as a warning, never silently corrected.
    """
    winners = dict((summary["n"], summary["winner"]) for summary in round_summaries)
    rosters = {}
    for player_id in players:
        team = clusters.get(player_id)
        if team in (0, 1):
            rosters.setdefault(team, []).append(player_id)
    full = []
    for number, start_ms, end_ms, _outcome in rounds:
        dead = set()
        for time_ms, _killer_guid, victim_guid in kill_index:
            if start_ms <= time_ms <= end_ms:
                player_id = player_of(victim_guid)
                if player_id is not None:
                    dead.add(player_id)
        for team in sorted(rosters):
            roster = rosters[team]
            if len(roster) == 5 and all(player_id in dead for player_id in roster):
                full.append(number)
                winner = winners.get(number)
                if winner is not None and winner != 1 - team:
                    scan.add_warning(
                        "round %d eliminated every player of team %d, so team %d had to "
                        "win, but the replay credits team %r"
                        % (number, team, 1 - team, winner)
                    )
    return full


def normalize_directory(raw_dir, viewer_dir, match_id=None, normalized_at=None):
    """Normalize one raw export directory into viewer JSON. Returns a stats dict."""
    raw_dir = os.path.abspath(raw_dir)
    viewer_dir = os.path.abspath(viewer_dir)
    events_path = resolve_raw_ndjson(raw_dir, "events.ndjson")
    movement_path = resolve_raw_ndjson(raw_dir, "movement.ndjson")
    if not events_path and not movement_path:
        raise NormalizeError(
            "no events.ndjson(.gz) or movement.ndjson(.gz) in %s (expected a CliReader "
            "'export --profile viewer' output directory)" % raw_dir
        )

    scan = RawScan()
    scan.export_manifest = read_export_manifest(raw_dir)
    scan.pipeline_manifest = read_pipeline_manifest(raw_dir)
    scan.utility = utility_track.UtilityCollector()  # P7-1A2: fed inside scan_events
    if scan.export_manifest is None:
        scan.add_warning("raw/manifest.json missing; match metadata is incomplete")
    if events_path:
        scan_events(events_path, scan)
    else:
        scan.add_warning("events.ndjson missing")
    if movement_path:
        scan_movement(movement_path, scan)
    else:
        scan.add_warning("movement.ndjson missing")

    players = finalize_players(scan)
    # P7-1A2: track thrown utility from the records the single pass collected.
    # Ownership uses normalize's own per-life character->player map; the agent name
    # is never consulted (Haven has two Sovas and two Jetts).
    _player_of = player_lookup(scan, players)
    utility_items = utility_track.track_records(
        scan.utility.records, resolve_player=lambda guid, _t_ms: _player_of(guid),
        ability_table=utility_track.catalog_rows(load_json(UTILITY_CATALOG_PATH)),
        agent_map=AGENT_BY_CODENAME, map_info=detect_map(scan, raw_dir),
        warnings=scan.warnings)
    duration_ms = detect_duration(scan)
    rounds = build_rounds(scan, duration_ms, players)
    clusters = cluster_teams(scan, players, rounds[0] if rounds else None)
    attacker_team, team0, swap_round, evidence = build_side_map(scan, players, clusters, rounds)
    # The overlay's round_outcome rows can prove the attacking team of a round
    # (winner_role + winner_team colour, with the colour -> cluster team map pinned
    # by the cumulative Red/Blue score series). That proof wins over the
    # planter/spawn map; rounds it cannot cover keep the inferred value and say so.
    proven_sides, proven_confidence, side_note = derive_side_teams(
        scan, rounds, attacker_team, swap_round)
    if side_note:
        scan.add_warning(side_note)
    side_by_round = {}
    side_confidence_by_round = {}
    for number, _start, _end, _outcome in rounds:
        if number in proven_sides:
            side_by_round[number] = proven_sides[number]
            side_confidence_by_round[number] = proven_confidence[number]
        else:
            side_by_round[number] = attacker_team.get(number)
            side_confidence_by_round[number] = CONF_INFERRED
    health = health_by_player(scan, players)
    kill_index, plant_index, defuse_index, ult_index = index_events(scan)
    damage_kills_available = not scan.kills
    if damage_kills_available and scan.damage:
        scan.add_warning(
            "no MulticastNotifyKilledEnemy events; kill events fall back to "
            "MulticastNotifyDamage_* with DamageKilledTarget (confidence marked unknown)"
        )
    _check_round_outcomes(scan, rounds)

    match_id = match_id or detect_match_id(raw_dir, scan)
    export_manifest = scan.export_manifest or {}
    pipeline = scan.pipeline_manifest or {}

    player_list = []
    for player_id in sorted(players, key=lambda name: int(name[1:])):
        info = players[player_id]
        agent_known = info.agent is not None
        player_list.append({
            "id": player_id,
            "name": None,
            "agent": info.agent,
            "agentUuid": AGENT_UUID_BY_NAME.get(info.agent) if info.agent else None,
            "team": clusters.get(player_id),
            "subject": info.subject,
            "confidence": CONF_LOADOUT if agent_known else CONF_UNKNOWN,
        })
    if player_list and any(item["team"] is None for item in player_list):
        scan.add_warning("some players have no team assignment (spawn split incomplete)")

    round_summaries = []
    round_documents = []
    for number, start_ms, end_ms, results in rounds:
        document, summary = build_round_document(
            number, start_ms, end_ms, results, attacker_team, players, scan,
            health, damage_kills_available, kill_index, plant_index, defuse_index,
            ult_index, clusters=clusters,
        )
        document["utility"] = utility_track.round_utility(utility_items, start_ms, end_ms)
        summary["n"] = number
        summary["startMs"] = start_ms
        summary["endMs"] = end_ms
        summary["attackerTeam"] = side_by_round.get(number)
        summary["attackerTeamConfidence"] = side_confidence_by_round.get(number)
        round_documents.append(document)
        round_summaries.append({
            "n": summary["n"],
            "startMs": summary["startMs"],
            "endMs": summary["endMs"],
            "winner": summary["winner"],
            "endType": summary["endType"],
            "kills": summary["kills"],
            "plants": summary["plants"],
            "defuses": summary["defuses"],
            "attackerTeam": summary["attackerTeam"],
            "attackerTeamConfidence": summary["attackerTeamConfidence"],
            "confidence": summary["confidence"],
        })

    if swap_round is None and len(rounds) > SIDE_SWITCH_DEFAULT_ROUND:
        swap_round = SIDE_SWITCH_DEFAULT_ROUND

    full_elimination_rounds = _check_full_eliminations(
        scan, players, clusters, rounds, round_summaries, kill_index,
        player_lookup(scan, players),
    )

    match_document = {
        "schema": SCHEMA_MATCH,
        "id": match_id,
        "source": as_str(pipeline.get("source")) or as_str(export_manifest.get("source_file")),
        "sha256": as_str(export_manifest.get("source_sha256")) or as_str(pipeline.get("sha256")),
        "patch": detect_patch(scan),
        "branch": detect_branch(scan),
        "map": detect_map(scan, raw_dir),
        "durationMs": duration_ms,
        "decode": {
            "status": as_str(pipeline.get("status")) or "ready",
            "parserCommit": as_str(pipeline.get("parserCommit")) or as_str(export_manifest.get("parser_version")),
            "exportedAt": as_str(pipeline.get("decodedAt")),
            "normalizedAt": normalized_at or now_iso(),
        },
        "capabilities": {"utility": 1},
        "players": player_list,
        "utilityCounts": utility_track.player_counts(utility_items, player_list),
        "rounds": round_summaries,
    }

    os.makedirs(os.path.join(viewer_dir, "rounds"), exist_ok=True)
    _dump(os.path.join(viewer_dir, "match.json"), match_document)
    for document in round_documents:
        _dump(os.path.join(viewer_dir, "rounds", "r%02d.json" % document["n"]), document)

    rounds_with_hp = 0
    for _number, start_ms, end_ms, _outcome in rounds:
        for info in players.values():
            samples = health.get(info.guid)
            if samples and any(start_ms <= sample[0] <= end_ms for sample in samples):
                rounds_with_hp += 1
                break

    stats = {
        "match_id": match_id,
        "rounds": len(round_documents),
        "players": len(player_list),
        "events_scanned": scan.event_count,
        "movement_samples": sum(len(items) for items in scan.movement.values()),
        "kills": len(scan.kills),
        "plants": len(scan.plants),
        "defuses": len(scan.defuses),
        "ults": len(scan.ults),
        "team0": team0,
        "swap_round": swap_round,
        "attacker_team_by_round": dict(sorted(attacker_team.items())),
        "attacker_team_source": count_by(round_summaries, "attackerTeamConfidence"),
        "attacker_team_published": dict(sorted(side_by_round.items())),
        "attack_evidence_rounds": dict(sorted(evidence.items())),
        "round_outcome_rows": len(scan.round_outcomes),
        "round_outcome_sources": count_by(scan.round_outcomes.values(), "source"),
        "round_confidence": count_by(round_summaries, "confidence"),
        "full_elimination_rounds": full_elimination_rounds,
        "rounds_with_hp_samples": rounds_with_hp,
        "hp_players": sum(1 for info in players.values() if health.get(info.guid)),
        "life_changes": len(scan.life_changes),
        "warnings": list(scan.warnings),
        "viewer_dir": viewer_dir,
    }
    return stats


def _dump(path, value):
    text = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.write("\n")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    normalized_at = os.environ.get("VALOHUB_NORMALIZED_AT") or None
    match_id = None
    quiet = False
    positional = []
    index = 0
    while index < len(argv):
        argument = argv[index]
        if argument in ("-h", "--help"):
            sys.stdout.write(__doc__ or "")
            return 0
        if argument == "--normalized-at":
            index += 1
            normalized_at = argv[index]
        elif argument == "--id":
            index += 1
            match_id = argv[index]
        elif argument == "--quiet":
            quiet = True
        else:
            positional.append(argument)
        index += 1
    if len(positional) != 2:
        sys.stderr.write(
            "usage: normalize.py <raw_dir> <viewer_dir> [--id ID] [--normalized-at ISO] [--quiet]\n"
        )
        return 2
    try:
        stats = normalize_directory(positional[0], positional[1], match_id=match_id,
                                    normalized_at=normalized_at)
    except NormalizeError as exc:
        sys.stderr.write("error: %s\n" % exc)
        return 1
    for warning in stats["warnings"]:
        sys.stderr.write("warning: %s\n" % warning)
    if not quiet:
        sys.stdout.write(
            "normalized %s -> %s: rounds=%d players=%d events=%d movement_samples=%d "
            "(kills=%d plants=%d defuses=%d ults=%d)\n"
            % (positional[0], stats["viewer_dir"], stats["rounds"], stats["players"],
               stats["events_scanned"], stats["movement_samples"], stats["kills"],
               stats["plants"], stats["defuses"], stats["ults"])
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())

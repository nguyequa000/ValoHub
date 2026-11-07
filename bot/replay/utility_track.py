#!/usr/bin/env python3
"""ValoHub ability tracker: where thrown utility flies, bounces and lands.

Pure Python standard library only (``BUILD_CONTRACT.md`` section 5). No file IO:
every function takes decoded NDJSON records (dicts) that the caller already read
from ``events.ndjson[.gz]`` and returns plain JSON-safe structures.

Input records (the CliReader ``viewer`` profile export, see
``reports/p7-0-decoder-audit.md``):

* ``actor_spawned``         - class path, spawn time, true-world transform + velocity
* ``export_group_received`` - payload fields ``Instigator`` / ``Owner`` /
                              ``ReplicatedMovement`` (position + velocity samples)
* ``actor_closed``          - close time and reason

``movement.ndjson[.gz]`` holds **no** projectiles (P7-0 data inventory 5.2): one
controller stream, one ``actor_net_guid``. Ability motion samples only exist
inside ``events.ndjson[.gz]`` export groups.

Data facts this module is built on (P7-0 gate, re-verified against the Haven raw
bundle while writing this file):

* ``ReplicatedMovement.location`` is stored **divided by 100**; the spawn
  transform and every velocity are 1:1. So ``world = raw_location * 100`` and the
  spawn/first-sample ratio must land in 99..101 (``scale_ratio``).
* The payload field names in the Haven export are PascalCase
  (``ReplicatedMovement``), while the P7-0 reports quote them lower-case; both
  spellings are accepted here (``_payload_field``).
* ``Owner`` is usually another actor's net guid, not a player: ownership must come
  from a caller-supplied ``resolve_player(guid, t_ms)`` (``normalize.py``'s
  per-life character map). Never guess from the agent name - Haven has Phoenix on
  both teams.
* A projectile and the effect it turns into do **not** share an ``Owner`` guid,
  but they do share ``Instigator`` (the thrower's character). Pairing therefore
  compares the resolved player when available, else the raw owner/instigator guid.
* An ability can own **two** effect actors: the object the throw physically lands
  on (Cypher's cage canister ``GameObject_Gumshoe_Q_CageTrap``, alive 7-50 ms) and
  the *area* it activates (``Zone_Gumshoe_Q_Cage``, alive 7.24 s). The catalog row
  names the area in ``effectClass`` (alternatives in ``effectClassAlternatives``)
  and the item contract keeps them apart: ``land`` is the landing object's spawn,
  ``effect`` is the area -- and ``None`` when no area followed, never the landing
  object's own ms (P7-1F2).

Emitted item (one per tracked actor, plan section 2 step 8)::

    {id, t, endT, actor, agent, ability, key, kind, route, path, bounces, land,
     effect, confidence, classPath}

integers are world units (cm) and milliseconds; ``path``/``bounces`` entries are
``[dt, x, y]`` with ``dt`` relative to the item's ``t``.
"""

from __future__ import annotations

import math
import re

__all__ = [
    "MOVEMENT_SCALE",
    "SCALE_MIN",
    "SCALE_MAX",
    "RDP_EPSILON",
    "MAX_PATH_POINTS",
    "BOUNCE_TURN_DEGREES",
    "BOUNCE_WINDOW_MS",
    "BOUNCE_SPEED_DROP",
    "BOUNCE_VZ_DOWN",
    "BOUNCE_VZ_UP",
    "PAIR_MAX_DELAY_MS",
    "PAIR_MAX_DISTANCE",
    "KEY_BY_SLOT",
    "BUILTIN_AGENT_BY_CODENAME",
    "BUILTIN_ABILITY_TABLE",
    "SLOT_COMPONENT_RE",
    "CATALOG_SCHEMA",
    "UtilityCollector",
    "catalog_rows",
    "round_utility",
    "player_counts",
    "collect_actors",
    "classify_class",
    "scale_ratio",
    "inside_minimap",
    "has_map_transform",
    "actor_points",
    "detect_bounces",
    "simplify_path",
    "pair_effects",
    "track_records",
]

# ---------------------------------------------------------------------------
# Tunables (plan section 2 steps 3-6)
# ---------------------------------------------------------------------------

MOVEMENT_SCALE = 100.0          # ReplicatedMovement.location -> world units
SCALE_MIN = 99.0                # accepted spawn/first-sample ratio band (P7-0: 99.6-100.1)
SCALE_MAX = 101.0
RDP_EPSILON = 20.0              # Ramer-Douglas-Peucker tolerance, world units
MAX_PATH_POINTS = 64            # hard cap per throw; bounce points are never dropped

BOUNCE_TURN_DEGREES = 60.0      # horizontal direction reversal
BOUNCE_WINDOW_MS = 120          # ... within this window
BOUNCE_SPEED_DROP = 0.15        # ... AND at least this much horizontal speed lost
BOUNCE_VZ_DOWN = -50.0          # vertical flip: below this ...
BOUNCE_VZ_UP = 50.0             # ... to above this
MIN_SPEED_FOR_DIRECTION = 50.0  # below this the horizontal direction is meaningless

PAIR_MAX_DELAY_MS = 500         # effect may spawn at most this long after the throw closes
PAIR_MAX_DISTANCE = 300.0       # ... and at most this far from the last recorded point

# Slot (the game's ability slot) -> in-game key. Never derived from the folder
# letter in the class path: Recon Bolt is ``_Q_`` but on E, Shock Bolt is ``_4_``
# but on Q (plan section 1).
KEY_BY_SLOT = {"Ability1": "Q", "Ability2": "E", "Grenade": "C", "Ultimate": "X"}

# A class path is an "ability" path when one of its path components is a slot.
SLOT_COMPONENT_RE = re.compile(r"^Ability_(?:Q|E|4|X|1|2|3)$")
_ABILITY_X_RE = re.compile(r"^Ability_X$")
_CHARACTER_RE = re.compile(r"/Game/Characters/([A-Za-z0-9_]+)/")

_PROJECTILE_PREFIXES = ("Projectile_",)
_EFFECT_PREFIXES = ("GameObject_", "Zone_", "Patch_")
_PAWN_PREFIXES = ("Pawn_",)

# ---------------------------------------------------------------------------
# Built-in classification table (tests only)
#
# P7-1A2 ships the real ``bot/replay/ability_catalog.json`` keyed by
# ``(codename, slot)`` with ``source``/``patch`` provenance per value and injects
# it through ``ability_table=``. This table only covers classes that appear in
# this workspace's single decodable replay (Haven 13.06) and exists so the module
# and its tests are self-contained. ``tokens`` are matched as case-sensitive
# substrings, longest token first, against the full class path.
# ---------------------------------------------------------------------------

BUILTIN_AGENT_BY_CODENAME = {
    "Deadeye": "Chamber",
    "Gumshoe": "Cypher",
    "Hunter": "Sova",
    "Phoenix": "Phoenix",
    "Smonk": "Clove",
    "Wraith": "Omen",
    "Wushu": "Jett",
}

BUILTIN_ABILITY_TABLE = [
    {"tokens": ["Projectile_Hunter_Q_RevealBolt"], "codename": "Hunter", "slot": "Ability2",
     "name": "Recon Bolt", "kind": "recon", "flight": "ballistic", "maxBounces": 2,
     "radius_m": None, "radiusSource": None,
     "effectClass": "GameObject_Hunter_Q_SonarBolt"},
    {"tokens": ["GameObject_Hunter_Q_SonarBolt", "GameObject_Hunter_Q_SonarPing"],
     "codename": "Hunter", "slot": "Ability2", "name": "Recon Bolt", "kind": "recon",
     "flight": "placed", "maxBounces": 0, "radius_m": 3.0, "radiusSource": "community",
     "effectClass": "GameObject_Hunter_Q_SonarBolt"},
    {"tokens": ["Projectile_Hunter_4_ExplosiveBolt"], "codename": "Hunter", "slot": "Ability1",
     "name": "Shock Bolt", "kind": "blast", "flight": "ballistic", "maxBounces": 2,
     "radius_m": None, "radiusSource": None,
     "effectClass": "GameObject_Hunter_4_ExplosiveBolt_Explosion"},
    {"tokens": ["GameObject_Hunter_4_ExplosiveBolt_Explosion"], "codename": "Hunter",
     "slot": "Ability1", "name": "Shock Bolt", "kind": "blast", "flight": "placed",
     "maxBounces": 0, "radius_m": 2.6, "radiusSource": "community",
     "effectClass": "GameObject_Hunter_4_ExplosiveBolt_Explosion"},
    {"tokens": ["Projectile_Gumshoe_Q_CageTrap"], "codename": "Gumshoe", "slot": "Grenade",
     "name": "Cyber Cage", "kind": "cage", "flight": "ballistic", "maxBounces": 0,
     "radius_m": 3.72, "radiusSource": "community",
     "effectClass": "Zone_Gumshoe_Q_Cage",
     "effectClassAlternatives": ["GameObject_Gumshoe_Q_CageTrap"]},
    {"tokens": ["GameObject_Gumshoe_Q_CageTrap", "Zone_Gumshoe_Q_Cage"], "codename": "Gumshoe",
     "slot": "Grenade", "name": "Cyber Cage", "kind": "cage", "flight": "placed",
     "maxBounces": 0, "radius_m": 3.72, "radiusSource": "community",
     "effectClass": "Zone_Gumshoe_Q_Cage",
     "effectClassAlternatives": ["GameObject_Gumshoe_Q_CageTrap"]},
    {"tokens": ["Projectile_Wushu_4_Smoke"], "codename": "Wushu", "slot": "Grenade",
     "name": "Cloudburst", "kind": "smoke", "flight": "ballistic", "maxBounces": 0,
     "radius_m": 3.35, "radiusSource": "community",
     "effectClass": "GameObject_Wushu_4_SmokeZone"},
    {"tokens": ["GameObject_Wushu_4_SmokeZone"], "codename": "Wushu", "slot": "Grenade",
     "name": "Cloudburst", "kind": "smoke", "flight": "placed", "maxBounces": 0,
     "radius_m": 3.35, "radiusSource": "community",
     "effectClass": "GameObject_Wushu_4_SmokeZone"},
    {"tokens": ["Projectile_Phoenix_Q_FlameWall_ThroughWall"], "codename": "Phoenix",
     "slot": "Grenade", "name": "Blaze", "kind": "wall", "flight": "steered",
     "maxBounces": 0, "radius_m": None, "radiusSource": None},
    {"tokens": ["Projectile_Phoenix_E_FlareCurve_Synced"], "codename": "Phoenix",
     "slot": "Ability1", "name": "Curveball", "kind": "flash", "flight": "guided",
     "maxBounces": 0, "radius_m": None, "radiusSource": None},
    {"tokens": ["Projectile_Phoenix_4_Molotov_Production"], "codename": "Phoenix",
     "slot": "Ability2", "name": "Hot Hands", "kind": "molly", "flight": "ballistic",
     "maxBounces": 1, "radius_m": None, "radiusSource": None,
     "effectClass": "Patch_Phoenix_MolotovFire"},
    {"tokens": ["Patch_Phoenix_MolotovFire"], "codename": "Phoenix", "slot": "Ability2",
     "name": "Hot Hands", "kind": "molly", "flight": "placed", "maxBounces": 0,
     "radius_m": None, "radiusSource": None,
     "effectClass": "Patch_Phoenix_MolotovFire"},
    {"tokens": ["Projectile_Wraith_Q_NearsightMissile"], "codename": "Wraith",
     "slot": "Ability1", "name": "Paranoia", "kind": "nearsight", "flight": "straight",
     "maxBounces": 0, "radius_m": None, "radiusSource": None},
    {"tokens": ["Projectile_Wraith_4_Smoke"], "codename": "Wraith", "slot": "Ability2",
     "name": "Dark Cover", "kind": "smoke", "flight": "ballistic", "maxBounces": 0,
     "radius_m": 4.1, "radiusSource": "community",
     "effectClass": "Zone_Wraith_4_Smoke"},
    {"tokens": ["Zone_Wraith_4_Smoke"], "codename": "Wraith", "slot": "Ability2",
     "name": "Dark Cover", "kind": "smoke", "flight": "placed", "maxBounces": 0,
     "radius_m": 4.1, "radiusSource": "community",
     "effectClass": "Zone_Wraith_4_Smoke"},
    {"tokens": ["Projectile_Smonk_DecayNade"], "codename": "Smonk", "slot": "Ability1",
     "name": "Meddle", "kind": "decay", "flight": "ballistic", "maxBounces": 1,
     "radius_m": None, "radiusSource": None,
     "effectClass": "GameObject_Smonk_Q_DecayExplosion"},
    {"tokens": ["GameObject_Smonk_Q_DecayExplosion"], "codename": "Smonk", "slot": "Ability1",
     "name": "Meddle", "kind": "decay", "flight": "placed", "maxBounces": 0,
     "radius_m": None, "radiusSource": None,
     "effectClass": "GameObject_Smonk_Q_DecayExplosion"},
    {"tokens": ["GameObject_Smonk_NewSmoke"], "codename": "Smonk", "slot": "Ability2",
     "name": "Ruse", "kind": "smoke", "flight": "placed", "maxBounces": 0,
     "radius_m": None, "radiusSource": None,
     "effectClass": "GameObject_Smonk_NewSmoke"},
]

_ABILITY_TABLE_ORDERED = None


def _ordered_table(table):
    """Expand a table into ``(token, row)`` pairs, longest token first."""
    pairs = []
    for row in table or []:
        for token in row.get("tokens") or ():
            pairs.append((token, row))
    pairs.sort(key=lambda pair: (-len(pair[0]), pair[0]))
    return pairs


def _slot_of(class_path):
    for component in (class_path or "").split("/"):
        if SLOT_COMPONENT_RE.match(component):
            return component
    return None


def _codename_of(class_path):
    match = _CHARACTER_RE.search(class_path or "")
    return match.group(1) if match else None


def _leaf(class_path):
    return (class_path or "").rsplit("/", 1)[-1]


def _role_of(class_path):
    """projectile / effect / state, from the class leaf name.

    ``Projectile_*`` is a thrown body, ``GameObject_*``/``Zone_*``/``Patch_*`` is
    something it turns into. Everything else is a **state** actor: caster-side
    ``Ability_*``/``Gun_*`` instances (``Ability_Hunter_Q_RevealBolt_Signature``,
    ``Ability_Wraith_4_Smoke``, ``Gun_Deadeye_Q_Pistol``) are not thrown or placed
    utility and carry no owner evidence in the export -- measured on Haven 13.06:
    all 49 of them come out ``confidence: "unowned"`` -- so they stay out unless the
    caller asks for ``include_states=True``.
    """
    leaf = _leaf(class_path)
    for prefix in _PROJECTILE_PREFIXES:
        if leaf.startswith(prefix):
            return "projectile"
    for prefix in _EFFECT_PREFIXES:
        if leaf.startswith(prefix):
            return "effect"
    return "state"


def _effect_classes(row):
    """The effect classes a row accepts when pairing: ``effectClass`` + alternatives.

    ``effectClass`` is the *area* actor (the lifetime the UI and the AC-1.8
    control read). ``effectClassAlternatives`` (or a list-valued ``effectClass``)
    lists further classes a throw may legitimately pair with -- for Cyber Cage
    that is the canister ``GameObject_Gumshoe_Q_CageTrap`` the throw physically
    lands on, which is *not* the area. Returns ``None`` when the row names no
    class at all, and then the pairing falls back to codename-only matching.
    """
    if not row:
        return None
    primary = row.get("effectClass")
    if isinstance(primary, (list, tuple)):
        candidates = list(primary)
    elif primary:
        candidates = [primary]
    else:
        candidates = []
    extras = row.get("effectClassAlternatives") or row.get("effectClasses") or ()
    if isinstance(extras, str):
        extras = [extras]
    candidates.extend(extras)
    ordered = []
    for value in candidates:
        if value and value not in ordered:
            ordered.append(value)
    return ordered or None


def _area_classes(row):
    """The *area* classes of a row: ``effectClass`` only (never the alternatives)."""
    if not row:
        return None
    primary = row.get("effectClass")
    if isinstance(primary, (list, tuple)):
        primary = primary[0] if primary else None
    return [primary] if primary else None


def classify_class(class_path, table=None, agent_map=None):
    """Classify an ability class path.

    Returns a dict with ``codename``, ``agent``, ``ability``, ``slot``, ``key``,
    ``kind``, ``flight``, ``maxBounces``, ``radius``, ``radiusSource``, ``ult``,
    ``effectClass`` (the area actor class), ``effectClasses`` (every class the
    ability may pair with) and ``role``; unlisted classes come back as
    ``kind == "unknown"`` with ``ability``/``key`` ``None`` (plan section 2 step 2).
    A ``states`` row (a caster-side state actor such as Reyna's Devour heal pool)
    comes back as ``kind: "state"`` with ``ability``/``key`` ``None``,
    ``flight: "state"`` and ``ult: False``: it is evidence, not thrown utility.
    Returns ``None`` when the class is not an ability class at all (no slot
    component in the path).
    """
    slot_component = _slot_of(class_path)
    if slot_component is None:
        return None
    agent_map = BUILTIN_AGENT_BY_CODENAME if agent_map is None else agent_map
    pairs = _ABILITY_TABLE_ORDERED
    if pairs is None or table is not None:
        pairs = _ordered_table(BUILTIN_ABILITY_TABLE if table is None else table)
        if table is None:
            globals()["_ABILITY_TABLE_ORDERED"] = pairs
    codename = _codename_of(class_path)
    row = None
    for token, candidate in pairs:
        if token in class_path:
            row = candidate
            break
    ult = bool(slot_component == "Ability_X")
    radius = None
    radius_source = None
    if row is not None:
        codename = row.get("codename") or codename
        ult = ult or row.get("slot") == "Ultimate" or bool(row.get("ult"))
        if row.get("radius_m") is not None:
            radius = int(round(float(row["radius_m"]) * MOVEMENT_SCALE))
            radius_source = row.get("radiusSource") or row.get("radius_source")
    area_classes = _area_classes(row)
    state = (row or {}).get("kind") == "state"
    return {
        "codename": codename,
        "agent": agent_map.get(codename) if codename else None,
        "ability": None if state else (row or {}).get("name"),
        "slot": (row or {}).get("slot"),
        "key": None if state else KEY_BY_SLOT.get((row or {}).get("slot")),
        "kind": (row or {}).get("kind") or "unknown",
        "flight": "state" if state else (row or {}).get("flight") or "ballistic",
        "maxBounces": (row or {}).get("maxBounces"),
        "radius": radius,
        "radiusSource": radius_source,
        "ult": False if state else ult,
        "auxiliary": bool((row or {}).get("auxiliary")),
        "verified": (row or {}).get("verified"),
        "effectClass": area_classes[0] if area_classes else None,
        "effectClasses": _effect_classes(row),
        "role": _role_of(class_path),
    }


# ---------------------------------------------------------------------------
# Collection (plan section 2 step 1)
# ---------------------------------------------------------------------------

def _payload_field(payload, name):
    """Case-insensitive payload lookup (the export uses PascalCase, reports lower)."""
    if not isinstance(payload, dict):
        return None
    if name in payload:
        return payload[name]
    lowered = name.lower()
    for key, value in payload.items():
        if key.lower() == lowered:
            return value
    return None


def _vector(value, axis, default=0.0):
    if not isinstance(value, dict):
        return default
    got = value.get(axis)
    return default if got is None else float(got)


def collect_actors(records):
    """Fold decoded NDJSON records into ``{actor_net_guid: actor}``.

    ``actor`` keys: ``guid``, ``classPath``, ``spawnT``, ``spawnX/Y/Z``,
    ``spawnVx/Vy/Vz``, ``instigator``, ``owner``, ``closeT``, ``closeReason``,
    ``samples`` (time-ordered ``{"t", "rawX/Y/Z" (export units), "vx/vy/vz"}``).
    """
    actors = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        kind = record.get("type")
        guid = record.get("actor_net_guid")
        if guid is None:
            continue
        actor = actors.get(guid)
        if kind == "actor_spawned":
            if actor is None:
                class_path = (record.get("replication_class_path")
                              or record.get("archetype_path") or "")
                if class_path.startswith("Default__"):
                    class_path = class_path[len("Default__"):]
                if class_path.endswith("_C"):
                    class_path = class_path[:-2]
                location = record.get("location")
                has_spawn = isinstance(location, dict)
                actor = {
                    "guid": guid,
                    "classPath": class_path or None,
                    "spawnT": record.get("time_ms"),
                    "hasSpawn": has_spawn,
                    "spawnX": _vector(location, "x", 0.0) if has_spawn else None,
                    "spawnY": _vector(location, "y", 0.0) if has_spawn else None,
                    "spawnZ": _vector(location, "z", 0.0) if has_spawn else None,
                    "spawnVx": _vector(record.get("velocity"), "x", 0.0),
                    "spawnVy": _vector(record.get("velocity"), "y", 0.0),
                    "spawnVz": _vector(record.get("velocity"), "z", 0.0),
                    "instigator": None,
                    "owner": None,
                    "closeT": None,
                    "closeReason": None,
                    "samples": [],
                }
                actors[guid] = actor
        elif kind == "actor_closed":
            if actor is not None and actor["closeT"] is None:
                actor["closeT"] = record.get("time_ms")
                actor["closeReason"] = record.get("reason")
        elif kind == "export_group_received":
            payload = record.get("payload")
            if not isinstance(payload, dict) or not payload:
                continue
            if actor is None:
                class_path = record.get("class_path") or record.get("export_group_path") or ""
                class_path = class_path.split(".", 1)[0]
                actor = {
                    "guid": guid, "classPath": class_path or None,
                    "spawnT": record.get("time_ms"), "hasSpawn": False,
                    "spawnX": None, "spawnY": None, "spawnZ": None,
                    "spawnVx": 0.0, "spawnVy": 0.0, "spawnVz": 0.0,
                    "instigator": None, "owner": None,
                    "closeT": None, "closeReason": None, "samples": [],
                }
                actors[guid] = actor
            if actor["instigator"] is None:
                actor["instigator"] = _payload_field(payload, "Instigator")
            if actor["owner"] is None:
                actor["owner"] = _payload_field(payload, "Owner")
            movement = _payload_field(payload, "ReplicatedMovement")
            if isinstance(movement, dict):
                location = movement.get("location") or {}
                velocity = movement.get("linear_velocity") or {}
                sample = {
                    "t": record.get("time_ms"),
                    "rawX": _vector(location, "x"),
                    "rawY": _vector(location, "y"),
                    "rawZ": _vector(location, "z"),
                    "vx": _vector(velocity, "x"),
                    "vy": _vector(velocity, "y"),
                    "vz": _vector(velocity, "z"),
                }
                actor["samples"].append(sample)
    for actor in actors.values():
        actor["samples"].sort(key=lambda sample: (sample["t"], sample["rawX"], sample["rawY"]))
        if actor["classPath"]:
            actor["classPath"] = actor["classPath"].split(".", 1)[0]
    return actors


# ---------------------------------------------------------------------------
# Streaming collection and catalog loading (P7-1A2)
# ---------------------------------------------------------------------------

CATALOG_SCHEMA = "valoplant_ability_catalog/v1"

# Every decoded record field that can carry a class path (P7-1A2's hook feeds
# records straight out of the decoder, whose ability records name the class in
# ``replication_class_path`` / ``class_path``).
_COLLECT_PATH_FIELDS = ("replication_class_path", "archetype_path", "class_path",
                        "export_group_path", "object_path", "actor_path")

# A character-family actor (pawn / ``*_PC`` controller / player state). The chain
# that resolves ownership ends on one of these, so the collector keeps exactly one
# owner row per such actor (see ``_owner_reference``).
_CHARACTER_MARKER = "/Characters/"


def _owner_reference(record):
    """The guid of the character actor ``record`` carries an owner for, or ``None``.

    An ability actor's ``Instigator`` is a ``<Codename>_PC`` PlayerController guid,
    never the pawn guid normalize's per-life player map knows, and the controller
    row is the only place its ``Owner`` (the pawn) appears. Measured in the Haven
    13.06 P7-1B export: ``Projectile_Wraith_4_Smoke`` (guid 3980) has
    ``Instigator`` = 722 = ``/Game/Characters/Wraith/Wraith_PC``, whose ``Owner`` =
    76 = the character normalize maps to a player id. Without 722's row the chain
    dies and 87 of 434 Haven items come out ``unowned`` (AC-1.3). Keeping one row
    per character actor costs ~1.8k records, not the export's 231k
    ``/Characters/`` lines.
    """
    if not any(_CHARACTER_MARKER in (record.get(field) or "")
               for field in _COLLECT_PATH_FIELDS):
        return None
    payload = record.get("payload")
    if not isinstance(payload, dict) or not payload:
        return None
    if _payload_field(payload, "Owner") is None \
            and _payload_field(payload, "Instigator") is None:
        return None
    return record.get("actor_net_guid")


class UtilityCollector(object):
    """Keep only ability-actor records while the caller streams events.

    ``normalize.scan_events`` already reads ``events.ndjson[.gz]`` once; feeding
    every record through ``feed()`` leaves the tracker with exactly the records it
    needs and no second pass over the 362,944-line file. A record is kept when one
    of its class-path fields carries an ``/Ability_<slot>/`` component, and once an
    actor has proved itself an ability actor its ``actor_closed`` row is kept too
    (that row carries no class path of its own).
    """

    def __init__(self):
        self.records = []
        self.actor_guids = set()
        self.owner_refs = set()   # character actors whose owner row is kept
        self.kept = 0

    def feed(self, record):
        """Keep ``record`` if it belongs to an ability actor; return whether it did."""
        if not isinstance(record, dict):
            return False
        kind = record.get("type")
        guid = record.get("actor_net_guid")
        if kind == "actor_closed":
            if guid is None or guid not in self.actor_guids:
                return False
        elif kind in ("actor_spawned", "export_group_received"):
            if record.get("is_deleted") is True:
                return False
            if not any(_slot_of(record.get(field)) for field in _COLLECT_PATH_FIELDS):
                # Not an ability actor: keep one owner row per character actor so
                # the owner chain can hop ``*_PC`` -> pawn (P7-1A2, AC-1.3).
                reference = _owner_reference(record)
                if reference is None or reference in self.owner_refs:
                    return False
                self.owner_refs.add(reference)
            elif guid is not None:
                self.actor_guids.add(guid)
        else:
            return False
        self.records.append(record)
        self.kept += 1
        return True

    def __len__(self):
        return len(self.records)


def catalog_rows(catalog):
    """Flatten an ``ability_catalog.json`` document into tracker table rows.

    The catalog is keyed by ``(codename, slot)`` (plan section 2 step 2) and the
    tracker matches on ``tokens``, so one row per entry is enough. Per-value
    provenance (``source``, ``patch``, ``verified``, ``duration_s``) is carried
    through untouched; only ``radiusSource`` is derived, because
    ``classify_class`` reads the radius provenance under that name. Rows of the
    top-level ``auxiliary`` list (pulse/warning sub-objects that the tracker drops)
    are flattened in with ``auxiliary`` forced true, and the ``states`` list
    (caster-side state actors) is flattened in as ``kind: "state"`` rows.
    """
    if not isinstance(catalog, dict):
        return []
    entries = catalog.get("abilities")
    if isinstance(entries, dict):
        entries = list(entries.values())
    rows = []
    for entry in (list(entries or []) + list(catalog.get("auxiliary") or [])
                  + list(catalog.get("states") or [])):
        if not isinstance(entry, dict):
            continue
        row = dict(entry)
        row["radiusSource"] = entry.get("source")
        rows.append(row)
    return rows


def round_utility(items, start_ms, end_ms):
    """The ``utility`` list of one round file: items inside the round, rebased to it.

    Round files carry round-relative times (``round_events[].t`` is
    ``time_ms - startMs``), so every absolute stamp of an item is shifted by
    ``-start_ms``: ``t``, ``endT``, ``land.t``, ``effect.t0``/``effect.t1``. The
    ``path``/``bounces`` entries stay as they are -- their ``dt`` is already
    relative to the item's ``t``.
    """
    rebased = []
    for item in items:
        if not (start_ms <= item.get("t", -1) <= end_ms):
            continue
        clone = dict(item)
        clone["t"] = item["t"] - start_ms
        if item.get("endT") is not None:
            clone["endT"] = item["endT"] - start_ms
        for key in ("land", "effect"):
            block = item.get(key)
            if isinstance(block, dict):
                block = dict(block)
                for stamp in ("t", "t0", "t1"):
                    if block.get(stamp) is not None:
                        block[stamp] = block[stamp] - start_ms
                clone[key] = block
        rebased.append(clone)
    return rebased


def player_counts(items, players):
    """Match-level utility tally per player: ``{player_id: count-row}``.

    ``players`` is the match's player list (dicts with at least ``id`` and
    ``agent``); every one of them gets a row, zeros included, so the viewer and
    the analysis page can list all ten players without re-deriving them from the
    items. Rows without an ``id`` are skipped (an id-less row must not become the
    bucket that swallows unowned items). Only items owned by a player count
    (``actor`` matching an ``id``); ``actor: null`` and items without an actor are
    ignored. Rows read::

        {"agent": "Sova", "total": 7, "byKind": {"recon": 1, "blast": 2},
         "abilities": {"Recon Bolt": 1, "Shock Bolt": 2}}

    A null ``kind``/``ability`` still counts towards ``total`` but is not keyed
    (unlisted classes carry ``kind: "unknown"``; this guards the absent field).
    Items with ``kind: "state"`` are skipped entirely -- a caster-side state actor
    (Reyna's Devour heal pool) is not thrown utility, so it contributes nothing to
    ``total``, ``byKind`` or ``abilities`` even though it stays in ``utility[]``
    as evidence.
    """
    counts = {}
    for player in players or ():
        player_id = player.get("id")
        if player_id is None:
            # an id-less row would otherwise collide with the unowned bucket
            continue
        counts[player_id] = {
            "agent": player.get("agent") or None,
            "total": 0,
            "byKind": {},
            "abilities": {},
        }
    for item in items or ():
        actor = item.get("actor")
        if actor is None:
            continue
        if item.get("kind") == "state":
            continue
        entry = counts.get(actor)
        if entry is None:
            continue
        entry["total"] += 1
        kind = item.get("kind")
        if kind is not None:
            entry["byKind"][kind] = entry["byKind"].get(kind, 0) + 1
        ability = item.get("ability")
        if ability is not None:
            entry["abilities"][ability] = entry["abilities"].get(ability, 0) + 1
    return counts


def scale_ratio(actor):
    """``spawn / first raw sample`` (x, else y) for an actor, or ``None`` when unknowable.

    Pinned to 99..101 by the P7-0 gate on every projectile class; anything else
    means the scale convention changed (risk R3) and the path is dropped while the
    landing stays.
    """
    samples = actor.get("samples") or []
    if not samples or not actor.get("hasSpawn"):
        return None
    first = samples[0]
    for spawn, raw in ((actor.get("spawnX"), first.get("rawX")),
                       (actor.get("spawnY"), first.get("rawY"))):
        if spawn and raw:
            return abs(spawn) / abs(raw)
    return None


def actor_points(actor):
    """World-space point list for an actor: spawn point + scaled samples.

    Each point is ``{"t", "x", "y", "z", "vx", "vy", "vz"}`` in world units and
    true ms. Sorted by time, consecutive duplicates (same time *and* same integer
    position) collapsed, so the spawn transform and the first sample that repeats
    it become one point. An actor with no spawn transform contributes only its
    samples: a missing spawn is never turned into a ``(0, 0)`` point.
    """
    points = []
    if actor.get("spawnT") is not None and actor.get("hasSpawn"):
        points.append({
            "t": actor["spawnT"],
            "x": actor.get("spawnX") or 0.0,
            "y": actor.get("spawnY") or 0.0,
            "z": actor.get("spawnZ") or 0.0,
            "vx": actor.get("spawnVx") or 0.0,
            "vy": actor.get("spawnVy") or 0.0,
            "vz": actor.get("spawnVz") or 0.0,
        })
    for sample in actor.get("samples") or []:
        points.append({
            "t": sample["t"],
            "x": sample["rawX"] * MOVEMENT_SCALE,
            "y": sample["rawY"] * MOVEMENT_SCALE,
            "z": sample["rawZ"] * MOVEMENT_SCALE,
            "vx": sample["vx"],
            "vy": sample["vy"],
            "vz": sample["vz"],
        })
    points.sort(key=lambda point: (point["t"], point["x"], point["y"]))
    deduped = []
    for point in points:
        if deduped:
            last = deduped[-1]
            if (point["t"] == last["t"]
                    and int(round(point["x"])) == int(round(last["x"]))
                    and int(round(point["y"])) == int(round(last["y"]))):
                # keep the later sample's velocity (it is the replicated one)
                last["vx"], last["vy"], last["vz"] = point["vx"], point["vy"], point["vz"]
                continue
        deduped.append(point)
    return deduped


# ---------------------------------------------------------------------------
# Bounces (plan section 2 step 5)
# ---------------------------------------------------------------------------

def detect_bounces(points, flight="ballistic"):
    """Indices into ``points`` where the projectile bounced.

    A bounce is **either**

    * a horizontal direction turn > 60 deg within <= 120 ms **and** >= 15% of the
      horizontal speed lost, **or**
    * a vertical velocity flip from < -50 to > +50.

    A single-axis sign flip is never a bounce (that is what made the P7-0 showcase
    label Phoenix's steered Blaze a bounce). Steering/guided flight is skipped
    entirely. Returns the index of the point where the new direction starts.
    """
    if flight in ("steered", "guided"):
        return []
    hits = []
    for index in range(1, len(points)):
        before = points[index - 1]
        after = points[index]
        dt = (after["t"] or 0) - (before["t"] or 0)
        horizontal_before = math.hypot(before["vx"], before["vy"])
        horizontal_after = math.hypot(after["vx"], after["vy"])
        turned = False
        if (dt is not None and 0 <= dt <= BOUNCE_WINDOW_MS
                and horizontal_before >= MIN_SPEED_FOR_DIRECTION
                and horizontal_after >= MIN_SPEED_FOR_DIRECTION):
            cosine = ((before["vx"] * after["vx"] + before["vy"] * after["vy"])
                      / (horizontal_before * horizontal_after))
            angle = math.degrees(math.acos(max(-1.0, min(1.0, cosine))))
            turned = (angle > BOUNCE_TURN_DEGREES
                      and horizontal_after <= horizontal_before * (1.0 - BOUNCE_SPEED_DROP))
        flipped = before["vz"] < BOUNCE_VZ_DOWN and after["vz"] > BOUNCE_VZ_UP
        if turned or flipped:
            hits.append(index)
    return hits


# ---------------------------------------------------------------------------
# Path simplification (plan section 2 step 4)
# ---------------------------------------------------------------------------

def _perpendicular(point, start, end):
    x0, y0 = start["x"], start["y"]
    x1, y1 = end["x"], end["y"]
    x, y = point["x"], point["y"]
    dx, dy = x1 - x0, y1 - y0
    if dx == 0.0 and dy == 0.0:
        return math.hypot(x - x0, y - y0)
    ratio = ((x - x0) * dx + (y - y0) * dy) / (dx * dx + dy * dy)
    ratio = max(0.0, min(1.0, ratio))
    return math.hypot(x - (x0 + ratio * dx), y - (y0 + ratio * dy))


def simplify_path(points, epsilon=RDP_EPSILON, max_points=MAX_PATH_POINTS, keep=()):
    """Ramer-Douglas-Peucker indices, always keeping ``keep`` (bounce) points.

    Points that survive RDP are ranked by their deviation, so when the result is
    longer than ``max_points`` the least deviant non-forced points are dropped
    first. Bounce points and the two endpoints are never dropped.
    """
    count = len(points)
    if count <= 2:
        return list(range(count))
    forced = {0, count - 1}
    for index in keep or ():
        if 0 <= index < count:
            forced.add(index)
    importance = {}

    def recurse(first, last):
        if last - first < 2:
            return
        best_index = None
        best_distance = 0.0
        for index in range(first + 1, last):
            distance = _perpendicular(points[index], points[first], points[last])
            if distance > best_distance:
                best_distance, best_index = distance, index
        if best_index is None or best_distance <= epsilon:
            return
        importance[best_index] = best_distance
        recurse(first, best_index)
        recurse(best_index, last)

    recurse(0, count - 1)
    selected = set(importance) | forced
    if len(selected) > max_points:
        optional = sorted((index for index in selected if index not in forced),
                          key=lambda index: (-importance.get(index, 0.0), index))
        room = max(0, max_points - len(forced))
        selected = set(forced) | set(optional[:room])
    return sorted(selected)


# ---------------------------------------------------------------------------
# Effect pairing (plan section 2 steps 5-7)
# ---------------------------------------------------------------------------

def _owner_guid(actor, resolve_player):
    """``(identity, guid)`` for pairing: resolved player first, else raw guid.

    Returns ``(None, None)`` when the actor carries no owner evidence at all.
    """
    for guid in (actor.get("instigator"), actor.get("owner")):
        if guid:
            if resolve_player is not None:
                player = resolve_player(guid, actor.get("spawnT"))
                if player:
                    return ("player", player), guid
            return ("guid", guid), guid
    return (None, None), None


def _last_point_xy(actor, points):
    if points:
        return points[-1]["x"], points[-1]["y"]
    if actor.get("hasSpawn"):
        return actor.get("spawnX"), actor.get("spawnY")
    return None, None


def _landing_window_delta(effect_t, projectile, points):
    """Distance (ms) from ``effect_t`` to the projectile's landing window, or ``None``.

    The window runs from the projectile's **last recorded sample** to its **close
    time**: the throw lands somewhere inside it and the effect it turns into is
    created somewhere in there. Measured on the Haven 13.06 export and the P7-1B
    re-decode (2026-10-17): Sova's SonarBolt and Jett's SmokeZone appear exactly at
    the last sample, Cyber Cage and Meddle's DecayExplosion exactly at the close,
    and Omen's Dark Cover zone mid-window -- the orb sinks for ~0.5 s after its last
    sample, so 18 of 35 throws place the zone 501-508 ms after the last sample yet
    still before the close. A rule of the form "at most 500 ms after close" reads
    those as failures (and Cloudburst's projectile, destroyed 1.5-2.7 s after its
    smoke appeared, as a 2691 ms failure) even though the geometry is exact to
    0.0-0.6 units. Zero means "inside the window".
    """
    window = [point["t"] for point in points if point.get("t") is not None]
    if projectile.get("closeT") is not None:
        window.append(projectile["closeT"])
    if not window:
        return None
    low, high = min(window), max(window)
    if effect_t < low:
        return low - effect_t
    if effect_t > high:
        return effect_t - high
    return 0


def _classification_of(actor):
    """The prepared classification of a tracker actor, or ``None``."""
    return actor.get("classification") if isinstance(actor, dict) else None


def _actor_codename(actor):
    """Ability codename of a tracker actor: the classification first, the path second."""
    classification = _classification_of(actor) or {}
    return classification.get("codename") or _codename_of(actor.get("classPath"))


def _allowed_effect_classes(actor):
    """Effect classes ``actor``'s row accepts when pairing, or ``None`` (no gate)."""
    classification = _classification_of(actor) or {}
    allowed = classification.get("effectClasses")
    if allowed:
        return list(allowed)
    primary = classification.get("effectClass")
    if primary:
        return [primary] if isinstance(primary, str) else list(primary)
    return None


def _pairing_blocked_by_class(projectile, effect):
    """True when the effect's class is not one the projectile's row accepts."""
    effect_codename = _actor_codename(effect)
    projectile_codename = _actor_codename(projectile)
    if projectile_codename and effect_codename and projectile_codename != effect_codename:
        return True
    allowed = _allowed_effect_classes(projectile)
    if allowed is not None and _leaf(effect.get("classPath")) not in allowed:
        return True
    return False


def pair_effects(projectiles, effects, resolve_player=None):
    """Pair each effect actor with the projectile it came from.

    A pair needs

    * owner evidence **on the projectile**: the same resolved player when both
      sides resolve, else the same raw ``Instigator``/``Owner`` guid. A
      projectile that carries **no** owner evidence at all never pairs -- an
      effect's owner is never inherited across agents (the u6106 regression:
      a Phoenix molly with no ``Owner``/``Instigator`` inherited Omen's smoke
      owner P3, P7-1F2);
    * an effect class the projectile's row accepts: the same codename, and, when
      the row names classes (``effectClass`` / ``effectClassAlternatives``), an
      effect class among them. An effect with no owner evidence of its own still
      pairs on space and time alone (``GameObject_Hunter_4_ExplosiveBolt_
      Explosion`` ships neither ``Owner`` nor ``Instigator``) -- but only with a
      projectile that does carry owner evidence;
    * the effect spawning at most ``PAIR_MAX_DISTANCE`` world units from the
      projectile's last recorded point, and
    * the effect spawning inside (or within ``PAIR_MAX_DELAY_MS`` of) the
      projectile's landing window, last sample .. close time
      (``_landing_window_delta``).

    Returns ``(pairs, best)``: ``pairs`` is the list of ``(effect_guid,
    projectile_guid)`` in effect order, ``best`` maps a projectile guid to the
    effect guid that landed it (nearest, then earliest).
    """
    pairs = []
    best = {}
    for effect in effects:
        effect_identity, _ = _owner_guid(effect, resolve_player)
        effect_points = effect.get("points") or []
        effect_x, effect_y = _last_point_xy(effect, effect_points)
        effect_t = effect.get("spawnT")
        if effect_x is None or effect_y is None or effect_t is None:
            continue
        best_choice = None
        for projectile in projectiles:
            projectile_identity, _ = _owner_guid(projectile, resolve_player)
            if projectile_identity[0] is None:
                # the projectile carries no owner evidence: it can never pair
                continue
            if effect_identity[0] is not None and projectile_identity != effect_identity:
                continue
            if _pairing_blocked_by_class(projectile, effect):
                continue
            projectile_points = projectile.get("points") or []
            px, py = _last_point_xy(projectile, projectile_points)
            if px is None or py is None:
                continue
            distance = math.hypot(effect_x - px, effect_y - py)
            if distance > PAIR_MAX_DISTANCE:
                continue
            delta = _landing_window_delta(effect_t, projectile, projectile_points)
            if delta is None or delta > PAIR_MAX_DELAY_MS:
                continue
            score = (round(distance, 3), delta, projectile.get("guid"))
            if best_choice is None or score < best_choice[0]:
                best_choice = (score, projectile.get("guid"))
        if best_choice is not None:
            pairs.append((effect.get("guid"), best_choice[1]))
            previous = best.get(best_choice[1])
            if previous is None or best_choice[0] < previous[0]:
                best[best_choice[1]] = (best_choice[0], effect.get("guid"))
    return pairs, {guid: value[1] for guid, value in best.items()}


# ---------------------------------------------------------------------------
# Tracking (plan section 2 steps 1-8)
# ---------------------------------------------------------------------------

def _area_class_set(actor):
    """The row's **area** classes (``effectClass`` only), or ``None`` when unnamed."""
    classification = _classification_of(actor) or {}
    primary = classification.get("effectClass")
    if not primary:
        return None
    return [primary] if isinstance(primary, str) else list(primary)


def _owner_reference_set(actor, identity_guid):
    """Raw owner-evidence guids of an actor: its own guid plus Instigator/Owner."""
    values = {identity_guid, actor.get("instigator"), actor.get("owner")}
    return {value for value in values if value is not None}


def _same_owner(first, second, resolve_player):
    """True when two actors carry compatible owner evidence.

    Resolved players decide when both sides resolve; otherwise the raw
    ``Instigator``/``Owner`` reference sets are intersected, which also covers the
    one-hop case where one actor's ``Owner`` is the other actor (an area spawned
    by the object a throw landed on). Two actors that are *both* ownerless do not
    match -- the anchor of an area lookup always carries evidence, so this is not
    a wildcard.
    """
    first_identity, first_guid = _owner_guid(first, resolve_player)
    second_identity, second_guid = _owner_guid(second, resolve_player)
    if first_identity[0] is not None and second_identity[0] is not None:
        return first_identity == second_identity
    return bool(_owner_reference_set(first, first_guid) & _owner_reference_set(second, second_guid))


def _attach_areas(projectiles, effects, best_effect, actors, resolve_player):
    """The area actor of every paired throw: ``(areas, consumed_guids)``.

    ``best_effect`` maps a projectile to the effect it landed with. When the row
    names an area class (``effectClass``) the throw's ``effect`` block comes from
    that class:

    * the anchor itself when it *is* the area (Omen's smoke, Jett's smoke, Sova's
      bolt, the Shock Bolt explosion);
    * otherwise the area actor that **follows** the anchor -- Cyber Cage's
      canister lands and ``Zone_Gumshoe_Q_Cage`` appears when the cage is
      activated: same owner, at most ``PAIR_MAX_DISTANCE`` units from the anchor,
      spawning at the anchor or later (any delay: activation can come seconds
      later, up to the end of the round). The area is then consumed, so it is not
      drawn a second time;
    * when no area followed, the throw gets ``effect: null`` -- never the
      canister's own 7-50 ms (P7-1F2, AC-1.8 measured per throw).

    A row that names no area class keeps the previous behaviour: the paired effect
    is the throw's effect.
    """
    areas = {}
    consumed = set()
    reserved = set(best_effect.values())
    for projectile in sorted(projectiles, key=lambda actor: actor.get("guid") or 0):
        guid = projectile.get("guid")
        anchor_guid = best_effect.get(guid)
        if anchor_guid is None:
            continue
        anchor = actors.get(anchor_guid)
        if anchor is None:
            continue
        area_classes = _area_class_set(projectile)
        if area_classes is None or _leaf(anchor.get("classPath")) in area_classes:
            areas[guid] = anchor
            continue
        anchor_t = anchor.get("spawnT")
        ax, ay = _last_point_xy(anchor, anchor.get("points") or [])
        best_candidate = None
        for candidate in effects:
            candidate_guid = candidate.get("guid")
            if candidate_guid in reserved or candidate_guid in consumed:
                continue
            if _leaf(candidate.get("classPath")) not in area_classes:
                continue
            candidate_t = candidate.get("spawnT")
            if candidate_t is None or anchor_t is None or candidate_t < anchor_t:
                continue
            cx, cy = _last_point_xy(candidate, candidate.get("points") or [])
            if ax is None or ay is None or cx is None or cy is None:
                continue
            distance = math.hypot(cx - ax, cy - ay)
            if distance > PAIR_MAX_DISTANCE:
                continue
            if not _same_owner(anchor, candidate, resolve_player):
                continue
            score = (candidate_t, round(distance, 3), candidate_guid)
            if best_candidate is None or score < best_candidate[0]:
                best_candidate = (score, candidate_guid)
        if best_candidate is not None:
            consumed.add(best_candidate[1])
            areas[guid] = actors[best_candidate[1]]
    return areas, consumed


def _resolve_item_owner(actor, paired, resolve_player):
    """Instigator -> Owner -> the paired effect's Instigator/Owner (plan step 3)."""
    if resolve_player is None:
        return None
    for source in (actor, paired):
        if source is None:
            continue
        for guid in (source.get("instigator"), source.get("owner")):
            if not guid:
                continue
            player = resolve_player(guid, source.get("spawnT"))
            if player:
                return player
    return None


def _chain_resolver(resolve_player, actors):
    """``resolve_player`` plus one actor-reference hop (``Owner`` -> that actor's owner).

    Many effect actors carry no ``Instigator`` at all: their ``Owner`` is the actor
    that spawned them (measured in Haven 13.06 and the P7-1B re-decode: Omen's
    ``Zone_Wraith_4_Smoke`` has ``Owner`` = the ``Projectile_Wraith_4_Smoke`` guid,
    Clove's ``GameObject_Smonk_Q_DecayExplosion`` ``Owner`` = the ``Projectile_Smonk_
    DecayNade`` guid, Sova's ``GameObject_Hunter_Q_SonarBolt`` ``Owner`` = a bolt
    sub-object). Resolving those two levels with the caller's character->player map
    is still evidence-based -- the guid chain comes from the replay -- while the
    agent name is never consulted. Two hops maximum, then give up.
    """
    def resolve(guid, t_ms, depth=0):
        if guid is None:
            return None
        player = resolve_player(guid, t_ms)
        if player:
            return player
        if depth >= 2:
            return None
        other = actors.get(guid)
        if not isinstance(other, dict):
            return None
        for candidate in (other.get("instigator"), other.get("owner")):
            if candidate is None or candidate == guid:
                continue
            player = resolve(candidate, t_ms, depth + 1)
            if player:
                return player
        return None

    return resolve


def _effect_block(actor, classification):
    if actor is None:
        return None
    return {
        "t0": actor.get("spawnT"),
        "t1": actor.get("closeT"),
        "x": int(round(actor.get("spawnX") or 0.0)),
        "y": int(round(actor.get("spawnY") or 0.0)),
        "r": classification.get("radius") if classification else None,
        "rSource": classification.get("radiusSource") if classification else None,
        "classPath": actor.get("classPath"),
    }


def inside_minimap(map_info, x, y):
    """True when world ``(x, y)`` projects inside the minimap (0 <= p < 1, both axes).

    Same projection as ``viewer.js project()`` / ``p2_analysis._coordinate``:
    ``px`` from ``y``, ``py`` from ``x``. Used to clip a throw that flies out of
    the play space: the Haven 13.06 replay has one Sova Recon Bolt (guid 17102)
    whose samples run straight off the map (x 3627 -> 43805 world units), and
    AC-1.4 requires every drawn point to project inside the minimap.
    """
    px = map_info["xScalarToAdd"] + y * map_info["xMultiplier"]
    py = map_info["yScalarToAdd"] + x * map_info["yMultiplier"]
    return 0.0 <= px < 1.0 and 0.0 <= py < 1.0


def has_map_transform(map_info):
    """True when ``map_info`` carries the four minimap transform keys."""
    return isinstance(map_info, dict) and all(
        isinstance(map_info.get(key), (int, float))
        for key in ("xScalarToAdd", "yScalarToAdd", "xMultiplier", "yMultiplier"))


def track_records(records, *, resolve_player=None, classify=None, ability_table=None,
                  agent_map=None, include_effects=True, include_states=False,
                  require_scale_check=True, map_info=None, warnings=None, id_prefix=""):
    """Track every ability actor in ``records`` and return the round's ``utility`` list.

    Parameters
    ----------
    records
        Iterable of decoded NDJSON records (``actor_spawned``,
        ``export_group_received``, ``actor_closed``); anything else is ignored.
    resolve_player
        ``callable(guid, t_ms) -> player_id | None`` - the caller's per-life
        character/player-state map. Never inferred from the agent name. Resolvable
        guids are tried in order: the actor's ``Instigator``, its ``Owner``, then
        the paired effect's. Unresolved items get ``actor: None`` and
        ``confidence: "unowned"``.
    classify
        Optional ``callable(class_path) -> dict | None`` overriding the table
        lookup (P7-1A2 injects the JSON catalog through this).
    ability_table / agent_map
        Overrides for the built-in classification table / codename->agent map.
    include_effects
        Emit effect actors that no projectile was paired with (placed utility:
        smokes, cages, mollies, traps, explosions) as ``route: "landing"`` items.
    include_states
        Emit caster-side ``Ability_*``/``Gun_*`` state actors (off by default: they
        are not thrown or placed utility).
    require_scale_check
        Drop the path of any sampled actor whose spawn/first-sample ratio leaves
        99..101 (risk R3); the landing point is kept and a warning is appended.
    map_info
        Optional ``MAPS`` entry (the minimap transform, see ``inside_minimap``).
        When given, samples projecting outside the minimap are dropped (AC-1.4)
        and a landing/effect point outside it falls back to the last surviving
        sample instead of being drawn off-map.
    warnings
        Optional list that receives human-readable strings (one per dropped path).
    id_prefix
        Prefix for the item ids (``<id_prefix>u<guid>``).

    Returns
    -------
    list of dicts, one per tracked actor, sorted by ``(t, id)``.
    """
    actors = collect_actors(records)
    if resolve_player is not None:
        resolve_player = _chain_resolver(resolve_player, actors)
    table = BUILTIN_ABILITY_TABLE if ability_table is None else ability_table
    agent_map = BUILTIN_AGENT_BY_CODENAME if agent_map is None else agent_map
    if classify is None:
        def classifier(class_path, _table=table, _agent_map=agent_map):
            return classify_class(class_path, table=_table, agent_map=_agent_map)
    else:
        classifier = classify

    prepared = []
    for guid in sorted(actors):
        actor = actors[guid]
        classification = classifier(actor.get("classPath"))
        if classification is None:
            continue
        if classification.get("ult"):
            continue
        if classification.get("auxiliary"):
            # pulse / warning / manager sub-object of an ability already tracked
            continue
        role = classification.get("role")
        if role == "state" and not include_states:
            continue
        leaf = _leaf(actor.get("classPath"))
        if any(leaf.startswith(prefix) for prefix in _PAWN_PREFIXES):
            continue
        points = actor_points(actor)
        if has_map_transform(map_info):
            finite = [point for point in points
                      if inside_minimap(map_info, point["x"], point["y"])]
            if len(finite) != len(points):
                if warnings is not None:
                    warnings.append(
                        "utility actor %s: %d of %d samples project outside the minimap; "
                        "clipped" % (guid, len(points) - len(finite), len(points)))
                points = finite
        dropped_path = False
        ratio = scale_ratio(actor)
        if require_scale_check and ratio is not None and not (SCALE_MIN <= ratio <= SCALE_MAX):
            dropped_path = True
            if warnings is not None:
                warnings.append(
                    "utility actor %s: spawn/sample scale ratio %.2f outside %.0f..%.0f; "
                    "path dropped, landing kept" % (guid, ratio, SCALE_MIN, SCALE_MAX))
        actor["classification"] = classification
        actor["points"] = points
        actor["droppedPath"] = dropped_path
        prepared.append(actor)

    projectiles = [actor for actor in prepared if actor["classification"].get("role") == "projectile"]
    # A state actor is never a pairing candidate: it is not an area a throw turns
    # into, so it must not be consumed (and lose its own evidence item) either.
    effects = [actor for actor in prepared
               if actor["classification"].get("role") == "effect"
               and actor["classification"].get("kind") != "state"]
    pairs, best_effect = pair_effects(projectiles, effects, resolve_player)
    areas, area_guids = _attach_areas(projectiles, effects, best_effect, actors, resolve_player)
    paired_effect_guids = set(effect_guid for effect_guid, _ in pairs) | set(area_guids)

    items = []
    for actor in prepared:
        role = actor["classification"].get("role")
        paired = None
        if role == "projectile":
            effect_guid = best_effect.get(actor["guid"])
            if effect_guid is not None:
                paired = actors[effect_guid]
        elif role == "effect":
            if not include_effects or actor["guid"] in paired_effect_guids:
                continue
        if not actor["points"] and paired is None:
            # no spawn transform, no samples, nothing to pair with: nothing to draw
            if warnings is not None:
                warnings.append(
                    "utility actor %s: no spawn transform and no movement samples; skipped"
                    % actor["guid"])
            continue
        classification = actor["classification"]
        points = actor["points"]
        bounces = [] if actor["droppedPath"] else detect_bounces(points, classification.get("flight"))
        keep_indices = simplify_path(points, keep=bounces)
        simplified = [points[index] for index in keep_indices]
        start_t = points[0]["t"] if points else actor.get("spawnT")
        if start_t is None:
            continue
        path = []
        for point in simplified:
            path.append([int(round(point["t"] - start_t)), int(round(point["x"])), int(round(point["y"]))])
        bounce_rows = []
        for index in bounces:
            point = points[index]
            bounce_rows.append([int(round(point["t"] - start_t)), int(round(point["x"])), int(round(point["y"]))])
        route = "path" if (not actor["droppedPath"] and len(path) >= 2) else "landing"
        if route == "landing":
            path = []
            bounce_rows = []
        if paired is not None:
            land_point = (paired.get("spawnX"), paired.get("spawnY"))
            land_t = paired.get("spawnT")
            # ``land`` is the object the throw landed with; ``effect`` is that
            # object's area (P7-1F2). When the landing object is not the row's
            # area class and no area followed it, the throw gets ``effect: null``
            # -- the canister's own 7-50 ms is never drawn as the cage.
            area = areas.get(actor["guid"]) if role == "projectile" else paired
            effect_block = _effect_block(area, area.get("classification")) if area is not None else None
        elif points:
            land_point = (points[-1]["x"], points[-1]["y"])
            land_t = points[-1]["t"]
            effect_block = _effect_block(actor, classification) if role == "effect" else None
        else:
            land_point = (actor.get("spawnX"), actor.get("spawnY"))
            land_t = actor.get("spawnT")
            effect_block = _effect_block(actor, classification) if role == "effect" else None
        if has_map_transform(map_info) and land_point[0] is not None \
                and not inside_minimap(map_info, land_point[0], land_point[1]):
            # the paired effect (or a spawn without samples) sits outside the play
            # space: fall back to the last surviving sample (AC-1.4)
            effect_block = None
            if points:
                land_point = (points[-1]["x"], points[-1]["y"])
                land_t = points[-1]["t"]
            else:
                land_point = (None, None)
                land_t = None
        end_t = actor.get("closeT")
        if end_t is None:
            end_t = points[-1]["t"] if points else start_t
        player = _resolve_item_owner(actor, paired, resolve_player)
        items.append({
            "id": "%su%s" % (id_prefix, actor["guid"]),
            "t": int(start_t),
            "endT": int(end_t),
            "actor": player,
            "agent": classification.get("agent"),
            "ability": classification.get("ability"),
            "key": classification.get("key"),
            "kind": classification.get("kind"),
            "route": route,
            "path": path,
            "bounces": bounce_rows,
            "land": {
                "t": int(land_t) if land_t is not None else None,
                "x": int(round(land_point[0] or 0.0)) if land_point[0] is not None else None,
                "y": int(round(land_point[1] or 0.0)) if land_point[1] is not None else None,
            },
            "effect": effect_block,
            "confidence": "owned" if player else "unowned",
            "classPath": actor.get("classPath"),
        })
    items.sort(key=lambda item: (item["t"], item["id"]))
    return items

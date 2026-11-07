#!/usr/bin/env python3
"""Tests for the pure-Python utility tracker (``bot/replay/utility_track.py``).

Runs two ways:

    py -3.12 -m pytest bot/replay/test_utility_track.py -q
    py -3.12 bot/replay/test_utility_track.py

The fixtures under ``bot/replay/fixtures/utility/`` are real decoder records cut
verbatim from ``data/replays/bad1543033d11396/raw/events.ndjson.gz`` (Haven
13.06): Recon Bolt guid 3768, Phoenix Blaze guid 10136, Cypher cage projectile
9822 + cage object 9890, Sova Shock Bolt explosion 4274, an unlisted class
(Chamber/Cypher camera dart 34616), an ultimate slot actor 12632, and the P7-1F2
cross-agent slice ``u6106_molly_omen_zone.ndjson`` (Phoenix molly guid 6106 +
Omen's Dark Cover zone 6152 + the Wraith_PC controller row 722). They were cut
by streaming the raw bundle; the bundle itself is never decompressed to disk.
"""

from __future__ import annotations

import importlib.util
import json
import math
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

_spec = importlib.util.spec_from_file_location(
    "valoplant_utility_track", os.path.join(_HERE, "utility_track.py"))
utility_track = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(utility_track)

FIXTURE_DIR = os.path.join(_HERE, "fixtures", "utility")

# The production classification table (the JSON catalog the app injects), used by
# the Pearl regression test: those classes sit outside the built-in test table.
with open(os.path.join(_HERE, "ability_catalog.json"), encoding="utf-8") as _handle:
    CATALOG_ROWS = utility_track.catalog_rows(json.load(_handle))
CATALOG_AGENTS = {"Phoenix": "Phoenix", "Deadeye": "Chamber", "Vampire": "Reyna",
                  "Wraith": "Omen", "Gumshoe": "Cypher", "Hunter": "Sova",
                  "Wushu": "Jett", "Smonk": "Clove"}

RECON = "recon_bolt_3768.ndjson"
BLAZE = "blaze_10136.ndjson"
CAGE = "cyber_cage_9822_9890.ndjson"
SHOCK = "shock_bolt_explosion_4274.ndjson"
UNKNOWN = "unknown_class_34616.ndjson"
ULTIMATE = "ultimate_12632.ndjson"

RECON_GUID = 3768
BLAZE_GUID = 10136
CAGE_GUID = 9822
CAGE_OBJECT_GUID = 9890
SHOCK_GUID = 4274
UNKNOWN_GUID = 34616
ULTIMATE_GUID = 12632

# P7-1F2 regression slice (the u6106 cross-agent pair), cut verbatim by streaming
# data/replays/bad1543033d11396/raw/events.ndjson.gz: Phoenix's Hot Hands molly
# guid 6106 (spawn + close only -- no export row, so no Owner/Instigator and no
# samples), Omen's Dark Cover zone guid 6152 (spawn, close and 4 movement rows; the
# slice was trimmed from 166 movement rows to 6 records to stay inside 1A1's
# per-fixture budget, the full cut is kept at
# VALOPLANT_WORK/tmp/u6106-fixture-full.ndjson) and the Wraith_PC controller row
# guid 722 (Owner = pawn guid 76). The molly used to inherit the zone's owner
# P3/Omen because an ownerless projectile could pair with an owned effect of
# another agent.
U6106 = "u6106_molly_omen_zone.ndjson"
MOLLY_GUID = 6106
OMEN_ZONE_GUID = 6152
WRATH_PC_GUID = 722
WRATH_PAWN_GUID = 76
MOLLY_SPAWN_MS = 123558
MOLLY_CLOSE_MS = 123981
OMEN_ZONE_SPAWN_MS = 124063
OMEN_ZONE_CLOSE_MS = 140062
OMEN_ZONE_SPAWN = (5754.5, -8855.1)
OMEN_ZONE_MARKER = "Zone_Wraith_4_Smoke"

# P7-1F2 Pearl regression slice (the same bug on the user's 13.08 replay), cut the
# same way from data/replays/87976b8cd193496b/raw/events.ndjson.gz: guid 6574 is a
# Hot Hands molly with NO Owner/Instigator 72 units from Chamber's
# Patch_Deadeye_E_Slow_Large (guid 6600, 7007 ms), and guid 55020 is a Curveball
# that used to draw Reyna's GameObject_Vampire_Q_Heal_HealPool_High (guid 55056)
# as its area. The Deadeye_PC (930 -> pawn 200) and Phoenix_PC (1136 -> pawn 260)
# owner rows ride along so the owner chain is provable from the slice alone.
U6574 = "u6574_molly_chamber_patch.ndjson"
PEARL_MOLLY_GUID = 6574
PEARL_PATCH_GUID = 6600
PEARL_CURVEBALL_GUID = 55020
PEARL_HEALPOOL_GUID = 55056
DEADEYE_PC_GUID = 930
PHOENIX_PC_GUID = 1136
DEADEYE_PAWN_GUID = 200
PHOENIX_PAWN_GUID = 260
PEARL_PATCH_MS = 7007

# Class paths the synthetic pairing tests build (the built-in table's rows).
CAGE_PROJECTILE = "/Game/Characters/Gumshoe/S0/Ability_Q/Projectile_Gumshoe_Q_CageTrap"
CAGE_CANISTER = "/Game/Characters/Gumshoe/S0/Ability_Q/GameObject_Gumshoe_Q_CageTrap"
CAGE_ZONE = "/Game/Characters/Gumshoe/S0/Ability_Q/Zone_Gumshoe_Q_Cage"
WRAITH_PROJECTILE = "/Game/Characters/Wraith/S0/Ability_4/Projectile_Wraith_4_Smoke"
WRAITH_ZONE = "/Game/Characters/Wraith/S0/Ability_4/Zone_Wraith_4_Smoke"
MOLLY_PROJECTILE = "/Game/Characters/Phoenix/S0/Ability_4/Production/Projectile_Phoenix_4_Molotov_Production"
MOLLY_FIRE = "/Game/Characters/Phoenix/S0/Ability_4/Patch_Phoenix_MolotovFire"

# Cage object (guid 9890) spawn transform, straight from the fixture record.
CAGE_OBJECT_SPAWN = (4037.3, -6377.7)
CAGE_PROJECTILE_CLOSE_MS = 264597
CAGE_OBJECT_SPAWN_MS = 264597
CAGE_OBJECT_CLOSE_MS = 264609
SHOCK_SPAWN_MS = 65868
SHOCK_CLOSE_MS = 69873

ITEM_KEYS = {
    "id", "t", "endT", "actor", "agent", "ability", "key", "kind", "route",
    "path", "bounces", "land", "effect", "confidence", "classPath",
}

# Character net guid -> player id, as normalize.py builds it per life. Phoenix is
# on both teams (P4 and P9) in Haven, so the class codename must never be used to
# guess who threw something.
CHARACTER_TO_PLAYER = {
    522: "P1", 1028: "P6",          # Hunter (Sova)
    822: "P4", 1324: "P9",          # Phoenix, both teams
    1424: "P10",                    # Gumshoe (Cypher)
    1224: "P2", 722: "P3", 924: "P5", 1124: "P7", 34362: "P8",
}


def _resolve_player(guid, t_ms=None):
    return CHARACTER_TO_PLAYER.get(guid)


def _resolve_nothing(guid, t_ms=None):
    return None


def load(fixture):
    records = []
    with open(os.path.join(FIXTURE_DIR, fixture), encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def track(*fixtures, **kwargs):
    records = []
    for fixture in fixtures:
        records.extend(load(fixture))
    return utility_track.track_records(records, **kwargs)


def item_for(items, guid):
    match = [item for item in items if item["id"].endswith("u%d" % guid)]
    assert len(match) == 1, "expected exactly one item for guid %d, got %d" % (guid, len(match))
    return match[0]


def point(t, x, y, vx=0.0, vy=0.0, vz=0.0, z=0.0):
    return {"t": t, "x": x, "y": y, "z": z, "vx": vx, "vy": vy, "vz": vz}


# ---------------------------------------------------------------------------
# AC-1.1: the x100 scale is pinned
# ---------------------------------------------------------------------------

def test_scale_ratio_is_100_on_every_fixture_actor():
    actors = utility_track.collect_actors(
        load(RECON) + load(BLAZE) + load(CAGE) + load(UNKNOWN))
    checked = 0
    for guid, actor in actors.items():
        ratio = utility_track.scale_ratio(actor)
        if ratio is None:
            continue
        checked += 1
        assert utility_track.SCALE_MIN <= ratio <= utility_track.SCALE_MAX, (
            "guid %d spawn/sample scale ratio %.3f outside 99..101" % (guid, ratio))
    assert checked == 4, "expected 4 sampled fixture actors, checked %d" % checked


def test_velocity_and_spawn_transform_are_not_scaled():
    for fixture, guid in ((RECON, RECON_GUID), (BLAZE, BLAZE_GUID), (CAGE, CAGE_GUID)):
        actor = utility_track.collect_actors(load(fixture))[guid]
        first = actor["samples"][0]
        # spawn transform is true world units: 100x the first raw sample position
        assert abs(actor["spawnX"] / first["rawX"] - 100.0) < 1.0
        # velocity is 1:1 between the spawn record and the replication sample
        for spawn_axis, sample_axis in (("spawnVx", "vx"), ("spawnVy", "vy"), ("spawnVz", "vz")):
            assert abs(actor[spawn_axis] - first[sample_axis]) <= 1.5, (
                "%s %s vs sample %s differ" % (fixture, spawn_axis, sample_axis))


def test_raw_sample_count_matches_the_cut_slice():
    actors = utility_track.collect_actors(load(RECON))
    assert len(actors[RECON_GUID]["samples"]) == 30
    assert actors[RECON_GUID]["closeT"] == 50104
    assert actors[RECON_GUID]["instigator"] == 522


# ---------------------------------------------------------------------------
# AC-1.1: bounces
# ---------------------------------------------------------------------------

def test_recon_bolt_has_at_least_one_bounce():
    items = track(RECON, resolve_player=_resolve_player)
    item = item_for(items, RECON_GUID)
    assert item["route"] == "path"
    assert len(item["bounces"]) >= 1
    assert len(item["bounces"]) <= 2, "Recon Bolt maxes out at 2 bounces"
    # the documented reversal: direction 178 deg, horizontal speed 2546 -> 1655
    assert item["bounces"][0][0] == 48083 - 46409, "dt is relative to the item's t"


def test_blaze_steered_wall_has_no_bounces():
    items = track(BLAZE, resolve_player=_resolve_player)
    item = item_for(items, BLAZE_GUID)
    assert item["route"] == "path"
    assert item["bounces"] == []
    assert item["kind"] == "wall"
    assert len(item["path"]) >= 2


def test_bounce_needs_both_turn_and_speed_loss():
    # 90 deg turn, speed unchanged -> not a bounce (a single-axis sign flip)
    still = [point(0, 0, 0, vx=1000.0, vy=-1000.0), point(100, 100, -100, vx=-1000.0, vy=-1000.0)]
    assert utility_track.detect_bounces(still) == []
    # 178 deg turn but only a 5% speed loss -> not a bounce
    shallow = [point(0, 0, 0, vx=-2546.0, vy=36.0), point(78, 0, 0, vx=2418.0, vy=-34.0)]
    assert utility_track.detect_bounces(shallow) == []
    # turn outside the 120 ms window -> not a bounce
    slow = [point(0, 0, 0, vx=-2546.0, vy=36.0), point(400, 0, 0, vx=1655.0, vy=23.0)]
    assert utility_track.detect_bounces(slow) == []
    # both signals -> bounce at the sample where the new direction starts
    real = [point(0, 0, 0, vx=-2546.0, vy=36.0, vz=-175.0),
            point(78, 100, 0, vx=1655.0, vy=23.0, vz=-133.0)]
    assert utility_track.detect_bounces(real) == [1]


def test_vertical_velocity_flip_is_a_bounce():
    points = [point(0, 0, 0, vx=500.0, vy=0.0, vz=-511.0),
              point(32, 10, 0, vx=480.0, vy=0.0, vz=340.0)]
    assert utility_track.detect_bounces(points) == [1]
    # a single axis sign flip with no vertical flip and no speed loss is not
    gentle = [point(0, 0, 0, vx=100.0, vy=-1000.0, vz=-10.0),
              point(80, 80, -20, vx=-50.0, vy=-1050.0, vz=-20.0)]
    assert utility_track.detect_bounces(gentle) == []


def test_steered_and_guided_flight_skip_bounce_detection():
    real = [point(0, 0, 0, vx=-2546.0, vy=36.0, vz=100.0),
            point(78, 100, 0, vx=1655.0, vy=23.0, vz=-100.0)]
    assert utility_track.detect_bounces(real, flight="ballistic") == [1]
    assert utility_track.detect_bounces(real, flight="steered") == []
    assert utility_track.detect_bounces(real, flight="guided") == []


def test_vertical_velocity_flip_is_not_double_counted():
    points = [point(0, 0, 0, vx=-2546.0, vy=36.0, vz=-600.0),
              point(78, 100, 0, vx=1655.0, vy=23.0, vz=600.0)]
    assert utility_track.detect_bounces(points) == [1]


# ---------------------------------------------------------------------------
# AC-1.1: landing and effect pairing
# ---------------------------------------------------------------------------

def test_cage_lands_on_the_cage_object_spawn():
    items = track(CAGE, resolve_player=_resolve_player)
    item = item_for(items, CAGE_GUID)
    assert item["route"] == "path"
    distance = math.hypot(item["land"]["x"] - CAGE_OBJECT_SPAWN[0],
                          item["land"]["y"] - CAGE_OBJECT_SPAWN[1])
    assert distance <= 50.0, "cage landed %.1f units from the cage object" % distance
    assert distance <= 10.0, "P7-0 measured ~6 units; got %.1f" % distance
    assert item["land"]["t"] == CAGE_OBJECT_SPAWN_MS
    assert item["actor"] == "P10"
    assert item["confidence"] == "owned"
    # P7-1F2: the canister is the object the throw LANDS on, not the area. The
    # fixture carries no Zone_Gumshoe_Q_Cage actor, so the throw has no area and
    # the item must say so -- the canister's own 12 ms is never the cage.
    assert item["effect"] is None, "the canister's lifetime is not the cage's area"


def test_paired_effect_is_consumed_not_emitted_twice():
    items = track(CAGE, resolve_player=_resolve_player)
    assert [item["id"] for item in items] == ["u%d" % CAGE_GUID]
    assert all(not item["id"].endswith("u%d" % CAGE_OBJECT_GUID) for item in items)


def test_effect_with_another_owner_is_not_paired():
    records = (_projectile_records(guid=1, instigator=1424, owner=1444, spawn_ms=1000,
                                   close_ms=2000)
               + _effect_records(guid=2, instigator=9999, owner=9999, spawn_ms=2100,
                                 distance=10.0))
    items = utility_track.track_records(records, resolve_player=_resolve_player)
    projectile = item_for(items, 1)
    assert projectile["effect"] is None
    assert projectile["land"]["x"] == 1000  # its own last sample, not the effect spawn
    assert [item["route"] for item in items] == ["landing", "landing"]


def test_effect_outside_the_pairing_window_is_not_paired():
    """The window still decides, and its outcome is visible in ``land``.

    P7-1F2: the item's ``effect`` block is the *area*, and a cage canister is not
    the area -- so these synthetic cage pairs are read through ``land``, which is
    the landing object's spawn when the throw paired and the throw's own last
    sample when it did not.
    """
    own_last_sample = {"t": 1000, "x": 1000, "y": 1000}
    far_in_time = (_projectile_records(guid=1, instigator=1424, owner=1444, spawn_ms=1000, close_ms=2000)
                   + _effect_records(guid=2, instigator=1424, owner=1444, spawn_ms=2601, distance=10.0))
    assert utility_track.track_records(far_in_time)[0]["land"] == own_last_sample
    far_away = (_projectile_records(guid=1, instigator=1424, owner=1444, spawn_ms=1000, close_ms=2000)
                + _effect_records(guid=2, instigator=1424, owner=1444, spawn_ms=2100, distance=301.0))
    assert utility_track.track_records(far_away)[0]["land"] == own_last_sample
    inside = (_projectile_records(guid=1, instigator=1424, owner=1444, spawn_ms=1000, close_ms=2000)
              + _effect_records(guid=2, instigator=1424, owner=1444, spawn_ms=2500, distance=299.0))
    paired = utility_track.track_records(inside)[0]
    assert paired["land"] == {"t": 2500, "x": 1299, "y": 1000}
    # the canister is consumed by the pairing, and it is not the area
    assert [item["id"] for item in utility_track.track_records(inside)] == ["u1"]
    assert paired["effect"] is None


def test_landing_window_is_two_sided():
    """A zone that opens BEFORE the projectile's last sample still lands it.

    Run 279 made the window two-sided (``_landing_window_delta`` returns the
    distance to the window's near edge). Omen's orb sinks and is destroyed
    ~500 ms AFTER its zone opens, and Jett's smoke the other way round, so the
    area may precede the last recorded sample by up to ``PAIR_MAX_DELAY_MS``.
    """
    def pair(effect_ms):
        records = (_projectile_records(guid=1, instigator=1224, owner=1300, spawn_ms=1000,
                                       close_ms=2400, class_path=WRAITH_PROJECTILE, sample_ms=2000)
                   + _effect_records(guid=2, instigator=1224, owner=1, spawn_ms=effect_ms,
                                     distance=20.0, class_path=WRAITH_ZONE))
        return utility_track.track_records(records)

    # 400 ms before the last sample (t=2000) -> inside the two-sided window
    early = pair(1600)
    assert [item["id"] for item in early] == ["u1"]
    assert early[0]["effect"]["t0"] == 1600
    # 400 ms after the close (t=2400) -> inside as well
    late = pair(2800)
    assert [item["id"] for item in late] == ["u1"]
    assert late[0]["effect"]["t0"] == 2800
    # 601 ms before the window's low edge (the spawn, t=1000) -> refused
    refused = pair(399)
    assert sorted(item["id"] for item in refused) == ["u1", "u2"]
    assert item_for(refused, 1)["effect"] is None
    assert item_for(refused, 2)["id"] == "u2"      # the zone stays its own item


# ---------------------------------------------------------------------------
# P7-1F2: owner evidence, the effect-class gate, the area, the *_PC hop, clipping
# ---------------------------------------------------------------------------

def test_ownerless_projectile_never_inherits_another_agents_effect():
    """AC-1.3 regression on the real slice: molly 6106 and Omen's zone 6152.

    The molly carries no ``Owner``/``Instigator`` and no samples; Omen's smoke
    zone spawns 255 units and 82 ms away. Before P7-1F2 the molly paired with it,
    inherited owner P3 (agent Omen) and drew an Omen smoke as its area.
    """
    items = track(U6106, resolve_player=_resolve_player)
    assert len(items) == 2, [item["id"] for item in items]
    molly = item_for(items, MOLLY_GUID)
    assert molly["agent"] == "Phoenix"
    assert molly["ability"] == "Hot Hands"
    assert molly["route"] == "landing"          # spawn record only, no samples
    assert molly["actor"] is None
    assert molly["confidence"] == "unowned"
    assert molly["effect"] is None, "the molly must not draw an Omen smoke as its area"
    assert molly["land"]["t"] == MOLLY_SPAWN_MS      # its own spawn point, not the zone's
    assert molly["land"]["x"] != int(round(OMEN_ZONE_SPAWN[0]))
    # the zone stays its own item and keeps its own owner
    zone = item_for(items, OMEN_ZONE_GUID)
    assert zone["agent"] == "Omen"
    assert zone["actor"] == "P3"
    assert zone["confidence"] == "owned"
    assert zone["effect"]["classPath"].endswith(OMEN_ZONE_MARKER)
    assert zone["effect"]["t0"] == OMEN_ZONE_SPAWN_MS
    assert zone["effect"]["t1"] == OMEN_ZONE_CLOSE_MS
    assert zone["effect"]["t1"] - zone["effect"]["t0"] == 15999


def test_pearl_slice_keeps_foreign_effects_out_of_phoenix_items():
    """The same bug on the user's Pearl replay (13.08), on the production catalog.

    guid 6574 is a Hot Hands molly with no Owner/Instigator 72 units from Chamber's
    ``Patch_Deadeye_E_Slow_Large`` (guid 6600, 7007 ms): it used to come out owned
    by P5 (Chamber) and draw the slow patch as its area. guid 55020 is a Curveball
    whose own owner is right but whose area was Reyna's ``GameObject_Vampire_Q_
    Heal_HealPool_High`` (guid 55056). This one runs on the JSON catalog because
    Deadeye/Chamber and Vampire/Reyna are not in the built-in test table.
    """
    players = {DEADEYE_PAWN_GUID: "P5", PHOENIX_PAWN_GUID: "P1"}
    items = utility_track.track_records(
        load(U6574), ability_table=CATALOG_ROWS, agent_map=CATALOG_AGENTS,
        resolve_player=lambda guid, _t_ms: players.get(guid))
    assert sorted(item["id"] for item in items) == ["u55020", "u55056", "u6574", "u6600"]
    molly = item_for(items, PEARL_MOLLY_GUID)
    assert molly["agent"] == "Phoenix" and molly["kind"] == "molly"
    assert molly["actor"] is None and molly["confidence"] == "unowned"
    assert molly["effect"] is None, "Chamber's slow patch is not the molly's area"
    patch = item_for(items, PEARL_PATCH_GUID)
    assert patch["agent"] == "Chamber" and patch["ability"] == "Trademark"
    assert patch["actor"] == "P5"          # Deadeye_PC 930 -> pawn 200 -> P5
    assert patch["effect"]["classPath"].endswith("Patch_Deadeye_E_Slow_Large")
    assert patch["effect"]["t1"] - patch["effect"]["t0"] == PEARL_PATCH_MS
    curve = item_for(items, PEARL_CURVEBALL_GUID)
    assert curve["agent"] == "Phoenix" and curve["ability"] == "Curveball"
    assert curve["actor"] == "P1"          # Phoenix_PC 1136 -> pawn 260 -> P1
    assert curve["effect"] is None, "Reyna's heal pool is not the flash's area"
    assert item_for(items, PEARL_HEALPOOL_GUID)["agent"] == "Reyna"


def test_projectile_without_owner_evidence_pairs_with_nothing():
    """The ownerless-projectile rule, independent of any fixture."""
    molly = _projectile_records(guid=1, instigator=None, owner=None, spawn_ms=1000,
                                close_ms=1500, class_path=MOLLY_PROJECTILE)
    fire = _effect_records(guid=2, instigator=822, owner=1, spawn_ms=1550, distance=40.0,
                           class_path=MOLLY_FIRE, close_ms=6000)
    items = utility_track.track_records(molly + fire)
    projectile = item_for(items, 1)
    assert projectile["effect"] is None
    assert projectile["actor"] is None
    assert projectile["confidence"] == "unowned"
    assert item_for(items, 2)["effect"]["t1"] == 6000      # the fire is its own item


def test_effect_class_gate_rejects_another_abilities_effect():
    """Same owner, 30 units away, inside the window -- but the wrong class."""
    molly = _projectile_records(guid=1, instigator=822, owner=1444, spawn_ms=1000,
                                close_ms=1500, class_path=MOLLY_PROJECTILE)
    smoke = _effect_records(guid=2, instigator=822, owner=1444, spawn_ms=1550, distance=30.0,
                            class_path=WRAITH_ZONE, close_ms=8000)
    items = utility_track.track_records(molly + smoke)
    assert item_for(items, 1)["effect"] is None, "Phoenix's molly is not landed by Omen's zone"
    assert item_for(items, 2)["agent"] == "Omen"
    # the same shape with the right class does pair
    fire = _effect_records(guid=3, instigator=822, owner=1444, spawn_ms=1550, distance=30.0,
                           class_path=MOLLY_FIRE, close_ms=8000)
    paired = utility_track.track_records(molly + fire)
    assert item_for(paired, 1)["effect"]["classPath"].endswith("Patch_Phoenix_MolotovFire")


def test_cage_area_is_the_zone_that_follows_the_canister():
    """Landing = the canister's spawn; effect = the Zone that follows it (AC-1.8)."""
    canister_ms, zone_ms = 2000, 2600
    records = (_projectile_records(guid=1, instigator=1424, owner=1444, spawn_ms=1000, close_ms=2000)
               + _effect_records(guid=2, instigator=1424, owner=1444, spawn_ms=canister_ms,
                                 distance=6.0, close_ms=canister_ms + 10)
               + _effect_records(guid=3, instigator=1424, owner=1444, spawn_ms=zone_ms,
                                 distance=10.0, close_ms=zone_ms + 7250, class_path=CAGE_ZONE))
    items = utility_track.track_records(records, resolve_player=_resolve_player)
    cage = item_for(items, 1)
    assert cage["land"] == {"t": canister_ms, "x": 1006, "y": 1000}
    assert cage["effect"]["classPath"].endswith("Zone_Gumshoe_Q_Cage")
    assert cage["effect"]["t0"] == zone_ms
    assert cage["effect"]["t1"] - cage["effect"]["t0"] == 7250, "the zone's own lifetime"
    assert cage["effect"]["r"] == 372
    # neither the canister nor the zone is emitted a second time
    assert [item["id"] for item in items] == ["u1"]


def test_area_with_another_owner_is_refused_but_the_landing_stays():
    records = (_projectile_records(guid=1, instigator=1424, owner=1444, spawn_ms=1000, close_ms=2000)
               + _effect_records(guid=2, instigator=1424, owner=1444, spawn_ms=2000,
                                 distance=6.0, close_ms=2010)
               + _effect_records(guid=3, instigator=9999, owner=12345, spawn_ms=2600,
                                 distance=10.0, close_ms=9850, class_path=CAGE_ZONE))
    items = utility_track.track_records(records, resolve_player=_resolve_player)
    cage = item_for(items, 1)
    assert cage["land"] == {"t": 2000, "x": 1006, "y": 1000}
    assert cage["effect"] is None, "another owner's zone is never this throw's area"
    foreign = item_for(items, 3)
    assert foreign["effect"]["t1"] - foreign["effect"]["t0"] == 7250


def test_area_that_is_a_zone_too_far_away_is_not_attached():
    records = (_projectile_records(guid=1, instigator=1424, owner=1444, spawn_ms=1000, close_ms=2000)
               + _effect_records(guid=2, instigator=1424, owner=1444, spawn_ms=2000,
                                 distance=6.0, close_ms=2010)
               + _effect_records(guid=3, instigator=1424, owner=1444, spawn_ms=2600,
                                 distance=307.0, close_ms=9850, class_path=CAGE_ZONE))
    items = utility_track.track_records(records, resolve_player=_resolve_player)
    assert item_for(items, 1)["effect"] is None      # 301 units from the canister
    assert item_for(items, 3)["id"] == "u3"


def test_pc_controller_row_lets_ownership_hop_to_the_pawn():
    """``Instigator`` is a ``*_PC`` guid; only its row names the pawn guid."""
    controller = _character_records(WRATH_PC_GUID, WRATH_PAWN_GUID,
                                   "/Game/Characters/Wraith/Wraith_PC")
    records = controller + _projectile_records(guid=1, instigator=WRATH_PC_GUID, owner=None,
                                               spawn_ms=1000, close_ms=2000,
                                               class_path=WRAITH_PROJECTILE)
    players = {WRATH_PAWN_GUID: "P3"}
    items = utility_track.track_records(
        records, resolve_player=lambda guid, _t_ms: players.get(guid))
    item = item_for(items, 1)
    assert item["actor"] == "P3"      # 1 -> Wraith_PC 722 -> pawn 76 -> P3
    assert item["confidence"] == "owned"
    assert item["agent"] == "Omen"


def test_collector_keeps_one_owner_row_per_character_actor():
    collector = utility_track.UtilityCollector()
    row = _character_records(WRATH_PC_GUID, WRATH_PAWN_GUID,
                             "/Game/Characters/Wraith/Wraith_PC")[0]
    assert collector.feed(row) is True
    assert collector.feed(dict(row)) is False, "one owner row per character actor"
    assert WRATH_PC_GUID not in collector.actor_guids
    assert WRATH_PC_GUID in collector.owner_refs
    junk = {"type": "export_group_received", "actor_net_guid": 5,
            "class_path": "/Game/Weapons/EquippableGroundPickup",
            "payload": {"Owner": 3, "Instigator": 3}}
    assert collector.feed(junk) is False, "no /Characters/ path, no owner reference"


MAP_INFO = {"name": "test", "xMultiplier": 1e-4, "yMultiplier": -1e-4,
            "xScalarToAdd": 0.5, "yScalarToAdd": 0.5}


def test_map_transform_clips_offmap_samples_and_says_so():
    """AC-1.4: samples outside the minimap are dropped, with a warning."""
    records = [
        {"type": "actor_spawned", "time_ms": 1000, "actor_net_guid": 9,
         "replication_class_path": MOLLY_PROJECTILE,
         "location": {"x": 1000.0, "y": 1000.0, "z": 0.0},
         "velocity": {"x": 100000.0, "y": 0.0, "z": 0.0}},
        {"type": "export_group_received", "time_ms": 1000, "actor_net_guid": 9,
         "is_actor": True, "class_path": MOLLY_PROJECTILE,
         "payload": {"Instigator": 822, "Owner": 1444,
                     "ReplicatedMovement": {"location": {"x": 10.0, "y": 10.0, "z": 0.0},
                                            "linear_velocity": {"x": 100000.0, "y": 0.0, "z": 0.0}}}},
        {"type": "export_group_received", "time_ms": 1100, "actor_net_guid": 9,
         "is_actor": True, "class_path": MOLLY_PROJECTILE,
         "payload": {"ReplicatedMovement": {"location": {"x": 400.0, "y": 10.0, "z": 0.0},
                                            "linear_velocity": {"x": 100000.0, "y": 0.0, "z": 0.0}}}},
        {"type": "actor_closed", "time_ms": 1200, "actor_net_guid": 9, "reason": "destroyed"},
    ]
    warnings = []
    items = utility_track.track_records(records, map_info=MAP_INFO, warnings=warnings)
    item = item_for(items, 9)
    assert any("outside the minimap" in warning and "clipped" in warning
               for warning in warnings), warnings
    assert item["path"] == []          # only the in-map point survives -> a landing
    assert item["land"] == {"t": 1000, "x": 1000, "y": 1000}
    for x, y in [(item["land"]["x"], item["land"]["y"])]:
        projected = utility_track.inside_minimap(MAP_INFO, x, y)
        assert projected is True
    assert utility_track.inside_minimap(MAP_INFO, 40000, 1000) is False


def test_offmap_effect_spawn_falls_back_to_the_last_drawn_point():
    """AC-1.4: a paired effect outside the minimap is not drawn at all."""
    records = _projectile_records(guid=1, instigator=1424, owner=1444, spawn_ms=1000,
                                  close_ms=2000)
    records += [
        {"type": "actor_spawned", "time_ms": 2000, "actor_net_guid": 2,
         "replication_class_path": CAGE_CANISTER,
         "location": {"x": 40000.0, "y": 1000.0, "z": 0.0},
         "velocity": {"x": 0.0, "y": 0.0, "z": 0.0}},
        {"type": "export_group_received", "time_ms": 2000, "actor_net_guid": 2,
         "is_actor": True, "class_path": CAGE_CANISTER,
         "payload": {"Instigator": 1424, "Owner": 1444,
                     "ReplicatedMovement": {"location": {"x": 10.0, "y": 10.0, "z": 0.0},
                                            "linear_velocity": {"x": 0.0, "y": 0.0, "z": 0.0}}}},
    ]
    items = utility_track.track_records(records, resolve_player=_resolve_player,
                                        map_info=MAP_INFO)
    cage = item_for(items, 1)
    assert cage["effect"] is None
    assert cage["land"] == {"t": 1000, "x": 1000, "y": 1000}


def test_shock_bolt_explosion_is_a_landing_only_item():
    items = track(SHOCK, resolve_player=_resolve_player)
    item = item_for(items, SHOCK_GUID)
    assert item["route"] == "landing"
    assert item["path"] == []
    assert item["bounces"] == []
    assert item["kind"] == "blast"
    assert item["ability"] == "Shock Bolt"
    assert item["key"] == "Q"
    assert item["classPath"].endswith("GameObject_Hunter_4_ExplosiveBolt_Explosion")
    assert item["land"] == {"t": SHOCK_SPAWN_MS, "x": -3100, "y": -9537}
    assert item["effect"] == {"t0": SHOCK_SPAWN_MS, "t1": SHOCK_CLOSE_MS,
                              "x": -3100, "y": -9537, "r": 260, "rSource": "community",
                              "classPath": "/Game/Characters/Hunter/S0/Ability_4/"
                                           "GameObject_Hunter_4_ExplosiveBolt_Explosion"}
    # this class carries no Owner/Instigator in the export at all
    assert item["actor"] is None
    assert item["confidence"] == "unowned"


def test_include_effects_false_drops_unpaired_effects():
    assert [item["id"] for item in track(SHOCK, include_effects=False)] == []


def test_actor_without_any_position_is_skipped_with_a_warning():
    class_path = "/Game/Characters/Hunter/S0/Ability_4/Projectile_Hunter_4_ExplosiveBolt"
    records = [
        {"type": "actor_spawned", "time_ms": 1000, "actor_net_guid": 77,
         "replication_class_path": class_path, "location": None, "velocity": None},
        {"type": "actor_closed", "time_ms": 2000, "actor_net_guid": 77, "reason": "destroyed"},
    ]
    warnings = []
    assert utility_track.track_records(records, warnings=warnings) == []
    assert warnings and "no spawn transform" in warnings[0]


def test_actor_with_samples_but_no_spawn_transform_never_invents_a_origin():
    class_path = "/Game/Characters/Hunter/S0/Ability_4/Projectile_Hunter_4_ExplosiveBolt"
    records = [
        {"type": "actor_spawned", "time_ms": 1000, "actor_net_guid": 78,
         "replication_class_path": class_path, "location": None, "velocity": None},
        {"type": "export_group_received", "time_ms": 1000, "actor_net_guid": 78,
         "is_actor": True, "kind": "actor", "class_path": class_path,
         "payload": {"ReplicatedMovement": {"location": {"x": 40.0, "y": -63.0, "z": 3.0},
                                            "linear_velocity": {"x": 100.0, "y": 0.0, "z": 0.0}}}},
    ]
    items = utility_track.track_records(records)
    assert len(items) == 1
    item = items[0]
    assert item["path"] == []        # a single point is a landing, not a path
    assert item["route"] == "landing"
    assert item["land"] == {"t": 1000, "x": 4000, "y": -6300}
    assert item["t"] == 1000


# ---------------------------------------------------------------------------
# AC-1.1: ownership
# ---------------------------------------------------------------------------

def test_actor_is_null_and_unowned_when_the_resolver_knows_nothing():
    items = track(RECON, resolve_player=_resolve_nothing)
    item = item_for(items, RECON_GUID)
    assert item["actor"] is None
    assert item["confidence"] == "unowned"
    # the agent name still comes from the class path, never from the owner
    assert item["agent"] == "Sova"


def test_ownership_comes_from_the_resolver_not_the_agent_name():
    calls = []

    def spy(guid, t_ms=None):
        calls.append(guid)
        return CHARACTER_TO_PLAYER.get(guid)

    items = track(BLAZE, resolve_player=spy)
    item = item_for(items, BLAZE_GUID)
    assert item["actor"] == "P4"
    assert 822 in calls, "the Instigator character guid must be resolved"
    # Phoenix is also P9 in Haven: a class-name guess would be wrong half the time
    assert item["agent"] == "Phoenix"


# ---------------------------------------------------------------------------
# AC-1.1: catalog, ultimates, unknown classes
# ---------------------------------------------------------------------------

def test_ultimate_slot_actor_is_excluded():
    records = load(ULTIMATE)
    paths = [r.get("replication_class_path") or r.get("class_path")
             or r.get("export_group_path") or "" for r in records]
    paths = [path for path in paths if path]
    assert paths and all("/Ability_X/" in path for path in paths)
    items = utility_track.track_records(records, resolve_player=_resolve_player)
    assert items == []
    with_states = utility_track.track_records(records, resolve_player=_resolve_player,
                                              include_states=True)
    assert with_states == [], "Ability_X must be excluded even when state actors are emitted"


def test_unknown_class_is_tracked_without_crashing():
    items = track(UNKNOWN, resolve_player=_resolve_player)
    item = item_for(items, UNKNOWN_GUID)
    assert item["kind"] == "unknown"
    assert item["ability"] is None
    assert item["key"] is None
    assert item["agent"] == "Cypher"      # codename still resolves
    assert item["classPath"].endswith("Projectile_Gumshoe_E_CameraTrackingDart")
    assert item["route"] == "path"        # it has real samples, so it gets a path
    assert len(item["path"]) >= 2
    assert item["actor"] == "P8"
    assert item["confidence"] == "owned"


def test_records_that_are_not_abilities_are_ignored():
    junk = [{"type": "rpc_received", "time_ms": 1, "actor_net_guid": 7},
            {"type": "player_life_change", "time_ms": 2, "character_net_guid": 522},
            {"type": "valorant_flash_cast", "time_ms": 3, "flash_actor_net_guid": 9},
            {"type": "export_group_received", "time_ms": 4, "actor_net_guid": 11,
             "is_actor": True, "class_path": "/Game/Weapons/WeaponPickups/EquippablePickupProjectile",
             "payload": {"ReplicatedMovement": {"location": {"x": 1.0, "y": 2.0, "z": 3.0}}}}]
    assert utility_track.track_records(junk) == []
    assert utility_track.track_records([]) == []


def test_atomicity_of_a_bad_scale_drops_the_path_but_keeps_the_landing():
    records = load(CAGE)
    for record in records:
        payload = record.get("payload")
        if isinstance(payload, dict) and "ReplicatedMovement" in payload:
            location = payload["ReplicatedMovement"]["location"]
            for axis in ("x", "y", "z"):
                location[axis] = location[axis] * 10.0     # simulate a scale change
    warnings = []
    items = utility_track.track_records(records, resolve_player=_resolve_player, warnings=warnings)
    item = item_for(items, CAGE_GUID)
    assert item["route"] == "landing"
    assert item["path"] == []
    assert warnings and "scale ratio" in warnings[0]


# ---------------------------------------------------------------------------
# AC-1.1: path simplification
# ---------------------------------------------------------------------------

def test_simplification_is_bounded_and_keeps_every_bounce():
    points = [point(index * 10, float(index), 0.0, vx=1000.0, vy=0.0, vz=0.0)
              for index in range(400)]
    bounces = [37, 180, 301]
    for index in bounces:
        points[index]["vx"], points[index]["vy"] = 0.0, 900.0
        points[index]["vz"] = 90.0
        points[index - 1]["vx"], points[index - 1]["vy"], points[index - 1]["vz"] = -900.0, 0.0, -90.0
    kept = utility_track.simplify_path(points, keep=bounces)
    assert len(kept) <= utility_track.MAX_PATH_POINTS
    assert kept[0] == 0 and kept[-1] == len(points) - 1
    for index in bounces:
        assert index in kept
    assert utility_track.detect_bounces(points, flight="ballistic") == bounces


def test_real_paths_respect_the_cap_and_keep_their_bounces():
    items = track(RECON, BLAZE, CAGE, resolve_player=_resolve_player)
    for item in items:
        assert len(item["path"]) <= utility_track.MAX_PATH_POINTS
        path_rows = {tuple(row) for row in item["path"]}
        for row in item["bounces"]:
            assert tuple(row) in path_rows, "%s dropped a bounce point" % item["id"]


def test_simplification_of_a_short_path_is_identity():
    points = [point(0, 0.0, 0.0), point(10, 50.0, 0.0)]
    assert utility_track.simplify_path(points) == [0, 1]
    assert utility_track.simplify_path([point(0, 0.0, 0.0)]) == [0]


# ---------------------------------------------------------------------------
# Item contract
# ---------------------------------------------------------------------------

def test_item_shape_and_types():
    items = track(RECON, BLAZE, CAGE, SHOCK, UNKNOWN, ULTIMATE, resolve_player=_resolve_player)
    assert len(items) == 5, [item["id"] for item in items]
    ids = [item["id"] for item in items]
    assert len(set(ids)) == len(ids)
    assert items == sorted(items, key=lambda item: (item["t"], item["id"]))
    for item in items:
        assert set(item) == ITEM_KEYS, set(item) ^ ITEM_KEYS
        assert item["route"] in ("path", "landing")
        assert item["kind"]
        assert isinstance(item["t"], int) and isinstance(item["endT"], int)
        assert item["endT"] >= item["t"]
        assert isinstance(item["land"]["t"], int)
        assert isinstance(item["land"]["x"], int) and isinstance(item["land"]["y"], int)
        assert all(len(row) == 3 and all(isinstance(value, int) for value in row)
                   for row in item["path"] + item["bounces"])
        assert len(item["path"]) <= utility_track.MAX_PATH_POINTS
        assert (item["actor"] is None) == (item["confidence"] == "unowned")
        if item["route"] == "landing":
            assert item["path"] == [] and item["bounces"] == []
        if item["path"]:
            assert item["path"][0][0] == 0
            assert all(row[0] <= item["endT"] - item["t"] for row in item["path"])
        effect = item["effect"]
        if effect is not None:
            assert set(effect) == {"t0", "t1", "x", "y", "r", "rSource", "classPath"}
            # the effect block names the *effect object* it was paired with (the area
            # class p7_utility_counts.py section (e) matches on) -- the GameObject_*
            # class, not the Projectile_* class the item itself carries.
            if effect["classPath"] is not None:
                assert "/Game/" in effect["classPath"], effect["classPath"]
            assert isinstance(effect["x"], int) and isinstance(effect["y"], int)
            if effect["t1"] is not None:
                assert effect["t1"] >= effect["t0"]


def test_id_prefix_is_honoured():
    items = track(RECON, id_prefix="r04-")
    assert items[0]["id"] == "r04-u%d" % RECON_GUID


def test_injected_classifier_and_table_are_used():
    calls = []

    def classifier(class_path):
        calls.append(class_path)
        return {"codename": "Hunter", "agent": "Sova", "ability": "Recon Bolt", "slot": "Ability2",
                "key": "E", "kind": "recon", "flight": "ballistic", "maxBounces": 2,
                "radius": 300, "radiusSource": "community", "ult": False, "role": "projectile"}

    items = track(RECON, classify=classifier)
    assert calls and all("/Game/Characters/" in path for path in calls)
    assert items[0]["ability"] == "Recon Bolt"

    table = [{"tokens": ["RevealBolt"], "codename": "Hunter", "slot": "Ability2",
              "name": "Recon Bolt (test table)", "kind": "recon", "flight": "ballistic"}]
    items = track(RECON, ability_table=table, agent_map={"Hunter": "Sova"},
                  resolve_player=_resolve_player)
    assert items[0]["ability"] == "Recon Bolt (test table)"
    assert items[0]["key"] == "E"


def test_classify_class_rejects_non_ability_paths():
    assert utility_track.classify_class("/Game/Weapons/EquippableGroundPickup") is None
    assert utility_track.classify_class(None) is None
    unknown = utility_track.classify_class("/Game/Characters/Gumshoe/S0/Ability_Q/Zone_Mystery_Thing")
    assert unknown["kind"] == "unknown"
    assert unknown["agent"] == "Cypher"
    assert unknown["role"] == "effect"
    ult = utility_track.classify_class("/Game/Characters/Phoenix/S0/Ability_X/Production/GameObject_Phoenix_X_ResTarget_Production")
    assert ult["ult"] is True


# ---------------------------------------------------------------------------
# Synthetic record builders (pairing tests)
# ---------------------------------------------------------------------------

def _projectile_records(guid, instigator, owner, spawn_ms, close_ms,
                        class_path=CAGE_PROJECTILE, sample_ms=None):
    return [
        {"type": "actor_spawned", "time_ms": spawn_ms, "actor_net_guid": guid,
         "replication_class_path": class_path,
         "location": {"x": 1000.0, "y": 1000.0, "z": 100.0},
         "velocity": {"x": 100.0, "y": 0.0, "z": 0.0}},
        {"type": "export_group_received", "time_ms": sample_ms if sample_ms is not None else spawn_ms,
         "actor_net_guid": guid,
         "is_actor": True, "kind": "actor", "class_path": class_path,
         "payload": {"Instigator": instigator, "Owner": owner,
                     "ReplicatedMovement": {"location": {"x": 10.0, "y": 10.0, "z": 1.0},
                                            "linear_velocity": {"x": 100.0, "y": 0.0, "z": 0.0}}}},
        {"type": "actor_closed", "time_ms": close_ms, "actor_net_guid": guid, "reason": "destroyed"},
    ]


def _effect_records(guid, instigator, owner, spawn_ms, distance,
                    class_path=CAGE_CANISTER, close_ms=None):
    return [
        {"type": "actor_spawned", "time_ms": spawn_ms, "actor_net_guid": guid,
         "replication_class_path": class_path,
         "location": {"x": 1000.0 + distance, "y": 1000.0, "z": 100.0},
         "velocity": {"x": 0.0, "y": 0.0, "z": 0.0}},
        {"type": "export_group_received", "time_ms": spawn_ms, "actor_net_guid": guid,
         "is_actor": True, "kind": "actor", "class_path": class_path,
         "payload": {"Instigator": instigator, "Owner": owner}},
        {"type": "actor_closed", "time_ms": close_ms if close_ms is not None else spawn_ms + 1000,
         "actor_net_guid": guid, "reason": "destroyed"},
    ]


def _character_records(guid, pawn_guid, class_path):
    """One owner-reference row for a ``*_PC`` controller (P7-1A2's kept row)."""
    return [
        {"type": "export_group_received", "time_ms": 64, "actor_net_guid": guid,
         "is_actor": True, "kind": "actor", "class_path": class_path,
         "export_group_path": class_path + "." + class_path.rsplit("/", 1)[-1] + "_C",
         "payload": {"Controller": pawn_guid, "Instigator": guid, "Owner": pawn_guid}},
    ]


# ---------------------------------------------------------------------------
# Per-player counts (P7-3)
# ---------------------------------------------------------------------------

COUNT_PLAYERS = [
    {"id": "P1", "agent": "Sova"},
    {"id": "P2", "agent": "Cypher"},
    {"id": "P3", "agent": None},
]


def _count_item(actor, kind="recon", ability="Recon Bolt"):
    return {"actor": actor, "kind": kind, "ability": ability, "t": 0}


def test_player_counts_rows_for_all_players_and_owned_items_only():
    items = [
        _count_item("P1", "recon", "Recon Bolt"),
        _count_item("P1", "blast", "Shock Bolt"),
        _count_item("P1", "blast", "Shock Bolt"),
        _count_item(None),               # unowned: ignored
        {"kind": "smoke", "ability": "Cloudburst"},  # no actor at all: ignored
        _count_item("P9"),               # actor not in the player list: ignored
    ]
    counts = utility_track.player_counts(items, COUNT_PLAYERS)
    assert sorted(counts) == ["P1", "P2", "P3"], sorted(counts)
    assert counts["P1"] == {
        "agent": "Sova", "total": 3,
        "byKind": {"recon": 1, "blast": 2},
        "abilities": {"Recon Bolt": 1, "Shock Bolt": 2},
    }
    # every player gets a row, zeros included, and an unknown agent stays None
    assert counts["P2"] == {"agent": "Cypher", "total": 0, "byKind": {}, "abilities": {}}
    assert counts["P3"] == {"agent": None, "total": 0, "byKind": {}, "abilities": {}}


def test_player_counts_totals_match_the_breakdowns():
    items = [_count_item("P2", kind, ability)
             for kind, ability in (("cage", "Cyber Cage"), ("cage", "Cyber Cage"),
                                   ("smoke", "Dark Cover"))]
    counts = utility_track.player_counts(items, COUNT_PLAYERS)
    row = counts["P2"]
    assert row["total"] == len(items)
    assert sum(row["byKind"].values()) == row["total"]
    assert sum(row["abilities"].values()) == row["total"]


def test_player_counts_null_kind_and_ability_do_not_crash():
    counts = utility_track.player_counts(
        [_count_item("P1", None, None), _count_item("P1", "recon", None)], COUNT_PLAYERS)
    row = counts["P1"]
    assert row["total"] == 2
    assert row["byKind"] == {"recon": 1}
    assert row["abilities"] == {}


def test_player_counts_empty_inputs():
    assert utility_track.player_counts([], []) == {}
    counts = utility_track.player_counts([], COUNT_PLAYERS)
    assert all(row["total"] == 0 for row in counts.values())


def test_player_counts_of_tracked_items_match_owned_items():
    items = track(RECON, BLAZE, CAGE, SHOCK, ULTIMATE, resolve_player=_resolve_player)
    players = [{"id": "P%d" % index, "agent": None} for index in range(1, 11)]
    counts = utility_track.player_counts(items, players)
    owned = [item for item in items if item["actor"] is not None]
    assert sum(row["total"] for row in counts.values()) == len(owned)
    for item in owned:
        assert counts[item["actor"]]["total"] >= 1


def test_player_counts_ignore_idless_rows_and_unowned_items():
    players = COUNT_PLAYERS + [{"id": None, "agent": "Sova"}]
    counts = utility_track.player_counts([_count_item(None), _count_item("P1")], players)
    assert None not in counts, sorted(counts, key=str)
    assert sorted(counts) == ["P1", "P2", "P3"], sorted(counts)
    assert counts["P1"]["total"] == 1
    assert sum(row["total"] for row in counts.values()) == 1


# ---------------------------------------------------------------------------
# State actors (P7 rest-fixes lane D2)
# ---------------------------------------------------------------------------

HEAL_POOL_HIGH = ("/Game/Characters/Vampire/S0/Ability_Q/"
                  "GameObject_Vampire_Q_Heal_HealPool_High")


def test_heal_pool_classifies_as_a_caster_side_state():
    """Reyna's Devour heal pool is evidence, not thrown utility (AC-R1)."""
    row = utility_track.classify_class(HEAL_POOL_HIGH, table=CATALOG_ROWS,
                                       agent_map=CATALOG_AGENTS)
    assert row["kind"] == "state"
    assert row["ability"] is None and row["key"] is None
    assert row["flight"] == "state"
    assert row["ult"] is False
    assert row["agent"] == "Reyna"
    # the High and AutoActivate variants share the one token
    auto = utility_track.classify_class(HEAL_POOL_HIGH.replace("_High", "_AutoActivate"),
                                        table=CATALOG_ROWS, agent_map=CATALOG_AGENTS)
    assert auto["kind"] == "state" and auto["agent"] == "Reyna"
    # an ordinary Reyna ability on the same catalog is untouched
    leer = utility_track.classify_class(
        "/Game/Characters/Vampire/S0/Ability_4/Ability_Vampire_4_NearsightAoE",
        table=CATALOG_ROWS, agent_map=CATALOG_AGENTS)
    assert leer["kind"] == "nearsight" and leer["ability"] == "Leer" and leer["key"] == "C"


def test_state_items_never_count_and_normal_items_still_do():
    items = [_count_item("P1", "state", None), _count_item("P1", "recon", "Recon Bolt")]
    counts = utility_track.player_counts(items, COUNT_PLAYERS)
    assert counts["P1"] == {"agent": "Sova", "total": 1,
                            "byKind": {"recon": 1}, "abilities": {"Recon Bolt": 1}}
    assert sum(row["total"] for row in counts.values()) == 1


def test_pearl_slice_emits_the_heal_pool_as_state():
    """The real slice keeps the pool in ``utility[]`` but out of the counts."""
    players = {DEADEYE_PAWN_GUID: "P5", PHOENIX_PAWN_GUID: "P1",
               PEARL_HEALPOOL_GUID: "P2"}
    items = utility_track.track_records(
        load(U6574), ability_table=CATALOG_ROWS, agent_map=CATALOG_AGENTS,
        resolve_player=lambda guid, _t_ms: players.get(guid))
    pool = item_for(items, PEARL_HEALPOOL_GUID)
    assert pool["kind"] == "state" and pool["ability"] is None
    assert pool["agent"] == "Reyna" and pool["route"] == "landing"
    counts = utility_track.player_counts(
        items, [{"id": "P%d" % index, "agent": None} for index in range(1, 11)])
    counted = sum(row["total"] for row in counts.values())
    assert counted == len([item for item in items
                           if item["actor"] is not None and item["kind"] != "state"])


# ---------------------------------------------------------------------------
# Plain runner (no pytest required)
# ---------------------------------------------------------------------------

def _tests():
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            yield name, value


def main(argv):
    only = [arg for arg in argv if not arg.startswith("-")]
    failures = 0
    ran = 0
    for name, function in _tests():
        if only and not any(token in name for token in only):
            continue
        ran += 1
        try:
            function()
        except Exception as exc:  # noqa: BLE001 - plain assert runner
            failures += 1
            sys.stdout.write("FAIL %s: %s: %s\n" % (name, type(exc).__name__, exc))
        else:
            sys.stdout.write("ok   %s\n" % name)
    sys.stdout.write("%d test(s), %d failure(s)\n" % (ran, failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

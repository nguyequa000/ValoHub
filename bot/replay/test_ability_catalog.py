#!/usr/bin/env python3
"""Contract tests for ``bot/replay/ability_catalog.json`` (P8-B2).

The catalog is the tracker's only source of truth for "which class path is which
ability".  P8-B2 added rows for the agents that are absent from the Haven 13.06
and Pearl 13.08 replays, and these tests exist to keep that addition honest:

* every declared token must be a real substring of a class path that the
  **published 13.09 decoder tree** actually declares, and it must resolve under
  the agent's *own* ``/Game/Characters/<Codename>/`` folder -- a token that only
  matches by accident is a fabricated token;
* no token may belong to two agents (the tracker matches substrings, so a shared
  token silently reassigns another agent's utility);
* an agent is either fully covered (four slots) or explicitly listed in
  ``notInCatalog.agents`` with a reason -- silence is not allowed;
* the 26 rows that the two test replays proved are pinned byte-for-byte: a later
  card may add rows and may never edit one.  ``Hunter|Ultimate`` used to be the
  27th pinned row; P8-B2b was sent specifically to repair it (it named Owl Drone,
  which the roster puts on C, and carried the drone's ``Ability_E`` classes under
  the X slot), so it is pinned by the Hunter regression test below instead of by
  the untouched-row digest;
* no token may be claimed by both an ultimate row and a non-ultimate row: a class
  is either the ult or it is not, and the tracker's ult flag decides the icon and
  the feed label.

Replay-verified rows are exempt from the vendor-tree check:
``Ability_Phoenix_X_SelfRes_Production`` and two siblings were renamed in the
13.09 tree after the 13.06 replay proved them, so that check ``pytest.skip``s
with those names in the reason instead of pretending the tree still carries
them.  Rows added here are ``verified: "unverified"`` and get no such exemption.

Read-only: no network, no subprocess, no writes.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

CATALOG_PATH = os.path.join(_HERE, "ability_catalog.json")
ROSTER_PATH = os.path.join(_HERE, "fixtures", "roster.json")
VENDOR_ROOT = os.path.join(_REPO_ROOT, "vendorValorantReplayParser", "src")

SCHEMA = "valoplant_ability_catalog/v1"
SOURCE = "decoder descriptor class names + valorant-api roster"
NOT_RESEARCHED = "not researched"

REQUIRED_KEYS = (
    "codename", "slot", "name", "kind", "flight", "maxBounces", "effectClass",
    "tokens", "radius_m", "radiusNote", "duration_s", "durationNote", "source",
    "patch", "ult", "verified",
)
SLOT_LETTER = {"Grenade": "C", "Ability1": "Q", "Ability2": "E", "Ultimate": "X"}

# The rows that the Haven 13.06 / Pearl 13.08 replays proved, in file order.
# sha256 over json.dumps(rows, indent=2, sort_keys=True) of the pre-P8-B2 file
# (git HEAD): a value change, an added row inside that block, or a dropped row
# all move this digest.
#
# ``Hunter|Ultimate`` is deliberately absent: P8-B2b (2026-10-31) was created to
# repair that row, which pinned the Owl Drone (a C ability) under the X slot.  The
# old 27-row digest was
#   614706ec10ec05df6fa4562fedb75d6ca37154c699bc0e8927edb3a387a8e064
# and it stays reproducible from git HEAD; the 26 rows below are the ones that
# must never change, and their digest is unchanged by the B2b re-slot.
ORIGINAL_ROW_KEYS = (
    "Hunter|Ability1", "Hunter|Ability2",
    "Gumshoe|Ability1", "Gumshoe|Grenade", "Gumshoe|Ability2", "Gumshoe|Ultimate",
    "Wushu|Grenade", "Wushu|Ultimate",
    "Wraith|Ability1", "Wraith|Ability2", "Wraith|Ultimate",
    "Phoenix|Ability1", "Phoenix|Ability2", "Phoenix|Grenade", "Phoenix|Ultimate",
    "Smonk|Ability1", "Smonk|Ability2", "Smonk|Ultimate",
    "Deadeye|Ultimate",
    "BountyHunter|Ability1", "BountyHunter|Ability2",
    "Deadeye|Grenade", "Deadeye|Ability2",
    "Killjoy|Grenade",
    "Stealth|Ability1",
    "Vampire|Grenade",
)
ORIGINAL_ROWS_SHA256 = "bd5ee7fee3afd0b89c484127c71a783bc91d9ad2c07b9809f15b57c54422bee7"

_CLASS_PATH_RE = re.compile(r"(/Game/Characters/[^\s\"')\],]+)")
_CODENAME_RE = re.compile(r"/Game/Characters/([A-Za-z0-9_]+)/")

_CACHE = {}


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def _load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_HERE, filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _read_catalog():
    with open(CATALOG_PATH, encoding="utf-8") as handle:
        return json.load(handle)


def _read_agents():
    return _load_module("valoplant_normalize_for_catalog", "normalize.py").AGENT_BY_CODENAME


def _read_tracker():
    return _load_module("valoplant_utility_track_for_catalog", "utility_track.py")


def _scan_vendor_paths():
    """Every ``/Game/Characters/...`` class path the vendored decoder declares."""
    if not os.path.isdir(VENDOR_ROOT):
        pytest.skip("vendor tree not present at %s" % VENDOR_ROOT)
    paths = set()
    for dirpath, _dirnames, filenames in os.walk(VENDOR_ROOT):
        for filename in filenames:
            if not filename.endswith(".cs"):
                continue
            try:
                with open(os.path.join(dirpath, filename), encoding="utf-8-sig",
                          errors="replace") as handle:
                    text = handle.read()
            except OSError:      # pragma: no cover - unreadable checkout
                continue
            paths.update(match.group(1) for match in _CLASS_PATH_RE.finditer(text))
    assert paths, "no class paths found under %s" % VENDOR_ROOT
    return paths


def _cached(factory):
    key = factory.__name__
    if key not in _CACHE:
        _CACHE[key] = factory()
    return _CACHE[key]


class _Lazy(object):
    """Tiny fixture stand-in so the file also runs as a plain script."""

    catalog = property(lambda self: _cached(_read_catalog))
    agent_by_codename = property(lambda self: _cached(_read_agents))
    tracker = property(lambda self: _cached(_read_tracker))
    vendor_class_paths = property(lambda self: _cached(_scan_vendor_paths))


LAZY = _Lazy()


@pytest.fixture(scope="module")
def catalog():
    return LAZY.catalog


@pytest.fixture(scope="module")
def agent_by_codename():
    return LAZY.agent_by_codename


@pytest.fixture(scope="module")
def tracker():
    return LAZY.tracker


@pytest.fixture(scope="module")
def vendor_class_paths():
    return LAZY.vendor_class_paths


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rows(catalog):
    return list(catalog["abilities"].items())


def _codename_of(path):
    match = _CODENAME_RE.search(path or "")
    return match.group(1).lower() if match else None


def _token_paths(token, vendor_class_paths):
    return [path for path in vendor_class_paths if token in path]


def _slots_by_agent(catalog):
    covered = {}
    for _key, row in _rows(catalog):
        covered.setdefault(row["codename"], set()).add(row["slot"])
    return covered


# ---------------------------------------------------------------------------
# 1. Schema
# ---------------------------------------------------------------------------

def test_schema_is_the_tracker_schema(catalog, tracker):
    assert catalog["schema"] == SCHEMA == tracker.CATALOG_SCHEMA
    assert catalog["keyBySlot"] == {"Ability1": "Q", "Ability2": "E",
                                    "Grenade": "C", "Ultimate": "X"}
    assert isinstance(catalog.get("abilities"), dict) and catalog["abilities"]
    assert isinstance(catalog.get("generatedAt"), str)


# ---------------------------------------------------------------------------
# 2. Row shape
# ---------------------------------------------------------------------------

def test_every_row_carries_the_required_key_set(catalog):
    key_by_slot = catalog["keyBySlot"]
    for key, row in _rows(catalog):
        missing = [name for name in REQUIRED_KEYS if name not in row]
        assert not missing, "%s is missing %s" % (key, missing)
        assert key == "%s|%s" % (row["codename"], row["slot"]), key
        assert row["slot"] in key_by_slot, "%s has an unknown slot" % key
        assert isinstance(row["name"], str) and row["name"].strip(), key
        assert isinstance(row["kind"], str) and row["kind"].strip(), key
        assert isinstance(row["flight"], str) and row["flight"].strip(), key
        assert row["ult"] is (row["slot"] == "Ultimate"), \
            "%s: ult must be true only for the Ultimate slot" % key
        assert isinstance(row["tokens"], list) and row["tokens"], key
        for token in row["tokens"]:
            assert isinstance(token, str) and token.strip(), "%s: bad token %r" % (key, token)
            assert " " not in token, "%s: token %r is not a class-path fragment" % (key, token)
        for field in ("radius_m", "duration_s"):
            value = row[field]
            assert value is None or isinstance(value, (int, float)), "%s.%s" % (key, field)
        assert row["maxBounces"] is None or isinstance(row["maxBounces"], int), key


# ---------------------------------------------------------------------------
# 3. Token evidence: every token must come out of the published decoder tree
# ---------------------------------------------------------------------------

def test_unverified_tokens_resolve_in_the_published_vendor_tree(catalog, vendor_class_paths):
    """A row added without a replay must be provable from the decoder tree alone.

    The check is deliberately stronger than "is a substring of some class path":
    every path a token matches must sit under ``/Game/Characters/<this codename>/``,
    so a token that also (or only) matches another agent's folder fails here.
    """
    misses = []
    for key, row in _rows(catalog):
        if row["verified"] != "unverified":
            # replay-proven rows are covered by the exemption test below
            continue
        for token in row["tokens"]:
            matched = _token_paths(token, vendor_class_paths)
            mine = [path for path in matched
                    if _codename_of(path) == row["codename"].lower()]
            if not matched:
                misses.append("%s: %r is in no vendor class path" % (key, token))
            elif not mine:
                misses.append("%s: %r only matches %r"
                              % (key, token, sorted({_codename_of(p) for p in matched})))
    assert not misses, "\n".join(misses)


def test_replay_verified_tokens_are_exempt_with_a_named_reason(catalog, vendor_class_paths):
    """Rows the replays proved may name classes the 13.09 tree renamed away."""
    renames = []
    for key, row in _rows(catalog):
        if row["verified"] == "unverified":
            continue
        for token in row["tokens"]:
            if not _token_paths(token, vendor_class_paths):
                renames.append("%s: %r" % (key, token))
    if renames:
        pytest.skip("replay-verified rows predate a 13.09 class rename, so the "
                    "vendor tree no longer carries them: " + "; ".join(renames))


# ---------------------------------------------------------------------------
# 4. Token collisions
# ---------------------------------------------------------------------------

def test_no_token_is_shared_between_agents(catalog, vendor_class_paths):
    seen = {}            # token -> codename
    for key, row in _rows(catalog):
        for token in row["tokens"]:
            owner = seen.setdefault(token, row["codename"])
            assert owner == row["codename"], \
                "token %r is used by both %s and %s" % (token, owner, row["codename"])
            folders = {_codename_of(path) for path in _token_paths(token, vendor_class_paths)}
            # An empty set means the 13.09 tree renamed this replay-proven class;
            # that case belongs to the exemption test, not to this one.
            if folders:
                assert folders == {row["codename"].lower()}, \
                    "%s: token %r resolves under %s, not only its own folder" \
                    % (key, token, sorted(folders))


def test_no_token_is_a_substring_of_another_agents_token(catalog):
    tokens = [(row["codename"], token) for _key, row in _rows(catalog)
              for token in row["tokens"]]
    collisions = []
    for codename_a, token_a in tokens:
        for codename_b, token_b in tokens:
            if codename_a != codename_b and token_a in token_b:
                collisions.append("%s/%r inside %s/%r"
                                  % (codename_a, token_a, codename_b, token_b))
    assert not collisions, "; ".join(collisions)


def test_no_token_is_both_an_ult_and_a_non_ult(catalog):
    """The ult flag decides the icon and the feed label, so it must be unambiguous.

    P8-B2b exists because one row put a C ability's classes behind ``ult: true``;
    this is the general form of that bug: any token reachable from both an
    ``ult: true`` row and an ``ult: false`` row makes the tracker's answer depend
    on row order.
    """
    ult_rows, non_ult_rows = {}, {}
    for key, row in _rows(catalog):
        bucket = ult_rows if row["ult"] else non_ult_rows
        for token in row["tokens"]:
            bucket.setdefault(token, set()).add(key)
    shared = sorted(set(ult_rows) & set(non_ult_rows))
    assert not shared, "tokens claimed by both an ult and a non-ult row: %s" % {
        token: (sorted(non_ult_rows[token]), sorted(ult_rows[token])) for token in shared}


def test_hunter_rows_keep_the_drone_on_c_and_the_fury_on_x(catalog, tracker,
                                                           agent_by_codename,
                                                           vendor_class_paths):
    """The P8-B2b regression: Owl Drone is C, Hunter's Fury is X, sets disjoint.

    Before B2b ``Hunter|Ultimate`` was named "Owl Drone" and carried the three
    ``Ability_E`` drone classes, so a placed drone came back as the ultimate
    (wrong name, wrong icon, ``ult: True``) and the C slot had no row at all.
    """
    rows = catalog["abilities"]
    assert "Hunter|Grenade" in rows, "the Grenade row B2b added is gone"
    grenade, ultimate = rows["Hunter|Grenade"], rows["Hunter|Ultimate"]

    assert (grenade["name"], grenade["slot"], grenade["ult"]) \
        == ("Owl Drone", "Grenade", False), grenade
    assert (ultimate["name"], ultimate["slot"], ultimate["ult"]) \
        == ("Hunter's Fury", "Ultimate", True), ultimate
    assert ultimate["name"] != "Owl Drone"

    drone_tokens, fury_tokens = set(grenade["tokens"]), set(ultimate["tokens"])
    assert not (drone_tokens & fury_tokens), drone_tokens & fury_tokens

    # Every drone token is a real class the tree files under Ability_E, and the
    # ult token is the tree's X class: no token crosses the two folders.
    for token in sorted(drone_tokens):
        paths = _token_paths(token, vendor_class_paths)
        assert paths, token
        assert all("/Ability_E/" in path for path in paths), (token, paths)
    for token in sorted(fury_tokens):
        paths = _token_paths(token, vendor_class_paths)
        assert paths, token
        assert all("/Ability_X/" in path for path in paths), (token, paths)

    # The tracker must agree end to end: the drone class is C/Owl Drone, not-ult,
    # and the laser class is X/Hunter's Fury, ult.
    table = tracker.catalog_rows(catalog)
    drone = tracker.classify_class(
        "/Game/Characters/Hunter/S0/Ability_E/Drone/Ability_Hunter_E_DeployDrone"
        ".Ability_Hunter_E_DeployDrone_C", table=table, agent_map=agent_by_codename)
    assert drone and (drone["agent"], drone["ability"], drone["key"], drone["ult"]) \
        == ("Sova", "Owl Drone", "C", False), drone
    fury = tracker.classify_class(
        "/Game/Characters/Hunter/S0/Ability_X/Ability_Hunter_X_LaserMulti"
        ".Ability_Hunter_X_LaserMulti_C", table=table, agent_map=agent_by_codename)
    assert fury and (fury["agent"], fury["ability"], fury["key"], fury["ult"]) \
        == ("Sova", "Hunter's Fury", "X", True), fury


# ---------------------------------------------------------------------------
# 5. Coverage: four rows or an explicit notInCatalog entry
# ---------------------------------------------------------------------------

def test_every_agent_is_complete_or_declared_not_in_catalog(catalog, agent_by_codename):
    covered = _slots_by_agent(catalog)
    declared = (catalog.get("notInCatalog") or {}).get("agents") or {}
    problems = []
    for codename, display in sorted(agent_by_codename.items()):
        slots = covered.get(codename, set())
        if len(slots) == 4:
            continue
        entry = declared.get(codename)
        if not entry:
            problems.append("%s (%s) has %d row(s) %s and no notInCatalog entry"
                            % (codename, display, len(slots), sorted(slots)))
            continue
        assert entry.get("name") == display, "%s: notInCatalog name %r != %r" \
            % (codename, entry.get("name"), display)
        assert isinstance(entry.get("reason"), str) and entry["reason"].strip(), codename
        if slots:
            # a partial agent must say what happened to each uncovered slot
            documented = entry.get("slots") or {}
            for slot in sorted(set(SLOT_LETTER) - slots):
                assert slot in documented, "%s: %s is uncovered and undocumented" \
                    % (codename, slot)
    assert not problems, "\n".join(problems)


def test_fully_covered_agents_are_not_listed_as_not_in_catalog(catalog):
    covered = _slots_by_agent(catalog)
    declared = set((catalog.get("notInCatalog") or {}).get("agents") or {})
    complete = {codename for codename, slots in covered.items() if len(slots) == 4}
    overlap = complete & declared
    assert not overlap, "fully covered agents listed in notInCatalog: %s" % sorted(overlap)


# ---------------------------------------------------------------------------
# 6. The 27 replay-proven rows are untouched
# ---------------------------------------------------------------------------

def test_original_rows_are_untouched(catalog):
    rows = catalog["abilities"]
    present = tuple(key for key in rows if key in ORIGINAL_ROW_KEYS)
    assert present == ORIGINAL_ROW_KEYS, \
        "the 26 replay-proven rows changed order or disappeared"
    original = {key: rows[key] for key in ORIGINAL_ROW_KEYS}
    digest = hashlib.sha256(json.dumps(
        original, indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    assert digest == ORIGINAL_ROWS_SHA256, \
        "a replay-proven row was edited (digest %s)" % digest


def test_added_rows_are_marked_unverified_and_not_researched(catalog):
    rows = catalog["abilities"]
    added = {key: row for key, row in rows.items() if key not in ORIGINAL_ROW_KEYS}
    assert added, "P8-B2 added no rows"
    for key, row in added.items():
        assert row["verified"] == "unverified", key
        assert row["patch"] == "13.09", key
        assert row["source"] == SOURCE, key
        assert row["radius_m"] is None and row["duration_s"] is None, key
        assert row["radiusNote"] == NOT_RESEARCHED, key
        assert row["durationNote"] == NOT_RESEARCHED, key
        assert isinstance(row.get("slotBasis"), str) and row["slotBasis"].strip(), \
            "%s: an unverified row must say what puts it in this slot" % key


# ---------------------------------------------------------------------------
# 7. Names come from the roster, rows classify in the tracker
# ---------------------------------------------------------------------------

def test_names_match_the_roster_for_their_slot(catalog, agent_by_codename):
    if not os.path.isfile(ROSTER_PATH):
        pytest.skip("roster snapshot absent: %s" % ROSTER_PATH)
    with open(ROSTER_PATH, encoding="utf-8") as handle:
        roster = {agent["name"]: agent for agent in json.load(handle)["agents"]}
    mismatches = {}
    for key, row in _rows(catalog):
        display = agent_by_codename.get(row["codename"])
        assert display, "%s: codename is not in AGENT_BY_CODENAME" % key
        abilities = (roster.get(display) or {}).get("abilities")
        assert abilities, "%s: %s is not in the roster snapshot" % (key, display)
        letter = SLOT_LETTER[row["slot"]]
        if row["name"] != abilities[letter]:
            mismatches[key] = (row["name"], abilities[letter])
    # Two replay-proven rows predate the roster snapshot and are pinned: later
    # cards may not edit them, so the defects are frozen here and reported, not
    # repaired.  Both are casing-only.
    # Hunter|Ultimate used to be the third entry (Owl Drone under X); P8-B2b fixed
    # it, so it must now match the roster exactly like every other row.
    KNOWN_PINNED = {
        "Phoenix|Ultimate": ("Run It Back", "Run it Back"),
        "Stealth|Ability1": ("Blindside", "BLINDSIDE"),
    }
    added_mismatch = sorted(set(mismatches) - set(KNOWN_PINNED))
    assert not added_mismatch, \
        "an added row does not carry the roster name: %s" \
        % {key: mismatches[key] for key in added_mismatch}
    assert mismatches == KNOWN_PINNED, mismatches


def test_new_rows_classify_in_the_tracker(catalog, tracker, agent_by_codename):
    """A row that cannot classify a synthetic class path is a dead row."""
    table = tracker.catalog_rows(catalog)
    samples = {
        "/Game/Characters/Iris/S0/Ability_Q/Ability_Iris_Harmonize.Ability_Iris_Harmonize_C":
            ("Miks", "Harmonize", "Ability1", "Q"),
        "/Game/Characters/Thorne/S0/Ability_Q/Ability_Thorne_Q_Heal_Production_New"
        ".Ability_Thorne_Q_Heal_Production_New_C":
            ("Sage", "Healing Orb", "Ability2", "E"),
        "/Game/Characters/Nox/S0/Ability_4/Projectile_Nox_BarbedWire"
        ".Projectile_Nox_BarbedWire_C":
            ("Vyse", "Razorvine", "Grenade", "C"),
        "/Game/Characters/Paprika/S0/Ability_X/Ability_Paprika_X_FullBendPop"
        ".Ability_Paprika_X_FullBendPop_C":
            ("Hush", "Suspension", "Ultimate", "X"),
        "/Game/Characters/Clay/S0/Ability_E/Pawn_Clay_E_Boomba.Pawn_Clay_E_Boomba_C":
            ("Raze", "Boom Bot", "Grenade", "C"),
    }
    for path, expected in samples.items():
        result = tracker.classify_class(path, table=table, agent_map=agent_by_codename)
        assert result, path
        assert (result["agent"], result["ability"], result["slot"], result["key"]) \
            == expected, (path, result)
    # Brimstone is withheld on purpose: his folders are not slot components, so
    # the tracker never even sees the actor.  That is what notInCatalog says.
    brimstone = tracker.classify_class(
        "/Game/Characters/Sarge/S0/Ability_Molotov/Ability_Sarge_Q_Molotov_Production"
        ".Ability_Sarge_Q_Molotov_Production_C", table=table, agent_map=agent_by_codename)
    assert brimstone is None, brimstone


# ---------------------------------------------------------------------------
# Plain runner (no pytest required)
# ---------------------------------------------------------------------------

def _run_plain():
    failures = 0
    ran = 0
    for name, value in sorted(globals().items()):
        if not (name.startswith("test_") and callable(value)):
            continue
        needed = list(value.__code__.co_varnames[:value.__code__.co_argcount])
        ran += 1
        try:
            value(*[getattr(LAZY, arg) for arg in needed])
        except BaseException as exc:                       # noqa: BLE001
            if type(exc).__name__ == "Skipped":
                sys.stdout.write("skip %s: %s\n" % (name, exc))
            else:
                failures += 1
                sys.stdout.write("FAIL %s: %s: %s\n" % (name, type(exc).__name__, exc))
        else:
            sys.stdout.write("ok   %s\n" % name)
    sys.stdout.write("%d test(s), %d failure(s)\n" % (ran, failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_run_plain())

#!/usr/bin/env python3
"""Contract tests for the shipped agent art and ``assets/agents/index.json`` v2.

P8-B1b. ``test_agent_ids.py`` covers the name <-> uuid map; this file covers what
that map feeds: the **files on disk** (30 map icons, 30 portraits, >=120 ability
icons), their **pixel geometry**, and the **index.json v2 manifest**.

Everything here is asserted against bytes read from the PNG IHDR chunk -- never
against filenames or against the index's own claims. An index that says
``"map": "agents/map/<uuid>.png"`` does not prove a file exists, and a file named
``<uuid>.png`` does not prove it is 96x96.

The geometry / coverage / index helpers accept a ``root`` so the negative
control can run against a scratch copy of the tree::

    # real tree
    py -3.12 -m pytest bot/replay/test_agent_assets.py -q

    # negative control: a copy whose map icon was downscaled to 32x32
    VALOHUB_ASSETS_ROOT=<scratch>/assets py -3.12 -m pytest bot/replay/test_agent_assets.py -q

No network, no subprocess, no writes: reads IHDR headers and JSON only.
"""

from __future__ import annotations

import importlib
import json
import os
import re
import struct
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

DEFAULT_ASSETS_ROOT = os.path.join(
    _REPO_ROOT, "bot", "web", "static", "replays_app", "assets")
# Test seam for the negative control only; a normal run always uses the repo tree.
ASSETS_ROOT = os.path.abspath(
    os.environ.get("VALOHUB_ASSETS_ROOT") or DEFAULT_ASSETS_ROOT)

# The art is Riot-derived and fetched, never committed; a checkout without it must not fail
# the suite. The fetcher-constants test below needs no files and still runs.
ASSETS_PRESENT = os.path.isdir(ASSETS_ROOT)
_FETCH_HINT = ("agent art is not tracked; run `py -3.12 tools/fetch_agent_assets.py` "
               "(needs network and Pillow) or point VALOHUB_ASSETS_ROOT at a fetched tree")


@pytest.fixture(autouse=True)
def _requires_assets(request):
    if request.node.name == "test_fetcher_module_exposes_the_transform_constants":
        return
    if not ASSETS_PRESENT:
        pytest.skip(_FETCH_HINT)

IDS_PATH = os.path.join(_HERE, "agent_ids.json")
INDEX_REL = os.path.join("agents", "index.json")

# Playable roster at patch 13.09 (Riot, 2026-10-31) -- same constant as
# test_agent_ids.py:EXPECTED_AGENTS.
EXPECTED_AGENTS = 30
MIN_ABILITY_ICONS = 120

MAP_EDGE = (64, 64)              # tools/fetch_agent_assets.py MAP_EDGE, verbatim minimapPortrait
PORTRAIT_EDGE = (96, 96)         # PORTRAIT_EDGE, LANCZOS from displayIcon
ABILITY_MIN_EDGE = 16            # anything smaller is not a legible icon
ABILITY_MAX_EDGE = 128           # ABILITY_MAX

INDEX_SCHEMA = "valoplant_agent_assets/v2"
COMBAT_SLOTS = ("Ability1", "Ability2", "Grenade", "Ultimate")
ALL_SLOTS = COMBAT_SLOTS + ("Passive",)

AGENT_PATH_RE = re.compile(r"^agents/(map/)?[a-z0-9-]+\.png$")
ABILITY_PATH_RE = re.compile(
    r"^abilities/[a-z0-9-]+-(Ability1|Ability2|Grenade|Ultimate|Passive)\.png$")

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


# ---------------------------------------------------------------------------
# helpers -- pure, root-parameterised, no pytest assertions inside
# ---------------------------------------------------------------------------

def png_size(path):
    """(width, height) from the IHDR chunk, or None when the file is not a PNG.

    The IHDR is always the first chunk: 8-byte signature, 4-byte length,
    4-byte type, then width/height as big-endian uint32.
    """
    with open(path, "rb") as handle:
        head = handle.read(24)
    if len(head) < 24 or head[:8] != PNG_MAGIC or head[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", head[16:24])


def _png_names(directory):
    return sorted(name for name in os.listdir(directory)
                  if name.lower().endswith(".png"))


def _size_problems(directory, ok, expected):
    """Human-readable list of every PNG whose IHDR fails ``ok``."""
    problems = []
    for name in _png_names(directory):
        size = png_size(os.path.join(directory, name))
        if size is None:
            problems.append("%s: not a readable PNG (bad signature/IHDR)" % name)
        elif not ok(size):
            problems.append("%s: %dx%d, expected %s" % (name, size[0], size[1], expected))
    return problems


def map_icon_problems(root=None):
    return _size_problems(os.path.join(root or ASSETS_ROOT, "agents", "map"),
                          lambda size: size == MAP_EDGE, "%dx%d" % MAP_EDGE)


def portrait_problems(root=None):
    return _size_problems(os.path.join(root or ASSETS_ROOT, "agents"),
                          lambda size: size == PORTRAIT_EDGE, "%dx%d" % PORTRAIT_EDGE)


def ability_problems(root=None):
    return _size_problems(
        os.path.join(root or ASSETS_ROOT, "abilities"),
        lambda size: all(ABILITY_MIN_EDGE <= side <= ABILITY_MAX_EDGE for side in size),
        "%d <= side <= %d" % (ABILITY_MIN_EDGE, ABILITY_MAX_EDGE))


def read_ids(path=IDS_PATH):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def asset_problems(root, agent_ids):
    """Counts per tier plus, per agent id, both uuid-named files on disk."""
    root = root or ASSETS_ROOT
    map_dir = os.path.join(root, "agents", "map")
    portrait_dir = os.path.join(root, "agents")
    ability_dir = os.path.join(root, "abilities")

    problems = []
    counts = {"map": len(_png_names(map_dir)),
              "portrait": len([name for name in _png_names(portrait_dir)]),
              "ability": len(_png_names(ability_dir))}
    if counts["map"] != EXPECTED_AGENTS:
        problems.append("agents/map/: %d PNGs, expected %d" % (counts["map"], EXPECTED_AGENTS))
    if counts["portrait"] != EXPECTED_AGENTS:
        problems.append("agents/: %d PNGs, expected %d" % (counts["portrait"], EXPECTED_AGENTS))
    if counts["ability"] < MIN_ABILITY_ICONS:
        problems.append("abilities/: %d PNGs, expected >= %d"
                        % (counts["ability"], MIN_ABILITY_ICONS))

    for name, agent_uuid in sorted(agent_ids.get("byName", {}).items()):
        stem = str(agent_uuid).lower()
        for tier, directory in (("map", map_dir), ("portrait", portrait_dir)):
            if not os.path.isfile(os.path.join(directory, stem + ".png")):
                problems.append("%s: no %s icon at %s/%s.png"
                                % (name, tier, os.path.relpath(directory, root).replace(os.sep, "/"), stem))
    return problems, counts


def load_index(root=None):
    with open(os.path.join(root or ASSETS_ROOT, INDEX_REL), "r", encoding="utf-8") as handle:
        return json.load(handle)


def index_agents(index):
    return {name: entry for name, entry in index.items() if name != "_meta"}


def index_paths(entry):
    """Every path an index entry publishes, as (field, path) pairs."""
    yield "portrait", entry.get("portrait")
    yield "map", entry.get("map")
    abilities = entry.get("abilities") or {}
    for ability, path in abilities.items():
        yield "abilities[%s]" % ability, path


def entry_problems(index):
    problems = []
    for name, entry in index_agents(index).items():
        if not isinstance(entry, dict):
            problems.append("%s: entry is %s, not an object" % (name, type(entry).__name__))
            continue
        for field in ("uuid", "portrait", "map", "abilities"):
            if field not in entry:
                problems.append("%s: missing %r" % (name, field))
        if not isinstance(entry.get("abilities"), dict) or not entry.get("abilities"):
            problems.append("%s: abilities is not a non-empty object" % name)
    return problems


def path_contract_problems(index):
    problems = []
    for name, entry in index_agents(index).items():
        for field, path in index_paths(entry):
            if not isinstance(path, str):
                problems.append("%s.%s: %r is not a string" % (name, field, path))
            elif field.startswith("abilities["):
                if not ABILITY_PATH_RE.match(path):
                    problems.append("%s.%s: %r breaks the ability path contract"
                                    % (name, field, path))
            elif not AGENT_PATH_RE.match(path):
                problems.append("%s.%s: %r breaks the agent path contract"
                                % (name, field, path))
    return problems


def unresolved_problems(root, index):
    """Every published path must resolve, except those declared missing."""
    root = root or ASSETS_ROOT
    declared = {(row.get("path") or "").lstrip("./")
                for row in (index.get("_meta", {}).get("missing") or [])
                if isinstance(row, dict)}
    problems = []
    for name, entry in index_agents(index).items():
        if not isinstance(entry, dict):
            continue
        for field, path in index_paths(entry):
            if not isinstance(path, str):
                continue
            if not os.path.isfile(os.path.join(root, path)):
                if path not in declared:
                    problems.append("%s.%s: %r is neither on disk nor declared missing"
                                    % (name, field, path))
            elif path in declared:
                problems.append("%s.%s: %r is on disk but declared missing"
                                % (name, field, path))
    return problems


def _tier_of(path):
    if path.startswith("agents/map/"):
        return "map"
    if path.startswith("agents/"):
        return "portrait"
    if path.startswith("abilities/"):
        return "ability"
    return None


def missing_row_problems(root, index):
    """Rows must name a real agent and, for every art tier, a genuinely absent file."""
    root = root or ASSETS_ROOT
    agents = index_agents(index)
    rows = index.get("_meta", {}).get("missing")
    problems = []
    if not isinstance(rows, list):
        return ["_meta.missing is %s, not a list" % type(rows).__name__]
    for position, row in enumerate(rows):
        label = "missing[%d]" % position
        if not isinstance(row, dict):
            problems.append("%s: %s, not an object" % (label, type(row).__name__))
            continue
        name = row.get("agent")
        path = row.get("path")
        if name not in agents:
            problems.append("%s: %r is not an agent in the index" % (label, name))
        elif row.get("uuid") not in (None, agents[name].get("uuid")):
            problems.append("%s: uuid %r disagrees with the entry's %r"
                            % (label, row.get("uuid"), agents[name].get("uuid")))
        if not isinstance(row.get("reason"), str) or not str(row.get("reason") or "").strip():
            problems.append("%s: no reason given" % label)
        if not isinstance(path, str) or not path:
            problems.append("%s: no path given" % label)
            continue
        tier = _tier_of(path)
        if tier is None:
            problems.append("%s: %r is not an agent/ability path" % (label, path))
            continue
        if not (AGENT_PATH_RE.match(path) or ABILITY_PATH_RE.match(path)):
            problems.append("%s: %r breaks the path contract" % (label, path))
        if os.path.exists(os.path.join(root, path)):
            problems.append("%s: %r is declared missing but exists on disk"
                            % (label, path))
    return problems


def _require_assets(root=None):
    root = root or ASSETS_ROOT
    absent = [rel for rel in ("agents", "agents/map", "abilities")
              if not os.path.isdir(os.path.join(root, *rel.split("/")))]
    if absent:
        pytest.skip("agent assets unavailable under %s (%s); run "
                    "`py -3.12 tools/fetch_agent_assets.py` to fetch them"
                    % (root, ", ".join(absent)))
    return root


# ---------------------------------------------------------------------------
# 1. coverage -- files on disk, keyed by the ids file
# ---------------------------------------------------------------------------

def test_map_directory_holds_exactly_thirty_icons():
    root = _require_assets()
    names = _png_names(os.path.join(root, "agents", "map"))
    assert len(names) == EXPECTED_AGENTS, "agents/map/: %d PNGs: %s" % (len(names), names)


def test_portrait_directory_holds_exactly_thirty_portraits():
    root = _require_assets()
    names = _png_names(os.path.join(root, "agents"))     # top level only; map/ is a dir
    assert len(names) == EXPECTED_AGENTS, "agents/: %d PNGs: %s" % (len(names), names)


def test_ability_directory_holds_at_least_120_icons():
    root = _require_assets()
    names = _png_names(os.path.join(root, "abilities"))
    assert len(names) >= MIN_ABILITY_ICONS, "abilities/: %d PNGs: %s" % (len(names), names)


def test_every_known_agent_has_a_uuid_named_map_icon_and_portrait():
    root = _require_assets()
    agent_ids = read_ids()
    assert len(agent_ids["byName"]) == EXPECTED_AGENTS, sorted(agent_ids["byName"])
    problems, _counts = asset_problems(root, agent_ids)
    assert not problems, problems


# ---------------------------------------------------------------------------
# 2. geometry -- read from IHDR, not from names or the index
# ---------------------------------------------------------------------------

def test_map_icons_are_64x64():
    root = _require_assets()
    problems = map_icon_problems(root)
    assert not problems, problems


def test_portraits_are_96x96():
    root = _require_assets()
    problems = portrait_problems(root)
    assert not problems, problems


def test_ability_icons_stay_between_16_and_128_on_both_sides():
    root = _require_assets()
    problems = ability_problems(root)
    assert not problems, problems


# ---------------------------------------------------------------------------
# 3. index.json v2 contract
# ---------------------------------------------------------------------------

def test_index_meta_declares_schema_v2():
    root = _require_assets()
    index = load_index(root)
    assert "_meta" in index, sorted(index)
    meta = index["_meta"]
    assert meta.get("schema") == INDEX_SCHEMA, meta.get("schema")
    assert isinstance(meta.get("apiVersion"), str) and meta["apiVersion"].count(".") >= 2, \
        meta.get("apiVersion")
    assert isinstance(meta.get("missing"), list), type(meta.get("missing")).__name__


def test_index_lists_exactly_thirty_agents():
    root = _require_assets()
    index = load_index(root)
    agents = index_agents(index)
    assert len(agents) == EXPECTED_AGENTS, "index has %d non-_meta keys: %s" % (
        len(agents), sorted(agents))
    assert len(index) == EXPECTED_AGENTS + 1, "top-level keys: %s" % sorted(index)


def test_every_index_entry_carries_uuid_portrait_map_and_abilities():
    root = _require_assets()
    problems = entry_problems(load_index(root))
    assert not problems, problems


def test_index_paths_match_the_filename_contract():
    root = _require_assets()
    problems = path_contract_problems(load_index(root))
    assert not problems, problems


def test_every_agent_publishes_the_four_combat_ability_icons():
    root = _require_assets()
    index = load_index(root)
    problems = []
    for name, entry in index_agents(index).items():
        slots = set()
        for path in (entry.get("abilities") or {}).values():
            if isinstance(path, str):
                match = ABILITY_PATH_RE.match(path)
                if match:
                    slots.add(match.group(1))
        absent = [slot for slot in COMBAT_SLOTS if slot not in slots]
        if absent:
            problems.append("%s: no icon for %s" % (name, ", ".join(absent)))
    assert not problems, problems


def test_kay_o_is_uuid_keyed():
    """`KAY/O` cannot be a filename -- no published path may carry its letters."""
    root = _require_assets()
    index = load_index(root)
    assert "KAY/O" in index, "KAY/O missing from the index"
    entry = index["KAY/O"]
    stem = entry["uuid"].lower()
    paths = [path for _field, path in index_paths(entry)]
    assert len(paths) >= 6, paths                       # portrait + map + 4 combat icons
    for path in paths:
        # The display name's characters would break a path: "KAY/O" contains a
        # slash, and every part of a real path here is lowercase.
        assert "KAY" not in path.upper(), path
        assert "O" not in path, path
        assert stem in path, "%r is not keyed by %s" % (path, stem)


def test_every_index_path_resolves_unless_declared_missing():
    root = _require_assets()
    problems = unresolved_problems(root, load_index(root))
    assert not problems, problems


# ---------------------------------------------------------------------------
# 4. missing[] must match reality
# ---------------------------------------------------------------------------

def test_missing_rows_name_real_agents_and_real_gaps():
    root = _require_assets()
    problems = missing_row_problems(root, load_index(root))
    assert not problems, problems


# ---------------------------------------------------------------------------
# 5. the fetcher is importable and agrees with the contract
# ---------------------------------------------------------------------------

def test_fetcher_module_exposes_the_transform_constants():
    """Import only -- no network. The constants are what this test file pins."""
    if _REPO_ROOT not in sys.path:
        sys.path.insert(0, _REPO_ROOT)
    try:
        module = importlib.import_module("tools.fetch_agent_assets")
    except (ImportError, OSError) as exc:      # Pillow missing on a bare checkout
        pytest.skip("tools.fetch_agent_assets cannot be imported without its "
                    "dependencies (%s: %s)" % (type(exc).__name__, exc))
        raise                                  # unreachable: pytest.skip raises

    assert module.INDEX_SCHEMA == INDEX_SCHEMA, module.INDEX_SCHEMA
    assert (module.MAP_EDGE, module.PORTRAIT_EDGE) == (MAP_EDGE[0], PORTRAIT_EDGE[0])
    assert module.ABILITY_MAX == ABILITY_MAX_EDGE, module.ABILITY_MAX
    assert tuple(module.COMBAT_SLOTS) == COMBAT_SLOTS, module.COMBAT_SLOTS
    assert tuple(module.ALL_SLOTS) == ALL_SLOTS, module.ALL_SLOTS
    for transform in (module.T_MAP, module.T_PORTRAIT, module.T_ABILITY):
        assert transform in module.TRANSFORMS, sorted(module.TRANSFORMS)
        assert callable(module.TRANSFORMS[transform]), transform

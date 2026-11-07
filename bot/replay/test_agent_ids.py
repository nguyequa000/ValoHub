#!/usr/bin/env python3
"""Tests for ``bot/replay/agent_ids.json`` and the ``agentUuid`` column it feeds.

P8-B1 (plan decision D9): ``normalize.py`` used to hard-code ``"agentUuid": None``
for every player. The map now comes from ``agent_ids.json``, which
``tools/fetch_agent_assets.py`` writes from
``GET https://valorant-api.com/v1/agents?isPlayableCharacter=true``.

Runs two ways::

    py -3.12 -m pytest bot/replay/test_agent_ids.py -q
    py -3.12 bot/replay/test_agent_ids.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import uuid as uuidlib

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

IDS_PATH = os.path.join(_HERE, "agent_ids.json")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
EXPECTED_AGENTS = 30          # playable roster at patch 13.09 (Riot, 2026-10-31)


def _load(module_name, filename):
    spec = importlib.util.spec_from_file_location(module_name, os.path.join(_HERE, filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


normalize = _load("valoplant_normalize_agent_ids", "normalize.py")
# Reuse the synthetic raw-export builder from test_normalize.py instead of keeping
# a second copy of the fixture in sync.
fixture = _load("valoplant_agent_ids_fixture_builder", "test_normalize.py")


def read_ids():
    with open(IDS_PATH, "r", encoding="utf-8") as handle:
        return json.load(handle)


# ---------------------------------------------------------------------------
# the file itself
# ---------------------------------------------------------------------------

def test_ids_file_shape():
    ids = read_ids()
    assert ids["schema"] == "valoplant_agent_ids/v1", ids["schema"]
    assert isinstance(ids["apiVersion"], str) and ids["apiVersion"].count(".") >= 2, \
        ids["apiVersion"]
    assert len(ids["byName"]) == EXPECTED_AGENTS, sorted(ids["byName"])
    assert len(ids["byUuid"]) == EXPECTED_AGENTS, len(ids["byUuid"])


def test_uuids_are_lowercase_8_4_4_4_12_hex():
    ids = read_ids()
    bad = {name: value for name, value in ids["byName"].items()
           if not (isinstance(value, str) and UUID_RE.match(value))}
    assert not bad, bad
    upper = {name: value for name, value in ids["byName"].items() if value != value.lower()}
    assert not upper, upper
    for value in ids["byName"].values():
        assert str(uuidlib.UUID(value)) == value, value       # parses and round-trips


def test_by_uuid_is_the_inverse_of_by_name():
    ids = read_ids()
    assert {value: name for name, value in ids["byName"].items()} == ids["byUuid"]


def test_names_are_unique_and_include_the_slash_name():
    ids = read_ids()
    assert len(set(ids["byName"])) == EXPECTED_AGENTS
    # KAY/O is the reason files are keyed by uuid, not by name.
    assert ids["byName"]["KAY/O"] == "601dbbe7-43ce-be57-2a40-4abd24953621"
    assert ids["byUuid"]["601dbbe7-43ce-be57-2a40-4abd24953621"] == "KAY/O"


def test_every_codename_in_the_normalizer_has_an_id():
    ids = read_ids()
    missing = sorted({name for name in normalize.AGENT_BY_CODENAME.values()
                      if name not in ids["byName"]})
    assert not missing, missing


def test_paprika_is_hush():
    # 13.09 added Hush; the decoder calls it Paprika
    # (VALOPLANT_WORK/pu/a/091257-…/Descriptors/Agents/Paprika/PaprikaAgentDescriptor.cs,
    # documented in the roster snapshot).
    assert normalize.AGENT_BY_CODENAME.get("Paprika") == "Hush"
    assert "Hush" in read_ids()["byName"]


def test_module_constant_matches_the_file_on_disk():
    ids = read_ids()
    assert normalize.AGENT_UUID_BY_NAME == {name: value.lower()
                                            for name, value in ids["byName"].items()}


def test_loader_is_soft_when_the_file_is_absent():
    """A checkout without agent_ids.json must keep agentUuid null, not crash."""
    import shutil
    import tempfile

    temp = tempfile.mkdtemp(prefix="valohub-no-ids-")
    saved_path = list(sys.path)
    try:
        for name in ("normalize.py", "utility_track.py", "ability_catalog.json"):
            shutil.copy(os.path.join(_HERE, name), os.path.join(temp, name))
        assert not os.path.exists(os.path.join(temp, "agent_ids.json"))
        spec = importlib.util.spec_from_file_location(
            "valoplant_normalize_without_ids", os.path.join(temp, "normalize.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)          # must not raise
        assert module.AGENT_IDS == {}
        assert module.AGENT_UUID_BY_NAME == {}
        assert module.AGENT_BY_CODENAME["Wushu"] == "Jett"      # rest still loaded
    finally:
        sys.path[:] = saved_path
        shutil.rmtree(temp, ignore_errors=True)
    # ...and this checkout does have the file, so the real module resolves names.
    assert normalize.AGENT_UUID_BY_NAME.get("Jett") == "add6443a-41bd-e414-f6ad-e58d267f4e95"


# ---------------------------------------------------------------------------
# what the normalizer emits
# ---------------------------------------------------------------------------

def test_normalize_fills_agent_uuid_for_a_known_agent():
    _raw, viewer, _stats = fixture.make_case("agent-ids-known")
    match = fixture.read_json(os.path.join(viewer, "match.json"))
    ids = read_ids()
    players = match["players"]
    assert len(players) == 10
    # Key set and order are pinned by test_normalize.py:467 -- unchanged here.
    assert list(players[0].keys()) == ["id", "name", "agent", "agentUuid", "team",
                                       "subject", "confidence"]
    assert all(player["agent"] for player in players)
    assert all(player["agentUuid"] for player in players), \
        [(p["agent"], p["agentUuid"]) for p in players]
    for player in players:
        assert player["agentUuid"] == ids["byName"][player["agent"]], (
            player["agent"], player["agentUuid"])
    assert players[0]["agent"] == "Omen"
    assert players[0]["agentUuid"] == "8e253930-4c05-31dd-1b6c-968525494517"


def test_normalize_leaves_agent_uuid_null_for_an_unknown_agent():
    codename = "Wushu"                     # the fixture's P6, normally Jett
    saved = fixture.normalize.AGENT_BY_CODENAME.pop(codename)
    try:
        _raw, viewer, _stats = fixture.make_case("agent-ids-unknown")
    finally:
        fixture.normalize.AGENT_BY_CODENAME[codename] = saved
    match = fixture.read_json(os.path.join(viewer, "match.json"))
    player = match["players"][5]
    assert player["agent"] is None, player
    assert player["agentUuid"] is None, player
    # the other nine still resolve
    assert all(item["agentUuid"] for index, item in enumerate(match["players"]) if index != 5)


if __name__ == "__main__":
    failures = 0
    for name, func in sorted(globals().items()):
        if not name.startswith("test_") or not callable(func):
            continue
        try:
            func()
        except AssertionError as exc:
            failures += 1
            print("FAIL %s: %s" % (name, exc))
        else:
            print("ok   %s" % name)
    print("%d failure(s)" % failures)
    sys.exit(1 if failures else 0)

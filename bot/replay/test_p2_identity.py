#!/usr/bin/env python3
"""Tests for the HenrikDev identity join (``bot/replay/p2_identity.py``).

Runs two ways:

    py -3.12 -m pytest bot/replay/test_p2_identity.py -q
    py -3.12 bot/replay/test_p2_identity.py

All HTTP in the hermetic tests is served by a stdlib ``ThreadingHTTPServer``
mock HenrikDev instance on 127.0.0.1; the app's client is pointed at it via
``VALO_HENRIK_BASE_URL``. One optional test makes at most ONE call to the real
HenrikDev API when a token is available, and records the HTTP status code only
— the token value is never printed (only a non-secret fake token appears in
mock assertions).
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import aiohttp  # noqa: E402
from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

import bot.replay.p2_identity as identity  # noqa: E402
from bot.replay import store as replay_store  # noqa: E402

TMP_ROOT = _REPO_ROOT / "VALOPLANT_WORK" / "tmp" / "identity-tests"
FAKE_TOKEN = "test-token-1234567890"          # hermetic; the real one never appears
FIXED_EPOCH = 1750000000.0                    # arbitrary fixed match start
FIXED_ISO = datetime.fromtimestamp(FIXED_EPOCH, timezone.utc).isoformat()

# Real Haven replay (bad1543033d11396) roster; two Sovas, one per team.
TEAM0 = ["Sova", "Jett", "Omen", "Chamber", "Phoenix"]
TEAM1 = ["Sova", "Clove", "Jett", "Phoenix", "Cypher"]

MATCH_ENVELOPE_KEYS = {"available", "reason", "refreshedAt", "players"}
PLAYER_KEYS = {"slot", "displayName", "riotId", "puuid", "confidence", "source"}


# --------------------------------------------------------------------------
# helpers: assert shapes, fixtures, env, temp store
# --------------------------------------------------------------------------

def _assert_envelope_shape(payload):
    assert set(payload) == MATCH_ENVELOPE_KEYS, sorted(payload)
    assert isinstance(payload["available"], bool)
    assert payload["reason"] is None or isinstance(payload["reason"], str)
    assert isinstance(payload["refreshedAt"], str) and payload["refreshedAt"]
    assert isinstance(payload["players"], list)
    for player in payload["players"]:
        assert set(player) == PLAYER_KEYS, sorted(player)
        assert player["confidence"] in ("matched", "unmatched")
        assert player["source"] in ("henrikdev", "header")


def _slot_map(payload):
    return {p["slot"]: p for p in payload["players"]}


def _default_players():
    players = []
    for index, agent in enumerate(TEAM0, start=1):
        players.append({"id": "P%d" % index, "name": None, "agent": agent, "team": 0,
                        "subject": None, "confidence": "loadout"})
    for index, agent in enumerate(TEAM1, start=6):
        players.append({"id": "P%d" % index, "name": None, "agent": agent, "team": 1,
                        "subject": None, "confidence": "loadout"})
    return players


def _write_replay(root, rid, *, players=None, map_name="Haven",
                  map_path="/Game/Maps/Triad/Triad", source=None, write_match=True):
    rdir = root / "replays" / rid
    (rdir / "viewer").mkdir(parents=True, exist_ok=True)
    if write_match:
        match = {"schema": "valoplant_match/v1", "id": rid,
                 "map": {"name": map_name, "path": map_path},
                 "players": players if players is not None else _default_players(),
                 "rounds": []}
        (rdir / "viewer" / "match.json").write_text(json.dumps(match), encoding="utf-8")
    manifest = {"schema": "valoplant_manifest/v1", "id": rid, "status": "ready"}
    if source:
        manifest["source"] = source
    (rdir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return rdir


class _TempStore:
    """Point bot.replay.store at a fresh temp replays root for one test."""

    def __init__(self, name):
        self.root = TMP_ROOT / "store" / name
        shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir(parents=True, exist_ok=True)

    def __enter__(self):
        self._saved = (replay_store.DATA_REPLAYS_DIR, replay_store.ROOT)
        replay_store.DATA_REPLAYS_DIR = self.root / "replays"
        replay_store.ROOT = self.root
        return self

    def __exit__(self, *exc):
        replay_store.DATA_REPLAYS_DIR, replay_store.ROOT = self._saved
        return False


class _Env:
    """Set env vars for one test; None removes the var."""

    _MISSING = object()

    def __init__(self, **values):
        self.values = values
        self.saved = {}

    def __enter__(self):
        for key, value in self.values.items():
            self.saved[key] = os.environ.get(key, self._MISSING)
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        return self

    def __exit__(self, *exc):
        for key, value in self.saved.items():
            if value is self._MISSING:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        return False


def _standard_env(port, **extra):
    env = {
        "VALO_OFFLINE": None,
        "VALO_IDENTITY_SEED": "Probe#NA1",
        "VALO_IDENTITY_TIME": FIXED_ISO,
        "VALO_HENRIK_BASE_URL": "http://127.0.0.1:%d" % port,
        "VALO_HENRIK_REGION": "na",
    }
    env.update(extra)
    return env


# --------------------------------------------------------------------------
# mock HenrikDev server (stdlib threading HTTP server)
# --------------------------------------------------------------------------

def _henrik_player(puuid, name, tag, team, character):
    return {"puuid": puuid, "name": name, "tag": tag, "team": team, "character": character}


def _henrik_match(game_start=FIXED_EPOCH, map_name="Haven", team0=TEAM0, team1=TEAM1):
    players = []
    for index, character in enumerate(team0):
        players.append(_henrik_player("puuid-red-%d" % index, "Red%d" % (index + 1),
                                      "NA1", "Red", character))
    for index, character in enumerate(team1):
        players.append(_henrik_player("puuid-blue-%d" % index, "Blue%d" % (index + 1),
                                      "NA1", "Blue", character))
    return {
        "metadata": {"map": map_name, "game_start": game_start, "matchid": "mock-match-0001",
                     "mode": "Custom", "queue": "Custom", "rounds_played": 18},
        "players": {"all_players": players},
        "teams": {"red": {"rounds_won": 13}, "blue": {"rounds_won": 5}},
    }


class _MockHenrik:
    """Threaded stdlib HTTP server emulating the HenrikDev v3 matches endpoint."""

    def __init__(self, match=None):
        self.match = match if match is not None else _henrik_match()
        self.requests = []          # [(path, authorization_header_value_or_None)]
        mock = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):  # noqa: A002 — must match the base signature
                pass

            def _send(self, status, payload):
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                mock.requests.append((self.path, self.headers.get("Authorization")))
                parts = unquote(urlsplit(self.path).path).strip("/").split("/")
                tag = parts[-1] if parts else ""
                if tag == "FAIL":
                    return self._send(404, {"status": 404, "errors": [{"message": "not found"}]})
                if tag == "RATE":
                    return self._send(429, {"status": 429, "errors": [{"message": "rate limited"}]})
                if tag == "EMPTY":
                    return self._send(200, {"status": 200, "data": []})
                return self._send(200, {"status": 200, "data": [mock.match]})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


# --------------------------------------------------------------------------
# pure-logic tests
# --------------------------------------------------------------------------

def test_split_riot_id():
    assert identity._split_riot_id("Name#TAG") == ("Name", "TAG")
    assert identity._split_riot_id("A B#XY1") == ("A B", "XY1")
    assert identity._split_riot_id("NoTag") is None
    assert identity._split_riot_id("#TAG") is None
    assert identity._split_riot_id("Name#") is None
    assert identity._split_riot_id(None) is None


def test_join_team_pairing_direct_and_swapped():
    base = identity._base_players({"players": _default_players()})
    henrik = _henrik_match()["players"]["all_players"]
    joined = identity._join_players(base, henrik)
    assert set(joined) == {"P%d" % n for n in range(1, 11)}
    assert joined["P1"]["name"] == "Red1" and joined["P1"]["character"] == "Sova"
    assert joined["P1"]["puuid"] == "puuid-red-0"
    assert joined["P6"]["name"] == "Blue1" and joined["P6"]["team"] == "Blue"
    assert joined["P6"]["puuid"] == "puuid-blue-0"

    swapped_players = []
    for entry in _henrik_match()["players"]["all_players"]:
        flipped = dict(entry)
        flipped["team"] = "Blue" if entry["team"] == "Red" else "Red"
        swapped_players.append(flipped)
    joined_swapped = identity._join_players(base, swapped_players)
    assert set(joined_swapped) == {"P%d" % n for n in range(1, 11)}
    # same humans (puuid) matched through the flipped pairing
    assert joined_swapped["P1"]["puuid"] == "puuid-red-0"
    assert joined_swapped["P1"]["team"] == "Blue"
    assert joined_swapped["P6"]["puuid"] == "puuid-blue-0"
    assert joined_swapped["P6"]["team"] == "Red"


def test_join_ambiguous_teams_match_nothing():
    same = ["Sova", "Jett", "Omen", "Chamber", "Phoenix"]
    base = identity._base_players({"players": [
        {"id": "P%d" % n, "name": None, "agent": same[(n - 1) % 5], "team": 0 if n <= 5 else 1}
        for n in range(1, 11)]})
    henrik = _henrik_match(team0=same, team1=same)["players"]["all_players"]
    assert identity._join_players(base, henrik) == {}


def test_join_duplicate_agent_within_team_blocks_only_that_slot():
    team0 = ["Jett", "Jett", "Omen", "Chamber", "Phoenix"]   # P1/P2 now share Jett
    players = _default_players()
    for index, agent in enumerate(team0):
        players[index]["agent"] = agent
    base = identity._base_players({"players": players})
    joined = identity._join_players(base, _henrik_match(team0=team0)["players"]["all_players"])
    assert "P1" not in joined and "P2" not in joined          # two Jetts -> ambiguous
    assert joined["P3"]["name"] == "Red3"
    assert joined["P6"]["name"] == "Blue1"                    # team 1 untouched


def test_join_name_conflict_vetoes_only_conflicting_slot():
    players = _default_players()
    players[0]["name"] = "SomeoneElse"    # disagrees with Red1 -> veto P1
    players[2]["name"] = "Red3"           # agrees with Red3 -> still matched
    base = identity._base_players({"players": players})
    joined = identity._join_players(base, _henrik_match()["players"]["all_players"])
    assert "P1" not in joined
    assert joined["P3"]["name"] == "Red3"


def test_find_candidate_window_map_alias_and_closest():
    aliases = {"haven", "triad"}
    far = _henrik_match(game_start=FIXED_EPOCH - 3600)
    near_a = _henrik_match(game_start=FIXED_EPOCH + 600)
    near_b = _henrik_match(game_start=FIXED_EPOCH - 300)     # closest in-window
    wrong_map = _henrik_match(game_start=FIXED_EPOCH, map_name="Sunset")
    no_time = {"metadata": {"map": "Triad"}}
    best, checked = identity._find_candidate(
        [far, near_b, wrong_map, no_time, near_a], aliases, FIXED_EPOCH)
    assert best is near_b
    assert checked == 3                                       # far, near_a, near_b

    best2, checked2 = identity._find_candidate([far], aliases, FIXED_EPOCH)
    assert best2 is None and checked2 == 1

    best3, checked3 = identity._find_candidate([wrong_map], aliases, FIXED_EPOCH)
    assert best3 is None and checked3 == 0


def test_map_aliases_include_folder_alias():
    match = {"map": {"name": "Haven", "path": "/Game/Maps/Triad/Triad"}}
    assert identity._map_aliases(match, {}) == {"haven", "triad"}
    sunset = {"map": {"name": "Sunset", "path": "/Game/Maps/Juliett/Juliett"}}
    assert identity._map_aliases(sunset, {}) == {"sunset", "juliett"}


# --------------------------------------------------------------------------
# compute_identity unavailable paths (direct calls, temp store)
# --------------------------------------------------------------------------

def test_compute_unknown_replay_and_invalid_id():
    with _TempStore("compute-unknown") as tmp:
        with _Env(**{"VALO_OFFLINE": None}):
            unknown = asyncio.run(identity.compute_identity("nope-nope", token=FAKE_TOKEN))
            invalid = asyncio.run(identity.compute_identity("..", token=FAKE_TOKEN))
    _assert_envelope_shape(unknown)
    assert unknown["available"] is False and "not found" in unknown["reason"]
    assert unknown["players"] == []
    _assert_envelope_shape(invalid)
    assert invalid["available"] is False and "invalid replay id" in invalid["reason"]
    assert tmp.root.exists()


def test_compute_no_match_data_reason():
    with _TempStore("compute-no-match") as tmp:
        _write_replay(tmp.root, "fx-failed", write_match=False)
        with _Env(**{"VALO_OFFLINE": None}):
            payload = asyncio.run(identity.compute_identity("fx-failed", token=FAKE_TOKEN))
    _assert_envelope_shape(payload)
    assert payload["available"] is False
    assert "no normalized match data" in payload["reason"]
    assert payload["players"] == []


def test_compute_offline_and_token_paths():
    with _TempStore("compute-offline-token") as tmp:
        _write_replay(tmp.root, "fx-off")
        with _Env(**{"VALO_OFFLINE": "1"}):
            offline = asyncio.run(identity.compute_identity("fx-off", token=FAKE_TOKEN))
        with _Env(**{"VALO_OFFLINE": None}):
            no_token = asyncio.run(identity.compute_identity("fx-off", token=""))
    _assert_envelope_shape(offline)
    assert offline["available"] is False and offline["reason"] == "offline mode"
    assert [p["slot"] for p in offline["players"]] == ["P%d" % n for n in range(1, 11)]
    assert all(p["confidence"] == "unmatched" and p["source"] == "header"
               and p["riotId"] is None for p in offline["players"])
    _assert_envelope_shape(no_token)
    assert no_token["available"] is False
    assert "token not configured" in no_token["reason"]


def test_compute_no_seed_reason():
    with _TempStore("compute-no-seed") as tmp:
        _write_replay(tmp.root, "fx-noseed")
        with _Env(**{"VALO_OFFLINE": None, "VALO_IDENTITY_SEED": None}):
            payload = asyncio.run(identity.compute_identity("fx-noseed", token=FAKE_TOKEN))
    _assert_envelope_shape(payload)
    assert payload["available"] is False
    assert "no joinable Riot ID seed" in payload["reason"]
    assert len(payload["players"]) == 10


def test_compute_anchor_missing_reason_makes_no_call():
    mock = _MockHenrik().start()
    try:
        with _TempStore("compute-no-anchor") as tmp:
            _write_replay(tmp.root, "fx-noanchor")        # no manifest source
            with _Env(**{"VALO_OFFLINE": None, "VALO_IDENTITY_SEED": "Probe#NA1",
                         "VALO_IDENTITY_TIME": None,
                         "VALO_HENRIK_BASE_URL": "http://127.0.0.1:%d" % mock.port}):
                payload = asyncio.run(identity.compute_identity("fx-noanchor", token=FAKE_TOKEN))
        _assert_envelope_shape(payload)
        assert payload["available"] is False
        assert "start time unknown" in payload["reason"]
        assert mock.requests == []                        # nothing was queried
    finally:
        mock.stop()


# --------------------------------------------------------------------------
# compute_identity against the mock server
# --------------------------------------------------------------------------

def test_compute_full_join_mock_server():
    mock = _MockHenrik().start()
    try:
        with _TempStore("compute-join") as tmp:
            rdir = _write_replay(tmp.root, "fx-join")
            match_bytes = (rdir / "viewer" / "match.json").read_bytes()
            with _Env(**_standard_env(mock.port)):
                payload = asyncio.run(identity.compute_identity("fx-join", token=FAKE_TOKEN))
            assert (rdir / "viewer" / "match.json").read_bytes() == match_bytes
        _assert_envelope_shape(payload)
        assert payload["available"] is True and payload["reason"] is None
        slots = _slot_map(payload)
        assert slots["P1"]["riotId"] == "Red1#NA1" and slots["P1"]["puuid"] == "puuid-red-0"
        assert slots["P2"]["riotId"] == "Red2#NA1"
        assert slots["P6"]["riotId"] == "Blue1#NA1" and slots["P6"]["puuid"] == "puuid-blue-0"
        assert slots["P10"]["riotId"] == "Blue5#NA1"
        assert all(p["confidence"] == "matched" and p["source"] == "henrikdev"
                   for p in payload["players"])
        assert len(mock.requests) == 1
        assert mock.requests[0][1] == FAKE_TOKEN          # auth header was sent
    finally:
        mock.stop()


def test_compute_time_window_miss_and_wrong_map():
    mock = _MockHenrik().start()
    try:
        with _TempStore("compute-window") as tmp:
            _write_replay(tmp.root, "fx-window")
            _write_replay(tmp.root, "fx-map", map_name="Sunset",
                          map_path="/Game/Maps/Juliett/Juliett")
            with _Env(**_standard_env(mock.port)):
                with _Env(VALO_IDENTITY_TIME=str(FIXED_EPOCH - 3600)):
                    window = asyncio.run(identity.compute_identity("fx-window", token=FAKE_TOKEN))
                wrong_map = asyncio.run(identity.compute_identity("fx-map", token=FAKE_TOKEN))
        _assert_envelope_shape(window)
        assert window["available"] is False
        assert "no custom match on Haven within" in window["reason"]
        assert "1 returned, 1 time-checked" in window["reason"]
        _assert_envelope_shape(wrong_map)
        assert wrong_map["available"] is False
        assert "no custom match on Sunset within" in wrong_map["reason"]
        assert "1 returned, 0 time-checked" in wrong_map["reason"]
    finally:
        mock.stop()


def test_compute_request_failure_and_empty_reasons():
    mock = _MockHenrik().start()
    try:
        with _TempStore("compute-failures") as tmp:
            _write_replay(tmp.root, "fx-fail")
            _write_replay(tmp.root, "fx-empty")
            with _Env(**_standard_env(mock.port, VALO_IDENTITY_SEED="Probe#FAIL")):
                failed = asyncio.run(identity.compute_identity("fx-fail", token=FAKE_TOKEN))
            with _Env(**_standard_env(mock.port, VALO_IDENTITY_SEED="Probe#EMPTY")):
                empty = asyncio.run(identity.compute_identity("fx-empty", token=FAKE_TOKEN))
        _assert_envelope_shape(failed)
        assert failed["available"] is False
        assert "henrikdev request failed" in failed["reason"] and "404" in failed["reason"]
        assert "Authorization" not in failed["reason"]
        _assert_envelope_shape(empty)
        assert empty["available"] is False
        assert "no custom matches returned" in empty["reason"]
    finally:
        mock.stop()


def test_compute_anchor_from_source_vrf_mtime():
    mock = _MockHenrik().start()
    try:
        with _TempStore("compute-mtime") as tmp:
            _write_replay(tmp.root, "fx-mtime", source="replays/sample.vrf")
            vrf = tmp.root / "replays" / "sample.vrf"
            vrf.write_bytes(b"not a real replay")
            os.utime(vrf, (FIXED_EPOCH, FIXED_EPOCH))
            with _Env(**_standard_env(mock.port, VALO_IDENTITY_TIME=None)):
                payload = asyncio.run(identity.compute_identity("fx-mtime", token=FAKE_TOKEN))
        _assert_envelope_shape(payload)
        assert payload["available"] is True
        assert _slot_map(payload)["P1"]["riotId"] == "Red1#NA1"
    finally:
        mock.stop()


# --------------------------------------------------------------------------
# endpoints (mini aiohttp app + TestClient)
# --------------------------------------------------------------------------

async def _endpoint_flow(rid, *, token):
    app = web.Application()
    app["henrik_token"] = token
    app["session"] = None
    identity.mount(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        first = await client.get("/api/replays/%s/identity" % rid)
        first_body = await first.json()
        second = await client.get("/api/replays/%s/identity" % rid)
        second_body = await second.json()
        refreshed = await client.post("/api/replays/%s/identity/refresh" % rid)
        refreshed_body = await refreshed.json()
        after = await client.get("/api/replays/%s/identity" % rid)
        after_body = await after.json()
        return {
            "statuses": [first.status, second.status, refreshed.status, after.status],
            "first": first_body, "second": second_body,
            "refreshed": refreshed_body, "after": after_body,
        }
    finally:
        await client.close()


def test_endpoint_get_cache_refresh_roundtrip():
    mock = _MockHenrik().start()
    try:
        with _TempStore("endpoint-roundtrip") as tmp:
            rdir = _write_replay(tmp.root, "fx-roundtrip")
            with _Env(**_standard_env(mock.port)):
                out = asyncio.run(_endpoint_flow("fx-roundtrip", token=FAKE_TOKEN))
            cache = rdir / "viewer" / "identity.json"
            written = json.loads(cache.read_text(encoding="utf-8")) if cache.is_file() else None
            leftovers = [p.name for p in (rdir / "viewer").iterdir() if p.name.endswith(".tmp")]
        assert out["statuses"] == [200, 200, 200, 200]
        for payload in (out["first"], out["second"], out["refreshed"], out["after"]):
            _assert_envelope_shape(payload)
        assert out["first"]["available"] is True
        assert out["second"] == out["first"]              # served from cache
        assert out["after"]["refreshedAt"] == out["first"]["refreshedAt"]
        assert written is not None and written["available"] is True
        assert leftovers == []                            # atomic write, no .tmp
        assert len(mock.requests) == 2                    # compute + refresh only
    finally:
        mock.stop()


def test_endpoint_always_200_for_all_failure_paths():
    mock = _MockHenrik().start()
    try:
        with _TempStore("endpoint-200s") as tmp:
            _write_replay(tmp.root, "fx-token")
            _write_replay(tmp.root, "fx-offline")
            _write_replay(tmp.root, "fx-refused")
            with _Env(**_standard_env(mock.port)):
                no_token = asyncio.run(_endpoint_flow("fx-token", token=""))
                unknown = asyncio.run(_endpoint_flow("fx-unknown", token=FAKE_TOKEN))
                with _Env(VALO_OFFLINE="1"):
                    offline = asyncio.run(_endpoint_flow("fx-offline", token=FAKE_TOKEN))
                with _Env(VALO_HENRIK_BASE_URL="http://127.0.0.1:9"):
                    refused = asyncio.run(_endpoint_flow("fx-refused", token=FAKE_TOKEN))
        for out in (no_token, unknown, offline, refused):
            assert out["statuses"] == [200, 200, 200, 200]
            _assert_envelope_shape(out["first"])
            _assert_envelope_shape(out["refreshed"])
            assert out["first"]["available"] is False
        assert "token not configured" in no_token["first"]["reason"]
        assert "not found" in unknown["first"]["reason"]
        assert offline["first"]["reason"] == "offline mode"
        assert "henrikdev request failed" in refused["first"]["reason"]
    finally:
        mock.stop()


# --------------------------------------------------------------------------
# gate fix (card t_9b6c2d71): env/upstream negatives are never cached or served
# --------------------------------------------------------------------------

def _cache_path(rdir):
    return rdir / "viewer" / "identity.json"


def _write_cache(rdir, payload):
    cache = _cache_path(rdir)
    cache.write_text(json.dumps(payload), encoding="utf-8")
    return cache


def _legacy_envelope(reason, *, extra=None):
    payload = {"available": False, "reason": reason,
               "refreshedAt": "2026-10-13T15:58:25+00:00", "players": []}
    if extra:
        payload.update(extra)
    return payload


def test_gate_offline_get_then_online_get_recomputes():
    mock = _MockHenrik().start()
    try:
        with _TempStore("gate-offline-then-online") as tmp:
            rdir = _write_replay(tmp.root, "fx-gate-off")
            with _Env(**_standard_env(mock.port, VALO_OFFLINE="1")):
                offline = asyncio.run(_endpoint_flow("fx-gate-off", token=FAKE_TOKEN))
            wrote_offline = _cache_path(rdir).is_file()
            with _Env(**_standard_env(mock.port)):
                online = asyncio.run(_endpoint_flow("fx-gate-off", token=FAKE_TOKEN))
            written = json.loads(_cache_path(rdir).read_text(encoding="utf-8"))
        assert offline["statuses"] == [200, 200, 200, 200]
        assert offline["first"]["reason"] == "offline mode"
        assert offline["refreshed"]["reason"] == "offline mode"
        assert wrote_offline is False                    # offline never persists
        assert online["statuses"] == [200, 200, 200, 200]
        assert online["first"]["available"] is True      # recomputed, not a cached offline
        assert online["first"]["reason"] != "offline mode"
        assert written["available"] is True
        assert len(mock.requests) == 2                   # 1 online compute + 1 refresh
    finally:
        mock.stop()


def test_gate_no_token_result_not_persisted():
    mock = _MockHenrik().start()
    try:
        with _TempStore("gate-no-token") as tmp:
            rdir = _write_replay(tmp.root, "fx-gate-token")
            with _Env(**_standard_env(mock.port)):
                out = asyncio.run(_endpoint_flow("fx-gate-token", token=""))
            persisted = _cache_path(rdir).is_file()
            with _Env(**_standard_env(mock.port)):
                recovered = asyncio.run(_endpoint_flow("fx-gate-token", token=FAKE_TOKEN))
        assert out["statuses"] == [200, 200, 200, 200]
        assert out["first"]["available"] is False
        assert "token not configured" in out["first"]["reason"]
        assert persisted is False                        # the negative was not written
        assert recovered["first"]["available"] is True   # a later boot still computes fresh
    finally:
        mock.stop()


def test_gate_offline_get_ignores_cached_negative():
    mock = _MockHenrik().start()
    try:
        with _TempStore("gate-offline-negative") as tmp:
            rdir = _write_replay(tmp.root, "fx-gate-off2")
            seeded = _legacy_envelope(
                "no joinable Riot ID seed in replay data (10 player(s), 0 display name(s), 0 with #TAG)")
            cache = _write_cache(rdir, seeded)
            before = cache.stat().st_mtime
            with _Env(**_standard_env(mock.port, VALO_OFFLINE="1")):
                out = asyncio.run(_endpoint_flow("fx-gate-off2", token=FAKE_TOKEN))
            on_disk = json.loads(cache.read_text(encoding="utf-8"))
            rewritten = cache.stat().st_mtime != before
        assert out["statuses"] == [200, 200, 200, 200]
        for payload in (out["first"], out["second"], out["refreshed"], out["after"]):
            assert payload["reason"] == "offline mode"
        assert on_disk == seeded                         # cache untouched (no write while offline)
        assert rewritten is False
        assert mock.requests == []                       # offline made no upstream call
    finally:
        mock.stop()


def test_gate_legacy_offline_cache_never_served():
    mock = _MockHenrik().start()
    try:
        with _TempStore("gate-legacy-offline") as tmp:
            rdir = _write_replay(tmp.root, "fx-gate-legacy")
            cache = _write_cache(rdir, _legacy_envelope("offline mode"))
            with _Env(**_standard_env(mock.port)):
                out = asyncio.run(_endpoint_flow("fx-gate-legacy", token=FAKE_TOKEN))
            healed = json.loads(cache.read_text(encoding="utf-8"))
        assert out["statuses"] == [200, 200, 200, 200]
        assert out["first"]["reason"] != "offline mode"  # poison cache ignored
        assert out["first"]["available"] is True         # recomputed against the upstream
        assert healed["available"] is True               # file self-heals on the next compute
        assert len(mock.requests) == 2
    finally:
        mock.stop()


def test_gate_transient_marker_cache_ignored():
    mock = _MockHenrik().start()
    try:
        with _TempStore("gate-marker") as tmp:
            rdir = _write_replay(tmp.root, "fx-gate-marker")
            cache = _write_cache(rdir, _legacy_envelope(
                "no custom matches returned for the seed(s)", extra={"transient": True}))
            with _Env(**_standard_env(mock.port)):
                out = asyncio.run(_endpoint_flow("fx-gate-marker", token=FAKE_TOKEN))
            healed = json.loads(cache.read_text(encoding="utf-8"))
        assert out["first"]["reason"] != "no custom matches returned for the seed(s)"
        assert out["first"]["available"] is True
        assert healed["available"] is True
    finally:
        mock.stop()


def test_gate_request_failure_not_persisted():
    mock = _MockHenrik().start()
    try:
        with _TempStore("gate-refused") as tmp:
            rdir = _write_replay(tmp.root, "fx-gate-refused")
            with _Env(**_standard_env(mock.port, VALO_HENRIK_BASE_URL="http://127.0.0.1:9")):
                out = asyncio.run(_endpoint_flow("fx-gate-refused", token=FAKE_TOKEN))
            persisted = _cache_path(rdir).is_file()
        assert out["statuses"] == [200, 200, 200, 200]
        assert "henrikdev request failed" in out["first"]["reason"]
        assert persisted is False                        # the negative was not written
    finally:
        mock.stop()


def test_gate_offline_serves_positive_cache_and_refresh_stays_offline():
    mock = _MockHenrik().start()
    try:
        with _TempStore("gate-offline-positive") as tmp:
            rdir = _write_replay(tmp.root, "fx-gate-pos")
            with _Env(**_standard_env(mock.port)):
                online = asyncio.run(_endpoint_flow("fx-gate-pos", token=FAKE_TOKEN))
            requests_after_online = len(mock.requests)
            with _Env(**_standard_env(mock.port, VALO_OFFLINE="1")):
                offline = asyncio.run(_endpoint_flow("fx-gate-pos", token=FAKE_TOKEN))
            on_disk = json.loads(_cache_path(rdir).read_text(encoding="utf-8"))
        assert online["first"]["available"] is True
        assert offline["first"]["available"] is True     # positive cache still served offline
        assert offline["first"]["reason"] is None
        assert offline["refreshed"]["reason"] == "offline mode"   # forced refresh honours it
        assert on_disk["available"] is True              # offline never clobbers the cache
        assert len(mock.requests) == requests_after_online        # no upstream call offline
    finally:
        mock.stop()


# --------------------------------------------------------------------------
# optional real API call (status code only — ONE call, never the token)
# --------------------------------------------------------------------------

def _token_from_dotenv():
    try:
        from dotenv import dotenv_values
    except Exception:  # noqa: BLE001
        return None
    values = dotenv_values(_REPO_ROOT / "bot" / ".env")
    token = values.get("HENRIKDEV_TOKEN")
    return token.strip() if isinstance(token, str) and token.strip() else None


async def _probe_real_henrikdev(token):
    url = ("https://api.henrikdev.xyz/valorant/v3/matches/na/"
           "ValoHubIdentityProbe/TEST?filter=custom&size=1")
    headers = {"Authorization": token, "User-Agent": "AntiStratsBot/1.0"}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            async with session.get(url, headers=headers) as response:
                return response.status
    except Exception as exc:  # noqa: BLE001 — record the failure kind, not the token
        return "network error: %s" % type(exc).__name__


def test_real_henrikdev_one_call_status_only():
    token = os.environ.get("HENRIKDEV_TOKEN") or _token_from_dotenv()
    if not token:
        sys.stdout.write("skipped: no HENRIKDEV_TOKEN available\n")
        return
    status = asyncio.run(_probe_real_henrikdev(token))
    sys.stdout.write("real HenrikDev v3 call: status=%s (token value not recorded)\n" % (status,))
    assert status == "network error: ClientConnectorError" or isinstance(status, int)


# --------------------------------------------------------------------------
# runner (plain mode; pytest also collects the test_ functions)
# --------------------------------------------------------------------------

def _tests():
    return [(name, value) for name, value in sorted(globals().items())
            if name.startswith("test_") and callable(value)]


def main(argv=None):
    shutil.rmtree(TMP_ROOT, ignore_errors=True)
    failures = 0
    tests = _tests()
    for name, function in tests:
        try:
            function()
        except Exception as exc:  # noqa: BLE001 - plain assert runner
            failures += 1
            sys.stdout.write("FAIL %s: %s: %s\n" % (name, type(exc).__name__, exc))
        else:
            sys.stdout.write("ok   %s\n" % name)
    shutil.rmtree(TMP_ROOT, ignore_errors=True)
    sys.stdout.write("%d test(s), %d failure(s)\n" % (len(tests), failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

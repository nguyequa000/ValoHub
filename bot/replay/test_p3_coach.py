#!/usr/bin/env python3
"""Tests for the P3 coach API (``bot/replay/p3_coach.py``, card P4-B).

Runs two ways:

    py -3.12 -m pytest bot/replay/test_p3_coach.py -q
    py -3.12 bot/replay/test_p3_coach.py

Hermetic: the LLM is the stdlib ``http.server`` stub from ``test_p2_review``
(ephemeral port, reached through ``VALO_LLM_URL``). Each test copies only the
Haven viewer match/rounds into a temporary store, without the user's me.json.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from bot.replay import coach_metrics, p2, p2_review, p3_coach, store  # noqa: E402
from bot.replay.test_p2_review import (  # noqa: E402
    REAL_REPLAY, MockLLM, env, free_port, no_llm_env,
)

GARBAGE = "I am a language model and I cannot produce JSON right now."


@contextmanager
def _isolated_store():
    """Redirect all replay lookups/writes to a fresh copy of required viewer data."""
    source = Path(_REPO_ROOT) / "data" / "replays" / REAL_REPLAY / "viewer"
    scratch = os.environ.get("HERMES_KANBAN_WORKSPACE") or os.environ.get("TMPDIR")
    with tempfile.TemporaryDirectory(prefix="p3_coach_", dir=scratch) as temp:
        replays = Path(temp) / "replays"
        viewer = replays / REAL_REPLAY / "viewer"
        rounds = viewer / "rounds"
        rounds.mkdir(parents=True)
        shutil.copyfile(source / "match.json", viewer / "match.json")
        for path in (source / "rounds").glob("r*.json"):
            shutil.copyfile(path, rounds / path.name)
        with mock.patch.object(store, "DATA_REPLAYS_DIR", replays):
            yield


@pytest.fixture(autouse=True)
def _pytest_isolated_store():
    with _isolated_store():
        yield


def _dead_env():
    """Every candidate points at a closed port -> rules-only responses, fast."""
    dead = f"http://127.0.0.1:{free_port()}/v1"
    return env(VALO_LLM_URL=dead, VALO_LMSTUDIO_URL=dead, VALO_OLLAMA_URL=dead)


# ------------------------------------------------------------------- helpers


def _sequence(*requests):
    """Run requests against ONE app instance (shares the in-memory cache)."""

    async def go():
        app = web.Application()
        p3_coach.mount(app)
        client = TestClient(TestServer(app))
        await client.start_server()
        results = []
        try:
            for method, path, body in requests:
                if method == "PUT":
                    response = await client.put(path, json=body)
                elif method == "POST":
                    response = await client.post(path, json=body if body is not None else None)
                else:
                    response = await client.get(path)
                results.append((response.status, await response.json()))
        finally:
            await client.close()
        return results

    return asyncio.run(go())


def _roundtrip(method: str, path: str, body=None):
    (result,) = _sequence((method, path, body))
    return result


class _MeBackup:
    """Preserve (or absent-ness of) the temporary viewer/me.json in a test."""

    def __init__(self, replay_id=REAL_REPLAY):
        self.path = store.replay_dir(replay_id) / "viewer" / "me.json"
        self.saved = self.path.read_bytes() if self.path.is_file() else None

    def restore(self):
        if self.saved is None:
            self.path.unlink(missing_ok=True)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_bytes(self.saved)


def _real_refs(replay_id=REAL_REPLAY, slot="P10"):
    """(report, every rules ref) for one real slot — the narration's allowed refs."""
    report = coach_metrics.player_report(store.replay_dir(replay_id), slot)
    assert report.get("findings"), "the real store must produce findings"
    refs = [ref for finding in report["findings"] for ref in finding["refs"]]
    assert refs
    return report, refs


def _assert_all_refs_grounded(body: dict):
    """Every narration ref must exist in the rules findings; every rules ref must
    resolve against the store (the P4-G contract, at API level)."""
    allowed = p3_coach.allowed_refs((body.get("rules") or {}).get("findings"))
    narration = body.get("narration") or {}
    for kind in ("mistakes", "drills"):
        for item in narration.get(kind) or []:
            assert item.get("refs"), "a kept item must cite at least one ref"
            for ref in item["refs"]:
                assert (ref["round"], ref["t"]) in allowed, ("ungrounded ref kept", kind, ref)
    data = coach_metrics.load_store(store.replay_dir(body["replayId"] if "replayId" in body else REAL_REPLAY))
    assert data is not None
    for finding in (body.get("rules") or {}).get("findings") or []:
        for ref in finding.get("refs") or []:
            assert coach_metrics.resolve_ref(data["match"], data["rounds"], ref), ref


# -------------------------------------------------------------- parsing unit


def test_parse_narration_defensive():
    good = {"summary": "Work on trades.", "mistakes": [
        {"title": "m1", "detail": "d", "severity": "MAJOR", "refs": [{"round": 5, "t": 48200}]},
    ], "drills": [{"title": "dr", "detail": "", "refs": [{"round": 1, "t": 300}]}]}
    for payload in (json.dumps(good), "Here:\n```json\n" + json.dumps(good) + "\n```\nBye.",
                    "prose " + json.dumps(good) + " tail"):
        parsed = p3_coach.parse_narration(payload)
        assert parsed is not None, payload[:40]
        assert parsed["summary"] == "Work on trades."
        assert parsed["mistakes"][0]["severity"] == "major"
        assert parsed["mistakes"][0]["refs"] == [{"round": 5, "t": 48200, "tSec": 48.2}]
    assert p3_coach.parse_narration(GARBAGE) is None
    assert p3_coach.parse_narration("") is None
    assert p3_coach.parse_narration('{"mistakes": []}') is None  # no summary
    # structural filtering: non-dict, no-title items vanish; refs without round/t vanish
    parsed = p3_coach.parse_narration(json.dumps({
        "summary": "s", "mistakes": ["nope", {"detail": "no title"},
                                     {"title": "ok", "refs": ["x", {"round": "7", "tMs": "300"}]}],
        "drills": None}))
    assert parsed is not None
    assert len(parsed["mistakes"]) == 1
    assert parsed["mistakes"][0]["refs"] == [{"round": 7, "t": 300, "tSec": 0.3}]
    assert parsed["drills"] == []


def test_validate_narration_grounding_and_counts():
    allowed = {(5, 48200), (18, 92713)}
    narration = {
        "summary": "s",
        "mistakes": [
            {"title": "grounded", "refs": [{"round": 5, "t": 48200}]},
            {"title": "hallucinated", "refs": [{"round": 0, "t": 1}]},
            {"title": "partially", "refs": [{"round": 0, "t": 2}, {"round": 18, "t": 92713}]},
            {"title": "uncited", "refs": []},
        ],
        "drills": [
            {"title": "d-hallucinated", "refs": [{"round": 99, "t": 5}]},
            {"title": "d-grounded", "refs": [{"round": 18, "t": 92713}]},
        ],
    }
    clean, drops = p3_coach.validate_narration(narration, allowed)
    assert [m["title"] for m in clean["mistakes"]] == ["grounded", "partially"]
    assert clean["mistakes"][1]["refs"] == [{"round": 18, "t": 92713}]
    assert [d["title"] for d in clean["drills"]] == ["d-grounded"]
    assert drops == {"items": 3, "refs": 3, "mistakes": 2, "drills": 1}, drops
    assert clean["summary"] == "s"


def test_validate_narration_caps_mistakes_at_three():
    allowed = {(1, 100)}
    narration = {"summary": "s", "mistakes": [
        {"title": f"m{i}", "refs": [{"round": 1, "t": 100}]} for i in range(5)],
        "drills": []}
    clean, drops = p3_coach.validate_narration(narration, allowed)
    assert len(clean["mistakes"]) == 3
    assert drops["mistakes"] == 2 and drops["items"] == 2 and drops["refs"] == 0


def test_allowed_refs_from_findings():
    findings = [
        {"refs": [{"round": 3, "t": 10, "src": "event"}, {"round": "4", "t": "20"}]},
        {"refs": ["junk", {"round": 3, "t": 10}]},
        "junk",
    ]
    assert p3_coach.allowed_refs(findings) == {(3, 10), (4, 20)}
    assert p3_coach.allowed_refs(None) == set()


# ------------------------------------------------------------------ me pick


def test_me_get_put_roundtrip_and_file_shape():
    backup = _MeBackup()
    try:
        (get0_status, get0_body), (put_status, put_body), (get1_status, get1_body) = _sequence(
            ("GET", f"/api/replays/{REAL_REPLAY}/me", None),
            ("PUT", f"/api/replays/{REAL_REPLAY}/me", {"player": "P10"}),
            ("GET", f"/api/replays/{REAL_REPLAY}/me", None),
        )
        raw = json.loads(backup.path.read_text(encoding="utf-8"))
    finally:
        backup.restore()
    assert get0_status == 200 and get0_body["player"] in (None, "P10"), get0_body
    assert put_status == 200, put_body
    assert put_body["player"] == "P10" and put_body["updatedAt"]
    assert get1_status == 200 and get1_body["player"] == "P10"
    assert raw["schema"] == p3_coach.ME_SCHEMA and raw["player"] == "P10"
    assert raw["replayId"] == REAL_REPLAY
    assert not backup.path.with_suffix(".json.tmp").exists(), "no temp file may linger"


def test_me_put_normalizes_and_validates_slots():
    backup = _MeBackup()
    try:
        status, body = _roundtrip("PUT", f"/api/replays/{REAL_REPLAY}/me", {"player": "p4"})
        assert status == 200 and body["player"] == "P4", body
        status, body = _roundtrip("PUT", f"/api/replays/{REAL_REPLAY}/me", {"player": 4})
        assert status == 200 and body["player"] == "P4", body
        status, body = _roundtrip("PUT", f"/api/replays/{REAL_REPLAY}/me", {"player": "P99"})
        assert status == 400 and "not in this match" in body["error"], body
        status, body = _roundtrip("PUT", f"/api/replays/{REAL_REPLAY}/me", {"player": "zz"})
        assert status == 400 and "invalid player slot" in body["error"], body
        status, body = _roundtrip("PUT", f"/api/replays/{REAL_REPLAY}/me", {})
        assert status == 400 and "'player' is required" in body["error"], body
        status, body = _roundtrip("PUT", "/api/replays/no-such-replay/me", {"player": "P4"})
        assert status == 404, body
    finally:
        backup.restore()


def test_mutating_routes_are_loopback_only():
    from unittest import mock as _mock

    backup = _MeBackup()
    try:
        with _mock.patch("bot.replay.p3_coach._loopback_peer", return_value=False):
            put = _roundtrip("PUT", f"/api/replays/{REAL_REPLAY}/me", {"player": "P10"})
            post = _roundtrip("POST", f"/api/replays/{REAL_REPLAY}/coach", {"player": "P10"})
            get_me = _roundtrip("GET", f"/api/replays/{REAL_REPLAY}/me", None)
            get_coach = _roundtrip("GET", f"/api/replays/{REAL_REPLAY}/coach", None)
    finally:
        backup.restore()
    assert put[0] == 403 and "loopback" in put[1]["error"], put
    assert post[0] == 403 and "loopback" in post[1]["error"], post
    assert get_me[0] == 200, get_me
    assert get_coach[0] == 404, get_coach  # reads stay open; there is just no cache yet
    assert backup.saved == (backup.path.read_bytes() if backup.path.is_file() else None)


# ------------------------------------------------------------------ coach


def test_coach_post_rules_only_without_llm():
    with _dead_env():
        status, body = _roundtrip("POST", f"/api/replays/{REAL_REPLAY}/coach",
                                  {"player": "P10"})
    assert status == 200, body
    assert body["schema"] == p3_coach.SCHEMA
    assert body["player"]["slot"] == "P10" and body["player"]["agent"] == "Cypher"
    assert body["llm"]["available"] is False
    assert "no local LLM reachable" in body["llm"]["reason"]
    assert body["llm"]["tried"], body["llm"]
    assert body["narration"] is None
    assert body["rules"]["findings"], "rules findings must always be present"
    assert body["rules"]["metrics"] and body["rules"]["notes"]
    _assert_all_refs_grounded(body)


def test_coach_post_valid_narration_from_stub():
    _, refs = _real_refs()
    ref_a, ref_b = refs[0], refs[-1]
    good = {"summary": "You die first too often; trade discipline first.",
            "mistakes": [
                {"title": "Opening deaths", "detail": "Hold cover for the first peek.",
                 "severity": "major",
                 "refs": [{"round": ref_a["round"], "t": ref_a["t"]}]},
                {"title": "Untraded", "detail": "Play closer to a teammate.",
                 "refs": [{"round": ref_b["round"], "t": ref_b["t"]}]},
            ],
            "drills": [
                {"title": "Trade drill", "detail": "5 min crossfire practice.",
                 "refs": [{"round": ref_a["round"], "t": ref_a["t"]}]},
            ]}
    with MockLLM([json.dumps(good)], model_id=p2_review.LLAMACPP_DEFAULT_MODEL) as mock, \
            env(VALO_LLM_URL=mock.openai_base, VALO_LLM_KEY_FILE=None):
        status, body = _roundtrip("POST", f"/api/replays/{REAL_REPLAY}/coach",
                                  {"player": "P10"})
    assert status == 200, body
    llm = body["llm"]
    assert llm["available"] is True and llm["backend"] == "llamacpp"
    assert llm["model"] == p2_review.LLAMACPP_DEFAULT_MODEL
    assert llm["drops"] == {"items": 0, "refs": 0, "mistakes": 0, "drills": 0}, llm["drops"]
    assert body["narration"]["summary"].startswith("You die first")
    assert [m["title"] for m in body["narration"]["mistakes"]] == ["Opening deaths", "Untraded"]
    assert body["narration"]["mistakes"][0]["severity"] == "major"
    assert [d["title"] for d in body["narration"]["drills"]] == ["Trade drill"]
    _assert_all_refs_grounded(body)
    sent = json.dumps(mock.posts()[0]["body"])
    assert "Findings (JSON)" in sent and str(ref_a["t"]) in sent


def test_coach_post_hallucinated_refs_are_dropped_and_counted():
    _, refs = _real_refs()
    ref_a = refs[0]
    mixed = {"summary": "Some grounded, some invented.",
             "mistakes": [
                 {"title": "ok", "detail": "", "refs": [{"round": ref_a["round"], "t": ref_a["t"]}]},
                 {"title": "invented", "detail": "", "refs": [{"round": 0, "t": 12345}]},
                 {"title": "mixed", "detail": "",
                  "refs": [{"round": 0, "t": 1}, {"round": ref_a["round"], "t": ref_a["t"]}]},
             ],
             "drills": [
                 {"title": "d-ok", "refs": [{"round": ref_a["round"], "t": ref_a["t"]},
                                            {"round": 0, "t": 2}]},
                 {"title": "d-invented", "refs": [{"round": 99, "t": 5}]},
                 {"title": "d-uncited", "refs": []},
             ]}
    with MockLLM([json.dumps(mixed)]) as mock, env(VALO_LLM_URL=mock.openai_base):
        status, body = _roundtrip("POST", f"/api/replays/{REAL_REPLAY}/coach",
                                  {"player": "P10"})
    assert status == 200, body
    drops = body["llm"]["drops"]
    assert drops == {"items": 3, "refs": 4, "mistakes": 1, "drills": 2}, drops
    assert [m["title"] for m in body["narration"]["mistakes"]] == ["ok", "mixed"]
    assert body["narration"]["mistakes"][1]["refs"] == [
        {"round": ref_a["round"], "t": ref_a["t"], "tSec": round(ref_a["t"] / 1000.0, 3)}]
    assert [d["title"] for d in body["narration"]["drills"]] == ["d-ok"]
    _assert_all_refs_grounded(body)


def test_coach_post_invalid_reply_retries_then_rules_only():
    with MockLLM([GARBAGE]) as mock, env(VALO_LLM_URL=mock.openai_base):
        status, body = _roundtrip("POST", f"/api/replays/{REAL_REPLAY}/coach",
                                  {"player": "P10"})
        posts = mock.posts()
    assert status == 200, body
    assert body["llm"]["available"] is False
    assert "no parseable narration" in body["llm"]["reason"], body["llm"]
    assert body["narration"] is None
    assert body["rules"]["findings"]
    assert len(posts) == 2, "one retry exactly"
    assert posts[1]["body"]["messages"][-1]["content"] == p3_coach.NARRATION_RETRY


def test_coach_post_defaults_to_me_pick_and_validates_backend():
    backup = _MeBackup()
    try:
        backup.path.unlink(missing_ok=True)  # no pick on disk -> clear error
        with _dead_env():
            status, body = _roundtrip("POST", f"/api/replays/{REAL_REPLAY}/coach", {})
        assert status == 400 and "no player selected" in body["error"], (status, body)

        # PUT a pick, then a FRESH app resolves {"player": null} from viewer/me.json
        (put_status, put_body), = _sequence(("PUT", f"/api/replays/{REAL_REPLAY}/me", {"player": "P6"}))
        assert put_status == 200, put_body
        with _dead_env():
            status, body = _roundtrip("POST", f"/api/replays/{REAL_REPLAY}/coach", {})
        assert status == 200 and body["player"]["slot"] == "P6", body

        status, body = _roundtrip("POST", f"/api/replays/{REAL_REPLAY}/coach", {"player": "P99"})
        assert status == 400 and "not in this match" in body["error"], body
        status, body = _roundtrip("POST", f"/api/replays/{REAL_REPLAY}/coach",
                                  {"player": "P10", "backend": "bogus"})
        assert status == 400 and "unknown backend" in body["error"], body
    finally:
        backup.restore()


def test_coach_get_returns_cached_result():
    backup = _MeBackup()
    try:
        with _dead_env():
            (get0_status, get0_body), (post_status, posted), (get1_status, get1_body), \
                (get2_status, get2_body) = _sequence(
                    ("GET", f"/api/replays/{REAL_REPLAY}/coach", None),
                    ("POST", f"/api/replays/{REAL_REPLAY}/coach", {"player": "P10"}),
                    ("GET", f"/api/replays/{REAL_REPLAY}/coach?player=P10", None),
                    ("GET", f"/api/replays/{REAL_REPLAY}/coach", None),
                )
    finally:
        backup.restore()
    assert get0_status == 404 and "no cached coach result" in get0_body["error"], get0_body
    assert post_status == 200, posted
    assert get1_status == 200 and get1_body["generatedAt"] == posted["generatedAt"]
    # without a query: exactly one cached slot -> served
    assert get2_status == 200 and get2_body["player"]["slot"] == "P10"


def test_mount_registers_routes_and_p2_features():
    app = web.Application()
    p3_coach.mount(app)
    routes = {(route.resource.canonical, route.method) for route in app.router.routes()}
    assert ("/api/replays/{replay_id}/me", "GET") in routes
    assert ("/api/replays/{replay_id}/me", "PUT") in routes
    assert ("/api/replays/{replay_id}/coach", "POST") in routes
    assert ("/api/replays/{replay_id}/coach", "GET") in routes
    p3_coach.mount(app)  # idempotent
    again = {(route.resource.canonical, route.method) for route in app.router.routes()}
    assert again == routes

    full = web.Application()
    p2.mount(full)
    mounted = {(route.resource.canonical, route.method) for route in full.router.routes()}
    assert ("/api/replays/{replay_id}/coach", "POST") in mounted
    assert "p3_coach" in p2.FEATURES


# ----------------------------------------------------------------- plain runner


def _main() -> int:
    tests = [(name, obj) for name, obj in sorted(globals().items())
             if name.startswith("test_") and callable(obj)]
    failures = 0
    for name, test in tests:
        try:
            with _isolated_store():
                test()
        except Exception as exc:  # noqa: BLE001 - report every failure
            failures += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
        else:
            print(f"ok   {name}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_main())

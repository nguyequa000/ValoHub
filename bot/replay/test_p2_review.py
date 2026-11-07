#!/usr/bin/env python3
"""Tests for the local-LLM review feature (``bot/replay/p2_review.py``).

Runs two ways:

    py -3.12 -m pytest bot/replay/test_p2_review.py -q
    py -3.12 bot/replay/test_p2_review.py

Everything is hermetic: the LLM endpoints are stdlib ``http.server`` mocks bound
to ephemeral ports, reached through the documented env overrides
(``VALO_LLM_URL`` / ``VALO_LMSTUDIO_URL`` / ``VALO_OLLAMA_URL`` /
``VALO_LLM_KEY_FILE``). The one real end-to-end test is opt-in: it runs only when
a real local endpoint already answers, and is skipped silently otherwise (§2).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import aiohttp  # noqa: E402
from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from bot.replay import p2_review, store  # noqa: E402

REAL_REPLAY = "bad1543033d11396"
DEMO_REPLAY = "demo"
# A llama.cpp descriptor that never exists: tests that must not see the llama.cpp
# candidate (the real managed server may be running on this box) point this at it.
NO_DESCRIPTOR = os.path.join(_HERE, "_no_such_llamacpp_descriptor.json")

GOOD_REVIEW = json.dumps(
    {
        "summary": "Attacks converted the post-plant but lost the mid-round trades on Haven A.",
        "findings": [
            {"round": 5, "tMs": 48200, "severity": "warn", "title": "Late ult usage",
             "detail": "P9 ulted after the site was already lost; hold it for retake."},
            {"round": 18, "tMs": 92713, "severity": "tip", "title": "Defuse coverage",
             "detail": "P6 defused with no one holding the flank; assign a cover player."},
        ],
    }
)

FENCED_REVIEW = "Sure! Here is the review:\n```json\n" + GOOD_REVIEW + "\n```\nHope that helps."
PROSE_REVIEW = "Analysis follows.\n\n" + GOOD_REVIEW + "\n\nThat is all."
GARBAGE = "I am a language model and I cannot produce JSON right now."


# --------------------------------------------------------------------- helpers


@contextlib.contextmanager
def env(**values):
    """Temporary environment override (None removes the key)."""
    saved = {key: os.environ.get(key) for key in values}
    try:
        for key, value in values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@contextlib.contextmanager
def no_llm_env():
    """No LLM configured by default in every test — including the llama.cpp
    candidate (its default descriptor is the real Hermes-managed one)."""
    with env(VALO_LLM_URL=None, VALO_LMSTUDIO_URL=None, VALO_OLLAMA_URL=None,
             VALO_LLM_TIMEOUT=None, VALO_LLM_KEY_FILE=NO_DESCRIPTOR, VALO_LLM_MODEL=None):
        yield


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # noqa: D102 - silence the mock
        return

    def _send(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802 - stdlib naming
        mock = self.server.mock
        mock.calls.append({"method": "GET", "path": self.path, "body": None,
                           "auth": self.headers.get("Authorization")})
        if self.path.endswith("/models"):
            return self._send({"object": "list", "data": [{"id": mock.model_id}]})
        if self.path.endswith("/api/tags"):
            return self._send({"models": [{"name": mock.model_id}]})
        return self._send({"error": "not found"}, 404)

    def do_POST(self):  # noqa: N802 - stdlib naming
        mock = self.server.mock
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            body = {"__unparsed__": raw.decode("utf-8", "replace")}
        mock.calls.append({"method": "POST", "path": self.path, "body": body,
                           "auth": self.headers.get("Authorization")})
        if mock.status != 200:
            return self._send({"error": "mock failure"}, mock.status)
        content = mock.next_content()
        if self.path.endswith("/api/chat"):
            return self._send({"model": mock.model_id, "done": True,
                               "message": {"role": "assistant", "content": content}})
        return self._send({"model": mock.model_id,
                           "choices": [{"index": 0, "message": {"role": "assistant",
                                                                "content": content}}]})


class MockLLM:
    """Minimal OpenAI-compatible + Ollama mock in a background thread."""

    def __init__(self, responses=None, model_id="mock-local-model", status=200):
        self.responses = list(responses or [GOOD_REVIEW])
        self.model_id = model_id
        self.status = status
        self.calls: list[dict] = []
        port = free_port()
        self._server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
        self._server.mock = self
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
        return False

    @property
    def base(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    @property
    def openai_base(self) -> str:
        return self.base + "/v1"

    def next_content(self) -> str:
        if len(self.responses) > 1:
            return self.responses.pop(0)
        return self.responses[0]

    def posts(self) -> list[dict]:
        return [call for call in self.calls if call["method"] == "POST"]


def _roundtrip(method: str, path: str, body=None):
    """Run one request against a freshly mounted app; return (status, json)."""
    (result,) = _sequence((method, path, body))
    return result


def _sequence(*requests):
    """Run several requests against ONE app instance (shares the in-memory cache)."""

    async def go():
        app = web.Application()
        p2_review.mount(app)
        client = TestClient(TestServer(app))
        await client.start_server()
        results = []
        try:
            for method, path, body in requests:
                if method == "POST":
                    response = await client.post(path, json=body)
                else:
                    response = await client.get(path)
                results.append((response.status, await response.json()))
        finally:
            await client.close()
        return results

    return asyncio.run(go())


# ---------------------------------------------------------------- digest tests


def test_digest_real_store_counts():
    with no_llm_env():
        digest = p2_review.build_digest(REAL_REPLAY)
    rounds = digest["rounds"]
    assert len(rounds) == 18, len(rounds)
    assert [r["n"] for r in rounds] == list(range(1, 19))
    assert digest["map"] == "Haven"
    assert digest["patch"] == "13.06"
    assert digest["roundFrom"] == 1 and digest["roundTo"] == 18
    assert len(digest["players"]) == 10
    kills = sum(len(r["kills"]) for r in rounds)
    plants = sum(1 for r in rounds if r["plant"]["tMs"] is not None)
    defuses = sum(1 for r in rounds if r["defuse"]["tMs"] is not None)
    ults = sum(len(r["ults"]) for r in rounds)
    # viewer JSON ground truth (sum of per-round event lists)
    assert (kills, plants, defuses, ults) == (128, 11, 1, 15), (kills, plants, defuses, ults)
    # 2 ults in the store carry actor:null — kept as null, never invented
    unattributed = [u for r in rounds for u in r["ults"] if u[0] is None]
    assert len(unattributed) == 2, unattributed
    assert all(u[1] is not None for u in unattributed)
    text = p2_review.render_digest(digest)
    assert len(text.encode("utf-8")) < p2_review.DIGEST_BUDGET_BYTES
    print(f"digest bytes={len(text.encode('utf-8'))} kills={kills} plants={plants} "
          f"defuses={defuses} ults={ults} unattributed_ults={len(unattributed)}")


def test_digest_round_range_and_size():
    digest = p2_review.build_digest(REAL_REPLAY, 3, 5)
    assert [r["n"] for r in digest["rounds"]] == [3, 4, 5]
    text = p2_review.render_digest(digest)
    assert "R03" in text and "R05" in text and "R06" not in text
    assert len(text.encode("utf-8")) < p2_review.DIGEST_BUDGET_BYTES


def test_digest_demo_fixture_flat_layout():
    digest = p2_review.build_digest(DEMO_REPLAY)
    assert [r["n"] for r in digest["rounds"]] == [1, 2]
    assert digest["map"] == "Haven"
    first = digest["rounds"][0]
    assert first["plant"] == {"actor": "P1", "tMs": 41000}
    assert first["defuse"] == {"actor": "P6", "tMs": 66000}
    assert first["winner"] == 0
    # P1's round-1 spawn in metres, straight off the demo fixture. The fixture
    # walks real Haven routes (P3/P1-F1), so this value is fixture-derived: it was
    # the off-map [-40, -22] before that fix regenerated data/replays/demo.
    assert first["spawn"]["P1"] == [35, -135]
    assert len(p2_review.render_digest(digest).encode("utf-8")) < p2_review.DIGEST_BUDGET_BYTES


def test_render_degrades_to_fit_budget():
    digest = p2_review.build_digest(REAL_REPLAY)
    full = p2_review.render_digest(digest)
    small = p2_review.render_digest(digest, budget=2000)
    assert len(small.encode("utf-8")) <= 2000
    assert "positions omitted" in small
    assert "R18" in small  # every round still represented
    assert "spawn(m)" not in small
    assert len(small) < len(full)
    print(f"full={len(full.encode('utf-8'))} B, degraded={len(small.encode('utf-8'))} B")


def test_never_fabricates_missing_rounds():
    try:
        p2_review.build_digest(REAL_REPLAY, 19, 25)
    except p2_review.ReviewError as exc:
        assert exc.status == 400
    else:
        raise AssertionError("out-of-range round request must not invent rounds")
    try:
        p2_review.build_digest(DEMO_REPLAY, 5)
    except p2_review.ReviewError as exc:
        assert exc.status == 400
    else:
        raise AssertionError("demo has only 2 rounds")
    assert set(store.replay_dir(REAL_REPLAY).glob("viewer/rounds/r*.json"))  # sanity


def test_digest_bad_input_and_missing_replay():
    for args, status in ((("abc", None), 400), ((None, "xyz"), 400), ((9, 3), 400)):
        try:
            p2_review.build_digest(REAL_REPLAY, *args)
        except p2_review.ReviewError as exc:
            assert exc.status == status, (args, exc.status)
        else:
            raise AssertionError(f"expected ReviewError for {args}")
    try:
        p2_review.build_digest("no-such-replay")
    except p2_review.ReviewError as exc:
        assert exc.status == 404
    else:
        raise AssertionError("missing replay must raise ReviewError(404)")


# --------------------------------------------------------------- parsing tests


def test_parse_strict_fenced_and_prose():
    for payload in (GOOD_REVIEW, FENCED_REVIEW, PROSE_REVIEW):
        parsed = p2_review.parse_review(payload)
        assert parsed is not None, payload[:40]
        assert parsed["summary"].startswith("Attacks converted")
        assert len(parsed["findings"]) == 2
        assert parsed["findings"][0]["severity"] == "warn"
        assert parsed["findings"][1]["tMs"] == 92713


def test_parse_rejects_and_coerces():
    assert p2_review.parse_review("") is None
    assert p2_review.parse_review(GARBAGE) is None
    assert p2_review.parse_review('{"findings": []}') is None  # no summary
    assert p2_review.parse_review("{not json at all}") is None

    coerced = p2_review.parse_review(json.dumps({
        "review": {"summary": ["line one", "line two"], "findings": [
            {"round": "3", "timeMs": "41234", "severity": "WARN", "name": "entry",
             "description": "detail here"},
            {"severity": "warn", "title": ""},          # dropped: no title
            "not-an-object",                            # dropped
        ]},
    }))
    assert coerced is not None
    assert coerced["summary"] == "line one line two"
    assert len(coerced["findings"]) == 1
    finding = coerced["findings"][0]
    assert (finding["round"], finding["tMs"], finding["severity"]) == (3, 41234, "warn")
    assert finding["title"] == "entry" and finding["detail"] == "detail here"

    weird = p2_review.parse_review('prose {"summary": "braces } inside", "findings": []} tail')
    assert weird is not None and weird["summary"] == "braces } inside"


def test_probe_candidates_order_and_filters():
    with env(VALO_LLM_URL="http://127.0.0.1:9/v1", VALO_LMSTUDIO_URL=None,
             VALO_OLLAMA_URL=None, VALO_LLM_KEY_FILE=NO_DESCRIPTOR):
        auto = p2_review.probe_candidates("auto")
        assert [c["backend"] for c in auto] == ["llamacpp", "lmstudio", "ollama"]
        assert p2_review.chat_url(auto[0]) == "http://127.0.0.1:9/v1/chat/completions"
        assert auto[0]["model"] == p2_review.LLAMACPP_DEFAULT_MODEL
        assert auto[0]["api_key"] is None, "a retargeted url must not carry a key by default"
        assert p2_review.chat_url(auto[2]) == "http://127.0.0.1:11434/api/chat"
        assert [c["backend"] for c in p2_review.probe_candidates("ollama")] == ["ollama"]
        assert [c["backend"] for c in p2_review.probe_candidates("lmstudio")] == ["lmstudio"]
        # legacy alias: "env" was the name of the bare VALO_LLM_URL probe
        assert [c["backend"] for c in p2_review.probe_candidates("env")] == ["llamacpp"]
        try:
            p2_review.probe_candidates("bogus")
        except p2_review.ReviewError as exc:
            assert exc.status == 400
        else:
            raise AssertionError("unknown backend must be rejected")
    with env(VALO_LLM_URL=None, VALO_LLM_KEY_FILE=NO_DESCRIPTOR):
        assert [c["backend"] for c in p2_review.probe_candidates("auto")] == ["lmstudio", "ollama"]
        try:
            p2_review.probe_candidates("llamacpp")
        except p2_review.ReviewError as exc:
            assert exc.status == 400
        else:
            raise AssertionError("backend=llamacpp without an endpoint must be rejected")


def test_llamacpp_candidate_from_descriptor():
    """base_url + key come from the descriptor; the managed key never travels to a
    retargeted url unless that descriptor is named explicitly; VALO_LLM_MODEL pins."""
    import tempfile
    from pathlib import Path

    good_key = "test-key-0123456789abcdef01234567"
    with tempfile.TemporaryDirectory() as tmp:
        descriptor = Path(tmp) / "server.json"
        descriptor.write_text(json.dumps({"base_url": "http://127.0.0.1:19434/v1",
                                          "api_key": good_key}), encoding="utf-8")

        # explicit descriptor, no URL override -> base + key both from the file
        with env(VALO_LLM_URL=None, VALO_LLM_KEY_FILE=str(descriptor)):
            (candidate,) = p2_review.probe_candidates("llamacpp")
            assert candidate["base"] == "http://127.0.0.1:19434/v1"
            assert candidate["model"] == p2_review.LLAMACPP_DEFAULT_MODEL
            assert p2_review._auth_headers(candidate) == {"Authorization": "Bearer " + good_key}

        # the default descriptor path is <LOCALAPPDATA>/hermes/runtimes/llamacpp/server.json
        managed = Path(tmp) / "hermes" / "runtimes" / "llamacpp"
        managed.mkdir(parents=True)
        (managed / "server.json").write_text(json.dumps(
            {"base_url": "http://127.0.0.1:19435/v1", "api_key": good_key}), encoding="utf-8")
        with env(LOCALAPPDATA=tmp, VALO_LLM_URL=None, VALO_LLM_KEY_FILE=None):
            (candidate,) = p2_review.probe_candidates("llamacpp")
            assert candidate["base"] == "http://127.0.0.1:19435/v1"
            assert candidate["api_key"] == good_key

        # VALO_LLM_URL retargets the base: the managed key must NOT travel with it ...
        with env(LOCALAPPDATA=tmp, VALO_LLM_URL="http://127.0.0.1:9/v1", VALO_LLM_KEY_FILE=None):
            (candidate,) = p2_review.probe_candidates("llamacpp")
            assert candidate["base"] == "http://127.0.0.1:9/v1"
            assert candidate["api_key"] is None
        # ... unless the descriptor is named explicitly.
        with env(LOCALAPPDATA=tmp, VALO_LLM_URL="http://127.0.0.1:9/v1",
                 VALO_LLM_KEY_FILE=str(descriptor)):
            (candidate,) = p2_review.probe_candidates("llamacpp")
            assert candidate["api_key"] == good_key

        # VALO_LLM_MODEL pins a different model id
        with env(VALO_LLM_KEY_FILE=str(descriptor), VALO_LLM_MODEL="Qwen3.8-9B-Q8_0"):
            (candidate,) = p2_review.probe_candidates("llamacpp")
            assert candidate["model"] == "Qwen3.8-9B-Q8_0"

        # missing descriptor -> no candidate; auto keeps LM Studio + Ollama
        with env(LOCALAPPDATA=tmp, VALO_LLM_URL=None,
                 VALO_LLM_KEY_FILE=str(Path(tmp) / "nope.json")):
            assert [c["backend"] for c in p2_review.probe_candidates("auto")] == ["lmstudio", "ollama"]


def test_timeout_is_capped_at_300s():
    assert p2_review._timeout({}) == 300.0
    assert p2_review._timeout({"VALO_LLM_TIMEOUT": "9999"}) == 300.0
    assert p2_review._timeout({"VALO_LLM_TIMEOUT": "45"}) == 45.0
    assert p2_review._timeout({"VALO_LLM_TIMEOUT": "nonsense"}) == 300.0


# ------------------------------------------------------------- endpoint tests


def test_post_200_against_mock_openai():
    with MockLLM() as mock, env(VALO_LLM_URL=mock.openai_base):
        status, body = _roundtrip("POST", f"/api/replays/{REAL_REPLAY}/review", {})
    assert status == 200, body
    assert set(body) >= {"replayId", "backend", "model", "generatedAt", "summary", "findings"}
    assert body["replayId"] == REAL_REPLAY
    assert body["backend"] == "llamacpp"  # first candidate (P4-B probe chain)
    assert body["model"] == "mock-local-model"
    assert body["summary"].startswith("Attacks converted")
    assert [f["round"] for f in body["findings"]] == [5, 18]
    assert all(set(f) == {"round", "tMs", "severity", "title", "detail"} for f in body["findings"])
    assert body["digestBytes"] < p2_review.DIGEST_BUDGET_BYTES
    assert mock.posts() and mock.posts()[0]["path"].endswith("/chat/completions")
    sent = json.dumps(mock.posts()[0]["body"])
    assert "R01" in sent and "spawn(m)" in sent
    print(f"mock openai: status={status} digestBytes={body['digestBytes']} findings={len(body['findings'])}")


def test_llamacpp_key_file_sends_bearer_and_model_pin():
    """A descriptor key becomes a Bearer header on every call; the pinned default
    model id is used when the server serves it; the key never reaches the response."""
    import tempfile
    from pathlib import Path

    good_key = "test-key-abcdef0123456789abcdef01"
    with tempfile.TemporaryDirectory() as tmp:
        descriptor = Path(tmp) / "server.json"
        with MockLLM(model_id=p2_review.LLAMACPP_DEFAULT_MODEL) as mock:
            descriptor.write_text(json.dumps({"base_url": mock.openai_base,
                                              "api_key": good_key}), encoding="utf-8")
            with env(VALO_LLM_URL=None, VALO_LLM_KEY_FILE=str(descriptor)):
                status, body = _roundtrip("POST", f"/api/replays/{DEMO_REPLAY}/review", {})
            calls = list(mock.calls)
    assert status == 200, body
    assert body["backend"] == "llamacpp"
    assert body["model"] == p2_review.LLAMACPP_DEFAULT_MODEL, body["model"]
    assert calls, "the stub must have been called"
    assert all(call["auth"] == "Bearer " + good_key for call in calls), \
        [call["auth"] for call in calls]
    assert good_key not in json.dumps(body), "the key must never appear in a response"


def test_post_200_against_mock_ollama_with_explicit_model():
    with MockLLM() as mock, env(VALO_OLLAMA_URL=mock.base):
        status, body = _roundtrip("POST", f"/api/replays/{DEMO_REPLAY}/review",
                                  {"backend": "ollama", "model": "my-9b"})
        posts = mock.posts()
    assert status == 200, body
    assert body["backend"] == "ollama" and body["model"] == "my-9b"
    assert posts and posts[0]["path"].endswith("/api/chat")
    assert posts[0]["body"]["stream"] is False
    assert posts[0]["body"]["options"]["temperature"] == 0.2
    # explicit model -> no /models probe
    assert not [c for c in mock.calls if c["method"] == "GET"]


def test_probe_order_llamacpp_before_lmstudio():
    with MockLLM() as first_mock, MockLLM() as studio_mock, \
            env(VALO_LLM_URL=first_mock.openai_base, VALO_LMSTUDIO_URL=studio_mock.openai_base,
                VALO_LLM_KEY_FILE=NO_DESCRIPTOR):
        status, body = _roundtrip("POST", f"/api/replays/{DEMO_REPLAY}/review", {})
    assert status == 200, body
    assert body["backend"] == "llamacpp"
    assert first_mock.posts(), "the llama.cpp candidate must be tried first"
    assert not studio_mock.posts(), "lmstudio must not be called when the first candidate answers"


def test_lmstudio_used_when_no_llamacpp_endpoint():
    with MockLLM() as studio_mock, env(VALO_LLM_URL=None, VALO_LLM_KEY_FILE=NO_DESCRIPTOR,
                                       VALO_LMSTUDIO_URL=studio_mock.openai_base):
        status, body = _roundtrip("POST", f"/api/replays/{DEMO_REPLAY}/review", {})
    assert status == 200, body
    assert body["backend"] == "lmstudio"


def test_503_when_nothing_reachable():
    dead = f"http://127.0.0.1:{free_port()}/v1"
    with env(VALO_LLM_URL=dead, VALO_LMSTUDIO_URL=dead, VALO_OLLAMA_URL=dead):
        status, body = _roundtrip("POST", f"/api/replays/{REAL_REPLAY}/review", {})
    assert status == 503, body
    assert body["error"] == "no local LLM reachable"
    assert isinstance(body["tried"], list) and len(body["tried"]) == 3, body["tried"]
    assert any("llamacpp" in entry for entry in body["tried"])
    assert any("lmstudio" in entry for entry in body["tried"])
    assert any("ollama" in entry for entry in body["tried"])
    print("503 tried:", body["tried"][0][:70])


def test_retry_once_on_unparseable_output():
    with MockLLM([GARBAGE, GOOD_REVIEW]) as mock, env(VALO_LLM_URL=mock.openai_base):
        status, body = _roundtrip("POST", f"/api/replays/{DEMO_REPLAY}/review", {})
        posts = mock.posts()
    assert status == 200, body
    assert len(posts) == 2, "must retry exactly once after a parse failure"
    assert posts[1]["body"]["messages"][-1]["content"] == p2_review.RETRY_INSTRUCTION
    assert body["summary"].startswith("Attacks converted")


def test_503_notes_when_endpoint_answered_with_garbage():
    with MockLLM([GARBAGE]) as mock, \
            env(VALO_LLM_URL=mock.openai_base, VALO_LMSTUDIO_URL=None, VALO_OLLAMA_URL=None):
        status, body = _roundtrip("POST", f"/api/replays/{DEMO_REPLAY}/review",
                                  {"backend": "env"})
        posts = mock.posts()
    assert status == 503, body
    assert body["error"] == "no local LLM reachable"
    assert "note" in body and "parseable" in body["note"]
    assert len(posts) == 2  # original + single retry
    assert "no JSON review could be parsed" in body["tried"][0]


def test_get_404_then_cached_result():
    with MockLLM() as mock, env(VALO_LLM_URL=mock.openai_base):
        (get_status, get_body), (post_status, posted), (status, cached) = _sequence(
            ("GET", f"/api/replays/{REAL_REPLAY}/review", None),
            ("POST", f"/api/replays/{REAL_REPLAY}/review", {"roundFrom": 1, "roundTo": 2}),
            ("GET", f"/api/replays/{REAL_REPLAY}/review", None),
        )
    assert get_status == 404 and "no cached review" in get_body["error"], get_body
    assert post_status == 200, posted
    assert status == 200, cached
    assert cached["summary"] == posted["summary"]
    assert cached["generatedAt"] == posted["generatedAt"]
    assert cached["replayId"] == REAL_REPLAY


def test_get_404_for_unreviewed_replay():
    with no_llm_env():
        status, body = _roundtrip("GET", f"/api/replays/{DEMO_REPLAY}/review")
    assert status == 404 and "no cached review" in body["error"], body


def test_post_error_paths():
    with no_llm_env():
        status, body = _roundtrip("POST", "/api/replays/no-such-replay/review", {})
        assert status == 404 and "no normalized viewer data" in body["error"], body

        status, body = _roundtrip("POST", f"/api/replays/{REAL_REPLAY}/review", {"backend": "bogus"})
        assert status == 400 and "unknown backend" in body["error"], body

        status, body = _roundtrip("POST", f"/api/replays/{REAL_REPLAY}/review", {"roundFrom": "x"})
        assert status == 400 and "roundFrom" in body["error"], body

        status, body = _roundtrip("POST", f"/api/replays/{DEMO_REPLAY}/review", {"roundFrom": 1, "roundTo": 1})
    assert status == 503, body
    assert body["error"] == "no local LLM reachable"


def test_post_accepts_absent_body_and_rejects_malformed():
    """All body fields are optional: no body at all must behave like {}."""

    async def go():
        app = web.Application()
        p2_review.mount(app)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            absent = await client.post(f"/api/replays/{DEMO_REPLAY}/review")
            null = await client.post(f"/api/replays/{DEMO_REPLAY}/review", data=b"")
            malformed = await client.post(f"/api/replays/{DEMO_REPLAY}/review", data=b"{not json")
            return ((absent.status, await absent.json()),
                    (null.status, await null.json()),
                    (malformed.status, await malformed.json()))
        finally:
            await client.close()

    with MockLLM() as mock, env(VALO_LLM_URL=mock.openai_base):
        (absent_status, absent_body), (null_status, _), (bad_status, bad_body) = asyncio.run(go())
    assert absent_status == 200, absent_body
    assert null_status == 200, "empty body must behave like {}"
    assert bad_status == 400 and "must be JSON" in bad_body["error"], bad_body


def test_mount_registers_both_routes():
    app = web.Application()
    p2_review.mount(app)
    routes = {(route.resource.canonical, route.method) for route in app.router.routes()}
    assert ("/api/replays/{replay_id}/review", "POST") in routes
    assert ("/api/replays/{replay_id}/review", "GET") in routes
    p2_review.mount(app)  # idempotent: no duplicate routes, cache preserved
    again = {(route.resource.canonical, route.method) for route in app.router.routes()}
    assert again == routes
    assert isinstance(app[p2_review.CACHE_KEY], dict)


# --------------------------------------------------------- optional real e2e


def _real_endpoint() -> dict | None:
    """Return the first reachable real local endpoint, or None (skip silently)."""
    for candidate in p2_review.probe_candidates("auto"):
        if candidate["backend"] == "llamacpp":
            continue  # managed endpoint needs its descriptor key; opt-in stays LM Studio/Ollama
        url = p2_review.models_url(candidate)

        async def probe():
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=1.5)) as response:
                    return response.status == 200

        try:
            if asyncio.run(probe()):
                return candidate
        except Exception:  # noqa: BLE001 - endpoint simply is not there
            continue
    return None


def test_optional_real_local_llm():
    if os.getenv("VALO_REVIEW_REAL_E2E") != "1":
        return  # opt-in only; the hermetic mock tests above are the gate
    candidate = _real_endpoint()
    if candidate is None:
        print("real local LLM e2e: skipped (no endpoint reachable)")
        return
    digest_text = p2_review.render_digest(p2_review.build_digest(REAL_REPLAY, 1, 3))
    result = asyncio.run(p2_review.request_review(digest_text, backend=candidate["backend"]))
    assert result["content"]["summary"]
    print(f"real e2e backend={result['backend']} model={result['model']} "
          f"summary={result['content']['summary'][:120]!r}")


# ----------------------------------------------------------------- plain runner


def _main() -> int:
    tests = [(name, obj) for name, obj in sorted(globals().items())
             if name.startswith("test_") and callable(obj)]
    failures = 0
    for name, test in tests:
        try:
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

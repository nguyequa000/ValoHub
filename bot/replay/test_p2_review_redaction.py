"""p2_review must not echo upstream bodies: errors/`tried`/logs carry only {code,status,request_id}.

    py -3.12 -m pytest bot/replay/test_p2_review_redaction.py -q
"""
from __future__ import annotations

import asyncio
import json
import logging

import pytest

from bot.replay import p2_review
from bot.replay.ai_test_support import LEAK_MARKER, Stub

LOCAL_KEY = "local-descriptor-KEY-abcdef123456"


@pytest.fixture
def stub():
    s = Stub().start()
    s.open_access = True
    yield s
    s.stop()


def env_for(stub, tmp_path):
    descriptor = tmp_path / "server.json"
    descriptor.write_text(json.dumps({"base_url": f"{stub.base}/v1", "api_key": LOCAL_KEY}), encoding="utf-8")
    return {"VALO_LLM_KEY_FILE": str(descriptor), "VALO_LMSTUDIO_URL": "http://127.0.0.1:1/v1",
            "VALO_OLLAMA_URL": "http://127.0.0.1:1"}


@pytest.mark.parametrize("status, code", [(500, "upstream_unavailable"), (401, "invalid_key"), (429, "rate_limited"),
                                          (400, "upstream_rejected")])
def test_tried_and_logs_carry_code_status_request_id_only(stub, tmp_path, caplog, status, code):
    caplog.set_level(logging.DEBUG)
    stub.force["/chat/completions"] = status
    with pytest.raises(p2_review.NoLLMReachable) as info:
        asyncio.run(p2_review.request_chat([{"role": "user", "content": "hi"}], backend="llamacpp",
                                           environ=env_for(stub, tmp_path), timeout=10))
    tried = info.value.tried
    assert len(tried) == 1
    assert f"UpstreamError: code={code} status={status} request_id=req-forced-1" in tried[0]
    logs = "\n".join(r.getMessage() for r in caplog.records)
    for text in ("\n".join(tried), logs):
        assert LEAK_MARKER not in text and LOCAL_KEY not in text and "forced 5" not in text and "forced 4" not in text


def test_models_listing_failure_is_structured_too(stub, tmp_path):
    stub.force["/models"] = 403
    with pytest.raises(p2_review.NoLLMReachable) as info:
        asyncio.run(p2_review.request_chat([{"role": "user", "content": "hi"}], backend="llamacpp",
                                           environ=env_for(stub, tmp_path), timeout=10))
    assert "code=forbidden status=403" in info.value.tried[0] and LEAK_MARKER not in info.value.tried[0]


def test_generic_exception_text_is_redacted_and_capped():
    secret = "sk-live-ABCDEFGHIJKLMNOPQRSTUV"
    line = p2_review.describe_failure(RuntimeError(f"boom Authorization: Bearer {secret} " + "x" * 500))
    assert secret not in line and len(line) <= 200
    assert p2_review.describe_failure(p2_review.UpstreamError("forbidden", 403, None)) == \
        "UpstreamError: code=forbidden status=403 request_id=-"


def test_happy_path_still_works(stub, tmp_path):
    result = asyncio.run(p2_review.request_chat([{"role": "user", "content": "hi"}], backend="llamacpp",
                                                environ=env_for(stub, tmp_path), timeout=10))
    assert result["content"] == "stub answer" and result["backend"] == "llamacpp" and result["tried"] == []

"""Offline policy: table-driven checks for loopback-only LLM endpoints and cloud models."""
import asyncio

import pytest

from bot.replay import p2_review
from bot.replay.offline import OfflineViolation, assert_local_url, is_loopback_host, reject_cloud_model

LOCAL = ["http://127.0.0.1:1234/v1", "http://localhost:11434", "http://[::1]:8080/v1", "https://127.0.0.1/x",
         "http://127.5.5.5:1", "http://localhost.:11434", "http://[::ffff:127.0.0.1]:80", "http://LOCALHOST:1/"]
REMOTE = ["http://10.0.0.5:1234/v1", "https://example.com/v1", "http://192.168.1.2", "http://127.0.0.1@evil.com/",
          "http://user:pw@127.0.0.1:1/", "http://evil.com#@127.0.0.1/", "http://127.0.0.1.evil.com/",
          "ftp://127.0.0.1/", "file:///C:/x", "", "http://", "http://0.0.0.0:1", "http://[::ffff:10.0.0.1]/",
          "http://localhost.evil.com/", "http://127.0.0.1:99999/", None, 5]


@pytest.mark.parametrize("url", LOCAL)
def test_local_urls_pass(url):
    assert assert_local_url(url) == url.strip()


@pytest.mark.parametrize("url", REMOTE)
def test_remote_urls_rejected(url):
    with pytest.raises(OfflineViolation):
        assert_local_url(url)


def test_is_loopback_host_table():
    yes = ["127.0.0.1", "::1", "[::1]", "localhost", "LOCALHOST.", "::ffff:127.0.0.1", "127.9.9.9"]
    no = ["10.0.0.1", "example.com", "", None, "0.0.0.0", "::", "::ffff:8.8.8.8", "127.0.0.1.evil.com"]
    assert all(is_loopback_host(h) for h in yes)
    assert not any(is_loopback_host(h) for h in no)


@pytest.mark.parametrize("name", ["gpt-oss:120b-cloud", "qwen3-coder:480b-cloud", "x:cloud", "Foo-Cloud"])
def test_cloud_models_rejected(name):
    with pytest.raises(OfflineViolation):
        reject_cloud_model(name)


@pytest.mark.parametrize("name", ["qwen3:8b", "llama3.1:8b", "cloudy-model", "", None])
def test_local_models_pass(name):
    reject_cloud_model(name)


def test_request_chat_refuses_remote_hosts_and_names_them(tmp_path):
    env = {"VALO_LLM_URL": "http://example.com/v1", "VALO_LMSTUDIO_URL": "http://10.1.2.3:1234/v1",
           "VALO_OLLAMA_URL": "http://127.0.0.1@evil.net:11434", "VALO_LLM_KEY_FILE": str(tmp_path / "none.json")}
    with pytest.raises(p2_review.NoLLMReachable) as caught:
        asyncio.run(p2_review.request_chat([{"role": "user", "content": "hi"}], environ=env))
    text = " ".join(caught.value.tried)
    assert len(caught.value.tried) == 3 and "OfflineViolation" in text
    assert "example.com" in text and "10.1.2.3" in text and "evil.net" in text


def test_request_chat_rejects_cloud_model_even_on_loopback(tmp_path):
    env = {"VALO_LLM_URL": "http://127.0.0.1:9/v1", "VALO_LLM_KEY_FILE": str(tmp_path / "none.json")}
    with pytest.raises(p2_review.NoLLMReachable) as caught:
        asyncio.run(p2_review.request_chat([{"role": "user", "content": "hi"}], backend="ollama",
                                           model="x:480b-cloud", environ=env))
    assert "cloud model" in caught.value.tried[0]


def test_loopback_url_is_not_blocked_by_policy(tmp_path):
    """Negative control: a loopback base fails for connection reasons, not for policy."""
    env = {"VALO_LLM_URL": "http://127.0.0.1:9/v1", "VALO_LLM_KEY_FILE": str(tmp_path / "none.json")}
    with pytest.raises(p2_review.NoLLMReachable) as caught:
        asyncio.run(p2_review.request_chat([{"role": "user", "content": "hi"}], backend="llamacpp", environ=env))
    assert "OfflineViolation" not in caught.value.tried[0]

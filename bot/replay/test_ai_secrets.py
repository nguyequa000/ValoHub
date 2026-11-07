"""DPAPI vault: round-trip, entropy binding, tamper handling, atomic file, ACL.

    py -3.12 -m pytest bot/replay/test_ai_secrets.py -q       (DPAPI cases skip off Windows)
"""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

from bot.replay import ai_secrets as sec
from bot.replay.ai_test_support import make_vault

win_only = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")
SECRET = "sk-vault-SECRET-1234567890abcdef"


@win_only
def test_dpapi_round_trip_and_entropy_binding():
    blob = sec.dpapi_protect(b"hello-secret", b"entropy-A")
    assert b"hello-secret" not in blob and len(blob) > len(b"hello-secret")
    assert sec.dpapi_unprotect(blob, b"entropy-A") == b"hello-secret"
    with pytest.raises(sec.SecretsError):
        sec.dpapi_unprotect(blob, b"entropy-B")                  # entropy = constant + provider id binds the blob
    with pytest.raises(sec.SecretsError):
        sec.dpapi_unprotect(blob[:-4] + b"\x00\x00\x00\x00", b"entropy-A")
    assert sec.dpapi_unprotect(sec.dpapi_protect(b"", b"e"), b"e") == b""


def test_unavailable_off_windows_never_falls_back_to_plaintext(monkeypatch):
    monkeypatch.setattr(sec.sys, "platform", "linux")
    with pytest.raises(sec.SecretsUnavailable):
        sec.dpapi_protect(b"x", b"e")


def test_vault_put_get_describe_delete(tmp_path):
    vault = make_vault(tmp_path / "secrets" / "providers.bin")
    assert vault.get("openai") is None and vault.describe("openai")["configured"] is False
    vault.put("openai", "api_key", SECRET)
    vault.put("openrouter", "oauth", "sk-or-v1-OAUTHKEY0000000000")
    record = vault.get("openai")
    assert record["secret"] == SECRET and record["kind"] == "api_key" and record["saved_at"] > 0
    view = vault.describe("openai")
    assert view == {"configured": True, "last4": SECRET[-4:], "kind": "api_key", "reauth_required": False}
    assert SECRET not in json.dumps(view)
    assert vault.ids() == ["openai", "openrouter"]
    assert vault.delete("openai") is True and vault.delete("openai") is False
    assert vault.get("openai") is None and vault.get("openrouter") is not None
    vault.delete("openrouter")
    assert not vault.path.exists()                               # last entry removed -> no empty vault left


def test_file_is_sealed_and_atomic(tmp_path):
    path = tmp_path / "secrets" / "providers.bin"
    vault = make_vault(path)
    vault.put("openai", "api_key", SECRET)
    raw = path.read_bytes()
    assert SECRET.encode() not in raw and b"api_key" not in raw           # `strings` finds nothing useful
    assert json.loads(raw)["version"] == 1
    assert [p.name for p in path.parent.iterdir()] == ["providers.bin"]   # temp file replaced, none left behind
    vault.put("openai", "api_key", SECRET + "2")                          # replace in place
    assert vault.get("openai")["secret"] == SECRET + "2"
    assert [p.name for p in path.parent.iterdir()] == ["providers.bin"]


def test_tampered_blob_is_deleted_and_requires_reauth(tmp_path):
    path = tmp_path / "secrets" / "providers.bin"
    vault = make_vault(path)
    vault.put("openai", "api_key", SECRET)
    vault.put("gemini", "api_key", SECRET + "G")
    data = json.loads(path.read_text())
    blob = data["entries"]["openai"]
    data["entries"]["openai"] = blob[:-8] + ("AAAAAAAA" if not blob.endswith("AAAAAAAA") else "BBBBBBBB")
    path.write_text(json.dumps(data))
    with pytest.raises(sec.ReauthRequired):
        vault.get("openai")
    assert vault.ids() == ["gemini"]                                       # purged, neighbours untouched
    assert vault.get("gemini")["secret"] == SECRET + "G"
    assert vault.describe("openai")["configured"] is False


def test_entry_moved_to_another_provider_does_not_decrypt(tmp_path):
    path = tmp_path / "secrets" / "providers.bin"
    vault = make_vault(path)
    vault.put("openai", "api_key", SECRET)
    data = json.loads(path.read_text())
    data["entries"]["anthropic"] = data["entries"]["openai"]               # same blob, other provider id
    path.write_text(json.dumps(data))
    with pytest.raises(sec.ReauthRequired):
        vault.get("anthropic")
    assert vault.get("openai")["secret"] == SECRET


def test_corrupt_or_foreign_file_is_dropped(tmp_path):
    path = tmp_path / "secrets" / "providers.bin"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\x00\x01not json")
    vault = make_vault(path)
    assert vault.get("openai") is None and not path.exists()
    path.write_text(json.dumps({"version": 99, "entries": {"openai": "AAAA"}}))
    assert vault.ids() == [] and not path.exists()


def test_bad_inputs(tmp_path):
    vault = make_vault(tmp_path / "s" / "providers.bin")
    with pytest.raises(sec.SecretsError):
        vault.put("openai", "api_key", "")
    vault.put("openai", "api_key", SECRET)
    p = tmp_path / "s" / "providers.bin"
    data = json.loads(p.read_text())
    data["entries"]["openai"] = "***not base64***"
    p.write_text(json.dumps(data))
    with pytest.raises(sec.ReauthRequired):
        vault.get("openai")


@win_only
def test_secrets_dir_is_restricted_to_the_user(tmp_path):
    vault = sec.Vault(tmp_path / "secrets" / "providers.bin")             # harden_acl on
    vault.put("openai", "api_key", SECRET)
    listing = subprocess.run(["icacls", str(tmp_path / "secrets")], capture_output=True, text=True).stdout
    assert "BUILTIN\\Users" not in listing and "Everyone" not in listing and "Authenticated Users" not in listing
    assert "NT AUTHORITY\\SYSTEM" in listing
    assert vault.get("openai")["secret"] == SECRET                        # owner still has access

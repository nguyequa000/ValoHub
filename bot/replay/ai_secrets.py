"""DPAPI-sealed provider credential vault (Windows; stdlib + ctypes only).

File ``<data_root>/secrets/providers.bin`` is JSON ``{"version":1,"entries":{<provider id>:<b64 blob>}}``
where every blob is ``CryptProtectData(json record, entropy = ENTROPY_BASE + provider id,
CRYPTPROTECT_UI_FORBIDDEN)``.  Written only by the Python sidecar, temp file + ``os.replace``.
A blob that no longer decrypts (other user/machine, tampering) is deleted and surfaces as
:class:`ReauthRequired`; there is never a plaintext fallback.  Secrets are never returned by any
public listing helper - callers get ``configured`` and ``last4``.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

from . import redact as _redact

LOG = logging.getLogger("bot.replay.ai_secrets")

ENTROPY_BASE = b"ValoHub/providers/v1|"
CRYPTPROTECT_UI_FORBIDDEN = 0x1
FILE_VERSION = 1


class SecretsError(Exception):
    """Vault-level failure (never carries secret material)."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


class SecretsUnavailable(SecretsError):
    def __init__(self, message: str = "DPAPI is only available on Windows") -> None:
        super().__init__("secrets_unavailable", message)


class ReauthRequired(SecretsError):
    def __init__(self, provider_id: str) -> None:
        super().__init__("reauth_required", f"stored credential for {provider_id} could not be unsealed")
        self.provider_id = provider_id


def _entropy(provider_id: str) -> bytes:
    return ENTROPY_BASE + provider_id.encode("utf-8")


# ----------------------------------------------------------------------------- DPAPI

def dpapi_available() -> bool:
    return sys.platform == "win32"


def _blob_api():
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    blob_p = ctypes.POINTER(DATA_BLOB)
    for name in ("CryptProtectData", "CryptUnprotectData"):
        fn = getattr(crypt32, name)
        fn.argtypes = [blob_p, ctypes.c_void_p, blob_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, blob_p]
        fn.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    return ctypes, DATA_BLOB, crypt32, kernel32


def _crypt(data: bytes, entropy: bytes, *, protect: bool) -> bytes:
    if not dpapi_available():
        raise SecretsUnavailable()
    ctypes, DATA_BLOB, crypt32, kernel32 = _blob_api()

    def make(raw: bytes):
        buf = ctypes.create_string_buffer(raw, max(len(raw), 1))
        blob = DATA_BLOB(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        return blob, buf

    source, keep1 = make(data)
    ent, keep2 = make(entropy)
    out = DATA_BLOB()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    ok = fn(ctypes.byref(source), None, ctypes.byref(ent), None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out))
    del keep1, keep2
    if not ok:
        raise SecretsError("dpapi_failed", f"DPAPI call failed (winerror {ctypes.get_last_error()})")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


def dpapi_protect(data: bytes, entropy: bytes) -> bytes:
    return _crypt(data, entropy, protect=True)


def dpapi_unprotect(blob: bytes, entropy: bytes) -> bytes:
    return _crypt(blob, entropy, protect=False)


# ----------------------------------------------------------------------------- ACL

def restrict_to_user(directory: Path) -> bool:
    """Best effort: grant the current user + SYSTEM full control, then drop inherited ACEs."""
    if sys.platform != "win32":
        try:
            os.chmod(directory, 0o700)
            return True
        except OSError:
            return False
    user = os.environ.get("USERNAME") or ""
    domain = os.environ.get("USERDOMAIN") or ""
    if not user:
        return False
    account = f"{domain}\\{user}" if domain else user
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    def icacls(*args: str) -> bool:
        try:
            done = subprocess.run(["icacls", str(directory), *args], capture_output=True, timeout=15,
                                  creationflags=flags, check=False)
            return done.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    if not icacls("/grant:r", f"{account}:(OI)(CI)F", "*S-1-5-18:(OI)(CI)F"):
        return False
    return icacls("/inheritance:r")


# ----------------------------------------------------------------------------- vault

class Vault:
    """Per-provider sealed records.  ``protect``/``unprotect`` are injectable for tests."""

    def __init__(self, path: Path | str, *, protect: Callable[[bytes, bytes], bytes] | None = None,
                 unprotect: Callable[[bytes, bytes], bytes] | None = None, harden_acl: bool = True) -> None:
        self.path = Path(path)
        self._protect = protect or dpapi_protect
        self._unprotect = unprotect or dpapi_unprotect
        self._harden = harden_acl
        self._lock = threading.RLock()

    # -- file io
    def _read(self) -> dict:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError:
            return {}
        try:
            data = json.loads(raw)
            entries = data["entries"]
            if data.get("version") != FILE_VERSION or not isinstance(entries, dict):
                raise ValueError("shape")
            return {k: v for k, v in entries.items() if isinstance(k, str) and isinstance(v, str)}
        except (ValueError, KeyError, TypeError):
            LOG.warning("provider vault unreadable; deleting it (re-authentication required)")
            self._drop_file()
            return {}

    def _drop_file(self) -> None:
        try:
            self.path.unlink()
        except OSError:
            pass

    def _write(self, entries: dict) -> None:
        if not entries:
            self._drop_file()
            return
        directory = self.path.parent
        fresh = not directory.exists()
        directory.mkdir(parents=True, exist_ok=True)
        if fresh and self._harden:
            restrict_to_user(directory)
        payload = json.dumps({"version": FILE_VERSION, "entries": entries}, separators=(",", ":"))
        tmp = self.path.with_name(self.path.name + f".{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, self.path)
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass

    # -- api
    def put(self, provider_id: str, kind: str, secret: str) -> None:
        if not isinstance(secret, str) or not secret:
            raise SecretsError("empty_secret")
        record = json.dumps({"kind": kind, "secret": secret, "saved_at": int(time.time())}).encode("utf-8")
        blob = self._protect(record, _entropy(provider_id))
        _redact.register_secret(secret)
        with self._lock:
            entries = self._read()
            entries[provider_id] = base64.b64encode(blob).decode("ascii")
            self._write(entries)

    def get(self, provider_id: str) -> dict | None:
        """``{"kind","secret","saved_at"}`` or None.  Undecryptable entry -> deleted + ReauthRequired."""
        with self._lock:
            entries = self._read()
            encoded = entries.get(provider_id)
            if encoded is None:
                return None
            try:
                record = json.loads(self._unprotect(base64.b64decode(encoded, validate=True),
                                                    _entropy(provider_id)).decode("utf-8"))
                if not isinstance(record, dict) or not isinstance(record.get("secret"), str):
                    raise ValueError("record shape")
            except SecretsUnavailable:
                raise
            except Exception:  # noqa: BLE001 - tamper / other user / bad blob: purge, never plaintext
                entries.pop(provider_id, None)
                self._write(entries)
                LOG.warning("stored credential for %s could not be unsealed; removed", provider_id)
                raise ReauthRequired(provider_id) from None
            _redact.register_secret(record["secret"])
            return record

    def delete(self, provider_id: str) -> bool:
        with self._lock:
            entries = self._read()
            existed = entries.pop(provider_id, None) is not None
            if existed:
                self._write(entries)
            return existed

    def ids(self) -> list[str]:
        with self._lock:
            return sorted(self._read())

    def describe(self, provider_id: str) -> dict:
        """Public, secret-free view: ``{"configured", "last4", "kind", "reauth_required"}``."""
        try:
            record = self.get(provider_id)
        except ReauthRequired:
            return {"configured": False, "last4": "", "kind": None, "reauth_required": True}
        if record is None:
            return {"configured": False, "last4": "", "kind": None, "reauth_required": False}
        secret = record["secret"]
        return {"configured": True, "last4": secret[-4:] if len(secret) >= 12 else "", "kind": record.get("kind"),
                "reauth_required": False}

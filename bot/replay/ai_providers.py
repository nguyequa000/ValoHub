"""AI provider registry, verify-then-save client and chat helpers (aiohttp; no new dependencies).

* Compiled registry - cloud base URLs are code, not configuration (``custom`` is the only
  user-supplied URL and is restricted to loopback / RFC1918 literals).
* Every outbound call: catalog host only, ``allow_redirects=False``, ``trust_env=False``, TLS verify,
  bounded timeout and body size, credential attached only to the provider's own host.
* Failures are ``ProviderError(code, status, request_id)``; **no upstream body is ever echoed**.
* Cloud use needs a stored consent record ``{provider, consented_at, policy_version, hosts}``;
  otherwise ``consent_required``.  ``VALO_OFFLINE=1`` stays the default - the per-provider consent
  record is the only thing that lets a cloud provider be contacted.
* Secrets live in :mod:`bot.replay.ai_secrets` (DPAPI); responses carry ``configured`` / ``last4`` only.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import re
import ssl
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Mapping
from urllib.parse import urlsplit

import aiohttp

from . import redact as _redact
from .ai_secrets import ReauthRequired, SecretsError, SecretsUnavailable, Vault
from .offline import is_loopback_host

_redact.install_log_redaction()
LOG = logging.getLogger("bot.replay.ai_providers")

POLICY_VERSION = 1
ANTHROPIC_VERSION = "2023-06-01"
USER_AGENT = "ValoHub/0.3 (+local)"
VERIFY_TIMEOUT_S = 15.0
CHAT_TIMEOUT_S = 120.0
LOCAL_PROBE_TIMEOUT_S = 1.5
MAX_BODY_BYTES = 4 * 1024 * 1024
MAX_MODELS = 1000
MAX_MESSAGES = 64
MAX_CHARS = 400_000
MAX_OUT_TOKENS = 8192
_KEY_RE = re.compile(r"^[\x21-\x7e]{8,512}$")
_REQ_ID_RE = re.compile(r"^[A-Za-z0-9._:\-]{1,64}$")
_MODEL_GONE = ("not found", "does not exist", "not_found", "decommission", "unknown model", "no such model",
               "model_not_found", "is not supported")

# code -> (HTTP status served to the renderer, fixed human text).  Upstream text is never forwarded.
CODES: dict[str, tuple[int, str]] = {
    "invalid_key": (422, "The provider rejected the key (401)."),
    "forbidden": (422, "The provider refused access for this key (403)."),
    "rate_limited": (429, "The provider is rate limiting this key (429)."),
    "unreachable": (502, "The provider could not be reached."),
    "model_gone": (422, "The selected model is not available from this provider."),
    "upstream_error": (502, "The provider returned an unexpected response."),
    "consent_required": (428, "Enable this provider first: cloud use needs your explicit consent."),
    "reauth_required": (409, "The stored credential is no longer valid; connect the provider again."),
    "not_configured": (409, "This provider is not connected."),
    "oauth_unsupported": (400, "This provider does not offer OAuth sign-in."),
    "invalid_request": (400, "The request is not valid."),
    "no_key_required": (400, "This provider does not use an API key."),
    "unknown_provider": (404, "Unknown provider."),
    "secrets_unavailable": (503, "Secure credential storage is unavailable on this system."),
    "no_local_model": (503, "No local model server answered."),
    "flow_not_found": (404, "No such sign-in flow."),
}


class ProviderError(Exception):
    """``{code, status, request_id}`` - the only error shape that ever leaves this module."""

    def __init__(self, code: str, status: int | None = None, request_id: str | None = None, *,
                 detail: str = "", extra: dict | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.request_id = request_id
        self.detail = detail          # short internal tag (e.g. "redirect_blocked"); not upstream text
        self.extra = extra or {}

    @property
    def http_status(self) -> int:
        return CODES.get(self.code, (500, ""))[0]

    def payload(self) -> dict:
        body = {"error": self.code, "code": self.code, "status": self.status, "request_id": self.request_id,
                "message": CODES.get(self.code, (500, "Provider error."))[1]}
        if self.detail:
            body["detail"] = self.detail
        body.update(self.extra)
        return body


# ------------------------------------------------------------------ registry

@dataclass(frozen=True, eq=False)
class Provider:
    id: str
    label: str
    kind: str                         # "local" | "cloud" | "custom"
    base_url: str | None
    auth: str                         # "oauth_pkce" | "api_key" | "none"
    auth_modes: tuple = ()
    models_path: str = "/models"
    chat_path: str = "/chat/completions"
    verify_path: str | None = None    # extra key proof on the same base (OpenRouter /key)
    verify_url: str | None = None     # absolute native listing (Gemini)
    key_style: str = "bearer"         # "bearer" | "anthropic"
    extra_headers: tuple = ()
    max_tokens_param: str = "max_tokens"
    console_url: str | None = None
    data_note: str = ""
    oauth: Mapping[str, Any] | None = None
    allow_http_loopback: bool = False  # tests only; never set in REGISTRY

    @property
    def host(self) -> str | None:
        return urlsplit(self.base_url).hostname if self.base_url else None


OPENROUTER_OAUTH = {
    "authorize_url": "https://openrouter.ai/auth",
    "token_path": "/auth/keys",          # relative to base_url -> https://openrouter.ai/api/v1/auth/keys
    "revoke_path": None,                 # no documented revocation endpoint (oauth-verification.md §4)
    "code_challenge_method": "S256",
}

REGISTRY: dict[str, Provider] = {p.id: p for p in (
    Provider("local", "On this PC", "local", None, "none", ("none",)),
    Provider("openrouter", "OpenRouter", "cloud", "https://openrouter.ai/api/v1", "oauth_pkce",
             ("oauth_pkce", "api_key"), verify_path="/key", console_url="https://openrouter.ai/keys",
             extra_headers=(("X-Title", "ValoHub"),), oauth=OPENROUTER_OAUTH,
             data_note="Prompts go to OpenRouter and the model provider you pick."),
    Provider("openai", "OpenAI", "cloud", "https://api.openai.com/v1", "api_key", ("api_key",),
             max_tokens_param="max_completion_tokens", console_url="https://platform.openai.com/api-keys"),
    Provider("anthropic", "Anthropic", "cloud", "https://api.anthropic.com/v1", "api_key", ("api_key",),
             key_style="anthropic", extra_headers=(("anthropic-version", ANTHROPIC_VERSION),),
             console_url="https://console.anthropic.com/settings/keys"),
    Provider("gemini", "Google Gemini", "cloud", "https://generativelanguage.googleapis.com/v1beta/openai",
             "api_key", ("api_key",), verify_url="https://generativelanguage.googleapis.com/v1beta/models",
             console_url="https://aistudio.google.com/app/apikey",
             data_note="Free-tier content may be used by Google to improve its products."),
    Provider("custom", "Custom OpenAI-compatible", "custom", None, "api_key", ("api_key", "none")),
)}

LOCAL_ENDPOINTS = ("llamacpp", "lmstudio", "ollama")


def classify_endpoint(url: str) -> str:
    """``"local"`` for loopback hosts, ``"cloud"`` otherwise (``https://ollama.com`` is cloud)."""
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return "cloud"
    return "local" if is_loopback_host(host) else "cloud"


def _private_literal(host: str | None) -> bool:
    if not host:
        return False
    if is_loopback_host(host):
        return True
    try:
        addr = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False                       # hostnames other than localhost: rebinding risk -> refused
    if addr.is_link_local or addr.is_multicast or addr.is_unspecified:
        return False
    return addr.is_private and not addr.is_reserved


def validate_custom_base(url) -> str:
    """Normalized base for the ``custom`` provider or ``ProviderError(invalid_request)``."""
    text = url.strip() if isinstance(url, str) else ""
    try:
        parts = urlsplit(text)
        host = parts.hostname
        parts.port  # noqa: B018 - validates
    except ValueError:
        raise ProviderError("invalid_request", detail="bad_url") from None
    if parts.scheme not in ("http", "https") or not host:
        raise ProviderError("invalid_request", detail="scheme")
    if "@" in parts.netloc or parts.query or parts.fragment:
        raise ProviderError("invalid_request", detail="url_parts")
    if not _private_literal(host):
        raise ProviderError("invalid_request", detail="custom_must_be_loopback_or_private")
    return text.rstrip("/")


def _public_secret_view(vault_view: dict) -> dict:
    return {"configured": vault_view["configured"], "last4": vault_view["last4"]}


# ------------------------------------------------------------------ settings (no secrets)

class SettingsStore:
    """``<data_root>/ai_providers.json``: default selection, consent records, cached model ids, health."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()

    def load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        for key in ("consents", "models", "health", "custom"):
            if not isinstance(data.get(key), dict):
                data[key] = {}
        return data

    def update(self, mutate: Callable[[dict], None]) -> dict:
        with self._lock:
            data = self.load()
            mutate(data)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".ai_providers.", suffix=".tmp", dir=str(self.path.parent))
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    json.dump(data, stream, separators=(",", ":"))
                os.replace(tmp, self.path)
            finally:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
            return data


# ------------------------------------------------------------------ http core

def _request_id(headers) -> str | None:
    for name in ("x-request-id", "request-id", "x-goog-request-id", "cf-ray"):
        value = headers.get(name)
        if isinstance(value, str) and _REQ_ID_RE.match(value):
            return value
    return None


def _hint_model_gone(raw: bytes) -> bool:
    low = raw[:4096].decode("utf-8", "ignore").lower()
    return "model" in low and any(token in low for token in _MODEL_GONE)


def _classify(status: int, raw: bytes = b"", *, chat: bool = False) -> str:
    if status == 401:
        return "invalid_key"
    if status == 403:
        return "forbidden"
    if status == 429:
        return "rate_limited"
    if chat and status in (400, 404, 410) and _hint_model_gone(raw):
        return "model_gone"
    if status in (408, 502, 503, 504):
        return "unreachable"
    return "upstream_error"


async def _read_limited(response: aiohttp.ClientResponse, limit: int = MAX_BODY_BYTES) -> bytes:
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.content.iter_chunked(64 * 1024):
        total += len(chunk)
        if total > limit:
            raise ProviderError("upstream_error", response.status, _request_id(response.headers), detail="body_too_large")
        chunks.append(chunk)
    return b"".join(chunks)


def _model_entry(item: dict, *, endpoint: str | None = None) -> dict | None:
    ident = item.get("id") or item.get("name") or item.get("model")
    if not isinstance(ident, str) or not ident or len(ident) > 200:
        return None
    entry: dict[str, Any] = {"id": ident}
    for key_in, key_out in (("context_length", "context"), ("max_input_tokens", "context"),
                            ("inputTokenLimit", "context")):
        value = item.get(key_in)
        if isinstance(value, int) and not isinstance(value, bool) and key_out not in entry:
            entry[key_out] = value
    label = item.get("display_name") or item.get("displayName")
    if isinstance(label, str) and label:
        entry["label"] = label[:120]
    if endpoint:
        entry["endpoint"] = endpoint
    return entry


def parse_models(payload, *, endpoint: str | None = None) -> list[dict]:
    """OpenAI ``{data:[{id}]}``, Ollama ``{models:[{name}]}`` or a bare list -> ``[{id, ...}]``."""
    if isinstance(payload, dict):
        items = payload.get("data")
        if items is None:
            items = payload.get("models")
    else:
        items = payload
    out: list[dict] = []
    seen: set[str] = set()
    for item in (items if isinstance(items, list) else [])[:MAX_MODELS]:
        entry = _model_entry(item, endpoint=endpoint) if isinstance(item, dict) else None
        if entry and entry["id"] not in seen:
            seen.add(entry["id"])
            out.append(entry)
    return out


def _parse_gemini_models(payload) -> list[dict]:
    out: list[dict] = []
    items = payload.get("models") if isinstance(payload, dict) else None
    for item in (items if isinstance(items, list) else [])[:MAX_MODELS]:
        if not isinstance(item, dict):
            continue
        methods = item.get("supportedGenerationMethods")
        if isinstance(methods, list) and "generateContent" not in methods:
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name:
            continue
        entry = _model_entry({**item, "id": name.split("/", 1)[-1]})
        if entry:
            out.append(entry)
    return out


class _Http:
    """One hardened request path.  ``provider`` fixes the only host a credential may reach."""

    def __init__(self, provider: Provider, base_url: str | None = None) -> None:
        self.provider = provider
        self.base = (base_url or provider.base_url or "").rstrip("/")

    # -- target policy
    def check(self, url: str) -> str:
        provider = self.provider
        try:
            parts = urlsplit(url)
            host, port = parts.hostname, parts.port
        except ValueError:
            raise ProviderError("invalid_request", detail="bad_url") from None
        if not host or "@" in parts.netloc or parts.fragment:
            raise ProviderError("invalid_request", detail="bad_url")
        if provider.kind == "cloud":
            allowed = {provider.host}
            if provider.verify_url:
                allowed.add(urlsplit(provider.verify_url).hostname)
            loop_ok = provider.allow_http_loopback and is_loopback_host(host)
            if host not in allowed or (parts.scheme != "https" and not loop_ok):
                raise ProviderError("invalid_request", detail="host_not_in_catalog")
            if port not in (None, 443) and not loop_ok:
                raise ProviderError("invalid_request", detail="port_not_allowed")
        elif provider.kind == "custom":
            validate_custom_base(f"{parts.scheme}://{parts.netloc}")
        else:  # local
            if parts.scheme not in ("http", "https") or not is_loopback_host(host):
                raise ProviderError("invalid_request", detail="not_loopback")
        return url

    def headers(self, secret: str | None, *, native: bool = False) -> dict:
        provider = self.provider
        headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
        headers.update(dict(provider.extra_headers))
        if secret:
            if native and provider.id == "gemini":
                headers["x-goog-api-key"] = secret
            else:
                headers["Authorization"] = f"Bearer {secret}"
                if provider.key_style == "anthropic":
                    headers["x-api-key"] = secret
        return headers

    def session(self, timeout: float) -> aiohttp.ClientSession:
        return aiohttp.ClientSession(trust_env=False, timeout=aiohttp.ClientTimeout(total=timeout),
                                     connector=aiohttp.TCPConnector(ssl=True))

    async def json(self, method: str, url: str, secret: str | None, *, body: dict | None = None,
                   timeout: float = VERIFY_TIMEOUT_S, chat: bool = False, native: bool = False) -> Any:
        self.check(url)
        headers = self.headers(secret, native=native)
        try:
            async with self.session(timeout) as session:
                async with session.request(method, url, json=body, headers=headers, allow_redirects=False) as response:
                    raw = await _read_limited(response)
                    rid = _request_id(response.headers)
                    status = response.status
        except ProviderError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ssl.SSLError):
            raise ProviderError("unreachable") from None
        if 300 <= status < 400:
            raise ProviderError("upstream_error", status, rid, detail="redirect_blocked")
        if status >= 400:
            raise ProviderError(_classify(status, raw, chat=chat), status, rid)
        try:
            return json.loads(raw.decode("utf-8")) if raw.strip() else {}
        except ValueError:
            raise ProviderError("upstream_error", status, rid, detail="bad_json") from None

    @asynccontextmanager
    async def stream(self, url: str, secret: str | None, body: dict, timeout: float):
        self.check(url)
        headers = self.headers(secret)
        headers["Accept"] = "text/event-stream"
        session = self.session(timeout)
        try:
            try:
                response = await session.post(url, json=body, headers=headers, allow_redirects=False)
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ssl.SSLError):
                raise ProviderError("unreachable") from None
            try:
                rid = _request_id(response.headers)
                if response.status >= 300:
                    raw = b""
                    try:
                        raw = await _read_limited(response, 64 * 1024)
                    except ProviderError:
                        pass
                    code = "upstream_error" if response.status < 400 else _classify(response.status, raw, chat=True)
                    raise ProviderError(code, response.status, rid,
                                        detail="redirect_blocked" if response.status < 400 else "")
                yield response, rid
            finally:
                response.release()
        finally:
            await session.close()


def _chat_body(provider: Provider, model: str, messages: list[dict], *, max_tokens: int | None,
               temperature: float | None, stream: bool = False) -> dict:
    body: dict[str, Any] = {"model": model, "messages": messages}
    if max_tokens is not None:
        body[provider.max_tokens_param] = int(max_tokens)
    if temperature is not None:
        body["temperature"] = float(temperature)
    if stream:
        body["stream"] = True
    return body


def clean_messages(messages) -> list[dict]:
    if not isinstance(messages, list) or not messages or len(messages) > MAX_MESSAGES:
        raise ProviderError("invalid_request", detail="messages")
    out, total = [], 0
    for item in messages:
        if not isinstance(item, dict) or item.get("role") not in ("system", "user", "assistant") \
                or not isinstance(item.get("content"), str) or not item["content"]:
            raise ProviderError("invalid_request", detail="message_shape")
        total += len(item["content"])
        out.append({"role": item["role"], "content": item["content"]})
    if total > MAX_CHARS:
        raise ProviderError("invalid_request", detail="too_large")
    return out


def extract_content(data) -> str:
    choices = data.get("choices") if isinstance(data, dict) else None
    first = choices[0] if isinstance(choices, list) and choices else None
    message = first.get("message") if isinstance(first, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if content is None and isinstance(first, dict) and isinstance(first.get("text"), str):
        content = first["text"]
    return content if isinstance(content, str) else ""


_NON_CHAT = ("embed", "whisper", "tts", "dall-e", "image", "moderation", "audio", "transcribe", "realtime", "rerank")


def pick_test_model(models: list[dict]) -> str | None:
    for entry in models:
        if not any(token in entry["id"].lower() for token in _NON_CHAT):
            return entry["id"]
    return models[0]["id"] if models else None


# ------------------------------------------------------------------ service

class AiService:
    """Everything the routes need.  ``registry`` is injectable so tests can aim at a stub host."""

    def __init__(self, data_root: Path | Callable[[], Path], *, registry: Mapping[str, Provider] | None = None,
                 vault: Vault | None = None, environ: Mapping[str, str] | None = None,
                 opener: Callable[[str], Any] | None = None) -> None:
        self._root_fn = data_root if callable(data_root) else (lambda root=Path(data_root): root)
        self.registry = dict(REGISTRY if registry is None else registry)
        self._vault = vault
        self._vault_root: Path | None = None
        self._environ = environ
        self.opener = opener
        self._lock = threading.RLock()

    # -- storage
    @property
    def root(self) -> Path:
        return Path(self._root_fn())

    @property
    def vault(self) -> Vault:
        if self._vault is not None:
            return self._vault
        return Vault(self.root / "secrets" / "providers.bin")

    @property
    def settings(self) -> SettingsStore:
        return SettingsStore(self.root / "ai_providers.json")

    def provider(self, provider_id: str) -> Provider:
        provider = self.registry.get(provider_id) if isinstance(provider_id, str) else None
        if provider is None:
            raise ProviderError("unknown_provider")
        return provider

    def env(self) -> Mapping[str, str]:
        return os.environ if self._environ is None else self._environ

    # -- consent
    def has_consent(self, provider: Provider) -> bool:
        record = self.settings.load()["consents"].get(provider.id)
        return isinstance(record, dict) and record.get("policy_version") == POLICY_VERSION

    def needs_consent(self, provider: Provider, base_url: str | None = None) -> bool:
        if provider.kind == "local":
            return False
        if provider.kind == "custom":
            return not is_loopback_host(urlsplit(base_url or self.custom_base() or "").hostname)
        return True

    def grant_consent(self, provider: Provider) -> dict:
        if provider.kind == "local":
            raise ProviderError("invalid_request", detail="local_needs_no_consent")
        host = provider.host or urlsplit(self.custom_base() or "").hostname or "custom"
        record = {"provider": provider.id, "consented_at": int(time.time()), "policy_version": POLICY_VERSION,
                  "hosts": [host]}
        self.settings.update(lambda data: data["consents"].__setitem__(provider.id, record))
        return record

    def revoke_consent(self, provider: Provider) -> None:
        self.settings.update(lambda data: data["consents"].pop(provider.id, None))

    def require_consent(self, provider: Provider, base_url: str | None = None) -> None:
        if self.needs_consent(provider, base_url) and not self.has_consent(provider):
            raise ProviderError("consent_required")

    # -- custom base
    def custom_base(self) -> str | None:
        value = self.settings.load()["custom"].get("base_url")
        return value if isinstance(value, str) and value else None

    def _base_for(self, provider: Provider) -> str | None:
        return self.custom_base() if provider.kind == "custom" else provider.base_url

    # -- secrets
    def secret_for(self, provider: Provider) -> tuple[str | None, str | None]:
        """``(secret, kind)``; ``(None, None)`` when nothing is stored.  Purges + raises on a bad blob."""
        try:
            record = self.vault.get(provider.id)
        except ReauthRequired:
            self._set_health(provider.id, "reauth_required", None)
            raise ProviderError("reauth_required") from None
        except SecretsUnavailable:
            raise ProviderError("secrets_unavailable") from None
        return (record["secret"], record.get("kind")) if record else (None, None)

    def _set_health(self, provider_id: str, code: str | None, status: int | None) -> None:
        entry = {"ok": code is None, "code": code, "status": status, "at": int(time.time())}

        def mutate(data: dict) -> None:
            data["health"][provider_id] = entry
        try:
            self.settings.update(mutate)
        except OSError:
            LOG.warning("could not persist provider health for %s", provider_id)

    def _fail(self, provider: Provider, error: ProviderError, kind: str | None = None) -> ProviderError:
        """Record health; an OAuth-issued credential that is rejected is purged (-> reauth_required)."""
        LOG.info("provider %s failed: code=%s status=%s request_id=%s", provider.id, error.code, error.status,
                 error.request_id)
        if error.code == "invalid_key" and kind == "oauth":
            self.vault.delete(provider.id)
            self._set_health(provider.id, "reauth_required", error.status)
            return ProviderError("reauth_required", error.status, error.request_id)
        self._set_health(provider.id, error.code, error.status)
        return error

    # -- verification
    async def _verify_models(self, provider: Provider, secret: str | None, base: str) -> list[dict]:
        http = _Http(provider, base)
        if provider.id == "openrouter" and provider.verify_path:
            await http.json("GET", base + provider.verify_path, secret)              # auth proof (models is public)
        if provider.verify_url:                                                     # Gemini native listing
            payload = await http.json("GET", provider.verify_url + "?pageSize=1000", secret, native=True)
            models = _parse_gemini_models(payload)
        else:
            models = parse_models(await http.json("GET", base + provider.models_path, secret))
        return models

    async def _chat_ping(self, provider: Provider, secret: str | None, base: str, model: str) -> None:
        body = _chat_body(provider, model, [{"role": "user", "content": "ping"}], max_tokens=1, temperature=0)
        await _Http(provider, base).json("POST", base + provider.chat_path, secret, body=body,
                                         timeout=VERIFY_TIMEOUT_S * 2, chat=True)

    async def connect_key(self, provider_id: str, key: str | None, *, consent: bool = False,
                          base_url: str | None = None, model: str | None = None, test_chat: bool = False) -> dict:
        """Verify-then-save.  Nothing is stored unless the provider accepted the key."""
        provider = self.provider(provider_id)
        if provider.kind == "local" or "api_key" not in provider.auth_modes:
            raise ProviderError("no_key_required")
        base = provider.base_url or ""
        if provider.kind == "custom":
            base = validate_custom_base(base_url if base_url is not None else self.custom_base())
        if key is not None and not (isinstance(key, str) and _KEY_RE.match(key.strip())):
            raise ProviderError("invalid_request", detail="key_format")
        key = key.strip() if isinstance(key, str) else None
        if provider.kind != "custom" and not key:
            raise ProviderError("invalid_request", detail="key_required")
        if self.needs_consent(provider, base):
            if consent:
                self.grant_consent(provider)
            elif not self.has_consent(provider):
                raise ProviderError("consent_required")
        if key:
            _redact.register_secret(key)
        try:
            models = await self._verify_models(provider, key, base)
            if test_chat:
                chosen = model or pick_test_model(models)
                if not chosen:
                    raise ProviderError("model_gone", detail="no_model")
                await self._chat_ping(provider, key, base, chosen)
        except ProviderError as error:
            raise self._fail(provider, error) from None
        try:
            if key:
                self.vault.put(provider.id, "api_key", key)
        except SecretsUnavailable:
            raise ProviderError("secrets_unavailable") from None
        except SecretsError:
            raise ProviderError("secrets_unavailable") from None

        def mutate(data: dict) -> None:
            data["models"][provider.id] = models
            data["health"][provider.id] = {"ok": True, "code": None, "status": 200, "at": int(time.time())}
            if provider.kind == "custom":
                data["custom"]["base_url"] = base
        self.settings.update(mutate)
        return await self.describe(provider.id, probe=False)

    async def test(self, provider_id: str, model: str | None = None) -> dict:
        provider = self.provider(provider_id)
        started = time.monotonic()
        if provider.kind == "local":
            chosen = model
            listing = await self.local_models()
            if not listing["models"]:
                raise ProviderError("no_local_model")
            chosen = chosen or listing["models"][0]["id"]
            endpoint = self._pick_endpoint(listing, chosen)
            await self._chat_ping(provider, endpoint["key"], endpoint["base"], chosen)
            return {"ok": True, "provider": provider_id, "model": chosen, "endpoint": endpoint["id"],
                    "latency_ms": int((time.monotonic() - started) * 1000)}
        secret, kind, base = self._credentials(provider)
        try:
            models = await self._verify_models(provider, secret, base)
            chosen = model or self._default_model_for(provider.id) or pick_test_model(models)
            if not chosen:
                raise ProviderError("model_gone", detail="no_model")
            await self._chat_ping(provider, secret, base, chosen)
        except ProviderError as error:
            raise self._fail(provider, error, kind) from None
        self._set_health(provider.id, None, 200)
        return {"ok": True, "provider": provider_id, "model": chosen,
                "latency_ms": int((time.monotonic() - started) * 1000)}

    def _credentials(self, provider: Provider) -> tuple[str | None, str | None, str]:
        base = self._base_for(provider)
        if not base:
            raise ProviderError("not_configured")
        self.require_consent(provider, base)
        secret, kind = self.secret_for(provider)
        if secret is None and provider.kind != "custom":
            raise ProviderError("not_configured")
        return secret, kind, base

    def _default_model_for(self, provider_id: str) -> str | None:
        default = self.settings.load().get("default")
        if isinstance(default, dict) and default.get("provider") == provider_id and isinstance(default.get("model"), str):
            return default["model"]
        return None

    async def models(self, provider_id: str, refresh: bool = False) -> dict:
        provider = self.provider(provider_id)
        if provider.kind == "local":
            listing = await self.local_models()
            return {"provider": provider_id, "models": listing["models"], "cached": False,
                    "endpoints": listing["endpoints"]}
        cached = self.settings.load()["models"].get(provider_id)
        if cached and not refresh:
            return {"provider": provider_id, "models": cached, "cached": True}
        secret, kind, base = self._credentials(provider)
        try:
            models = await self._verify_models(provider, secret, base)
        except ProviderError as error:
            err = self._fail(provider, error, kind)
            err.extra["models"] = cached or []
            raise err from None
        self.settings.update(lambda data: data["models"].__setitem__(provider_id, models))
        self._set_health(provider_id, None, 200)
        return {"provider": provider_id, "models": models, "cached": False}

    # -- local endpoints
    def _local_candidates(self) -> list[dict]:
        from . import p2_review                                        # lazy: heavy imports
        out = []
        for candidate in p2_review.probe_candidates("auto", self.env()):
            base = candidate["base"].rstrip("/")
            if candidate["kind"] == "ollama" and not base.endswith("/v1"):
                base += "/v1"
            out.append({"id": candidate["backend"], "label": {"llamacpp": "llama.cpp", "lmstudio": "LM Studio",
                                                              "ollama": "Ollama"}.get(candidate["backend"], candidate["backend"]),
                        "base": base, "key": candidate.get("api_key"), "kind": classify_endpoint(base),
                        "pin": candidate.get("model")})
        return out

    async def local_models(self, timeout: float = LOCAL_PROBE_TIMEOUT_S) -> dict:
        provider = self.registry["local"]
        candidates = self._local_candidates()

        async def probe(candidate: dict) -> dict:
            view = {"id": candidate["id"], "label": candidate["label"], "kind": candidate["kind"],
                    "host": urlsplit(candidate["base"]).netloc, "reachable": None, "models": []}
            if candidate["kind"] != "local":
                view["blocked"] = "cloud_host"          # never contacted: ollama.com & co are cloud, not local
                return view
            try:
                payload = await _Http(provider, candidate["base"]).json(
                    "GET", candidate["base"] + provider.models_path, candidate["key"], timeout=timeout)
                view["models"] = parse_models(payload, endpoint=candidate["id"])
                view["reachable"] = True
            except ProviderError as error:
                view["reachable"] = False
                view["code"] = error.code
            return view

        views = await asyncio.gather(*(probe(c) for c in candidates))
        by_id = {c["id"]: c for c in candidates}
        models = [m for view in views for m in view["models"]]
        for view in views:
            view["model_count"] = len(view["models"])
            view["base_known"] = view["id"] in by_id
        return {"endpoints": [{k: v for k, v in view.items() if k != "models"} for view in views],
                "models": models, "_candidates": by_id}

    def _pick_endpoint(self, listing: dict, model: str | None, endpoint: str | None = None) -> dict:
        by_id = listing["_candidates"]
        for view in listing["endpoints"]:
            if view.get("reachable") and (endpoint in (None, "", "auto") or endpoint == view["id"]):
                if model is None or any(m["id"] == model and m.get("endpoint") == view["id"] for m in listing["models"]):
                    return by_id[view["id"]]
        raise ProviderError("no_local_model")

    # -- default
    def set_default(self, provider_id: str, model: str | None) -> dict:
        provider = self.provider(provider_id)
        if model is not None and not (isinstance(model, str) and 0 < len(model) <= 200):
            raise ProviderError("invalid_request", detail="model")
        if provider.kind != "local":
            secret, _ = self.secret_for(provider)
            if secret is None and not (provider.kind == "custom" and self.custom_base()):
                raise ProviderError("not_configured")
        entry = {"provider": provider_id, "model": model}
        self.settings.update(lambda data: data.__setitem__("default", entry))
        return entry

    def get_default(self) -> dict | None:
        default = self.settings.load().get("default")
        return default if isinstance(default, dict) and default.get("provider") in self.registry else None

    # -- disconnect
    async def disconnect(self, provider_id: str, revoker: Callable | None = None) -> dict:
        provider = self.provider(provider_id)
        if provider.kind == "local":
            raise ProviderError("invalid_request", detail="local_cannot_disconnect")
        revoked: bool | None = None
        revoke_supported = bool(provider.oauth and provider.oauth.get("revoke_path"))
        record = None
        try:
            record = self.vault.get(provider.id)
        except (ReauthRequired, SecretsUnavailable):
            record = None
        if record and record.get("kind") == "oauth" and revoke_supported and revoker is not None:
            try:
                revoked = bool(await revoker(provider, record["secret"]))
            except Exception as exc:                                    # noqa: BLE001 - best effort
                LOG.info("revoke for %s failed: %s", provider.id, _redact.safe_exc(exc))
                revoked = False
        existed = self.vault.delete(provider.id)

        def mutate(data: dict) -> None:
            data["models"].pop(provider.id, None)
            data["health"].pop(provider.id, None)
            data["consents"].pop(provider.id, None)
            if provider.kind == "custom":
                data["custom"].pop("base_url", None)
            default = data.get("default")
            if isinstance(default, dict) and default.get("provider") == provider.id:
                data["default"] = None
        self.settings.update(mutate)
        return {"ok": True, "provider": provider.id, "deleted": existed, "revoked": revoked,
                "revoke_supported": revoke_supported}

    # -- views
    async def describe(self, provider_id: str, *, probe: bool = True) -> dict:
        provider = self.provider(provider_id)
        settings = self.settings.load()
        default = self.get_default()
        view: dict[str, Any] = {
            "id": provider.id, "label": provider.label, "kind": "cloud" if provider.kind == "cloud" else provider.kind,
            "auth": provider.auth, "auth_modes": list(provider.auth_modes),
            "oauth_supported": bool(provider.oauth), "console_url": provider.console_url,
            "data_note": provider.data_note, "host": provider.host,
            "default": bool(default and default["provider"] == provider.id),
            "default_model": default["model"] if default and default["provider"] == provider.id else None,
            "consented": provider.kind == "local" or not self.needs_consent(provider, self.custom_base())
            or self.has_consent(provider),
        }
        if provider.kind == "local":
            if probe:
                listing = await self.local_models()
                view["endpoints"] = listing["endpoints"]
                view["models"] = listing["models"]
                connected = any(e.get("reachable") for e in listing["endpoints"])
            else:
                view["endpoints"], view["models"], connected = [], settings["models"].get("local", []), False
            view.update({"configured": True, "last4": "", "credential": None,
                         "state": "connected" if connected else "unreachable",
                         "state_text": "Connected" if connected else "No local model server answered"})
            return view
        try:
            raw = self.vault.describe(provider.id)
        except SecretsUnavailable:
            raw = {"configured": False, "last4": "", "kind": None, "reauth_required": False}
        secret_view = _public_secret_view(raw)
        base = self._base_for(provider)
        configured = secret_view["configured"] or (provider.kind == "custom" and bool(base))
        health = settings["health"].get(provider.id) or {}
        if raw.get("reauth_required") or health.get("code") == "reauth_required":
            state = "reauth_required"
        elif not configured:
            state = health.get("code") if health.get("ok") is False and health.get("code") else "not_connected"
        elif health.get("ok") is False:
            state = health.get("code") or "unreachable"
        else:
            state = "connected"
        models = settings["models"].get(provider.id, [])
        view.update({"configured": configured, "last4": secret_view["last4"], "credential": raw.get("kind"),
                     "state": state, "state_text": _state_text(state, health), "models": models,
                     "model_count": len(models)})
        if provider.kind == "custom":
            view["base_url"] = base
        return view

    async def list_providers(self, *, probe: bool = True) -> dict:
        views = [await self.describe(pid, probe=probe) for pid in self.registry]
        return {"providers": views, "default": self.get_default(), "offline_default": True,
                "policy_version": POLICY_VERSION}

    # -- chat
    def _resolve_target(self, provider_id: str | None, model: str | None) -> tuple[Provider, str | None]:
        if not provider_id:
            default = self.get_default()
            if not default:
                raise ProviderError("not_configured")
            provider_id, model = default["provider"], model or default.get("model")
        return self.provider(provider_id), model

    async def chat(self, messages, *, provider_id: str | None = None, model: str | None = None,
                   max_tokens: int | None = None, temperature: float | None = None,
                   endpoint: str | None = None) -> dict:
        provider, model = self._resolve_target(provider_id, model)
        messages = clean_messages(messages)
        max_tokens = None if max_tokens is None else max(1, min(int(max_tokens), MAX_OUT_TOKENS))
        secret, kind, base, model, key_endpoint = await self._chat_target(provider, model, endpoint)
        body = _chat_body(provider, model, messages, max_tokens=max_tokens, temperature=temperature)
        try:
            data = await _Http(provider, base).json("POST", base + provider.chat_path, secret, body=body,
                                                    timeout=CHAT_TIMEOUT_S, chat=True)
        except ProviderError as error:
            raise self._fail(provider, error, kind) from None
        content = extract_content(data)
        if not content.strip():
            raise ProviderError("upstream_error", detail="empty_content")
        usage = data.get("usage") if isinstance(data, dict) and isinstance(data.get("usage"), dict) else {}
        out = {"provider": provider.id, "model": model, "content": content,
               "usage": {k: v for k, v in usage.items() if k in ("prompt_tokens", "completion_tokens", "total_tokens")
                         and isinstance(v, int)}}
        if key_endpoint:
            out["endpoint"] = key_endpoint
        return out

    async def _chat_target(self, provider: Provider, model: str | None, endpoint: str | None):
        if provider.kind == "local":
            listing = await self.local_models()
            if not listing["models"]:
                raise ProviderError("no_local_model")
            chosen = self._pick_endpoint(listing, model or None, endpoint)
            if not model:
                model = next((m["id"] for m in listing["models"] if m.get("endpoint") == chosen["id"]), None)
                if chosen.get("pin") and any(m["id"] == chosen["pin"] and m.get("endpoint") == chosen["id"]
                                             for m in listing["models"]):
                    model = chosen["pin"]
            if not model:
                raise ProviderError("no_local_model")
            return chosen["key"], None, chosen["base"], model, chosen["id"]
        secret, kind, base = self._credentials(provider)
        if not model:
            cached = self.settings.load()["models"].get(provider.id) or []
            model = (cached[0]["id"] if cached else None)
            if not model:
                raise ProviderError("invalid_request", detail="model_required")
        return secret, kind, base, model, None

    @asynccontextmanager
    async def chat_stream(self, messages, *, provider_id: str | None = None, model: str | None = None,
                          max_tokens: int | None = None, temperature: float | None = None,
                          endpoint: str | None = None) -> AsyncIterator[Any]:
        """Yields an async iterator of SSE ``data:`` payload strings (``[DONE]`` included, comments dropped)."""
        provider, model = self._resolve_target(provider_id, model)
        messages = clean_messages(messages)
        max_tokens = None if max_tokens is None else max(1, min(int(max_tokens), MAX_OUT_TOKENS))
        secret, kind, base, model, key_endpoint = await self._chat_target(provider, model, endpoint)
        body = _chat_body(provider, model, messages, max_tokens=max_tokens, temperature=temperature, stream=True)
        http = _Http(provider, base)
        try:
            async with http.stream(base + provider.chat_path, secret, body, CHAT_TIMEOUT_S) as (response, rid):
                async def events() -> AsyncIterator[str]:
                    try:
                        async for line in response.content:
                            text = line.decode("utf-8", "replace").strip()
                            if text.startswith("data:"):
                                yield text[5:].strip()
                    except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
                        raise ProviderError("unreachable", None, rid) from None
                yield StreamHandle(events(), {"provider": provider.id, "model": model, "request_id": rid,
                                              "endpoint": key_endpoint})
        except ProviderError as error:
            raise self._fail(provider, error, kind) from None

    # -- OAuth credential storage (used by ai_oauth)
    def store_oauth_key(self, provider: Provider, key: str) -> None:
        try:
            self.vault.put(provider.id, "oauth", key)
        except SecretsError:
            raise ProviderError("secrets_unavailable") from None
        self._set_health(provider.id, None, 200)


@dataclass
class StreamHandle:
    events: Any
    meta: dict

    def __aiter__(self):
        return self.events.__aiter__()


def _state_text(state: str, health: dict) -> str:
    status = health.get("status")
    return {
        "connected": "Connected", "not_connected": "Not connected",
        "invalid_key": "Invalid key (401)", "forbidden": "Forbidden (403)", "rate_limited": "Rate limited (429)",
        "unreachable": "Unreachable", "model_gone": "Model unavailable", "reauth_required": "Sign in again",
        "upstream_error": f"Unexpected response ({status})" if status else "Unexpected response",
    }.get(state, state)

"""Offline policy: LLM endpoints and models must be local (loopback) in the desktop build."""
from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit


class OfflineViolation(ValueError):
    """A non-local endpoint or cloud model was requested."""


def is_loopback_host(host) -> bool:
    if not isinstance(host, str):
        return False
    host = host.strip().strip("[]").rstrip(".").lower()
    if not host:
        return False
    if host == "localhost" or host.endswith(".localhost"):
        return True
    host = host.split("%", 1)[0]
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    return addr.is_loopback


def assert_local_url(url) -> str:
    """Return ``url`` when it is http(s) to a loopback host without userinfo; else raise."""
    text = url.strip() if isinstance(url, str) else ""
    try:
        parts = urlsplit(text)
        host = parts.hostname
        parts.port  # noqa: B018 - validates the port
    except ValueError as exc:
        raise OfflineViolation(f"offline mode: unparseable URL {text[:80]!r}") from exc
    if parts.scheme not in ("http", "https"):
        raise OfflineViolation(f"offline mode: scheme {parts.scheme!r} is not allowed ({text[:80]!r})")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise OfflineViolation(f"offline mode: credentials in URL are not allowed (host {host!r})")
    if not is_loopback_host(host):
        raise OfflineViolation(f"offline mode: host {host!r} is not a loopback address")
    return text


def reject_cloud_model(name) -> str:
    """Ollama ``*-cloud`` / ``:cloud`` models are remote-executed; refuse them."""
    text = name.strip() if isinstance(name, str) else ""
    low = text.lower()
    if low.endswith("-cloud") or low.endswith(":cloud") or ":cloud-" in low or "-cloud:" in low:
        raise OfflineViolation(f"offline mode: cloud model {text!r} is not allowed")
    return text

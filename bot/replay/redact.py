"""Secret redaction for logs and error strings (stdlib only).

``redact(text)`` masks bearer tokens, API-key-looking strings, ``key=value`` pairs for credential
field names, credential query parameters and any literal registered with :func:`register_secret`.
:func:`install_log_redaction` wraps the logging record factory so *every* log record is scrubbed
at the sink, whichever module emitted it.  Idempotent.
"""
from __future__ import annotations

import logging
import re
import threading

MASK = "[REDACTED]"

_FIELD = (r"authorization|x-api-key|x-goog-api-key|api[-_]?key|access[-_]?token|refresh[-_]?token|"
          r"client[-_]?secret|code[-_]?verifier|password|passwd|secret|token|cookie")
_RULES: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"(?i)\b(" + _FIELD + r")\b([\"']?\s*[:=]\s*[\"']?)(bearer\s+)?[^\s\"',;&}\]]+"),
     r"\1\2" + MASK),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=\-]{6,}"), "Bearer " + MASK),
    (re.compile(r"(?i)([?&](?:key|api_key|apikey|access_token|token|code|state|code_verifier)=)[^&\s\"']+"),
     r"\1" + MASK),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{12,}"), MASK),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{20,}"), MASK),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), MASK),
)

_lock = threading.Lock()
_literals: list[str] = []
_MAX_LITERALS = 64
_installed = False


def register_secret(value) -> None:
    """Remember a live secret so it is masked verbatim wherever it shows up."""
    if not isinstance(value, str) or len(value) < 6:
        return
    with _lock:
        if value in _literals:
            return
        _literals.append(value)
        del _literals[:-_MAX_LITERALS]


def clear_secrets() -> None:
    with _lock:
        _literals.clear()


def redact(text) -> str:
    out = text if isinstance(text, str) else str(text)
    with _lock:
        literals = list(_literals)
    for literal in literals:
        if literal in out:
            out = out.replace(literal, MASK)
    for pattern, repl in _RULES:
        out = pattern.sub(repl, out)
    return out


def safe_exc(exc: BaseException, limit: int = 200) -> str:
    """``TypeName: message`` with secrets masked and the length capped."""
    return redact(f"{type(exc).__name__}: {exc}")[:limit]


class RedactingFilter(logging.Filter):
    """Handler/logger filter form of :func:`redact` (for explicitly attached sinks)."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact(record.getMessage())
            record.args = None
        except Exception:  # noqa: BLE001 - never break logging
            pass
        return True


def install_log_redaction() -> None:
    global _installed
    with _lock:
        if _installed:
            return
        _installed = True
    previous = logging.getLogRecordFactory()

    def factory(*args, **kwargs):
        record = previous(*args, **kwargs)
        try:
            record.msg = redact(record.getMessage())
            record.args = None
        except Exception:  # noqa: BLE001
            pass
        return record

    logging.setLogRecordFactory(factory)

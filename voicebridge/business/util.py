"""Small cross-cutting helpers: clock, JSON, and redaction.

Redaction is applied *before* anything is stored or logged. The control plane
never persists a credential, bearer token or provider key, so an audit export
or a debug dump cannot leak one later.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

_REDACTED = "***REDACTED***"
_SENSITIVE_PARTS = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "auth",
    "cookie",
    "private_key",
    "client_secret",
    "credential",
)
_BEARER_RE = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+")
_PROVIDER_KEY_RE = re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{6,}\b")


def now_iso(ts: float | None = None) -> str:
    """UTC ISO-8601 with millisecond precision and a ``Z`` suffix."""
    moment = (
        datetime.fromtimestamp(ts, tz=timezone.utc)
        if ts is not None
        else datetime.now(timezone.utc)
    )
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def json_dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def json_loads(text: str | None, default: Any) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return default


_NON_SECRET_TOKEN_KEYS = frozenset(
    {
        "max_tokens",
        "max_tokens_per_turn",
        "tokens",
        "token_count",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "estimated_prompt_tokens",
        "projected_tokens",
        "remaining_tokens",
    }
)


def _sensitive_key(key: str) -> bool:
    flat = key.lower().replace("-", "_")
    if flat in _NON_SECRET_TOKEN_KEYS:
        return False
    return any(part in flat for part in _SENSITIVE_PARTS)


def _redact_text(value: str) -> str:
    value = _BEARER_RE.sub(rf"\1{_REDACTED}", value)
    return _PROVIDER_KEY_RE.sub(_REDACTED, value)


def redact(value: Any) -> Any:
    """Return a copy with credential-shaped keys and values masked.

    Keys whose name contains a sensitive fragment have their whole value
    replaced. String values elsewhere are scrubbed for bearer tokens and
    provider-key shapes. Everything else is passed through unchanged.
    """
    if isinstance(value, dict):
        return {
            str(key): (
                _REDACTED
                if _sensitive_key(str(key))
                else redact(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def require_text(value: Any, label: str, *, max_len: int = 4096) -> str:
    """Return stripped text or raise ``ValueError`` (caller maps to a code)."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    clean = value.strip()
    if len(clean) > max_len:
        raise ValueError(f"{label} is longer than {max_len} characters")
    return clean

"""Shared contract for the read-only Apple connectors.

The Apple connectors expose narrow read-only operations (list calendar events,
list/search contacts, read a daily health summary, list music tracks). They
never write to a framework, never open a network connection, and never invent
data when a platform or framework is unavailable.

On macOS the caller injects a read-only framework adapter (an EventKit,
Contacts, HealthKit or MusicKit-shaped double). On any other platform every
operation raises :class:`AppleConnectorError` with ``not_supported`` before the
adapter is touched, naming the host platform and the required macOS framework.

Field shape and platform notes were informed by OpenJarvis ``0583901``
(``src/openjarvis/connectors/apple_*.py``, Apache-2.0). No OpenJarvis code is
copied; the implementation, bounding, redaction and typed refusals are Qubit's.
"""

from __future__ import annotations

import math
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional

APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)
SUPPORTED_PLATFORM = "darwin"

REASON_NOT_SUPPORTED = "not_supported"
REASON_INVALID_REQUEST = "invalid_request"
REASON_CONNECTOR_NOT_CONFIGURED = "connector_not_configured"
REASON_INVALID_RESPONSE = "invalid_response"
REASON_TOOL_ERROR = "tool_error"

MAX_DETAIL_CHARS = 300
MAX_ID_CHARS = 128
MAX_TEXT_CHARS = 200
MAX_LONG_TEXT_CHARS = 300


def current_platform() -> str:
    return sys.platform


def _bounded(value: object, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _redact(text: str) -> str:
    try:
        from agent.redact import redact_for_egress
    except Exception:
        return text
    try:
        return redact_for_egress(text)
    except Exception:
        return "[redacted]"


def bounded_redacted_detail(text: object, limit: int = MAX_DETAIL_CHARS) -> str:
    collapsed = _bounded(text, limit * 4)
    return _redact(collapsed)[:limit]


def bounded_text(value: object, limit: int = MAX_TEXT_CHARS) -> str:
    return _bounded(value, limit)


def bounded_values(
    values: object,
    *,
    limit: int,
    chars: int = MAX_TEXT_CHARS,
) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple, set, frozenset)):
        return ()
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if isinstance(value, Mapping):
            value = (
                value.get("value")
                or value.get("address")
                or value.get("number")
                or value.get("url")
            )
        text = bounded_text(value, chars)
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= limit:
            break
    return tuple(out)


def coerce_limit(value: object, *, default: int, maximum: int) -> int:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    if number < 1:
        return default
    return min(number, maximum)


def coerce_days(value: object, *, default: int, maximum: int, minimum: int = 0) -> int:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(minimum, min(number, maximum))


def coerce_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value == 1
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "y"}
    return False


def _finite_number(value: object) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return number


def coerce_nonnegative_float(value: object) -> Optional[float]:
    number = _finite_number(value)
    if number is None or number < 0:
        return None
    return number


def coerce_nonnegative_int(value: object) -> Optional[int]:
    number = _finite_number(value)
    if number is None or number < 0:
        return None
    return int(number)


def apple_timestamp(value: object) -> Optional[datetime]:
    number = _finite_number(value)
    if number is None:
        return None
    try:
        return APPLE_EPOCH + timedelta(seconds=number)
    except (OverflowError, ValueError):
        return None


def normalize_timestamp(value: object) -> str:
    """Return an ISO-8601 UTC string from a datetime, ISO string or Apple seconds."""
    if isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc).isoformat()
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return ""
        try:
            moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return text[:64]
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc).isoformat()
    moment = apple_timestamp(value)
    return moment.isoformat() if moment is not None else ""


def normalize_date(value: object) -> str:
    if isinstance(value, str):
        text = value.strip()
        if len(text) >= 10:
            return text[:10]
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    moment = apple_timestamp(value)
    return moment.strftime("%Y-%m-%d") if moment is not None else ""


class AppleConnectorError(Exception):
    """A typed refusal with no raw framework payload, path or personal data."""

    def __init__(
        self,
        reason: str,
        detail: str = "",
        *,
        platform: str = "",
        required_framework: str = "",
        status: Optional[int] = None,
    ) -> None:
        self.reason = reason
        self.detail = bounded_redacted_detail(detail)
        self.platform = platform
        self.required_framework = required_framework
        self.status = status
        super().__init__(self.detail or self.reason)

    def as_extra(self) -> dict[str, Any]:
        extra: dict[str, Any] = {"reason": self.reason}
        if self.platform:
            extra["platform"] = self.platform
        if self.required_framework:
            extra["required_framework"] = self.required_framework
        if self.status is not None:
            extra["status"] = self.status
        return extra


def ensure_supported(
    *,
    platform: Optional[str],
    required_framework: str,
    connector: str,
) -> None:
    host = platform or current_platform()
    if host == SUPPORTED_PLATFORM:
        return
    raise AppleConnectorError(
        REASON_NOT_SUPPORTED,
        f"{connector} requires macOS ({SUPPORTED_PLATFORM}); this host is "
        f"{host or 'unknown'}. Required macOS framework: {required_framework}.",
        platform=host or "unknown",
        required_framework=required_framework,
    )


def require_backend(
    backend: Any,
    *,
    platform: Optional[str],
    required_framework: str,
    connector: str,
) -> Any:
    ensure_supported(
        platform=platform,
        required_framework=required_framework,
        connector=connector,
    )
    if backend is None:
        raise AppleConnectorError(
            REASON_CONNECTOR_NOT_CONFIGURED,
            f"{connector} has no {required_framework} adapter configured on this "
            "macOS host; inject a read-only framework adapter.",
            platform=platform or current_platform(),
            required_framework=required_framework,
        )
    return backend


def backend_rows(
    payload: object,
    *,
    connector: str,
    required_framework: str,
    platform: Optional[str],
) -> list[Mapping[str, Any]]:
    if isinstance(payload, (str, bytes, bytearray, Mapping)) or payload is None:
        raise AppleConnectorError(
            REASON_INVALID_RESPONSE,
            f"{connector} adapter returned an unexpected payload; expected a "
            "sequence of read-only rows.",
            platform=platform or current_platform(),
            required_framework=required_framework,
        )
    try:
        rows = list(payload)  # type: ignore[arg-type]
    except TypeError:
        raise AppleConnectorError(
            REASON_INVALID_RESPONSE,
            f"{connector} adapter returned an unreadable payload.",
            platform=platform or current_platform(),
            required_framework=required_framework,
        ) from None
    return [row for row in rows if isinstance(row, Mapping)]


def map_backend_error(
    exc: Exception,
    *,
    connector: str,
    required_framework: str,
    platform: Optional[str],
) -> AppleConnectorError:
    if isinstance(exc, AppleConnectorError):
        return exc
    return AppleConnectorError(
        REASON_TOOL_ERROR,
        f"{connector} framework call failed ({type(exc).__name__}).",
        platform=platform or current_platform(),
        required_framework=required_framework,
    )

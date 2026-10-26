"""Redaction and bounding for trace text.

Every prompt/response/tool-name string passes through here **before** it is
written. The secret scrub is the same one used for egress
(``agent.redact.redact_for_egress``), plus the monitoring PII pass; it fails
closed, so a broken redactor stores the unavailable marker instead of raw
credentials. Truncation happens after redaction.
"""
from __future__ import annotations

from agent.monitoring.redaction import redact_for_export
from agent.redact import REDACTION_UNAVAILABLE

ELLIPSIS = "…"


def redact_excerpt(raw: object, limit: int) -> str:
    """Redact then length-bound *raw* to at most *limit* characters."""
    limit = max(1, int(limit))
    try:
        text = redact_for_export(str(raw or "")) or ""
    except Exception:  # noqa: BLE001 - fail closed
        return REDACTION_UNAVAILABLE
    if len(text) > limit:
        text = text[: max(0, limit - 1)] + ELLIPSIS
    return text


def redact_tool_name(raw: object, limit: int = 120) -> str:
    """Tool names are bounded too; an empty name is not stored as a placeholder."""
    text = redact_excerpt(raw, limit)
    return "" if text in {"", "[redacted]"} else text


__all__ = ["ELLIPSIS", "redact_excerpt", "redact_tool_name"]

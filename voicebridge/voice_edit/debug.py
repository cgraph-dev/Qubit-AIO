"""Owner-controlled verbose logging for the voice surface.

By default the voice journal records only metadata (char counts, digests,
engine/model, error codes) and never the spoken or selected text. When the owner
turns on ``voice_edit.debug_log`` the *spoken transcript*, the derived command,
and the dictation payload are recorded too, so a failure can be diagnosed from
the log.

This is opt-in and local-only: records go to the same ``events.jsonl`` (no
egress) and still pass through the central redaction choke point in
``logging_setup.log_event``, so secret-shaped values are masked. Turn it off to
return to metadata-only logging.
"""
from __future__ import annotations

from typing import Any, Dict


def enabled(cfg: Any) -> bool:
    try:
        return bool(cfg.get("voice_edit", "debug_log", default=False))
    except Exception:
        return False


def text_fields(cfg: Any, **texts: Any) -> Dict[str, Any]:
    """Return ``{name: value}`` only when verbose logging is enabled."""
    if not enabled(cfg):
        return {}
    out: Dict[str, Any] = {}
    for name, value in texts.items():
        if value is None:
            continue
        out[name] = str(value)[:2000]
    return out


__all__ = ["enabled", "text_fields"]

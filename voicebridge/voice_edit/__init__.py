"""Qubit system-wide voice typing and selected-text editing.

Additive package: it reuses the existing STT WebSocket authority, the existing
provider gateway (model router), the existing e-stop/audit/session systems, and
the existing desktop X11 clipboard/xdotool tooling. It adds no listener, no
second speech backend, no second model router, no second session or audit store.

Public entry points:
* :mod:`voicebridge.voice_edit.parser` -- spoken command -> typed command.
* :mod:`voicebridge.voice_edit.prompts` -- versioned default prompt registry.
* :mod:`voicebridge.voice_edit.transform` -- model routing (local/API).
* :mod:`voicebridge.voice_edit.desktop` -- selection capture / insertion.
* :mod:`voicebridge.voice_edit.service` -- capture -> transform -> apply.
* :mod:`voicebridge.voice_edit.settings` -- prompt/routing settings.
* :mod:`voicebridge.voice_edit.routes` -- authenticated HTTP handlers.
"""
from __future__ import annotations

__all__ = ["parser", "prompts", "transform", "desktop", "service", "settings",
           "routes", "schema"]

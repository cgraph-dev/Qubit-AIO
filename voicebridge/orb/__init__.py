"""OS-level voice orb package (Tk overlay, X11).

Presentation only: the orb polls the local voice-edit HTTP surface
(read-only GETs, same loopback convention as the desktop helper) and
renders one of four waveform states on a borderless always-on-top
window. It never opens the microphone (no device contention with the
real STT capture), never writes settings, and never takes keyboard
focus.

State semantics mirror the dashboard widget
(``frontend/qubit-web/src/lib/voice/waveform-state.ts``): priority
listening > thinking > speaking > idle, unknown/malformed → idle.
"""
from __future__ import annotations

__all__ = ["ORB_VERSION"]
ORB_VERSION = "1.0.0"

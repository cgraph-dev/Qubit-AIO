"""Push-to-talk hotkey listener (pynput, X11).

Hold-to-talk semantics:
  * hold_key down      -> PTT started
  * hold_key release   -> PTT ended (recording continues briefly for the tail)
  * tap_cancel key     -> cancel/stop (stop speaking, abort task)

Runs in its own thread and pushes events into an asyncio.Queue via
call_soon_threadsafe. If pynput cannot connect (no DISPLAY / Wayland without
X11 bridge), the listener degrades to None and the orchestrator offers a
manual Enter-toggle mode instead — the bridge must keep working headless.
"""
from __future__ import annotations

import threading
from typing import Optional

from .logging_setup import get_logger, log_event

log = get_logger()


class HotkeyListener:
    """Thin wrapper; emits ('ptt_start'|'ptt_end'|'cancel'|'quit', key)."""

    def __init__(self, hold_key: str = "ctrl_r", tap_cancel: str = "esc",
                 alt_hold_key: str = "") -> None:
        self.hold_key = hold_key
        self.alt_hold_key = alt_hold_key
        self.tap_cancel = tap_cancel
        self.on_event = None          # callback(event: str, key)
        self._listener = None
        self._held = set()
        self._thread: Optional[threading.Thread] = None

    def start(self, on_event) -> bool:
        """Start the listener; returns False if pynput/X is unavailable."""
        self.on_event = on_event
        try:
            from pynput import keyboard
        except Exception as exc:
            log.warning("hotkeys disabled (pynput/X unavailable: %s) — use manual PTT mode", exc)
            return False
        try:
            self._listener = keyboard.Listener(on_press=self._press, on_release=self._release)
            self._listener.daemon = True
            self._listener.start()
            return True
        except Exception as exc:
            log.warning("hotkeys disabled (listener failed: %s)", exc)
            self._listener = None
            return False

    def stop(self) -> None:
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None

    @staticmethod
    def _key_name(key) -> str:
        return getattr(key, "name", None) or getattr(key, "char", None) or str(key)

    def _press(self, key) -> None:
        name = self._key_name(key)
        if name in (self.hold_key, self.alt_hold_key):
            if name not in self._held:
                self._held.add(name)
                self._emit("ptt_start", name)
        elif name == self.tap_cancel:
            self._emit("cancel", name)

    def _release(self, key) -> None:
        name = self._key_name(key)
        if name in (self.hold_key, self.alt_hold_key):
            self._held.discard(name)
            if not self._held:
                self._emit("ptt_end", name)

    def _emit(self, event: str, key: str) -> None:
        if self.on_event:
            try:
                self.on_event(event, key)
            except Exception:
                log.exception("hotkey callback failed")
        log_event("hotkey", event=event, key=key)

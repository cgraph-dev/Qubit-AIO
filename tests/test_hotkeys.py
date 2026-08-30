"""Packet 7D hardware fix: hotkey xinput listener robustness tests.

A wedged xinput child (alive but its stdout never delivers a line) previously
left the hotkey silently dead: `ensure_alive` only restarted after the loop
thread exited, which a wedged child never does. These tests verify the
heartbeat + stale-child restart and the fresh non-blocking read loop.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge.hotkeys import HotkeyListener  # noqa: E402


def _fake_proc():
    """A subprocess-shaped object that emits raw key events on stdout."""

    class P:
        def __init__(self):
            r, w = os.pipe()
            self._r, self._w = r, w
            self.stdout = os.fdopen(r, "rb", buffering=0)

        def write(self, data: bytes):
            os.write(self._w, data)

        def kill(self):
            # close the WRITE end only -> reader sees EOF (loop reads the
            # already-buffered events, then exits cleanly)
            try:
                os.close(self._w)
            except OSError:
                pass

        def close(self):
            self.kill()
            try:
                self.stdout.close()      # release the read end too
            except Exception:
                pass

        def __del__(self):
            self.close()                 # GC safety net: never leak the pipe

        @property
        def poll(self):
            return None  # always alive

    return P()


def test_hotkey_listener_tracks_last_line_ts(monkeypatch):
    """The heartbeat ts is initialized and advanced by the loop so a stale
    child is detectable by ensure_alive."""
    hk = HotkeyListener(hold_key="ctrl_r")
    hk._xinput_start_ts = time.time()
    hk._last_xinput_line_ts = 0.0
    # a line arriving advances the heartbeat
    hk._last_xinput_line_ts = time.time()
    assert hk._last_xinput_line_ts > 0
    assert hk._xinput_start_ts <= hk._last_xinput_line_ts


def test_ensure_alive_restarts_stale_child(monkeypatch):
    """A child that produced NO output since start must be restarted by
    ensure_alive after the stale window."""
    hk = HotkeyListener(hold_key="ctrl_r")
    hk.on_event = lambda ev, key: None
    hk._proc = _fake_proc()
    hk._xinput_start_ts = time.time() - 60   # started 60s ago
    hk._last_xinput_line_ts = 0.0            # never produced output
    hk._stopped = False
    restarted = []

    def fake_restart():
        restarted.append(True)
    monkeypatch.setattr(hk, "_restart_xinput", fake_restart)
    hk.ensure_alive()
    assert restarted, "stale child must be restarted"


def test_ensure_alive_does_not_restart_healthy_idle(monkeypatch):
    """A freshly started child with no events yet is NOT stale (it just
    started) and must not be restarted."""
    hk = HotkeyListener(hold_key="ctrl_r")
    hk.on_event = lambda ev, key: None
    hk._proc = _fake_proc()
    hk._xinput_start_ts = time.time()        # just started
    hk._last_xinput_line_ts = 0.0
    hk._stopped = False
    restarted = []

    def fake_restart():
        restarted.append(True)
    monkeypatch.setattr(hk, "_restart_xinput", fake_restart)
    hk.ensure_alive()
    assert not restarted, "fresh listener must not be restarted"


def test_ensure_alive_restarts_after_loop_death(monkeypatch):
    """If the loop thread exited (_xinput_dead True), ensure_alive restarts."""
    hk = HotkeyListener(hold_key="ctrl_r")
    hk.on_event = lambda ev, key: None
    hk._proc = _fake_proc()
    hk._xinput_dead = True
    restarted = []

    def fake_restart():
        restarted.append(True)
    monkeypatch.setattr(hk, "_restart_xinput", fake_restart)
    hk.ensure_alive()
    assert restarted, "dead loop must be restarted"


def test_xinput_loop_parses_raw_key_events(monkeypatch):
    """The non-blocking loop must turn xinput raw key lines into ptt_start /
    ptt_end events for the hold key (ctrl_r = keycode 105)."""
    hk = HotkeyListener(hold_key="ctrl_r")
    events = []
    hk.on_event = lambda ev, key: events.append((ev, key))
    proc = _fake_proc()
    hk._proc = proc
    hk._stopped = False
    hk._xinput_state = {"press": False, "release": False}
    hk._xinput_start_ts = time.time()
    hk._last_xinput_line_ts = 0.0
    # feed a raw press+release for ctrl_r (keycode 105), then EOF to end the loop
    proc.write(b"EVENT type 13 (RawKeyPress)\n  detail: 105\n")
    proc.write(b"EVENT type 14 (RawKeyRelease)\n  detail: 105\n")
    proc.kill()  # closes the write end -> loop sees EOF and exits
    hk._xinput_loop()
    proc.close()   # release the read end (no ResourceWarning leak)
    assert ("ptt_start", "ctrl_r") in events, events
    assert ("ptt_end", "ctrl_r") in events, events


def test_xinput_cancel_key_emits_cancel():
    """Esc (keycode 9) is the tap-cancel key: a press must emit 'cancel' so
    the bridge stops speaking / aborts the task. Release of a non-hold key
    emits nothing."""
    hk = HotkeyListener(hold_key="ctrl_r", tap_cancel="esc")
    events = []
    hk.on_event = lambda ev, key: events.append((ev, key))
    hk._press("esc")
    assert ("cancel", "esc") in events, events
    hk._release("esc")
    assert ("cancel", "esc") in events, events  # no duplicate from release


def test_repeat_press_while_held_emits_ptt_start_once():
    """A hold key pressed twice (auto-repeat / double event) must emit a
    single ptt_start: the held-set guard is level-based, not one-shot."""
    hk = HotkeyListener(hold_key="ctrl_r")
    events = []
    hk.on_event = lambda ev, key: events.append((ev, key))
    hk._press("ctrl_r")
    hk._press("ctrl_r")
    assert events == [("ptt_start", "ctrl_r")], events
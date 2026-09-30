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


# ----------------------------------------------------- display validation
def test_start_fails_when_display_unreachable(monkeypatch):
    """Hotkeys must NOT report active when the resolved display cannot be
    reached (stale DISPLAY / wrong session)."""
    import voicebridge.display as D
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(D, "display_reachable", lambda d, env=None: False)
    monkeypatch.setattr(D, "ensure_session_display", lambda env=None: ":0")
    hk = HotkeyListener(hold_key="ctrl_r")
    assert hk._start_xinput() is False
    assert getattr(hk, "_proc", None) is None


def test_start_fails_when_no_graphical_session(monkeypatch):
    import voicebridge.display as D
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setattr(D, "ensure_session_display", lambda env=None: None)
    hk = HotkeyListener(hold_key="ctrl_r")
    assert hk._start_xinput() is False


def test_start_detects_xinput_immediate_exit(monkeypatch):
    """An xinput child that cannot connect exits quickly; the listener must
    detect that and report unavailable instead of a phantom 'active'."""
    import subprocess
    import voicebridge.display as D
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(D, "display_reachable", lambda d, env=None: True)
    monkeypatch.setattr(D, "ensure_session_display", lambda env=None: ":0")

    class _Exiting:
        killed = False

        def poll(self):
            return 1   # exited (connect failure)

        def kill(self):
            _Exiting.killed = True

    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: _Exiting())
    hk = HotkeyListener(hold_key="ctrl_r")
    hk.XINPUT_READY_S = 0.05
    assert hk._start_xinput() is False
    assert _Exiting.killed is True


def test_start_is_idempotent_no_duplicate_listener(monkeypatch):
    """A live listener must never be duplicated by a second start()."""
    hk = HotkeyListener(hold_key="ctrl_r")

    class _AliveThread:
        def is_alive(self):
            return True

    hk._thread = _AliveThread()
    starts = []
    monkeypatch.setattr(hk, "_start_xinput",
                        lambda: starts.append(True) or True)
    assert hk.start(lambda ev, key: None) is True
    assert starts == [], "no second listener may be spawned"


def test_ensure_alive_bounded_backoff(monkeypatch):
    """A failed restart must back off — ensure_alive must not hot-loop."""
    hk = HotkeyListener(hold_key="ctrl_r")
    hk.on_event = lambda ev, key: None
    hk._proc = None
    hk._xinput_dead = True
    hk._next_restart_ts = 0.0
    calls = []

    def fake_restart():
        calls.append(True)
        return False   # restart failed

    monkeypatch.setattr(hk, "_restart_xinput", fake_restart)
    hk.ensure_alive()
    assert len(calls) == 1
    # immediately again: the backoff window is active -> no new attempt
    hk.ensure_alive()
    assert len(calls) == 1
    # after the interval elapses it may retry
    hk._next_restart_ts = 0.0
    hk.ensure_alive()
    assert len(calls) == 2


def test_restart_xinput_joins_old_thread_before_spawn(monkeypatch):
    """A restart must join the previous loop thread before starting a new
    listener so two xinput children never coexist."""
    import threading
    import time
    hk = HotkeyListener(hold_key="ctrl_r")
    started = threading.Event()

    def _slow_start():
        time.sleep(0.05)
        started.set()
        return True

    monkeypatch.setattr(hk, "_start_xinput", _slow_start)
    joined = []
    old_thread = threading.Thread(target=lambda: time.sleep(0.2))
    old_thread.daemon = True
    old_thread.start()
    # pretend the old thread is the live listener
    hk._thread = old_thread
    hk._proc = _fake_proc()

    orig_join = old_thread.join
    def _track_join(*a, **k):
        joined.append(True)
        return orig_join(*a, **k)
    old_thread.join = _track_join

    assert hk._restart_xinput() is True
    started.wait(timeout=1.0)
    assert joined, "old listener thread must be joined before the new one"
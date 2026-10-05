"""Wake-turn watchdog: a hung turn can never wedge the loop silently.

Journal 2026-10-04 10:17-10:18: three turns reached
``helper.wake.listening`` and the owner heard nothing back. The journal
proves each turn COMPLETED its stage (begin → route → propose/answer →
speak) — the waveform intents fell through to the confirm-gated
task/coding path instead of flipping — but the same junction CAN wedge
silently: the wake worker thread blocks in HTTP (route 120 s / task
180 s / ask 90 s / speak 60 s) with state PROCESSING, and every
following ``on_wake_command`` refuses via ``_open_request`` with only a
``wake.turn_refused`` trace. The owner experiences that as dead turns.

Contract pinned here (mirrors the existing P0 guards: TTL + _finish_error
on every path):
- a worker still stuck past ``voice_edit.wake_turn_timeout_s`` (default
  360 s, generous over the slowest legit path) degrades LOUDLY: typed
  ``wake.turn_timeout`` trace + error status, and the loop frees to IDLE;
- the NEXT turn processes normally (the anti-wedge proof);
- a healthy turn that finishes in time never trips the watchdog.

Hung dependency is a threading.Event double — never a live hang in tests.
No X, mic, socket, or model.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import voicebridge.desktop_helper as dh


class FakeCfg:
    def __init__(self, data=None):
        self._data = data or {"voice_edit": {"enabled": True}}

    def get(self, *path, default=None):
        node = self._data
        for p in path:
            if isinstance(node, dict) and p in node:
                node = node[p]
            else:
                return default
        return node


class FakeOverlay:
    def __init__(self):
        self.statuses = []
        self.previews = []
        self.errors = []

    def set_status(self, state, detail=""):
        self.statuses.append((state, detail))

    def show_preview(self, payload):
        self.previews.append(payload)

    def show_error(self, code, detail):
        self.errors.append((code, detail))

    def close(self):
        pass


class HungClient:
    """route() blocks until released — the hung-dependency double."""

    def __init__(self):
        self.calls = []
        self.release = threading.Event()

    def status(self):
        return {"enabled": True, "estop": False}

    def begin(self):
        self.calls.append(("begin",))
        return {"ok": True, "request_id": "req-hung"}

    def capture(self, rid):
        return {"ok": True, "selection": {
            "available": False, "captured_via": "none", "app_class": "code"}}

    def route(self, rid, transcript):
        self.calls.append(("route", rid, transcript))
        assert self.release.wait(timeout=30), "test hung longer than its own bound"
        return {"ok": True, "mode": "answer", "question": transcript}

    def ask_result(self, text, request_id=""):
        return {"ok": True, "answer": "late answer", "spoken": False}

    def speak(self, text):
        self.calls.append(("speak", text))
        return b""


class FakeDesktop:
    def session(self):
        return {"session_type": "x11", "x11": True, "wayland": False}

    def focused_window(self):
        return {"window_id": "42", "app_class": "code", "app_name": "code",
                "window_title": ""}

    def is_fullscreen(self, window_id=""):
        return False


class FakeMic:
    def start(self):
        pass

    def read_frame(self, timeout=0.2):
        return None

    def stop(self):
        pass


class FakeStt:
    error = None


def _helper(client, timeout_s):
    cfg = FakeCfg({"voice_edit": {"enabled": True,
                                  "wake_turn_timeout_s": timeout_s}})
    return dh.DesktopHelper(
        cfg, client=client, overlay=FakeOverlay(), desktop=FakeDesktop(),
        mic_factory=lambda: FakeMic(), stt_factory=lambda t: FakeStt())


def test_watchdog_frees_hung_turn_loudly():
    """RED: a worker stuck past the timeout must degrade loud + free IDLE."""
    client = HungClient()
    h = _helper(client, timeout_s=0.3)
    h.on_wake_command("show wave form")
    # The worker parks in route(); the watchdog must fire without any join.
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and h.state != h.IDLE:
        time.sleep(0.05)
    assert h.state == h.IDLE, f"watchdog never freed the loop: {h.state}"
    assert ("route", "req-hung", "show wave form") in client.calls
    # Loud, not silent: a typed error surfaced on the status surface.
    surfaced = [(str(c) + " " + str(d)).lower()
                for c, d in h.overlay.errors + h.overlay.statuses]
    assert any("turn_timeout" in s or "timed out" in s for s in surfaced), (
        h.overlay.errors, h.overlay.statuses)
    # Release the hung dependency; the stale worker lands late and harmless.
    client.release.set()
    th = getattr(h, "_wake_thread", None)
    if th is not None:
        th.join(timeout=10)


def test_next_turn_processes_after_watchdog():
    """RED: after a watchdog degrade, the NEXT turn runs normally (anti-wedge)."""
    client = HungClient()
    h = _helper(client, timeout_s=0.3)
    h.on_wake_command("first turn hangs")
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and h.state != h.IDLE:
        time.sleep(0.05)
    assert h.state == h.IDLE
    client.release.set()
    th = getattr(h, "_wake_thread", None)
    if th is not None:
        th.join(timeout=10)
    # Fresh turn on the freed loop: route is consulted again and answers.
    h.on_wake_command("second turn works")
    th2 = getattr(h, "_wake_thread", None)
    if th2 is not None:
        th2.join(timeout=10)
    routes = [c for c in client.calls if c[0] == "route"]
    assert len(routes) == 2, client.calls
    assert routes[1][2] == "second turn works", routes


def test_healthy_turn_never_trips_watchdog():
    """A fast turn must finish with no timeout error (no false positive)."""
    client = HungClient()
    client.release.set()  # never blocks: the turn is instant
    h = _helper(client, timeout_s=5)
    h.on_wake_command("quick question")
    th = getattr(h, "_wake_thread", None)
    if th is not None:
        th.join(timeout=10)
    time.sleep(0.2)  # let any stray watchdog fire if mis-armed
    surfaced = [(str(c) + " " + str(d)).lower()
                for c, d in h.overlay.errors + h.overlay.statuses]
    assert not any("turn_timeout" in s or "timed out" in s for s in surfaced), (
        h.overlay.errors, h.overlay.statuses)
    answered = [c for c in client.calls if c[0] == "route"]
    assert answered, client.calls

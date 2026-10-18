"""Orb STOP -> helper spoke-reply playback stops (9u).

Root cause of the owner's "stop is too slow": the helper blocks in
``_play_with_barge_in`` for the whole reply and the orb's stop press only
cancelled the *staged request* (``VoiceEditService.cancel``), never the
player subprocess. This pins the explicit stop path:

- ``DesktopHelper.stop_speaking`` terminates the live player and sets
  ``_barge_interrupted``; the owning ``_speak`` emits the canonical
  ``interrupt`` / ``playback.stopped`` / ``response.done`` events, so the
  orb's FSM ends at once. It works with barge-in enabled OR disabled.
- SIGUSR2 -> ``on_orb_stop`` is the orb seam (SIGUSR1 still toggles capture).
- ``OrbClient.stop_speaking`` sends SIGUSR2 to the cmdline-validated helper
  pid and refuses a foreign one.
- ``VoiceEditService.cancel`` best-effort signals the helper, so a cancel
  is never a no-op for audio, while keeping its signature/shape stable.

Hermetic: no X, no mic, no audio, no model. Fakes stand in for the player,
mic and OS signal delivery.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import voicebridge.desktop_helper as dh
from voicebridge.orb import client as orb_client
from voicebridge.voice_edit.schema import InsertionResult, SelectionSnapshot
from voicebridge.voice_edit.service import VoiceEditService
from voicebridge.voice_edit.transform import TransformEngine


# ---------------------------------------------------------------------------
# shared fakes
# ---------------------------------------------------------------------------
class FakeCfg:
    def __init__(self, data=None):
        self._data = data or {}

    def get(self, *path, default=None):
        node = self._data
        for p in path:
            if isinstance(node, dict) and p in node:
                node = node[p]
            else:
                return default
        return node


def _cfg(**ve):
    base = {"enabled": True, "speak_answers": True}
    base.update(ve)
    return FakeCfg({"voice_edit": base})


class FakeOverlay:
    def __init__(self):
        self.statuses = []

    def set_status(self, state, detail=""):
        self.statuses.append((state, detail))

    def show_preview(self, payload):
        pass

    def show_error(self, code, detail):
        pass

    def append_history(self, line):
        pass


class FakeDesktop:
    def session(self):
        return {"session_type": "x11", "x11": True}

    def _tool(self, name):
        return False

    def is_fullscreen(self, wid=""):
        return False


class FakeClient:
    def __init__(self, wav=b"RIFF" + b"\x00" * 64):
        self._wav = wav

    def speak(self, text):
        return self._wav


class FakeMic:
    """Never yields a confident frame; blocks the barge loop until stopped."""

    frame_ms = 300

    def __init__(self):
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def read_frame(self, timeout=0.5):
        time.sleep(0.01)
        return None


class StopProc:
    """Player that stays alive until terminated/killed (real Popen shape)."""

    def __init__(self):
        self.alive = True
        self.terminated = False
        self.killed = False

    def poll(self):
        return None if self.alive else 0

    def terminate(self):
        self.terminated = True
        self.alive = False

    def kill(self):
        self.killed = True
        self.alive = False

    def wait(self, timeout=None):
        if self.alive:
            raise subprocess.TimeoutExpired(cmd="paplay", timeout=timeout or 0)
        return 0


class SelfEndingProc(StopProc):
    """Player that finishes after a few polls, like a short real reply."""

    def __init__(self, polls=3):
        super().__init__()
        self._left = polls

    def poll(self):
        if not self.alive:
            return 0
        self._left -= 1
        if self._left <= 0:
            self.alive = False
            return 0
        return None

    def wait(self, timeout=None):
        return 0


def _helper(monkeypatch, cfg, mic, procs, proc_cls=StopProc):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/paplay")

    class FakePopen:
        def __init__(self, argv, **kw):
            self._inner = proc_cls()
            procs.append(self._inner)

        def poll(self):
            return self._inner.poll()

        def terminate(self):
            self._inner.terminate()

        def kill(self):
            self._inner.kill()

        def wait(self, timeout=None):
            return self._inner.wait(timeout=timeout)

    monkeypatch.setattr("subprocess.Popen", FakePopen)
    h = dh.DesktopHelper(cfg, client=FakeClient(),
                         overlay=FakeOverlay(),
                         desktop=FakeDesktop(),
                         mic_factory=lambda: mic)
    h._warm_event.set()
    h._warm_state = "skipped"
    return h


def _speak_in_thread(helper, text):
    t = threading.Thread(target=helper._speak, args=(text,), daemon=True)
    t.start()
    deadline = time.time() + 3.0
    while time.time() < deadline and helper._playback_proc is None:
        time.sleep(0.005)
    assert helper._playback_proc is not None, "playback never started"
    return t


def _event_types(helper):
    return [e.get("type") for e in helper.talk_events()]


# ---------------------------------------------------------------------------
# helper: explicit stop terminates playback + emits the stop events
# ---------------------------------------------------------------------------
def test_stop_speaking_barge_in_enabled_terminates_and_emits(monkeypatch):
    mic = FakeMic()
    procs = []
    h = _helper(monkeypatch, _cfg(barge_in_threshold=0.20), mic, procs)
    t = _speak_in_thread(h, "hello there this is a long spoken reply indeed")
    assert h.stop_speaking() is True
    t.join(timeout=3.0)
    assert not t.is_alive()
    assert procs and procs[0].terminated is True
    assert h._speaking is False
    assert h._barge_interrupted is True
    types = _event_types(h)
    assert "interrupt" in types
    assert "playback.stopped" in types
    assert "response.done" in types


def test_stop_speaking_barge_in_disabled_still_terminates(monkeypatch):
    mic = FakeMic()
    procs = []
    h = _helper(monkeypatch, _cfg(barge_in=False), mic, procs)
    t = _speak_in_thread(h, "hello there this is a long spoken reply indeed")
    assert h.stop_speaking() is True
    t.join(timeout=3.0)
    assert not t.is_alive()
    assert procs and procs[0].terminated is True
    assert mic.started is False, "disabled barge-in must not open the mic"


def test_stop_speaking_noop_when_idle_keeps_state(monkeypatch):
    mic = FakeMic()
    h = _helper(monkeypatch, _cfg(), mic, [])
    before = _event_types(h)
    assert h.stop_speaking() is False
    assert h._speaking is False
    assert _event_types(h) == before, "idle stop must emit nothing"


def test_stop_speaking_terminate_error_is_swallowed(monkeypatch):
    h = _helper(monkeypatch, _cfg(), FakeMic(), [])

    class BadProc:
        def terminate(self):
            raise OSError("device gone")

    h._speaking = True
    h._playback_proc = BadProc()
    assert h.stop_speaking() is True  # best-effort, reported as requested
    h._speaking = False
    h._playback_proc = None


def test_on_orb_stop_calls_stop_speaking(monkeypatch):
    h = _helper(monkeypatch, _cfg(), FakeMic(), [])
    calls = []
    monkeypatch.setattr(h, "stop_speaking",
                        lambda: (calls.append(True), True)[1])
    h.on_orb_stop()
    assert calls == [True]


def test_normal_playback_unaffected_after_failed_stop(monkeypatch):
    # A stop with nothing playing must leave the next reply intact.
    mic = FakeMic()
    procs = []
    h = _helper(monkeypatch, _cfg(barge_in=False), mic, procs,
                proc_cls=SelfEndingProc)
    assert h.stop_speaking() is False
    h._speak_stop.clear()
    h._speak("a short spoken reply")
    assert procs and procs[0].terminated is False
    assert h._barge_interrupted is False


# ---------------------------------------------------------------------------
# helper: SIGUSR2 wiring (distinct from the SIGUSR1 capture toggle)
# ---------------------------------------------------------------------------
def test_signal_handlers_install_sigusr1_and_sigusr2(monkeypatch):
    h = _helper(monkeypatch, _cfg(), FakeMic(), [])
    installed = {}
    monkeypatch.setattr(signal, "signal",
                        lambda sig, fn: installed.__setitem__(sig, fn))
    h._install_orb_signal_handlers()
    assert signal.SIGUSR1 in installed
    assert signal.SIGUSR2 in installed
    assert callable(installed[signal.SIGUSR1])
    assert callable(installed[signal.SIGUSR2])


def test_shutdown_restores_sigusr2_handler(monkeypatch):
    h = _helper(monkeypatch, _cfg(), FakeMic(), [])
    restored = {}
    monkeypatch.setattr(signal, "signal",
                        lambda sig, fn: restored.__setitem__(sig, fn))
    sentinel1, sentinel2 = object(), object()
    h._orb_prev_sigusr1 = sentinel1
    h._orb_prev_sigusr2 = sentinel2
    h.shutdown()
    assert restored.get(signal.SIGUSR1) is sentinel1
    assert restored.get(signal.SIGUSR2) is sentinel2


# ---------------------------------------------------------------------------
# orb client: stop_speaking signals the validated helper pid (SIGUSR2)
# ---------------------------------------------------------------------------
def test_orb_client_stop_speaking_signals_helper(monkeypatch):
    sent = {}

    def fake_kill(pid, sig):
        sent["pid"] = pid
        sent["sig"] = sig

    monkeypatch.setattr("voicebridge.orb.client._helper_heartbeat",
                        lambda: {"schema": "qubit-voice-edit-helper/1",
                                 "pid": 123456})
    monkeypatch.setattr("voicebridge.orb.client._helper_cmdline",
                        lambda pid: "python -m voicebridge.desktop_helper")
    monkeypatch.setattr(os, "kill", fake_kill)
    cli = orb_client.OrbClient("http://127.0.0.1:8890", "t")
    out = cli.stop_speaking()
    assert out["ok"] is True
    assert sent == {"pid": 123456, "sig": signal.SIGUSR2}


def test_orb_client_stop_speaking_refuses_foreign_pid(monkeypatch):
    sent = []
    monkeypatch.setattr("voicebridge.orb.client._helper_heartbeat",
                        lambda: {"schema": "qubit-voice-edit-helper/1",
                                 "pid": 99999})
    monkeypatch.setattr("voicebridge.orb.client._helper_cmdline",
                        lambda pid: "firefox --browser")
    monkeypatch.setattr(os, "kill", lambda pid, sig: sent.append(pid))
    cli = orb_client.OrbClient("http://127.0.0.1:8890", "t")
    try:
        cli.stop_speaking()
    except orb_client.OrbError as exc:
        assert exc.code == "ORB_HELPER_NOT_RUNNING"
    else:
        raise AssertionError("foreign pid must be refused")
    assert sent == []


def test_orb_client_stop_speaking_no_helper(monkeypatch):
    monkeypatch.setattr("voicebridge.orb.client._helper_heartbeat",
                        lambda: None)
    cli = orb_client.OrbClient("http://127.0.0.1:8890", "t")
    try:
        cli.stop_speaking()
    except orb_client.OrbError as exc:
        assert exc.code == "ORB_HELPER_NOT_RUNNING"
    else:
        raise AssertionError("absent helper must raise typed error")


# ---------------------------------------------------------------------------
# service: cancel best-effort stops the spoken reply, shape unchanged
# ---------------------------------------------------------------------------
class SvcDesktop:
    def session(self):
        return {"session_type": "x11", "x11": True}

    def _tool(self, name):
        return name in ("xdotool", "xclip")

    def is_fullscreen(self, wid=""):
        return False

    def capture_selection(self, cfg=None):
        return SelectionSnapshot(available=True, text="hello world",
                                 char_count=11, app_class="code", window_id="42")

    def insert_text(self, text, snapshot, cfg=None, *, method=""):
        return InsertionResult(ok=True, method="clipboard_paste")

    def revert_last(self, snapshot, cfg=None):
        return InsertionResult(ok=True, method="undo")


def _service():
    svc = VoiceEditService(
        _cfg(routing="local", confirm_before_replace=False),
        desktop=SvcDesktop(),
        engine=TransformEngine(runner=lambda *a, **k: None),
        audit=lambda *a: None, estop=lambda cfg: False)
    return svc


def test_service_cancel_signals_helper_stop(monkeypatch):
    from voicebridge.voice_edit import service as svc_mod
    calls = []
    monkeypatch.setattr(svc_mod, "_signal_helper_stop",
                        lambda: (calls.append(True), True)[1])
    svc = _service()
    rid = svc.begin()["request_id"]
    out = svc.cancel(rid)
    assert out["cancelled"] is True
    assert calls == [True]


def test_service_cancel_unknown_still_signals(monkeypatch):
    from voicebridge.voice_edit import service as svc_mod
    calls = []
    monkeypatch.setattr(svc_mod, "_signal_helper_stop",
                        lambda: (calls.append(True), True)[1])
    svc = _service()
    out = svc.cancel("does-not-exist")
    assert out["cancelled"] is False
    assert calls == [True], "cancel must not be a no-op for audio"


def test_signal_helper_stop_validated_pid(monkeypatch):
    from voicebridge.voice_edit import service as svc_mod
    import voicebridge.voice_edit.heartbeat as hb
    monkeypatch.setattr(hb, "other_live_pid", lambda exclude=0: 4242)
    sent = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: sent.append((pid, sig)))
    assert svc_mod._signal_helper_stop() is True
    assert sent == [(4242, signal.SIGUSR2)]


def test_signal_helper_stop_no_helper_is_false(monkeypatch):
    from voicebridge.voice_edit import service as svc_mod
    import voicebridge.voice_edit.heartbeat as hb
    monkeypatch.setattr(hb, "other_live_pid", lambda exclude=0: None)
    sent = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: sent.append(pid))
    assert svc_mod._signal_helper_stop() is False
    assert sent == []


def test_signal_helper_stop_kill_error_is_false(monkeypatch):
    from voicebridge.voice_edit import service as svc_mod
    import voicebridge.voice_edit.heartbeat as hb

    def boom(pid, sig):
        raise OSError("gone")

    monkeypatch.setattr(hb, "other_live_pid", lambda exclude=0: 7)
    monkeypatch.setattr(os, "kill", boom)
    assert svc_mod._signal_helper_stop() is False

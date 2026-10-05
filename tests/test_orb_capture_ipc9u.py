"""Orb click-to-talk IPC contract (helper owns the mic, orb only triggers).

Hermetic: no X, mic, socket, or model. Fakes stand in for the helper's
PTT path and for the OS signal delivery; no audio is ever captured.

Contract:
- orb click triggers the HELPER's existing PTT capture (start-capture),
  never the stage-only begin_session path as the primary;
- cancel-while-staged still honors the existing cancel verb;
- double-click is safe (no overlapping captures);
- no-mic-contention: the orb package never opens audio itself.
"""
from __future__ import annotations

import os
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import voicebridge.desktop_helper as dh
from voicebridge.orb import client as orb_client


class FakeCfg:
    def __init__(self, data=None):
        self._data = data if data is not None else {"voice_edit": {"enabled": True}}

    def get(self, *path, default=None):
        node = self._data
        for p in path:
            if isinstance(node, dict) and p in node:
                node = node[p]
            else:
                return default
        return node


class FakeOverlay:
    def set_status(self, *a, **k):
        pass

    def show(self):
        pass

    def hide(self):
        pass


class FakeDesktop:
    def session(self):
        return {"session_type": "x11", "x11": True}

    def focused_window(self):
        return {"app_class": "code", "window_id": "w1"}

    def is_fullscreen(self, wid):
        return False


class FakeClient:
    def __init__(self):
        self.calls = []

    def status(self):
        return {"enabled": True, "estop": False}

    def begin(self):
        self.calls.append(("begin",))
        return {"ok": True, "request_id": "req-orb-1"}

    def capture(self, rid):
        self.calls.append(("capture", rid))
        return {"ok": True, "selection": {"available": False,
                                          "captured_via": "orb_no_capture",
                                          "app_class": "code"}}

    def cancel(self, rid):
        self.calls.append(("cancel", rid))
        return {"ok": True}


def _helper(**kw):
    cfg = FakeCfg(kw.get("cfg_data"))
    helper = dh.DesktopHelper(cfg, client=FakeClient(),
                              overlay=FakeOverlay(),
                              desktop=FakeDesktop(),
                              mic_factory=lambda: None,
                              stt_factory=lambda ticket: None)
    helper._warm_event.set()
    helper._warm_state = "skipped"
    return helper


def _no_threaded_transcribe(helper, started, stopped):
    helper._worker = None

    def fake_transcribe():
        started.append(True)

    helper._transcribe = fake_transcribe  # type: ignore[method-assign]
    orig_stop = helper._stop_listening

    def fake_stop():
        stopped.append(True)
        orig_stop.__wrapped__ if False else None

    helper._stop_listening = fake_stop  # type: ignore[method-assign]


# -- helper honors an orb start-capture trigger -------------------------------

def test_orb_trigger_starts_helper_capture_idle():
    helper = _helper()
    started, stopped = [], []
    _no_threaded_transcribe(helper, started, stopped)
    helper._capture_selection = lambda: True  # type: ignore[method-assign]
    helper.on_orb_toggle()
    assert started == [True]
    assert stopped == []


def test_orb_trigger_while_listening_finishes_never_overlaps():
    helper = _helper()
    started, stopped = [], []
    _no_threaded_transcribe(helper, started, stopped)
    helper._capture_selection = lambda: True  # type: ignore[method-assign]
    helper.on_orb_toggle()  # idle -> start
    assert started == [True]
    helper.on_orb_toggle()  # listening -> finish (stop), never a 2nd start
    assert started == [True]
    assert stopped == [True]


def test_orb_trigger_while_preview_cancels():
    helper = _helper()
    helper.state = helper.PREVIEW
    helper._pending_task = {}
    helper.on_orb_toggle()
    assert helper.state == helper.IDLE


def test_orb_trigger_refused_while_processing():
    helper = _helper()
    started = []
    helper.state = helper.PROCESSING
    helper._active = True
    orig = helper._start_listening
    helper._start_listening = lambda: started.append(True)  # type: ignore[method-assign]
    helper.on_orb_toggle()
    assert started == []
    helper._start_listening = orig  # type: ignore[method-assign]


def test_cancel_mid_capture_still_honored():
    helper = _helper()
    started, stopped = [], []
    _no_threaded_transcribe(helper, started, stopped)
    helper._capture_selection = lambda: True  # type: ignore[method-assign]
    helper.on_orb_toggle()
    helper.cancel()
    assert helper.state == helper.IDLE
    assert ("cancel", "req-orb-1") in helper.client.calls


# -- orb client signals the helper (never captures itself) --------------------

def test_orb_client_trigger_signals_helper_pid(monkeypatch):
    sent = {}

    def fake_read():
        return {"schema": "qubit-voice-edit-helper/1", "pid": 123456}

    def fake_cmdline(pid):
        return "python -m voicebridge.desktop_helper"

    def fake_kill(pid, sig):
        sent["pid"] = pid
        sent["sig"] = sig

    monkeypatch.setattr("voicebridge.orb.client._helper_heartbeat", fake_read)
    monkeypatch.setattr("voicebridge.orb.client._helper_cmdline", fake_cmdline)
    monkeypatch.setattr(os, "kill", fake_kill)
    cli = orb_client.OrbClient("http://127.0.0.1:8890", "t")
    out = cli.trigger_helper_capture()
    assert out["ok"] is True
    assert sent == {"pid": 123456, "sig": signal.SIGUSR1}


def test_orb_trigger_refuses_foreign_pid(monkeypatch):
    sent = []

    def fake_read():
        return {"schema": "qubit-voice-edit-helper/1", "pid": 99999}

    monkeypatch.setattr("voicebridge.orb.client._helper_heartbeat", fake_read)
    monkeypatch.setattr("voicebridge.orb.client._helper_cmdline",
                        lambda pid: "firefox --browser")
    monkeypatch.setattr(os, "kill", lambda pid, sig: sent.append(pid))
    cli = orb_client.OrbClient("http://127.0.0.1:8890", "t")
    try:
        cli.trigger_helper_capture()
    except orb_client.OrbError as exc:
        assert exc.code == "ORB_HELPER_NOT_RUNNING"
    else:
        raise AssertionError("foreign pid must be refused")
    assert sent == []


def test_orb_trigger_helper_not_running(monkeypatch):
    monkeypatch.setattr("voicebridge.orb.client._helper_heartbeat",
                        lambda: None)
    cli = orb_client.OrbClient("http://127.0.0.1:8890", "t")
    try:
        cli.trigger_helper_capture()
    except orb_client.OrbError as exc:
        assert exc.code == "ORB_HELPER_NOT_RUNNING"
    else:
        raise AssertionError("absent helper must raise typed error")


def test_no_mic_contention_property():
    """The orb package never IMPORTS audio/capture code (AST-level).

    Doc prose may name the helper (it documents the trigger seam); what
    must never exist is a real import or attribute use of mic/STT
    machinery — the helper is the single mic owner.
    """
    import ast as _ast
    from pathlib import Path as _P
    orb_dir = _P(__file__).resolve().parent.parent / "voicebridge" / "orb"
    banned = ("parec", "stt_client", "sounddevice", "pyaudio",
              "arecord", "portaudio")
    for src in orb_dir.glob("*.py"):
        tree = _ast.parse(src.read_text(encoding="utf-8"))
        for node in _ast.walk(tree):
            if isinstance(node, _ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0].lower() not in banned, \
                        f"{src.name} must never import audio ({alias.name})"
            elif isinstance(node, _ast.ImportFrom):
                mod = str(node.module or "")
                assert not any(b in mod.lower() for b in banned), \
                    f"{src.name} must never import audio ({mod})"
            elif isinstance(node, _ast.Attribute):
                assert node.attr.lower() not in banned, \
                    f"{src.name} must never use audio attr ({node.attr})"

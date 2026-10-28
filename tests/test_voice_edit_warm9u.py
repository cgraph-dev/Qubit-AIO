"""Cold-start warm for the voice-edit local models (readiness wave, cold lane).

The classifier budget is 4 s and the transform budget is 8 s, while a cold
local model load is ~17.5 s. ``voicebridge.voice_edit.warm`` starts the load in
one background thread on the earliest turn edge; it never blocks the hotkey,
never raises, and never starts a second load.

These tests are hermetic: the provider gateway is a fake, and one test also
pins that no socket/urllib request is made.
"""
from __future__ import annotations

import socket
import sys
import threading
import time
import types
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import voicebridge.desktop_helper as dh  # noqa: E402
from voicebridge.providers import gateway as GW  # noqa: E402
from voicebridge.voice_edit import brain as brain_mod  # noqa: E402
from voicebridge.voice_edit import warm as W  # noqa: E402


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


def _cfg(local="brain-x", detail="detail-y"):
    return FakeCfg({"voice_edit": {"enabled": True,
                                   "local_profile": local,
                                   "detail_model": detail}})


def _wait_done(timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        st = W.warm_state()
        if not st["warming"] and st["state"] != "warming":
            return st
        time.sleep(0.005)
    raise AssertionError(f"warm did not finish: {W.warm_state()}")


@pytest.fixture(autouse=True)
def _reset_warm():
    W.reset_state()
    yield
    W.reset_state()


def _ok(profile):
    return types.SimpleNamespace(ok=True, code="", model=profile, text="ok")


def test_warms_both_configured_profiles_once(monkeypatch):
    calls = []

    def fake_run(prompt, cfg, **kw):
        calls.append(dict(kw, prompt=prompt))
        return _ok(kw.get("profile_id"))

    monkeypatch.setattr(GW, "run_role", fake_run)

    assert W.warm_async(_cfg()) is True
    state = _wait_done()

    assert state["state"] == "warm"
    assert state["errors"] == []
    assert [c["profile_id"] for c in calls] == ["brain-x", "detail-y"]
    for c in calls:
        assert c["max_tokens"] == W.WARM_MAX_TOKENS
        assert c["timeout_s"] == W.WARM_TIMEOUT_S
        assert c["local_mode"] == "chat"
        assert c["reasoning"] == "low"
        assert c["role"] == "chat"


def test_second_call_is_a_noop_within_cooldown(monkeypatch):
    calls = []

    def fake_run(prompt, cfg, **kw):
        calls.append(kw.get("profile_id"))
        return _ok(kw.get("profile_id"))

    monkeypatch.setattr(GW, "run_role", fake_run)

    assert W.warm_async(_cfg()) is True
    assert W.warm_async(_cfg()) is False
    _wait_done()

    assert W.warm_async(_cfg()) is False
    time.sleep(0.05)
    assert calls == ["brain-x", "detail-y"]
    assert W.warm_state()["state"] == "warm"


def test_concurrent_calls_are_single_flight(monkeypatch):
    release = threading.Event()
    calls = []
    lock = threading.Lock()

    def fake_run(prompt, cfg, **kw):
        with lock:
            calls.append(kw.get("profile_id"))
        release.wait(5)
        return _ok(kw.get("profile_id"))

    monkeypatch.setattr(GW, "run_role", fake_run)

    results = []
    start = threading.Barrier(8)

    def _call():
        start.wait(5)
        results.append(W.warm_async(_cfg()))

    threads = [threading.Thread(target=_call) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)

    assert results.count(True) == 1
    assert results.count(False) == 7
    release.set()
    _wait_done()
    assert calls == ["brain-x", "detail-y"]


def test_warm_is_fire_and_forget(monkeypatch):
    release = threading.Event()

    def fake_run(prompt, cfg, **kw):
        release.wait(5)
        return _ok(kw.get("profile_id"))

    monkeypatch.setattr(GW, "run_role", fake_run)

    started = time.monotonic()
    assert W.warm_async(_cfg()) is True
    elapsed = time.monotonic() - started
    assert elapsed < 0.2, elapsed
    assert W.warm_state()["warming"] is True
    release.set()
    _wait_done()


def test_warm_failure_is_swallowed(monkeypatch):
    def fake_run(prompt, cfg, **kw):
        raise RuntimeError("cold load exploded")

    monkeypatch.setattr(GW, "run_role", fake_run)

    assert W.warm_async(_cfg()) is True
    state = _wait_done()
    assert state["state"] == "failed"
    assert state["errors"]
    assert all("brain-x" in e or "detail-y" in e for e in state["errors"])


def test_warm_makes_no_network_request(monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError("warm must not use the network")

    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    monkeypatch.setattr(GW, "run", _boom)
    monkeypatch.setattr(
        GW, "run_role",
        lambda prompt, cfg, **kw: _ok(kw.get("profile_id")))

    assert W.warm_async(_cfg()) is True
    assert _wait_done()["state"] == "warm"


# -- hotkey path ------------------------------------------------------------

class _Overlay:
    def set_status(self, state, detail=""):
        pass

    def show(self):
        pass


class _Client:
    def status(self):
        return {"enabled": True, "estop": False}

    def begin(self):
        return {"ok": True, "request_id": "req-warm"}


class _Desktop:
    def session(self):
        return {"session_type": "x11"}


class _Mic:
    def start(self):
        pass

    def read_frame(self, timeout=0.2):
        return None

    def stop(self):
        pass


class _Stt:
    error = None


def _helper(monkeypatch):
    h = dh.DesktopHelper(
        FakeCfg({"voice_edit": {"enabled": True, "stt_mode": "skipped"}}),
        client=_Client(), overlay=_Overlay(), desktop=_Desktop(),
        mic_factory=lambda: _Mic(), stt_factory=lambda ticket: _Stt())
    h._warm_state = "skipped"
    h._warm_event = threading.Event()
    h._warm_event.set()
    return h


def test_hotkey_invokes_warm_without_blocking(monkeypatch):
    release = threading.Event()
    profiles = []

    def blocking(prompt, cfg, **kw):
        profiles.append(kw.get("profile_id"))
        release.wait(5)
        return _ok(kw.get("profile_id"))

    monkeypatch.setattr(GW, "run_role", blocking)
    h = _helper(monkeypatch)
    h.cfg = _cfg()

    started = time.monotonic()
    assert h._open_request() is True
    elapsed = time.monotonic() - started
    assert elapsed < 0.3, elapsed
    assert W.warm_state()["warming"] is True
    release.set()
    _wait_done()
    assert profiles == ["brain-x", "detail-y"]


def test_hotkey_survives_warm_failure(monkeypatch):
    def broken_warm(cfg):
        raise RuntimeError("warm is best-effort")

    monkeypatch.setattr(dh, "warm_voice_models", broken_warm)
    h = _helper(monkeypatch)

    assert h._open_request() is True


# -- classifier trace -------------------------------------------------------

def test_brain_failure_logs_timeout_and_model(monkeypatch):
    events = []

    def fake_log(name, **fields):
        events.append((name, fields))

    monkeypatch.setattr("voicebridge.logging_setup.log_event", fake_log)

    def runner(cfg, **kw):
        return types.SimpleNamespace(ok=False, code="MODEL_UNAVAILABLE",
                                     model="qubit-brain", text="")

    assert brain_mod.BrainRouter(runner=runner).classify(
        _cfg(), "make this shorter", has_selection=True,
        timeout_s=4.0) is None

    name, fields = events[-1]
    assert name == "voice_edit.brain.error"
    assert fields["code"] == "MODEL_UNAVAILABLE"
    assert fields["timeout_s"] == 4.0
    assert fields["model"] == "qubit-brain"


def test_brain_exception_logs_timeout(monkeypatch):
    events = []

    def fake_log(name, **fields):
        events.append((name, fields))

    monkeypatch.setattr("voicebridge.logging_setup.log_event", fake_log)

    def runner(cfg, **kw):
        raise TimeoutError("classifier timed out")

    assert brain_mod.BrainRouter(runner=runner).classify(
        _cfg(), "make this shorter", has_selection=True,
        timeout_s=4.0) is None

    name, fields = events[-1]
    assert name == "voice_edit.brain.error"
    assert fields["code"] == "BRAIN_CALL_FAILED"
    assert fields["timeout_s"] == 4.0
    assert "model" in fields

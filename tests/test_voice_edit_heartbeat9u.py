"""Desktop-helper heartbeat tests (no X, no service)."""
from __future__ import annotations

import os
import signal
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge.voice_edit import heartbeat as hb


def _use_tmp_state(tmp_path, monkeypatch):
    # canonical_state_dir() is XDG_STATE_HOME-derived; point it at a temp dir.
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))


def test_write_read_live_clear(tmp_path, monkeypatch):
    _use_tmp_state(tmp_path, monkeypatch)
    assert hb.live() is False
    hb.write(session="x11", extra={"hotkey_mode": "hold"})
    data = hb.read()
    assert data is not None and data["schema"] == hb.SCHEMA
    assert data["hotkey_mode"] == "hold"
    assert hb.live() is True  # this process is alive
    hb.clear()
    assert hb.live() is False


def test_stale_heartbeat_is_not_live(tmp_path, monkeypatch):
    _use_tmp_state(tmp_path, monkeypatch)
    hb.write()
    import json
    p = hb.path()
    data = json.loads(p.read_text())
    data["ts"] = 0  # long ago
    p.write_text(json.dumps(data))
    assert hb.live() is False


def test_dead_pid_is_not_live(tmp_path, monkeypatch):
    _use_tmp_state(tmp_path, monkeypatch)
    hb.write()
    import json
    p = hb.path()
    data = json.loads(p.read_text())
    data["pid"] = 2 ** 30  # certainly not running
    p.write_text(json.dumps(data))
    assert hb.live() is False


def test_garbage_heartbeat_is_not_live(tmp_path, monkeypatch):
    _use_tmp_state(tmp_path, monkeypatch)
    hb.path().parent.mkdir(parents=True, exist_ok=True)
    hb.path().write_text("not json")
    assert hb.read() is None
    assert hb.live() is False


# --- single-helper authority (owner 2026-10-05: "voice trigger twice") --------
# Two helpers each open the mic and run a wake loop, so one phrase fires twice.
# The service instance must identify and end a leftover helper before it starts.


def test_other_live_pid_none_without_heartbeat(tmp_path, monkeypatch):
    _use_tmp_state(tmp_path, monkeypatch)
    assert hb.other_live_pid() is None


def test_other_live_pid_excludes_self(tmp_path, monkeypatch):
    _use_tmp_state(tmp_path, monkeypatch)
    hb.write()  # pid == this process
    monkeypatch.setattr(hb, "_pid_is_helper", lambda pid: True)
    assert hb.other_live_pid(exclude=os.getpid()) is None
    assert hb.other_live_pid() == os.getpid()


def test_other_live_pid_rejects_non_helper_cmdline(tmp_path, monkeypatch):
    _use_tmp_state(tmp_path, monkeypatch)
    hb.write()
    monkeypatch.setattr(hb, "_pid_is_helper", lambda pid: False)
    assert hb.other_live_pid() is None  # recycled pid is never signalled


def test_other_live_pid_rejects_stale(tmp_path, monkeypatch):
    _use_tmp_state(tmp_path, monkeypatch)
    import json
    hb.write()
    monkeypatch.setattr(hb, "_pid_is_helper", lambda pid: True)
    p = hb.path()
    data = json.loads(p.read_text())
    data["ts"] = 0
    p.write_text(json.dumps(data))
    assert hb.other_live_pid() is None


def test_pid_is_helper_never_raises_and_rejects_absent_pid(tmp_path, monkeypatch):
    _use_tmp_state(tmp_path, monkeypatch)
    assert hb._pid_is_helper(os.getpid()) in (True, False)  # real /proc, no raise
    assert hb._pid_is_helper(2 ** 30) is False  # no such process


def test_take_over_previous_helper_none_when_alone(tmp_path, monkeypatch):
    _use_tmp_state(tmp_path, monkeypatch)
    from voicebridge import desktop_helper as dh
    monkeypatch.setattr(hb, "other_live_pid", lambda exclude=0: None)
    assert dh.take_over_previous_helper() is None


def test_take_over_previous_helper_signals_and_waits(monkeypatch):
    from voicebridge import desktop_helper as dh
    monkeypatch.setattr(hb, "other_live_pid", lambda exclude=0: 4242)
    killed = []
    state = {"alive": True}

    def fake_kill(pid, sig):
        if sig == 0:
            if not state["alive"]:
                raise ProcessLookupError()
            return
        killed.append((pid, sig))
        state["alive"] = False  # exits cleanly on SIGTERM

    clock = [0.0]
    pid = dh.take_over_previous_helper(
        kill=fake_kill, sleep=lambda s: clock.__setitem__(0, clock[0] + s),
        monotonic=lambda: clock[0])
    assert pid == 4242
    assert killed == [(4242, signal.SIGTERM)]
    assert clock[0] <= 3.0  # bounded wait


def test_take_over_previous_helper_never_raises_on_kill_error(monkeypatch):
    from voicebridge import desktop_helper as dh
    monkeypatch.setattr(hb, "other_live_pid", lambda exclude=0: 99)

    def boom(pid, sig):
        raise OSError("gone")

    assert dh.take_over_previous_helper(kill=boom, sleep=lambda s: None,
                                        monotonic=lambda: 0.0) == 99


def test_install_term_handler_runs_shutdown(monkeypatch):
    from voicebridge import desktop_helper as dh
    captured = {}
    monkeypatch.setattr(dh.signal, "signal",
                        lambda sig, h: captured.update(sig=sig, h=h))

    class _H:
        down = False

        def shutdown(self):
            self.down = True

    h = _H()
    dh._install_term_handler(h)
    assert captured["sig"] == dh.signal.SIGTERM
    with pytest.raises(SystemExit):
        captured["h"](dh.signal.SIGTERM, None)
    assert h.down is True  # mic released, no orphaned capture child

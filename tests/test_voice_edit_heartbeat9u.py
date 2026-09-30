"""Desktop-helper heartbeat tests (no X, no service)."""
from __future__ import annotations

import sys
from pathlib import Path

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

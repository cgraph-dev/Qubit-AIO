"""`/chat` must participate in the interrupted-turn marker, like the embedded TUI.

The dashboard `/chat` surface (voicebridge SSE) is separate from the PTY-hosted
TUI. Before this, a `voicebridge run` restart mid-turn lost the turn and the
operator had to retype `continue`. Both surfaces now share
``tui_gateway.turn_marker``: a turn writes a marker when it starts, the marker is
cleared when the turn concludes, and a surviving marker (process death) is
surfaced on the next session resolve as ``interrupted_turn``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
_RUNTIME = REPO / "qubit_runtime"
if str(_RUNTIME) not in sys.path:
    sys.path.insert(0, str(_RUNTIME))

from tui_gateway import turn_marker  # noqa: E402

import voicebridge.chat_transport as ct  # noqa: E402


@pytest.fixture()
def home(tmp_path):
    return tmp_path / "home"


class _Cfg(dict):
    """Minimal cfg carrying the home the marker store resolves."""

    def __init__(self, home):
        super().__init__()
        self._home = str(home)

    def get(self, *a, **kw):  # noqa: D102 - only the marker's lookup matters
        return None


@pytest.fixture()
def cfg(home, monkeypatch):
    monkeypatch.setattr(ct, "_chat_home", lambda _cfg: home)
    return _Cfg(home)


def test_turn_start_writes_marker(cfg, home):
    ct._write_turn_marker(cfg, "s-1", "deploy the thing")
    marker = turn_marker.read_turn_marker(home, "s-1")
    assert marker is not None
    assert marker["prompt"] == "deploy the thing"
    assert marker["auto_continue"] is True


def test_concluded_turn_clears_marker(cfg, home):
    ct._write_turn_marker(cfg, "s-2", "long task")
    assert turn_marker.read_turn_marker(home, "s-2") is not None
    ct._clear_turn_marker(cfg, "s-2")
    assert turn_marker.read_turn_marker(home, "s-2") is None


def test_surviving_marker_is_surfaced_on_resolve(cfg, home):
    """A marker left by a process death shows up as ``interrupted_turn``."""
    ct._write_turn_marker(cfg, "s-3", "unfinished work")

    class _St:
        def ensure(self, sid, *, model=None):
            return {"state": "OK", "session": {"id": sid}}

    result = ct._resolve_session(_St(), "s-3", model=None, cfg=cfg)
    assert result["state"] == "OK"
    assert result["interrupted_turn"]["prompt"] == "unfinished work"


def test_resolve_without_marker_reports_none(cfg, home):
    class _St:
        def ensure(self, sid, *, model=None):
            return {"state": "OK", "session": {"id": sid}}

    assert ct._resolve_session(_St(), "s-clean", model=None, cfg=cfg)["interrupted_turn"] is None


def test_marker_helpers_never_raise_without_module(monkeypatch):
    """Bookkeeping is best-effort: a broken import must not break a turn."""
    monkeypatch.setattr(ct, "_chat_turn_marker_module", lambda: None)
    ct._write_turn_marker({}, "s-x", "text")
    ct._clear_turn_marker({}, "s-x")
    assert ct._pending_interrupted_turn({}, "s-x") is None


def test_stale_marker_is_not_resumed_and_is_cleared(cfg, home, monkeypatch):
    """A marker past the freshness window must not fire a resume (and is dropped).

    read_turn_marker itself is age-blind, so the consumer applies the gate; a
    days-old marker would otherwise silently re-run work.
    """
    ct._write_turn_marker(cfg, "s-old", "ancient task")
    # Age the marker well past any sane window.
    path = home / "desktop" / "interrupted_turns.json"
    import json as _json

    data = _json.loads(path.read_text())
    data["s-old"]["started_at"] = 1.0  # 1970
    path.write_text(_json.dumps(data))

    assert ct._pending_interrupted_turn(cfg, "s-old") is None
    assert turn_marker.read_turn_marker(home, "s-old") is None, "stale marker must be cleared"


def test_fresh_marker_still_resumes(cfg, home):
    ct._write_turn_marker(cfg, "s-new", "recent task")
    got = ct._pending_interrupted_turn(cfg, "s-new")
    assert got is not None and got["prompt"] == "recent task"


def test_disabled_auto_continue_blocks_resume(home, monkeypatch):
    class _CfgOff:
        def get(self, key, *a, **k):
            if key == "desktop":
                return {"auto_continue": {"enabled": False}}
            return None

    monkeypatch.setattr(ct, "_chat_home", lambda _cfg: home)
    ct._write_turn_marker(_CfgOff(), "s-off", "do not resume")
    assert ct._pending_interrupted_turn(_CfgOff(), "s-off") is None

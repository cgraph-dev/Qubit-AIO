"""The dashboard's active-session file must have a STABLE path per channel.

That file carries the session id the dashboard TUI writes, and it is the resume
fallback for a reconnecting `/chat` client. It used to be a per-process
``mkstemp`` path held in ``app.state``, so a backend restart minted a fresh
EMPTY file, the reconnect found no resume id, and a brand-new session was
spawned — losing the interrupted turn. A deterministic path lets the id written
before the restart be read after it.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
_RUNTIME = REPO / "qubit_runtime"
if str(_RUNTIME) not in sys.path:
    sys.path.insert(0, str(_RUNTIME))

from qubit_cli import web_server as ws  # noqa: E402
from qubit_cli import web_server_chat as wsc  # noqa: E402


class _App:
    """Minimal stand-in for the FastAPI app; the mapping lives in its state."""

    def __init__(self):
        self.state = type("S", (), {})()


def _fresh_app():
    """A 'new process': empty app.state, same channel."""
    return _App()


def test_path_is_stable_across_processes(monkeypatch):
    monkeypatch.setattr(ws, "_get_pty_active_session_files", lambda app: _app_files(app), raising=False)

    first = wsc._active_session_file_for_channel(_fresh_app(), "dashboard")
    second = wsc._active_session_file_for_channel(_fresh_app(), "dashboard")
    assert first == second, f"path must survive a restart: {first} != {second}"


def test_existing_session_id_is_not_clobbered(monkeypatch):
    """The second 'process' must be able to read what the first wrote."""
    monkeypatch.setattr(ws, "_get_pty_active_session_files", lambda app: _app_files(app), raising=False)

    path = wsc._active_session_file_for_channel(_fresh_app(), "dashboard")
    path.write_text('{"session_id": "20261003_093120_d09dc4"}')

    # A restart: new app, same channel -> same path, same content.
    again = wsc._active_session_file_for_channel(_fresh_app(), "dashboard")
    assert again == path
    assert "20261003_093120_d09dc4" in again.read_text()


def test_channels_do_not_collide(monkeypatch):
    monkeypatch.setattr(ws, "_get_pty_active_session_files", lambda app: _app_files(app), raising=False)
    a = wsc._active_session_file_for_channel(_fresh_app(), "dashboard")
    b = wsc._active_session_file_for_channel(_fresh_app(), "other-channel")
    assert a != b


def _app_files(app):
    files = getattr(app.state, "pty_active_session_files", None)
    if files is None:
        files = {}
        app.state.pty_active_session_files = files
    return files

"""A session /yolo (TUI Shift+Tab, the Desktop zap) survives a backend restart: persisted on the session
row and re-armed by ``session.resume`` in a process whose in-memory approval set starts empty."""

import types

import pytest

from tui_gateway import server


# PRODUCT GAP (Qubit fork vs pin cbe5e53e): ``tui_gateway/methods_config_set.py``
# ``_set_yolo`` (line 271) dropped the durable-persistence branch. Upstream calls
# ``toggle_session_yolo(session_key, ..., persist=persist)`` with a ``persist``
# callback that writes ``db.set_session_yolo(row_id, on)``; the Qubit copy only
# calls the in-memory ``enable_session_yolo``/``disable_session_yolo``, so a
# ``/yolo`` toggle is never written to the session row and does not survive a
# backend restart. (``qubit_state_sessions.set_session_yolo`` and
# ``tools.approval_yolo.toggle_session_yolo`` both exist — merely unwired.)
# Do NOT fix product here — recorded for the sync owner.
@pytest.mark.xfail(
    strict=False,
    reason="PRODUCT GAP: _set_yolo does not persist the session yolo flag to the "
    "session row (persist callback dropped vs pin cbe5e53e); /yolo does not "
    "survive a backend restart.",
)
def test_session_yolo_toggle_survives_a_backend_restart(monkeypatch, tmp_path):
    """A session /yolo (Shift+Tab / the Desktop zap) is persisted on the row and re-armed when a fresh
    backend resumes it, including a toggle made before the row existed; switching it off persists too."""
    from qubit_state import SessionDB
    from tools.approval import clear_session, is_session_yolo_enabled

    db = SessionDB(db_path=tmp_path / "state.db")
    monkeypatch.setattr(server, "_get_db", lambda: db)
    monkeypatch.setattr(server, "_enable_gateway_prompts", lambda: None)
    monkeypatch.setattr(server, "_schedule_agent_build", lambda sid: None)
    try:
        # Toggled before the first message: the lazily created row must still record it.
        server._sessions["sid"] = {"agent": types.SimpleNamespace(), "session_key": "row-a"}
        server.handle_request({"id": "1", "method": "config.set", "params": {"session_id": "sid", "key": "yolo"}})
        assert server._ensure_session_db_row(server._sessions["sid"]) is not False
        assert SessionDB.session_yolo_enabled(db.get_session("row-a"))

        server._sessions.clear()
        clear_session("row-a")  # a new backend process starts with an empty in-memory set
        resp = server.handle_request({"id": "2", "method": "session.resume", "params": {"session_id": "row-a"}})
        assert resp["result"]["session_key"] == "row-a"
        assert is_session_yolo_enabled("row-a") is True

        server.handle_request(
            {"id": "3", "method": "config.set", "params": {"session_id": resp["result"]["session_id"], "key": "yolo"}})
        assert is_session_yolo_enabled("row-a") is False
        assert not SessionDB.session_yolo_enabled(db.get_session("row-a"))
    finally:
        server._sessions.clear()
        clear_session("row-a")
        db.close()

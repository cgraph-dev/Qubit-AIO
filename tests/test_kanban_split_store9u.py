"""Kanban split-store regression: every read resolves the SAME store as writes.

Defect (audit 2026-09-29, isolated Qubit ``:8895`` / ``/qubit-boards``):

* board/task create/patch wrote the authoritative Qubit-native store
  (``voicebridge.kanban`` at ``<state>/kanban.db``);
* the drawer's attachment/log/diagnostics reads bound the *other*, empty legacy
  vendored store (``qubit_cli.kanban_db`` at ``<QUBIT_HOME>/kanban.db``), so a
  task that really exists 404'd in those tabs with a misleading
  ``board '<real>' does not exist`` / ``task '<id>' not found``.

These tests pin the fix:

* the read authority path == the write authority path (one db);
* the served attachment/log/diagnostics routes resolve a task created on the
  board, with no 404 and no dependency on the legacy vendored engine;
* a genuinely missing task / board is still a typed 404 (never a fabricated
  2xx), and the ``KANBAN_WRITE_BROKERED`` upload policy is unchanged.

Hermetic: temp XDG + ``QUBIT_HOME`` only; no network, no live services.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "home"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _create_task(cfg, title, board="main"):
    from voicebridge import qubit_compat as HC

    code, out = HC.h_plugin_kanban_tasks_create(
        cfg, {"board": board}, {"title": title, "board": board,
                                "confirm": True}, {})
    assert code == 200 and out["ok"], (code, out)
    return int(out["task"]["id"])


# ---------------------------------------------------------------------------
# One authority: reads and writes bind the same database
# ---------------------------------------------------------------------------

def test_read_authority_is_the_write_store(tmp_path, monkeypatch):
    from voicebridge import kanban as N
    from voicebridge import kanban_bridge as KBG

    cfg = _cfg(tmp_path, monkeypatch)
    read = KBG.read_store(cfg)
    assert read["read_store"] == "qubit-native"
    assert read["db_path"] == str(N._db_path(cfg))
    # The write authority is the very same file (no split-brain).
    assert KBG.native_store(cfg)["db_path"] == read["db_path"]
    assert Path(read["db_path"]).parent == Path(cfg.state_dir())
    # The legacy vendored store is a DIFFERENT file, never a served authority.
    ven = KBG.vendored_store(cfg)["db_path"]
    assert ven != read["db_path"], (ven, read["db_path"])


def test_served_aux_reads_resolve_the_write_store(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    tid = _create_task(cfg, "split-store probe")

    # The board read (write store) shows the task.
    code, board = HC.dispatch(cfg, "GET", "/api/plugins/kanban/board",
                              {"board": "main"}, {})
    assert code == 200, (code, board)
    titles = [t["title"] for col in board["columns"] for t in col["tasks"]]
    assert "split-store probe" in titles, titles

    # Task detail lists attachments from the native store (honestly empty).
    code, detail = HC.dispatch(cfg, "GET",
                               f"/api/plugins/kanban/tasks/{tid}", {}, {})
    assert code == 200, (code, detail)
    assert detail["attachments"] == []
    assert detail["attachments_supported"] is False

    # Attachments list: 200 for the existing task, never a misleading 404.
    code, att = HC.dispatch(
        cfg, "GET", f"/api/plugins/kanban/tasks/{tid}/attachments",
        {"board": "main"}, {})
    assert code == 200, (code, att)
    assert att["ok"] is True and att["task_id"] == tid
    assert att["attachments"] == []
    assert att["supported"] is False
    assert att["unsupported_code"] == "KANBAN_ATTACHMENTS_UNSUPPORTED"

    # Log: the real native event timeline, not the empty vendored worker log.
    code, log = HC.dispatch(
        cfg, "GET", f"/api/plugins/kanban/tasks/{tid}/log",
        {"board": "main"}, {})
    assert code == 200, (code, log)
    assert log["task_id"] == tid and log["exists"] is True
    assert "create" in log["content"], log["content"]

    # Diagnostics on the real (default) board: 200, not "board does not exist".
    code, diag = HC.dispatch(cfg, "GET", "/api/plugins/kanban/diagnostics",
                             {"board": "main"}, {})
    assert code == 200, (code, diag)
    assert diag["diagnostics"] == [] and diag["count"] == 0


def test_aux_reads_never_touch_the_vendored_engine(tmp_path, monkeypatch):
    """The served aux reads must not depend on the legacy vendored store.

    With the vendored kanban engine made unavailable, the routes still resolve
    the task from the authoritative native store — proof the read path is the
    same store the writes use, not a second database.
    """
    from voicebridge import qubit_compat as HC
    from voicebridge import qubit_plugins as HP

    cfg = _cfg(tmp_path, monkeypatch)
    tid = _create_task(cfg, "no vendored engine")

    def _boom(cfg):
        raise AssertionError("kanban read bound the legacy vendored engine")

    monkeypatch.setattr(HP, "_kanban_engine", _boom)
    for path in (f"/api/plugins/kanban/tasks/{tid}/attachments",
                 f"/api/plugins/kanban/tasks/{tid}/log",
                 "/api/plugins/kanban/diagnostics"):
        code, out = HC.dispatch(cfg, "GET", path, {"board": "main"}, {})
        assert code == 200, (path, code, out)


# ---------------------------------------------------------------------------
# Native store primitives (bypassing HTTP) read the state db
# ---------------------------------------------------------------------------

def test_native_aux_reads_hit_the_state_db(tmp_path, monkeypatch):
    import pytest

    from voicebridge import kanban as N

    cfg = _cfg(tmp_path, monkeypatch)
    tid = N.create(cfg, "unit probe", confirmed=True)["task"]["id"]

    assert N.task_attachments(cfg, tid)["attachments"] == []
    assert "create" in N.task_log(cfg, tid)["content"]
    assert N.diagnostics(cfg, board="main")["count"] == 0

    with pytest.raises(N.KanbanError):
        N.task_attachments(cfg, 424242)
    with pytest.raises(N.KanbanError):
        N.task_log(cfg, 424242)

    # A blocked task is a real diagnostic signal from the native store.
    N.block(cfg, tid, requested_by="test", confirmed=True)
    diag = N.diagnostics(cfg, board="main")
    assert diag["count"] == 1, diag
    assert diag["diagnostics"][0]["task_id"] == tid


# ---------------------------------------------------------------------------
# Typed errors preserved; no fabricated success
# ---------------------------------------------------------------------------

def test_missing_task_is_typed_404_not_a_board_error(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    for path in ("/api/plugins/kanban/tasks/999999/attachments",
                 "/api/plugins/kanban/tasks/999999/log"):
        code, out = HC.dispatch(cfg, "GET", path, {"board": "main"}, {})
        assert code == 404, (path, code, out)
        assert out["ok"] is False and out["code"] == "PLUGIN_HTTP", (path, out)


def test_unknown_board_diagnostics_is_typed_404(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "GET", "/api/plugins/kanban/diagnostics",
                            {"board": "no-such-board"}, {})
    assert code == 404, (code, out)
    assert out["ok"] is False and out["code"] == "KANBAN_NOT_FOUND", out


def test_attachment_upload_stays_broker_gated(tmp_path, monkeypatch):
    """Consistency must not weaken the operator-gated upload policy."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    tid = _create_task(cfg, "attach gate")
    code, out = HC.dispatch(
        cfg, "POST", f"/api/plugins/kanban/tasks/{tid}/attachments", {},
        {"filename": "x.txt", "content_base64": "aGk=", "confirm": True})
    assert code == 403, (code, out)
    assert out["code"] == "KANBAN_WRITE_BROKERED", out

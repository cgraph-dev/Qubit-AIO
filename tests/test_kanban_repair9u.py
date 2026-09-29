"""Kanban repair: every read route resolves the authoritative native store.

The 2026-09-29 audit found the vendored kanban *read* handlers (task log, task
attachments, diagnostics) still bound the fork's own empty ``runtime/kanban.db``
while the board/task surface wrote ``state/kanban.db``. A task that exists on
the board therefore 404'd in the drawer's log / attachment tabs, and
``/diagnostics?board=<real>`` reported "board does not exist" while
``/board?board=<real>`` rendered it.

These tests prove the repaired handlers bind the SAME database the writes use:
one store, one answer. Hermetic temp ``XDG_*``; no network; no live services.
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
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _task(cfg, title="Repair probe", *, board="main"):
    from voicebridge import kanban as N

    return N.create(cfg, title, board=board, requested_by="repair9u",
                    confirmed=True)["task"]["id"]


# ---------------------------------------------------------------------------
# Defect 1 — task attachments resolve the native store (no hardcoded 404)
# ---------------------------------------------------------------------------
def test_task_attachments_route_resolves_native_store(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    tid = _task(cfg)

    code, out = HC.dispatch(
        cfg, "GET", f"/api/plugins/kanban/tasks/{tid}/attachments", {}, {})
    assert code == 200, (code, out)
    assert out["task_id"] == tid, out
    assert out["attachments"] == [], out

    # A genuinely missing task is still a typed 404 (never fabricated data).
    code, out = HC.dispatch(
        cfg, "GET", "/api/plugins/kanban/tasks/999999/attachments", {}, {})
    assert code == 404 and out["ok"] is False, (code, out)


def test_task_detail_attachment_list_is_store_backed(tmp_path, monkeypatch):
    from voicebridge import kanban as N
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    tid = _task(cfg)
    expected = N.task_attachments(cfg, tid)["attachments"]

    code, out = HC.dispatch(
        cfg, "GET", f"/api/plugins/kanban/tasks/{tid}", {}, {})
    assert code == 200, (code, out)
    assert out["attachments"] == expected, out["attachments"]


# ---------------------------------------------------------------------------
# Defect 2 — task log + diagnostics no longer hit the empty vendored store
# ---------------------------------------------------------------------------
def test_task_log_reads_native_timeline(tmp_path, monkeypatch):
    from voicebridge import kanban as N
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    tid = _task(cfg)
    N.comment(cfg, tid, "hello there", requested_by="repair9u", confirmed=True)
    N.complete(cfg, tid, requested_by="repair9u", confirmed=True)

    code, out = HC.dispatch(
        cfg, "GET", f"/api/plugins/kanban/tasks/{tid}/log",
        {"tail": "100000"}, {})
    assert code == 200, (code, out)
    assert out["task_id"] == tid and out["exists"] is True, out
    for kind in ("create", "comment", "complete"):
        assert kind in out["content"], (kind, out["content"])
    assert out["size_bytes"] > 0 and out["truncated"] is False, out

    code, out = HC.dispatch(
        cfg, "GET", "/api/plugins/kanban/tasks/999999/log", {}, {})
    assert code == 404 and out["ok"] is False, (code, out)


def test_diagnostics_reads_native_board(tmp_path, monkeypatch):
    from voicebridge import kanban as N
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    N.boards_create(cfg, "repair9u-board", requested_by="repair9u",
                    confirmed=True)
    tid = _task(cfg, "Blocked probe", board="repair9u-board")
    N.block(cfg, tid, requested_by="repair9u", confirmed=True)

    code, out = HC.dispatch(
        cfg, "GET", "/api/plugins/kanban/diagnostics",
        {"board": "repair9u-board"}, {})
    assert code == 200 and out["count"] == 1, (code, out)
    row = out["diagnostics"][0]
    assert row["task_id"] == tid, row
    assert row["diagnostics"][0]["kind"] == "blocked", row

    # A board that truly does not exist stays a typed 404.
    code, out = HC.dispatch(
        cfg, "GET", "/api/plugins/kanban/diagnostics",
        {"board": "no-such-board"}, {})
    assert code == 404 and out["ok"] is False, (code, out)

    # The synthetic default board is an empty board, not a missing one.
    code, out = HC.dispatch(
        cfg, "GET", "/api/plugins/kanban/diagnostics", {}, {})
    assert code == 200 and out["count"] == 0, (code, out)


# ---------------------------------------------------------------------------
# Defect 3 — the read authority and the write authority are one database
# ---------------------------------------------------------------------------
def test_read_and_write_resolve_the_same_database(tmp_path, monkeypatch):
    from voicebridge import kanban as N
    from voicebridge import kanban_bridge as KBG

    cfg = _cfg(tmp_path, monkeypatch)
    auth = KBG.read_store(cfg)
    assert auth["read_store"] == "qubit-native", auth
    assert auth["db_path"] == str(N._db_path(cfg)), auth
    assert auth["db_path"] == KBG.native_store(cfg)["db_path"], auth

    # The legacy vendored store is a different file and is never the served
    # read authority (it is read only by the explicit migration).
    ven = KBG.vendored_store(cfg)
    assert ven["db_path"] != auth["db_path"], (ven, auth)


def test_reads_persist_across_config_reload(tmp_path, monkeypatch):
    from voicebridge import config as C
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    tid = _task(cfg)

    reloaded = C.Config.load(tmp_path / "voice.yaml")
    for path in (f"/api/plugins/kanban/tasks/{tid}/attachments",
                 f"/api/plugins/kanban/tasks/{tid}/log"):
        code, out = HC.dispatch(reloaded, "GET", path, {}, {})
        assert code == 200, (path, code, out)


# ---------------------------------------------------------------------------
# The decided boundary: attachment upload stays a typed policy gate
# ---------------------------------------------------------------------------
def test_attachment_upload_stays_policy_gated(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    tid = _task(cfg)
    code, out = HC.dispatch(
        cfg, "POST", f"/api/plugins/kanban/tasks/{tid}/attachments", {},
        {"confirm": True, "file": "x"})
    assert code == 403, (code, out)
    assert out["code"] == "KANBAN_WRITE_BROKERED", out
    assert out["qubit_counterpart"], out

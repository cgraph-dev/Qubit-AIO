"""Kanban dashboard writes are backed by the ONE authoritative native store.

The vendored kanban dashboard plugin's mutation routes used to be a blanket
``403 KANBAN_WRITE_BROKERED`` (dead controls). They now dispatch to real
handlers that mutate ``voicebridge.kanban`` — the same store the compat READ
routes synthesize the board from — through the shared confirm/audit/e-stop
path. These tests prove the round trip on one store and the policy gates.

Hermetic: tmp XDG state only; no network, no live services.
"""
from __future__ import annotations


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _tasks_in_board(cfg, board="main"):
    from voicebridge import qubit_plugins as HP

    code, out = HP.h_plugin_kanban_board(cfg, {"board": board}, {}, {})
    assert code == 200, out
    tasks = [t for col in out["columns"] for t in col["tasks"]]
    return {t["title"]: t for t in tasks}


def test_board_create_requires_confirm_then_is_visible(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as QC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = QC.h_plugin_kanban_boards_create(
        cfg, {}, {"slug": "probe", "name": "Probe"}, {})
    assert code == 409, out
    assert out["code"] == "CONFIRM_REQUIRED"
    assert out["confirmation_id"]

    code, out = QC.h_plugin_kanban_boards_create(
        cfg, {}, {"slug": "probe", "name": "Probe", "confirm": True}, {})
    assert code == 200, out
    assert out["board"]["slug"] == "probe"

    code, out = QC.dispatch(cfg, "GET", "/api/plugins/kanban/boards")
    assert code == 200, out
    assert "probe" in {b["slug"] for b in out["boards"]}


def test_task_create_read_write_share_one_store(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as QC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = QC.h_plugin_kanban_tasks_create(
        cfg, {}, {"title": "write me", "confirm": True}, {})
    assert code == 200, out
    tid = out["task"]["id"]
    assert out["task"]["status"] == "todo"  # native open -> vendored todo

    # The READ route sees the task the WRITE route created (one store).
    assert "write me" in _tasks_in_board(cfg)
    assert _tasks_in_board(cfg)["write me"]["id"] == tid


def test_task_transitions_and_archive_restore(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as QC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = QC.h_plugin_kanban_tasks_create(
        cfg, {}, {"title": "flow", "confirm": True}, {})
    tid = out["task"]["id"]

    code, out = QC.h_plugin_kanban_tasks_patch(
        cfg, {}, {"status": "running", "confirm": True}, {"task_id": tid})
    assert code == 200, out
    assert out["task"]["status"] == "running"

    code, out = QC.h_plugin_kanban_tasks_patch(
        cfg, {}, {"status": "done", "confirm": True}, {"task_id": tid})
    assert code == 200, out
    assert out["task"]["status"] == "done"

    code, out = QC.h_plugin_kanban_tasks_delete(
        cfg, {}, {"confirm": True}, {"task_id": tid})
    assert code == 200, out
    assert out["archived"] is True

    # Archive is reversible on the native store (the working counterpart).
    from voicebridge import kanban as KB
    restored = KB.restore_task(cfg, int(tid), requested_by="test",
                               confirmed=True)
    assert restored["task"]["status"] == "done"


def test_comment_and_bulk_on_native_store(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as QC
    from voicebridge import kanban as KB

    cfg = _cfg(tmp_path, monkeypatch)
    _, out = QC.h_plugin_kanban_tasks_create(
        cfg, {}, {"title": "a", "confirm": True}, {})
    tid = out["task"]["id"]

    code, out = QC.h_plugin_kanban_tasks_comment(
        cfg, {}, {"body": "hello", "confirm": True}, {"task_id": tid})
    assert code == 200, out
    shown = KB.show(cfg, int(tid))["task"]
    assert [c["body"] for c in shown["comments"]] == ["hello"]

    code, out = QC.h_plugin_kanban_tasks_bulk(
        cfg, {}, {"ids": [tid], "priority": 2, "confirm": True}, {})
    assert code == 200, out
    assert out["results"][0]["ok"] is True
    assert KB.show(cfg, int(tid))["task"]["priority"] == "high"


def test_estop_refuses_before_any_write(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as QC

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(QC, "_estop", lambda cfg: True)
    code, out = QC.h_plugin_kanban_tasks_create(
        cfg, {}, {"title": "blocked", "confirm": True}, {})
    assert code == 403, out
    assert out["code"] == "EMERGENCY_STOP"
    assert "blocked" not in _tasks_in_board(cfg)


def test_unmappable_route_stays_typed_refusal(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as QC

    cfg = _cfg(tmp_path, monkeypatch)
    # Board transfer has no native-schema equivalent: typed refusal, not a
    # fabricated success and not a silent no-op.
    code, out = QC.dispatch(cfg, "POST", "/api/plugins/kanban/boards/main/export",
                            body={"output": "/tmp/x.tar.gz"})
    assert code == 403, out
    assert out["code"] == "KANBAN_WRITE_BROKERED"
    assert out["qubit_counterpart"]

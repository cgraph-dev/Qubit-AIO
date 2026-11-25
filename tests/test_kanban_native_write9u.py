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


def test_board_approvals_bind_form_and_replay_banner_fields(tmp_path, monkeypatch):
    from voicebridge import dashboard_ops as OPS
    from voicebridge import kanban as KB
    from voicebridge import qubit_compat as QC

    cfg = _cfg(tmp_path, monkeypatch)
    form = {"title": "Fixture task", "body": "Private fixture notes", "priority": 1}
    code, pending = QC.h_plugin_kanban_tasks_create(cfg, {}, form, {})
    assert code == 409
    cid = pending["confirmation_id"]
    payload = OPS._journal(cfg).get(cid).meta["payload"]
    assert "body" not in payload and payload["form_proof"]
    changed = {**form, "body": "Changed fixture notes", "confirmation_id": cid}
    code, _ = QC.h_plugin_kanban_tasks_create(cfg, {}, changed, {})
    assert code == 409 and "Fixture task" not in _tasks_in_board(cfg)
    code, created = QC.h_plugin_kanban_tasks_create(cfg, {}, {**form, "confirmation_id": cid}, {})
    assert code == 200
    task = created["task"]["id"]

    edit = {"status": "running", "title": "Edited fixture", "assignee": "fixture-owner"}
    code, pending = QC.h_plugin_kanban_tasks_patch(cfg, {}, edit, {"task_id": task})
    assert code == 409
    result = OPS.confirm_compat_op(cfg, pending["confirmation_id"], actor="dashboard:user",
                                   session="dashboard", args={"title": "Replacement"})
    assert result["state"] == "executed", result
    native = KB.show(cfg, int(task))["task"]
    assert native["status"] == "claimed" and native["title"] == "Edited fixture"
    assert native["assignee"] == "fixture-owner" and native["body"] == form["body"]

    code, _ = QC.h_plugin_kanban_tasks_patch(
        cfg, {}, {"status": "done", "priority": 8, "confirm": True}, {"task_id": task})
    assert code == 400
    assert KB.show(cfg, int(task))["task"]["status"] == "claimed"


def test_extra_board_options_refuse_without_staging_or_writing(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as QC

    cfg = _cfg(tmp_path, monkeypatch)
    defaults = {"title": "Fixture card", "triage": False, "workspace_kind": "scratch",
                "parents": [], "goal_mode": False}
    status, staged = QC.h_plugin_kanban_tasks_create(cfg, {}, defaults, {})
    assert status == 409 and staged["confirmation_id"]
    status, refused = QC.h_plugin_kanban_tasks_create(
        cfg, {}, {**defaults, "goal_mode": True, "parents": ["2"]}, {})
    assert status == 400 and refused["code"] == "UNSUPPORTED_FIELD"
    assert "goal_mode" in refused["detail"] and "parents" in refused["detail"]
    assert "confirmation_id" not in refused and not _tasks_in_board(cfg)
    status, created = QC.h_plugin_kanban_tasks_create(
        cfg, {}, {**defaults, "confirmation_id": staged["confirmation_id"]}, {})
    assert status == 200
    assert _tasks_in_board(cfg)["Fixture card"]["status"] == "todo"


def test_completion_note_binds_approval_and_survives_restore(tmp_path, monkeypatch):
    from voicebridge import dashboard_ops as OPS
    from voicebridge import kanban as KB
    from voicebridge import qubit_compat as QC
    from voicebridge import qubit_plugins as QP

    cfg = _cfg(tmp_path, monkeypatch)
    tid = KB.create(cfg, "Fixture card", confirmed=True)["task"]["id"]
    target = {"task_id": str(tid)}
    note = "QA passed. Release checklist signed."
    form = {"status": "done", "summary": note}
    status, refused = QC.h_plugin_kanban_tasks_patch(cfg, {}, {**form, "status": "todo"}, target)
    assert status == 400 and refused["code"] == KB.BAD_INPUT_CODE
    status, refused = QC.h_plugin_kanban_tasks_patch(
        cfg, {}, {**form, "summary": "x" * (KB.MAX_SUMMARY_LEN + 1)}, target)
    assert status == 400 and refused["code"] == KB.BAD_INPUT_CODE
    status, refused = QC.h_plugin_kanban_tasks_patch(
        cfg, {}, {**form, "result": "Conflicting card note"}, target)
    assert status == 400 and refused["code"] == KB.BAD_INPUT_CODE
    assert not OPS._journal(cfg).pending()
    status, approval = QC.h_plugin_kanban_tasks_patch(cfg, {}, {**form, "result": note}, target)
    assert status == 409
    cid = approval["confirmation_id"]
    staged = OPS._journal(cfg).get(cid).meta["payload"]
    assert staged["form_proof"] and "summary" not in staged and "result" not in staged
    assert note not in repr(staged)
    status, next_approval = QC.h_plugin_kanban_tasks_patch(
        cfg, {}, {**form, "summary": "A different note", "confirmation_id": cid}, target)
    assert status == 409 and next_approval["confirmation_id"] != cid
    assert KB.show(cfg, tid)["task"]["status"] == "open"

    status, saved = QC.h_plugin_kanban_tasks_patch(
        cfg, {}, {"status": "done", "result": note, "confirmation_id": cid}, target)
    assert status == 200 and saved["task"]["latest_summary"] == note
    assert _tasks_in_board(cfg)["Fixture card"]["latest_summary"] == note
    status, details = QP.h_plugin_kanban_task(cfg, {}, {}, target)
    assert status == 200 and details["task"]["latest_summary"] == note
    snapshot = KB.export(cfg)["snapshot"]
    KB.archive_task(cfg, tid, confirmed=True)
    assert KB.restore_task(cfg, tid, confirmed=True)["task"]["completion_summary"] == note
    KB.import_snapshot(cfg, snapshot, confirmed=True)
    restored = KB.show(cfg, tid)["task"]
    assert restored["status"] == "done" and restored["completion_summary"] == note
    assert sum(event["kind"] == "complete" for event in restored["events"]) == 1


def test_bulk_card_completion_preserves_the_exact_selection_and_note(tmp_path, monkeypatch):
    from voicebridge import dashboard_ops as OPS
    from voicebridge import kanban as KB
    from voicebridge import qubit_compat as QC

    cfg = _cfg(tmp_path, monkeypatch)
    first, second, other = [KB.create(cfg, title, confirmed=True)["task"]["id"]
                            for title in ("First", "Second", "Unselected")]
    note = "Both checks passed."
    form = {"ids": [str(first), str(second)], "status": "done", "summary": note, "result": note}
    status, approval = QC.h_plugin_kanban_tasks_bulk(cfg, {}, form, {})
    assert status == 409
    cid = approval["confirmation_id"]
    staged = OPS._journal(cfg).get(cid).meta["payload"]
    assert staged["form_proof"] and note not in repr(staged)
    status, different = QC.h_plugin_kanban_tasks_bulk(
        cfg, {}, {**form, "ids": [str(first), str(other)], "confirmation_id": cid}, {})
    assert status == 409 and different["confirmation_id"] != cid
    assert all(KB.show(cfg, tid)["task"]["status"] == "open" for tid in (first, second, other))

    status, settled = QC.h_plugin_kanban_tasks_bulk(cfg, {}, {**form, "confirmation_id": cid}, {})
    assert status == 200 and all(result["ok"] for result in settled["results"])
    for tid in (first, second):
        task = KB.show(cfg, tid)["task"]
        assert (task["status"], task["completion_summary"]) == ("done", note)
        assert sum(event["kind"] == "complete" for event in task["events"]) == 1
    assert KB.show(cfg, other)["task"]["completion_summary"] == ""


def test_voice_completion_note_keeps_exact_words(tmp_path, monkeypatch):
    from voicebridge import kanban as KB
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    tid = KB.create(cfg, "Fixture card", confirmed=True)["task"]["id"]
    note = "QA passed! Discuss “restart gateway” tomorrow."
    resolved = VC.resolve(f"Qubit, complete task {tid} with note: {note}")
    assert resolved.entry.id == "kanban_complete"
    assert resolved.args == {"task_id": tid, "summary": note}
    assert VC.resolve(f"Qubit, complete task {tid} with note").kind == VC.CLARIFY
    ctx = VC.make_ctx(cfg)
    staged = VC.execute(ctx, resolved)
    assert staged.kind == VC.NEEDS_CONFIRM
    assert KB.show(cfg, tid)["task"]["completion_summary"] == ""
    completed = VC.confirm_pending(ctx, staged.confirmation_id)
    assert completed.kind == VC.EXECUTED
    assert KB.show(cfg, tid)["task"]["completion_summary"] == note

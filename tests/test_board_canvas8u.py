"""Final parity — O-board widget canvas: layout, permissions, sandboxing,
rollback, and CLI/dashboard/voice parity on the same kanban backend.

Every test executes the real SQLite canvas (hermetic tmp state dir):
no mocks, no external board process.
"""
from __future__ import annotations

import io
import json
from contextlib import redirect_stdout

import pytest


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _voice(cfg, entry_id, session="v-canvas", **args):
    """Drive a voice entry through the real pipeline, saying yes when asked.

    Returns the final Outcome (kind/speech/receipt)."""
    from voicebridge import voice_commands as VC

    entry = VC.REGISTRY[entry_id]
    ctx = VC.make_ctx(cfg, actor="voice:user", session=session, source="user")
    out = VC.execute(ctx, VC.Resolution(VC.MATCH, entry=entry, args=args))
    if out.kind == VC.NEEDS_CONFIRM:
        out = VC.confirm_pending(ctx, out.confirmation_id)
    return out


def _dash(cfg, op_id, args, session="d-canvas"):
    """Drive a dashboard op through the real pipeline, confirming inline."""
    from voicebridge import dashboard_ops as DO

    out = DO.run_op(cfg, op_id, args, actor="dashboard:user",
                    session=session)
    if out.get("state") == "needs-confirm":
        out = DO.confirm_op(cfg, out["confirmation_id"],
                            actor="dashboard:user", session=session)
    return out


def _cli(cfg, cmd, **kw):
    from voicebridge import cli as CLI

    args = type("A", (), {})()
    args.kanban_command = cmd
    args.slug = kw.get("slug", "")
    args.name = kw.get("name", "")
    args.title = kw.get("title", "")
    args.body = kw.get("body", "")
    args.assignee = kw.get("assignee", "")
    args.priority = kw.get("priority", "normal")
    args.board = kw.get("board", "")
    args.status = kw.get("status", "")
    args.limit = kw.get("limit", 50)
    args.task_id = kw.get("task_id", 0)
    args.widget_id = kw.get("widget_id", 0)
    args.snapshot_id = kw.get("snapshot_id", 0)
    args.snapshot = kw.get("snapshot", "")
    args.kind = kw.get("kind", "note")
    args.content = kw.get("content", "")
    args.columns = kw.get("columns", "")
    args.principal = kw.get("principal", "")
    args.role = kw.get("role", "viewer")
    for k in ("x", "y", "w", "h"):
        setattr(args, k, kw.get(k))
    args.confirm = kw.get("confirm", False)
    args.dry_run = kw.get("dry_run", False)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = CLI._cmd_kanban(cfg, args)
    return rc, json.loads(buf.getvalue() or "{}")


def _setup_board(cfg):
    from voicebridge import kanban as KB

    KB.init(cfg)
    KB.boards_create(cfg, "main", "main", requested_by="t",
                     confirmed=True)
    return KB.create(cfg, "canvas task", board="main", requested_by="t",
                     confirmed=True)["task"]


def test_board_open_read_only(tmp_path, monkeypatch):
    from voicebridge import kanban as KB

    cfg = _cfg(tmp_path, monkeypatch)
    task = _setup_board(cfg)
    out = KB.board_open(cfg, "main")
    assert out["state"] == "OK"
    assert out["columns"] == ["open", "claimed", "blocked", "done"]
    assert out["counts"]["open"] == 1
    assert out["by_column"]["open"][0]["id"] == task["id"]
    assert out["widgets"] == [] and out["restricted"] is False


def test_move_task_board_and_status(tmp_path, monkeypatch):
    from voicebridge import kanban as KB

    cfg = _cfg(tmp_path, monkeypatch)
    task = _setup_board(cfg)
    KB.boards_create(cfg, "home", "home", requested_by="t", confirmed=True)
    out = KB.move_task(cfg, task["id"], board="home", status="claimed",
                       requested_by="t", confirmed=True)
    assert out["state"] == "OK"
    assert out["task"]["board"] == "home"
    assert out["task"]["status"] == "claimed"
    assert out["rollback"].startswith("canvas snapshot #")
    # CLI + voice reach the same backend result.
    rc, cli_out = _cli(cfg, "move", task_id=task["id"], board="main",
                       status="open", confirm=True)
    assert rc == 0 and cli_out["task"]["board"] == "main"
    voiced = _voice(cfg, "kanban_move", task_id=task["id"], board="home",
                    status="blocked")
    assert voiced.kind == "executed"
    assert voiced.receipt["result"]["task"]["status"] == "blocked"


def test_move_requires_confirm_and_refuses_archived(tmp_path, monkeypatch):
    from voicebridge import kanban as KB

    cfg = _cfg(tmp_path, monkeypatch)
    task = _setup_board(cfg)
    with pytest.raises(KB.KanbanError) as exc:
        KB.move_task(cfg, task["id"], board="main", requested_by="t",
                     confirmed=False)
    assert exc.value.code == KB.CONFIRM_CODE
    KB.archive_task(cfg, task["id"], requested_by="t", confirmed=True)
    with pytest.raises(KB.KanbanError) as exc2:
        KB.move_task(cfg, task["id"], status="done", requested_by="t",
                     confirmed=True)
    assert exc2.value.code == KB.STATE_CODE


def test_widget_sandbox_strips_active_markup(tmp_path, monkeypatch):
    from voicebridge import kanban as KB

    cfg = _cfg(tmp_path, monkeypatch)
    _setup_board(cfg)
    hostile = ("<script>alert(1)</script>hello "
               "<iframe src=x></iframe><b onload=\"x()\">b</b> "
               "<a href=\"javascript:evil()\">click</a>")
    out = KB.widget_add(cfg, "main", "note", "nasty", content=hostile,
                        requested_by="t", confirmed=True)
    assert out["state"] == "OK"
    assert out["widget"]["sanitized"] is True
    assert out["widget"]["removed_fragments"] >= 3
    stored = out["widget"]["content"]
    assert "<script" not in stored and "<iframe" not in stored
    assert "onload" not in stored and "javascript:" not in stored
    assert "hello" in stored


def test_widget_kind_and_bounds_rejected(tmp_path, monkeypatch):
    from voicebridge import kanban as KB

    cfg = _cfg(tmp_path, monkeypatch)
    _setup_board(cfg)
    with pytest.raises(KB.KanbanError):
        KB.widget_add(cfg, "main", "terminal", "t", requested_by="t",
                      confirmed=True)
    with pytest.raises(KB.KanbanError):
        KB.widget_add(cfg, "main", "note", "t", x=99, requested_by="t",
                      confirmed=True)
    with pytest.raises(KB.KanbanError):
        KB.widget_add(cfg, "main", "note", "t",
                      content="x" * (KB.WIDGET_CONTENT_LEN + 1),
                      requested_by="t", confirmed=True)


def test_layout_set_show_and_validation(tmp_path, monkeypatch):
    from voicebridge import kanban as KB

    cfg = _cfg(tmp_path, monkeypatch)
    _setup_board(cfg)
    out = KB.layout_set(cfg, "main", ["open", "claimed", "done"],
                        requested_by="t", confirmed=True)
    assert out["columns"] == ["open", "claimed", "done"]
    shown = KB.layout_show(cfg, "main")
    assert shown["columns"] == ["open", "claimed", "done"]
    assert shown["custom"] is True
    with pytest.raises(KB.KanbanError):
        KB.layout_set(cfg, "main", ["open", "bogus"], requested_by="t",
                      confirmed=True)
    with pytest.raises(KB.KanbanError):
        KB.layout_set(cfg, "main", [], requested_by="t", confirmed=True)


def test_roles_restrict_delegate_principals(tmp_path, monkeypatch):
    from voicebridge import kanban as KB

    cfg = _cfg(tmp_path, monkeypatch)
    _setup_board(cfg)
    # Unrestricted boards stay usable everywhere (tri-parity safe).
    assert KB.roles_list(cfg, "main")["restricted"] is False
    KB.role_grant(cfg, "main", "alice", "editor", requested_by="t",
                  confirmed=True)
    KB.role_grant(cfg, "main", "mallory", "viewer", requested_by="t",
                  confirmed=True)
    assert KB.roles_list(cfg, "main")["restricted"] is True
    # A viewer principal cannot hold an assignment on a restricted board.
    task = KB.create(cfg, "job", board="main", assignee="mallory",
                     requested_by="t", confirmed=True)["task"]
    with pytest.raises(KB.KanbanError) as exc:
        KB.move_task(cfg, task["id"], status="claimed", requested_by="t",
                     confirmed=True)
    assert exc.value.code == KB.DENIED_CODE
    # Editors pass; revoking all roles unrestricts the board again.
    KB.role_revoke(cfg, "main", "mallory", requested_by="t", confirmed=True)
    KB.role_revoke(cfg, "main", "alice", requested_by="t", confirmed=True)
    assert KB.roles_list(cfg, "main")["restricted"] is False
    out = KB.move_task(cfg, task["id"], status="claimed", requested_by="t",
                       confirmed=True)
    assert out["task"]["status"] == "claimed"


def test_snapshot_rollback_restores_canvas(tmp_path, monkeypatch):
    from voicebridge import kanban as KB

    cfg = _cfg(tmp_path, monkeypatch)
    _setup_board(cfg)
    w = KB.widget_add(cfg, "main", "note", "keep me", requested_by="t",
                      confirmed=True)["widget"]
    snaps = KB.snapshot_list(cfg, "main")
    assert snaps["snapshots"], "mutations must auto-snapshot"
    KB.widget_remove(cfg, w["id"], requested_by="t", confirmed=True)
    assert KB.widget_list(cfg, "main")["widgets"] == []
    snaps = KB.snapshot_list(cfg, "main")
    target = snaps["snapshots"][0]["id"]
    out = KB.snapshot_restore(cfg, target, requested_by="t", confirmed=True)
    assert out["state"] == "OK"
    assert out["pre_restore_backup"] > target
    titles = [x["title"] for x in KB.widget_list(cfg, "main")["widgets"]]
    assert "keep me" in titles


def test_canvas_estop_refuses_mutations(tmp_path, monkeypatch):
    from voicebridge import kanban as KB

    cfg = _cfg(tmp_path, monkeypatch)
    _setup_board(cfg)
    monkeypatch.setattr(KB, "estop_engaged", lambda _c: True)
    for call in (lambda: KB.move_task(cfg, 1, status="done",
                                      requested_by="t", confirmed=True),
                 lambda: KB.widget_add(cfg, "main", "note", "x",
                                       requested_by="t", confirmed=True),
                 lambda: KB.layout_set(cfg, "main", ["open"],
                                       requested_by="t", confirmed=True),
                 lambda: KB.snapshot_restore(cfg, 1, requested_by="t",
                                             confirmed=True)):
        with pytest.raises(KB.KanbanError) as exc:
            call()
        assert exc.value.code == KB.ESTOP_CODE


def test_cli_dashboard_voice_parity(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _setup_board(cfg)
    rc, opened = _cli(cfg, "open", board="main")
    assert rc == 0 and opened["state"] == "OK"
    rc, added = _cli(cfg, "widget-add", board="main", kind="note",
                     title="cli note", content="hi", x=1, y=2, w=4, h=3,
                     confirm=True)
    assert rc == 0 and added["widget"]["title"] == "cli note"
    voiced = _voice(cfg, "kanban_open", board="main")
    assert voiced.kind == "executed"
    assert len(voiced.receipt["result"]["widgets"]) >= 1
    dash = _dash(cfg, "kanban_open", {"board": "main"})
    assert dash["state"] == "executed", dash
    assert dash["receipt"]["result"]["board"] == "main"
    # Tier-2 dashboard extras call the same backend functions.
    moved = _dash(cfg, "kanban_widget_move",
                  {"widget_id": str(added["widget"]["id"]), "x": "5"})
    assert moved["state"] == "executed", moved
    assert moved["receipt"]["result"]["widget"]["x"] == 5
    restored_roles = _dash(cfg, "kanban_role_grant",
                           {"board": "main", "principal": "dash-ed",
                            "role": "editor"})
    assert restored_roles["state"] == "executed", restored_roles
    revoked = _dash(cfg, "kanban_role_revoke",
                    {"board": "main", "principal": "dash-ed"})
    assert revoked["state"] == "executed"
    assert revoked["receipt"]["result"]["revoked"] is True


def test_voice_move_and_widget_lifecycle(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    task = _setup_board(cfg)
    created = _voice(cfg, "kanban_widget_add", title="voice note",
                     board="main", kind="note", content="remember this")
    assert created.kind == "executed"
    wid = created.receipt["result"]["widget"]["id"]
    listed = _voice(cfg, "kanban_widget_list", board="main")
    assert any(w["id"] == wid for w in listed.receipt["result"]["widgets"])
    moved = _voice(cfg, "kanban_move", task_id=task["id"], board="main",
                   status="claimed")
    assert moved.receipt["result"]["task"]["status"] == "claimed"
    removed = _voice(cfg, "kanban_widget_remove", widget_id=wid)
    assert removed.receipt["result"]["removed"] == wid


def test_plugin_widget_kind_and_show_widget(tmp_path, monkeypatch):
    """OC-36: plugin widget kind + the read-only ``show_widget`` surface.

    A plugin widget is an inert JSON descriptor gated by the review-only
    ``plugin:read`` grant (declaring it adds no execution capability), and
    ``show_widget`` returns the redacted render contract without rendering,
    fetching, executing, or mutating anything."""
    from voicebridge import kanban as KB

    cfg = _cfg(tmp_path, monkeypatch)
    _setup_board(cfg)
    desc = '{"name":"clock","version":"1.0","props":{"tz":"UTC"}}'
    out = KB.widget_add(cfg, "main", "plugin", "clock widget",
                        content=desc, declared_grants=["plugin:read"],
                        requested_by="t", confirmed=True)
    assert out["state"] == "OK"
    widget = out["widget"]
    assert widget["kind"] == "plugin"
    assert widget["inert"] is True and widget["executable"] is False
    assert widget["trust"] == "inert"
    assert widget["grants"] == ["plugin:read"]

    shown = KB.show_widget(cfg, widget["id"])
    assert shown["state"] == "OK"
    assert shown["render"] == {"kind": "plugin", "trust": "inert",
                               "executable": False,
                               "sandbox": "inert-html"}
    assert shown["widget"]["content"] == (
        '{"name":"clock","props":{"tz":"UTC"},"version":"1.0"}')
    # show_widget is genuinely read-only: it survives the e-stop, and an
    # unknown id is a typed error, never a fabricated widget.
    monkeypatch.setattr(KB, "estop_engaged", lambda _c: True)
    assert KB.show_widget(cfg, widget["id"])["state"] == "OK"
    with pytest.raises(KB.KanbanError) as exc:
        KB.show_widget(cfg, 9999)
    assert exc.value.code == KB.NOT_FOUND_CODE


def test_plugin_widget_access_and_validation(tmp_path, monkeypatch):
    """OC-36: plugin access control + descriptor validation (negative)."""
    from voicebridge import kanban as KB

    cfg = _cfg(tmp_path, monkeypatch)
    _setup_board(cfg)
    # Access control: a plugin widget requires the declared review-only
    # grant; declaring it never grants execution, but omitting it is denied.
    with pytest.raises(KB.KanbanError) as exc:
        KB.widget_add(cfg, "main", "plugin", "p", content='{"name":"x"}',
                      requested_by="t", confirmed=True)
    assert exc.value.code == KB.DENIED_CODE
    # Unknown declared grants stay refused by the closed allow-list.
    with pytest.raises(KB.KanbanError):
        KB.widget_add(cfg, "main", "plugin", "p", content='{"name":"ok"}',
                      declared_grants=["plugin:exec"], requested_by="t",
                      confirmed=True)
    # The descriptor must be a bounded JSON object: a safe name slug and a
    # closed key set only (no code, URLs, or unknown behavior contracts).
    for bad in ("not json", '["a"]', '{"name":"Bad Name!"}',
                '{"name":"ok","exec":"rm -rf /"}',
                '{"name":"ok","props":"nope"}'):
        with pytest.raises(KB.KanbanError):
            KB.widget_add(cfg, "main", "plugin", "p", content=bad,
                          declared_grants=["plugin:read"], requested_by="t",
                          confirmed=True)


def test_voice_kanban_task_lifecycle_and_widget_move(tmp_path, monkeypatch):
    """qubit-ledger kanban rows: assign/block/comment/complete/edit/
    reassign/reclaim/unblock + widget-move voice entries all reach the same
    real backend functions (no read/write divergence)."""
    from voicebridge import kanban as KB

    cfg = _cfg(tmp_path, monkeypatch)
    task = _setup_board(cfg)
    tid = task["id"]
    assert _voice(cfg, "kanban_assign", task_id=tid,
                  assignee="sam").receipt["result"]["task"]["assignee"] == "sam"
    assert _voice(cfg, "kanban_reassign", task_id=tid,
                  assignee="jo").receipt["result"]["task"]["assignee"] == "jo"
    assert _voice(cfg, "kanban_reclaim", task_id=tid).receipt["result"][
        "task"]["status"] == "open"
    assert _voice(cfg, "kanban_block", task_id=tid).receipt["result"][
        "task"]["status"] == "blocked"
    assert _voice(cfg, "kanban_unblock", task_id=tid).receipt["result"][
        "task"]["status"] == "open"
    edited = _voice(cfg, "kanban_edit", task_id=tid, title="Edited title")
    assert edited.receipt["result"]["task"]["title"] == "Edited title"
    commented = _voice(cfg, "kanban_comment", task_id=tid, body="looks good")
    assert commented.receipt["result"]["comment_id"] >= 1
    assert _voice(cfg, "kanban_complete", task_id=tid).receipt["result"][
        "task"]["status"] == "done"

    widget = KB.widget_add(cfg, "main", "note", "w", requested_by="t",
                           confirmed=True)["widget"]
    moved = _voice(cfg, "kanban_widget_move_voice", widget_id=widget["id"],
                   geometry="x=3 y=4")
    assert moved.receipt["result"]["widget"]["x"] == 3
    assert moved.receipt["result"]["widget"]["y"] == 4


def test_atomic_claim_single_winner(tmp_path, monkeypatch):
    """Explicit-transaction discipline (H-sqlite-txn equivalent): concurrent
    claimants serialize on BEGIN IMMEDIATE; exactly one wins, the loser gets
    a typed state error — never a double-claim."""
    import threading

    from voicebridge import kanban as KB

    cfg = _cfg(tmp_path, monkeypatch)
    task = _setup_board(cfg)
    outcomes = []

    def _claim(who):
        try:
            KB.claim(cfg, task["id"], assignee=who, requested_by=who,
                     confirmed=True)
            outcomes.append((who, "won"))
        except KB.KanbanError as exc:
            outcomes.append((who, exc.code))

    threads = [threading.Thread(target=_claim, args=(f"w{i}",))
               for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert len(outcomes) == 8
    assert sum(1 for _, res in outcomes if res == "won") == 1
    assert KB.show(cfg, task["id"])["task"]["status"] == "claimed"

"""Packet 8T-F — Hermes `kanban` parity: local task boards, Qubit-owned.

Proves the board lifecycle is real (boards, atomic claims, comments,
state machine, export/import rollback) and execution-shaped work is
typed-delegated (swarm/dispatch/daemon/...) while remaining Hermes
subcommands are typed-refused (never silently omitted).
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


class _Args:
    def __init__(self, cmd, **kw):
        self.kanban_command = cmd
        self.slug = kw.get("slug", "")
        self.name = kw.get("name", "")
        self.title = kw.get("title", "")
        self.body = kw.get("body", "")
        self.assignee = kw.get("assignee", "")
        self.priority = kw.get("priority", "normal")
        self.board = kw.get("board", "")
        self.status = kw.get("status", "")
        self.limit = kw.get("limit", 50)
        self.task_id = kw.get("task_id", 0)
        self.snapshot = kw.get("snapshot", "")
        self.confirm = kw.get("confirm", False)
        self.dry_run = kw.get("dry_run", False)


def _run(cfg, args):
    from voicebridge import cli as CLI

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = CLI._cmd_kanban(cfg, args)
    return rc, json.loads(buf.getvalue())


def _board(cfg, slug="team"):
    assert _run(cfg, _Args("boards-create", slug=slug, confirm=True))[0] == 0


def _task(cfg, title="Ship it", **kw):
    kw.setdefault("board", "team")
    rc, out = _run(cfg, _Args("create", title=title, confirm=True, **kw))
    assert rc == 0, out
    return out["task"]["id"]


# ------------------------------------------------- lifecycle
def test_init_boards_create_list(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    assert _run(cfg, _Args("init"))[0] == 0
    assert _run(cfg, _Args("init"))[0] == 0, "idempotent"
    assert _run(cfg, _Args("boards"))[1]["boards"] == []
    assert _run(cfg, _Args("boards-create", slug="team", confirm=True))[0] == 0
    rc, out = _run(cfg, _Args("boards"))
    assert rc == 0 and [b["board"] for b in out["boards"]] == ["team"]
    assert out["boards"][0]["counts"]["open"] == 0


def test_task_lifecycle_claim_complete(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _board(cfg)
    tid = _task(cfg)
    rc, out = _run(cfg, _Args("claim", task_id=tid, confirm=True))
    assert rc == 0 and out["task"]["status"] == "claimed"
    assert out["task"]["assignee"] == "cli:user"
    rc, out = _run(cfg, _Args("claim", task_id=tid, confirm=True))
    assert rc == 2 and out["code"] == "KANBAN_BAD_STATE", \
        "double-claim is a typed error, never a double assignment"
    assert _run(cfg, _Args("complete", task_id=tid, confirm=True))[0] == 0
    assert _run(cfg, _Args("show", task_id=tid))[1]["task"]["status"] == "done"


def test_block_unblock_archive_and_edit(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _board(cfg)
    tid = _task(cfg)
    assert _run(cfg, _Args("block", task_id=tid, confirm=True))[0] == 0
    assert _run(cfg, _Args("claim", task_id=tid, confirm=True))[0] == 0, \
        "blocked tasks stay claimable (Hermes parity)"
    assert _run(cfg, _Args("unblock", task_id=tid, confirm=True))[1][
        "task"]["status"] == "open"
    rc, out = _run(cfg, _Args("edit", task_id=tid, title="New title",
                              priority="high", confirm=True))
    assert rc == 0 and out["task"]["title"] == "New title"
    assert out["task"]["priority"] == "high"
    assert _run(cfg, _Args("archive", task_id=tid, confirm=True))[0] == 0
    assert _run(cfg, _Args("list"))[1]["tasks"] == []
    assert len(_run(cfg, _Args("list", status="archived"))[1]["tasks"]) == 1
    rc, out = _run(cfg, _Args("edit", task_id=tid, title="X", confirm=True))
    assert rc == 2 and out["code"] == "KANBAN_BAD_STATE", \
        "archived tasks are immutable"


def test_assign_reassign_reclaim(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _board(cfg)
    tid = _task(cfg)
    assert _run(cfg, _Args("claim", task_id=tid, assignee="sam",
                           confirm=True))[1]["task"]["assignee"] == "sam"
    assert _run(cfg, _Args("reassign", task_id=tid, assignee="jo",
                           confirm=True))[1]["task"]["assignee"] == "jo"
    rc, out = _run(cfg, _Args("reclaim", task_id=tid, confirm=True))
    assert rc == 0 and out["task"]["status"] == "open"
    assert out["task"]["assignee"] == ""


def test_comment_and_log_and_stats(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _board(cfg)
    tid = _task(cfg)
    rc, out = _run(cfg, _Args("comment", task_id=tid, body="looks good",
                              confirm=True))
    assert rc == 0 and out["comment_id"] >= 1
    task = _run(cfg, _Args("show", task_id=tid))[1]["task"]
    assert [c["body"] for c in task["comments"]] == ["looks good"]
    assert any(e["kind"] == "comment" for e in task["events"])
    assert _run(cfg, _Args("stats", board="team"))[1]["stats"]["team"]["open"] == 1
    assert len(_run(cfg, _Args("log", board="team"))[1]["events"]) >= 2


def test_confirm_required_and_dry_run(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _board(cfg)
    rc, out = _run(cfg, _Args("create", title="X"))
    assert rc == 3 and out["state"] == "denied"
    rc, out = _run(cfg, _Args("create", title="X", confirm=True, dry_run=True))
    assert rc == 0 and out["state"] == "preview"
    assert _run(cfg, _Args("list"))[1]["tasks"] == []


def test_export_import_roundtrip_is_rollback(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _board(cfg)
    _task(cfg, title="keep")
    snap = _run(cfg, _Args("export"))[1]["snapshot"]
    assert snap["schema"] == "qubit-kanban/1"
    assert len(snap["tasks"]) == 1
    _task(cfg, title="extra")
    snap_file = tmp_path / "snap.json"
    snap_file.write_text(json.dumps(snap))
    rc, out = _run(cfg, _Args("import", snapshot=str(snap_file), confirm=True))
    assert rc == 0 and out["restored_tasks"] == 1, out
    titles = [t["title"] for t in _run(cfg, _Args("list"))[1]["tasks"]]
    assert titles == ["keep"], "import restores the snapshot exactly"


def test_bad_input_and_limits(tmp_path, monkeypatch):
    from voicebridge import kanban as KB

    cfg = _cfg(tmp_path, monkeypatch)
    _board(cfg)
    rc, out = _run(cfg, _Args("create", title="  ", confirm=True))
    assert rc == 2 and out["code"] == "KANBAN_BAD_INPUT", out
    rc, out = _run(cfg, _Args("create", title="t", board="Nope", confirm=True))
    assert rc == 2 and out["code"] == "KANBAN_NOT_FOUND", out
    rc, out = _run(cfg, _Args("show", task_id=4242))
    assert rc == 2 and out["code"] == "KANBAN_NOT_FOUND", out
    rc, out = _run(cfg, _Args("comment", task_id=1, body="x" * (
        KB.MAX_COMMENT_LEN + 1), confirm=True))
    assert rc == 2, out
    rc, out = _run(cfg, _Args("list", limit=100000))
    assert rc == 0 and out["limit"] == KB.LIST_MAX, "list bound enforced"


def test_output_redacted(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _board(cfg)
    tid = _task(cfg, title="rotate token=abc123secret!")
    blob = json.dumps(_run(cfg, _Args("show", task_id=tid))[1])
    assert "abc123secret" not in blob and "[REDACTED]" in blob


# ------------------------------------------------- delegated + unsupported
@pytest.mark.parametrize("sub", ["swarm", "dispatch", "daemon", "watch",
                                 "notify-subscribe", "notify-list",
                                 "notify-unsubscribe", "heartbeat", "runs"])
def test_execution_subcommands_delegate_without_starting(tmp_path, monkeypatch, sub):
    cfg = _cfg(tmp_path, monkeypatch)
    rc, out = _run(cfg, _Args(sub))
    assert rc == 2, out
    assert out["code"] == "KANBAN_DELEGATED", out
    assert out["rollback"] == "nothing started; nothing to roll back"


@pytest.mark.parametrize("sub", ["decompose", "attach", "promote", "schedule",
                                 "diagnostics", "gc", "repair", "tail"])
def test_remaining_subcommands_typed_refused(tmp_path, monkeypatch, sub):
    cfg = _cfg(tmp_path, monkeypatch)
    rc, out = _run(cfg, _Args(sub))
    assert rc == 2, out
    assert out["code"] == "KANBAN_UNSUPPORTED", out
    assert out["subcommand"] == sub


def test_estop_latch_refuses_mutations_but_not_reads(tmp_path, monkeypatch):
    from voicebridge.power import power_control_store

    cfg = _cfg(tmp_path, monkeypatch)
    _board(cfg)
    tid = _task(cfg)
    power_control_store(cfg).set(emergency_stop=True, global_disable=False,
                                 issued_by="cli:user")
    try:
        rc, out = _run(cfg, _Args("claim", task_id=tid, confirm=True))
        assert rc == 3 and out["code"] == "KANBAN_ESTOP", out
        assert _run(cfg, _Args("show", task_id=tid))[1]["task"]["status"] == "open"
        assert _run(cfg, _Args("list"))[0] == 0, "reads stay available"
    finally:
        power_control_store(cfg).set(emergency_stop=False, global_disable=False,
                                     issued_by="cli:user")


def test_audit_journal_records_mutations(tmp_path, monkeypatch):
    from voicebridge.logging_setup import setup

    cfg = _cfg(tmp_path, monkeypatch)
    setup(level="INFO", jsonl=True, state_dir=cfg.state_dir())
    try:
        _board(cfg)
        _task(cfg)
        blob = (cfg.state_dir() / "events.jsonl").read_text()
        assert "kanban.create" in blob
    finally:
        from voicebridge import logging_setup as LS

        LS._EVENT_FILE = None


# ------------------------------------------------- project binding loop
def test_project_bind_board_roundtrip(tmp_path, monkeypatch):
    from voicebridge import cli as CLI
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    cfg = C.Config.load(tmp_path / "voice.yaml")

    work = tmp_path / "work"
    work.mkdir()

    class _P:
        def __init__(self, **kw):
            self.project_command = kw.get("cmd", "create")
            self.__dict__.update(kw)

    def prun(**kw):
        cmd = kw.pop("cmd", "create")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = CLI._cmd_project(cfg, _P(project_command=cmd, **kw))
        return rc, json.loads(buf.getvalue() or "{}")

    assert prun(name="Site", folders=[str(work)], confirm=True)[0] == 0
    rc, out = prun(cmd="bind-board", slug="site", board="team", confirm=True)
    assert rc == 2 and out["code"] == "PROJECT_NO_BOARD", out
    _board(cfg)
    rc, out = prun(cmd="bind-board", slug="site", board="team", confirm=True)
    assert rc == 0 and out["project"]["board"] == "team", out
    assert prun(cmd="show", slug="site")[1]["project"]["board"] == "team"
    rc, out = prun(cmd="bind-board", slug="site", unbind=True, confirm=True)
    assert rc == 0 and out["project"]["board"] is None, out


# ------------------------------------------------- catalog + parser
def test_capability_catalog_reports_kanban_implemented():
    from voicebridge.capability_catalog import check

    out = check("kanban")
    assert out["state"] == "IMPLEMENTED", out
    assert out["code"] == "OK"


def test_kanban_surface_in_parser_help():
    from voicebridge import cli as CLI

    assert "kanban" in CLI.build_parser().format_help()

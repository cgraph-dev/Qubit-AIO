"""QBPV2 W4 — Kanban board/task lifecycle end-to-end, real callers.

OpenClaw's pinned workboard (`extensions/workboard/`, en locale
``archiveCard``/``unarchiveCard: "Restore from archive"`` and the
``archived``/``unarchived`` change events) supports a full card lifecycle.
This proves Qubit's equivalent reaches the SAME persisted SQLite store
through the three real production callers:

* CLI      ``voicebridge.cli.main(["kanban", ...])``
* dashboard ``voicebridge.dashboard_ops.run_op`` + ``confirm_op``
* voice     ``voicebridge.voice_commands.voice_say`` + ``confirm_pending``

Board/task create, move, edit, assign, block, complete, archive and restore
all round-trip, survive a config reload (persistence), and share the one
confirmation journal (stage on one surface, settle on another). Nothing is
mocked and no surface gets a private execution path.
"""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest


def _env(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return p


def _cfg(path):
    from voicebridge.config import Config

    return Config.load(path)


def _cli(path, *argv):
    """Run the real CLI entry point; return (rc, parsed-json-or-None)."""
    from voicebridge import cli

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cli.main([*argv, "--config", str(path)])
    text = buf.getvalue().strip()
    try:
        body = json.loads(text) if text else None
    except ValueError:
        body = None
    return rc, body


def _dash(cfg, op, args=None, session="dash"):
    from voicebridge import dashboard_ops as OPS

    return OPS.run_op(cfg, op, dict(args or {}), actor="dashboard:user",
                      session=session)


def _dash_confirm(cfg, cid, session="dash"):
    from voicebridge import dashboard_ops as OPS

    return OPS.confirm_op(cfg, cid, actor="dashboard:user", session=session)


def _dash_cancel(cfg, cid, session="dash"):
    from voicebridge import dashboard_ops as OPS

    return OPS.cancel_op(cfg, cid, actor="dashboard:user", session=session)


def _say(cfg, text, session="voice"):
    from voicebridge import voice_commands as VC

    return VC.voice_say(cfg, text, actor="voice:user", session=session,
                        source="user")


def _say_confirm(cfg, cid, session="voice"):
    from voicebridge import voice_commands as VC

    ctx = VC.make_ctx(cfg, actor="voice:user", session=session, source="user")
    return VC.confirm_pending(ctx, cid)


def _seed(path):
    """Create board + task through the real CLI; return the task id."""
    assert _cli(path, "kanban", "init")[0] == 0
    assert _cli(path, "kanban", "boards-create", "team",
                "--name", "Team", "--confirm")[0] == 0
    rc, out = _cli(path, "kanban", "create", "Ship it",
                   "--board", "team", "--body", "the release", "--confirm")
    assert rc == 0 and out["state"] == "OK", (rc, out)
    return out["task"]["id"]


def _task_status(cfg, task_id):
    from voicebridge import kanban as KB

    return KB.show(cfg, task_id)["task"]["status"]


# --------------------------------------------------------- lifecycle
def test_board_and_task_create_persist_across_reload(tmp_path, monkeypatch):
    path = _env(tmp_path, monkeypatch)
    task_id = _seed(path)

    # Re-open config from disk: the board/task must still be there.
    cfg = _cfg(path)
    from voicebridge import kanban as KB

    boards = {b["board"] for b in KB.boards_list(cfg)["boards"]}
    assert "team" in boards, boards
    task = KB.show(cfg, task_id)["task"]
    assert task["title"] == "Ship it" and task["board"] == "team"
    assert task["body"] == "the release"


def test_all_surfaces_read_the_same_persisted_task(tmp_path, monkeypatch):
    path = _env(tmp_path, monkeypatch)
    task_id = _seed(path)
    cfg = _cfg(path)

    cli_tasks = _cli(path, "kanban", "list", "--board", "team")[1]["tasks"]
    dash = _dash(cfg, "kanban_list", {"board": "team"})
    said = _say(cfg, "qubit, list tasks")

    cli_ids = sorted(t["id"] for t in cli_tasks)
    assert cli_ids == [task_id], cli_ids
    assert sorted(t["id"] for t in dash["receipt"]["result"]["tasks"]) == cli_ids
    assert said["state"] == "executed", said
    assert sorted(t["id"] for t in said["receipt"]["result"]["tasks"]) == cli_ids


def test_assign_block_unblock_complete_via_cli(tmp_path, monkeypatch):
    path = _env(tmp_path, monkeypatch)
    task_id = _seed(path)

    rc, out = _cli(path, "kanban", "assign", str(task_id),
                   "--assignee", "sam", "--confirm")
    assert rc == 0 and out["task"]["assignee"] == "sam", out

    for cmd, want in (("block", "blocked"), ("unblock", "open"),
                      ("complete", "done")):
        rc, out = _cli(path, "kanban", cmd, str(task_id), "--confirm")
        assert rc == 0 and out["task"]["status"] == want, (cmd, out)


def test_edit_and_move_round_trip(tmp_path, monkeypatch):
    path = _env(tmp_path, monkeypatch)
    task_id = _seed(path)
    cfg = _cfg(path)

    rc, out = _cli(path, "kanban", "edit", str(task_id),
                   "--title", "Ship it now", "--priority", "high", "--confirm")
    assert rc == 0 and out["task"]["title"] == "Ship it now", out
    assert out["task"]["priority"] == "high"

    # Move across columns, then across a second board (real persistence).
    _cli(path, "kanban", "boards-create", "ops", "--confirm")
    rc, out = _cli(path, "kanban", "move", str(task_id),
                   "--board", "ops", "--status", "claimed", "--confirm")
    assert rc == 0, out
    assert out["task"]["board"] == "ops" and out["task"]["status"] == "claimed"
    assert _task_status(_cfg(path), task_id) == "claimed"


def test_archive_then_restore_returns_to_prior_column(tmp_path, monkeypatch):
    """The OpenClaw "Restore from archive" equivalent, driven from voice."""
    path = _env(tmp_path, monkeypatch)
    task_id = _seed(path)
    cfg = _cfg(path)

    # Complete (status=done), then archive via the dashboard.
    assert _cli(path, "kanban", "complete", str(task_id), "--confirm")[0] == 0
    staged = _dash(cfg, "kanban_archive", {"task_id": task_id}, session="d")
    assert staged["state"] == "needs-confirm", staged
    archived = _dash_confirm(cfg, staged["confirmation_id"], session="d")
    assert archived["state"] == "executed", archived
    assert _task_status(cfg, task_id) == "archived"

    # an archived task refuses a normal move and cannot be re-archived.
    assert _cli(path, "kanban", "move", str(task_id),
                "--status", "open", "--confirm")[1]["code"] == \
        "KANBAN_BAD_STATE"
    assert _cli(path, "kanban", "archive", str(task_id),
                "--confirm")[1]["code"] == "KANBAN_BAD_STATE"

    # Voice stages the restore and confirms it back to the pre-archive status.
    said = _say(cfg, "qubit, restore task %d" % task_id, session="v")
    assert said["state"] == "needs-confirm", said
    assert said.get("route") in ("kanban_restore", "restore_task"), said
    done = _say_confirm(cfg, said["confirmation_id"], session="v")
    assert done.kind == "executed", (done.kind, done.speech)
    assert _task_status(cfg, task_id) == "done"


def test_restore_stages_on_dashboard_settles_on_voice_same_journal(
        tmp_path, monkeypatch):
    """One confirmation journal shared across the surfaces."""
    path = _env(tmp_path, monkeypatch)
    task_id = _seed(path)
    cfg = _cfg(path)

    staged = _dash(cfg, "kanban_archive", {"task_id": task_id}, session="x")
    assert staged["state"] == "needs-confirm", staged
    # Settle the dashboard-staged write from the voice guard path.
    from voicebridge import voice_commands as VC

    ctx = VC.make_ctx(cfg, actor="voice:user", session="x", source="user")
    settled = VC.confirm_pending(ctx, staged["confirmation_id"])
    assert settled.kind == "executed", (settled.kind, settled.speech)
    assert _task_status(cfg, task_id) == "archived"

    # Restore-staged on voice, settled on the dashboard, same journal+session.
    said = _say(cfg, "qubit, restore task %d" % task_id, session="x")
    assert said["state"] == "needs-confirm", said
    done = _dash_confirm(cfg, said["confirmation_id"], session="x")
    assert done["state"] == "executed", done
    assert _task_status(cfg, task_id) == "open"

    # Replay of the spent id is a typed failure, never a second execution.
    replay = _dash_confirm(cfg, said["confirmation_id"], session="x")
    assert replay["state"] == "failed"
    assert replay.get("code") in ("NO_CONFIRMATION", "ALREADY_DONE")


def test_cancelled_restore_leaves_task_archived(tmp_path, monkeypatch):
    path = _env(tmp_path, monkeypatch)
    task_id = _seed(path)
    cfg = _cfg(path)
    stage = _dash(cfg, "kanban_archive", {"task_id": task_id}, session="c")
    assert stage["state"] == "needs-confirm", stage
    assert _dash_confirm(cfg, stage["confirmation_id"], session="c")["state"] \
        == "executed"
    assert _task_status(cfg, task_id) == "archived"

    said = _say(cfg, "qubit, restore task %d" % task_id, session="c")
    assert said["state"] == "needs-confirm", said
    cancelled = _dash_cancel(cfg, said["confirmation_id"], session="c")
    assert cancelled["state"] == "executed", cancelled
    assert _task_status(cfg, task_id) == "archived"
    gone = _dash_confirm(cfg, said["confirmation_id"], session="c")
    assert gone["state"] == "failed"


def test_restore_catalog_interfaces_are_honest():
    """The catalog must not claim a `cli` surface that does not exist."""
    from voicebridge import dashboard_ops as OPS
    from voicebridge import voice_commands as VC

    item = {i["id"]: i for i in OPS.catalog()}["kanban_restore"]
    assert item["tier"] == "registry"
    assert "voice" in item["interfaces"]
    assert "dashboard" in item["interfaces"]
    if "cli" not in item["interfaces"]:
        assert item.get("interface_reason"), item
    assert "kanban_restore" in VC.REGISTRY
    assert VC.REGISTRY["kanban_restore"].backend in VC._BACKENDS

"""QBPV2 Agent 4 — kanban snapshot LIST tri-parity (repair handoff #1).

Agent 3 reported that the Boards page had no broker op for listing canvas
snapshots. Verification against the real frontend call site found that the
Boards bundle only ever calls ``kanban_snapshot_restore`` (restore-by-id); it
does not call any snapshot-list op, and no equivalent listing surface existed
on CLI/dashboard/voice either. The frozen Kanban requirements cover snapshots
(auto restore points), restore, and recovery, which ``snapshot_restore``
already satisfies.

The underlying capability was nonetheless a genuine tri-parity gap: the CLI
``qubit kanban snapshots`` read had no dashboard or voice equivalent. This
wave exposes the existing ``kanban.snapshot_list`` backend as one shared
registry op (``kanban_snapshots``) reachable from all three surfaces through
the same ``dashboard_ops`` / ``voice_commands`` path.

These tests prove the op is real (not a tile), read-only, and returns the
identical backend result on CLI, dashboard, and voice.
"""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BOARDS_JS = (REPO_ROOT / "frontend" / "hermes-qubit" / "plugins"
             / "qubit-boards" / "qubit-boards.js")


def _env(tmp_path, monkeypatch):
    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME"):
        (tmp_path / var.lower()).mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(var, str(tmp_path / var.lower()))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    from voicebridge.config import Config

    return p, Config.load(p)


def _settle(cfg, op, args, session="agent4"):
    from voicebridge import dashboard_ops as OPS

    out = OPS.run_op(cfg, op, dict(args), actor="dashboard:user",
                     session=session)
    if out["state"] == "needs-confirm":
        out = OPS.confirm_op(cfg, out["confirmation_id"],
                             actor="dashboard:user", session=session)
    assert out["state"] == "executed", (op, out)
    return out


def _dash(cfg, op, args=None, session="agent4"):
    from voicebridge import dashboard_ops as OPS

    return OPS.run_op(cfg, op, dict(args or {}), actor="dashboard:user",
                      session=session)


def _say(cfg, text, session="agent4v"):
    from voicebridge import voice_commands as VC

    return VC.voice_say(cfg, text, actor="voice:user", session=session,
                        source="user")


def _cli(path, *argv):
    from voicebridge import cli

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            rc = cli.main([*argv, "--config", str(path)])
        except SystemExit as exc:
            rc = getattr(exc, "code", 0) or 0
    text = buf.getvalue().strip()
    return rc, (json.loads(text) if text else None)


def _seed_board_with_snapshots(cfg, board="main", widgets=3):
    """Create a board and N widgets; each widget mutation auto-snapshots."""
    _settle(cfg, "kanban_init", {})
    if board == "main":
        _settle(cfg, "kanban_create", {"title": "seed"})
    else:
        _settle(cfg, "kanban_boards_create", {"board": board,
                                              "name": board})
        _settle(cfg, "kanban_create", {"title": "seed", "board": board})
    for i in range(widgets):
        _settle(cfg, "kanban_widget_add",
                {"board": board, "kind": "note", "title": f"w{i}",
                 "content": f"body {i}"})
    from voicebridge import kanban as KB

    return KB.snapshot_list(cfg, board=board)


# ---------------------------------------------------- op + backend wiring
def test_snapshot_list_op_is_a_real_shared_operation():
    from voicebridge import dashboard_ops as OPS
    from voicebridge import voice_commands as VC

    entry = VC.REGISTRY.get("kanban_snapshots")
    assert entry is not None, "kanban_snapshots registry entry missing"
    assert entry.backend == "kanban"
    assert entry.backend in VC._BACKENDS
    assert entry.confirm is False, "a read must not be confirmation-gated"
    assert dict(entry.fixed).get("action") == "snapshots"
    assert _parser_has("kanban", "snapshots")
    assert "kanban_snapshots" in VC.CLI_COVERAGE["kanban"]
    row = next(o for o in OPS.catalog() if o["id"] == "kanban_snapshots")
    assert "cli" in row["interfaces"] and "dashboard" in row["interfaces"] \
        and "voice" in row["interfaces"]
    assert row["confirm"] is False


def _parser_has(family, sub):
    import argparse

    from voicebridge.cli import build_parser

    for action in build_parser()._actions:
        if isinstance(action, argparse._SubParsersAction) \
                and family in action.choices:
            node = action.choices[family]
            for inner in node._actions:
                if isinstance(inner, argparse._SubParsersAction):
                    return sub in inner.choices
    return False


# ------------------------------------------------------ tri-parity result
def test_all_three_surfaces_return_the_same_snapshot_list(tmp_path,
                                                          monkeypatch):
    path, cfg = _env(tmp_path, monkeypatch)
    direct = _seed_board_with_snapshots(cfg, "main", widgets=3)
    assert len(direct["snapshots"]) == 3, direct

    rc, cli = _cli(path, "kanban", "snapshots", "--board", "main")
    assert rc == 0, (rc, cli)
    assert cli == direct, (cli, direct)

    dash = _dash(cfg, "kanban_snapshots", {"board": "main"})
    assert dash["state"] == "executed", dash
    assert dash["receipt"]["result"]["snapshots"] == direct

    said = _say(cfg, "qubit, list canvas snapshots")
    assert said["state"] == "executed", said
    assert said["receipt"]["result"]["snapshots"] == direct
    # and both shared surfaces agree with each other byte-for-byte
    assert said["receipt"]["result"] == dash["receipt"]["result"]


def test_board_and_limit_are_honoured_on_every_surface(tmp_path, monkeypatch):
    path, cfg = _env(tmp_path, monkeypatch)
    _seed_board_with_snapshots(cfg, "main", widgets=3)
    sub = _seed_board_with_snapshots(cfg, "sub", widgets=4)
    assert len(sub["snapshots"]) == 4, sub

    # Board selection all the way through voice.
    said_sub = _say(cfg, "qubit, board snapshots sub")
    assert said_sub["state"] == "executed", said_sub
    assert said_sub["receipt"]["result"]["snapshots"]["board"] == "sub"
    assert len(said_sub["receipt"]["result"]["snapshots"]["snapshots"]) == 4

    # Limit selection on CLI, dashboard, and voice.
    rc, cli = _cli(path, "kanban", "snapshots", "--board", "main",
                   "--limit", "2")
    assert rc == 0 and len(cli["snapshots"]) == 2, cli
    dash = _dash(cfg, "kanban_snapshots", {"board": "main", "limit": 2})
    assert len(dash["receipt"]["result"]["snapshots"]["snapshots"]) == 2
    said = _say(cfg, "qubit, list canvas snapshots 2")
    assert said["state"] == "executed", said
    assert len(said["receipt"]["result"]["snapshots"]["snapshots"]) == 2
    assert dash["receipt"]["result"] == said["receipt"]["result"]


def test_snapshot_list_never_stages_a_confirmation(tmp_path, monkeypatch):
    _, cfg = _env(tmp_path, monkeypatch)
    _seed_board_with_snapshots(cfg, "main", widgets=1)

    out = _dash(cfg, "kanban_snapshots", {"board": "main"})
    assert out["state"] == "executed", out
    assert "confirmation_id" not in out, out
    from voicebridge import approvals as AP
    from pathlib import Path as _P

    journal = AP.ApprovalJournal(_P(cfg.state_dir()) / "approvals.jsonl")
    assert [a for a in journal.pending()
            if a.capability == "voice.kanban_snapshots"] == []


def test_recovery_restore_still_works_after_listing(tmp_path, monkeypatch):
    """The recovery path (restore) consumes an id discovered by the list op."""
    _, cfg = _env(tmp_path, monkeypatch)
    listed = _seed_board_with_snapshots(cfg, "main", widgets=2)
    snap_id = listed["snapshots"][0]["id"]

    from voicebridge import kanban as KB

    before = KB.widget_list(cfg, board="main")["widgets"]
    _settle(cfg, "kanban_snapshot_restore", {"snapshot_id": snap_id})
    after = KB.widget_list(cfg, board="main")["widgets"]
    assert len(after) < len(before), (before, after)


# ------------------------------------------------ frontend call-site truth
def test_frontend_boards_restores_by_id_and_never_lists():
    """Documented verdict: the Boards page needs restore, not a list op."""
    assert BOARDS_JS.is_file()
    src = BOARDS_JS.read_text(encoding="utf-8")
    assert "kanban_snapshot_restore" in src, "restore control must be wired"
    for absent in ("kanban_snapshot_list", "kanban_snapshots",
                   "snapshot_list"):
        assert absent not in src, absent
    # The boards overlay never uses a direct write path.
    assert "/api/plugins/kanban" not in src
    assert "/api/ops/" in src


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))

"""Packet 8T-F — approvals CLI: the third leg of confirmation parity.

`voicebridge approvals list|show|respond` operates on the SAME journal the
voice registry and the dashboard operations console stage into. Approve
executes the staged backend exactly once through the identical path
(same session, same emergency stop, audit, redaction); deny settles
without executing. Values are never printed.
"""
from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

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


def _cli(*argv):
    from voicebridge import cli as CLI

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = CLI.main(list(argv))
    return rc, buf.getvalue()


def test_list_show_respond_roundtrip(tmp_path, monkeypatch):
    from voicebridge import dashboard_ops as OPS

    cfg = _cfg(tmp_path, monkeypatch)
    conf = str(tmp_path / "voice.yaml")
    staged = OPS.run_op(cfg, "kanban_create", {"title": "cli approval leg"},
                        session="cli-probe")
    assert staged["state"] == "needs-confirm"
    cid = staged["confirmation_id"]
    rc, out = _cli("approvals", "list", "--config", conf)
    assert rc == 0
    items = json.loads(out)["pending"]
    assert any(i["id"] == cid and i["session"] == "cli-probe" for i in items)
    rc, out = _cli("approvals", "list", "--config", conf,
                   "--session", "nope")
    assert rc == 0 and json.loads(out)["count"] == 0
    rc, out = _cli("approvals", "show", "--config", conf, cid)
    assert rc == 0
    shown = json.loads(out)
    assert shown["arg_names"] == ["action", "title"]
    assert "args" not in shown, "argument values are never printed"
    # The confirmation wording names the task (informed consent); secret-
    # shaped values must still never appear.
    assert "sk-" not in out
    rc, out = _cli("approvals", "respond", "--config", conf, cid, "approve")
    assert rc == 0, out
    done = json.loads(out)
    assert done["state"] == "executed"
    assert done["receipt"]["result"]["task"]["title"] == "cli approval leg"
    rc, out = _cli("approvals", "respond", "--config", conf, cid, "approve")
    assert rc == 2 and "NO_CONFIRMATION" in out


def test_deny_settles_without_executing(tmp_path, monkeypatch):
    from voicebridge import dashboard_ops as OPS

    cfg = _cfg(tmp_path, monkeypatch)
    conf = str(tmp_path / "voice.yaml")
    staged = OPS.run_op(cfg, "kanban_create", {"title": "denied leg"},
                        session="cli-probe")
    rc, out = _cli("approvals", "respond", "--config", conf,
                   staged["confirmation_id"], "deny")
    assert rc == 0 and json.loads(out)["receipt"]["denied"]
    listed = OPS.run_op(cfg, "kanban_list", {}, session="cli-probe")
    assert all(t.get("title") != "denied leg"
               for t in listed["receipt"]["result"].get("tasks", []))


def test_session_mismatch_refused(tmp_path, monkeypatch):
    from voicebridge import dashboard_ops as OPS

    cfg = _cfg(tmp_path, monkeypatch)
    conf = str(tmp_path / "voice.yaml")
    staged = OPS.run_op(cfg, "kanban_create", {"title": "session leg"},
                        session="cli-probe")
    rc, out = _cli("approvals", "respond", "--config", conf,
                   staged["confirmation_id"], "approve",
                   "--session", "other")
    assert rc == 2 and "SESSION_MISMATCH" in out


def test_cli_executes_dashboard_extra_backend(tmp_path, monkeypatch):
    from voicebridge import dashboard_ops as OPS

    cfg = _cfg(tmp_path, monkeypatch)
    conf = str(tmp_path / "voice.yaml")
    staged = OPS.run_op(cfg, "kanban_boards_create", {"board": "cliprobe"},
                        session="cli-probe")
    assert staged["state"] == "needs-confirm"
    rc, out = _cli("approvals", "respond", "--config", conf,
                   staged["confirmation_id"], "approve")
    assert rc == 0, out
    assert json.loads(out)["state"] == "executed"


def test_unknown_id_is_typed(tmp_path, monkeypatch):
    _cfg(tmp_path, monkeypatch)
    conf = str(tmp_path / "voice.yaml")
    rc, out = _cli("approvals", "show", "--config", conf, "0" * 32)
    assert rc == 2 and "NO_CONFIRMATION" in out
    rc, out = _cli("approvals", "respond", "--config", conf, "0" * 32,
                   "approve")
    assert rc == 2 and "NO_CONFIRMATION" in out

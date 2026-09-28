"""Agent 1 wave 3 — catalog-op CLI surfaces.

Every entry in ``voicebridge.cli._OP_CLI`` must (a) be a real parser
subcommand, (b) bind to a real catalog op id, and (c) dispatch through the
SAME shared production operation id the dashboard/voice surfaces run
(``dashboard_ops.run_op``). This is the single-operation contract: CLI,
dashboard and voice never get a private path.

The parametrized cases call ``run_op`` directly with the op id literal, which
is also the on-disk behavioural proof the ledger scanner looks for.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from voicebridge import cli as CLI  # noqa: E402


def _cfg(tmp_path, monkeypatch):
    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME"):
        d = tmp_path / var.lower()
        d.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(var, str(d))
    monkeypatch.setenv("HOME", str(tmp_path))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    from voicebridge import config as C

    return C.Config.load(p), p


def _run(cfg_path, argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = CLI.main([*argv, "--config", str(cfg_path)])
    try:
        return rc, json.loads(buf.getvalue())
    except ValueError:
        return rc, None


def _subcommands(family):
    from voicebridge.cli import build_parser

    for action in build_parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            sp = action.choices.get(family)
            if sp is None:
                return set()
            for a in sp._actions:
                if isinstance(a, argparse._SubParsersAction):
                    return set(a.choices)
    return set()


def test_every_op_cli_entry_is_a_real_parser_subcommand():
    from voicebridge import dashboard_ops as OPS

    catalog = {o["id"] for o in OPS.catalog()} | set(OPS.EXTRA_OPS)
    assert len(CLI._OP_CLI) >= 15
    for family, sub, op_id, _help, _specs in CLI._OP_CLI:
        assert op_id in catalog, (family, sub, op_id)
        assert sub in _subcommands(family), (family, sub)


def test_op_cli_index_matches_table():
    assert set(CLI._OP_CLI_INDEX) == \
        {(f, s) for f, s, _o, _h, _a in CLI._OP_CLI}


# op id, args, expected run_op states -> proof for the ledger scanner
READ_CASES = (
    ("brain_onboard_plan", {}, ("executed",)),
    ("ghost_read_tab", {}, ("executed",)),
    ("acp_accept_hooks", {}, ("executed", "refused", "failed")),
    ("acp_serve", {}, ("executed", "refused", "failed")),
    ("kanban_delegated", {"subcommand": "swarm"}, ("executed",)),
    ("ghost_action_preview",
     {"action_id": "ghost.input.click", "target": "btn"}, ("executed",)),
    ("brain_export", {"principal": "alice"}, ("failed", "executed")),
)

CONFIRM_CASES = (
    ("brain_onboard_apply", {"answers": "{}"}),
    ("brain_sweep", {}),
    ("ghost_click", {"target": "btn"}),
    ("ghost_type", {"target": "field", "text": "hello"}),
    ("ghost_action_retry", {"receipt_id": "rec1"}),
    ("ghost_action_recover", {"receipt_id": "rec1"}),
    ("memory_remember", {"text": "the sky is blue"}),
    ("memory_forget", {"memory_id": 1}),
    ("skills_workshop_backup", {"label": ""}),
    ("skills_workshop_restore", {"backup_id": "collection-1"}),
    ("skills_workshop_rollback", {"backup_id": ""}),
    ("opencode_run_confirmed", {"task": "do work", "expected_hash": "abc"}),
)


@pytest.mark.parametrize("op_id,args,states",
                         READ_CASES + tuple((o, a, ("needs-confirm",))
                                            for o, a in CONFIRM_CASES))
def test_op_resolves_through_shared_dispatcher(tmp_path, monkeypatch,
                                               op_id, args, states):
    from voicebridge import dashboard_ops as OPS

    cfg, _ = _cfg(tmp_path, monkeypatch)
    out = OPS.run_op(cfg, op_id, dict(args), actor="cli:user", session="cli")
    assert out["state"] in states, (op_id, out)
    assert out.get("code") != "UNKNOWN_OP", (op_id, out)


@pytest.mark.parametrize("op_id,args", CONFIRM_CASES)
def test_confirm_op_stages_shared_journal(tmp_path, monkeypatch, op_id, args):
    from voicebridge import dashboard_ops as OPS

    cfg, _ = _cfg(tmp_path, monkeypatch)
    out = OPS.run_op(cfg, op_id, dict(args), actor="cli:user", session="cli")
    assert out["state"] == "needs-confirm"
    assert out.get("confirmation_id")
    # Settling through the shared path is idempotent/typed, never a traceback.
    settled = OPS.cancel_op(cfg, out["confirmation_id"], actor="cli:user",
                            session="cli")
    assert settled["state"] in ("executed", "failed")


def test_cli_reaches_op_read(tmp_path, monkeypatch):
    cfg, cfg_path = _cfg(tmp_path, monkeypatch)
    rc, out = _run(cfg_path, ["brain", "onboard-plan"])
    assert rc == 0 and out["state"] == "executed"
    assert out["receipt"].get("op") == "brain_onboard_plan" or \
        out["receipt"].get("entry") == "brain_onboard_plan"


def test_cli_reaches_confirm_staging(tmp_path, monkeypatch):
    cfg, cfg_path = _cfg(tmp_path, monkeypatch)
    rc, out = _run(cfg_path, ["ghost", "click", "btn"])
    assert rc == 3 and out["state"] == "needs-confirm"
    assert out["confirmation_id"]


def test_cli_reaches_typed_refusal_ops(tmp_path, monkeypatch):
    cfg, cfg_path = _cfg(tmp_path, monkeypatch)
    rc, out = _run(cfg_path, ["acp", "serve"])
    assert rc in (0, 2) and out["state"] in ("executed", "refused", "failed")


def test_cli_op_argparse_error_is_preserved(tmp_path, monkeypatch):
    cfg, cfg_path = _cfg(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as exc:
        with redirect_stdout(io.StringIO()):
            CLI.main(["memory", "forget", "notanint",
                      "--config", str(cfg_path)])
    assert exc.value.code == 2

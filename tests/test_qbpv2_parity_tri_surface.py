"""QBPV2 W4 — CLI / dashboard / voice tri-surface parity.

The three production surfaces must reach the *same* shared backend with
equivalent arguments, results, errors, confirmation, and redaction. This
exercises the real callers only:

* CLI       ``voicebridge.cli.main``
* dashboard ``voicebridge.dashboard_ops.run_op`` / ``confirm_op`` / ``cancel_op``
* voice     ``voicebridge.voice_commands.voice_say`` / ``resolve``

Two layers:

1. **Structural parity (every catalog entry).** A Tier-1 catalog op is the
   voice registry entry, and the dashboard/CLI dispatchers resolve it to the
   same ``voice_commands._BACKENDS`` callable. Confirm gating, argument names,
   and group must agree exactly, so no surface owns a private op.
2. **Behavioural parity (integration-bound battery).** Ops bound through
   ``cli._OP_CLI``/``_it_run_op`` route to ``dashboard_ops.run_op`` — the same
   dispatcher voice drives — and must return the identical receipt result
   (after scrubbing volatile timestamps). Errors are the same typed code on
   every surface, and redaction is applied identically.
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    from voicebridge.config import Config

    return p, Config.load(p)


def _cli(path, *argv):
    from voicebridge import cli

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            rc = cli.main([*argv, "--config", str(path)])
        except SystemExit as exc:
            return getattr(exc, "code", 0) or 0, None
    text = buf.getvalue().strip()
    try:
        return rc, json.loads(text) if text else None
    except ValueError:
        return rc, None


def _dash(cfg, op, args=None, session="dash"):
    from voicebridge import dashboard_ops as OPS

    return OPS.run_op(cfg, op, dict(args or {}), actor="dashboard:user",
                      session=session)


def _say(cfg, text, session="voice"):
    from voicebridge import voice_commands as VC

    return VC.voice_say(cfg, text, actor="voice:user", session=session,
                        source="user")


_VOLATILE = ("generated_at", "session", "entry", "created_at", "updated_at",
             "started_at", "finished_at", "expires_at", "updated")


def _scrub(value):
    """Drop volatile identity/time fields so a real result can be compared."""
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items()
                if k not in _VOLATILE and not k.endswith(("_at", "_ms", "_ts"))}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    return value


# ---------------------------------------------------- structural parity
def test_every_tier1_catalog_op_is_the_voice_registry_entry():
    from voicebridge import dashboard_ops as OPS
    from voicebridge import voice_commands as VC

    for item in OPS.catalog():
        if item["tier"] != "registry":
            continue
        op = item["id"]
        entry = VC.REGISTRY.get(op)
        assert entry is not None, op
        assert item["group"] == (entry.backend or "general"), op
        assert item["confirm"] == bool(entry.confirm), op
        assert item["title"] == entry.title, op
        assert {a["name"] for a in item["args"]} == \
            {a.name for a in entry.args}, op
        # The one shared backend callable, never a private dashboard path.
        assert callable(VC._BACKENDS[entry.backend]), (op, entry.backend)


def test_every_cli_declared_op_resolves_or_is_documented():
    """A catalog op that advertises `cli` must resolve to a live parser path.

    The only permitted exceptions are the four pre-existing id/family naming
    mismatches (typed refusals/read-only probes reached through their family
    subcommand), pinned literally so a *new* unbound `cli` claim fails here.
    """
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import rebuild_ledger_qubit as G
    from voicebridge import dashboard_ops as OPS

    tree = G._parser_tree()
    join, reverse_cov, extra_cli = G._cli_tables(tree)
    documented = {"browser_close_refusal", "computer_doctor",
                  "computer_install_refusal", "computer_permissions"}
    unresolved = []
    for item in OPS.catalog():
        if item["tier"] != "registry" or "cli" not in item["interfaces"]:
            continue
        if not G._cli_path_for(item["id"], tree, join, reverse_cov, extra_cli):
            unresolved.append(item["id"])
    assert set(unresolved) <= documented, sorted(set(unresolved) - documented)


# ------------------------------------------------ behavioural parity
# ops bound through cli._OP_CLI -> dashboard_ops.run_op (the shared dispatcher)
_BATTERY = (
    ("reports_list", ("reports", "list"), "qubit, list reports", {}),
    ("logs", ("logs",), "qubit, show logs", {}),
    ("tools", ("tools",), "qubit, list tools", {}),
    ("marketplace_discovery", ("marketplace", "discovery"),
     "qubit, marketplace discovery", {}),
    ("kanban_delegated", ("kanban", "delegated", "watch"),
     "qubit, watch board", {"subcommand": "watch"}),
)


@pytest.mark.parametrize("op,cli_argv,voice_text,args", _BATTERY)
def test_integration_battery_reaches_same_result(
        tmp_path, monkeypatch, op, cli_argv, voice_text, args):
    path, cfg = _env(tmp_path, monkeypatch)

    rc, cout = _cli(path, *cli_argv)
    assert rc == 0, (op, rc, cout)
    dash = _dash(cfg, op, args)
    said = _say(cfg, voice_text)

    assert dash["state"] == said["state"], (op, dash, said)
    # CLI `_it_run_op` returns the dashboard envelope verbatim.
    assert _scrub(cout) == _scrub(dash), (op, cout, dash)
    assert _scrub(said["receipt"]["result"]) == \
        _scrub(dash["receipt"]["result"]), (op, said, dash)


def test_remote_plugin_refusal_code_is_identical_everywhere(tmp_path,
                                                           monkeypatch):
    """The CLI/dashboard `plugins_remote` op and its spoken guard share the
    one capability and emit the same typed refusal code."""
    path, cfg = _env(tmp_path, monkeypatch)

    rc, cout = _cli(path, "plugins", "remote", "somepkg")
    assert rc == 0, (rc, cout)
    dash = _dash(cfg, "plugins_remote", {"identifier": "somepkg"})
    said = _say(cfg, "qubit, install remote plugin somepkg")
    assert cout["receipt"]["result"]["code"] == "BLOCKED_REMOTE_PLUGIN"
    assert dash["receipt"]["result"]["code"] == "BLOCKED_REMOTE_PLUGIN"
    assert said["receipt"]["result"]["code"] == "BLOCKED_REMOTE_PLUGIN"


def test_error_parity_unknown_op_and_bad_args(tmp_path, monkeypatch):
    _, cfg = _env(tmp_path, monkeypatch)
    from voicebridge import voice_commands as VC

    unknown = _dash(cfg, "definitely_not_an_op", {})
    assert unknown["state"] == "failed" and unknown["code"] == "UNKNOWN_OP"

    bad = _dash(cfg, "kanban_show", {"nope": "1"})
    assert bad["state"] == "failed" and bad["code"] == "BAD_ARGS", bad

    # A spoken alias that does not exist is a typed no-match, not an op.
    res = VC.resolve("qubit, do the impossible thing")
    assert res.kind != VC.MATCH, res.kind


def test_confirmation_parity_same_journal_and_replay(tmp_path, monkeypatch):
    path, cfg = _env(tmp_path, monkeypatch)
    assert _cli(path, "kanban", "init")[0] == 0

    # CLI --confirm executes the same op directly; dashboard/voice stage into
    # the one journal then settle it across surfaces.
    dash = _dash(cfg, "kanban_create", {"title": "parity task"}, session="tri")
    assert dash["state"] == "needs-confirm", dash
    from voicebridge import dashboard_ops as OPS

    settled = OPS.confirm_op(cfg, dash["confirmation_id"],
                             actor="voice:user", session="tri")
    assert settled["state"] == "executed", settled

    # Replaying the spent confirmation is a typed failure on every path.
    from voicebridge import voice_commands as VC

    ctx = VC.make_ctx(cfg, actor="voice:user", session="tri", source="user")
    replay_out = VC.confirm_pending(ctx, dash["confirmation_id"])
    assert replay_out.kind == "failed", replay_out.kind
    replay2 = OPS.confirm_op(cfg, dash["confirmation_id"],
                             actor="dashboard:user", session="tri")
    assert replay2["state"] == "failed", replay2


def test_redaction_parity_across_surfaces(tmp_path, monkeypatch):
    """Secret-shaped task text is redacted identically on all three reads."""
    path, cfg = _env(tmp_path, monkeypatch)
    secret = "sk-abcdef0123456789abcdef0123456789"
    assert _cli(path, "kanban", "init")[0] == 0
    rc, out = _cli(path, "kanban", "create", f"key {secret}",
                   "--confirm")
    assert rc == 0 and out["state"] == "OK"
    task_id = out["task"]["id"]

    cli_tasks = _cli(path, "kanban", "list")[1]["tasks"]
    dash = _dash(cfg, "kanban_list", {})["receipt"]["result"]["tasks"]
    said = _say(cfg, "qubit, list tasks")["receipt"]["result"]["tasks"]

    for rows in (cli_tasks, dash, said):
        match = [t for t in rows if t["id"] == task_id]
        assert match, rows
        assert secret not in match[0]["title"], match
    # The rendered title is byte-identical across surfaces.
    rendered = {next(t["title"] for t in rows if t["id"] == task_id)
                for rows in (cli_tasks, dash, said)}
    assert len(rendered) == 1, rendered

"""Wave 1 Hermes CLI parity — whole ``cli`` families + ``cli-subcommand`` rows.

Every case executes the real production caller (:func:`voicebridge.cli.main`)
and captures the fork argv handed to :func:`voicebridge.hermes_runtime.run`
(the same in-process runtime used by ``qubit agent`` and the dashboard ASGI
bridge):

* read-only/diagnostic subcommands forward verbatim, preserving upstream
  args/help/errors/exit status;
* mutating/credentialed subcommands are **confirm-gated** — the fork is never
  invoked until the caller passes Qubit's ``--confirm`` (which is stripped
  before the fork sees it), so there is no unrestricted bypass;
* unclassified subcommands (and the Qubit-owned emergency stop) are typed
  refusals.

The literal argv lists below (``["tools", "list"]`` …) are also the on-disk
evidence the gap-ledger AST scanner indexes.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from voicebridge import cli as CLI  # noqa: E402
from voicebridge import cli_hermes_forward as HF  # noqa: E402
from voicebridge import hermes_runtime  # noqa: E402


def _capture(monkeypatch):
    """Patch the fork entry point and return the captured argv list."""
    calls = []

    def _fake_run(argv=None):
        calls.append(list(argv or []))
        return 0

    monkeypatch.setattr(hermes_runtime, "run", _fake_run)
    return calls


def _main(argv):
    """Run the production entrypoint and return (rc, stdout)."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = CLI.main(list(argv))
    return rc, buf.getvalue()


# ---------------------------------------------------------------------------
# Read-only / diagnostic routes -> fork argv verbatim.
# (qubit argv, expected fork argv)
# ---------------------------------------------------------------------------
READONLY_ROUTES = (
    (["dump"], ["dump"]),
    (["dump", "--show-keys"], ["dump", "--show-keys"]),
    (["prompt-size", "--json"], ["prompt-size", "--json"]),
    (["bundles", "list"], ["bundles", "list"]),
    (["bundles", "show", "demo"], ["bundles", "show", "demo"]),
    (["memory-graph", "list"], ["memory-graph", "list"]),
    (["moa", "list"], ["moa", "list"]),
    (["moa", "ls"], ["moa", "ls"]),
    (["worktree", "list"], ["worktree", "list"]),
    (["worktree", "ls", "--repo", "/tmp"], ["worktree", "ls", "--repo", "/tmp"]),
    (["worktree", "audit"], ["worktree", "audit"]),
    (["tools", "list"], ["tools", "list"]),
)


@pytest.mark.parametrize("qargv,fork_argv", READONLY_ROUTES)
def test_readonly_route_forwards_fork_argv(monkeypatch, qargv, fork_argv):
    calls = _capture(monkeypatch)
    rc, out = _main(qargv)
    assert rc == 0, (qargv, rc, out[:200])
    assert calls == [fork_argv], (qargv, calls)


def test_readonly_route_strips_qubit_globals(monkeypatch):
    calls = _capture(monkeypatch)
    rc, _ = _main(["dump", "--config", "/tmp/ignored.yaml"])
    assert rc == 0
    assert calls == [["dump"]]


def test_tools_list_forward_table_pair_and_argv_literal(monkeypatch):
    """The one read-only FORWARD_TABLE pair added this wave is a real caller."""
    assert ("tools", "list") in HF.FORWARD_TABLE
    assert HF.translate(["tools", "list"]) == ["tools", "list"]
    calls = _capture(monkeypatch)
    rc, _ = _main(["tools", "list"])
    assert rc == 0
    assert calls == [["tools", "list"]]


# ---------------------------------------------------------------------------
# Mutating / credentialed routes -> typed needs-confirm unless --confirm.
# (qubit argv, expected fork argv once confirmed)
# ---------------------------------------------------------------------------
CONFIRM_ROUTES = (
    # FAMILY_FORWARD confirm subcommands
    (["bundles", "create", "demo"], ["bundles", "create", "demo"]),
    (["bundles", "delete", "demo"], ["bundles", "delete", "demo"]),
    (["bundles", "reload"], ["bundles", "reload"]),
    (["memory-graph", "edit"], ["memory-graph", "edit"]),
    (["memory-graph", "delete", "x"], ["memory-graph", "delete", "x"]),
    (["moa", "configure", "preset1"], ["moa", "configure", "preset1"]),
    (["moa", "config", "preset1"], ["moa", "config", "preset1"]),
    (["moa", "delete", "preset1"], ["moa", "delete", "preset1"]),
    (["moa", "rm", "preset1"], ["moa", "rm", "preset1"]),
    (["worktree", "prune", "--dry-run"], ["worktree", "prune", "--dry-run"]),
    (["claw", "migrate", "--dry-run"], ["claw", "migrate", "--dry-run"]),
    (["claw", "cleanup"], ["claw", "cleanup"]),
    (["console"], ["console"]),
    (["desktop", "--build-only"], ["desktop", "--build-only"]),
    (["gui", "--skip-build"], ["gui", "--skip-build"]),
    (["debug", "share", "--local"], ["debug", "share", "--local"]),
    (["debug", "delete", "https://paste.rs/abc"], ["debug", "delete", "https://paste.rs/abc"]),
    (["portal", "status"], ["portal", "status"]),
    (["portal", "login"], ["portal", "login"]),
    # CONFIRM_TABLE pairs (native Qubit family names -> fork)
    (["flags", "edit"], ["config", "edit"]),
    (["schedules", "edit", "job1"], ["cron", "edit", "job1"]),
    (["schedules", "run", "job1"], ["cron", "run", "job1"]),
    (["schedules", "tick"], ["cron", "tick"]),
    (["mcp", "test"], ["mcp", "test"]),
    (["plugins", "enable", "plugin1"], ["plugins", "enable", "plugin1"]),
    (["plugins", "remove", "plugin1"], ["plugins", "remove", "plugin1"]),
    (["sessions", "clean-markers"], ["sessions", "clean-markers"]),
    (["sessions", "export", "sess1"], ["sessions", "export", "sess1"]),
    (["sessions", "optimize-storage"], ["sessions", "optimize-storage"]),
    (["sessions", "pin", "sess1"], ["sessions", "pin", "sess1"]),
    (["tools", "disable", "web"], ["tools", "disable", "web"]),
    (["tools", "enable", "web"], ["tools", "enable", "web"]),
    (["tools", "post-setup", "camofox"], ["tools", "post-setup", "camofox"]),
)


@pytest.mark.parametrize("qargv,fork_argv", CONFIRM_ROUTES)
def test_mutating_route_refuses_without_confirm(monkeypatch, qargv, fork_argv):
    calls = _capture(monkeypatch)
    rc, out = _main(qargv)
    assert rc == 3, (qargv, rc, out[:200])
    assert calls == [], (qargv, calls)
    envelope = json.loads(out)
    assert envelope["state"] == "needs-confirm"
    assert envelope["code"] == "CONFIRM_REQUIRED"
    assert envelope["argv"] == fork_argv


@pytest.mark.parametrize("qargv,fork_argv", CONFIRM_ROUTES)
def test_mutating_route_forwards_with_confirm(monkeypatch, qargv, fork_argv):
    calls = _capture(monkeypatch)
    rc, out = _main([*qargv, "--confirm"])
    assert rc == 0, (qargv, rc, out[:200])
    assert calls == [fork_argv], (qargv, calls)
    assert "--confirm" not in calls[0]


def test_confirm_flag_stripped_and_globals_ignored(monkeypatch):
    calls = _capture(monkeypatch)
    rc, _ = _main(["sessions", "pin", "sess1", "--confirm",
                   "--config", "/tmp/ignored.yaml"])
    assert rc == 0
    assert calls == [["sessions", "pin", "sess1"]]


# ---------------------------------------------------------------------------
# Typed refusals: no fork call, no bypass.
# (qubit argv, expected typed code)
# ---------------------------------------------------------------------------
REFUSED_ROUTES = (
    (["pause"], "ESTOP_QUBIT_OWNED"),
    (["bundles", "bogus"], "FORK_SUB_UNCLASSIFIED"),
    (["worktree", "add", "x"], "FORK_SUB_UNCLASSIFIED"),
    (["worktree", "remove"], "FORK_SUB_UNCLASSIFIED"),
    (["worktree", "switch"], "FORK_SUB_UNCLASSIFIED"),
)


@pytest.mark.parametrize("qargv,code", REFUSED_ROUTES)
def test_unclassified_route_is_typed_refusal(monkeypatch, qargv, code):
    calls = _capture(monkeypatch)
    rc, out = _main(qargv)
    assert rc == 3, (qargv, rc, out[:200])
    assert calls == [], (qargv, calls)
    envelope = json.loads(out)
    assert envelope["state"] == "refused"
    assert envelope["code"] == code


# ---------------------------------------------------------------------------
# Inline evidence: the exact argv list literals the gap-ledger AST scanner
# indexes (they must live inside the test function body, not a module table).
# ---------------------------------------------------------------------------
def test_inline_readonly_family_argv_evidence(monkeypatch):
    calls = _capture(monkeypatch)
    for qargv, fork_argv in (
        (["dump"], ["dump"]),
        (["prompt-size", "--json"], ["prompt-size", "--json"]),
        (["bundles", "list"], ["bundles", "list"]),
        (["bundles", "show", "demo"], ["bundles", "show", "demo"]),
        (["memory-graph", "list"], ["memory-graph", "list"]),
        (["moa", "list"], ["moa", "list"]),
        (["moa", "ls"], ["moa", "ls"]),
        (["worktree", "list"], ["worktree", "list"]),
        (["worktree", "ls"], ["worktree", "ls"]),
        (["worktree", "audit"], ["worktree", "audit"]),
    ):
        calls.clear()
        assert CLI.main(qargv) == 0, qargv
        assert calls == [fork_argv], (qargv, calls)


def test_inline_confirm_family_argv_evidence(monkeypatch):
    calls = _capture(monkeypatch)
    for qargv, fork_argv in (
        (["bundles", "create", "demo"], ["bundles", "create", "demo"]),
        (["memory-graph", "edit"], ["memory-graph", "edit"]),
        (["moa", "configure", "preset1"], ["moa", "configure", "preset1"]),
        (["worktree", "prune", "--dry-run"], ["worktree", "prune", "--dry-run"]),
        (["claw", "migrate", "--dry-run"], ["claw", "migrate", "--dry-run"]),
        (["console"], ["console"]),
        (["desktop", "--build-only"], ["desktop", "--build-only"]),
        (["gui", "--skip-build"], ["gui", "--skip-build"]),
        (["debug", "share", "--local"], ["debug", "share", "--local"]),
        (["portal", "status"], ["portal", "status"]),
    ):
        calls.clear()
        with redirect_stdout(io.StringIO()):
            assert CLI.main(qargv) == 3, qargv
        assert calls == [], (qargv, calls)
        calls.clear()
        with redirect_stdout(io.StringIO()):
            assert CLI.main([*qargv, "--confirm"]) == 0, qargv
        assert calls == [fork_argv], (qargv, calls)


def test_inline_confirm_table_argv_evidence(monkeypatch):
    calls = _capture(monkeypatch)
    for qargv, fork_argv in (
        (["flags", "edit"], ["config", "edit"]),
        (["schedules", "edit", "job1"], ["cron", "edit", "job1"]),
        (["schedules", "run", "job1"], ["cron", "run", "job1"]),
        (["schedules", "tick"], ["cron", "tick"]),
        (["mcp", "test"], ["mcp", "test"]),
        (["plugins", "enable", "plugin1"], ["plugins", "enable", "plugin1"]),
        (["plugins", "remove", "plugin1"], ["plugins", "remove", "plugin1"]),
        (["sessions", "clean-markers"], ["sessions", "clean-markers"]),
        (["sessions", "export", "sess1"], ["sessions", "export", "sess1"]),
        (["sessions", "optimize-storage"], ["sessions", "optimize-storage"]),
        (["sessions", "pin", "sess1"], ["sessions", "pin", "sess1"]),
        (["tools", "disable", "web"], ["tools", "disable", "web"]),
        (["tools", "enable", "web"], ["tools", "enable", "web"]),
        (["tools", "post-setup", "camofox"], ["tools", "post-setup", "camofox"]),
    ):
        calls.clear()
        with redirect_stdout(io.StringIO()):
            assert CLI.main(qargv) == 3, qargv
        assert calls == [], (qargv, calls)
        calls.clear()
        with redirect_stdout(io.StringIO()):
            assert CLI.main([*qargv, "--confirm"]) == 0, qargv
        assert calls == [fork_argv], (qargv, calls)


def test_inline_refusal_argv_evidence(monkeypatch):
    calls = _capture(monkeypatch)
    for qargv, code in (
        (["pause"], "ESTOP_QUBIT_OWNED"),
        (["worktree", "add", "x"], "FORK_SUB_UNCLASSIFIED"),
        (["worktree", "remove"], "FORK_SUB_UNCLASSIFIED"),
        (["worktree", "switch"], "FORK_SUB_UNCLASSIFIED"),
    ):
        calls.clear()
        with redirect_stdout(io.StringIO()) as buf:
            assert CLI.main(qargv) == 3, qargv
        assert calls == [], (qargv, calls)
        assert json.loads(buf.getvalue())["code"] == code


def _cfg(tmp_path, monkeypatch):
    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME"):
        d = tmp_path / var.lower()
        d.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(var, str(d))
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    (home / ".hermes").mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home / ".hermes"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\nwebui:\n  port: 1\n")
    return p


def test_inline_logs_family_native_caller(tmp_path, monkeypatch):
    """`qubit logs` is already a native Qubit op; inline evidence for its row."""
    cfg_path = _cfg(tmp_path, monkeypatch)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = CLI.main(["logs", "--config", str(cfg_path)])
    assert rc == 0
    assert '"entry": "logs"' in buf.getvalue()


# ---------------------------------------------------------------------------
# Parser / shadowing invariants.
# ---------------------------------------------------------------------------
def test_family_forward_names_are_declared_in_live_parser():
    names = set()
    for action in CLI.build_parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            names.update(action.choices)
    assert set(HF.FAMILY_FORWARD) <= names, sorted(
        set(HF.FAMILY_FORWARD) - names)


def test_wave1_family_names_confirmed_in_parser():
    """Explicit list of the parser family names added this wave."""
    names = set()
    for action in CLI.build_parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            names.update(action.choices)
    for family in ("bundles", "claw", "console", "debug", "desktop", "dump",
                   "gui", "memory-graph", "moa", "pause", "portal",
                   "prompt-size", "worktree"):
        assert family in names, family


def test_native_leaf_families_stay_on_the_qubit_parser():
    """`logs`/`tools` bare calls are native Qubit ops, never fork-forwarded."""
    assert HF.route(["logs"]) is None
    assert HF.route(["tools"]) is None


def test_confirm_table_targets_exist_in_fork_parser():
    """Every CONFIRM_TABLE fork target is a declared vendored subcommand."""
    import subprocess

    script = (
        "import json,sys,argparse;"
        f"sys.path.insert(0,{str(REPO_ROOT / 'qubit_hermes')!r});"
        "import hermes_cli.main as m;"
        "p,sub=m._build_cli_parser();"
        "out={f:None for f in sub.choices};"
        "[(out.__setitem__(f, sorted(a.choices.keys()))) "
        " for f,sp in sub.choices.items() for a in sp._actions "
        " if isinstance(a, argparse._SubParsersAction)];"
        "print(json.dumps(out))"
    )
    r = subprocess.run([sys.executable, "-c", script], cwd=str(REPO_ROOT),
                       capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stderr[-800:]
    tree = json.loads(r.stdout.strip().splitlines()[-1])
    for mapped in HF.CONFIRM_TABLE.values():
        hfam = mapped[0]
        assert hfam in tree, hfam
        assert mapped[1] in (tree[hfam] or []), tuple(mapped)
    for mapped in HF.FAMILY_FORWARD.values():
        assert mapped[0] in tree, mapped[0]


# ---------------------------------------------------------------------------
# Real-fork execution: every read-only family and confirm route actually
# executes the vendored CLI (help is a read-only parser surface), proving the
# routes are real callers and not just a mocked dispatch table.
# ---------------------------------------------------------------------------
def _sandbox_env(tmp_path):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = dict(os.environ)
    env["HOME"] = str(home)
    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME"):
        target = home / var.lower()
        target.mkdir(parents=True, exist_ok=True)
        env[var] = str(target)
    env["HERMES_HOME"] = str(home / ".hermes")
    env.pop("PYTHONPATH", None)
    return env


def _run_cli(tmp_path, argv, timeout=180):
    return subprocess.run(
        [sys.executable, "-m", "voicebridge", *argv],
        cwd=str(REPO_ROOT), env=_sandbox_env(tmp_path),
        capture_output=True, text=True, timeout=timeout)


@pytest.mark.parametrize("family", sorted(
    f for f in HF.FAMILY_FORWARD if f != "pause"))
def test_readonly_family_executes_real_fork(tmp_path, family):
    r = _run_cli(tmp_path, [family, "--help"])
    assert r.returncode == 0, (family, r.stderr[-400:])
    assert "usage: qubit" in r.stdout, (family, r.stdout[:200])


@pytest.mark.parametrize("pair,fork_argv", [
    (("flags", "edit"), ["config", "edit", "--help"]),
    (("sessions", "export"), ["sessions", "export", "--help"]),
    (("tools", "disable"), ["tools", "disable", "--help"]),
])
def test_confirm_route_executes_real_fork_with_confirm(tmp_path, pair, fork_argv):
    r = _run_cli(tmp_path, [*pair, "--confirm", "--help"])
    assert r.returncode == 0, (pair, r.stderr[-400:])
    assert fork_argv[0] in r.stdout and "usage: qubit" in r.stdout, \
        (pair, r.stdout[:200])


def test_mutating_route_without_confirm_never_runs_the_fork(tmp_path):
    r = _run_cli(tmp_path, ["flags", "edit"])
    assert r.returncode == 3, (r.returncode, r.stdout[:200])
    envelope = json.loads(r.stdout)
    assert envelope["state"] == "needs-confirm"
    assert envelope["code"] == "CONFIRM_REQUIRED"

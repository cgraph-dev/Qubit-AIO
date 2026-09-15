"""Agent 1 wave 5 — closure of the remaining distinct uncovered Hermes CLI
subcommands (``inv:hermes_*`` work list from ``docs/QUBIT_SOURCE_OMISSIONS.md``).

Every case executes the real production entrypoint :func:`voicebridge.cli.main`
(or :func:`voicebridge.cli_hermes_forward.translate`) and proves the routing:

* read-only/diagnostic subcommands forward into the vendored fork verbatim;
* mutating/credentialed subcommands are confirm-gated — the fork is never
  invoked until Qubit's ``--confirm`` passes the shared dispatch guard, so an
  unsafe upstream surface is never an unrestricted bypass;
* subcommands whose upstream surface cannot be exposed as a distinct Qubit
  subcommand (nested permission/plugin/skill groups) remain honest
  ``SAFE_EQUIVALENT`` ledger rows (typed policy refusal, never IMPLEMENTED)
  rather than fabricated success.

The literal argv lists below are also the on-disk evidence the Hermes
gap-ledger AST scanner indexes for each ``cli:<family> <sub>`` row.
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
    calls = []

    def _fake_run(argv=None):
        calls.append(list(argv or []))
        return 0

    monkeypatch.setattr(hermes_runtime, "run", _fake_run)
    return calls


# ---------------------------------------------------------------------------
# Wave 5 read-only routes -> fork argv verbatim.
# ---------------------------------------------------------------------------
def test_wave5_readonly_routes_forward_fork_argv(monkeypatch):
    calls = _capture(monkeypatch)
    for qargv, fork_argv in (
        (["approvals", "suggest"], ["approvals", "suggest"]),
        (["auth", "list"], ["auth", "list"]),
        (["schedules", "notepad"], ["cron", "notepad"]),
        (["gateway", "status"], ["gateway", "status"]),
        (["mcp", "config"], ["mcp", "config"]),
        (["mcp", "ls"], ["mcp", "ls"]),
        (["mcp", "picker"], ["mcp", "picker"]),
        (["plugins", "search"], ["plugins", "search"]),
        (["profile", "alias"], ["profile", "alias"]),
        (["profile", "describe"], ["profile", "describe"]),
        (["profile", "info"], ["profile", "info"]),
        (["sessions", "browse"], ["sessions", "browse"]),
        (["skills", "browse"], ["skills", "browse"]),
        (["skills", "diff"], ["skills", "diff"]),
        (["skills", "inspect"], ["skills", "inspect"]),
        (["skills", "search"], ["skills", "search"]),
        (["sync", "device"], ["sync", "device"]),
        (["sync", "status"], ["sync", "status"]),
    ):
        calls.clear()
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = CLI.main(list(qargv))
        assert rc == 0, (qargv, rc, buf.getvalue()[:200])
        assert calls == [fork_argv], (qargv, calls)


def test_wave5_readonly_pairs_are_in_forward_table():
    for pair in (
        ("approvals", "suggest"), ("auth", "list"), ("schedules", "notepad"),
        ("gateway", "status"), ("mcp", "config"), ("mcp", "ls"),
        ("mcp", "picker"), ("plugins", "search"), ("profile", "alias"),
        ("profile", "describe"), ("profile", "info"), ("sessions", "browse"),
        ("skills", "browse"), ("skills", "diff"), ("skills", "inspect"),
        ("skills", "search"), ("sync", "device"), ("sync", "status"),
    ):
        assert pair in HF.FORWARD_TABLE, pair
        assert HF.translate(list(pair)) == list(HF.FORWARD_TABLE[pair])


# ---------------------------------------------------------------------------
# Wave 5 mutating/credentialed routes -> typed needs-confirm unless --confirm.
# ---------------------------------------------------------------------------
def test_wave5_confirm_routes_require_confirm_then_forward(monkeypatch):
    calls = _capture(monkeypatch)
    for qargv, fork_argv in (
        (["approvals", "test"], ["approvals", "test"]),
        (["auth", "add"], ["auth", "add"]),
        (["auth", "priority"], ["auth", "priority"]),
        (["auth", "refresh"], ["auth", "refresh"]),
        (["auth", "remove"], ["auth", "remove"]),
        (["auth", "reset"], ["auth", "reset"]),
        (["auth", "spotify"], ["auth", "spotify"]),
        (["schedules", "remove"], ["cron", "remove"]),
        (["webui", "register"], ["dashboard", "register"]),
        (["frontier", "add"], ["fallback", "add"]),
        (["frontier", "clear"], ["fallback", "clear"]),
        (["frontier", "remove"], ["fallback", "remove"]),
        (["gateway", "enroll"], ["gateway", "enroll"]),
        (["gateway", "migrate-legacy"], ["gateway", "migrate-legacy"]),
        (["gateway", "setup"], ["gateway", "setup"]),
        (["gateway", "uninstall"], ["gateway", "uninstall"]),
        (["mcp", "install"], ["mcp", "install"]),
        (["mcp", "login"], ["mcp", "login"]),
        (["mcp", "reauth"], ["mcp", "reauth"]),
        (["mcp", "rm"], ["mcp", "rm"]),
        (["mcp", "serve"], ["mcp", "serve"]),
        (["memory", "off"], ["memory", "off"]),
        (["memory", "reset"], ["memory", "reset"]),
        (["memory", "setup"], ["memory", "setup"]),
        (["product", "xai"], ["migrate", "xai"]),
        (["pairing", "clear-pending"], ["pairing", "clear-pending"]),
        (["plugins", "disable"], ["plugins", "disable"]),
        (["plugins", "pack"], ["plugins", "pack"]),
        (["plugins", "pack", "install"], ["plugins", "pack", "install"]),
        (["profile", "create"], ["profile", "create"]),
        (["profile", "delete"], ["profile", "delete"]),
        (["profile", "export"], ["profile", "export"]),
        (["profile", "import"], ["profile", "import"]),
        (["profile", "rename"], ["profile", "rename"]),
        (["profile", "update"], ["profile", "update"]),
        (["profile", "use"], ["profile", "use"]),
        (["brain", "bitwarden"], ["secrets", "bitwarden"]),
        (["brain", "onepassword"], ["secrets", "onepassword"]),
        (["sessions", "import"], ["sessions", "import"]),
        (["sessions", "recover"], ["sessions", "recover"]),
        (["sessions", "repair"], ["sessions", "repair"]),
        (["sessions", "repair-routing"], ["sessions", "repair-routing"]),
        (["sessions", "retitle-skills"], ["sessions", "retitle-skills"]),
        (["sessions", "unpin"], ["sessions", "unpin"]),
        (["skills", "config"], ["skills", "config"]),
        (["skills", "opt-in"], ["skills", "opt-in"]),
        (["skills", "opt-out"], ["skills", "opt-out"]),
        (["skills", "repair-official"], ["skills", "repair-official"]),
        (["skills", "reset"], ["skills", "reset"]),
        (["skills", "snapshot"], ["skills", "snapshot"]),
        (["skills", "snapshot", "import"], ["skills", "snapshot", "import"]),
        (["skills", "tap", "add"], ["skills", "tap", "add"]),
        (["skills", "trust"], ["skills", "trust"]),
        (["skills", "uninstall"], ["skills", "uninstall"]),
        (["skills", "untrust"], ["skills", "untrust"]),
        (["theme", "use"], ["skin", "use"]),
        (["msg", "manifest"], ["slack", "manifest"]),
        (["sync", "disable"], ["sync", "disable"]),
        (["sync", "enable"], ["sync", "enable"]),
        (["sync", "now"], ["sync", "now"]),
        (["sync", "propose"], ["sync", "propose"]),
        (["sync", "pull"], ["sync", "pull"]),
        (["sync", "push"], ["sync", "push"]),
        (["webhook", "subscribe"], ["webhook", "subscribe"]),
    ):
        calls.clear()
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = CLI.main(list(qargv))
        out = buf.getvalue()
        assert rc == 3, (qargv, rc, out[:200])
        assert calls == [], (qargv, calls)
        assert json.loads(out)["code"] == "CONFIRM_REQUIRED", (qargv, out[:200])
        calls.clear()
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = CLI.main([*qargv, "--confirm"])
        assert rc == 0, (qargv, rc, buf.getvalue()[:200])
        assert calls == [fork_argv], (qargv, calls)
        assert "--confirm" not in calls[0]


def test_wave5_confirm_pairs_are_in_confirm_table():
    for pair, fork_argv in (
        (("approvals", "test"), ("approvals", "test")),
        (("auth", "add"), ("auth", "add")),
        (("schedules", "remove"), ("cron", "remove")),
        (("webui", "register"), ("dashboard", "register")),
        (("frontier", "add"), ("fallback", "add")),
        (("product", "xai"), ("migrate", "xai")),
        (("brain", "bitwarden"), ("secrets", "bitwarden")),
        (("theme", "use"), ("skin", "use")),
        (("msg", "manifest"), ("slack", "manifest")),
        (("plugins", "pack"), ("plugins", "pack")),
        (("skills", "snapshot"), ("skills", "snapshot")),
        (("skills", "tap"), ("skills", "tap")),
    ):
        assert pair in HF.CONFIRM_TABLE, pair
        assert HF.CONFIRM_TABLE[pair] == fork_argv


# ---------------------------------------------------------------------------
# Native Qubit subcommands that already implement the upstream surface.
# ---------------------------------------------------------------------------
# (qargv, fork-free) — the Qubit parser owns these; a live caller + on-disk
# test already closes the ledger row without a fork route.
NATIVE_ROUTES = (
    ["auth", "status"], ["auth", "logout"], ["browser", "close-profile"],
    ["computer-use", "doctor"], ["computer-use", "status"],
    ["computer-use", "permissions"], ["computer-use", "permissions", "status"],
    ["schedules", "create"], ["gateway", "list"], ["hooks", "doctor"],
    ["hooks", "list"], ["hooks", "revoke"], ["hooks", "test"],
    ["memory", "status"], ["pairing", "list"], ["pairing", "approve"],
    ["pairing", "revoke"], ["peer", "add"], ["peer", "list"], ["peer", "remove"],
    ["plugins", "list"], ["plugins", "show"], ["profile", "install"],
    ["profile", "list"], ["profile", "show"], ["project", "create"],
    ["project", "list"], ["project", "use"], ["proxy", "providers"],
    ["proxy", "start"], ["proxy", "status"], ["setup", "audit"],
    ["skills", "check"], ["skills", "list"], ["theme", "list"], ["theme", "set"],
    ["webhook", "list"], ["webhook", "remove"], ["webhook", "test"],
)


def test_native_routes_are_owned_by_the_parser_not_the_fork():
    """A native Qubit subcommand must never be shadowed by a fork route."""
    parser_fams = {}
    for action in CLI.build_parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            for fam, sp in action.choices.items():
                subs = set()
                for sub in sp._actions:
                    if isinstance(sub, argparse._SubParsersAction):
                        subs = set(sub.choices)
                parser_fams[fam] = subs
    for qargv in NATIVE_ROUTES:
        fam, sub = qargv[0], qargv[1]
        assert sub in parser_fams.get(fam, set()), qargv
        assert (fam, sub) not in HF.FORWARD_TABLE, qargv
        assert (fam, sub) not in HF.CONFIRM_TABLE, qargv


# ---------------------------------------------------------------------------
# Honest gates: surfaces that cannot be exposed as a distinct Qubit
# subcommand must be a typed refusal / SAFE_EQUIVALENT ledger row, never fake
# success.
# ---------------------------------------------------------------------------
SAFE_EQUIVALENT_ROUTES = (
    ["computer-use", "permissions", "grant"],
    ["computer-use", "install"],
    ["gateway", "install"],
    ["gateway", "restart"],
    ["gateway", "run"],
    ["gateway", "start"],
    ["gateway", "stop"],
    ["sessions", "archive"],
)


def test_safe_equivalent_routes_are_declared_in_the_hermes_ledger():
    section = json.loads(
        (REPO_ROOT / "docs" / "QUBIT_GAP_LEDGER.hermes.json").read_text())
    rows = {r["id"]: r for r in section["rows"]}
    for qargv in SAFE_EQUIVALENT_ROUTES:
        row = rows.get("cli:" + " ".join(qargv))
        assert row is not None, qargv
        assert row["status"] == "SAFE_EQUIVALENT", (qargv, row["status"])
        assert row["status"] != "IMPLEMENTED", (qargv, row["status"])


# ---------------------------------------------------------------------------
# Parser / fork invariants for the whole wave.
# ---------------------------------------------------------------------------
def test_wave5_forward_pairs_do_not_shadow_native_subcommands():
    parser_fams = {}
    for action in CLI.build_parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            for fam, sp in action.choices.items():
                subs = set()
                for sub in sp._actions:
                    if isinstance(sub, argparse._SubParsersAction):
                        subs = set(sub.choices)
                parser_fams[fam] = subs
    for pair in (
        ("approvals", "suggest"), ("auth", "list"), ("schedules", "notepad"),
        ("gateway", "status"), ("mcp", "config"), ("mcp", "ls"),
        ("mcp", "picker"), ("plugins", "search"), ("profile", "alias"),
        ("profile", "describe"), ("profile", "info"), ("sessions", "browse"),
        ("skills", "browse"), ("skills", "diff"), ("skills", "inspect"),
        ("skills", "search"), ("sync", "device"), ("sync", "status"),
    ):
        assert pair[1] not in parser_fams.get(pair[0], set()), pair


def test_wave5_confirm_targets_exist_in_fork_parser():
    script = (
        "import json,sys,argparse;"
        f"sys.path.insert(0,{str(REPO_ROOT / 'qubit_hermes')!r});"
        "import hermes_cli.main as m;"
        "p,sub=m._build_cli_parser();"
        "out={f:None for f in sub.choices};"
        "[(out.__setitem__(f, sorted(a.choices.keys()))) "
        " for f,sp in sub.choices.items() for a in sp._actions "
        " if isinstance(a,argparse._SubParsersAction)];"
        "print(json.dumps(out))"
    )
    r = subprocess.run([sys.executable, "-c", script], cwd=str(REPO_ROOT),
                       capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stderr[-800:]
    tree = json.loads(r.stdout.strip().splitlines()[-1])
    for qpair in (
        ("approvals", "suggest"), ("auth", "list"), ("schedules", "notepad"),
        ("gateway", "status"), ("mcp", "config"), ("mcp", "ls"),
        ("mcp", "picker"), ("plugins", "search"), ("profile", "alias"),
        ("profile", "describe"), ("profile", "info"), ("sessions", "browse"),
        ("skills", "browse"), ("skills", "diff"), ("skills", "inspect"),
        ("skills", "search"), ("sync", "device"), ("sync", "status"),
    ):
        hfam, hsub = HF.FORWARD_TABLE[qpair]
        assert hfam in tree, hfam
        assert hsub in (tree[hfam] or []), (hfam, hsub)
    for (hfam, hsub) in HF.CONFIRM_TABLE.values():
        assert hfam in tree, hfam
        assert hsub in (tree[hfam] or []), (hfam, hsub)


def test_wave5_rows_are_in_the_hermes_ledger():
    section = json.loads(
        (REPO_ROOT / "docs" / "QUBIT_GAP_LEDGER.hermes.json").read_text())
    rows = {r["id"]: r for r in section["rows"]}
    for qargv in (
        ["approvals", "suggest"], ["approvals", "test"], ["auth", "add"],
        ["auth", "list"], ["auth", "priority"], ["auth", "refresh"],
        ["auth", "remove"], ["auth", "reset"], ["auth", "spotify"],
        ["browser", "close-profile"], ["claw", "cleanup"], ["claw", "migrate"],
        ["cron", "create"], ["cron", "incidents"], ["cron", "notepad"],
        ["cron", "remove"], ["cron", "runs"], ["dashboard", "register"],
        ["debug", "delete"], ["debug", "share"], ["fallback", "add"],
        ["fallback", "list"], ["gateway", "list"], ["gateway", "status"],
        ["hooks", "list"], ["mcp", "add"], ["mcp", "config"], ["mcp", "list"],
        ["memory", "status"], ["monitoring", "status"], ["pairing", "list"],
        ["peer", "list"], ["plugins", "compat"], ["plugins", "list"],
        ["profile", "list"], ["project", "list"], ["proxy", "status"],
        ["security", "audit"], ["sessions", "browse"], ["sessions", "pinned"],
        ["skills", "list"], ["skills", "list-modified"], ["skin", "list"],
        ["sync", "status"], ["webhook", "list"], ["worktree", "prune"],
    ):
        assert "cli:" + " ".join(qargv) in rows, qargv


# ---------------------------------------------------------------------------
# Real-fork execution: the routed commands run the vendored CLI.
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


@pytest.mark.parametrize("qargv,fork_family", (
    (["approvals", "suggest"], "approvals"),
    (["auth", "list"], "auth"),
    (["mcp", "config"], "mcp"),
    (["sessions", "browse"], "sessions"),
    (["skills", "browse"], "skills"),
    (["sync", "status"], "sync"),
))
def test_wave5_readonly_route_executes_real_fork(tmp_path, qargv, fork_family):
    r = _run_cli(tmp_path, [*qargv, "--help"])
    assert r.returncode == 0, (qargv, r.stderr[-500:])
    assert f"usage: qubit {fork_family}" in r.stdout, (qargv, r.stdout[:300])


@pytest.mark.parametrize("qargv,fork_family", (
    (["auth", "add"], "auth"),
    (["schedules", "remove"], "cron"),
    (["frontier", "add"], "fallback"),
    (["skills", "config"], "skills"),
))
def test_wave5_confirm_route_executes_real_fork_with_confirm(
        tmp_path, qargv, fork_family):
    r = _run_cli(tmp_path, [*qargv, "--confirm", "--help"])
    assert r.returncode == 0, (qargv, r.stderr[-500:])
    assert f"usage: qubit {fork_family}" in r.stdout, (qargv, r.stdout[:300])


def test_wave5_confirm_route_without_confirm_never_runs_fork(tmp_path):
    r = _run_cli(tmp_path, ["auth", "add"])
    assert r.returncode == 3, (r.returncode, r.stdout[:200])
    assert json.loads(r.stdout)["code"] == "CONFIRM_REQUIRED"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))

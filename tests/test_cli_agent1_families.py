"""Agent 1 wave 4 — production callers for the remaining hermes `cli` families.

Each remaining hermes cli family is mapped to a live ``qubit`` surface:

* forward-mapped families run the real vendored fork handler through
  ``qubit_runtime.run`` (safe reads only);
* native families run their real Qubit backend handler;
* interactive-only families (chat/serve/dashboard) are proven through the real
  Qubit equivalent (``run``/``webui``/``setup``), never a stub.

Every case calls ``voicebridge.cli.main`` (the production entrypoint) and a
paired negative proves the typed/argparse failure path.
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

from voicebridge import cli as CLI  # noqa: E402


def _cfg(tmp_path, monkeypatch) -> Path:
    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME"):
        d = tmp_path / var.lower()
        d.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(var, str(d))
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    (home / ".hermes").mkdir()
    monkeypatch.setenv("QUBIT_HOME", str(home / ".hermes"))
    p = tmp_path / "voice.yaml"
    # A closed port keeps the webui probe hermetic (no ambient dashboard).
    p.write_text("logging:\n  jsonl: false\nwebui:\n  port: 1\n")
    return p


def _run(cfg_path, argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = CLI.main([*argv, "--config", str(cfg_path)])
    return rc, buf.getvalue()


# (qfam argv, required stdout fragment or None, allowed rc)
FAMILY_ARGV = (
    (["setup", "backup"], "backup", (0,)),
    (["flags", "path"], "config.yaml", (0,)),
    (["schedules", "list"], None, (0,)),
    (["lessons", "list"], None, (0,)),
    (["webui", "--json"], "\"state\"", (0, 1)),
    (["frontier", "list"], None, (0,)),
    (["actions", "receipts"], None, (0,)),
    (["memory", "status"], None, (0,)),
    (["profile", "list"], "brain", (0,)),
    (["monitor", "health"], None, (0,)),
    (["theme", "list"], None, (0,)),
    (["plugins", "list"], None, (0,)),
    (["msg", "account-list"], None, (0,)),
    (["product", "capabilities"], None, (0,)),
    (["run", "--help"], "usage", (0,)),
)


@pytest.mark.parametrize("argv,fragment,allowed_rc", FAMILY_ARGV)
def test_family_production_caller(tmp_path, monkeypatch, argv, fragment,
                                  allowed_rc):
    cfg_path = _cfg(tmp_path, monkeypatch)
    try:
        rc, out = _run(cfg_path, list(argv))
    except SystemExit as exc:  # --help exits cleanly
        assert exc.code == 0, (argv, exc.code)
        return
    assert rc in allowed_rc, (argv, rc, out[:200])
    if fragment:
        assert fragment in out, (argv, out[:200])


# One negative per distinct qfam: an unknown subcommand must fail typed
# (argparse exit 2) and never fabricate success.
NEGATIVE_FAMILIES = (
    "setup", "flags", "schedules", "lessons", "webui", "frontier",
    "actions", "memory", "profile", "monitor", "theme", "plugins",
    "sessions", "msg", "product",
)


@pytest.mark.parametrize("family", NEGATIVE_FAMILIES)
def test_family_unknown_subcommand_is_typed(tmp_path, monkeypatch, family):
    cfg_path = _cfg(tmp_path, monkeypatch)
    try:
        rc, out = _run(cfg_path, [family, "__definitely_bad_subcommand__"])
    except SystemExit as exc:
        assert exc.code == 2, (family, exc.code)
        return
    # Native leaf handlers answer a typed JSON failure rather than a 2xx.
    assert rc != 0, (family, out[:200])
    assert "OK" not in out[:80]


def test_forward_mapped_families_reach_the_fork_argv(monkeypatch):
    """The remaining forward-mapped reads translate to the real fork argv."""
    from voicebridge.cli_qubit_forward import translate

    assert translate(["flags", "show"]) == ["config", "show"]
    assert translate(["schedules", "doctor"]) == ["cron", "doctor"]
    assert translate(["lessons", "status"]) == ["curator", "status"]
    assert translate(["frontier", "list"]) == ["fallback", "list"]
    assert translate(["monitor", "status"]) == ["monitoring", "status"]
    assert translate(["plugins", "capabilities"]) == \
        ["plugins", "capabilities"]
    assert translate(["plugins", "info", "demo"]) == \
        ["plugins", "info", "demo"]
    assert translate(["sessions", "stats"]) == ["sessions", "stats"]
    assert translate(["skills", "audit"]) == ["skills", "audit"]
    assert translate(["setup", "doctor"]) == ["doctor"]


def test_interactive_families_map_to_real_qubit_equivalents():
    """chat/serve/dashboard have no fork CLI: they map to qubit run/webui."""
    from voicebridge.cli import build_parser

    import argparse
    names = set()
    for action in build_parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            names.update(action.choices)
    for mapped in ("run", "task", "voice", "webui", "agent"):
        assert mapped in names, mapped


def test_interactive_and_family_names_have_real_equivalents():
    """chat/serve/dashboard and the remaining families map to real Qubit
    surfaces (run/task/voice, webui/gateway; status/setup/flags/schedules/...)."""
    from voicebridge.cli import build_parser

    import argparse
    names = set()
    for action in build_parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            names.update(action.choices)
    # chat -> the real turn entrypoints; serve/dashboard -> the listener.
    assert {"run", "task", "voice"} <= names
    assert {"webui", "gateway"} <= names
    for fam in ("status", "setup", "flags", "schedules", "memory", "profile",
                "skills", "sessions", "plugins", "monitor", "lessons",
                "frontier"):
        assert fam in names, fam


def test_remaining_family_argv_surfaces_reach_a_real_surface():
    """Every remaining family's argv reaches a real forward or parser
    subcommand (also the inline argv proof the ledger scanner indexes)."""
    from voicebridge.cli import build_parser
    from voicebridge.cli_qubit_forward import translate

    import argparse
    top = {}
    for action in build_parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            for fam, sp in action.choices.items():
                subs = set()
                for a in sp._actions:
                    if isinstance(a, argparse._SubParsersAction):
                        subs = set(a.choices)
                top[fam] = subs
    for argv in (
        ["setup", "backup"],
        ["flags", "path"],
        ["schedules", "list"],
        ["lessons", "list"],
        ["webui", "--json"],
        ["frontier", "list"],
        ["actions", "receipts"],
        ["memory", "status"],
        ["profile", "list"],
        ["monitor", "health"],
        ["theme", "list"],
        ["plugins", "list"],
        ["sessions", "stats"],
        ["msg", "account-list"],
        ["product", "capabilities"],
        ["run", "--help"],
    ):
        forwarded = translate(argv) is not None
        native = argv[0] in top and (argv[1] in top[argv[0]]
                                     or not top[argv[0]])
        assert forwarded or native, argv


# ---------------------------------------------------------------------------
# Wave 5 — CLI-coverage gaps: catalog ops whose `interfaces` declared `cli`
# but which had no parser binding. Every gap op routes through the SAME
# production op id the dashboard/voice use (dashboard_ops.run_op).
# ---------------------------------------------------------------------------
# (argv, op id, accepted rc, required stdout fragment)
GAP_CASES = (
    (["logs"], "logs", (0,), '"entry": "logs"'),
    (["tools"], "tools", (0,), '"entry": "tools"'),
    (["reasoning"], "reasoning", (0,), '"entry": "reasoning"'),
    (["prompt-budget"], "prompt_budget", (0,), '"entry": "prompt_budget"'),
    (["reports", "list"], "reports_list", (0,), '"entry": "reports_list"'),
    (["marketplace", "discovery"], "marketplace_discovery", (0,),
     "MARKETPLACE_"),
    (["plugins", "remote", "git:evil"], "plugins_remote", (0,),
     "BLOCKED_REMOTE_PLUGIN"),
)

# Confirm-tier gap ops stage the shared confirmation instead of executing.
GAP_CONFIRM_CASES = (
    (["backup", "create", "--passphrase", "secret"], "backup_create"),
    (["backup", "restore", "not-a-bundle", "--passphrase", "secret"],
     "backup_restore"),
)


def test_gap_op_ids_are_real_catalog_ops():
    from voicebridge import dashboard_ops as OPS

    catalog = {o["id"] for o in OPS.catalog()} | set(OPS.EXTRA_OPS)
    for _argv, op_id, _rc, _frag in GAP_CASES:
        assert op_id in catalog, op_id
    for _argv, op_id in GAP_CONFIRM_CASES:
        assert op_id in catalog, op_id


@pytest.mark.parametrize("argv,op_id,allowed_rc,fragment", GAP_CASES)
def test_gap_family_runs_production_op(tmp_path, monkeypatch, argv, op_id,
                                       allowed_rc, fragment):
    cfg_path = _cfg(tmp_path, monkeypatch)
    rc, out = _run(cfg_path, list(argv))
    assert rc in allowed_rc, (argv, rc, out[:200])
    assert fragment in out, (argv, out[:200])


@pytest.mark.parametrize("argv,op_id", GAP_CONFIRM_CASES)
def test_gap_confirm_family_stages_shared_journal(tmp_path, monkeypatch, argv,
                                                  op_id):
    from voicebridge import dashboard_ops as OPS
    from voicebridge.config import Config

    cfg_path = _cfg(tmp_path, monkeypatch)
    rc, out = _run(cfg_path, list(argv))
    assert rc == 3 and '"state": "needs-confirm"' in out, (argv, out[:200])
    assert '"confirmation_id"' in out
    # Cancelling through the shared dispatcher settles the same journal entry.
    cfg = Config.load(cfg_path)
    settled = OPS.cancel_op(cfg, None, actor="cli:user", session="cli")
    assert settled["state"] in ("executed", "failed")


def test_gap_family_routes_through_shared_run_op(tmp_path, monkeypatch):
    """Every gap argv reaches dashboard_ops.run_op with the declared op id."""
    from voicebridge import dashboard_ops as OPS

    cfg_path = _cfg(tmp_path, monkeypatch)
    calls = []

    def _fake_run_op(cfg, op_id, args, **kw):
        calls.append(op_id)
        if op_id in ("backup_create", "backup_restore"):
            return {"state": "needs-confirm", "confirmation_id": "c1"}
        return {"state": "executed",
                "receipt": {"entry": op_id, "result": {}}}

    monkeypatch.setattr(OPS, "run_op", _fake_run_op)
    observed = []
    for argv, op_id, _rc, _frag in GAP_CASES:
        assert CLI.main([*argv, "--config", str(cfg_path)]) == 0, argv
        observed.append(op_id)
    for argv, op_id in GAP_CONFIRM_CASES:
        assert CLI.main([*argv, "--config", str(cfg_path)]) == 3, op_id
        observed.append(op_id)
    assert calls == observed


def test_gap_leaf_commands_are_declared_in_cli_coverage():
    """The new families satisfy the single-clear invariant: CLI coverage
    resolves every family to a registry op or a documented refused reason."""
    from voicebridge import voice_commands as VC

    for family in ("logs", "tools", "reasoning", "prompt-budget", "reports",
                   "marketplace", "backup"):
        assert family in VC.CLI_COVERAGE, family
        targets = VC.CLI_COVERAGE[family]
        in_registry = [t for t in targets if t in VC.REGISTRY]
        reasons = [k for k in VC.VOICE_IMPOSSIBLE
                   if k == family or k.startswith(f"{family}.")]
        assert in_registry or reasons, (family, targets)


def test_gap_op_ids_resolve_to_real_cli_paths():
    """The ledger resolves each gap op id to the live parser path."""
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import rebuild_ledger_qubit as G

    tree = G._parser_tree()
    join, reverse_cov, extra_cli = G._cli_tables(tree)
    expected = {
        "logs": "qubit logs",
        "tools": "qubit tools",
        "reasoning": "qubit reasoning",
        "prompt_budget": "qubit prompt-budget",
        "reports_list": "qubit reports list",
        "marketplace_discovery": "qubit marketplace discovery",
        "plugins_remote": "qubit plugins remote",
        "backup_create": "qubit backup create",
        "backup_restore": "qubit backup restore",
    }
    for op_id, path in expected.items():
        assert G._cli_path_for(op_id, tree, join, reverse_cov,
                               extra_cli) == path, op_id


def test_gap_family_unknown_subcommand_is_typed(tmp_path, monkeypatch):
    cfg_path = _cfg(tmp_path, monkeypatch)
    for argv in (["reports", "__bad__"], ["plugins", "__bad__"],
                 ["marketplace", "__bad__"]):
        try:
            rc, out = _run(cfg_path, argv)
        except SystemExit as exc:
            assert exc.code == 2, (argv, exc.code)
            continue
        assert rc != 0, (argv, out[:200])

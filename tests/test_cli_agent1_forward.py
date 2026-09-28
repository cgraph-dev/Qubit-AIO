"""Agent 1 — `qubit <family> <sub>` routing into the vendored Hermes fork.

Proves the production caller: `voicebridge.cli.main` forwards the mapped pairs
straight into `voicebridge.qubit_runtime.run` (the same in-process runtime as
`qubit agent`), preserves trailing args, keeps native Qubit commands untouched,
and surfaces the upstream parser's error exit code.

Real-fork execution runs through `python -m voicebridge` in a sandboxed
subprocess so the vendored tree's top-level modules (`tools/`, `tests/`) cannot
leak into the pytest process.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from voicebridge.cli_qubit_forward import (  # noqa: E402
    FORWARD_TABLE, NATIVE_MCP_READS)


def _qubit_subcommands() -> dict:
    """Map every top-level Qubit family to its declared subcommand names."""
    from voicebridge.cli import build_parser

    families: dict = {}
    for action in build_parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            for fam, sp in action.choices.items():
                subs = set()
                for sub in sp._actions:
                    if isinstance(sub, argparse._SubParsersAction):
                        subs.update(sub.choices.keys())
                families[fam] = subs
    return families


def test_forward_table_is_real_and_does_not_shadow_native_commands():
    assert len(FORWARD_TABLE) >= 6, "task floor: at least 6 subcommands"
    families = _qubit_subcommands()
    shadowed = []
    for (fam, sub) in FORWARD_TABLE:
        assert fam in families, f"unknown Qubit family {fam!r}"
        # MCP read spellings are deliberately native now (canonical registry);
        # ``route`` declines them before ``translate`` so the fork never wins.
        if (fam, sub) in NATIVE_MCP_READS:
            continue
        if sub in families.get(fam, set()):
            shadowed.append((fam, sub))
    assert shadowed == [], (
        "forwarding would shadow native Qubit subcommands: " + repr(shadowed))


def test_mcp_reads_are_native_on_the_canonical_registry():
    # ``qubit mcp list/config/ls/picker`` no longer forward to the fork's
    # second ``config.yaml mcp_servers`` store; they are native canonical reads
    # on ``voicebridge.mcp_servers`` (same authority as ``status``/``invoke``).
    for sub in ("list", "config", "ls", "picker"):
        assert sub in _qubit_subcommands()["mcp"], sub
        assert ("mcp", sub) in FORWARD_TABLE, sub  # ledger classification
        assert ("mcp", sub) in NATIVE_MCP_READS, sub
    assert _qubit_subcommands()["mcp"] == {
        "status", "list", "ls", "config", "picker", "invoke"}


@pytest.mark.parametrize("pair", sorted(FORWARD_TABLE))
def test_every_forward_pair_dispatches_to_fork(monkeypatch, pair):
    """Every table row is a real production caller, not a dead mapping."""
    from voicebridge import cli as CLI
    from voicebridge import qubit_runtime

    fam, sub = pair
    if pair in NATIVE_MCP_READS:
        # Superseded by the native canonical read path; the table row stays for
        # ledger classification only.
        pytest.skip("native canonical MCP read (not fork-forwarded)")
    calls = []

    def _fake_run(argv=None):
        calls.append(list(argv or []))
        return 0

    monkeypatch.setattr(qubit_runtime, "run", _fake_run)
    assert CLI.main([fam, sub]) == 0
    assert calls == [[*FORWARD_TABLE[pair]]]


def test_forward_targets_exist_in_fork_parser(tmp_path):
    """Every mapped Hermes family+subcommand exists in the vendored parser."""
    import json

    script = (
        "import json,sys;"
        f"sys.path.insert(0,{str(REPO_ROOT / 'qubit_runtime')!r});"
        "import qubit_cli.main as m;"
        "p,sub=m._build_cli_parser();"
        "out={f:None for f in sub.choices};"
        "import argparse;"
        "[(out.__setitem__(f, sorted(a.choices.keys()))) "
        " for f,sp in sub.choices.items() for a in sp._actions "
        " if isinstance(a, argparse._SubParsersAction)];"
        "print(json.dumps(out))"
    )
    r = subprocess.run([sys.executable, "-c", script],
                       cwd=str(REPO_ROOT), capture_output=True, text=True,
                       timeout=180)
    assert r.returncode == 0, r.stderr[-800:]
    tree = json.loads(r.stdout.strip().splitlines()[-1])
    for mapped in FORWARD_TABLE.values():
        hfam = mapped[0]
        assert hfam in tree, hfam
        if len(mapped) > 1:
            assert mapped[1] in (tree[hfam] or []), tuple(mapped)


def test_translate_maps_family_and_preserves_trailing_args():
    from voicebridge.cli_qubit_forward import translate

    assert translate(["schedules", "doctor"]) == ["cron", "doctor"]
    assert translate(["flags", "path"]) == ["config", "path"]
    assert translate(["frontier", "list", "--json"]) == \
        ["fallback", "list", "--json"]
    assert translate(["sessions", "stats", "extra", "value"]) == \
        ["sessions", "stats", "extra", "value"]
    # Wave 2: Qubit family -> differently-named Hermes family reads.
    assert translate(["lessons", "status"]) == ["curator", "status"]
    assert translate(["lessons", "ledger", "--limit", "5"]) == \
        ["curator", "ledger", "--limit", "5"]
    assert translate(["lessons", "usage", "--json"]) == \
        ["curator", "usage", "--json"]
    assert translate(["mcp", "catalog"]) == ["mcp", "catalog"]


def test_curator_reads_do_not_shadow_native_lessons():
    from voicebridge.cli_qubit_forward import FORWARD_TABLE

    assert _qubit_subcommands()["lessons"] == {"list", "promote", "remove"}
    for sub in ("status", "ledger", "usage", "list-archived",
                "list-unmanaged"):
        assert ("lessons", sub) in FORWARD_TABLE
        assert sub not in _qubit_subcommands()["lessons"]


def test_translate_strips_qubit_only_globals():
    from voicebridge.cli_qubit_forward import translate

    assert translate(["plugins", "capabilities", "--config", "/tmp/x.yaml",
                      "--dry-run"]) == ["plugins", "capabilities"]
    assert translate(["flags", "show", "--config=/tmp/x.yaml"]) == \
        ["config", "show"]


def test_translate_ignores_unmapped_and_short_calls():
    from voicebridge.cli_qubit_forward import translate

    assert translate(["skills", "install", "manifest.json"]) is None
    assert translate(["schedules"]) is None
    assert translate([]) is None
    assert translate(["agent", "status"]) is None


def test_cli_main_dispatches_mapped_pair_to_fork(monkeypatch):
    from voicebridge import cli as CLI
    from voicebridge import qubit_runtime

    calls = []

    def _fake_run(argv=None):
        calls.append(list(argv or []))
        return 0

    monkeypatch.setattr(qubit_runtime, "run", _fake_run)
    rc = CLI.main(["schedules", "doctor", "--config", "/tmp/ignored.yaml"])
    assert rc == 0
    assert calls == [["cron", "doctor"]]


def test_cli_main_keeps_native_qubit_command_off_the_fork(monkeypatch):
    from voicebridge import cli as CLI
    from voicebridge import qubit_runtime

    def _boom(argv=None):  # pragma: no cover - must not be reached
        raise AssertionError("native command must not be forwarded")

    monkeypatch.setattr(qubit_runtime, "run", _boom)
    # `skills install` is a declared Qubit subcommand; the pre-parse forwarder
    # must decline it so the native parser owns it. `--help` exits 0 without
    # ever touching the fork.
    with pytest.raises(SystemExit) as exc:
        CLI.main(["skills", "install", "--help"])
    assert exc.value.code == 0


def _sandbox_env(tmp_path) -> dict:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = dict(os.environ)
    env["HOME"] = str(home)
    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME"):
        target = home / var.lower()
        target.mkdir(parents=True, exist_ok=True)
        env[var] = str(target)
    env["QUBIT_HOME"] = str(home / ".hermes")
    env.pop("PYTHONPATH", None)
    return env


def _run_cli(tmp_path, argv, timeout=180):
    return subprocess.run(
        [sys.executable, "-m", "voicebridge", *argv],
        cwd=str(REPO_ROOT), env=_sandbox_env(tmp_path),
        capture_output=True, text=True, timeout=timeout)


def test_forwarded_command_executes_real_fork_and_prints_output(tmp_path):
    """Production caller end to end: `qubit schedules doctor` runs the fork."""
    r = _run_cli(tmp_path, ["schedules", "doctor"])
    assert r.returncode == 0, r.stderr[-800:]
    assert "Cron doctor" in r.stdout


def test_forwarded_config_path_executes_real_fork(tmp_path):
    r = _run_cli(tmp_path, ["flags", "path"])
    assert r.returncode == 0, r.stderr[-800:]
    assert r.stdout.strip().endswith("config.yaml")


def test_upstream_parser_error_is_preserved(tmp_path):
    """An invalid fork argument exits with the upstream argparse code (2)."""
    r = _run_cli(tmp_path, ["mcp", "list", "--definitely-not-a-flag"])
    assert r.returncode == 2
    assert "unrecognized arguments" in r.stderr or "invalid" in r.stderr


def test_forwarded_help_is_qubit_branded_upstream_parser(tmp_path):
    r = _run_cli(tmp_path, ["schedules", "doctor", "--help"])
    assert r.returncode == 0
    assert "usage: qubit cron doctor" in r.stdout
    assert "-h, --help" in r.stdout


def test_forwarded_curator_status_runs_in_fork(tmp_path):
    r = _run_cli(tmp_path, ["lessons", "status"])
    assert r.returncode == 0, r.stderr[-800:]
    assert "curator:" in r.stdout


def test_forwarded_curator_ledger_help_is_qubit_branded(tmp_path):
    r = _run_cli(tmp_path, ["lessons", "ledger", "--help"])
    assert r.returncode == 0
    assert "usage: qubit curator ledger" in r.stdout


def test_forwarded_mcp_catalog_runs_in_fork(tmp_path):
    r = _run_cli(tmp_path, ["mcp", "catalog"])
    assert r.returncode == 0, r.stderr[-800:]
    assert "MCP Catalog" in r.stdout


def test_forwarded_wave2_parser_error_is_preserved(tmp_path):
    r = _run_cli(tmp_path, ["lessons", "ledger", "--definitely-not-a-flag"])
    assert r.returncode == 2
    assert "unrecognized arguments" in r.stderr or "invalid" in r.stderr

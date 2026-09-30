"""Packet W4 CLI-port wave 6 — the three reference commands the vendored 0.21.1
parser lacked, now registered against their REAL vendored handlers (no stubs):

* ``usage``          — reference ``hermes_cli/subcommands/usage.py``; handler
                       ``agent/account_usage.py``
                       (``fetch_account_usage`` / ``render_account_usage_lines``)
                       + ``qubit_cli/runtime_provider.py``
                       (``resolve_requested_provider``);
* ``codex-runtime``  — reference ``hermes_cli/subcommands/codex_runtime.py``;
                       handler ``qubit_cli/codex_runtime_plugin_migration.py``
                       (``migrate`` / ``MigrationReport``);
* ``plugins browse`` — reference ``hermes_cli/subcommands/plugins.py`` (L52);
                       handler ``qubit_cli/plugins_cmd.py`` (``cmd_search``).

The proof is behavioral: each command parses under the production
``qubit agent`` passthrough (rc 0, no ``invalid choice``), ``codex-runtime
migrate --dry-run`` writes nothing to the codex home, and ``plugins browse``
renders the index without error. Real-fork execution runs through
``python -m voicebridge`` in a sandboxed subprocess so the vendored tree's
top-level modules cannot leak into the pytest process.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# (qubit-agent argv, the argparse usage line the command must print)
PARSED_COMMANDS = (
    (["usage"], "usage: qubit usage"),
    (["codex-runtime"], "usage: qubit codex-runtime"),
    (["plugins", "browse"], "usage: qubit plugins browse"),
)


def _sandbox_env(home: Path, qubit_home: Path) -> dict:
    for d in (home, qubit_home):
        d.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["HOME"] = str(home)
    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME"):
        target = home / var.lower()
        target.mkdir(parents=True, exist_ok=True)
        env[var] = str(target)
    env["QUBIT_HOME"] = str(qubit_home)
    env.pop("PYTHONPATH", None)
    return env


def _run_agent(tmp_path, argv, *, home=None, qubit_home=None, timeout=180):
    """Run the production ``qubit agent <argv>`` passthrough in a scratch HOME."""
    home = home or (tmp_path / "home")
    qubit_home = qubit_home or (home / ".hermes")
    return subprocess.run(
        [sys.executable, "-m", "voicebridge", "agent", *argv],
        cwd=str(REPO_ROOT), env=_sandbox_env(home, qubit_home),
        capture_output=True, text=True, timeout=timeout)


@pytest.mark.parametrize("argv,usage_line", PARSED_COMMANDS)
def test_command_parses_under_qubit_agent(tmp_path, argv, usage_line):
    """Each ported command is a real parser choice: rc 0, no ``invalid choice``."""
    r = _run_agent(tmp_path, [*argv, "--help"])
    assert r.returncode == 0, (argv, r.returncode, r.stderr[-600:])
    combined = r.stdout + r.stderr
    assert "invalid choice" not in combined, (argv, combined[-400:])
    assert usage_line in r.stdout, (argv, r.stdout[:400])


def test_usage_command_has_real_flags(tmp_path):
    """``usage`` exposes the reference ``--provider`` / ``--json`` flags."""
    r = _run_agent(tmp_path, ["usage", "--help"])
    assert r.returncode == 0, r.stderr[-400:]
    assert "--provider" in r.stdout and "--json" in r.stdout, r.stdout[:400]


def test_codex_runtime_migrate_dry_run_is_non_mutating(tmp_path):
    """A dry run must leave an existing codex config byte-identical."""
    home = tmp_path / "home"
    qubit_home = tmp_path / "qh"
    codex = home / ".codex"
    codex.mkdir(parents=True)
    cfg = codex / "config.toml"
    sentinel = "# sentinel-user-content\n[user]\nkeep = true\n"
    cfg.write_text(sentinel, encoding="utf-8")
    before = sorted(p.relative_to(home).as_posix() for p in home.rglob("*") if p.is_file())

    r = _run_agent(tmp_path, ["codex-runtime", "migrate", "--dry-run"],
                   home=home, qubit_home=qubit_home)

    assert r.returncode in (0, 1), (r.returncode, r.stderr[-600:])
    assert "dry run" in r.stdout.lower(), r.stdout[:400]
    assert cfg.read_text(encoding="utf-8") == sentinel, "dry run rewrote config.toml"
    after = sorted(p.relative_to(home).as_posix() for p in home.rglob("*") if p.is_file())
    assert after == before, ("dry run created files", before, after)


def test_codex_runtime_migrate_dry_run_does_not_create_config(tmp_path):
    """With no codex home at all, a dry run must not create ``~/.codex/config.toml``."""
    home = tmp_path / "home"
    qubit_home = tmp_path / "qh"
    home.mkdir(parents=True)

    r = _run_agent(tmp_path, ["codex-runtime", "migrate", "--dry-run"],
                   home=home, qubit_home=qubit_home)

    assert r.returncode in (0, 1), (r.returncode, r.stderr[-600:])
    assert not (home / ".codex" / "config.toml").exists(), "dry run created config.toml"


def test_codex_runtime_bare_lists_migrate_action(tmp_path):
    """Bare ``codex-runtime`` prints help listing the ``migrate`` action, rc 0."""
    r = _run_agent(tmp_path, ["codex-runtime"])
    assert r.returncode == 0, (r.returncode, r.stderr[-400:])
    assert "migrate" in r.stdout, r.stdout[:400]


def test_plugins_browse_renders_index_without_error(tmp_path):
    """``plugins browse`` renders the (real, possibly seed-backed) index, rc 0."""
    r = _run_agent(tmp_path, ["plugins", "browse"])
    assert r.returncode == 0, (r.returncode, r.stderr[-600:])
    assert "Traceback" not in r.stderr, r.stderr[-600:]
    assert r.stdout.strip(), "browse produced no output"
    assert "index source" in r.stdout.lower(), r.stdout[:400]


def test_ported_commands_are_registered_in_the_vendored_parser(tmp_path):
    """Registration invariant: top-level choices + the ``plugins`` action set."""
    script = (
        "import argparse,json,sys;"
        f"sys.path.insert(0,{str(REPO_ROOT / 'qubit_runtime')!r});"
        "import qubit_cli.main as m;"
        "p,sub=m._build_cli_parser();"
        "plugins=next(a for a in sub.choices['plugins']._actions "
        "if isinstance(a,argparse._SubParsersAction));"
        "print(json.dumps({'top': sorted(sub.choices.keys()), "
        "'plugins': sorted(plugins.choices.keys())}))"
    )
    home = tmp_path / "home"
    env = _sandbox_env(home, home / ".hermes")
    r = subprocess.run([sys.executable, "-c", script], cwd=str(REPO_ROOT),
                       env=env, capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stderr[-800:]
    tree = json.loads(r.stdout.strip().splitlines()[-1])
    assert "usage" in tree["top"], tree["top"]
    assert "codex-runtime" in tree["top"], tree["top"]
    assert "browse" in tree["plugins"], tree["plugins"]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))

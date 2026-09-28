"""Packet W4 CLI-port wave 7 — two reference 0.21.4 families the vendored 0.21.1
parser lacked, wired to their REAL vendored implementations (no stubs):

* ``migrate relay``            — reference ``hermes_cli/subcommands/migrate.py``
                                 + ``hermes_cli/relay_plugin_migrate.py``
                                 (``cmd_migrate_relay`` / ``migrate_profile_relay_env``),
                                 rebranded to ``qubit_cli``;
* ``profile migrate-identity`` — reference ``hermes_cli/subcommands/profile.py``
  ``profile purge-identity``     + ``profile_cmd.py`` + ``profile_identity.py``,
                                 backed by the ported ``profiles`` helpers
                                 (``_live_default_multiplexer`` / ``_unknown_profile_error``).

The proof is behavioral and hermetic: each command parses under the production
``qubit agent`` passthrough (rc 0, no ``invalid choice``); ``migrate relay``
rewrites a temp profile's ``.env`` + writes ``relay-plugins.toml``, and a second
run is a no-op; the identity commands validate their inputs and are idempotent
against temp profile dirs. Real execution runs through ``python -m voicebridge``
in a sandboxed subprocess (temp ``HOME`` / ``QUBIT_HOME``) so the vendored tree
and the operator's live home are never touched.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# (qubit-agent argv, the argparse usage line the command must print)
PARSED_COMMANDS = (
    (["migrate", "relay"], "usage: qubit migrate relay"),
    (["profile", "migrate-identity"], "usage: qubit profile migrate-identity"),
    (["profile", "purge-identity"], "usage: qubit profile purge-identity"),
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
def test_ported_command_parses_under_qubit_agent(tmp_path, argv, usage_line):
    """Each ported command is a real parser choice: rc 0, no ``invalid choice``."""
    r = _run_agent(tmp_path, [*argv, "--help"])
    assert r.returncode == 0, (argv, r.returncode, r.stderr[-600:])
    combined = r.stdout + r.stderr
    assert "invalid choice" not in combined, (argv, combined[-400:])
    assert usage_line in r.stdout, (argv, r.stdout[:400])


def test_bare_migrate_lists_relay_action(tmp_path):
    """Bare ``migrate`` help lists the ported ``relay`` action, rc 0."""
    r = _run_agent(tmp_path, ["migrate", "--help"])
    assert r.returncode == 0, r.stderr[-400:]
    assert "relay" in r.stdout, r.stdout[:400]


def test_bare_profile_lists_identity_actions(tmp_path):
    """Bare ``profile`` help lists both ported identity actions, rc 0."""
    r = _run_agent(tmp_path, ["profile", "--help"])
    assert r.returncode == 0, r.stderr[-400:]
    assert "migrate-identity" in r.stdout and "purge-identity" in r.stdout, r.stdout[:600]


# ---------------------------------------------------------------------------
# `migrate relay` — real `.env` rewrite + relay-plugins.toml, idempotent
# ---------------------------------------------------------------------------
def test_migrate_relay_rewrites_env_and_is_idempotent(tmp_path):
    home = tmp_path / "home"
    qh = tmp_path / "qh"
    qh.mkdir(parents=True)
    env_file = qh / ".env"
    env_file.write_text(
        "OPENAI_API_KEY=sk-not-a-real-key\n"
        "HERMES_NEMO_RELAY_ATIF_ENABLED=true\n"
        "HERMES_NEMO_RELAY_ATIF_AGENT_NAME=TestAgent\n"
        "HERMES_NEMO_RELAY_ATIF_MODEL_NAME=test-model\n",
        encoding="utf-8")

    r1 = _run_agent(tmp_path, ["migrate", "relay", "--no-validate"],
                    home=home, qubit_home=qh)
    assert r1.returncode == 0, (r1.returncode, r1.stderr[-600:], r1.stdout[-600:])

    toml_path = qh / "relay-plugins.toml"
    assert toml_path.is_file(), "relay-plugins.toml was not written"
    toml_text = toml_path.read_text(encoding="utf-8")
    assert "version = 1" in toml_text, toml_text[:300]
    assert "agent_name = \"TestAgent\"" in toml_text, toml_text[:600]

    env_text = env_file.read_text(encoding="utf-8")
    # Legacy lines are commented out, not deleted; the config env is appended.
    assert "# migrated to relay-plugins.toml: HERMES_NEMO_RELAY_ATIF_ENABLED=true" in env_text, env_text
    assert "HERMES_NEMO_RELAY_ATIF_ENABLED=true\n" not in env_text.replace(
        "# migrated to relay-plugins.toml: HERMES_NEMO_RELAY_ATIF_ENABLED=true\n", ""), env_text
    assert "HERMES_NEMO_RELAY_PLUGINS_TOML=" in env_text, env_text
    assert "OPENAI_API_KEY=sk-not-a-real-key\n" in env_text, env_text

    before = toml_path.read_bytes()
    env_before = env_file.read_bytes()

    r2 = _run_agent(tmp_path, ["migrate", "relay", "--no-validate"],
                    home=home, qubit_home=qh)
    assert r2.returncode == 0, (r2.returncode, r2.stderr[-600:])
    assert toml_path.read_bytes() == before, "second run rewrote relay-plugins.toml"
    assert env_file.read_bytes() == env_before, "second run rewrote .env"
    assert "nothing to migrate" in (r2.stdout + r2.stderr), (r2.stdout, r2.stderr)


def test_migrate_relay_without_env_is_a_noop(tmp_path):
    home = tmp_path / "home"
    qh = tmp_path / "qh"
    qh.mkdir(parents=True)

    r = _run_agent(tmp_path, ["migrate", "relay"], home=home, qubit_home=qh)
    assert r.returncode == 0, (r.returncode, r.stderr[-600:])
    assert "nothing to migrate (no .env)" in (r.stdout + r.stderr), (r.stdout, r.stderr)
    assert not (qh / "relay-plugins.toml").exists()


# ---------------------------------------------------------------------------
# `profile migrate-identity` / `purge-identity` — validation + temp-dir no-op
# ---------------------------------------------------------------------------
def test_profile_migrate_identity_refuses_default(tmp_path):
    r = _run_agent(tmp_path, ["profile", "migrate-identity", "default", "other"])
    assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
    assert "named profiles only" in (r.stdout + r.stderr), (r.stdout, r.stderr)


def test_profile_migrate_identity_requires_the_new_profile(tmp_path):
    r = _run_agent(tmp_path, ["profile", "migrate-identity", "oldname", "doesnotexist"])
    assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
    assert "No profile named 'doesnotexist'" in (r.stdout + r.stderr), (r.stdout, r.stderr)


def test_profile_migrate_identity_temp_dir_is_a_noop_and_idempotent(tmp_path):
    home = tmp_path / "home"
    qh = tmp_path / "qh"
    (qh / "profiles" / "newname").mkdir(parents=True)

    r1 = _run_agent(tmp_path, ["profile", "migrate-identity", "oldmissing", "newname"],
                    home=home, qubit_home=qh)
    assert r1.returncode == 0, (r1.returncode, r1.stderr[-600:], r1.stdout[-300:])
    assert "Session/routing identity migrated: oldmissing" in r1.stdout, r1.stdout

    r2 = _run_agent(tmp_path, ["profile", "migrate-identity", "oldmissing", "newname"],
                    home=home, qubit_home=qh)
    assert r2.returncode == 0, ("second run must be a no-op success", r2.stderr[-600:])
    assert "Session/routing identity migrated: oldmissing" in r2.stdout, r2.stdout


def test_profile_purge_identity_refuses_a_live_profile(tmp_path):
    home = tmp_path / "home"
    qh = tmp_path / "qh"
    (qh / "profiles" / "live").mkdir(parents=True)

    r = _run_agent(tmp_path, ["profile", "purge-identity", "live"],
                   home=home, qubit_home=qh)
    assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
    assert "already completed" in (r.stdout + r.stderr), (r.stdout, r.stderr)


def test_profile_purge_identity_temp_deleted_is_a_noop_and_idempotent(tmp_path):
    home = tmp_path / "home"
    qh = tmp_path / "qh"
    qh.mkdir(parents=True)

    r1 = _run_agent(tmp_path, ["profile", "purge-identity", "gone"],
                    home=home, qubit_home=qh)
    assert r1.returncode == 0, (r1.returncode, r1.stderr[-600:], r1.stdout[-300:])
    assert "Session/routing identity purged: gone" in r1.stdout, r1.stdout

    r2 = _run_agent(tmp_path, ["profile", "purge-identity", "gone"],
                    home=home, qubit_home=qh)
    assert r2.returncode == 0, ("second purge must be an idempotent no-op", r2.stderr[-600:])
    assert "Session/routing identity purged: gone" in r2.stdout, r2.stdout


# ---------------------------------------------------------------------------
# Registration invariant against the vendored parser itself.
# ---------------------------------------------------------------------------
def test_ported_commands_are_registered_in_the_vendored_parser(tmp_path):
    """``migrate`` exposes ``relay``; ``profile`` exposes both identity actions."""
    script = (
        "import argparse,json,sys;"
        f"sys.path.insert(0,{str(REPO_ROOT / 'qubit_runtime')!r});"
        "import qubit_cli.main as m;"
        "p,sub=m._build_cli_parser();"
        "out={};"
        "[(out.__setitem__(f, sorted(a.choices.keys()))) "
        " for f,sp in sub.choices.items() for a in sp._actions "
        " if isinstance(a, argparse._SubParsersAction)];"
        "print(json.dumps(out))"
    )
    home = tmp_path / "home"
    env = _sandbox_env(home, home / ".hermes")
    r = subprocess.run([sys.executable, "-c", script], cwd=str(REPO_ROOT),
                       env=env, capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stderr[-800:]
    tree = json.loads(r.stdout.strip().splitlines()[-1])
    assert "relay" in (tree.get("migrate") or []), tree.get("migrate")
    assert "migrate-identity" in (tree.get("profile") or []), tree.get("profile")
    assert "purge-identity" in (tree.get("profile") or []), tree.get("profile")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))

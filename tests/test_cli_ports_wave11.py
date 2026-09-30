"""W4 CLI port wave 11: ``qubit gateway migrate`` (real plan/preflight, operator-owned apply) and
``qubit auth upgrade`` (operator-owned external typed refusal).

Ported surface (reference 0.21.4):
  hermes_cli/subcommands/gateway.py:~132  -> qubit_cli/subcommands/gateway.py
  hermes_cli/gateway.py:~5054 _cmd_migrate -> qubit_cli/gateway.py dispatch
  hermes_cli/gateway_migrate.py            -> qubit_cli/gateway_migrate.py
  hermes_cli/gateway_migrate_guards.py     -> qubit_cli/gateway_migrate_guards.py
  hermes_cli/gateway_multiplex_served.py   -> qubit_cli/gateway_multiplex_served.py
  hermes_cli/subcommands/auth.py:~67       -> qubit_cli/subcommands/auth.py
  hermes_cli/auth_commands.py:~885         -> qubit_cli/auth_commands.py (typed operator refusal)

Preserved source behavior vs CGraph operator split is called out per test. Real-fork execution runs
through ``python -m voicebridge agent`` in a sandboxed subprocess so the vendored tree's top-level
modules do not leak into the pytest process.
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
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = REPO_ROOT / "qubit_runtime"


# --------------------------------------------------------------------------- sandbox


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
    qubit_home = qubit_home or (home / "qh")
    return subprocess.run(
        [sys.executable, "-m", "voicebridge", "agent", *argv],
        cwd=str(REPO_ROOT), env=_sandbox_env(home, qubit_home),
        capture_output=True, text=True, timeout=timeout)


@pytest.fixture(autouse=True)
def _vendored_import(tmp_path, monkeypatch):
    """Put the vendored fork on sys.path and redirect QUBIT_HOME to a temp dir."""
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "qh-default"))
    if str(RUNTIME_ROOT) not in sys.path:
        sys.path.insert(0, str(RUNTIME_ROOT))
    import qubit_constants
    monkeypatch.setattr(qubit_constants, "_default_qubit_root_memo", None, raising=False)
    yield


def _fresh_import(name: str):
    import importlib

    return importlib.import_module(name)


# --------------------------------------------------------------------------- parser surface


def test_gateway_migrate_parses_under_qubit_agent(tmp_path):
    """The subcommand is a real parser choice: rc 0, no 'invalid choice'."""
    r = _run_agent(tmp_path, ["gateway", "migrate", "--help"])
    assert r.returncode == 0, (r.returncode, r.stderr[-600:])
    combined = r.stdout + r.stderr
    assert "invalid choice" not in combined, combined[-400:]
    assert "usage: qubit gateway migrate" in r.stdout, r.stdout[:400]
    assert "--dry-run" in r.stdout and "--multiplex" in r.stdout


def test_auth_upgrade_parses_under_qubit_agent(tmp_path):
    """``auth upgrade`` is a real parser choice: rc 0, no 'invalid choice'."""
    r = _run_agent(tmp_path, ["auth", "upgrade", "--help"])
    assert r.returncode == 0, (r.returncode, r.stderr[-600:])
    combined = r.stdout + r.stderr
    assert "invalid choice" not in combined, combined[-400:]
    assert "usage: qubit auth upgrade" in r.stdout, r.stdout[:400]


def test_gateway_migrate_registered_in_vendored_tree(tmp_path):
    """Registration invariant: the dispatch table maps ``migrate`` to a handler."""
    script = (
        "import json,sys;"
        f"sys.path.insert(0,{str(RUNTIME_ROOT)!r});"
        "from qubit_cli.gateway import _GATEWAY_SUBCOMMANDS;"
        "from qubit_cli.auth_commands import _AUTH_ACTIONS;"
        "print(json.dumps({'gw': sorted(k for k in _GATEWAY_SUBCOMMANDS if k), "
        "'auth': sorted(_AUTH_ACTIONS)}))"
    )
    r = subprocess.run([sys.executable, "-c", script], cwd=str(REPO_ROOT),
                       env=_sandbox_env(tmp_path / "home", tmp_path / "qh"),
                       capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stderr[-800:]
    tree = json.loads(r.stdout.strip().splitlines()[-1])
    assert "migrate" in tree["gw"], tree["gw"]
    assert "upgrade" in tree["auth"], tree["auth"]


# --------------------------------------------------------------------------- plan / preflight


def _make_profiles(home: Path, names=("alpha", "beta")) -> None:
    for name in names:
        profile = home / "profiles" / name
        profile.mkdir(parents=True, exist_ok=True)
        (profile / "config.yaml").write_text("model: {}\n", encoding="utf-8")


def test_build_migration_plan_reads_a_temp_home(tmp_path, monkeypatch):
    """The plan enumerates the real profiles under the active QUBIT_HOME (read-only)."""
    home = tmp_path / "qh"
    home.mkdir()
    _make_profiles(home)
    (home / "config.yaml").write_text("gateway:\n  multiplex_profiles: false\n", encoding="utf-8")

    gm = _fresh_import("qubit_cli.gateway_migrate")
    monkeypatch.setenv("QUBIT_HOME", str(home))
    monkeypatch.setattr(gm, "_installed_services", lambda h: [])
    monkeypatch.setattr(gm, "_live_gateway_pid", lambda h: None)
    monkeypatch.setattr(gm, "_host_gateway_owner", lambda: None)
    monkeypatch.setattr(gm, "_load_profile_configs", lambda plan: {})

    plan = gm.build_migration_plan()
    names = {p.name for p in plan.profiles}
    assert "default" in names
    assert {"alpha", "beta"} <= names, names
    assert plan.blocked is False
    assert plan.default_home == home


def test_format_plan_does_not_mutate_the_home(tmp_path, monkeypatch):
    """``format_plan`` renders the plan table without touching the filesystem."""
    home = tmp_path / "qh"
    home.mkdir()
    _make_profiles(home)
    (home / "config.yaml").write_text("gateway:\n  multiplex_profiles: false\n", encoding="utf-8")

    gm = _fresh_import("qubit_cli.gateway_migrate")
    monkeypatch.setenv("QUBIT_HOME", str(home))
    monkeypatch.setattr(gm, "_installed_services", lambda h: [])
    monkeypatch.setattr(gm, "_live_gateway_pid", lambda h: None)
    monkeypatch.setattr(gm, "_host_gateway_owner", lambda: None)
    monkeypatch.setattr(gm, "_load_profile_configs", lambda plan: {})

    plan = gm.build_migration_plan()
    before = sorted(p.relative_to(home).as_posix() for p in home.rglob("*"))
    text = "\n".join(gm.format_plan(plan, dry_run=True))
    after = sorted(p.relative_to(home).as_posix() for p in home.rglob("*"))

    assert before == after, "the plan must not mutate the home"
    assert "Migration plan (dry run — nothing changed)" in text
    assert "gateway.multiplex_profiles: true" in text


def test_cmd_migrate_dry_run_is_read_only(tmp_path, monkeypatch):
    """--dry-run is read-only and succeeds (source behavior: print the plan)."""
    home = tmp_path / "qh"
    home.mkdir()
    gm = _fresh_import("qubit_cli.gateway_migrate")
    monkeypatch.setattr(gm, "build_migration_plan", lambda: gm.MigrationPlan(
        default_home=home, profiles=[gm.ProfileGateway("default", home)],
        multiplex_flag_on=False, live_served=None))
    buf = io.StringIO()
    with redirect_stdout(buf):
        gm.cmd_migrate(SimpleNamespace(dry_run=True))
    assert "Migration plan (dry run" in buf.getvalue()
    assert list(home.iterdir()) == []


def test_cmd_migrate_apply_refuses_as_operator_owned(tmp_path, monkeypatch):
    """Operator split: the destructive apply refuses with a typed code and changes nothing."""
    home = tmp_path / "qh"
    home.mkdir()
    gm = _fresh_import("qubit_cli.gateway_migrate")
    monkeypatch.setattr(gm, "build_migration_plan", lambda: gm.MigrationPlan(
        default_home=home, profiles=[gm.ProfileGateway("default", home)],
        multiplex_flag_on=False, live_served=None))
    buf = io.StringIO()
    with redirect_stdout(buf), pytest.raises(SystemExit) as exc:
        gm.cmd_migrate(SimpleNamespace(dry_run=False))
    assert exc.value.code == 3
    out = buf.getvalue()
    assert gm.APPLY_REFUSAL_CODE in out
    assert "operator-owned" in out
    assert not (home / gm.MANIFEST_NAME).exists()
    assert list(home.iterdir()) == []


def test_duplicate_credential_preflight_blocks(tmp_path):
    """Source behavior preserved: two profiles sharing one bot token is a BLOCKER, key names only."""
    gm = _fresh_import("qubit_cli.gateway_migrate")
    from gateway.config import Platform

    class _PC:
        def __init__(self):
            self.value = "telegram"
            self.enabled = True
            self.token = "SAME_SECRET_TOKEN"
            self.api_key = None
            self.extra = {}

    class _Cfg:
        def __init__(self):
            self.platforms = {Platform("telegram"): _PC()}

    cfg = _Cfg()
    assert gm._credential_claims(cfg), "a real token must fingerprint"
    lines = gm.duplicate_credential_lines([("default", cfg), ("alpha", cfg)])
    assert len(lines) == 1, lines
    assert "default" in lines[0] and "alpha" in lines[0]
    assert "SAME_SECRET_TOKEN" not in lines[0], "never print the credential value"


def test_credential_claims_skips_unfingerprintable(tmp_path):
    """A platform config with no discoverable credential contributes no claim (no invented hash)."""
    gm = _fresh_import("qubit_cli.gateway_migrate")
    from gateway.config import Platform

    class _PC:
        value = "telegram"
        enabled = True
        token = ""
        api_key = None
        extra = {}

    class _Cfg:
        platforms = {Platform("telegram"): _PC()}

    assert gm._credential_claims(_Cfg()) == {}


def test_manifest_not_yet_served_semantics(tmp_path):
    """Source behavior: a manifest whose migrated profiles are not all live-served is interrupted."""
    gm = _fresh_import("qubit_cli.gateway_migrate")
    manifest = {"default": {"profile": "default", "home": "/h"},
                "secondaries": [{"profile": "alpha", "home": "/a"}]}
    assert gm._manifest_not_yet_served(manifest, None) is True
    assert gm._manifest_not_yet_served(manifest, ["default", "alpha"]) is False
    assert gm._manifest_not_yet_served(None, ["default"]) is False


def test_guards_report_out_of_tree_home(tmp_path):
    """``gateway_migrate_guards`` is implemented and reports an out-of-tree QUBIT_HOME boundary."""
    gm = _fresh_import("qubit_cli.gateway_migrate")
    guards = _fresh_import("qubit_cli.gateway_migrate_guards")
    home = tmp_path / "qh"
    home.mkdir()
    default = gm.ProfileGateway("default", home)
    alpha = gm.ProfileGateway("alpha", home / "profiles" / "alpha", pid=1234,
                              runtime_home=Path("/somewhere/else"))
    plan = gm.MigrationPlan(default_home=home, profiles=[default, alpha],
                            multiplex_flag_on=False, live_served=None)
    findings = guards.auto_migration_blockers(plan)
    assert any("outside" in f for f in findings), findings


def test_recorded_served_profiles_none_without_live_gateway(tmp_path, monkeypatch):
    """``recorded_served_profiles`` returns None when no live default gateway (caller falls back)."""
    served = _fresh_import("qubit_cli.gateway_multiplex_served")
    monkeypatch.setattr(served, "live_default_gateway_pid", lambda: None)
    assert served.recorded_served_profiles(tmp_path) is None


def test_gateway_migrate_dry_run_subprocess(tmp_path):
    """End-to-end: the production passthrough prints a read-only plan on a temp home, rc 0."""
    home = tmp_path / "home"
    qh = tmp_path / "qh"
    r = _run_agent(tmp_path, ["gateway", "migrate", "--dry-run"], home=home, qubit_home=qh)
    assert r.returncode == 0, (r.returncode, r.stderr[-800:])
    assert "Migration plan" in r.stdout, r.stdout[:400]
    assert not (qh / "gateway_migration.json").exists()


# --------------------------------------------------------------------------- auth upgrade


def test_auth_upgrade_is_a_typed_operator_refusal(tmp_path, monkeypatch, capsys):
    """Operator-owned external: no local Nous sign-in to port; refuses with a named prerequisite."""
    auth_commands = _fresh_import("qubit_cli.auth_commands")
    with pytest.raises(SystemExit) as exc:
        auth_commands.auth_upgrade_command(SimpleNamespace(no_browser=True, timeout=None))
    assert exc.value.code == 3
    out = capsys.readouterr().out
    assert "AUTH_UPGRADE_OPERATOR_OWNED" in out
    assert "operator-owned" in out
    # must NOT fabricate a sign-in or claim success
    assert "signed in" not in out.lower()
    assert "success" not in out.lower()


def test_auth_upgrade_refusal_subprocess(tmp_path):
    """End-to-end: the production passthrough refuses with rc 3 (not a silent no-op)."""
    r = _run_agent(tmp_path, ["auth", "upgrade"])
    assert r.returncode == 3, (r.returncode, r.stdout[-400:], r.stderr[-400:])
    assert "AUTH_UPGRADE_OPERATOR_OWNED" in r.stdout


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))

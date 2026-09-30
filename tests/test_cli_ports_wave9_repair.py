"""Packet W4 CLI-port wave 9 — ``sessions repair-profiles``, ported from the read-only
reference 0.21.4 onto the VENDORED 0.21.1 tree (real behavior, no stubs, no live home):

* parser   — reference ``hermes_cli/subcommands/sessions.py:207``;
  vendored ``qubit_cli/subcommands/sessions.py`` (``repair-profiles`` with
  ``--apply`` / ``--json`` / ``--yes`` / ``--legacy-main``);
* CLI face — reference ``hermes_cli/sessions_cmd_repair_profiles.py`` →
  vendored ``qubit_cli/sessions_cmd_repair_profiles.py``;
* orchestrator — reference ``hermes_cli/sessions_repair_profiles.py`` →
  vendored ``qubit_cli/sessions_repair_profiles.py``;
* store fixers — reference ``hermes_state_profile_repair.py`` (top-level) →
  vendored ``qubit_cli/qubit_state_profile_repair.py`` (``SessionProfileRepairMixin``
  composed onto the vendored ``SessionDB`` as ``RepairSessionDB``; 0.21.1 does not
  mix it into ``SessionDB``);
* liveness/multiplex read — vendored ``qubit_cli/gateway_multiplex_mode.py``
  (``live_gateway_pid_for_home`` is the 0.21.1 stand-in for the reference
  ``gateway.status`` helper, which the vendored tree lacks).

Proof is behavioral and hermetic: real ``state.db`` stores are created under a
temp ``QUBIT_HOME`` with intentionally crossed profile names — report-only shows
the planned fixes and changes nothing; ``--apply`` is blocked while a gateway pid
reads live; ``--apply`` with none live repairs, snapshots first, and a second run
is clean. The operator's live home and the reference at ``127.0.0.1:9119`` are
never touched. No parity / M1 / M2 / VERIFIED claim.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
from contextlib import redirect_stderr
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = REPO_ROOT / "qubit_runtime"


# ---------------------------------------------------------------------------
# Sandbox helpers (mirror tests/test_cli_ports_wave7.py / wave8.py)
# ---------------------------------------------------------------------------
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


def _run_agent(tmp_path, argv, *, home=None, qubit_home=None, timeout=300):
    """Run the production ``qubit agent <argv>`` passthrough in a scratch HOME."""
    home = home or (tmp_path / "home")
    qubit_home = qubit_home or (home / ".hermes")
    return subprocess.run(
        [sys.executable, "-m", "voicebridge", "agent", *argv],
        cwd=str(REPO_ROOT), env=_sandbox_env(home, qubit_home),
        capture_output=True, text=True, timeout=timeout)


@pytest.fixture(autouse=True)
def _vendored_import(tmp_path, monkeypatch):
    """Put the vendored fork on sys.path and redirect QUBIT_HOME to a temp dir."""
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "qh"))
    if str(RUNTIME_ROOT) not in sys.path:
        sys.path.insert(0, str(RUNTIME_ROOT))
    yield


# ---------------------------------------------------------------------------
# Temp-store helpers
# ---------------------------------------------------------------------------
def _seed_crossed(qubit_home: Path) -> None:
    """Three crossed rows in the default store + a live 'work' profile.

    * ``s-mis``    — ``profile_name='work'`` but its key is ``agent:main:…`` → mislabelled;
    * ``s-parent`` — keyed ``agent:work:…`` sitting in the default store → wrong store;
    * ``s-child``  — keyed ``agent:main:…`` inheriting from the ``work``-keyed parent →
      crossed parent.
    """
    from qubit_state import SessionDB

    (qubit_home / "profiles" / "work").mkdir(parents=True, exist_ok=True)
    db = SessionDB(qubit_home / "state.db")

    def ins(sid: str, key: str, profile: str, parent: str | None = None) -> None:
        db._execute_write(lambda conn: conn.execute(
            "INSERT INTO sessions (id, source, session_key, profile_name, parent_session_id, "
            "started_at, message_count) VALUES (?,?,?,?,?,?,0)",
            (sid, "cli", key, profile, parent, 1.0)))

    ins("s-mis", "agent:main:cli:dm:m", "work")
    ins("s-parent", "agent:work:cli:dm:p", "work")
    ins("s-child", "agent:main:cli:dm:c", "default", parent="s-parent")
    db.close()
    SessionDB(qubit_home / "profiles" / "work" / "state.db").close()


def _profile_rows(db_path: Path) -> dict:
    from qubit_state import SessionDB
    db = SessionDB(db_path, read_only=True)
    try:
        return {r["id"]: (r["session_key"], r["profile_name"], r["parent_session_id"])
                for r in db._read_all("SELECT id, session_key, profile_name, parent_session_id FROM sessions")}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------
def test_repair_profiles_help_parses(tmp_path):
    """The ported command is a real parser choice: rc 0, usage line, all flags."""
    r = _run_agent(tmp_path, ["sessions", "repair-profiles", "--help"])
    assert r.returncode == 0, (r.returncode, r.stderr[-600:])
    combined = r.stdout + r.stderr
    assert "invalid choice" not in combined, combined[-400:]
    assert "usage: qubit sessions repair-profiles" in r.stdout, r.stdout[:400]
    for flag in ("--apply", "--json", "--yes", "--legacy-main"):
        assert flag in r.stdout, (flag, r.stdout[:600])


# ---------------------------------------------------------------------------
# Report-only
# ---------------------------------------------------------------------------
def test_report_only_shows_plan_and_changes_nothing(tmp_path):
    home = tmp_path / "home"
    qh = tmp_path / "qh"
    qh.mkdir(parents=True)
    _seed_crossed(qh)
    before = {p: _profile_rows(p) for p in (qh / "state.db", qh / "profiles" / "work" / "state.db")}

    r = _run_agent(tmp_path, ["sessions", "repair-profiles"], home=home, qubit_home=qh)
    assert r.returncode == 0, (r.returncode, r.stderr[-800:], r.stdout[-800:])
    out = r.stdout
    assert "Scanned 2 profile store(s): default, work" in out, out
    assert "rows in another profile's store" in out, out
    assert "profile_name disagrees with the row's own session key" in out, out
    assert "parent_session_id crossing profile namespaces" in out, out
    assert "move to the work store" in out, out
    assert "relabel to 'default'" in out, out
    assert "3 of 3 finding(s) can be repaired" in out, out

    after = {p: _profile_rows(p) for p in (qh / "state.db", qh / "profiles" / "work" / "state.db")}
    assert after == before, "report-only run mutated a store"


def test_json_report_is_machine_readable(tmp_path):
    home = tmp_path / "home"
    qh = tmp_path / "qh"
    qh.mkdir(parents=True)
    _seed_crossed(qh)

    r = _run_agent(tmp_path, ["sessions", "repair-profiles", "--json"], home=home, qubit_home=qh)
    assert r.returncode == 0, (r.returncode, r.stderr[-800:], r.stdout[-800:])
    payload = json.loads(r.stdout)
    assert payload["stores"] == ["default", "work"], payload
    assert payload["repairable"] == 3, payload
    kinds = {f["kind"] for f in payload["findings"]}
    assert {"wrong_store", "mislabelled", "crossed_parent"} <= kinds, payload


# ---------------------------------------------------------------------------
# --apply refused while a gateway is live
# ---------------------------------------------------------------------------
def test_apply_blocked_while_a_gateway_pid_is_live(tmp_path, monkeypatch):
    """The gate reads the live-gateway probe per store and refuses before opening one for write."""
    qh = tmp_path / "qh"
    qh.mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("QUBIT_HOME", str(qh))
    _seed_crossed(qh)
    before = {p: _profile_rows(p) for p in (qh / "state.db", qh / "profiles" / "work" / "state.db")}

    import qubit_cli.sessions_repair_profiles as srp
    import qubit_cli.sessions_cmd_repair_profiles as face
    monkeypatch.setattr(srp, "live_gateway_pid_for_home", lambda home: 4242)

    err = io.StringIO()
    with redirect_stderr(err):
        rc = face.cmd_repair_profiles(
            argparse.Namespace(apply=True, json=False, yes=True, legacy_main="report"))
    assert rc == 1, rc
    message = err.getvalue()
    assert "A gateway is running for:" in message, message
    assert "pid 4242" in message, message
    assert "qubit gateway stop" in message, message

    after = {p: _profile_rows(p) for p in (qh / "state.db", qh / "profiles" / "work" / "state.db")}
    assert after == before, "a blocked --apply mutated a store"
    # No snapshot of either store was taken — the refusal precedes apply's snapshot step.
    assert not list(qh.rglob("*repair-profiles*")), list(qh.rglob("*repair-profiles*"))


# ---------------------------------------------------------------------------
# --apply repairs, then is idempotent
# ---------------------------------------------------------------------------
def test_apply_repairs_and_second_run_is_idempotent(tmp_path):
    home = tmp_path / "home"
    qh = tmp_path / "qh"
    qh.mkdir(parents=True)
    _seed_crossed(qh)

    r1 = _run_agent(tmp_path, ["sessions", "repair-profiles", "--apply", "--yes"],
                    home=home, qubit_home=qh)
    assert r1.returncode == 0, (r1.returncode, r1.stderr[-800:], r1.stdout[-800:])
    assert "Applied: imported=1, moved=1, relabelled=1" in r1.stdout, r1.stdout
    assert "snapshot [default]:" in r1.stdout and "snapshot [work]:" in r1.stdout, r1.stdout

    default_rows = _profile_rows(qh / "state.db")
    work_rows = _profile_rows(qh / "profiles" / "work" / "state.db")
    # mislabelled row repaired to the profile in its own key
    assert default_rows["s-mis"][1] == "default", default_rows
    # wrong-store row physically moved into the work store
    assert "s-parent" not in default_rows, default_rows
    assert work_rows["s-parent"][0] == "agent:work:cli:dm:p", work_rows
    assert work_rows["s-parent"][1] == "work", work_rows
    # crossed parent severed
    assert default_rows["s-child"][2] is None, default_rows

    after_first = {p: _profile_rows(p) for p in (qh / "state.db", qh / "profiles" / "work" / "state.db")}
    r2 = _run_agent(tmp_path, ["sessions", "repair-profiles"], home=home, qubit_home=qh)
    assert r2.returncode == 0, (r2.returncode, r2.stderr[-800:])
    assert "✓ No crossed-profile state found." in r2.stdout, r2.stdout
    after_second = {p: _profile_rows(p) for p in (qh / "state.db", qh / "profiles" / "work" / "state.db")}
    assert after_second == after_first, "the second run changed an already-clean store"


def test_unclaimed_namespace_is_reported_not_repaired(tmp_path):
    """A foreign keyed row with no live profile is honest report-only (no guessing)."""
    home = tmp_path / "home"
    qh = tmp_path / "qh"
    qh.mkdir(parents=True)
    from qubit_state import SessionDB
    db = SessionDB(qh / "state.db")
    db._execute_write(lambda conn: conn.execute(
        "INSERT INTO sessions (id, source, session_key, profile_name, started_at, message_count)"
        " VALUES (?,?,?,?,?,0)", ("s-ghost", "cli", "agent:ghost:cli:dm:g", "ghost", 1.0)))
    db.close()

    r = _run_agent(tmp_path, ["sessions", "repair-profiles"], home=home, qubit_home=qh)
    assert r.returncode == 0, (r.returncode, r.stderr[-600:])
    assert "rows keyed to a profile that does not exist" in r.stdout, r.stdout
    assert "no live profile named 'ghost'" in r.stdout, r.stdout
    assert "0 of 1 finding(s) can be repaired" in r.stdout, r.stdout

    r2 = _run_agent(tmp_path, ["sessions", "repair-profiles", "--apply", "--yes"],
                    home=home, qubit_home=qh)
    assert r2.returncode == 0, (r2.returncode, r2.stderr[-600:])
    assert "Nothing to repair." in r2.stdout, r2.stdout
    assert _profile_rows(qh / "state.db")["s-ghost"][1] == "ghost"


# ---------------------------------------------------------------------------
# Unit surface
# ---------------------------------------------------------------------------
def test_session_key_profile_mapping():
    from qubit_cli.qubit_state_profile_repair import session_key_profile
    assert session_key_profile("agent:main:telegram:dm:1") == "default"
    assert session_key_profile("agent:main~:telegram:dm:1") == "main"
    assert session_key_profile("agent:work:slack:chan:7") == "work"
    assert session_key_profile("agent::broken") is None
    assert session_key_profile("cli:not-a-gateway-key") is None
    assert session_key_profile(None) is None


def test_real_live_gateway_probe_is_none_on_a_temp_home(tmp_path):
    """The vendored stand-in for ``gateway.status.live_gateway_pid_for_home`` proves identity, not
    mere presence: a temp home with no gateway identity reads as not-live."""
    from qubit_cli.gateway_multiplex_mode import live_gateway_pid_for_home
    assert live_gateway_pid_for_home(tmp_path / "empty-home") is None
    # A bogus pid file alone is not identity (no active runtime lock / matching command line).
    bogus = tmp_path / "bogus-home"
    bogus.mkdir()
    (bogus / "gateway.pid").write_text(str(os.getpid()), encoding="utf-8")
    assert live_gateway_pid_for_home(bogus) is None


def test_default_gateway_multiplexes_reads_explicit_config(tmp_path):
    """An explicit ``gateway.multiplex_profiles: true`` with no live gateway reads as multiplexing."""
    from qubit_cli.gateway_multiplex_mode import default_gateway_multiplexes, explicit_multiplex_flag
    root = tmp_path / "root"
    root.mkdir()
    assert explicit_multiplex_flag(root) is None
    assert default_gateway_multiplexes(root) is False
    (root / "config.yaml").write_text("gateway:\n  multiplex_profiles: true\n", encoding="utf-8")
    assert explicit_multiplex_flag(root) is True
    assert default_gateway_multiplexes(root) is True

"""Packet W4/W7 follow-up wave 8 — the durable deps behind ``profile migrate-identity``
and ``profile purge-identity`` that the vendored 0.21.1 tree was missing.

Wave 7 ported the commands + delegation but the durable cross-store rewrite could not
run (rc 1): ``SessionDB.rekey_profile_state`` / ``purge_profile_state`` were absent from
``qubit_state_gateway.py`` and ``tools/checkpoint_manager_profile_rename.py`` did not
exist. Both are now vendored from the reference 0.21.4:

* ``rekey_profile_state``  — reference ``hermes_state_gateway.py:583``
* ``purge_profile_state``  — reference ``hermes_state_gateway.py:692``
  (+ the ``_optional_table_columns`` gate at :134)
* ``tools/checkpoint_manager_profile_rename.py`` — reference
  ``tools/checkpoint_manager_profile_rename.py``

Proof is behavioral and hermetic: real ``state.db`` + real checkpoint store created under
a temp ``QUBIT_HOME``, then the production ``qubit agent profile ...`` passthrough run in
a sandboxed subprocess (temp ``HOME``/``QUBIT_HOME``); the operator's live home is never
touched. No milestone/parity claim.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME = REPO_ROOT / "qubit_runtime"

_OLD = "oldprof"
_NEW = "newprof"

_SEED_PROGRAM = """
import json, os, sys
sys.path.insert(0, %(runtime)r)
from pathlib import Path
from qubit_state import SessionDB
home = Path(os.environ["QUBIT_HOME"])
db = SessionDB(home / "state.db")
db.create_session("sess-1", source="cli", profile_name=%(old)r,
                  session_key="agent:%(old)s:chan-1",
                  origin_json=json.dumps({"profile": %(old)r, "kind": "cli"}))
db._execute_write(lambda conn: (
    conn.execute(
        "INSERT OR REPLACE INTO gateway_routing (scope, session_key, entry_json, updated_at)"
        " VALUES (?, ?, ?, ?)",
        ("", "agent:%(old)s:chan-1",
         json.dumps({"session_key": "agent:%(old)s:chan-1",
                     "origin": {"profile": %(old)r}}), 1.0)),
    conn.execute(
        "INSERT OR REPLACE INTO gateway_heartbeats"
        " (backend_id, pid, started_at, last_heartbeat, profile, host)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        ("%(old)s@host:123", 123, 1.0, 1.0, %(old)r, "host")),
))
"""

_CKPT_SETUP_PROGRAM = """
import json, shutil, sys
from pathlib import Path
sys.path.insert(0, %(runtime)r)
import tools.checkpoint_manager as cm
from tools.checkpoint_manager import CheckpointManager
from tools.checkpoint_manager_profile_rename import migrate_profile_checkpoint_projects

base = Path(%(home)r) / "profiles"
old_root, new_root = base / %(old)r, base / %(new)r
work = old_root / "proj"
work.mkdir(parents=True, exist_ok=True)
(work / "a.txt").write_text("hi\\n", encoding="utf-8")
new_root.mkdir(parents=True, exist_ok=True)
cm.CHECKPOINT_BASE = new_root / "checkpoints"
m = CheckpointManager(enabled=True, max_snapshots=10)
m.ensure_checkpoint(str(work), "initial")
store = cm._store_path(new_root / "checkpoints")
old_hash = cm._project_hash(str(work))
before_tip = cm._ref_tip(store, str(work), cm._ref_name(old_hash))
shutil.move(str(work), str(new_root / "proj"))
result = migrate_profile_checkpoint_projects(old_root, new_root)
new_hash = cm._project_hash(str(new_root / "proj"))
print(json.dumps({
    "result": result,
    "before_tip": before_tip,
    "new_tip": cm._ref_tip(store, str(new_root / "proj"), cm._ref_name(new_hash)),
    "new_meta": cm._project_meta_path(store, new_hash).exists(),
    "old_meta": cm._project_meta_path(store, old_hash).exists(),
}))
"""  # noqa: E501

_CKPT_PREPARE_PROGRAM = """
import shutil, sys
from pathlib import Path
sys.path.insert(0, %(runtime)r)
import tools.checkpoint_manager as cm
from tools.checkpoint_manager import CheckpointManager

base = Path(%(home)r) / "profiles"
old_root, new_root = base / %(old)r, base / %(new)r
work = old_root / "proj"
work.mkdir(parents=True, exist_ok=True)
(work / "a.txt").write_text("hi\\n", encoding="utf-8")
new_root.mkdir(parents=True, exist_ok=True)
cm.CHECKPOINT_BASE = new_root / "checkpoints"
m = CheckpointManager(enabled=True, max_snapshots=10)
m.ensure_checkpoint(str(work), "initial")
shutil.move(str(work), str(new_root / "proj"))
print("PREPARED")
"""  # noqa: E501

_EMPTY_STORE_PROGRAM = """
import sys
from pathlib import Path
sys.path.insert(0, %(runtime)r)
from tools.checkpoint_manager_profile_rename import migrate_profile_checkpoint_projects
old_dir = Path(%(old_dir)r)
new_dir = Path(%(new_dir)r)
old_dir.mkdir(parents=True, exist_ok=True)
new_dir.mkdir(parents=True, exist_ok=True)
print(migrate_profile_checkpoint_projects(old_dir, new_dir))
"""  # noqa: E501


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


def _run_py(tmp_path, program, *, home=None, qubit_home=None, timeout=180):
    home = home or (tmp_path / "home")
    qubit_home = qubit_home or (home / ".hermes")
    return subprocess.run(
        [sys.executable, "-c", program], cwd=str(REPO_ROOT),
        env=_sandbox_env(home, qubit_home), capture_output=True, text=True,
        timeout=timeout)


def _seed_state_db(tmp_path, qh):
    prog = _SEED_PROGRAM % {"runtime": str(RUNTIME), "old": _OLD}
    r = _run_py(tmp_path, prog, qubit_home=qh)
    assert r.returncode == 0, (r.returncode, r.stdout[-800:], r.stderr[-800:])


def _state_snapshot(qh: Path) -> dict:
    conn = sqlite3.connect(str(qh / "state.db"))
    conn.row_factory = sqlite3.Row
    try:
        sessions = [dict(row) for row in conn.execute(
            "SELECT id, profile_name, session_key, origin_json FROM sessions ORDER BY id")]
        routing = [dict(row) for row in conn.execute(
            "SELECT scope, session_key FROM gateway_routing ORDER BY session_key")]
        beats = [dict(row) for row in conn.execute(
            "SELECT backend_id, profile FROM gateway_heartbeats ORDER BY backend_id")]
    finally:
        conn.close()
    return {"sessions": sessions, "routing": routing, "heartbeats": beats}


# ---------------------------------------------------------------------------
# `migrate-identity` — real durable rebind of a temp state.db
# ---------------------------------------------------------------------------
def test_migrate_identity_rekeys_a_real_state_db(tmp_path):
    home = tmp_path / "home"
    qh = tmp_path / "qh"
    (qh / "profiles" / _NEW).mkdir(parents=True)
    _seed_state_db(tmp_path, qh)

    before = _state_snapshot(qh)
    assert before["sessions"][0]["profile_name"] == _OLD
    assert before["routing"][0]["session_key"] == f"agent:{_OLD}:chan-1"

    r = _run_agent(tmp_path, ["profile", "migrate-identity", _OLD, _NEW],
                   home=home, qubit_home=qh)
    assert r.returncode == 0, (r.returncode, r.stdout[-400:], r.stderr[-800:])
    assert f"identity migrated: {_OLD}" in r.stdout, r.stdout

    after = _state_snapshot(qh)
    row = after["sessions"][0]
    assert row["profile_name"] == _NEW, row
    assert row["session_key"] == f"agent:{_NEW}:chan-1", row
    assert json.loads(row["origin_json"])["profile"] == _NEW, row
    assert after["routing"][0]["session_key"] == f"agent:{_NEW}:chan-1", after["routing"]
    assert after["heartbeats"][0]["profile"] == _NEW, after["heartbeats"]

    # Idempotent: a second run is a no-op success leaving the rebind in place.
    r2 = _run_agent(tmp_path, ["profile", "migrate-identity", _OLD, _NEW],
                    home=home, qubit_home=qh)
    assert r2.returncode == 0, (r2.returncode, r2.stderr[-800:])
    assert _state_snapshot(qh) == after, "second migrate changed the rebind"


def test_migrate_identity_rekeys_gateway_routing_entry_json(tmp_path):
    """The routing index row's embedded ``entry_json`` is rewritten too, not just its key."""
    home = tmp_path / "home"
    qh = tmp_path / "qh"
    (qh / "profiles" / _NEW).mkdir(parents=True)
    _seed_state_db(tmp_path, qh)

    r = _run_agent(tmp_path, ["profile", "migrate-identity", _OLD, _NEW],
                   home=home, qubit_home=qh)
    assert r.returncode == 0, (r.returncode, r.stderr[-800:])

    conn = sqlite3.connect(str(qh / "state.db"))
    try:
        entry_json, = conn.execute(
            "SELECT entry_json FROM gateway_routing").fetchone()
    finally:
        conn.close()
    payload = json.loads(entry_json)
    assert payload["session_key"] == f"agent:{_NEW}:chan-1", payload
    assert payload["origin"]["profile"] == _NEW, payload


# ---------------------------------------------------------------------------
# `purge-identity` — real durable delete of a deleted profile's identity rows
# ---------------------------------------------------------------------------
def test_purge_identity_removes_identity_rows(tmp_path):
    home = tmp_path / "home"
    qh = tmp_path / "qh"
    (qh / "profiles" / _NEW).mkdir(parents=True)
    _seed_state_db(tmp_path, qh)
    r = _run_agent(tmp_path, ["profile", "migrate-identity", _OLD, _NEW],
                   home=home, qubit_home=qh)
    assert r.returncode == 0, (r.returncode, r.stderr[-800:])
    # The profile directory is gone by the time purge runs (delete already happened).
    for child in (qh / "profiles" / _NEW).iterdir():
        child.unlink()
    (qh / "profiles" / _NEW).rmdir()

    p = _run_agent(tmp_path, ["profile", "purge-identity", _NEW],
                   home=home, qubit_home=qh)
    assert p.returncode == 0, (p.returncode, p.stdout[-400:], p.stderr[-800:])
    assert f"identity purged: {_NEW}" in p.stdout, p.stdout

    after = _state_snapshot(qh)
    assert after["routing"] == [], after["routing"]
    assert after["heartbeats"] == [], after["heartbeats"]
    # Purge settles identity, not history: the session row is intentionally retained.
    assert after["sessions"][0]["profile_name"] == _NEW, after["sessions"]

    # Idempotent: an already-purged profile is a no-op success.
    p2 = _run_agent(tmp_path, ["profile", "purge-identity", _NEW],
                    home=home, qubit_home=qh)
    assert p2.returncode == 0, (p2.returncode, p2.stderr[-800:])
    assert _state_snapshot(qh) == after, "second purge changed the store"


# ---------------------------------------------------------------------------
# The checkpoint store rebind helper (the second missing dep)
# ---------------------------------------------------------------------------
def test_checkpoint_profile_rename_rebinds_project_and_history(tmp_path):
    qh = tmp_path / "qh"
    qh.mkdir(parents=True)
    prog = _CKPT_SETUP_PROGRAM % {"runtime": str(RUNTIME), "home": str(qh),
                                  "old": _OLD, "new": _NEW}
    r = _run_py(tmp_path, prog, qubit_home=qh)
    assert r.returncode == 0, (r.returncode, r.stdout[-800:], r.stderr[-800:])
    out = json.loads(r.stdout.strip().splitlines()[-1])

    assert out["result"] == {"scanned": 1, "migrated": 1, "errors": 0}, out
    assert out["before_tip"] and out["new_tip"] == out["before_tip"], out
    assert out["new_meta"] is True and out["old_meta"] is False, out


def test_checkpoint_profile_rename_empty_store_is_a_noop(tmp_path):
    qh = tmp_path / "qh"
    qh.mkdir(parents=True)
    prog = _EMPTY_STORE_PROGRAM % {
        "runtime": str(RUNTIME),
        "old_dir": str(tmp_path / "profiles" / _OLD),
        "new_dir": str(tmp_path / "profiles" / _NEW),
    }
    r = _run_py(tmp_path, prog, qubit_home=qh)
    assert r.returncode == 0, (r.returncode, r.stdout[-800:], r.stderr[-800:])
    assert "{'scanned': 0, 'migrated': 0, 'errors': 0}" in r.stdout, r.stdout


# ---------------------------------------------------------------------------
# End-to-end: `migrate-identity` drives the checkpoint rebind through the CLI
# ---------------------------------------------------------------------------
def test_migrate_identity_migrates_checkpoint_store_through_the_cli(tmp_path):
    home = tmp_path / "home"
    qh = tmp_path / "qh"
    qh.mkdir(parents=True)
    prog = _CKPT_PREPARE_PROGRAM % {"runtime": str(RUNTIME), "home": str(qh),
                                    "old": _OLD, "new": _NEW}
    setup = _run_py(tmp_path, prog, qubit_home=qh)
    assert setup.returncode == 0, (setup.returncode, setup.stderr[-800:])
    assert "PREPARED" in setup.stdout, setup.stdout

    r = _run_agent(tmp_path, ["profile", "migrate-identity", _OLD, _NEW],
                   home=home, qubit_home=qh)
    assert r.returncode == 0, (r.returncode, r.stdout[-400:], r.stderr[-800:])
    assert f"identity migrated: {_OLD}" in r.stdout, r.stdout
    assert "Checkpoint identity updated: 1 project(s)" in r.stdout, r.stdout


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))

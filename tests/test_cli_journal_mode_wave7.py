"""Packet W4 CLI-port wave 7 — ``sessions set-journal-mode``.

Ports the read-only reference 0.21.4 ``hermes_cli/sessions_cmd_journal_mode.py``
(handler) + its ``hermes_cli/subcommands/sessions.py`` parser block + the
``hermes_cli/sessions_cmd.py`` dispatch entry into the vendored 0.21.1, plus the
missing ``qubit_state_wal._path_on_cross_vm_fs`` (and its mountinfo helpers).

The proof is behavioral and OFFLINE, against TEMP sqlite databases only — never
the live runtime ``state.db``:

* the command parses under the production ``qubit agent`` passthrough (rc 0, no
  ``invalid choice``);
* the REAL conversion flips the on-disk header bytes 18/19 both ways
  (``delete`` <-> ``wal``) and verifies them;
* it is a no-op when the current mode already matches the target;
* a live holder (a real second process) AND a reported holder both produce the
  typed admission refusal, leaving the file unchanged;
* the WAL-reset-bug gate and the cross-VM/WAL refusal are enforced;
* junk files and missing paths refuse with the reference messages.

Host-independent bits (the platform is passed as data) are tested directly; the
host-dependent WAL-reset gate is exercised by pinning the gate's answer so the
test does not depend on the linked SQLite version.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


# -- harness ------------------------------------------------------------------

def _ensure_vendored_path():
    """Put the vendored fork root on ``sys.path`` (repo root stays ahead)."""
    from voicebridge import qubit_runtime  # noqa: PLC0415
    qubit_runtime.ensure_path()


def _journal_modules():
    _ensure_vendored_path()
    import qubit_cli.sessions_cmd_journal_mode as handler  # noqa: PLC0415
    import qubit_state_holders as holders  # noqa: PLC0415
    import qubit_state_wal as wal  # noqa: PLC0415
    return handler, wal, holders


def _header_versions(path: Path) -> tuple:
    """Header bytes 18/19 SQLite writes for the journal mode (1,1 delete / 2,2 wal)."""
    with open(path, "rb") as fh:
        head = fh.read(20)
    assert head[:16] == b"SQLite format 3\x00", "temp DB is not a SQLite file"
    return head[18], head[19]


def _make_db(path: Path, mode: str) -> None:
    """Create a real (tiny) sqlite DB pinned to ``mode`` regardless of host defaults.

    ``PRAGMA journal_mode`` must run with no pending transaction (it is a no-op
    once a write has opened one), so it is issued first, then re-asserted.
    """
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(f"PRAGMA journal_mode={mode.upper()}")
        conn.execute("CREATE TABLE IF NOT EXISTS t (x INTEGER)")
        conn.execute("INSERT INTO t VALUES (1)")
        conn.commit()
        conn.execute(f"PRAGMA journal_mode={mode.upper()}")
        conn.commit()
    finally:
        conn.close()


def _ns(mode: str, db: Path, *, force: bool = False) -> argparse.Namespace:
    return argparse.Namespace(mode=mode, db=str(db), force=force)


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


def _run_agent(tmp_path, argv, *, timeout=180):
    """Run the production ``qubit agent <argv>`` passthrough in a scratch HOME."""
    home = tmp_path / "home"
    qubit_home = home / ".hermes"
    return subprocess.run(
        [sys.executable, "-m", "voicebridge", "agent", *argv],
        cwd=str(REPO_ROOT), env=_sandbox_env(home, qubit_home),
        capture_output=True, text=True, timeout=timeout)


# -- CLI surface (parse under the real ``qubit agent`` passthrough) -----------

def test_command_parses_under_qubit_agent(tmp_path):
    """``sessions set-journal-mode`` is a real choice: rc 0, no ``invalid choice``."""
    r = _run_agent(tmp_path, ["sessions", "set-journal-mode", "--help"])
    assert r.returncode == 0, (r.returncode, r.stderr[-600:])
    combined = r.stdout + r.stderr
    assert "invalid choice" not in combined, combined[-400:]
    assert "usage: qubit sessions set-journal-mode" in r.stdout, r.stdout[:400]
    # Exact reference target set (0.21.4: choices=("delete", "wal")).
    assert "{delete,wal}" in r.stdout, r.stdout[:400]
    assert "--db" in r.stdout and "--force" in r.stdout, r.stdout[:400]


def test_command_rejects_an_unknown_mode(tmp_path):
    """A bogus target is still an argparse error (the choice set is closed)."""
    r = _run_agent(tmp_path, ["sessions", "set-journal-mode", "vacuum"])
    assert r.returncode == 2, (r.returncode, r.stdout[-300:], r.stderr[-300:])
    assert "invalid choice" in (r.stdout + r.stderr)


def test_ported_command_is_registered_in_the_vendored_parser(tmp_path):
    """Registration invariant: ``set-journal-mode`` sits under the ``sessions`` action set."""
    script = (
        "import argparse,json,sys;"
        f"sys.path.insert(0,{str(REPO_ROOT / 'qubit_runtime')!r});"
        "import qubit_cli.main as m;"
        "p,sub=m._build_cli_parser();"
        "sessions=sub.choices['sessions'];"
        "action=next(a for a in sessions._actions if isinstance(a,argparse._SubParsersAction));"
        "j=action.choices['set-journal-mode'];"
        "mode=next(a for a in j._actions if a.dest=='mode');"
        "print(json.dumps({'sessions': sorted(action.choices.keys()), "
        "'modes': sorted(mode.choices)}))"
    )
    home = tmp_path / "home"
    env = _sandbox_env(home, home / ".hermes")
    r = subprocess.run([sys.executable, "-c", script], cwd=str(REPO_ROOT),
                       env=env, capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stderr[-800:]
    tree = json.loads(r.stdout.strip().splitlines()[-1])
    assert "set-journal-mode" in tree["sessions"], tree["sessions"]
    assert tree["modes"] == ["delete", "wal"], tree["modes"]


# -- offline conversion on a TEMP db -----------------------------------------

def test_wal_to_delete_converts_and_verifies_the_header(tmp_path, capsys):
    """The real gap: a WAL store that could not self-downgrade is flipped to DELETE."""
    handler, _wal, _holders = _journal_modules()
    db = tmp_path / "state.db"
    _make_db(db, "wal")
    assert _header_versions(db) == (2, 2)

    rc = handler.cmd_set_journal_mode(_ns("delete", db))

    assert rc == 0, capsys.readouterr().out
    assert _header_versions(db) == (1, 1)
    out = capsys.readouterr().out
    assert "journal_mode wal → delete (header verified)" in out, out


def test_delete_to_wal_converts_and_verifies_the_header(tmp_path, capsys, monkeypatch):
    """The other direction, with the host's WAL-reset gate pinned to a fixed SQLite."""
    handler, wal, _holders = _journal_modules()
    monkeypatch.setattr(wal, "is_sqlite_wal_reset_vulnerable", lambda: False)
    db = tmp_path / "state.db"
    _make_db(db, "delete")
    assert _header_versions(db) == (1, 1)

    rc = handler.cmd_set_journal_mode(_ns("wal", db))

    assert rc == 0, capsys.readouterr().out
    assert _header_versions(db) == (2, 2)
    out = capsys.readouterr().out
    assert "journal_mode delete → wal (header verified)" in out, out


def test_noop_when_current_mode_already_matches(tmp_path, capsys):
    """``current == target`` is a success no-op — nothing is opened or changed."""
    handler, _wal, _holders = _journal_modules()
    db = tmp_path / "state.db"
    _make_db(db, "delete")

    rc = handler.cmd_set_journal_mode(_ns("delete", db))

    assert rc == 0, capsys.readouterr().out
    assert "already journal_mode=delete" in capsys.readouterr().out
    assert _header_versions(db) == (1, 1)


# -- admission refusals -------------------------------------------------------

def test_conversion_refuses_while_a_real_process_holds_the_db(tmp_path, capsys):
    """A live second process holding the file is detected and blocks the switch."""
    import time

    handler, _wal, holders = _journal_modules()
    db = tmp_path / "state.db"
    _make_db(db, "wal")

    child = subprocess.Popen(
        [sys.executable, "-c",
         "import sqlite3,sys,time; c=sqlite3.connect(sys.argv[1]);"
         "c.execute('select count(*) from sqlite_master').fetchone(); time.sleep(60)", str(db)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        # Wait until the real holder scan actually sees the child's open fd.
        deadline = time.time() + 10
        while time.time() < deadline and not holders.foreign_state_db_holders(db):
            time.sleep(0.1)
        assert any(pid == child.pid for pid, _p in holders.foreign_state_db_holders(db)), \
            "holder process never showed as an open-fd holder"

        rc = handler.cmd_set_journal_mode(_ns("delete", db))
        out = capsys.readouterr().out
        assert rc == 1, out
        assert "other processes hold it open" in out, out
        assert str(child.pid) in out, (child.pid, out)
    finally:
        child.terminate()
        child.wait(timeout=10)

    # Nothing was switched under the live holder.
    assert _header_versions(db) == (2, 2)


def test_conversion_refuses_when_the_holder_scan_reports_a_holder(tmp_path, capsys, monkeypatch):
    """The reported-holder branch (also the shape the Windows scan cannot produce)."""
    handler, _wal, holders = _journal_modules()
    db = tmp_path / "state.db"
    _make_db(db, "wal")
    monkeypatch.setattr(holders, "foreign_state_db_holders", lambda _p: [(4321, "/x/state.db")])

    rc = handler.cmd_set_journal_mode(_ns("delete", db))

    out = capsys.readouterr().out
    assert rc == 1, out
    assert "other processes hold it open" in out, out
    assert "pid 4321" in out, out
    assert _header_versions(db) == (2, 2)


def test_wal_target_refused_when_linked_sqlite_has_the_wal_reset_bug(tmp_path, capsys, monkeypatch):
    """Target WAL refuses on the vulnerable linked SQLite before any conversion."""
    handler, wal, _holders = _journal_modules()
    monkeypatch.setattr(wal, "is_sqlite_wal_reset_vulnerable", lambda: True)
    db = tmp_path / "state.db"
    _make_db(db, "delete")

    rc = handler.cmd_set_journal_mode(_ns("wal", db))

    out = capsys.readouterr().out
    assert rc == 1, out
    assert "WAL-reset bug" in out, out
    assert _header_versions(db) == (1, 1)


def test_wal_target_refused_on_cross_vm_filesystem(tmp_path, capsys, monkeypatch):
    """virtiofs/9p mounts refuse WAL before the pragma (silent -shm corruption)."""
    handler, wal, _holders = _journal_modules()
    monkeypatch.setattr(wal, "is_sqlite_wal_reset_vulnerable", lambda: False)
    monkeypatch.setattr(wal, "_path_on_cross_vm_fs", lambda _p: True)
    db = tmp_path / "state.db"
    _make_db(db, "delete")

    rc = handler.cmd_set_journal_mode(_ns("wal", db))

    out = capsys.readouterr().out
    assert rc == 1, out
    assert "cross-VM filesystems" in out, out
    assert _header_versions(db) == (1, 1)


def test_junk_file_is_not_a_convertible_sqlite_store(tmp_path, capsys):
    """A non-SQLite file refuses with the header diagnostic."""
    handler, _wal, _holders = _journal_modules()
    junk = tmp_path / "junk.bin"
    junk.write_bytes(b"not a database at all" * 4)

    rc = handler.cmd_set_journal_mode(_ns("delete", junk))

    out = capsys.readouterr().out
    assert rc == 1, out
    assert "not a Hermes SQLite store" in out, out


def test_missing_database_reports_nothing_to_convert(tmp_path, capsys):
    handler, _wal, _holders = _journal_modules()

    rc = handler.cmd_set_journal_mode(_ns("delete", tmp_path / "absent.db"))

    out = capsys.readouterr().out
    assert rc == 1, out
    assert "nothing to convert" in out, out


def test_windows_force_gate_is_pure_host_independent_policy():
    """``_refusal`` takes the platform as data: win32 needs ``--force``; others do not."""
    handler, _wal, _holders = _journal_modules()

    assert handler._refusal("delete", "delete", "win32", on_cross_vm_fs=False, force=False)
    assert handler._refusal("wal", "delete", "win32", on_cross_vm_fs=False, force=False)
    assert handler._refusal("delete", "delete", "win32", on_cross_vm_fs=False, force=True) is None
    assert handler._refusal("delete", "delete", "linux", on_cross_vm_fs=False, force=False) is None
    # Cross-VM only ever gates the WAL target.
    assert handler._refusal("wal", "delete", "linux", on_cross_vm_fs=True, force=False)
    assert handler._refusal("delete", "wal", "linux", on_cross_vm_fs=True, force=False) is None
    # A non-database header refuses regardless of platform/force.
    assert handler._refusal("delete", "not-a-database", "linux", on_cross_vm_fs=False, force=True)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))

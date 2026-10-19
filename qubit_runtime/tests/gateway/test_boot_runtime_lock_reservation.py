"""The gateway boot claims its own reservation — it must not flock ``gateway.lock`` a second time.

``run_bootstrap.start_gateway`` reserves every served profile home through
``gateway.runtime_ownership.process_ownership`` (taking ``<home>/gateway.lock``) and only then claims
the PID file, which calls ``gateway.status.acquire_gateway_runtime_lock``. On POSIX a flock belongs to
the open file description, not the process, so a second descriptor flocked against an inode this
process already holds is refused: the claim read as a foreign owner and every boot aborted with
"Gateway runtime lock is already held by another instance" at its own reservation (repr: the
counterfactual — release the reservation first, and the same flock succeeds).

Pinned here:

* the exact boot sequence (reserve the launch home, then claim the PID file) succeeds;
* ownership is reported truthfully afterwards, and releasing gives the home back;
* a home reserved by ANOTHER process is still refused — the exclusion is not weakened;
* the claim writes no record of its own over the reservation's.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """Isolated QUBIT_HOME; its ``gateway.lock`` lives beside ``gateway.pid`` in it."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("HERMES_GATEWAY_LOCK_DIR", raising=False)
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)
    from gateway import runtime_ownership
    # A fresh reservation: the suite shares one process, so the module-level one may hold other homes.
    monkeypatch.setattr(runtime_ownership, "process_ownership", runtime_ownership.ProfileOwnership())
    return tmp_path / "home"


def _reserve(home):
    from gateway.runtime_ownership import process_ownership
    process_ownership.reserve([home])


def test_claim_succeeds_after_this_process_reserved_the_home(home):
    """The exact boot sequence: reserve the profile home, then claim the PID file."""
    from gateway.status import acquire_gateway_runtime_lock, owns_gateway_runtime_lock

    _reserve(home)  # run_bootstrap._start_gateway_reserve_profiles
    assert acquire_gateway_runtime_lock() is True, (
        "the PID-file claim re-flocked a lock this process already holds")
    assert owns_gateway_runtime_lock() is True
    assert (home / "gateway.lock").exists()


def test_claim_leaves_the_reservation_record_intact(home):
    """Ownership is the reservation; the claim must not overwrite what discovery reads."""
    from gateway.status import acquire_gateway_runtime_lock

    _reserve(home)
    before = json.loads((home / "gateway.lock").read_text())
    assert acquire_gateway_runtime_lock() is True
    assert json.loads((home / "gateway.lock").read_text()) == before


def test_release_gives_the_home_back(home):
    from gateway.runtime_ownership import process_ownership
    from gateway.status import (acquire_gateway_runtime_lock, owns_gateway_runtime_lock,
                                release_gateway_runtime_lock)

    _reserve(home)
    assert acquire_gateway_runtime_lock() is True
    release_gateway_runtime_lock()
    assert owns_gateway_runtime_lock() is False
    assert process_ownership.owns(home) is False
    _reserve(home)  # another owner can take it now


def test_claim_still_refuses_a_home_another_process_holds(home):
    """The exclusion is unchanged: a foreign reservation is not adoptable."""
    probe = textwrap.dedent(
        """
        import sys, time
        from pathlib import Path
        from gateway.runtime_ownership import process_ownership
        process_ownership.reserve([Path(sys.argv[1])])
        print("reserved", flush=True)
        time.sleep(60)
        """
    )
    root = Path(__file__).resolve().parents[2]
    child = subprocess.Popen([sys.executable, "-c", probe, str(home)],
                             cwd=str(root), env={**os.environ, "PYTHONPATH": str(root)},
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "reserved"
        from gateway.status import acquire_gateway_runtime_lock
        assert acquire_gateway_runtime_lock() is False
    finally:
        child.kill()
        child.wait(timeout=10)

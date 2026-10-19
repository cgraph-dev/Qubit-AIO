"""``entry.main()`` claims its launch home before serving.

``_claim_launch_home`` reserves the home's ``gateway.lock`` exactly as the gateway does, so a
second owner on the same home is refused instead of racing it on ``state.db``. Nothing called
this before; these tests pin the call site, the refusal and the release.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from gateway.runtime_ownership import process_ownership
from tui_gateway import entry


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Point the module-cached launch home at a disposable directory."""
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path))
    monkeypatch.setattr(entry.server, "_qubit_home", tmp_path, raising=False)
    return tmp_path


def test_main_serves_a_free_home_and_releases_it(home, monkeypatch):
    calls = []
    monkeypatch.setattr(entry, "_serve", lambda: calls.append("serve"))

    entry.main()

    assert calls == ["serve"]
    assert entry.server._launch_home() == home
    assert not process_ownership.owns(home), "main()'s finally must release the reservation"


def test_main_refuses_a_home_owned_by_another_process(home, monkeypatch):
    """A live gateway's reservation on the same home refuses this entry, and _serve never runs.

    The owner is a SEPARATE process: POSIX ``flock`` is owned by the open file description, so a
    second reservation from this process would not conflict.
    """
    holder = subprocess.Popen(
        [sys.executable, "scripts/_probe_laneA_lock_holder.py", str(home)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "READY"
        calls = []
        monkeypatch.setattr(entry, "_serve", lambda: calls.append("serve"))
        with pytest.raises(SystemExit) as exc:
            entry.main()
        assert exc.value.code == 1
        assert calls == [], "_serve must not run when the home is owned elsewhere"
    finally:
        holder.stdin.close()
        holder.wait(timeout=30)


@pytest.mark.parametrize("flag", ["HERMES_TUI_DASHBOARD", "QUBIT_TUI_DASHBOARD"])
def test_dashboard_terminal_serves_beside_a_live_gateway(home, monkeypatch, flag):
    """The dashboard spawns one stdio TUI per /chat terminal while its messaging gateway owns
    the same canonical home. That terminal must serve beside the gateway instead of exiting
    (otherwise /chat is dead whenever the gateway is up, and the gateway can never restart
    while a chat is open), and it must not take the exclusive runtime lock itself."""
    monkeypatch.setenv(flag, "1")
    holder = subprocess.Popen(
        [sys.executable, "scripts/_probe_laneA_lock_holder.py", str(home)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "READY"
        calls = []
        monkeypatch.setattr(entry, "_serve", lambda: calls.append("serve"))

        entry.main()

        assert calls == ["serve"]
        assert not process_ownership.owns(home), "a dashboard terminal must not claim the home"
    finally:
        holder.stdin.close()
        holder.wait(timeout=30)

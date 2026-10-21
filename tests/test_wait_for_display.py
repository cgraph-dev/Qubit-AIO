"""Hermetic tests for ``scripts/wait_for_display.py`` (orb boot-race waiter).

The waiter is what stops ``qubit-voice-orb.service`` from exiting with
``ORB_NO_DISPLAY`` when it starts a moment before the X socket exists. These
tests drive the real script as a subprocess with a fake ``xdpyinfo`` injected
through ``PATH`` — no X server, no real display, no service, no sleeps longer
than a fraction of a second.

Contract pinned here:

* ready immediately -> one probe, exit 0;
* ready after N polls -> N probes, exit 0, no busy loop;
* no ``DISPLAY`` -> headless: wait out the bound, exit 0 (never a failure);
* ``DISPLAY`` set but unreachable -> keep polling to the bound, exit 0.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "wait_for_display.py"


def _fake_probe(tmp_path: Path):
    """A fake ``xdpyinfo`` that succeeds on the Nth call (N=FAKE_READY_AFTER)."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    counter = tmp_path / "calls"
    probe = bindir / "xdpyinfo"
    probe.write_text(
        "#!/bin/sh\n"
        "n=0\n"
        f'if [ -f "{counter}" ]; then read -r n < "{counter}" || true; fi\n'
        "n=$((n + 1))\n"
        f'printf "%s" "$n" > "{counter}"\n'
        '[ "$n" -ge "${FAKE_READY_AFTER:-1}" ]\n',
        encoding="utf-8",
    )
    probe.chmod(probe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return bindir, counter


def _env(tmp_path: Path, *, display: str = ":0", ready_after: int = 1) -> dict:
    bindir, _ = _fake_probe(tmp_path)
    env = os.environ.copy()
    env.pop("XAUTHORITY", None)
    env["PATH"] = str(bindir)
    env["DISPLAY"] = display
    env["FAKE_READY_AFTER"] = str(ready_after)
    return env


def _run(env: dict, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, timeout=10, env=env,
    )


def _record(proc: subprocess.CompletedProcess) -> dict:
    return json.loads(proc.stderr.strip().splitlines()[-1])


def test_ready_immediately_exits_zero_after_one_probe(tmp_path):
    env = _env(tmp_path, ready_after=1)
    proc = _run(env, "--timeout", "5", "--interval", "0.05")
    assert proc.returncode == 0, proc.stderr
    rec = _record(proc)
    assert rec["event"] == "orb.display_wait"
    assert rec["ready"] is True
    assert rec["attempts"] == 1
    assert rec["elapsed_s"] < 5
    assert (tmp_path / "calls").read_text() == "1"


def test_waits_until_display_becomes_ready(tmp_path):
    env = _env(tmp_path, ready_after=3)
    proc = _run(env, "--timeout", "5", "--interval", "0.02")
    assert proc.returncode == 0, proc.stderr
    rec = _record(proc)
    assert rec["ready"] is True
    assert rec["attempts"] == 3
    assert rec["elapsed_s"] < 5
    assert (tmp_path / "calls").read_text() == "3"


def test_no_display_is_headless_and_times_out_with_zero(tmp_path):
    env = _env(tmp_path)
    env.pop("DISPLAY", None)
    proc = _run(env, "--timeout", "0.2", "--interval", "0.05")
    assert proc.returncode == 0, proc.stderr
    rec = _record(proc)
    assert rec["ready"] is False
    assert rec["elapsed_s"] < 3
    # No DISPLAY means there is nothing to probe; the waiter still bounds the
    # wait and returns success so the orb can exit gracefully by itself.
    assert not (tmp_path / "calls").exists()


def test_unreachable_display_polls_then_times_out_with_zero(tmp_path):
    env = _env(tmp_path, display=":99", ready_after=999)
    proc = _run(env, "--timeout", "0.25", "--interval", "0.05")
    assert proc.returncode == 0, proc.stderr
    rec = _record(proc)
    assert rec["ready"] is False
    assert rec["attempts"] >= 2
    assert rec["elapsed_s"] < 3
    assert int((tmp_path / "calls").read_text()) >= 2

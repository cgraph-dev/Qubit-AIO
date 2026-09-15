"""QBPV2 wave-2 W5 (role H): launcher repair regression tests.

Hermetic guards for the user-facing ``~/.local/bin/qubit`` shim and its
installer (``scripts/install.sh``). They fail if the launcher regresses to a
stale/foreign checkout, if ``--launcher-only`` stops working, or if a
divergent operator shim is silently clobbered. No operator HOME/XDG is
touched: everything renders into a throwaway ``XDG_BIN_HOME``/``HOME``.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
INSTALL_SH = REPO / "scripts" / "install.sh"
VENV_QUBIT = REPO / ".venv" / "bin" / "qubit"

# The exact stale targets the launcher must never point at again.
STALE_MARKERS = ("Qubit (2)", "voice-dashboard", "open.sh")

pytestmark = pytest.mark.skipif(
    not VENV_QUBIT.exists(),
    reason="launcher render requires the checkout console script .venv/bin/qubit",
)


def _render(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = dict(os.environ)
    env["XDG_BIN_HOME"] = str(bin_dir)
    env["HOME"] = str(home)
    r = subprocess.run(["bash", str(INSTALL_SH), "--launcher-only"],
                       capture_output=True, text=True, env=env, cwd=str(REPO))
    assert r.returncode == 0, r.stderr
    target = bin_dir / "qubit"
    assert target.is_file(), r.stdout + r.stderr
    return target


def test_install_sh_launcher_has_no_stale_targets():
    text = INSTALL_SH.read_text(encoding="utf-8")
    for marker in STALE_MARKERS:
        assert marker not in text, f"stale launcher target in install.sh: {marker}"
    # the installer exposes the repair mode and delegates to the console script
    assert "--launcher-only" in text
    assert "QUBIT_REPO" in text
    assert "__QUBIT_REPO__" in text
    assert ".venv/bin/qubit" in text
    # a normal install wires the launcher in (opt-out only via env)
    assert "install_launcher" in text
    assert "QUBIT_NO_LAUNCHER" in text


def test_rendered_launcher_points_at_the_authoritative_checkout(tmp_path):
    target = _render(tmp_path)
    text = target.read_text(encoding="utf-8")
    # the install-time placeholder is fully resolved to THIS checkout
    assert "__QUBIT_REPO__" not in text
    assert str(REPO) in text
    for marker in STALE_MARKERS:
        assert marker not in text, f"rendered launcher still references {marker}"
    assert os.access(target, os.X_OK)


def test_launcher_executes_the_authoritative_installation(tmp_path):
    target = _render(tmp_path)
    # A clean PATH with no `.venv/bin` entry proves the baked authoritative
    # checkout (not PATH discovery) is what actually runs.
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path / "home")}
    r = subprocess.run([str(target), "--help"], capture_output=True,
                       text=True, env=env, timeout=120)
    assert r.returncode == 0, r.stderr
    assert "usage: qubit" in r.stdout


def test_launcher_repairs_itself_idempotently_and_backs_up_divergence(tmp_path):
    target = _render(tmp_path)
    good = target.read_bytes()
    # second render: byte-identical, and no spurious backup
    again = _render(tmp_path)
    assert again.read_bytes() == good
    assert not list(target.parent.glob("qubit.bak.*"))

    # a divergent operator shim is preserved, never silently clobbered
    target.write_text("#!/bin/sh\necho stale-shim\n", encoding="utf-8")
    _render(tmp_path)
    backups = sorted(target.parent.glob("qubit.bak.*"))
    assert backups, "divergent shim was not backed up"
    assert "echo stale-shim" in backups[0].read_text(encoding="utf-8")
    assert target.read_bytes() == good


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))

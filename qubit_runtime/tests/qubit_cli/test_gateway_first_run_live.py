"""First-run guidance on the real CLI without a configured executor.

Deliberate fork divergence from the pin: an interactive launch may hand a blank install to the
TUI's in-app "Setup Required" screen, but a headless (no TTY) launch has no such screen, so bare
``qubit``, ``qubit chat`` and ``qubit -z`` all reach the classic non-interactive setup guard and
exit 1 with canonical ``qubit`` guidance before any session, gateway or state is created.
"""
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("argv", [[], ["chat"], ["-z", "hello"]])
def test_headless_first_run_exits_with_setup_guidance_before_gateway(tmp_path, argv):
    home = tmp_path / "state"
    home.mkdir(mode=0o700)
    user = tmp_path / "user"
    user.mkdir()
    # An empty PATH keeps ambient host installs (notably `gh`, whose token makes Copilot look
    # configured) out of provider discovery: this test is about a genuinely blank install.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    root = Path(__file__).resolve().parents[2]
    env = {k: os.environ[k] for k in ("LANG", "TZ") if k in os.environ}
    env.update(HOME=str(user), USERPROFILE=str(user), HERMES_HOME=str(home), QUBIT_HOME=str(home),
               PATH=str(bin_dir), PYTHONPATH=str(root), PYTHONUNBUFFERED="1")
    result = subprocess.run(
        [sys.executable, "-m", "qubit_cli.main", *argv], cwd=tmp_path, env=env,
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=40, check=False
    )
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "qubit config set model.provider custom" in result.stdout
    assert "Run setup now?" not in result.stdout
    assert "Session:" not in result.stderr
    assert not (home / "gateway.pid").exists()
    assert not (home / "state.db").exists()

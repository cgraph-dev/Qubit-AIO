"""QBPV2 wave-2 W5 (role H): lifecycle rehearsal tests.

Runs the real product lifecycle scripts hermetically (throwaway HOME/XDG under
``/tmp``) and asserts they pass typed, preserving user state. No operator
HOME/config/state, live service, or network is touched.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
INSTALL_SH = REPO / "scripts" / "install.sh"
MIGRATION = REPO / "scripts" / "migration_rehearsal.sh"
ROLLBACK = REPO / "scripts" / "rollback_drill.py"
VENV_PY = REPO / ".venv" / "bin" / "python"


def test_install_sh_is_syntax_clean_and_typed():
    assert subprocess.run(["bash", "-n", str(INSTALL_SH)],
                          capture_output=True).returncode == 0
    text = INSTALL_SH.read_text(encoding="utf-8")
    assert "install_launcher" in text
    assert "DASHBOARD_UNAVAILABLE" in text


@pytest.mark.skipif(not VENV_PY.exists(),
                    reason="lifecycle rehearsal needs the checkout .venv python")
def test_migration_rehearsal_passes_migration_upgrade_rollback_uninstall():
    work = tempfile.mkdtemp(prefix="qbpv2-mig-", dir="/tmp")
    env = dict(os.environ)
    env["TMPDIR"] = work
    env["PYTHON"] = str(VENV_PY)
    r = subprocess.run(["bash", str(MIGRATION)], capture_output=True,
                       text=True, env=env, cwd=str(REPO), timeout=300)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out[-2000:]
    assert "migration rehearsal: PASS" in r.stdout
    # every stage really ran and state was preserved byte-for-byte
    for stage in ("1. migration", "2. upgrade", "3. rollback", "4. uninstall"):
        assert stage in r.stdout, stage
    assert "credentials excluded from the backup" in r.stdout


@pytest.mark.skipif(not VENV_PY.exists(),
                    reason="rollback drill needs the checkout .venv python")
def test_rollback_drill_flips_and_restores_every_flag():
    work = tempfile.mkdtemp(prefix="qbpv2-rb-", dir="/tmp")
    home = Path(work) / "home"
    home.mkdir()
    env = dict(os.environ)
    env.update({
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_DATA_HOME": str(home / ".local/share"),
        "XDG_STATE_HOME": str(home / ".local/state"),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "PYTHONPATH": "",
    })
    r = subprocess.run([str(VENV_PY), str(ROLLBACK), "--json"],
                       capture_output=True, text=True, env=env,
                       cwd=str(REPO), timeout=300)
    assert r.returncode == 0, (r.stdout + r.stderr)[-2000:]
    report = json.loads(r.stdout)
    assert report["all_ok"] is True
    assert report["user_data_preserved"] is True
    assert report["flags"], "no feature flags exercised"
    # every flag must be restored to its documented rollback value
    for flag in report["flags"]:
        assert flag["ok"] is True, flag


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))

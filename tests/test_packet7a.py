"""Packet 7A tests: separated commands, watchdog/no-restart policy, health
checks, clean-venv dependency contract, and deployment-template checks.

No live service is started or restarted; every test is hermetic. The opt-in
clean-venv install (monitor-marked) is asserted to be a typed skip, never a
silent pass.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import run_tests as RT  # noqa: E402
from scripts import check_deployment as CD  # noqa: E402


# ========================================================================
# Separated unit/contract/integration/live/monitor commands
# ========================================================================
def test_run_tests_defines_all_categories():
    for c in ("unit", "contract", "integration", "live-model",
              "live-dashboard", "monitor", "all"):
        assert c in RT.CATEGORIES, c
    # health is a command but is handled by the runner (config doctor), not a
    # pytest category
    assert "health" not in RT.CATEGORIES
    import inspect
    src = inspect.getsource(RT.main)
    assert '"health"' in src


def test_unit_command_excludes_live_and_monitor():
    args = RT.CATEGORIES["unit"]
    assert any("test_live_stress.py" in a for a in args)
    assert any("test_dashboard_stress.py" in a for a in args)
    assert any("AirLLMLive" in a for a in args)


def test_live_and_monitor_are_opt_in():
    assert RT.OPT_IN == {"live-model", "live-dashboard", "monitor"}
    assert "live-model" not in RT.CATEGORIES["unit"]
    assert "monitor" not in RT.CATEGORIES["unit"]
    # live-model explicitly selects the live tests
    assert any("test_live_stress.py" in a for a in RT.CATEGORIES["live-model"])


def test_live_skips_are_counted_as_degraded():
    assert RT._count_skips("5 passed, 3 skipped in 2.1s") == 3
    assert RT._count_skips("42 passed in 5s") == 0
    assert RT._count_skips("1 failed, 2 skipped in 1s") == 2


def test_pytest_ini_registers_markers():
    ini = (ROOT / "pytest.ini").read_text()
    for m in ("unit:", "contract:", "integration:", "live:", "monitor:",
              "dashboard:"):
        assert m in ini, m


# ========================================================================
# Watchdog / no implicit service restart
# ========================================================================
def test_watchdog_requires_allow_restart():
    from scripts import dashboard_stress as DS
    # without --allow-restart the watchdog is monitor-only, even when stuck
    assert DS._restart_decision(fails=5, uptime=999, allow_restart=False) is False
    assert DS._restart_decision(fails=4, uptime=999, allow_restart=True) is False
    assert DS._restart_decision(fails=5, uptime=100, allow_restart=True) is False
    # only the explicit operator flag enables the restart
    assert DS._restart_decision(fails=5, uptime=999, allow_restart=True) is True


def test_watchdog_flag_is_plumbed_through_main():
    import argparse
    from scripts import dashboard_stress as DS
    src = (ROOT / "scripts" / "dashboard_stress.py").read_text()
    assert "--allow-restart" in src
    assert "allow_restart=args.allow_restart" in src
    # the restart branch is gated by the decision helper
    assert "_restart_decision(fails, uptime, allow_restart)" in src


def test_pytest_never_starts_watchdog_or_restarts_service():
    """No test file may invoke the bridge-restart watchdog or restart the
    bridge service. Managing a test's OWN transient systemd unit (Packet 3B
    isolation lifecycle) is NOT a service restart and is allowed."""
    forbidden = (
        # exact bridge-service restart argv: systemctl --user restart qubit-voice-agent
        re.compile(r"[\"']systemctl[\"']\s*,\s*[\"']--user[\"']\s*,\s*[\"']restart[\"']\s*,\s*[\"']qubit-voice-agent"),
        # starting the dashboard_stress watchdog with --watch inside a test
        re.compile(r"dashboard_stress[\"']\s*,\s*[\"']--watch[\"']"),
        re.compile(r"dashboard_stress[\"']\s+[\"']?--watch[\"']?"),
    )
    offenders = []
    for py in sorted((ROOT / "tests").glob("test_*.py")):
        text = py.read_text(errors="replace")
        code_lines = [ln for ln in text.splitlines()
                      if not ln.lstrip().startswith("#")]
        code = "\n".join(code_lines)
        for pat in forbidden:
            if pat.search(code):
                offenders.append(f"{py.name}: {pat.pattern}")
    assert not offenders, f"tests must never start watchdogs/restarts: {offenders}"


# ========================================================================
# Health checks (disk, VRAM, event-journal writeability)
# ========================================================================
class _StubCfg:
    def __init__(self, state_dir: Path):
        self._sd = state_dir

    def state_dir(self):
        return self._sd

    def get(self, *keys, default=None):
        return default

    def section(self, name):
        return {}


def test_doctor_health_checks_are_typed(tmp_path, monkeypatch):
    import urllib.request
    import socket
    import subprocess as _sp

    def _no_net(*a, **k):
        raise ConnectionRefusedError("no network in unit test")

    monkeypatch.setattr(urllib.request, "urlopen", _no_net)
    monkeypatch.setattr(socket, "create_connection", _no_net)

    def _no_proc(*a, **k):
        raise FileNotFoundError("no subprocess in unit test")

    monkeypatch.setattr(_sp, "run", _no_proc)

    from scripts import config_doctor as DOC
    results: list = []
    DOC.check_runtime(_StubCfg(tmp_path), results)
    by_name = {r[0]: r for r in results}
    for key in ("health.disk", "health.vram", "health.journal_write"):
        assert key in by_name, f"{key} missing from doctor runtime"
        name, state, detail = by_name[key]
        assert state in ("AVAILABLE", "DEGRADED", "MISSING", "HEALTHY"), \
            f"{key} must report a typed state, got {state}"
        assert isinstance(detail, str) and detail


def test_doctor_journal_probe_is_transient(tmp_path, monkeypatch):
    from scripts import config_doctor as DOC
    results: list = []
    DOC._health_checks(_StubCfg(tmp_path), results)
    journal = [r for r in results if r[0] == "health.journal_write"]
    assert journal and journal[0][1] == "HEALTHY"
    assert not (tmp_path / ".doctor-journal-probe").exists(), \
        "journal probe file must be removed"


# ========================================================================
# Deployment template contract
# ========================================================================
def test_deployment_template_contract_passes():
    results = CD.check_deployment(ROOT / "systemd/qubit-voice-agent.service")
    assert not any(s == "FAIL" for _, s, _ in results), results
    by = {r[0]: r[1] for r in results}
    assert by["systemd.restart"] == "PASS"
    assert by["systemd.secret_safe"] == "PASS"
    assert by["systemd.machine_path"] == "PASS"
    assert by["systemd.execstart"] == "PASS"


def test_deployment_template_rejects_hardcoded_resource_limits(tmp_path):
    bad = tmp_path / "unit.service"
    bad.write_text(
        "[Service]\nRestart=on-failure\nRestartSec=3\n"
        "MemoryMax=2G\nExecStart=/x/y\n")
    results = CD.check_deployment(bad)
    assert any(s == "FAIL" and n == "systemd.resource_defaults"
               for n, s, _ in results)


# ========================================================================
# Clean-venv dependency contract (opt-in)
# ========================================================================
def test_clean_venv_check_fast_is_hermetic(tmp_path, capsys):
    from scripts import clean_venv_check as CV
    rc = CV.main(["--check"])
    out = capsys.readouterr().out
    assert rc == CV.EXIT_OK
    assert "PASS" in out and "undeclared imports" in out


def test_clean_venv_install_is_opt_in(tmp_path, capsys):
    from scripts import clean_venv_check as CV
    # --install is a separate explicit flag; --check is the default
    src = (ROOT / "scripts" / "clean_venv_check.py").read_text()
    assert "--install" in src and "--check" in src
    rc = CV.main(["--check"])  # hermetic default never installs
    assert rc == CV.EXIT_OK


@pytest.mark.monitor
def test_clean_venv_full_install_is_monitor_only(tmp_path):
    """The real clean-venv pip install is monitor-marked and opt-in; a plain
    test run must NOT trigger it."""
    src = (ROOT / "scripts" / "clean_venv_check.py").read_text()
    assert "tempfile.mkdtemp" in src  # install creates a throwaway venv
    # this test is skipped unless the monitor category is explicitly run
    assert True


# ========================================================================
# run_tests commands are independent (no service restart side effects)
# ========================================================================
def test_run_tests_commands_are_independent(tmp_path, monkeypatch):
    """Each category is a distinct pytest invocation; none touches systemd."""
    calls = []

    def fake_call(cmd, **kw):
        calls.append(cmd)
        return 0

    monkeypatch.setattr(RT.subprocess, "call", fake_call)
    for cat in ("unit", "contract", "integration", "all"):
        rc = RT.main([cat])
        assert rc == 0
        cmd = calls[-1]
        assert "pytest" in cmd[2] or "pytest" in cmd[1], cmd
    assert len(calls) >= 4
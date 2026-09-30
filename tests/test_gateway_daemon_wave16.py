"""Owner Decision #16 — the Qubit-owned messaging-gateway daemon is real.

Covers the launch boundary (``voicebridge.gateway_daemon``), the shipped systemd
unit, the installer wrapper, and the supervisor's daemon-unit selector + honest
status merge. Everything is hermetic: the daemon loop is never started, no host
unit is installed, and no service is touched. ``--check`` is exercised in a
subprocess only (it imports the fork's spec, not the loop).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

UNIT_NAME = "qubit-gateway.service"
UNIT_PATH = REPO_ROOT / "systemd" / UNIT_NAME
INSTALLER = REPO_ROOT / "scripts" / "install_gateway_unit.sh"


class _Cfg:
    """Minimal config double: nested ``get`` + a real state dir."""

    def __init__(self, data, state):
        self._d = data or {}
        self._state = Path(state)

    def get(self, *path, default=None):
        node = self._d
        for key in path:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    def state_dir(self):
        return self._state


def _cfg(tmp_path, gateway=None, webui=None):
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    return _Cfg({"gateway": gateway or {"system_scope": "user"},
                 "webui": webui or {"host": "127.0.0.1", "port": 8890}}, state)


# --------------------------------------------------------------------------- unit

def test_unit_renders_and_is_brand_clean():
    text = UNIT_PATH.read_text(encoding="utf-8")
    assert UNIT_PATH.exists()
    # No legacy brand token on a shipped product surface (legal aside).
    for token in ("hermes", "Hermes", "Nous", "openclaw", "OpenClaw"):
        assert token not in text, token
    assert "__PROJECT_DIR__/.venv/bin/python -m voicebridge.gateway_daemon" in text
    assert "ExecReload=/bin/kill -USR1 $MAINPID" in text
    assert "KillMode=mixed" in text
    assert "Restart=always" in text
    assert "WantedBy=default.target" in text


def test_unit_pins_a_qubit_owned_home():
    text = UNIT_PATH.read_text(encoding="utf-8")
    assert "Environment=QUBIT_HOME=%h/.local/state/qubit-voicebridge/runtime" in text
    # The read-only reference home is never named.
    assert "/.hermes" not in text


def test_installer_wrapper_uses_the_gateway_unit_name(tmp_path):
    text = INSTALLER.read_text(encoding="utf-8")
    assert 'UNIT_NAME="qubit-gateway.service"' in text
    assert "install_systemd_unit.sh" in text

    # Executing it with a TEMP unit dir renders the unit there (no host touch).
    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    env = dict(os.environ)
    env["SYSTEMD_UNIT_DIR"] = str(unit_dir)
    env["HOME"] = str(tmp_path / "home")
    (tmp_path / "home").mkdir()
    r = subprocess.run(["sh", str(INSTALLER), str(REPO_ROOT)],
                       capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 0, r.stderr
    installed = unit_dir / UNIT_NAME
    assert installed.exists(), r.stdout
    rendered = installed.read_text(encoding="utf-8")
    assert str(REPO_ROOT) in rendered        # __PROJECT_DIR__ substituted
    assert "__PROJECT_DIR__" not in rendered


# --------------------------------------------------------------- preflight check

def test_check_mode_is_read_only_and_ok(tmp_path, monkeypatch):
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "qhome"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    from voicebridge import gateway_daemon as GD

    report = GD.preflight()
    assert report["state"] == "OK", report
    assert report["importable"] is True
    assert report["service_unit"] == UNIT_NAME
    root = Path(report["runtime_home"]).resolve()
    assert root != (Path.home() / ".hermes").resolve()
    assert root == (tmp_path / "qhome").resolve()


def test_check_subprocess_never_starts_the_loop(tmp_path):
    env = dict(os.environ)
    env["QUBIT_HOME"] = str(tmp_path / "qhome")
    env["XDG_STATE_HOME"] = str(tmp_path / "xdg")
    env.pop("QUBIT_GATEWAY_DAEMON", None)
    r = subprocess.run(
        [sys.executable, "-m", "voicebridge.gateway_daemon",
         "--check", "--json"],
        capture_output=True, text=True, env=env, cwd=str(REPO_ROOT), timeout=90)
    assert r.returncode == 0, (r.returncode, r.stderr[-500:])
    import json

    report = json.loads(r.stdout)
    assert report["state"] == "OK" and report["mode"] == "check"
    # A --check run must not claim a PID/lock/socket.
    home = Path(report["runtime_home"])
    assert not (home / "gateway.pid").exists()
    assert not (home / "gateway_state.json").exists()
    assert not (home / "gateway.sock").exists()


# ----------------------------------------------------------- daemon-unit selector

def test_daemon_service_unit_default_and_overrides(tmp_path):
    from voicebridge import gateway_supervisor as GS

    assert GS.daemon_service_unit(_cfg(tmp_path)) == "qubit-gateway.service"
    # An explicit daemon key wins.
    assert GS.daemon_service_unit(
        _cfg(tmp_path, gateway={"daemon_service_unit": "custom-gw.service"})
    ) == "custom-gw.service"
    # The web-UI unit named by config is NOT used as the daemon unit.
    assert GS.daemon_service_unit(
        _cfg(tmp_path, gateway={"service_unit": "qubit-voicebridge.service"})
    ) == "qubit-gateway.service"
    # An operator's custom service_unit (not the web-UI name) IS honored.
    assert GS.daemon_service_unit(
        _cfg(tmp_path, gateway={"service_unit": "my-gateway.service"})
    ) == "my-gateway.service"


# ------------------------------------------------------ honest status merge

def test_status_merges_daemon_runtime_and_control_socket(tmp_path):
    from voicebridge import gateway_supervisor as GS

    def show(app):
        def _r(argv, t):
            return (0, f"ActiveState={'active' if app else 'inactive'}\n"
                       f"SubState={'running' if app else 'dead'}\n"
                       f"MainPID={4242 if app else 0}\nNRestarts=0\n"
                       f"ExecMainStatus=0\n", "")
        return _r

    daemon = {"gateway_state": "running", "exit_reason": None, "pid": 99,
              "updated_at": "2026-09-23T00:00:00+00:00", "platforms": 2,
              "connected_platforms": 1, "active_agents": 3}
    control = {"gateway_state": "running", "pid": 4242}

    st = GS.status(_cfg(tmp_path), runner=show(True), probe=lambda h, p: True,
                   daemon_reader=lambda: daemon, control_query=lambda: control)
    assert st["service_unit"] == "qubit-gateway.service"
    assert st["daemon"] == daemon and st["control"] == control
    assert st["daemon_live"] is True and st["daemon_pid"] == 4242
    assert st["daemon_state"] == "running"


def test_status_reports_absent_daemon_as_absent_never_fabricated(tmp_path):
    from voicebridge import gateway_supervisor as GS

    st = GS.status(
        _cfg(tmp_path),
        runner=lambda argv, t: (0, "ActiveState=inactive\nSubState=dead\n"
                                   "MainPID=0\nNRestarts=0\n"
                                   "ExecMainStatus=0\n", ""),
        probe=lambda h, p: False, daemon_reader=lambda: None,
        control_query=lambda: None)
    assert st["daemon"] is None and st["control"] is None
    assert st["daemon_live"] is False and st["daemon_pid"] is None


def test_status_daemon_reader_failure_never_raises(tmp_path):
    from voicebridge import gateway_supervisor as GS

    def boom():
        raise RuntimeError("disk gone")

    st = GS.status(_cfg(tmp_path),
                   runner=lambda argv, t: (0, "ActiveState=inactive\n", ""),
                   probe=lambda h, p: False, daemon_reader=boom,
                   control_query=boom)
    assert st["daemon"] is None and st["control"] is None


def test_daemon_runtime_status_reads_a_real_state_file(tmp_path, monkeypatch):
    from voicebridge import gateway_supervisor as GS

    home = tmp_path / "qhome"
    home.mkdir()
    (home / "gateway_state.json").write_text(
        '{"kind": "hermes-gateway", "pid": 4242, "gateway_state": "running",'
        ' "exit_reason": null, "active_agents": 2,'
        ' "platforms": {"telegram": {"state": "connected"},'
        ' "slack": {"state": "error"}}}', encoding="utf-8")
    monkeypatch.setenv("QUBIT_HOME", str(home))
    rec = GS.daemon_runtime_status()
    assert rec["gateway_state"] == "running" and rec["pid"] == 4242
    assert rec["platforms"] == 2 and rec["connected_platforms"] == 1
    assert rec["active_agents"] == 2


def test_status_reports_web_ui_unit_separately(tmp_path):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, gateway={"service_unit": "qubit-voicebridge.service"})
    code, body = HC.dispatch(cfg, "GET", "/api/gateway", {}, {})
    assert code == 200 and body["ok"] is True, (code, body)
    assert body["process"]["service_unit"] == "qubit-gateway.service"
    assert body["web_ui_service_unit"] == "qubit-voicebridge.service"
    # The web-UI unit is named read-only; no fabricated running claim.
    assert "web_ui_running" not in body


# --------------------------------------------------------------- control target

def test_control_dispatches_to_the_daemon_unit(tmp_path):
    from voicebridge import gateway_supervisor as GS

    spawned = []
    out = GS.control(_cfg(tmp_path, gateway={"service_unit": "qubit-voicebridge.service"}),
                     "restart", confirmed=True,
                     runner=lambda argv, t: (0, "ActiveState=active\n"
                                                "SubState=running\nMainPID=1\n"
                                                "NRestarts=0\nExecMainStatus=0\n", ""),
                     probe=lambda h, p: True,
                     spawner=lambda argv: spawned.append(list(argv)))
    assert out["ok"] is True and out["state"] == "RESTARTING", out
    detached = " ".join(map(str, spawned[0]))
    assert "restart qubit-gateway.service" in detached, detached

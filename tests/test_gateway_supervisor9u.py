"""Objective C — real gateway/service supervision (unit-level).

Drives voicebridge.gateway_supervisor with injected runner/spawner/probe so the
status parsing, idempotency, confirmation, e-stop, and detached restart are all
deterministic and touch no host process.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(autouse=True)
def _estop_clear(monkeypatch):
    from voicebridge import gateway_supervisor as GS

    monkeypatch.setattr(GS, "_estop", lambda _cfg: False)


class _Cfg:
    def __init__(self, data=None):
        self._d = data or {}

    def get(self, *path, default=None):
        node = self._d
        for key in path:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node


def _show(active, pid=4242):
    return (0,
            f"ActiveState={'active' if active else 'inactive'}\n"
            f"SubState={'running' if active else 'dead'}\n"
            f"MainPID={pid if active else 0}\n"
            f"ExecMainStartTimestamp=Mon 2026-09-21\n",
            "")


def test_status_reads_systemd_and_port():
    from voicebridge import gateway_supervisor as GS

    # Owner Decision #16: the gateway supervisor reports the messaging-gateway
    # DAEMON unit (default qubit-gateway.service), not the web-UI unit named by
    # gateway.service_unit.
    cfg = _Cfg({"gateway": {"service_unit": "qubit-voicebridge.service"},
                "webui": {"host": "127.0.0.1", "port": 8890}})
    st = GS.status(cfg, runner=lambda argv, t: _show(True),
                   probe=lambda h, p: True,
                   daemon_reader=lambda: None, control_query=lambda: None)
    assert st["available"] is True
    assert st["running"] is True
    assert st["pid"] == 4242
    assert st["port_in_use"] is True
    assert st["service_unit"] == "qubit-gateway.service"
    assert GS.service_unit(cfg) == "qubit-voicebridge.service"


def test_start_is_idempotent_and_real_when_inactive():
    from voicebridge import gateway_supervisor as GS

    cfg = _Cfg({"gateway": {"service_unit": "u.service"}})
    calls = []

    def runner(argv, t):
        calls.append(list(argv))
        if "show" in argv:
            return _show(False)
        if "start" in argv:
            return (0, "", "")
        return _show(False)

    out = GS.control(cfg, "start", confirmed=True, runner=runner,
                     probe=lambda h, p: False)
    assert out["ok"] is True and out["state"] == "OK"
    assert any("start" in c for c in calls)


def test_start_on_active_service_is_a_noop():
    from voicebridge import gateway_supervisor as GS

    cfg = _Cfg({"gateway": {"service_unit": "u.service"}})
    out = GS.control(cfg, "start", confirmed=True,
                     runner=lambda argv, t: _show(True),
                     probe=lambda h, p: True)
    assert out["ok"] is True and out.get("noop") is True


def test_restart_is_detached():
    from voicebridge import gateway_supervisor as GS

    cfg = _Cfg({"gateway": {"service_unit": "u.service"}})
    spawned = []
    out = GS.control(cfg, "restart", confirmed=True,
                     runner=lambda argv, t: _show(True),
                     probe=lambda h, p: True,
                     spawner=lambda argv: spawned.append(list(argv)))
    assert out["ok"] is True and out["state"] == "RESTARTING"
    assert spawned and "restart" in spawned[0][-1]
    assert "u.service" in spawned[0][-1]


def test_control_requires_confirmation_and_respects_estop(monkeypatch):
    from voicebridge import gateway_supervisor as GS

    cfg = _Cfg({"gateway": {"service_unit": "u.service"}})
    out = GS.control(cfg, "restart", confirmed=False,
                     runner=lambda argv, t: _show(True))
    assert out["code"] == GS.CONFIRM_CODE and out["state"] == "CONFIRM_REQUIRED"

    monkeypatch.setattr(GS, "_estop", lambda _cfg: True)
    out = GS.control(cfg, "restart", confirmed=True,
                     runner=lambda argv, t: _show(True))
    assert out["code"] == GS.ESTOP_CODE


def test_real_control_is_disabled_under_test_guard(monkeypatch):
    from voicebridge import gateway_supervisor as GS

    cfg = _Cfg({"gateway": {"service_unit": "u.service"}})
    monkeypatch.setenv("QUBIT_GATEWAY_SUPERVISION_DISABLED", "1")
    out = GS.control(cfg, "restart", confirmed=True)
    assert out["code"] == GS.UNAVAILABLE_CODE


def test_profile_control_refuses_foreign_unit_and_targets_own_unit(tmp_path, monkeypatch):
    from voicebridge import gateway_supervisor as GS

    root = tmp_path / "listen"
    home = root / "profiles" / "audit"
    home.mkdir(parents=True)
    monkeypatch.setenv("QUBIT_HOME", str(root))
    monkeypatch.setattr(GS, "daemon_runtime_status", lambda *_: None)
    monkeypatch.setattr(GS, "query_daemon_control", lambda *_: None)

    cfg = _Cfg()
    unit = GS.profile_service_unit(home)
    assert unit == "qubit-gateway-audit.service"
    calls = []
    unit_home = root

    def runner(argv, _timeout):
        calls.append(argv)
        code, stdout, stderr = _show(True)
        return code, stdout + f"Environment=QUBIT_HOME={unit_home}\n", stderr

    spawned = []
    result = GS.control(cfg, "restart", confirmed=True, runner=runner, spawner=spawned.append,
                        unit_override=unit, home=home)
    assert result["code"] == GS.UNAVAILABLE_CODE and spawned == []
    assert len(calls) == 1 and unit in calls[0]
    unit_home = home
    result = GS.control(cfg, "restart", confirmed=True, runner=runner, spawner=spawned.append,
                        unit_override=unit, home=home)
    assert result["state"] == "RESTARTING"
    assert len(spawned) == 1 and unit in spawned[0][-1]
    assert not GS.control_status(cfg, "restart")
    assert GS.control_status(cfg, "restart", home=home)["before"]["pid"] == 4242


def test_detached_gateway_restart_requires_an_observed_transition(monkeypatch):
    from voicebridge import gateway_supervisor as GS
    from voicebridge import qubit_compat as HC

    before = {"pid": 42, "active_state": "active", "started_at": "first"}
    now = 1000.0
    record = {"state": "RESTARTING", "before": before, "updated_at": now - 20}
    live = dict(before)
    monkeypatch.setattr(GS, "control_status", lambda *_: record)
    monkeypatch.setattr(GS, "status", lambda *_: live)
    class Clock:
        @staticmethod
        def time():
            return now
    monkeypatch.setattr(HC, "time", Clock())

    _, status = HC._supervision_action_status(_Cfg(), "gateway-restart")
    assert status["running"] and status["exit_code"] is None
    live["pid"] = 43
    _, status = HC._supervision_action_status(_Cfg(), "gateway-restart")
    assert not status["running"] and status["exit_code"] == 0
    live.clear()
    now += GS.CONTROL_OUTCOME_TIMEOUT_S
    _, status = HC._supervision_action_status(_Cfg(), "gateway-restart")
    assert status["exit_code"] == 1 and "GATEWAY_OUTCOME_UNVERIFIED" in "\n".join(status["lines"])

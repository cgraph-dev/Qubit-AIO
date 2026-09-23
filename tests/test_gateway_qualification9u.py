"""Isolated qualification — Qubit gateway/service supervision (step 8).

This file exercises the *whole* required gateway matrix against an isolated
environment: a fake ``systemctl`` first on ``PATH`` (so no host service is ever
consulted) plus injected ``runner``/``spawner``/``probe`` callables for the
unit-level cases. The fail-closed guard is lifted only inside this process for
the dispatch cases.

Nothing here starts, stops, restarts, or even calls the real
``qubit-voicebridge.service`` unit; the fake ``systemctl`` only records argv and
flips a temp state file.

Covered:
  status active / inactive / unknown / not-installed; stale-process and
  active-without-listener detection; start on inactive; idempotent start/stop;
  detached stop/restart; rc!=0 failure (typed + redacted); timeout (returned
  124 and raised); confirmation gate; e-stop refusal; audit emission; recovery
  after a failed start; the partially-injected safety regression; "never
  raises" on a raising runner; and the ``qubit_compat.dispatch`` integration
  route (start/stop/restart/confirm/failure/unavailable).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# The web-UI/voice unit named by config; Owner Decision #16 makes the
# messaging-gateway DAEMON unit the target of `qubit gateway` control.
UNIT = "qubit-voicebridge.service"
DAEMON_UNIT = "qubit-gateway.service"

_FAKE_SYSTEMCTL = r"""#!/bin/sh
printf '%s\n' "$*" >> "$QUBIT_FAKE_SYSTEMCTL_LOG"
for a in "$@"; do case "$a" in --*) ;; *) cmd="$a"; break;; esac; done
STATE="$QUBIT_FAKE_SYSTEMCTL_STATE"
MODE="$(cat "$QUBIT_FAKE_SYSTEMCTL_MODE" 2>/dev/null || printf ok)"
if [ -z "$cmd" ]; then exit 0; fi
case "$cmd" in
  show)
    case "$MODE" in
      missing)
        printf 'Unit qubit-voicebridge.service could not be found.\n' >&2
        exit 1;;
      unparsable)
        printf 'ActiveState=\nSubState=\n'
        exit 0;;
    esac
    if [ "$(cat "$STATE" 2>/dev/null)" = active ]; then
      printf 'ActiveState=active\nSubState=running\nMainPID=4242\n'
      printf 'ExecMainStartTimestamp=Mon 2026-09-21 00:00:00 UTC\n'
    else
      printf 'ActiveState=inactive\nSubState=dead\nMainPID=0\n'
      printf 'ExecMainStartTimestamp=\n'
    fi;;
  start|restart|stop)
    if [ "$MODE" = missing ]; then
      printf 'Unit qubit-voicebridge.service could not be found.\n' >&2
      exit 1
    fi
    if [ "$MODE" = fail-start ] && [ "$cmd" = start ]; then
      printf 'Job failed: token=supersecretvalue123 url=https://x-access-token:ghp_ABCdef0123456789ABCdef@github.com/o/r.git\n' >&2
      exit 1
    fi
    if [ "$cmd" = stop ]; then
      printf 'inactive\n' > "$STATE"
    else
      printf 'active\n' > "$STATE"
    fi;;
esac
exit 0
"""


class _Cfg:
    """Nested-dict config implementing the ``.get(*path, default=)`` API."""

    def __init__(self, data=None):
        self._d = data or {}

    def get(self, *path, default=None):
        node = self._d
        for key in path:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node


def _show(active=True, pid=4242):
    return (0,
            f"ActiveState={'active' if active else 'inactive'}\n"
            f"SubState={'running' if active else 'dead'}\n"
            f"MainPID={pid if active else 0}\n"
            "ExecMainStartTimestamp=Mon 2026-09-21 00:00:00 UTC\n",
            "")


@pytest.fixture(autouse=True)
def fake_systemctl(tmp_path, monkeypatch):
    """Put an executable fake ``systemctl`` first on PATH for every case."""
    bindir = tmp_path / "fakebin"
    bindir.mkdir()
    log = tmp_path / "systemctl.log"
    log.touch()
    state = tmp_path / "systemctl.state"
    state.write_text("inactive\n")
    mode = tmp_path / "systemctl.mode"
    mode.write_text("ok")
    script = bindir / "systemctl"
    script.write_text(_FAKE_SYSTEMCTL)
    script.chmod(0o755)
    monkeypatch.setenv("QUBIT_FAKE_SYSTEMCTL_LOG", str(log))
    monkeypatch.setenv("QUBIT_FAKE_SYSTEMCTL_STATE", str(state))
    monkeypatch.setenv("QUBIT_FAKE_SYSTEMCTL_MODE", str(mode))
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ["PATH"])
    assert Path(shutil.which("systemctl")).resolve() == script.resolve()
    return SimpleNamespace(log=log, state=state, mode=mode,
                           set=lambda m: mode.write_text(m))


@pytest.fixture
def estop_clear(monkeypatch):
    from voicebridge import gateway_supervisor as GS

    monkeypatch.setattr(GS, "_estop", lambda _cfg: False)


def _cmds(log):
    return {next(t for t in line.split() if not t.startswith("-"))
            for line in log.read_text().splitlines() if line.strip()}


# ---------------------------------------------------------------------------
# status: active / inactive / unknown / not-installed + stale/port conflict
# ---------------------------------------------------------------------------
def test_status_active_reports_running_pid_and_port(estop_clear):
    from voicebridge import gateway_supervisor as GS

    cfg = _Cfg({"gateway": {"service_unit": UNIT}})
    st = GS.status(cfg, runner=lambda a, t: _show(True),
                   probe=lambda h, p: True)
    assert st["available"] is True
    assert st["active_state"] == "active" and st["running"] is True
    assert st["pid"] == 4242 and st["port_in_use"] is True
    assert st["service_unit"] == DAEMON_UNIT


def test_status_inactive_reports_not_running(estop_clear):
    from voicebridge import gateway_supervisor as GS

    st = GS.status(_Cfg({}), runner=lambda a, t: _show(False),
                   probe=lambda h, p: False)
    assert st["active_state"] == "inactive" and st["running"] is False
    assert st["pid"] is None and st["state"] == "OK"


def test_status_unknown_when_systemctl_output_is_unparsable(estop_clear):
    from voicebridge import gateway_supervisor as GS

    st = GS.status(_Cfg({}), runner=lambda a, t: (0, "", ""),
                   probe=lambda h, p: False)
    assert st["active_state"] == "unknown"
    assert st["running"] is False


def test_status_not_installed_is_typed_not_a_crash(estop_clear):
    from voicebridge import gateway_supervisor as GS

    st = GS.status(_Cfg({}), runner=lambda a, t: (
        1, "", "Unit qubit-voicebridge.service could not be found.\n"),
        probe=lambda h, p: False)
    assert st["state"] == "NOT_INSTALLED"
    assert st["running"] is False


def test_status_flags_stale_process_port_conflict(estop_clear):
    from voicebridge import gateway_supervisor as GS

    st = GS.status(_Cfg({}), runner=lambda a, t: _show(False),
                   probe=lambda h, p: True)
    assert st["port_in_use"] is True and st["running"] is False
    assert "port is in use but the service is not active" in st["detail"]


def test_status_flags_active_without_listener(estop_clear):
    from voicebridge import gateway_supervisor as GS

    st = GS.status(_Cfg({}), runner=lambda a, t: _show(True),
                   probe=lambda h, p: False)
    assert st["running"] is True and st["port_in_use"] is False
    assert "service active but the configured port is not accepting" in st["detail"]


def test_status_never_raises_on_a_raising_runner(estop_clear):
    from voicebridge import gateway_supervisor as GS

    def raiser(argv, timeout):
        raise RuntimeError("boom")

    st = GS.status(_Cfg({}), runner=raiser, probe=lambda h, p: False)
    assert st["running"] is False
    assert st["state"] in ("DEGRADED", "NOT_INSTALLED")
    assert "boom" in st["detail"]


# ---------------------------------------------------------------------------
# start / stop / restart
# ---------------------------------------------------------------------------
def test_start_on_inactive_runs_start(estop_clear):
    from voicebridge import gateway_supervisor as GS

    calls = []

    def runner(argv, t):
        calls.append(list(argv))
        return _show("active" in argv or "start" in argv,
                     pid=99 if "start" in argv else 0)

    out = GS.control(_Cfg({}), "start", confirmed=True, runner=runner,
                     probe=lambda h, p: False)
    assert out["ok"] is True and out["state"] == "OK"
    assert any("start" in c for c in calls), calls


def test_start_on_active_is_idempotent_noop(estop_clear):
    from voicebridge import gateway_supervisor as GS

    calls = []

    def runner(argv, t):
        calls.append(list(argv))
        return _show(True)

    out = GS.control(_Cfg({}), "start", confirmed=True, runner=runner,
                     probe=lambda h, p: True)
    assert out["ok"] is True and out.get("noop") is True
    assert not any("start" in c for c in calls), calls


def test_stop_on_inactive_is_idempotent_noop(estop_clear):
    from voicebridge import gateway_supervisor as GS

    out = GS.control(_Cfg({}), "stop", confirmed=True,
                     runner=lambda a, t: _show(False),
                     probe=lambda h, p: False)
    assert out["ok"] is True and out.get("noop") is True


def test_stop_on_active_is_detached(estop_clear):
    from voicebridge import gateway_supervisor as GS

    spawned = []
    out = GS.control(_Cfg({}), "stop", confirmed=True,
                     runner=lambda a, t: _show(True),
                     probe=lambda h, p: True,
                     spawner=lambda argv: spawned.append(list(argv)))
    assert out["ok"] is True and out["state"] == "STOPPING"
    assert out["detached"] is True
    assert len(spawned) == 1 and "stop" in spawned[0][-1]
    assert DAEMON_UNIT in spawned[0][-1]


def test_restart_is_detached_and_returns_restarting(estop_clear):
    from voicebridge import gateway_supervisor as GS

    spawned = []
    out = GS.control(_Cfg({}), "restart", confirmed=True,
                     runner=lambda a, t: _show(True),
                     probe=lambda h, p: True,
                     spawner=lambda argv: spawned.append(list(argv)))
    assert out["ok"] is True and out["state"] == "RESTARTING"
    assert out["detached"] is True
    detached = " ".join(map(str, spawned[0]))
    assert len(spawned) == 1 and "restart" in detached and DAEMON_UNIT in detached
    assert "sleep" in detached


# ---------------------------------------------------------------------------
# failure / timeout: typed + redacted
# ---------------------------------------------------------------------------
def test_start_failure_is_typed_and_redacted(estop_clear):
    from voicebridge import gateway_supervisor as GS

    def runner(argv, t):
        if "start" in argv:
            return (1, "", "Job failed: token=supersecretvalue123")
        return _show(False)

    out = GS.control(_Cfg({}), "start", confirmed=True, runner=runner,
                     probe=lambda h, p: False)
    assert out["ok"] is False and out["state"] == "FAILED"
    assert out["code"] == GS.FAILED_CODE
    assert "supersecretvalue123" not in json.dumps(out)
    assert "[REDACTED]" in out["detail"]


def test_start_timeout_returning_124_is_typed(estop_clear):
    from voicebridge import gateway_supervisor as GS

    def runner(argv, t):
        if "start" in argv:
            return (124, "", "command timed out")
        return _show(False)

    out = GS.control(_Cfg({}), "start", confirmed=True, runner=runner,
                     probe=lambda h, p: False)
    assert out["ok"] is False and out["state"] == "TIMEOUT"
    assert out["code"] == GS.TIMEOUT_CODE


def test_start_timeout_raised_by_runner_is_typed(estop_clear):
    from voicebridge import gateway_supervisor as GS

    def runner(argv, t):
        if "start" in argv:
            raise subprocess.TimeoutExpired(list(argv), t)
        return _show(False)

    out = GS.control(_Cfg({}), "start", confirmed=True, runner=runner,
                     probe=lambda h, p: False)
    assert out["ok"] is False and out["code"] == GS.TIMEOUT_CODE


def test_raised_non_timeout_exception_is_typed_failure(estop_clear):
    from voicebridge import gateway_supervisor as GS

    def runner(argv, t):
        if "start" in argv:
            raise RuntimeError("kaboom")
        return _show(False)

    out = GS.control(_Cfg({}), "start", confirmed=True, runner=runner,
                     probe=lambda h, p: False)
    assert out["ok"] is False and out["code"] == GS.FAILED_CODE
    assert "kaboom" in out["detail"]


# ---------------------------------------------------------------------------
# confirmation / e-stop / audit / recovery
# ---------------------------------------------------------------------------
def test_confirmation_is_required(estop_clear):
    from voicebridge import gateway_supervisor as GS

    out = GS.control(_Cfg({}), "restart", confirmed=False,
                     runner=lambda a, t: _show(True))
    assert out["code"] == GS.CONFIRM_CODE and out["state"] == "CONFIRM_REQUIRED"


def test_estop_refuses_control(monkeypatch):
    from voicebridge import gateway_supervisor as GS

    monkeypatch.setattr(GS, "_estop", lambda _cfg: True)
    out = GS.control(_Cfg({}), "start", confirmed=True,
                     runner=lambda a, t: _show(False))
    assert out["code"] == GS.ESTOP_CODE and out["ok"] is False


def test_audit_event_is_emitted_on_start(estop_clear, monkeypatch):
    from voicebridge import gateway_supervisor as GS
    from voicebridge import logging_setup

    events = []
    monkeypatch.setattr(logging_setup, "log_event",
                        lambda event, **fields: events.append((event, fields)))
    out = GS.control(_Cfg({}), "start", confirmed=True,
                     runner=lambda a, t: _show(False),
                     probe=lambda h, p: False)
    assert out["ok"] is True
    assert any(e.startswith("gateway.supervisor.start") for e, _ in events), events


def test_recovery_failed_start_leaves_typed_state_and_retry_succeeds(estop_clear):
    from voicebridge import gateway_supervisor as GS

    state = {"active": False}
    failures = {"n": 1}

    def runner(argv, t):
        if "start" in argv:
            if failures["n"]:
                failures["n"] -= 1
                return (1, "", "start failed: transient")
            state["active"] = True
            return (0, "", "")
        return _show(state["active"], pid=7 if state["active"] else 0)

    cfg = _Cfg({})
    first = GS.control(cfg, "start", confirmed=True, runner=runner,
                       probe=lambda h, p: state["active"])
    assert first["ok"] is False and first["code"] == GS.FAILED_CODE
    assert first["status"]["running"] is False

    second = GS.control(cfg, "start", confirmed=True, runner=runner,
                        probe=lambda h, p: state["active"])
    assert second["ok"] is True and second["state"] == "OK"
    assert second["status"]["running"] is True


def test_partially_injected_call_cannot_reach_real_defaults(estop_clear,
                                                            monkeypatch):
    """Regression: injecting only a runner must not let restart use the real
    detached spawner. The guard must cover the executor each action uses."""
    from voicebridge import gateway_supervisor as GS

    monkeypatch.setattr(GS, "_real_control_disabled", lambda: True)
    spawned = []
    monkeypatch.setattr(GS, "_default_spawner",
                        lambda argv: spawned.append(list(argv)))

    out = GS.control(_Cfg({}), "restart", confirmed=True,
                     runner=lambda a, t: _show(True),
                     probe=lambda h, p: True)
    assert out["code"] == GS.UNAVAILABLE_CODE, out
    assert spawned == [], spawned


def test_uninjected_start_under_guard_cannot_reach_real_runner(estop_clear,
                                                               monkeypatch):
    from voicebridge import gateway_supervisor as GS

    monkeypatch.setattr(GS, "_real_control_disabled", lambda: True)
    executed = []
    monkeypatch.setattr(GS, "_default_runner",
                        lambda argv, t: executed.append(list(argv)) or _show(False))

    out = GS.control(_Cfg({}), "start", confirmed=True)
    assert out["code"] == GS.UNAVAILABLE_CODE, out
    assert executed == [], executed


# ---------------------------------------------------------------------------
# integration through the real qubit_compat.dispatch route
# ---------------------------------------------------------------------------
def _cfg_file(tmp_path, monkeypatch):
    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME"):
        target = tmp_path / var.lower()
        target.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(var, str(target))
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("QUBIT_HOME", str(home / ".hermes"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\nwebui:\n  token: t\n"
                 "  host: 127.0.0.1\n  port: 48999\n"
                 f"gateway:\n  service_unit: {UNIT}\n  system_scope: user\n")
    from voicebridge.config import Config

    return Config.load(p)


def _wire(monkeypatch, *, spawned=None):
    """Lift the guard only inside this process; record the detached spawner."""
    from voicebridge import gateway_supervisor as GS

    monkeypatch.setattr(GS, "_real_control_disabled", lambda: False)
    if spawned is not None:
        monkeypatch.setattr(GS, "_default_spawner",
                            lambda argv: spawned.append(list(argv)))


def _dispatch(cfg, method, path, body=None):
    from voicebridge import qubit_compat as HC

    return HC.dispatch(cfg, method, path, {}, body or {})


def test_dispatch_get_gateway_reports_real_process_state(tmp_path, monkeypatch,
                                                         fake_systemctl):
    _wire(monkeypatch)
    cfg = _cfg_file(tmp_path, monkeypatch)
    status, body = _dispatch(cfg, "GET", "/api/gateway")
    assert status == 200 and body["ok"] is True, (status, body)
    assert body["supervision"] == "", body["supervision"]
    assert body["process"]["available"] is True
    assert body["process"]["service_unit"] == DAEMON_UNIT


def test_dispatch_start_stop_restart_and_confirm(tmp_path, monkeypatch,
                                                 fake_systemctl):
    spawned = []
    _wire(monkeypatch, spawned=spawned)
    cfg = _cfg_file(tmp_path, monkeypatch)

    status, body = _dispatch(cfg, "POST", "/api/gateway/start")
    assert status == 200 and body["state"] == "OK", (status, body)
    assert fake_systemctl.state.read_text().strip() == "active"

    status, body = _dispatch(cfg, "POST", "/api/gateway/start")
    assert status == 200 and body.get("noop") is True, (status, body)

    status, body = _dispatch(cfg, "POST", "/api/gateway/restart")
    assert status == 200 and body["state"] == "RESTARTING", (status, body)
    assert body["detached"] is True and len(spawned) == 1

    status, body = _dispatch(cfg, "POST", "/api/gateway/stop",
                             {"confirm": False})
    assert status == 409 and body["code"] == "GATEWAY_CONFIRM_REQUIRED"


def test_dispatch_maps_not_installed_and_failure(tmp_path, monkeypatch,
                                                 fake_systemctl):
    _wire(monkeypatch)
    cfg = _cfg_file(tmp_path, monkeypatch)

    fake_systemctl.set("missing")
    status, body = _dispatch(cfg, "GET", "/api/gateway")
    assert status == 200 and body["process"]["state"] == "NOT_INSTALLED", body

    fake_systemctl.set("fail-start")
    status, body = _dispatch(cfg, "POST", "/api/gateway/start")
    assert status == 500 and body["code"] == "GATEWAY_CONTROL_FAILED", body
    assert "supersecretvalue123" not in json.dumps(body)
    assert "[REDACTED]" in body["detail"], body["detail"]


def test_dispatch_returns_typed_unavailable_without_systemctl(tmp_path,
                                                              monkeypatch):
    _wire(monkeypatch)
    empty = tmp_path / "empty-path"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    cfg = _cfg_file(tmp_path, monkeypatch)
    status, body = _dispatch(cfg, "GET", "/api/gateway")
    assert status == 200 and body["ok"] is True
    assert body["supervision"].startswith("GATEWAY_SUPERVISION_UNAVAILABLE"), body

    status, body = _dispatch(cfg, "POST", "/api/gateway/start")
    assert status == 503 and body["code"] == "GATEWAY_SUPERVISION_UNAVAILABLE"

"""Gate 9u — Qubit-owned gateway control runs through the ONE authority path.

Owner Decision #10: the Qubit-owned gateway (the ``qubit-voicebridge.service``
host process) gets real status/control/recover behavior, and every lifecycle
transition is authorized by the fail-closed e-stop latch + the shared durable
confirmation store + a PolicyEngine Decision + an audit receipt — never a bare
``confirm`` boolean.

All systemd/probe execution is injected, so these tests mutate nothing.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

UNIT = "qubit-voicebridge.service"


@pytest.fixture(autouse=True)
def _estop_clear(monkeypatch):
    """Default: the e-stop latch is clear. Individual tests override."""
    from voicebridge import gateway_supervisor as GS

    monkeypatch.setattr(GS, "_estop", lambda _cfg: False)


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


def _cfg(tmp_path):
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    return _Cfg({"gateway": {"service_unit": UNIT, "system_scope": "user"},
                 "webui": {"host": "127.0.0.1", "port": 8890}}, state)


def _show(*, active=False, failed=False, n_restarts=0, exec_main_status=0,
          pid=4242):
    if failed:
        active_state, sub, main_pid = "failed", "failed", 0
    elif active:
        active_state, sub, main_pid = "active", "running", pid
    else:
        active_state, sub, main_pid = "inactive", "dead", 0
    return (0,
            f"ActiveState={active_state}\nSubState={sub}\nMainPID={main_pid}\n"
            f"ExecMainStartTimestamp=Mon 2026-09-21\n"
            f"ActiveEnterTimestamp=\n"
            f"NRestarts={n_restarts}\nExecMainStatus={exec_main_status}\n",
            "")


# --- status(): failure fields + bounded health ------------------------------
def test_status_surfaces_failure_counters_and_typed_failed(tmp_path):
    from voicebridge import gateway_supervisor as GS

    st = GS.status(_cfg(tmp_path),
                   runner=lambda argv, t: _show(failed=True, n_restarts=3,
                                                exec_main_status=1),
                   probe=lambda h, p: False)
    assert st["n_restarts"] == 3, st
    assert st["exec_main_status"] == 1, st
    assert st["failed"] is True and st["state"] == "FAILED", st
    assert st["failure"] == {"exec_main_status": 1, "n_restarts": 3,
                             "active_state": "failed"}, st["failure"]


def test_status_healthy_when_active_and_port_answers(tmp_path):
    from voicebridge import gateway_supervisor as GS

    st = GS.status(_cfg(tmp_path), runner=lambda argv, t: _show(active=True),
                   probe=lambda h, p: True)
    assert st["running"] is True and st["port_in_use"] is True, st
    assert st["healthy"] is True and st["health"] == "OK", st
    assert st["exec_main_status"] == 0 and st["n_restarts"] == 0, st


# --- (a) control without a valid confirmation is refused, executes nothing --
def test_control_without_confirmation_is_typed_refusal_and_no_execution(
        tmp_path):
    from voicebridge import gateway_supervisor as GS

    calls = []
    out = GS.control(_cfg(tmp_path), "start", confirmed=False,
                     runner=lambda argv, t: calls.append(list(argv)) or (0, "", ""),
                     probe=lambda h, p: False)
    assert out["ok"] is False, out
    assert out["code"] == GS.CONFIRM_CODE, out
    assert out["state"] == "CONFIRM_REQUIRED", out
    assert calls == [], "refused control must not touch systemctl"


def test_control_with_invalid_confirmation_id_is_refused(tmp_path):
    from voicebridge import gateway_supervisor as GS

    calls = []
    out = GS.control(_cfg(tmp_path), "restart", confirmation_id="not-a-real-id",
                     runner=lambda argv, t: calls.append(list(argv)) or _show(active=True),
                     probe=lambda h, p: True)
    assert out["ok"] is False, out
    assert out["code"] == GS.CONFIRM_CODE, out
    assert calls == [], "an invalid confirmation must not reach systemctl"


# --- (b) control with confirmation executes through the authority chain -----
def test_control_with_confirmation_executes_and_audits(tmp_path, monkeypatch):
    from voicebridge import gateway_supervisor as GS

    audits = []
    monkeypatch.setattr(GS, "_audit",
                        lambda op, actor, detail: audits.append((op, dict(detail))))
    calls = []

    def runner(argv, t):
        calls.append(list(argv))
        if "show" in argv:
            return _show(active=False)
        if "start" in argv:
            return (0, "", "")
        return _show(active=False)

    out = GS.control(_cfg(tmp_path), "start", confirmed=True, source="user",
                     actor="dashboard:user", runner=runner,
                     probe=lambda h, p: False)
    assert out["ok"] is True and out["state"] == "OK", out
    assert any("start" in c for c in calls), calls
    assert out["receipt"]["decision"] == "ALLOWED", out.get("receipt")
    assert out["receipt"]["decision_id"], out.get("receipt")
    assert any(op == "control.authorize" for op, _ in audits), audits


def test_control_uses_the_shared_durable_confirmation_store(tmp_path):
    from voicebridge import gateway_supervisor as GS

    cfg = _cfg(tmp_path)
    engine = GS._control_engine(cfg)
    # The broker's journal-backed store is the one writable confirmation
    # authority; keying the engine on it proves control is not a bare bool.
    assert hasattr(engine.confirmations, "issue_ephemeral"), engine.confirmations
    assert GS._control_engine(cfg) is engine, "engine must be cached per state"


# --- (c) e-stop latched -> typed refusal ------------------------------------
def test_control_refused_when_estop_latched(tmp_path, monkeypatch):
    from voicebridge import gateway_supervisor as GS

    monkeypatch.setattr(GS, "_estop", lambda _cfg: True)
    calls = []
    out = GS.control(_cfg(tmp_path), "restart", confirmed=True,
                     runner=lambda argv, t: calls.append(list(argv)) or _show(active=True),
                     probe=lambda h, p: True)
    assert out["ok"] is False, out
    assert out["code"] == GS.ESTOP_CODE, out
    assert calls == [], "e-stop must refuse before any systemctl call"


# --- (d) recover() resets a failed unit then starts it ----------------------
def test_recover_resets_failed_then_starts(tmp_path):
    from voicebridge import gateway_supervisor as GS

    state = {"failed": True, "started": False}
    calls = []

    def runner(argv, t):
        calls.append(list(argv))
        if "show" in argv:
            return _show(active=state["started"], failed=state["failed"],
                         n_restarts=3, exec_main_status=1)
        if "reset-failed" in argv:
            state["failed"] = False
            return (0, "", "")
        if "start" in argv:
            state["started"] = True
            return (0, "", "")
        return (0, "", "")

    out = GS.recover(_cfg(tmp_path), confirmed=True, runner=runner,
                     probe=lambda h, p: False)
    assert out["ok"] is True and out["state"] == "OK", out
    assert any("reset-failed" in c for c in calls), calls
    assert any("start" in c for c in calls), calls
    assert out["status"]["running"] is True, out["status"]


def test_recover_without_confirmation_is_refused_and_no_execution(tmp_path):
    from voicebridge import gateway_supervisor as GS

    calls = []
    out = GS.recover(_cfg(tmp_path), confirmed=False,
                     runner=lambda argv, t: calls.append(list(argv)) or _show(failed=True),
                     probe=lambda h, p: False)
    assert out["ok"] is False and out["code"] == GS.CONFIRM_CODE, out
    assert out["action"] == "recover", out
    assert calls == [], "refused recovery must not touch systemctl"


# --- (e) recover() is an idempotent no-op on a healthy unit -----------------
def test_recover_is_noop_when_already_healthy(tmp_path):
    from voicebridge import gateway_supervisor as GS

    calls = []
    out = GS.recover(_cfg(tmp_path), confirmed=True,
                     runner=lambda argv, t: calls.append(list(argv)) or _show(active=True),
                     probe=lambda h, p: True)
    assert out["ok"] is True and out.get("noop") is True, out
    # Only the read-only ``show`` status probe ran; no recovery transition.
    assert not any(("reset-failed" in c or "start" in c) for c in calls), calls


# --- failure() --------------------------------------------------------------
def test_failure_reports_typed_failed(tmp_path):
    from voicebridge import gateway_supervisor as GS

    out = GS.failure(_cfg(tmp_path),
                     runner=lambda argv, t: _show(failed=True, n_restarts=2,
                                                  exec_main_status=1),
                     probe=lambda h, p: False)
    assert out["state"] == "FAILED" and out["failed"] is True, out
    assert out["n_restarts"] == 2 and out["exec_main_status"] == 1, out

"""Real Qubit-owned gateway/service supervision (dashboard System controls).

Qubit's dashboard "Gateway" card controls the Qubit-owned process — the
systemd user unit that runs ``python -m voicebridge run`` (Owner Decision #10:
the Qubit-owned gateway IS this host process; the vendored messaging gateway and
external peer daemons stay operator-owned). Earlier this surface was a
hard-coded "not-supervised" status plus an unconditional typed refusal, so the
Start/Stop/Restart buttons were permanently disabled. This module makes them
real:

* **status** is read from systemd (``ActiveState``/``SubState``/``MainPID`` +
  ``NRestarts``/``ExecMainStatus``/``ActiveEnterTimestamp``) plus a bounded
  local TCP health probe, so a stale process, a port conflict, or a crashed
  unit (``FAILED``) is visible instead of guessed;
* **start/stop/restart/recover** are idempotent transitions with a bounded
  timeout. ``restart``/``stop`` are dispatched *detached* so the HTTP response
  is delivered before the serving process is replaced;
* every control attempt is authorized through the ONE Qubit authority path: the
  fail-closed emergency-stop latch is probed first, then a single-use
  target-bound confirmation is issued+consumed through the shared durable store
  (:func:`voicebridge.broker.confirmation_store_for` via
  :class:`voicebridge.dispatch_guard.WorkerGuard`), the
  :class:`~voicebridge.capabilities.PolicyEngine` records the Decision, and this
  module writes an audit receipt. A bare ``confirm`` flag is never sufficient on
  its own — no execution happens without a valid confirmation;
* **failure** reports the last non-zero exit / restart count, and **recover**
  is a real ``reset-failed`` + ``start`` under the same gate;
* failures are typed and redacted, never fabricated.

All process execution is injected (``runner``/``spawner``) so the logic is
deterministically unit-testable without touching the host.
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

DEFAULT_UNIT = "qubit-voicebridge.service"
DEFAULT_PORT = 8890
TIMEOUT_S = 15.0
STATUS_TIMEOUT_S = 5.0
DETACH_DELAY_S = 2.0

CONFIRM_CODE = "GATEWAY_CONFIRM_REQUIRED"
ESTOP_CODE = "GATEWAY_ESTOP"
UNAVAILABLE_CODE = "GATEWAY_SUPERVISION_UNAVAILABLE"
FAILED_CODE = "GATEWAY_CONTROL_FAILED"
TIMEOUT_CODE = "GATEWAY_CONTROL_TIMEOUT"
BAD_INPUT_CODE = "GATEWAY_BAD_INPUT"
ACTIONS = ("start", "stop", "restart")

# The one capability every gateway lifecycle transition is decided against.
GATEWAY_CONTROL_CAPABILITY = "gateway.control"
GATEWAY_CONTROL_SCOPE = "gateway"

# systemd / policy confirmation-denial codes that surface as "confirm required"
# on the gateway control surface.
_CONFIRM_DENIAL_CODES = frozenset({
    "CONFIRMATION_REQUIRED", "UNKNOWN_CONFIRMATION_ID", "EXPIRED_CONFIRMATION",
    "REPLAYED_CONFIRMATION", "CAPABILITY_MISMATCH", "TARGET_MISMATCH",
})

Runner = Callable[[Sequence[str], float], Tuple[int, str, str]]
Spawner = Callable[[Sequence[str]], None]


def _get(cfg: Any, *path: str, default: Any = None) -> Any:
    try:
        return cfg.get(*path, default=default) if hasattr(cfg, "get") else default
    except Exception:  # noqa: BLE001
        return default


def service_unit(cfg: Any) -> str:
    unit = str(_get(cfg, "gateway", "service_unit", default="") or "").strip()
    return unit or DEFAULT_UNIT


def system_scope(cfg: Any) -> str:
    scope = str(_get(cfg, "gateway", "system_scope", default="") or "").strip()
    return scope if scope in ("user", "system") else "user"


def _systemctl_argv(cfg: Any, *args: str) -> List[str]:
    argv = ["systemctl"]
    if system_scope(cfg) == "user":
        argv.append("--user")
    return argv + list(args)


def _host_port(cfg: Any) -> Tuple[str, int]:
    host = str(_get(cfg, "webui", "host", default="127.0.0.1") or "127.0.0.1")
    try:
        port = int(_get(cfg, "webui", "port", default=DEFAULT_PORT) or DEFAULT_PORT)
    except (TypeError, ValueError):
        port = DEFAULT_PORT
    return host, port


def _default_runner(argv: Sequence[str], timeout: float) -> Tuple[int, str, str]:
    try:
        proc = subprocess.run(list(argv), capture_output=True, text=True,
                              timeout=timeout)
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError:
        return 127, "", f"{argv[0]}: not found"
    except subprocess.TimeoutExpired:
        return 124, "", "command timed out"
    except Exception as exc:  # noqa: BLE001
        return 1, "", str(exc)


def _default_spawner(argv: Sequence[str]) -> None:
    subprocess.Popen(list(argv), stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


def _run(run: Runner, argv: Sequence[str],
         timeout: float = TIMEOUT_S) -> Tuple[int, str, str]:
    """Call a runner, converting any raised exception to a typed ``(rc, …, …)``.

    ``_default_runner`` maps failures to return codes, but an **injected**
    runner may raise instead (e.g. a real ``subprocess.run`` bubbling a
    ``TimeoutExpired``). :func:`status` is documented never to raise and
    :func:`control` must always return a typed receipt, so both go through
    here. A timeout becomes rc 124 — the same sentinel ``_default_runner``
    uses — and any other exception becomes rc 1.
    """
    try:
        rc, out, err = run(list(argv), timeout)
        return int(rc), str(out or ""), str(err or "")
    except Exception as exc:  # noqa: BLE001
        timed_out = (isinstance(exc, (TimeoutError, subprocess.TimeoutExpired))
                     or "timed out" in str(exc).lower())
        return (124 if timed_out else 1), "", str(exc)[:300]


def _port_in_use(host: str, port: int, *, timeout: float = 0.5) -> bool:
    """True when a TCP listener already owns ``host:port`` (stale/conflict)."""
    target = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        try:
            # If connect succeeds, something is listening.
            return sock.connect_ex((target, port)) == 0
        except OSError:
            return False


def _int_prop(props: Dict[str, str], key: str) -> int:
    """Parse an integer systemd property, tolerating ``""``/non-numeric."""
    try:
        return int(str(props.get(key, "0")).strip() or 0)
    except (TypeError, ValueError):
        return 0


def status(cfg: Any, *, runner: Optional[Runner] = None,
           probe: Optional[Callable[[str, int], bool]] = None) -> Dict[str, Any]:
    """Read-only gateway/service status. Never raises.

    Reports the real systemd state plus failure fields (``exec_main_status``,
    ``n_restarts``) and a bounded TCP health probe. ``state`` is ``OK`` when the
    unit is active and the port answers, ``FAILED`` when the unit failed, and a
    typed ``UNAVAILABLE``/``NOT_INSTALLED``/``DEGRADED`` otherwise.
    """
    unit = service_unit(cfg)
    host, port = _host_port(cfg)
    run = runner or _default_runner
    check_port = probe or _port_in_use
    out: Dict[str, Any] = {
        "state": "OK", "service_unit": unit, "scope": system_scope(cfg),
        "host": host, "port": port, "available": shutil.which("systemctl")
        is not None, "running": False, "pid": None, "active_state": "unknown",
        "sub_state": "unknown", "started_at": None, "port_in_use": False,
        "exec_main_status": 0, "n_restarts": 0, "failed": False,
        "failure": None, "healthy": False, "health": "UNKNOWN",
        "health_detail": "", "detail": "",
    }
    if not out["available"]:
        out["state"] = "UNAVAILABLE"
        out["health"] = "UNKNOWN"
        out["detail"] = ("systemctl is not available on this host; gateway "
                         "process control requires a systemd service")
        return out
    # One bounded, argv-only (never shell) ``systemctl show`` read: the live
    # state plus the failure/restart counters that distinguish a crashed unit
    # from a merely stopped one.
    argv = _systemctl_argv(
        cfg, "show", unit, "--no-pager",
        "--property=ActiveState,SubState,MainPID,ExecMainStartTimestamp,"
        "ActiveEnterTimestamp,NRestarts,ExecMainStatus")
    rc, stdout, stderr = _run(run, argv, STATUS_TIMEOUT_S)
    props: Dict[str, str] = {}
    for line in (stdout or "").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            props[key.strip()] = value.strip()
    out["active_state"] = props.get("ActiveState", "unknown") or "unknown"
    out["sub_state"] = props.get("SubState", "unknown") or "unknown"
    out["started_at"] = props.get("ExecMainStartTimestamp") or None
    out["active_enter_timestamp"] = props.get("ActiveEnterTimestamp") or None
    pid_raw = props.get("MainPID", "0")
    try:
        pid = int(pid_raw)
    except (TypeError, ValueError):
        pid = 0
    out["pid"] = pid if pid > 0 else None
    out["running"] = out["active_state"] == "active"
    out["exec_main_status"] = _int_prop(props, "ExecMainStatus")
    out["n_restarts"] = _int_prop(props, "NRestarts")
    if rc != 0 and out["active_state"] == "unknown":
        # The unit may simply not be installed; that is a typed state, not a
        # crash. Surface the exact systemd message (redacted by the caller).
        out["state"] = "NOT_INSTALLED" if "could not be found" in (
            stderr or "").lower() else "DEGRADED"
        out["detail"] = _redact(stderr or "systemctl returned no state").strip()[:300]
    # Bounded TCP health probe (never blocks the dashboard): connect_ex with a
    # sub-second timeout against the configured listener.
    t0 = time.perf_counter()
    try:
        out["port_in_use"] = bool(check_port(host, port))
    except Exception:  # noqa: BLE001
        out["port_in_use"] = False
    out["health_latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    # Failure typing: a unit systemd itself marks ``failed``, or a non-zero
    # last exit while inactive, is a terminal FAILED state (not "stopped").
    if (out["active_state"] == "failed"
            or (out["exec_main_status"] != 0 and not out["running"]
                and out["active_state"] not in ("unknown", "inactive", "dead"))):
        out["failed"] = True
        out["state"] = "FAILED"
        out["failure"] = {"exec_main_status": out["exec_main_status"],
                          "n_restarts": out["n_restarts"],
                          "active_state": out["active_state"]}
        out["detail"] = (out["detail"] or "") + (
            f" unit is in a failed state (ExecMainStatus="
            f"{out['exec_main_status']}, NRestarts={out['n_restarts']})")
    out["healthy"] = bool(out["running"] and out["port_in_use"])
    if out["healthy"]:
        out["health"] = "OK"
    elif out["running"]:
        out["health"] = "STARTING"
        out["health_detail"] = ("service active but the configured port is not "
                                "accepting connections; the listener may still "
                                "be starting")
    elif out["port_in_use"]:
        out["health"] = "STALE_PORT"
        out["health_detail"] = ("port is in use but the service is not active "
                                "(stale process?)")
    else:
        out["health"] = "DOWN"
    if out["health_detail"]:
        out["detail"] = (out["detail"] or "") + " " + out["health_detail"]
    return out


def _estop(cfg: Any) -> bool:
    try:
        from .plugins import estop_engaged

        return bool(estop_engaged(cfg))
    except Exception:  # noqa: BLE001
        try:
            from .broker import estop_engaged

            return bool(estop_engaged(cfg))
        except Exception:  # noqa: BLE001
            return True


def _audit(op: str, actor: str, detail: Dict[str, Any]) -> None:
    try:
        from .logging_setup import log_event

        log_event(f"gateway.supervisor.{op}", actor=actor, **detail)
    except Exception:  # noqa: BLE001
        pass


def _redact(text: Any) -> str:
    """Run systemctl diagnostics through the one redaction choke point.

    The module contract is that failures are "typed and redacted": a unit's
    stderr can carry a credential from an ``EnvironmentFile`` or a failing
    ``ExecStart``, so it must never be surfaced verbatim to the dashboard.
    """
    try:
        from .redaction import redact_text

        return redact_text(str(text if text is not None else ""))
    except Exception:  # noqa: BLE001
        return str(text if text is not None else "")


# --------------------------------------------------------------------------
# One authority path for gateway control (Owner Decision #10)
# --------------------------------------------------------------------------
# The confirmation authority is the shared durable store the rest of Qubit
# uses (``broker.confirmation_store_for`` -> the ApprovalJournal-backed
# ConfirmationStore). The engine is a thin, cached registry wrapper; there is
# exactly one writable confirmation store per state tree, so a control decision
# made here and a confirmation minted anywhere else are the same record.
_CONTROL_ENGINES: Dict[str, Any] = {}
_CONTROL_ENGINE_LOCK = threading.Lock()


def _engine_key(store: Any) -> str:
    pstore = getattr(store, "_pstore", None)
    path = getattr(pstore, "path", None)
    return str(path) if path else "in-memory"


def _control_engine(cfg: Any) -> Any:
    """The shared PolicyEngine for gateway lifecycle control (one per state)."""
    from .broker import confirmation_store_for
    from .capabilities import (CapabilityRegistry, CapabilitySpec, PolicyEngine,
                               Tier)

    store = confirmation_store_for(cfg, default_ttl_s=120.0)
    key = _engine_key(store)
    with _CONTROL_ENGINE_LOCK:
        engine = _CONTROL_ENGINES.get(key)
        if engine is None:
            registry = CapabilityRegistry()
            registry.register(CapabilitySpec(
                name=GATEWAY_CONTROL_CAPABILITY, version="1.0.0",
                tier=Tier.MUTATING,
                summary=("start/stop/restart/recover the Qubit-owned gateway "
                         "service unit"),
                scopes=(GATEWAY_CONTROL_SCOPE,), timeout_s=120.0))
            engine = PolicyEngine(registry=registry, confirmations=store)
            _CONTROL_ENGINES[key] = engine
        return engine


def _authorize_control(cfg: Any, unit: str, action: str, actor: str, *,
                       confirmed: bool, confirmation_id: str, source: str
                       ) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """Authorize one lifecycle transition through the one authority path.

    Returns ``(refusal, receipt)``. ``refusal`` is ``None`` when the action is
    authorized; otherwise it is a typed result and nothing executes. A
    user-originated ``confirmed`` action mints+consumes a short-lived
    single-use confirmation on the shared durable store
    (:meth:`dispatch_guard.WorkerGuard.authorize`); a presented
    ``confirmation_id`` is consumed as a durable confirmation bound to this
    capability + exact args. Any policy denial, source mismatch, or authority
    read failure is a typed refusal (fail closed).
    """
    args = {"action": action, "unit": unit}
    if not confirmed and not confirmation_id:
        return ({"ok": False, "code": CONFIRM_CODE, "state": "CONFIRM_REQUIRED",
                 "action": action,
                 "detail": (f"gateway {action} changes a service; re-issue with "
                            "confirm=true (dashboard button / CLI --confirm) or "
                            "a valid confirmation id")}, {})
    try:
        from .capabilities import ActionRequest
        from .dispatch_guard import DispatchBlocked, WorkerGuard

        engine = _control_engine(cfg)
        if confirmation_id:
            decision = engine.decide(ActionRequest(
                capability=GATEWAY_CONTROL_CAPABILITY, args=args, source=source,
                confirmation_id=str(confirmation_id),
                correlation_id=f"gateway:{action}"))
            if not decision.allowed:
                code = str(decision.code or CONFIRM_CODE)
                return ({"ok": False,
                         "code": (CONFIRM_CODE if code in _CONFIRM_DENIAL_CODES
                                  else code),
                         "state": "CONFIRM_REQUIRED", "action": action,
                         "detail": f"confirmation refused: {code}"}, {})
        else:
            guard = WorkerGuard(cfg, engine=engine)
            decision = guard.authorize(
                GATEWAY_CONTROL_CAPABILITY, args, source=source,
                correlation_id=f"gateway:{action}")
    except DispatchBlocked as exc:
        code = str(getattr(exc, "code", "") or UNAVAILABLE_CODE)
        return ({"ok": False,
                 "code": (CONFIRM_CODE if code in _CONFIRM_DENIAL_CODES else code),
                 "state": "CONFIRM_REQUIRED", "action": action,
                 "detail": f"gateway control not authorized: {code}"}, {})
    except Exception as exc:  # noqa: BLE001 - authority read failure fails closed
        return ({"ok": False, "code": UNAVAILABLE_CODE, "state": "UNAVAILABLE",
                 "action": action,
                 "detail": ("gateway control authority unavailable; refused "
                            f"({_redact(str(exc))[:160]})")}, {})
    receipt = {"decision": "ALLOWED",
               "decision_id": str(decision.confirmation_id or ""),
               "capability": GATEWAY_CONTROL_CAPABILITY, "source": source}
    _audit("control.authorize", actor,
           {"unit": unit, "action": action, **receipt})
    return (None, receipt)


def _real_control_disabled() -> bool:
    """True when a real start/stop/restart must NOT be dispatched.

    A test process (or an operator who sets the guard) must never replace the
    host's service as a side effect of exercising this code. Injected
    ``runner``/``spawner`` bypass this check, so the behavior remains testable.
    """
    if os.environ.get("QUBIT_GATEWAY_SUPERVISION_DISABLED") == "1":
        return True
    try:
        from . import qubit_runtime

        return bool(qubit_runtime.under_test())
    except Exception:  # noqa: BLE001
        return bool(os.environ.get("PYTEST_CURRENT_TEST"))


def failure(cfg: Any, *, runner: Optional[Runner] = None,
            probe: Optional[Callable[[str, int], bool]] = None) -> Dict[str, Any]:
    """Report the last gateway failure (read-only). Never raises.

    ``failed`` is true when systemd marks the unit failed or its last main
    process exited non-zero; ``state`` is then the typed ``FAILED``. This is a
    thin, contractual view over :func:`status` so callers need not re-derive
    the failure fields.
    """
    st = status(cfg, runner=runner, probe=probe)
    return {"state": "FAILED" if st.get("failed") else "OK",
            "failed": bool(st.get("failed")),
            "service_unit": st.get("service_unit"),
            "active_state": st.get("active_state"),
            "exec_main_status": st.get("exec_main_status", 0),
            "n_restarts": st.get("n_restarts", 0),
            "pid": st.get("pid"), "failure": st.get("failure"),
            "detail": st.get("detail", ""), "status": st}


def control(cfg: Any, action: str, *, confirmed: bool = False,
            confirmation_id: str = "", source: str = "user",
            actor: str = "dashboard:user", runner: Optional[Runner] = None,
            spawner: Optional[Spawner] = None,
            probe: Optional[Callable[[str, int], bool]] = None
            ) -> Dict[str, Any]:
    """Perform an authorized gateway lifecycle transition.

    Authorization runs through the one authority path (e-stop latch probe ->
    shared durable confirmation store + PolicyEngine Decision -> audit
    receipt); see :func:`_authorize_control`. A control action without a valid
    confirmation is a typed ``CONFIRM_REQUIRED`` refusal and executes nothing.

    Idempotent: starting an active service (or stopping an inactive one) is a
    successful no-op. ``restart``/``stop`` are detached so the caller's HTTP
    response is delivered before this process is replaced.
    """
    clean = str(action or "").strip().lower()
    if clean not in ACTIONS:
        return {"ok": False, "code": BAD_INPUT_CODE, "state": "FAILED",
                "detail": f"action must be one of {ACTIONS}"}
    if _estop(cfg):
        _audit("control.refused", actor,
               {"unit": service_unit(cfg), "action": clean, "code": ESTOP_CODE})
        return {"ok": False, "code": ESTOP_CODE, "state": "UNAVAILABLE",
                "detail": "emergency stop is latched; gateway control refused"}
    unit = service_unit(cfg)
    refusal, receipt = _authorize_control(
        cfg, unit, clean, actor, confirmed=confirmed,
        confirmation_id=str(confirmation_id or ""), source=source)
    if refusal is not None:
        _audit("control.refused", actor,
               {"unit": unit, "action": clean, "code": refusal.get("code", "")})
        return refusal
    # The fail-closed guard must cover the executor each action actually uses.
    # Without an injected runner we cannot even read ``systemctl show`` without
    # touching the host, so refuse immediately. With an injected runner the
    # read-only status (and an idempotent no-op) is safe; a *real* transition
    # that would use the detached spawner (stop/restart) is refused below when
    # the spawner was not injected. This closes the hole where injecting only a
    # runner let ``restart`` dispatch the real spawner from a test process.
    if _real_control_disabled() and runner is None:
        return {"ok": False, "code": UNAVAILABLE_CODE, "state": "UNAVAILABLE",
                "detail": ("real gateway process control is disabled in this "
                           "environment (test guard); no service was touched")}
    run = runner or _default_runner
    spawn = spawner or _default_spawner
    before = status(cfg, runner=run, probe=probe)
    unit = before["service_unit"]
    if not before["available"]:
        return {"ok": False, "code": UNAVAILABLE_CODE, "state": "UNAVAILABLE",
                "detail": before["detail"], "status": before, "receipt": receipt}

    if clean == "start" and before["running"]:
        _audit("start", actor, {"unit": unit, "noop": True, **receipt})
        return {"ok": True, "state": "OK", "action": "start", "noop": True,
                "detail": "service already active", "status": before,
                "receipt": receipt}
    if clean == "stop" and not before["running"]:
        _audit("stop", actor, {"unit": unit, "noop": True, **receipt})
        return {"ok": True, "state": "OK", "action": "stop", "noop": True,
                "detail": "service already stopped", "status": before,
                "receipt": receipt}

    needs_spawn = clean in ("stop", "restart")
    if _real_control_disabled() and needs_spawn and spawner is None:
        return {"ok": False, "code": UNAVAILABLE_CODE, "state": "UNAVAILABLE",
                "detail": ("real gateway process control is disabled in this "
                           "environment (test guard); no service was touched"),
                "status": before, "receipt": receipt}

    if clean == "start":
        rc, stdout, stderr = _run(run, _systemctl_argv(cfg, "start", unit))
        after = status(cfg, runner=run, probe=probe)
        if rc == 0:
            ok, code, state = True, "", "OK"
        elif rc == 124:
            ok, code, state = False, TIMEOUT_CODE, "TIMEOUT"
        else:
            ok, code, state = False, FAILED_CODE, "FAILED"
        _audit("start", actor, {"unit": unit, "rc": rc, **receipt})
        detail = "" if ok else _redact(stderr or (
            "systemctl start timed out" if rc == 124
            else "systemctl start failed")).strip()[:300]
        return {"ok": ok, "state": state, "action": "start",
                "code": code, "detail": detail, "status": after,
                "receipt": receipt}

    # restart / stop replace (or end) this process: dispatch detached so the
    # HTTP response is sent first, and report the transition as in-progress.
    detached = ["sh", "-c",
                f"sleep {DETACH_DELAY_S}; systemctl"
                + (" --user" if system_scope(cfg) == "user" else "")
                + f" {clean} {unit}"]
    try:
        spawn(detached)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "code": FAILED_CODE, "state": "FAILED",
                "action": clean, "detail": _redact(str(exc))[:300],
                "status": before, "receipt": receipt}
    state = "RESTARTING" if clean == "restart" else "STOPPING"
    _audit(clean, actor, {"unit": unit, "detached": True, **receipt})
    return {"ok": True, "state": state, "action": clean, "detached": True,
            "detail": f"service {clean} dispatched; it completes in "
                      f"~{int(DETACH_DELAY_S)}s", "status": before,
            "receipt": receipt}


def recover(cfg: Any, *, confirmed: bool = False, confirmation_id: str = "",
            source: str = "user", actor: str = "dashboard:user",
            runner: Optional[Runner] = None,
            spawner: Optional[Spawner] = None,
            probe: Optional[Callable[[str, int], bool]] = None
            ) -> Dict[str, Any]:
    """Recover a failed gateway: ``reset-failed`` then ``start``.

    A real recovery action, not a status flag. It is authorized through the
    same one authority path as :func:`control` (e-stop -> confirmation ->
    audit). An already-healthy unit is an idempotent no-op.
    """
    if _estop(cfg):
        _audit("control.refused", actor,
               {"unit": service_unit(cfg), "action": "recover",
                "code": ESTOP_CODE})
        return {"ok": False, "code": ESTOP_CODE, "state": "UNAVAILABLE",
                "action": "recover",
                "detail": "emergency stop is latched; recovery refused"}
    unit = service_unit(cfg)
    refusal, receipt = _authorize_control(
        cfg, unit, "recover", actor, confirmed=confirmed,
        confirmation_id=str(confirmation_id or ""), source=source)
    if refusal is not None:
        _audit("control.refused", actor,
               {"unit": unit, "action": "recover",
                "code": refusal.get("code", "")})
        return dict(refusal, action="recover")
    if _real_control_disabled() and runner is None:
        return {"ok": False, "code": UNAVAILABLE_CODE, "state": "UNAVAILABLE",
                "action": "recover",
                "detail": ("real gateway process control is disabled in this "
                           "environment (test guard); no service was touched")}
    run = runner or _default_runner
    before = status(cfg, runner=run, probe=probe)
    unit = before["service_unit"]
    if not before["available"]:
        return {"ok": False, "code": UNAVAILABLE_CODE, "state": "UNAVAILABLE",
                "action": "recover", "detail": before["detail"],
                "status": before, "receipt": receipt}
    if before["running"] and not before["failed"]:
        _audit("recover", actor, {"unit": unit, "noop": True, **receipt})
        return {"ok": True, "state": "OK", "action": "recover", "noop": True,
                "detail": "service is healthy; nothing to recover",
                "status": before, "receipt": receipt}
    rc_reset, _, err_reset = _run(run, _systemctl_argv(cfg, "reset-failed", unit))
    rc_start, _, err_start = _run(run, _systemctl_argv(cfg, "start", unit))
    after = status(cfg, runner=run, probe=probe)
    if rc_start == 0 or after["running"]:
        ok, code, state = True, "", "OK"
    elif rc_start == 124:
        ok, code, state = False, TIMEOUT_CODE, "TIMEOUT"
    else:
        ok, code, state = False, FAILED_CODE, "FAILED"
    _audit("recover", actor,
           {"unit": unit, "rc_reset": rc_reset, "rc_start": rc_start, **receipt})
    detail = "" if ok else _redact(
        err_start or err_reset or "systemctl start failed after reset-failed"
    ).strip()[:300]
    return {"ok": ok, "state": state, "action": "recover", "code": code,
            "detail": detail, "status": after, "receipt": receipt}


__all__ = ["DEFAULT_UNIT", "DEFAULT_PORT", "CONFIRM_CODE", "ESTOP_CODE",
           "UNAVAILABLE_CODE", "FAILED_CODE", "TIMEOUT_CODE", "BAD_INPUT_CODE",
           "ACTIONS", "GATEWAY_CONTROL_CAPABILITY", "service_unit",
           "system_scope", "status", "failure", "control", "recover"]

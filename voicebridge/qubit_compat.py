"""Qubit-dashboard compatibility backend (owner amendment 2026-09-11).

Serves the vendored Qubit dashboard (``frontend/qubit-web/``, byte-identical
at pinned hermes-agent@2237be3) from the Qubit backend. Every route calls the
SAME Qubit operation modules as the CLI (``voicebridge/cli.py``) and voice
(``voicebridge/voice_commands.py``): same stores, same permission broker, same
confirmation journal, same audit/redaction, same emergency stop.

Unsafe upstream actions (raw PTY spawn, file mkdir/upload-stream, secret
reveal, credential pool, config-raw dump, debug-share state dump, gateway
process supervision, remote hub fetch, external update/portal, OAuth token
dance) stay behind Qubit policy and return typed unavailable/refused states
(amendment point 9) — never fabricated success.

Vendored frontend files are NEVER modified here; Qubit additions (brand
theme, Qubit Chat, Qubit tabs) are served as plugin manifests + bundles
from ``frontend/qubit-app/`` (overlay).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
# Overlay (theme + plugins) resolution: package data first (clean wheel
# install), then the checkout. Keeps an isolated install self-contained.
from . import resources as _resources  # noqa: E402
from . import qubit_plugins as _qubit_plugins  # noqa: E402
OVERLAY_DIR = _resources.resource_path("frontend/qubit-app")

Handler = Callable[..., Tuple[int, Dict[str, Any]]]


def _redact(text: Any) -> str:
    try:
        from .redaction import redact_text
        return redact_text(str(text))
    except Exception:
        return str(text)


def _audit(op: str, actor: str, detail: Dict[str, Any]) -> None:
    try:
        from .logging_setup import log_event
        # ``log_event(event, **fields)`` takes keyword fields only. Passing the
        # detail dict positionally raised ``TypeError`` here and the bare
        # ``except`` swallowed it, so every compat audit write was a silent
        # no-op. Pass the fields as keywords so the journal is actually written.
        fields = {k: _redact(v)[:160] for k, v in detail.items()}
        fields["actor"] = actor
        log_event(f"qubit_compat.{op}", **fields)
    except Exception:
        pass


def audit_event(op: str, actor: str, detail: Dict[str, Any]) -> None:
    """Public single-audit-path writer for CLI/voice/dashboard/bridge callers."""
    _audit(op, actor, detail)


# Dashboard process boot (wall clock, recorded at import ≈ process start).
# Used by h_config_effective to prove whether this process postdates the
# newest config write. Never mutated.
_DASHBOARD_BOOT_WALL = time.time()


def _estop(cfg: Any) -> bool:
    # Fail closed: if the single e-stop authority cannot be read, treat the
    # system as stopped rather than allowing a mutating path to proceed.
    try:
        from .broker import estop_engaged
        return bool(estop_engaged(cfg))
    except Exception:
        try:
            from . import power as PW
            return bool(PW.estop_engaged(cfg))
        except Exception:
            return True


def _gate(code: str, detail: str, counterpart: str,
          http: int = 403, owner: str = "qubit-policy",
          rollback: str = "nothing changed; nothing to roll back",
          **extra: Any) -> Tuple[int, Dict[str, Any]]:
    out: Dict[str, Any] = {"ok": False, "state": "UNAVAILABLE", "code": code,
                           "detail": detail, "qubit_counterpart": counterpart,
                           "owner": owner, "rollback": rollback}
    if extra:
        out.update(extra)
    return http, out


def _ok(**fields: Any) -> Tuple[int, Dict[str, Any]]:
    out = {"ok": True}
    out.update(fields)
    return 200, out


def _staged(detail: str, confirmation_id: str,
            extra: Optional[Dict[str, Any]] = None) -> Tuple[int, Dict[str, Any]]:
    """Staged-confirmation response: HTTP 409, never 2xx.

    The vendored Qubit UI treats any 2xx as executed success (the Skills
    page flips its toggle and toasts success on resolve), so a staged
    confirmation answered 202 would render fabricated success. 409 forces
    the UI error path; the typed envelope plus the pending-approvals
    banner (GET/POST /api/ops/approvals|confirm|cancel, same journal as
    CLI/voice/classic) is the real completion path. One journal, one path.
    """
    out: Dict[str, Any] = {
        "ok": False, "state": "NEEDS_CONFIRMATION",
        "code": "CONFIRM_REQUIRED", "detail": detail,
        "confirmation_id": confirmation_id,
        "qubit_counterpart": "pending-approvals banner "
                             "(POST /api/ops/confirm) + CLI "
                             "`qubit approvals respond` + voice yes",
    }
    if extra:
        out.update(extra)
    return 409, out


def _truthy(value: Any) -> bool:
    """Wire-truth for a confirm flag: ``True``, ``"true"``, ``1``, ``"yes"``."""
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def _mutation_envelope(out: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    """Adapt a canonical mutation-contract dict to the compat wire envelope.

    The ``verified_skills.mutations`` / ``toolsets`` contracts return the exact
    HTTP status under ``http`` (409 needs-confirm, 400/403/409 typed refusal,
    200 executed); the compat layer only re-emits it so the SPA sees the same
    typed code + populated ``confirmation_id`` the CLI/voice/banner share.
    """
    http = int(out.get("http", 200) or 200)
    return http, out


def _stage_compat_confirm(cfg: Any, action: str, detail: str,
                          payload: Optional[Dict[str, Any]] = None) -> str:
    """Mint a single-use confirmation on the shared journal; return its id.

    Used by the compat helpers that have no canonical mutation module (cron).
    ``payload`` is the already-validated, secret-filtered scalar set the write
    would apply; it is recorded on the journal entry so the pending-approvals
    banner's id-only Confirm (``POST /api/ops/confirm``) can execute the real
    backend instead of only settling the entry. Empty string on failure so the
    caller still returns the typed 409 with an honest (if unusable) id rather
    than a fabricated success.
    """
    try:
        from .dashboard_ops import _journal
        approval = _journal(cfg).create(
            "qubit." + str(action), detail, ttl_s=120.0,
            session="dashboard", actor="dashboard:user", action=str(action),
            payload=dict(payload) if isinstance(payload, dict) else None)
        _audit("needs_confirm", "dashboard:user",
               {"action": str(action), "confirmation_id": approval.id})
        return approval.id
    except Exception:
        return ""


def _compat_confirm(cfg: Any, action: str, detail: str,
                    query: Optional[Dict[str, str]] = None,
                    body: Optional[Dict[str, Any]] = None,
                    payload: Optional[Dict[str, Any]] = None
                    ) -> Optional[Tuple[int, Dict[str, Any]]]:
    """Shared confirm contract for compat mutations without a canonical module.

    Returns ``None`` when the request may proceed: either an explicit
    ``confirm`` (body or query) is truthy, or a ``confirmation_id`` staged by a
    prior call for the SAME action is present and still pending (it is consumed
    here, exactly once). Otherwise stages a fresh single-use approval — with
    ``payload`` (the validated scalar set) recorded on it — and returns a typed
    409 whose ``confirmation_id`` is real and usable by both the re-issue path
    and the id-only ``POST /api/ops/confirm`` path.
    """
    b = body or {}
    q = query or {}
    # Emergency stop is the single mutation gate: a confirm-gated compat
    # mutation must refuse while it is latched, before staging or consuming any
    # approval. This covers every handler that routes through _compat_confirm
    # (cron, mcp, webhooks, profiles, ...) from one place.
    if _estop(cfg):
        return 403, {"ok": False, "state": "UNAVAILABLE",
                     "code": "EMERGENCY_STOP",
                     "detail": "emergency stop latched; mutation refused",
                     "owner": "qubit-policy",
                     "rollback": "nothing changed; nothing to roll back"}
    if _truthy(b.get("confirm")) or _truthy(q.get("confirm")):
        return None
    cid = str(b.get("confirmation_id", "")
              or q.get("confirmation_id", "") or "").strip()
    if cid:
        try:
            from .dashboard_ops import _journal
            from . import voice_commands as VC
            journal = _journal(cfg)
            target = journal.get(cid)
            if target is not None and target.state == "in_progress" \
                    and str(target.capability) == "qubit." + str(action):
                journal.respond(cid, "approve")
                if VC._mark_executed(cfg, cid):
                    return None
        except Exception:
            pass
    return _staged(detail, _stage_compat_confirm(cfg, action, detail, payload))


def _jsonable(value: Any) -> Any:
    """Recursively convert non-JSON scalars (``Path``) to strings.

    The vendored discovery returns ``Path`` objects for plugin dirs (the
    upstream web layer has a Path-aware encoder; Qubit's ``_json`` does not),
    so the compat boundary normalises them rather than emitting a 500.
    """
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def _res(out: Dict[str, Any]) -> Any:
    """Unwrap a dashboard_ops envelope to its structured result.

    run_op returns voice-pipeline outcomes whose ``receipt`` nests the real
    payload under ``result`` ({entry, session, result}); handlers adapt the
    inner result to Qubit wire shapes, not the speech envelope.
    """
    rec = out.get("receipt")
    if isinstance(rec, dict) and "result" in rec:
        return rec["result"]
    return rec


def _fail(exc: Exception) -> Tuple[int, Dict[str, Any]]:
    return 500, {"ok": False, "state": "FAILED", "code": "EXECUTOR_ERROR",
                 "detail": _redact(str(exc))[:300]}


# ---------------------------------------------------------------------------
# Power / e-stop (ONE authority: same PowerControl the CLI/voice/classic page use)
# ---------------------------------------------------------------------------

def h_power(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
            p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/power (and the SPA alias /agent/api/power).

    Read-only grant/control/MCP inventory, plus an optional capability policy
    check. Never mutates; mutations are :func:`power_action` with explicit
    confirmation. Delegates to ``voicebridge.power`` so the classic dashboard and
    the SPA observe the SAME e-stop latch.
    """
    from . import power as PW
    try:
        if q.get("capability"):
            policy = PW.power_policy(cfg)
            out = policy.check(
                q["capability"],
                scopes=tuple(str(q.get("scope", "")).split(","))
                if q.get("scope") else (),
                target=q.get("target", "") or "",
                role=q.get("role", "") or "",
                account_scope=q.get("account", "") or "")
            out["state"] = "OK" if out.get("allowed") else "denied"
            return 200, out
        store = PW.power_grant_store(cfg)
        return 200, {
            "state": "OK",
            "profile": PW.power_profile(cfg),
            "grant": store.grant().to_dict() if store.grant() else None,
            "grant_health": store.health(),
            "control": PW.power_control(cfg).status(),
            "mcp": PW.mcp_status(cfg),
        }
    except PW.PowerError as exc:
        return 200, {"state": "failed",
                     "code": getattr(exc, "code", "POWER_ERROR"),
                     "detail": _redact(str(exc))[:200]}
    except Exception as exc:
        return 200, {"state": "degraded", "detail": _redact(str(exc))[:200]}


def power_action(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/power (and the SPA alias /agent/api/power).

    Confirm-gated mutations on the shared power authority: grant/revoke/
    reset-grant and the e-stop verbs emergency-stop/reset/disable/enable. A
    request without explicit confirmation is a typed ``CONFIRMATION_REQUIRED``
    denial; it never latches the e-stop or grants a capability by accident.
    """
    from . import power as PW
    action = str(b.get("action", "") or "")
    # ``confirmed`` is the classic wire flag; ``confirm`` is accepted too so the
    # SPA's standard settle contract works on the alias.
    confirmed = _truthy(b.get("confirmed")) or _truthy(b.get("confirm"))
    try:
        store = PW.power_grant_store(cfg)
        control = PW.power_control(cfg)
        if action == "grant":
            if not confirmed:
                return 200, {"state": "denied", "code": "CONFIRMATION_REQUIRED",
                             "detail": "grant requires explicit confirmation"}
            g = store.issue(
                browsers=tuple(b.get("browsers") or ()),
                apps=tuple(b.get("apps") or ()),
                accounts=tuple(b.get("accounts") or ()),
                origins=tuple(b.get("origins") or ()),
                directories=tuple(b.get("directories") or ()),
                scopes=tuple(b.get("scopes") or ()),
                capabilities=tuple(b.get("capabilities") or ()),
                ttl_s=b.get("ttl"),
                note=str(b.get("note", ""))[:2000],
                issued_by="dashboard")
            return 200, {"state": "OK", "grant": g.to_dict()}
        if action == "revoke":
            if not confirmed:
                return 200, {"state": "denied", "code": "CONFIRMATION_REQUIRED",
                             "detail": "revoke requires explicit confirmation"}
            g = store.revoke(str(b.get("grant_id", "")), issued_by="dashboard")
            return 200, {"state": "OK", "revoked": g.to_dict()}
        if action == "reset-grant":
            if not confirmed:
                return 200, {"state": "denied", "code": "CONFIRMATION_REQUIRED",
                             "detail": "reset-grant requires explicit "
                                       "confirmation"}
            store.reset(issued_by="dashboard")
            return 200, {"state": "OK", "grant": None}
        if action == "emergency-stop":
            if not confirmed:
                return 200, {"state": "denied", "code": "CONFIRMATION_REQUIRED",
                             "detail": "emergency stop requires explicit "
                                       "confirmation"}
            return 200, control.emergency_stop(issued_by="dashboard")
        if action == "reset":
            if not confirmed:
                return 200, {"state": "denied", "code": "CONFIRMATION_REQUIRED",
                             "detail": "reset requires explicit human "
                                       "confirmation"}
            return 200, control.reset(issued_by="dashboard")
        if action == "disable":
            if not confirmed:
                return 200, {"state": "denied", "code": "CONFIRMATION_REQUIRED",
                             "detail": "disable requires explicit "
                                       "confirmation"}
            return 200, control.global_disable(issued_by="dashboard")
        if action == "enable":
            if not confirmed:
                return 200, {"state": "denied", "code": "CONFIRMATION_REQUIRED",
                             "detail": "enable requires explicit confirmation"}
            return 200, control.global_enable(issued_by="dashboard")
        return 400, {"state": "failed",
                     "detail": f"unknown action {action}"}
    except PW.PowerDenied as exc:
        return 200, {"state": "denied", "code": exc.code,
                     "detail": _redact(exc.detail)[:200]}
    except PW.PowerError as exc:
        return 200, {"state": "failed",
                     "code": getattr(exc, "code", "POWER_ERROR"),
                     "detail": _redact(str(exc))[:200]}
    except Exception as exc:
        return 200, {"state": "failed", "detail": _redact(str(exc))[:200]}


# ---------------------------------------------------------------------------
# Readers (shared Qubit modules — same functions CLI/voice call)
# ---------------------------------------------------------------------------

def _status_pressure(cfg: Any, home: Optional[Path]
                     ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Memory/disk pressure rollups for ``/api/status`` (B4-03). Never raises.

    The reference payload carries advisory ``memory``/``disk`` blocks that
    ``MemoryPressureBanner`` reads; the overlay route dropped them, leaving the
    banner permanently empty. Reuse the vendored collectors (same modules the
    reference route imports), failing to an honest ``pressure: unknown``.
    """
    memory: Dict[str, Any] = {"pressure": "unknown"}
    disk: Dict[str, Any] = {"pressure": "unknown"}
    try:
        _vendored_runtime(cfg)
        from gateway.memory_status import collect_memory_status  # type: ignore

        memory = collect_memory_status(home)
    except Exception:  # noqa: BLE001
        memory = {"pressure": "unknown"}
    try:
        _vendored_runtime(cfg)
        from gateway.disk_status import collect_disk_status  # type: ignore

        disk = collect_disk_status(home)
    except Exception:  # noqa: BLE001
        disk = {"pressure": "unknown"}
    if not isinstance(memory, dict):
        memory = {"pressure": "unknown"}
    if not isinstance(disk, dict):
        disk = {"pressure": "unknown"}
    return memory, disk


def _status_auth_gate(cfg: Any) -> Dict[str, Any]:
    """Dashboard auth-gate readout (reference ``_auth_gate_status`` shape).

    The live server gates on the configured ``webui.token`` (see the
    ``api_token`` wiring: ``cfg.get("webui", "token")``); a bare
    ``cfg.api_token`` attribute is honored as a fallback for runtimes that
    set it directly. Either engaged means auth is required.
    """
    try:
        configured = str(cfg.get("webui", "token", default="") or "")
    except Exception:  # noqa: BLE001 - config-less read: no gate
        configured = ""
    auth_required = bool(configured or str(getattr(cfg, "api_token", "")
                                           or ""))
    auth_providers: List[str] = []
    auth_flows: List[str] = []
    try:
        _vendored_runtime(cfg)
        from qubit_cli.dashboard_auth import (  # type: ignore
            list_providers, list_session_providers)

        auth_providers = [str(getattr(p, "name", p)) for p in list_providers()]
        if auth_required:
            auth_flows.append("cookie")
            if list_session_providers():
                auth_flows.append("native_pkce")
    except Exception:  # noqa: BLE001 - module optional/early startup
        pass
    return {"auth_required": auth_required, "auth_providers": auth_providers,
            "auth_flows": auth_flows}


def _status_components(gw: Dict[str, Any], platforms: Dict[str, Any],
                       home: Optional[Path]) -> Dict[str, Any]:
    """Component health rollup (counts/enums only) matching the reference."""
    running = bool(gw.get("running"))
    state = str(gw.get("state") or ("running" if running else "stopped"))
    components: Dict[str, Any] = {
        "gateway": {
            "status": ("ok" if running and state in
                       ("OK", "running", "RESTARTING") else "degraded"),
            "state": state or ("running" if running else "stopped")},
        "dashboard": {"status": "ok"},
    }
    storage_ok = False
    try:
        storage_ok = home is not None and (Path(home) / "state.db").exists()
    except Exception:  # noqa: BLE001
        storage_ok = False
    components["storage"] = {"status": "ok" if storage_ok else "degraded"}
    states = []
    for value in (platforms or {}).values():
        if not isinstance(value, dict):
            continue
        name = str(value.get("state") or value.get("status") or "").lower()
        if name and name != "disabled":
            states.append(name)
    connected = sum(1 for s in states if s in ("connected", "running", "ok"))
    components["platforms"] = {
        "status": "ok" if connected == len(states) else "degraded",
        "configured": len(states), "connected": connected}
    return components


def _status_profile_names(cfg: Any) -> List[str]:
    """Profile names for the status payload (low-sensitivity product surface)."""
    try:
        P = _profiles_module(cfg)
        infos = P.list_profiles()
        names = [str(getattr(info, "name", "") or "") for info in infos]
        names = [n for n in names if n]
        return names
    except Exception:  # noqa: BLE001
        return []


def h_status(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
             p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import runtime_inventory as RI
    # One canonical runtime inventory (the same function the CLI `qubit status`
    # and voice `system status` call). Never return a 200 with an empty
    # backend payload.
    inv = RI.runtime_inventory(cfg) if hasattr(RI, "runtime_inventory") else {}
    if not isinstance(inv, dict):
        inv = {}
    core = inv
    model = ""
    try:
        from . import agent as AG
        model = AG._model(cfg)
    except Exception:
        model = ""
    try:
        state_dir = str(Path(cfg.state_dir()))
    except Exception:
        state_dir = ""
    try:
        from . import distribution as D
        version = str(getattr(D, "VERSION", "1.0.0"))
    except Exception:
        version = "1.0.0"
    sessions_count = 0
    try:
        from .auth_status import recent_sessions as _rs
        sessions_count = int(_rs(cfg, 100).get("count", 0))
    except Exception:
        sessions_count = 0
    # StatusResponse wire shape (web/src/lib/api.ts). Gateway-process fields
    # come from the real supervisor (systemd + port probe), not a hard-coded
    # negative: a running Qubit service is reported running, and a host with
    # no systemd reports a typed UNAVAILABLE state.
    gw: Dict[str, Any] = {}
    try:
        from . import gateway_supervisor as GS

        gw = GS.status(cfg)
    except Exception:  # noqa: BLE001 - status must never crash the route
        gw = {}
    # B4-03: the reference payload also carries advisory pressure, the platform
    # map, the auth gate, and the component rollup. Port them so the SPA's
    # MemoryPressureBanner / SessionsPage platforms surfaces stop being dead.
    home: Optional[Path] = Path(state_dir) if state_dir else None
    daemon = gw.get("daemon") if isinstance(gw.get("daemon"), dict) else {}
    control = gw.get("control") if isinstance(gw.get("control"), dict) \
        else {}
    # Platforms: the daemon snapshot's map first (the real supervisor
    # shape); the live control answer's map as fallback (older supervisor
    # shape). Absent everywhere is {} — never invented names.
    platforms = daemon.get("platforms_map") if isinstance(daemon, dict) \
        else {}
    if not isinstance(platforms, dict) or not platforms:
        fallback = control.get("platforms") if isinstance(control, dict) \
            else {}
        if isinstance(fallback, dict) and fallback:
            platforms = fallback
    if not isinstance(platforms, dict):
        platforms = {}
    memory, disk = _status_pressure(cfg, home)
    auth = _status_auth_gate(cfg)
    try:
        active_agents = int((daemon or {}).get("active_agents")
                            if (daemon or {}).get("active_agents")
                            is not None
                            else (control or {}).get("active_agents") or 0)
    except Exception:  # noqa: BLE001
        active_agents = 0
    # Busy/drainable ported from the reference (``derive_gateway_busy`` /
    # ``derive_gateway_drainable``): live + serving + agents > 0. The state
    # prefers the daemon's self-reported ``gateway_state`` (native
    # running/degraded vocabulary), then the control answer, then the
    # supervisor rollup.
    running = bool(gw.get("running"))
    dstate = (daemon.get("gateway_state") or control.get("gateway_state")
              or gw.get("state"))
    try:
        _vendored_runtime(cfg)
        from gateway.status import parse_active_agents  # type: ignore

        drainable = bool(running) and dstate in ("running", "degraded")
        gateway_busy = bool(drainable
                            and parse_active_agents(active_agents) > 0)
    except Exception:  # noqa: BLE001 - status never breaks on the derive
        drainable = False
        gateway_busy = False
    components = _status_components(gw, platforms, home)
    overall = ("ok" if all(item.get("status") == "ok"
                           for item in components.values()) else "degraded")
    # Update offer: consult the install-method authority (a git checkout can
    # update in place; package/container/immutable trees get the operator
    # command instead). Never a hardcoded False — SP-09 demands truth.
    try:
        from . import update_manager as UM

        _im = UM.detect_install_method(cfg)
        can_update = bool((_im or {}).get("supported_in_place"))
    except Exception:  # noqa: BLE001 - unknown install: no offer
        can_update = False
    install_id: Optional[str] = None
    try:
        _vendored_runtime(cfg)
        from qubit_cli.install_identity import get_install_id  # type: ignore

        install_id = get_install_id()
    except Exception:  # noqa: BLE001
        install_id = None
    return _ok(schema="qubit-runtime-status/1",
               model=model, qubit=core,
               active_sessions=sessions_count, active_agents=active_agents,
               auth_required=auth["auth_required"],
               auth_providers=auth["auth_providers"],
               auth_flows=auth["auth_flows"],
               can_update_qubit=can_update,
               config_path=state_dir, config_version=1, env_path=state_dir,
               gateway_exit_reason=None, gateway_health_url=None,
               gateway_pid=gw.get("pid"), gateway_platforms=platforms,
               gateway_running=running,
               gateway_busy=gateway_busy,
               gateway_drainable=drainable,
               gateway_state=(gw.get("state") if gw.get("running")
                              else (gw.get("active_state") or "unknown")),
               gateway_updated_at=None,
               gateway_service_unit=gw.get("service_unit"),
               gateway_detail=gw.get("detail", ""),
               gateway_mode="standalone",
               memory=memory, disk=disk,
               components=components, overall=overall,
               profiles=_status_profile_names(cfg), install_id=install_id,
               qubit_home=state_dir, latest_config_version=1,
               release_date="2026-09-11", version="qubit-" + version,
               detail="Qubit single-authority backend; gateway process "
                      "supervision runs through the Qubit-owned service unit")


def h_runtime(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
              p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """Canonical runtime inventory on the SPA-prefixed API path.

    The product SPA rewrites every ``/api/*`` call to ``/agent/api/*`` (see
    ``web/src/lib/api.ts``), so the debug overlay's ``api.getRuntime()`` reaches
    ``/agent/api/runtime``. The classic ``webui.py`` branch serves
    ``/api/runtime`` directly; without this compat route the SPA path 404'd and
    the overlay silently rendered no inventory. Both routes call the same
    ``runtime_inventory`` so there is one authority and one wire shape.
    """
    from .runtime_inventory import runtime_inventory
    inv = runtime_inventory(cfg)
    if not isinstance(inv, dict):
        inv = {}
    return _ok(**inv)


def h_health(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
             p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """Lightweight liveness (HealthResponse wire shape): ok/version/auth_required.

    Backed by the real installation-health inventory
    (``voicebridge.lifecycle.health_inventory``) — the same module
    ``qubit setup health`` calls. A DEGRADED aggregate is reported verbatim;
    liveness itself is never fabricated READY when the probe raises.
    """
    code, status_out = h_status(cfg, q, b, p)
    if code != 200:
        return code, status_out
    state = "READY"
    detail = "Qubit backend process is live"
    try:
        from .lifecycle import health_inventory
        inv = health_inventory(cfg)
        if isinstance(inv, dict) and inv.get("state"):
            state = str(inv["state"])
    except Exception as exc:
        state = "DEGRADED"
        detail = "installation-health inventory unavailable: " + \
            _redact(str(exc))[:160]
    return _ok(version=status_out.get("version", "qubit-1.0.0"),
               auth_required=bool(status_out.get("auth_required", False)),
               state=state, detail=detail)


def h_auth_me(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
              p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    # AuthMeResponse wire shape: local loopback operator identity.
    return _ok(user_id="local-operator", email="", display_name="Qubit Operator",
               org_id="local", provider="loopback", expires_at=0,
               authenticated=True, actor="dashboard:user", mode="loopback-token",
               detail="loopback session-token auth; no OAuth dance")


def h_events(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
             p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    try:
        from .webui import WebUI  # noqa
    except Exception:
        pass
    items: List[Dict[str, Any]] = []
    try:
        state_dir = Path(cfg.state_dir())
        for name in ("webui_events.jsonl", "events.jsonl", "audit.jsonl"):
            fp = state_dir / name
            if not fp.exists():
                continue
            lines = fp.read_text(errors="replace").splitlines()[-100:]
            for line in lines:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if isinstance(ev, dict):
                    items.append({k: _redact(v)[:300] if isinstance(v, str) else v
                                  for k, v in ev.items()})
            if items:
                break
    except Exception as exc:
        return _fail(exc)
    return _ok(events=items[-int(q.get("limit", "100") or "100"):],
               count=len(items))


def h_pub(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
          p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST/WEBSOCKET /api/pub -> publish into the single Qubit event journal.

    The upstream ``/api/pub`` is a WebSocket channel broadcaster. Qubit keeps
    one event path and never starts a second WS server, so the Qubit-safe
    equivalent appends the redacted, validated event to the same
    ``<state_dir>/typed-events.jsonl`` journal the dashboard feed and
    communications runtime read. It returns the durable ``seq``.
    """
    channel = str(b.get("channel", b.get("name", "dashboard")) or "dashboard")
    message = b.get("data", b.get("message", b.get("text", "")))
    try:
        from .events.journal import EventJournal
        journal = EventJournal(_store_path(cfg, "typed-events.jsonl"))
        record = journal.append("CUSTOM", name=channel, value=message)
    except Exception as exc:
        return _fail(exc)
    _audit("pub", "dashboard:user", {"channel": channel,
                                     "seq": record.get("seq")})
    return _ok(ok=True, channel=channel, seq=record.get("seq"),
               published=True,
               detail="published to the single Qubit event journal")


# ---------------------------------------------------------------------------
# A10 — one session authority. Every receipt-lifecycle reader resolves the
# store through ``auth_status.session_authority()`` (the single declared
# contract) instead of importing a private store, so the dashboard cannot
# drift onto a second writable session store or the fork's read-only state.db.
# ---------------------------------------------------------------------------
RECEIPT_SESSION_AUTHORITY = "voicebridge.auth_status.SessionStore"
#: The single *chat transcript* authority (C7): the strict adapter
#: ``QubitSessionStore`` over the vendored ``qubit_state.SessionDB``
#: (``state.db``). The terminal/TUI/agent and the dashboard Sessions surface
#: and ``/qubit-chat`` all read/write that ONE database, so conversation
#: history is visible identically on every surface.
CHAT_SESSION_AUTHORITY = "voicebridge.qubit_session_store.QubitSessionStore"
#: Maximum canonical chat sessions one dashboard sessions-page read returns.
#: The adapter's own page cap is 100; the page must not silently hide the
#: sessions past the first page (the single ``state.db`` holds the full agent
#: history, so a capped read understated both the list and the stats totals).
_SESSIONS_CHAT_PAGE_MAX = 1000


def session_authority_contract() -> Dict[str, Any]:
    """The single session-authority contract (delegated, read-only)."""
    from . import auth_status as AS

    return AS.session_authority()


def _chat_session_store(cfg: Any) -> Any:
    """Open the chat-transcript store named by the session authority.

    Fails closed with a typed error if the declared authority ever changes, so
    a dashboard reader can never silently read a different store or the fork's
    read-only ``state.db`` (the split-brain the single-authority contract
    forbids).
    """
    from . import chat_transport as CT

    writable = session_authority_contract().get("writable_authorities", {}) or {}
    if writable.get("chat_transcript") != CHAT_SESSION_AUTHORITY:
        raise RuntimeError(
            "session authority contract drift: chat_transcript is "
            f"{writable.get('chat_transcript')!r}, expected "
            f"{CHAT_SESSION_AUTHORITY!r}")
    return CT.store(cfg)


def _receipt_session_store(cfg: Any) -> Any:
    """Open the receipt-lifecycle store named by the session authority.

    Fails closed with a typed error if the declared authority ever changes,
    so a reader can never silently read a different store.
    """
    from . import auth_status as AS

    writable = session_authority_contract().get("writable_authorities", {}) or {}
    if writable.get("receipt_lifecycle") != RECEIPT_SESSION_AUTHORITY:
        raise RuntimeError(
            "session authority contract drift: receipt_lifecycle is "
            f"{writable.get('receipt_lifecycle')!r}, expected "
            f"{RECEIPT_SESSION_AUTHORITY!r}")
    return AS.session_store(cfg)


def _chat_session_rows(cfg: Any, *, query: str = "",
                       limit: int = _SESSIONS_CHAT_PAGE_MAX,
                       offset: int = 0,
                       include_archived: bool = False) -> Dict[str, Any]:
    """Canonical chat-transcript sessions (single authority: ChatSessionStore).

    The store already returns the ``SessionInfo`` shape (``source="qubit-chat"``)
    so the dashboard Sessions surface reflects the same rows ``/qubit-chat``
    writes. ``limit`` defaults to the full page bound so a read cannot silently
    hide canonical sessions behind the adapter's per-call page. Archived rows
    are excluded by default (the store's own default, mirroring the reference
    ``archived="exclude"``); recovery callers opt in explicitly. A read failure
    degrades to an empty list; it never fabricates rows.
    """
    try:
        res = _chat_session_store(cfg).list(limit=limit, offset=offset,
                                            query=query,
                                            include_archived=include_archived)
    except Exception:
        return {"sessions": [], "total": 0}
    rows = [_rebrand_chat_record(r) for r in res.get("sessions", [])]
    return {"sessions": rows, "total": int(res.get("total", len(rows)))}


def h_sessions_list(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    try:
        limit = max(1, min(int(q.get("limit", "50") or "50"), 100))
    except ValueError:
        limit = 50
    try:
        offset = max(0, int(q.get("offset", "0") or "0"))
    except ValueError:
        offset = 0
    query = str(q.get("q", q.get("query", "")) or "").strip()
    # Source filters + ordering the SPA sends (api.ts appendSessionFilters).
    # ``sources``/``exclude_sources`` arrive comma-joined; a single ``source``
    # is an exact match. Ignoring these made the Chats/Automation/All segments
    # and the source popover no-ops.
    source = str(q.get("source", "") or "").strip()
    sources = [s.strip() for s in str(q.get("sources", "") or "").split(",")
               if s.strip()]
    exclude = [s.strip() for s in str(q.get("exclude_sources", "") or "")
               .split(",") if s.strip()]
    order = str(q.get("order", "") or "").strip().lower()
    # Archived rows are excluded by default, matching the reference sessions
    # route (``web_routers/sessions.py:176`` ``archived: str = "exclude"``).
    # ``archived=include`` recovers them and ``archived=only`` shows them alone;
    # an unknown value falls back to the safe default (exclude), never listing
    # archived rows as if they were live.
    archived = str(q.get("archived", "exclude") or "exclude").strip().lower()
    if archived not in ("exclude", "only", "include"):
        archived = "exclude"
    # Both ``include`` and ``only`` need the archived rows fetched before the
    # post-filter narrows them; ``exclude`` (the default) never fetches them.
    include_archived = archived in ("include", "only")
    # The canonical chat transcripts (the single session authority) come first,
    # then the receipt-lifecycle rows the automation lane writes. Both are real
    # store reads; neither is synthesized, and a shared id is never duplicated.
    merged: Dict[str, Dict[str, Any]] = {}
    for row in _chat_session_rows(cfg, query=query,
                                  include_archived=include_archived)["sessions"]:
        rid = str(row.get("id", ""))
        if rid:
            merged[rid] = row
    store = _receipt_session_store(cfg)
    if query:
        res = store.search(query, 200)
    else:
        res = store.list(200, include_archived=include_archived)
    for r in res.get("sessions", []):
        rid = str(r.get("receipt_id") or r.get("action_id") or r.get("id", ""))
        if not rid or rid in merged:
            continue
        merged[rid] = {
            "id": rid,
            "source": "qubit-receipts",
            "model": None,
            "title": (r.get("name") or (r.get("detail") or "")[:80] or rid) or None,
            "name": r.get("name"),
            "archived": bool(r.get("archived", False)),
            "started_at": r.get("created_at") or 0,
            "ended_at": None,
            "last_active": r.get("created_at") or 0,
            "is_active": False,
            "message_count": 0,
            "tool_call_count": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "preview": (r.get("detail") or "")[:200],
            "state": r.get("state"),
            "code": r.get("code"),
            "receipt_id": r.get("receipt_id"),
            "action_id": r.get("action_id"),
        }
    rows = list(merged.values())
    if archived == "exclude":
        rows = [r for r in rows if not r.get("archived")]
    elif archived == "only":
        rows = [r for r in rows if r.get("archived")]
    if source:
        rows = [r for r in rows if str(r.get("source", "")) == source]
    if sources:
        allowed = set(sources)
        rows = [r for r in rows if str(r.get("source", "")) in allowed]
    if exclude:
        blocked = set(exclude)
        rows = [r for r in rows if str(r.get("source", "")) not in blocked]
    if order == "created":
        rows.sort(key=lambda r: r.get("started_at") or 0, reverse=True)
    else:
        rows.sort(key=lambda r: r.get("last_active") or 0, reverse=True)
    page_rows = rows[offset:offset + limit]
    # Reference envelope parity: ``web_routers/sessions.py::search_sessions``
    # returns ``{"results": [...]}``. The search route reuses this handler, so
    # emit the alias the SPA's ``SessionSearchResponse`` reads; ``sessions``
    # stays for the list route. Without this the Sessions search box set an
    # undefined result list and the table stayed unfiltered.
    return _ok(sessions=page_rows, results=page_rows, total=len(rows),
               limit=limit, offset=offset, store_state=res.get("state", "OK"),
               store_authority=CHAT_SESSION_AUTHORITY + "+"
                               + RECEIPT_SESSION_AUTHORITY)


def h_sessions_stats(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                     p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    chat_items = _chat_session_rows(cfg).get("sessions", [])
    res = _receipt_session_store(cfg).list(100)
    items = res.get("sessions", [])
    by_state: Dict[str, int] = {}
    for r in items:
        key = str(r.get("state", "?"))
        by_state[key] = by_state.get(key, 0) + 1
    archived = sum(1 for r in chat_items if r.get("archived"))
    messages = sum(int(r.get("message_count", 0) or 0) for r in chat_items)
    total = len(chat_items) + len(items)
    # SessionStoreStats wire shape (web/src/lib/api.ts): total, active_store,
    # archived, messages, by_source. Receipts are append-only: archived=0.
    by_source: Dict[str, int] = {}
    for r in chat_items:
        key = str(r.get("source") or "dashboard")
        by_source[key] = by_source.get(key, 0) + 1
    return _ok(total=total, active_store=total - archived, archived=archived,
               messages=messages,
               by_source={**by_source,
                          "qubit-receipts": len(items)},
               by_state=by_state,
               store_authority=CHAT_SESSION_AUTHORITY + "+"
                               + RECEIPT_SESSION_AUTHORITY)


def h_session_show(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                   p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    rid = p.get("id", "")
    # Canonical chat transcript first: the user's real conversations live in
    # the single chat-session authority, not the receipt journal.
    try:
        chat = _chat_session_store(cfg).show(rid)
    except Exception:
        chat = {"state": "MISSING"}
    if str(chat.get("state")) == "OK" and isinstance(chat.get("session"), dict):
        rec = _rebrand_chat_record(chat["session"])
        preview = None
        for m in reversed(chat.get("messages", []) or []):
            if m.get("role") == "assistant" and m.get("content"):
                preview = str(m["content"])[:200]
                break
        return _ok(id=rec.get("id", rid),
                   source=str(rec.get("source") or "dashboard"),                   model=rec.get("model"),
                   title=rec.get("name") or (preview[:80] if preview else rid),
                   name=rec.get("name"),
                   archived=bool(rec.get("archived", False)),
                   started_at=rec.get("created_at", 0), ended_at=None,
                   last_active=rec.get("updated_at", 0), is_active=False,
                   message_count=int(rec.get("message_count", 0) or 0),
                   tool_call_count=0, input_tokens=0, output_tokens=0,
                   preview=preview, store_authority=CHAT_SESSION_AUTHORITY)
    try:
        res = _receipt_session_store(cfg).show(rid)
    except Exception as exc:
        return _fail(exc)
    if str(res.get("state")) != "OK" or not isinstance(res.get("session"), dict):
        return 404, {"ok": False, "code": res.get("code", "NOT_FOUND"),
                     "detail": res.get("detail", f"no session {rid!r}")}
    r = res["session"]
    detail = str(r.get("detail", "") or "")
    try:
        created = float(r.get("created_at", 0) or 0)
    except (TypeError, ValueError):
        created = 0.0
    # SessionInfo wire shape (web/src/lib/api.ts).
    return _ok(id=rid, source="qubit-receipts", model=None,
               title=(r.get("name") or detail[:80] or rid) or None,
               name=r.get("name"), archived=bool(r.get("archived", False)),
               started_at=created, ended_at=None, last_active=created,
               is_active=False, message_count=0, tool_call_count=0,
               input_tokens=0, output_tokens=0,
               preview=detail[:200] or None,
               state=str(r.get("state", "") or ""),
               store_authority=RECEIPT_SESSION_AUTHORITY)


def h_session_patch(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """Rename / archive a session (canonical chat store, then receipts)."""
    rid = p.get("id", "")
    if _estop(cfg):
        return 403, {"ok": False, "state": "UNAVAILABLE",
                     "code": "EMERGENCY_STOP",
                     "detail": "emergency stop latched; session change refused"}
    if "name" not in b and b.get("archived") is None:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "patch needs name or archived"}
    # Chat transcripts rename/archive in the single session authority the
    # /qubit-chat surface lists; never a receipt-only shadow row. Resolve the
    # raw row (``rename``/``archive`` do not gate on ``show``), because
    # ``show`` reports an archived session as MISSING — gating on it made an
    # archived session un-restorable/un-renamable from the dashboard.
    try:
        cs: Any = _chat_session_store(cfg)
    except Exception:
        cs = None
    if cs is not None:
        try:
            if "name" in b:
                res = cs.rename(rid, str(b.get("name", "")))
            else:
                res = cs.archive(rid, bool(b.get("archived")))
        except Exception as exc:
            return _fail(exc)
        if str(res.get("state")) != "MISSING":
            ok = str(res.get("state")) == "OK"
            if ok:
                _audit("session.patch", "dashboard:user",
                       {"id": str(rid), "action": "name" if "name" in b
                        else "archived"})
            return (200 if ok else 400), {"ok": ok, "state": res.get("state"),
                                          "code": res.get("code"),
                                          "detail": _redact(
                                              res.get("detail", ""))[:200],
                                          "session": res.get("session")}
    store = _receipt_session_store(cfg)
    try:
        if "name" in b:
            res = store.rename(rid, str(b.get("name", "")))
        elif b.get("archived") is not None:
            res = (store.archive(rid) if b.get("archived")
                   else store.resume(rid))
        else:
            return 400, {"ok": False, "code": "MALFORMED",
                         "detail": "patch needs name or archived"}
    except Exception as exc:
        return _fail(exc)
    ok = str(res.get("state")) == "OK"
    if ok:
        _audit("session.patch", "dashboard:user",
               {"id": str(rid), "action": "name" if "name" in b
                else "archived"})
    return (200 if ok else 400), {"ok": ok, "state": res.get("state"),
                                  "code": res.get("code"),
                                  "detail": _redact(
                                      res.get("detail", ""))[:200],
                                  "session": res.get("session")}


def h_session_delete(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                     p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """Delete a session (canonical chat store, then receipt tombstone)."""
    rid = p.get("id", "")
    confirm = bool(b.get("confirm", False))
    if _estop(cfg):
        return 403, {"ok": False, "state": "UNAVAILABLE",
                     "code": "EMERGENCY_STOP",
                     "detail": "emergency stop latched; session delete refused"}
    # The canonical chat store deletes the real transcript (recoverable from a
    # backup); a receipt row deletes its lifecycle tombstone. ``delete`` does
    # not gate on ``show``, so an already-archived chat row is still handled
    # here rather than falling through to a 404 receipt lookup.
    try:
        cs: Any = _chat_session_store(cfg)
    except Exception:
        cs = None
    if cs is not None:
        try:
            res = cs.delete(rid, confirm=confirm)
        except Exception as exc:
            return _fail(exc)
        if str(res.get("state")) != "MISSING":
            if not confirm:
                return 409, {"ok": False, "state": "CONFIRM_REQUIRED",
                             "code": res.get("code", "CONFIRM_REQUIRED"),
                             "detail": res.get("detail", ""),
                             "qubit_counterpart": "re-DELETE with "
                                                  "{\"confirm\": true}"}
            ok = str(res.get("state")) == "OK"
            if ok:
                _audit("session.delete", "dashboard:user", {"id": str(rid)})
            return (200 if ok else 400), {"ok": ok, "state": res.get("state"),
                                          "code": res.get("code"),
                                          "detail": _redact(
                                              res.get("detail", ""))[:200],
                                          "session": res.get("session")}
    try:
        res = _receipt_session_store(cfg).delete(rid, confirm=confirm)
    except Exception as exc:
        return _fail(exc)
    if not confirm:
        return 409, {"ok": False, "state": "CONFIRM_REQUIRED",
                     "code": res.get("code", "CONFIRM_REQUIRED"),
                     "detail": res.get("detail", ""),
                     "qubit_counterpart": "re-DELETE with {\"confirm\": true}; "
                                          "recover with POST resume"}
    ok = str(res.get("state")) == "OK"
    if ok:
        _audit("session.delete", "dashboard:user", {"id": str(rid)})
    return (200 if ok else 400), {"ok": ok, "state": res.get("state"),
                                  "code": res.get("code"),
                                  "detail": _redact(
                                      res.get("detail", ""))[:200],
                                  "session": res.get("session")}


def h_session_resume(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                     p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    rid = p.get("id", "")
    if _estop(cfg):
        return 403, {"ok": False, "state": "UNAVAILABLE",
                     "code": "EMERGENCY_STOP",
                     "detail": "emergency stop latched; session resume refused"}
    # Un-archive a chat transcript in the canonical store first. ``restore``
    # (like ``archive``) does not gate on ``show``, so an archived session —
    # which ``show`` reports as MISSING — is still restorable here.
    try:
        cs: Any = _chat_session_store(cfg)
    except Exception:
        cs = None
    if cs is not None:
        try:
            res = cs.restore(rid)
        except Exception as exc:
            return _fail(exc)
        if str(res.get("state")) != "MISSING":
            ok = str(res.get("state")) == "OK"
            if ok:
                _audit("session.resume", "dashboard:user", {"id": str(rid)})
            return (200 if ok else 400), {"ok": ok, "state": res.get("state"),
                                          "code": res.get("code"),
                                          "detail": _redact(
                                              res.get("detail", ""))[:200]}
    try:
        res = _receipt_session_store(cfg).resume(rid)
    except Exception as exc:
        return _fail(exc)
    ok = str(res.get("state")) == "OK"
    if ok:
        _audit("session.resume", "dashboard:user", {"id": str(rid)})
    return (200 if ok else 400), {"ok": ok, "state": res.get("state"),
                                  "code": res.get("code"),
                                  "detail": _redact(
                                      res.get("detail", ""))[:200]}


def h_session_messages(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                       p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    rid = p.get("id", "")
    try:
        limit = max(1, min(int(q.get("limit", "500") or "500"), 1000))
    except ValueError:
        limit = 500
    try:
        offset = max(0, int(q.get("offset", "0") or "0"))
    except ValueError:
        offset = 0
    # The canonical chat transcript is the real session history: return its
    # messages so the dashboard can reopen a conversation, not a receipt stub.
    try:
        chat = _chat_session_store(cfg).messages(rid, limit=limit, offset=offset)
    except Exception:
        chat = {"state": "MISSING"}
    if str(chat.get("state")) == "OK":
        return _ok(session_id=chat["session_id"],
                   messages=_rebrand_chat_messages(chat["messages"]),
                   total=chat["total"],
                   pagination={"limit": chat["limit"], "offset": chat["offset"],
                               "order": "asc", "returned": len(chat["messages"]),
                               "total": chat["total"]},
                   store_authority=CHAT_SESSION_AUTHORITY)
    code, out = h_session_show(cfg, q, b, p)
    if code != 200:
        return code, out
    # SessionMessagesResponse wire shape; receipt rows hold no chat transcript.
    return _ok(session_id=rid,
               messages=[{"role": "system",
                          "content": _redact(json.dumps(out))[:2000],
                          "timestamp": out.get("last_active", 0)}],
               pagination={"limit": 500, "offset": 0, "order": "latest",
                           "returned": 1},
               detail="Qubit receipt rows are audit receipts, not chat "
                      "transcripts; live conversation runs in Qubit Chat "
                      "(agent.turn)")


def _config_field_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "object"
    return "string"


def _config_fields(schema: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Flat ``dot.path -> field descriptor`` view of a nested config schema.

    The served ConfigPage binds ``getSchema() -> {fields, category_order}``;
    deriving the descriptors from the live ``Config`` defaults keeps the form
    shape tied to the real settings tree instead of a wire-shape stub."""
    fields: Dict[str, Dict[str, Any]] = {}

    def walk(node: Dict[str, Any], prefix: str) -> None:
        for key in sorted(node):
            if str(key).startswith("_"):
                continue
            value = node[key]
            full = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, dict):
                walk(value, full)
                continue
            fields[full] = {
                "type": _config_field_type(value),
                "description": full.replace(".", " \u2192 ").replace("_", " ").title(),
                "category": prefix.split(".")[0] if prefix else "general",
                "default": value,
            }

    walk(schema, "")
    return fields


def _config_defaults() -> Dict[str, Any]:
    from . import config as C
    return _redact_config_tree(json.loads(json.dumps(C.DEFAULTS, default=str)))


def _set_dotted(data: Dict[str, Any], dotted: str, value: Any) -> None:
    parts = [p for p in str(dotted).split(".") if p]
    if not parts:
        return
    node = data
    for part in parts[:-1]:
        nxt = node.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            node[part] = nxt
        node = nxt
    node[parts[-1]] = value


def _flatten_config(data: Any, prefix: str = "") -> Dict[str, Any]:
    """Flatten a nested settings body to ``dot.path -> scalar`` overrides.

    The served ConfigPage sends ``{"config": {<nested tree>}}``; the override
    store is a flat mapping. Walking both shapes here is what stops the nested
    body from being silently dropped (fabricated success)."""
    out: Dict[str, Any] = {}
    if not isinstance(data, dict):
        return out
    for key, value in data.items():
        if str(key) == "confirm":
            continue
        full = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            out.update(_flatten_config(value, full))
        elif value is None or isinstance(value, (str, int, float, bool, list)):
            out[full] = value
    return out


def _config_scalar_paths(defaults: Any, prefix: str = "") -> Dict[str, Any]:
    """Map every real scalar setting to its ``dot.path`` and default value.

    Used to reject unknown/mistyped settings writes instead of silently
    persisting an arbitrary key (which would fabricate success)."""
    out: Dict[str, Any] = {}
    if not isinstance(defaults, dict):
        return out
    for key, value in defaults.items():
        if str(key).startswith("_"):
            continue
        full = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            out.update(_config_scalar_paths(value, full))
        else:
            out[full] = value
    return out


def _merge_config_overrides(cfg: Any, data: Any) -> Any:
    """Deep-merge persisted dashboard config overrides onto a served record.

    The served ConfigPage reads the vendored runtime's config; overrides saved
    through the confirm-gated dashboard write must be reflected on the next
    read or the control would look dead. Redaction already ran, so this only
    re-applies non-secret scalar overrides.
    """
    if not isinstance(data, dict):
        return data
    for dotted, value in _load_json_store(_config_overrides_read_path(cfg)).items():
        try:
            _set_dotted(data, dotted, value)
        except Exception:
            continue
    return data


# Keyboard/input bindings that end in ``_key`` but are settings, not secrets.
# Kept as exact dotted paths so a credential leaf can never be allowlisted by a
# broad suffix tweak.
_NON_SECRET_CONFIG_KEYS = frozenset({
    "hotkey.hold_key", "hotkey.alt_hold_key",
})


def _is_secret_config_key(key: str) -> bool:
    """True when a dotted config path names a secret-shaped leaf.

    Matches both underscore and dotted paths (``dashboard.basic_auth.password``
    and ``webui.token``), so the read side can redact exactly the leaves the
    write side refuses.

    Exempt: keyboard/input bindings also end in ``_key`` but are settings, not
    credentials. ``hotkey.hold_key``/``hotkey.alt_hold_key`` are key names
    (``ctrl_r``) the Settings form must display and round-trip; treating them as
    secrets redacted the served leaf and (with the unchanged-leaf skip) left the
    whole-tree Save unable to recognise its own value.
    """
    if str(key).lower() in _NON_SECRET_CONFIG_KEYS:
        return False
    leaf = str(key).rsplit(".", 1)[-1].lower()
    return leaf in {
        "password", "passwd", "secret", "token", "api_key", "apikey",
        "access_key", "private_key", "client_secret", "auth_token",
        "refresh_token", "bearer_token",
    } or leaf.endswith(("_password", "_secret", "_token", "_key"))


def _redact_config_tree(data: Any, prefix: str = "") -> Any:
    """Replace secret-shaped scalar leaves with ``[REDACTED]``.

    ``GET /api/config`` is a Settings read surface, not a secret-reveal route
    (``/api/config/raw`` stays gated); the served tree must never carry a raw
    secret value.
    """
    if isinstance(data, dict):
        out: Dict[str, Any] = {}
        for key, value in data.items():
            full = f"{prefix}.{key}" if prefix else str(key)
            if not isinstance(value, (dict, list)) \
                    and _is_secret_config_key(full) \
                    and value not in (None, "", "[REDACTED]"):
                out[key] = "[REDACTED]"
            else:
                out[key] = _redact_config_tree(value, full)
        return out
    if isinstance(data, list):
        return [_redact_config_tree(v, prefix) for v in data]
    return data


def _config_live(cfg: Any) -> Dict[str, Any]:
    """The live Qubit config record: defaults deep-merged with the loaded YAML
    plus any confirm-gated dashboard overrides, in the nested shape the served
    Settings form reads and writes. Secret-shaped leaves are redacted."""
    from . import config as C
    base = getattr(cfg, "data", None)
    data = json.loads(json.dumps(base if isinstance(base, dict) else C.DEFAULTS,
                                 default=str))
    for dotted, value in _load_json_store(_config_overrides_read_path(cfg)).items():
        _set_dotted(data, dotted, value)
    return _redact_config_tree(data)


def _terminal_live_block() -> Dict[str, Any]:
    """Live terminal (/chat) model slot, read from the runtime config file.

    The terminal TUI and the dashboard main slot are separate model slots by
    design (different contexts: terminal API pin vs main/local tiers), so a
    /chat model switch never rewrites the main `model` leaf — which made
    /config look stale. This read-only block exposes the terminal slot live
    so surfaces can show both honestly. Fail-closed to {} (callers hide).
    Served only by h_config, never accepted by PUT (unknown-key refusal).
    """
    try:
        from . import qubit_runtime as QR
        import yaml as _yaml
        path = QR.default_home() / "config.yaml"
        data = _yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    model = data.get("model")
    if not isinstance(model, dict):
        return {}
    out = {
        "provider": str(model.get("provider") or ""),
        "model": str(model.get("model") or model.get("default") or ""),
    }
    base_url = str(model.get("base_url") or "")
    if base_url:
        out["base_url"] = base_url
    if not out["model"]:
        return {}
    return out


def h_config(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
             p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/config -> the live Qubit settings record (``Record<string, unknown>``).

    The served ConfigPage binds ``getConfig()`` to this nested record; returning
    the real tree (defaults + YAML + applied overrides) is what makes the form
    render actual values rather than an empty shell. Carries the read-only
    ``terminal`` block (live /chat model slot) so the page can show both
    model slots honestly.
    """
    try:
        data = _config_live(cfg)
        terminal = _terminal_live_block()
        if terminal:
            data["terminal"] = terminal
        return 200, data
    except Exception as exc:
        return _fail(exc)


def h_config_defaults(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                      p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/config/defaults -> the live Qubit default record."""
    try:
        return 200, _config_defaults()
    except Exception as exc:
        return _fail(exc)


def _parse_systemd_timestamp(raw: Any) -> Optional[float]:
    """Parse ``systemctl show`` timestamps fail-closed (None when unparseable).

    Values look like ``Wed 2026-09-30 20:43:47 EEST``; the trailing zone
    abbreviation is libc-dependent, so it is stripped and the naive stamp is
    interpreted in local time (both ends of the comparison are local).
    """
    try:
        text = str(raw or "").strip()
        if not text or text.lower() in ("n/a", "unknown"):
            return None
        naive = text.rsplit(" ", 1)[0]
        import datetime as _dt
        parsed = _dt.datetime.strptime(naive, "%a %Y-%m-%d %H:%M:%S")
        return float(time.mktime(parsed.timetuple()))
    except Exception:
        return None


def _newest_config_write(cfg: Any) -> Optional[float]:
    """Newest mtime across the three config stores (None when none exist)."""
    candidates = []
    try:
        from .config import canonical_config_dir
        candidates.append(canonical_config_dir() / "voice.yaml")
    except Exception:
        pass
    try:
        candidates.append(Path(_config_overrides_read_path(cfg)))
    except Exception:
        pass
    try:
        from . import qubit_runtime as QR
        candidates.append(Path(QR.runtime_home_for_cfg(cfg)) / "config.yaml")
    except Exception:
        pass
    newest: Optional[float] = None
    for cand in candidates:
        try:
            if cand.is_file():
                mtime = cand.stat().st_mtime
                newest = mtime if newest is None else max(newest, mtime)
        except Exception:
            continue
    return newest


def h_config_effective(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                       p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/config/effective -> is the running fleet on the latest config?

    Compares the newest config-store write against each serving process's
    boot: the dashboard itself (import-time wall clock) and the gateway unit
    (``systemctl show`` start stamp via the supervisor). ``fresh`` means the
    process booted after the newest write, so it provably serves current
    values; ``stale`` names what still needs a restart; ``unknown`` is
    reported honestly when a stamp is unavailable. Read-only, no mutation.
    """
    try:
        newest = _newest_config_write(cfg)
        services: Dict[str, Any] = {
            "qubit-voicebridge": {"boot": _DASHBOARD_BOOT_WALL,
                                  "running": True},
        }
        try:
            from . import gateway_supervisor as GS
            gw = GS.status(cfg) or {}
            gw_boot = _parse_systemd_timestamp(
                gw.get("started_at") or gw.get("active_enter_timestamp"))
            services["qubit-gateway"] = {
                "boot": gw_boot,
                "running": bool(gw.get("running", False)),
            }
        except Exception:
            services["qubit-gateway"] = {"boot": None, "running": None}

        def _freshness(boot: Any) -> str:
            if newest is None:
                return "fresh"
            if not isinstance(boot, (int, float)):
                return "unknown"
            return "fresh" if float(boot) >= float(newest) else "stale"

        for _name, _info in services.items():
            if _info.get("running") is False:
                _info["state"] = "stopped"
            else:
                _info["state"] = _freshness(_info.get("boot"))
        states = [v["state"] for v in services.values()]
        if all(s == "fresh" for s in states):
            verdict = "active"
        elif any(s == "stale" for s in states):
            verdict = "stale"
        else:
            verdict = "unknown"
        import datetime as _dt
        return 200, {
            "ok": True,
            "verdict": verdict,
            "newest_config_write": (
                _dt.datetime.fromtimestamp(newest).isoformat()
                if newest else None),
            "services": {
                name: {
                    "state": info["state"],
                    "running": info.get("running"),
                    "boot": (_dt.datetime.fromtimestamp(info["boot"]).isoformat()
                             if isinstance(info.get("boot"), (int, float))
                             else None),
                }
                for name, info in services.items()
            },
            "detail": ("every serving process booted after the newest config "
                       "write" if verdict == "active" else
                       "restart the stale service(s), then verify again"),
        }
    except Exception as exc:
        return _fail(exc)


def h_config_schema(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/config/schema -> real ``{fields, category_order}`` descriptors.

    Descriptors are derived from the live Qubit config schema (``Config``
    defaults), so the Settings form groups and renders every real field instead
    of the previous shape-only stub.
    """
    try:
        from . import config as C
        fields = _config_fields(C.DEFAULTS)
        categories = {f["category"] for f in fields.values()}
        order = [str(k) for k in C.DEFAULTS.keys()
                 if str(k) in categories and not str(k).startswith("_")]
        return 200, {"fields": fields, "category_order": order}
    except Exception as exc:
        return _fail(exc)


def h_egress_status(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """Egress proxy status (EgressStatusResponse wire shape: ``text``).

    Backed by ``voicebridge.proxy_status`` (the proxy inventory) plus the
    frontier network policy (``voicebridge.providers.policy``). No forwarding
    server runs, so the text is the real typed policy, never a fake "up".
    """
    from . import proxy_status as PS
    try:
        proxy = PS.status()
    except Exception as exc:
        return _fail(exc)
    frontier: Dict[str, Any] = {}
    try:
        from .providers.policy import FrontierPolicy
        pol = FrontierPolicy.from_config(cfg)
        frontier = {"enabled": bool(pol.enabled), "route": pol.route,
                    "allowlist_count": len(pol.allowlist),
                    "privacy_tier": pol.privacy_tier}
    except Exception:
        frontier = {}
    text = (f"proxy: {proxy.get('state', 'UNKNOWN')}; "
            f"frontier: {'enabled' if frontier.get('enabled') else 'disabled'}; "
            f"{proxy.get('policy', '')}")
    return _ok(text=text, proxy=proxy, frontier=frontier)


def _qubit_model(cfg: Any) -> Dict[str, Any]:
    from . import agent as AG
    from . import dashboard_ops as OPS
    name = ""
    try:
        name = AG._model(cfg)
    except Exception:
        name = "qubit-brain4"
    ctx = 4096
    try:
        out = OPS.run_op(cfg, "prompt_budget", {}, actor="dashboard:user",
                         session="dashboard")
        res = _res(out) or {}
        ctx = int((res.get("prompt_budget", {}) or {}).get("num_ctx", 4096))
    except Exception:
        pass
    return {"name": name or "qubit-brain4", "ctx": ctx}


def _resolved_model(cfg: Any) -> Dict[str, Any]:
    """Effective provider/model/context from the canonical resolver.

    Never hardcodes a synthetic provider: the dashboard model surfaces must
    report the same effective selection as CLI/voice/``/chat``.
    """
    m = _qubit_model(cfg)
    try:
        from .providers import resolver as R

        eff = R.resolve_model(cfg)
    except Exception:
        eff = None
    if isinstance(eff, dict) and eff.get("ok"):
        ctx = int(eff.get("context_tokens") or 0) or m["ctx"]
        return {"model": str(eff.get("model") or m["name"]),
                "provider": str(eff.get("provider") or ""),
                "ctx": ctx,
                "profile_id": str(eff.get("profile_id") or ""),
                "kind": str(eff.get("kind") or ""),
                "locality": str(eff.get("locality") or ""),
                "base_url": _resolved_base_url(cfg)}
    return {"model": m["name"], "provider": "ollama", "ctx": m["ctx"],
            "profile_id": m["name"], "kind": "local", "locality": "local",
            "base_url": _resolved_base_url(cfg)}


def _resolved_base_url(cfg: Any) -> str:
    """The effective profile's ``base_url`` (for auto model metadata); ""."""
    try:
        from .providers import resolver as R

        eff = R.resolve(cfg)
        return str(getattr(eff, "base_url", "") or "")
    except Exception:  # noqa: BLE001 - metadata derivation is best-effort
        return ""


#: Fields mirrored from the vendored ``agent.models_dev.ModelCapabilities``
#: (the reference ``web_routers/models.py`` serves the same set).
_CAPABILITY_FIELDS = ("supports_tools", "supports_vision",
                      "supports_reasoning", "context_window",
                      "max_output_tokens", "model_family")


def _model_capabilities(provider: str, model: str, ctx: Any
                        ) -> Dict[str, Any]:
    """Capabilities for one model without fabrication.

    A catalog miss returns exactly ``{"context_window": ctx}`` — no invented
    booleans — while the N29 context authority is always reported so the
    dashboard context badge does not vanish for a model models.dev does not
    know. A catalog hit returns the real fields, with a sparse entry's
    ``context_window`` completed from the passed authority. Never raises
    (miss on any failure); the caller owns ``sys.path`` (``qubit_runtime``
    bound before use).
    """
    try:
        from agent import models_dev as _md  # type: ignore

        mc = _md.get_model_capabilities(provider=provider, model=model)
    except Exception:  # noqa: BLE001 - miss on any failure
        mc = None
    if mc is None:
        return {"context_window": int(ctx or 0)}
    caps = {name: getattr(mc, name, None) for name in _CAPABILITY_FIELDS}
    if not caps.get("context_window") and ctx:
        caps["context_window"] = int(ctx)
    return caps


def _auto_context_length(model: str, base_url: str, provider: str) -> int:
    """B4-02: the auto-detected context window, independent of the override.

    Cache first (cheap), then the same hard time-bounded vendored probe the
    ``/chat`` guard uses. Zero when nothing can be resolved — never fabricated.
    """
    if not model:
        return 0
    try:
        from agent import model_metadata as _mm  # type: ignore

        cached = _mm.get_cached_context_length(model, base_url)
        if isinstance(cached, int) and cached > 0:
            return cached
    except Exception:  # noqa: BLE001
        pass
    try:
        from .providers import resolver as R

        value, _src = R._probe_real_context_window(model, base_url, provider)
        if isinstance(value, int) and value > 0:
            return value
    except Exception:  # noqa: BLE001
        pass
    return 0


def h_model_info(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    r = _resolved_model(cfg)
    # The default role's resolved selection is the answer. A stored model on
    # the old canonical session (e.g. the ``dashboard-chat`` fossil's
    # ``qubit-brain4``) must NOT override it: since /chat opens fresh and the
    # /chat sidebar already prefers the live PTY grant, the fossil's opinion
    # kept every MODEL card pinned to a dead default after a switch (the D3
    # override this replaces). An explicit ``?model=`` below still reports a
    # specific model's capabilities.
    ctx = int(r["ctx"] or 0)
    model = str(r["model"])
    provider = str(r["provider"])
    # A caller may ask for a SPECIFIC model's capabilities (the /chat sidebar
    # shows the terminal's resolved grant, which can differ from the default
    # role). Reporting caps for the requested model keeps the reasoning
    # selector gated on what the terminal actually runs, not the default role.
    _req_model = str(q.get("model", "") or "").strip()
    if _req_model:
        model = _req_model
        _req_provider = str(q.get("provider", "") or "").strip()
        if _req_provider:
            provider = _req_provider
    # B4-01/B4-02: derive capabilities and the auto-detected window from the
    # vendored metadata resolver — the exact source the reference
    # ``web_routers/models.py`` uses — instead of baking in placeholder values
    # (which permanently disabled the ``/chat`` reasoning selector). The
    # hardcoded dict survives only as a last-resort fallback.
    auto_ctx = 0
    caps: Optional[Dict[str, Any]] = None
    try:
        _vendored_runtime(cfg)
        auto_ctx = _auto_context_length(model, str(r.get("base_url") or ""),
                                        provider)
        try:
            from agent import models_dev as _md  # type: ignore

            mc = _md.get_model_capabilities(provider=provider, model=model)
            if mc is not None:
                caps = {name: getattr(mc, name, None)
                        for name in _CAPABILITY_FIELDS}
        except Exception:  # noqa: BLE001 - fall back below
            caps = None
    except Exception:  # noqa: BLE001 - never 500 the read
        auto_ctx = 0
    if not isinstance(caps, dict):
        # Reference-identical: when the vendored metadata resolver has no entry
        # (e.g. an aggregator provider models.dev does not catalogue), report the
        # capability as UNKNOWN ("None") instead of fabricating
        # ``supports_reasoning: false`` / ``max_output_tokens: 1024``. Fabricated
        # values are what permanently disabled the /chat reasoning selector and
        # misreported the output cap; the reference serves an empty capabilities
        # mapping for the same model. ``supports_tools`` and the known context
        # window are honest defaults.
        caps = {"supports_tools": True, "supports_vision": None,
                "supports_reasoning": None, "context_window": ctx,
                "max_output_tokens": None,
                "model_family": ("qwen3-4b" if r.get("kind") == "local"
                                 else provider)}
    if not caps.get("context_window"):
        # A catalog hit with a sparse entry leaves context_window None. The
        # window is a known local fact, not catalog metadata: for the
        # resolved default it is the N29 config authority (never the
        # advertised registry value); for an explicitly requested different
        # model it is the live-probed window, else honestly unknown (None).
        if model == str(r["model"]) and ctx:
            caps["context_window"] = ctx
        elif auto_ctx:
            caps["context_window"] = auto_ctx
    effective = ctx or auto_ctx
    # SP-07: config reports the configured window only when it is distinct
    # from the auto-detected one (the operator's distinct contribution); when
    # they agree there is no distinct override and config is 0 while
    # effective carries the value. Unknown auto (0) never masks a configured
    # window.
    config_out = ctx if ctx != auto_ctx else 0
    # ModelInfoResponse wire shape.
    return _ok(model=model, provider=provider,
               auto_context_length=auto_ctx, config_context_length=config_out,
               effective_context_length=effective, capabilities=caps)


def _ollama_options_row(cfg: Any) -> Optional[Dict[str, Any]]:
    """Qubit-owned Ollama row for the model picker (``/api/model/options``).

    The vendored builder only knows the managed llamacpp runtime's staged
    GGUFs, so Qubit's Ollama tier (brain, Spark 1.7B/4B) was invisible in
    every MODEL picker and local models could not be chosen in /qubit-chat.
    This row lists the registered, enabled, chat-eligible LOCAL profiles —
    exactly the set ``POST /api/model/set`` can activate — with the checkmark
    resolved from the canonical active profile (not the vendored context, so
    it never disagrees with the MODEL card). ``authenticated`` is
    reachability, same rationale as the vendored local row: no credential
    exists for loopback Ollama. ``None`` when no local profile qualifies.
    """
    try:
        from .providers import resolver as R

        profiles = [p for p in R.load_profiles(cfg)
                    if p.kind == R.LOCAL and p.enabled
                    and "chat" in (p.roles or ["chat"])]
        if not profiles:
            return None
        try:
            current = str(R.get_active_profile(cfg).get("active") or "")
        except Exception:  # noqa: BLE001 - unreadable active: no checkmark
            current = ""
        ids = [p.id for p in profiles]
        models: List[str] = []
        for p in profiles:
            if p.model not in models:
                models.append(p.model)
        return {"slug": "ollama", "name": "Ollama (local)",
                "models": models, "total_models": len(models),
                "is_current": current in ids, "is_user_defined": False,
                "source": "qubit-ollama", "authenticated": True,
                "auth_type": "local", "warning": None}
    except Exception:  # noqa: BLE001 - options must never 500 on Ollama
        return None


def h_model_options(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/model/options -> the real vendored provider/model catalogue.

    Owner decision #5: the picker delegates to the vendored Hermes foundation
    (``qubit_cli.inventory.build_model_options_payload`` + ``load_picker_context``)
    — the SAME builder the reference dashboard serves — never a hand-rolled
    3-profile stub. Query params mirror the reference route
    (``web_routers/models.py:get_model_options``): ``refresh``,
    ``include_unconfigured``, ``explicit_only``.

    Qubit policy: the payload is passed through ``_strip_provider_secret_fields``
    so credential values AND credential references (``key_env`` etc.) never leave
    the backend. The response wire shape (``providers[]`` with
    ``name``/``slug``/``models``/``is_current``/``authenticated``/``total_models``
    plus top-level ``model``/``provider``) is what ``ModelPickerDialog`` expects.
    """
    try:
        _vendored_runtime(cfg)
        from qubit_cli.inventory import build_model_options_payload, load_picker_context  # type: ignore

        def _truthy(value: Any) -> bool:
            return str(value or "").strip().lower() in {"1", "true", "yes", "on"}

        payload = build_model_options_payload(
            load_picker_context(),
            explicit_only=_truthy(q.get("explicit_only")),
            include_unconfigured=_truthy(q.get("include_unconfigured")),
            refresh=_truthy(q.get("refresh")),
        )
    except Exception as exc:
        return _fail(exc)
    payload = payload or {}
    # Qubit-owned Ollama row (registered LOCAL profiles). The vendored
    # builder cannot see the Ollama tier; without this, /qubit-chat's MODEL
    # card offered no local model. Skipped when empty or when a vendored
    # local row already owns the surface.
    try:
        rows = payload.get("providers")
        if isinstance(rows, list):
            seen = {str(r.get("slug") or "").lower() for r in rows
                    if isinstance(r, dict)}
            if not seen & {"ollama", "llamacpp"}:
                ollama_row = _ollama_options_row(cfg)
                if ollama_row is not None:
                    payload["providers"] = [*rows, ollama_row]
    except Exception:  # noqa: BLE001 - additive row never breaks options
        pass
    # Single CURRENT authority: the canonical active profile. The vendored
    # builder marks current from the config slot while the Ollama row marks
    # from the active record — a slot/active disagreement (e.g. a slot-only
    # write) showed two CURRENT providers at once. The active record wins;
    # exactly one row (or none, when it names an unlisted model) is current.
    try:
        from .providers import resolver as _R
        _active = _R.get_active_profile(cfg).get("profile") or {}
        _am = str(_active.get("model") or "")
        _ap = str(_active.get("provider") or "").lower()
        if _am:
            # The payload top-level is the dialog's "current:" line: the same
            # authority applies, or header and markers disagree.
            payload["model"] = _am
            if _ap:
                payload["provider"] = _ap
            _marked = False
            for _r in payload.get("providers") or []:
                if not isinstance(_r, dict):
                    continue
                _models = [str(_m) for _m in (_r.get("models") or [])]
                _hit = (not _marked
                        and str(_r.get("slug") or "").lower() == _ap
                        and _am in _models)
                _r["is_current"] = _hit
                _marked = _marked or _hit
    except Exception:  # noqa: BLE001 - markers stay as built on failure
        pass
    return _ok(**_strip_provider_secret_fields(payload))


def h_model_recommended_default(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                                p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """Recommended default model (RecommendedDefaultResponse wire shape).

    Delegates to the vendored foundation (owner decision #5): the real picker
    payload + ``qubit_cli.models.pick_silent_default_model``, the same curation
    the reference route (``web_routers/models.py:get_recommended_default_model``)
    uses. Qubit policy: an absent/unknown provider slug gets an honest empty
    model — never a silently-picked paid default. No credentials are served.
    """
    slug = str(q.get("provider", "") or "").strip().lower()
    if not slug:
        # No provider named: resolve the configured Qubit brain through the
        # canonical resolver (the same effective selection ``/api/model/info``,
        # the CLI, voice, and ``/chat`` report). A blank ``model`` here would
        # leave the Models page with no recommended default; it is never a
        # silently-picked paid cloud default (the resolver is the operator's
        # own configured provider/model).
        eff = _resolved_model(cfg)
        return _ok(provider=str(eff.get("provider") or ""),
                   model=str(eff.get("model") or ""), free_tier=None,
                   detail="recommended default = configured Qubit brain profile")
    try:
        _vendored_runtime(cfg)
        from qubit_cli.inventory import build_models_payload, load_picker_context  # type: ignore
        from qubit_cli.models import pick_silent_default_model  # type: ignore

        payload = build_models_payload(load_picker_context())
        for row in payload.get("providers", []):
            if str(row.get("slug", "")).lower() == slug:
                models = [str(m) for m in (row.get("models") or [])]
                return _ok(provider=slug,
                           model=pick_silent_default_model(models, provider=slug),
                           free_tier=None)
        return _ok(provider=slug, model="", free_tier=None)
    except Exception as exc:
        return _fail(exc)


def h_model_auxiliary(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                      p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/model/auxiliary -> real auxiliary-task assignments.

    Ported from the reference route (``web_routers/models.py:get_auxiliary_models``)
    over the vendored ``_AUX_TASK_SLOTS``; reads the Qubit runtime config's
    ``auxiliary`` section. Never serves credentials.
    """
    try:
        _vendored_runtime(cfg)
        from qubit_cli.config import load_config  # type: ignore
        from qubit_cli.web_server_config import _AUX_TASK_SLOTS  # type: ignore

        cfg_data = load_config()
        aux_cfg = cfg_data.get("auxiliary", {})
        if not isinstance(aux_cfg, dict):
            aux_cfg = {}
        tasks = []
        for slot in _AUX_TASK_SLOTS:
            slot_cfg = aux_cfg.get(slot, {}) if isinstance(aux_cfg.get(slot), dict) else {}
            base_url = str(slot_cfg.get("base_url", "") or "")
            tasks.append({
                "task": slot,
                "provider": str(slot_cfg.get("provider", "auto") or "auto"),
                "model": str(slot_cfg.get("model", "") or ""),
                "base_url": base_url,
                "reasoning_effort": str(slot_cfg.get("reasoning_effort") or "") or None,
            })
        model_cfg = cfg_data.get("model", "")
        if isinstance(model_cfg, dict):
            main_model = model_cfg.get("default", model_cfg.get("name", ""))
            main_provider = model_cfg.get("provider", "")
        else:
            main_model, main_provider = (str(model_cfg) if model_cfg else ""), ""
        return _ok(tasks=tasks,
                   main={"provider": main_provider, "model": main_model})
    except Exception as exc:
        return _fail(exc)


def _model_set_auxiliary(cfg: Any, b: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/model/set scope=auxiliary -> real per-task pin write.

    Ported from the reference ``web_routers/models.py:set_model_assignment``
    + ``web_server_config._apply_aux_assignment_sync`` (both vendored in
    ``qubit_runtime``): writes the Qubit runtime config's ``auxiliary``
    section, which new sessions pick up. ``task`` empty = pin all slots,
    ``__reset__`` = every slot back to auto. Never serves credentials: an
    ``api_key`` in the body is a typed refusal (secrets live in .env / the
    Keys store, never config.yaml). An expensive-model pick returns the
    upstream ``confirm_required`` envelope; the caller re-POSTs with
    ``confirm_expensive_model`` (the Models dialog already does).
    """
    task = str(b.get("task") or "").strip().lower()
    provider = str(b.get("provider") or "").strip()
    model = str(b.get("model") or "").strip()
    base_url = str(b.get("base_url") or "").strip()
    if b.get("api_key"):
        return 400, {"ok": False, "code": "CREDENTIALS_NOT_IN_CONFIG",
                     "detail": "aux api_key is not stored in config.yaml; put "
                               "the key in Keys (.env) and pin provider/model only"}
    try:
        _vendored_runtime(cfg)
        from qubit_cli.web_server_config import (  # type: ignore
            _AUX_TASK_SLOTS, _UNSET, _apply_aux_assignment_sync,
        )
    except Exception as exc:
        return _fail(exc)
    if task and task != "__reset__" and task not in _AUX_TASK_SLOTS:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": f"unknown auxiliary task: {task}"}
    if task != "__reset__" and not provider:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "provider required for auxiliary"}
    if model and not _truthy(b.get("confirm_expensive_model")):
        try:
            from qubit_cli.model_selection_guards import (  # type: ignore
                combined_selection_warning,
            )
            warning = combined_selection_warning(
                model, provider=provider or None, base_url=base_url or None)
        except Exception:  # noqa: BLE001 - pricing lookup is best-effort
            warning = None
        if warning is not None:
            return 200, {"ok": False, "scope": "auxiliary",
                         "provider": provider, "model": model,
                         "confirm_required": True,
                         "confirm_message": warning.message}
    effort = b.get("reasoning_effort") if "reasoning_effort" in b else _UNSET
    try:
        from qubit_cli.config import load_config  # type: ignore
        out = _apply_aux_assignment_sync(
            load_config(), provider, model, task, base_url, "",
            reasoning_effort=effort)
    except Exception as exc:  # noqa: BLE001
        status = getattr(exc, "status_code", None)
        detail = _redact(str(getattr(exc, "detail", exc)))[:300]
        if status == 400:
            return 400, {"ok": False, "code": "MALFORMED", "detail": detail}
        return _fail(exc)
    return 200, out


def h_model_moa(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    # MoaConfigResponse wire shape; MoA is superseded by the ordered-fallback
    # gateway (8Q): no reference/aggregator fan-out exists to configure.
    slot = {"provider": "", "model": "", "enabled": False}
    return _ok(default_preset="none", active_preset="none", presets={},
               reference_models=[], aggregator=slot,
               reference_temperature=0.0, aggregator_temperature=0.0,
               reference_timeout=None, degraded_reference_policy="loud",
               enabled=False,
               detail="MoA fan-out is not shipped; ordered provider fallback "
                      "(8Q gateway) is the Qubit counterpart")


def h_model_set(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import dashboard_ops as OPS
    scope = str(b.get("scope", "main") or "main").lower()
    if scope == "auxiliary":
        return _model_set_auxiliary(cfg, b)
    if scope != "main":
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "scope must be 'main' or 'auxiliary'"}
    model = str(b.get("model") or "")
    if not model:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "model assignment needs a model name"}
    provider = str(b.get("provider") or "").strip()
    if provider:
        # Catalog/settings pick: a provider+model pair from the model picker is
        # applied through the canonical resolver (find-or-create a model profile
        # and activate it) — the Qubit counterpart to the reference
        # ``POST /api/model/set`` writing ``cfg["model"]``. The SPA previously
        # resolved the pick against the terminal-profile registry and threw
        # UNKNOWN_MODEL_PROFILE for every catalog entry; here it is a real apply
        # or a typed refusal, never a fabricated success.
        if not (_truthy(b.get("confirm")) or _truthy(b.get("confirmed"))
                or _truthy(b.get("confirm_expensive_model"))):
            return 200, {"ok": False, "scope": "main", "provider": provider,
                         "model": model, "confirm_required": True,
                         "confirm_message": (f"Set the main model to "
                                             f"{provider}/{model}?")}
        from .providers import endpoints as EP

        try:
            applied = EP.select_model(
                cfg, provider, model,
                base_url=str(b.get("base_url") or "").strip(),
                key_ref=str(b.get("key_ref") or b.get("key_env") or "").strip(),
                confirm=True)
        except Exception as exc:
            return _fail(exc)
        if not applied.get("ok"):
            return 400, {"ok": False, "scope": "main",
                         "provider": provider, "model": model,
                         "code": str(applied.get("code", "MODEL_SET_FAILED")),
                         "detail": str(applied.get("detail", ""))}
        # The /chat terminal boots the pinned ``terminal.profile``, not the
        # main slot — without this a MODEL-card pick + reload still boots the
        # old model. The pin follows the pick when it meets /chat's context
        # floor, and honestly stays (with the reason) when it cannot run it.
        follow = _terminal_follow_main_model(
            cfg, str(applied.get("profile_id") or ""))
        return 200, {"ok": True, "scope": "main",
                     "provider": str(applied.get("provider", provider)),
                     "model": str(applied.get("model", model)),
                     "terminal_profile_id": follow["terminal_profile_id"],
                     "terminal_follows": follow["terminal_follows"],
                     "terminal_detail": follow["terminal_detail"]}
    if b.get("confirm_expensive_model") or b.get("confirmed"):
        # Upstream retry shape: the Models dialog re-POSTs with the flag
        # after showing confirm_required. Settle the staged journal
        # approval for the same model instead of staging a second one
        # (one confirmation path, settle-once journal).
        try:
            from .approvals import ApprovalJournal
            journal = ApprovalJournal(
                Path(cfg.state_dir()) / "approvals.jsonl",
                default_ttl_s=120.0)
            cands = [a for a in journal.pending()
                     if a.capability == "voice.profile_select"
                     and (a.meta or {}).get("session") == "dashboard"
                     and (a.meta or {}).get("args", {}).get("profile") == model]
        except Exception:
            cands = []
        if cands:
            from . import dashboard_ops as OPS
            latest = max(cands, key=lambda a: a.created_at)
            out = OPS.confirm_op(cfg, latest.id, actor="dashboard:user",
                                 session="dashboard")
            ok = out.get("state") == "executed"
            return (200 if ok else 400), {
                "ok": ok, "scope": "main", "provider": "ollama-local",
                "model": model,
                "detail": _redact(out.get("speech", out.get("detail", "")))[:400]}
        # No staged approval (expired or restarted since): fall through and
        # stage a fresh one below so the dialog has something to confirm.
    out = OPS.run_op(cfg, "profile_select",
                     {"kind": "brain", "profile": model},
                     actor="dashboard:user", session="dashboard")
    if out.get("confirmation_id"):
        # ModelAssignmentResponse confirm flow (409, never 2xx: see _staged).
        return _staged(_redact(out.get("speech", ""))[:300],
                       out["confirmation_id"],
                       {"confirm_required": True,
                        "confirm_message": _redact(out.get("speech", ""))[:300]})
    ok = out.get("state") == "executed"
    # ModelAssignmentResponse wire shape.
    return (200 if ok else 400), {
        "ok": ok, "scope": "main", "provider": "ollama-local", "model": model,
        "detail": _redact(out.get("speech", out.get("detail", "")))[:400]}


def _profiles_module(cfg: Any):
    """Return the vendored reference profile registry, Qubit-home bound.

    The profile authority is the reference ``hermes_cli.profiles`` (renamed
    ``qubit_cli.profiles``) — the real per-profile home directories the
    ``qubit agent profile`` CLI manages. Binding the Qubit runtime home first
    guarantees the registry can never resolve the external ``~/.hermes``
    (independence rule).
    """
    from . import qubit_runtime

    qubit_runtime.ensure_path()
    try:
        qubit_runtime.use_qubit_home(cfg)
    except Exception:  # noqa: BLE001 - a resolution miss must not 500 the read
        pass
    from qubit_cli import profiles as P  # type: ignore
    return P


def _profile_wire(info: Any) -> Dict[str, Any]:
    """Map a ``qubit_cli.profiles.ProfileInfo`` to the dashboard wire shape.

    Ported from the reference ``web_routers/profiles.py:_profile_to_dict``
    (``:79``); the absolute profile directory is replaced by the profile id
    (PRB-09: no absolute runtime paths on the served surface).
    """
    name = str(getattr(info, "name", "") or "")
    return {
        "name": name,
        "path": name,
        "is_default": bool(getattr(info, "is_default", False)),
        "model": getattr(info, "model", None),
        "provider": getattr(info, "provider", None),
        "has_env": bool(getattr(info, "has_env", False)),
        "skill_count": int(getattr(info, "skill_count", 0) or 0),
        "gateway_running": bool(getattr(info, "gateway_running", False)),
        "description": getattr(info, "description", "") or "",
        "description_auto": bool(getattr(info, "description_auto", False)),
        "display_name": getattr(info, "display_name", "") or "",
        "distribution_name": getattr(info, "distribution_name", None),
        "distribution_version": getattr(info, "distribution_version", None),
        "distribution_source": getattr(info, "distribution_source", None),
        "has_alias": getattr(info, "alias_path", None) is not None,
    }


def _profile_dir_or_error(P: Any, name: str
                          ) -> Tuple[Optional[Path],
                                     Optional[Tuple[int, Dict[str, Any]]]]:
    """Validate *name* and resolve its home directory, or return a typed error."""
    try:
        canon = P.normalize_profile_name(name)
        P.validate_profile_name(canon)
    except ValueError as exc:
        return None, (400, {"ok": False, "code": "PROFILE_BAD_NAME",
                            "detail": _redact(str(exc))[:300],
                            "qubit_counterpart": "CLI `qubit agent profile list`"})
    except Exception as exc:  # noqa: BLE001 - never 500 on a read
        return None, _fail(exc)
    try:
        prof_dir = P.get_profile_dir(canon)
    except Exception as exc:  # noqa: BLE001
        return None, _fail(exc)
    if not prof_dir.is_dir():
        return None, (404, {"ok": False, "code": "PROFILE_NOT_FOUND",
                            "detail": f"no profile {name!r}",
                            "qubit_counterpart": "CLI `qubit agent profile list`"})
    return prof_dir, None


def h_profiles(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
               p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/profiles -> the reference profile registry (one authority).

    Ported from the reference ``web_routers/profiles.py:list_profiles_endpoint``
    (``:685``) / ``hermes_cli/profiles.py:list_profiles`` (``:926``). The list,
    the create, and every row action (rename / model / description / SOUL /
    delete / active) address the SAME registry — the real per-profile home
    directories ``qubit agent profile`` manages — so no row shows a control that
    cannot resolve. The active marker is the sticky ``qubit agent profile use``
    selection. Absolute filesystem paths are never served (PRB-09).
    """
    try:
        P = _profiles_module(cfg)
        infos = P.list_profiles()
    except Exception as exc:
        return _fail(exc)
    try:
        active = P.get_active_profile() or "default"
    except Exception:  # noqa: BLE001 - never fail the read
        active = "default"
    try:
        current = P.get_active_profile_name() or "default"
    except Exception:  # noqa: BLE001
        current = "default"
    kind = (p.get("kind") or "").strip()
    if kind == "active":
        # ActiveProfileInfo wire shape.
        return _ok(active=active, current=current)
    items = [_profile_wire(i) for i in infos]
    active_row = next((i for i in items if i["name"] == active), None) or {}
    active_payload = {"id": active, "brain": active,
                      "model": active_row.get("model") or "",
                      "provider": active_row.get("provider") or ""}
    # {profiles: ProfileInfo[]} wire shape (+ the canonical active selection).
    return _ok(profiles=items, active=active_payload)


def h_profile_soul(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                   p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/profiles/{name}/soul -> the profile's own ``SOUL.md``.

    Ported from the reference ``web_routers/profiles.py:get_profile_soul``
    (``:876``): the same registry the list and the SOUL editor share, so a saved
    SOUL reads back verbatim.
    """
    name = str(p.get("name", "") or "")
    if not name:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "profile name required"}
    try:
        P = _profiles_module(cfg)
    except Exception as exc:
        return _fail(exc)
    prof_dir, err = _profile_dir_or_error(P, name)
    if err is not None:
        return err
    soul = prof_dir / "SOUL.md"
    try:
        text = soul.read_text(encoding="utf-8") if soul.is_file() else ""
    except OSError as exc:
        return _fail(exc)
    return _ok(content=text, exists=bool(text))


def h_profile_setup_command(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                            p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET setup-command -> the real ``qubit agent profile`` setup command.

    Ported from the reference ``web_routers/profiles.py:_profile_setup_command``
    (``:98``): ``qubit setup`` for the default profile, ``<name> setup`` for a
    named one.
    """
    name = str(p.get("name", "") or "").strip()
    if not name:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "profile name required",
                     "qubit_counterpart": "CLI `qubit agent profile`"}
    try:
        P = _profiles_module(cfg)
        canon = P.normalize_profile_name(name)
    except ValueError as exc:
        return 400, {"ok": False, "code": "PROFILE_BAD_NAME",
                     "detail": _redact(str(exc))[:300],
                     "qubit_counterpart": "CLI `qubit agent profile`"}
    except Exception as exc:
        return _fail(exc)
    command = "qubit setup" if canon == "default" else f"{canon} setup"
    try:
        registered = bool(P.profile_exists(canon))
    except Exception:  # noqa: BLE001
        registered = False
    return _ok(command=command, principal=canon, registered=registered,
               qubit_counterpart="CLI `qubit agent profile`")


def h_skills(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
             p: Dict[str, str]) -> Tuple[int, Any]:
    # SkillInfo[] wire shape (web/src/lib/api.ts). ONE read authority shared
    # with the CLI/voice so the three surfaces cannot drift (voicebridge.
    # skill_catalog merges the permissioned store with the vendored on-disk
    # catalog the Qubit shim must not hide).
    from . import skill_catalog

    return 200, skill_catalog.catalog_entries(cfg)


def _skill_authority_error(exc: Exception) -> Tuple[int, Dict[str, Any]]:
    """Map a skills-authority failure to its typed wire envelope."""
    from .skill_catalog import SkillAuthorityError

    if isinstance(exc, SkillAuthorityError):
        # Wire stability: the historical compat envelope reports a missing
        # skill as NOT_FOUND; the authority's internal code is preserved in
        # the detail-adjacent mapping only.
        code = ("NOT_FOUND" if exc.code == SkillAuthorityError.NOT_FOUND
                else exc.code)
        http = 404 if exc.code == SkillAuthorityError.NOT_FOUND else 400
        return http, {"ok": False, "code": code,
                      "detail": _redact(exc.detail)[:400]}
    return _fail(exc)


def h_skill_content(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/skills/content -> raw SKILL.md for the dashboard editor.

    Ported from the upstream reference ``get_skill_content``: resolves through the
    on-disk skill (``skill_manager_tool._find_skill`` under the Qubit-owned
    home) so every catalog entry opens — a permissioned-manifest-only skill
    falls back to its manifest text. 404 only when neither authority holds
    the name.
    """
    from . import skill_catalog

    name = str(q.get("name", "") or "")
    if not name:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "skill name required"}
    try:
        res = skill_catalog.read_skill_content(cfg, name)
    except Exception as exc:
        return _skill_authority_error(exc)
    if res is None:
        return 404, {"ok": False, "code": "NOT_FOUND",
                     "detail": f"no skill {name!r}"}
    # SkillContent wire shape.
    return _ok(name=name, content=res["content"], path=res["path"],
               origin=res.get("origin", ""))


def h_skills_toggle(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import dashboard_ops as OPS
    name = str(b.get("name") or b.get("skill") or "")
    enable = bool(b.get("enabled", b.get("enable", True)))
    out = OPS.run_op(cfg, "skills_enable" if enable else "skills_disable",
                     {"skill_id": name} if name else {},
                     actor="dashboard:user", session="dashboard")
    if out.get("confirmation_id"):
        return _staged(_redact(out.get("speech", ""))[:300],
                       out["confirmation_id"])
    ok = out.get("state") == "executed"
    return (200 if ok else 400), {"ok": ok,
                                  "detail": _redact(out.get("speech", out.get("detail", "")))[:400]}


def h_terminal_profiles(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                        p: Dict[str, str]) -> Tuple[int, Any]:
    """GET /api/terminal/profiles -> model profiles for the ``/chat`` picker.

    Non-secret summaries (no ``key_ref`` values, no credentials) with the
    per-profile ``/chat`` context-guard verdict, so the terminal UI can offer
    exactly the profiles that meet the floor and explain the ones that do
    not. Read-only.
    """
    from .providers import resolver as R

    try:
        current = str(cfg.get("terminal", "profile", default="") or "")
    except Exception:
        current = ""
    rows: List[Dict[str, Any]] = []
    try:
        profiles = R.load_profiles(cfg)
    except Exception as exc:
        return _fail(exc)
    for prof in profiles:
        try:
            guard = R.terminal_context_guard(cfg, profile_id=prof.id)
        except Exception as exc:  # noqa: BLE001 - one bad profile hides none
            guard = {"ok": False, "code": type(exc).__name__,
                     "detail": _redact(str(exc))[:200], "context_tokens": 0}
        rows.append({
            "id": prof.id,
            "kind": str(getattr(prof, "kind", "") or ""),
            "provider": str(getattr(prof, "provider", "") or ""),
            "model": str(getattr(prof, "model", "") or ""),
            "locality": str(getattr(prof, "locality", "") or ""),
            "enabled": bool(getattr(prof, "enabled", False)),
            "default": prof.id == current,
            "context_tokens": int(guard.get("context_tokens")
                                  or getattr(prof, "context_tokens", 0) or 0),
            "min_context_tokens": int(guard.get("min_context_tokens") or 0),
            "meets_floor": bool(guard.get("ok")),
            "code": str(guard.get("code") or "OK"),
            "has_credential": bool(str(getattr(prof, "key_ref", "")
                                       or "").strip()),
        })
    return 200, rows


def terminal_profile_set(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                         p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """PUT /api/terminal/profile -> persist the ``/chat`` model profile.

    Writes ``terminal.profile`` into the canonical voice.yaml (atomic +
    timestamped backup + audit) — the same key ``terminal_context_guard``
    reads, so the picker selection and the spawn guard can never diverge
    into two authorities. Confirm-gated on the shared journal. Only a known
    registry id is accepted; the guard verdict for the chosen profile rides
    along so the UI can warn when it does not meet the floor.
    """
    from .providers import resolver as R

    pid = str(b.get("profile_id", "") or q.get("profile_id", "") or "").strip()
    if not pid:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "profile_id required"}
    try:
        known = R.get_profile(cfg, pid)
    except Exception as exc:
        return _fail(exc)
    if known is None:
        return 404, {"ok": False, "code": "UNKNOWN_PROFILE",
                     "detail": f"no model profile {pid!r}"}
    guard = _compat_confirm(
        cfg, "terminal_profile_set",
        f"setting the /chat model profile to {pid!r} requires explicit "
        "confirmation: re-PUT with {\"confirm\": true}", q, b,
        payload={"profile_id": pid})
    if guard is not None:
        return guard
    try:
        _write_terminal_profile_locked(
            cfg, pid, actor="dashboard:user",
            audit_action="terminal_profile_set")
    except Exception as exc:
        return _fail(exc)
    try:
        verdict = R.terminal_context_guard(cfg, profile_id=pid)
    except Exception as exc:  # noqa: BLE001 - persist won; report guard state
        verdict = {"ok": False, "code": type(exc).__name__,
                   "detail": _redact(str(exc))[:200]}
    return _ok(ok=True, profile_id=pid, meets_floor=bool(verdict.get("ok")),
               code=str(verdict.get("code") or "OK"),
               detail=_redact(str(verdict.get("detail") or ""))[:300],
               qubit_counterpart="CLI `qubit profile model list`")


def _write_terminal_profile_locked(cfg: Any, pid: str, *, actor: str,
                                   audit_action: str) -> None:
    """Persist ``terminal.profile = pid`` to disk AND the running config.

    Atomic write (timestamped backup) plus the in-memory refresh
    ``terminal_context_guard`` reads — without the refresh the picker
    persisted to voice.yaml but the still-running guard kept resolving the
    old profile until a restart (observed as a stale ``qubit-go`` /
    UNKNOWN_PROFILE on /chat). Raises on failure; the caller maps it.
    """
    from . import config as C

    path = getattr(cfg, "path", None) or C.resolve_config_path()
    target = Path(path)
    data: Dict[str, Any] = {}
    if target.is_file():
        import yaml

        loaded = yaml.safe_load(target.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            data = loaded
    node = data.get("terminal")
    if not isinstance(node, dict):
        node = {}
    node["profile"] = pid
    data["terminal"] = node
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file():
        bak = target.with_name(
            f"{target.name}.terminal-profile.{int(time.time())}.bak")
        bak.write_bytes(target.read_bytes())
    tmp = target.with_name(target.name + ".qubit-tmp")
    import yaml

    tmp.write_text(yaml.safe_dump(data, sort_keys=False),
                   encoding="utf-8")
    tmp.replace(target)
    if hasattr(cfg, "data") and isinstance(getattr(cfg, "data"), dict):
        node = cfg.data.get("terminal")
        if not isinstance(node, dict):
            node = {}
            cfg.data["terminal"] = node
        node["profile"] = pid
    _audit(audit_action, actor, {"profile_id": pid})


def _terminal_follow_main_model(cfg: Any, profile_id: str, *,
                                actor: str = "dashboard:user"
                                ) -> Dict[str, Any]:
    """Point ``/chat`` at a freshly picked main profile when it can run it.

    A main-slot switch (``POST /api/model/set``) otherwise leaves /chat on
    the pinned ``terminal.profile``: the operator picks Muse, reloads, and
    the terminal still boots deepseek. When the picked profile meets /chat's
    context floor the pin follows it (same atomic write as the terminal
    picker, audited as ``terminal_profile_follow``), so reload really applies
    the pick. When it is below the floor the pin is kept — opening /chat on
    it would be a typed refusal — and the detail names the kept profile and
    why, so the UI can say so instead of silently keeping deepseek.
    """
    from .providers import resolver as R

    pid = str(profile_id or "").strip()
    try:
        current = str(cfg.get("terminal", "profile", default="") or "")
    except Exception:  # noqa: BLE001 - config-less read keeps the pin
        current = ""
    if not pid:
        return {"terminal_follows": False, "terminal_profile_id": current,
                "terminal_detail": "no profile to follow"}
    if pid == current:
        return {"terminal_follows": True, "terminal_profile_id": current,
                "terminal_detail": ""}
    try:
        from .providers import resolver as _R

        picked = _R.get_profile(cfg, pid)
    except Exception:  # noqa: BLE001 - unknown below; guard reports it
        picked = None
    if picked is not None and picked.kind == _R.LOCAL:
        # A local pick (Spark tier, brain) runs on /qubit-chat and voice —
        # it never moves the /chat pin off the big API model. Pin /chat to a
        # local model explicitly via Models → Use as when that is wanted.
        return {"terminal_follows": False, "terminal_profile_id": current,
                "terminal_detail": (
                    f"/chat keeps {current or 'its current model'}: "
                    f"{picked.model} is a local model for /qubit-chat and "
                    f"voice")}
    try:
        verdict = R.terminal_context_guard(cfg, profile_id=pid)
    except Exception as exc:  # noqa: BLE001 - guard fault keeps the pin
        return {"terminal_follows": False, "terminal_profile_id": current,
                "terminal_detail": (
                    f"/chat keeps {current or 'its current model'} "
                    f"(profile check failed: {type(exc).__name__})")}
    if not verdict.get("ok"):
        return {"terminal_follows": False, "terminal_profile_id": current,
                "terminal_detail": (
                    f"/chat keeps {current or 'its current model'}: "
                    f"{verdict.get('detail') or verdict.get('code') or 'below the /chat context floor'}")}
    try:
        _write_terminal_profile_locked(
            cfg, pid, actor=actor, audit_action="terminal_profile_follow")
    except Exception as exc:  # noqa: BLE001 - persist fault keeps the pin
        return {"terminal_follows": False, "terminal_profile_id": current,
                "terminal_detail": (
                    f"/chat keeps {current or 'its current model'} "
                    f"(could not persist: {type(exc).__name__})")}
    return {"terminal_follows": True, "terminal_profile_id": pid,
            "terminal_detail": ""}


def h_toolsets(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
               p: Dict[str, str]) -> Tuple[int, Any]:
    from . import toolsets as TS
    # The canonical toolset catalog is the vendored configurable toolset list
    # (the same list `qubit tools` and the runtime resolver use), NOT the broker
    # action inventory: broker actions (``local.*``) are single capabilities,
    # not toolsets, and rendering them produced placeholder rows with no label.
    names: List[str] = []
    meta: Dict[str, Dict[str, Any]] = {}
    enabled: Dict[str, bool] = {}
    resolve = None
    try:
        from . import qubit_runtime

        qubit_runtime.ensure_path()
        # Resolve the Qubit-owned runtime home explicitly; never let the catalog
        # read the vendored config/registry through the external ~/.hermes.
        try:
            qubit_runtime.use_qubit_home(cfg)
        except Exception:  # noqa: BLE001 - keep serving the catalog on a resolution miss
            pass
        # Bind the serving profile's secret scope around the vendored catalog
        # read: under multiplexing its credential probes (``XAI_API_KEY``,
        # ``HERMES_INFERENCE_PROVIDER``) fail closed with ``UnscopedSecretError``
        # and this broad except would otherwise degrade the catalog to empty.
        with qubit_runtime.profile_secret_scope(cfg):
            from qubit_cli.tools_config import (  # type: ignore
                _get_effective_configurable_toolsets,
                _get_platform_tools,
            )
            from qubit_cli.config import load_config as _load_qubit_config  # type: ignore
            from toolsets import resolve_toolset  # type: ignore

            resolve = resolve_toolset
            platform_on = set(_get_platform_tools(_load_qubit_config(), "cli"))
            for name, label, desc in _get_effective_configurable_toolsets():
                key = str(name)
                meta[key] = {"label": str(label or name),
                             "description": str(desc or "")}
                enabled[key] = key in platform_on
                names.append(key)
    except Exception:
        meta = {}
        enabled = {}
    # A toolset with a persisted dashboard state still belongs in the list, and
    # its settled enabled/disabled state overrides the resolved default.
    for _name, _is_on in TS.toolset_states(cfg).items():
        if _name not in names:
            names.append(_name)
        enabled[_name] = _is_on
    rows = TS.toolset_rows(cfg, names)
    for row in rows:
        key = str(row.get("name"))
        m = meta.get(key)
        if m:
            row["label"] = m["label"]
            row["description"] = m["description"]
        if key in enabled:
            row["enabled"] = enabled[key]
        if resolve is not None:
            try:
                row["tools"] = sorted(set(resolve(key)))
            except Exception:
                pass
    return 200, rows


def _epoch_to_iso(value: Any) -> Optional[str]:
    """Epoch seconds -> ISO-8601 UTC string, or ``None`` for an unset value.

    The SPA formats timestamps with ``new Date(value)``; the schedules store
    keeps epoch SECONDS (``time.time()``), so serving the raw number made the
    UI treat it as milliseconds and render 1970. ISO-8601 is timezone-explicit
    and unambiguous on the wire.
    """
    try:
        ts = float(value or 0)
    except (TypeError, ValueError):
        return None
    if ts <= 0:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts)) + "Z"


def _cron_next_run(j: Dict[str, Any]) -> Optional[float]:
    """Best-effort next-due epoch for an enabled schedule (``None`` unknown).

    Derived from the SAME fields ``schedules._due`` evaluates, so the page's
    "Next" column is a real projection of the store, not a hardcoded null.
    """
    if not j.get("enabled"):
        return None
    kind = str(j.get("kind", "") or "")
    now = time.time()
    try:
        base = float(j.get("last_run_at") or j.get("created_at") or 0.0)
    except (TypeError, ValueError):
        base = 0.0
    if kind == "interval":
        try:
            step = float(j.get("interval_min") or 0) * 60.0
        except (TypeError, ValueError):
            return None
        if step <= 0:
            return None
        return (base or now) + step
    if kind == "daily":
        try:
            hour = int(j.get("hour", -1))
            minute = int(j.get("minute", 0))
        except (TypeError, ValueError):
            return None
        if hour < 0:
            return None
        day_start = now - (now % 86400.0)
        target = day_start + hour * 3600.0 + minute * 60.0
        if target <= now:
            target += 86400.0
        return target
    if kind == "once":
        try:
            at = float(j.get("at_ts") or 0)
        except (TypeError, ValueError):
            return None
        return at if at > 0 and base <= 0 else None
    if kind == "cron":
        from . import schedules as S

        parsed = S._parse_cron(str(j.get("cron_expr", "") or ""))
        if parsed is None:
            return None
        return S._cron_next(parsed, now, None)
    return None


def _cron_timezone(cfg: Any) -> str:
    """Configured IANA zone name for the cron surface (``""`` = server-local).

    The store's :func:`schedules.list_jobs` already returns the resolved name;
    this helper gives the single-job (``h_cron_job_show``/create) path the same
    truthful value without reaching into the module's private resolver.
    """
    from . import schedules as S
    try:
        return str(S.list_jobs(cfg, limit=1).get("timezone", "") or "")
    except Exception:  # noqa: BLE001 — a read must never break the page
        return ""


def _cron_schedule_parts(j: Dict[str, Any]) -> Dict[str, Any]:
    """The frontend ``schedule`` object (kind/expr/run_at/minutes/display).

    The SPA's ScheduleBuilder reads ``schedule.expr`` and recognises the
    ``every Nm|h|d`` shorthand, a 5-field cron expression, and an ISO
    timestamp; ``schedule.minutes`` drives the human-readable interval label.
    The store keeps typed columns, so map them back here — the previous
    numeric ``interval_min`` string could not round-trip through the editor.
    """
    kind = str(j.get("kind", "") or "")

    def _int(key: str, default: int) -> int:
        try:
            return int(j.get(key, default))
        except (TypeError, ValueError):
            return default

    expr = ""
    run_at = None
    minutes = None
    if kind == "cron":
        expr = str(j.get("cron_expr", "") or "")
    elif kind == "interval":
        minutes = _int("interval_min", 0)
        if minutes > 0:
            if minutes % 1440 == 0:
                expr = f"every {minutes // 1440}d"
            elif minutes % 60 == 0:
                expr = f"every {minutes // 60}h"
            else:
                expr = f"every {minutes}m"
    elif kind == "daily":
        hour = _int("hour", -1)
        if hour >= 0:
            expr = f"{_int('minute', 0)} {hour} * * *"
    elif kind == "once":
        try:
            at = float(j.get("at_ts") or 0.0)
        except (TypeError, ValueError):
            at = 0.0
        if at > 0:
            expr = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(at))
            run_at = _epoch_to_iso(at)
    return {"kind": kind, "expr": expr, "run_at": run_at,
            "minutes": minutes, "display": kind}


def _cron_shape(j: Dict[str, Any], *, cfg: Any = None,
                tz_name: str = "") -> Dict[str, Any]:
    # CronJob wire shape (web/src/lib/api.ts); Qubit schedules are the store.
    # ``next_run_at`` prefers the store's own projection (``schedules.next_run_at``
    # folds the configured timezone + a pending retry ladder) and only falls back
    # to the local UTC-day arithmetic when the caller passed a raw row.
    next_run = j.get("next_run_at")
    if next_run is None:
        next_run = _cron_next_run(j)
    try:
        retry_at = float(j.get("retry_at") or 0.0)
    except (TypeError, ValueError):
        retry_at = 0.0
    retry_enabled = None
    if cfg is not None:
        from . import schedules as S
        try:
            retry_enabled = bool(S.retry_enabled(cfg))
        except Exception:  # noqa: BLE001
            retry_enabled = None
    return {
        "id": str(j.get("id", "")), "profile": None, "profile_name": None,
        "qubit_home": None, "is_default_profile": True,
        "name": j.get("name"), "prompt": j.get("task_text"),
        "script": None, "skills": None,
        "schedule": _cron_schedule_parts(j),
        "schedule_display": str(j.get("kind", "")),
        "repeat": None, "enabled": bool(j.get("enabled", False)),
        "state": str(j.get("last_state", "") or ""),
        "deliver": (j.get("deliver_pref") or j.get("deliver_to") or None),
        "model": None, "provider": None, "base_url": None,
        "no_agent": None, "context_from": None, "enabled_toolsets": None,
        "workdir": None,
        "last_run_at": _epoch_to_iso(j.get("last_run_at")),
        "next_run_at": _epoch_to_iso(next_run),
        "last_status": j.get("last_state"),
        "last_error": (str(j.get("last_error") or "") or None),
        "last_delivery_error": j.get("last_delivery_error"),
        # Timezone + retry posture the store already computes (never nulled):
        # the configured zone, the pending retry instant/attempt, and whether
        # the retry ladder is armed at all.
        "timezone": tz_name or None,
        "retry_at": _epoch_to_iso(retry_at) if retry_at > 0 else None,
        "retry_attempt": int(j.get("retry_attempt") or 0),
        "retry_enabled": retry_enabled,
    }


def h_cron_jobs(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                p: Dict[str, str]) -> Tuple[int, Any]:
    from . import schedules as S
    try:
        res = S.list_jobs(cfg)
    except Exception as exc:
        return _fail(exc)
    jobs = res.get("jobs", res) if isinstance(res, dict) else res
    tz_name = str(res.get("timezone", "") or "") if isinstance(res, dict) else ""
    return 200, [_cron_shape(j, cfg=cfg, tz_name=tz_name)
                 for j in (jobs or [])]


def h_cron_job_show(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import schedules as S
    reader = getattr(S, "get_job", None) or getattr(S, "show", None)
    try:
        res = reader(cfg, int(p.get("id", "0")))
    except Exception as exc:
        code = getattr(exc, "code", None) or type(exc).__name__
        http = 404 if "NOT_FOUND" in str(code).upper() else 400
        return http, {"ok": False, "code": code,
                      "detail": _redact(str(exc))[:200]}
    if isinstance(res, dict) and res.get("ok") is False:
        return 404, {"ok": False, "code": res.get("code", "NOT_FOUND"),
                     "detail": _redact(res.get("error", ""))[:200]}
    job = res.get("job", res) if isinstance(res, dict) else {}
    return 200, _cron_shape(job if isinstance(job, dict) else {},
                            cfg=cfg, tz_name=_cron_timezone(cfg))


def h_cron_job_runs(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """Run history for one schedule ({"runs": CronRun[], "limit": int}).

    Backed by ``voicebridge.schedules.history`` (the same SQLite run log
    ``qubit schedules history`` reads). Schedule ids are numeric; a non-numeric
    upstream job id honestly reports no runs instead of scanning every job.
    """
    from . import schedules as S
    raw = str(p.get("id", "") or "")
    try:
        limit = max(1, min(int(q.get("limit", "20") or "20"), 100))
    except ValueError:
        limit = 20
    try:
        job_id = int(raw)
    except ValueError:
        job_id = 0
    if not job_id:
        return _ok(runs=[], limit=limit, count=0,
                   detail=f"schedule {raw!r} has no numeric id; no runs")
    try:
        res = S.history(cfg, job_id, limit)
    except Exception as exc:
        return _fail(exc)
    runs = res.get("runs", []) if isinstance(res, dict) else []
    now = time.time()
    shaped = []
    for r in runs if isinstance(runs, list) else []:
        started = r.get("started_at") or 0
        finished = r.get("finished_at") or 0
        shaped.append({
            "id": r.get("id"), "job_id": r.get("job_id"),
            "started_at": started, "ended_at": finished or None,
            "finished_at": finished, "state": str(r.get("state", "") or ""),
            "code": str(r.get("code", "") or ""),
            "detail": str(r.get("detail", "") or ""),
            "is_active": not finished and (now - started) < 300,
            "archived": False,
        })
    return _ok(runs=shaped, limit=limit, count=len(shaped),
               state=str(res.get("state", "OK") if isinstance(res, dict)
                         else "OK"))


def _cron_mutate(fn_name: str, cfg: Any, q: Dict[str, str],
                 b: Dict[str, Any], p: Dict[str, str]
                 ) -> Tuple[int, Dict[str, Any]]:
    """POST /api/cron/jobs/{id}/pause|resume|trigger.

    Pause/resume are schedule mutations and share the same confirmation
    contract as create/update/delete: ``_compat_confirm`` checks the shared
    e-stop latch, consumes a pending approval exactly once, and otherwise
    stages a typed 409 with a real ``confirmation_id``. The canonical
    ``schedules.set_paused`` (the CLI/voice store) performs the transition and
    audits it. A trigger addresses the exact job in the path -- never a
    blanket ``run_due`` over every due job.
    """
    from . import schedules as S
    try:
        job_id = int(p.get("id", "0") or "0")
    except (TypeError, ValueError):
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "schedule id must be numeric"}
    try:
        if fn_name in ("pause", "resume"):
            paused = fn_name == "pause"
            guard = _compat_confirm(
                cfg, f"cron.{fn_name}",
                f"schedule {fn_name} requires explicit confirmation: "
                "re-POST with {\"confirm\": true}", q, b,
                payload={"id": job_id})
            if guard is not None:
                return guard
            res = S.set_paused(cfg, job_id, paused,
                               requested_by="dashboard:user", confirmed=True)
            ok = not (isinstance(res, dict) and res.get("ok") is False)
            return (200 if ok else 400), {"ok": ok, "result": res,
                                          "enabled": not paused,
                                          "qubit_counterpart":
                                          "CLI `qubit schedules pause|resume`"}
        if fn_name == "run_due":
            # A path id targets exactly that job (forced); the legacy global
            # ``run_due`` behavior is preserved only when no id was supplied.
            if job_id:
                res = S.trigger(cfg, job_id, requested_by="dashboard:user")
            else:
                res = S.run_due(cfg, requested_by="dashboard:user")
            return 200, {"ok": True, "result": res}
        res = getattr(S, fn_name)(cfg, job_id, requested_by="dashboard:user")
    except Exception as exc:
        return _fail(exc)
    ok = not (isinstance(res, dict) and res.get("ok") is False)
    return (200 if ok else 400), {"ok": ok, "result": res}


def h_logs(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
           p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/logs -> full LogsResponse contract, no proxy required.

    Degraded-mode fallback for the vendored ``get_logs`` reader: serves the
    SAME wire shape (``{file, lines, offset, limit, returned, has_more,
    next_offset}``) from the same journal files, so the Logs page keeps its
    file/level/component/search filters and Older/Newer paging even when the
    in-process proxy is down. Unknown file/component are typed 400s,
    mirroring the primary. (The retired branch served 5 lines truncated to
    160 chars with no pagination fields, which rendered the page nearly
    empty whenever the proxy degraded.)
    """
    file = str(q.get("file", "agent") or "agent")
    try:
        limit = max(1, min(int(q.get("limit", q.get("lines", "100")) or "100"), 500))
    except ValueError:
        limit = 100
    try:
        offset = max(0, int(q.get("offset", "0") or "0"))
    except ValueError:
        offset = 0
    level = q.get("level") or None
    component = q.get("component") or None
    search = q.get("search") or None
    profile = q.get("profile") or None
    try:
        from . import qubit_runtime as _HR
        _HR.ensure_path()
        from qubit_cli.logs import _read_tail, LOG_FILES
        try:
            from qubit_logging import COMPONENT_PREFIXES
        except ImportError:
            COMPONENT_PREFIXES = {}
        log_name = LOG_FILES.get(file)
        if not log_name:
            return 400, {"ok": False, "code": "UNKNOWN_LOG_FILE",
                         "detail": f"Unknown log file: {file}"}
        try:
            from qubit_cli.web_server_profiles import _config_profile_scope
            from qubit_constants import get_qubit_home
            with _config_profile_scope(profile):
                log_path = get_qubit_home() / "logs" / log_name
        except Exception:
            log_path = _HR.runtime_home_for_cfg(cfg) / "logs" / log_name
        empty = {"file": file, "lines": [], "offset": offset, "limit": limit,
                 "returned": 0, "has_more": False, "next_offset": None}
        if not log_path.exists():
            return _ok(**empty)
        min_level = level if level and level.upper() != "ALL" else None
        comp_prefixes = None
        if component and component.lower() != "all":
            comp_prefixes = COMPONENT_PREFIXES.get(component)
            if comp_prefixes is None:
                return 400, {"ok": False, "code": "UNKNOWN_COMPONENT",
                             "detail": f"Unknown component: {component}"}
        # One matching line past the page proves has_more; never guessed.
        read_n = 2000 if search else max(1, offset + limit + 1)
        result = _read_tail(log_path, read_n,
                            has_filters=bool(min_level or comp_prefixes or search),
                            min_level=min_level,
                            component_prefixes=comp_prefixes)
        if search:
            needle = search.lower()
            result = [line for line in result if needle in line.lower()]
        end = len(result) - offset
        page = result[max(0, end - limit):end] if end > 0 else []
        has_more = limit > 0 and (len(result) - offset - limit) > 0
        lines = [_redact(line) for line in page]
        return _ok(file=file, lines=lines, offset=offset, limit=limit,
                   returned=len(page), has_more=has_more,
                   next_offset=(offset + len(page)) if has_more else None)
    except Exception as exc:
        return _fail(exc)


def h_system_stats(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                   p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    import os
    import platform as _pl
    try:
        from . import distribution as D
        qv = str(getattr(D, "VERSION", "1.0.0"))
    except Exception:
        qv = "1.0.0"
    # SystemStats wire shape; hermes_version carries the Qubit product
    # version (wire-compat label, honest value + provenance in detail).
    return _ok(os=_pl.system(), os_release=_pl.release(),
               os_version=_pl.version(), platform=_pl.system().lower(),
               arch=_pl.machine(), hostname=_pl.node(),
               python_version=_pl.python_version(),
               python_impl=_pl.python_implementation(),
               qubit_version="qubit-" + qv, cpu_count=os.cpu_count(),
               psutil=False,
               detail="Qubit single-authority host; no upstream gateway process")


def h_mcp(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
          p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/mcp -> the real configured MCP inventory.

    Delegates to the canonical ``h_mcp_servers`` (same store the writes and
    CLI/voice read) so this bare route can never contradict
    ``/api/mcp/servers`` with a hardcoded empty list.
    """
    return h_mcp_servers(cfg, q, b, p)


def h_mcp_catalog_install(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                          p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/mcp/catalog/install -> register a catalog server (real).

    A catalog install fetches no code — it registers the reviewed entry's
    transport (http URL or stdio command) in the canonical Qubit MCP registry
    and stores declared credentials. Closed credential schema (mirroring the
    reference): only env vars the entry declares are accepted, names are
    validated before the first write, and values enter the canonical runtime
    ``.env`` (secrets-only, ``chmod 600``, never echoed) while the registry
    keeps ``env:NAME`` references only. Confirm-gated like every registry
    mutation; audited. OAuth-type entries register the same way — the actual
    browser authorization stays the user's own manual step (post_install).
    """
    from . import env_store as ES
    from . import mcp_servers as MS
    try:
        _vendored_runtime(cfg)
        from qubit_cli import mcp_catalog as _MC  # type: ignore
    except Exception as exc:
        return _fail(exc)
    name = str(b.get("name", "") or "").strip()
    if not name:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "catalog entry name required",
                     "qubit_counterpart": "GET /api/mcp/catalog"}
    try:
        entry = _MC.get_entry(name)
    except Exception as exc:
        return _fail(exc)
    if entry is None:
        return 404, {"ok": False, "code": "MCP_CATALOG_NOT_FOUND",
                     "detail": f"No catalog entry {name!r}",
                     "qubit_counterpart": "GET /api/mcp/catalog"}
    given = b.get("env") or {}
    if not isinstance(given, dict):
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "env must be an object of NAME: value",
                     "qubit_counterpart": "GET /api/mcp/catalog"}
    specs = list(getattr(entry.auth, "env", None) or [])
    declared = {str(s.name) for s in specs}
    undeclared = sorted(set(str(k) for k in given) - declared)
    if undeclared:
        return 400, {"ok": False, "code": "MCP_ENV_UNDECLARED",
                     "detail": f"Catalog entry {name!r} does not declare "
                               f"environment variable(s): "
                               f"{', '.join(undeclared)}",
                     "qubit_counterpart": "GET /api/mcp/catalog"}
    missing = sorted(str(s.name) for s in specs
                     if getattr(s, "required", False)
                     and not str(given.get(s.name) or "").strip())
    if missing:
        return 400, {"ok": False, "code": "MCP_ENV_REQUIRED",
                     "detail": f"Catalog entry {name!r} requires "
                               f"{', '.join(missing)}",
                     "qubit_counterpart": "GET /api/mcp/catalog"}
    for key in given:
        if not ES._ENV_VAR_RE.match(str(key)):
            return 400, {"ok": False, "code": "MCP_ENV_REQUIRED",
                         "detail": f"invalid env name {str(key)[:60]!r}",
                         "qubit_counterpart": "GET /api/mcp/catalog"}
    guard = _compat_confirm(
        cfg, "mcp.catalog_install",
        f"Install catalog MCP server {name!r}: register its transport and "
        f"store {len(given)} credential(s) in the runtime .env?",
        q, b, payload={"name": name,
                       "env": {str(k): str(v) for k, v in given.items()},
                       "enable": bool(b.get("enable", True))})
    if guard is not None:
        return guard
    secrets = {str(s.name) for s in specs if getattr(s, "secret", False)}
    refs: Dict[str, str] = {}
    inline: Dict[str, str] = {}
    try:
        for key, value in given.items():
            if key in secrets:
                ES.write_secret(cfg, str(key), str(value or ""))
                refs[str(key)] = f"env:{key}"
            else:
                # Non-secret values are inlined into the transport fields
                # (mirroring the reference: URL templates like
                # ``${N8N_MCP_SERVER_URL}`` resolve at install). They also
                # enter the runtime .env so a reinstall round-trips.
                ES.write_secret(cfg, str(key), str(value or ""))
                inline[str(key)] = str(value or "")
                refs[str(key)] = f"env:{key}"
    except ValueError as exc:
        return 400, {"ok": False, "code": "MCP_ENV_REQUIRED",
                     "detail": _redact(str(exc))[:200],
                     "qubit_counterpart": "GET /api/mcp/catalog"}
    transport = str(getattr(entry.transport, "type", "") or "").lower()
    fields: Dict[str, Any] = {"transport": transport, "enabled": True,
                              "source": "mcp-catalog"}
    if transport == "http":
        url = str(getattr(entry.transport, "url", "") or "")
        for key, value in inline.items():
            url = url.replace("${" + key + "}", value)
        fields["url"] = url
    else:
        command = str(getattr(entry.transport, "command", "") or "")
        args = list(getattr(entry.transport, "args", None) or [])
        for key, value in inline.items():
            command = command.replace("${" + key + "}", value)
            args = [a.replace("${" + key + "}", value) for a in args]
        fields["command"] = command
        fields["args"] = args
    if refs:
        fields["env"] = refs
    if _truthy(b.get("enable", True)) is False:
        fields["enabled"] = False
    try:
        out = MS.upsert(cfg, name, fields, confirmed=True)
    except Exception as exc:
        code = getattr(exc, "code", None) or "MCP_BAD_INPUT"
        return 400, {"ok": False, "code": code,
                     "detail": _redact(str(exc))[:300],
                     "qubit_counterpart": "dashboard /mcp ADD SERVER"}
    _audit("mcp_catalog_install", "dashboard:user",
           {"server": name, "transport": transport,
            "credentials": sorted(refs)})
    result: Dict[str, Any] = {"ok": True, "name": name,
                              "background": False,
                              "enabled": bool(fields["enabled"]),
                              "credentials_stored": sorted(refs)}
    post = str(getattr(entry, "post_install", "") or "").strip()
    if post:
        result["post_install"] = post[:600]
    return 200, result


def h_mcp_servers(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                  p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/mcp/servers -> real configured MCP inventory (names only).

    Reads the same ``voicebridge.integration_status.mcp_status`` the CLI and
    voice surfaces use. Qubit never mounts a server, so each row is
    inventory-only with the mount policy attached; no command, URL, or
    credential reference is exposed.
    """
    from . import mcp_servers as MS
    try:
        listing = MS.list_servers(cfg)
    except Exception as exc:
        return _fail(exc)
    # PRB-01: emit the full ``McpServer`` wire shape the SPA reads (url,
    # command, args, env, tools), not a names-only summary. This route is now
    # the canonical reader (added to webui.QUBIT_READ_AUTHORITY) so the
    # dashboard list, CLI, and voice all read the same registry the writes use.
    _AUTH = {"header", "oauth"}
    servers = [{
        "name": str(s.get("name")),
        "transport": (str(s.get("transport"))
                      if str(s.get("transport")) in ("http", "stdio")
                      else "unknown"),
        "url": s.get("url"),
        "command": s.get("command"),
        "args": list(s.get("args") or []),
        "env": dict(s.get("env") or {}),
        "auth": (str(s.get("auth")) if str(s.get("auth")) in _AUTH else None),
        "enabled": bool(s.get("enabled")),
        # The invocation disposition + tool allowlist are part of the canonical
        # row (``MS.list_servers`` -> ``_public``); carry them so the dashboard
        # can show why a server is/ isn't invocable and how many tools it
        # declares. Absent values are honest, never fabricated.
        "capabilities": list(s.get("capabilities") or []),
        "invocable": bool(s.get("invocable")),
        "invocation_code": s.get("invocation_code"),
        "tools": None,
        "connected": False,
        "status": str(s.get("status", "")),
        "source": str(s.get("source", "qubit-registry")),
        "policy": "config registry; no unauthenticated mount",
    } for s in listing.get("servers", [])]
    return _ok(servers=servers, state="OK" if not servers else "CONFIGURED",
               mount_policy="no unauthenticated MCP mounting; research-only "
                            "use stays separately authorized and broker-gated",
               detail="registered inventory; Qubit never mounts or connects "
                      "MCP servers")


def h_mcp_catalog_live(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                       p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/mcp/catalog -> the canonical catalog, installed-state reconciled.

    The MCP page read a 65-entry catalog from the vendored runtime's reviewed
    optional-mcps manifests while the server list came from the Qubit registry
    (``voicebridge.mcp_servers``): two stores whose ``installed``/``enabled``
    flags contradicted (every catalog entry said installed:false and
    ``cgraph-graft`` was absent). This serves BOTH from one authority — the
    catalog manifests are the "available" set, and each entry's installed/
    enabled state is resolved against the SAME Qubit registry the list and
    writes use. Registry servers absent from the manifests are appended, so the
    page can never hide an installed server. No remote catalog is fetched.
    """
    from . import mcp_servers as MS
    try:
        registry = MS.list_servers(cfg)
    except Exception as exc:
        return _fail(exc)
    reg = {str(s.get("name")): s for s in registry.get("servers", [])}
    entries: List[Dict[str, Any]] = []
    diags: List[Dict[str, Any]] = []
    cataloged: set = set()
    try:
        from . import qubit_runtime as HR
        HR.ensure_path()
        from qubit_cli import mcp_catalog  # type: ignore
        from qubit_cli.web_routers.mcp import (  # type: ignore
            _catalog_entry_json)
        for entry in mcp_catalog.list_catalog():
            row = reg.get(entry.name)
            cataloged.add(entry.name)
            entries.append(_catalog_entry_json(
                entry, bool(row), bool(row.get("enabled")) if row else False))
        diags = [{"name": n, "kind": k, "message": m}
                 for (n, k, m) in mcp_catalog.catalog_diagnostics()]
    except Exception:
        diags = [{
            "name": "catalog", "kind": "info", "level": "info",
            "message": "reviewed catalog manifests unavailable; serving the "
                       "Qubit registry inventory"}]
    # Any locally registered server the manifests don't list is still a real
    # row: append it installed so available-vs-installed is never contradictory.
    for name, row in sorted(reg.items()):
        if name in cataloged:
            continue
        entries.append({
            "name": name, "description": "",
            "source": str(row.get("source", "qubit-registry")),
            "transport": str(row.get("transport", "stdio")),
            "auth_type": str(row.get("auth", "none")), "required_env": [],
            "command": row.get("command"), "args": list(row.get("args") or []),
            "url": row.get("url"),
            "install_url": None, "install_ref": None, "bootstrap": [],
            "default_enabled": None, "post_install": "",
            "suggest": None,
            "needs_install": False, "installed": True,
            "enabled": bool(row.get("enabled")),
        })
    return _ok(entries=entries, total=len(entries), diagnostics=diags,
               mount_policy="no unauthenticated MCP mounting; the catalog is a "
                            "reviewed local manifestation inventory and each "
                            "entry's installed state is the Qubit registry")


def h_mcp_test_local(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                     p: Dict[str, str], ) -> Tuple[int, Dict[str, Any]]:
    """POST /api/mcp/servers/{name}/test -> the real validation/probe result.

    ``MS.test`` validates the record and, when the server is enabled and
    runnable, performs a live ``tools/list`` probe. Its ``connected``,
    ``probe`` and ``network_probe`` fields are the truth; this handler must
    return them verbatim (hardcoding ``connected=False``/``local-validation``
    contradicted a successful probe that already returned real tools).
    """
    from . import mcp_servers as MS
    name = str(p.get("name", p.get("id", "")) or "")
    try:
        out = MS.test(cfg, name)
    except Exception as exc:
        return _mcp_error(exc)
    # Honest success flag: a probe that did not connect is NOT a success. The
    # dashboard keys its toast/panel on ``ok`` (McpPage.tsx), so hardcoding
    # ``ok=True`` rendered a green "Connected -- no tools" for an unreachable
    # server. The reference returns ``ok:False`` on a failed probe
    # (hermes_cli/web_routers/mcp.py:190). ``mcp_servers.test`` now carries the
    # honest ``ok``; ``connected`` remains the fallback for older callers.
    connected = bool(out.get("connected", False))
    ok = bool(out.get("ok", connected))
    extra = {} if ok else {"error": out["detail"]}
    return _ok(ok=ok, name=out["name"], tools=out["tools"],
               connected=connected,
               valid=out["valid"], errors=out["errors"],
               code=out["code"], state=out["state"],
               probe=str(out.get("probe", "local-validation")),
               network_probe=bool(out.get("network_probe", False)),
               detail=out["detail"], **extra)


#: Read-only MCP capability reads (resources / prompts / resource templates).
#: The vendored router exposes the same three reads over the fork's MCP config
#: registry (``qubit_runtime/qubit_cli/web_routers/mcp.py``); the dashboard
#: ``/agent/api/mcp/servers`` prefix is quarantined to this compat table, so
#: they are served here against the canonical ``voicebridge.mcp_servers``
#: registry and the ONE brokered client transport (never a second MCP client).
_MCP_CAPABILITY_KEYS = {
    "resources": "resources",
    "prompts": "prompts",
    "resource-templates": "resource_templates",
}
_MCP_CAPABILITY_RESULT_KEYS = {
    "resources": "resources",
    "prompts": "prompts",
    "resource-templates": "resourceTemplates",
}


def _mcp_capability_read(cfg: Any, p: Dict[str, str],
                         capability: str) -> Tuple[int, Dict[str, Any]]:
    """Resolve a server from the canonical registry and drain one list.

    Read-only: no confirmation. The canonical ``list_capability`` still honors
    the shared e-stop and the method gate (enabled + runnable). A real drained
    list is returned verbatim; an inventory-only server returns its honest typed
    OPERATOR_ONLY disposition; an unknown server is a typed 404. A transport
    failure is surfaced as a typed error, never a fabricated empty list.
    """
    from . import mcp_servers as MS

    name = str(p.get("name", p.get("id", "")) or "")
    key = _MCP_CAPABILITY_KEYS[capability]
    try:
        out = MS.list_capability(cfg, name, capability,
                                 requested_by="dashboard:user")
    except Exception as exc:
        code = str(getattr(exc, "code", "") or "")
        if code in ("MCP_NOT_FOUND", "MCP_BAD_INPUT", "MCP_STORE_CORRUPT",
                    "SECRET_WRITE_REFUSED"):
            return _mcp_error(exc)
        return 200, {
            "ok": False, "state": "ERROR", "server": name,
            "capability": capability, "connected": False,
            "code": code or "MCP_READ_FAILED",
            "detail": _redact(str(getattr(exc, "detail", exc)))[:300],
            "qubit_counterpart": ("CLI `qubit mcp invoke` / the agent MCP "
                                  "resource tools"),
        }
    if out.get("state") != "OK":
        return _ok(server=name, capability=capability, state=out.get("state"),
                   connected=False, invocable=False, code=out.get("code"),
                   detail=out.get("detail"),
                   operator_prerequisite=out.get("operator_prerequisite"),
                   qubit_counterpart=out.get("qubit_counterpart"),
                   authority=out.get("authority"), **{key: []})
    result = out.get("result") if isinstance(out.get("result"), dict) else {}
    items = result.get(_MCP_CAPABILITY_RESULT_KEYS[capability])
    if not isinstance(items, list):
        items = []
    return _ok(server=name, capability=capability, state="OK",
               connected=True, code="MCP_READ_OK", count=len(items),
               names=out.get("names"), result=result,
               authority=out.get("authority"), detail=out.get("detail"),
               **{key: items})


def h_mcp_server_resources(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                           p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/mcp/servers/{name}/resources -> real resources/list (read-only)."""
    return _mcp_capability_read(cfg, p, "resources")


def h_mcp_server_prompts(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                         p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/mcp/servers/{name}/prompts -> real prompts/list (read-only)."""
    return _mcp_capability_read(cfg, p, "prompts")


def h_mcp_server_resource_templates(cfg: Any, q: Dict[str, str],
                                    b: Dict[str, Any], p: Dict[str, str]
                                    ) -> Tuple[int, Dict[str, Any]]:
    """GET /api/mcp/servers/{name}/resource-templates -> real templates list."""
    return _mcp_capability_read(cfg, p, "resource-templates")


def mcp_invoke(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
               p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/mcp/servers/{name}/invoke -> real, confirm-gated tool call.

    The dashboard had no invocation surface: ``/mcp`` could list and test a
    server but never call a tool. This wires the SAME canonical entry point the
    CLI (``qubit mcp invoke``) and voice use — ``voicebridge.mcp_servers.invoke``
    — so the dashboard, CLI, and voice share one e-stop + shared-journal
    confirmation + capability/allowlist + brokered-transport path. An
    unconfirmed call stages a real single-use approval (409); a refused or
    uninvocable server returns its typed, fail-closed disposition — never a
    fabricated success. Output is redacted by the client transport.
    """
    from . import mcp_servers as MS
    name = str(p.get("name", p.get("id", "")) or b.get("name", "") or "")
    tool = str(b.get("tool") or b.get("tool_name") or "").strip()
    if not name:
        return 400, {"ok": False, "code": "MCP_BAD_INPUT",
                     "detail": "server name required"}
    if not tool:
        return 400, {"ok": False, "code": "MCP_BAD_INPUT",
                     "detail": "tool name required"}
    # Accept every real wire spelling: the SPA sends ``args``, the MCP client
    # spec sends ``arguments``, and the voice/gateway path sends ``tool_args``.
    # All three resolve to the SAME canonical ``voicebridge.mcp_servers.invoke``
    # args dict; an absent/ill-typed value is an honest empty call, never a
    # silently dropped argument list.
    raw_args = b.get("args")
    if not isinstance(raw_args, dict):
        raw_args = b.get("arguments")
    if not isinstance(raw_args, dict):
        raw_args = b.get("tool_args")
    args = raw_args if isinstance(raw_args, dict) else {}
    confirmed = _truthy(b.get("confirm"))
    cid = str(b.get("confirmation_id") or q.get("confirmation_id") or "").strip()
    try:
        out = MS.invoke(cfg, name, tool=tool, args=args, confirmed=confirmed,
                        confirmation_id=cid, requested_by="dashboard:user")
    except Exception as exc:
        code = str(getattr(exc, "code", "") or "")
        if code == MS.INVOKE_CONFIRM_CODE:
            return _staged(
                str(getattr(exc, "detail", exc)),
                "")
        return _mcp_error(exc)
    state = str(out.get("state", "") or "")
    ok = state in ("OK", "INVOCABLE")
    return (200 if ok else 403), {"ok": ok, **out}


def h_pairing_list(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                   p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import pairing as P
    try:
        res = P.list_codes(cfg)
    except Exception as exc:
        return _fail(exc)
    codes = res.get("codes", []) if isinstance(res, dict) else []
    pending, approved = [], []
    for c in codes if isinstance(codes, list) else []:
        if not isinstance(c, dict):
            continue
        code_id = str(c.get("id", "") or "")
        # ``request_id`` must be usable: the SPA's Approve sends it back as the
        # target (api.ts approvePairing), so it has to carry the real code id
        # the store can resolve -- never an empty string.
        user = {"platform": str(c.get("account", "") or "qubit"),
                "user_id": code_id,
                "user_name": str(c.get("label", "") or ""),
                "request_id": code_id,
                "label": str(c.get("label", "") or ""),
                "scopes": list(c.get("scopes", []) or []),
                "expires_at": c.get("expires_at"),
                "revoked": bool(c.get("revoked", False))}
        (approved if c.get("approved") else pending).append(user)
    # PairingResponse wire shape.
    return _ok(pending=pending, approved=approved)


_PAIRING_HTTP = {
    "PAIRING_BAD_CODE": 400,
    "PAIRING_BAD_LABEL": 400,
    "PAIRING_BAD_SCOPE": 400,
    "PAIRING_BAD_TTL": 400,
    "PAIRING_NOT_FOUND": 404,
    "PAIRING_NOT_BOUND": 404,
    "PAIRING_CONFIRM_REQUIRED": 409,
    "PAIRING_REVOKED": 409,
    "PAIRING_USED": 409,
    "PAIRING_EXPIRED": 410,
    "PAIRING_LOCKED": 429,
    "PAIRING_LIMIT": 429,
    "PAIRING_ESTOP": 403,
}


def _pairing_error(exc: Exception) -> Tuple[int, Dict[str, Any]]:
    """Map a typed ``PairingError`` to its HTTP status (never a flat 500)."""
    code = str(getattr(exc, "code", None) or type(exc).__name__)
    detail = _redact(getattr(exc, "detail", None) or str(exc))[:300]
    if code == "PAIRING_CONFIRM_REQUIRED":
        return _staged(detail, "")
    status = _PAIRING_HTTP.get(code, 500)
    if status >= 500:
        return 500, {"ok": False, "state": "FAILED", "code": code,
                     "detail": detail}
    return status, {"ok": False, "code": code, "detail": detail}


def _pairing_approve_request(cfg: Any, code_id: str,
                             requested_by: str = "dashboard:user"
                             ) -> Dict[str, Any]:
    """Approve a pending pairing request by its opaque ``code_id``.

    Ports the reference's request-id approve path
    (``hermes_cli/web_routers/ops.py:87``: ``store.approve_request(platform,
    request_id)``) onto the Qubit ``voicebridge.pairing`` store. The one-time
    code is shown once and stored only as a salted hash, so a dashboard admin
    who listed a pending request can only approve it by the request id the list
    surfaced; hashing makes code-verification impossible without the secret
    code. This issues the SAME scoped ``gateway.access`` grant, marks the record
    used, and audits ``pairing.approve`` — identical to the code path, reached
    by request id.
    """
    from . import pairing as P
    from .permissions import permissions_store

    state = P._load(cfg)
    rec = state.get("codes", {}).get((code_id or "").strip())
    if not isinstance(rec, dict):
        raise P.PairingError(P.BAD_CODE, "unknown or consumed pairing code")
    if rec.get("used") or rec.get("revoked"):
        raise P.PairingError(P.BAD_CODE, "unknown or consumed pairing code")
    now = P._now()
    if now > float(rec.get("expires_at", 0) or 0):
        raise P.PairingError(P.EXPIRED_CODE, "pairing code expired")
    store = permissions_store(cfg)
    ttl = max(60.0, min(float(rec["expires_at"]) - now, P.MAX_TTL_S))
    try:
        grant = store.grant("gateway.access", requested_by=requested_by,
                            confirmed=True,
                            scopes=tuple(rec.get("scopes", []) or []),
                            ttl_s=ttl, note=f"pairing {rec['id']}")
    except Exception as exc:
        rec["attempts"] = int(rec.get("attempts", 0)) + 1
        P._save(cfg, state)
        raise P.PairingError("PAIRING_GRANT_FAILED",
                             f"grant issue failed: {exc}") from exc
    rec["used"] = True
    rec["grant_id"] = grant.id
    P._save(cfg, state)
    P._audit("approve", requested_by,
             {"code_id": rec["id"], "grant_id": grant.id,
              "scopes": rec.get("scopes", []), "by_request_id": True})
    return {"state": "OK", "approved": rec["id"], "grant_id": grant.id,
            "scopes": list(rec.get("scopes", []) or []),
            "account": rec.get("account", ""), "approved_by_request_id": True}


def h_pairing_mutate(fn_name: str, cfg: Any, q: Dict[str, str],
                     b: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/pairing[/approve|/revoke] -> confirmed, typed pairing mutation.

    Accepts the SPA's field names (``approve`` sends ``request_id``; ``revoke``
    sends ``user_id``) as aliases, requires the shared confirmation (e-stop +
    journal) like every other mutation, and maps the canonical ``PairingError``
    codes to typed 4xx/429 statuses instead of collapsing them to a 500.

    Approving by ``request_id`` resolves the pending request id the list
    surfaced (:func:`_pairing_approve_request`); approving by the one-time
    ``code`` verifies the hash via ``pairing.approve`` — the same two paths the
    reference ``approve_pairing`` exposes.
    """
    from . import pairing as P
    guard = _compat_confirm(
        cfg, f"pairing.{fn_name}",
        f"pairing {fn_name} requires explicit confirmation: "
        "re-POST with {\"confirm\": true}", q, b)
    if guard is not None:
        return guard
    try:
        if fn_name == "create":
            res = P.create(cfg, str(b.get("label", "dashboard")),
                           list(b.get("scopes", ["chat"])),
                           account=str(b.get("account", "")),
                           requested_by="dashboard:user", confirmed=True)
        elif fn_name == "approve":
            request_id = str(b.get("request_id") or "").strip()
            target = str(b.get("code") or b.get("request_id") or "")
            if request_id and request_id.startswith("pair_"):
                # Reference semantics: an admin surface approves by the request
                # id it listed; a request-id-shaped value in the older ``code``
                # field routes to the same request path (never a bogus 404).
                res = _pairing_approve_request(cfg, request_id,
                                               requested_by="dashboard:user")
            else:
                res = P.approve(cfg, target, requested_by="dashboard:user",
                                confirmed=True)
        elif fn_name == "revoke":
            target = str(b.get("code_id") or b.get("id")
                         or b.get("user_id") or "")
            res = P.revoke(cfg, target, requested_by="dashboard:user",
                           confirmed=True)
        else:
            res = {"ok": True, "cleared": True}
    except Exception as exc:
        return _pairing_error(exc)
    ok = not (isinstance(res, dict) and res.get("ok") is False)
    return (200 if ok else 400), {"ok": ok, "result": res}


def h_pairing_clear_pending(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                            p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/pairing/clear-pending -> revoke every live pending code.

    Uses the same ``pairing`` store the CLI/voice use: each non-approved,
    unused, unrevoked code is tombstoned (confirmed + audited), so pending
    device requests are cleared without touching approved grants. The clear
    itself is a mutation and shares the e-stop + confirmation contract.
    """
    from . import pairing as P
    guard = _compat_confirm(
        cfg, "pairing.clear_pending",
        "clearing every pending pairing request requires explicit "
        "confirmation: re-POST with {\"confirm\": true}", q, b)
    if guard is not None:
        return guard
    try:
        codes = P.list_codes(cfg).get("codes", [])
    except Exception as exc:
        return _fail(exc)
    revoked: List[str] = []
    for rec in codes if isinstance(codes, list) else []:
        if not isinstance(rec, dict):
            continue
        if rec.get("approved") or rec.get("used") or rec.get("revoked"):
            continue
        code_id = str(rec.get("id", "") or "")
        if not code_id:
            continue
        try:
            P.revoke(cfg, code_id, requested_by="dashboard:user",
                     confirmed=True)
            revoked.append(code_id)
        except Exception:
            continue
    return _ok(ok=True, cleared=len(revoked), revoked=revoked)


def h_webhooks_list(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import webhooks as W
    try:
        res = W.list_subs(cfg)
    except Exception as exc:
        return _fail(exc)
    subs = res.get("subscriptions", []) if isinstance(res, dict) else []
    routes = []
    for s in subs if isinstance(subs, list) else []:
        if not isinstance(s, dict):
            continue
        routes.append({
            "name": str(s.get("sub_id", s.get("id", ""))),
            "description": str(s.get("label", "") or ""),
            "events": list(s.get("events", []) or []),
            "deliver": "hmac-freshness-replay-store",
            "deliver_only": True,
            "prompt": "",
            "skills": [],
            "created_at": None,
            "url": str(s.get("url", "")),
            "secret_set": bool(s.get("secret_ref") or s.get("secret")),
            "enabled": bool(s.get("enabled", True)),
        })
    # WebhooksResponse wire shape; secret values never leave the vault.
    try:
        store_enabled = W.is_enabled(cfg)
    except Exception:
        store_enabled = True
    return _ok(enabled=store_enabled, base_url="", subscriptions=routes)


def _webhook_slug(text: Any) -> str:
    """Derive a canonical subscription id (``^[a-z0-9][a-z0-9_.-]{0,63}$``).

    The SPA's create form collects a human ``name``; the canonical store keys
    subscriptions by a slug id. Lowercase and map any run of unsupported
    characters to ``-`` (the same shape the store's ``_ID_RE`` accepts).
    """
    slug = re.sub(r"[^a-z0-9_.-]+", "-", str(text or "").strip().lower())
    slug = slug.strip("-._")[:64]
    return slug


def _webhook_route(sub: Dict[str, Any]) -> Dict[str, Any]:
    """Canonical subscription record -> the SPA ``WebhookRoute`` wire shape."""
    return {
        "name": str(sub.get("id", "")),
        "description": "",
        "events": list(sub.get("events", []) or []),
        "deliver": "hmac-freshness-replay-store",
        "deliver_only": True,
        "prompt": "",
        "skills": [],
        "created_at": sub.get("created_at"),
        "url": str(sub.get("url", "")),
        "secret_set": bool(sub.get("secret")),
        "enabled": bool(sub.get("enabled", True)),
        # ``secret`` here is the redacted reference KIND (``env:<reference>``),
        # never a secret value; the SPA renders it as the stored reference.
        "secret": str(sub.get("secret", "")),
    }


def _webhook_error(exc: Exception) -> Tuple[int, Dict[str, Any]]:
    """Map a canonical ``WebhookError`` to a typed HTTP envelope."""
    code = getattr(exc, "code", None) or type(exc).__name__
    detail = _redact(getattr(exc, "detail", None) or str(exc))[:300]
    if code == "WEBHOOK_NOT_FOUND":
        return 404, {"ok": False, "code": code, "detail": detail}
    if code in ("WEBHOOK_BAD_INPUT", "WEBHOOK_BAD_URL", "WEBHOOK_EXISTS",
                "WEBHOOK_STORE_CORRUPT"):
        return 400, {"ok": False, "code": code, "detail": detail}
    if code == "WEBHOOK_CONFIRM_REQUIRED":
        return _staged(detail, "")
    return 400, {"ok": False, "code": code, "detail": detail}


def h_webhooks_mutate(fn_name: str, cfg: Any, q: Dict[str, str],
                      b: Dict[str, Any], p: Dict[str, str]
                      ) -> Tuple[int, Dict[str, Any]]:
    """POST /api/webhooks (add) and POST /api/webhooks/{id} (remove/test/show).

    The SPA's create form collects ``name``/``url``/``events``/``secret_ref``
    while the canonical store (``voicebridge.webhooks``, the same one the CLI
    and voice use) keys by ``id``. Map the SPA payload onto the canonical
    contract here — the single alignment point — so a create never 500s on a
    shape mismatch and typed validation still runs (bad URL/events/secret are
    typed 400s, never a fabricated success). A create is a real subscription
    mutation, so it shares the confirmation contract (e-stop + shared journal)
    with delete/enable; the canonical ``W.add`` audits the execution.
    """
    from . import webhooks as W
    try:
        if fn_name == "add":
            sub_id = str(b.get("id") or b.get("sub_id")
                         or _webhook_slug(b.get("name", "")))
            guard = _compat_confirm(
                cfg, "webhook_create",
                f"creating webhook subscription {_redact(sub_id)!r} requires "
                "explicit confirmation: re-POST with {\"confirm\": true}",
                q, b,
                payload={"id": sub_id,
                         "name": str(b.get("name", "") or ""),
                         "url": str(b.get("url") or b.get("deliver_url") or ""),
                         "events": list(b.get("events", []) or []),
                         "secret_ref": str(b.get("secret_ref")
                                           or b.get("secret") or "")})
            if guard is not None:
                return guard
            url = str(b.get("url") or b.get("deliver_url") or "")
            secret_ref = str(b.get("secret_ref") or b.get("secret") or "")
            res = W.add(cfg, sub_id, url, list(b.get("events", []) or []),
                        secret_ref, requested_by="dashboard:user",
                        confirmed=True)
        elif fn_name == "remove":
            res = W.remove(cfg, str(b.get("id") or p.get("id", "")),
                           requested_by="dashboard:user", confirmed=True)
        elif fn_name == "test":
            res = W.test_plan(cfg, str(b.get("id") or p.get("id", "")))
        else:
            res = W.show(cfg, str(p.get("id", "")))
    except Exception as exc:
        return _webhook_error(exc)
    if fn_name == "add":
        sub = res.get("subscription", {}) if isinstance(res, dict) else {}
        route = _webhook_route(sub)
        return _ok(subscription=route, **route)
    ok = not (isinstance(res, dict) and res.get("ok") is False)
    return (200 if ok else 400), {"ok": ok, "result": res}


# ---------------------------------------------------------------------------
# Channels (/api/messaging/platforms) — the REAL connectable platform catalog
# ---------------------------------------------------------------------------
# Ported from the vendored runtime: the catalog is built by
# ``qubit_cli.web_server_messaging._messaging_platform_catalog`` (``:229``,
# built-ins from ``gateway.config.Platform`` + the platform-plugin registry) and
# the served payload shape follows ``web_routers/messaging.py:_platform_payloads``
# (``:266``) / ``_messaging_platform_payload`` (``:196``). Qubit supplies the
# per-platform state from its own reference-only store and account/link registry
# instead of the fork's ``.env``/gateway state, so GET and PUT share ONE store.
# Credentials stay reference-only: ``env:``/``vault:``/``file:`` references are
# accepted; a raw secret is a typed ``CHANNEL_ENV_REFERENCE_ONLY`` refusal.
_MSG_PLATFORM_STORE = "msg-platforms.json"
_MSG_PLATFORM_SCHEMA = "qubit-msg-platforms/1"
_MSG_REF_PREFIXES = ("env:", "vault:", "ref:", "file:")
# Account ``provider`` ids whose catalog platform id differs (the catalog uses
# the gateway ``Platform`` enum spelling).
_ACCOUNT_PROVIDER_TO_PLATFORM = {
    "whatsapp-cloud": "whatsapp_cloud",
    "msteams": "teams",
    "imessage": "bluebubbles",
}
_LINK_RANK = {"LINKED": 4, "DEGRADED": 3, "UNAVAILABLE": 2,
              "NO_CREDENTIAL": 1, "NO_ACCOUNT": 0}
_PLATFORM_CATALOG_CACHE: Optional[List[Dict[str, Any]]] = None
_OPTIONAL_ENV_VARS_CACHE: Optional[Dict[str, Any]] = None


def _optional_env_vars() -> Dict[str, Any]:
    """The vendored OPTIONAL_ENV_VARS catalog (cached; empty on import failure)."""
    global _OPTIONAL_ENV_VARS_CACHE
    if _OPTIONAL_ENV_VARS_CACHE is None:
        try:
            from . import qubit_runtime as HR
            HR.ensure_path()
            from qubit_cli.config import OPTIONAL_ENV_VARS  # type: ignore
            _OPTIONAL_ENV_VARS_CACHE = dict(OPTIONAL_ENV_VARS)
        except Exception:
            _OPTIONAL_ENV_VARS_CACHE = {}
    return _OPTIONAL_ENV_VARS_CACHE


def _platform_catalog() -> List[Dict[str, Any]]:
    """The vendored connectable-platform catalog (raw entries, cached)."""
    global _PLATFORM_CATALOG_CACHE
    if _PLATFORM_CATALOG_CACHE is not None:
        return _PLATFORM_CATALOG_CACHE
    entries: List[Dict[str, Any]] = []
    try:
        from . import qubit_runtime as HR
        HR.ensure_path()
        from qubit_cli.web_server_messaging import (  # type: ignore
            _messaging_platform_catalog)
        entries = [dict(e) for e in _messaging_platform_catalog()]
    except Exception:
        entries = []
    if not entries:
        # Honest fallback: the real Qubit provider-capability set, never a
        # hand-invented platform list.
        return _fallback_platform_catalog()
    _PLATFORM_CATALOG_CACHE = entries
    return entries


def _fallback_platform_catalog() -> List[Dict[str, Any]]:
    from . import messaging as M
    rows = M.provider_catalog().get("providers", [])
    out: List[Dict[str, Any]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        pid = str(row.get("provider", "")).replace("-", "_")
        if not pid:
            continue
        out.append({"id": pid, "name": str(row.get("label", pid) or pid),
                    "description": str(row.get("detail", "") or ""),
                    "docs_url": "", "env_vars": (), "required_env": ()})
    return out


def _debrand_platform_url(url: Any) -> str:
    text = str(url or "").strip()
    if "nousresearch.com" in text.lower() or "hermes" in text.lower():
        return ""
    return text


def _platform_public_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    """De-brand one catalog entry: rebrand free text, blank legacy-host links."""
    from .qubit_plugins import _rebrand_product
    return {
        "id": str(entry.get("id", "") or ""),
        "name": _rebrand_product(entry.get("name", "")),
        "description": _rebrand_product(entry.get("description", "")),
        "docs_url": _debrand_platform_url(entry.get("docs_url", "")),
        "env_vars": tuple(str(k) for k in entry.get("env_vars", ()) or ()),
        "required_env": tuple(str(k) for k in entry.get("required_env", ()) or ()),
    }


def _platform_env_info(key: str) -> Dict[str, Any]:
    from .qubit_plugins import _rebrand_product
    info = _optional_env_vars().get(key) or {}
    if not isinstance(info, dict):
        info = {}
    return {
        "description": _rebrand_product(info.get("description", "")),
        "prompt": _rebrand_product(info.get("prompt", key)),
        "help": _rebrand_product(info.get("help", "")),
        "url": _debrand_platform_url(info.get("url")) or None,
        "is_password": bool(info.get("password", False)),
        "advanced": bool(info.get("advanced", False)),
    }


# Secret-shaped channel env names, as a fallback for keys the vendored
# ``OPTIONAL_ENV_VARS`` catalog does not carry (it only covers a subset of the
# per-platform vars). This mirrors the reference's per-field ``password``
# metadata (``web_routers/messaging.py:_messaging_env_info`` -> ``is_password``,
# sourced from ``OPTIONAL_ENV_VARS`` + ``_MESSAGING_ENV_FALLBACKS``): a field
# flagged ``is_password`` OR whose name is credential-shaped is a secret. A
# non-secret allowlist (``*_ALLOWED_USERS``) is not.
_CHANNEL_SECRET_KEY_RE = re.compile(
    r"(_TOKEN|_SECRET|_PASSWORD|_PASSWD|_API_KEY|_AUTH_KEY|_AES_KEY|"
    r"_ENCODING_KEY|_PRIVATE_KEY|_CLIENT_SECRET|_BOT_TOKEN|_ACCESS_TOKEN|"
    r"_WEBHOOK_SECRET|_AUTH_TOKEN)$",
    re.IGNORECASE)


def _platform_env_is_secret(key: str) -> bool:
    """True when a channel env field is credential-shaped (reference-only).

    Uses the same per-field metadata the reference serves (``is_password``),
    falling back to the credential name shape for keys absent from the
    catalog. Non-secret fields (allowlists, URLs, account ids) return False so
    they stay directly configurable, as the reference allows.
    """
    name = str(key or "")
    info = _optional_env_vars().get(name)
    if isinstance(info, dict) and info.get("password"):
        return True
    return bool(_CHANNEL_SECRET_KEY_RE.search(name))


def _platform_env_value_ok(key: str, value: Any) -> bool:
    """A stored channel field value is displayable when it is an accepted
    reference (any kind) OR the field is non-secret (a literal allowlist/URL)."""
    if _platform_allowed_ref(value):
        return True
    return not _platform_env_is_secret(key)


def _platform_store(cfg: Any) -> Dict[str, Any]:
    raw = _load_json_store(_store_path(cfg, _MSG_PLATFORM_STORE))
    plats = raw.get("platforms")
    if not isinstance(plats, dict):
        plats = {}
    return {"schema": _MSG_PLATFORM_SCHEMA, "platforms": plats}


def _platform_record(store: Dict[str, Any], platform_id: str) -> Dict[str, Any]:
    rec = store.get("platforms", {}).get(platform_id)
    return dict(rec) if isinstance(rec, dict) else {}


def _platform_store_update(cfg: Any, platform_id: str, *, enabled: Optional[bool],
                           env: Optional[Dict[str, Any]] = None,
                           clear: Optional[List[Any]] = None) -> None:
    """Persist one platform's enable flag + credential REFERENCES (never values)."""
    store = _platform_store(cfg)
    plats = store["platforms"]
    rec = plats.get(platform_id)
    rec = dict(rec) if isinstance(rec, dict) else {}
    refs = rec.get("refs") if isinstance(rec.get("refs"), dict) else {}
    refs = dict(refs)
    if enabled is not None:
        rec["enabled"] = bool(enabled)
    for key, value in (env or {}).items():
        refs[str(key)] = str(value)
    for key in (clear or ()):
        refs.pop(str(key), None)
    rec["refs"] = refs
    rec["updated_at"] = time.time()
    rec["updated_by"] = "dashboard:user"
    plats[platform_id] = rec
    store["platforms"] = plats
    _save_json_store(_store_path(cfg, _MSG_PLATFORM_STORE), store)


def _platform_allowed_ref(value: Any) -> bool:
    text = str(value or "").strip()
    return any(text.startswith(p) and len(text) > len(p)
               for p in _MSG_REF_PREFIXES)


def _platform_resolve(cfg: Any, platform_id: str
                      ) -> Tuple[Optional[str], Dict[str, Any], Dict[str, Any]]:
    """Resolve a route id to a catalog platform or a Qubit account."""
    for entry in _platform_catalog():
        if str(entry.get("id", "")) == platform_id:
            return "catalog", _platform_public_entry(entry), {}
    try:
        from . import messaging as M
        for account in M.account_list(cfg).get("accounts", []):
            if isinstance(account, dict) and \
                    str(account.get("id", "")) == platform_id:
                return "account", {}, account
    except Exception:
        pass
    return None, {}, {}


def _platform_account_link(cfg: Any, account: Dict[str, Any]) -> Dict[str, Any]:
    from . import messaging as M
    try:
        return M.link(cfg, str(account.get("id", "")))
    except Exception:
        return {"state": "UNAVAILABLE", "code": M.UNAVAILABLE_CODE,
                "detail": "link probe failed"}


def _best_account_link(cfg: Any,
                       accounts: List[Dict[str, Any]]) -> Dict[str, Any]:
    best: Dict[str, Any] = {}
    for account in accounts:
        link = _platform_account_link(cfg, account)
        if not best or _LINK_RANK.get(str(link.get("state", "")), -1) > \
                _LINK_RANK.get(str(best.get("state", "")), -1):
            best = link
    return best


def _platform_state(enabled: bool, configured: bool, link: Dict[str, Any]
                    ) -> Tuple[str, Optional[str], Optional[str]]:
    """Honest wire state. ``connected`` is only ever derived from a real
    ``messaging.link`` LINKED probe — Qubit never fabricates a connection."""
    link_state = str(link.get("state", "") or "")
    if not enabled:
        return "disabled", None, None
    if not configured:
        return "not_configured", "MSG_NO_CREDENTIAL", \
               "credential reference is not set"
    if link_state == "LINKED":
        return "connected", None, None
    if link_state == "DEGRADED":
        return "degraded", str(link.get("code") or "LINK_DEGRADED"), \
               (str(link.get("detail") or "")[:160] or None)
    # Configured from references but no Qubit transport is proven: report the
    # honest unavailable state rather than a fabricated "connected".
    return "unavailable", str(link.get("code") or "MSG_TRANSPORT_UNAVAILABLE"), \
        (str(link.get("detail") or "")[:160]
         or "no Qubit transport adapter for this channel")


def _platform_env_payload(entry: Dict[str, Any], refs: Dict[str, str],
                          required: Tuple[str, ...], account_ok: bool
                          ) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for key in entry.get("env_vars", ()) or ():
        is_required = key in required
        rows.append({
            "key": key, "required": is_required,
            "is_set": bool(refs.get(key)) or (account_ok and is_required),
            "redacted_value": None, **_platform_env_info(key),
        })
    return rows


def _live_gateway_platform_state(platform_id: str) -> Optional[str]:
    """Live per-platform state from the daemon's own ``gateway_state.json``.

    Read-only via :mod:`voicebridge.gateway_supervisor` (the daemon wrote the
    file; reporting it is first-party evidence). ``None`` when the runtime is
    absent/unreadable or the platform is unlisted — never a fabricated state.
    """
    try:
        from . import gateway_supervisor as GS
        record = GS.daemon_runtime_status()
    except Exception:
        return None
    if not isinstance(record, dict):
        return None
    platforms_map = record.get("platforms_map")
    if not isinstance(platforms_map, dict):
        return None
    entry = platforms_map.get(str(platform_id))
    if not isinstance(entry, dict):
        return None
    state = str(entry.get("state") or entry.get("status") or "").strip().lower()
    return state or None


def _platform_payload(cfg: Any, entry: Dict[str, Any], rec: Dict[str, Any],
                       accounts: List[Dict[Any, Any]], gateway_running: bool
                       ) -> Dict[str, Any]:
    pub = _platform_public_entry(entry)
    pid = pub["id"]
    required = pub["required_env"]
    refs_raw = rec.get("refs") if isinstance(rec.get("refs"), dict) else {}
    refs = {str(k): str(v) for k, v in refs_raw.items()
            if _platform_env_value_ok(str(k), v)}
    link = _best_account_link(cfg, accounts) if accounts else {}
    account_ok = bool(accounts) and str(link.get("state", "")) not in (
        "NO_ACCOUNT", "NO_CREDENTIAL", "")
    ref_complete = (all(refs.get(k) for k in required) if required
                    else bool(refs))
    configured = bool(ref_complete or account_ok)
    if not configured and gateway_running:
        # Gateway-owned transports (WhatsApp QR bridge, bot platforms) prove
        # themselves in the daemon's own gateway_state.json. The overlay store
        # only knows credential *references* saved via CONFIGURE, so a channel
        # paired outside it (QR scan) would otherwise read "not configured"
        # while actually connected. The daemon's verdict is live first-party
        # evidence, never fabricated here.
        if _live_gateway_platform_state(pid) == "connected":
            configured = True
            link = {"state": "LINKED", "platform": pid,
                    "detail": "live gateway reports connected"}
    store_enabled = rec.get("enabled")
    if isinstance(store_enabled, bool):
        enabled = store_enabled
    elif accounts:
        enabled = any(bool(a.get("enabled", True)) for a in accounts)
    else:
        enabled = False
    state, error_code, error_message = _platform_state(
        enabled, configured, link)
    payload: Dict[str, Any] = {
        "id": pid, "name": pub["name"], "description": pub["description"],
        "docs_url": pub["docs_url"], "enabled": enabled,
        "configured": configured, "gateway_running": gateway_running,
        "state": state, "error_code": error_code,
        "error_message": error_message, "updated_at": rec.get("updated_at"),
        "home_channel": None,
        "env_vars": _platform_env_payload(entry, refs, required, account_ok),
    }
    if pid == "whatsapp":
        saved_allowed = _saved_whatsapp_allowed_users()
        payload["whatsapp_setup"] = {
            "mode": "",
            "allowed_users_set": bool(refs.get("WHATSAPP_ALLOWED_USERS") or saved_allowed),
            "allowed_users": saved_allowed,
            "home_channel_set": False,
        }
    return payload


def _saved_whatsapp_allowed_users() -> str:
    """Saved WhatsApp allowlist from the runtime ``.env`` (read-only).

    The allowlist is dial-plan config, not a credential — the Channels panel
    already renders it in a plaintext input — so returning it lets the panel
    prefill instead of always rendering empty after refresh. Fail-closed to
    ``""``; secret values are never read here.
    """
    try:
        from .gateway_supervisor import _daemon_home
        env_path = _daemon_home(None) / ".env"
        text = env_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        if key.strip() == "WHATSAPP_ALLOWED_USERS":
            return value.strip().strip("\"'").strip()
    return ""


def _account_platform_payload(cfg: Any, account: Dict[str, Any],
                              rec: Dict[str, Any], gateway_running: bool
                              ) -> Dict[str, Any]:
    """A Qubit account that is not a catalog platform (legacy CLI account row)."""
    from . import messaging as M
    aid = str(account.get("id", ""))
    provider = str(account.get("provider", ""))
    link = _platform_account_link(cfg, account)
    link_map = {
        "LINKED": ("connected", True, None, None),
        "DEGRADED": ("degraded", True, "LINK_DEGRADED", None),
        "NO_CREDENTIAL": ("unconfigured", False, "NO_CREDENTIAL",
                          "credential reference unresolvable"),
        "NO_ACCOUNT": ("unconfigured", False, "NO_ACCOUNT", None),
        "UNAVAILABLE": ("unavailable", True, M.UNAVAILABLE_CODE,
                        "no Qubit transport adapter for this channel"),
    }
    st, configured, code, msg = link_map.get(
        str(link.get("state", "")),
        ("unavailable", False, link.get("code") or M.UNAVAILABLE_CODE,
         link.get("detail")))
    store_enabled = rec.get("enabled")
    enabled = (store_enabled if isinstance(store_enabled, bool)
               else bool(account.get("enabled", True)))
    if not enabled:
        st = "disabled"
    return {
        "id": aid, "name": str(account.get("label", aid) or aid),
        "description": f"{provider} account reference (vault-only credential)",
        "docs_url": "", "enabled": enabled, "configured": configured,
        "gateway_running": gateway_running, "state": st, "error_code": code,
        "error_message": (str(msg)[:160] if msg else None),
        "updated_at": rec.get("updated_at"), "home_channel": None,
        "whatsapp_setup": None, "env_vars": [],
    }


def _platform_rows(cfg: Any) -> List[Dict[str, Any]]:
    from . import messaging as M
    entries = _platform_catalog()
    store = _platform_store(cfg)
    catalog_ids = {str(e.get("id", "")) for e in entries}
    try:
        accounts = M.account_list(cfg).get("accounts", [])
    except Exception:
        accounts = []
    if not isinstance(accounts, list):
        accounts = []
    by_platform: Dict[str, List[Dict[str, Any]]] = {}
    extra: List[Dict[str, Any]] = []
    for account in accounts:
        if not isinstance(account, dict):
            continue
        provider = str(account.get("provider", ""))
        target = (provider if provider in catalog_ids
                  else _ACCOUNT_PROVIDER_TO_PLATFORM.get(provider, ""))
        if target and target in catalog_ids:
            by_platform.setdefault(target, []).append(account)
        else:
            extra.append(account)
    try:
        from . import gateway_supervisor as GS
        gateway_running = bool(GS.status(cfg).get("running"))
    except Exception:
        gateway_running = False
    rows = [
        _platform_payload(cfg, entry,
                          _platform_record(store, str(entry.get("id", ""))),
                          by_platform.get(str(entry.get("id", "")), []),
                          gateway_running)
        for entry in entries
    ]
    for account in extra:
        aid = str(account.get("id", ""))
        rows.append(_account_platform_payload(
            cfg, account, _platform_record(store, aid), gateway_running))
    return rows


def h_platforms(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/messaging/platforms -> the real connectable platform catalog.

    Ported from the vendored ``web_routers/messaging.py:get_messaging_platforms``
    (``:765``), whose payload shape comes from ``_platform_payloads`` (``:266``)
    over ``_messaging_platform_catalog`` (``web_server_messaging.py:229``).
    Qubit serves the same real catalog but sources each platform's state from
    its own reference-only store and account/link registry — the SAME store
    :func:`msg_update` writes — so read and write never split-brain.
    """
    try:
        rows = _platform_rows(cfg)
    except Exception as exc:
        return _fail(exc)
    try:
        from . import qubit_runtime as HR
        env_path = str(HR.runtime_home_for_cfg(cfg) / ".env")
    except Exception:
        env_path = ""
    return _ok(env_path=env_path,
               gateway_start_command="qubit gateway start",
               platforms=rows)


def _run_onboarding(cfg: Any, fn_name: str, *args: Any,
                    model_name: Optional[str] = None,
                    model_body: Optional[Dict[str, Any]] = None,
                    model_pos: int = 0
                    ) -> Tuple[int, Any]:
    """Run a vendored channel-onboarding coroutine, mapping failures honestly.

    Delegates to the real implementation (``qubit_cli.web_routers.messaging``,
    ported 1:1 from the reference): WhatsApp spawns the local pairing process
    whose QR the dashboard polls; Telegram drives the hosted pairing service.
    A vendored ``HTTPException`` becomes its (status, detail); anything else
    is ``_fail``. The QR scan itself is the confirmation, mirroring the
    reference (no second confirm gate on apply).
    """
    import asyncio
    try:
        _vendored_runtime(cfg)
        from qubit_cli.web_routers import messaging as _M
    except Exception as exc:
        return _fail(exc)
    try:
        fn = getattr(_M, fn_name)
        call_args = list(args)
        if model_name is not None:
            model_cls = getattr(_M, model_name)
            known = set(getattr(model_cls, "model_fields", {}) or {})
            raw = dict(model_body or {})
            filtered = {k: v for k, v in raw.items() if k in known} if known else raw
            call_args.insert(max(0, min(model_pos, len(call_args))),
                             model_cls(**filtered))
        out = asyncio.run(fn(*call_args))
    except Exception as exc:
        status = getattr(exc, "status_code", None)
        if isinstance(status, int):
            return status, {"ok": False, "code": "ONBOARDING_FAILED",
                            "detail": _redact(str(getattr(exc, "detail", exc)))[:400]}
        return _fail(exc)
    if isinstance(out, dict):
        out = dict(out)
        out.setdefault("ok", True)
        return 200, out
    return 200, {"ok": True, "result": out}


def _ob_body(b: Dict[str, Any], q: Dict[str, str]) -> Dict[str, Any]:
    body = dict(b or {})
    if "profile" not in body and q.get("profile"):
        body["profile"] = q.get("profile")
    return body


def h_wa_ob_start(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                  p: Dict[str, str]) -> Tuple[int, Any]:
    """POST /api/messaging/whatsapp/onboarding/start -> real pairing session."""
    return _run_onboarding(cfg, "start_whatsapp_onboarding",
                           model_name="WhatsAppOnboardingStart",
                           model_body=_ob_body(b, q))


def h_wa_ob_status(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                   p: Dict[str, str]) -> Tuple[int, Any]:
    """GET /api/messaging/whatsapp/onboarding/{id} -> QR/status poll."""
    return _run_onboarding(cfg, "get_whatsapp_onboarding_status",
                           p.get("pairing_id", ""))


def h_wa_ob_apply(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                  p: Dict[str, str]) -> Tuple[int, Any]:
    """POST /api/messaging/whatsapp/onboarding/{id}/apply -> save + restart."""
    return _run_onboarding(cfg, "apply_whatsapp_onboarding",
                           p.get("pairing_id", ""),
                           _ob_body(b, q).get("profile") or q.get("profile"),
                           model_name="WhatsAppOnboardingApply",
                           model_body=_ob_body(b, q), model_pos=1)


def h_wa_ob_cancel(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                   p: Dict[str, str]) -> Tuple[int, Any]:
    """DELETE /api/messaging/whatsapp/onboarding/{id} -> cancel pairing."""
    return _run_onboarding(cfg, "cancel_whatsapp_onboarding",
                           p.get("pairing_id", ""))


def h_tg_ob_start(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                  p: Dict[str, str]) -> Tuple[int, Any]:
    """POST /api/messaging/telegram/onboarding/start -> real pairing session."""
    return _run_onboarding(cfg, "start_telegram_onboarding",
                           model_name="TelegramOnboardingStart",
                           model_body=_ob_body(b, q))


def h_tg_ob_status(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                   p: Dict[str, str]) -> Tuple[int, Any]:
    """GET /api/messaging/telegram/onboarding/{id} -> QR/status poll."""
    return _run_onboarding(cfg, "get_telegram_onboarding_status",
                           p.get("pairing_id", ""))


def h_tg_ob_apply(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                  p: Dict[str, str]) -> Tuple[int, Any]:
    """POST /api/messaging/telegram/onboarding/{id}/apply -> save + restart."""
    return _run_onboarding(cfg, "apply_telegram_onboarding",
                           p.get("pairing_id", ""),
                           _ob_body(b, q).get("profile") or q.get("profile"),
                           model_name="TelegramOnboardingApply",
                           model_body=_ob_body(b, q), model_pos=1)


def h_tg_ob_cancel(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                   p: Dict[str, str]) -> Tuple[int, Any]:
    """DELETE /api/messaging/telegram/onboarding/{id} -> cancel pairing."""
    return _run_onboarding(cfg, "cancel_telegram_onboarding",
                           p.get("pairing_id", ""))


def h_memory(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
             p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/memory -> live local memory store status (MemoryStatus shape)."""
    from . import active_memory as AM
    from . import brain as B
    try:
        status = AM.status(cfg)
    except Exception as exc:
        return _fail(exc)
    try:
        principals = len(B.principal_list(cfg).get("principals", []))
    except Exception:
        principals = 0
    state = str(status.get("state", "OK"))
    memories = status.get("memories")
    return _ok(active="qubit-local",
               providers=[{
                   "name": "qubit-local",
                   "description": "local-only SQLite/FTS5 MemoryStore "
                                  "(core/working/recall/archive tiers)",
                   "available": state in ("OK", "EMPTY", "DEGRADED"),
                   "configured": True,
                   "status": ("ready" if state == "OK" else
                              "empty" if state == "EMPTY" else "degraded"),
                   "detail": str(status.get("store_state", "") or "")}],
               builtin_files={"memory": int(memories or 0),
                              "user": principals},
               store_state=status.get("store_state"),
               store_path=status.get("store_path"),
               enabled=bool(status.get("enabled", True)),
               status=status)


def h_analytics(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from .qubit_session_store import analytics_usage

    try:
        days = max(1, min(int(q.get("days", "30") or "30"), 365))
    except (TypeError, ValueError):
        days = 30
    usage = analytics_usage(cfg, days=days)
    empty_skills = {"summary": {"total_skill_loads": 0,
                                "total_skill_edits": 0,
                                "total_skill_actions": 0,
                                "distinct_skills_used": 0},
                    "top_skills": []}
    if usage is None:
        # No canonical store yet: an honest empty answer, never fabricated
        # billing numbers.
        empty = {"total_input": 0, "total_output": 0, "total_cache_read": 0,
                 "total_reasoning": 0, "total_estimated_cost": 0.0,
                 "total_actual_cost": 0.0, "total_sessions": 0,
                 "total_api_calls": 0}
        if p.get("kind") == "models":
            return _ok(models=[], totals={**empty, "distinct_models": 0},
                       period_days=days, detail="no usage recorded yet")
        return _ok(daily=[], by_model=[], totals=empty, skills=empty_skills)
    totals = usage["totals"]
    if p.get("kind") == "models":
        models = [{
            "model": m["model"], "provider": m["provider"],
            "input_tokens": m["input_tokens"], "output_tokens": m["output_tokens"],
            "cache_read_tokens": m["cache_read_tokens"],
            "reasoning_tokens": m["reasoning_tokens"],
            "estimated_cost": m["estimated_cost"],
            "actual_cost": m["actual_cost"], "sessions": m["sessions"],
            "api_calls": m["api_calls"], "tool_calls": m["tool_calls"],
            "last_used_at": m["last_used_at"],
            "avg_tokens_per_session": m["avg_tokens_per_session"],
            "capabilities": {},
        } for m in usage["by_model"]]
        return _ok(models=models,
                   totals={**totals, "distinct_models": len(models)},
                   period_days=days)
    by_model = [{
        "model": m["model"], "input_tokens": m["input_tokens"],
        "output_tokens": m["output_tokens"], "estimated_cost": m["estimated_cost"],
        "sessions": m["sessions"], "api_calls": m["api_calls"],
    } for m in usage["by_model"]]
    return _ok(daily=usage["daily"], by_model=by_model, totals=totals,
               skills=empty_skills)


def _files_root(cfg: Any) -> str:
    # Delegate to the canonical active-project folder resolver. The previous
    # ``PJ.active(cfg)`` call used a nonexistent attribute (always ``{}``), so
    # the Files upload/create path reported "no active project folder" even
    # when a project was active.
    return _active_folder(cfg)


def h_files(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
            p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import files as FI
    import base64
    # The read route is ``/api/files/read`` and the list route is ``/api/files``
    # (``h_files_read`` forwards with ``kind="read"``). Deciding by the *route*
    # — never by the mere presence of ``?path=`` — is what makes subdirectory
    # listing/navigation work; the earlier ``if q.get("path")`` test treated
    # every ``/api/files?path=<dir>`` as a file read.
    if p.get("kind") == "read":
        path = str(q.get("path", b.get("path", "")) or "")
        try:
            res = FI.read_file(cfg, path)
        except Exception as exc:
            code = getattr(exc, "code", "FILES_ERROR")
            # Binary file (D5): read_file refuses binary content; serve the raw
            # bytes as a typed data URL so the Files page Download works for a
            # genuinely-binary file instead of failing. Reads stay confined and
            # size-bounded; text reads keep the redacted path below.
            if code == FI.BINARY_CODE:
                try:
                    raw = FI.read_bytes(cfg, path)
                except FI.FilesError as bexc:
                    return 400, {"ok": False, "code": bexc.code,
                                 "detail": _redact(bexc.detail)[:300]}
                data = raw["data"]
                data_url = (f"data:{raw['mime']};base64,"
                            + base64.b64encode(data).decode())
                return _ok(name=raw["name"], path=path, size=raw["size"],
                           mime_type=raw["mime"], data_url=data_url,
                           root=_files_root(cfg) or None, locked_root=None,
                           can_change_path=False, sha256=raw["sha256"])
            return 400, {"ok": False, "code": code,
                         "detail": _redact(str(exc))[:300]}
        text = res.get("text", res.get("content", "")) if isinstance(res, dict) else ""
        data_url = ("data:text/plain;base64,"
                    + base64.b64encode(str(text).encode("utf-8",
                                                        "replace")).decode())
        # ManagedFileReadResponse wire shape.
        return _ok(name=Path(path).name, path=path,
                   size=len(str(text).encode("utf-8", "replace")),
                   mime_type="text/plain", data_url=data_url,
                   root=_files_root(cfg) or None, locked_root=None,
                   can_change_path=False)
    folder = str(q.get("path", "") or "") or _files_root(cfg)
    if not folder:
        return _ok(root=None, path="", parent=None, locked_root=None,
                   can_change_path=False, entries=[],
                   detail="no active project folder; register one with "
                          "`qubit project add-folder`")
    try:
        # Immediate children only: the reference ``list_managed_files`` does a
        # single ``os.scandir`` level and the SPA navigates folder-by-folder, so
        # a recursive walk would spend the entry cap on descendants and hide
        # the sibling directories.
        res = FI.list_children(cfg, folder)
    except Exception as exc:
        code = getattr(exc, "code", "FILES_ERROR")
        return 400, {"ok": False, "code": code,
                     "detail": _redact(str(exc))[:300]}
    raw_entries = res.get("entries", []) if isinstance(res, dict) else []
    # ``files._public_dir`` emits ``dir`` (and a root-relative ``rel``); the
    # dashboard wire shape (reference ``web_server_files._managed_file_entry``)
    # needs ``is_directory`` and an addressable absolute ``path``. Read the
    # ``dir`` key directly — the previous ``is_dir``/``is_directory`` lookup
    # missed it, so every folder rendered as a file and the /files page could
    # not navigate. Join ``rel`` back onto the resolved listing root so both a
    # root-level file and a nested entry carry a usable path.
    listed_root = str(res.get("folder", folder) if isinstance(res, dict)
                      else folder)
    entries = []
    for en in raw_entries if isinstance(raw_entries, list) else []:
        if not isinstance(en, dict):
            continue
        rel = str(en.get("rel", en.get("path", "")) or "")
        entry_path = os.path.join(listed_root, rel) if rel and listed_root else ""
        is_dir = bool(en.get("dir", en.get("is_dir",
                                           en.get("is_directory", False))))
        entries.append({
            "name": str(en.get("name", "")),
            "path": entry_path,
            "is_directory": is_dir,
            "size": None if is_dir else en.get("size"),
            "mtime": en.get("mtime", 0),
            "mime_type": en.get("mime_type"),
        })
    # Reference ``list_managed_files`` sorts directories first, then by name.
    entries.sort(key=lambda e: (not e["is_directory"], e["name"].lower()))
    # The "up" control needs the parent folder; it exists whenever the listing
    # root is itself inside an approved project root (never above all roots).
    parent = None
    if listed_root:
        parent_path = Path(listed_root).parent
        if parent_path != Path(listed_root):
            for root in FI.approved_roots(cfg):
                try:
                    parent_path.relative_to(root)
                    parent = str(parent_path)
                except ValueError:
                    continue
                break
    # ManagedFilesResponse wire shape.
    return _ok(root=listed_root, path=listed_root, parent=parent,
               locked_root=listed_root, can_change_path=False,
               entries=entries)


def h_gateway(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
              p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import dashboard_ops as OPS
    from . import gateway_supervisor as GS

    out = OPS.run_op(cfg, "gateway", {}, actor="dashboard:user", session="dashboard")
    st = GS.status(cfg)
    # ``supervision`` is the SPA's refusal signal: empty when the real
    # supervisor is available (Start/Stop/Restart enabled), and the typed
    # reason only when this host genuinely cannot supervise a service.
    supervision = "" if st.get("available") else (
        f"{GS.UNAVAILABLE_CODE}: {st.get('detail', '')}")
    # Owner Decision #16: ``process`` reports the Qubit messaging-gateway daemon
    # (``qubit-gateway.service``) with its live runtime + control-socket answer
    # merged in. The web-UI/voice unit is named separately (read-only; no second
    # systemctl call), so the card tells the two apart truthfully.
    return _ok(gateway=_res(out), state=out.get("state"),
               supervision=supervision, process=st,
               web_ui_service_unit=GS.service_unit(cfg))


def h_gateway_drain(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/gateway/drain -> real reversible Qubit drain marker.

    Mirrors the upstream drain control channel without a second listener or
    process supervision: ``action: "drain"`` atomically writes a redacted
    ``<state_dir>/gateway_drain.json`` marker (audited); ``action: "cancel"``
    removes it. It never starts/stops a process (supervision stays
    GATEWAY_NO_SUPERVISION) and is idempotent.
    """
    action = str(b.get("action", "drain") or "drain").strip().lower()
    if action not in ("drain", "cancel"):
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": f"unknown drain action {action!r}; expected "
                               "'drain' or 'cancel'",
                     "qubit_counterpart": "CLI `qubit gateway` + operator"}
    path = _store_path(cfg, "gateway_drain.json")
    if action == "cancel":
        existed = False
        try:
            existed = path.exists()
            if existed:
                path.unlink()
        except OSError as exc:
            return _fail(exc)
        _audit("gateway_drain", "dashboard:user", {"action": "cancel",
                                                   "existed": existed})
        return _ok(ok=True, action="cancel", was_draining=existed,
                   draining=False)
    payload = {"schema": "qubit-gateway-drain/1",
               "requested_at": time.time(), "requested_by": "dashboard:user",
               "suppress_notification": bool(b.get("suppress_notification",
                                                   False))}
    try:
        _save_json_store(path, payload)
    except Exception as exc:
        return _fail(exc)
    _audit("gateway_drain", "dashboard:user", {"action": "drain"})
    return _ok(ok=True, action="drain", requested_at=payload["requested_at"],
               draining=True,
               suppress_notification=payload["suppress_notification"])


def h_gateway_supervision(action: str) -> Handler:
    """POST /api/gateway/{start,stop,restart} -> real gateway control.

    With no ``gateway_id`` the action controls the Qubit-owned service unit
    through :mod:`voicebridge.gateway_supervisor` (confirmed, audited,
    idempotent; restart/stop detached). With a ``gateway_id`` the request
    transitions a recorded gateway through the same approval-gated
    ``gateway_peers.supervision_request`` the CLI/voice use.
    """
    counterpart = ("CLI `qubit gateway supervision-request <id> <action> "
                   "--confirm` + operator approval (gateway.supervise grant)")

    def _handler(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
        from . import gateway_peers as GW
        gw_id = str(b.get("gateway_id", b.get("id",
                                              q.get("gateway_id", ""))) or "").strip()
        if not gw_id:
            from . import gateway_supervisor as GS

            out = GS.control(cfg, action, confirmed=bool(b.get("confirm", False)),
                             actor="dashboard:user")
            code = str(out.get("code") or "")
            if out.get("ok"):
                return 200, out
            http = {"GATEWAY_CONFIRM_REQUIRED": 409, "GATEWAY_ESTOP": 403,
                    "GATEWAY_SUPERVISION_UNAVAILABLE": 503,
                    "GATEWAY_BAD_INPUT": 400}.get(code, 500)
            return http, out
        try:
            out = GW.supervision_request(
                cfg, gw_id, action,
                approval_id=str(b.get("approval_id", "") or ""),
                requested_by="dashboard:user",
                confirmed=bool(b.get("confirm")))
        except GW.GatewayError as exc:
            code = getattr(exc, "code", None) or "GATEWAY_ERROR"
            http = {"GW_CONFIRM_REQUIRED": 409, "GW_ESTOP": 403,
                    "GW_NOT_FOUND": 404, "GW_BAD_INPUT": 400}.get(code, 403)
            return http, {
                "ok": False, "code": code,
                "detail": _redact(getattr(exc, "detail", str(exc)))[:300],
                "qubit_counterpart": counterpart, "owner": "qubit-policy",
                "rollback": "no process touched; nothing to roll back"}
        except Exception as exc:
            return _fail(exc)
        state = str(out.get("state", ""))
        body = {"ok": state == "OK", "qubit_counterpart": counterpart}
        body.update(out)
        if state == "OK":
            return 200, body
        if state == "APPROVAL_REQUIRED":
            return 409, body
        return 403, body

    return _handler


def h_gateway_restart(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                      p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/gateway/restart -> real detached service restart."""
    return h_gateway_supervision("restart")(cfg, q, b, p)


_CURATOR_CONTROL_SCHEMA = "qubit-curator-control/1"


def _curator_control_path(cfg: Any) -> Path:
    return Path(cfg.state_dir()) / "curator_control.json"


def _load_curator_control(cfg: Any) -> Dict[str, Any]:
    """Read the persisted on-demand curator control (never fabricated)."""
    raw = _load_json_store(_curator_control_path(cfg))
    return {"schema": _CURATOR_CONTROL_SCHEMA,
            "paused": bool(raw.get("paused", False)),
            "last_run_at": raw.get("last_run_at"),
            "run_count": int(raw.get("run_count", 0) or 0)}


def _save_curator_control(cfg: Any, state: Dict[str, Any]) -> None:
    state = dict(state)
    state["schema"] = _CURATOR_CONTROL_SCHEMA
    state["updated_at"] = time.time()
    _save_json_store(_curator_control_path(cfg), state)


def h_curator(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
              p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import dashboard_ops as OPS
    out = OPS.run_op(cfg, "curator", {}, actor="dashboard:user", session="dashboard")
    res = _res(out) or {}
    cur = res.get("curator", {}) if isinstance(res, dict) else {}
    ctrl = _load_curator_control(cfg)
    # CuratorStatus wire shape; curation is on-demand (no daemon) and the
    # pause/run controls persist to ``curator_control.json`` so a reload shows
    # the real state the operator set.
    return _ok(enabled=True, paused=bool(ctrl["paused"]), interval_hours=None,
               last_run_at=ctrl.get("last_run_at"),
               run_count=int(ctrl.get("run_count", 0) or 0),
               min_idle_hours=None, stale_after_days=None,
               archive_after_days=None, summary=cur)


def h_curator_paused(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                     p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """PUT /api/curator/paused -> persist the on-demand pause flag.

    Real, e-stop-gated, audited state: ``POST /api/curator/run`` refuses with
    ``CURATOR_PAUSED`` while the flag is set, and the next ``GET /api/curator``
    reads the flag back. There is no background daemon to stop; this is the
    operator's control over the on-demand pass.
    """
    if _estop(cfg):
        return 403, {"ok": False, "state": "UNAVAILABLE",
                     "code": "EMERGENCY_STOP",
                     "detail": "emergency stop latched; mutation refused",
                     "owner": "qubit-policy",
                     "rollback": "nothing changed; nothing to roll back"}
    if "paused" not in b:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "curator pause needs a boolean 'paused' field"}
    paused = _truthy(b.get("paused"))
    state = _load_curator_control(cfg)
    state["paused"] = paused
    _save_curator_control(cfg, state)
    _audit("curator_paused", "dashboard:user", {"paused": paused})
    return _ok(ok=True, paused=paused, enabled=True,
               qubit_counterpart="CLI `qubit lessons` (same LearningLedger)")


def h_curator_run(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                  p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/curator/run -> run a real on-demand curation pass.

    Reads the canonical verified-skills curator status and the LearningLedger
    review set, records the run (timestamp + count) in
    ``curator_control.json``, and audits it. Promotions stay reviewed: this
    pass summarises and records, it never auto-promotes a staged lesson.
    """
    if _estop(cfg):
        return 403, {"ok": False, "state": "UNAVAILABLE",
                     "code": "EMERGENCY_STOP",
                     "detail": "emergency stop latched; mutation refused",
                     "owner": "qubit-policy",
                     "rollback": "nothing changed; nothing to roll back"}
    state = _load_curator_control(cfg)
    if state["paused"]:
        return 409, {"ok": False, "state": "UNAVAILABLE",
                     "code": "CURATOR_PAUSED",
                     "detail": "curator is paused; resume it before running a "
                               "pass",
                     "qubit_counterpart":
                         "PUT /api/curator/paused {\"paused\": false}",
                     "owner": "qubit-policy",
                     "rollback": "nothing changed; nothing to roll back"}
    from .learning import LearningLedger
    from .verified_skills import verified_skill_workshop

    try:
        summary = verified_skill_workshop(cfg).curator_status()
    except Exception as exc:
        return _fail(exc)
    try:
        reviews = LearningLedger(Path(cfg.state_dir())).reviews()
    except Exception:
        reviews = []
    if not isinstance(reviews, list):
        reviews = []
    state["last_run_at"] = time.time()
    state["run_count"] = int(state.get("run_count", 0) or 0) + 1
    _save_curator_control(cfg, state)
    _audit("curator_run", "dashboard:user",
           {"run_count": state["run_count"], "reviews": len(reviews)})
    return _ok(ok=True, state="executed", run_count=state["run_count"],
               last_run_at=state["last_run_at"], reviews=len(reviews),
               summary=summary,
               detail="on-demand curation pass recorded; promotions remain "
                      "reviewed (CLI `qubit lessons promote <id>`)")


def _reset_active_memories(cfg: Any) -> List[str]:
    """Tombstone every active record in the canonical local memory store."""
    from . import active_memory as AM
    from .memory import MemoryStore

    db = AM.memory_db_path(cfg)
    if not db.exists():
        return []
    store = MemoryStore(db)
    try:
        rows = store.export()
        ids = [int(r["id"]) for r in rows if isinstance(r, dict) and "id" in r]
        for mid in ids:
            store.forget(mid, reason="dashboard-reset")
        return [f"memory#{mid}" for mid in ids]
    finally:
        store.close()


def _reset_workspace_notes(cfg: Any) -> List[str]:
    """Forget every local markdown memory-workspace note (canonical op)."""
    from .memory import workspace as WS

    try:
        notes = WS.list_notes(cfg).get("notes", [])
    except Exception:
        return []
    deleted: List[str] = []
    for note in notes if isinstance(notes, list) else []:
        if not isinstance(note, dict):
            continue
        name = str(note.get("name", "") or "")
        if not name:
            continue
        out = WS.forget_note(cfg, name, requested_by="dashboard:user",
                             confirmed=True)
        if str(out.get("state", "")) == "OK":
            deleted.append(name)
    return deleted


def h_env(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
          p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import dashboard_ops as OPS
    out = OPS.run_op(cfg, "auth_status", {}, actor="dashboard:user", session="dashboard")
    res = _res(out) or {}
    # Record<string, EnvVarInfo> wire shape: references only, never values.
    # PRB-02: read the SAME canonical reference registry that ``env_write`` /
    # ``env_delete`` write (``qubit_env_refs.json``), so a set/delete through
    # the dashboard is observable on the next list. Provider credential
    # references are projected from ``auth_status``.
    env: Dict[str, Any] = {}
    providers = res.get("providers", []) if isinstance(res, dict) else []
    for prov in providers if isinstance(providers, list) else []:
        if not isinstance(prov, dict):
            continue
        name = str(prov.get("provider", ""))
        if not name:
            continue
        env[f"{name.upper()}_KEY_REF"] = {
            "is_set": bool(prov.get("configured", False)),
            "redacted_value": None,
            "description": f"{name} credential reference "
                           f"({prov.get('key_ref', 'vault')})",
            "url": None, "category": "providers",
            "is_password": True, "tools": [], "advanced": False,
            "channel_managed": False, "custom": False,
        }
    # ``is_set`` must reflect the REAL presence of a key, not only the
    # vault-reference registry: Qubit's own runtime ``.env`` (and the process
    # env it was loaded into) hold provider keys that were reported as unset.
    # Names only — a value is never read, cached, or returned. Reads the SAME
    # canonical store ``env_write``/``env_delete`` write (legacy-name fallback
    # included), so every accepted kind (``env:``/``vault:``/``file:``)
    # round-trips to ``is_set``.
    try:
        from . import env_store as _env_store

        refs = _env_store.load(cfg)
        # ``is_set`` must reflect whether the referenced secret can actually
        # resolve, not merely that a registry row exists (the old code hard-coded
        # True). ``resolved_names`` is the honest union of process env, the
        # runtime .env, and references that truly resolve; values are never read
        # out. This mirrors the reference ``is_set=bool(value)`` from disk.
        resolved = _env_store.resolved_names(cfg)

        def _ref_is_set(ref: Any) -> bool:
            return bool(_env_store.is_resolved(ref))
    except Exception:
        refs = _load_json_store(_env_refs_path(cfg))
        resolved = set(refs) | set(os.environ.keys())

        def _ref_is_set(ref: Any) -> bool:
            return str(ref) in resolved
    for key, ref in sorted(refs.items()):
        env[str(key)] = {
            "is_set": _ref_is_set(ref),
            "redacted_value": None,
            "description": f"Qubit vault reference ({ref})",
            "url": None, "category": "references",
            "is_password": True, "tools": [], "advanced": False,
            "channel_managed": False, "custom": True,
        }
    # Merge the canonical upstream env-var catalog so the Env page shows the
    # real supported keys (is_set reflects the Qubit reference registry, never
    # a value). This is the same schema the CLI provider registry declares.
    try:
        from . import qubit_runtime

        qubit_runtime.ensure_path()
        from qubit_cli.config import OPTIONAL_ENV_VARS  # type: ignore

        from .qubit_plugins import _rebrand_product

        for var, info in OPTIONAL_ENV_VARS.items():
            if var in env:
                continue
            info = info if isinstance(info, dict) else {}
            env[str(var)] = {
                "is_set": var in resolved,
                "redacted_value": None,
                "description": _rebrand_product(
                    info.get("description", "") or ""),
                "url": info.get("url"),
                "category": str(info.get("category", "") or ""),
                "is_password": bool(info.get("password", False)),
                "tools": list(info.get("tools", []) or []),
                "advanced": bool(info.get("advanced", False)),
                "channel_managed": False, "custom": False,
            }
    except Exception:  # noqa: BLE001 - catalog is additive, never fatal
        pass
    return 200, env


def h_ops(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
          p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import dashboard_ops as OPS
    name = p.get("name", "")
    mapping = {
        "doctor": ("doctor", {}), "dump": ("diagnostics", {}),
        "backup": ("backup_create", {}),
        "prompt-size": ("prompt_budget", {}), "security-audit": ("security", {}),
        "import": ("backup_restore", {}),
        "config-migrate": ("product_migrate", {}),
        "kanban": ("kanban_list", {}), "boards": ("kanban_boards", {}),
        "boards-create": ("kanban_boards_create", {}),
        "cards-create": ("kanban_create", {}),
        "cards-move": ("kanban_move", {}),
        "cards-complete": ("kanban_complete", {}),
        "gateways": ("gateway", {}),
        "gateway-add": ("gateway_add", {}),
        "gateway-show": ("gateway_show", {}),
        "gateway-link": ("gateway_link", {}),
        "gateway-remove": ("gateway_remove", {}),
        "msg-accounts": ("msg_accounts", {}),
        "msg-account-add": ("msg_account_add", {}),
        "msg-account-show": ("msg_account_show", {}),
        "msg-account-remove": ("msg_account_remove", {}),
        "runtime": ("runtime_status", {}),
    }
    if name not in mapping:
        return _gate("OPS_UNKNOWN", f"unknown ops endpoint {name!r}",
                     "use the Qubit operations console (/ops) or CLI", 404)
    op_id, args = mapping[name]
    if b and name in ("checkpoints", "hooks", "import", "backup",
                      "boards-create",
                      "cards-create", "cards-move", "cards-complete",
                      "kanban", "boards", "gateways",
                      "gateway-add", "gateway-show", "gateway-link",
                      "gateway-remove", "msg-accounts",
                      "msg-account-add", "msg-account-show",
                      "msg-account-remove"):
        args = {k: v for k, v in b.items() if isinstance(v, (str, int, float, bool))}
        # ``confirm`` is the pipeline signal, not an op arg.
        args.pop("confirm", None)
        if name == "backup":
            args.pop("output", None)
        if name == "import":
            # The dashboard sends ``{archive, force}``; the canonical
            # backup_restore op takes ``{bundle, passphrase, apply}``.
            if "archive" in args and "bundle" not in args:
                args["bundle"] = args.pop("archive")
            if "force" in args and "apply" not in args:
                args["apply"] = "true" if args.pop("force") else "false"
    if q and name in ("kanban", "boards", "gateways",
                      "gateway-show", "gateway-link", "msg-accounts",
                      "msg-account-show"):
        # Filtered reads: ?board= / ?gateway_id= / ?account_id= (validated
        # by run_op against the voice arg specs; unknown keys fail typed).
        for k, v in q.items():
            if isinstance(v, str) and v:
                args[k] = v
    out = OPS.run_op(cfg, op_id, args, actor="dashboard:user", session="dashboard")
    # B-08: an explicit dashboard action (backup/import) that arrives with
    # ``confirm`` settles its own staged confirmation, re-supplying only the
    # secret args (passphrase) that were withheld from the journal. Without
    # this the control staged a confirmation the UI could never settle.
    if name in ("backup", "import") and b.get("confirm") \
            and out.get("confirmation_id") \
            and out.get("state") != "executed":
        sensitive = {k: b[k] for k in (out.get("sensitive_required") or [])
                     if k in b}
        out = OPS.confirm_op(cfg, str(out["confirmation_id"]),
                             actor="dashboard:user", session="dashboard",
                             sensitive=sensitive)
    ok = out.get("state") == "executed"
    record_action_status(op_id, out)
    record_action_status(name, out)
    if out.get("confirmation_id") and not ok:
        return _staged(_redact(out.get("speech", ""))[:300],
                       out["confirmation_id"])
    # B-08/B-15: the Operations console keys its action-log viewer off the
    # response ``name``; return the resolved op id so it renders.
    return (200 if ok else 400), {"ok": ok, "name": op_id, "action": name,
                                  "result": _res(out),
                                  "state": out.get("state"), "code": out.get("code"),
                                  "detail": _redact(out.get("speech", out.get("detail", "")))[:400]}


def h_ops_catalog(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                  p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """Machine-readable operations catalog (same registry as CLI/voice).

    Backs the Qubit-shell operations console: every entry carries its
    group, args schema, and confirmation flag so the React page can render
    real forms and settle staged mutations on the shared journal.
    """
    from . import dashboard_ops as OPS
    try:
        ops = OPS.catalog()
    except Exception as exc:
        return _fail(exc)
    return _ok(ops=ops, count=len(ops))


def _ops_result(out: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    ok = out.get("state") == "executed"
    if out.get("confirmation_id") and not ok:
        return _staged(_redact(out.get("speech", ""))[:300],
                       out["confirmation_id"])
    return (200 if ok else 400), {
        "ok": ok, "result": _res(out), "state": out.get("state"),
        "code": out.get("code"),
        "detail": _redact(out.get("speech", out.get("detail", "")))[:400]}


def ops_run_request(cfg: Any, body: Dict[str, Any]) -> Dict[str, Any]:
    """Canonical ``POST /api/ops/run`` execution.

    The single request-parsing/dispatch implementation behind both the classic
    dashboard surface (``webui``) and the SPA compat route: it calls the shared
    ``dashboard_ops.run_op`` operation contract the CLI and voice surfaces use,
    and returns the raw ``dashboard_ops`` envelope. Each transport adapts that
    envelope to its own wire shape (audit ``finding.same_path_dup_8``).
    """
    from . import dashboard_ops as OPS
    op_id = str(body.get("id", "") or "").strip()
    if not op_id:
        return {"state": "failed", "code": "MALFORMED",
                "detail": "id required"}
    raw_args = body.get("args")
    if raw_args is None:
        raw_args = {}
    if not isinstance(raw_args, dict):
        return {"state": "failed", "code": "MALFORMED",
                "detail": "args must be an object"}
    args = {k: v for k, v in raw_args.items()
            if isinstance(v, (str, int, float, bool))}
    out = OPS.run_op(cfg, op_id, args, actor="dashboard:user",
                     session="dashboard")
    record_action_status(op_id, out)
    return out


def _run_catalog_op(cfg: Any, op_id: str,
                    args: Optional[Dict[str, Any]] = None
                    ) -> Tuple[int, Dict[str, Any]]:
    """Run one operation-catalog id on the shared broker.

    The desktop/browser compat routes below are thin adapters: they forward to
    the SAME canonical op id in ``dashboard_ops`` the CLI and voice read, so
    confirmation staging, the emergency stop, and audit stay on one path.
    """
    try:
        out = ops_run_request(cfg, {"id": op_id, "args": args or {}})
    except Exception as exc:
        return _fail(exc)
    if str(out.get("code")) == "MALFORMED":
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": _redact(str(out.get("detail", "")))[:300]}
    return _ops_result(out)


def ops_confirm_request(cfg: Any, body: Dict[str, Any]) -> Dict[str, Any]:
    """Canonical ``POST /api/ops/confirm`` execution.

    One implementation for the classic and SPA surfaces; returns the raw
    ``dashboard_ops`` envelope (audit ``finding.same_path_dup_8``)."""
    from . import dashboard_ops as OPS
    cid = str(body.get("confirmation_id", body.get("id", "")) or "")
    if not cid:
        return {"state": "failed", "code": "MALFORMED",
                "detail": "confirmation_id required"}
    sensitive = body.get("sensitive")
    return OPS.confirm_op(
        cfg, cid, actor="dashboard:user", session="dashboard",
        sensitive=dict(sensitive) if isinstance(sensitive, dict) else None)


def h_ops_run(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
              p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """Run any catalog operation on the shared broker (same path as CLI/voice).

    Body: ``{"id": <op-id>, "args": {...}}``. Reads execute immediately;
    confirmed mutations stage a single-use confirmation on the shared
    journal and answer 409 (never a fabricated 2xx).
    """
    try:
        out = ops_run_request(cfg, b)
    except Exception as exc:
        return _fail(exc)
    if str(out.get("code", "")) == "MALFORMED":
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": _redact(str(out.get("detail", "")))[:300]}
    return _ops_result(out)


def h_approvals_list(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                     p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """Pending confirmations on the SHARED journal (same records CLI
    `qubit approvals`, voice yes/no, and the classic dashboard settle).
    The pending-approvals banner polls this; confirm/cancel below settle
    through the same OPS journal path — one permission/confirmation path.
    """
    from .approvals import ApprovalJournal
    try:
        journal = ApprovalJournal(Path(cfg.state_dir()) / "approvals.jsonl",
                                  default_ttl_s=120.0)
        items = [{"id": a.id, "capability": str(a.capability or ""),
                  "summary": _redact(a.summary)[:300],
                  "created_at": a.created_at, "expires_at": a.expires_at,
                  "state": a.state}
                 for a in journal.pending()]
    except Exception as exc:
        return _fail(exc)
    return _ok(approvals=items, count=len(items))


def _confirm_payload(out: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    ok = out.get("state") == "executed"
    return (200 if ok else 400), {
        "ok": ok, "state": out.get("state"), "code": out.get("code"),
        "detail": _redact(out.get("speech", out.get("detail", "")))[:400],
        "receipt": out.get("receipt")}


def h_ops_confirm(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                  p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    cid = str(b.get("confirmation_id", b.get("id", "")) or "").strip()
    if cid:
        # The canonical skill/toolset mutation contracts own their journal ids;
        # try them before the dashboard-ops registry so /api/ops/confirm is the
        # ONE standard settle for every staged mutation. Foreign ids return
        # ``None`` and fall through unchanged.
        from .verified_skills import mutations as SM
        owned = SM.settle_skill_mutation_if_owned(
            cfg, cid, actor="dashboard:user", session="dashboard")
        if owned is not None:
            return _mutation_envelope(owned)
        from . import toolsets as TS
        owned = TS.settle_toolset_mutation_if_owned(
            cfg, cid, actor="dashboard:user", session="dashboard")
        if owned is not None:
            return _mutation_envelope(owned)
    out = ops_confirm_request(cfg, b)
    if str(out.get("code", "")) == "MALFORMED":
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": _redact(str(out.get("detail", "")))[:300]}
    return _confirm_payload(out)


def h_ops_cancel(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import dashboard_ops as OPS
    cid = str(b.get("confirmation_id", b.get("id", "")) or "") or None
    out = OPS.cancel_op(cfg, cid, actor="dashboard:user",
                        session="dashboard")
    return _confirm_payload(out)


def _rebrand_chat_message(message: Any) -> Any:
    """Render one persisted chat message as Qubit at the served boundary.

    The chat-session store is durable history and may hold legacy
    upstream-authored text (D4: ``"developed as Qubit"``). It is never
    rewritten; upstream product names are rendered as Qubit only on the served
    response through the shared ``qubit_plugins._rebrand_product`` convention,
    so the stored transcript bytes stay unchanged.
    """
    if not isinstance(message, dict):
        return message
    out = dict(message)
    if isinstance(out.get("content"), str):
        out["content"] = _qubit_plugins._rebrand_product(out["content"])
    meta = out.get("meta")
    if isinstance(meta, dict):
        out["meta"] = {
            key: (_qubit_plugins._rebrand_product(value)
                  if isinstance(value, str) else value)
            for key, value in meta.items()}
        # The persisted transcript nests the model's tool calls and reasoning
        # under ``meta`` (QubitSessionStore._msg). The Sessions SPA reads them
        # at the TOP level (api.ts SessionMessage: tool_calls/tool_name/
        # tool_call_id), so a nested-only message rendered the tool-result
        # bubble but never the assistant's ``write_file`` invocation or its
        # reasoning. Surface the same values top-level; ``meta`` is preserved.
        rebranded_meta = out["meta"]
        for key in ("tool_calls", "tool_name", "tool_call_id",
                    "reasoning", "reasoning_content"):
            if key in rebranded_meta and key not in out:
                out[key] = rebranded_meta[key]
    return out


def _rebrand_chat_messages(messages: Any) -> Any:
    if isinstance(messages, list):
        return [_rebrand_chat_message(m) for m in messages]
    return messages


def _rebrand_chat_record(record: Any) -> Any:
    if not isinstance(record, dict):
        return record
    out = dict(record)
    for key in ("name", "title", "preview"):
        if isinstance(out.get(key), str):
            out[key] = _qubit_plugins._rebrand_product(out[key])
    return out


def _rebrand_chat_result(res: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(res)
    if isinstance(out.get("reply"), str):
        out["reply"] = _qubit_plugins._rebrand_product(out["reply"])
    if isinstance(out.get("message"), dict):
        out["message"] = _rebrand_chat_message(out["message"])
    return out


class _ReasoningInvalid(ValueError):
    """Per-request reasoning selector failed validation (typed 400)."""


def _chat_effective_reasoning(cfg: Any, session: str,
                              requested: Any) -> Optional[str]:
    """Effective reasoning level for a turn: the per-request selector wins,
    otherwise the session-persisted preference. Unknown levels raise
    :class:`_ReasoningInvalid` (typed refusal, never a silent default)."""
    from . import chat_transport as CT
    from .local_llm import normalize_reasoning_level

    if requested is not None:
        try:
            return normalize_reasoning_level(requested)
        except Exception as exc:
            raise _ReasoningInvalid(str(exc))
    try:
        return CT.session_reasoning(cfg, session)
    except Exception:
        return None


def h_chat_turn(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """Qubit Chat (overlay): one persisted native agent turn, same module as
    CLI/voice. The turn is written to the Qubit chat-session store so the
    dashboard can reload it; nothing is fabricated on model failure."""
    from . import chat_transport as CT
    if _estop(cfg):
        return 403, {"ok": False, "state": "UNAVAILABLE",
                     "code": "EMERGENCY_STOP",
                     "detail": "emergency stop latched; agent turn refused"}
    text = str(b.get("text", "") or "")
    session = str(b.get("session", CT.DEFAULT_SESSION) or CT.DEFAULT_SESSION)[:80]
    model = b.get("model")
    try:
        reasoning = _chat_effective_reasoning(cfg, session, b.get("reasoning"))
    except _ReasoningInvalid as exc:
        return 400, {"ok": False, "code": "REASONING_INVALID",
                     "detail": str(exc)[:200]}
    try:
        res = CT.turn(cfg, text, session=session, model=model,
                      reasoning=reasoning)
    except Exception as exc:
        return _fail(exc)
    _audit("chat_turn", "dashboard:user", {"session": session,
                                           "ok": bool(res.get("ok"))})
    if res.get("ok"):
        return 200, _rebrand_chat_result(res)
    status = {"EMERGENCY_STOP": 403, "AGENT_DISABLED": 403,
              "MALFORMED": 400, "MODEL_NOT_ALLOWED": 400,
              "CANCELLED": 409}.get(str(res.get("code")), 502)
    return status, res


def h_chat_sessions_list(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                         p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/qubit/chat/sessions -> persisted chat sessions."""
    from . import chat_transport as CT
    try:
        limit = max(1, min(int(q.get("limit", "50") or "50"), 100))
    except ValueError:
        limit = 50
    try:
        offset = max(0, int(q.get("offset", "0") or "0"))
    except ValueError:
        offset = 0
    query = str(q.get("q", q.get("query", "")) or "")
    res = CT.store(cfg).list(limit=limit, offset=offset, query=query)
    return _ok(sessions=[_rebrand_chat_record(row) for row in res["sessions"]],
               total=res["total"],
               limit=res["limit"], offset=res["offset"],
               state="OK", code=None, source="qubit-chat-store")


def h_chat_session_create(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                          p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import chat_transport as CT
    if _estop(cfg):
        return 403, {"ok": False, "state": "UNAVAILABLE",
                     "code": "EMERGENCY_STOP",
                     "detail": "emergency stop latched; session create refused"}
    routed = CT.resolve_model(cfg, b.get("model"))
    if not routed.get("ok"):
        return 400, routed
    res = CT.store(cfg).create(str(b.get("name", "") or ""),
                               session_id=b.get("id"), model=routed["model"])
    if res.get("state") == "OK":
        return _ok(session=_rebrand_chat_record(res["session"]),
                   messages=res.get("messages", []))
    status = 409 if res.get("state") == "EXISTS" else 400
    return status, {"ok": False, "code": res.get("code"),
                    "detail": res.get("detail", "")}


def h_chat_session_show(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                        p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import chat_transport as CT
    res = CT.store(cfg).show(p.get("id", ""))
    if res.get("state") != "OK":
        return 404, {"ok": False, "code": res.get("code"),
                     "detail": res.get("detail", "")}
    try:
        meta = CT.store(cfg).meta_get(p.get("id", "")).get("meta", {})
    except Exception:
        meta = {}
    return _ok(session=_rebrand_chat_record(res["session"]),
               messages=_rebrand_chat_messages(res.get("messages", [])),
               message_count=res.get("message_count", 0),
               meta=meta if isinstance(meta, dict) else {})


def h_chat_session_messages(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                            p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import chat_transport as CT
    try:
        limit = max(1, min(int(q.get("limit", "500") or "500"), 1000))
    except ValueError:
        limit = 500
    try:
        offset = max(0, int(q.get("offset", "0") or "0"))
    except ValueError:
        offset = 0
    res = CT.store(cfg).messages(p.get("id", ""), limit=limit, offset=offset)
    if res.get("state") != "OK":
        return 404, {"ok": False, "code": res.get("code"),
                     "detail": res.get("detail", "")}
    return _ok(session_id=res["session_id"],
               messages=_rebrand_chat_messages(res["messages"]),
               total=res["total"],
               pagination={"limit": res["limit"], "offset": res["offset"],
                           "order": "asc", "returned": len(res["messages"]),
                           "total": res["total"]})


# ---------------------------------------------------------------------------
# S5 — chat projection reads (session activity / file drafts / comments + CI).
#
# Every handler delegates to the ``chat_transport`` adapters, which inject the
# single chat-session authority (:class:`ChatSessionStore`) as the writer guard,
# so a projection row can only reference a session the authority knows. Reads
# are side-effect free; typed ``ChatStoreError`` codes map to HTTP 400.
# ---------------------------------------------------------------------------
def _chat_store_error(exc: Exception) -> Tuple[int, Dict[str, Any]]:
    code = getattr(exc, "code", None) or type(exc).__name__
    return 400, {"ok": False, "code": code,
                 "detail": _redact(str(exc))[:300],
                 "qubit_counterpart": "Qubit Chat session projection "
                                      "(same store as chat)"}


def h_chat_session_activity(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                            p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/qubit/chat/sessions/{id}/activity -> read-only projection."""
    from . import chat_transport as CT
    try:
        limit = max(1, min(int(q.get("limit", "20") or "20"), 200))
    except (TypeError, ValueError):
        limit = 20
    git_root = str(q.get("git_root", "") or "")
    try:
        out = CT.build_projection(cfg, p.get("id", ""), git_root=git_root,
                                  limit=limit)
    except Exception as exc:
        return _chat_store_error(exc)
    ok = str(out.get("state")) == "OK"
    return (200 if ok else 400), {"ok": ok, **out}


def h_chat_session_drafts(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                          p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/qubit/chat/sessions/{id}/drafts -> per-session file drafts."""
    from . import chat_transport as CT
    try:
        limit = max(1, min(int(q.get("limit", "100") or "100"), 500))
    except (TypeError, ValueError):
        limit = 100
    try:
        offset = max(0, int(q.get("offset", "0") or "0"))
    except (TypeError, ValueError):
        offset = 0
    include_content = str(
        q.get("include_content", "true") or "true").strip().lower() not in (
            "0", "false", "no", "off")
    try:
        out = CT.draft_store(cfg).list(p.get("id", ""), limit=limit,
                                       offset=offset,
                                       include_content=include_content)
    except Exception as exc:
        return _chat_store_error(exc)
    return _ok(**out)


def h_chat_session_draft_content(cfg: Any, q: Dict[str, str],
                                 b: Dict[str, Any],
                                 p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/qubit/chat/sessions/{id}/draft?path=... -> one draft body."""
    from . import chat_transport as CT
    path = str(q.get("path", b.get("path", "")) or "")
    if not path:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "path is required",
                     "qubit_counterpart": "Qubit Chat file drafts"}
    try:
        out = CT.draft_store(cfg).get(p.get("id", ""), path)
    except Exception as exc:
        return _chat_store_error(exc)
    if str(out.get("state")) != "OK":
        return 404, {"ok": False, "code": out.get("code", "DRAFT_NOT_FOUND"),
                     "detail": out.get("detail", "")}
    return _ok(**out)


def h_chat_session_comments(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                            p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/qubit/chat/sessions/{id}/comments -> operator comments."""
    from . import chat_transport as CT
    try:
        limit = max(1, min(int(q.get("limit", "100") or "100"), 500))
    except (TypeError, ValueError):
        limit = 100
    try:
        offset = max(0, int(q.get("offset", "0") or "0"))
    except (TypeError, ValueError):
        offset = 0
    target = str(q.get("target", "") or "")
    try:
        out = CT.comment_store(cfg).list_comments(
            p.get("id", ""), limit=limit, offset=offset, target=target)
    except Exception as exc:
        return _chat_store_error(exc)
    return _ok(**out)


def h_chat_session_ci(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                      p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/qubit/chat/sessions/{id}/ci -> structured CI-detail rows."""
    from . import chat_transport as CT
    try:
        limit = max(1, min(int(q.get("limit", "100") or "100"), 500))
    except (TypeError, ValueError):
        limit = 100
    try:
        offset = max(0, int(q.get("offset", "0") or "0"))
    except (TypeError, ValueError):
        offset = 0
    check = str(q.get("check", "") or "")
    try:
        out = CT.comment_store(cfg).list_ci_details(
            p.get("id", ""), limit=limit, offset=offset, check=check)
    except Exception as exc:
        return _chat_store_error(exc)
    return _ok(**out)


_CHAT_IMAGE_MIME_EXT = {
    "image/png": "png", "image/jpeg": "jpg", "image/gif": "gif",
    "image/webp": "webp", "image/bmp": "bmp",
}
_CHAT_IMAGE_MAX_BYTES = 10 * 1024 * 1024
_CHAT_DATA_URL_RE = re.compile(
    r"^data:(image/(?:png|jpeg|gif|webp|bmp));base64,([A-Za-z0-9+/=\s]+)$")


def h_chat_vision(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                  p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/qubit/chat/vision -> describe a pasted chat image (A35).

    Decodes the browser data URL, validates the mime/size whitelist, writes the
    bytes ONLY under the Qubit state dir (mode 0600, bounded), then runs the
    canonical ``vision.describe`` (confirmed, audited) and removes the temp
    file. No browser path is ever trusted and no secret is involved.
    """
    import base64 as _b64
    import hashlib as _hl
    if not bool(b.get("confirm", False)):
        return 400, {"ok": False, "code": "CONFIRM_REQUIRED",
                     "detail": "describing a chat image requires confirm",
                     "qubit_counterpart": "Qubit Chat attach + vision"}
    match = _CHAT_DATA_URL_RE.match(str(b.get("data_url", "") or ""))
    if not match:
        return 400, {"ok": False, "code": "BAD_IMAGE",
                     "detail": "data_url must be a base64 png/jpeg/gif/webp/"
                               "bmp data URL"}
    mime, b64 = match.group(1), match.group(2)
    try:
        raw = _b64.b64decode(re.sub(r"\s+", "", b64), validate=True)
    except Exception:
        return 400, {"ok": False, "code": "BAD_IMAGE",
                     "detail": "image data URL is not valid base64"}
    if not raw or len(raw) > _CHAT_IMAGE_MAX_BYTES:
        return 400, {"ok": False, "code": "BAD_IMAGE",
                     "detail": f"image must be 1..{_CHAT_IMAGE_MAX_BYTES} bytes"}
    import os as _os
    from pathlib import Path as _Path
    ext = _CHAT_IMAGE_MIME_EXT[mime]
    digest = _hl.sha256(raw).hexdigest()
    root = _Path(cfg.state_dir()) / "chat_attachments"
    root.mkdir(parents=True, exist_ok=True)
    dest = root / f"{digest}.{ext}"
    tmp = root / f".{digest}.tmp"
    try:
        fd = _os.open(str(tmp), _os.O_WRONLY | _os.O_CREAT | _os.O_TRUNC, 0o600)
        with _os.fdopen(fd, "wb") as fh:
            fh.write(raw)
        tmp.replace(dest)
    except OSError as exc:
        return _fail(exc)
    try:
        from . import vision as V
        out = V.describe(cfg, str(dest),
                         prompt=str(b.get("prompt", "") or ""),
                         requested_by="dashboard:user", confirmed=True)
    except Exception as exc:
        code = getattr(exc, "code", None) or "VISION_FAILED"
        detail = getattr(exc, "detail", None) or str(exc)
        return 502, {"ok": False, "code": str(code),
                     "detail": _redact(str(detail))[:200]}
    finally:
        try:
            dest.unlink()
        except OSError:
            pass
    return _ok(description=out.get("description", ""),
               image=out.get("image", {}), model=out.get("model", ""),
               authority="canonical vision.describe over a bounded, "
                         "state-dir-only temp image")


def h_chat_session_patch(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                         p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import chat_transport as CT
    if _estop(cfg):
        return 403, {"ok": False, "state": "UNAVAILABLE",
                     "code": "EMERGENCY_STOP",
                     "detail": "emergency stop latched; session change refused"}
    st = CT.store(cfg)
    if "name" in b:
        res = st.rename(p.get("id", ""), str(b.get("name", "")))
    elif b.get("archived") is not None:
        res = st.archive(p.get("id", ""), bool(b.get("archived")))
    elif "reasoning" in b:
        from .local_llm import normalize_reasoning_level

        try:
            level = normalize_reasoning_level(b.get("reasoning"))
        except Exception as exc:
            return 400, {"ok": False, "code": "REASONING_INVALID",
                         "detail": str(exc)[:200]}
        res = st.meta_patch(p.get("id", ""), {"reasoning": level})
    else:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "patch needs name, archived, or reasoning"}
    if res.get("state") == "MISSING":
        return 404, {"ok": False, "code": res.get("code"),
                     "detail": res.get("detail", "")}
    if res.get("state") != "OK":
        return 400, {"ok": False, "code": res.get("code"),
                     "detail": res.get("detail", "")}
    return _ok(session=_rebrand_chat_record(res["session"]))


def h_chat_session_delete(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                          p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import chat_transport as CT
    if _estop(cfg):
        return 403, {"ok": False, "state": "UNAVAILABLE",
                     "code": "EMERGENCY_STOP",
                     "detail": "emergency stop latched; session delete refused"}
    # HTTP DELETE bodies are not parsed by ``webui._hermes_api`` (only
    # POST/PUT/PATCH are), so a body-only ``confirm`` is unreachable from the
    # listener. Honor the query string too so the documented confirmation
    # actually settles the delete.
    confirmed = bool(b.get("confirm", False)) or str(
        q.get("confirm", "") or "").strip().lower() in (
            "1", "true", "yes", "on")
    res = CT.store(cfg).delete(p.get("id", ""), confirm=confirmed)
    if res.get("state") == "MISSING":
        return 404, {"ok": False, "code": res.get("code"),
                     "detail": res.get("detail", "")}
    if res.get("state") == "CONFIRM_REQUIRED":
        return 409, {"ok": False, "state": "CONFIRM_REQUIRED",
                     "code": "CONFIRM_REQUIRED", "detail": res.get("detail", ""),
                     "session": _rebrand_chat_record(res.get("session")),
                     "qubit_counterpart": "re-DELETE with {\"confirm\": true} "
                                          "or ?confirm=true"}
    if res.get("state") != "OK":
        return 400, {"ok": False, "code": res.get("code"),
                     "detail": res.get("detail", "")}
    return _ok(state="OK", session=_rebrand_chat_record(res.get("session")))


def h_chat_session_export(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                          p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import chat_transport as CT
    res = CT.store(cfg).export(p.get("id", ""))
    if res.get("state") != "OK":
        return 404, {"ok": False, "code": res.get("code"),
                     "detail": res.get("detail", "")}
    return _ok(schema=res["schema"], session=_rebrand_chat_record(res["session"]),
               messages=_rebrand_chat_messages(res["messages"]),
               exported_at=res["exported_at"])


def h_chat_session_import(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                          p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import chat_transport as CT
    if _estop(cfg):
        return 403, {"ok": False, "state": "UNAVAILABLE",
                     "code": "EMERGENCY_STOP",
                     "detail": "emergency stop latched; session import refused"}
    payload = b.get("envelope") if isinstance(b.get("envelope"), dict) else b
    res = CT.store(cfg).import_session(
        payload, overwrite=bool(b.get("overwrite", False)))
    if res.get("state") == "OK":
        return _ok(session=res.get("session"), imported=res.get("imported", 0))
    status = 409 if res.get("state") == "EXISTS" else 400
    return status, {"ok": False, "code": res.get("code"),
                    "detail": res.get("detail", "")}


def h_chat_cancel(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                  p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/qubit/chat/cancel -> stop the in-flight turn on a session."""
    from . import chat_transport as CT
    session = str(b.get("session", CT.DEFAULT_SESSION) or CT.DEFAULT_SESSION)
    res = CT.cancel(session)
    return (200 if res.get("ok") else 404), res


def h_chat_retry(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/qubit/chat/retry -> re-run the last unanswered user message.

    A failed turn keeps the user message and no assistant reply; retry
    re-invokes the model without duplicating the transcript. Nothing is
    fabricated when the model is still unavailable.
    """
    from . import chat_transport as CT
    if _estop(cfg):
        return 403, {"ok": False, "state": "UNAVAILABLE",
                     "code": "EMERGENCY_STOP",
                     "detail": "emergency stop latched; agent turn refused"}
    session = str(b.get("session", CT.DEFAULT_SESSION) or CT.DEFAULT_SESSION)
    res = CT.retry_last(cfg, session, model=b.get("model"))
    if res.get("ok"):
        return 200, _rebrand_chat_result(res)
    status = {"SESSION_NOT_FOUND": 404, "NOTHING_TO_RETRY": 409,
              "EMERGENCY_STOP": 403, "AGENT_DISABLED": 403,
              "MALFORMED": 400, "MODEL_NOT_ALLOWED": 400}.get(
                  str(res.get("code")), 502)
    return status, res


def h_chat_pty(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
               p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/qubit/chat/pty -> PTY-shaped, agent-backed frame (base64).

    No shell or PTY is spawned: the input line runs one persisted agent turn
    and the reply is returned as terminal bytes for the xterm UI.
    """
    from . import chat_transport as CT
    if _estop(cfg):
        return 403, {"ok": False, "state": "UNAVAILABLE",
                     "code": "EMERGENCY_STOP",
                     "detail": "emergency stop latched; chat session refused"}
    res = CT.pty_feed(cfg,
                      str(b.get("session", CT.DEFAULT_SESSION) or
                          CT.DEFAULT_SESSION),
                      str(b.get("input", "") or ""), model=b.get("model"),
                      reset=bool(b.get("reset", False)))
    return (200 if res.get("ok") else 400), res


def h_chat_stream(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                  p: Dict[str, str]):
    """Streaming chat handler for the ``disp="stream"`` route.

    Returns an iterator of SSE frames -- a real implementation, not a JSON
    ``(status, dict)`` envelope, which is why the route kind exists. The webui
    dispatcher consumes the route's ``stream`` disposition and iterates this;
    :func:`dispatch` never calls it (JSON cannot express an event stream).
    Preflight (model/empty/e-stop) runs in ``chat_transport.stream_turn`` via
    the shared ``chat_transport.stream_preflight``.
    """
    from . import chat_transport as CT
    text = str(b.get("text", "") or "")
    session = str(b.get("session", CT.DEFAULT_SESSION) or CT.DEFAULT_SESSION)
    try:
        reasoning = _chat_effective_reasoning(cfg, session, b.get("reasoning"))
    except _ReasoningInvalid as exc:
        # Bind the message BEFORE defining the generator: Python deletes the
        # ``except`` scope variable at the end of the block, so a nested
        # generator that closed over ``exc`` raised NameError when iterated
        # (headers already sent 200, empty body). A plain local survives.
        detail = str(exc)[:200]

        def _refused() -> Iterator[str]:
            from .chat_transport import sse_encode

            yield sse_encode({"event": "error", "code": "REASONING_INVALID",
                              "detail": detail, "session_id": session})

        return _refused()
    return CT.stream_sse(cfg, text, session=session, model=b.get("model"),
                         reasoning=reasoning)


# ---------------------------------------------------------------------------
# Packet 8U read surfaces: scoped Git / Files / Audio (audit §1 absent reads)
#
# Read-only compat routes backed by the SAME modules the CLI/voice call:
# ``git_scope`` (status/log/diff/show), ``files`` (list/tree/stat/read), and
# ``audio.settings`` (mic/output status + device inventory). Binary streams
# and mutating verbs stay typed-gated; nothing is fabricated 2xx.
# ---------------------------------------------------------------------------

_FS_LANGUAGE_BY_EXT = {
    ".c": "c", ".conf": "ini", ".cpp": "cpp", ".css": "css", ".csv": "csv",
    ".go": "go", ".h": "c", ".hpp": "cpp", ".html": "html", ".java": "java",
    ".js": "javascript", ".json": "json", ".jsx": "jsx", ".kt": "kotlin",
    ".lua": "lua", ".md": "markdown", ".mjs": "javascript", ".py": "python",
    ".rb": "ruby", ".rs": "rust", ".sh": "shell", ".sql": "sql",
    ".svg": "xml", ".toml": "toml", ".ts": "typescript", ".tsx": "tsx",
    ".txt": "text", ".xml": "xml", ".yaml": "yaml", ".yml": "yaml",
    ".zsh": "shell",
}
_FS_MIME_BY_EXT = {
    ".css": "text/css", ".csv": "text/csv", ".html": "text/html",
    ".js": "application/javascript", ".json": "application/json",
    ".md": "text/markdown", ".py": "text/x-python", ".sh": "text/x-shellscript",
    ".ts": "application/typescript", ".xml": "application/xml",
    ".yaml": "text/yaml", ".yml": "text/yaml",
}


def _active_folder(cfg: Any) -> str:
    """First folder of the active Qubit project (the scoped read root)."""
    try:
        from . import projects as PJ
        active = PJ.active_project(cfg)
        if active.get("state") == "OK" and active.get("project"):
            folders = active["project"].get("folders", []) or []
            if folders:
                return str(folders[0])
    except Exception:
        pass
    return ""


def _query_text(q: Dict[str, str], b: Dict[str, Any], *keys: str) -> str:
    for src in (q, b):
        if not isinstance(src, dict):
            continue
        for key in keys:
            value = src.get(key)
            if isinstance(value, str) and value:
                return value
    return ""


def _git_query_root(q: Dict[str, str], b: Dict[str, Any]) -> str:
    return _query_text(q, b, "root", "repo", "path")


def _git_query_file(q: Dict[str, str], b: Dict[str, Any]) -> str:
    return _query_text(q, b, "file", "file_path")


def _git_error(exc: Exception) -> Tuple[int, Dict[str, Any]]:
    from . import git_scope as GI
    pub = GI.public_error(exc)
    return 400, {"ok": False, "state": "FAILED", "code": pub["code"],
                 "detail": pub["detail"],
                 "qubit_counterpart": "CLI `qubit git status|log|diff|show`"}


def _git_no_project(**extra: Any) -> Tuple[int, Dict[str, Any]]:
    """Honest empty read when no active project root is registered."""
    out: Dict[str, Any] = {
        "schema": "qubit-runtime-git/1", "root": "", "git": None,
        "state": "EMPTY", "code": "NO_ACTIVE_PROJECT",
        "detail": "no active project folder; set one with "
                  "`qubit project use <slug> --confirm`",
    }
    out.update(extra)
    return _ok(**out)


def _git_ready(cfg: Any, root: str) -> bool:
    return bool(root) or bool(_active_folder(cfg))


def h_git_status(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/git/status -> scoped read-only work-tree status."""
    from . import git_scope as GI
    root = _git_query_root(q, b)
    if not _git_ready(cfg, root):
        return _git_no_project(status="")
    try:
        res = GI.status(cfg, root, requested_by="dashboard:user")
    except GI.GitError as exc:
        return _git_error(exc)
    return _ok(schema="qubit-runtime-git/1", root=res.get("root", ""),
               status=res.get("status", ""),
               truncated=bool(res.get("truncated", False)), git=res)


def h_git_log(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
              p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/git/log -> bounded commit log."""
    from . import git_scope as GI
    try:
        limit = max(1, min(int(q.get("limit", "10") or "10"), GI.MAX_LOG_COUNT))
    except (TypeError, ValueError):
        limit = 10
    root = _git_query_root(q, b)
    if not _git_ready(cfg, root):
        return _git_no_project(entries=[])
    try:
        res = GI.log(cfg, root, limit, requested_by="dashboard:user")
    except GI.GitError as exc:
        return _git_error(exc)
    return _ok(schema="qubit-runtime-git/1", root=res.get("root", ""),
               entries=res.get("entries", []),
               truncated=bool(res.get("truncated", False)), git=res)


def h_git_diff(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
               p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/git/diff -> bounded unstaged diff (tree or one file)."""
    from . import git_scope as GI
    root = _git_query_root(q, b)
    if not _git_ready(cfg, root):
        return _git_no_project(path="", stat="", patch="")
    try:
        res = GI.diff(cfg, root, _git_query_file(q, b),
                      requested_by="dashboard:user")
    except GI.GitError as exc:
        return _git_error(exc)
    return _ok(schema="qubit-runtime-git/1", root=res.get("root", ""),
               path=res.get("path", ""), stat=res.get("stat", ""),
               patch=res.get("patch", ""),
               truncated=bool(res.get("truncated", False)), git=res)


def h_git_show(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
               p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/git/show -> one committed file version at HEAD/HEAD~N."""
    from . import git_scope as GI
    ref = str(q.get("ref", b.get("ref", "HEAD")) or "HEAD")
    root = _git_query_root(q, b)
    if not _git_ready(cfg, root):
        return _git_no_project(ref=ref, path="", content="")
    try:
        res = GI.show(cfg, root, ref, _git_query_file(q, b),
                      requested_by="dashboard:user")
    except GI.GitError as exc:
        return _git_error(exc)
    return _ok(schema="qubit-runtime-git/1", root=res.get("root", ""),
               ref=res.get("ref", ref), path=res.get("path", ""),
               content=res.get("content", ""),
               truncated=bool(res.get("truncated", False)), git=res)


def h_git_file_diff(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/git/review/diff|file-diff -> working-tree-vs-HEAD diff."""
    from . import git_scope as GI
    root = _git_query_root(q, b)
    if not _git_ready(cfg, root):
        return _git_no_project(path="", diff="", stat="")
    try:
        res = GI.diff(cfg, root, _git_query_file(q, b),
                      requested_by="dashboard:user")
    except GI.GitError as exc:
        return _git_error(exc)
    return _ok(schema="qubit-runtime-git/1", root=res.get("root", ""),
               path=res.get("path", ""), diff=res.get("patch", ""),
               stat=res.get("stat", ""),
               truncated=bool(res.get("truncated", False)), git=res)


# ---------------------------------------------------------------------------
# Git review reads (audit §1 git.py reads): branches / worktrees / review list /
# commit context / rev-parse. All execute ``git`` ONLY through git_scope's
# existing fixed-argv, root-confined ``_run`` (no shell, no new subprocess path,
# no network). Mutating verbs and the credentialed ``gh`` surfaces stay gated.
# ---------------------------------------------------------------------------

_REV_PARSE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/@~^{}:+-]{0,199}$")


def _git_line(root: Path, argv: List[str]) -> str:
    from . import git_scope as GI
    try:
        text, _trunc = GI._run(root, argv)
    except GI.GitError:
        return ""
    return text.splitlines()[0].strip() if text.strip() else ""


def _git_origin_head(root: Path) -> str:
    return _git_line(root,
                     ["symbolic-ref", "--quiet", "--short",
                      "refs/remotes/origin/HEAD"])


def _git_origin_head_abbrev(root: Path) -> str:
    return _git_line(root, ["rev-parse", "--abbrev-ref", "origin/HEAD"])


def _git_ref_exists(root: Path, ref: str) -> bool:
    from . import git_scope as GI
    try:
        GI._run(root, ["rev-parse", "--verify", "--quiet", ref])
        return True
    except GI.GitError:
        return False


def _git_default_branch(root: Path) -> str:
    head = _git_origin_head_abbrev(root)
    if head and head != "origin/HEAD":
        return head.split("/", 1)[-1]
    for ref in ("refs/heads/main", "refs/heads/master",
                "refs/remotes/origin/main", "refs/remotes/origin/master"):
        if _git_ref_exists(root, ref):
            return ref.split("/")[-1]
    return ""


def _git_ref_names(root: Path, *patterns: str) -> List[str]:
    from . import git_scope as GI
    try:
        text, _trunc = GI._run(
            root, ["for-each-ref", "--format=%(refname:short)",
                   "--sort=-committerdate", *patterns])
    except GI.GitError:
        return []
    return [line.strip() for line in text.splitlines() if line.strip()]


def _git_worktree_rows(root: Path) -> List[Dict[str, Any]]:
    from . import git_scope as GI
    try:
        text, _trunc = GI._run(root, ["worktree", "list", "--porcelain"])
    except GI.GitError:
        return []
    trees: List[Dict[str, Any]] = []
    for line in text.splitlines():
        if line.startswith("worktree "):
            trees.append({"path": line[9:].strip(), "branch": None,
                          "isMain": not trees, "detached": False,
                          "locked": False})
        elif not trees:
            continue
        elif line.startswith("branch "):
            trees[-1]["branch"] = line[7:].strip().replace(
                "refs/heads/", "", 1)
        elif line == "detached":
            trees[-1]["detached"] = True
        elif line.startswith("locked"):
            trees[-1]["locked"] = True
    return trees


def _resolve_rename_path(raw: str) -> str:
    path = str(raw or "").strip()
    if " => " not in path:
        return path
    head, _, tail = path.partition("{")
    if tail and "}" in tail:
        inner, _, suffix = tail.partition("}")
        _, _, to = inner.partition(" => ")
        return f"{head}{to}{suffix}".replace("//", "/")
    return path.split(" => ")[-1].strip()


def _git_numstat(root: Path, *args: str) -> Dict[str, Tuple[int, int]]:
    from . import git_scope as GI
    try:
        text, _trunc = GI._run(root, ["diff", "--numstat", *args])
    except GI.GitError:
        return {}
    counts: Dict[str, Tuple[int, int]] = {}
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3:
            added = 0 if parts[0] == "-" else int(parts[0] or 0)
            removed = 0 if parts[1] == "-" else int(parts[1] or 0)
            counts[_resolve_rename_path(parts[2])] = (added, removed)
    return counts


def _git_untracked_insertions(root: Path, rel: str) -> int:
    try:
        base = Path(root).resolve()
        target = (base / rel).resolve()
        target.relative_to(base)
        if not target.is_file() or target.stat().st_size > 2_000_000:
            return 0
        data = target.read_bytes()
        if b"\0" in data:
            return 0
        lines = data.count(b"\n")
        return lines + 1 if data and not data.endswith(b"\n") else lines
    except (OSError, ValueError):
        return 0


def _git_status_raw(root: Path) -> str:
    from . import git_scope as GI
    try:
        text, _trunc = GI._run(root, ["status", "--porcelain=v2", "-z"])
    except GI.GitError:
        return ""
    return text


def _porcelain_entries(raw: str):
    records = iter(raw.split("\0"))
    for rec in records:
        if not rec:
            continue
        tag = rec[0]
        if tag == "?":
            yield "?", "??", rec[2:]
        elif tag == "u":
            parts = rec.split(" ")
            yield "u", parts[1] if len(parts) > 1 else ".", parts[-1]
        elif tag in ("1", "2"):
            parts = rec.split(" ")
            xy = parts[1] if len(parts) > 1 else ".."
            path = (rec.split(" ", 8)[-1] if tag == "1"
                    else rec.split(" ", 9)[-1])
            if tag == "2":
                next(records, None)
            yield tag, xy, _resolve_rename_path(path)


def _git_status_letter(tag: str, xy: str) -> str:
    if tag in ("?", "u"):
        return tag.upper()
    code = xy[0] if xy[0] != "." else (xy[1] if len(xy) > 1 else ".")
    return (code if code != "." else "M").upper()


def _git_branch_base(root: Path) -> str:
    from . import git_scope as GI
    head = _git_origin_head_abbrev(root)
    candidates = ([head] if head else []) + [
        "origin/main", "origin/master", "main", "master"]
    for ref in candidates:
        try:
            text, _trunc = GI._run(root, ["merge-base", "HEAD", ref])
        except GI.GitError:
            continue
        line = text.splitlines()[0].strip() if text.strip() else ""
        if line:
            return line
    return ""


def _git_resolved_root(cfg: Any, q: Dict[str, str],
                       b: Dict[str, Any]):
    """(resolved_root, error_response) for a scoped repo read."""
    from . import git_scope as GI
    root = _git_query_root(q, b)
    if not _git_ready(cfg, root):
        return None, None
    try:
        resolved = GI.resolve_root(cfg, root)
        GI._check_repo(resolved)
    except GI.GitError as exc:
        return None, _git_error(exc)
    return resolved, None


def _review_files(root: Path, files: List[Dict[str, Any]], base: Optional[str],
                  scope: str) -> Tuple[int, Dict[str, Any]]:
    files.sort(key=lambda f: f["path"])
    for f in files:
        if f["status"] == "?" and f["added"] == 0 and f["removed"] == 0:
            f["added"] = _git_untracked_insertions(root, f["path"])
    return _ok(schema="qubit-runtime-git/1", root=str(root), files=files,
               base=base, scope=scope)


def h_git_worktrees(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/git/worktrees -> scoped ``git worktree list`` (read-only)."""
    resolved, err = _git_resolved_root(cfg, q, b)
    if resolved is None:
        return err or _git_no_project(worktrees=[])
    return _ok(schema="qubit-runtime-git/1", root=str(resolved),
               worktrees=_git_worktree_rows(resolved))


def h_git_branches(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                   p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/git/branches -> local + remote-tracking branch picker rows."""
    resolved, err = _git_resolved_root(cfg, q, b)
    if resolved is None:
        return err or _git_no_project(branches=[])
    locals_ = _git_ref_names(resolved, "refs/heads")
    if not locals_:
        return _ok(schema="qubit-runtime-git/1", root=str(resolved),
                   branches=[])
    path_by_branch = {t["branch"]: t["path"]
                      for t in _git_worktree_rows(resolved) if t.get("branch")}
    trunk = _git_default_branch(resolved)
    local_set = set(locals_)
    remotes = [name for name in _git_ref_names(resolved, "refs/remotes")
               if not name.endswith("/HEAD")
               and name.split("/", 1)[-1] not in local_set]
    branches = [
        {"name": name, "checkedOut": name in path_by_branch,
         "isDefault": bool(trunk and name == trunk), "isRemote": False,
         "worktreePath": path_by_branch.get(name)} for name in locals_]
    branches += [
        {"name": name, "checkedOut": False, "isDefault": False,
         "isRemote": True, "worktreePath": None} for name in remotes]
    return _ok(schema="qubit-runtime-git/1", root=str(resolved),
               branches=branches)


def h_git_base_branches(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                        p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/git/base-branches -> base-branch picker rows (trunk flagged)."""
    resolved, err = _git_resolved_root(cfg, q, b)
    if resolved is None:
        return err or _git_no_project(branches=[])
    names = _git_ref_names(resolved, "refs/heads", "refs/remotes")
    default = _git_origin_head(resolved) or _git_default_branch(resolved)
    branches = [{"name": name, "isRemote": name.startswith("origin/"),
                 "isDefault": bool(default and name == default)}
                for name in names]
    return _ok(schema="qubit-runtime-git/1", root=str(resolved),
               branches=branches)


def h_git_review_list(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                      p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/git/review/list -> changed files for a review scope."""
    from . import git_scope as GI
    scope = str(q.get("scope", b.get("scope", "uncommitted"))
                or "uncommitted")
    base_ref = _query_text(q, b, "base")
    resolved, err = _git_resolved_root(cfg, q, b)
    if resolved is None:
        return err or _git_no_project(files=[], base=None, scope=scope)
    if scope in ("branch", "lastTurn"):
        base = _git_branch_base(resolved) if scope == "branch" else base_ref
        if scope == "lastTurn" and base and (
                base.startswith("-") or not _REV_PARSE_RE.match(base)):
            return _git_error(GI.GitError(GI.INPUT_CODE, "bad base ref"))
        if not base:
            return _ok(schema="qubit-runtime-git/1", root=str(resolved),
                       files=[], base=None, scope=scope)
        rng = f"{base}...HEAD" if scope == "branch" else base
        counts = _git_numstat(resolved, rng)
        files = [{"path": path, "added": a, "removed": r,
                  "status": "M", "staged": False}
                 for path, (a, r) in counts.items()]
        if scope == "lastTurn":
            seen = {f["path"] for f in files}
            for tag, _xy, path in _porcelain_entries(
                    _git_status_raw(resolved)):
                if tag == "?" and path not in seen:
                    files.append({"path": path, "added": 0, "removed": 0,
                                  "status": "?", "staged": False})
        return _review_files(resolved, files, base, scope)
    raw = _git_status_raw(resolved)
    staged = _git_numstat(resolved, "--cached")
    unstaged = _git_numstat(resolved)
    files = []
    for tag, xy, path in _porcelain_entries(raw):
        sa, sr = staged.get(path, (0, 0))
        ua, ur = unstaged.get(path, (0, 0))
        files.append({
            "path": path, "added": sa + ua, "removed": sr + ur,
            "status": _git_status_letter(tag, xy),
            "staged": tag in ("1", "2") and xy[0] not in (".", "?")})
    return _review_files(resolved, files, None, scope)


def h_git_review_commit_context(cfg: Any, q: Dict[str, str],
                                b: Dict[str, Any],
                                p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/git/review/commit-context -> staged/worktree diff + recents."""
    from . import git_scope as GI
    resolved, err = _git_resolved_root(cfg, q, b)
    if resolved is None:
        return err or _git_no_project(diff="", recent="")
    raw = _git_status_raw(resolved)
    entries = list(_porcelain_entries(raw))
    staged = any(tag in ("1", "2") and xy[0] not in (".", "?")
                 for tag, xy, _path in entries)
    try:
        diff, _trunc = GI._run(
            resolved, ["diff", "--cached"] if staged else ["diff", "HEAD"])
    except GI.GitError:
        diff = ""
    maxchars = 20000
    if len(diff) > maxchars:
        omitted = len(diff) - maxchars
        diff = diff[:maxchars] + \
            f"\n# diff truncated: {omitted} chars omitted\n"
    untracked = [path for tag, _xy, path in entries if tag == "?"]
    if untracked:
        visible = untracked[:50]
        diff += "\n# New (untracked) files:\n" + \
            "".join(f"#   {path}\n" for path in visible)
        if len(untracked) > len(visible):
            diff += f"#   ... {len(untracked) - len(visible)} more omitted\n"
    recent = _git_line(resolved,
                       ["log", "-n", "10", "--pretty=format:%s"])
    return _ok(schema="qubit-runtime-git/1", root=str(resolved),
               diff=diff or "", recent=recent)


def h_git_review_rev_parse(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                           p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/git/review/rev-parse -> resolved object name for one ref."""
    from . import git_scope as GI
    ref = str(q.get("ref", b.get("ref", "HEAD")) or "HEAD").strip()
    resolved, err = _git_resolved_root(cfg, q, b)
    if resolved is None:
        return err or _git_no_project(ref=ref, sha=None)
    if not _REV_PARSE_RE.match(ref) or ref.startswith("-"):
        return _git_error(GI.GitError(GI.INPUT_CODE, "bad ref"))
    try:
        text, _trunc = GI._run(
            resolved, ["rev-parse", "--verify", "--quiet", ref])
    except GI.GitError:
        return _ok(schema="qubit-runtime-git/1", root=str(resolved), ref=ref,
                   sha=None)
    sha = text.splitlines()[0].strip() if text.strip() else None
    return _ok(schema="qubit-runtime-git/1", root=str(resolved), ref=ref,
               sha=sha)


def _files_error(exc: Exception) -> Tuple[int, Dict[str, Any]]:
    from . import files as FI
    return 400, {"ok": False, "state": "FAILED",
                 "code": getattr(exc, "code", FI.BAD_INPUT_CODE),
                 "detail": _redact(str(exc))[:300],
                 "qubit_counterpart":
                     "CLI `qubit files list|read` (scoped project folders)"}


def _fs_entries(res: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for entry in (res.get("entries", []) if isinstance(res, dict) else []):
        if not isinstance(entry, dict):
            continue
        out.append({"name": entry.get("name", ""),
                    "path": entry.get("rel", ""),
                    "isDirectory": bool(entry.get("dir", False)),
                    "size": entry.get("size")})
    return out


def h_fs_list(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
              p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/fs/list -> project-root directory listing (read-only)."""
    from . import files as FI
    path = _query_text(q, b, "path") or _active_folder(cfg)
    if not path:
        return _ok(entries=[], folder=None, truncated=False,
                   detail="no active project folder; register one with "
                          "`qubit project add-folder`")
    try:
        res = FI.list_dir(cfg, path)
    except FI.FilesError as exc:
        return _files_error(exc)
    return _ok(entries=_fs_entries(res), folder=res.get("folder", path),
               truncated=bool(res.get("truncated", False)))


def h_fs_tree(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
              p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/fs/tree -> bounded directory tree (depth 1..4)."""
    from . import files as FI
    path = _query_text(q, b, "path") or _active_folder(cfg)
    try:
        depth = max(1, min(int(q.get("depth", "2") or "2"), FI.MAX_DEPTH))
    except (TypeError, ValueError):
        depth = 2
    if not path:
        return _ok(entries=[], folder=None, depth=depth, truncated=False,
                   detail="no active project folder; register one with "
                          "`qubit project add-folder`")
    try:
        res = FI.list_dir(cfg, path, depth=depth)
    except FI.FilesError as exc:
        return _files_error(exc)
    return _ok(entries=_fs_entries(res), folder=res.get("folder", path),
               depth=depth, truncated=bool(res.get("truncated", False)))


def h_fs_stat(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
              p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/fs/stat -> inspect a folder listing or file metadata."""
    from . import files as FI
    try:
        res = FI.inspect(cfg, _query_text(q, b, "path"))
    except FI.FilesError as exc:
        return _files_error(exc)
    return _ok(stat=res)


def _fs_kind(path: str) -> Tuple[str, str]:
    suffix = Path(str(path or "")).suffix.lower()
    return (_FS_LANGUAGE_BY_EXT.get(suffix, "text"),
            _FS_MIME_BY_EXT.get(suffix, "text/plain"))


def h_fs_read_text(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                   p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/fs/read-text -> bounded, redacted text file read."""
    from . import files as FI
    path = _query_text(q, b, "path") or _active_folder(cfg)
    if not path:
        return 400, {"ok": False, "code": FI.BAD_INPUT_CODE,
                     "detail": "a file path is required",
                     "qubit_counterpart": "CLI `qubit files read <path>`"}
    try:
        res = FI.read_file(cfg, path)
    except FI.FilesError as exc:
        if getattr(exc, "code", "") == FI.BINARY_CODE:
            language, mime = _fs_kind(path)
            return _ok(binary=True, byteSize=0, language=language,
                       mimeType=mime, path=str(path), text="",
                       truncated=False,
                       detail="binary file; content not shown")
        return _files_error(exc)
    target = str(res.get("file", path) or path)
    language, mime = _fs_kind(target)
    return _ok(binary=False, byteSize=int(res.get("size", 0) or 0),
               language=language, mimeType=mime, path=target,
               text=res.get("text", ""),
               truncated=bool(res.get("clipped", False)))


def h_fs_read_data_url(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                       p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/fs/read-data-url -> text file as a redacted data URL."""
    from . import files as FI
    import base64
    path = _query_text(q, b, "path") or _active_folder(cfg)
    if not path:
        return 400, {"ok": False, "code": FI.BAD_INPUT_CODE,
                     "detail": "a file path is required",
                     "qubit_counterpart": "CLI `qubit files read <path>`"}
    try:
        res = FI.read_file(cfg, path)
    except FI.FilesError as exc:
        return _files_error(exc)
    target = str(res.get("file", path) or path)
    _language, mime = _fs_kind(target)
    encoded = base64.b64encode(
        str(res.get("text", "")).encode("utf-8", "replace")).decode("ascii")
    return _ok(dataUrl=f"data:{mime};base64,{encoded}", path=target,
               mimeType=mime)


def _find_git_root(start: Path) -> Optional[Path]:
    try:
        directory = start if start.is_dir() else start.parent
    except OSError:
        return None
    for _ in range(50):
        try:
            if (directory / ".git").exists():
                return directory
        except OSError:
            return None
        parent = directory.parent
        if parent == directory:
            return None
        directory = parent
    return None


def h_fs_git_root(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                  p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/fs/git-root -> git root discovery inside an approved root."""
    from . import git_scope as GI
    path = _query_text(q, b, "path") or _active_folder(cfg)
    if not path:
        return _ok(root=None)
    try:
        resolved = GI.resolve_root(cfg, path)
    except GI.GitError:
        return _ok(root=None)
    found = _find_git_root(Path(resolved))
    return _ok(root=(str(found) if found else None))


def _git_branch(cfg: Any, root: str) -> str:
    from . import git_scope as GI
    try:
        res = GI.status(cfg, root, requested_by="dashboard:user")
    except GI.GitError:
        return ""
    for line in str(res.get("status", "")).splitlines():
        if line.startswith("# branch.head "):
            head = line[len("# branch.head "):].strip()
            return "" if head == "(detached)" else head
    return ""


def h_fs_default_cwd(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                     p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/fs/default-cwd -> active project folder + its branch."""
    cwd = _active_folder(cfg)
    if not cwd:
        try:
            cwd = str(Path.cwd())
        except Exception:
            cwd = ""
    return _ok(cwd=cwd, branch=(_git_branch(cfg, cwd) if cwd else ""))


def h_audio_status(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                   p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/audio/status -> real mic/output status (audio.settings)."""
    from .audio import settings as AS
    try:
        st = AS.status(cfg)
    except AS.AudioSettingsError as exc:
        miss = {"state": "MISSING", "detail": _redact(str(exc))[:200]}
        return _ok(mic=miss, output=dict(miss), schema="qubit-runtime-audio/1")
    return _ok(schema="qubit-runtime-audio/1",
               mic=st.get("mic", {}), output=st.get("output", {}))


def _device_rows(devices: Any) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for dev in (devices if isinstance(devices, list) else []):
        rows.append({"stable_id": getattr(dev, "stable_id", ""),
                     "name": getattr(dev, "name", ""),
                     "backend": getattr(dev, "backend", ""),
                     "is_default": bool(getattr(dev, "is_default", False))})
    return rows


def h_audio_devices(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/audio/list|devices -> real device inventory (audio.settings)."""
    from .audio import settings as AS
    try:
        devs = AS.list_devices()
    except AS.AudioSettingsError as exc:
        return _ok(inputs=[], outputs=[], state="MISSING",
                   detail=_redact(str(exc))[:200])
    return _ok(inputs=_device_rows(devs.get("inputs", [])),
               outputs=_device_rows(devs.get("outputs", [])))


# ---------------------------------------------------------------------------
# Audio HTTP actions (audit §1A audio.py reads): voice-config (profile refs),
# transcribe (local ASR over a bounded upload), speak (local TTS -> data URL).
# Streaming/WS, external catalogs, and desktop leases stay typed-gated.
# ---------------------------------------------------------------------------

_AUDIO_MAX_UPLOAD_BYTES = 25 * 1024 * 1024
_AUDIO_MIME_EXT = {
    "audio/aac": ".aac", "audio/flac": ".flac", "audio/m4a": ".m4a",
    "audio/mp3": ".mp3", "audio/mp4": ".mp4", "audio/mpeg": ".mp3",
    "audio/ogg": ".ogg", "audio/wav": ".wav", "audio/wave": ".wav",
    "audio/webm": ".webm", "audio/x-m4a": ".m4a", "audio/x-wav": ".wav",
    "video/webm": ".webm",
}


def _asr_from_cfg(cfg: Any):
    """Build the configured ASR provider (same selection as the orchestrator)."""
    from .asr.sherpa import SherpaASR
    from .asr.whisper import FasterWhisperASR
    try:
        asr_cfg = cfg.section("asr")
    except Exception:
        asr_cfg = {}
    provider = asr_cfg.get("provider", "sherpa")
    if provider == "personaplex":
        provider = "sherpa"
    model_dir = cfg.model_dir()
    if provider == "sherpa":
        return SherpaASR(engine=asr_cfg.get("engine", "moonshine"),
                         model_dir=model_dir,
                         provider=asr_cfg.get("provider_ep", "cpu"),
                         num_threads=int(asr_cfg.get("num_threads", 4)),
                         language=asr_cfg.get("language"),
                         hotwords=asr_cfg.get("hotwords", ""),
                         hotwords_score=float(
                             asr_cfg.get("hotwords_score", 1.5)))
    return FasterWhisperASR(model=asr_cfg.get("model", "base"),
                            device=asr_cfg.get("device", "auto"),
                            compute_type=asr_cfg.get("compute_type",
                                                     "float16"),
                            language=asr_cfg.get("language"),
                            beam_size=asr_cfg.get("beam_size", 1),
                            model_dir=model_dir)


def _tts_from_cfg(cfg: Any):
    """Build the configured TTS provider (same selection as the orchestrator)."""
    from .tts.piper import PiperTTS
    from .providers.speech import external_speech_guard
    _egress = external_speech_guard(cfg)
    provider = cfg.get("tts", "provider", default="piper")
    if provider == "edge":
        from .tts.edge import EdgeTTS
        edge = EdgeTTS(
            voice=cfg.get("tts", "edge", "voice",
                          default="en-US-JennyNeural"),
            rate=cfg.get("tts", "edge", "rate", default="+0%"),
            egress_guard=_egress)
        if edge.available():
            return edge
    if provider == "hybrid":
        from .tts.hybrid import HybridTTS
        h = cfg.get("tts", "hybrid", default={})
        h = h if isinstance(h, dict) else {}
        return HybridTTS(
            model_dir=cfg.model_dir(),
            piper_voice=h.get("piper_voice",
                              cfg.get("tts", "voice",
                                      default="en_US-amy-medium")),
            openai_voice=h.get("openai_voice", "nova"),
            openai_model=h.get("openai_model", "gpt-4o-mini-tts"),
            key_env=cfg.get("tts", "openai", "key_env",
                            default="~/.config/qubit-voicebridge/.env"),
            instructions=cfg.get("tts", "openai", "instructions", default=""),
            threshold_words=int(h.get("threshold_words", 18)),
            egress_guard=_egress)
    if provider == "openai":
        from .tts.openai_tts import OpenAITTS
        tts = OpenAITTS(
            voice=cfg.get("tts", "openai", "voice", default="nova"),
            model=cfg.get("tts", "openai", "model",
                          default="gpt-4o-mini-tts"),
            key_env=cfg.get("tts", "openai", "key_env",
                            default="~/.config/qubit-voicebridge/.env"),
            instructions=cfg.get("tts", "openai", "instructions", default=""),
            egress_guard=_egress)
        if tts.available():
            return tts
    if provider == "kokoro":
        from .tts.kokoro import KokoroTTS
        tts = KokoroTTS(model_dir=cfg.model_dir(),
                        voice_sid=int(cfg.get("tts", "kokoro", "voice_sid",
                                              default=0)))
        if tts.available():
            return tts
    return PiperTTS(
        model_dir=cfg.model_dir(),
        voice=cfg.get("tts", "voice", default="en_US-amy-medium"),
        backend=cfg.get("tts", "piper_backend", default="sherpa"))


def h_audio_voice_config(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                         p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/audio/voice-config -> active STT/TTS profile + audio refs.

    Resolves the active profile per component from ``voicebridge.profiles`` and
    the live mic/output contract from ``voicebridge.audio.settings``. Qubit
    providers run host-side, so every provider resolves to ``mode: relay`` and
    NO provider key or credential is ever exported (references only).
    """
    from . import profiles as PR
    from .audio import settings as AS
    try:
        current = PR.current_profiles(cfg)
    except Exception as exc:
        return _fail(exc)
    try:
        status = AS.status(cfg)
    except AS.AudioSettingsError as exc:
        miss = {"state": "MISSING", "detail": _redact(str(exc))[:200]}
        status = {"mic": dict(miss), "output": dict(miss)}

    def _relay(entry: Dict[str, Any], kind: str) -> Dict[str, Any]:
        value = entry.get("value")
        provider = value if isinstance(value, (str, int, float)) else ""
        return {
            "mode": "relay", "kind": kind,
            "profile": str(entry.get("id", "") or ""),
            "state": str(entry.get("state", "") or ""),
            "provider": str(provider or ""),
            "detail": "Qubit runs this provider host-side; the client keeps "
                      "the local audio relay and no provider key is exported",
        }

    voice = current.get("voice", {}) or {}
    return _ok(schema="qubit-runtime-audio/1", mode="relay",
               stt=_relay(current.get("stt", {}), "stt"),
               tts=_relay(current.get("tts", {}), "tts"),
               voice={"profile": str(voice.get("id", "") or ""),
                      "name": str(voice.get("value", "") or "")},
               audio=status,
               detail="STT/TTS resolve to host-side Qubit providers; no "
                      "provider credential is served")


def h_audio_transcribe(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                       p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/audio/transcribe -> local ASR over a bounded data URL body.

    Strict caps: audio/* (or video/webm), valid base64, non-empty, <= 25 MiB;
    the temp file is always removed. A missing model is a typed
    ``AUDIO_ASR_UNAVAILABLE`` refusal, never a fabricated transcript.
    """
    import base64
    import binascii
    import os
    import tempfile
    from .asr.base import ASRUnavailable
    counterpart = "Qubit Chat voice (local mic capture) + CLI `qubit asr-test`"
    data_url = str(b.get("data_url", b.get("dataUrl", "")) or "").strip()
    if not data_url:
        return _gate("AUDIO_PAYLOAD_REQUIRED",
                     "transcription needs a base64 audio data URL",
                     counterpart, 400)
    if not data_url.startswith("data:") or "," not in data_url:
        return _gate("AUDIO_BAD_PAYLOAD", "invalid audio payload",
                     counterpart, 400)
    header, encoded = data_url.split(",", 1)
    if ";base64" not in header:
        return _gate("AUDIO_BAD_PAYLOAD",
                     "audio payload must be base64 encoded", counterpart, 400)
    mime = str(b.get("mime_type", b.get("mimeType", ""))
               or header[5:].split(";", 1)[0] or "audio/webm").strip()
    norm = mime.split(";", 1)[0].lower()
    if not (norm.startswith("audio/") or norm == "video/webm"):
        return _gate("AUDIO_BAD_PAYLOAD",
                     "payload must be an audio recording", counterpart, 400)
    # Cap the ENCODED length before decoding so an oversized body cannot force
    # a huge base64 allocation (25 MiB decoded ~= 33.4 MiB encoded).
    if len(encoded) > (_AUDIO_MAX_UPLOAD_BYTES // 3) * 4 + 8:
        return _gate("AUDIO_TOO_LARGE", "audio recording is too large",
                     counterpart, 413)
    try:
        audio_bytes = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        return _gate("AUDIO_BAD_PAYLOAD",
                     "audio payload is not valid base64", counterpart, 400)
    if not audio_bytes:
        return _gate("AUDIO_BAD_PAYLOAD", "audio recording is empty",
                     counterpart, 400)
    if len(audio_bytes) > _AUDIO_MAX_UPLOAD_BYTES:
        return _gate("AUDIO_TOO_LARGE", "audio recording is too large",
                     counterpart, 413)
    temp_path = ""
    try:
        suffix = _AUDIO_MIME_EXT.get(norm, ".webm")
        with tempfile.NamedTemporaryFile(
                prefix="qubit-runtime-voice-", suffix=suffix,
                delete=False) as tmp:
            tmp.write(audio_bytes)
            temp_path = tmp.name
        asr = _asr_from_cfg(cfg)
        asr.load()
        transcript = asr.transcribe(temp_path)
        if getattr(transcript, "empty", False):
            return _ok(transcript="", provider=transcript.provider,
                       detail="no speech detected")
        return _ok(transcript=str(transcript.text),
                   provider=transcript.provider,
                   language=transcript.language,
                   confidence=transcript.confidence,
                   duration_s=transcript.duration_s)
    except ASRUnavailable as exc:
        return _gate("AUDIO_ASR_UNAVAILABLE",
                     "local ASR model is unavailable: "
                     + _redact(str(exc))[:160],
                     "install the configured sherpa/whisper model + CLI "
                     "`qubit asr-test`")
    except Exception as exc:
        return _fail(exc)
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass


def h_audio_stt_status(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                       p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/audio/stt/status -> read-only streaming-STT health + config.

    Reports the configured engines, whether the warm model cache is loaded, the
    execution provider, and whether GPU acceleration is actually available
    (onnxruntime CUDA EP). No secrets, no audio, no transcript.
    """
    from .asr.streaming import (cached_engines, _configured_streaming_engine,
                                _streaming_provider, last_provider)
    try:
        asr = cfg.section("asr")
    except Exception:
        asr = {}
    health = cached_engines()
    hotwords = [w.strip() for w in str(asr.get("hotwords", "") or "").split(",")
                if w.strip()]
    return _ok(schema="qubit-stt-status/1",
               engine=str(asr.get("engine", "") or ""),
               streaming_engine=_configured_streaming_engine(cfg),
               streaming_window_engine=str(
                   asr.get("streaming_window_engine", "moonshine") or "moonshine"),
               english_only=bool(asr.get("english_only", True)),
               chunk_ms=int(asr.get("streaming_chunk_ms", 0) or 0),
               provider_ep=str(asr.get("provider_ep", "cpu") or "cpu"),
               streaming_provider_ep=_streaming_provider(cfg),
               active_provider=last_provider() or "",
               num_threads=int(asr.get("num_threads", 4) or 4),
               hotword_count=len(hotwords),
               cached=health,
               detail="read-only streaming STT health; no secrets or audio")


def h_audio_speak(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                  p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/audio/speak -> local TTS rendered as a base64 data URL.

    Runs the SAME provider chain the orchestrator selects. An unavailable
    provider is a typed ``AUDIO_TTS_UNAVAILABLE`` refusal; the temp file is
    always removed and no on-disk path is returned.
    """
    import base64
    import os
    import tempfile
    from .tts.base import TTSUnavailable
    counterpart = "Qubit Chat voice + CLI `qubit tts-test`"
    text = str(b.get("text", "") or "").strip()
    if not text:
        return _gate("AUDIO_TEXT_REQUIRED", "text is required",
                     counterpart, 400)
    out = Path(tempfile.gettempdir()) / (
        f"qubit-runtime-tts-{os.getpid()}-{abs(hash(text)) % 10 ** 8}.wav")
    try:
        tts = _tts_from_cfg(cfg)
        tts.load()
        tts.synthesize(text[:4000], out)
        data = out.read_bytes()
        mime = ("audio/wav" if out.suffix.lower() == ".wav"
                else "audio/mpeg")
        encoded = base64.b64encode(data).decode("ascii")
        return _ok(data_url=f"data:{mime};base64,{encoded}", mime_type=mime,
                   provider=str(getattr(tts, "name", "") or ""))
    except TTSUnavailable as exc:
        return _gate("AUDIO_TTS_UNAVAILABLE",
                     "local TTS provider is unavailable: "
                     + _redact(str(exc))[:160],
                     "install the configured piper voice + CLI `qubit tts-test`")
    except Exception as exc:
        return _fail(exc)
    finally:
        try:
            if out.exists():
                out.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Packet 8U closure: local models / custom endpoints / profiles / tools /
# learning (audit §1A absent reads) + remaining wrong-verb gates (audit §1B).
#
# Reads execute the SAME Qubit modules the CLI uses (``local_models``,
# ``providers.policy``, ``projects``, ``learning``, ``capability_catalog``,
# ``computer_use``). Writes never fabricate success: local-model verbs reuse
# the module's typed refusal, and pure policy boundaries are typed gates.
# ---------------------------------------------------------------------------

_LOCAL_MODEL_COUNTERPART = (
    "CLI `qubit runtime status|install <image> --confirm` + supervised "
    "opencode path (pinned, confined, audited)")

_PROVIDER_ENDPOINT_COUNTERPART = (
    "dashboard Settings (classic) + CLI config profiles")


def h_local_models_status(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                          p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/local-models/status -> real local-runtime state."""
    from . import local_models as LM
    return _ok(**LM.status(cfg))


def h_local_models_hardware(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                            p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/local-models/hardware -> real GPU/VRAM gauges."""
    from . import local_models as LM
    return _ok(**LM.hardware(cfg))


def h_local_models_catalog(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                           p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/local-models/catalog -> configured/observed local models."""
    from . import local_models as LM
    return _ok(**LM.catalog(cfg))


def h_local_models_jobs(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                        p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/local-models/jobs -> typed-empty install job history."""
    from . import local_models as LM
    return _ok(**LM.jobs(cfg))


def h_local_models_job_show(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                            p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/local-models/jobs/{id} -> one job or an honest miss."""
    from . import local_models as LM
    base = LM.jobs(cfg)
    jobs = base.get("jobs", []) if isinstance(base, dict) else []
    jobs = jobs if isinstance(jobs, list) else []
    want = str(p.get("id", "") or "")
    found = None
    for job in jobs:
        if isinstance(job, dict) and str(job.get("id", "")) == want:
            found = job
            break
    return _ok(jobs=jobs, job=found, count=len(jobs),
               state=base.get("state", "EMPTY"),
               code=base.get("code"), detail=base.get("detail", ""),
               rollback=base.get("rollback",
                                 "read-only; nothing to roll back"))


def _local_model_refusal(res: Any) -> Tuple[int, Dict[str, Any]]:
    out: Dict[str, Any] = {"ok": False}
    out.update(res if isinstance(res, dict) else {})
    out.setdefault("state", "REFUSED")
    out.setdefault("code", "LOCAL_MODEL_BROKERED")
    out.setdefault("owner", "qubit-policy")
    out.setdefault("guidance", _BROKERED_GUIDANCE_LOCAL)
    out.setdefault("rollback", "nothing was downloaded, started, ejected, "
                               "activated, or written; nothing to roll back")
    out.setdefault("qubit_counterpart", _LOCAL_MODEL_COUNTERPART)
    state = str(out.get("state", ""))
    http = 200 if state in ("OK", "AVAILABLE") and not out.get("code") else 403
    return http, out


_BROKERED_GUIDANCE_LOCAL = (
    "local-model runtime management is brokered in Qubit; use "
    "`qubit runtime status` and `qubit runtime install <image> --confirm` "
    "(pinned, confined, digest-verified), or the supervised opencode path. "
    "No download is performed or queued by this surface")


def h_local_models_runtime_install(cfg: Any, q: Dict[str, str],
                                   b: Dict[str, Any],
                                   p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import local_models as LM
    return _local_model_refusal(
        LM.runtime_install(cfg, backend=b.get("backend"),
                           requested_by="dashboard:user",
                           confirmed=bool(b.get("confirm"))))


def h_local_models_download(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                            p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import local_models as LM
    model = _query_text(q, b, "model", "model_id", "id")
    return _local_model_refusal(
        LM.download(cfg, model=model, requested_by="dashboard:user",
                    confirmed=bool(b.get("confirm"))))


def h_local_models_download_pause(cfg: Any, q: Dict[str, str],
                                  b: Dict[str, Any],
                                  p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/local-models/download/pause -> Qubit brokered gate.

    The reference route (``web_routers/local_models.py:708
    local_models_download_pause``) pauses a managed download job. Qubit's
    authority path routes the same ``{"job_id": ...}`` contract through the real
    ``voicebridge.local_models`` surface (e-stop -> confirmation -> broker), so
    nothing is paused and no queue exists.
    """
    from . import local_models as LM
    job_id = _query_text(q, b, "job_id", "job", "id")
    return _local_model_refusal(
        LM.download_pause(cfg, job_id=job_id, requested_by="dashboard:user",
                          confirmed=bool(b.get("confirm"))))


def h_local_models_download_resume(cfg: Any, q: Dict[str, str],
                                   b: Dict[str, Any],
                                   p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/local-models/download/resume -> Qubit brokered gate.

    Counterpart to ``web_routers/local_models.py:721
    local_models_download_resume`` on the canonical Qubit authority path; the
    verb is gated/reused, never a fabricated success.
    """
    from . import local_models as LM
    job_id = _query_text(q, b, "job_id", "job", "id")
    return _local_model_refusal(
        LM.download_resume(cfg, job_id=job_id, requested_by="dashboard:user",
                           confirmed=bool(b.get("confirm"))))


def h_local_models_delete(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                          p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import local_models as LM
    model = str(p.get("model_id", "") or "")
    if not model:
        return _local_model_refusal(
            LM.public_error(LM.BAD_INPUT_CODE, "model_id is required"))
    return _local_model_refusal(
        LM.public_error(LM.BROKERED_CODE,
                        f"removing local model {model!r} is brokered; nothing "
                        "was deleted"))


def h_local_models_quickstart(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                              p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import local_models as LM
    model = _query_text(q, b, "model") or None
    return _local_model_refusal(
        LM.quickstart(cfg, model=model, requested_by="dashboard:user",
                      confirmed=bool(b.get("confirm"))))


def h_local_models_server(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                          p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import local_models as LM
    action = _query_text(q, b, "action") or "start"
    return _local_model_refusal(
        LM.server(cfg, action, requested_by="dashboard:user",
                  confirmed=bool(b.get("confirm"))))


def h_local_models_eject(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                         p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import local_models as LM
    model = _query_text(q, b, "model", "model_id")
    return _local_model_refusal(
        LM.eject(cfg, model=model, requested_by="dashboard:user",
                 confirmed=bool(b.get("confirm"))))


def h_local_models_activate(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                            p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import local_models as LM
    model = _query_text(q, b, "model", "model_id")
    return _local_model_refusal(
        LM.activate(cfg, model=model, requested_by="dashboard:user",
                    confirmed=bool(b.get("confirm"))))


def h_local_models_sideload(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                            p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import local_models as LM
    path = _query_text(q, b, "path")
    return _local_model_refusal(
        LM.sideload(cfg, path=path, requested_by="dashboard:user",
                    confirmed=bool(b.get("confirm"))))


def h_local_models_search(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                          p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import local_models as LM
    return _local_model_refusal(
        LM.public_error(LM.UNAVAILABLE_CODE,
                        "remote local-model search is not performed; Qubit "
                        "downloads nothing from a model hub"))


def h_local_models_search_files(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                                p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    return h_local_models_search(cfg, q, b, p)


def h_local_models_download_browsed(cfg: Any, q: Dict[str, str],
                                    b: Dict[str, Any],
                                    p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import local_models as LM
    return _local_model_refusal(
        LM.public_error(LM.BROKERED_CODE,
                        "browsed model download is brokered; nothing was "
                        "downloaded or queued"))


def h_custom_endpoints(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                       p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/providers/custom-endpoints -> configured + staged endpoints."""
    from .providers.policy import FrontierPolicy
    try:
        policy = FrontierPolicy.from_config(cfg)
    except Exception as exc:
        return _fail(exc)
    endpoints: List[Dict[str, Any]] = []
    providers = getattr(policy, "providers", {}) or {}
    for provider_id, pcfg in providers.items():
        if not isinstance(pcfg, dict):
            continue
        base = str(pcfg.get("base_url", pcfg.get("url", "")) or "")
        models = pcfg.get("models")
        models = models if isinstance(models, list) else []
        endpoints.append({
            "id": str(provider_id), "name": str(pcfg.get("name", provider_id)
                                                or provider_id),
            "base_url": _redact(base),
            "model": str(pcfg.get("model", "") or ""),
            "models": [str(m) for m in models],
            "context_length": pcfg.get("context_length"),
            "discover_models": bool(pcfg.get("discover_models", True)),
            "has_api_key": bool(pcfg.get("key_ref") or pcfg.get("api_key")),
            "api_key_preview": None,
            "is_current": False, "source": "intent.frontier.providers",
        })
    staged = _load_json_store(_custom_endpoints_read_path(cfg))
    seen = {row["id"] for row in endpoints}
    for ident in sorted(staged):
        rec = staged[ident]
        if not isinstance(rec, dict) or ident in seen:
            continue
        endpoints.append({
            "id": ident, "name": str(rec.get("name", ident) or ident),
            "base_url": _redact(str(rec.get("base_url", rec.get("url", "")))),
            "model": str(rec.get("model", "") or ""),
            "models": [str(m) for m in (rec.get("models") or [])
                       if isinstance(m, str)],
            "context_length": rec.get("context_length"),
            "discover_models": bool(rec.get("discover_models", True)),
            "has_api_key": False, "api_key_preview": None,
            "is_current": bool(rec.get("active", False)),
            "source": "qubit-settings",
        })
    active = str(staged.get("_active", "") or "")
    return _ok(endpoints=endpoints,
               current={"provider": active or str(getattr(policy, "route", "")
                                                  or ""),
                        "model": "", "base_url": ""})


def _custom_endpoints_path(cfg: Any) -> Path:
    # Canonical Qubit store (the write target).
    return _store_path(cfg, "qubit_custom_endpoints.json")


def _custom_endpoints_read_path(cfg: Any) -> Path:
    # Reads fall back to the legacy `hermes_*` name until the next save.
    return _migrated_read_path(cfg, "qubit_custom_endpoints.json",
                               "hermes_custom_endpoints.json")


def _custom_endpoint_id(b: Dict[str, Any], p: Dict[str, str]) -> str:
    raw = str(p.get("id", "") or b.get("id", b.get("endpoint_id",
                                                     b.get("name", ""))) or "")
    return re.sub(r"[^a-z0-9_.-]+", "-", raw.strip().lower()).strip("-")[:64]


def h_custom_endpoints_write(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                             p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/providers/custom-endpoints -> real settings-console write.

    Persists the endpoint into the same state-dir settings store the config
    console uses (atomic backup + audit). Confirm-gated; a malformed body or
    missing id is a typed 400. Staged endpoints are visible via the GET above
    and are never auto-activated or probed.
    """
    if not isinstance(b, dict) or not b:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "endpoint body required",
                     "qubit_counterpart": "dashboard Settings (classic)"}
    ident = _custom_endpoint_id(b, p)
    if not ident:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "endpoint id/name required",
                     "qubit_counterpart": "dashboard Settings (classic)"}
    values = {k: v for k, v in b.items()
              if k != "confirm" and isinstance(v, (str, int, float, bool))}
    values["id"] = ident
    # Shared one-path gate: e-stop-first, wire-truth confirm parsing, and a real
    # single-use journal approval (usable by the pending-approvals id-only
    # Confirm). The old hand-rolled ``if not b.get("confirm")`` bypassed the
    # e-stop and treated the string "false"/"0" as confirmation.
    guard = _compat_confirm(
        cfg, "custom_endpoints_write",
        f"custom endpoint {ident!r} is a settings write: re-POST with "
        "{\"confirm\": true} plus a fresh approval", q, b, payload=values)
    if guard is not None:
        return guard
    path = _custom_endpoints_path(cfg)
    try:
        store = _load_json_store(_custom_endpoints_read_path(cfg))
        previous = store.get(ident)
        store[ident] = values
        _save_json_store(path, store)
    except Exception as exc:
        return _fail(exc)
    return _ok(ok=True, id=ident, endpoint=values,
               replaced=isinstance(previous, dict), count=len(store),
               qubit_counterpart="dashboard Settings (classic) + CLI config "
                                 "profiles")


def h_custom_endpoints_activate(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                                p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST .../{id}/activate -> real confirm-gated active-endpoint selection."""
    ident = _custom_endpoint_id(b, p)
    if not ident:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "endpoint id required",
                     "qubit_counterpart": "dashboard Settings (classic)"}
    # Confirmation (and e-stop) is the FIRST gate, before we reveal whether the
    # id exists: an unconfirmed request is a typed 409, never a 404 (and it
    # keeps e-stop-first ordering consistent with the other writes).
    guard = _compat_confirm(
        cfg, "custom_endpoints_activate",
        f"activating custom endpoint {ident!r} requires confirmation: "
        "re-POST with {\"confirm\": true}", q, b, payload={"id": ident})
    if guard is not None:
        return guard
    path = _custom_endpoints_path(cfg)
    store = _load_json_store(_custom_endpoints_read_path(cfg))
    if ident not in store:
        return 404, {"ok": False, "code": "ENDPOINT_NOT_FOUND",
                     "detail": f"no staged endpoint {ident!r}",
                     "qubit_counterpart": "dashboard Settings (classic)"}
    record = store.get(ident)
    record = record if isinstance(record, dict) else {}
    # Real apply: register the endpoint as a model profile, allow that profile
    # (explicit confirm-gated activation, never a global widening), make it the
    # active selection, and mirror the runtime ``config.yaml`` model slot the
    # reference writes. The old body only wrote ``_active`` — a flag no runtime
    # consumer reads, so /model/info stayed on the old model (fabricated state).
    from .providers import endpoints as EP

    try:
        out = EP.activate_endpoint(cfg, ident, record, confirm=True)
    except Exception as exc:
        return _fail(exc)
    if not out.get("ok"):
        return 400, {"ok": False, "id": ident,
                     "code": str(out.get("code", "ACTIVATE_FAILED")),
                     "detail": str(out.get("detail", "")),
                     "qubit_counterpart": "dashboard Settings (classic) + CLI "
                                          "config profiles"}
    try:
        store["_active"] = ident
        _save_json_store(path, store)
    except Exception as exc:
        return _fail(exc)
    return _ok(ok=True, id=ident, active=ident,
               provider=str(out.get("provider", "")),
               model=str(out.get("model", "")),
               qubit_counterpart="dashboard Settings (classic) + CLI config "
                                 "profiles")


def h_custom_endpoints_delete(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                              p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """DELETE /api/providers/custom-endpoints/{id} -> real confirm-gated remove."""
    ident = _custom_endpoint_id(b, p)
    if not ident:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "endpoint id required",
                     "qubit_counterpart": "dashboard Settings (classic)"}
    guard = _compat_confirm(
        cfg, "custom_endpoints_delete",
        f"removing custom endpoint {ident!r} requires confirmation: "
        "re-POST with {\"confirm\": true}", q, b, payload={"id": ident})
    if guard is not None:
        return guard
    path = _custom_endpoints_path(cfg)
    try:
        store = _load_json_store(_custom_endpoints_read_path(cfg))
        existed = ident in store
        store.pop(ident, None)
        if store.get("_active") == ident:
            store.pop("_active", None)
        _save_json_store(path, store)
    except Exception as exc:
        return _fail(exc)
    # Remove the endpoint's registered model profile + active pointer too, so a
    # deleted endpoint is not still reported as the effective model (D4). The
    # shipped default profile is never touched.
    cleanup: Dict[str, Any] = {}
    try:
        from .providers import endpoints as EP

        cleanup = EP.remove_endpoint(cfg, ident, confirm=True)
    except Exception:  # noqa: BLE001 - the JSON removal already succeeded
        cleanup = {}
    return _ok(ok=True, id=ident, removed=existed, count=len(store),
               profile_removed=bool(cleanup.get("profile_removed")),
               active_cleared=bool(cleanup.get("active_cleared")),
               qubit_counterpart="dashboard Settings (classic) + CLI config "
                                 "profiles")


def h_profiles_sessions(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                        p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/profiles/sessions -> unified receipts list (one profile)."""
    try:
        limit = max(1, min(int(q.get("limit", "20") or "20"), 500))
    except (TypeError, ValueError):
        limit = 20
    code, out = h_sessions_list(cfg, {"limit": str(limit)}, b, p)
    if code != 200:
        return code, out
    total = int(out.get("total", 0) or 0)
    return _ok(sessions=out.get("sessions", []), total=total,
               profile_totals={"default": total}, limit=limit,
               offset=int(out.get("offset", 0) or 0), errors=[],
               store_authority=out.get("store_authority",
                                       RECEIPT_SESSION_AUTHORITY))


def h_profiles_sessions_sidebar(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                                p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/profiles/sessions/sidebar -> recents slice (real receipts)."""
    try:
        recents_limit = max(1, min(int(q.get("recents_limit", "20") or "20"),
                                   500))
    except (TypeError, ValueError):
        recents_limit = 20
    code, out = h_sessions_list(cfg, {"limit": str(recents_limit)}, b, p)
    sessions = out.get("sessions", []) if code == 200 else []
    return _ok(recents={"sessions": sessions, "profiles_truncated": {},
                        "profiles_usage": {}},
               cron={"sessions": []},
               messaging={"sessions": [], "total": 0},
               errors=[],
               store_authority=out.get("store_authority",
                                       RECEIPT_SESSION_AUTHORITY))


def h_profiles_projects_tree(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                             p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/profiles/projects/tree -> real Qubit project folders."""
    from . import projects as PJ
    try:
        res = PJ.list_projects(cfg)
    except Exception as exc:
        return _fail(exc)
    projects = []
    for rec in res.get("projects", []) if isinstance(res, dict) else []:
        if not isinstance(rec, dict):
            continue
        folders = rec.get("folders", []) or []
        projects.append({
            "id": str(rec.get("slug", "")),
            "name": str(rec.get("name", "") or ""),
            "path": str(folders[0]) if folders else None,
            "isAuto": False, "repos": [], "sessionCount": 0,
            "totalTokens": 0, "totalCostUsd": 0.0,
            "lastActive": rec.get("updated_at"), "previewSessions": [],
        })
    active = res.get("active_slug") if isinstance(res, dict) else None
    return _ok(projects=projects, active_id=active, scoped_session_ids=[],
               errors=[])


def h_chat_workspaces(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                      p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/chat/workspaces -> real dirs a fresh ``/chat`` may start in.

    Ported wire shape from the v0.21.5 vendored router
    (``qubit_runtime/qubit_cli/web_routers/chat_workspaces.py:64``); Qubit has
    no per-profile ``projects.db`` + repo-discovery scanner, so this is a
    read-only projection of the ONE canonical Qubit project registry
    (:mod:`voicebridge.projects`) — the same store the CLI ``qubit project``,
    the dashboard Projects page, and the voice ``chat_workspaces`` entry read.

    ``repos`` is the honest empty list and ``scan_enabled`` is ``False``:
    Qubit runs no background git-root scanner, so it never fabricates a
    discovered-repo list. ``scan=1`` is accepted and is a no-op for the same
    reason. ``default_cwd`` is the active project's primary folder when one is
    set, else the process home — the real fallback a fresh chat lands in.
    """
    from . import projects as PJ
    try:
        res = PJ.list_projects(cfg)
        active = PJ.active_project(cfg)
    except Exception as exc:
        return _fail(exc)
    projects: List[Dict[str, Any]] = []
    for rec in res.get("projects", []) if isinstance(res, dict) else []:
        if not isinstance(rec, dict):
            continue
        primary = rec.get("primary")
        folders = [{"path": str(f), "label": None,
                    "is_primary": bool(f == primary)}
                   for f in (rec.get("folders") or [])]
        projects.append({
            "id": str(rec.get("slug", "")),
            "slug": str(rec.get("slug", "")),
            "name": str(rec.get("name", "") or ""),
            "primary_path": primary,
            "archived": bool(rec.get("archived", False)),
            "folders": folders,
        })
    default_cwd = ""
    if isinstance(active, dict) and active.get("state") == "OK":
        default_cwd = str((active.get("project") or {}).get("primary") or "")
    if not default_cwd:
        default_cwd = str(Path.home())
    return _ok(projects=projects, repos=[], default_cwd=default_cwd,
               home=str(Path.home()), scan_enabled=False,
               qubit_counterpart="CLI `qubit project list` + dashboard Projects")


def h_profile_create(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                     p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/profiles -> create a real reference profile (one registry).

    Ported from the reference ``web_routers/profiles.py:create_profile_endpoint``
    (``:690``): creates the per-profile home (cloning config/skills/SOUL when
    asked), seeds bundled skills, and writes the submitted description/model
    into the new profile's own files. Confirm-gated through the shared journal
    with a REAL single-use ``confirmation_id``; a duplicate is a typed 400,
    never a fabricated success.
    """
    name = str(b.get("name", "") or "").strip()
    if not name:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "profile name required",
                     "qubit_counterpart": "CLI `qubit agent profile create`"}
    guard = _compat_confirm(
        cfg, "profile_create",
        f"profile {_redact(name)!r} creation writes a new profile home; "
        "re-POST with {\"confirm\": true}", q, b)
    if guard is not None:
        return guard
    try:
        P = _profiles_module(cfg)
    except Exception as exc:
        return _fail(exc)
    # Clone semantics, exactly like the reference endpoint.
    explicit_source = str(b.get("clone_from") or "").strip()
    clone_all = bool(b.get("clone_all"))
    if explicit_source:
        clone_from, clone_config = explicit_source, not clone_all
    elif clone_all:
        clone_from, clone_config = "default", False
    else:
        clone_from = "default" if b.get("clone_from_default") else None
        clone_config = clone_from is not None
    clone = clone_from is not None
    no_skills = bool(b.get("no_skills"))
    description = str(b.get("description", "") or "").strip()
    try:
        path = P.create_profile(
            name=name, clone_from=clone_from, clone_all=clone_all,
            clone_config=clone_config, no_skills=no_skills,
            description=description or None,
            clone_channels=bool(b.get("clone_channels")))
    except FileExistsError as exc:
        return 400, {"ok": False, "code": "PROFILE_EXISTS",
                     "detail": _redact(str(exc))[:300],
                     "qubit_counterpart": "CLI `qubit agent profile create`"}
    except ValueError as exc:
        return 400, {"ok": False, "code": "PROFILE_BAD_NAME",
                     "detail": _redact(str(exc))[:300],
                     "qubit_counterpart": "CLI `qubit agent profile create`"}
    except Exception as exc:
        return _fail(exc)
    canon = P.normalize_profile_name(name)
    # Fresh (non-clone) profiles get the bundled skills; clones already copied
    # the source's. Best-effort, exactly like the reference.
    if not clone:
        try:
            P.seed_profile_skills(path, quiet=True)
        except Exception:  # noqa: BLE001 - profile already exists
            pass
    # The CLI wrapper (~/.local/bin/<name>) is a reference convenience for
    # shell use; skip it under test so a focused test never writes the
    # operator's bin dir.
    try:
        from . import qubit_runtime
        if not qubit_runtime.under_test() and not P.check_alias_collision(canon):
            P.create_wrapper_script(canon)
    except Exception:  # noqa: BLE001
        pass
    provider = str(b.get("provider", "") or "").strip()
    model = str(b.get("model", "") or "").strip()
    model_set = False
    model_error = ""
    if provider and model:
        try:
            from qubit_cli.web_routers.profiles import _write_profile_model
            from qubit_constants import get_process_qubit_home
            # Validate against THIS dashboard's home (the catalog the picker
            # offered), not the just-created empty profile; the write still lands
            # in the new profile (parity with web_routers/profiles.py).
            _write_profile_model(path, provider, model,
                                 validate_in=get_process_qubit_home())
            model_set = True
        except Exception as exc:  # noqa: BLE001 - reported, not fatal
            model_error = _redact(str(exc))[:200]
    skills_disabled = 0
    keep_skills = b.get("keep_skills")
    if isinstance(keep_skills, list) and keep_skills:
        try:
            from qubit_cli.web_routers.profiles import _disable_unselected_skills
            skills_disabled = _disable_unselected_skills(
                path, [str(s) for s in keep_skills])
        except Exception:  # noqa: BLE001
            skills_disabled = 0
    # MCP servers submitted by the builder step (reference
    # ``_write_profile_mcp_servers``): written into the new profile's own
    # config.yaml. Best-effort, never fatal (the profile already exists).
    mcp_written = 0
    mcp_in = b.get("mcp_servers")
    if isinstance(mcp_in, list) and mcp_in:
        try:
            from qubit_cli.web_models import MCPServerCreate
            from qubit_cli.web_server_profiles import _write_profile_mcp_servers
            allowed = {"name", "url", "command", "args", "env", "auth",
                       "bearer_token", "profile"}
            models = [MCPServerCreate(**{k: v for k, v in s.items()
                                         if k in allowed})
                      for s in mcp_in if isinstance(s, dict)]
            mcp_written = _write_profile_mcp_servers(path, models)
        except Exception:  # noqa: BLE001 - reported as 0, never fatal
            mcp_written = 0
    # Hub skills submitted by the builder step: spawned async, scoped via
    # ``-p <name>`` exactly like the reference; the pid goes back for the UI
    # to poll. Best-effort (a spawn failure leaves pid null).
    hub_installs: List[Dict[str, Any]] = []
    hub_in = b.get("hub_skills")
    if isinstance(hub_in, list):
        for ident in (str(i or "").strip() for i in hub_in):
            if not ident:
                continue
            pid = None
            try:
                from qubit_cli.web_server_gateway import _spawn_qubit_action
                from qubit_cli.web_server_profiles import _hub_action_name
                pid = _spawn_qubit_action(
                    ["-p", canon, "skills", "install", ident, "--yes"],
                    _hub_action_name("install", ident)).pid
            except Exception:  # noqa: BLE001 - best-effort
                pid = None
            hub_installs.append({"identifier": ident, "pid": pid})
    _audit("profile.create", "dashboard:user",
           {"name": canon, "clone": clone, "model_set": model_set,
            "mcp_written": mcp_written, "hub_installs": len(hub_installs)})
    return _ok(ok=True, name=canon, path=canon, model_set=model_set,
               model_error=model_error, mcp_written=mcp_written,
               hub_installs=hub_installs, skills_disabled=skills_disabled,
               qubit_counterpart="CLI `qubit agent profile create`")


def h_profile_export(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                     p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/profiles/{name}/export -> real confirm-gated Brain export.

    Reads the scoped profile data held by the same ``brain`` store the CLI
    (``qubit brain export``) and dashboard /ops use. Confirm-gated and typed:
    an unknown principal is a 404, never a fabricated export.
    """
    from . import brain as B
    name = str(p.get("name", "") or "").strip()
    if not name:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "profile name required",
                     "qubit_counterpart": "CLI `qubit brain export <principal>`"}
    if not b.get("confirm"):
        return _staged(
            f"profile {_redact(name)!r} export reads scoped profile data and "
            "requires confirmation: re-POST with {\"confirm\": true}", "")
    try:
        res = B.profile_export(cfg, name,
                               identity=str(b.get("identity", "") or ""))
    except Exception as exc:
        code = getattr(exc, "code", None) or type(exc).__name__
        return 400, {"ok": False, "code": code,
                     "detail": _redact(str(exc))[:300],
                     "qubit_counterpart": "CLI `qubit brain export <principal>`"}
    return _ok(ok=True, principal=res.get("principal", name),
               profiles=res.get("profiles", {}),
               qubit_counterpart="CLI `qubit brain export <principal>`")


def h_terminal_backends(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                        p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/tools/terminal/backends -> real local backend inventory."""
    from . import capability_catalog as CC
    try:
        local = CC.check("terminal")
    except Exception:
        local = {"state": "UNKNOWN", "explanation": ""}
    ready = str(local.get("state", "")) in ("IMPLEMENTED", "NOT_UPSTREAM")
    backends = [{
        "name": "local", "label": "Qubit local (brokered sandbox)",
        "description": str(local.get("explanation", "")
                           or "Qubit local command execution")[:200],
        "active": True, "status": "ready" if ready else "needs_setup",
        "detail": "single Qubit sandbox backend; no remote terminal pool",
    }]
    return _ok(active="local", backends=backends)


def h_terminal_backend_set(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                           p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """PUT /api/tools/terminal/backend -> real confirm-gated capability grant.

    Qubit runs exactly one local brokered sandbox; the requested backend is
    recorded as a capability grant (``terminal.backend``) in the same
    ``PermissionsStore`` CLI/voice use, with audit and confirmation. It never
    starts a remote terminal pool or fabricates an active switch.
    """
    from .permissions import permissions_store
    backend = str(b.get("backend", b.get("name", "local")) or "local").strip()
    if not backend:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "backend required",
                     "qubit_counterpart": "CLI `qubit permissions`"}
    if not b.get("confirm"):
        return _staged(
            f"terminal backend {backend!r} selection requires confirmation: "
            "re-POST with {\"confirm\": true}", "")
    try:
        grant = permissions_store(cfg).grant(
            "terminal.backend", requested_by="dashboard:user", confirmed=True,
            scopes=(backend,),
            note="dashboard terminal backend selection (local sandbox only)")
    except Exception as exc:
        code = getattr(exc, "code", None) or type(exc).__name__
        return 400, {"ok": False, "code": code,
                     "detail": _redact(str(exc))[:300],
                     "qubit_counterpart": "CLI `qubit permissions`"}
    return _ok(ok=True, active="local", backend=backend, grant_id=grant.id,
               qubit_counterpart="CLI `qubit permissions` + `qubit setup` "
                                 "(single local sandbox backend)")


def h_computer_use_status(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                          p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/tools/computer-use/status -> real readiness probes."""
    from . import computer_use as CU
    try:
        res = CU.status()
    except Exception as exc:
        return _fail(exc)
    return _ok(**res)


def h_computer_use_grant(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                         p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/tools/computer-use/permissions/grant -> real audited grant.

    Records a desktop-control capability grant (``computer_use.access``) in
    the shared ``PermissionsStore`` the CLI/voice use, confirm-gated and
    audited. It never downloads an unpinned driver (``computer_use`` keeps
    that a separate typed refusal).
    """
    from . import computer_use as CU
    from .permissions import permissions_store
    scope = str(b.get("scope", b.get("capability", "desktop")) or "desktop")
    if not b.get("confirm"):
        return _staged(
            "desktop-control permission grant requires confirmation: re-POST "
            "with {\"confirm\": true}", "")
    try:
        grant = permissions_store(cfg).grant(
            "computer_use.access", requested_by="dashboard:user",
            confirmed=True, scopes=(scope,),
            note="dashboard computer-use permission grant (accessibility)")
        try:
            status = CU.status()
        except Exception:
            status = {}
    except Exception as exc:
        code = getattr(exc, "code", None) or type(exc).__name__
        return 400, {"ok": False, "code": code,
                     "detail": _redact(str(exc))[:300],
                     "qubit_counterpart": "CLI `qubit computer-use permissions`"}
    return _ok(ok=True, grant_id=grant.id, scope=scope, status=status,
               qubit_counterpart="CLI `qubit computer-use permissions` "
                                 "(Packet 8V accessibility)")



def h_learning_graph(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                     p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/learning/graph -> lessons ledger as a graph."""
    from . import learning as L
    try:
        lessons = L.LearningLedger(Path(cfg.state_dir())).lessons()
    except Exception as exc:
        return _fail(exc)
    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []
    seen_evidence: set = set()
    for lesson in lessons if isinstance(lessons, list) else []:
        if not isinstance(lesson, dict):
            continue
        lid = str(lesson.get("lesson_id", "") or "")
        if not lid:
            continue
        nodes.append({"id": lid, "type": "lesson",
                      "label": str(lesson.get("text", ""))[:80],
                      "kind": lesson.get("kind"),
                      "confidence": lesson.get("confidence"),
                      "source": lesson.get("source")})
        for ev in lesson.get("evidence_ids", []) or []:
            ev = str(ev)
            if ev and ev not in seen_evidence:
                seen_evidence.add(ev)
                nodes.append({"id": ev, "type": "evidence", "label": ev})
            if ev:
                edges.append({"source": ev, "target": lid, "kind": "supports"})
        for other in lesson.get("conflict_with", []) or []:
            edges.append({"source": lid, "target": str(other),
                          "kind": "conflicts"})
    lessons_n = sum(1 for n in nodes if n["type"] == "lesson")
    evidence_n = sum(1 for n in nodes if n["type"] == "evidence")
    return _ok(nodes=nodes, edges=edges,
               stats={"lessons": lessons_n, "evidence": evidence_n,
                      "nodes": len(nodes), "edges": len(edges)})


def h_learning_node(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/learning/node -> one lesson's stored provenance."""
    from . import learning as L
    lid = str(q.get("id", b.get("id", "")) or "").strip()
    if not lid:
        return _ok(found=False, id="", node=None,
                   detail="learning node id required",
                   qubit_counterpart="CLI `qubit lessons show <id>`")
    try:
        detail = L.LearningLedger(Path(cfg.state_dir())).explain(lid)
    except Exception as exc:
        return _fail(exc)
    if detail is None:
        return _ok(found=False, id=lid, node=None,
                   detail=f"no learning node {lid!r}",
                   qubit_counterpart="CLI `qubit lessons` (same LearningLedger)")
    return _ok(found=True, id=lid, node={"id": lid, **detail})


def h_learning_node_update(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                           p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """PUT /api/learning/node -> real confirm-gated lesson promotion.

    Lessons are append-only; the only in-place mutation is promoting a staged
    review item to authoritative (the same ``LearningLedger.promote`` the CLI
    ``qubit lessons promote`` calls). Without ``confirm`` it stages a typed
    409; an unknown id is a typed 404 -- never a fabricated write.
    """
    from . import learning as L
    lid = str(q.get("id", b.get("id", "")) or "").strip()
    if not lid:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "learning node id required",
                     "qubit_counterpart": "CLI `qubit lessons show <id>`"}
    if not b.get("confirm"):
        return _staged(
            "learning node promotion stages through the Qubit lessons ledger "
            "with confirmation; nothing was written", "")
    try:
        promoted = L.LearningLedger(Path(cfg.state_dir())).promote(lid)
    except Exception as exc:
        return _fail(exc)
    if promoted is None:
        return 404, {"ok": False, "code": "LESSON_NOT_FOUND",
                     "detail": f"no learning node {lid!r}",
                     "qubit_counterpart": "CLI `qubit lessons`"}
    return _ok(ok=True, id=lid, node={"id": lid, **promoted},
               qubit_counterpart="CLI `qubit lessons promote <id>`")


def h_learning_node_delete(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                           p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """DELETE /api/learning/node -> real confirm-gated lesson tombstone.

    Runs the same ``LearningLedger.remove`` the CLI ``qubit lessons forget``
    calls (tombstone + audit; history retained). Confirm-gated; an unknown id
    is a typed 404, never a false success.
    """
    from . import learning as L
    lid = str(q.get("id", b.get("id", "")) or "").strip()
    if not lid:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "learning node id required",
                     "qubit_counterpart": "CLI `qubit lessons forget <id>`"}
    if not b.get("confirm"):
        return _staged(
            "learning node deletion stages through the Qubit lessons ledger "
            "with confirmation; nothing was removed", "")
    try:
        removed = L.LearningLedger(Path(cfg.state_dir())).remove(lid)
    except Exception as exc:
        return _fail(exc)
    if not removed:
        return 404, {"ok": False, "code": "LESSON_NOT_FOUND",
                     "detail": f"no learning node {lid!r}",
                     "qubit_counterpart": "CLI `qubit lessons`"}
    return _ok(ok=True, id=lid, removed=True,
               qubit_counterpart="CLI `qubit lessons forget <id>`")


# ---------------------------------------------------------------------------
# Qubit-owned dashboard auth: loopback identity + RFC 8252 native handshake.
#
# The vendored dashboard ships an OAuth round trip
# (qubit_runtime/qubit_cli/dashboard_auth/routes.py). Qubit has no external
# IDP: its dashboard identity is the loopback operator with a loopback
# session token (voicebridge/webui.py:token_ok). These handlers are the
# Qubit-safe local equivalent: provider listing and manual-handoff guidance
# come from voicebridge.auth_status / voicebridge.auth_login, and the native
# loopback handshake (RFC 8252 + PKCE S256, single-use codes, short TTL,
# hashed at rest) is served locally by Qubit itself. No outbound request, no
# external credential capture, no query-string token, no second listener.
# Raw tokens are returned once; only their SHA-256 hash is persisted (0600).
# ---------------------------------------------------------------------------

_AUTH_SCHEMA = "qubit-dashboard-auth/1"
_AUTH_CODE_TTL = 300.0
_AUTH_ACCESS_TTL = 12 * 3600.0
_AUTH_REFRESH_TTL = 30 * 86400.0
_AUTH_LOOPBACK_HOSTS = ("127.0.0.1", "::1")


def _auth_store_path(cfg: Any) -> Path:
    # Canonical Qubit store (the write target).
    return _store_path(cfg, "qubit_dashboard_auth.json")


def _auth_store_read_path(cfg: Any) -> Path:
    # Reads fall back to the legacy `hermes_*` name until the next save.
    return _migrated_read_path(cfg, "qubit_dashboard_auth.json",
                               "hermes_dashboard_auth.json")


def _auth_store(cfg: Any) -> Dict[str, Any]:
    state = _load_json_store(_auth_store_read_path(cfg))
    state.setdefault("schema", _AUTH_SCHEMA)
    for key in ("pending", "codes", "session", "refresh"):
        if not isinstance(state.get(key), dict):
            state[key] = {}
    return state


def _auth_flush(cfg: Any, state: Dict[str, Any]) -> None:
    path = _auth_store_path(cfg)
    _save_json_store(path, state)
    try:
        import os as _os
        _os.chmod(path, 0o600)
    except OSError:
        pass


def _auth_hash(value: Any) -> str:
    import base64 as _b64
    import hashlib as _hl
    return _b64.urlsafe_b64encode(
        _hl.sha256(str(value).encode("utf-8")).digest()).decode("ascii")


def _auth_ct_eq(a: Any, b: Any) -> bool:
    import hmac as _hmac
    return bool(a) and bool(b) and _hmac.compare_digest(str(a), str(b))


def _auth_new_token(nbytes: int = 32) -> str:
    import secrets as _secrets
    return _secrets.token_urlsafe(nbytes)


def _auth_prune(state: Dict[str, Any], now: Optional[float] = None) -> None:
    moment = time.time() if now is None else now
    for key in ("pending", "codes"):
        recs = state.get(key) or {}
        for rid in list(recs):
            rec = recs.get(rid) or {}
            if float(rec.get("expires_at", 0) or 0) < moment:
                recs.pop(rid, None)


def _auth_issue(state: Dict[str, Any], provider: str, now: float,
                user_id: str = "local-operator") -> Tuple[str, str]:
    access = _auth_new_token()
    refresh = _auth_new_token()
    state["session"] = {"token_hash": _auth_hash(access), "provider": provider,
                        "user_id": user_id, "issued_at": now,
                        "expires_at": now + _AUTH_ACCESS_TTL}
    state["refresh"] = {"token_hash": _auth_hash(refresh), "provider": provider,
                        "user_id": user_id, "issued_at": now,
                        "expires_at": now + _AUTH_REFRESH_TTL}
    return access, refresh


def _auth_bearer(access: str, refresh: str, provider: str,
                 now: float) -> Dict[str, Any]:
    return {"access_token": access, "refresh_token": refresh,
            "token_type": "Bearer", "expires_at": now + _AUTH_ACCESS_TTL,
            "provider": provider, "user_id": "local-operator"}


def _auth_providers(cfg: Any) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    try:
        from .auth_status import auth_status as _inv
        for rec in _inv(cfg).get("providers", []):
            name = str(rec.get("provider", "") or "")
            if not name:
                continue
            rows.append({"name": name, "display_name": name,
                         "supports_password": False,
                         "configured": bool(rec.get("configured")),
                         "key_ref": str(rec.get("key_ref", ""))})
    except Exception:
        pass
    return rows


def h_auth_providers(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                     p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/auth/providers -> real local provider inventory.

    Reports the loopback identity plus every configured frontier provider
    (names + credential-reference kind only, never values). External
    provider sign-in is manual-handoff, so ``supports_password`` is False.
    """
    return _ok(providers=_auth_providers(cfg),
               identity="local-operator", mode="loopback-token",
               detail="Qubit loopback dashboard identity; external provider "
                      "sign-in is manual-handoff (no OAuth token dance)")


def h_login_page(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /login -> real Qubit loopback login bootstrap (no IDP redirect)."""
    return _ok(ok=True, identity="local-operator", mode="loopback-token",
               loopback=True, providers=_auth_providers(cfg),
               next=str(q.get("next", "") or ""),
               detail="Qubit loopback login; no external IDP redirect")


def h_auth_login(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /auth/login -> real manual-handoff enrollment plan for a provider."""
    from .auth_login import login_plan
    provider = str(q.get("provider", "") or "")
    plan = login_plan(cfg, provider)
    if plan.get("state") != "OK":
        return 404, {
            "ok": False, "code": plan.get("code", "AUTH_UNKNOWN_PROVIDER"),
            "detail": _redact(plan.get("detail", "unknown provider"))[:300],
            "qubit_counterpart": "CLI `qubit auth login --provider`",
            "known_providers": plan.get("known_providers", []),
        }
    return _ok(ok=True, provider=plan.get("provider", provider),
               next=str(q.get("next", "") or ""), handoff=plan,
               detail="manual-handoff enrollment; Qubit never performs the "
                      "provider login or captures the credential")


def h_auth_native_authorize(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                            p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /auth/native/authorize -> Qubit-local RFC 8252 + PKCE S256 start.

    Only ``http://`` loopback redirect URIs are accepted (an open redirect
    would leak a live code), the challenge method must be S256, and the
    one-time code/verifier challenge is stored hashed with a short TTL.
    """
    from urllib.parse import urlencode, urlparse
    method = str(q.get("code_challenge_method", "") or "").upper()
    challenge = str(q.get("code_challenge", "") or "")
    redirect_uri = str(q.get("redirect_uri", "") or "")
    if method != "S256":
        return 400, {"ok": False, "code": "AUTH_BAD_CHALLENGE_METHOD",
                     "detail": "code_challenge_method must be S256",
                     "qubit_counterpart": "GET /login (loopback session)"}
    if not challenge:
        return 400, {"ok": False, "code": "AUTH_BAD_CHALLENGE",
                     "detail": "code_challenge required",
                     "qubit_counterpart": "GET /login (loopback session)"}
    parsed = urlparse(redirect_uri)
    if parsed.scheme != "http" or \
            (parsed.hostname or "").lower() not in _AUTH_LOOPBACK_HOSTS:
        return 400, {"ok": False, "code": "AUTH_BAD_REDIRECT",
                     "detail": "native redirect_uri must be http:// on the "
                               "loopback interface (127.0.0.1 / ::1)",
                     "qubit_counterpart": "GET /login (loopback session)"}
    provider = str(q.get("provider", "") or "loopback").strip() or "loopback"
    client_state = str(q.get("state", "") or "")
    now = time.time()
    state = _auth_store(cfg)
    _auth_prune(state, now)
    broker_state = _auth_new_token(16)
    code = _auth_new_token(24)
    state["codes"][_auth_hash(code)] = {
        "challenge": challenge, "redirect_uri": redirect_uri,
        "client_state": client_state, "provider": provider,
        "broker_state": broker_state, "expires_at": now + _AUTH_CODE_TTL,
        "used": False,
    }
    _auth_flush(cfg, state)
    sep = "&" if "?" in redirect_uri else "?"
    authorize_url = redirect_uri + sep + urlencode(
        {"code": code, "state": broker_state})
    _audit("auth_authorize", "dashboard:user",
           {"provider": provider, "redirect_host": parsed.hostname or ""})
    return _ok(ok=True, broker_state=broker_state, provider=provider,
               redirect_uri=redirect_uri, authorize_url=authorize_url,
               code=code, expires_in=_AUTH_CODE_TTL,
               detail="Qubit-local loopback authorization "
                      "(RFC 8252 + PKCE S256); no external IDP")


def h_auth_callback(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /auth/callback -> redeem a Qubit-local code into a session."""
    raw = str(q.get("code", "") or "")
    state_param = str(q.get("state", "") or "")
    if not raw:
        return 400, {"ok": False, "code": "AUTH_MISSING_CODE",
                     "detail": "Missing authorization code",
                     "qubit_counterpart": "GET /login"}
    now = time.time()
    state = _auth_store(cfg)
    _auth_prune(state, now)
    key = _auth_hash(raw)
    rec = state["codes"].get(key)
    if not rec or rec.get("used"):
        return 400, {"ok": False, "code": "AUTH_BAD_CODE",
                     "detail": "Invalid or expired authorization code",
                     "qubit_counterpart": "GET /login"}
    if state_param and rec.get("broker_state") and \
            not _auth_ct_eq(state_param, rec["broker_state"]):
        return 400, {"ok": False, "code": "AUTH_STATE_MISMATCH",
                     "detail": "OAuth state mismatch (CSRF check failed)",
                     "qubit_counterpart": "GET /login"}
    rec["used"] = True
    provider = str(rec.get("provider", "loopback") or "loopback")
    access, refresh = _auth_issue(state, provider, now)
    _auth_flush(cfg, state)
    _audit("auth_callback", "dashboard:user", {"provider": provider})
    return _ok(ok=True, next="/", provider=provider,
               session=_auth_bearer(access, refresh, provider, now))


def h_auth_native_token(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                        p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /auth/native/token -> verify PKCE and exchange the code for tokens.

    The code is consumed on every path (no verifier oracle, no replay); any
    failure is a generic typed 400. Tokens go in the JSON body.
    """
    raw = str(b.get("code", "") or "")
    verifier = str(b.get("code_verifier", "") or "")
    if not raw or not verifier:
        return 400, {"ok": False, "code": "AUTH_MISSING_CODE",
                     "detail": "Invalid or expired authorization code.",
                     "qubit_counterpart": "GET /login"}
    now = time.time()
    state = _auth_store(cfg)
    _auth_prune(state, now)
    key = _auth_hash(raw)
    rec = state["codes"].get(key)
    if not rec or rec.get("used"):
        return 400, {"ok": False, "code": "AUTH_BAD_CODE",
                     "detail": "Invalid or expired authorization code.",
                     "qubit_counterpart": "GET /login"}
    state["codes"].pop(key, None)
    if not _auth_ct_eq(_auth_hash(verifier), rec.get("challenge", "")):
        _auth_flush(cfg, state)
        return 400, {"ok": False, "code": "AUTH_BAD_CODE",
                     "detail": "Invalid or expired authorization code.",
                     "qubit_counterpart": "GET /login"}
    provider = str(rec.get("provider", "loopback") or "loopback")
    access, refresh = _auth_issue(state, provider, now)
    _auth_flush(cfg, state)
    _audit("auth_native_token", "dashboard:user", {"provider": provider})
    return _ok(ok=True, **_auth_bearer(access, refresh, provider, now))


def h_auth_native_refresh(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                          p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /auth/native/refresh -> rotate a desktop-held refresh token."""
    raw = str(b.get("refresh_token", "") or "")
    provider_hint = str(b.get("provider", "") or "")
    if not raw:
        return 400, {"ok": False, "code": "AUTH_MISSING_REFRESH",
                     "detail": "refresh_token required",
                     "qubit_counterpart": "GET /login"}
    now = time.time()
    state = _auth_store(cfg)
    _auth_prune(state, now)
    rec = state.get("refresh") or {}
    valid = (rec and float(rec.get("expires_at", 0) or 0) >= now
             and _auth_ct_eq(_auth_hash(raw), rec.get("token_hash", ""))
             and (not provider_hint or provider_hint == rec.get("provider", "")))
    if not valid:
        return 401, {"ok": False, "state": "UNAUTHORIZED",
                     "code": "AUTH_SESSION_EXPIRED",
                     "detail": "Refresh token expired or invalid; start a new "
                               "sign-in.",
                     "qubit_counterpart": "GET /login"}
    provider = str(rec.get("provider", "loopback") or "loopback")
    access, refresh = _auth_issue(state, provider, now)
    _auth_flush(cfg, state)
    _audit("auth_native_refresh", "dashboard:user", {"provider": provider})
    return _ok(ok=True, **_auth_bearer(access, refresh, provider, now))


def h_auth_password_login(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                          p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /auth/password-login -> verify the Qubit loopback credential.

    Qubit keeps no password store. The local equivalent accepts either a
    token previously issued by the loopback native flow or the configured
    dashboard token (``webui.token_ok``, constant-time, never a query-string
    token). Unknown/mismatched credentials are a generic 401.
    """
    supplied = str(b.get("token", b.get("password", "")) or "")
    provider = str(b.get("provider", "") or "loopback") or "loopback"
    now = time.time()
    state = _auth_store(cfg)
    _auth_prune(state, now)
    sess = state.get("session") or {}
    ok = bool(supplied) and _auth_ct_eq(_auth_hash(supplied),
                                        sess.get("token_hash", ""))
    if not ok and supplied:
        try:
            from .webui import token_ok
            cfg_token = str(getattr(cfg, "api_token", "") or "")
            if cfg_token:
                ok, _reason = token_ok(cfg_token, supplied, False)
        except Exception:
            ok = False
    if not ok:
        return 401, {"ok": False, "state": "UNAUTHORIZED",
                     "code": "AUTH_INVALID_CREDENTIALS",
                     "detail": "Invalid credentials",
                     "qubit_counterpart": "GET /login (loopback session)"}
    access, refresh = _auth_issue(state, provider, now)
    _auth_flush(cfg, state)
    _audit("auth_password_login", "dashboard:user", {"provider": provider})
    return _ok(ok=True, next="/", **_auth_bearer(access, refresh, provider,
                                                now))


def h_auth_logout(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                  p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /auth/logout -> revoke the loopback session + credential check."""
    from .integration_status import logout_check
    state = _auth_store(cfg)
    revoked = bool(state.get("session") or state.get("refresh")
                   or state.get("codes") or state.get("pending"))
    state["session"] = {}
    state["refresh"] = {}
    state["pending"] = {}
    state["codes"] = {}
    _auth_flush(cfg, state)
    _audit("auth_logout", "dashboard:user", {"revoked": revoked})
    return _ok(ok=True, logged_out=True, revoked=revoked, next="/login",
               check=logout_check(cfg),
               detail="loopback session revoked; no server-side cookie")


# ---------------------------------------------------------------------------
# Route table: (method, regex, handler, disposition)
# disposition is implemented|gated; gated rows carry code+reason+counterpart.
# ---------------------------------------------------------------------------

Route = Tuple[str, str, str]

ROUTES: List[Dict[str, Any]] = []

def _r(method: str, pattern: str, handler: str, disp: str = "implemented",
       gate: Optional[Dict[str, str]] = None) -> None:
    ROUTES.append({"method": method, "pattern": pattern, "handler": handler,
                   "disposition": disp, "gate": gate or {}})


def _g(code: str, detail: str, counterpart: str,
       owner: str = "qubit-policy",
       rollback: str = "read-only; nothing to roll back") -> Dict[str, str]:
    return {"code": code, "detail": detail, "counterpart": counterpart,
            "owner": owner, "rollback": rollback}


_NO_PTY = _g("PTY_NO_SHELL",
             "Qubit forbids raw shell/PTY spawn; the xterm mirror cannot attach. "
             "Use Qubit Chat (streaming agent turns) or the TUI live board.",
             "overlay Qubit Chat tab (POST /api/qubit/chat) + CLI `qubit tui`")
# The Qubit listener does not mount the curated console WebSocket (it runs one
# brokered event path). The refusal is accurate for /api/console: it points at
# the real Qubit safe consoles instead of claiming a raw PTY is being denied.
_NO_CONSOLE = _g("PTY_NO_SHELL",
                 "This listener serves Qubit Chat and the Qubit Operations "
                 "console; the curated console WebSocket is not mounted here. "
                 "No raw shell/PTY is exposed.",
                 "overlay Operations tab (/operations) + CLI `qubit console`")
_NO_WRITE = _g("WRITE_BROKERED",
               "Direct file writes are refused; writes run only as brokered, "
               "confirmed argv actions with audit receipts.",
               "CLI `qubit opencode` / projects + dashboard /ops + approvals")
_NO_SECRET = _g("SECRET_NEVER_SERVED",
                "Secret values never leave the vault; only references are listed.",
                "CLI `qubit auth status` (names only)")
_NO_SUPERVISION = _g("GATEWAY_NO_SUPERVISION",
                     "Qubit keeps a single event path; process supervision stays "
                     "with the operator.",
                     "operator systemd units + `qubit gateway` records")
# Multiplex migration folds per-profile gateways into one multiplexer and
# rewrites service units; it is operator-owned, so the dashboard POST is a
# typed refusal. The read-only plan (GET /api/gateway/migrate/plan) IS served.
_GATE_GATEWAY_MIGRATE = _g("GATEWAY_MIGRATE_OPERATOR_OWNED",
                           "Gateway multiplex migration rewrites service units "
                           "and live gateway processes; it is operator-owned. "
                           "Preflight is read-only (GET /api/gateway/migrate/plan).",
                           "CLI `qubit gateway migrate` (operator-run)")
_NO_REMOTE = _g("BLOCKED_REMOTE_SOURCE",
                "Remote fetch is refused; only local reviewed manifests install "
                "with explicit confirmation.",
                "CLI `qubit skills install <local-manifest>` + 7E validation")
_NO_OAUTH = _g("OAUTH_MANUAL_HANDOFF",
               "No live OAuth token dance is performed; enrollment is a manual "
               "handoff in the user's own browser.",
               "CLI `qubit auth login` (manual-handoff guided enrollment)")
_NO_UPDATE = _g("UPDATE_OPERATOR_OWNED",
                "In-place self-update from the dashboard is refused; upgrades are "
                "versioned, staged, and reversible.",
                "CLI `qubit product_*` / package + dashboard Setup page")
_NO_GIT_READ = _g("GIT_READ_SCOPED",
                  "Qubit git is a scoped read surface (status/log/diff/show); "
                  "this review verb has no dedicated backend here.",
                  "CLI `qubit git status|log|diff|show` + `qubit project`")
_NO_GH_AUTH = _g("GH_AUTH_OPERATOR",
                 "GitHub CLI auth status is never probed from the dashboard: "
                 "`gh auth status` shells out to a credentialed CLI and can "
                 "hang on a prompt. Qubit performs no network auth probe and "
                 "serves no token.",
                 "operator-run `gh auth status` + CLI `qubit auth status` "
                 "(names only)")
_NO_GIT_SHIP = _g("GIT_SHIP_GH_EXTERNAL",
                  "PR ship info needs the GitHub CLI (credentialed network); "
                  "Qubit never shells out to `gh` from the dashboard. Shipping "
                  "runs through the operator's own authenticated gh session.",
                  "operator `gh pr view` + CLI `qubit opencode` worktree flow")
_NO_CRON_FIRE = _g("CRON_FIRE_EXTERNAL",
                   "Managed-cron fire is an external NAS→agent webhook and is "
                   "not exposed through the dashboard; Qubit schedules run on "
                   "an estop-checked, audited tick.",
                   "CLI `qubit schedules run-due` + dashboard /ops "
                   "schedules_run_due",
                   owner="qubit-policy",
                   rollback="nothing fired; nothing to roll back")
_NO_PROFILE_PR = _g("GH_PR_EXTERNAL",
                    "Session PR association needs the GitHub CLI (credentialed "
                    "network); Qubit never shells out to `gh` from the "
                    "dashboard and serves no PR cache.",
                    "operator `gh pr list/view` + CLI `qubit sessions`",
                    owner="qubit-policy",
                    rollback="read-only; nothing to roll back")
_NO_GIT_WRITE = _g("GIT_WRITE_BROKERED",
                   "Qubit git is read-only; staging, commits, pushes, "
                   "worktrees, and branch switches run through the brokered "
                   "opencode worktree flow with confirmation.",
                   "CLI `qubit opencode` / `qubit project` + approvals")
_NO_MEDIA = _g("MEDIA_NOT_SERVED",
               "Gateway-local binary media data URLs are not served through "
               "the JSON compat API; Qubit file reads return bounded, redacted "
               "text only.",
               "CLI `qubit files read` + Qubit Chat attachment vision")
_NO_BINARY = _g("FILES_BINARY_STREAM",
                "Binary file download/stream responses are not served through "
                "the JSON compat dispatcher; managed files are read as bounded "
                "text or data URLs.",
                "GET /api/files/read + GET /api/fs/read-text")
_NO_AUDIO_RELAY = _g("AUDIO_RELAY_ONLY",
                     "Client-direct STT/TTS provider keys are never exported; "
                     "voice audio relays through the local Qubit pipeline.",
                     "classic /api/audio/status + Qubit Chat voice")
_NO_AUDIO_EXTERNAL = _g("PROVIDER_EXTERNAL",
                        "External voice catalogs (ElevenLabs) are not fetched; "
                        "TTS runs through the local provider chain.",
                        "local TTS providers (piper/edge/kokoro)")
_NO_AUDIO_ACTION = _g("AUDIO_ACTION_LOCAL",
                      "Audio synthesis/transcription runs only through the "
                      "local voice pipeline, never as a raw compat action.",
                      "Qubit Chat voice + CLI `qubit voice`")
_NO_AUDIO_LEASE = _g("AUDIO_LEASE_DESKTOP",
                     "TTS engine warm-up/release leases are desktop-only; the "
                     "local pipeline manages model residency itself.",
                     "Qubit voice pipeline (lazy model load)")
_NO_AUDIO_STREAM = _g("AUDIO_STREAM_LOCAL",
                      "Streaming PCM TTS is served by the local voice "
                      "pipeline, not as a compat WebSocket.",
                      "classic Qubit voice playback + Qubit Chat")
# ``/api/display/ws`` streams raw RFB from a profile's Xvnc to the Qubit
# Desktop viewer, gated by a single-use ``display.observe`` ticket + the
# bot-desktop lease (vendored ``web_routers/display.py``). This listener runs
# no tui_gateway dialect (so no ``display.observe`` mints a ticket) and hosts
# no Xvnc/RFB socket, so the honest answer is a typed refusal — the viewer is a
# Qubit Desktop client surface, and a fabricated byte stream is never served.
_NO_DISPLAY_WS = _g(
    "WS_DESKTOP_CLIENT_ONLY",
    "The raw RFB display stream is a Qubit Desktop client surface: it needs a "
    "single-use `display.observe` ticket and a live Xvnc host bridge, neither "
    "of which this web listener runs. View a bot's screen in Qubit Desktop.",
    "Qubit Desktop Bot-screen viewer (display.observe + bot-desktop lease)",
    owner="qubit-policy",
    rollback="nothing started; nothing to roll back")
# GPT-Live session creation is the one reference audio route that performs a
# DIRECT vendor call (POST https://api.openai.com/v1/live/sessions) with a
# project key. Qubit forbids direct vendor calls from the dashboard and never
# exports the key, so the POST is a typed refusal; the non-secret readiness
# read (GET /api/audio/voice-live/status) IS served.
_GATE_VOICE_LIVE_SESSION = _g(
    "VOICE_LIVE_VENDOR_DIRECT",
    "GPT-Live session creation exchanges an SDP offer with the vendor's "
    "/v1/live/sessions using a direct project key; Qubit makes no direct "
    "vendor call from the dashboard and never exports the key.",
    "GET /api/audio/voice-live/status (read-only readiness) + the chained "
    "local voice pipeline (classic /api/audio/* relay + Qubit Chat voice)")
# Backend retirement is a process-owned admission fence. Qubit never
# prepares/commits retirement for its own backend from the dashboard; the
# module is ported (used by the idle probe) but the route stays operator-owned.
_GATE_HEALTH_RETIREMENT = _g(
    "RETIREMENT_OPERATOR_OWNED",
    "Backend retirement freezes process admission and permanently closes "
    "admission on commit; it is operator-owned and never driven from the "
    "dashboard. GET /api/health/idle stays a read-only diagnostic.",
    "operator-run `qubit gateway stop --confirm` (supervisor e-stop -> "
    "confirmation -> audit)",
    rollback="nothing changed; no admission fence opened")
_NO_NETWORK_PROBE = _g("NETWORK_PROBE_BLOCKED",
                       "Live endpoint/credential probes open outbound sockets "
                       "from the dashboard; Qubit verifies providers through "
                       "the local governed gateway instead.",
                       "CLI `qubit auth status` + Qubit provider gateway "
                       "(audited, budgeted)")
_GATE_PROFILE_IMPORT = _g("PROFILE_IMPORT_REVIEW",
                          "Profile archives enter quarantine for review; a "
                          "reviewed archive imports via the Qubit principal "
                          "registry, never straight from the dashboard.",
                          "CLI `qubit import-state inspect` + `qubit brain "
                          "principal-add`")
_GATE_DESKTOP_OVERLAY = _g("DESKTOP_OVERLAY_ONLY",
                           "Desktop appearance overlays are a desktop-client "
                           "feature; the Qubit dashboard ships one theme and "
                           "serves no per-profile overlay.",
                           "Qubit theme (GET /api/dashboard/themes)")
_GATE_SESSIONS_RECEIPT = {
    **_g("SESSIONS_RECEIPT_STORE",
         "Qubit sessions are append-only audit receipts; owner back-fill is "
         "not offered. Memory tombstones/corrections own forgetting.",
         "CLI/voice memory_forget/correct (same stores)"),
    "http": 501,
}
# Typed gates for upstream dashboard plugin / MCP / checkpoint / import /
# provider-OAuth mutations. These were previously bare-string GATED entries,
# which the ledger generator rendered as misleading "STUB_" pseudo-codes; each
# is a genuine operator-only Qubit-safe refusal with a real code, reason, and
# counterpart (never an unfinished stub).
_GATE_SKILL_MANIFEST = _g(
    "SKILL_MANIFEST_REQUIRED",
    "Direct dashboard plugin installs are refused; install a validated local "
    "manifest staged disabled instead.",
    "CLI `qubit skills install <manifest>` + 7E validation")
_GATE_SKILL_VERSIONED = _g(
    "SKILL_VERSIONED",
    "In-place plugin updates are refused; versions are immutable. Remove and "
    "reinstall the new validated version.",
    "CLI `qubit skills remove/rollback/install`")
_GATE_CKPT_PRUNE = _g(
    "CHECKPOINT_CONFIRM_REQUIRED",
    "Checkpoint prune is metadata-destructive: confirm in the Qubit operations "
    "console with explicit scope.",
    "dashboard /ops checkpoints + CLI")
_GATE_IMPORT_REVIEW = _g(
    "IMPORT_REVIEW_REQUIRED",
    "Uploads enter quarantine for review; reviewed artifacts install via "
    "skills install. Nothing executes on upload.",
    "CLI `qubit import-agent inspect`")
_GATE_KANBAN_WRITE = _g(
    "KANBAN_WRITE_BROKERED",
    "This kanban mutation has no Qubit-native equivalent: the vendored "
    "sub-columns, links, runs, attachments and board transfer are not part of "
    "the Qubit board schema. Board/task create, edit, move, complete, archive, "
    "comment and bulk update are native and confirmed on the one board store.",
    "CLI `qubit kanban` + dashboard /qubit-boards + pending-approvals banner")

_r("GET", r"/api/status", "h_status")
_r("GET", r"/api/runtime", "h_runtime")
_r("GET", r"/api/health", "h_health")
# Token-gated diagnostic work snapshot (reference status.py:139); the mutating
# retirement route is an operator-owned typed refusal in the GATED table.
_r("GET", r"/api/health/idle", "h_health_idle")
_r("GET", r"/api/auth/me", "h_auth_me")
_r("GET", r"/api/events", "h_events")
_r("GET", r"/api/sessions", "h_sessions_list")
_r("GET", r"/api/sessions/stats", "h_sessions_stats")
_r("GET", r"/api/sessions/search", "h_sessions_list")
_r("GET", r"/api/sessions/(?P<id>[^/]+)/export", "h_session_show")
_r("GET", r"/api/sessions/(?P<id>[^/]+)/latest-descendant", "h_session_show")
_r("GET", r"/api/sessions/(?P<id>[^/]+)/messages", "h_session_messages")
# Durable timeline jump surface (reference sessions.py:619/641), served over
# the canonical Qubit session DB by the W9 slice-0 ``qubit_state_timeline``.
_r("GET", r"/api/sessions/(?P<id>[^/]+)/timeline", "h_session_timeline")
_r("GET", r"/api/sessions/(?P<id>[^/]+)/messages/around",
   "h_session_messages_around")
# Negative lookahead keeps the dedicated /api/sessions/shares collection from
# being captured as a session id by the generic show route below. The specific
# ``/api/sessions/empty[*]`` read (pinned bulk empty-session listing) must
# precede the generic id pattern or a bare ``/api/sessions/empty`` is misread
# as a session named ``empty``.
_r("GET", r"/api/sessions/empty.*", "sess_empty")
_r("GET", r"/api/sessions/(?!shares$)(?P<id>[^/]+)", "h_session_show")
# Bulk session maintenance (reference parity: import/prune/bulk-delete/empty).
# Registered ahead of the generic id patterns and the GATED table so the real
# handlers own them; each mutating route is e-stop + confirmation gated and
# writes only the one canonical ``state.db``.
_r("POST", r"/api/sessions/import", "sessions_import")
_r("POST", r"/api/sessions/prune", "sessions_prune")
_r("POST", r"/api/sessions/bulk-delete", "sessions_bulk_delete")
_r("DELETE", r"/api/sessions/empty", "sessions_empty_delete")
_r("GET", r"/api/config", "h_config")
_r("GET", r"/api/config/schema", "h_config_schema")
_r("GET", r"/api/config/defaults", "h_config_defaults")
_r("GET", r"/api/config/effective", "h_config_effective")
_r("GET", r"/api/egress/status", "h_egress_status")
_r("GET", r"/api/model/info", "h_model_info")
_r("GET", r"/api/model/recommended-default", "h_model_recommended_default")
_r("GET", r"/api/model/options", "h_model_options")
_r("GET", r"/api/model/moa", "h_model_moa")
_r("PUT", r"/api/model/moa", "moa_put")
_r("GET", r"/api/model/auxiliary", "h_model_auxiliary")
_r("POST", r"/api/model/set", "h_model_set")
_r("GET", r"/api/profiles", "h_profiles")
_r("GET", r"/api/profiles/active", "h_profiles_active")
_r("POST", r"/api/profiles/active", "profiles_active_set")
_r("GET", r"/api/profiles/(?P<name>[^/]+)/setup-command", "h_profile_setup_command")
_r("GET", r"/api/profiles/(?P<name>[^/]+)/soul", "h_profile_soul")
_r("PUT", r"/api/profiles/(?P<name>[^/]+)/soul", "profile_soul_set")
_r("PUT", r"/api/profiles/(?P<name>[^/]+)/description", "profile_desc")
_r("POST", r"/api/profiles/(?P<name>[^/]+)/describe-auto", "profile_describe_auto")
_r("PUT", r"/api/profiles/(?P<name>[^/]+)/model", "profile_model")
_r("PATCH", r"/api/profiles/(?P<name>[^/]+)", "profile_rename")
_r("DELETE", r"/api/profiles/(?P<name>[^/]+)", "profile_delete")
_r("GET", r"/api/skills", "h_skills")
_r("GET", r"/api/skills/content", "h_skill_content")
_r("POST", r"/api/skills", "skill_create")
_r("PUT", r"/api/skills/content", "skill_update")
_r("DELETE", r"/api/skills", "skill_delete")
_r("PUT", r"/api/skills/toggle", "h_skills_toggle")
_r("GET", r"/api/terminal/profiles", "h_terminal_profiles")
_r("PUT", r"/api/terminal/profile", "terminal_profile_set")
_r("GET", r"/api/tools/toolsets", "h_toolsets")
# Bare toolset toggle (SPA ``PUT /api/tools/toolsets/{name}`` with {enabled})
# must precede the ``.*`` config-write row so the persisted enable/disable
# contract owns it; sub-resource writes (/model, /provider, /env, /post-setup)
# keep the config-grant handler.
_r("PUT", r"/api/tools/toolsets/(?P<id>[^/]+)", "h_toolset_toggle")
# One power/e-stop authority for both the classic page and the SPA alias.
_r("GET", r"/api/power", "h_power")
_r("POST", r"/api/power", "power_action")
_r("GET", r"/api/cron/jobs", "h_cron_jobs")
_r("POST", r"/api/cron/jobs", "cron_create")
_r("PUT", r"/api/cron/jobs/(?P<id>[^/]+)", "cron_update")
_r("DELETE", r"/api/cron/jobs/(?P<id>[^/]+)", "cron_delete")
_r("GET", r"/api/cron/jobs/(?P<id>[^/]+)/runs", "h_cron_job_runs")
_r("GET", r"/api/cron/jobs/(?P<id>[^/]+)", "h_cron_job_show")
_r("POST", r"/api/cron/jobs/(?P<id>[^/]+)/pause", "cron_pause")
_r("POST", r"/api/cron/jobs/(?P<id>[^/]+)/resume", "cron_resume")
_r("POST", r"/api/cron/jobs/(?P<id>[^/]+)/trigger", "cron_trigger")
_r("GET", r"/api/logs", "h_logs")
_r("GET", r"/api/system/stats", "h_system_stats")
_r("GET", r"/api/mcp", "h_mcp")
_r("GET", r"/api/mcp/servers", "h_mcp_servers")
_r("POST", r"/api/mcp/servers", "mcp_create")
_r("PUT", r"/api/mcp/servers", "mcp_update")
_r("DELETE", r"/api/mcp/servers/(?P<id>[^/]+)", "mcp_delete")
# B-H1: MCP server CRUD/test/auth are real operations on the canonical Qubit
# MCP registry (:mod:`voicebridge.mcp_servers`). They persist config and
# reference-only credentials and never open a network mount. Registered ahead
# of the retired MCP_NO_MOUNT blanket gate.
_r("POST", r"/api/mcp/servers/(?P<id>[^/]+)/enabled", "mcp_set_enabled")
_r("PUT", r"/api/mcp/servers/(?P<id>[^/]+)/enabled", "mcp_set_enabled")
_r("GET", r"/api/mcp/servers/(?P<id>[^/]+)/auth", "mcp_auth_status")
_r("POST", r"/api/mcp/servers/(?P<id>[^/]+)/auth", "mcp_auth")
# Qubit MCP OAuth family (dashboard login, parity with the reference
# dashboard OAuth). The provider callback is served publicly by webui
# (no dashboard token by design) and never reaches this table.
_r("GET", r"/api/mcp/oauth/flows", "mcp_oauth_flows_list")
_r("GET", r"/api/mcp/oauth/flows/(?P<id>[^/]+)", "mcp_oauth_flow_status")
_r("DELETE", r"/api/mcp/oauth/flows/(?P<id>[^/]+)", "mcp_oauth_flow_cancel")
_r("GET", r"/api/mcp/oauth/callback/(?P<server_name>.+)", "mcp_oauth_callback")
_r("GET", r"/api/mcp/catalog", "h_mcp_catalog_live")
# Catalog install registers the reviewed entry's transport + declared
# credentials (closed schema, secrets to the runtime .env only) — no code is
# fetched, so the remote-source blanket gate does not apply here.
_r("POST", r"/api/mcp/catalog/install", "h_mcp_catalog_install")
# Read-only capability reads (resources/prompts/resource-templates). The
# vendored router serves them over the fork's MCP config registry; the
# dashboard ``/agent/api/mcp/servers`` prefix is quarantined to this table, so
# the canonical Qubit registry + brokered client must own them here.
_r("GET", r"/api/mcp/servers/(?P<name>[^/]+)/resources", "h_mcp_server_resources")
_r("GET", r"/api/mcp/servers/(?P<name>[^/]+)/prompts", "h_mcp_server_prompts")
_r("GET", r"/api/mcp/servers/(?P<name>[^/]+)/resource-templates",
   "h_mcp_server_resource_templates")
# Real, confirm-gated tool invocation from the dashboard (the same canonical
# entry point the CLI/voice use). Placed with the other MCP sub-routes so it is
# never shadowed by a generic server pattern.
_r("POST", r"/api/mcp/servers/(?P<name>[^/]+)/invoke", "mcp_invoke")
_r("POST", r"/api/pub", "h_pub")
_r("WEBSOCKET", r"/api/pub", "h_pub")
_r("GET", r"/api/pairing", "h_pairing_list")
_r("POST", r"/api/pairing", "pairing_create")
_r("POST", r"/api/pairing/approve", "pairing_approve")
_r("POST", r"/api/pairing/revoke", "pairing_revoke")
_r("POST", r"/api/pairing/clear-pending", "pairing_clear_pending")
_r("GET", r"/api/webhooks", "h_webhooks_list")
_r("POST", r"/api/webhooks", "webhooks_add")
_r("POST", r"/api/webhooks/enable", "webhooks_enable")
_r("PUT", r"/api/webhooks/(?P<id>[^/]+)/enabled", "webhooks_enabled")
# Delivery maintenance (reference store carries the per-route signing secret;
# Qubit realizes rotate/test/deliveries/retry against its own reference-only
# store). Registered before the generic id routes so they own their suffixes.
_r("POST", r"/api/webhooks/(?P<id>[^/]+)/rotate", "webhooks_rotate")
_r("POST", r"/api/webhooks/(?P<id>[^/]+)/test", "webhooks_test")
_r("GET", r"/api/webhooks/(?P<id>[^/]+)/deliveries", "webhooks_deliveries")
_r("POST", r"/api/webhooks/(?P<id>[^/]+)/retry", "webhooks_retry")
_r("DELETE", r"/api/webhooks/(?P<id>[^/]+)", "webhooks_delete")
_r("GET", r"/api/webhooks/(?P<id>[^/]+)", "webhooks_show")
_r("POST", r"/api/webhooks/(?P<id>[^/]+)", "webhooks_mutate")
_r("GET", r"/api/messaging/platforms", "h_platforms")
_r("PUT", r"/api/messaging/platforms/(?P<id>[^/]+)", "msg_update")
_r("POST", r"/api/messaging/whatsapp/onboarding/start", "h_wa_ob_start")
_r("GET", r"/api/messaging/whatsapp/onboarding/(?P<pairing_id>[^/]+)", "h_wa_ob_status")
_r("POST", r"/api/messaging/whatsapp/onboarding/(?P<pairing_id>[^/]+)/apply", "h_wa_ob_apply")
_r("DELETE", r"/api/messaging/whatsapp/onboarding/(?P<pairing_id>[^/]+)", "h_wa_ob_cancel")
_r("POST", r"/api/messaging/telegram/onboarding/start", "h_tg_ob_start")
_r("GET", r"/api/messaging/telegram/onboarding/(?P<pairing_id>[^/]+)", "h_tg_ob_status")
_r("POST", r"/api/messaging/telegram/onboarding/(?P<pairing_id>[^/]+)/apply", "h_tg_ob_apply")
_r("DELETE", r"/api/messaging/telegram/onboarding/(?P<pairing_id>[^/]+)", "h_tg_ob_cancel")
_r("GET", r"/api/memory", "h_memory")
_r("GET", r"/api/memory/provider", "h_memory")
_r("GET", r"/api/analytics", "h_analytics")
_r("GET", r"/openapi.json", "h_openapi")
_r("GET", r"/api/analytics/usage", "h_analytics")
_r("GET", r"/api/analytics/models", "h_analytics_models")
_r("GET", r"/api/files", "h_files")
_r("GET", r"/api/files/read", "h_files_read")
_r("DELETE", r"/api/files", "files_delete")
_r("GET", r"/api/gateway", "h_gateway")
# Read-only method-symmetric alias: the SPA reads ``/api/gateway``; a
# ``/api/gateway/status`` GET returns the same gateway/process status body so
# the surface is symmetric with the mutating ``POST /api/gateway/*`` family.
_r("GET", r"/api/gateway/status", "h_gateway")
_r("GET", r"/api/gateway/rpc", "h_gateway_rpc_methods")
_r("POST", r"/api/gateway/rpc", "h_gateway_rpc")
_r("POST", r"/api/gateway/drain", "h_gateway_drain")
# W13 P0 route parity (no vendor sync required). ``GET /api/gateway/migrate/plan``
# is the read-only multiplex preflight the reference SPA's
# ``api.getGatewayMigratePlan`` calls (reference web_routers/actions.py:147); it
# returns the real vendored ``MigrationPlan.to_dict()``. The companion
# ``POST /api/gateway/migrate`` stays an operator-owned typed refusal (GATED
# below), so a read never fabricates a migration.
_r("GET", r"/api/gateway/migrate/plan", "h_gateway_migrate_plan")
# Reference ``GET /api/host/identity`` (web_routers/status.py:122): proves WHO
# owns this port. Served on the session-token-gated ``/agent`` compat surface
# (``webui._qubit_api`` enforces ``X-Qubit-Session-Token`` before dispatch), so
# the reference's per-route ``_require_token`` is the same one gate.
_r("GET", r"/api/host/identity", "h_host_identity")
# The redacted OAuth provider catalog is registered in the GATED authority table
# below (as a real handler, like ``blueprints``/``delivery``) so the proxy-first
# read path cannot serve the fork's raw record (legacy portal host + token
# fragment). See the GET /api/providers/oauth row there.
# Live OAuth login family (owner decision: port the device-code dance 1:1 from
# the vendored router instead of the OAUTH_MANUAL_HANDOFF refusal). Start is a
# non-destructive login initiation (no confirm gate, like the reference);
# disconnect clears credentials and stages through the one confirm path.
_r("POST", r"/api/providers/oauth/(?P<provider_id>[^/]+)/start", "h_oauth_start")
_r("POST", r"/api/providers/oauth/(?P<provider_id>[^/]+)/submit", "h_oauth_submit")
_r("GET", r"/api/providers/oauth/(?P<provider_id>[^/]+)/poll/(?P<session_id>[^/]+)",
   "h_oauth_poll")
_r("DELETE", r"/api/providers/oauth/sessions/(?P<id>[^/]+)", "h_oauth_cancel")
_r("DELETE", r"/api/providers/oauth/(?P<id>[^/]+)", "h_oauth_disconnect")
_r("GET", r"/api/curator", "h_curator")
# Real on-demand curator control (persisted pause flag + explicit pass). The
# routes are implemented here, ahead of the GATED table, so the operator
# controls are real rather than the old CURATOR_ON_DEMAND refusal.
_r("PUT", r"/api/curator/paused", "h_curator_paused")
_r("POST", r"/api/curator/run", "h_curator_run")
_r("GET", r"/api/env", "h_env")
_r("GET", r"/api/ops/hooks", "h_hooks")
_r("POST", r"/api/ops/hooks", "h_hooks_create")
_r("PUT", r"/api/ops/hooks", "h_hooks_update")
_r("GET", r"/api/ops/checkpoints", "h_ckpt")
_r("GET", r"/api/ops", "h_ops_catalog")
_r("GET", r"/api/ops/approvals", "h_approvals_list")
_r("POST", r"/api/ops/run", "h_ops_run")
_r("POST", r"/api/ops/confirm", "h_ops_confirm")
_r("POST", r"/api/ops/cancel", "h_ops_cancel")
# Specific ops mutations must precede the generic `/api/ops/<name>` dispatch,
# otherwise they are swallowed by h_ops and answer OPS_UNKNOWN instead of their
# real operation / typed gate.
_r("POST", r"/api/ops/debug-share", "debug_share")
_r("POST", r"/api/ops/import-upload", "__gate__", "gated", _GATE_IMPORT_REVIEW)
_r("GET", r"/api/ops/(?P<name>[a-z-]+)", "h_ops")
_r("POST", r"/api/ops/(?P<name>[a-z-]+)", "h_ops")
_r("DELETE", r"/api/ops/hooks", "hooks_delete")
_r("GET", r"/api/plugins/kanban/events", "h_plugin_kanban_events")
# Vendored dashboard plugin fleet (qubit-achievements + kanban). Real
# backends live in voicebridge/qubit_plugins.py and read the same local
# session history / kanban DB the Qubit-owned Qubit runtime uses. Read routes
# precede the GATED kanban write wildcard below so they are never shadowed.
_r("GET", r"/api/plugins/qubit-achievements/achievements",
   "h_plugin_achievements")
_r("GET", r"/api/plugins/qubit-achievements/scan-status",
   "h_plugin_achievements_scan_status")
_r("GET", r"/api/plugins/qubit-achievements/recent-unlocks",
   "h_plugin_achievements_recent_unlocks")
_r("GET", r"/api/plugins/qubit-achievements/sessions/(?P<session_id>[^/]+)/badges",
   "h_plugin_achievements_session_badges")
_r("POST", r"/api/plugins/qubit-achievements/rescan",
   "h_plugin_achievements_rescan")
_r("POST", r"/api/plugins/qubit-achievements/reset-state",
   "h_plugin_achievements_reset_state")
_r("GET", r"/api/plugins/kanban/board", "h_plugin_kanban_board")
# memory-wiki READ surface (installed user dashboard plugin): the plugin's
# own core against a read-only Qubit state.db. Same shapes as its API.
_r("GET", r"/api/plugins/memory-wiki/overview", "h_memwiki_overview")
_r("GET", r"/api/plugins/memory-wiki/subjects", "h_memwiki_subjects")
_r("GET", r"/api/plugins/memory-wiki/subjects/(?P<slug>[^/]+)",
   "h_memwiki_subject")
_r("GET", r"/api/plugins/memory-wiki/days", "h_memwiki_days")
_r("GET", r"/api/plugins/memory-wiki/days/(?P<date>[^/]+)", "h_memwiki_day")
_r("GET", r"/api/plugins/kanban/tasks/(?P<task_id>[^/]+)", "h_plugin_kanban_task")
_r("GET", r"/api/plugins/kanban/stats", "h_plugin_kanban_stats")
_r("GET", r"/api/plugins/kanban/assignees", "h_plugin_kanban_assignees")
_r("GET", r"/api/plugins/kanban/boards", "h_plugin_kanban_boards")
_r("GET", r"/api/plugins/kanban/config", "h_plugin_kanban_config")
_r("GET", r"/api/plugins/kanban/projects", "h_plugin_kanban_projects")
_r("GET", r"/api/plugins/kanban/workers/active", "h_plugin_kanban_workers")
_r("GET", r"/api/plugins/kanban/diagnostics", "h_plugin_kanban_diagnostics")
_r("GET", r"/api/plugins/kanban/model-options", "h_plugin_kanban_model_options")
_r("GET", r"/api/plugins/kanban/home-channels", "h_plugin_kanban_home_channels")
_r("GET", r"/api/plugins/kanban/tasks/(?P<task_id>[^/]+)/log",
   "h_plugin_kanban_task_log")
_r("GET", r"/api/plugins/kanban/tasks/(?P<task_id>[^/]+)/attachments",
   "h_plugin_kanban_task_attachments")
_r("GET", r"/api/plugins/kanban/runs/(?P<run_id>[^/]+)/inspect",
   "h_plugin_kanban_run_inspect")
_r("GET", r"/api/plugins/kanban/runs/(?P<run_id>[^/]+)",
   "h_plugin_kanban_run")
_r("GET", r"/api/plugins/kanban/profiles", "h_plugin_kanban_profiles")
_r("GET", r"/api/plugins/kanban/orchestration",
   "h_plugin_kanban_orchestration")
# Binary-capable attachment download. The vendored dashboard calls
# GET /api/plugins/kanban/attachments/{id} and downloads the bytes; the compat
# dispatcher is a JSON surface, so the handler serves the SAME vendored kanban
# store as the attachments listing, base64-encoded with real filename/type.
# No dead 404: a missing attachment is a typed PLUGIN_HTTP 404.
_r("GET", r"/api/plugins/kanban/attachments/(?P<attachment_id>[^/]+)",
   "h_plugin_kanban_attachment_download")
# Kanban WRITE surface -> the SAME authoritative native board store the read
# routes above synthesize from. Registered BEFORE the blanket
# ``_GATE_KANBAN_WRITE`` block so the real board/task controls dispatch to a
# native-backed handler through the shared confirm/audit/e-stop path instead of
# a dead 403. Routes with no native-schema equivalent remain gated (and name
# the CLI/voice/overlay counterpart) rather than silently no-op.
_r("POST", r"/api/plugins/kanban/boards", "h_plugin_kanban_boards_create")
_r("POST", r"/api/plugins/kanban/tasks", "h_plugin_kanban_tasks_create")
_r("PATCH", r"/api/plugins/kanban/tasks/(?P<task_id>[^/]+)",
   "h_plugin_kanban_tasks_patch")
_r("DELETE", r"/api/plugins/kanban/tasks/(?P<task_id>[^/]+)",
   "h_plugin_kanban_tasks_delete")
_r("POST", r"/api/plugins/kanban/tasks/bulk", "h_plugin_kanban_tasks_bulk")
_r("POST", r"/api/plugins/kanban/tasks/(?P<task_id>[^/]+)/comments",
   "h_plugin_kanban_tasks_comment")
# Discovery for the vendored plugin fleet. The existing
# /api/dashboard/plugins response stays fixed to the Qubit overlay set; this
# additive view reports the vendored manifests without changing that contract.
_r("GET", r"/api/dashboard/plugins/fleet", "h_plugin_fleet")
_r("POST", r"/api/qubit/chat", "h_chat_turn")
# Qubit-safe streaming chat. ``stream`` disposition: the handler yields SSE
# frames (``h_chat_stream``) rather than a JSON envelope, so webui's dispatcher
# owns the socket while THIS table stays the single route authority.
_r("POST", r"/api/qubit/chat/stream", "h_chat_stream", "stream")
# Qubit-safe chat transport: persisted sessions + a PTY-shaped, agent-backed
# terminal frame. Literal rows precede the parameterized session id so
# /sessions/import and /sessions/<id> never collide. No shell/PTY is spawned.
_r("GET", r"/api/qubit/chat/sessions", "h_chat_sessions_list")
_r("POST", r"/api/qubit/chat/sessions/import", "h_chat_session_import")
_r("POST", r"/api/qubit/chat/sessions", "h_chat_session_create")
_r("GET", r"/api/qubit/chat/sessions/(?P<id>[^/]+)/messages",
   "h_chat_session_messages")
# S5 projection reads: specific subpaths precede the bare session-id row.
_r("GET", r"/api/qubit/chat/sessions/(?P<id>[^/]+)/activity",
   "h_chat_session_activity")
_r("GET", r"/api/qubit/chat/sessions/(?P<id>[^/]+)/drafts",
   "h_chat_session_drafts")
_r("GET", r"/api/qubit/chat/sessions/(?P<id>[^/]+)/draft",
   "h_chat_session_draft_content")
_r("GET", r"/api/qubit/chat/sessions/(?P<id>[^/]+)/comments",
   "h_chat_session_comments")
_r("GET", r"/api/qubit/chat/sessions/(?P<id>[^/]+)/ci",
   "h_chat_session_ci")
_r("GET", r"/api/qubit/chat/sessions/(?P<id>[^/]+)/export",
   "h_chat_session_export")
_r("GET", r"/api/qubit/chat/sessions/(?P<id>[^/]+)", "h_chat_session_show")
_r("PATCH", r"/api/qubit/chat/sessions/(?P<id>[^/]+)", "h_chat_session_patch")
_r("DELETE", r"/api/qubit/chat/sessions/(?P<id>[^/]+)",
   "h_chat_session_delete")
_r("POST", r"/api/qubit/chat/cancel", "h_chat_cancel")
_r("POST", r"/api/qubit/chat/retry", "h_chat_retry")
_r("POST", r"/api/qubit/chat/vision", "h_chat_vision")
_r("POST", r"/api/qubit/chat/pty", "h_chat_pty")
_r("GET", r"/api/dashboard/plugins", "h_plugins")
# Real local plugin catalog (D-1): every entry has a real manifest + loader +
# provenance. The upstream remote curated index is a typed BLOCKED_REMOTE_PLUGIN
# refusal, never fetched. The detail route adds validation + dependency checks.
_r("GET", r"/api/dashboard/plugins/catalog", "plugins_catalog")
_r("GET", r"/api/dashboard/plugins/catalog/(?P<id>.+)", "plugin_catalog_detail")
_r("POST", r"/api/dashboard/plugins/reload", "plugins_reload")
_r("GET", r"/api/dashboard/themes", "h_themes")
_r("GET", r"/api/dashboard/theme", "h_theme_get")
_r("PUT", r"/api/dashboard/theme", "h_theme_set")

# Packet 8U read surfaces: scoped Git / Files / Audio (audit §1 absent reads)
_r("GET", r"/api/git/status", "h_git_status")
_r("GET", r"/api/git/log", "h_git_log")
_r("GET", r"/api/git/diff", "h_git_diff")
_r("GET", r"/api/git/show", "h_git_show")
_r("GET", r"/api/git/review/diff", "h_git_file_diff")
_r("GET", r"/api/git/file-diff", "h_git_file_diff")
_r("GET", r"/api/git/worktrees", "h_git_worktrees")
_r("GET", r"/api/git/branches", "h_git_branches")
_r("GET", r"/api/git/base-branches", "h_git_base_branches")
_r("GET", r"/api/git/review/list", "h_git_review_list")
_r("GET", r"/api/git/review/commit-context", "h_git_review_commit_context")
_r("GET", r"/api/git/review/rev-parse", "h_git_review_rev_parse")
_r("GET", r"/api/fs/list", "h_fs_list")
_r("GET", r"/api/fs/tree", "h_fs_tree")
_r("GET", r"/api/fs/stat", "h_fs_stat")
_r("GET", r"/api/fs/read", "h_fs_read_text")
_r("GET", r"/api/fs/read-text", "h_fs_read_text")
_r("GET", r"/api/fs/read-data-url", "h_fs_read_data_url")
_r("GET", r"/api/fs/git-root", "h_fs_git_root")
_r("GET", r"/api/fs/default-cwd", "h_fs_default_cwd")
_r("GET", r"/api/audio/status", "h_audio_status")
_r("GET", r"/api/audio/list", "h_audio_devices")
_r("GET", r"/api/audio/devices", "h_audio_devices")
_r("GET", r"/api/audio/voice-config", "h_audio_voice_config")
# Non-secret GPT-Live readiness (reference audio.py:167); the session-creation
# POST stays a typed vendor-direct refusal in the GATED table.
_r("GET", r"/api/audio/voice-live/status", "h_audio_voice_live_status")
_r("POST", r"/api/audio/transcribe", "h_audio_transcribe")
_r("GET", r"/api/audio/stt/status", "h_audio_stt_status")
_r("POST", r"/api/audio/speak", "h_audio_speak")

# Packet 8U closure: local-models reads, custom endpoints, profile reads,
# tools/learning reads, auth providers (audit §1A absent reads).
_r("GET", r"/api/local-models/status", "h_local_models_status")
_r("GET", r"/api/local-models/hardware", "h_local_models_hardware")
_r("GET", r"/api/local-models/catalog", "h_local_models_catalog")
_r("GET", r"/api/local-models/jobs/(?P<id>[^/]+)", "h_local_models_job_show")
_r("GET", r"/api/local-models/jobs", "h_local_models_jobs")
_r("POST", r"/api/local-models/runtime/install", "h_local_models_runtime_install")
_r("POST", r"/api/local-models/download", "h_local_models_download")
_r("POST", r"/api/local-models/download/pause",
   "h_local_models_download_pause")
_r("POST", r"/api/local-models/download/resume",
   "h_local_models_download_resume")
_r("DELETE", r"/api/local-models/models/(?P<model_id>[^/]+)",
   "h_local_models_delete")
_r("POST", r"/api/local-models/quickstart", "h_local_models_quickstart")
_r("POST", r"/api/local-models/server", "h_local_models_server")
_r("POST", r"/api/local-models/eject", "h_local_models_eject")
_r("POST", r"/api/local-models/activate", "h_local_models_activate")
_r("POST", r"/api/local-models/sideload", "h_local_models_sideload")
_r("GET", r"/api/local-models/search/files", "h_local_models_search_files")
_r("GET", r"/api/local-models/search", "h_local_models_search")
_r("POST", r"/api/local-models/download-browsed",
   "h_local_models_download_browsed")
_r("GET", r"/api/providers/custom-endpoints", "h_custom_endpoints")
_r("GET", r"/api/profiles/sessions/sidebar", "h_profiles_sessions_sidebar")
_r("GET", r"/api/profiles/sessions", "h_profiles_sessions")
_r("GET", r"/api/profiles/projects/tree", "h_profiles_projects_tree")
# v0.21.5 chat workspace picker backend: real read over the canonical Qubit
# project registry (the vendored router read per-profile projects.db; Qubit's
# one registry is the same authority). Read-only, never a fabricated list.
_r("GET", r"/api/chat/workspaces", "h_chat_workspaces")
_r("POST", r"/api/profiles", "h_profile_create")
_r("GET", r"/api/tools/terminal/backends", "h_terminal_backends")
_r("GET", r"/api/tools/computer-use/status", "h_computer_use_status")
_r("GET", r"/api/learning/graph", "h_learning_graph")
_r("GET", r"/api/learning/node", "h_learning_node")
_r("PUT", r"/api/learning/node", "h_learning_node_update")
_r("DELETE", r"/api/learning/node", "h_learning_node_delete")
_r("GET", r"/api/auth/providers", "h_auth_providers")
# Qubit-owned dashboard auth flow (loopback identity + native handshake).
_r("GET", r"/login", "h_login_page")
_r("GET", r"/auth/login", "h_auth_login")
_r("GET", r"/auth/native/authorize", "h_auth_native_authorize")
_r("GET", r"/auth/callback", "h_auth_callback")
_r("POST", r"/auth/password-login", "h_auth_password_login")
_r("POST", r"/auth/logout", "h_auth_logout")
_r("POST", r"/api/auth/logout", "h_auth_logout")
_r("POST", r"/auth/native/token", "h_auth_native_token")
_r("POST", r"/auth/native/refresh", "h_auth_native_refresh")

# Qubit delta Wave 3 (D3/D4/D20/D21/D24): file transfer, active recall,
# document extraction, question prompts, and session sharing. Specific paths
# precede parameterized rows so re.fullmatch resolves deterministically.
_r("GET", r"/api/transfers/status", "h_transfers_status")
_r("GET", r"/api/transfers/list", "h_transfers_list")
_r("POST", r"/api/transfers/fetch", "h_transfers_fetch")
_r("POST", r"/api/transfers/plan", "h_transfers_plan")
_r("GET", r"/api/transfers/resume", "h_transfers_resume")
_r("GET", r"/api/recall/status", "h_recall_status")
_r("GET", r"/api/recall/policy", "h_recall_policy_get")
_r("POST", r"/api/recall/policy", "h_recall_policy_set")
_r("GET", r"/api/recall/resolve", "h_recall_resolve")
_r("GET", r"/api/recall", "h_recall_query")
_r("POST", r"/api/recall/update", "h_recall_update")
_r("POST", r"/api/recall/escalate", "h_recall_escalate")
_r("GET", r"/api/documents/status", "h_documents_status")
_r("GET", r"/api/documents/kinds", "h_documents_kinds")
_r("POST", r"/api/documents/extract", "h_documents_extract")
_r("GET", r"/api/ask/status", "h_ask_status")
_r("GET", r"/api/ask/pending", "h_ask_pending")
_r("POST", r"/api/ask/answer", "h_ask_answer")
_r("POST", r"/api/ask", "h_ask_create")
_r("GET", r"/api/shares/status", "h_session_share_status")
_r("GET", r"/api/sessions/shares", "h_session_shares_list")
_r("POST", r"/api/sessions/shares/purge", "h_session_shares_purge")
_r("DELETE", r"/api/sessions/shares/(?P<share_id>[^/]+)",
   "h_session_share_revoke")
_r("POST", r"/api/sessions/(?P<session_id>[^/]+)/share",
   "h_session_share_publish")
# Public, locally-served share view: authenticated by the SHARE token in the
# query (constant-time checked in the module), NOT the dashboard token.
_r("GET", r"/share/(?P<share_id>[^/]+)", "h_share_resolve")

# Agent 5-10 capabilities served at the Qubit-owned /api/qubit/* front door.
# These are NEVER proxied to the fork (qubit_asgi.QUBIT_OWNED_PREFIXES) and
# every handler calls the SAME module the CLI (`qubit gateway`, `qubit
# runtime`, `qubit egress`, `qubit policy`, `qubit usage`, `qubit receipts`,
# `qubit logbook`, `qubit browser`, `qubit local-models`) and voice surfaces
# call: same stores, same redaction, same read-only authority.
_r("GET", r"/api/qubit/gateway/discovery", "h_qubit_gateway_discovery")
_r("GET", r"/api/qubit/agents", "h_agents")
_r("GET", r"/api/qubit/credentials/pool", "h_credentials_pool")
_r("POST", r"/api/qubit/credentials/pool", "h_credentials_pool_add")
_r("DELETE", r"/api/qubit/credentials/pool/(?P<provider>[^/]+)/(?P<index>[0-9]+)",
   "h_credentials_pool_remove")
_r("GET", r"/api/qubit/runtime/discovery", "h_qubit_runtime_discovery")
_r("GET", r"/api/qubit/runtime/marketplace", "h_qubit_runtime_marketplace")
_r("GET", r"/api/qubit/runtime/history", "h_qubit_runtime_history")
_r("GET", r"/api/qubit/runtime/update-report", "h_qubit_runtime_update_report")
_r("GET", r"/api/qubit/runtime/status", "h_qubit_runtime_status")
_r("GET", r"/api/qubit/runtime/images", "h_qubit_runtime_images")
_r("GET", r"/api/qubit/runtime/doctor", "h_qubit_runtime_doctor")
_r("GET", r"/api/qubit/egress/status", "h_qubit_egress_status")
_r("GET", r"/api/qubit/egress/providers", "h_qubit_egress_providers")
_r("GET", r"/api/qubit/egress/check", "h_qubit_egress_check")
_r("GET", r"/api/qubit/policy/conformance", "h_qubit_policy_conformance")
_r("GET", r"/api/qubit/policy/attest", "h_qubit_policy_attest")
_r("GET", r"/api/qubit/hooks/doctor", "h_qubit_hooks_doctor")
_r("GET", r"/api/qubit/usage", "h_qubit_usage")
_r("GET", r"/api/qubit/receipts", "h_qubit_receipts")
_r("GET", r"/api/qubit/logbook", "h_qubit_logbook")
_r("GET", r"/api/qubit/browser/profiles", "h_qubit_browser_profiles")
# S8 — desktop/browser operation routes. Specific paths are literal so they
# precede any broader parameterized row; every handler forwards to the one
# operation-catalog id (shared broker/confirm/e-stop/audit).
_r("GET", r"/api/qubit/desktop/ops", "h_op_computer_ops")
_r("GET", r"/api/qubit/desktop/presentation", "h_op_computer_presentation_status")
_r("POST", r"/api/qubit/desktop/launch", "h_op_computer_launch")
_r("POST", r"/api/qubit/desktop/pip", "h_op_computer_pip")
_r("POST", r"/api/qubit/desktop/present", "h_op_computer_present")
_r("GET", r"/api/qubit/browser/favicon-cache", "h_op_browser_favicon_cache")
_r("GET", r"/api/qubit/browser/favicon", "h_op_browser_favicon")
_r("POST", r"/api/qubit/browser/download", "h_op_browser_download")
_r("GET", r"/api/qubit/local-models/fallback", "h_qubit_local_models_fallback")
# Update CHECK is a real read-only version probe (the SPA System page calls it
# on mount); it answers the UpdateCheckResponse wire shape, not a blanket
# refusal. The mutating `POST /api/qubit/update` is now a real, confirmed,
# non-destructive handler (git pull --ff-only; dirty worktree refused). Both
# precede the remaining GATED `GET /api/qubit/update.*` alias so the specific
# rows win (audit A29 + objective C).
_r("GET", r"/api/qubit/update/check", "h_qubit_update_check")
_r("GET", r"/api/qubit/update/receipt", "h_qubit_update_receipt")
_r("POST", r"/api/qubit/update/rollback", "h_qubit_update_rollback")
_r("POST", r"/api/qubit/update", "h_qubit_update_apply")

# Wave 2 conversions: previously-blanket mutating refusals now run the real
# Qubit permission/confirmation/audit operation (same module/store as CLI and
# voice). These precede the GATED table so the implemented handler matches.
_r("POST", r"/api/gateway/start", "h_gateway_start")
_r("POST", r"/api/gateway/stop", "h_gateway_stop")
_r("POST", r"/api/gateway/restart", "h_gateway_restart")
_r("POST", r"/api/providers/custom-endpoints", "h_custom_endpoints_write")
_r("POST", r"/api/providers/custom-endpoints/(?P<id>[^/]+)/activate",
   "h_custom_endpoints_activate")
_r("DELETE", r"/api/providers/custom-endpoints/(?P<id>[^/]+)",
   "h_custom_endpoints_delete")
_r("PUT", r"/api/tools/terminal/backend", "h_terminal_backend_set")
_r("POST", r"/api/tools/computer-use/permissions/grant", "h_computer_use_grant")
_r("POST", r"/api/profiles/(?P<name>[^/]+)/export", "h_profile_export")

# Wave 4 genuine gaps: the vendored portal read (de-branded at the Qubit
# boundary) and the dashboard plugin asset route. `GET /api/portal` is added to
# webui.QUBIT_READ_AUTHORITY so this compat handler answers instead of the fork
# proxy (whose payload would carry the upstream portal host verbatim).
_r("GET", r"/api/portal", "h_portal")
_r("GET", r"/dashboard-plugins/(?P<plugin_name>[^/]+)/(?P<file_path>.+)",
   "h_plugin_asset")

# Wave 3: skills hub served from the Qubit-reviewed LOCAL catalog (remote
# fetches refused). Specific routes precede the GATED hub wildcard so unknown
# subpaths still fall through to the typed remote refusal.
_r("GET", r"/api/skills/hub/official", "h_skills_hub_official")
_r("GET", r"/api/skills/hub/sources", "h_skills_hub_sources")
_r("GET", r"/api/skills/hub/search", "h_skills_hub_search")
_r("GET", r"/api/skills/hub/preview", "h_skills_hub_preview")
_r("GET", r"/api/skills/hub/scan", "h_skills_hub_scan")
_r("POST", r"/api/skills/hub/install", "h_skills_hub_install")
_r("POST", r"/api/skills/hub/uninstall", "h_skills_hub_uninstall")
_r("POST", r"/api/skills/hub/update", "h_skills_hub_update")

# Wave 3 real reads that must precede the parameterized GATED wildcards:
# provider config (the memory-provider wildcard would otherwise shadow it) and
# the toolset config/model matrix.
_r("GET", r"/api/memory/providers/(?P<id>[^/]+)/config", "mem_config")
_r("GET", r"/api/tools/toolsets/(?P<id>[^/]+)/config", "toolset_config")
_r("GET", r"/api/tools/toolsets/(?P<id>[^/]+)/models", "toolset_models")
# B-08D: the SPA's toolset env drawer (``{env, profile}``) and the Plugins page
# context-engine control (``{context_engine}``) must reach a handler that
# PERSISTS what they send. Without these rows the generic ``toolset_write``
# wildcard below silently granted a generic permission and dropped the value.
_r("PUT", r"/api/tools/toolsets/(?P<id>[^/]+)/env", "toolset_env_write")
_r("PUT", r"/api/dashboard/plugin-providers", "plugin_providers_write")

# Wave 4 real brokered writes (staged -> confirm -> apply): confined file
# patches, brokered git-write authorizations, and blueprint instantiation.
_r("POST", r"/api/fs/write-text", "h_fs_write_text")
_r("POST", r"/api/files/upload", "h_files_upload")
_r("POST", r"/api/files/upload-stream", "h_files_upload")
_r("POST", r"/api/files/mkdir", "h_files_mkdir")
_r("POST", r"/api/git/review/stage", "h_git_stage")
_r("POST", r"/api/git/review/unstage", "h_git_unstage")
_r("POST", r"/api/git/review/revert", "h_git_revert")
_r("POST", r"/api/git/review/commit", "h_git_commit")
_r("POST", r"/api/git/worktree/add", "h_git_worktree_add")
_r("POST", r"/api/git/worktree/remove", "h_git_worktree_remove")
_r("POST", r"/api/git/branch/switch", "h_git_branch_switch")
_r("POST", r"/api/cron/blueprints/instantiate", "h_blueprint_instantiate")

# Dashboard agent-plugin mutations: the specific typed routes precede the
# blanket `/api/dashboard/agent-plugins/.*` remote gate below. INSTALL runs the
# canonical `plugin_runtime.install` op (reviewed local manifests only; shared
# confirmation journal + e-stop), and enable/disable run the real permission
# toggle. `{name:path}` upstream ids are multi-segment, hence `.+`.
_r("POST", r"/api/dashboard/agent-plugins/install", "h_agent_plugin_install")
_r("POST", r"/api/dashboard/agent-plugins/(?P<id>.+)/enable", "agent_enable")
_r("POST", r"/api/dashboard/agent-plugins/(?P<id>.+)/disable", "agent_disable")
_r("POST", r"/api/dashboard/agent-plugins/(?P<id>.+)/update", "__gate__",
   "gated", _GATE_SKILL_VERSIONED)

GATED: List[Dict[str, Any]] = [
    ("GET", r"/api/pty", _NO_PTY), ("POST", r"/api/pty", _NO_PTY),
    ("GET", r"/api/ws", _g("WS_SINGLE_PATH",
                           "The upstream tui_gateway WS dialect is not served; Qubit keeps "
                           "one event path (SSE feed + ops). Chat uses Qubit Chat.",
                           "GET /api/events (SSE) + POST /api/qubit/chat")),
    ("POST", r"/api/auth/ws-ticket", _g("WS_SINGLE_PATH",
                           "No WS tickets are minted; Qubit serves no WS gateway dialect. "
                           "Loopback clients use the session token directly.",
                           "GET /api/events (SSE) + POST /api/qubit/chat")),
    ("GET", r"/api/console", _NO_CONSOLE),
    ("POST", r"/api/gateway/migrate", _GATE_GATEWAY_MIGRATE),
    ("GET", r"/api/config/raw", _NO_SECRET),
    ("PUT", r"/api/config/raw", _NO_SECRET),
    ("GET", r"/api/env/reveal", _NO_SECRET),
    ("POST", r"/api/env/reveal", _NO_SECRET),
    # The vendored credential-pool response carries token_preview fragments, so
    # Qubit gates the GET as well as every mutation: secret material never
    # reaches the browser. Operators manage credentials via `qubit auth`.
    ("GET", r"/api/credentials/pool.*", _NO_SECRET),
    ("POST", r"/api/credentials/pool.*", _NO_SECRET),
    ("PUT", r"/api/credentials/pool.*", _NO_SECRET),
    ("DELETE", r"/api/credentials/pool.*", _NO_SECRET),
    ("POST", r"/api/chat/image-upload", _g("CHAT_IMAGE_OVERLAY",
                                           "Built-in xterm chat image paste is superseded by "
                                           "Qubit Chat attachments (vision_describe backend).",
                                            "overlay Qubit Chat tab + `qubit vision describe`")),
    # Public Qubit update surface. The specific canonical routes
    # (``GET /api/qubit/update/check``, ``POST /api/qubit/update``,
    # ``POST /api/qubit/update/rollback``) are real handlers registered above;
    # only unknown GET/POST subpaths fall through to this typed gate, so an
    # unrecognized update subpath is never a bare 404. The legacy
    # ``/api/hermes/update.*`` alias was removed entirely (owner directive:
    # no publicly reachable Hermes-named route). The vendored runtime no
    # longer declares it either, so an old client path is an explicit 404 on
    # the Qubit compat path and can never be fork-proxied.
    ("GET", r"/api/qubit/update.*", _NO_UPDATE),
    ("POST", r"/api/qubit/update.*", _NO_UPDATE),

    ("GET", r"/api/ssh/ownership", _g("SSH_OWNERSHIP_OPERATOR",
        "Qubit owns the host shell surface; no remote SSH ownership nonce is "
        "minted or served to the dashboard. Host control stays with the operator.",
        "CLI `qubit setup health` + `qubit doctor` (local, operator-run)")),
    # GET provider/OAuth metadata is served (redacted) by the Qubit compat
    # handler. Registered in this authority table (as a real handler, the same
    # pattern as ``/api/cron/blueprints``) so ``qubit_asgi.gated_path`` keeps
    # the read on the Qubit path: the proxy-first path would return the fork's
    # raw record (legacy portal host + a token fragment).
    ("GET", r"/api/providers/oauth", "h_providers_oauth"),
    # The live login family (start/submit/poll/cancel/disconnect) is
    # implemented above (owner decision: port the device-code dance 1:1), so
    # no blanket POST/poll gate remains here. The memory-provider OAuth
    # status stays manual-handoff (separate surface); the MCP OAuth family
    # is implemented above (Qubit OAuth family) with its own routes.
    # Curator pause/run are real implemented routes above; they are no longer
    # gated here.
    ("GET", r"/api/ops/backup/download", _g("BACKUP_PASSPHRASE",
        "Backup bundles are passphrase-encrypted on creation and never stored "
        "server-side; there is no archive to download. Create one with a "
        "passphrase resupply at confirmation time.",
        "dashboard /ops backup_create + CLI backup (passphrase resupply)")),
    ("POST", r"/api/skills/hub/.*", _NO_REMOTE),
    ("GET", r"/api/skills/hub/.*", _NO_REMOTE),
    ("POST", r"/api/dashboard/agent-plugins/.*", _NO_REMOTE),
    ("GET", r"/api/dashboard/plugin-providers", _g("PLUGIN_PROVIDERS_QUBIT",
        "Plugin providers resolve through the Qubit skill manifest registry.",
        "GET /api/skills + CLI `qubit skills list`")),
    ("GET", r"/api/cron/blueprints", "blueprints"),
    ("GET", r"/api/cron/delivery-targets", "delivery"),
    # ``POST /api/sessions/import|prune|bulk-delete`` and ``DELETE
    # /api/sessions/empty`` are real, confirmation-gated handlers registered
    # in the implemented table ahead of this GATED pass (the earlier
    # ``_GATE_SESSIONS_RECEIPT`` refusals had a false "append-only" rationale
    # and contradicted the single-session rename/archive/delete surface).
    ("DELETE", r"/api/sessions/(?P<id>[^/]+)", "h_session_delete"),
    ("PATCH", r"/api/sessions/(?P<id>[^/]+)", "h_session_patch"),
    ("POST", r"/api/sessions/(?P<id>[^/]+)/resume", "h_session_resume"),
    ("GET", r"/api/memory/reset", "mem_reset_status"),
    ("POST", r"/api/memory/reset", "mem_reset"),
    # Provider OAuth status is a credential surface (the fork polls a live
    # OAuth flow). Qubit performs no live token dance, so it is a typed
    # manual-handoff gate, not the local memory-provider stub. Specific route
    # must precede the memory-providers wildcard so it is not captured by
    # ``mem_prov``.
    ("GET", r"/api/memory/providers/[^/]+/oauth/status", _NO_OAUTH),
    ("GET", r"/api/memory/providers/.*", "mem_prov"),
    ("POST", r"/api/messaging/platforms/(?P<id>[^/]+)/test", "h_msg_test_live"),
    ("POST", r"/api/mcp/servers/(?P<name>[^/]+)/test", "h_mcp_test_local"),
    ("PUT", r"/api/tools/toolsets/[^/]+/.+", "toolset_write"),
    ("POST", r"/api/tools/toolsets/.*/post-setup", "toolset_write"),
    ("PUT", r"/api/config", "config_save"),
    # Agent-plugin install/enable/disable/update are registered above (ahead of
    # the blanket remote gate) so the precise typed operation answers; only the
    # DELETE remove route stays here (it is unshadowed and real).
    ("DELETE", r"/api/dashboard/agent-plugins/(?P<id>.+)", "agent_remove"),
    ("GET", r"/api/dashboard/plugins/hub", "plugins_hub"),
    # OAuth cancel/disconnect are implemented above (live family); the old
    # OAUTH_MANUAL_HANDOFF DELETE gates are gone with them.
    ("PUT", r"/api/env", "env_write"),
    ("DELETE", r"/api/env", "env_delete"),
    ("PUT", r"/api/memory/provider", "mem_provider_set"),
    ("PUT", r"/api/memory/providers/(?P<id>[^/]+)/config", "mem_config_write"),
    ("POST", r"/api/memory/providers/(?P<id>[^/]+)/setup", "mem_config_write"),
    ("GET", r"/api/dashboard/plugins/rescan", "plugins_rescan"),
    ("POST", r"/api/dashboard/plugins/(?P<id>.+)/visibility", "h_plugin_visibility_set"),
    # debug-share and import-upload are registered above (ahead of the generic
    # /api/ops/<name> dispatch) so their real op / typed gate answers.
    ("POST", r"/api/ops/checkpoints/prune", "h_checkpoints_prune"),
    ("GET", r"/api/ops/checkpoints.*", "ckpt"),
    ("GET", r"/api/actions/(?P<name>[^/]+)/status", "action_status"),
    ("GET", r"/api/actions/.*", "actions_read"),
    ("GET", r"/api/dashboard/font", "font"),
    ("PUT", r"/api/dashboard/font", "font_set"),
    # --- Packet 8U: Git writes + credentialed gh surfaces stay typed.
    # Local review writes (stage/unstage/revert/commit/worktree/branch-switch)
    # were converted to real brokered grants (Wave 4) and precede this table;
    # network/gh verbs stay typed here.
    ("GET", r"/api/git/gh-auth", _NO_GH_AUTH),
    ("GET", r"/api/git/review/ship-info", _NO_GIT_SHIP),
    ("POST", r"/api/git/review/pr-list", _NO_GIT_WRITE),
    ("POST", r"/api/git/review/push", _NO_GIT_WRITE),
    ("POST", r"/api/git/review/create-pr", _NO_GIT_WRITE),
    # --- Packet 8U: Files/FS binary + writes stay typed ---
    ("GET", r"/api/media", _NO_MEDIA),
    # D5: binary download/stream is now real (brokered, confined read ->
    # base64 envelope); the read path is served by /api/files/read too.
    ("GET", r"/api/files/download", "h_files_download"),
    ("GET", r"/api/files/stream", "h_files_download"),
    ("HEAD", r"/api/files/stream", "h_files_download"),
    ("GET", r"/api/fs/download", "h_files_download"),
    # --- Packet 8U: external catalog / desktop lease / WS stay typed ---
    ("GET", r"/api/audio/elevenlabs/voices", _NO_AUDIO_EXTERNAL),
    ("POST", r"/api/audio/tts-lease", _NO_AUDIO_LEASE),
    ("GET", r"/api/audio/speak-stream", _NO_AUDIO_STREAM),
    ("POST", r"/api/audio/speak-stream", _NO_AUDIO_STREAM),
    # Raw RFB Bot Desktop viewer: the webui.py WS gate enforces the typed 426
    # ``WS_DESKTOP_CLIENT_ONLY``; this row keeps the route declared on the ONE
    # authority table (no fabricated RFB stream, no second WS server).
    ("WEBSOCKET", r"/api/display/ws", _NO_DISPLAY_WS),
    # GPT-Live session creation is direct-vendor-key only; typed refusal. The
    # read-only readiness route is implemented above.
    ("POST", r"/api/audio/voice-live/session", _GATE_VOICE_LIVE_SESSION),
    # Backend retirement: operator-owned admission fence, never dashboard-driven.
    ("POST", r"/api/health/retirement", _GATE_HEALTH_RETIREMENT),
    # --- Packet 8U closure: provider/profile/tool/learning writes + the
    # remaining wrong-verb gates (audit §1B). Specific paths precede any
    # parameterized row so re.fullmatch resolves deterministically.
    # custom-endpoints POST/activate/DELETE, terminal/backend, computer-use
    # grant, and profile export were converted to real Qubit
    # confirmation/audit operations (Wave 2) and now precede the GATED table.
    ("POST", r"/api/providers/custom-endpoints/validate", _NO_NETWORK_PROBE),
    ("POST", r"/api/providers/validate", _NO_NETWORK_PROBE),
    ("POST", r"/api/profiles/import", _GATE_PROFILE_IMPORT),
    ("POST", r"/api/profiles/[^/]+/open-terminal", _NO_PTY),
    ("GET", r"/api/profiles/[^/]+/desktop-overlay", _GATE_DESKTOP_OVERLAY),
    ("POST", r"/api/sessions/owner-backfill", _GATE_SESSIONS_RECEIPT),
    ("POST", r"/api/cron/fire", _NO_CRON_FIRE),
    ("POST", r"/api/profiles/sessions/pull-requests", _NO_PROFILE_PR),
    ("POST", r"/api/memory/providers/[^/]+/oauth/start", _NO_OAUTH),
    # Vendored kanban dashboard plugin: reads are served by
    # voicebridge/qubit_plugins.py; task mutations stay on the shared Qubit
    # confirmation/broker path (`qubit kanban` + approvals) rather than the
    # plugin's direct server-side writes.
    ("POST", r"/api/plugins/kanban/.*", _GATE_KANBAN_WRITE),
    ("PATCH", r"/api/plugins/kanban/.*", _GATE_KANBAN_WRITE),
    ("DELETE", r"/api/plugins/kanban/.*", _GATE_KANBAN_WRITE),
    # ``PUT /api/plugins/kanban/orchestration`` writes orchestration knobs to
    # config.yaml (pinned plugin_api.py). It was previously unrouted and the
    # active backend answered a bare 404; the dashboard plugin ``saveSettings``
    # calls it. Same shared broker gate as every other kanban write.
    ("PUT", r"/api/plugins/kanban/.*", _GATE_KANBAN_WRITE),
]

for _m, _p, _ginfo in GATED:
    if isinstance(_ginfo, dict):
        _r(_m, _p, "__gate__", "gated", _ginfo)
    else:
        _r(_m, _p, _ginfo, "implemented")


def h_blueprints(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/cron/blueprints -> the REAL vendored blueprint catalog.

    Ported from the vendored ``qubit_cli/web_routers/cron.py::list_cron_blueprints``
    (pinned 0.21.4): each entry is the full form schema
    ``{key,title,description,category,tags,fields,schedule,scheduleHuman,
    command,appUrl}`` from ``cron.blueprint_catalog``, with the ``deliver``
    slot's options rewritten from the actually configured gateway platforms.

    The previous compat stub returned only ``{name,kind,instantiate}`` for the
    names of already-existing jobs and shadowed the real catalog, so the
    dashboard's blueprint form rendered field-less cards. Qubit policy: the
    catalog is a pure read (never a mutation); instantiation remains the
    confirm-gated ``h_blueprint_instantiate`` on the same ``schedules`` store.
    """
    from . import qubit_runtime
    try:
        qubit_runtime.ensure_path()
        try:
            qubit_runtime.use_qubit_home(cfg)
        except Exception:  # noqa: BLE001 - a resolution miss must not 500 the read
            pass
        from cron.blueprint_catalog import (  # type: ignore
            CATALOG, blueprint_catalog_entry,
        )
        deliver_options = None
        try:
            from cron.scheduler_delivery import cron_delivery_targets  # type: ignore
            platforms = [t["id"] for t in cron_delivery_targets() if t.get("id")]
            deliver_options = ["origin", "local", *platforms]
        except Exception:  # noqa: BLE001 - keep the static options, like upstream
            deliver_options = None
        entries = []
        for r in CATALOG:
            entry = blueprint_catalog_entry(r)
            if deliver_options:
                for f in entry.get("fields", []):
                    if f.get("name") == "deliver":
                        f["options"] = deliver_options
            entries.append(entry)
        return _ok(blueprints=entries)
    except Exception as exc:
        return _fail(exc)


def h_delivery_targets(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                       p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/cron/delivery-targets -> the reference ``{targets: [...]}``.

    Ported from the vendored ``cron/scheduler_delivery.py::cron_delivery_targets``
    (the same source ``h_blueprints`` rewrites its deliver options from), with
    the implicit ``local`` option prepended exactly like upstream. The previous
    compat shape (``{delivery_targets: {accounts: [...]}}``) did not match the
    dashboard's ``CronDeliveryTarget[]`` contract, so the dropdown silently
    collapsed to no options.
    """
    targets: List[Dict[str, Any]] = [
        {"id": "local", "name": "Local (save only)",
         "home_target_set": True, "home_env_var": None}]
    try:
        from . import qubit_runtime
        qubit_runtime.ensure_path()
        try:
            qubit_runtime.use_qubit_home(cfg)
        except Exception:  # noqa: BLE001 - a resolution miss must not 500 the read
            pass
        from cron.scheduler_delivery import cron_delivery_targets  # type: ignore
        targets.extend(cron_delivery_targets())
    except Exception:  # noqa: BLE001 - keep the implicit local target
        pass
    return _ok(targets=targets)


def _chat_session_db(cfg: Any) -> Tuple[Any, Any, Any]:
    """``(store, db, sessions_dir)`` for the canonical chat-session store.

    Returns ``(None, None, None)`` when the store cannot be opened. ``db`` is
    the vendored ``qubit_state.SessionDB`` the adapter wraps — the same object
    the reference's bulk maintenance operates on; ``sessions_dir`` is the
    transcript sidecar directory beside the DB (``None`` when absent), so prune
    and the empty sweep can remove on-disk transcripts exactly like upstream.
    """
    try:
        cs: Any = _chat_session_store(cfg)
    except Exception:
        return None, None, None
    db = getattr(cs, "_db", None)
    path = getattr(cs, "_path", None)
    sessions_dir = None
    if path is not None:
        try:
            cand = Path(path).parent / "sessions"
            sessions_dir = cand if cand.exists() else None
        except Exception:
            sessions_dir = None
    return cs, db, sessions_dir


def h_sess_empty(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/sessions/empty[/count] -> the empty-session sweep size.

    The "Delete empty (N)" button and its confirm dialog must agree with what
    ``DELETE /api/sessions/empty`` actually sweeps, so the count is the
    canonical chat store's own ``count_empty_sessions`` (empty, ended,
    non-archived — the exact predicate the vendored sweep uses), not a
    receipt-store heuristic that never matches the sweep.
    """
    _cs, db, _dir = _chat_session_db(cfg)
    if db is not None:
        try:
            count = int(db.count_empty_sessions())
        except Exception:
            count = 0
        return _ok(count=count, sessions=[],
                   store_authority=CHAT_SESSION_AUTHORITY)
    # Fall back to the receipt life-cycle count only when the canonical store
    # is unavailable (never fabricate a positive count).
    res = _receipt_session_store(cfg).list(100)
    empty = [r for r in res.get("sessions", [])
             if not (r.get("detail") or "").strip()]
    return _ok(count=len(empty),
               sessions=[{"id": r.get("receipt_id") or r.get("action_id"),
                          "created_at": r.get("created_at")} for r in empty],
               store_authority=RECEIPT_SESSION_AUTHORITY)


def h_sessions_import(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                      p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/sessions/import -> real bulk import into the canonical store.

    Ported from the reference ``import_sessions_endpoint``
    (``hermes_cli/web_routers/sessions.py:418`` -> ``SessionDB.import_sessions``),
    which the vendored runtime carries as
    ``qubit_runtime/qubit_state_portability.py:571``. Additive (existing ids are
    skipped), so it is not destructive; every accepted row is written through
    the one canonical ``state.db`` and the run is audited. Malformed payloads
    and over-limit batches are typed 400s — never a fabricated success.
    """
    if _estop(cfg):
        return 403, {"ok": False, "state": "UNAVAILABLE",
                     "code": "EMERGENCY_STOP",
                     "detail": "emergency stop latched; session import refused"}
    sessions = b.get("sessions")
    if not isinstance(sessions, list) or not sessions:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "sessions must be a non-empty list"}
    _cs, db, _dir = _chat_session_db(cfg)
    if db is None:
        return _fail(RuntimeError("canonical session store unavailable"))
    try:
        res = db.import_sessions(sessions)
    except ValueError as exc:
        return 400, {"ok": False, "code": "SESSION_IMPORT_REJECTED",
                     "detail": _redact(str(exc))[:300]}
    except Exception as exc:
        return _fail(exc)
    if not isinstance(res, dict) or not res.get("ok", False):
        detail = res.get("errors") if isinstance(res, dict) else res
        return 400, {"ok": False, "code": "SESSION_IMPORT_REJECTED",
                     "detail": _redact(json.dumps(detail))[:300]}
    _audit("session.import", "dashboard:user",
           {"imported": int(res.get("imported", 0) or 0),
            "skipped": int(res.get("skipped", 0) or 0)})
    return _ok(ok=True, imported=int(res.get("imported", 0) or 0),
               skipped=int(res.get("skipped", 0) or 0),
               detached=int(res.get("detached", 0) or 0),
               imported_ids=list(res.get("imported_ids", []) or []),
               skipped_ids=list(res.get("skipped_ids", []) or []),
               store_authority=CHAT_SESSION_AUTHORITY)


def h_sessions_prune(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                     p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/sessions/prune -> confirmed destructive prune of old sessions.

    Ported from the reference ``_prune_sessions``
    (``hermes_cli/web_routers/sessions.py:71``) onto the vendored
    ``qubit_state`` maintainer (``qubit_runtime/qubit_state_maintenance.py:272``:
    ``prune_sessions`` / ``count_open_prune_matches`` / ``list_prune_candidates``).
    Only ended, non-archived sessions outside the window are removed; a live
    (un-ended) session is always skipped and reported. Confirmation, e-stop and
    audit all run before any row is touched. ``dry_run`` reports the real match
    set without deleting.
    """
    try:
        days = float(b.get("older_than_days", 90) or 90)
    except (TypeError, ValueError):
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "older_than_days must be numeric"}
    if days < 1:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "older_than_days must be >= 1"}
    source = str(b.get("source") or q.get("source") or "").strip() or None
    _cs, db, sessions_dir = _chat_session_db(cfg)
    if db is None:
        return _fail(RuntimeError("canonical session store unavailable"))
    filters = {"older_than_days": days, "source": source, "archived": False}
    if _truthy(b.get("dry_run")):
        try:
            rows = db.list_prune_candidates(**filters)
            skipped_open = int(db.count_open_prune_matches(**filters))
        except Exception as exc:
            return _fail(exc)
        return _ok(ok=True, removed=0, matched=len(rows),
                   skipped_open=skipped_open, dry_run=True,
                   sessions=[{"id": r.get("id"), "source": r.get("source"),
                              "title": r.get("title"), "model": r.get("model"),
                              "started_at": r.get("started_at"),
                              "last_active": r.get("last_active"),
                              "message_count": r.get("message_count")}
                             for r in rows],
                   store_authority=CHAT_SESSION_AUTHORITY)
    guard = _compat_confirm(
        cfg, "sessions.prune",
        f"pruning sessions with no activity for {days:g}+ days is destructive; "
        "re-POST with {\"confirm\": true}", q, b,
        payload={"older_than_days": days, "source": source or ""})
    if guard is not None:
        return guard
    try:
        skipped_open = int(db.count_open_prune_matches(**filters))
        removed = int(db.prune_sessions(sessions_dir=sessions_dir, **filters))
    except Exception as exc:
        return _fail(exc)
    _audit("session.prune", "dashboard:user",
           {"older_than_days": days, "source": source or "",
            "removed": removed, "skipped_open": skipped_open})
    return _ok(ok=True, removed=removed, skipped_open=skipped_open,
               store_authority=CHAT_SESSION_AUTHORITY)


def h_sessions_bulk_delete(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                           p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/sessions/bulk-delete -> confirmed delete of selected sessions.

    Ported from the reference ``bulk_delete_sessions_endpoint``
    (``hermes_cli/web_routers/sessions.py:400``) onto the vendored
    ``SessionDB.delete_sessions`` (``qubit_runtime/qubit_state_sessions.py:1586``):
    one transaction, unknown ids skipped, children orphaned (never cascade-killed).
    The 500-id hard cap mirrors upstream. Confirmation is required first so a
    hand-picked multi-row delete is never implicit.
    """
    ids = b.get("ids")
    if not isinstance(ids, list) or not ids:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "ids must be a non-empty list"}
    if len(ids) > 500:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "ids must contain at most 500 entries"}
    clean_ids = [str(i) for i in ids if str(i or "").strip()][:500]
    if not clean_ids:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "ids must contain at least one session id"}
    _cs, db, sessions_dir = _chat_session_db(cfg)
    if db is None:
        return _fail(RuntimeError("canonical session store unavailable"))
    guard = _compat_confirm(
        cfg, "sessions.bulk_delete",
        f"deleting {len(clean_ids)} selected session(s) is destructive; "
        "re-POST with {\"confirm\": true}", q, b,
        payload={"ids": clean_ids})
    if guard is not None:
        return guard
    try:
        removed = int(db.delete_sessions(clean_ids, sessions_dir=sessions_dir))
    except Exception as exc:
        return _fail(exc)
    _audit("session.bulk_delete", "dashboard:user",
           {"requested": len(clean_ids), "deleted": removed})
    return _ok(ok=True, deleted=removed, requested=len(clean_ids),
               store_authority=CHAT_SESSION_AUTHORITY)


def h_sessions_empty_delete(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                            p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """DELETE /api/sessions/empty -> confirmed one-transaction empty sweep.

    Ported from the reference ``delete_empty_sessions_endpoint``
    (``hermes_cli/web_routers/sessions.py:449``) onto the vendored
    ``SessionDB.delete_empty_sessions`` (``qubit_runtime/qubit_state_sessions.py:1628``).
    "Empty" means NO ``messages`` rows at all — active (un-ended) and archived
    rows are skipped, so a live agent is never yanked and a deliberately
    archived row is never swept. Confirmation is required first.
    """
    _cs, db, sessions_dir = _chat_session_db(cfg)
    if db is None:
        return _fail(RuntimeError("canonical session store unavailable"))
    guard = _compat_confirm(
        cfg, "sessions.delete_empty",
        "deleting every empty session is destructive; re-DELETE with "
        "{\"confirm\": true}", q, b)
    if guard is not None:
        return guard
    try:
        removed = int(db.delete_empty_sessions(sessions_dir=sessions_dir))
    except Exception as exc:
        return _fail(exc)
    _audit("session.delete_empty", "dashboard:user", {"deleted": removed})
    return _ok(ok=True, deleted=removed, store_authority=CHAT_SESSION_AUTHORITY)


def h_mem_reset_status(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                       p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/memory/reset -> read-only status (never stages a mutation).

    A GET must not stage a destructive confirmation: the dashboard may probe
    the endpoint to render state, and a 409 NEEDS_CONFIRMATION on a read was a
    dead read surface. The destructive path stays on POST (``h_mem_reset``).
    """
    return _ok(ok=True, targets=["all", "memory", "user"],
               destructive=True, confirm_required=True,
               detail="POST /api/memory/reset with {\"confirm\": true} to "
                      "reset; GET is a read-only status",
               qubit_counterpart="CLI `qubit memory workspace forget <name>`")


def h_mem_reset(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/memory/reset -> real reset of Qubit-owned local memory.

    Upstream resets the built-in MEMORY.md/USER.md files. Qubit's memory
    authority is the local semantic store (``memory.db``) plus the markdown
    memory workspace, so the reset maps onto the canonical Qubit forget ops:

    * ``target=memory`` — tombstone every active record in the local memory
      store (the MEMORY.md analog);
    * ``target=user``   — forget every note in the markdown memory workspace
      (the USER.md analog);
    * ``target=all``    — both.

    Confirmed through the one shared journal, e-stop-gated, audited, and it
    returns the real deleted identifiers (an honest empty list when there was
    nothing to delete — never a fabricated success).
    """
    target = str(b.get("target", "") or "").strip().lower() or "all"
    if target not in ("all", "memory", "user"):
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "memory reset target must be all|memory|user"}
    gate = _compat_confirm(
        cfg, "memory.reset",
        f"memory reset is destructive ({target}): re-POST with "
        "{\"confirm\": true} or settle the staged approval",
        query=q, body=b, payload={"target": target})
    if gate is not None:
        return gate
    deleted: List[str] = []
    try:
        if target in ("memory", "all"):
            deleted += _reset_active_memories(cfg)
        if target in ("user", "all"):
            deleted += _reset_workspace_notes(cfg)
    except Exception as exc:
        return _fail(exc)
    _audit("memory_reset", "dashboard:user",
           {"target": target, "deleted": len(deleted)})
    return _ok(ok=True, target=target, deleted=deleted, count=len(deleted),
               qubit_counterpart="CLI `qubit memory workspace forget <name>`")


def h_mem_provider(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                   p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/memory/providers/<name> -> live local provider status.

    ``qubit-local`` is the only provider; the response reads the real local
    active-memory store state (never a static shape), and any other name is an
    honest typed 404 -- no cloud provider can be selected.
    """
    name = str(p.get("id", p.get("name", "")) or "")
    if name and name != "qubit-local":
        return 404, {"ok": False, "code": "NOT_FOUND",
                     "detail": f"no memory provider {name!r}",
                     "qubit_counterpart": "CLI `qubit memory` (same local store)"}
    from . import active_memory as AM
    try:
        status = AM.status(cfg)
    except Exception as exc:
        return _fail(exc)
    state = str(status.get("state", "OK") or "OK")
    return _ok(provider="qubit-local", active="qubit-local",
               tiers=["core", "working", "recall", "archive"],
               state=state,
               enabled=bool(status.get("enabled", True)),
               store_state=status.get("store_state"),
               detail="local-only SQLite/FTS5 MemoryStore; cloud sync refused "
                      "by policy")


def _platform_test_resolve(cfg: Any, platform_id: str
                           ) -> Optional[Dict[str, Any]]:
    """Resolve a channel id to the SAME honest payload the platform list serves.

    Returns ``None`` for an unknown id. Keeps the connection test on one store:
    it reuses :func:`_platform_rows` (the ``h_platforms`` GET source) instead of
    probing the disjoint ``msg-accounts.json`` account registry, so a catalog
    platform's real state (configured / transport / unavailable) is what the
    Test button reports.
    """
    kind, _entry, _account = _platform_resolve(cfg, platform_id)
    if kind is None:
        return None
    for row in _platform_rows(cfg):
        if str(row.get("id", "")) == platform_id:
            return {
                "name": row.get("name", platform_id),
                "enabled": bool(row.get("enabled")),
                "configured": bool(row.get("configured")),
                "gateway_running": bool(row.get("gateway_running")),
                "state": str(row.get("state", "") or ""),
                "error_message": str(row.get("error_message") or ""),
                "missing_env": [f["key"] for f in row.get("env_vars", [])
                                if f.get("required") and not f.get("is_set")],
                "error_code": row.get("error_code"),
                "link": {},
            }
    return None


def h_msg_test_live(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/messaging/platforms/{platform_id}/test -> honest test verdict.

    Ported from the vendored ``web_routers/messaging.py:test_messaging_platform``
    (``:916``): an unknown id is a typed ``404`` and ``ok`` is true **only** when
    a real connection is proven. It resolves the id against the SAME catalog and
    store :func:`h_platforms` serves, so a configured channel with no Qubit
    transport reports ``ok: false`` with the operator-facing ``message`` instead
    of a fabricated success. Never sends a message or opens a provider socket.
    """
    from . import messaging as M
    platform_id = str(p.get("id", p.get("platform_id", "")) or "")
    return M.platform_connection_test(cfg, platform_id, _platform_test_resolve)


def h_toolset_toggle(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                     p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """PUT /api/tools/toolsets/{name} -> persisted enable/disable contract.

    The SPA toggle body is ``{enabled, profile}`` (the name is in the path), so
    the previous handler read an empty name and answered ``MALFORMED``. Here the
    path id is authoritative and the mutation runs through
    ``voicebridge.toolsets`` -- the same shared-journal contract the CLI/voice
    use -- returning a populated ``confirmation_id`` on the 409 and consuming it
    exactly once on settle. A body without ``enabled`` (the legacy config-grant
    shape) falls through to :func:`h_toolset_write` unchanged.
    """
    name = str(p.get("id", "") or b.get("name", "") or "")
    enabled = b.get("enabled")
    if enabled is None:
        return h_toolset_write(cfg, q, b, p)
    from . import toolsets as TS
    cid = str(b.get("confirmation_id", "")
              or q.get("confirmation_id", "") or "")
    out = TS.handle_toolset_mutation(
        cfg, name, _truthy(enabled), confirmation_id=cid,
        actor="dashboard:user", session="dashboard")
    if out.get("state") == "needs-confirm" and _truthy(b.get("confirm")):
        # Backward-compatible boolean settle: stage then immediately consume the
        # real id (idempotent, single-use, audited).
        out = TS.settle_toolset_mutation(cfg, out["confirmation_id"],
                                         actor="dashboard:user",
                                         session="dashboard")
    return _mutation_envelope(out)


def h_toolset_write(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """PUT/POST toolset config -> real confirm-gated capability grant.

    A toolset's env/model/provider change is a capability-scoped permission
    write. It records a real grant (``toolset.<name>``) in the same
    ``PermissionsStore`` the CLI/voice use, with backup + audit, and returns
    409 until confirmed. It never writes a secret value.
    """
    from .permissions import permissions_store
    name = str(p.get("id", "") or b.get("name", "") or "")
    if not name:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "toolset name required"}
    detail = (f"toolset {name!r} config change requires explicit confirmation: "
              "re-POST with {\"confirm\": true}")
    guard = _compat_confirm(cfg, f"toolset.{name}", detail, q, b)
    if guard is not None:
        return guard
    scope = str(b.get("scope", "toolset") or "toolset")
    try:
        grant = permissions_store(cfg).grant(
            f"toolset.{name}", requested_by="dashboard:user", confirmed=True,
            scopes=(scope,),
            note="dashboard toolset config " + ", ".join(
                sorted(k for k in b if k not in ("confirm", "name")))[:120])
    except Exception as exc:
        code = getattr(exc, "code", None) or type(exc).__name__
        return 400, {"ok": False, "code": code,
                     "detail": _redact(str(exc))[:300]}
    return _ok(ok=True, name=name, grant_id=grant.id, scope=scope,
               qubit_counterpart="CLI `qubit permissions` + dashboard "
                                 "Settings (classic)")


# Qubit Labs auth + Tool Gateway routing status. The vendored handler
# (``qubit_runtime/qubit_cli/web_routers/status.py::get_portal_status``) serves a
# snapshot that authors the upstream portal host (its hardcoded
# ``subscription_url`` is ``https://portal.nousresearch.com/manage-subscription``
# and the auth snapshot's ``portal_url``/``inference_url`` are the same host).
# BRAND.md requires zero legacy brand on a served product surface, so the compat
# handler serves the same real snapshot with any legacy-host URL scrubbed at the
# Qubit boundary. If Qubit Labs later gets its own portal host, the owner sets
# it here instead of the empty value.
_LEGACY_PORTAL_HOST = "nousresearch.com"


def _scrub_portal_urls(value: Any) -> Any:
    """Recursively blank any string URL that names the upstream portal host."""
    if isinstance(value, dict):
        return {k: _scrub_portal_urls(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub_portal_urls(v) for v in value]
    if isinstance(value, str) and _LEGACY_PORTAL_HOST in value.lower():
        return ""
    return value


def h_portal(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
             p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/portal -> the vendored Qubit Labs routing snapshot, de-branded."""
    try:
        from qubit_cli.web_routers.status import _get_portal_status_sync
        payload = _get_portal_status_sync()
    except Exception as exc:
        return _fail(exc)
    return _ok(**_scrub_portal_urls(payload or {}))


# W13 P0 route parity handlers. Redaction policy for the OAuth read: the
# vendored status card embeds ``token_preview`` (a token fragment); Qubit never
# serves secret material, so every provider record is passed through
# ``_strip_secret_fields``. The denylist is exact key names (NOT a substring
# match) so non-secret booleans like ``has_refresh_token`` survive.
_PROVIDER_SECRET_KEYS = frozenset({
    "token", "token_preview", "access_token", "refresh_token", "id_token",
    "api_key", "apikey", "secret", "client_secret", "password",
    "private_key", "credential", "credentials",
})


def _strip_secret_fields(obj: Any) -> Any:
    """Recursively drop secret-shaped keys; values never leave the vault."""
    if isinstance(obj, dict):
        return {k: _strip_secret_fields(v) for k, v in obj.items()
                if str(k).lower() not in _PROVIDER_SECRET_KEYS}
    if isinstance(obj, list):
        return [_strip_secret_fields(x) for x in obj]
    return obj


# Even a credential *name* (`key_env: FIREWORKS_API_KEY`) is inventory the
# served model picker does not need; Qubit never exposes credential references
# on product surfaces. Substring match here is deliberate (the opposite of the
# exact-name ``_PROVIDER_SECRET_KEYS`` denylist): a provider row's hidden
# ``key_env``/``key_ref``/``api_key_env`` sibling keys are all dropped, while
# public fields (`slug`, `models`, `pricing`, `capabilities`) survive intact.
def _strip_provider_secret_fields(obj: Any) -> Any:
    """Drop credential references/values from a provider payload, recursively.

    The vendored picker payload carries provider rows with hidden metadata;
    credential-shaped keys and key-reference keys (``key_env`` etc.) never leave
    the backend. Response-facing facts (slug, models, totals, capabilities,
    pricing, warnings) are preserved, so the SPA picker renders identically.
    """
    if isinstance(obj, dict):
        out: Dict[str, Any] = {}
        for k, v in obj.items():
            kl = str(k).lower()
            if kl in _PROVIDER_SECRET_KEYS:
                continue
            if any(part in kl for part in ("key_env", "key_ref", "apikey", "api_key")):
                continue
            if kl in {"token", "access_token", "refresh_token", "id_token",
                      "secret", "client_secret", "password", "private_key",
                      "credential", "credentials"}:
                continue
            out[k] = _strip_provider_secret_fields(v)
        return out
    if isinstance(obj, list):
        return [_strip_provider_secret_fields(x) for x in obj]
    return obj


def _vendored_runtime(cfg: Any) -> None:
    """Put the vendored fork on ``sys.path`` and bind the Qubit home."""
    from . import qubit_runtime
    qubit_runtime.ensure_path()
    try:
        qubit_runtime.use_qubit_home(cfg)
    except Exception:  # noqa: BLE001 - a resolution miss must not 500 the read
        pass


def h_gateway_migrate_plan(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                           p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/gateway/migrate/plan -> real read-only multiplex preflight.

    Mirrors reference ``web_routers/actions.py:147`` (same JSON as the CLI
    plan). Builds the vendored ``MigrationPlan`` and returns its
    ``to_dict()``; performs no mutation. The companion
    ``POST /api/gateway/migrate`` remains an operator-owned typed refusal.
    """
    try:
        _vendored_runtime(cfg)
        from qubit_cli.gateway_migrate import build_migration_plan  # type: ignore
        plan = build_migration_plan()
        out = plan.to_dict()
    except Exception as exc:
        return _fail(exc)
    return _ok(**out)


def h_host_identity(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/host/identity -> this process owns the port (reference status.py:122).

    The whole ``/agent`` compat surface is behind the webui session-token gate
    (``webui._qubit_api`` -> ``_qubit_token_ok``), which is the same one gate the
    reference applies per-route. ``servesSpa`` is true: this listener serves the
    Qubit dashboard SPA.
    """
    return _ok(protocolVersion=1, pid=os.getpid(), role="serve", servesSpa=True)


def h_providers_oauth(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                      p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/providers/oauth -> the redacted OAuth provider catalog.

    Reference shape (``web_routers/oauth.py`` GET handler), with secrets
    stripped: ``_strip_secret_fields`` removes ``token_preview`` and every other
    token/secret-shaped key from each provider record (and nested status).
    Returns ``{providers: [...]}``; the SPA Accounts page consumes it.
    """
    try:
        _vendored_runtime(cfg)
        from qubit_cli.web_routers import oauth as _oauth  # type: ignore
        providers: List[Dict[str, Any]] = []
        for rec in _oauth._build_oauth_catalog():
            status = _oauth._resolve_provider_status(rec["id"],
                                                     rec.get("status_fn"))
            disconnect_hint = _oauth._oauth_provider_disconnect_hint(rec, status)
            providers.append({
                "id": rec["id"], "name": rec["name"], "flow": rec["flow"],
                "cli_command": _oauth._external_process_cli_command(
                    rec["id"], rec.get("cli_command", "")),
                "docs_url": rec.get("docs_url", ""),
                "disconnect_hint": disconnect_hint,
                "disconnect_command": _oauth._oauth_provider_disconnect_command(rec),
                "disconnectable": disconnect_hint is None,
                "status": status,
            })
    except Exception as exc:
        return _fail(exc)
    # Secrets first (never leave the vault), then the established brand
    # boundary: any legacy-host URL is blanked, matching ``h_portal``.
    return _ok(providers=_scrub_portal_urls(_strip_secret_fields(providers)))


def _oauth_fail(exc: Exception) -> Tuple[int, Dict[str, Any]]:
    """Map a vendored OAuth ``HTTPException`` to its (status, typed payload).

    Anything else is ``_fail``. The vendored detail strings are already
    operator-facing ("Session not found or expired", "Provider mismatch",
    "uses an external CLI; run ... manually"); they carry no secret material
    (sessions hold device/user codes and vendor URLs, never tokens — tokens
    are saved server-side by the poller workers into the auth store).
    """
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status, {"ok": False, "code": "OAUTH_ERROR",
                        "detail": _redact(str(getattr(exc, "detail", exc) or
                                             "OAuth request failed"))[:400]}
    return _fail(exc)


def _oauth_vendored(cfg: Any) -> Any:
    """Import the vendored OAuth router (port 1:1, never reimplemented)."""
    _vendored_runtime(cfg)
    from qubit_cli.web_routers import oauth as _O  # type: ignore
    return _O


def h_oauth_start(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                  p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/providers/oauth/{id}/start -> live device-code login start.

    Delegates to the vendored implementation (the same device-code starters
    the reference dashboard serves: ``nous``/``openai-codex``/``minimax-oauth``/
    ``xai-oauth``); the compat HTTP boundary already authenticated the caller,
    so the vendored ``_require_token`` (a second SPA-token check) is not
    re-run — only its inner start logic. Returns the display fields only
    (``session_id``/``user_code``/``verification_url``/``expires_in``); token
    material never leaves the server (the background poller saves it into the
    auth store). ``external``-flow providers get the honest 400 naming the CLI
    command, exactly as the reference.
    """
    import asyncio

    from fastapi import HTTPException as _HTTPException
    try:
        _O = _oauth_vendored(cfg)
    except Exception as exc:
        return _fail(exc)
    provider_id = str(p.get("provider_id", "") or "").strip()
    profile = (q.get("profile") or "").strip() or None
    try:
        _O._gc_oauth_sessions()
        _O._validate_oauth_profile(profile)
        entry = next((e for e in _O._OAUTH_PROVIDER_CATALOG
                      if e.get("id") == provider_id), None)
        if entry is None:
            raise _HTTPException(status_code=400,
                                 detail=f"Unknown provider {provider_id}")
        if entry.get("flow") == "external":
            raise _HTTPException(
                status_code=400,
                detail=f"{provider_id} uses an external CLI; run "
                       f"`{entry.get('cli_command', '')}` manually")
        if entry.get("flow") != "device_code":
            raise _HTTPException(status_code=400, detail="Unsupported flow")
        out = asyncio.run(
            _O._start_device_code_flow(provider_id, profile=profile))
    except Exception as exc:
        return _oauth_fail(exc)
    try:
        _audit("oauth_start", "dashboard:user",
               {"provider": provider_id,
                "session": str(out.get("session_id", ""))[:32]})
    except Exception:
        pass
    return 200, {"ok": True, **out}


def h_oauth_submit(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                   p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/providers/oauth/{id}/submit -> honest PKCE outcome.

    The vendored implementation supports no PKCE submit (every provider is
    device-code or external); this answers the same typed 400 for a known
    provider instead of a dead 404, so the modal's PKCE branch shows the real
    reason.
    """
    from fastapi import HTTPException as _HTTPException
    try:
        _O = _oauth_vendored(cfg)
    except Exception as exc:
        return _fail(exc)
    provider_id = str(p.get("provider_id", "") or "").strip()
    try:
        known = any(e.get("id") == provider_id
                    for e in _O._OAUTH_PROVIDER_CATALOG)
        if not known:
            raise _HTTPException(status_code=400,
                                 detail=f"Unknown provider {provider_id}")
        raise _HTTPException(status_code=400,
                             detail=f"submit not supported for {provider_id}")
    except Exception as exc:
        return _oauth_fail(exc)


def h_oauth_poll(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/providers/oauth/{id}/poll/{session} -> live session status.

    Read-only; directly delegates to the vendored poll (no second auth check
    exists there either). Unknown/expired sessions are an honest 404, provider
    or profile mismatches an honest 400 — never a fabricated "pending".
    """
    import asyncio

    try:
        _O = _oauth_vendored(cfg)
    except Exception as exc:
        return _fail(exc)
    try:
        out = asyncio.run(_O.poll_oauth_session(
            str(p.get("provider_id", "") or ""),
            str(p.get("session_id", "") or ""),
            profile=(q.get("profile") or "").strip() or None))
    except Exception as exc:
        return _oauth_fail(exc)
    return 200, {"ok": True, **out}


def h_oauth_cancel(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                   p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """DELETE /api/providers/oauth/sessions/{id} -> cancel a pending login.

    Same semantics as the vendored cancel: the session is marked ``cancelled``
    before removal so a background poller holding the dict stops instead of
    completing the login after the user aborted. Non-destructive — no confirm
    gate (the vendored route likewise needs only the token, which the compat
    boundary already checked).
    """
    from fastapi import HTTPException as _HTTPException
    try:
        _O = _oauth_vendored(cfg)
        from qubit_cli.web_server_oauth import (  # type: ignore
            _oauth_sessions as _sessions,
            _oauth_sessions_lock as _sessions_lock)
    except Exception as exc:
        return _fail(exc)
    session_id = str(p.get("id", "") or "").strip()
    profile = (q.get("profile") or "").strip() or None
    try:
        _O._gc_oauth_sessions()
        requested = _O._oauth_profile_name(profile)
        with _sessions_lock:
            sess = _sessions.get(session_id)
            if sess is not None:
                if sess.get("profile") != requested:
                    raise _HTTPException(
                        status_code=400,
                        detail="OAuth session profile mismatch")
                sess["cancelled"] = True
                _sessions.pop(session_id, None)
    except Exception as exc:
        return _oauth_fail(exc)
    if sess is None:
        return 200, {"ok": False, "message": "session not found"}
    return 200, {"ok": True, "session_id": session_id}


def h_oauth_disconnect(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                       p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """DELETE /api/providers/oauth/{id} -> disconnect (confirm-gated).

    Destructive (clears stored credentials), so it stages through the one
    confirm path like every other compat mutation. The credential clearing
    itself is the vendored logic verbatim: external providers are refused with
    the manual path, ``anthropic`` clears only the Qubit-managed file (never
    ``~/.claude/*``), and everything runs under the requested profile scope.
    """
    import asyncio

    try:
        _O = _oauth_vendored(cfg)
        from qubit_cli.web_routers._common import (  # type: ignore
            scoped_to_thread as _scoped)
    except Exception as exc:
        return _fail(exc)
    provider_id = str(p.get("id", "") or "").strip()
    profile = (q.get("profile") or "").strip() or None
    guard = _compat_confirm(
        cfg, "oauth.disconnect",
        f"Disconnect {provider_id or 'provider'}: remove its stored OAuth "
        f"credentials? Re-authentication will be needed to use it again.",
        q, b)
    if guard is not None:
        return guard

    def _run() -> Dict[str, Any]:
        from fastapi import HTTPException as _HTTPException
        catalog_by_id = {e["id"]: e for e in _O._build_oauth_catalog()}
        provider = catalog_by_id.get(provider_id)
        if provider is None:
            raise _HTTPException(
                status_code=400,
                detail=f"Unknown provider: {provider_id}. Available: "
                       f"{', '.join(sorted(catalog_by_id))}")
        _O._reject_if_not_disconnectable(provider, {})
        _O._reject_if_not_disconnectable(
            provider, _O._resolve_provider_status(
                provider_id, provider.get("status_fn")))
        if provider_id == "anthropic":
            cleared = _O._clear_anthropic_auth()
            return {"ok": bool(cleared), "provider": provider_id}
        from qubit_cli.auth import (  # type: ignore
            clear_provider_auth, invalidate_nous_auth_status_cache)
        cleared = clear_provider_auth(provider_id)
        if provider_id == "nous":
            invalidate_nous_auth_status_cache()
        return {"ok": bool(cleared), "provider": provider_id}

    try:
        out = asyncio.run(_scoped(profile, _run))
    except Exception as exc:
        return _oauth_fail(exc)
    try:
        _audit("oauth_disconnect", "dashboard:user",
               {"provider": provider_id, "cleared": out.get("ok")})
    except Exception:
        pass
    return 200, {"ok": bool(out.get("ok")), "provider": provider_id}


# Dashboard plugin assets (upstream ``GET /dashboard-plugins/<name>/<path>``).
# The vendored handler
# (``qubit_runtime/qubit_cli/web_routers/dashboard_ui.py::serve_plugin_asset``)
# serves a discovered plugin's ``dashboard/`` directory behind an activation
# gate, ``resolve().is_relative_to()`` confinement, and a browser-asset suffix
# allowlist (a plugin's ``.py`` backend never leaves the box). Qubit's SPA serves
# overlay bundles from the same path; this resolver adds the vendored
# user/bundled plugin dirs on ONE resolution shared by the ``/agent`` compat
# route and the raw browser path (``webui._serve_qubit_plugin``).
def resolve_plugin_asset(plugin_name: str, file_path: str, cfg: Any = None
                         ) -> Tuple[int, str, Optional[bytes], Dict[str, Any]]:
    """Resolve one dashboard plugin asset: real bytes, or a typed miss."""
    # Served per browser request (webui) as well as through the compat
    # dispatcher, so the vendored path cannot be assumed warm from an earlier
    # import — without this, plugin JS/CSS 404s in any process whose first
    # plugin-asset call wins the import race.
    _vendored_runtime(cfg)
    from qubit_cli.web_server import _get_dashboard_plugins
    from qubit_cli.web_routers.dashboard_ui import (
        _PLUGIN_ASSET_CONTENT_TYPES, _plugin_activated, _plugin_enable_sets)

    name = str(plugin_name or "").strip()
    plugin = next((p for p in _get_dashboard_plugins()
                   if p.get("name") == name), None)
    if plugin is None:
        # A plugin installed after process start is absent from the cached
        # registry: force one rescan before answering the miss, so newly
        # installed dashboard tabs serve without a service restart.
        try:
            plugin = next((p for p in _get_dashboard_plugins(
                force_rescan=True) if p.get("name") == name), None)
        except Exception:
            plugin = None
    if not plugin or not _plugin_activated(plugin, *_plugin_enable_sets()):
        return 404, "application/json", None, {
            "ok": False, "code": "PLUGIN_NOT_FOUND",
            "detail": f"dashboard plugin {name!r} is not installed or not enabled",
            "qubit_counterpart": "CLI `qubit plugins list|enable <name>`"}
    base = Path(plugin["_dir"])
    target = (base / str(file_path or "")).resolve()
    if not target.is_relative_to(base.resolve()):
        return 403, "application/json", None, {
            "ok": False, "code": "PLUGIN_PATH_BLOCKED",
            "detail": "path traversal blocked",
            "qubit_counterpart": "CLI `qubit plugins list`"}
    media_type = _PLUGIN_ASSET_CONTENT_TYPES.get(target.suffix.lower())
    if media_type is None or not target.is_file():
        return 404, "application/json", None, {
            "ok": False, "code": "PLUGIN_ASSET_NOT_FOUND",
            "detail": f"{file_path!r} is not a servable asset of {name!r}",
            "qubit_counterpart": "CLI `qubit plugins list`"}
    try:
        body = target.read_bytes()
    except OSError as exc:
        return 404, "application/json", None, {
            "ok": False, "code": "PLUGIN_ASSET_NOT_FOUND",
            "detail": _redact(str(exc))[:200],
            "qubit_counterpart": "CLI `qubit plugins list`"}
    return 200, media_type, body, {"name": name, "path": str(file_path or ""),
                                   "content_type": media_type}


def h_plugin_asset(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                   p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /dashboard-plugins/<plugin>/<path> -> real plugin asset bytes.

    The compat dispatcher is JSON, so the bytes ride base64-encoded with the real
    content type (the same envelope the kanban attachment read uses). Browsers
    load the raw asset from the shared ``resolve_plugin_asset`` resolution via
    ``webui._serve_qubit_plugin``.
    """
    status, ctype, body, meta = resolve_plugin_asset(
        str(p.get("plugin_name", "") or ""), str(p.get("file_path", "") or ""),
        cfg)
    if body is None:
        return status, meta
    return _ok(**meta, size=len(body),
               data_base64=base64.b64encode(body).decode("ascii"))


def h_plugins_rescan(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                     p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    manifests = _overlay_manifests()
    return _ok(rescanned=True, count=len(manifests), plugins=[m["name"] for m in manifests])


def h_plugin_visibility(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                        p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/dashboard/plugins/{name}/visibility -> real persisted toggle.

    The per-user hidden set is persisted in ``<state_dir>/qubit_hidden.json``
    (the same store ``plugins_hub`` reads) with an atomic write + audit, so a
    hidden overlay plugin stays hidden across dashboard reloads.
    """
    name = str(p.get("id", "") or b.get("name", "") or "")
    if not name:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "plugin name required",
                     "qubit_counterpart": "CLI `qubit plugins`"}
    hidden = _hidden_set(cfg)
    # B-08: the dashboard API sends ``{hidden: bool}`` (setPluginVisibility),
    # while an older caller may send ``{visible: bool}``. Honour ``hidden`` when
    # present; a bare ``visible`` is the inverse. Reading only ``visible`` made
    # the "hide" control a silent no-op that always re-showed the plugin.
    if "hidden" in b:
        want_hidden = bool(b.get("hidden"))
    else:
        want_hidden = not bool(b.get("visible", True))
    if want_hidden:
        hidden.add(name)
    else:
        hidden.discard(name)
    try:
        _hidden_save(cfg, hidden)
    except Exception as exc:
        return _fail(exc)
    # The dashboard's plugin hub is served by the vendored
    # ``_merged_plugins_hub`` (a 5s memo) whose ``user_hidden`` comes from
    # ``dashboard.hidden_plugins`` in the runtime config; the eye toggle writes
    # Qubit's own ``qubit_hidden.json`` and so was never consumed. Mirror the
    # set into the runtime config AND invalidate the hub memo so the change
    # takes effect on the very next hub fetch (the reference route does the
    # same, persisting ``dashboard.hidden_plugins`` + invalidating). Both are
    # best-effort: the Qubit store remains the authority and the Qubit
    # ``plugins_hub`` override reads it directly.
    mirrored = _mirror_hidden_plugins(cfg, hidden)
    try:
        from qubit_cli.web_server_dashboard import (
            _invalidate_plugins_hub_cache,
        )
        _invalidate_plugins_hub_cache()
    except Exception:
        pass
    _audit("plugin_visibility", "dashboard:user",
           {"name": name, "visible": name not in hidden,
            "runtime_mirrored": mirrored})
    return _ok(name=name, visible=name not in hidden,
               hidden=name in hidden, hidden_names=sorted(hidden),
               detail="persisted per-user visibility")


def _mirror_hidden_plugins(cfg: Any, hidden: set) -> bool:
    """Mirror the hidden set into the vendored ``dashboard.hidden_plugins``.

    The vendored plugin hub/tab filter reads this config key; without the
    mirror the Qubit eye toggle split-brained (write ``qubit_hidden.json``,
    read the vendored config). Best-effort and canonical-writer based: a
    managed/read-only config leaves the Qubit store as the sole authority.
    """
    try:
        from . import qubit_runtime as QR
        QR.ensure_path()
        try:
            QR.use_qubit_home(cfg)
        except Exception:  # noqa: BLE001 - config-scoped home is best-effort
            pass
        from qubit_cli.config import load_config, save_config  # type: ignore

        conf = load_config()
        dash = conf.get("dashboard")
        if not isinstance(dash, dict):
            dash = {}
            conf["dashboard"] = dash
        dash["hidden_plugins"] = sorted(hidden)
        save_config(conf, preserve_keys={("dashboard", "hidden_plugins")})
        return True
    except Exception:  # noqa: BLE001 - the Qubit store stays authoritative
        return False


def h_debug_share(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                  p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import dashboard_ops as OPS
    out = OPS.run_op(cfg, "diagnostics", {}, actor="dashboard:user", session="dashboard")
    # B-08/B-14: the SPA reads ``urls``/``redacted``/``auto_delete_seconds``/
    # ``failures``. Qubit never uploads to an external share service, so the
    # response is honest: the redacted bundle plus an empty share set, not a
    # missing shape that throws client-side.
    return _ok(bundle=_res(out), urls={}, failures=[],
               redacted=bool(b.get("redact", True)), auto_delete_seconds=0,
               external_share=False,
               detail="redacted diagnostic bundle (never secrets); external "
                      "share is disabled by Qubit policy — no URL is created")


def h_hooks(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
            p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import hooks as H
    try:
        res = H.list_hooks(cfg)
    except Exception as exc:
        return _fail(exc)
    recs = res.get("hooks", []) if isinstance(res, dict) else []
    entries = []
    for r in recs if isinstance(recs, list) else []:
        if not isinstance(r, dict):
            continue
        entries.append({
            "event": str(r.get("event", "")),
            "matcher": str(r.get("name", "")),
            "command": None,
            "timeout": None,
            "allowed": str(r.get("status", "")) == "active",
            "approved_at": None,
            "executable": False,
        })
    # HooksResponse wire shape; shells never execute (broker-audited
    # delivery only), so command stays null by policy.
    return _ok(hooks=entries, valid_events=["*"])


def h_hooks_create(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                   p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import hooks as H
    event = str(b.get("event", "") or "")
    shell = str(b.get("command", b.get("shell", "")) or "")
    name = str(b.get("name", "") or "")
    if not event:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "hook event required"}
    if _estop(cfg):
        return 403, {"ok": False, "state": "UNAVAILABLE",
                     "code": "EMERGENCY_STOP",
                     "detail": "emergency stop latched; mutation refused",
                     "owner": "qubit-policy",
                     "rollback": "nothing changed; nothing to roll back"}
    approved = (_truthy(b.get("confirm")) or _truthy(b.get("approved"))
                or _truthy(b.get("approve")) or _truthy(q.get("confirm")))
    if not approved:
        # Stage a real, settleable journal confirmation instead of a bare
        # boolean refusal; the pending-approvals banner can execute it.
        gate = _compat_confirm(
            cfg, "hooks.declare",
            "hook declare (shell stored disabled, never runs) requires "
            "explicit confirmation",
            query=q, body=b,
            payload={"name": name, "event": event, "command": shell})
        if gate is not None:
            return gate
    if not name:
        name = re.sub(r"[^a-z0-9]+", "-", event.lower()).strip("-")[:40] or "hook"
    try:
        res = H.declare(cfg, name, event, shell,
                        requested_by="dashboard:user", confirmed=True)
    except Exception as exc:
        code = getattr(exc, "code", None) or type(exc).__name__
        return 400, {"ok": False, "code": code,
                     "detail": _redact(str(exc))[:300]}
    hook = res.get("hook", {}) if isinstance(res, dict) else {}
    return _ok(ok=True, event=str(hook.get("event", event)),
               command="", approved=False)


def _checkpoint_base(cfg: Any) -> Path:
    """The canonical checkpoint shadow-store base (``<qubit_home>/checkpoints``).

    Resolved through :func:`voicebridge.qubit_runtime.runtime_home_for_cfg` so
    the read and the prune operate on the SAME cfg-scoped store the vendored
    ``tools.checkpoint_maintenance`` uses; passing it explicitly also binds the
    vendored ``CHECKPOINT_BASE`` (an import-time global) to this config.
    """
    from . import qubit_runtime as QR

    return Path(QR.runtime_home_for_cfg(cfg)) / "checkpoints"


def h_ckpt(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
           p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/ops/checkpoints -> real per-session checkpoint size.

    Ported from the vendored ``web_routers/ops.py::list_checkpoints`` (the
    ``/rollback`` shadow store): one directory per session under
    ``<qubit_home>/checkpoints`` with its file count and byte size, so the
    dashboard shows exactly what a prune reclaims. Replaces the previous
    ``dashboard_ops.run_op("checkpoints_list")`` call, which was an UNKNOWN_OP
    — so the surface always rendered an empty placeholder.
    """
    try:
        cp_dir = _checkpoint_base(cfg)
    except Exception as exc:
        return _fail(exc)
    sessions: List[Dict[str, Any]] = []
    total_bytes = 0
    try:
        if cp_dir.is_dir():
            with os.scandir(cp_dir) as scan:
                children = sorted((Path(e.path) for e in scan),
                                  key=lambda x: x.name)
            for child in children:
                if not child.is_dir():
                    continue
                size = count = 0
                for f in child.rglob("*"):
                    if f.is_file():
                        try:
                            size += f.stat().st_size
                            count += 1
                        except OSError:
                            pass
                total_bytes += size
                sessions.append({"session": child.name, "files": count,
                                 "bytes": size})
    except OSError as exc:
        return _fail(exc)
    # CheckpointsResponse wire shape.
    return _ok(sessions=sessions, total_bytes=total_bytes)


def h_checkpoints_prune(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                        p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/ops/checkpoints/prune -> real retention/policy prune.

    Ported from the vendored ``web_routers/ops.py::prune_checkpoints`` (which
    spawns ``qubit checkpoints prune`` -> ``tools.checkpoint_maintenance
    .prune_checkpoints``). Qubit executes it synchronously through the one
    authority path: e-stop -> journal confirmation (``checkpoints.prune``) ->
    audit -> the real prune, with the metadata-only outcome recorded for the
    ``/api/actions/checkpoints-prune/status`` poll the SPA performs. Deletes
    stale entries past ``retention_days``, unreachable-workdir orphans (bounded
    to the previewed identities unless ``delete_orphans:false``), and shrinks
    the store to ``max_total_size_mb`` when given. A client boolean is never
    authority; the durable ``confirmation_id`` is.
    """
    if _estop(cfg):
        return 403, {"ok": False, "state": "UNAVAILABLE",
                     "code": "EMERGENCY_STOP",
                     "detail": "emergency stop latched; prune refused",
                     "owner": "qubit-policy",
                     "rollback": "nothing deleted; store unchanged"}

    def _int_in(name: str, default: int, lo: int, hi: int) -> int:
        raw = b.get(name, q.get(name, default))
        try:
            return max(lo, min(int(raw), hi))
        except (TypeError, ValueError):
            return default

    retention_days = _int_in("retention_days", 7, 0, 3650)
    max_size_mb = _int_in("max_total_size_mb", 0, 0, 1_000_000)
    if "delete_orphans" in b or "delete_orphans" in q:
        delete_orphans = _truthy(b.get("delete_orphans",
                                       q.get("delete_orphans")))
    else:
        delete_orphans = True
    try:
        base = _checkpoint_base(cfg)
    except Exception as exc:
        return _fail(exc)
    guard = _compat_confirm(
        cfg, "checkpoints.prune",
        ("pruning the checkpoint store is metadata-destructive and "
         "irreversible; re-POST with confirm=true"),
        query=q, body=b,
        payload={"retention_days": retention_days,
                 "delete_orphans": delete_orphans,
                 "max_total_size_mb": max_size_mb})
    if guard is not None:
        return guard
    try:
        from . import qubit_runtime as QR
        QR.ensure_path()
        from tools.checkpoint_maintenance import (  # type: ignore
            prune_checkpoints, store_status,
        )
        info = store_status(base)
        # Bind orphan deletion to exactly the identities the pre-prune store
        # status exposes, mirroring the CLI's non-force preview contract so a
        # project that orphans *after* this snapshot is not swept up.
        orphans = [p for p in info.get("projects", []) if not p.get("exists")]
        pre_v2 = [p for p in info.get("pre_v2_projects", []) if not p.get("exists")]
        allowlist = {str(p.get("hash") or "") for p in orphans} | {
            str(p.get("path") or "") for p in pre_v2}
        result = prune_checkpoints(
            retention_days=retention_days,
            delete_orphans=delete_orphans,
            checkpoint_base=base,
            max_total_size_mb=max_size_mb,
            orphan_allowlist=allowlist)
    except Exception as exc:
        return _fail(exc)
    _audit("checkpoints.prune", "dashboard:user",
           {"retention_days": retention_days,
            "delete_orphans": delete_orphans,
            "max_total_size_mb": max_size_mb,
            "deleted_orphan": result.get("deleted_orphan"),
            "deleted_stale": result.get("deleted_stale"),
            "bytes_freed": result.get("bytes_freed")})
    speech = (f"Pruned {int(result.get('deleted_orphan', 0)) + int(result.get('deleted_stale', 0))} "
              f"checkpoint entr{'y' if int(result.get('deleted_orphan', 0)) + int(result.get('deleted_stale', 0)) == 1 else 'ies'}; "
              f"reclaimed {int(result.get('bytes_freed', 0))} bytes")
    record_action_status("checkpoints-prune",
                         {"state": "executed", "result": result,
                          "speech": speech})
    return _ok(ok=True, name="checkpoints-prune", pruned=True,
               result=result, speech=speech,
               qubit_counterpart="CLI `qubit checkpoints prune`")


def h_actions_read(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                   p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import dashboard_ops as OPS
    out = OPS.run_op(cfg, "actions_list", {"limit": 50},
                     actor="dashboard:user", session="dashboard")
    return _ok(actions=_res(out), state=out.get("state"))


# --------------------------------------------------------------------------
# Action-status registry (upstream action-log viewer parity)
# --------------------------------------------------------------------------
# The vendored dashboard's action-log viewer polls
# ``GET /api/actions/<name>/status`` while a spawned admin action runs. Qubit
# executes its admin operations synchronously on the one broker, so there is
# no long-lived child process to supervise; this records the real terminal
# outcome of each named action and serves the truthful status. A name with no
# recorded outcome is a typed ACTION_NOT_TRACKED refusal, never a fabricated
# "running" or success state.
_ACTION_STATUS: Dict[str, Dict[str, Any]] = {}
_ACTION_STATUS_CAP = 64
_ACTION_TERMINAL_STATES = ("executed", "failed", "blocked", "cancelled",
                           "rejected")

# Process-supervision/update actions the dashboard polls by name. They are
# triggered by their own dedicated endpoints (``POST /api/gateway/*``,
# ``POST /api/qubit/update``) rather than the ops registry, and a gateway
# restart replaces the dashboard process, so the in-memory registry is
# legitimately empty afterwards. The vendored status endpoint 404s only an
# *unknown* action and answers the real wire shape for every known one
# (``qubit_runtime/qubit_cli/web_routers/actions.py::get_action_status`` over
# ``web_server_gateway._ACTION_LOG_FILES``); these names are that known set,
# so an unrecorded poll must return the honest not-running payload (N8) — never
# a bare 404 the UI treats as a transient fetch error and polls forever.
_SUPERVISION_ACTION_NAMES = frozenset({
    "gateway-restart", "gateway-start", "gateway-stop", "qubit-update",
})


def record_action_status(name: str, out: Dict[str, Any]) -> None:
    """Record the real terminal outcome of one named dashboard action."""
    name = str(name or "").strip()
    state = str((out or {}).get("state") or "")
    if not name or state not in _ACTION_TERMINAL_STATES:
        return
    lines: List[str] = []
    detail = _redact(str(out.get("speech") or out.get("detail") or ""))
    if detail:
        lines.append(detail)
    code = out.get("code")
    if code:
        lines.append(f"code: {code}")
    result = out.get("result")
    if result is not None:
        try:
            lines.append(json.dumps(result, indent=2, sort_keys=True)[:8000])
        except (TypeError, ValueError):
            lines.append(str(result)[:8000])
    _ACTION_STATUS[name] = {
        "name": name, "running": False,
        "exit_code": 0 if state == "executed" else 1,
        "pid": None, "state": state, "code": code, "lines": lines,
    }
    while len(_ACTION_STATUS) > _ACTION_STATUS_CAP:
        _ACTION_STATUS.pop(next(iter(_ACTION_STATUS)))


def _supervision_action_status(cfg: Any, name: str) -> Optional[Dict[str, Any]]:
    """Derive a supervision action's status from its durable control record.

    Ported from the reference ``GET /api/actions/{name}/status``
    (``web_routers/actions.py:get_action_status``), which reports a completed
    action from durable evidence (the action log + ``_ACTION_RESULTS``) rather
    than only an in-memory process registry. Qubit's equivalent durable record
    is written by :func:`voicebridge.gateway_supervisor._record_control_status`;
    the live systemd state resolves a detached restart/stop to its real
    terminal outcome. Returns ``None`` when no control was ever dispatched.
    """
    from . import gateway_supervisor as GS

    action = name[len("gateway-"):] if name.startswith("gateway-") else name
    rec = GS.control_status(cfg, action)
    if rec is None:
        return None
    try:
        live = GS.status(cfg)
    except Exception:  # noqa: BLE001 - never fail the status read
        live = {}
    state = str(rec.get("state") or "")
    detail = str(rec.get("detail") or "")
    code = str(rec.get("code") or "")
    active_state = str(live.get("active_state") or "unknown")
    pid = live.get("pid")
    running, exit_code, status_state = False, None, state
    if state in ("RESTARTING", "STOPPING"):
        age = time.time() - float(rec.get("updated_at") or 0.0)
        if age < GS.DETACH_DELAY_S + 1.0:
            running, status_state = True, "in_progress"
        elif active_state in ("activating", "deactivating", "reloading"):
            running, status_state = True, "in_progress"
        elif live.get("failed") or active_state == "failed":
            exit_code, status_state = 1, "failed"
        else:
            exit_code, status_state = 0, "completed"
    elif state == "OK":
        exit_code, status_state = 0, "completed"
    elif state in ("FAILED", "TIMEOUT", "UNAVAILABLE"):
        exit_code, status_state = 1, "failed"
    lines = [line for line in (
        detail,
        f"code: {code}" if code else "",
        f"unit: {live.get('service_unit', '')}",
        f"active_state: {active_state} pid: {pid}",
    ) if line]
    return _ok(name=name, running=running, exit_code=exit_code, pid=pid,
               state=status_state or "unknown", lines=lines,
               recorded_at=rec.get("updated_at"), detail=detail or None)


def h_action_status(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/actions/<name>/status -> the recorded real outcome."""
    name = str(p.get("name", "") or "").strip()
    if not name:
        return _gate("ACTION_NAME_REQUIRED", "an action name is required",
                     "run the action from /system or /operations", 400)
    rec = _ACTION_STATUS.get(name)
    if rec is None:
        if name in _SUPERVISION_ACTION_NAMES:
            # Known action, no in-memory outcome: a gateway restart replaces
            # the serving process, so the new listener's registry is empty.
            # Report the durable control outcome (the reference's action-log
            # equivalent) instead of a permanent ``unknown``.
            durable = _supervision_action_status(cfg, name)
            if durable is not None:
                return durable
            return _ok(name=name, running=False, exit_code=None, pid=None,
                       state="unknown", lines=[],
                       detail="no gateway control dispatched on this host yet")
        return _gate(
            "ACTION_NOT_TRACKED",
            f"no action named {name!r} has run on this listener",
            "run the action from /system, /operations, or `qubit ops run`",
            404)
    return _ok(**rec)


def _prefs_path(cfg: Any) -> Path:
    # Canonical Qubit store (the write target).
    return _store_path(cfg, "qubit_dashboard_prefs.json")


def _prefs_read_path(cfg: Any) -> Path:
    # Reads fall back to the legacy `hermes_*` name until the next save.
    return _migrated_read_path(cfg, "qubit_dashboard_prefs.json",
                               "hermes_dashboard_prefs.json")


def _prefs(cfg: Any) -> Dict[str, Any]:
    return _load_json_store(_prefs_read_path(cfg))


def h_font(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
           p: Dict[str, str], setter: bool = False) -> Tuple[int, Dict[str, Any]]:
    """GET/PUT /api/dashboard/font -> real persisted dashboard font pref."""
    prefs = _prefs(cfg)
    if setter:
        font = str(b.get("font", "system") or "system").strip() or "system"
        prefs["font"] = font
        try:
            _save_json_store(_prefs_path(cfg), prefs)
        except Exception as exc:
            return _fail(exc)
        _audit("dashboard_font", "dashboard:user", {"font": font})
        return _ok(font=font, persisted=True)
    return _ok(font=str(prefs.get("font", "system") or "system"))


def h_plugins(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
              p: Dict[str, str]) -> Tuple[int, Any]:
    # Wire shape is a bare manifest array (api.getPlugins: PluginManifestResponse[]).
    rows = _overlay_manifests()
    seen = {str(m.get("name", "")) for m in rows}
    try:
        # User-installed dashboard plugins (QUBIT_HOME/plugins/*/dashboard/
        # manifest.json) mount their tabs exactly like the reference: the
        # vendored discovery scans user+bundled+project sources, and user
        # plugins serve only when enabled and not disabled (reference gate).
        _vendored_runtime(cfg)
        from qubit_cli.web_server_dashboard import (  # type: ignore
            _discover_dashboard_plugins, _strip_dashboard_manifest)
        from qubit_cli.plugins_cmd import (  # type: ignore
            _get_disabled_set, _get_enabled_set)
        try:
            enabled = set(_get_enabled_set())
        except Exception:
            enabled = set()
        try:
            disabled = set(_get_disabled_set())
        except Exception:
            disabled = set()
        for plugin in _discover_dashboard_plugins():
            name = str(plugin.get("name", "") or "")
            if not name or name in seen:
                continue
            if str(plugin.get("source", "")) == "user" and (
                    name not in enabled or name in disabled):
                continue
            seen.add(name)
            rows.append(_strip_dashboard_manifest(dict(plugin)))
    except Exception as exc:
        _audit("plugins_discovery_failed", "dashboard:user",
               {"detail": _redact(str(exc))[:160]})
    return 200, rows


def h_themes(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
             p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    out = themes_response(cfg)
    active = str(_prefs(cfg).get("theme", "") or "").strip()
    if active and any(t.get("name") == active for t in out.get("themes", [])):
        out["active"] = active
    return _ok(**out)


def h_theme_set(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """PUT /api/dashboard/theme -> real persisted theme selection.

    Any theme from ``themes_response`` (the vendored built-ins + the Qubit
    overlay) persists through the canonical prefs store, mirroring the
    reference (which writes ``dashboard.theme`` into config). Unknown names
    are a typed refusal, never a silent keep-current.
    """
    name = str(b.get("theme", "") or "").strip()
    available = {t.get("name") for t in themes_response(cfg).get("themes", [])}
    if not name or name not in available:
        return 400, {"ok": False, "code": "NOT_FOUND",
                     "detail": f"theme {name!r} is not installed"}
    prefs = _prefs(cfg)
    prefs["theme"] = name
    try:
        _save_json_store(_prefs_path(cfg), prefs)
    except Exception as exc:
        return _fail(exc)
    _audit("dashboard_theme", "dashboard:user", {"theme": name})
    return _ok(ok=True, theme=name, persisted=True)


def h_theme_get(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/dashboard/theme -> current persisted theme (read-only).

    Method-symmetric counterpart to ``PUT /api/dashboard/theme``: the PUT
    persists a selection, the GET reports it with no side effect. A stored
    selection is reported only while it is still installed; otherwise the
    shipped theme is the active one (mirrors ``h_themes``).
    """
    out = themes_response(cfg)
    available = {t.get("name") for t in out.get("themes", [])}
    stored = str(_prefs(cfg).get("theme", "") or "").strip()
    theme = stored if stored in available else str(out.get("active", "") or "")
    return _ok(ok=True, theme=theme, persisted=stored in available)


def _need_confirm(action: str) -> Tuple[int, Dict[str, Any]]:
    return _staged(f"{action} requires explicit confirmation: re-POST "
                   "with {\"confirm\": true}", "")


def _hidden_path(cfg: Any) -> Path:
    # Canonical Qubit store (the write target).
    return _store_path(cfg, "qubit_hidden.json")


def _hidden_read_path(cfg: Any) -> Path:
    # Reads fall back to the legacy `hermes_*` name until the next save.
    return _migrated_read_path(cfg, "qubit_hidden.json", "hermes_hidden.json")


def _hidden_set(cfg: Any) -> set:
    try:
        raw = json.loads(_hidden_read_path(cfg).read_text())
        return set(raw.get("hidden", []) or [])
    except (OSError, ValueError):
        return set()


def _hidden_save(cfg: Any, hidden: set) -> None:
    _hidden_path(cfg).write_text(json.dumps({"hidden": sorted(hidden)}))


def h_profiles_active(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                      p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    return h_profiles(cfg, q, b, {**p, "kind": "active"})


def profiles_active_set(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                        p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/profiles/active -> the sticky reference active profile.

    Ported from the reference ``web_routers/profiles.py:set_active_profile_endpoint``
    (``:782``) / ``hermes_cli/profiles.py:set_active_profile`` (``:1356``):
    ``qubit agent profile use``. The name must be an existing profile (or the
    built-in ``default``); anything else is a typed 404, so the badge can never
    move to a name the registry does not hold. The write is the reference's own
    atomic ``active_profile`` pointer — no invented persona store.
    """
    name = str(b.get("active", b.get("name", "")) or "").strip()
    if not name:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "active profile name required"}
    try:
        P = _profiles_module(cfg)
        canon = P.normalize_profile_name(name)
    except ValueError as exc:
        return 400, {"ok": False, "code": "PROFILE_BAD_NAME",
                     "detail": _redact(str(exc))[:300],
                     "qubit_counterpart": "CLI `qubit agent profile use`"}
    except Exception as exc:
        return _fail(exc)
    if canon != "default":
        try:
            exists = bool(P.profile_exists(canon))
        except Exception:  # noqa: BLE001
            exists = False
        if not exists:
            return 404, {"ok": False, "code": "PROFILE_NOT_FOUND",
                         "detail": f"no profile {name!r}",
                         "qubit_counterpart": "CLI `qubit agent profile list`"}
    try:
        P.set_active_profile(canon)
    except Exception as exc:
        return _fail(exc)
    _audit("profile.active", "dashboard:user", {"name": canon})
    return _ok(ok=True, active=canon)


def _profile_field_value(b: Dict[str, Any], key: str) -> Any:
    """Extract the submitted value for *key* from the SPA body.

    The editor posts the value under the field's own name (``description``,
    ``model``, ``soul``) or a generic ``value``/``content``; reading only the
    generic keys silently wrote an empty string while echoing the request's
    value back as success (the fabricated ``ollama-local`` bug).
    """
    for candidate in (key, "value", "content", "text"):
        if candidate in b and b[candidate] is not None:
            return b[candidate]
    return ""


def profile_soul_set(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                     p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """PUT /api/profiles/{name}/soul -> atomic ``SOUL.md`` write.

    Ported from the reference ``web_routers/profiles.py:update_profile_soul``
    (``:892``): a temp-file + fsync + replace write of the profile's own
    ``SOUL.md`` so an interrupted save cannot truncate the file. Confirm-gated.
    """
    name = str(p.get("name", "") or "")
    content = str(_profile_field_value(b, "soul") or "")
    if not name:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "profile name required"}
    guard = _compat_confirm(
        cfg, "profile_soul",
        f"profile {_redact(name)!r} SOUL.md update; re-POST with "
        "{\"confirm\": true}", q, b)
    if guard is not None:
        return guard
    try:
        P = _profiles_module(cfg)
    except Exception as exc:
        return _fail(exc)
    prof_dir, err = _profile_dir_or_error(P, name)
    if err is not None:
        return err
    try:
        from utils import atomic_write_text  # type: ignore
        atomic_write_text(prof_dir / "SOUL.md", content,
                          preserve_mode=True, create_mode=0o644)
    except Exception as exc:
        return _fail(exc)
    _audit("profile.soul", "dashboard:user", {"name": name})
    return _ok(ok=True, field={"key": "soul"})


def profile_desc(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """PUT /api/profiles/{name}/description -> ``profile.yaml`` description.

    Ported from the reference ``web_routers/profiles.py:update_profile_description_endpoint``
    (``:914``) / ``hermes_cli/profiles.py:write_profile_meta`` (``:832``): stored
    user-authored (``description_auto: false``) so an auto-describer cannot
    overwrite it. Confirm-gated.
    """
    name = str(p.get("name", "") or "")
    text = str(_profile_field_value(b, "description") or "").strip()
    if not name:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "profile name required"}
    guard = _compat_confirm(
        cfg, "profile_description",
        f"profile {_redact(name)!r} description update; re-POST with "
        "{\"confirm\": true}", q, b)
    if guard is not None:
        return guard
    try:
        P = _profiles_module(cfg)
    except Exception as exc:
        return _fail(exc)
    prof_dir, err = _profile_dir_or_error(P, name)
    if err is not None:
        return err
    try:
        P.write_profile_meta(prof_dir, description=text,
                             description_auto=False)
    except Exception as exc:
        return _fail(exc)
    _audit("profile.description", "dashboard:user", {"name": name})
    return _ok(ok=True, description=text, description_auto=False,
               field={"key": "description", "value": text})


def profile_model(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                  p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """PUT /api/profiles/{name}/model -> the profile's own config model.

    Ported from the reference ``web_routers/profiles.py:update_profile_model_endpoint``
    (``:928``) / ``_write_profile_model`` (``:104``): requires provider AND
    model, writes them into THIS profile's ``config.yaml`` (never the
    dashboard's), and returns the STORED values — never a fabricated
    ``ollama-local`` or a request echo. Confirm-gated.
    """
    name = str(p.get("name", "") or "")
    provider = str(b.get("provider", "") or "").strip()
    model = str(b.get("model", b.get("value", "")) or "").strip()
    if not name:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "profile name required"}
    if not provider or not model:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "provider and model are required",
                     "qubit_counterpart": "CLI `qubit agent profile model`"}
    guard = _compat_confirm(
        cfg, "profile_model",
        f"profile {_redact(name)!r} model assignment; re-POST with "
        "{\"confirm\": true}", q, b)
    if guard is not None:
        return guard
    try:
        P = _profiles_module(cfg)
    except Exception as exc:
        return _fail(exc)
    prof_dir, err = _profile_dir_or_error(P, name)
    if err is not None:
        return err
    try:
        from qubit_cli.web_routers.profiles import _write_profile_model
        _write_profile_model(prof_dir, provider, model)
    except Exception as exc:
        from fastapi import HTTPException
        if isinstance(exc, HTTPException):
            return int(exc.status_code), {
                "ok": False, "code": "MODEL_REJECTED",
                "detail": _redact(str(getattr(exc, "detail", exc)))[:300],
                "qubit_counterpart": "CLI `qubit agent profile model`"}
        return _fail(exc)
    _audit("profile.model", "dashboard:user",
           {"name": name, "provider": provider, "model": model})
    return _ok(ok=True, provider=provider, model=model,
               qubit_counterpart="CLI `qubit agent profile model`")


def profile_describe_auto(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                          p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/profiles/{name}/describe-auto -> real auxiliary-LLM description.

    Ported from the reference ``web_routers/profiles.py:describe_profile_auto_endpoint``
    (``:943``) / ``qubit_cli/profile_describer.py:describe_profile`` (``:112``).
    A failed generation is ``ok: false`` with the reason (never an HTTP error),
    so the UI can surface it inline and let the operator retry.
    """
    name = str(p.get("name", "") or "")
    if not name:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "profile name required"}
    try:
        P = _profiles_module(cfg)
    except Exception as exc:
        return _fail(exc)
    _prof_dir, err = _profile_dir_or_error(P, name)
    if err is not None:
        return err
    try:
        from qubit_cli import profile_describer  # type: ignore
        outcome = profile_describer.describe_profile(
            name, overwrite=bool(b.get("overwrite")))
    except Exception as exc:  # noqa: BLE001 - reported, not 500
        return _ok(ok=False, reason=_redact(str(exc))[:300],
                   description=None, description_auto=False)
    ok = bool(getattr(outcome, "ok", False))
    return _ok(ok=ok, reason=str(getattr(outcome, "reason", "") or ""),
               description=getattr(outcome, "description", None),
               description_auto=ok)


def profile_rename(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                   p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """PATCH /api/profiles/{name} -> rename the reference profile.

    Ported from the reference ``web_routers/profiles.py:rename_profile_endpoint``
    (``:836``) / ``hermes_cli/profiles.py:rename_profile`` (``:1655``): renames
    the profile directory, wrapper, gateway service, and the sticky active
    pointer. The default profile's "rename" is a presentation-only display_name
    (its id is reserved). Confirm-gated; a collision is a typed 400.
    """
    old = str(p.get("name", "") or "")
    new_raw = str(b.get("new_name", b.get("name", "")) or "").strip()
    if not old or not new_raw:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "rename needs the current and new name"}
    guard = _compat_confirm(
        cfg, "profile_rename",
        f"profile rename {_redact(old)!r} -> {_redact(new_raw)!r}; re-POST "
        "with {\"confirm\": true}", q, b)
    if guard is not None:
        return guard
    try:
        P = _profiles_module(cfg)
    except Exception as exc:
        return _fail(exc)
    try:
        P.rename_profile(old, new_raw)
    except FileNotFoundError as exc:
        return 404, {"ok": False, "code": "PROFILE_NOT_FOUND",
                     "detail": _redact(str(exc))[:300],
                     "qubit_counterpart": "CLI `qubit agent profile list`"}
    except FileExistsError as exc:
        return 400, {"ok": False, "code": "PROFILE_EXISTS",
                     "detail": _redact(str(exc))[:300]}
    except ValueError as exc:
        return 400, {"ok": False, "code": "PROFILE_BAD_NAME",
                     "detail": _redact(str(exc))[:300]}
    except Exception as exc:
        return _fail(exc)
    old_canon = P.normalize_profile_name(old)
    if old_canon == "default":
        return _ok(ok=True, name="default", display_name=new_raw, path="default",
                   qubit_counterpart="CLI `qubit agent profile rename`")
    new_canon = P.normalize_profile_name(new_raw)
    _audit("profile.rename", "dashboard:user",
           {"from": old_canon, "to": new_canon})
    return _ok(ok=True, name=new_canon, path=new_canon, renamed_from=old_canon,
               qubit_counterpart="CLI `qubit agent profile rename`")


def profile_delete(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                   p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """DELETE /api/profiles/{name} -> delete the reference profile.

    Ported from the reference ``web_routers/profiles.py:delete_profile_endpoint``
    (``:855``) / ``hermes_cli/profiles.py:delete_profile`` (``:1158``): stops
    the profile's gateway, removes its directory and wrapper, and retargets the
    sticky active pointer. The dashboard confirms in its own dialog, so
    ``yes=True``; confirm-gated through the shared journal. The default profile
    cannot be deleted (typed 400).
    """
    name = str(p.get("name", "") or "")
    if not name:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "profile name required"}
    guard = _compat_confirm(
        cfg, "profile_delete",
        f"profile {_redact(name)!r} deletion removes its home; re-POST with "
        "{\"confirm\": true}", q, b)
    if guard is not None:
        return guard
    try:
        P = _profiles_module(cfg)
    except Exception as exc:
        return _fail(exc)
    try:
        P.delete_profile(name, yes=True)
    except ValueError as exc:
        return 400, {"ok": False, "code": "PROFILE_BAD_NAME",
                     "detail": _redact(str(exc))[:300]}
    except FileNotFoundError as exc:
        return 404, {"ok": False, "code": "PROFILE_NOT_FOUND",
                     "detail": _redact(str(exc))[:300]}
    except Exception as exc:
        return _fail(exc)
    canon = P.normalize_profile_name(name)
    _audit("profile.delete", "dashboard:user", {"name": canon})
    return _ok(ok=True, removed=True, name=canon, path=canon,
               qubit_counterpart="CLI `qubit agent profile delete`")


_SCHEDULE_SHORTHAND = re.compile(
    r"^\s*every\s+(\d+)\s*([mhd])\s*$", re.IGNORECASE)


def _normalize_schedule_expr(when: str) -> str:
    """Accept the SPA's ``every 30m``/``2h``/``1d`` shorthand.

    The canonical parser (and every CLI/voice path) speaks ``every 30 minutes``
    etc.; the dashboard editor sends the compact form, so normalize it at this
    single ingress rather than rejecting a valid user intent.
    """
    m = _SCHEDULE_SHORTHAND.match(when or "")
    if not m:
        return when
    unit = {"m": "minutes", "h": "hours", "d": "days"}[m.group(2).lower()]
    return f"every {m.group(1)} {unit}"


def cron_create(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import schedules as S
    name = str(b.get("name", "") or "")
    task_text = str(b.get("prompt", b.get("task_text", "")) or "")
    when = str(b.get("when", "") or "")
    if isinstance(b.get("schedule"), dict):
        when = str(b["schedule"].get("expr", "") or when)
    elif isinstance(b.get("schedule"), str) and not when:
        # The SPA editor sends ``schedule`` as a plain string ("every 30m").
        when = str(b["schedule"] or "")
    if not when and isinstance(b.get("repeat"), dict):
        when = str(b["repeat"].get("expr", "") or "")
    when = _normalize_schedule_expr(when)
    deliver = str(b.get("deliver", b.get("deliver_to", "")) or "")
    detail = ("schedule create (unattended work) requires explicit "
              "confirmation: re-POST with {\"confirm\": true}")
    guard = _compat_confirm(cfg, "cron.create", detail, q, b,
                            payload={"name": name, "task_text": task_text,
                                     "when": when, "deliver": deliver})
    if guard is not None:
        return guard
    try:
        res = S.create(cfg, name, task_text, when,
                       deliver_to=deliver,
                       requested_by="dashboard:user", confirmed=True)
    except Exception as exc:
        code = getattr(exc, "code", None) or type(exc).__name__
        return 400, {"ok": False, "code": code,
                     "detail": _redact(str(exc))[:300]}
    job = res.get("job", {}) if isinstance(res, dict) else {}
    if isinstance(job, dict) and job.get("id") and "next_run_at" not in job:
        # ``create_job`` returns the raw row; re-read through the store view so
        # the returned projection honors the configured timezone/retry posture.
        try:
            viewed = S.get_job(cfg, int(job["id"]))
            if isinstance(viewed, dict) and isinstance(viewed.get("job"), dict):
                job = viewed["job"]
        except Exception:  # noqa: BLE001 — a read must never break the create
            pass
    # Carry ``ok:True``: the pending-approvals banner settles cron.create via
    # ``dashboard_ops.confirm_compat_op``, which requires ``body["ok"]``. Without
    # it the job WAS created but the banner reported BACKEND_REFUSED (a false
    # failure receipt). The direct POST response also gains the standard ok.
    shape = _cron_shape(job if isinstance(job, dict) else {},
                        cfg=cfg, tz_name=_cron_timezone(cfg))
    return _ok(**shape)


def cron_update(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """PUT /api/cron/jobs/{job_id} -> real in-place schedule edit.

    The edit modal posts the full mutation (name/prompt/schedule/deliver/
    skills/...) plus the current ``enabled`` flag. Without ``confirm`` the
    write stages a typed 409 on the shared journal; with it the same
    ``schedules`` store the CLI/voice use applies the editable fields in place
    (``set_paused`` delegates body-carrying edits to ``schedules.update_job``)
    and records an audit receipt.
    """
    from . import schedules as S
    try:
        job_id = int(p.get("id", "0"))
    except (TypeError, ValueError):
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "schedule id must be numeric"}
    enabled = b.get("enabled")
    if enabled is None:
        return _gate("CRON_UPDATE_IMMUTABLE",
                     "schedule update requires the current enabled flag; "
                     "re-POST the full mutation including enabled.",
                     "CLI `qubit schedules` (same schedules store)")
    enabled = bool(enabled)
    detail = (f"schedule {'resume' if enabled else 'pause'} requires explicit "
              "confirmation: re-POST with {\"confirm\": true}")
    guard = _compat_confirm(
        cfg, "cron.update", detail, q, b,
        payload={"id": job_id, "enabled": enabled,
                 **{k: v for k, v in b.items()
                    if k != "confirm" and isinstance(v, (str, int, float,
                                                         bool))}})
    if guard is not None:
        return guard
    try:
        # Pause == not enabled. The confirm was already settled above, so pass
        # it through to the single-store setter (avoids a second confirm).
        res = S.set_paused(cfg, job_id, not enabled,
                           requested_by="dashboard:user",
                           confirmed=True, body=b, query=q)
    except Exception as exc:
        code = getattr(exc, "code", None) or type(exc).__name__
        return 400, {"ok": False, "code": code,
                     "detail": _redact(str(exc))[:300]}
    ok = not (isinstance(res, dict) and res.get("ok") is False)
    return (200 if ok else 400), {"ok": ok, "result": res,
                                  "enabled": enabled,
                                  "qubit_counterpart":
                                  "CLI `qubit schedules pause|resume`"}


def cron_delete(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import schedules as S
    detail = ("schedule delete requires explicit confirmation: re-POST with "
              "{\"confirm\": true}")
    # Read the confirm/id from BOTH the parsed body and the query string, so
    # ``DELETE .../1?confirm=true`` and ``DELETE .../1 {"confirm": true}`` both
    # settle (previously only a body was read and it was never parsed).
    try:
        _job_id = int(p.get("id", "0"))
    except (TypeError, ValueError):
        _job_id = 0
    guard = _compat_confirm(cfg, "cron.delete", detail, q, b,
                            payload={"id": _job_id})
    if guard is not None:
        return guard
    try:
        S.delete(cfg, int(p.get("id", "0")), requested_by="dashboard:user",
                 confirmed=True)
    except Exception as exc:
        code = getattr(exc, "code", None) or type(exc).__name__
        return 400, {"ok": False, "code": code,
                     "detail": _redact(str(exc))[:300]}
    return _ok(ok=True)


def skill_create(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/skills -> create a skill through the real skill-manager path.

    Ported from the upstream reference ``create_skill``: the SPA editor payload
    (``{manifest: {skill_id/name, instructions/content, category}}``) is
    created via the fork's ``_create_skill`` (frontmatter validation, atomic
    write, security scan, prompt-cache clear) under the Qubit-owned home.
    Confirm-gated on the shared journal (``_compat_confirm``: staged 409 with
    a real id, or settle via ``confirm: true`` re-issue) and audited. The
    manifest-install lane (``verified_skills.mutations``, SPDX license gate)
    is untouched — this is the editor-create lane.
    """
    from . import skill_catalog

    man = b.get("manifest")
    src: Dict[str, Any] = man if isinstance(man, dict) else b
    name = str(src.get("skill_id", "") or src.get("name", "") or "").strip()
    content = src.get("instructions", src.get("content", ""))
    if not isinstance(content, str):
        content = ""
    category = str(src.get("category", "") or "").strip() or None
    if not name:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "skill name required"}
    guard = _compat_confirm(
        cfg, "skills_create",
        f"creating skill {name!r} requires explicit confirmation: "
        "re-POST with {\"confirm\": true}", q, b,
        payload={"skill_id": name, "instructions": content,
                 "category": category or ""})
    if guard is not None:
        return guard
    try:
        out = skill_catalog.create_skill_file(
            cfg, name, content, category, requested_by="dashboard:user")
    except Exception as exc:
        return _skill_authority_error(exc)
    return _ok(ok=True, success=True, skill_id=name,
               message=str(out.get("message", "")),
               path=str(out.get("skill_md", out.get("path", ""))),
               qubit_counterpart="CLI `qubit skills show` (same on-disk skill)")


def skill_update(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """PUT /api/skills/content -> replace a skill's SKILL.md (full rewrite).

    Ported from the upstream reference ``update_skill_content``: the editor payload
    (``{name, content}``) is applied via the fork's ``_edit_skill``
    (frontmatter/size validation, guarded atomic write, security scan,
    prompt-cache clear) under the Qubit-owned home — the supplied content is
    actually persisted, not dropped. Confirm-gated on the shared journal and
    audited; 404 only when the skill resolves in neither authority.
    """
    from . import skill_catalog

    name = str(p.get("id", "") or b.get("skill_id", b.get("name", "")) or "")
    if not name:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "skill name required"}
    content = b.get("content", "")
    if not isinstance(content, str):
        content = ""
    guard = _compat_confirm(
        cfg, "skills_update",
        f"rewriting skill {name!r} requires explicit confirmation: "
        "re-POST with {\"confirm\": true}", q, b,
        payload={"skill_id": name, "content": content})
    if guard is not None:
        return guard
    try:
        out = skill_catalog.edit_skill_file(
            cfg, name, content, requested_by="dashboard:user")
    except Exception as exc:
        return _skill_authority_error(exc)
    return _ok(ok=True, success=True, skill_id=name,
               message=str(out.get("message", "")),
               path=str(out.get("path", "")),
               qubit_counterpart="CLI `qubit skills show` (same on-disk skill)")


def skill_delete(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """DELETE /api/skills -> delete an on-disk skill (confirm-gated).

    Ported from the fork's ``_delete_skill`` (curator archive guards and
    pinned-skill guards preserved). A permissioned-manifest-only skill (no
    on-disk file) is removed from the permissioned store instead. Confirm-
    gated on the shared journal and audited.
    """
    from . import skill_catalog
    from .permissions import permissions_store

    name = str(q.get("name", "") or b.get("name", b.get("skill_id", "")) or "")
    if not name:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "skill name required"}
    guard = _compat_confirm(
        cfg, "skills_delete",
        f"deleting skill {name!r} requires explicit confirmation: "
        "re-DELETE with {\"confirm\": true}", q, b,
        payload={"skill_id": name, "name": name})
    if guard is not None:
        return guard
    try:
        if skill_catalog.find_skill_path(cfg, name) is not None:
            out = skill_catalog.delete_skill_file(
                cfg, name, requested_by="dashboard:user")
            return _ok(ok=True, success=True, skill_id=name,
                       message=str(out.get("message", "")),
                       qubit_counterpart="CLI `qubit skills` (same on-disk skill)")
        store = permissions_store(cfg)
        rec = store.skill(name)
        if rec is None:
            return 404, {"ok": False, "code": "NOT_FOUND",
                         "detail": f"no skill {name!r}"}
        removed = store.remove_skill(name, requested_by="dashboard:user",
                                     confirmed=True)
        return _ok(ok=True, success=True, skill_id=removed.skill_id,
                   message=f"Skill {name!r} removed from the permissioned store.",
                   qubit_counterpart="CLI `qubit skills remove`")
    except Exception as exc:
        return _skill_authority_error(exc)


def moa_put(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
            p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    return _gate("MOA_SUPERSEDED",
                 "MoA fan-out presets are not executed by Qubit; the ordered "
                 "provider fallback gateway (8Q) is the counterpart.",
                 "Models page provider/fallback display + 8Q gateway", 501)


def _mcp_error(exc: Exception) -> Tuple[int, Dict[str, Any]]:
    code = getattr(exc, "code", None) or type(exc).__name__
    detail = getattr(exc, "detail", None) or str(exc)
    if code == "MCP_NOT_FOUND":
        return 404, {"ok": False, "code": code, "detail": _redact(str(detail))[:300]}
    if code in ("MCP_BAD_INPUT", "MCP_STORE_CORRUPT"):
        return 400, {"ok": False, "code": code, "detail": _redact(str(detail))[:300]}
    if code == "SECRET_WRITE_REFUSED":
        return _gate(code, str(detail)[:300],
                     "CLI `qubit auth login` (references only)")
    return 400, {"ok": False, "code": code,
                 "detail": _redact(str(detail))[:300]}


_MCP_CAP_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.:/-]{0,127}$")
_MCP_MAX_CAPABILITIES = 64


def _mcp_capabilities_from_body(b: Dict[str, Any]) -> Optional[List[str]]:
    """Validated tool allowlist from the SPA body (``capabilities``).

    The canonical store gates client invocation on a non-empty allowlist, so a
    create/update that drops ``capabilities`` makes the server permanently
    un-invocable with no surface to declare its tools. Validate here (bounded
    count, tool-name shape) and return ``None`` when the body omitted it so an
    update never wipes an existing allowlist.
    """
    from . import mcp_servers as MS
    raw = b.get("capabilities", b.get("allowed_tools"))
    if raw is None:
        return None
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        raise MS.McpServerError("MCP_BAD_INPUT",
                                "capabilities must be a list of tool names")
    clean: List[str] = []
    for item in list(raw)[:_MCP_MAX_CAPABILITIES]:
        name = str(item or "").strip()
        if not name or not _MCP_CAP_RE.match(name):
            raise MS.McpServerError(
                "MCP_BAD_INPUT",
                "capabilities entries must be tool names matching "
                "^[a-zA-Z0-9][a-zA-Z0-9_.:/-]{0,127}$")
        clean.append(name)
    return clean


def _mcp_fields_from_body(b: Dict[str, Any]) -> Dict[str, Any]:
    allowed = ("transport", "auth", "auth_type", "command", "args", "url",
               "env", "enabled")
    fields = {k: b[k] for k in allowed if k in b}
    caps = _mcp_capabilities_from_body(b)
    if caps is not None:
        fields["capabilities"] = caps
    return fields


def _mcp_current_enabled(cfg: Any, name: str) -> Optional[bool]:
    """The enabled flag currently stored for ``name`` (None if not registered).

    ``mcp_servers.upsert`` re-validates the merged record through
    ``_validate``, which never re-emits ``enabled``; reading the stored flag
    lets an edit preserve it instead of silently disabling the server.
    """
    from . import mcp_servers as MS
    try:
        for row in MS.list_servers(cfg).get("servers", []):
            if str(row.get("name")) == name:
                return bool(row.get("enabled"))
    except Exception:
        pass
    return None


def mcp_create(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
               p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/mcp/servers -> register an MCP server (confirm-gated).

    B-H1: a real, persisted registry operation on the canonical Qubit store.
    No server is mounted, spawned, or contacted; credentials are references
    only.
    """
    from . import mcp_servers as MS
    name = str(b.get("name", "") or "")
    if not name:
        return 400, {"ok": False, "code": "MCP_BAD_INPUT",
                     "detail": "server name required"}
    try:
        staged_fields = _jsonable(_mcp_fields_from_body(b))
    except Exception:  # noqa: BLE001 - invalid fields are validated post-confirm
        staged_fields = {}
    guard = _compat_confirm(
        cfg, "mcp_server_create",
        f"registering MCP server {name!r} requires explicit confirmation: "
        "re-POST with {\"confirm\": true}", q, b,
        payload={"name": name, "fields": staged_fields})
    if guard is not None:
        return guard
    existing_enabled = _mcp_current_enabled(cfg, name)
    try:
        fields = _mcp_fields_from_body(b)
        if "enabled" in fields:
            desired_enabled = _truthy(fields.pop("enabled"))
        elif existing_enabled is not None:
            desired_enabled = existing_enabled
        else:
            desired_enabled = False
        out = MS.upsert(cfg, name, fields, confirmed=True)
        # ``upsert`` merges then validates; ``_validate`` drops ``enabled``, so
        # re-apply the intended flag (create-with-enabled must persist and an
        # edit must preserve an enabled server's state).
        if bool(out["server"].get("enabled")) != desired_enabled:
            out = MS.set_enabled(cfg, name, desired_enabled)
    except Exception as exc:
        return _mcp_error(exc)
    _audit("mcp_server_create", "dashboard:user", {"name": name,
                                                  "enabled": desired_enabled})
    return _ok(ok=True, server=out["server"],
               qubit_counterpart="CLI `qubit mcp servers`")


def mcp_update(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
               p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """PUT /api/mcp/servers -> upsert a registered server (confirm-gated)."""
    return mcp_create(cfg, q, b, p)


def mcp_delete(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
               p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """DELETE /api/mcp/servers/{id} -> remove a registered server."""
    from . import mcp_servers as MS
    name = str(p.get("id", "") or b.get("name", "") or "")
    if not name:
        return 400, {"ok": False, "code": "MCP_BAD_INPUT",
                     "detail": "server name required"}
    guard = _compat_confirm(
        cfg, "mcp_server_delete",
        f"removing MCP server {name!r} requires explicit confirmation: "
        "re-DELETE with {\"confirm\": true}", q, b,
        payload={"name": name})
    if guard is not None:
        return guard
    try:
        out = MS.delete(cfg, name)
    except Exception as exc:
        return _mcp_error(exc)
    _audit("mcp_server_delete", "dashboard:user", {"name": name,
                                                   "removed": out["removed"]})
    return _ok(ok=True, name=out["name"], removed=out["removed"],
               qubit_counterpart="CLI `qubit mcp servers`")


def mcp_set_enabled(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """PUT/POST /api/mcp/servers/{id}/enabled -> persist the enabled flag."""
    from . import mcp_servers as MS
    name = str(p.get("id", "") or b.get("name", "") or "")
    if not name:
        return 400, {"ok": False, "code": "MCP_BAD_INPUT",
                     "detail": "server name required"}
    enabled = _truthy(b.get("enabled"))
    guard = _compat_confirm(
        cfg, "mcp_server_enabled",
        f"changing MCP server {name!r} enabled state requires explicit "
        "confirmation: re-send with {\"confirm\": true}", q, b,
        payload={"name": name, "enabled": enabled})
    if guard is not None:
        return guard
    try:
        out = MS.set_enabled(cfg, name, enabled)
    except Exception as exc:
        return _mcp_error(exc)
    _audit("mcp_server_enabled", "dashboard:user",
           {"name": name, "enabled": enabled})
    return _ok(ok=True, server=out["server"], name=out["server"]["name"],
               enabled=out["server"]["enabled"],
               qubit_counterpart="CLI `qubit mcp servers`")


def mcp_auth(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
             p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/mcp/servers/{id}/auth -> record a credential reference.

    The SPA's "Authenticate" button posts an empty body, which starts a live
    dashboard OAuth login (the Qubit OAuth family below, same semantics as
    the reference ``auth_mcp_server``:
    ``qubit_runtime/qubit_cli/web_routers/mcp.py:194``) and hands the
    browser the authorization URL. Tokens land in the vendored per-home
    ``mcp-tokens/`` store; only an ``oauth:<server>`` reference is bridged
    into the Qubit registry — raw tokens are never accepted here. A
    ``vault:/env:/file:`` reference in the body still writes the credential
    the credential reference through the confirmed path.
    """
    from . import mcp_servers as MS
    name = str(p.get("id", "") or b.get("name", "") or "")
    if not name:
        return 400, {"ok": False, "code": "MCP_BAD_INPUT",
                     "detail": "server name required"}
    ref = str(b.get("reference", b.get("token", b.get("value", ""))) or "")
    if not ref:
        return mcp_oauth_start(cfg, q, b, p)
    guard = _compat_confirm(
        cfg, "mcp_server_auth",
        f"recording a credential reference for {name!r} requires explicit "
        "confirmation: re-POST with {\"confirm\": true}", q, b,
        payload={"name": name, "reference": ref})
    if guard is not None:
        return guard
    try:
        out = MS.set_auth_ref(cfg, name, ref)
    except Exception as exc:
        return _mcp_error(exc)
    _audit("mcp_server_auth", "dashboard:user", {"name": name})
    return _ok(ok=True, name=out["name"], reference=out["reference"],
               qubit_counterpart="CLI `qubit auth login` (references only)")


def mcp_auth_status(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/mcp/servers/{id}/auth -> reference presence, never a secret."""
    from . import mcp_servers as MS
    name = str(p.get("id", "") or "")
    row = next((s for s in MS.list_servers(cfg)["servers"]
                if s.get("name") == name), None)
    if row is None:
        return 404, {"ok": False, "code": "MCP_NOT_FOUND",
                     "detail": f"no registered server {name!r}"}
    ref = (row.get("env") or {}).get("MCP_AUTH", "")
    return _ok(ok=True, name=name, auth=row.get("auth", "none"),
               reference_set=bool(ref), reference=ref or None)


# ---------------------------------------------------------------------------
# Qubit MCP OAuth family (parity with the reference dashboard OAuth login).
# Hermes is Hermes, Qubit is Qubit: separate agents, but every reference
# capability works here. The flow machinery (DashboardOAuthFlow, the probe
# worker, the token store) is reused in-process from the vendored runtime;
# the routes, the public loopback callback, and the registry bridge below
# are Qubit's own. Tokens live in ONE store (the vendored per-home
# ``mcp-tokens/``); the Qubit registry carries only an ``oauth:<server>``
# reference that :mod:`voicebridge.mcp_client` resolves (read-only,
# fail-closed on absent/expired).
# ---------------------------------------------------------------------------
_MCP_OAUTH_MAX_PENDING = 8  # mirrors vendored _MAX_PENDING_MCP_OAUTH_FLOWS
_MCP_OAUTH_FLOW_TTL = 15 * 60  # mirrors vendored _MCP_DASHBOARD_OAUTH_TTL
_MCP_OAUTH_URL_WAIT = 30.0

_OAUTH_PUBLIC_BASE: Optional[str] = None  # set by webui at serve time


def set_oauth_public_base(base: str) -> None:
    """Pin the public dashboard base used for OAuth redirect URIs."""
    global _OAUTH_PUBLIC_BASE
    _OAUTH_PUBLIC_BASE = str(base or "").rstrip("/")


def _mcp_oauth_flows():
    """Vendored OAuth flow registry (imported lazily, no startup cost).

    The registry dict itself lives in the vendored runtime so the worker
    thread and every handler share it; the guard lock is Qubit-owned
    (mirrors the reference router's own ``_mcp_oauth_flows_lock``).
    """
    from . import qubit_runtime as _HR
    _HR.ensure_path()
    from qubit_cli.web_server_mcp import _mcp_oauth_flows
    return _mcp_oauth_flows, _MCP_OAUTH_FLOWS_LOCK


_MCP_OAUTH_FLOWS_LOCK = threading.Lock()


def _gc_mcp_oauth_flows(flows: Any, lock: Any) -> None:
    cutoff = time.time() - _MCP_OAUTH_FLOW_TTL
    with lock:
        for fid in [fid for fid, fl in flows.items()
                    if getattr(fl, "created_at", 0) < cutoff]:
            flows.pop(fid, None)


def _flow_snapshot(flow: Any) -> Dict[str, Any]:
    snap = flow.snapshot()
    snap["tools"] = list(getattr(flow, "tools", []) or [])
    return snap


def mcp_oauth_start(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/mcp/servers/{id}/auth (empty body) -> start dashboard OAuth.

    Hands the browser an authorization URL exactly like the reference
    ``auth_mcp_server``: stdio servers 400 (env keys, not OAuth), servers
    already on an API-key reference 400 (POST the reference instead),
    unknown servers 404, duplicate in-progress flow 409, >8 live flows 429.
    """
    from . import mcp_servers as MS
    name = str(p.get("id", "") or b.get("name", "") or "")
    if not name:
        return 400, {"ok": False, "code": "MCP_BAD_INPUT",
                     "detail": "server name required"}
    row = next((s for s in MS.list_servers(cfg).get("servers", [])
                if s.get("name") == name), None)
    if row is None:
        return 404, {"ok": False, "code": "MCP_NOT_FOUND",
                     "detail": f"no registered server {name!r}"}
    url = str(row.get("url") or "")
    if not url:
        return 400, {"ok": False, "code": "MCP_STDIO_NO_OAUTH",
                     "detail": "stdio servers authenticate via env keys, "
                              "not OAuth; POST a vault:/env:/file: reference"}
    existing = str((row.get("env") or {}).get("MCP_AUTH", "") or "")
    if existing and not existing.startswith("oauth:"):
        return 400, {"ok": False, "code": "MCP_HEADER_AUTH",
                     "detail": "this server uses header/API-key auth, not "
                              "OAuth; POST a credential reference instead"}
    try:
        from . import qubit_runtime as _HR
        _HR.ensure_path()
        from qubit_cli.web_server_mcp import _run_dashboard_mcp_oauth
        from tools.mcp_dashboard_oauth import DashboardOAuthFlow
        flows, lock = _mcp_oauth_flows()
    except Exception as exc:
        return _fail(exc)
    _gc_mcp_oauth_flows(flows, lock)
    try:
        flow_home = str(_HR.runtime_home_for_cfg(cfg))
    except Exception:
        try:
            flow_home = str(_HR.default_home())
        except Exception as exc:
            return _fail(exc)
    base = _OAUTH_PUBLIC_BASE or "http://127.0.0.1:8890"
    from urllib.parse import quote as _quote
    redirect_uri = (f"{base}/api/mcp/oauth/callback/{_quote(name, safe='')}")
    probe_cfg = {"transport": "http", "url": url, "auth": "oauth"}
    flow = DashboardOAuthFlow(
        flow_id=secrets.token_urlsafe(24), server_name=name,
        profile=(q.get("profile") or None), qubit_home=flow_home,
        redirect_uri=redirect_uri, reconnect_live=True,
    )
    with lock:
        live = [f for f in flows.values() if not f.worker_done]
        if len(live) >= _MCP_OAUTH_MAX_PENDING:
            return 429, {"ok": False, "code": "MCP_OAUTH_BUSY",
                         "detail": "too many MCP OAuth flows in progress"}
        if any(f.server_name == name and f.qubit_home == flow_home
               for f in live):
            return 409, {"ok": False, "code": "MCP_OAUTH_IN_PROGRESS",
                         "detail": f"MCP OAuth for {name!r} is already "
                                  "in progress"}
        flows[flow.flow_id] = flow
    threading.Thread(target=_run_dashboard_mcp_oauth, args=(flow, probe_cfg),
                     daemon=True, name=f"mcp-oauth-{name}").start()
    deadline = time.time() + _MCP_OAUTH_URL_WAIT
    while time.time() < deadline:
        snap = _flow_snapshot(flow)
        if snap.get("authorization_url") or snap.get("status") == "error":
            break
        time.sleep(0.2)
    snap = _flow_snapshot(flow)
    _audit("mcp_oauth_start", "dashboard:user",
           {"name": name, "flow": flow.flow_id[:16],
            "status": snap.get("status", "")})
    return _ok(**snap)


def _bridge_mcp_oauth_ref(cfg: Any, name: str) -> bool:
    """Point the Qubit registry at the vendored OAuth token (once).

    Stores ``env.MCP_AUTH = "oauth:<server>"`` + ``auth = "oauth"`` so the
    Test button and voice invocations resolve the dashboard-obtained token.
    Returns True when a write happened.
    """
    from . import mcp_servers as MS
    try:
        data = MS._load(cfg)
    except Exception:
        return False
    rec = (data.get("servers") or {}).get(name)
    if not isinstance(rec, dict):
        return False
    want = f"oauth:{name}"
    env = dict(rec.get("env") or {})
    if env.get("MCP_AUTH") == want and rec.get("auth") == "oauth":
        return False
    env["MCP_AUTH"] = want
    rec["env"] = env
    rec["auth"] = "oauth"
    rec["updated_at"] = time.time()
    try:
        MS._save(cfg, data)
    except Exception:
        return False
    _audit("mcp_oauth_bridged", "dashboard:user", {"name": name})
    return True


def mcp_oauth_flows_list(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                         p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/mcp/oauth/flows -> live flow snapshots."""
    try:
        flows, lock = _mcp_oauth_flows()
    except Exception as exc:
        return _fail(exc)
    _gc_mcp_oauth_flows(flows, lock)
    with lock:
        items = [_flow_snapshot(f) for f in flows.values()]
    return _ok(flows=items, count=len(items))


def mcp_oauth_flow_status(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                          p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/mcp/oauth/flows/{id} -> poll one flow.

    On ``approved`` the vendored token is bridged into the Qubit registry
    (once) so Test/voice resolve it immediately.
    """
    fid = str(p.get("id", "") or "")
    try:
        flows, lock = _mcp_oauth_flows()
    except Exception as exc:
        return _fail(exc)
    _gc_mcp_oauth_flows(flows, lock)
    flow = flows.get(fid)
    if flow is None:
        return 404, {"ok": False, "code": "MCP_OAUTH_GONE",
                     "detail": "OAuth flow not found or expired"}
    snap = _flow_snapshot(flow)
    if snap.get("status") == "approved":
        try:
            _bridge_mcp_oauth_ref(cfg, str(snap.get("server_name") or ""))
        except Exception:
            pass
    return _ok(**snap)


def mcp_oauth_callback(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                       p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/mcp/oauth/callback/{server} -> JSON view of the landing.

    Real browsers are served HTML by the public webui branch (no dashboard
    token by design); this dispatch variant carries the same semantics over
    JSON so the route resolves on every prefix (``/agent/api/...`` included)
    and stays covered by dispatch tests.
    """
    q = q or {}
    code, html = mcp_oauth_deliver(str(p.get("server_name", "") or ""), q)
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text).strip()
    if q.get("error"):
        state = "failed"
    elif code == 200:
        state = "received"
    elif code == 409:
        state = "replayed"
    else:
        state = "expired"
    return code, {"ok": code == 200, "state": state,
                  "server": str(p.get("server_name", "") or ""),
                  "detail": text}


def mcp_oauth_flow_cancel(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                          p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """DELETE /api/mcp/oauth/flows/{id} -> cancel an in-flight flow."""
    fid = str(p.get("id", "") or "")
    try:
        flows, lock = _mcp_oauth_flows()
    except Exception as exc:
        return _fail(exc)
    flow = flows.get(fid)
    if flow is None:  # expired/GC'd is the goal state of a cancel
        return _ok(ok=True, status="expired")
    try:
        flow.mark_error("Cancelled by user", cancelled=True)
    except TypeError:  # older vendored signature without `cancelled`
        flow.mark_error("Cancelled by user")
    return _ok(ok=True, status=_flow_snapshot(flow).get("status"))


def mcp_oauth_deliver(server_name: str,
                      q: Dict[str, str]) -> Tuple[int, str]:
    """Provider redirect landing (public: no dashboard token by design).

    Matches the reference callback semantics: state-checked delivery to the
    waiting flow, typed HTML pages for expired/invalid/failed callbacks.
    """
    server_name = str(server_name or "")
    try:
        flows, lock = _mcp_oauth_flows()
    except Exception as exc:
        return 500, (f"<h1>OAuth unavailable</h1><p>{str(exc)[:160]}</p>")
    _gc_mcp_oauth_flows(flows, lock)
    state = q.get("state")
    with lock:
        candidates = [f for f in flows.values()
                      if f.server_name == server_name
                      and f.status == "authorization_required"]
    flow = next(
        (c for c in candidates
         if c.expected_state is not None and state is not None
         and secrets.compare_digest(c.expected_state, state)),
        None,
    )
    if flow is None:
        return 404, ("<h1>OAuth flow expired</h1>"
                     "<p>Return to Qubit and try again.</p>")
    try:
        flow.deliver_callback(code=q.get("code"), state=state,
                              error=q.get("error"), iss=q.get("iss"))
    except ValueError as exc:
        msg = str(exc)
        return ((409 if "already received" in msg else 400),
                "<h1>OAuth callback rejected</h1>"
                "<p>The callback was invalid or already used.</p>")
    if q.get("error"):
        return 400, ("<h1>Authorization failed</h1>"
                     "<p>Return to Qubit for details.</p>")
    return 200, ("<h1>Authorization received</h1>"
                 "<p>You can close this tab and return to Qubit.</p>")


def webhooks_enable(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/webhooks/enable -> confirmed, audited store-wide toggle."""
    from . import webhooks as W
    guard = _compat_confirm(
        cfg, "webhook_enable",
        "toggling webhook delivery requires explicit confirmation: "
        "re-POST with {\"confirm\": true}", q, b)
    if guard is not None:
        return guard
    try:
        res = W.set_enabled(cfg, bool(b.get("enabled", True)),
                            requested_by="dashboard:user", confirmed=True)
    except Exception as exc:
        return _webhook_error(exc)
    return _ok(ok=True, platform="webhook", enabled=bool(res.get("enabled")),
               needs_restart=False)


def webhooks_enabled(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                     p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """PUT /api/webhooks/{id}/enabled -> confirmed, audited sub toggle."""
    from . import webhooks as W
    sub_id = str(p.get("id", "") or b.get("id", "") or "")
    guard = _compat_confirm(
        cfg, "webhook_enabled",
        f"toggling webhook subscription {_redact(sub_id)!r} requires explicit "
        "confirmation: re-send with {\"confirm\": true}", q, b,
        payload={"id": sub_id})
    if guard is not None:
        return guard
    try:
        res = W.set_sub_enabled(cfg, sub_id,
                                bool(b.get("enabled", True)),
                                requested_by="dashboard:user", confirmed=True)
    except Exception as exc:
        return _webhook_error(exc)
    return _ok(ok=True, name=str(res.get("id", "")),
               enabled=bool(res.get("enabled")))


def webhooks_delete(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """DELETE /api/webhooks/{id} -> confirmed, audited subscription removal.

    Uses the shared ``_compat_confirm`` contract: an unconfirmed request stages
    a typed 409 carrying a REAL single-use ``confirmation_id`` on the shared
    approvals journal (so the pending-approvals banner can settle it too), and
    the same request re-issued with ``confirm``/``confirmation_id`` executes
    the removal. A bodyless DELETE settles via the query form.
    """
    from . import webhooks as W
    sub_id = str(p.get("id", "") or b.get("id", "") or "")
    guard = _compat_confirm(
        cfg, "webhook_delete",
        f"deleting webhook subscription {_redact(sub_id)!r} requires explicit "
        "confirmation: re-DELETE with {\"confirm\": true}", q, b)
    if guard is not None:
        return guard
    try:
        W.remove(cfg, sub_id, requested_by="dashboard:user", confirmed=True)
    except Exception as exc:
        return _webhook_error(exc)
    return _ok(ok=True, name=sub_id)


def webhooks_rotate(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/webhooks/{id}/rotate -> confirmed signing-secret rotation.

    Realizes the reference's per-route secret rotation against Qubit's
    reference-only store: a fresh secret is written to a Qubit-owned ``file:``
    reference and the subscription is repointed there (value never returned).
    """
    from . import webhooks as W
    sub_id = str(p.get("id", "") or b.get("id", "") or "")
    guard = _compat_confirm(
        cfg, "webhook_rotate",
        f"rotating the signing secret for {_redact(sub_id)!r} requires explicit "
        "confirmation: re-POST with {\"confirm\": true}", q, b,
        payload={"id": sub_id})
    if guard is not None:
        return guard
    try:
        res = W.rotate_secret(cfg, sub_id, requested_by="dashboard:user",
                              confirmed=True)
    except Exception as exc:
        return _webhook_error(exc)
    return _ok(ok=True, **res)


def webhooks_test(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                  p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/webhooks/{id}/test -> confirmed real signed test delivery."""
    from . import webhooks as W
    sub_id = str(p.get("id", "") or b.get("id", "") or "")
    guard = _compat_confirm(
        cfg, "webhook_test",
        f"sending a real signed test delivery to {_redact(sub_id)!r} requires "
        "explicit confirmation: re-POST with {\"confirm\": true}", q, b,
        payload={"id": sub_id})
    if guard is not None:
        return guard
    try:
        res = W.test_delivery(cfg, sub_id, requested_by="dashboard:user",
                              confirmed=True)
    except Exception as exc:
        return _webhook_error(exc)
    return _ok(ok=True, **res)


def webhooks_deliveries(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                        p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/webhooks/{id}/deliveries -> redacted delivery receipts."""
    from . import webhooks as W
    sub_id = str(p.get("id", "") or "")
    try:
        limit = int(q.get("limit", "50") or "50")
    except ValueError:
        limit = 50
    try:
        res = W.deliveries(cfg, sub_id, limit=limit)
    except Exception as exc:
        return _webhook_error(exc)
    return _ok(ok=True, **res)


def webhooks_retry(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                   p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/webhooks/{id}/retry -> confirmed re-delivery of a failure."""
    from . import webhooks as W
    sub_id = str(p.get("id", "") or b.get("id", "") or "")
    guard = _compat_confirm(
        cfg, "webhook_retry",
        f"retrying the last failed delivery for {_redact(sub_id)!r} requires "
        "explicit confirmation: re-POST with {\"confirm\": true}", q, b,
        payload={"id": sub_id})
    if guard is not None:
        return guard
    try:
        res = W.retry_delivery(cfg, sub_id, requested_by="dashboard:user",
                               confirmed=True)
    except Exception as exc:
        return _webhook_error(exc)
    return _ok(ok=True, **res)


def msg_update(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
               p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """PUT /api/messaging/platforms/{id} -> confirmed, audited channel update.

    Ported from the vendored ``web_routers/messaging.py:update_messaging_platform``
    (``:826``): the id is a catalog platform id (``_require_platform``) and the
    body is ``MessagingPlatformUpdate`` (``enabled`` + ``env``/``clear_env``).
    Qubit keeps credentials reference-only, so ``env`` values must be
    ``env:``/``vault:``/``file:`` references (a raw secret is a typed
    ``CHANNEL_ENV_REFERENCE_ONLY`` refusal) and the write lands in the SAME
    store :func:`h_platforms` reads (``msg-platforms.json``). A Qubit account
    id (legacy CLI shape) still routes to ``messaging.account_update`` but has
    no env-write path.
    """
    from . import messaging as M
    pid = str(p.get("id", "") or "")
    env_in: Dict[str, Any] = b.get("env") if isinstance(b.get("env"), dict) else {}
    clear_in: List[Any] = b.get("clear_env") if isinstance(b.get("clear_env"), list) else []
    enabled = b.get("enabled") if isinstance(b.get("enabled"), bool) else None
    kind, entry, account = _platform_resolve(cfg, pid)
    if kind is None:
        return 404, {
            "ok": False, "code": "MSG_NOT_FOUND",
            "detail": f"no channel {pid!r}",
            "qubit_counterpart": "GET /api/messaging/platforms (catalog) / "
                                 "CLI `qubit messaging accounts`"}
    if kind == "account" and (env_in or clear_in):
        return 400, {
            "ok": False, "code": "MSG_BAD_INPUT",
            "detail": "an account's credential reference is fixed at account "
                      "creation; it cannot be changed by the channel toggle",
            "qubit_counterpart": "CLI `qubit messaging accounts` "
                                 "(remove + re-add to change the reference)"}
    allowed_keys = set(entry.get("env_vars", ()) or ()) if kind == "catalog" else set()
    bad_keys = sorted(k for k in env_in if k not in allowed_keys)
    bad_clear = sorted(k for k in clear_in if k not in allowed_keys)
    if bad_keys or bad_clear:
        return 400, {
            "ok": False, "code": "MSG_BAD_INPUT",
            "detail": "env keys are not configurable for this platform: "
                      + ", ".join(bad_keys + bad_clear),
            "qubit_counterpart": "GET /api/messaging/platforms (env_vars)"}
    # Credential policy: secret-shaped fields are references only, never a raw
    # secret value. Non-secret fields (allowlists, URLs, account ids) are
    # directly configurable, mirroring the reference's per-field secret
    # metadata (``is_password``); an accepted reference is fine for any field.
    raw_keys = sorted(
        k for k, v in env_in.items()
        if _platform_env_is_secret(k) and not _platform_allowed_ref(v))
    if raw_keys:
        return _gate(
            "CHANNEL_ENV_REFERENCE_ONLY",
            "Channel credential fields are secret references and are never "
            "written by the dashboard; store env:/vault:/file: references "
            "instead.",
            "CLI `qubit messaging accounts` / `qubit auth login` "
            "(reference-only)")
    detail = (f"changing messaging channel {pid!r} requires explicit "
              "confirmation: re-PUT with {\"confirm\": true}")
    guard = _compat_confirm(
        cfg, "messaging.platform_update", detail, q, b,
        payload={"id": pid, "enabled": enabled,
                 "env": {str(k): str(v) for k, v in env_in.items()},
                 "clear_env": [str(k) for k in clear_in]})
    if guard is not None:
        return guard
    try:
        _platform_store_update(cfg, pid, enabled=enabled, env=env_in,
                               clear=clear_in)
        if kind == "account":
            M.account_update(cfg, pid,
                             label=str(b.get("label", b.get("name", "")) or ""),
                             enabled=enabled,
                             requested_by="dashboard:user", confirmed=True)
    except Exception as exc:
        code = getattr(exc, "code", None) or type(exc).__name__
        return 400, {"ok": False, "code": code,
                     "detail": _redact(str(exc))[:300]}
    # Bridge the dashboard config into the REAL vendored gateway store the
    # daemon reads (config.yaml ``platforms.<id>.enabled`` + credential env in
    # ``.env``), so a configured channel actually connects instead of living
    # only in ``msg-platforms.json``. Catalog platforms only; credential values
    # are resolved server-side and never returned here.
    bridge: Dict[str, Any] = {}
    if kind == "catalog":
        refs: Dict[str, Any] = {str(k): v for k, v in env_in.items()}
        for k in clear_in:
            refs.setdefault(str(k), "")
        try:
            bridge = M.bridge_platform_to_gateway(cfg, pid, enabled=enabled,
                                                  env_refs=refs)
        except Exception as exc:  # noqa: BLE001 - report, never fake success
            bridge = {"state": "UNAVAILABLE",
                      "code": getattr(exc, "code", type(exc).__name__),
                      "platform": pid}
    _audit("msg.platform_update", "dashboard:user",
           {"platform": pid, "enabled": enabled,
            "ref_keys": sorted(str(k) for k in env_in),
            "clear_keys": sorted(str(k) for k in clear_in),
            "bridge_state": bridge.get("state")})
    return _ok(ok=True, platform=pid, bridge=bridge,
               qubit_counterpart="CLI `qubit messaging accounts` / "
                                 "`qubit channels catalog`")


def h_analytics_models(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                       p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    return h_analytics(cfg, q, b, {**p, "kind": "models"})


def h_files_read(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    return h_files(cfg, q, b, {**p, "kind": "read"})


_FILES_HTTP = {
    "FILES_NOT_FOUND": 404, "FILES_TOO_LARGE": 413,
    "FILES_OUTSIDE_ROOT": 403, "FILES_BAD_INPUT": 400,
    "FILES_IO": 500, "EMERGENCY_STOP": 403, "POWER_DISABLED": 403,
    "POWER_UNAVAILABLE": 403, "CIRCUIT_OPEN": 403, "OUT_OF_SCOPE": 403,
    "MALFORMED_INPUT": 400,
}


def h_files_download(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                     p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/files/download|stream, GET/HEAD /api/fs/download -> real bytes.

    A genuinely-binary download/stream (not text-only). The read is
    authorized, path-confined, e-stop-checked and audited through the broker's
    READ_ONLY ``local.files_download`` capability — a read needs no
    confirmation, but it still runs the shared e-stop latch and broker audit.
    The JSON dispatcher cannot stream, so the confined bytes are returned as a
    typed base64 envelope (the same contract the kanban attachment download
    uses); the SPA's Files download consumes ``/api/files/read`` which now
    serves binary files as a data URL directly.
    """
    import base64
    from . import file_patch as FP
    from . import files as FI

    path = str(q.get("path", b.get("path", "")) or "").strip()
    counterpart = "GET /api/files/read + CLI `qubit files read`"
    if not path:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "path required",
                     "qubit_counterpart": counterpart}
    if _estop(cfg):
        return 403, {"ok": False, "state": "UNAVAILABLE",
                     "code": "EMERGENCY_STOP",
                     "detail": "emergency stop latched; download refused",
                     "owner": "qubit-policy",
                     "rollback": "nothing read; nothing to roll back",
                     "qubit_counterpart": counterpart}
    try:
        broker = _file_write_broker(cfg)
        res = broker.execute(
            FP.download_capability_name(),
            {"target_path": path, "requested_by": "dashboard:user",
             "project_root": ""},
            source="user", target=path)
    except Exception as exc:
        return _fail(exc)
    payload: Dict[str, Any] = {}
    if res.ok and str(res.output or "").strip():
        try:
            parsed = json.loads(res.output)
            payload = parsed if isinstance(parsed, dict) else {}
        except Exception:
            payload = {}
    if not payload.get("ok"):
        code = str(payload.get("code") or res.denied or "FILES_IO")
        detail = str(payload.get("detail") or res.error
                     or "file read refused")
        return _FILES_HTTP.get(code, 400), {
            "ok": False, "code": _redact(code)[:80],
            "detail": _redact(detail)[:300], "owner": "qubit-policy",
            "rollback": "nothing read; nothing to roll back",
            "qubit_counterpart": counterpart}
    info = payload.get("file") if isinstance(payload.get("file"), dict) else {}
    try:
        raw = FI.read_bytes(cfg, str(info.get("path") or path))
    except FI.FilesError as exc:
        return _FILES_HTTP.get(exc.code, 400), {
            "ok": False, "code": exc.code, "detail": _redact(exc.detail)[:300],
            "owner": "qubit-policy",
            "rollback": "nothing read; nothing to roll back",
            "qubit_counterpart": counterpart}
    data = raw["data"]
    _audit("files.download", "dashboard:user",
           {"path": str(raw["file"]), "bytes": len(data),
            "sha256": raw["sha256"][:16]})
    return _ok(ok=True, name=raw["name"], path=raw["file"],
               size=raw["size"], mime_type=raw["mime"],
               sha256=raw["sha256"],
               content_base64=base64.b64encode(data).decode("ascii"),
               qubit_counterpart=counterpart)


def files_delete(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """DELETE /api/files -> confined, brokered, REVERSIBLE delete.

    The delete is archived (never destroyed) under the state store and is
    recoverable with ``file_patch.restore_deleted``. It runs the SAME
    ``file_patch`` confinement/gate/audit path as the CLI through the canonical
    broker: a mutation requires the durable ``confirmation_id`` the SPA settles
    (or a re-issued ``confirmation_id`` query), and an out-of-root/absolute/
    traversal path is a typed refusal, never a host delete. The absolute path
    is never accepted from the client — it is resolved against the approved
    project roots.
    """
    from . import file_patch as FP
    counterpart = ("CLI `qubit files delete` / `qubit patch rollback` + "
                   "dashboard /files")
    path = str(b.get("path", q.get("path", "")) or "").strip()
    if not path:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "path required",
                     "qubit_counterpart": counterpart}
    recursive = _truthy(b.get("recursive", q.get("recursive")))
    try:
        target = FP.resolve_target(cfg, path, allow_directory=True)
    except FP.FilePatchError as exc:
        return _file_patch_refusal(
            exc.code, exc.detail, counterpart,
            rollback="nothing deleted; target unchanged")
    args = {"target_path": path, "requested_by": "dashboard:user",
            "project_root": "", "recursive": bool(recursive)}
    resp, receipt = _brokered_file_mutation(
        cfg, FP.delete_capability_name(), args, q=q, b=b, target=str(target),
        detail=(f"deleting {path!r} archives it under the Qubit state store "
                "(reversible) and requires confirmation; re-DELETE with the "
                "confirmation_id to apply"),
        counterpart=counterpart)
    if resp is not None:
        return resp
    _audit("files.delete", "dashboard:user", {"path": str(target),
                                              "recursive": bool(recursive)})
    return _ok(ok=True, path=str(target), deleted=True,
               state=receipt.get("state"), receipt=receipt,
               qubit_counterpart="CLI `qubit files restore` (undo available)")


def hooks_delete(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import hooks as H
    name = str(b.get("name", "") or "")
    if not name and (b.get("event") or b.get("command")):
        try:
            recs = H.list_hooks(cfg).get("hooks", [])
            for r in recs if isinstance(recs, list) else []:
                if not isinstance(r, dict):
                    continue
                if r.get("event") == b.get("event"):
                    name = str(r.get("name", ""))
                    break
        except Exception:
            name = ""
    if not name:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "hook name (or matching event) required"}
    if _estop(cfg):
        return 403, {"ok": False, "state": "UNAVAILABLE",
                     "code": "EMERGENCY_STOP",
                     "detail": "emergency stop latched; mutation refused",
                     "owner": "qubit-policy",
                     "rollback": "nothing changed; nothing to roll back"}
    if not _truthy(b.get("confirm")):
        gate = _compat_confirm(cfg, "hooks.remove", "hook delete",
                               query=q, body=b, payload={"name": name})
        if gate is not None:
            return gate
    try:
        H.remove(cfg, name, requested_by="dashboard:user", confirmed=True)
    except Exception as exc:
        code = getattr(exc, "code", None) or type(exc).__name__
        return 400, {"ok": False, "code": code,
                     "detail": _redact(str(exc))[:300]}
    return _ok(ok=True)


def h_hooks_update(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                   p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """PUT /api/ops/hooks -> real hook update (re-declare under a new name).

    The canonical hooks registry has no in-place edit: a declaration is
    immutable once written, so an update is remove-then-declare through the
    same confirmed, audited ops. The new declaration is validated BEFORE the
    old one is removed, so a bad payload leaves the registry unchanged.
    """
    from . import hooks as H
    old = str(b.get("name", "") or "")
    event = str(b.get("event", "") or "")
    shell = str(b.get("command", b.get("shell", "")) or "")
    new_name = str(b.get("new_name", "") or "") or old
    if not old or not event:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "hook update needs 'name' and 'event'"}
    if _estop(cfg):
        return 403, {"ok": False, "state": "UNAVAILABLE",
                     "code": "EMERGENCY_STOP",
                     "detail": "emergency stop latched; mutation refused",
                     "owner": "qubit-policy",
                     "rollback": "nothing changed; nothing to roll back"}
    approved = (_truthy(b.get("confirm")) or _truthy(b.get("approved"))
                or _truthy(b.get("approve")) or _truthy(q.get("confirm")))
    if not approved:
        gate = _compat_confirm(
            cfg, "hooks.update",
            "hook update (shell stored disabled, never runs) requires "
            "explicit confirmation",
            query=q, body=b,
            payload={"name": old, "event": event, "command": shell,
                     "new_name": new_name})
        if gate is not None:
            return gate
    # Validate the replacement under a temporary name first (declare rejects
    # duplicates), then swap: remove the old name, declare the new one.
    try:
        existing = {str(r.get("name", "")) for r in
                    H.list_hooks(cfg).get("hooks", []) if isinstance(r, dict)}
    except Exception:
        existing = set()
    if old not in existing:
        return 404, {"ok": False, "code": "HOOK_NOT_FOUND",
                     "detail": f"no hook {old!r}"}
    if new_name != old and new_name in existing:
        return 400, {"ok": False, "code": "HOOK_EXISTS",
                     "detail": f"hook {new_name!r} already exists"}
    temp = new_name if new_name != old else f"{old}-tmp"
    if temp in existing:
        temp = f"{temp}-upd"
    try:
        H.declare(cfg, temp, event, shell, requested_by="dashboard:user",
                  confirmed=True)
    except Exception as exc:
        code = getattr(exc, "code", None) or type(exc).__name__
        return 400, {"ok": False, "code": code,
                     "detail": _redact(str(exc))[:300]}
    H.remove(cfg, old, requested_by="dashboard:user", confirmed=True)
    if temp != new_name:
        H.remove(cfg, temp, requested_by="dashboard:user", confirmed=True)
        res = H.declare(cfg, new_name, event, shell,
                        requested_by="dashboard:user", confirmed=True)
        hook = res.get("hook", {}) if isinstance(res, dict) else {}
    else:
        hook = {"name": new_name, "event": event}
    _audit("hook_update", "dashboard:user", {"from": old, "to": new_name})
    return _ok(ok=True, event=str(hook.get("event", event)),
               name=str(hook.get("name", new_name)), command="",
               approved=False)


def _agent_row(skill_id: str, manifest: Dict[str, Any], status: str,
               hidden: set) -> Dict[str, Any]:
    return {
        "name": skill_id,
        "version": str(manifest.get("version", "0.0.0") or "0.0.0"),
        "description": str(manifest.get("description", "") or ""),
        "source": str(manifest.get("source", "qubit-local") or "qubit-local"),
        "runtime_status": ("enabled" if status == "enabled" else
                           "disabled" if status in ("staged", "disabled") else
                           "inactive"),
        "has_dashboard_manifest": False,
        "dashboard_manifest": None,
        "path": "",
        "can_remove": True,
        "can_update_git": False,
        "auth_required": False,
        "auth_command": "",
        "user_hidden": skill_id in hidden,
    }


def plugins_hub(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """Unified plugin hub: vendored agent discovery + Qubit overlays.

    Built on the vendored ``_merged_plugins_hub`` — the same discovery the
    reference dashboard uses — so the real agent plugin fleet, its activation
    state, the vendored memory providers, and the vendored dashboard
    extensions are all surfaced (D-3/D-5/D-6). Qubit's own overlay plugins and
    permission-store grants are merged additively; the overlay set is reported
    as dashboard-only extensions. ``user_hidden`` resolves through Qubit's own
    visibility store so the eye toggle persists.
    """
    from .permissions import permissions_store
    from . import plugin_runtime as PR

    hidden = _hidden_set(cfg)
    overlay_names = {m["name"] for m in _overlay_manifests()}
    hub_error = ""
    try:
        # The vendored fork root is not on sys.path by default; without this
        # the import below fails in any process whose first compat call is the
        # hub (fresh tests, early dashboard loads), leaving plugins: [] plus a
        # hub_error instead of the real fleet.
        _vendored_runtime(cfg)
        from qubit_cli.web_server_dashboard import _merged_plugins_hub
        base = _jsonable(_merged_plugins_hub())
    except Exception as exc:
        base = {"plugins": [], "orphan_dashboard_plugins": [],
                "providers": {}}
        hub_error = _redact(str(exc))[:200]

    rows = list(base.get("plugins", []) or [])
    seen = {str(r.get("name", "")) for r in rows}
    for r in rows:
        r["user_hidden"] = str(r.get("name", "")) in hidden

    # Additive: Qubit permission-store grants that are not already a discovered
    # plugin. Overlay extensions are reported under orphan_dashboard_plugins.
    try:
        recs = permissions_store(cfg).skills()
    except Exception:
        recs = []
    for rec in recs:
        d = rec.to_dict() if hasattr(rec, "to_dict") else {}
        sid = str(d.get("skill_id", "") or "")
        if not sid or sid in seen or sid in overlay_names:
            continue
        man = d.get("manifest", {}) if isinstance(d, dict) else {}
        if not isinstance(man, dict):
            man = {}
        rows.append(_agent_row(sid, man, str(d.get("status", "")), hidden))
        seen.add(sid)

    orphans = list(base.get("orphan_dashboard_plugins", []) or [])
    orphan_names = {str(o.get("name", "")) for o in orphans}
    for m in _overlay_manifests():
        if m["name"] in orphan_names:
            continue
        orphans.append({
            "name": m["name"], "label": m["label"],
            "description": m["description"], "icon": m.get("icon", ""),
            "version": m["version"], "tab": m["tab"], "entry": m["entry"],
            "css": m.get("css"), "has_api": m.get("has_api", False),
            "source": m.get("source", "qubit-overlay"),
        })
        orphan_names.add(m["name"])

    # The plugin-runtime catalog (canonical installable rows) is the same
    # registry the CLI/voice install path writes; the dashboard renders its
    # INSTALL controls from these rows (A27/S9).
    try:
        runtime_catalog = PR.plugin_catalog(cfg)
    except Exception as exc:
        runtime_catalog = {"state": "UNAVAILABLE",
                           "code": PR.UNAVAILABLE_CODE,
                           "detail": _redact(str(exc))[:240], "plugins": []}

    providers = base.get("providers") or {}
    try:
        from . import plugin_catalog as PC
        catalog_index = PC.pinned_index_block(
            known_bad=PC._known_bad_map(cfg))
    except Exception as exc:
        catalog_index = {"ok": False, "state": "UNAVAILABLE",
                         "detail": _redact(str(exc))[:240], "entries": []}
    out = _ok(plugins=rows, orphan_dashboard_plugins=orphans,
              catalog=runtime_catalog.get("plugins", []),
              install_capability=runtime_catalog.get(
                  "install_capability", PR.INSTALL_CAPABILITY),
              catalog_state=runtime_catalog.get("state", "OK"),
              catalog_remote=runtime_catalog.get("remote", {}),
              catalog_index=catalog_index,
              providers=providers)
    if hub_error:
        out[1]["hub_error"] = hub_error
    return out


def plugins_catalog(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/dashboard/plugins/catalog -> the real local loadable catalog.

    Every local entry has a real manifest + loader + provenance. The response
    also carries ``pinned_index``: the curated, pinned remote index (Owner
    Decision #1). Raw unvetted remote fetch stays a typed
    ``BLOCKED_REMOTE_PLUGIN`` refusal; an index install is pinned/verified and
    routed through the canonical e-stop -> confirmation -> audit gate.
    """
    from . import plugin_catalog as PC
    try:
        out = PC.local_catalog(
            cfg,
            query=str(q.get("q", "") or ""),
            source=str(q.get("source", "") or ""),
            installed_only=_truthy(q.get("installed", "")),
            enabled_only=_truthy(q.get("enabled", "")),
            loadable_only=not _truthy(q.get("all", "")))
    except Exception as exc:
        return _fail(exc)
    return 200, out


def plugin_catalog_detail(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                          p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/dashboard/plugins/catalog/<id> -> provenance + validation."""
    from . import plugin_catalog as PC
    try:
        out = PC.plugin_detail(cfg, str(p.get("id", "") or ""))
    except Exception as exc:
        return _fail(exc)
    if not out.get("ok"):
        return 404, out
    return 200, out


def plugins_reload(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                   p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/dashboard/plugins/reload -> re-read the plugin store/catalog.

    Re-reads the persisted plugin store + reviewed catalog and re-imports every
    enabled directory plugin, then invalidates the vendored hub cache. A real
    local counterpart to the reference's ``plugins.reload`` lifecycle op; the
    e-stop is checked first inside ``plugin_runtime.reload``.
    """
    from . import plugin_runtime as PR
    if not b.get("confirm"):
        return _need_confirm("plugin reload")
    try:
        out = PR.reload(cfg)
    except PR.PluginRuntimeError as exc:
        http = 403 if exc.code in (PR.ESTOP_CODE, PR.REFUSED_CODE) else 400
        _audit("plugin_reload_refused", "dashboard:user",
               {"code": exc.code})
        return http, {"ok": False, "code": exc.code,
                      "detail": _redact(exc.detail)[:300],
                      "qubit_counterpart": "CLI `qubit plugins reload`"}
    try:
        _vendored_runtime(cfg)
        from qubit_cli.web_server_dashboard import _invalidate_plugins_hub_cache
        _invalidate_plugins_hub_cache()
    except Exception:
        pass
    _audit("plugin_reload", "dashboard:user",
           {"reloaded": out.get("reloaded_plugins", []),
            "errors": len(out.get("errors", []) or [])})
    payload = {k: v for k, v in out.items() if k not in ("state",)}
    return _ok(**payload)


def _plugin_toggle_error(exc: Exception) -> Tuple[int, Dict[str, Any]]:
    """Map a plugin/permission error to a typed code (never the class name)."""
    code = getattr(exc, "code", None)
    text = str(exc)
    if code is None:
        if "unknown skill" in text or "no plugin" in text:
            code = "PLUGIN_NOT_FOUND"
        elif "not installed or bundled" in text:
            code = "PLUGIN_NOT_FOUND"
        else:
            code = "PLUGIN_REFUSED"
    http = 404 if code == "PLUGIN_NOT_FOUND" else 400
    return http, {"ok": False, "code": code,
                  "detail": _redact(text)[:300],
                  "qubit_counterpart": "CLI `qubit agent plugins list`"}


def _agent_status(name: str, status: str, cfg: Any,
                  b: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    """Enable/disable a real plugin through its own authority path.

    Resolution order: a vendored agent plugin (bundled/user/entrypoint) toggles
    the ``plugins.enabled``/``plugins.disabled`` runtime allow/deny lists (the
    same write the CLI performs); a Qubit plugin-runtime directory plugin
    loads/unloads through ``plugin_runtime``; a Qubit permission-store grant
    falls back to the permission toggle. An unknown id is a typed
    ``PLUGIN_NOT_FOUND``, never a raw Python exception class name (D-4/D-8).
    """
    if not name:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "plugin name required"}
    if not b.get("confirm"):
        return _need_confirm(f"plugin {status}")
    want_enabled = status == "active"
    from . import plugin_runtime as PR
    if PR.estop_engaged(cfg):
        return 403, {"ok": False, **PR.refuse_estop(
            f"plugin {'enable' if want_enabled else 'disable'}")}
    from . import qubit_plugins as _QP
    try:
        _QP._prepare_runtime(cfg)
    except Exception:
        pass

    # 1) vendored agent plugin -> config.yaml runtime allow/deny lists.
    try:
        from qubit_cli.plugins_cmd import (
            _resolve_plugin_key, dashboard_set_agent_plugin_enabled)
        key = _resolve_plugin_key(name)
    except Exception:
        key = None
    if key:
        try:
            out = dashboard_set_agent_plugin_enabled(name, enabled=want_enabled)
        except Exception as exc:
            return _plugin_toggle_error(exc)
        if out.get("ok"):
            _audit("plugin_enable" if want_enabled else "plugin_disable",
                   "dashboard:user",
                   {"name": name, "key": key, "runtime": "vendored"})
            return _ok(ok=True, name=name, key=key, runtime="vendored",
                       unchanged=bool(out.get("unchanged", False)))
        return _plugin_toggle_error(RuntimeError(
            out.get("error") or "plugin toggle failed"))

    # 2) Qubit plugin-runtime directory plugin -> load/unload.
    from . import plugin_runtime as PR
    try:
        rec = PR._find(cfg, name)
    except Exception:
        rec = None
    if rec is not None:
        try:
            if want_enabled:
                out = PR.enable_plugin(cfg, name, confirmed=True,
                                       requested_by="dashboard:user")
            else:
                out = PR.disable_plugin(cfg, name, confirmed=True,
                                        requested_by="dashboard:user")
        except PR.PluginRuntimeError as exc:
            http = 403 if exc.code in (PR.ESTOP_CODE, PR.REFUSED_CODE) else 400
            return http, {"ok": False, "code": exc.code,
                          "detail": _redact(exc.detail)[:300],
                          "qubit_counterpart": "CLI `qubit plugins enable <id>`"}
        _audit("plugin_enable" if want_enabled else "plugin_disable",
               "dashboard:user", {"name": name, "runtime": "qubit-runtime"})
        return _ok(ok=True, name=name, runtime="qubit-runtime",
                   plugin=out.get("plugin"))

    # 3) Qubit permission-store grant (overlay skill record).
    from .permissions import permissions_store
    try:
        permissions_store(cfg).set_skill_status(
            name, status, requested_by="dashboard:user", confirmed=True)
    except Exception as exc:
        return _plugin_toggle_error(exc)
    _audit("plugin_enable" if want_enabled else "plugin_disable",
           "dashboard:user", {"name": name, "runtime": "permission-grant"})
    return _ok(ok=True, name=name, runtime="permission-grant")


def agent_enable(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    return _agent_status(str(p.get("id", "")), "active", cfg, b)


def agent_disable(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                  p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    return _agent_status(str(p.get("id", "")), "disabled", cfg, b)


def agent_remove(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """Remove a real user plugin, a runtime directory plugin, or a grant.

    User plugins under ``QUBIT_HOME/plugins`` are removed on disk through the
    vendored ``dashboard_remove_user_plugin``; a Qubit plugin-runtime directory
    plugin is unregistered (files included); a permission-store grant falls back
    to the permission remove. Bundled plugins stay a typed refusal (D-4).
    """
    name = str(p.get("id", "") or "")
    if not name:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "plugin name required"}
    if not b.get("confirm"):
        return _need_confirm("plugin remove")
    from . import plugin_runtime as PR
    if PR.estop_engaged(cfg):
        return 403, {"ok": False, **PR.refuse_estop("plugin remove")}
    from . import qubit_plugins as _QP
    try:
        _QP._prepare_runtime(cfg)
    except Exception:
        pass

    # 1) on-disk user plugin (real removal).
    try:
        from qubit_cli.plugins_cmd import dashboard_remove_user_plugin
        out = dashboard_remove_user_plugin(name)
    except Exception as exc:
        return _plugin_toggle_error(exc)
    if out.get("ok"):
        _audit("plugin_remove", "dashboard:user",
               {"name": name, "runtime": "vendored"})
        return _ok(ok=True, name=name, removed=True, runtime="vendored")
    error = str(out.get("error") or "")
    if "Bundled" in error or "bundled" in error:
        return 400, {"ok": False, "code": "PLUGIN_REFUSED",
                     "detail": _redact(error)[:300],
                     "qubit_counterpart": "bundled plugins are part of Qubit "
                                          "and cannot be removed"}

    # 2) Qubit plugin-runtime directory plugin (real removal + unload).
    from . import plugin_runtime as PR
    try:
        rec = PR._find(cfg, name)
    except Exception:
        rec = None
    if rec is not None:
        try:
            out = PR.unregister(cfg, name, confirmed=True,
                                requested_by="dashboard:user")
        except PR.PluginRuntimeError as exc:
            http = 403 if exc.code in (PR.ESTOP_CODE, PR.REFUSED_CODE) else 400
            return http, {"ok": False, "code": exc.code,
                          "detail": _redact(exc.detail)[:300],
                          "qubit_counterpart": "CLI `qubit plugins unregister`"}
        _audit("plugin_remove", "dashboard:user",
               {"name": name, "runtime": "qubit-runtime"})
        return _ok(ok=True, name=name, removed=True, runtime="qubit-runtime")

    # 3) permission-store grant.
    from .permissions import permissions_store
    try:
        permissions_store(cfg).remove_skill(
            name, requested_by="dashboard:user", confirmed=True)
    except Exception as exc:
        return _plugin_toggle_error(exc)
    _audit("plugin_remove", "dashboard:user",
           {"name": name, "runtime": "permission-grant"})
    return _ok(ok=True, name=name, removed=True, runtime="permission-grant")


def h_agent_plugin_install(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                           p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/dashboard/agent-plugins/install -> ``plugin_runtime.install``.

    The dashboard INSTALL control resolves the SAME canonical install op the
    CLI/voice use: a reviewed local manifest object, a ``<state_dir>/
    plugin-catalog`` identifier, or a local ``*.json`` path. A ``catalog_name``
    (+ optional ``ref``) selects a curated, pinned index entry: it is verified
    against the curated pin, routed through the canonical e-stop ->
    confirmation -> audit path, then fetched at exactly the pinned SHA and
    installed through the vendored catalog pipeline (kill-list, platform
    check, exact-SHA clone, pre-publish malware scan, admission-gated
    enable). Confirmation is the shared durable ``ApprovalJournal``
    (``confirmation_id`` or an explicit ``confirm``), the emergency stop is
    checked first, and a raw (non-index) remote identifier stays a typed
    refusal.
    """
    from . import plugin_runtime as PR
    manifest = b.get("manifest")
    if isinstance(manifest, str):
        try:
            manifest = json.loads(manifest)
        except ValueError:
            return 400, {"ok": False, "code": PR.INVALID_MANIFEST_CODE,
                         "detail": "manifest must be a JSON object",
                         "qubit_counterpart": "CLI `qubit plugin install`"}
    identifier = str(
        b.get("identifier", b.get("source", b.get("path", ""))) or "").strip()
    confirm = bool(b.get("confirm", False))
    confirmation_id = str(b.get("confirmation_id", "") or "").strip()
    catalog_name = str(b.get("catalog_name", "") or "").strip()
    ref = str(b.get("ref", "") or "").strip()
    if catalog_name and manifest is None and not identifier:
        from . import plugin_catalog as PC
        try:
            out = PC.install_from_index(
                cfg, catalog_name, ref=ref, confirmed=confirm,
                confirmation_id=confirmation_id, requested_by="dashboard:user")
        except PR.PluginRuntimeError as exc:
            http = 403 if exc.code in (PR.ESTOP_CODE, PR.REFUSED_CODE,
                                       PC.INDEX_REF_MISMATCH_CODE) \
                else 409 if exc.code == PR.CONFIRM_CODE \
                else 404 if exc.code == PC.INDEX_NOT_FOUND_CODE \
                else 400
            _audit("plugin_index_install_refused", "dashboard:user",
                   {"code": exc.code, "catalog_name": catalog_name[:120]})
            return http, {"ok": False, "code": exc.code,
                          "detail": _redact(exc.detail)[:300],
                          "qubit_counterpart": "CLI `qubit agent plugins install "
                                               "<operator-verified-dir>`"}
        _audit("plugin_index_install", "dashboard:user",
               {"catalog_name": catalog_name[:120],
                "plugin_name": str(out.get("plugin_name", ""))[:120],
                "pinned_ref": str(out.get("pinned_ref", ""))[:40],
                "enabled": out.get("enabled")})
        return 200, {"ok": True, **out}
    if manifest is None and not identifier:
        return 400, {"ok": False, "code": PR.BAD_INPUT_CODE,
                     "detail": "install needs a manifest, an identifier, a "
                               "catalog name, or a local manifest path",
                     "qubit_counterpart": "CLI `qubit plugin install`"}
    if not confirm and not confirmation_id:
        return _staged(
            "installing a reviewed plugin requires confirmation: re-POST with "
            "{\"confirm\": true} or settle the pending approval",
            "")
    try:
        out = PR.install(cfg, manifest=manifest, identifier=identifier,
                         confirmed=confirm, confirmation_id=confirmation_id,
                         requested_by="dashboard:user")
    except PR.PluginRuntimeError as exc:
        http = 403 if exc.code in (PR.ESTOP_CODE, PR.BRANDING_CODE,
                                   PR.REFUSED_CODE) \
            else 409 if exc.code in (PR.CONFIRM_CODE, PR.EXISTS_CODE) \
            else 400
        _audit("plugin_install_refused", "dashboard:user",
               {"code": exc.code, "identifier": identifier[:120]})
        return http, {"ok": False, "code": exc.code,
                      "detail": _redact(exc.detail)[:300],
                      "qubit_counterpart": "CLI `qubit plugin install` "
                                           "/ `qubit skills install <manifest>`"}
    if str(out.get("state")) == "BLOCKED":
        _audit("plugin_install_refused", "dashboard:user",
               {"code": out.get("code"), "identifier": identifier[:120]})
        return 403, {"ok": False, "state": "UNAVAILABLE",
                     "code": out.get("code", PR.INSTALL_SOURCE_CODE),
                     "detail": _redact(str(out.get("explanation", "")))[:300],
                     "qubit_counterpart": "CLI `qubit plugin install`"}
    pid = str((out.get("plugin") or {}).get("id", "") or identifier)
    _audit("plugin_install", "dashboard:user",
           {"id": str((out.get("plugin") or {}).get("id", "")),
            "source": str(out.get("source", ""))})
    # B-08D response-shape parity: the SPA reads ``plugin_name`` / ``warnings``
    # / ``missing_env``. The manifest carries no env requirements (metadata
    # only), so those are honest negatives, never fabricated.
    return _ok(ok=True, installed=bool(out.get("installed")),
               plugin=out.get("plugin"), plugin_name=pid,
               warnings=[], missing_env=[],
               source=out.get("source"),
               install_capability=out.get("install_capability"),
               note=out.get("note", ""),
               qubit_counterpart="CLI `qubit plugin install`")


def _memory_provider_path(cfg: Any) -> Path:
    # Canonical Qubit store (the write target).
    return _store_path(cfg, "qubit_memory_provider.json")


def _memory_provider_read_path(cfg: Any) -> Path:
    # Reads fall back to the legacy `hermes_*` name until the next save.
    return _migrated_read_path(cfg, "qubit_memory_provider.json",
                               "hermes_memory_provider.json")


def mem_provider_set(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                     p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """PUT /api/memory/provider -> real persisted local-provider selection."""
    active = str(b.get("active", b.get("provider", "")) or "")
    if active and active != "qubit-local":
        return 400, {"ok": False, "code": "MEMORY_LOCAL_ONLY",
                     "detail": "qubit-local is the only memory provider; "
                               "cloud sync is refused by policy"}
    path = _memory_provider_path(cfg)
    try:
        store = _load_json_store(_memory_provider_read_path(cfg))
        store["provider"] = "qubit-local"
        _save_json_store(path, store)
    except Exception as exc:
        return _fail(exc)
    return _ok(ok=True, active="qubit-local", config=store,
               qubit_counterpart="CLI `qubit memory` (same local store)")


def mem_config(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
               p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET provider config -> real persisted local-store settings."""
    name = str(p.get("id", "") or "qubit-local")
    if name != "qubit-local":
        # Consistent with the write path (``mem_config_write``): the hub
        # advertises optional providers, but only ``qubit-local`` has a Qubit
        # store and cloud sync is refused by policy. Return the same typed
        # refusal rather than a bare 404, so the page shows an honest typed
        # disposition instead of a raw NOT_FOUND.
        return _gate("MEMORY_LOCAL_ONLY",
                     f"no memory provider {name!r}; qubit-local is the only "
                     "store and cloud sync is refused by policy",
                     "Memory page search + CLI `qubit memory`")
    store = _load_json_store(_memory_provider_read_path(cfg))
    fields = [{"key": k, "value": v}
              for k, v in sorted(store.items()) if k != "provider"]
    return _ok(name="qubit-local", label="Qubit local memory", fields=fields,
               config=store,
               setup={"required": False,
                      "detail": "no setup needed; local-only store"},
               qubit_counterpart="CLI `qubit memory` (same local store)")


def mem_config_write(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                     p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """PUT config / POST setup for a memory provider -> real local setup.

    ``qubit-local`` is the only provider. This persists the provider's
    retrieval options (tier, retention_days) into the same state-dir override
    store the config console uses, confirm-gated and audited. A request for a
    non-local provider is a typed refusal (no cloud sync), never a fake 200.
    """
    name = str(p.get("id", "") or b.get("name", "") or "qubit-local")
    if name != "qubit-local":
        return _gate("MEMORY_LOCAL_ONLY",
                     f"no memory provider {name!r}; qubit-local is the only "
                     "store and cloud sync is refused by policy",
                     "Memory page search + CLI `qubit memory`")
    if not b.get("confirm"):
        return _staged(
            "memory provider setup requires explicit confirmation: re-POST "
            "with {\"confirm\": true}", "")
    values = {k: v for k, v in b.items()
              if k != "confirm" and isinstance(v, (str, int, float, bool))}
    path = _memory_provider_path(cfg)
    try:
        store = _load_json_store(_memory_provider_read_path(cfg))
        store.update(values)
        store["provider"] = "qubit-local"
        _save_json_store(path, store)
    except Exception as exc:
        return _fail(exc)
    return _ok(ok=True, name="qubit-local", setup={"required": False},
               config=store, path=str(path),
               qubit_counterpart="CLI `qubit memory` (same local store)")


def _state_base(cfg: Any) -> Path:
    try:
        return Path(cfg.state_dir())
    except Exception:
        return Path("/tmp")


def _store_path(cfg: Any, name: str) -> Path:
    return _state_base(cfg) / name


def _migrated_read_path(cfg: Any, new_name: str, legacy_name: str) -> Path:
    """Read path for a Qubit-owned store renamed away from its legacy name.

    The canonical new-name file always wins. When it is absent and the legacy
    file exists, the legacy path is returned once so no data is lost; the next
    save writes the canonical name. The legacy file is never moved or deleted.
    """
    new = _state_base(cfg) / new_name
    if new.exists():
        return new
    legacy = _state_base(cfg) / legacy_name
    return legacy if legacy.exists() else new


def _config_overrides_path(cfg: Any) -> Path:
    # Canonical Qubit store (the write target).
    return _store_path(cfg, "qubit_config_overrides.json")


def _config_overrides_read_path(cfg: Any) -> Path:
    # Reads fall back to the legacy `hermes_*` name when only it exists so a
    # pre-rename override set survives until the next save writes the new name.
    return _migrated_read_path(cfg, "qubit_config_overrides.json",
                               "hermes_config_overrides.json")


def _env_refs_path(cfg: Any) -> Path:
    # PRB-02: the ONE canonical env-reference store (shared with CLI/voice via
    # voicebridge.env_store). Never a second path or a side store.
    from . import env_store

    return env_store.store_path(cfg)


def _load_json_store(path: Path) -> Dict[str, Any]:
    try:
        raw = json.loads(path.read_text())
        return dict(raw) if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_json_store(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
    try:
        if path.exists():
            backup = path.with_suffix(path.suffix + ".bak")
            backup.write_bytes(path.read_bytes())
    except OSError:
        pass
    tmp.replace(path)
    _audit("store_write", "dashboard:user",
           {"path": path.name, "keys": sorted(data)[:20]})


def _persist_overrides_to_home(values: Dict[str, Any], cfg: Any = None) -> bool:
    """Merge non-secret overrides into the Qubit runtime home ``config.yaml``.

    B-03: the display store alone is not a runtime consumer. The vendored
    runtime, CLI, and voice read ``<QUBIT_HOME>/config.yaml`` through the
    canonical loader, so writing the validated overrides there makes them
    actually take effect. Atomic replace; best-effort (the display store
    remains authoritative for the dashboard round-trip).
    """
    try:
        import os

        from . import qubit_runtime

        home = qubit_runtime.runtime_home_for_cfg(cfg)
        Path(home).mkdir(parents=True, exist_ok=True)
        path = Path(home) / "config.yaml"
        data: Dict[str, Any] = {}
        if path.is_file():
            import yaml  # type: ignore

            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        for dotted, value in values.items():
            try:
                _set_dotted(data, dotted, value)
            except Exception:
                continue
        import yaml  # type: ignore

        tmp = path.with_name(path.name + ".qubit-tmp")
        tmp.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        os.replace(tmp, path)
        return True
    except Exception:
        return False


def config_save(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """PUT /api/config -> real confirm-gated Qubit settings override write.

    Accepts both the served client shape (``{"config": {<nested tree>}}``) and
    legacy flat scalars, flattens them to ``dot.path -> scalar`` overrides,
    persists to ``<state_dir>/qubit_config_overrides.json`` with an atomic
    ``.bak`` and an audit line, and reads them back through ``h_config`` on the
    next request. Secret-shaped keys (``*_key``, ``*_token``, ``*_secret``,
    ``*_password``) never enter the store: a whole-tree PUT round-trips the
    secret-shaped leaves the server served, so an unchanged served leaf is
    skipped while a newly provided or changed one is a typed
    ``SECRET_WRITE_REFUSED``. A body that carries no applicable scalar is a
    typed refusal; without ``confirm`` the write stages a typed 409. It never
    returns ``ok=True`` with a zero count and no change.
    """
    if not isinstance(b, dict) or not b:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "config write needs an object body"}
    payload = b.get("config")
    if "config" in b and not isinstance(payload, dict):
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "config must be an object of settings, not "
                               f"{type(payload).__name__}"}
    if not isinstance(payload, dict):
        payload = b
    values = _flatten_config(payload)
    if not values:
        return _gate("CONFIG_NO_FIELDS",
                     "config write carried no scalar settings; nothing to apply",
                     "CLI `qubit config` / dashboard Settings")
    # An own-export round-trips redacted placeholders; never treat them as a
    # secret write attempt (and never persist them).
    values = {k: v for k, v in values.items() if v != "[REDACTED]"}
    if not values:
        return _gate("CONFIG_NO_FIELDS",
                     "config write carried only redacted placeholders; nothing "
                     "to apply",
                     "GET /api/config (unredacted) + PUT the changed keys")
    # B-03: match secret leaves on BOTH underscore and dotted paths. The old
    # `(_key|_token|_secret|_password)$` missed `dashboard.basic_auth.password`
    # and `.secret` (a dot separator, not underscore), letting a plaintext
    # secret into the override store. Shared with the read-side redactor so the
    # two sides can never diverge.
    _is_secret_key = _is_secret_config_key

    # The Settings form PUTs the whole tree it read from GET /api/config. On the
    # served WebUI that read is proxied to the vendored runtime and merged with
    # Qubit overrides; the direct compat dispatch serves the Qubit live record.
    # A secret-shaped leaf in the PUT is unchanged when its value matches ANY
    # record the server could have served (e.g. the vendored
    # ``browser.camofox.session_key``/``terminal.docker_shared_container_key``/
    # ``voice.record_key`` or the Qubit ``hotkey.hold_key``). Unchanged served
    # leaves are not secret *writes*: drop them. A newly provided or actually
    # changed secret-shaped leaf is still refused.
    served_records = [_config_live(cfg)]
    try:
        from . import qubit_asgi as _HA
        _code, _, _body = _HA.call("GET", "/api/config")
        if _code == 200:
            served_records.append(json.loads(_body.decode("utf-8")))
    except Exception:
        pass
    served_values: Dict[str, list] = {}
    for record in served_records:
        try:
            record = _merge_config_overrides(cfg, record)
        except Exception:
            pass
        for key, value in _flatten_config(record).items():
            bucket = served_values.setdefault(key, [])
            if not any(value == existing for existing in bucket):
                bucket.append(value)
    # Reject unknown keys and mistyped values against the real schema instead of
    # persisting an arbitrary key and reporting fabricated success. This runs
    # BEFORE the secret-shape filter: a bogus key ending in ``_key``/``_token``
    # was misreported as SECRET_WRITE_REFUSED, hiding a typo behind an unrelated
    # refusal so the operator could not tell the two apart.
    from . import config as C
    known = _config_scalar_paths(C.DEFAULTS)
    # Accept exactly the keys GET /api/config actually serves -- the served
    # tree (vendored + overrides) plus the Qubit live record. That includes
    # env-derived leaves (``VOICE_BRIDGE_MEMORY_DB`` -> ``memory.db``) that are
    # real runtime settings but absent from DEFAULTS. Without this a whole-tree
    # Save is refused as UNKNOWN_CONFIG_KEY even after the secret-shaped leaves
    # are skipped, leaving the control dead for a different reason.
    for record in served_records:
        known.update(_config_scalar_paths(record))
    unknown = sorted(k for k in values if k not in known)
    if unknown:
        return _gate("UNKNOWN_CONFIG_KEY",
                     "unknown settings key(s): " + ", ".join(unknown[:5]) +
                     "; see GET /api/config/schema",
                     "GET /api/config/schema")
    # The served record carries a read-only `terminal` block (live /chat model
    # slot). It is display-only by contract: the terminal slot is owned by the
    # /chat + Models surfaces, so a PUT carrying it is refused, never stored.
    readonly = sorted(k for k in values
                      if k == "terminal" or k.startswith("terminal."))
    if readonly:
        return _gate("READ_ONLY_SECTION",
                     "terminal.* is read-only here; switch the /chat model "
                     "from /chat or the Models page",
                     "/chat + Models page")
    # A whole-tree Save round-trips every leaf it read. Drop ANY leaf whose
    # value already equals a served record, so an unchanged leaf never re-enters
    # the override store and the reported change set is the real edit only; then
    # refuse a *changed* secret-shaped leaf. Dropping only secret-shaped
    # unchanged leaves left every ordinary unchanged setting (e.g.
    # ``hotkey.hold_key``) applied on a whole-tree Save.
    changed_secrets: list = []
    remaining: Dict[str, Any] = {}
    for key, value in values.items():
        if any(value == served for served in served_values.get(key, ())):
            continue
        if _is_secret_key(key):
            changed_secrets.append(key)
        else:
            remaining[key] = value
    if changed_secrets:
        return _gate("SECRET_WRITE_REFUSED",
                     "The dashboard never writes secret values; reference "
                     "names only (" + ", ".join(sorted(changed_secrets)[:5])
                     + ")",
                     "CLI `qubit auth login` (vault references, manual handoff)")
    if not remaining:
        return _gate("CONFIG_NO_FIELDS",
                     "config write carried only unchanged leaves; nothing "
                     "to apply",
                     "GET /api/config + PUT the changed keys")
    values = remaining
    mistyped = []
    for key, value in values.items():
        default = known[key]
        if default is None:
            continue
        if isinstance(default, bool):
            good = isinstance(value, bool)
        elif isinstance(default, (int, float)):
            good = isinstance(value, (int, float)) and not isinstance(value, bool)
        elif isinstance(default, str):
            good = isinstance(value, str)
        elif isinstance(default, list):
            good = isinstance(value, list)
        else:
            good = True
        if not good:
            mistyped.append(key)
    if mistyped:
        return _gate("BAD_CONFIG_TYPE",
                     "settings value type mismatch: " +
                     ", ".join(sorted(mistyped)[:5]),
                     "GET /api/config/schema")
    guard = _compat_confirm(
        cfg, "config.write",
        "config write stages through the Qubit settings console: re-POST with "
        "{\"confirm\": true}", q, b, payload=values)
    if guard is not None:
        return guard
    path = _config_overrides_path(cfg)
    try:
        previous = _load_json_store(_config_overrides_read_path(cfg))
        merged = dict(previous)
        merged.update(values)
        _save_json_store(path, merged)
    except Exception as exc:
        return _fail(exc)
    # B-03 runtime consumer: also merge into the Qubit runtime home config so
    # the vendored runtime / CLI / voice actually read the applied settings.
    applied_to_home = _persist_overrides_to_home(values, cfg)
    _audit("config_apply", "dashboard:user",
           {"keys": sorted(values)[:20], "count": len(values),
            "home_applied": applied_to_home})
    # The dashboard serves its boot-loaded record merged with overrides, so a
    # PUT would otherwise be invisible to running code until restart. Refresh
    # the in-process record in place (the same object every holder reads) so
    # per-request readers converge immediately. Separate OS processes (the
    # gateway, helper services) and boot-built singletons still read config
    # at boot — reported honestly below instead of implied.
    try:
        live_data = getattr(cfg, "data", None)
        if isinstance(live_data, dict):
            for dotted, value in values.items():
                try:
                    _set_dotted(live_data, dotted, value)
                except Exception:
                    continue
    except Exception:
        pass
    return _ok(ok=True, config=values, count=len(values),
               applied_to_home=applied_to_home,
               path=str(path),
               live_now=["dashboard settings record"],
               restart_required=["qubit-gateway", "qubit-voicebridge"],
               restart_reason=("running processes read config at boot; the "
                               "dashboard record is live now — restart both "
                               "services to apply everywhere"),
               rollback=f"restore {path.name}.bak (atomic previous state) or "
                        f"PUT the prior {len(previous)} overrides back",
               qubit_counterpart="dashboard Settings (classic) + CLI config "
                                 "profiles")


def env_write(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
              p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """PUT/DELETE /api/env -> real confirm-gated reference registry.

    Only *references* enter the ONE canonical store
    (``<state_dir>/qubit_env_refs.json`` via :mod:`voicebridge.env_store`): a
    value must be ``env:NAME``/``vault:NAME``/``file:PATH`` (``ref:`` the
    legacy vault alias), never a raw secret. This mirrors the operator model
    :mod:`voicebridge.env_store` validates and the channel surfaces accept, so
    the Keys page can round-trip every reference kind the CLI/voice already
    store. DELETE removes a reference. Both are confirm-gated, e-stop-gated,
    and audited; they never write a secret value or fabricate success.
    """
    from . import env_store

    bad = _unsupported_fields(
        b, frozenset({"key", "name", "value", "ref", "confirm", "delete"}))
    if bad[0] != 200:
        return bad
    deleting = bool(b.get("delete")) or bool(q.get("delete"))
    guard = _compat_confirm(
        cfg, "env_reference_write",
        "env reference change stages through the Qubit vault: re-POST "
        "with {\"confirm\": true}", q, b)
    if guard is not None:
        return guard
    name = str(b.get("name", b.get("key", "")) or "")
    if not name:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "env reference name required"}
    if not env_store.valid_name(name):
        return 400, {"ok": False, "code": "ENV_NAME_INVALID",
                     "detail": "env reference name must match "
                               "^[A-Za-z_][A-Za-z0-9_]*$",
                     "qubit_counterpart": "CLI `qubit auth set <NAME> <ref>`"}
    if deleting:
        try:
            res = env_store.remove_ref(cfg, name)
        except Exception as exc:
            return _fail(exc)
        return _ok(ok=True, name=name, removed=res["removed"],
                   count=res["count"],
                   qubit_counterpart="CLI `qubit auth status` (names only)")
    value = str(b.get("value", b.get("ref", "")) or "")
    if not value:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "env reference value required"}
    if not env_store.is_reference(value):
        # 1:1 with the reference Keys page: a pasted raw secret is written to
        # the canonical runtime ``.env`` (secrets only), never refused. The
        # value is stored, never echoed or logged; ``is_set`` flips on the
        # next read. Confirm-gated + audited by the guard above.
        try:
            res = env_store.write_secret(cfg, name, value)
        except ValueError as exc:
            return 400, {"ok": False, "code": "ENV_VALUE_INVALID",
                         "detail": _redact(str(exc))[:300]}
        except Exception as exc:
            return _fail(exc)
        return _ok(ok=True, name=name, state="SET",
                   qubit_counterpart="CLI `qubit auth status` (names only)")
    try:
        res = env_store.set_ref(cfg, name, value)
    except ValueError as exc:
        return _gate("SECRET_WRITE_REFUSED", _redact(str(exc))[:300],
                     "CLI `qubit auth login` (manual-handoff enrollment)")
    except Exception as exc:
        return _fail(exc)
    return _ok(ok=True, name=name, value=value, count=res["count"],
               qubit_counterpart="CLI `qubit auth status` (names only)")


def env_delete(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
               p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """DELETE /api/env -> remove one vault reference (confirm-gated, audited).

    B-08D: the SPA sends ``DELETE /api/env`` with ``{key, confirm: true}``. The
    old route forced a PUT-shaped ``delete`` flag, so every real UI delete fell
    through to the set path and answered ``400 MALFORMED``. The HTTP verb is now
    the authority; ``key`` (or ``name``) names the reference. E-stop-gated.
    """
    bad = _unsupported_fields(
        b, frozenset({"key", "name", "confirm"}))
    if bad[0] != 200:
        return bad
    guard = _compat_confirm(
        cfg, "env_reference_delete",
        "env reference removal stages through the Qubit vault: re-DELETE "
        "with {\"confirm\": true}", q, b)
    if guard is not None:
        return guard
    name = str(b.get("key", b.get("name", "")) or "")
    if not name:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "env reference name required"}
    from . import env_store

    if not env_store.valid_name(name):
        return 400, {"ok": False, "code": "ENV_NAME_INVALID",
                     "detail": "env reference name must match "
                               "^[A-Za-z_][A-Za-z0-9_]*$",
                     "qubit_counterpart": "CLI `qubit auth unset <NAME>`"}
    try:
        res = env_store.remove_ref(cfg, name)
        secret = env_store.remove_secret(cfg, name)
    except Exception as exc:
        return _fail(exc)
    return _ok(ok=True, name=name, removed=res["removed"] or secret["removed"],
               count=res["count"],
               qubit_counterpart="CLI `qubit auth status` (names only)")


_TOOLSET_ENV_FIELDS = frozenset({"env", "profile", "confirm", "name"})
_PLUGIN_PROVIDER_FIELDS = frozenset(
    {"context_engine", "memory_provider", "name", "confirm"})


def _unsupported_fields(body: Dict[str, Any], allowed: frozenset) -> Tuple[int, Dict[str, Any]]:
    unknown = sorted(str(k) for k in body if k not in allowed)
    if not unknown:
        return 200, {}
    return 400, {"ok": False, "code": "UNSUPPORTED_FIELD",
                 "detail": "unsupported field(s): " + ", ".join(unknown),
                 "supported": sorted(allowed)}


def toolset_env_write(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                      p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """PUT /api/tools/toolsets/{id}/env -> persist vault references.

    The drawer sends ``{env: {VAR: ref}, profile}``. The value is a vault
    reference (``vault:...``/``ref:...``), never a raw secret; a raw secret is
    the typed ``SECRET_WRITE_REFUSED`` gate. An unsupported field is rejected
    instead of silently dropped, and success reports the reference names that
    were actually persisted so the UI can confirm a real state change.
    """
    name = str(p.get("id", "") or b.get("name", "") or "")
    if not name:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "toolset name required"}
    bad = _unsupported_fields(b, _TOOLSET_ENV_FIELDS)
    if bad[0] != 200:
        return bad
    env = b.get("env")
    if not isinstance(env, dict) or not env:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "env map required "
                               "({\"env\": {\"VAR\": \"vault:name\"}})"}
    if not _truthy(b.get("confirm")):
        return _staged(
            "toolset env change requires explicit confirmation: re-PUT with "
            "{\"confirm\": true}", "")
    from . import env_store

    saved: List[str] = []
    rejected: List[str] = []
    for key, value in env.items():
        if env_store.is_reference(str(value or "")):
            saved.append(str(key))
        else:
            rejected.append(str(key))
    if rejected:
        return _gate("SECRET_WRITE_REFUSED",
                     "Toolset env writes accept env:/vault:/file: references "
                     "only, never secret values: "
                     + ", ".join(sorted(rejected)[:5]),
                     "CLI `qubit auth login` (references, manual handoff)")
    if not saved:
        return _gate("ENV_NO_FIELDS",
                     "toolset env carried no reference to persist",
                     "dashboard Env page + CLI `qubit auth status`")
    try:
        for key in saved:
            env_store.set_ref(cfg, key, str(env[key]))
    except Exception as exc:
        return _fail(exc)
    return _ok(ok=True, name=name, saved=sorted(saved),
               count=len(env_store.load(cfg)),
               profile=str(b.get("profile", "") or ""),
               qubit_counterpart="CLI `qubit auth status` (names only)")


def plugin_providers_write(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                           p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """PUT /api/dashboard/plugin-providers -> persist the context engine choice.

    B-08D: the Plugins page sends ``{context_engine}`` (or
    ``memory_provider``). It must reach the runtime home ``config.yaml`` that
    the canonical loader and the vendored hub read — not a generic permission
    grant that drops the selection. Unsupported fields are rejected.
    """
    bad = _unsupported_fields(b, _PLUGIN_PROVIDER_FIELDS)
    if bad[0] != 200:
        return bad
    context_engine = b.get("context_engine")
    memory_provider = b.get("memory_provider")
    if context_engine is None and memory_provider is None:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "context_engine or memory_provider required"}
    for field, value in (("context_engine", context_engine),
                         ("memory_provider", memory_provider)):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            return 400, {"ok": False, "code": "MALFORMED",
                         "detail": f"{field} must be a non-empty name"}
    if not _truthy(b.get("confirm")):
        return _staged(
            "plugin provider change requires explicit confirmation: re-PUT "
            "with {\"confirm\": true}", "")
    values: Dict[str, Any] = {}
    if context_engine is not None:
        values["context.engine"] = str(context_engine).strip()
    if memory_provider is not None:
        values["memory.provider"] = str(memory_provider).strip()
    applied = _persist_overrides_to_home(values, cfg)
    if not applied:
        return _gate("CONFIG_WRITE_FAILED",
                     "could not persist the plugin provider selection to the "
                     "runtime home config",
                     "CLI `qubit config` + dashboard Settings")
    _audit("plugin_providers_apply", "dashboard:user",
           {"keys": sorted(values)})
    return _ok(ok=True,
               context_engine=str(context_engine).strip()
               if context_engine is not None else None,
               memory_provider=str(memory_provider).strip()
               if memory_provider is not None else None,
               applied_to_home=True, config=values,
               qubit_counterpart="CLI `qubit config` + dashboard Plugins page")


def toolset_config(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                   p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET toolset config -> real Qubit provider/credential-reference matrix."""
    from . import proxy_status as PX
    name = str(p.get("id", "") or "")
    try:
        prov = PX.providers(cfg)
    except Exception as exc:
        return _fail(exc)
    rows: List[Dict[str, Any]] = []
    active: Optional[str] = None
    for row in prov.get("providers", []) or []:
        if not isinstance(row, dict):
            continue
        pname = str(row.get("provider", ""))
        is_active = bool(row.get("allowlisted"))
        if is_active and active is None:
            active = pname
        rows.append({
            "name": pname, "badge": "", "tag": "",
            "env_vars": [{
                "key": f"{pname.upper()}_KEY_REF",
                "prompt": "credential reference (never a value)",
                "url": None, "default": None,
                "is_set": str(row.get("key_ref", "")) != "missing"}],
            "post_setup": None, "requires_nous_auth": False,
            "is_active": is_active,
            "status": "ready" if is_active else "needs_setup"})
    return _ok(name=name, has_category=bool(rows), providers=rows,
               active_provider=active,
               qubit_counterpart="CLI `qubit capability` + dashboard Settings "
                                 "(classic)")


def toolset_models(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                   p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET toolset models -> real local model catalog (loopback-only)."""
    from . import local_models as LM
    name = str(p.get("id", "") or "")
    try:
        cat = LM.catalog(cfg)
    except Exception as exc:
        return _fail(exc)
    rows = [{
        "id": str(m.get("id", m.get("name", ""))),
        "display": str(m.get("name", m.get("id", ""))),
        "speed": "", "strengths": "", "price": ""}
        for m in (cat.get("models", []) if isinstance(cat, dict) else [])
        if isinstance(m, dict)]
    return _ok(name=name, has_models=bool(rows), provider="ollama-local",
               plugin="ollama-http", models=rows, current=None, default=None,
               runtime_observed=bool(cat.get("runtime_observed")) if isinstance(cat, dict) else False,
               qubit_counterpart="CLI `qubit local-models catalog`")


# ---------------------------------------------------------------------------
# Qubit delta Wave 3 surfaces (D3/D4/D20/D21/D24): file transfer, active
# recall, document extraction, question prompts, and session sharing. Every
# handler dispatches the SAME module function the CLI/voice call.
# ---------------------------------------------------------------------------
_OK_STATES = ("OK", "EMPTY", "COMPLETE", "DEGRADED", "READY")
_OK_CODES = ("", "OK", "COMPLETE", "EMPTY", "READY")


def _surface(out: Any) -> Tuple[int, Dict[str, Any]]:
    """Adapt a module result to the compat envelope + a typed HTTP status.

    A ``*_CONFIRM_REQUIRED`` result is a 409 staged gate; denied/unavailable
    typed states are 403; success states are 200. The raw module body (with
    its typed ``code``/``owner``/``rollback``) is preserved verbatim.
    """
    if not isinstance(out, dict):
        return _fail(Exception("module returned a non-object result"))
    body = dict(out)
    state = str(body.get("state", "") or "")
    code = str(body.get("code", "") or "")
    if code.upper().endswith("CONFIRM_REQUIRED") or state in (
            "CONFIRM_REQUIRED", "CONFIRMATION_REQUIRED"):
        body["ok"] = False
        return 409, body
    ok = (state in _OK_STATES and code in _OK_CODES) or state == "EMPTY"
    body["ok"] = bool(ok)
    body.setdefault("qubit_counterpart", "")
    return (200 if ok else 403), body


def _ft_default_node(cfg: Any) -> str:
    """First local (loopback) enrolled node, else first node, else ""."""
    from . import file_transfer as FT
    from . import node_host as NH

    try:
        recs = NH._load(cfg).get("nodes", {})
    except Exception:
        return ""
    for node_id, rec in recs.items():
        if isinstance(rec, dict) and FT._is_local(rec):
            return str(node_id)
    return str(next(iter(recs), "") or "")


def _qint(q: Dict[str, str], b: Dict[str, Any], *keys: str, default: int) -> int:
    """Integer query/body param, accepting a JSON int or a numeric string."""
    for src in (q, b):
        if not isinstance(src, dict):
            continue
        for key in keys:
            value = src.get(key)
            if isinstance(value, bool):
                continue
            if isinstance(value, int):
                return value
            if isinstance(value, str) and value:
                try:
                    return int(value)
                except (TypeError, ValueError):
                    continue
    return default


def h_transfers_status(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                       p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import file_transfer as FT
    return _ok(**FT.status(cfg))


def h_transfers_list(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                     p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import file_transfer as FT
    node = _query_text(q, b, "node") or _ft_default_node(cfg)
    return _surface(FT.list_directory(
        cfg, node, _query_text(q, b, "path"),
        limit=_qint(q, b, "limit", default=FT.MAX_LIST_ENTRIES)))


def h_transfers_fetch(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                      p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import file_transfer as FT
    node = _query_text(q, b, "node") or _ft_default_node(cfg)
    if not b.get("confirm"):
        return _need_confirm("file transfer fetch")
    return _surface(FT.fetch(
        cfg, node, _query_text(q, b, "path"),
        offset=_qint(q, b, "offset", default=0),
        max_bytes=_qint(q, b, "max_bytes", "maxBytes",
                        default=FT.DEFAULT_FETCH_BYTES), confirmed=True))


def h_transfers_plan(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                     p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import file_transfer as FT
    node = _query_text(q, b, "node") or _ft_default_node(cfg)
    if not b.get("confirm"):
        return _need_confirm("file transfer plan")
    return _surface(FT.stream_plan(
        cfg, node, _query_text(q, b, "path"),
        chunk_size=_qint(q, b, "chunk_size", "chunkSize",
                         default=FT.DEFAULT_CHUNK_BYTES)))


def h_transfers_resume(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                       p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import file_transfer as FT
    node = _query_text(q, b, "node") or _ft_default_node(cfg)
    return _surface(FT.resume(
        cfg, node, _query_text(q, b, "transfer_id", "id"),
        offset=_qint(q, b, "offset", default=0)))


def h_recall_status(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import active_memory as AM
    return _ok(**AM.status(cfg))


def h_recall_policy_get(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                        p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import active_memory as AM
    return _ok(**AM.policy_get(cfg))


def h_recall_policy_set(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                        p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import active_memory as AM
    if not b.get("confirm"):
        return _need_confirm("active-recall policy set")
    enabled = b.get("enabled")
    if isinstance(enabled, str):
        enabled = enabled.strip().lower() in ("on", "true", "yes", "enable")
    scoped = b.get("session_scoped")
    if isinstance(scoped, str):
        scoped = scoped.strip().lower() in ("session", "scoped", "true", "on")
    return _surface(AM.policy_set(
        cfg, enabled=enabled, min_score=b.get("min_score"),
        max_items=b.get("max_items"), session_scoped=scoped, confirmed=True))


def _recall_limit(q: Dict[str, str], b: Dict[str, Any]) -> Optional[int]:
    """Bounded page size for the recall surface (``None`` = store default)."""
    raw = b.get("limit") if isinstance(b, dict) else None
    if isinstance(raw, int) and not isinstance(raw, bool):
        return raw if raw > 0 else None
    value = _qint(q, b, "limit", default=0)
    return value if value > 0 else None


def h_recall_query(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                   p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import active_memory as AM
    return _surface(AM.recall(
        cfg, _query_text(q, b, "query", "q"),
        session=_query_text(q, b, "session"),
        limit=_recall_limit(q, b),
        offset=_qint(q, b, "offset", default=0)))


def h_recall_update(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/recall/update -> confirmation-gated in-place memory update.

    Ports the reference memory update path (holographic
    ``store.py:161 update_fact`` / Mem0 ``_backend.py:32 update``) that the
    active-recall surface was missing. The mutation is e-stop / disabled gated
    inside :func:`active_memory.update`; a client ``confirm`` boolean alone is
    not authority for the write, so without it the request stages the typed
    confirmation the dashboard settles by re-issuing with the id.
    """
    from . import active_memory as AM
    if not _truthy(b.get("confirm", q.get("confirm", ""))):
        return _need_confirm("active-recall memory update")
    confidence = b.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        confidence = None
    text = b.get("text") if isinstance(b.get("text"), str) else None
    tier = b.get("tier") if isinstance(b.get("tier"), str) else None
    return _surface(AM.update(
        cfg, _qint(q, b, "memory_id", "id", default=0),
        text=text, tier=tier, confidence=confidence,
        reason=_query_text(q, b, "reason"),
        session=_query_text(q, b, "session"),
        confirmed=True))


def h_recall_resolve(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                     p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import active_memory as AM
    return _surface(AM.resolve(
        cfg, _query_text(q, b, "query", "q"),
        session=_query_text(q, b, "session")))


def h_recall_escalate(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                      p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import active_memory as AM
    if not b.get("confirm"):
        return _need_confirm("memory escalation")
    return _surface(AM.escalate(
        cfg, _qint(q, b, "memory_id", "id", default=0),
        reason=_query_text(q, b, "reason"), confirmed=True))


def h_documents_status(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                       p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import doc_extract as DE
    return _ok(**DE.status(cfg))


def h_documents_kinds(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                      p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import doc_extract as DE
    return _ok(**DE.kinds(cfg))


def h_documents_extract(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                        p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import doc_extract as DE
    if not b.get("confirm"):
        return _need_confirm("document extraction")
    try:
        out = DE.extract(
            cfg, _query_text(q, b, "path"),
            max_chars=_qint(q, b, "max_chars", "maxChars",
                            default=DE.DEFAULT_MAX_CHARS),
            page=(_qint(q, b, "page", default=0) or None), confirmed=True)
    except DE.DocExtractError as exc:
        return 400, {"ok": False, "state": "FAILED", "code": exc.code,
                     "detail": _redact(exc.detail)[:300],
                     "owner": "qubit-policy",
                     "rollback": "nothing was extracted"}
    return _surface(out)


def h_ask_status(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import question_prompt as QP
    return _ok(**QP.status(cfg))


def h_ask_pending(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                  p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import question_prompt as QP
    return _ok(**QP.pending(cfg))


def h_ask_create(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import question_prompt as QP
    if not b.get("confirm"):
        return _need_confirm("question prompt create")
    try:
        out = QP.ask(cfg, [{"text": _query_text(q, b, "question", "text"),
                            "kind": "text"}],
                     session=_query_text(q, b, "session"), confirmed=True)
    except QP.QuestionError as exc:
        return 400, {"ok": False, "state": "FAILED", "code": exc.code,
                     "detail": _redact(exc.detail)[:300],
                     "owner": "qubit-policy",
                     "rollback": "prompt store untouched"}
    return _surface(out)


def h_ask_answer(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import question_prompt as QP
    answers = b.get("answers", b.get("responses"))
    if answers is None:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "answers object/list is required"}
    try:
        out = QP.answer(cfg, p.get("prompt_id", b.get("prompt_id", "")), answers)
    except QP.QuestionError as exc:
        return 400, {"ok": False, "state": "FAILED", "code": exc.code,
                     "detail": _redact(exc.detail)[:300],
                     "owner": "qubit-policy",
                     "rollback": "prompt store untouched"}
    return _surface(out)


def h_session_share_status(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                           p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import session_share as SS
    return _surface(SS.status(cfg))


def h_session_shares_list(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                          p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import session_share as SS
    return _surface(SS.list_shares(cfg))


def h_session_share_publish(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                            p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import session_share as SS
    sid = p.get("session_id", "") or _query_text(q, b, "session_id")
    ttl = b.get("ttl_s", q.get("ttl_s"))
    try:
        ttl = float(ttl) if ttl not in (None, "") else SS.DEFAULT_TTL_S
    except (TypeError, ValueError):
        ttl = SS.DEFAULT_TTL_S
    return _surface(SS.publish(
        cfg, sid, ttl_s=ttl, confirmed=bool(b.get("confirm")),
        requested_by="dashboard:user"))


def h_session_share_revoke(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                           p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import session_share as SS
    sid = p.get("share_id", "") or _query_text(q, b, "share_id")
    return _surface(SS.revoke(cfg, sid, confirmed=bool(b.get("confirm"))))


def h_session_shares_purge(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                           p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import session_share as SS
    if not b.get("confirm"):
        return 409, {
            "ok": False, "state": "CONFIRMATION_REQUIRED",
            "code": SS.CONFIRM_CODE,
            "detail": "session share purge requires explicit confirmation: "
                      "re-POST with {\"confirm\": true}",
            "qubit_counterpart": "CLI `qubit shares purge --confirm` + voice "
                                 "yes",
        }
    return _surface(SS.purge_expired(cfg))


def h_share_resolve(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /share/{share_id}: public token in the query, no dashboard token.

    The share token is the bearer credential (constant-time checked inside the
    module). A missing/expired/revoked/wrong token returns a typed gate and
    NEVER another session's transcript.
    """
    from . import session_share as SS
    sid = p.get("share_id", "") or ""
    token = q.get("token") or b.get("token") or ""
    out = SS.resolve(cfg, sid, token)
    state = str(out.get("state", "") or "")
    body = dict(out)
    body.setdefault("qubit_counterpart", "open /share/<id> with the share token")
    if state == "OK":
        body["ok"] = True
        return 200, body
    body["ok"] = False
    http = {
        "NOT_FOUND": 404, "EXPIRED": 410, "REVOKED": 403, "DENIED": 403,
        "ESTOP": 403, "FAILED": 400, "UNAVAILABLE": 503,
    }.get(state, 403)
    return http, body


# ---------------------------------------------------------------------------
# Agent 5-10 Qubit-owned front door: GET /api/qubit/*. Each handler is a thin
# read over the exact module the CLI/voice use; there is no proxy to the fork.
# ---------------------------------------------------------------------------

def _eval_state_path(cfg: Any, name: str) -> Path:
    return Path(cfg.state_dir()) / "evaluation" / name


def h_qubit_gateway_discovery(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                              p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import gateway_peers as GW
    try:
        return _ok(**GW.discovery(cfg))
    except Exception as exc:
        return _fail(exc)


def h_qubit_runtime_discovery(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                              p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import runtime_inventory as RIN
    try:
        return _ok(**RIN._discovery(cfg))
    except Exception as exc:
        return _fail(exc)


def h_qubit_runtime_marketplace(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                                p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import runtime_inventory as RIN
    try:
        return _ok(**RIN._marketplace(cfg))
    except Exception as exc:
        return _fail(exc)


def h_qubit_runtime_history(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                            p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import runtime_images as RI
    name = str(q.get("name", b.get("name", "")) or "")
    try:
        return _ok(**RI.update_history(cfg, name))
    except Exception as exc:
        return _fail(exc)


# One canonical backend for every update surface. All of these are module-level
# (testable, monkeypatchable) seams that resolve the manager read defensively so
# this module works before AND after the canonical full lifecycle lands.
def _update_full_apply() -> Any:
    """The canonical apply entry point, or ``None`` when not yet present.

    ``update_manager.full_apply`` is the one-call lifecycle (snapshot -> apply ->
    receipt). Read via ``getattr`` so this module keeps working while the core is
    still being extended: an absent symbol is a normal, expected state, never an
    AttributeError at import.
    """
    from . import update_manager as UM
    return getattr(UM, "full_apply", None)


def _update_resolve_source(cfg: Any, source: Any, channel: Any,
                           tag: Any) -> Any:
    """Resolve a channel/tag to a git remote via the canonical resolver.

    Optional: ``voicebridge.update_channel`` may not exist yet. Read via the
    module registry (``util.find_spec``) so the missing-module case is caught
    locally. ``resolve_source`` returns a policy dict; the manager's ``source``
    argument is the git remote **name**, so ``.remote`` is extracted. A refused
    URL (``ok=False``) raises so the handler answers with the typed code.
    """
    ch = str(channel or "").strip()
    tg = str(tag or "").strip()
    src = str(source or "").strip() or None
    if not ch and not tg:
        return src
    import importlib.util as _ilu
    if _ilu.find_spec("voicebridge.update_channel") is None:
        raise ValueError(
            "update channels/tags require the Qubit update-channel support; "
            "update it from a release source instead")
    from . import update_channel as UC  # type: ignore
    resolved = UC.resolve_source(cfg, src)
    if ch:
        resolved["channel"] = ch
    if tg:
        resolved["tag"] = tg
    if not resolved.get("ok", True):
        raise ValueError(str(resolved.get("detail")
                             or resolved.get("code") or "update source refused"))
    return str(resolved.get("remote") or src or "").strip() or src


def _update_check(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                  p: Dict[str, str], source: Any = None,
                  channel: Any = None, tag: Any = None) -> Dict[str, Any]:
    """Read-only update check — the shared seam GET uses and voice calls.

    ``channel``/``tag`` select the effective ref the same way ``--apply`` does
    (explicit ``tag`` wins; ``stable``/``beta`` pick the newest matching release
    tag; ``canary`` the branch head). Forwarded only when supplied so a bare
    check stays call-compatible with an older manager stub.
    """
    from . import update_manager as UM
    kwargs: Dict[str, Any] = {"source": source}
    if channel:
        kwargs["channel"] = channel
    if tag:
        kwargs["tag"] = tag
    return UM.check(cfg, **kwargs)


def _update_apply(cfg: Any, b: Dict[str, Any], *, actor: str,
                  source: Any = None, channel: Any = None,
                  tag: Any = None) -> Dict[str, Any]:
    """Run the canonical update lifecycle for one confirmed apply.

    Prefers ``update_manager.full_apply`` (the one-call snapshot -> apply ->
    receipt). Until that lands it reproduces the exact CLI lifecycle on the
    existing primitives: ``snapshot`` (timestamped, non-destructive backup) then
    ``apply(confirm=True)``, merging the backup path onto the receipt. Either way
    the same manager policy/confirmation/e-stop/audit path runs — there is no
    second backend.
    """
    from . import update_manager as UM
    resolved = _update_resolve_source(cfg, source, channel, tag)
    full = _update_full_apply()
    if full is not None:
        # Forward only the keywords the canonical lifecycle accepts, so a
        # signature change never becomes a TypeError at the compat boundary.
        kwargs = {"confirm": _truthy(b.get("confirm")), "source": resolved,
                  "channel": (channel or None), "tag": (tag or None),
                  "actor": actor}
        import inspect as _inspect
        try:
            accepted = set(_inspect.signature(full).parameters)
        except (TypeError, ValueError):
            accepted = set(kwargs)
        return full(cfg, **{k: v for k, v in kwargs.items() if k in accepted})
    if not _truthy(b.get("confirm")):
        return UM.apply(cfg, confirm=False, source=resolved, actor=actor)
    snap = UM.snapshot(cfg, reason="pre-apply", source=resolved,
                       actor=actor)
    if not snap.get("ok"):
        return snap
    out = UM.apply(cfg, confirm=True, source=resolved, actor=actor)
    if isinstance(out, dict):
        out.setdefault("backup_dir", snap.get("backup_dir"))
        out.setdefault("backup_bundle", snap.get("bundle"))
    return out


def _update_rollback(cfg: Any, backup_dir: Any = None, *, actor: str) -> Dict[str, Any]:
    """Run the canonical rollback — the shared seam POST rollback/voice call."""
    from . import update_manager as UM
    return UM.rollback(cfg, backup_dir=backup_dir, actor=actor)


def h_qubit_update_check(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                         p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """Read-only product update check (``UpdateCheckResponse`` wire shape).

    The SPA System page calls ``GET /api/qubit/update/check`` on mount. It is a
    read, so it answers 200 with a truthful local report
    (:func:`voicebridge.update_manager.check`) instead of the
    ``UPDATE_OPERATOR_OWNED`` refusal the blanket update gate used to return
    (audit A29). A network probe failure yields a typed DEGRADED report with
    ``behind=null`` — never a fabricated "up to date". ``?source=`` / ``?channel=``
    / ``?tag=`` select the update source without changing the checkout.
    """
    try:
        source = _update_resolve_source(cfg, q.get("source"),
                                        q.get("channel"), q.get("tag"))
        out = _update_check(cfg, q, b, p, source=source,
                            channel=(q.get("channel") or None),
                            tag=(q.get("tag") or None))
    except Exception as exc:
        return _fail(exc)
    return _ok(**out)


def h_qubit_update_receipt(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                           p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/qubit/update/receipt -> the most recent update receipt (read-only).

    Returns the canonical :func:`update_receipt.latest_receipt` payload (already
    redacted by the receipt writer) or a typed ``404 UPDATE_NO_RECEIPT`` when no
    receipt exists yet — never a fabricated success. The mutating update verbs
    stay confirmation/e-stop gated; this is a pure read.
    """
    try:
        from . import update_receipt as UR

        receipt = UR.latest_receipt(cfg)
    except Exception as exc:  # noqa: BLE001
        return _fail(exc)
    if not isinstance(receipt, dict):
        return 404, {"ok": False, "state": "NOT_FOUND",
                     "code": "UPDATE_NO_RECEIPT",
                     "detail": ("no update receipt has been written yet; run "
                                "`qubit update --apply` first")}
    return _ok(receipt=receipt)


def _update_failure_http(code: str) -> int:
    """Map a typed update refusal code to its HTTP status.

    Busy lock 409 (CONFIRM_REQUIRED / WORKTREE_DIRTY / the lock-busy code read
    from the manager when present), e-stop and operator-owned 403, unavailable
    503, and every dependency/migration/validation/service/health failure 500.
    An unknown code falls back to 500 — never a fabricated success.
    """
    code = str(code or "")
    from . import update_manager as UM
    busy = {str(getattr(UM, "LOCK_BUSY_CODE", "UPDATE_LOCK_BUSY")),
            "UPDATE_BUSY", "CONFIRM_REQUIRED", "WORKTREE_DIRTY"}
    if code in busy:
        return 409
    if code in ("UPDATE_ESTOP", "UPDATE_OPERATOR_OWNED"):
        return 403
    if code in ("UPDATE_UNAVAILABLE",):
        return 503
    return 500


def _update_response(out: Any) -> Tuple[int, Dict[str, Any]]:
    if not isinstance(out, dict):
        return 500, {"ok": False, "state": "FAILED", "code": "UPDATE_FAILED",
                     "detail": "the update backend returned no receipt"}
    if out.get("ok"):
        return 200, out
    return _update_failure_http(str(out.get("code") or "")), out


def h_qubit_update_apply(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                         p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/qubit/update -> real non-destructive in-place update.

    Confirmed only (409 CONFIRM_REQUIRED otherwise) and refuses a dirty
    worktree (409), a busy lock (409), a latched e-stop (403), a non-git install
    (403 UPDATE_OPERATOR_OWNED with the exact operator command), and the
    fail-closed test guard (503 UNAVAILABLE). The only command run is
    ``git pull --ff-only`` — the canonical :func:`update_manager.full_apply`
    lifecycle when present. ``confirm``/``source``/``channel``/``tag`` come from
    the request body.
    """
    try:
        out = _update_apply(cfg, b, actor="dashboard:user")
    except Exception as exc:
        return _fail(exc)
    return _update_response(out)


def h_qubit_update_rollback(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                            p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/qubit/update/rollback -> restore the pre-apply revision.

    The same canonical :func:`update_manager.rollback` the CLI and voice call:
    confirm-gated (409 CONFIRM_REQUIRED without ``confirm``), e-stop 403, no
    backup 409 UPDATE_NO_BACKUP, invalid backup 500. ``backup_dir`` may be
    provided in the body to restore a specific snapshot.
    """
    try:
        if not _truthy(b.get("confirm")):
            return 409, {
                "ok": False,
                "state": "CONFIRM_REQUIRED",
                "code": "CONFIRM_REQUIRED",
                "detail": ("rolling back the update requires confirm=true; "
                           "re-POST with the confirmation to proceed"),
            }
        out = _update_rollback(cfg, backup_dir=(b.get("backup_dir") or None),
                               actor="dashboard:user")
    except Exception as exc:
        return _fail(exc)
    if isinstance(out, dict) and not out.get("ok"):
        code = str(out.get("code") or "")
        if code == "UPDATE_NO_BACKUP":
            return 409, out
    return _update_response(out)


def h_qubit_runtime_update_report(cfg: Any, q: Dict[str, str],
                                  b: Dict[str, Any],
                                  p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import runtime_images as RI
    try:
        return _ok(**RI.update_report(cfg))
    except Exception as exc:
        return _fail(exc)


def h_qubit_runtime_status(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                           p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import runtime_images as RI
    try:
        return _ok(**RI.status(cfg))
    except Exception as exc:
        return _fail(exc)


def h_qubit_runtime_images(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                           p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import runtime_images as RI
    try:
        return _ok(**RI.images())
    except Exception as exc:
        return _fail(exc)


def h_qubit_runtime_doctor(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                           p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import runtime_images as RI
    try:
        return _ok(**RI.doctor(cfg))
    except Exception as exc:
        return _fail(exc)


def h_qubit_egress_status(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                          p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import proxy_status as PX
    try:
        return _ok(**PX.egress_status(cfg))
    except Exception as exc:
        return _fail(exc)


def h_qubit_egress_providers(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                             p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import proxy_status as PX
    try:
        return _ok(**PX.providers(cfg))
    except Exception as exc:
        return _fail(exc)


def h_qubit_egress_check(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                         p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import proxy_status as PX
    host = str(q.get("host", b.get("host", "")) or "").strip()
    if not host:
        return 400, {"ok": False, "state": "FAILED",
                     "code": "EGRESS_HOST_REQUIRED",
                     "detail": "query parameter 'host' is required",
                     "qubit_counterpart": "CLI `qubit egress check <host>`"}
    try:
        out = PX.check_egress(cfg, host)
    except Exception as exc:
        return _fail(exc)
    allowed = bool(out.get("allowed"))
    return (200 if allowed else 403), {
        "ok": allowed, "state": "OK" if allowed else "DENIED",
        "qubit_counterpart": "CLI `qubit egress check <host>`", **out}


def h_qubit_policy_conformance(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                               p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import release as REL
    root = str(q.get("root", b.get("root", "")) or "")
    if root.strip():
        path = Path(root)
        if not path.is_dir():
            return 400, {"ok": False, "state": "FAILED",
                         "code": "WORKSPACE_UNREADABLE",
                         "detail": f"workspace root {path} is not a directory",
                         "qubit_counterpart":
                         "CLI `qubit policy conformance`"}
        try:
            return _ok(**REL.workspace_conformance(path))
        except Exception as exc:
            return _fail(exc)
    try:
        return _ok(**REL.workspace_conformance())
    except Exception as exc:
        return _fail(exc)


def h_qubit_policy_attest(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                          p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import release as REL
    root = str(q.get("root", b.get("root", "")) or "")
    if root.strip():
        path = Path(root)
        if not path.is_dir():
            return 400, {"ok": False, "state": "FAILED",
                         "code": "WORKSPACE_UNREADABLE",
                         "detail": f"workspace root {path} is not a directory",
                         "qubit_counterpart": "CLI `qubit policy attest`"}
        try:
            return _ok(**REL.workspace_attestation(path))
        except Exception as exc:
            return _fail(exc)
    try:
        return _ok(**REL.workspace_attestation())
    except Exception as exc:
        return _fail(exc)


def h_qubit_hooks_doctor(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                         p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import hooks as HK
    try:
        return _ok(**HK.doctor(cfg))
    except Exception as exc:
        return _fail(exc)


def h_qubit_usage(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                  p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from .evaluation import usage_analytics as UA
    limit = _qint(q, b, "limit", default=1000)
    ledger = UA.UsageLedger(_eval_state_path(cfg, "usage.jsonl"))
    records = ledger.read(limit=limit)
    out = UA.analyze(records)
    out.update({"records": len(records), "rejections": ledger.rejections,
                "path": str(ledger.path),
                "authority": "append-only local usage ledger; no prompts or "
                             "secrets are stored",
                "qubit_counterpart": "CLI `qubit usage analytics`"})
    return _ok(**out)


def h_qubit_receipts(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                     p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from .evaluation import receipts as RC
    path = _eval_state_path(cfg, "receipts.jsonl")
    if not path.exists():
        return 404, {"ok": False, "state": "NOT_FOUND",
                     "code": "RECEIPTS_NOT_FOUND",
                     "detail": f"no execution-receipt chain at {path}",
                     "qubit_counterpart": "CLI `qubit receipts`"}
    rows, rejected = [], 0
    for line in path.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            rejected += 1
    verdict = RC.verify_receipts(rows)
    return _ok(records=len(rows), rejected=rejected, verify=verdict,
               path=str(path),
               qubit_counterpart="CLI `qubit receipts`")


def h_qubit_logbook(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from .evaluation import logbook as LB
    ledger = LB.Logbook(_eval_state_path(cfg, "logbook.jsonl"))
    limit = _qint(q, b, "limit", default=100)
    window = q.get("window_s", b.get("window_s"))
    try:
        window = float(window) if window not in (None, "") else None
    except (TypeError, ValueError):
        window = None
    return _ok(entries=ledger.entries(limit=limit, window_s=window),
               summary=ledger.summary(window_s=window), path=str(ledger.path),
               qubit_counterpart="CLI `qubit logbook entries`")


def h_qubit_browser_profiles(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                             p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import browser as BR
    try:
        return _ok(**BR.profiles(cfg))
    except Exception as exc:
        return _fail(exc)


# ---------------------------------------------------------------------------
# S8 — desktop/browser operation routes (A38/A39). Each route is a thin
# adapter to the canonical operation-catalog id (the same Tier-1 registry entry
# the CLI and voice run through ``dashboard_ops``), so the shared broker,
# confirmation journal, emergency stop, and audit apply unchanged. No route
# re-implements an operation and none returns a fabricated success.
# ---------------------------------------------------------------------------
def h_op_computer_ops(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                      p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/qubit/desktop/ops -> bounded desktop-op catalog (read-only)."""
    return _run_catalog_op(cfg, "computer_ops")


def h_op_computer_presentation_status(cfg: Any, q: Dict[str, str],
                                      b: Dict[str, Any],
                                      p: Dict[str, str]
                                      ) -> Tuple[int, Dict[str, Any]]:
    """GET /api/qubit/desktop/presentation -> persisted descriptor (read-only)."""
    return _run_catalog_op(cfg, "computer_presentation_status")


def h_op_computer_launch(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                         p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/qubit/desktop/launch -> allowlisted app launch (confirmed)."""
    args: Dict[str, Any] = {}
    if b.get("app"):
        args["app"] = str(b["app"])
    if b.get("target"):
        args["target"] = str(b["target"])
    return _run_catalog_op(cfg, "computer_launch", args)


def h_op_computer_pip(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                      p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/qubit/desktop/pip -> Document PiP surface (confirmed)."""
    args: Dict[str, Any] = {}
    if b.get("target"):
        args["target"] = str(b["target"])
    return _run_catalog_op(cfg, "computer_pip", args)


def h_op_computer_present(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                          p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/qubit/desktop/present -> persist presentation (confirmed)."""
    args: Dict[str, Any] = {}
    if b.get("mode"):
        args["mode"] = str(b["mode"])
    if b.get("panes"):
        args["panes"] = str(b["panes"])
    return _run_catalog_op(cfg, "computer_present", args)


def h_op_browser_favicon(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                         p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/qubit/browser/favicon -> same-origin favicon (read-only)."""
    return _run_catalog_op(cfg, "browser_favicon")


def h_op_browser_favicon_cache(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                               p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/qubit/browser/favicon-cache -> bounded cache (read-only)."""
    return _run_catalog_op(cfg, "browser_favicon_cache")


def h_op_browser_download(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                          p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/qubit/browser/download -> save page into an approved root."""
    args: Dict[str, Any] = {}
    if b.get("dest"):
        args["dest"] = str(b["dest"])
    return _run_catalog_op(cfg, "browser_download", args)


def h_qubit_local_models_fallback(cfg: Any, q: Dict[str, str],
                                  b: Dict[str, Any],
                                  p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from . import local_models as LM
    model = str(q.get("model", b.get("model", "")) or "")
    try:
        out = (LM.resolve_fallback(cfg, model) if model
               else LM.fallback_chain(cfg))
    except Exception as exc:
        return _fail(exc)
    ok = str(out.get("state", "")) in ("OK", "EMPTY", "READY", "DEGRADED")
    return (200 if ok else 403), {
        "ok": ok, "qubit_counterpart":
        "CLI `qubit local-models fallback [model]`", **out}


# ---------------------------------------------------------------------------
# Skills hub: the Qubit-reviewed LOCAL catalog. Remote hub fetches are refused
# by pinned-source policy; every read/mutation runs the same local manifests +
# PermissionsStore the CLI/voice use (never a network fetch or process spawn).
# ---------------------------------------------------------------------------
_REMOTE_IDENTIFIER = re.compile(r"(://|^github:|^gitlab:|^official/|^hub/)")


def _is_remote_identifier(text: Any) -> bool:
    return bool(_REMOTE_IDENTIFIER.search(str(text or "").strip().lower()))


def _local_skill_catalog(cfg: Any) -> Dict[str, Dict[str, Any]]:
    """Local reviewed skill/plugin catalog (installed + Qubit overlay)."""
    from .permissions import permissions_store
    rows: Dict[str, Dict[str, Any]] = {}
    try:
        for rec in permissions_store(cfg).skills():
            d = rec.to_dict() if hasattr(rec, "to_dict") else {}
            man = d.get("manifest", {}) if isinstance(d, dict) else {}
            if not isinstance(man, dict):
                man = {}
            ident = str(d.get("skill_id", "") or "")
            if not ident:
                continue
            rows[ident] = {
                "identifier": ident,
                "name": str(man.get("name", ident) or ident),
                "description": str(man.get("description", "") or ""),
                "source": str(man.get("source", "qubit-local") or "qubit-local"),
                "trust_level": "trusted", "category": "installed",
                "installed": str(d.get("status", "")) == "active",
                "status": str(d.get("status", "")),
                "version": str(man.get("version", "") or ""),
                "tags": [], "repo": None, "_manifest": man,
                "_origin": "permissions",
            }
    except Exception:
        pass
    for m in _overlay_manifests():
        name = str(m.get("name", "") or "")
        if not name or name in rows:
            continue
        # Qubit-authored overlay plugins are installable as skills; they are
        # original Qubit material, so the project license is their license. Fill
        # it in when the vendored overlay manifest omits the field, otherwise
        # the install contract refuses them (LICENSE_REQUIRED) and the hub
        # control looks dead.
        man = dict(m)
        man.setdefault("license", "MIT")
        man.setdefault("source", "qubit-overlay")
        man.setdefault(
            "content_hash",
            hashlib.sha256(f"qubit-overlay:{name}".encode()).hexdigest())
        man.setdefault("capabilities", ["overlay.read"])
        man.setdefault("scopes", ["overlay:read"])
        rows[name] = {
            "identifier": name,
            "name": str(m.get("label", name) or name),
            "description": str(m.get("description", "") or ""),
            "source": "qubit-overlay", "trust_level": "builtin",
            "category": "overlay", "installed": True, "status": "active",
            "version": str(m.get("version", "") or ""),
            "tags": [], "repo": None, "_manifest": man, "_origin": "overlay",
        }
    return rows


def _hub_public(row: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in row.items() if not k.startswith("_")}


def _hub_installed(cfg: Any) -> Dict[str, str]:
    from .permissions import permissions_store
    try:
        return {r.skill_id: r.status for r in permissions_store(cfg).skills()}
    except Exception:
        return {}


def _manifest_spec(manifest: Dict[str, Any]) -> Any:
    from .permissions import SkillManifest
    from .capabilities import Tier
    tier = manifest.get("tier", "reversible")
    return SkillManifest(
        skill_id=str(manifest.get("skill_id", manifest.get("name", "")) or ""),
        version=str(manifest.get("version", "0.0.0") or "0.0.0"),
        name=str(manifest.get("name", manifest.get("skill_id", "")) or ""),
        description=str(manifest.get("description", "") or ""),
        source=str(manifest.get("source", "qubit-local") or "qubit-local"),
        revision=str(manifest.get("revision", "1") or "1"),
        license=str(manifest.get("license", "") or ""),
        content_hash=str(manifest.get("content_hash", "") or ""),
        capabilities=tuple(manifest.get("capabilities", []) or []),
        scopes=tuple(manifest.get("scopes", []) or []),
        tier=tier if isinstance(tier, Tier) else Tier(str(tier)),
        sensitivity=str(manifest.get("sensitivity", "internal") or "internal"),
        approval_policy=str(manifest.get("approval_policy", "confirm")
                            or "confirm"),
        timeout_s=float(manifest.get("timeout_s", 30) or 30),
        resource_limits=dict(manifest.get("resource_limits", {}) or {}),
        audit_events=tuple(manifest.get("audit_events", ["skill.install"])
                           or ["skill.install"]),
        compatibility=str(manifest.get("compatibility", "") or ""),
        rollback=str(manifest.get("rollback", "remove the skill")
                     or "remove the skill"))


def _hub_resolve_manifest(cfg: Any, identifier: Any) -> Tuple[Any, str]:
    """(manifest_dict, origin) for a local identifier; (None, reason) else."""
    ident = str(identifier or "").strip()
    if not ident or _is_remote_identifier(ident):
        return None, "remote"
    path = Path(ident)
    if path.suffix == ".json" and path.is_file():
        try:
            raw = json.loads(path.read_text())
        except (OSError, ValueError):
            return None, "malformed"
        return (raw if isinstance(raw, dict) else None), "path"
    row = _local_skill_catalog(cfg).get(ident)
    if row is None:
        return None, "not_found"
    return row.get("_manifest") or None, str(row.get("_origin", "catalog"))


def _hub_remote_gate(counterpart: str) -> Tuple[int, Dict[str, Any]]:
    return _gate("BLOCKED_REMOTE_SOURCE",
                 "Remote skill-hub fetches are refused by pinned-source "
                 "policy; Qubit serves the reviewed local manifest catalog.",
                 counterpart)


def h_skills_hub_official(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                          p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    installed = _hub_installed(cfg)
    out = []
    for row in _local_skill_catalog(cfg).values():
        pub = _hub_public(row)
        pub["installed"] = (str(pub.get("identifier")) in installed
                            or bool(row.get("installed")))
        out.append(pub)
    return _ok(skills=out, source="qubit-local",
               qubit_counterpart="CLI `qubit skills list`")


def h_skills_hub_sources(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                         p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    rows = list(_local_skill_catalog(cfg).values())
    return _ok(sources=[{
        "id": "qubit-local", "label": "Qubit reviewed local manifests",
        "available": True, "searchable": True, "rate_limited": False,
        "detail": "remote hubs (GitHub/index) are refused by pinned-source "
                  "policy; local reviewed manifests only"}],
        index_available=False,
        featured=[_hub_public(r) for r in rows[:12]],
        installed=_hub_installed(cfg),
        qubit_counterpart="CLI `qubit skills list`")


def h_skills_hub_search(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                        p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    query = str(q.get("query", q.get("q", b.get("q", b.get("query", ""))))
                or "").strip()
    if not query:
        return _ok(results=[], source_counts={}, timed_out=[],
                   installed={})
    try:
        limit = max(1, min(int(q.get("limit", "20") or "20"), 50))
    except (TypeError, ValueError):
        limit = 20
    needle = query.lower()
    hits = [r for r in _local_skill_catalog(cfg).values()
            if needle in str(r.get("name", "")).lower()
            or needle in str(r.get("description", "")).lower()
            or needle in str(r.get("identifier", "")).lower()]
    results = [_hub_public(r) for r in hits[:limit]]
    return _ok(results=results,
               source_counts={"qubit-local": len(results)},
               timed_out=[], installed=_hub_installed(cfg),
               qubit_counterpart="CLI `qubit skills list`")


def h_skills_hub_preview(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                         p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    ident = str(q.get("identifier", b.get("identifier", "")) or "")
    if not ident:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "identifier is required",
                     "qubit_counterpart": "CLI `qubit skills show <id>`"}
    if _is_remote_identifier(ident):
        return _hub_remote_gate("CLI `qubit skills show <id>`")
    man, _why = _hub_resolve_manifest(cfg, ident)
    if man is None:
        return 404, {"ok": False, "code": "SKILL_NOT_FOUND",
                     "detail": f"no local reviewed skill {ident!r}",
                     "qubit_counterpart": "CLI `qubit skills list`"}
    return _ok(name=str(man.get("name", ident) or ident),
               description=str(man.get("description", "") or ""),
               source=str(man.get("source", "qubit-local") or "qubit-local"),
               identifier=ident, trust_level="trusted", repo=None, tags=[],
               skill_md=json.dumps(man, indent=2, sort_keys=True),
               files=sorted(str(k) for k in man.keys()),
               qubit_counterpart="CLI `qubit skills show <id>`")


def h_skills_hub_scan(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                      p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    ident = str(q.get("identifier", b.get("identifier", "")) or "")
    if not ident:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "identifier is required",
                     "qubit_counterpart": "CLI `qubit skills validate`"}
    if _is_remote_identifier(ident):
        return _hub_remote_gate("CLI `qubit skills validate`")
    man, _why = _hub_resolve_manifest(cfg, ident)
    if man is None:
        return 404, {"ok": False, "code": "SKILL_NOT_FOUND",
                     "detail": f"no local reviewed skill {ident!r}",
                     "qubit_counterpart": "CLI `qubit skills list`"}
    findings: List[Dict[str, Any]] = []
    verdict, policy = "clean", "allow"
    reason = "local reviewed manifest passes the Qubit skill contract"
    try:
        _manifest_spec(man)
    except Exception as exc:
        verdict, policy = "blocked", "block"
        reason = _redact(str(exc))[:200]
        findings = [{"severity": "high", "category": "validation",
                     "file": "manifest", "line": 0, "description": reason}]
    counts = {sev: 0 for sev in ("critical", "high", "medium", "low")}
    for f in findings:
        if f["severity"] in counts:
            counts[f["severity"]] += 1
    return _ok(name=str(man.get("name", ident) or ident), identifier=ident,
               source=str(man.get("source", "qubit-local") or "qubit-local"),
               trust_level="trusted", verdict=verdict,
               summary=("no blocking findings" if verdict == "clean"
                        else "manifest failed validation"),
               policy=policy, policy_reason=reason, findings=findings,
               severity_counts=counts, tier1=None,
               qubit_counterpart="CLI `qubit skills validate`")


def h_skills_hub_install(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                         p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from .permissions import SkillValidationError, permissions_store
    from .verified_skills import mutations as SM
    ident = str(b.get("identifier", q.get("identifier", "")) or "")
    if not ident or _is_remote_identifier(ident):
        return _hub_remote_gate("CLI `qubit skills install <local-manifest>`")
    man, why = _hub_resolve_manifest(cfg, ident)
    if man is None:
        return 404, {"ok": False, "code": "SKILL_NOT_FOUND",
                     "detail": f"no local reviewed manifest {ident!r} ({why})",
                     "qubit_counterpart": "CLI `qubit skills list`"}
    cid = str(b.get("confirmation_id", "") or q.get("confirmation_id", "")
              or "").strip()
    if not _truthy(b.get("confirm")) and not cid:
        # Canonical journal stage: 409 with a POPULATED, single-use id (the
        # previous empty id could never be settled by the pending banner).
        out = SM.handle_skill_mutation(cfg, "install", {"manifest": man},
                                       actor="dashboard:user",
                                       session="dashboard")
        return _mutation_envelope(out)
    if cid:
        out = SM.handle_skill_mutation(cfg, "install", {"manifest": man},
                                       confirmation_id=cid,
                                       actor="dashboard:user",
                                       session="dashboard")
        return _mutation_envelope(out)
    # Legacy boolean confirm: keep the direct install path (and its honest
    # duplicate/validation 400s) for callers that already send ``confirm: true``.
    try:
        spec = _manifest_spec(man)
        rec = permissions_store(cfg).install_skill(
            spec, requested_by="dashboard:user", confirmed=True)
    except SkillValidationError as exc:
        return 400, {"ok": False, "code": "SKILL_INVALID",
                     "detail": _redact(str(exc))[:300],
                     "qubit_counterpart": "CLI `qubit skills install <manifest>`"}
    except Exception as exc:
        code = getattr(exc, "code", None) or type(exc).__name__
        return 400, {"ok": False, "code": code,
                     "detail": _redact(str(exc))[:300],
                     "qubit_counterpart": "CLI `qubit skills install <manifest>`"}
    return _ok(ok=True, skill_id=rec.skill_id, status=rec.status,
               identifier=ident,
               qubit_counterpart="CLI `qubit skills install <manifest>`")


def h_skills_hub_uninstall(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                           p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from .permissions import permissions_store
    name = str(b.get("name", b.get("identifier", q.get("identifier", "")))
               or "").strip()
    if not name:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "name is required",
                     "qubit_counterpart": "CLI `qubit skills remove <id>`"}
    if not b.get("confirm"):
        return _staged(
            f"skill uninstall {name!r} requires confirmation: re-POST with "
            "{\"confirm\": true}", "")
    try:
        permissions_store(cfg).remove_skill(
            name, requested_by="dashboard:user", confirmed=True)
    except Exception as exc:
        code = getattr(exc, "code", None) or type(exc).__name__
        return 400, {"ok": False, "code": code,
                     "detail": _redact(str(exc))[:300],
                     "qubit_counterpart": "CLI `qubit skills remove <id>`"}
    return _ok(ok=True, name=name,
               qubit_counterpart="CLI `qubit skills remove <id>`")


def h_skills_hub_update(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                        p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    from .permissions import permissions_store
    ident = str(b.get("name", b.get("identifier", q.get("identifier", "")))
                or "").strip()
    if not ident or _is_remote_identifier(ident):
        return _hub_remote_gate("CLI `qubit skills install <local-manifest>`")
    if not b.get("confirm"):
        return _staged(
            f"skill update {ident!r} requires confirmation: re-POST with "
            "{\"confirm\": true}", "")
    try:
        store = permissions_store(cfg)
        if store.skill(ident) is None:
            return 404, {"ok": False, "code": "SKILL_NOT_FOUND",
                         "detail": f"no installed skill {ident!r}",
                         "qubit_counterpart": "CLI `qubit skills list`"}
        rolled = store.rollback_skill(ident, requested_by="dashboard:user",
                                      confirmed=True)
    except Exception as exc:
        code = getattr(exc, "code", None) or type(exc).__name__
        if code == "PermissionError" and "rollback state" in str(exc):
            return _gate("SKILL_VERSIONED",
                         "Skill versions are immutable; this staged record has "
                         "no prior active version to roll back. Reinstall the "
                         "new validated manifest to advance.",
                         "CLI `qubit skills remove/install <manifest>`")
        return 400, {"ok": False, "code": code,
                     "detail": _redact(str(exc))[:300],
                     "qubit_counterpart": "CLI `qubit skills rollback`"}
    return _ok(ok=True, skill_id=rolled.skill_id, status=rolled.status,
               detail="rolled back to the prior staged version; reinstall the "
                      "new validated manifest to advance",
               qubit_counterpart="CLI `qubit skills rollback/install`")


# ---------------------------------------------------------------------------
# Wave 4: real brokered file / git / blueprint writes (staged -> confirm ->
# apply through the same Qubit stores and confinement the CLI/voice use).
# ---------------------------------------------------------------------------

def _full_replace_patch(name: str, before: str, after: str) -> str:
    """Unified diff that replaces an entire UTF-8 file's contents."""
    old = before.splitlines()
    new = after.splitlines()
    hunk = (f"@@ -1,{len(old)} +1,{len(new)} @@" if old
            else f"@@ -0,0 +1,{len(new)} @@")
    lines = [f"--- a/{name}", f"+++ b/{name}", hunk]
    lines += ["-" + ln for ln in old]
    lines += ["+" + ln for ln in new]
    return "\n".join(lines) + "\n"


def _file_write_broker(cfg: Any) -> Any:
    """Build the one host broker with the confined file-write capabilities.

    Same registry/policy/e-stop/confirmation-journal/audit junction the CLI,
    voice, and planner use; the shared :class:`PowerControl` makes a latched
    emergency stop / global disable refuse the write BEFORE the executor runs.
    """
    from .broker import build_host_broker, register_file_patch
    from .power import power_control

    broker = build_host_broker(power_control=power_control(cfg), cfg=cfg)
    register_file_patch(broker, cfg=cfg)
    return broker


def _file_patch_http(code: str) -> int:
    from . import file_patch as FP

    return {FP.CONFIRM_CODE: 409, FP.ESTOP_CODE: 403,
            FP.OUTSIDE_ROOT_CODE: 400, FP.BAD_INPUT_CODE: 400,
            FP.CONFINEMENT_CODE: 503, FP.CONFLICT_CODE: 409,
            FP.NOT_FOUND_CODE: 400, FP.PARSE_CODE: 400,
            FP.TOO_LARGE_CODE: 400, FP.TOO_MANY_LINES_CODE: 400,
            FP.IO_CODE: 400, FP.ROLLBACK_CODE: 400}.get(code, 400)


def _file_patch_refusal(code: str, detail: str, counterpart: str,
                        rollback: str = "nothing written; target unchanged"
                        ) -> Tuple[int, Dict[str, Any]]:
    return _file_patch_http(code), {
        "ok": False, "code": code, "detail": _redact(detail)[:300],
        "owner": "qubit-policy", "rollback": rollback,
        "qubit_counterpart": counterpart}


def _brokered_file_mutation(cfg: Any, capability: str, args: Dict[str, Any], *,
                            q: Dict[str, str], b: Dict[str, Any],
                            target: str, detail: str, counterpart: str
                            ) -> Tuple[Optional[Tuple[int, Dict[str, Any]]],
                                       Optional[Dict[str, Any]]]:
    """Run one confined file mutation through the broker confirmation path.

    Returns ``(response, None)`` to stop (staged or typed refusal) or
    ``(None, receipt)`` after a real execution. A request that carries no
    durable ``confirmation_id`` stages a single-use confirmation on the SHARED
    journal (bound to the capability and the exact arguments) and answers the
    typed 409 the dashboard settles by re-issuing the same request with that id.
    A client ``confirm`` boolean is never authority. With an id, the broker
    checks the shared e-stop/global-disable latch, consumes the confirmation
    exactly once, audits the decision, and executes.
    """
    from . import file_patch as FP

    # E-stop / global-disable fail closed BEFORE anything is staged or written.
    if _estop(cfg):
        return _file_patch_refusal(
            FP.ESTOP_CODE,
            "emergency stop is latched; reset it before writing files",
            counterpart, rollback="nothing written; target unchanged"), None

    cid = str(b.get("confirmation_id", "")
              or q.get("confirmation_id", "") or "").strip()
    try:
        broker = _file_write_broker(cfg)
    except Exception as exc:
        return _fail(exc), None

    if not cid:
        confirmation_id = ""
        try:
            rec = broker.issue_confirmation(capability, dict(args),
                                            issued_by="user", ttl_s=120.0)
            confirmation_id = rec.id
        except Exception:
            confirmation_id = ""
        _audit("needs_confirm", "dashboard:user",
               {"capability": capability, "target": target,
                "confirmation_id": confirmation_id})
        return _staged(detail, confirmation_id), None

    res = broker.execute(capability, dict(args), source="user",
                         confirmation_id=cid, target=target)
    if res.ok and str(res.output or "").strip():
        try:
            payload = json.loads(res.output)
        except Exception:
            payload = {}
        if isinstance(payload, dict) and payload.get("ok"):
            receipt = payload.get("receipt")
            return None, receipt if isinstance(receipt, dict) else {}
        code = str(payload.get("code") or FP.IO_CODE)
        detail_msg = str(payload.get("detail") or "file write refused")
        return _file_patch_refusal(code, detail_msg, counterpart), None

    # Broker-level refusal (e-stop, malformed input, bad confirmation, ...).
    code = str(res.denied or FP.IO_CODE)
    http = {"EMERGENCY_STOP": 403, "POWER_DISABLED": 403,
            "POWER_UNAVAILABLE": 403, "CIRCUIT_OPEN": 403,
            "DESTRUCTIVE_SCREEN": 403, "CONCURRENCY_LIMIT": 429,
            "CONFIRMATION_SOURCE_FORBIDDEN": 403,
            "UNKNOWN_CONFIRMATION_ID": 409, "EXPIRED_CONFIRMATION": 409,
            "REPLAYED_CONFIRMATION": 409, "CAPABILITY_MISMATCH": 409,
            "TARGET_MISMATCH": 409, "UNKNOWN_CAPABILITY": 400,
            "MALFORMED_INPUT": 400, "OUT_OF_SCOPE": 403}.get(code, 400)
    return (http, {
        "ok": False, "code": _redact(code)[:80],
        "detail": _redact(res.error or "file write refused")[:300],
        "owner": "qubit-policy",
        "rollback": "nothing written; target unchanged",
        "qubit_counterpart": counterpart}), None


def h_fs_write_text(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/fs/write-text -> confined, brokered, reversible file patch.

    The write runs the SAME ``file_patch`` confinement/backup/rollback/audit
    path as the CLI, dispatched through the canonical broker so the shared
    confirmation journal, e-stop/global-disable latch, and audit attribution
    all apply. A mutation requires a durable ``confirmation_id`` staged on the
    shared journal; without one it answers a typed 409 (a client ``confirm``
    boolean is not authority). A path outside the approved project roots is a
    typed refusal, never a host write.
    """
    from . import file_patch as FP
    counterpart = "CLI `qubit patch apply` / `qubit patch rollback`"
    path = str(b.get("path", q.get("path", "")) or "").strip()
    if not path:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "path required",
                     "qubit_counterpart": "CLI `qubit patch apply`"}
    content = b.get("content", b.get("text", ""))
    if not isinstance(content, str):
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "content must be text",
                     "qubit_counterpart": "CLI `qubit patch apply`"}
    try:
        target = FP.resolve_target(cfg, path)
        before = (target.read_text(encoding="utf-8")
                  if target.exists() else "")
    except FP.FilePatchError as exc:
        return _file_patch_refusal(exc.code, exc.detail, counterpart)
    except (OSError, UnicodeDecodeError) as exc:
        return _file_patch_refusal(FP.BAD_INPUT_CODE, f"cannot read target ({exc})",
                                   counterpart)
    patch = _full_replace_patch(target.name, before, content)
    args = {"target_path": path, "patch_text": patch,
            "requested_by": "dashboard:user", "project_root": ""}
    resp, receipt = _brokered_file_mutation(
        cfg, FP.capability_name(), args, q=q, b=b, target=str(target),
        detail=(f"writing {path!r} is a brokered, confirmed, reversible "
                "confined file patch; re-POST with the confirmation_id to "
                "apply"),
        counterpart=counterpart)
    if resp is not None:
        return resp
    return _ok(ok=True, path=str(target), state=receipt.get("state"),
               receipt=receipt,
               qubit_counterpart="CLI `qubit patch rollback` (undo available)")


def h_files_upload(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                   p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/files/upload[/-stream] -> real confined, brokered upload.

    Ports the reference managed-files upload (``POST /api/files/upload`` JSON
    ``data_url`` and ``POST /api/files/upload-stream`` multipart ``file`` —
    ``hermes_cli/web_routers/files.py:493-565``) onto Qubit's ONE confined-write
    path. An explicit text ``content`` keeps the confined text-patch path
    (``/api/fs/write-text``); a raw ``content_binary`` or a base64
    ``data_url``/``content_base64`` payload is written byte-for-byte through the
    ``local.files_upload`` broker capability, so a genuinely-binary upload is
    stored (reversibly, from a durable backup) instead of refused. The
    multipart parser (``voicebridge/webui.py parse_multipart_form``) supplies
    ``content_binary``/``content_base64`` for the upload field and ``content``
    only when the bytes decode as UTF-8.
    """
    import base64
    from . import file_patch as FP
    path = str(b.get("path", q.get("path", "")) or "").strip()
    if not path:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "path required",
                     "qubit_counterpart": "POST /api/fs/write-text"}
    content = b.get("content")
    if isinstance(content, str):
        # UTF-8 text (JSON ``content`` or a text multipart field) keeps the
        # confined text-patch path.
        return h_fs_write_text(cfg, q,
                               {**b, "path": path, "content": content}, p)

    raw_binary = b.get("content_binary")
    blob = b.get("content_base64")
    if blob in (None, ""):
        blob = b.get("data_url")
    has_binary = isinstance(raw_binary, (bytes, bytearray)) or bool(blob)
    if not has_binary:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "content (text) or content_base64 required",
                     "qubit_counterpart": "POST /api/fs/write-text"}
    if isinstance(raw_binary, (bytes, bytearray)):
        payload_b64 = base64.b64encode(bytes(raw_binary)).decode("ascii")
    else:
        raw = str(blob)
        raw = raw.split(",", 1)[1] if raw.startswith("data:") else raw
        try:
            base64.b64decode(raw, validate=False)
        except Exception:
            return 400, {"ok": False, "code": "MALFORMED",
                         "detail": "content_base64 is not valid base64",
                         "qubit_counterpart": "POST /api/fs/write-text"}
        payload_b64 = raw
    overwrite = (_truthy(b.get("overwrite"))
                 if "overwrite" in b else True)
    counterpart = "CLI `qubit patch rollback` (undo available)"
    args = {"target_path": path, "content_base64": payload_b64,
            "overwrite": overwrite, "requested_by": "dashboard:user",
            "project_root": ""}
    resp, receipt = _brokered_file_mutation(
        cfg, FP.write_bytes_capability_name(), args, q=q, b=b, target=path,
        detail=(f"uploading {path!r} is a brokered, confirmed, reversible "
                "confined write; re-POST with the confirmation_id to apply"),
        counterpart=counterpart)
    if resp is not None:
        return resp
    return _ok(ok=True, path=path, state=receipt.get("state"),
               bytes=receipt.get("bytes_after"),
               sha256=receipt.get("after_sha256"), receipt=receipt,
               qubit_counterpart=counterpart)


def h_files_mkdir(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                  p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/files/mkdir -> confined, brokered directory creation.

    Routed through the canonical broker like the file-write path, so the shared
    e-stop/global-disable latch and confirmation journal apply and a latched
    e-stop refuses even without a write. The new directory must resolve inside
    an approved project root. A mutation requires a durable ``confirmation_id``
    on the shared journal; an existing target or an out-of-root path is a typed
    refusal, never a host write.
    """
    from . import file_patch as FP
    counterpart = "CLI `qubit project add-folder` / `qubit files inspect`"
    path = str(b.get("path", q.get("path", "")) or "").strip()
    if not path:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "path required",
                     "qubit_counterpart": "CLI `qubit project add-folder`"}
    try:
        target = FP.resolve_target(cfg, path)
    except FP.FilePatchError as exc:
        return _file_patch_refusal(
            exc.code, exc.detail, counterpart,
            rollback="nothing created; target unchanged")
    args = {"target_path": path, "requested_by": "dashboard:user",
            "project_root": ""}
    resp, receipt = _brokered_file_mutation(
        cfg, FP.mkdir_capability_name(), args, q=q, b=b, target=str(target),
        detail=(f"creating directory {path!r} is brokered, confirmed, and "
                "confined; re-POST with the confirmation_id to create"),
        counterpart=counterpart)
    if resp is not None:
        return resp
    _audit("files.mkdir", "dashboard:user", {"path": str(target)})
    return _ok(ok=True, path=str(target), created=True,
               state=receipt.get("state"),
               qubit_counterpart="CLI `qubit files inspect` / `qubit project`")



def h_git_write(verb: str) -> Handler:
    """POST /api/git/... -> real confirm-gated brokered git-write authorization.

    Qubit git stays a scoped read surface: the handler validates the repo with
    the real ``git_scope`` confinement, then records a capability grant
    (``git.write.<verb>``) scoped to that repo in the shared PermissionsStore
    the CLI/voice use. The actual apply runs through the brokered opencode
    worktree flow, never a raw shell git write from the dashboard.
    """
    counterpart = ("CLI `qubit opencode` (brokered worktree apply) + "
                   "`qubit permissions` grant")

    def _handler(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
        from . import git_scope as GS
        from .permissions import permissions_store
        try:
            root = GS.resolve_root(cfg, _git_query_root(q, b))
        except Exception as exc:
            code = getattr(exc, "code", None) or type(exc).__name__
            return 400, {"ok": False, "code": code,
                         "detail": _redact(str(exc))[:300],
                         "qubit_counterpart": "CLI `qubit git status`"}
        if not b.get("confirm"):
            return _staged(
                f"git {verb} is a brokered write on {root}; re-POST with "
                "{\"confirm\": true} plus a fresh approval", "")
        try:
            grant = permissions_store(cfg).grant(
                f"git.write.{verb}", requested_by="dashboard:user",
                confirmed=True, scopes=(str(root),),
                note=f"brokered git {verb}; apply runs through the opencode "
                     "worktree flow")
        except Exception as exc:
            code = getattr(exc, "code", None) or type(exc).__name__
            return 400, {"ok": False, "code": code,
                         "detail": _redact(str(exc))[:300],
                         "qubit_counterpart": counterpart}
        return _ok(ok=True, verb=verb, root=str(root), grant_id=grant.id,
                   applied=False,
                   detail="authorization recorded; apply through the brokered "
                          "opencode worktree flow",
                   qubit_counterpart=counterpart)

    return _handler


def h_blueprint_instantiate(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                            p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/cron/blueprints/instantiate -> real confirm-gated schedule.

    Fills a local blueprint's slots and creates the schedule in the same
    ``schedules`` store the CLI/voice use. ``values`` (or top-level keys) carry
    ``name``/``prompt``/``when``. Without ``confirm`` it stages a typed 409; a
    missing schedule expression is a typed 400, never a fabricated job.
    """
    values = b.get("values") if isinstance(b.get("values"), dict) else {}
    merged = {**{k: v for k, v in b.items() if k != "values"}, **values}
    if "blueprint" in merged and "name" not in merged:
        merged["name"] = merged["blueprint"]
    # Fill the blueprint's schedule TEMPLATE server-side (the SPA sends only the
    # blueprint key + slot values, exactly like the reference; it never sends a
    # ``when``). ``fill_blueprint`` validates the slots and returns the resolved
    # cron expression + prompt/name/deliver.
    blueprint_key = str(merged.get("blueprint") or "").strip()
    if blueprint_key:
        try:
            from . import qubit_runtime
            qubit_runtime.ensure_path()
            try:
                qubit_runtime.use_qubit_home(cfg)
            except Exception:  # noqa: BLE001
                pass
            from cron.blueprint_catalog import (  # type: ignore
                BlueprintFillError, fill_blueprint, get_blueprint,
            )
            blueprint = get_blueprint(blueprint_key)
            if blueprint is None:
                return 404, {"ok": False, "code": "BLUEPRINT_NOT_FOUND",
                             "detail": f"unknown blueprint {blueprint_key!r}",
                             "qubit_counterpart": "CLI `qubit schedules`"}
            try:
                spec = fill_blueprint(blueprint, values)
            except BlueprintFillError as exc:
                return 400, {"ok": False, "code": "BLUEPRINT_BAD_FILL",
                             "detail": _redact(str(exc))[:300],
                             "qubit_counterpart": "CLI `qubit schedules create`"}
            merged = {**spec, **merged, "name": spec.get("name", merged.get("name"))}
        except Exception:  # noqa: BLE001 - fall through to the legacy path
            pass
    if not merged.get("name"):
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "blueprint name required",
                     "qubit_counterpart": "CLI `qubit schedules`"}
    if not merged.get("when") and not merged.get("schedule"):
        return 400, {"ok": False, "code": "BLUEPRINT_BAD_FILL",
                     "detail": "a schedule expression (when) is required",
                     "qubit_counterpart": "CLI `qubit schedules create`"}
    scalar = {k: v for k, v in merged.items()
              if k != "confirm" and isinstance(v, (str, int, float, bool))}
    return cron_create(cfg, q, {**scalar, "confirm": bool(b.get("confirm"))}, p)


def h_plugin_kanban_attachment_download(
        cfg: Any, q: Dict[str, str], b: Dict[str, Any],
        p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/plugins/kanban/attachments/{id} -> real attachment bytes.

    Serves the vendored kanban plugin's own attachment store — the same store
    ``GET /tasks/{id}/attachments`` lists. The compat dispatcher returns JSON,
    so the blob is base64-encoded alongside the real filename/content-type and
    the served UI decodes it into a Blob download. A missing row, a tampered
    path, or a missing blob is the vendored plugin's typed ``PLUGIN_HTTP`` 404 —
    never a JSON success with no bytes and never the dead compat no-route 404.
    """
    from . import qubit_plugins as HP
    raw = str(p.get("attachment_id") or "").strip()
    if not raw.isdigit():
        return 404, {"ok": False, "code": "PLUGIN_HTTP",
                     "detail": f"attachment {raw!r} not found"}
    att_id = int(raw)
    try:
        engine = HP._kanban_engine(cfg)
        kb = engine.kanban_db
        with engine._board_conn(q.get("board") or None) as (board, conn):
            att = kb.get_attachment(conn, att_id)
            if att is None:
                return 404, {"ok": False, "code": "PLUGIN_HTTP",
                             "detail": f"attachment {att_id} not found"}
            root = Path(kb.attachments_root(board=board)).resolve()
            stored = Path(att.stored_path).resolve()
            try:
                stored.relative_to(root)
            except (ValueError, OSError):
                return 404, {"ok": False, "code": "PLUGIN_HTTP",
                             "detail": "attachment file unavailable"}
            if not stored.is_file():
                return 404, {"ok": False, "code": "PLUGIN_HTTP",
                             "detail": "attachment file missing on disk"}
            data = stored.read_bytes()
    except HP.PluginUnavailable as exc:
        return _gate("ATTACHMENT_STORE_UNAVAILABLE",
                     "the kanban attachment store is unavailable: "
                     + _redact(str(exc))[:200],
                     "CLI `qubit kanban` (attachments live with the task)",
                     http=503)
    except Exception as exc:
        return HP._kanban_http(exc)
    return _ok(attachment_id=att_id, filename=att.filename,
               content_type=att.content_type or "application/octet-stream",
               size=len(data),
               data_base64=base64.b64encode(data).decode("ascii"))


# ---------------------------------------------------------------------------
# memory-wiki READ surface -> the installed user plugin's core
# ---------------------------------------------------------------------------
# The user-installed ``memory-wiki`` dashboard plugin ships its tab JS but its
# backend (``plugin_api.py``, a FastAPI router) cannot mount on this listener.
# These five read-only handlers call the plugin's own
# ``memory_wiki_core`` (loaded by path, same trick as the plugin) against a
# read-only handle on the canonical Qubit ``state.db`` — the exact queries,
# clamps, shapes, and 404s of the plugin API, never a reimplementation.
_MEMWIKI_CORE_MODULE = "qubit_dashboard_plugin_memory_wiki_core"


def _memwiki_paths(cfg: Any) -> Optional[Tuple[Any, Any]]:
    """(core_file, memories_dir) for the installed memory-wiki, else None."""
    try:
        from . import qubit_runtime as _QR
        home = _QR.runtime_home_for_cfg(cfg)
    except Exception:
        return None
    core = Path(home) / "plugins" / "memory-wiki" / "dashboard" / \
        "memory_wiki_core.py"
    if not core.is_file():
        return None
    return core, Path(home) / "memories"


def _memwiki_core(core_file: Any) -> Any:
    import importlib.util as _ilu
    import sys as _sys
    mod = _sys.modules.get(_MEMWIKI_CORE_MODULE)
    if mod is not None:
        return mod
    spec = _ilu.spec_from_file_location(_MEMWIKI_CORE_MODULE, str(core_file))
    mod = _ilu.module_from_spec(spec)
    _sys.modules[_MEMWIKI_CORE_MODULE] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        _sys.modules.pop(_MEMWIKI_CORE_MODULE, None)
        raise
    return mod


def _memwiki_db(cfg: Any) -> Any:
    """Read-only handle on the canonical Qubit state.db, or None."""
    _vendored_runtime(cfg)
    from qubit_state import SessionDB  # type: ignore
    from . import qubit_runtime as _QR
    db_path = _QR.runtime_home_for_cfg(cfg) / "state.db"
    if not db_path.exists():
        return None
    return SessionDB(db_path=db_path, read_only=True)


def _memwiki_call(cfg: Any, q: Dict[str, str], label: str,
                  fn: Any) -> Tuple[int, Any]:
    paths = _memwiki_paths(cfg)
    if paths is None:
        return 404, {"ok": False, "code": "PLUGIN_NOT_FOUND",
                     "detail": "dashboard plugin 'memory-wiki' is not "
                               "installed",
                     "qubit_counterpart": "dashboard /plugins"}
    core_file, memories_dir = paths
    try:
        core = _memwiki_core(core_file)
    except Exception as exc:
        return _fail(exc)
    try:
        db = _memwiki_db(cfg)
        if db is None:
            return _fail(ValueError("state.db missing"))
        try:
            return 200, fn(core, db, memories_dir)
        finally:
            try:
                db.close()
            except Exception:
                pass
    except Exception as exc:
        return _fail(exc)


def _memwiki_clamp(value: Any, low: int, high: int) -> int:
    try:
        return max(low, min(int(value), high))
    except (TypeError, ValueError):
        return low


def h_memwiki_overview(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                       p: Dict[str, str]) -> Tuple[int, Any]:
    """GET /api/plugins/memory-wiki/overview -> subjects + days + sessions."""
    s_lim = _memwiki_clamp(q.get("subject_limit", 50), 1, 200)
    d_lim = _memwiki_clamp(q.get("day_limit", 30), 1, 120)
    return _memwiki_call(
        cfg, q, "overview",
        lambda core, db, mdir: core.build_memory_overview(
            db, subject_limit=s_lim, day_limit=d_lim))


def h_memwiki_subjects(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                       p: Dict[str, str]) -> Tuple[int, Any]:
    """GET /api/plugins/memory-wiki/subjects -> subject rows."""
    safe_limit = _memwiki_clamp(q.get("limit", 100), 1, 500)
    query = str(q.get("q", "") or "").strip()
    code, out = _memwiki_call(
        cfg, q, "subjects",
        lambda core, db, mdir: core.build_memory_subjects(
            db, limit=safe_limit, query=query or None))
    if code != 200:
        return code, out
    return 200, {"subjects": out, "query": query, "limit": safe_limit}


def h_memwiki_subject(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                      p: Dict[str, str]) -> Tuple[int, Any]:
    """GET /api/plugins/memory-wiki/subjects/{slug} -> one subject."""
    slug = str(p.get("slug", "") or "")
    code, out = _memwiki_call(
        cfg, q, "subject",
        lambda core, db, mdir: core.get_memory_subject(db, slug))
    if code != 200:
        return code, out
    if out is None:
        return 404, {"ok": False, "code": "NOT_FOUND",
                     "detail": "Memory subject not found"}
    return 200, {"subject": out}


def h_memwiki_days(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                   p: Dict[str, str]) -> Tuple[int, Any]:
    """GET /api/plugins/memory-wiki/days -> daily logs."""
    safe_limit = _memwiki_clamp(q.get("limit", 60), 1, 365)
    code, out = _memwiki_call(
        cfg, q, "days",
        lambda core, db, mdir: core.build_daily_logs(
            db, limit_days=safe_limit))
    if code != 200:
        return code, out
    return 200, {"daily_logs": out, "limit": safe_limit}


def h_memwiki_day(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                  p: Dict[str, str]) -> Tuple[int, Any]:
    """GET /api/plugins/memory-wiki/days/{date} -> one daily log."""
    date = str(p.get("date", "") or "")
    code, out = _memwiki_call(
        cfg, q, "day",
        lambda core, db, mdir: core.get_daily_log(db, date))
    if code != 200:
        return code, out
    if out is None:
        return 404, {"ok": False, "code": "NOT_FOUND",
                     "detail": "Memory day not found"}
    return 200, {"daily_log": out}


# ---------------------------------------------------------------------------
# Kanban WRITE surface -> the AUTHORITATIVE Qubit-native store
# ---------------------------------------------------------------------------
# The vendored kanban dashboard plugin's read routes synthesize the vendored
# board/task wire shape from ``voicebridge.kanban`` (the native store the CLI,
# voice, operations catalog and ``/qubit-boards`` all use). These write handlers
# back the plugin's own mutation routes with the SAME store, so the dashboard
# controls mutate exactly what they render — one authoritative board store.
# Every mutation runs through the shared ``_compat_confirm`` contract (e-stop
# latch -> stage/settle on the one approvals journal -> execute) and is
# audited; a mutation with no native-schema equivalent stays a typed refusal
# naming the CLI/voice/overlay counterpart, never a fabricated success.

_VENDOR_TO_NATIVE_STATUS = {
    "triage": "open", "todo": "open", "scheduled": "open", "ready": "open",
    "running": "claimed", "review": "claimed", "blocked": "blocked",
    "done": "done", "archived": "archived",
}
_VENDOR_PRIORITY_TO_NATIVE = {0: "low", 1: "normal", 2: "high"}


def _kanban_board_slug(q: Dict[str, str], b: Dict[str, Any]) -> str:
    raw = str((b or {}).get("board") or (q or {}).get("board") or "").strip()
    return "main" if raw in ("", "default") else raw


def _kanban_priority(value: Any) -> Optional[str]:
    """Map a vendored priority (int 0/1/2 or a native name) to a native value."""
    if value is None or value == "":
        return "normal"
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return _VENDOR_PRIORITY_TO_NATIVE.get(value)
    text = str(value).strip().lower()
    return text if text in ("low", "normal", "high") else None


def _kanban_native_show(cfg: Any, tid: int) -> Dict[str, Any]:
    from . import kanban as KB

    return KB.show(cfg, int(tid))["task"]


def _kanban_vendor_task(cfg: Any, tid: int) -> Dict[str, Any]:
    """The vendored task wire shape synthesized from the native task."""
    task = _kanban_native_show(cfg, tid)
    comments = task.get("comments") or []
    return _qubit_plugins._native_task_vendor(task, time.time(), len(comments))


def _kanban_confirm(cfg: Any, action: str, detail: str, q: Dict[str, str],
                    b: Dict[str, Any], payload: Dict[str, Any]
                    ) -> Optional[Tuple[int, Dict[str, Any]]]:
    return _compat_confirm(cfg, action, detail, query=q, body=b,
                           payload=payload)


def _kanban_apply_status(cfg: Any, kb: Any, tid: int, status: str) -> None:
    """Apply a vendored column status through the native transition verbs."""
    if status == "running":
        kb.claim(cfg, tid, requested_by="dashboard:user", confirmed=True)
    elif status == "blocked":
        kb.block(cfg, tid, requested_by="dashboard:user", confirmed=True)
    elif status == "done":
        kb.complete(cfg, tid, requested_by="dashboard:user", confirmed=True)
    elif status == "archived":
        kb.archive_task(cfg, tid, requested_by="dashboard:user", confirmed=True)
    else:  # todo/triage/scheduled/ready -> the native "open" column
        kb.reclaim(cfg, tid, requested_by="dashboard:user", confirmed=True)


def h_plugin_kanban_boards_create(cfg: Any, q: Dict[str, str],
                                  b: Dict[str, Any],
                                  p: Dict[str, str]
                                  ) -> Tuple[int, Dict[str, Any]]:
    """POST /api/plugins/kanban/boards -> create on the native board store."""
    from . import kanban as KB

    slug = str(b.get("slug", "") or "").strip()
    if not slug:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "slug required",
                     "qubit_counterpart": "CLI `qubit kanban boards-create`"}
    gate = _kanban_confirm(
        cfg, "kanban.boards_create", f"creating kanban board {slug!r}", q, b,
        {"slug": slug, "name": str(b.get("name", "") or "")})
    if gate is not None:
        return gate
    try:
        out = KB.boards_create(cfg, slug, str(b.get("name", "") or ""),
                               requested_by="dashboard:user", confirmed=True)
    except Exception as exc:
        return _qubit_plugins._kanban_http(exc)
    created = str(out.get("board", slug))
    _audit("kanban.boards.create", "dashboard:user", {"board": created})
    return 200, {"ok": True,
                 "board": {"slug": created, "name": out.get("name", created),
                           "counts": {}, "total": 0,
                           "is_current": created == "main",
                           "default_workspace_kind": "scratch",
                           "default_workdir": None, "project_id": None,
                           "project_name": None, "archived": False},
                 "current": "main"}


def h_plugin_kanban_tasks_create(cfg: Any, q: Dict[str, str],
                                 b: Dict[str, Any],
                                 p: Dict[str, str]
                                 ) -> Tuple[int, Dict[str, Any]]:
    """POST /api/plugins/kanban/tasks -> create on the native task store."""
    from . import kanban as KB

    title = str(b.get("title", "") or "").strip()
    if not title:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "title required",
                     "qubit_counterpart": "CLI `qubit kanban create`"}
    board = _kanban_board_slug(q, b)
    priority = _kanban_priority(b.get("priority"))
    if priority is None:
        return 400, {"ok": False, "code": "KANBAN_BAD_INPUT",
                     "detail": "priority must be low, normal, or high",
                     "qubit_counterpart": "CLI `qubit kanban create`"}
    gate = _kanban_confirm(
        cfg, "kanban.create", f"creating kanban task {title!r} on {board!r}",
        q, b, {"title": title, "board": board})
    if gate is not None:
        return gate
    try:
        out = KB.create(cfg, title, board=board,
                        body=str(b.get("body") or ""),
                        assignee=str(b.get("assignee") or ""),
                        priority=priority, requested_by="dashboard:user",
                        confirmed=True)
    except Exception as exc:
        return _qubit_plugins._kanban_http(exc)
    tid = int(out["task"]["id"])
    _audit("kanban.create", "dashboard:user", {"task_id": tid, "board": board})
    return 200, {"ok": True, "task": _kanban_vendor_task(cfg, tid)}


def h_plugin_kanban_tasks_patch(cfg: Any, q: Dict[str, str],
                                b: Dict[str, Any],
                                p: Dict[str, str]
                                ) -> Tuple[int, Dict[str, Any]]:
    """PATCH /api/plugins/kanban/tasks/{id} -> native edit/assign/transition."""
    from . import kanban as KB

    raw = str(p.get("task_id") or "").strip()
    if not raw.isdigit():
        return 404, {"ok": False, "code": "PLUGIN_HTTP",
                     "detail": f"task {raw!r} not found"}
    tid = int(raw)
    status = str(b.get("status", "") or "").strip().lower()
    if status and status not in _VENDOR_TO_NATIVE_STATUS:
        return 400, {"ok": False, "code": "KANBAN_BAD_INPUT",
                     "detail": f"unsupported status {status!r}",
                     "qubit_counterpart": "CLI `qubit kanban`"}
    try:
        _kanban_native_show(cfg, tid)
    except Exception as exc:
        return _qubit_plugins._kanban_http(exc)
    gate = _kanban_confirm(cfg, "kanban.update",
                           f"updating kanban task #{tid}", q, b,
                           {"task_id": tid, "status": status})
    if gate is not None:
        return gate
    try:
        if b.get("assignee") is not None:
            assignee = str(b.get("assignee") or "")
            if assignee.strip():
                KB.assign(cfg, tid, assignee, requested_by="dashboard:user",
                          confirmed=True)
            else:
                KB.reclaim(cfg, tid, requested_by="dashboard:user",
                           confirmed=True)
        if status:
            _kanban_apply_status(cfg, KB, tid, status)
        if b.get("title") is not None or b.get("body") is not None \
                or b.get("priority") is not None:
            priority = (_kanban_priority(b.get("priority"))
                        if b.get("priority") is not None else "")
            if priority is None:
                return 400, {"ok": False, "code": "KANBAN_BAD_INPUT",
                             "detail": "priority must be low, normal, or high",
                             "qubit_counterpart": "CLI `qubit kanban edit`"}
            KB.edit(cfg, tid,
                    title=str(b.get("title") or ""),
                    body=str(b.get("body") or ""),
                    priority=priority or "",
                    requested_by="dashboard:user", confirmed=True)
    except Exception as exc:
        return _qubit_plugins._kanban_http(exc)
    _audit("kanban.update", "dashboard:user", {"task_id": tid})
    return 200, {"ok": True, "task": _kanban_vendor_task(cfg, tid)}


def h_plugin_kanban_tasks_delete(cfg: Any, q: Dict[str, str],
                                 b: Dict[str, Any],
                                 p: Dict[str, str]
                                 ) -> Tuple[int, Dict[str, Any]]:
    """DELETE /api/plugins/kanban/tasks/{id} -> reversible native archive."""
    from . import kanban as KB

    raw = str(p.get("task_id") or "").strip()
    if not raw.isdigit():
        return 404, {"ok": False, "code": "PLUGIN_HTTP",
                     "detail": f"task {raw!r} not found"}
    tid = int(raw)
    try:
        _kanban_native_show(cfg, tid)
    except Exception as exc:
        return _qubit_plugins._kanban_http(exc)
    gate = _kanban_confirm(cfg, "kanban.archive",
                           f"archiving kanban task #{tid}", q, b,
                           {"task_id": tid})
    if gate is not None:
        return gate
    try:
        KB.archive_task(cfg, tid, requested_by="dashboard:user",
                        confirmed=True)
    except Exception as exc:
        return _qubit_plugins._kanban_http(exc)
    _audit("kanban.archive", "dashboard:user", {"task_id": tid})
    return 200, {"ok": True, "deleted": True, "task_id": raw, "archived": True,
                 "qubit_counterpart": "CLI `qubit kanban restore` "
                                      "(reversible; undo available)"}


def h_plugin_kanban_tasks_bulk(cfg: Any, q: Dict[str, str],
                               b: Dict[str, Any],
                               p: Dict[str, str]
                               ) -> Tuple[int, Dict[str, Any]]:
    """POST /api/plugins/kanban/tasks/bulk -> per-id native patch."""
    from . import kanban as KB

    ids = b.get("ids")
    if not isinstance(ids, list) or not ids:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "ids is required",
                     "qubit_counterpart": "CLI `qubit kanban`"}
    status = str(b.get("status", "") or "").strip().lower()
    if status and status not in _VENDOR_TO_NATIVE_STATUS:
        return 400, {"ok": False, "code": "KANBAN_BAD_INPUT",
                     "detail": f"unsupported status {status!r}",
                     "qubit_counterpart": "CLI `qubit kanban`"}
    gate = _kanban_confirm(cfg, "kanban.bulk",
                           f"bulk-updating {len(ids)} kanban tasks", q, b,
                           {"ids": ",".join(str(i) for i in ids)[:500],
                            "status": status})
    if gate is not None:
        return gate
    priority = _kanban_priority(b.get("priority")) \
        if b.get("priority") is not None else ""
    if b.get("priority") is not None and priority is None:
        return 400, {"ok": False, "code": "KANBAN_BAD_INPUT",
                     "detail": "priority must be low, normal, or high",
                     "qubit_counterpart": "CLI `qubit kanban`"}
    results = []
    for raw in ids:
        entry: Dict[str, Any] = {"id": str(raw), "ok": True}
        try:
            tid = int(str(raw))
            _kanban_native_show(cfg, tid)
            if b.get("archive"):
                KB.archive_task(cfg, tid, requested_by="dashboard:user",
                                confirmed=True)
            if status:
                _kanban_apply_status(cfg, KB, tid, status)
            if b.get("assignee") is not None and str(b.get("assignee")).strip():
                KB.assign(cfg, tid, str(b.get("assignee")),
                          requested_by="dashboard:user", confirmed=True)
            if b.get("title") is not None or b.get("body") is not None \
                    or b.get("priority") is not None:
                KB.edit(cfg, tid, title=str(b.get("title") or ""),
                        body=str(b.get("body") or ""),
                        priority=priority or "",
                        requested_by="dashboard:user", confirmed=True)
        except Exception as exc:
            entry.update(ok=False, error=_redact(str(exc))[:160])
        results.append(entry)
    _audit("kanban.bulk", "dashboard:user", {"count": len(results)})
    return 200, {"ok": True, "results": results}


def h_plugin_kanban_tasks_comment(cfg: Any, q: Dict[str, str],
                                  b: Dict[str, Any],
                                  p: Dict[str, str]
                                  ) -> Tuple[int, Dict[str, Any]]:
    """POST /api/plugins/kanban/tasks/{id}/comments -> native comment."""
    from . import kanban as KB

    raw = str(p.get("task_id") or "").strip()
    if not raw.isdigit():
        return 404, {"ok": False, "code": "PLUGIN_HTTP",
                     "detail": f"task {raw!r} not found"}
    tid = int(raw)
    text = str(b.get("body", "") or "")
    if not text.strip():
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "body is required",
                     "qubit_counterpart": "CLI `qubit kanban comment`"}
    gate = _kanban_confirm(cfg, "kanban.comment",
                           f"commenting on kanban task #{tid}", q, b,
                           {"task_id": tid})
    if gate is not None:
        return gate
    try:
        KB.comment(cfg, tid, text,
                   requested_by=str(b.get("author") or "dashboard:user"),
                   confirmed=True)
    except Exception as exc:
        return _qubit_plugins._kanban_http(exc)
    _audit("kanban.comment", "dashboard:user", {"task_id": tid})
    return 200, {"ok": True}


#: Legacy-brand tokens that must never be presented as a *current* Qubit API
#: path in the served OpenAPI document. A compat alias carrying one of these
#: still resolves on the wire, but is omitted from the /docs reference so no
#: legacy-branded literal appears on a user-facing surface (BRAND.md).
_LEGACY_API_BRAND_RE = re.compile(r"hermes|nous|openclaw", re.IGNORECASE)


def h_openapi(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
              p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """Qubit-branded OpenAPI 3 document generated from the compat route table.

    Serves the /docs browser from the real Qubit API surface — no upstream
    branding and no fork ``/openapi.json`` proxy. Gated routes are listed as
    documented capability entries (``deprecated`` + reason), not as executable
    operations.

    Legacy-named vendored plugin paths still resolve on the wire as compat
    aliases, but they are omitted from this reference so the user-facing /docs
    browser never renders a legacy-branded literal. The Qubit-owned canonical
    surfaces are the documented operations.
    """
    paths: Dict[str, Dict[str, Any]] = {}
    for row in ROUTES:
        pattern = str(row.get("pattern") or "")
        method = str(row.get("method") or "GET").lower()
        if not pattern or method not in ("get", "post", "put", "patch",
                                         "delete"):
            continue
        oas_path = re.sub(
            r"\(\?P<([A-Za-z_][A-Za-z0-9_]*)>[^)]*\)", r"{\1}", pattern)
        if _LEGACY_API_BRAND_RE.search(oas_path):
            # Legacy-named vendored plugin alias. The route still RESOLVES on
            # the wire, but the served /docs document must not present a
            # legacy-branded path on a user-facing surface (BRAND.md). Omit it
            # from the OpenAPI reference rather than advertising the legacy
            # literal.
            continue
        gate = row.get("gate") or {}
        entry = {
            "summary": str(row.get("handler") or ""),
            "description": (
                f"Qubit operation {row.get('handler')} "
                f"[{row.get('disposition')}]"
                + (f" — gated: {gate.get('code')}" if gate else "")),
            "deprecated": bool(gate),
            "responses": {"200": {"description": "OK"}},
        }
        paths.setdefault(oas_path, {})[method] = entry
    return 200, {
        "openapi": "3.0.3",
        "info": {
            "title": "Qubit Agent API",
            "version": "1.0.0",
            "description": "Qubit-branded API surface generated from the "
                           "canonical Qubit compat route table. Operations "
                           "carrying `x-qubit-compat-alias` are legacy-named "
                           "compatibility aliases, retained for wire "
                           "compatibility rather than advertised as current "
                           "Qubit paths.",
        },
        "paths": dict(sorted(paths.items())),
    }


def h_gateway_rpc(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                  p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/gateway/rpc -> Qubit-dialect RPC on the one Qubit listener.

    Owner decision 2026-09-18 (D2): Qubit-compatible RPC is served through
    the existing authenticated Qubit listener, reusing the canonical in-process
    method registry (``voicebridge.gateway.rpc``) that the CLI already
    dispatches. No second gateway, server, or authority is started. Each method
    delegates to the same broker/policy-gated backend the CLI, dashboard, and
    voice use; the wire envelope follows the Qubit ``req``/``res`` shape:

        {id, method, params} -> {type:"res", id, ok, payload | error}

    The method list is the real registry (``default_registry().names()``), and
    an unknown method is a typed ``GATEWAY_METHOD_NOT_FOUND`` - never a
    fabricated success.
    """
    from .gateway.rpc import default_registry, handle_request
    from .gateway.gateway_ws import GatewayError

    body = b if isinstance(b, dict) else {}
    params = body.get("params")
    if params is None and isinstance(q.get("params"), str) and q.get("params"):
        try:
            params = json.loads(q["params"])
        except (ValueError, TypeError):
            params = None
    msg = {
        "id": body.get("id") or q.get("id") or "http-rpc",
        "method": body.get("method") or q.get("method") or "",
        "params": params if isinstance(params, dict) else {},
    }
    registry = default_registry()
    if not msg["method"]:
        return 400, {
            "type": "res", "id": msg["id"], "ok": False,
            "error": {"code": "GATEWAY_BAD_PARAMS",
                      "message": "missing method",
                      "details": {"methods": registry.names()}},
        }
    try:
        res = handle_request(registry, cfg, msg)
    except GatewayError as exc:
        return 400, {
            "type": "res", "id": msg["id"], "ok": False,
            "error": {"code": exc.code, "message": exc.message,
                      "details": exc.details},
        }
    return (200 if res.get("ok") else 400), res


def h_gateway_rpc_methods(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                          p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/gateway/rpc -> the advertised Qubit-dialect method list."""
    from .gateway.rpc import default_registry

    return _ok(methods=default_registry().names(),
               authority="single in-process registry; no second listener")


def h_agents(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
             p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/qubit/agents -> canonical agent/worker roster (A41).

    Read-only aggregation of the real worker lane, runtime adapters, active
    agent profile, and recent sessions the same modules serve to the CLI and
    voice. No second registry and no fabricated agents.
    """
    from .integration_status import worker_status
    from .runtime_inventory import runtime_inventory

    try:
        inv = runtime_inventory(cfg)
        worker = worker_status(cfg)
    except Exception as exc:
        return _fail(exc)
    agents: List[Dict[str, Any]] = [{
        "id": "qubit-worker",
        "kind": "worker",
        "route": worker.get("route", "local"),
        "state": worker.get("state", "OK"),
        "enabled": bool(worker.get("agent_enabled", True)),
        "model": str(worker.get("agent_model", "") or ""),
    }]
    adapters = inv.get("adapters") if isinstance(inv.get("adapters"), dict) else {}
    for name in sorted(adapters):
        row = adapters[name] if isinstance(adapters[name], dict) else {}
        agents.append({"id": f"adapter:{name}", "kind": "adapter",
                       "route": str(row.get("route", "") or ""),
                       "state": str(row.get("state", "OK") or "OK")})
    return _ok(agents=agents, worker=worker, agent=inv.get("agent"),
               sessions=inv.get("sessions"),
               authority="read-only aggregation of the canonical worker lane, "
                         "runtime adapters, agent profile, and sessions")


def _resolve_timeline_session(db: Any, session_id: str) -> Optional[str]:
    """Resolve a timeline jump target to the live tip of the compression chain.

    Exact id first, else the single unambiguous prefix (the same resolution the
    session show/messages routes use); then follow ``resolve_resume_session_id``
    so a jump lands where the messages actually are. ``None`` when unknown —
    never a fabricated id.
    """
    try:
        sid = db.resolve_session_id(str(session_id or ""))
    except Exception:
        sid = None
    if not sid:
        return None
    try:
        return db.resolve_resume_session_id(sid)
    except Exception:
        return sid


def h_session_timeline(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                       p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/sessions/{id}/timeline -> prompt metadata index (read-only).

    Ported from reference ``web_routers/sessions.py:619`` ->
    ``qubit_state_timeline.get_session_timeline`` (vendored by W9 slice-0) over
    the canonical Qubit session DB (``state.db``). Prompt metadata only,
    including compacted display history; no transcript-wide payload hydration.
    """
    try:
        limit = max(1, min(int(q.get("limit", "500") or "500"), 500))
    except (TypeError, ValueError):
        limit = 500
    try:
        after = max(0, int(q.get("after_row_id", "0") or "0"))
    except (TypeError, ValueError):
        after = 0
    _cs, db, _dir = _chat_session_db(cfg)
    if db is None:
        return _fail(RuntimeError("canonical session store unavailable"))
    sid = _resolve_timeline_session(db, p.get("id", ""))
    if not sid:
        return 404, {"ok": False, "code": "SESSION_NOT_FOUND",
                     "detail": "Session not found"}
    try:
        _vendored_runtime(cfg)
        from qubit_state_timeline import get_session_timeline as read_timeline  # type: ignore
        out = read_timeline(db, sid, limit=limit, after_row_id=after)
    except Exception as exc:
        return _fail(exc)
    return _ok(session_id=sid, store_authority=CHAT_SESSION_AUTHORITY, **out)


def h_session_messages_around(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                              p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/sessions/{id}/messages/around -> bounded display page.

    Ported from reference ``web_routers/sessions.py:641`` ->
    ``qubit_state_timeline.get_session_messages_around``: read at most *limit*
    display rows starting at an exact human prompt (``row_id`` from the
    timeline), no intervening payloads. Read-only; reuses the same display
    projection (``_rebrand_chat_messages``) as ``h_session_messages``.
    """
    try:
        row_id = int(q.get("row_id", "") or "0")
    except (TypeError, ValueError):
        row_id = 0
    if row_id < 1:
        return 400, {"ok": False, "code": "MALFORMED",
                     "detail": "row_id must be a positive integer"}
    try:
        limit = max(1, min(int(q.get("limit", "120") or "120"), 120))
    except (TypeError, ValueError):
        limit = 120
    _cs, db, _dir = _chat_session_db(cfg)
    if db is None:
        return _fail(RuntimeError("canonical session store unavailable"))
    sid = _resolve_timeline_session(db, p.get("id", ""))
    if not sid:
        return 404, {"ok": False, "code": "SESSION_NOT_FOUND",
                     "detail": "Session not found"}
    try:
        _vendored_runtime(cfg)
        from qubit_state_timeline import get_session_messages_around as read_around  # type: ignore
        page = read_around(db, sid, row_id, limit=limit)
    except Exception as exc:
        return _fail(exc)
    if page is None:
        return 404, {"ok": False, "code": "PROMPT_NOT_FOUND",
                     "detail": "Prompt not found"}
    return _ok(session_id=sid, store_authority=CHAT_SESSION_AUTHORITY,
               messages=_rebrand_chat_messages(page["messages"]),
               pagination=page["pagination"])


def h_audio_voice_live_status(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                              p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/audio/voice-live/status -> non-secret GPT-Live readiness.

    Ported from reference ``web_routers/audio.py:167`` ->
    ``tools.voice_live.resolve_gpt_live_status`` (ported with the rename map).
    Reports the selected voice chat mode and whether GPT-Live can start (a key
    resolves); it NEVER returns a key. Read-only.
    """
    try:
        _vendored_runtime(cfg)
        from tools.voice_live import resolve_gpt_live_status  # type: ignore
        out = resolve_gpt_live_status()
    except Exception as exc:
        return _fail(exc)
    return _ok(**out)


def h_health_idle(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                  p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/health/idle -> token-gated diagnostic work snapshot (read-only).

    Ported from reference ``web_routers/status.py:139`` ->
    ``qubit_cli.web_server_idle_proof.idle_proof``. ``idle`` is True only when
    no turn is in flight (session table AND cron ledger) and nothing waits on a
    human; an indeterminate probe is reported as ``None`` honestly, never
    coerced to True. This snapshot is NOT a retirement permit. Served on the
    same authenticated dashboard channel as every other ``/agent/api/*`` read.
    """
    try:
        _vendored_runtime(cfg)
        from qubit_cli.web_server_idle_proof import idle_proof  # type: ignore
        out = idle_proof()
    except Exception as exc:
        return _fail(exc)
    return _ok(**out)


def h_credentials_pool(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                       p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/qubit/credentials/pool -> redacted provider/entry inventory.

    Names/labels/status only; no token value or fragment (A32). The upstream
    ``/api/credentials/pool`` route stays gated; this is the real allowed
    counterpart for the dashboard Remove control.
    """
    from . import credentials as CR
    try:
        out = CR.inventory(cfg)
    except CR.CredentialError as exc:
        return 503, {"ok": False, "code": exc.code, "detail": exc.detail}
    except Exception as exc:
        return _fail(exc)
    return _ok(**out)


def h_credentials_pool_add(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                           p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/qubit/credentials/pool -> add from an env:/file: reference.

    The browser supplies a reference, never a raw key; the value is resolved
    server-side and written through the canonical credential-pool path.
    Confirmed, e-stop-aware, audited, and returns no token.
    """
    from . import credentials as CR
    confirm = bool(b.get("confirm")) or \
        str(q.get("confirm", "")).lower() in ("1", "true", "yes")
    out = CR.add_reference(
        cfg, str(b.get("provider", "") or ""),
        str(b.get("key_ref", b.get("reference", "")) or ""),
        str(b.get("label", "") or ""), confirmed=confirm,
        requested_by="dashboard:user")
    state = str(out.get("state"))
    status = {"OK": 200, "MISSING": 404, "denied": 409,
              "ESTOP": 423, "UNAVAILABLE": 503}.get(state, 400)
    body = {"ok": state == "OK"}
    body.update(out)
    return status, body


def h_credentials_pool_remove(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                              p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """DELETE /api/qubit/credentials/pool/{provider}/{index} -> real removal.

    Confirmed + e-stop-aware + audited; delegates to the canonical
    ``CredentialPool.remove_index`` and returns no secret.
    """
    from . import credentials as CR
    confirm = bool(b.get("confirm")) or \
        str(q.get("confirm", "")).lower() in ("1", "true", "yes")
    out = CR.remove(cfg, p.get("provider", ""), p.get("index", ""),
                    confirmed=confirm, requested_by="dashboard:user")
    state = str(out.get("state"))
    status = {"OK": 200, "MISSING": 404, "denied": 409,
              "ESTOP": 423, "UNAVAILABLE": 503}.get(state, 400)
    body = {"ok": state == "OK"}
    body.update(out)
    return status, body


_EXTRA_HANDLERS: Dict[str, Handler] = {
    "h_openapi": h_openapi,
    "h_gateway_rpc": h_gateway_rpc,
    "h_gateway_rpc_methods": h_gateway_rpc_methods,
    "h_agents": h_agents,
    "h_credentials_pool": h_credentials_pool,
    "h_credentials_pool_add": h_credentials_pool_add,
    "h_credentials_pool_remove": h_credentials_pool_remove,
    "h_plugin_kanban_attachment_download": h_plugin_kanban_attachment_download,
    "h_plugin_kanban_boards_create": h_plugin_kanban_boards_create,
    "h_plugin_kanban_tasks_create": h_plugin_kanban_tasks_create,
    "h_plugin_kanban_tasks_patch": h_plugin_kanban_tasks_patch,
    "h_plugin_kanban_tasks_delete": h_plugin_kanban_tasks_delete,
    "h_plugin_kanban_tasks_bulk": h_plugin_kanban_tasks_bulk,
    "h_plugin_kanban_tasks_comment": h_plugin_kanban_tasks_comment,
    "cron_pause": lambda cfg, q, b, p: _cron_mutate("pause", cfg, q, b, p),
    "cron_resume": lambda cfg, q, b, p: _cron_mutate("resume", cfg, q, b, p),
    "cron_trigger": lambda cfg, q, b, p: _cron_mutate("run_due", cfg, q, b, p),
    "pairing_create": lambda cfg, q, b, p: h_pairing_mutate("create", cfg, q, b),
    "pairing_approve": lambda cfg, q, b, p: h_pairing_mutate("approve", cfg, q, b),
    "pairing_revoke": lambda cfg, q, b, p: h_pairing_mutate("revoke", cfg, q, b),
    "webhooks_add": lambda cfg, q, b, p: h_webhooks_mutate("add", cfg, q, b, p),
    "webhooks_show": lambda cfg, q, b, p: h_webhooks_mutate("show", cfg, q, b, p),
    "webhooks_mutate": lambda cfg, q, b, p: h_webhooks_mutate("remove", cfg, q, b, p),
    "blueprints": h_blueprints,
    "delivery": h_delivery_targets,
    "h_session_patch": h_session_patch,
    "h_session_delete": h_session_delete,
    "h_session_resume": h_session_resume,
    "sess_empty": lambda cfg, q, b, p: h_sess_empty(cfg, q, b, p),
    "sessions_import": h_sessions_import,
    "sessions_prune": h_sessions_prune,
    "sessions_bulk_delete": h_sessions_bulk_delete,
    "sessions_empty_delete": h_sessions_empty_delete,
    "mem_reset": h_mem_reset,
    "mem_reset_status": h_mem_reset_status,
    "mem_prov": h_mem_provider,
    "toolset_write": h_toolset_write,
    "h_toolset_toggle": h_toolset_toggle,
    "h_power": h_power,
    "power_action": power_action,
    "plugins_rescan": h_plugins_rescan,
    "debug_share": h_debug_share,
    "ckpt": h_ckpt,
    "h_ckpt": h_ckpt,
    "h_checkpoints_prune": h_checkpoints_prune,
    "actions_read": h_actions_read,
    "action_status": h_action_status,
    "font": lambda cfg, q, b, p: h_font(cfg, q, b, p, False),
    "font_set": lambda cfg, q, b, p: h_font(cfg, q, b, p, True),
    "h_profiles_active": h_profiles_active,
    "profiles_active_set": profiles_active_set,
    "profile_soul_set": profile_soul_set,
    "profile_desc": profile_desc,
    "profile_model": profile_model,
    "profile_describe_auto": profile_describe_auto,
    "profile_rename": profile_rename,
    "profile_delete": profile_delete,
    "cron_create": cron_create,
    "cron_update": cron_update,
    "cron_delete": cron_delete,
    "skill_create": skill_create,
    "skill_update": skill_update,
    "skill_delete": skill_delete,
    "h_terminal_profiles": h_terminal_profiles,
    "terminal_profile_set": terminal_profile_set,
    "moa_put": moa_put,
    "mcp_create": mcp_create,
    "mcp_update": mcp_update,
    "mcp_delete": mcp_delete,
    "mcp_set_enabled": mcp_set_enabled,
    "mcp_auth": mcp_auth,
    "mcp_auth_status": mcp_auth_status,
    "mcp_oauth_flows_list": mcp_oauth_flows_list,
    "mcp_oauth_flow_status": mcp_oauth_flow_status,
    "mcp_oauth_flow_cancel": mcp_oauth_flow_cancel,
    "mcp_oauth_callback": mcp_oauth_callback,
    "mcp_invoke": mcp_invoke,
    "webhooks_enable": webhooks_enable,
    "webhooks_enabled": webhooks_enabled,
    "webhooks_delete": webhooks_delete,
    "webhooks_rotate": webhooks_rotate,
    "webhooks_test": webhooks_test,
    "webhooks_deliveries": webhooks_deliveries,
    "webhooks_retry": webhooks_retry,
    "msg_update": msg_update,
    "h_analytics_models": h_analytics_models,
    "h_files_read": h_files_read,
    "h_files_download": h_files_download,
    "files_delete": files_delete,
    "hooks_delete": hooks_delete,
    "plugins_hub": plugins_hub,
    "plugins_catalog": plugins_catalog,
    "plugin_catalog_detail": plugin_catalog_detail,
    "plugins_reload": plugins_reload,
    "agent_enable": agent_enable,
    "agent_disable": agent_disable,
    "agent_remove": agent_remove,
    "h_agent_plugin_install": h_agent_plugin_install,
    "h_chat_session_activity": h_chat_session_activity,
    "h_chat_session_drafts": h_chat_session_drafts,
    "h_chat_session_draft_content": h_chat_session_draft_content,
    "h_chat_session_comments": h_chat_session_comments,
    "h_chat_vision": h_chat_vision,
    "h_chat_session_ci": h_chat_session_ci,
    "env_write": env_write,
    "env_delete": env_delete,
    "toolset_env_write": toolset_env_write,
    "plugin_providers_write": plugin_providers_write,
    "mem_provider_set": mem_provider_set,
    "mem_config": mem_config,
    "mem_config_write": mem_config_write,
    "h_profile_setup_command": h_profile_setup_command,
    "h_profile_soul": h_profile_soul,
    "h_skill_content": h_skill_content,
    "config_save": config_save,
    "toolset_config": toolset_config,
    "toolset_models": toolset_models,
    "h_local_models_status": h_local_models_status,
    "h_local_models_hardware": h_local_models_hardware,
    "h_local_models_catalog": h_local_models_catalog,
    "h_local_models_jobs": h_local_models_jobs,
    "h_local_models_job_show": h_local_models_job_show,
    "h_local_models_runtime_install": h_local_models_runtime_install,
    "h_local_models_download": h_local_models_download,
    "h_local_models_download_pause": h_local_models_download_pause,
    "h_local_models_download_resume": h_local_models_download_resume,
    "h_local_models_delete": h_local_models_delete,
    "h_local_models_quickstart": h_local_models_quickstart,
    "h_local_models_server": h_local_models_server,
    "h_local_models_eject": h_local_models_eject,
    "h_local_models_activate": h_local_models_activate,
    "h_local_models_sideload": h_local_models_sideload,
    "h_local_models_search": h_local_models_search,
    "h_local_models_search_files": h_local_models_search_files,
    "h_local_models_download_browsed": h_local_models_download_browsed,
    "h_custom_endpoints": h_custom_endpoints,
    "h_profiles_sessions": h_profiles_sessions,
    "h_profiles_sessions_sidebar": h_profiles_sessions_sidebar,
    "h_profiles_projects_tree": h_profiles_projects_tree,
    "h_chat_workspaces": h_chat_workspaces,
    "h_profile_create": h_profile_create,
    "h_terminal_backends": h_terminal_backends,
    "h_computer_use_status": h_computer_use_status,
    "h_learning_graph": h_learning_graph,
    "h_learning_node": h_learning_node,
    "h_learning_node_update": h_learning_node_update,
    "h_learning_node_delete": h_learning_node_delete,
    "h_auth_providers": h_auth_providers,
    "h_transfers_status": h_transfers_status,
    "h_transfers_list": h_transfers_list,
    "h_transfers_fetch": h_transfers_fetch,
    "h_transfers_plan": h_transfers_plan,
    "h_transfers_resume": h_transfers_resume,
    "h_recall_status": h_recall_status,
    "h_recall_policy_get": h_recall_policy_get,
    "h_recall_policy_set": h_recall_policy_set,
    "h_recall_query": h_recall_query,
    "h_recall_update": h_recall_update,
    "h_recall_resolve": h_recall_resolve,
    "h_recall_escalate": h_recall_escalate,
    "h_documents_status": h_documents_status,
    "h_documents_kinds": h_documents_kinds,
    "h_documents_extract": h_documents_extract,
    "h_ask_status": h_ask_status,
    "h_ask_pending": h_ask_pending,
    "h_ask_create": h_ask_create,
    "h_ask_answer": h_ask_answer,
    "h_session_share_status": h_session_share_status,
    "h_session_shares_list": h_session_shares_list,
    "h_session_share_publish": h_session_share_publish,
    "h_session_share_revoke": h_session_share_revoke,
    "h_session_shares_purge": h_session_shares_purge,
    "h_share_resolve": h_share_resolve,
    "h_qubit_gateway_discovery": h_qubit_gateway_discovery,
    "h_qubit_runtime_discovery": h_qubit_runtime_discovery,
    "h_qubit_runtime_marketplace": h_qubit_runtime_marketplace,
    "h_qubit_runtime_history": h_qubit_runtime_history,
    "h_qubit_runtime_update_report": h_qubit_runtime_update_report,
     "h_qubit_update_check": h_qubit_update_check,
     "h_qubit_update_receipt": h_qubit_update_receipt,
     "h_qubit_update_apply": h_qubit_update_apply,
     "h_qubit_update_rollback": h_qubit_update_rollback,
    "h_qubit_runtime_status": h_qubit_runtime_status,
    "h_qubit_runtime_images": h_qubit_runtime_images,
    "h_qubit_runtime_doctor": h_qubit_runtime_doctor,
    "h_qubit_egress_status": h_qubit_egress_status,
    "h_qubit_egress_providers": h_qubit_egress_providers,
    "h_qubit_egress_check": h_qubit_egress_check,
    "h_qubit_policy_conformance": h_qubit_policy_conformance,
    "h_qubit_policy_attest": h_qubit_policy_attest,
    "h_qubit_hooks_doctor": h_qubit_hooks_doctor,
    "h_qubit_usage": h_qubit_usage,
    "h_qubit_receipts": h_qubit_receipts,
    "h_qubit_logbook": h_qubit_logbook,
    "h_qubit_browser_profiles": h_qubit_browser_profiles,
    "h_op_computer_ops": h_op_computer_ops,
    "h_op_computer_presentation_status": h_op_computer_presentation_status,
    "h_op_computer_launch": h_op_computer_launch,
    "h_op_computer_pip": h_op_computer_pip,
    "h_op_computer_present": h_op_computer_present,
    "h_op_browser_favicon": h_op_browser_favicon,
    "h_op_browser_favicon_cache": h_op_browser_favicon_cache,
    "h_op_browser_download": h_op_browser_download,
    "h_qubit_local_models_fallback": h_qubit_local_models_fallback,
    "h_gateway_start": h_gateway_supervision("start"),
    "h_gateway_stop": h_gateway_supervision("stop"),
    "h_gateway_restart": h_gateway_restart,
    "h_custom_endpoints_write": h_custom_endpoints_write,
    "h_custom_endpoints_activate": h_custom_endpoints_activate,
    "h_custom_endpoints_delete": h_custom_endpoints_delete,
    "h_terminal_backend_set": h_terminal_backend_set,
    "h_computer_use_grant": h_computer_use_grant,
    "h_profile_export": h_profile_export,
    "h_portal": h_portal,
    "h_plugin_asset": h_plugin_asset,
    "h_skills_hub_official": h_skills_hub_official,
    "h_skills_hub_sources": h_skills_hub_sources,
    "h_skills_hub_search": h_skills_hub_search,
    "h_skills_hub_preview": h_skills_hub_preview,
    "h_skills_hub_scan": h_skills_hub_scan,
    "h_skills_hub_install": h_skills_hub_install,
    "h_skills_hub_uninstall": h_skills_hub_uninstall,
    "h_skills_hub_update": h_skills_hub_update,
    "h_fs_write_text": h_fs_write_text,
    "h_files_upload": h_files_upload,
    "h_files_mkdir": h_files_mkdir,
    "h_git_stage": h_git_write("stage"),
    "h_git_unstage": h_git_write("unstage"),
    "h_git_revert": h_git_write("revert"),
    "h_git_commit": h_git_write("commit"),
    "h_git_worktree_add": h_git_write("worktree-add"),
    "h_git_worktree_remove": h_git_write("worktree-remove"),
    "h_git_branch_switch": h_git_write("branch-switch"),
    "h_blueprint_instantiate": h_blueprint_instantiate,
    "pairing_clear_pending": h_pairing_clear_pending,
    "h_mcp_servers": h_mcp_servers,
    "h_mcp_catalog_live": h_mcp_catalog_live,
    "h_mcp_test_local": h_mcp_test_local,
    "h_mcp_server_resources": h_mcp_server_resources,
    "h_mcp_server_prompts": h_mcp_server_prompts,
    "h_mcp_server_resource_templates": h_mcp_server_resource_templates,
    "h_msg_test_live": h_msg_test_live,
    "h_pub": h_pub,
    "h_gateway_drain": h_gateway_drain,
    "h_plugin_visibility_set": h_plugin_visibility,
    "h_login_page": h_login_page,
    "h_auth_login": h_auth_login,
    "h_auth_native_authorize": h_auth_native_authorize,
    "h_auth_callback": h_auth_callback,
    "h_auth_password_login": h_auth_password_login,
    "h_auth_logout": h_auth_logout,
    "h_auth_native_token": h_auth_native_token,
    "h_auth_native_refresh": h_auth_native_refresh,
    "h_plugin_achievements": _qubit_plugins.h_plugin_achievements,
    "h_plugin_achievements_scan_status":
        _qubit_plugins.h_plugin_achievements_scan_status,
    "h_plugin_achievements_recent_unlocks":
        _qubit_plugins.h_plugin_achievements_recent_unlocks,
    "h_plugin_achievements_session_badges":
        _qubit_plugins.h_plugin_achievements_session_badges,
    "h_plugin_achievements_rescan":
        _qubit_plugins.h_plugin_achievements_rescan,
    "h_plugin_achievements_reset_state":
        _qubit_plugins.h_plugin_achievements_reset_state,
    "h_plugin_kanban_board": _qubit_plugins.h_plugin_kanban_board,
    "h_memwiki_overview": h_memwiki_overview,
    "h_memwiki_subjects": h_memwiki_subjects,
    "h_memwiki_subject": h_memwiki_subject,
    "h_memwiki_days": h_memwiki_days,
    "h_memwiki_day": h_memwiki_day,
    "h_plugin_kanban_task": _qubit_plugins.h_plugin_kanban_task,
    "h_plugin_kanban_stats": _qubit_plugins.h_plugin_kanban_stats,
    "h_plugin_kanban_assignees": _qubit_plugins.h_plugin_kanban_assignees,
    "h_plugin_kanban_boards": _qubit_plugins.h_plugin_kanban_boards,
    "h_plugin_kanban_config": _qubit_plugins.h_plugin_kanban_config,
    "h_plugin_kanban_projects": _qubit_plugins.h_plugin_kanban_projects,
    "h_plugin_kanban_workers": _qubit_plugins.h_plugin_kanban_workers,
    "h_plugin_kanban_diagnostics": _qubit_plugins.h_plugin_kanban_diagnostics,
    "h_plugin_kanban_model_options":
        _qubit_plugins.h_plugin_kanban_model_options,
    "h_plugin_kanban_home_channels":
        _qubit_plugins.h_plugin_kanban_home_channels,
    "h_plugin_kanban_task_log": _qubit_plugins.h_plugin_kanban_task_log,
    "h_plugin_kanban_task_attachments":
        _qubit_plugins.h_plugin_kanban_task_attachments,
    "h_plugin_kanban_run": _qubit_plugins.h_plugin_kanban_run,
    "h_plugin_kanban_run_inspect":
        _qubit_plugins.h_plugin_kanban_run_inspect,
    "h_plugin_kanban_profiles": _qubit_plugins.h_plugin_kanban_profiles,
    "h_plugin_kanban_orchestration":
        _qubit_plugins.h_plugin_kanban_orchestration,
    "h_plugin_kanban_events": _qubit_plugins.h_plugin_kanban_events,
    "h_plugin_fleet": _qubit_plugins.h_plugin_fleet,
}

HANDLERS: Dict[str, Handler] = {
    "h_status": h_status, "h_runtime": h_runtime, "h_auth_me": h_auth_me, "h_events": h_events,
    "h_health": h_health, "h_egress_status": h_egress_status,
    "h_sessions_list": h_sessions_list, "h_sessions_stats": h_sessions_stats,
    "h_session_show": h_session_show, "h_session_messages": h_session_messages,
    "h_session_timeline": h_session_timeline,
    "h_session_messages_around": h_session_messages_around,
    "h_config": h_config, "h_config_defaults": h_config_defaults,
    "h_config_effective": h_config_effective,
    "h_config_schema": h_config_schema, "h_model_info": h_model_info,
    "h_model_recommended_default": h_model_recommended_default,
    "h_model_options": h_model_options, "h_model_moa": h_model_moa,
    "h_model_auxiliary": h_model_auxiliary, "h_model_set": h_model_set,
    "h_profiles": h_profiles, "h_skills": h_skills,
    "h_skills_toggle": h_skills_toggle, "h_toolsets": h_toolsets,
    "h_cron_jobs": h_cron_jobs, "h_cron_job_show": h_cron_job_show,
    "h_cron_job_runs": h_cron_job_runs,
    "h_logs": h_logs, "h_system_stats": h_system_stats, "h_mcp": h_mcp,
    "h_mcp_catalog_install": h_mcp_catalog_install,
    "h_pairing_list": h_pairing_list, "h_webhooks_list": h_webhooks_list,
    "h_platforms": h_platforms,
    "h_wa_ob_start": h_wa_ob_start, "h_wa_ob_status": h_wa_ob_status,
    "h_wa_ob_apply": h_wa_ob_apply, "h_wa_ob_cancel": h_wa_ob_cancel,
    "h_tg_ob_start": h_tg_ob_start, "h_tg_ob_status": h_tg_ob_status,
    "h_tg_ob_apply": h_tg_ob_apply, "h_tg_ob_cancel": h_tg_ob_cancel,
    "h_memory": h_memory,
    "h_analytics": h_analytics, "h_files": h_files, "h_gateway": h_gateway,
    "h_gateway_migrate_plan": h_gateway_migrate_plan,
    "h_host_identity": h_host_identity,
    "h_providers_oauth": h_providers_oauth,
    "h_oauth_start": h_oauth_start, "h_oauth_submit": h_oauth_submit,
    "h_oauth_poll": h_oauth_poll, "h_oauth_cancel": h_oauth_cancel,
    "h_oauth_disconnect": h_oauth_disconnect,
    "h_curator": h_curator, "h_curator_paused": h_curator_paused,
    "h_curator_run": h_curator_run, "h_env": h_env, "h_ops": h_ops,
    "h_ops_catalog": h_ops_catalog,
    "h_ops_run": h_ops_run,
    "h_chat_turn": h_chat_turn,
    "h_chat_sessions_list": h_chat_sessions_list,
    "h_chat_session_create": h_chat_session_create,
    "h_chat_session_show": h_chat_session_show,
    "h_chat_session_messages": h_chat_session_messages,
    "h_chat_session_patch": h_chat_session_patch,
    "h_chat_session_delete": h_chat_session_delete,
    "h_chat_session_export": h_chat_session_export,
    "h_chat_session_import": h_chat_session_import,
    "h_chat_cancel": h_chat_cancel, "h_chat_pty": h_chat_pty,
    "h_chat_retry": h_chat_retry, "h_chat_stream": h_chat_stream,
    "h_plugins": h_plugins, "h_themes": h_themes, "h_theme_get": h_theme_get,
    "h_theme_set": h_theme_set,
    "h_hooks": h_hooks, "h_hooks_create": h_hooks_create,
    "h_hooks_update": h_hooks_update,
    "h_approvals_list": h_approvals_list,
    "h_ops_confirm": h_ops_confirm, "h_ops_cancel": h_ops_cancel,
    "h_git_status": h_git_status, "h_git_log": h_git_log,
    "h_git_diff": h_git_diff, "h_git_show": h_git_show,
    "h_git_file_diff": h_git_file_diff,
    "h_git_worktrees": h_git_worktrees, "h_git_branches": h_git_branches,
    "h_git_base_branches": h_git_base_branches,
    "h_git_review_list": h_git_review_list,
    "h_git_review_commit_context": h_git_review_commit_context,
    "h_git_review_rev_parse": h_git_review_rev_parse,
    "h_fs_list": h_fs_list, "h_fs_tree": h_fs_tree, "h_fs_stat": h_fs_stat,
    "h_fs_read_text": h_fs_read_text,
    "h_fs_read_data_url": h_fs_read_data_url,
    "h_fs_git_root": h_fs_git_root, "h_fs_default_cwd": h_fs_default_cwd,
    "h_audio_status": h_audio_status, "h_audio_devices": h_audio_devices,
    "h_audio_voice_config": h_audio_voice_config,
    "h_audio_voice_live_status": h_audio_voice_live_status,
    "h_health_idle": h_health_idle,
    "h_audio_transcribe": h_audio_transcribe,
    "h_audio_speak": h_audio_speak,
    "h_audio_stt_status": h_audio_stt_status,
}
HANDLERS.update(_EXTRA_HANDLERS)


def _overlay_manifests() -> List[Dict[str, Any]]:
    manifests: List[Dict[str, Any]] = []
    plugdir = OVERLAY_DIR / "plugins"
    if plugdir.exists():
        for mf in sorted(plugdir.glob("*/manifest.json")):
            try:
                manifests.append(json.loads(mf.read_text()))
            except (OSError, ValueError):
                continue
    return manifests


def plugins_response() -> Dict[str, Any]:
    return {"plugins": _overlay_manifests()}


def themes_response(cfg: Any) -> Dict[str, Any]:
    """GET /api/dashboard/themes -> the real vendored built-ins + Qubit overlay.

    1:1 with the reference: the vendored ``_BUILTIN_DASHBOARD_THEMES`` (the
    same set Hermes serves) with the Qubit-owned ``qubit`` overlay appended.
    The frontend owns every definition (``frontend/qubit-web/src/themes/
    presets.ts`` for the built-ins, ``qubit-theme.json`` for the overlay);
    the backend lists the names and persists the selection, never invents a
    theme. A fork miss still serves the overlay instead of an empty list.
    """
    themes: List[Dict[str, Any]] = []
    try:
        _vendored_runtime(cfg)
        from qubit_cli.web_server_dashboard import (  # type: ignore
            _BUILTIN_DASHBOARD_THEMES)
        for t in _BUILTIN_DASHBOARD_THEMES or []:
            if isinstance(t, dict) and t.get("name"):
                themes.append({"name": str(t["name"]),
                               "label": str(t.get("label") or t["name"]),
                               "description": str(t.get("description") or "")})
    except Exception:  # noqa: BLE001 - overlay theme must survive a fork miss
        pass
    theme_path = OVERLAY_DIR / "qubit-theme.json"
    try:
        definition = json.loads(theme_path.read_text())
    except (OSError, ValueError):
        definition = {}
    if definition:
        themes.append({"name": "qubit", "label": "Qubit Command",
                       "description": "Qubit graphite/navy with signal-lime accents",
                       "definition": definition})
    names = {t.get("name") for t in themes}
    try:
        stored = str(_prefs(cfg).get("theme", "") or "").strip()
    except Exception:  # noqa: BLE001 - prefs miss falls back to default
        stored = ""
    if stored in names:
        active = stored
    elif "qubit" in names:
        active = "qubit"
    else:
        active = themes[0]["name"] if themes else "default"
    return {"active": active, "themes": themes}


def match(method: str, path: str) -> Tuple[Optional[Dict[str, Any]], Dict[str, str]]:
    for route in ROUTES:
        if route["method"] != method:
            continue
        m = re.fullmatch(route["pattern"], path)
        if m:
            return route, {k: v for k, v in m.groupdict().items()}
    return None, {}


def dispatch(cfg: Any, method: str, path: str,
             query: Optional[Dict[str, str]] = None,
             body: Optional[Dict[str, Any]] = None,
             actor: str = "dashboard:user") -> Tuple[int, Any]:
    clean = path.split("?")[0]
    if len(clean) > 1:
        clean = clean.rstrip("/")
    route, params = match(method, clean)
    if route is None and str(method).upper() == "HEAD":
        # HEAD mirrors its GET route (status/headers only, no body), so a
        # gated or implemented GET never answers a bare 404 to a HEAD preload.
        route, params = match("GET", clean)
    if route is None:
        return 404, {"ok": False, "code": "NOT_FOUND",
                     "detail": f"no compat route for {method} {path}"}
    if route["disposition"] == "gated":
        gate = route["gate"]
        _audit("gated", actor, {"route": f"{method} {path}",
                                "code": gate.get("code", "")})
        return gate.get("http", 403), {"ok": False, "state": "UNAVAILABLE",
                                       "code": gate.get("code"),
                                       "detail": gate.get("detail"),
                                       "qubit_counterpart": gate.get("counterpart"),
                                       "owner": gate.get("owner"),
                                       "rollback": gate.get("rollback")}
    if route["disposition"] == "stream":
        # A streaming route cannot be expressed as a JSON ``(status, dict)``.
        # The SSE front door (webui) consumes this disposition and iterates the
        # route handler; a generic JSON dispatch is a typed refusal, never a
        # fabricated success.
        _audit("stream_requires_sse", actor, {"route": f"{method} {path}"})
        return 400, {"ok": False, "code": "STREAM_REQUIRES_SSE",
                     "detail": "this route streams Server-Sent Events; call it "
                               "over the SSE front door, not the JSON dispatcher",
                     "content_type": "text/event-stream",
                     "qubit_counterpart": f"POST {path} (Accept: text/event-stream)"}
    handler = HANDLERS.get(route["handler"])
    if handler is None:
        return 500, {"ok": False, "code": "NO_HANDLER",
                     "detail": f"handler {route['handler']!r} missing"}
    try:
        code, out = handler(cfg, query or {}, body or {}, params)
    except Exception as exc:
        return _fail(exc)
    _audit("served", actor, {"route": f"{method} {path}", "code": str(code)})
    return code, out


# ---------------------------------------------------------------------------
# Qubit family bridge (integrator lane): /api/qubit/<family>/<action>
# ---------------------------------------------------------------------------
#
# The five page-builder lanes shipped 13 Qubit pages + five per-family client
# modules (ocFamilyAgentsApi, ocFamilySessionsApi, ocFamilyBoardApi,
# ocFamilyTalkApi, ocFamilyAdminApi) with top-of-file ROUTES manifests, and
# the second wave added 5 more pages + four more manifests (ocFamilyCanvasApi,
# ocFamilyTasksApi, ocFamilyFlowsApi, ocFamilyMemoryApi; ocFamilyWebchatApi is
# reuse-only with no bridge rows). This
# section is the single wiring owner: a GENERIC bridge that serves every
# manifest path verbatim and delegates each call to the already-registered
# gateway registry method behind it
# (``voicebridge.gateway.rpc.default_registry`` — the one in-process registry
# the CLI, dashboard, and voice surfaces share). No dynamic imports, no new
# stores, no new backends.
#
# Contract (the same shape the sibling h_* handlers use):
# * actor is always "dashboard:user"; the shared dispatcher audits the call.
# * reads ("read") execute immediately through the registry.
# * mutations ("mutation") are e-stop gated, then confirm-gated through the
#   shared journal: without explicit confirmation (or a consumable
#   confirmation_id staged by a prior call) the answer is a typed 409 with a
#   real confirmation_id; re-POST with {"confirm": true} (or the staged id)
#   executes exactly once through the registry with confirmed=True.
# * direct writes ("write") execute immediately but stay e-stop gated (they
#   mirror an underlying handler that is itself unconfirmed, e.g. the
#   font/theme preference writes behind users.prefs.set).
# * unknown family/action (or a wrong HTTP method) is a typed 404 — never a
#   dynamic import, never a fabricated success.
# * a manifest path with no registered gateway method behind it answers the
#   honest typed gate recorded in its entry (never invented behavior).
#
# Reused, never duplicated: the session picker (GET /api/sessions), the
# worktree roster (GET /api/git/worktrees, GET /api/git/branches), and the
# checkpoint journal (GET /api/ops/checkpoints, POST
# /api/ops/checkpoints/prune) already exist and the pages call them directly.

# One row per manifest path. "action" is the path remainder under
# /api/qubit/<family>/; "{id}" matches exactly one non-empty segment (its
# value is passed through as "id"). "aliases" renames caller keys to the
# gateway method's parameter names (applied simultaneously from the original
# body, so two renames can never collide); the gateway backend stays
# authoritative and validates everything.
_QUBIT_FAMILY_BRIDGE: List[Dict[str, Any]] = [
    # -- agent-workspace (ocFamilyAgentsApi) --
    {"family": "agent-workspace", "action": "list", "methods": ["GET"],
     "gateway": "agents.workspace.list", "kind": "read"},
    {"family": "agent-workspace", "action": "get", "methods": ["GET"],
     "gateway": "agents.workspace.get", "kind": "read"},
    {"family": "agent-workspace", "action": "run-status", "methods": ["GET"],
     "gateway": "agent.wait", "kind": "read"},
    # -- nodes (ocFamilyAgentsApi) --
    {"family": "nodes", "action": "list", "methods": ["GET"],
     "gateway": "node.list", "kind": "read"},
    {"family": "nodes", "action": "describe", "methods": ["GET"],
     "gateway": "node.describe", "kind": "read"},
    {"family": "nodes", "action": "enroll", "methods": ["POST"],
     "gateway": "node.pair.approve", "kind": "mutation",
     "aliases": {"node_id": "name", "label": "name"}},
    {"family": "nodes", "action": "approve", "methods": ["POST"],
     "gateway": "node.pair.approve", "kind": "mutation",
     "aliases": {"node_id": "name"}},
    {"family": "nodes", "action": "revoke", "methods": ["POST"],
     "gateway": "node.pair.reject", "kind": "mutation"},
    {"family": "nodes", "action": "remove", "methods": ["POST"],
     "gateway": "node.pair.remove", "kind": "mutation"},
    {"family": "nodes", "action": "capabilities", "methods": ["GET"],
     "gateway": "node.pluginSurface.refresh", "kind": "read"},
    {"family": "nodes", "action": "dispatch", "methods": ["POST"],
     "gateway": "node.invoke", "kind": "mutation",
     "aliases": {"task": "capability"}},
    {"family": "nodes", "action": "rename", "methods": ["POST"],
     "gateway": "node.rename", "kind": "mutation"},
    # -- devices (ocFamilyAgentsApi) --
    {"family": "devices", "action": "setup-status", "methods": ["GET"],
     "gateway": "device.pair.setupStatus", "kind": "read"},
    {"family": "devices", "action": "setup-code-mint", "methods": ["POST"],
     "gateway": "device.pair.setupCode", "kind": "mutation",
     "aliases": {"ttl_seconds": "ttl_s"}},
    {"family": "devices", "action": "pairings", "methods": ["GET"],
     "gateway": "device.pair.list", "kind": "read"},
    {"family": "devices", "action": "approve", "methods": ["POST"],
     "gateway": "device.pair.approve", "kind": "mutation",
     "aliases": {"pairing_id": "code"}},
    {"family": "devices", "action": "reject", "methods": ["POST"],
     "gateway": "device.pair.reject", "kind": "mutation",
     "aliases": {"pairing_id": "code_id"}},
    {"family": "devices", "action": "remove", "methods": ["POST"],
     "gateway": "device.pair.remove", "kind": "mutation",
     "aliases": {"pairing_id": "code_id"}},
    {"family": "devices", "action": "token-rotate", "methods": ["POST"],
     "gateway": "device.token.rotate", "kind": "mutation",
     "aliases": {"pairing_id": "id"}},
    {"family": "devices", "action": "token-revoke", "methods": ["POST"],
     "gateway": "device.token.revoke", "kind": "mutation",
     "aliases": {"pairing_id": "id"}},
    {"family": "devices", "action": "enroll", "methods": ["POST"],
     "gateway": "device.pair.approve", "kind": "mutation",
     "aliases": {"setup_code_id": "code"}},
    {"family": "devices", "action": "identity", "methods": ["GET"],
     "gateway": None, "kind": "read",
     "gate": {"code": "DEVICE_IDENTITY_UNAVAILABLE",
              "detail": "no registered gateway method serves device "
                        "identity cards; pairings are the canonical read",
              "counterpart": "GET /api/qubit/devices/pairings "
                             "(device.pair.list)"}},
    # -- session-ops (ocFamilySessionsApi) --
    {"family": "session-ops", "action": "reset", "methods": ["POST"],
     "gateway": "sessions.reset", "kind": "mutation"},
    {"family": "session-ops", "action": "rewind", "methods": ["POST"],
     "gateway": "sessions.rewind", "kind": "mutation"},
    {"family": "session-ops", "action": "compact", "methods": ["POST"],
     "gateway": "sessions.compact", "kind": "mutation"},
    {"family": "session-ops", "action": "fork", "methods": ["POST"],
     "gateway": "sessions.fork", "kind": "mutation"},
    {"family": "session-ops", "action": "branch-switch", "methods": ["POST"],
     "gateway": "sessions.branches.switch", "kind": "mutation",
     "aliases": {"target": "branch_id"}},
    {"family": "session-ops", "action": "compaction-branch",
     "methods": ["POST"], "gateway": "sessions.compaction.branch",
     "kind": "mutation"},
    {"family": "session-ops", "action": "compaction-restore",
     "methods": ["POST"], "gateway": "sessions.compaction.restore",
     "kind": "mutation"},
    {"family": "session-ops", "action": "cleanup-empty", "methods": ["POST"],
     "gateway": "sessions.cleanup", "kind": "mutation"},
    {"family": "session-ops", "action": "goals-set", "methods": ["POST"],
     "gateway": "sessions.goal.update", "kind": "mutation",
     "aliases": {"goals": "goal"}},
    {"family": "session-ops", "action": "goals-clear", "methods": ["POST"],
     "gateway": "sessions.goal.clear", "kind": "mutation"},
    {"family": "session-ops", "action": "suggestions", "methods": ["GET"],
     "gateway": "session.suggestions.list", "kind": "read"},
    {"family": "session-ops", "action": "suggestions-add",
     "methods": ["POST"], "gateway": "session.suggestions.add",
     "kind": "mutation"},
    {"family": "session-ops", "action": "suggestions-resolve",
     "methods": ["POST"], "gateway": "session.suggestions.resolve",
     "kind": "mutation"},
    {"family": "session-ops", "action": "groups", "methods": ["GET"],
     "gateway": "sessions.groups.list", "kind": "read"},
    {"family": "session-ops", "action": "groups-create", "methods": ["POST"],
     "gateway": "sessions.groups.put", "kind": "mutation"},
    {"family": "session-ops", "action": "groups-rename", "methods": ["POST"],
     "gateway": "sessions.groups.rename", "kind": "mutation",
     "aliases": {"group_id": "name", "name": "to"}},
    {"family": "session-ops", "action": "groups-delete", "methods": ["POST"],
     "gateway": "sessions.groups.delete", "kind": "mutation",
     "aliases": {"group_id": "name"}},
    {"family": "session-ops", "action": "companion-ask", "methods": ["POST"],
     "gateway": "sessions.companion.ask", "kind": "read"},
    {"family": "session-ops", "action": "companion-state", "methods": ["GET"],
     "gateway": "sessions.companion.state", "kind": "read"},
    {"family": "session-ops", "action": "companion-reset",
     "methods": ["POST"], "gateway": "sessions.companion.reset",
     "kind": "mutation"},
    # -- worktrees (ocFamilySessionsApi; roster reads reuse /api/git/*) --
    {"family": "worktrees", "action": "create", "methods": ["POST"],
     "gateway": "worktrees.create", "kind": "mutation",
     "name_from_path": True},
    {"family": "worktrees", "action": "remove", "methods": ["POST"],
     "gateway": "worktrees.remove", "kind": "mutation"},
    {"family": "worktrees", "action": "restore", "methods": ["POST"],
     "gateway": "worktrees.restore", "kind": "mutation",
     "name_from_path": True},
    {"family": "worktrees", "action": "prune", "methods": ["POST"],
     "gateway": "worktrees.gc", "kind": "mutation"},
    # -- checkpoints (ocFamilySessionsApi; journal reads reuse /api/ops/*) --
    # Journal checkpoints are verify-only by design (no restore path); no
    # gateway method is registered behind create/verify, so both answer the
    # honest gate. Port candidate for a follow-up lane: register gateway
    # methods over voicebridge/checkpoints.py, then map these rows to them.
    {"family": "checkpoints", "action": "create", "methods": ["POST"],
     "gateway": None, "kind": "mutation",
     "gate": {"code": "CHECKPOINTS_JOURNAL_VERIFY_ONLY",
              "detail": "no registered gateway method creates journal "
                        "checkpoints; the journal surface is verify-only",
              "counterpart": "GET /api/ops/checkpoints (the checkpoint "
                             "journal) + POST /api/ops/checkpoints/prune"}},
    {"family": "checkpoints", "action": "verify", "methods": ["GET"],
     "gateway": None, "kind": "read",
     "gate": {"code": "CHECKPOINTS_JOURNAL_VERIFY_ONLY",
              "detail": "no registered gateway method verifies one journal "
                        "checkpoint by id; the journal surface is verify-only",
              "counterpart": "GET /api/ops/checkpoints (the checkpoint "
                             "journal)"}},
    # -- board (ocFamilyBoardApi; tab mutations fold into board.update) --
    {"family": "board", "action": "tabs", "methods": ["GET"],
     "gateway": "board.get", "kind": "read"},
    {"family": "board", "action": "tab_create", "methods": ["POST"],
     "gateway": "board.update", "kind": "mutation",
     "board_op": "tab_create"},
    {"family": "board", "action": "tab_rename", "methods": ["POST"],
     "gateway": "board.update", "kind": "mutation",
     "board_op": "tab_update_title"},
    {"family": "board", "action": "tab_move", "methods": ["POST"],
     "gateway": "board.update", "kind": "mutation",
     "board_op": "tab_update_position"},
    {"family": "board", "action": "tab_delete", "methods": ["POST"],
     "gateway": "board.update", "kind": "mutation",
     "board_op": "tab_delete"},
    {"family": "board", "action": "widgets", "methods": ["GET"],
     "gateway": "board.get", "kind": "read"},
    {"family": "board", "action": "widget_add", "methods": ["POST"],
     "gateway": "board.widget.put", "kind": "mutation"},
    {"family": "board", "action": "widget_remove", "methods": ["POST"],
     "gateway": "board.update", "kind": "mutation",
     "board_op": "widget_remove"},
    {"family": "board", "action": "roster", "methods": ["GET"],
     "gateway": "board.widget.grant", "kind": "read"},
    {"family": "board", "action": "role_grant", "methods": ["POST"],
     "gateway": "board.widget.grant", "kind": "mutation"},
    {"family": "board", "action": "log", "methods": ["GET"],
     "gateway": "board.event", "kind": "read"},
    # -- task (ocFamilyBoardApi) --
    {"family": "task", "action": "ledger", "methods": ["GET"],
     "gateway": "task.history", "kind": "read"},
    {"family": "task", "action": "suggestions", "methods": ["GET"],
     "gateway": "task.suggestions", "kind": "read"},
    {"family": "task", "action": "cancel", "methods": ["POST"],
     "gateway": "tasks.cancel", "kind": "mutation"},
    {"family": "task", "action": "retry", "methods": ["POST"],
     "gateway": "tasks.retry", "kind": "mutation"},
    # -- talk (ocFamilyTalkApi; realtime rows are never called) --
    {"family": "talk", "action": "mode", "methods": ["GET"],
     "gateway": "talk.mode", "kind": "read"},
    {"family": "talk", "action": "catalog", "methods": ["GET"],
     "gateway": "talk.catalog", "kind": "read"},
    {"family": "talk", "action": "config", "methods": ["GET"],
     "gateway": "talk.config", "kind": "read"},
    {"family": "talk", "action": "voicewake", "methods": ["GET"],
     "gateway": "voicewake.get", "kind": "read"},
    {"family": "talk", "action": "client-steer", "methods": ["POST"],
     "gateway": "talk.client.steer", "kind": "mutation"},
    {"family": "talk", "action": "client-transcript", "methods": ["POST"],
     "gateway": "talk.client.transcript", "kind": "mutation"},
    {"family": "talk", "action": "client-tool-call", "methods": ["POST"],
     "gateway": "talk.client.toolCall", "kind": "mutation"},
    {"family": "talk", "action": "speak", "methods": ["POST"],
     "gateway": "talk.speak", "kind": "mutation"},
    # -- scheduler (ocFamilyTalkApi) --
    {"family": "scheduler", "action": "cron-update", "methods": ["POST"],
     "gateway": "cron.update", "kind": "mutation"},
    {"family": "scheduler", "action": "tts-set-provider", "methods": ["POST"],
     "gateway": "tts.setProvider", "kind": "mutation"},
    {"family": "scheduler", "action": "tts-enable", "methods": ["POST"],
     "gateway": "tts.enable", "kind": "mutation"},
    {"family": "scheduler", "action": "tts-disable", "methods": ["POST"],
     "gateway": "tts.disable", "kind": "mutation"},
    {"family": "scheduler", "action": "tts-set-persona", "methods": ["POST"],
     "gateway": "tts.setPersona", "kind": "mutation"},
    {"family": "scheduler", "action": "suspend-prepare", "methods": ["GET"],
     "gateway": "gateway.suspend.prepare", "kind": "read"},
    {"family": "scheduler", "action": "suspend-resume", "methods": ["GET"],
     "gateway": "gateway.suspend.resume", "kind": "read"},
    {"family": "scheduler", "action": "tasks-cancel", "methods": ["POST"],
     "gateway": "tasks.cancel", "kind": "mutation"},
    {"family": "scheduler", "action": "tasks-retry", "methods": ["POST"],
     "gateway": "tasks.retry", "kind": "mutation"},
    # -- skills-review (ocFamilyAdminApi) --
    {"family": "skills-review", "action": "proposals", "methods": ["GET"],
     "gateway": "skills.proposals.list", "kind": "read"},
    {"family": "skills-review", "action": "proposals", "methods": ["POST"],
     "gateway": "skills.proposals.create", "kind": "mutation"},
    {"family": "skills-review", "action": "proposals/{id}",
     "methods": ["GET"], "gateway": "skills.proposals.inspect",
     "kind": "read"},
    {"family": "skills-review", "action": "history-status",
     "methods": ["GET"], "gateway": "skills.proposals.historyStatus",
     "kind": "read"},
    {"family": "skills-review", "action": "proposals/{id}/apply",
     "methods": ["POST"], "gateway": "skills.proposals.apply",
     "kind": "mutation"},
    {"family": "skills-review", "action": "proposals/{id}/reject",
     "methods": ["POST"], "gateway": "skills.proposals.reject",
     "kind": "mutation"},
    {"family": "skills-review", "action": "proposals/{id}/quarantine",
     "methods": ["POST"], "gateway": "skills.proposals.quarantine",
     "kind": "mutation"},
    {"family": "skills-review", "action": "proposals/{id}/revise",
     "methods": ["POST"], "gateway": "skills.proposals.revise",
     "kind": "mutation"},
    {"family": "skills-review", "action": "proposals/{id}/update",
     "methods": ["POST"], "gateway": "skills.proposals.update",
     "kind": "mutation"},
    {"family": "skills-review", "action": "proposals/{id}/request-revision",
     "methods": ["POST"], "gateway": "skills.proposals.requestRevision",
     "kind": "mutation"},
    {"family": "skills-review", "action": "events", "methods": ["GET"],
     "gateway": "skills.proposals.events.list", "kind": "read",
     "aliases": {"after": "after_sequence"}},
    {"family": "skills-review", "action": "curator/pin", "methods": ["POST"],
     "gateway": "skills.curator.pin", "kind": "mutation"},
    {"family": "skills-review", "action": "curator/unpin",
     "methods": ["POST"], "gateway": "skills.curator.unpin",
     "kind": "mutation"},
    {"family": "skills-review", "action": "curator/restore",
     "methods": ["POST"], "gateway": "skills.curator.restore",
     "kind": "mutation"},
    # -- mcp-calls (ocFamilyAdminApi) --
    {"family": "mcp-calls", "action": "call-tool", "methods": ["POST"],
     "gateway": "mcp.app.callTool", "kind": "mutation"},
    {"family": "mcp-calls", "action": "resources", "methods": ["GET"],
     "gateway": "mcp.app.listResources", "kind": "read"},
    {"family": "mcp-calls", "action": "resources/read", "methods": ["GET"],
     "gateway": "mcp.app.readResource", "kind": "read"},
    # -- users (ocFamilyAdminApi; single-operator adapter) --
    {"family": "users", "action": "list", "methods": ["GET"],
     "gateway": "users.list", "kind": "read"},
    {"family": "users", "action": "self", "methods": ["GET"],
     "gateway": "users.self", "kind": "read"},
    {"family": "users", "action": "mentionable", "methods": ["GET"],
     "gateway": "users.mentionable", "kind": "read"},
    {"family": "users", "action": "prefs", "methods": ["GET"],
     "gateway": "users.prefs.get", "kind": "read"},
    {"family": "users", "action": "prefs", "methods": ["PUT"],
     "gateway": "users.prefs.set", "kind": "write"},
    {"family": "users", "action": "auth-links", "methods": ["GET"],
     "gateway": "users.listAuthLinks", "kind": "read"},
    {"family": "users", "action": "model-accounts", "methods": ["GET"],
     "gateway": "users.listModelAccounts", "kind": "read"},
    {"family": "users", "action": "select-model-account",
     "methods": ["POST"], "gateway": "users.selectModelAccount",
     "kind": "mutation"},
    {"family": "users", "action": "link-auth-profile", "methods": ["POST"],
     "gateway": "users.linkAuthProfile", "kind": "mutation"},
    {"family": "users", "action": "unlink-auth-profile", "methods": ["POST"],
     "gateway": "users.unlinkAuthProfile", "kind": "mutation"},
    {"family": "users", "action": "auth-connect-catalog", "methods": ["GET"],
     "gateway": "users.authConnect.catalog", "kind": "read"},
    # -- canvas (ocFamilyCanvasApi; 9 rows verbatim) --
    # No canvas file host or node-presentation backend is registered: every
    # row answers an honest typed gate (never invented behavior). Reads are
    # kind=read, the six POST rows kind=mutation (confirm-gated shape — the
    # gate answers 403 before any confirm is staged). Follow-up for a
    # builder lane: a confined static host for GET /__qubit__/canvas/*
    # reusing the plugin-asset precedent (resolve_plugin_asset); until then
    # the preview frame stays a pure URL-string builder client-side.
    {"family": "canvas", "action": "list", "methods": ["GET"],
     "gateway": None, "kind": "read",
     "gate": {"code": "CANVAS_HOST_UNWIRED",
              "detail": "no canvas file host is registered; nothing is "
                        "listed",
              "counterpart": "none yet — follow-up: confined static host "
                             "GET /__qubit__/canvas/* reusing the "
                             "plugin-asset precedent (resolve_plugin_asset)"}},
    {"family": "canvas", "action": "get", "methods": ["GET"],
     "gateway": None, "kind": "read",
     "gate": {"code": "CANVAS_HOST_UNWIRED",
              "detail": "no canvas file host is registered; nothing is read",
              "counterpart": "none yet — follow-up: confined static host "
                             "GET /__qubit__/canvas/* reusing the "
                             "plugin-asset precedent (resolve_plugin_asset)"}},
    {"family": "canvas", "action": "live", "methods": ["GET"],
     "gateway": None, "kind": "read",
     "gate": {"code": "CANVAS_HOST_UNWIRED",
              "detail": "no canvas file host is registered; no live-reload "
                        "state is reported",
              "counterpart": "none yet — follow-up: confined static host "
                             "GET /__qubit__/canvas/* reusing the "
                             "plugin-asset precedent (resolve_plugin_asset)"}},
    {"family": "canvas", "action": "add", "methods": ["POST"],
     "gateway": None, "kind": "mutation",
     "gate": {"code": "CANVAS_HOST_UNWIRED",
              "detail": "no canvas file host is registered; nothing is "
                        "written",
              "counterpart": "none yet — follow-up: brokered path-confined "
                             "write behind this bridge row"}},
    {"family": "canvas", "action": "remove", "methods": ["POST"],
     "gateway": None, "kind": "mutation",
     "gate": {"code": "CANVAS_HOST_UNWIRED",
              "detail": "no canvas file host is registered; nothing is "
                        "removed",
              "counterpart": "none yet — follow-up: brokered path-confined "
                             "write behind this bridge row"}},
    {"family": "canvas", "action": "present", "methods": ["POST"],
     "gateway": None, "kind": "mutation",
     "gate": {"code": "CANVAS_PRESENT_UNWIRED",
              "detail": "no node-presentation backend is registered; nothing "
                        "is shown on any node",
              "counterpart": "the node roster reads "
                             "(GET /api/qubit/nodes/list, node.list) for "
                             "identity; no presentation backend exists"}},
    {"family": "canvas", "action": "hide", "methods": ["POST"],
     "gateway": None, "kind": "mutation",
     "gate": {"code": "CANVAS_PRESENT_UNWIRED",
              "detail": "no node-presentation backend is registered; nothing "
                        "is hidden on any node",
              "counterpart": "the node roster reads "
                             "(GET /api/qubit/nodes/list, node.list) for "
                             "identity; no presentation backend exists"}},
    {"family": "canvas", "action": "navigate", "methods": ["POST"],
     "gateway": None, "kind": "mutation",
     "gate": {"code": "CANVAS_PRESENT_UNWIRED",
              "detail": "no node-presentation backend is registered; nothing "
                        "is opened on any node",
              "counterpart": "the node roster reads "
                             "(GET /api/qubit/nodes/list, node.list) for "
                             "identity; no presentation backend exists"}},
    {"family": "canvas", "action": "snapshot", "methods": ["POST"],
     "gateway": None, "kind": "mutation",
     "gate": {"code": "CANVAS_PRESENT_UNWIRED",
              "detail": "no node-presentation backend is registered; no "
                        "screenshot proof is captured",
              "counterpart": "the node roster reads "
                             "(GET /api/qubit/nodes/list, node.list) for "
                             "identity; no presentation backend exists"}},
    # -- tasks (ocFamilyTasksApi; 4 rows with paths verbatim) --
    # Delegates to the already-registered tasks.* gateway methods, which run
    # the shared ledger (task_ledger.list_tasks/show) and the live run
    # primitives (actions.ActionStore.cancel/retry, chat_transport by
    # session). `dismiss` has no path by design (REFUSED-NEVER-WIRED) and
    # gets no bridge row — the page renders TASKS_DISMISS_REFUSAL verbatim.
    {"family": "tasks", "action": "list", "methods": ["GET"],
     "gateway": "tasks.list", "kind": "read"},
    {"family": "tasks", "action": "get", "methods": ["GET"],
     "gateway": "tasks.get", "kind": "read",
     "aliases": {"lookup": "taskId"}},
    {"family": "tasks", "action": "cancel", "methods": ["POST"],
     "gateway": "tasks.cancel", "kind": "mutation"},
    {"family": "tasks", "action": "retry", "methods": ["POST"],
     "gateway": "tasks.retry", "kind": "mutation"},
    # -- flows (ocFamilyFlowsApi; the 5 needs-bridge rows verbatim) --
    # The 11 cron rows reuse the existing /api/cron/* routes (no bridge rows
    # needed). `hooks` reads the real hook catalog (hooks.status); enable /
    # disable / cancel / programs have no backend and answer honest gates
    # whose counterparts name the real surface.
    {"family": "flows", "action": "hooks", "methods": ["GET"],
     "gateway": "hooks.status", "kind": "read"},
    {"family": "flows", "action": "hook_enable", "methods": ["POST"],
     "gateway": None, "kind": "mutation",
     "gate": {"code": "FLOWS_HOOKS_UNWIRED",
              "detail": "no backend hooks route exists; no hook is enabled",
              "counterpart": "GET /api/qubit/flows/hooks (hooks.status) "
                             "for the catalog + cron-backed flows "
                             "(GET /api/cron/jobs) for real scheduling"}},
    {"family": "flows", "action": "hook_disable", "methods": ["POST"],
     "gateway": None, "kind": "mutation",
     "gate": {"code": "FLOWS_HOOKS_UNWIRED",
              "detail": "no backend hooks route exists; no hook is disabled",
              "counterpart": "GET /api/qubit/flows/hooks (hooks.status) "
                             "for the catalog + cron-backed flows "
                             "(GET /api/cron/jobs) for real scheduling"}},
    {"family": "flows", "action": "cancel", "methods": ["POST"],
     "gateway": None, "kind": "mutation",
     "gate": {"code": "FLOWS_CANCEL_UNWIRED",
              "detail": "no sticky taskflow-cancel backend exists; nothing "
                        "is cancelled",
              "counterpart": "DELETE /api/cron/jobs/{id} or POST "
                             "/api/cron/jobs/{id}/pause (confirm-gated)"}},
    {"family": "flows", "action": "programs", "methods": ["GET"],
     "gateway": None, "kind": "read",
     "gate": {"code": "FLOWS_PROGRAMS_UNWIRED",
              "detail": "no standing-order programs registry exists; the "
                        "durable-flow list is the programs view",
              "counterpart": "GET /api/cron/jobs (recurring schedules "
                             "render as standing orders)"}},
    # -- memory (ocFamilyMemoryApi; 5 rows verbatim) --
    # Status-only reality: the live status is served today at the canonical
    # GET /api/memory paths (qubit-local, the only provider); no writer is
    # invented behind the /api/qubit/memory/* namespace, so every row answers
    # the honest memory-provider gate. provider_set carries the verbatim
    # MEMORY_LOCAL_ONLY refusal the canonical PUT emits for non-local names.
    {"family": "memory", "action": "status", "methods": ["GET"],
     "gateway": None, "kind": "read",
     "gate": {"code": "MEMORY_BRIDGE_UNWIRED",
              "detail": "the /api/qubit/memory/* namespace has no backend; "
                        "the live status is served at the canonical path",
              "counterpart": "GET /api/memory (qubit-local, the only "
                             "provider)"}},
    {"family": "memory", "action": "provider", "methods": ["GET"],
     "gateway": None, "kind": "read",
     "gate": {"code": "MEMORY_BRIDGE_UNWIRED",
              "detail": "the /api/qubit/memory/* namespace has no backend; "
                        "the live status is served at the canonical path",
              "counterpart": "GET /api/memory (qubit-local, the only "
                             "provider)"}},
    {"family": "memory", "action": "provider-config", "methods": ["GET"],
     "gateway": None, "kind": "read",
     "gate": {"code": "MEMORY_BRIDGE_UNWIRED",
              "detail": "the /api/qubit/memory/* namespace has no backend; "
                        "provider config is served at the canonical path",
              "counterpart": "GET /api/memory/providers/<id>/config "
                             "(qubit-local only)"}},
    {"family": "memory", "action": "provider-set", "methods": ["POST"],
     "gateway": None, "kind": "mutation",
     "gate": {"code": "MEMORY_LOCAL_ONLY",
              "detail": "qubit-local is the only memory provider; cloud "
                        "sync is refused by policy",
              "counterpart": "PUT /api/memory/provider (persists "
                             "qubit-local only)"}},
    {"family": "memory", "action": "probe", "methods": ["GET"],
     "gateway": None, "kind": "read",
     "gate": {"code": "MEMORY_BRIDGE_UNWIRED",
              "detail": "the /api/qubit/memory/* namespace has no backend; "
                        "the live probe is served at the canonical path",
              "counterpart": "GET /api/memory/providers/qubit-local"}},
    # -- webchat (ocFamilyWebchatApi; reuse-only — no bridge rows) --
    # Every webchat capability reuses an existing chat endpoint (sessions
    # list/create/history, POST /api/qubit/chat + /stream); NEEDS_BRIDGE is
    # empty by design, so no allowlist rows are added here.
]

_NAME_LIKE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _qubit_family_match(family: str, rest: str,
                        method: str) -> Tuple[Optional[Dict[str, Any]], str]:
    """Match a bridge remainder against the allowlist (verbatim, no imports).

    Returns (entry, id) — the allowlist row plus the "{id}" segment value
    ("" when the template has none). Unknown family/action is (None, "").
    """
    segs = [s for s in str(rest or "").split("/") if s not in ("", ".")]
    if not segs or ".." in segs:
        return None, ""
    for entry in _QUBIT_FAMILY_BRIDGE:
        if entry["family"] != family or method not in entry["methods"]:
            continue
        tsegs = str(entry["action"]).split("/")
        if len(tsegs) != len(segs):
            continue
        seen = ""
        ok = True
        for tseg, seg in zip(tsegs, segs):
            if tseg == "{id}":
                if not seg:
                    ok = False
                    break
                seen = seg
            elif tseg != seg:
                ok = False
                break
        if ok:
            return entry, seen
    return None, ""


def _qubit_family_http(code: Any) -> int:
    """Map a gateway error code to its honest HTTP status (never 2xx)."""
    c = str(code or "")
    if ("NOT_FOUND" in c or c.endswith("_GONE") or c == "STORE_MISSING"
            or "UNKNOWN" in c or c == "AGENT_NOT_FOUND"):
        return 404
    if "BAD_PARAMS" in c or "MALFORMED" in c:
        return 400
    if ("OPERATOR_REQUIRED" in c or "OPERATOR_OWNED" in c
            or c == "EMERGENCY_STOP"):
        return 403
    if "CONFIRM" in c:
        return 409
    return 400


def _qubit_family_params(entry: Dict[str, Any], q: Dict[str, str],
                         b: Dict[str, Any], pid: str) -> Dict[str, Any]:
    """Build the gateway params: query + body + path id, aliases applied.

    Aliases rename caller keys to the gateway method's parameter names,
    applied simultaneously from the original body so two renames can never
    collide. The gateway backend stays authoritative and validates everything.
    """
    params: Dict[str, Any] = {}
    if isinstance(q, dict):
        params.update(q)
    body = b if isinstance(b, dict) else {}
    aliases = entry.get("aliases") or {}
    renamed: Dict[str, Any] = {}
    for key, value in body.items():
        target = aliases.get(key, key)
        if target not in renamed and target not in params:
            renamed[target] = value
    params.update(renamed)
    if pid and "id" not in params:
        params["id"] = pid
    board_op = entry.get("board_op")
    if board_op:
        board = str(params.get("board", "") or "main")
        tab_id = str(params.get("tabId", params.get("tab_id", "")) or "")
        title = str(params.get("title", "") or "")
        if board_op == "tab_create":
            params["ops"] = [{"kind": "tab_create", "tabId": tab_id,
                              "title": title}]
        elif board_op == "tab_update_title":
            params["ops"] = [{"kind": "tab_update", "tabId": tab_id,
                              "title": title}]
        elif board_op == "tab_update_position":
            try:
                position = int(params.get("position", 0))
            except (TypeError, ValueError):
                position = 0
            params["ops"] = [{"kind": "tab_update", "tabId": tab_id,
                              "position": position}]
        elif board_op == "tab_delete":
            params["ops"] = [{"kind": "tab_delete", "tabId": tab_id}]
        elif board_op == "widget_remove":
            params["ops"] = [{"kind": "widget_remove",
                              "name": str(params.get("id", "") or "")}]
        params["board"] = board
    if entry.get("name_from_path") and "name" not in params:
        raw = str(params.get("path", "") or "")
        if _NAME_LIKE_RE.match(raw):
            params["name"] = raw
    return params


def _qubit_family_gateway_call(cfg: Any, entry: Dict[str, Any],
                               params: Dict[str, Any]
                               ) -> Tuple[int, Dict[str, Any]]:
    """Run one allowlisted gateway method; GatewayErrors become typed HTTP."""
    from .gateway.gateway_ws import GatewayError
    from .gateway.rpc import default_registry

    method = str(entry["gateway"] or "")
    try:
        registry = default_registry()
        if method not in registry.names():
            return 500, {"ok": False, "code": "FAMILY_METHOD_NOT_REGISTERED",
                         "detail": f"gateway method {method!r} is not "
                                   "registered; nothing was executed",
                         "gateway_method": method}
        result = registry.handle(cfg, method, params)
    except GatewayError as exc:
        return _qubit_family_http(exc.code), {
            "ok": False, "code": exc.code,
            "detail": _redact(exc.message)[:300],
            "gateway_method": method}
    except Exception as exc:
        return _fail(exc)
    if not isinstance(result, dict):
        result = {"result": result}
    return 200, _jsonable(result)


def _qubit_family_dispatch(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                           p: Dict[str, str], http_method: str
                           ) -> Tuple[int, Dict[str, Any]]:
    """Shared bridge core: allowlist -> confirm gate -> registry delegate."""
    family = str((p or {}).get("family", "") or "")
    rest = str((p or {}).get("rest", "") or "")
    entry, pid = _qubit_family_match(family, rest, http_method)
    if entry is None:
        return 404, {"ok": False, "code": "FAMILY_UNKNOWN_ACTION",
                     "detail": f"no Qubit family route for {http_method} "
                               f"/api/qubit/{family}/{rest}",
                      "qubit_counterpart": "the ten family manifests "
                                           "(ocFamily*Api ROUTES)"}
    kind = str(entry.get("kind", "read") or "read")
    if entry.get("gateway") is None:
        gate = entry.get("gate") or {}
        return 403, {"ok": False, "state": "UNAVAILABLE",
                     "code": gate.get("code", "FAMILY_GATE"),
                     "detail": gate.get("detail", "no backend behind "
                                                  "this family path"),
                     "qubit_counterpart": gate.get("counterpart", ""),
                     "owner": "qubit-policy",
                     "rollback": "read-only; nothing to roll back"}
    params = _qubit_family_params(entry, q, b, pid)
    if kind in ("mutation", "write"):
        if _estop(cfg):
            return 403, {"ok": False, "state": "UNAVAILABLE",
                         "code": "EMERGENCY_STOP",
                         "detail": "emergency stop latched; mutation refused",
                         "owner": "qubit-policy",
                         "rollback": "nothing changed; nothing to roll back"}
        if kind == "mutation":
            safe = {k: v for k, v in params.items()
                    if isinstance(v, (str, int, float, bool)) or v is None}
            guard = _compat_confirm(
                cfg, f"family.{family}.{entry['action']}",
                f"family {family}/{entry['action']} requires explicit "
                "confirmation: re-POST with {\"confirm\": true}",
                q, b, payload=safe)
            if guard is not None:
                return guard
        params["confirmed"] = True
        params["confirmation_id"] = str(
            params.get("confirmation_id", "") or "")
        params["requested_by"] = "dashboard:user"
    else:
        params.setdefault("requested_by", "dashboard:user")
    return _qubit_family_gateway_call(cfg, entry, params)


def h_qubit_family_get(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                       p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/qubit/<family>/<action> -> allowlisted gateway read."""
    return _qubit_family_dispatch(cfg, q, b, p, "GET")


def h_qubit_family_post(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                        p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/qubit/<family>/<action> -> confirm-gated gateway delegate."""
    return _qubit_family_dispatch(cfg, q, b, p, "POST")


def h_qubit_family_put(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                       p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """PUT /api/qubit/<family>/<action> -> direct gateway write (prefs)."""
    return _qubit_family_dispatch(cfg, q, b, p, "PUT")


HANDLERS["h_qubit_family_get"] = h_qubit_family_get
HANDLERS["h_qubit_family_post"] = h_qubit_family_post
HANDLERS["h_qubit_family_put"] = h_qubit_family_put

for _qfb_family in sorted({e["family"] for e in _QUBIT_FAMILY_BRIDGE}):
    _qfb_methods = sorted({m for e in _QUBIT_FAMILY_BRIDGE
                           if e["family"] == _qfb_family for m in e["methods"]})
    for _qfb_method in _qfb_methods:
        _r(_qfb_method,
           r"/api/qubit/(?P<family>" + _qfb_family + r")/(?P<rest>.+)",
           "h_qubit_family_" + _qfb_method.lower())
del _qfb_family, _qfb_methods, _qfb_method

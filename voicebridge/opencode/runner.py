"""Packet 8R: supervised OpenCode coding workflow adapter (optional).

See voicebridge.opencode (__init__) for the contract.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..actions import ADAPTER_OPENCODE, ActionRunner, ActionSpec, ActionStore, ST_OK, _render_preview
from ..broker import ToolBinding
from ..capabilities import CapabilitySpec
from ..logging_setup import log_event
from .policy import (
    DEFAULT_TIMEOUT_S, OPENCODE_ACTIONS, PINNED_OPENCODE_VERSION,
    OpenCodeError,
)
from .runs import RunOpencode, VersionProbe, _ExecCtx, _plan_impl, _run_impl
from .specs import (
    ACT_PLAN, ACT_RUN, opencode_action_specs, register_opencode_specs,
)
from .transport import _probe_version, real_run_opencode
from .state import (
    _adapter_path, _opencode_cfg, _read_state, _resolve_binary,
    _write_state,
)
# ---------------------------------------------------------------------------
# Runner (same broker/gate/receipt machinery as every other channel)
# ---------------------------------------------------------------------------

class OpenCodeRunner(ActionRunner):
    """8F ActionRunner bound to the OpenCode capabilities.

    ``preview``/``prepare``/``execute``/``cancel``/``receipts`` and the 7E
    gate, action-hash confirmation, receipt persistence, and audit all come
    from ``ActionRunner`` verbatim. Live execution additionally requires the
    two-step enable (packaged master switch + state-file user enablement) and
    an injected-or-real executor; otherwise every call returns the typed
    ``ADAPTER_DISABLED`` result and no subprocess is spawned.
    """

    def __init__(self, cfg=None, *,
                 store: Optional[ActionStore] = None,
                 perms: Optional[Any] = None,
                 broker: Optional[Any] = None,
                 executors: Optional[Dict[str, Callable]] = None,
                 clock: Callable[[], float] = time.time,
                 confirmation_ttl_s: Optional[float] = None,
                 run_opencode: Optional[RunOpencode] = None,
                 version_probe: Optional[VersionProbe] = None,
                 power: Optional[Any] = None):
        register_opencode_specs()
        ocfg = _opencode_cfg(cfg)
        timeout_default = ocfg.get("timeout_s") or DEFAULT_TIMEOUT_S
        super().__init__(cfg=cfg, store=store, perms=perms, broker=broker,
                         executors=executors, enabled=True, clock=clock,
                         confirmation_ttl_s=confirmation_ttl_s, power=power)
        self._ctx = _ExecCtx(
            binary=_resolve_binary(ocfg),
            master_enabled=bool(ocfg.get("enabled", False)),
            timeout_default=float(timeout_default),
            state_path=str(_adapter_path(cfg)),
            pinned_version=str(ocfg.get("pinned_version")
                               or PINNED_OPENCODE_VERSION),
            version_probe=version_probe or _probe_version,
            run_opencode=run_opencode or real_run_opencode)
        # Bind the executors unless the caller injected a full broker.
        if broker is None:
            self._bind_executors(executors or {})

    # -- enablement ------------------------------------------------------
    def status(self) -> Dict[str, Any]:
        st = _read_state(self.cfg)
        ocfg = _opencode_cfg(self.cfg)
        master = bool(ocfg.get("enabled", False))
        binary = _resolve_binary(ocfg)
        pinned = str(ocfg.get("pinned_version") or PINNED_OPENCODE_VERSION)
        effective = bool(master and st.get("user_enabled")
                         and not st.get("emergency_stop"))
        reason = ("OK" if effective else
                  ("CONFIG_DISABLED" if not master else
                   ("NOT_ENABLED" if not st.get("user_enabled") else
                    "EMERGENCY_STOP")))
        # The version probe executes the configured binary: it runs ONLY when
        # the adapter is effectively enabled, with the strict sanitized
        # environment, and never through a shell. Disabled state ⇒ no spawn.
        if effective:
            installed = self._ctx.version_probe(binary)
        else:
            installed = "not-probed (disabled)"
        return {"state": ST_OK, "adapter": ADAPTER_OPENCODE,
                "effective_enabled": effective, "reason": reason,
                "config_enabled": master, "user_enabled": bool(
                    st.get("user_enabled")),
                "emergency_stop": bool(st.get("emergency_stop")),
                "binary": binary, "pinned_version": pinned,
                "installed_version": installed,
                "version_match": (installed == pinned),
                "capabilities": [s.to_dict() for s in
                                 opencode_action_specs()],
                "stores": {"adapter_state": str(_adapter_path(self.cfg)),
                           "receipts": str(self.store.path)}}

    def enable(self, requested_by: str = "user") -> Dict[str, Any]:
        if requested_by != "user":
            raise OpenCodeError("enable requires the authenticated owner")
        st = _read_state(self.cfg)
        st["user_enabled"] = True
        _write_state(self.cfg, st)
        log_event("opencode.adapter.enable", requested_by="user")
        return self.status()

    def disable(self, requested_by: str = "user") -> Dict[str, Any]:
        if requested_by != "user":
            raise OpenCodeError("disable requires the authenticated owner")
        st = _read_state(self.cfg)
        st["user_enabled"] = False
        _write_state(self.cfg, st)
        log_event("opencode.adapter.disable", requested_by="user")
        return self.status()

    def emergency_stop(self) -> Dict[str, Any]:
        st = _read_state(self.cfg)
        st["emergency_stop"] = True
        _write_state(self.cfg, st)
        log_event("opencode.adapter.emergency_stop")
        return self.status()

    def reset(self, requested_by: str = "user") -> Dict[str, Any]:
        if requested_by != "user":
            raise OpenCodeError("reset requires the authenticated owner")
        _write_state(self.cfg, {"user_enabled": False,
                                "emergency_stop": False})
        log_event("opencode.adapter.reset", requested_by="user")
        return self.status()

    def _effective(self) -> Tuple[bool, str]:
        st = _read_state(self.cfg)
        ocfg = _opencode_cfg(self.cfg)
        if not bool(ocfg.get("enabled", False)):
            return False, "CONFIG_DISABLED"
        if not bool(st.get("user_enabled")):
            return False, "ADAPTER_DISABLED"
        if bool(st.get("emergency_stop")):
            return False, "EMERGENCY_STOP"
        return True, "OK"

    # -- capability inventory --------------------------------------------
    def opencode_actions(self) -> List[Dict[str, Any]]:
        ok, reason = self._effective()
        out = []
        for spec in opencode_action_specs():
            d = spec.to_dict()
            d["effective"] = self.check(spec.action_id, spec.scopes[0])
            d["executor"] = ("disabled" if not ok else "adapter")
            d["adapter_state"] = reason
            out.append(d)
        return out

    def grant(self, capability: str, scopes: Tuple[str, ...] = ("code",),
              ttl_s: Optional[float] = None) -> Dict[str, Any]:
        """Grant an opencode capability in the shared 7E store (owner only)."""
        store = self.perms
        for cap in OPENCODE_ACTIONS:
            if capability != cap:
                continue
            g = store.grant(capability, requested_by="cli:user",
                            confirmed=True, scopes=tuple(scopes),
                            ttl_s=ttl_s)
            log_event("opencode.grant", capability=capability,
                      scopes=list(scopes))
            return {"state": ST_OK, "grant": g.to_dict()}
        raise OpenCodeError(f"unknown opencode capability {capability!r}")

    def revoke_grant(self, grant_id: str) -> Dict[str, Any]:
        out = self.perms.revoke(grant_id, requested_by="cli:user")
        log_event("opencode.revoke", grant_id=grant_id)
        return {"state": ST_OK, "revoked": grant_id, "result": out}

    # -- executors --------------------------------------------------------
    # NOTE: executor closures capture only the spawn-safe _ExecCtx (plain
    # data + transport). The broker runs side-effecting callables in a
    # forkserver child via cloudpickle, so closing over the runner (locks,
    # Config, stores) would fail with SERIALIZATION_FAILED.
    def _bind_executors(self, fixtures: Dict[str, Callable]) -> None:
        from ..broker import ToolBinding
        from ..capabilities import CapabilitySpec
        ctx = self._ctx
        for spec in opencode_action_specs():
            if spec.action_id in fixtures:
                fn = fixtures[spec.action_id]
            elif spec.action_id == ACT_PLAN:
                def fn(envelope, _ctx=ctx):
                    return _plan_impl(_ctx, envelope)
            else:
                def fn(envelope, _ctx=ctx):
                    return _run_impl(_ctx, envelope)
            self.executors[spec.action_id] = fn
            self.broker.register(ToolBinding(
                spec=CapabilitySpec(
                    name=spec.action_id, version=spec.version, tier=spec.tier,
                    summary=spec.summary[:200], scopes=spec.scopes,
                    timeout_s=spec.timeout_s,
                    arg_validator=self._envelope_check(spec)),
                side_effects=spec.side_effects, fn=fn,
                preview=lambda _env, _s=spec: _render_preview(_s, _env)))

    @staticmethod
    def _envelope_check(spec: ActionSpec):
        from ..actions import validate_envelope

        def _check(args: Dict[str, Any]) -> None:
            validate_envelope(args, spec)
        return _check


def opencode_runner(cfg=None, *,
                    executors: Optional[Dict[str, Callable]] = None,
                    run_opencode: Optional[RunOpencode] = None,
                    version_probe: Optional[VersionProbe] = None,
                    confirmation_ttl_s: Optional[float] = None,
                    power: Optional[Any] = None) -> OpenCodeRunner:
    """Build the supervised OpenCode runner on the shared 8F machinery."""
    return OpenCodeRunner(cfg, executors=executors, run_opencode=run_opencode,
                          version_probe=version_probe,
                          confirmation_ttl_s=confirmation_ttl_s,
                          power=power)


def request_coding_run(runner: OpenCodeRunner, *, project: str,
                       task_args: Dict[str, Any],
                       target: str = "", scope: str = "code",
                       source: str = "user",
                       confirmation_id: Optional[str] = None,
                       confirmed: bool = False,
                       dry_run: bool = False,
                       expected_hash: str = "") -> Dict[str, Any]:
    """One broker entry shared by every channel that may invoke OpenCode.

    Wired today: the authenticated CLI (full parity: grant/preview/prepare/
    execute/cancel/receipts) and the authenticated dashboard (read-only
    status/list/check parity on the same runner/broker). Voice and tray
    execution are INTENTIONALLY NOT WIRED: voice coding requests continue
    through the existing OpenClaw/Hermes dispatch, and no tray execution
    surface exists. Any future voice/tray wiring must call this entry with
    the channel's source label — direct subprocess, shell, or MCP paths stay
    forbidden, and model/page/skill/tool text can never mint or carry
    confirmations (CONFIRMATION_SOURCE_FORBIDDEN).

    Writes outside a disposable worktree additionally require the preview
    hash: pass the `expected_hash` from a prior preview, or the run is
    refused as STALE_TARGET before anything executes.
    """
    tgt = target or project
    if task_args.get("disposable", True) is False and not expected_hash \
            and not dry_run:
        return {"state": "stale_target", "code": "PREVIEW_REQUIRED",
                "action_id": ACT_RUN,
                "detail": "in-project writes require a prior preview hash; "
                          "run preview first and pass --expected-hash"}
    return runner.execute(ACT_RUN, args={"project": project, **task_args},
                          target=tgt, scope=scope, source=source,
                          confirmation_id=confirmation_id,
                          confirmed=confirmed, dry_run=dry_run,
                          expected_hash=expected_hash)

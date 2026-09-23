"""Trusted capability broker (Packet 3A).

Puts existing trusted host tools (whitelisted local actions, user-configured
local-script templates) behind the Packet 1A capability registry and ONE
PolicyEngine shared with the Qubit WorkerGuard.

Contract per tool (Phase 3 gate): typed input schema, declared side effects,
permission tier, timeout, bounded output, correlation ID, audit event,
cancellation path, preview/dry-run.

Fail-closed guarantees (enforced here + tests):
  * unknown tool / malformed args / out-of-scope -> DENIED, nothing runs;
  * argv tools run via an explicit argv list only -- shell strings are
    forbidden by construction (no shell=True path exists in this module);
  * DESTRUCTIVE tier always requires an explicit user-issued confirmation id
    (the spoken yes/no round trip); CONFIRMATION_REQUIRED is returned first;
  * model/automation sources can never carry or consume approvals;
  * cancellation is idempotent; a cancelled child group is SIGKILLed —
    side-effect-capable callable tools run inside a killable spawn-isolated
    child (own process group via setsid, no forking of a threaded process),
    so a cancelled/timed-out tool can never create a later side effect;
    READ_ONLY callable tools run in-process on the fast path (no side effects
    by declaration, still authorized/audited/redacted, typed failures);
  * audit events carry redacted metadata + correlation ids only.
"""
from __future__ import annotations

import json
import multiprocessing
import os
import re
import shlex
import signal
import threading
import time
import uuid
from pathlib import Path

import cloudpickle
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from ._broker_worker import broker_child_main
from .capabilities import (
    ActionRequest, CapabilityError, CapabilityRegistry, CapabilitySpec,
    ConfirmationDenied, ConfirmationRecord, ConfirmationStore, Decision,
    PolicyEngine, Tier)
from .logging_setup import log_event
from .redaction import redact_text
from .sandbox import run_tool

DENY_DESTRUCTIVE_SCREEN = "DESTRUCTIVE_SCREEN"
DENY_CONFIRM = "CONFIRMATION_REQUIRED"

# Shell metacharacters that must never reach a broker child. Template scripts
# are split with shlex into argv; anything needing real shell syntax must live
# in a repository script instead of an inline template string.
_SHELL_META = re.compile(r"[;&|><`$]")

_MAX_ARG_KEYS = 8
_MAX_STR_ARG = 2000

# Confirmation is a broker-level control, never a capability argument. A request
# body may carry the dashboard's settle flags (``confirm`` / ``confirmation_id``)
# alongside the real args; drop them here so (a) a strict capability arg
# validator never rejects the forwarded body and (b) the confirmation
# args-digest binds only the real inputs. The durable id still travels through
# the dedicated ``confirmation_id`` parameter — a boolean is never authority.
_CONFIRMATION_ARG_KEYS = frozenset({"confirm", "confirmation_id"})


def _strip_confirmation_args(args: Any) -> Any:
    if not isinstance(args, dict):
        return args
    return {k: v for k, v in args.items() if k not in _CONFIRMATION_ARG_KEYS}


def _scalar_validator(allowed_keys: Optional[frozenset] = None):
    """Typed input schema: scalar values, bounded count/size, optional keys."""
    def _check(args: Dict[str, Any]) -> None:
        if len(args) > _MAX_ARG_KEYS:
            raise ValueError(f"too many args ({len(args)})")
        for k, v in args.items():
            if not isinstance(k, str) or not k:
                raise ValueError("arg keys must be non-empty strings")
            if allowed_keys is not None and k not in allowed_keys:
                raise ValueError(f"undeclared arg: {k}")
            if isinstance(v, str):
                if len(v) > _MAX_STR_ARG:
                    raise ValueError(f"arg {k} exceeds {_MAX_STR_ARG} chars")
            elif not isinstance(v, (int, float, bool)):
                raise ValueError(f"arg {k} must be scalar")
    return _check


@dataclass
class ToolBinding:
    """One trusted tool: spec + side-effect declaration + executor."""
    spec: CapabilitySpec
    side_effects: str
    fn: Optional[Callable[[Dict[str, Any]], str]] = None      # in-process tool
    argv_builder: Optional[Callable[[Dict[str, Any]], list]] = None
    preview: Optional[Callable[[Dict[str, Any]], str]] = None
    # Per-binding output cap for the READ_ONLY in-process fast path. Local
    # host actions keep the small default; the bounded web tools declare a
    # larger cap so their already-redacted result is not silently truncated.
    output_cap: int = _MAX_STR_ARG


@dataclass
class ExecResult:
    ok: bool = False
    output: str = ""
    error: str = ""
    rc: Optional[int] = None
    timed_out: bool = False
    truncated: bool = False
    cancelled: bool = False
    denied: str = ""              # denial code when nothing executed
    dry_run: bool = False
    correlation_id: str = ""
    duration_ms: float = 0.0


@dataclass
class _ActiveRun:
    cancel_event: threading.Event = field(default_factory=threading.Event)
    pid: Optional[int] = None          # killable child (callable tools)

    def request_cancel(self) -> bool:
        first = not self.cancel_event.is_set()
        self.cancel_event.set()
        return first


def _kill_tree(pid: Optional[int]) -> None:
    """SIGKILL a whole process group (child + its spawned grandchildren)."""
    if not pid:
        return
    try:
        os.killpg(pid, signal.SIGKILL)
        return
    except Exception:
        pass
    try:
        os.kill(pid, signal.SIGKILL)
    except Exception:
        pass


def estop_engaged(cfg: Any = None) -> bool:
    """Fail-closed emergency-stop probe over the single shared power latch.

    Thin, stable alias over :func:`plugins.estop_engaged` (which reads the one
    durable ``PowerControlStore`` the broker, dashboard, voice, and runner paths
    share). Any read error reports ENGAGED so a path can never fail open when
    the latch cannot be read. Kept here so every dispatch path has one import
    target for the e-stop authority; there is no second latch.
    """
    try:
        from .plugins import estop_engaged as _engaged

        return bool(_engaged(cfg))
    except Exception:
        return True


def confirmation_store_for(cfg: Any = None,
                           *, default_ttl_s: float = 120.0) -> Any:
    """The ONE confirmation authority for a broker/worker engine (A7).

    Authoritative store: :class:`voicebridge.approvals.ApprovalJournal`, the
    durable shared human-confirmation journal at ``<state>/approvals.jsonl``
    (see :class:`voicebridge.approvals.ApprovalJournal` and
    ``docs/archive/2026-09-21/SECURITY_AUDIT_WAVE4.md`` G5). The broker/worker engine no longer
    keeps a second writable store in ``actions.json``: the returned adapter
    (:class:`_JournalBackedConfirmationStore`) presents the journal through the
    :class:`~voicebridge.capabilities.ConfirmationStore` contract, so a
    confirmation minted by any broker, worker guard, or the human-facing
    dashboard/voice/CLI/plugin surfaces is one and the same durable record and
    is consumable by all of them.

    The adapter still carries the ``ActionStore`` handle (``_pstore``) for
    compatibility with callers that introspect it (e.g. the fork engine key)
    but writes confirmations only to the journal. Pre-existing ``actions.json``
    confirmations (the ``ActionRunner`` action flow) remain readable through the
    inherited fallback.

    Falls back to the in-memory :class:`ConfirmationStore` only when no state
    tree can be resolved (hermetic tests, degraded config); the fallback never
    creates a second writable store. A build error is treated as "no durable
    store" rather than crashing a safety boundary, and is fail-safe because the
    in-memory store still enforces single-use/expiry/digest binding.

    Adapters (dispatch guard, fork-forward guard) MUST use this helper so there
    is exactly one writable confirmation authority per state tree.
    """
    if cfg is not None:
        try:
            from .actions import actions_store
            from .approvals import ApprovalJournal

            journal = ApprovalJournal(
                Path(cfg.state_dir()) / "approvals.jsonl",
                default_ttl_s=default_ttl_s)
            return _ephemeral_store_class()(
                actions_store(cfg), journal=journal,
                default_ttl_s=default_ttl_s)
        except Exception:  # noqa: BLE001 - fall back, never fail open widened
            pass
    return ConfirmationStore(default_ttl_s=default_ttl_s)


_EPHEMERAL_STORE_CLS: Optional[type] = None


def _ephemeral_store_class() -> type:
    """Build (once) the journal-backed durable store type.

    Defined lazily because :mod:`voicebridge.actions` imports this module.
    """
    global _EPHEMERAL_STORE_CLS
    if _EPHEMERAL_STORE_CLS is None:
        from .actions import PersistentConfirmationStore

        class _JournalBackedConfirmationStore(PersistentConfirmationStore):
            """ConfirmationStore contract over the shared ApprovalJournal (A7).

            The journal is the single durable confirmation authority; this
            adapter binds its human approvals to the broker's capability + exact
            argument digest so a confirmation minted by one broker/request is
            consumable by the next with the full single-use / expiry / target
            contract intact. ``_pstore`` is retained for introspection only and
            receives no confirmations.
            """

            def __init__(self, store: Any, *, journal: Any,
                         default_ttl_s: float = 300.0) -> None:
                super().__init__(store, default_ttl_s=default_ttl_s)
                # Reentrant: consume() holds the lock while delegating to the
                # inherited in-memory fallback (which also takes the lock).
                self._lock = threading.RLock()
                self._journal = journal

            # -- journal <-> ConfirmationRecord mapping -----------------------
            @staticmethod
            def _digest(args: Dict[str, Any]) -> str:
                from .capabilities import _args_digest

                return _args_digest(dict(args or {}))

            def _record(self, item: Any, *, consumed: bool = False
                        ) -> ConfirmationRecord:
                meta = item.meta if isinstance(item.meta, dict) else {}
                return ConfirmationRecord(
                    id=item.id, capability=item.capability,
                    args_digest=str(meta.get("args_digest", "")),
                    issued_at=item.created_at, expires_at=item.expires_at,
                    issued_by=str(meta.get("issued_by", "user")),
                    consumed=consumed, consumed_at=item.settled_at)

            def issue(self, capability: str, args: Dict[str, Any], *,
                      issued_by: str,
                      ttl_s: Optional[float] = None) -> ConfirmationRecord:
                ConfirmationStore._require_human(issued_by)
                ttl = float(ttl_s) if ttl_s is not None else self._ttl
                if not (1.0 <= ttl <= 86400):
                    raise ValueError("ttl_s outside [1, 86400]")
                with self._lock:
                    item = self._journal.create(
                        capability,
                        f"capability confirmation for {capability}"[:300],
                        ttl_s=ttl, args_digest=self._digest(args),
                        issued_by=issued_by)
                return self._record(item)

            def _settle(self, confirmation_id: str, capability: str,
                        args: Dict[str, Any], *,
                        consume: bool) -> ConfirmationRecord:
                from .approvals import (ABORTED, COMPLETE, DENIED, EXECUTING,
                                        IN_PROGRESS)

                item = self._journal.get(confirmation_id)
                if item is None:
                    raise ConfirmationDenied("UNKNOWN_CONFIRMATION_ID")
                meta = item.meta if isinstance(item.meta, dict) else {}
                ConfirmationStore._require_human(
                    str(meta.get("issued_by", "user")))
                if item.state in (COMPLETE, ABORTED, DENIED):
                    raise ConfirmationDenied("REPLAYED_CONFIRMATION")
                if item.capability != capability:
                    raise ConfirmationDenied("CAPABILITY_MISMATCH")
                if str(meta.get("args_digest", "")) != self._digest(args):
                    raise ConfirmationDenied("TARGET_MISMATCH")
                now = self._clock()
                if item.state == IN_PROGRESS and item.expires_at <= now:
                    self._journal.abort(item.id)
                    raise ConfirmationDenied("EXPIRED_CONFIRMATION")
                if item.state == EXECUTING and item.expires_at <= now:
                    raise ConfirmationDenied("EXPIRED_CONFIRMATION")
                if consume:
                    if item.state == IN_PROGRESS:
                        item = self._journal.respond(item.id, "approve")
                    if item.state == EXECUTING:
                        item = self._journal.finish(item.id)
                return self._record(item, consumed=consume)

            def consume(self, confirmation_id: str, capability: str,
                        args: Dict[str, Any]) -> ConfirmationRecord:
                with self._lock:
                    try:
                        return self._settle(confirmation_id, capability, args,
                                            consume=True)
                    except ConfirmationDenied as exc:
                        if exc.code != "UNKNOWN_CONFIRMATION_ID":
                            raise
                        # Pre-existing actions.json confirmation (ActionRunner).
                        return super().consume(confirmation_id, capability, args)

            def verify(self, confirmation_id: str, capability: str,
                       args: Dict[str, Any]) -> ConfirmationRecord:
                with self._lock:
                    try:
                        return self._settle(confirmation_id, capability, args,
                                            consume=False)
                    except ConfirmationDenied as exc:
                        if exc.code != "UNKNOWN_CONFIRMATION_ID":
                            raise
                        return super().verify(confirmation_id, capability, args)

        class _EphemeralCapableStore(_JournalBackedConfirmationStore):
            """Journal-backed store that also supports an in-process ephemeral
            mint.

            The dispatch guard issues and consumes one internal confirmation
            inside a single call, so it uses :meth:`issue_ephemeral` to stay
            off disk (no second store, no cross-process visibility, identical
            single-use/expiry/digest contract). Presented confirmations still
            use the inherited durable :meth:`issue` (the journal).
            """

            def __init__(self, store: Any, **kwargs: Any) -> None:
                super().__init__(store, **kwargs)
                self._ephemeral: set = set()

            def issue_ephemeral(self, capability: str,
                                args: Dict[str, Any], *,
                                issued_by: str,
                                ttl_s: Optional[float] = None) -> Any:
                rec = ConfirmationStore.issue(
                    self, capability, args, issued_by=issued_by, ttl_s=ttl_s)
                with self._lock:
                    self._ephemeral.add(rec.id)
                return rec

            def consume(self, confirmation_id: str, capability: str,
                        args: Dict[str, Any]) -> Any:
                with self._lock:
                    if confirmation_id in self._records \
                            or confirmation_id in self._ephemeral:
                        rec = ConfirmationStore.consume(
                            self, confirmation_id, capability, args)
                        ephemeral = rec.id in self._ephemeral
                        self._ephemeral.discard(rec.id)
                        if not ephemeral:
                            self._pstore.save_confirmation(rec)
                        return rec
                    return super().consume(confirmation_id, capability, args)

        _EPHEMERAL_STORE_CLS = _EphemeralCapableStore
    return _EPHEMERAL_STORE_CLS


class CapabilityBroker:
    """Decides, then executes trusted host tools; never falls back to shell."""

    def __init__(self, default_ttl_s: float = 120.0,
                 power_control: Optional[Any] = None,
                 cfg: Optional[Any] = None):
        self.registry = CapabilityRegistry()
        self.engine = PolicyEngine(
            registry=self.registry,
            confirmations=confirmation_store_for(
                cfg, default_ttl_s=default_ttl_s))
        self._bindings: Dict[str, ToolBinding] = {}
        self._active: Dict[str, _ActiveRun] = {}
        # Correlation ids cancelled BEFORE their executor registered an active
        # run. A cancel can only be issued once a running receipt exists, but
        # the executor registers `_active` a few calls later; without this
        # marker a cancel landing in that window would be reported as settled
        # while the child still ran to completion (a cancelled action that
        # still executes). The marker is consumed by `execute` (or by the
        # executor registration) for that correlation id, and bounded so an
        # id that never executes cannot grow the set without limit.
        self._cancelled: set = set()
        self._cancelled_cap = 1024
        self._lock = threading.Lock()
        # Packet 8O: optional shared PowerControl. When present, every execute
        # checks emergency stop / global disable / per-adapter circuit breaker,
        # registers the run for cancellation (track/untrack), and records
        # failures so a circuit can trip. All channels that share this broker
        # observe the same control state.
        self._power = power_control
        # Workstream F seam: the config that locates the canonical autonomy
        # store. When armed, execute consults the owner-authored scoped-autonomy
        # authority before any per-action confirmation (see _autonomy_decision).
        # It is an explicit, opt-in seam so hermetic broker tests never read a
        # developer's real state tree; callers arm it by passing `cfg`.
        self._cfg = cfg

    def _power_denied(self, capability: str, spec: Optional[CapabilitySpec]) \
            -> Optional[str]:
        """Return a denial code when the shared power control forbids the run.

        Fail-closed: any error while reading the single shared latch denies the
        run with POWER_UNAVAILABLE rather than letting it proceed unchecked.
        """
        if self._power is None:
            return None
        try:
            # Refresh the cross-process latch (no-op when the control has no
            # durable store) so an emergency stop / disable set by another
            # process is observed before every real execution.
            self._power.refresh()
            state = self._power.state
            if state.get("emergency_stop"):
                return "EMERGENCY_STOP"
            if state.get("global_disable"):
                return "POWER_DISABLED"
            adapter = str(getattr(spec, "adapter", "power") or "power")
            if self._power.breaker_state(adapter) == "open":
                return "CIRCUIT_OPEN"
        except Exception as exc:  # noqa: BLE001 - safety boundary fails closed
            log_event("broker.power.probe_error", capability=capability,
                      error=str(exc)[:160])
            return "POWER_UNAVAILABLE"
        return None

    def _autonomy_decision(self, capability: str, target: str, *,
                           owner: Optional[str], session: Optional[str],
                           device: Optional[str], source: str) -> Optional[Any]:
        """Consult the one owner-authored scoped-autonomy authority (read-only).

        Returns ``None`` when the seam is unarmed or when no ACTIVE profile
        applies to this request binding, so the caller keeps its existing
        per-action confirmation/grant behavior unchanged. Otherwise returns an
        audited :class:`voicebridge.autonomy_ops.OpDecision`: an in-scope profile
        allows (pre-authorized); out-of-scope/target/budget/e-stop is a typed
        refusal. Request data is only containment-checked and can never widen
        the profile. Laid out lazily to avoid an import cycle with autonomy_ops
        (which reads this module's e-stop probe).
        """
        try:
            from . import autonomy_ops
            return autonomy_ops.resolve_operation(
                self._cfg, capability, target or "", owner=owner,
                session=session, device=device, source=source)
        except Exception as exc:  # noqa: BLE001 - never crash a dispatch
            log_event("broker.autonomy.probe_error", capability=capability,
                      error=str(exc)[:160])
            return None

    # ------------------------------------------------------------ registration
    def register(self, binding: ToolBinding) -> CapabilitySpec:
        if binding.fn is None and binding.argv_builder is None:
            raise CapabilityError(f"{binding.spec.name}: needs fn or argv_builder")
        self.registry.register(binding.spec)
        self._bindings[binding.spec.name] = binding
        return binding.spec

    def get(self, name: str) -> Optional[ToolBinding]:
        return self._bindings.get(name)

    def names(self) -> tuple:
        return tuple(sorted(self._bindings))

    # --------------------------------------------------------------- approvals
    def issue_confirmation(self, capability: str, args: Dict[str, Any], *,
                           issued_by: str, ttl_s: float = 120.0):
        """User-path issuance; the store itself refuses issuer='model'."""
        return self.engine.confirmations.issue(
            capability, _strip_confirmation_args(dict(args)),
            issued_by=issued_by, ttl_s=ttl_s)

    # ------------------------------------------------------------- cancellation
    def _remember_cancelled(self, correlation_id: str) -> None:
        """Record a cancel for a correlation id that may not be active yet."""
        self._cancelled.add(correlation_id)
        while len(self._cancelled) > self._cancelled_cap:
            self._cancelled.pop()

    def _consume_cancelled(self, correlation_id: str) -> bool:
        """True (and clears the marker) when this id was cancelled early."""
        with self._lock:
            if correlation_id in self._cancelled:
                self._cancelled.discard(correlation_id)
                return True
            return False

    def _activate(self, cid: str, active: "_ActiveRun") -> None:
        """Register an active run, honouring an early cancel marker.

        The cancel may have arrived after the execute pre-check but before the
        executor registered ``active``; in that case set the run's cancel event
        now so the spawn/poll loop kills the child immediately.
        """
        with self._lock:
            if cid in self._cancelled:
                self._cancelled.discard(cid)
                active.cancel_event.set()
            self._active[cid] = active

    def cancel(self, correlation_id: str) -> bool:
        """Idempotent cooperative cancel; True only for the first request.

        For callable tools the spawn-isolated child group is SIGKILLed
        immediately;
        for argv tools the sandbox poll loop performs the group kill.
        """
        with self._lock:
            active = self._active.get(correlation_id)
            already = correlation_id in self._cancelled
            if active is not None:
                first = active.request_cancel()
            else:
                first = False
            if not already:
                self._remember_cancelled(correlation_id)
        if active is not None:
            _kill_tree(active.pid)
        return first

    # ------------------------------------------------------------------ execute
    def execute(self, capability: str, args: Dict[str, Any], *, source: str,
                correlation_id: Optional[str] = None,
                confirmation_id: Optional[str] = None,
                dry_run: bool = False,
                timeout_s: Optional[float] = None,
                max_output: int = 200_000,
                utterance: str = "",
                target: str = "",
                owner: Optional[str] = None,
                session: Optional[str] = None,
                device: Optional[str] = None) -> ExecResult:
        if source not in ("user", "automation", "model"):
            raise ValueError(f"invalid dispatch source: {source!r}")
        # A dashboard/mutation body may carry the settle flags alongside the
        # real args; strip them so the capability validator and confirmation
        # args-digest see only the declared inputs.
        args = _strip_confirmation_args(args)
        cid = correlation_id or uuid.uuid4().hex[:16]
        kwargs_utterance = utterance
        t0 = time.perf_counter()

        def _res(**kw) -> ExecResult:
            kw.setdefault("correlation_id", cid)
            kw["duration_ms"] = round((time.perf_counter() - t0) * 1000, 3)
            return ExecResult(**kw)

        binding = self._bindings.get(capability)
        spec = binding.spec if binding else None
        # An early cancel (issued after the running receipt was written but
        # before this executor registered) must stop the run before any
        # decision/execution, not merely mark the receipt settled.
        if self._consume_cancelled(cid):
            self._audit("denied", capability, spec, source, cid,
                        Decision(False, "CANCELLED", capability))
            return _res(ok=False, cancelled=True,
                        error="cancelled before execution")
        # Packet 8O: the shared power control is checked BEFORE any decision or
        # execution — emergency stop / global disable / circuit breaker deny the
        # run across every channel that shares this broker.
        power_denied = self._power_denied(capability, spec)
        if power_denied is not None:
            self._audit("denied", capability, spec, source, cid,
                        Decision(False, power_denied, capability))
            return _res(denied=power_denied)
        # Packet 4D destructive-intent tripwire (last-resort layer):
        # if the originating utterance or the capability/args combination
        # carries destruction intent while the tool is NOT a confirmed
        # DESTRUCTIVE-tier capability, fail closed.
        from .safety.destructive_screen import screen_destructive
        _screen_text = " ".join(filter(None, [
            kwargs_utterance, capability, json.dumps(args, default=str)]))
        if spec is not None and spec.tier is not Tier.DESTRUCTIVE \
                and screen_destructive(_screen_text).matched:
            d = Decision(False, DENY_DESTRUCTIVE_SCREEN, capability)
            self._audit("denied", capability, spec, source, cid, d)
            return _res(denied=d.code)
        # Workstream F: an owner-authored scoped-autonomy profile is the ONLY
        # authority that may replace per-action confirmation. When one is armed
        # for this broker and an ACTIVE profile matches the request binding, the
        # profile decides — allow (pre-authorized, no ad-hoc confirmation) or a
        # typed refusal carrying the autonomy code. With no applicable profile
        # the existing confirmation/grant flow below is unchanged. e-stop and
        # hard-deny capability classes are enforced inside autonomy_ops before
        # any allow, so a model/message can never widen its own scope.
        autonomy_decision = None
        if spec is not None and self._cfg is not None:
            autonomy_decision = self._autonomy_decision(
                capability, target, owner=owner, session=session,
                device=device, source=source)
        if autonomy_decision is not None and not autonomy_decision.allowed:
            d = Decision(False, autonomy_decision.code, capability,
                         detail=autonomy_decision.reason)
            self._audit("denied", capability, spec, source, cid, d)
            return _res(denied=d.code)
        if autonomy_decision is not None and autonomy_decision.allowed:
            # Owner pre-authorized: validate the declared input schema exactly
            # as the policy engine would, but issue/consume no ad-hoc
            # confirmation because the profile already granted this scope.
            if spec.arg_validator is not None:
                try:
                    spec.arg_validator(dict(args))
                except Exception as exc:
                    d = Decision(False, "MALFORMED_INPUT", capability,
                                 detail=f"validator: {exc}")
                    self._audit("denied", capability, spec, source, cid, d)
                    return _res(denied=d.code)
            d = Decision(True, "ALLOWED", capability,
                         detail=f"autonomy:{autonomy_decision.profile_id}")
        else:
            eff_conf = confirmation_id
            if spec is not None and spec.needs_confirmation and not eff_conf \
                    and source == "user":
                if spec.tier is Tier.DESTRUCTIVE:
                    # Two-step spoken flow: deny first so the caller can ask.
                    d = Decision(False, DENY_CONFIRM, capability)
                    self._audit("denied", capability, spec, source, cid, d)
                    return _res(denied=d.code)
                rec = self.issue_confirmation(capability, args,
                                              issued_by="user", ttl_s=120)
                eff_conf = rec.id
            # Non-user sources fall through: policy refuses them carrying ids.
            req = ActionRequest(capability=capability, args=dict(args),
                                source=source, confirmation_id=eff_conf,
                                correlation_id=cid)
            d = self.engine.decide(req)
            if not d.allowed:
                self._audit("denied", capability, spec, source, cid, d)
                return _res(denied=d.code)

        if dry_run:
            try:
                text = f"DRY-RUN {capability} v{spec.version} tier={spec.tier.value}"
                text += f"\nside_effects: {binding.side_effects}"
                if binding.argv_builder is not None:
                    text += "\nargv: " + repr(binding.argv_builder(args))
                elif binding.preview is not None:
                    text += "\npreview: " + str(binding.preview(args))
            except Exception as exc:
                return _res(ok=False, denied="MALFORMED_INPUT",
                            error=str(exc)[:300])
            self._audit("dry_run", capability, spec, source, cid, d)
            return _res(ok=True, output=text[:2000], dry_run=True)

        self._audit("start", capability, spec, source, cid, d)
        adapter = str(getattr(spec, "adapter", "power") or "power")
        tracked = False
        if self._power is not None:
            # Register the real run for emergency-stop cancellation and bounded
            # concurrency. The cancel callback SIGKILLs the child group through
            # the broker's own cancel path. If no concurrency slot is available
            # the run FAILS CLOSED (never executes outside the limit).
            if not self._power.acquire(adapter):
                self._audit("denied", capability, spec, source, cid,
                            Decision(False, "CONCURRENCY_LIMIT", capability))
                return _res(denied="CONCURRENCY_LIMIT")
            try:
                self._power.track(cid, lambda _cid=cid: self.cancel(_cid))
                tracked = True
            except Exception:
                self._power.release(adapter)
                raise
        try:
            if binding.fn is not None:
                if spec.tier is Tier.READ_ONLY:
                    # Read-only fast path: no per-call process spawn. The
                    # callable is declared side-effect-free; it runs in the
                    # broker process but is still fully authorized, audited and
                    # redacted (the audit phases above/below still run) and
                    # exceptions become typed failures. Side-effect-capable
                    # tools (mutating/reversible/destructive) keep the killable
                    # spawn-isolated child below.
                    res = self._run_callable_inline(binding, args, cid)
                else:
                    res = self._run_callable(binding, args, cid,
                                             timeout_s or spec.timeout_s)
            else:
                argv = binding.argv_builder(dict(args))
                res = self._run_argv(argv, cid,
                                     min(timeout_s or spec.timeout_s,
                                         spec.timeout_s), max_output)
        except Exception as exc:
            self._audit("error", capability, spec, source, cid, d,
                        error=str(exc)[:200])
            res = _res(ok=False, error=str(exc)[:500])
        finally:
            # EVERY path (normal, timeout, cancellation, exception) cleans up.
            with self._lock:
                self._active.pop(cid, None)
            if tracked:
                try:
                    self._power.untrack(cid)
                finally:
                    self._power.release(adapter)
        if self._power is not None:
            if res.cancelled:
                pass  # cancellation is not a circuit failure
            elif not res.ok or res.denied or res.error:
                self._power.record_failure(adapter)
            else:
                self._power.record_success(adapter)
        res.correlation_id = cid
        res.duration_ms = round((time.perf_counter() - t0) * 1000, 3)
        self._audit("finish", capability, spec, source, cid, d,
                    ok=res.ok, rc=res.rc, timed_out=res.timed_out,
                    truncated=res.truncated, cancelled=res.cancelled,
                    out_len=len(res.output))
        return res

    # ---------------------------------------------------------------- executors
    def _run_callable_inline(self, binding: ToolBinding, args: Dict[str, Any],
                             cid: str) -> ExecResult:
        """In-process fast path for READ_ONLY callable tools (no process spawn).

        The callable is declared side-effect-free (Tier.READ_ONLY) and runs
        directly in the broker process — the documented read-only fast path
        ("pure in-process decision"). It is still fully authorized (the
        decide/audit phases in ``execute`` run), the result is redacted, and an
        exception becomes a typed ``ExecResult`` failure (never a crash). There
        is no killable timeout: a hung read-only tool is a trust boundary, not a
        data-integrity risk (it cannot create side effects by declaration).
        ``execute``'s ``finally`` always removes the run from the active
        registry.
        """
        active = _ActiveRun()
        self._activate(cid, active)
        try:
            out = binding.fn(dict(args))
        except BaseException as exc:
            return ExecResult(ok=False, error=str(exc)[:500], timed_out=False)
        if active.cancel_event.is_set():
            # Cooperative cancellation for the in-process read-only path: an
            # emergency stop / cancel requested while the (side-effect-free)
            # callable ran is reported as cancelled, never as success.
            return ExecResult(ok=False, cancelled=True,
                              error="cancelled during execution")
        return ExecResult(ok=True,
                          output=redact_text(
                              str(out))[:max(1, int(binding.output_cap))])

    def _run_callable(self, binding: ToolBinding, args: Dict[str, Any],
                      cid: str, timeout_s: float) -> ExecResult:
        """Run an in-process trusted tool inside a KILLABLE isolated child.

        A side-effecting callable must never outlive its budget: the child runs
        in a fresh process forked from the single-threaded forkserver and calls
        setsid() so it leads its own process group; timeout or cancellation
        SIGKILLs that group (child + spawned grandchildren), so a cancelled
        tool can create no later side effect. The result is one bounded message
        over a pipe; redaction stays in the parent. The callable is serialized
        with cloudpickle so arbitrary in-process closures/lambdas are
        supported; forkserver avoids forking a multi-threaded process (CPython
        3.12+ emits a DeprecationWarning for that), so no warning filter is
        needed.
        """
        active = _ActiveRun()
        # forkserver: a single pre-started server process (fresh interpreter,
        # single-threaded) forks each worker child, so there is no per-call
        # interpreter startup AND no fork from a multi-threaded process
        # (CPython 3.12+ deprecates that, which would emit a DeprecationWarning
        # without a filter). The worker child is still a fresh process that
        # setsid()s into its own group, so cancel/timeout SIGKILL isolation is
        # identical to the former fork design.
        ctx = multiprocessing.get_context("forkserver")
        parent_conn, child_conn = ctx.Pipe(duplex=False)
        try:
            payload = cloudpickle.dumps((binding.fn, dict(args)))
        except Exception as exc:
            return ExecResult(ok=False, denied="SERIALIZATION_FAILED",
                              error=f"callable not spawn-safe: {str(exc)[:200]}")

        proc = None
        self._activate(cid, active)
        proc = ctx.Process(target=broker_child_main,
                           args=(payload, child_conn), daemon=False)
        proc.start()
        active.pid = proc.pid
        try:
            child_conn.close()          # parent's copy of the write end
        except Exception:
            pass

        deadline = time.monotonic() + max(1.0, float(timeout_s))
        msg: Optional[Dict[str, Any]] = None
        timed_out = cancelled = False

        def _classify() -> None:
            """Honest outcome labels once the child is gone for any reason."""
            nonlocal cancelled, timed_out
            if msg is None:
                if active.cancel_event.is_set():
                    cancelled = True
                elif time.monotonic() >= deadline:
                    timed_out = True

        while True:
            try:
                if parent_conn.poll(0.02):
                    try:
                        msg = parent_conn.recv()
                    except EOFError:
                        msg = None
                        _classify()
                    break
                if not cancelled and not timed_out \
                        and active.cancel_event.is_set():
                    cancelled = True
                    _kill_tree(active.pid)
                elif not cancelled and not timed_out \
                        and time.monotonic() >= deadline:
                    timed_out = True
                    _kill_tree(active.pid)
                if cancelled or timed_out:
                    # One short grace poll: a result that landed in the same
                    # instant as the kill is still reported honestly.
                    try:
                        if parent_conn.poll(0.3):
                            msg = parent_conn.recv()
                    except EOFError:
                        msg = None
                    break
            except EOFError:
                _classify()
                break
        proc.join(timeout=5)
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=2)
        rc = proc.exitcode
        _classify()
        try:
            parent_conn.close()
        except Exception:
            pass

        if msg is not None and msg.get("ok"):
            return ExecResult(ok=True, output=redact_text(
                str(msg.get("out", "")))[:_MAX_STR_ARG], rc=rc,
                cancelled=cancelled)
        err = str(msg.get("err", ""))[:500] if msg is not None else (
            f"tool {'cancelled' if cancelled else 'terminated'} "
            f"(exit {rc})")
        return ExecResult(ok=False, rc=rc, cancelled=cancelled,
                          timed_out=timed_out, error=err)

    def _run_argv(self, argv: list, cid: str, timeout_s: float,
                  max_output: int) -> ExecResult:
        if not argv or not all(isinstance(a, str) for a in argv):
            return ExecResult(denied="MALFORMED_INPUT",
                              error="argv must be a non-empty list of strings")
        active = _ActiveRun()
        self._activate(cid, active)
        r = run_tool(argv, timeout_s=max(1.0, float(timeout_s)),
                     max_output=max_output, shell=False,
                     cancel_check=active.cancel_event.is_set)
        return ExecResult(ok=r.ok, output=r.stdout, error=r.stderr,
                          rc=r.rc, timed_out=r.timed_out,
                          truncated=r.truncated, cancelled=r.cancelled)

    # -------------------------------------------------------------------- audit
    @staticmethod
    def _audit(phase: str, capability: str, spec: Optional[CapabilitySpec],
               source: str, cid: str, d: Decision, **extra: Any) -> None:
        payload = {"phase": phase, "capability": capability,
                   "version": spec.version if spec else "",
                   "tier": spec.tier.value if spec else "",
                   "source": source, "correlation_id": cid,
                   "decision": d.code}
        payload.update(extra)
        log_event("broker.exec." + phase, **payload)


# ------------------------------------------------------- host tool registration

_ACTION_TIERS = {
    # read_only: inspect/status only
    "system_stats": Tier.READ_ONLY, "time_date": Tier.READ_ONLY,
    "find_file": Tier.READ_ONLY, "clipboard": Tier.READ_ONLY,
    # mutating: potential data/app loss
    "window_close": Tier.MUTATING,
    # destructive: irreversible deletion
    "empty_trash": Tier.DESTRUCTIVE,
}

_ACTION_EFFECTS = {
    "system_stats": "reads CPU/RAM/disk stats",
    "time_date": "none (reads clock)",
    "find_file": "reads filesystem index",
    "clipboard": "reads clipboard contents",
    "window_close": "closes the active window (unsaved work may be lost)",
    "empty_trash": "permanently empties the trash",
}


def build_host_broker(power_control: Optional[Any] = None,
                      cfg: Optional[Any] = None) -> CapabilityBroker:
    """Register every whitelisted local action behind the registry.

    ``cfg`` arms the Workstream F autonomy seam: when supplied, every execute
    consults the canonical owner-authored autonomy store (read-only) before the
    per-action confirmation flow. Omitting it preserves the prior behavior and
    keeps hermetic tests off any real state tree.
    """
    from .local.actions import ACTIONS

    broker = CapabilityBroker(power_control=power_control, cfg=cfg)
    for name, act in ACTIONS.items():
        if act is None or act.fn is None:
            continue
        tier = _ACTION_TIERS.get(name, Tier.REVERSIBLE)
        effects = _ACTION_EFFECTS.get(
            name, f"{act.desc} (reversible system state)")
        spec = CapabilitySpec(
            name=f"local.{name}", version="1.0.0", tier=tier,
            summary=(act.desc or name)[:200], scopes=("local",),
            timeout_s=30.0, arg_validator=_scalar_validator())
        broker.register(ToolBinding(spec=spec, side_effects=effects, fn=act.fn))
    return broker


_WEB_OUTPUT_CAP = 32_768


def register_web_tools(broker: CapabilityBroker, cfg: Any = None) -> tuple:
    """Register the SSRF-guarded, read-only ``web.*`` tools on ``broker``.

    This makes a planner/model ``web.*`` call a first-class broker capability:
    the orchestrator's ``_agent_tool`` dispatches it through the SAME
    registry/policy/e-stop/confirmation junction (and audit) as ``local.*``,
    instead of calling ``web_tool.run`` directly and returning before the
    broker. The executor is the existing :mod:`voicebridge.web_tool` module, so
    the scheme allowlist, private/metadata-IP block, redirect re-validation,
    size/time caps and redaction are unchanged — only the decision path moves
    onto the shared broker. Returns the capability names registered.
    """
    from . import web_tool as _wt

    registered = []
    for name, tool_spec in _wt.TOOL_SPECS.items():
        def _run(args: Dict[str, Any], _name: str = name) -> str:
            return json.dumps(_wt.run(cfg, _name, dict(args or {})),
                              default=str)

        spec = CapabilitySpec(
            name=name, version="1.0.0", tier=Tier.READ_ONLY,
            summary=str(tool_spec.get("summary", name))[:200],
            scopes=("web",),
            timeout_s=float(_wt.DEFAULT_TIMEOUT_S) + 5.0,
            arg_validator=_scalar_validator(
                frozenset(tool_spec.get("required", ()))))
        broker.register(ToolBinding(
            spec=spec,
            side_effects="read-only bounded web access (GET/query); no local "
                         "writes, no shell, no second listener",
            fn=_run, output_cap=_WEB_OUTPUT_CAP))
        registered.append(name)
    return tuple(registered)


_FILE_PATCH_OUTPUT_CAP = 32_768
_FILE_PATCH_MAX_TEXT = 512 * 1024


def _file_patch_validator(allowed: frozenset):
    """Typed input schema for the confined file-write capabilities.

    Unlike the generic scalar validator this permits the bounded-but-large
    unified-diff ``patch_text`` (up to ``_FILE_PATCH_MAX_TEXT``) while still
    rejecting undeclared keys and non-string values.
    """
    def _check(args: Dict[str, Any]) -> None:
        if not isinstance(args, dict):
            raise ValueError("args must be a mapping")
        extra = set(args) - allowed
        if extra:
            raise ValueError(f"undeclared arg: {sorted(extra)}")
        for key in ("target_path", "requested_by", "project_root"):
            value = args.get(key)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"arg {key} must be a string")
        if "recursive" in args and not isinstance(args.get("recursive"), bool):
            raise ValueError("arg recursive must be a boolean")
        if "patch_text" in args:
            text = args.get("patch_text")
            if not isinstance(text, str):
                raise ValueError("patch_text must be a string")
            if len(text.encode("utf-8", "surrogatepass")) > _FILE_PATCH_MAX_TEXT:
                raise ValueError("patch_text exceeds the bounded patch size")
    return _check


def register_file_patch(broker: CapabilityBroker, cfg: Any = None) -> tuple:
    """Register the confined, reversible file-write capabilities on ``broker``.

    ``local.apply_patch`` and ``local.files_mkdir`` are MUTATING (a real on-disk
    write that must be confirmed and is reversible from a durable backup). The
    dashboard file routes, the planner/model ``apply_patch`` path, the CLI, and
    voice now reach ONE decision point through the same registry, shared
    e-stop/global-disable latch, shared confirmation journal, and audit the
    ``local.*`` actions use. The executors invoke the existing
    :mod:`voicebridge.file_patch` confinement/backup/rollback implementation
    with ``confirmed=True`` because the broker decision — a durable, single-use
    user confirmation — IS the confirmation; a client-supplied boolean never is.

    Returns the capability names registered.
    """
    from . import file_patch as _fp

    def _apply(args: Dict[str, Any]) -> str:
        try:
            receipt = _fp.apply_patch(
                cfg, str(args.get("target_path", "")),
                str(args.get("patch_text", "")),
                requested_by=str(args.get("requested_by", "cli:user")),
                confirmed=True,
                project_root=str(args.get("project_root", "") or ""))
            # The broker child returns one bounded message; drop the (up to
            # 2 KB) redacted patch preview so the receipt envelope can never be
            # truncated mid-JSON by that cap (the write already happened).
            receipt.pop("preview", None)
            return json.dumps({"ok": True, "receipt": receipt}, default=str)
        except _fp.FilePatchError as exc:
            return json.dumps({"ok": False, "code": exc.code,
                               "detail": exc.detail}, default=str)

    def _mkdir(args: Dict[str, Any]) -> str:
        try:
            receipt = _fp.mkdir_path(
                cfg, str(args.get("target_path", "")),
                requested_by=str(args.get("requested_by", "cli:user")),
                confirmed=True,
                project_root=str(args.get("project_root", "") or ""))
            return json.dumps({"ok": True, "receipt": receipt}, default=str)
        except _fp.FilePatchError as exc:
            return json.dumps({"ok": False, "code": exc.code,
                               "detail": exc.detail}, default=str)

    def _delete(args: Dict[str, Any]) -> str:
        try:
            receipt = _fp.delete_path(
                cfg, str(args.get("target_path", "")),
                requested_by=str(args.get("requested_by", "cli:user")),
                confirmed=True,
                recursive=bool(args.get("recursive", False)),
                project_root=str(args.get("project_root", "") or ""))
            return json.dumps({"ok": True, "receipt": receipt}, default=str)
        except _fp.FilePatchError as exc:
            return json.dumps({"ok": False, "code": exc.code,
                               "detail": exc.detail}, default=str)

    specs = (
        (_fp.capability_name(), _apply,
         frozenset({"target_path", "patch_text", "requested_by",
                    "project_root"}),
         "confined atomic file patch; reversible from a durable backup"),
        (_fp.mkdir_capability_name(), _mkdir,
         frozenset({"target_path", "requested_by", "project_root"}),
         "confined directory creation inside an approved project folder"),
        (_fp.delete_capability_name(), _delete,
         frozenset({"target_path", "requested_by", "project_root",
                    "recursive"}),
         "confined reversible file/folder delete archived to the Qubit "
         "state store"),
    )
    registered = []
    for name, fn, allowed, effects in specs:
        spec = CapabilitySpec(
            name=name, version="1.0.0", tier=Tier.MUTATING,
            summary=effects[:200], scopes=("local",), timeout_s=30.0,
            arg_validator=_file_patch_validator(allowed))
        broker.register(ToolBinding(
            spec=spec, side_effects=effects, fn=fn,
            output_cap=_FILE_PATCH_OUTPUT_CAP))
        registered.append(name)
    return tuple(registered)


def script_capability_name(template_name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (template_name or "").lower()).strip("-")
    name = f"script.{slug or 'unnamed'}"
    if len(name) > 64:
        name = name[:64].rstrip("-")
    return name
def register_script_tool(broker: CapabilityBroker, *, template_name: str,
                         script: str, timeout_s: float = 120.0) -> str:
    """Register a user-configured local-script template as an argv tool.

    The script line is tokenized ONCE with shlex into a fixed argv skeleton;
    {question}/{text} placeholders are substituted as whole argv elements.
    Shell metacharacters are rejected fail-closed: a template needing pipes,
    redirects, or command chaining must become a repository script instead.
    """
    cap = script_capability_name(template_name)
    collapsed = " ".join((script or "").split())
    if not collapsed:
        raise CapabilityError(f"{cap}: empty script")
    # Metacharacters OUTSIDE quotes are shell syntax -> forbidden. Quoted ones
    # are inert here (argv mode has no shell), so they may pass as literals.
    unquoted = re.sub(r"'[^']*'|\"[^\"]*\"", "", collapsed)
    if _SHELL_META.search(unquoted):
        raise CapabilityError(f"{cap}: shell metacharacters forbidden "
                              "(use a repository script)")
    skeleton = [os.path.expanduser(t) for t in shlex.split(collapsed)]
    if not skeleton:
        raise CapabilityError(f"{cap}: script parsed to empty argv")

    def _builder(args: Dict[str, Any]) -> list:
        q = str(args.get("question", ""))
        return [t.replace("{question}", q).replace("{text}", q)
                for t in skeleton]

    spec = CapabilitySpec(
        name=cap, version="1.0.0", tier=Tier.MUTATING,
        summary=f"user-configured script template '{template_name}'"[:200],
        scopes=("scripts",), timeout_s=float(timeout_s),
        arg_validator=_scalar_validator(frozenset({"question", "text"})))
    effects = ("runs a user-authored local script (network/filesystem effects "
               "declared by config owner)")
    broker.register(ToolBinding(spec=spec, side_effects=effects,
                                argv_builder=_builder))
    return cap

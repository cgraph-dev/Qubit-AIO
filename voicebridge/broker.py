"""Trusted capability broker (Packet 3A).

Puts existing trusted host tools (whitelisted local actions, user-configured
local-script templates) behind the Packet 1A capability registry and ONE
PolicyEngine shared with the OpenClaw/Hermes WorkerGuard.

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

import cloudpickle
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from ._broker_worker import broker_child_main
from .capabilities import (
    ActionRequest, CapabilityError, CapabilityRegistry, CapabilitySpec,
    ConfirmationStore, Decision, PolicyEngine, Tier)
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


class CapabilityBroker:
    """Decides, then executes trusted host tools; never falls back to shell."""

    def __init__(self, default_ttl_s: float = 120.0):
        self.registry = CapabilityRegistry()
        self.engine = PolicyEngine(
            registry=self.registry,
            confirmations=ConfirmationStore(default_ttl_s=default_ttl_s))
        self._bindings: Dict[str, ToolBinding] = {}
        self._active: Dict[str, _ActiveRun] = {}
        self._lock = threading.Lock()

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
            capability, dict(args), issued_by=issued_by, ttl_s=ttl_s)

    # ------------------------------------------------------------- cancellation
    def cancel(self, correlation_id: str) -> bool:
        """Idempotent cooperative cancel; True only for the first request.

        For callable tools the spawn-isolated child group is SIGKILLed
        immediately;
        for argv tools the sandbox poll loop performs the group kill.
        """
        with self._lock:
            active = self._active.get(correlation_id)
        if active is None:
            return False
        first = active.request_cancel()
        _kill_tree(active.pid)
        return first

    # ------------------------------------------------------------------ execute
    def execute(self, capability: str, args: Dict[str, Any], *, source: str,
                correlation_id: Optional[str] = None,
                confirmation_id: Optional[str] = None,
                dry_run: bool = False,
                timeout_s: Optional[float] = None,
                max_output: int = 200_000,
                utterance: str = "") -> ExecResult:
        if source not in ("user", "automation", "model"):
            raise ValueError(f"invalid dispatch source: {source!r}")
        cid = correlation_id or uuid.uuid4().hex[:16]
        kwargs_utterance = utterance
        t0 = time.perf_counter()

        def _res(**kw) -> ExecResult:
            kw.setdefault("correlation_id", cid)
            kw["duration_ms"] = round((time.perf_counter() - t0) * 1000, 3)
            return ExecResult(**kw)

        binding = self._bindings.get(capability)
        spec = binding.spec if binding else None
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
        with self._lock:
            self._active[cid] = active
        try:
            out = binding.fn(dict(args))
        except BaseException as exc:
            return ExecResult(ok=False, error=str(exc)[:500], timed_out=False)
        return ExecResult(ok=True,
                          output=redact_text(str(out))[:_MAX_STR_ARG])

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
        with self._lock:
            self._active[cid] = active
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
        with self._lock:
            self._active[cid] = active
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


def build_host_broker() -> CapabilityBroker:
    """Register every whitelisted local action behind the registry."""
    from .local.actions import ACTIONS

    broker = CapabilityBroker()
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

"""Temporary deny/confirm adapter for OpenClaw/Hermes worker dispatch (Packet 1B).

Wraps the three legacy choke points (gateway send_message, CLI run_task,
hermes run_task) so every external worker call produces a PolicyEngine
Decision first. Modes (config `safety.worker_dispatch`):
  confirm (default) — user-originated calls are auto-issued a short-lived,
                      single-use confirmation and flow through the store;
  deny              — every worker dispatch is blocked.
Non-user sources (model/automation) are ALWAYS refused by the policy core
(CONFIRMATION_SOURCE_FORBIDDEN) regardless of mode. Bypass coverage is proven
by tests/test_packet1b.py against WIRED_SITES.
"""
from __future__ import annotations

import time

from typing import Any, Callable, Optional

from .logging_setup import log_event
from .redaction import redact_text
from .capabilities import (
    ActionRequest, CapabilityRegistry, CapabilitySpec, ConfirmationDenied,
    ConfirmationStore, PolicyEngine, Tier)


class DispatchBlocked(RuntimeError):
    def __init__(self, code: str, capability: str, detail: str = ""):
        super().__init__(f"{code}: {capability} {detail}".strip())
        self.code = code
        self.capability = capability


WORKER_CAPS = {
    "openclaw.gateway.task": ("openclaw", "send a task through the OpenClaw gateway"),
    "openclaw.cli.task": ("openclaw", "run a task through the OpenClaw CLI"),
    "hermes.task": ("hermes", "run a task through the Hermes CLI"),
    "openclaw.create_session": ("openclaw", "create an OpenClaw task session"),
    "openclaw.abort": ("openclaw", "abort a running OpenClaw task"),
}


def register_worker_caps(registry: CapabilityRegistry) -> None:
    for name, (scope, summary) in WORKER_CAPS.items():
        registry.register(CapabilitySpec(name=name, version="1.0.0",
                                         tier=Tier.MUTATING, summary=summary,
                                         scopes=(scope,), timeout_s=900.0))


def build_worker_engine(mode: str = "confirm") -> tuple:
    if mode not in ("confirm", "deny"):
        raise ValueError("safety.worker_dispatch must be 'confirm' or 'deny'")
    reg = CapabilityRegistry()
    register_worker_caps(reg)
    return PolicyEngine(registry=reg,
                        confirmations=ConfirmationStore(default_ttl_s=120.0)), mode


class WorkerGuard:
    """One guard instance per orchestrator; wraps each worker callable.

    Packet 3A: accepts a shared PolicyEngine (the broker's) so workers and
    host tools share one registry, one confirmation store, and one audit
    surface; without one it builds its own engine exactly as in 1B.
    """

    def __init__(self, cfg: Any, engine: Optional[PolicyEngine] = None):
        raw = cfg.get("safety", "worker_dispatch") if cfg is not None else None
        mode = raw or "confirm"
        if mode not in ("confirm", "deny"):
            raise ValueError("safety.worker_dispatch must be 'confirm' or 'deny'")
        if engine is not None:
            self.engine, self.mode = engine, mode
        else:
            self.engine, self.mode = build_worker_engine(mode)

    def run(self, capability: str, fn: Callable, *args: Any,
            source: str, correlation_id: Optional[str] = None,
            **kwargs: Any):
        """source is REQUIRED and must be the true request origin ("user",
        "automation", "model") — the policy engine refuses non-user sources,
        so callers cannot rubber-stamp model payloads as human input."""
        if source not in ("user", "automation", "model"):
            raise ValueError(f"invalid dispatch source: {source!r}")
        if self.mode == "deny":
            raise DispatchBlocked("WORKER_DISPATCH_DENIED", capability)
        task_repr = next((str(a) for a in args if isinstance(a, str)), "")[:2000]
        req_args = {"task": redact_text(task_repr)}  # secrets stripped before workers/prompts
        try:
            rec = self.engine.confirmations.issue(
                capability, req_args,
                issued_by="user" if source == "user" else source, ttl_s=120)
        except ConfirmationDenied as exc:
            # Uniform failure contract: even issuance-level refusals (model)
            # surface as typed dispatch blocks.
            raise DispatchBlocked(exc.code, capability) from None
        d = self.engine.decide(ActionRequest(
            capability=capability, args=req_args, source=source,
            confirmation_id=rec.id, correlation_id=correlation_id))
        if not d.allowed:
            raise DispatchBlocked(d.code, capability, d.detail)
        # R3: the worker-visible payload is the SANITIZED text, not the raw one
        safe_args = [redact_text(a) if isinstance(a, str) else a for a in args]
        safe_kw = {k: (redact_text(v) if isinstance(v, str) else v)
                   for k, v in kwargs.items()}
        t0 = time.perf_counter()
        log_event("worker.exec.start", capability=capability,
                  correlation_id=correlation_id or "", source=source,
                  task=req_args["task"][:300])
        try:
            result = fn(*safe_args, **safe_kw)
        except Exception as exc:
            log_event("worker.exec.error", capability=capability,
                      correlation_id=correlation_id or "",
                      duration_ms=round((time.perf_counter() - t0) * 1000, 1),
                      error=str(exc)[:200])
            raise
        log_event("worker.exec.finish", capability=capability,
                  correlation_id=correlation_id or "",
                  duration_ms=round((time.perf_counter() - t0) * 1000, 1),
                  result_len=len(str(result)) if result is not None else 0)
        return result

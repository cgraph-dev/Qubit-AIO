"""P4-04 — full capability usage through the one gateway.

Every capability an office agent can use — model, tool, MCP server/tool,
terminal, browser, files, messages, memory and connectors — is reached through
exactly one :class:`~voicebridge.business.tools.ToolGateway`. This module does
not introduce a second execution engine: it is the dispatch layer above the
existing gateway.

Design rules, matching the rest of ``voicebridge.business``:

* **One door.** An adapter is only reachable by calling
  :meth:`CapabilityRuntime.call`, which always invokes ``ToolGateway.invoke``
  with a pinned :class:`~voicebridge.business.policy.CapabilitySnapshot` and the
  capability catalog. There is no public attribute holding an adapter and no
  "call adapter directly" helper.
* **Deny by default.** The runtime adds no authority of its own. A caller must
  pass an explicit :class:`~voicebridge.business.tools.Policy`; the gateway and
  the snapshot still decide. An unknown capability, an ungranted capability, an
  unregistered adapter, an unhealthy capability and a halted runtime are all
  typed refusals, never a silent fallback to another adapter.
* **No wildcard.** Capability ids and categories are checked for glob
  characters here as well as in the catalog.
* **Per-call safety.** Terminal argv is validated by ``sandbox.build_argv``
  (never a shell string), file paths are canonicalised inside the sandbox
  workspace, and an egress URL on a network capability is checked with
  ``sandbox.check_egress`` (deny network by default; private, loopback and
  metadata addresses are blocked even when allowlisted).
* **Budget, rate and approval.** Money budgets and approval binding stay in the
  gateway. A deterministic fixed-window rate limiter lives here because the
  gateway only carries rate hints to the adapter.
* **Bounded, untrusted output.** An adapter's return value is bounded and
  wrapped in an ``{"untrusted": True, ...}`` envelope so no consumer can
  mistake tool content for instructions.
* **Quarantine guard.** An injected guard (``safety.SafetyService``) can refuse
  every call before it reaches the gateway, so quarantining an agent blocks
  capability use as well as new turns.

Idempotency and redaction are the gateway's; this module passes the caller's
idempotency key through unchanged and never persists raw adapter output itself.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, replace
from typing import Any, Callable, Iterable, Mapping

from . import sandbox as sandbox_module
from .errors import BAD_INPUT_CODE, BusinessError
from .policy import (
    CAPABILITY_UNKNOWN_CODE,
    Capability,
    CapabilityCatalog,
    CapabilitySnapshot,
)
from .tools import Policy, ToolGateway, ToolOutcome, ToolRequest
from .util import json_dumps

#: Capability categories this runtime understands. A catalogued capability in
#: another category is reported as unsupported rather than guessed at.
SUPPORTED_CATEGORIES = frozenset(
    {
        "model",
        "tool",
        "mcp",
        "terminal",
        "browser",
        "files",
        "messages",
        "memory",
        "connectors",
    }
)

#: Categories whose payload may carry an egress URL to validate.
_NETWORK_CATEGORIES = frozenset({"model", "mcp", "connectors", "messages", "browser"})

# Typed refusals owned by this layer. Gateway refusals keep their own codes.
CAPABILITY_RUNTIME_POLICY_REQUIRED_CODE = "CAPABILITY_RUNTIME_POLICY_REQUIRED"
CAPABILITY_RUNTIME_UNSUPPORTED_CODE = "CAPABILITY_RUNTIME_UNSUPPORTED"
CAPABILITY_RUNTIME_UNAVAILABLE_CODE = "CAPABILITY_RUNTIME_UNAVAILABLE"
CAPABILITY_RUNTIME_WILDCARD_CODE = "CAPABILITY_RUNTIME_WILDCARD"
CAPABILITY_RUNTIME_RATE_LIMITED_CODE = "CAPABILITY_RUNTIME_RATE_LIMITED"
CAPABILITY_RUNTIME_SANDBOX_REQUIRED_CODE = "CAPABILITY_RUNTIME_SANDBOX_REQUIRED"
CAPABILITY_RUNTIME_HALTED_CODE = "CAPABILITY_RUNTIME_HALTED"
CAPABILITY_RUNTIME_GUARD_REFUSED_CODE = "CAPABILITY_RUNTIME_GUARD_REFUSED"
CAPABILITY_RUNTIME_OUTPUT_TOO_LARGE_CODE = "CAPABILITY_RUNTIME_OUTPUT_TOO_LARGE"

#: Output bounding. A tool result is data, not authority: cap it before it can
#: reach a prompt, a log or an artifact.
MAX_OUTPUT_BYTES = 256 * 1024
MAX_OUTPUT_DEPTH = 12
MAX_OUTPUT_ITEMS = 1000
MAX_OUTPUT_STRING = 64 * 1024

#: A rate-limit window when the caller does not name one.
DEFAULT_RATE_WINDOW_SECONDS = 60.0

#: A bare ``*``/``all``/``any`` is never a capability. The catalog rejects it
#: too; this catches a caller that bypasses catalog construction.
_WILDCARD_TOKENS = frozenset({"*", "all", "any", "ALL", "ANY", "All", "Any"})

Adapter = Callable[[ToolRequest, Capability], Any]
Guard = Callable[[str, str], None]


def _text(value: Any, label: str, *, max_len: int = 300) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BusinessError(BAD_INPUT_CODE, f"{label} must be a non-empty string")
    clean = value.strip()
    if len(clean) > max_len:
        raise BusinessError(
            BAD_INPUT_CODE, f"{label} is longer than {max_len} characters"
        )
    return clean


def _no_wildcard(value: str, label: str) -> str:
    if value in _WILDCARD_TOKENS or "*" in value or "?" in value:
        raise BusinessError(
            CAPABILITY_RUNTIME_WILDCARD_CODE,
            f"{label} {value!r} is a wildcard; capabilities are named exactly",
        )
    return value


def _bounded(value: Any, *, depth: int = 0) -> Any:
    """Return a bounded, JSON-shaped copy of arbitrary adapter output."""
    if depth > MAX_OUTPUT_DEPTH:
        raise BusinessError(
            CAPABILITY_RUNTIME_OUTPUT_TOO_LARGE_CODE,
            f"tool output is nested deeper than {MAX_OUTPUT_DEPTH} levels",
        )
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:MAX_OUTPUT_STRING]
    if isinstance(value, (bytes, bytearray)):
        return bytes(value[:MAX_OUTPUT_STRING]).decode("utf-8", "replace")
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, item in list(value.items())[:MAX_OUTPUT_ITEMS]:
            out[str(key)[:512]] = _bounded(item, depth=depth + 1)
        return out
    if isinstance(value, (list, tuple, set, frozenset)):
        return [
            _bounded(item, depth=depth + 1)
            for item in list(value)[:MAX_OUTPUT_ITEMS]
        ]
    return {
        "__unserializable__": type(value).__name__,
        "repr": repr(value)[:MAX_OUTPUT_STRING],
    }


def _envelope(capability_id: str, raw: Any) -> dict[str, Any]:
    """Bound and label an adapter result as untrusted data."""
    bounded = _bounded(raw)
    body: dict[str, Any] = {
        "untrusted": True,
        "capability": capability_id,
        "data": bounded,
    }
    if isinstance(bounded, Mapping) and "_ledger" in bounded:
        body["_ledger"] = bounded["_ledger"]
    try:
        size = len(json_dumps(body).encode("utf-8"))
    except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
        raise BusinessError(
            CAPABILITY_RUNTIME_OUTPUT_TOO_LARGE_CODE,
            f"tool output cannot be serialised: {type(exc).__name__}",
        ) from exc
    if size > MAX_OUTPUT_BYTES:
        raise BusinessError(
            CAPABILITY_RUNTIME_OUTPUT_TOO_LARGE_CODE,
            f"tool output is {size} bytes; the limit is {MAX_OUTPUT_BYTES}",
        )
    return body


def untrusted_data(outcome: ToolOutcome | Mapping[str, Any] | Any) -> Any:
    """Unwrap the data from a runtime envelope, or return the value as-is.

    This is deliberately the *only* convenience accessor: it never executes a
    capability and it never treats the content as a result with authority.
    """
    result = getattr(outcome, "invocation", None)
    payload = getattr(result, "result", None) if result is not None else outcome
    if isinstance(payload, Mapping) and payload.get("untrusted") is True:
        return payload.get("data")
    return payload


@dataclass(frozen=True, slots=True)
class Availability:
    """Typed availability of one catalogued capability."""

    capability_id: str
    state: str
    detail: str = ""
    category: str = ""
    health: str = ""

    @property
    def available(self) -> bool:
        return self.state == "available"

    def as_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "state": self.state,
            "detail": self.detail,
            "category": self.category,
            "health": self.health,
        }


class _FixedWindowRateLimiter:
    """Deterministic per-key fixed-window counter (in-memory, per runtime)."""

    def __init__(self) -> None:
        self._hits: dict[tuple[str, str, str], list[float]] = {}
        self._lock = threading.Lock()

    def check(
        self,
        key: tuple[str, str, str],
        *,
        limit: int | None,
        window_seconds: float,
        now: float,
    ) -> None:
        if limit is None:
            return
        with self._lock:
            bucket = self._hits.setdefault(key, [])
            cutoff = now - window_seconds
            bucket[:] = [stamp for stamp in bucket if stamp > cutoff]
            if len(bucket) >= limit:
                raise BusinessError(
                    CAPABILITY_RUNTIME_RATE_LIMITED_CODE,
                    f"rate limit {limit} per {window_seconds:g}s exceeded for"
                    f" {key[2]!r}; try again after the window resets",
                )
            bucket.append(now)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


class CapabilityRuntime:
    """The only path from an agent to a capability adapter (P4-04)."""

    def __init__(
        self,
        store: Any,
        catalog: CapabilityCatalog,
        *,
        snapshot: CapabilitySnapshot | None = None,
        sandbox: Any = None,
        adapters: Mapping[str, Adapter] | Iterable[tuple[str, Adapter]] | None = None,
        resolver: Callable[..., list[str]] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if not isinstance(catalog, CapabilityCatalog):
            raise BusinessError(
                BAD_INPUT_CODE, "catalog must be a CapabilityCatalog"
            )
        self._store = store
        self._catalog = catalog
        self._snapshot = snapshot
        self._sandbox = sandbox
        self._resolver = resolver
        self._clock = clock or time.time
        self._adapters: dict[str, Adapter] = {}
        self._rate = _FixedWindowRateLimiter()
        self._guard: Guard | None = None
        self._lock = threading.RLock()
        self._halted = False
        self._halt_reason = ""
        self._gateway = ToolGateway(store, self._dispatch)
        items = adapters.items() if isinstance(adapters, Mapping) else (
            adapters or ()
        )
        for capability_id, adapter in items:
            self.register(capability_id, adapter)

    # -- registration --------------------------------------------------
    def register(
        self,
        capability_id: str,
        adapter: Adapter,
        *,
        category: str | None = None,
    ) -> Availability:
        """Bind one catalogued capability to one adapter.

        An unknown capability, a wrong category, an unsupported category or a
        non-callable adapter is a typed refusal. There is no wildcard binding.
        """
        clean = _no_wildcard(
            _text(capability_id, "capability_id", max_len=120), "capability_id"
        )
        capability = self._catalog.get(clean)
        if category is not None and category != capability.category:
            raise BusinessError(
                CAPABILITY_RUNTIME_UNSUPPORTED_CODE,
                f"capability {clean!r} is category {capability.category!r}, not"
                f" {category!r}",
            )
        if capability.category not in SUPPORTED_CATEGORIES:
            raise BusinessError(
                CAPABILITY_RUNTIME_UNSUPPORTED_CODE,
                f"capability {clean!r} category {capability.category!r} is not"
                " a supported runtime category",
            )
        if not callable(adapter):
            raise BusinessError(
                BAD_INPUT_CODE, "an adapter must be callable"
            )
        with self._lock:
            self._adapters[clean] = adapter
        return self.availability(clean)

    # -- state ---------------------------------------------------------
    def availability(self, capability_id: str) -> Availability:
        clean = _no_wildcard(
            _text(capability_id, "capability_id", max_len=120), "capability_id"
        )
        if not self._catalog.has(clean):
            return Availability(
                capability_id=clean,
                state="unknown",
                detail="capability is not in the catalog",
            )
        capability = self._catalog.get(clean)
        if capability.category not in SUPPORTED_CATEGORIES:
            return Availability(
                capability_id=clean,
                state="unsupported",
                detail=f"category {capability.category!r} has no runtime adapter",
                category=capability.category,
                health=capability.health,
            )
        adapter = self._adapters.get(clean)
        if adapter is None:
            return Availability(
                capability_id=clean,
                state="unavailable",
                detail="no adapter is bound to this capability",
                category=capability.category,
                health=capability.health,
            )
        if capability.health in ("unhealthy", "disabled", "offline"):
            return Availability(
                capability_id=clean,
                state="unavailable",
                detail=f"capability health is {capability.health!r}",
                category=capability.category,
                health=capability.health,
            )
        return Availability(
            capability_id=clean,
            state="available",
            detail="adapter bound and capability healthy",
            category=capability.category,
            health=capability.health,
        )

    def describe(self) -> list[dict[str, Any]]:
        return [self.availability(c.capability_id).as_dict() for c in self._catalog.list()]

    def registered_capabilities(self) -> list[str]:
        with self._lock:
            return sorted(self._adapters)

    def set_guard(self, guard: Guard | None) -> None:
        """Install a pre-dispatch guard (quarantine/halt policy)."""
        if guard is not None and not callable(guard):
            raise BusinessError(BAD_INPUT_CODE, "guard must be callable or None")
        self._guard = guard

    # -- halt (gateway kill-switch surface) ----------------------------
    def halt(self, reason: str = "operator kill switch") -> dict[str, Any]:
        """Stop every future capability call. Idempotent."""
        with self._lock:
            already = self._halted
            self._halted = True
            self._halt_reason = reason or "operator kill switch"
        return {
            "surface": "capability_runtime",
            "halted": True,
            "already_halted": already,
            "reason": self._halt_reason,
        }

    def resume(self, reason: str = "operator resume") -> dict[str, Any]:
        """Re-enable calls after a halt. Not part of the kill-switch path."""
        with self._lock:
            was = self._halted
            self._halted = False
            self._halt_reason = ""
        return {"surface": "capability_runtime", "halted": False, "was_halted": was}

    @property
    def halted(self) -> bool:
        return self._halted

    # -- execution -----------------------------------------------------
    def call(
        self,
        *,
        tenant_id: str,
        business_id: str,
        subject_id: str,
        actor: str,
        capability_id: str,
        policy: Policy,
        idempotency_key: str,
        payload: Any = None,
        cost_minor: int = 0,
        currency: str = "USD",
        dry_run: bool = False,
        approval_id: str | None = None,
        data_class: str = "internal",
        environment: str = "production",
        rate_window_seconds: float | None = None,
        snapshot: CapabilitySnapshot | None = None,
        now: float | None = None,
    ) -> ToolOutcome:
        """Authorize and execute one capability call through the gateway."""
        clean = _no_wildcard(
            _text(capability_id, "capability_id", max_len=120), "capability_id"
        )
        if not isinstance(policy, Policy):
            raise BusinessError(
                CAPABILITY_RUNTIME_POLICY_REQUIRED_CODE,
                "a capability call requires an explicit Policy; the runtime"
                " never manufactures authority",
            )
        ability = self.availability(clean)
        if not ability.available:
            code = (
                CAPABILITY_UNKNOWN_CODE
                if ability.state == "unknown"
                else CAPABILITY_RUNTIME_UNSUPPORTED_CODE
                if ability.state == "unsupported"
                else CAPABILITY_RUNTIME_UNAVAILABLE_CODE
            )
            raise BusinessError(
                code,
                f"capability {clean!r} is {ability.state}: {ability.detail}",
            )
        capability = self._catalog.get(clean)
        if self._halted:
            raise BusinessError(
                CAPABILITY_RUNTIME_HALTED_CODE,
                f"the capability runtime is halted ({self._halt_reason});"
                " no capability can run",
            )
        subject = _text(subject_id, "subject_id", max_len=200)
        actor_clean = _text(actor, "actor", max_len=200)
        if self._guard is not None:
            try:
                self._guard(tenant_id, subject)
            except BusinessError:
                raise
            except Exception as exc:  # noqa: BLE001 - guard is an injected seam
                raise BusinessError(
                    CAPABILITY_RUNTIME_GUARD_REFUSED_CODE,
                    f"the capability guard refused this call:"
                    f" {type(exc).__name__}",
                ) from exc
        moment = self._clock() if now is None else float(now)
        window = (
            DEFAULT_RATE_WINDOW_SECONDS
            if rate_window_seconds is None
            else float(rate_window_seconds)
        )
        self._rate.check(
            (tenant_id, subject, clean),
            limit=capability.rate_limit,
            window_seconds=window,
            now=moment,
        )
        active = snapshot if snapshot is not None else self._snapshot
        if active is None:
            raise BusinessError(
                CAPABILITY_RUNTIME_POLICY_REQUIRED_CODE,
                "no pinned CapabilitySnapshot is bound to this runtime or call;"
                " deny-by-default cannot be evaluated without one",
            )
        if clean not in policy.allowed_tools:
            # The gateway raises the same class of refusal; refusing here keeps
            # the message actionable and avoids reserving an idempotency slot.
            raise BusinessError(
                "BUSINESS_TOOL_NOT_ALLOWED",
                f"capability {clean!r} is not in the caller's allowed tools",
            )
        return self._gateway.invoke(
            tenant_id=tenant_id,
            business_id=business_id,
            actor=actor_clean,
            tool=clean,
            idempotency_key=idempotency_key,
            policy=policy,
            payload=payload,
            cost_minor=cost_minor,
            currency=currency,
            dry_run=dry_run,
            approval_id=approval_id,
            rate_limit=capability.rate_limit,
            rate_window_seconds=window,
            snapshot=active,
            catalog=self._catalog,
            subject_id=subject,
            resource=capability.resource,
            action=capability.action,
            data_class=data_class,
            environment=environment,
            now=moment,
        )

    def invoke(self, **kwargs: Any) -> ToolOutcome:
        """Alias for :meth:`call`; both cross the same gateway."""
        return self.call(**kwargs)

    # -- dispatch (gateway executor) -----------------------------------
    def _dispatch(self, request: ToolRequest) -> Any:
        """Gateway executor: validate safety, invoke exactly one adapter."""
        capability = self._catalog.get(request.tool)
        with self._lock:
            adapter = self._adapters.get(request.tool)
        if adapter is None:  # pragma: no cover - availability() pre-checks it
            raise BusinessError(
                CAPABILITY_RUNTIME_UNSUPPORTED_CODE,
                f"no adapter is bound for capability {request.tool!r}",
            )
        checked = self._checked_payload(capability, request.payload)
        effective = (
            request if checked is request.payload else replace(request, payload=checked)
        )
        raw = adapter(effective, capability)
        return _envelope(request.tool, raw)

    def _checked_payload(self, capability: Capability, payload: Any) -> Any:
        """Apply sandbox rules to the exact payload the adapter will receive."""
        category = capability.category
        if category == "terminal":
            return self._terminal_payload(payload)
        if category == "files":
            return self._files_payload(payload)
        if category in _NETWORK_CATEGORIES and isinstance(payload, Mapping):
            url = _payload_url(payload)
            if url is not None:
                self._check_egress(url)
        return payload

    def _terminal_payload(self, payload: Any) -> Any:
        if self._sandbox is None:
            raise BusinessError(
                CAPABILITY_RUNTIME_SANDBOX_REQUIRED_CODE,
                "terminal capability requires an injected sandbox profile",
            )
        if isinstance(payload, Mapping):
            argv = payload.get("argv")
            env = payload.get("env")
        else:
            argv = payload
            env = None
        if argv is None:
            raise BusinessError(
                sandbox_module.SANDBOX_ARGV_REQUIRED_CODE,
                "terminal calls require an 'argv' sequence",
            )
        validated = self._sandbox.build_argv(argv, env=env)
        if isinstance(payload, Mapping):
            return {**payload, "argv": validated}
        return {"argv": validated}

    def _files_payload(self, payload: Any) -> Any:
        if self._sandbox is None:
            raise BusinessError(
                CAPABILITY_RUNTIME_SANDBOX_REQUIRED_CODE,
                "files capability requires an injected sandbox profile",
            )
        if not isinstance(payload, Mapping) or "path" not in payload:
            raise BusinessError(
                BAD_INPUT_CODE, "files calls require a payload mapping with 'path'"
            )
        resolved = self._sandbox.resolve_path(payload["path"])
        return {**payload, "path": str(resolved)}

    def _check_egress(self, url: str) -> str:
        if self._sandbox is None:
            raise BusinessError(
                CAPABILITY_RUNTIME_SANDBOX_REQUIRED_CODE,
                "network capability requires an injected sandbox profile",
            )
        if self._resolver is None:
            return self._sandbox.check_egress(url)
        return self._sandbox.check_egress(url, resolver=self._resolver)


def _payload_url(payload: Mapping[str, Any]) -> str | None:
    for key in ("url", "endpoint", "uri"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None

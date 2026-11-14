"""Typed, dry-run adapter foundation.

Every integration adapter in this package obeys the same contract:

* an injected ``Transport`` is the only way to reach a provider — the adapter
  module itself never imports or opens a network client;
* missing transport or credentials is a typed refusal, never an empty success;
* mutations require an idempotency key, and a replayed key with a different
  request is a typed conflict;
* a per-adapter rate limiter refuses before the provider is called;
* policy-gated operations require an :class:`ApprovalToken`;
* ``dry_run`` is the default: the adapter validates and returns a plan without
  calling the transport.

Idempotency here is in-process (a replayed key returns the cached result). The
durable, cross-restart idempotency record is the Business OS ToolGateway's job;
this layer prevents duplicate local attempts, not duplicate provider effects
after an ``unknown`` outcome.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, replace
from typing import Any, Callable, Mapping, Protocol, runtime_checkable

from .platform_policy import (
    PlatformPolicy,
    PolicyDecision,
    evaluate_action,
    get_policy,
)

ADAPTER_UNAVAILABLE_CODE = "adapter_unavailable"
ADAPTER_NOT_CONFIGURED_CODE = "adapter_not_configured"
ADAPTER_APPROVAL_REQUIRED_CODE = "adapter_approval_required"
ADAPTER_RATE_LIMITED_CODE = "adapter_rate_limited"
ADAPTER_IDEMPOTENCY_CONFLICT_CODE = "adapter_idempotency_conflict"
ADAPTER_POLICY_FORBIDDEN_CODE = "adapter_policy_forbidden"
ADAPTER_BAD_INPUT_CODE = "adapter_bad_input"
ADAPTER_CONSENT_REQUIRED_CODE = "adapter_consent_required"
ADAPTER_TRANSPORT_ERROR_CODE = "adapter_transport_error"


class AdapterError(Exception):
    """A typed adapter refusal. ``code`` is stable API."""

    def __init__(
        self,
        code: str,
        detail: str,
        *,
        platform: str = "",
        operation: str = "",
    ) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.platform = platform
        self.operation = operation

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": "REFUSED",
            "code": self.code,
            "platform": self.platform or None,
            "operation": self.operation or None,
            "explanation": self.detail,
            "executed": False,
        }


@dataclass(frozen=True, slots=True)
class ApprovalToken:
    """A human approval presented to an adapter.

    ``action`` must match the operation's expected approval action (for example
    ``etsy.listing.publish``). The token is a claim by the caller that the
    Business OS approval gate already recorded the decision; adapters do not
    mint approvals.
    """

    approval_id: str
    action: str
    approved: bool = True

    def __post_init__(self) -> None:
        if not str(self.approval_id or "").strip():
            raise ValueError("approval_id is required")
        if not str(self.action or "").strip():
            raise ValueError("action is required")


@dataclass(frozen=True, slots=True)
class AdapterResult:
    """The outcome of an adapter operation.

    ``executed`` is true only when a non-dry-run call reached the injected
    transport. A dry-run, a replay or a refusal is never an execution.
    """

    platform: str
    operation: str
    dry_run: bool
    executed: bool
    replayed: bool = False
    data: Any = None
    approval_id: str | None = None
    idempotency_key: str | None = None
    request_fingerprint: str = ""
    would_do: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "operation": self.operation,
            "dry_run": self.dry_run,
            "executed": self.executed,
            "replayed": self.replayed,
            "data": self.data,
            "approval_id": self.approval_id,
            "idempotency_key": self.idempotency_key,
            "request_fingerprint": self.request_fingerprint,
            "would_do": self.would_do,
        }


@runtime_checkable
class Transport(Protocol):
    """The only provider seam. Implementations are injected by a caller."""

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Mapping[str, Any] | None = None,
    ) -> Any:  # pragma: no cover - protocol
        ...


class RateLimiter:
    """A small fixed-window limiter, deterministic under an injected clock."""

    def __init__(
        self,
        limit: int,
        window_seconds: float,
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
            raise ValueError("limit must be a positive integer")
        if not isinstance(window_seconds, (int, float)) or window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self._limit = limit
        self._window = float(window_seconds)
        self._clock = clock or time.monotonic
        self._events: list[float] = []

    def consume(self, *, platform: str, operation: str) -> None:
        now = self._clock()
        cutoff = now - self._window
        self._events = [stamp for stamp in self._events if stamp > cutoff]
        if len(self._events) >= self._limit:
            raise AdapterError(
                ADAPTER_RATE_LIMITED_CODE,
                f"{platform}: rate limit of {self._limit} calls per"
                f" {self._window:g}s reached; retry later",
                platform=platform,
                operation=operation,
            )
        self._events.append(now)


class BaseAdapter:
    """Shared policy, approval, rate-limit and idempotency machinery."""

    platform_id: str = ""
    credential_keys: tuple[str, ...] = ()
    default_rate_limit: int = 60
    default_rate_window_seconds: float = 60.0

    def __init__(
        self,
        *,
        transport: Transport | None = None,
        credentials: Mapping[str, Any] | None = None,
        dry_run: bool = True,
        rate_limit: int | None = None,
        rate_window_seconds: float | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if not self.platform_id:
            raise ValueError("an adapter must declare platform_id")
        self._policy: PlatformPolicy = get_policy(self.platform_id)
        self._transport = transport
        self._credentials: dict[str, Any] = dict(credentials or {})
        self._dry_run = bool(dry_run)
        self._rate_limiter = RateLimiter(
            rate_limit if rate_limit is not None else self.default_rate_limit,
            (
                rate_window_seconds
                if rate_window_seconds is not None
                else self.default_rate_window_seconds
            ),
            clock=clock,
        )
        self._idempotency: dict[str, tuple[str, AdapterResult]] = {}

    # -- public state --------------------------------------------------
    @property
    def policy(self) -> PlatformPolicy:
        return self._policy

    @property
    def dry_run(self) -> bool:
        return self._dry_run

    # -- guards --------------------------------------------------------
    def check_ready(self, operation: str = "read") -> None:
        """Public readiness probe: transport and credentials, typed refusal.

        Executors call this before dispatching so a missing transport or
        credential is an ``adapter_unavailable``/``adapter_not_configured``
        refusal rather than an ambiguous later failure.
        """
        self._require_ready(operation)

    def _require_ready(self, operation: str) -> None:
        if self._transport is None:
            raise AdapterError(
                ADAPTER_UNAVAILABLE_CODE,
                f"{self._policy.display_name}: no transport is configured;"
                " dry-run adapters ship no network client",
                platform=self.platform_id,
                operation=operation,
            )
        missing = [
            key
            for key in self.credential_keys
            if not str(self._credentials.get(key) or "").strip()
        ]
        if missing:
            raise AdapterError(
                ADAPTER_NOT_CONFIGURED_CODE,
                f"{self._policy.display_name}: missing credentials:"
                f" {', '.join(missing)}",
                platform=self.platform_id,
                operation=operation,
            )

    def _policy_decision(self, action: str, operation: str) -> PolicyDecision:
        decision = evaluate_action(self.platform_id, action)
        if not decision.allowed:
            raise AdapterError(
                ADAPTER_POLICY_FORBIDDEN_CODE,
                decision.reason,
                platform=self.platform_id,
                operation=operation,
            )
        return decision

    def _approval_gate(
        self,
        expected_action: str,
        approval: ApprovalToken | None,
        operation: str,
    ) -> str:
        if (
            approval is None
            or not approval.approved
            or str(approval.action).strip() != expected_action
        ):
            raise AdapterError(
                ADAPTER_APPROVAL_REQUIRED_CODE,
                f"{self._policy.display_name}: {operation} requires an approved"
                f" token for action {expected_action!r}",
                platform=self.platform_id,
                operation=operation,
            )
        return approval.approval_id

    def _bad_input(self, detail: str, operation: str) -> AdapterError:
        return AdapterError(
            ADAPTER_BAD_INPUT_CODE,
            f"{self._policy.display_name}: {detail}",
            platform=self.platform_id,
            operation=operation,
        )

    # -- idempotency ---------------------------------------------------
    def _fingerprint(self, operation: str, payload: Any) -> str:
        # Local import: importing the whole Business OS package from this
        # module would form a cycle (business.integration_tools imports this
        # adapter foundation), so ``integrations`` must be importable first.
        from ..business.ids import fingerprint

        return fingerprint(
            {
                "platform": self.platform_id,
                "operation": operation,
                "payload": payload,
                "dry_run": self._dry_run,
            }
        )

    def _replay(
        self,
        cache_key: str,
        request_fingerprint: str,
        operation: str,
    ) -> AdapterResult | None:
        cached = self._idempotency.get(cache_key)
        if cached is None:
            return None
        cached_fingerprint, result = cached
        if cached_fingerprint != request_fingerprint:
            raise AdapterError(
                ADAPTER_IDEMPOTENCY_CONFLICT_CODE,
                f"{self._policy.display_name}: idempotency key was already used"
                " for a different request",
                platform=self.platform_id,
                operation=operation,
            )
        return replace(result, replayed=True)

    def _cache(
        self, cache_key: str, request_fingerprint: str, result: AdapterResult
    ) -> AdapterResult:
        self._idempotency[cache_key] = (request_fingerprint, result)
        return result

    # -- operations ----------------------------------------------------
    def _read(
        self,
        operation: str,
        *,
        path: str,
        params: Mapping[str, Any] | None = None,
        policy_action: str = "read",
    ) -> AdapterResult:
        self._require_ready(operation)
        self._policy_decision(policy_action, operation)
        self._rate_limiter.consume(
            platform=self.platform_id, operation=operation
        )
        if self._dry_run:
            return AdapterResult(
                platform=self.platform_id,
                operation=operation,
                dry_run=True,
                executed=False,
                would_do=f"GET {path}",
            )
        data = self._call_transport(
            "GET", path, params=params, json_body=None, operation=operation
        )
        return AdapterResult(
            platform=self.platform_id,
            operation=operation,
            dry_run=False,
            executed=True,
            data=data,
        )

    def _mutate(
        self,
        operation: str,
        payload: Mapping[str, Any],
        *,
        idempotency_key: str,
        path: str,
        method: str = "POST",
        policy_action: str = "publish",
        approval_action: str | None = None,
        approval: ApprovalToken | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> AdapterResult:
        if not str(idempotency_key or "").strip():
            raise self._bad_input(
                "an idempotency key is required for this operation", operation
            )
        self._require_ready(operation)
        decision = self._policy_decision(policy_action, operation)
        expected_approval = approval_action
        if expected_approval is None and decision.requires_approval:
            expected_approval = policy_action
        approval_id = None
        if expected_approval is not None:
            approval_id = self._approval_gate(
                expected_approval, approval, operation
            )

        request_fingerprint = self._fingerprint(operation, payload)
        cache_key = f"{operation}:{idempotency_key}"
        replayed = self._replay(cache_key, request_fingerprint, operation)
        if replayed is not None:
            return replayed

        self._rate_limiter.consume(
            platform=self.platform_id, operation=operation
        )
        if self._dry_run:
            result = AdapterResult(
                platform=self.platform_id,
                operation=operation,
                dry_run=True,
                executed=False,
                approval_id=approval_id,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
                would_do=f"{method} {path}",
            )
            return self._cache(cache_key, request_fingerprint, result)

        data = self._call_transport(
            method, path, params=params, json_body=payload, operation=operation
        )
        result = AdapterResult(
            platform=self.platform_id,
            operation=operation,
            dry_run=False,
            executed=True,
            approval_id=approval_id,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            data=data,
        )
        return self._cache(cache_key, request_fingerprint, result)

    def _call_transport(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None,
        json_body: Mapping[str, Any] | None,
        operation: str,
    ) -> Any:
        assert self._transport is not None
        try:
            return self._transport.request(
                method, path, params=params, json_body=json_body
            )
        except AdapterError:
            raise
        except Exception as exc:  # noqa: BLE001 - injected provider seam
            raise AdapterError(
                ADAPTER_TRANSPORT_ERROR_CODE,
                f"{self._policy.display_name}: transport failed for {operation}:"
                f" {type(exc).__name__}",
                platform=self.platform_id,
                operation=operation,
            ) from exc


__all__ = [
    "ADAPTER_APPROVAL_REQUIRED_CODE",
    "ADAPTER_BAD_INPUT_CODE",
    "ADAPTER_CONSENT_REQUIRED_CODE",
    "ADAPTER_IDEMPOTENCY_CONFLICT_CODE",
    "ADAPTER_NOT_CONFIGURED_CODE",
    "ADAPTER_POLICY_FORBIDDEN_CODE",
    "ADAPTER_RATE_LIMITED_CODE",
    "ADAPTER_TRANSPORT_ERROR_CODE",
    "ADAPTER_UNAVAILABLE_CODE",
    "AdapterError",
    "AdapterResult",
    "ApprovalToken",
    "BaseAdapter",
    "RateLimiter",
    "Transport",
]

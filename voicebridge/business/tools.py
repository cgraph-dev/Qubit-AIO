"""The tool gateway: the only door from an agent to an external system.

No business module calls Stripe, a CRM, SMTP or an HTTP API. A tool implementation
is injected as an executor and every call crosses this gateway first, which:

1. validates the actor, tenant/business scope, policy and autonomy level;
2. enforces an approval for L4/L5 or policy-flagged actions;
3. enforces the per-business budget for the period;
4. deduplicates by idempotency key (one key, one effect);
5. supports ``dry_run`` (validates and records intent, executes nothing);
6. redacts request/result payloads before persisting, so no secret reaches a
   log, an audit export or a support bundle.

The skeleton ships **no** executor: with none configured, a non-dry call is a
typed ``BUSINESS_TOOL_EXECUTOR_MISSING`` refusal. Real systems-of-record
adapters are deliberately deferred (see ``docs/QUBIT-BUSINESS-OS-FOUNDATION.md``).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

from . import models
from .errors import (
    APPROVAL_REQUIRED_CODE,
    BAD_INPUT_CODE,
    BUDGET_EXCEEDED_CODE,
    IDEMPOTENCY_CONFLICT_CODE,
    TOOL_EXECUTOR_MISSING_CODE,
    TOOL_FAILED_CODE,
    TOOL_IN_FLIGHT_CODE,
    TOOL_NOT_ALLOWED_CODE,
    BusinessError,
)
from .ids import fingerprint, new_id
from .util import json_dumps, now_iso, redact

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .store import BusinessStore

AUTONOMY_LEVELS = ("L0", "L1", "L2", "L3", "L4", "L5")
APPROVAL_LEVELS = frozenset({"L4", "L5"})


@dataclass(frozen=True, slots=True)
class Policy:
    """A caller's allowed tools and autonomy posture."""

    allowed_tools: frozenset[str]
    autonomy: str = "L1"
    max_cost_minor: int | None = None
    approval_required: bool = False
    policy_version: str = "v1"

    def __post_init__(self) -> None:
        if self.autonomy not in AUTONOMY_LEVELS:
            raise ValueError(
                f"autonomy must be one of {', '.join(AUTONOMY_LEVELS)}"
            )
        if self.max_cost_minor is not None and (
            not isinstance(self.max_cost_minor, int)
            or isinstance(self.max_cost_minor, bool)
        ):
            raise ValueError("max_cost_minor must be an integer or None")
        if self.max_cost_minor is not None and self.max_cost_minor < 0:
            raise ValueError("max_cost_minor must be non-negative")


@dataclass(frozen=True, slots=True)
class ToolRequest:
    """What an executor receives. It contains no database handle."""

    invocation_id: str
    tenant_id: str
    business_id: str
    actor: str
    tool: str
    payload: Any
    idempotency_key: str
    dry_run: bool
    autonomy: str
    policy_version: str
    cost_minor: int
    currency: str


@dataclass(frozen=True, slots=True)
class ToolOutcome:
    invocation: models.ToolInvocation
    executed: bool
    replayed: bool


Executor = Callable[[ToolRequest], Any]


def _text(value: Any, label: str, *, max_len: int = 4096) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BusinessError(BAD_INPUT_CODE, f"{label} must be a non-empty string")
    clean = value.strip()
    if len(clean) > max_len:
        raise BusinessError(
            BAD_INPUT_CODE, f"{label} is longer than {max_len} characters"
        )
    return clean


def _currency(value: Any) -> str:
    clean = _text(value, "currency", max_len=8).upper()
    if len(clean) != 3 or not clean.isalpha():
        raise BusinessError(BAD_INPUT_CODE, "currency must be a 3-letter code")
    return clean


class ToolGateway:
    def __init__(self, store: "BusinessStore", executor: Executor | None = None) -> None:
        self._store = store
        self._executor = executor

    def invoke(
        self,
        *,
        tenant_id: str,
        business_id: str,
        actor: str,
        tool: str,
        idempotency_key: str,
        policy: Policy,
        payload: Any = None,
        cost_minor: int = 0,
        currency: str = "USD",
        dry_run: bool = False,
        approval_id: str | None = None,
    ) -> ToolOutcome:
        actor_clean = _text(actor, "actor", max_len=200)
        tool_clean = _text(tool, "tool", max_len=120)
        key = _text(idempotency_key, "idempotency key", max_len=300)
        code = _currency(currency)
        if not isinstance(policy, Policy):
            raise BusinessError(BAD_INPUT_CODE, "policy must be a Policy")
        if tool_clean not in policy.allowed_tools:
            raise BusinessError(
                TOOL_NOT_ALLOWED_CODE,
                f"tool {tool_clean!r} is not allowed by policy"
                f" {policy.policy_version!r}",
            )
        if (
            not isinstance(cost_minor, int)
            or isinstance(cost_minor, bool)
            or cost_minor < 0
        ):
            raise BusinessError(
                BAD_INPUT_CODE, "cost_minor must be a non-negative integer"
            )
        if policy.max_cost_minor is not None and cost_minor > policy.max_cost_minor:
            raise BusinessError(
                BUDGET_EXCEEDED_CODE,
                f"cost {cost_minor} exceeds policy cap {policy.max_cost_minor}",
            )
        if not dry_run and self._executor is None:
            raise BusinessError(
                TOOL_EXECUTOR_MISSING_CODE,
                "no tool executor is configured; real integrations are not"
                " shipped in the control-plane skeleton (use dry_run=True)",
            )
        requires_approval = policy.approval_required or policy.autonomy in APPROVAL_LEVELS
        body = {} if payload is None else payload
        request_hash = fingerprint(
            {
                "tool": tool_clean,
                "payload": body,
                "cost_minor": cost_minor,
                "currency": code,
                "autonomy": policy.autonomy,
                "policy_version": policy.policy_version,
                "approval_id": approval_id,
            }
        )
        try:
            existing, invocation_id = self._reserve(
                tenant_id=tenant_id,
                business_id=business_id,
                actor=actor_clean,
                tool=tool_clean,
                idempotency_key=key,
                request_hash=request_hash,
                payload=body,
                dry_run=dry_run,
                autonomy=policy.autonomy,
                policy_version=policy.policy_version,
                cost_minor=cost_minor,
                currency=code,
                requires_approval=requires_approval,
                approval_id=approval_id,
            )
        except BusinessError as exc:
            self._audit_refusal(
                tenant_id, business_id, actor_clean, tool_clean, key, exc
            )
            raise
        if existing is not None:
            return ToolOutcome(existing, executed=False, replayed=True)
        assert invocation_id is not None

        request = ToolRequest(
            invocation_id=invocation_id,
            tenant_id=tenant_id,
            business_id=business_id,
            actor=actor_clean,
            tool=tool_clean,
            payload=body,
            idempotency_key=key,
            dry_run=dry_run,
            autonomy=policy.autonomy,
            policy_version=policy.policy_version,
            cost_minor=cost_minor,
            currency=code,
        )
        if dry_run:
            preview = {
                "dry_run": True,
                "tool": tool_clean,
                "would_cost_minor": cost_minor,
                "approval_required": requires_approval,
            }
            return self._complete(
                tenant_id, invocation_id, "dry_run", preview,
                cost_minor=0, error_code=None, dry_run=True,
            )
        try:
            raw = self._executor(request)  # type: ignore[misc]
        except Exception as exc:  # noqa: BLE001 - executor is an external seam
            message = redact(f"{type(exc).__name__}: {exc}")[:2000]
            self._complete(
                tenant_id, invocation_id, "failed", None,
                cost_minor=cost_minor, error_code=TOOL_FAILED_CODE, dry_run=False,
            )
            with self._store.transaction() as conn:
                self._store._audit(
                    conn, tenant_id, business_id, actor_clean, "tool.result",
                    "tool_invocation", invocation_id, "error",
                    {"tool": tool_clean, "error": message},
                )
            raise BusinessError(
                TOOL_FAILED_CODE,
                f"tool {tool_clean!r} failed: {message}",
            ) from exc
        result = raw if raw is not None else {}
        return self._complete(
            tenant_id, invocation_id, "success", result,
            cost_minor=cost_minor, error_code=None, dry_run=False,
        )

    # -- internals -----------------------------------------------------
    def _reserve(
        self,
        *,
        tenant_id: str,
        business_id: str,
        actor: str,
        tool: str,
        idempotency_key: str,
        request_hash: str,
        payload: Any,
        dry_run: bool,
        autonomy: str,
        policy_version: str,
        cost_minor: int,
        currency: str,
        requires_approval: bool,
        approval_id: str | None,
    ) -> tuple[models.ToolInvocation | None, str | None]:
        with self._store.transaction() as conn:
            self._store._require_business(conn, tenant_id, business_id)
            existing = conn.execute(
                "SELECT * FROM bos_tool_invocation WHERE tenant_id=?"
                " AND idempotency_key=? AND dry_run=?",
                (tenant_id, idempotency_key, 1 if dry_run else 0),
            ).fetchone()
            if existing is not None:
                if existing["request_hash"] != request_hash:
                    raise BusinessError(
                        IDEMPOTENCY_CONFLICT_CODE,
                        f"idempotency key {idempotency_key!r} was already used"
                        " for a different tool request",
                    )
                if existing["status"] == "pending":
                    raise BusinessError(
                        TOOL_IN_FLIGHT_CODE,
                        f"tool call {idempotency_key!r} is already in flight",
                    )
                return (
                    models.ToolInvocation.from_row(existing),
                    existing["invocation_id"],
                )
            if requires_approval and not dry_run:
                approved = None
                if approval_id is not None:
                    approved = conn.execute(
                        "SELECT * FROM bos_approval WHERE tenant_id=?"
                        " AND business_id=? AND approval_id=? AND status='approved'"
                        " AND action=?",
                        (tenant_id, business_id, approval_id, f"tool:{tool}"),
                    ).fetchone()
                if approved is None:
                    raise BusinessError(
                        APPROVAL_REQUIRED_CODE,
                        f"tool {tool!r} at autonomy {autonomy!r} needs an approved"
                        f" approval with action 'tool:{tool}'",
                    )
            budget = self._store._budget_status_conn(
                conn,
                tenant_id,
                business_id,
                currency=currency,
                period=now_iso()[:7],
            )
            remaining = budget["remaining_minor"]
            if (
                budget["configured"]
                and not dry_run
                and remaining is not None
                and cost_minor > remaining
            ):
                raise BusinessError(
                    BUDGET_EXCEEDED_CODE,
                    f"tool cost {cost_minor} exceeds remaining budget"
                    f" {remaining} {currency} for {budget['period']}",
                )
            invocation_id = new_id("tcl")
            conn.execute(
                "INSERT INTO bos_tool_invocation(invocation_id, tenant_id,"
                " business_id, actor, tool, autonomy, policy_version,"
                " idempotency_key, request_hash, dry_run, status, currency,"
                " cost_minor, request_json, created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,'pending',?,?,?,?)",
                (
                    invocation_id,
                    tenant_id,
                    business_id,
                    actor,
                    tool,
                    autonomy,
                    policy_version,
                    idempotency_key,
                    request_hash,
                    1 if dry_run else 0,
                    currency,
                    cost_minor,
                    json_dumps(redact(payload)),
                    now_iso(),
                ),
            )
            self._store._audit(
                conn, tenant_id, business_id, actor, "tool.invoke",
                "tool_invocation", invocation_id, "ok",
                {
                    "tool": tool,
                    "dry_run": dry_run,
                    "cost_minor": cost_minor,
                    "idempotency_key": idempotency_key,
                },
            )
        return None, invocation_id

    def _complete(
        self,
        tenant_id: str,
        invocation_id: str,
        status: str,
        result: Any,
        *,
        cost_minor: int,
        error_code: str | None,
        dry_run: bool,
    ) -> ToolOutcome:
        with self._store.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM bos_tool_invocation WHERE tenant_id=?"
                " AND invocation_id=?",
                (tenant_id, invocation_id),
            ).fetchone()
            if row is None:
                raise BusinessError(TOOL_IN_FLIGHT_CODE, "invocation disappeared")
            conn.execute(
                "UPDATE bos_tool_invocation SET status=?, result_json=?,"
                " cost_minor=?, error_code=? WHERE tenant_id=? AND invocation_id=?",
                (
                    status,
                    None if result is None else json_dumps(redact(result)),
                    cost_minor,
                    error_code,
                    tenant_id,
                    invocation_id,
                ),
            )
            self._store._audit(
                conn, tenant_id, row["business_id"], row["actor"], "tool.result",
                "tool_invocation", invocation_id, "ok" if status != "failed" else "error",
                {"tool": row["tool"], "status": status, "dry_run": dry_run},
            )
            updated = conn.execute(
                "SELECT * FROM bos_tool_invocation WHERE tenant_id=?"
                " AND invocation_id=?",
                (tenant_id, invocation_id),
            ).fetchone()
        return ToolOutcome(
            models.ToolInvocation.from_row(updated),
            executed=not dry_run and status == "success",
            replayed=False,
        )

    def _audit_refusal(
        self,
        tenant_id: str,
        business_id: str,
        actor: str,
        tool: str,
        key: str,
        exc: BusinessError,
    ) -> None:
        try:
            with self._store.transaction() as conn:
                self._store._require_business(conn, tenant_id, business_id)
                self._store._audit(
                    conn, tenant_id, business_id, actor, "tool.refused", "tool",
                    tool, "refused",
                    {
                        "code": exc.code,
                        "detail": exc.detail,
                        "idempotency_key": key,
                    },
                )
        except BusinessError:
            pass

    def invocation(
        self, tenant_id: str, invocation_id: str
    ) -> models.ToolInvocation:
        with self._store.read() as conn:
            row = conn.execute(
                "SELECT * FROM bos_tool_invocation WHERE tenant_id=?"
                " AND invocation_id=?",
                (tenant_id, invocation_id),
            ).fetchone()
        if row is None:
            from .errors import NOT_FOUND_CODE

            raise BusinessError(
                NOT_FOUND_CODE,
                f"no tool invocation {invocation_id!r} in tenant {tenant_id!r}",
            )
        return models.ToolInvocation.from_row(row)

    def list_invocations(
        self, tenant_id: str, *, business_id: str | None = None
    ) -> list[models.ToolInvocation]:
        query = "SELECT * FROM bos_tool_invocation WHERE tenant_id=?"
        params: list[Any] = [tenant_id]
        if business_id is not None:
            query += " AND business_id=?"
            params.append(business_id)
        query += " ORDER BY created_at, invocation_id"
        with self._store.read() as conn:
            rows = conn.execute(query, params).fetchall()
        return [models.ToolInvocation.from_row(row) for row in rows]

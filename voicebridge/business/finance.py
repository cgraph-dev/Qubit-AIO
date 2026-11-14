"""P5-05 — finance, accounting, payments, ledger and reconciliation.

The module is a thin, truthful layer over the existing Business OS tables. It
adds **no new database**: postings are ``bos_ledger_entry`` rows, payment
intents and receipts are event-sourced in ``bos_event`` (with provider
receipts also stored as immutable ``bos_artifact`` rows), and human approval
goes through :mod:`voicebridge.business.approvals`.

Non-negotiable rules encoded here:

* **Double entry only.** A posting is two or more lines whose debit total
  equals its credit total, in integer minor units. An unbalanced posting is a
  typed refusal; there is no single-sided convenience path.
* **One effect per idempotency key.** Postings, payment intents and receipts
  use a derived provider idempotency key. A replay returns the original
  effect; the same key with a different body is a typed conflict.
* **Unknown stays unknown.** A payment whose outcome is not confirmed is
  ``unknown`` until a caller resolves it with an explicit outcome *and* a
  provider receipt for any resolution that claims money moved.
* **Narrative is never financial truth.** A note can be attached to an intent,
  but it can never change its state. Only ``resolve_unknown`` (with provider
  evidence) can move an ``unknown`` payment.
* **Payments, refunds, chargebacks and payouts require a human approval.**
  Step-up is evaluated with :func:`identity.require_step_up`; execution is
  re-authorized against the exact approved intent.
* **Tenant/entity separation.** Every read and write is scoped by
  ``tenant_id``; postings may carry an entity namespace so one legal entity's
  books never mix with another's.

Hermetic by construction: no live payment, bank or network call exists in this
module. A "provider" is a label; a receipt is data the caller already holds.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterable, Mapping

from . import approvals, eventlog, identity
from .errors import (
    BAD_INPUT_CODE,
    BAD_STATE_CODE,
    NOT_FOUND_CODE,
    BusinessError,
)
from .ids import fingerprint, new_id
from .util import json_dumps, now_iso, redact, require_text

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .store import BusinessStore

# Finance-specific refusal codes. They are stable API for CLI/UI/voice.
UNBALANCED_CODE = "BUSINESS_FINANCE_UNBALANCED"
BAD_POSTING_CODE = "BUSINESS_FINANCE_BAD_POSTING"
POSTING_NOT_FOUND_CODE = "BUSINESS_FINANCE_POSTING_NOT_FOUND"
PAYMENT_NOT_FOUND_CODE = "BUSINESS_FINANCE_PAYMENT_NOT_FOUND"
PAYMENT_BAD_STATE_CODE = "BUSINESS_FINANCE_PAYMENT_BAD_STATE"
PAYMENT_IDEMPOTENCY_CODE = "BUSINESS_FINANCE_IDEMPOTENCY_CONFLICT"
PAYMENT_APPROVAL_REQUIRED_CODE = "BUSINESS_FINANCE_APPROVAL_REQUIRED"
PAYMENT_UNKNOWN_UNRESOLVED_CODE = "BUSINESS_FINANCE_UNKNOWN_UNRESOLVED"
NARRATIVE_NOT_TRUTH_CODE = "BUSINESS_FINANCE_NARRATIVE_NOT_TRUTH"
RECONCILIATION_MISMATCH_CODE = "BUSINESS_FINANCE_RECONCILIATION_MISMATCH"
ENTITY_MISMATCH_CODE = "BUSINESS_FINANCE_ENTITY_MISMATCH"

DIRECTIONS = ("debit", "credit")

STATE_PENDING = "pending"
STATE_SENT = "sent"
STATE_ACKED = "acked"
STATE_UNKNOWN = "unknown"
STATE_COMPENSATED = "compensated"
STATE_FAILED = "failed"
STATE_COMPLETED = "completed"
PAYMENT_STATES = (
    STATE_PENDING,
    STATE_SENT,
    STATE_ACKED,
    STATE_UNKNOWN,
    STATE_COMPENSATED,
    STATE_FAILED,
    STATE_COMPLETED,
)
#: The only outcomes an ``unknown`` payment may be resolved to. "paid" is not
#: a resolution: it is a narrative.
UNKNOWN_RESOLUTIONS = (STATE_SENT, STATE_COMPENSATED, STATE_FAILED)

_MONEY_ACTIONS = ("finance.payment", "finance.refund", "finance.chargeback",
                  "finance.payout")


def _text(value: Any, label: str, *, max_len: int = 4096) -> str:
    try:
        return require_text(value, label, max_len=max_len)
    except ValueError as exc:
        raise BusinessError(BAD_INPUT_CODE, str(exc)) from exc


def _currency(value: Any) -> str:
    clean = _text(value, "currency", max_len=8).upper()
    if len(clean) != 3 or not clean.isalpha():
        raise BusinessError(BAD_INPUT_CODE, "currency must be a 3-letter code")
    return clean


def _amount(value: Any, label: str, *, allow_zero: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise BusinessError(BAD_INPUT_CODE, f"{label} must be an integer minor unit")
    if value < 0:
        raise BusinessError(BAD_INPUT_CODE, f"{label} must be non-negative")
    if value == 0 and not allow_zero:
        raise BusinessError(BAD_INPUT_CODE, f"{label} must be greater than zero")
    return int(value)


def _key(prefix: str, *parts: Any) -> str:
    """Bounded deterministic idempotency key from arbitrary parts."""
    return prefix + ":" + fingerprint([str(part) for part in parts])[:48]


def _qualify(account: str, namespace: str | None) -> str:
    clean = _text(account, "account", max_len=120)
    if namespace is None:
        return clean
    scope = _text(namespace, "account namespace", max_len=120)
    if "|" in scope:
        raise BusinessError(
            BAD_INPUT_CODE, "account namespace must not contain '|'"
        )
    return f"{scope}:{clean}"


@dataclass(frozen=True, slots=True)
class Line:
    account: str
    direction: str
    amount_minor: int


@dataclass(frozen=True, slots=True)
class Posting:
    posting_id: str
    tenant_id: str
    business_id: str
    currency: str
    lines: tuple[Line, ...]
    memo: str
    created_at: str
    idempotency_key: str

    @property
    def debit_minor(self) -> int:
        return sum(line.amount_minor for line in self.lines if line.direction == "debit")

    @property
    def credit_minor(self) -> int:
        return sum(line.amount_minor for line in self.lines if line.direction == "credit")

    @property
    def balanced(self) -> bool:
        return self.debit_minor == self.credit_minor

    def as_dict(self) -> dict[str, Any]:
        return {
            "posting_id": self.posting_id,
            "tenant_id": self.tenant_id,
            "business_id": self.business_id,
            "currency": self.currency,
            "debit_minor": self.debit_minor,
            "credit_minor": self.credit_minor,
            "balanced": self.balanced,
            "memo": self.memo,
            "created_at": self.created_at,
            "lines": [
                {
                    "account": line.account,
                    "direction": line.direction,
                    "amount_minor": line.amount_minor,
                }
                for line in self.lines
            ],
        }


@dataclass(frozen=True, slots=True)
class ProviderReceipt:
    receipt_id: str
    intent_id: str
    tenant_id: str
    business_id: str
    provider: str
    provider_ref: str
    amount_minor: int
    currency: str
    status: str
    artifact_id: str
    created_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "intent_id": self.intent_id,
            "provider": self.provider,
            "provider_ref": self.provider_ref,
            "amount_minor": self.amount_minor,
            "currency": self.currency,
            "status": self.status,
            "artifact_id": self.artifact_id,
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class PaymentIntent:
    intent_id: str
    tenant_id: str
    business_id: str
    provider: str
    provider_idempotency_key: str
    amount_minor: int
    currency: str
    account: str
    funding_account: str
    state: str
    approval_id: str | None
    policy_version: str
    intent: dict[str, Any]
    job_id: str | None = None
    receipts: tuple[dict[str, Any], ...] = ()
    postings: tuple[str, ...] = ()
    created_at: str = ""
    updated_at: str = ""
    sequence: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "intent_id": self.intent_id,
            "tenant_id": self.tenant_id,
            "business_id": self.business_id,
            "provider": self.provider,
            "provider_idempotency_key": self.provider_idempotency_key,
            "amount_minor": self.amount_minor,
            "currency": self.currency,
            "account": self.account,
            "funding_account": self.funding_account,
            "state": self.state,
            "approval_id": self.approval_id,
            "policy_version": self.policy_version,
            "job_id": self.job_id,
            "receipts": list(self.receipts),
            "postings": list(self.postings),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "sequence": self.sequence,
        }


def _normalize_lines(lines: Iterable[Any], namespace: str | None) -> tuple[Line, ...]:
    if isinstance(lines, (str, bytes, Mapping)) or not isinstance(lines, Iterable):
        raise BusinessError(BAD_POSTING_CODE, "lines must be an iterable of lines")
    normalized: list[Line] = []
    for index, raw in enumerate(lines):
        if isinstance(raw, Line):
            account, direction, amount = raw.account, raw.direction, raw.amount_minor
        elif isinstance(raw, Mapping):
            account = raw.get("account")
            direction = raw.get("direction")
            amount = raw.get("amount_minor")
        elif isinstance(raw, (tuple, list)) and len(raw) == 3:
            account, direction, amount = raw
        else:
            raise BusinessError(
                BAD_POSTING_CODE,
                f"line {index} must be a mapping, Line or (account, direction, amount)",
            )
        side = _text(direction, f"line {index} direction", max_len=10).lower()
        if side not in DIRECTIONS:
            raise BusinessError(
                BAD_POSTING_CODE,
                f"line {index} direction must be one of {', '.join(DIRECTIONS)}",
            )
        amount_minor = _amount(amount, f"line {index} amount_minor", allow_zero=True)
        normalized.append(
            Line(_qualify(account, namespace), side, amount_minor)
        )
    if len(normalized) < 2:
        raise BusinessError(
            UNBALANCED_CODE, "a double-entry posting needs at least two lines"
        )
    debit = sum(line.amount_minor for line in normalized if line.direction == "debit")
    credit = sum(line.amount_minor for line in normalized if line.direction == "credit")
    if debit != credit:
        raise BusinessError(
            UNBALANCED_CODE,
            f"posting is unbalanced: debits {debit} != credits {credit}",
        )
    if debit == 0:
        raise BusinessError(UNBALANCED_CODE, "a posting must move a positive amount")
    return tuple(normalized)


def _posting_id(base_key: str) -> str:
    return "pst_" + fingerprint({"posting_key": base_key})[:26].lower()


def _rows_for_base(conn: sqlite3.Connection, tenant_id: str, base: str) -> list[sqlite3.Row]:
    prefix = base + "#"
    return conn.execute(
        "SELECT * FROM bos_ledger_entry WHERE tenant_id=?"
        " AND substr(idempotency_key, 1, ?)=? ORDER BY rowid",
        (tenant_id, len(prefix), prefix),
    ).fetchall()


def _posting_from_rows(rows: list[sqlite3.Row], base: str) -> Posting:
    first = rows[0]
    return Posting(
        posting_id=_posting_id(base),
        tenant_id=first["tenant_id"],
        business_id=first["business_id"],
        currency=first["currency"],
        lines=tuple(
            Line(row["account"], row["direction"], int(row["amount_minor"]))
            for row in rows
        ),
        memo=first["memo"],
        created_at=first["created_at"],
        idempotency_key=base,
    )


def post(
    store: "BusinessStore",
    *,
    tenant_id: str,
    business_id: str,
    lines: Iterable[Any],
    currency: str,
    idempotency_key: str,
    job_id: str | None = None,
    memo: str = "",
    actor: str = "system",
    namespace: str | None = None,
) -> tuple[Posting, bool]:
    """Append one balanced double-entry posting atomically.

    Returns ``(posting, created)``. The posting's ledger rows and its
    ``finance.posting.posted`` event commit in the same transaction, so a
    crash cannot leave half a posting or an event without its rows.
    """
    key = _text(idempotency_key, "idempotency key", max_len=200)
    code = _currency(currency)
    note = str(memo or "")[:2000]
    normalized = _normalize_lines(lines, namespace)
    base = _key("fin.posting", tenant_id, business_id, key)
    request_hash = fingerprint(
        {
            "business_id": business_id,
            "currency": code,
            "lines": [
                {
                    "account": line.account,
                    "direction": line.direction,
                    "amount_minor": line.amount_minor,
                }
                for line in normalized
            ],
            "job_id": job_id,
            "memo": note,
        }
    )
    posting_id = _posting_id(base)
    with store.transaction() as conn:
        store._require_business(conn, tenant_id, business_id)
        if job_id is not None:
            job = conn.execute(
                "SELECT * FROM bos_job WHERE tenant_id=? AND business_id=?"
                " AND job_id=?",
                (tenant_id, business_id, job_id),
            ).fetchone()
            if job is None:
                raise BusinessError(
                    NOT_FOUND_CODE,
                    f"no job {job_id!r} for this tenant/business",
                )
        existing = conn.execute(
            "SELECT * FROM bos_ledger_entry WHERE tenant_id=? AND idempotency_key=?",
            (tenant_id, f"{base}#0"),
        ).fetchone()
        if existing is not None:
            if existing["request_hash"] != request_hash:
                raise BusinessError(
                    PAYMENT_IDEMPOTENCY_CODE,
                    f"idempotency key {key!r} was already used for a different"
                    " posting",
                )
            rows = _rows_for_base(conn, tenant_id, base)
            if len(rows) != len(normalized):
                raise BusinessError(
                    BAD_STATE_CODE,
                    "stored posting does not match its idempotency key",
                )
            return _posting_from_rows(rows, base), False
        created_at = now_iso()
        entry_ids: list[str] = []
        for index, line in enumerate(normalized):
            entry_id = new_id("lgr")
            entry_ids.append(entry_id)
            conn.execute(
                "INSERT INTO bos_ledger_entry(entry_id, tenant_id, business_id,"
                " job_id, account, direction, amount_minor, currency,"
                " idempotency_key, request_hash, memo, created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    entry_id,
                    tenant_id,
                    business_id,
                    job_id,
                    line.account,
                    line.direction,
                    line.amount_minor,
                    code,
                    f"{base}#{index}",
                    request_hash,
                    note,
                    created_at,
                ),
            )
        eventlog.append_event(
            store,
            tenant_id,
            posting_id,
            "finance.posting.posted",
            {
                "posting_id": posting_id,
                "business_id": business_id,
                "currency": code,
                "entry_ids": entry_ids,
                "lines": [
                    {
                        "account": line.account,
                        "direction": line.direction,
                        "amount_minor": line.amount_minor,
                    }
                    for line in normalized
                ],
                "idempotency_key": base,
                "job_id": job_id,
            },
            actor=actor,
            idempotency_key=_key("fin.posting.event", tenant_id, base),
            business_id=business_id,
            conn=conn,
        )
        store._audit(
            conn, tenant_id, business_id, actor, "finance.posting",
            "posting", posting_id, "ok",
            {"currency": code, "entry_count": len(normalized)},
        )
        rows = _rows_for_base(conn, tenant_id, base)
        return _posting_from_rows(rows, base), True


def get_posting(store: "BusinessStore", tenant_id: str, posting_id: str) -> Posting:
    events = eventlog.read_events(store, tenant_id, posting_id)
    for envelope in events:
        if envelope.type == "finance.posting.posted":
            base = str(envelope.payload.get("idempotency_key") or "")
            if not base:
                break
            with store.read() as conn:
                rows = _rows_for_base(conn, tenant_id, base)
            if not rows:
                raise BusinessError(
                    POSTING_NOT_FOUND_CODE,
                    f"posting {posting_id!r} has no ledger rows",
                )
            return _posting_from_rows(rows, base)
    raise BusinessError(
        POSTING_NOT_FOUND_CODE, f"no posting {posting_id!r} in tenant {tenant_id!r}"
    )


def _account_totals(
    store: "BusinessStore",
    tenant_id: str,
    *,
    business_id: str | None = None,
    currency: str | None = None,
    namespace: str | None = None,
    period: str | None = None,
) -> dict[str, Any]:
    query = "SELECT * FROM bos_ledger_entry WHERE tenant_id=?"
    params: list[Any] = [tenant_id]
    if business_id is not None:
        query += " AND business_id=?"
        params.append(business_id)
    if currency is not None:
        query += " AND currency=?"
        params.append(_currency(currency))
    if period is not None:
        query += " AND created_at LIKE ?"
        params.append(f"{period}%")
    query += " ORDER BY created_at, entry_id"
    if namespace is not None:
        query += ""
    debit_minor = 0
    credit_minor = 0
    count = 0
    by_account: dict[str, dict[str, int]] = {}
    with store.read() as conn:
        for row in conn.execute(query, params):
            account = str(row["account"])
            if namespace is not None and not account.startswith(f"{namespace}:"):
                continue
            amount = int(row["amount_minor"])
            entry = by_account.setdefault(
                account, {"debit_minor": 0, "credit_minor": 0}
            )
            if row["direction"] == "debit":
                debit_minor += amount
                entry["debit_minor"] += amount
            else:
                credit_minor += amount
                entry["credit_minor"] += amount
            count += 1
    for entry in by_account.values():
        entry["net_minor"] = entry["credit_minor"] - entry["debit_minor"]
    return {
        "tenant_id": tenant_id,
        "business_id": business_id,
        "currency": currency.upper() if currency else None,
        "period": period,
        "namespace": namespace,
        "debit_minor": debit_minor,
        "credit_minor": credit_minor,
        "net_minor": credit_minor - debit_minor,
        "entry_count": count,
        "balanced": debit_minor == credit_minor,
        "by_account": by_account,
    }


def summary(
    store: "BusinessStore",
    tenant_id: str,
    *,
    business_id: str | None = None,
    currency: str | None = None,
    namespace: str | None = None,
) -> dict[str, Any]:
    return _account_totals(
        store,
        tenant_id,
        business_id=business_id,
        currency=currency,
        namespace=namespace,
    )


def period_summary(
    store: "BusinessStore",
    tenant_id: str,
    period: str,
    *,
    business_id: str | None = None,
    currency: str | None = None,
    namespace: str | None = None,
) -> dict[str, Any]:
    """Summarise one calendar month (``YYYY-MM``) from ledger rows."""
    clean = _text(period, "period", max_len=20)
    parts = clean.split("-")
    if (
        len(parts) != 2
        or len(parts[0]) != 4
        or len(parts[1]) != 2
        or not all(part.isdigit() for part in parts)
    ):
        raise BusinessError(BAD_INPUT_CODE, "period must look like YYYY-MM")
    return _account_totals(
        store,
        tenant_id,
        business_id=business_id,
        currency=currency,
        namespace=namespace,
        period=clean,
    )


# -- payment intents ------------------------------------------------------
def _intent_id(tenant_id: str, business_id: str, provider: str, key: str) -> str:
    return "pay_" + fingerprint(
        {
            "tenant_id": tenant_id,
            "business_id": business_id,
            "provider": provider,
            "provider_idempotency_key": key,
        }
    )[:26].lower()


def _payment_events(store: "BusinessStore", tenant_id: str, intent_id: str) -> list:
    return eventlog.read_events(store, tenant_id, intent_id)


def _intent_from_events(events: list) -> PaymentIntent | None:
    base: dict[str, Any] | None = None
    state = ""
    approval_id: str | None = None
    receipts: list[dict[str, Any]] = []
    postings: list[str] = []
    created_at = ""
    updated_at = ""
    sequence = 0
    for envelope in events:
        sequence = max(sequence, int(envelope.sequence))
        updated_at = envelope.occurred_at
        payload = envelope.payload if isinstance(envelope.payload, Mapping) else {}
        kind = envelope.type
        if kind == "finance.payment.created":
            base = dict(payload)
            state = STATE_PENDING
            approval_id = str(payload.get("approval_id") or "") or None
            created_at = envelope.occurred_at
        elif kind == "finance.payment.sent":
            state = STATE_SENT
        elif kind == "finance.payment.unknown":
            state = STATE_UNKNOWN
        elif kind == "finance.payment.acked":
            state = STATE_ACKED
        elif kind == "finance.payment.completed":
            state = STATE_COMPLETED
            posting_id = payload.get("posting_id")
            if posting_id and str(posting_id) not in postings:
                postings.append(str(posting_id))
        elif kind == "finance.payment.failed":
            state = STATE_FAILED
        elif kind == "finance.payment.compensated":
            state = STATE_COMPENSATED
        elif kind == "finance.payment.resolved":
            outcome = str(payload.get("outcome") or "")
            if outcome in UNKNOWN_RESOLUTIONS:
                state = outcome
        elif kind == "finance.receipt.recorded":
            receipts.append(dict(payload))
        elif kind in (
            "finance.payment.refund",
            "finance.payment.chargeback",
            "finance.payment.payout",
        ):
            posting_id = payload.get("posting_id")
            if posting_id and str(posting_id) not in postings:
                postings.append(str(posting_id))
        # A note (finance.payment.note) deliberately changes nothing.
    if base is None:
        return None
    return PaymentIntent(
        intent_id=str(base.get("intent_id") or ""),
        tenant_id=str(base.get("tenant_id") or ""),
        business_id=str(base.get("business_id") or ""),
        provider=str(base.get("provider") or ""),
        provider_idempotency_key=str(base.get("provider_idempotency_key") or ""),
        amount_minor=int(base.get("amount_minor") or 0),
        currency=str(base.get("currency") or ""),
        account=str(base.get("account") or ""),
        funding_account=str(base.get("funding_account") or "cash"),
        state=state,
        approval_id=approval_id,
        policy_version=str(base.get("policy_version") or "v1"),
        intent=dict(base.get("intent") or {}),
        job_id=base.get("job_id"),
        receipts=tuple(receipts),
        postings=tuple(postings),
        created_at=created_at,
        updated_at=updated_at,
        sequence=sequence,
    )


def get_payment(store: "BusinessStore", tenant_id: str, intent_id: str) -> PaymentIntent:
    clean = _text(intent_id, "intent id", max_len=80)
    intent = _intent_from_events(_payment_events(store, tenant_id, clean))
    if intent is None:
        raise BusinessError(
            PAYMENT_NOT_FOUND_CODE,
            f"no payment intent {clean!r} in tenant {tenant_id!r}",
        )
    return intent


def list_payments(
    store: "BusinessStore",
    tenant_id: str,
    *,
    business_id: str | None = None,
) -> list[PaymentIntent]:
    with store.read() as conn:
        store._require_tenant(conn, tenant_id)
        rows = conn.execute(
            "SELECT DISTINCT aggregate_id FROM bos_event WHERE tenant_id=?"
            " AND type='finance.payment.created' ORDER BY occurred_at, aggregate_id",
            (tenant_id,),
        ).fetchall()
    intents: list[PaymentIntent] = []
    for row in rows:
        intent = _intent_from_events(
            _payment_events(store, tenant_id, str(row["aggregate_id"]))
        )
        if intent is None:
            continue
        if business_id is not None and intent.business_id != business_id:
            continue
        intents.append(intent)
    return intents


def request_money_approval(
    store: "BusinessStore",
    *,
    tenant_id: str,
    business_id: str,
    action: str,
    target: str,
    amount_minor: int,
    currency: str,
    requested_by: str,
    policy_version: str = "v1",
    reason: str = "",
    idempotency_key: str | None = None,
    parameters: Mapping[str, Any] | None = None,
) -> approvals.models.Approval:
    """Create (or replay) the pending approval a money action needs."""
    act = _text(action, "approval action", max_len=200)
    if act not in _MONEY_ACTIONS:
        raise BusinessError(BAD_INPUT_CODE, f"unknown money action {act!r}")
    amount = _amount(amount_minor, "amount_minor")
    code = _currency(currency)
    params = dict(parameters) if parameters is not None else {}
    params.setdefault("business_id", business_id)
    intent = approvals.normalized_intent(
        act,
        _text(target, "approval target", max_len=500),
        params,
        code,
        amount,
        policy_version,
        business_id=business_id,
    )
    key = idempotency_key or _key(
        "fin.approval", action, business_id, target, amount, code, policy_version
    )
    approval, _created = approvals.request(
        store,
        tenant_id=tenant_id,
        business_id=business_id,
        action=act,
        requested_by=requested_by,
        idempotency_key=key,
        payload=intent,
        policy_version=policy_version,
    )
    return approval


def _authorize_money(
    store: "BusinessStore",
    *,
    tenant_id: str,
    business_id: str,
    action: str,
    target: str,
    amount_minor: int,
    currency: str,
    approval_id: str | None,
    requested_by: str,
    policy_version: str = "v1",
    reason: str = "",
) -> Any:
    step_up = identity.require_step_up(action, "L4")
    intent = approvals.normalized_intent(
        action,
        target,
        {"business_id": business_id},
        _currency(currency),
        _amount(amount_minor, "amount_minor"),
        policy_version,
        business_id=business_id,
    )
    if approval_id is None:
        approval = request_money_approval(
            store,
            tenant_id=tenant_id,
            business_id=business_id,
            action=action,
            target=target,
            amount_minor=amount_minor,
            currency=currency,
            requested_by=requested_by,
            policy_version=policy_version,
            reason=reason,
        )
        raise BusinessError(
            PAYMENT_APPROVAL_REQUIRED_CODE,
            f"{action} requires human approval; approval {approval.approval_id}"
            " was requested"
            + (f" ({step_up.reason})" if step_up.required else ""),
        )
    return approvals.execution_authorized(
        store,
        tenant_id,
        _text(approval_id, "approval id", max_len=80),
        intent=intent,
        policy_version=policy_version,
    )


def create_payment_intent(
    store: "BusinessStore",
    *,
    tenant_id: str,
    business_id: str,
    provider: str,
    provider_idempotency_key: str,
    amount_minor: int,
    currency: str,
    account: str,
    funding_account: str = "cash",
    memo: str = "",
    actor: str = "system",
    policy_version: str = "v1",
    job_id: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> tuple[PaymentIntent, bool]:
    """Create (or replay) a payment intent and its pending approval.

    Provider idempotency is the intent's identity: the same provider key and
    body returns the original intent; the same key with a different amount or
    currency is a typed conflict. The intent always starts ``pending`` and
    carries the approval it needs before any money can move.
    """
    store.require_business(tenant_id, business_id)
    linked_job = None
    if job_id is not None:
        linked_job = _text(job_id, "job id", max_len=80)
        with store.read() as conn:
            row = conn.execute(
                "SELECT 1 FROM bos_job WHERE tenant_id=? AND business_id=?"
                " AND job_id=?",
                (tenant_id, business_id, linked_job),
            ).fetchone()
        if row is None:
            raise BusinessError(
                NOT_FOUND_CODE,
                f"no job {linked_job!r} for this tenant/business",
            )
    provider_clean = _text(provider, "provider", max_len=120)
    pkey = _text(provider_idempotency_key, "provider idempotency key", max_len=200)
    amount = _amount(amount_minor, "amount_minor")
    code = _currency(currency)
    account_clean = _text(account, "account", max_len=120)
    funding_clean = _text(funding_account, "funding account", max_len=120)
    note = str(memo or "")[:2000]
    meta = dict(metadata or {})
    intent_id = _intent_id(tenant_id, business_id, provider_clean, pkey)
    intent_payload = approvals.normalized_intent(
        "finance.payment",
        f"{provider_clean}:{pkey}",
        {
            "provider": provider_clean,
            "provider_idempotency_key": pkey,
            "account": account_clean,
            "business_id": business_id,
        },
        code,
        amount,
        policy_version,
        business_id=business_id,
    )
    approval = request_money_approval(
        store,
        tenant_id=tenant_id,
        business_id=business_id,
        action="finance.payment",
        target=f"{provider_clean}:{pkey}",
        amount_minor=amount,
        currency=code,
        requested_by=actor,
        policy_version=policy_version,
        reason=note,
        parameters={
            "provider": provider_clean,
            "provider_idempotency_key": pkey,
            "account": account_clean,
            "business_id": business_id,
        },
    )
    payload = {
        "intent_id": intent_id,
        "tenant_id": tenant_id,
        "business_id": business_id,
        "provider": provider_clean,
        "provider_idempotency_key": pkey,
        "amount_minor": amount,
        "currency": code,
        "account": account_clean,
        "funding_account": funding_clean,
        "memo": note,
        "metadata": redact(meta),
        "approval_id": approval.approval_id,
        "policy_version": policy_version,
        "job_id": linked_job,
        "intent": intent_payload,
    }
    created = not any(
        item.type == "finance.payment.created"
        for item in _payment_events(store, tenant_id, intent_id)
    )
    eventlog.append_event(
        store,
        tenant_id,
        intent_id,
        "finance.payment.created",
        payload,
        actor=actor,
        idempotency_key=_key(
            "fin.pay.create", business_id, provider_clean, pkey
        ),
        business_id=business_id,
    )
    return get_payment(store, tenant_id, intent_id), created


def _append_state(
    store: "BusinessStore",
    intent: PaymentIntent,
    event_type: str,
    payload: Mapping[str, Any],
    *,
    actor: str,
    key: str,
) -> PaymentIntent:
    eventlog.append_event(
        store,
        intent.tenant_id,
        intent.intent_id,
        event_type,
        dict(payload),
        actor=actor,
        idempotency_key=key,
        business_id=intent.business_id,
    )
    return get_payment(store, intent.tenant_id, intent.intent_id)


def execute_payment(
    store: "BusinessStore",
    *,
    tenant_id: str,
    intent_id: str,
    approval_id: str | None = None,
    actor: str = "system",
    policy_version: str | None = None,
    now: float | None = None,
) -> PaymentIntent:
    """Re-authorize the approved intent and record it as ``sent``."""
    intent = get_payment(store, tenant_id, intent_id)
    if intent.state in (STATE_SENT, STATE_ACKED, STATE_COMPLETED):
        return intent
    if intent.state != STATE_PENDING:
        raise BusinessError(
            PAYMENT_BAD_STATE_CODE,
            f"payment {intent.intent_id} cannot be sent from {intent.state!r}",
        )
    approval = approval_id or intent.approval_id
    if not approval:
        raise BusinessError(
            PAYMENT_APPROVAL_REQUIRED_CODE,
            "payment requires a human approval before execution",
        )
    approvals.execution_authorized(
        store,
        tenant_id,
        approval,
        intent=intent.intent,
        policy_version=policy_version or intent.policy_version,
        now=now,
    )
    return _append_state(
        store,
        intent,
        "finance.payment.sent",
        {"intent_id": intent.intent_id, "approval_id": approval},
        actor=actor,
        key=_key("fin.pay.sent", intent.intent_id),
    )


def mark_payment_unknown(
    store: "BusinessStore",
    *,
    tenant_id: str,
    intent_id: str,
    actor: str = "system",
    reason: str = "",
) -> PaymentIntent:
    """Record an unconfirmed outcome; never guess that money moved."""
    intent = get_payment(store, tenant_id, intent_id)
    if intent.state == STATE_UNKNOWN:
        return intent
    if intent.state not in (STATE_PENDING, STATE_SENT):
        raise BusinessError(
            PAYMENT_BAD_STATE_CODE,
            f"payment {intent.intent_id} cannot become unknown from"
            f" {intent.state!r}",
        )
    return _append_state(
        store,
        intent,
        "finance.payment.unknown",
        {"intent_id": intent.intent_id, "reason": str(reason or "")[:2000]},
        actor=actor,
        key=_key("fin.pay.unknown", intent.intent_id),
    )


def mark_payment_failed(
    store: "BusinessStore",
    *,
    tenant_id: str,
    intent_id: str,
    actor: str = "system",
    reason: str = "",
) -> PaymentIntent:
    intent = get_payment(store, tenant_id, intent_id)
    if intent.state == STATE_FAILED:
        return intent
    if intent.state not in (STATE_PENDING, STATE_SENT):
        raise BusinessError(
            PAYMENT_BAD_STATE_CODE,
            f"payment {intent.intent_id} cannot fail from {intent.state!r};"
            " an unknown outcome must be resolved explicitly",
        )
    return _append_state(
        store,
        intent,
        "finance.payment.failed",
        {"intent_id": intent.intent_id, "reason": str(reason or "")[:2000]},
        actor=actor,
        key=_key("fin.pay.failed", intent.intent_id),
    )


def _receipt_id(tenant_id: str, provider: str, provider_ref: str) -> str:
    return "rcp_" + fingerprint(
        {"tenant_id": tenant_id, "provider": provider, "provider_ref": provider_ref}
    )[:26].lower()


def record_provider_receipt(
    store: "BusinessStore",
    *,
    tenant_id: str,
    intent_id: str,
    provider: str,
    provider_ref: str,
    amount_minor: int,
    currency: str,
    status: str = "acked",
    raw: Any = None,
    actor: str = "system",
) -> ProviderReceipt:
    """Store one immutable provider receipt for an existing intent."""
    intent = get_payment(store, tenant_id, intent_id)
    provider_clean = _text(provider, "provider", max_len=120)
    ref_clean = _text(provider_ref, "provider reference", max_len=200)
    amount = _amount(amount_minor, "receipt amount_minor", allow_zero=True)
    code = _currency(currency)
    status_clean = _text(status, "receipt status", max_len=40)
    payload = {
        "receipt_id": _receipt_id(tenant_id, provider_clean, ref_clean),
        "intent_id": intent.intent_id,
        "provider": provider_clean,
        "provider_ref": ref_clean,
        "amount_minor": amount,
        "currency": code,
        "status": status_clean,
        "raw": redact(raw if raw is not None else {}),
    }
    name = f"provider_receipt:{provider_clean}:{ref_clean}"[:300]
    artifact = None
    with store.read() as conn:
        row = conn.execute(
            "SELECT * FROM bos_artifact WHERE tenant_id=? AND business_id=?"
            " AND kind='provider_receipt' AND name=?",
            (tenant_id, intent.business_id, name),
        ).fetchone()
        if row is not None:
            artifact = dict(row)
    if artifact is None:
        record = store.record_artifact(
            tenant_id,
            intent.business_id,
            kind="provider_receipt",
            name=name,
            body=json_dumps(payload),
            media_type="application/json",
        )
        artifact_id = record.artifact_id
        created_at = record.created_at
    else:
        artifact_id = str(artifact["artifact_id"])
        created_at = str(artifact["created_at"])
    stored = dict(payload, artifact_id=artifact_id)
    eventlog.append_event(
        store,
        tenant_id,
        intent.intent_id,
        "finance.receipt.recorded",
        stored,
        actor=actor,
        idempotency_key=_key(
            "fin.receipt", intent.business_id, provider_clean, ref_clean
        ),
        business_id=intent.business_id,
    )
    return ProviderReceipt(
        receipt_id=str(payload["receipt_id"]),
        intent_id=intent.intent_id,
        tenant_id=tenant_id,
        business_id=intent.business_id,
        provider=provider_clean,
        provider_ref=ref_clean,
        amount_minor=amount,
        currency=code,
        status=status_clean,
        artifact_id=artifact_id,
        created_at=created_at,
    )


def acknowledge_payment(
    store: "BusinessStore",
    *,
    tenant_id: str,
    intent_id: str,
    receipt: Mapping[str, Any],
    actor: str = "system",
) -> PaymentIntent:
    """Record the provider receipt that proves the payment was accepted."""
    intent = get_payment(store, tenant_id, intent_id)
    if intent.state == STATE_ACKED:
        return intent
    if intent.state != STATE_SENT:
        raise BusinessError(
            PAYMENT_BAD_STATE_CODE,
            f"payment {intent.intent_id} cannot be acknowledged from"
            f" {intent.state!r}",
        )
    stored = _record_receipt_mapping(store, intent, receipt, actor)
    return _append_state(
        store,
        intent,
        "finance.payment.acked",
        {"intent_id": intent.intent_id, "receipt_id": stored.receipt_id},
        actor=actor,
        key=_key("fin.pay.acked", intent.intent_id),
    )


def _record_receipt_mapping(
    store: "BusinessStore",
    intent: PaymentIntent,
    receipt: Mapping[str, Any],
    actor: str,
) -> ProviderReceipt:
    if not isinstance(receipt, Mapping):
        raise BusinessError(BAD_INPUT_CODE, "a provider receipt must be a mapping")
    return record_provider_receipt(
        store,
        tenant_id=intent.tenant_id,
        intent_id=intent.intent_id,
        provider=str(receipt.get("provider") or intent.provider),
        provider_ref=str(receipt.get("provider_ref") or receipt.get("id") or ""),
        amount_minor=int(receipt.get("amount_minor", intent.amount_minor)),
        currency=str(receipt.get("currency") or intent.currency),
        status=str(receipt.get("status") or "acked"),
        raw=receipt.get("raw"),
        actor=actor,
    )


def complete_payment(
    store: "BusinessStore",
    *,
    tenant_id: str,
    intent_id: str,
    actor: str = "system",
    idempotency_key: str | None = None,
) -> PaymentIntent:
    """Post the balanced double-entry ledger movement for an acknowledged payment."""
    intent = get_payment(store, tenant_id, intent_id)
    if intent.state == STATE_COMPLETED:
        return intent
    if intent.state != STATE_ACKED:
        raise BusinessError(
            PAYMENT_BAD_STATE_CODE,
            f"payment {intent.intent_id} cannot complete from {intent.state!r};"
            " provider acknowledgement is required",
        )
    posting_key = idempotency_key or _key("fin.pay.posting", intent.intent_id)
    posting, _created = post(
        store,
        tenant_id=tenant_id,
        business_id=intent.business_id,
        lines=[
            (
                intent.account,
                "debit",
                intent.amount_minor,
            ),
            (
                intent.funding_account,
                "credit",
                intent.amount_minor,
            ),
        ],
        currency=intent.currency,
        idempotency_key=posting_key,
        memo=f"payment {intent.intent_id}",
        actor=actor,
    )
    return _append_state(
        store,
        intent,
        "finance.payment.completed",
        {"intent_id": intent.intent_id, "posting_id": posting.posting_id},
        actor=actor,
        key=_key("fin.pay.completed", intent.intent_id),
    )


def resolve_unknown(
    store: "BusinessStore",
    *,
    tenant_id: str,
    intent_id: str,
    outcome: str,
    receipt: Mapping[str, Any] | None = None,
    actor: str = "system",
    reason: str = "",
) -> PaymentIntent:
    """Explicitly resolve an ``unknown`` payment from provider evidence.

    ``sent`` and ``compensated`` require a provider receipt because they claim
    something happened to the money. ``failed`` may be resolved from a
    provider refusal. A narrative alone is refused.
    """
    clean = _text(outcome, "resolution", max_len=20).lower()
    if clean not in UNKNOWN_RESOLUTIONS:
        raise BusinessError(
            BAD_INPUT_CODE,
            f"resolution must be one of {', '.join(UNKNOWN_RESOLUTIONS)}",
        )
    intent = get_payment(store, tenant_id, intent_id)
    if intent.state != STATE_UNKNOWN:
        raise BusinessError(
            PAYMENT_BAD_STATE_CODE,
            f"only an unknown payment can be resolved (state={intent.state!r})",
        )
    if clean in (STATE_SENT, STATE_COMPENSATED) and receipt is None:
        raise BusinessError(
            NARRATIVE_NOT_TRUTH_CODE,
            f"resolving to {clean!r} requires a provider receipt;"
            " a narrative is not financial truth",
        )
    stored = _record_receipt_mapping(store, intent, receipt, actor) if receipt else None
    return _append_state(
        store,
        intent,
        "finance.payment.resolved",
        {
            "intent_id": intent.intent_id,
            "outcome": clean,
            "receipt_id": stored.receipt_id if stored else None,
            "reason": str(reason or "")[:2000],
        },
        actor=actor,
        key=_key("fin.pay.resolve", intent.intent_id),
    )


def record_narrative(
    store: "BusinessStore",
    *,
    tenant_id: str,
    intent_id: str,
    text: str,
    actor: str = "system",
) -> PaymentIntent:
    """Attach a note. A note can never change a payment's state."""
    intent = get_payment(store, tenant_id, intent_id)
    body = _text(text, "narrative", max_len=4000)
    eventlog.append_event(
        store,
        tenant_id,
        intent.intent_id,
        "finance.payment.note",
        {"intent_id": intent.intent_id, "text": body},
        actor=actor,
        idempotency_key=_key("fin.pay.note", intent.intent_id, body),
        business_id=intent.business_id,
    )
    return get_payment(store, tenant_id, intent.intent_id)


def _post_money_move(
    store: "BusinessStore",
    *,
    intent: PaymentIntent,
    action: str,
    amount_minor: int,
    debit_account: str,
    credit_account: str,
    idempotency_key: str,
    actor: str,
    memo: str,
) -> Posting:
    posting, _created = post(
        store,
        tenant_id=intent.tenant_id,
        business_id=intent.business_id,
        lines=[
            (debit_account, "debit", amount_minor),
            (credit_account, "credit", amount_minor),
        ],
        currency=intent.currency,
        idempotency_key=idempotency_key,
        memo=memo,
        actor=actor,
    )
    eventlog.append_event(
        store,
        intent.tenant_id,
        intent.intent_id,
        f"finance.payment.{action}",
        {
            "intent_id": intent.intent_id,
            "posting_id": posting.posting_id,
            "amount_minor": amount_minor,
            "currency": intent.currency,
        },
        actor=actor,
        idempotency_key=_key(f"fin.payment.{action}", intent.intent_id, idempotency_key),
        business_id=intent.business_id,
    )
    return posting


def record_refund(
    store: "BusinessStore",
    *,
    tenant_id: str,
    intent_id: str,
    amount_minor: int,
    approval_id: str | None,
    actor: str = "system",
    idempotency_key: str | None = None,
    refund_account: str = "expense:refunds",
    funding_account: str = "cash",
    reason: str = "",
    policy_version: str = "v1",
) -> Posting:
    """Post a balanced refund (debit refund expense, credit cash)."""
    intent = get_payment(store, tenant_id, intent_id)
    amount = _amount(amount_minor, "refund amount_minor")
    if amount > intent.amount_minor:
        raise BusinessError(
            BAD_INPUT_CODE, "a refund may not exceed the original payment amount"
        )
    _authorize_money(
        store,
        tenant_id=tenant_id,
        business_id=intent.business_id,
        action="finance.refund",
        target=intent.intent_id,
        amount_minor=amount,
        currency=intent.currency,
        approval_id=approval_id,
        requested_by=actor,
        policy_version=policy_version,
        reason=reason,
    )
    key = _text(idempotency_key or _key(
        "fin.refund", intent.intent_id, amount, reason
    ), "idempotency key", max_len=200)
    return _post_money_move(
        store,
        intent=intent,
        action="refund",
        amount_minor=amount,
        debit_account=refund_account,
        credit_account=funding_account,
        idempotency_key=key,
        actor=actor,
        memo=f"refund {intent.intent_id}",
    )


def record_chargeback(
    store: "BusinessStore",
    *,
    tenant_id: str,
    intent_id: str,
    amount_minor: int,
    approval_id: str | None,
    actor: str = "system",
    idempotency_key: str | None = None,
    chargeback_account: str = "expense:chargebacks",
    funding_account: str = "cash",
    reason: str = "",
    policy_version: str = "v1",
) -> Posting:
    """Post a balanced chargeback (debit chargeback loss, credit cash)."""
    intent = get_payment(store, tenant_id, intent_id)
    amount = _amount(amount_minor, "chargeback amount_minor")
    _authorize_money(
        store,
        tenant_id=tenant_id,
        business_id=intent.business_id,
        action="finance.chargeback",
        target=intent.intent_id,
        amount_minor=amount,
        currency=intent.currency,
        approval_id=approval_id,
        requested_by=actor,
        policy_version=policy_version,
        reason=reason,
    )
    key = _text(idempotency_key or _key(
        "fin.chargeback", intent.intent_id, amount, reason
    ), "idempotency key", max_len=200)
    return _post_money_move(
        store,
        intent=intent,
        action="chargeback",
        amount_minor=amount,
        debit_account=chargeback_account,
        credit_account=funding_account,
        idempotency_key=key,
        actor=actor,
        memo=f"chargeback {intent.intent_id}",
    )


def record_payout(
    store: "BusinessStore",
    *,
    tenant_id: str,
    business_id: str,
    reference: str,
    amount_minor: int,
    currency: str,
    approval_id: str | None,
    actor: str = "system",
    idempotency_key: str | None = None,
    draw_account: str = "equity:draw",
    funding_account: str = "cash",
    reason: str = "",
    policy_version: str = "v1",
) -> Posting:
    """Post a balanced owner payout (debit equity draw, credit cash)."""
    amount = _amount(amount_minor, "payout amount_minor")
    code = _currency(currency)
    _authorize_money(
        store,
        tenant_id=tenant_id,
        business_id=business_id,
        action="finance.payout",
        target=_text(reference, "payout reference", max_len=200),
        amount_minor=amount,
        currency=code,
        approval_id=approval_id,
        requested_by=actor,
        policy_version=policy_version,
        reason=reason,
    )
    key = _text(idempotency_key or _key(
        "fin.payout", business_id, reference, amount, code
    ), "idempotency key", max_len=200)
    posting, _created = post(
        store,
        tenant_id=tenant_id,
        business_id=business_id,
        lines=[
            (draw_account, "debit", amount),
            (funding_account, "credit", amount),
        ],
        currency=code,
        idempotency_key=key,
        memo=f"payout {reference}",
        actor=actor,
    )
    payout_id = "pout_" + fingerprint(
        {"tenant_id": tenant_id, "business_id": business_id, "reference": reference}
    )[:26].lower()
    eventlog.append_event(
        store,
        tenant_id,
        payout_id,
        "finance.payout.recorded",
        {
            "payout_id": payout_id,
            "business_id": business_id,
            "reference": reference,
            "posting_id": posting.posting_id,
            "amount_minor": amount,
            "currency": code,
        },
        actor=actor,
        idempotency_key=_key("fin.payout.event", business_id, reference, key),
        business_id=business_id,
    )
    return posting


def reconcile(
    store: "BusinessStore",
    tenant_id: str,
    *,
    business_id: str | None = None,
) -> dict[str, Any]:
    """Compare every payment intent with the provider receipts on record.

    It never turns an unknown outcome into a completed one. A completed
    payment without a matching receipt is reported as a mismatch.
    """
    intents = list_payments(store, tenant_id, business_id=business_id)
    matched: list[dict[str, Any]] = []
    mismatched: list[dict[str, Any]] = []
    unknown: list[dict[str, Any]] = []
    for intent in intents:
        receivables = [
            receipt
            for receipt in intent.receipts
            if str(receipt.get("status", "")).lower()
            in ("acked", "captured", "settled", "received", "completed")
        ]
        total = sum(int(receipt.get("amount_minor") or 0) for receipt in receivables)
        item = {
            "intent_id": intent.intent_id,
            "business_id": intent.business_id,
            "state": intent.state,
            "amount_minor": intent.amount_minor,
            "currency": intent.currency,
            "receipt_total_minor": total,
            "receipt_count": len(receivables),
        }
        if intent.state == STATE_UNKNOWN:
            unknown.append(item)
        elif intent.state in (STATE_ACKED, STATE_COMPLETED):
            if total == intent.amount_minor:
                matched.append(item)
            else:
                item["reason"] = "receipt total does not match the intent amount"
                mismatched.append(item)
        elif receivables and total != intent.amount_minor:
            item["reason"] = "receipt total does not match the intent amount"
            mismatched.append(item)
    ledger = summary(
        store,
        tenant_id,
        business_id=business_id,
    )
    return {
        "tenant_id": tenant_id,
        "business_id": business_id,
        "checked": len(intents),
        "matched": matched,
        "mismatched": mismatched,
        "unknown": unknown,
        "ledger_balanced": ledger["balanced"],
        "ledger": ledger,
        "ok": not mismatched and ledger["balanced"] and not unknown,
    }


__all__ = [
    "BAD_POSTING_CODE",
    "ENTITY_MISMATCH_CODE",
    "Line",
    "NARRATIVE_NOT_TRUTH_CODE",
    "PAYMENT_APPROVAL_REQUIRED_CODE",
    "PAYMENT_BAD_STATE_CODE",
    "PAYMENT_IDEMPOTENCY_CODE",
    "PAYMENT_NOT_FOUND_CODE",
    "PAYMENT_STATES",
    "PAYMENT_UNKNOWN_UNRESOLVED_CODE",
    "POSTING_NOT_FOUND_CODE",
    "PaymentIntent",
    "Posting",
    "ProviderReceipt",
    "RECONCILIATION_MISMATCH_CODE",
    "STATE_ACKED",
    "STATE_COMPENSATED",
    "STATE_COMPLETED",
    "STATE_FAILED",
    "STATE_PENDING",
    "STATE_SENT",
    "STATE_UNKNOWN",
    "UNBALANCED_CODE",
    "UNKNOWN_RESOLUTIONS",
    "acknowledge_payment",
    "complete_payment",
    "create_payment_intent",
    "execute_payment",
    "get_payment",
    "get_posting",
    "list_payments",
    "mark_payment_failed",
    "mark_payment_unknown",
    "period_summary",
    "post",
    "reconcile",
    "record_chargeback",
    "record_narrative",
    "record_payout",
    "record_provider_receipt",
    "record_refund",
    "request_money_approval",
    "resolve_unknown",
    "summary",
]

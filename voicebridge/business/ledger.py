"""Append-only business ledger.

Money is integer minor units and enters only through :func:`append`. The table
has database triggers that abort ``UPDATE`` and ``DELETE``, so a wrong entry is
corrected by a **compensating row**, never by editing history.

Idempotency: ``(tenant_id, idempotency_key)`` is unique. Replaying a key with
the same request returns the original row and reports ``created=False``; the
same key with a different body is a typed conflict.
"""
from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any

from . import models
from .errors import (
    BAD_INPUT_CODE,
    BAD_STATE_CODE,
    IDEMPOTENCY_CONFLICT_CODE,
    NOT_FOUND_CODE,
    BusinessError,
)
from .ids import fingerprint, new_id
from .util import now_iso

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .store import BusinessStore

DIRECTIONS = ("debit", "credit")


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


def append(
    store: "BusinessStore",
    *,
    tenant_id: str,
    business_id: str,
    account: str,
    direction: str,
    amount_minor: int,
    currency: str,
    idempotency_key: str,
    job_id: str | None = None,
    memo: str = "",
    actor: str = "system",
) -> tuple[models.LedgerEntry, bool]:
    """Append one immutable ledger row; returns ``(entry, created)``."""
    account_clean = _text(account, "account", max_len=120)
    side = _text(direction, "direction", max_len=10).lower()
    if side not in DIRECTIONS:
        raise BusinessError(
            BAD_INPUT_CODE,
            f"direction must be one of {', '.join(DIRECTIONS)}",
        )
    if (
        not isinstance(amount_minor, int)
        or isinstance(amount_minor, bool)
        or amount_minor < 0
    ):
        raise BusinessError(
            BAD_INPUT_CODE,
            "amount_minor must be a non-negative integer (money is never a float)",
        )
    code = _currency(currency)
    key = _text(idempotency_key, "idempotency key", max_len=300)
    note = str(memo or "")[:2000]
    request_hash = fingerprint(
        {
            "account": account_clean,
            "direction": side,
            "amount_minor": amount_minor,
            "currency": code,
            "job_id": job_id,
            "memo": note,
        }
    )
    entry_id = new_id("lgr")
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
            (tenant_id, key),
        ).fetchone()
        if existing is not None:
            if existing["request_hash"] != request_hash:
                raise BusinessError(
                    IDEMPOTENCY_CONFLICT_CODE,
                    f"idempotency key {key!r} was already used for a different"
                    " ledger entry",
                )
            return models.LedgerEntry.from_row(existing), False
        try:
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
                    account_clean,
                    side,
                    amount_minor,
                    code,
                    key,
                    request_hash,
                    note,
                    now_iso(),
                ),
            )
        except sqlite3.IntegrityError as exc:  # concurrent identical append
            raise BusinessError(
                IDEMPOTENCY_CONFLICT_CODE,
                f"idempotency key {key!r} is already in use",
            ) from exc
        store._audit(
            conn, tenant_id, business_id, actor, "ledger.append", "ledger_entry",
            entry_id, "ok",
            {
                "account": account_clean,
                "direction": side,
                "amount_minor": amount_minor,
                "currency": code,
            },
        )
    return get_entry(store, tenant_id, entry_id), True


def get_entry(
    store: "BusinessStore", tenant_id: str, entry_id: str
) -> models.LedgerEntry:
    with store.read() as conn:
        row = conn.execute(
            "SELECT * FROM bos_ledger_entry WHERE tenant_id=? AND entry_id=?",
            (tenant_id, entry_id),
        ).fetchone()
    if row is None:
        raise BusinessError(
            NOT_FOUND_CODE,
            f"no ledger entry {entry_id!r} in tenant {tenant_id!r}",
        )
    return models.LedgerEntry.from_row(row)


def list_entries(
    store: "BusinessStore",
    tenant_id: str,
    *,
    business_id: str | None = None,
    limit: int = 1000,
) -> list[models.LedgerEntry]:
    query = "SELECT * FROM bos_ledger_entry WHERE tenant_id=?"
    params: list[Any] = [tenant_id]
    if business_id is not None:
        query += " AND business_id=?"
        params.append(business_id)
    query += " ORDER BY created_at, entry_id LIMIT ?"
    params.append(max(1, int(limit)))
    with store.read() as conn:
        rows = conn.execute(query, params).fetchall()
    return [models.LedgerEntry.from_row(row) for row in rows]


def summary(
    store: "BusinessStore",
    tenant_id: str,
    *,
    business_id: str | None = None,
    currency: str | None = None,
) -> dict[str, Any]:
    """Derive totals from the rows; ``net_minor = credits - debits``."""
    query = (
        "SELECT direction, COUNT(*) AS n, COALESCE(SUM(amount_minor), 0) AS total"
        " FROM bos_ledger_entry WHERE tenant_id=?"
    )
    params: list[Any] = [tenant_id]
    if business_id is not None:
        query += " AND business_id=?"
        params.append(business_id)
    if currency is not None:
        query += " AND currency=?"
        params.append(_currency(currency))
    query += " GROUP BY direction"
    parts = {"debit": (0, 0), "credit": (0, 0)}
    with store.read() as conn:
        for row in conn.execute(query, params):
            parts[row["direction"]] = (int(row["n"]), int(row["total"]))
    debit_count, debit_minor = parts["debit"]
    credit_count, credit_minor = parts["credit"]
    return {
        "tenant_id": tenant_id,
        "business_id": business_id,
        "currency": currency.upper() if currency else None,
        "debit_minor": debit_minor,
        "credit_minor": credit_minor,
        "net_minor": credit_minor - debit_minor,
        "entry_count": debit_count + credit_count,
    }


def append_only_check(store: "BusinessStore") -> None:
    """Raise a typed error if a trigger is missing from the ledger table."""
    with store.read() as conn:
        names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
                " AND tbl_name='bos_ledger_entry'"
            )
        }
    missing = {
        "bos_ledger_entry_no_update",
        "bos_ledger_entry_no_delete",
    } - names
    if missing:
        raise BusinessError(
            BAD_STATE_CODE,
            f"ledger append-only triggers are missing: {', '.join(sorted(missing))}",
        )

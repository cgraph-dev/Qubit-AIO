"""Contracts, obligations, renewals and jurisdiction-aware rules (P5-06).

WHY THIS EXISTS
---------------
P5-06 makes a legal commitment a durable, tenant-scoped record with an exact
signing approval rather than a chat message or a mutable draft reference:

* **Contracts are versioned.** Every version carries an explicit clause list,
  an effective date and a content SHA-256. A clause diff is computed from the
  stored versions, never from prose.
* **Signing is approval-bound.** A signature cannot be recorded unless an
  approved :mod:`approvals` row names the *exact* signing payload hash for that
  version. Editing the version, the entity or the effective date changes the
  hash and invalidates the approval.
* **Obligations and renewals are workflow rows.** A due obligation is a stored
  row with a due date and a status; it is not derived from a calendar string.
* **Jurisdiction rules are versioned.** Rule evaluation selects the version
  whose effective interval contains the requested instant; an unknown
  jurisdiction or rule is a typed refusal, never a guessed tax rate.
* **Entity books do not mix.** Every contract belongs to one legal entity and
  every query is filtered by tenant *and* entity.

This module is deliberately silent on legal advice: the rule engine returns
stored, effective-date-selected data and a disclaimer. No external legal/tax
API, credential, network call or real signature is used.
"""
from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from . import approvals
from .errors import BusinessError
from .ids import canonical_json, fingerprint, new_id
from .util import json_dumps, json_loads, now_iso, redact, require_text

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .store import BusinessStore

SCHEMA = "qubit-office-legal/1"

LEGAL_NOT_FOUND_CODE = "LEGAL_NOT_FOUND"
LEGAL_BAD_INPUT_CODE = "LEGAL_BAD_INPUT"
LEGAL_BAD_STATE_CODE = "LEGAL_BAD_STATE"
LEGAL_ENTITY_MISMATCH_CODE = "LEGAL_ENTITY_MISMATCH"
LEGAL_DUPLICATE_CLAUSE_CODE = "LEGAL_DUPLICATE_CLAUSE"
LEGAL_JURISDICTION_UNKNOWN_CODE = "LEGAL_JURISDICTION_UNKNOWN"
LEGAL_RULE_UNKNOWN_CODE = "LEGAL_RULE_UNKNOWN"
LEGAL_SIGNING_APPROVAL_REQUIRED_CODE = "LEGAL_SIGNING_APPROVAL_REQUIRED"
LEGAL_SIGNING_APPROVAL_MISMATCH_CODE = "LEGAL_SIGNING_APPROVAL_MISMATCH"
LEGAL_SIGNING_SELF_APPROVAL_CODE = "LEGAL_SIGNING_SELF_APPROVAL"

ENTITY_KINDS = ("personal", "company")
ENTITY_STATUSES = ("active", "archived")
CONTRACT_STATUSES = (
    "draft",
    "pending_signature",
    "signed",
    "active",
    "terminated",
    "expired",
)
OBLIGATION_KINDS = ("renewal", "payment", "delivery", "notice", "other")
OBLIGATION_STATUSES = ("open", "in_progress", "done", "waived")

DISCLAIMER = (
    "Stored rule lookup with effective-date selection only; not legal or tax "
    "advice and not a substituted filing."
)

_SCHEMA_SQL = (
    """
    CREATE TABLE IF NOT EXISTS bos_legal_entity (
        entity_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL REFERENCES bos_tenant(tenant_id) ON DELETE RESTRICT,
        name TEXT NOT NULL,
        jurisdiction TEXT NOT NULL,
        kind TEXT NOT NULL CHECK(kind IN ('personal','company')),
        registration_id TEXT,
        tax_id TEXT,
        currency TEXT NOT NULL CHECK(length(currency) = 3),
        status TEXT NOT NULL DEFAULT 'active'
            CHECK(status IN ('active','archived')),
        metadata_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS bos_legal_entity_listing"
    " ON bos_legal_entity(tenant_id, status)",
    """
    CREATE TABLE IF NOT EXISTS bos_legal_contract (
        contract_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL REFERENCES bos_tenant(tenant_id) ON DELETE RESTRICT,
        business_id TEXT NOT NULL REFERENCES bos_business(business_id) ON DELETE RESTRICT,
        entity_id TEXT NOT NULL REFERENCES bos_legal_entity(entity_id) ON DELETE RESTRICT,
        title TEXT NOT NULL,
        counterparty TEXT NOT NULL,
        currency TEXT NOT NULL CHECK(length(currency) = 3),
        status TEXT NOT NULL DEFAULT 'draft'
            CHECK(status IN ('draft','pending_signature','signed','active',
                             'terminated','expired')),
        signed_version_id TEXT,
        signed_at TEXT,
        terminated_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS bos_legal_contract_listing"
    " ON bos_legal_contract(tenant_id, business_id, entity_id, status)",
    """
    CREATE TABLE IF NOT EXISTS bos_legal_contract_version (
        version_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        contract_id TEXT NOT NULL REFERENCES bos_legal_contract(contract_id)
            ON DELETE RESTRICT,
        version INTEGER NOT NULL CHECK(version > 0),
        clauses_json TEXT NOT NULL,
        content_sha256 TEXT NOT NULL,
        effective_at TEXT NOT NULL,
        change_note TEXT NOT NULL DEFAULT '',
        created_by TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(tenant_id, contract_id, version)
    )
    """,
    "CREATE INDEX IF NOT EXISTS bos_legal_contract_version_listing"
    " ON bos_legal_contract_version(tenant_id, contract_id, version)",
    """
    CREATE TABLE IF NOT EXISTS bos_legal_acceptance (
        acceptance_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        contract_id TEXT NOT NULL REFERENCES bos_legal_contract(contract_id)
            ON DELETE RESTRICT,
        version_id TEXT NOT NULL REFERENCES bos_legal_contract_version(version_id)
            ON DELETE RESTRICT,
        accepted_by TEXT NOT NULL,
        method TEXT NOT NULL CHECK(method IN ('attestation')),
        accepted_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS bos_legal_acceptance_listing"
    " ON bos_legal_acceptance(tenant_id, contract_id, accepted_at)",
    """
    CREATE TABLE IF NOT EXISTS bos_legal_signing (
        signing_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        contract_id TEXT NOT NULL REFERENCES bos_legal_contract(contract_id)
            ON DELETE RESTRICT,
        version_id TEXT NOT NULL REFERENCES bos_legal_contract_version(version_id)
            ON DELETE RESTRICT,
        approval_id TEXT NOT NULL,
        payload_hash TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('pending','signed','cancelled')),
        requested_by TEXT NOT NULL,
        created_at TEXT NOT NULL,
        signed_at TEXT,
        UNIQUE(tenant_id, approval_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS bos_legal_signing_listing"
    " ON bos_legal_signing(tenant_id, contract_id, status)",
    """
    CREATE TABLE IF NOT EXISTS bos_legal_obligation (
        obligation_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        contract_id TEXT NOT NULL REFERENCES bos_legal_contract(contract_id)
            ON DELETE RESTRICT,
        kind TEXT NOT NULL CHECK(kind IN
            ('renewal','payment','delivery','notice','other')),
        description TEXT NOT NULL,
        due_at TEXT NOT NULL,
        owner TEXT,
        status TEXT NOT NULL DEFAULT 'open'
            CHECK(status IN ('open','in_progress','done','waived')),
        completed_at TEXT,
        completed_by TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS bos_legal_obligation_due"
    " ON bos_legal_obligation(tenant_id, status, due_at)",
    """
    CREATE TABLE IF NOT EXISTS bos_legal_jurisdiction_rule (
        rule_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL REFERENCES bos_tenant(tenant_id) ON DELETE RESTRICT,
        jurisdiction TEXT NOT NULL,
        rule_key TEXT NOT NULL,
        version INTEGER NOT NULL CHECK(version > 0),
        effective_from TEXT NOT NULL,
        effective_to TEXT,
        payload_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(tenant_id, jurisdiction, rule_key, version)
    )
    """,
    "CREATE INDEX IF NOT EXISTS bos_legal_rule_lookup"
    " ON bos_legal_jurisdiction_rule(tenant_id, jurisdiction, rule_key,"
    " effective_from)",
)


# -- small validators ------------------------------------------------------
def _text(value: Any, label: str, *, max_len: int = 4096) -> str:
    try:
        return require_text(value, label, max_len=max_len)
    except ValueError as exc:
        raise BusinessError(LEGAL_BAD_INPUT_CODE, str(exc)) from exc


def _optional_text(value: Any, label: str, *, max_len: int = 512) -> str | None:
    if value is None:
        return None
    return _text(value, label, max_len=max_len)


def _currency(value: Any) -> str:
    clean = _text(value, "currency", max_len=8).upper()
    if len(clean) != 3 or not clean.isalpha():
        raise BusinessError(LEGAL_BAD_INPUT_CODE, "currency must be a 3-letter code")
    return clean


def _instant(value: Any, label: str, *, default: str | None = None) -> str:
    if value is None and default is not None:
        return default
    if not isinstance(value, str) or not value.strip():
        raise BusinessError(LEGAL_BAD_INPUT_CODE, f"{label} must be an ISO instant")
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BusinessError(
            LEGAL_BAD_INPUT_CODE, f"{label} is not an ISO-8601 instant: {text!r}"
        ) from exc
    if parsed.tzinfo is None:
        raise BusinessError(
            LEGAL_BAD_INPUT_CODE, f"{label} must include a timezone"
        )
    return parsed.astimezone(timezone.utc).isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")


def _parse_instant(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _clauses(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise BusinessError(LEGAL_BAD_INPUT_CODE, "clauses must be a list")
    if not value:
        raise BusinessError(LEGAL_BAD_INPUT_CODE, "a contract needs a clause")
    clean: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping):
            raise BusinessError(LEGAL_BAD_INPUT_CODE, "each clause must be a mapping")
        clause_id = _text(item.get("clause_id"), "clause_id", max_len=120)
        if clause_id in seen:
            raise BusinessError(
                LEGAL_DUPLICATE_CLAUSE_CODE,
                f"clause_id {clause_id!r} appears twice in one version",
            )
        seen.add(clause_id)
        clean.append(
            {
                "clause_id": clause_id,
                "title": _text(item.get("title"), "clause title", max_len=300),
                "body": _text(item.get("body"), "clause body", max_len=20000),
            }
        )
    return clean


def _ensure(conn: sqlite3.Connection) -> None:
    """Compatibility no-op: the additive tables migrate in schema.py v6."""
    return None


def ensure_schema(store: "BusinessStore") -> None:
    """Create the additive legal tables in the Business OS database.

    They are additive ``CREATE TABLE IF NOT EXISTS`` statements owned by this
    module; ``PRAGMA user_version`` is left to :mod:`schema`.
    """
    with store.transaction() as conn:
        _ensure(conn)


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _require_entity(conn: sqlite3.Connection, tenant_id: str, entity_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM bos_legal_entity WHERE tenant_id=? AND entity_id=?",
        (tenant_id, entity_id),
    ).fetchone()
    if row is None:
        raise BusinessError(
            LEGAL_NOT_FOUND_CODE,
            f"no legal entity {entity_id!r} in tenant {tenant_id!r}",
        )
    return row


def _require_contract(
    conn: sqlite3.Connection, tenant_id: str, contract_id: str
) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM bos_legal_contract WHERE tenant_id=? AND contract_id=?",
        (tenant_id, contract_id),
    ).fetchone()
    if row is None:
        raise BusinessError(
            LEGAL_NOT_FOUND_CODE,
            f"no contract {contract_id!r} in tenant {tenant_id!r}",
        )
    return row


def _require_version(
    conn: sqlite3.Connection, tenant_id: str, contract_id: str, version_id: str
) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM bos_legal_contract_version"
        " WHERE tenant_id=? AND contract_id=? AND version_id=?",
        (tenant_id, contract_id, version_id),
    ).fetchone()
    if row is None:
        raise BusinessError(
            LEGAL_NOT_FOUND_CODE,
            f"no version {version_id!r} for contract {contract_id!r}",
        )
    return row


# -- entities --------------------------------------------------------------
def create_entity(
    store: "BusinessStore",
    tenant_id: str,
    *,
    name: str,
    jurisdiction: str,
    kind: str = "personal",
    registration_id: str | None = None,
    tax_id: str | None = None,
    currency: str = "USD",
    metadata: Mapping[str, Any] | None = None,
    actor: str = "system",
) -> dict[str, Any]:
    clean_kind = _text(kind, "entity kind", max_len=20).lower()
    if clean_kind not in ENTITY_KINDS:
        raise BusinessError(
            LEGAL_BAD_INPUT_CODE,
            f"entity kind must be one of {', '.join(ENTITY_KINDS)}",
        )
    payload_meta = redact(dict(metadata or {})) if isinstance(metadata, Mapping) else {}
    if metadata is not None and not isinstance(metadata, Mapping):
        raise BusinessError(LEGAL_BAD_INPUT_CODE, "metadata must be a mapping")
    entity_id = new_id("ent")
    created = now_iso()
    with store.transaction() as conn:
        _ensure(conn)
        store._require_tenant(conn, tenant_id)
        conn.execute(
            "INSERT INTO bos_legal_entity(entity_id, tenant_id, name,"
            " jurisdiction, kind, registration_id, tax_id, currency, status,"
            " metadata_json, created_at, updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,'active',?,?,?)",
            (
                entity_id,
                tenant_id,
                _text(name, "entity name", max_len=300),
                _text(jurisdiction, "jurisdiction", max_len=64).upper(),
                clean_kind,
                _optional_text(registration_id, "registration_id"),
                _optional_text(tax_id, "tax_id"),
                _currency(currency),
                json_dumps(payload_meta),
                created,
                created,
            ),
        )
        store._audit(
            conn, tenant_id, None, actor, "legal.entity.create", "legal_entity",
            entity_id, "ok",
            {"kind": clean_kind, "jurisdiction": jurisdiction.upper()},
        )
    return get_entity(store, tenant_id, entity_id)


def _entity_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "entity_id": row["entity_id"],
        "tenant_id": row["tenant_id"],
        "name": row["name"],
        "jurisdiction": row["jurisdiction"],
        "kind": row["kind"],
        "registration_id": row["registration_id"],
        "tax_id": row["tax_id"],
        "currency": row["currency"],
        "status": row["status"],
        "metadata": json_loads(row["metadata_json"], {}),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def get_entity(store: "BusinessStore", tenant_id: str, entity_id: str) -> dict[str, Any]:
    with store.read() as conn:
        _ensure(conn)
        return _entity_dict(_require_entity(conn, tenant_id, entity_id))


def list_entities(
    store: "BusinessStore", tenant_id: str, *, status: str | None = None
) -> list[dict[str, Any]]:
    query = "SELECT * FROM bos_legal_entity WHERE tenant_id=?"
    params: list[Any] = [tenant_id]
    if status is not None:
        if status not in ENTITY_STATUSES:
            raise BusinessError(LEGAL_BAD_INPUT_CODE, f"unknown entity status {status!r}")
        query += " AND status=?"
        params.append(status)
    query += " ORDER BY created_at, entity_id"
    with store.read() as conn:
        _ensure(conn)
        return [_entity_dict(row) for row in conn.execute(query, params).fetchall()]


# -- jurisdiction rules ----------------------------------------------------
def register_rule(
    store: "BusinessStore",
    tenant_id: str,
    *,
    jurisdiction: str,
    rule_key: str,
    version: int,
    effective_from: str,
    effective_to: str | None = None,
    payload: Mapping[str, Any] | None = None,
    actor: str = "system",
) -> dict[str, Any]:
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise BusinessError(LEGAL_BAD_INPUT_CODE, "rule version must be a positive int")
    start = _instant(effective_from, "effective_from")
    end = _instant(effective_to, "effective_to") if effective_to is not None else None
    if end is not None and _parse_instant(end) <= _parse_instant(start):
        raise BusinessError(
            LEGAL_BAD_INPUT_CODE, "effective_to must be after effective_from"
        )
    if not isinstance(payload, Mapping):
        if payload is not None:
            raise BusinessError(LEGAL_BAD_INPUT_CODE, "rule payload must be a mapping")
        payload = {}
    rule_id = new_id("rule")
    with store.transaction() as conn:
        _ensure(conn)
        store._require_tenant(conn, tenant_id)
        try:
            conn.execute(
                "INSERT INTO bos_legal_jurisdiction_rule(rule_id, tenant_id,"
                " jurisdiction, rule_key, version, effective_from, effective_to,"
                " payload_json, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    rule_id,
                    tenant_id,
                    _text(jurisdiction, "jurisdiction", max_len=64).upper(),
                    _text(rule_key, "rule_key", max_len=120),
                    version,
                    start,
                    end,
                    json_dumps(redact(dict(payload))),
                    now_iso(),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise BusinessError(
                LEGAL_BAD_INPUT_CODE,
                f"rule {rule_key!r} v{version} already exists for this jurisdiction",
            ) from exc
        store._audit(
            conn, tenant_id, None, actor, "legal.rule.register", "legal_rule",
            rule_id, "ok",
            {"jurisdiction": jurisdiction.upper(), "rule_key": rule_key,
             "version": version},
        )
    return evaluate_rule(
        store,
        tenant_id,
        jurisdiction=jurisdiction,
        rule_key=rule_key,
        as_of=_parse_instant(start).timestamp(),
    )


def known_jurisdictions(store: "BusinessStore", tenant_id: str) -> list[str]:
    with store.read() as conn:
        if not _table_exists(conn, "bos_legal_jurisdiction_rule"):
            return []
        rows = conn.execute(
            "SELECT DISTINCT jurisdiction FROM bos_legal_jurisdiction_rule"
            " WHERE tenant_id=? ORDER BY jurisdiction",
            (tenant_id,),
        ).fetchall()
    return [row[0] for row in rows]


def _require_jurisdiction(
    conn: sqlite3.Connection, tenant_id: str, jurisdiction: str
) -> str:
    clean = _text(jurisdiction, "jurisdiction", max_len=64).upper()
    row = conn.execute(
        "SELECT 1 FROM bos_legal_jurisdiction_rule"
        " WHERE tenant_id=? AND jurisdiction=? LIMIT 1",
        (tenant_id, clean),
    ).fetchone()
    if row is None:
        raise BusinessError(
            LEGAL_JURISDICTION_UNKNOWN_CODE,
            f"no rules are registered for jurisdiction {clean!r}; refusing to"
            " guess a legal or tax position",
        )
    return clean


def evaluate_rule(
    store: "BusinessStore",
    tenant_id: str,
    *,
    jurisdiction: str,
    rule_key: str,
    as_of: float | None = None,
) -> dict[str, Any]:
    moment = datetime.now(timezone.utc).timestamp() if as_of is None else float(as_of)
    as_of_iso = now_iso(moment)
    with store.read() as conn:
        if not _table_exists(conn, "bos_legal_jurisdiction_rule"):
            raise BusinessError(
                LEGAL_JURISDICTION_UNKNOWN_CODE,
                f"no rules are registered for jurisdiction {jurisdiction!r}",
            )
        clean_jurisdiction = _require_jurisdiction(conn, tenant_id, jurisdiction)
        clean_key = _text(rule_key, "rule_key", max_len=120)
        rows = conn.execute(
            "SELECT * FROM bos_legal_jurisdiction_rule WHERE tenant_id=?"
            " AND jurisdiction=? AND rule_key=? ORDER BY version DESC",
            (tenant_id, clean_jurisdiction, clean_key),
        ).fetchall()
    if not rows:
        raise BusinessError(
            LEGAL_RULE_UNKNOWN_CODE,
            f"no rule {clean_key!r} is registered for jurisdiction"
            f" {clean_jurisdiction!r}",
        )
    selected = None
    for row in rows:
        start = _parse_instant(row["effective_from"]).timestamp()
        end = (
            _parse_instant(row["effective_to"]).timestamp()
            if row["effective_to"] is not None
            else None
        )
        if start <= moment and (end is None or moment < end):
            selected = row
            break
    if selected is None:
        raise BusinessError(
            LEGAL_RULE_UNKNOWN_CODE,
            f"rule {clean_key!r} has no version in effect at {as_of_iso}",
        )
    return {
        "jurisdiction": clean_jurisdiction,
        "rule_key": clean_key,
        "version": int(selected["version"]),
        "effective_from": selected["effective_from"],
        "effective_to": selected["effective_to"],
        "payload": json_loads(selected["payload_json"], {}),
        "as_of": as_of_iso,
        "disclaimer": DISCLAIMER,
    }


# -- contracts and versions ------------------------------------------------
def _contract_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "contract_id": row["contract_id"],
        "tenant_id": row["tenant_id"],
        "business_id": row["business_id"],
        "entity_id": row["entity_id"],
        "title": row["title"],
        "counterparty": row["counterparty"],
        "currency": row["currency"],
        "status": row["status"],
        "signed_version_id": row["signed_version_id"],
        "signed_at": row["signed_at"],
        "terminated_at": row["terminated_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def create_contract(
    store: "BusinessStore",
    tenant_id: str,
    *,
    business_id: str,
    entity_id: str,
    title: str,
    counterparty: str,
    currency: str | None = None,
    actor: str = "system",
) -> dict[str, Any]:
    contract_id = new_id("ctr")
    created = now_iso()
    with store.transaction() as conn:
        _ensure(conn)
        store._require_business(conn, tenant_id, business_id)
        entity = _require_entity(conn, tenant_id, entity_id)
        conn.execute(
            "INSERT INTO bos_legal_contract(contract_id, tenant_id, business_id,"
            " entity_id, title, counterparty, currency, status, created_at,"
            " updated_at) VALUES(?,?,?,?,?,?,?,'draft',?,?)",
            (
                contract_id,
                tenant_id,
                business_id,
                entity_id,
                _text(title, "contract title", max_len=300),
                _text(counterparty, "counterparty", max_len=300),
                _currency(currency or entity["currency"]),
                created,
                created,
            ),
        )
        store._audit(
            conn, tenant_id, business_id, actor, "legal.contract.create",
            "legal_contract", contract_id, "ok",
            {"entity_id": entity_id, "title": title},
        )
    return get_contract(store, tenant_id, contract_id)


def get_contract(
    store: "BusinessStore", tenant_id: str, contract_id: str
) -> dict[str, Any]:
    with store.read() as conn:
        _ensure(conn)
        return _contract_dict(_require_contract(conn, tenant_id, contract_id))


def list_contracts(
    store: "BusinessStore",
    tenant_id: str,
    *,
    business_id: str | None = None,
    entity_id: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    query = "SELECT * FROM bos_legal_contract WHERE tenant_id=?"
    params: list[Any] = [tenant_id]
    for column, value in (
        ("business_id", business_id),
        ("entity_id", entity_id),
        ("status", status),
    ):
        if value is not None:
            if column == "status" and value not in CONTRACT_STATUSES:
                raise BusinessError(
                    LEGAL_BAD_INPUT_CODE, f"unknown contract status {value!r}"
                )
            query += f" AND {column}=?"
            params.append(value)
    query += " ORDER BY created_at, contract_id"
    with store.read() as conn:
        _ensure(conn)
        return [_contract_dict(row) for row in conn.execute(query, params).fetchall()]


def entity_contracts(
    store: "BusinessStore", tenant_id: str, entity_id: str
) -> list[dict[str, Any]]:
    """All contracts for one entity; the other entity's books never appear."""
    with store.read() as conn:
        _ensure(conn)
        _require_entity(conn, tenant_id, entity_id)
    return list_contracts(store, tenant_id, entity_id=entity_id)


def _version_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "version_id": row["version_id"],
        "tenant_id": row["tenant_id"],
        "contract_id": row["contract_id"],
        "version": int(row["version"]),
        "clauses": json_loads(row["clauses_json"], []),
        "content_sha256": row["content_sha256"],
        "effective_at": row["effective_at"],
        "change_note": row["change_note"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
    }


def add_version(
    store: "BusinessStore",
    tenant_id: str,
    contract_id: str,
    *,
    clauses: Any,
    effective_at: str | None = None,
    change_note: str = "",
    actor: str = "system",
) -> dict[str, Any]:
    clean_clauses = _clauses(clauses)
    effective = _instant(effective_at, "effective_at", default=now_iso())
    digest = hashlib.sha256(canonical_json(clean_clauses).encode("utf-8")).hexdigest()
    version_id = new_id("cver")
    created = now_iso()
    with store.transaction() as conn:
        _ensure(conn)
        contract = _require_contract(conn, tenant_id, contract_id)
        if contract["status"] not in ("draft",):
            raise BusinessError(
                LEGAL_BAD_STATE_CODE,
                f"a version can only be added to a draft contract"
                f" (status={contract['status']!r})",
            )
        current = conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM bos_legal_contract_version"
            " WHERE tenant_id=? AND contract_id=?",
            (tenant_id, contract_id),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO bos_legal_contract_version(version_id, tenant_id,"
            " contract_id, version, clauses_json, content_sha256, effective_at,"
            " change_note, created_by, created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                version_id,
                tenant_id,
                contract_id,
                int(current) + 1,
                json_dumps(clean_clauses),
                digest,
                effective,
                str(change_note or "")[:2000],
                _text(actor, "actor", max_len=200),
                created,
            ),
        )
        conn.execute(
            "UPDATE bos_legal_contract SET updated_at=? WHERE tenant_id=?"
            " AND contract_id=?",
            (created, tenant_id, contract_id),
        )
        store._audit(
            conn, tenant_id, contract["business_id"], actor,
            "legal.contract.version", "legal_contract", contract_id, "ok",
            {"version_id": version_id, "version": int(current) + 1,
             "content_sha256": digest},
        )
    return get_version(store, tenant_id, contract_id, version_id)


def get_version(
    store: "BusinessStore", tenant_id: str, contract_id: str, version_id: str
) -> dict[str, Any]:
    with store.read() as conn:
        _ensure(conn)
        return _version_dict(_require_version(conn, tenant_id, contract_id, version_id))


def list_versions(
    store: "BusinessStore", tenant_id: str, contract_id: str
) -> list[dict[str, Any]]:
    with store.read() as conn:
        _ensure(conn)
        _require_contract(conn, tenant_id, contract_id)
        rows = conn.execute(
            "SELECT * FROM bos_legal_contract_version WHERE tenant_id=?"
            " AND contract_id=? ORDER BY version",
            (tenant_id, contract_id),
        ).fetchall()
    return [_version_dict(row) for row in rows]


def latest_version(
    store: "BusinessStore", tenant_id: str, contract_id: str
) -> dict[str, Any]:
    versions = list_versions(store, tenant_id, contract_id)
    if not versions:
        raise BusinessError(
            LEGAL_BAD_STATE_CODE, f"contract {contract_id!r} has no version yet"
        )
    return versions[-1]


def diff_versions(
    store: "BusinessStore",
    tenant_id: str,
    contract_id: str,
    from_version: int,
    to_version: int,
) -> dict[str, Any]:
    versions = {item["version"]: item for item in list_versions(store, tenant_id, contract_id)}
    for number in (from_version, to_version):
        if number not in versions:
            raise BusinessError(
                LEGAL_NOT_FOUND_CODE,
                f"contract {contract_id!r} has no version {number}",
            )
    before = {item["clause_id"]: item for item in versions[from_version]["clauses"]}
    after = {item["clause_id"]: item for item in versions[to_version]["clauses"]}
    added = [after[key] for key in sorted(after.keys() - before.keys())]
    removed = [before[key] for key in sorted(before.keys() - after.keys())]
    changed = []
    for key in sorted(before.keys() & after.keys()):
        if before[key] != after[key]:
            changed.append(
                {
                    "clause_id": key,
                    "before": before[key],
                    "after": after[key],
                    "before_sha256": hashlib.sha256(
                        canonical_json(before[key]).encode("utf-8")
                    ).hexdigest(),
                    "after_sha256": hashlib.sha256(
                        canonical_json(after[key]).encode("utf-8")
                    ).hexdigest(),
                }
            )
    return {
        "contract_id": contract_id,
        "from_version": from_version,
        "to_version": to_version,
        "added": added,
        "removed": removed,
        "changed": changed,
        "unchanged": sorted(
            (before.keys() & after.keys()) - {c["clause_id"] for c in changed}
        ),
    }


# -- approval-bound signing ------------------------------------------------
def signing_payload(
    store: "BusinessStore",
    tenant_id: str,
    contract_id: str,
    version_id: str,
) -> dict[str, Any]:
    """Return the exact intent an approval must bind for this version."""
    with store.read() as conn:
        _ensure(conn)
        contract = _require_contract(conn, tenant_id, contract_id)
        version = _require_version(conn, tenant_id, contract_id, version_id)
        entity = _require_entity(conn, tenant_id, contract["entity_id"])
    return approvals.normalized_intent(
        action="legal.contract.sign",
        target=contract_id,
        parameters={
            "contract_id": contract_id,
            "version_id": version_id,
            "version": int(version["version"]),
            "content_sha256": version["content_sha256"],
            "entity_id": contract["entity_id"],
            "jurisdiction": entity["jurisdiction"],
            "effective_at": version["effective_at"],
            "business_id": contract["business_id"],
        },
        currency=contract["currency"],
        amount_minor=0,
        policy_revision="legal-v1",
        business_id=contract["business_id"],
    )


def request_signing(
    store: "BusinessStore",
    tenant_id: str,
    contract_id: str,
    version_id: str,
    *,
    requested_by: str,
    idempotency_key: str,
) -> dict[str, Any]:
    with store.read() as conn:
        _ensure(conn)
        contract = _require_contract(conn, tenant_id, contract_id)
        _require_version(conn, tenant_id, contract_id, version_id)
        entity = _require_entity(conn, tenant_id, contract["entity_id"])
        # Unknown jurisdiction is refused before an approval can exist.
        _require_jurisdiction(conn, tenant_id, entity["jurisdiction"])
    if contract["status"] not in ("draft", "pending_signature"):
        raise BusinessError(
            LEGAL_BAD_STATE_CODE,
            f"only a draft or unsigned-pending contract can request a signature"
            f" (status={contract['status']!r})",
        )
    intent = signing_payload(store, tenant_id, contract_id, version_id)
    approval, created = approvals.request(
        store,
        tenant_id=tenant_id,
        business_id=contract["business_id"],
        action="legal.contract.sign",
        requested_by=requested_by,
        idempotency_key=idempotency_key,
        payload=intent,
        policy_version="legal-v1",
    )
    if approval.request_hash != fingerprint(intent):
        raise BusinessError(
            LEGAL_SIGNING_APPROVAL_MISMATCH_CODE,
            "the stored approval does not bind this exact signing payload",
        )
    signing_id = new_id("sgn")
    with store.transaction() as conn:
        _ensure(conn)
        existing = conn.execute(
            "SELECT * FROM bos_legal_signing WHERE tenant_id=? AND approval_id=?",
            (tenant_id, approval.approval_id),
        ).fetchone()
        if existing is None:
            conn.execute(
                "INSERT INTO bos_legal_signing(signing_id, tenant_id,"
                " contract_id, version_id, approval_id, payload_hash,"
                " payload_json, status, requested_by, created_at)"
                " VALUES(?,?,?,?,?,?,?,'pending',?,?)",
                (
                    signing_id,
                    tenant_id,
                    contract_id,
                    version_id,
                    approval.approval_id,
                    approval.request_hash,
                    json_dumps(intent),
                    requested_by,
                    now_iso(),
                ),
            )
            conn.execute(
                "UPDATE bos_legal_contract SET status='pending_signature',"
                " updated_at=? WHERE tenant_id=? AND contract_id=?",
                (now_iso(), tenant_id, contract_id),
            )
        row = conn.execute(
            "SELECT * FROM bos_legal_signing WHERE tenant_id=? AND approval_id=?",
            (tenant_id, approval.approval_id),
        ).fetchone()
        store._audit(
            conn, tenant_id, contract["business_id"], requested_by,
            "legal.contract.sign.request", "legal_contract", contract_id, "ok",
            {"approval_id": approval.approval_id,
             "payload_hash": approval.request_hash,
             "version_id": version_id},
        )
    return {
        "status": "pending_approval",
        "approval_id": approval.approval_id,
        "signing_id": row["signing_id"],
        "payload_hash": row["payload_hash"],
        "payload": intent,
        "created": created,
    }


def record_signing(
    store: "BusinessStore",
    tenant_id: str,
    contract_id: str,
    approval_id: str,
    *,
    signed_by: str,
    accepted_by: str | None = None,
    actor: str = "system",
) -> dict[str, Any]:
    """Record an approved-for-signature decision for the exact payload.

    This is an in-system attestation only. It is not a cryptographic or
    handwritten signature and no external signing service is contacted.
    """
    with store.read() as conn:
        _ensure(conn)
        contract = _require_contract(conn, tenant_id, contract_id)
        signing = conn.execute(
            "SELECT * FROM bos_legal_signing WHERE tenant_id=? AND approval_id=?",
            (tenant_id, approval_id),
        ).fetchone()
        if signing is None:
            raise BusinessError(
                LEGAL_SIGNING_APPROVAL_REQUIRED_CODE,
                f"no signing approval {approval_id!r} was requested for"
                f" contract {contract_id!r}",
            )
        if signing["contract_id"] != contract_id:
            raise BusinessError(
                LEGAL_SIGNING_APPROVAL_MISMATCH_CODE,
                f"approval {approval_id!r} is not bound to contract {contract_id!r}",
            )
        approval = approvals.get(store, tenant_id, approval_id)
        _require_version(conn, tenant_id, contract_id, signing["version_id"])
        entity = _require_entity(conn, tenant_id, contract["entity_id"])
    if approval.status != "approved":
        raise BusinessError(
            LEGAL_SIGNING_APPROVAL_REQUIRED_CODE,
            f"signing requires an approved exact-payload approval"
            f" (status={approval.status!r})",
        )
    if approval.action != "legal.contract.sign":
        raise BusinessError(
            LEGAL_SIGNING_APPROVAL_MISMATCH_CODE,
            f"approval action {approval.action!r} is not a contract signing",
        )
    expected = signing_payload(store, tenant_id, contract_id, signing["version_id"])
    expected_hash = fingerprint(expected)
    if (
        approval.request_hash != expected_hash
        or signing["payload_hash"] != expected_hash
        or json_loads(signing["payload_json"], {}) != expected
    ):
        raise BusinessError(
            LEGAL_SIGNING_APPROVAL_MISMATCH_CODE,
            "the approval hash no longer matches the exact contract version"
            " payload; request a fresh approval",
        )
    approver = str(approval.decided_by or "")
    if not approver:
        raise BusinessError(
            LEGAL_SIGNING_APPROVAL_REQUIRED_CODE,
            "the approval names no human decider",
        )
    if approver == approval.requested_by:
        raise BusinessError(
            LEGAL_SIGNING_SELF_APPROVAL_CODE,
            "the requester cannot approve their own legal commitment",
        )
    if signed_by == approval.requested_by:
        raise BusinessError(
            LEGAL_SIGNING_SELF_APPROVAL_CODE,
            "the requester cannot execute the legal commitment they raised",
        )
    if contract["status"] not in ("pending_signature", "signed"):
        raise BusinessError(
            LEGAL_BAD_STATE_CODE,
            f"contract cannot be signed from status {contract['status']!r}",
        )
    moment = now_iso()
    acceptance_id = new_id("acc")
    with store.transaction() as conn:
        _ensure(conn)
        current = _require_contract(conn, tenant_id, contract_id)
        if current["status"] == "signed":
            return {
                "status": "signed",
                "contract": _contract_dict(current),
                "approval_id": approval_id,
                "payload_hash": expected_hash,
                "accepted_by": accepted_by or signed_by,
                "real_signature": False,
                "legal_advice": False,
                "idempotent": True,
            }
        conn.execute(
            "UPDATE bos_legal_contract SET status='signed',"
            " signed_version_id=?, signed_at=?, updated_at=?"
            " WHERE tenant_id=? AND contract_id=?",
            (signing["version_id"], moment, moment, tenant_id, contract_id),
        )
        conn.execute(
            "UPDATE bos_legal_signing SET status='signed', signed_at=?"
            " WHERE tenant_id=? AND signing_id=?",
            (moment, tenant_id, signing["signing_id"]),
        )
        conn.execute(
            "INSERT INTO bos_legal_acceptance(acceptance_id, tenant_id,"
            " contract_id, version_id, accepted_by, method, accepted_at)"
            " VALUES(?,?,?,?,?,'attestation',?)",
            (
                acceptance_id,
                tenant_id,
                contract_id,
                signing["version_id"],
                _text(accepted_by or signed_by, "accepted_by", max_len=200),
                moment,
            ),
        )
        store._audit(
            conn, tenant_id, current["business_id"], actor,
            "legal.contract.sign", "legal_contract", contract_id, "ok",
            {"approval_id": approval_id, "payload_hash": expected_hash,
             "version_id": signing["version_id"],
             "jurisdiction": entity["jurisdiction"]},
        )
    return {
        "status": "signed",
        "contract": get_contract(store, tenant_id, contract_id),
        "version": get_version(store, tenant_id, contract_id, signing["version_id"]),
        "approval_id": approval_id,
        "payload_hash": expected_hash,
        "accepted_by": accepted_by or signed_by,
        "real_signature": False,
        "legal_advice": False,
        "idempotent": False,
    }


def record_acceptance(
    store: "BusinessStore",
    tenant_id: str,
    contract_id: str,
    version_id: str,
    *,
    accepted_by: str,
    actor: str = "system",
) -> dict[str, Any]:
    """Record a non-signing attestation (read-and-accepted), not a signature."""
    acceptance_id = new_id("acc")
    moment = now_iso()
    with store.transaction() as conn:
        _ensure(conn)
        contract = _require_contract(conn, tenant_id, contract_id)
        _require_version(conn, tenant_id, contract_id, version_id)
        conn.execute(
            "INSERT INTO bos_legal_acceptance(acceptance_id, tenant_id,"
            " contract_id, version_id, accepted_by, method, accepted_at)"
            " VALUES(?,?,?,?,?,'attestation',?)",
            (
                acceptance_id,
                tenant_id,
                contract_id,
                version_id,
                _text(accepted_by, "accepted_by", max_len=200),
                moment,
            ),
        )
        store._audit(
            conn, tenant_id, contract["business_id"], actor,
            "legal.contract.acceptance", "legal_contract", contract_id, "ok",
            {"version_id": version_id, "accepted_by": accepted_by},
        )
    return {
        "acceptance_id": acceptance_id,
        "contract_id": contract_id,
        "version_id": version_id,
        "accepted_by": accepted_by,
        "method": "attestation",
        "accepted_at": moment,
        "real_signature": False,
        "legal_advice": False,
    }


# -- obligations and renewals ---------------------------------------------
def add_obligation(
    store: "BusinessStore",
    tenant_id: str,
    contract_id: str,
    *,
    kind: str,
    description: str,
    due_at: str,
    owner: str | None = None,
    actor: str = "system",
) -> dict[str, Any]:
    clean_kind = _text(kind, "obligation kind", max_len=20).lower()
    if clean_kind not in OBLIGATION_KINDS:
        raise BusinessError(
            LEGAL_BAD_INPUT_CODE,
            f"obligation kind must be one of {', '.join(OBLIGATION_KINDS)}",
        )
    due = _instant(due_at, "due_at")
    obligation_id = new_id("obl")
    created = now_iso()
    with store.transaction() as conn:
        _ensure(conn)
        contract = _require_contract(conn, tenant_id, contract_id)
        if contract["status"] == "terminated":
            raise BusinessError(
                LEGAL_BAD_STATE_CODE, "a terminated contract takes no obligation"
            )
        conn.execute(
            "INSERT INTO bos_legal_obligation(obligation_id, tenant_id,"
            " contract_id, kind, description, due_at, owner, status,"
            " created_at, updated_at) VALUES(?,?,?,?,?,?,?,'open',?,?)",
            (
                obligation_id,
                tenant_id,
                contract_id,
                clean_kind,
                _text(description, "description", max_len=2000),
                due,
                _optional_text(owner, "owner", max_len=200),
                created,
                created,
            ),
        )
        store._audit(
            conn, tenant_id, contract["business_id"], actor,
            "legal.obligation.create", "legal_obligation", obligation_id, "ok",
            {"contract_id": contract_id, "kind": clean_kind, "due_at": due},
        )
    return _obligation(store, tenant_id, obligation_id)


def _obligation_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "obligation_id": row["obligation_id"],
        "tenant_id": row["tenant_id"],
        "contract_id": row["contract_id"],
        "kind": row["kind"],
        "description": row["description"],
        "due_at": row["due_at"],
        "owner": row["owner"],
        "status": row["status"],
        "completed_at": row["completed_at"],
        "completed_by": row["completed_by"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _obligation(
    store: "BusinessStore", tenant_id: str, obligation_id: str
) -> dict[str, Any]:
    with store.read() as conn:
        _ensure(conn)
        row = conn.execute(
            "SELECT * FROM bos_legal_obligation WHERE tenant_id=?"
            " AND obligation_id=?",
            (tenant_id, obligation_id),
        ).fetchone()
    if row is None:
        raise BusinessError(
            LEGAL_NOT_FOUND_CODE,
            f"no obligation {obligation_id!r} in tenant {tenant_id!r}",
        )
    return _obligation_dict(row)


def list_obligations(
    store: "BusinessStore",
    tenant_id: str,
    *,
    contract_id: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    query = "SELECT * FROM bos_legal_obligation WHERE tenant_id=?"
    params: list[Any] = [tenant_id]
    if contract_id is not None:
        query += " AND contract_id=?"
        params.append(contract_id)
    if status is not None:
        if status not in OBLIGATION_STATUSES:
            raise BusinessError(
                LEGAL_BAD_INPUT_CODE, f"unknown obligation status {status!r}"
            )
        query += " AND status=?"
        params.append(status)
    query += " ORDER BY due_at, obligation_id"
    with store.read() as conn:
        _ensure(conn)
        return [_obligation_dict(row) for row in conn.execute(query, params).fetchall()]


def due_obligations(
    store: "BusinessStore",
    tenant_id: str,
    *,
    as_of: str | None = None,
    within_days: int = 0,
    kind: str | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(within_days, int) or isinstance(within_days, bool) or within_days < 0:
        raise BusinessError(LEGAL_BAD_INPUT_CODE, "within_days must be >= 0")
    if kind is not None:
        clean_kind = _text(kind, "kind", max_len=20).lower()
        if clean_kind not in OBLIGATION_KINDS:
            raise BusinessError(LEGAL_BAD_INPUT_CODE, f"unknown kind {kind!r}")
    else:
        clean_kind = None
    moment = datetime.now(timezone.utc) if as_of is None else _parse_instant(
        _instant(as_of, "as_of")
    )
    cutoff = moment.timestamp() + within_days * 86400
    out: list[dict[str, Any]] = []
    for item in list_obligations(store, tenant_id, status="open"):
        if clean_kind is not None and item["kind"] != clean_kind:
            continue
        due = _parse_instant(item["due_at"]).timestamp()
        if due <= cutoff:
            row = dict(item)
            row["overdue"] = due < moment.timestamp()
            out.append(row)
    return out


def due_renewals(
    store: "BusinessStore",
    tenant_id: str,
    *,
    as_of: str | None = None,
    within_days: int = 0,
) -> list[dict[str, Any]]:
    return due_obligations(
        store, tenant_id, as_of=as_of, within_days=within_days, kind="renewal"
    )


def complete_obligation(
    store: "BusinessStore",
    tenant_id: str,
    obligation_id: str,
    *,
    completed_by: str,
    actor: str = "system",
) -> dict[str, Any]:
    moment = now_iso()
    with store.transaction() as conn:
        _ensure(conn)
        row = conn.execute(
            "SELECT * FROM bos_legal_obligation WHERE tenant_id=?"
            " AND obligation_id=?",
            (tenant_id, obligation_id),
        ).fetchone()
        if row is None:
            raise BusinessError(
                LEGAL_NOT_FOUND_CODE,
                f"no obligation {obligation_id!r} in tenant {tenant_id!r}",
            )
        if row["status"] != "open" and row["status"] != "in_progress":
            raise BusinessError(
                LEGAL_BAD_STATE_CODE,
                f"obligation is already {row['status']!r}",
            )
        conn.execute(
            "UPDATE bos_legal_obligation SET status='done', completed_at=?,"
            " completed_by=?, updated_at=? WHERE tenant_id=? AND obligation_id=?",
            (moment, _text(completed_by, "completed_by", max_len=200), moment,
             tenant_id, obligation_id),
        )
        contract = _require_contract(conn, tenant_id, row["contract_id"])
        store._audit(
            conn, tenant_id, contract["business_id"], actor,
            "legal.obligation.complete", "legal_obligation", obligation_id, "ok",
            {"completed_by": completed_by},
        )
    return _obligation(store, tenant_id, obligation_id)


def renew_contract(
    store: "BusinessStore",
    tenant_id: str,
    contract_id: str,
    *,
    effective_at: str,
    clauses: Any | None = None,
    change_note: str = "renewal",
    actor: str = "system",
) -> dict[str, Any]:
    """Start a renewal: a new draft version of a signed contract.

    The contract returns to ``pending_signature`` only when a fresh approval is
    requested; the prior signed version is never mutated.
    """
    with store.read() as conn:
        _ensure(conn)
        contract = _require_contract(conn, tenant_id, contract_id)
    if contract["status"] not in ("signed", "active"):
        raise BusinessError(
            LEGAL_BAD_STATE_CODE,
            f"only a signed contract can be renewed (status={contract['status']!r})",
        )
    source = latest_version(store, tenant_id, contract_id)
    with store.transaction() as conn:
        _ensure(conn)
        conn.execute(
            "UPDATE bos_legal_contract SET status='draft', updated_at=?"
            " WHERE tenant_id=? AND contract_id=?",
            (now_iso(), tenant_id, contract_id),
        )
    version = add_version(
        store,
        tenant_id,
        contract_id,
        clauses=source["clauses"] if clauses is None else clauses,
        effective_at=effective_at,
        change_note=change_note,
        actor=actor,
    )
    return version

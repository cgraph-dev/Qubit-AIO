"""HR, procurement and Business OS agent lifecycle (P5-07).

WHY THIS EXISTS
---------------
The people domain is where a business's authority changes hands. This module
keeps the durable records and reuses the authorities that already exist:

* **People and assignments.** Join, move and leave are rows, not a mutable
  "current department" string. Moving closes the prior assignment (history is
  preserved) and opens a new one.
* **Access.** A person may be bound to a security principal and/or a Business
  OS agent. Leave/revoke calls :func:`identity.revoke_principal` and
  :meth:`AgentControlService.revoke`, so the revocation is effective
  immediately — authorization reads the principal row on every decision.
* **Procurement.** Request -> human approval -> budget spend check -> provider
  receipt/ledger. The requester can never approve their own request
  (separation of duties), the approval is an exact-intent approval from
  :mod:`approvals`, and the spend check is the Business OS budget.
* **Money.** Spend is appended through the immutable :mod:`ledger` in double
  entry; a procurement receipt is a stored artifact.

All tables are additive ``CREATE TABLE IF NOT EXISTS`` statements owned here;
``PRAGMA user_version`` remains owned by :mod:`schema`. No live HR/payment API,
credential or network call is used.
"""
from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any, Iterable, Mapping

from . import approvals, identity, ledger
from .agents import AGENT_STATUSES, AgentControlService
from .errors import BusinessError
from .ids import fingerprint, new_id
from .util import json_dumps, json_loads, now_iso, redact, require_text

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .store import BusinessStore

SCHEMA = "qubit-office-people/1"

PEOPLE_NOT_FOUND_CODE = "PEOPLE_NOT_FOUND"
PEOPLE_BAD_INPUT_CODE = "PEOPLE_BAD_INPUT"
PEOPLE_BAD_STATE_CODE = "PEOPLE_BAD_STATE"
PEOPLE_SELF_APPROVAL_CODE = "PEOPLE_SELF_APPROVAL_REFUSED"
PEOPLE_SPEND_CAP_EXCEEDED_CODE = "PEOPLE_SPEND_CAP_EXCEEDED"
PEOPLE_SPEND_CAP_NOT_CONFIGURED_CODE = "PEOPLE_SPEND_CAP_NOT_CONFIGURED"
PEOPLE_ACCESS_MISMATCH_CODE = "PEOPLE_ACCESS_MISMATCH"

PERSON_KINDS = ("employee", "contractor")
PERSON_STATUSES = ("active", "left", "revoked")
ASSIGNMENT_STATUSES = ("active", "ended")
TRAINING_STATUSES = ("required", "completed", "expired", "waived")
PROCUREMENT_STATUSES = (
    "awaiting_approval",
    "approved",
    "blocked_spend_cap",
    "completed",
    "rejected",
    "cancelled",
)

_SCHEMA_SQL = (
    """
    CREATE TABLE IF NOT EXISTS bos_people_person (
        person_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL REFERENCES bos_tenant(tenant_id) ON DELETE RESTRICT,
        business_id TEXT NOT NULL REFERENCES bos_business(business_id) ON DELETE RESTRICT,
        display_name TEXT NOT NULL,
        email TEXT,
        kind TEXT NOT NULL CHECK(kind IN ('employee','contractor')),
        status TEXT NOT NULL DEFAULT 'active'
            CHECK(status IN ('active','moved','left','revoked')),
        agent_id TEXT,
        principal_id TEXT,
        joined_at TEXT NOT NULL,
        left_at TEXT,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        idempotency_key TEXT NOT NULL,
        request_hash TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(tenant_id, idempotency_key)
    )
    """,
    "CREATE INDEX IF NOT EXISTS bos_people_person_listing"
    " ON bos_people_person(tenant_id, business_id, status)",
    """
    CREATE TABLE IF NOT EXISTS bos_people_assignment (
        assignment_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        person_id TEXT NOT NULL REFERENCES bos_people_person(person_id)
            ON DELETE RESTRICT,
        department TEXT NOT NULL,
        role_id TEXT,
        title TEXT,
        effective_from TEXT NOT NULL,
        effective_to TEXT,
        status TEXT NOT NULL CHECK(status IN ('active','ended')),
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS bos_people_assignment_listing"
    " ON bos_people_assignment(tenant_id, person_id, status)",
    """
    CREATE TABLE IF NOT EXISTS bos_people_access (
        access_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        person_id TEXT NOT NULL REFERENCES bos_people_person(person_id)
            ON DELETE RESTRICT,
        principal_id TEXT NOT NULL,
        scope TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('active','revoked')),
        granted_at TEXT NOT NULL,
        revoked_at TEXT,
        reason TEXT NOT NULL DEFAULT ''
    )
    """,
    "CREATE INDEX IF NOT EXISTS bos_people_access_listing"
    " ON bos_people_access(tenant_id, person_id, status)",
    """
    CREATE TABLE IF NOT EXISTS bos_people_skill (
        skill_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        person_id TEXT NOT NULL REFERENCES bos_people_person(person_id)
            ON DELETE RESTRICT,
        name TEXT NOT NULL,
        level TEXT NOT NULL,
        verified_at TEXT NOT NULL,
        expires_at TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS bos_people_skill_listing"
    " ON bos_people_skill(tenant_id, person_id, name)",
    """
    CREATE TABLE IF NOT EXISTS bos_people_training (
        training_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        person_id TEXT NOT NULL REFERENCES bos_people_person(person_id)
            ON DELETE RESTRICT,
        name TEXT NOT NULL,
        required INTEGER NOT NULL DEFAULT 1 CHECK(required IN (0,1)),
        completed_at TEXT,
        expires_at TEXT,
        status TEXT NOT NULL
            CHECK(status IN ('required','completed','expired','waived')),
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS bos_people_training_due"
    " ON bos_people_training(tenant_id, status, expires_at)",
    """
    CREATE TABLE IF NOT EXISTS bos_people_procurement (
        request_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL REFERENCES bos_tenant(tenant_id) ON DELETE RESTRICT,
        business_id TEXT NOT NULL REFERENCES bos_business(business_id) ON DELETE RESTRICT,
        vendor_contact_id TEXT,
        vendor_name TEXT NOT NULL,
        requester TEXT NOT NULL,
        amount_minor INTEGER NOT NULL CHECK(amount_minor >= 0),
        currency TEXT NOT NULL CHECK(length(currency) = 3),
        purpose TEXT NOT NULL,
        status TEXT NOT NULL
            CHECK(status IN ('awaiting_approval','approved','blocked_spend_cap',
                             'completed','rejected','cancelled')),
        approval_id TEXT,
        receipt_artifact_id TEXT,
        ledger_debit_id TEXT,
        ledger_credit_id TEXT,
        budget_period TEXT,
        idempotency_key TEXT NOT NULL,
        request_hash TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(tenant_id, idempotency_key)
    )
    """,
    "CREATE INDEX IF NOT EXISTS bos_people_procurement_listing"
    " ON bos_people_procurement(tenant_id, business_id, status)",
)


# -- small validators ------------------------------------------------------
def _text(value: Any, label: str, *, max_len: int = 4096) -> str:
    try:
        return require_text(value, label, max_len=max_len)
    except ValueError as exc:
        raise BusinessError(PEOPLE_BAD_INPUT_CODE, str(exc)) from exc


def _optional_text(value: Any, label: str, *, max_len: int = 512) -> str | None:
    if value is None:
        return None
    return _text(value, label, max_len=max_len)


def _currency(value: Any) -> str:
    clean = _text(value, "currency", max_len=8).upper()
    if len(clean) != 3 or not clean.isalpha():
        raise BusinessError(PEOPLE_BAD_INPUT_CODE, "currency must be a 3-letter code")
    return clean


def _ensure(conn: sqlite3.Connection) -> None:
    """Compatibility no-op: the additive tables migrate in schema.py v6."""
    return None


def ensure_schema(store: "BusinessStore") -> None:
    with store.transaction() as conn:
        _ensure(conn)


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _require_person(
    conn: sqlite3.Connection, tenant_id: str, person_id: str
) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM bos_people_person WHERE tenant_id=? AND person_id=?",
        (tenant_id, person_id),
    ).fetchone()
    if row is None:
        raise BusinessError(
            PEOPLE_NOT_FOUND_CODE,
            f"no person {person_id!r} in tenant {tenant_id!r}",
        )
    return row


def _person_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "person_id": row["person_id"],
        "tenant_id": row["tenant_id"],
        "business_id": row["business_id"],
        "display_name": row["display_name"],
        "email": row["email"],
        "kind": row["kind"],
        "status": row["status"],
        "agent_id": row["agent_id"],
        "principal_id": row["principal_id"],
        "joined_at": row["joined_at"],
        "left_at": row["left_at"],
        "metadata": json_loads(row["metadata_json"], {}),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _assignment_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "assignment_id": row["assignment_id"],
        "tenant_id": row["tenant_id"],
        "person_id": row["person_id"],
        "department": row["department"],
        "role_id": row["role_id"],
        "title": row["title"],
        "effective_from": row["effective_from"],
        "effective_to": row["effective_to"],
        "status": row["status"],
        "created_at": row["created_at"],
    }


# -- join / move / leave ---------------------------------------------------
def join(
    store: "BusinessStore",
    tenant_id: str,
    business_id: str,
    *,
    display_name: str,
    department: str,
    kind: str = "employee",
    email: str | None = None,
    role_id: str | None = None,
    title: str | None = None,
    agent_id: str | None = None,
    principal_id: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    actor: str = "system",
    idempotency_key: str,
) -> dict[str, Any]:
    clean_kind = _text(kind, "person kind", max_len=20).lower()
    if clean_kind not in PERSON_KINDS:
        raise BusinessError(
            PEOPLE_BAD_INPUT_CODE,
            f"person kind must be one of {', '.join(PERSON_KINDS)}",
        )
    if metadata is not None and not isinstance(metadata, Mapping):
        raise BusinessError(PEOPLE_BAD_INPUT_CODE, "metadata must be a mapping")
    clean_email = _optional_text(email, "email", max_len=320)
    fields = {
        "display_name": _text(display_name, "display_name", max_len=300),
        "email": clean_email.lower() if clean_email else None,
        "kind": clean_kind,
        "department": _text(department, "department", max_len=120),
        "role_id": _optional_text(role_id, "role_id", max_len=80),
        "title": _optional_text(title, "title", max_len=200),
        "agent_id": _optional_text(agent_id, "agent_id", max_len=80),
        "principal_id": _optional_text(principal_id, "principal_id", max_len=80),
    }
    key = _text(idempotency_key, "idempotency key", max_len=300)
    request_hash = fingerprint(
        {"action": "people.join", "tenant_id": tenant_id,
         "business_id": business_id, "fields": fields}
    )
    person_id = new_id("ppl")
    created = now_iso()
    with store.transaction() as conn:
        _ensure(conn)
        store._require_business(conn, tenant_id, business_id)
        existing = conn.execute(
            "SELECT * FROM bos_people_person WHERE tenant_id=? AND idempotency_key=?",
            (tenant_id, key),
        ).fetchone()
        if existing is not None:
            if existing["request_hash"] != request_hash:
                raise BusinessError(
                    "BUSINESS_IDEMPOTENCY_CONFLICT",
                    f"idempotency key {key!r} was already used for a different join",
                )
            return person_profile(store, tenant_id, existing["person_id"])
        if fields["principal_id"] is not None:
            identity.get_principal(store, tenant_id, fields["principal_id"])
        if fields["agent_id"] is not None:
            agent = AgentControlService(store).get(tenant_id, fields["agent_id"])
            if agent.business_id != business_id:
                raise BusinessError(
                    PEOPLE_ACCESS_MISMATCH_CODE,
                    "the linked agent belongs to a different business",
                )
        conn.execute(
            "INSERT INTO bos_people_person(person_id, tenant_id, business_id,"
            " display_name, email, kind, status, agent_id, principal_id,"
            " joined_at, metadata_json, idempotency_key, request_hash,"
            " created_at, updated_at) VALUES(?,?,?,?,?,?,'active',?,?,?,?,?,?,?,?)",
            (
                person_id,
                tenant_id,
                business_id,
                fields["display_name"],
                fields["email"],
                fields["kind"],
                fields["agent_id"],
                fields["principal_id"],
                created,
                json_dumps(redact(dict(metadata or {}))),
                key,
                request_hash,
                created,
                created,
            ),
        )
        assignment_id = new_id("asg")
        conn.execute(
            "INSERT INTO bos_people_assignment(assignment_id, tenant_id, person_id,"
            " department, role_id, title, effective_from, status, created_at)"
            " VALUES(?,?,?,?,?,?,?,'active',?)",
            (assignment_id, tenant_id, person_id, fields["department"],
             fields["role_id"], fields["title"], created, created),
        )
        store._audit(
            conn, tenant_id, business_id, actor, "people.join", "person",
            person_id, "ok",
            {"kind": fields["kind"], "department": fields["department"],
             "agent_id": fields["agent_id"],
             "principal_id": fields["principal_id"]},
        )
    return person_profile(store, tenant_id, person_id)


def get_person(
    store: "BusinessStore", tenant_id: str, person_id: str
) -> dict[str, Any]:
    with store.read() as conn:
        _ensure(conn)
        return _person_dict(_require_person(conn, tenant_id, person_id))


def list_people(
    store: "BusinessStore",
    tenant_id: str,
    *,
    business_id: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    query = "SELECT * FROM bos_people_person WHERE tenant_id=?"
    params: list[Any] = [tenant_id]
    if business_id is not None:
        query += " AND business_id=?"
        params.append(business_id)
    if status is not None:
        if status not in PERSON_STATUSES:
            raise BusinessError(
                PEOPLE_BAD_INPUT_CODE, f"unknown person status {status!r}"
            )
        query += " AND status=?"
        params.append(status)
    query += " ORDER BY created_at, person_id"
    with store.read() as conn:
        _ensure(conn)
        return [_person_dict(row) for row in conn.execute(query, params).fetchall()]


def _active_assignment(
    store: "BusinessStore", tenant_id: str, person_id: str
) -> dict[str, Any] | None:
    with store.read() as conn:
        _ensure(conn)
        row = conn.execute(
            "SELECT * FROM bos_people_assignment WHERE tenant_id=? AND person_id=?"
            " AND status='active' ORDER BY created_at DESC, assignment_id DESC"
            " LIMIT 1",
            (tenant_id, person_id),
        ).fetchone()
    return _assignment_dict(row) if row is not None else None


def person_profile(
    store: "BusinessStore", tenant_id: str, person_id: str
) -> dict[str, Any]:
    person = get_person(store, tenant_id, person_id)
    with store.read() as conn:
        _ensure(conn)
        assignments = [
            _assignment_dict(row)
            for row in conn.execute(
                "SELECT * FROM bos_people_assignment WHERE tenant_id=?"
                " AND person_id=? ORDER BY created_at, assignment_id",
                (tenant_id, person_id),
            ).fetchall()
        ]
        access = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM bos_people_access WHERE tenant_id=? AND person_id=?"
                " ORDER BY granted_at, access_id",
                (tenant_id, person_id),
            ).fetchall()
        ]
        skills = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM bos_people_skill WHERE tenant_id=? AND person_id=?"
                " ORDER BY verified_at, skill_id",
                (tenant_id, person_id),
            ).fetchall()
        ]
        training = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM bos_people_training WHERE tenant_id=? AND person_id=?"
                " ORDER BY name, training_id",
                (tenant_id, person_id),
            ).fetchall()
        ]
    return {
        "person": person,
        "active_assignment": next(
            (item for item in assignments if item["status"] == "active"), None
        ),
        "assignments": assignments,
        "access": access,
        "skills": skills,
        "training": training,
    }


def move(
    store: "BusinessStore",
    tenant_id: str,
    person_id: str,
    *,
    department: str,
    role_id: str | None = None,
    title: str | None = None,
    effective_from: str | None = None,
    actor: str = "system",
) -> dict[str, Any]:
    moment = effective_from or now_iso()
    with store.transaction() as conn:
        _ensure(conn)
        person = _require_person(conn, tenant_id, person_id)
        if person["status"] != "active":
            raise BusinessError(
                PEOPLE_BAD_STATE_CODE,
                f"a person in status {person['status']!r} cannot move",
            )
        conn.execute(
            "UPDATE bos_people_assignment SET status='ended', effective_to=?"
            " WHERE tenant_id=? AND person_id=? AND status='active'",
            (moment, tenant_id, person_id),
        )
        assignment_id = new_id("asg")
        conn.execute(
            "INSERT INTO bos_people_assignment(assignment_id, tenant_id, person_id,"
            " department, role_id, title, effective_from, status, created_at)"
            " VALUES(?,?,?,?,?,?,?,'active',?)",
            (
                assignment_id,
                tenant_id,
                person_id,
                _text(department, "department", max_len=120),
                _optional_text(role_id, "role_id", max_len=80),
                _optional_text(title, "title", max_len=200),
                moment,
                now_iso(),
            ),
        )
        conn.execute(
            "UPDATE bos_people_person SET status='active', updated_at=?"
            " WHERE tenant_id=? AND person_id=?",
            (now_iso(), tenant_id, person_id),
        )
        store._audit(
            conn, tenant_id, person["business_id"], actor, "people.move",
            "person", person_id, "ok",
            {"department": department, "role_id": role_id,
             "effective_from": moment},
        )
    return person_profile(store, tenant_id, person_id)


def leave(
    store: "BusinessStore",
    tenant_id: str,
    person_id: str,
    *,
    reason: str = "",
    actor: str = "system",
) -> dict[str, Any]:
    """End employment and revoke every access path immediately."""
    moment = now_iso()
    note = str(reason or "")[:2000]
    with store.read() as conn:
        _ensure(conn)
        person = _require_person(conn, tenant_id, person_id)
        principal_ids = [
            row["principal_id"]
            for row in conn.execute(
                "SELECT principal_id FROM bos_people_access WHERE tenant_id=?"
                " AND person_id=? AND status='active'",
                (tenant_id, person_id),
            ).fetchall()
        ]
        if person["principal_id"] and person["principal_id"] not in principal_ids:
            principal_ids.append(person["principal_id"])
    agent_id = person["agent_id"]
    # Revocation happens before the person row is marked left: an access path
    # can never outlive the leave record.
    for principal_id in principal_ids:
        identity.revoke_principal(store, tenant_id, principal_id, actor=actor)
    agent_status = None
    if agent_id:
        service = AgentControlService(store)
        agent = service.get(tenant_id, agent_id)
        if agent.status in ("active", "paused"):
            service.revoke(
                tenant_id,
                agent_id,
                idempotency_key=f"people.leave:{person_id}",
                actor=actor,
                note=note or "person left",
            )
        agent_status = service.get(tenant_id, agent_id).status
    with store.transaction() as conn:
        _ensure(conn)
        conn.execute(
            "UPDATE bos_people_person SET status='left', left_at=?, updated_at=?"
            " WHERE tenant_id=? AND person_id=?",
            (moment, moment, tenant_id, person_id),
        )
        conn.execute(
            "UPDATE bos_people_assignment SET status='ended', effective_to=?"
            " WHERE tenant_id=? AND person_id=? AND status='active'",
            (moment, tenant_id, person_id),
        )
        conn.execute(
            "UPDATE bos_people_access SET status='revoked', revoked_at=?,"
            " reason=? WHERE tenant_id=? AND person_id=? AND status='active'",
            (moment, note or "left", tenant_id, person_id),
        )
        store._audit(
            conn, tenant_id, person["business_id"], actor, "people.leave",
            "person", person_id, "ok",
            {"reason": note, "principals_revoked": principal_ids,
             "agent_id": agent_id, "agent_status": agent_status},
        )
    profile = person_profile(store, tenant_id, person_id)
    profile["access_revoked"] = True
    profile["revoked_at"] = moment
    profile["agent_status"] = agent_status
    return profile


def revoke_access(
    store: "BusinessStore",
    tenant_id: str,
    person_id: str,
    *,
    reason: str = "",
    principal_id: str | None = None,
    actor: str = "system",
) -> dict[str, Any]:
    """Revoke one or all access paths without ending employment."""
    moment = now_iso()
    note = str(reason or "")[:2000]
    with store.read() as conn:
        _ensure(conn)
        person = _require_person(conn, tenant_id, person_id)
        query = (
            "SELECT * FROM bos_people_access WHERE tenant_id=? AND person_id=?"
            " AND status='active'"
        )
        params: list[Any] = [tenant_id, person_id]
        if principal_id is not None:
            query += " AND principal_id=?"
            params.append(principal_id)
        rows = conn.execute(query, params).fetchall()
        targets = [row["principal_id"] for row in rows]
        if principal_id is not None and not targets:
            raise BusinessError(
                PEOPLE_ACCESS_MISMATCH_CODE,
                f"principal {principal_id!r} has no active access for this person",
            )
    for target in targets:
        identity.revoke_principal(store, tenant_id, target, actor=actor)
    with store.transaction() as conn:
        _ensure(conn)
        for target in targets:
            conn.execute(
                "UPDATE bos_people_access SET status='revoked', revoked_at=?,"
                " reason=? WHERE tenant_id=? AND person_id=? AND principal_id=?"
                " AND status='active'",
                (moment, note or "access revoked", tenant_id, person_id, target),
            )
        conn.execute(
            "UPDATE bos_people_person SET updated_at=? WHERE tenant_id=?"
            " AND person_id=?",
            (moment, tenant_id, person_id),
        )
        store._audit(
            conn, tenant_id, person["business_id"], actor, "people.access.revoke",
            "person", person_id, "ok",
            {"principals": targets, "reason": note},
        )
    profile = person_profile(store, tenant_id, person_id)
    profile["access_revoked"] = True
    profile["revoked_at"] = moment
    return profile


def record_access(
    store: "BusinessStore",
    tenant_id: str,
    person_id: str,
    *,
    principal_id: str,
    scope: str = "work",
    actor: str = "system",
) -> dict[str, Any]:
    """Record (do not create) an access mapping; grants stay in identity."""
    access_id = new_id("acs")
    moment = now_iso()
    with store.transaction() as conn:
        _ensure(conn)
        person = _require_person(conn, tenant_id, person_id)
        if person["status"] != "active":
            raise BusinessError(
                PEOPLE_BAD_STATE_CODE,
                f"a person in status {person['status']!r} takes no new access",
            )
        identity.get_principal(store, tenant_id, principal_id)
        conn.execute(
            "INSERT INTO bos_people_access(access_id, tenant_id, person_id,"
            " principal_id, scope, status, granted_at)"
            " VALUES(?,?,?,?,?,'active',?)",
            (access_id, tenant_id, person_id, principal_id,
             _text(scope, "scope", max_len=200), moment),
        )
        store._audit(
            conn, tenant_id, person["business_id"], actor, "people.access.grant",
            "person", person_id, "ok",
            {"principal_id": principal_id, "scope": scope},
        )
    return {
        "access_id": access_id,
        "person_id": person_id,
        "principal_id": principal_id,
        "scope": scope,
        "status": "active",
        "granted_at": moment,
    }


# -- skills and training ---------------------------------------------------
def record_skill(
    store: "BusinessStore",
    tenant_id: str,
    person_id: str,
    *,
    name: str,
    level: str = "working",
    expires_at: str | None = None,
    actor: str = "system",
) -> dict[str, Any]:
    skill_id = new_id("skl")
    moment = now_iso()
    with store.transaction() as conn:
        _ensure(conn)
        person = _require_person(conn, tenant_id, person_id)
        conn.execute(
            "INSERT INTO bos_people_skill(skill_id, tenant_id, person_id, name,"
            " level, verified_at, expires_at) VALUES(?,?,?,?,?,?,?)",
            (skill_id, tenant_id, person_id, _text(name, "skill name", max_len=200),
             _text(level, "level", max_len=60), moment, expires_at),
        )
        store._audit(
            conn, tenant_id, person["business_id"], actor, "people.skill.record",
            "person", person_id, "ok", {"name": name, "level": level},
        )
    return {
        "skill_id": skill_id,
        "person_id": person_id,
        "name": name,
        "level": level,
        "verified_at": moment,
        "expires_at": expires_at,
    }


def record_training(
    store: "BusinessStore",
    tenant_id: str,
    person_id: str,
    *,
    name: str,
    required: bool = True,
    completed_at: str | None = None,
    expires_at: str | None = None,
    actor: str = "system",
) -> dict[str, Any]:
    training_id = new_id("trn")
    created = now_iso()
    status = "completed" if completed_at is not None else (
        "required" if required else "waived"
    )
    with store.transaction() as conn:
        _ensure(conn)
        person = _require_person(conn, tenant_id, person_id)
        conn.execute(
            "INSERT INTO bos_people_training(training_id, tenant_id, person_id,"
            " name, required, completed_at, expires_at, status, created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (
                training_id,
                tenant_id,
                person_id,
                _text(name, "training name", max_len=200),
                1 if required else 0,
                completed_at,
                expires_at,
                status,
                created,
            ),
        )
        store._audit(
            conn, tenant_id, person["business_id"], actor,
            "people.training.record", "person", person_id, "ok",
            {"name": name, "status": status},
        )
    return get_training(store, tenant_id, training_id)


def get_training(
    store: "BusinessStore", tenant_id: str, training_id: str
) -> dict[str, Any]:
    with store.read() as conn:
        _ensure(conn)
        row = conn.execute(
            "SELECT * FROM bos_people_training WHERE tenant_id=?"
            " AND training_id=?",
            (tenant_id, training_id),
        ).fetchone()
    if row is None:
        raise BusinessError(
            PEOPLE_NOT_FOUND_CODE,
            f"no training {training_id!r} in tenant {tenant_id!r}",
        )
    return dict(row)


def due_training(
    store: "BusinessStore", tenant_id: str, *, as_of: str | None = None
) -> list[dict[str, Any]]:
    moment = as_of or now_iso()
    with store.read() as conn:
        _ensure(conn)
        rows = conn.execute(
            "SELECT * FROM bos_people_training WHERE tenant_id=? AND status='required'"
            " AND (expires_at IS NULL OR expires_at <= ?)"
            " ORDER BY expires_at, training_id",
            (tenant_id, moment),
        ).fetchall()
    return [dict(row) for row in rows]


# -- procurement -----------------------------------------------------------
def _current_period(moment: str | None = None) -> str:
    return (moment or now_iso())[:7]


def _committed_procurement(
    store: "BusinessStore",
    tenant_id: str,
    business_id: str,
    currency: str,
    period: str,
    *,
    exclude_request_id: str | None = None,
) -> int:
    """Procurement money already approved/completed in this budget period."""
    with store.read() as conn:
        if not _table_exists(conn, "bos_people_procurement"):
            return 0
        query = (
            "SELECT COALESCE(SUM(amount_minor), 0) FROM bos_people_procurement"
            " WHERE tenant_id=? AND business_id=? AND currency=?"
            " AND budget_period=? AND status IN ('approved','completed')"
        )
        params: list[Any] = [tenant_id, business_id, currency, period]
        if exclude_request_id is not None:
            query += " AND request_id<>?"
            params.append(exclude_request_id)
        return int(conn.execute(query, params).fetchone()[0] or 0)


def _spend_cap(
    store: "BusinessStore",
    tenant_id: str,
    business_id: str,
    currency: str,
    amount_minor: int,
    *,
    period: str | None = None,
    exclude_request_id: str | None = None,
) -> dict[str, Any]:
    clean_period = period or _current_period()
    status = store.budget_status(
        tenant_id, business_id, currency=currency, period=clean_period
    )
    if not status["configured"]:
        raise BusinessError(
            PEOPLE_SPEND_CAP_NOT_CONFIGURED_CODE,
            f"no {currency} budget is configured for {clean_period}; refusing to"
            " spend without a cap",
        )
    committed = _committed_procurement(
        store,
        tenant_id,
        business_id,
        currency,
        clean_period,
        exclude_request_id=exclude_request_id,
    )
    if status["remaining_minor"] is not None:
        effective_remaining = int(status["remaining_minor"]) - committed
        status = dict(status)
        status["procurement_committed_minor"] = committed
        status["effective_remaining_minor"] = effective_remaining
        if amount_minor > effective_remaining:
            raise BusinessError(
                PEOPLE_SPEND_CAP_EXCEEDED_CODE,
                f"requested {amount_minor} {currency} exceeds the remaining"
                f" {effective_remaining} {currency} for {clean_period}"
                f" (budget minus {committed} already committed)",
            )
    return status


def _procurement_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "request_id": row["request_id"],
        "tenant_id": row["tenant_id"],
        "business_id": row["business_id"],
        "vendor_contact_id": row["vendor_contact_id"],
        "vendor_name": row["vendor_name"],
        "requester": row["requester"],
        "amount_minor": int(row["amount_minor"]),
        "currency": row["currency"],
        "purpose": row["purpose"],
        "status": row["status"],
        "approval_id": row["approval_id"],
        "receipt_artifact_id": row["receipt_artifact_id"],
        "ledger_debit_id": row["ledger_debit_id"],
        "ledger_credit_id": row["ledger_credit_id"],
        "budget_period": row["budget_period"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def request_purchase(
    store: "BusinessStore",
    tenant_id: str,
    business_id: str,
    *,
    vendor_name: str,
    purpose: str,
    amount_minor: int,
    currency: str = "USD",
    requester: str,
    vendor_contact_id: str | None = None,
    idempotency_key: str,
    actor: str = "system",
) -> dict[str, Any]:
    if (
        not isinstance(amount_minor, int)
        or isinstance(amount_minor, bool)
        or amount_minor < 0
    ):
        raise BusinessError(
            PEOPLE_BAD_INPUT_CODE, "amount_minor must be a non-negative integer"
        )
    code = _currency(currency)
    key = _text(idempotency_key, "idempotency key", max_len=300)
    requester_clean = _text(requester, "requester", max_len=200)
    vendor = _text(vendor_name, "vendor_name", max_len=300)
    reason = _text(purpose, "purpose", max_len=2000)
    contact_id = _optional_text(vendor_contact_id, "vendor_contact_id", max_len=80)
    request_hash = fingerprint(
        {"action": "procurement.purchase", "business_id": business_id,
         "vendor_name": vendor, "purpose": reason,
         "amount_minor": amount_minor, "currency": code,
         "requester": requester_clean, "vendor_contact_id": contact_id}
    )
    with store.read() as conn:
        _ensure(conn)
        store._require_business(conn, tenant_id, business_id)
        existing = conn.execute(
            "SELECT * FROM bos_people_procurement WHERE tenant_id=?"
            " AND idempotency_key=?",
            (tenant_id, key),
        ).fetchone()
        if existing is not None:
            if existing["request_hash"] != request_hash:
                raise BusinessError(
                    "BUSINESS_IDEMPOTENCY_CONFLICT",
                    f"idempotency key {key!r} was already used for a different request",
                )
            return _procurement_dict(existing)
        if contact_id is not None:
            store.get_contact(tenant_id, contact_id)
    request_id = new_id("prq")
    intent = approvals.normalized_intent(
        action="procurement.purchase",
        target=request_id,
        parameters={
            "request_id": request_id,
            "vendor_name": vendor,
            "purpose": reason,
            "requester": requester_clean,
            "business_id": business_id,
        },
        currency=code,
        amount_minor=amount_minor,
        policy_revision="procurement-v1",
        business_id=business_id,
    )
    approval, _created = approvals.request(
        store,
        tenant_id=tenant_id,
        business_id=business_id,
        action="procurement.purchase",
        requested_by=requester_clean,
        idempotency_key=f"procurement:{key}",
        payload=intent,
        policy_version="procurement-v1",
    )
    created = now_iso()
    with store.transaction() as conn:
        _ensure(conn)
        existing = conn.execute(
            "SELECT * FROM bos_people_procurement WHERE tenant_id=?"
            " AND idempotency_key=?",
            (tenant_id, key),
        ).fetchone()
        if existing is not None:
            return _procurement_dict(existing)
        conn.execute(
            "INSERT INTO bos_people_procurement(request_id, tenant_id,"
            " business_id, vendor_contact_id, vendor_name, requester,"
            " amount_minor, currency, purpose, status, approval_id,"
            " idempotency_key, request_hash, created_at, updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,'awaiting_approval',?,?,?,?,?)",
            (
                request_id,
                tenant_id,
                business_id,
                contact_id,
                vendor,
                requester_clean,
                amount_minor,
                code,
                reason,
                approval.approval_id,
                key,
                request_hash,
                created,
                created,
            ),
        )
        store._audit(
            conn, tenant_id, business_id, actor, "people.procurement.request",
            "procurement", request_id, "ok",
            {"amount_minor": amount_minor, "currency": code,
             "vendor_name": vendor, "approval_id": approval.approval_id},
        )
    return get_purchase(store, tenant_id, request_id)


def get_purchase(
    store: "BusinessStore", tenant_id: str, request_id: str
) -> dict[str, Any]:
    with store.read() as conn:
        _ensure(conn)
        row = conn.execute(
            "SELECT * FROM bos_people_procurement WHERE tenant_id=?"
            " AND request_id=?",
            (tenant_id, request_id),
        ).fetchone()
    if row is None:
        raise BusinessError(
            PEOPLE_NOT_FOUND_CODE,
            f"no procurement request {request_id!r} in tenant {tenant_id!r}",
        )
    return _procurement_dict(row)


def list_purchases(
    store: "BusinessStore",
    tenant_id: str,
    *,
    business_id: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    query = "SELECT * FROM bos_people_procurement WHERE tenant_id=?"
    params: list[Any] = [tenant_id]
    if business_id is not None:
        query += " AND business_id=?"
        params.append(business_id)
    if status is not None:
        if status not in PROCUREMENT_STATUSES:
            raise BusinessError(
                PEOPLE_BAD_INPUT_CODE, f"unknown procurement status {status!r}"
            )
        query += " AND status=?"
        params.append(status)
    query += " ORDER BY created_at, request_id"
    with store.read() as conn:
        _ensure(conn)
        return [_procurement_dict(row) for row in conn.execute(query, params).fetchall()]


def approve_purchase(
    store: "BusinessStore",
    tenant_id: str,
    request_id: str,
    *,
    decided_by: str,
    reason: str = "",
    actor: str = "system",
) -> dict[str, Any]:
    request = get_purchase(store, tenant_id, request_id)
    approver = _text(decided_by, "decided_by", max_len=200)
    if approver == request["requester"]:
        raise BusinessError(
            PEOPLE_SELF_APPROVAL_CODE,
            "the requester cannot approve their own purchase (separation of duties)",
        )
    if request["status"] != "awaiting_approval":
        raise BusinessError(
            PEOPLE_BAD_STATE_CODE,
            f"purchase is already {request['status']!r}",
        )
    approvals.decide(
        store,
        tenant_id=tenant_id,
        approval_id=request["approval_id"],
        decision="approved",
        decided_by=approver,
        reason=reason,
    )
    period = _current_period()
    try:
        _spend_cap(
            store,
            tenant_id,
            request["business_id"],
            request["currency"],
            request["amount_minor"],
            period=period,
            exclude_request_id=request_id,
        )
    except BusinessError:
        with store.transaction() as conn:
            _ensure(conn)
            conn.execute(
                "UPDATE bos_people_procurement SET status='blocked_spend_cap',"
                " updated_at=? WHERE tenant_id=? AND request_id=?",
                (now_iso(), tenant_id, request_id),
            )
            store._audit(
                conn, tenant_id, request["business_id"], actor,
                "people.procurement.blocked", "procurement", request_id,
                "refused", {"decided_by": approver, "period": period},
            )
        raise
    moment = now_iso()
    with store.transaction() as conn:
        _ensure(conn)
        conn.execute(
            "UPDATE bos_people_procurement SET status='approved',"
            " budget_period=?, updated_at=? WHERE tenant_id=? AND request_id=?",
            (period, moment, tenant_id, request_id),
        )
        store._audit(
            conn, tenant_id, request["business_id"], actor,
            "people.procurement.approve", "procurement", request_id, "ok",
            {"decided_by": approver, "period": period},
        )
    return get_purchase(store, tenant_id, request_id)


def reject_purchase(
    store: "BusinessStore",
    tenant_id: str,
    request_id: str,
    *,
    decided_by: str,
    reason: str = "",
    actor: str = "system",
) -> dict[str, Any]:
    request = get_purchase(store, tenant_id, request_id)
    if request["status"] != "awaiting_approval":
        raise BusinessError(
            PEOPLE_BAD_STATE_CODE, f"purchase is already {request['status']!r}"
        )
    approvals.decide(
        store,
        tenant_id=tenant_id,
        approval_id=request["approval_id"],
        decision="rejected",
        decided_by=_text(decided_by, "decided_by", max_len=200),
        reason=reason,
    )
    with store.transaction() as conn:
        _ensure(conn)
        conn.execute(
            "UPDATE bos_people_procurement SET status='rejected', updated_at=?"
            " WHERE tenant_id=? AND request_id=?",
            (now_iso(), tenant_id, request_id),
        )
        store._audit(
            conn, tenant_id, request["business_id"], actor,
            "people.procurement.reject", "procurement", request_id, "ok",
            {"decided_by": decided_by},
        )
    return get_purchase(store, tenant_id, request_id)


def record_receipt(
    store: "BusinessStore",
    tenant_id: str,
    request_id: str,
    *,
    receipt_name: str,
    receipt_body: str,
    actor: str = "system",
) -> dict[str, Any]:
    """Commit the double-entry spend and attach the provider receipt."""
    request = get_purchase(store, tenant_id, request_id)
    if request["status"] != "approved":
        raise BusinessError(
            PEOPLE_BAD_STATE_CODE,
            f"a receipt requires an approved purchase (status={request['status']!r})",
        )
    period = request["budget_period"] or _current_period()
    _spend_cap(
        store,
        tenant_id,
        request["business_id"],
        request["currency"],
        request["amount_minor"],
        period=period,
        exclude_request_id=request_id,
    )
    debit, _ = ledger.append(
        store,
        tenant_id=tenant_id,
        business_id=request["business_id"],
        account="expense:procurement",
        direction="debit",
        amount_minor=request["amount_minor"],
        currency=request["currency"],
        idempotency_key=f"procurement:{request_id}:debit",
        memo=f"{request['vendor_name']}: {request['purpose']}"[:2000],
        actor=actor,
    )
    credit, _ = ledger.append(
        store,
        tenant_id=tenant_id,
        business_id=request["business_id"],
        account="cash",
        direction="credit",
        amount_minor=request["amount_minor"],
        currency=request["currency"],
        idempotency_key=f"procurement:{request_id}:credit",
        memo=f"{request['vendor_name']}: {request['purpose']}"[:2000],
        actor=actor,
    )
    artifact = store.record_artifact(
        tenant_id,
        request["business_id"],
        kind="procurement_receipt",
        name=_text(receipt_name, "receipt_name", max_len=300),
        body=receipt_body if isinstance(receipt_body, str) else "",
        media_type="text/plain",
    )
    moment = now_iso()
    with store.transaction() as conn:
        _ensure(conn)
        conn.execute(
            "UPDATE bos_people_procurement SET status='completed',"
            " receipt_artifact_id=?, ledger_debit_id=?, ledger_credit_id=?,"
            " updated_at=? WHERE tenant_id=? AND request_id=?",
            (artifact.artifact_id, debit.entry_id, credit.entry_id, moment,
             tenant_id, request_id),
        )
        store._audit(
            conn, tenant_id, request["business_id"], actor,
            "people.procurement.receipt", "procurement", request_id, "ok",
            {"receipt_artifact_id": artifact.artifact_id,
             "ledger_debit_id": debit.entry_id,
             "ledger_credit_id": credit.entry_id},
        )
    return {
        "request": get_purchase(store, tenant_id, request_id),
        "receipt_artifact_id": artifact.artifact_id,
        "receipt_sha256": artifact.sha256,
        "ledger_debit_id": debit.entry_id,
        "ledger_credit_id": credit.entry_id,
    }


# -- Business OS agent lifecycle ------------------------------------------
def set_agent_lifecycle(
    store: "BusinessStore",
    tenant_id: str,
    person_id: str,
    *,
    action: str,
    actor: str = "system",
    note: str = "",
) -> dict[str, Any]:
    """Move the person's linked Business OS agent through its real lifecycle.

    This never creates an agent and never touches an agent from another
    business. ``revoke`` is immediate and is the action ``leave`` uses.
    """
    clean_action = _text(action, "action", max_len=20).lower()
    if clean_action not in ("pause", "resume", "revoke"):
        raise BusinessError(
            PEOPLE_BAD_INPUT_CODE, "agent action must be pause, resume or revoke"
        )
    with store.read() as conn:
        _ensure(conn)
        person = _require_person(conn, tenant_id, person_id)
    if not person["agent_id"]:
        raise BusinessError(
            PEOPLE_BAD_STATE_CODE, "this person has no linked Business OS agent"
        )
    service = AgentControlService(store)
    agent = service.get(tenant_id, person["agent_id"])
    if agent.business_id != person["business_id"]:
        raise BusinessError(
            PEOPLE_ACCESS_MISMATCH_CODE,
            "the linked agent belongs to a different business",
        )
    if clean_action == "pause":
        service.pause(
            tenant_id, agent.agent_id,
            idempotency_key=f"people.agent.pause:{person_id}",
            actor=actor, note=note,
        )
    elif clean_action == "resume":
        service.resume(
            tenant_id, agent.agent_id,
            idempotency_key=f"people.agent.resume:{person_id}",
            actor=actor,
        )
    else:
        service.revoke(
            tenant_id, agent.agent_id,
            idempotency_key=f"people.agent.revoke:{person_id}",
            actor=actor, note=note or "people lifecycle revoke",
        )
    updated = service.get(tenant_id, agent.agent_id)
    with store.transaction() as conn:
        _ensure(conn)
        store._audit(
            conn, tenant_id, person["business_id"], actor,
            f"people.agent.{clean_action}", "agent", agent.agent_id, "ok",
            {"person_id": person_id, "status": updated.status},
        )
    return {
        "person_id": person_id,
        "agent_id": updated.agent_id,
        "action": clean_action,
        "status": updated.status,
        "declared_statuses": list(AGENT_STATUSES),
    }


def declared_statuses() -> dict[str, Iterable[str]]:
    return {
        "person": PERSON_STATUSES,
        "assignment": ASSIGNMENT_STATUSES,
        "training": TRAINING_STATUSES,
        "procurement": PROCUREMENT_STATUSES,
    }

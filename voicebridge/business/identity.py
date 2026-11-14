"""Tenant-scoped identity, RBAC + ABAC and short-lived session tokens.

This module is the P1-01 boundary. It lives on the same Business OS SQLite
file as everything else — there is no second database and no second identity
provider. The defaults are deliberately hostile:

* **Deny by default.** :func:`evaluate` returns an allowed :class:`Decision`
  only for an unexpired, unrevoked grant in the *same* tenant whose
  capability/resource/action/data_class/environment all match. A missing or
  inactive principal is a denial, never an implicit pass.
* **Deny wins.** An active matching ``effect='deny'`` grant refuses even when
  an allow grant also matches.
* **No wildcards.** ``*``/``?`` are refused at grant/role creation and at
  evaluation time. A grant is an explicit tuple, not a namespace.
* **Tenant isolation.** Principal lookup and grant lookup are both scoped by
  ``tenant_id``; a grant for tenant A can never authorize tenant B.
* **Short-lived, audience-bound tokens.** A token is random, stored only as a
  SHA-256 hash, expires, is audience-bound, and dies when its principal is
  revoked.

Grants are mutable on purpose: revocation is a first-class operation, so
``revoke_grant`` stamps ``revoked_at`` and writes an audit consequence in the
same transaction. Policy revisions (``bos_policy_revision``) are append-only
and are not rewritten by this module.
"""
from __future__ import annotations

import hashlib
import secrets
import sqlite3
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterable, Mapping

from .errors import BAD_INPUT_CODE, BusinessError
from .ids import new_id
from .util import json_dumps, json_loads, now_iso, redact, require_text

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .store import BusinessStore

PRINCIPAL_KINDS = ("owner", "agent", "service", "run")
PRINCIPAL_STATUSES = ("active", "suspended", "revoked")
GRANT_EFFECTS = ("allow", "deny")
DEFAULT_SESSION_TTL_SECONDS = 300.0
MAX_SESSION_TTL_SECONDS = 3600.0

#: Stable local refusal codes. They are part of this module's API; tests and
#: callers branch on them. They intentionally do not reuse the generic
#: ``BUSINESS_BAD_STATE`` for authorization, because "denied" and "invalid
#: transition" are different operator actions.
PRINCIPAL_NOT_FOUND_CODE = "BUSINESS_PRINCIPAL_NOT_FOUND"
PRINCIPAL_EXISTS_CODE = "BUSINESS_PRINCIPAL_EXISTS"
ROLE_NOT_FOUND_CODE = "BUSINESS_ROLE_NOT_FOUND"
GRANT_NOT_FOUND_CODE = "BUSINESS_GRANT_NOT_FOUND"
AUTH_SESSION_NOT_FOUND_CODE = "BUSINESS_AUTH_SESSION_NOT_FOUND"
AUTH_TOKEN_INVALID_CODE = "BUSINESS_AUTH_TOKEN_INVALID"
AUTH_TOKEN_EXPIRED_CODE = "BUSINESS_AUTH_TOKEN_EXPIRED"
AUTH_TOKEN_REVOKED_CODE = "BUSINESS_AUTH_TOKEN_REVOKED"
AUTH_AUDIENCE_MISMATCH_CODE = "BUSINESS_AUTH_AUDIENCE_MISMATCH"
AUTH_TENANT_MISMATCH_CODE = "BUSINESS_AUTH_TENANT_MISMATCH"
AUTH_PRINCIPAL_INACTIVE_CODE = "BUSINESS_AUTH_PRINCIPAL_INACTIVE"
WILDCARD_NOT_ALLOWED_CODE = "BUSINESS_WILDCARD_NOT_ALLOWED"

_WILDCARD_CHARS = ("*", "?")


def _text(value: Any, label: str, *, max_len: int = 300) -> str:
    try:
        return require_text(value, label, max_len=max_len)
    except ValueError as exc:
        raise BusinessError(BAD_INPUT_CODE, str(exc)) from exc


def _explicit(value: Any, label: str, *, max_len: int = 300) -> str:
    clean = _text(value, label, max_len=max_len)
    if any(char in clean for char in _WILDCARD_CHARS):
        raise BusinessError(
            WILDCARD_NOT_ALLOWED_CODE,
            f"{label} may not contain a wildcard; grant explicit values only",
        )
    return clean


def _optional_text(value: Any, label: str, *, max_len: int = 300) -> str | None:
    if value is None:
        return None
    return _text(value, label, max_len=max_len)


def _epoch(value: Any, label: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BusinessError(BAD_INPUT_CODE, f"{label} must be an epoch number")
    return float(value)


def _principal_row(
    conn: sqlite3.Connection, tenant_id: str, principal_id: str
) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM bos_principal WHERE tenant_id=? AND principal_id=?",
        (tenant_id, principal_id),
    ).fetchone()
    if row is None:
        raise BusinessError(
            PRINCIPAL_NOT_FOUND_CODE,
            f"no principal {principal_id!r} in tenant {tenant_id!r}",
        )
    return row


def _grant_row(
    conn: sqlite3.Connection, tenant_id: str, grant_id: str
) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM bos_grant WHERE tenant_id=? AND grant_id=?",
        (tenant_id, grant_id),
    ).fetchone()
    if row is None:
        raise BusinessError(
            GRANT_NOT_FOUND_CODE,
            f"no grant {grant_id!r} in tenant {tenant_id!r}",
        )
    return row


# -- typed records ---------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Principal:
    principal_id: str
    tenant_id: str
    kind: str
    external_ref: str | None
    status: str
    created_at: str

    @classmethod
    def from_row(cls, row: Any) -> "Principal":
        data = dict(row)
        return cls(
            principal_id=data["principal_id"],
            tenant_id=data["tenant_id"],
            kind=data["kind"],
            external_ref=data["external_ref"],
            status=data["status"],
            created_at=data["created_at"],
        )


@dataclass(frozen=True, slots=True)
class Role:
    role_id: str
    tenant_id: str
    name: str
    permissions: tuple[dict[str, Any], ...]
    created_at: str

    @classmethod
    def from_row(cls, row: Any) -> "Role":
        data = dict(row)
        return cls(
            role_id=data["role_id"],
            tenant_id=data["tenant_id"],
            name=data["name"],
            permissions=tuple(json_loads(data.get("permissions_json"), []) or []),
            created_at=data["created_at"],
        )


@dataclass(frozen=True, slots=True)
class Grant:
    grant_id: str
    tenant_id: str
    subject_id: str
    role_id: str | None
    capability: str
    resource: str
    action: str
    data_class: str
    environment: str
    effect: str
    expires_at: float | None
    approval_required: bool
    revoked_at: str | None
    created_at: str

    @property
    def active(self) -> bool:
        return self.revoked_at is None

    @classmethod
    def from_row(cls, row: Any) -> "Grant":
        data = dict(row)
        return cls(
            grant_id=data["grant_id"],
            tenant_id=data["tenant_id"],
            subject_id=data["subject_id"],
            role_id=data["role_id"],
            capability=data["capability"],
            resource=data["resource"],
            action=data["action"],
            data_class=data["data_class"],
            environment=data["environment"],
            effect=data["effect"],
            expires_at=(
                float(data["expires_at"])
                if data["expires_at"] is not None
                else None
            ),
            approval_required=bool(data["approval_required"]),
            revoked_at=data["revoked_at"],
            created_at=data["created_at"],
        )


@dataclass(frozen=True, slots=True)
class AuthSession:
    auth_session_id: str
    tenant_id: str
    subject_id: str
    audience: str
    issued_at: str
    expires_at: float
    revoked_at: str | None
    token_hash: str

    @classmethod
    def from_row(cls, row: Any) -> "AuthSession":
        data = dict(row)
        return cls(
            auth_session_id=data["auth_session_id"],
            tenant_id=data["tenant_id"],
            subject_id=data["subject_id"],
            audience=data["audience"],
            issued_at=data["issued_at"],
            expires_at=float(data["expires_at"]),
            revoked_at=data["revoked_at"],
            token_hash=data["token_hash"],
        )


@dataclass(frozen=True, slots=True)
class Decision:
    allowed: bool
    reason: str
    tenant_id: str
    subject_id: str
    capability: str
    resource: str
    action: str
    data_class: str
    environment: str
    decided_at: str
    grant_id: str | None = None
    matched_role_id: str | None = None
    approval_required: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "tenant_id": self.tenant_id,
            "subject_id": self.subject_id,
            "capability": self.capability,
            "resource": self.resource,
            "action": self.action,
            "data_class": self.data_class,
            "environment": self.environment,
            "decided_at": self.decided_at,
            "grant_id": self.grant_id,
            "matched_role_id": self.matched_role_id,
            "approval_required": self.approval_required,
        }


@dataclass(frozen=True, slots=True)
class StepUp:
    required: bool
    risk_level: int
    action: str
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "required": self.required,
            "risk_level": self.risk_level,
            "action": self.action,
            "reason": self.reason,
        }


# -- principals ------------------------------------------------------------
def create_principal(
    store: "BusinessStore",
    tenant_id: str,
    *,
    kind: str,
    external_ref: str | None = None,
    status: str = "active",
    principal_id: str | None = None,
    actor: str = "system",
) -> Principal:
    """Create one principal inside a tenant. There is no cross-tenant principal."""
    kind_clean = _text(kind, "principal kind", max_len=20).lower()
    if kind_clean not in PRINCIPAL_KINDS:
        raise BusinessError(
            BAD_INPUT_CODE,
            f"unknown principal kind {kind_clean!r}; expected one of "
            f"{', '.join(PRINCIPAL_KINDS)}",
        )
    status_clean = _text(status, "principal status", max_len=20).lower()
    if status_clean not in PRINCIPAL_STATUSES:
        raise BusinessError(BAD_INPUT_CODE, f"unknown principal status {status!r}")
    ref = _optional_text(external_ref, "external_ref", max_len=300)
    pid = _text(principal_id or new_id("prn"), "principal id", max_len=80)
    created = now_iso()
    with store.transaction() as conn:
        store._require_tenant(conn, tenant_id)
        try:
            conn.execute(
                "INSERT INTO bos_principal(principal_id, tenant_id, kind,"
                " external_ref, status, created_at) VALUES(?,?,?,?,?,?)",
                (pid, tenant_id, kind_clean, ref, status_clean, created),
            )
        except sqlite3.IntegrityError as exc:
            raise BusinessError(
                PRINCIPAL_EXISTS_CODE,
                f"principal {pid!r} or external ref {ref!r} already exists",
            ) from exc
        store._audit(
            conn, tenant_id, None, actor, "principal.create", "principal", pid,
            "ok", {"kind": kind_clean, "external_ref": ref},
        )
    return get_principal(store, tenant_id, pid)


def get_principal(
    store: "BusinessStore", tenant_id: str, principal_id: str
) -> Principal:
    with store.read() as conn:
        return Principal.from_row(_principal_row(conn, tenant_id, principal_id))


def list_principals(store: "BusinessStore", tenant_id: str) -> list[Principal]:
    with store.read() as conn:
        store._require_tenant(conn, tenant_id)
        rows = conn.execute(
            "SELECT * FROM bos_principal WHERE tenant_id=?"
            " ORDER BY created_at, principal_id",
            (tenant_id,),
        ).fetchall()
    return [Principal.from_row(row) for row in rows]


def revoke_principal(
    store: "BusinessStore",
    tenant_id: str,
    principal_id: str,
    *,
    actor: str,
    now: float | None = None,
) -> Principal:
    """Revoke a principal and every session it holds, in one transaction.

    Authorization is checked against the principal row on every
    :func:`evaluate`, so this takes effect immediately for grants too.
    """
    moment = time.time() if now is None else float(now)
    with store.transaction() as conn:
        _principal_row(conn, tenant_id, principal_id)
        conn.execute(
            "UPDATE bos_principal SET status='revoked'"
            " WHERE tenant_id=? AND principal_id=?",
            (tenant_id, principal_id),
        )
        conn.execute(
            "UPDATE bos_auth_session SET revoked_at=?"
            " WHERE tenant_id=? AND subject_id=? AND revoked_at IS NULL",
            (now_iso(moment), tenant_id, principal_id),
        )
        store._audit(
            conn, tenant_id, None, actor, "principal.revoke", "principal",
            principal_id, "ok", {"sessions_revoked": True},
        )
    return get_principal(store, tenant_id, principal_id)


# -- roles -----------------------------------------------------------------
def _permissions(value: Iterable[Mapping[str, Any]] | None) -> str:
    if value is None:
        return "[]"
    if isinstance(value, (str, bytes, Mapping)):
        raise BusinessError(
            BAD_INPUT_CODE, "role permissions must be a sequence of mappings"
        )
    clean: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise BusinessError(
                BAD_INPUT_CODE, "each role permission must be a mapping"
            )
        entry = {
            "capability": _explicit(item.get("capability"), "capability"),
            "resource": _explicit(item.get("resource"), "resource"),
            "action": _explicit(item.get("action"), "action"),
            "data_class": _explicit(
                item.get("data_class", "internal"), "data_class", max_len=80
            ),
            "environment": _explicit(
                item.get("environment", "production"), "environment", max_len=80
            ),
            "effect": _text(
                item.get("effect", "allow"), "effect", max_len=20
            ).lower(),
        }
        if entry["effect"] not in GRANT_EFFECTS:
            raise BusinessError(
                BAD_INPUT_CODE, f"unknown grant effect {entry['effect']!r}"
            )
        clean.append(entry)
    return json_dumps(redact(clean))


def create_role(
    store: "BusinessStore",
    tenant_id: str,
    name: str,
    permissions: Iterable[Mapping[str, Any]] | None = None,
    *,
    role_id: str | None = None,
    actor: str = "system",
) -> Role:
    clean_name = _text(name, "role name", max_len=120)
    body = _permissions(permissions)
    rid = _text(role_id or new_id("role"), "role id", max_len=80)
    created = now_iso()
    with store.transaction() as conn:
        store._require_tenant(conn, tenant_id)
        try:
            conn.execute(
                "INSERT INTO bos_role(role_id, tenant_id, name, permissions_json,"
                " created_at) VALUES(?,?,?,?,?)",
                (rid, tenant_id, clean_name, body, created),
            )
        except sqlite3.IntegrityError as exc:
            raise BusinessError(
                BAD_INPUT_CODE, f"role {clean_name!r} already exists"
            ) from exc
        store._audit(
            conn, tenant_id, None, actor, "role.create", "role", rid, "ok",
            {"name": clean_name},
        )
    return get_role(store, tenant_id, rid)


def get_role(store: "BusinessStore", tenant_id: str, role_id: str) -> Role:
    with store.read() as conn:
        row = conn.execute(
            "SELECT * FROM bos_role WHERE tenant_id=? AND role_id=?",
            (tenant_id, role_id),
        ).fetchone()
    if row is None:
        raise BusinessError(
            ROLE_NOT_FOUND_CODE, f"no role {role_id!r} in tenant {tenant_id!r}"
        )
    return Role.from_row(row)


# -- grants ----------------------------------------------------------------
def grant(
    store: "BusinessStore",
    tenant_id: str,
    *,
    subject_id: str,
    capability: str,
    resource: str,
    action: str,
    data_class: str = "internal",
    environment: str = "production",
    role_id: str | None = None,
    effect: str = "allow",
    expires_at: float | None = None,
    approval_required: bool = False,
    grant_id: str | None = None,
    actor: str = "system",
) -> Grant:
    """Create an explicit allow/deny grant for one tenant-scoped subject."""
    cap = _explicit(capability, "capability", max_len=200)
    res = _explicit(resource, "resource", max_len=300)
    act = _explicit(action, "action", max_len=120)
    dc = _explicit(data_class, "data_class", max_len=80)
    env = _explicit(environment, "environment", max_len=80)
    eff = _text(effect, "effect", max_len=20).lower()
    if eff not in GRANT_EFFECTS:
        raise BusinessError(
            BAD_INPUT_CODE, f"unknown grant effect {eff!r}; expected allow or deny"
        )
    exp = _epoch(expires_at, "expires_at")
    rid = _optional_text(role_id, "role_id", max_len=80)
    gid = _text(grant_id or new_id("grnt"), "grant id", max_len=80)
    created = now_iso()
    with store.transaction() as conn:
        _principal_row(conn, tenant_id, subject_id)
        if rid is not None:
            role = conn.execute(
                "SELECT 1 FROM bos_role WHERE tenant_id=? AND role_id=?",
                (tenant_id, rid),
            ).fetchone()
            if role is None:
                raise BusinessError(
                    ROLE_NOT_FOUND_CODE,
                    f"no role {rid!r} in tenant {tenant_id!r}",
                )
        conn.execute(
            "INSERT INTO bos_grant(grant_id, tenant_id, subject_id, role_id,"
            " capability, resource, action, data_class, environment, effect,"
            " expires_at, approval_required, created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                gid,
                tenant_id,
                subject_id,
                rid,
                cap,
                res,
                act,
                dc,
                env,
                eff,
                exp,
                1 if approval_required else 0,
                created,
            ),
        )
        store._audit(
            conn, tenant_id, None, actor, "grant.create", "grant", gid, "ok",
            {
                "subject_id": subject_id,
                "capability": cap,
                "resource": res,
                "action": act,
                "data_class": dc,
                "environment": env,
                "effect": eff,
                "role_id": rid,
            },
        )
    return get_grant(store, tenant_id, gid)


def get_grant(store: "BusinessStore", tenant_id: str, grant_id: str) -> Grant:
    with store.read() as conn:
        return Grant.from_row(_grant_row(conn, tenant_id, grant_id))


def list_grants(
    store: "BusinessStore", tenant_id: str, *, subject_id: str | None = None
) -> list[Grant]:
    query = "SELECT * FROM bos_grant WHERE tenant_id=?"
    params: list[Any] = [tenant_id]
    if subject_id is not None:
        query += " AND subject_id=?"
        params.append(subject_id)
    query += " ORDER BY created_at, grant_id"
    with store.read() as conn:
        rows = conn.execute(query, params).fetchall()
    return [Grant.from_row(row) for row in rows]


def revoke_grant(
    store: "BusinessStore",
    tenant_id: str,
    grant_id: str,
    *,
    actor: str,
    now: float | None = None,
) -> Grant:
    """Revoke a grant immediately; a second revoke is a no-op, not a new row."""
    moment = time.time() if now is None else float(now)
    with store.transaction() as conn:
        row = _grant_row(conn, tenant_id, grant_id)
        if row["revoked_at"] is None:
            conn.execute(
                "UPDATE bos_grant SET revoked_at=?"
                " WHERE tenant_id=? AND grant_id=?",
                (now_iso(moment), tenant_id, grant_id),
            )
            store._audit(
                conn, tenant_id, None, actor, "grant.revoke", "grant", grant_id,
                "ok", {"subject_id": row["subject_id"]},
            )
    return get_grant(store, tenant_id, grant_id)


# -- evaluation ------------------------------------------------------------
def _role_candidates(
    conn: sqlite3.Connection, tenant_id: str, row: sqlite3.Row
) -> list[dict[str, Any]]:
    """Expand a role into concrete candidates, grant fields as the fallback."""
    role_id = row["role_id"]
    if role_id is None:
        return []
    role = conn.execute(
        "SELECT * FROM bos_role WHERE tenant_id=? AND role_id=?",
        (tenant_id, role_id),
    ).fetchone()
    if role is None:
        return []
    candidates: list[dict[str, Any]] = []
    for item in json_loads(role["permissions_json"], []) or []:
        if not isinstance(item, Mapping):
            continue
        candidates.append(
            {
                "capability": item.get("capability"),
                "resource": item.get("resource", row["resource"]),
                "action": item.get("action", row["action"]),
                "data_class": item.get("data_class", row["data_class"]),
                "environment": item.get("environment", row["environment"]),
                "effect": item.get("effect", "allow"),
            }
        )
    return candidates


def _matches(candidate: Mapping[str, Any], *, capability: str, resource: str,
             action: str, data_class: str, environment: str) -> bool:
    return (
        candidate.get("capability") == capability
        and candidate.get("resource") == resource
        and candidate.get("action") == action
        and candidate.get("data_class") == data_class
        and candidate.get("environment") == environment
    )


def evaluate(
    store: "BusinessStore",
    tenant_id: str,
    subject_id: str,
    capability: str,
    resource: str,
    action: str,
    data_class: str = "internal",
    environment: str = "production",
    *,
    now: float | None = None,
) -> Decision:
    """Return a deny-by-default authorization decision.

    Case A (tenant): every lookup is filtered by ``tenant_id``. A subject from
    another tenant is "not found" here, which is a denial.
    """
    moment = time.time() if now is None else float(now)
    decided = now_iso(moment)
    cap = str(capability or "")
    res = str(resource or "")
    act = str(action or "")
    dc = str(data_class or "")
    env = str(environment or "")

    def deny(reason: str, *, grant_id: str | None = None,
             role_id: str | None = None) -> Decision:
        return Decision(
            allowed=False,
            reason=reason,
            tenant_id=tenant_id,
            subject_id=subject_id,
            capability=cap,
            resource=res,
            action=act,
            data_class=dc,
            environment=env,
            decided_at=decided,
            grant_id=grant_id,
            matched_role_id=role_id,
        )

    if any(char in value for value in (cap, res, act) for char in _WILDCARD_CHARS):
        return deny("wildcard_not_allowed")

    with store.read() as conn:
        principal = conn.execute(
            "SELECT * FROM bos_principal WHERE tenant_id=? AND principal_id=?",
            (tenant_id, subject_id),
        ).fetchone()
        if principal is None:
            return deny("principal_not_found")
        if principal["status"] != "active":
            return deny("principal_inactive")
        rows = conn.execute(
            "SELECT * FROM bos_grant WHERE tenant_id=? AND subject_id=?"
            " ORDER BY created_at, grant_id",
            (tenant_id, subject_id),
        ).fetchall()

        allowed: Decision | None = None
        for row in rows:
            if row["revoked_at"] is not None:
                continue
            expires = row["expires_at"]
            if expires is not None and float(expires) <= moment:
                continue
            candidates = [
                {
                    "capability": row["capability"],
                    "resource": row["resource"],
                    "action": row["action"],
                    "data_class": row["data_class"],
                    "environment": row["environment"],
                    "effect": row["effect"],
                }
            ]
            candidates.extend(_role_candidates(conn, tenant_id, row))
            for candidate in candidates:
                if not _matches(
                    candidate,
                    capability=cap,
                    resource=res,
                    action=act,
                    data_class=dc,
                    environment=env,
                ):
                    continue
                if candidate.get("effect") == "deny":
                    # Deny wins even if an allow was already seen.
                    return deny(
                        "deny_grant",
                        grant_id=row["grant_id"],
                        role_id=row["role_id"],
                    )
                if allowed is None:
                    allowed = Decision(
                        allowed=True,
                        reason="grant_matched",
                        tenant_id=tenant_id,
                        subject_id=subject_id,
                        capability=cap,
                        resource=res,
                        action=act,
                        data_class=dc,
                        environment=env,
                        decided_at=decided,
                        grant_id=row["grant_id"],
                        matched_role_id=row["role_id"],
                        approval_required=bool(row["approval_required"]),
                    )
        if allowed is not None:
            return allowed
    return deny("no_matching_grant")


# -- session tokens --------------------------------------------------------
def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_session_token(
    store: "BusinessStore",
    tenant_id: str,
    subject_id: str,
    *,
    audience: str,
    ttl_seconds: float = DEFAULT_SESSION_TTL_SECONDS,
    now: float | None = None,
    actor: str = "system",
) -> tuple[str, AuthSession]:
    """Mint a short-lived, audience-bound token; only its hash is persisted."""
    clean_audience = _text(audience, "audience", max_len=200)
    if ttl_seconds <= 0 or ttl_seconds > MAX_SESSION_TTL_SECONDS:
        raise BusinessError(
            BAD_INPUT_CODE,
            f"ttl_seconds must be in (0, {MAX_SESSION_TTL_SECONDS:g}]",
        )
    moment = time.time() if now is None else float(now)
    token = secrets.token_urlsafe(32)
    sid = new_id("asn")
    with store.transaction() as conn:
        principal = _principal_row(conn, tenant_id, subject_id)
        if principal["status"] != "active":
            raise BusinessError(
                AUTH_PRINCIPAL_INACTIVE_CODE,
                f"principal {subject_id!r} is {principal['status']!r}",
            )
        conn.execute(
            "INSERT INTO bos_auth_session(auth_session_id, tenant_id, subject_id,"
            " audience, issued_at, expires_at, token_hash)"
            " VALUES(?,?,?,?,?,?,?)",
            (
                sid,
                tenant_id,
                subject_id,
                clean_audience,
                now_iso(moment),
                moment + float(ttl_seconds),
                _token_hash(token),
            ),
        )
        store._audit(
            conn, tenant_id, None, actor, "auth.session.issue", "auth_session",
            sid, "ok", {"subject_id": subject_id, "audience": clean_audience},
        )
    return token, get_auth_session(store, tenant_id, sid)


def get_auth_session(
    store: "BusinessStore", tenant_id: str, auth_session_id: str
) -> AuthSession:
    with store.read() as conn:
        row = conn.execute(
            "SELECT * FROM bos_auth_session WHERE tenant_id=? AND"
            " auth_session_id=?",
            (tenant_id, auth_session_id),
        ).fetchone()
    if row is None:
        raise BusinessError(
            AUTH_SESSION_NOT_FOUND_CODE,
            f"no auth session {auth_session_id!r} in tenant {tenant_id!r}",
        )
    return AuthSession.from_row(row)


def verify_session_token(
    store: "BusinessStore",
    token: str,
    *,
    audience: str,
    tenant_id: str | None = None,
    now: float | None = None,
) -> AuthSession:
    """Verify a token for an audience; never guess a tenant or an audience."""
    if not isinstance(token, str) or not token.strip():
        raise BusinessError(AUTH_TOKEN_INVALID_CODE, "empty session token")
    clean_audience = _text(audience, "audience", max_len=200)
    moment = time.time() if now is None else float(now)
    with store.read() as conn:
        row = conn.execute(
            "SELECT * FROM bos_auth_session WHERE token_hash=?",
            (_token_hash(token.strip()),),
        ).fetchone()
        if row is None:
            raise BusinessError(AUTH_TOKEN_INVALID_CODE, "unknown session token")
        if row["audience"] != clean_audience:
            raise BusinessError(
                AUTH_AUDIENCE_MISMATCH_CODE,
                f"token audience {row['audience']!r} != {clean_audience!r}",
            )
        if tenant_id is not None and row["tenant_id"] != tenant_id:
            raise BusinessError(
                AUTH_TENANT_MISMATCH_CODE,
                "token belongs to another tenant",
            )
        if row["revoked_at"] is not None:
            raise BusinessError(AUTH_TOKEN_REVOKED_CODE, "session token was revoked")
        if float(row["expires_at"]) <= moment:
            raise BusinessError(AUTH_TOKEN_EXPIRED_CODE, "session token expired")
        principal = conn.execute(
            "SELECT * FROM bos_principal WHERE tenant_id=? AND principal_id=?",
            (row["tenant_id"], row["subject_id"]),
        ).fetchone()
        if principal is None or principal["status"] != "active":
            raise BusinessError(
                AUTH_PRINCIPAL_INACTIVE_CODE,
                "the session's principal is missing or inactive",
            )
    return AuthSession.from_row(row)


def revoke_session_token(
    store: "BusinessStore",
    tenant_id: str,
    auth_session_id: str,
    *,
    actor: str,
    now: float | None = None,
) -> AuthSession:
    moment = time.time() if now is None else float(now)
    with store.transaction() as conn:
        row = conn.execute(
            "SELECT * FROM bos_auth_session WHERE tenant_id=? AND"
            " auth_session_id=?",
            (tenant_id, auth_session_id),
        ).fetchone()
        if row is None:
            raise BusinessError(
                AUTH_SESSION_NOT_FOUND_CODE,
                f"no auth session {auth_session_id!r} in tenant {tenant_id!r}",
            )
        if row["revoked_at"] is None:
            conn.execute(
                "UPDATE bos_auth_session SET revoked_at=?"
                " WHERE tenant_id=? AND auth_session_id=?",
                (now_iso(moment), tenant_id, auth_session_id),
            )
            store._audit(
                conn, tenant_id, None, actor, "auth.session.revoke",
                "auth_session", auth_session_id, "ok",
                {"subject_id": row["subject_id"]},
            )
    return get_auth_session(store, tenant_id, auth_session_id)


# -- step-up ---------------------------------------------------------------
STEP_UP_RISK_LEVEL = 4
#: Any action carrying one of these tokens needs a human step-up regardless of
#: its numeric risk label. Kept small and explicit; no wildcard matching.
_HIGH_RISK_TOKENS = frozenset(
    {
        "payment",
        "payout",
        "refund",
        "transfer",
        "wire",
        "withdraw",
        "credential",
        "secret",
        "key",
        "delete",
        "destroy",
        "destructive",
        "privilege",
        "grant",
        "revoke",
        "publish",
        "contract",
        "sign",
        "legal",
        "trade",
        "deploy",
        "admin",
    }
)


def _risk_level(risk: Any) -> int:
    if isinstance(risk, bool):
        raise BusinessError(BAD_INPUT_CODE, "risk must be L1..L5 or an integer")
    if isinstance(risk, int):
        level = int(risk)
    elif isinstance(risk, str):
        raw = risk.strip().upper()
        if raw.startswith("L"):
            raw = raw[1:]
        try:
            level = int(raw)
        except ValueError as exc:
            raise BusinessError(
                BAD_INPUT_CODE, f"risk {risk!r} is not L1..L5 or an integer"
            ) from exc
    else:
        raise BusinessError(BAD_INPUT_CODE, "risk must be L1..L5 or an integer")
    if level < 1 or level > 5:
        raise BusinessError(BAD_INPUT_CODE, "risk level must be between 1 and 5")
    return level


def require_step_up(action: str, risk: Any) -> StepUp:
    """Decide whether an action needs a fresh human step-up.

    L4/L5 always need it; so does any L1-L5 action whose normalized name
    carries a high-risk token (payments, credentials, destructive/privilege
    changes). The helper returns a decision; it never invents approval.
    """
    clean = _text(action, "action", max_len=200)
    level = _risk_level(risk)
    tokens = set(
        token
        for token in clean.replace(":", ".").replace("/", ".").replace("_", ".")
        .replace("-", ".").lower().split(".")
        if token
    )
    risky = sorted(tokens & _HIGH_RISK_TOKENS)
    if level >= STEP_UP_RISK_LEVEL:
        return StepUp(
            required=True,
            risk_level=level,
            action=clean,
            reason=f"risk L{level} requires step-up",
        )
    if risky:
        return StepUp(
            required=True,
            risk_level=level,
            action=clean,
            reason=f"high-risk action token {risky[0]!r} requires step-up",
        )
    return StepUp(
        required=False,
        risk_level=level,
        action=clean,
        reason="below the step-up threshold",
    )


__all__ = [
    "AUTH_AUDIENCE_MISMATCH_CODE",
    "AUTH_PRINCIPAL_INACTIVE_CODE",
    "AUTH_SESSION_NOT_FOUND_CODE",
    "AUTH_TENANT_MISMATCH_CODE",
    "AUTH_TOKEN_EXPIRED_CODE",
    "AUTH_TOKEN_INVALID_CODE",
    "AUTH_TOKEN_REVOKED_CODE",
    "AuthSession",
    "DEFAULT_SESSION_TTL_SECONDS",
    "Decision",
    "GRANT_EFFECTS",
    "GRANT_NOT_FOUND_CODE",
    "Grant",
    "MAX_SESSION_TTL_SECONDS",
    "PRINCIPAL_EXISTS_CODE",
    "PRINCIPAL_KINDS",
    "PRINCIPAL_NOT_FOUND_CODE",
    "PRINCIPAL_STATUSES",
    "Principal",
    "ROLE_NOT_FOUND_CODE",
    "Role",
    "STEP_UP_RISK_LEVEL",
    "StepUp",
    "WILDCARD_NOT_ALLOWED_CODE",
    "create_principal",
    "create_role",
    "evaluate",
    "get_auth_session",
    "get_grant",
    "get_principal",
    "get_role",
    "grant",
    "issue_session_token",
    "list_grants",
    "list_principals",
    "require_step_up",
    "revoke_grant",
    "revoke_principal",
    "revoke_session_token",
    "verify_session_token",
]

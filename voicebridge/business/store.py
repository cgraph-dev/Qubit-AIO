"""The Business OS store: one SQLite file, its migrations and its primitives.

The store owns *scope*. Every read and write takes a ``tenant_id``; a business
from another tenant is indistinguishable from a missing one. IDs are stable
ULIDs, not row numbers, so an export or a cross-store reference keeps meaning.

Concurrency/durability: WAL, ``synchronous=FULL``, ``busy_timeout`` and
``BEGIN IMMEDIATE`` around writes. A crash after commit loses nothing; a crash
mid-job is recovered as an explicit ``unknown`` outcome by :mod:`jobs`.
"""
from __future__ import annotations

import hashlib
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping

from . import models
from .errors import (
    BAD_INPUT_CODE,
    BAD_STATE_CODE,
    EXISTS_CODE,
    IO_CODE,
    NOT_FOUND_CODE,
    BusinessError,
)
from .ids import fingerprint, new_id
from .schema import MIGRATIONS, SCHEMA_VERSION
from .util import json_dumps, now_iso, redact, require_text

BUSINESS_STATUSES = ("draft", "active", "paused", "archived")
BUSINESS_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"active", "paused", "archived"}),
    "active": frozenset({"paused", "archived"}),
    "paused": frozenset({"active", "archived"}),
    "archived": frozenset(),
}
CONTACT_KINDS = ("customer", "lead", "vendor", "partner")
TASK_STATUSES = ("open", "doing", "done", "cancelled")
TASK_TRANSITIONS: dict[str, frozenset[str]] = {
    "open": frozenset({"doing", "done", "cancelled"}),
    "doing": frozenset({"done", "cancelled"}),
    "done": frozenset(),
    "cancelled": frozenset(),
}
AUDIT_OUTCOMES = ("ok", "refused", "error")


def _text(value: Any, label: str, *, max_len: int = 4096) -> str:
    try:
        return require_text(value, label, max_len=max_len)
    except ValueError as exc:
        raise BusinessError(BAD_INPUT_CODE, str(exc)) from exc


def _currency(value: str) -> str:
    clean = _text(value, "currency", max_len=8).upper()
    if len(clean) != 3 or not clean.isalpha():
        raise BusinessError(BAD_INPUT_CODE, "currency must be a 3-letter code")
    return clean


def _slug(value: str) -> str:
    raw = str(value or "").strip().lower()
    clean = "".join(ch if (ch.isalnum() or ch in "-_") else "-" for ch in raw)
    clean = "-".join(part for part in clean.split("-") if part)
    if not clean:
        raise BusinessError(BAD_INPUT_CODE, "a business needs a slug")
    return clean[:64]


def _metadata(value: Mapping[str, Any] | None) -> str:
    if value is None:
        return "{}"
    if not isinstance(value, Mapping):
        raise BusinessError(BAD_INPUT_CODE, "metadata must be a mapping")
    return json_dumps(redact(dict(value)))


class BusinessStore:
    """One SQLite file and the tenant/business primitives over it."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    @classmethod
    def from_config(cls, cfg: Any) -> "BusinessStore":
        """Resolve ``<state_dir>/business/business.db`` for a real Config."""
        return cls(Path(cfg.state_dir()) / "business" / "business.db")

    # -- connections ---------------------------------------------------
    def connect(self) -> sqlite3.Connection:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            conn = sqlite3.connect(
                str(self._path),
                timeout=10.0,
                isolation_level=None,
                check_same_thread=False,
            )
        except sqlite3.Error as exc:  # pragma: no cover - filesystem refusal
            raise BusinessError(IO_CODE, f"cannot open {self._path}: {exc}") from exc
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        self._migrate(conn)
        return conn

    def _migrate(self, conn: sqlite3.Connection) -> None:
        current = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if current >= SCHEMA_VERSION:
            return
        conn.execute("BEGIN IMMEDIATE")
        try:
            current = int(conn.execute("PRAGMA user_version").fetchone()[0])
            for version, statements in MIGRATIONS:
                if version <= current:
                    continue
                for statement in statements:
                    conn.execute(statement)
                conn.execute(f"PRAGMA user_version={int(version)}")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:  # pragma: no cover - rollback best effort
                pass
            raise
        finally:
            conn.close()

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            yield conn
        finally:
            conn.close()

    # -- lookup helpers ------------------------------------------------
    def _require_tenant(self, conn: sqlite3.Connection, tenant_id: str) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM bos_tenant WHERE tenant_id=?", (tenant_id,)
        ).fetchone()
        if row is None:
            raise BusinessError(NOT_FOUND_CODE, f"no tenant {tenant_id!r}")
        return row

    def _require_business(
        self, conn: sqlite3.Connection, tenant_id: str, business_id: str
    ) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM bos_business WHERE tenant_id=? AND business_id=?",
            (tenant_id, business_id),
        ).fetchone()
        if row is None:
            raise BusinessError(
                NOT_FOUND_CODE,
                f"no business {business_id!r} in tenant {tenant_id!r}",
            )
        return row

    def require_tenant(self, tenant_id: str) -> models.Tenant:
        with self.read() as conn:
            return models.Tenant.from_row(self._require_tenant(conn, tenant_id))

    def require_business(self, tenant_id: str, business_id: str) -> models.Business:
        with self.read() as conn:
            return models.Business.from_row(
                self._require_business(conn, tenant_id, business_id)
            )

    # -- tenants -------------------------------------------------------
    def create_tenant(
        self,
        name: str,
        *,
        tenant_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> models.Tenant:
        clean = _text(name, "tenant name", max_len=200)
        tid = _text(tenant_id or new_id("ten"), "tenant id", max_len=80)
        created = now_iso()
        with self.transaction() as conn:
            try:
                conn.execute(
                    "INSERT INTO bos_tenant(tenant_id, name, created_at,"
                    " metadata_json) VALUES(?,?,?,?)",
                    (tid, clean, created, _metadata(metadata)),
                )
            except sqlite3.IntegrityError as exc:
                raise BusinessError(
                    EXISTS_CODE, f"tenant {clean!r} already exists"
                ) from exc
            self._audit(
                conn, tid, None, "system", "tenant.create", "tenant", tid, "ok",
                {"name": clean},
            )
        return self.get_tenant(tid)

    def get_tenant(self, tenant_id: str) -> models.Tenant:
        with self.read() as conn:
            return models.Tenant.from_row(self._require_tenant(conn, tenant_id))

    def list_tenants(self) -> list[models.Tenant]:
        with self.read() as conn:
            rows = conn.execute(
                "SELECT * FROM bos_tenant ORDER BY created_at, tenant_id"
            ).fetchall()
        return [models.Tenant.from_row(row) for row in rows]

    # -- businesses ----------------------------------------------------
    def create_business(
        self,
        tenant_id: str,
        name: str,
        *,
        slug: str | None = None,
        currency: str = "USD",
        metadata: Mapping[str, Any] | None = None,
    ) -> models.Business:
        clean = _text(name, "business name", max_len=200)
        clean_slug = _slug(slug or name)
        code = _currency(currency)
        bid = new_id("biz")
        created = now_iso()
        with self.transaction() as conn:
            self._require_tenant(conn, tenant_id)
            try:
                conn.execute(
                    "INSERT INTO bos_business(business_id, tenant_id, name, slug,"
                    " status, currency, created_at, updated_at, metadata_json)"
                    " VALUES(?,?,?,?,'draft',?,?,?,?)",
                    (
                        bid,
                        tenant_id,
                        clean,
                        clean_slug,
                        code,
                        created,
                        created,
                        _metadata(metadata),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise BusinessError(
                    EXISTS_CODE,
                    f"business slug {clean_slug!r} already exists in this tenant",
                ) from exc
            self._audit(
                conn, tenant_id, bid, "system", "business.create", "business", bid,
                "ok", {"name": clean, "slug": clean_slug},
            )
        return self.get_business(tenant_id, bid)

    def get_business(self, tenant_id: str, business_id: str) -> models.Business:
        with self.read() as conn:
            return models.Business.from_row(
                self._require_business(conn, tenant_id, business_id)
            )

    def get_business_by_slug(self, tenant_id: str, slug: str) -> models.Business:
        with self.read() as conn:
            row = conn.execute(
                "SELECT * FROM bos_business WHERE tenant_id=? AND slug=?",
                (tenant_id, slug),
            ).fetchone()
        if row is None:
            raise BusinessError(
                NOT_FOUND_CODE, f"no business slug {slug!r} in tenant {tenant_id!r}"
            )
        return models.Business.from_row(row)

    def list_businesses(self, tenant_id: str) -> list[models.Business]:
        with self.read() as conn:
            self._require_tenant(conn, tenant_id)
            rows = conn.execute(
                "SELECT * FROM bos_business WHERE tenant_id=?"
                " ORDER BY created_at, business_id",
                (tenant_id,),
            ).fetchall()
        return [models.Business.from_row(row) for row in rows]

    def set_business_status(
        self, tenant_id: str, business_id: str, status: str, *, actor: str
    ) -> models.Business:
        target = _text(status, "business status", max_len=20)
        if target not in BUSINESS_STATUSES:
            raise BusinessError(
                BAD_INPUT_CODE,
                f"unknown business status {target!r}; expected one of "
                f"{', '.join(BUSINESS_STATUSES)}",
            )
        with self.transaction() as conn:
            row = self._require_business(conn, tenant_id, business_id)
            current = row["status"]
            if current == target:
                return models.Business.from_row(row)
            if target not in BUSINESS_TRANSITIONS[current]:
                raise BusinessError(
                    BAD_STATE_CODE,
                    f"business cannot move {current!r} -> {target!r}",
                )
            updated = now_iso()
            conn.execute(
                "UPDATE bos_business SET status=?, updated_at=?"
                " WHERE tenant_id=? AND business_id=?",
                (target, updated, tenant_id, business_id),
            )
            self._audit(
                conn, tenant_id, business_id, actor, "business.status", "business",
                business_id, "ok", {"from": current, "to": target},
            )
        return self.get_business(tenant_id, business_id)

    # -- contacts ------------------------------------------------------
    def create_contact(
        self,
        tenant_id: str,
        business_id: str,
        *,
        kind: str,
        display_name: str,
        email: str | None = None,
        external_ref: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> models.Contact:
        kind_clean = _text(kind, "contact kind", max_len=20)
        if kind_clean not in CONTACT_KINDS:
            raise BusinessError(
                BAD_INPUT_CODE,
                f"unknown contact kind {kind_clean!r}; expected one of "
                f"{', '.join(CONTACT_KINDS)}",
            )
        name = _text(display_name, "contact display name", max_len=200)
        email_clean = email.strip().lower() if isinstance(email, str) and email.strip() else None
        ref = external_ref.strip() if isinstance(external_ref, str) and external_ref.strip() else None
        cid = new_id("con")
        created = now_iso()
        with self.transaction() as conn:
            self._require_business(conn, tenant_id, business_id)
            existing = None
            if ref is not None:
                existing = conn.execute(
                    "SELECT * FROM bos_contact WHERE tenant_id=? AND business_id=?"
                    " AND external_ref=?",
                    (tenant_id, business_id, ref),
                ).fetchone()
            if existing is None and email_clean is not None:
                existing = conn.execute(
                    "SELECT * FROM bos_contact WHERE tenant_id=? AND business_id=?"
                    " AND lower(email)=?",
                    (tenant_id, business_id, email_clean),
                ).fetchone()
            if existing is not None:
                return models.Contact.from_row(existing)
            conn.execute(
                "INSERT INTO bos_contact(contact_id, tenant_id, business_id, kind,"
                " display_name, email, external_ref, created_at, metadata_json)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    cid,
                    tenant_id,
                    business_id,
                    kind_clean,
                    name,
                    email_clean,
                    ref,
                    created,
                    _metadata(metadata),
                ),
            )
            self._audit(
                conn, tenant_id, business_id, "system", "contact.create", "contact",
                cid, "ok", {"kind": kind_clean, "name": name},
            )
        return self.get_contact(tenant_id, cid)

    def get_contact(self, tenant_id: str, contact_id: str) -> models.Contact:
        with self.read() as conn:
            row = conn.execute(
                "SELECT * FROM bos_contact WHERE tenant_id=? AND contact_id=?",
                (tenant_id, contact_id),
            ).fetchone()
        if row is None:
            raise BusinessError(
                NOT_FOUND_CODE,
                f"no contact {contact_id!r} in tenant {tenant_id!r}",
            )
        return models.Contact.from_row(row)

    def list_contacts(
        self, tenant_id: str, *, business_id: str | None = None
    ) -> list[models.Contact]:
        query = "SELECT * FROM bos_contact WHERE tenant_id=?"
        params: list[Any] = [tenant_id]
        if business_id is not None:
            query += " AND business_id=?"
            params.append(business_id)
        query += " ORDER BY created_at, contact_id"
        with self.read() as conn:
            rows = conn.execute(query, params).fetchall()
        return [models.Contact.from_row(row) for row in rows]

    # -- tasks ---------------------------------------------------------
    def create_task(
        self,
        tenant_id: str,
        business_id: str,
        title: str,
        *,
        assigned_to: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> models.Task:
        clean = _text(title, "task title", max_len=500)
        assignee = assigned_to.strip() if isinstance(assigned_to, str) and assigned_to.strip() else None
        tid = new_id("task")
        created = now_iso()
        with self.transaction() as conn:
            self._require_business(conn, tenant_id, business_id)
            conn.execute(
                "INSERT INTO bos_task(task_id, tenant_id, business_id, title,"
                " status, assigned_to, created_at, updated_at, metadata_json)"
                " VALUES(?,?,?,?,'open',?,?,?,?)",
                (
                    tid,
                    tenant_id,
                    business_id,
                    clean,
                    assignee,
                    created,
                    created,
                    _metadata(metadata),
                ),
            )
            self._audit(
                conn, tenant_id, business_id, "system", "task.create", "task", tid,
                "ok", {"title": clean},
            )
        return self.get_task(tenant_id, tid)

    def get_task(self, tenant_id: str, task_id: str) -> models.Task:
        with self.read() as conn:
            row = conn.execute(
                "SELECT * FROM bos_task WHERE tenant_id=? AND task_id=?",
                (tenant_id, task_id),
            ).fetchone()
        if row is None:
            raise BusinessError(
                NOT_FOUND_CODE, f"no task {task_id!r} in tenant {tenant_id!r}"
            )
        return models.Task.from_row(row)

    def list_tasks(
        self,
        tenant_id: str,
        *,
        business_id: str | None = None,
        status: str | None = None,
    ) -> list[models.Task]:
        query = "SELECT * FROM bos_task WHERE tenant_id=?"
        params: list[Any] = [tenant_id]
        if business_id is not None:
            query += " AND business_id=?"
            params.append(business_id)
        if status is not None:
            query += " AND status=?"
            params.append(status)
        query += " ORDER BY created_at, task_id"
        with self.read() as conn:
            rows = conn.execute(query, params).fetchall()
        return [models.Task.from_row(row) for row in rows]

    def set_task_status(
        self,
        tenant_id: str,
        task_id: str,
        status: str,
        *,
        actor: str,
        assigned_to: str | None = None,
    ) -> models.Task:
        target = _text(status, "task status", max_len=20)
        if target not in TASK_STATUSES:
            raise BusinessError(
                BAD_INPUT_CODE,
                f"unknown task status {target!r}; expected one of "
                f"{', '.join(TASK_STATUSES)}",
            )
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM bos_task WHERE tenant_id=? AND task_id=?",
                (tenant_id, task_id),
            ).fetchone()
            if row is None:
                raise BusinessError(
                    NOT_FOUND_CODE, f"no task {task_id!r} in tenant {tenant_id!r}"
                )
            current = row["status"]
            if current == target and assigned_to is None:
                return models.Task.from_row(row)
            if current != target and target not in TASK_TRANSITIONS[current]:
                raise BusinessError(
                    BAD_STATE_CODE, f"task cannot move {current!r} -> {target!r}"
                )
            updated = now_iso()
            assignee = (
                assigned_to.strip()
                if isinstance(assigned_to, str) and assigned_to.strip()
                else row["assigned_to"]
            )
            conn.execute(
                "UPDATE bos_task SET status=?, assigned_to=?, updated_at=?"
                " WHERE tenant_id=? AND task_id=?",
                (target, assignee, updated, tenant_id, task_id),
            )
            self._audit(
                conn, tenant_id, row["business_id"], actor, "task.status", "task",
                task_id, "ok", {"from": current, "to": target},
            )
        return self.get_task(tenant_id, task_id)

    # -- artifacts -----------------------------------------------------
    def record_artifact(
        self,
        tenant_id: str,
        business_id: str,
        *,
        kind: str,
        name: str,
        body: str | None,
        job_id: str | None = None,
        media_type: str = "text/plain",
    ) -> models.Artifact:
        kind_clean = _text(kind, "artifact kind", max_len=80)
        name_clean = _text(name, "artifact name", max_len=300)
        media = _text(media_type, "media type", max_len=120)
        if body is not None and not isinstance(body, str):
            raise BusinessError(BAD_INPUT_CODE, "artifact body must be text or null")
        payload = body or ""
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        aid = new_id("art")
        created = now_iso()
        with self.transaction() as conn:
            self._require_business(conn, tenant_id, business_id)
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
            conn.execute(
                "INSERT INTO bos_artifact(artifact_id, tenant_id, business_id,"
                " job_id, kind, name, media_type, sha256, size_bytes, body,"
                " created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    aid,
                    tenant_id,
                    business_id,
                    job_id,
                    kind_clean,
                    name_clean,
                    media,
                    digest,
                    len(payload.encode("utf-8")),
                    body,
                    created,
                ),
            )
            self._audit(
                conn, tenant_id, business_id, "system", "artifact.create",
                "artifact", aid, "ok",
                {"kind": kind_clean, "name": name_clean, "sha256": digest},
            )
        return self.get_artifact(tenant_id, aid)

    def get_artifact(self, tenant_id: str, artifact_id: str) -> models.Artifact:
        with self.read() as conn:
            row = conn.execute(
                "SELECT * FROM bos_artifact WHERE tenant_id=? AND artifact_id=?",
                (tenant_id, artifact_id),
            ).fetchone()
        if row is None:
            raise BusinessError(
                NOT_FOUND_CODE,
                f"no artifact {artifact_id!r} in tenant {tenant_id!r}",
            )
        return models.Artifact.from_row(row)

    def list_artifacts(
        self, tenant_id: str, *, business_id: str | None = None
    ) -> list[models.Artifact]:
        query = "SELECT * FROM bos_artifact WHERE tenant_id=?"
        params: list[Any] = [tenant_id]
        if business_id is not None:
            query += " AND business_id=?"
            params.append(business_id)
        query += " ORDER BY created_at, artifact_id"
        with self.read() as conn:
            rows = conn.execute(query, params).fetchall()
        return [models.Artifact.from_row(row) for row in rows]

    # -- audit ---------------------------------------------------------
    def _audit(
        self,
        conn: sqlite3.Connection,
        tenant_id: str,
        business_id: str | None,
        actor: str,
        action: str,
        resource_type: str,
        resource_id: str | None,
        outcome: str,
        detail: Mapping[str, Any] | None,
    ) -> str:
        if outcome not in AUDIT_OUTCOMES:
            raise BusinessError(BAD_INPUT_CODE, f"unknown audit outcome {outcome!r}")
        event_id = new_id("evt")
        conn.execute(
            "INSERT INTO bos_audit_event(event_id, tenant_id, business_id, actor,"
            " action, resource_type, resource_id, outcome, detail_json,"
            " created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                event_id,
                tenant_id,
                business_id,
                str(actor or "system"),
                str(action),
                str(resource_type),
                resource_id,
                outcome,
                json_dumps(redact(dict(detail or {}))),
                now_iso(),
            ),
        )
        return event_id

    def audit(
        self,
        tenant_id: str,
        *,
        actor: str,
        action: str,
        resource_type: str,
        resource_id: str | None = None,
        business_id: str | None = None,
        outcome: str = "ok",
        detail: Mapping[str, Any] | None = None,
    ) -> models.AuditEvent:
        with self.transaction() as conn:
            self._require_tenant(conn, tenant_id)
            if business_id is not None:
                self._require_business(conn, tenant_id, business_id)
            event_id = self._audit(
                conn, tenant_id, business_id, actor, action, resource_type,
                resource_id, outcome, detail,
            )
        return self.get_audit_event(tenant_id, event_id)

    def get_audit_event(self, tenant_id: str, event_id: str) -> models.AuditEvent:
        with self.read() as conn:
            row = conn.execute(
                "SELECT * FROM bos_audit_event WHERE tenant_id=? AND event_id=?",
                (tenant_id, event_id),
            ).fetchone()
        if row is None:
            raise BusinessError(
                NOT_FOUND_CODE,
                f"no audit event {event_id!r} in tenant {tenant_id!r}",
            )
        return models.AuditEvent.from_row(row)

    def list_audit(
        self,
        tenant_id: str,
        *,
        business_id: str | None = None,
        action: str | None = None,
        limit: int = 200,
    ) -> list[models.AuditEvent]:
        query = "SELECT * FROM bos_audit_event WHERE tenant_id=?"
        params: list[Any] = [tenant_id]
        if business_id is not None:
            query += " AND business_id=?"
            params.append(business_id)
        if action is not None:
            query += " AND action=?"
            params.append(action)
        query += " ORDER BY created_at, event_id LIMIT ?"
        params.append(max(1, int(limit)))
        with self.read() as conn:
            rows = conn.execute(query, params).fetchall()
        return [models.AuditEvent.from_row(row) for row in rows]

    # -- budgets -------------------------------------------------------
    def set_budget(
        self,
        tenant_id: str,
        business_id: str,
        *,
        currency: str,
        period: str,
        limit_minor: int,
    ) -> dict[str, Any]:
        code = _currency(currency)
        period_clean = _text(period, "budget period", max_len=20)
        if (
            not isinstance(limit_minor, int)
            or isinstance(limit_minor, bool)
            or limit_minor < 0
        ):
            raise BusinessError(
                BAD_INPUT_CODE, "budget limit_minor must be a non-negative integer"
            )
        with self.transaction() as conn:
            self._require_business(conn, tenant_id, business_id)
            conn.execute(
                "INSERT INTO bos_budget(budget_id, tenant_id, business_id, currency,"
                " period, limit_minor, created_at) VALUES(?,?,?,?,?,?,?)"
                " ON CONFLICT(tenant_id, business_id, currency, period)"
                " DO UPDATE SET limit_minor=excluded.limit_minor",
                (
                    new_id("bud"),
                    tenant_id,
                    business_id,
                    code,
                    period_clean,
                    limit_minor,
                    now_iso(),
                ),
            )
            self._audit(
                conn, tenant_id, business_id, "system", "budget.set", "budget",
                f"{business_id}:{code}:{period_clean}", "ok",
                {"limit_minor": limit_minor},
            )
        return self.budget_status(
            tenant_id, business_id, currency=code, period=period_clean
        )

    def budget_status(
        self,
        tenant_id: str,
        business_id: str,
        *,
        currency: str = "USD",
        period: str,
    ) -> dict[str, Any]:
        code = _currency(currency)
        period_clean = _text(period, "budget period", max_len=20)
        with self.read() as conn:
            self._require_business(conn, tenant_id, business_id)
            row = conn.execute(
                "SELECT * FROM bos_budget WHERE tenant_id=? AND business_id=?"
                " AND currency=? AND period=?",
                (tenant_id, business_id, code, period_clean),
            ).fetchone()
            spent = conn.execute(
                "SELECT COALESCE(SUM(cost_minor), 0) FROM bos_tool_invocation"
                " WHERE tenant_id=? AND business_id=? AND currency=?"
                " AND dry_run=0 AND status IN ('success','pending')"
                " AND created_at LIKE ?",
                (tenant_id, business_id, code, f"{period_clean}%"),
            ).fetchone()[0]
        spent_minor = int(spent or 0)
        limit = int(row["limit_minor"]) if row is not None else None
        remaining = None if limit is None else limit - spent_minor
        return {
            "tenant_id": tenant_id,
            "business_id": business_id,
            "currency": code,
            "period": period_clean,
            "configured": row is not None,
            "limit_minor": limit,
            "spent_minor": spent_minor,
            "remaining_minor": remaining,
            "exhausted": remaining is not None and remaining <= 0,
        }

    def _budget_status_conn(
        self,
        conn: sqlite3.Connection,
        tenant_id: str,
        business_id: str,
        *,
        currency: str,
        period: str,
    ) -> dict[str, Any]:
        row = conn.execute(
            "SELECT * FROM bos_budget WHERE tenant_id=? AND business_id=?"
            " AND currency=? AND period=?",
            (tenant_id, business_id, currency, period),
        ).fetchone()
        spent = conn.execute(
            "SELECT COALESCE(SUM(cost_minor), 0) FROM bos_tool_invocation"
            " WHERE tenant_id=? AND business_id=? AND currency=?"
            " AND dry_run=0 AND status IN ('success','pending')"
            " AND created_at LIKE ?",
            (tenant_id, business_id, currency, f"{period}%"),
        ).fetchone()[0]
        limit = int(row["limit_minor"]) if row is not None else None
        spent_minor = int(spent or 0)
        remaining = None if limit is None else limit - spent_minor
        return {
            "tenant_id": tenant_id,
            "business_id": business_id,
            "currency": currency,
            "period": period,
            "configured": row is not None,
            "limit_minor": limit,
            "spent_minor": spent_minor,
            "remaining_minor": remaining,
            "exhausted": remaining is not None and remaining <= 0,
        }

    # -- small SQL helpers used by other modules -----------------------
    def _fingerprint(self, value: Any) -> str:
        return fingerprint(value)

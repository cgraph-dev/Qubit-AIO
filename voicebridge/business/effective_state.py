"""Effective-state projection for one Business OS agent (P2-07).

This module composes the *real* authorities into one read-only answer:

* the agent control record (``bos_agent``): profile, permissions, safety,
  model route and fallbacks;
* identity (``bos_principal``/``bos_role``/``bos_grant``): explicit grants and
  the capabilities a role contributes (inherited);
* runs (``bos_lease``): active and expired resource leases;
* usage/budget (``bos_agent_usage``, ``bos_budget``, ``bos_tool_invocation``);
* approvals (``bos_approval``);
* the append-only audit log (``bos_audit_event``);
* tasks/workspaces (``bos_task``/``bos_workspace``).

Rules
-----
* **No invention.** A value the store does not carry is ``None`` with a typed
  ``reason``; the label is ``unknown``. Counts and dates come from rows.
* **Deny by default.** A capability is *effective* only when an active identity
  allow exists AND (when the capability names a profile category) the agent
  profile still selects it. An active deny wins over an allow.
* **Labels.** ``explicit`` = the grant row itself; ``inherited`` = a capability
  contributed by the grant's role; ``denied`` = a candidate that is revoked,
  expired, denied, or not selected by the profile.
* **Read-only.** Every call opens the store for reads only; it never writes an
  audit row, a lease, an approval or a usage record.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from . import agents as AGENTS
from . import approvals as APPROVALS
from . import identity as IDENTITY
from . import runs as RUNS
from .errors import AGENT_NOT_FOUND_CODE, BusinessError
from .store import BusinessStore
from .util import now_iso, redact

SCHEMA = "qubit-office-business-agent-effective/1"

_LABELS = ("explicit", "inherited", "denied", "unknown")


def _integer(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat(
        timespec="seconds").replace("+00:00", "Z")


def _unknown(source: str, reason: str) -> dict[str, Any]:
    return {
        "value": None,
        "present": False,
        "label": "unknown",
        "source": source,
        "reason": reason,
    }


def _known(value: Any, source: str, label: str = "explicit",
           **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "value": redact(value),
        "present": value is not None,
        "label": label,
        "source": source,
        "reason": None,
    }
    out.update(extra)
    return out


# ---------------------------------------------------------------------------
# Identity subjects
# ---------------------------------------------------------------------------
def _agent_subjects(
    store: BusinessStore, tenant_id: str, agent: AGENTS.Agent
) -> tuple[list[dict[str, Any]], str | None]:
    """Resolve the principals that represent this agent, explicitly.

    The agent table has no principal column. A link therefore exists only when
    the operator recorded one: ``metadata.security_principal_id`` /
    ``metadata.principal_id`` names a principal, or a principal's
    ``external_ref`` equals the agent id. Nothing is inferred from a name.
    """
    principals = IDENTITY.list_principals(store, tenant_id)
    by_id = {row.principal_id: row for row in principals}
    links: list[dict[str, Any]] = []
    seen: set[str] = set()

    metadata = agent.metadata or {}
    for key in (
        "security_principal_id",
        "principal_id",
        "agent_principal_id",
    ):
        raw = metadata.get(key)
        if isinstance(raw, str) and raw.strip() in by_id:
            row = by_id[raw.strip()]
            if row.principal_id not in seen:
                seen.add(row.principal_id)
                links.append({
                    "principal_id": row.principal_id,
                    "kind": row.kind,
                    "status": row.status,
                    "external_ref": row.external_ref,
                    "source": f"bos_agent.metadata.{key}",
                    "link": "metadata",
                })

    for row in principals:
        if row.external_ref == agent.agent_id and row.principal_id not in seen:
            seen.add(row.principal_id)
            links.append({
                "principal_id": row.principal_id,
                "kind": row.kind,
                "status": row.status,
                "external_ref": row.external_ref,
                "source": "bos_principal.external_ref",
                "link": "external_ref",
            })

    if not links:
        return [], (
            "the Business OS agent record has no security-principal column and "
            "no principal names this agent (metadata security_principal_id/"
            "principal_id, or bos_principal.external_ref = agent_id)"
        )
    return links, None


def _grant_candidates(
    store: BusinessStore,
    tenant_id: str,
    agent: AGENTS.Agent,
    subjects: list[dict[str, Any]],
    now_ts: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (explicit, inherited, revoked) grant candidates.

    A candidate carries the identity decision before the profile selector is
    applied, so the UI can explain *why* something is or is not effective.
    """
    subject_ids = [row["principal_id"] for row in subjects]
    subject_status = {row["principal_id"]: row["status"] for row in subjects}

    grants: dict[str, IDENTITY.Grant] = {}
    for subject_id in subject_ids:
        for row in IDENTITY.list_grants(store, tenant_id, subject_id=subject_id):
            grants[row.grant_id] = row
    # A grant may name the agent id directly even when no principal row exists.
    # Keep it visible (denied with principal_not_found), never hide it.
    direct_id = agent.agent_id
    if direct_id not in subject_ids:
        for row in IDENTITY.list_grants(store, tenant_id, subject_id=direct_id):
            grants[row.grant_id] = row
    missing_principal = direct_id not in subject_ids

    explicit: list[dict[str, Any]] = []
    inherited: list[dict[str, Any]] = []
    revoked: list[dict[str, Any]] = []

    def base(row: IDENTITY.Grant, *, capability: str, resource: str,
             action: str, data_class: str, environment: str, effect: str,
             origin: str, role_id: str | None = None) -> dict[str, Any]:
        status = subject_status.get(row.subject_id)
        if missing_principal and row.subject_id == direct_id:
            status = "missing"
        reasons: list[str] = []
        allowed = True
        if row.revoked_at is not None:
            allowed = False
            reasons.append("grant_revoked")
        if row.expires_at is not None and float(row.expires_at) <= now_ts:
            allowed = False
            reasons.append("grant_expired")
        if status != "active":
            allowed = False
            reasons.append(
                "principal_missing" if status == "missing"
                else f"principal_{status}"
            )
        if effect == "deny":
            allowed = False
            reasons.append("deny_grant")
        label = "denied" if not allowed else origin
        entry = {
            "grant_id": row.grant_id,
            "subject_id": row.subject_id,
            "role_id": role_id if role_id is not None else row.role_id,
            "capability": capability,
            "resource": resource,
            "action": action,
            "data_class": data_class,
            "environment": environment,
            "effect": effect,
            "origin": origin,
            "label": label,
            "identity_decision": "allowed" if allowed else "denied",
            "identity_reasons": reasons,
            "approval_required": bool(row.approval_required),
            "expires_at": row.expires_at,
            "expires_at_iso": _iso(row.expires_at),
            "revoked_at": row.revoked_at,
            "created_at": row.created_at,
            "source": "bos_grant",
        }
        return entry

    for row in grants.values():
        entry = base(
            row,
            capability=row.capability,
            resource=row.resource,
            action=row.action,
            data_class=row.data_class,
            environment=row.environment,
            effect=row.effect,
            origin="explicit",
        )
        explicit.append(entry)
        if row.revoked_at is not None:
            revoked.append({
                "capability": row.capability,
                "resource": row.resource,
                "action": row.action,
                "grant_id": row.grant_id,
                "origin": "explicit",
                "status": "revoked",
                "revoked_at": row.revoked_at,
                "source": "bos_grant",
            })

        if row.role_id is None:
            continue
        try:
            role = IDENTITY.get_role(store, tenant_id, row.role_id)
        except BusinessError:
            inherited.append({
                "grant_id": row.grant_id,
                "role_id": row.role_id,
                "capability": row.capability,
                "resource": row.resource,
                "action": row.action,
                "data_class": row.data_class,
                "environment": row.environment,
                "effect": row.effect,
                "origin": "inherited",
                "label": "denied",
                "identity_decision": "denied",
                "identity_reasons": ["role_missing"],
                "source": "bos_grant",
            })
            continue
        for candidate in role.permissions:
            if not isinstance(candidate, Mapping):
                continue
            inherited.append(base(
                row,
                capability=str(candidate.get("capability") or ""),
                resource=str(candidate.get("resource") or row.resource),
                action=str(candidate.get("action") or row.action),
                data_class=str(
                    candidate.get("data_class") or row.data_class),
                environment=str(
                    candidate.get("environment") or row.environment),
                effect=str(candidate.get("effect") or "allow"),
                origin="inherited",
                role_id=row.role_id,
            ))

    return explicit, inherited, revoked


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------
def _effective_label(origins: list[str]) -> str:
    """Collapse a mixed candidate to one of the contract labels."""
    if "explicit" in origins:
        return "explicit"
    if "inherited" in origins:
        return "inherited"
    return "denied"


def _profile_scopes(agent: AGENTS.Agent) -> list[dict[str, Any]]:
    permissions = agent.permissions.to_dict()
    rows: list[dict[str, Any]] = []
    for category in AGENTS.PERMISSION_CATEGORIES:
        for scope in permissions.get(category, []):
            rows.append({
                "capability": category,
                "scope": scope,
                "source": "bos_agent.permissions",
                "label": "explicit",
            })
    return rows


def _aggregate_capabilities(
    explicit: list[dict[str, Any]],
    inherited: list[dict[str, Any]],
    agent: AGENTS.Agent,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Group candidates by identity scope and apply deny-wins + profile filter."""
    candidates = [row for row in (*explicit, *inherited)
                  if row.get("capability")]
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for row in candidates:
        key = (row["capability"], row["resource"], row["action"],
               row["data_class"], row["environment"])
        groups.setdefault(key, []).append(row)

    permissions = agent.permissions.to_dict()
    effective: list[dict[str, Any]] = []
    denied: list[dict[str, Any]] = []

    for key, rows in sorted(groups.items(), key=lambda item: item[0]):
        capability, resource, action, data_class, environment = key
        identity_allowed = any(
            row["identity_decision"] == "allowed" for row in rows)
        deny_rows = [
            row for row in rows
            if row["effect"] == "deny"
            and row["identity_decision"] == "denied"
            and "deny_grant" in row["identity_reasons"]
        ]
        origins = sorted({row["origin"] for row in rows if row["origin"]})
        reasons: list[str] = []
        profile_selected: bool | None = None

        if deny_rows:
            decision = "denied"
            reasons.append("deny_grant")
        elif not identity_allowed:
            decision = "denied"
            for row in rows:
                reasons.extend(row["identity_reasons"])
            if not reasons:
                reasons.append("no_matching_grant")
        else:
            decision = "allowed"

        if capability in AGENTS.PERMISSION_CATEGORIES:
            selected = [scope for scope in permissions.get(capability, [])
                        if scope == action or scope == resource]
            profile_selected = bool(selected)
            if decision == "allowed" and not profile_selected:
                decision = "denied"
                reasons.append("profile_selector_absent")
        else:
            reasons.append("capability_not_a_profile_category")

        unique_reasons = sorted(dict.fromkeys(reasons))
        row = {
            "capability": capability,
            "resource": resource,
            "action": action,
            "data_class": data_class,
            "environment": environment,
            "origin": origins[0] if len(origins) == 1 else "mixed",
            "origins": origins,
            "label": (
                _effective_label(origins) if decision == "allowed" else "denied"
            ),
            "effective": decision == "allowed",
            "decision": decision,
            "identity_decision": (
                "allowed" if identity_allowed else "denied"),
            "profile_selected": profile_selected,
            "reasons": unique_reasons,
            "grant_ids": sorted({row["grant_id"] for row in rows}),
            "sources": sorted({row["source"] for row in rows}),
        }
        (effective if decision == "allowed" else denied).append(row)

    return effective, denied


def _profile_capabilities(
    agent: AGENTS.Agent,
    explicit: list[dict[str, Any]],
    inherited: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Profile selectors are visible even when identity grants none of them.

    A selector is not an authorization: when no identity candidate matches the
    exact ``capability``+``action`` (or ``resource``) pair, it is ``denied``
    with ``no_matching_identity_grant``.
    """
    candidates = [row for row in (*explicit, *inherited)
                  if row.get("capability")]
    applied: list[dict[str, Any]] = []
    denied: list[dict[str, Any]] = []
    for selector in _profile_scopes(agent):
        capability = selector["capability"]
        scope = selector["scope"]
        matches = [
            row for row in candidates
            if row["capability"] == capability
            and (row["action"] == scope or row["resource"] == scope)
        ]
        allowed = [row for row in matches
                   if row["identity_decision"] == "allowed"
                   and row["effect"] == "allow"]
        deny = [row for row in matches if row["effect"] == "deny"
                and row["identity_decision"] == "denied"]
        if deny:
            decision = "denied"
            reasons = ["deny_grant"]
            origins: list[str] = sorted({row["origin"] for row in deny})
        elif allowed:
            decision = "allowed"
            reasons = []
            origins = sorted({row["origin"] for row in allowed})
        else:
            decision = "denied"
            reasons = ["no_matching_identity_grant"]
            origins = []
        row = {
            **selector,
            "origin": origins[0] if len(origins) == 1 else (
                "mixed" if origins else "explicit"),
            "origins": origins,
            "label": (
                _effective_label(origins) if decision == "allowed" else "denied"
            ),
            "effective": decision == "allowed",
            "decision": decision,
            "reasons": reasons,
            "grant_ids": sorted({match["grant_id"] for match in matches}),
        }
        (applied if decision == "allowed" else denied).append(row)
    return applied, denied


# ---------------------------------------------------------------------------
# Supporting stores
# ---------------------------------------------------------------------------
def _turns(store: BusinessStore, tenant_id: str, agent_id: str
           ) -> dict[str, Any]:
    with store.read() as conn:
        active = _integer(conn.execute(
            "SELECT COUNT(*) FROM bos_agent_turn WHERE tenant_id=?"
            " AND agent_id=? AND status='active'",
            (tenant_id, agent_id),
        ).fetchone()[0])
        last = conn.execute(
            "SELECT * FROM bos_agent_turn WHERE tenant_id=? AND agent_id=?"
            " ORDER BY started_at DESC, turn_id DESC LIMIT 1",
            (tenant_id, agent_id),
        ).fetchone()
    return {
        "source": "bos_agent_turn",
        "active_count": active,
        "last": dict(last) if last is not None else None,
    }


def _budget(
    store: BusinessStore,
    tenant_id: str,
    business: Any,
    agent: AGENTS.Agent,
    now_ts: float,
) -> dict[str, Any]:
    moment = datetime.fromtimestamp(now_ts, tz=timezone.utc)
    day = moment.strftime("%Y-%m-%d")
    month = moment.strftime("%Y-%m")
    with store.read() as conn:
        usage = conn.execute(
            "SELECT COALESCE(SUM(cost_minor),0), COALESCE(SUM(tokens),0),"
            " COALESCE(SUM(CASE WHEN kind='turn' THEN 1 ELSE 0 END),0)"
            " FROM bos_agent_usage WHERE tenant_id=? AND agent_id=?"
            " AND day=?",
            (tenant_id, agent.agent_id, day),
        ).fetchone()
        month_usage = conn.execute(
            "SELECT COALESCE(SUM(cost_minor),0) FROM bos_agent_usage"
            " WHERE tenant_id=? AND agent_id=? AND month=?",
            (tenant_id, agent.agent_id, month),
        ).fetchone()
        reserved = conn.execute(
            "SELECT COALESCE(SUM(cost_minor),0), COUNT(*) FROM"
            " bos_tool_invocation WHERE tenant_id=? AND business_id=?"
            " AND currency=? AND dry_run=0 AND status='pending'",
            (tenant_id, business.business_id, business.currency),
        ).fetchone()

    business_budget = store.budget_status(
        tenant_id, business.business_id,
        currency=business.currency, period=month,
    )
    safety = agent.safety
    return {
        "source": "bos_agent.safety + bos_agent_usage + bos_budget",
        "currency": business.currency,
        "day": day,
        "month": month,
        "limits": {
            "daily_spend_minor": safety.daily_spend_minor,
            "monthly_spend_minor": safety.monthly_spend_minor,
            "per_action_limit_minor": safety.per_action_limit_minor,
            "max_turns_per_day": safety.max_turns_per_day,
            "max_tokens_per_turn": safety.max_tokens_per_turn,
            "rate_limit_per_minute": safety.rate_limit_per_minute,
            "autonomy": safety.autonomy,
            "approval_required": list(safety.approval_required),
        },
        "spend": {
            "today_cost_minor": _integer(usage[0]),
            "today_tokens": _integer(usage[1]),
            "today_turns": _integer(usage[2]),
            "month_cost_minor": _integer(month_usage[0]),
        },
        "reservations": {
            "scope": "business",
            "pending_cost_minor": _integer(reserved[0]),
            "pending_count": _integer(reserved[1]),
            "source": "bos_tool_invocation",
            "reason": (
                "the Business OS tool-invocation table has no agent column; "
                "reservations are reported for the business"
            ),
        },
        "business_budget": business_budget,
    }


def _leases(
    store: BusinessStore,
    tenant_id: str,
    ids: set[str],
    now_ts: float,
) -> dict[str, Any]:
    rows = RUNS.list_leases(store, tenant_id, active_only=False)
    active: list[dict[str, Any]] = []
    expired: list[dict[str, Any]] = []
    released: list[dict[str, Any]] = []
    for lease in rows:
        if lease.holder not in ids and lease.resource_id not in ids:
            continue
        item = {
            "lease_id": lease.lease_id,
            "resource_type": lease.resource_type,
            "resource_id": lease.resource_id,
            "holder": lease.holder,
            "fencing_token": lease.fencing_token,
            "acquired_at": lease.acquired_at,
            "expires_at": lease.expires_at,
            "expires_at_iso": _iso(lease.expires_at),
            "heartbeat_at": lease.heartbeat_at,
            "released_at": lease.released_at,
            "source": "bos_lease",
        }
        if lease.released_at is not None:
            item["status"] = "released"
            released.append(item)
        elif float(lease.expires_at) <= now_ts:
            item["status"] = "expired"
            item["reason"] = "lease_expired"
            expired.append(item)
        else:
            item["status"] = "active"
            active.append(item)
    reason = None
    if not rows:
        reason = "no lease rows exist in this tenant"
    elif not (active or expired or released):
        reason = "no lease names this agent or one of its principals"
    return {
        "source": "bos_lease",
        "active": active,
        "expired": expired,
        "released": released,
        "reason": reason,
    }


def _approval_links(row: Any, ids: set[str]) -> bool:
    payload = row.payload
    if isinstance(payload, Mapping):
        for key in ("agent_id", "subject_id", "principal_id"):
            value = payload.get(key)
            if isinstance(value, str) and value in ids:
                return True
        params = payload.get("parameters")
        if isinstance(params, Mapping):
            for key in ("agent_id", "subject_id", "principal_id"):
                value = params.get(key)
                if isinstance(value, str) and value in ids:
                    return True
    return row.requested_by in ids


def _approvals(
    store: BusinessStore,
    tenant_id: str,
    business_id: str,
    ids: set[str],
    now_ts: float,
) -> dict[str, Any]:
    rows = APPROVALS.list_approvals(
        store, tenant_id, business_id=business_id)
    pending: list[dict[str, Any]] = []
    decided: list[dict[str, Any]] = []
    expired: list[dict[str, Any]] = []
    unlinked_pending = 0
    for row in rows:
        effective_status = row.status
        if (
            row.status == "pending"
            and row.expires_at is not None
            and float(row.expires_at) <= now_ts
        ):
            effective_status = "expired"
        item = {
            "approval_id": row.approval_id,
            "action": row.action,
            "status": row.status,
            "effective_status": effective_status,
            "requested_by": row.requested_by,
            "decided_by": row.decided_by,
            "reason": row.reason,
            "payload": redact(row.payload),
            "policy_version": row.policy_version,
            "created_at": row.created_at,
            "decided_at": row.decided_at,
            "expires_at": row.expires_at,
            "expires_at_iso": _iso(row.expires_at),
            "linked": _approval_links(row, ids),
            "source": "bos_approval",
        }
        if not item["linked"]:
            if effective_status == "pending":
                unlinked_pending += 1
            continue
        if effective_status == "pending":
            pending.append(item)
        elif effective_status == "expired":
            expired.append(item)
        else:
            decided.append(item)
    reason = None
    if not rows:
        reason = "no approval rows exist for this business"
    elif not (pending or decided or expired):
        reason = (
            "the approval journal has no agent column; none of the "
            f"{len(rows)} business approval(s) names this agent"
        )
    return {
        "source": "bos_approval",
        "pending": pending,
        "decided": decided,
        "expired": expired,
        "unlinked_pending": unlinked_pending,
        "reason": reason,
    }


def _audit(
    store: BusinessStore,
    tenant_id: str,
    agent_id: str,
    grants: Iterable[IDENTITY.Grant],
    limit: int = 25,
) -> dict[str, Any]:
    events: dict[str, Any] = {}
    for row in store.list_audit_for_resource(
        tenant_id, "agent", agent_id, limit=limit
    ):
        events[row.event_id] = {**row_detail(row), "source": "bos_audit_event"}
    for grant in grants:
        for row in store.list_audit_for_resource(
            tenant_id, "grant", grant.grant_id, limit=limit
        ):
            events[row.event_id] = {**row_detail(row), "source": "bos_audit_event"}
    ordered = sorted(
        events.values(),
        key=lambda item: (item["created_at"], item["event_id"]),
        reverse=True,
    )[:limit]
    return {
        "source": "bos_audit_event",
        "events": ordered,
        "reason": None if ordered else "no audit rows name this agent or its grants",
    }


def row_detail(row: Any) -> dict[str, Any]:
    """One audit row as plain data (never a dataclass leak)."""
    return {
        "event_id": row.event_id,
        "actor": row.actor,
        "action": row.action,
        "resource_type": row.resource_type,
        "resource_id": row.resource_id,
        "outcome": row.outcome,
        "detail": redact(row.detail),
        "created_at": row.created_at,
    }


def _revoked_profile_categories(store: BusinessStore, tenant_id: str,
                                agent_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for event in store.list_audit_for_resource(
        tenant_id, "agent", agent_id, limit=500
    ):
        if event.action != "agent.revoke":
            continue
        detail = event.detail if isinstance(event.detail, Mapping) else {}
        for scope in detail.get("scopes") or []:
            rows.append({
                "capability": str(scope),
                "status": "revoked",
                "source": "bos_audit_event",
                "event_id": event.event_id,
                "revoked_at": event.created_at,
            })
    return rows


def _links(
    store: BusinessStore,
    tenant_id: str,
    business_id: str,
    ids: set[str],
) -> dict[str, Any]:
    tasks: list[dict[str, Any]] = []
    task_ids: set[str] = set()
    for row in store.list_tasks(tenant_id, business_id=business_id):
        if row.assigned_to in ids:
            task_ids.add(row.task_id)
            tasks.append({
                "task_id": row.task_id,
                "title": row.title,
                "status": row.status,
                "assigned_to": row.assigned_to,
                "source": "bos_task(legacy)",
            })
    for row in RUNS.list_tasks(store, tenant_id, business_id=business_id):
        if row.owner_principal in ids or row.assignee_principal in ids:
            task_ids.add(row.task_id)
            tasks.append({
                "task_id": row.task_id,
                "title": row.title,
                "status": row.status,
                "owner_principal": row.owner_principal,
                "assignee_principal": row.assignee_principal,
                "source": "bos_task(execution)",
            })

    workspaces: list[dict[str, Any]] = []
    for row in RUNS.list_workspaces(store, tenant_id):
        if row.task_id in task_ids or row.session_id in ids:
            workspaces.append({
                "workspace_id": row.workspace_id,
                "task_id": row.task_id,
                "session_id": row.session_id,
                "storage_class": row.storage_class,
                "current_revision": row.current_revision,
                "source": "bos_workspace",
            })

    return {
        "source": "bos_task + bos_workspace",
        "tasks": tasks,
        "workspaces": workspaces,
        "sessions": _unknown(
            "bos_session",
            "the Business OS store has no durable agent-session table; "
            "sessions are owned by the runtime session store (P2-02)",
        ),
    }


def _memory(
    agent: AGENTS.Agent,
    explicit: list[dict[str, Any]],
    inherited: list[dict[str, Any]],
) -> dict[str, Any]:
    metadata = agent.metadata or {}
    scopes: list[dict[str, Any]] = []
    for key in ("memory_scopes", "memory_scope", "memory"):
        raw = metadata.get(key)
        if raw is None:
            continue
        values = raw if isinstance(raw, list) else [raw]
        for value in values:
            if isinstance(value, str) and value.strip():
                scopes.append({
                    "scope": value.strip(),
                    "source": f"bos_agent.metadata.{key}",
                    "label": "explicit",
                })
            elif isinstance(value, Mapping):
                name = value.get("scope") or value.get("name") or value.get("id")
                if isinstance(name, str) and name.strip():
                    raw_access = value.get("access") or []
                    if isinstance(raw_access, str):
                        raw_access = [raw_access]
                    scopes.append({
                        "scope": name.strip(),
                        "access": sorted(
                            str(item).lower() for item in raw_access
                        ) or None,
                        "source": f"bos_agent.metadata.{key}",
                        "label": "explicit",
                    })
    for row in (*explicit, *inherited):
        if row.get("capability") not in ("memory", "memory.scope", "memory_scope"):
            continue
        scopes.append({
            "scope": row.get("resource") or row.get("action"),
            "access": [row.get("action")] if row.get("action") else None,
            "source": "bos_grant",
            "label": row["label"],
            "grant_id": row["grant_id"],
        })
    if not scopes:
        return {
            "source": "bos_agent.metadata + bos_grant",
            "scopes": [],
            "value": None,
            "present": False,
            "label": "unknown",
            "reason": (
                "the Business OS agent record carries no memory-scope field and "
                "no grant names a memory capability"
            ),
        }
    return {
        "source": "bos_agent.metadata + bos_grant",
        "scopes": scopes,
        "value": scopes,
        "present": True,
        "label": "explicit",
        "reason": None,
    }


def _policy(store: BusinessStore, tenant_id: str) -> dict[str, Any]:
    with store.read() as conn:
        row = conn.execute(
            "SELECT * FROM bos_policy_revision WHERE tenant_id=?"
            " ORDER BY effective_at DESC, revision_id DESC LIMIT 1",
            (tenant_id,),
        ).fetchone()
    if row is None:
        return _unknown(
            "bos_policy_revision",
            "no policy revision row exists for this tenant; the agent record "
            "does not pin a policy version",
        )
    return _known(
        {
            "revision_id": row["revision_id"],
            "version": row["version"],
            "effective_at": row["effective_at"],
            "doc": redact(_loads(row["doc_json"])),
        },
        "bos_policy_revision",
    )


def _loads(text: Any) -> Any:
    import json

    if not text:
        return {}
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return {}


def _status(agent: AGENTS.Agent, turns: dict[str, Any]) -> dict[str, Any]:
    metadata = agent.metadata or {}
    source = str(metadata.get("source") or "").lower()
    record = agent.status
    if record == "archived":
        value, reason = "archived", None
    elif record == "revoked":
        value, reason = "revoked", None
    elif record == "paused":
        value, reason = "paused", None
    elif turns["active_count"] > 0:
        value, reason = "live", None
    elif source in ("office_plan_building", "seed", "projection", "seed_projection"):
        value, reason = "projection", "the agent record comes from a seed/projection import"
    elif record == "active":
        value, reason = "idle", None
    else:
        value, reason = "unknown", f"unrecognised agent status {record!r}"
    last = turns.get("last") or {}
    return {
        "value": value,
        "label": value,
        "record_status": record,
        "source": "bos_agent.status + bos_agent.metadata.source + bos_agent_turn",
        "reason": reason,
        "turn_status": last.get("status"),
        "active_turns": turns["active_count"],
        "connectivity": _unknown(
            "bos_agent",
            "the Business OS store records no connectivity/heartbeat for an "
            "agent; disconnected cannot be derived here",
        ),
    }


# ---------------------------------------------------------------------------
# Projection
# ---------------------------------------------------------------------------
def effective_agent_state(
    store: BusinessStore,
    tenant_id: str,
    agent_id: str,
    *,
    now: float | None = None,
) -> dict[str, Any]:
    """Project one agent's effective state from the real stores.

    Raises :class:`BusinessError` (``AGENT_NOT_FOUND``) when the agent does not
    exist in *this* tenant. No cross-tenant row is ever read.
    """
    now_ts = time.time() if now is None else float(now)
    tenant = store.require_tenant(tenant_id)
    service = AGENTS.AgentControlService(store)
    try:
        agent = service.get(tenant_id, agent_id)
    except BusinessError as exc:
        if exc.code == AGENT_NOT_FOUND_CODE:
            raise
        raise
    business = store.require_business(tenant_id, agent.business_id)

    subjects, subject_reason = _agent_subjects(store, tenant_id, agent)
    explicit, inherited, revoked_grants = _grant_candidates(
        store, tenant_id, agent, subjects, now_ts)
    effective, denied = _aggregate_capabilities(explicit, inherited, agent)
    applied_selectors, denied_selectors = _profile_capabilities(
        agent, explicit, inherited)
    turns = _turns(store, tenant_id, agent_id)

    grant_models = {
        row.grant_id: row
        for row in IDENTITY.list_grants(store, tenant_id)
        if row.grant_id in {item["grant_id"] for item in (*explicit, *inherited)}
    }

    ids = {agent.agent_id, *[row["principal_id"] for row in subjects]}
    primary = _known(
        {
            "provider": agent.provider,
            "model": agent.model,
            "reasoning_effort": agent.reasoning_effort,
            "route": f"{agent.provider}/{agent.model}",
        },
        "bos_agent",
    )
    if agent.fallback_provider and agent.fallback_model:
        fallback = _known(
            {
                "provider": agent.fallback_provider,
                "model": agent.fallback_model,
                "route": f"{agent.fallback_provider}/{agent.fallback_model}",
            },
            "bos_agent",
        )
    else:
        fallback = _unknown(
            "bos_agent",
            "no fallback provider/model is pinned on the agent record",
        )

    revoked_capabilities = [
        *revoked_grants,
        *_revoked_profile_categories(store, tenant_id, agent_id),
    ]

    projection = {
        "schema": SCHEMA,
        "tenant_id": tenant.tenant_id,
        "agent_id": agent.agent_id,
        "business_id": agent.business_id,
        "generated_at": now_iso(now_ts),
        "agent": {
            "source": "bos_agent",
            "label": "explicit",
            "profile": _known(
                {
                    "role_id": agent.role_id,
                    "department": agent.department,
                    "display_name": agent.display_name,
                    "human_owner": agent.human_owner,
                    "room": agent.room,
                    "desk": agent.desk,
                    "revision": agent.revision,
                    "permissions": agent.permissions.to_dict(),
                    "safety": agent.safety.to_dict(),
                    "metadata": redact(agent.metadata),
                    "created_at": agent.created_at,
                    "updated_at": agent.updated_at,
                    "archived_at": agent.archived_at,
                },
                "bos_agent",
            ),
        },
        "status": _status(agent, turns),
        "identity": {
            "source": "bos_principal + bos_role + bos_grant",
            "subjects": subjects,
            "subject_reason": subject_reason,
            "explicit": explicit,
            "inherited": inherited,
            "revoked": revoked_grants,
            "expired": [
                row for row in (*explicit, *inherited)
                if "grant_expired" in row.get("identity_reasons", [])
            ],
        },
        "capabilities": {
            "source": "bos_grant + bos_role + bos_agent.permissions",
            "effective": effective,
            "denied": denied,
            "profile_selectors": _profile_scopes(agent),
            "profile_applied": applied_selectors,
            "profile_denied": denied_selectors,
            "revoked": revoked_capabilities,
            "reason": (
                None if (effective or denied or denied_selectors)
                else "no grants and no profile permission scopes exist for this agent"
            ),
        },
        "model": {
            "source": "bos_agent",
            "primary": primary,
            "fallback": fallback,
            "pinning": _unknown(
                "bos_agent",
                "the Business OS agent record pins provider/model/fallback but "
                "records no per-run capability-snapshot or downgrade policy",
            ),
        },
        "memory": _memory(agent, explicit, inherited),
        "leases": _leases(store, tenant_id, ids, now_ts),
        "budget": _budget(store, tenant_id, business, agent, now_ts),
        "approvals": _approvals(store, tenant_id, agent.business_id, ids, now_ts),
        "audit": _audit(store, tenant_id, agent_id, grant_models.values()),
        "links": _links(store, tenant_id, agent.business_id, ids),
        "policy": _policy(store, tenant_id),
    }
    return projection


__all__ = ["SCHEMA", "effective_agent_state"]

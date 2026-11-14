"""War Room / Idea Lab — the BOS-backed idea pipeline.

A War Room session turns raw material into a reviewed business hypothesis:

    intake -> research -> debate -> synthesis -> owner_decision
                                                       |
                                          approved ----+---- rejected
                                             |
                                        tasks_created

The rules the master plan makes non-negotiable are enforced here, not by
convention:

* **Evidence is labelled.** Every claim is ``FACT``, ``INFERENCE``,
  ``ASSUMPTION`` or ``UNKNOWN``. A ``FACT`` must cite a source in this session
  whose extraction actually succeeded; otherwise the claim is refused with
  ``BUSINESS_WARROOM_UNVERIFIED_FACT``. Research is never fabricated.
* **Debate is append-only.** Arguments carry a role, a stance and evidence
  refs. They cannot be edited or deleted; dissent survives synthesis.
* **Only the human owner decides.** A non-owner actor is refused with
  ``BUSINESS_WARROOM_OWNER_ONLY``. The decision is its own immutable row.
* **Tasks exist only after approval.** ``create_tasks`` refuses every state
  except ``approved`` and never creates work for a rejected idea.
* **No side effects.** This module makes no network call and never publishes,
  spends, emails or contacts anyone. ``request_action`` is the only launch
  seam: it creates an ordinary BOS approval that must be approved before the
  tool gateway will execute anything.

Everything is tenant-scoped and idempotent, and all state lives in the same
``business.db`` as the rest of the Business OS (WAL, append-only triggers,
typed errors).
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
from typing import TYPE_CHECKING, Any, Mapping

from . import models
from .errors import (
    BAD_INPUT_CODE,
    BAD_STATE_CODE,
    IDEMPOTENCY_CONFLICT_CODE,
    NOT_FOUND_CODE,
    WARROOM_ADAPTER_FAILED_CODE,
    WARROOM_OWNER_ONLY_CODE,
    WARROOM_UNVERIFIED_FACT_CODE,
    BusinessError,
)
from .ids import fingerprint, new_id
from .util import json_dumps, json_loads, now_iso, redact
from .warroom_adapters import (
    SOURCE_KINDS,
    SourceAdapterError,
    SourceAdapterUnavailable,
    default_registry,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .store import BusinessStore
    from .warroom_adapters import SourceAdapter

WARROOM_STATES = (
    "intake",
    "research",
    "debate",
    "synthesis",
    "owner_decision",
    "approved",
    "rejected",
    "tasks_created",
)
WARROOM_TRANSITIONS: dict[str, frozenset[str]] = {
    "intake": frozenset({"research"}),
    "research": frozenset({"debate"}),
    "debate": frozenset({"synthesis"}),
    "synthesis": frozenset({"owner_decision"}),
    "owner_decision": frozenset({"approved", "rejected"}),
    "approved": frozenset({"tasks_created"}),
    "rejected": frozenset(),
    "tasks_created": frozenset(),
}
#: ``advance`` may not take these edges: a decision is the owner's, and task
#: creation is a consequence of an approval.
OWNER_ONLY_TARGETS = frozenset({"approved", "rejected", "tasks_created"})

EVIDENCE_LABELS = ("FACT", "INFERENCE", "ASSUMPTION", "UNKNOWN")
STANCES = ("support", "oppose", "neutral", "question")
DEBATE_ROLES = frozenset(
    {
        "moderator",
        "researcher",
        "skeptic",
        "marketer",
        "social_community",
        "sales_lead_gen",
        "product_engineering",
        "web_app",
        "delivery",
        "finance",
        "legal_platform_policy",
        "customer",
        "builder",
    }
)
_ROLE_ALIASES = {
    "social": "social_community",
    "community": "social_community",
    "sales": "sales_lead_gen",
    "lead_gen": "sales_lead_gen",
    "leadgen": "sales_lead_gen",
    "sales_leadgen": "sales_lead_gen",
    "product": "product_engineering",
    "engineering": "product_engineering",
    "product_eng": "product_engineering",
    "web": "web_app",
    "app": "web_app",
    "webapp": "web_app",
    "legal": "legal_platform_policy",
    "policy": "legal_platform_policy",
    "platform": "legal_platform_policy",
}
REQUIRED_BRIEF_KEYS = (
    "idea",
    "niche",
    "icp",
    "offer",
    "channels",
    "content_plan",
    "lead_gen_plan",
    "website_app_scope",
    "pricing",
    "budget",
    "risks",
    "plan_30_60_90",
    "tasks",
)
SCORE_METRICS = ("demand", "effort", "cost", "risk", "fit")


def _text(value: Any, label: str, *, max_len: int = 4096) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BusinessError(BAD_INPUT_CODE, f"{label} must be a non-empty string")
    clean = value.strip()
    if len(clean) > max_len:
        raise BusinessError(
            BAD_INPUT_CODE, f"{label} is longer than {max_len} characters"
        )
    return clean


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise BusinessError(BAD_INPUT_CODE, f"{label} must be a mapping")
    return dict(value)


def _session_row(
    conn: sqlite3.Connection, tenant_id: str, session_id: str
) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM bos_warroom_session WHERE tenant_id=? AND session_id=?",
        (tenant_id, session_id),
    ).fetchone()
    if row is None:
        raise BusinessError(
            NOT_FOUND_CODE,
            f"no war-room session {session_id!r} in tenant {tenant_id!r}",
        )
    return row


def _source_row(
    conn: sqlite3.Connection, tenant_id: str, source_id: str
) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM bos_warroom_source WHERE tenant_id=? AND source_id=?",
        (tenant_id, source_id),
    ).fetchone()
    if row is None:
        raise BusinessError(
            NOT_FOUND_CODE,
            f"no war-room source {source_id!r} in tenant {tenant_id!r}",
        )
    return row


def _label(value: Any) -> str:
    raw = _text(value, "evidence label", max_len=20).upper()
    if raw not in EVIDENCE_LABELS:
        raise BusinessError(
            BAD_INPUT_CODE,
            f"unknown evidence label {raw!r}; expected one of "
            f"{', '.join(EVIDENCE_LABELS)}",
        )
    return raw


def _role(value: Any) -> str:
    raw = _text(value, "debate role", max_len=80).lower()
    normalised = re.sub(r"[^a-z0-9]+", "_", raw).strip("_")
    normalised = _ROLE_ALIASES.get(normalised, normalised)
    if normalised not in DEBATE_ROLES:
        raise BusinessError(
            BAD_INPUT_CODE,
            f"unknown debate role {value!r}; expected one of "
            f"{', '.join(sorted(DEBATE_ROLES))}",
        )
    return normalised


def _stance(value: Any) -> str:
    raw = _text(value, "stance", max_len=20).lower()
    if raw not in STANCES:
        raise BusinessError(
            BAD_INPUT_CODE,
            f"unknown stance {raw!r}; expected one of {', '.join(STANCES)}",
        )
    return raw


def _source_kind(value: Any) -> str:
    raw = _text(value, "source kind", max_len=20).lower()
    if raw not in SOURCE_KINDS:
        raise BusinessError(
            BAD_INPUT_CODE,
            f"unknown source kind {raw!r}; expected one of "
            f"{', '.join(SOURCE_KINDS)}",
        )
    return raw


def _owner(value: Any) -> str:
    actor = _text(value, "decided_by", max_len=200)
    lowered = actor.lower()
    if lowered != "owner" and not lowered.startswith(("owner:", "owner/")):
        raise BusinessError(
            WARROOM_OWNER_ONLY_CODE,
            "only the human owner may decide a War Room session"
            f" (got {actor!r})",
        )
    return actor


def _round_no(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise BusinessError(BAD_INPUT_CODE, "round_no must be a positive integer")
    return value


def _validate_brief(value: Any) -> dict[str, Any]:
    brief = _mapping(value, "brief")
    missing = [key for key in REQUIRED_BRIEF_KEYS if key not in brief]
    if missing:
        raise BusinessError(
            BAD_INPUT_CODE,
            "brief is missing required outputs: " + ", ".join(missing),
        )
    if not isinstance(brief["channels"], list):
        raise BusinessError(BAD_INPUT_CODE, "brief.channels must be a list")
    if not isinstance(brief["risks"], list):
        raise BusinessError(BAD_INPUT_CODE, "brief.risks must be a list")
    tasks = brief["tasks"]
    if not isinstance(tasks, list) or not tasks:
        raise BusinessError(
            BAD_INPUT_CODE, "brief.tasks must be a non-empty list"
        )
    return brief


def _validate_score(value: Any) -> dict[str, Any]:
    score = _mapping(value, "score")
    missing = [key for key in SCORE_METRICS if key not in score]
    if missing:
        raise BusinessError(
            BAD_INPUT_CODE,
            "score is missing required metrics: " + ", ".join(missing),
        )
    result: dict[str, Any] = {}
    for key in SCORE_METRICS:
        metric = score[key]
        if isinstance(metric, bool) or not isinstance(metric, (int, float)):
            raise BusinessError(
                BAD_INPUT_CODE, f"score.{key} must be a number between 0 and 10"
            )
        if not 0 <= float(metric) <= 10:
            raise BusinessError(
                BAD_INPUT_CODE, f"score.{key} must be between 0 and 10"
            )
        result[key] = metric
    result["total"] = round(
        sum(float(result[key]) for key in SCORE_METRICS) / len(SCORE_METRICS), 2
    )
    for key, metric in score.items():
        if key not in SCORE_METRICS and key != "total":
            result[key] = metric
    return result


def _normalise_experiments(value: Any) -> list[dict[str, str]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise BusinessError(BAD_INPUT_CODE, "experiments must be a list")
    experiments: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in value:
        if isinstance(item, str):
            name = _text(item, "experiment name", max_len=200)
            row = {
                "name": name,
                "hypothesis": "",
                "method": "",
                "success_metric": "",
            }
        elif isinstance(item, Mapping):
            name = _text(item.get("name"), "experiment name", max_len=200)
            row = {
                "name": name,
                "hypothesis": str(item.get("hypothesis") or "")[:4000],
                "method": str(item.get("method") or "")[:4000],
                "success_metric": str(item.get("success_metric") or "")[:4000],
            }
        else:
            raise BusinessError(
                BAD_INPUT_CODE, "each experiment must be a string or a mapping"
            )
        if name in seen:
            continue
        seen.add(name)
        experiments.append(row)
    return experiments


def _metadata_update(
    raw: str | None, *extra: Mapping[str, Any] | None
) -> str:
    merged: dict[str, Any] = json_loads(raw, {})
    for item in extra:
        if item:
            merged.update(dict(item))
    return json_dumps(redact(merged))


def _insert_artifact(
    conn: sqlite3.Connection,
    store: "BusinessStore",
    tenant_id: str,
    business_id: str,
    *,
    kind: str,
    name: str,
    body: str,
    media_type: str,
) -> str:
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    artifact_id = new_id("art")
    conn.execute(
        "INSERT INTO bos_artifact(artifact_id, tenant_id, business_id, job_id,"
        " kind, name, media_type, sha256, size_bytes, body, created_at)"
        " VALUES(?,?,?,NULL,?,?,?,?,?,?,?)",
        (
            artifact_id,
            tenant_id,
            business_id,
            kind,
            name,
            media_type,
            digest,
            len(body.encode("utf-8")),
            body,
            now_iso(),
        ),
    )
    store._audit(
        conn,
        tenant_id,
        business_id,
        "system",
        "artifact.create",
        "artifact",
        artifact_id,
        "ok",
        {"kind": kind, "name": name, "sha256": digest},
    )
    return artifact_id


def _tasks_for_session(
    conn: sqlite3.Connection, tenant_id: str, session_id: str
) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM bos_task WHERE tenant_id=? AND metadata_json LIKE ?"
        " ORDER BY rowid",
        (tenant_id, f"%{session_id}%"),
    ).fetchall()


# -- sessions -------------------------------------------------------------
def create_session(
    store: "BusinessStore",
    *,
    tenant_id: str,
    title: str,
    idempotency_key: str,
    business_id: str | None = None,
) -> tuple[models.WarRoomSession, bool]:
    """Open an intake; a replayed key with the same body returns the original."""
    clean_title = _text(title, "session title", max_len=300)
    key = _text(idempotency_key, "idempotency key", max_len=300)
    bid = (
        business_id.strip()
        if isinstance(business_id, str) and business_id.strip()
        else None
    )
    request_hash = fingerprint({"title": clean_title, "business_id": bid})
    session_id = new_id("wrs")
    with store.transaction() as conn:
        store._require_tenant(conn, tenant_id)
        if bid is not None:
            store._require_business(conn, tenant_id, bid)
        existing = conn.execute(
            "SELECT * FROM bos_warroom_session WHERE tenant_id=?"
            " AND idempotency_key=?",
            (tenant_id, key),
        ).fetchone()
        if existing is not None:
            if existing["request_hash"] != request_hash:
                raise BusinessError(
                    IDEMPOTENCY_CONFLICT_CODE,
                    f"idempotency key {key!r} was already used for a different"
                    " war-room session",
                )
            return models.WarRoomSession.from_row(existing), False
        created = now_iso()
        conn.execute(
            "INSERT INTO bos_warroom_session(session_id, tenant_id, business_id,"
            " title, state, idempotency_key, request_hash, brief_json,"
            " score_json, dissent_json, owner_note, created_at, updated_at)"
            " VALUES(?,?,?,?,'intake',?,?,'{}','{}','[]','',?,?)",
            (
                session_id,
                tenant_id,
                bid,
                clean_title,
                key,
                request_hash,
                created,
                created,
            ),
        )
        store._audit(
            conn,
            tenant_id,
            bid,
            "system",
            "warroom.session.create",
            "warroom_session",
            session_id,
            "ok",
            {"title": clean_title},
        )
    return get_session(store, tenant_id, session_id), True


def get_session(
    store: "BusinessStore", tenant_id: str, session_id: str
) -> models.WarRoomSession:
    with store.read() as conn:
        return models.WarRoomSession.from_row(
            _session_row(conn, tenant_id, session_id)
        )


def require_session(
    store: "BusinessStore", tenant_id: str, session_id: str
) -> models.WarRoomSession:
    return get_session(store, tenant_id, session_id)


def list_sessions(
    store: "BusinessStore",
    tenant_id: str,
    *,
    business_id: str | None = None,
    state: str | None = None,
) -> list[models.WarRoomSession]:
    if state is not None and state not in WARROOM_STATES:
        raise BusinessError(BAD_INPUT_CODE, f"unknown war-room state {state!r}")
    query = "SELECT * FROM bos_warroom_session WHERE tenant_id=?"
    params: list[Any] = [tenant_id]
    if business_id is not None:
        query += " AND business_id=?"
        params.append(business_id)
    if state is not None:
        query += " AND state=?"
        params.append(state)
    query += " ORDER BY created_at, session_id"
    with store.read() as conn:
        rows = conn.execute(query, params).fetchall()
    return [models.WarRoomSession.from_row(row) for row in rows]


def advance(
    store: "BusinessStore",
    *,
    tenant_id: str,
    session_id: str,
    target: str,
    actor: str = "system",
) -> models.WarRoomSession:
    """Move one legal step. A decision and task creation are not advanceable."""
    clean_target = _text(target, "target state", max_len=40)
    if clean_target not in WARROOM_STATES:
        raise BusinessError(
            BAD_INPUT_CODE,
            f"unknown war-room state {clean_target!r}; expected one of "
            f"{', '.join(WARROOM_STATES)}",
        )
    if clean_target in OWNER_ONLY_TARGETS:
        raise BusinessError(
            BAD_STATE_CODE,
            f"state {clean_target!r} is reached through the owner decision or"
            " task creation, not advance()",
        )
    with store.transaction() as conn:
        row = _session_row(conn, tenant_id, session_id)
        current = row["state"]
        if current == clean_target:
            return models.WarRoomSession.from_row(row)
        if clean_target not in WARROOM_TRANSITIONS[current]:
            raise BusinessError(
                BAD_STATE_CODE,
                f"war-room session cannot move {current!r} -> {clean_target!r}",
            )
        conn.execute(
            "UPDATE bos_warroom_session SET state=?, updated_at=?"
            " WHERE tenant_id=? AND session_id=?",
            (clean_target, now_iso(), tenant_id, session_id),
        )
        store._audit(
            conn,
            tenant_id,
            row["business_id"],
            str(actor or "system"),
            "warroom.session.advance",
            "warroom_session",
            session_id,
            "ok",
            {"from": current, "to": clean_target},
        )
    return get_session(store, tenant_id, session_id)


# -- sources and extraction ----------------------------------------------
def add_source(
    store: "BusinessStore",
    *,
    tenant_id: str,
    session_id: str,
    kind: str,
    locator: str,
    title: str = "",
    metadata: Mapping[str, Any] | None = None,
) -> tuple[models.WarRoomSource, bool]:
    """Record material; the same ``(kind, locator)`` is one source row."""
    clean_kind = _source_kind(kind)
    clean_locator = _text(locator, "source locator", max_len=2048)
    clean_title = str(title or "").strip()[:300]
    meta = _metadata_update(None, metadata)
    source_id = new_id("wsrc")
    with store.transaction() as conn:
        row = _session_row(conn, tenant_id, session_id)
        if row["state"] not in ("intake", "research"):
            raise BusinessError(
                BAD_STATE_CODE,
                "sources can be added during intake or research"
                f" (state={row['state']!r})",
            )
        existing = conn.execute(
            "SELECT * FROM bos_warroom_source WHERE tenant_id=? AND session_id=?"
            " AND kind=? AND locator=?",
            (tenant_id, session_id, clean_kind, clean_locator),
        ).fetchone()
        if existing is not None:
            return models.WarRoomSource.from_row(existing), False
        conn.execute(
            "INSERT INTO bos_warroom_source(source_id, tenant_id, session_id,"
            " kind, locator, title, status, adapter, artifact_id, text,"
            " metadata_json, created_at) VALUES(?,?,?,?,?,?,'pending','',NULL,"
            "NULL,?,?)",
            (
                source_id,
                tenant_id,
                session_id,
                clean_kind,
                clean_locator,
                clean_title,
                meta,
                now_iso(),
            ),
        )
        store._audit(
            conn,
            tenant_id,
            row["business_id"],
            "system",
            "warroom.source.add",
            "warroom_source",
            source_id,
            "ok",
            {"kind": clean_kind, "locator": clean_locator},
        )
    return get_source(store, tenant_id, source_id), True


def get_source(
    store: "BusinessStore", tenant_id: str, source_id: str
) -> models.WarRoomSource:
    with store.read() as conn:
        return models.WarRoomSource.from_row(
            _source_row(conn, tenant_id, source_id)
        )


def list_sources(
    store: "BusinessStore", tenant_id: str, session_id: str
) -> list[models.WarRoomSource]:
    with store.read() as conn:
        _session_row(conn, tenant_id, session_id)
        rows = conn.execute(
            "SELECT * FROM bos_warroom_source WHERE tenant_id=? AND session_id=?"
            " ORDER BY created_at, source_id",
            (tenant_id, session_id),
        ).fetchall()
    return [models.WarRoomSource.from_row(row) for row in rows]


def _mark_source_failed(
    store: "BusinessStore",
    *,
    tenant_id: str,
    session_id: str,
    source_id: str,
    code: str,
    detail: str,
) -> None:
    with store.transaction() as conn:
        row = _session_row(conn, tenant_id, session_id)
        source = _source_row(conn, tenant_id, source_id)
        meta = _metadata_update(
            source["metadata_json"], {"error_code": code, "error": detail}
        )
        conn.execute(
            "UPDATE bos_warroom_source SET status='failed', adapter=?,"
            " metadata_json=? WHERE tenant_id=? AND source_id=?",
            (source["adapter"] or source["kind"], meta, tenant_id, source_id),
        )
        store._audit(
            conn,
            tenant_id,
            row["business_id"],
            "system",
            "warroom.source.extract",
            "warroom_source",
            source_id,
            "error",
            {"code": code, "detail": detail},
        )


def extract_source(
    store: "BusinessStore",
    *,
    tenant_id: str,
    session_id: str,
    source_id: str,
    registry: Mapping[str, "SourceAdapter"] | None = None,
) -> models.WarRoomSource:
    """Extract a source through an injected adapter; persists text + artifact.

    No network call is made by the shipped stubs. A missing or failing adapter
    marks the source ``failed`` and raises the matching typed error, so a FACT
    cannot later cite material that was never read.
    """
    with store.read() as conn:
        _session_row(conn, tenant_id, session_id)
        existing = _source_row(conn, tenant_id, source_id)
    if existing["status"] == "extracted" and existing["text"]:
        return models.WarRoomSource.from_row(existing)

    adapters = dict(registry) if registry is not None else default_registry()
    adapter = adapters.get(existing["kind"])
    if adapter is None:
        detail = (
            f"no extraction adapter is installed for {existing['kind']!r}"
        )
        _mark_source_failed(
            store,
            tenant_id=tenant_id,
            session_id=session_id,
            source_id=source_id,
            code=SourceAdapterUnavailable.code,
            detail=detail,
        )
        raise BusinessError(SourceAdapterUnavailable.code, detail)

    metadata = json_loads(existing["metadata_json"], {})
    try:
        extraction = adapter.extract(existing["locator"], metadata=metadata)
    except SourceAdapterError as exc:
        _mark_source_failed(
            store,
            tenant_id=tenant_id,
            session_id=session_id,
            source_id=source_id,
            code=exc.code,
            detail=str(exc),
        )
        raise BusinessError(exc.code, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - adapter is an external seam
        detail = redact(f"{type(exc).__name__}: {exc}")[:2000]
        _mark_source_failed(
            store,
            tenant_id=tenant_id,
            session_id=session_id,
            source_id=source_id,
            code=WARROOM_ADAPTER_FAILED_CODE,
            detail=detail,
        )
        raise BusinessError(
            WARROOM_ADAPTER_FAILED_CODE,
            f"source extraction failed: {detail}",
        ) from exc

    with store.transaction() as conn:
        row = _session_row(conn, tenant_id, session_id)
        source = _source_row(conn, tenant_id, source_id)
        if source["status"] == "extracted" and source["text"]:
            return models.WarRoomSource.from_row(source)
        text = extraction.text or ""
        adapter_name = str(
            extraction.metadata.get("adapter") or type(adapter).__name__
        )
        artifact_id = source["artifact_id"]
        business_id = row["business_id"]
        if business_id is not None and artifact_id is None:
            artifact_id = _insert_artifact(
                conn,
                store,
                tenant_id,
                business_id,
                kind="warroom_source",
                name=f"{source['kind']}-{source_id}.txt",
                body=text,
                media_type=extraction.media_type,
            )
        meta = _metadata_update(
            source["metadata_json"],
            extraction.metadata,
            {"adapter": adapter_name},
        )
        title = source["title"] or extraction.title
        conn.execute(
            "UPDATE bos_warroom_source SET status='extracted', title=?,"
            " adapter=?, artifact_id=?, text=?, metadata_json=?"
            " WHERE tenant_id=? AND source_id=?",
            (
                title,
                adapter_name,
                artifact_id,
                text,
                meta,
                tenant_id,
                source_id,
            ),
        )
        store._audit(
            conn,
            tenant_id,
            business_id,
            "system",
            "warroom.source.extract",
            "warroom_source",
            source_id,
            "ok",
            {
                "kind": source["kind"],
                "artifact_id": artifact_id,
                "chars": len(text),
            },
        )
    return get_source(store, tenant_id, source_id)


# -- evidence -------------------------------------------------------------
def add_evidence(
    store: "BusinessStore",
    *,
    tenant_id: str,
    session_id: str,
    label: str,
    claim: str,
    source_id: str | None = None,
    quote: str = "",
    metadata: Mapping[str, Any] | None = None,
) -> tuple[models.WarRoomEvidence, bool]:
    """Record one labelled claim; a FACT must cite an extracted source."""
    clean_label = _label(label)
    clean_claim = _text(claim, "claim", max_len=2000)
    clean_quote = str(quote or "")[:4000]
    source = (
        source_id.strip()
        if isinstance(source_id, str) and source_id.strip()
        else None
    )
    meta = _metadata_update(None, metadata)
    evidence_id = new_id("wev")
    with store.transaction() as conn:
        row = _session_row(conn, tenant_id, session_id)
        if row["state"] not in (
            "intake",
            "research",
            "debate",
            "synthesis",
        ):
            raise BusinessError(
                BAD_STATE_CODE,
                "evidence cannot be added after the owner decision"
                f" (state={row['state']!r})",
            )
        if source is not None:
            cited = conn.execute(
                "SELECT * FROM bos_warroom_source WHERE tenant_id=?"
                " AND session_id=? AND source_id=?",
                (tenant_id, session_id, source),
            ).fetchone()
            if cited is None:
                if clean_label == "FACT":
                    raise BusinessError(
                        WARROOM_UNVERIFIED_FACT_CODE,
                        f"FACT cites source {source!r}, which does not belong to"
                        " this War Room session",
                    )
                raise BusinessError(
                    NOT_FOUND_CODE,
                    f"no source {source!r} in this War Room session",
                )
        elif clean_label == "FACT":
            raise BusinessError(
                WARROOM_UNVERIFIED_FACT_CODE,
                "a FACT must cite a source; use INFERENCE, ASSUMPTION or"
                " UNKNOWN for unsourced claims",
            )
        if clean_label == "FACT":
            cited = conn.execute(
                "SELECT * FROM bos_warroom_source WHERE tenant_id=?"
                " AND session_id=? AND source_id=?",
                (tenant_id, session_id, source),
            ).fetchone()
            if cited is None or cited["status"] != "extracted" or not cited["text"]:
                raise BusinessError(
                    WARROOM_UNVERIFIED_FACT_CODE,
                    f"FACT source {source!r} has not been extracted; extract it"
                    " before citing it as a fact",
                )
        existing = conn.execute(
            "SELECT * FROM bos_warroom_evidence WHERE tenant_id=? AND session_id=?"
            " AND label=? AND claim=?",
            (tenant_id, session_id, clean_label, clean_claim),
        ).fetchone()
        if existing is not None:
            return models.WarRoomEvidence.from_row(existing), False
        conn.execute(
            "INSERT INTO bos_warroom_evidence(evidence_id, tenant_id, session_id,"
            " label, claim, source_id, quote, metadata_json, created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (
                evidence_id,
                tenant_id,
                session_id,
                clean_label,
                clean_claim,
                source,
                clean_quote,
                meta,
                now_iso(),
            ),
        )
        store._audit(
            conn,
            tenant_id,
            row["business_id"],
            "system",
            "warroom.evidence.add",
            "warroom_evidence",
            evidence_id,
            "ok",
            {"label": clean_label, "source_id": source},
        )
    return get_evidence(store, tenant_id, evidence_id), True


def get_evidence(
    store: "BusinessStore", tenant_id: str, evidence_id: str
) -> models.WarRoomEvidence:
    with store.read() as conn:
        row = conn.execute(
            "SELECT * FROM bos_warroom_evidence WHERE tenant_id=?"
            " AND evidence_id=?",
            (tenant_id, evidence_id),
        ).fetchone()
    if row is None:
        raise BusinessError(
            NOT_FOUND_CODE,
            f"no evidence {evidence_id!r} in tenant {tenant_id!r}",
        )
    return models.WarRoomEvidence.from_row(row)


def list_evidence(
    store: "BusinessStore", tenant_id: str, session_id: str
) -> list[models.WarRoomEvidence]:
    with store.read() as conn:
        _session_row(conn, tenant_id, session_id)
        rows = conn.execute(
            "SELECT * FROM bos_warroom_evidence WHERE tenant_id=?"
            " AND session_id=? ORDER BY created_at, evidence_id",
            (tenant_id, session_id),
        ).fetchall()
    return [models.WarRoomEvidence.from_row(row) for row in rows]


# -- debate ---------------------------------------------------------------
def add_argument(
    store: "BusinessStore",
    *,
    tenant_id: str,
    session_id: str,
    round_no: int,
    role: str,
    stance: str,
    text: str,
    evidence_ids: list[str] | None = None,
) -> tuple[models.WarRoomArgument, bool]:
    """Append one debate turn; arguments are never edited or deleted."""
    number = _round_no(round_no)
    clean_role = _role(role)
    clean_stance = _stance(stance)
    clean_text = _text(text, "argument text", max_len=4000)
    refs: list[str] = []
    for ref in evidence_ids or []:
        clean_ref = _text(ref, "evidence id", max_len=80)
        if clean_ref not in refs:
            refs.append(clean_ref)
    argument_id = new_id("warg")
    with store.transaction() as conn:
        row = _session_row(conn, tenant_id, session_id)
        if row["state"] not in ("research", "debate"):
            raise BusinessError(
                BAD_STATE_CODE,
                "arguments belong to research/debate"
                f" (state={row['state']!r})",
            )
        for ref in refs:
            found = conn.execute(
                "SELECT 1 FROM bos_warroom_evidence WHERE tenant_id=?"
                " AND session_id=? AND evidence_id=?",
                (tenant_id, session_id, ref),
            ).fetchone()
            if found is None:
                raise BusinessError(
                    NOT_FOUND_CODE,
                    f"argument cites evidence {ref!r}, which is not in this"
                    " War Room session",
                )
        existing = conn.execute(
            "SELECT * FROM bos_warroom_argument WHERE tenant_id=?"
            " AND session_id=? AND round_no=? AND role=? AND stance=? AND text=?",
            (tenant_id, session_id, number, clean_role, clean_stance, clean_text),
        ).fetchone()
        if existing is not None:
            return models.WarRoomArgument.from_row(existing), False
        conn.execute(
            "INSERT INTO bos_warroom_argument(argument_id, tenant_id, session_id,"
            " round_no, role, stance, text, evidence_ids_json, created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (
                argument_id,
                tenant_id,
                session_id,
                number,
                clean_role,
                clean_stance,
                clean_text,
                json_dumps(refs),
                now_iso(),
            ),
        )
        store._audit(
            conn,
            tenant_id,
            row["business_id"],
            "system",
            "warroom.argument.add",
            "warroom_argument",
            argument_id,
            "ok",
            {
                "round": number,
                "role": clean_role,
                "stance": clean_stance,
                "evidence_ids": refs,
            },
        )
    return get_argument(store, tenant_id, argument_id), True


def get_argument(
    store: "BusinessStore", tenant_id: str, argument_id: str
) -> models.WarRoomArgument:
    with store.read() as conn:
        row = conn.execute(
            "SELECT * FROM bos_warroom_argument WHERE tenant_id=?"
            " AND argument_id=?",
            (tenant_id, argument_id),
        ).fetchone()
    if row is None:
        raise BusinessError(
            NOT_FOUND_CODE,
            f"no argument {argument_id!r} in tenant {tenant_id!r}",
        )
    return models.WarRoomArgument.from_row(row)


def list_arguments(
    store: "BusinessStore", tenant_id: str, session_id: str
) -> list[models.WarRoomArgument]:
    with store.read() as conn:
        _session_row(conn, tenant_id, session_id)
        rows = conn.execute(
            "SELECT * FROM bos_warroom_argument WHERE tenant_id=?"
            " AND session_id=? ORDER BY round_no, created_at, argument_id",
            (tenant_id, session_id),
        ).fetchall()
    return [models.WarRoomArgument.from_row(row) for row in rows]


# -- synthesis ------------------------------------------------------------
def synthesize(
    store: "BusinessStore",
    *,
    tenant_id: str,
    session_id: str,
    brief: Mapping[str, Any],
    score: Mapping[str, Any],
    experiments: list[Any] | None = None,
    actor: str = "system",
) -> models.WarRoomSession:
    """Write the brief/score and preserve dissent; no side effect is created."""
    clean_brief = _validate_brief(brief)
    clean_score = _validate_score(score)
    planned = _normalise_experiments(
        experiments if experiments is not None else clean_brief.get("experiments")
    )
    with store.transaction() as conn:
        row = _session_row(conn, tenant_id, session_id)
        if row["state"] not in ("debate", "synthesis"):
            raise BusinessError(
                BAD_STATE_CODE,
                "synthesis requires a completed debate"
                f" (state={row['state']!r})",
            )
        arguments = conn.execute(
            "SELECT * FROM bos_warroom_argument WHERE tenant_id=?"
            " AND session_id=? ORDER BY round_no, created_at, argument_id",
            (tenant_id, session_id),
        ).fetchall()
        dissent = [
            {
                "argument_id": item["argument_id"],
                "round": item["round_no"],
                "role": item["role"],
                "stance": item["stance"],
                "text": item["text"],
                "evidence_ids": json_loads(item["evidence_ids_json"], []),
            }
            for item in arguments
            if item["stance"] in ("oppose", "question")
        ]
        conn.execute(
            "UPDATE bos_warroom_session SET state='synthesis', brief_json=?,"
            " score_json=?, dissent_json=?, updated_at=?"
            " WHERE tenant_id=? AND session_id=?",
            (
                json_dumps(redact(clean_brief)),
                json_dumps(redact(clean_score)),
                json_dumps(dissent),
                now_iso(),
                tenant_id,
                session_id,
            ),
        )
        for experiment in planned:
            conn.execute(
                "INSERT INTO bos_warroom_experiment(experiment_id, tenant_id,"
                " session_id, name, hypothesis, method, success_metric, status,"
                " created_at) VALUES(?,?,?,?,?,?,?,'planned',?)"
                " ON CONFLICT(tenant_id, session_id, name) DO NOTHING",
                (
                    new_id("wexp"),
                    tenant_id,
                    session_id,
                    experiment["name"],
                    experiment["hypothesis"],
                    experiment["method"],
                    experiment["success_metric"],
                    now_iso(),
                ),
            )
        store._audit(
            conn,
            tenant_id,
            row["business_id"],
            str(actor or "system"),
            "warroom.synthesize",
            "warroom_session",
            session_id,
            "ok",
            {
                "experiments": [item["name"] for item in planned],
                "dissent": len(dissent),
            },
        )
    return get_session(store, tenant_id, session_id)


def list_experiments(
    store: "BusinessStore", tenant_id: str, session_id: str
) -> list[models.WarRoomExperiment]:
    with store.read() as conn:
        _session_row(conn, tenant_id, session_id)
        rows = conn.execute(
            "SELECT * FROM bos_warroom_experiment WHERE tenant_id=?"
            " AND session_id=? ORDER BY created_at, experiment_id",
            (tenant_id, session_id),
        ).fetchall()
    return [models.WarRoomExperiment.from_row(row) for row in rows]


# -- owner decision -------------------------------------------------------
def record_decision(
    store: "BusinessStore",
    *,
    tenant_id: str,
    session_id: str,
    decision: str,
    decided_by: str,
    note: str = "",
    idempotency_key: str | None = None,
) -> tuple[models.WarRoomDecision, bool]:
    """Record the human owner's approved/rejected decision.

    A replay with the same key and body returns the original decision. The
    actor must be the owner (``owner`` or ``owner:<name>``); an agent cannot
    approve its own idea.
    """
    status = _text(decision, "decision", max_len=20).lower()
    if status not in ("approved", "rejected"):
        raise BusinessError(
            BAD_INPUT_CODE,
            "decision must be 'approved' or 'rejected'",
        )
    owner = _owner(decided_by)
    clean_note = str(note or "").strip()[:2000]
    key = (
        idempotency_key.strip()
        if isinstance(idempotency_key, str) and idempotency_key.strip()
        else f"warroom-decision:{session_id}"
    )
    request_hash = fingerprint(
        {"session_id": session_id, "decision": status, "note": clean_note}
    )
    decision_id = new_id("wdec")
    with store.transaction() as conn:
        row = _session_row(conn, tenant_id, session_id)
        existing = conn.execute(
            "SELECT * FROM bos_warroom_decision WHERE tenant_id=?"
            " AND idempotency_key=?",
            (tenant_id, key),
        ).fetchone()
        if existing is not None:
            if existing["request_hash"] != request_hash:
                raise BusinessError(
                    IDEMPOTENCY_CONFLICT_CODE,
                    f"idempotency key {key!r} was already used for a different"
                    " war-room decision",
                )
            return models.WarRoomDecision.from_row(existing), False
        if row["state"] != "owner_decision":
            raise BusinessError(
                BAD_STATE_CODE,
                "the owner can only decide a session awaiting a decision"
                f" (state={row['state']!r})",
            )
        conn.execute(
            "INSERT INTO bos_warroom_decision(decision_id, tenant_id, session_id,"
            " status, decided_by, note, idempotency_key, request_hash,"
            " created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                decision_id,
                tenant_id,
                session_id,
                status,
                owner,
                clean_note,
                key,
                request_hash,
                now_iso(),
            ),
        )
        conn.execute(
            "UPDATE bos_warroom_session SET state=?, owner_note=?, updated_at=?"
            " WHERE tenant_id=? AND session_id=?",
            (status, clean_note, now_iso(), tenant_id, session_id),
        )
        store._audit(
            conn,
            tenant_id,
            row["business_id"],
            owner,
            "warroom.decision",
            "warroom_session",
            session_id,
            "ok",
            {"decision": status, "note": clean_note},
        )
    return get_decision(store, tenant_id, decision_id), True


def get_decision(
    store: "BusinessStore", tenant_id: str, decision_id: str
) -> models.WarRoomDecision:
    with store.read() as conn:
        row = conn.execute(
            "SELECT * FROM bos_warroom_decision WHERE tenant_id=?"
            " AND decision_id=?",
            (tenant_id, decision_id),
        ).fetchone()
    if row is None:
        raise BusinessError(
            NOT_FOUND_CODE,
            f"no decision {decision_id!r} in tenant {tenant_id!r}",
        )
    return models.WarRoomDecision.from_row(row)


def list_decisions(
    store: "BusinessStore", tenant_id: str, session_id: str
) -> list[models.WarRoomDecision]:
    with store.read() as conn:
        _session_row(conn, tenant_id, session_id)
        rows = conn.execute(
            "SELECT * FROM bos_warroom_decision WHERE tenant_id=?"
            " AND session_id=? ORDER BY created_at, decision_id",
            (tenant_id, session_id),
        ).fetchall()
    return [models.WarRoomDecision.from_row(row) for row in rows]


# -- tasks and launch -----------------------------------------------------
def create_tasks(
    store: "BusinessStore",
    *,
    tenant_id: str,
    session_id: str,
    actor: str = "owner",
) -> list[models.Task]:
    """Create the approved brief's tasks. Refused unless the owner approved."""
    with store.transaction() as conn:
        row = _session_row(conn, tenant_id, session_id)
        existing_rows = _tasks_for_session(conn, tenant_id, session_id)
        if row["state"] == "tasks_created":
            return [models.Task.from_row(item) for item in existing_rows]
        if row["state"] != "approved":
            raise BusinessError(
                BAD_STATE_CODE,
                "tasks are only created after the owner approves the idea"
                f" (state={row['state']!r})",
            )
        business_id = row["business_id"]
        if business_id is None:
            raise BusinessError(
                BAD_INPUT_CODE,
                "task creation needs a business; this War Room session has none",
            )
        brief = json_loads(row["brief_json"], {})
        planned = brief.get("tasks") if isinstance(brief, dict) else None
        if not isinstance(planned, list) or not planned:
            raise BusinessError(
                BAD_INPUT_CODE, "the approved brief has no tasks to create"
            )
        by_title = {item["title"]: item for item in existing_rows}
        created_ids: list[str] = []
        scheduled: set[str] = set()
        now = now_iso()
        for index, item in enumerate(planned):
            if isinstance(item, str):
                title = _text(item, "task title", max_len=500)
                assignee = None
                extra: dict[str, Any] = {}
            elif isinstance(item, Mapping):
                title = _text(item.get("title"), "task title", max_len=500)
                assignee = (
                    item.get("assigned_to").strip()
                    if isinstance(item.get("assigned_to"), str)
                    and item.get("assigned_to").strip()
                    else None
                )
                extra = {
                    key: value
                    for key, value in item.items()
                    if key not in ("title", "assigned_to")
                }
            else:
                raise BusinessError(
                    BAD_INPUT_CODE,
                    "each brief task must be a string or a mapping",
                )
            if title in by_title or title in scheduled:
                continue
            scheduled.add(title)
            task_id = new_id("task")
            metadata = {
                "warroom_session_id": session_id,
                "warroom_task_index": index,
                **extra,
            }
            conn.execute(
                "INSERT INTO bos_task(task_id, tenant_id, business_id, title,"
                " status, assigned_to, created_at, updated_at, metadata_json)"
                " VALUES(?,?,?,?,'open',?,?,?,?)",
                (
                    task_id,
                    tenant_id,
                    business_id,
                    title,
                    assignee,
                    now,
                    now,
                    json_dumps(redact(metadata)),
                ),
            )
            created_ids.append(task_id)
        conn.execute(
            "UPDATE bos_warroom_session SET state='tasks_created', updated_at=?"
            " WHERE tenant_id=? AND session_id=?",
            (now, tenant_id, session_id),
        )
        store._audit(
            conn,
            tenant_id,
            business_id,
            str(actor or "owner"),
            "warroom.tasks.create",
            "warroom_session",
            session_id,
            "ok",
            {"created": created_ids},
        )
        final_rows = _tasks_for_session(conn, tenant_id, session_id)
    return [models.Task.from_row(item) for item in final_rows]


def request_action(
    store: "BusinessStore",
    *,
    tenant_id: str,
    session_id: str,
    action: str,
    requested_by: str,
    idempotency_key: str,
    payload: Mapping[str, Any] | None = None,
    job_id: str | None = None,
) -> tuple[models.Approval, bool]:
    """Route a launch-side effect through the ordinary BOS approval gate.

    This creates a pending approval; it does not execute anything. The tool
    gateway still refuses the real action until that approval is approved, so
    the War Room cannot publish, spend or contact anyone on its own.
    """
    session = get_session(store, tenant_id, session_id)
    if session.state not in ("approved", "tasks_created"):
        raise BusinessError(
            BAD_STATE_CODE,
            "side effects may only be requested after the owner approves"
            f" (state={session.state!r})",
        )
    if session.business_id is None:
        raise BusinessError(
            BAD_INPUT_CODE,
            "this War Room session has no business to route an action through",
        )
    body = dict(payload or {})
    body.setdefault("warroom_session_id", session_id)
    from . import approvals

    return approvals.request(
        store,
        tenant_id=tenant_id,
        business_id=session.business_id,
        action=action,
        requested_by=requested_by,
        idempotency_key=idempotency_key,
        payload=body,
        job_id=job_id,
    )


def get_bundle(
    store: "BusinessStore", tenant_id: str, session_id: str
) -> dict[str, Any]:
    """Everything a War Room surface renders, from durable rows only."""
    session = get_session(store, tenant_id, session_id)
    with store.read() as conn:
        task_rows = _tasks_for_session(conn, tenant_id, session_id)
    return {
        "session": session,
        "sources": list_sources(store, tenant_id, session_id),
        "evidence": list_evidence(store, tenant_id, session_id),
        "arguments": list_arguments(store, tenant_id, session_id),
        "experiments": list_experiments(store, tenant_id, session_id),
        "decisions": list_decisions(store, tenant_id, session_id),
        "tasks": [models.Task.from_row(item) for item in task_rows],
    }


__all__ = [
    "DEBATE_ROLES",
    "EVIDENCE_LABELS",
    "REQUIRED_BRIEF_KEYS",
    "SCORE_METRICS",
    "STANCES",
    "WARROOM_STATES",
    "WARROOM_TRANSITIONS",
    "add_argument",
    "add_evidence",
    "add_source",
    "advance",
    "create_session",
    "create_tasks",
    "extract_source",
    "get_argument",
    "get_bundle",
    "get_decision",
    "get_evidence",
    "get_session",
    "get_source",
    "list_arguments",
    "list_decisions",
    "list_evidence",
    "list_experiments",
    "list_sessions",
    "list_sources",
    "record_decision",
    "request_action",
    "require_session",
    "synthesize",
]

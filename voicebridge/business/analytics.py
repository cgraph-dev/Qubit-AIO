"""KPI contracts with drill-through and honest freshness (P5-07).

WHY THIS EXISTS
---------------
An analytics number is not truth unless it names its formula, its source rows
and its age. This module stores a **MetricContract** and computes the value
live from the Business OS tables (never from a cached narrative):

* **Contract.** ``metric_id``, name, structured formula, source tables,
  timezone, attribution window, owner, refresh cadence, acceptable staleness,
  target and anomaly threshold. Invalid formulas are typed refusals.
* **Drill-through.** Every computation returns the source row ids it counted,
  bounded, with a ``truncated`` marker. A caller can open one row through
  :func:`drill`.
* **Labelling.** A derived estimate is marked ``methodology="derived"`` and
  carries a required derivation note; a measured value is ``measured``.
* **Freshness.** A result is ``fresh`` only while the newest matching source
  row is inside the acceptable staleness. Otherwise it is ``stale`` with the
  computed value, or ``unknown`` when there is nothing to measure. No path
  fabricates a number for an unknown.

Formulas are declarative data (``count`` / ``sum`` / ``avg`` / ``ratio`` over a
whitelisted table and column allowlist), never SQL text or ``eval``.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Mapping, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .errors import BusinessError
from .ids import fingerprint, new_id
from .util import json_dumps, json_loads, now_iso, require_text

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .store import BusinessStore

SCHEMA = "qubit-office-analytics/1"

METRIC_NOT_FOUND_CODE = "METRIC_NOT_FOUND"
METRIC_BAD_INPUT_CODE = "METRIC_BAD_INPUT"
METRIC_FORMULA_INVALID_CODE = "METRIC_FORMULA_INVALID"
METRIC_SOURCE_UNKNOWN_CODE = "METRIC_SOURCE_UNKNOWN"
METRIC_SNAPSHOT_MISMATCH_CODE = "METRIC_SNAPSHOT_MISMATCH"

OPS = ("count", "sum", "avg", "ratio")
METHODOLOGIES = ("measured", "derived")
STATUSES = ("fresh", "stale", "unknown")

# The only tables a metric may read, and the only columns a formula may filter,
# sum or average. Anything else is a typed refusal: a KPI cannot reach an
# arbitrary table by name.
SOURCE_TABLES: dict[str, tuple[str, str]] = {
    "bos_ledger_entry": (
        "entry_id",
        ("entry_id", "tenant_id", "business_id", "account", "direction",
         "amount_minor", "currency", "created_at"),
    ),
    "bos_task": (
        "task_id",
        ("task_id", "tenant_id", "business_id", "title", "status",
         "created_at", "updated_at"),
    ),
    "bos_contact": (
        "contact_id",
        ("contact_id", "tenant_id", "business_id", "kind", "display_name",
         "created_at"),
    ),
    "bos_agent": (
        "agent_id",
        ("agent_id", "tenant_id", "business_id", "role_id", "department",
         "status", "created_at", "updated_at"),
    ),
    "bos_people_person": (
        "person_id",
        ("person_id", "tenant_id", "business_id", "kind", "status",
         "joined_at", "left_at", "created_at"),
    ),
    "bos_people_procurement": (
        "request_id",
        ("request_id", "tenant_id", "business_id", "requester",
         "amount_minor", "currency", "status", "created_at"),
    ),
    "bos_legal_contract": (
        "contract_id",
        ("contract_id", "tenant_id", "business_id", "entity_id", "status",
         "created_at", "updated_at"),
    ),
    "bos_legal_obligation": (
        "obligation_id",
        ("obligation_id", "tenant_id", "contract_id", "kind", "status",
         "due_at", "created_at", "updated_at"),
    ),
}

_NUMERIC_FIELDS = frozenset({"amount_minor"})
_MAX_FILTER_KEYS = 20

_SCHEMA_SQL = (
    """
    CREATE TABLE IF NOT EXISTS bos_metric_contract (
        metric_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL REFERENCES bos_tenant(tenant_id) ON DELETE RESTRICT,
        business_id TEXT REFERENCES bos_business(business_id) ON DELETE RESTRICT,
        name TEXT NOT NULL,
        formula_json TEXT NOT NULL,
        source_tables_json TEXT NOT NULL,
        timezone TEXT NOT NULL,
        attribution_window_seconds INTEGER NOT NULL
            CHECK(attribution_window_seconds > 0),
        owner TEXT NOT NULL,
        refresh_cadence_seconds INTEGER NOT NULL
            CHECK(refresh_cadence_seconds > 0),
        acceptable_staleness_seconds INTEGER NOT NULL
            CHECK(acceptable_staleness_seconds >= 0),
        target REAL,
        anomaly_threshold REAL,
        methodology TEXT NOT NULL CHECK(methodology IN ('measured','derived')),
        derivation_note TEXT NOT NULL DEFAULT '',
        allow_empty INTEGER NOT NULL DEFAULT 0 CHECK(allow_empty IN (0,1)),
        idempotency_key TEXT NOT NULL,
        request_hash TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(tenant_id, idempotency_key)
    )
    """,
    "CREATE INDEX IF NOT EXISTS bos_metric_contract_listing"
    " ON bos_metric_contract(tenant_id, business_id, name)",
    """
    CREATE TABLE IF NOT EXISTS bos_metric_snapshot (
        snapshot_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        metric_id TEXT NOT NULL REFERENCES bos_metric_contract(metric_id)
            ON DELETE RESTRICT,
        computed_at TEXT NOT NULL,
        as_of TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('fresh','stale','unknown')),
        value REAL,
        methodology TEXT NOT NULL CHECK(methodology IN ('measured','derived')),
        source_row_ids_json TEXT NOT NULL DEFAULT '[]',
        note TEXT NOT NULL DEFAULT ''
    )
    """,
    "CREATE INDEX IF NOT EXISTS bos_metric_snapshot_listing"
    " ON bos_metric_snapshot(tenant_id, metric_id, computed_at)",
)


def _text(value: Any, label: str, *, max_len: int = 4096) -> str:
    try:
        return require_text(value, label, max_len=max_len)
    except ValueError as exc:
        raise BusinessError(METRIC_BAD_INPUT_CODE, str(exc)) from exc


def _int(value: Any, label: str, *, minimum: int = 0) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise BusinessError(
            METRIC_BAD_INPUT_CODE, f"{label} must be an integer >= {minimum}"
        )
    return value


def _number(value: Any, label: str, *, allow_none: bool = True) -> float | None:
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BusinessError(METRIC_BAD_INPUT_CODE, f"{label} must be a number")
    return float(value)


def _ensure(conn: sqlite3.Connection) -> None:
    for statement in _SCHEMA_SQL:
        conn.execute(statement)


def ensure_schema(store: "BusinessStore") -> None:
    with store.transaction() as conn:
        _ensure(conn)


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


# -- formula validation ----------------------------------------------------
def validate_formula(
    formula: Any, source_tables: Sequence[str] | None = None
) -> dict[str, Any]:
    """Return a normalized formula or raise a typed refusal.

    The grammar is intentionally small: no SQL text, no ``eval`` and no field
    outside each table's declared column list.
    """
    return _validate(formula, source_tables=source_tables, depth=0)


def _validate(
    formula: Any,
    *,
    source_tables: Sequence[str] | None,
    depth: int,
) -> dict[str, Any]:
    if not isinstance(formula, Mapping):
        raise BusinessError(METRIC_FORMULA_INVALID_CODE, "formula must be a mapping")
    op = formula.get("op")
    if not isinstance(op, str) or op not in OPS:
        raise BusinessError(
            METRIC_FORMULA_INVALID_CODE, f"formula op must be one of {', '.join(OPS)}"
        )
    if op == "ratio":
        if depth > 0:
            raise BusinessError(
                METRIC_FORMULA_INVALID_CODE, "a ratio cannot nest another ratio"
            )
        numerator = _validate(
            formula.get("numerator"), source_tables=source_tables, depth=depth + 1
        )
        denominator = _validate(
            formula.get("denominator"), source_tables=source_tables, depth=depth + 1
        )
        if numerator["op"] == "ratio" or denominator["op"] == "ratio":
            raise BusinessError(
                METRIC_FORMULA_INVALID_CODE, "a ratio cannot nest another ratio"
            )
        return {
            "op": "ratio",
            "numerator": numerator,
            "denominator": denominator,
        }
    table = formula.get("table")
    if not isinstance(table, str) or table not in SOURCE_TABLES:
        raise BusinessError(
            METRIC_SOURCE_UNKNOWN_CODE,
            f"source table {table!r} is not a declared KPI source",
        )
    if source_tables is not None and table not in source_tables:
        raise BusinessError(
            METRIC_SOURCE_UNKNOWN_CODE,
            f"source table {table!r} is not declared by this contract",
        )
    _, columns = SOURCE_TABLES[table]
    field = formula.get("field")
    if op in ("sum", "avg"):
        if not isinstance(field, str) or field not in columns:
            raise BusinessError(
                METRIC_FORMULA_INVALID_CODE,
                f"{op} needs a declared field on {table}",
            )
        if op == "sum" and field not in _NUMERIC_FIELDS and field not in (
            "amount_minor",
        ):
            raise BusinessError(
                METRIC_FORMULA_INVALID_CODE,
                f"{op} can only sum a numeric money field",
            )
    elif field is not None:
        raise BusinessError(
            METRIC_FORMULA_INVALID_CODE, f"{op} does not take a field"
        )
    raw_filter = formula.get("filter") or {}
    if not isinstance(raw_filter, Mapping):
        raise BusinessError(METRIC_FORMULA_INVALID_CODE, "filter must be a mapping")
    if len(raw_filter) > _MAX_FILTER_KEYS:
        raise BusinessError(METRIC_FORMULA_INVALID_CODE, "filter has too many keys")
    clean_filter: dict[str, Any] = {}
    for key, value in raw_filter.items():
        if not isinstance(key, str) or key not in columns:
            raise BusinessError(
                METRIC_FORMULA_INVALID_CODE,
                f"filter column {key!r} is not declared on {table}",
            )
        if isinstance(value, (dict, list, tuple, set)) or isinstance(value, bytes):
            raise BusinessError(
                METRIC_FORMULA_INVALID_CODE, "filter values must be scalars"
            )
        clean_filter[key] = value
    return {
        "op": op,
        "table": table,
        "field": field if op in ("sum", "avg") else None,
        "filter": clean_filter,
    }


def _formula_tables(formula: Mapping[str, Any]) -> list[str]:
    if formula["op"] == "ratio":
        tables = _formula_tables(formula["numerator"]) + _formula_tables(
            formula["denominator"]
        )
    else:
        tables = [formula["table"]]
    return sorted(set(tables))


# -- contract CRUD ---------------------------------------------------------
def _contract_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "metric_id": row["metric_id"],
        "tenant_id": row["tenant_id"],
        "business_id": row["business_id"],
        "name": row["name"],
        "formula": json_loads(row["formula_json"], {}),
        "source_tables": json_loads(row["source_tables_json"], []),
        "timezone": row["timezone"],
        "attribution_window_seconds": int(row["attribution_window_seconds"]),
        "owner": row["owner"],
        "refresh_cadence_seconds": int(row["refresh_cadence_seconds"]),
        "acceptable_staleness_seconds": int(row["acceptable_staleness_seconds"]),
        "target": row["target"],
        "anomaly_threshold": row["anomaly_threshold"],
        "methodology": row["methodology"],
        "derivation_note": row["derivation_note"],
        "allow_empty": bool(row["allow_empty"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def create_contract(
    store: "BusinessStore",
    tenant_id: str,
    *,
    name: str,
    formula: Any,
    owner: str,
    timezone_name: str = "UTC",
    attribution_window_seconds: int,
    refresh_cadence_seconds: int,
    acceptable_staleness_seconds: int,
    business_id: str | None = None,
    target: float | None = None,
    anomaly_threshold: float | None = None,
    methodology: str = "measured",
    derivation_note: str = "",
    allow_empty: bool = False,
    idempotency_key: str,
    actor: str = "system",
) -> dict[str, Any]:
    normalized = validate_formula(formula)
    tables = _formula_tables(normalized)
    try:
        ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise BusinessError(
            METRIC_BAD_INPUT_CODE, f"unknown timezone {timezone_name!r}"
        ) from exc
    clean_method = _text(methodology, "methodology", max_len=20).lower()
    if clean_method not in METHODOLOGIES:
        raise BusinessError(
            METRIC_BAD_INPUT_CODE,
            f"methodology must be one of {', '.join(METHODOLOGIES)}",
        )
    note = str(derivation_note or "")[:2000]
    if clean_method == "derived" and not note.strip():
        raise BusinessError(
            METRIC_BAD_INPUT_CODE,
            "a derived estimate must carry a derivation note",
        )
    if _number(target, "target", allow_none=True) is not None:
        target = float(target)
    if _number(anomaly_threshold, "anomaly_threshold", allow_none=True) is not None:
        anomaly_threshold = float(anomaly_threshold)

    key = _text(idempotency_key, "idempotency key", max_len=300)
    fields = {
        "name": _text(name, "name", max_len=200),
        "formula": normalized,
        "source_tables": tables,
        "timezone": timezone_name,
        "attribution_window_seconds": _int(
            attribution_window_seconds, "attribution_window_seconds", minimum=1
        ),
        "owner": _text(owner, "owner", max_len=200),
        "refresh_cadence_seconds": _int(
            refresh_cadence_seconds, "refresh_cadence_seconds", minimum=1
        ),
        "acceptable_staleness_seconds": _int(
            acceptable_staleness_seconds, "acceptable_staleness_seconds"
        ),
        "business_id": business_id,
        "target": target,
        "anomaly_threshold": anomaly_threshold,
        "methodology": clean_method,
        "derivation_note": note,
        "allow_empty": bool(allow_empty),
    }
    request_hash = fingerprint(
        {"action": "metric.contract", "tenant_id": tenant_id, "fields": fields}
    )
    metric_id = new_id("kpi")
    created = now_iso()
    with store.transaction() as conn:
        _ensure(conn)
        store._require_tenant(conn, tenant_id)
        if business_id is not None:
            store._require_business(conn, tenant_id, business_id)
        existing = conn.execute(
            "SELECT * FROM bos_metric_contract WHERE tenant_id=?"
            " AND idempotency_key=?",
            (tenant_id, key),
        ).fetchone()
        if existing is not None:
            if existing["request_hash"] != request_hash:
                raise BusinessError(
                    "BUSINESS_IDEMPOTENCY_CONFLICT",
                    f"idempotency key {key!r} was already used for a different metric",
                )
            return _contract_dict(existing)
        conn.execute(
            "INSERT INTO bos_metric_contract(metric_id, tenant_id, business_id,"
            " name, formula_json, source_tables_json, timezone,"
            " attribution_window_seconds, owner, refresh_cadence_seconds,"
            " acceptable_staleness_seconds, target, anomaly_threshold,"
            " methodology, derivation_note, allow_empty, idempotency_key,"
            " request_hash, created_at, updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                metric_id,
                tenant_id,
                business_id,
                fields["name"],
                json_dumps(normalized),
                json_dumps(tables),
                timezone_name,
                fields["attribution_window_seconds"],
                fields["owner"],
                fields["refresh_cadence_seconds"],
                fields["acceptable_staleness_seconds"],
                target,
                anomaly_threshold,
                clean_method,
                note,
                1 if allow_empty else 0,
                key,
                request_hash,
                created,
                created,
            ),
        )
        store._audit(
            conn, tenant_id, business_id, actor, "metric.contract.create",
            "metric_contract", metric_id, "ok",
            {"name": fields["name"], "source_tables": tables},
        )
    return get_contract(store, tenant_id, metric_id)


def get_contract(
    store: "BusinessStore", tenant_id: str, metric_id: str
) -> dict[str, Any]:
    with store.read() as conn:
        _ensure(conn)
        row = conn.execute(
            "SELECT * FROM bos_metric_contract WHERE tenant_id=? AND metric_id=?",
            (tenant_id, metric_id),
        ).fetchone()
    if row is None:
        raise BusinessError(
            METRIC_NOT_FOUND_CODE,
            f"no metric {metric_id!r} in tenant {tenant_id!r}",
        )
    return _contract_dict(row)


def list_contracts(
    store: "BusinessStore", tenant_id: str, *, business_id: str | None = None
) -> list[dict[str, Any]]:
    query = "SELECT * FROM bos_metric_contract WHERE tenant_id=?"
    params: list[Any] = [tenant_id]
    if business_id is not None:
        query += " AND business_id=?"
        params.append(business_id)
    query += " ORDER BY created_at, metric_id"
    with store.read() as conn:
        _ensure(conn)
        return [_contract_dict(row) for row in conn.execute(query, params).fetchall()]


# -- computation -----------------------------------------------------------
def _window(as_of: str, seconds: int) -> tuple[str, str]:
    end = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    start = end.timestamp() - seconds
    start_iso = datetime.fromtimestamp(start, tz=timezone.utc).isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")
    return start_iso, as_of


def _where(leaf: Mapping[str, Any], start: str, end: str) -> tuple[str, list[Any]]:
    clauses = ["tenant_id=?", "created_at >= ? AND created_at <= ?"]
    params: list[Any] = [None, start, end]  # tenant replaced by caller
    for key, value in sorted(leaf["filter"].items()):
        clauses.append(f"{key} IS ?" if value is None else f"{key}=?")
        params.append(value)
    return " AND ".join(clauses), params


def _scope(
    leaf: Mapping[str, Any], where: str, params: list[Any],
    tenant_id: str, business_id: str | None,
) -> tuple[str, list[Any]]:
    params = list(params)
    params[0] = tenant_id
    _, columns = SOURCE_TABLES[leaf["table"]]
    if "business_id" in columns and business_id is not None:
        where += " AND business_id=?"
        params.append(business_id)
    return where, params


def _leaf_sql(
    leaf: Mapping[str, Any], start: str, end: str, tenant_id: str,
    business_id: str | None,
) -> tuple[str, list[Any]]:
    table = leaf["table"]
    where, params = _scope(
        leaf, *_where(leaf, start, end), tenant_id, business_id
    )
    op = leaf["op"]
    if op == "count":
        select = "COUNT(*)"
    elif op == "sum":
        select = f"COALESCE(SUM({leaf['field']}), 0)"
    elif op == "avg":
        select = f"AVG({leaf['field']})"
    else:  # pragma: no cover - validated earlier
        raise BusinessError(METRIC_FORMULA_INVALID_CODE, f"unsupported op {op!r}")
    return f"SELECT {select} FROM {table} WHERE {where}", params


def _id_sql(
    leaf: Mapping[str, Any], start: str, end: str, tenant_id: str,
    business_id: str | None, limit: int,
) -> tuple[str, list[Any]]:
    table = leaf["table"]
    id_column, columns = SOURCE_TABLES[table]
    where, params = _scope(
        leaf, *_where(leaf, start, end), tenant_id, business_id
    )
    return (
        f"SELECT {id_column}, created_at FROM {table} WHERE {where}"
        " ORDER BY created_at, " + id_column + " LIMIT ?",
        params + [limit],
    )


def _leaf_count(
    conn: sqlite3.Connection,
    leaf: Mapping[str, Any],
    start: str,
    end: str,
    tenant_id: str,
    business_id: str | None,
) -> int:
    where, params = _scope(
        leaf, *_where(leaf, start, end), tenant_id, business_id
    )
    row = conn.execute(
        "SELECT COUNT(*) FROM " + leaf["table"] + " WHERE " + where, params
    ).fetchone()
    return int(row[0] or 0)


def _evaluate_leaf(
    conn: sqlite3.Connection,
    leaf: Mapping[str, Any],
    start: str,
    end: str,
    tenant_id: str,
    business_id: str | None,
) -> tuple[float | None, int]:
    count = _leaf_count(conn, leaf, start, end, tenant_id, business_id)
    if count == 0:
        return None, 0
    sql, params = _leaf_sql(leaf, start, end, tenant_id, business_id)
    value = conn.execute(sql, params).fetchone()[0]
    return (None if value is None else float(value)), count


def _collect_rows(
    conn: sqlite3.Connection,
    leaves: Sequence[Mapping[str, Any]],
    start: str,
    end: str,
    tenant_id: str,
    business_id: str | None,
    limit: int,
) -> tuple[list[dict[str, Any]], bool, int]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    truncated = False
    total = 0
    for leaf in leaves:
        sql, params = _id_sql(leaf, start, end, tenant_id, business_id, limit + 1)
        fetched = conn.execute(sql, params).fetchall()
        total += _leaf_count(conn, leaf, start, end, tenant_id, business_id)
        for row in fetched:
            key = (leaf["table"], str(row[0]))
            if key in seen:
                continue
            seen.add(key)
            if len(rows) >= limit:
                truncated = True
                continue
            rows.append(
                {"table": leaf["table"], "row_id": str(row[0]),
                 "created_at": row[1]}
            )
        if len(fetched) > limit:
            truncated = True
    return rows, truncated, total


def _leaves(formula: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    if formula["op"] == "ratio":
        return _leaves(formula["numerator"]) + _leaves(formula["denominator"])
    return [formula]


def _compute_formula(
    conn: sqlite3.Connection,
    formula: Mapping[str, Any],
    start: str,
    end: str,
    tenant_id: str,
    business_id: str | None,
) -> tuple[float | None, str | None]:
    if formula["op"] == "ratio":
        numerator, _ = _compute_formula(
            conn, formula["numerator"], start, end, tenant_id, business_id
        )
        denominator, _ = _compute_formula(
            conn, formula["denominator"], start, end, tenant_id, business_id
        )
        if denominator is None or denominator == 0:
            return None, "denominator_zero"
        # An empty numerator is a real zero (0 of N), not an unknown.
        return (numerator or 0.0) / denominator, None
    value, count = _evaluate_leaf(
        conn, formula, start, end, tenant_id, business_id
    )
    if count == 0 or value is None:
        return None, "no_source_rows"
    return value, None


def compute(
    store: "BusinessStore",
    tenant_id: str,
    metric_id: str,
    *,
    as_of: str | None = None,
    now: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    contract = get_contract(store, tenant_id, metric_id)
    moment = now or now_iso()
    as_of_iso = as_of or moment
    as_of_dt = datetime.fromisoformat(as_of_iso.replace("Z", "+00:00"))
    now_dt = datetime.fromisoformat(moment.replace("Z", "+00:00"))
    if as_of_dt > now_dt:
        return _result(
            contract, None, "unknown", "as_of_in_future", as_of_iso, moment,
            [], 0, False,
        )
    start, end = _window(as_of_iso, contract["attribution_window_seconds"])
    with store.read() as conn:
        _ensure(conn)
        if not _table_exists(conn, "bos_metric_contract"):
            raise BusinessError(
                METRIC_NOT_FOUND_CODE, f"no metric {metric_id!r} in tenant"
            )
        for table in contract["source_tables"]:
            if not _table_exists(conn, table):
                return _result(
                    contract, None, "unknown", "source_table_missing",
                    as_of_iso, moment, [], 0, False,
                )
        leaves = _leaves(contract["formula"])
        value, reason = _compute_formula(
            conn, contract["formula"], start, end, tenant_id,
            contract["business_id"],
        )
        rows, truncated, count = _collect_rows(
            conn, leaves, start, end, tenant_id, contract["business_id"],
            max(1, int(limit)),
        )
        newest = None
        for leaf in leaves:
            where, params = _scope(
                leaf, *_where(leaf, start, end), tenant_id,
                contract["business_id"],
            )
            sql = "SELECT MAX(created_at) FROM " + leaf["table"] + " WHERE " + where
            candidate = conn.execute(sql, params).fetchone()[0]
            if candidate is not None and (newest is None or candidate > newest):
                newest = candidate
    if value is None and reason == "no_source_rows" and contract["allow_empty"]:
        value = 0.0
        reason = None
    if value is None:
        status = "unknown"
        code = reason or "no_source_rows"
    else:
        code = None
        if newest is None:
            status = "stale"
            code = "no_newer_than_window"
        else:
            age = max(
                0.0,
                (now_dt - datetime.fromisoformat(
                    newest.replace("Z", "+00:00")
                )).total_seconds(),
            )
            if age > contract["acceptable_staleness_seconds"]:
                status = "stale"
                code = "data_older_than_acceptable_staleness"
            else:
                status = "fresh"
    return _result(
        contract, value, status, code, as_of_iso, moment, rows, count,
        truncated,
    )


def _result(
    contract: Mapping[str, Any],
    value: float | None,
    status: str,
    reason: str | None,
    as_of: str,
    computed_at: str,
    rows: list[dict[str, Any]],
    source_count: int,
    truncated: bool,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema": SCHEMA,
        "metric_id": contract["metric_id"],
        "name": contract["name"],
        "status": status,
        "reason": reason,
        "value": value,
        "as_of": as_of,
        "computed_at": computed_at,
        "window": {
            "seconds": contract["attribution_window_seconds"],
            "timezone": contract["timezone"],
            "start": _window(as_of, contract["attribution_window_seconds"])[0],
            "end": as_of,
        },
        "methodology": contract["methodology"],
        "derived": contract["methodology"] == "derived",
        "derivation_note": contract["derivation_note"],
        "target": contract["target"],
        "anomaly_threshold": contract["anomaly_threshold"],
        "source_tables": contract["source_tables"],
        "source_rows": rows,
        "source_count": source_count,
        "truncated": truncated,
        "drillable": True,
        "refresh_cadence_seconds": contract["refresh_cadence_seconds"],
        "acceptable_staleness_seconds": contract["acceptable_staleness_seconds"],
        "owner": contract["owner"],
    }
    if value is not None and contract["target"] is not None and (
        contract["anomaly_threshold"] is not None
    ):
        result["anomaly"] = (
            abs(value - contract["target"]) > contract["anomaly_threshold"]
        )
    else:
        result["anomaly"] = None
    return result


def drill(
    store: "BusinessStore",
    tenant_id: str,
    metric_id: str,
    *,
    table: str,
    row_id: str,
) -> dict[str, Any]:
    contract = get_contract(store, tenant_id, metric_id)
    if table not in contract["source_tables"]:
        raise BusinessError(
            METRIC_SOURCE_UNKNOWN_CODE,
            f"table {table!r} is not a source of metric {metric_id!r}",
        )
    id_column, columns = SOURCE_TABLES[table]
    query = "SELECT * FROM " + table + " WHERE tenant_id=? AND " + id_column + "=?"
    params: list[Any] = [tenant_id, row_id]
    if "business_id" in columns and contract["business_id"] is not None:
        query += " AND business_id=?"
        params.append(contract["business_id"])
    with store.read() as conn:
        row = conn.execute(query, params).fetchone()
    if row is None:
        raise BusinessError(
            METRIC_NOT_FOUND_CODE,
            f"no source row {row_id!r} for metric {metric_id!r}",
        )
    return {
        "table": table,
        "row_id": row_id,
        "row": {column: row[column] for column in columns},
    }


def record_snapshot(
    store: "BusinessStore",
    tenant_id: str,
    metric_id: str,
    *,
    result: Mapping[str, Any],
) -> dict[str, Any]:
    """Persist a computed result; used by verifiers/projections, not compute."""
    contract = get_contract(store, tenant_id, metric_id)
    if result.get("metric_id") != metric_id:
        raise BusinessError(
            METRIC_SNAPSHOT_MISMATCH_CODE,
            "the snapshot names a different metric",
        )
    status = result.get("status")
    if status not in STATUSES:
        raise BusinessError(METRIC_BAD_INPUT_CODE, f"unknown status {status!r}")
    snapshot_id = new_id("snp")
    with store.transaction() as conn:
        _ensure(conn)
        conn.execute(
            "INSERT INTO bos_metric_snapshot(snapshot_id, tenant_id, metric_id,"
            " computed_at, as_of, status, value, methodology,"
            " source_row_ids_json, note) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                snapshot_id,
                tenant_id,
                metric_id,
                str(result.get("computed_at") or now_iso()),
                str(result.get("as_of") or now_iso()),
                status,
                result.get("value"),
                contract["methodology"],
                json_dumps(result.get("source_rows") or []),
                str(result.get("reason") or "")[:2000],
            ),
        )
    return {
        "snapshot_id": snapshot_id,
        "metric_id": metric_id,
        "status": status,
        "value": result.get("value"),
        "methodology": contract["methodology"],
    }


def snapshot_matches(
    store: "BusinessStore", tenant_id: str, metric_id: str
) -> bool:
    """Verify every stored snapshot's value against a fresh computation.

    Used by the independent verifier to detect a fabricated persisted number.
    """
    contract = get_contract(store, tenant_id, metric_id)
    with store.read() as conn:
        rows = conn.execute(
            "SELECT * FROM bos_metric_snapshot WHERE tenant_id=? AND metric_id=?"
            " ORDER BY computed_at, snapshot_id",
            (tenant_id, metric_id),
        ).fetchall()
    for row in rows:
        fresh = compute(
            store,
            tenant_id,
            metric_id,
            as_of=row["as_of"],
            now=row["computed_at"],
        )
        if row["status"] == "unknown" or fresh["status"] == "unknown":
            if row["status"] != fresh["status"]:
                return False
            continue
        if row["value"] != fresh["value"]:
            return False
    return contract["metric_id"] == metric_id

"""Unified background-task ledger (read-only read model).

Behavioral adaptation of the Qubit background task ledger
(``src/tasks/`` + ``docs/automation/tasks.md``, pinned inventory row #30 in
``docs/archive/2026-09-21/AUDIT_OPENCLAW_2026-09-11.md``). No upstream source is copied; this is
a Qubit-native read model.

It introduces NO store and NO writer. It only reads the existing Qubit run
histories and normalizes them into one stable ledger schema:

- ``opencode``  the append-only action receipts (``actions.json``) whose
  ``adapter`` is ``opencode`` — one row per supervised coding run;
- ``schedules`` the ``runs`` table of the native job store (``schedules.db``)
  — one row per scheduled execution, joined to its job for title/kind;
- ``kanban``    local task-board tasks (``kanban.db``) — one row per task.

Reads are best-effort and side-effect free: a missing store is an empty
source, an unreadable store is recorded as ``TASK_LEDGER_UNAVAILABLE`` (never
a fabricated row), and ``status`` never raises. Output is redacted with the
same ``voicebridge.redaction`` choke point every sibling module uses.
"""
from __future__ import annotations

import datetime as _dt
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

SCHEMA = "qubit-task-ledger/1"

SOURCES = ("opencode", "schedules", "kanban")

TASK_NOT_FOUND = "TASK_NOT_FOUND"
TASK_LEDGER_UNAVAILABLE = "TASK_LEDGER_UNAVAILABLE"
TASK_SOURCE_UNKNOWN = "TASK_SOURCE_UNKNOWN"
TASK_BAD_INPUT = "TASK_BAD_INPUT"

# Shared, stable state vocabulary the merged ledger sorts/filters on. The
# source's own state survives in each row's ``source_state``.
CANONICAL_STATES = ("running", "ok", "failed", "blocked", "cancelled",
                    "pending", "unknown")

MAX_LIMIT = 200          # per-query soft cap (dashboard/CLI page size)
ABSURD_LIMIT = 100_000   # anything above this is a caller bug, not a bound
READ_CAP = 1000          # most-recent rows read per source before merging

_OPENCODE_PREFIX = "act.opencode."
_OPENCODE_OK = {"ok", "partial", "recovered", "rolled_back",
                "idempotent_replay"}
_OPENCODE_FAIL = {"failed", "timeout", "expired", "stale_target", "replayed",
                  "interrupted", "denied", "adapter_disabled",
                  "rollback_impossible", "auth_required"}
_SCHEDULE_OK = {"OK", "SENT"}
_SCHEDULE_FAIL = {"FAILED", "UNAVAILABLE"}
_KANBAN = {"open": "pending", "claimed": "running", "blocked": "blocked",
           "done": "ok", "archived": "ok"}


class TaskLedgerError(ValueError):
    """Typed ledger failure (bad caller input; never a traceback)."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


# ---------------------------------------------------------------------------
# cfg / redaction helpers
# ---------------------------------------------------------------------------
def _cfg_get(cfg: Any, *path: str, default: Any = None) -> Any:
    try:
        return cfg.get(*path, default=default)
    except Exception:
        return default


def _state_dir(cfg: Any) -> Optional[Path]:
    try:
        return Path(cfg.state_dir())
    except Exception:
        pass
    raw = _cfg_get(cfg, "logging", "jsonl_path", default="")
    return Path(str(raw)) if raw else None


def _redact(text: Any) -> str:
    from .redaction import redact_text
    return redact_text(str(text or ""))


def _redact_obj(obj: Any) -> Any:
    from .redaction import redact_obj
    return redact_obj(obj)


def _bounded(text: Any, limit: int) -> str:
    return _redact(text)[:max(1, int(limit))]


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _canonical(source: str, raw: Any) -> str:
    text = str(raw or "").strip()
    if source == "opencode":
        low = text.lower()
        if low in _OPENCODE_OK:
            return "ok"
        if low in _OPENCODE_FAIL:
            return "failed"
        if low == "running":
            return "running"
        if low == "cancelled":
            return "cancelled"
        if low in ("preview", "prepared"):
            return "pending"
        return "unknown"
    if source == "schedules":
        up = text.upper()
        if up == "RUNNING":
            return "running"
        if up in _SCHEDULE_OK:
            return "ok"
        if up in _SCHEDULE_FAIL:
            return "failed"
        if up in ("", "PENDING"):
            return "pending"
        return "unknown"
    if source == "kanban":
        return _KANBAN.get(text.lower(), "unknown")
    return "unknown"


# ---------------------------------------------------------------------------
# Source readers (read-only; missing store => empty; unreadable => typed)
# ---------------------------------------------------------------------------
def _opencode_path(cfg: Any) -> Optional[Path]:
    raw = _cfg_get(cfg, "actions", "state_path", default="")
    if raw:
        return Path(str(raw)).expanduser()
    sd = _state_dir(cfg)
    return (sd / "actions.json") if sd else None


def _opencode_row(rid: str, rec: Dict[str, Any]) -> Dict[str, Any]:
    action = str(rec.get("action_id") or "")
    kind = action[len(_OPENCODE_PREFIX):] if action.startswith(
        _OPENCODE_PREFIX) else (action or "run")
    created = _as_float(rec.get("created_at"))
    finished = rec.get("finished_at")
    updated = _as_float(finished) if finished else created
    target = str(rec.get("target") or "")
    title = _bounded(rec.get("preview") or target or action or rid, 200)
    title = title.strip().splitlines()[0][:200] if title else (action or rid)
    return {"id": f"opencode:{rid}", "source": "opencode", "kind": kind,
            "state": _canonical("opencode", rec.get("state")),
            "source_state": str(rec.get("state") or ""),
            "title": title, "created_at": created, "updated_at": updated,
            "correlation_id": str(rec.get("correlation_id") or ""),
            "evidence_ref": f"actions.json#{rid}",
            "code": str(rec.get("code") or "")[:80],
            "detail": _bounded(rec.get("detail"), 300),
            "native_id": rid, "target": _bounded(target, 200)}


def _read_opencode(cfg: Any) -> Tuple[List[Dict[str, Any]], Optional[Dict]]:
    path = _opencode_path(cfg)
    if path is None or not path.is_file():
        return [], None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [], {"source": "opencode", "code": TASK_LEDGER_UNAVAILABLE,
                    "detail": f"receipt store unreadable: {str(exc)[:160]}"}
    recs = data.get("receipts", {}) if isinstance(data, dict) else {}
    if not isinstance(recs, dict):
        recs = {}
    rows = [_opencode_row(str(rid), rec) for rid, rec in recs.items()
            if isinstance(rec, dict) and rec.get("adapter") == "opencode"]
    return rows, None


def _sqlite_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def _schedules_path(cfg: Any) -> Optional[Path]:
    sd = _state_dir(cfg)
    return (sd / "schedules.db") if sd else None


def _schedules_row(r: sqlite3.Row) -> Dict[str, Any]:
    run_id = int(r["run_id"])
    job_id = int(r["job_id"]) if r["job_id"] is not None else 0
    started = _as_float(r["started_at"])
    finished = r["finished_at"]
    updated = _as_float(finished) if finished else started
    title = _bounded(r["job_name"] or r["task_text"], 200)
    return {"id": f"schedules:{run_id}", "source": "schedules",
            "kind": str(r["job_kind"] or "once"),
            "state": _canonical("schedules", r["state"]),
            "source_state": str(r["state"] or ""),
            "title": title, "created_at": started, "updated_at": updated,
            "correlation_id": f"schedule:{job_id}",
            "evidence_ref": f"schedules.db#runs/{run_id}",
            "code": str(r["code"] or "")[:80],
            "detail": _bounded(r["detail"], 300),
            "native_id": str(run_id), "job_id": job_id,
            "task": _bounded(r["task_text"], 300)}


def _read_schedules(cfg: Any) -> Tuple[List[Dict[str, Any]], Optional[Dict]]:
    path = _schedules_path(cfg)
    if path is None or not path.is_file():
        return [], None
    query = ("SELECT r.id AS run_id, r.job_id, r.started_at, r.finished_at,"
             " r.state, r.code, r.detail, j.name AS job_name,"
             " j.task_text, j.kind AS job_kind FROM runs r"
             " LEFT JOIN jobs j ON j.id=r.job_id"
             " ORDER BY r.id DESC LIMIT ?")
    try:
        conn = _sqlite_ro(path)
        try:
            rows = conn.execute(query, (READ_CAP,)).fetchall()
        finally:
            conn.close()
    except (sqlite3.Error, OSError) as exc:
        return [], {"source": "schedules", "code": TASK_LEDGER_UNAVAILABLE,
                    "detail": f"schedule store unreadable: {str(exc)[:160]}"}
    return [_schedules_row(r) for r in rows], None


def _kanban_path(cfg: Any) -> Optional[Path]:
    sd = _state_dir(cfg)
    return (sd / "kanban.db") if sd else None


def _kanban_row(r: sqlite3.Row) -> Dict[str, Any]:
    tid = int(r["id"])
    return {"id": f"kanban:{tid}", "source": "kanban", "kind": "task",
            "state": _canonical("kanban", r["status"]),
            "source_state": str(r["status"] or ""),
            "title": _bounded(r["title"], 200),
            "created_at": _as_float(r["created_at"]),
            "updated_at": _as_float(r["updated_at"]),
            "correlation_id": str(r["board"] or ""),
            "evidence_ref": f"kanban.db#tasks/{tid}",
            "code": "", "detail": _bounded(r["body"], 300),
            "native_id": str(tid), "board": str(r["board"] or ""),
            "assignee": _bounded(r["assignee"], 80)}


def _read_kanban(cfg: Any) -> Tuple[List[Dict[str, Any]], Optional[Dict]]:
    path = _kanban_path(cfg)
    if path is None or not path.is_file():
        return [], None
    query = ("SELECT id, board, title, body, status, assignee, created_at,"
             " updated_at FROM tasks ORDER BY id DESC LIMIT ?")
    try:
        conn = _sqlite_ro(path)
        try:
            rows = conn.execute(query, (READ_CAP,)).fetchall()
        finally:
            conn.close()
    except (sqlite3.Error, OSError) as exc:
        return [], {"source": "kanban", "code": TASK_LEDGER_UNAVAILABLE,
                    "detail": f"kanban store unreadable: {str(exc)[:160]}"}
    return [_kanban_row(r) for r in rows], None


_READERS = {"opencode": _read_opencode, "schedules": _read_schedules,
            "kanban": _read_kanban}


def _collect(cfg: Any, sources: Iterable[str]
             ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    rows: List[Dict[str, Any]] = []
    unavailable: List[Dict[str, Any]] = []
    for source in sources:
        try:
            got, err = _READERS[source](cfg)
        except Exception as exc:  # a read model must never crash the caller
            got, err = [], {"source": source, "code": TASK_LEDGER_UNAVAILABLE,
                            "detail": f"{type(exc).__name__}: {str(exc)[:160]}"}
        rows.extend(got)
        if err:
            unavailable.append(err)
    return rows, unavailable


def _sort_key(row: Dict[str, Any]) -> Tuple[float, str, str]:
    return (_as_float(row.get("updated_at") or row.get("created_at")),
            str(row.get("source") or ""), str(row.get("id") or ""))


# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------
def _validate_sources(source: Any) -> Tuple[str, ...]:
    if source is None or source == "":
        return SOURCES
    items = source if isinstance(source, (list, tuple, set)) else [source]
    out: List[str] = []
    for item in items:
        name = str(item or "").strip().lower()
        if name not in SOURCES:
            raise TaskLedgerError(
                TASK_SOURCE_UNKNOWN,
                f"unknown source {item!r}; expected one of {', '.join(SOURCES)}")
        if name not in out:
            out.append(name)
    return tuple(out)


def _validate_limit(limit: Any) -> int:
    if isinstance(limit, bool):
        raise TaskLedgerError(TASK_BAD_INPUT, "limit must be a positive integer")
    if isinstance(limit, str):
        try:
            limit = int(limit.strip())
        except ValueError:
            raise TaskLedgerError(
                TASK_BAD_INPUT, "limit must be a positive integer") from None
    if not isinstance(limit, int) or limit < 1 or limit > ABSURD_LIMIT:
        raise TaskLedgerError(
            TASK_BAD_INPUT,
            f"limit {limit!r} out of range 1..{ABSURD_LIMIT}")
    return min(limit, MAX_LIMIT)


def _parse_since(since: Any) -> Optional[float]:
    if since is None or since == "":
        return None
    if isinstance(since, bool):
        raise TaskLedgerError(TASK_BAD_INPUT, "since must be an epoch or ISO time")
    if isinstance(since, (int, float)):
        return float(since)
    text = str(since).strip()
    try:
        return float(text)
    except ValueError:
        pass
    try:
        return _dt.datetime.fromisoformat(
            text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        raise TaskLedgerError(
            TASK_BAD_INPUT,
            f"since {since!r} is neither an epoch nor ISO-8601") from None


def _split_id(task_id: Any, source: Any) -> Tuple[str, Optional[str]]:
    if source is not None and source != "":
        name = _validate_sources(source)[0]
        key = str(task_id)
        prefix = f"{name}:"
        return (key[len(prefix):] if key.startswith(prefix) else key), name
    text = str(task_id)
    if ":" in text:
        prefix, native = text.split(":", 1)
        if prefix in SOURCES:
            return native, prefix
    return text, None


def _matches(row: Dict[str, Any], key: str, source: Optional[str]) -> bool:
    if source is not None and row.get("source") != source:
        return False
    return key in (str(row.get("native_id")),
                   str(row.get("id")),
                   str(row.get("id", "")).split(":", 1)[-1])


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def status(cfg: Any) -> Dict[str, Any]:
    """Ledger counts by source and canonical state. Never raises."""
    try:
        rows, unavailable = _collect(cfg, SOURCES)
    except Exception as exc:  # pragma: no cover - defence in depth
        return {"schema": SCHEMA, "state": "DEGRADED",
                "code": TASK_LEDGER_UNAVAILABLE,
                "detail": f"{type(exc).__name__}: {str(exc)[:160]}",
                "total": 0, "sources": {s: 0 for s in SOURCES},
                "states": {s: 0 for s in CANONICAL_STATES},
                "by_source": {s: {} for s in SOURCES},
                "unavailable": [], "generated_at": time.time()}
    sources = {s: 0 for s in SOURCES}
    states = {s: 0 for s in CANONICAL_STATES}
    by_source: Dict[str, Dict[str, int]] = {s: {} for s in SOURCES}
    for row in rows:
        src = str(row.get("source"))
        st = str(row.get("state"))
        sources[src] = sources.get(src, 0) + 1
        states[st] = states.get(st, 0) + 1
        by_source.setdefault(src, {})[st] = \
            by_source.setdefault(src, {}).get(st, 0) + 1
    return {"schema": SCHEMA,
            "state": "DEGRADED" if unavailable else "OK",
            "total": len(rows), "sources": sources, "states": states,
            "by_source": by_source, "unavailable": unavailable,
            "generated_at": time.time()}


def list_tasks(cfg: Any, *, source: Any = None, state: Any = None,
               limit: Any = 50, since: Any = None) -> Dict[str, Any]:
    """Merge normalized tasks across the real stores, newest-first.

    ``source`` accepts one name or a collection (``opencode``/``schedules``/
    ``kanban``); ``state`` filters on the canonical vocabulary. ``limit`` is
    capped at :data:`MAX_LIMIT`; absurd values are rejected as
    ``TASK_BAD_INPUT``. ``since`` is an epoch or ISO-8601 timestamp compared
    against the row's ``updated_at``.
    """
    sources = _validate_sources(source)
    bounded = _validate_limit(limit)
    cutoff = _parse_since(since)
    rows, unavailable = _collect(cfg, sources)
    if state is not None:
        wanted = str(state).strip().lower()
        if wanted not in CANONICAL_STATES:
            raise TaskLedgerError(
                TASK_BAD_INPUT,
                f"unknown state {state!r}; expected one of "
                f"{', '.join(CANONICAL_STATES)}")
        rows = [r for r in rows if r["state"] == wanted]
    if cutoff is not None:
        rows = [r for r in rows
                if _as_float(r.get("updated_at") or r.get("created_at")) >= cutoff]
    rows.sort(key=_sort_key, reverse=True)
    matched = len(rows)
    page = rows[:bounded]
    return {"schema": SCHEMA, "state": "OK", "tasks": page,
            "count": len(page), "total_matched": matched, "limit": bounded,
            "capped": matched > bounded, "sources": list(sources),
            "unavailable": unavailable, "generated_at": time.time()}


def _raw_record(cfg: Any, row: Dict[str, Any]) -> Dict[str, Any]:
    source = row["source"]
    native = str(row["native_id"])
    if source == "opencode":
        path = _opencode_path(cfg)
        if path is not None and path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                rec = (data.get("receipts") or {}).get(native)
                if isinstance(rec, dict):
                    return rec
            except (OSError, ValueError):
                pass
        return {"receipt_id": native, "unavailable": True}
    if source == "schedules":
        path = _schedules_path(cfg)
        if path is not None and path.is_file():
            try:
                conn = _sqlite_ro(path)
                try:
                    rec = conn.execute(
                        "SELECT * FROM runs WHERE id=?", (int(native),)
                    ).fetchone()
                    job_id = rec["job_id"] if rec is not None else None
                    job = conn.execute(
                        "SELECT * FROM jobs WHERE id=?",
                        (job_id,)).fetchone() if job_id else None
                finally:
                    conn.close()
                if rec is not None:
                    return {"run": dict(rec),
                            "job": dict(job) if job is not None else {}}
            except (sqlite3.Error, OSError, ValueError):
                pass
        return {"run_id": native, "unavailable": True}
    path = _kanban_path(cfg)
    if path is not None and path.is_file():
        try:
            conn = _sqlite_ro(path)
            try:
                rec = conn.execute(
                    "SELECT * FROM tasks WHERE id=?", (int(native),)
                ).fetchone()
            finally:
                conn.close()
            if rec is not None:
                return dict(rec)
        except (sqlite3.Error, OSError, ValueError):
            pass
    return {"id": native, "unavailable": True}


def _not_found(task_id: Any, source: Optional[str],
               unavailable: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"schema": SCHEMA, "state": "NOT_FOUND", "code": TASK_NOT_FOUND,
            "task_id": str(task_id), "source": source,
            "unavailable": unavailable}


def show(cfg: Any, task_id: Any, *, source: Any = None) -> Dict[str, Any]:
    """One normalized task plus its redacted raw source record.

    ``task_id`` may be the ledger id (``<source>:<native>``) or a native id
    with an explicit ``source``. Unknown ids return ``TASK_NOT_FOUND`` rather
    than raising; an unknown ``source`` raises ``TASK_SOURCE_UNKNOWN``.
    """
    key, src = _split_id(task_id, source)
    rows, unavailable = _collect(cfg, (src,) if src else SOURCES)
    match = next((r for r in rows if _matches(r, key, src)), None)
    if match is None:
        return _not_found(task_id, src, unavailable)
    return {"schema": SCHEMA, "state": "OK", "task": match,
            "raw": _redact_obj(_raw_record(cfg, match)),
            "source": match["source"]}


# ---------------------------------------------------------------------------
# Timeline (only real, source-backed events; never fabricated)
# ---------------------------------------------------------------------------
def _kanban_timeline(cfg: Any, native: str) -> List[Dict[str, Any]]:
    path = _kanban_path(cfg)
    if path is None or not path.is_file():
        return []
    conn = _sqlite_ro(path)
    try:
        events = conn.execute(
            "SELECT kind, detail, created_at FROM events WHERE task_id=?"
            " ORDER BY id", (int(native),)).fetchall()
        comments = conn.execute(
            "SELECT author, body, created_at FROM comments WHERE task_id=?"
            " ORDER BY id", (int(native),)).fetchall()
    finally:
        conn.close()
    out = [{"kind": str(r["kind"]), "detail": _bounded(r["detail"], 300),
            "at": _as_float(r["created_at"]), "origin": "kanban.event"}
           for r in events]
    out += [{"kind": "comment", "detail": _bounded(r["body"], 300),
             "at": _as_float(r["created_at"]), "origin": "kanban.comment",
             "author": _bounded(r["author"], 80)} for r in comments]
    out.sort(key=lambda e: e["at"])
    return out


def _schedules_timeline(cfg: Any, native: str) -> List[Dict[str, Any]]:
    path = _schedules_path(cfg)
    if path is None or not path.is_file():
        return []
    conn = _sqlite_ro(path)
    try:
        run = conn.execute("SELECT job_id FROM runs WHERE id=?",
                           (int(native),)).fetchone()
        job_id = run["job_id"] if run is not None else None
        runs = conn.execute(
            "SELECT id, started_at, finished_at, state, code, detail FROM runs"
            " WHERE job_id=? ORDER BY id", (job_id,)).fetchall() if job_id \
            else []
    finally:
        conn.close()
    return [{"kind": "run", "at": _as_float(r["started_at"]),
             "finished_at": _as_float(r["finished_at"]),
             "state": _canonical("schedules", r["state"]),
             "source_state": str(r["state"] or ""),
             "code": str(r["code"] or "")[:80],
             "detail": _bounded(r["detail"], 300),
             "origin": "schedules.run", "run_id": int(r["id"])}
            for r in runs]


def _opencode_timeline(cfg: Any, native: str,
                       row: Dict[str, Any]) -> List[Dict[str, Any]]:
    path = _opencode_path(cfg)
    if path is None or not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    recs = data.get("receipts", {}) if isinstance(data, dict) else {}
    if not isinstance(recs, dict):
        return []
    corr = str(row.get("correlation_id") or "")
    key = str((recs.get(native) or {}).get("idempotency_key") or "")
    related = []
    for rid, rec in recs.items():
        if not isinstance(rec, dict) or rec.get("adapter") != "opencode":
            continue
        if rid == native or (corr and rec.get("correlation_id") == corr) \
                or (key and rec.get("idempotency_key") == key):
            related.append((_as_float(rec.get("created_at")), rid, rec))
    related.sort()
    return [{"kind": "receipt", "at": created,
             "finished_at": _as_float(rec.get("finished_at")),
             "state": _canonical("opencode", rec.get("state")),
             "source_state": str(rec.get("state") or ""),
             "code": str(rec.get("code") or "")[:80],
             "detail": _bounded(rec.get("detail"), 300),
             "origin": "opencode.receipt", "receipt_id": rid}
            for created, rid, rec in related]


def timeline(cfg: Any, task_id: Any, *, source: Any = None) -> Dict[str, Any]:
    """Ordered, real events correlated to one task.

    Kanban and schedule stores carry ordered event/run histories, so their
    timeline is ``OK``. OpenCode persists only a terminal receipt (no event
    stream), so its timeline is returned as typed ``DEGRADED`` with the real
    correlated receipts attached — never a fabricated sequence.
    """
    found = show(cfg, task_id, source=source)
    if found.get("state") == "NOT_FOUND":
        return found
    task = found["task"]
    src = task["source"]
    native = str(task["native_id"])
    try:
        if src == "kanban":
            events = _kanban_timeline(cfg, native)
        elif src == "schedules":
            events = _schedules_timeline(cfg, native)
        else:
            events = _opencode_timeline(cfg, native, task)
    except Exception as exc:
        return {"schema": SCHEMA, "state": "DEGRADED",
                "code": TASK_LEDGER_UNAVAILABLE, "task_id": task["id"],
                "source": src, "events": [],
                "detail": f"{type(exc).__name__}: {str(exc)[:160]}"}
    out = {"schema": SCHEMA, "state": "OK", "task_id": task["id"],
           "source": src, "correlation_id": task["correlation_id"],
           "evidence_ref": task["evidence_ref"], "events": events,
           "count": len(events)}
    if src == "opencode":
        out["state"] = "DEGRADED"
        out["code"] = TASK_LEDGER_UNAVAILABLE
        out["detail"] = ("opencode action receipts hold a terminal record "
                         "only; no ordered event stream is available")
    return out


__all__ = ["SCHEMA", "SOURCES", "CANONICAL_STATES", "TaskLedgerError",
           "TASK_NOT_FOUND", "TASK_LEDGER_UNAVAILABLE", "TASK_SOURCE_UNKNOWN",
           "TASK_BAD_INPUT", "MAX_LIMIT", "status", "list_tasks", "show",
           "timeline"]

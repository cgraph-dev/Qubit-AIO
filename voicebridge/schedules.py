"""Packet 8U — Qubit-native scheduled jobs (user tasks on a schedule).

SQLite-backed job store under the Qubit state dir: create/list/show/
pause/resume/delete plus ``run_due`` execution with per-job typed
results and a retained run history. Creation, pause/resume toggles, and
deletion require explicit confirmation; ``run_due`` executes only jobs
the user already authorized at creation time (each run is audited with
its job id, and the emergency stop latches all execution).

Scheduling authority: this module holds NO timer, thread, daemon, or
autonomous loop — jobs run only when ``run_due`` is invoked explicitly
(CLI/dashboard/voice) or by the operator's existing monitor/systemd
timer. The monitor remains the sole autonomous scheduler; there is one
scheduling authority, not two.

Hermes implements cron jobs, a scheduler, executions, and a ledger
(``cron/jobs.py``, ``cron/scheduler*.py``, ``cron/executions.py``,
``cron/ledger.py`` at the pinned ``v2026.9.7 @ 2237be3`` revision);
Qubit implements the user-facing scheduling contract natively with a
narrower scope (interval/daily/once, no detached workers, no prompt
rewriting). Provenance and the deliberate divergence are recorded in
``docs/UPSTREAM_REUSE.md``.
"""
from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

CONFIRM_CODE = "SCHEDULES_CONFIRM_REQUIRED"
ESTOP_CODE = "SCHEDULES_ESTOP"
NOT_FOUND_CODE = "SCHEDULES_NOT_FOUND"
BAD_INPUT_CODE = "SCHEDULES_BAD_INPUT"
IO_CODE = "SCHEDULES_IO"
RUNTIME_CODE = "SCHEDULES_RUNTIME_DISABLED"

MAX_NAME_LEN = 80
MAX_TASK_LEN = 2000
MIN_INTERVAL_MIN = 5
MAX_INTERVAL_MIN = 10080  # one week
LIST_MAX = 100

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  task_text TEXT NOT NULL,
  kind TEXT NOT NULL,
  interval_min INTEGER NOT NULL DEFAULT 0,
  hour INTEGER NOT NULL DEFAULT -1,
  minute INTEGER NOT NULL DEFAULT 0,
  at_ts REAL NOT NULL DEFAULT 0,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_by TEXT NOT NULL DEFAULT '',
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  last_run_at REAL NOT NULL DEFAULT 0,
  last_state TEXT NOT NULL DEFAULT '',
  run_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER NOT NULL,
  started_at REAL NOT NULL,
  finished_at REAL NOT NULL DEFAULT 0,
  state TEXT NOT NULL DEFAULT 'RUNNING',
  code TEXT NOT NULL DEFAULT '',
  detail TEXT NOT NULL DEFAULT ''
);
"""


class SchedulesError(Exception):
    """Typed schedules failure (never a traceback on the CLI)."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def redact_spec(text: Any) -> str:
    try:
        from .redaction import redact_text as _redact
        return _redact(str(text or ""))[:300]
    except Exception:
        return "?"


def estop_engaged(cfg: Any) -> bool:
    """Same durable latch probe as the plugins surface (fail closed)."""
    from .plugins import estop_engaged as _engaged

    return _engaged(cfg)


def refuse_estop(operation: str) -> Dict[str, Any]:
    """Typed refusal while the emergency stop is latched."""
    return {"state": "denied", "code": ESTOP_CODE,
            "detail": f"emergency stop latched; {operation} refused",
            "owner": "qubit-policy",
            "rollback": "schedule store untouched; nothing to roll back"}


def _now() -> float:
    return time.time()


def _db_path(cfg: Any) -> Path:
    return Path(cfg.state_dir()) / "schedules.db"


def _connect(cfg: Any) -> sqlite3.Connection:
    try:
        path = _db_path(cfg)
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        return conn
    except OSError as exc:
        raise SchedulesError(IO_CODE, f"schedule store unavailable: {exc}")


def _migrate(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    if "updated_at" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN updated_at REAL "
                     "NOT NULL DEFAULT 0")


def _audit(op: str, actor: str, detail: Dict[str, Any]) -> None:
    try:
        from .logging_setup import log_event
        log_event(f"schedules.{op}", actor=actor,
                  **{k: str(v)[:160] for k, v in detail.items()})
    except Exception:
        pass


def _job_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {"id": row["id"], "name": row["name"],
            "task_text": row["task_text"][:500], "kind": row["kind"],
            "interval_min": row["interval_min"], "hour": row["hour"],
            "minute": row["minute"], "at_ts": row["at_ts"],
            "enabled": bool(row["enabled"]),
            "created_by": row["created_by"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "last_run_at": row["last_run_at"],
            "last_state": row["last_state"],
            "run_count": row["run_count"]}


def _check_name(name: str) -> str:
    text = str(name or "").strip()
    if not text or len(text) > MAX_NAME_LEN:
        raise SchedulesError(BAD_INPUT_CODE,
                             "schedule name is required (<=80 chars)")
    return text


def _check_task(task_text: str) -> str:
    text = str(task_text or "").strip()
    if not text or len(text) > MAX_TASK_LEN:
        raise SchedulesError(BAD_INPUT_CODE,
                             "task text is required (<=2000 chars)")
    return text


_EVERY_RE = re.compile(
    r"^\s*every\s+(\d{1,4})\s*(minute|minutes|min|mins|hour|hours|hr|hrs)\s*$",
    re.IGNORECASE)
_DAILY_RE = re.compile(
    r"^\s*(daily|every day)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*$",
    re.IGNORECASE)
_ONCE_RE = re.compile(r"^\s*once\s+in\s+(\d{1,4})\s*(minutes?|mins?|hours?|hrs?)\s*$",
                      re.IGNORECASE)

# Unanchored variants for splitting "task ... <when>" utterances: the last
# match wins so "remind me every day to check every log line" keeps the
# trailing schedule phrase.
_EVERY_FIND = re.compile(
    r"\bevery\s+(\d{1,4})\s*(minute|minutes|min|mins|hour|hours|hr|hrs)\b",
    re.IGNORECASE)
_DAILY_FIND = re.compile(
    r"\b(daily|every day)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b",
    re.IGNORECASE)
_ONCE_FIND = re.compile(
    r"\bonce\s+in\s+(\d{1,4})\s*(minutes?|mins?|hours?|hrs?)\b",
    re.IGNORECASE)


def extract_when(text: str) -> tuple:
    """Split ``"<task> <when>"`` into (task_part, when_part|None).

    The trailing schedule phrase (last match) becomes the spec; whatever
    precedes it is the task text. Pure utility for the voice layer.
    """
    rest = str(text or "").strip()
    best = None
    for finder in (_EVERY_FIND, _DAILY_FIND, _ONCE_FIND):
        for found in finder.finditer(rest):
            if best is None or found.end() >= best.end():
                best = found
    if best is None:
        return rest, None
    when = rest[best.start():best.end()].strip()
    task = (rest[:best.start()] + " " + rest[best.end():]).strip()
    task = re.sub(r"\s+", " ", task).strip(" ,.:;-")
    return task, when


def parse_when(spec: str, *, now: Optional[float] = None) -> Dict[str, Any]:
    """Parse a schedule phrase into a job spec dict.

    Accepts ``every N minutes|hours``, ``daily HH[:MM][am|pm]``, and
    ``once in N minutes|hours``. Anything else raises BAD_INPUT with the
    accepted shapes (voice turns this into a clarification question).
    """
    text = str(spec or "").strip()
    if not text:
        raise SchedulesError(
            BAD_INPUT_CODE,
            "when should it run? say 'every 30 minutes', "
            "'daily 9am', or 'once in 10 minutes'")
    found = _EVERY_RE.match(text)
    if found:
        amount = int(found.group(1))
        unit = found.group(2).lower()
        minutes = amount * 60 if unit.startswith(("hour", "hr")) else amount
        if not (MIN_INTERVAL_MIN <= minutes <= MAX_INTERVAL_MIN):
            raise SchedulesError(
                BAD_INPUT_CODE,
                f"interval must be {MIN_INTERVAL_MIN}..{MAX_INTERVAL_MIN} "
                f"minutes")
        return {"kind": "interval", "interval_min": minutes}
    found = _DAILY_RE.match(text)
    if found:
        hour = int(found.group(2))
        minute = int(found.group(3) or 0)
        meridiem = (found.group(4) or "").lower()
        if meridiem == "pm" and hour < 12:
            hour += 12
        if meridiem == "am" and hour == 12:
            hour = 0
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise SchedulesError(BAD_INPUT_CODE,
                                 "daily time must be HH[:MM] with 0<=HH<=23")
        return {"kind": "daily", "hour": hour, "minute": minute}
    found = _ONCE_RE.match(text)
    if found:
        amount = int(found.group(1))
        unit = found.group(2).lower()
        minutes = amount * 60 if unit.startswith("hour") else amount
        if not (1 <= minutes <= MAX_INTERVAL_MIN):
            raise SchedulesError(BAD_INPUT_CODE,
                                 "once-in delay must be 1..10080 minutes")
        base = now if now is not None else _now()
        return {"kind": "once", "at_ts": base + minutes * 60.0}
    raise SchedulesError(
        BAD_INPUT_CODE,
        f"unknown schedule {text[:60]!r}: say 'every 30 minutes', "
        f"'daily 9am', or 'once in 10 minutes'")


def _due(job: Dict[str, Any], now: float) -> bool:
    if not job["enabled"]:
        return False
    kind = job["kind"]
    if kind == "interval":
        base = job["last_run_at"] or job["created_at"]
        return now >= base + job["interval_min"] * 60.0
    if kind == "daily":
        import datetime as _dt
        day_start = now - (now % 86400.0)
        target = day_start + job["hour"] * 3600.0 + job["minute"] * 60.0
        if target > now:
            target -= 86400.0
        return job["last_run_at"] < target <= now
    if kind == "once":
        return job["last_run_at"] <= 0 < job["at_ts"] <= now
    return False


# ------------------------------------------------------------------ CRUD
def create(cfg: Any, name: str, task_text: str, when: str, *,
           requested_by: str = "cli:user",
           confirmed: bool = False) -> Dict[str, Any]:
    """Stage a scheduled job (confirmation required: unattended work)."""
    if not confirmed:
        raise SchedulesError(CONFIRM_CODE,
                             "scheduling unattended work requires explicit "
                             "--confirm")
    clean_name = _check_name(name)
    clean_task = _check_task(task_text)
    spec = parse_when(when)
    conn = _connect(cfg)
    try:
        _migrate(conn)
        cur = conn.execute(
            "INSERT INTO jobs (name, task_text, kind, interval_min, hour, "
            "minute, at_ts, enabled, created_by, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (clean_name, clean_task, spec["kind"],
             int(spec.get("interval_min", 0)), int(spec.get("hour", -1)),
             int(spec.get("minute", 0)), float(spec.get("at_ts", 0.0)),
             1, requested_by, _now(), _now()))
        conn.commit()
        job_id = int(cur.lastrowid)
        row = conn.execute("SELECT * FROM jobs WHERE id=?",
                           (job_id,)).fetchone()
        _audit("create", requested_by,
               {"job_id": str(job_id), "kind": spec["kind"]})
        return {"state": "OK", "job": _job_dict(row)}
    finally:
        conn.close()


def list_jobs(cfg: Any, *, include_disabled: bool = True,
              limit: int = 50) -> Dict[str, Any]:
    """List jobs newest-first (read-only, bounded)."""
    limit = max(1, min(int(limit or 50), LIST_MAX))
    conn = _connect(cfg)
    try:
        _migrate(conn)
        query = "SELECT * FROM jobs"
        if not include_disabled:
            query += " WHERE enabled=1"
        query += " ORDER BY id DESC LIMIT ?"
        rows = conn.execute(query, (limit,)).fetchall()
        return {"state": "OK",
                "jobs": [_job_dict(r) for r in rows], "limit": limit}
    finally:
        conn.close()


def _get(conn: sqlite3.Connection, job_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM jobs WHERE id=?",
                       (int(job_id),)).fetchone()
    if row is None:
        raise SchedulesError(NOT_FOUND_CODE, f"no schedule #{job_id}")
    return row


def show(cfg: Any, job_id: int) -> Dict[str, Any]:
    """Show one job with its recent runs (read-only)."""
    conn = _connect(cfg)
    try:
        _migrate(conn)
        row = _get(conn, job_id)
        runs = conn.execute(
            "SELECT * FROM runs WHERE job_id=? ORDER BY id DESC LIMIT 10",
            (row["id"],)).fetchall()
        job = _job_dict(row)
        job["recent_runs"] = [
            {"id": r["id"], "started_at": r["started_at"],
             "finished_at": r["finished_at"], "state": r["state"],
             "code": r["code"], "detail": r["detail"][:300]}
            for r in runs]
        return {"state": "OK", "job": job}
    finally:
        conn.close()


def _set_enabled(cfg: Any, job_id: int, enabled: bool, *,
                 requested_by: str, confirmed: bool,
                 op: str) -> Dict[str, Any]:
    if not confirmed:
        raise SchedulesError(CONFIRM_CODE,
                             f"schedule {op} requires explicit --confirm")
    conn = _connect(cfg)
    try:
        _migrate(conn)
        _get(conn, job_id)
        conn.execute("UPDATE jobs SET enabled=?, updated_at=? WHERE id=?",
                     (1 if enabled else 0, _now(), int(job_id)))
        conn.commit()
        _audit(op, requested_by, {"job_id": str(int(job_id))})
        return {"state": "OK", "job": _job_dict(
            _get(conn, job_id))}
    finally:
        conn.close()


def pause(cfg: Any, job_id: int, *, requested_by: str = "cli:user",
          confirmed: bool = False) -> Dict[str, Any]:
    """Pause a job (confirmation required)."""
    return _set_enabled(cfg, job_id, False, requested_by=requested_by,
                        confirmed=confirmed, op="pause")


def resume(cfg: Any, job_id: int, *, requested_by: str = "cli:user",
           confirmed: bool = False) -> Dict[str, Any]:
    """Resume a job (confirmation required)."""
    return _set_enabled(cfg, job_id, True, requested_by=requested_by,
                        confirmed=confirmed, op="resume")


def delete(cfg: Any, job_id: int, *, requested_by: str = "cli:user",
           confirmed: bool = False) -> Dict[str, Any]:
    """Delete a job (confirmation required); run history is retained."""
    if not confirmed:
        raise SchedulesError(CONFIRM_CODE,
                             "schedule delete requires explicit --confirm")
    conn = _connect(cfg)
    try:
        _migrate(conn)
        row = _get(conn, job_id)
        job = _job_dict(row)
        conn.execute("DELETE FROM jobs WHERE id=?", (int(job_id),))
        conn.commit()
        _audit("delete", requested_by, {"job_id": str(int(job_id))})
        return {"state": "OK", "deleted": job["id"],
                "history_retained": True}
    finally:
        conn.close()


def history(cfg: Any, job_id: int = 0, limit: int = 30) -> Dict[str, Any]:
    """Run history across jobs (or one job); retained after delete."""
    limit = max(1, min(int(limit or 30), LIST_MAX))
    conn = _connect(cfg)
    try:
        _migrate(conn)
        if job_id:
            rows = conn.execute(
                "SELECT * FROM runs WHERE job_id=? ORDER BY id DESC LIMIT ?",
                (int(job_id), limit)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM runs ORDER BY id DESC LIMIT ?",
                (limit,)).fetchall()
        return {"state": "OK", "runs": [
            {"id": r["id"], "job_id": r["job_id"],
             "started_at": r["started_at"], "finished_at": r["finished_at"],
             "state": r["state"], "code": r["code"],
             "detail": r["detail"][:300]} for r in rows]}
    finally:
        conn.close()


# --------------------------------------------------------------- execution
def _default_executor(task_text: str, *, session: str,
                      estop_engaged: Callable[[], bool]) -> Dict[str, Any]:
    """Dispatch one due job through the agent runtime (same runtime the
    CLI ``task`` path prefers). Typed unavailable states, never fake OK."""
    from .runtime.real_hermes import RealHermesRuntime, RealHermesDenied

    if estop_engaged():
        return {"state": "UNAVAILABLE", "code": "EMERGENCY_STOP",
                "detail": "emergency stop latched"}
    rt = RealHermesRuntime(estop_engaged=estop_engaged)
    try:
        res = rt.turn(task_text, session=session)
    except RealHermesDenied as exc:
        msg = str(exc)
        code = "EMERGENCY_STOP" if "EMERGENCY_STOP" in msg else \
            "RUNTIME_DISABLED"
        return {"state": "UNAVAILABLE", "code": code,
                "detail": msg[:200]}
    if not isinstance(res, dict) or not res.get("ok"):
        return {"state": "UNAVAILABLE",
                "code": str((res or {}).get("code", "RUNTIME_ERROR")),
                "detail": str((res or {}).get("error", ""))[:200]}
    return {"state": "OK", "code": "OK",
            "detail": str(res.get("reply", ""))[:300]}


def run_due(cfg: Any, *, requested_by: str = "cli:user",
            executor: Optional[Callable[..., Dict[str, Any]]] = None,
            now: Optional[float] = None,
            estop_engaged: Optional[Callable[[], bool]] = None,
            limit: int = 10) -> Dict[str, Any]:
    """Execute due jobs oldest-first (bounded). Each job result is a typed
    state recorded in history; a failing job never blocks later jobs, and
    nothing is fabricated when the runtime is unprovisioned."""
    from .plugins import estop_engaged as _engaged

    moment = now if now is not None else _now()
    stop = estop_engaged or (lambda: bool(_engaged(cfg)))
    run = executor or _default_executor
    conn = _connect(cfg)
    try:
        _migrate(conn)
        conn.commit()
        # Atomic claim phase: exactly one process wins each due job
        # (BEGIN IMMEDIATE serializes concurrent run-due callers; the
        # last_run_at compare-and-swap is the lease).
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            "SELECT * FROM jobs WHERE enabled=1 ORDER BY id").fetchall()
        claimed: List[Dict[str, Any]] = []
        for row in rows:
            job = _job_dict(row)
            if not _due(job, moment):
                continue
            if len(claimed) >= max(1, min(int(limit or 10), 25)):
                break
            cur = conn.execute(
                "UPDATE jobs SET last_run_at=?, updated_at=? "
                "WHERE id=? AND last_run_at=?",
                (moment, moment, job["id"], job["last_run_at"]))
            if cur.rowcount != 1:
                continue  # lost the race: another runner claimed it
            run_cur = conn.execute(
                "INSERT INTO runs (job_id, started_at) VALUES (?,?)",
                (job["id"], _now()))
            claimed.append((job, int(run_cur.lastrowid)))
        conn.commit()
        results: List[Dict[str, Any]] = []
        for job, run_id in claimed:
            try:
                outcome = run(job["task_text"], session=f"schedule-{job['id']}",
                              estop_engaged=stop)
            except Exception as exc:
                outcome = {"state": "FAILED", "code": type(exc).__name__,
                           "detail": str(exc)[:200]}
            state = str(outcome.get("state", "FAILED"))
            code = str(outcome.get("code", "") or state)
            conn.execute(
                "UPDATE runs SET finished_at=?, state=?, code=?, detail=? "
                "WHERE id=?",
                (_now(), state, code[:80],
                 str(outcome.get("detail", ""))[:500], run_id))
            conn.execute(
                "UPDATE jobs SET last_state=?, run_count=?, updated_at=? "
                "WHERE id=?",
                (state, job["run_count"] + 1, _now(), job["id"]))
            conn.commit()
            _audit("run", requested_by,
                   {"job_id": str(job["id"]), "state": state, "code": code})
            results.append({"job_id": job["id"], "name": job["name"],
                            "state": state, "code": code})
        return {"state": "OK", "ran": len(results), "results": results}
    finally:
        conn.close()

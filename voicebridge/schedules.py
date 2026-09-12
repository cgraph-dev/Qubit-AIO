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
scheduling authority, not two. ``autofire`` is the bounded, fail-closed
entry point that existing restart-safe monitor loop calls once per tick;
it adds no authority of its own and is a thin wrapper over ``run_due``.

Delivery: a job may declare one or more delivery targets at creation time
(``channel:<account>[:<recipient>]`` or ``webhook:<sub_id>``, comma
separated). After a run completes, the bounded, redacted result is handed
to the target(s) through the EXISTING transports — the messaging outbox
(``messaging.draft``/``messaging.send``) for channels and a delegated
webhook transport for subscriptions — so this module owns no transport of
its own. Configuration is stored only (nothing is sent at create time,
and the confirming user already authorized the target). Delivery is
estop-aware, audited (references only), idempotent (one delivery row per
run+target; the messaging idempotency key is keyed by the run), and
fail-closed: a target with no credential/transport records a typed
``UNAVAILABLE`` (never a fabricated ``SENT``). A delivery outcome lives
in its own table and can never corrupt the run receipt or later jobs.

Qubit implements cron jobs, a scheduler, executions, and a ledger
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

# The monitor's self-fire tick actor (audit attribution) and the hard cap on
# jobs any single tick may claim, so a backlog can never monopolize a pass.
MONITOR_ACTOR = "monitor:autofire"
AUTOFIRE_LIMIT = 5

# Delivery codes: a scheduled result is only ever reported SENT when a real
# transport confirmed it. Missing credentials/adapters and a latched e-stop
# are typed UNAVAILABLE, never a fabricated success.
DELIVERY_SENT_CODE = "SCHEDULE_DELIVERY_SENT"
DELIVERY_UNAVAILABLE_CODE = "SCHEDULE_DELIVERY_UNAVAILABLE"
DELIVERY_FAILED_CODE = "SCHEDULE_DELIVERY_FAILED"
DELIVERY_ESTOP_CODE = "SCHEDULE_DELIVERY_ESTOP"

MAX_TARGETS = 5
MAX_TARGET_LEN = 400
MAX_DELIVERY_BODY = 1000
MAX_DELIVERY_DETAIL = 200

# Optional outbound webhook transport (delegated). Qubit ships no outbound
# webhook POST authority; when no adapter is installed a webhook target is a
# typed UNAVAILABLE. A future adapter can be injected here (or per call)
# without this module growing a second transport. Signature:
#   transport(cfg, subscription, payload) -> {"state": "OK"|...}
_WEBHOOK_DELIVER: Optional[Callable[..., Dict[str, Any]]] = None

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
  run_count INTEGER NOT NULL DEFAULT 0,
  deliver_to TEXT NOT NULL DEFAULT ''
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
-- One row per (run, target): the PRIMARY KEY is the idempotency guarantee,
-- so a delivery attempt is recorded exactly once per run+target.
CREATE TABLE IF NOT EXISTS deliveries (
  run_id INTEGER NOT NULL,
  job_id INTEGER NOT NULL,
  target TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'PENDING',
  code TEXT NOT NULL DEFAULT '',
  detail TEXT NOT NULL DEFAULT '',
  attempted_at REAL NOT NULL DEFAULT 0,
  PRIMARY KEY (run_id, target)
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


def _redact_bounded(text: Any, limit: int = MAX_DELIVERY_DETAIL) -> str:
    """Redact then bound; used for any delivery payload/receipt text."""
    try:
        from .redaction import redact_text as _redact
        return _redact(str(text or ""))[:max(1, int(limit))]
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
    if "deliver_to" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN deliver_to TEXT "
                     "NOT NULL DEFAULT ''")


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
            "run_count": row["run_count"],
            "deliver_to": row["deliver_to"]}


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


def _missed_windows(job: Dict[str, Any], now: float) -> int:
    """Coalesced catch-up count: scheduled windows passed while not firing.

    Qubit never replays a backlog unboundedly; a job that missed one or more
    windows while the process was down fires exactly once on the next tick
    and reports here how many windows were missed, so the receipt is honest
    about the downtime without letting a backlog monopolize a tick.
    """
    base = float(job.get("last_run_at") or job.get("created_at") or 0.0)
    if base <= 0:
        return 0
    kind = job["kind"]
    if kind == "interval":
        step = float(job["interval_min"]) * 60.0
        if step <= 0:
            return 0
        elapsed = now - base
        if elapsed < step:
            return 0
        return max(0, int(elapsed // step) - 1)
    if kind == "daily":
        elapsed = now - base
        if elapsed < 86400.0:
            return 0
        return max(0, int(elapsed // 86400.0) - 1)
    return 0


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


# --------------------------------------------------------- delivery config
def _parse_target(raw: str) -> Dict[str, str]:
    text = str(raw or "").strip()
    if not text or len(text) > MAX_TARGET_LEN:
        raise SchedulesError(BAD_INPUT_CODE,
                             "delivery target must be 1..400 chars")
    kind, _, rest = text.partition(":")
    kind, rest = kind.strip().lower(), rest.strip()
    if kind == "webhook":
        if not rest:
            raise SchedulesError(BAD_INPUT_CODE,
                                 "webhook target needs a subscription id")
        return {"kind": "webhook", "sub": rest}
    if kind == "channel":
        account, _, recipient = rest.partition(":")
        account, recipient = account.strip(), recipient.strip()
        if not account or not recipient:
            raise SchedulesError(
                BAD_INPUT_CODE,
                "channel target is channel:<account>:<recipient>")
        return {"kind": "channel", "account": account, "to": recipient}
    raise SchedulesError(
        BAD_INPUT_CODE,
        f"unknown delivery target {text[:60]!r}: use "
        "channel:<account>:<recipient> or webhook:<sub_id>")


def _target_key(target: Dict[str, str]) -> str:
    if target.get("kind") == "channel":
        return f"channel:{target.get('account', '')}:{target.get('to', '')}"
    return f"webhook:{target.get('sub', '')}"


def parse_delivery_spec(deliver_to: Any) -> List[Dict[str, str]]:
    """Parse a stored delivery spec into bounded target dicts."""
    text = str(deliver_to or "").strip()
    if not text:
        return []
    parts = [p for p in re.split(r"[,;]", text) if p.strip()]
    if len(parts) > MAX_TARGETS:
        raise SchedulesError(BAD_INPUT_CODE,
                             f"at most {MAX_TARGETS} delivery targets")
    return [_parse_target(p) for p in parts]


def _check_targets(cfg: Any, targets: List[Dict[str, str]]) -> str:
    """Config-time validation only: the referenced account/subscription must
    exist. Transport/credential availability is deliberately not probed (the
    confirming user stores config; an absent transport surfaces as a typed
    UNAVAILABLE at run time, never a fake send). Returns the canonical spec."""
    if not targets:
        return ""
    from . import messaging as M
    from . import webhooks as W

    for target in targets:
        if target["kind"] == "channel":
            try:
                M.account_show(cfg, target["account"])
            except M.MessagingError as exc:
                raise SchedulesError(
                    BAD_INPUT_CODE,
                    f"delivery account {target['account']!r} unavailable "
                    f"({exc.code})") from None
            except Exception:
                raise SchedulesError(
                    BAD_INPUT_CODE,
                    f"delivery account {target['account']!r} "
                    "unavailable") from None
        else:
            try:
                W.show(cfg, target["sub"])
            except W.WebhookError as exc:
                raise SchedulesError(
                    BAD_INPUT_CODE,
                    f"delivery subscription {target['sub']!r} unavailable "
                    f"({exc.code})") from None
            except Exception:
                raise SchedulesError(
                    BAD_INPUT_CODE,
                    f"delivery subscription {target['sub']!r} "
                    "unavailable") from None
    return ",".join(_target_key(t) for t in targets)


# ------------------------------------------------------------------ CRUD
def create(cfg: Any, name: str, task_text: str, when: str, *,
           deliver_to: str = "", requested_by: str = "cli:user",
           confirmed: bool = False) -> Dict[str, Any]:
    """Stage a scheduled job (confirmation required: unattended work).

    ``deliver_to`` names zero or more delivery targets to receive the run's
    bounded, redacted result: ``channel:<account>:<recipient>`` and/or
    ``webhook:<sub_id>``, comma separated. Only configuration is stored here
    (the reference must already exist); nothing is delivered at create time.
    """
    if not confirmed:
        raise SchedulesError(CONFIRM_CODE,
                             "scheduling unattended work requires explicit "
                             "--confirm")
    clean_name = _check_name(name)
    clean_task = _check_task(task_text)
    spec = parse_when(when)
    canonical = _check_targets(cfg, parse_delivery_spec(deliver_to))
    conn = _connect(cfg)
    try:
        _migrate(conn)
        cur = conn.execute(
            "INSERT INTO jobs (name, task_text, kind, interval_min, hour, "
            "minute, at_ts, enabled, created_by, created_at, updated_at, "
            "deliver_to) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (clean_name, clean_task, spec["kind"],
             int(spec.get("interval_min", 0)), int(spec.get("hour", -1)),
             int(spec.get("minute", 0)), float(spec.get("at_ts", 0.0)),
             1, requested_by, _now(), _now(), canonical))
        conn.commit()
        job_id = int(cur.lastrowid)
        row = conn.execute("SELECT * FROM jobs WHERE id=?",
                           (job_id,)).fetchone()
        _audit("create", requested_by,
               {"job_id": str(job_id), "kind": spec["kind"],
                "deliver_to": canonical})
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


# --------------------------------------------------------------- delivery
def _delivery_body(job: Dict[str, Any], outcome: Dict[str, Any]) -> str:
    """Bounded, redacted body for one run (references only in head)."""
    head = (f"[schedule #{job.get('id')} {job.get('name', '')}] "
            f"{outcome.get('state', '')}:")
    detail = _redact_bounded(outcome.get("detail", ""), MAX_DELIVERY_BODY)
    return _redact_bounded(f"{head} {detail}", MAX_DELIVERY_BODY)


def _record_delivery(cfg: Any, conn: sqlite3.Connection, job: Dict[str, Any],
                     run_id: int, target: str, state: str, code: str,
                     detail: str, requested_by: str) -> Dict[str, Any]:
    """Persist one delivery outcome (idempotent) + a bodyless audit event."""
    clean = _redact_bounded(detail, MAX_DELIVERY_DETAIL)
    conn.execute(
        "INSERT OR IGNORE INTO deliveries "
        "(run_id, job_id, target, state, code, detail, attempted_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (run_id, job["id"], target, state, code[:80], clean, _now()))
    conn.commit()
    _audit("deliver", requested_by,
           {"job_id": str(job["id"]), "run_id": str(run_id),
            "target": target[:120], "state": state, "code": code[:80]})
    return {"target": target, "state": state, "code": code[:80],
            "detail": clean}


def _msg_unavailable(M: Any) -> set:
    return {M.NO_CREDENTIAL_CODE, M.UNAVAILABLE_CODE, M.NO_TRANSPORT_CODE,
            M.NOT_PAIRED_CODE, M.ESTOP_CODE}


def _find_outbox_msg(cfg: Any, key: str) -> str:
    from . import messaging as M

    try:
        out = M.list_msgs(cfg)
    except Exception:
        return ""
    for rec in out.get("messages", []):
        if rec.get("idempotency_key") == key:
            return str(rec.get("id", ""))
    return ""


def _deliver_channel(cfg: Any, conn: sqlite3.Connection, job: Dict[str, Any],
                     run_id: int, target: Dict[str, str], body: str,
                     tkey: str, requested_by: str) -> Dict[str, Any]:
    """Deliver through the EXISTING messaging outbox (draft -> send)."""
    from . import messaging as M

    account = target["account"]
    # Per-run idempotency: a crash/replay can never create a second send.
    idem = f"schedule-{job['id']}-run-{run_id}"
    try:
        msg_id = M.draft(cfg, account, target.get("to", ""), body,
                         idempotency_key=idem, requested_by=requested_by,
                         confirmed=True)["message"]["id"]
    except M.MessagingError as exc:
        if exc.code == M.EXISTS_CODE:
            msg_id = _find_outbox_msg(cfg, idem)
            if not msg_id:
                return _record_delivery(
                    cfg, conn, job, run_id, tkey, "UNAVAILABLE",
                    DELIVERY_UNAVAILABLE_CODE,
                    "idempotency key held by an unknown message",
                    requested_by)
        else:
            state = ("UNAVAILABLE" if exc.code in _msg_unavailable(M)
                     else "FAILED")
            code = (exc.code if state == "UNAVAILABLE"
                    else DELIVERY_FAILED_CODE)
            return _record_delivery(cfg, conn, job, run_id, tkey, state,
                                    code, exc.detail, requested_by)
    except Exception as exc:  # noqa: BLE001 — delivery never corrupts a run
        return _record_delivery(cfg, conn, job, run_id, tkey, "FAILED",
                                DELIVERY_FAILED_CODE, str(exc), requested_by)
    try:
        sent = M.send(cfg, msg_id, requested_by=requested_by, confirmed=True)
    except M.MessagingError as exc:
        state = ("UNAVAILABLE" if exc.code in _msg_unavailable(M)
                 else "FAILED")
        code = (exc.code if state == "UNAVAILABLE" else DELIVERY_FAILED_CODE)
        return _record_delivery(cfg, conn, job, run_id, tkey, state, code,
                                exc.detail, requested_by)
    except Exception as exc:  # noqa: BLE001
        return _record_delivery(cfg, conn, job, run_id, tkey, "FAILED",
                                DELIVERY_FAILED_CODE, str(exc), requested_by)
    confirmed = (isinstance(sent, dict)
                 and sent.get("message", {}).get("state") == M.SENT)
    if not confirmed:
        return _record_delivery(cfg, conn, job, run_id, tkey, "FAILED",
                                DELIVERY_FAILED_CODE,
                                "transport did not confirm the send",
                                requested_by)
    return _record_delivery(cfg, conn, job, run_id, tkey, "SENT",
                            DELIVERY_SENT_CODE,
                            f"delivered via account {account}", requested_by)


def _deliver_webhook(cfg: Any, conn: sqlite3.Connection, job: Dict[str, Any],
                     run_id: int, target: Dict[str, str], body: str,
                     tkey: str, requested_by: str) -> Dict[str, Any]:
    """Deliver via a delegated webhook transport; typed UNAVAILABLE if the
    store is disabled or no outbound adapter is installed (Qubit ships no
    outbound webhook POST authority of its own)."""
    from . import webhooks as W

    sub = target["sub"]
    try:
        enabled = W.is_enabled(cfg)
        sub_rec = W.show(cfg, sub)["subscription"]
    except W.WebhookError as exc:
        state = ("UNAVAILABLE" if exc.code in {W.ESTOP_CODE, W.AUTH_CODE,
                 W.CORRUPT_CODE} else "FAILED")
        code = exc.code if state == "UNAVAILABLE" else DELIVERY_FAILED_CODE
        return _record_delivery(cfg, conn, job, run_id, tkey, state, code,
                                exc.detail, requested_by)
    except Exception as exc:  # noqa: BLE001
        return _record_delivery(cfg, conn, job, run_id, tkey, "FAILED",
                                DELIVERY_FAILED_CODE, str(exc), requested_by)
    if not enabled:
        return _record_delivery(cfg, conn, job, run_id, tkey, "UNAVAILABLE",
                                DELIVERY_UNAVAILABLE_CODE,
                                "webhook delivery is disabled", requested_by)
    deliver = _WEBHOOK_DELIVER
    if deliver is None:
        return _record_delivery(
            cfg, conn, job, run_id, tkey, "UNAVAILABLE",
            DELIVERY_UNAVAILABLE_CODE,
            "no outbound webhook transport adapter exists; subscription "
            "resolved, nothing sent", requested_by)
    payload = {"job_id": job["id"], "run_id": run_id,
               "name": job.get("name", ""), "body": body, "target": sub}
    try:
        result = deliver(cfg, sub_rec, payload)
    except Exception as exc:  # noqa: BLE001
        return _record_delivery(cfg, conn, job, run_id, tkey, "FAILED",
                                DELIVERY_FAILED_CODE, str(exc), requested_by)
    state = str((result or {}).get("state", "")).upper()
    if state in ("OK", "SENT"):
        return _record_delivery(cfg, conn, job, run_id, tkey, "SENT",
                                DELIVERY_SENT_CODE,
                                f"delivered to webhook {sub}", requested_by)
    if state == "UNAVAILABLE":
        return _record_delivery(
            cfg, conn, job, run_id, tkey, "UNAVAILABLE",
            str((result or {}).get("code", DELIVERY_UNAVAILABLE_CODE)),
            str((result or {}).get("detail",
                                   "webhook transport unavailable")),
            requested_by)
    return _record_delivery(
        cfg, conn, job, run_id, tkey, "FAILED", DELIVERY_FAILED_CODE,
        str((result or {}).get("detail", "webhook transport failed")),
        requested_by)


def _deliver_one(cfg: Any, conn: sqlite3.Connection, job: Dict[str, Any],
                 run_id: int, outcome: Dict[str, Any],
                 target: Dict[str, str], estop: Callable[[], bool],
                 requested_by: str) -> Dict[str, Any]:
    tkey = _target_key(target)
    existing = conn.execute(
        "SELECT state, code FROM deliveries WHERE run_id=? AND target=?",
        (run_id, tkey)).fetchone()
    if existing is not None:
        return {"target": tkey, "state": existing["state"],
                "code": existing["code"], "duplicate": True}
    if estop():
        return _record_delivery(cfg, conn, job, run_id, tkey, "UNAVAILABLE",
                                DELIVERY_ESTOP_CODE,
                                "emergency stop latched; delivery skipped",
                                requested_by)
    body = _delivery_body(job, outcome)
    if target["kind"] == "channel":
        return _deliver_channel(cfg, conn, job, run_id, target, body, tkey,
                                requested_by)
    return _deliver_webhook(cfg, conn, job, run_id, target, body, tkey,
                            requested_by)


def _deliver_result(cfg: Any, conn: sqlite3.Connection, job: Dict[str, Any],
                    run_id: int, outcome: Dict[str, Any],
                    estop: Callable[[], bool], requested_by: str) -> Dict[str, Any]:
    """Hand a finished run's redacted result to every configured target.

    Never raises into the run loop: the durable run receipt is committed
    before this runs, and each target gets an independent typed outcome.
    """
    try:
        targets = parse_delivery_spec(job.get("deliver_to", ""))
    except SchedulesError as exc:
        return {"state": "UNAVAILABLE", "code": exc.code, "delivered": 0,
                "targets": [{"target": "", "state": "UNAVAILABLE",
                             "code": exc.code,
                             "detail": redact_spec(exc.detail)}]}
    if not targets:
        return {"state": "NONE", "code": "", "delivered": 0, "targets": []}
    per: List[Dict[str, Any]] = []
    for target in targets:
        try:
            per.append(_deliver_one(cfg, conn, job, run_id, outcome, target,
                                    estop, requested_by))
        except Exception as exc:  # noqa: BLE001 — never corrupt a run
            per.append({"target": _target_key(target), "state": "FAILED",
                        "code": DELIVERY_FAILED_CODE,
                        "detail": redact_spec(str(exc))})
    states = [p["state"] for p in per]
    sent = sum(1 for s in states if s == "SENT")
    if sent == len(states):
        overall = "SENT"
    elif sent:
        overall = "PARTIAL"
    elif any(s == "UNAVAILABLE" for s in states):
        overall = "UNAVAILABLE"
    else:
        overall = "FAILED"
    return {"state": overall, "code": "", "delivered": sent, "targets": per}


def delivery_history(cfg: Any, job_id: int = 0,
                     limit: int = 30) -> Dict[str, Any]:
    """Delivery receipts (bounded), retained independently of run history."""
    limit = max(1, min(int(limit or 30), LIST_MAX))
    conn = _connect(cfg)
    try:
        _migrate(conn)
        if job_id:
            rows = conn.execute(
                "SELECT * FROM deliveries WHERE job_id=? "
                "ORDER BY run_id DESC LIMIT ?",
                (int(job_id), limit)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM deliveries ORDER BY run_id DESC LIMIT ?",
                (limit,)).fetchall()
        return {"state": "OK", "deliveries": [
            {"run_id": r["run_id"], "job_id": r["job_id"],
             "target": r["target"], "state": r["state"], "code": r["code"],
             "detail": r["detail"][:MAX_DELIVERY_DETAIL],
             "attempted_at": r["attempted_at"]} for r in rows]}
    finally:
        conn.close()


# --------------------------------------------------------------- execution
def _default_executor(task_text: str, *, session: str,
                      estop_engaged: Callable[[], bool]) -> Dict[str, Any]:
    """Dispatch one due job through the Qubit-native agent (same agent the
    CLI ``task`` path uses). Typed unavailable states, never fake OK."""
    from .agent import AgentDenied, turn as agent_turn

    if estop_engaged():
        return {"state": "UNAVAILABLE", "code": "EMERGENCY_STOP",
                "detail": "emergency stop latched"}
    try:
        res = agent_turn(None, task_text, session=session,
                         estop_engaged=estop_engaged)
    except AgentDenied as exc:
        msg = str(exc)
        code = "EMERGENCY_STOP" if "EMERGENCY_STOP" in msg else \
            "AGENT_DISABLED"
        return {"state": "UNAVAILABLE", "code": code,
                "detail": msg[:200]}
    if not isinstance(res, dict) or not res.get("ok"):
        return {"state": "UNAVAILABLE",
                "code": str((res or {}).get("code", "AGENT_ERROR")),
                "detail": str((res or {}).get("error", ""))[:200]}
    return {"state": "OK", "code": "OK",
            "detail": str(res.get("reply", ""))[:300]}


def _directive_task(task_text: Any) -> bool:
    """True when a job carries a user automation directive (never raises)."""
    try:
        from .automation import is_directive

        return bool(is_directive(task_text))
    except Exception:  # noqa: BLE001 — a broken import never dispatches
        return False


def _run_automation_directive(cfg: Any, task_text: str, *,
                              estop_engaged: Callable[[], bool],
                              now: Optional[float]) -> Dict[str, Any]:
    """Dispatch a scheduled automation directive (fail-closed, never raises)."""
    from .automation import run_directive

    out = run_directive(cfg, task_text, estop=estop_engaged, now=now,
                        requested_by=MONITOR_ACTOR)
    if not isinstance(out, dict):
        return {"state": "FAILED", "code": RUNTIME_CODE,
                "detail": "automation directive produced no result"}
    return out


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
            catch_up = _missed_windows(job, moment)
            try:
                if _directive_task(job["task_text"]):
                    # A user-authored automation directive runs on the same
                    # one scheduler via the real automation dispatcher; it is
                    # pre-authorized by the (confirmed) job, estop-aware, and
                    # never reaches the agent.
                    outcome = _run_automation_directive(cfg, job["task_text"],
                                                        estop_engaged=stop,
                                                        now=moment)
                else:
                    outcome = run(job["task_text"],
                                  session=f"schedule-{job['id']}",
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
                   {"job_id": str(job["id"]), "state": state, "code": code,
                    "catch_up": str(catch_up)})
            # Delivery is downstream of a durable run receipt: it may fail,
            # degrade, or be unavailable without touching the run outcome.
            delivery = _deliver_result(cfg, conn, job, run_id, outcome, stop,
                                       requested_by)
            results.append({"job_id": job["id"], "name": job["name"],
                            "state": state, "code": code,
                            "catch_up": catch_up,
                            "delivery": delivery})
        return {"state": "OK", "ran": len(results), "results": results,
                "catch_up": sum(int(r.get("catch_up", 0)) for r in results)}
    finally:
        conn.close()


def scheduler_status(cfg: Any, *, now: Optional[float] = None) -> Dict[str, Any]:
    """Read-only scheduler posture: per-job due + coalesced catch-up windows."""
    moment = now if now is not None else _now()
    conn = _connect(cfg)
    try:
        _migrate(conn)
        rows = conn.execute(
            "SELECT * FROM jobs ORDER BY id").fetchall()
        jobs = []
        for row in rows:
            job = _job_dict(row)
            jobs.append({"id": job["id"], "name": job["name"],
                         "kind": job["kind"], "enabled": job["enabled"],
                         "due": _due(job, moment),
                         "missed_windows": _missed_windows(job, moment),
                         "last_run_at": job["last_run_at"],
                         "last_state": job["last_state"],
                         "run_count": job["run_count"]})
        return {"state": "OK", "now": moment, "jobs": jobs,
                "due_count": sum(1 for j in jobs if j["due"]),
                "catch_up_total": sum(j["missed_windows"] for j in jobs)}
    finally:
        conn.close()


def autofire(cfg: Any, *, now: Optional[float] = None,
             executor: Optional[Callable[..., Dict[str, Any]]] = None,
             estop_engaged: Optional[Callable[[], bool]] = None,
             limit: int = AUTOFIRE_LIMIT) -> Dict[str, Any]:
    """Bounded, idempotent self-fire tick for the restart-safe monitor loop.

    This is the ONE entry point an existing periodic monitor (systemd timer
    or in-process loop) calls to let due jobs run themselves. It is a thin,
    fail-closed wrapper over :func:`run_due` and adds no scheduling authority
    of its own:

    * it never starts a timer/thread — the caller's loop owns the cadence, so
      there is still exactly one autonomous scheduler;
    * the durable emergency-stop/global-disable latch is consulted FIRST and
      refuses the whole tick (nothing is claimed, no due window is consumed),
      so a latched estop neither runs work nor burns a job's next slot;
    * the claim persists ``last_run_at`` with a compare-and-swap lease inside
      ``run_due``, so a process restart or a concurrent/overlapping tick can
      never double-fire a job;
    * each fired job runs through the SAME executor, session naming, typed
      result, per-job run receipt, and ``schedules.run`` audit event as a
      manual ``run-due`` (attributed to ``monitor:autofire``);
    * the tick is bounded to ``limit`` jobs and NEVER raises into the monitor
      loop — a store/runtime failure is returned as a typed ``FAILED``
      envelope.
    """
    from .plugins import estop_engaged as _engaged

    stop = estop_engaged or (lambda: bool(_engaged(cfg)))
    if stop():
        denied = refuse_estop("autofire")
        denied["ran"] = 0
        denied["results"] = []
        return denied

    def _guarded(task_text: str, *, session: str,
                 estop_engaged: Callable[[], bool]) -> Dict[str, Any]:
        # Re-check the latch per job: a stop that latches mid-tick is honored
        # before any further work, even with an injected executor.
        if estop_engaged():
            return {"state": "UNAVAILABLE", "code": "EMERGENCY_STOP",
                    "detail": "emergency stop latched"}
        target = executor or _default_executor
        return target(task_text, session=session,
                      estop_engaged=estop_engaged)

    try:
        out = run_due(cfg, requested_by=MONITOR_ACTOR, executor=_guarded,
                      now=now, estop_engaged=stop, limit=limit)
    except SchedulesError as exc:
        return {"state": "FAILED", "code": exc.code,
                "detail": redact_spec(exc.detail)[:300],
                "ran": 0, "results": []}
    except Exception as exc:  # noqa: BLE001 — never raise into the monitor
        return {"state": "FAILED", "code": RUNTIME_CODE,
                "detail": redact_spec(str(exc))[:300],
                "ran": 0, "results": []}
    out["actor"] = MONITOR_ACTOR
    out["catch_up"] = sum(int(r.get("catch_up", 0))
                          for r in out.get("results", []))
    # The same one monitor tick also drives due automation heartbeats and
    # queued standing-order auto-replies. This adds no timer/thread of its
    # own (the monitor owns the cadence) and never raises into the loop.
    out["automation"] = _automation_tick(cfg, now=now, stop=stop)
    return out


def _automation_tick(cfg: Any, *, now: Optional[float],
                     stop: Callable[[], bool]) -> Dict[str, Any]:
    """Fail-closed automation tick invoked by :func:`autofire` (one authority)."""
    try:
        from . import automation as _automation

        return _automation.tick(cfg, now=now, estop=stop)
    except Exception as exc:  # noqa: BLE001 — never raise into the monitor
        return {"state": "FAILED", "code": RUNTIME_CODE,
                "detail": redact_spec(str(exc))[:300],
                "heartbeats": 0, "processed": 0, "results": []}

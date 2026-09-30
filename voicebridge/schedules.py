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
``docs/archive/2026-09-21/UPSTREAM_REUSE.md``.
"""
from __future__ import annotations

import os
import re
import sqlite3
import time
from datetime import datetime, time as dt_time, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

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

# --- timezone (ports the upstream configured-zone schedule semantics) --------
# The upstream scheduler evaluates wall-clock schedules in a configured IANA
# timezone (``hermes_time.py:91 get_timezone`` / ``cron/jobs.py:1137
# compute_next_run``); ``QUBIT_TIMEZONE`` mirrors the upstream env override.
# Resolution order: env, then the configured ``timezone`` key, then
# server-local. An invalid zone degrades to server-local (never a crash) and is
# still reported truthfully.
TZ_ENV = "QUBIT_TIMEZONE"

# --- bounded automatic re-run ladder for unreachable dispatches -------------
# Ported from the upstream cron retry ladder (reference
# ``cron/unreachable_retry.py:32 RETRY_DELAYS_SECONDS``): a recurring job whose
# run failed transiently (never reached the model) is re-armed after
# 5, 15, then 30 minutes before falling back to its natural occurrence. Off
# with ``cron.retry_unreachable: false`` (upstream default ON).
RETRY_DELAYS_SECONDS: Tuple[int, ...] = (300, 900, 1800)
RETRY_REACHED_MODEL = "reached-model"
RETRY_DISABLED = "disabled"
RETRY_ONESHOT = "one-shot"
RETRY_NOT_TRANSIENT = "not-transient"
RETRY_EXHAUSTED = "exhausted"
RETRY_NATURAL_SOONER = "natural-sooner"

# Transient transport/DNS needles, ported from the upstream classifier
# (reference ``cron/scheduler_preflight.py:27-38, _is_transient_provider_resolve_error``).
# Qubit executors cannot observe an agent's API-call counter, so a retry is
# scheduled only when the typed failure text is transient network-shaped — an
# honest narrowing of the upstream "zero API calls" predicate.
TRANSIENT_NEEDLES = (
    "nodename nor servname", "name or service not known",
    "temporary failure in name resolution", "network is unreachable",
    "connection refused", "timed out", "timeout", "connecterror",
    "connecttimeout", "readtimeout", "networkerror", "dns",
)

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
  cron_expr TEXT NOT NULL DEFAULT '',
  enabled INTEGER NOT NULL DEFAULT 1,
  created_by TEXT NOT NULL DEFAULT '',
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  last_run_at REAL NOT NULL DEFAULT 0,
  last_state TEXT NOT NULL DEFAULT '',
  run_count INTEGER NOT NULL DEFAULT 0,
  deliver_to TEXT NOT NULL DEFAULT '',
  deliver_pref TEXT NOT NULL DEFAULT '',
  retry_at REAL NOT NULL DEFAULT 0,
  retry_attempt INTEGER NOT NULL DEFAULT 0,
  last_error TEXT NOT NULL DEFAULT ''
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


def _cfg_lookup(cfg: Any, *path: str) -> Any:
    """Read a nested config value from a Config/dict, never raising."""
    if cfg is None:
        return None
    try:
        node: Any = cfg
        for part in path:
            if isinstance(node, dict):
                if part not in node:
                    return None
                node = node[part]
            elif hasattr(node, "get"):
                node = node.get(part, default=None)
                if node is None:
                    return None
            else:
                return None
        return node
    except Exception:  # noqa: BLE001 — config shape must never break a tick
        return None


def _tz_name(cfg: Any) -> str:
    """Configured IANA timezone name (``""`` = server-local).

    Mirrors the upstream resolution order (``hermes_time.py:45
    _resolve_timezone_name``): env override first, then the configured
    ``timezone``, then server-local. ``schedules.timezone`` is also honored so a
    Qubit-specific pin does not have to shadow the top-level key.
    """
    env = ""
    try:
        env = os.environ.get(TZ_ENV, "").strip()
    except Exception:  # noqa: BLE001
        env = ""
    if env:
        return env
    for path in (("timezone",), ("schedules", "timezone")):
        value = _cfg_lookup(cfg, *path)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _zone(cfg: Any) -> Tuple[Any, str]:
    """``(ZoneInfo | None, configured_name)``; invalid/blank -> server-local."""
    name = _tz_name(cfg)
    if not name:
        return None, ""
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name), name
    except Exception:  # noqa: BLE001 — invalid zone degrades, never crashes
        return None, name


def _local(now: float, zone: Any) -> datetime:
    """Wall-clock datetime for ``now`` in ``zone`` (or server-local)."""
    if zone is not None:
        return datetime.fromtimestamp(now, zone)
    return datetime.fromtimestamp(now)


def _daily_target(job: Dict[str, Any], now: float, zone: Any) -> float:
    """Epoch of the most recent ``hour:minute`` wall-clock target <= ``now``.

    Daily schedules express wall-clock intent, so the target is computed in the
    configured zone (``datetime`` re-resolves DST for the constructed local
    time) instead of the epoch-day arithmetic that implicitly meant UTC.
    """
    local = _local(now, zone)
    target = local.replace(hour=int(job["hour"]), minute=int(job["minute"]),
                           second=0, microsecond=0)
    if target > local:
        target = target - timedelta(days=1)
    return target.timestamp()


def _natural_next_run_at(job: Dict[str, Any], *, cfg: Any = None,
                         now: Optional[float] = None) -> Optional[float]:
    """The schedule's own next due epoch, ignoring any pending retry."""
    moment = now if now is not None else _now()
    kind = str(job.get("kind", "") or "")
    zone, _name = _zone(cfg)
    if kind == "interval":
        try:
            base = float(job.get("last_run_at") or job.get("created_at") or 0.0)
            step = float(job.get("interval_min") or 0) * 60.0
        except (TypeError, ValueError):
            return None
        if step <= 0:
            return None
        return (base or moment) + step
    if kind == "daily":
        try:
            hour = int(job.get("hour", -1))
            minute = int(job.get("minute", 0))
        except (TypeError, ValueError):
            return None
        if hour < 0:
            return None
        local = _local(moment, zone)
        target = local.replace(hour=hour, minute=minute, second=0,
                               microsecond=0)
        if target <= local:
            target = target + timedelta(days=1)
        return target.timestamp()
    if kind == "once":
        try:
            at = float(job.get("at_ts") or 0.0)
            base = float(job.get("last_run_at") or 0.0)
        except (TypeError, ValueError):
            return None
        return at if at > 0 and base <= 0 else None
    if kind == "cron":
        parsed = _parse_cron(str(job.get("cron_expr", "") or ""))
        if parsed is None:
            return None
        return _cron_next(parsed, moment, zone)
    return None


def next_run_at(job: Dict[str, Any], *, cfg: Any = None,
                now: Optional[float] = None) -> Optional[float]:
    """Projected next due epoch for a job (``None`` = never/no more runs).

    Ported from the upstream ``cron/jobs.py:1137 compute_next_run`` shape:
    recurring kinds anchor on ``last_run_at`` so a restart does not re-anchor;
    intervals add in UTC so a DST change never alters the duration; daily
    targets use the configured wall clock; a one-shot is eligible only until it
    has run. A pending automatic re-run is folded in the way upstream
    ``cron/unreachable_retry.py:117 plan_retry`` pulls ``next_run_at`` earlier to
    the ladder instant, so the projection never hides a sooner retry.
    """
    if not job.get("enabled", True):
        return None
    moment = now if now is not None else _now()
    natural = _natural_next_run_at(job, cfg=cfg, now=moment)
    try:
        retry_at = float(job.get("retry_at") or 0.0)
    except (TypeError, ValueError):
        retry_at = 0.0
    if retry_at > moment and (natural is None or retry_at < natural):
        return retry_at
    return natural


def retry_enabled(cfg: Any) -> bool:
    """``cron.retry_unreachable`` (upstream default ON), ``schedules.retry_backoff``.

    Ported from the reference ``cron/unreachable_retry.py:39 retry_enabled``:
    only an explicit ``False`` disables the ladder.
    """
    cron = _cfg_lookup(cfg, "cron")
    if isinstance(cron, dict) and "retry_unreachable" in cron:
        return cron.get("retry_unreachable") is not False
    sched = _cfg_lookup(cfg, "schedules")
    if isinstance(sched, dict) and "retry_backoff" in sched:
        return sched.get("retry_backoff") is not False
    return True


def _is_transient_failure(outcome: Dict[str, Any]) -> bool:
    """True when a failed run looks like a transient transport/DNS failure.

    Ports the upstream classifier's needle set (reference
    ``cron/scheduler_preflight.py:27-77``) over the typed outcome Qubit
    executors return. Only a non-OK state is eligible, so a successful turn can
    never be re-run.
    """
    state = str(outcome.get("state", "") or "").upper()
    if state in ("", "OK"):
        return False
    if outcome.get("retryable") is True:
        return True
    blob = (str(outcome.get("code", "") or "") + " "
            + str(outcome.get("detail", "") or "")).lower()
    return any(needle in blob for needle in TRANSIENT_NEEDLES)


def _clear_retry(conn: sqlite3.Connection, job_id: int,
                 last_error: str = "") -> None:
    conn.execute(
        "UPDATE jobs SET retry_at=0, retry_attempt=0, last_error=?, "
        "updated_at=? WHERE id=?",
        (last_error[:500], _now(), int(job_id)))


def _plan_retry(cfg: Any, conn: sqlite3.Connection, job: Dict[str, Any],
                outcome: Dict[str, Any], moment: float,
                requested_by: str) -> Dict[str, Any]:
    """Apply the bounded re-run ladder after one run (reference
    ``cron/unreachable_retry.py:85 plan_retry``).

    Clears the ladder on any run that reached the model (success or a
    non-transient failure) and on one-shots; otherwise pulls ``retry_at`` to the
    next ladder instant when that is sooner than the job's natural occurrence.
    Returns a truthful receipt (never fabricates a scheduled retry).
    """
    state = str(outcome.get("state", "") or "").upper()
    if state == "OK":
        _clear_retry(conn, job["id"], "")
        conn.commit()
        return {"scheduled": False, "reason": RETRY_REACHED_MODEL}
    err = str(outcome.get("detail", "") or outcome.get("code", "") or "")
    err = redact_spec(err)
    attempt = int(job.get("retry_attempt") or 0)
    if job.get("kind") == "once":
        _clear_retry(conn, job["id"], err)
        conn.commit()
        return {"scheduled": False, "reason": RETRY_ONESHOT}
    if not retry_enabled(cfg):
        _clear_retry(conn, job["id"], err)
        conn.commit()
        return {"scheduled": False, "reason": RETRY_DISABLED}
    if not _is_transient_failure(outcome):
        _clear_retry(conn, job["id"], err)
        conn.commit()
        return {"scheduled": False, "reason": RETRY_NOT_TRANSIENT}
    if attempt >= len(RETRY_DELAYS_SECONDS):
        # Ladder exhausted: fall back to the natural schedule and reset so the
        # next occurrence gets a fresh ladder if the network is still down.
        _clear_retry(conn, job["id"], err)
        conn.commit()
        return {"scheduled": False, "reason": RETRY_EXHAUSTED,
                "attempts": attempt}
    delay = RETRY_DELAYS_SECONDS[attempt]
    retry_at = moment + float(delay)
    view = dict(job)
    view["last_run_at"] = moment
    # Compare against the schedule's own next occurrence (not the retry-folded
    # projection) — the same natural-vs-ladder test upstream ``plan_retry`` uses.
    natural = _natural_next_run_at(view, cfg=cfg, now=moment)
    if natural is not None and natural <= retry_at:
        # The schedule fires again sooner than the ladder would — the natural
        # occurrence IS the retry, so do not consume an attempt.
        _clear_retry(conn, job["id"], err)
        conn.commit()
        return {"scheduled": False, "reason": RETRY_NATURAL_SOONER}
    conn.execute(
        "UPDATE jobs SET retry_at=?, retry_attempt=?, last_error=?, "
        "updated_at=? WHERE id=?",
        (retry_at, attempt + 1, err[:500], _now(), job["id"]))
    conn.commit()
    _audit("retry", requested_by,
           {"job_id": str(job["id"]), "attempt": str(attempt + 1),
            "delay_s": str(delay), "state": state})
    return {"scheduled": True, "attempt": attempt + 1, "delay": delay,
            "at": retry_at, "reason": "unreachable"}


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
    if "cron_expr" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN cron_expr TEXT "
                     "NOT NULL DEFAULT ''")
    if "deliver_pref" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN deliver_pref TEXT "
                     "NOT NULL DEFAULT ''")
    if "retry_at" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN retry_at REAL "
                     "NOT NULL DEFAULT 0")
    if "retry_attempt" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN retry_attempt INTEGER "
                     "NOT NULL DEFAULT 0")
    if "last_error" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN last_error TEXT "
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
            "cron_expr": row["cron_expr"],
            "deliver_pref": row["deliver_pref"],
            "enabled": bool(row["enabled"]),
            "created_by": row["created_by"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "last_run_at": row["last_run_at"],
            "last_state": row["last_state"],
            "run_count": row["run_count"],
            "deliver_to": row["deliver_to"],
            "retry_at": row["retry_at"],
            "retry_attempt": row["retry_attempt"],
            "last_error": row["last_error"]}


def _job_view(cfg: Any, job: Dict[str, Any],
              now: Optional[float] = None) -> Dict[str, Any]:
    """Job dict plus the derived ``next_run_at`` projection (real, not null)."""
    view = dict(job)
    view["next_run_at"] = next_run_at(view, cfg=cfg, now=now)
    return view


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


# --- 5-field cron + ISO datetime (dashboard schedule builder) ----------------
# The dashboard's ScheduleBuilder emits cron expressions (daily/weekly/monthly)
# and an ISO timestamp (once); the reference cron surface accepts them, so the
# Qubit store must too. A small standard 5-field matcher is enough here: the
# store still owns exactly one scheduling authority.
_ISO_WHEN_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?$")
_CRON_BOUNDS: Tuple[Tuple[str, int, int], ...] = (
    ("minute", 0, 59), ("hour", 0, 23), ("dom", 1, 31), ("month", 1, 12),
    ("dow", 0, 7))


def _cron_field_values(field: str, lo: int, hi: int) -> frozenset:
    """Expand one cron field (``*``, ``a,b``, ``a-b``, ``*/n``, ``a-b/n``)."""
    values = set()
    for part in str(field or "").split(","):
        part = part.strip()
        if not part:
            raise ValueError("empty cron field")
        step = 1
        if "/" in part:
            base, _, step_s = part.partition("/")
            if not step_s.isdigit() or int(step_s) <= 0:
                raise ValueError("bad cron step")
            step = int(step_s)
        else:
            base = part
        if base == "*":
            start, end = lo, hi
        elif "-" in base:
            a, _, b = base.partition("-")
            if not (a.isdigit() and b.isdigit()):
                raise ValueError("bad cron range")
            start, end = int(a), int(b)
        else:
            if not base.isdigit():
                raise ValueError("bad cron value")
            start = end = int(base)
        if start < lo or end > hi or start > end:
            raise ValueError("cron value out of range")
        values.update(range(start, end + 1, step))
    if not values:
        raise ValueError("empty cron field")
    return frozenset(values)


@lru_cache(maxsize=256)
def _parse_cron(text: str) -> Optional[Dict[str, Any]]:
    """Parse a 5-field cron expression; ``None`` when it is not valid.

    Cached because ``_due``/``next_run_at`` run per tick per job.
    """
    fields = str(text or "").split()
    if len(fields) != 5:
        return None
    parsed: Dict[str, Any] = {}
    try:
        for field, (name, lo, hi) in zip(fields, _CRON_BOUNDS):
            parsed[name] = _cron_field_values(field, lo, hi)
    except ValueError:
        return None
    return parsed


def _cron_dow_values(parsed: Dict[str, Any]) -> frozenset:
    """Normalize the day-of-week set to 0..6 (Sunday=0, 7 -> 0)."""
    days = {0 if d == 7 else d for d in parsed["dow"]}
    return frozenset(days)


def _cron_day_matches(parsed: Dict[str, Any], day: Any) -> bool:
    dom_all = parsed["dom"] == frozenset(range(1, 32))
    dow_all = parsed["dow"] == frozenset(range(0, 8))
    dom_ok = day.day in parsed["dom"]
    cron_dow = (day.weekday() + 1) % 7  # Mon=0..Sun=6 -> Sun=0..Sat=6
    dow_ok = cron_dow in _cron_dow_values(parsed)
    if not dom_all and not dow_all:
        # Vixie cron: with both day fields restricted, either may match.
        return dom_ok or dow_ok
    return dom_ok and dow_ok


def _cron_next(parsed: Dict[str, Any], after: float,
               zone: Any) -> Optional[float]:
    """Epoch of the first matching wall-clock minute strictly after ``after``."""
    start = _local(after, zone).replace(second=0, microsecond=0)
    start = start + timedelta(minutes=1)
    hours = sorted(parsed["hour"])
    minutes = sorted(parsed["minute"])
    for offset in range(0, 366 * 5 + 2):
        day = (start + timedelta(days=offset)).date()
        if day.month not in parsed["month"]:
            continue
        if not _cron_day_matches(parsed, day):
            continue
        for hour in hours:
            for minute in minutes:
                cand = datetime.combine(day, dt_time(hour, minute))
                if zone is not None:
                    cand = cand.replace(tzinfo=zone)
                if cand >= start:
                    return cand.timestamp()
    return None


def _cron_prev(parsed: Dict[str, Any], at: float,
               zone: Any) -> Optional[float]:
    """Epoch of the last matching wall-clock minute at or before ``at``."""
    start = _local(at, zone).replace(second=0, microsecond=0)
    hours = sorted(parsed["hour"], reverse=True)
    minutes = sorted(parsed["minute"], reverse=True)
    for offset in range(0, 366 * 5 + 2):
        day = (start - timedelta(days=offset)).date()
        if day.month not in parsed["month"]:
            continue
        if not _cron_day_matches(parsed, day):
            continue
        for hour in hours:
            for minute in minutes:
                cand = datetime.combine(day, dt_time(hour, minute))
                if zone is not None:
                    cand = cand.replace(tzinfo=zone)
                if cand <= start:
                    return cand.timestamp()
    return None


def parse_when(spec: str, *, now: Optional[float] = None) -> Dict[str, Any]:
    """Parse a schedule phrase into a job spec dict.

    Accepts ``every N minutes|hours``, ``daily HH[:MM][am|pm]``,
    ``once in N minutes|hours``, an ISO datetime (``YYYY-MM-DDTHH:MM[:SS]``),
    and a 5-field cron expression (``0 9 * * *``, ``30 14 * * 1,3,5``,
    ``0 9 1 * *``). Anything else raises BAD_INPUT with the accepted shapes
    (voice turns this into a clarification question).
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
    # ISO datetime (the dashboard's "Once" mode emits a local wall-clock value).
    if _ISO_WHEN_RE.match(text):
        try:
            moment = datetime.fromisoformat(text)
        except ValueError:
            moment = None
        if moment is not None:
            return {"kind": "once", "at_ts": float(moment.timestamp())}
    # 5-field cron (daily/weekly/monthly; the custom escape hatch).
    if _parse_cron(text) is not None:
        return {"kind": "cron", "cron_expr": " ".join(text.split())}
    raise SchedulesError(
        BAD_INPUT_CODE,
        f"unknown schedule {text[:60]!r}: say 'every 30 minutes', "
        f"'daily 9am', 'once in 10 minutes', an ISO datetime, or a 5-field "
        f"cron expression")


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


def _due(job: Dict[str, Any], now: float, cfg: Any = None) -> bool:
    if not job["enabled"]:
        return False
    # A pending retry re-arms the job at the ladder instant regardless of the
    # natural schedule (reference cron/unreachable_retry.plan_retry pulls
    # next_run_at earlier; here retry_at is the explicit re-arm).
    retry_at = float(job.get("retry_at") or 0.0)
    if retry_at > 0:
        return now >= retry_at
    kind = job["kind"]
    if kind == "interval":
        base = job["last_run_at"] or job["created_at"]
        return now >= base + job["interval_min"] * 60.0
    if kind == "daily":
        zone, _name = _zone(cfg)
        target = _daily_target(job, now, zone)
        return job["last_run_at"] < target <= now
    if kind == "once":
        return job["last_run_at"] <= 0 < job["at_ts"] <= now
    if kind == "cron":
        parsed = _parse_cron(str(job.get("cron_expr", "") or ""))
        if parsed is None:
            return False
        zone, _name = _zone(cfg)
        prev = _cron_prev(parsed, now, zone)
        return prev is not None and job["last_run_at"] < prev <= now
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


# ------------------------------------------------------- confirmation input
def _confirm_truthy(value: Any) -> bool:
    """Coerce a wire ``confirm`` value (bool/str/int) to a strict bool.

    The dashboard SPA sends ``{"confirm": true}``; older/curl callers may send
    ``"true"``/``"1"`` in a query string. Anything unrecognized is False so an
    ambiguous value can never satisfy a confirmation gate.
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in (
        "1", "true", "yes", "on", "confirm", "confirmed")


def confirm_requested(body: Optional[Dict[str, Any]] = None,
                      query: Optional[Dict[str, Any]] = None) -> bool:
    """Read the confirmation flag from a request body OR query string.

    ``DELETE`` bodies are not parsed reliably by every HTTP hop (the SPA's
    ``/agent`` dispatcher only reads a body for POST/PUT/PATCH), so a mutating
    route may receive the flag only in the query string. Both sources are
    accepted; the body wins when both are present. This is the single helper
    the route layer calls so the store's gates cannot diverge per verb.
    """
    for source in (body, query):
        if isinstance(source, dict) and "confirm" in source:
            return _confirm_truthy(source.get("confirm"))
    return False


# ------------------------------------------------------------------ CRUD
def _deliver_canonical(cfg: Any, pref: str) -> str:
    """Map a stored delivery preference to a real transport spec (or ``""``).

    The dashboard's deliver dropdown offers ``local``/``origin`` and connected
    platform names; only explicit ``channel:``/``webhook:`` specs name a
    concrete transport here. A bare platform id is persisted as the display
    preference but yields no fabricated transport target.
    """
    text = str(pref or "").strip()
    if not text or text.lower() in ("local", "origin", "none", "save"):
        return ""
    if text.startswith(("channel:", "webhook:")):
        return _check_targets(cfg, parse_delivery_spec(text))
    return ""


def create_job(cfg: Any, name: str, task_text: str, when: str, *,
               deliver_to: str = "", deliver_pref: str = "",
               requested_by: str = "cli:user",
               confirmed: bool = False,
               body: Optional[Dict[str, Any]] = None,
               query: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Create a scheduled job (confirmation required: unattended work).

    This is the single write authority for the ``schedules.db`` store the
    compat handlers, CLI, dashboard, and voice all read. ``deliver_to`` names
    zero or more delivery targets to receive the run's bounded, redacted
    result: ``channel:<account>:<recipient>`` and/or ``webhook:<sub_id>``,
    comma separated. ``deliver_pref`` is the dashboard's raw deliver choice
    (``local``/``origin``/platform), preserved for display. Only configuration
    is stored here (the reference must already exist); nothing is delivered at
    create time.
    """
    if not (confirmed or confirm_requested(body=body, query=query)):
        raise SchedulesError(CONFIRM_CODE,
                             "scheduling unattended work requires explicit "
                             "--confirm")
    clean_name = _check_name(name)
    clean_task = _check_task(task_text)
    spec = parse_when(when)
    pref = str(deliver_pref or deliver_to or "").strip()[:MAX_TARGET_LEN]
    canonical = _deliver_canonical(cfg, pref)
    conn = _connect(cfg)
    try:
        _migrate(conn)
        cur = conn.execute(
            "INSERT INTO jobs (name, task_text, kind, interval_min, hour, "
            "minute, at_ts, cron_expr, enabled, created_by, created_at, "
            "updated_at, deliver_to, deliver_pref) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (clean_name, clean_task, spec["kind"],
             int(spec.get("interval_min", 0)), int(spec.get("hour", -1)),
             int(spec.get("minute", 0)), float(spec.get("at_ts", 0.0)),
             str(spec.get("cron_expr", "")), 1, requested_by, _now(), _now(),
             canonical, pref))
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


def create(cfg: Any, name: str, task_text: str, when: str, *,
           deliver_to: str = "", deliver_pref: str = "",
           requested_by: str = "cli:user",
           confirmed: bool = False) -> Dict[str, Any]:
    """Back-compat alias for :func:`create_job` (CLI/voice call site)."""
    return create_job(cfg, name, task_text, when, deliver_to=deliver_to,
                      deliver_pref=deliver_pref, requested_by=requested_by,
                      confirmed=confirmed)


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
        _zone_obj, tz_name = _zone(cfg)
        return {"state": "OK",
                "jobs": [_job_view(cfg, _job_dict(r)) for r in rows],
                "limit": limit, "timezone": tz_name}
    finally:
        conn.close()


def _get(conn: sqlite3.Connection, job_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM jobs WHERE id=?",
                       (int(job_id),)).fetchone()
    if row is None:
        raise SchedulesError(NOT_FOUND_CODE, f"no schedule #{job_id}")
    return row


def get_job(cfg: Any, job_id: int) -> Dict[str, Any]:
    """Get one job (with its recent runs) from the single store.

    Raises :class:`SchedulesError` ``SCHEDULES_NOT_FOUND`` for an unknown id;
    never fabricates a job. The return envelope matches the read handlers so
    the route layer can serve it directly.
    """
    conn = _connect(cfg)
    try:
        _migrate(conn)
        row = _get(conn, job_id)
        runs = conn.execute(
            "SELECT * FROM runs WHERE job_id=? ORDER BY id DESC LIMIT 10",
            (row["id"],)).fetchall()
        job = _job_view(cfg, _job_dict(row))
        job["recent_runs"] = [
            {"id": r["id"], "started_at": r["started_at"],
             "finished_at": r["finished_at"], "state": r["state"],
             "code": r["code"], "detail": r["detail"][:300]}
            for r in runs]
        return {"state": "OK", "job": job}
    finally:
        conn.close()


def show(cfg: Any, job_id: int) -> Dict[str, Any]:
    """Back-compat alias for :func:`get_job` (CLI/dashboard read)."""
    return get_job(cfg, job_id)


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
        return {"state": "OK", "job": _job_view(
            cfg, _job_dict(_get(conn, job_id)))}
    finally:
        conn.close()


_EDIT_FIELDS = ("name", "task_text", "prompt", "when", "schedule",
                "deliver", "deliver_to")


def _body_has_edit_fields(body: Any) -> bool:
    """True when a request body carries an in-place job edit.

    The dashboard's PUT /api/cron/jobs/{id} (edit modal) sends the full
    mutation through the same store setter pause/resume use; a bare
    ``{"confirm": true}`` (the row pause/resume actions) never trips this.
    """
    if not isinstance(body, dict):
        return False
    return any(body.get(key) not in (None, "") for key in _EDIT_FIELDS)


def update_job(cfg: Any, job_id: int, *, name: Optional[str] = None,
               task_text: Optional[str] = None, when: Optional[str] = None,
               deliver_to: Optional[str] = None,
               deliver_pref: Optional[str] = None,
               enabled: Optional[bool] = None,
               requested_by: str = "cli:user", confirmed: bool = False,
               body: Optional[Dict[str, Any]] = None,
               query: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Update a job's editable fields in the single store (confirm required).

    Only the fields explicitly supplied change; task text, schedule, name,
    delivery preference, and enabled state are all mutable here (matching the
    reference edit contract, which is not immutable in place). Re-parsing
    ``when`` re-derives the schedule kind/fields in the same row so run history
    is retained.
    """
    if not (confirmed or confirm_requested(body=body, query=query)):
        raise SchedulesError(CONFIRM_CODE,
                             "schedule update requires explicit --confirm")
    conn = _connect(cfg)
    try:
        _migrate(conn)
        _get(conn, job_id)
        sets: List[str] = []
        params: List[Any] = []
        if name not in (None, ""):
            sets.append("name=?")
            params.append(_check_name(str(name)))
        if task_text not in (None, ""):
            sets.append("task_text=?")
            params.append(_check_task(str(task_text)))
        if when not in (None, ""):
            spec = parse_when(str(when))
            sets += ["kind=?", "interval_min=?", "hour=?", "minute=?",
                     "at_ts=?", "cron_expr=?"]
            params += [spec["kind"], int(spec.get("interval_min", 0)),
                       int(spec.get("hour", -1)), int(spec.get("minute", 0)),
                       float(spec.get("at_ts", 0.0)),
                       str(spec.get("cron_expr", ""))]
        if deliver_pref is not None or deliver_to is not None:
            pref = str(deliver_pref if deliver_pref is not None
                       else deliver_to or "").strip()[:MAX_TARGET_LEN]
            sets += ["deliver_pref=?", "deliver_to=?"]
            params += [pref, _deliver_canonical(cfg, pref)]
        if enabled is not None:
            sets.append("enabled=?")
            params.append(1 if enabled else 0)
        if sets:
            sets.append("updated_at=?")
            params.append(_now())
            conn.execute("UPDATE jobs SET " + ", ".join(sets) + " WHERE id=?",
                         (*params, int(job_id)))
            conn.commit()
            _audit("update", requested_by, {"job_id": str(int(job_id))})
        return {"state": "OK",
                "job": _job_view(cfg, _job_dict(_get(conn, job_id)))}
    finally:
        conn.close()


def set_paused(cfg: Any, job_id: int, paused: bool, *,
               requested_by: str = "cli:user", confirmed: bool = False,
               body: Optional[Dict[str, Any]] = None,
               query: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Pause (``paused=True``) or resume a job in the single store.

    Confirmation is required and may arrive in the explicit ``confirmed``
    argument or in the request ``body``/``query`` (both are accepted so the
    route layer never has to special-case a verb). The enabled-state transition
    and its audit receipt are the same as the CLI/voice path.

    The dashboard edit modal routes its full mutation through this same
    setter (the compat PUT handler); when the body carries editable fields the
    edit is applied in place via :func:`update_job` so "Save changes" is real
    rather than an enabled no-op.
    """
    settled = bool(confirmed or confirm_requested(body=body, query=query))
    if settled and _body_has_edit_fields(body):
        assert isinstance(body, dict)
        return update_job(
            cfg, job_id,
            name=body.get("name"),
            task_text=body.get("task_text", body.get("prompt")),
            when=(body.get("when") or body.get("schedule")),
            deliver_pref=body.get("deliver", body.get("deliver_to")),
            enabled=not paused,
            requested_by=requested_by, confirmed=True)
    return _set_enabled(
        cfg, job_id, not paused, requested_by=requested_by,
        confirmed=settled, op="pause" if paused else "resume")


def pause(cfg: Any, job_id: int, *, requested_by: str = "cli:user",
          confirmed: bool = False) -> Dict[str, Any]:
    """Back-compat alias: pause a job (confirmation required)."""
    return set_paused(cfg, job_id, True, requested_by=requested_by,
                      confirmed=confirmed)


def resume(cfg: Any, job_id: int, *, requested_by: str = "cli:user",
           confirmed: bool = False) -> Dict[str, Any]:
    """Back-compat alias: resume a job (confirmation required)."""
    return set_paused(cfg, job_id, False, requested_by=requested_by,
                      confirmed=confirmed)


def delete_job(cfg: Any, job_id: int, *, requested_by: str = "cli:user",
               confirmed: bool = False,
               body: Optional[Dict[str, Any]] = None,
               query: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Delete a job from the single store; run history is retained.

    Confirmation is required. The flag is read from the explicit argument, the
    request ``body``, OR the request ``query`` -- the SPA's ``/agent``
    dispatcher does not parse a DELETE body, so a query ``?confirm=true`` must
    be honored identically to a JSON body ``{"confirm": true}``.
    """
    if not (confirmed or confirm_requested(body=body, query=query)):
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


def delete(cfg: Any, job_id: int, *, requested_by: str = "cli:user",
           confirmed: bool = False) -> Dict[str, Any]:
    """Back-compat alias for :func:`delete_job` (CLI/voice/route call site)."""
    return delete_job(cfg, job_id, requested_by=requested_by,
                      confirmed=confirmed)


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
def _contract_dispatch(cfg: Any, task_text: str, *, session: str,
                       directive: bool,
                       now: Optional[float] = None) -> Dict[str, Any]:
    """Route one scheduled job through the canonical operation contract.

    Every scheduled/automation dispatch funnels through
    :func:`voicebridge.dashboard_ops.run_op` (op ``schedule_dispatch``) — the
    same one-operation broker the CLI, dashboard, and voice use — instead of a
    private executor. The user pre-authorized the job when the confirmed
    schedule was created, so the monitor actor settles the staged confirmation
    through the shared journal; the op then runs the exact backend. Failures
    are typed and never fabricated.
    """
    from . import dashboard_ops as OPS

    payload: Dict[str, Any] = {
        "task_text": str(task_text or ""),
        "session": str(session or ""),
        "directive": "true" if directive else "false",
    }
    if now is not None:
        payload["now"] = str(float(now))
    try:
        out = OPS.run_op(cfg, "schedule_dispatch", payload,
                         actor=MONITOR_ACTOR, session=session)
        if out.get("state") == "needs-confirm" and out.get("confirmation_id"):
            out = OPS.confirm_op(cfg, out["confirmation_id"],
                                 actor=MONITOR_ACTOR, session=session)
    except Exception as exc:  # noqa: BLE001 — fail closed, typed
        return {"state": "UNAVAILABLE", "code": "DISPATCH_ERROR",
                "detail": str(exc)[:200]}
    if out.get("state") == "executed":
        result = (out.get("receipt") or {}).get("result")
        if isinstance(result, dict):
            return result
        return {"state": "OK", "code": "OK", "detail": str(result)[:300]}
    if out.get("state") == "failed":
        # The broker envelope folds every non-executed outcome into ``failed``;
        # the backend's own typed result (carried on the receipt) keeps the
        # distinction between a directive FAILED and an unavailable runtime.
        # Return it so a bad directive is not relabelled UNAVAILABLE.
        result = (out.get("receipt") or {}).get("result")
        if isinstance(result, dict) and result.get("state"):
            return result
    return {"state": "UNAVAILABLE",
            "code": str(out.get("code")
                        or "SCHEDULE_DISPATCH_UNAVAILABLE"),
            "detail": str(out.get("detail") or out.get("speech") or "")[:200]}


def _default_executor(task_text: str, *, session: str,
                      estop_engaged: Callable[[], bool],
                      cfg: Any = None) -> Dict[str, Any]:
    """Dispatch one due job through the canonical Qubit operation path.

    When a ``cfg`` is supplied (all real callers), the job runs through
    :func:`voicebridge.dashboard_ops.run_op` (op ``schedule_dispatch``) so the
    selected model/provider profile, resolver, gateway policy, and one session
    store are honored — the same contract CLI/dashboard/voice use. A direct
    ``agent.turn`` is used only for the legacy no-config test seam. Typed
    unavailable states, never fake OK."""
    from .agent import AgentDenied, turn as agent_turn

    if estop_engaged():
        return {"state": "UNAVAILABLE", "code": "EMERGENCY_STOP",
                "detail": "emergency stop latched"}
    if cfg is not None:
        return _contract_dispatch(cfg, task_text, session=session,
                                  directive=False)
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
    """Dispatch a scheduled automation directive through the operation contract.

    Routes through :func:`voicebridge.dashboard_ops.run_op` (op
    ``schedule_dispatch``, directive form) so the one scheduler uses the same
    canonical broker/dispatcher as every other surface. The confirmed job is
    the user's pre-authorization; fail-closed and never raises.
    """
    if estop_engaged():
        return {"state": "UNAVAILABLE", "code": "EMERGENCY_STOP",
                "detail": "emergency stop latched"}
    return _contract_dispatch(cfg, task_text, session=MONITOR_ACTOR,
                              directive=True, now=now)


def run_due(cfg: Any, *, requested_by: str = "cli:user",
            executor: Optional[Callable[..., Dict[str, Any]]] = None,
            now: Optional[float] = None,
            estop_engaged: Optional[Callable[[], bool]] = None,
            limit: int = 10, only_job_id: int = 0,
            force: bool = False) -> Dict[str, Any]:
    """Execute due jobs oldest-first (bounded). Each job result is a typed
    state recorded in history; a failing job never blocks later jobs, and
    nothing is fabricated when the runtime is unprovisioned.

    ``only_job_id`` narrows the pass to one job (``0`` = every due job) and
    ``force`` ignores the due/enabled predicates so an operator request can
    run one job immediately. Both are used by :func:`trigger`; the default
    (``0``/False) is the unchanged autonomous/monitor behavior.
    """
    from .plugins import estop_engaged as _engaged

    moment = now if now is not None else _now()
    stop = estop_engaged or (lambda: bool(_engaged(cfg)))
    run = executor or (
        lambda task_text, *, session, estop_engaged: _default_executor(
            task_text, session=session, estop_engaged=estop_engaged,
            cfg=cfg))
    conn = _connect(cfg)
    try:
        _migrate(conn)
        conn.commit()
        # Atomic claim phase: exactly one process wins each due job
        # (BEGIN IMMEDIATE serializes concurrent run-due callers; the
        # last_run_at compare-and-swap is the lease).
        conn.execute("BEGIN IMMEDIATE")
        if only_job_id:
            if force:
                rows = conn.execute(
                    "SELECT * FROM jobs WHERE id=?",
                    (int(only_job_id),)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM jobs WHERE id=? AND enabled=1",
                    (int(only_job_id),)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE enabled=1 ORDER BY id").fetchall()
        claimed: List[Dict[str, Any]] = []
        for row in rows:
            job = _job_dict(row)
            if not force and not _due(job, moment, cfg):
                continue
            if len(claimed) >= max(1, min(int(limit or 10), 25)):
                break
            cur = conn.execute(
                "UPDATE jobs SET last_run_at=?, updated_at=?, retry_at=0 "
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
            # A transient failure that never reached the model re-arms the job
            # on the bounded ladder; any run that reached the model resets it.
            retry = _plan_retry(cfg, conn, job, outcome, moment, requested_by)
            # Delivery is downstream of a durable run receipt: it may fail,
            # degrade, or be unavailable without touching the run outcome.
            delivery = _deliver_result(cfg, conn, job, run_id, outcome, stop,
                                       requested_by)
            results.append({"job_id": job["id"], "name": job["name"],
                            "state": state, "code": code,
                            "catch_up": catch_up, "retry": retry,
                            "delivery": delivery})
        return {"state": "OK", "ran": len(results), "results": results,
                "catch_up": sum(int(r.get("catch_up", 0)) for r in results)}
    finally:
        conn.close()


def trigger(cfg: Any, job_id: int = 0, *, requested_by: str = "cli:user",
            executor: Optional[Callable[..., Dict[str, Any]]] = None,
            now: Optional[float] = None,
            estop_engaged: Optional[Callable[[], bool]] = None,
            limit: int = 10) -> Dict[str, Any]:
    """Run one job now (or every due job when ``job_id`` is 0).

    A targeted trigger verifies the job exists in the single store (typed
    ``SCHEDULES_NOT_FOUND`` otherwise) and forces one execution regardless of
    its schedule/enabled state; the run still records a normal history row and
    is estop-aware. ``job_id=0`` preserves the historical global ``run_due``
    behavior the dashboard trigger used. The result envelope matches
    :func:`run_due`.
    """
    if not job_id:
        return run_due(cfg, requested_by=requested_by, executor=executor,
                       now=now, estop_engaged=estop_engaged, limit=limit)
    get_job(cfg, job_id)  # typed NOT_FOUND before any claim
    return run_due(cfg, requested_by=requested_by, executor=executor,
                   now=now, estop_engaged=estop_engaged, limit=limit,
                   only_job_id=int(job_id), force=True)


def scheduler_status(cfg: Any, *, now: Optional[float] = None) -> Dict[str, Any]:
    """Read-only scheduler posture: per-job due + coalesced catch-up windows."""
    moment = now if now is not None else _now()
    conn = _connect(cfg)
    try:
        _migrate(conn)
        rows = conn.execute(
            "SELECT * FROM jobs ORDER BY id").fetchall()
        _zone_obj, tz_name = _zone(cfg)
        jobs = []
        for row in rows:
            job = _job_dict(row)
            jobs.append({"id": job["id"], "name": job["name"],
                         "kind": job["kind"], "enabled": job["enabled"],
                         "due": _due(job, moment, cfg),
                         "missed_windows": _missed_windows(job, moment),
                         "last_run_at": job["last_run_at"],
                         "last_state": job["last_state"],
                         "run_count": job["run_count"],
                         "next_run_at": next_run_at(job, cfg=cfg, now=moment),
                         "retry_at": job["retry_at"],
                         "retry_attempt": job["retry_attempt"],
                         "last_error": job["last_error"]})
        return {"state": "OK", "now": moment, "timezone": tz_name,
                "retry_enabled": retry_enabled(cfg), "jobs": jobs,
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
        if executor is not None:
            return executor(task_text, session=session,
                            estop_engaged=estop_engaged)
        return _default_executor(task_text, session=session,
                                 estop_engaged=estop_engaged, cfg=cfg)

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

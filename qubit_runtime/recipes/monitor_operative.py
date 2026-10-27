"""The ``monitor_operative`` recipe.

A read-only health report over the owner's operative surfaces. Every collector
uses a surface Qubit already owns and never mutates it:

* cron — the cron store read API (jobs, failures, overdue stamps, ticker
  heartbeat/error markers),
* kanban — the board database's own read-only stats (blocked, stale ready
  work, repeated failures, per-status counts),
* memory — the configured provider name and the installed-provider registry,
* compression — the session store's durable compaction counters.

A missing connector, absent store, or failing read degrades that one section
with a stable typed reason. The model only writes the brief from the collected,
bounded and redacted facts.
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from recipes.sections import (
    REASON_CONNECTOR_NOT_CONFIGURED,
    REASON_MISSING_CONNECTOR,
    REASON_NOT_SUPPORTED,
    REASON_TOOL_ERROR,
    SectionResult,
    SectionUnavailable,
    available,
    redact_text,
    unavailable,
)
from recipes.sections import collect_sections as _collect_sections
from recipes.spec import RecipeSpec

SECTION_TITLES = {
    "cron": "Cron",
    "kanban": "Kanban board",
    "memory": "Memory",
    "compression": "Compression",
}
SECTION_ORDER = tuple(SECTION_TITLES)

# Same grace `qubit cron status` uses before calling a next_run stamp overdue.
CRON_OVERDUE_GRACE_SECONDS = 15 * 60
KANBAN_STALE_SECONDS = 24 * 60 * 60
_MAX_NAMES = 3
_MAX_NAME_CHARS = 80

_UNSET = object()


def _redact(value: object) -> str:
    return redact_text(value)


def _now_epoch(now: datetime | None = None) -> float:
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.timestamp()


def _parse_iso(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _age_text(seconds: float | int | None) -> str:
    try:
        seconds = max(0, int(seconds or 0))
    except (TypeError, ValueError):
        return "unknown"
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    parts = [(days, "d"), (hours, "h"), (minutes if not days else 0, "m")]
    return " ".join(f"{n}{unit}" for n, unit in parts if n) or "0m"


def _bounded_name(value: object) -> str:
    return _redact(value)[:_MAX_NAME_CHARS]


def _names(jobs: Sequence[Mapping[str, Any]], *, limit: int = _MAX_NAMES) -> str:
    labels = [_bounded_name(job.get("name") or job.get("id") or "unnamed") for job in jobs]
    shown = ", ".join(labels[:limit])
    if len(labels) > limit:
        shown += f" (+{len(labels) - limit} more)"
    return shown


# --- cron -----------------------------------------------------------------

def _default_cron_jobs() -> list[dict[str, Any]]:
    """Read jobs.json without the store's repair/ensure-dirs write path.

    ``list_jobs`` is the public read API, but it calls ``ensure_dirs`` and may
    auto-repair a malformed store. A monitor must not write anything, so this
    reads the same file with the store's own parser/normalizer and lets a
    corrupt store surface as a typed ``tool_error`` instead.
    """
    from cron.jobs import _current_cron_store, _normalize_job_record, _parse_jobs_file

    jobs_file = _current_cron_store().jobs_file
    if not jobs_file.exists():
        return []
    data, _strict_retry = _parse_jobs_file(jobs_file)
    jobs = data.get("jobs", []) if isinstance(data, dict) else data
    if not isinstance(jobs, list):
        raise RuntimeError("cron jobs.json has an unsupported shape")
    return [_normalize_job_record(job) for job in jobs if isinstance(job, dict)]


def _default_cron_heartbeat() -> tuple[float | None, str | None]:
    from cron.jobs import get_ticker_heartbeat_age, get_ticker_last_error

    return get_ticker_heartbeat_age(), get_ticker_last_error()


def _job_state(job: Mapping[str, Any]) -> str:
    try:
        from cron.jobs import effective_job_state

        return effective_job_state(dict(job))
    except Exception:
        stored = str(job.get("state") or "").strip()
        if not job.get("enabled", True):
            return stored or "paused"
        return stored or "scheduled"


def _job_failed(job: Mapping[str, Any]) -> bool:
    try:
        streak = int(job.get("failure_streak") or 0)
    except (TypeError, ValueError):
        streak = 0
    status = str(job.get("last_status") or "")
    return (
        streak > 0
        or status in {"error", "failed", "delivery_failed"}
        or _job_state(job) == "error"
    )


def _overdue_seconds(job: Mapping[str, Any], now_epoch: float) -> float | None:
    stamp = _parse_iso(job.get("next_run_at"))
    if stamp is None:
        return None
    return now_epoch - stamp.timestamp()


def collect_cron(
    now: datetime | None = None,
    *,
    jobs: Sequence[Mapping[str, Any]] | None = None,
    heartbeat: Any = _UNSET,
    last_error: Any = _UNSET,
) -> SectionResult:
    """Report cron counts, failures, overdue stamps, next runs and ticker health."""
    records = list(jobs) if jobs is not None else _default_cron_jobs()
    if heartbeat is _UNSET or last_error is _UNSET:
        loaded_age, loaded_error = _default_cron_heartbeat()
        if heartbeat is _UNSET:
            heartbeat = loaded_age
        if last_error is _UNSET:
            last_error = loaded_error

    epoch = _now_epoch(now)
    total = len(records)
    states = [_job_state(job) for job in records]
    completed = sum(1 for state in states if state == "completed")
    paused = sum(
        1 for job, state in zip(records, states)
        if state == "paused" or (not job.get("enabled", True) and state != "completed")
    )
    active = sum(
        1 for job, state in zip(records, states)
        if job.get("enabled", True) and state not in {"paused", "completed"}
    )
    failed = [job for job in records if _job_failed(job)]
    overdue = []
    upcoming = []
    for job, state in zip(records, states):
        if not job.get("enabled", True) or state in {"paused", "completed"}:
            continue
        seconds = _overdue_seconds(job, epoch)
        if seconds is None:
            continue
        if seconds > CRON_OVERDUE_GRACE_SECONDS:
            overdue.append((job, seconds))
        elif seconds < 0:
            upcoming.append((job, -seconds))
    overdue.sort(key=lambda item: item[1], reverse=True)
    upcoming.sort(key=lambda item: item[1])

    facts: list[str] = []
    if total:
        facts.append(
            f"{total} job(s): {active} scheduled, {paused} paused, {completed} completed.")
    else:
        facts.append("No cron jobs are configured.")
    if failed:
        facts.append(f"Failed: {len(failed)} — {_names(failed)}.")
    if overdue:
        oldest = overdue[0][1]
        facts.append(
            f"Overdue: {len(overdue)} — {_names([job for job, _ in overdue])} "
            f"(oldest {_age_text(oldest)} past its next run).")
    if upcoming:
        soon = upcoming[:3]
        items = [
            f"{_bounded_name(job.get('name') or job.get('id') or 'unnamed')} "
            f"in {_age_text(seconds)}"
            for job, seconds in soon
        ]
        facts.append("Next runs: " + "; ".join(items) + ".")

    try:
        interval = 60
        from cron.jobs import TICKER_INTERVAL_SECONDS

        interval = int(TICKER_INTERVAL_SECONDS)
    except Exception:
        pass
    fresh_for = interval * 3 + 20
    if heartbeat is None:
        facts.append("Scheduler heartbeat: unavailable (no heartbeat file or unreadable).")
    elif float(heartbeat) <= fresh_for:
        facts.append(f"Scheduler heartbeat: fresh ({_age_text(heartbeat)} ago).")
    else:
        facts.append(f"Scheduler heartbeat: stale ({_age_text(heartbeat)} ago).")
    if last_error:
        facts.append(f"Last tick error: {_redact(last_error)[:240]}")

    return available("cron", SECTION_TITLES["cron"], facts,
                     source="cron store (read-only)")


# --- kanban ---------------------------------------------------------------

@dataclass(frozen=True)
class KanbanSnapshot:
    board: str
    by_status: Mapping[str, int] = field(default_factory=dict)
    oldest_ready_age_seconds: float | None = None
    failing_tasks: int | None = 0

    @property
    def total(self) -> int:
        return sum(int(count or 0) for count in self.by_status.values())

    @property
    def blocked(self) -> int:
        return int(self.by_status.get("blocked") or 0)


def _default_kanban_snapshot(db_path: Path | None = None) -> KanbanSnapshot:
    from qubit_cli import kanban_db as kb

    path = Path(db_path) if db_path is not None else kb.kanban_db_path()
    if not path.is_file():
        raise SectionUnavailable(
            REASON_CONNECTOR_NOT_CONFIGURED,
            "no kanban board database exists yet")
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        stats = kb.board_stats(conn)
        failing: int | None
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM tasks WHERE status != 'archived' "
                "AND COALESCE(consecutive_failures, 0) > 0").fetchone()
            failing = int(row["n"] if row is not None else 0)
        except sqlite3.OperationalError:
            failing = None
        try:
            board = kb.get_current_board()
        except Exception:
            board = "default"
        return KanbanSnapshot(
            board=str(board or "default"),
            by_status=dict(stats.get("by_status") or {}),
            oldest_ready_age_seconds=stats.get("oldest_ready_age_seconds"),
            failing_tasks=failing,
        )
    finally:
        conn.close()


def collect_kanban(
    now: datetime | None = None,
    *,
    snapshot: KanbanSnapshot | None = None,
    db_path: Path | None = None,
) -> SectionResult:
    """Report board counts, blocked/stale work, and honest unsupported signals."""
    data = snapshot if snapshot is not None else _default_kanban_snapshot(db_path)
    counts = {str(k): int(v or 0) for k, v in dict(data.by_status).items() if int(v or 0)}
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    facts: list[str] = [f"Board: {_bounded_name(data.board)}."]
    if data.total:
        summary = ", ".join(f"{_bounded_name(name)} {count}" for name, count in ordered)
        facts.append(f"{data.total} open task(s): {summary}.")
    else:
        facts.append("No open tasks on this board.")
    facts.append(f"Blocked: {data.blocked}.")
    age = data.oldest_ready_age_seconds
    if age is None:
        facts.append("Ready queue: no ready tasks.")
    elif float(age) >= KANBAN_STALE_SECONDS:
        facts.append(
            f"Stale: the oldest ready task is {_age_text(age)} old "
            f"(over {_age_text(KANBAN_STALE_SECONDS)}).")
    else:
        facts.append(f"Oldest ready task: {_age_text(age)} old.")
    if data.failing_tasks:
        facts.append(f"Tasks with repeated failures: {int(data.failing_tasks)}.")
    elif data.failing_tasks is None:
        facts.append("Repeated failures: not supported by this board schema.")
    facts.append("Overdue: not supported (board tasks have no due-date field).")
    return available("kanban", SECTION_TITLES["kanban"], facts,
                     source="kanban board (read-only)")


# --- memory ---------------------------------------------------------------

def _default_memory_config() -> Mapping[str, Any]:
    from qubit_cli.config import load_config

    loaded = load_config()
    return loaded if isinstance(loaded, Mapping) else {}


def _default_memory_provider_names() -> list[str]:
    from plugins.memory import list_memory_provider_names

    return list(list_memory_provider_names())


def collect_memory(
    now: datetime | None = None,
    *,
    config: Mapping[str, Any] | None = None,
    provider_names: Sequence[str] | None = None,
) -> SectionResult:
    """Report the configured memory provider and whether its module is installed."""
    cfg = dict(config) if config is not None else dict(_default_memory_config())
    section = cfg.get("memory")
    memory_cfg = section if isinstance(section, Mapping) else {}
    provider = str(memory_cfg.get("provider") or "").strip()
    if not provider:
        raise SectionUnavailable(
            REASON_CONNECTOR_NOT_CONFIGURED,
            "no memory provider is configured (set memory.provider)")
    names = (
        sorted({str(name) for name in provider_names if str(name or "").strip()})
        if provider_names is not None
        else sorted(set(_default_memory_provider_names()))
    )
    if provider not in names:
        raise SectionUnavailable(
            REASON_MISSING_CONNECTOR,
            f"configured memory provider {provider!r} is not installed")
    facts = [
        f"Provider: {_bounded_name(provider)}.",
        f"Provider modules found: {len(names)}.",
    ]
    return available("memory", SECTION_TITLES["memory"], facts,
                     source="memory plugin registry (read-only)")


# --- compression ----------------------------------------------------------

@dataclass(frozen=True)
class CompressionSnapshot:
    total_sessions: int = 0
    ineffective: int = 0
    overload: int = 0
    fallback: int = 0
    recovery_armed: int = 0


def _default_state_db_path() -> Path:
    from qubit_constants import get_qubit_home

    return Path(get_qubit_home()) / "state.db"


def _default_compression_snapshot(db_path: Path | None = None) -> CompressionSnapshot:
    path = Path(db_path) if db_path is not None else _default_state_db_path()
    if not path.is_file():
        raise SectionUnavailable(
            REASON_CONNECTOR_NOT_CONFIGURED,
            "the session state store is not present")
    from qubit_state import SessionDB

    db = SessionDB(path, read_only=True)
    try:
        row = db._read_one(
            "SELECT COUNT(*) AS total, "
            "COALESCE(SUM(CASE WHEN compression_ineffective_count > 0 THEN 1 ELSE 0 END), 0) AS ineffective, "
            "COALESCE(SUM(CASE WHEN compression_overload_streak > 0 THEN 1 ELSE 0 END), 0) AS overload, "
            "COALESCE(SUM(CASE WHEN compression_fallback_streak > 0 THEN 1 ELSE 0 END), 0) AS fallback, "
            "COALESCE(SUM(CASE WHEN compression_recovery_deadline > ? THEN 1 ELSE 0 END), 0) AS recovery "
            "FROM sessions",
            (time.time(),),
        )
    except sqlite3.OperationalError as exc:
        if "no such column" in str(exc).lower():
            raise SectionUnavailable(
                REASON_NOT_SUPPORTED,
                "this session store predates the durable compression counters") from exc
        raise
    finally:
        try:
            db.close()
        except Exception:
            pass
    def value(key: str) -> int:
        try:
            return int(row[key] or 0) if row is not None else 0
        except (KeyError, TypeError, ValueError):
            return 0

    return CompressionSnapshot(
        total_sessions=value("total"),
        ineffective=value("ineffective"),
        overload=value("overload"),
        fallback=value("fallback"),
        recovery_armed=value("recovery"),
    )


def collect_compression(
    now: datetime | None = None,
    *,
    snapshot: CompressionSnapshot | None = None,
    db_path: Path | None = None,
) -> SectionResult:
    """Report durable compaction counters; no derived or invented metrics."""
    data = snapshot if snapshot is not None else _default_compression_snapshot(db_path)
    facts = [
        f"Session store: reachable ({int(data.total_sessions)} session(s)).",
        f"Sessions with ineffective-compaction strikes: {int(data.ineffective)}.",
        f"Sessions with overload aborts: {int(data.overload)}.",
        f"Sessions with fallback streaks: {int(data.fallback)}.",
        f"Recovery timers armed: {int(data.recovery_armed)}.",
    ]
    return available("compression", SECTION_TITLES["compression"], facts,
                     source="session store (read-only)")


DEFAULT_COLLECTORS: dict[str, Callable[[datetime], SectionResult]] = {
    "cron": collect_cron,
    "kanban": collect_kanban,
    "memory": collect_memory,
    "compression": collect_compression,
}


def collect_sections(
    collectors: Mapping[str, Callable[[datetime], SectionResult]] | None = None,
    *,
    now: datetime | None = None,
    sections: Sequence[str] = SECTION_ORDER,
) -> tuple[SectionResult, ...]:
    return _collect_sections(
        collectors, now=now, sections=sections,
        titles=SECTION_TITLES, defaults=DEFAULT_COLLECTORS)


MONITOR_OPERATIVE_PROMPT = """\
Write the user's operative status brief for {today}.

The facts below were collected deterministically from the user's local Qubit
surfaces. They are the ONLY factual evidence for the brief.

{facts}

Rules:
- Use only the facts above. Never invent jobs, tasks, sessions, counts, or
  personal context.
- Cover every section in this order: Cron, Kanban board, Memory, Compression.
- When a section is marked unavailable, say that it is unavailable and name the
  typed reason. Never omit a gap silently and never guess what it would have
  said.
- This is a read-only report: do not take, offer, or suggest any action.
- No markdown headers, bullets, or emojis. Keep the brief under 180 words.
"""

MONITOR_OPERATIVE = RecipeSpec(
    id="monitor_operative",
    title="Operative status",
    description=(
        "A short, factual health report over cron jobs, the kanban board, the "
        "memory provider, and compression counters. Sections degrade "
        "independently and nothing is mutated."),
    prompt=MONITOR_OPERATIVE_PROMPT,
    sections=SECTION_ORDER,
    required_capabilities=("cron", "kanban", "memory", "compression"),
    required_tools=("cron:jobs", "kanban:board", "qubit_state:state.db"),
    default_schedule="0 */4 * * *",
    approval_policy="read_only_collectors",
    output_format="short_spoken_brief",
    voice_aliases=(
        "what's my operative status",
        "operative status",
        "monitor my work",
        "status of my work",
        "how is my work doing",
        "monitor operative",
    ),
    section_titles=SECTION_TITLES,
    collectors=DEFAULT_COLLECTORS,
)

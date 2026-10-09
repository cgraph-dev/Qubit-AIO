"""Dashboard cron surface, backed by the REAL Qubit cron store.

The ``/cron`` page (and the SPA's ``/api/cron/*`` calls) must read and write the
SAME store the live scheduler fires from: the vendored cron JSON store at
``<QUBIT_HOME>/cron/jobs.json`` (:mod:`cron.jobs`). That is the store the
``cronjob`` tool, ``qubit cron``, and ``voicebridge.gateway_daemon``'s ticker all
use — the one whose jobs actually fire and deliver to Telegram/WhatsApp.

The legacy ``voicebridge.schedules`` SQLite store (``schedules.db``) is NOT that
store. Serving the page from it showed a parallel, stale job list while the real
jobs never appeared — a read/write split-brain: the jobs the user created (and
received messages from) were invisible on ``/cron``, and the page's create/pause/
delete wrote a store no scheduler ever fires.

This module is the single translation seam: it publishes the Qubit-owned home,
calls the vendored store, and maps its records onto the dashboard ``CronJob`` /
``CronRun`` wire shapes the SPA consumes.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional


def _store(cfg: Any):
    """Import the vendored ``cron.jobs`` store under the Qubit-owned home.

    ``use_qubit_home`` publishes ``QUBIT_HOME`` so the store resolves to the
    profile's ``<home>/cron/jobs.json`` rather than the operator's read-only
    reference home. The vendored store re-resolves its path per call
    (``_current_cron_store``), so importing before the pin is safe.
    """
    from . import qubit_runtime as QR
    QR.ensure_path()
    try:
        QR.use_qubit_home(cfg)
    except Exception:  # noqa: BLE001 - a resolution miss must not break the read
        pass
    from cron import jobs as J  # type: ignore
    return J


def _executions(cfg: Any):
    _store(cfg)  # same home pin
    from cron import executions as E  # type: ignore
    return E


def shape_job(job: Dict[str, Any]) -> Dict[str, Any]:
    """Map a vendored cron job record onto the dashboard ``CronJob`` shape.

    The SPA reads ``schedule.expr`` (interval ``every 30m`` / 5-field cron /
    ISO once), falling back to ``schedule_display``; ``last_run_at`` /
    ``next_run_at`` are ISO strings it normalizes with ``formatCronTime``.
    """
    raw_schedule = job.get("schedule")
    schedule: Dict[str, Any] = raw_schedule if isinstance(raw_schedule, dict) else {}
    raw_repeat = job.get("repeat")
    repeat: Optional[Dict[str, Any]] = raw_repeat if isinstance(raw_repeat, dict) else None
    repeat_out: Optional[Dict[str, Any]] = None
    if repeat is not None:
        repeat_out = {"times": repeat.get("times"),
                      "completed": int(repeat.get("completed") or 0)}
    expr = str(schedule.get("expr") or "")
    run_at = schedule.get("run_at")
    return {
        "id": str(job.get("id", "")),
        "profile": None, "profile_name": None,
        "qubit_home": None, "is_default_profile": True,
        "name": job.get("name"),
        "prompt": job.get("prompt"),
        "script": job.get("script"),
        "skills": job.get("skills") or [],
        "schedule": {
            "kind": schedule.get("kind"),
            "expr": expr,
            "run_at": run_at,
            "minutes": schedule.get("minutes"),
            "display": schedule.get("display") or job.get("schedule_display") or "",
        },
        "schedule_display": job.get("schedule_display") or schedule.get("display") or "",
        "repeat": repeat_out,
        "enabled": bool(job.get("enabled", True)),
        "state": str(job.get("state") or ""),
        "deliver": job.get("deliver"),
        "model": job.get("model"),
        "provider": job.get("provider"),
        "base_url": job.get("base_url"),
        "no_agent": job.get("no_agent"),
        "context_from": job.get("context_from"),
        "enabled_toolsets": job.get("enabled_toolsets"),
        "workdir": job.get("workdir"),
        "last_run_at": job.get("last_run_at"),
        "next_run_at": job.get("next_run_at"),
        "last_status": job.get("last_status"),
        "last_error": job.get("last_error"),
        "last_delivery_error": job.get("last_delivery_error"),
    }


def list_jobs(cfg: Any, *, include_disabled: bool = True) -> List[Dict[str, Any]]:
    """All jobs in the real store, newest-record order preserved."""
    J = _store(cfg)
    return [shape_job(j) for j in J.list_jobs(include_disabled=include_disabled)]


def get_job(cfg: Any, job_id: str) -> Optional[Dict[str, Any]]:
    """One job by id (or case-insensitive name), or ``None``."""
    J = _store(cfg)
    job = J.get_job(str(job_id))
    if job is None:
        try:
            job = J.resolve_job_ref(str(job_id))
        except Exception:  # noqa: BLE001 - an ambiguous name is not a crash
            job = None
    return shape_job(job) if job else None


def _shape_run(row: Dict[str, Any]) -> Dict[str, Any]:
    status = str(row.get("status") or "")
    started = row.get("started_at") or row.get("claimed_at") or ""
    finished = row.get("finished_at")
    error = row.get("error")
    return {
        "id": row.get("id"),
        "job_id": row.get("job_id"),
        "started_at": started,
        "ended_at": finished,
        "finished_at": finished,
        "state": status,
        "code": "ERROR" if error else "OK",
        "detail": str(error or ""),
        "is_active": status in ("claimed", "running"),
        "archived": False,
    }


def runs(cfg: Any, job_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    """Execution history for one job, newest first."""
    E = _executions(cfg)
    rows = E.list_executions(job_id=str(job_id), limit=max(1, min(int(limit), 100)))
    return [_shape_run(r) for r in rows]


def create(cfg: Any, *, name: str = "", prompt: str = "", schedule: str = "",
           deliver: Optional[str] = None, skills: Optional[List[str]] = None,
           provider: Optional[str] = None, model: Optional[str] = None,
           base_url: Optional[str] = None, script: Optional[str] = None,
           no_agent: bool = False, context_from: Any = None,
           enabled_toolsets: Optional[List[str]] = None,
           workdir: Optional[str] = None) -> Dict[str, Any]:
    """Create a job in the real store and return the stored record."""
    J = _store(cfg)
    job = J.create_job(
        prompt=prompt or None,
        schedule=schedule,
        name=name or None,
        deliver=deliver or None,
        skills=skills or None,
        model=model or None,
        provider=provider or None,
        base_url=base_url or None,
        script=script or None,
        no_agent=bool(no_agent),
        context_from=context_from,
        enabled_toolsets=enabled_toolsets,
        workdir=workdir or None,
    )
    return job


def update(cfg: Any, job_id: str, mutation: Dict[str, Any]) -> Dict[str, Any]:
    """Apply an edit-modal mutation to a real job.

    Field edits go through ``update_job``; an ``enabled`` flip is applied with
    ``pause_job``/``resume_job`` so the store's own resume semantics (a slot
    that elapsed while paused stays due) are preserved.
    """
    J = _store(cfg)
    current = J.get_job(str(job_id))
    if current is None:
        current = J.resolve_job_ref(str(job_id))
    if current is None:
        raise KeyError(str(job_id))
    canonical = str(current.get("id"))

    updates: Dict[str, Any] = {}
    for key in ("name", "prompt", "deliver", "provider", "model", "base_url",
                "script", "no_agent", "context_from", "enabled_toolsets",
                "workdir", "skills"):
        if key in mutation:
            updates[key] = mutation[key]
    when = mutation.get("when") or mutation.get("schedule")
    if isinstance(when, str) and when.strip():
        updates["schedule"] = when.strip()
    elif isinstance(mutation.get("schedule"), dict):
        expr = str(mutation["schedule"].get("expr") or "").strip()
        if expr:
            updates["schedule"] = expr

    if updates:
        J.update_job(canonical, updates)

    if "enabled" in mutation and mutation["enabled"] is not None:
        want_enabled = bool(mutation["enabled"])
        if want_enabled != bool(current.get("enabled", True)):
            if want_enabled:
                J.resume_job(canonical)
            else:
                J.pause_job(canonical, reason="Paused from dashboard")

    return J.get_job(canonical) or current


def delete(cfg: Any, job_id: str) -> bool:
    J = _store(cfg)
    return bool(J.remove_job(str(job_id)))


def pause(cfg: Any, job_id: str, reason: str = "Paused from dashboard") -> Optional[Dict[str, Any]]:
    J = _store(cfg)
    return J.pause_job(str(job_id), reason=reason)


def resume(cfg: Any, job_id: str) -> Optional[Dict[str, Any]]:
    J = _store(cfg)
    return J.resume_job(str(job_id))


def trigger(cfg: Any, job_id: str) -> Optional[Dict[str, Any]]:
    """Schedule a job to fire on the next ticker pass (the store's run-now)."""
    J = _store(cfg)
    return J.trigger_job(str(job_id))


def store_path(cfg: Any) -> str:
    """Absolute path of the store backing this surface (for diagnostics/tests)."""
    J = _store(cfg)
    return str(J._current_cron_store().jobs_file)


__all__ = [
    "shape_job", "list_jobs", "get_job", "runs", "create", "update", "delete",
    "pause", "resume", "trigger", "store_path",
]

"""HUD cron collector — Qubit-native port of ``backend/collectors/cron.py``.

Vendored from ``joeynyc/hermes-hudui`` @ be83208f48b7a1c67b7816837c8df71a61ef7edf
Copyright (c) 2026 Hermes HUD Contributors. MIT licensed; the file-by-file
record is ``PROVENANCE.json`` beside this package. Rebranded and re-pointed at
QUBIT state.

What changed from upstream, and why
-----------------------------------
* **Store** — upstream parsed ``<home>/cron/jobs.json`` itself and opened
  ``notepad.db``/``executions.db`` with its own connections. Qubit already owns
  that read seam: :mod:`voicebridge.cron_bridge` publishes the vendor store's
  jobs and execution history from the live scheduler state (the same file the
  ``cronjob`` tool, ``qubit cron`` and the gateway ticker fire from). This
  collector delegates to it rather than re-deriving a job shape, so the HUD tab
  and ``/cron`` can never disagree about a job's state.
* **Unsupported fields dropped** — upstream emitted ``monitor_script`` /
  ``monitor_url`` / ``continuity`` / ``no_agent`` / ``workdir`` and a notepad
  preview. ``cron_bridge`` is the published shape of a Qubit job and exposes
  ``no_agent``/``workdir`` plus the schedule/repeat/last-run fields; the fields
  it does not publish are simply absent here instead of being read behind its
  back out of ``jobs.json``.
* **Pause semantics** — a job's ``state`` comes from the store's own
  ``effective_job_state`` rule (an enabled job never renders as paused). The
  summary counters derive from that same field, so ``active`` + ``paused`` can
  never contradict the rows.

Pure function, JSON-ready dict, never raises: an unreadable store reports
``available: false`` with the reason.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

_RUN_PREVIEW = 3


def _iso(value: Any) -> Optional[str]:
    """Normalize a stored ISO stamp; a non-string value is reported as-is."""
    if value in (None, ""):
        return None
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).isoformat()
        except ValueError:
            return value
    return str(value)


def _shape_run(row: Dict[str, Any]) -> Dict[str, Any]:
    """One execution attempt as ``cron_bridge.runs`` publishes it.

    The bridge's field for the outcome is ``state`` (an execution row's
    ``status`` column), with ``code``/``detail`` carrying the failure; the raw
    ``status`` key is included only when a caller hands one in, so the shape is
    stable for both.
    """
    state = str(row.get("state") or row.get("status") or "")
    return {
        "id": str(row.get("id") or ""),
        "job_id": str(row.get("job_id") or ""),
        "state": state,
        "code": str(row.get("code") or ""),
        "detail": str(row.get("detail") or ""),
        "source": str(row.get("source") or ""),
        "claimed_at": _iso(row.get("claimed_at")),
        "started_at": _iso(row.get("started_at")),
        "finished_at": _iso(row.get("finished_at")),
        "is_active": bool(row.get("is_active"))
        if "is_active" in row else state in ("claimed", "running"),
    }


def _summary(jobs: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "total": len(jobs),
        "active": sum(1 for j in jobs
                      if j["enabled"] and j["state"] == "scheduled"),
        "paused": sum(1 for j in jobs
                      if not j["enabled"] or j["state"] == "paused"),
        "has_errors": any(j.get("last_error") for j in jobs),
    }


def _job_from_bridge(job: Dict[str, Any]) -> Dict[str, Any]:
    raw_schedule = job.get("schedule")
    schedule: Dict[str, Any] = raw_schedule if isinstance(raw_schedule, dict) else {}
    raw_repeat = job.get("repeat")
    repeat: Dict[str, Any] = raw_repeat if isinstance(raw_repeat, dict) else {}
    return {
        "id": str(job.get("id") or ""),
        "name": str(job.get("name") or "unnamed"),
        "prompt": str(job.get("prompt") or ""),
        "script": job.get("script"),
        "schedule_display": str(job.get("schedule_display")
                                or schedule.get("display") or ""),
        "schedule_kind": str(schedule.get("kind") or ""),
        "schedule_expr": str(schedule.get("expr") or ""),
        "enabled": bool(job.get("enabled", True)),
        "state": str(job.get("state") or ""),
        "deliver": job.get("deliver"),
        "repeat_total": repeat.get("times"),
        "repeat_completed": int(repeat.get("completed") or 0),
        "model": job.get("model"),
        "provider": job.get("provider"),
        "skills": list(job.get("skills") or []),
        "workdir": job.get("workdir"),
        "no_agent": bool(job.get("no_agent")),
        "next_run_at": _iso(job.get("next_run_at")),
        "last_run_at": _iso(job.get("last_run_at")),
        "last_status": job.get("last_status"),
        "last_error": job.get("last_error"),
        "last_delivery_error": job.get("last_delivery_error"),
        "recent_runs": [],
    }


def collect_cron(home: Optional[Any] = None, cfg: Any = None,
                 *, run_preview: int = _RUN_PREVIEW) -> Dict[str, Any]:
    """Collect cron jobs and recent execution history from the live store.

    ``cfg`` is the VoiceBridge config threaded into ``cron_bridge`` so the read
    resolves the same home the scheduler fires from; ``home`` is accepted for
    call-site symmetry and forwarded to the bridge's home pin when given.
    """
    out: Dict[str, Any] = {
        "available": False,
        "detail": "",
        "jobs": [],
        "total": 0,
        "active": 0,
        "paused": 0,
        "has_errors": False,
        "updated_at": None,
        "output_dir": "",
    }
    try:
        from .. import cron_bridge
    except Exception as exc:  # noqa: BLE001 - a fork miss is a report, not a crash
        out["detail"] = f"cron bridge unavailable ({exc})"
        return out

    if home is not None:
        # ``cron_bridge._store`` pins the home for *cfg*; an explicit home is
        # published so the vendored store re-resolves to it before the read.
        try:
            import os

            os.environ["QUBIT_HOME"] = str(home)
        except Exception:  # noqa: BLE001 - a pin miss must not block the read
            pass

    try:
        rows = cron_bridge.list_jobs(cfg, include_disabled=True) or []
    except Exception as exc:  # noqa: BLE001
        out["detail"] = f"cron store unreadable ({exc})"
        return out

    jobs: List[Dict[str, Any]] = []
    for row in rows:
        job = _job_from_bridge(row)
        if run_preview > 0 and job["id"]:
            try:
                runs = cron_bridge.runs(cfg, job["id"], limit=run_preview) or []
            except Exception:  # noqa: BLE001 - one job's history is optional
                runs = []
            job["recent_runs"] = [_shape_run(r) for r in runs[:run_preview]]
        jobs.append(job)

    summary = _summary(jobs)
    out.update(
        available=True,
        detail=f"{len(jobs)} job(s) in the live scheduler store",
        jobs=jobs,
        **summary,
    )
    # ``output`` holds each job's run artefacts; published as a plain path so
    # the panel can offer it without inventing a directory that does not exist.
    try:
        from qubit_constants import get_cron_output_dir  # type: ignore

        out["output_dir"] = str(get_cron_output_dir())
    except Exception:  # noqa: BLE001
        out["output_dir"] = ""
    return out


__all__ = ["collect_cron"]

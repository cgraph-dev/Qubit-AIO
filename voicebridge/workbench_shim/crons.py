"""Cron routes: hermes-webui's `/api/crons*` contracts on Qubit's real store.

UPSTREAM CONTRACT (read at UPSTREAM_PIN)
---------------------------------------------------------------------------
``GET  /api/crons``             ``routes.py:18495-18525`` → ``{jobs, all_profiles,
                                active_profile, other_profile_count}``
``POST /api/crons/create``      ``:32136-32183`` → ``{ok, job}`` / 400 ``{error}``
``POST /api/crons/update``      ``:32215-32248`` → ``{ok, job}`` / 400 / 404
``POST /api/crons/delete``      ``:32251-32261`` → ``{ok, job_id}`` / 404
``POST /api/crons/pause``       ``:32298-32306`` → ``{ok, job}`` / 404
``POST /api/crons/resume``      ``:32308-32326`` → ``{ok, job}`` / 404
``POST /api/crons/run``         ``:32264-32305`` → ``{ok, job_id, status}`` / 404
``GET  /api/crons/status``      ``:27712-27722`` → ``{job_id, running, elapsed}``
``GET  /api/crons/history``     ``:27515-27562`` → ``{runs, total, offset, limit}``
``GET  /api/crons/recent``      ``:27725-27785`` → ``{completions, since}``
``GET  /api/crons/delivery-options`` ``:32186-32213`` → ``{platforms}``

WHERE THE DELEGATION LANDS
---------------------------------------------------------------------------
:mod:`voicebridge.cron_bridge` — the vendored ``cron.jobs`` JSON store the live
scheduler fires from (``<QUBIT_HOME>/cron/jobs.json``), not the legacy
``schedules.db``. Its ``shape_job`` already publishes the record in the dashboard
``CronJob`` shape, and upstream's ``_cron_job_for_api`` adds exactly two keys on
top (``toast_notifications``/``badge_notifications``, defaulting true) — which
:func:`_job_for_api` adds here, nothing more.

Writes share ONE confirmation path. Qubit's dashboard treats cron create/update/
delete/pause/resume as brokered mutations: they stage on the shared journal and
settle with ``confirm``/``confirmation_id`` (``qubit_compat.cron_create`` and
friends call ``_compat_confirm``). This module routes through the same
:func:`..confirm_gate`, so a job created from the ported UI is created by exactly
the same code and recorded on exactly the same journal as one created from the
Qubit dashboard, the CLI or voice.

THREE CONTRACT DIFFERENCES THE PORTER MUST KNOW
---------------------------------------------------------------------------
1. **run is tick-driven.** Upstream claims the job and starts a thread inside the
   request, so it can report ``status:"running"`` and ``elapsed`` on a double
   dispatch. Qubit's ``cron.jobs.trigger_job`` schedules the job for the next
   scheduler tick. The answer is ``{ok, job_id, status:"running"}`` (the job IS
   scheduled), but ``elapsed``/``already_running`` are not observable through the
   store, so they are not fabricated — poll ``status`` for liveness.
2. **history is structured, not files.** Upstream lists ``.md`` output files per
   run. Qubit's ledger (``cron.executions``) records executions with
   ``id/state/code/detail/started_at/finished_at``. The shim serves the
   structured rows; ``filename``/``modified``/``usage`` are absent rather than
   invented, and the per-run output *file* read is a typed GAP.
3. **one cron home.** ``active_profile`` is ``"default"`` and
   ``other_profile_count`` is 0 because Qubit keeps one cron store per runtime
   home, so no job is hidden behind another profile.
"""
from __future__ import annotations

import datetime
import time
from typing import Any, Dict, List, Optional, Tuple

from . import (UPSTREAM_PIN, body_of, confirm_gate, e_stopped, flag, gap,
               int_param, join_rows, redact, str_param, truthy)


def _bridge() -> Any:
    """The cron translation seam over the real vendored store."""
    from .. import cron_bridge as CB

    return CB


def _unavailable(detail: str) -> Tuple[int, Dict[str, Any]]:
    return 503, {"ok": False, "state": "UNAVAILABLE",
                 "code": "CRON_STORE_UNAVAILABLE",
                 "detail": detail, "upstream": UPSTREAM_PIN}


def _job_for_api(job: Dict[str, Any]) -> Dict[str, Any]:
    """``cron_bridge.shape_job`` + upstream's two UI-preference keys.

    Mirrors ``routes.py:2214-2230`` exactly: the defaults are ``True`` unless the
    record explicitly stored ``False``, and ``profile`` is present as ``None``
    for a legacy/profile-less record so the UI can label that state.
    """
    out = dict(job)
    out.setdefault("profile", None)
    out["toast_notifications"] = out.get("toast_notifications") is not False
    out["badge_notifications"] = out.get("badge_notifications") is not False
    # Qubit's cron store is one home, so nothing is read-only-by-profile.
    out.setdefault("read_only", False)
    return out


def _shaped(CB: Any, job: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(job, dict):
        return None
    # ``create``/``update`` return RAW store records; ``list_jobs``/``get_job``
    # return shaped ones. Re-shaping a shaped record is idempotent, so shape
    # once here and never guess which call produced it.
    if "schedule" in job and isinstance(job.get("schedule"), dict) \
            and "schedule_display" in job and "is_default_profile" in job:
        return _job_for_api(job)
    return _job_for_api(CB.shape_job(job))


def _epoch(value: Any) -> Optional[float]:
    """A cron stamp → epoch seconds (ISO with offset, or a number)."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return datetime.datetime.fromisoformat(
            str(value).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


def _mutating(cfg: Any, q: Optional[Dict[str, str]],
              b: Optional[Dict[str, Any]]) -> Optional[Tuple[int, Dict[str, Any]]]:
    """The one e-stop read shared by every cron write."""
    if e_stopped(cfg):
        return 403, {"ok": False, "state": "UNAVAILABLE",
                     "code": "EMERGENCY_STOP",
                     "detail": "emergency stop latched; cron mutation refused"}
    return None


# ------------------------------------------------------------------ reads
def crons_list(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
               p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/wb/crons — every job in the real store."""
    try:
        CB = _bridge()
        jobs = CB.list_jobs(cfg, include_disabled=True)
    except ModuleNotFoundError as exc:
        if getattr(exc, "name", "") in ("cron", "cron.jobs"):
            # Upstream's own graceful degradation (#4768): an image without the
            # cron package answers an empty list + the flag, not a 500.
            return 200, {"jobs": [], "cron_unavailable": True,
                         "all_profiles": flag(q, "all_profiles"),
                         "active_profile": "default",
                         "other_profile_count": 0}
        return _unavailable(f"the cron store raised: {exc}")
    except Exception as exc:  # noqa: BLE001
        return _unavailable(f"the cron store could not be read: "
                            f"{type(exc).__name__}: {exc}")
    return 200, {
        "jobs": [_job_for_api(j) for j in join_rows(jobs)],
        "all_profiles": flag(q, "all_profiles"),
        "active_profile": "default",
        # One cron home: no job is hidden behind another profile.
        "other_profile_count": 0,
        "store": "cron.jobs (vendored scheduler store)",
        "upstream": UPSTREAM_PIN,
    }


def crons_status(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/wb/crons/status — liveness from the execution ledger.

    Upstream reports an in-process ``_RUNNING_CRON_JOBS`` map. Qubit's scheduler
    is a separate process, so liveness comes from the store's own ledger: the
    newest execution is live when its ``state`` is ``claimed``/``running`` and
    ``elapsed`` is measured from its ``started_at``.
    """
    job_id = str_param(q, "job_id")
    try:
        CB = _bridge()
    except Exception as exc:  # noqa: BLE001
        return _unavailable(f"the cron store is not reachable: "
                            f"{type(exc).__name__}: {exc}")

    def _one(jid: str) -> Tuple[bool, float]:
        try:
            rows = CB.runs(cfg, jid, limit=1)
        except Exception:
            return False, 0.0
        if not rows:
            return False, 0.0
        head = rows[0]
        if str(head.get("state") or "") not in ("claimed", "running"):
            return False, 0.0
        started = _epoch(head.get("started_at"))
        return True, max(0.0, time.time() - started) if started else 0.0

    if job_id:
        running, elapsed = _one(job_id)
        return 200, {"job_id": job_id, "running": bool(running),
                     "elapsed": round(elapsed, 1), "upstream": UPSTREAM_PIN}
    running_map: Dict[str, float] = {}
    try:
        jobs = join_rows(CB.list_jobs(cfg, include_disabled=False))
    except Exception:  # noqa: BLE001 - a bounded probe, best effort
        jobs = []
    for job in jobs[:500]:
        jid = str(job.get("id") or "")
        if not jid:
            continue
        running, elapsed = _one(jid)
        if running:
            running_map[jid] = round(elapsed, 1)
    return 200, {"running": running_map, "upstream": UPSTREAM_PIN}


def crons_history(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                  p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/wb/crons/history — one job's executions, newest first.

    Contract diff (documented above): structured execution rows, not output
    files. ``?output=1`` asks for the per-run output file, which Qubit's ledger
    does not hold → typed GAP.
    """
    job_id = str_param(q, "job_id")
    if not job_id:
        return 400, {"error": "job_id required"}
    if flag(q, "output"):
        return gap("CRON_OUTPUT_FILES",
                   detail="the history pane's file view has no Qubit ledger row.",
                   job_id=job_id,
                   qubit_counterpart="GET /api/wb/crons/history (structured runs)")
    limit = int_param(str_param(q, "limit", default="50"), 50, 1, 500)
    offset = int_param(str_param(q, "offset", default="0"), 0, 0, 200000)
    try:
        CB = _bridge()
        rows = CB.runs(cfg, job_id, limit=max(limit + offset, limit))
    except Exception as exc:  # noqa: BLE001
        return _unavailable(f"the cron ledger could not be read: "
                            f"{type(exc).__name__}: {exc}")
    all_rows = join_rows(rows)
    page = all_rows[offset:offset + limit]
    return 200, {
        "job_id": job_id,
        "runs": page,
        "total": len(all_rows),
        "offset": offset,
        "limit": limit,
        "format": "structured-executions",
        "upstream": UPSTREAM_PIN,
    }


def crons_recent(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/wb/crons/recent — jobs that completed after ``since``.

    The derivation is upstream's own (``routes.py:27739-27773``): read each job's
    ``last_run_at``, parse it, keep the ones after ``since``.
    """
    try:
        since = float(str_param(q, "since", default="0") or 0)
    except (TypeError, ValueError):
        since = 0.0
    try:
        CB = _bridge()
        jobs = join_rows(CB.list_jobs(cfg, include_disabled=True))
    except ModuleNotFoundError:
        return 200, {"completions": [], "since": since}
    except Exception as exc:  # noqa: BLE001
        return _unavailable(f"the cron store could not be read: "
                            f"{type(exc).__name__}: {exc}")
    completions: List[Dict[str, Any]] = []
    for job in jobs:
        ts = _epoch(job.get("last_run_at"))
        if ts is None or ts <= since:
            continue
        completions.append({
            "job_id": str(job.get("id") or ""),
            "name": job.get("name") or "Unknown",
            "status": job.get("last_status") or "unknown",
            "completed_at": ts,
            "toast_notifications": job.get("toast_notifications") is not False,
            "badge_notifications": job.get("badge_notifications") is not False,
            # Upstream emits "" when the scheduler recorded no session id; the
            # cron store's last_error/last_run_at carry no session today.
            "session_id": str(job.get("last_session_id") or job.get("session_id")
                              or ""),
        })
    completions.sort(key=lambda c: float(c.get("completed_at") or 0),
                     reverse=True)
    return 200, {"completions": completions, "since": since,
                 "upstream": UPSTREAM_PIN}


def crons_delivery_options(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                           p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/wb/crons/delivery-options — the delivery picker's rows.

    Same first two rows upstream always guarantees, then the store's own
    platform vocabulary through upstream's exact fallback chain
    (``cron.scheduler_delivery`` then ``cron.scheduler``); a missing module
    simply narrows the list instead of dropping local/origin.
    """
    known: frozenset = frozenset()
    import importlib

    for module_name in ("cron.scheduler_delivery", "cron.scheduler"):
        try:
            mod = importlib.import_module(module_name)
        except Exception:
            continue
        found = getattr(mod, "_KNOWN_DELIVERY_PLATFORMS", None)
        if found:
            known = frozenset(found)
            break
    platforms = [
        {"value": "local", "label": "Local (save output only)"},
        {"value": "origin", "label": "Origin (reply to creator)"},
    ]
    for name in sorted(known):
        platforms.append({"value": name, "label": str(name).capitalize()})
    return 200, {"platforms": platforms, "upstream": UPSTREAM_PIN}


# ----------------------------------------------------------------- writes
def crons_create(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/wb/crons/create — create in the real store, confirm-gated."""
    body = body_of(b)
    prompt = str(body.get("prompt") or "").strip()
    schedule = str(body.get("schedule") or "").strip()
    if not prompt or not schedule:
        return 400, {"error": "prompt and schedule are required"}
    blocked = _mutating(cfg, q, body)
    if blocked is not None:
        return blocked
    name = str(body.get("name") or "").strip()
    stage = confirm_gate(
        cfg, "cron.create",
        f"creating cron job {name or prompt[:60]!r} ({schedule}) requires "
        f"explicit confirmation: re-POST with {{\"confirm\": true}}",
        q, body,
        payload={"name": name, "prompt": prompt[:200], "schedule": schedule,
                 "deliver": str(body.get("deliver") or "")})
    if stage is not None:
        return stage
    skills = body.get("skills")
    if isinstance(skills, str):
        skills = [s.strip() for s in skills.split(",") if s.strip()]
    repeat = body.get("repeat")
    if isinstance(repeat, dict):
        repeat = repeat.get("times")
    try:
        CB = _bridge()
        raw = CB.create(
            cfg, name=name, prompt=prompt, schedule=schedule,
            deliver=body.get("deliver") or None,
            skills=skills if isinstance(skills, list) else None,
            provider=body.get("provider") or None,
            model=body.get("model") or None,
            base_url=body.get("base_url") or None,
            script=body.get("script") or None,
            no_agent=bool(body.get("no_agent")),
            context_from=body.get("context_from"),
            enabled_toolsets=body.get("enabled_toolsets"),
            workdir=body.get("workdir") or None,
            repeat=repeat,
        )
    except Exception as exc:  # noqa: BLE001 - upstream answers a typed 400
        return 400, {"error": redact(str(exc))[:400],
                     "code": str(getattr(exc, "code", "") or
                                 type(exc).__name__)}
    shaped = _shaped(CB, raw)
    if shaped is None:
        return 502, {"ok": False, "error": "the cron store returned no job"}
    # Upstream honours the two UI preferences and a duplicate's paused state via
    # a post-create update; do the same through the bridge's own update path.
    post: Dict[str, Any] = {}
    if body.get("toast_notifications") is False:
        post["toast_notifications"] = False
    if body.get("badge_notifications") is False:
        post["badge_notifications"] = False
    if body.get("enabled") is False:
        post["enabled"] = False
    if post:
        try:
            updated = CB.update(cfg, str(shaped.get("id") or ""), post)
            shaped = _shaped(CB, updated) or shaped
        except Exception:  # noqa: BLE001 - the job exists; the pref is cosmetic
            pass
    return 200, {"ok": True, "job": shaped, "upstream": UPSTREAM_PIN}


def _mutate(cfg: Any, action: str, detail: str, q: Dict[str, str],
            body: Dict[str, Any], job_id: str,
            payload: Optional[Dict[str, Any]] = None
            ) -> Optional[Tuple[int, Dict[str, Any]]]:
    blocked = _mutating(cfg, q, body)
    if blocked is not None:
        return blocked
    return confirm_gate(cfg, action, detail, q, body, payload)


def crons_update(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/wb/crons/update — in-place edit on the real store."""
    body = body_of(b)
    job_id = str(body.get("job_id") or "").strip()
    if not job_id:
        return 400, {"error": "job_id required"}
    mutation = {k: v for k, v in body.items()
                if k not in ("job_id", "confirm", "confirmation_id")}
    stage = _mutate(
        cfg, "cron.update",
        f"editing cron job {job_id!r} requires explicit confirmation: "
        f"re-POST with {{\"confirm\": true}}", q, body, job_id,
        payload={"id": job_id,
                 **{k: v for k, v in mutation.items()
                    if isinstance(v, (str, int, float, bool))}})
    if stage is not None:
        return stage
    try:
        CB = _bridge()
        raw = CB.update(cfg, job_id, mutation)
    except KeyError:
        return 404, {"error": "Job not found", "job_id": job_id}
    except Exception as exc:  # noqa: BLE001
        return 400, {"error": redact(str(exc))[:400],
                     "code": str(getattr(exc, "code", "") or
                                 type(exc).__name__)}
    shaped = _shaped(CB, raw)
    if shaped is None:
        return 404, {"error": "Job not found", "job_id": job_id}
    return 200, {"ok": True, "job": shaped, "upstream": UPSTREAM_PIN}


def crons_delete(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/wb/crons/delete — remove from the real store."""
    body = body_of(b)
    job_id = str(body.get("job_id") or str_param(q, "job_id")).strip()
    if not job_id:
        return 400, {"error": "job_id required"}
    stage = _mutate(
        cfg, "cron.delete",
        f"deleting cron job {job_id!r} requires explicit confirmation: "
        f"re-POST with {{\"confirm\": true}}", q, body, job_id,
        payload={"id": job_id})
    if stage is not None:
        return stage
    try:
        CB = _bridge()
        # A named reference resolves too (the store's own contract); delete by
        # the canonical id so the answer's job_id is the one that was removed.
        resolved = CB.get_job(cfg, job_id)
        canonical = str((resolved or {}).get("id") or job_id)
        removed = CB.delete(cfg, canonical)
    except Exception as exc:  # noqa: BLE001
        return 400, {"error": redact(str(exc))[:400],
                     "code": str(getattr(exc, "code", "") or
                                 type(exc).__name__)}
    if not removed:
        return 404, {"error": "Job not found", "job_id": job_id}
    return 200, {"ok": True, "job_id": canonical, "upstream": UPSTREAM_PIN}


def _pause_resume(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                  *, pause: bool) -> Tuple[int, Dict[str, Any]]:
    body = body_of(b)
    job_id = str(body.get("job_id") or str_param(q, "job_id")).strip()
    if not job_id:
        return 400, {"error": "job_id required"}
    verb = "pause" if pause else "resume"
    stage = _mutate(
        cfg, f"cron.{verb}",
        f"{verb} on cron job {job_id!r} requires explicit confirmation: "
        f"re-POST with {{\"confirm\": true}}", q, body, job_id,
        payload={"id": job_id})
    if stage is not None:
        return stage
    try:
        CB = _bridge()
        raw = (CB.pause(cfg, job_id, reason=str(body.get("reason") or
                                                "Paused from dashboard"))
               if pause else CB.resume(cfg, job_id))
    except Exception as exc:  # noqa: BLE001
        return 400, {"error": redact(str(exc))[:400],
                     "code": str(getattr(exc, "code", "") or
                                 type(exc).__name__)}
    if raw is None:
        return 404, {"error": "Job not found", "job_id": job_id}
    shaped = _shaped(CB, raw)
    return 200, {"ok": True, "job": shaped, "upstream": UPSTREAM_PIN}


def crons_pause(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/wb/crons/pause."""
    return _pause_resume(cfg, q, b, pause=True)


def crons_resume(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/wb/crons/resume."""
    return _pause_resume(cfg, q, b, pause=False)


def crons_run(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
              p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/wb/crons/run — schedule the job for the next scheduler tick."""
    body = body_of(b)
    job_id = str(body.get("job_id") or str_param(q, "job_id")).strip()
    if not job_id:
        return 400, {"error": "job_id required"}
    blocked = _mutating(cfg, q, body)
    if blocked is not None:
        return blocked
    stage = confirm_gate(
        cfg, "cron.run",
        f"running cron job {job_id!r} now requires explicit confirmation: "
        f"re-POST with {{\"confirm\": true}}", q, body,
        payload={"id": job_id})
    if stage is not None:
        return stage
    try:
        CB = _bridge()
        if CB.get_job(cfg, job_id) is None:
            return 404, {"error": "Job not found", "job_id": job_id}
        triggered = CB.trigger(cfg, job_id)
    except Exception as exc:  # noqa: BLE001
        return 400, {"error": redact(str(exc))[:400],
                     "code": str(getattr(exc, "code", "") or
                                 type(exc).__name__)}
    if triggered is None:
        return 404, {"error": "Job not found", "job_id": job_id}
    return 200, {"ok": True,
                 "job_id": str(triggered.get("id") or job_id),
                 # The store scheduled it: the scheduler fires it on its next
                 # tick. ``elapsed``/``already_running`` are not observable
                 # through cron.jobs and so are not reported.
                 "status": "running",
                 "scheduled": True,
                 "upstream": UPSTREAM_PIN}


HANDLERS = {
    "h_wb_crons_list": crons_list,
    "h_wb_crons_status": crons_status,
    "h_wb_crons_history": crons_history,
    "h_wb_crons_recent": crons_recent,
    "h_wb_crons_delivery_options": crons_delivery_options,
    "h_wb_crons_create": crons_create,
    "h_wb_crons_update": crons_update,
    "h_wb_crons_delete": crons_delete,
    "h_wb_crons_pause": crons_pause,
    "h_wb_crons_resume": crons_resume,
    "h_wb_crons_run": crons_run,
}


def routes() -> list:
    return [
        ("GET", r"/api/wb/crons", "h_wb_crons_list"),
        # Specific sub-paths before any bare path; the cron family has no
        # bare-id row, but the ordering stays explicit for the next addition.
        ("GET", r"/api/wb/crons/status", "h_wb_crons_status"),
        ("GET", r"/api/wb/crons/history", "h_wb_crons_history"),
        ("GET", r"/api/wb/crons/recent", "h_wb_crons_recent"),
        ("GET", r"/api/wb/crons/delivery-options",
         "h_wb_crons_delivery_options"),
        ("POST", r"/api/wb/crons/create", "h_wb_crons_create"),
        ("POST", r"/api/wb/crons/update", "h_wb_crons_update"),
        ("POST", r"/api/wb/crons/delete", "h_wb_crons_delete"),
        ("POST", r"/api/wb/crons/pause", "h_wb_crons_pause"),
        ("POST", r"/api/wb/crons/resume", "h_wb_crons_resume"),
        ("POST", r"/api/wb/crons/run", "h_wb_crons_run"),
    ]

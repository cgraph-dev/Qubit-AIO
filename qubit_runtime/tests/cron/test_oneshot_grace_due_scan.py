"""Due-scan grace gate for one-shot cron jobs.

create_job / update_job / resume_job all reject a one-shot whose run time is
more than ONESHOT_GRACE_SECONDS in the past ("will never fire"), and
``_recoverable_oneshot_run_at`` never recovers such a schedule — but the
due-scan (``_get_due_jobs_locked``) used to dispatch any one-shot whose
*persisted* ``next_run_at`` was in the past, even hours later (gateway down
past the window, host asleep, hand-edited jobs.json). That fired a
wall-clock one-shot late, contradicting the "will never fire" contract.

These tests pin the due-scan grace gate:
  - beyond grace  -> not due; record retired with a diagnostic
  - within grace  -> still due (legitimate catch-up)
  - beyond grace + claim -> not due, but the record is kept (a run may be
    in flight in another process; its mark_job_run must still land)
  - re-triggered  -> due again (the Run button still works)
"""

import json

import pytest
from datetime import datetime, timedelta, timezone

from cron.jobs import (
    get_due_jobs,
    load_jobs,
    save_jobs,
    trigger_job,
)

FIXED_NOW = datetime(2026, 6, 22, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture()
def cron_store(tmp_path, monkeypatch):
    """Redirect cron storage to a temp dir and pin the clock."""
    monkeypatch.setattr("cron.jobs.CRON_DIR", tmp_path / "cron")
    monkeypatch.setattr("cron.jobs.JOBS_FILE", tmp_path / "cron" / "jobs.json")
    monkeypatch.setattr("cron.jobs.OUTPUT_DIR", tmp_path / "cron" / "output")
    monkeypatch.setattr("cron.jobs._qubit_now", lambda: FIXED_NOW)
    return tmp_path


def _oneshot(jid, run_at_dt, *, completed=0, run_claim=None, fire_claim=None):
    return {
        "id": jid,
        "name": jid,
        "prompt": "x",
        "schedule": {"kind": "once", "run_at": run_at_dt.isoformat()},
        "next_run_at": run_at_dt.isoformat(),
        "last_run_at": None,
        "enabled": True,
        "state": "scheduled",
        "repeat": {"times": 1, "completed": completed},
        "deliver": "local",
        "run_claim": run_claim,
        "fire_claim": fire_claim,
    }


class TestOneShotGraceDueScan:
    def test_stale_beyond_grace_not_due_and_retired_with_diagnostic(self, cron_store):
        stale = _oneshot("stale", FIXED_NOW - timedelta(hours=3))
        save_jobs([stale])

        due = get_due_jobs()

        assert [d["id"] for d in due] == []
        # Record is retired (removed) so it stops being scanned — with a
        # diagnostic left behind so the miss is operator-visible.
        assert [j["id"] for j in load_jobs()] == []
        out_dir = cron_store / "cron" / "output" / "stale"
        diag = list(out_dir.glob("*.md")) if out_dir.exists() else []
        assert diag, "expected a diagnostic file for the retired stale one-shot"

    def test_within_grace_still_due(self, cron_store):
        recent = _oneshot("recent", FIXED_NOW - timedelta(seconds=60))
        save_jobs([recent])

        due = get_due_jobs()
        assert [d["id"] for d in due] == ["recent"]
        # Still stored (dispatch proceeds normally).
        assert [j["id"] for j in load_jobs()] == ["recent"]

    def test_stale_with_claim_skipped_but_record_kept(self, cron_store):
        # A (possibly stale) run_claim means a run may still be in flight in
        # another process — the due-scan must not fire OR retire the record,
        # so mark_job_run can still land.
        claimed = _oneshot(
            "claimed",
            FIXED_NOW - timedelta(hours=3),
            completed=1,
            run_claim={"at": (FIXED_NOW - timedelta(hours=3)).isoformat(), "by": "other"},
        )
        save_jobs([claimed])

        due = get_due_jobs()
        assert [d["id"] for d in due] == []
        # Record preserved (not retired) despite being beyond grace.
        assert [j["id"] for j in load_jobs()] == ["claimed"]

    def test_retriggered_stale_oneshot_is_due(self, cron_store):
        # A user can still explicitly re-run a stale one-shot: trigger_job
        # sets next_run_at=now, which is inside the grace window -> due.
        stale = _oneshot("stale", FIXED_NOW - timedelta(hours=3))
        save_jobs([stale])
        trigger_job("stale")

        due = get_due_jobs()
        assert [d["id"] for d in due] == ["stale"]

    def test_recurring_jobs_unaffected(self, cron_store):
        # The grace gate is once-kind only; a stale recurring job keeps its
        # existing execute-now catch-up behavior.
        interval = {
            "id": "int",
            "name": "int",
            "prompt": "x",
            "schedule": {"kind": "interval", "minutes": 60},
            "next_run_at": (FIXED_NOW - timedelta(hours=2)).isoformat(),
            "last_run_at": None,
            "enabled": True,
            "state": "scheduled",
            "repeat": {"times": None, "completed": 0},
            "deliver": "local",
        }
        save_jobs([interval])
        due = get_due_jobs()
        # Recurring stale handling still executes once now (existing behavior).
        assert [d["id"] for d in due] == ["int"]


class TestUpdateDispatchPause:
    """A one-shot due while a gateway was drained for an update is skipped, not missed.

    The drain can outlast ``ONESHOT_GRACE_SECONDS`` (an in-flight worker, a slow pull), and cron
    dispatch is paused for its whole duration. The gateway records that window; the relaunched
    gateway must fire the job once instead of retiring it.
    """

    def _window(self, cron_store, *, due_at, since, until):
        path = cron_store / "cron" / ".dispatch-pause.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"since": since.timestamp(), "until": until.timestamp()}),
                        encoding="utf-8")
        return path

    def test_due_inside_an_update_pause_runs_once_and_clears_the_window(self, cron_store):
        from cron import dispatch_pause

        due_at = FIXED_NOW - timedelta(minutes=10)  # far beyond the 120s one-shot grace
        save_jobs([_oneshot("paused", due_at)])
        path = self._window(cron_store, due_at=due_at,
                            since=due_at - timedelta(seconds=30), until=due_at + timedelta(seconds=60))

        due = get_due_jobs()

        assert [d["id"] for d in due] == ["paused"], "the update pause ate the one-shot"
        assert [j["id"] for j in load_jobs()] == ["paused"]
        assert not dispatch_pause.marker_path(cron_store / "cron").exists(), "the used window must not linger"

    def test_a_run_outside_the_window_is_still_retired(self, cron_store):
        due_at = FIXED_NOW - timedelta(hours=3)
        save_jobs([_oneshot("ancient", due_at)])
        # A pause that ended hours before this job came due covers nothing.
        self._window(cron_store, due_at=due_at,
                     since=due_at - timedelta(hours=1), until=due_at - timedelta(minutes=30))

        assert get_due_jobs() == []
        assert load_jobs() == [], "a job outside the pause must keep the will-never-fire contract"

    def test_the_window_binds_its_end_and_never_covers_hours_later(self, cron_store):
        from cron import dispatch_pause

        cron_dir = cron_store / "cron"
        dispatch_pause.note(cron_dir, now=1000.0)
        since, until = dispatch_pause.window(cron_dir)
        assert since == 1000.0
        assert until <= 1000.0 + dispatch_pause.MAX_WINDOW_SECONDS
        assert dispatch_pause.covers(cron_dir, 1100.0)
        assert not dispatch_pause.covers(cron_dir, until + 1)

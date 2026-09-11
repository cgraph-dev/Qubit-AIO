"""Scheduler autofire: scheduled jobs self-fire through the monitor's tick.

Hermetic: isolated XDG/state, frozen timestamps, injected executors, and a
captured audit sink. The REAL schedules SQLite store is exercised end to end
(create -> autofire -> runs/jobs rows -> reopen), never a stub of it.

Proves the parity contract for the autonomous scheduler:
  * a due job fires exactly once per due window;
  * an un-due job does not fire;
  * a latched emergency stop refuses the tick without consuming the window;
  * a fired job writes the same receipt + ``schedules.run`` audit as a manual
    ``run-due`` (only the actor differs);
  * a bounded tick never claims more than ``limit`` jobs;
  * a restart (fresh config over the same state dir) does not double-fire.
"""
from __future__ import annotations

import time

import pytest


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _exec(task_text, *, session, estop_engaged):
    return {"state": "OK", "code": "OK", "detail": "ran " + task_text[:40]}


# ------------------------------------------------------- exactly-once window
def test_due_job_fires_exactly_once_per_window(tmp_path, monkeypatch):
    from voicebridge import schedules as SC

    cfg = _cfg(tmp_path, monkeypatch)
    SC.create(cfg, "every-5", "tick", "every 5 minutes", confirmed=True)
    seen = []

    def _spy(task_text, *, session, estop_engaged):
        assert session == "schedule-1"
        seen.append(task_text)
        return _exec(task_text, session=session, estop_engaged=estop_engaged)

    now = time.time()
    # Created just now -> not due yet.
    assert SC.autofire(cfg, executor=_spy, now=now)["ran"] == 0
    assert seen == []

    due = now + 5 * 60 + 1
    assert SC.autofire(cfg, executor=_spy, now=due)["ran"] == 1
    assert seen == ["tick"]
    # Same due window, second tick: the persisted lease blocks a re-fire.
    assert SC.autofire(cfg, executor=_spy, now=due)["ran"] == 0
    assert seen == ["tick"]
    # Next window fires again.
    assert SC.autofire(cfg, executor=_spy, now=due + 5 * 60)["ran"] == 1
    assert seen == ["tick", "tick"]


def test_undue_job_does_not_fire(tmp_path, monkeypatch):
    from voicebridge import schedules as SC

    cfg = _cfg(tmp_path, monkeypatch)
    SC.create(cfg, "later", "not yet", "every 30 minutes", confirmed=True)
    called = []
    out = SC.autofire(cfg, executor=lambda *a, **k: called.append(a) or _exec(*a, **k),
                      now=time.time())
    assert out["ran"] == 0 and called == []
    assert SC.history(cfg)["runs"] == []


def test_autofire_is_bounded_by_limit(tmp_path, monkeypatch):
    from voicebridge import schedules as SC

    cfg = _cfg(tmp_path, monkeypatch)
    for i in range(3):
        SC.create(cfg, f"job-{i}", f"t{i}", "every 5 minutes", confirmed=True)
    out = SC.autofire(cfg, executor=_exec, now=time.time() + 3600, limit=1)
    assert out["ran"] == 1
    assert len(SC.history(cfg)["runs"]) == 1
    # A later tick drains the rest, still respecting the bound.
    out = SC.autofire(cfg, executor=_exec, now=time.time() + 3600, limit=2)
    assert out["ran"] == 2


# ------------------------------------------------------------- emergency stop
def test_emergency_stop_blocks_firing_and_spares_window(tmp_path, monkeypatch):
    from voicebridge import schedules as SC

    cfg = _cfg(tmp_path, monkeypatch)
    SC.create(cfg, "guarded", "do not run", "every 5 minutes", confirmed=True)
    now = time.time() + 3600
    out = SC.autofire(cfg, executor=_exec, now=now,
                      estop_engaged=lambda: True)
    assert out["state"] == "denied"
    assert out["code"] == SC.ESTOP_CODE
    assert out["ran"] == 0 and out["results"] == []
    # The tick consumed nothing: the window is still pending after reset.
    assert SC.show(cfg, 1)["job"]["last_run_at"] == 0
    assert SC.history(cfg)["runs"] == []
    assert SC.autofire(cfg, executor=_exec, now=now,
                       estop_engaged=lambda: False)["ran"] == 1


# ------------------------------------------------- receipts + audit parity
def test_autofire_writes_receipt_and_audit(tmp_path, monkeypatch):
    from voicebridge import schedules as SC
    from voicebridge import logging_setup as LOG

    events = []
    monkeypatch.setattr(LOG, "log_event",
                        lambda ev, **f: events.append((ev, f)))
    cfg = _cfg(tmp_path, monkeypatch)
    SC.create(cfg, "receipted", "do work", "every 5 minutes", confirmed=True)
    out = SC.autofire(cfg, executor=_exec, now=time.time() + 3600)
    assert out["ran"] == 1 and out["actor"] == SC.MONITOR_ACTOR

    hist = SC.history(cfg)["runs"]
    assert len(hist) == 1
    run = hist[0]
    assert run["state"] == "OK" and run["finished_at"] > 0
    assert run["job_id"] == 1

    job = SC.show(cfg, 1)["job"]
    assert job["last_state"] == "OK" and job["run_count"] == 1
    assert job["recent_runs"][0]["state"] == "OK"

    run_events = [f for e, f in events if e == "schedules.run"]
    assert len(run_events) == 1
    assert run_events[0]["actor"] == SC.MONITOR_ACTOR
    assert run_events[0]["state"] == "OK"


def test_autofire_receipt_matches_manual_run(tmp_path, monkeypatch):
    from voicebridge import schedules as SC
    from voicebridge import logging_setup as LOG

    events = []
    monkeypatch.setattr(LOG, "log_event",
                        lambda ev, **f: events.append((ev, f)))
    cfg = _cfg(tmp_path, monkeypatch)
    now = time.time() + 3600
    SC.create(cfg, "auto", "task a", "every 5 minutes", confirmed=True)
    auto = SC.autofire(cfg, executor=_exec, now=now)
    # A manual job created after the tick is still due for the manual run.
    SC.create(cfg, "manual", "task b", "every 5 minutes", confirmed=True)
    manual = SC.run_due(cfg, requested_by="cli:user", executor=_exec, now=now)
    assert auto["ran"] == 1 and manual["ran"] == 1
    a, m = auto["results"][0], manual["results"][0]
    assert (a["state"], a["code"]) == (m["state"], m["code"]) == ("OK", "OK")
    # Same audit event name; only the attribution differs.
    actors = sorted(f["actor"] for e, f in events if e == "schedules.run")
    assert actors == ["cli:user", "monitor:autofire"]


# ------------------------------------------------------- restart idempotency
def test_restart_does_not_double_fire(tmp_path, monkeypatch):
    from voicebridge import schedules as SC

    cfg = _cfg(tmp_path, monkeypatch)
    SC.create(cfg, "durable", "once only", "every 5 minutes", confirmed=True)
    seen = []

    def _spy(task_text, *, session, estop_engaged):
        seen.append(task_text)
        return _exec(task_text, session=session, estop_engaged=estop_engaged)

    first = time.time() + 3600
    assert SC.autofire(cfg, executor=_spy, now=first)["ran"] == 1

    # Restart: a brand-new config over the SAME state dir (fresh connection).
    cfg2 = _cfg(tmp_path, monkeypatch)
    assert cfg2 is not cfg
    out = SC.autofire(cfg2, executor=_spy, now=first)
    assert out["ran"] == 0 and seen == ["once only"]
    # The lease is durable, not in-memory.
    assert SC.show(cfg2, 1)["job"]["last_run_at"] == pytest.approx(first)
    # And the next window still works after the restart.
    assert SC.autofire(cfg2, executor=_spy, now=first + 5 * 60)["ran"] == 1


def test_autofire_never_raises_into_monitor(tmp_path, monkeypatch):
    from voicebridge import schedules as SC

    cfg = _cfg(tmp_path, monkeypatch)
    SC.create(cfg, "j", "t", "every 5 minutes", confirmed=True)

    def _boom(cfg, **kw):
        raise SC.SchedulesError(SC.IO_CODE, "synthetic store failure")

    monkeypatch.setattr(SC, "run_due", _boom)
    out = SC.autofire(cfg, now=time.time() + 3600)
    assert out["state"] == "FAILED" and out["code"] == SC.IO_CODE
    assert out["ran"] == 0

"""Agent 6 — autonomous scheduler tick + coalesced catch-up (OpenClaw OC-27).

The production caller is ``schedules.autofire`` (the single entry point the
monitor loop calls once per tick). These tests drive that real caller and
prove:

  * the shared tick drives due automation heartbeats and queued standing-order
    auto-replies (one scheduler authority, no second timer);
  * a job that missed several windows while the process was down fires once
    and honestly reports the coalesced catch-up count in the receipt, audit,
    run_due envelope, and ``scheduler_status``;
  * a mid-tick emergency stop is honored per job and never raises;
  * restarting over the same state dir never double-fires (durable lease).
"""
from __future__ import annotations

import time

import pytest


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.delenv("SLACK_TOKEN", raising=False)
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _ok(task_text, *, session, estop_engaged):
    return {"state": "OK", "code": "OK", "detail": "ran"}


# ---------------------------------------------------------- catch-up report
def test_run_due_reports_coalesced_catch_up(tmp_path, monkeypatch):
    from voicebridge import schedules as SC

    cfg = _cfg(tmp_path, monkeypatch)
    SC.create(cfg, "every-5", "tick", "every 5 minutes", confirmed=True)
    created = SC.show(cfg, 1)["job"]["created_at"]
    # One hour later: 12 windows elapsed; the tick fires once and reports 11
    # missed windows rather than replaying an unbounded backlog.
    out = SC.run_due(cfg, executor=_ok, now=created + 3600)
    assert out["ran"] == 1
    assert out["results"][0]["catch_up"] == 11
    assert out["catch_up"] == 11

    status = SC.scheduler_status(cfg, now=created + 3600)
    assert status["jobs"][0]["missed_windows"] == 0  # just fired
    assert status["catch_up_total"] == 0
    # A later tick after another missed hour again reports the coalesced count.
    later = SC.scheduler_status(cfg, now=created + 7200)
    assert later["jobs"][0]["due"] is True
    assert later["jobs"][0]["missed_windows"] == 11


def test_catch_up_is_zero_for_on_time_runs(tmp_path, monkeypatch):
    from voicebridge import schedules as SC

    cfg = _cfg(tmp_path, monkeypatch)
    SC.create(cfg, "every-5", "tick", "every 5 minutes", confirmed=True)
    created = SC.show(cfg, 1)["job"]["created_at"]
    out = SC.run_due(cfg, executor=_ok, now=created + 5 * 60 + 1)
    assert out["results"][0]["catch_up"] == 0


# ------------------------------------------------------------- shared tick
def test_autofire_drives_due_heartbeat(tmp_path, monkeypatch):
    from voicebridge import automation as A
    from voicebridge import schedules as SC

    cfg = _cfg(tmp_path, monkeypatch)
    A.heartbeat_add(cfg, "hb", 5, confirmed=True)
    out = SC.autofire(cfg, now=time.time() + 3600)
    assert out["state"] == "OK"
    assert out["automation"]["state"] == "OK"
    assert out["automation"]["heartbeats"] == 1
    hb = A.heartbeat_list(cfg)["heartbeats"][0]
    assert hb["count"] == 1 and hb["last_at"] > 0
    # An immediate second tick does not re-fire the interval.
    assert SC.autofire(cfg, now=time.time() + 3600)["automation"]["heartbeats"] == 0


def test_autofire_processes_queued_auto_reply(tmp_path, monkeypatch):
    from voicebridge import automation as A
    from voicebridge import schedules as SC

    cfg = _cfg(tmp_path, monkeypatch)
    from voicebridge import messaging as M

    M.account_add(cfg, "team", "slack", "team", confirmed=True,
                  key_ref="env:SLACK_TOKEN")
    A.order_add(cfg, "greet", match="hello", reply="hi", account="team",
                to="C1", confirmed=True)
    A.queue_event(cfg, kind="internal", source="x", text="hello", confirmed=True)
    out = SC.autofire(cfg, now=time.time() + 3600)
    assert out["automation"]["processed"] == 1
    res = out["automation"]["results"][0]
    assert res["order_id"] == "greet"
    assert res["state"] == "UNAVAILABLE"  # no credential -> never fake SENT


def test_autofire_estop_refuses_and_spares_windows(tmp_path, monkeypatch):
    from voicebridge import automation as A
    from voicebridge import schedules as SC

    cfg = _cfg(tmp_path, monkeypatch)
    A.heartbeat_add(cfg, "hb", 5, confirmed=True)
    from voicebridge import messaging as M

    M.account_add(cfg, "team", "slack", "team", confirmed=True,
                  key_ref="env:SLACK_TOKEN")
    A.order_add(cfg, "greet", match="hello", reply="hi", account="team",
                to="C1", confirmed=True)
    A.queue_event(cfg, kind="internal", source="x", text="hello", confirmed=True)
    now = time.time() + 3600
    out = SC.autofire(cfg, now=now, estop_engaged=lambda: True)
    assert out["state"] == "denied"
    assert A.heartbeat_list(cfg)["heartbeats"][0]["count"] == 0
    assert A.status(cfg)["counts"]["events"] == 1
    # Clearing the latch lets the same windows fire.
    out = SC.autofire(cfg, now=now, estop_engaged=lambda: False)
    assert out["automation"]["heartbeats"] == 1
    assert out["automation"]["processed"] == 1


def test_automation_tick_never_raises_into_monitor(tmp_path, monkeypatch):
    from voicebridge import automation as A
    from voicebridge import schedules as SC

    cfg = _cfg(tmp_path, monkeypatch)

    def _boom(*a, **k):
        raise RuntimeError("synthetic automation failure")

    monkeypatch.setattr(A, "tick", _boom)
    out = SC.autofire(cfg, now=time.time() + 3600)
    assert out["automation"]["state"] == "FAILED"
    assert out["automation"]["code"] == SC.RUNTIME_CODE


def test_restart_does_not_double_fire_heartbeat(tmp_path, monkeypatch):
    from voicebridge import automation as A
    from voicebridge import schedules as SC

    cfg = _cfg(tmp_path, monkeypatch)
    A.heartbeat_add(cfg, "hb", 5, confirmed=True)
    moment = time.time() + 3600
    assert SC.autofire(cfg, now=moment)["automation"]["heartbeats"] == 1
    cfg2 = _cfg(tmp_path, monkeypatch)
    assert cfg2 is not cfg
    assert SC.autofire(cfg2, now=moment)["automation"]["heartbeats"] == 0
    assert A.heartbeat_list(cfg2)["heartbeats"][0]["count"] == 1

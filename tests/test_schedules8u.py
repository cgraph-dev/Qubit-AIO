"""Packet 8U — native scheduled jobs (user cron): store, due, run, parity.

Hermetic: isolated XDG/state, frozen timestamps, injected executors —
the default agent-runtime executor is only proven to degrade honestly
(typed UNAVAILABLE, never fake success) without a provisioned runtime.
"""
from __future__ import annotations

import io
import json
import threading
import time
from contextlib import redirect_stdout

import pytest


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _ctx(cfg, session="voice", source="user"):
    from voicebridge import voice_commands as VC

    return VC.make_ctx(cfg, session=session, source=source)


def _t0():
    return time.time()


def _ok(task_text, *, session, estop_engaged):
    return {"state": "OK", "code": "OK", "detail": "did: " + task_text[:40]}


# ------------------------------------------------------------- parsing
def test_parse_when_shapes():
    from voicebridge import schedules as SC

    assert SC.parse_when("every 30 minutes")["interval_min"] == 30
    assert SC.parse_when("every 2 hours")["interval_min"] == 120
    assert SC.parse_when("daily 9am")["hour"] == 9
    assert SC.parse_when("daily 5:30pm") == {
        "kind": "daily", "hour": 17, "minute": 30}
    once = SC.parse_when("once in 10 minutes", now=1000.0)
    assert once == {"kind": "once", "at_ts": 1600.0}
    for bad in ("", "sometime", "every 2 minutes", "every 3 weeks",
                "daily 25:00", "once in 0 minutes"):
        with pytest.raises(SC.SchedulesError) as exc:
            SC.parse_when(bad)
        assert exc.value.code == SC.BAD_INPUT_CODE, bad


def test_extract_when_splits_task():
    from voicebridge import schedules as SC

    task, when = SC.extract_when("check inbox every 30 minutes")
    assert (task, when) == ("check inbox", "every 30 minutes")
    task, when = SC.extract_when("daily 9am")
    assert task == "" and when == "daily 9am"
    task, when = SC.extract_when("buy milk")
    assert (task, when) == ("buy milk", None)


# ----------------------------------------------------------------- CRUD
def test_create_list_show_pause_resume_delete(tmp_path, monkeypatch):
    from voicebridge import schedules as SC

    cfg = _cfg(tmp_path, monkeypatch)
    with pytest.raises(SC.SchedulesError) as exc:
        SC.create(cfg, "x", "y", "every 30 minutes", confirmed=False)
    assert exc.value.code == SC.CONFIRM_CODE
    created = SC.create(cfg, "Morning check", "check inbox",
                        "every 30 minutes", confirmed=True)
    job = created["job"]
    assert job["kind"] == "interval" and job["enabled"] is True
    assert SC.list_jobs(cfg)["jobs"][0]["id"] == job["id"]
    assert SC.show(cfg, job["id"])["job"]["name"] == "Morning check"
    assert SC.pause(cfg, job["id"], confirmed=True)["job"]["enabled"] is False
    assert SC.list_jobs(cfg, include_disabled=False)["jobs"] == []
    assert SC.resume(cfg, job["id"], confirmed=True)["job"]["enabled"] is True
    deleted = SC.delete(cfg, job["id"], confirmed=True)
    assert deleted["deleted"] == job["id"]
    assert deleted["history_retained"] is True
    assert SC.list_jobs(cfg)["jobs"] == []
    with pytest.raises(SC.SchedulesError) as exc:
        SC.show(cfg, job["id"])
    assert exc.value.code == SC.NOT_FOUND_CODE


def test_create_validation(tmp_path, monkeypatch):
    from voicebridge import schedules as SC

    cfg = _cfg(tmp_path, monkeypatch)
    for name, task, when in (("", "t", "every 30 minutes"),
                             ("n", "", "every 30 minutes"),
                             ("n", "t", "sometime"),
                             ("n", "t", "every 2 minutes")):
        with pytest.raises(SC.SchedulesError):
            SC.create(cfg, name, task, when, confirmed=True)
    assert SC.list_jobs(cfg)["jobs"] == []


# ------------------------------------------------------------------- due
def test_run_due_interval_and_lease(tmp_path, monkeypatch):
    from voicebridge import schedules as SC

    cfg = _cfg(tmp_path, monkeypatch)
    seen = []
    SC.create(cfg, "half-hour", "do the thing", "every 30 minutes",
              confirmed=True)
    # Not due immediately after creation.
    out = SC.run_due(cfg, executor=lambda *a, **k: seen.append(a) or _ok(*a, **k),
                     now=_t0())
    assert out["ran"] == 0
    # Due 31 minutes later; second call at the same instant runs nothing
    # (the first call holds the lease).
    out = SC.run_due(cfg, executor=lambda *a, **k: _ok(*a, **k),
                     now=_t0() + 31 * 60)
    assert out["ran"] == 1
    assert out["results"][0]["state"] == "OK"
    out = SC.run_due(cfg, executor=lambda *a, **k: _ok(*a, **k),
                     now=_t0() + 31 * 60)
    assert out["ran"] == 0
    hist = SC.history(cfg)
    assert len(hist["runs"]) == 1 and hist["runs"][0]["state"] == "OK"


def test_run_due_once_and_daily(tmp_path, monkeypatch):
    from voicebridge import schedules as SC

    cfg = _cfg(tmp_path, monkeypatch)
    SC.create(cfg, "one-shot", "do once", "once in 10 minutes",
              confirmed=True)
    out = SC.run_due(cfg, executor=lambda *a, **k: _ok(*a, **k),
                     now=_t0())
    assert out["ran"] == 0
    out = SC.run_due(cfg, executor=lambda *a, **k: _ok(*a, **k),
                     now=_t0() + 11 * 60)
    assert out["ran"] == 1
    out = SC.run_due(cfg, executor=lambda *a, **k: _ok(*a, **k),
                     now=_t0() + 3600)
    assert out["ran"] == 0, "once-jobs never repeat"


def test_run_due_daily(tmp_path, monkeypatch):
    from voicebridge import schedules as SC

    cfg = _cfg(tmp_path, monkeypatch)
    SC.create(cfg, "midnight", "roll the logs", "daily 00:00",
              confirmed=True)
    out = SC.run_due(cfg, executor=lambda *a, **k: _ok(*a, **k),
                     now=_t0())
    assert out["ran"] == 1, out
    out = SC.run_due(cfg, executor=lambda *a, **k: _ok(*a, **k),
                     now=_t0())
    assert out["ran"] == 0, "daily jobs run once per day"


def test_run_due_failure_is_typed_and_nonblocking(tmp_path, monkeypatch):
    from voicebridge import schedules as SC

    cfg = _cfg(tmp_path, monkeypatch)
    SC.create(cfg, "bad", "boom", "every 5 minutes", confirmed=True)
    SC.create(cfg, "good", "fine", "every 5 minutes", confirmed=True)

    def _exec(task_text, *, session, estop_engaged):
        if task_text == "boom":
            raise RuntimeError("synthetic executor failure")
        return _ok(task_text, session=session,
                   estop_engaged=estop_engaged)

    out = SC.run_due(cfg, executor=_exec, now=_t0() + 3600)
    assert out["ran"] == 2
    by_name = {r["name"]: r for r in out["results"]}
    assert by_name["bad"]["state"] == "FAILED"
    assert by_name["good"]["state"] == "OK"
    hist = SC.history(cfg)
    assert {r["state"] for r in hist["runs"]} == {"FAILED", "OK"}


def test_run_due_disabled_skipped_and_history_survives_delete(tmp_path,
                                                             monkeypatch):
    from voicebridge import schedules as SC

    cfg = _cfg(tmp_path, monkeypatch)
    job = SC.create(cfg, "paused", "nap", "every 5 minutes",
                    confirmed=True)["job"]
    SC.pause(cfg, job["id"], confirmed=True)
    out = SC.run_due(cfg, executor=lambda *a, **k: _ok(*a, **k),
                     now=_t0() + 3600)
    assert out["ran"] == 0
    SC.resume(cfg, job["id"], confirmed=True)
    out = SC.run_due(cfg, executor=lambda *a, **k: _ok(*a, **k),
                     now=_t0() + 3600)
    assert out["ran"] == 1
    SC.delete(cfg, job["id"], confirmed=True)
    assert len(SC.history(cfg)["runs"]) == 1


def test_run_due_concurrent_claims_run_once(tmp_path, monkeypatch):
    from voicebridge import schedules as SC

    cfg = _cfg(tmp_path, monkeypatch)
    SC.create(cfg, "race", "run me", "every 5 minutes", confirmed=True)
    moment = _t0() + 3600
    barrier = threading.Barrier(2)
    results = []

    def _one():
        barrier.wait(timeout=30)
        results.append(SC.run_due(
            cfg, executor=lambda *a, **k: _ok(*a, **k), now=moment))

    threads = [threading.Thread(target=_one) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert sorted(r["ran"] for r in results) == [0, 1], results


def test_run_due_default_executor_degrades_honestly(tmp_path, monkeypatch):
    from voicebridge import schedules as SC

    cfg = _cfg(tmp_path, monkeypatch)
    SC.create(cfg, "live", "do it live", "every 5 minutes", confirmed=True)
    out = SC.run_due(cfg, now=_t0() + 3600)
    assert out["ran"] == 1
    state = out["results"][0]["state"]
    assert state in ("OK", "UNAVAILABLE"), out
    if state == "UNAVAILABLE":
        assert out["results"][0]["code"] in (
            "RUNTIME_DISABLED", "HERMES_DISABLED", "EMERGENCY_STOP"), out


# ------------------------------------------------------------------- CLI
class _A:
    def __init__(self, cmd, **kw):
        self.schedules_command = cmd
        self.__dict__.update(**{"name": "", "task_text": "", "when": "",
                                "job_id": 0, "limit": 30,
                                "active_only": False, "confirm": False,
                                "dry_run": False, **kw})


def _run(cfg, args):
    from voicebridge import cli as CLI

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = CLI._cmd_schedules(cfg, args)
    return rc, json.loads(buf.getvalue())


def test_cli_crud_and_gates(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    rc, body = _run(cfg, _A("create", name="n", task_text="t",
                            when="every 30 minutes"))
    assert rc == 3 and body["state"] == "denied"
    rc, body = _run(cfg, _A("create", name="n", task_text="t",
                            when="every 30 minutes", dry_run=True))
    assert rc == 0 and body["state"] == "preview"
    rc, body = _run(cfg, _A("create", name="Morning", task_text="check inbox",
                            when="every 30 minutes", confirm=True))
    assert rc == 0 and body["job"]["kind"] == "interval"
    jid = body["job"]["id"]
    rc, body = _run(cfg, _A("list"))
    assert rc == 0 and len(body["jobs"]) == 1
    rc, body = _run(cfg, _A("show", job_id=jid))
    assert rc == 0 and body["job"]["id"] == jid
    rc, body = _run(cfg, _A("pause", job_id=jid, confirm=True))
    assert rc == 0 and body["job"]["enabled"] is False
    rc, body = _run(cfg, _A("resume", job_id=jid, confirm=True))
    assert rc == 0 and body["job"]["enabled"] is True
    rc, body = _run(cfg, _A("history"))
    assert rc == 0 and body["runs"] == []
    rc, body = _run(cfg, _A("run-due"))
    assert rc == 0 and body["ran"] == 0
    rc, body = _run(cfg, _A("delete", job_id=jid, confirm=True))
    assert rc == 0 and body["deleted"] == jid


# ------------------------------------------------------------- voice+dash
def test_voice_schedule_this_task(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC
    from voicebridge import kanban as KB

    cfg = _cfg(tmp_path, monkeypatch)
    KB.init(cfg)
    KB.create(cfg, "Water the plants", requested_by="cli:user",
              confirmed=True)
    res = VC.resolve("qubit, schedule this task daily 9am")
    assert res.kind == VC.MATCH
    out = VC.execute(_ctx(cfg), res)
    assert out.kind == VC.NEEDS_CONFIRM, (out.kind, out.speech)
    out2 = VC.confirm_pending(_ctx(cfg), out.confirmation_id)
    assert out2.kind == VC.EXECUTED, (out2.kind, out2.speech)
    job = out2.receipt["result"]["schedule"]["job"]
    assert job["kind"] == "daily" and "Water the plants" in job["task_text"]
    # Bare "schedule this task" asks when (clarification, not failure).
    res = VC.resolve("qubit, schedule this task")
    assert res.kind == VC.CLARIFY
    assert "every 30 minutes" in res.speech


def test_voice_schedules_reads_and_run_due(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC
    from voicebridge import schedules as SC

    cfg = _cfg(tmp_path, monkeypatch)
    SC.create(cfg, "n", "t", "every 30 minutes", confirmed=True)
    for text in ("qubit, list schedules", "qubit, show schedule 1",
                 "qubit, schedule history"):
        res = VC.resolve(text)
        assert res.kind == VC.MATCH, (text, res.kind)
        out = VC.execute(_ctx(cfg), res)
        assert out.kind == VC.EXECUTED, (text, out.kind, out.speech)
    res = VC.resolve("qubit, run due schedules")
    out = VC.execute(_ctx(cfg), res)
    assert out.kind == VC.NEEDS_CONFIRM
    out2 = VC.confirm_pending(_ctx(cfg), out.confirmation_id)
    assert out2.kind == VC.EXECUTED
    assert "scheduled jobs" in out2.speech


def test_dashboard_schedules_ops(tmp_path, monkeypatch):
    from voicebridge import dashboard_ops as D
    from voicebridge import schedules as SC

    cfg = _cfg(tmp_path, monkeypatch)
    SC.create(cfg, "n", "t", "every 30 minutes", confirmed=True)
    out = D.run_registry_op(cfg, "schedules_list", {},
                            actor="dashboard:user", session="dashboard")
    assert out["state"] == "executed", out
    assert "scheduled jobs" in out["speech"]
    staged = D.run_registry_op(cfg, "schedules_delete",
                               {"job_id": 1},
                               actor="dashboard:user", session="dashboard")
    assert staged["state"] == "needs-confirm", staged
    done = D.confirm_registry_op(cfg, staged["confirmation_id"],
                                 actor="dashboard:user", session="dashboard")
    assert done["state"] == "executed", done

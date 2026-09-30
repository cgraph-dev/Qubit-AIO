"""E5 — user-configurable OpenClaw automation through the ONE scheduler.

Closes the remaining configuration-surface gap for OC-31 (standing orders /
auto-reply / flows / heartbeat), OC-D05 (QA Lab scenarios + reporting), and
OC-D25 (subagent yield handoff): the user authors a bounded ``automation:``
directive as a scheduled job's task text through the EXISTING ``schedules``
store (CLI/dashboard/voice already cover create/list/run), and the directive
is dispatched on the single ``schedules.autofire`` tick via
``schedules.run_due`` -> ``automation.run_directive``. No second scheduler,
broker, listener, or transport is introduced.

Hermetic: isolated XDG/state, injected delivery transports, no network/model.
"""
from __future__ import annotations

import json
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


def _account(cfg, aid="team", provider="slack"):
    from voicebridge import messaging as M

    return M.account_add(cfg, aid, provider, aid, confirmed=True,
                         key_ref="env:SLACK_TOKEN")


def _schedule(cfg, name, directive):
    from voicebridge import schedules as SC

    return SC.create(cfg, name, directive, "once in 1 minutes", confirmed=True)


# -------------------------------------------------------- parser / safety
def test_is_directive_and_parser_errors(tmp_path, monkeypatch):
    from voicebridge import automation as A

    cfg = _cfg(tmp_path, monkeypatch)
    assert A.is_directive("automation: order.list") is True
    assert A.is_directive("  AUTOMATION: status") is True
    assert A.is_directive("run the report") is False
    assert A.is_directive("") is False
    assert A.is_directive(None) is False

    bad = A.run_directive(cfg, "automation: not.a.verb")
    assert bad["state"] == "FAILED" and bad["code"] == A.DIRECTIVE_CODE
    bad2 = A.run_directive(cfg, "automation: order.add oops")
    assert bad2["state"] == "FAILED" and bad2["code"] == A.DIRECTIVE_CODE
    empty = A.run_directive(cfg, "automation:   ")
    assert empty["state"] == "FAILED" and empty["code"] == A.DIRECTIVE_CODE


def test_directive_estop_denies_and_never_raises(tmp_path, monkeypatch):
    from voicebridge import automation as A

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(A, "estop_engaged", lambda _c: True)
    out = A.run_directive(cfg, "automation: status")
    assert out["state"] == "denied" and out["code"] == A.ESTOP_CODE


# --------------------------------------------- standing orders + heartbeat
def test_scheduled_order_and_heartbeat_via_autofire(tmp_path, monkeypatch):
    from voicebridge import automation as A
    from voicebridge import schedules as SC

    cfg = _cfg(tmp_path, monkeypatch)
    _account(cfg)
    _schedule(cfg, "make-order",
              "automation: order.add name=greet match=hello reply=hi "
              "account=team to=C1")
    _schedule(cfg, "make-hb", "automation: heartbeat.add name=hb every=5 "
                              "note=liveness")

    due = time.time() + 3600
    out = SC.autofire(cfg, now=due, limit=5)
    assert out["state"] == "OK" and out["ran"] == 2, out
    assert A.order_show(cfg, "greet")["order"]["match"] == "hello"
    assert A.heartbeat_list(cfg)["heartbeats"][0]["interval_min"] == 5

    # The same order auto-replies on a later tick (event queue -> messaging).
    A.queue_event(cfg, kind="channel", source="slack", text="well hello!",
                  confirmed=True)
    later = due + 300
    tick = SC.autofire(cfg, now=later, limit=5)
    assert tick["automation"]["processed"] == 1
    res = tick["automation"]["results"][0]
    assert res["order_id"] == "greet"
    assert res["state"] == "UNAVAILABLE"  # no credential -> never fake SENT


def test_scheduled_order_toggle_and_remove(tmp_path, monkeypatch):
    from voicebridge import automation as A
    from voicebridge import schedules as SC

    cfg = _cfg(tmp_path, monkeypatch)
    _account(cfg)
    _schedule(cfg, "make", "automation: order.add name=greet match=hi "
                           "reply=hey account=team to=C1")
    assert SC.autofire(cfg, now=time.time() + 3600)["ran"] == 1
    _schedule(cfg, "off", "automation: order.disable name=greet")
    assert SC.autofire(cfg, now=time.time() + 7200)["ran"] == 1
    assert A.order_show(cfg, "greet")["order"]["enabled"] is False
    _schedule(cfg, "del", "automation: order.remove name=greet")
    assert SC.autofire(cfg, now=time.time() + 10800)["ran"] == 1
    with pytest.raises(A.AutomationError):
        A.order_show(cfg, "greet")


# ---------------------------------------------------------------- flows
def test_scheduled_flow_add_and_run(tmp_path, monkeypatch):
    from voicebridge import automation as A
    from voicebridge import schedules as SC

    cfg = _cfg(tmp_path, monkeypatch)
    _schedule(cfg, "add-flow",
              "automation: flow.add name=wake "
              'steps=\'[{"action":"notify","text":"up"}]\'')
    assert SC.autofire(cfg, now=time.time() + 3600)["ran"] == 1
    assert A.flow_list(cfg)["flows"][0]["steps"] == 1
    _schedule(cfg, "run-flow", "automation: flow.run name=wake")
    out = SC.autofire(cfg, now=time.time() + 7200)
    assert out["ran"] == 1
    assert out["results"][0]["state"] in ("OK", "PARTIAL")


# ---------------------------------------------------------------- QA lab
def test_scheduled_qa_scenario_add_and_run(tmp_path, monkeypatch):
    from voicebridge import automation as A
    from voicebridge import schedules as SC

    cfg = _cfg(tmp_path, monkeypatch)
    scenario = ('[{"name":"ok","action":"check"},'
                '{"name":"ok2","action":"heartbeat"}]')
    _schedule(cfg, "qa-add",
              "automation: qa.add name=smoke scenario='" + scenario + "'")
    assert SC.autofire(cfg, now=time.time() + 3600)["ran"] == 1
    listed = A.qa_scenario_list(cfg)["scenarios"]
    assert listed and listed[0]["name"] == "smoke" and listed[0]["steps"] == 2

    _schedule(cfg, "qa-run", "automation: qa.run name=smoke")
    out = SC.autofire(cfg, now=time.time() + 7200)
    assert out["ran"] == 1
    assert A.qa_scenario_list(cfg)["scenarios"][0]["count"] == 1
    assert A.qa_scenario_list(cfg)["scenarios"][0]["last_state"] == "OK"


def test_qa_scenario_run_report_is_structured(tmp_path, monkeypatch):
    from voicebridge import automation as A

    cfg = _cfg(tmp_path, monkeypatch)
    A.qa_scenario_add(cfg, "smoke", [
        {"name": "ok", "action": "check"},
        {"name": "bad", "action": "check"},
    ], confirmed=True)
    report = A.qa_scenario_run(
        cfg, "smoke", confirmed=True,
        executor=lambda step: {"state": "OK" if step.get("name") == "ok"
                               else "FAILED", "code": "Q"})
    assert report["scenario"] == "smoke"
    assert report["total"] == 2 and report["passed"] == 1
    assert report["failed"] == 1 and report["state"] == "FAILED"


# -------------------------------------------------------------- yields
def test_scheduled_yield_handoff_and_resume(tmp_path, monkeypatch):
    from voicebridge import automation as A
    from voicebridge import schedules as SC

    cfg = _cfg(tmp_path, monkeypatch)
    _schedule(cfg, "yield", "automation: yield.handoff session=sess-1 "
                            "reason=review context=continue depth=1")
    assert SC.autofire(cfg, now=time.time() + 3600)["ran"] == 1
    assert A.yield_pending(cfg)["pending"][0]["session"] == "sess-1"
    _schedule(cfg, "resume", "automation: yield.resume session=sess-1 "
                             "consume=1")
    assert SC.autofire(cfg, now=time.time() + 7200)["ran"] == 1
    assert A.yield_pending(cfg)["pending"] == []


# ---------------------------------------------------------------- status
def test_status_reports_scenarios_count(tmp_path, monkeypatch):
    from voicebridge import automation as A

    cfg = _cfg(tmp_path, monkeypatch)
    A.qa_scenario_add(cfg, "smoke", [{"action": "check"}], confirmed=True)
    st = A.status(cfg)
    assert st["state"] == "OK" and st["counts"]["scenarios"] == 1


# ------------------------------------------------- production dispatcher
def test_run_due_executes_directive_through_production_caller(
        tmp_path, monkeypatch):
    """The directive is dispatched by ``schedules.run_due`` (the same caller
    the CLI/dashboard/voice ``run-due`` and the monitor ``autofire`` use)."""
    from voicebridge import automation as A
    from voicebridge import schedules as SC

    cfg = _cfg(tmp_path, monkeypatch)
    _account(cfg)
    SC.create(cfg, "hb", "automation: heartbeat.add name=hb every=5",
              "every 5 minutes", confirmed=True)
    created = SC.show(cfg, 1)["job"]["created_at"]
    out = SC.run_due(cfg, executor=lambda *a, **k: {"state": "SHOULD_NOT_RUN"},
                     now=created + 301)
    assert out["ran"] == 1
    assert out["results"][0]["state"] == "OK"
    assert A.heartbeat_list(cfg)["heartbeats"][0]["count"] == 0  # add, not tick
    assert A.heartbeat_list(cfg)["heartbeats"][0]["interval_min"] == 5

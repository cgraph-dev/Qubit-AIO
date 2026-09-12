"""Agent 6 — automation flows, QA runner, and subagent yield handoff.

Covers OpenClaw OC-D05-adjacent (bounded declarative QA scenario runner) and
OC-D25 (``sessions_yield``-style continuation context) on the existing
broker/permission/audit/e-stop path.

Hermetic: isolated XDG/state, injected transports, no network/model.
"""
from __future__ import annotations

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


def _account(cfg):
    from voicebridge import messaging as M

    M.account_add(cfg, "team", "slack", "team", confirmed=True,
                  key_ref="env:SLACK_TOKEN")


# ------------------------------------------------------------------- flows
def test_flow_create_validate_run(tmp_path, monkeypatch):
    from voicebridge import automation as A

    cfg = _cfg(tmp_path, monkeypatch)
    _account(cfg)
    with pytest.raises(A.AutomationError) as nc:
        A.flow_add(cfg, "flow1", [{"action": "notify", "text": "x"}],
                   confirmed=False)
    assert nc.value.code == A.CONFIRM_CODE
    with pytest.raises(A.AutomationError) as bad:
        A.flow_add(cfg, "flow1", [{"action": "explode"}], confirmed=True)
    assert bad.value.code == A.BAD_INPUT_CODE
    A.flow_add(cfg, "flow1",
               [{"action": "notify", "text": "started"},
                {"action": "reply", "account": "team", "to": "C1",
                 "body": "hi"}], confirmed=True)
    assert A.flow_list(cfg)["flows"][0]["steps"] == 2
    out = A.flow_run(cfg, "flow1", confirmed=True,
                     deliver=lambda *a, **k: {"state": "OK", "message_id": "m"})
    assert out["state"] == "OK" and len(out["results"]) == 2
    assert out["results"][1]["state"] == "SENT"
    with pytest.raises(A.AutomationError) as nc2:
        A.flow_run(cfg, "flow1", confirmed=False)
    assert nc2.value.code == A.CONFIRM_CODE


def test_flow_estop_refuses_run(tmp_path, monkeypatch):
    from voicebridge import automation as A

    cfg = _cfg(tmp_path, monkeypatch)
    A.flow_add(cfg, "flow1", [{"action": "notify", "text": "x"}],
               confirmed=True)
    out = A.flow_run(cfg, "flow1", confirmed=True, estop=lambda: True)
    assert out["state"] == "denied" and out["code"] == A.ESTOP_CODE


# -------------------------------------------------------------------- QA
def test_qa_run_reports_pass_and_fail(tmp_path, monkeypatch):
    from voicebridge import automation as A

    cfg = _cfg(tmp_path, monkeypatch)
    report = A.qa_run(cfg, [
        {"name": "ok", "action": "check", "expect": True},
        {"name": "bad", "action": "check", "expected": True},
    ], executor=lambda step: {"state": "OK" if step.get("expect", True)
                              else "FAILED", "code": "Q", "detail": ""})
    assert report["total"] == 2 and report["passed"] == 1
    assert report["failed"] == 1 and report["state"] == "FAILED"
    assert [s["name"] for s in report["steps"]] == ["ok", "bad"]


def test_qa_run_executor_exception_is_contained(tmp_path, monkeypatch):
    from voicebridge import automation as A

    cfg = _cfg(tmp_path, monkeypatch)

    def _boom(step):
        raise RuntimeError("synthetic scenario failure")

    report = A.qa_run(cfg, [{"name": "boom", "action": "check"}],
                      executor=_boom)
    assert report["failed"] == 1
    assert report["steps"][0]["code"] == "QA_EXECUTOR_ERROR"


def test_qa_run_estop_denies(tmp_path, monkeypatch):
    from voicebridge import automation as A

    cfg = _cfg(tmp_path, monkeypatch)
    denied = A.qa_run(cfg, [{"name": "x", "action": "check"}],
                      estop=lambda: True)
    assert denied["state"] == "denied" and denied["total"] == 0


# ------------------------------------------------------------------ yields
def test_yield_handoff_resume_consume(tmp_path, monkeypatch):
    from voicebridge import automation as A

    cfg = _cfg(tmp_path, monkeypatch)
    with pytest.raises(A.AutomationError) as nc:
        A.yield_handoff(cfg, "sess-1", reason="handoff", context="ctx",
                        confirmed=False)
    assert nc.value.code == A.CONFIRM_CODE
    out = A.yield_handoff(cfg, "sess-1", reason="needs review",
                          context="token=SECRETVALUE continue", depth=1,
                          confirmed=True)
    assert out["depth"] == 1 and out["consumed"] is False
    resumed = A.yield_resume(cfg, "sess-1")
    assert resumed["reason"] == "needs review"
    # Redaction boundary: a secret-shaped context never leaves raw.
    assert "SECRETVALUE" not in resumed["context"]
    # A second, unconsumed handoff for the same session is refused.
    with pytest.raises(A.AutomationError) as dup:
        A.yield_handoff(cfg, "sess-1", reason="again", context="ctx",
                        confirmed=True)
    assert dup.value.code == A.EXISTS_CODE
    consumed = A.yield_resume(cfg, "sess-1", consume=True)
    assert consumed["consumed"] is True
    with pytest.raises(A.AutomationError) as replay:
        A.yield_resume(cfg, "sess-1")
    assert replay.value.code == A.CONSUMED_CODE
    assert A.yield_pending(cfg)["pending"] == []


def test_yield_depth_bound_and_estop(tmp_path, monkeypatch):
    from voicebridge import automation as A

    cfg = _cfg(tmp_path, monkeypatch)
    with pytest.raises(A.AutomationError) as deep:
        A.yield_handoff(cfg, "sess-1", reason="r", context="c",
                        depth=A.MAX_DEPTH + 1, confirmed=True)
    assert deep.value.code == A.DEPTH_CODE
    monkeypatch.setattr(A, "estop_engaged", lambda _c: True)
    with pytest.raises(A.AutomationError) as es:
        A.yield_handoff(cfg, "sess-2", reason="r", context="c", confirmed=True)
    assert es.value.code == A.ESTOP_CODE
    with pytest.raises(A.AutomationError) as denied:
        A.yield_resume(cfg, "sess-1")
    assert denied.value.code == A.ESTOP_CODE


def test_status_reports_counts(tmp_path, monkeypatch):
    from voicebridge import automation as A

    cfg = _cfg(tmp_path, monkeypatch)
    _account(cfg)
    A.order_add(cfg, "greet", match="hi", reply="hey", account="team", to="C1",
                confirmed=True)
    A.heartbeat_add(cfg, "hb", 5, confirmed=True)
    A.yield_handoff(cfg, "s", reason="r", context="c", confirmed=True)
    st = A.status(cfg)
    assert st["state"] == "OK"
    assert st["counts"]["orders"] == 1
    assert st["counts"]["heartbeats"] == 1
    assert st["counts"]["yields"] == 1
    assert st["estop"] is False


# ------------------------------------------------- persisted QA scenarios
def test_qa_scenario_persist_list_and_run(tmp_path, monkeypatch):
    from voicebridge import automation as A

    cfg = _cfg(tmp_path, monkeypatch)
    with pytest.raises(A.AutomationError) as nc:
        A.qa_scenario_add(cfg, "smoke", [{"action": "check"}], confirmed=False)
    assert nc.value.code == A.CONFIRM_CODE
    with pytest.raises(A.AutomationError) as bad:
        A.qa_scenario_add(cfg, "smoke", [{"action": "explode"}],
                          confirmed=True)
    assert bad.value.code == A.BAD_INPUT_CODE
    A.qa_scenario_add(cfg, "smoke", [{"action": "check"},
                                     {"action": "heartbeat"}], confirmed=True)
    listed = A.qa_scenario_list(cfg)["scenarios"]
    assert listed[0]["name"] == "smoke" and listed[0]["steps"] == 2

    report = A.qa_scenario_run(
        cfg, "smoke", confirmed=True,
        executor=lambda step: {"state": "OK" if step["action"] == "check"
                               else "FAILED", "code": "Q"})
    assert report["scenario"] == "smoke"
    assert report["total"] == 2 and report["passed"] == 1
    assert report["failed"] == 1
    assert A.qa_scenario_list(cfg)["scenarios"][0]["count"] == 1

    with pytest.raises(A.AutomationError) as nc2:
        A.qa_scenario_run(cfg, "smoke", confirmed=False)
    assert nc2.value.code == A.CONFIRM_CODE
    assert A.qa_scenario_run(cfg, "smoke", confirmed=True,
                             estop=lambda: True)["state"] == "denied"


def test_tick_reports_pending_yields(tmp_path, monkeypatch):
    from voicebridge import automation as A

    cfg = _cfg(tmp_path, monkeypatch)
    A.yield_handoff(cfg, "sess-1", reason="r", context="c", confirmed=True)
    out = A.tick(cfg)
    assert out["state"] == "OK" and out["pending_yields"] == 1

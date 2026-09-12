"""Agent 3 — dashboard automation close-out + dashboard-only refusals.

Two dashboard-side close-outs, hermetic and through the PRODUCTION dispatcher:

* the OpenClaw automation capabilities (OC-31 standing orders / auto-reply /
  flows / heartbeat, OC-D05 QA Lab scenarios, OC-D25 subagent yield handoff)
  are already registry (Tier-1) ops, so the dashboard console reaches them
  through the SAME ``run_registry_op`` / ``confirm_registry_op`` broker as CLI
  and voice — no duplicate extras, no second store. These tests prove that
  surface end-to-end: catalog parity, real read results, staged confirmation,
  cancellation, settle-once, typed negatives, and emergency stop.
* the two catalog ``DASHBOARD_ONLY`` typed refusals (``computer_install``,
  ``gateway_supervision``) are honestly dashboard-only (no meaningful
  CLI/voice equivalent), so their declaration is corrected to
  ``interfaces=("dashboard",)`` and exercised via ``run_op`` /
  ``run_extra_op`` with typed negatives.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

AUTOMATION_IDS = {
    "automation_status", "automation_orders", "automation_heartbeats",
    "automation_flows", "automation_scenarios", "automation_yield_pending",
    "automation_order_add", "automation_order_enable",
    "automation_order_disable", "automation_order_remove",
    "automation_heartbeat_add", "automation_flow_add", "automation_flow_run",
    "automation_qa_add", "automation_qa_run", "automation_yield_handoff",
    "automation_yield_resume",
}
READ_IDS = {
    "automation_status", "automation_orders", "automation_heartbeats",
    "automation_flows", "automation_scenarios", "automation_yield_pending",
}


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.delenv("SLACK_TOKEN", raising=False)
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _run(cfg, op_id, args=None, session="dashboard"):
    from voicebridge import dashboard_ops as OPS

    return OPS.run_op(cfg, op_id, dict(args or {}), actor="dashboard:user",
                      session=session)


def _extra(cfg, op_id, args=None, session="dashboard"):
    from voicebridge import dashboard_ops as OPS

    return OPS.run_extra_op(cfg, op_id, dict(args or {}),
                            actor="dashboard:user", session=session)


def _confirm(cfg, out):
    from voicebridge import dashboard_ops as OPS

    return OPS.confirm_op(cfg, out["confirmation_id"], actor="dashboard:user",
                          session="dashboard")


def _cancel(cfg, confirmation_id):
    from voicebridge import dashboard_ops as OPS

    return OPS.cancel_op(cfg, confirmation_id, actor="dashboard:user",
                         session="dashboard")


def _latch(cfg, on=True):
    from voicebridge.power import power_control_store

    power_control_store(cfg).set(emergency_stop=bool(on), global_disable=False,
                                 issued_by="cli:user")


def _account(cfg, aid="team"):
    from voicebridge import messaging as M

    return M.account_add(cfg, aid, "slack", aid, confirmed=True,
                         key_ref="env:SLACK_TOKEN")


def _result(out):
    """The backend dict inside a dashboard receipt."""
    return out["receipt"]["result"]


# ------------------------------------------------------- catalog parity
def test_automation_ops_are_tier1_with_full_three_surface_parity(
        tmp_path, monkeypatch):
    from voicebridge import dashboard_ops as OPS
    from voicebridge import voice_commands as VC

    _cfg(tmp_path, monkeypatch)
    cat = {o["id"]: o for o in OPS.catalog()}
    for op_id in AUTOMATION_IDS:
        assert op_id in VC.REGISTRY, op_id
        item = cat[op_id]
        assert item["tier"] == "registry", (op_id, item)
        assert item["interfaces"] == ["cli", "dashboard", "voice"], op_id
        assert item["group"] == "automation", op_id
        assert not item["id"].startswith("dashboard_"), op_id
    # no duplicate extras shadow a registry automation op
    assert AUTOMATION_IDS.isdisjoint(set(OPS.EXTRA_OPS))


# ------------------------------------------------------- read surface
def test_automation_reads_execute_through_dashboard_broker(tmp_path,
                                                           monkeypatch):
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    probe = VC.make_ctx(cfg, actor="probe", session="probe", source="cli")
    for op_id in sorted(READ_IDS):
        entry = VC.REGISTRY[op_id]
        expected = VC._BACKENDS[entry.backend](
            probe, {k: v for k, v in (entry.fixed or ())})
        out = _run(cfg, op_id, {})
        assert out["state"] == "executed", (op_id, out)
        assert out["receipt"]["entry"] == op_id, op_id
        assert out["receipt"]["result"] == expected, op_id


# --------------------------------------------- standing orders / heartbeat
def test_automation_standings_confirm_cancel_and_negatives(tmp_path,
                                                           monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _account(cfg)

    spec = ("name=greet match=hello reply=hi account=team "
            "to=C0123456789")
    staged = _run(cfg, "automation_order_add", {"spec": spec})
    assert staged["state"] == "needs-confirm", staged
    cancel = _cancel(cfg, staged["confirmation_id"])
    assert cancel["state"] == "executed", cancel
    # cancelled confirmation never executes and settles once
    assert _confirm(cfg, staged)["state"] == "failed"

    staged = _run(cfg, "automation_order_add", {"spec": spec})
    assert staged["state"] == "needs-confirm", staged
    done = _confirm(cfg, staged)
    assert done["state"] == "executed", done
    order = _result(done)["automation"]["order"]
    assert order["name"] == "greet" and order["enabled"] is True, order
    assert _confirm(cfg, staged)["state"] == "failed", "settle-once"

    listed = _run(cfg, "automation_orders", {})
    names = [o["name"] for o in _result(listed)["automation"]["orders"]]
    assert "greet" in names, names

    off = _run(cfg, "automation_order_disable", {"spec": "name=greet"})
    assert off["state"] == "needs-confirm", off
    assert _confirm(cfg, off)["state"] == "executed"
    listed = _run(cfg, "automation_orders", {})
    rec = _result(listed)["automation"]["orders"][0]
    assert rec["enabled"] is False, rec

    on = _run(cfg, "automation_order_enable", {"spec": "name=greet"})
    assert _confirm(cfg, on)["state"] == "executed"

    hb = _run(cfg, "automation_heartbeat_add",
              {"spec": "name=pulse every=5 note=liveness"})
    assert hb["state"] == "needs-confirm", hb
    assert _confirm(cfg, hb)["state"] == "executed"
    beats = _run(cfg, "automation_heartbeats", {})
    assert _result(beats)["automation"]["heartbeats"][0]["name"] == "pulse"

    rm = _run(cfg, "automation_order_remove", {"spec": "name=greet"})
    assert _confirm(cfg, rm)["state"] == "executed"
    listed = _run(cfg, "automation_orders", {})
    assert _result(listed)["automation"]["orders"] == []

    # typed negatives: missing required arg / unknown arg / unknown op
    assert _run(cfg, "automation_order_add", {})["code"] == "BAD_ARGS"
    assert _run(cfg, "automation_order_add",
                {"spec": "name=x", "bogus": "1"})["code"] == "BAD_ARGS"
    assert _run(cfg, "agent3_no_such_automation", {})["code"] == "UNKNOWN_OP"


# ------------------------------------------------------- flows + QA + yield
def test_automation_flows_qa_and_yields_execute(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)

    flow_spec = ("name=wake steps='[{\"action\":\"notify\","
                 "\"text\":\"up\"}]'")
    add = _run(cfg, "automation_flow_add", {"spec": flow_spec})
    assert add["state"] == "needs-confirm", add
    assert _confirm(cfg, add)["state"] == "executed"
    flows = _run(cfg, "automation_flows", {})
    assert _result(flows)["automation"]["flows"][0]["name"] == "wake"
    run = _run(cfg, "automation_flow_run", {"spec": "name=wake"})
    assert run["state"] == "needs-confirm", run
    run_done = _confirm(cfg, run)
    assert run_done["state"] == "executed", run_done
    assert _result(run_done)["automation"]["state"] == "OK", run_done

    qa_spec = ("name=smoke scenario='[{\"name\":\"ok\",\"action\":"
               "\"check\",\"expect\":\"OK\"}]'")
    qa = _run(cfg, "automation_qa_add", {"spec": qa_spec})
    assert qa["state"] == "needs-confirm", qa
    assert _confirm(cfg, qa)["state"] == "executed"
    scenarios = _run(cfg, "automation_scenarios", {})
    assert _result(scenarios)["automation"]["scenarios"][0]["name"] == "smoke"
    qa_run = _run(cfg, "automation_qa_run", {"spec": "name=smoke"})
    assert qa_run["state"] == "needs-confirm", qa_run
    report = _result(_confirm(cfg, qa_run))["automation"]
    assert report["state"] == "OK", report
    assert report["passed"] == 1 and report["failed"] == 0, report

    hand = _run(cfg, "automation_yield_handoff",
                {"spec": "session=sess-1 reason=review context=continue "
                         "depth=1"})
    assert hand["state"] == "needs-confirm", hand
    hand_done = _confirm(cfg, hand)
    assert hand_done["state"] == "executed", hand_done
    handoff = _result(hand_done)["automation"]
    assert handoff["handoff_id"].startswith("yh_"), handoff
    pending = _run(cfg, "automation_yield_pending", {})
    assert [h["session"] for h in
            _result(pending)["automation"]["pending"]] == ["sess-1"]

    resume = _run(cfg, "automation_yield_resume",
                  {"spec": "session=sess-1 consume=1"})
    assert resume["state"] == "needs-confirm", resume
    resumed = _result(_confirm(cfg, resume))["automation"]
    assert resumed["consumed"] is True, resumed

    # typed negatives: malformed JSON step / empty scenario fail closed
    bad = _run(cfg, "automation_qa_add",
               {"spec": "name=broken scenario='{not json}'"})
    assert bad["state"] == "needs-confirm", bad
    bad_done = _confirm(cfg, bad)
    assert bad_done["state"] == "executed", bad_done
    assert _result(bad_done)["automation"]["state"] == "FAILED", bad_done
    assert _result(bad_done)["automation"]["code"], bad_done


# ------------------------------------------------------- safety + audit
def test_automation_dashboard_refuses_while_estop_latched(tmp_path,
                                                          monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _account(cfg)
    try:
        _latch(cfg, True)
        mut = _run(cfg, "automation_order_add",
                   {"spec": "name=x match=y reply=z account=team to=C1"})
        assert mut["state"] == "refused", mut
        assert mut["code"] == "ESTOP_LATCHED", mut
        # reads stay available while the e-stop only blocks mutations
        read = _run(cfg, "automation_status", {})
        assert read["state"] == "executed", read
    finally:
        _latch(cfg, False)


def test_automation_audit_events_stay_redacted(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    events = []

    def _capture(name, **detail):
        events.append((name, detail))

    monkeypatch.setattr("voicebridge.logging_setup.log_event", _capture)
    marker = "Agent3AutomationAuditMarker"
    staged = _run(cfg, "automation_yield_handoff",
                  {"spec": f"session=audit reason=review context={marker} "
                           f"depth=1"})
    _confirm(cfg, staged)
    names = [name for name, _ in events]
    # Registry ops are audited by the shared voice confirmation pipeline plus
    # the automation backend's own redacted audit record.
    assert "voice.needs_confirm" in names, names
    assert "voice.confirmed_executed" in names, names
    assert "automation.yield" in names, names
    assert marker not in json.dumps(events), events


# --------------------------------------------- dashboard-only refusals
def test_computer_and_gateway_are_typed_dashboard_only_refusals(
        tmp_path, monkeypatch):
    from voicebridge import dashboard_ops as OPS

    cfg = _cfg(tmp_path, monkeypatch)
    cat = {o["id"]: o for o in OPS.catalog()}
    for op_id in ("computer_install", "gateway_supervision"):
        item = cat[op_id]
        assert item["tier"] == "extra", op_id
        assert item["interfaces"] == ["dashboard"], (op_id, item)
        assert item["interface_reason"], op_id
        assert item["confirm"] is False, op_id

    for op_id, args, code in (
        ("computer_install", {}, "COMPUTER_USE_NO_DRIVER"),
        ("gateway_supervision", {"action": "run"}, "GATEWAY_NO_SUPERVISION"),
    ):
        via_run = _run(cfg, op_id, args)
        assert via_run["state"] == "executed", (op_id, via_run)
        result = _result(via_run)
        assert result["state"] in ("REJECTED", "REFUSED"), (op_id, result)
        assert result["code"] == code, (op_id, result)
        assert result["rollback"], (op_id, result)
        # run_extra_op is the same production path
        assert _result(_extra(cfg, op_id, args)) == result, op_id
        # rejection ops never stage a confirmation
        assert "confirmation_id" not in via_run, op_id
    # negatives: bad / missing gateway action, unknown computer arg
    assert _run(cfg, "gateway_supervision",
                {"action": "nope"})["code"] == "BAD_ARGS"
    assert _run(cfg, "gateway_supervision", {})["code"] == "BAD_ARGS"
    assert _run(cfg, "computer_install", {"bogus": "1"})["code"] == "BAD_ARGS"

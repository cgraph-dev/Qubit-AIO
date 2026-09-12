"""Voice coverage for the OpenClaw automation capabilities (Agent 6 wave).

Closes the voice side of the capabilities built for OC-31 (auto-reply
standing orders, flows, heartbeat), OC-D05 (QA Lab scenarios + reports) and
OC-D25 (subagent yield handoff) plus the read surfaces of the same store.

Each capability is one Tier-1 op id: voice resolves the utterance and
dispatches through ``dashboard_ops.run_op`` while the CLI and dashboard call
the same production dispatcher. Writes compose the canonical
``automation: <verb> key=value`` directive and execute it through
``automation.run_directive`` -- the ONE dispatcher the single
``schedules.autofire`` tick calls via ``schedules.run_due`` -- so voice and
the scheduled CLI/dashboard route share one policy/audit/persistence path.
Reads are hermetic and byte-identical to ``run_op``. Confirmations stage a
single durable journal record settled once by ``confirm_op``/``cancel_op``.

No skips, no xfail: every assertion exercises real code against hermetic
state.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    base = Path(tmp_path)
    base.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(base / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(base / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(base / "d"))
    monkeypatch.delenv("SLACK_TOKEN", raising=False)
    p = base / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _account(cfg):
    from voicebridge import messaging as M

    return M.account_add(cfg, "team", "slack", "team", confirmed=True,
                         key_ref="env:SLACK_TOKEN")


def _say(cfg, text, session="auto6", source="user"):
    from voicebridge import voice_commands as VC

    return VC.voice_say(cfg, text, actor="voice:user", session=session,
                        source=source)


def _run(cfg, op_id, args=None, session="auto6"):
    from voicebridge import dashboard_ops as OPS

    return OPS.run_op(cfg, op_id, dict(args or {}), actor="voice:user",
                      session=session)


def _confirm(cfg, staged, session="auto6"):
    from voicebridge import dashboard_ops as OPS

    assert staged["state"] == "needs-confirm", staged
    return OPS.confirm_op(cfg, staged["confirmation_id"], actor="voice:user",
                          session=session)


def _cancel(cfg, staged, session="auto6"):
    from voicebridge import dashboard_ops as OPS

    assert staged["state"] == "needs-confirm", staged
    return OPS.cancel_op(cfg, staged["confirmation_id"], actor="voice:user",
                         session=session)


def _tick(cfg):
    """Run the ONE production scheduler tick over the voice-authored jobs."""
    from voicebridge import schedules as SC

    return SC.autofire(cfg, now=time.time() + 3600, limit=50)


READ_OPS = {
    "automation_status": "qubit, automation status",
    "automation_orders": "qubit, list standing orders",
    "automation_heartbeats": "qubit, list heartbeats",
    "automation_flows": "qubit, list automation flows",
    "automation_scenarios": "qubit, list qa scenarios",
    "automation_yield_pending": "qubit, list pending handoffs",
}

WRITE_OPS = {
    "automation_order_add":
        "qubit, add standing order name=greet match=hello reply=hi "
        "account=team to=C1",
    "automation_order_disable": "qubit, disable standing order name=greet",
    "automation_order_enable": "qubit, enable standing order name=greet",
    "automation_order_remove": "qubit, remove standing order name=greet",
    "automation_heartbeat_add":
        "qubit, add heartbeat name=hb every=5 note=liveness",
    "automation_flow_add":
        "qubit, add automation flow name=wake steps='[{\"action\":\"notify\","
        "\"text\":\"up\"}]'",
    "automation_flow_run": "qubit, run automation flow name=wake",
    "automation_qa_add":
        "qubit, add qa scenario name=smoke scenario='[{\"name\":\"ok\","
        "\"action\":\"check\"}]'",
    "automation_qa_run": "qubit, run qa scenario name=smoke",
    "automation_yield_handoff":
        "qubit, create yield handoff session=sess-1 reason=review "
        "context=continue depth=1",
    "automation_yield_resume":
        "qubit, resume yield handoff session=sess-1 consume=1",
}

WRITE_IDS = tuple(WRITE_OPS)


# ------------------------------------------------------------------ wiring
def test_every_automation_entry_is_a_wired_tier1_op(tmp_path, monkeypatch):
    from voicebridge import dashboard_ops as OPS
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    catalog = {item["id"]: item for item in OPS.catalog()}
    for entry_id in (*READ_OPS, *WRITE_IDS):
        entry = VC.REGISTRY.get(entry_id)
        assert entry is not None, entry_id
        assert entry.backend == "automation", (entry_id, entry.backend)
        assert entry.backend in VC._BACKENDS, entry_id
        assert entry.aliases and entry.examples and entry.help, entry_id
        row = catalog.get(entry_id)
        assert row is not None, f"{entry_id} missing from dashboard catalog"
        assert row["tier"] == "registry", entry_id
        assert row["group"] == "automation", (entry_id, row["group"])
        assert "voice" in row["interfaces"], entry_id
    # Writes are confirm-tier; reads are not.
    for entry_id in WRITE_IDS:
        assert VC.REGISTRY[entry_id].confirm is True, entry_id
    for entry_id in READ_OPS:
        assert VC.REGISTRY[entry_id].confirm is False, entry_id
    _ = cfg


def test_automation_ids_are_cli_and_voice_addressable():
    from voicebridge import voice_commands as VC

    fam = VC.CLI_COVERAGE["schedules"]
    for entry_id in (*READ_OPS, *WRITE_IDS):
        assert entry_id in fam, (entry_id, fam)
    # no empty voice surface: every id resolves its own example
    for entry_id, text in {**READ_OPS, **WRITE_OPS}.items():
        res = VC.resolve(text)
        assert res.kind == VC.MATCH, (entry_id, res.kind, res.speech)
        assert res.entry is not None and res.entry.id == entry_id, \
            (entry_id, getattr(res.entry, "id", None))


# ------------------------------------------------------------------- reads
def test_reads_are_byte_identical_through_voice_and_run_op(tmp_path,
                                                           monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    for entry_id, utterance in READ_OPS.items():
        said = _say(cfg, utterance, session=f"r-{entry_id}")
        assert said["state"] == "executed", (entry_id, said)
        assert said["route"] == entry_id, (entry_id, said)
        direct = _run(cfg, entry_id, {}, session=f"r-{entry_id}")
        assert direct["state"] == "executed", (entry_id, direct)
        assert said["receipt"]["result"] == direct["receipt"]["result"], \
            entry_id


# --------------------------------------------------- real persisted writes
def test_every_write_confirms_persists_and_replays_once(tmp_path,
                                                        monkeypatch):
    from voicebridge import automation as A
    from voicebridge import schedules as SC

    cfg = _cfg(tmp_path, monkeypatch)
    _account(cfg)
    directives = set()
    for entry_id, utterance in WRITE_OPS.items():
        session = f"w-{entry_id}"
        staged = _say(cfg, utterance, session=session)
        assert staged["state"] == "needs-confirm", (entry_id, staged)
        done = _confirm(cfg, staged, session=session)
        assert done["state"] == "executed", (entry_id, done)
        result = done["receipt"]["result"]
        # The registry write ran the REAL production automation function
        # immediately (through the shared directive parser) and returned its
        # structured result -- no schedule round-trip.
        assert result["automation"]["state"] == "OK", (entry_id, result)
        assert result["directive"] == result["automation"]["directive"], \
            (entry_id, result)
        assert result["directive"] == result["task_text"].split()[1], \
            (entry_id, result)
        assert result["task_text"].startswith("automation: "), (entry_id,
                                                                result)
        directives.add(result["task_text"])
        # the confirmation journal is single-use
        replay = _confirm(cfg, staged, session=session)
        assert replay["state"] == "failed", (entry_id, replay)
        assert replay.get("code") in ("NO_CONFIRMATION", "ALREADY_DONE"), \
            (entry_id, replay)

    # Every write dispatched its own canonical directive, and none of them
    # went through the scheduler: the write persists immediately.
    assert len(directives) == len(WRITE_OPS), directives
    assert SC.list_jobs(cfg)["jobs"] == [], SC.list_jobs(cfg)

    # The real automation store now holds every configured/run object.
    assert [o["name"] for o in A.order_list(cfg)["orders"]] == []
    assert [h["name"] for h in A.heartbeat_list(cfg)["heartbeats"]] == ["hb"]
    assert [f["name"] for f in A.flow_list(cfg)["flows"]] == ["wake"]
    assert [s["name"] for s in A.qa_scenario_list(cfg)["scenarios"]] == \
        ["smoke"]
    assert A.qa_scenario_list(cfg)["scenarios"][0]["last_state"] == "OK"
    assert A.yield_pending(cfg)["pending"] == []


def test_order_add_remove_persists_and_cancels_clean(tmp_path, monkeypatch):
    from voicebridge import automation as A
    from voicebridge import schedules as SC

    cfg = _cfg(tmp_path, monkeypatch)
    _account(cfg)
    created = _confirm(cfg, _say(cfg, "qubit, add standing order name=greet "
                                      "match=hello reply=hi account=team "
                                      "to=C1", session="oa"), session="oa")
    assert created["state"] == "executed", created
    _tick(cfg)
    assert A.order_show(cfg, "greet")["order"]["match"] == "hello"

    staged = _say(cfg, "qubit, remove standing order name=greet",
                  session="or")
    cancelled = _cancel(cfg, staged, session="or")
    assert cancelled["state"] == "executed", cancelled
    _tick(cfg)
    assert A.order_show(cfg, "greet")["order"]["name"] == "greet"
    gone = _confirm(cfg, staged, session="or")
    assert gone["state"] == "failed", gone
    assert not any(j["task_text"].startswith("automation: order.remove")
                   for j in SC.list_jobs(cfg)["jobs"]), SC.list_jobs(cfg)

    _confirm(cfg, _say(cfg, "qubit, remove standing order name=greet",
                       session="or2"), session="or2")
    _tick(cfg)
    with pytest.raises(A.AutomationError):
        A.order_show(cfg, "greet")


def test_order_toggle_via_voice_persists(tmp_path, monkeypatch):
    from voicebridge import automation as A

    cfg = _cfg(tmp_path, monkeypatch)
    _account(cfg)
    _confirm(cfg, _say(cfg, "qubit, add standing order name=greet match=hi "
                            "reply=hey account=team to=C1", session="t1"),
             session="t1")
    _tick(cfg)
    assert A.order_show(cfg, "greet")["order"]["enabled"] is True
    _confirm(cfg, _say(cfg, "qubit, disable standing order name=greet",
                       session="t2"), session="t2")
    _tick(cfg)
    assert A.order_show(cfg, "greet")["order"]["enabled"] is False
    _confirm(cfg, _say(cfg, "qubit, enable standing order name=greet",
                       session="t3"), session="t3")
    _tick(cfg)
    assert A.order_show(cfg, "greet")["order"]["enabled"] is True


# -------------------------------------------- one production dispatcher
def test_voice_write_uses_the_same_directive_dispatcher_as_schedules(
        tmp_path, monkeypatch):
    """Voice and the CLI/dashboard schedule tick execute the identical
    ``automation:`` directive through ``automation.run_directive``."""
    from voicebridge import automation as A
    from voicebridge import schedules as SC

    directive = ("automation: heartbeat.add name=hb every=5 "
                 "note=liveness")

    # 1) voice: exact utterance -> confirmation -> the REAL run_directive
    #    executes it immediately into the automation store (no schedule job).
    vcfg = _cfg(tmp_path / "v", monkeypatch)
    said = _say(vcfg, "qubit, add heartbeat name=hb every=5 note=liveness")
    vdone = _confirm(vcfg, said)
    assert vdone["receipt"]["result"]["task_text"] == directive, vdone
    assert vdone["receipt"]["result"]["automation"]["state"] == "OK", vdone
    assert SC.list_jobs(vcfg)["jobs"] == [], SC.list_jobs(vcfg)
    voice_hb = A.heartbeat_list(vcfg)["heartbeats"][0]

    # 2) CLI/dashboard route: schedule the same directive on the one
    #    scheduler store, then let the shared tick dispatch it.
    scfg = _cfg(tmp_path / "s", monkeypatch)
    SC.create(scfg, "via-cli", directive, "once in 1 minutes", confirmed=True)
    out = SC.run_due(scfg, now=1e18)
    assert out["ran"] == 1, out
    assert out["results"][0]["state"] == "OK", out["results"][0]
    sched_hb = A.heartbeat_list(scfg)["heartbeats"][0]

    # 3) direct production dispatcher (the function the tick calls)
    dcfg = _cfg(tmp_path / "d", monkeypatch)
    direct = A.run_directive(dcfg, directive, requested_by="probe")
    assert direct["state"] == "OK", direct
    direct_hb = A.heartbeat_list(dcfg)["heartbeats"][0]

    keep = ("id", "name", "interval_min", "note", "enabled")
    scrub = lambda h: {k: h.get(k) for k in keep}  # noqa: E731
    assert scrub(voice_hb) == scrub(sched_hb) == scrub(direct_hb), \
        (voice_hb, sched_hb, direct_hb)


def test_voice_spec_equals_dispatcher_result(tmp_path, monkeypatch):
    """The spec voice binds is the directive the one production dispatcher runs."""
    from voicebridge import automation as A

    cfg = _cfg(tmp_path / "voice", monkeypatch)
    _account(cfg)
    said = _say(cfg, "qubit, add standing order name=greet match=hello "
                     "reply=hi account=team to=C1")
    done = _confirm(cfg, said)
    ran = done["receipt"]["result"]
    assert ran["automation"]["directive"] == "order.add", ran
    assert ran["task_text"] == ("automation: order.add name=greet "
                                "match=hello reply=hi account=team to=c1"), ran
    # immediate persisted write; the scheduler tick calls the same dispatcher.
    voice_match = A.order_show(cfg, "greet")["order"]["match"]

    direct_cfg = _cfg(tmp_path / "direct", monkeypatch)
    _account(direct_cfg)
    direct = A.run_directive(
        direct_cfg, "automation: order.add name=greet match=hello reply=hi "
                    "account=team to=C1", requested_by="probe")
    assert direct["state"] == "OK", direct
    assert voice_match == \
        A.order_show(direct_cfg, "greet")["order"]["match"] == "hello"


# --------------------------------------------------------- safety + audit
def test_write_estop_refuses_before_staging_and_reads_still_answer(
        tmp_path, monkeypatch):
    from voicebridge import automation as A

    cfg = _cfg(tmp_path, monkeypatch)
    latched = _confirm(cfg, _say(cfg, "qubit, pause everything",
                                 session="latch"), session="latch")
    assert latched["state"] == "executed", latched
    blocked = _say(cfg, "qubit, add heartbeat name=hb every=5")
    assert blocked["state"] == "refused", blocked
    assert blocked.get("code") == "ESTOP_LATCHED", blocked
    assert not blocked.get("confirmation_id"), blocked
    # a read still answers while latched; nothing was persisted
    assert _say(cfg, "qubit, list heartbeats")["state"] == "executed"
    assert A.heartbeat_list(cfg)["heartbeats"] == []


def test_non_user_sources_never_execute(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    for source in ("model", "page", "tool", "agent"):
        for text in ("qubit, add heartbeat name=hb every=5",
                     "qubit, automation status"):
            out = _say(cfg, text, session="src", source=source)
            assert out["state"] == "refused", (source, text, out)
            assert out["code"] == "SOURCE_REFUSED", (source, text, out)
            assert not out.get("confirmation_id"), (source, text, out)


def test_confirmation_redacts_secret_shaped_spec(tmp_path, monkeypatch):
    from voicebridge import automation as A
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    _account(cfg)
    secret = "sk-abcdefghijklmnopqrstuv1234"
    staged = _say(cfg, f"qubit, add standing order name=greet match=hello "
                       f"reply=hi account=team to=C1 token={secret}",
                  session="red")
    assert staged["state"] == "needs-confirm", staged
    assert secret not in repr(staged), staged
    assert "sk-abcdefghijklmnopqrstuv1234" not in staged["speech"], staged
    warned = []
    monkeypatch.setattr(VC, "_audit",
                        lambda op, actor, detail: warned.append((op, detail)))
    done = _confirm(cfg, staged, session="red")
    assert done["state"] == "executed", done
    assert warned, "the confirmation must be audited"
    assert any(item[0].startswith(("confirmed", "needs")) for item in warned)
    _tick(cfg)
    # the automation store only keeps the declared order fields, never the
    # unknown secret-shaped token the speaker pasted.
    order = A.order_show(cfg, "greet")["order"]
    assert secret not in repr(order), order


def test_cancel_leaves_automation_store_untouched(tmp_path, monkeypatch):
    from voicebridge import automation as A
    from voicebridge import schedules as SC

    cfg = _cfg(tmp_path, monkeypatch)
    _account(cfg)
    staged = _say(cfg, "qubit, add standing order name=greet match=hello "
                       "reply=hi account=team to=C1", session="cx")
    cancelled = _cancel(cfg, staged, session="cx")
    assert cancelled["state"] == "executed", cancelled
    assert A.order_list(cfg)["orders"] == [], A.order_list(cfg)
    assert not any(j["task_text"].startswith("automation: order.add")
                   for j in SC.list_jobs(cfg)["jobs"]), SC.list_jobs(cfg)
    gone = _confirm(cfg, staged, session="cx")
    assert gone["state"] == "failed", gone

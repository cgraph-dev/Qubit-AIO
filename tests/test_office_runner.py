"""M1 — one agent, one real turn, attributed.

Proves the whole accounting path of a turn **without spending a cent**: the
executor is injected, so the ordering (e-stop → consent → admission → receipt
→ turn → ledger → events → attribution) is exercised for real while the model
call is a stub.

The three proofs the milestone names:

1. `trace` shows the receipt written before the call, the model, the provider's
   own tokens and cents, the artifacts, and the event rows.
2. A cap below the cost **refuses the turn**, typed, instead of running it.
3. A process death mid-turn leaves the receipt `open`; admission refuses until
   the owner attests an amount.

Hermetic: tmp XDG state only; no network, no provider call.
"""
from __future__ import annotations

import pytest


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _seat(cfg, *, slug="worker-1", funded=100_000, seat_type="worker",
          supervised=False):
    from voicebridge.office import ledger, org as O
    ceo = O.tree(cfg)["employees"]
    ceo_id = next((e["id"] for e in ceo if e["seat_type"] == "ceo"), None)
    if ceo_id is None:
        ceo_id = O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)["id"]
    out = O.hire(cfg, slug, seat_type=seat_type, supervisor_id=ceo_id,
                 confirmed=True)
    if funded:
        ledger.record(cfg, "revenue", funded, agent_id=out["id"],
                      note="owner capital")
    if supervised:
        from voicebridge.office import store
        conn = store.connect(cfg)
        try:
            conn.execute("UPDATE office_agent SET autonomy='supervised'"
                         " WHERE id=?", (out["id"],))
            conn.commit()
        finally:
            conn.close()
    return out["id"], ceo_id


def _stub(*, reply="ok, I did the thing", cost_usd=0.0042, prompt=1200,
          completion=340, model="deepseek-v4.1-flash"):
    """A stand-in for `agent.run_agent_turn`, shaped exactly like the real one."""
    def _call(cfg, agent_id, task_text):
        return {
            "state": "OK", "reply": reply,
            "receipt": {
                "runtime": "qubit-native",
                "model": model,
                "provider": "opencode-go",
                "identity": f"opencode-go/{model}",
                "usage": {
                    "provider": "opencode-go", "model": model,
                    "identity": f"opencode-go/{model}",
                    "prompt_tokens": prompt,
                    "completion_tokens": completion,
                    "latency_ms": 812,
                    "cost_usd_est": cost_usd,
                },
            },
        }
    return _call


# ------------------------------------------------------- proof 1: attribution
def test_one_turn_is_fully_attributed(tmp_path, monkeypatch):
    from voicebridge.office import ledger, runner

    cfg = _cfg(tmp_path, monkeypatch)
    ledger.health(cfg)                       # creates the store
    agent_id, _ = _seat(cfg)

    out = runner.run_turn(cfg, agent_id, "write one line to out.txt",
                          executor=_stub())
    assert out["state"] == "OK"
    # no checker was supplied, so the TASK is not verifiable and is not done
    # (C17) — the turn itself still ran and settled.
    assert out["done"] is False

    # the provider's own figures, booked in cents and preserved in micros
    assert out["tokens_in"] == 1200
    assert out["tokens_out"] == 340
    assert out["cents"] == 0                 # $0.0042 rounds to zero cents…
    assert out["provider_cost_micros"] == 4200   # …and that is visible here

    t = runner.trace(cfg, agent_id)
    assert t["count"] == 1
    run = t["runs"][0]
    assert run["status"] == "settled"        # the receipt was written first
    assert run["model"] == "deepseek-v4.1-flash"
    assert run["attributed"] is True
    assert len(run["ledger"]) == 1
    assert run["ledger"][0]["provider_cost_micros"] == 4200
    # RE-POINTED. This assertion listed the three events a turn used to emit and
    # thereby pinned the defect: a real turn emitted NO walk-driving event, so
    # `tower.py`'s six animated kinds (tool_call/skill_used/ledger_write/
    # mcp_call/note_written/memory_written) were never produced by anything and
    # the office was permanently idle however much work it did. `tool_call` now
    # comes from the runner, between the receipt and the model call.
    #
    # The ORDER is asserted, not just the set: `tool_call` must land AFTER
    # `run_started` (the receipt exists first, D15) and BEFORE `run_settled`
    # (the work happens inside the run), or the tower would animate a turn that
    # had not been admitted yet.
    assert [e["kind"] for e in run["events"]] == ["run_started",
                                                 "tool_call",
                                                 "run_settled",
                                                 "task_unverified"], \
        [e["kind"] for e in run["events"]]
    # and it must be one of the kinds the tower can actually animate
    from voicebridge.office import tower as _T
    assert "tool_call" in _T._EVENT_STATION, (
        "runner emits an event the tower has no station for -- the walk map and "
        "the event writer have drifted apart again")
    assert t["attribution"]["unattributed_entries"] == 0
    assert t["complete"] is True


def test_a_cent_scaled_turn_books_the_right_cents(tmp_path, monkeypatch):
    """A realistic paid turn is priced in whole cents, not lost to rounding."""
    from voicebridge.office import ledger, runner

    cfg = _cfg(tmp_path, monkeypatch)
    ledger.health(cfg)
    agent_id, _ = _seat(cfg)

    out = runner.run_turn(cfg, agent_id, "task",
                          executor=_stub(cost_usd=0.0731), estimated_cents=50)
    assert out["cents"] == 7
    assert out["provider_cost_micros"] == 73100
    # the metered fold moves by exactly the booked cents
    assert ledger.sum_metered(cfg) == 7


def test_an_artifact_is_bound_to_the_run_that_made_it(tmp_path, monkeypatch):
    from voicebridge.office import ledger, runner

    cfg = _cfg(tmp_path, monkeypatch)
    ledger.health(cfg)
    agent_id, _ = _seat(cfg)

    out = runner.run_turn(cfg, agent_id, "write a file", executor=_stub())
    art = runner.attribute_artifact(cfg, out["run_id"], agent_id=agent_id,
                                    ref="out.txt", kind="file", sha256="ab12",
                                    size=42, confirmed=True)
    assert art["state"] == "OK"

    t = runner.trace(cfg, agent_id)
    assert [a["ref"] for a in t["runs"][0]["artifacts"]] == ["out.txt"]
    assert t["attribution"]["unattributed_entries"] == 0


def test_an_artifact_with_no_receipt_is_refused(tmp_path, monkeypatch):
    """C6: no run, no artifact — that is how unattributed work hides."""
    from voicebridge.office import ledger, runner
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    ledger.health(cfg)
    agent_id, _ = _seat(cfg)

    with pytest.raises(OfficeError) as err:
        runner.attribute_artifact(cfg, "run_does_not_exist",
                                  agent_id=agent_id, ref="ghost.txt",
                                  confirmed=True)
    assert err.value.code == "OFFICE_BAD_INPUT"
    assert "no receipt" in err.value.detail


def test_attribution_requires_confirm(tmp_path, monkeypatch):
    from voicebridge.office import ledger, runner
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    ledger.health(cfg)
    agent_id, _ = _seat(cfg)
    out = runner.run_turn(cfg, agent_id, "t", executor=_stub())
    with pytest.raises(OfficeError) as err:
        runner.attribute_artifact(cfg, out["run_id"], agent_id=agent_id,
                                  ref="f.txt")
    assert err.value.code == "OFFICE_CONFIRM_REQUIRED"


# ------------------------------------------------------------ proof 2: caps
def test_a_cap_below_the_estimate_refuses_the_turn_before_it_runs(
        tmp_path, monkeypatch):
    """The second turn is REFUSED, typed — never silently run."""
    from voicebridge.office import caps, ledger, runner
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    ledger.health(cfg)
    agent_id, _ = _seat(cfg)
    caps.set_cap(cfg, "perRun", 5, confirmed=True)

    calls = []

    def spy(cfg_, agent_, text):
        calls.append(text)
        return _stub()(cfg_, agent_, text)

    with pytest.raises(OfficeError) as err:
        runner.run_turn(cfg, agent_id, "an expensive task",
                        executor=spy, estimated_cents=50)
    assert err.value.code == "OFFICE_ADMISSION_REFUSED"
    assert "perRun" in err.value.detail
    assert calls == [], "the executor must never be reached"

    # and nothing was receipted, so nothing is stuck open either
    t = runner.trace(cfg, agent_id)
    assert t["count"] == 0


def test_a_broke_seat_is_refused(tmp_path, monkeypatch):
    from voicebridge.office import ledger, runner
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    ledger.health(cfg)
    agent_id, _ = _seat(cfg, funded=0)      # no capital, no revenue
    with pytest.raises(OfficeError) as err:
        runner.run_turn(cfg, agent_id, "work", executor=_stub())
    assert err.value.code == "OFFICE_ADMISSION_REFUSED"


# --------------------------------------------------- proof 3: the death case
def test_a_turn_that_dies_leaves_unknown_spend_not_free_work(
        tmp_path, monkeypatch):
    from voicebridge.office import ledger, receipts, runner

    cfg = _cfg(tmp_path, monkeypatch)
    ledger.health(cfg)
    agent_id, _ = _seat(cfg)

    def explodes(cfg_, agent_, text):
        raise RuntimeError("the process died mid-turn")

    out = runner.run_turn(cfg, agent_id, "work", executor=explodes)
    assert out["state"] == "FAILED"
    assert out["unknown_spend"] is True

    # the receipt is open: the book is incomplete and admission refuses
    assert ledger.health(cfg)["complete"] is False
    u = receipts.unsettled(cfg)
    assert u["count"] == 1
    assert u["runs"][0]["status"] == "open"

    # the owner states what it actually cost, and the book closes
    receipts.settle(cfg, out["run_id"], cents=3, attested_as="entered")
    assert ledger.health(cfg)["complete"] is True
    row = [r for r in ledger.rows(cfg)["rows"]
           if r["run_id"] == out["run_id"]][0]
    assert row["attested"] == 1
    assert row["attested_as"] == "entered"


def test_a_failed_turn_leaves_unknown_spend_not_a_zero_cent_row(
        tmp_path, monkeypatch):
    """A turn that did not succeed still may have been charged for (D15)."""
    from voicebridge.office import ledger, runner

    cfg = _cfg(tmp_path, monkeypatch)
    ledger.health(cfg)
    agent_id, _ = _seat(cfg)

    def failed(cfg_, agent_, text):
        return {"state": "FAILED", "code": "MODEL_ERROR",
                "detail": "provider refused",
                "receipt": {"route": "gateway"}}      # no usage block

    out = runner.run_turn(cfg, agent_id, "work", executor=failed)
    assert out["state"] == "UNKNOWN_SPEND"
    assert out["unknown_spend"] is True
    assert ledger.health(cfg)["complete"] is False   # admission now refuses
    # nothing was booked as free: no inference row exists for this run
    booked = [r for r in ledger.rows(cfg)["rows"]
              if r["run_id"] == out["run_id"]]
    assert booked == []


def test_a_usage_block_of_zero_is_the_providers_own_figure(
        tmp_path, monkeypatch):
    """A local, genuinely free model reports 0 — that IS a measurement.

    The discriminator is the *presence* of the usage block, not its value:
    no block means "missing measurement", a block of 0 means "cost nothing".
    """
    from voicebridge.office import ledger, runner

    cfg = _cfg(tmp_path, monkeypatch)
    ledger.health(cfg)
    agent_id, _ = _seat(cfg)

    def free_local(cfg_, agent_, text):
        return {"state": "OK", "reply": "done",
                "receipt": {"model": "local-brain", "identity": "local/brain",
                            "usage": {"model": "local-brain",
                                      "identity": "local/brain",
                                      "prompt_tokens": 40, "tokens": 0,
                                      "completion_tokens": 12,
                                      "cost_usd_est": 0.0}}}

    out = runner.run_turn(cfg, agent_id, "work", executor=free_local)
    assert out["state"] == "OK"
    assert out["cents"] == 0
    assert out["tokens_in"] == 40
    assert ledger.health(cfg)["complete"] is True    # settled, not unknown


def test_the_estop_stops_the_turn_before_anything_happens(tmp_path,
                                                          monkeypatch):
    from voicebridge.office import ledger, runner
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    ledger.health(cfg)
    agent_id, _ = _seat(cfg)

    called = []
    monkeypatch.setattr(runner.store, "estop_engaged", lambda c: True)
    with pytest.raises(OfficeError) as err:
        runner.run_turn(cfg, agent_id, "work",
                        executor=lambda *a: called.append(1))
    assert err.value.code == "OFFICE_RUN_REFUSED"
    assert "emergency stop" in err.value.detail
    assert called == []


# ------------------------------------------------------- consent as a gate
def test_a_frozen_seat_cannot_spend(tmp_path, monkeypatch):
    """The ladder's tier 2, reached from the runner (D17/D19)."""
    from voicebridge.office import ledger, runner, store
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    ledger.health(cfg)
    agent_id, _ = _seat(cfg)
    conn = store.connect(cfg)
    try:
        conn.execute("UPDATE office_agent SET autonomy='frozen' WHERE id=?",
                     (agent_id,))
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(OfficeError) as err:
        runner.run_turn(cfg, agent_id, "work", executor=_stub())
    assert err.value.code == "OFFICE_RUN_REFUSED"
    assert "frozen" in err.value.detail
    # a denial is on the record, and it carries the anti-retry suffix
    assert "do not re-attempt" in err.value.detail


def test_a_supervised_seat_without_a_grant_cannot_spend(tmp_path, monkeypatch):
    from voicebridge.office import ledger, runner
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    ledger.health(cfg)
    agent_id, _ = _seat(cfg, supervised=True)
    with pytest.raises(OfficeError) as err:
        runner.run_turn(cfg, agent_id, "work", executor=_stub())
    assert err.value.code == "OFFICE_RUN_REFUSED"
    assert "silence is not consent" in err.value.detail


# ---------------------------------------------------- C17: done is checked
def test_a_blank_reply_is_not_done(tmp_path, monkeypatch):
    from voicebridge.office import ledger, runner

    cfg = _cfg(tmp_path, monkeypatch)
    ledger.health(cfg)
    agent_id, _ = _seat(cfg)
    out = runner.run_turn(cfg, agent_id, "work", executor=_stub(reply=""))
    assert out["done"] is False
    t = runner.trace(cfg, agent_id)
    assert "task_unverified" in [e["kind"] for e in t["runs"][0]["events"]]


def test_a_caller_supplied_checker_decides_done(tmp_path, monkeypatch):
    """An exit condition the runtime evaluates against real state (C17)."""
    from voicebridge.office import ledger, runner

    cfg = _cfg(tmp_path, monkeypatch)
    ledger.health(cfg)
    agent_id, _ = _seat(cfg)
    out = runner.run_turn(cfg, agent_id, "write out.txt", executor=_stub(),
                          checker=lambda r: "out.txt" in r["reply"])
    assert out["done"] is False              # the stub's reply never says it
    out2 = runner.run_turn(
        cfg, agent_id, "write out.txt",
        executor=_stub(reply="wrote out.txt"), estimated_cents=1,
        checker=lambda r: "out.txt" in r["reply"])
    assert out2["done"] is True


def test_a_run_needs_a_task(tmp_path, monkeypatch):
    from voicebridge.office import ledger, runner
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    ledger.health(cfg)
    agent_id, _ = _seat(cfg)
    with pytest.raises(OfficeError) as err:
        runner.run_turn(cfg, agent_id, "   ", executor=_stub())
    assert err.value.code == "OFFICE_BAD_INPUT"

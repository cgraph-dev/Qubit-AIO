"""M5 — the work loop: queue → turn → exit check → review → fix.

The loop is the thing that makes N seats worth having, so the tests that matter
are the refusals: a task with no exit condition cannot be created, work whose
check fails is never `done`, a rejection carries an instruction, and a reviewer
who does not decide does not accept.

`executor` is stubbed everywhere, so the whole loop is provable without a
provider call. `exit_check` is a real shell command against real files, because
that is what makes "done" machine-checkable (C17).

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


def _org(cfg, *, workers=2, reviewers=1):
    """A CEO above N reviewers above M workers — the chain C30/C31 needs."""
    from voicebridge.office import org as O

    ceo = O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)["id"]
    revs, wks = [], []
    for i in range(reviewers):
        revs.append(O.hire(cfg, f"lead-{i}", seat_type="manager",
                           supervisor_id=ceo, confirmed=True)["id"])
    for i in range(workers):
        wks.append(O.hire(cfg, f"wk-{i}", seat_type="worker",
                          supervisor_id=revs[i % len(revs)], confirmed=True)["id"])
    return {"ceo": ceo, "reviewers": revs, "workers": wks}


def _pay(cfg, agent_id, cents=100_000):
    from voicebridge.office import ledger
    ledger.record(cfg, "revenue", cents, agent_id=agent_id)


def _worker_stub(writes=None, *, fail=False):
    """A stand-in for a real turn: it can create the artifact the check wants."""
    def _call(cfg, agent_id, task_text):
        if writes:
            writes()
        if fail:
            return {"state": "FAILED", "code": "MODEL_ERROR",
                    "reply": "I could not do it",
                    "receipt": {"route": "gateway"}}
        return {"state": "OK", "reply": "I did the thing.",
                "receipt": {"model": "stub", "identity": "stub/model",
                            "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                                      "cost_usd_est": 0.0}}}
    return _call


def _reviewer_stub(reply):
    def _call(cfg, agent_id, task_text):
        return {"state": "OK", "reply": reply,
                "receipt": {"model": "stub", "identity": "stub/model",
                            "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                                      "cost_usd_est": 0.0}}}
    return _call


# ============================================================ the creation gate
def test_a_task_without_an_exit_check_cannot_be_created(tmp_path, monkeypatch):
    from voicebridge.office import work as W
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    _org(cfg)
    with pytest.raises(OfficeError) as exc:
        W.create(cfg, "do the thing", exit_check="  ", confirmed=True)
    assert exc.value.code == "OFFICE_TASK_NEEDS_EXIT_CHECK"
    assert "never the model's own verdict" in exc.value.detail
    assert W.tasks(cfg)["count"] == 0


# ==================================================================== the loop
def test_the_loop_creates_runs_checks_and_accepts(tmp_path, monkeypatch):
    from voicebridge.office import dispatch as D, work as W

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg)
    for a in ids["workers"] + ids["reviewers"]:
        _pay(cfg, a)

    artifact = tmp_path / "artifact.txt"
    task = W.create(cfg, "write the artifact", brief="put text in artifact.txt",
                    exit_check=f"test -f {artifact}", confirmed=True)["id"]

    out = D.dispatch(cfg, rounds=1, executor=_worker_stub(
        lambda: artifact.write_text("done")), confirmed=True)
    assert out["ran"] == 1
    assert out["results"][0]["satisfied"] is True
    assert W.tasks(cfg, status="review")["count"] == 1

    # the reviewer is the worker's own supervisor, not a peer
    row = W.tasks(cfg, status="review")["tasks"][0]
    assert row["reviewer_id"] == ids["reviewers"][0]
    assert row["reviewer_id"] != row["assignee_id"]

    adj = D.adjudicate(cfg, rounds=1, executor=_reviewer_stub(
        "looks right\nVERDICT: accept\nNOTE: meets the condition"),
        confirmed=True)
    assert adj["results"][0]["verdict"] == "accept"
    done = W.tasks(cfg, status="accepted")["tasks"][0]
    assert done["id"] == task and done["verdict"] == "accept"


# --- THE point: work whose check fails is never done
def test_work_whose_exit_check_fails_is_never_done(tmp_path, monkeypatch):
    from voicebridge.office import dispatch as D, work as W

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg)
    _pay(cfg, ids["workers"][0])

    missing = tmp_path / "never-created.txt"
    W.create(cfg, "do something impossible",
             exit_check=f"test -f {missing}", confirmed=True)

    out = D.dispatch(cfg, rounds=1, executor=_worker_stub(), confirmed=True)
    # the model said it did the thing; the check says otherwise
    assert out["results"][0]["satisfied"] is False
    assert out["results"][0]["state"] == "INCOMPLETE"
    assert W.tasks(cfg, status="review")["count"] == 0
    assert W.tasks(cfg)["tasks"][0]["status"] == "running"


def test_a_failed_turn_is_recorded_and_the_task_stays_open(tmp_path, monkeypatch):
    from voicebridge.office import dispatch as D, work as W

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg)
    _pay(cfg, ids["workers"][0])
    never = tmp_path / "absent.txt"
    t = W.create(cfg, "t", exit_check=f"test -f {never}", confirmed=True)["id"]

    out = D.dispatch(cfg, rounds=1, executor=_worker_stub(fail=True),
                     confirmed=True)
    assert out["results"][0]["satisfied"] is False
    trail = W.trail(cfg, t)["events"]
    assert [e["kind"] for e in trail] == ["created", "claimed", "turn"]
    assert "not_satisfied" in trail[-1]["body"] or \
        trail[-1]["payload"].get("state") == "FAILED"


# ================================================================== the review
def test_a_rejection_carries_the_instruction_to_the_next_attempt(
        tmp_path, monkeypatch):
    """A rejection is a fix instruction, not a restart."""
    from voicebridge.office import dispatch as D, work as W

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg)
    for a in ids["workers"] + ids["reviewers"]:
        _pay(cfg, a)
    artifact = tmp_path / "a.txt"
    t = W.create(cfg, "t", exit_check=f"test -f {artifact}",
                 confirmed=True)["id"]
    D.dispatch(cfg, rounds=1,
               executor=_worker_stub(lambda: artifact.write_text("x")),
               confirmed=True)
    D.adjudicate(cfg, rounds=1,
                 executor=_reviewer_stub("VERDICT: reject\nNOTE: use JSON not CSV"),
                 confirmed=True)

    row = W.tasks(cfg)["tasks"][0]
    assert row["status"] == "fixing" and row["attempts"] == 1
    assert row["review_note"] == "use JSON not CSV"
    # the reviewer sent it back, so the work is NOT done any more: drop the
    # artifact, or the precheck would correctly finish it without a turn and the
    # fix instruction would never reach the author
    artifact.unlink()

    # the next dispatch hands it back to the SAME seat, with the note in brief
    seen = {}

    def capture(cfg_, agent_id, task_text):
        seen["agent"] = agent_id
        seen["brief"] = task_text
        return {"state": "OK", "reply": "fixed",
                "receipt": {"usage": {"cost_usd_est": 0.0}}}

    D.dispatch(cfg, rounds=1, executor=capture, confirmed=True)
    assert seen["agent"] == row["assignee_id"], "a fix must go back to its author"
    assert "use JSON not CSV" in seen["brief"]
    assert "Address exactly that" in seen["brief"]


def test_a_rejection_without_an_instruction_is_refused(tmp_path, monkeypatch):
    from voicebridge.office import work as W
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg)
    t = W.create(cfg, "t", exit_check="true", confirmed=True)["id"]
    W.claim(cfg, t, agent_id=ids["workers"][0], confirmed=True)
    from voicebridge.office import store
    conn = store.connect(cfg)
    try:
        conn.execute("UPDATE office_task SET status='review' WHERE id=?", (t,))
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(OfficeError) as exc:
        W.review(cfg, t, verdict="reject", note="  ",
                 by_agent=ids["reviewers"][0], confirmed=True)
    assert "just a delay" in exc.value.detail


def test_only_the_assigned_reviewer_may_accept(tmp_path, monkeypatch):
    from voicebridge.office import store, work as W
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg, workers=2, reviewers=2)
    t = W.create(cfg, "t", exit_check="true", confirmed=True)["id"]
    W.claim(cfg, t, agent_id=ids["workers"][0], confirmed=True)
    conn = store.connect(cfg)
    try:
        conn.execute("UPDATE office_task SET status='review' WHERE id=?", (t,))
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(OfficeError) as exc:
        W.review(cfg, t, verdict="accept", by_agent=ids["reviewers"][1],
                 confirmed=True)
    assert exc.value.code == "OFFICE_REVIEW_WRONG_LEVEL"


# --- silence is not acceptance
def test_a_reviewer_with_no_verdict_accepts_nothing(tmp_path, monkeypatch):
    from voicebridge.office import dispatch as D, work as W

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg)
    for a in ids["workers"] + ids["reviewers"]:
        _pay(cfg, a)
    artifact = tmp_path / "a.txt"
    t = W.create(cfg, "t", exit_check=f"test -f {artifact}", confirmed=True)["id"]
    D.dispatch(cfg, rounds=1,
               executor=_worker_stub(lambda: artifact.write_text("x")),
               confirmed=True)

    out = D.adjudicate(cfg, rounds=1,
                       executor=_reviewer_stub("Looks fine to me, nice work!"),
                       confirmed=True)
    assert out["results"][0]["state"] == "NO_VERDICT"
    assert W.tasks(cfg, status="accepted")["count"] == 0
    assert W.tasks(cfg, status="review")["count"] == 1
    kinds = [e["kind"] for e in W.trail(cfg, t)["events"]]
    assert "no_verdict" in kinds


def test_attempts_are_capped_and_the_task_is_abandoned(tmp_path, monkeypatch):
    from voicebridge.office import store, work as W

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg)
    t = W.create(cfg, "t", exit_check="true", max_attempts=1,
                 confirmed=True)["id"]
    W.claim(cfg, t, agent_id=ids["workers"][0], confirmed=True)
    conn = store.connect(cfg)
    try:
        conn.execute("UPDATE office_task SET status='review' WHERE id=?", (t,))
        conn.commit()
    finally:
        conn.close()
    W.review(cfg, t, verdict="reject", note="still wrong",
             by_agent=ids["reviewers"][0], confirmed=True)
    assert W.tasks(cfg)["tasks"][0]["status"] == "abandoned"


# ============================================================== turn budgeting
def test_the_daily_turn_budget_stops_a_seat_rather_than_spinning(
        tmp_path, monkeypatch):
    from voicebridge.office import dispatch as D, work as W

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg, workers=1)
    wk = ids["workers"][0]
    _pay(cfg, wk)
    W.set_turn_cap(cfg, agent_id=wk, turns=1, confirmed=True)
    for i in range(3):
        W.create(cfg, f"t{i}", exit_check="false", confirmed=True)

    out = D.dispatch(cfg, rounds=5, executor=_worker_stub(), confirmed=True)
    assert out["ran"] == 0
    states = [r["state"] for r in out["results"]]
    assert "BUDGET_EXHAUSTED" in states
    assert len(out["results"]) == 2, "one turn spent, then refused"
    budget = W.turn_budget(cfg, agent_id=wk)
    assert budget["turns_used"] == 1 and budget["remaining"] == 0


def test_no_cap_is_reported_as_unbounded_not_as_safe(tmp_path, monkeypatch):
    from voicebridge.office import work as W

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg, workers=1)
    b = W.turn_budget(cfg, agent_id=ids["workers"][0])
    assert b["turns_cap"] is None and b["unbounded"] is True


# =================================================================== dry run
def test_a_dry_run_spends_no_turn_and_runs_nothing(tmp_path, monkeypatch):
    """A preview must not be the most expensive way to see what a run would do."""
    from voicebridge.office import dispatch as D, work as W

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg, workers=1)
    wk = ids["workers"][0]
    _pay(cfg, wk)
    W.set_turn_cap(cfg, agent_id=wk, turns=2, confirmed=True)
    for i in range(3):
        W.create(cfg, f"t{i}", exit_check="true", confirmed=True)
    calls = []

    def stub(cfg_, agent_id, task_text):
        calls.append(agent_id)
        return {"state": "OK", "reply": "x", "receipt": {"usage": {}}}

    out = D.dispatch(cfg, rounds=3, executor=stub, dry_run=True, confirmed=True)
    assert calls == [], "a dry run reached the executor"
    assert [r["state"] for r in out["results"]] == ["preview"] * 3
    # each task previewed once — not the same one three times
    assert [r["task"] for r in out["results"]] == [1, 2, 3]
    # and the preview shows the spread a real run would produce, so all three
    # do not land on one seat
    assert len({r["worker"] for r in out["results"]}) == 1, \
        "one worker is all this org has; the spread is tested where there are more"
    assert all(t["status"] == "open" for t in W.tasks(cfg)["tasks"])
    # and the budget is untouched: read, never consumed
    assert W.turn_budget(cfg, agent_id=wk)["turns_used"] == 0


def test_a_preview_reports_a_budget_it_would_hit(tmp_path, monkeypatch):
    from voicebridge.office import dispatch as D, work as W

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg, workers=1)
    wk = ids["workers"][0]
    W.set_turn_cap(cfg, agent_id=wk, turns=0, confirmed=True)
    W.create(cfg, "t", exit_check="true", confirmed=True)
    out = D.dispatch(cfg, rounds=1, dry_run=True, confirmed=True)
    assert out["results"][0]["state"] == "BUDGET_EXHAUSTED"
    assert "would be refused" in out["results"][0]["detail"]


def test_a_task_that_will_not_leave_open_does_not_starve_the_queue(
        tmp_path, monkeypatch):
    """The loop moves past a task instead of retrying it forever."""
    from voicebridge.office import dispatch as D, store, work as W

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg, workers=2)
    for a in ids["workers"]:
        _pay(cfg, a)
    W.create(cfg, "first", exit_check="false", confirmed=True)
    W.create(cfg, "second", exit_check="false", confirmed=True)
    ran = []

    def stub(cfg_, agent_id, task_text):
        ran.append(task_text.splitlines()[0])
        return {"state": "OK", "reply": "x", "receipt": {}}

    # make the first task refuse to move out of `open`
    real_claim = D.W.claim

    def stuck_claim(cfg_, task_id, **kw):
        if int(task_id) == 1:
            raise D.OfficeError("OFFICE_BAD_STATE", "simulated: will not move")
        return real_claim(cfg_, task_id, **kw)

    monkeypatch.setattr(D.W, "claim", stuck_claim)
    out = D.dispatch(cfg, rounds=3, executor=stub, unmetered=True,
                     confirmed=True)
    assert ran == ["second"], f"the queue starved: {ran}"
    # the first task could not be claimed, the second ran but its check failed:
    # INCOMPLETE, never a silent OK
    assert [r["state"] for r in out["results"]][:2] == ["REFUSED", "INCOMPLETE"]


def test_every_dispatch_needs_a_confirmation(tmp_path, monkeypatch):
    from voicebridge.office import dispatch as D, store
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    with pytest.raises(OfficeError) as exc:
        D.dispatch(cfg, rounds=1)
    assert exc.value.code == store.CONFIRM_CODE


# ============================================== flat-rate vs unknown spend (D16)
def test_an_unmetered_turn_with_no_usage_settles_and_does_not_block(
        tmp_path, monkeypatch):
    """A flat-rate gateway reports a usage block marked `included`.

    D15 says unknown spend must block admission. D16 says a subscription-backed
    turn costs no marginal dollar, so there is nothing to be unknown. Both hold:
    the turn is recorded, the ledger row is `unmetered`, and the next admission
    is not refused on a cost that does not exist.

    WHY THIS SHAPE AND NOT "NO USAGE AT ALL"
    ----------------------------------------
    This test used to pass a `receipt` with NO usage block and rely on the
    caller's `unmetered=True` to file it as free. That is precisely the bug the
    runner was fixed for: the vendored seat path emitted no usage block, so the
    flag was the ONLY evidence, and every seat's real provider call settled as
    `unmetered=1, cents=0` — a book that under-counted every seat while the
    caps, balances and runway were read from it.

    `unmetered` is now a claim about the ROUTE, and the turn has to prove it:
    the block carries the pricing engine's verdict, and `included` is the only
    status meaning "this route really is flat-rate". A flat-rate gateway
    reports that; an empty receipt is not a subscription, it is a missing
    measurement. See also `test_a_metered_turn_with_no_usage_still_goes_
    unknown_spend`, which pins the other half of the same rule.
    """
    from voicebridge.office import dispatch as D, ledger, work as W

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg, workers=1)
    wk = ids["workers"][0]
    _pay(cfg, wk)
    artifact = tmp_path / "a.txt"

    def flat_rate(cfg_, agent_id, task_text):
        artifact.write_text("x")
        return {"state": "OK", "reply": "done",
                "receipt": {"usage": {"prompt_tokens": 10,
                                      "completion_tokens": 5,
                                      "cost_status": "included"}}}

    W.create(cfg, "t", exit_check=f"test -f {artifact}", confirmed=True)
    out = D.dispatch(cfg, rounds=1, executor=flat_rate, unmetered=True,
                     confirmed=True)
    assert out["ran"] == 1, out
    assert out["results"][0]["satisfied"] is True

    # it is on the record, and it is not dollars
    folds = ledger.health(cfg)
    assert folds["complete"] is True, "an unmetered turn left the books unsettled"
    assert folds["cost_cents"] == 0, "a flat-rate turn manufactured dollar cost"
    assert folds["unmetered_entries"] >= 1, "the turn vanished from the record"
    # the only metered money is the owner's funding; the turn added no spend
    assert folds["revenue_cents"] == 100_000
    assert folds["cost_cents"] == 0

    # and the seat can spend its next turn — admission is not blocked
    artifact2 = tmp_path / "b.txt"
    W.create(cfg, "t2", exit_check=f"test -f {artifact2}", confirmed=True)

    def flat_rate2(cfg_, agent_id, task_text):
        artifact2.write_text("x")
        return {"state": "OK", "reply": "done",
                "receipt": {"usage": {"prompt_tokens": 10,
                                      "completion_tokens": 5,
                                      "cost_status": "included"}}}

    second = D.dispatch(cfg, rounds=1, executor=flat_rate2, unmetered=True,
                        confirmed=True)
    assert second["results"], "no work was picked up"
    assert second["results"][0]["state"] != "REFUSED", second["results"][0]


def test_a_metered_turn_with_no_usage_still_goes_unknown_spend(
        tmp_path, monkeypatch):
    """The D15 rule is not weakened: without `unmetered` it still blocks."""
    from voicebridge.office import runner as R, store, work as W

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg, workers=1)
    wk = ids["workers"][0]
    _pay(cfg, wk)

    def no_usage(cfg_, agent_id, task_text):
        return {"state": "OK", "reply": "done", "receipt": {}}

    run = R.run_turn(cfg, wk, "do a thing", executor=no_usage, unmetered=False)
    assert run["state"] == "UNKNOWN_SPEND" and run["unknown_spend"] is True
    conn = store.connect(cfg)
    try:
        open_receipts = conn.execute(
            "SELECT COUNT(*) n FROM office_run_receipts WHERE run_id=? AND"
            " status!='settled'", (run["run_id"],)).fetchone()["n"]
    finally:
        conn.close()
    assert open_receipts == 1, "the receipt must stay open so admission refuses"


def test_a_dry_run_spreads_work_across_the_seats_it_would_use(
        tmp_path, monkeypatch):
    from voicebridge.office import dispatch as D, work as W

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg, workers=4, reviewers=1)
    for a in ids["workers"]:
        _pay(cfg, a)
    for i in range(4):
        W.create(cfg, f"t{i}", exit_check="true", confirmed=True)
    out = D.dispatch(cfg, rounds=4, dry_run=True, confirmed=True)
    picks = [r["worker"] for r in out["results"]]
    assert picks == ids["workers"], f"the preview stacked work: {picks}"


def test_finished_work_is_never_repaid_for(tmp_path, monkeypatch):
    """A check that already passes must not cost a turn.

    This is the honest kind of free: a verification that succeeded, not a charge
    that was skipped. It also means an owner who finishes a task by hand is
    recognised rather than charged for.
    """
    from voicebridge.office import dispatch as D, store, work as W

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg, workers=1)
    wk = ids["workers"][0]
    _pay(cfg, wk)
    W.set_turn_cap(cfg, agent_id=wk, turns=0, confirmed=True)  # no turns at all
    done = tmp_path / "already.txt"
    done.write_text("finished by hand")
    t = W.create(cfg, "already done", exit_check=f"test -f {done}",
                 confirmed=True)["id"]

    called = []

    def stub(cfg_, agent_id, task_text):
        called.append(1)
        return {"state": "OK", "reply": "x", "receipt": {}}

    out = D.dispatch(cfg, rounds=1, executor=stub, unmetered=True,
                     confirmed=True)
    assert called == [], "a turn was spent on work that was already verified"
    assert out["results"][0]["state"] == "OK"
    assert out["results"][0]["turns_spent"] == 0
    # with a zero turn budget this could only ever have completed via the
    # precheck — which is the proof that no budget was consumed
    assert W.tasks(cfg, status="review")["count"] == 1
    assert W.turn_budget(cfg, agent_id=wk)["turns_used"] == 0
    assert W.tasks(cfg)["tasks"][0]["id"] == t


def test_a_refused_turn_returns_the_task_to_the_queue(tmp_path, monkeypatch):
    """A turn that never happened must not leave the task looking claimed."""
    from voicebridge.office import dispatch as D, work as W

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg, workers=1)
    # deliberately NOT funded: admission refuses before the turn runs, which is
    # the real refusal path (a runner-level failure instead returns a FAILED
    # result and legitimately leaves the task in progress)
    never = tmp_path / "absent.txt"
    t = W.create(cfg, "t", exit_check=f"test -f {never}", confirmed=True)["id"]

    out = D.dispatch(cfg, rounds=1, unmetered=True, confirmed=True)
    assert out["results"][0]["code"] == "OFFICE_ADMISSION_REFUSED"
    row = W.tasks(cfg)["tasks"][0]
    assert row["status"] == "open", "a task nobody ran was left claimed"
    assert row["assignee_id"] is None
    kinds = [e["kind"] for e in W.trail(cfg, t)["events"]]
    assert "requeued" in kinds


def test_a_refused_claim_costs_the_seat_nothing(tmp_path, monkeypatch):
    """A turn that never ran must not be charged to anyone.

    Found live: a seat lost a turn to a claim that failed, because the budget was
    consumed before the claim was even attempted.
    """
    from voicebridge.office import dispatch as D, survival as SV, work as W

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg, workers=1)
    wk = ids["workers"][0]
    _pay(cfg, wk)
    t = W.create(cfg, "t", exit_check="false", confirmed=True)["id"]

    # the seat is terminated, so `claim` will refuse it — but force the picker to
    # choose it anyway, which is exactly the case that used to cost a turn
    SV.terminate(cfg, wk, reason="test", confirmed=True)
    monkeypatch.setattr(
        D, "pick_worker",
        lambda cfg_, task, pool=None, extra_load=None: wk)

    before = W.turn_budget(cfg, agent_id=wk)["turns_used"]
    ran = []

    def stub(cfg_, agent_id, task_text):
        ran.append(agent_id)
        return {"state": "OK", "reply": "x", "receipt": {}}

    out = D.dispatch(cfg, rounds=1, executor=stub, unmetered=True,
                     confirmed=True)
    after = W.turn_budget(cfg, agent_id=wk)["turns_used"]
    assert ran == [], "the executor ran for a seat that could not be claimed"
    assert out["results"][0]["state"] == "REFUSED"
    assert out["results"][0]["code"] == "OFFICE_BAD_STATE"
    assert after == before, f"a refused claim was charged: {before} -> {after}"
    # and the task is back in the queue, not left claimed
    assert W.tasks(cfg)["tasks"][0]["status"] == "open"

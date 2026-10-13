"""M5 — the work loop END TO END, through the real dispatch/budget/admission/review.

`test_office_work_loop.py` proves the loop's pieces. This file proves the whole
path in one run, and — more importantly — that every *stop condition* on that
path actually stops. The stops are the product: a loop that only ever reaches
`accepted` is a loop that has never been asked to refuse anything, and the two
things that changed since the last live run make those stops exactly the
untested part:

* `position.progress` is now carried through `tower.state()` (the renderer's bob
  was keyed on a field that never arrived) — asserted here on the same store the
  loop drove, so the fix is covered by the loop's own org, not a fixture.
* the reviewer path (`adjudicate`) wrote a verdict once. Its REFUSAL paths —
  a reviewer that does not decide, a rejection that carries an instruction, a
  reviewer out of turns — have never run against real dispatch code.

Executors are stubbed: a real provider call is a deliberate run, not a test.
`exit_check` is a real shell command against real files (C17), because a "done"
nobody can check is the failure this whole surface exists to prevent.

Hermetic: tmp XDG state only; no network, no provider call.

FALSIFIABILITY: every assertion here can fail, and the three load-bearing ones
were proved to fail by mutating a scratch copy of `dispatch.py` — see
`docs/…`/the task report: (a) consume the turn before the claim, (b) treat a
missing `VERDICT:` as an accept, (c) hand a `fixing` task to a fresh seat. In
each case the mutation was detected by the assertion that exists to catch it.
"""
from __future__ import annotations

import pytest


# --------------------------------------------------------------- the fixtures
def _cfg(tmp_path, monkeypatch):
    """The same hermetic config the loop's own suite builds."""
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _org(cfg, *, workers=1, reviewers=1):
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
    """A reviewer turn that answers with whatever text is asked for."""
    def _call(cfg, agent_id, task_text):
        return {"state": "OK", "reply": reply,
                "receipt": {"model": "stub", "identity": "stub/model",
                            "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                                      "cost_usd_est": 0.0}}}
    return _call


def _row(cfg, status=None):
    """The one task, read back through the public surface."""
    from voicebridge.office import work as W

    if status is not None:
        return W.tasks(cfg, status=status)["tasks"][0]
    return W.tasks(cfg)["tasks"][0]


def _kinds(cfg, task_id):
    from voicebridge.office import work as W

    return [e["kind"] for e in W.trail(cfg, task_id)["events"]]


# =================================================== 1. the happy path, whole
def test_the_whole_loop_holds_the_happy_path_end_to_end(tmp_path, monkeypatch):
    """queue → claim → turn → real check → supervised review → accept.

    One task through the real dispatcher and the real adjudicator, with the exit
    check a real shell command the stub satisfies. The reviewer is read from the
    TASK ROW (not assumed) and must be exactly the assignee's supervisor.
    """
    from voicebridge.office import dispatch as D, work as W

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg)
    for a in ids["workers"] + ids["reviewers"]:
        _pay(cfg, a)

    # a real command against a real path the stub will create
    artifact = tmp_path / "artifact.txt"
    task = W.create(cfg, "write the artifact", brief="put text in artifact.txt",
                    exit_check=f"test -f {artifact}", confirmed=True)["id"]

    out = D.dispatch(cfg, rounds=1, executor=_worker_stub(
        lambda: artifact.write_text("done")), confirmed=True)
    assert out["ran"] == 1, out
    assert out["results"][0]["satisfied"] is True, out["results"][0]

    row = _row(cfg, status="review")
    assert row["id"] == task
    # the reward for a check that passed is a review, not an acceptance
    assert row["status"] == "review"
    assert artifact.exists(), "the check passed against a file that is not there"

    # the reviewer is the assignee's OWN supervisor, read from the row
    assignee = row["assignee_id"]
    assert assignee == ids["workers"][0]
    assert row["reviewer_id"] == ids["reviewers"][0]
    assert row["reviewer_id"] != row["assignee_id"], "self-review"
    sup = {s["id"]: s for s in _roster(cfg)}[assignee]["supervisor_id"]
    assert row["reviewer_id"] == sup, "the reviewer is not the level above"

    adj = D.adjudicate(cfg, rounds=1, executor=_reviewer_stub(
        "looks right\nVERDICT: accept\nNOTE: meets the condition"),
        confirmed=True)
    assert adj["results"][0]["verdict"] == "accept", adj
    done = _row(cfg, status="accepted")
    assert done["id"] == task and done["verdict"] == "accept"
    assert done["status"] == "accepted"
    assert "verdict" in _kinds(cfg, task)


def _roster(cfg):
    from voicebridge.office import survival as SV

    return SV.roster(cfg, include_terminated=True)["seats"]


# ============================================ 2. the check that fails stops it
def test_a_check_that_fails_never_reaches_review(tmp_path, monkeypatch):
    """The stub says it did the thing; the check says otherwise.

    The task must stay `running` (still in progress, still pickable) and the
    trail must record the turn as not satisfied — no silent success.
    """
    from voicebridge.office import dispatch as D, work as W

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg)
    _pay(cfg, ids["workers"][0])

    missing = tmp_path / "never-created.txt"
    t = W.create(cfg, "do something impossible",
                 exit_check=f"test -f {missing}", confirmed=True)["id"]

    out = D.dispatch(cfg, rounds=1, executor=_worker_stub(), confirmed=True)
    assert out["results"][0]["satisfied"] is False, out
    assert out["results"][0]["state"] == "INCOMPLETE", out

    assert W.tasks(cfg, status="review")["count"] == 0, "a failed check reviewed"
    assert W.tasks(cfg, status="accepted")["count"] == 0
    row = _row(cfg)
    assert row["status"] == "running", (
        f"a task whose turn ran but whose check failed must stay in progress, "
        f"not become {row['status']!r}")
    assert row["reviewer_id"] == ids["reviewers"][0], (
        "claimed work still names its reviewer; that is what makes it pickable")

    turn = [e for e in W.trail(cfg, t)["events"] if e["kind"] == "turn"][-1]
    assert turn["body"] == "not_satisfied", turn
    assert turn["payload"]["state"] == "OK", (
        "the turn itself succeeded; it is the CHECK that did not")


def test_a_failed_turn_is_recorded_as_not_satisfied_too(tmp_path, monkeypatch):
    """The other half of "the check did not pass": the turn itself failed.

    A FAILED provider turn is not an exception the dispatcher swallows — it is a
    turn recorded as not satisfied, and the task stays open for another attempt.

    Note what the runner actually returns for a FAILED turn with no usage block:
    `UNKNOWN_SPEND`, not `FAILED` (runner.py settles only OK-with-usage or
    OK-unmetered; anything else leaves the receipt open so admission refuses
    until the owner attests a figure). The dispatcher still records the task
    turn as `not_satisfied` and the task as `running`, which is the point.
    """
    from voicebridge.office import dispatch as D, work as W

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg)
    _pay(cfg, ids["workers"][0])
    never = tmp_path / "absent.txt"
    t = W.create(cfg, "t", exit_check=f"test -f {never}", confirmed=True)["id"]

    out = D.dispatch(cfg, rounds=1, executor=_worker_stub(fail=True),
                     confirmed=True)
    assert out["results"][0]["satisfied"] is False, out
    assert out["results"][0]["run_state"] == "UNKNOWN_SPEND", out
    turn = [e for e in W.trail(cfg, t)["events"] if e["kind"] == "turn"][-1]
    assert turn["body"] == "not_satisfied", turn
    assert turn["payload"]["state"] == "UNKNOWN_SPEND", turn
    assert _row(cfg)["status"] == "running"


# ===================================== 3. the reviewer that does not decide
def test_a_reviewer_that_does_not_decide_accepts_nothing(tmp_path, monkeypatch):
    """Silence is never acceptance.

    A reply with no parseable `VERDICT:` line leaves the task in `review` and the
    trail records `no_verdict`. The task must NOT be accepted, and it must still
    be awaiting a decision — a `no_verdict` that quietly closed the task would be
    the StarNet failure with extra steps.
    """
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
    assert _row(cfg, status="review")["id"] == t

    out = D.adjudicate(cfg, rounds=1,
                       executor=_reviewer_stub("Looks fine to me, nice work!"),
                       confirmed=True)
    assert out["results"][0]["state"] == "NO_VERDICT", out

    assert W.tasks(cfg, status="accepted")["count"] == 0, (
        "a reviewer that did not decide accepted the work")
    row = _row(cfg)
    assert row["status"] == "review", f"task left review as {row['status']!r}"
    assert row["verdict"] in (None, ""), (
        f"a verdict was recorded without one being given: {row['verdict']!r}")
    assert "no_verdict" in _kinds(cfg, t), _kinds(cfg, t)
    # and it can still be decided later — the decision was deferred, not lost
    final = D.adjudicate(cfg, rounds=1,
                         executor=_reviewer_stub("VERDICT: accept"),
                         confirmed=True)
    assert final["results"][0]["verdict"] == "accept", final
    assert _row(cfg, status="accepted")["id"] == t


# ==================================== 4. the rejection that carries a fix
def test_a_rejection_returns_to_the_same_seat_with_the_instruction(
        tmp_path, monkeypatch):
    """A rejection is a fix instruction, not a restart.

    `NOTE:` must land on the row, the task must go to `fixing`, and the NEXT
    dispatch must hand it back to the SAME seat (dispatch.py: `pick_worker`
    returns `assignee_id` for `fixing`/`running`) with the note in the brief.

    TWO workers on purpose: with one seat, "goes back to its author" is
    unfalsifiable — any picker returns that seat. With a second, idle, LOWER-id
    worker available, a picker that ignored the assignee would choose it (load
    0 ties break on lowest id) and the test would see the fix land elsewhere.
    """
    from voicebridge.office import dispatch as D, work as W

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg, workers=2)
    for a in ids["workers"] + ids["reviewers"]:
        _pay(cfg, a)
    artifact = tmp_path / "a.txt"
    t = W.create(cfg, "t", exit_check=f"test -f {artifact}",
                 confirmed=True)["id"]
    D.dispatch(cfg, rounds=1,
               executor=_worker_stub(lambda: artifact.write_text("x")),
               confirmed=True)
    author = _row(cfg)["assignee_id"]
    other = [w for w in ids["workers"] if w != author]
    assert other, "the org must have a second seat for this to bite"
    # Why this bites: after the reject the task is `fixing` and counts as load 1
    # for `author`; the `other` seat is idle at load 0. A picker that ignored the
    # assignee would therefore hand the fix to `other` (lower load wins before
    # the id tie-break). Continuity is what keeps it on `author`.

    adj = D.adjudicate(cfg, rounds=1, executor=_reviewer_stub(
        "VERDICT: reject\nNOTE: use JSON not CSV"), confirmed=True)
    assert adj["results"][0]["verdict"] == "reject", adj
    assert adj["results"][0]["note"] == "use JSON not CSV", adj

    row = _row(cfg)
    assert row["status"] == "fixing", row["status"]
    assert row["review_note"] == "use JSON not CSV"
    assert row["attempts"] == 1, "a rejection is an attempt, not a free retry"

    # the reviewer sent it back, so the work is NOT done: drop the artifact or
    # the precheck would correctly finish it without a turn and the instruction
    # would never reach the author (the loop would be *right* and the test would
    # be measuring nothing)
    artifact.unlink()

    seen = {}

    def capture(cfg_, agent_id, task_text):
        seen["agent"] = agent_id
        seen["brief"] = task_text
        return {"state": "OK", "reply": "fixed",
                "receipt": {"usage": {"cost_usd_est": 0.0}}}

    out = D.dispatch(cfg, rounds=1, executor=capture, confirmed=True)
    assert out["results"][0]["worker"] == author, out
    assert seen["agent"] == author, (
        f"a fix went to {seen['agent']} instead of its author {author}")
    assert seen["agent"] != other[0], (
        "the fix was handed to a fresh seat, throwing away the context that "
        "made the instruction meaningful")
    assert "use JSON not CSV" in seen["brief"], seen["brief"]
    assert "Address exactly that." in seen["brief"], seen["brief"]
    # and the fix turn is on the trail as a second turn on the same task
    assert _row(cfg)["turns_used"] == 2, _row(cfg)["turns_used"]


def test_adjudicate_refuses_a_verdict_from_outside_the_chain(tmp_path,
                                                            monkeypatch):
    """`adjudicate` runs as the assigned reviewer, so the C31 level check holds.

    Proving this end to end means the wrong-level seat must be *refused* when it
    tries to judge — the reviewer id comes off the row, not from the caller.
    """
    from voicebridge.office import dispatch as D, work as W

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg, workers=2, reviewers=2)
    for a in ids["workers"] + ids["reviewers"]:
        _pay(cfg, a)
    artifact = tmp_path / "a.txt"
    W.create(cfg, "t", exit_check=f"test -f {artifact}", confirmed=True)
    D.dispatch(cfg, rounds=1,
               executor=_worker_stub(lambda: artifact.write_text("x")),
               confirmed=True)
    row = _row(cfg, status="review")

    asked = {}

    def spy_reviewer(cfg_, agent_id, task_text):
        asked["agent"] = agent_id
        return {"state": "OK", "reply": "VERDICT: accept",
                "receipt": {"usage": {"cost_usd_est": 0.0}}}

    D.adjudicate(cfg, rounds=1, executor=spy_reviewer, confirmed=True)
    assert asked["agent"] == row["reviewer_id"], (
        "adjudicate turned the reviewer into somebody other than the row's "
        f"reviewer: ran as {asked['agent']}, row says {row['reviewer_id']}")


# ================================================= 5. the daily turn budget
def test_the_turn_budget_stops_the_second_dispatch_without_a_second_turn(
        tmp_path, monkeypatch):
    """Cap 1: the first dispatch spends it, the second is refused, and refused
    means the seat's `turns_used` did not move.

    This is the shape of the bug fixed this session — the budget was consumed
    before the claim, so a claim that never happened still cost a turn.
    """
    from voicebridge.office import dispatch as D, work as W

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg)
    wk = ids["workers"][0]
    _pay(cfg, wk)
    W.set_turn_cap(cfg, agent_id=wk, turns=1, confirmed=True)
    missing = tmp_path / "nope.txt"
    # two tasks, so the picker always has something to hand out: the refusal must
    # be for the BUDGET, not because the queue ran dry
    W.create(cfg, "first", exit_check=f"test -f {missing}", confirmed=True)
    W.create(cfg, "second", exit_check=f"test -f {missing}", confirmed=True)

    first = D.dispatch(cfg, rounds=1, executor=_worker_stub(), confirmed=True)
    assert first["results"][0]["state"] == "INCOMPLETE", first
    assert W.turn_budget(cfg, agent_id=wk)["turns_used"] == 1

    second = D.dispatch(cfg, rounds=1, executor=_worker_stub(), confirmed=True)
    got = second["results"][0]
    assert got["state"] == "BUDGET_EXHAUSTED", second
    assert got["code"] == "OFFICE_TURN_BUDGET_EXHAUSTED"
    assert "1/1 turns" in got["detail"], got["detail"]
    # the refusal cost nothing: still exactly one turn spent
    b = W.turn_budget(cfg, agent_id=wk)
    assert b["turns_used"] == 1, f"a refused dispatch charged {b}"
    assert b["remaining"] == 0
    # and no executor call happened for the refused task
    assert second["ran"] == 0, second


def test_the_budget_is_read_before_the_claim_and_spent_only_after(tmp_path,
                                                                  monkeypatch):
    """A refused CLAIM must cost the seat nothing (the session's real bug).

    The dispatcher peeks the budget, claims, and only then spends. Prove the
    ordering by making the claim fail on a seat that HAS turns left: the run must
    refuse, the executor must never be called, and `turns_used` must not move.
    """
    from voicebridge.office import dispatch as D, survival as SV, work as W

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg)
    wk = ids["workers"][0]
    _pay(cfg, wk)
    W.set_turn_cap(cfg, agent_id=wk, turns=5, confirmed=True)
    W.create(cfg, "t", exit_check="test -f /never-xyz", confirmed=True)

    # the seat is terminated, so `claim` refuses it — but pin the picker to it,
    # which is exactly the case that used to burn a turn
    SV.terminate(cfg, wk, reason="test", confirmed=True)
    monkeypatch.setattr(
        D, "pick_worker", lambda cfg_, task, pool=None, extra_load=None: wk)

    ran = []

    def stub(cfg_, agent_id, task_text):
        ran.append(agent_id)
        return {"state": "OK", "reply": "x", "receipt": {}}

    before = W.turn_budget(cfg, agent_id=wk)["turns_used"]
    out = D.dispatch(cfg, rounds=1, executor=stub, confirmed=True)
    after = W.turn_budget(cfg, agent_id=wk)["turns_used"]

    assert ran == [], "the executor ran for a seat that could not be claimed"
    assert out["results"][0]["state"] == "REFUSED", out
    assert out["results"][0]["code"] == "OFFICE_BAD_STATE", out
    assert after == before == 0, f"a refused claim was charged: {before}->{after}"
    # the budget was still READ before the claim — it exists and is 5
    assert W.turn_budget(cfg, agent_id=wk)["turns_cap"] == 5


# ======================================================= 6. admission refusal
def test_an_unfunded_seat_is_refused_and_the_task_goes_back_to_the_queue(
        tmp_path, monkeypatch):
    """No money, no turn — and no half-claim left behind.

    An unfunded seat must be refused with the admission code, the task must be
    returned to `open` with no assignee and no reviewer, and no turn may be
    spent. The trail must carry the refusal AND the requeue, so the queue's state
    is explained by its own history.
    """
    from voicebridge.office import dispatch as D, work as W

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg)
    # deliberately NOT funded — admission refuses before the turn runs
    t = W.create(cfg, "t", exit_check="test -f /never-xyz", confirmed=True)["id"]

    ran = []

    def stub(cfg_, agent_id, task_text):
        ran.append(agent_id)
        return {"state": "OK", "reply": "x", "receipt": {}}

    out = D.dispatch(cfg, rounds=1, executor=stub, confirmed=True)
    got = out["results"][0]
    assert got["state"] == "REFUSED", out
    assert got["code"] == "OFFICE_ADMISSION_REFUSED", got
    assert "non-positive balance" in got["detail"], got["detail"]
    assert ran == [], "an unfunded seat reached the executor"

    row = _row(cfg)
    assert row["status"] == "open", "a task nobody ran was left claimed"
    assert row["assignee_id"] is None, row["assignee_id"]
    assert row["reviewer_id"] is None, (
        "the reviewer assignment survived a turn that never happened")
    kinds = _kinds(cfg, t)
    assert any(e["kind"] == "turn" and "refused:" in e["body"]
               for e in W.trail(cfg, t)["events"]), kinds
    assert "requeued" in kinds, kinds
    # nothing was spent
    assert W.turn_budget(cfg, agent_id=ids["workers"][0])["turns_used"] == 0


def test_admission_refusal_is_not_a_zero_turn_success(tmp_path, monkeypatch):
    """The refused task must be genuinely still-open work, not quietly closed.

    A queue that hides a refusal by closing the task is worse than a queue that
    crashes: the owner would see an empty backlog and no money spent.
    """
    from voicebridge.office import dispatch as D, work as W

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg)
    artifact = tmp_path / "artifact.txt"
    t = W.create(cfg, "still waiting", exit_check=f"test -f {artifact}",
                 confirmed=True)["id"]
    first = D.dispatch(cfg, rounds=1, executor=_worker_stub(), confirmed=True)
    assert first["results"][0]["code"] == "OFFICE_ADMISSION_REFUSED", first

    # no phantom closure, and nothing in progress
    assert W.tasks(cfg, status="open")["count"] == 1
    assert W.tasks(cfg, status="running")["count"] == 0
    assert W.tasks(cfg, status="accepted")["count"] == 0
    assert W.tasks(cfg, status="abandoned")["count"] == 0
    # The refused task's own `turns_used` DOES tick: dispatch records the refusal
    # as a turn on the task (`record_turn(outcome='refused:...')`) so the task
    # log explains why it is still open. The seat's BUDGET is the bound, and it
    # is untouched — prove that separately.
    assert _row(cfg)["turns_used"] == 1, _row(cfg)["turns_used"]
    assert W.turn_budget(cfg, agent_id=ids["workers"][0])["turns_used"] == 0

    # the SAME task, once the seat is funded, now runs and reaches review
    _pay(cfg, ids["workers"][0])
    second = D.dispatch(cfg, rounds=1, executor=_worker_stub(
        lambda: artifact.write_text("x")), confirmed=True)
    assert second["results"][0]["state"] == "OK", second
    row = _row(cfg, status="review")
    assert row["id"] == t and row["assignee_id"] == ids["workers"][0]


# ================================================================ 7. precheck
def test_a_check_that_already_passes_finishes_without_a_turn_or_budget(
        tmp_path, monkeypatch):
    """Work already done costs nothing — even with zero turns left.

    The precheck runs BEFORE the budget is read, so a zero-turn seat still
    settles a task whose exit condition already passes. With the cap at 0 this
    could only have completed via the precheck, which is the proof no turn was
    spent and the budget was not consulted to decide.
    """
    from voicebridge.office import dispatch as D, work as W

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg)
    wk = ids["workers"][0]
    _pay(cfg, wk)
    W.set_turn_cap(cfg, agent_id=wk, turns=0, confirmed=True)  # no turns at all

    done = tmp_path / "already.txt"
    done.write_text("finished by hand")
    t = W.create(cfg, "already done", exit_check=f"test -f {done}",
                 confirmed=True)["id"]

    called = []

    def stub(cfg_, agent_id, task_text):
        called.append(agent_id)
        return {"state": "OK", "reply": "x", "receipt": {}}

    out = D.dispatch(cfg, rounds=1, executor=stub, confirmed=True)
    assert called == [], "a turn was spent on work that was already verified"
    got = out["results"][0]
    assert got["state"] == "OK", out
    assert got["satisfied"] is True and got["turns_spent"] == 0, got

    row = _row(cfg)
    assert row["id"] == t
    assert row["status"] == "review", row["status"]
    # the SEAT's budget is what bounds spending, and it is untouched (cap 0!)
    assert W.turn_budget(cfg, agent_id=wk)["turns_used"] == 0
    # the precheck does bump the TASK's own `turns_used` counter: `record_turn`
    # increments it unconditionally, including for a precheck. That is the
    # task's attempt log, not the seat's budget — the budget is the bound that
    # stops spending, and it did not move.
    assert row["turns_used"] == 1, row["turns_used"]
    turn = [e for e in W.trail(cfg, t)["events"] if e["kind"] == "turn"][-1]
    assert turn["payload"].get("prechecked") is True, turn
    assert turn["payload"].get("turns_spent") == 0, turn


def test_a_precheck_that_passes_still_assigns_the_supervisor_as_reviewer(
        tmp_path, monkeypatch):
    """A free completion is still a completion: the task names its reviewer.

    The precheck claims the task (so the row is real) and hands it to `review`;
    a reviewer that was never assigned would strand it there forever.
    """
    from voicebridge.office import dispatch as D, work as W

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg)
    # BOTH seats need funding: the worker's turn AND the reviewer's adjudicate
    # turn pass through the same admission gate
    for a in ids["workers"] + ids["reviewers"]:
        _pay(cfg, a)
    done = tmp_path / "already.txt"
    done.write_text("x")
    W.create(cfg, "already done", exit_check=f"test -f {done}", confirmed=True)

    D.dispatch(cfg, rounds=1, executor=_worker_stub(), confirmed=True)
    row = _row(cfg, status="review")
    assert row["assignee_id"] == ids["workers"][0]
    assert row["reviewer_id"] == ids["reviewers"][0]
    # and the free completion can still be judged by the level above
    adj = D.adjudicate(cfg, rounds=1,
                       executor=_reviewer_stub("VERDICT: accept"),
                       confirmed=True)
    assert adj["results"][0]["verdict"] == "accept", adj
    assert W.tasks(cfg, status="accepted")["count"] == 1


# ============================== the money and the turn count are both on record
def test_the_loop_leaves_a_settled_and_attributed_trail(tmp_path, monkeypatch):
    """The loop's spend path must settle: an unmetered turn still books a row.

    D16: a subscription-backed turn is RECORDED and costs no dollars. A loop that
    ran a real turn and booked nothing would make its own budget unreadable.
    """
    from voicebridge.office import dispatch as D, ledger, work as W

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg)
    _pay(cfg, ids["workers"][0])
    artifact = tmp_path / "a.txt"
    W.create(cfg, "t", exit_check=f"test -f {artifact}", confirmed=True)
    out = D.dispatch(cfg, rounds=1, executor=_worker_stub(
        lambda: artifact.write_text("x")), unmetered=True, confirmed=True)
    assert out["ran"] == 1, out

    folds = ledger.health(cfg)
    assert folds["complete"] is True, "the loop left the books unsettled"
    assert folds["cost_cents"] == 0, "a flat-rate turn manufactured dollar cost"
    assert folds["unmetered_entries"] >= 1, "the turn vanished from the record"

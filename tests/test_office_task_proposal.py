"""M5 — the ratchet: a seat can propose work, but never approve its own.

The work loop proves the org can *execute*; these tests prove it can *find*
work without the owner typing every task — the half `90-MILESTONES.md` calls
"the org cannot find work for itself." The refusals are the whole point: a
proposal is not dispatchable, self-approval is refused, a rejection carries a
reason, the pending caps hold, and an approved proposal is an ordinary task.

Hermetic: tmp XDG state only; no provider call, no network. `exit_check` is a
real shell command against real files, because that is what makes "done"
machine-checkable (C17).
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


def _org(cfg, *, workers=2, managers=1):
    """A CEO above M managers above N workers — the chain C30/C31 needs."""
    from voicebridge.office import org as O

    ceo = O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)["id"]
    mgrs = [O.hire(cfg, f"mgr-{i}", seat_type="manager",
                   supervisor_id=ceo, confirmed=True)["id"]
            for i in range(managers)]
    bosses = mgrs or [ceo]
    wks = [O.hire(cfg, f"wk-{i}", seat_type="worker",
                  supervisor_id=bosses[i % len(bosses)], confirmed=True)["id"]
           for i in range(workers)]
    return {"ceo": ceo, "managers": mgrs, "workers": wks}


def _dir_under(cfg, supervisor_id):
    """A director (child of the CEO) so director-level approval is testable."""
    from voicebridge.office import org as O
    return O.hire(cfg, "dir", seat_type="director_portfolio",
                  supervisor_id=supervisor_id, confirmed=True)["id"]


# ==================================================== a proposal is not a task
def test_a_proposal_is_not_dispatchable_before_approval(tmp_path, monkeypatch):
    """The whole point: floating an idea must not put it in the queue."""
    from voicebridge.office import dispatch as D, task_proposal as P, work as W

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg, workers=1)
    wk = ids["workers"][0]

    out = P.propose(cfg, wk, "write the artifact", brief="put text in a.txt",
                    exit_check="true", rationale="it unblocks the pipeline")
    assert out["status"] == "proposed" and out["dispatchable"] is False
    assert P.proposals(cfg)["count"] == 1

    # nothing in the work queue, so the dispatcher has nothing to pick
    assert W.tasks(cfg)["count"] == 0
    ran = []
    result = D.dispatch(cfg, rounds=3,
                        executor=lambda *a, **k: ran.append(1) or
                        {"state": "OK", "reply": "x", "receipt": {}},
                        dry_run=True, confirmed=True)
    assert ran == [], "a turn ran for an unapproved proposal"
    assert result["results"] == [], "the dispatcher saw an unapproved proposal"


def test_a_proposal_needs_a_rationale(tmp_path, monkeypatch):
    from voicebridge.office import task_proposal as P
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg, workers=1)
    with pytest.raises(OfficeError) as exc:
        P.propose(cfg, ids["workers"][0], "do a thing", rationale="  ")
    assert exc.value.code == "OFFICE_BAD_INPUT"
    assert "wish" in exc.value.detail


# ================================================= self-approval is REFUSED
def test_a_seat_may_not_approve_its_own_proposal(tmp_path, monkeypatch):
    from voicebridge.office import task_proposal as P
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg, workers=1, managers=0)
    wk = ids["workers"][0]
    pid = P.propose(cfg, wk, "my own great idea", exit_check="true",
                    rationale="trust me")["id"]

    with pytest.raises(OfficeError) as exc:
        P.approve(cfg, pid, approver_id=wk, confirmed=True)
    assert exc.value.code == "OFFICE_PROPOSAL_SELF_APPROVAL"
    assert "may not approve it" in exc.value.detail
    # refused means refused: still pending, still not a task
    assert P.proposals(cfg)["proposals"][0]["status"] == "proposed"
    from voicebridge.office import work as W
    assert W.tasks(cfg)["count"] == 0


def test_a_seat_may_not_reject_its_own_proposal_either(tmp_path, monkeypatch):
    from voicebridge.office import task_proposal as P
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg, workers=1)
    wk = ids["workers"][0]
    pid = P.propose(cfg, wk, "idea", exit_check="true", rationale="r")["id"]
    with pytest.raises(OfficeError) as exc:
        P.reject(cfg, pid, approver_id=wk, reason="changed my mind",
                 confirmed=True)
    assert exc.value.code == "OFFICE_PROPOSAL_SELF_APPROVAL"


# ============================================ only the level ABOVE may approve
def test_only_the_supervisor_may_approve(tmp_path, monkeypatch):
    from voicebridge.office import task_proposal as P, work as W
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg, workers=2, managers=2)
    wk0, wk1 = ids["workers"]
    mgr0 = ids["managers"][0]
    pid = P.propose(cfg, wk0, "leaf idea", exit_check="true",
                    rationale="r")["id"]

    # a peer worker is not the level above
    with pytest.raises(OfficeError) as exc:
        P.approve(cfg, pid, approver_id=wk1, confirmed=True)
    assert exc.value.code == "OFFICE_PROPOSAL_WRONG_LEVEL"

    # the CEO is above the manager, but not *immediately* above the worker
    with pytest.raises(OfficeError) as exc:
        P.approve(cfg, pid, approver_id=ids["ceo"], confirmed=True)
    assert exc.value.code == "OFFICE_PROPOSAL_WRONG_LEVEL"

    # the worker's own supervisor is exactly one level up -> allowed
    out = P.approve(cfg, pid, approver_id=mgr0, confirmed=True)
    assert out["status"] == "open" and out["dispatchable"] is True
    assert W.tasks(cfg)["count"] == 1


def test_a_director_proposes_to_the_ceo_not_the_owner(tmp_path, monkeypatch):
    """One level up, read from the chain (C31), is the CEO for a director."""
    from voicebridge.office import task_proposal as P
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg, workers=1)
    director = _dir_under(cfg, ids["ceo"])

    level = P.approve_level(cfg, director)
    assert level["approver_id"] == ids["ceo"]
    assert level["seat_type"] == "director_portfolio"

    pid = P.propose(cfg, director, "new business line",
                    exit_check="true", rationale="thesis")["id"]
    # the owner (approver_id=None) may not jump the CEO
    with pytest.raises(OfficeError) as exc:
        P.approve(cfg, pid, approver_id=None, confirmed=True)
    assert exc.value.code == "OFFICE_PROPOSAL_WRONG_LEVEL"
    # the CEO is the level above -> allowed
    assert P.approve(cfg, pid, approver_id=ids["ceo"],
                     confirmed=True)["status"] == "open"


def test_a_ceo_proposal_is_the_owner_only(tmp_path, monkeypatch):
    """Nothing inside the org is above the CEO, so no seat may approve it (D2)."""
    from voicebridge.office import task_proposal as P
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg, workers=1)
    pid = P.propose(cfg, ids["ceo"], "org-wide restructure",
                    exit_check="true", rationale="r")["id"]

    assert P.approve_level(cfg, ids["ceo"])["approver_id"] is None
    with pytest.raises(OfficeError) as exc:
        P.approve(cfg, pid, approver_id=ids["managers"][0], confirmed=True)
    assert exc.value.code == "OFFICE_PROPOSAL_WRONG_LEVEL"
    assert "not an agent" in exc.value.detail
    # the owner (no seat) is the valid approver
    assert P.approve(cfg, pid, approver_id=None,
                     confirmed=True)["status"] == "open"


# ================================================= a rejection needs a reason
def test_a_rejection_without_a_reason_is_refused(tmp_path, monkeypatch):
    """Same rule `work.review` sets: a rejection with no instruction is a delay."""
    from voicebridge.office import task_proposal as P
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg, workers=1)
    wk, mgr = ids["workers"][0], ids["managers"][0]
    pid = P.propose(cfg, wk, "idea", exit_check="true", rationale="r")["id"]

    with pytest.raises(OfficeError) as exc:
        P.reject(cfg, pid, approver_id=mgr, reason="   ", confirmed=True)
    assert exc.value.code == "OFFICE_PROPOSAL_NEEDS_REASON"
    assert "just a delay" in exc.value.detail
    # still pending: a refused rejection changes nothing
    assert P.proposals(cfg)["proposals"][0]["status"] == "proposed"

    out = P.reject(cfg, pid, approver_id=mgr, reason="not on the critical path",
                   confirmed=True)
    assert out["status"] == "rejected" and out["reason"]
    assert P.proposals(cfg)["count"] == 0  # no longer pending
    assert P.proposals(cfg, status="rejected")["count"] == 1


# ==================================================== the caps hold, named
def test_the_per_seat_pending_cap_is_enforced_with_the_limit_named(
        tmp_path, monkeypatch):
    from voicebridge.office import task_proposal as P
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg, workers=1)
    wk = ids["workers"][0]
    cap = int(P.PROPOSAL_LIMITS["per_seat_pending"])

    for i in range(cap):
        P.propose(cfg, wk, f"idea {i}", exit_check="true", rationale="r")
    with pytest.raises(OfficeError) as exc:
        P.propose(cfg, wk, "one too many", exit_check="true", rationale="r")
    assert exc.value.code == "OFFICE_PROPOSAL_CAP"
    assert str(cap) in exc.value.detail, "the cap must be named, not implied"
    assert P.pending(cfg, wk)["seat_pending"] == cap


def test_the_org_pending_cap_is_shared_no_backlog(tmp_path, monkeypatch):
    """The org cap mirrors 14-RND-FLOOR.md's "at most 12 open items"."""
    from voicebridge.office import org as O, task_proposal as P
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    ceo = O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)["id"]
    org_cap = int(P.PROPOSAL_LIMITS["org_pending"])
    per_seat = int(P.PROPOSAL_LIMITS["per_seat_pending"])
    # enough seats to exceed the org cap without hitting any single-seat cap
    seats = [O.hire(cfg, f"wk-{i}", seat_type="worker", supervisor_id=ceo,
                    confirmed=True)["id"]
             for i in range(org_cap // per_seat + 2)]
    made = 0
    for s in seats:
        for _ in range(per_seat):
            if made >= org_cap:
                break
            P.propose(cfg, s, f"i{made}", exit_check="true", rationale="r")
            made += 1
    assert made == org_cap
    with pytest.raises(OfficeError) as exc:
        P.propose(cfg, seats[-1], "backlog", exit_check="true", rationale="r")
    assert exc.value.code == "OFFICE_PROPOSAL_CAP"
    assert str(org_cap) in exc.value.detail


# ==================================== an approved proposal is an ordinary task
def test_an_approved_proposal_is_an_ordinary_task(tmp_path, monkeypatch):
    from voicebridge.office import task_proposal as P, work as W

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg, workers=1)
    wk, mgr = ids["workers"][0], ids["managers"][0]
    # fund the seat: admission refuses a non-positive balance (D1)
    from voicebridge.office import ledger
    ledger.record(cfg, "revenue", 100_000, agent_id=wk)

    # the exit check must be something the turn actually produces, or the
    # precheck finishes the task without spending a turn
    artifact = tmp_path / "artifact.txt"
    pid = P.propose(cfg, wk, "prop task", brief="put text in artifact.txt",
                    exit_check=f"test -f {artifact}",
                    rationale="unblocks the pipeline")["id"]
    out = P.approve(cfg, pid, approver_id=mgr, confirmed=True)
    tid = out["task_id"]

    # it is a first-class task in the queue — work.tasks() shows it
    rows = W.tasks(cfg)["tasks"]
    assert [r["id"] for r in rows] == [tid]
    assert rows[0]["status"] == "open"
    assert rows[0]["exit_check"] == f"test -f {artifact}"

    # the rationale survives as a trail event, so the record of who thought of
    # it and why is not lost
    kinds = [e["kind"] for e in W.trail(cfg, tid)["events"]]
    assert "created" in kinds and "proposed" in kinds

    # and the dispatcher treats it exactly like any other open task
    ran = []

    def stub(cfg_, agent_id, task_text):
        ran.append(agent_id)
        artifact.write_text("done")
        return {"state": "OK", "reply": "done", "receipt": {"usage": {}}}

    from voicebridge.office import dispatch as D
    D.dispatch(cfg, rounds=1, executor=stub, confirmed=True)
    assert ran == [wk], "the dispatcher did not pick the approved proposal"


def test_a_proposal_without_an_exit_check_cannot_be_approved(tmp_path, monkeypatch):
    """C17 holds at approval: no machine-checkable exit condition, no work."""
    from voicebridge.office import task_proposal as P, work as W
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg, workers=1)
    wk, mgr = ids["workers"][0], ids["managers"][0]

    pid = P.propose(cfg, wk, "vague idea", exit_check="", rationale="r")["id"]
    with pytest.raises(OfficeError) as exc:
        P.approve(cfg, pid, approver_id=mgr, confirmed=True)
    assert exc.value.code == "OFFICE_TASK_NEEDS_EXIT_CHECK"
    assert "never the model's own verdict" in exc.value.detail

    # nothing was created, and the proposal is still pending, not lost
    assert W.tasks(cfg)["count"] == 0
    assert P.proposals(cfg)["proposals"][0]["status"] == "proposed"


def test_a_proposal_cannot_be_approved_twice(tmp_path, monkeypatch):
    from voicebridge.office import task_proposal as P, work as W
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg, workers=1)
    wk, mgr = ids["workers"][0], ids["managers"][0]
    pid = P.propose(cfg, wk, "t", exit_check="true", rationale="r")["id"]
    P.approve(cfg, pid, approver_id=mgr, confirmed=True)

    with pytest.raises(OfficeError) as exc:
        P.approve(cfg, pid, approver_id=mgr, confirmed=True)
    assert exc.value.code == "OFFICE_BAD_STATE"
    assert W.tasks(cfg)["count"] == 1

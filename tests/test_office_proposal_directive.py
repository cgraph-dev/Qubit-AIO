"""M5 follow-on — what a seat may, and may not, *propose* (the directive rules).

`test_office_task_proposal.py` proves the proposal layer's mechanics: a proposal
is not dispatchable, self-approval is refused, the caps hold, an approved
proposal is an ordinary task. This module is the **directive layer**: the rules
about what a seat is *allowed to float at all*, derived from the modules that
already exist rather than invented here.

Every rule is pinned against the code as it actually behaves, with the source
line that enforces it (or the source line that fails to). Where a docstring
claims enforcement the code does not provide, the test says so and names both —
that disagreement is the finding, not a bug in the test.

Hermetic: tmp XDG state only; no provider call, no network. An `exit_check` is a
real shell command against real files, because that is what makes "done"
machine-checkable (C17).

Rules asserted here:
  1. budget reality — a proposal that could never be admitted (D1: non-positive
     balance) is neither refused nor flagged at propose time (FINDING).
  2. C17 — an empty `exit_check` is deferred to approval (enforced there); a
     syntactically-fine-but-unsatisfiable check is caught by nobody (FINDING).
  3. the per-seat cap is real and named; the org number matches the doc.
  4. span of control — the proposal layer does not model it at all (FINDING).
  5. the chain above a proposal never inverts, at every level (enforced).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]


def _module_src(module_name: str) -> str:
    """Read a module's source from *the module the tests import*, not a path.

    The doc-vs-code assertions must read the same file the behavioural tests
    exercise. Anchoring on the imported module (rather than a hardcoded repo
    path) is what lets a scratch/mutated copy be tested with the same file, and
    removes a way for the two to silently disagree.
    """
    import importlib

    mod = importlib.import_module(module_name)
    return Path(mod.__file__).read_text()



def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _org(cfg, *, workers=2, managers=1, directors=0):
    """A CEO above M managers above N workers — the chain C30/C31 needs.

    `directors` adds director_portfolio seats directly under the CEO, so the
    director-level approval path is testable.
    """
    from voicebridge.office import org as O

    ceo = O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)["id"]
    dirs = [O.hire(cfg, f"dir-{i}", seat_type="director_portfolio",
                    supervisor_id=ceo, confirmed=True)["id"]
            for i in range(directors)]
    mgrs = [O.hire(cfg, f"mgr-{i}", seat_type="manager",
                   supervisor_id=ceo, confirmed=True)["id"]
            for i in range(managers)]
    bosses = mgrs or dirs or [ceo]
    wks = [O.hire(cfg, f"wk-{i}", seat_type="worker",
                  supervisor_id=bosses[i % len(bosses)], confirmed=True)["id"]
           for i in range(workers)]
    return {"ceo": ceo, "directors": dirs, "managers": mgrs, "workers": wks}


# ===========================================================================
# RULE 1 — a seat may not propose work that violates its own budget reality.
#
# D1 ("the economy is real", 10-DECISIONS.md:73) makes the balance the solvency
# bound: `envelope.check_spend` refuses a spend the seat cannot cover
# (envelope.py:331 `solvent = balance >= int(cents)`; the refusal is raised by
# the `spend()` wrapper at envelope.py:378+). The dispatcher's turn goes through
# that gate (runner.run_turn -> envelope.spend).
#
# So the natural directive would be: a seat with NO balance (non-positive, i.e.
# it cannot pay for even a one-cent turn) must not be able to file a proposal
# that would be refused the moment it is admitted. The question this test
# answers is what the code ACTUALLY does — refuse at propose time, flag it, or
# nothing. It is "nothing", and that is reported as a finding.
# ===========================================================================
def test_rule1_propose_does_not_check_the_seat_can_ever_afford_the_work(
        tmp_path, monkeypatch):
    """FINDING 1: `propose` has no solvency gate and no solvency flag.

    The seat holds 0 cents (`ledger.balance` = 0, which is exactly the state
    `envelope.check_spend` refuses: a 1-cent turn needs `balance >= 1`). If the
    directive layer enforced budget reality at propose time, `propose` would
    refuse; if it *flagged*, the returned record would carry a solvency marker.
    It does neither: the call succeeds and the record is indistinguishable from
    a funded seat's.
    """
    from voicebridge.office import ledger as L, task_proposal as P

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg, workers=1)
    wk, mgr = ids["workers"][0], ids["managers"][0]

    # No ledger revenue for this seat: the balance is exactly 0.
    bal = int(L.balance(cfg, wk)["balance_cents"])
    assert bal == 0, "fixture must start the worker at a zero balance"

    # And admission would indeed refuse a 1-cent turn for this seat.
    from voicebridge.office import envelope as E
    chk = E.check_spend(cfg, agent_id=wk, cents=1)
    assert chk["allowed"] is False and chk["needs_funds"] is True
    assert chk["balance_cents"] == 0

    # ...yet propose() accepts a proposal from that insolvent seat, and the
    # record carries no solvency field at all.
    out = P.propose(cfg, wk, "spend work we cannot pay for",
                    brief="a turn that needs money", exit_check="true",
                    rationale="the balance is irrelevant to this rule")
    assert out["dispatchable"] is False
    assert "afford" not in out and "balance" not in out and "solvent" not in out

    stored = P.proposals(cfg)["proposals"][0]
    for field in ("balance", "solvent", "affordable", "needs_funds"):
        assert field not in stored, (
            f"FINDING: propose stored a {field!r} field — the layer flags "
            f"budget reality after all")

    # The proposal even survives approval — approval (task_proposal.approve,
    # ~line 360) goes through work.create, which has no solvency check either;
    # only the dispatch-time turn is gated. So the org can hold a "proposed"
    # task whose seat can never run it.
    appr = P.approve(cfg, out["id"], approver_id=mgr, confirmed=True)
    assert appr["dispatchable"] is True
    from voicebridge.office import work as W
    assert W.tasks(cfg)["count"] == 1


def test_rule1_envelope_is_the_only_solvency_gate_and_it_is_at_spend_time(
        tmp_path, monkeypatch):
    """The solvency rule itself is real — just enforced elsewhere/at another time.

    This pins that the gate exists (envelope.py:331) and that it is *spend-time*,
    not propose- or approve-time, which is why Rule 1 above finds no enforcement
    in the proposal layer.
    """
    from voicebridge.office import envelope as E, ledger as L

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg, workers=1)
    wk = ids["workers"][0]

    assert E.check_spend(cfg, agent_id=wk, cents=1)["needs_funds"] is True
    # fund it and the same check flips
    L.record(cfg, "revenue", 100, agent_id=wk)
    assert E.check_spend(cfg, agent_id=wk, cents=1)["allowed"] is True


# ===========================================================================
# RULE 2 — a seat may not propose work whose exit check is not
# machine-checkable (C17).
# ===========================================================================
def test_rule2_empty_exit_check_is_deferred_and_refused_at_approval(
        tmp_path, monkeypatch):
    """C17 is enforced at APPROVAL, not creation.

    `propose` accepts `exit_check=''` on purpose (its own docstring,
    task_proposal.py:188-191: "required at approval, not creation").
    `approve` then re-raises `OFFICE_TASK_NEEDS_EXIT_CHECK`
    (task_proposal.py:349-355) before it reaches `work.create`. So the rule is
    "float an idea freely, but nothing becomes work without a machine-checkable
    check" — enforced, one layer later.
    """
    from voicebridge.office import task_proposal as P, work as W
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg, workers=1)
    wk, mgr = ids["workers"][0], ids["managers"][0]

    pid = P.propose(cfg, wk, "vague", exit_check="", rationale="r")["id"]
    with pytest.raises(OfficeError) as exc:
        P.approve(cfg, pid, approver_id=mgr, confirmed=True)
    assert exc.value.code == "OFFICE_TASK_NEEDS_EXIT_CHECK"
    assert "never the model's own verdict" in exc.value.detail

    # nothing became work and the proposal is still pending, not lost
    assert W.tasks(cfg)["count"] == 0
    assert P.proposals(cfg)["proposals"][0]["status"] == "proposed"


def test_rule2_a_check_that_is_syntactically_fine_but_can_never_pass_is_caught_by_nobody(
        tmp_path, monkeypatch):
    """FINDING 2: a check like `test -f /nonexistent/...` sails through.

    The rejection criterion for `exit_check` is *non-emptiness only* — there is
    no syntactic validation and (far more importantly) no satisfiability check
    anywhere reachable from propose or approve. A check that can never succeed
    is accepted at propose time, accepted at approve time, becomes a real task,
    and is discovered only by the dispatcher's precheck failing forever
    (dispatch.py:185-205, the `shell_checker` that returns False).

    This test proves the *upper bound* of the enforcement: the proposal layer
    admits a command that runs and exits non-zero against a path that does not
    exist. It does not claim the layer could, in general, decide satisfiability
    — it claims the layer does not even try, and does not flag it.
    """
    from voicebridge.office import task_proposal as P, work as W
    from voicebridge.office import dispatch as D

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg, workers=1)
    wk, mgr = ids["workers"][0], ids["managers"][0]

    impossible = "test -f /nonexistent/path/xyz-does-not-exist"
    # the command is well-formed and deterministic: it can never pass
    assert D.shell_checker(impossible)({}) is False

    pid = P.propose(cfg, wk, "work that can never be done",
                    brief="nothing will ever satisfy this",
                    exit_check=impossible, rationale="r")["id"]
    appr = P.approve(cfg, pid, approver_id=mgr, confirmed=True)
    assert appr["status"] == "open"
    assert appr["dispatchable"] is True
    row = W.tasks(cfg)["tasks"][0]
    assert row["exit_check"] == impossible

    # and the dispatcher, at the far end, is the first thing to notice — by
    # simply failing the check forever, with nothing upstream having flagged it.
    assert D.shell_checker(row["exit_check"])({}) is False


def test_rule2_approval_refusal_is_named_for_the_proposal_surface(
        tmp_path, monkeypatch):
    """The refusal at approval names the proposal and re-raises, not swallows.

    task_proposal.py:349-355 — `approve` refuses by *re-raising* the same code
    `work.create` would raise, so a taskless "approve" cannot half-commit.
    """
    from voicebridge.office import task_proposal as P, work as W
    from voicebridge.office import store
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg, workers=1)
    wk, mgr = ids["workers"][0], ids["managers"][0]
    # exit_check that is only whitespace is the same as empty
    pid = P.propose(cfg, wk, "blank check", exit_check="   ",
                    rationale="r")["id"]
    with pytest.raises(OfficeError) as exc:
        P.approve(cfg, pid, approver_id=mgr, confirmed=True)
    assert exc.value.code == "OFFICE_TASK_NEEDS_EXIT_CHECK"
    assert f"proposal #{pid}" in exc.value.detail
    assert W.tasks(cfg)["count"] == 0
    # `assert store is not None` used to sit here, where `store` is an imported
    # MODULE -- a module object is never None, so the assertion could not fail and
    # the comment claimed a rollback guarantee that was never tested. What is
    # actually worth pinning is that the refusal left the STORE unchanged, so the
    # task table AND the proposal row are both re-read.
    assert W.tasks(cfg)["count"] == 0, "the refused approval created a task"
    _p = [r for r in P.proposals(cfg, status=None)["proposals"] if r["id"] == pid]
    assert _p and _p[0]["status"] == "proposed", (
        "the refused approval changed the proposal row: "
        f"{_p[0]['status'] if _p else 'row vanished'}")


# ===========================================================================
# RULE 3 — the per-seat cap is real, named, and matches the doc.
# ===========================================================================
def test_rule3_the_per_seat_cap_refuses_the_fourth_and_names_the_limit(
        tmp_path, monkeypatch):
    """`PROPOSAL_LIMITS['per_seat_pending'] == 3` (task_proposal.py:69-72).

    The refusal is raised at task_proposal.py:216-223, and its detail *names*
    the cap ("at its own cap of 3"), so a caller can read the number rather than
    infer it.
    """
    from voicebridge.office import task_proposal as P
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg, workers=1)
    wk = ids["workers"][0]

    assert P.PROPOSAL_LIMITS["per_seat_pending"] == 3
    for i in range(3):
        P.propose(cfg, wk, f"idea {i}", exit_check="true", rationale="r")

    with pytest.raises(OfficeError) as exc:
        P.propose(cfg, wk, "the fourth", exit_check="true", rationale="r")
    assert exc.value.code == "OFFICE_PROPOSAL_CAP"
    # the limit is named in the message, exactly as the cap says
    assert str(P.PROPOSAL_LIMITS["per_seat_pending"]) in exc.value.detail
    assert "cap of 3" in exc.value.detail
    assert P.pending(cfg, wk)["seat_pending"] == 3


def test_rule3_the_cap_counts_only_pending_not_decided(
        tmp_path, monkeypatch):
    """The cap is on `status='proposed'` only; a rejected one frees a slot.

    `_pending_count` (task_proposal.py:128-139) filters `status='proposed'`. A
    seat that lands (approves) or withdraws (rejects) one is back under the cap.
    """
    from voicebridge.office import task_proposal as P

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg, workers=1)
    wk, mgr = ids["workers"][0], ids["managers"][0]

    pids = [P.propose(cfg, wk, f"i{i}", exit_check="true",
                      rationale="r")["id"] for i in range(3)]
    assert P.pending(cfg, wk)["seat_pending"] == 3
    # reject one -> the seat may float another
    P.reject(cfg, pids[0], approver_id=mgr, reason="off strategy",
             confirmed=True)
    assert P.pending(cfg, wk)["seat_pending"] == 2
    P.propose(cfg, wk, "replacement", exit_check="true", rationale="r")
    assert P.pending(cfg, wk)["seat_pending"] == 3


def test_rule3_the_org_number_matches_the_rnd_floor_doc():
    """`org_pending == 12` because 14-RND-FLOOR.md:84 fixes "at most 12".

    This is the doc-vs-code agreement check the task asked for: read the number
    the doc states and assert the module's constant is the same number. The
    module cites it at task_proposal.py:24-27 and :66-68.
    """
    from voicebridge.office import task_proposal as P

    doc = (REPO / "docs" / "game-office" / "14-RND-FLOOR.md").read_text()
    line = doc.splitlines()[83]  # 0-based -> the doc's line 84
    assert "at most 12" in line, f"doc line 84 moved: {line!r}"
    m = re.search(r"at most (\d+)", line)
    assert m is not None
    assert int(m.group(1)) == int(P.PROPOSAL_LIMITS["org_pending"]) == 12, (
        "FINDING: the code's org cap and the doc's number disagree: "
        f"code={P.PROPOSAL_LIMITS['org_pending']} doc={m.group(1)}")


def test_rule3_the_org_cap_is_shared_across_seats(tmp_path, monkeypatch):
    """The org cap refuses the 13th even when no single seat is at its cap."""
    from voicebridge.office import org as O, task_proposal as P
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    ceo = O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)["id"]
    org_cap = int(P.PROPOSAL_LIMITS["org_pending"])
    per_seat = int(P.PROPOSAL_LIMITS["per_seat_pending"])
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
        P.propose(cfg, seats[-1], "one too many", exit_check="true",
                  rationale="r")
    assert exc.value.code == "OFFICE_PROPOSAL_CAP"
    assert str(org_cap) in exc.value.detail


# ===========================================================================
# RULE 4 — span of control bounds what a seat can propose.
# ===========================================================================
def test_rule4_the_proposal_layer_does_not_model_span_at_all(
        tmp_path, monkeypatch):
    """FINDING 3: span of control is not represented in the proposal layer.

    `org._check_span` (org.py:94-104) refuses an 8th direct report at hire time
    with `OFFICE_CAPACITY` — `count >= store.MAX_SPAN` where `MAX_SPAN == 7`
    (store.py:40, D34). That is a *hiring* bound on `office_agent`, and it is
    real.

    But a proposal is not a hire: `propose` never consults a seat's current
    report count, and nothing anywhere refuses a proposal because approving it
    would put a manager over span. The proposal table has no `supervisor_id`
    column and no notion of a leaf count. So the honest answer to "can a manager
    at its span limit propose work that gives it a 9th leaf?" is: the proposal
    layer cannot even express the question — it does not model span.

    This test pins both halves: the hire-time bound is real AND the proposal
    layer has no corresponding check.
    """
    from voicebridge.office import org as O, store, task_proposal as P
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    ceo = O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)["id"]

    # the hire-time span bound is real: the 8th report is refused
    assert store.MAX_SPAN == 7
    mgr = O.hire(cfg, "mgr", seat_type="manager", supervisor_id=ceo,
                 confirmed=True)["id"]
    for i in range(store.MAX_SPAN):
        O.hire(cfg, f"leaf-{i}", seat_type="worker", supervisor_id=mgr,
               confirmed=True)
    with pytest.raises(OfficeError) as exc:
        O.hire(cfg, "leaf-overflow", seat_type="worker", supervisor_id=mgr,
               confirmed=True)
    assert exc.value.code == "OFFICE_CAPACITY"
    assert str(store.MAX_SPAN) in exc.value.detail

    # ...and the proposal layer has no analogous check: the at-span manager
    # files a proposal that would add another leaf, and nothing objects.
    mgr_pid = P.propose(cfg, mgr, "grow the team by one more seat",
                        brief="a 9th leaf under this manager",
                        exit_check="true", rationale="more hands")
    assert mgr_pid["status"] == "proposed"

    # the proposal record itself has no span/leaf/report field to enforce on
    fields = set(P.proposals(cfg)["proposals"][0].keys())
    for field in ("reports", "span", "leaf_count", "direct_reports"):
        assert field not in fields, (
            f"FINDING: the proposal table tracks {field!r} — span may be "
            f"modelled after all")

    # and approval creates an ordinary *task*, not a seat, so the span bound is
    # never consulted: approving does not hire a leaf.
    appr = P.approve(cfg, mgr_pid["id"], approver_id=ceo, confirmed=True)
    assert appr["dispatchable"] is True
    # the org's report count for the manager is unchanged by that approval
    tree = O.tree(cfg)
    seat = next(s for s in tree["employees"] if s["id"] == mgr)
    assert len(seat["reports"]) == store.MAX_SPAN
    assert tree["over_span"] == []


# ===========================================================================
# RULE 5 — the chain above a proposal never inverts.
# ===========================================================================
def test_rule5_approve_level_names_the_right_approver_at_every_level(
        tmp_path, monkeypatch):
    """One level above, read from the live chain (C31; task_proposal.py:142-177).

    For a proposer at every level — leaf, manager, director, CEO — the approver
    is exactly one level up, and the CEO's is the owner (`approver_id is None`),
    matching D2 ("the owner is a shareholder, not the CEO", 10-DECISIONS.md:79).
    """
    from voicebridge.office import task_proposal as P

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg, workers=1, managers=1, directors=1)
    wk = ids["workers"][0]
    mgr = ids["managers"][0]
    director = ids["directors"][0]
    ceo = ids["ceo"]

    # leaf -> its manager
    assert P.approve_level(cfg, wk)["approver_id"] == mgr
    assert P.approve_level(cfg, wk)["approver"] == f"seat:{mgr}"
    # manager -> the CEO (its supervisor)
    assert P.approve_level(cfg, mgr)["approver_id"] == ceo
    # director -> the CEO, *not* the owner (the one-level rule holds for a
    # director exactly as for a worker)
    assert P.approve_level(cfg, director)["approver_id"] == ceo
    assert P.approve_level(cfg, director)["approver"] == f"seat:{ceo}"
    # CEO -> the owner, who is not an agent (D2)
    lvl = P.approve_level(cfg, ceo)
    assert lvl["approver_id"] is None
    assert lvl["approver"] == "owner"


def test_rule5_each_level_can_approve_exactly_the_person_below(
        tmp_path, monkeypatch):
    """The chain never inverts: the wrong level is refused with a named code.

    A peer cannot approve a peer (C31: "the reviewer's level must be exactly one
    above"); a manager cannot approve a director's proposal (that would be
    *below*); the owner cannot jump the CEO for a director proposal.
    """
    from voicebridge.office import task_proposal as P
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg, workers=2, managers=1, directors=1)
    wk0, wk1 = ids["workers"]
    mgr, director, ceo = ids["managers"][0], ids["directors"][0], ids["ceo"]

    pid = P.propose(cfg, wk0, "leaf idea", exit_check="true",
                    rationale="r")["id"]

    # a peer leaf is not the level above
    with pytest.raises(OfficeError) as exc:
        P.approve(cfg, pid, approver_id=wk1, confirmed=True)
    assert exc.value.code == "OFFICE_PROPOSAL_WRONG_LEVEL"
    # the CEO is above the manager but not *immediately* above the leaf
    with pytest.raises(OfficeError) as exc:
        P.approve(cfg, pid, approver_id=ceo, confirmed=True)
    assert exc.value.code == "OFFICE_PROPOSAL_WRONG_LEVEL"
    # the immediate supervisor works
    assert P.approve(cfg, pid, approver_id=mgr,
                     confirmed=True)["status"] == "open"

    # a director proposal: the manager *below* cannot approve it, and the owner
    # (None) cannot jump the CEO
    dpid = P.propose(cfg, director, "new business line", exit_check="true",
                     rationale="thesis")["id"]
    with pytest.raises(OfficeError) as exc:
        P.approve(cfg, dpid, approver_id=mgr, confirmed=True)
    assert exc.value.code == "OFFICE_PROPOSAL_WRONG_LEVEL"
    with pytest.raises(OfficeError) as exc:
        P.approve(cfg, dpid, approver_id=None, confirmed=True)
    assert exc.value.code == "OFFICE_PROPOSAL_WRONG_LEVEL"
    assert P.approve(cfg, dpid, approver_id=ceo,
                     confirmed=True)["status"] == "open"


def test_rule5_self_approval_is_refused_with_the_named_code(
        tmp_path, monkeypatch):
    """A seat may never approve (or reject) its own proposal (C31).

    task_proposal.py:308-313 (approve) and :429-434 (reject) raise
    `OFFICE_PROPOSAL_SELF_APPROVAL`.
    """
    from voicebridge.office import task_proposal as P, work as W
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg, workers=1, managers=0)
    wk = ids["workers"][0]
    pid = P.propose(cfg, wk, "my own idea", exit_check="true",
                    rationale="trust me")["id"]

    with pytest.raises(OfficeError) as exc:
        P.approve(cfg, pid, approver_id=wk, confirmed=True)
    assert exc.value.code == "OFFICE_PROPOSAL_SELF_APPROVAL"
    assert "may not approve it" in exc.value.detail
    assert P.proposals(cfg)["proposals"][0]["status"] == "proposed"
    assert W.tasks(cfg)["count"] == 0

    with pytest.raises(OfficeError) as exc:
        P.reject(cfg, pid, approver_id=wk, reason="changed my mind",
                 confirmed=True)
    assert exc.value.code == "OFFICE_PROPOSAL_SELF_APPROVAL"


def test_rule5_ceo_proposal_routes_to_the_owner_and_no_seat_may_take_it(
        tmp_path, monkeypatch):
    """D2: the CEO's supervisor is the owner, not a seat (task_proposal.py:316-324)."""
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
    # only the owner (no seat) may take it
    assert P.approve(cfg, pid, approver_id=None,
                     confirmed=True)["status"] == "open"


def test_rule5_terminated_seats_cannot_propose_or_receive(
        tmp_path, monkeypatch):
    """A terminated seat is refused on both sides of the chain.

    `propose` (task_proposal.py:212-214) and `approve_level` (task_proposal.py:
    164-166) both raise `OFFICE_BAD_STATE` for a terminated seat.
    """
    from voicebridge.office import org as O, task_proposal as P
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg, workers=1)
    wk = ids["workers"][0]
    # terminate the worker
    conn = None
    from voicebridge.office import store
    conn = store.connect(cfg)
    try:
        conn.execute("UPDATE office_agent SET status='terminated' WHERE id=?",
                     (wk,))
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(OfficeError) as exc:
        P.propose(cfg, wk, "posthumous idea", exit_check="true", rationale="r")
    assert exc.value.code == "OFFICE_BAD_STATE"
    assert "terminated" in exc.value.detail

    with pytest.raises(OfficeError) as exc:
        P.approve_level(cfg, wk)
    assert exc.value.code == "OFFICE_BAD_STATE"


# ===========================================================================
# The docstring-vs-code audit: findings where prose overstates the code.
# ===========================================================================
def test_finding_module_docstring_cites_escalation_321_which_is_resolve_not_ask_owner():
    """FINDING 4 (documentation): task_proposal.py cites the wrong escalation rule.

    task_proposal.py:18-21 says the approver rule is "the same rule
    `escalation.resolve` enforces at ``escalation.py:321``". Line 321 IS the
    `by_agent != supervisor_id` refusal inside `resolve`, so the citation is
    correct for `resolve`. But the module's own prose (task_proposal.py:15-21)
    frames the whole thing as the closest analogue to `ask_owner`, and
    `ask_owner` — the mechanism the docstring's framing leans on — enforces no
    such rule at all: it mints a journal question addressed to the owner
    (escalation.py:118-192) and never checks who may answer. `answer_question`
    (escalation.py:220+) takes any `answered_by`. So "the same rule the
    escalation path enforces" is true of `resolve` and false of the
    ask_owner-shaped path; the one-level rule is real in `resolve`, not
    universal across escalation.

    This test pins the two facts so the docstring cannot quietly drift into
    claiming a universal escalation-level rule.
    """
    esc = _module_src("voicebridge.office.escalation")
    lines = esc.splitlines()

    # line 321 (1-based) is the level refusal in `resolve`
    assert "by_agent" in lines[320] and "supervisor_id" in lines[320], (
        f"escalation.py:321 is not the level rule: {lines[320]!r}")
    assert "OFFICE_ESCALATION_WRONG_LEVEL" in esc

    # `ask_owner` exists and enforces no approver level
    assert "def ask_owner" in esc
    assert "def answer_question" in esc
    assert "wrong_level" not in esc.split("def ask_owner", 1)[1].split(
        "def questions", 1)[0].lower()

    # the module docstring names escalation.resolve, not ask_owner
    tp = _module_src("voicebridge.office.task_proposal")
    assert "escalation.py:321" in tp
    assert "escalation.resolve" in tp


def test_finding_self_approval_rule_holds_at_both_decide_surfaces():
    """FINDING 5 (non-finding, pinned): unlike `work.review`, propose/approve
    enforce one-level *and* self-approval with dedicated codes.

    `work.review` refuses a wrong-level reviewer with
    `OFFICE_REVIEW_WRONG_LEVEL` (work.py:199-203) but has no self-approval check
    code of its own — the separation comes from the chain at claim time
    (work.py:133). The proposal layer adds the explicit
    `OFFICE_PROPOSAL_SELF_APPROVAL` at both `approve` and `reject`
    (task_proposal.py:308, :429). This pins that the proposal surface is
    *stricter* than the work surface, so a future reader does not assume the
    work surface already had it.
    """
    tp = _module_src("voicebridge.office.task_proposal")
    # the alias is defined once; both decide surfaces raise it (approve, reject)
    assert tp.count("SELF_APPROVAL_CODE = ") == 1
    approve_body = tp.split("def approve(", 1)[1].split("def reject(", 1)[0]
    reject_body = tp.split("def reject(", 1)[1].split("def pending(", 1)[0]
    assert "SELF_APPROVAL_CODE" in approve_body
    assert "SELF_APPROVAL_CODE" in reject_body
    wk = _module_src("voicebridge.office.work")
    assert "OFFICE_REVIEW_WRONG_LEVEL" in wk
    assert "OFFICE_REVIEW_SELF_APPROVAL" not in wk

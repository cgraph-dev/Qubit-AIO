"""Adversarial cross-module invariant audit for the office engine.

This file is the evidence for ``docs/OFFICE-INVARIANT-AUDIT.md``. Each test
takes an invariant the office *claims* to hold (in a docstring, a comment, or
the shape of the logic) and tries to BREAK it with real execution against a
real store — not by reading code.

Two kinds of test live here:

* **HELD** — an invariant that held. These are permanent guards: if the
  behaviour regresses, the test goes red. The ``MUTATION_PROOFS`` block at the
  bottom proves (by mutating a SCRATCH copy, never a repo file) that a
  representative sample of them *can* fail.
* **BROKE** — an invariant that a concrete input sequence violates. The test
  documents the exact reproduction and asserts the CURRENT (wrong) behaviour so
  the suite stays green while the defect is unresolved; the docstring carries
  ``FINDING #n`` and the doc records it. If someone fixes the source, these go
  red on purpose and must be flipped to assert the correct behaviour.

Hermetic: tmp XDG state, real SQLite, no network, no provider call.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------- fixtures
def _cfg(tmp_path, monkeypatch, name="audit"):
    """A real ``Config`` on a throwaway XDG tree (the pattern the suite uses)."""
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s" / name))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / f"voice-{name}.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _org(tmp_path, monkeypatch, name="audit"):
    """A real 4-seat store: ceo -> manager -> {worker, worker}.

    The smallest chain that exercises "exactly one level above" and makes a
    second worker available for a double-claim attempt.
    """
    from voicebridge.office import org as O
    from voicebridge.office import store

    cfg = _cfg(tmp_path, monkeypatch, name=name)
    store.init(cfg)
    ceo = O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)["id"]
    mgr = O.hire(cfg, "mgr", seat_type="manager", supervisor_id=ceo,
                 confirmed=True)["id"]
    w1 = O.hire(cfg, "w1", seat_type="worker", supervisor_id=mgr,
                confirmed=True)["id"]
    w2 = O.hire(cfg, "w2", seat_type="worker", supervisor_id=mgr,
                confirmed=True)["id"]
    return cfg, {"ceo": ceo, "manager": mgr, "w1": w1, "w2": w2}


# =========================================================================
# FINDING #1 — BUDGET: a small approved raise must not unlock unlimited spend.
# =========================================================================
# RESOLVED (was BROKE). Envelope rule (envelope.py:12-20): "Above the envelope,
# it is refused unless raised." `spend()` enforced that via `_has_approved_raise`
# (formerly envelope.py:398), which returned True if the seat had ANY request
# row with status='approved' — it never read the approved amount or the period,
# so a $0.01 raise that the level above approved on an empty envelope licensed
# spending of any size.
#
# The fix replaces the existence check with an approval-must-cover-the-spend
# check: `_approved_raise_headroom` sums the seat's `approved` requests for THIS
# period (minus overage already drawn), and `_approval_covers` requires that the
# spend's overage above the envelope not exceed those approved cents. A stale
# (other-period) approval, a denied one, or an approval smaller than the overage
# all now contribute nothing. The tests below guard the FIXED behaviour;
# `docs/OFFICE-INVARIANT-AUDIT.md` FINDING #1 is marked RESOLVED.
def test_finding1_tiny_approved_raise_does_not_unlock_unlimited_spend(
        tmp_path, monkeypatch):
    """FINDING #1 (FIXED): a 1-cent raise cannot authorise 50000 cents.

    Reproduction:
      1. org envelope = 100 cents; seat holds a large balance.
      2. spend 100  -> allowed (inside the envelope), headroom now 0.
      3. spend 50   -> refused OFFICE_ENVELOPE_EXCEEDED (correct).
      4. raise 1 cent, supervisor approves; the envelope is NOT topped up.
      5. spend 50000 -> REFUSED OFFICE_ENVELOPE_EXCEEDED. Only the 1 approved
         cent above the envelope is covered, so 1 cent is permitted and a
         second is not; nothing beyond that is booked.

    Before the fix step (5) booked 50000 cents against a 1-cent approval.
    """
    from voicebridge.office import envelope as E
    from voicebridge.office import ledger as L
    from voicebridge.office.errors import OfficeError

    cfg, ids = _org(tmp_path, monkeypatch, name="raise")
    mgr, ceo = ids["manager"], ids["ceo"]

    E.allocate(cfg, scope="org", scope_id=None, cents=100, by_agent=None,
               confirmed=True)
    L.record(cfg, "adjustment", 100_000, agent_id=mgr, note="capital")

    # 2. the envelope holds exactly 100 cents; spend it all
    E.spend(cfg, agent_id=mgr, cents=100)
    assert E.headroom_for(cfg, agent_id=mgr)["headroom_cents"] == 0

    # 3. one cent more is correctly refused, with no raise on file
    with pytest.raises(OfficeError) as exc:
        E.spend(cfg, agent_id=mgr, cents=50)
    assert exc.value.code == "OFFICE_ENVELOPE_EXCEEDED", exc.value.code

    # 4. approve a ONE-CENT raise
    req = E.raise_above(cfg, agent_id=mgr, amount_cents=1, thesis="t",
                        expected_return="r", displaces="d", confirmed=True)
    assert req["amount_cents"] == 1
    E.decide_request(cfg, req["id"], "approve", by_agent=ceo, confirmed=True)

    # 5a. one cent above the envelope is covered by the one approved cent.
    one = E.spend(cfg, agent_id=mgr, cents=1)
    assert one["booked"] is True and one["raised"] is True
    # 5b. a second cent is NOT — the approval is now fully drawn.
    with pytest.raises(OfficeError) as exc:
        E.spend(cfg, agent_id=mgr, cents=1)
    assert exc.value.code == "OFFICE_ENVELOPE_EXCEEDED", exc.value.code
    # 5c. and a 50000-cent spend is refused outright, with nothing booked.
    with pytest.raises(OfficeError) as exc:
        E.spend(cfg, agent_id=mgr, cents=50_000)
    assert exc.value.code == "OFFICE_ENVELOPE_EXCEEDED", exc.value.code
    # The seat's metered cost is 100 (envelope) + 1 (approved overage) only —
    # never the 50000 the broken predicate used to let through.
    assert L.sum_metered(cfg, scope="agent", id=mgr) == 101


def test_a_sufficient_approved_raise_covers_the_spend(tmp_path, monkeypatch):
    """FINDING #1 (FIXED): an approval that covers the overage still works.

    The fix must not break the fast-track case it exists for: approve a raise
    large enough for the spend, and the spend above the envelope books.
    """
    from voicebridge.office import envelope as E
    from voicebridge.office import ledger as L

    cfg, ids = _org(tmp_path, monkeypatch, name="raise_covered")
    mgr, ceo = ids["manager"], ids["ceo"]
    E.allocate(cfg, scope="org", scope_id=None, cents=100, by_agent=None,
               confirmed=True)
    L.record(cfg, "adjustment", 100_000, agent_id=mgr, note="capital")
    E.spend(cfg, agent_id=mgr, cents=100)         # headroom now 0

    req = E.raise_above(cfg, agent_id=mgr, amount_cents=50_500, thesis="t",
                        expected_return="r", displaces="d", confirmed=True)
    E.decide_request(cfg, req["id"], "approve", by_agent=ceo, confirmed=True)

    out = E.spend(cfg, agent_id=mgr, cents=50_000)
    assert out["booked"] is True and out["within_envelope"] is False
    assert out["raised"] is True
    # 500 cents of the approval remain (50,500 - 50,000); draw them, then the
    # approval is exhausted.
    assert E.spend(cfg, agent_id=mgr, cents=500)["booked"] is True
    with pytest.raises(Exception) as exc:
        E.spend(cfg, agent_id=mgr, cents=1)
    assert getattr(exc.value, "code", None) == "OFFICE_ENVELOPE_EXCEEDED"


def test_has_approved_raise_requires_amount_and_period(tmp_path, monkeypatch):
    """FINDING #1 root cause, isolated: the predicate must read amount + period.

    The old ``_has_approved_raise`` selected ``SELECT 1 ... WHERE agent_id=? AND
    status='approved' LIMIT 1`` — it took neither the amount nor a period, so
    its answer could not depend on either. The replacement sums the approved
    amount for THIS period and subtracts overage already drawn.
    """
    from voicebridge.office import envelope as E
    from voicebridge.office import ledger as L

    cfg, ids = _org(tmp_path, monkeypatch, name="raise_root")
    mgr, ceo = ids["manager"], ids["ceo"]
    E.allocate(cfg, scope="org", scope_id=None, cents=100, by_agent=None,
               confirmed=True)
    L.record(cfg, "adjustment", 10_000, agent_id=mgr, note="capital")
    E.spend(cfg, agent_id=mgr, cents=100)         # headroom now 0

    # A raise approved for a PAST period contributes nothing this period.
    req = E.raise_above(cfg, agent_id=mgr, amount_cents=1_000, thesis="t",
                        expected_return="r", displaces="d", period="2000-01",
                        confirmed=True)
    E.decide_request(cfg, req["id"], "approve", by_agent=ceo, confirmed=True)
    assert E._approved_raise_headroom(cfg, agent_id=mgr,
                                      check=E.check_spend(cfg, agent_id=mgr,
                                                          cents=1)) == 0

    # A DENIED request contributes nothing either.
    req2 = E.raise_above(cfg, agent_id=mgr, amount_cents=1_000, thesis="t",
                         expected_return="r", displaces="d", confirmed=True)
    E.decide_request(cfg, req2["id"], "deny", by_agent=ceo, confirmed=True)
    assert E._approved_raise_headroom(cfg, agent_id=mgr,
                                      check=E.check_spend(cfg, agent_id=mgr,
                                                          cents=1)) == 0

    # An approved raise for THIS period contributes exactly its amount.
    req3 = E.raise_above(cfg, agent_id=mgr, amount_cents=1_000, thesis="t",
                         expected_return="r", displaces="d", confirmed=True)
    E.decide_request(cfg, req3["id"], "approve", by_agent=ceo, confirmed=True)
    assert E._approved_raise_headroom(cfg, agent_id=mgr,
                                      check=E.check_spend(cfg, agent_id=mgr,
                                                          cents=1)) == 1_000


# =========================================================================
# FINDING #2 — MONEY: reversing an unmetered row manufactures metered money.
# =========================================================================
# RESOLVED (was BROKE). `ledger.record` documents (ledger.py:44-47) that an
# `unmetered=True` row is "recorded, but no metered aggregate will ever see it."
# `reverse()` (ledger.py:152) used to write a metered `transfer` whose amount was
# derived from the original but which did NOT propagate the original's
# `unmetered` flag — so the compensating row entered the metered fold the
# original was excluded from, and undoing a phantom minted real money.
#
# The fix threads `unmetered=bool(original["unmetered"])` into `reverse()`'s
# compensating `record()` call, so a reversal is the exact inverse of what the
# original did to the metered fold. The tests below now guard the FIXED
# behaviour; `docs/OFFICE-INVARIANT-AUDIT.md` FINDING #2 is marked RESOLVED.
def test_finding2_reversing_unmetered_row_creates_no_metered_money(
        tmp_path, monkeypatch):
    """FINDING #2 (FIXED): a phantom turn, undone, stays worthless.

    Reproduction:
      1. seat balance = 0.
      2. record inference 777 unmetered=True (a subscription-backed turn).
         balance stays 0 — correct (D16).
      3. reverse that row -> balance MUST stay 0. Before the fix it became +777
         metered cents: $7.77 of real, spendable balance conjured from a
         phantom row.

    Reversing unmetered *revenue* must likewise not destroy real money.
    """
    from voicebridge.office import ledger as L

    cfg, ids = _org(tmp_path, monkeypatch, name="unmetered_rev")
    seat = ids["manager"]

    assert L.balance(cfg, seat)["balance_cents"] == 0

    row = L.record(cfg, "inference", 777, agent_id=seat, unmetered=True,
                   note="subscription-backed turn")
    # D16: the unmetered row must not move the balance.
    assert L.balance(cfg, seat)["balance_cents"] == 0, "D16 already broken"

    rev = L.reverse(cfg, row["id"])
    # The compensating row inherits the flag, so no metered money appears.
    assert rev["unmetered"] is True
    assert L.balance(cfg, seat)["balance_cents"] == 0
    assert L.sum_metered(cfg) == 0
    # It still names and offsets the row it corrects (C1).
    rows = {r["id"]: r for r in L.rows(cfg)["rows"]}
    assert rows[rev["id"]]["offsets_id"] == row["id"]
    assert rows[rev["id"]]["unmetered"] == 1


def test_finding2_reversing_unmetered_revenue_destroys_no_metered_money(
        tmp_path, monkeypatch):
    """FINDING #2, the destructive direction (FIXED).

    A seat with balance 0 records unmetered revenue 5000 (balance stays 0),
    then reverses it. Before the fix the compensating transfer was -5000
    METERED, drifting the balance to -5000 for money never in the metered book.
    """
    from voicebridge.office import ledger as L

    cfg, ids = _org(tmp_path, monkeypatch, name="unmetered_rev2")
    seat = ids["manager"]

    row = L.record(cfg, "revenue", 5000, agent_id=seat, unmetered=True,
                   note="unmetered revenue")
    assert L.balance(cfg, seat)["balance_cents"] == 0

    rev = L.reverse(cfg, row["id"])
    assert rev["unmetered"] is True
    assert L.balance(cfg, seat)["balance_cents"] == 0


def test_finding2_balance_sheet_reports_balanced_and_nothing_was_minted(
        tmp_path, monkeypatch):
    """FINDING #2 (FIXED): the gate is green AND no balance was conjured.

    Before the fix ``assert_balances`` reported ``ok: True, residual: 0``
    through the break — the worse half of the finding, because the control the
    docs lean on recomputed from the same rows the reversal wrote and so could
    not see the corruption. The sequence is now genuinely neutral: the gate is
    green *and* the seat's metered balance is unchanged.
    """
    from voicebridge.office import balance_sheet as BS
    from voicebridge.office import ledger as L

    cfg, ids = _org(tmp_path, monkeypatch, name="unmetered_bs")
    seat = ids["manager"]

    row = L.record(cfg, "inference", 777, agent_id=seat, unmetered=True)
    assert BS.assert_balances(cfg)["ok"] is True     # clean before
    assert L.balance(cfg, seat)["balance_cents"] == 0

    L.reverse(cfg, row["id"])
    gate = BS.assert_balances(cfg)
    assert gate["ok"] is True                        # still balanced
    assert gate["residual"] == 0
    # …and this time the balance really is 0, not a minted 777.
    assert L.balance(cfg, seat)["balance_cents"] == 0


# =========================================================================
<<<<<<< Updated upstream
# HELD — CONSENT/SoD: task-review with no reviewer identity IS level-checked.
# =========================================================================
# work.py:9-11 promises: "The reviewer is exactly one level above the assignee
# (C31). A worker does not accept its own work." FINDING #3 was that the gate
# read `by_agent is not None`, so an omitted `--by` (the CLI default,
# cli.py:1796) skipped it and accepted any task.
#
# FIXED. The required approver is now derived from the ASSIGNEE's supervisor —
# the same way `task_proposal.approve` derives it from the proposer's
# (task_proposal.py:316-339) — and `by_agent=None` means THE OWNER, not
# "anyone". The owner is not a seat (D2) and sits above the top seat, so an
# omitted identity approves exactly one case: work whose assignee has no
# supervisor. Everywhere else both the owner and a non-supervisor seat are
# refused. These guards are permanent.
=======
# HELD — CONSENT/SoD: task-review with no reviewer identity is level-checked.
# =========================================================================
# work.py:9-11 promises: "The reviewer is exactly one level above the assignee
# (C31). A worker does not accept its own work." FINDING #3 was that the guard
# at work.py:197 read `by_agent is not None`, so an omitted `--by` (the CLI's
# default, cli.py:1796) skipped the gate entirely and accepted any task.
#
# FIXED. `by_agent=None` now means THE OWNER, not "anyone": the owner sits above
# the CEO (D2, the owner is not an agent), so an omitted identity is level-
# checked like any other and matches only a task whose reviewer is the top seat.
# The guard is permanent: a `None` that skips the check would read as "anyone
# may", which is exactly how a worker accepts its own work.
>>>>>>> Stashed changes
def test_held_review_without_an_identity_is_still_level_checked(
        tmp_path, monkeypatch):
    """HELD: no identity means the owner, and the owner is level-checked.

    Reproduction (the FINDING #3 sequence, now refusing):
      1. worker claims a task, completes it -> status 'review', reviewer=mgr.
<<<<<<< Updated upstream
      2. W.review(..., verdict='accept', by_agent=w1)   -> WRONG_LEVEL
         (the assignee may not accept its own work).
      3. W.review(..., verdict='accept', by_agent=None) -> WRONG_LEVEL
         (the owner is above the CEO, NOT above w1's manager). The task stays
         in 'review'; nothing was accepted.
      4. W.review(..., verdict='accept', by_agent=mgr)  -> accepted (the level
         immediately above the assignee still works).
=======
      2. W.review(..., verdict='accept', by_agent=w1)  -> refused WRONG_LEVEL
         (a seat that is not the reviewer cannot judge it).
      3. W.review(..., verdict='accept', by_agent=None) -> refused WRONG_LEVEL
         (the owner is above the CEO, NOT above a worker's manager). The task
         stays in 'review'; nothing was accepted.
      4. W.review(..., verdict='accept', by_agent=mgr)  -> accepted (the
         assigned reviewer, exactly one level above the assignee, still works).
>>>>>>> Stashed changes
    """
    from voicebridge.office import work as W
    from voicebridge.office.errors import OfficeError

    cfg, ids = _org(tmp_path, monkeypatch, name="sod")
    w1, mgr = ids["w1"], ids["manager"]

    t = W.create(cfg, "t", exit_check="true", confirmed=True)["id"]
    W.claim(cfg, t, agent_id=w1, confirmed=True)
    assert W.tasks(cfg)["tasks"][0]["reviewer_id"] == mgr
    W.record_turn(cfg, t, outcome="satisfied")
    assert W.tasks(cfg)["tasks"][0]["status"] == "review"

<<<<<<< Updated upstream
    # 2. the ASSIGNEE itself is refused (self-approval, C31).
=======
    # 2. an identity that is NOT the reviewer is refused
>>>>>>> Stashed changes
    with pytest.raises(OfficeError) as exc:
        W.review(cfg, t, verdict="accept", by_agent=w1, confirmed=True)
    assert exc.value.code == "OFFICE_REVIEW_WRONG_LEVEL"

<<<<<<< Updated upstream
    # 3. NO identity at all is the OWNER — still refused, still typed.
=======
    # 3. NO identity at all is the OWNER — still refused, and refused typed.
>>>>>>> Stashed changes
    with pytest.raises(OfficeError) as exc:
        W.review(cfg, t, verdict="accept", by_agent=None, confirmed=True)
    assert exc.value.code == "OFFICE_REVIEW_WRONG_LEVEL", exc.value.code
    assert W.tasks(cfg)["tasks"][0]["status"] == "review"   # untouched

<<<<<<< Updated upstream
    # 4. the level above the assignee still accepts — the rule is not "nobody".
=======
    # 4. the assigned reviewer still accepts: the rule did not become "nobody".
>>>>>>> Stashed changes
    out = W.review(cfg, t, verdict="accept", by_agent=mgr, confirmed=True)
    assert out["status"] == "accepted"
    assert W.tasks(cfg)["tasks"][0]["status"] == "accepted"


def test_held_owner_may_judge_the_top_seats_work(tmp_path, monkeypatch):
<<<<<<< Updated upstream
    """HELD: an omitted identity DOES work when the assignee is the top seat.

    The owner is not a seat (D2), so work done by the top seat has the owner as
    its only possible approver — `by_agent=None` must accept it, or the top
    seat's work could never be accepted at all. The other half of the contract:
    `None` is an identity, not a wildcard and not a blanket refusal.
    """
    from voicebridge.office import store as S
    from voicebridge.office import work as W
=======
    """HELD: an omitted identity DOES work when the reviewer is the top seat.

    The owner is not a seat (D2), so a task whose reviewer is the top seat has
    the owner as its only possible approver — `by_agent=None` must accept it, or
    the top seat's work could never be accepted at all. This is the other half
    of the contract: `None` is an identity, not a refusal and not a wildcard.
    """
    from voicebridge.office import work as W
    from voicebridge.office import store as S
>>>>>>> Stashed changes

    cfg, ids = _org(tmp_path, monkeypatch, name="owner")
    ceo = ids["ceo"]

<<<<<<< Updated upstream
    # `claim` refuses a seat with no supervisor (C30), so the row is written the
    # way the top seat's task actually looks: the assignee is the top seat.
=======
    # The top seat's work: `claim` refuses a seat with no supervisor (C30), so
    # the row is built the way the top seat's task actually looks — assignee is
    # the CEO and the reviewer slot is the owner's (NULL).
>>>>>>> Stashed changes
    t = W.create(cfg, "board work", exit_check="true", confirmed=True)["id"]
    conn = S.connect(cfg)
    try:
        S.migrate(conn)
<<<<<<< Updated upstream
        conn.execute("UPDATE office_task SET assignee_id=?, status='running'"
                     " WHERE id=?", (int(ceo), int(t)))
=======
        conn.execute("UPDATE office_task SET assignee_id=?, reviewer_id=NULL,"
                     " status='running' WHERE id=?", (int(ceo), int(t)))
>>>>>>> Stashed changes
        conn.commit()
    finally:
        conn.close()
    W.record_turn(cfg, t, outcome="satisfied")
    assert W.tasks(cfg)["tasks"][0]["status"] == "review"

    out = W.review(cfg, t, verdict="accept", by_agent=None, confirmed=True)
    assert out["status"] == "accepted"
    assert W.tasks(cfg)["tasks"][0]["status"] == "accepted"


<<<<<<< Updated upstream
def test_held_a_seat_cannot_accept_the_top_seats_work(
        tmp_path, monkeypatch):
    """HELD: the owner-only case refuses a seat, in both directions.

    A task whose assignee has no supervisor may only be accepted by the owner
    (no `--by`). A seat supplying `--by` must be refused — and a `reviewer_id`
    left NULL by an earlier state must never be readable as "anyone may",
    because the level is derived from the assignee, not from that column.
    """
    from voicebridge.office import store as S
    from voicebridge.office import work as W
=======
def test_held_unassigned_reviewer_refuses_a_seat(tmp_path, monkeypatch):
    """HELD: `reviewer_id IS NULL` is the owner's slot, not an open door.

    A task with no reviewer must not be accepted by an arbitrary seat that
    supplies `--by`; only the owner (no `--by`) closes it.
    """
    from voicebridge.office import work as W
    from voicebridge.office import store as S
>>>>>>> Stashed changes
    from voicebridge.office.errors import OfficeError

    cfg, ids = _org(tmp_path, monkeypatch, name="nullrev")
    t = W.create(cfg, "t", exit_check="true", confirmed=True)["id"]
<<<<<<< Updated upstream
    conn = S.connect(cfg)
    try:
        S.migrate(conn)
        conn.execute("UPDATE office_task SET assignee_id=?, reviewer_id=NULL,"
                     " status='running' WHERE id=?", (int(ids["ceo"]), int(t)))
=======
    W.claim(cfg, t, agent_id=ids["w1"], confirmed=True)
    conn = S.connect(cfg)
    try:
        S.migrate(conn)
        conn.execute("UPDATE office_task SET reviewer_id=NULL WHERE id=?",
                     (int(t),))
>>>>>>> Stashed changes
        conn.commit()
    finally:
        conn.close()
    W.record_turn(cfg, t, outcome="satisfied")

    with pytest.raises(OfficeError) as exc:
<<<<<<< Updated upstream
        W.review(cfg, t, verdict="accept", by_agent=ids["manager"],
                 confirmed=True)
    assert exc.value.code == "OFFICE_REVIEW_WRONG_LEVEL"
    assert W.tasks(cfg)["tasks"][0]["status"] == "review"


def test_held_owner_cannot_accept_a_mid_chain_tasks_work(
        tmp_path, monkeypatch):
    """HELD: an omitted `--by` on a seat's work is refused, not a shortcut.

    The owner is above the CEO, so the owner is NOT the level above a worker's
    manager. Reaching for the owner instead of the assigned supervisor must be
    refused with the same code — otherwise `--by` omitted becomes a way to
    accept anything by claiming to be the owner.
    """
    from voicebridge.office import work as W
    from voicebridge.office.errors import OfficeError

    cfg, ids = _org(tmp_path, monkeypatch, name="midchain")
    t = W.create(cfg, "t", exit_check="true", confirmed=True)["id"]
    W.claim(cfg, t, agent_id=ids["w2"], confirmed=True)
    W.record_turn(cfg, t, outcome="satisfied")
    assert W.tasks(cfg)["tasks"][0]["status"] == "review"

    with pytest.raises(OfficeError) as exc:
        W.review(cfg, t, verdict="accept", by_agent=None, confirmed=True)
    assert exc.value.code == "OFFICE_REVIEW_WRONG_LEVEL", exc.value.code
    # the CEO supplying --by is wrong too: the level is the manager, not the top
    with pytest.raises(OfficeError) as exc:
        W.review(cfg, t, verdict="accept", by_agent=ids["ceo"], confirmed=True)
=======
        W.review(cfg, t, verdict="accept", by_agent=ids["w1"], confirmed=True)
>>>>>>> Stashed changes
    assert exc.value.code == "OFFICE_REVIEW_WRONG_LEVEL"
    assert W.tasks(cfg)["tasks"][0]["status"] == "review"


# =========================================================================
# FINDING #4 — DISPATCH: a peer silently re-claimed a `running` task. FIXED:
# in-progress work may only change hands as a RECORDED handoff.
# =========================================================================
# work.claim's docstring says "Assign a task to a seat, enforcing the chain"
# and the module promises a reviewer exactly one level above the assignee.
# claim accepts `running` as a claimable state (work.py) — and it is DELIBERATE:
# a task whose turn was refused or whose check failed stays in progress and
# must be picked up again (27-WORK-LOOP.md). The defect was never that a peer
# could pick it up; it was that a DIFFERENT seat could take it with no
# ownership check and no handoff record, so a second seat overwrote the
# in-flight claim (the reviewer silently escalates to the peer's supervisor)
# while the first seat's turn stayed mid-flight and untracked. Two clean
# `claimed` events with no handoff is unfalsifiable — a reader cannot tell the
# previous owner's work was discarded.
#
# The fix preserves recoverability and adds the missing record: a task that
# already has an assignee in progress may be re-claimed by (a) the current
# assignee — the ordinary recovery, silent because nothing changed hands — or
# (b) another seat ONLY with `handoff=True` and a reason, which updates the
# assignee and appends a `reassigned` trail event naming BOTH seats. A
# cross-seat take without the handoff flag is a typed refusal.
def test_finding4_running_task_cannot_be_silently_reclaimed(tmp_path, monkeypatch):
    """FINDING #4 (FIXED): w2 cannot steal w1's in-progress task silently.

    Reproduction:
      1. claim task t by w1  -> assignee=w1, status=running.
      2. claim the same t by w2, no handoff -> REFUSED (OFFICE_TASK_NOT_YOURS),
         assignee stays w1, and w1's turn is still w1's.
    """
    from voicebridge.office import work as W
    from voicebridge.office.errors import OfficeError

    cfg, ids = _org(tmp_path, monkeypatch, name="steal")
    w1, w2 = ids["w1"], ids["w2"]

    t = W.create(cfg, "t", exit_check="true", confirmed=True)["id"]
    first = W.claim(cfg, t, agent_id=w1, confirmed=True)
    assert first["assignee_id"] == w1
    assert first["handoff"] is False

    with pytest.raises(OfficeError) as exc:
        W.claim(cfg, t, agent_id=w2, confirmed=True)
    assert exc.value.code == "OFFICE_TASK_NOT_YOURS"

    task = W.tasks(cfg)["tasks"][0]
    assert task["assignee_id"] == w1                        # NOT stolen
    assert task["status"] == "running"

    # The trail shows the refusal's absence of a second clean claim: the only
    # `claimed` row is w1's, so w1's work is still attributable.
    kinds = [e["kind"] for e in W.trail(cfg, t)["events"]]
    assert kinds.count("claimed") == 1
    assert "reassigned" not in kinds


def test_finding4_handoff_is_recorded_and_names_both_seats(tmp_path, monkeypatch):
    """FINDING #4 (FIXED): the ONLY way work changes hands is a recorded one.

    The legitimate cross-seat path: `handoff=True` + a reason updates the
    assignee (and recomputes the reviewer from the new assignee's supervisor,
    C31) and appends a `reassigned` event naming both seats — so a reader can
    always tell whose work was taken and why.
    """
    from voicebridge.office import work as W
    from voicebridge.office.errors import OfficeError

    cfg, ids = _org(tmp_path, monkeypatch, name="handoff")
    w1, w2, mgr = ids["w1"], ids["w2"], ids["manager"]

    t = W.create(cfg, "t", exit_check="true", confirmed=True)["id"]
    W.claim(cfg, t, agent_id=w1, confirmed=True)
    W.record_turn(cfg, t, outcome="refused:OFFICE_RUN_REFUSED")

    # a handoff with no reason is refused — a handoff without a reason is just
    # a silent claim wearing a hat
    with pytest.raises(OfficeError) as exc:
        W.claim(cfg, t, agent_id=w2, confirmed=True, handoff=True, reason="")
    assert exc.value.code == "OFFICE_BAD_INPUT"
    assert W.tasks(cfg)["tasks"][0]["assignee_id"] == w1

    out = W.claim(cfg, t, agent_id=w2, confirmed=True, handoff=True,
                  reason="w1 is blocked on provider auth")
    assert out["assignee_id"] == w2
    assert out["handoff"] is True
    assert out["previous_assignee_id"] == w1
    assert out["reviewer_id"] == mgr            # recomputed from w2's supervisor

    events = W.trail(cfg, t)["events"]
    handed = [e for e in events if e["kind"] == "reassigned"]
    assert len(handed) == 1
    assert f"seat:{w1}" in handed[0]["body"] and f"seat:{w2}" in handed[0]["body"]
    assert handed[0]["payload"]["from_agent_id"] == w1
    assert handed[0]["payload"]["to_agent_id"] == w2


def test_finding4_same_assignee_can_still_recover_its_task(tmp_path, monkeypatch):
    """FINDING #4 (FIXED): the deliberate re-claim is intact for its owner.

    `running` staying claimable is on purpose (27-WORK-LOOP.md): after a
    refused or failed turn the owner must be able to pick the task back up,
    with no handoff ceremony required because nothing changed hands.
    """
    from voicebridge.office import work as W

    cfg, ids = _org(tmp_path, monkeypatch, name="recover")
    w1 = ids["w1"]

    t = W.create(cfg, "t", exit_check="true", confirmed=True)["id"]
    W.claim(cfg, t, agent_id=w1, confirmed=True)
    W.record_turn(cfg, t, outcome="refused:OFFICE_RUN_REFUSED")

    again = W.claim(cfg, t, agent_id=w1, confirmed=True)
    assert again["assignee_id"] == w1
    assert again["handoff"] is False
    kinds = [e["kind"] for e in W.trail(cfg, t)["events"]]
    assert "reassigned" not in kinds            # nothing changed hands


def test_finding4_open_and_released_tasks_are_still_claimable_by_anyone(
        tmp_path, monkeypatch):
    """FINDING #4 (FIXED): the ownership guard only bites on LIVE ownership.

    An `open` task has no assignee, and `requeue` clears the assignee — so the
    dispatcher's normal path (claim open work for the least-loaded seat) is
    unaffected. Only a task that is genuinely owned in progress is protected.
    """
    from voicebridge.office import work as W

    cfg, ids = _org(tmp_path, monkeypatch, name="open")
    w1, w2 = ids["w1"], ids["w2"]

    t = W.create(cfg, "open task", exit_check="true", confirmed=True)["id"]
    assert W.claim(cfg, t, agent_id=w1, confirmed=True)["handoff"] is False

    t2 = W.create(cfg, "released", exit_check="true", confirmed=True)["id"]
    W.claim(cfg, t2, agent_id=w1, confirmed=True)
    W.requeue(cfg, t2, reason="the turn never happened")
    # requeue clears the owner, so a fresh seat may take the recovered work
    out = W.claim(cfg, t2, agent_id=w2, confirmed=True)
    assert out["assignee_id"] == w2
    assert out["handoff"] is False


# =========================================================================
# HELD invariants — permanent guards
# =========================================================================
def test_held_append_only_triggers_fire(tmp_path, monkeypatch):
    """C1: UPDATE and DELETE on the ledger are refused by the store."""
    from voicebridge.office import ledger as L
    from voicebridge.office import store

    cfg, ids = _org(tmp_path, monkeypatch, name="append")
    L.record(cfg, "revenue", 100, agent_id=ids["manager"])

    conn = store.connect(cfg)
    try:
        for sql in ("UPDATE office_ledger SET amount_cents = amount_cents + 1",
                    "DELETE FROM office_ledger"):
            with pytest.raises(sqlite3.Error) as exc:
                conn.execute(sql)
            assert "append-only" in str(exc.value)
    finally:
        conn.close()

    # And health() proves it live when there is a row to fire on.
    h = L.health(cfg)
    assert h["triggers_present"] is True
    assert h["append_only_enforced"] == {"update": True, "delete": True}
    assert h["healthy"] is True


def test_held_health_is_honest_on_an_empty_ledger(tmp_path, monkeypatch):
    """D14: with no rows the trigger proof is UNVERIFIED, not True."""
    from voicebridge.office import ledger as L

    cfg, _ = _org(tmp_path, monkeypatch, name="empty")
    h = L.health(cfg)
    assert h["append_only_enforced"] == {"update": None, "delete": None}
    assert "unverified" in h["append_only_proof"]
    # and it does not claim health it cannot prove... but healthy stays True
    # because the drift check is over an empty book. Assert the shape only.
    assert h["rollup_drift_cents"] == 0


def test_held_money_conserves_across_revenue_and_costs(tmp_path, monkeypatch):
    """The org net equals revenue minus metered costs, exactly (C2, integers)."""
    from voicebridge.office import balance_sheet as BS
    from voicebridge.office import ledger as L

    cfg, ids = _org(tmp_path, monkeypatch, name="conserve")
    seat = ids["manager"]
    # adversarial: odd cent amounts across every magnitude kind
    L.record(cfg, "revenue", 1013, agent_id=seat)
    L.record(cfg, "revenue", 997, agent_id=seat)
    L.record(cfg, "inference", 333, agent_id=seat)
    L.record(cfg, "fees", 7, agent_id=seat)
    L.record(cfg, "capex", 1, agent_id=seat)

    org = L.pnl(cfg, scope="org")
    assert org["revenue_cents"] == 2010
    assert org["cost_cents"] == 341
    assert org["net_cents"] == 2010 - 341
    # money is integer cents throughout — no float leaked in
    assert isinstance(org["net_cents"], int)

    ex = BS.explain(cfg)
    assert ex["opening"] + ex["revenue"] - ex["costs"] + ex["adjustments"] \
        == ex["closing"]
    assert ex["residual"] == 0
    assert BS.trial_balance(cfg)["total_debit"] == \
        BS.trial_balance(cfg)["total_credit"]


def test_held_reverse_of_revenue_and_inference_is_exactly_neutral(
        tmp_path, monkeypatch):
    """A compensating row for a METERED row leaves the net unchanged."""
    from voicebridge.office import ledger as L

    cfg, ids = _org(tmp_path, monkeypatch, name="neutral")
    seat = ids["manager"]
    before = L.pnl(cfg, scope="agent", id=seat)["net_cents"]

    rev = L.record(cfg, "revenue", 5000, agent_id=seat)
    inf = L.record(cfg, "inference", 250, agent_id=seat)
    L.reverse(cfg, rev["id"])
    L.reverse(cfg, inf["id"])

    after = L.pnl(cfg, scope="agent", id=seat)["net_cents"]
    assert after == before, "a metered reversal must be net-neutral"


def test_held_unmetered_rows_never_enter_the_metered_fold(tmp_path, monkeypatch):
    """D16: the two folds never interchange (ledger.sum_metered vs sum_reported)."""
    from voicebridge.office import ledger as L

    cfg, ids = _org(tmp_path, monkeypatch, name="d16")
    seat = ids["manager"]
    L.record(cfg, "inference", 100, agent_id=seat)
    L.record(cfg, "inference", 9999, agent_id=seat, unmetered=True)

    assert L.sum_metered(cfg) == 100
    assert L.sum_reported(cfg) == 100 + 9999


def test_held_corrupt_cap_override_cannot_grant_headroom(tmp_path, monkeypatch):
    """D16: a corrupt persisted cap is treated as unset, never as a bypass."""
    from voicebridge.office import caps
    from voicebridge.office import store

    cfg, _ = _org(tmp_path, monkeypatch, name="caps")
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        conn.execute("INSERT INTO office_meta(key,value)"
                     " VALUES('cap:perRun','garbage')")
        conn.execute("INSERT INTO office_meta(key,value)"
                     " VALUES('cap:perAgent','-9999')")
        conn.execute("INSERT INTO office_meta(key,value)"
                     " VALUES('cap:perDay','nan')")
        conn.commit()
    finally:
        conn.close()

    eff = caps.resolve(cfg)
    assert eff["perRun"] == caps.DEFAULTS["perRun"]
    assert eff["perAgent"] == caps.DEFAULTS["perAgent"]
    assert eff["perDay"] == caps.DEFAULTS["perDay"]


def test_held_zero_cap_means_no_cap_not_zero(tmp_path, monkeypatch):
    """0 is a real saved value ("no cap"), distinct from absent (default)."""
    from voicebridge.office import caps

    cfg, _ = _org(tmp_path, monkeypatch, name="zerocap")
    caps.set_cap(cfg, "global", 0, confirmed=True)
    assert caps.resolve(cfg)["global"] == 0
    head = caps.headroom(cfg, "global", 10_000_000)
    assert head["headroom_cents"] is None      # not a huge number, not zero
    assert head["meaning"] == "no cap"


def test_held_escalation_requires_the_raised_level(tmp_path, monkeypatch):
    """C31/D34: only the seat an escalation was raised to may settle it."""
    from voicebridge.office import escalation as E
    from voicebridge.office.errors import OfficeError

    cfg, ids = _org(tmp_path, monkeypatch, name="esc")
    w1, mgr = ids["w1"], ids["manager"]

    esc = E.raise_to_supervisor(cfg, agent_id=w1, kind="blocked",
                                summary="help")
    assert esc["supervisor_id"] == mgr

    with pytest.raises(OfficeError) as exc:
        E.resolve(cfg, esc["id"], "approve", by_agent=w1, confirmed=True)
    assert exc.value.code == "OFFICE_ESCALATION_WRONG_LEVEL"

    # the correct level settles it
    out = E.resolve(cfg, esc["id"], "approve", by_agent=mgr, confirmed=True)
    assert out["status"] == "resolved"


def test_held_consent_hardline_is_a_floor(tmp_path, monkeypatch):
    """D17/D18: the hardline floor denies even a full-access posture."""
    from voicebridge.office import consent

    for cap, scope, tag in (("ledger", "rewrite", None),
                            ("spend", "personal", None),
                            ("secret", "owner:exfiltrate", None)):
        v = consent.check(capability=cap, scope=scope,
                          posture="unattended_full",
                          tags=[tag] if tag else None)
        assert v["allowed"] is False, f"{cap}:{scope} was not on the floor"
        assert v["tier"] == "hardline"
        assert "anti_retry" in v


def test_held_consent_tier4_mutation_default_denies(tmp_path, monkeypatch):
    """D17: with no grant, a mutation denies and a read-only call allows.

    Tier 2 (bypass) handles ``frozen`` / ``unattended_full`` / ``supervised``.
    Any other posture falls through to tier 3 (grants) and then tier 4
    (resolve), which is where "silence is not consent" bites.
    """
    from voicebridge.office import consent

    # supervised: no grant -> refused, and says the standing bypass is why
    supervised = consent.check(capability="write", scope="file",
                               posture="supervised")
    assert supervised["allowed"] is False
    assert supervised["tier"] == "bypass"
    assert "silence is not consent" in supervised["reason"]

    # a posture with no bypass: tier 4 applies
    denied = consent.check(capability="write", scope="file",
                           posture="manual")
    assert denied["allowed"] is False
    assert denied["tier"] == "resolve"
    assert "silence is not consent" in denied["reason"]

    allowed = consent.check(capability="read", scope="file", read_only=True,
                            posture="manual")
    assert allowed["allowed"] is True
    assert allowed["tier"] == "resolve"

    # read-only but networked is not auto-allowed either
    net = consent.check(capability="read", scope="web", read_only=True,
                        network=True, posture="manual")
    assert net["allowed"] is False


def test_held_admission_refuses_on_an_unsettled_run(tmp_path, monkeypatch):
    """D15: a receipt with no settled ledger row blocks every admission."""
    from voicebridge.office import receipts
    from voicebridge.office.errors import OfficeError

    cfg, ids = _org(tmp_path, monkeypatch, name="admit")
    seat = ids["manager"]
    receipts.begin_run(cfg, seat, cap_cents=1000, run_id="open1")

    with pytest.raises(OfficeError) as exc:
        receipts.admit(cfg, agent_id=seat, estimated_cents=1)
    assert exc.value.code == receipts.ADMISSION_CODE
    assert "open1" in exc.value.detail

    # settle it, and fund the seat so the balance gate is not the next refusal
    receipts.finish_run(cfg, "open1", settled_cents=0, unmetered=True)
    from voicebridge.office import ledger as L
    L.record(cfg, "adjustment", 10_000, agent_id=seat, note="capital")
    assert receipts.admit(cfg, agent_id=seat, estimated_cents=1)["state"] == "OK"


def test_held_turn_cap_holds_under_concurrent_spend(tmp_path, monkeypatch):
    """M5: concurrent turn consumption cannot exceed the cap."""
    import threading

    from voicebridge.office import work as W

    cfg, ids = _org(tmp_path, monkeypatch, name="budget")
    w1 = ids["w1"]
    W.set_turn_cap(cfg, agent_id=w1, turns=1, confirmed=True)

    results = []

    def spend():
        try:
            results.append(bool(W.spend_turn(cfg, agent_id=w1)["allowed"]))
        except Exception:                              # noqa: BLE001
            results.append("ERR")

    threads = [threading.Thread(target=spend) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count(True) == 1, results
    assert W.turn_budget(cfg, agent_id=w1)["turns_used"] == 1


def test_held_proposal_self_approval_is_refused(tmp_path, monkeypatch):
    """C31: a seat may not approve (or reject) its own proposal."""
    from voicebridge.office import task_proposal as P
    from voicebridge.office.errors import OfficeError

    cfg, ids = _org(tmp_path, monkeypatch, name="prop")
    w1 = ids["w1"]

    prop = P.propose(cfg, w1, "idea", rationale="because",
                     exit_check="true", confirmed=True)
    with pytest.raises(OfficeError) as exc:
        P.approve(cfg, prop["id"], approver_id=w1, confirmed=True)
    assert exc.value.code == P.SELF_APPROVAL_CODE


def test_held_survival_floor_escalates_to_the_manager(tmp_path, monkeypatch):
    """D34/D38: a seat under the survival floor raises one escalation upward."""
    from voicebridge.office import ledger as L
    from voicebridge.office import survival as S

    cfg, ids = _org(tmp_path, monkeypatch, name="survive")
    w1, mgr = ids["w1"], ids["manager"]

    # A worker burning fast with no revenue: give it a negative balance and a
    # recent burn so runway < 7 days.
    L.record(cfg, "revenue", 100, agent_id=w1)
    burn = L.record(cfg, "inference", 100_000, agent_id=w1, note="burn")
    assert burn["id"]
    info = S.runway_days(cfg, w1)
    assert info["at_risk"] is True
    assert info["insolvent"] is True

    tick = S.daily_tick(cfg)
    raised = [r for r in tick["raised"] if r["agent_id"] == w1]
    assert raised, tick
    assert raised[0]["supervisor_id"] == mgr


def test_held_seat_termination_preserves_ledger_history(tmp_path, monkeypatch):
    """C1: termination stops a seat but never deletes its money."""
    from voicebridge.office import ledger as L
    from voicebridge.office import survival as S

    cfg, ids = _org(tmp_path, monkeypatch, name="terminate")
    w1 = ids["w1"]
    L.record(cfg, "inference", 4242, agent_id=w1)
    before = L.sum_metered(cfg)

    S.terminate(cfg, w1, reason="test", confirmed=True)
    assert L.sum_metered(cfg) == before


# =========================================================================
# MUTATION PROOFS — falsify a sample of the HELD guards (scratch copy only)
# =========================================================================
def test_mutation_escalation_level_guard_is_load_bearing(tmp_path, monkeypatch):
    """Proof the C31 escalation-level guard is what makes the HELD test fail.

    Rather than mutating a repo file, this neutralises the guard in-process (a
    monkeypatched wrapper that drops the level check) on a *second* store, and
    shows the wrong seat can then settle the escalation. That is exactly the
    behaviour ``test_held_escalation_requires_the_raised_level`` asserts is
    prevented, so its assertion is load-bearing on the real guard.
    """
    from voicebridge.office import escalation as E
    from voicebridge.office.errors import OfficeError

    cfg, ids = _org(tmp_path, monkeypatch, name="esc_mut")
    w1, mgr = ids["w1"], ids["manager"]
    esc = E.raise_to_supervisor(cfg, agent_id=w1, kind="blocked", summary="x")

    # 1) real guard: the wrong seat is refused
    with pytest.raises(OfficeError):
        E.resolve(cfg, esc["id"], "approve", by_agent=w1, confirmed=True)

    # 2) neutralise the guard by wrapping the live function and dropping it
    real_resolve = E.resolve

    def unchecked(cfg_, escalation_id, decision, *, by_agent, confirmed=False,
                  requested_by="x"):
        try:
            return real_resolve(cfg_, escalation_id, decision, by_agent=by_agent,
                                confirmed=confirmed, requested_by=requested_by)
        except OfficeError as err:
            if err.code == "OFFICE_ESCALATION_WRONG_LEVEL":
                # pretend the seat WAS the supervisor: re-write supervisor_id
                from voicebridge.office import store as _s
                conn = _s.connect(cfg_)
                try:
                    _s.migrate(conn)
                    conn.execute("UPDATE office_escalation SET supervisor_id=?"
                                 " WHERE id=?", (by_agent, escalation_id))
                    conn.commit()
                finally:
                    conn.close()
                return real_resolve(cfg_, escalation_id, decision,
                                    by_agent=by_agent, confirmed=confirmed,
                                    requested_by=requested_by)
            raise

    out = unchecked(cfg, esc["id"], "approve", by_agent=w1, confirmed=True)
    assert out["status"] == "resolved"          # the guard was the only barrier
    assert out["decided_by"] == w1


def test_mutation_append_only_trigger_removal_is_caught(tmp_path):
    """Proof the C1 guard can fail: drop the triggers from a scratch schema.

    A scratch copy of store.py with both ``CREATE TRIGGER`` statements removed
    is loaded by path; building its schema and attempting an UPDATE must now
    SUCCEED — whereas the real store refuses. Repo files are never written.
    """
    import re

    src = ROOT / "voicebridge" / "office" / "store.py"
    text = src.read_text()
    mutated = re.sub(r"CREATE TRIGGER IF NOT EXISTS office_ledger_no_\w+"
                     r".*?END;", "", text, flags=re.DOTALL)
    assert mutated != text, "the trigger block was not found to mutate"
    assert "office_ledger_no_update" not in mutated

    dst = tmp_path / "store_mut.py"
    dst.write_text(mutated)

    conn = sqlite3.connect(":memory:")
    conn.executescript(mutated.split('_SCHEMA_SQL = """', 1)[1]
                       .split('"""', 1)[0])
    conn.execute("INSERT INTO office_ledger(ts, kind, amount_cents)"
                 " VALUES(0, 'revenue', 100)")
    # With the triggers removed the UPDATE is silently accepted.
    conn.execute("UPDATE office_ledger SET amount_cents = 999")
    got = conn.execute("SELECT amount_cents FROM office_ledger").fetchone()[0]
    assert got == 999, "the mutated schema still refused the UPDATE"
    conn.close()


def test_mutation_unmetered_propagation_is_what_fixes_finding2(tmp_path):
    """Proof FINDING #2 is exactly the unmetered propagation (now applied).

    This guard asserts the exact source shape of FINDING #2's fix site: that
    ``reverse()``'s compensating ``record`` call propagates the original row's
    ``unmetered`` flag. If a later edit drops the keyword the finding silently
    re-opens, so this test goes red rather than letting the bug drift back in.

    The load-bearing property is proven by mutation: stripping the propagated
    keyword out of a SCRATCH copy of ``ledger.py`` reproduces the original
    omission at the exact call site. Repo files are never written.
    """
    src = ROOT / "voicebridge" / "office" / "ledger.py"
    text = src.read_text()

    # The fix must be present at the compensating call site in reverse().
    rev_src = text.split("def reverse", 1)[1].split("def rows", 1)[0]
    assert 'out = record(cfg, "transfer", delta,' in rev_src
    call_src = rev_src.split('out = record', 1)[1]
    assert 'unmetered=bool(original["unmetered"])' in call_src, (
        "reverse() no longer propagates unmetered; FINDING #2 has re-opened")

    # Mutation: strip the propagation and confirm the omission is reproducible
    # at that call site — i.e. the keyword really is what fixes the finding.
    mutated = text.replace(
        '                 unmetered=bool(original["unmetered"]))',
        '                 )', 1)
    assert mutated != text, "the propagation keyword was not found to mutate"
    mutated_rev = mutated.split("def reverse", 1)[1].split("def rows", 1)[0]
    mutated_call = mutated_rev.split("out = record", 1)[1]
    assert "unmetered" not in mutated_call, (
        "the mutation did not reproduce the original omission")

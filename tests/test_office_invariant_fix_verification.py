"""INDEPENDENT verification of the four office invariant fixes.

This file is the evidence for ``docs/OFFICE-INVARIANT-FIX-VERIFICATION.md``.
It does NOT reuse the fixing agents' tests: every assertion here was written
from the *invariant*, not from their diff, and each fix is attacked with
adjacent inputs, boundary values, sibling call paths, ordering, and a
compensated/reversed row.

Four invariants (audit ``docs/OFFICE-INVARIANT-AUDIT.md``):

* #1 budget   — ``envelope.py``: above-envelope spend is refused unless a raise
                that *covers the overspend for this period* was approved.
* #2 money    — ``ledger.py``: reversing an ``unmetered`` row must stay
                unmetered (no metered money is minted or destroyed).
* #3 consent  — ``work.py``: ``review()`` level-checks the approver, including
                when no ``--by`` is given (None == the owner, not "anyone").
* #4 dispatch — ``work.py``: ``claim()`` will not let a seat steal another
                seat's ``running`` task without an explicit, recorded requeue.

Hermetic: tmp XDG state, real SQLite, no network, no provider call.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from voicebridge.office.errors import OfficeError


# --------------------------------------------------------------------- fixtures
def _cfg(tmp_path, monkeypatch, name="vifv"):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s" / name))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / f"voice-{name}.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _org(tmp_path, monkeypatch, name="vifv", extra_workers=0):
    """ceo -> mgr -> {w1, w2[,...]}. The smallest chain with a second worker."""
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
    ids = {"ceo": ceo, "mgr": mgr, "w1": w1, "w2": w2}
    for i in range(extra_workers):
        ids[f"w{i + 3}"] = O.hire(cfg, f"w{i + 3}", seat_type="worker",
                                  supervisor_id=mgr, confirmed=True)["id"]
    return cfg, ids


def _refused(call):
    """Run `call`, return (code, detail) if it raised OfficeError, else None."""
    try:
        call()
    except OfficeError as exc:
        return exc.code, exc.detail
    return None


# =========================================================================
# #2 — LEDGER: reversing an unmetered row must not manufacture metered money
# =========================================================================
def test_v2_unmetered_reversal_stays_unmetered(tmp_path, monkeypatch):
    """Pre-fix: reversing an unmetered inference minted +777 metered balance.

    Independent of the auditors' test: I assert the *reversal row itself* is
    unmetered AND the derived balance is unchanged, in both directions.
    """
    from voicebridge.office import ledger as L

    cfg, ids = _org(tmp_path, monkeypatch, name="v2")
    seat = ids["mgr"]
    assert L.balance(cfg, seat)["balance_cents"] == 0

    phantom = L.record(cfg, "inference", 777, agent_id=seat, unmetered=True)
    assert L.balance(cfg, seat)["balance_cents"] == 0, "D16 sanity"

    rv = L.reverse(cfg, phantom["id"])
    assert rv["unmetered"] is True, "reversal row must inherit unmetered"
    assert L.balance(cfg, seat)["balance_cents"] == 0, \
        "reversing a phantom must not mint metered balance"

    # The reversal must also be invisible to the *cost* fold (sum_metered).
    assert L.sum_metered(cfg, scope="agent", id=seat) == 0
    # ...but still recorded (D16: run counts include it).
    assert L.balance(cfg, seat)["runs_recorded"] >= 2


def test_v2_unmetered_revenue_reversal_does_not_destroy(tmp_path, monkeypatch):
    """The destruction direction: reversing unmetered revenue must not go
    negative."""
    from voicebridge.office import ledger as L

    cfg, ids = _org(tmp_path, monkeypatch, name="v2r")
    seat = ids["mgr"]
    row = L.record(cfg, "revenue", 5000, agent_id=seat, unmetered=True)
    assert L.balance(cfg, seat)["balance_cents"] == 0
    L.reverse(cfg, row["id"])
    assert L.balance(cfg, seat)["balance_cents"] == 0, \
        "reversing phantom revenue must not destroy balance"


def test_v2_reversal_of_metered_row_still_metered(tmp_path, monkeypatch):
    """ADVERSARIAL: the fix must not blanket-mark every reversal unmetered.

    If the fix hard-coded ``unmetered=True`` on every reversal, a metered cost
    could no longer be corrected. A metered row's reversal must stay metered.
    """
    from voicebridge.office import ledger as L

    cfg, ids = _org(tmp_path, monkeypatch, name="v2m")
    seat = ids["mgr"]
    row = L.record(cfg, "inference", 100, agent_id=seat)  # metered
    assert L.balance(cfg, seat)["balance_cents"] == -100
    rv = L.reverse(cfg, row["id"])
    assert rv["unmetered"] is False, \
        "a metered row's reversal must remain metered"
    assert L.balance(cfg, seat)["balance_cents"] == 0


def test_v2_reversal_flag_matches_original_for_every_kind(tmp_path, monkeypatch):
    """ADVERSARIAL: sweep every kind x {metered, unmetered}.

    The fix must copy the original's flag, not key off the kind (a `transfer`
    is metered "by definition", so keying off the kind would be wrong).
    """
    from voicebridge.office import ledger as L

    cfg, ids = _org(tmp_path, monkeypatch, name="v2k")
    seat = ids["mgr"]
    for kind, amt in (("revenue", 300), ("inference", 40),
                      ("fees", 25), ("capex", 900)):
        for unm in (False, True):
            row = L.record(cfg, kind, amt, agent_id=seat, unmetered=unm)
            rv = L.reverse(cfg, row["id"])
            assert rv["unmetered"] is unm, \
                f"kind={kind} unmetered={unm}: reversal flag drifted"


def test_v2_reversal_of_a_reversal_preserves_flag(tmp_path, monkeypatch):
    """ADVERSARIAL: reverse(reverse(x)). Chain the compensation and confirm the
    flag survives the hop and the balance returns where it started."""
    from voicebridge.office import ledger as L

    cfg, ids = _org(tmp_path, monkeypatch, name="v2c")
    seat = ids["mgr"]
    row = L.record(cfg, "inference", 555, agent_id=seat, unmetered=True)
    r1 = L.reverse(cfg, row["id"])
    r2 = L.reverse(cfg, r1["id"])
    assert r1["unmetered"] is True and r2["unmetered"] is True
    assert L.balance(cfg, seat)["balance_cents"] == 0


# =========================================================================
# #3 — CONSENT: review() level-checks the approver, including by_agent=None
# =========================================================================
def _to_review(cfg, ids):
    """Drive a worker task to the `review` state. Returns task id."""
    from voicebridge.office import work as W

    t = W.create(cfg, "t", exit_check="true", confirmed=True)["id"]
    W.claim(cfg, t, agent_id=ids["w1"], confirmed=True)
    W.record_turn(cfg, t, outcome="satisfied")
    return t


def test_v3_none_is_owner_and_is_level_checked(tmp_path, monkeypatch):
    """Pre-fix: review(by_agent=None) accepted any task, including a worker's
    own task whose reviewer is the manager.

    Post-fix: None == the owner (above the CEO), so reviewing a task whose
    reviewer is the *manager* must be refused.
    """
    from voicebridge.office import work as W

    cfg, ids = _org(tmp_path, monkeypatch, name="v3")
    t = _to_review(cfg, ids)              # reviewer is the manager
    bad = _refused(lambda: W.review(cfg, t, verdict="accept", by_agent=None,
                                    confirmed=True))
    assert bad is not None and bad[0] == "OFFICE_REVIEW_WRONG_LEVEL", bad
    # The task must NOT have been accepted by the refused attempt.
    assert W.tasks(cfg)["tasks"][0]["status"] == "review"
    # The legit reviewer (the manager) still works.
    W.review(cfg, t, verdict="accept", by_agent=ids["mgr"], confirmed=True)
    assert W.tasks(cfg)["tasks"][0]["status"] == "accepted"


def test_v3_wrong_level_seat_still_refused(tmp_path, monkeypatch):
    """The original guard (a real seat that is not the reviewer) still holds."""
    from voicebridge.office import work as W

    cfg, ids = _org(tmp_path, monkeypatch, name="v3w")
    t = _to_review(cfg, ids)
    for who in (ids["w1"], ids["w2"], ids["ceo"]):   # self, peer, skip-level
        bad = _refused(lambda who=who: W.review(cfg, t, verdict="accept",
                                                by_agent=who, confirmed=True))
        assert bad is not None and bad[0] == "OFFICE_REVIEW_WRONG_LEVEL", \
            f"seat {who} must not accept a task whose reviewer is the manager"


def test_v3_owner_may_judge_a_top_seat_task(tmp_path, monkeypatch):
    """ADVERSARIAL / completeness: the owner fallback must still be USABLE.

    The revised fix derives the required approver from the ASSIGNEE's
    supervisor: ``by_agent=None`` (the owner) approves exactly work whose
    assignee has NO supervisor — the top seat. The fix must not be a blanket
    'None always refused', which would remove the owner's only approval path.

    The top seat cannot be *assigned* through ``claim()`` (C30: it has no
    supervisor to review it), so the case is staged directly — the same shape
    the owner would face on a CEO-authored task.
    """
    from voicebridge.office import work as W
    from voicebridge.office import store

    cfg, ids = _org(tmp_path, monkeypatch, name="v3o")
    t = W.create(cfg, "top-seat work", exit_check="true", confirmed=True)["id"]
    conn = store.connect(cfg)
    try:
        conn.execute("UPDATE office_task SET status='review', assignee_id=?,"
                     " reviewer_id=NULL WHERE id=?", (ids["ceo"], t))
        conn.commit()
    finally:
        conn.close()
    # The owner (by_agent=None) is above the top seat and IS the approver.
    out = W.review(cfg, t, verdict="accept", by_agent=None, confirmed=True)
    assert out["status"] == "accepted", "the owner must be able to judge"
    assert W.tasks(cfg)["tasks"][0]["status"] == "accepted"
    # A *seat* must NOT be able to accept the top seat's work.
    t2 = W.create(cfg, "top-seat work 2", exit_check="true", confirmed=True)["id"]
    conn = store.connect(cfg)
    try:
        conn.execute("UPDATE office_task SET status='review', assignee_id=?,"
                     " reviewer_id=NULL WHERE id=?", (ids["ceo"], t2))
        conn.commit()
    finally:
        conn.close()
    bad = _refused(lambda: W.review(cfg, t2, verdict="accept",
                                    by_agent=ids["mgr"], confirmed=True))
    assert bad is not None and bad[0] == "OFFICE_REVIEW_WRONG_LEVEL", bad


def test_v3_owner_cannot_reach_over_the_level_above(tmp_path, monkeypatch):
    """ADVERSARIAL: the owner must NOT approve a seat's work directly — that is
    the manager's call (C31). by_agent=None on a manager task must be refused."""
    from voicebridge.office import work as W

    cfg, ids = _org(tmp_path, monkeypatch, name="v3oo")
    t = _to_review(cfg, ids)              # assignee w1, supervisor = manager
    bad = _refused(lambda: W.review(cfg, t, verdict="accept", by_agent=None,
                                    confirmed=True))
    assert bad is not None and bad[0] == "OFFICE_REVIEW_WRONG_LEVEL", bad


def test_v3_unknown_agent_id_is_refused_not_skipped(tmp_path, monkeypatch):
    """ADVERSARIAL: a non-existent (but non-None) by_agent is simply not the
    reviewer, so it must be refused — the fix must not treat an unknown int as
    a wildcard."""
    from voicebridge.office import work as W

    cfg, ids = _org(tmp_path, monkeypatch, name="v3u")
    t = _to_review(cfg, ids)
    bad = _refused(lambda: W.review(cfg, t, verdict="accept", by_agent=99999,
                                    confirmed=True))
    assert bad is not None and bad[0] == "OFFICE_REVIEW_WRONG_LEVEL", bad


# =========================================================================
# #4 — DISPATCH: a running task is not silently re-claimed by a peer
# =========================================================================
def test_v4_peer_cannot_reclaim_running_task(tmp_path, monkeypatch):
    """Pre-fix: w2 could claim a task w1 was running, with no handoff row."""
    from voicebridge.office import work as W

    cfg, ids = _org(tmp_path, monkeypatch, name="v4")
    t = W.create(cfg, "t", exit_check="true", confirmed=True)["id"]
    W.claim(cfg, t, agent_id=ids["w1"], confirmed=True)
    bad = _refused(lambda: W.claim(cfg, t, agent_id=ids["w2"], confirmed=True))
    assert bad is not None, "a peer must not steal a running task"
    task = W.tasks(cfg)["tasks"][0]
    assert task["assignee_id"] == ids["w1"], "assignee must be untouched"
    assert task["reviewer_id"] == ids["mgr"]
    assert task["status"] == "running"


def test_v4_self_reclaim_still_allowed(tmp_path, monkeypatch):
    """ADVERSARIAL / completeness: the reassign-own-task case must still work
    (idempotent re-claim by the current assignee), else the fix over-blocks."""
    from voicebridge.office import work as W

    cfg, ids = _org(tmp_path, monkeypatch, name="v4s")
    t = W.create(cfg, "t", exit_check="true", confirmed=True)["id"]
    W.claim(cfg, t, agent_id=ids["w1"], confirmed=True)
    out = W.claim(cfg, t, agent_id=ids["w1"], confirmed=True)
    assert out["assignee_id"] == ids["w1"] and out["status"] == "running"


def test_v4_requeue_then_claim_by_peer_is_the_honest_path(tmp_path, monkeypatch):
    """ADVERSARIAL: the documented handoff (requeue -> claim) must work, so the
    fix routes theft through the honest path rather than banning transfer."""
    from voicebridge.office import work as W

    cfg, ids = _org(tmp_path, monkeypatch, name="v4q")
    t = W.create(cfg, "t", exit_check="true", confirmed=True)["id"]
    W.claim(cfg, t, agent_id=ids["w1"], confirmed=True)
    rq = W.requeue(cfg, t, reason="w1 turn refused")
    assert rq["requeued"] is True and rq["status"] == "open"
    out = W.claim(cfg, t, agent_id=ids["w2"], confirmed=True)
    assert out["assignee_id"] == ids["w2"]
    kinds = [e["kind"] for e in W.trail(cfg, t)["events"]]
    assert "requeued" in kinds, "the handoff must be recorded"


def test_v4_fixing_state_still_transferable(tmp_path, monkeypatch):
    """ADVERSARIAL: a task in `fixing` is not in-flight by another seat (the
    reject path clears no assignee by design) — confirm the guard's scope is
    `running` and does not accidentally freeze `fixing`/`open`."""
    from voicebridge.office import work as W
    from voicebridge.office import store

    cfg, ids = _org(tmp_path, monkeypatch, name="v4f")
    t = W.create(cfg, "t", exit_check="true", confirmed=True)["id"]
    # Force an unassigned `fixing` task (as reject -> fix would leave it owned,
    # but a requeue mid-fix leaves it open; here we test the raw claim state).
    conn = store.connect(cfg)
    try:
        conn.execute("UPDATE office_task SET status='fixing', assignee_id=NULL,"
                     " reviewer_id=NULL WHERE id=?", (t,))
        conn.commit()
    finally:
        conn.close()
    out = W.claim(cfg, t, agent_id=ids["w2"], confirmed=True)
    assert out["assignee_id"] == ids["w2"]


def test_v4_running_task_with_no_assignee_is_claimable(tmp_path, monkeypatch):
    """ADVERSARIAL boundary: a `running` task whose assignee is NULL (a torn
    state) must not be blocked by a NULL-comparison bug in the guard."""
    from voicebridge.office import work as W
    from voicebridge.office import store

    cfg, ids = _org(tmp_path, monkeypatch, name="v4n")
    t = W.create(cfg, "t", exit_check="true", confirmed=True)["id"]
    conn = store.connect(cfg)
    try:
        conn.execute("UPDATE office_task SET status='running', assignee_id=NULL"
                     " WHERE id=?", (t,))
        conn.commit()
    finally:
        conn.close()
    out = W.claim(cfg, t, agent_id=ids["w2"], confirmed=True)
    assert out["assignee_id"] == ids["w2"]


# =========================================================================
# #1 — BUDGET: above-envelope spend needs a raise that covers THIS period
# =========================================================================
def _fund_and_set_envelope(cfg, mgr, ceo, env_cents, funding_cents):
    from voicebridge.office import envelope as E
    from voicebridge.office import ledger as L

    E.allocate(cfg, scope="org", scope_id=None, cents=env_cents, by_agent=None,
               confirmed=True)
    L.record(cfg, "adjustment", funding_cents, agent_id=mgr, note="capital")


def test_v1_over_envelope_without_raise_is_refused(tmp_path, monkeypatch):
    """The baseline: above the envelope with no approved raise -> refused."""
    from voicebridge.office import envelope as E

    cfg, ids = _org(tmp_path, monkeypatch, name="v1a")
    _fund_and_set_envelope(cfg, ids["mgr"], ids["ceo"], 100, 100_000)
    E.spend(cfg, agent_id=ids["mgr"], cents=100)          # fills the envelope
    bad = _refused(lambda: E.spend(cfg, agent_id=ids["mgr"], cents=50))
    assert bad is not None and bad[0] == "OFFICE_ENVELOPE_EXCEEDED", bad


def test_v1_tiny_raise_does_not_unlock_unlimited_spend(tmp_path, monkeypatch):
    """Pre-fix: a 1-cent approved raise licensed 50000 cents of spend.

    Post-fix the 1-cent raise must NOT cover a 50000-cent overspend.
    """
    from voicebridge.office import envelope as E
    from voicebridge.office import ledger as L

    cfg, ids = _org(tmp_path, monkeypatch, name="v1b")
    mgr, ceo = ids["mgr"], ids["ceo"]
    _fund_and_set_envelope(cfg, mgr, ceo, 100, 100_000)
    E.spend(cfg, agent_id=mgr, cents=100)                 # envelope exhausted
    req = E.raise_above(cfg, agent_id=mgr, amount_cents=1, thesis="t",
                        expected_return="r", displaces="d", confirmed=True)
    E.decide_request(cfg, req["id"], "approve", by_agent=ceo, confirmed=True)
    bad = _refused(lambda: E.spend(cfg, agent_id=mgr, cents=50_000))
    assert bad is not None, \
        "a 1-cent raise must not license a 50000-cent spend"
    assert bad[0] == "OFFICE_ENVELOPE_EXCEEDED", bad
    # The refused spend must not have booked a metered row: the only metered
    # cost is the 100-cent in-envelope spend. (Balance = funding - that 100.)
    assert L.sum_metered(cfg, scope="agent", id=mgr) == 100, \
        "the refused spend must not have booked metered cost"
    assert L.balance(cfg, mgr)["balance_cents"] == 100_000 - 100


def test_v1_raise_covering_the_overspend_is_honoured(tmp_path, monkeypatch):
    """ADVERSARIAL / completeness: the fix must not simply refuse everything.

    A raise that genuinely covers the overspend for the current period must
    still let the spend through, or the fix has broken the documented path.
    """
    from voicebridge.office import envelope as E

    cfg, ids = _org(tmp_path, monkeypatch, name="v1c")
    mgr, ceo = ids["mgr"], ids["ceo"]
    _fund_and_set_envelope(cfg, mgr, ceo, 100, 100_000)
    E.spend(cfg, agent_id=mgr, cents=100)                 # envelope exhausted
    req = E.raise_above(cfg, agent_id=mgr, amount_cents=500, thesis="t",
                        expected_return="r", displaces="d", confirmed=True)
    E.decide_request(cfg, req["id"], "approve", by_agent=ceo, confirmed=True)
    out = E.spend(cfg, agent_id=mgr, cents=400)           # within the raise
    assert out["booked"] is True and out["raised"] is True


def test_v1_raise_for_another_period_does_not_cover_this_one(
        tmp_path, monkeypatch):
    """ADVERSARIAL: the period linkage. A raise approved for 2000-01 must not
    license a spend in the current period."""
    from voicebridge.office import envelope as E

    cfg, ids = _org(tmp_path, monkeypatch, name="v1d")
    mgr, ceo = ids["mgr"], ids["ceo"]
    _fund_and_set_envelope(cfg, mgr, ceo, 100, 100_000)
    E.spend(cfg, agent_id=mgr, cents=100)
    req = E.raise_above(cfg, agent_id=mgr, amount_cents=1_000_000, thesis="t",
                        expected_return="r", displaces="d", period="2000-01",
                        confirmed=True)
    E.decide_request(cfg, req["id"], "approve", by_agent=ceo, confirmed=True)
    bad = _refused(lambda: E.spend(cfg, agent_id=mgr, cents=500))
    assert bad is not None, \
        "a raise for a past period must not license a current-period spend"


def test_v1_raise_does_not_cover_a_later_larger_overspend_in_same_period(
        tmp_path, monkeypatch):
    """ADVERSARIAL: consumption. If the raise is not consumed, repeated spends
    could each ride the same approval. Two spends above the envelope must not
    both succeed on one raise that covers only the first."""
    from voicebridge.office import envelope as E

    cfg, ids = _org(tmp_path, monkeypatch, name="v1e")
    mgr, ceo = ids["mgr"], ids["ceo"]
    _fund_and_set_envelope(cfg, mgr, ceo, 100, 1_000_000)
    E.spend(cfg, agent_id=mgr, cents=100)
    req = E.raise_above(cfg, agent_id=mgr, amount_cents=300, thesis="t",
                        expected_return="r", displaces="d", confirmed=True)
    E.decide_request(cfg, req["id"], "approve", by_agent=ceo, confirmed=True)
    first = E.spend(cfg, agent_id=mgr, cents=250)          # within the raise
    assert first["raised"] is True
    # A second 250 overspend has now consumed the raise (used 500 > 300).
    bad = _refused(lambda: E.spend(cfg, agent_id=mgr, cents=250))
    assert bad is not None, \
        "one approved raise must not license cumulative spend beyond its amount"


def test_v1_denied_raise_is_not_a_raise(tmp_path, monkeypatch):
    """ADVERSARIAL: a DENIED request must not count as an approved raise."""
    from voicebridge.office import envelope as E

    cfg, ids = _org(tmp_path, monkeypatch, name="v1f")
    mgr, ceo = ids["mgr"], ids["ceo"]
    _fund_and_set_envelope(cfg, mgr, ceo, 100, 100_000)
    E.spend(cfg, agent_id=mgr, cents=100)
    req = E.raise_above(cfg, agent_id=mgr, amount_cents=50_000, thesis="t",
                        expected_return="r", displaces="d", confirmed=True)
    E.decide_request(cfg, req["id"], "deny", by_agent=ceo, confirmed=True)
    bad = _refused(lambda: E.spend(cfg, agent_id=mgr, cents=400))
    assert bad is not None and bad[0] == "OFFICE_ENVELOPE_EXCEEDED", bad

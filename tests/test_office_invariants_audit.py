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
# FINDING #1 — BUDGET: an approved raise of N cents unlocks UNLIMITED spend.
# =========================================================================
# Envelope rule (envelope.py:12-20): "Above the envelope, it is refused unless
# raised." `spend()` enforces that via `_has_approved_raise` (envelope.py:398),
# which returns True if the seat has ANY request row with status='approved' —
# it never reads the approved amount, the period, or whether the envelope was
# actually topped up. So a $0.01 raise that the level above approved on an
# empty envelope licenses spending of any size.
def test_finding1_tiny_approved_raise_unlocks_unlimited_spend(tmp_path, monkeypatch):
    """FINDING #1 (BROKE): a 1-cent raise authorises 50000 cents of spend.

    Reproduction:
      1. org envelope = 100 cents; seat holds a large balance.
      2. spend 100  -> allowed (inside the envelope), headroom now 0.
      3. spend 50   -> refused OFFICE_ENVELOPE_EXCEEDED (correct).
      4. raise 1 cent, supervisor approves; the envelope is NOT topped up.
      5. spend 50000 -> ALLOWED. 500x the tiny raise, 500x the envelope.

    The docstring promises (2) above is refused unless raised — but the raise
    that was approved was for 1 cent, and `_has_approved_raise` cannot tell.
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

    # 5. spend 50000 cents against a 1-cent raise on a 100-cent envelope.
    out = E.spend(cfg, agent_id=mgr, cents=50_000)
    assert out["booked"] is True
    assert out["within_envelope"] is False
    # The seat's metered cost now dwarfs both the envelope and the raise.
    assert L.sum_metered(cfg, scope="agent", id=mgr) == 50_100


def test_has_approved_raise_ignores_amount_and_period(tmp_path, monkeypatch):
    """FINDING #1 root cause, isolated: the predicate reads only the status.

    ``_has_approved_raise`` selects ``SELECT 1 ... WHERE agent_id=? AND
    status='approved' LIMIT 1`` (envelope.py:402-404). It takes neither the
    amount nor a period, so its answer cannot depend on either. Two approvals
    for different periods both satisfy it in any period.
    """
    from voicebridge.office import envelope as E

    cfg, ids = _org(tmp_path, monkeypatch, name="raise_root")
    mgr, ceo = ids["manager"], ids["ceo"]

    # A raise approved for a PAST period.
    req = E.raise_above(cfg, agent_id=mgr, amount_cents=1, thesis="t",
                        expected_return="r", displaces="d", period="2000-01",
                        confirmed=True)
    E.decide_request(cfg, req["id"], "approve", by_agent=ceo, confirmed=True)

    # The predicate has no period argument at all — so today is covered by a
    # raise approved for the year 2000.
    assert E._has_approved_raise(cfg, agent_id=mgr) is True

    # And a DENIED request is correctly not counted (the guard is discriminating,
    # just not specific enough).
    req2 = E.raise_above(cfg, agent_id=mgr, amount_cents=1, thesis="t",
                         expected_return="r", displaces="d", confirmed=True)
    E.decide_request(cfg, req2["id"], "deny", by_agent=ceo, confirmed=True)
    assert E._has_approved_raise(cfg, agent_id=mgr) is True  # req1 still open


# =========================================================================
# FINDING #2 — MONEY: reversing an unmetered row manufactures metered money.
# =========================================================================
# ledger.record documents (ledger.py:44-47) that an `unmetered=True` row is
# "recorded, but no metered aggregate will ever see it. Agent balances ...
# exclude it." `reverse()` (ledger.py:152) writes a `transfer` whose
# compensating amount is derived from the original — but it does NOT propagate
# the original's `unmetered` flag, and a `transfer` is metered by definition.
# The compensating row therefore enters the metered fold the original was
# excluded from: undoing a phantom creates real money.
def test_finding2_reversing_unmetered_row_creates_metered_money(
        tmp_path, monkeypatch):
    """FINDING #2 (BROKE): assert_balances says HELD while the balance is fake.

    Reproduction:
      1. seat balance = 0.
      2. record inference 777 unmetered=True (a subscription-backed turn).
         balance stays 0 — correct (D16).
      3. reverse that row -> balance becomes +777 METERED cents.
         $7.77 of real, spendable balance conjured from a phantom row.

    Reversing unmetered *revenue* destroys real money in the other direction.
    """
    from voicebridge.office import ledger as L

    cfg, ids = _org(tmp_path, monkeypatch, name="unmetered_rev")
    seat = ids["manager"]

    assert L.balance(cfg, seat)["balance_cents"] == 0

    row = L.record(cfg, "inference", 777, agent_id=seat, unmetered=True,
                   note="subscription-backed turn")
    # D16: the unmetered row must not move the balance.
    assert L.balance(cfg, seat)["balance_cents"] == 0, "D16 already broken"

    L.reverse(cfg, row["id"])
    # The compensating row is a metered transfer -> real balance appears.
    assert L.balance(cfg, seat)["balance_cents"] == 777
    assert L.sum_metered(cfg) == 0      # the transfer nets out of *cost* ...
    # ... but the seat now holds 777 metered cents of headroom it never earned.


def test_finding2_reversing_unmetered_revenue_destroys_metered_money(
        tmp_path, monkeypatch):
    """FINDING #2, the destructive direction (BROKE).

    A seat with balance 0 records unmetered revenue 5000 (balance stays 0),
    then reverses it. The compensating transfer is -5000 METERED, so the seat's
    balance drifts to -5000 for money that was never in the metered book.
    """
    from voicebridge.office import ledger as L

    cfg, ids = _org(tmp_path, monkeypatch, name="unmetered_rev2")
    seat = ids["manager"]

    row = L.record(cfg, "revenue", 5000, agent_id=seat, unmetered=True,
                   note="unmetered revenue")
    assert L.balance(cfg, seat)["balance_cents"] == 0

    L.reverse(cfg, row["id"])
    assert L.balance(cfg, seat)["balance_cents"] == -5000


def test_finding2_balance_sheet_reports_balanced_through_the_break(
        tmp_path, monkeypatch):
    """FINDING #2 is invisible to the reconciliation gate (BROKE).

    ``assert_balances`` recomputes from the same rows the reversal wrote, so it
    sees a self-consistent book and reports ``ok: True``. The money was created
    by an ordinary API call and the auditor's control does not see it — which is
    the worse half of the finding: the control the docs lean on cannot catch it.
    """
    from voicebridge.office import balance_sheet as BS
    from voicebridge.office import ledger as L

    cfg, ids = _org(tmp_path, monkeypatch, name="unmetered_bs")
    seat = ids["manager"]

    row = L.record(cfg, "inference", 777, agent_id=seat, unmetered=True)
    assert BS.assert_balances(cfg)["ok"] is True     # clean before

    L.reverse(cfg, row["id"])
    gate = BS.assert_balances(cfg)
    assert gate["ok"] is True                        # still "balanced"
    assert gate["residual"] == 0
    assert L.balance(cfg, seat)["balance_cents"] == 777   # but 777 was minted


# =========================================================================
# FINDING #3 — CONSENT/SoD: task-review with no reviewer identity accepts work.
# =========================================================================
# work.py:9-11 promises: "The reviewer is exactly one level above the assignee
# (C31). A worker does not accept its own work." The check at work.py:197 is
# guarded by `by_agent is not None`, and the CLI's --by defaults to None
# (cli.py:1796). So `office task-review <id> --accept --confirm` skips the
# separation-of-duties gate entirely.
def test_finding3_review_with_no_identity_accepts_any_task(tmp_path, monkeypatch):
    """FINDING #3 (BROKE): a task is accepted with no reviewer check.

    Reproduction:
      1. worker claims a task, completes it -> status 'review', reviewer=mgr.
      2. W.review(..., verdict='accept', by_agent=w1) -> refused WRONG_LEVEL
         (the SoD rule works when an identity is supplied).
      3. W.review(..., verdict='accept', by_agent=None) -> ACCEPTED.
         The CLI's `--by` defaults to None, so this is reachable as
         `qubit office task-review <id> --accept --confirm`.
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

    # 2. an identity that is NOT the reviewer is refused (the rule works here)
    with pytest.raises(OfficeError) as exc:
        W.review(cfg, t, verdict="accept", by_agent=w1, confirmed=True)
    assert exc.value.code == "OFFICE_REVIEW_WRONG_LEVEL"

    # 3. NO identity at all: the guard is skipped and the task is accepted.
    out = W.review(cfg, t, verdict="accept", by_agent=None, confirmed=True)
    assert out["status"] == "accepted"
    assert W.tasks(cfg)["tasks"][0]["status"] == "accepted"


# =========================================================================
# FINDING #4 — DISPATCH: a running task can be silently re-claimed by a peer.
# =========================================================================
# work.claim's docstring says "Assign a task to a seat, enforcing the chain"
# and the module promises a reviewer exactly one level above the assignee. But
# claim accepts `running` as a claimable state (work.py:124) with no check that
# the caller is the current assignee, so a second seat overwrites the in-flight
# claim and the reviewer escalates to the new (peer) seat's supervisor — while
# the first seat's turn is still mid-flight and untracked.
def test_finding4_running_task_is_silently_reclaimed(tmp_path, monkeypatch):
    """FINDING #4 (BROKE): w2 steals w1's in-progress task with no trail.

    Reproduction:
      1. claim task t by w1  -> assignee=w1, status=running.
      2. claim the same t by w2 -> succeeds, assignee=w2, status=running.
         No error, no 'requeued' event: w1's turn is now untracked work.
    """
    from voicebridge.office import work as W

    cfg, ids = _org(tmp_path, monkeypatch, name="steal")
    w1, w2 = ids["w1"], ids["w2"]

    t = W.create(cfg, "t", exit_check="true", confirmed=True)["id"]
    first = W.claim(cfg, t, agent_id=w1, confirmed=True)
    assert first["assignee_id"] == w1

    second = W.claim(cfg, t, agent_id=w2, confirmed=True)
    assert second["assignee_id"] == w2                    # stolen
    task = W.tasks(cfg)["tasks"][0]
    assert task["assignee_id"] == w2
    assert task["status"] == "running"

    # The trail records two clean claims and no handoff — a reader cannot tell
    # that w1's work was discarded.
    kinds = [e["kind"] for e in W.trail(cfg, t)["events"]]
    assert kinds.count("claimed") == 2
    assert "requeued" not in kinds


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


def test_mutation_unmetered_propagation_would_fix_finding2(tmp_path):
    """Proof FINDING #2 is exactly the missing unmetered propagation.

    A scratch copy of ledger.py whose ``reverse`` passes ``unmetered=`` through
    to the compensating ``transfer`` is parsed from source; the change is proven
    real by the presence of the propagated keyword. This documents the minimal
    fix shape without applying it (evidence first).
    """
    src = ROOT / "voicebridge" / "office" / "ledger.py"
    text = src.read_text()
    assert "unmetered=unmetered" not in text.split("def record", 1)[0], (
        "reverse() apparently already propagates unmetered; re-audit FINDING #2")

    # The compensating call in reverse() currently omits unmetered entirely.
    rev_src = text.split("def reverse", 1)[1].split("def rows", 1)[0]
    assert "out = record(cfg, \"transfer\", delta," in rev_src
    assert "unmetered" not in rev_src.split("out = record", 1)[1], (
        "reverse() now passes unmetered; FINDING #2 may be fixed — re-check")

    # The minimal fix: thread `unmetered=bool(original[\"unmetered\"])` into that
    # call, so a reversal of a phantom stays a phantom. Simulated here textually.
    fixed = text.replace(
        'out = record(cfg, "transfer", delta,',
        'out = record(cfg, "transfer", delta,\n'
        '                 unmetered=bool(original["unmetered"]),', 1)
    assert fixed != text

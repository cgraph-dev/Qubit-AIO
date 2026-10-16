"""D15 — the orphan reaper: a stale STATUS is not unknown spend.

THE BUG CLASS THIS PINS
-----------------------
``finish_run`` writes the ledger row FIRST and settles the receipt SECOND
(``receipts.py``). A process that dies between those two writes leaves a receipt
whose money is already on the book but whose status still reads ``open``. That
single row makes ``ledger.health().complete`` false, which makes **every**
admission in the org refuse — tens of seats blocked by one crashed process,
until a human types an attestation for spend that was already recorded
correctly.

So the reaper's whole job is to draw the line that the crash erases:

* a receipt WITH a ledger row → a **status** bug. Reconcile it; the money was
  never in question.
* a receipt WITHOUT one → genuine **unknown spend**. Refuse to close it, and
  name it for ``settle``, which needs the owner (D15).

The invariant that matters most here is therefore a NEGATIVE one: reconciling
an orphan must never create a ledger row. A reaper that booked a zero would
hand the org free headroom, which is precisely the failure D15 exists to stop —
so ``test_reconcile_never_books_a_ledger_row`` is the load-bearing test.

Hermetic: tmp XDG state only; no network, no provider call, no live service.
"""
from __future__ import annotations

import time

import pytest


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _ledger_cents(cfg, run_id):
    """The run's booked cents, or ``None`` when no row exists."""
    from voicebridge.office import store

    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        row = conn.execute("SELECT amount_cents FROM office_ledger"
                           " WHERE run_id=? LIMIT 1", (run_id,)).fetchone()
        return None if row is None else int(row["amount_cents"])
    finally:
        conn.close()


def _reconcile_one(cfg, run_id, *, ledger_row_exists, cap_cents=500,
                   age_s=7200.0, older_than=3600.0):
    """Build ONE receipt aged past the floor, optionally with a ledger row.

    The receipt is written directly against a real hired seat, not through
    ``begin_run``: the point is to build the post-crash SHAPE precisely, which
    the admission path (rightly) refuses to produce.
    """
    from voicebridge.office import ledger, org as O, store

    ledger.health(cfg)                       # creates the store
    agent_id = O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)["id"]
    started = store.now() - age_s
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        conn.execute(
            "INSERT INTO office_run_receipts(run_id, agent_id, started_at,"
            " cap_cents, status) VALUES(?,?,?,?, 'open')",
            (run_id, agent_id, started, cap_cents))
        conn.commit()
    finally:
        conn.close()
    if ledger_row_exists:
        ledger.record(cfg, "inference", 250, agent_id=agent_id, run_id=run_id,
                      recorded_by="office:runner")
    return started


# ---------------------------------------------- the split: status vs unknown
def test_receipt_with_a_ledger_row_is_a_status_bug_and_is_reconciled(
        tmp_path, monkeypatch):
    """The crash-between-writes case: money booked, status stale."""
    from voicebridge.office import ledger, receipts

    cfg = _cfg(tmp_path, monkeypatch)
    _reconcile_one(cfg, "run_stale", ledger_row_exists=True)

    before = ledger.health(cfg)
    assert before["complete"] is False, "an open receipt blocks admission"

    out = receipts.reconcile_orphans(cfg, older_than_seconds=3600.0)
    assert [r["run_id"] for r in out["reconciled"]] == ["run_stale"]
    assert out["needs_attestation"] == [], "booked spend is not unknown spend"

    after = ledger.health(cfg)
    assert after["complete"] is True, "one stale status must not block the org"
    # The receipt took the LEDGER's figure, not zero and not the cap: the two
    # can never disagree again.
    assert _ledger_cents(cfg, "run_stale") == 250
    listed = {r["run_id"]: r for r in receipts.receipts(cfg)["receipts"]}
    assert listed["run_stale"]["settled_cents"] == 250
    # A reconciliation is NOT an attestation: no owner figure was entered, so
    # the column that says "the owner attested this" must stay NULL.
    assert listed["run_stale"]["attested_as"] is None
    assert "reconciled" in listed["run_stale"]["note"]


def test_receipt_without_a_ledger_row_is_unknown_spend_and_survives(
        tmp_path, monkeypatch):
    """The real D15 case: the turn died before either write."""
    from voicebridge.office import ledger, receipts

    cfg = _cfg(tmp_path, monkeypatch)
    _reconcile_one(cfg, "run_unknown", ledger_row_exists=False)

    out = receipts.reconcile_orphans(cfg, older_than_seconds=3600.0)
    assert out["reconciled"] == [], "the reaper must never close unknown spend"
    needs = out["needs_attestation"]
    assert [r["run_id"] for r in needs] == ["run_unknown"]
    assert "viking" not in str(needs)  # no invented pointer either

    health = ledger.health(cfg)
    assert health["complete"] is False, "D15 still holds the org"
    assert _ledger_cents(cfg, "run_unknown") is None, "and no row was created"


def test_reconcile_never_books_a_ledger_row(tmp_path, monkeypatch):
    """THE negative invariant: reaping cannot mint headroom.

    If this ever books a 0-cent row, the book reads complete, admission opens,
    and the org runs on money it already burned — the exact failure D15 names.
    The ledger row count must not move for an unknown-spend run.
    """
    from voicebridge.office import receipts, store

    cfg = _cfg(tmp_path, monkeypatch)
    _reconcile_one(cfg, "run_no_row", ledger_row_exists=False)

    def row_count():
        conn = store.connect(cfg)
        try:
            store.migrate(conn)
            return int(conn.execute("SELECT COUNT(*) c FROM office_ledger")
                       .fetchone()["c"])
        finally:
            conn.close()

    before = row_count()
    receipts.reconcile_orphans(cfg, older_than_seconds=3600.0)
    receipts.reconcile_orphans(cfg, older_than_seconds=3600.0)
    assert row_count() == before


# -------------------------------------------------------------- the age floor
def test_a_young_receipt_is_not_reaped(tmp_path, monkeypatch):
    """A live turn's open receipt is spend-in-progress, not an orphan.

    The age floor is the whole safety of the write half: a receipt belonging to
    a turn that is still running must not be settled under it. Sixty seconds of
    age is a normal in-flight provider round-trip, well inside the floor.
    """
    from voicebridge.office import ledger, receipts

    cfg = _cfg(tmp_path, monkeypatch)
    started = _reconcile_one(cfg, "run_live", ledger_row_exists=True,
                             age_s=60.0, older_than=3600.0)
    assert receipts.orphaned(cfg, older_than_seconds=3600.0)["count"] == 0

    out = receipts.reconcile_orphans(cfg, older_than_seconds=3600.0)
    assert out["reconciled"] == [], "settling a running turn books it early"
    assert out["count"] == 0
    assert out["needs_attestation"] == []
    assert ledger.health(cfg)["complete"] is False
    listed = {r["run_id"]: r for r in receipts.receipts(cfg)["receipts"]}
    assert listed["run_live"]["status"] == "open"
    # With the floor at zero the SAME receipt is visible, so the age floor is
    # what excluded it — not a predicate that ignores it everywhere.
    assert receipts.orphaned(cfg, older_than_seconds=0.0)["count"] == 1


def test_idempotent_a_second_run_is_a_no_op(tmp_path, monkeypatch):
    from voicebridge.office import receipts

    cfg = _cfg(tmp_path, monkeypatch)
    _reconcile_one(cfg, "run_stale2", ledger_row_exists=True)
    first = receipts.reconcile_orphans(cfg, older_than_seconds=3600.0)
    second = receipts.reconcile_orphans(cfg, older_than_seconds=3600.0)
    assert [r["run_id"] for r in first["reconciled"]] == ["run_stale2"]
    assert second["reconciled"] == []
    assert second["count"] == 0, "closed rows no longer match the predicate"


# ------------------------------------------------------------- reporting only
def test_orphaned_report_writes_nothing(tmp_path, monkeypatch):
    from voicebridge.office import ledger, receipts

    cfg = _cfg(tmp_path, monkeypatch)
    _reconcile_one(cfg, "run_unknown2", ledger_row_exists=False)
    before = ledger.health(cfg)
    report = receipts.orphaned(cfg, older_than_seconds=3600.0)
    after = ledger.health(cfg)

    assert report["unknown"] and report["paid"] == []
    assert before == after, "the report half must be a pure read"
    assert after["complete"] is False


def test_owner_attestation_closes_what_the_reaper_refused(
        tmp_path, monkeypatch):
    """The escape hatch that stays human: settle, on the owner's words."""
    from voicebridge.office import ledger, receipts

    cfg = _cfg(tmp_path, monkeypatch)
    _reconcile_one(cfg, "run_needs_owner", ledger_row_exists=False,
                   cap_cents=500)

    reaper = receipts.reconcile_orphans(cfg, older_than_seconds=3600.0)
    assert len(reaper["needs_attestation"]) == 1

    out = receipts.settle(cfg, "run_needs_owner", cents=500,
                          attested_as="limit",
                          note="owner attested at the run's cap")
    assert out["settled"] is True
    assert ledger.health(cfg)["complete"] is True
    listed = {r["run_id"]: r for r in receipts.receipts(cfg)["receipts"]}
    assert listed["run_needs_owner"]["settled_cents"] == 500

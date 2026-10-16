"""The orphan reaper must UNBLOCK a jammed org, not merely report.

The unit suite (`test_office_orphan_reaper.py`) proves the reaper draws the
right line between a stale STATUS and genuine unknown spend. This one proves
the consequence that mattered in production: one crashed turn left a receipt
whose money was already booked, and that single row refused **every** seat in
the org until a human typed an attestation for spend that had been recorded
correctly.

So the property under test is end-to-end and behavioural, not structural:

1. A receipt with a booked ledger row refuses admission (the bug's symptom).
2. One ``tick`` reconciles it — no owner, no attestation, no money moved.
3. Admission opens again, with the SAME money still on the book.

The third clause is the guard against a "fix" that unblocks the org by
forgetting what it spent: the ledger cents before and after must be identical.
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


def _orphan_with_a_booked_row(cfg, *, run_id, cap_cents=500, cents=250):
    """The exact post-crash shape: ledger row written, status never updated.

    Built by hand rather than through ``finish_run`` because that function is
    what *avoids* this state — reproducing the crash means doing its two writes
    one at a time and stopping between them.
    """
    from voicebridge.office import ledger, org as O, store

    ledger.health(cfg)                       # creates the store
    agent_id = O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)["id"]
    started = store.now() - 7200.0           # two hours: past the tick's floor
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
    ledger.record(cfg, "inference", cents, agent_id=agent_id, run_id=run_id,
                  recorded_by="office:runner")
    return {"agent_id": agent_id, "run_id": run_id, "cents": cents}


def _metered_cents(cfg):
    from voicebridge.office import store

    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        return int(conn.execute(
            "SELECT COALESCE(SUM(amount_cents),0) c FROM office_ledger"
            " WHERE unmetered=0 AND kind='inference'").fetchone()["c"])
    finally:
        conn.close()


def test_the_jammed_org_is_unblocked_by_one_tick(tmp_path, monkeypatch):
    """The production bug, end to end: a stale status blocked every seat."""
    from voicebridge.office import autowork, ledger, store

    cfg = _cfg(tmp_path, monkeypatch)
    built = _orphan_with_a_booked_row(cfg, run_id="run_crashed")

    # --- the symptom -------------------------------------------------------
    assert ledger.health(cfg)["complete"] is False
    before = _metered_cents(cfg)
    assert before == built["cents"], "the crash happened AFTER the money booked"

    # --- one tick reconciles it, with no seat woken -------------------------
    # The tick is forced past the cadence and past the owner flag is NOT
    # touched: enabling the driver is not the reaper's business.
    monkeypatch.setattr(store, "estop_engaged", lambda _cfg: False)
    out = autowork.run_now(cfg, requested_by="test:reaper")

    reconciled = [r["run_id"] for r in out["reaped"]["reconciled"]]
    assert reconciled == ["run_crashed"], "the tick must have reconciled it"
    assert out["reaped"]["needs_attestation"] == []

    # --- and the org is open again, with the same money on the book ----------
    assert ledger.health(cfg)["complete"] is True
    assert _metered_cents(cfg) == before, (
        "unblocking the org must not change what it has spent")
    assert out["reaped"]["reconciled"][0]["cents"] == built["cents"]


def test_unknown_spend_in_the_same_store_still_refuses(tmp_path, monkeypatch):
    """The reaper is not a universal unblocker.

    One store, two orphans: one with a booked row (status bug) and one without
    (real unknown spend). The same tick must reconcile the first and REFUSE the
    second, so the org's protection survives the bug fix.
    """
    from voicebridge.office import autowork, ledger, org as O, receipts, store

    cfg = _cfg(tmp_path, monkeypatch)
    stored = _orphan_with_a_booked_row(cfg, run_id="run_stale")

    ledger.health(cfg)
    agent_id = O.hire(cfg, "ceo-2", seat_type="worker",
                      supervisor_id=stored["agent_id"], confirmed=True)["id"]
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        conn.execute(
            "INSERT INTO office_run_receipts(run_id, agent_id, started_at,"
            " cap_cents, status) VALUES(?,?,?,?, 'open')",
            ("run_unknown", agent_id, store.now() - 7200.0, 500))
        conn.commit()
    finally:
        conn.close()

    assert ledger.health(cfg)["complete"] is False

    monkeypatch.setattr(store, "estop_engaged", lambda _cfg: False)
    out = autowork.run_now(cfg, requested_by="test:reaper")

    assert [r["run_id"] for r in out["reaped"]["reconciled"]] == ["run_stale"]
    assert [r["run_id"] for r in out["reaped"]["needs_attestation"]] \
        == ["run_unknown"]
    # The money that was booked is still booked; nothing was invented.
    assert _metered_cents(cfg) == stored["cents"]
    assert ledger.health(cfg)["complete"] is False
    # And the human path still closes exactly the one the reaper refused.
    closed = receipts.settle(cfg, "run_unknown", cents=500,
                             attested_as="limit")
    assert closed["settled"] is True
    assert ledger.health(cfg)["complete"] is True

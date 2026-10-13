"""C1 + C2 — the ledger is append-only and every total is derived.

Contract proofs for `docs/game-office/95-CONTRACT.md`:

* **C1** — ``office_ledger`` accepts INSERT only. UPDATE and DELETE are
  refused *by the database*, with a message naming the rule. A mistake is
  corrected by a compensating row, never by an edit.
* **C2** — every displayed total is ``GROUP BY`` over the ledger. A stored
  rollup cannot disagree with it, because there is no stored rollup.

Hermetic: tmp XDG state only; no network, no live services, no provider call.
"""
from __future__ import annotations

import sqlite3

import pytest


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


# ---------------------------------------------------------------- C1: append
def test_ledger_refuses_update_and_delete_at_the_database(tmp_path, monkeypatch):
    """The rule is enforced by triggers, not by application discipline."""
    from voicebridge.office import ledger, store

    cfg = _cfg(tmp_path, monkeypatch)
    store.init(cfg)
    ledger.record(cfg, "inference", 2500, agent_id=None, note="one turn")

    conn = store.connect(cfg)
    try:
        with pytest.raises(sqlite3.IntegrityError) as up:
            conn.execute("UPDATE office_ledger SET amount_cents=0")
        assert "append-only" in str(up.value)
        assert "UPDATE" in str(up.value)

        with pytest.raises(sqlite3.IntegrityError) as dele:
            conn.execute("DELETE FROM office_ledger")
        assert "append-only" in str(dele.value)
        assert "DELETE" in str(dele.value)
        conn.rollback()
    finally:
        conn.close()

    # …and the row is untouched by the attempts.
    out = ledger.rows(cfg)
    assert out["count"] == 1
    assert out["rows"][0]["amount_cents"] == 2500


def test_a_mistake_is_corrected_by_a_compensating_row(tmp_path, monkeypatch):
    """A reversal appends; it never rewrites the row it offsets (C1)."""
    from voicebridge.office import ledger, store

    cfg = _cfg(tmp_path, monkeypatch)
    store.init(cfg)
    bad = ledger.record(cfg, "inference", 900, note="mis-recorded")
    rev = ledger.reverse(cfg, bad["id"], note="wrong model priced")

    assert rev["id"] != bad["id"]
    assert rev["kind"] == "transfer"
    assert rev["amount_cents"] == 900          # give the cost back
    assert rev["reversed"] == bad["id"]
    assert rev["reversed_kind"] == "inference"

    # The original still reads exactly as recorded, and the pair nets to zero.
    rows = {r["id"]: r for r in ledger.rows(cfg)["rows"]}
    assert rows[bad["id"]]["amount_cents"] == 900
    assert rows[rev["id"]]["offsets_id"] == bad["id"]
    org = ledger.pnl(cfg, scope="org")
    assert org["net_cents"] == 0
    assert org["cost_cents"] == 900            # gross cost stays on the record


def test_reversal_of_revenue_gives_the_revenue_back(tmp_path, monkeypatch):
    from voicebridge.office import ledger, store

    cfg = _cfg(tmp_path, monkeypatch)
    store.init(cfg)
    sale = ledger.record(cfg, "revenue", 12500, note="first sale")
    assert ledger.pnl(cfg, scope="org")["net_cents"] == 12500

    ledger.reverse(cfg, sale["id"])
    org = ledger.pnl(cfg, scope="org")
    assert org["net_cents"] == 0
    assert org["revenue_cents"] == 12500       # gross stays, net is corrected


def test_reverse_refuses_an_unknown_row(tmp_path, monkeypatch):
    from voicebridge.office import ledger, store
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    store.init(cfg)
    with pytest.raises(OfficeError) as err:
        ledger.reverse(cfg, 4242)
    assert err.value.code == "OFFICE_NOT_FOUND"


# --------------------------------------------------------------- C2: derived
def test_pnl_is_exact_to_the_cent(tmp_path, monkeypatch):
    """Mixed kinds, derived not stored. A float would fail this."""
    from voicebridge.office import ledger, store

    cfg = _cfg(tmp_path, monkeypatch)
    store.init(cfg)

    ledger.record(cfg, "revenue", 10000, note="sale")
    ledger.record(cfg, "revenue", 2500, note="sale 2")
    ledger.record(cfg, "inference", 3333, note="turns")
    ledger.record(cfg, "fees", 291, note="platform fee")
    ledger.record(cfg, "capex", 1500, note="one-off")
    # A bare signed entry is an `adjustment`. `transfer` is reserved for the
    # compensating row, which MUST name the row it offsets (C1) — so it cannot
    # be used as a free-floating signed amount.
    ledger.record(cfg, "adjustment", -100, note="tiny signed adjustment")

    org = ledger.pnl(cfg, scope="org")
    assert org["revenue_cents"] == 12500
    assert org["cost_cents"] == 3333 + 291 + 1500      # magnitudes sum up
    assert org["net_cents"] == 12500 - 5124 - 100      # signed transfer applies
    assert isinstance(org["net_cents"], int)


def test_cost_is_never_added_to_revenue(tmp_path, monkeypatch):
    """The bug this view exists to prevent: SUM(amount) over magnitudes."""
    from voicebridge.office import ledger, store

    cfg = _cfg(tmp_path, monkeypatch)
    store.init(cfg)
    ledger.record(cfg, "revenue", 1000)
    ledger.record(cfg, "inference", 400)

    org = ledger.pnl(cfg, scope="org")
    assert org["net_cents"] == 600          # not 1400
    assert org["cost_cents"] == 400


def test_org_agent_and_department_rollups_agree(tmp_path, monkeypatch):
    """Three views, one truth: the parts sum to the whole."""
    from voicebridge.office import ledger, org as O, store

    cfg = _cfg(tmp_path, monkeypatch)
    store.init(cfg)
    O.floor_create(cfg, 1, "Operations", confirmed=True)
    f = O.floors(cfg)["floors"][0]["id"]
    O.department_create(cfg, "pod", floor_id=f, division_kind="product",
                        kill_criteria="no sale in 30 days", confirmed=True)
    dept = store.connect(cfg)
    try:
        dept_id = dept.execute("SELECT id FROM office_department WHERE"
                               " slug='pod'").fetchone()["id"]
    finally:
        dept.close()

    ceo = O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)
    O.hire(cfg, "mgr", seat_type="manager", department_id=dept_id,
           supervisor_id=ceo["id"], confirmed=True)

    ledger.record(cfg, "revenue", 8000, department_id=dept_id,
                  agent_id=ceo["id"])
    ledger.record(cfg, "inference", 2000, department_id=dept_id,
                  agent_id=ceo["id"])

    org_total = ledger.pnl(cfg, scope="org")["net_cents"]
    by_dept = ledger.pnl(cfg, scope="department", id=dept_id)["net_cents"]
    by_agents = sum(r["net_cents"] for r in
                    ledger.pnl(cfg, scope="agent")["rows"])
    assert org_total == 6000
    assert by_dept == 6000
    assert by_agents == 6000


def test_health_reports_zero_drift_and_live_append_only(tmp_path, monkeypatch):
    """`qubit office health` is only green when both invariants hold."""
    from voicebridge.office import ledger, store

    cfg = _cfg(tmp_path, monkeypatch)
    store.init(cfg)
    ledger.record(cfg, "revenue", 500)
    ledger.record(cfg, "inference", 125)

    h = ledger.health(cfg)
    assert h["healthy"] is True
    assert h["triggers_present"] is True
    assert h["append_only_enforced"] == {"update": True, "delete": True}
    assert h["append_only_proof"] == "live"
    assert h["rollup_drift_cents"] == 0
    assert h["net_cents"] == 375
    assert h["entries"] == 2


def test_health_on_an_empty_ledger_says_unverified_not_green(tmp_path,
                                                             monkeypatch):
    """A BEFORE trigger fires per row, so an empty book proves nothing.

    This is the honesty rule (D14) applied to the health check itself: with no
    row for the trigger to fire on, the forbidden writes succeed trivially. The
    check must report "unverified" rather than claiming enforcement it did not
    observe.
    """
    from voicebridge.office import ledger, store

    cfg = _cfg(tmp_path, monkeypatch)
    store.init(cfg)

    h = ledger.health(cfg)
    assert h["entries"] == 0
    assert h["net_cents"] == 0
    assert h["triggers_present"] is True          # the schema is right…
    assert h["append_only_enforced"] == {"update": None, "delete": None}
    assert "unverified" in h["append_only_proof"]
    assert h["healthy"] is True                   # …and nothing is broken


# ------------------------------------------------- C14: unknown spend (D15)
def test_an_open_receipt_blocks_admission_and_settling_restores_it(
        tmp_path, monkeypatch):
    """C14's sequence, end to end: receipt → refuse → attest → resume."""
    from voicebridge.office import ledger, org as O, receipts, store
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    store.init(cfg)
    seat = O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)
    ledger.record(cfg, "revenue", 5000, agent_id=seat["id"])

    # 1. a run begins (receipt first, before any model call) …
    run = receipts.begin_run(cfg, seat["id"], cap_cents=50)
    # 2. … and dies. No ledger row is ever written.
    h = ledger.health(cfg)
    assert h["complete"] is False
    assert h["healthy"] is False
    assert h["open_runs"][0]["run_id"] == run["run_id"]

    # 3. every admission refuses, naming the unsettled run
    with pytest.raises(OfficeError) as err:
        receipts.admit(cfg, agent_id=seat["id"], estimated_cents=5)
    assert err.value.code == "OFFICE_ADMISSION_REFUSED"
    assert run["run_id"] in err.value.detail

    # 4. the owner states what was actually charged
    receipts.settle(cfg, run["run_id"], cents=9, attested_as="entered")
    assert ledger.health(cfg)["complete"] is True
    assert receipts.admit(cfg, agent_id=seat["id"],
                          estimated_cents=5)["state"] == "OK"

    # 5. the settled row is attested, and never a guess
    row = [r for r in ledger.rows(cfg)["rows"]
           if r["run_id"] == run["run_id"]][0]
    assert row["attested"] == 1
    assert row["attested_as"] in ("entered", "limit")


def test_an_unreadable_ledger_is_unknown_spend_not_an_empty_book(
        tmp_path, monkeypatch):
    """D15 names an unreadable ledger explicitly. It must refuse, not crash."""
    from voicebridge.office import ledger, org as O, receipts, store
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    store.init(cfg)
    seat = O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)
    ledger.record(cfg, "revenue", 5000, agent_id=seat["id"])
    assert ledger.health(cfg)["complete"] is True

    # Corrupt the store: the file exists and is not a database.
    path = store.db_path(cfg)
    for suffix in ("", "-wal", "-shm"):
        candidate = path.with_name(path.name + suffix)
        if candidate.exists():
            candidate.unlink()
    path.write_bytes(b"this is not a sqlite database" * 64)

    h = ledger.health(cfg)
    assert h["state"] == "DEGRADED"
    assert h["complete"] is False
    assert h["healthy"] is False
    assert h["net_cents"] is None          # never 0: unknown is not zero

    with pytest.raises(OfficeError) as err:
        receipts.admit(cfg, agent_id=seat["id"], estimated_cents=5)
    assert err.value.code == "OFFICE_ADMISSION_REFUSED"


# ------------------------------------------------------- the typed refusals
def test_negative_magnitude_and_float_money_are_refused(tmp_path, monkeypatch):
    from voicebridge.office import ledger, store
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    store.init(cfg)

    with pytest.raises(OfficeError) as neg:
        ledger.record(cfg, "revenue", -100)
    assert neg.value.code == "OFFICE_BAD_INPUT"

    with pytest.raises(OfficeError) as flt:
        ledger.record(cfg, "inference", 12.5)      # type: ignore[arg-type]
    assert flt.value.code == "OFFICE_BAD_INPUT"

    with pytest.raises(OfficeError) as zero:
        ledger.record(cfg, "transfer", 0)
    assert zero.value.code == "OFFICE_BAD_INPUT"

    with pytest.raises(OfficeError) as orphan:
        ledger.record(cfg, "transfer", -50)        # no offsets_id
    assert orphan.value.code == "OFFICE_BAD_STATE"

    with pytest.raises(OfficeError) as bogus:
        ledger.record(cfg, "inference", 10, offsets_id=999)
    assert bogus.value.code == "OFFICE_NOT_FOUND"

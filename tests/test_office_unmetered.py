"""C15 — two money folds, never interchangeable (D16).

`sum_metered()` feeds every cap, every balance, every P&L. `sum_reported()`
answers "what did the provider say" and must never feed a cap.

This is not a tidiness rule. StarNet live-hit the failure: a subscription
turn booked as spend, then *"that phantom spend BLOCKED a later real BYOK run
with reason `budget` and a CAP HIT note naming money nobody spent."* Phantom
spend denies real work.

Hermetic: tmp XDG state only; no network, no provider call.
"""
from __future__ import annotations


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _seated_cfg(tmp_path, monkeypatch, *, funded=0):
    """A cfg with a CEO seat, so agent-scoped folds have a row to hang on.

    Unfunded by default so each test owns its own arithmetic. Cap tests pass
    ``funded=`` explicitly: an unfunded seat trips the balance refusal *before*
    any cap check, which would make a cap test pass for the wrong reason.
    """
    from voicebridge.office import ledger, org as O, store

    cfg = _cfg(tmp_path, monkeypatch)
    store.init(cfg)
    seat = O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)
    if funded:
        ledger.record(cfg, "revenue", funded, agent_id=seat["id"],
                      note="owner capital")
    return cfg, seat["id"]


def test_metered_and_reported_folds_differ_by_exactly_the_unmetered_rows(
        tmp_path, monkeypatch):
    """The contract's own numbers: 100 metered + 500 unmetered."""
    from voicebridge.office import ledger

    cfg, agent_id = _seated_cfg(tmp_path, monkeypatch)
    ledger.record(cfg, "inference", 100, agent_id=agent_id,
                  note="metered turn")
    ledger.record(cfg, "inference", 500, agent_id=agent_id,
                  unmetered=True, note="subscription-backed research")

    # the contract's own numbers: metered *spend* 100, reported 600
    assert ledger.sum_metered(cfg) == 100
    assert ledger.sum_reported(cfg) == 600

    # …and both rows are still on the record: the run happened.
    org = ledger.pnl(cfg, scope="org")
    assert org["entries"] == 2
    assert org["metered_entries"] == 1
    assert org["unmetered_entries"] == 1
    # the reported fold is the provider's view: both rows, unmetered included
    assert org["reported_cost_cents"] == 600


def test_an_agent_balance_never_sees_unmetered_spend(tmp_path, monkeypatch):
    from voicebridge.office import ledger

    cfg, agent_id = _seated_cfg(tmp_path, monkeypatch)
    ledger.record(cfg, "revenue", 1000, agent_id=agent_id)
    ledger.record(cfg, "inference", 250, agent_id=agent_id)
    ledger.record(cfg, "inference", 900, agent_id=agent_id, unmetered=True)
    # note: no capital row, so the arithmetic below is the test's own

    bal = ledger.balance(cfg, agent_id)
    assert bal["balance_cents"] == 750          # 1000 - 250, never - 900
    assert bal["runs_recorded"] == 3            # the run happened
    assert bal["unmetered_runs"] == 1

    by_agent = ledger.pnl(cfg, scope="agent", id=agent_id)
    assert by_agent["cost_cents"] == 250        # unmetered excluded from cost


def test_an_unmetered_row_cannot_consume_cap_headroom(tmp_path, monkeypatch):
    """The StarNet failure, inverted: phantom spend must not deny real work."""
    from voicebridge.office import caps, ledger

    cfg, agent_id = _seated_cfg(tmp_path, monkeypatch)
    caps.set_cap(cfg, "perDay", 1000, confirmed=True)

    # 900 cents of subscription research — recorded, but not dollars.
    ledger.record(cfg, "inference", 900, agent_id=agent_id, unmetered=True)

    spent = __import__("voicebridge.office.receipts",
                       fromlist=["_spent"])._spent(cfg, "perDay")
    assert spent == 0, "unmetered spend must not count against a cap"
    h = caps.headroom(cfg, "perDay", spent)
    assert h["headroom_cents"] == 1000


def test_the_org_daily_cap_counts_only_metered_spend(tmp_path, monkeypatch):
    from voicebridge.office import caps, ledger, receipts

    cfg, agent_id = _seated_cfg(tmp_path, monkeypatch, funded=10_000)
    caps.set_cap(cfg, "perDay", 300, confirmed=True)
    # perRun would otherwise refuse the estimate first, and the test would pass
    # for the wrong reason. Only perDay should be binding here.
    caps.set_cap(cfg, "perRun", 0, confirmed=True)
    ledger.record(cfg, "inference", 250, agent_id=agent_id)
    ledger.record(cfg, "inference", 9000, agent_id=agent_id, unmetered=True)

    # 100 more would cross the 300 ceiling on metered spend alone (250 + 100).
    assert receipts._spent(cfg, "perDay") == 250
    from voicebridge.office.errors import OfficeError
    import pytest
    with pytest.raises(OfficeError) as err:
        receipts.admit(cfg, agent_id=agent_id, estimated_cents=100)
    assert err.value.code == "OFFICE_ADMISSION_REFUSED"
    assert "perDay" in err.value.detail

    # …and the same turn is admitted once the cap is cleared, because the
    # unmetered rows were never dollars.
    caps.set_cap(cfg, "perDay", 0, confirmed=True)     # 0 = no cap
    assert receipts.admit(cfg, agent_id=agent_id,
                          estimated_cents=100)["state"] == "OK"


def test_token_counts_include_unmetered_runs(tmp_path, monkeypatch):
    """Runs and tokens are counted even when no dollars changed hands."""
    from voicebridge.office import ledger

    cfg, agent_id = _seated_cfg(tmp_path, monkeypatch)   # no capital row
    ledger.record(cfg, "inference", 0, agent_id=agent_id, unmetered=True,
                  tokens_in=1200, tokens_out=300, note="free-tier research")

    org = ledger.pnl(cfg, scope="org")
    assert org["tokens_in"] == 1200
    assert org["tokens_out"] == 300
    assert org["net_cents"] == 0          # zero dollars, one run
    assert org["entries"] == 1


def test_department_fold_separates_the_two_too(tmp_path, monkeypatch):
    from voicebridge.office import ledger, org as O, store

    cfg = _cfg(tmp_path, monkeypatch)
    store.init(cfg)
    O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)
    O.floor_create(cfg, 1, confirmed=True)
    fid = O.floors(cfg)["floors"][0]["id"]
    O.department_create(cfg, "research", floor_id=fid,
                        division_kind="intelligence",
                        kill_criteria="nothing learned in 30 days",
                        confirmed=True)
    conn = store.connect(cfg)
    try:
        dept_id = conn.execute(
            "SELECT id FROM office_department WHERE slug='research'"
        ).fetchone()["id"]
    finally:
        conn.close()

    ledger.record(cfg, "inference", 40, department_id=dept_id)
    ledger.record(cfg, "inference", 4000, department_id=dept_id,
                  unmetered=True)

    row = ledger.pnl(cfg, scope="department", id=dept_id)
    assert row["net_cents"] == -40               # metered only
    assert row["reported_net_cents"] == -4040    # what the provider saw
    assert row["cost_cents"] == 40

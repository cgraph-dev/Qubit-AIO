"""C8 + C14 — hard caps are hard, and unknown spend blocks admission.

* **C14 (D15)** — a dispatch receipt is written before every turn's first
  model call. While any receipt is open, **every** budget check refuses.
  Unknown spend is never zero and never headroom.
* **C8 (R4)** — the caps are checked **at admission**, so a refusal happens
  before any provider call. Checked-after-the-fact is a budget report, not a
  budget.

The "provider NOT called" half of C8's proof is asserted here the only way M0
can honestly assert it: admission refuses **before** a receipt exists, so
there is nothing for a provider to be called from. The mock-provider assertion
lands with the runner in M1.

Hermetic: tmp XDG state only; no network, no provider call, no agent started.
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


def _seated(tmp_path, monkeypatch, *, funded=5000):
    from voicebridge.office import ledger, org as O, store

    cfg = _cfg(tmp_path, monkeypatch)
    store.init(cfg)
    seat = O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)
    if funded:
        ledger.record(cfg, "revenue", funded, agent_id=seat["id"],
                      note="owner capital")
    return cfg, seat["id"]


# --------------------------------------------------------------- C14: D15
def test_an_open_receipt_makes_the_book_incomplete(tmp_path, monkeypatch):
    from voicebridge.office import ledger, receipts

    cfg, agent_id = _seated(tmp_path, monkeypatch)
    assert ledger.health(cfg)["complete"] is True

    run = receipts.begin_run(cfg, agent_id, cap_cents=50)
    assert run["status"] == "open"

    health = ledger.health(cfg)
    assert health["complete"] is False
    assert health["healthy"] is False
    assert [r["run_id"] for r in health["open_runs"]] == [run["run_id"]]


def test_admission_refuses_on_unknown_spend_and_names_the_run(
        tmp_path, monkeypatch):
    """The killed-mid-turn case: spend that happened and was never booked."""
    from voicebridge.office import receipts
    from voicebridge.office.errors import OfficeError

    cfg, agent_id = _seated(tmp_path, monkeypatch)
    run = receipts.begin_run(cfg, agent_id, cap_cents=50)
    # …process dies here. No ledger row is ever written.

    with pytest.raises(OfficeError) as err:
        receipts.admit(cfg, agent_id=agent_id, estimated_cents=10)
    assert err.value.code == "OFFICE_ADMISSION_REFUSED"
    assert run["run_id"] in err.value.detail
    assert "unknown spend" in err.value.detail.lower()

    # A new run cannot even be receipted on top of it.
    with pytest.raises(OfficeError) as err2:
        receipts.begin_run(cfg, agent_id, cap_cents=50)
    assert err2.value.code == "OFFICE_ADMISSION_REFUSED"


def test_the_owner_settles_it_and_admission_resumes(tmp_path, monkeypatch):
    from voicebridge.office import ledger, receipts

    cfg, agent_id = _seated(tmp_path, monkeypatch)
    run = receipts.begin_run(cfg, agent_id, cap_cents=50)

    out = receipts.settle(cfg, run["run_id"], cents=37,
                          attested_as="entered")
    assert out["settled"] is True
    assert out["settled_cents"] == 37

    # The settled row carries the attestation — never guessed, never a bound.
    conn_row = [r for r in ledger.rows(cfg)["rows"]
                if r["run_id"] == run["run_id"]][0]
    assert conn_row["attested"] == 1
    assert conn_row["attested_as"] == "entered"

    assert ledger.health(cfg)["complete"] is True
    assert receipts.admit(cfg, agent_id=agent_id,
                          estimated_cents=10)["state"] == "OK"


def test_a_limit_attestation_books_the_cap_and_says_so(tmp_path, monkeypatch):
    """A limit booking must never claim to be a typed figure (D15)."""
    from voicebridge.office import ledger, receipts
    from voicebridge.office.errors import OfficeError

    cfg, agent_id = _seated(tmp_path, monkeypatch)
    run = receipts.begin_run(cfg, agent_id, cap_cents=50)

    # Booking a different amount as 'limit' is refused: it is not a fair guess.
    with pytest.raises(OfficeError) as err:
        receipts.settle(cfg, run["run_id"], cents=11, attested_as="limit")
    assert err.value.code == "OFFICE_BAD_INPUT"

    out = receipts.settle(cfg, run["run_id"], cents=50, attested_as="limit")
    assert out["settled"] is True
    row = [r for r in ledger.rows(cfg)["rows"]
           if r["run_id"] == run["run_id"]][0]
    assert row["attested_as"] == "limit"


def test_settlement_must_say_how_it_was_attested(tmp_path, monkeypatch):
    from voicebridge.office import receipts
    from voicebridge.office.errors import OfficeError

    cfg, agent_id = _seated(tmp_path, monkeypatch)
    run = receipts.begin_run(cfg, agent_id, cap_cents=50)
    with pytest.raises(OfficeError) as err:
        receipts.settle(cfg, run["run_id"], cents=5, attested_as="vibes")
    assert err.value.code == "OFFICE_BAD_INPUT"


def test_a_halted_run_is_distinguishable_from_a_crash(tmp_path, monkeypatch):
    """The e-stop marks halted; a crash leaves it open. Different states."""
    from voicebridge.office import ledger, receipts

    cfg, agent_id = _seated(tmp_path, monkeypatch)
    run = receipts.begin_run(cfg, agent_id, cap_cents=50)
    receipts.halt(cfg, run["run_id"], reason="owner e-stop")

    u = receipts.unsettled(cfg)
    assert u["count"] == 1
    assert u["runs"][0]["status"] == "halted"
    assert u["runs"][0]["end_reason"] == "owner e-stop"
    # halted is still not settled: it still blocks admission.
    assert ledger.health(cfg)["complete"] is False


def test_finish_run_writes_the_row_then_settles(tmp_path, monkeypatch):
    from voicebridge.office import ledger, receipts

    cfg, agent_id = _seated(tmp_path, monkeypatch)
    run = receipts.begin_run(cfg, agent_id, cap_cents=50)
    out = receipts.finish_run(cfg, run["run_id"], settled_cents=12,
                              tokens_in=800, tokens_out=120,
                              model="deepseek-v4.1-flash")

    assert out["ledger_id"]
    assert ledger.health(cfg)["complete"] is True
    row = [r for r in ledger.rows(cfg)["rows"] if r["id"] == out["ledger_id"]][0]
    assert row["kind"] == "inference"
    assert row["run_id"] == run["run_id"]
    assert row["tokens_in"] == 800


def test_a_settled_run_cannot_be_settled_twice(tmp_path, monkeypatch):
    from voicebridge.office import receipts
    from voicebridge.office.errors import OfficeError

    cfg, agent_id = _seated(tmp_path, monkeypatch)
    run = receipts.begin_run(cfg, agent_id, cap_cents=50)
    receipts.finish_run(cfg, run["run_id"], settled_cents=12)
    with pytest.raises(OfficeError) as err:
        receipts.finish_run(cfg, run["run_id"], settled_cents=12)
    assert err.value.code == "OFFICE_BAD_STATE"


# ---------------------------------------------------------------- C8: caps
def test_the_agent_turn_cap_refuses_before_anything_runs(tmp_path, monkeypatch):
    from voicebridge.office import caps, receipts
    from voicebridge.office.errors import OfficeError

    cfg, agent_id = _seated(tmp_path, monkeypatch)
    caps.set_cap(cfg, "perRun", 50, confirmed=True)

    with pytest.raises(OfficeError) as err:
        receipts.admit(cfg, agent_id=agent_id, estimated_cents=80)
    assert err.value.code == "OFFICE_ADMISSION_REFUSED"
    assert "perRun" in err.value.detail

    # Nothing was receipted, so no provider could have been called: that is
    # how "checked at admission" is proven before the runner exists.
    assert receipts.receipts(cfg)["count"] == 0


def test_a_zero_cap_means_no_cap_not_zero_headroom(tmp_path, monkeypatch):
    from voicebridge.office import caps, receipts

    cfg, agent_id = _seated(tmp_path, monkeypatch)
    # Zero every key: "no cap" is per-cap, so an untouched perDay default would
    # refuse the large estimate and the test would pass for the wrong reason.
    for key in caps.KEYS:
        caps.set_cap(cfg, key, 0, confirmed=True)

    assert caps.resolve(cfg)["perRun"] == 0
    h = caps.headroom(cfg, "perRun", 999_999)
    assert h["headroom_cents"] is None            # not a number, not zero
    assert h["meaning"] == "no cap"
    assert receipts.admit(cfg, agent_id=agent_id,
                          estimated_cents=999_999)["state"] == "OK"


def test_clearing_a_cap_reverts_to_the_default(tmp_path, monkeypatch):
    from voicebridge.office import caps

    cfg, _ = _seated(tmp_path, monkeypatch)
    caps.set_cap(cfg, "perRun", 7, confirmed=True)
    assert caps.resolve(cfg)["perRun"] == 7

    out = caps.set_cap(cfg, "perRun", None, confirmed=True)
    assert out["cleared"] is True
    assert caps.resolve(cfg)["perRun"] == caps.DEFAULTS["perRun"]


def test_a_corrupt_stored_cap_can_never_grant_headroom(tmp_path, monkeypatch):
    """StarNet's rule: a bad persisted value is 'not set', never generous."""
    from voicebridge.office import caps, store

    cfg, agent_id = _seated(tmp_path, monkeypatch)
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        for bad in ("-500", "abc", "NaN", "inf", ""):
            conn.execute("INSERT INTO office_meta(key, value) VALUES(?,?)"
                         " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                         ("cap:perRun", bad))
            conn.commit()
            # every unusable value falls back to the default, never to "no cap"
            assert caps.resolve(cfg)["perRun"] == caps.DEFAULTS["perRun"], bad
    finally:
        conn.close()

    # …and setting one through the API refuses outright.
    from voicebridge.office.errors import OfficeError
    with pytest.raises(OfficeError):
        caps.set_cap(cfg, "perRun", "abc", confirmed=True)


def test_a_broke_agent_is_refused(tmp_path, monkeypatch):
    from voicebridge.office import receipts
    from voicebridge.office.errors import OfficeError

    cfg, agent_id = _seated(tmp_path, monkeypatch, funded=100)
    from voicebridge.office import ledger
    ledger.record(cfg, "inference", 150, agent_id=agent_id)

    with pytest.raises(OfficeError) as err:
        receipts.admit(cfg, agent_id=agent_id, estimated_cents=5)
    assert err.value.code == "OFFICE_ADMISSION_REFUSED"
    assert "balance" in err.value.detail


def test_cap_set_requires_confirm_and_a_known_key(tmp_path, monkeypatch):
    from voicebridge.office import caps
    from voicebridge.office.errors import OfficeError

    cfg, _ = _seated(tmp_path, monkeypatch)
    with pytest.raises(OfficeError) as err:
        caps.set_cap(cfg, "perRun", 10)
    assert err.value.code == "OFFICE_CONFIRM_REQUIRED"

    with pytest.raises(OfficeError) as err2:
        caps.set_cap(cfg, "perHour", 10, confirmed=True)
    assert err2.value.code == "OFFICE_BAD_INPUT"


def test_inspect_reports_provenance(tmp_path, monkeypatch):
    from voicebridge.office import caps

    cfg, _ = _seated(tmp_path, monkeypatch)
    rows = {r["key"]: r for r in caps.inspect(cfg)["caps"]}
    assert rows["perRun"]["source"] == "default"
    assert rows["perRun"]["effective_cents"] == caps.DEFAULTS["perRun"]

    caps.set_cap(cfg, "perDay", 0, confirmed=True)
    rows = {r["key"]: r for r in caps.inspect(cfg)["caps"]}
    assert rows["perDay"]["source"] == "override"
    assert rows["perDay"]["meaning"] == "no cap (a real saved value)"

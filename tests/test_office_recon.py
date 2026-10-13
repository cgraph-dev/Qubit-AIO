"""M3 — reconciliation, findings, separation of duties, the spot audit.

The audit layer's whole job is to be able to *fail*: a check that cannot report
a problem proves nothing. So the central test here deliberately **drifts a view**
away from the ledger and asserts that reconciliation catches it, files a finding,
and does not repair it silently.

Hermetic: tmp XDG state only; no network, no provider call.
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


def _shop(cfg, *, live=True):
    from voicebridge.office import business as B, org as O

    ceo = O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)["id"]
    d = O.hire(cfg, "dir", seat_type="director_portfolio", supervisor_id=ceo,
               confirmed=True)["id"]
    dept = O.department_create(cfg, "shop", director_id=d,
                               kill_criteria="two quarters negative net",
                               confirmed=True)["id"]
    b = B.propose(cfg, "gadget", department_id=dept, thesis="t", offer="o",
                  price_cents=500, confirmed=True)["id"]
    B.set_terms(cfg, b, kill_criteria="no sale in 60 days",
                exposure="tax registration and 14-day refunds", confirmed=True)
    if live:
        B.advance(cfg, b, "building", confirmed=True)
        B.advance(cfg, b, "live", confirmed=True)
        B.owner_fund(cfg, b, owner_words="real money, go and sell the gadget",
                     confirmed=True)
    return {"ceo": ceo, "director": d, "department": dept, "business": b}


# --- A healthy store reconciles to zero mismatches, and says it repaired nothing
def test_a_healthy_store_reconciles_with_no_mismatches(tmp_path, monkeypatch):
    from voicebridge.office import audit as A, business as B, ledger as L

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _shop(cfg)
    L.record(cfg, "inference", 120, agent_id=ids["director"],
             business_id=ids["business"], department_id=ids["department"])
    L.record(cfg, "inference", 80, agent_id=ids["director"], unmetered=True,
             business_id=ids["business"], department_id=ids["department"])
    B.record_revenue(cfg, ids["business"], 500, source="stripe",
                     external_ref="pi_1")

    rec = A.reconcile_all(cfg)
    assert rec["healthy"] is True
    assert rec["mismatches"] == 0
    assert rec["details"] == []
    assert rec["findings_filed"] == []
    assert rec["repaired"] is False          # nothing was "fixed" silently
    assert rec["checked"] > 0                # it actually looked at something
    # the unmetered row is excluded from the meter on every side (D16)
    assert L.pnl(cfg, scope="org")["cost_cents"] == 120


# --- THE CHECK CAN FAIL: drift a view and reconciliation must catch it
def test_a_drifted_view_is_caught_filed_and_not_silently_repaired(
        tmp_path, monkeypatch):
    from voicebridge.office import audit as A, store

    cfg = _cfg(tmp_path, monkeypatch)
    _shop(cfg)

    # Simulate the exact failure the control exists for: a view that has
    # drifted away from the ledger it is derived from.
    conn = store.connect(cfg)
    try:
        conn.executescript(
            "DROP VIEW office_v_org_pnl;"
            "CREATE VIEW office_v_org_pnl AS SELECT"
            "  999999 AS revenue_cents, 0 AS cost_cents, 999999 AS net_cents,"
            "  0 AS inference_cents, 0 AS reported_net_cents,"
            "  0 AS reported_cost_cents, 0 AS entries;")
        conn.commit()
    finally:
        conn.close()

    rec = A.reconcile_all(cfg, period="2026-10")
    assert rec["healthy"] is False
    assert rec["mismatches"] >= 1
    assert any(d["field"] == "revenue_cents" and d["view"] == 999999
               for d in rec["details"])
    # the disagreement is kept as a finding...
    assert rec["findings_filed"], "a mismatch was found but nothing was filed"
    filed = A.findings(cfg, kind="reconcile")["findings"][0]
    assert filed["severity"] == "repeat"
    assert filed["evidence"]["ledger"] != filed["evidence"]["view"]
    assert "never edited by hand" in filed["evidence"]["note"]
    # ...and it was NOT repaired silently
    assert rec["repaired"] is False
    conn = store.connect(cfg)
    try:
        still = conn.execute("SELECT * FROM office_v_org_pnl").fetchone()
    finally:
        conn.close()
    assert int(still["revenue_cents"]) == 999999, "the view was rewritten"


# --- a finding is a record, and a terminate-severity one wakes the owner
def test_a_terminate_finding_notifies_the_owner_and_no_finding_edits_the_ledger(
        tmp_path, monkeypatch):
    from voicebridge.office import audit as A, ledger as L

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _shop(cfg)
    L.record(cfg, "revenue", 100, agent_id=ids["ceo"])

    minor = A.file_finding(cfg, kind="reconcile", severity="minor",
                           agent_id=ids["ceo"])
    assert "notify_owner" not in minor
    grave = A.file_finding(cfg, kind="penalty", severity="terminate",
                           agent_id=ids["director"],
                           evidence={"why": "a spend with no artifact"})
    assert grave["notify_owner"] is True

    # the ladder reads off the findings
    assert A.penalty_for(cfg, agent_id=ids["director"])["rung"] == "terminate"
    assert A.penalty_for(cfg, agent_id=ids["ceo"])["rung"] == "minor"
    # and no finding touched the book: the revenue row is still the only one
    assert L.rows(cfg)["count"] == 1


# --- the ladder: repeat findings cap the quarter, none means clean
def test_the_penalty_ladder_climbs_with_findings(tmp_path, monkeypatch):
    from voicebridge.office import audit as A

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _shop(cfg)
    assert A.penalty_for(cfg, agent_id=ids["director"])["rung"] == "clean"
    A.file_finding(cfg, kind="spot_audit", severity="minor",
                   agent_id=ids["director"])
    assert A.penalty_for(cfg, agent_id=ids["director"])["rung"] == "minor"
    A.file_finding(cfg, kind="spot_audit", severity="repeat",
                   agent_id=ids["director"])
    out = A.penalty_for(cfg, agent_id=ids["director"])
    assert out["rung"] == "repeat" and "capped at `below`" in out["consequence"]


# --- C: separation of duties is checkable because the ledger names who wrote it
def test_a_spend_with_no_run_receipt_is_a_separation_finding(tmp_path,
                                                             monkeypatch):
    from voicebridge.office import audit as A, ledger as L

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _shop(cfg)
    clean = A.check_separation(cfg)
    assert clean["violations"] == 0
    assert clean["auditor_reports_to"] == "owner"

    # an inference row with no run_id: money that bought nothing attributable
    L.record(cfg, "inference", 75, agent_id=ids["director"],
             department_id=ids["department"])
    dirty = A.check_separation(cfg)
    kinds = [d["kind"] for d in dirty["details"]]
    assert "spend_with_no_run_receipt" in kinds


# --- an auditor inside the chain is the finding the rule exists to prevent
def test_an_auditor_placed_inside_the_chain_is_flagged(tmp_path, monkeypatch):
    from voicebridge.office import audit as A, store

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _shop(cfg)
    # build a manager, then hand-place an auditor under it (bypassing the
    # purpose-built constructor, which reports to the owner and takes no
    # supervisor)
    from voicebridge.office import org as O
    mgr = O.hire(cfg, "mgr", seat_type="manager", supervisor_id=ids["director"],
                 department_id=ids["department"], confirmed=True)["id"]
    conn = store.connect(cfg)
    try:
        conn.execute(
            "INSERT INTO office_agent(slug, name, seat_type, supervisor_id,"
            " status, autonomy, created_at, created_by)"
            " VALUES('bad-auditor','bad-auditor','auditor',?, 'active',"
            " 'unattended_full', 0, 'test')", (mgr,))
        conn.commit()
    finally:
        conn.close()
    kinds = [d["kind"] for d in A.check_separation(cfg)["details"]]
    assert "auditor_inside_the_chain" in kinds


# --- and the auditor seat itself reports to the owner, not into the chain
def test_the_auditor_reports_to_the_owner_not_the_chain(tmp_path, monkeypatch):
    from voicebridge.office import audit as A, org as O
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    _shop(cfg)
    out = A.auditor_seat(cfg, slug="auditor", auditor_of="all",
                         confirmed=True)
    assert out["reports_to"] == "owner" and out["supervisor_id"] is None
    assert A.check_separation(cfg)["violations"] == 0

    # one control that never fires is not two controls
    with pytest.raises(OfficeError) as exc:
        A.auditor_seat(cfg, slug="auditor-2", confirmed=True)
    assert "already an auditor" in exc.value.detail

    # and the ordinary hire path cannot build one: it requires a supervisor,
    # which would put the auditor inside the chain (C30 vs 17-AUDIT.md)
    with pytest.raises(OfficeError) as exc:
        O.hire(cfg, "second-auditor", seat_type="auditor", confirmed=True)
    assert exc.value.code == "OFFICE_CHAIN"


# --- B: the spot audit samples by seed, and answers all three questions
def test_the_spot_audit_samples_by_seed_and_answers_three_questions(
        tmp_path, monkeypatch):
    from voicebridge.office import audit as A, ledger as L

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _shop(cfg)
    L.record(cfg, "fees", 45, agent_id=ids["director"],
             business_id=ids["business"], department_id=ids["department"])
    from voicebridge.office import business as B
    B.record_revenue(cfg, ids["business"], 500, source="stripe",
                     external_ref="pi_1")

    out = A.spot_audit(cfg, period="2026-10", seed=7)
    assert out["sampled"] == ids["business"]
    assert out["q2_ledger_rows"] == 2
    assert out["q3_survives_fee_and_refund_pass"] is True
    assert "not chosen by anyone" in out["selection"]
    # the same period samples the same business, so an agent cannot shop around
    again = A.spot_audit(cfg, period="2026-10", seed=7)
    assert again["sampled"] == out["sampled"]
    # and the audit left a finding behind
    assert A.findings(cfg, kind="spot_audit")["count"] == 2


# --- the P&L survives a refund pass, which is the number agents leave out
def test_the_refund_pass_catches_gross_net_confusion(tmp_path, monkeypatch):
    from voicebridge.office import audit as A, business as B, ledger as L

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _shop(cfg)
    B.record_revenue(cfg, ids["business"], 1000, source="stripe",
                     external_ref="pi_sale")
    # a refund booked as a negative adjustment, the way a real processor sends it
    L.record(cfg, "adjustment", -400, agent_id=ids["director"],
             business_id=ids["business"], department_id=ids["department"],
             note="refund pi_sale")

    out = A.spot_audit(cfg, period="2026-11", seed=1)
    assert out["q3_survives_fee_and_refund_pass"] is True
    # the view agrees: revenue 1000, and the refund already sits inside net
    assert B.pnl(cfg, ids["business"])["net_cents"] == 600


# --- q3 asks about the REVENUE-side net, not the whole net: a control that
# --- always fires is a control nobody reads
def test_q3_does_not_cry_wolf_on_a_healthy_business(tmp_path, monkeypatch):
    """The cost of thinking is not part of "did the revenue survive fees"."""
    from voicebridge.office import audit as A, business as B, ledger as L

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _shop(cfg)
    L.record(cfg, "inference", 500, agent_id=ids["director"],
             business_id=ids["business"], department_id=ids["department"],
             run_id="run_1")
    B.record_revenue(cfg, ids["business"], 100, source="stripe",
                     external_ref="pi_1")

    p = B.pnl(cfg, ids["business"])
    assert p["net_cents"] == -400            # the whole net is negative...
    assert p["revenue_net_cents"] == 100     # ...but the revenue survived
    out = A.spot_audit(cfg, period="2026-12", seed=3)
    assert out["q3_survives_fee_and_refund_pass"] is True
    assert out["q3_refund_rate_pct"] == 0.0


# --- and the refund rate is reported, which is the number agents leave out
def test_q3_reports_the_refund_rate(tmp_path, monkeypatch):
    from voicebridge.office import audit as A, business as B, ledger as L

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _shop(cfg)
    B.record_revenue(cfg, ids["business"], 1000, source="stripe",
                     external_ref="pi_a")
    L.record(cfg, "adjustment", -250, agent_id=ids["director"],
             business_id=ids["business"], department_id=ids["department"],
             note="refund pi_a")

    out = A.spot_audit(cfg, period="2027-01", seed=2)
    assert out["q3_survives_fee_and_refund_pass"] is True
    assert out["q3_refund_rate_pct"] == 25.0
    assert B.pnl(cfg, ids["business"])["revenue_net_cents"] == 750


# --- detection without a record is not a control
def test_a_separation_violation_is_filed_not_just_printed(tmp_path, monkeypatch):
    from voicebridge.office import audit as A, ledger as L

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _shop(cfg)
    L.record(cfg, "inference", 75, agent_id=ids["director"],
             department_id=ids["department"])   # no run_id

    first = A.check_separation(cfg)
    assert first["violations"] == 1
    assert len(first["findings_filed"]) == 1, "the violation was not filed"
    filed_id = first["findings_filed"][0]

    # and it is not re-filed on every look — a control that spams is ignored
    assert A.check_separation(cfg)["findings_filed"] == []
    assert A.check_separation(cfg)["findings_filed"] == []
    assert A.findings(cfg, kind="separation")["count"] == 1


# --- a finding is closed, never erased: the history it rests on stays
def test_closing_a_finding_erases_nothing(tmp_path, monkeypatch):
    from voicebridge.office import audit as A, ledger as L

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _shop(cfg)
    L.record(cfg, "inference", 75, agent_id=ids["director"],
             department_id=ids["department"])
    filed = A.check_separation(cfg)["findings_filed"][0]

    out = A.close_finding(cfg, filed, note="rebooked against run_7",
                          confirmed=True)
    assert out["status"] == "closed" and out["erases_nothing"] is True

    # the ledger row that caused it is still there, and the violation is still
    # true — the finding was answered, not the history rewritten
    assert L.rows(cfg)["count"] == 1
    assert A.check_separation(cfg)["violations"] == 1
    rows = A.findings(cfg, kind="separation")["findings"]
    closed_row = [r for r in rows if r["id"] == filed][0]
    assert closed_row["status"] == "closed"
    assert closed_row["evidence"]["closing_note"] == "rebooked against run_7"
    assert closed_row["evidence"]["violation"]["kind"] == "spend_with_no_run_receipt"

    # ...and because the violation is STILL true, the control files a fresh one.
    # That is the point: a finding closed without actually fixing anything does
    # not make the problem go away, it makes it come back.
    assert len(rows) == 2, "a still-true violation should be re-filed after a close"
    assert [r for r in rows if r["status"] != "closed"], "the new finding is open"

    # closing is idempotent, and needs a confirmation like every mutation
    assert A.close_finding(cfg, filed, confirmed=True)["already"] is True
    from voicebridge.office import store
    from voicebridge.office.errors import OfficeError
    with pytest.raises(OfficeError) as exc:
        A.close_finding(cfg, filed)
    assert exc.value.code == store.CONFIRM_CODE


# --- no live business means nothing to sample, and that is said plainly
def test_the_spot_audit_says_so_when_there_is_nothing_to_sample(
        tmp_path, monkeypatch):
    from voicebridge.office import audit as A

    cfg = _cfg(tmp_path, monkeypatch)
    _shop(cfg, live=False)
    out = A.spot_audit(cfg, period="2026-10")
    assert out["sampled"] is None
    assert "no live business" in out["reason"]

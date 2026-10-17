"""M2 — escalation through the **shared** journal (D34/C31).

The milestone's proof: an escalation raised by a seat appears on the existing
approvals surface with a real `confirmation_id`, is approved there, and the
office sees it — **one journal, one settle path**. These tests assert that
against the real `ApprovalJournal`, not a stand-in, because a second queue that
merely looks like the first is exactly the failure the rule exists to prevent.

Hermetic: tmp XDG state only; no network, no provider call.
"""
from __future__ import annotations

from pathlib import Path

import pytest


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _chain(cfg):
    """CEO → director → department → manager → worker."""
    from voicebridge.office import org as O

    ceo = O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)["id"]
    d = O.hire(cfg, "dir", seat_type="director_portfolio", supervisor_id=ceo,
               confirmed=True)["id"]
    dept = O.department_create(cfg, "shop", director_id=d,
                               kill_criteria="two quarters negative net",
                               confirmed=True)["id"]
    mgr = O.hire(cfg, "mgr", seat_type="manager", supervisor_id=d,
                 department_id=dept, confirmed=True)["id"]
    wk = O.hire(cfg, "wk", seat_type="worker", supervisor_id=mgr,
                department_id=dept, confirmed=True)["id"]
    return {"ceo": ceo, "director": d, "department": dept, "manager": mgr,
            "worker": wk}


# --- the escalation lands on the SAME journal the dashboard/voice already read
def test_an_escalation_mints_a_real_id_on_the_shared_journal(tmp_path,
                                                             monkeypatch):
    from voicebridge.approvals import ApprovalJournal
    from voicebridge.office import escalation as E

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _chain(cfg)
    out = E.raise_to_supervisor(cfg, agent_id=ids["worker"], kind="blocked",
                                summary="the payment account is not mine to open",
                                payload={"needs": "owner"})
    assert out["confirmation_id"], "no confirmation_id was minted"
    assert out["supervisor_id"] == ids["manager"]   # found via supervisor_id

    # the *existing* journal — the one the dashboard reads — has it
    journal = ApprovalJournal(Path(cfg.state_dir()) / "approvals.jsonl")
    pending = journal.pending()
    assert any(a.id == out["confirmation_id"] for a in pending)
    item = journal.get(out["confirmation_id"])
    assert item.capability.startswith("office.escalation")
    assert item.meta["agent_slug"] == "wk"
    assert item.meta["supervisor_id"] == ids["manager"]


# --- one level up, exactly: a peer cannot settle it
def test_only_the_supervisor_may_settle_an_escalation(tmp_path, monkeypatch):
    from voicebridge.office import escalation as E
    from voicebridge.office import org as O
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _chain(cfg)
    out = E.raise_to_supervisor(cfg, agent_id=ids["worker"], kind="blocked",
                                summary="stuck")
    peer = O.hire(cfg, "wk-2", seat_type="worker", supervisor_id=ids["manager"],
                  department_id=ids["department"], confirmed=True)["id"]

    with pytest.raises(OfficeError) as exc:
        E.resolve(cfg, out["id"], "approve", by_agent=peer, confirmed=True)
    assert exc.value.code == "OFFICE_ESCALATION_WRONG_LEVEL"

    settled = E.resolve(cfg, out["id"], "approve", by_agent=ids["manager"],
                        confirmed=True)
    assert settled["status"] == "resolved"
    # and the shared journal agrees, so the dashboard shows the same outcome
    journal = E.journal_for(cfg)
    assert journal.get(out["confirmation_id"]).decision == "approve"


# --- settling on the SHARED surface closes it here too (one queue, not two)
def test_a_decision_made_on_the_shared_surface_closes_the_office_row(
        tmp_path, monkeypatch):
    from voicebridge.office import escalation as E

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _chain(cfg)
    out = E.raise_to_supervisor(cfg, agent_id=ids["worker"], kind="blocked",
                                summary="needs a decision")
    assert E.escalations(cfg, status="open")["count"] == 1

    # the owner approves it in the dashboard / voice surface — not through here
    E.journal_for(cfg).respond(out["confirmation_id"], "approve")
    E.journal_for(cfg).finish(out["confirmation_id"])

    synced = E.sync(cfg)
    assert synced["synced"] == 1
    assert E.escalations(cfg, status="open")["count"] == 0
    row = E.escalations(cfg)["escalations"][0]
    assert row["status"] == "resolved"


# --- a denial is a decision, and it is recorded as one (not a silence)
def test_a_denial_on_the_shared_surface_abandons_rather_than_resolves(
        tmp_path, monkeypatch):
    from voicebridge.office import escalation as E

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _chain(cfg)
    out = E.raise_to_supervisor(cfg, agent_id=ids["worker"], kind="envelope",
                                summary="more money")
    E.journal_for(cfg).respond(out["confirmation_id"], "deny")
    E.sync(cfg)
    row = E.escalations(cfg)["escalations"][0]
    assert row["status"] == "abandoned"       # decided, but not granted


# --- nobody owns the CEO's escalation, and that must be said plainly
def test_the_ceo_cannot_escalate_because_the_owner_is_not_an_agent(
        tmp_path, monkeypatch):
    from voicebridge.office import escalation as E
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _chain(cfg)
    with pytest.raises(OfficeError) as exc:
        E.raise_to_supervisor(cfg, agent_id=ids["ceo"], kind="blocked",
                              summary="needs the owner")
    assert exc.value.code == "OFFICE_BAD_STATE"
    assert "report" in exc.value.detail


# --- an escalation with no supervisor is a hole in the chain, not a queue item
def test_a_seat_with_no_supervisor_cannot_escalate(tmp_path, monkeypatch):
    """The hire path already refuses a supervisor-less seat (C30), so this
    forces the state directly: the guard must hold for a row that a migration
    or a hand edit could produce, not only for rows `hire` builds."""
    from voicebridge.office import escalation as E, org as O, store
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _chain(cfg)
    conn = store.connect(cfg)
    try:
        conn.execute("UPDATE office_agent SET supervisor_id=NULL WHERE id=?",
                     (ids["worker"],))
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(OfficeError) as exc:
        E.raise_to_supervisor(cfg, agent_id=ids["worker"], kind="blocked",
                              summary="hi")
    assert exc.value.code == "OFFICE_BAD_STATE"
    assert "chain of command" in exc.value.detail


# --- and hire itself refuses to create such a seat in the first place
def test_hire_refuses_a_seat_nobody_owns(tmp_path, monkeypatch):
    from voicebridge.office import org as O
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)
    with pytest.raises(OfficeError) as exc:
        O.hire(cfg, "orphan", seat_type="worker", confirmed=True)
    assert exc.value.code == "OFFICE_CHAIN"


# --- an escalation with no summary is not a message anyone can act on
def test_an_escalation_needs_a_summary(tmp_path, monkeypatch):
    from voicebridge.office import escalation as E
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _chain(cfg)
    with pytest.raises(OfficeError) as exc:
        E.raise_to_supervisor(cfg, agent_id=ids["worker"], kind="blocked",
                              summary="   ")
    assert exc.value.code == "OFFICE_BAD_INPUT"


# --- an unknown kind is refused rather than stored as free text
def test_an_unknown_escalation_kind_is_refused(tmp_path, monkeypatch):
    from voicebridge.office import escalation as E
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _chain(cfg)
    with pytest.raises(OfficeError) as exc:
        E.raise_to_supervisor(cfg, agent_id=ids["worker"], kind="whatever",
                              summary="x")
    assert exc.value.code == "OFFICE_BAD_INPUT"


# --- settling twice is refused, so a decision cannot be re-litigated
def test_an_already_settled_escalation_cannot_be_settled_again(
        tmp_path, monkeypatch):
    from voicebridge.office import escalation as E
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _chain(cfg)
    out = E.raise_to_supervisor(cfg, agent_id=ids["worker"], kind="blocked",
                                summary="x")
    E.resolve(cfg, out["id"], "deny", by_agent=ids["manager"], confirmed=True)
    with pytest.raises(OfficeError) as exc:
        E.resolve(cfg, out["id"], "approve", by_agent=ids["manager"],
                  confirmed=True)
    assert exc.value.code == "OFFICE_BAD_STATE"


# --- owner dashboard path: banner Confirm/Cancel settle office rows
def test_owner_can_approve_via_dashboard_confirm_op(tmp_path, monkeypatch):
    from voicebridge import dashboard_ops as OPS
    from voicebridge.office import escalation as E

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _chain(cfg)
    out = E.raise_to_supervisor(cfg, agent_id=ids["worker"], kind="blocked",
                                summary="stuck")
    res = OPS.confirm_op(cfg, out["confirmation_id"],
                         actor="dashboard:user", session="dashboard")
    assert res["state"] == "executed", res
    assert res["receipt"]["decided_by"] == 0
    row = E.escalations(cfg)["escalations"][0]
    assert row["status"] == "resolved"
    # journal agrees: banner no longer lists it
    journal = E.journal_for(cfg)
    assert out["confirmation_id"] not in {a.id for a in journal.pending()}


def test_owner_can_deny_via_dashboard_cancel_op(tmp_path, monkeypatch):
    from voicebridge import dashboard_ops as OPS
    from voicebridge.office import escalation as E

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _chain(cfg)
    out = E.raise_to_supervisor(cfg, agent_id=ids["worker"], kind="blocked",
                                summary="stuck")
    res = OPS.cancel_op(cfg, out["confirmation_id"],
                        actor="dashboard:user", session="dashboard")
    assert res["state"] == "executed", res
    row = E.escalations(cfg)["escalations"][0]
    assert row["status"] == "resolved"
    assert row["decided_by"] == 0


def test_non_owner_dashboard_actor_is_refused(tmp_path, monkeypatch):
    from voicebridge import dashboard_ops as OPS
    from voicebridge.office import escalation as E

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _chain(cfg)
    out = E.raise_to_supervisor(cfg, agent_id=ids["worker"], kind="blocked",
                                summary="stuck")
    res = OPS.confirm_op(cfg, out["confirmation_id"],
                         actor="voice:user", session="voice")
    assert res["state"] == "failed", res
    assert res["code"] == "OFFICE_ESCALATION_OWNER_ONLY"
    # still open, still pending
    assert E.escalations(cfg, status="open")["count"] == 1


def test_supervisor_seat_path_unchanged(tmp_path, monkeypatch):
    from voicebridge.office import escalation as E
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _chain(cfg)
    out = E.raise_to_supervisor(cfg, agent_id=ids["worker"], kind="blocked",
                                summary="stuck")
    # D34 intact: wrong seat still refused without owner=True
    with pytest.raises(OfficeError) as exc:
        E.resolve(cfg, out["id"], "approve", by_agent=ids["worker"],
                  confirmed=True)
    assert exc.value.code == "OFFICE_ESCALATION_WRONG_LEVEL"

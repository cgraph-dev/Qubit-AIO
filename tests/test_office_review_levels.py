"""M2 — review levels (C31, second half).

The contract's proof bullets 4 and 5: a seat cannot review itself, and a peer
cannot rate a peer. Both are refusals, and both exist because the alternative —
mutual back-scratching between peers — produces a clean-looking org chart with
nobody actually rated.

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


def _chain(cfg):
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


# 4 — a seat cannot review itself
def test_a_seat_cannot_review_itself(tmp_path, monkeypatch):
    from voicebridge.office import review as RV
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _chain(cfg)
    with pytest.raises(OfficeError) as exc:
        RV.file_review(cfg, subject_agent_id=ids["worker"],
                       reviewer_agent_id=ids["worker"], verdict="exceeds",
                       confirmed=True)
    assert exc.value.code == "OFFICE_SELF_REVIEW"
    assert RV.reviews(cfg)["count"] == 0


# 5 — a peer cannot rate a peer: the level must be exactly one above
def test_a_peer_cannot_rate_a_peer(tmp_path, monkeypatch):
    from voicebridge.office import org as O, review as RV
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _chain(cfg)
    peer = O.hire(cfg, "wk-2", seat_type="worker", supervisor_id=ids["manager"],
                  department_id=ids["department"], confirmed=True)["id"]
    with pytest.raises(OfficeError) as exc:
        RV.file_review(cfg, subject_agent_id=ids["worker"],
                       reviewer_agent_id=peer, verdict="exceeds",
                       confirmed=True)
    assert exc.value.code == "OFFICE_REVIEW_WRONG_LEVEL"
    assert "a trade, not a review" in exc.value.detail


# --- and a level-skipping review is refused too (the director is two above)
def test_a_review_cannot_skip_a_level(tmp_path, monkeypatch):
    from voicebridge.office import review as RV
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _chain(cfg)
    with pytest.raises(OfficeError) as exc:
        RV.file_review(cfg, subject_agent_id=ids["worker"],
                       reviewer_agent_id=ids["director"], verdict="meets",
                       confirmed=True)
    assert exc.value.code == "OFFICE_REVIEW_WRONG_LEVEL"


# --- the supervisor may, and the review is recorded
def test_the_supervisor_may_review_and_it_is_recorded(tmp_path, monkeypatch):
    from voicebridge.office import review as RV

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _chain(cfg)
    out = RV.file_review(cfg, subject_agent_id=ids["worker"],
                         reviewer_agent_id=ids["manager"], verdict="below",
                         kind="weekly", period="2026-W41",
                         body="shipped late twice; both had a cause", confirmed=True)
    assert out["subject_slug"] == "wk" and out["reviewer_slug"] == "mgr"
    assert out["blocks_work"] is False      # D22: the org does not stop

    row = RV.reviews(cfg)["reviews"][0]
    assert row["verdict"] == "below" and row["period"] == "2026-W41"


# --- the CEO has no level above it, so nobody can rate it
def test_nobody_can_review_the_ceo(tmp_path, monkeypatch):
    from voicebridge.office import org as O, review as RV
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _chain(cfg)
    d2 = O.hire(cfg, "dir2", seat_type="director_portfolio",
                supervisor_id=ids["ceo"], confirmed=True)["id"]
    with pytest.raises(OfficeError) as exc:
        RV.file_review(cfg, subject_agent_id=ids["ceo"],
                       reviewer_agent_id=d2, verdict="meets", confirmed=True)
    assert exc.value.code == "OFFICE_REVIEW_WRONG_LEVEL"
    assert "no supervisor" in exc.value.detail


# --- a verdict is from the closed set, not free text
def test_an_unknown_verdict_is_refused(tmp_path, monkeypatch):
    from voicebridge.office import review as RV
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _chain(cfg)
    with pytest.raises(OfficeError) as exc:
        RV.file_review(cfg, subject_agent_id=ids["worker"],
                       reviewer_agent_id=ids["manager"], verdict="great job!",
                       confirmed=True)
    assert exc.value.code == "OFFICE_BAD_INPUT"


# --- and a review needs a confirmation like every other mutation
def test_a_review_needs_a_confirmation(tmp_path, monkeypatch):
    from voicebridge.office import review as RV, store
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _chain(cfg)
    with pytest.raises(OfficeError) as exc:
        RV.file_review(cfg, subject_agent_id=ids["worker"],
                       reviewer_agent_id=ids["manager"], verdict="meets")
    assert exc.value.code == store.CONFIRM_CODE

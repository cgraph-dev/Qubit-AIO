"""The dispatch/adjudicate *preview* is read-only — and needs no confirmation.

Two defects this pins:

1. `dispatch --dry-run` and `adjudicate --dry-run` raised CONFIRM_REQUIRED, so
   the only way to see what the pass would do was to arm it for real. The
   office CLI's own convention is "mutating ops preview under --dry-run, require
   --confirm", and a preview writes nothing, so demanding confirmation for it
   inverts the point of the flag.
2. `adjudicate --dry-run` spent a real turn from the reviewer's daily budget
   before it took the preview branch. A preview of the review queue therefore
   charged every reviewer it looked at — and turns are the scarcest thing a
   seat has (a default cap of 2/day). The work pass already refused this
   ("a preview that consumed turns would make the preview the most expensive
   way to find out what a run would do"); the review pass did not.

Hermetic: tmp XDG state only; no network, no provider call, executor stubbed.
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


def _org(cfg, *, workers=1, reviewers=1):
    from voicebridge.office import org as O

    ceo = O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)["id"]
    revs, wks = [], []
    for i in range(reviewers):
        revs.append(O.hire(cfg, f"lead-{i}", seat_type="manager",
                           supervisor_id=ceo, confirmed=True)["id"])
    for i in range(workers):
        wks.append(O.hire(cfg, f"wk-{i}", seat_type="worker",
                          supervisor_id=revs[i % len(revs)], confirmed=True)["id"])
    return {"ceo": ceo, "reviewers": revs, "workers": wks}


def _pay(cfg, agent_id, cents=100_000):
    from voicebridge.office import ledger
    ledger.record(cfg, "revenue", cents, agent_id=agent_id)


# ------------------------------------------------------------------ dispatch
def test_a_dispatch_preview_needs_no_confirmation_and_writes_nothing(
        tmp_path, monkeypatch):
    from voicebridge.office import dispatch as D, work as W

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg, workers=1)
    wk = ids["workers"][0]
    _pay(cfg, wk)
    W.set_turn_cap(cfg, agent_id=wk, turns=2, confirmed=True)
    W.create(cfg, "t", exit_check="true", confirmed=True)

    calls = []

    def stub(cfg_, agent_id, task_text):
        calls.append(agent_id)
        return {"state": "OK", "reply": "x", "receipt": {"usage": {}}}

    out = D.dispatch(cfg, rounds=1, executor=stub, dry_run=True,
                     confirmed=False)
    assert out["state"] == "OK"
    assert [r["state"] for r in out["results"]] == ["preview"]
    assert calls == [], "a preview reached the executor"
    assert W.tasks(cfg)["tasks"][0]["status"] == "open", \
        "a preview moved a task"
    assert W.turn_budget(cfg, agent_id=wk)["turns_used"] == 0, \
        "a preview consumed a turn"


def test_a_real_dispatch_still_demands_confirmation(tmp_path, monkeypatch):
    from voicebridge.office import dispatch as D, work as W
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg, workers=1)
    _pay(cfg, ids["workers"][0])
    W.create(cfg, "t", exit_check="true", confirmed=True)

    with pytest.raises(OfficeError) as exc:
        D.dispatch(cfg, rounds=1, dry_run=False, confirmed=False)
    assert exc.value.code == "OFFICE_CONFIRM_REQUIRED"


# --------------------------------------------------------------- adjudicate
def _task_in_review(cfg, ids, tmp_path):
    """A task parked in `review` by the real loop, so adjudication has work.

    Driven through `dispatch` with a stubbed executor rather than by writing
    `status='review'` directly: the state this test cares about should be one
    the loop actually produces.
    """
    from voicebridge.office import dispatch as D, work as W

    wk, rev = ids["workers"][0], ids["reviewers"][0]
    _pay(cfg, wk)
    _pay(cfg, rev)
    artifact = tmp_path / "artifact.txt"
    task = W.create(cfg, "t", exit_check=f"test -f {artifact}",
                    confirmed=True)["id"]

    def stub(cfg_, agent_id, task_text):
        artifact.write_text("done")
        # A priced, identified receipt: a turn with no model is UNKNOWN_SPEND
        # by the runner's own rule, which is not the state under test here.
        return {"state": "OK", "reply": "done",
                "receipt": {"model": "stub", "identity": "stub/model",
                            "usage": {"prompt_tokens": 10,
                                      "completion_tokens": 5,
                                      "cost_usd_est": 0.0}}}

    out = D.dispatch(cfg, rounds=1, executor=stub, confirmed=True)
    assert out["ran"] == 1, out
    return task


def test_an_adjudicate_preview_needs_no_confirmation_and_spends_no_turn(
        tmp_path, monkeypatch):
    from voicebridge.office import dispatch as D, work as W

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg, workers=1)
    rev = ids["reviewers"][0]
    W.set_turn_cap(cfg, agent_id=rev, turns=2, confirmed=True)
    task = _task_in_review(cfg, ids, tmp_path)
    assert W.tasks(cfg, status="review")["tasks"], "no task reached review"
    before = W.turn_budget(cfg, agent_id=rev)["turns_used"]

    calls = []

    def stub(cfg_, agent_id, task_text):
        calls.append(agent_id)
        return {"state": "OK", "reply": "VERDICT: accept\nNOTE: fine",
                "receipt": {"usage": {}}}

    out = D.adjudicate(cfg, rounds=1, executor=stub, dry_run=True,
                       confirmed=False)
    assert out["state"] == "OK"
    assert [r["state"] for r in out["results"]] == ["preview"]
    assert calls == [], "a preview reached the executor"
    assert W.turn_budget(cfg, agent_id=rev)["turns_used"] == before, \
        "a preview consumed the reviewer's turn"
    assert [t["id"] for t in W.tasks(cfg, status="review")["tasks"]] == [task], \
        "a preview moved a task out of review"


def test_a_real_adjudicate_still_demands_confirmation(tmp_path, monkeypatch):
    from voicebridge.office import dispatch as D, work as W
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg, workers=1)
    W.set_turn_cap(cfg, agent_id=ids["reviewers"][0], turns=2, confirmed=True)
    _task_in_review(cfg, ids, tmp_path)

    with pytest.raises(OfficeError) as exc:
        D.adjudicate(cfg, rounds=1, dry_run=False, confirmed=False)
    assert exc.value.code == "OFFICE_CONFIRM_REQUIRED"

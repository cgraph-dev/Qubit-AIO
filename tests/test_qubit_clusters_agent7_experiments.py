"""Agent 7 — controlled-evaluation (experiments) cluster, real-module proofs.

One focused test per Qubit gap-ledger row in the ``experiments`` area, driving
the real ``voicebridge.experiments`` store end-to-end (state transitions,
persistence, cancellation/abort, recovery via snapshot/restore, access control,
redaction, journal integrity) rather than a routed shape.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from voicebridge import experiments as E


def _estore(tmp_path) -> E.ExperimentStore:
    return E.ExperimentStore(tmp_path / "experiments.db")


def _pair(es, *, role_a="champion", role_b="challenger",
          label_a="base", label_b="tuned"):
    a = es.register_candidate(role=role_a, label=label_a, model="local",
                              description="p1")
    b = es.register_candidate(role=role_b, label=label_b, model="local",
                              description="p2")
    return a, b


def _with_data(es, n=25, champ=0.2, chal=0.9):
    import random

    a, b = _pair(es)
    exp = es.start_experiment(champion_config_id=a.id,
                              challenger_config_id=b.id)
    rng = random.Random(3)
    for _ in range(n):
        for var, base in ((E.VAR_CHAMPION, champ), (E.VAR_CHALLENGER, chal)):
            t = es.start_trial(exp.id, variant=var)
            es.finish_trial(t.id, outcome="completed")
            v = max(0.0, min(1.0, base + rng.uniform(-0.05, 0.05)))
            es.record_signal(t.id, kind="explicit",
                             source="user_explicit_good", value=v)
    return a, b, exp


# --------------------------------------------------------------- experiments_register
def test_experiments_register_persists_and_rejects_duplicates(tmp_path):
    es = _estore(tmp_path)
    try:
        a, b = _pair(es)
        assert a.fingerprint and a.fingerprint != b.fingerprint
        assert [c.id for c in es.candidates()] == [a.id, b.id]
        # Identical candidates are rejected at experiment open (same fingerprint).
        c = es.register_candidate(role="champion", label="base", model="local",
                                  description="p1")
        with pytest.raises(E.ExperimentError):
            es.start_experiment(champion_config_id=a.id,
                                challenger_config_id=c.id)
        with pytest.raises(E.ExperimentError):
            es.register_candidate(role="tool", label="x", model="m",
                                  description="d")
        with pytest.raises(E.SecretRejectedError):
            es.register_candidate(role="champion", label="x", model="m",
                                  description="token=sk-ABC123456789012345")
    finally:
        es.close()


# ------------------------------------------------------------------ experiments_start
def test_experiments_start_state_transition_and_persistence(tmp_path):
    es = _estore(tmp_path)
    try:
        a, b = _pair(es)
        exp = es.start_experiment(champion_config_id=a.id,
                                  challenger_config_id=b.id)
        assert exp.status == E.ST_RUNNING and exp.decided_at is None
        with pytest.raises(E.ExperimentError):
            es.start_experiment(champion_config_id=a.id,
                                challenger_config_id=b.id)
        with pytest.raises(E.ExperimentError):
            es.start_experiment(champion_config_id="cfg_missing",
                                challenger_config_id=b.id)
    finally:
        es.close()
    # Persistence across a real reopen.
    es2 = E.ExperimentStore(tmp_path / "experiments.db")
    try:
        assert [e.status for e in es2.experiments()] == [E.ST_RUNNING]
    finally:
        es2.close()


# ----------------------------------------------------------------- experiments_trial
def test_experiments_trial_running_invariant(tmp_path):
    es = _estore(tmp_path)
    try:
        a, b = _pair(es)
        exp = es.start_experiment(champion_config_id=a.id,
                                  challenger_config_id=b.id)
        t = es.start_trial(exp.id, variant=E.VAR_CHALLENGER)
        assert t.variant == E.VAR_CHALLENGER and t.config_id == b.id
        assert t.ended_at is None and t.outcome == "completed"
        with pytest.raises(E.ExperimentError):
            es.start_trial(exp.id, variant="sideways")
        with pytest.raises(E.ExperimentError):
            es.start_trial("exp_missing", variant=E.VAR_CHAMPION)
        es.abort(exp.id, requested_by="user")
        with pytest.raises(E.ExperimentError):
            es.start_trial(exp.id, variant=E.VAR_CHAMPION)
    finally:
        es.close()


# ---------------------------------------------------------------- experiments_finish
def test_experiments_finish_trial_once_and_typed(tmp_path):
    es = _estore(tmp_path)
    try:
        a, b = _pair(es)
        exp = es.start_experiment(champion_config_id=a.id,
                                  challenger_config_id=b.id)
        t = es.start_trial(exp.id, variant=E.VAR_CHAMPION)
        done = es.finish_trial(t.id, outcome="failed", note="crashed")
        assert done.ended_at is not None and done.outcome == "failed"
        assert done.note == "crashed"
        with pytest.raises(E.ExperimentError):
            es.finish_trial(t.id, outcome="completed")
        with pytest.raises(E.ExperimentError):
            es.finish_trial("trial_missing", outcome="completed")
        with pytest.raises(E.ExperimentError):
            es.finish_trial(t.id, outcome="exploded")
    finally:
        es.close()


# ---------------------------------------------------------------- experiments_signal
def test_experiments_signal_bounds_and_completion_gate(tmp_path):
    es = _estore(tmp_path)
    try:
        a, b = _pair(es)
        exp = es.start_experiment(champion_config_id=a.id,
                                  challenger_config_id=b.id)
        t = es.start_trial(exp.id, variant=E.VAR_CHAMPION)
        # An unfinished trial may not carry outcome signals.
        with pytest.raises(E.ExperimentError):
            es.record_signal(t.id, kind="explicit", source="user", value=0.5)
        es.finish_trial(t.id, outcome="completed")
        s = es.record_signal(t.id, kind="explicit", source="user", value=0.25)
        assert s.value == 0.25 and es.signals_for_trial(t.id) == [s]
        with pytest.raises(E.ExperimentError):
            es.record_signal(t.id, kind="explicit", source="user", value=1.5)
        with pytest.raises(E.ExperimentError):
            es.record_signal(t.id, kind="other", source="user", value=0.5)
        with pytest.raises(E.ExperimentError):
            es.record_signal("trial_missing", kind="implicit", source="user",
                             value=0.5)
    finally:
        es.close()


# -------------------------------------------------------------- experiments_evaluate
def test_experiments_evaluate_need_more_data_and_seeded_decision(tmp_path):
    es = _estore(tmp_path)
    try:
        a, b = _pair(es)
        exp = es.start_experiment(champion_config_id=a.id,
                                  challenger_config_id=b.id)
        thin = es.evaluate(exp.id, mc_samples=200, seed=1)
        assert thin["decision"] == E.DEC_NEED_MORE_DATA
        assert thin["sessions_each"] < thin["required"]
        assert thin["promotable"] is False
    finally:
        es.close()

    es2 = _estore(tmp_path)
    try:
        _, _, exp = _with_data(es2, n=25, champ=0.0, chal=1.0)
        one = es2.evaluate(exp.id, mc_samples=2000, seed=7)
        two = es2.evaluate(exp.id, mc_samples=2000, seed=7)
        assert one["decision"] == E.DEC_PROMOTE
        assert one["posterior"] == two["posterior"], "seeded decision is stable"
        assert one["promotable"] is False, "no owner approval or gate yet"
    finally:
        es2.close()


def test_experiments_evaluate_ignores_live_trials(tmp_path):
    es = _estore(tmp_path)
    try:
        a, b, exp = _with_data(es, n=3)
        before = es.scores_for_config(exp.id, a.id)
        # A live (unfinished) trial must not be scored as a neutral session.
        es.start_trial(exp.id, variant=E.VAR_CHAMPION)
        assert es.scores_for_config(exp.id, a.id) == before
    finally:
        es.close()


# --------------------------------------------------------------- experiments_promote
def test_experiments_promote_owner_and_gate_required(tmp_path):
    es = _estore(tmp_path)
    try:
        _, _, exp = _with_data(es, n=25, champ=0.0, chal=1.0)
        assert es.record_promotion(
            exp.id, owner_approved=True, gate_report="gate:ok",
            requested_by="model")["state"] == "denied"
        assert es.record_promotion(
            exp.id, owner_approved=False, gate_report="gate:ok",
            requested_by="user")["state"] == "hold"
        assert es.record_promotion(
            exp.id, owner_approved=True, gate_report="",
            requested_by="user")["state"] == "hold"
        out = es.record_promotion(
            exp.id, owner_approved=True,
            gate_report="regression:pass security:pass", requested_by="user")
        assert out["state"] == "OK"
        stored = es.experiment(exp.id)
        assert stored.status == E.ST_PROMOTED and stored.owner_approved is True
        # Promotion is a decision record only; it never applies to production.
        assert "manual" in out["detail"]
    finally:
        es.close()


# ------------------------------------------------------------------ experiments_abort
def test_experiments_abort_cancellation_and_persistence(tmp_path):
    es = _estore(tmp_path)
    try:
        a, b = _pair(es)
        exp = es.start_experiment(champion_config_id=a.id,
                                  challenger_config_id=b.id)
        assert es.abort(exp.id, requested_by="model")["state"] == "denied"
        out = es.abort(exp.id, requested_by="user")
        assert out["state"] == "OK" and out["status"] == E.ST_ABORTED
        assert es.abort(exp.id, requested_by="user")["state"] == "denied"
    finally:
        es.close()
    es2 = E.ExperimentStore(tmp_path / "experiments.db")
    try:
        assert es2.experiment(exp.id).status == E.ST_ABORTED
    finally:
        es2.close()


# ----------------------------------------------------------------- experiments_status
def test_experiments_status_journal_verifies_and_detects_tamper(tmp_path):
    es = _estore(tmp_path)
    try:
        _with_data(es, n=2)
        status = es.journal_verify()
        assert status["state"] == "OK" and status["count"] >= 3
        assert len(es.experiments()) == 1
    finally:
        es.close()
    journal = tmp_path / "experiments.db.journal.jsonl"
    lines = journal.read_text().splitlines()
    lines[0] = json.dumps({"body": "tampered", "chain": "deadbeef"})
    journal.write_text("\n".join(lines) + "\n")
    es2 = E.ExperimentStore(tmp_path / "experiments.db")
    try:
        assert es2.journal_verify()["state"] == "failed"
    finally:
        es2.close()


# ------------------------------------------------------------------- experiments_list
def test_experiments_list_filters_and_orders(tmp_path):
    es = _estore(tmp_path)
    try:
        a, b = _pair(es)
        e1 = es.start_experiment(champion_config_id=a.id,
                                 challenger_config_id=b.id)
        es.abort(e1.id, requested_by="user")
        c = es.register_candidate(role="challenger", label="other",
                                  model="local", description="p3")
        e2 = es.start_experiment(champion_config_id=a.id,
                                 challenger_config_id=c.id)
        assert [e.id for e in es.experiments(status=E.ST_RUNNING)] == [e2.id]
        assert {e.id for e in es.experiments()} == {e1.id, e2.id}
    finally:
        es.close()


# ------------------------------------------------------------------- experiments_show
def test_experiments_show_returns_experiment_plus_evaluation(tmp_path):
    es = _estore(tmp_path)
    try:
        _, _, exp = _with_data(es, n=3)
        got = es.experiment(exp.id)
        assert got.to_dict()["id"] == exp.id
        assert es.evaluate(exp.id, mc_samples=100)["experiment_id"] == exp.id
        assert es.experiment("exp_missing") is None
    finally:
        es.close()


# --------------------------------------------------------------- experiments_snapshot
def test_experiments_snapshot_restore_and_digest_recovery(tmp_path):
    es = _estore(tmp_path)
    try:
        _, _, exp = _with_data(es, n=3)
        snap = es.snapshot()
    finally:
        es.close()
    # Reopen a fresh store (simulating lost state) and recover from the snapshot.
    es2 = E.ExperimentStore(tmp_path / "recovered.db")
    try:
        assert es2.restore(dict(snap), requested_by="user")["restored"] is True
        assert [e.id for e in es2.experiments()] == [exp.id]
        assert len(es2.candidates()) == 2
        # Non-human restore and digest tampering both fail closed.
        with pytest.raises(E.ExperimentError):
            es2.restore(dict(snap), requested_by="model")
        bad = dict(snap)
        bad["candidates"] = []
        with pytest.raises(E.ExperimentError):
            es2.restore(bad, requested_by="user")
    finally:
        es2.close()

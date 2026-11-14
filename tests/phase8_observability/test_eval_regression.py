"""P8-04 — an eval regression or a missing required eval blocks release."""
from __future__ import annotations

from _p8_observability_helpers import IDS, make_observability


def _run_eval(tensor, *, name="contains_answer", score=1.0, passed=True):
    OBS, _, _ = tensor
    obs = make_observability(
        tensor,
        eval_baselines=[
            OBS.EvalBaseline(name, min_pass_rate=1.0, min_score=0.9)
        ],
    )
    with obs.start(ids=IDS) as run:
        with run.model() as span:
            run.evaluate(span, name, score=score, passed=passed)
    return obs.eval_gate


def test_a_meeting_baseline_does_not_block_release(tensor):
    gate = _run_eval(tensor, score=0.95, passed=True)
    report = gate.report()
    assert report["blocked"] is False
    assert gate.release_blocked() is False
    assert gate.release_reasons() == []


def test_a_score_regression_blocks_release(tensor):
    gate = _run_eval(tensor, score=0.2, passed=False)
    report = gate.report()
    assert report["blocked"] is True
    assert gate.release_blocked() is True
    assert any("pass rate" in reason for reason in report["reasons"])
    assert any("mean score" in reason for reason in report["reasons"])


def test_a_pass_rate_regression_blocks_release(tensor):
    gate = _run_eval(tensor, score=1.0, passed=False)
    assert gate.release_blocked() is True


def test_missing_required_eval_blocks_release(tensor):
    OBS, _, _ = tensor
    obs = make_observability(
        tensor,
        eval_baselines=[
            OBS.EvalBaseline("contains_answer", min_pass_rate=1.0)
        ],
    )
    with obs.start(ids=IDS) as run:
        with run.run():
            pass
    report = obs.eval_gate.report()
    assert report["blocked"] is True
    assert any("required evaluation did not run" in reason for reason in report["reasons"])


def test_optional_missing_eval_is_reported_not_blocking(tensor):
    OBS, _, _ = tensor
    obs = make_observability(
        tensor,
        eval_baselines=[
            OBS.EvalBaseline(
                "nice_to_have", min_pass_rate=1.0, required=False
            )
        ],
    )
    with obs.start(ids=IDS) as run:
        with run.run():
            pass
    report = obs.eval_gate.report()
    assert report["blocked"] is False
    assert report["rows"][0]["status"] == "missing"

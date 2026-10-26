from __future__ import annotations

from evals.bench.dataset import EvalSample
from evals.bench.metrics import percentile, summarize


def test_percentile_linear_interpolation():
    assert percentile([], 0.5) is None
    assert percentile([4], 0.95) == 4
    assert percentile([10, 20, 30, 40], 0.50) == 25
    assert percentile([10, 20, 30, 40], 0.95) == 38.5


def test_accuracy_explicit_and_reference_labels():
    samples = [
        EvalSample("a", response="Paris", correct=True, latency_ms=10),
        EvalSample("b", response="  PARIS ", reference="paris", latency_ms=20),
        EvalSample("c", response="London", reference="Paris", latency_ms=30),
        EvalSample("d", response="unlabeled", latency_ms=40),
    ]
    report = summarize(samples)
    accuracy = report["accuracy"]
    assert accuracy["labeled"] == 3
    assert accuracy["correct"] == 2
    assert accuracy["missing"] == 1
    assert accuracy["accuracy"] == 2 / 3


def test_accuracy_is_unavailable_without_labels_and_energy_flops_are_explicit():
    samples = [EvalSample("a", response="x", latency_ms=100, prompt_tokens=10,
                          completion_tokens=5, cost_usd=0.02)]
    report = summarize(samples)
    assert report["accuracy"]["status"] == "unavailable"
    assert report["energy_joules"]["status"] == "unavailable"
    assert report["flops"]["status"] == "unavailable"
    assert "energy_joules" in report["unavailable"]
    assert "flops" in report["unavailable"]
    assert report["latency_ms"]["p50"] == 100
    assert report["prompt_tokens"]["total"] == 10
    assert report["completion_tokens"]["mean"] == 5
    assert report["cost_usd"]["total"] == 0.02
    assert report["model_calls"] == 0
    assert report["network"] is False
    assert report["optimization"]["status"] == "unavailable"


def test_reported_energy_and_flops_are_summed():
    samples = [
        EvalSample("a", energy_joules=2.0, flops=100),
        EvalSample("b", energy_joules=3.0, flops=200),
    ]
    report = summarize(samples)
    assert report["energy_joules"]["status"] == "ok"
    assert report["energy_joules"]["total"] == 5.0
    assert report["flops"]["total"] == 300
    assert "energy_joules" not in report["unavailable"]


def test_empty_dataset_is_honest_not_a_zero():
    report = summarize([])
    assert report["sample_count"] == 0
    assert report["accuracy"]["status"] == "unavailable"
    assert report["latency_ms"]["status"] == "unavailable"
    assert report["cost_usd"]["status"] == "unavailable"

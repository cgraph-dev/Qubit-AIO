"""Descriptive metrics for an offline eval run.

No value is invented: a metric whose source rows never reported it is marked
``unavailable`` with the count of missing samples. Accuracy only appears when
the dataset actually carries labels.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Sequence

from evals.bench.dataset import EvalSample

_WHITESPACE = re.compile(r"\s+")

OPTIMIZATION_UNAVAILABLE = (
    "unavailable: no local optimizer is installed (DSPy/OpenJarvis optimize is "
    "not vendored) and no model calls are made by this bench"
)


def percentile(values: Sequence[float], fraction: float) -> float | None:
    """Linear-interpolation percentile (numpy's default) over known values."""
    if not values:
        return None
    ordered = sorted(float(v) for v in values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * max(0.0, min(1.0, float(fraction)))
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    weight = rank - low
    return ordered[low] + (ordered[high] - ordered[low]) * weight


def _normalize(text: str) -> str:
    return _WHITESPACE.sub(" ", str(text or "").strip()).casefold()


def _values(samples: Iterable[EvalSample], attr: str) -> list[float]:
    return [getattr(sample, attr) for sample in samples if getattr(sample, attr) is not None]


def _distribution(values: Sequence[float], *, total: bool = True) -> dict[str, Any]:
    if not values:
        return {"status": "unavailable", "known": 0}
    out: dict[str, Any] = {
        "status": "ok",
        "known": len(values),
        "mean": sum(values) / len(values),
    }
    if total:
        out["total"] = sum(values)
    return out


def _latency(samples: Sequence[EvalSample]) -> dict[str, Any]:
    values = _values(samples, "latency_ms")
    if not values:
        return {"status": "unavailable", "known": 0}
    return {
        "status": "ok",
        "known": len(values),
        "p50": percentile(values, 0.50),
        "p95": percentile(values, 0.95),
        "mean": sum(values) / len(values),
    }


def _accuracy(samples: Sequence[EvalSample]) -> dict[str, Any]:
    judged: list[bool] = []
    for sample in samples:
        if sample.correct is not None:
            judged.append(bool(sample.correct))
        elif sample.reference is not None:
            judged.append(_normalize(sample.response) == _normalize(sample.reference))
    if not judged:
        return {
            "status": "unavailable",
            "reason": "no labeled samples (no correct field or reference text)",
            "labeled": 0,
            "missing": len(samples),
        }
    correct = sum(1 for value in judged if value)
    return {
        "status": "ok",
        "scorer": "explicit_correct_or_normalized_exact_match",
        "labeled": len(judged),
        "missing": len(samples) - len(judged),
        "correct": correct,
        "accuracy": correct / len(judged),
    }


def summarize(samples: Sequence[EvalSample], *, source: str = "") -> dict[str, Any]:
    """Build the JSON report for one offline run."""
    sample_list = list(samples)
    unavailable: list[str] = []
    accuracy = _accuracy(sample_list)
    if accuracy["status"] != "ok":
        unavailable.append("accuracy")
    latency = _latency(sample_list)
    if latency["status"] != "ok":
        unavailable.append("latency")
    prompt_tokens = _distribution(_values(sample_list, "prompt_tokens"))
    if prompt_tokens["status"] != "ok":
        unavailable.append("prompt_tokens")
    completion_tokens = _distribution(_values(sample_list, "completion_tokens"))
    if completion_tokens["status"] != "ok":
        unavailable.append("completion_tokens")
    cost = _distribution(_values(sample_list, "cost_usd"))
    if cost["status"] != "ok":
        unavailable.append("cost_usd")
    energy = _distribution(_values(sample_list, "energy_joules"))
    if energy["status"] != "ok":
        energy = {"status": "unavailable", "known": 0,
                  "reason": "source did not report energy"}
        unavailable.append("energy_joules")
    flops = _distribution(_values(sample_list, "flops"))
    if flops["status"] != "ok":
        flops = {"status": "unavailable", "known": 0,
                 "reason": "source did not report FLOPs"}
        unavailable.append("flops")
    errored = sum(1 for sample in sample_list if sample.error)
    return {
        "source": source,
        "sample_count": len(sample_list),
        "errored_samples": errored,
        "network": False,
        "model_calls": 0,
        "accuracy": accuracy,
        "latency_ms": latency,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cost_usd": cost,
        "energy_joules": energy,
        "flops": flops,
        "unavailable": unavailable,
        "optimization": {"status": "unavailable", "reason": OPTIMIZATION_UNAVAILABLE},
    }


__all__ = ["OPTIMIZATION_UNAVAILABLE", "percentile", "summarize"]

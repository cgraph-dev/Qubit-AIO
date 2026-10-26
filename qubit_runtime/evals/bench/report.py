"""Human-readable rendering of an eval report."""
from __future__ import annotations

from typing import Any


def _fmt(value: Any, suffix: str = "") -> str:
    if value is None:
        return "unavailable"
    if isinstance(value, float):
        text = f"{value:.4f}".rstrip("0").rstrip(".")
        return f"{text}{suffix}"
    return f"{value}{suffix}"


def render_summary(report: dict[str, Any]) -> str:
    lines = [
        f"Dataset: {report.get('source') or '(unknown)'}",
        f"Samples: {report.get('sample_count', 0)}"
        + (f" ({report.get('errored_samples')} errored)" if report.get("errored_samples") else ""),
    ]
    accuracy = report.get("accuracy") or {}
    if accuracy.get("status") == "ok":
        lines.append(
            f"Accuracy: {_fmt(accuracy.get('accuracy'))} "
            f"({accuracy.get('correct')}/{accuracy.get('labeled')} labeled)")
    else:
        lines.append(f"Accuracy: unavailable ({accuracy.get('reason', 'no labels')})")

    latency = report.get("latency_ms") or {}
    if latency.get("status") == "ok":
        lines.append(
            f"Latency: p50 {_fmt(latency.get('p50'), ' ms')} · "
            f"p95 {_fmt(latency.get('p95'), ' ms')} · mean {_fmt(latency.get('mean'), ' ms')}")
    else:
        lines.append("Latency: unavailable")

    for key, label in (("prompt_tokens", "Prompt tokens"),
                       ("completion_tokens", "Completion tokens")):
        metric = report.get(key) or {}
        if metric.get("status") == "ok":
            lines.append(
                f"{label}: {metric.get('total')} total "
                f"(mean {_fmt(metric.get('mean'))})")
        else:
            lines.append(f"{label}: unavailable")

    cost = report.get("cost_usd") or {}
    if cost.get("status") == "ok":
        lines.append(f"Cost: ${_fmt(cost.get('total'))} (mean ${_fmt(cost.get('mean'))})")
    else:
        lines.append("Cost: unavailable")

    for key, label in (("energy_joules", "Energy"), ("flops", "FLOPs")):
        metric = report.get(key) or {}
        if metric.get("status") == "ok":
            lines.append(f"{label}: {_fmt(metric.get('total'))} (total)")
        else:
            lines.append(f"{label}: {metric.get('reason', 'unavailable')}")

    optimization = report.get("optimization") or {}
    lines.append(f"Optimization run: {optimization.get('status', 'unavailable')}")
    lines.append("Network: none · model calls: 0")
    return "\n".join(lines)


__all__ = ["render_summary"]

"""Human-readable rendering of an offline variant-selection report."""
from __future__ import annotations

from typing import Any


def _pct(value: Any) -> str:
    if value is None:
        return "unavailable"
    return f"{float(value) * 100:.1f}%"


def _interval(value: Any) -> str:
    if not value:
        return "unavailable"
    low, high = value
    return f"[{_pct(low)}, {_pct(high)}]"


def render_variant_report(report: dict[str, Any]) -> str:
    target = report.get("target") or "(unspecified)"
    lines = [
        f"Target: {target}",
        f"Samples: {report.get('sample_count', 0)} "
        f"(assigned {report.get('assigned_samples', 0)}, "
        f"labeled {report.get('labeled_samples', 0)})",
        f"Scorer: {report.get('scorer', 'exact')} · source: {report.get('source', '')}",
    ]
    skipped = [
        (report.get("unassigned_samples", 0), "unassigned"),
        (report.get("unknown_variant_samples", 0), "unknown variant"),
        (report.get("ambiguous_prompt_samples", 0), "ambiguous prompt"),
    ]
    skipped_text = ", ".join(f"{count} {label}" for count, label in skipped if count)
    if skipped_text:
        lines.append(f"Skipped: {skipped_text}")

    lines.append("Variants:")
    for metric in report.get("variants", []):
        if metric.get("labeled"):
            score = f"{_pct(metric.get('accuracy'))} " \
                    f"({metric.get('correct')}/{metric.get('labeled')})"
        else:
            score = "no labels"
        line = (
            f"  {metric.get('name')}: {score} · "
            f"95% CI {_interval(metric.get('wilson_95'))} · "
            f"assigned {metric.get('assigned')}"
        )
        if not metric.get("eligible"):
            line += f" · needs {metric.get('short_by')} more"
        lines.append(line)

    winner = report.get("winner") or {}
    confidence = report.get("confidence") or {}
    if winner:
        lines.append(
            f"Winner: {winner.get('name')} at {_pct(winner.get('accuracy'))} "
            f"({winner.get('labeled')} labeled samples)")
        lines.append(
            f"Confidence: {confidence.get('status', 'not_established')} "
            f"(95% Wilson; margin {_pct(confidence.get('margin'))})")
        if confidence.get("note"):
            lines.append(f"  {confidence['note']}")
    optimization = (report.get("optimization") or {}).get("status") or "not_run"
    lines.append(f"Optimization run: {optimization}")
    lines.append(
        "Boundary: selects among recorded labeled outputs; "
        "no model calls, no network.")
    return "\n".join(lines)


__all__ = ["render_variant_report"]

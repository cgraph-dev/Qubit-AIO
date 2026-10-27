"""Offline runners: dataset or trace store → variant report.

Neither path calls a model or opens a network connection. The trace path needs
a label sidecar because the trace store never stores ground truth.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from evals.bench.dataset import load_jsonl, samples_from_traces
from evals.optimize.errors import OptimizeError
from evals.optimize.labels import TraceLabel, apply_labels
from evals.optimize.scoring import DEFAULT_MIN_SAMPLES, Scorer, score_variants
from evals.optimize.variants import VariantSet


def run_dataset_variants(
    dataset: str | Path,
    variant_set: VariantSet,
    *,
    scorer: str | Scorer | None = None,
    min_samples: int = DEFAULT_MIN_SAMPLES,
    limit: int | None = None,
    labels: Mapping[str, TraceLabel] | None = None,
    label_source: str = "",
) -> dict[str, Any]:
    samples = load_jsonl(dataset)
    if limit is not None:
        samples = samples[: max(0, int(limit))]
    matched = unmatched = 0
    if labels is not None:
        samples, matched, unmatched = apply_labels(samples, labels)
    report = score_variants(
        samples,
        variant_set,
        scorer=scorer,
        min_samples=min_samples,
        source=str(dataset),
        label_source=label_source or "dataset",
        prompt_source="prompt field",
    )
    if labels is not None:
        report["labels_matched"] = matched
        report["labels_unmatched"] = unmatched
    return report


def run_traces_variants(
    store: Any,
    variant_set: VariantSet,
    *,
    labels: Mapping[str, TraceLabel] | None,
    scorer: str | Scorer | None = None,
    min_samples: int = DEFAULT_MIN_SAMPLES,
    limit: int | None = None,
    label_source: str = "",
) -> dict[str, Any]:
    if labels is None:
        raise OptimizeError(
            "trace rows do not carry labels; pass --labels with "
            "{id, reference|correct} records",
            code="optimize_labels_missing")
    rows = list(store.iter_rows())
    samples = samples_from_traces(rows)
    if limit is not None:
        samples = samples[: max(0, int(limit))]
    samples, matched, unmatched = apply_labels(samples, labels)
    if matched == 0:
        raise OptimizeError(
            f"labels file matched none of {len(samples)} trace id(s); refusing to "
            "invent a result",
            code="optimize_no_labels")
    report = score_variants(
        samples,
        variant_set,
        scorer=scorer,
        min_samples=min_samples,
        source="traces",
        label_source=label_source or "labels",
        prompt_source="trace prompt_excerpt",
    )
    report["trace_count"] = len(rows)
    report["labels_matched"] = matched
    report["labels_unmatched"] = unmatched
    return report


__all__ = ["run_dataset_variants", "run_traces_variants"]

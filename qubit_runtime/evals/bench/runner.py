"""Offline eval runner: dataset → report. Never calls a model or the network."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from evals.bench.dataset import EvalSample, load_jsonl, samples_from_traces
from evals.bench.metrics import summarize


def run_dataset(path: str | Path, *, limit: int | None = None) -> dict[str, Any]:
    samples = load_jsonl(path)
    if limit is not None:
        samples = samples[: max(0, int(limit))]
    return summarize(samples, source=str(path))


def run_traces(store: Any, *, limit: int = 1000) -> dict[str, Any]:
    """Evaluate stored traces; accuracy stays unavailable without labels."""
    rows = store.list(limit=limit)
    samples: list[EvalSample] = samples_from_traces(rows)
    return summarize(samples, source="traces")


__all__ = ["run_dataset", "run_traces"]

"""Offline eval bench over stored traces or a JSONL dataset."""
from evals.bench.dataset import EvalInputError, EvalSample, load_jsonl, sample_from_mapping, samples_from_traces
from evals.bench.metrics import OPTIMIZATION_UNAVAILABLE, percentile, summarize
from evals.bench.report import render_summary
from evals.bench.runner import run_dataset, run_traces

__all__ = [
    "EvalInputError",
    "EvalSample",
    "OPTIMIZATION_UNAVAILABLE",
    "load_jsonl",
    "percentile",
    "render_summary",
    "run_dataset",
    "run_traces",
    "sample_from_mapping",
    "samples_from_traces",
    "summarize",
]

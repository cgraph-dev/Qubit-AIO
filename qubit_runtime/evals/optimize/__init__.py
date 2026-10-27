"""Bounded offline prompt-variant selection.

This package is the remaining Phase 6 *learning* slice that can be honest
without a model: it compares 2–4 declared prompt variants over already-recorded,
labeled samples and reports a winner with a Wilson confidence interval. It does
not generate prompts, rewrite them, call a model, or open a network connection.
"""
from evals.optimize.errors import OptimizeError
from evals.optimize.labels import TraceLabel, apply_labels, load_labels
from evals.optimize.report import render_variant_report
from evals.optimize.runner import run_dataset_variants, run_traces_variants
from evals.optimize.scoring import (
    DEFAULT_MIN_SAMPLES,
    OPTIMIZATION_BOUNDARY,
    SCORERS,
    exact_match,
    score_variants,
    wilson_interval,
)
from evals.optimize.variants import (
    MAX_VARIANTS,
    MIN_VARIANTS,
    PromptVariant,
    VariantSet,
    build_variant_set,
    load_variant_file,
    load_variants,
    variant_from_arg,
)

__all__ = [
    "DEFAULT_MIN_SAMPLES",
    "MAX_VARIANTS",
    "MIN_VARIANTS",
    "OPTIMIZATION_BOUNDARY",
    "OptimizeError",
    "PromptVariant",
    "SCORERS",
    "TraceLabel",
    "VariantSet",
    "apply_labels",
    "build_variant_set",
    "exact_match",
    "load_labels",
    "load_variant_file",
    "load_variants",
    "render_variant_report",
    "run_dataset_variants",
    "run_traces_variants",
    "score_variants",
    "variant_from_arg",
    "wilson_interval",
]

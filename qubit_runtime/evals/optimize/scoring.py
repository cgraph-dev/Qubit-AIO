"""Deterministic scoring for candidate prompt variants.

Scoring happens entirely on already-recorded samples. A variant's accuracy is
the share of its labeled samples the scorer accepts; confidence is a 95% Wilson
interval, which is appropriate for the small samples an offline trace set
usually holds. Nothing here calls a model or opens a socket.
"""
from __future__ import annotations

import math
import re
from typing import Any, Callable, Iterable, Mapping, Sequence

from evals.bench.dataset import EvalSample
from evals.optimize.errors import OptimizeError
from evals.optimize.variants import PromptVariant, VariantSet

DEFAULT_MIN_SAMPLES = 5
MIN_SAMPLES_FLOOR = 2
MAX_MIN_SAMPLES = 10_000
MAX_SAMPLES = 20_000
Z_95 = 1.959963984540054
MIN_PROMPT_MATCH_CHARS = 16
MIN_TRUNCATED_MATCH_CHARS = 32

Scorer = Callable[[EvalSample], bool | None]

_WHITESPACE = re.compile(r"\s+")

OPTIMIZATION_BOUNDARY = (
    "offline variant selection over recorded labeled outputs; it does not "
    "generate prompts, rewrite them, call a model, or run a learning loop"
)


def normalize_text(text: object) -> str:
    return _WHITESPACE.sub(" ", str(text or "").strip()).casefold()


def normalize_prompt(text: object) -> str:
    return normalize_text(text)


def exact_match(sample: EvalSample) -> bool | None:
    """Explicit ``correct`` wins; otherwise normalized reference equality."""
    if sample.correct is not None:
        return bool(sample.correct)
    if sample.reference is not None:
        return normalize_text(sample.response) == normalize_text(sample.reference)
    return None


def contains_match(sample: EvalSample) -> bool | None:
    """Like exact match, but a labeled reference may appear inside the response."""
    if sample.correct is not None:
        return bool(sample.correct)
    if sample.reference is None:
        return None
    reference = normalize_text(sample.reference)
    if not reference:
        return False
    return reference in normalize_text(sample.response)


SCORERS: dict[str, Scorer] = {
    "exact": exact_match,
    "contains": contains_match,
}


def resolve_scorer(scorer: str | Scorer | None) -> tuple[Scorer, str]:
    if scorer is None:
        return exact_match, "exact"
    if isinstance(scorer, str):
        found = SCORERS.get(scorer.strip().casefold())
        if found is None:
            raise OptimizeError(
                f"unknown scorer {scorer!r}; choose one of {', '.join(sorted(SCORERS))}",
                code="optimize_unknown_scorer")
        return found, scorer.strip().casefold()
    if callable(scorer):
        return scorer, getattr(scorer, "__name__", "callable")
    raise OptimizeError("scorer must be 'exact', 'contains', or a callable",
                        code="optimize_invalid_scorer")


def wilson_interval(correct: int, total: int, z: float = Z_95) -> tuple[float, float] | None:
    """Two-sided Wilson score interval for one proportion."""
    if total <= 0:
        return None
    proportion = max(0.0, min(1.0, correct / total))
    denominator = 1.0 + (z * z) / total
    centre = (proportion + (z * z) / (2.0 * total)) / denominator
    margin = (z * math.sqrt(
        (proportion * (1.0 - proportion) / total)
        + (z * z) / (4.0 * total * total)
    )) / denominator
    return max(0.0, centre - margin), min(1.0, centre + margin)


def _prompt_matches(sample_prompt: str, variant_prompt: str) -> bool:
    if not sample_prompt or not variant_prompt:
        return False
    if sample_prompt == variant_prompt:
        return True
    # A stored trace keeps a bounded prefix of the real prompt, so a variant
    # prompt may be the beginning of the excerpt. A longer excerpt may also
    # contain the variant prompt after a system wrapper.
    if len(variant_prompt) >= MIN_PROMPT_MATCH_CHARS and sample_prompt.startswith(variant_prompt):
        return True
    if len(sample_prompt) >= MIN_TRUNCATED_MATCH_CHARS and variant_prompt.startswith(sample_prompt):
        return True
    if len(variant_prompt) >= MIN_TRUNCATED_MATCH_CHARS and variant_prompt in sample_prompt:
        return True
    return False


def _match_sample(
    sample: EvalSample,
    variants: Sequence[PromptVariant],
    normalized_prompts: Mapping[str, str],
) -> tuple[str | None, str | None]:
    """Return ``(variant_name, reason)``; reason is set when no unique match."""
    if sample.variant:
        wanted = sample.variant.strip().casefold()
        for variant in variants:
            if variant.name.casefold() == wanted:
                return variant.name, None
        return None, "unknown_variant"
    prompt = normalize_prompt(sample.prompt)
    if not prompt:
        return None, "unassigned"
    exact = [
        variant for variant in variants
        if prompt == normalized_prompts[variant.name]
    ]
    if len(exact) == 1:
        return exact[0].name, None
    matches = [
        variant for variant in variants
        if _prompt_matches(prompt, normalized_prompts[variant.name])
    ]
    if not matches:
        return None, "unassigned"
    longest = max(len(normalized_prompts[variant.name]) for variant in matches)
    winners = [v for v in matches if len(normalized_prompts[v.name]) == longest]
    if len(winners) != 1:
        return None, "ambiguous_prompt"
    return winners[0].name, None


def _judge(sample: EvalSample, scorer: Scorer) -> bool | None:
    try:
        verdict = scorer(sample)
    except Exception as exc:  # noqa: BLE001 - never leak a scorer traceback
        raise OptimizeError(
            f"scorer failed with {type(exc).__name__}",
            code="optimize_scorer_error") from exc
    if verdict is None:
        return None
    if not isinstance(verdict, bool):
        raise OptimizeError("scorer must return True, False, or None",
                            code="optimize_invalid_scorer")
    return verdict


def _metric(
    variant: PromptVariant,
    *,
    assigned: int,
    verdicts: Sequence[bool],
    min_samples: int,
) -> dict[str, Any]:
    total = len(verdicts)
    correct = sum(1 for value in verdicts if value)
    accuracy = correct / total if total else None
    interval = wilson_interval(correct, total)
    return {
        "name": variant.name,
        "description": variant.description,
        "prompt_chars": len(variant.prompt),
        "assigned": assigned,
        "labeled": total,
        "unlabeled": max(0, assigned - total),
        "correct": correct,
        "accuracy": accuracy,
        "wilson_95": None if interval is None else [interval[0], interval[1]],
        "eligible": total >= min_samples,
        "minimum_samples": min_samples,
        "short_by": max(0, min_samples - total),
    }


def score_variants(
    samples: Iterable[EvalSample],
    variant_set: VariantSet,
    *,
    scorer: str | Scorer | None = None,
    min_samples: int = DEFAULT_MIN_SAMPLES,
    source: str = "",
    label_source: str = "",
    prompt_source: str = "",
) -> dict[str, Any]:
    """Score the declared variants and report a winner with confidence.

    Raises :class:`OptimizeError` when no samples are labeled or fewer than two
    variants have ``min_samples`` labeled samples. It never fills a missing
    metric with zero.
    """
    try:
        minimum = int(min_samples)
    except (TypeError, ValueError) as exc:
        raise OptimizeError("min_samples must be an integer",
                            code="optimize_invalid_min_samples") from exc
    if not (MIN_SAMPLES_FLOOR <= minimum <= MAX_MIN_SAMPLES):
        raise OptimizeError(
            f"min_samples must be between {MIN_SAMPLES_FLOOR} and {MAX_MIN_SAMPLES}",
            code="optimize_invalid_min_samples")

    sample_list = list(samples)
    if len(sample_list) > MAX_SAMPLES:
        raise OptimizeError(
            f"{len(sample_list)} samples exceed the {MAX_SAMPLES}-sample bound; "
            "use --limit or a smaller dataset",
            code="optimize_too_many_samples")

    chosen, scorer_name = resolve_scorer(scorer)
    variants = variant_set.variants
    normalized = {variant.name: normalize_prompt(variant.prompt) for variant in variants}
    assigned: dict[str, list[EvalSample]] = {variant.name: [] for variant in variants}
    unassigned = 0
    unknown = 0
    ambiguous = 0
    for sample in sample_list:
        name, reason = _match_sample(sample, variants, normalized)
        if name is None:
            if reason == "unknown_variant":
                unknown += 1
            elif reason == "ambiguous_prompt":
                ambiguous += 1
            else:
                unassigned += 1
            continue
        assigned[name].append(sample)

    verdicts: dict[str, list[bool]] = {variant.name: [] for variant in variants}
    for variant in variants:
        for sample in assigned[variant.name]:
            judged = _judge(sample, chosen)
            if judged is not None:
                verdicts[variant.name].append(judged)

    total_labeled = sum(len(values) for values in verdicts.values())
    if total_labeled == 0:
        raise OptimizeError(
            "no labeled samples were found for the declared variants; refusing to "
            "invent an accuracy or a winner",
            code="optimize_no_labels")

    metrics = [
        _metric(variant, assigned=len(assigned[variant.name]),
                verdicts=verdicts[variant.name], min_samples=minimum)
        for variant in variants
    ]
    eligible = [metric for metric in metrics if metric["eligible"]]
    if len(eligible) < 2:
        detail = ", ".join(
            f"{metric['name']}={metric['labeled']}" for metric in metrics)
        raise OptimizeError(
            f"not enough labeled samples for at least two variants "
            f"(minimum {minimum} each; labeled: {detail})",
            code="optimize_insufficient_samples")

    ranked = sorted(
        eligible,
        key=lambda metric: (-(metric["accuracy"] or 0.0), -metric["labeled"],
                            metric["name"].casefold()),
    )
    winner = ranked[0]
    runner_up = ranked[1]
    winner_interval = winner["wilson_95"]
    runner_interval = runner_up["wilson_95"]
    tie = bool(
        winner["accuracy"] == runner_up["accuracy"]
        and winner["labeled"] == runner_up["labeled"]
    )
    separation = bool(
        winner_interval and runner_interval
        and winner_interval[0] > runner_interval[1]
    )
    if tie:
        status = "tie"
    elif separation:
        status = "clear"
    else:
        status = "not_established"
    confidence = {
        "method": "wilson_95",
        "status": status,
        "separation": separation,
        "tie": tie,
        "margin": (winner["accuracy"] or 0.0) - (runner_up["accuracy"] or 0.0),
        "winner_interval": winner_interval,
        "runner_up_interval": runner_interval,
        "note": (
            "95% Wilson intervals overlap; the leader is reported but the "
            "difference is not established"
            if status == "not_established" else
            "a deterministic tie; the name ordering breaks it for reporting"
            if status == "tie" else
            "the winner's 95% Wilson lower bound is above the runner-up's upper bound"
        ),
    }

    return {
        "kind": "offline_prompt_variant_selection",
        "target": variant_set.target,
        "source": source,
        "variant_source": variant_set.source,
        "prompt_source": prompt_source,
        "scorer": scorer_name,
        "sample_count": len(sample_list),
        "label_source": label_source or source,
        "assigned_samples": sum(len(items) for items in assigned.values()),
        "labeled_samples": total_labeled,
        "unlabeled_samples": max(0, len(sample_list) - total_labeled),
        "unassigned_samples": unassigned,
        "unknown_variant_samples": unknown,
        "ambiguous_prompt_samples": ambiguous,
        "min_samples": minimum,
        "variants": metrics,
        "eligible_variants": len(eligible),
        "winner": {
            "name": winner["name"],
            "accuracy": winner["accuracy"],
            "labeled": winner["labeled"],
            "correct": winner["correct"],
            "wilson_95": winner_interval,
            "confidence": status,
        },
        "runner_up": {
            "name": runner_up["name"],
            "accuracy": runner_up["accuracy"],
            "labeled": runner_up["labeled"],
            "wilson_95": runner_interval,
        },
        "confidence": confidence,
        "network": False,
        "model_calls": 0,
        "deterministic": True,
        "optimization": {
            "status": "not_run",
            "mode": "offline_variant_selection",
            "reason": OPTIMIZATION_BOUNDARY,
        },
    }


__all__ = [
    "DEFAULT_MIN_SAMPLES",
    "MAX_MIN_SAMPLES",
    "MAX_SAMPLES",
    "MIN_SAMPLES_FLOOR",
    "OPTIMIZATION_BOUNDARY",
    "SCORERS",
    "Scorer",
    "Z_95",
    "contains_match",
    "exact_match",
    "normalize_prompt",
    "normalize_text",
    "resolve_scorer",
    "score_variants",
    "wilson_interval",
]

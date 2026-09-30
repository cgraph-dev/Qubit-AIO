"""Packet 8P controlled evaluation — pure statistics (no I/O).

Adapted from the smallest tested concepts of Evolve (Apache-2.0 (c) 2026
Kristian Baer / Northtek; docs/archive/2026-09-21/UPSTREAM_REUSE.md R27): per-session signal
aggregation with explicit=5x implicit weighting, wins/losses binarization, a
seeded Beta(1+wins,1+losses) Monte Carlo posterior, and the promotion decision
(NeedMoreData below a minimum per-arm sample, Promote only at a high
posterior, else Hold). Deterministic under a fixed seed; stdlib only.
"""
from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass
from typing import List, Optional, Tuple

DEFAULT_MIN_SESSIONS_PER_ARM = 20
DEFAULT_PROMOTE_THRESHOLD = 0.95
DEFAULT_MC_SAMPLES = 10_000
DEFAULT_EXPLICIT_WEIGHT = 5.0
DEFAULT_IMPLICIT_WEIGHT = 1.0

KIND_EXPLICIT = "explicit"
KIND_IMPLICIT = "implicit"
SIGNAL_KINDS = (KIND_EXPLICIT, KIND_IMPLICIT)

DEC_NEED_MORE_DATA = "NEED_MORE_DATA"
DEC_PROMOTE = "PROMOTE"
DEC_HOLD = "HOLD"


def config_fingerprint(role: str, label: str, model: str,
                       description: str) -> str:
    payload = json.dumps({"role": role, "label": label, "model": model,
                          "description": description}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def aggregate_scores(signals: List[Tuple[str, float]],
                     explicit_weight: float = DEFAULT_EXPLICIT_WEIGHT,
                     implicit_weight: float = DEFAULT_IMPLICIT_WEIGHT) -> float:
    if not signals:
        return 0.5
    total_w = 0.0
    acc = 0.0
    for kind, value in signals:
        w = explicit_weight if kind == KIND_EXPLICIT else implicit_weight
        v = max(0.0, min(1.0, float(value)))
        total_w += w
        acc += w * v
    if total_w <= 0:
        return 0.5
    return acc / total_w


def wins_losses(scores: List[float]) -> Tuple[int, int]:
    wins = 0
    losses = 0
    for s in scores:
        v = max(0.0, min(1.0, float(s)))
        if v >= 0.5:
            wins += 1
        else:
            losses += 1
    return wins, losses


def _beta_sample(rng: random.Random, alpha: float, beta: float) -> float:
    if alpha <= 0 or beta <= 0:
        return 0.5
    x = rng.gammavariate(alpha, 1.0)
    y = rng.gammavariate(beta, 1.0)
    s = x + y
    return 0.5 if s <= 0 else x / s


def posterior_probability(champion_scores: List[float],
                          challenger_scores: List[float], *,
                          samples: int = DEFAULT_MC_SAMPLES,
                          seed: Optional[int] = None) -> float:
    cw, cl = wins_losses(champion_scores)
    gw, gl = wins_losses(challenger_scores)
    rng = random.Random(seed)
    hits = 0
    n = max(1, int(samples))
    for _ in range(n):
        c = _beta_sample(rng, 1 + cw, 1 + cl)
        g = _beta_sample(rng, 1 + gw, 1 + gl)
        if g > c:
            hits += 1
    return hits / n


@dataclass
class PromotionDecision:
    decision: str
    posterior: float
    sessions_each: int
    required: int


def promotion_decision(champion_scores: List[float],
                       challenger_scores: List[float], *,
                       min_sessions_per_arm: int = DEFAULT_MIN_SESSIONS_PER_ARM,
                       promote_threshold: float = DEFAULT_PROMOTE_THRESHOLD,
                       mc_samples: int = DEFAULT_MC_SAMPLES,
                       seed: Optional[int] = None) -> PromotionDecision:
    sessions_each = min(len(champion_scores), len(challenger_scores))
    if len(champion_scores) < min_sessions_per_arm or \
            len(challenger_scores) < min_sessions_per_arm:
        return PromotionDecision(DEC_NEED_MORE_DATA, 0.0, sessions_each,
                                 min_sessions_per_arm)
    post = posterior_probability(champion_scores, challenger_scores,
                                 samples=mc_samples, seed=seed)
    if post >= promote_threshold:
        return PromotionDecision(DEC_PROMOTE, post, sessions_each,
                                 min_sessions_per_arm)
    return PromotionDecision(DEC_HOLD, post, sessions_each, min_sessions_per_arm)

"""Packet 6B: private evaluation set, task outcomes, regression scoring, and a
non-auto-promoting promotion gate for router, memory, skills, model, and
safety changes.

Quality, safety, latency, resource, and tool-success are scored and gated as
SEPARATE families. No single weighted average is ever emitted, so a safety
regression can never be hidden inside a "better" quality score. Nothing is
promoted automatically: the gate returns a decision only, and an explicit
user/operator action is always required to promote anything.
"""
from __future__ import annotations

from .task_outcomes import (TaskOutcomeError, TaskOutcomeStore,
                            VALID_APPROVALS, VALID_TOOL_STATUSES)
from .eval_set import base_cases, load_eval_set, load_private_set
from .scoring import (CATEGORY_FAMILY, EVAL_CATEGORIES, FAMILIES,
                      SAFETY_CRITICAL, coverage_policy, score_run)
from .gate import (APPROVE, NEEDS_REVIEW, REJECT, PromotionDecision,
                   PromotionGate)
from .poisoning import DANGEROUS_KINDS, run_poisoning, summarize_poisoning
from .runner import EvalComponents, evaluate_cases

__all__ = [
    "TaskOutcomeError", "TaskOutcomeStore", "VALID_APPROVALS",
    "VALID_TOOL_STATUSES",
    "base_cases", "load_eval_set", "load_private_set",
    "CATEGORY_FAMILY", "EVAL_CATEGORIES", "FAMILIES", "SAFETY_CRITICAL",
    "coverage_policy", "score_run",
    "PromotionDecision", "PromotionGate",
    "APPROVE", "REJECT", "NEEDS_REVIEW",
    "DANGEROUS_KINDS", "run_poisoning", "summarize_poisoning",
    "EvalComponents", "evaluate_cases",
]
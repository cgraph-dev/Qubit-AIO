"""Packet 6B: regression scoring with SEPARATE quality / safety / latency /
resource / tool-success families.

The evaluator never emits one overall weighted score. Every category is scored
on its own pass rate and every family keeps its own metrics, so a safety
regression can never be hidden inside a quality or latency average.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..benchmarking.harness import percentile

FAMILIES = ("quality", "safety", "latency", "resource", "tool_success")

EVAL_CATEGORIES: Dict[str, tuple] = {
    "safety": ("prompt_injection", "local_vs_coding_safety",
               "confirm_cancel_contextual", "refusal_fallback",
               "secret_handling"),
    "quality": ("correction_priority", "personal_fact_conflict",
                "memory_retrieval", "short_chat", "language_policy"),
    "latency": ("deterministic_routing", "ambiguous_routing",
                "malformed_speech"),
    "resource": ("model_residency", "runner_options", "vram_guard"),
    "tool_success": ("tool_selection", "invalid_arguments", "tool_execution"),
}
CATEGORY_FAMILY: Dict[str, str] = {
    cat: family for family, cats in EVAL_CATEGORIES.items() for cat in cats}

# Any regression in these categories is fatal for promotion, by construction.
SAFETY_CRITICAL: tuple = EVAL_CATEGORIES["safety"]


def _empty_category(category: str, family: str) -> Dict[str, Any]:
    return {"category": category, "family": family, "n": 0, "passed": 0,
            "failed": 0, "errors": 0, "pass_rate": None, "failed_reasons": []}


def score_run(observations: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Score one evaluation run.

    ``observations`` rows carry at least case_id/category/passed and may carry
    error, failed_reason, latency_ms, tool, resource (structural checks).
    """
    per_category: Dict[str, Dict[str, Any]] = {}
    per_family: Dict[str, Dict[str, Any]] = {}
    for fam in FAMILIES:
        per_family[fam] = {"family": fam, "n": 0, "passed": 0, "failed": 0,
                           "errors": 0, "pass_rate": None}
    latency: Dict[str, List[float]] = {}
    tools: Dict[str, Dict[str, Any]] = {}

    for obs in observations:
        cat = obs.get("category")
        fam = CATEGORY_FAMILY.get(cat)
        row = per_category.setdefault(cat, _empty_category(cat, fam or "quality"))
        row["n"] += 1
        err = bool(obs.get("error"))
        passed = bool(obs.get("passed"))
        if err:
            row["errors"] += 1
        elif passed:
            row["passed"] += 1
        else:
            row["failed"] += 1
            row["failed_reasons"].append(
                obs.get("failed_reason") or obs.get("case_id") or "failed")
        if fam is not None:
            f = per_family[fam]
            f["n"] += 1
            if err:
                f["errors"] += 1
            elif passed:
                f["passed"] += 1
            else:
                f["failed"] += 1
        lat = obs.get("latency_ms")
        if lat is not None and not err:
            latency.setdefault(cat, []).append(float(lat))
        tool = obs.get("tool")
        if tool:
            t = tools.setdefault(tool, {"tool": tool, "attempts": 0,
                                        "success": 0, "failures": 0,
                                        "denied": 0, "errors": 0})
            t["attempts"] += 1
            if err:
                t["errors"] += 1
            elif passed:
                t["success"] += 1
            elif obs.get("denied") is True:
                t["denied"] += 1
            else:
                t["failures"] += 1

    for row in per_category.values():
        if row["n"]:
            row["pass_rate"] = round(row["passed"] / row["n"], 4)
    for fam, f in per_family.items():
        if f["n"]:
            f["pass_rate"] = round(f["passed"] / f["n"], 4)

    latency_summary: Dict[str, Any] = {}
    for cat, vals in latency.items():
        latency_summary[cat] = {
            "n": len(vals),
            "p50_ms": percentile(sorted(vals), 50),
            "p95_ms": percentile(sorted(vals), 95),
            "max_ms": max(vals),
        }

    return {
        "n": len(observations),
        "per_category": per_category,
        "per_family": per_family,
        "latency": latency_summary,
        "tool_success": tools,
    }


def family_summary(scores: Dict[str, Any], family: str) -> Dict[str, Any]:
    """Isolated summary of ONE metric family (never merged with others)."""
    fam = scores["per_family"].get(family)
    if fam is None:
        raise ValueError(f"unknown family: {family}")
    cats = {cat: per for cat, per in scores["per_category"].items()
            if CATEGORY_FAMILY.get(cat) == family}
    return {"family": family, "summary": fam, "categories": cats}


def pass_rate(score_row: Optional[Dict[str, Any]]) -> Optional[float]:
    if not score_row or not score_row.get("n"):
        return None
    return round(float(score_row["passed"]) / float(score_row["n"]), 4)
"""Offline (no-network) unit-mode benchmarking for Packet 4A.

Evaluates deterministic-corpus categories through the pure Classifier so the
L0 route path has measured latency evidence without touching Ollama.
Categories that require a live model are skipped with a typed reason and
counted separately — never reported as measured.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, List

from ..intent.classifier import Classifier
from ..intent.state import ConfirmationState, ConversationState
from .corpus import Case

OFFLINE_CATEGORIES = {
    "deterministic_routing",
    "confirm_cancel_contextual",
    "malformed_speech",
    "local_vs_coding_safety",
}

MODEL_REQUIRED_CATEGORIES = sorted(
    {"deterministic_routing", "ambiguous_routing", "confirm_cancel_contextual",
     "local_vs_coding_safety", "malformed_speech", "short_chat",
     "personal_fact_conflict", "tool_selection", "invalid_arguments",
     "refusal_fallback", "language_policy", "prompt_injection"}
    - OFFLINE_CATEGORIES)


def _offline_classifier(cfg_dict: Dict[str, Any],
                        confirmations: Optional[ConfirmationState] = None,
                        conversation: Optional[ConversationState] = None
                        ) -> Classifier:
    conf = confirmations or ConfirmationState()
    conv = conversation or ConversationState()
    return Classifier(cfg_dict, confirmations=conf, conversation=conv,
                      busy_fn=lambda: False)


def evaluate_offline(cases: List[Case]) -> List[Dict[str, Any]]:
    """Measure in-process routing latency for offline-evaluable categories."""
    cfg_dict: Dict[str, Any] = {"llm_classifier": False}
    rows: List[Dict[str, Any]] = []
    classifier = _offline_classifier(cfg_dict)
    for case in cases:
        if case.category not in OFFLINE_CATEGORIES:
            rows.append({"case_id": case.case_id,
                         "category": case.category,
                         "split": case.split,
                         "skipped": "requires-model"})
            continue
        pending_needed = bool(case.expected.get("pending_required"))
        busy_needed = bool(case.expected.get("busy_required"))
        conf = ConfirmationState()
        if pending_needed:
            conf.request("demo action", {}, "unit-mode fixture")
        cls = _offline_classifier(cfg_dict, confirmations=conf) \
            if (pending_needed or busy_needed) else classifier
        if busy_needed:
            cls.busy_fn = lambda: True
        t0 = time.perf_counter()
        try:
            intent = cls.classify(case.text)
            kind = getattr(intent, "kind", None)
            reason = getattr(intent, "reason", "")
            error = None
        except Exception as exc:
            kind, reason, error = None, "", f"{type(exc).__name__}: {exc}"[:160]
        elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 4)
        rows.append({"case_id": case.case_id, "category": case.category,
                     "split": case.split, "route_latency_ms": elapsed_ms,
                     "observed_kind": kind, "observed_reason": str(reason)[:80],
                     "error": error})
    return rows


def summarize_unit(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    from .harness import percentile
    latencies = [r["route_latency_ms"] for r in rows
                 if "route_latency_ms" in r and r.get("error") is None]
    skipped = sum(1 for r in rows if r.get("skipped"))
    errored = sum(1 for r in rows if r.get("error"))
    return {
        "n_measured": len(latencies),
        "n_skipped_requires_model": skipped,
        "n_errors": errored,
        "route_latency_ms_p50": percentile(latencies, 50),
        "route_latency_ms_p95": percentile(latencies, 95),
        "route_latency_ms_max": max(latencies) if latencies else None,
        "note": ("measurement only: Packet 4A records evidence and enforces "
                 "no L0 latency gate (gating belongs to later packets)"),
    }

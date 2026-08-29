"""Flag-gated routing pipeline (Packet 4C).

`route_for(text, cfg)` is the ONLY production entry point. With
intent.router.enabled false (the default) it returns None immediately and
production behavior is byte-identical to pre-4C. L0 runs first; if it
declines and intent.router.l1_enabled is true AND the measured embedding
gate passed at fit time, L1 gets a chance; its NO-MATCH sentinel hands the
utterance back to the existing classifier/LLM path unchanged.
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional, Tuple

from .l0 import RouteDecision, route as l0_route


class RouterPipeline:
    def __init__(self, cfg: Any) -> None:
        self.enabled = bool(cfg.get("intent", "router", "enabled", default=False))
        self.l1_enabled = self.enabled and bool(
            cfg.get("intent", "router", "l1_enabled", default=False))
        self._l1 = None
        self.last_l0_ms: Optional[float] = None
        self.last_stage: Optional[str] = None

    def route(self, text: str, *, pending_confirmation: bool = False,
              busy: bool = False) -> Tuple[Optional[RouteDecision],
                                           Optional[object]]:
        """Returns (L0 decision or None, L1 decision or None)."""
        if not self.enabled:
            return None, None
        t0 = time.perf_counter()
        d0 = l0_route(text, pending_confirmation=pending_confirmation,
                      busy=busy)
        self.last_l0_ms = round((time.perf_counter() - t0) * 1000.0, 4)
        self.last_stage = "l0" if d0 else None
        if d0 is not None:
            return d0, None
        if self.l1_enabled and self._l1 is not None:
            d1 = self._l1.decide(text)
            self.last_stage = "l1" if d1 else "l1-nomatch"
            return None, d1
        return None, None


def build_pipeline(cfg: Any) -> RouterPipeline:
    """Build a pipeline; fits/calibrates L1 only when explicitly enabled."""
    pipe = RouterPipeline(cfg)
    if pipe.l1_enabled:
        from .l1 import SemanticRouter, load_default_routes
        router = SemanticRouter(routes=load_default_routes())
        url = cfg.get("intent", "local_llm", "url", default=None) \
            or "http://127.0.0.1:11434"
        model = cfg.get("intent", "router", "embed_model",
                        default="nomic-embed-text")
        router.fit(url=url, model=model)
        calib = _calibration_examples()
        if calib:
            router.calibrate(calib, url=url, model=model)
        pipe._l1 = router
    return pipe


def _calibration_examples() -> list:
    """Calibration slice from the ACCEPTANCE corpus split (held-out is never
    used for thresholds). Returns (route_label, text) pairs."""
    try:
        from ..benchmarking.corpus import load_corpus
        from pathlib import Path
        corpus_dir = Path(__file__).resolve().parents[2] / "benchmarks" / "corpus"
        corpus = load_corpus(corpus_dir)
    except Exception:
        return []
    route_map = {
        "deterministic_routing": None,   # L0 territory — excluded from L1
        "ambiguous_routing": "clarify",
        "confirm_cancel_contextual": None,
        "local_vs_coding_safety": None,
        "malformed_speech": "clarify",
        "short_chat": "conversation",
        "personal_fact_conflict": "conversation",
        "tool_selection": "local",
        "invalid_arguments": None,
        "refusal_fallback": "conversation",
        "language_policy": None,
        "prompt_injection": None,
    }
    out = []
    for case in corpus["acceptance"]:
        route = route_map.get(case.category)
        if route is None:
            continue
        kinds = case.expected.get("kinds")
        if isinstance(kinds, list) and len(kinds) == 1:
            route = kinds[0]
        out.append((route, case.text))
    return out

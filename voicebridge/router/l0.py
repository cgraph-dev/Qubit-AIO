"""L0 deterministic routing (Packet 4C).

Pure in-process rules only: no model calls, no embedding calls, no I/O.
Target: p95 < 10 ms per decision. Rules reuse the classifier's own pattern
constants so L0 and the legacy heuristic path cannot drift apart.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from ..intent.classifier import (
    CANCEL_PATTERNS, CHITCHAT_RE, CONFIRM_PATTERNS, SYSTEM_COMMANDS,
    TASK_HINTS)
from ..local import actions as local_actions


@dataclass(frozen=True)
class RouteDecision:
    kind: str
    confidence: float
    rule: str
    params: Dict[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.params is None:
            object.__setattr__(self, "params", {})


def _matches(patterns, text: str) -> bool:
    return any(re.match(p, text) for p in patterns)


def route(text: str, *, pending_confirmation: bool = False,
          busy: bool = False,
          clock: Callable[[], float] = time.perf_counter,
          ) -> Optional[RouteDecision]:
    """Deterministic-first routing; None means 'not decided at L0'."""
    orig = text.strip()
    if not orig:
        return RouteDecision("conversation", 0.0, "empty")
    low = orig.lower()

    for name, spec in SYSTEM_COMMANDS.items():
        for pat in spec["patterns"]:
            if re.search(pat, low):
                if name in ("mute", "unmute") and re.search(
                        r"\bmic(rophone)?\b", low):
                    continue
                params: Dict[str, Any] = {"command": name}
                m = re.search(r"(turbo|normal|slow|hard|pro|fast|deepseek|default)",
                              low)
                if name == "model":
                    params["tier"] = m.group(1) if m else None
                return RouteDecision("system", 0.98, f"system:{name}", params)

    if pending_confirmation:
        if _matches(CONFIRM_PATTERNS, low):
            return RouteDecision("confirm", 0.97, "confirmation-affirmative")
        if _matches(CANCEL_PATTERNS, low):
            return RouteDecision("cancel", 0.97, "confirmation-negative")

    # Packet 4D destructive screen: destruction text never takes the
    # immediate-action route; it goes to the confirmation-gated agent path.
    from ..safety.destructive_screen import screen_destructive
    if screen_destructive(orig).matched:
        return RouteDecision("coding", 0.9, "destructive-intent-guard")

    hit = local_actions.match(orig)
    if hit:
        return RouteDecision("local", 0.95, f"local:{hit.name}",
                             {"action": hit.name, "action_params": hit.params})

    if re.search(r"\bperplexity\b", low):
        return RouteDecision("conversation", 0.9, "perplexity-guard")

    if re.search(r"\b(weather|forecast|meteo)\b|\b(temperature|rain|snow)\s+"
                 r"(outside|today|tomorrow|in\s+\w+)", orig, re.I):
        return RouteDecision("conversation", 0.9, "weather-fastpath")

    task_score = 0.0
    for pat in TASK_HINTS:
        if re.search(pat, low):
            task_score += 0.5

    if task_score == 0.0 and CHITCHAT_RE.match(text):
        return RouteDecision("conversation", 0.62, "chitchat")

    if task_score == 0.0 and busy and re.search(
            r"\b(stop|cancel|abort|kill|halt)\b.*\b(task|work|it|that|them|"
            r"this|job|agent)\b|\b(stop it|stop now|abort)\b", low):
        return RouteDecision("cancel", 0.92, "busy-stop")

    if task_score == 0.0 and len(text) > 8 and _looks_non_english(text):
        return RouteDecision("clarify", 0.7, "non-english-guard")

    if task_score >= 1.0:
        return RouteDecision("coding", 0.85, "strong-task-hint")

    return None


_NON_LATIN_THRESHOLD = 0x24F


def _looks_non_english(text: str) -> bool:
    """Mirror of the classifier's unintelligible-input guard."""
    for ch in text[:64]:
        if ord(ch) > _NON_LATIN_THRESHOLD:
            return True
    tokens = [t for t in re.findall(r"[A-Za-z']+", text.lower())]
    if not tokens:
        return True
    common = {"the", "is", "are", "you", "me", "my", "what", "how", "can",
              "do", "please", "and", "to", "a", "i"}
    hits = sum(1 for t in tokens if t in common)
    return len(tokens) >= 4 and hits == 0

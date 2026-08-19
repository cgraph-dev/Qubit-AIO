"""Deterministic + LLM-assisted utterance router.

Priority order:
  1. pending confirmation  -> confirm / cancel patterns
  2. system control        -> fast local commands (no model needed)
  3. confirm / cancel      -> yes/no patterns
  4. coding task           -> heuristic task patterns; if unsure, LLM classifier
  5. conversation / clarify-> LLM-assisted default

Returns an Intent with kind + task_text + params. The orchestrator executes it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from ..logging_setup import get_logger, log_event
from .llm import classify_with_ollama
from .state import ConfirmationState, ConversationState

log = get_logger()

SYSTEM_COMMANDS: Dict[str, Dict[str, Any]] = {
    "status": {
        "patterns": [r"\b(what'?s|what is)?\s*(the\s+)?(status|state|health|how are you)\b",
                     r"\b(are you (there|working|listening))\b"],
        "desc": "report bridge + gateway status",
    },
    "mute":     {"patterns": [r"\b(mute|silence|shut up|be quiet)\b"], "desc": "mute spoken output"},
    "unmute":   {"patterns": [r"\b(unmute|un-mute|speak again|turn (the )?voice (back )?on)\b"], "desc": "unmute"},
    "help":     {"patterns": [r"\b(help|what can you do|how do i use (you|this))\b"], "desc": "help"},
    "stop":     {"patterns": [r"^\s*(stop|halt|freeze|quit that|never ?mind)\s*[.!]*$"], "desc": "abort active task / stop speaking"},
    "cancel":   {"patterns": [r"^\s*(cancel|abort|forget it|scrap (that|it)|never ?mind)\s*[.!]*$"], "desc": "cancel confirmation or task"},
    "model":    {"patterns": [r"^\s*(use|switch (to|back (to)?)|set (the )?model (to)?)\s+(turbo|normal|slow|hard|pro|fast|deepseek|default)\b"],
                 "desc": "switch agent model tier for subsequent tasks"},
    "quit":     {"patterns": [r"^\s*(quit|exit|goodbye|good night|shut ?down)\s*[.!]*$"], "desc": "exit the bridge"},
}

CONFIRM_PATTERNS = [
    r"^\s*(yes|yeah|yep|yup|sure|ok|okay|go ahead|go for it|do it|apply (it|them|the patch|the change|all)|confirm|affirmative|please do|sounds good|correct|right|that'?s fine|approved|approve)\s*[,.]?\s*(please|yes|go ahead|do it)?\s*[.!]*$",
    r"^\s*(y|ok|k|sure)\s*[.!]*$",
]
CANCEL_PATTERNS = [
    r"^\s*(no|nope|nah|cancel|abort|stop|don'?t|never ?mind|forget (it|that)|not now|skip|negative|no thanks|no way|wrong|reject)\s*[,.]?\s*(cancel|stop|don'?t|please)?\s*[.!]*$",
]

# Task-ish verbs/domains that strongly suggest a coding request.
TASK_HINTS = [
    r"\b(open|inspect|look (at|into)|check|patch|fix|create|write|add|edit|modify|update|refactor|"
    r"refactor|rename|move|delete|remove|implement|build|test|run tests|run the (tests|app|server|pipeline)|"
    r"compile|review|debug|trace|profile|commit|push|pull|merge|rebase|deploy|release|migrate|"
    r"restructure|simplify|clean( up)?|lint|format|document|explain (the )?(code|repo|project)|"
    r"analyze|generate|scaffold|initialize|install|upgrade|downgrade|configure|setup)\b",
    r"\b(webhook|api|endpoint|schema|database|migration|stripe|repo|repository|branch|commit|pr|"
    r"pull request|issue|bug|crash|error|exception|stack trace|log|logs|terminal|shell|command|"
    r"script|browser|file|files|directory|folder|package|dependency|docker|kubernetes|ci|pipeline|"
    r"component|widget|screen|feature|module|class|function|method|test|tests|deploy|production)\b",
]

SYSTEM_STOP_WORDS = {"the", "a", "an", "is", "are", "to", "of", "in", "on", "for", "and", "or"}


@dataclass
class Intent:
    kind: str                       # system|confirm|cancel|conversation|coding|clarify
    confidence: float = 0.5
    task_text: str = ""
    params: Dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    source: str = "heuristic"       # heuristic | llm


class Classifier:
    def __init__(self, cfg: Dict[str, Any], confirmations: ConfirmationState,
                 conversation: ConversationState) -> None:
        self.cfg = cfg
        self.confirmations = confirmations
        self.conversation = conversation
        self.llm_enabled = bool(cfg.get("llm_classifier", True))
        self.ollama_url = cfg.get("ollama_url", "http://127.0.0.1:11434")
        self.ollama_model = cfg.get("ollama_model", "qwen3-vl:8b")
        self.timeout = float(cfg.get("classify_timeout_s", 8.0))
        self._tier_map = {
            "turbo": "ollama/qwen3-vl:8b", "fast": "ollama/qwen3-vl:8b",
            "normal": "ollama/qwen3:30b-a3b", "slow": "ollama/qwen3.8:27b-q3",
            "hard": "deepseek/deepseek-v4-flash", "pro": "deepseek/deepseek-v4-pro",
            "deepseek": "deepseek/deepseek-v4-flash", "default": None,
        }

    # ---- deterministic checks -------------------------------------------------
    def _system_command(self, text: str) -> Optional[Intent]:
        low = text.lower().strip()
        for name, spec in SYSTEM_COMMANDS.items():
            for pat in spec["patterns"]:
                if re.search(pat, low):
                    params: Dict[str, Any] = {"command": name}
                    if name == "model":
                        m = re.search(r"(turbo|normal|slow|hard|pro|fast|deepseek|default)", low)
                        params["tier"] = m.group(1) if m else None
                    return Intent("system", confidence=0.98, task_text=low,
                                  params=params, reason=f"system:{name}")
        return None

    def _confirm_cancel(self, text: str) -> Optional[Intent]:
        low = text.strip().lower()
        if self.confirmations.pending:
            for pat in CONFIRM_PATTERNS:
                if re.match(pat, low):
                    return Intent("confirm", confidence=0.97, task_text=low, reason="confirmation affirmative")
            for pat in CANCEL_PATTERNS:
                if re.match(pat, low):
                    return Intent("cancel", confidence=0.97, task_text=low, reason="confirmation negative")
        return None

    def _task_hint_score(self, text: str) -> float:
        low = text.lower()
        score = 0.0
        for pat in TASK_HINTS:
            if re.search(pat, low):
                score += 0.5
        return min(score, 1.5)

    # ---- main entry -----------------------------------------------------------
    def classify(self, text: str) -> Intent:
        text = text.strip()
        if not text:
            return Intent("conversation", confidence=0.0, reason="empty")

        sys_i = self._system_command(text)
        if sys_i:
            return sys_i

        cc = self._confirm_cancel(text)
        if cc:
            return cc

        task_score = self._task_hint_score(text)
        # Strong task signal -> coding without a model round-trip.
        if task_score >= 1.0:
            return Intent("coding", confidence=0.85, task_text=text,
                          reason=f"task hints ({task_score})")

        # Weak/ambiguous -> LLM classifier (if enabled & available).
        if self.llm_enabled:
            llm = classify_with_ollama(text, self.ollama_url, self.ollama_model, self.timeout)
            if llm:
                kind = llm["kind"]
                conf = float(llm.get("confidence", 0.6))
                if kind == "coding":
                    task = llm.get("task") or text
                    return Intent("coding", confidence=conf, task_text=task,
                                  reason=llm.get("reason", ""), source="llm")
                if kind in ("conversation", "clarify", "system", "confirm", "cancel"):
                    params = {}
                    if kind == "system" and llm.get("task"):
                        params["llm_suggestion"] = llm["task"]
                    return Intent(kind, confidence=conf, task_text=text,
                                  params=params, reason=llm.get("reason", ""), source="llm")
                # fall through to heuristic default

        # Default: conversation (safe), or coding if hints were partial.
        if task_score >= 0.5:
            return Intent("coding", confidence=0.7, task_text=text,
                          reason=f"partial task hints ({task_score})")
        return Intent("conversation", confidence=0.55, task_text=text, reason="default")

    def resolve_model_tier(self, tier: Optional[str]) -> Optional[str]:
        if not tier:
            return None
        return self._tier_map.get(tier, tier)

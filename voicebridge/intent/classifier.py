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

from ..local import actions as local_actions
from ..logging_setup import get_logger, log_event
from .llm import classify_with_ollama
from .state import ConfirmationState, ConversationState

log = get_logger()

# Broken-English / accent / ASR-error tolerant helpers
_BROKEN_MAP = {
    "open claw": "openclaw", "openclaw": "openclaw",
    "hermes": "hermes",
    "root to": "route to",  # "root to open claw" ~ "route to openclaw"
    "trickish": "tricky", "micky": "mickey", "miki": "mickey",
}
_FUZZY_TASK_VERBS = r"\b(creat|creaet|cretae|make|do|put|write|ad|add|build|fix|open|update|change|delete|remove|move|copy)\w*\b"


SYSTEM_COMMANDS: Dict[str, Dict[str, Any]] = {
    "status": {
        # explicit status asks ONLY — "how are you" is a greeting, not status
        "patterns": [r"\b(status|system status|bridge status)\b",
                     r"\b(are you (there|working|listening|alive|up|online))\b",
                     r"\bhow('s| is) the (bridge|voice|system|assistant)\b"],
        "desc": "report bridge + gateway status",
    },
    "mute":     {"patterns": [r"\b(mute|silence|shut up|be quiet)\b"], "desc": "mute spoken output"},
    "unmute":   {"patterns": [r"\b(unmute|un-mute|speak again|turn (the )?voice (back )?on)\b"], "desc": "unmute"},
    "help":     {"patterns": [r"\b(help|what can you do|how do i use (you|this))\b"], "desc": "help"},
    "stop":     {"patterns": [r"^\s*(stop|halt|freeze|quit that|never ?mind)\s*[.!]*$"], "desc": "abort active task / stop speaking"},
    "continue": {"patterns": [r"^\s*(continue|resume|keep going|go on|carry on|pick it back up)\s*[.!]*$"],
                 "desc": "resume the interrupted task"},
    "closetask": {"patterns": [r"^\s*(close( the)? (current )?task|finish (the current )?task|close this|wrap (it|this) up|ok done|that'?s (all|enough)|done with this)\s*[.!]*$"],
                  "desc": "finish current task, then ask about the queue"},
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

# Sticky routing — explicit user preference that survives turns (fixes the 'forgets Hermes/browser' bug)
STICKY_PATTERNS = [
    (r"\broute (this|it|everything|all)?\s*(to|through)\s+(hermes|openclaw|browser)\b", "route"),
    (r"\buse (the )?(hermes|openclaw|browser)\b( for (this|now|everything|all))?", "use"),
    (r"\b(always )?(use|route through) hermes\b", "hermes"),
    (r"\b(always )?(use|route through) openclaw\b", "openclaw"),
    (r"\b(always )?(use|route through) browser\b", "browser"),
]

# Explicit Hermes delegation ("ask Hermes to ...", "Hermes, ...").
HERMES_PATTERNS = [
    r"\b(ask|tell|have|let|get)\s+hermes\b",
    r"^\s*hermes[,:]?\s+",
    r"\broute (this|it|that)?\s*(to|through|via)\s+hermes\b",
    r"\b(delegate|hand off|handoff)\s+(this|it)?\s*to\s+hermes\b",
    r"\buse\s+(the\s+)?(browser|chromium)\b.*\b(check|open|browse|look|search|find|watch|read|scrape|research|copy)\b",
    r"\buse\s+(the\s+)?hermes\b",      # "use Hermes", "use Hermes agent"
    r"\bhermes agent\b",
    r"\b(send|give|hand)\s+(this|it|the task)\s+to\s+hermes\b",
]

# Task-ish verbs/domains that strongly suggest a coding request.
TASK_HINTS = [
    r"\b(open|inspect|look (at|into)|check|patch|fix|create|write|add|edit|modify|update|refactor|"
    r"refactor|rename|move|delete|remove|implement|build|test|run tests|run the (tests|app|server|pipeline)|"
    r"compile|review|debug|trace|profile|commit|push|pull|merge|rebase|deploy|release|migrate|"
    r"restructure|simplify|clean( up)?|lint|format|document|explain (the )?(code|repo|project)|"
    r"analyze|generate|scaffold|initialize|install|upgrade|downgrade|configure|setup|"
    r"list|show|find|search|count|summarize|report|describe)\b",
    r"\b(webhook|webhooks|api|apis|endpoint|endpoints|schema|database|migration|stripe|repo|repos|"
    r"repository|repositories|branch|branches|commit|commits|pr|"
    r"pull request|issue|issues|bug|bugs|crash|error|errors|exception|stack trace|log|logs|"
    r"terminal|shell|command|commands|"
    r"script|scripts|browser|file|files|directory|directories|folder|folders|package|packages|"
    r"dependency|dependencies|docker|kubernetes|ci|pipeline|pipelines|"
    r"component|components|widget|widgets|screen|screens|feature|features|module|modules|"
    r"class|classes|function|functions|method|methods|test|tests|deploy|production)\b",
    # web-action tasks (shop/order/browse/captcha) -> agent with browser
    r"\b(open|visit|go to|navigate to|load)\s+[a-z0-9.-]+\.[a-z]{2,}\b",
    r"\b(buy|purchase|order|add to cart|checkout|pay for|book|reserve|subscribe)\b",
    r"\b(price|prices|product|products|shop|shopping|store|cart|order|orders|delivery|shipping)\b",
    r"\b(captcha|sign in|log in|login|2fa|two.factor|verify me on)\b",
    r"\b(website|web page|site|online|amazon|ebay|zara|aliexpress|netflix|spotify|steam|epic)\b",
    r"\b(how much|what does it cost|is it in stock|in stock|available)\b",
]

SYSTEM_STOP_WORDS = {"the", "a", "an", "is", "are", "to", "of", "in", "on", "for", "and", "or"}


@dataclass
class Intent:
    kind: str                       # system|confirm|cancel|conversation|coding|clarify|local|hermes|status
    confidence: float = 0.5
    task_text: str = ""
    params: Dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    source: str = "heuristic"       # heuristic | llm
    backend: str = "openclaw"       # openclaw | hermes | local | chat | system


def normalize_broken_english(text: str) -> str:
    """Fix common ASR/accents artifacts without breaking correct English."""
    low = text.lower()
    # keep original casing for task_text but use lowered for matching; these fixes are whitespace-safe
    for k, v in _BROKEN_MAP.items():
        if k in low:
            # word-boundary replace
            low = re.sub(r"\b" + re.escape(k) + r"\b", v, low)
    # collapse repeated spaces
    low = re.sub(r"\s+", " ", low).strip()
    return low

class Classifier:
    def __init__(self, cfg: Dict[str, Any], confirmations: ConfirmationState,
                 conversation: ConversationState) -> None:
        self.cfg = cfg
        self.confirmations = confirmations
        self.conversation = conversation
        self.llm_enabled = bool(cfg.get("llm_classifier", True))
        self.ollama_url = cfg.get("ollama_url", "http://127.0.0.1:11434")
        # Default the classifier to the SAME resident model the bridge uses for
        # chat (local_llm.model / intent.ollama_model), not a hardcoded qwen3-vl
        # which is never pulled/resident on this box. Falls back gracefully.
        _local = (cfg.get("local_llm") or {}).get("model") or cfg.get("ollama_model") or "qubit-brain"
        self.ollama_model = cfg.get("ollama_model") or _local
        self.timeout = float(cfg.get("classify_timeout_s", 8.0))
        # Tier map: local tiers resolve to the resident local model; cloud tiers
        # to deepseek (already configured). Kept as labels, not hardcoded emojis.
        self._tier_map = {
            "turbo": _local, "fast": _local, "normal": _local,
            "hard": cfg.get("deepseek_model", "deepseek/deepseek-v4-flash"),
            "pro": cfg.get("deepseek_model", "deepseek/deepseek-v4-pro"),
            "deepseek": cfg.get("deepseek_model", "deepseek/deepseek-v4-flash"),
            "default": None,
        }

    # ---- deterministic checks -------------------------------------------------
    def _system_command(self, text: str) -> Optional[Intent]:
        low = text.lower().strip()
        for name, spec in SYSTEM_COMMANDS.items():
            for pat in spec["patterns"]:
                if re.search(pat, low):
                    # "mute" alone = bridge output; "mute the microphone" = local action
                    if name in ("mute", "unmute") and re.search(r"\bmic(rophone)?\b", low):
                        continue
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

    def _sticky_route(self, text: str) -> Optional[str]:
        """If text is an explicit routing preference, return target (hermes/openclaw/browser)."""
        low = text.lower().strip()
        for pat, _ in STICKY_PATTERNS:
            m = __import__("re").search(pat, low)
            if m:
                # extract which target was mentioned
                for t in ("hermes", "openclaw", "browser"):
                    if t in low[m.start():m.end()+20] or t in m.group(0):
                        return t
                # fallback: find target anywhere in text
                for t in ("hermes", "openclaw", "browser"):
                    if t in low:
                        return t
        return None

    def _hermes_request(self, text: str) -> Optional[Intent]:
        low = text.lower().strip()
        for pat in HERMES_PATTERNS:
            if re.search(pat, low):
                # strip the "ask hermes to" / "hermes," prefix so the task is clean
                task = re.sub(r"^\s*hermes[,:]?\s*", "", low, flags=re.I)
                task = re.sub(r"^(ask|tell|have|let|get)\s+hermes\s+(to\s+)?", "", task, flags=re.I)
                task = task.strip(" .,;:") or text
                return Intent("hermes", confidence=0.9, task_text=task,
                              backend="hermes", reason="hermes delegation")
        return None

    # ---- main entry -----------------------------------------------------------
    def classify(self, text: str) -> Intent:
        orig = text.strip()
        if not orig:
            return Intent("conversation", confidence=0.0, reason="empty")
        # keep original for speaking/task but use normalized for decisions
        norm = normalize_broken_english(orig)
        text = norm  # downstream heuristics see cleaned form
        # preserve original for task_text when we emit intents (swap back)
        _orig_text = orig

        sys_i = self._system_command(text)
        if sys_i:
            return sys_i

        cc = self._confirm_cancel(text)
        if cc:
            return cc

        # Direct PC action (whitelist) beats everything except system/confirm.
        local_match = local_actions.match(text)
        if local_match:
            return Intent("local", confidence=0.95, task_text=text,
                          params={"action": local_match.name,
                                  "action_params": local_match.params},
                          backend="local", reason=f"local:{local_match.name}")

        # "use perplexity API to search X" MUST reach the web/conversation
        # path, NOT the coding agent: TASK_HINTS contains \bapi\b + \bcheck\b
        # so it otherwise scores 1.0 -> coding -> a full agent dispatch that
        # double-bills (agent tokens + its own Perplexity search). Forcing any
        # utterance naming perplexity to conversation fixes the trap.
        if re.search(r"\bperplexity\b", text, re.I):
            return Intent("conversation", confidence=0.9,
                          reason="perplexity named -> web/conversation path",
                          source="heuristic")

        hermes_i = self._hermes_request(text)
        if hermes_i:
            return hermes_i

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
                # confirm/cancel only make sense with a pending confirmation;
                # without one, an LLM "confirm"/"cancel" guess is just chat.
                if kind in ("confirm", "cancel") and not self.confirmations.pending:
                    kind = "conversation"
                if kind == "coding":
                    task = llm.get("task") or text
                    return Intent("coding", confidence=conf, task_text=task,
                                  reason=llm.get("reason", ""), source="llm")
                if kind == "local":
                    return Intent("local", confidence=conf, task_text=text,
                                  params={"llm_suggestion": llm.get("task", "")},
                                  backend="local", reason=llm.get("reason", ""), source="llm")
                if kind == "hermes":
                    task = llm.get("task") or text
                    return Intent("hermes", confidence=conf, task_text=task,
                                  backend="hermes", reason=llm.get("reason", ""), source="llm")
                if kind in ("conversation", "clarify", "system", "confirm", "cancel"):
                    params = {}
                    if kind == "system" and llm.get("task"):
                        params["llm_suggestion"] = llm["task"]
                    return Intent(kind, confidence=conf, task_text=text,
                                  params=params, reason=llm.get("reason", ""), source="llm")
                # fall through to heuristic default

        # Broken-English boost: file/doc/text + action verb even with typo
        if re.search(_FUZZY_TASK_VERBS, text) and re.search(r"\b(file|document|doc|text|folder|tmp|project|code|html|image|screenshot)\b", text):
            return Intent("coding", confidence=0.68, task_text=text, reason="broken-english file task")
        # Default: conversation (safe), or coding if hints were partial.
        if task_score >= 0.5:
            return Intent("coding", confidence=0.7, task_text=text,
                          reason=f"partial task hints ({task_score})")
        return Intent("conversation", confidence=0.55, task_text=text, reason="default")

    def resolve_model_tier(self, tier: Optional[str]) -> Optional[str]:
        if not tier:
            return None
        return self._tier_map.get(tier, tier)

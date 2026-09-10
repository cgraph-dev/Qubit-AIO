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
from typing import Any, Callable, Dict, Optional

from ..local import actions as local_actions
from ..logging_setup import get_logger
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
    "cancel":   {"patterns": [r"^\s*(cancel|abort|forget it|scrap (that|it)|cancel (that|it|this)|never ?mind)\s*[.!]*$"], "desc": "cancel confirmation or task"},
    "model":    {"patterns": [r"^\s*(use|switch (to|back (to)?)|set (the )?model (to)?)\s+(turbo|normal|slow|hard|pro|fast|deepseek|default)\b"],
                 "desc": "switch agent model tier for subsequent tasks"},
    "quit":     {"patterns": [r"^\s*(quit|exit|goodbye|good night|shut ?down)\s*[.!]*$"], "desc": "exit the bridge"},
}

CONFIRM_PATTERNS = [
    r"^\s*(yes|yeah|yep|yup|sure|ok|okay|go ahead|go for it|do it|apply (it|them|the patch|the change|all)|confirm|affirmative|please do|sounds good|correct|right|that'?s fine|approved|approve)\s*[,.]?\s*(please|yes|go ahead|do it)?\s*[.!]*$",
    r"^\s*(y|ok|k|sure)\s*[.!]*$",
]
CANCEL_PATTERNS = [
    r"^\s*(no|nope|nah|cancel|abort|stop|don'?t|never ?mind|forget (it|that)|not now|skip|negative|no thanks|no way|wrong|reject)\s*[,.]?\s*(cancel|stop|don'?t|please|that|it|this)?\s*[.!]*$",
]

# Leading direct address ("Qubit, ...", "Hey Qubit ...") is stripped
# before confirm/cancel matching: ASR transcripts keep the wake word, and
# anchored yes/no patterns otherwise miss "Qubit, confirm." /
# "Qubit, cancel that." (the registry path strips it already).
_WAKE_STRIP_RE = re.compile(r"^(?:hey\s+|ok\s+|okay\s+)?qubit[,.]?\s+",
                            re.IGNORECASE)

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

# Obvious conversational openers with ZERO task signal skip the ~0.7s LLM
# classifier round-trip entirely ("responds a little slow" fix). Anything
# containing a TASK_HINTS word still goes through the full pipeline.
CHITCHAT_RE = re.compile(
    r"^(?:how\s+(?:are|r|is)\s+(?:you|u|it|things|life|your day)"
    r"|what(?:'s| is)?\s+(?:up|the time|time|your name|today)"
    r"|who\s+(?:are|r)\s+(?:you|u)"
    r"|are\s+you\s+(?:there|ok|okay|good|fine|alive|working|listening)"
    r"|good\s+(?:morning|afternoon|evening|night)"
    r"|thanks?(?:\s+you)?|sorry|please"
    r"|hello+|hi+|hey+"
    r"|(?:what(?:'s| is)?|who(?:'s| is)?)\s+(?:your|my)\s+(?:name|handle)"
    r"|who\s+(?:are|r|made|created|built)\s+(?:you|u|this))\b",
    re.I)


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

# Minimal English function words — real English input almost always contains
# at least one; garbled/foreign input typically none.
_INPUT_EN_WORDS = {
    "the", "a", "an", "is", "are", "am", "was", "were", "what", "when",
    "where", "who", "how", "why", "which", "my", "your", "his", "her",
    "i", "you", "we", "they", "he", "she", "it", "to", "for", "of", "in",
    "on", "at", "and", "or", "but", "please", "can", "could", "would",
    "should", "do", "does", "did", "tell", "know", "about", "me", "us",
    "need", "want", "give", "show", "find", "open", "make", "lets", "let",
}


def _input_looks_non_english(text: str) -> bool:
    """True when input is clearly NOT plain English: any character beyond
    Latin Extended-A (ă ș т cyrillic CJK...) or zero English function words
    across >=3 alphabetic tokens."""
    for ch in text[:200]:
        if ord(ch) > 0x24F:
            return True
    toks = [w for w in re.findall(r"[a-z]+", text.lower()) if len(w) > 1]
    if len(toks) >= 3 and not any(w in _INPUT_EN_WORDS for w in toks):
        return True
    return False


class Classifier:
    def __init__(self, cfg: Dict[str, Any], confirmations: ConfirmationState,
                 conversation: ConversationState,
                 busy_fn: Optional[Callable[[], bool]] = None) -> None:
        self.cfg = cfg
        self.confirmations = confirmations
        self.conversation = conversation
        # True while a Hermes/OpenClaw task is actually running — "stop"/"cancel"
        # must ABORT then, even with no pending confirmation (2026-08-24 bug:
        # 'stop the task' was downgraded to chitchat and Hermes kept running).
        self.busy_fn = busy_fn or (lambda: False)
        self.llm_enabled = bool(cfg.get("llm_classifier", True))
        # Packet 4B: alias/url come from the ONE canonical runner contract
        # (same source as chat/summary/planner) — no per-caller chain.
        from ..runner_contract import resolve as _rc_resolve
        _rc = _rc_resolve(cfg)
        self.ollama_url = _rc.url
        self.ollama_model = _rc.alias
        self.timeout = float(cfg.get("classify_timeout_s", 8.0))
        _local = self.ollama_model
        # Tier map: local tiers resolve to the resident local model; cloud tiers
        # to deepseek (already configured). Kept as labels, not hardcoded emojis.
        self._tier_map = {
            "turbo": _local, "fast": _local, "normal": _local,
            "hard": cfg.get("deepseek_model", "deepseek/deepseek-v4-flash"),
            "pro": cfg.get("deepseek_model", "deepseek/deepseek-v4-pro"),
            "deepseek": cfg.get("deepseek_model", "deepseek/deepseek-v4-flash"),
            "default": None,
        }
        # Packet 4C: optional router pipeline (None = feature disabled).
        self._router: Any = None

    def attach_router(self, pipeline) -> None:
        """Attach a flag-gated RouterPipeline (Packet 4C). Callers keep full
        responsibility for building it only when intent.router.enabled."""
        self._router = pipeline

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
        low = _WAKE_STRIP_RE.sub("", text.strip()).lower()
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
        # Packet 4C: flag-gated router pipeline (default OFF). When enabled,
        # L0 deterministic decisions short-circuit here and L1 semantic
        # no-match simply falls through to the legacy path unchanged.
        if getattr(self, "_router", None) is not None:
            d0, d1 = self._router.route(
                orig, pending_confirmation=self.confirmations.pending,
                busy=self.busy_fn())
            if d0 is not None:
                return Intent(d0.kind, confidence=d0.confidence,
                              task_text=text, params=dict(d0.params or {}),
                              reason=f"l0:{d0.rule}", source="router")
            if d1 is not None:
                return Intent(d1.route, confidence=round(d1.score, 3),
                              task_text=text,
                              reason=f"l1:{d1.score:.2f}/{d1.margin:.2f}",
                              source="router")
        # keep original for speaking/task but use normalized for decisions
        norm = normalize_broken_english(orig)
        text = norm  # downstream heuristics see cleaned form

        sys_i = self._system_command(text)
        if sys_i:
            return sys_i

        cc = self._confirm_cancel(text)
        if cc:
            return cc

        # Direct PC action (whitelist) beats everything except system/confirm.
        # Packet 4D destructive screen: destruction text never takes the
        # immediate-action whitelist; it falls through to coding hints
        # (confirmation-gated agent path).
        from ..safety.destructive_screen import screen_destructive
        if not screen_destructive(orig).matched:
            local_match = local_actions.match(text)
        else:
            local_match = None
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

        # Weather/forecast fast-path: ALWAYS live-web info (Perplexity), never
        # the coding agent and never the baked brain (which can't know today's
        # forecast). _needs_web() on the orchestrator side completes the route.
        if re.search(r"\b(weather|forecast|meteo)\b|\b(temperature|rain|snow)\s+(outside|today|tomorrow|in\s+\w+)", text, re.I):
            return Intent("conversation", confidence=0.9, task_text=text,
                          reason="weather/forecast -> live web")

        # Identity-fact fast-path: "what's my instagram/github/phone/..."
        # are served from compiled core-fact context (Packet 4B; formerly baked). Routing them to the
        # coding agent made Qubit spin up Hermes for a fact it knows cold.
        if re.search(r"(?:what(?:'?s| is)|whats|tell\s+me|give\s+me|remind\s+me|"
                     r"do\s+you\s+(?:know|remember)|which\s+is)[^.?!]{0,28}"
                     r"\b(my|mi)\s+(instagram|insta|github|discord|telegram|"
                     r"whatsapp|email|e-mail|phone|number|handle|socials?)\b",
                     text, re.I):
            return Intent("conversation", confidence=0.9, task_text=text,
                          reason="identity fact (baked) -> answer locally")

        task_score = self._task_hint_score(text)
        # Chitchat fast-path: pure conversational openers never need the model
        # (task_score==0 guarantees no coding/domain signal was matched).
        if task_score == 0.0 and CHITCHAT_RE.match(text):
            return Intent("conversation", confidence=0.62, task_text=text,
                          reason="chitchat fast-path (skip LLM)")
        # Explicit STOP while something is actually running -> cancel NOW,
        # never a chitchat answer claiming the task was stopped (2026-08-24:
        # 'stop the task' reached the LLM, got downgraded to chat, and Hermes
        # kept grinding while Qubit said "I'll stop it").
        if task_score == 0.0 and self.busy_fn() and re.search(
                r"\b(stop|cancel|abort|kill|halt)\b.*\b(task|work|it|that|them|this|job|agent)\b|\b(stop it|stop now|abort)\b",
                text, re.I):
            return Intent("cancel", confidence=0.92, task_text=text,
                          reason="explicit stop while task running")
        # Non-English / unintelligible guard: garbage-in must NEVER become a
        # coding task (a garbled Romanian sentence once dispatched Hermes to
        # "understand the code"). Ask for clarification instead.
        if task_score == 0.0 and len(text) > 8 and _input_looks_non_english(text):
            return Intent("clarify", confidence=0.7, task_text=text,
                          reason="non-english/unclear input (no task signal)")
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
                # confirm needs a pending confirmation; cancel needs a pending
                # confirmation OR a running task. Downgrading 'cancel' to chat
                # while Hermes was mid-task made "stop the task" a lie — the
                # agent kept working and Qubit claimed it had stopped.
                if kind == "confirm" and not self.confirmations.pending:
                    kind = "conversation"
                elif kind == "cancel" and not self.confirmations.pending \
                        and not self.busy_fn():
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

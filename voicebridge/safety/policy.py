"""Safety policy: confirmation before destructive actions, allow/deny lists.

A voice-triggered agent has real destructive potential (delete/rm/git reset/
force-push/drop table...). This policy is the gate between an utterance and a
dispatched task:

  1. deny patterns        -> refuse outright, say why
  2. always_confirm       -> forced spoken confirmation
  3. destructive patterns -> require spoken confirmation ("Do you want me to...")
  4. never_confirm        -> skip confirmation even if it looks destructive
  5. dry_run_first        -> prepend a dry-run instruction to coding tasks

All confirmations are stateful in the orchestrator: yes -> dispatch, no ->
cancel. Nothing is executed until the confirmation resolves.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List

from ..logging_setup import log_event

# Actions that are almost certainly destructive/irreversible.
DESTRUCTIVE_PATTERNS: List[str] = [
    r"\b(delete|remove|rm|unlink|uninstall|purge|wipe|erase|drop)\b",
    r"\brm\s+-[a-z]*r[a-z]*f?",
    r"\b(drop|truncate)\s+(table|database|schema|collection)\b",
    r"\b(format|mkfs)\b",
    r"\bdd\s+if=",
    r"\b(shutdown|poweroff|reboot|halt)\b",
    r"\bk(ill|illall|pkill)\b",
    r"\b(git\s+clean\s+-[a-z]*f|git\s+reset\s+--hard|git\s+push\s+(-f|--force)|git\s+checkout\s+--\s+\.)",
    r"\b(force\s+(push|delete|remove|overwrite)|push\s+-f\b)",
    r"\boverwrite\b",
    r"\breplace\s+(all|the\s+(file|config|production))",
    r"\bsudo\b",
    r"\bchmod\s+[0-7]{3}\b",
    r"\bchown\b",
    r"\btruncate\s+-s",
    r"\b>(>)?\s*[/~]?[a-z0-9_.-]+",
    r"\bcurl\s+[^|]*\|\s*(sudo\s+)?(ba)?sh\b",
    r"\b(wipe|clear|reset)\s+(all\s+)?(data|database|state|sessions)",
]

# Refused outright (agentic-safety / prompt-injection markers).
DENY_PATTERNS: List[str] = [
    r"ignore (all )?(previous|prior|earlier) instructions",
    r"disable (the )?safety",
    r"remove (the )?(safety|confirmation)",
    r"jailbreak",
    r"you are now (dan|without restrictions)",
]

# Money / real-world actions: always ask before spending or ordering.
# Matched before destructive patterns so a "buy X" is confirmed even though
# it isn't destructive in the filesystem sense.
MONEY_ALWAYS_CONFIRM: List[str] = [
    r"\b(buy|purchase|order|checkout|pay for|subscribe|book|reserve|place (the )?order)\b",
    r"\b(add to cart|confirm (the )?purchase|complete (the )?(order|purchase|checkout)|pay now)\b",
    r"\b(price|cost)\s+.*\b(buy|order|purchase)\b",
]


@dataclass
class SafetyVerdict:
    allowed: bool = True
    deny: bool = False
    deny_reason: str = ""
    require_confirm: bool = False
    confirm_reason: str = ""
    dry_run: bool = False
    matched_pattern: str = ""
    dry_run_instruction: str = ""

    @property
    def blocked(self) -> bool:
        return self.deny


class SafetyPolicy:
    def __init__(self, cfg: Dict[str, Any]) -> None:
        self.cfg = cfg
        self.enabled = bool(cfg.get("enabled", True))
        self.confirm_destructive = bool(cfg.get("confirm_destructive", True))
        self.dry_run_first = bool(cfg.get("dry_run_first", False))
        self.denylist = [re.compile(p, re.I) for p in cfg.get("denylist", [])]
        self.always_confirm = [re.compile(p, re.I) for p in cfg.get("always_confirm", [])]
        self.never_confirm = [re.compile(p, re.I) for p in cfg.get("never_confirm", [])]
        self._destructive = [re.compile(p, re.I) for p in DESTRUCTIVE_PATTERNS]
        self._money = [re.compile(p, re.I) for p in MONEY_ALWAYS_CONFIRM]

    def check(self, text: str) -> SafetyVerdict:
        v = SafetyVerdict()
        if not self.enabled or not text.strip():
            return v
        low = text.lower()

        for p in self.denylist:
            if p.search(low):
                v.allowed = False
                v.deny = True
                v.deny_reason = f"denied phrase: {p.pattern}"
                log_event("safety.deny", reason=v.deny_reason, text=text)
                return v

        for p in self.always_confirm:
            if p.search(low):
                v.require_confirm = True
                v.confirm_reason = f"always-confirm pattern: {p.pattern}"
                v.matched_pattern = p.pattern
                log_event("safety.confirm", reason=v.confirm_reason, text=text)
                return v

        # Money actions (buy/order/checkout) always need spoken confirmation,
        # even though they aren't filesystem-destructive. Exempt informational
        # / code-context questions ("tell me about the buy handler", "what
        # does the checkout function do") — those are code questions, not
        # purchase attempts.
        MONEY_INFO = re.compile(
            r"\b(tell me about|explain|what does|how does|why does|what is|"
            r"how is|look at|show me|find|search for|review|inspect|describe|"
            r"the (buy|order|checkout|purchase)\s+(function|method|handler|"
            r"endpoint|api|route|class|module|code|logic|flow|test))\b",
            re.I)
        if MONEY_INFO.search(low):
            pass
        else:
            for p in self._money:
                if p.search(low):
                    v.require_confirm = True
                    v.confirm_reason = f"money action: {p.pattern}"
                    v.matched_pattern = p.pattern
                    log_event("safety.money_confirm", reason=v.confirm_reason, text=text)
                    return v

        for p in self.never_confirm:
            if p.search(low):
                v.require_confirm = False
                v.confirm_reason = ""
                v.matched_pattern = ""
                log_event("safety.never_confirm", pattern=p.pattern, text=text)
                return v

        # Informational / code-context override: destructive verbs used to
        # describe CODE SYMBOLS or questions are not destructive actions.
        # "fix the delete endpoint", "tell me about the remove function",
        # "how does the drop handler work" -> no confirmation. Real actions
        # ("delete the files", "remove the database") still confirm.
        CODE_CTX = re.compile(
            r"\b(delete|remove|rm|drop|erase|wipe|unlink|truncate)\s+"
            r"(the\s+|this\s+|that\s+|my\s+|our\s+)?"
            r"(function|method|handler|endpoint|api|route|class|module|"
            r"code|logic|error|bug|warning|block|test|middleware|decorator|"
            r"import|export|statement|line)\b",
            re.I)
        INFO_PHRASES = re.compile(
            r"\b(tell me about|explain|what does|how does|why does|what is|"
            r"how is|look at|show me|find|search for|review|inspect|describe)\b",
            re.I)
        if CODE_CTX.search(low) or (INFO_PHRASES.search(low)
                                    and re.search(r"\b(delete|remove|rm|drop|"
                                                  r"erase|wipe|unlink|truncate)\b", low)):
            v.require_confirm = False
            v.confirm_reason = ""
            v.matched_pattern = ""
            log_event("safety.code_context", text=text)
            if self.dry_run_first:
                v.dry_run = True
                v.dry_run_instruction = (
                    "First do a dry run: inspect the affected files and report the "
                    "exact changes without modifying anything. Then wait for "
                    "confirmation before applying changes.")
            return v

        if self.confirm_destructive:
            for p in self._destructive:
                if p.search(low):
                    v.require_confirm = True
                    v.confirm_reason = f"destructive pattern: {p.pattern}"
                    v.matched_pattern = p.pattern
                    log_event("safety.destructive", pattern=p.pattern, text=text)
                    break

        if self.dry_run_first:
            v.dry_run = True
            v.dry_run_instruction = (
                "First do a dry run: inspect the affected files and report the "
                "exact changes without modifying anything. Then wait for "
                "confirmation before applying changes.")
        return v

    def spoken_confirmation(self, action_desc: str, verdict: SafetyVerdict) -> str:
        if verdict.dry_run:
            return (f"OK, I'll start with a dry run: {action_desc}. "
                    "I'll report what would change and ask before applying anything.")
        return f"Do you want me to {action_desc}?"

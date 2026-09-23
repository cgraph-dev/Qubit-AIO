"""Destructive-intent screen (Packet 4D structural mitigation, option A).

A deterministic, model-independent lexical screen used as defense-in-depth
at three independent layers:
  1. after the schema-constrained classifier decision,
  2. before the deterministic whitelist/local route,
  3. inside the capability broker before execution.

Measured motivation (docs/archive/2026-09-21/BAKEOFF_4D.json): official Instruct-2507 quants
routed unseen destructive phrasings ("wipe the demo database") to the
immediate-action `local` class even with safety persona rules. This screen
does not depend on model behavior at all.

Fail-closed semantics: anything that MATCHES is treated as destructive
regardless of what a model claimed. False positives degrade to the
confirmation-gated coding path - never to silent execution.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List

MAX_INPUT_CHARS = 2000

# Destruction verbs.
_VERBS = (
    r"delete|remove|removing|removed|wipe|wiping|wiped|erase|erasing|"
    r"erased|format(?:ting|ted)?|destroy|destroying|purge|purging|purged|"
    r"truncate|uninstall|shred|zero[\s-]*out"
)

# Data-bearing objects / targets.
_OBJECTS = (
    r"files?|folders?|director(?:y|ies)|database[s]?|db|tables?|rows?|"
    r"records?|drive|drives|disk|disks|ssd|hdd|partition[s]?|volume[s]?|"
    r"data|history|cookies|backups?|downloads|documents|pictures|photos|"
    r"images|videos|projects?|repo[s]?|hard\s*drive|external|usb|sd\s*card|"
    r"(?:app|application)s?\s+settings?"
)

# Quantifier/adjective words allowed between verb and object (repeatable).
_QUANT = r"(?:(?:all|entire|whole|every|my|the|his|her)\s+)*"

# Strong standalone patterns (no adjacency requirement).
_STRONG = (
    r"\brm\s+-{1,2}[a-z]+\b",
    r"\brm\s+-rf\b",
    r"\bfactory[\s-]*reset\b",
    r"\bformat\s+(?:the\s+|my\s+|this\s+|that\s+)?(?:c:|d:|drive|disk|"
    r"ssd|hdd|partition|usb|sd|external)",
    r"\bdrop\s+(?:\w+\s+){0,2}(?:table|database|schema|db)\b",
    r"\bdelete\s+" + _QUANT + r"(?:files?|folders?|data|records?)",
    r"\berase\s+" + _QUANT
    + r"(?:everything|files?|photos?|images|data|drive|disk)",
    r"\bwipe\b",
    r"\b(?:clean|clear)\s+(?:out\s+)?" + _QUANT
    + r"(?:pictures|photos|images|videos|documents|downloads|files|folders|"
    r"history|records|data)\b",
    r"\bget\s+rid\s+of\s+" + _QUANT
    + r"(?:files?|folders?|pictures|photos|images|videos|documents|"
    r"downloads|records|data)\b",
    r"\bpurge\s+" + _QUANT + r"(?:records?|data|history|logs?)",
)

_VERB_RE = re.compile(rf"\b(?:{_VERBS})\b", re.I)
_OBJ_RE = re.compile(rf"\b(?:{_OBJECTS})\b", re.I)
_STRONG_RE = re.compile(r"(?:" + "|".join(_STRONG) + r")", re.I)
_COMBINED_RE = re.compile(
    rf"\b(?:{_VERBS})\s+{_QUANT}(?:{_OBJECTS})\b", re.I)
_WS_RE = re.compile(r"[^\w]+")
_WS_KEEP_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class ScreenResult:
    matched: bool
    reason: str = ""
    terms: List[str] = field(default_factory=list)


def _variants(text: str) -> List[str]:
    base = (text or "")[:MAX_INPUT_CHARS]
    return [_WS_KEEP_RE.sub(" ", base).strip(),
            _WS_RE.sub(" ", base).strip()]


def screen_destructive(text: str) -> ScreenResult:
    """True when the text carries destruction intent toward data/objects.

    Deterministic and model-independent. Screens two normalizations: one
    preserving hyphens (rm -rf, format c:) and one collapsing punctuation
    (catches 'wipe....the....db' obfuscation).
    """
    for variant in _variants(text):
        if not variant:
            continue
        m = _STRONG_RE.search(variant)
        if m:
            return ScreenResult(True, f"strong:{m.group(0).lower()}",
                                [m.group(0).lower()[:60]])
        if _COMBINED_RE.search(variant):
            m2 = _COMBINED_RE.search(variant)
            return ScreenResult(True, f"verb+object:{m2.group(0).lower()}",
                                [m2.group(0).lower()[:60]])
        verb = _VERB_RE.search(variant)
        obj = _OBJ_RE.search(variant)
        if verb and obj:
            return ScreenResult(True, "verb+noun-proximity",
                                [verb.group(0).lower()[:30],
                                 obj.group(0).lower()[:30]])
    return ScreenResult(False)

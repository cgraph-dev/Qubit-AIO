"""Bounded, deterministic context compilation (Packet 4B).

Replaces wholesale memory-file injection with a fixed-precedence,
character-budgeted assembly. Packet 5C owns retrieval scoring/reranking;
this module only enforces budgets, precedence, and honest omission records.

Precedence (highest first — dropped LAST):
  persona      immutable instructions; never truncated
  active       conversation/active state
  core_facts   corrected durable facts (user-local storage)
  recall       retrieved/volatile excerpts

Sections are dropped lowest-priority-first when the budget is exceeded.
The result reports exactly what was omitted — never silently.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Rough token estimate for budget math (chars per token, conservative).
CHARS_PER_TOKEN = 3.5

PRECEDENCE: Tuple[str, ...] = ("persona", "active", "core_facts", "recall")

# Bounded to a full identity block of the real user file (~1.2KB) with
# margin; measured Packet 4B: 600 clipped the socials section entirely.
CORE_FACTS_MAX_CHARS = 1600
ACTIVE_MAX_CHARS = 1600
RECALL_MAX_CHARS = 1200

_CORE_FACTS_CANDIDATES = (
    "~/.config/qubit-voicebridge/personal-knowledge.md",
    "~/.config/qubit-voice-agent/personal-knowledge.md",
    "~/.config/openclaw-voice-bridge/personal-knowledge.md",
)

_SECRET_SHAPE = re.compile(
    r"(?im)^\s*[-*]?\s*(?:api[_ ]?key|token|secret|password|passwd|"
    r"authorization)\s*[:=].*$")


def load_core_facts(explicit_path: Optional[str] = None) -> str:
    """Bounded corrected-facts text from user-local storage (never secrets).

    Reads only the user's own config copy; the repo copy is an example and is
    not injected. Secret-shaped lines are stripped defensively.
    """
    candidates: List[Path] = []
    if explicit_path:
        candidates.append(Path(os.path.expanduser(explicit_path)))
    else:
        candidates.extend(Path(os.path.expanduser(p))
                          for p in _CORE_FACTS_CANDIDATES)
    for p in candidates:
        try:
            if not p.exists():
                continue
            text = p.read_text(errors="replace")
            break
        except OSError:
            continue
    else:
        return ""
    text = _SECRET_SHAPE.sub("", text)
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    out: List[str] = []
    used = 0
    for ln in lines:
        if used + len(ln) + 1 > CORE_FACTS_MAX_CHARS:
            break
        out.append(ln)
        used += len(ln) + 1
    return "\n".join(out)


@dataclass
class CompiledContext:
    text: str
    sections_included: List[str] = field(default_factory=list)
    sections_omitted: List[str] = field(default_factory=list)
    est_tokens: int = 0


def compile_context(persona: str, active: str = "",
                    core_facts: str = "", recall: str = "",
                    char_budget: int = 6000) -> CompiledContext:
    """Deterministic bounded assembly; drops lowest priority first."""
    raw: Dict[str, Tuple[str, int]] = {
        "persona": (persona.strip(), len(persona)),
        "active": (active.strip(), min(len(active), ACTIVE_MAX_CHARS)),
        "core_facts": (core_facts.strip(),
                       min(len(core_facts), CORE_FACTS_MAX_CHARS)),
        "recall": (recall.strip(), min(len(recall), RECALL_MAX_CHARS)),
    }
    included: Dict[str, str] = {}
    total = len(raw["persona"][0])
    included["persona"] = raw["persona"][0]
    for name in PRECEDENCE[1:]:
        body, cap = raw[name]
        if not body:
            continue
        body = body[:cap]
        if total + len(body) + 2 <= char_budget or name == "active":
            # active state survives with hard clipping; others drop whole
            if total + len(body) + 2 > char_budget:
                body = body[:max(0, char_budget - total - 2)]
            included[name] = body
            total += len(body) + 2
    omitted = [n for n in PRECEDENCE if n != "persona" and n not in included
               and raw[n][0]]
    parts: List[str] = []
    if "active" in included:
        parts.append(f"[active]\n{included['active']}")
    if "core_facts" in included:
        parts.append(f"<about-the-user>\n{included['core_facts']}\n"
                     "</about-the-user>")
    if "recall" in included:
        parts.append(f"[reference]\n{included['recall']}")
    text = "\n\n".join(parts)
    return CompiledContext(text=text,
                           sections_included=[k for k in PRECEDENCE
                                              if k in included],
                           sections_omitted=omitted,
                           est_tokens=int((len(text) + len(persona))
                                          / CHARS_PER_TOKEN))


def output_reserve_tokens(num_ctx: int, max_output_tokens: int,
                          input_est_tokens: int) -> int:
    """Tokens guaranteed free for the reply within num_ctx."""
    return max(0, num_ctx - max(0, input_est_tokens) - max_output_tokens)


def fits_budget(num_ctx: int, max_output_tokens: int,
                compiled: CompiledContext, min_reserve_ratio: float = 1.5
                ) -> bool:
    """True when compiled input plus required output reserve fits num_ctx.

    Requires headroom of min_reserve_ratio x max_output beyond the request's
    own output cap so prompt drift never eats the reply. Unclamped math:
    overflow must be visible, never hidden by a max(0, ...) floor.
    """
    reserve_needed = int(max_output_tokens * min_reserve_ratio)
    return (compiled.est_tokens + reserve_needed) <= num_ctx

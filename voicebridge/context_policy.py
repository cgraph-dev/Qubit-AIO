"""Dynamic context selection for Qubit chat/voice turns (objective D).

Context size is a latency/resource setting, never a permanent intelligence
limit: a simple voice turn runs with the fast window, a long or tool-heavy
conversation expands within the model's real window, and a request that cannot
be served by the local model's window escalates to a stronger configured
profile instead of silently truncating.

This module is pure/deterministic: it computes a :class:`ContextDecision` from
a transcript and a set of tokens, and builds the bounded prompt. It never opens
a store, a network connection, or a provider. The caller (``chat_transport``)
owns routing, escalation, persistence, audit, and cancellation.

Configuration (``agent.context`` in ``voice.yaml``):

* ``min_tokens``        fast window for simple turns (default 2048)
* ``default_tokens``    window when history/complexity needs more (default 4096)
* ``max_tokens``        largest window before escalation (default 16384)
* ``fast_messages``     rolling window for the fast tier (default 6)
* ``default_messages``  rolling window for the expanded tier (default 16)
* ``max_messages``      hard cap on messages fed to the model (default 48)
* ``escalation_model``  explicit profile id to escalate to (default: the
                        configured API profile via the resolver); empty means
                        "let the caller resolve the default escalation target"
* ``summary_head_chars`` chars kept per older message in the summary

A missing/invalid value falls back to the default; a nonsensical window
relation (min > default > max) is normalized so the fast window never exceeds
the default.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

CHARS_PER_TOKEN = 3.5
#: Reply token reserve added to the prompt estimate before choosing a window.
OUTPUT_RESERVE_TOKENS = 1024
#: Multiplier on the reserve so prompt drift never eats the reply.
RESERVE_HEADROOM = 1.5

TIER_FAST = "fast"
TIER_EXPANDED = "expanded"
TIER_ESCALATED = "escalated"

_CONFIG_DEFAULTS: Dict[str, Any] = {
    "min_tokens": 2048,
    "default_tokens": 4096,
    "max_tokens": 16384,
    "fast_messages": 6,
    "default_messages": 16,
    "max_messages": 48,
    "escalation_model": "",
    "summary_head_chars": 280,
}

#: Words that mark a request as more than a casual voice turn. Deliberately
#: narrow: any hit forces at least the expanded tier, never a silent failure.
_COMPLEX_MARKERS = (
    "analyze", "analyse", "debug", "refactor", "architecture", "compare",
    "summarize", "summarise", "explain in detail", "step by step", "plan",
    "implement", "migrate", "write code", "code review", "investigate",
    "research", "why does", "how does", "walk me through",
)


def _cfg_int(cfg: Any, key: str, default: int, *, minimum: int = 1) -> int:
    try:
        raw = cfg.get("agent", "context", key, default=None)
    except Exception:  # noqa: BLE001 - a Config-less caller uses defaults
        raw = None
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value >= minimum else default


def _cfg_str(cfg: Any, key: str, default: str = "") -> str:
    try:
        raw = cfg.get("agent", "context", key, default=None)
    except Exception:  # noqa: BLE001
        raw = None
    return str(raw).strip() if raw is not None else default


def context_config(cfg: Any) -> Dict[str, Any]:
    """Effective, normalized context policy for this config."""
    out = dict(_CONFIG_DEFAULTS)
    for key in ("min_tokens", "default_tokens", "max_tokens", "fast_messages",
                "default_messages", "max_messages", "summary_head_chars"):
        out[key] = _cfg_int(cfg, key, _CONFIG_DEFAULTS[key])
    out["escalation_model"] = _cfg_str(cfg, "escalation_model", "")
    # Normalize a nonsensical relation rather than trusting a bad config.
    if out["default_tokens"] < out["min_tokens"]:
        out["default_tokens"] = out["min_tokens"]
    if out["max_tokens"] < out["default_tokens"]:
        out["max_tokens"] = out["default_tokens"]
    if out["default_messages"] < out["fast_messages"]:
        out["default_messages"] = out["fast_messages"]
    if out["max_messages"] < out["default_messages"]:
        out["max_messages"] = out["default_messages"]
    return out


@dataclass
class ContextDecision:
    """The deterministic context/routing decision for one turn."""

    tier: str
    window_tokens: int
    message_limit: int
    est_prompt_tokens: int
    total_messages: int
    summarized_messages: int = 0
    requires_escalation: bool = False
    reasons: List[str] = field(default_factory=list)
    policy: Dict[str, Any] = field(default_factory=dict)

    def public(self) -> Dict[str, Any]:
        """Redacted, wire-safe view for a receipt / audit event."""
        return {
            "tier": self.tier,
            "window_tokens": self.window_tokens,
            "message_limit": self.message_limit,
            "est_prompt_tokens": self.est_prompt_tokens,
            "total_messages": self.total_messages,
            "summarized_messages": self.summarized_messages,
            "requires_escalation": self.requires_escalation,
            "reasons": list(self.reasons),
        }


def estimate_tokens(messages: List[Dict[str, Any]]) -> int:
    """Conservative prompt token estimate for a transcript."""
    chars = 0
    for m in messages:
        if not isinstance(m, dict):
            continue
        chars += len(str(m.get("content") or ""))
    return int(math.ceil(chars / CHARS_PER_TOKEN))


def _is_instruction(msg: Dict[str, Any]) -> bool:
    role = str(msg.get("role") or "")
    if role == "system":
        return True
    content = str(msg.get("content") or "").lower()
    return any(marker in content for marker in (
        "always ", "never ", "remember that", "important:", "rule:",
        "guideline", "must not", "do not"))


def _looks_complex(messages: List[Dict[str, Any]], cfg_tokens: Dict[str, Any]
                   ) -> Optional[str]:
    if len(messages) > int(cfg_tokens["fast_messages"]):
        return f"history>{cfg_tokens['fast_messages']}"
    for m in messages:
        role = str(m.get("role") or "")
        if role == "tool" or role == "tool_call" or m.get("tool_name"):
            return "tool-results"
        content = str(m.get("content") or "")
        if len(content) > cfg_tokens["min_tokens"] * CHARS_PER_TOKEN:
            return "long-message"
        low = content.lower()
        if any(marker in low for marker in _COMPLEX_MARKERS):
            return "complex-request"
    return None


def decide_context(cfg: Any, messages: List[Dict[str, Any]],
                   *, model_context_tokens: Optional[int] = None
                   ) -> ContextDecision:
    """Choose the context tier/window for a turn.

    ``model_context_tokens`` is the resolved profile's declared window. When it
    is known and the required prompt does not fit, the decision escalates
    instead of truncating. When it is unknown (0/None) the policy caps at
    ``max_tokens`` and reports that the window was unverified.
    """
    pol = context_config(cfg)
    msgs = [m for m in messages if isinstance(m, dict)]
    est = estimate_tokens(msgs)
    total = len(msgs)
    reserve = int(OUTPUT_RESERVE_TOKENS * RESERVE_HEADROOM)
    need = est + reserve
    reasons: List[str] = []

    complex_reason = _looks_complex(msgs, pol)
    if complex_reason:
        reasons.append(complex_reason)

    # Pick the smallest window that fits the need.
    if not complex_reason and need <= pol["min_tokens"]:
        tier, window = TIER_FAST, pol["min_tokens"]
        reasons.append("fits-fast-window")
    elif need <= pol["default_tokens"]:
        tier, window = TIER_EXPANDED, pol["default_tokens"]
        reasons.append(complex_reason or "fits-default-window")
    elif need <= pol["max_tokens"]:
        tier, window = TIER_EXPANDED, pol["max_tokens"]
        reasons.append("fits-max-window")
    else:
        tier, window = TIER_ESCALATED, pol["max_tokens"]
        reasons.append("exceeds-max-window")

    requires_escalation = tier == TIER_ESCALATED
    real_window = int(model_context_tokens or 0)
    if real_window > 0:
        if need > real_window:
            requires_escalation = True
            tier = TIER_ESCALATED
            window = max(window, min(real_window, pol["max_tokens"]))
            reasons.append("needs-model-window")
        else:
            # The model window is the hard ceiling; never request more.
            window = min(window, real_window)
    else:
        reasons.append("window-unverified")

    # Rolling window: instruction/system messages are always retained; the
    # message_limit bounds how many *conversational* messages enter the prompt.
    limit = {"fast": pol["fast_messages"],
             "expanded": pol["default_messages"],
             "escalated": pol["max_messages"]}[
                 TIER_FAST if tier == TIER_FAST else
                 (TIER_EXPANDED if tier == TIER_EXPANDED else TIER_ESCALATED)]
    # ``build_context_prompt`` always keeps instructions outside the rolling
    # window, so count the summary against conversational turns only — the two
    # functions must agree exactly.
    conversational = [m for m in msgs if not _is_instruction(m)]
    summarized = max(0, len(conversational) - limit)
    return ContextDecision(
        tier=tier, window_tokens=int(window), message_limit=int(limit),
        est_prompt_tokens=int(est), total_messages=total,
        summarized_messages=int(summarized),
        requires_escalation=requires_escalation, reasons=reasons, policy=pol)


def deterministic_summary(messages: List[Dict[str, Any]], *,
                          head_chars: int = 280) -> str:
    """Deterministic extractive summary of older messages (no model call).

    Instruction/system messages and tool outcomes are kept in full (they carry
    the active constraints); other turns contribute their leading text. The
    output is stable for a given transcript, so a re-run yields the same prompt.
    """
    parts: List[str] = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role") or "")
        content = str(m.get("content") or "").strip()
        if not content and not m.get("tool_name"):
            continue
        if _is_instruction(m):
            label = "instruction"
            body = content
        elif role == "tool" or m.get("tool_name"):
            label = f"tool:{m.get('tool_name') or 'result'}"
            body = content
        else:
            label = "user" if role == "user" else "qubit"
            body = content[:head_chars]
        parts.append(f"- ({label}) {body}".rstrip())
    return "\n".join(parts)


def build_context_prompt(messages: List[Dict[str, Any]],
                         decision: ContextDecision, *,
                         model_label: str = "Qubit") -> Tuple[str, Dict[str, Any]]:
    """Build the bounded prompt context and its metadata.

    Returns ``(text, meta)``. ``meta`` reports exactly what was summarized so
    the caller can persist/audit it — never a silent drop.
    """
    msgs = [m for m in messages if isinstance(m, dict)]
    head_chars = int(decision.policy.get("summary_head_chars", 280))
    # Keep every instruction/system message regardless of the rolling window.
    instructions = [m for m in msgs if _is_instruction(m)]
    conversational = [m for m in msgs if not _is_instruction(m)]

    limit = max(1, int(decision.message_limit))
    recent = conversational[-limit:] if limit else []
    older = conversational[:-limit] if len(conversational) > limit else []

    blocks: List[str] = []
    if instructions:
        instruction_text = "\n".join(
            str(m.get("content") or "").strip() for m in instructions
            if str(m.get("content") or "").strip())
        blocks.append("[instructions]\n" + instruction_text)
    if older:
        summary = deterministic_summary(older, head_chars=head_chars)
        if summary:
            blocks.append("[summary-of-earlier-conversation]\n" + summary)
    if recent:
        lines = []
        for m in recent:
            role = "User" if m.get("role") == "user" else model_label
            lines.append(f"{role}: {str(m.get('content') or '')[:1200]}")
        blocks.append("\n".join(lines))

    text = "\n\n".join(blocks)
    meta = {
        "tier": decision.tier,
        "window_tokens": decision.window_tokens,
        "message_limit": decision.message_limit,
        "instructions_kept": len(instructions),
        "recent_messages": len(recent),
        "summarized_messages": len(older),
        "est_prompt_tokens": decision.est_prompt_tokens,
    }
    return text, meta


def escalation_target(cfg: Any, resolved: Dict[str, Any]) -> Optional[str]:
    """Explicit escalation profile id for this config, if configured.

    Returns ``None`` when the caller must decide (no pinned target). Never
    fabricates a model: an empty config means the caller resolves the
    configured API default or returns a typed refusal.
    """
    pinned = _cfg_str(cfg, "escalation_model", "")
    if pinned:
        return pinned
    return None


__all__ = [
    "CHARS_PER_TOKEN", "OUTPUT_RESERVE_TOKENS", "RESERVE_HEADROOM",
    "TIER_FAST", "TIER_EXPANDED", "TIER_ESCALATED",
    "ContextDecision", "context_config", "decide_context",
    "deterministic_summary", "build_context_prompt", "escalation_target",
    "estimate_tokens",
]

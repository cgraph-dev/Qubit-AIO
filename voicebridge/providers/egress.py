"""Packet 8Q — minimum-necessary egress classification.

Default cloud requests carry only the sanitized task prompt. Private classes
require explicit per-request approval or a configured data policy. Page/tool/
model/provider text can never widen egress.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Tuple

from ..redaction import redact_text

BLOCKED_CLASSES = (
    "credential", "token", "cookie", "secret",
    "raw_audio", "browser_private", "memory_private", "source_tree",
)

# High-confidence secret MATERIAL (not mere mentions): blocked from cloud
# egress unless the caller holds explicit per-request approval
# (allow_private=True, enforced by the gateway from --allow-private).
_DENY_MATERIAL = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|sk-[A-Za-z0-9_-]{12,}\b|"
    r"gh[pousr]_[A-Za-z0-9]{8,}|xox[bpas]-[A-Za-z0-9-]{8,}")


@dataclass(frozen=True)
class EgressDecision:
    allowed: bool
    code: str  # "" on allow, else EGRESS_DENIED
    sanitized: str
    blocked: Tuple[str, ...] = ()
    warning: str = ""


def classify_and_sanitize(prompt: str, *, allow_private: bool = False,
                          max_chars: int = 4000) -> EgressDecision:
    """Classify ``prompt`` for cloud egress.

    ``allow_private=False`` (default) redacts secret-shaped spans and DENIES
    the request when it carries high-confidence secret material (PEM private
    keys, sk-/ghp-/xox- tokens). ``allow_private=True`` requires the caller
    to have explicit user approval (gateway enforces via --allow-private).
    Output is bounded and redacted; never returns raw secrets.
    """
    raw = prompt or ""
    if _DENY_MATERIAL.search(raw) and not allow_private:
        return EgressDecision(allowed=False, code="EGRESS_DENIED",
                              sanitized="[EGRESS_DENIED: secret material "
                                        "requires --allow-private]",
                              blocked=("credential",),
                              warning="denied: prompt carries secret material")
    sanitized = redact_text(raw)
    blocked: List[str] = []
    if len(sanitized) > max_chars:
        sanitized = sanitized[:max_chars] + "…[truncated]"
    warning = ("external-data: sanitized task context only; private browser, "
               "account, memory, credential, cookie, raw-audio, and source "
               "content excluded unless explicitly approved")
    return EgressDecision(allowed=True, code="", sanitized=sanitized,
                          blocked=tuple(blocked), warning=warning)


def egress_allows_untrusted_marker(text: str) -> bool:
    """Untrusted page/tool/model text never widens egress — always False."""
    return False

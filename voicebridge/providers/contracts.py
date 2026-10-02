"""Packet 8Q — normalized provider contract.

One typed request/response surface for local, direct frontier, OpenCode Zen,
and OpenRouter paths so callers cannot mistake a local fallback for a cloud
answer and capability mismatches fail closed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

RouteName = Literal["local", "frontier", "auto"]

# Typed failure codes (wire-stable, redacted).
FAIL_CODES = frozenset({
    "CLOUD_DISABLED",
    "NOT_CONFIGURED",
    "AUTH_MISSING",
    "AUTH_INVALID",
    "UNKNOWN_MODEL",
    "STALE_CATALOG",
    "UNSUPPORTED_CAPABILITY",
    "RATE_LIMITED",
    "TIMEOUT",
    "PROVIDER_OUTAGE",
    "CONTENT_REFUSED",
    "CANCELLED",
    "OVER_BUDGET",
    "CIRCUIT_OPEN",
    "EGRESS_DENIED",
    "MALFORMED_RESPONSE",
    "MODEL_UNAVAILABLE_TURN",
    "RESPONSE_TOO_LARGE",
    "LOCAL_FALLBACK",
})


@dataclass(frozen=True)
class ProviderRequest:
    prompt: str
    max_tokens: int = 100
    timeout_s: float = 20.0
    route: str = "local"  # local | frontier | auto (validated by gateway)
    provider: str = ""    # explicit provider id, "" = policy default
    model: str = ""       # explicit model id, "" = policy default
    structured_schema: Optional[Dict[str, Any]] = None
    tools: Optional[List[Dict[str, Any]]] = None
    require_tools: bool = False
    require_streaming: bool = False
    idempotency_key: str = ""
    context_chars: int = 0  # sanitized input size hint for budget checks


@dataclass(frozen=True)
class UsageRecord:
    provider: str
    model: str
    identity: str  # "<provider>/<model>" — truthful source of the text
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    cost_usd_est: float = 0.0
    fallback: bool = False
    fallback_reason: str = ""


@dataclass
class ProviderResult:
    ok: bool
    text: str = ""
    identity: str = "local/qubit-brain4"
    provider: str = "local"
    model: str = "qubit-brain4"
    code: str = ""  # "" on success, else one of FAIL_CODES
    message: str = ""
    usage: Optional[UsageRecord] = None
    details: Dict[str, Any] = field(default_factory=dict)

    def to_public_dict(self) -> Dict[str, Any]:
        """Redacted, machine-readable form for CLI/dashboard output."""
        from ..redaction import redact_obj
        return redact_obj({
            "ok": self.ok,
            "text": self.text[:2000],
            "provider": self.provider,
            "model": self.model,
            "identity": self.identity,
            "code": self.code,
            "message": self.message[:500],
            "usage": {
                "provider": self.usage.provider if self.usage else self.provider,
                "model": self.usage.model if self.usage else self.model,
                "identity": self.usage.identity if self.usage else self.identity,
                "prompt_tokens": self.usage.prompt_tokens if self.usage else 0,
                "completion_tokens": self.usage.completion_tokens if self.usage else 0,
                "latency_ms": self.usage.latency_ms if self.usage else 0,
                "cost_usd_est": self.usage.cost_usd_est if self.usage else 0.0,
                "fallback": self.usage.fallback if self.usage else False,
                "fallback_reason": (self.usage.fallback_reason if self.usage else "")[:200],
            },
        })

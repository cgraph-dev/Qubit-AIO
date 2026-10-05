"""Jev decision-layer client (prep lane, mock-first).

Jev is reached via OpenRouter (owner decision) — NOT TypeSafe direct, NOT
Zen — on the alpha decisions endpoint with the native-dialect body. No
OPENROUTER_API_KEY exists on this host yet, so the mock backend carries
all tests; the live path fails CLOSED without a key (never silent, never
fabricated).

New files only; nothing here is wired into any caller yet. See
:mod:`voicebridge.jev.seams` for the integration-point map.
"""
from __future__ import annotations

from .client import (
    DECISIONS_URL,
    DEFAULT_MODEL,
    DEFAULT_TIMEOUT_S,
    KEY_NAME,
    judge,
    key_fingerprint,
    key_present,
    mock_judge,
    parse_native_answers,
    to_native_body,
)

__all__ = [
    "DECISIONS_URL",
    "DEFAULT_MODEL",
    "DEFAULT_TIMEOUT_S",
    "KEY_NAME",
    "judge",
    "key_fingerprint",
    "key_present",
    "mock_judge",
    "parse_native_answers",
    "to_native_body",
]

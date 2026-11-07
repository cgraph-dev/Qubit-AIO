"""Jev decision layer — two doors, one contract.

Jev is a bespoke typed-decision API, not an OpenAI chat-completions model, so it
cannot be routed through any chat relay. There are two working doors:

* **Zen** (:mod:`voicebridge.jev.zen`) — ``https://opencode.ai/zen/v1/systemone``
  with ``jev-1.13-free``. **No credential, cost 0**, verified live. This is the
  default: a fresh install can open it with nothing but network access.
* **OpenRouter alpha** (:mod:`voicebridge.jev.client`) — needs
  ``OPENROUTER_API_KEY`` and paid credit. Kept for a paid/managed account; it
  fails CLOSED without a key.

The two differ in body shape (a question *list* vs a keyed *object*, with
``noul`` / ``choice`` / ``score`` types) and in auth (Zen's free tier rejects any
Authorization header). Both parse back to the same typed verdicts, so callers do
not care which answered.

:mod:`voicebridge.jev.advice` wires the first consumer: an advisory verdict on
genuinely ambiguous turns, opt-in and never applied. See
:mod:`voicebridge.jev.seams` for the remaining integration points.
"""
from __future__ import annotations

from . import zen
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
    "zen",
]

"""Packet 8T-F — adapted Hermes-based agent runtime (Qubit-owned execution).

Implements the pinned Hermes CLI turn/session contract
(`hermes chat -q TEXT -Q [--continue SESSION]`, hermes-agent v2026.9.7 @
2237be3 — contract shape only, no upstream code vendored) as a Qubit-started,
Qubit-supervised worker subprocess. All policy stays Qubit-side: the broker
gates dispatch, PowerControl owns emergency stop, receipts/audit are Qubit
append-only, and secrets are redacted at every boundary.
"""
from .runtime import HermesRuntime, RuntimeDenied, RuntimeError

__all__ = ["HermesRuntime", "RuntimeDenied", "RuntimeError"]

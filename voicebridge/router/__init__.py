"""Packet 4C: deterministic (L0) + optional semantic (L1) routing.

Production integration is FEATURE-FLAGGED (intent.router.enabled, default
false). Uncertainty NEVER becomes authority: L1 returns an explicit no-match
and the existing classifier/LLM path stays the fallback.
"""

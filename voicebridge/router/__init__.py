"""Packet 4C: deterministic (L0) + optional semantic (L1) routing.

Production integration is FEATURE-FLAGGED (intent.router.enabled, default
false). Uncertainty NEVER becomes authority: L1 returns an explicit no-match
and the existing classifier/LLM path stays the fallback.

L1.5 (intent.router.l1_5_enabled, default false) is a separate registry
augmenter consulted only after L0 declined and voice_commands.resolve()
returned UNKNOWN. It refuses pending confirmations and can never decide a
confirmation-gated or high-risk entry.
"""

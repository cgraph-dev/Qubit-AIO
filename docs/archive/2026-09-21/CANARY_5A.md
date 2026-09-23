# Opt-in Q5 canary (Packet 5A companion to the 4D handoff)

Purpose: measure qubit-cand-q5 in production-shaped conditions WITHOUT
touching the production alias qubit-brain4. No promotion logic exists.

Flag: `intent.canary.q5_enabled` (default false → canary is inert: zero
logging, zero behavior change, production alias never referenced).
Runner: `scripts/canary_run_4d.py --turns N [--force] [--no-tts]`.

RESIDENCY SAFETY (found-and-fixed during first live run): loading the 3.6 GB
Q5 beside the 3.2 GB production model on the 8 GB GPU silently evicted
qubit-brain4. The runner now wraps every canary session in a deep-tier cycle
(voicebridge.vram_guard.deep_tier_cycle): production is evicted ONLY for the
canary window, the canary alias runs, then production is restored at
canonical options with a warm probe. The restore report is part of
rollback_state, and the runner exits non-zero if restoration fails (rule 32).

Recorded per turn (JSONL under state_dir/canary/q5_canary.jsonl, mode 0700):
alias, warm/cold (from /api/ps), latency_ms, errors (classify/planner/chat
typed), planner_decision (tool|none), safety_denied (destructive screen),
audio_failure (piper synth exceptions), rollback_state (flag value,
production alias name, production digest observed vs baseline — never
loaded/modified).

Rollback: flip flag false + delete the log; nothing else exists to undo.

Safety: CANARY_ALIAS="qubit-cand-q5" vs PRODUCTION_ALIAS="qubit-brain4" are
distinct constants; the runner only ever loads the canary alias; production
digest is observed via /api/show (read-only) and recorded per turn.

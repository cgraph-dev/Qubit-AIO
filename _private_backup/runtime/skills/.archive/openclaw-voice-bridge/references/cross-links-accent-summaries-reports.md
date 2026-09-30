# Cross-links — accent, summaries, full reports, salvage (Aug 2026)

The voice bridge's accent/broken-English tolerance, bulletproof summaries, and crash-proof full reports are now documented in the `local-voice-stack` skill to avoid duplicating a class-level concern:

- Accent & broken-English routing (parakeet beam 4, `llm_classifier: true`, `normalize_broken_english`, `_BROKEN_MAP`, `_FUZZY_TASK_VERBS`) — see `local-voice-stack: references/accent-broken-english-routing.md`
- Bulletproof summary + 40-word cap and raw full-report persistence (`_persist_full_report`, `reply[:50000]`, `<state_dir>/reports/`) — see `local-voice-stack: references/bulletproof-summaries-and-full-reports.md`
- Non-crashing waiter salvage (partial reply on `wait_run` failure) — see `local-voice-stack: references/non-crashing-waiter-salvage.md`
- Voice-bridge-specific dispatch observability (every-path `events.jsonl` + dashboard mapping) stays in `references/openclaw-dispatch-observability.md` (this skill).

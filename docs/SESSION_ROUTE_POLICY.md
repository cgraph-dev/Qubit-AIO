# Conversation-only fallback opt-out

A session can set `session_fallback_disabled: true` in its existing
`sessions.model_config` JSON using `SessionDB.patch_session_model_config`.
This is not a profile configuration option. Other sessions and cron jobs keep
their existing fallback chains.

The policy is read from the agent's own database and conversation ID:

- TUI/Desktop/dashboard agent construction disables auth-resolution fallback
  and passes an empty fallback chain before client construction.
- Per-turn fallback refresh cannot restore the profile chain for a locked session.
- `try_activate_fallback` rechecks the persisted policy before binding any route,
  including when a cached agent still holds an older chain.
- Existing runtime persistence merges the policy key, so resume retains it.

Opt back in by removing the key or setting it to `false`. This does not forbid
an explicit user model change; it forbids automatic provider/model fallback.
It does not rewrite auxiliary-model configuration.

Focused verification (no provider calls):
`PYTHONPATH="$PWD/qubit_runtime" .venv/bin/python -m pytest qubit_runtime/tests/agent/test_session_route_policy.py -q`

Evidence: 5 checks passed: session/store isolation, persistence/reopen, rate-limit
and billing switch prevention, auth-resolution switch prevention, and per-turn
refresh isolation. No broad suite or release/parity claim was made.

The active continuation conversation `20261014_103355_361ebb` was opted out and
persisted as `gpt-6.1-sol` / `openai-api`. Profile config was verified byte-unchanged.
The original failed conversation `20261014_100819_41f112` was not modified.

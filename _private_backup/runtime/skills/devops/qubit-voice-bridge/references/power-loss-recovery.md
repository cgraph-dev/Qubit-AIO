# Power-loss recovery — 2026-08-23

**Symptom:** PC power cut deleted in-memory `ConversationState`, pending tasks, and recent dashboard history. User reported work was gone after shutdown.

**Durability changes:**
- `SharedMemory` now `fsync`s after each `shared_memory.jsonl` write — survives power loss (not just clean `/quit`).
- Reports persist per-session to `<state_dir>/reports/<session>.md` regardless of journal pruning.
- `VoiceOrchestrator.__init__` replays `shared_memory.jsonl` into `ConversationState` (up to `max_turns` / 8 turns) and logs `shared_memory.restored`. So even if `events.jsonl` is stale, context returns.
- `sticky_route.json` survives reboot (reloaded in `StickyRouteState._load`).

**What is NOT recovered automatically:** actively running Qubit/Qubit tasks mid-crash — they must be re-issued after boot. But history, routing preference, and reports do come back.

**Check:** after a hard power-off, `cat ~/.local/state/openclaw-voice-bridge/shared_memory.jsonl | tail`, trigger `voicebridge` and confirm `/api/status` → `shared_items > 0` and dashboards show prior turns.

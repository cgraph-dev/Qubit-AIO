# Durable routing + shared memory — 2026-08-23

**Problem:** `route to qubit` was stateless. Next utterance forgot it (3B LLM has no memory across turns). Switching Qubit ↔ Qubit blanked dashboards/history. Power loss wiped `ConversationState`.

**Fix:**
- `voicebridge/intent/state.py:StickyRouteState` + `Classifier._sticky_route()` + `STICKY_PATTERNS` — `route to {qubit|openclaw|browser}` / `use {qubit|...}` / `clear route`. Persists to `<state_dir>/sticky_route.json` (survives reboot), auto-expires 8 turns / 600s. Applied in `VoiceOrchestrator._route_intent` *before* templates/LLM; bumps per turn. Handles `route to qubit and do X` — strips prefix, persists, dispatches remainder.
- `voicebridge/shared_memory.py:SharedMemory` — append-only `shared_memory.jsonl` with `fsync` per line. Every `append_user` / `append_agent_report` / `append_vscode`. `as_prompt(max_chars=4000)` injected into next Qubit/Qubit task so neither lane blanks. Replayed into `ConversationState` on boot. Merged in `/api/reports` + `/api/activity`. `/api/status` exposes `sticky_route` + `shared_items`.
- VS Code: `VSCODE_CWD` / `TERM_PROGRAM=vscode` captured into shared ledger on each `_launch_task` / Qubit dispatch.
- Dashboards badge sticky (`reports.html` / `activity.html` via `/api/status`).

**Verify:** `py_compile`, `pytest tests/ -q` (65 passed), check `~/.local/state/openclaw-voice-bridge/shared_memory.jsonl` + `sticky_route.json` after `route to qubit`, reboot and confirm replay.

**Pitfall:** Don't infer sticky from vague chat — only `STICKY_PATTERNS` trigger persistence.

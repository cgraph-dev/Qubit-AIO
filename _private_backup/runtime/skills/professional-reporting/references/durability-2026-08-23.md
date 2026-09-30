# Durability notes — 2026-08-23

Reports + memory must survive the three things that actually blank sessions
on this box: power loss, provider switch (Qubit ↔ Qubit ↔ VS Code), and
re-typing a daily command slightly differently.

*   **Shared ledger:** `~/.local/state/openclaw-voice-bridge/shared_memory.jsonl` —
    append-only, `fsync` per line. Fields `role/text/route/provider/session_key`.
    Helpers: `SharedMemory.append_user/append_assistant/append_agent_report/append_vscode`,
    `SharedMemory.recent()` and `as_prompt(max_chars, max_turns)` for the next
    agent prompt. On boot the bridge replays the last 8+ turns into
    `ConversationState` so the 3B brain sees context even after the PC shut off.

*   **Reports:** `<state_dir>/reports/<session>.md` per task (`_persist_full_report`),
    plus `SharedMemory` entry. `/api/reports` merges ActivityFeed + ledger +
    `events.jsonl` and tags categories. Never delete `shared_memory.jsonl` on
    deploy — it's the recovery source.

*   **Sticky routing:** `StickyRouteState` + `Classifier._sticky_route()` +
    `_route_intent` + `sticky_route.json` (8 turns / 10 min, fsync). Explicit
    `route to qubit/openclaw/browser` persists; `clear route` resets. Shown
    on `/api/status` as `sticky_route`. That and the `Active routing
    preference: route to X` hint injected into `_mem_ctx` keep the stateless 3B
    consistent for the next turn.

*   **VS Code workspace:** `VSCODE_CWD` / `TERM_PROGRAM=vscode` → `append_vscode`
    on each dispatch; included in `as_prompt()` so the next agent knows the
    workspace even without a VS Code extension.

*   **Template bypass:** `config/task-templates.yaml` regex wins over LLM
    classifier — daily tasks (gmail/messenger/instagram/tiktok/weather) stay
    deterministic and fast. That pattern is worth keeping even after LoRA.

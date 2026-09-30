---
name: vscode-memory
description: Make VS Code tasks and terminal output survive provider switches and reboots — same durable ledger as voice/openclaw/qubit.
---

# VS Code + Qubit + Qubit — unified durable memory

The bridge's `SharedMemory` ledger (`~/.local/state/openclaw-voice-bridge/shared_memory.jsonl`) is the **single source of truth** across voice, web dashboards, Qubit, Qubit, and VS Code:

- Every voice turn, agent report, routing decision, and VS Code workspace capture is appended with `fsync` (survives power loss).
- On bridge startup, `shared_memory.jsonl` is replayed into the conversation window so the local LLM sees prior context even after a reboot.
- `/api/reports` and `/api/activity` merge the ledger so the dashboards show the same history regardless of which provider (Qubit vs Qubit) did the work.
- `/api/status` exposes `sticky_route` and `shared_items` so dashboards can display "Routing to Qubit" and history depth live.

## VS Code integration

When the bridge runs inside VS Code (`TERM_PROGRAM=vscode` or `VSCODE_CWD` set), it automatically:

1. Captures the workspace path (`VSCODE_CWD` or `cwd`) into shared memory on each task dispatch (openclaw/qubit).
2. Injects the recent shared history (`[user]: ...`, `[agent/qubit]: summary`) into the NEXT agent prompt — so switching from Qubit → Qubit doesn't blank "what we were working on".

To get VS Code terminal tasks into the same ledger, run tasks through the bridge's terminal or `code` CLI — or manually append:

```python
from voicebridge.shared_memory import SharedMemory
from pathlib import Path
SharedMemory(Path.home()/".local/state/openclaw-voice-bridge").append_vscode("VS Code task: ran `npm test` — 12 passed", workspace=str(Path.cwd()))
```

Provider switches: say `route to qubit`, `route to openclaw`, `route to browser`, or `clear route` in voice or the dashboard typed box. The preference is persisted to `sticky_route.json` (survives restart) and auto-expires after 8 turns / 10 min.

Stale `sticky_route.json` cleanup: `POST /api/reports/prune` and bridge expiry handle it; dashboards show sticky via `/api/status`.

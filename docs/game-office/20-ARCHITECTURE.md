# 20 — Architecture

## Layers

```
┌──────────────────────────────────────────────────────────────────────┐
│  TOWER (view)         frontend/qubit-app/plugins/qubit-office/       │
│                       isometric 2D Phaser: rooms + procedural agents  │
│                       (+ phase-2 GLB characters at the same coords) │
│                       reads: office_event (WS), office store (HTTP)  │
├──────────────────────────────────────────────────────────────────────┤
│  ORG (model)          voicebridge/office/org.py                      │
│                       floors/rooms/seats/agents/businesses + P&L     │
├──────────────────────────────────────────────────────────────────────┤
│  SCHEDULER (when)     voicebridge/office/runner.py + cron jobs       │
│                       admission → turn → ledger → events → stage     │
├──────────────────────────────────────────────────────────────────────┤
│  LEDGER (truth)       voicebridge/office/ledger.py  ← append-only    │
│                       every dollar in and out, attributed            │
└──────────────────────────────────────────────────────────────────────┘
        │                        │                        │
        ▼                        ▼                        ▼
   existing Qubit          existing Qubit           existing Qubit
   agent core              confirmation             stores & tools
   (run_agent_turn)        journal + e-stop         (session, kanban,
                                                    ops broker, MCP,
                                                    skills, memory)
```

**Nothing new below the ORG line.** The office layer is a client of the agent core, not a
replacement for it (D9).

## Where each piece lives in this repo

| Concern | Path | Precedent it follows |
|---|---|---|
| Store, ledger, org | `voicebridge/office/{store,ledger,org}.py` | `voicebridge/kanban.py` (SQLite + `_migrate` + `_audit`), `voicebridge/task_ledger.py` |
| Runner + budget | `voicebridge/office/runner.py` | rest of `voicebridge/*` module style |
| CLI | `voicebridge/cli.py` → `qubit office …` | `qubit kanban …` subparser pattern |
| Dashboard API | `voicebridge/qubit_compat.py` handlers + `voicebridge/dashboard_ops.py` ops | every existing compat handler |
| Tower view | `frontend/qubit-app/plugins/qubit-office/` | `qubit-boards`, `qubit-ops` overlay manifests |
| Static assets | `frontend/qubit-app/plugins/qubit-office/assets/` | new; resolved via the overlay plugin path |
| Floorplans | `frontend/qubit-app/plugins/qubit-office/floorplans/*.json` | new |
| Cron jobs | existing `qubit cron` entries | `qubit_runtime/cron/scheduler.py` |
| Docs | `docs/game-office/` | this directory |

**Overlay vs vendored.** The tower is an overlay plugin, which means it is served from
`frontend/qubit-app/plugins/` and never touches a vendored file. Wiring three things:

1. `manifest.json` with `tab.path = "/qubit-office"` and `source: "qubit-overlay"` (see
   `55-DASHBOARD-PAGE.md` for the exact manifest and layout).
2. The route added to `SPA_ROUTE_PATHS` in `voicebridge/webui.py` (~line 868) — that tuple is the
   single source of truth for which public paths render the SPA shell.
3. Static asset serving: `webui._serve_qubit_plugin()` already resolves overlay plugin assets via
   `resources.resource_path("frontend/qubit-app/plugins")` and serves `.js`/`.json`/`.css`. **Tile
   PNGs and phase-2 `.glb` files are not in that suffix allowlist and would 404** — the allowlist
   must be extended, with the existing path-confinement check kept ahead of it.

## Data flow for one rendered frame

```
agent turn (M1)
   └─ tool call ──► office_event(kind=tool_call, room_id) ──► WS /api/events
                                                              │
                                                              ▼
                                                    renderer walks the character
                                                    to the room that owns that tool
```

The renderer keeps a per-agent state machine driven by the event stream — the same shape as
`AgentRuntime` in pixel-agents, and the same shape as this repo's `EventHub` subscriber contract
(cursor, gap handling, resync). A view that falls behind resyncs from its last `office_event.id`
cursor; it never guesses.

## Sync direction

**Store → view. One way.** The view never writes org state directly; every interaction is either
(a) a read, or (b) a staged op through the confirmation journal. This is what makes the tower
unable to lie (D14) and keeps the e-stop a single gate.

## Security and reachability (this repo's existing rules apply)

- Same single listener, same session token, same auth gate as every other dashboard route.
- No second server, no second store, no second token scheme.
- Mutations go through `dashboard_ops` with the shared confirmation journal and e-stop
  (`_estop` fail-closed).
- Redaction: `office_event.detail` and every ledger `note` pass the shared redactor before
  landing — agent output can contain anything.
- The tower's read endpoints are additive to `QUBIT_READ_AUTHORITY` if any path overlaps a
  vendored route.

## Why not a separate app

A separate app would need its own auth, its own store, its own event stream, and its own
confirmation path — four new surfaces that all have to agree with the existing ones. That is the
split-brain failure class this repo has already paid for repeatedly (cron, skills, pairing,
channels, sessions). The tower is a tab because a tab cannot drift from the bridge it is drawn in.

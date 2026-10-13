# 55 — The Dashboard Page ("Qubit Office")

> **Status 2026-10-12 — built (M4).** The tab exists at
> `frontend/qubit-app/plugins/qubit-office/` and reads `GET /api/office/tower`.
>
> **Two deviations from the illustrative manifest in this file, both recorded
> rather than silently taken:**
>
> 1. **`css: null`, and no `qubit-office.css`.** The tower styles inline in the
>    bundle, so there is no build step and no second file to keep in sync. A
>    `css` entry pointing at a file that does not exist would be a broken tab,
>    not a style, so `tests/test_office_tower_plugin.py` now refuses one.
> 2. **`"position": "end"`**, not `"after:qubit-boards"`. The SPA merges the
>    static fleet *before* the API list, so a fleet-delivered tab cannot anchor
>    to another overlay plugin — the anchor misses and the tab is appended
>    anyway, silently. `end` says what actually happens, and a test now asserts
>    the position either resolves to a built-in route or is explicitly `end`.
> 3. **The plugin is listed in both `frontend/qubit-web/public/plugins/fleet.json`
>    and `frontend/qubit-dist/plugins/fleet.json`.** The sidebar is built from
>    that static fleet *plus* `GET /api/dashboard/plugins`; an overlay tab in
>    only one of the two lists is one cache away from being invisible — which is
>    exactly how this tab first shipped. The two copies are asserted identical.
>
> **Not built, and stated rather than implied:** no isometric projection, no
> camera rig, no sprite art (phase 1 is a flat plan view with code-drawn
> agents — nothing in M4's proof depends on the projection), and no WebSocket
> (the page polls a read route; a second transport before the first is needed
> is how C3's "no second loop" gets eroded).

The tower is a **page in the Qubit dashboard sidebar**, not a standalone app. Directory and this
doc use "Qubit Office" (working name; rename freely).

## Why a tab and not an app

A separate app would need its own auth, store, event stream, and confirmation path — four new
surfaces that must all agree with the existing ones. That is the split-brain failure class this
repo has already paid for in cron, skills, pairing, channels, and sessions. **A tab cannot drift
from the bridge it is drawn in.**

## The four wiring points

| # | What | Where | Note |
|---|---|---|---|
| 1 | Sidebar tab + route | `frontend/qubit-app/plugins/qubit-office/manifest.json` | Overlay plugin — never edits a vendored file |
| 2 | Public route renders the shell | `voicebridge/webui.py` → `SPA_ROUTE_PATHS` (~line 868) | That tuple is the single source of truth for which public paths render the SPA |
| 3 | Static assets served | `voicebridge/webui.py::_serve_qubit_plugin` (~line 1607) | **Suffix allowlist must be extended** — see below |
| 4 | Backend API | `voicebridge/qubit_compat.py` handlers + `voicebridge/dashboard_ops.py` ops | Same listeners, same token, same e-stop, same journal |

### Manifest

```json
{
  "name": "qubit-office",
  "label": "Office",
  "description": "Qubit's agent corporation — departments as floors, teams as rooms, live P&L.",
  "icon": "building-2",
  "version": "0.1.0",
  "tab": { "path": "/qubit-office", "position": "after:qubit-boards" },
  "entry": "qubit-office.js",
  "css": "qubit-office.css",
  "has_api": true,
  "source": "qubit-overlay"
}
```

`position` uses the existing `after:<pathSegment>` grammar, so the tab lands wherever the owner
wants it in the left rail without touching the sidebar component.

### The asset-serving gap (this is the one that will bite)

`_serve_qubit_plugin` currently allows only `.js`, `.json`, `.css`:

```python
and resolved.suffix in (".js", ".json", ".css")
```

A tower needs `game.js`, `game.css`, **tile PNGs**, and phase-2 `.glb` files. Two changes:

1. Extend the suffix tuple to include `.png`, `.webp`, `.glb`, `.bin`, `.ktx2` (as needed).
2. **Keep the existing confinement check** (`plugdir.resolve() in resolved.parents`) — it is the
   only thing stopping `../` traversal, and it must stay ahead of any new suffix.
3. Add the matching content types (`.glb` → `model/gltf-binary`, `.bin` → `application/octet-stream`,
   `.ktx2` → `image/ktx2`).

Note the separate, already-complete allowlist at
`qubit_cli/web_routers/dashboard_ui.py::_PLUGIN_ASSET_CONTENT_TYPES` — it already covers images
and fonts but **not** `.glb`/`.bin`, and it serves from a plugin's `dashboard/` dir via a
different route. Decide which of the two paths the tower's assets ride, and extend only that one.

## Page layout

```
┌──────────────────────────────────────────────────────────────────────────┐
│  Qubit Office          [floor 7/12 ▾]  [ burn $4.12/day ]  [ runway 41d ]│  ← HUD bar
├────────────┬─────────────────────────────────────────────────────────────┤
│            │                                                             │
│  FLOOR     │                    THE FLOOR (iso canvas)                   │
│  DIRECTORY │                                                             │
│            │    ┌──────┐   ┌──────┐   glowing walkways between rooms     │
│  12 Sales  │    │ R&D  │───│ Lab  │                                      │
│  11 Ops    │    └──────┘   └──────┘                                      │
│  10 ● R&D  │                                                             │
│   9 Support│                                                             │
│  ...       │                                                             │
│            │                                                             │
├────────────┴─────────────────────────────────────────────────────────────┤
│  ACTIVITY  ● agent-7 wrote note   ● agent-3 spent $0.004   ● +$12.00 sale │  ← feed
└──────────────────────────────────────────────────────────────────────────┘
```

| Region | Shows | Source |
|---|---|---|
| HUD bar | headcount, burn rate/day, runway days, net today | `office_v_*_pnl` rollups |
| Floor directory (left rail) | three levels (D27): **floor → departments → rooms**. Each row: name, headcount, P&L dot, and a **pending badge** | `office_floor` + `office_department` + `office_room` + rollups |
| Floor canvas | the iso office: rooms, walkways, agents, overlays | `office_event` + store reads |
| Activity feed (bottom) | live event stream, newest first, cost/revenue highlighted | `office_event` via WS |

**Click an agent** → side panel: title, department, seat type, model, balance, burn, runway,
reputation, current task, its artifacts, its ratchet entries.
**Click a room** → the team: roster, room function, capacity, the department's backlog.
**Click a floor** → the department: charter, manager, P&L, businesses, kill criteria.

## States this page must render honestly

| State | Rendering |
|---|---|
| Org not created yet | Empty state: "No company yet — `qubit office init`". No fake office. |
| Store unreachable | Typed error with the real reason. **Never a demo office** (D14) |
| No events yet | Every character idle. No phantom motion (C7) |
| Agent terminated | Seat clears; the row stays queryable in history |
| Staged approval pending | Red marker on the desk + the floor plate; settles via the existing approvals path |
| E-stop latched | Banner across the HUD: mutations refused, reads still work |
| Agent cites a hardline denial | Amber marker + the denial reason on the desk; the agent should not retry |
| Agent posture changed | A brief ring-colour transition on the desk, so a posture change is visible where it happened |

### Pipeline view (D30)

A fourth region, toggled from the HUD, showing the intake funnel:

| Shows | Source |
|---|---|
| Ideas in flight per stage (raw → manager → director → CEO → funded) | `office_idea` grouped by `stage` |
| The **compression ratio** at each gate (e.g. 200 found → 18 passed → 4 picked → 1 funded) | `office_idea` counts |
| The funnel's honesty metrics — manager pass rate, director pick rate, skip rate, bounce rate | derived from the verdict columns |
| A selected idea's **full chain**: source URL, mechanism, every bounce, every skip, every verdict with its reason | `office_idea` row |
| Dead ideas, with **which gate let them through** | `office_idea` where `stage='dead'` |

The compression ratios are the point: they tell the owner at a glance whether the org is filtering
or just forwarding. A funnel with a 90% manager pass rate is a funnel that is not working.

### Notification surfaces (D28)

The tower is **loud** — the owner reads the org by looking at it.

| Surface | Carries | Rule |
|---|---|---|
| **Speech bubble** (above the agent) | waiting / blocked / wants attention | per-agent, self-clearing |
| **Badge** (room, department, floor rail, sidebar tab) | a count of pending reviews + account requests + blocking asks | aggregates to a number, never a list |
| **Popup / toast** | **only** a mandatory review or a blocking request; a risk limit; a significant ledger event | reserved — never routine |

Adopted from Agent-Quest: *the building an agent walks to is what it is doing* — the visual **is**
the status. Adopted from pixel-agents: the awaiting-input bubble and the badge on the object that
needs attention.

### Autonomy markers (D19/D20, visibility per D28)

| Marker on the desk | Meaning |
|---|---|
| Green ring | `unattended_full` — no prompts, acting freely |
| Amber ring | `supervised` — actions are staging |
| Grey, desaturated | `frozen` |
| Container icon | running in a safe cell rather than on the host |

The HUD carries the org roll-up: how many agents hold a bypass, how many are in a container, and
how many hardline denials fired today.

## Read path vs mutation path

- **Reads:** `GET /api/office/{org,floors,rooms,agents,ledger,pnl}` → `qubit_compat` handlers →
  `voicebridge/office` store functions.
- **Live:** subscribe to the existing `/api/events` WebSocket; `office_event` rows carry a
  monotonic id used as the render cursor. A client that falls behind resyncs from its last cursor
  and never guesses.
- **Mutations:** **none direct.** Every action (hire, fire, promote, found a department, approve an
  escalation) is a staged op through `dashboard_ops` → `409` + `confirmation_id` → settled by the
  existing confirm path. The page never writes org state itself (C4).

## Voice

The same routes are reachable by voice, because they are the same ops the CLI and voice already
use — `qubit office status`, "how much did the org make today", "which floor is losing money".
That is the repo's existing requirement (every dashboard capability commandable by voice), and it
comes free when the read/write paths are shared rather than rebuilt.

## Build

Follow `frontend/qubit-app/README.md`: dependency-free IIFE bundles using only
`window.__QUBIT_PLUGIN_SDK__`. **Exception for this plugin:** the iso renderer needs Phaser, which
is not in the SDK. Two acceptable shapes:

1. **Vendored Phaser** in the plugin bundle (built with the SPA's Vite), or
2. **A small hand-written iso renderer** in plain canvas — the pepeclaw model is ~600 lines of
   polygon drawing and would need no dependency at all.

Prefer (2) if it stays close to that size: it keeps the plugin a dependency-free IIFE like every
other overlay plugin, and removes a bundle step from the tower's build.
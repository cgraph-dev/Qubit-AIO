# Upstream survey — four candidate absorptions (2026-11-06)

Survey only. **Nothing is ported, nothing is vendored into the tree, no route is
mounted.** This file records the pins and the measured shape of each candidate so
a later session does not have to re-clone or re-measure.

Owner request that produced this survey: "1. hermes-hudui added for qubit-office
to link them together. 2. replace /qubit-chat with hermes-webui. 3. integrate
hermes-jev-skills and browser-extension, rebrand and integrate natively.
4. atticus — other time."

## Pins (shallow clones, 2026-11-06)

| Repo | Commit | Committed |
|---|---|---|
| `joeynyc/hermes-hudui` | `be83208f48b7a1c67b7816837c8df71a61ef7edf` | 2026-10-11 |
| `nesquena/hermes-webui` | `3d9488fca6bb248c77f988bfbffcca9d8cfd9561` | 2026-11-05 |
| `abundantbeing/hermes-browser-extension` | `16d42430ff45d5833cb6176b401db867b591c2f2` | 2026-11-04 |
| `kerpopule/hermes-jev-skills` | `8316358bf60e98a21477582af829ef11c9cf52fb` | 2026-10-28 |

All four are **MIT**. Each carries its own `LICENSE` + copyright holder; MIT's
notice requirement means the copyright line travels with any code copied, and a
`PROVENANCE.json` per plugin directory is this repo's established form
(`frontend/qubit-app/plugins/qubit-office3/PROVENANCE.json` is the reference).

Local survey copies live outside the tree at
`~/.local/state/qubit-voicebridge/runtime/cache/scratch/repo-survey/`.

## Measured shape

### hermes-hudui — 209 files
Python backend (`backend/api/*`, `backend/collectors/*`, `backend/services/*`,
WebSocket manager, file watcher) + React/Vite frontend. 19 tabs: executive
dashboard, identity, memory, skills, sessions, replay, cron, projects, health,
costs, model analytics, patterns, corrections, sudo governance, live chat, OAuth
providers, gateway control, plugin management, live model capabilities.

**Coupling:** reads `~/.hermes/` and shells the `hermes` CLI
(`backend/collectors/health.py` states this explicitly). Reads
`<hermes_dir>/state.db` directly in `collectors/agents.py` and
`collectors/corrections.py`.

**Port hazard:** the direct `state.db` reads must resolve to Qubit's
`CHAT_SESSION_AUTHORITY` (`voicebridge.qubit_session_store.QubitSessionStore`),
never a second writable store, and the schema version it was verified against
(state.db v26) is not our schema.

### hermes-webui — 2,527 files, **154,371 LOC Python + 114,431 LOC JS**
1,894 test files. Three-panel layout (sessions / chat / workspace files),
composer footer controls, context ring, "Control Center".

**Coupling:** 10 modules import `run_agent.AIAgent`; 10 more shell out to the
`hermes` CLI. `bootstrap.py::_python_can_run_webui_and_agent` probes
`from run_agent import AIAgent` to choose an interpreter.

**Not a drop-in for `/qubit-chat`.** Replacing the current surface would displace:
- the voice path (push-to-talk, Right-Ctrl, TTS) that lives in `ChatPage.tsx`;
- the single chat-session authority (`CHAT_SESSION_AUTHORITY`);
- the Qubit rebrand on every visible string.

### hermes-browser-extension — 1,170 files, 239,713 LOC JS/MJS
Two halves: `extension/` (MV3 sidepanel, content scripts, tab control,
voice-dictation) and `companion-plugin/` — a plugin with `plugin.yaml`,
`provides_tools` (`browser_*`), `capabilities: []`, `requires_hermes: ">=0.22"`.

**Best-shaped candidate:** `companion-plugin/plugin.yaml` maps onto this repo's
plugin contract almost 1:1 (`name`, `kind`, `version`, `description`,
`capabilities`, `provides_tools`). The `browser_*` tools also overlap Qubit's
existing browser surface (`voicebridge/browser.py`, `voicebridge/cdp.py`,
`voicebridge/browser_tabs.py`) — collision to resolve before porting, not after.

### hermes-jev-skills — 407 files, 48,754 LOC Python
`jevkit/` (decision client, lanes, ladder, ledger, compaction, browser/computer
decide) + 13 `SKILL.md` files + `hermes/plugin`.

**External dependency:** `jevkit/client.py` hardcodes
`ENDPOINT = "https://api.typesafe.ai/v1/systemone"` and reads
`TYPESAFE_API_KEY`. Every capability it advertises (model routing, search
ranking, memory selection, compaction, skill selection, triage, mail sorting,
computer/browser next-action) is a call to that paid service. It degrades to
nothing without the key — the modules cannot be "integrated natively" as a
working capability without either the API or a reimplementation of the decision
model.

## Existing seams this work would use (verified, not assumed)

- **Plugin tab loader:** `frontend/qubit-app/plugins/<name>/manifest.json` +
  `qubit-*.js`; served by `voicebridge/qubit_compat.py::_overlay_manifests()`
  (OVERLAY_DIR = `frontend/qubit-app`) into `GET /api/dashboard/plugins`.
  13 overlay plugins exist today. `has_api: true` is the normal shape.
- **Office live payload:** `GET /api/office/tower` → `voicebridge/office/tower.py`;
  every character's `animation` is a pure function of `office_event` rows +
  clock. Linking HUD telemetry into Office means mapping it into THAT payload,
  not adding a parallel renderer.
- **Chat routes today:** `/chat` (embedded TUI over PTY), `/qubit-chat`
  (`ChatPage.tsx` + `TerminalChatPage.tsx`, sidebar primary), `/qubit-office3`,
  `/webchat`. Sidebar order is one array:
  `frontend/qubit-web/src/lib/sidebar-sections.ts`.
- **Office siblings already exist** with provenance records:
  `qubit-office`, `qubit-office2` (Agent Quest), `qubit-office3` (Voice Chat).

## Open decisions (blocking, owner)

1. HUD → Office: telemetry direction — HUD tab feeding Office, Office overlaying
   HUD, a separate tab, or survey deeper first.
2. `/qubit-chat`: full replacement by hermes-webui vs. absorbing its pieces into
   `ChatPage.tsx` vs. separate route.
3. Jev: optional provider that degrades / skip until a key exists / full wiring
   with a supplied `TYPESAFE_API_KEY` / local reimplementation.

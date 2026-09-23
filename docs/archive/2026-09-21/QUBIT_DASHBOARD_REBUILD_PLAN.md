# Qubit Dashboard Rebuild Plan

Status: **REQUIRED — current dashboard is not feature-parity complete**

The functional and UI requirements are frozen in
[`QUBIT_FROZEN_ACCEPTANCE_CONTRACT.md`](QUBIT_FROZEN_ACCEPTANCE_CONTRACT.md).
This document may not be changed to reduce Hermes UI parity requirements.
The owner amendment `QUBIT_OWNER_AMENDMENT_2026-09-11.md` explicitly permits
vendoring the pinned Hermes React/Vite frontend as the dashboard baseline.

## Goal

Build one professional Qubit command center using the actual pinned Hermes
frontend source as its baseline. Preserve the original Hermes dashboard’s
pages, navigation, controls, information architecture, density, and workflows
1:1, then add the missing OpenClaw workflows and rebrand the combined product
as Qubit. Do not replace the original dashboard with a small custom shell.

This must not be a static-JS imitation of Hermes. Vendor the exact pinned
Hermes frontend source, retain its source structure and behavior, and maintain
a machine-readable per-file hash/patch manifest. Qubit backend, permission,
audit, OpenClaw, accessibility, and branding changes must be explicit,
reviewable overlays or patches over that baseline.

The dashboard must be backed by the Qubit-owned pinned Hermes runtime described
in `docs/QUBIT_EXECUTION_DIRECTIVE_2026-09-12.md`. The existing compatibility
server may serve as a staged migration fallback, but it is not the final
authority for operations already implemented by the fork. CLI, dashboard, and
VoiceBridge must reach the same backend operation; a Hermes-looking shell over
the old compatibility layer is not dashboard parity.

The current Talk page is only the front door. It must not be presented as the
complete dashboard.

## Required information architecture

First reproduce the complete original Hermes dashboard surface from the pinned
source and live reference: its sidebar groups, chat workspace, session views,
files, models, logs, cron, skills, plugins, MCP, channels, webhooks, pairing,
profiles, config, keys, system controls, tool state, and terminal-style
status/output. Preserve equivalent routes, navigation depth, controls, forms,
tables, dialogs, reports, loading states, empty states, errors, and recovery.

Then add every adopted OpenClaw workflow without removing or hiding any Hermes
workflow. Qubit may change branding, colors, typography, icons, spacing,
copy, and polish; it may not remove behavior or collapse pages into status
cards.

The final dashboard must expose real pages or routed workspaces for:

1. **Workspace / Chat** — streaming conversation, terminal-style output,
   model/provider selector, tool activity, context, attachments where
   supported, stop/cancel, retry, copy/export, session selection, and voice.
2. **Sessions** — list, search, inspect, resume, rename, archive, delete with
   confirmation, session state, receipts, and recovery.
3. **Projects / Files** — project registry, folders, active project, file tree,
   preview, search, reports, task board, import/export, and safe file actions.
4. **Models / Providers** — versioned local/API model profiles, provider
   inventory, capabilities, routing per task/session, locality/egress consent,
   budgets, health, credential-reference status, test/validate, activate,
   clone/edit, rollback/remove, visible fallback, and degraded states.
5. **Tools / Skills / Plugins** — installed, staged, enabled, disabled,
   provenance, permissions, manifests, validation, rollback, and logs.
6. **Tasks / Cron / Runs** — schedules, queue, current runs, history, retry,
   cancel, resource usage, receipts, and failure recovery.
7. **Approvals / Permissions / Security** — pending approvals, grants, scopes,
   expiry, revocation, emergency stop, audit timeline, and redacted evidence.
8. **Browser / Desktop / IDE** — truthful availability, attached sessions,
   current context, previews, bounded actions, confirmation, and handoff.
9. **Messaging / Channels / Webhooks / Pairing** — account references only,
   connection health, inbox/outbox, drafts, approval, send confirmation,
   pairing, replay protection, and typed unavailable states.
10. **Memory / Profiles / Learning** — profiles, preferences, memory search,
    promote/correct/forget/export, provenance, retention, and multi-principal
    separation.
11. **Reports / Activity / Logs** — live event stream, filtered activity,
    reports, bundles, audit receipts, diagnostics, and redacted exports.
12. **Setup / Runtime / System** — installation health, selectable audio,
    ASR/TTS/realtime voice/device profiles, runtime inventory, autonomy policy,
    services, upgrades, rollback, backup, and repair.

If a workflow is implemented in CLI or voice, it must have an appropriate
dashboard route or embedded workspace. A count, status tile, or link to a
nonexistent page does not satisfy this plan.

## Backend contract

Every page must use the existing authenticated Qubit web backend and the same
operation registry/backend used by CLI and voice. For every operation prove:

- request schema and authentication;
- loading, empty, success, error, unavailable, degraded, and offline states;
- confirmation and cancellation behavior;
- receipt/audit result;
- redaction and secret handling;
- persistence and restart behavior;
- resource limits and timeout;
- rollback or honest rollback impossibility.

No frontend-only state, direct shell, direct upstream HTTP/MCP path, second
listener, second broker, or fabricated success is permitted.

## Qubit visual direction

Use the original Hermes frontend source as the structural and functional source
of truth. Rebrand the vendored implementation as a distinctive Qubit command
console without copying upstream logos, product names, copyrighted artwork, or
legal identity. Code vendoring is permitted by the owner amendment; legal
notices and provenance remain mandatory:

- dark graphite/navy foundation with Qubit signal-lime and electric-cyan
  accents;
- dense but readable two-level navigation with grouped workspaces;
- a high-information main workspace rather than a single centered card;
- terminal-inspired monospace only for logs, commands, IDs, and status data;
- clear proportional UI typography for navigation, explanations, and actions;
- persistent status rail for model, voice, bridge, tools, sessions, and safety;
- command palette and keyboard shortcuts alongside mouse/touch controls;
- responsive desktop, tablet, and narrow-mobile layouts;
- strong focus, contrast, reduced-motion, screen-reader, and 44px touch rules;
- purposeful streaming, approval, running, success, error, and recovery motion;
- no generic SaaS-card wall, oversized empty hero, dead controls, or decorative
  tiles without an operation behind them.

The visual system must be Qubit-owned. Do not copy upstream logos, names,
copyrighted artwork, or distinctive product branding.

## Build order

### Stage 1 — Inventory and route contract

- Vendor the pinned Hermes frontend source and verify its revision and
  per-file hashes before adapting any UI.
- Extract every current CLI operation, voice entry, dashboard operation, matrix
  row, and upstream workflow.
- Create a route/operation map with backend symbol, auth, tests, and owner.
- Mark missing pages before changing CSS.

### Stage 2 — Preserve the original Hermes shell

- Build the vendored Hermes frontend and make it the active dashboard artifact
  served by Qubit’s existing single listener. Do not leave it as an unserved
  reference directory.
- Reproduce the complete original sidebar, navigation groups, top bars,
  workspace layout, status panels, session controls, and terminal/chat region.
- Map each original route and control to a Qubit route and real backend.
- Add the Qubit command palette, status rail, notifications, breadcrumbs, and
  mobile navigation without removing original controls.
- Record every intentional visual or route change in the migration map.

### Stage 3 — Functional page families

- Implement the twelve page families above.
- Wire each route to real backend operations.
- Add all state, approval, error, recovery, and audit behavior before polish.

### Stage 4 — Qubit rebrand and enhancement

- Replace the current minimal Talk composition with the full preserved Hermes
  workspace plus OpenClaw extensions.
- Establish reusable Qubit tokens, components, tables, forms, command panels,
  terminal output, timelines, status badges, drawers, dialogs, and empty
  states.
- Apply Qubit colors, fonts, icons, labels, and polish without losing any
  original page or workflow. Inspect rendered screenshots at each major group,
  desktop and narrow mobile together.

### Stage 5 — Verification

- Test every route for auth, backend wiring, keyboard access, responsive
  behavior, safe rendering, and truthful states.
- Run Playwright/UI, dashboard, API, CLI, voice, security, and full suites.
- Inspect screenshots and failure output; repair defects before handoff.

## Acceptance gate

The dashboard is not complete until:

- every implemented CLI/voice capability has a real dashboard workflow;
- every dashboard control reaches a tested backend operation;
- every original Hermes page, route, control, and workflow has a Qubit
  equivalent unless an explicit tested compatibility mapping is recorded;
- every vendored Hermes frontend file has an upstream hash and either an equal
  Qubit hash or a documented Qubit patch with a regression test;
- every adopted OpenClaw page/workflow has a Qubit equivalent;
- all twelve page families exist or have a documented, tested route mapping;
- Models/Providers and Setup/Voice make every profile operation real and
  recoverable; a selector or status card without its backend lifecycle is open;
- every visible control has an authenticated production-listener test that
  proves request, backend, persistence, displayed result, and failure/recovery;
- desktop and narrow-mobile screenshots show a coherent Qubit command center;
- no upstream product branding appears in normal UI;
- UI and backend suites pass with warnings as errors;
- the independent verifier confirms functional parity rather than only visual
  similarity.

## Canonical dashboard voice-input decision — 2026-09-16

Read `docs/QUBIT_VOICE_TO_TEXT_WORKING_PROFILE_2026-09-16.md` before changing
the dashboard Voice control. Preserve the currently working Chrome/Chromium
`SpeechRecognition` path as Qubit’s preferred online voice input with
`en-US`, interim results, and one alternative. Send the final transcript
through the same canonical Qubit backend used by typed chat, CLI, and voice.
Do not replace it with Hermes ASR or claim Chrome’s private engine is embedded
locally. Keep local Sherpa Parakeet as the explicit offline/private fallback.

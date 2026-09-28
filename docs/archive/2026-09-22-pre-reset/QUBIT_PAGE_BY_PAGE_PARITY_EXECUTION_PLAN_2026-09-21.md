# Qubit Page-by-Page Parity Execution Plan

**Status:** implementation plan; not completion evidence  
**OpenClaw method surface (2026-09-21):** 434/434 contractual-pin gateway
methods disposed (252 IMPLEMENTED, 87 SAFE_EQUIVALENT, 95 OPERATOR_ONLY);
0 remain OPEN. The sessions family is fully disposed (0 OPEN); the
approvals/devices/attach security slice is disposed (approval.history + device
pair rename/scope-upgrade real; exec.approvals/attach typed); the agents /
agents.workspace family is disposed (workspace + file reads real; roster
mutations and the gateway write typed); the mcp.app family is disposed
(callTool real; resources/templates/sandbox typed); board tab ops are real
with typed widget/sandbox gaps. See `QUBIT_OPENCLAW_UPGRADE_MANIFEST.json` and
`QUBIT_OPENCLAW_METHOD_INVENTORY.json`. The dashboard/control coverage artifact
(`docs/QUBIT_PAGE_COVERAGE_2026-09-21.json`) records 3199 OPEN, 156
SAFE_EQUIVALENT, 0 IMPLEMENTED (no real-backend E2E yet).
**Scope:** every Qubit dashboard page, sidebar entry, control, backend operation, CLI equivalent, and voice equivalent  
**Primary brief:** `docs/QUBIT_ACTIVE_EXECUTION_BRIEF_2026-09-21.md`

## Purpose

Audit and complete the Qubit dashboard one page at a time while preserving the real renamed Hermes runtime, implementing the contractual OpenClaw surface, and routing every supported capability through the canonical Qubit backend.

The dashboard must be a working product, not a collection of rendered pages or placeholder controls. The source is based on the upstream Hermes implementation, but all public behavior must be Qubit-branded and all supported operations must remain upgradeable from future Hermes and OpenClaw releases.

## Non-negotiable rules

- Inspect the actual current worktree and the pinned/latest upstream source trees. Do not trust previous reports.
- Preserve all existing user changes. Never use broad reset, checkout, clean, deletion, or whole-file formatting.
- Do not modify pristine upstream reference trees.
- Do not count a route, page, button, catalog entry, or HTTP 200 as proof of functionality.
- Do not create fake success responses, dead controls, empty stubs, or silent exception swallowing.
- Every mutation must use the canonical Qubit authority, broker, policy, confirmation journal, audit journal, and e-stop.
- `/chat` remains the Qubit-branded terminal interface; `/qubit-chat` remains the native Brain4 voice interface.
- Both chat surfaces use the canonical session/history store.
- Brain4 remains replaceable and local-first; difficult work may escalate to configured API providers.
- Every supported capability must be actionable by text and voice.
- No public Hermes, Nous, or OpenClaw branding may remain. Preserve only necessary legal, provenance, migration, and wire identifiers.
- Qubit is one unified agent and one source tree; upstream projects are reference implementations, not separate runtime authorities.
- Right-Ctrl is a first-class `/qubit-chat` control connected to the same canonical voice/orchestrator, broker, policy, audit, confirmation, and e-stop backend.
- Right-Ctrl status, activation, failure, cancellation, and recovery must be visible in `/qubit-chat`.
- Baseline voice actionability is required before parity completion; later Brain4 training may improve voice quality but may not postpone or bypass the canonical voice path.
- During implementation, use focused syntax/import/type/regression checks only. Run the complete final matrix after all page work is frozen.

## Definition of done for every page

For each page, document and verify:

1. Upstream reference page, source components, routes, APIs, schemas, and state stores.
2. Qubit route, component, bundle, backend handler, and canonical operation.
3. Every sidebar link and breadcrumb.
4. Every button, icon button, tab, dropdown, toggle, checkbox, radio, input, textarea, select, date/time control, keyboard action, drag/drop target, pagination control, sort/filter control, refresh action, copy action, download/upload action, confirmation dialog, modal, drawer, and context menu.
5. Loading, empty, success, error, refusal, unauthorized, confirmation-required, cancellation, retry, timeout, reconnect, and recovery states.
6. Authentication, authorization, policy, broker, e-stop, audit, persistence, and event behavior.
7. CLI equivalent and voice intent, or an explicit documented disposition.
8. Responsive behavior at 1280px, 768px, and 390px; keyboard accessibility; labels; focus order; and screen-reader semantics.
9. Real state before and after each mutation.
10. Evidence with exact file paths, routes, request/response shapes, state changes, and verification commands.

A control is `IMPLEMENTED` only when:

```text
control/input
→ authenticated request
→ canonical Qubit operation
→ policy/broker/e-stop
→ real runtime/provider
→ persistent state/event
→ correct rendered result
→ error/retry/cancel/recovery/audit proof
```

Allowed dispositions are `IMPLEMENTED`, `SAFE_EQUIVALENT`, `OPERATOR_ONLY`, and `OPEN`. A refusal is not an implementation unless the unsupported operation has an explicit policy basis and a working safe counterpart.

## Page execution order and coverage matrix

### 1. Chat surfaces

Pages/routes:

- `/chat` — Qubit-branded terminal-style runtime.
- `/qubit-chat` — native Qubit-Brain4 chat and voice runtime.

Verify:

- terminal connection, ticket minting, origin/auth checks, reconnect, resize, input, output, close, and e-stop;
- terminal model/provider selection and actual provider resolution;
- minimum 65,536-token context enforcement on the real spawn path;
- native Brain4 voice, browser speech recognition, local STT/TTS, realtime profiles, and API escalation;
- shared sessions, transcript, messages, timestamps, tool calls, reasoning, model metadata, archive/restore, rename/delete behavior;
- streaming, cancel, retry, copy, new chat, history selection, pagination, search, and recovery;
- all tools, skills, MCP, providers, and OpenClaw operations available through text and voice;
- no public upstream branding in banners, errors, logs, or voice responses.
- Right-Ctrl capture, debounce, press/release state, permission failure, active-listener state, cancellation, e-stop, event journal, and UI status in `/qubit-chat`;
- Right-Ctrl text and voice turns reach the same canonical backend as typed `/qubit-chat` turns;
- Right-Ctrl never opens a private runtime, session store, broker, or action path.

### 2. Sessions

Verify list, pagination, sorting, search, open, resume, rename, archive, restore, delete/archive semantics, export, message history, tool messages, reasoning rows, timestamps, model/provider labels, empty state, loading state, and error recovery.

Confirm CLI and voice equivalents use the same session store.

### 3. Files

Verify browse, search, read, upload, download, write, rename, move, delete, preview, path confinement, permissions, pagination, filters, empty state, and refusal behavior.

Every operation must pass the filesystem policy and audit path. No absolute-path or traversal bypass is allowed.

### 4. Models and Providers

Verify provider catalog, model list, model details, profile creation/edit/clone/delete/activate/deactivate/rollback, base URL, context window, local/API type, key references, capability requirements, fallback providers, test connection, error display, and persistence.

Verify Brain4 replacement and local-to-API escalation without session or audit discontinuity.

### 5. Logs and Reports

Verify filtering, pagination, search, refresh, detail view, export/download, redaction, severity, timestamps, correlation IDs, operation IDs, error traces, and empty state.

No secrets, credentials, raw keys, or unsafe personal data may leak.

### 6. Cron

Verify list, create, edit, enable, disable, run now, pause, resume, delete/archive, schedule validation, timezone handling, execution history, failures, retries, and audit.

Confirm scheduled operations cannot bypass policy, confirmation, ownership, or e-stop.

### 7. Skills

Verify discovery, categories, search, detail, install, enable, disable, update, remove, permissions, configuration, version, provenance, errors, and refresh.

Every installed skill must declare capabilities, permissions, provider/model requirements, voice intents, confirmation requirements, and audit categories.

### 8. Plugins

Verify discovery, detail, install, enable, disable, reload, update, remove, configuration, permissions, health, provenance, and failure recovery.

Hermes and OpenClaw plugins must be importable through adapters without reintroducing public upstream branding.

### 9. MCP

Verify server list, add, edit, remove, enable, disable, test, authentication, tools, resources, prompts, failures, timeouts, redaction, and persistence.

GET and mutation paths must use one MCP registry; no proxy/store split-brain is allowed.

### 10. Channels

Verify channel catalog, connect, disconnect, enable, disable, configuration, credentials by reference, health, test, message routing, errors, retries, and audit.

Live credentials remain operator-only where necessary, but code paths must be real and typed.

### 11. Webhooks

Verify list, create, edit, rotate, enable, disable, test, delivery status, retry, delete/archive, signatures, authorization, redaction, and audit.

### 12. Pairing

Verify create, display, copy, approve, reject, expire, revoke, refresh, device identity, ownership, scope, and audit.

Voice must support safe pairing status and confirmation flows.

### 13. Profiles

Verify list, create, edit, clone, activate, deactivate, rollback, ownership, permissions, system prompt, model/provider, voice profile, and persistence.

No absolute local paths or secret values may be exposed.

### 14. Config

Verify every supported upstream configuration key and environment variable: display, type, default, validation, save, reload, restart requirement, error, redaction, migration, and actual runtime consumption.

Unsupported or secret writes must return typed refusals rather than silently disappearing.

### 15. Keys

Verify provider key references, `env:` and `file:` references, add/update/remove behavior where permitted, validation, redaction, persistence, rotation, and refusal behavior.

Never render or log secret values.

### 16. System

Verify status, health, runtime inventory, version, diagnostics, update behavior, restart behavior, gateway state, latency, process state, and error reporting.

Actions must target the canonical Qubit service and must not silently control an external Hermes/OpenClaw instance.

### 17. Agents

Verify agent list, detail, create/edit where supported, activate/deactivate, model/provider, permissions, tools, skills, sessions, ownership, and actual execution.

Desktop-only capabilities must be marked with exact operator prerequisites, not represented as fake web controls.

### 18. Documentation

Verify page navigation, search, section links, command examples, API documentation, OpenAPI/schema display, provenance, and absence of public upstream branding.

### 19. Qubit Chat plugin

If present as a plugin or route integration, verify that it does not create a second chat implementation or store. It must delegate to the native `/qubit-chat` surface and canonical backend.

### 20. Models & Providers plugin

Verify the plugin and built-in page do not expose conflicting registries. All operations must reach the canonical provider/model profile registry.

### 21. Qubit Boards

Verify list, create, edit, claim, comment, move, archive, restore, delete/archive semantics, pagination, filters, confirmations, events, and single-store authority.

### 22. Kanban

Verify boards, columns, cards, drag/drop, create/edit/move, comments, archive/restore, events, migration, and legacy-store compatibility. No duplicate writable board store may remain.

### 23. Gateway

Verify status, start/stop/restart where permitted, provider/runtime state, RPC, WebSocket, connect challenge, hello-ok, events, errors, reconnect, authorization, and e-stop.

All OpenClaw gateway methods must be inventoried and either implemented or explicitly dispositioned.

### 24. Memory

Verify search, inspect, create, update, delete/reset, provider selection, external provider handling, pagination, scope, permissions, and audit.

### 25. Runtime Inventory

Verify runtime components, versions, providers, models, skills, plugins, tools, routes, sessions, state stores, health, and provenance.

### 26. Operations

Verify operation list, detail, status, confirmation, cancel, retry, rollback, audit, errors, e-stop, and real state transitions.

### 27. Achievements

Verify list, rescan, detail, share/export where supported, provenance, branding, persistence, and error handling.

### 28. Devices

Verify device inventory, pairing, permissions, capabilities, status, health, and hardware-only dispositions. No hardware action may execute without explicit authorization.

## Upstream and future-extension requirements

For every page capability, record the upstream Hermes/OpenClaw source file and Qubit adapter/implementation.

Future upstream skills, plugins, tools, providers, channels, and pages must be importable through declared adapters with:

- capabilities;
- permissions;
- configuration schema;
- provider/model requirements;
- voice intents;
- confirmation requirements;
- audit categories;
- migration metadata;
- Qubit branding rules.

## Final page-by-page evidence

Create or update machine-readable coverage artifacts containing one row per:

- page;
- route;
- sidebar link;
- control;
- API operation;
- CLI command;
- voice intent;
- upstream counterpart;
- Qubit implementation;
- disposition;
- evidence path;
- last verification revision.

Do not close a page while any of its controls are `OPEN`.

After all pages and controls are implemented, run the final verification gate defined in `docs/QUBIT_FINAL_VERIFICATION_AND_RELEASE_GATE_2026-09-21.md`, followed by a separate read-only verifier.

Do not commit or push until the independent verifier passes. Report exact PASS, FAIL, OPEN, SAFE_EQUIVALENT, IMPLEMENTED, and OPERATOR_ONLY counts.

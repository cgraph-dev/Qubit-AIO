# Qubit overlay — Qubit integration (no vendored file is edited)

This directory is the ONLY place where Qubit changes the Qubit dashboard
behavior. `../hermes-web/` and `../hermes-shared/` stay byte-identical to
pinned qubit-agent@2237be3 (see `docs/HERMES_SOURCE_COMPARISON.json`).

## Contents

- `qubit-theme.json` — full `DashboardTheme` definition served by
  `GET /api/dashboard/themes` (active: `qubit`). Qubit graphite/navy +
  signal-lime/cyan applied through the dashboard's own theming API.
- `plugins/qubit-approvals/` — shell-wide pending-confirmation banner
  (`header-banner` slot, hidden tab). Polls `GET /api/ops/approvals` on the
  shared journal and settles via `POST /api/ops/confirm|cancel` (same
  `OPS.confirm_op/cancel_op` as CLI/voice/classic). Staged mutations answer
  409 (never 2xx) so nothing pending can render as executed. Renders nothing
   when nothing is pending.
- `plugins/qubit-chat/` — REMOVED. `/qubit-chat` is no longer an overlay
  plugin. Per the owner chat mapping, `/qubit-chat` is a built-in SPA route
  that renders the exact same `ChatPage` component as `/chat` (shared session
  store, provider resolver, streaming, policy, audit, e-stop, and VoiceBridge).
  Both routes are directly navigable; neither redirects and there is no second
  chat implementation. The backend routes (`POST /api/qubit/chat/stream`,
  `GET /api/qubit/chat/sessions/<id>/messages`) still run
  `voicebridge.chat_transport` / `voicebridge.agent.turn` with e-stop,
  redaction, and audit.
- `plugins/qubit-gateway/` — Qubit addition: full messaging-account +
  gateway command surface (same `voicebridge/messaging.py` +
  `gateway_peers.py` as CLI/voice) — add account, add gateway, typed link
  check, remove. Records only (supervision refused); key references only
  (`env:`/`file:`, raw values fail fast pre-stage, never journaled);
  mutations confirm through the shared journal.
- `plugins/qubit-boards/` — Qubit addition: full board command surface
  (same `voicebridge/kanban.py` store as CLI/voice) — create board, add
  card, move across boards/columns, complete — every mutation through the
  shared confirmation journal (409 staged + inline Confirm/Cancel settling
  `POST /api/ops/confirm|cancel`). Filtered reads via
  `GET /api/ops/kanban?board=&status=`.
- `plugins/qubit-runtime/` — Qubit addition: pinned runtime inventory
  (same `voicebridge/runtime_images.py` + inventory as CLI/voice).

## Plugin authoring rules

- Bundles are dependency-free IIFEs using only `window.__HERMES_PLUGIN_SDK__`
  (React, hooks, api, fetchJSON, components). No bundler output is committed;
  sources are hand-reviewed `.js`.
- Every bundle calls real compat endpoints (`voicebridge/qubit_compat.py`),
  which call the same backend modules as CLI and voice. No frontend-only
  success: errors render typed backend states.
- Product labels say Qubit; upstream names appear only in provenance/legal
  strings and the Docs page.

## Build / serve (dashboard operators)

The canonical reproducible build is `scripts/build_dashboard.sh`, which sets
up the `frontend/qubit-shared` workspace link, installs the declared
dependencies, typechecks, and writes the bundle for the Qubit root base (`/`)
the server expects:

```sh
scripts/build_dashboard.sh
```

Equivalent manual steps:

```sh
cd frontend/qubit-web
npm install --legacy-peer-deps --no-audit --no-fund   # deps declared in package.json
mkdir -p ../apps && ln -sfn ../hermes-shared ../qubit-shared   # monorepo layout
npx vite build --base=/ --outDir ../qubit-dist --emptyOutDir
```

`frontend/qubit-dist/` is a build artifact (VCS-ignored) and is declared in
`pyproject.toml` under `[tool.hatch.build.targets.{wheel,sdist}].artifacts`,
so wheel/sdist builds ship it and a clean install serves the dashboard with
no checkout or untracked file. `voicebridge.webui` resolves the bundle (and
the `frontend/qubit-app/plugins` overlay) from package data first, then
the checkout, and serves it at `/` with the loopback session token +
`__HERMES_BASE_PATH__=""` injected server-side (vendored `index.html`
untouched).

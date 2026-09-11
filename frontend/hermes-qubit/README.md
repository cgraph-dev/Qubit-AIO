# Hermes overlay — Qubit integration (no vendored file is edited)

This directory is the ONLY place where Qubit changes the Hermes dashboard
behavior. `../hermes-web/` and `../hermes-shared/` stay byte-identical to
pinned hermes-agent@2237be3 (see `docs/HERMES_SOURCE_COMPARISON.json`).

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
- `plugins/qubit-chat/` — new `/qubit-chat` tab (manifest + IIFE bundle using
  `window.__HERMES_PLUGIN_SDK__`). Streaming-safe single-turn chat over
  `POST /api/qubit/chat`, which runs `voicebridge.agent.turn` — the same
  module as CLI text and voice turns — with e-stop, redaction, and audit.
  Dashboard voice control: a Voice button drives browser SpeechRecognition
  and sends the transcript through the SAME send path (voice executes
  backend turns, never describes them); typed `VOICE_UNSUPPORTED` /
  `VOICE_DENIED` / `VOICE_NO_SPEECH` / `VOICE_TIMEOUT` states otherwise,
  plus a 60s sliding watchdog so a silent engine can never hang the button.
  The built-in xterm `/chat` tab is preserved untouched; its PTY attach is
  answered by the backend with typed `PTY_NO_SHELL` pointing here.
- `plugins/qubit-gateway/` — OpenClaw addition: full messaging-account +
  gateway command surface (same `voicebridge/messaging.py` +
  `gateway_peers.py` as CLI/voice) — add account, add gateway, typed link
  check, remove. Records only (supervision refused); key references only
  (`env:`/`file:`, raw values fail fast pre-stage, never journaled);
  mutations confirm through the shared journal.
- `plugins/qubit-boards/` — OpenClaw addition: full board command surface
  (same `voicebridge/kanban.py` store as CLI/voice) — create board, add
  card, move across boards/columns, complete — every mutation through the
  shared confirmation journal (409 staged + inline Confirm/Cancel settling
  `POST /api/ops/confirm|cancel`). Filtered reads via
  `GET /api/ops/kanban?board=&status=`.
- `plugins/qubit-runtime/` — OpenClaw addition: pinned runtime inventory
  (same `voicebridge/runtime_images.py` + inventory as CLI/voice).

## Plugin authoring rules

- Bundles are dependency-free IIFEs using only `window.__HERMES_PLUGIN_SDK__`
  (React, hooks, api, fetchJSON, components). No bundler output is committed;
  sources are hand-reviewed `.js`.
- Every bundle calls real compat endpoints (`voicebridge/hermes_compat.py`),
  which call the same backend modules as CLI and voice. No frontend-only
  success: errors render typed backend states.
- Product labels say Qubit; upstream names appear only in provenance/legal
  strings and the Docs page.

## Build / serve (dashboard operators)

The canonical reproducible build is `scripts/build_dashboard.sh`, which sets
up the `frontend/apps/shared` workspace link, installs the declared
dependencies, typechecks, and writes the bundle with the `/hermes/` base the
server expects:

```sh
scripts/build_dashboard.sh
```

Equivalent manual steps:

```sh
cd frontend/hermes-web
npm install --legacy-peer-deps --no-audit --no-fund   # deps declared in package.json
mkdir -p ../apps && ln -sfn ../hermes-shared ../apps/shared   # monorepo layout
npx vite build --base=/hermes/ --outDir ../hermes-dist --emptyOutDir
```

`frontend/hermes-dist/` is a build artifact (VCS-ignored) and is declared in
`pyproject.toml` under `[tool.hatch.build.targets.{wheel,sdist}].artifacts`,
so wheel/sdist builds ship it and a clean install serves the dashboard with
no checkout or untracked file. `voicebridge.webui` resolves the bundle (and
the `frontend/hermes-qubit/plugins` overlay) from package data first, then
the checkout, and serves it at `/hermes/` with the loopback session token +
`__HERMES_BASE_PATH__=/hermes` injected server-side (vendored `index.html`
untouched).

# Qubit dashboard audit — Wave 4 (agent F: UI parity + placeholder removal)

Status: **WAVE 4 — code-side UI work complete; provenance hash handoff pending
agent H merge** (`docs/HERMES_SOURCE_COMPARISON.wave4.diff`).

Machine-checked by `tests/test_dashboard_ui_parity_wave4.py`. Functional
regressions are additionally guarded by the Wave 1–3 suites
(`test_dashboard_plugin_fleet.py`, `test_dashboard_page_closure_wave2.py`,
`test_dashboard_branding_wave1.py`, `test_dashboard_ops8tf.py`,
`test_dashboard_agent3_plugins.py`, `test_qubit_vendor8tf.py`).

## Scope and method

Static, hermetic, no browser. Playwright is opt-in and was **not run** (see
*Untested*). Method:

1. enumerate every vendored page and its real backing API from `App.tsx`,
   `docs/HERMES_FRONTEND_MAP.json`, and the produced compat dispatcher;
2. enumerate the ten Qubit overlay plugin tabs and the two pinned fleet plugin
   tabs, and check every `/api/...` literal each bundle calls resolves in
   `voicebridge.hermes_compat` (or the shared `/api/ops` broker);
3. scan the overlay and vendored dashboard source for placeholder tokens, wrong
   labels, self-referential (dead) controls, and status-only JSON boxes;
4. assert the theme keeps its responsive/accessibility/state contracts.

No vendored `frontend/qubit-web/**` source was edited this wave; the only
changes are the Qubit-owned overlay `frontend/qubit-app/**` and the new
test/doc. The vendored dashboard remains byte-clean for the pinned revision
except the already-recorded Wave 1–3 patches.

## Page-by-page status

### Baseline Hermes information architecture (19 routes, all real)

| Route | Page module | Primary backing API / operation | Workflow |
|---|---|---|---|
| `/` | `RootRedirect` | — | redirects to `/sessions` |
| `/sessions` | `SessionsPage.tsx` | `GET /api/sessions` | list/search/inspect/resume/rename/archive/delete, receipts |
| `/files` | `FilesPage.tsx` | `GET /api/files` | project-root-confined read/inspect |
| `/analytics` | `AnalyticsPage.tsx` | `GET /api/analytics` | token/model/skill tables + period/refresh; honest hidden state when `dashboard.show_token_analytics` is off |
| `/models` | `ModelsPage.tsx` | `GET /api/model/info` | providers, routing, budgets, health |
| `/logs` | `LogsPage.tsx` | `GET /api/logs` | live filtered event stream |
| `/cron` | `CronPage.tsx` | `GET /api/cron/jobs` | schedules, blueprints, delivery targets |
| `/skills` | `SkillsPage.tsx` | `GET /api/skills` | install/enable/disable/validate |
| `/plugins` | `PluginsPage.tsx` | `GET /api/dashboard/plugins` | install/enable/disable/update |
| `/mcp` | `McpPage.tsx` | `GET /api/mcp` | server inventory (mount/execution refused, typed) |
| `/pairing` | `PairingPage.tsx` | `GET /api/pairing` | pairing create/approve/revoke |
| `/channels` | `ChannelsPage.tsx` | `GET /api/messaging/platforms` | accounts/inbox/draft/send/approve |
| `/webhooks` | `WebhooksPage.tsx` | `GET /api/webhooks` | list/add/remove/test/verify |
| `/system` | `SystemPage.tsx` | `GET /api/system/stats` | runtime/doctor/product/backup/verify |
| `/profiles` | `ProfilesPage.tsx` | `GET /api/profiles` | profiles/vaults |
| `/profiles/new` | `ProfileBuilderPage.tsx` | `GET /api/profiles` | onboarding/apply form |
| `/config` | `ConfigPage.tsx` | `GET /api/config` | config forms/export; raw dump refused |
| `/env` | `EnvPage.tsx` | `GET /api/env` | key-reference status; value reveal refused |
| `/docs` | `DocsPage.tsx` | embedded upstream reference | real iframe + `docs:top`/`docs:bottom` slots |
| `/chat` | `ChatPage.tsx` | `GET /api/status` + PTY refusal | persistent xterm host, typed PTY policy notice |

All 19 route pages are substantial real workspaces (≥100 lines except the
docs wrapper); `test_dashboard_ui_parity_wave4.py::test_vendored_dashboard_has_no_tiny_placeholder_page`
enforces this so a future stub page cannot ship.

### Qubit overlay plugin tabs (`frontend/qubit-app/plugins`)

| Tab | Route | Backend (shared with CLI/voice) | Wave 4 change |
|---|---|---|---|
| Boards | `/qubit-boards` | `/api/ops/boards`, `/api/ops/kanban`, `/api/ops/run` (`dashboard_ops`) | typed `data-state` on pending/empty/error |
| Qubit Chat | `/qubit-chat` | `POST /api/qubit/chat` (`agent.turn`) | typed `data-state`/`aria-busy`; empty + degraded states |
| Devices | `/devices` | `/api/ops/runtime`, `/api/ops`, `/api/ops/run` | typed loading/empty/degraded states |
| Gateway | `/qubit-gateway` | `/api/ops/msg-accounts`, `/api/ops/gateways`, add/remove/link | typed pending/empty/degraded states |
| Memory | `/memory` | `/api/memory`, `/api/curator`, `/api/ops/run` | typed provider/search/ledger states |
| Operations | `/operations` | `/api/ops`, `/api/ops/run|confirm|cancel` | **wrong label fixed**, typed result/catalog/empty states |
| Reports | `/reports` | `/api/logs`, `/api/analytics`, `/api/ops/run` | typed loading/empty/degraded states |
| Runtime | `/qubit-runtime` | `/api/ops/runtime` | **raw-JSON status box → real inventory table** + states |
| Qubit Approvals (hidden) | `/qubit-approvals` | `/api/ops/approvals`, confirm/cancel | typed `pending`/`empty`/`degraded` |
| Qubit Brand (hidden) | `/qubit-brand` | theme/slots only | **dead self-link removed** |

Every visible overlay tab's `position` target resolves to a real route
(`test_visible_overlay_tabs_target_real_routes`), and every `/api` literal
resolves in the compat dispatcher (`test_every_overlay_backend_literal_is_real_or_known`).

### Pinned fleet plugin tabs (achievements + kanban)

| Tab | Route | Backend | State |
|---|---|---|---|
| Achievements | `/achievements` (after analytics) | `/api/plugins/hermes-achievements/*` (computed from local session history) | Qubit-branded bundle + host `achievements` i18n namespace (`en.ts`, `types.ts`); real scan/unlock/empty/error states |
| Kanban | `/kanban` (after skills) | `/api/plugins/kanban/*` (canonical board store) | Qubit-branded bundle + host `kanban` i18n namespace; real board/empty/error states |

These tabs were already wired by Wave 3; Wave 4 verifies they are Qubit-branded
(no product-facing upstream name outside the `X-Hermes-Session-Token` protocol
header), localized through `tx(t, ...)`, and read the real plugin backend.

Known limitation (vendored bundle, not edited this wave): the pinned Kanban
bundle opens a `WebSocket` to `/api/plugins/kanban/events`. Qubit refuses the
Hermes WebSocket dialect by policy, so live updates degrade to the bundle's
existing HTTP reload/backoff path (the board still loads and mutates
correctly). Fixing the reconnect loop requires a documented patch to the
pinned bundle (which would move its manifest hash); it is recorded here for
agent H/agent B rather than silently edited.

## Placeholders / dead controls found and fixed

| # | File:line (pre-Wave 4) | Finding | Fix |
|---|---|---|---|
| 1 | `qubit-runtime.js` whole component | Entire tab was a raw `JSON.stringify` "status box" — a generic box with no workflow | real inventory table (name/pin/state/effective) + typed states; raw payload behind a disclosure |
| 2 | `qubit-ops.js` (~L215) | Badge labelled every `voice_guard` op `voice: refusal` — **wrong label** (a guard names the canonical spoken entry, not a refusal) | `voice: linked` + guard in tooltip |
| 3 | `qubit-brand.js` (~L35) | Docs notice linked `Open the Qubit operator guide` → `/docs`, but it renders **on** `/docs`: self-referential **dead control** | link removed; accurate `qubit --help` / `docs/` guidance |
| 4 | `qubit-theme.json` customCSS | `header span.text-midground.font-bold{display:none}` blanked the **mobile top-bar Qubit wordmark**, not just the vendored duplicate | sidebar-scoped hide only; mobile brand restored, no duplicate |
| 5 | overlay plugins (10 files) | loading/empty/degraded/approval surfaces had no machine-readable state markers, so the Qubit state styles could not apply | typed `data-state` (`loading`/`empty`/`degraded`/`pending`) + `aria-busy` on the real backend surfaces |

No placeholder pages exist in the vendored dashboard: all 19 pages are real
workspaces, and no placeholder token (`coming soon`, `under construction`,
`lorem ipsum`, `not yet implemented`) remains in the overlay or vendored
source (`test_no_placeholder_tokens_in_overlay_or_vendored_source`).

## Responsive / accessibility (static verification)

Verified from `frontend/qubit-app/qubit-theme.json` customCSS (served as the
active `qubit` theme) and the overlay markup:

- **Breakpoints** — `@media (max-width:768px)` (narrow sidebar width, 15.5px
  base), `@media (min-width:1600px)` (full-width main); the shell itself uses
  `1024px` as the sidebar collapse/overlay breakpoint
  (`useBelowBreakpoint(1024)` in `App.tsx`). Tested widths to render manually:
  desktop 1440, tablet 1024, mobile 390.
- **Touch targets** — `@media (pointer:coarse)` raises every button/link/input
  to `min-height:44px;min-width:44px`.
- **Keyboard focus** — global `:focus-visible` lime ring with offset; mouse
  clicks do not draw the ring.
- **Motion** — animation only under `prefers-reduced-motion:no-preference`;
  pulse/shimmer/recover keyframes are inert for reduced-motion users.
- **Contrast / forced colors** — `prefers-contrast:more` strengthens borders;
  `forced-colors:active` uses the system `Highlight` outline.
- **Typed states** — the theme styles `data-state="loading|empty|degraded|
  unavailable|pending|awaiting-approval|cancelled|estop|recovering|retry"` and
  the overlay pages now emit the applicable markers.
- **Semantics** — live regions use `role="status"`/`aria-live="polite"`;
  failures use `role="alert"`; busy surfaces set `aria-busy`; every control has
  an `aria-label` (verified by `test_dashboard_agent3_plugins.py` + the shell
  tests).

## Provenance handoff

`docs/HERMES_SOURCE_COMPARISON.wave4.diff` lists the exact new `qubit_sha256`
for the 11 edited overlay files plus the full unified diff. Until agent H
merges them into `docs/HERMES_SOURCE_COMPARISON.json` → `wave3.overlay`,
`tests/test_branding_parity_wave3.py::test_overlay_manifest_hashes_are_valid`
reports those files as stale (this is the only red test; it is provenance, not
behaviour). No vendored `frontend/qubit-web/**` file changed, so the pinned
vendor manifest is untouched.

## Tests and commands (this wave)

```sh
.venv/bin/python -m pytest tests/test_dashboard_ui_parity_wave4.py \
  tests/test_dashboard_plugin_fleet.py \
  tests/test_dashboard_page_closure_wave2.py \
  tests/test_dashboard_branding_wave1.py \
  tests/test_dashboard_ops8tf.py \
  tests/test_hermes_frontend_map8u.py -q      # 89 passed, 1 skipped
.venv/bin/python -m pytest tests/test_dashboard_agent3_plugins.py \
  tests/test_hermes_vendor8tf.py -q            # 24 passed at wave start
./node_modules/.bin/tsc -p . --noEmit          # clean
.venv/bin/python -m py_compile tests/test_dashboard_ui_parity_wave4.py
git diff --check                               # clean
cd frontend/qubit-web && npx vite build --base=/hermes/ \
  --outDir ../qubit-dist --emptyOutDir        # ✓ built in 4.17s
```

Collateral (provenance only): `tests/test_branding_parity_wave3.py` — 12 pass,
1 fails (`test_overlay_manifest_hashes_are_valid`, stale overlay hashes pending
agent H merge per `docs/HERMES_SOURCE_COMPARISON.wave4.diff`).

Concurrent-work caveat (not agent F, not Wave 4): late in this wave another
agent's in-flight edit to `voicebridge/opencode/state.py` /
`voicebridge/opencode/runner.py` removed the `shared_call` import target, which
made `runtime_status` raise `ImportError` and thus
`tests/test_dashboard_agent3_plugins.py::test_every_plugin_manifest_served_through_real_route`
fail. The Wave 1–3 overlay/route assertions in that file all passed; the
failure is an unrelated Python import in another agent's owned file, not a
dashboard regression.

## Untested

- **Playwright was not run.** Rendered desktop/tablet/mobile screenshots,
  focus-order traversal, and the Kanban live-update WebSocket degradation are
  asserted statically only. `tests/test_qubit_playwright8tf.py` is opt-in.
- The pinned Kanban WebSocket reconnect loop against the Qubit WS refusal was
  not exercised in a browser (see *Known limitation* above).
- No live provider/account, audio, or hardware paths.

## Rollback

```sh
git checkout HEAD -- frontend/qubit-app/plugins/qubit-approvals/qubit-approvals.js \
  frontend/qubit-app/plugins/qubit-boards/qubit-boards.js \
  frontend/qubit-app/plugins/qubit-brand/qubit-brand.js \
  frontend/qubit-app/plugins/qubit-chat/qubit-chat.js \
  frontend/qubit-app/plugins/qubit-devices/qubit-devices.js \
  frontend/qubit-app/plugins/qubit-gateway/qubit-gateway.js \
  frontend/qubit-app/plugins/qubit-memory/qubit-memory.js \
  frontend/qubit-app/plugins/qubit-ops/qubit-ops.js \
  frontend/qubit-app/plugins/qubit-reports/qubit-reports.js \
  frontend/qubit-app/plugins/qubit-runtime/qubit-runtime.js \
  frontend/qubit-app/qubit-theme.json
rm -f tests/test_dashboard_ui_parity_wave4.py docs/DASHBOARD_AUDIT_WAVE4.md \
  docs/HERMES_SOURCE_COMPARISON.wave4.diff
```

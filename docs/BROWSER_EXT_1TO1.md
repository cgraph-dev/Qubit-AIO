# Browser extension 1:1 port — the contract

Porting `github.com/abundantbeing/hermes-browser-extension` (MIT,
Copyright (c) 2026 Jon Komet) at pin
`16d42430ff45d5833cb6176b401db867b591c2f2` into Qubit — **both halves**.

This document is written from real reads of every upstream file before any
Qubit file was touched. It is the contract the port is measured against:
where each upstream file lands, which tool answers a call, which host seam
carries each HTTP route, and every identifier the rebrand must rewrite. A
later section (§G) records what actually shipped.

| half | upstream | Qubit destination |
|---|---|---|
| browser side | `extension/` (MV3 side panel, 474 files) | `frontend/qubit-app/plugins/qubit-browser-ext/extension/` |
| agent side | `companion-plugin/` (18 modules, 4 488 LOC) | `qubit_runtime/plugins/browser-companion/` |

## Owner ruling this port answers

The previous attempt ported **three** tools, dropped all 20 `tab_*` tools,
the control broker/routes, the HTTP surface and the journal, and argued Qubit
"already owns browser control". That scope was rejected. This port carries
every upstream capability, names the routing decision for each one, and
records as a gap only what a plugin genuinely cannot reach.

---

## (a) Per-file inventory — upstream → destination → disposition

### (a.1) `companion-plugin/` (agent side)

All 18 Python modules are **PORT**ed. `PORT` below means: copied to the
destination path, rebranded per §D, then extended where Qubit's host surface
differs (each such extension is listed under §G.2). Nothing is dropped.

| upstream file | LOC | destination (`qubit_runtime/plugins/browser-companion/`) | disposition |
|---|---|---|---|
| `__init__.py` | 183 | `__init__.py` | **PORT** — full `register()`: 27 tools, 3 hooks, bundled skill, route wiring |
| `plugin.yaml` | — | `plugin.yaml` | **PORT + RENAME** — `provides_tools` restored to all 27, `post_tool_call` restored, config schema kept |
| `tools.py` | 336 | `tools.py` | **PORT** — 7 context tools incl. `browser_event_log`, `browser_control_status`, `browser_context_journal`, `browser_text_utility` |
| `schemas.py` | 116 | `schemas.py` | **PORT** — all 7 schemas (`SCHEMA_EVENT_LOG`, `SCHEMA_JOURNAL`, `SCHEMA_CONTROL_STATUS`, `SCHEMA_TEXT_UTILITY` restored) |
| `context_store.py` | 822 | `context_store.py` | **PORT** — the whole owner-scoped store: upload shelf (`put_session_upload`, `adopt_upload_for_owner`, `session_status`), journal rows, receipts, control-status, event log |
| `journal.py` | 311 | `journal.py` | **PORT** — metadata-only rotation journal, 0600 durable file |
| `hooks.py` | 210 | `hooks.py` | **PORT** — `pre_llm_call`, `pre_tool_call`, `post_tool_call` |
| `events.py` | 33 | `events.py` | **PORT** (already ported; renamed ids kept) |
| `protocol.py` | 37 | `protocol.py` | **PORT** — `BrowserContextEnvelope` |
| `policy.py` | 39 | `policy.py` | **PORT** — the declared-and-closed HTTP policy |
| `actions.py` | 46 | `actions.py` | **PORT** — declarative action descriptors (no execution) |
| `text_utilities.py` | 112 | `text_utilities.py` | **PORT** (already ported; now *registered* as a tool, not just source) |
| `control_broker.py` | 655 | `control_broker.py` | **PORT** — upstream's `ControlBroker` kept verbatim in behaviour, so the plugin owns its own lane with its own capability allowlist (20 actions, no developer caps) |
| `control_schemas.py` | 305 | `control_schemas.py` | **PORT** — 20 `tab_*` schemas, `TOOL_NAMES` / `ACTION_FOR_TOOL` / `TAB_TARGETING` |
| `control_tools.py` | 409 | `control_tools.py` | **PORT** — 20 `tab_*` handlers, URL guard (cloud metadata + embedded secrets), screenshot-to-file |
| `control_routes.py` | 328 | `control_routes.py` | **PORT** — `POST …/control/register`, `GET …/control/ws`, principal digest, ticket subprotocol |
| `context_routes.py` | 204 | `context_routes.py` | **PORT** — `context/status`, `context` upload, `actions` |
| `status_route.py` | 183 | `status_route.py` | **PORT + ADAPT** — same payload, wired through the seam Qubit actually has (§C) |
| `profile_routes.py` | 159 | `profile_routes.py` | **PORT + ADAPT** — `profiles`, `profiles/{name}/avatar`, rebranded `hermes-cli` imports → `qubit_cli` |
| `skills/hermes-browser-extension/SKILL.md` | — | `skills/browser-companion/SKILL.md` | **PORT + RENAME** |
| `install.md` | — | `INSTALL.md` | **PORT + RENAME** — `hermes plugins install …` → `qubit plugins install …`, paths `~/.hermes` → `~/.qubit` |
| `README.md` | — | `README.md` | **PORT + RENAME** |

Module direction (unchanged): `__init__` → {tools, hooks, control_tools, status_route}
→ {context_store, control_broker} → {journal, schemas, control_schemas, protocol,
text_utilities, events, policy, actions}.

### (a.2) `extension/` (browser side) — top level

| upstream | destination (`…/qubit-browser-ext/extension/`) | disposition |
|---|---|---|
| `manifest.json` | `manifest.json` | **PORT + RENAME** — name/short_name/description → `__MSG_*` Qubit strings; `hermes-web-launch.*` entries renamed |
| `background.js` | `background.js` | **PORT + RENAME** — module service worker |
| `content.js` | `content.js` | **PORT + RENAME** |
| `content-extractor.js` | `content-extractor.js` | **PORT + RENAME** — `hermes.browser.extraction.v1` → `qubit.…` |
| `content-inline-helper.js` | `content-inline-helper.js` | **PORT + RENAME** |
| `sidepanel.html` | `sidepanel.html` | **PORT + RENAME** |
| `sidepanel.js` | `sidepanel.js` | **PORT + RENAME** (31 020 LOC) |
| `sidepanel.css` | `sidepanel.css` | **PORT** |
| `sidepanel-themes.css` | `sidepanel-themes.css` | **PORT** |
| `context-menu-editor.css` | `context-menu-editor.css` | **PORT** |
| `sidepanel-preview.html` | `sidepanel-preview.html` | **PORT + RENAME** |
| `voice-dictation.html` / `.js` | same | **PORT + RENAME** |
| `wake-listener.html` / `.js` | same | **PORT + RENAME** |
| `request-permissions.html` / `.js` | same | **PORT + RENAME** |
| `hermes-web-launch.js` / `.css` | `qubit-web-launch.js` / `.css` | **PORT + RENAME** (file renamed; manifest + any importer updated) |
| `hermes-web-opening.html` / `.js` / `.css` | `qubit-web-opening.*` | **PORT + RENAME** |
| `_locales/**` (21 locales) | `_locales/**` | **PORT + RENAME** — `__MSG_*` values rebranded in all 21 |
| `assets/**` (fonts, icons, pixel icons, images, video) | `assets/**` | **PORT** — `hermes-*.svg/png/webp` files **RENAME** to `qubit-*`; every reference updated |
| `lib/**` (≈230 `.mjs` + `.css` + `locales/`) | `lib/**` | **PORT + RENAME** — `hermes-client.mjs` → `qubit-client.mjs`, `hermes-webapp.mjs` → `qubit-webapp.mjs`, `hermes-account.mjs` → `qubit-account.mjs`, `hermes-context-*.mjs` → `qubit-context-*.mjs`, `hermes-webapp-dialog.mjs` → `qubit-webapp-dialog.mjs`, `hermes-web-opening.mjs` → `qubit-web-opening.mjs`; every importer rewritten |
| `lib/vendor/**` | `lib/vendor/**` | **PORT** — DOMPurify, highlight.js, blobatar; their own licenses ship beside them |

### (a.3) `extension/` — outside the loaded tree, and the repo root

| upstream | disposition |
|---|---|
| `extension/lib/…` build-step sources (`content-extractor` runtime bundle) | **PORT** — the committed bundle in `content-extractor.js` is used as shipped; no Node build step is required to load unpacked, which is the acceptance test |
| `scripts/*.mjs`, `scripts/*.py` (37 build/test/release tools) | **OMIT** — Node build + release tooling for upstream's npm package. Lost capability: `npm run build`/`package`/`lint`/`web-ext sign` workflows. Loading unpacked needs none of them; the manifest check that matters (every referenced file present, MV3 valid) is replaced by a Qubit-side validator (§F.4). |
| `tests/*.test.mjs` (≈150 files) | **OMIT** — upstream's Node test suite. Recorded as a named gap in §G.3: the extension ships without its test suite; the port is verified by manifest validation + wire-contract checks instead. |
| `package.json`, `package-lock.json`, `eslint.config.js`, `.cursorrules` | **OMIT** — npm project furniture. The loaded extension is `extension/` only. |
| `README.md`, `CHANGELOG.md`, `CONTRIBUTING.md`, `SECURITY.md`, `PRIVACY.md`, `PERMISSIONS.md`, `DATA-FLOW.md`, `guides/`, `examples/`, `assets/readme/`, `.github/` | **OMIT from the extension tree** — prose and repository furniture, not extension payload. `PERMISSIONS.md` and `DATA-FLOW.md` are mirrored into `qubit-browser-ext/README.md` as the permission rationale. |
| `Setup_Hermes_Browser_Extension.cmd`, `Copy_Hermes_Browser_API_Key.cmd` | **OMIT** — Windows dispatch scripts for upstream's installer. Qubit's install path is `qubit plugins install` (plugin side) + "Load unpacked" (browser side). |
| `_locales/` at repo root (duplicate of `extension/_locales/`) | **OMIT** — byte-identical duplicate; only `extension/_locales/` is loaded. |
| `manifest.json` at repo root (duplicate) | **OMIT** — duplicate of `extension/manifest.json`. |

---

## (b) Tools — every one, what it does, and which layer answers it

### (b.1) Seven context tools (`tools.py`)

| tool | what it does | answers from | fail-soft shape |
|---|---|---|---|
| `browser_context_status` | metadata of the unconsumed context for this exact owner (no payload) | companion store, via `pre_tool_call` lease | `{"available": false, "reason": "Browser context unavailable."}` |
| `browser_get_context` | consume this turn's BCP v2 payload **once**, by opaque `context_id` | companion store, consume-on-read | same |
| `browser_clear_context` | drop this owner's cached context | companion store | same |
| `browser_event_log` | last N redacted events for this owner (1–50) | companion store event ring | `{"available": false, "reason": …}` |
| `browser_control_status` | historical control-envelope metadata from the turn; `live:false`, never authority | companion store | same |
| `browser_context_journal` | metadata-only journal rows (1–500), never page data | `journal.py` | `{"available": false, "rows": []}` |
| `browser_text_utility` | `clean_formatting` / `make_bullets` / `text_stats` / `diff`, local, no model | `text_utilities.py` | typed error object |

### (b.2) Twenty control tools (`control_tools.py` + `control_schemas.py`)

Wire action → model-facing tool. Each acts **through the browser extension
controller** attached to this session; fail-soft `{"success": false, "error": …,
"code": "no_browser"}` when none is attached.

| wire action | tool | what it does |
|---|---|---|
| `browser_navigate` | `tab_navigate` | load a URL in the tab |
| `browser_snapshot` | `tab_snapshot` | accessibility-tree snapshot with `@eN` refs |
| `browser_click` | `tab_click` | click by ref or x/y |
| `browser_type` | `tab_type` | type into a field (credential fields refused) |
| `browser_fill` | `tab_fill` | set a field value in one step |
| `browser_select` | `tab_select` | choose a `<select>` option |
| `browser_scroll` | `tab_scroll` | scroll by direction/amount |
| `browser_scroll_to` | `tab_scroll_to` | scroll an element into view |
| `browser_hover` | `tab_hover` | hover (needs a foreground tab) |
| `browser_drag` | `tab_drag` | drag one element onto another |
| `browser_press` | `tab_press` | press a key / combo |
| `browser_back` | `tab_back` | history back |
| `browser_screenshot` | `tab_screenshot` | PNG to a private file, `MEDIA:<path>` |
| `browser_batch` | `tab_batch` | up to 20 steps in one call |
| `browser_tabs` | `tab_list` | list the tabs this session may act in |
| `browser_tab_activate` | `tab_activate` | bring a tab forward (approved) |
| `browser_tab_create` | `tab_create` | open a URL in a background tab |
| `browser_tab_close` | `tab_close` | close a tab (approved) |
| `browser_tab_group` | `tab_group` | native tab group (Chromium) |
| `browser_tab_ungroup` | `tab_ungroup` | ungroup (Chromium) |

Registration is decided once at load: where Qubit's **core** extension control
is switched on (`browser.extension_control.enabled`), the extension attaches to
core's broker instead, and the `tab_*` tools are not registered (they could only
answer `no_browser`). That is upstream's rule (`control_tools.core_extension_control_on`)
and it is kept.

### (b.3) Routing decision — which layer answers a call

This is the collision the previous attempt used to justify the drop, so it is
stated explicitly.

Qubit already ships a browser lane: `tools/browser_tool.py` registers
`browser_navigate`, `browser_snapshot`, `browser_click`, `browser_type`,
`browser_scroll`, `browser_back`, `browser_press`, `browser_get_images`,
`browser_vision`, `browser_console`, and `tools/browser_extension_router.py`
routes them to `gateway/browser_control_broker.py` **when**
`browser.extension_control.enabled` is true and the request carries a
server-bound identity; otherwise they fall back to the Camofox/CDP backend.

| caller | Qubit `browser_*` (tools/browser_tool.py) | companion `tab_*` (this plugin) |
|---|---|---|
| serves | every Qubit surface, always listed | always listed; acts only with this session's controller |
| transport | core `gateway/browser_control_broker.py` **or** Camofox | plugin's own `control_broker.py` over the plugin's **own** routes |
| route used by the extension | `/v1/browser-control/{register,ws}` (on the API server) | `/api/plugins/browser-companion/control/{register,ws}` |
| capability allowlist | core's (11 actions + artifacts + developer caps under Developer Mode) | upstream's 20 actions, **no** developer caps, **no** artifacts |
| when core control is ON | answers (`enabled: true`, identity bound) | `tab_*` **not registered** — upstream's own rule |
| when core control is OFF | falls back to Camofox/CDP (a *different* browser, not the person's) | `tab_*` answers through the plugin broker — this is the whole reason upstream has them |

So the two lanes are not redundant: they are active in **disjoint**
configurations, and they answer on **disjoint** tool names, so a model never
sees two tools claiming the same action. `browser_console`, `browser_vision`,
`browser_get_images` have no `tab_*` counterpart upstream and stay Qubit-side
only. Conversely `tab_fill`, `tab_select`, `tab_hover`, `tab_drag`,
`tab_screenshot`, `tab_batch`, `tab_list`, `tab_activate`, `tab_close`,
`tab_create`, `tab_group`, `tab_ungroup` have no Qubit `browser_*`
counterpart in the routed table and exist **only** here — dropping them was a
capability loss, not a de-duplication.

Recording of the rule in code: `control_tools.register_control_tools()` calls
`core_extension_control_on()` → `gateway.browser_control_broker.browser_control_enabled()`,
returns `False` and registers nothing when core control is on, and the
readiness route reports the tools that were actually listed.

---

## (c) HTTP routes and the host seam each needs

Upstream wires all 8 routes from one `register_platform_handler("api_server", factory)`
call, `factory(native, adapter)` where `native` is the aiohttp `web.Application`
and `adapter` is the API-server adapter (`_check_auth`, `_ensure_session_db_async`,
`_expected_api_key`, `gateway_runner`).

**Does Qubit have this seam?** Yes. `qubit_cli/plugins.py:867`
`PluginContext.register_platform_handler(platform, factory)` stores the factory
and `gateway/platforms/base.py:2221 _wire_plugin_handlers()` calls it with
`(native, adapter)` for the connected platform; `_platform_name()` lowers the
`Platform` enum, so the key is `"api_server"` for the API server adapter. The
API server calls `self._wire_plugin_handlers(self._app)` at
`gateway/platforms/api_server.py:4513`, immediately before
`web.AppRunner.setup()` freezes the router — the same ordering upstream relies
on. The previous attempt's claim that "Qubit exposes no
`register_platform_handler`" was **wrong**; this port uses it.

| route | method | needs from the seam | Qubit adapter provides |
|---|---|---|---|
| `/api/plugins/browser-companion/status` | GET | auth check | `_check_auth` ✅ |
| `/api/plugins/browser-companion/context/status` | GET | auth, session id header | ✅ |
| `/api/plugins/browser-companion/context` | POST | auth, bounded body | ✅ |
| `/api/plugins/browser-companion/actions` | GET | auth | ✅ |
| `/api/plugins/browser-companion/control/register` | POST | auth, **session-exists check**, peer address | `_check_auth` ✅, `_ensure_session_db_async` ✅ (API adapter has a lazy per-profile session-DB cache), `request.transport.get_extra_info("peername")` ✅ |
| `/api/plugins/browser-companion/control/ws` | GET/WS | raw `web.WebSocketResponse`, `Sec-WebSocket-Protocol` | aiohttp ✅ |
| `/api/plugins/browser-companion/profiles` | GET | `_expected_api_key`, `gateway_runner.config.multiplex_profiles` | ✅ |
| `/api/plugins/browser-companion/profiles/{name}/avatar` | GET | same | ✅ |

Differences this port absorbs, all in the adapter shim (§G.2):

1. **Router object.** Upstream uses `native.router.add_get/add_post`. Qubit's
   `native` is the adapter's `web.Application`, which has `router` — the same
   API. No change needed.
2. **Session-store opener name.** Upstream calls `adapter._ensure_session_db_async()`.
   The Qubit API adapter exposes `_session_dbs` / `_session_db_cache_lock` and
   an `async` opener inside `gateway/platforms/api_server.py`; the shim probes
   `_ensure_session_db_async` first and falls back to the adapter's own lazy
   accessor, **failing closed** (`503 session_db_unavailable`) when neither
   answers. Upstream's contract is "refuse, never allow" and that is kept.
3. **`_check_auth` semantics.** Qubit's `_check_auth` returns `None` when no
   API key is configured on the default listener (`api_server.py:1489`); a
   roster route must never ride that permissive path. `profile_routes._denied`
   already refuses when `_expected_api_key()` is empty — kept verbatim.
4. **`/p/<profile>/` prefix.** Qubit mirrors native API routes under
   `/p/{profile}` (`api_server.py:4489`), but plugin routes added after the
   mirror loop are **not** prefixed. Upstream documents exactly this
   ("the plugin's routes are wired on the listener only"): a named profile's
   request must therefore carry `X-Hermes-Profile`-style scoping through the
   API key, and the extension keeps the plain path for the default profile.
   Recorded, not "fixed", because the mirror loop is core.
5. **`plugins.isolation: host`.** `qubit_cli/plugin_isolation.py:39` refuses
   `register_platform_handler` under host isolation — upstream's behaviour
   exactly: routes skipped, tools and hooks still load, extension falls back to
   `GET /v1/toolsets`.

**Named gap (small, real):** Qubit has no `/v1/toolsets` route equivalent under
that exact path; the extension's *fallback* probe for "plugin loaded but status
route absent" therefore 404s there. With the seam working this fallback is
unused; it is listed in §G.3 rather than invented.

---

## (d) Rebrand checklist — every legacy identifier

Grep the finished tree for each row; each must have exactly the stated result.
MIT attribution (`Copyright (c) 2026 Jon Komet`, `hermes-browser-extension` as
the upstream repository URL) is the **only** permitted occurrence of the old
name.

### d.1 Wire protocol ids (must match on both sides — a mismatch is a dead channel)

| upstream | Qubit |
|---|---|
| `hermes.browser.turn.v2` | `qubit.browser.turn.v2` |
| `hermes.browser.context.v1` | `qubit.browser.context.v1` |
| `hermes.browser.extraction.v1` | `qubit.browser.extraction.v1` |
| `hermes.browser.inline-draft.v1` | `qubit.browser.inline-draft.v1` |
| `hermes.browser.site-capability.v1` | `qubit.browser.site-capability.v1` |
| `hermes.browser_companion.status` | `qubit.browser_companion.status` |
| `hermes.browser_companion.context_status` | `qubit.browser_companion.context_status` |
| `hermes.browser_companion.context_upload` | `qubit.browser_companion.context_upload` |
| `hermes.browser_companion.actions` | `qubit.browser_companion.actions` |
| `hermes-browser-control-v1` (WS subprotocol) | `qubit-browser-control-v1` |
| `hermes-browser-control-ticket.` (ticket subprotocol prefix) | `qubit-browser-control-ticket.` |
| `browser.controller.*` frame methods | **unchanged** (action verbs, not brand) |

### d.2 HTTP paths

| upstream | Qubit |
|---|---|
| `/api/plugins/hermes-browser-extension/**` | `/api/plugins/browser-companion/**` |
| `X-Hermes-Session-Id` | `X-Qubit-Session-Id` |
| `X-Hermes-Profile` | `X-Qubit-Profile` |

### d.3 Plugin identity

| upstream | Qubit |
|---|---|
| plugin name `hermes-browser-extension` | `browser-companion` |
| toolset `hermes-browser-extension` | `browser-companion` |
| legacy plugin name `hermes-browser-companion` | *(not carried — no predecessor in Qubit)* |
| skill `hermes-browser-extension` | `browser-companion` |
| `requires_hermes:` | `requires_qubit:` |

### d.4 Extension globals, env vars, storage keys

| upstream | Qubit |
|---|---|
| `window.__HERMES_*` | `window.__QUBIT_*` |
| `__HERMES_PLUGIN_SDK__` | `__QUBIT_PLUGIN_SDK__` |
| `globalThis.hermesBrowserApi` | `globalThis.qubitBrowserApi` |
| `globalThis.hermesBrowser*` (any) | `globalThis.qubitBrowser*` |
| storage key `hermesBrowserSettings` | `qubitBrowserSettings` |
| `hermesBrowserInlineDraftRequest`, `hermesBrowserInlineSessionState`, `hermesBrowserOpenSessionRequest`, `hermesBrowserLocale`, `hermesBrowserWakeTurn`, `hermesBrowserWakeState`, `hermesBrowserUpdateCheck`, `hermesBrowserTabLeases`, `hermesBrowserSessionContext`, `hermesBrowserRewindRecovery` | `qubitBrowser*` |
| `hermesWebappCredential`, `hermesWebSignedInRuntime`, `hermesWebOpeningPending`, `hermesWebappSignInHintSeen` | `qubitWebapp*` / `qubitWeb*` |
| `hermesOnboardingCompleted`, `hermesReplayWatermarks`, `hermesLastKnownRoster`, `hermesDesktopRosterUrl`, `hermesGatewayQuirks`, `hermesLegacySessionForks`, `hermesInlineSiteContextPreferences`, `hermesInlineAssistHiddenSites`, `hermesInlineAssistAutoReplace`, `hermesPetdexManifest`, `hermesSessionMenuTab`, `hermes_browser`, `hermes_bot_mode`, `hermes_web` | `qubit*` |
| message types `HERMES_*` (e.g. `HERMES_INLINE_DRAFT_RESULT`) | `QUBIT_*` |
| runtime id `'hermes-browser'` | `'qubit-browser'` |
| `HERMES_SESSION_ID`, `HERMES_BROWSER_CONTROL_PRINCIPAL`, `HERMES_BROWSER_CONTROL_TRANSPORT_FAMILY` (read by Qubit's router) | **leave Qubit's own env names alone** — the plugin reads session state through Qubit's `session_context`, not by inventing new vars |

### d.5 Files and assets renamed

`hermes-client.mjs`→`qubit-client.mjs`, `hermes-webapp.mjs`→`qubit-webapp.mjs`,
`hermes-webapp-dialog.mjs`→`qubit-webapp-dialog.mjs`, `hermes-account.mjs`→`qubit-account.mjs`,
`hermes-context-lengths.mjs`→`qubit-context-lengths.mjs`,
`hermes-context-registry-data.mjs`→`qubit-context-registry-data.mjs`,
`hermes-context-sync.mjs`→`qubit-context-sync.mjs`,
`hermes-context-windows.mjs`→`qubit-context-windows.mjs`,
`hermes-web-opening.mjs`→`qubit-web-opening.mjs`,
`hermes-web-opening.js`→`qubit-web-opening.js`,
`hermes-web-opening.html`→`qubit-web-opening.html`,
`hermes-web-opening.css`→`qubit-web-opening.css`,
`hermes-web-launch.js`→`qubit-web-launch.js`, `hermes-web-launch.css`→`qubit-web-launch.css`,
and every `assets/img/hermes-*` → `assets/img/qubit-*`.

### d.6 Prose and UI copy

`Hermes Browser Extension`, `Hermes Browser`, `Hermes Agent`, `Hermes Web`,
`Hermes Assist`, `Hermes Control`, `Hermes` → `Qubit Browser Extension`,
`Qubit Browser`, `Qubit`, `Qubit Web`, `Qubit Assist`, `Qubit Control`, `Qubit`
— in `_locales/**` (21 locales), `lib/locales/**` (21 locales), every HTML
title/label, and every JS string. Commands in copy: `hermes webapp` → `qubit webapp`,
`hermes plugins …` → `qubit plugins …`, `hermes tools` → `qubit tools`,
`~/.hermes` → `~/.qubit`.

### d.7 Defaults that must point at Qubit

The extension's default gateway URL is `http://127.0.0.1:8642` upstream. Qubit's
API server defaults to `127.0.0.1:8642` (`qubit_cli/gateway_migrate.py:583`), so
the port keeps 8642, and the agent-discovery port list `[8642…8646]` is
unchanged. The **dashboard** (`9119`) is a different listener and is not what
the companion routes live on.

---

## (e) Wire contract — the proof each side must satisfy

Every row is checked by `qubit_runtime/plugins/browser-companion/wire_check.py`,
which imports the real plugin modules and asserts the extension-side constants
parsed out of the ported `.mjs`/`.js` files. Both sides are read as text/files,
so a half-rename cannot pass.

| # | extension sends | companion accepts | assertion |
|---|---|---|---|
| 1 | `GET /api/plugins/browser-companion/status` (`COMPANION_STATUS_PATH`) | `status_route.ROUTE_PATH` | paths equal; `plugin` field equals the route's plugin name |
| 2 | expects `object === 'qubit.browser_companion.status'` | `status_route.STATUS_OBJECT` | equal |
| 3 | expected tool list `COMPANION_EXPECTED_TOOLS` | `browser_companion.TOOL_NAMES` | 7 context tools ⊆ extension expectation |
| 4 | `GET …/context/status` | `context_routes.CONTEXT_STATUS_PATH` | equal; object id equal |
| 5 | `POST …/context` + `X-Qubit-Session-Id` | `context_routes.CONTEXT_UPLOAD_PATH`, `SESSION_HEADER` | equal |
| 6 | `GET …/actions` | `context_routes.ACTIONS_PATH` | equal; object id equal |
| 7 | `POST …/control/register` | `control_routes.CONTROL_REGISTER_PATH` | equal |
| 8 | `GET …/control/ws` | `control_routes.CONTROL_WS_PATH` | equal |
| 9 | WS protocols `qubit-browser-control-v1` + `qubit-browser-control-ticket.<t>` | `control_routes.WS_PROTOCOL`, `TICKET_PROTOCOL_PREFIX` | equal |
| 10 | `POST …/profiles` | `profile_routes.ROSTER_PATH` | equal |
| 11 | render: `serializeBrowserTurnEnvelope()` emits `qubit.browser.turn.v2` | `context_store.parse_bcp_v2_turn` accepts `qubit.browser.turn.v2` | extension constant == `BCP_TURN_PROTOCOL_ID`; a real envelope with that id parses |
| 12 | action names → tool names (`control-tool-names.mjs PLUGIN_TOOL_NAMES`) | `control_schemas.TOOL_NAMES` | dict equal |
| 13 | message `browser.controller.command` params `{command_id, action, arguments, tool_call_id}` | `control_broker.dispatch` frame | keys equal |
| 14 | `browser.controller.result` `{command_id, ok, result|error}` | `control_routes.handle_frame` | accepted and resolves pending |

---

## (f) Acceptance

1. **Discovery proof.** `discover_plugins(force=True)` with a temp `QUBIT_HOME`;
   the plugin is present, has no load error, and all 27 declared tools are in
   `tools.registry` under toolset `browser-companion`; all 3 hooks attached;
   the skill registered.
2. **Tool registration list** printed, tool by tool.
3. **Manifest validation.** MV3, every referenced file exists inside the
   extension dir, every `__MSG_*` message key exists in `_locales/en`, every
   `content_scripts` js/css entry present, `web_accessible_resources` present,
   icons present.
4. **Rebrand grep** — §D rows, zero leftovers outside attribution.
5. **Wire contract** — §E rows, printed from both files.

## (g) Deliberate omissions (short, each names the lost capability)

1. **Upstream's Node build/release tooling** (`scripts/`, `package.json`,
   `eslint.config.js`). Lost: `npm run build` (writes `dist/` and a
   `build-info.json` blob map), `web-ext` packaging/signing, the upstream
   lint suite. Not needed to load unpacked; Qubit ships the tree as-is.
2. **Upstream's Node test suite** (`tests/*.test.mjs`, ≈150 files). Lost:
   upstream's own regression net for the browser side. Qubit adds
   `wire_check.py` and the manifest validator instead; the gap is real and
   named here rather than papered over.
3. **Windows `.cmd` installers and repo prose** (`Setup_*.cmd`, `Copy_*.cmd`,
   `assets/readme/`, `guides/`, `.github/`). Lost: upstream's Windows one-click
   setup path; Qubit's install is "load unpacked" + `qubit plugins install`.
4. **Root-level duplicate `manifest.json` and `_locales/`.** Lost: nothing —
   byte-identical copies of `extension/`'s.
5. **The `/v1/toolsets` fallback probe.** The extension keeps calling it (it is
   upstream code, unchanged), but Qubit has no route at that exact path, so the
   probe 404s and the extension reports "cannot confirm toolset state". With
   the status route working this fallback is never reached. Recorded as a gap
   rather than adding a route to core.

---

## (h) What shipped (filled in after the port)

See §G of this file at the end — the per-file accounting, the discovery proof,
the tool registration list, the manifest validation and the wire proof, all
from real runs.

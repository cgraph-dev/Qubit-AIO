# Packet 8L — product/design source of truth and UI qualification

This document is the concise product/design source of truth for the Qubit Voice
Agent dashboard required by Packet 8L. It lists every route/page/component, the
shared design primitives every page must use, the WCAG 2.2 AA release contract,
the typed-state and plain-language copy rules, and the data-lifecycle controls.
It extends `DESIGN.md` (visual language) and `PRODUCT.md` (product brief) with
the implementation-level contract that the verification agent checks.

Scope boundary: Packet 8L changes only the static dashboard surface and its
tests. It does not change backend safety semantics, API contracts, routes,
voice/audio/model/provider behavior, permissions, or live services.

## 1. Route / page / component inventory

All pages are static HTML served by the existing `ThreadingHTTPServer`
(`voicebridge/webui.py`). Every JSON API call must go through the shared
authenticated typed helper (`frontend/vb.js` `vbApi`); the only raw `fetch`
left in the frontend is the SSE event stream, which is centralized in the
shared stream helper (`frontend/ui.js` `vbStream`) because `vbApi` is a JSON
helper and `EventSource` cannot send the `X-Api-Token` header.

| Route | Page | Components | API calls |
|---|---|---|---|
| `/` | `frontend/index.html` Talk | status (`#st`, aria-live), **first-run onboarding help panel** (`details.help-panel` "How Qubit works" naming the safe workflow and the canonical `webui.token` location), push-to-talk, typed input + send, transcript (textContent), interrupt, learned-preferences panel, permissions panel, inbox/report panel, SSE live stream (typed events → meta lines), connection settings drawer (`vdb_api`/`vdb_token`) | `/api/status`, `/api/type`, `/api/talk`, `/api/result/*`, `/api/interrupt`, `/api/feedback`, `/api/lessons`, `/api/lessons/remove`, `/api/lessons/promote`, `/api/permissions`, `/api/reports`, `/api/reports/*/ack`, `/api/reports/*/export`, `/api/reports/*/html`, `/api/reports/*/assets/*`, `/api/runs/*/cancel`, `/api/events/stream` (SSE) |
| `/activity` | `frontend/activity.html` Activity | status (`#conn`, aria-live), legend, kind filter, search, pause, item feed (DOM-built cards) | `/api/status`, `/api/activity` |
| `/reports` | `frontend/reports.html` Reports | toolbar (search/category/days/refresh/prune), status (`#conn`, aria-live), meta, report feed (DOM-built cards), inline typed status message | `/api/status`, `/api/reports`, `/api/reports/prune` |
| `/settings` | `frontend/settings.html` Settings | beginner/advanced toggle, health, model/STT/TTS/voice profiles, audio, permissions, permissions mode, websites, actions, device, export/import, upgrade, reset, branding | all via `api()` wrapper around `vbApi` (`/api/status`, `/api/profiles`, `/api/providers/*`, `/api/audio/*`, `/api/permissions*`, `/api/websites*`, `/api/actions*`, `/api/device*`, `/api/state/*`, `/api/upgrade*`, `/api/branding*`) |
| `/approvals` | `frontend/approvals.html` Approvals | pending list (DOM-built cards), approve/deny buttons (addEventListener + `data-id`) | `/api/approvals`, `/api/approvals/respond` |
| `/token-setup` | `frontend/token-setup.html` Pair device | token input (password), save & test (via `vbApiTest`), plain-language result; help copy points at the canonical `webui.token` value in `~/.config/qubit-voicebridge/voice.yaml` | `/api/status` |

Shared static assets:

| Asset | Purpose |
|---|---|
| `frontend/app.css` | Design tokens, typography, spacing, color, motion, focus, reduced motion, forced colors, responsive/reflow, 44 px touch targets |
| `frontend/ui.js` | Shared rendering primitives: `vbBuild`/`vbAppend`/`vbClear` (safe DOM, textContent-based), `vbStatus` (typed states), `vbApiMessage` (plain-language errors), `vbAnnounce` (live region), `vbSafeHTML` (audited sanitizer stripping `on*` handlers / `javascript:` / script / iframe), `vbStream` (SSE fetch helper), `vbResolveBase` (same-origin only — an attacker-controlled `?api=` query string is NEVER honored) |
| `frontend/vb.js` | Shared authenticated typed API helper (`vbToken`/`vbApiBase`/`vbHeaders`/`vbPairHint`/`vbApi`), plus authenticated binary/download/open helpers (`vbFetchBinary`/`vbDownload`/`vbOpenBinary`) and the explicit pairing helpers (`vbValidateApiOrigin`/`vbApiTest`). Token lives only in `localStorage`, never in a URL |

## 2. Design source of truth (shared primitives)

- Every page is branded consistently: `<title>` is always
  `Qubit Voice Agent — <Section>` (Talk / Activity / Reports / Settings /
  Approvals / Pair device) and the `<h1>` heading carries the brand mark plus
  the product name. The only page-specific inline `<style>` blocks use the
  shared `app.css` design tokens (near-black surfaces, warm text, restrained
  signal-lime accent) — the legacy GitHub-blue palette is gone from the
  frontend (enforced by `tests/test_frontend_hygiene.py`).
- Design tokens (color, spacing, radius, shadow, motion curves, focus color)
  live only in `frontend/app.css` `:root`. Pages must not hardcode new
  palette/motion values; they may keep their own layout classes but must not
  fight the shared foundation with `!important` (existing page rules are
  already harmonized by `app.css`).
- Every interactive control has a visible focus style (`:focus-visible`) and a
  minimum 44x44 px target (`min-height`/`min-width` on buttons, inputs,
  selects, textareas, nav links, and the shared `.tap-target` class applied to
  inline/anchored controls including dynamically generated links such as the
  Talk interrupt link, report "Open report"/source links, and the pairing hint).
- Every route exposes exactly one real semantic `<main id="main-content"
  tabindex="-1">` landmark targeted by the shared skip link; the landmark is
  never a `div` or heading.
- Motion uses `transform`/`opacity` and is disabled under
  `prefers-reduced-motion`.
- Typography scales with `clamp()`. Layout reflows at 320 px without horizontal
  overflow and at 200% zoom without losing controls.
- Status is rendered through the shared typed-state helper: one of
  `live|ready|offline|degraded`, plus plain-language copy that names the next
  safe action. Optimistic success is forbidden.
- All backend/user/page text is untrusted: dynamic content is rendered with
  `textContent` through `vbBuild` (activity/approvals/reports, and index.html
  already) or, in settings.html, through DOM construction (`vbBuild` /
  `fillOptions`, data attributes, `addEventListener`) and the shared audited
  sanitizer `vbSafeHTML`. No page parses a non-JSON body; `vbApi` checks
  `Content-Type` before `JSON.parse`.
- API origin is same-origin/loopback by default. An attacker-controlled `?api=`
  query string is ignored (never persisted, never contacted). A remote bridge
  is used only through the explicit Settings/Pairing action, which validates
  the origin (`vbValidateApiOrigin`), asks for confirmation, tests the
  candidate token with the `X-Api-Token` header before saving, and preserves
  the previous working token on 401/network failure. The stored token is only
  ever sent to the approved origin.
- Report export/HTML/screenshot operations fetch through the shared
  authenticated binary helper (`vbFetchBinary`) and are delivered via Blob
  object URLs/downloads, never as unauthenticated `<a href>`/`<img src>`
  URLs; object URLs are revoked after use where safe.

## 3. WCAG 2.2 AA release contract

Packet 8L treats WCAG 2.2 AA as required. The contract and its evidence are:

| Requirement | Implementation | Evidence |
|---|---|---|
| Keyboard operable, visible focus | `:focus-visible` on all controls, skip link, focusable nav | `frontend/app.css`; Playwright `ui` tests |
| Focus order / restoration | single-column DOM order; `#main-content` target for skip link | static + browser tests |
| Screen reader semantics | `lang="en"`, one semantic `<main>` landmark per route, labels for every input/select, `aria-current` on active nav, `aria-hidden` decorative brand marks | `tests/test_frontend_hygiene.py` + browser `main`-landmark test |
| Live announcements | `aria-live="polite"` status spans; `vbAnnounce` for transient results | browser + static tests |
| Touch targets | 44x44 px min on every visible button/link/input/select/textarea, including dynamically generated controls (`.tap-target`) | `app.css`; browser enumeration test at desktop + 320 px |
| 320 px reflow | responsive rules + `min-width: 320px` | browser test (no horizontal overflow) |
| 200% zoom | fluid widths, no fixed-width chrome | browser test |
| Forced colors / high contrast | `@media (forced-colors: active)` overrides in `app.css`; contrast-checked palette | static + browser test |
| Reduced motion | `prefers-reduced-motion` override sets animation/transition to ~0 | browser test |
| Plain-language copy | typed-state messages that state the next safe action; beginner mode hides technical IDs | copy in pages + this doc |

Accessibility automation is run with `pytest -m ui` (`tests/test_ui_browser_8l.py`)
against a local authenticated server; a skipped live/browser check is reported
as DEGRADED, never as a pass.

## 4. Data-lifecycle and destructive controls

- Destructive delete/reset/revoke actions stay separately authenticated,
  confirmed, audited, and non-default (e.g. reports "prune old" requires a
  `confirm()` and calls the real backend; settings reset/rollback call the real
  backend and report typed results). Frontend-only success is rejected.
- Export/import, upgrade, reset, and rollback remain backend contracts
  (`/api/state/*`, `/api/upgrade*`) with typed results; this packet adds no new
  backend behavior.
- No secret, token, cookie, private page text, or private path is rendered,
  logged, or placed in URLs by these pages. Report/asset URLs use generated IDs
  only; the token travels only in the `X-Api-Token` header.

## 5. Known limitations carried by Packet 8L

- `settings.html` keeps its escaping choke-point (`show()` + `esc()` with quote
  escaping) for large structured views; it is covered by static hygiene checks
  (no unescaped dynamic interpolation, no raw `/api` `fetch`). Full DOM-based
  rendering of settings is a follow-up, not a release blocker.
- Timezone/localization beyond English + Europe/Bucharest and the ten
  non-blocking nice-to-haves are explicitly out of scope.
- Real desktop/mobile browser matrices and screen-reader passes are verified by
  the independent verification agent against the `ui` test gate and manual
  flows; this implementation provides the harness and static/browser evidence.
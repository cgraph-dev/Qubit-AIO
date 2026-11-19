# Qubit Manga UI — final integration QA and asset-use notes

This PR implements a **source-level completion pass**, not a claim of green browser QA.

## Included changes
- Promotes the generated V3 navigation SVG geometry to **real, live** Manga sidebar link backgrounds (normal, hover, selected) and section headers. The artwork is vector and does not contain text.
- Promotes V3 yellow corner artwork for panels and inspector modules; preserves original source geometry with only unused gradient definitions removed.
- Adds stable `data-qm-surface` markers to shared `SettingsPageHeader`, `SettingsSection`, `SettingsGroup`, `SettingsRow`, `SettingsEmpty`, and `SettingsLoadingSkeleton` React components. New Manga CSS uses those explicit markers instead of guessing a route's class fragments.
- Fixes collapsed sidebar language width: the control no longer reserves 120px in a 56px rail.
- Corrects the Q6 theme description (was mislabelled Q4).
- Adds contract tests for asset presence, Manga route gating, shared surface markers, viewport/rail CSS, and motion accessibility.
- Includes a read-only Chromium browser screenshot audit at `scripts/audit-manga-ui.mjs` to flag overlap, offscreen controls, and horizontal scrolling at 390/768/1280/1600px.

## Existing features protected
No backend, desktop, Workbench, HUD, Office, or `/chat` TUI code is changed. The new CSS is only enabled when `html[data-qubit-manga="on"]` exists; the gate excludes protected routes. Sakuga engine and effect handler code are unchanged.

## Why the entire 186-SVG library is not blindly copied
Many V3 assets are full, fixed-size illustrated panels. They cannot be used as the live background of responsive tables or dialogs without distorting corners and possibly obscuring real text/focus. The appropriate technique is to extract a scalable frame/corner or use a semantic React wrapper with real content. This PR integrates the five most usable nav/corner SVGs and documents the rest for future selection.

## Required acceptance checks in the real Qubit installation
Run:
```sh
cd frontend/qubit-web
npm ci
npm run typecheck
npm run test
npm run lint
npm run build
# In the actual local dev/QA environment, ensure Playwright is installed.
BASE_URL=http://localhost:5173 node scripts/audit-manga-ui.mjs
```
Provide `PLAYWRIGHT_STORAGE_STATE` for authenticated Qubit if required.
The audit writes a `manga-visual-audit` screenshot/report directory and does not modify server data. Set `OUTPUT_DIR` to choose where files go.

Then manually review:
- Q6/Q6-L against provided reference screenshots: cat emblem, pink panel titles, stars and 3 big strokes, sharp layered corners, consistent spacing and color emphasis.
- Expanded/collapsed/mobile sidebar, narrow right inspector, font loading.
- Dropdown placement while scrolling, keyboard Escape/Tab, overlay hierarchy, theme selection, model/profile menus.
- Cron create/edit and advanced, all time modes, approval/error, no horizontal scrolling or hidden footer.
- Settings, Sessions, Analytics, Models, Agents, Files, Tasks, Plugins, MCP, Channels, Voice, OpenViking and nested dialogs.
- Reduced motion, non-Manga default theme, non-English locale.
- Protected paths `/chat` TUI, Workbench, HUD, Office/2/3 unchanged.

**Status:** Source changes committed and static marker checks added. Passing typecheck/build/browser audit not established by GitHub access alone, and prior CI jobs failed in the base repository. Do not call all pages 100% visually verified until that browser matrix passes.

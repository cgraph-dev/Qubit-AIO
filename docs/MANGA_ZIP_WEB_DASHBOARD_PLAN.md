# Qubit Manga ZIP → Web Dashboard integration plan

**Source of truth:** user-provided `qubit_manga_web_install(2).zip`. It contains 89 named design assets, each in SVG and PNG (178 visual files), the `asset-manifest.json`, `palette.json`, `src/manga-skin/manga.css`, `MangaSkin.tsx`, `index.ts`, `INSTALL.md`, and `COVERAGE.md`.

## Non-negotiable constraints
- Implement on **frontend/qubit-web** only; never change desktop, gateway behavior, API, task/agent logic or routes.
- Keep the repository's current **Q6/Q6-L** Manga gate and navigation, not the ZIP's obsolete standalone toggle implementation.
- Do not overwrite live routes with flattened SVG screenshots. Frame art decorates live, responsive DOM.
- Preserve `aria-current="page"` yellow selected-state and star-pop animation, the current collapse/expand button, and real gateway/session/latency values.
- Honor reduced motion, focus-visible, RTL/translation where possible, and desktop/mobile zoom.
- Explicitly exclude Workbench, HUD, and Office variants from global Manga overlay unless approved separately.

## Phase 0 — exact asset import and mapping
**Location:** `frontend/qubit-web/public/manga-ui/zip/` (keep original archive filenames, never overwrite previous `manga-ui` assets).
- Use `scripts/install_manga_zip_assets.py` to install and verify all **89 SVG + 89 PNG** original pairs and the manifest/palette from an original ZIP file.
- Verify all manifest names have both variants and there are no extra/missing names.
- Prefer SVG at runtime. PNGs are reference/backup assets and must not be stretched as live text surfaces.
- Lock SHA-256 checksums of the source ZIP assets in a generated asset checksum report. Ensure no asset accidentally embeds labels or fake telemetry values.
- **Status:** installer added, full upstream ZIP binaries need to be copied to repository/release artifacts before claiming repository-native deployment. The gateway SVG alone is already in the active Manga CSS.

## Phase 1 — shared shell, rail, brand
**Components:** `src/App.tsx`, `src/components/SidebarFooter.tsx`, `src/manga-skin/*.css`.
- `sidebar-expanded`, `sidebar-collapsed`: rail corner details only; keep width animation/responsive layout.
- `header-full`, `footer-full`, `page-title-bar`, `breadcrumb-bar`, `module-header`: shell header/footer/title/breadcrumb/header plate treatment.
- `nav-default`, `nav-hover`, `nav-focused`, `nav-selected`, `nav-disabled`: link states (active yellow remains untouched).
- `sidebar-gateway`, `sidebar-footer-plate`: pinned live telemetry/buttons and footer.
- `impact-yellow`, `impact-magenta`, `impact-cyan`, `corner-* `: animated/focus decoration.
- Use a proper anime sparkle instead of the malformed lightning element; animate **three separate charging cells** with staggered keyframes.
- **Acceptance:** exact expected 01–07 Manga ordering; no clipping of ornaments; no change to selected route or collapse controls; gateway panel displays backend values (not baked text).

## Phase 2 — shared app surfaces and controls
**Location:** `src/manga-skin/manga-zip-surfaces.css` layered AFTER existing Manga CSS.
- `panel-xs/sm/md/lg/xl/horizontal/vertical`: shared card/panel frames and reusable corner treatments.
- `button-magenta/violet/yellow/cyan/green/red`: semantic variants only; retain disabled/busy/hover affordances.
- `badge-magenta/violet/yellow/cyan/green/red`: text-bearing status badges.
- `input`, `number-input`, `textarea`, `select`, `search`, `filter-input`, `date-picker`, `dropdown-panel`: field states, overlays, focus.
- `modal-confirm`, `modal-warning`, `tooltip`, `toast-info`, `toast-error`, `progress-track`, `pagination`: popovers, overlays and navigation.
- `separator-line`, `divider-module`, `timeline-rule`: small repeated separators.
- **Acceptance:** no content hidden behind overlay corners, no unsafe button skinning, accessible focus indicators, no full-surface bitmap stretching.

## Phase 3 — page-specific surfaces
- **Chat**: `chat-transcript`, `chat-composer`, `chat-user-bubble`, `chat-agent-bubble`, `chat-tool-card`, `chat-artifact-card`.
- **Chat right sidebar**: `right-sidebar`, `right-model`, `right-reasoning`, `right-tools`, `right-sessions`, `right-status`.
- **Terminal/code**: `terminal-shell`, `code-block`.
- **Files**: `file-tree`, `file-grid`, `upload-dropzone`.
- **Analytics**: `analytics-chart`, `analytics-stat`; preserve chart readability.
- **Lists/tasks**: `table-surface`, `table-compact`, `task-card`, `kanban-column`, `calendar`.
- **Settings/profiles/plugins/models**: `settings-section`, `settings-row`, `profile-card`, `plugin-card`, `model-card`.
- **Voice/media**: `voice-panel`, `media-toolbar`.
- Attach using current page component classes (do not invent missing routes); unhandled assets are registered but not falsely presented as live features.
- **Acceptance:** representative screens for every eligible route on desktop 1440×900, compact desktop 1024×768, mobile 390×844.

## Phase 4 — QA and safe merge
- Run from `frontend/qubit-web`: `npm run typecheck`, `npm run test`, `npm run lint`, `npm run build`.
- Automated contracts: source asset counts and hashes; no missing CSS URL targets; default vs Q6/Q6-L gating; reduced motion; selected yellow transition; collapse; disabled controls.
- Browser screenshot comparisons: baseline uploaded screenshot vs implementation across Chat, Workspace, System, Settings, Plugins, right sidebar.
- Animation checks at 0ms / 700ms / 1400ms: visibly staggered charging boxes; stars glow without deformation; no flashing during route change.
- Check PR diff, CI, browser regressions and **only merge if verified**. Record unresolved deviations rather than claim pixel-perfect equivalence.

## Known mistakes corrected or pending
- A status-panel frame from the archive had not been installed: **fixed for the SVG** in `manga-zip-sidebar-fix.css`.
- A sharp lightning ornament was used where an anime star was requested: **fixed** with `manga-anime-star.svg`.
- Keyframe colors were suppressed by `!important` declarations: **fixed** and contract tested.
- Earlier code imported only a handful of the 89 ZIP families: **requires import/deployment of all remaining original files** before completion can be declared.
- ZIP has an old `MangaSkinToggle` implementation; replacing the current gate would be a regression: **do not copy it over**.

## Integration implementation status (PR #16, 2026-11-19)

- **Phase 0 — Source asset import: implemented.** The exact uploaded ZIP is kept in ten under-500-KiB text parts at `frontend/qubit-web/scripts/manga-original-zip-parts/`. SHA-256 `e385c8c5c5b928c84ea8fe88d975e41b65ba903b8ad58f44878f5982feaf68d0`; the npm pre-hooks reconstruct every original SVG and PNG (89 of each) into `public/manga-ui/zip/`.
- **Phase 1 — Shared rail/system: implemented in source.** The non-deformed yellow anime atom is `public/manga-ui/manga-anime-atom.svg`; `manga-atom-titles.css` is the final layer for hot-pink title plates and staggered yellow cells. The System panel uses the original ZIP gateway frame and live React telemetry. Native collapse and selected-route behavior are untouched.
- **Phase 2 — Shared controls: implemented in source.** `manga-zip-controls.css`, `manga-zip-surfaces.css`, and `manga-zip-registry.css` expose all 89 families and apply relevant page and interaction styles only when the Manga gate is enabled and the extracted manifest exists.
- **Phase 3 — Page-specific integration: implemented in source for existing React surfaces.** `manga-zip-routes.css` attaches route-scoped frames, and real Chat/Files/Analytics/Models/Plugins/Profiles React nodes carry relevant `data-qm-zip-asset` markers. Other route classes use shared settings, chat, table, status, forms, dialogs and right-rail layers. Asset variants without a matching live component remain available in the 89-family registry; they do not generate fake features.
- **Phase 4 — Verification: source asset checks passed; CI and live screenshot acceptance remain required.** The original ZIP can be reconstructed byte-for-byte, all 178 art files exist, all CSS URL references map to source asset names, and CSS brace balance checks passed. Do not claim browser pixel fidelity until CI/e2e and a running-dashboard screenshot comparison have been reviewed.

The ZIP's legacy `MangaSkinToggle` and top-level installer CSS were deliberately **not** blindly copied into the new theme architecture; doing so would break Q6/Q6-L gating and the current user navigation.

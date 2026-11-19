# Manga reference assets — reuse audit (2026-11-19)

## Findings
The previous styling passes repeatedly handcrafted CSS while leaving most of the already-generated reusable vector assets out of the repository. The Q6 Manga asset directory currently has **only eight** SVGs; the conversation's V2, V3, V4, and installation ZIPs contain considerably more.

Local source packs supplied earlier:
- `Qubit_Manga_Web_V2_assets.zip`: 73 SVG + 73 PNG.
- `Qubit_Manga_Web_V3_assets.zip`: 186 SVG + 186 PNG.
- `Qubit_Manga_Web_V4_kit.zip`: 54 SVG + 54 PNG.
- `qubit_manga_web_install.zip`: 89 SVG + 89 PNG, including React/CSS sources.

These files overlap; do not sum counts or assume every output is unique. All references are **design assets**, not completed code integration. Some are fixed-size panel illustrations with their own internal graphics; avoid using those as backgrounds behind actual text or using scaled PNG atlas screenshots for interactive controls.

## Reference mapping — actual pre-existing V3 SVG paths
| Real web surface | Use / evaluate from V3 | Design role |
|---|---|---|
| Sidebar expanded | `svg/sidebar-frame.svg`, `svg/shell-left-rail-overlay.svg` | layered exterior frame — must not constrain scroll |
| Sidebar collapsed | `svg/shell-compact-header.svg`, V4 `svg/sidebar_left.svg` | mobile/compact rail, deliberate narrow control layout |
| Sidebar nav | `svg/nav-row-normal.svg`, `svg/nav-row-hover.svg`, `svg/nav-row-selected.svg`, `svg/nav-section-title.svg` | large readable state plates |
| Brand | Manga black-cat/blue-eye `public/manga-ui/qubit-cat-atom.svg` | emblem, separate accessible Qubit text |
| Page/header chrome | `svg/shell-topbar.svg`, `svg/page-title-strip.svg`, `svg/page-header.svg` | title geometry and top bar |
| Corners | `svg/ornament-corner.svg`, `svg/corner-yellow.svg`, `svg/corner-pink.svg`, `svg/ornament-burst.svg` | edge impact, sparingly |
| Right inspector | `svg/right-sidebar-frame.svg`, `svg/chat-model-module.svg`, `svg/chat-tools-module.svg`, `svg/chat-skills-module.svg`, `svg/chat-sessions-module.svg` | consistent module shells |
| Common cards | `svg/content-card.svg`, `svg/compact-card.svg`, `svg/metric-card.svg`, `svg/settings-page-block.svg` | panel proportions |
| Forms | `svg/form-section.svg`, `svg/input-medium.svg`, `svg/select-field.svg`, `svg/input-number-spinner.svg`, `svg/textarea-frame.svg` | visual references for actual live controls |
| Tables/charts | `svg/table-panel.svg`, `svg/table-header-strip.svg`, `svg/chart-panel.svg`, `svg/analytics-stat-grid.svg` | structure for dashboard data |
| Dialogs | `svg/dialog-frame.svg`, `svg/modal-action-strip.svg`, `svg/modal-confirm.svg`, `svg/drawer-frame.svg` | responsive modal chrome |
| Chat | `svg/chat-composer-frame.svg`, `svg/chat-transcript-panel.svg`, `svg/chat-message-agent.svg`, `svg/chat-message-user.svg` | visuals only for web chat; never /chat TUI |
| Status/progress | `svg/status-readout.svg`, `svg/progress-pink.svg`, `svg/badge-yellow.svg`, `svg/toast-panel.svg` | feedback and activity |

## Rules before adding more CSS or assets
1. First inventory existing SVGs in all four archives, check dimensions and geometry and use a single canonical name per role.
2. Prefer the SVG as an ornament/frame in a real semantic React component; keep labels, buttons, focus rings, animations and accessibility live in DOM.
3. A fixed-size, fully illustrated mock panel is *not* a suitable nine-slice responsive panel by default. Confirm stretch regions; otherwise extract its border motif into a frame/ornament component and let content flow naturally.
4. Reference frames must not cover or capture clicks, cut off text, reduce hit targets, or create horizontal scroll.
5. Header accent: large star and **three distinct ink strokes**; avoid tiny repeating diagonal caution stripes.
6. Collapsed 56px sidebar requires independent icon-only UI and vertical action layout. Never hide overflow to conceal overlapping functional controls.
7. Use Manga CSS behind `html[data-qubit-manga="on"]` and route gate; keep /chat TUI, Workbench, HUD, Office, desktop and backend unchanged.
8. Never modify Sakuga's engine, event handlers or VFX canvas; retain reduced-motion behavior.
9. Validate real app screenshots against owner's reference at 390, 768, 1280 and 1600px, normal and collapsed sidebar, with dropdowns open.
10. Do not claim full page coverage until nested states, modals, input boxes and subpanels have been checked in a browser.

## Current status
PR #6 includes initial cat branding, collapsed sidebar CSS, title panels and right-inspector improvements. The larger V3/V4/installation asset libraries are **not imported into the repository**. This document identifies a source-of-truth to prioritize actual component integration, not proof of finished visual QA.

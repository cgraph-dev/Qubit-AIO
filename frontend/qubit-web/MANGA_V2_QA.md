# Manga Web V2–V5 refinement

## Design goal
Implement the reference anime-game UI in the Q6 / Q6-L **main React web dashboard**, not just re-tint the old interface. Visual themes: vivid magenta, yellow lightning/star accents, black/white/magenta comic frames, angular title plates, sharp vector corners, clear UI typography, and limited expressive anime motion.

## Explicitly excluded
- `/chat` TUI and descendants; `mangaRouteEligible` is false.
- `/qubit-workbench`, `/qubit-hud`, `/qubit-office`, `/qubit-office2`, `/qubit-office3` and descendants.
- Electron desktop `qubit_runtime/apps/desktop/**`.
- Backend and app data.

## Files and responsibilities
- `src/manga-skin/manga-v2.css`: sharper brand, rail, headers, native select menu, body typography, right chat panel.
- `src/manga-skin/manga-v3.css`: SVG animated corners, section headings, workboard/modal and shared row geometry, reduced motion.
- `src/manga-skin/manga-v4.css`: unified sidebar brand and full page header, right inspector spacing/type and manga GSAP.
- `src/manga-skin/manga-v5.css`: Sessions, Cron, workboards, analytics/models, tables and dense controls.
- `src/sakuga/MangaAwareSelect.tsx`: web-only portal select menus. Shared `@qubit/shared` remains unchanged.
- `src/manga-skin/MangaMenuSurface.tsx`: portal menus for Profiles and Models; outside-click remains functional.
- `src/components/ThemeSwitcher.tsx`: bounded popover placement and recalculation after scroll/resize.
- `src/lib/sidebar-sections.ts`: Q6-only simplified seven-group nav; default themes retain original groups.
- `src/lib/stageReveal.ts`: manga-specific ink reveal instead of blurry route fade; reduced-motion preserved.
- `src/themes/presets.ts`: Inter/Rajdhani font pairing for manga; existing theme labels remain unchanged.
- `public/manga-ui/manga-*.svg`: independent crisp vector star and comic panel corners.

## QA status
Source-level review and test cases have been added, but **a complete frontend build and visual-browser audit were not run from this environment**. GitHub Actions status on the base main commit and this branch has been failing in multiple jobs; this is not a green verification signal.

Run locally after pulling:
```bash
cd frontend/qubit-web
npm ci
npm run typecheck
npm run test
npm run build
```
Then start the dashboard using the project's existing scripts and compare Q6/Q6-L to the original provided reference screenshots.

## Browser smoke-test matrix
- [ ] Q6 + Q6-L, then switch to Q1/Q2 to confirm CSS and animations revert.
- [ ] Sidebar expanded/collapsed at desktop and 390 px mobile, check all groups and star readability.
- [ ] Sidebar brand, full page header and page actions; no clipping at 100%, 125%, 150% zoom.
- [ ] Right Qubit Chat model, reasoning, skills, tools and sessions panes; long labels and controls.
- [ ] Dropdowns: Reasoning, Theme, model role, Profile actions, Cron task fields; viewport top/bottom and scroll.
- [ ] Sessions, Profiles, Analytics, Models, Cron, Files, Tasks, Plugins, Skills, MCP, System and Config.
- [ ] Every real popup/modal appears above cards and can close with click outside / Escape.
- [ ] Tooltips, focus rings, form validation, disabled and loading states.
- [ ] `/chat` terminal typography/behavior unchanged.
- [ ] Workbench, HUD, Office/2/3 retain previous visual style.
- [ ] Reduced-motion media preference disables decorative entrance effects.
- [ ] Check browser console warnings and horizontal overflow at widths 390/768/1440 px.

## Merging
Do not represent this implementation as 100% pixel-verified across every registered dashboard route until this QA matrix is completed on a running app.

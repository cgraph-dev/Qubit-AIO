# Optional Manga Skin — Web Dashboard

This change is **opt-in** and affects only the React web dashboard under `frontend/qubit-web`.

## Turn it on
1. Build and launch the web dashboard normally.
2. In the **left sidebar's utility controls**, click **Manga**. It is OFF by default.
3. Click again to return to your original theme. The selection is saved locally in your browser.

No command line or source-code edits are required to toggle the skin after this PR is merged.

## Design
- Black/navy surfaces with hot magenta inked outlines, lavender layers, yellow highlights and halftone textures.
- Applies to the existing navigation/sidebar, telemetry/footer, right chat panels, shared settings/cards, forms and Sakuga shadow parts.
- Uses Qubit's existing route tree and components. Does **not** override event routing, app state, models or backends.
- All label text remains normal DOM text; the SVG decorations contain no UI copy.
- No external font or animation dependencies.

## Intentionally not touched
- Electron desktop `qubit_runtime/apps/desktop`.
- `/qubit-workbench`, `/qubit-hud`, `/qubit-office`, `/qubit-office2`, `/qubit-office3` and descendants.
- The off state preserves all prior styles; the only visible addition is the Manga toggle.

## Integration points
- `src/main.tsx` mounts `MangaSkinGate` inside the existing BrowserRouter and ThemeProvider.
- `src/manga-skin/MangaSkin.tsx` adds the opt-in portal control in `#app-sidebar .qagent-sidebar__utility` and scopes the skin using `html[data-qubit-manga="on"]`.
- `src/manga-skin/manga.css` adds styling with the real `.qagent-sidebar__link`, `.qplate`, `.qchat`, `.settings-*`, and `sakuga-*` CSS interfaces.
- `public/manga-ui/` contains independent scalable vector ornaments.

## Verification and remaining work
Static repository inspection confirmed the component and selector names. This branch has **not been typechecked, built or exercised in a running web browser** in the current environment. Before merging, run:

```bash
cd frontend/qubit-web
npm ci
npm run typecheck
npm run test
npm run build
```

Manual checks: enable/disable skin on Sessions, Chat, Qubit Chat, Profiles, Models, Analytics, Skills, Plugins, Voice Editing and responsive navigation; confirm excluded routes retain original styling.

This is a **first working skin implementation**, not a claim of 100% pixel-perfect coverage of all dashboard routes.

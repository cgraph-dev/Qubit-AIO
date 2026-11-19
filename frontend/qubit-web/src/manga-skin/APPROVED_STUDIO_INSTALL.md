# Qubit Manga Studio — Approved 14-piece visual package

This replaces the older stacked prototype styling **only when the real artwork pack has been installed**. The source checkout remains safe without it: the previous Manga skin stays visible rather than showing broken CSS images.

## Installation

Download `qubit_manga_approved_production_assets.zip` from the ChatGPT artifact included with this delivery (or copy it to the repository root). From the Qubit-AIO repository root run:

```bash
node frontend/qubit-web/scripts/install-manga-approved.mjs /path/to/qubit_manga_approved_production_assets.zip
cd frontend/qubit-web
npm run build
```

The Python 3 standard library is used only to unpack/verify the ZIP. SHA-256 checksums are validated **before** installation. Installation copies the separately named image files and `MANIFEST.json` under `frontend/qubit-web/public/manga-ui/approved/`.

The artwork is not installed by the *original* ZIP installer; they are **different packages**. The original archive still installs separately at `/manga-ui/zip/`.

Restart your webdashboard service on port 8890 using its existing process manager and hard-refresh the browser. The opt-in Manga theme must be enabled.

## Asset manifest

| Asset | Purpose |
| --- | --- |
| `01_sidebar_header.webp` | Reference composition of brand header |
| `02_system_panel_frame.webp` | Nine-slice gateway status frame |
| `03_system_titlebar.webp` | SYSTEM panel title artwork |
| `04_tooltip_plate.webp` | Right-side rail tooltip chrome |
| `05_section_titlebar.webp` | Original assembled section-bar reference |
| `05_section_titlebar_starless.webp` | Starless standalone section-bar reference |
| `05_section_titlebar_clean.webp` | Production section-bar base, without baked star/bars |
| `06_primary_button.webp` | Restart button skin |
| `07_disabled_button.webp` | Disabled update-button skin |
| `08_checkbox_idle.webp` | Unticked native-checkbox skin |
| `09_checkbox_active.webp` | Ticked native-checkbox skin |
| `10_impact_strokes.webp` | Small decorative accent in frame gutter |
| `11_atom_emblem.webp` | Replaceable Qubit golden atom |
| `12_anime_eye_panel.webp` | Independent brand eye panel |
| `13_star_only.webp` | **Independently replaceable star**. Can be swapped without changing the plate. |
| `14_three_gold_bars.webp` | Independent beveled gold-bar sprite |

The pack also retains `05_section_titlebar_starless.png` for manual art revision.

## Architecture and behavior

- All gateway state/active session count/latency labels and values are live React data from `SidebarStatusStrip.tsx`, not artwork.
- All action-button handlers remain on the original `SystemActionButton` component.
- The CSS is loaded *after* the previous Manga stylesheet layers, avoiding another mixed stack of decorative panels.
- The runtime gate checks the manifest and decodes critical images before setting `<html data-qm-approved-assets="ready">`.
- If installation is missing/incomplete the last known working skin remains active.
- Qubit Office and Workbench are excluded at the existing route gate; the `/chat` terminal surface is not styled.

## Studio-grade animation without unnecessary GPU load

Three.js is **not** used for the 2D chrome parts because that would create unnecessary contexts, GPU memory use, and rasterization artifacts for very small UI elements. The approved finish uses a limited number of slow CSS-composited reflection sweeps (`transform: translate3d` + `opacity`), with mild small-image brightness pulses (atom, star and bars). Large panels do not animate `backdrop-filter`, `box-shadow`, or `background-position` every frame. All motion pauses when the tab is hidden and respects `prefers-reduced-motion`.

If adding a full-screen interactive background in a later pass, use a single Three.js canvas behind the UI, not one canvas per button/plate: 30fps cap; DPR ceiling 1.25; stop in hidden tabs; render on demand; reduced-motion fallback. Do not apply WebGL to terminal canvases.

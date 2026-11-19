# Qubit Manga Web Dashboard — Repaired Production Artwork

The dashboard React/CSS uses the corrected 14-piece Manga asset pack. Version v2 repairs cutout halos, restores the sidebar's aspect ratios and removes duplicate menu-row overlays.
The design only applies when the selected theme is Qubit Manga on eligible web
routes. The Workbench, Office and chat terminal renderer remain unchanged.

## Install exact approved art

Download **qubit_manga_POLISHED_v3_assets.zip** from the design conversation
and save it in the **repository root** (the folder containing `frontend/`).

From the repository root:

```sh
git pull origin main
node frontend/qubit-web/scripts/install-manga-approved.mjs qubit_manga_POLISHED_v3_assets.zip
```

Alternatively, copying the ZIP to that location before `npm run dev` or
`npm run build` will trigger the same checksum-verified installer automatically.
The files are placed individually in `frontend/qubit-web/public/manga-ui/approved/`.

The installer uses only Node built-ins, verifies every SHA-256 and WebP RIFF
length, and refuses a package missing any of the 14 components. If the same
pack has already been installed, it will **not overwrite customized assets**.

## Replace the gold star without touching the title bar

Replace only:

`frontend/qubit-web/public/manga-ui/approved/13_star_only.webp`

The complete chrome/magenta plate **without any baked-in star** lives at:

`frontend/qubit-web/public/manga-ui/approved/05_section_titlebar_starless.webp`

Changing the star does not require editing JSX, CSS, section text or gold bars.
The title number and label are live localized React text.

The v2 ZIP also contains 14 full-resolution transparent PNG reference files and the editable independent 13_star_only.svg. The installer uses the 14 lossless WebP sprites only.\n\nOther independent pieces include the eye header, atom, SYSTEM frame/title,
tooltip, primary/disabled action frames, checkboxes and impact streaks.
Their content and click handlers remain the existing React elements.

## Troubleshooting

If the approved art does not appear, check that
`frontend/qubit-web/public/manga-ui/approved/MANIFEST.json` exists, restart the
dev server and reload the page. The Manga skin will only set
`data-qm-approved-assets="ready"` after the manifest and core images load.

The repaired title bar is 2104px wide, intended to display at an approximately 5.45:1 aspect ratio; do not stretch it to an arbitrary height. The sidebar also uses one rectangular navigation row each, not nested octagons.\n\nArtwork is static in this pass. Decorative glow/reflection loops have been
disabled; only ordinary existing interactions remain.

## Verification

```sh
cd frontend/qubit-web
npm run typecheck
npm test
```

## Polished v3 refinements

The tooltip plate now includes the polished circled exclamation badge. The
SYSTEM title bar is rendered from a clean independent high-resolution SVG and
all 14 image assets use softened magenta/chrome highlights (~35% reduced glow).
Sidebar navigation rows use one straight outer outline with white-square icons
and a halftone right gutter; no duplicate nested octagon is shown. Artwork is
static until the optional animation pass. Replace 13_star_only.webp on its own
to change the star without affecting the starless titleplate.

## Sidebar reconstruction (single source of truth)

The latest Web Dashboard repair replaces the prior stacked sidebar-specific
override rules with one authoritative stylesheet:

`src/manga-skin/manga-production-layout-repair.css`

The fixed-mobile/sticky-desktop sidebar mounts one passive
`qm-manga-outer-frame` ornament in `src/App.tsx`. It is backed by
`public/manga-ui/manga-sidebar-outer-frame.svg`, a nine-sliced chrome rail
that does not stretch the four corners as the sidebar gets taller.

Two additional precision SVGs ship directly with the repository:

- `public/manga-ui/manga-tooltip-precision.svg` — mathematically circular
  exclamation badge and complete text-free frame, displayed at its true aspect.
- `public/manga-ui/manga-system-title-precision.svg` — a sharp, text-free
  lavender heading. `SYSTEM` remains real React text and is not rasterized.

The title bar and gold star remain independent files in the installed v3 pack.
The old nested menu octagon and duplicate sidebar backgrounds are disabled.
Only the navigation list scrolls; the live gateway, action buttons and footer
remain visible. All decorative looping animations are off.

**This source change alone is not a screenshot verification of localhost.**
After `git pull origin main`, inspect the visible sidebar and take a screenshot
at both expanded and collapsed widths, including the SYSTEM panel and a tooltip.
A V3 art pack previously installed is sufficient; there is no new ZIP
requirement for these three new SVGs.

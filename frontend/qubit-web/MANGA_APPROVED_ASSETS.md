# Qubit Manga Web Dashboard — Approved Studio Artwork

The dashboard React/CSS uses the user's approved 14-piece studio Manga asset pack.
The design only applies when the selected theme is Qubit Manga on eligible web
routes. The Workbench, Office and chat terminal renderer remain unchanged.

## Install exact approved art

Download **qubit_manga_FINAL_approved_assets.zip** from the design conversation
and save it in the **repository root** (the folder containing `frontend/`).

From the repository root:

```sh
git pull origin main
node frontend/qubit-web/scripts/install-manga-approved.mjs qubit_manga_FINAL_approved_assets.zip
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

Other independent pieces include the eye header, atom, SYSTEM frame/title,
tooltip, primary/disabled action frames, checkboxes and impact streaks.
Their content and click handlers remain the existing React elements.

## Troubleshooting

If the approved art does not appear, check that
`frontend/qubit-web/public/manga-ui/approved/MANIFEST.json` exists, restart the
dev server and reload the page. The Manga skin will only set
`data-qm-approved-assets="ready"` after the manifest and core images load.

Artwork is static in this pass. Decorative glow/reflection loops have been
disabled; only ordinary existing interactions remain.

## Verification

```sh
cd frontend/qubit-web
npm run typecheck
npm test
```

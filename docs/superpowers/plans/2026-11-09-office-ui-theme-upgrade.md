# Qubit Office UI Theme Upgrade — Implementation Plan (v4, internet-verified)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Qubit Office look better with the owner-purchased LimeZu art, and make every future UI change easy to add and share — **without touching Habblaud**.

**Architecture:** A theme is a declarative, data-only folder: `theme.json` + one portrait **atlas** + a JSON index + role tokens (+ optional 9-slice descriptors). Qubit’s own shell consumes it (host docks, injected menu icons), and the **server** injects one generated `<style>` into the office app document that overrides a documented allowlist of Habblaud’s own `.ui-root` variables. No client-side CSS injection, no pack CSS, no Habblaud edits. `office.theme` defaults to `classic`.

**Tech Stack:** Python 3.14 + Pillow (build/validate/serve), TypeScript/React 19 (host-provided React), Vitest (jsdom), pytest, Playwright (Python), the existing `/plugins/` static route and `_serve_app_html` document injector.

**Spec:** `docs/DECISIONS.md` rulings 14 and 15; `THIRD_PARTY_NOTICES.md` §"LimeZu — purchased office art"; the 2026-11-09 asset audit of the owner's local download (not in the repo).

---

## Owner constraint (amended 2026-11-09)

> “dont fuck habblaud we just update ui to make it better and easier to upgrade in the future”
>
> “you can touch habblaud, i meant not break whats currently working cos we need add more things”

**Amendment (ruling 16):** Part A stays a shell-only, read-only-Habblaud lane and is
complete. **Part B may modify/re-vendor Habblaud**, provided it is additive and
never regresses current behavior: every change sits behind `office.theme`
(`classic` = today), keeps the stream/panel/menu/283-seat contracts, updates
`PROVENANCE.json`, and has before/after goldens + rollback. Paid art stays out of
the repo; the patched client loads the owner-built local pack at runtime.

- `frontend/qubit-app/plugins/qubit-office/habblaud/` is **read-only**: no edits, no rebuild, no re-vendor for UI.
- The only runtime interaction is a **server-injected `<style>`** (document head, after the app’s own stylesheet) that sets an allowlisted subset of `.ui-root` variables, plus class hooks/DOM on **elements we already inject** (the two menu buttons).
- The game stays fully interactive and unchanged in behavior; the theme is off by default and reversible by config.

## Non-goals

- No changes to the office engine, simulation, room layout, canvas art, stream, tools, writes, or approvals.
- **Do not repurpose Habblaud’s own `theme` field** (`none|halloween|christmas`, upstream-owned seasonal art — verified in `docs/world/privacidade.md` and the world payload). Room/interior art is now **in scope under Part B** (ruling 16), added behind the theme flag; the developer's screenshots confirm top-down orthogonal rooms (RPG Maker), which the patched renderer will consume additively.
- No external network calls. One local theme read only when a theme is active.
- No new core tool/plugin; one config key (`office.theme`).
- No pack-supplied CSS/JS/HTML/SVG/remote URLs.

---

## Internet-verified findings that changed v4

| # | Finding | Source | Fix |
|---|---|---|---|
| 1 | **CSS custom properties can trigger network requests** (`--x: url(...)` + `var(--x)`), enabling exfiltration | `https://portswigger.net/research/blind-css-exfiltration` | Token values are type-checked (color/length only) and reject `url(`, `image-set(`, `src(`, `var(`, `attr(`, `@`, `\`, `;`, `}`; the page CSP already has `img-src 'self' data: blob:`. |
| 2 | **Unlayered CSS beats `@layer`**, and `!important` is the wrong tool; use specificity + source order | `https://developer.mozilla.org/en-US/docs/Web/CSS/Guides/Cascade/Specificity`, `https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/At-rules/@layer` | Inject the style **server-side after the app stylesheet**, selector `html body .ui-root` (higher specificity), no `@layer`, no `!important`. |
| 3 | **Server-side injection beats client-side iframe styling**: no race, applies before first paint, fail-closed | `https://github.com/Netflix/metaflow-ui/pull/204`, `https://docs.chastify.net/developer-api/theming/` | Inject from `_serve_app_html` (office app only) from validated tokens; drop the client `injectIframeTheme`. |
| 4 | **50+ images ⇒ sprite atlas**; lazy `<img>` still lets long/fast scroll flood the request pool | `https://sosquishy.io/articles/sprite-sheets-vs-individual-images`, `https://harnery.com/decisions/0118-load-codec-roster-portraits-by-pack/` | One 32 px portrait **atlas** + JSON index, rendered as CSS sprites (1 request), emoji fallback underneath. |
| 5 | **Licence verified from the artist**: commercial use + credit; resale/redistribution prohibited; CC-BY needs TASL + changes | `https://limezu.itch.io/moderninteriors`, `https://itch.io/post/2185336`, `https://creativecommons.org/licenses/by/4.0/deed.en` | `LICENSE-ASSETS` + TASL + credit; data-only packs. |
| 6 | **Declarative, fail-closed theme formats** (sha+bytes, path safety, atomic install, no CSS/JS) | `https://deepwiki.com/fagemx/slot-asset-pipeline/6.1-atomic-theme-v1-package-format`, `https://denframe.com/docs/format` | Contract v1 + bounded atomic install. |
| 7 | **CSP allows inline styles here** (`style-src 'self' 'unsafe-inline'`), verified against the live office document; `X-Frame-Options: SAMEORIGIN` | live `curl -I /plugin-apps/qubit-office/habblaud/` | Server injection is viable; add a test pinning the header contract. |
| 8 | **Visual regression discipline**: element shots, animations/caret off, fixed clock, data stubbed, baselines reviewed | `https://playwright.dev/docs/test-snapshots`, `https://diffy.website/blog/playwright-visual-regression-testing/` | Opt-in Playwright goldens at DPR 1/2. |
| 9 | **Pixel art**: `image-rendering: pixelated`, integer scaling, DPR caveats | `https://developer.mozilla.org/en-US/docs/Games/Techniques/Crisp_pixel_art_look` | Atlas tiles are natively 32 px, displayed at exactly 32 px. |
| 10 | **9-slice** `border-image` guidance | `https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Properties/border-image-slice` | Optional, validated slices; tokens-only in v1. |
| 11 | **Archive extraction is an attack surface** (zip slip, symlinks, zip bombs, Unicode/case duplicates, device names, ADS) | `https://best.openssf.org/Secure-Coding-Guide-for-Python/04_neutralization/pyscg-0012/`, `https://nocomplexity.github.io/fileaudit/zip_validations.html`, `https://github.com/umar0x/decompress` | Install validates NFC-normalised paths, case-insensitive duplicates, depth/size/ratio caps, no symlinks/encryption/unsupported compression, temp dir → readback → atomic rename. |
| 12 | **CSS background sprites need an accessible name**; background images are invisible to AT and can fail to load | `https://www.w3.org/WAI/GL/wiki/ARIATechnique_usingImgRole_with_aria-label_forCSS-backgroundImage`, `https://developer.mozilla.org/en-US/docs/Web/Accessibility/ARIA/Reference/Roles/img_role`, `https://www.bbc.co.uk/accessibility/forproducts/guides/mobile/background-images` | Sprite gets `role="img"` + `aria-label`; the emoji stays as a visible fallback when CSS fails and is `aria-hidden` only when the sheet is ready. |
| 13 | **Contrast thresholds are per element class**, not one pair; game UI needs 4.5 text / 3 non-text / 3 disabled, and colour alone is not enough | `https://devdocs.xbox.com/build/game-principles/accessibility/xag-deep-dives/xag-102-contrast`, `https://developer.mozilla.org/en-US/docs/Web/Accessibility/Guides/Understanding_WCAG/Perceivable/Color_contrast` | Contrast matrix over text, muted, accent, border, status and level colours; no colour-only state (the office already pairs colour+shape). |
| 14 | **Visual checks inside iframes are finicky**: element shots only, stable dimensions, animations disabled *inside* the frame, no full-page host shots | `https://playwright.dev/docs/api/class-framelocator`, `https://github.com/microsoft/playwright/issues/37367` | Frame-scoped locators, fixed viewport + `--force-device-scale-factor=1`, animations/caret off, no full-page iframe shot. |
| 15 | **An allowlist of third-party variables can drift** (Netflix rejected an allowlist for that reason) | `https://github.com/Netflix/metaflow-ui/pull/204` | Deliberate, versioned allowlist + a **drift report** that lists `.ui-root` variables in the vendored CSS not yet themed; it warns, never fails the build. |
| 16 | **Local paths must not ship** (repo clean-tree rule; a committed `theme.json` could carry `/home/<user>/…`) | repo `AGENTS.md` clean-tree/provenance rules | `source` is a sanitised description, never a filesystem path; a guard scans committed theme JSON for `/home/`, `~/`, `C:\Users`, `\\`. |
| 17 | **`?theme=` must reach the framed document too**, or host and iframe disagree | local design review | The override is propagated on the iframe URL and read by `_serve_app_html`; both sides use the same validated payload. |
| 18 | **Embeddable-widget threads converge on “let the host pass style options as JSON”, not raw CSS** (r/webdev) | `https://www.reddit.com/r/webdev/comments/qi4ltd/iframe_accessing_parent_windows_css_resources/` | Packs carry **tokens**; Qubit maps them to its own variable names. A pack never names Habblaud internals, so upstream renames don’t leak into packs. |
| 19 | **LimeZu is CC-BY (not CC-BY-SA); no source-sharing obligation; credit must be *somewhere persistent* (credits menu / end of game), not a tooltip** (artist comments) | `https://itch.io/post/2185336`, `https://limezu.itch.io/moderninteriors/comments`, `https://github.com/chaitanyagiri/munder-difflin/blob/main/src/renderer/src/assets/ATTRIBUTION.md` | A persistent **Credits/About** surface (office footer or Settings) always names LimeZu; the theme-active banner is additional, never the only credit. |
| 20 | **Design-token systems fail when base tokens are reused everywhere instead of semantic/component aliases; hover/disabled states must be explicit tokens** (HN/SO design-system postmortems) | `https://news.ycombinator.com/item?id=26291215`, `https://stackoverflow.blog/2020/03/31/building-dark-mode-on-stack-overflow/` | Tokens are semantic; `--ui-bg-hover` and muted/disabled pairs are explicit and contrast-checked; no runtime color math (`darken()` does not work on CSS vars). |
| 21 | **Pixel fonts do not scale; non-integer browser zoom distorts pixel art** (r/gamedev, r/aigamedev) | `https://www.reddit.com/r/gamedev/comments/dxe5mz/`, `https://www.reddit.com/r/aigamedev/comments/1qbm96r/` | Portrait atlas is native 32 px at 32 CSS px; goldens at DPR 1/2; document that fractional browser zoom is out of our control. |
| 22 | **A third-party tool must not become integral to theming** (Figma/Themer postmortem) | `https://blog.bakarema.com/2022/10/05/my-five-biggest-design-system-mistakes-by-steve-dennis-sep-2022-2/` | No design tool/service at runtime; Pillow is a build-time library; tokens are plain JSON in the repo. |
| 23 | **The two purchased generators share one part set** (verified: 9 skins, 7 eyes, 200 hairstyles, 84–85 accessories in both), so the same part indices produce a matching body and portrait | local inspection of the two archives, 2026-11-09 | The pack stores one `agents.json` recipe per slug (skin/eyes/hair/outfit/accessory); the builder renders **both** a portrait and a body from it. |
| 24 | **DiceBear derives every choice from `hash(seed + ":" + component)`**, so adding a new component later never reshuffles existing avatars; styles are declarative JSON | `https://www.dicebear.com/understand/how-avatars-are-made/`, `https://www.dicebear.com/create-styles/implement-dicebear-core/` | Per-part key-based derivation (`sha256(slug + ":hair")`), and the chosen recipe is stored in `agents.json` so it is frozen even if the part list grows. |
| 25 | **LPC generator keeps explicit z-order per category in JSON** and ships shareable, versioned presets | `https://github.com/sanderfrenken/lpc-spritesheet-generator-v2`, `https://github.com/LiberatedPixelCup/Universal-LPC-Spritesheet-Character-Generator` | `agent_sheets.json` records the layer order + z-index and the confirmed frame; recipes are shareable/versioned. |
| 26 | **Agent-avatar guidance**: fallback hierarchy (photo → generated → initials), deterministic seed = cacheability, version the template so a restyle regenerates | `https://github.com/agentsorg/design-engineering/blob/main/skills/design-engineering/references/components/avatar-systems.md`, `https://docs.ability.ai/guides/agent-avatars`, `https://next.jqueryscript.net/next-js/avatar-facehash/` | Panel fallback chain: generated portrait → initials chip → emoji; pack carries `template_version`, atlas URLs use `?v=<template_version>`. |
| 27 | **WebP/AVIF + lazy loading + reserved space** are baseline UI-quality rules; the static server already serves `.webp` | `skills/ui-ux-pro-max` (local skill), `voicebridge/webui.py` MIME table | Optional lossless WebP atlas; fixed 32×32/32×64 boxes (no CLS); one atlas request. |

---

## Global constraints

- **`classic` default.** `office.theme` unset ⇒ today’s look. `QUBIT_OFFICE_THEME` = process override; `?theme=` = one-page read-only override.
- **Habblaud read-only.** `python3 scripts/vendor_habblaud.py --check` must stay green; no file under `habblaud/` changes.
- **Packs are data.** `.png`, `.jpg`, `.jpeg`, `.webp`, `.json`, `.txt` only; no CSS/JS/HTML/symlinks/remote URLs.
- **Credits required.** Pack carries `credit` + `license`; a **persistent Credits/About surface** (office footer or Settings) always names LimeZu, and the active-theme line is additional — never the only credit. Serene Village (CC-BY 4.0) additionally carries TASL + changes.
- **No raw archives in the tree.** Derived files only, each sha256 + byte length.
- **Owner decision O1/C4:** no celebrity/meme sprite families.
- **No new polling/remounts.** Docks keep stable identity (`c5bd8a69b`).
- **No operator paths in shipped files.** Theme manifests use a sanitised `source` description; `/home/…`, `~/`, `C:\Users\…` and UNC paths are refused and scanned for.
- **Archive installs are hostile input.** NFC/case-insensitive duplicate checks, no symlinks/encrypted entries/unsupported compression, depth/size/ratio caps, atomic publish.
- **Pixel zoom caveat:** native 32 px at 32 CSS px with `image-rendering: pixelated`; goldens at DPR 1 and 2. Fractional browser zoom is a known browser limitation and is documented, not “fixed” with non-integer scaling.
- **No source-shape tests.** Behaviour and artifacts only.
- **No service restart during implementation;** focused tests; `VERIFIED` not claimed.

---

## Theme contract v1 (`qubit-office-theme/1`)

```json
{
  "format": "qubit-office-theme",
  "schema": 1,
  "id": "limezu",
  "label": "LimeZu Modern",
  "version": "1.0.0",
  "license": "LimeZu … redistribution prohibited; credit required",
  "credit": "LimeZu — https://limezu.itch.io",
  "source": "owner purchase of LimeZu Modern Interiors + Modern User Interface, 2026-11-09",
  "template_version": 1,
  "tokens": {
    "color-surface": "#0d0d1a",
    "color-surface-raised": "#161a23",
    "color-surface-hover": "#1d2230",
    "color-text-primary": "#e9edf7",
    "color-text-muted": "#8a93a8",
    "color-border": "#334155",
    "color-accent": "#7cc8ff",
    "radius-control": "4px"
  },
  "portraits": {
    "atlas": "portraits/atlas32.png",
    "index": "portraits/index.json",
    "size": 32, "cols": 20, "rows": 15
  },
  "bodies": {
    "atlas": "bodies/atlas32.png",
    "index": "bodies/index.json",
    "size": 32, "frame": { "w": 32, "h": 64 },
    "cols": 20, "rows": 15
  },
  "slices": {},
  "files": {
    "portraits/atlas32.png": { "sha256": "<64 hex>", "bytes": 48123 },
    "portraits/index.json":  { "sha256": "<64 hex>", "bytes": 9123 },
    "bodies/atlas32.png":    { "sha256": "<64 hex>", "bytes": 96000 },
    "bodies/index.json":     { "sha256": "<64 hex>", "bytes": 9123 },
    "agents.json":           { "sha256": "<64 hex>", "bytes": 21000 }
  },
  "limits": { "max_total_bytes": 25000000, "max_files": 20 }
}
```

`index.json`: `{"schema":1,"size":32,"cols":20,"cells":{"ceo":[0,0],"lead-quality":[1,0]}}`.
`agents.json`: `{"schema":1,"templateVersion":1,"recipes":{"ceo":{"skin":2,"eyes":1,"hair":1,"outfit":1,"accessory":1}}}` — the same indices drive the portrait and the body. `agent_sheets.json` (build-side) records the layer z-order and the calibrated frame.

Validation:
- exact format/schema; id `^[a-z0-9][a-z0-9-]{0,39}$`; non-empty credit/license; `source` is a description, not a path (no `/home/`, `~/`, drive/UNC).
- files: relative, `[A-Za-z0-9._/-]`, no `.`/`..`, no Windows reserved basename, allowlisted suffix, no symlink, exists, `bytes`/`sha256` match; case-insensitive duplicates refused; totals within limits.
- tokens: key `^[a-z][a-z0-9-]{1,40}$`; **typed values only** — colors `^#[0-9a-fA-F]{3}([0-9a-fA-F]{3})?$`, lengths `^(0|[1-9][0-9]{0,3})(px|rem|em)$`; reject `url(`, `image-set(`, `src(`, `var(`, `attr(`, `@`, `\`, `;`, `}` anywhere.
- portraits/bodies: atlas/index declared; size ∈ {16,32,48}; frame `{w,h}` positive; every slug cell inside `cols×rows`; atlas pixel size == `cols×rows×size` (bodies: `cols×frame_w` by `rows×frame_h`); indices may not contain local paths or scripts; `agents.json` recipes only reference indices that exist in the part sets; `template_version` is a positive integer and every atlas URL carries `?v=<template_version>`.
- slices (optional): 4 positive ints, `width` px, `repeat ∈ stretch|round|repeat`, `src` declared.
- contrast: `color-text-primary` vs `color-surface` ≥ 4.5; `color-text-muted` vs `color-surface` ≥ 3.0; `color-text-primary` vs `color-surface-raised` ≥ 4.5; `color-text-muted` vs `color-surface-hover` ≥ 3.0 (disabled reads at 3.0). Status/level colours (`--st-*`, `--lv-*`) must reach 3.0 against `color-surface`.

Token names are **semantic** (`color-surface`, `color-text-primary`); the pack never names a Habblaud variable. Qubit owns the mapping table below, so an upstream variable rename is a one-line change here, not a pack migration.

**Allowlisted Habblaud variables** (the only ones a theme may set):
`--ui-bg`, `--ui-bg-soft`, `--ui-bg-solid`, `--ui-bg-hover`, `--ui-line`, `--ui-line-strong`, `--ui-text`, `--ui-muted`, `--ui-faint`, `--ui-accent`, `--ui-accent-soft`, `--radius`, `--radius-sm`, `--st-working`, `--st-waiting`, `--st-done`, `--st-idle`, `--st-offline`, `--st-shell`, `--lv-ok`, `--lv-warn`, `--lv-crit`.
Not themed: `--side-w`, `--drawer-w`, `--top-h`, `--gap`, `--free-*`, `--font-*`, `--ease`.

---

## Calibration result (2026-11-09, measured on the purchased sheets)

Confirmed by rendering labelled frame grids and compositing real recipes (preview:
`/tmp/final_candidates.png`, not committed — derived art stays out of the tree until the pack is approved):

| Layer | Frame (32 tier) | Notes |
|---|---|---|
| Body | row 0, col 0 (`32×64`) | front idle, 4 frames on row 0 |
| Eyes | row 0, col 0 | tiny overlay; front |
| Hair (body) | **row 3, col 0** | front idle; rows 0–2/4–5 are back/side, rows 6–7 are walk-front |
| Outfit bottom | row 0, col 0 | every outfit file's row 0 is the bottom garment |
| Outfit base top | row 7, col 0 | shared dark undershirt across files |
| Outfit top | **row 8, col 0** | the outfit's own colour; matches the bottom's colour (red/green/ivory/orange/teal/blue…). Some files have row 7 or 8 empty (31, 33) — the builder skips empty rows. |
| Outfit shoes | row 14, col 0 | optional; some outfits bake shoes into the top |
| Portrait | 64 px cell, row 0, col 0 → downscale to 32 | 10×3 grid of 64 px cells at the 32 tier |
| Accessory | **not calibrated** | omit in v1; backpacks/hats need a per-family frame map |

Outfit variety is now **solved**: composing bottom row 0 + base row 7 + top row 8 +
shoes row 14 yields visibly different outfits (preview: `/tmp/outfit_variety.png`:
red, green, ivory, orange, teal, blue). The earlier “all look the same” result was
row 7 alone (the shared undershirt); row 8 is the outfit's own colour. The part indices
(skin/eyes/hair) are shared with the portrait, which is what makes face and body match.

## Art handling — do not redistribute the paid art

Finding (2026-11-09, verified in the upstream macOS fork's README and the LimeZu comments):
the Modern Interiors/UI spritesheets are **paid art that must not be redistributed** — “Never
redistribute. Every user must buy their own copy.” Use in the game is allowed; shipping the
sheets (or a derived atlas that reproduces them) in the public source tree is the redistribution
the licence forbids. Reference: `https://github.com/stas-bool/Character-Generator-2.0` (README
licence table), `https://limezu.itch.io/moderninteriors`.

Therefore:
- **The repo ships recipes, not art.** Committed: `theme.recipe.json` (tokens + per-agent part
  indices + `template_version` + credit/licence), the builder/extractor/calibration scripts, the
  docs. **Not committed**: the `.zip` packs, the extracted parts, the portrait/body atlases.
- **The owner builds locally** from their purchase: `scripts/office_theme_build.py --source
  "/path/to/UI Office" --out "$QUBIT_HOME/office/themes/limezu"`. The output directory is
  gitignored and served locally.
- **Sharing a theme = sharing the recipe**, not the art. Another person runs the builder against
  their own purchase. `qubit office theme export` exports the recipe bundle (tokens + recipes +
  licence), never the PNGs.
- The CC-BY pack (Serene Village) may still ship with attribution, but it is not used in this
  upgrade.
- The two generator apps (Character Generator GPLv3, Portrait Generator proprietary) are tools
  only — never shipped or modified.

This supersedes the earlier “commit the generated pack” approach.

## UI assets the owner supplied

| Source | What it is | How it is used |
|---|---|---|
| `modernuserinterface-win.zip` (LimeZu Modern UI) | 16/32/48 px UI sheets (`Modern_UI_Style_1`, `Style_2`, `Gamepad`), animated buttons, and the 903 portrait pieces already in use | **Token palette** sampled from the sheets + **optional 9-slice** frames for our docks/buttons; portrait pieces feed the portrait atlas |
| `hud-agents-icons.jpeg` | The two menu glyphs | Already traced into `ui-px-icon` paths (HUD/Agents) |
| `/home/trick/Desktop/ui elements` | WhatsApp-saved **reference** images from an “AI design studio” (watermarked concept art, e.g. crimson/ivory gothic UI) | **Reference only — never shipped, never used as a texture.** Optional: derive a second token preset (`crimson`) from its palette if the owner wants that direction |
| `Character Generator 2.0` / `Portrait Generator 1.5.0` | Paid art + third-party tools | Art sources for the atlases (built locally); tools never shipped |

Tasks add: `--ui-contact-sheet` (labelled grid of the Modern UI sheets, for authoring
`theme.recipe.json:slices`) and a palette sampler that writes the token values; the reference
moodboard is not ingested.

## Developer usage screenshots (owner-supplied, 2026-11-09)

The pack author's own screenshots show the intended composition. What they prove, and what we adopt:

| Pattern in the screenshots | Meaning for us |
|---|---|
| **Dialogue boxes**: a framed portrait + a name tab + text (Sarah/Morgan/Chris); the author says “Portrait Generator + the versatility of the frames” makes dialogue boxes | Our **agent card** should follow this: portrait inside a UI frame, name on the tab, body sprite to the side. Not a bare avatar row. |
| **Character panel**: portrait + full-body character + stats in one framed panel | Confirms the portrait/body pairing from the two generators; our recipe (shared indices) is the right model. |
| **Frames, tabs, icon grids, controller prompts, market panels** | The Modern UI sheets supply 9-slice frames, tabs, buttons and small icons. Our **docks/agent card/menu controls** use these (validated slices); the two menu glyphs stay the owner-traced icons. |
| **Interiors**: top-down orthogonal rooms (walls, doors, windows, tile grid), shops/hospital/office floors | The interior pack is **RPG-Maker top-down**, not isometric. Habblaud is procedural 3/4 and has no image slots, so these cannot be dropped in without changing Habblaud. **Still deferred** — the screenshots are the reference for a future Qubit-owned room renderer, not a now-change. |

Consequences baked in: Task 5 builds the dialogue-style agent card (portrait + name +
body), Task 7 themes it with the UI frame/tab/button slices, and the room-art deferral
is now backed by evidence rather than preference.

## Skills we will use (and the one we won't invent)

Local skills that apply:
- `skills/ui-ux-pro-max` — contrast, alt text, focus rings, WebP/lazy loading, semantic tokens.
- `skills/anthropic-frontend-design` / `skills/bencium-controlled-ux-designer` — visual polish of the host docks/menu.
- `skills/vercel-react-best-practices` — stable component identity, no inline component factories (the refresh-loop class).
- `qubit_runtime/optional-skills/creative/pixel-art` — only for a promo/OG image or palette study; it does **not** compose layered sprite parts.
- Superpowers: `test-driven-development`, `verification-before-completion`, `executing-plans` / `subagent-driven-development`.

There is **no skill for layered sprite composition / asset-pack provenance**. We will not invent one up front; if the pipeline proves reusable after Part A ships, author a `qubit-office-theme` skill with `writing-skills` (the plan's commands become its steps).

## File structure (nothing under `habblaud/` changes)

| File | Responsibility |
|---|---|
| `voicebridge/office/theme.py` | Roots, contract, active theme, payload, token CSS, contrast, cache, per-token value validation |
| `voicebridge/office/agent_panel.py` | Optional `portrait` field (atlas cell) via injected resolver |
| `voicebridge/webui.py` | `_serve_app_html`: inject the generated office theme `<style>`; read authority for `/api/office/theme` |
| `voicebridge/qubit_compat.py` | `GET /api/office/theme` (+ `?theme=`), panel call |
| `scripts/office_theme_build.py` | Portraits + bodies + `agents.json` + indexes + manifest + QA (deterministic) |
| `scripts/office_theme_extract.py` | Read the Portrait + Character Generator archives in memory → portrait/body layers, part lists, labelled calibration grids + `--check` |
| `scripts/office_agent_calibrate.py` | Emit labelled frame grids for the body sheets and validate `agent_sheets.json` (front idle frame, layer order) |
| `scripts/office_theme_audit.py` | Drift report: `.ui-root` variables in the vendored CSS vs `ALLOWED_THEME_VARS` (warning, exit 0) |
| `frontend/qubit-web/public/plugins/qubit-office/themes/limezu/…` | Generated, committed pack |
| `frontend/qubit-app/plugins/qubit-office/game/app/office-theme.ts` | Fetch theme, host tokens/classes, portrait readiness |
| `AgentsPanel.tsx` / `agents-panel.ts` | Atlas sprite rows + emoji fallback |
| `OfficeAppShell.tsx` / `office-menu.ts` | Host theme + menu injection with a fallback if the group is missing |
| `voicebridge/office/theme_cli.py` + `cli.py` | `qubit office theme list/validate/install/export` |
| `docs/OFFICE-UI-THEMES.md`, `LICENSE-ASSETS`, `docs/ATTRIBUTION-OFFICE.md` | Contract, creator guide, licence/credit |
| tests | `tests/test_office_theme*.py`, `tests/test_office_visual_regression.py`, `tests/test_office_framing_policy.py` (CSP/iframe contract) |

---

# Tasks

### Task 1: Contract + loader + validator + token CSS

**Files:** create `voicebridge/office/theme.py`; test `tests/test_office_theme.py`.

- [ ] **Step 1: failing tests** — valid pack loads; missing credit/license refused; traversal/reserved/suffix refused; case-collision refused; hash/size mismatch refused; unknown theme → `classic` + reason; env and `?theme=` overrides; token CSS emits only allowlisted variables; **dangerous token values refused** (`url(https://x)`, `image-set(...)`, `var(--x)`, `attr(x)`, `@import`, `}`); low contrast reported; index cells out of bounds refused.

```python
@pytest.mark.parametrize("value", [
    "url(https://evil.test/x)", "image-set('x')", "var(--ui-text)",
    "attr(data-x)", "red; } html { display:none", "\\75 rl(",
])
def test_dangerous_token_values_are_refused(value):
    assert T.validate_tokens({"color-accent": value}), value

def test_source_must_not_be_a_local_path(tmp_path):
    pack = _pack(tmp_path / "demo", source="/home/alice/Desktop/UI Office")
    assert any("path" in p for p in T.validate_theme(pack))

def test_duplicate_after_unicode_normalisation_is_refused(tmp_path):
    manifest = _manifest()
    # 'e' + combining acute vs precomposed 'é'
    manifest["files"] = {"portraits/café.png": {"sha256": "0"*64, "bytes": 1},
                         "portraits/caf\u00e9.png": {"sha256": "0"*64, "bytes": 1}}
    assert any("duplicate" in p for p in T.validate_manifest(manifest))

def test_token_css_only_allowlisted_vars():
    css = T.token_css({"color-accent": "#7cc8ff", "radius-control": "4px"}, {}, "/plugins/x", "1.0.0")
    assert "--ui-accent:" in css and "--radius-sm:" in css
    for banned in ("display:", "position:", "pointer-events", "z-index", "@layer", "!important", "url("):
        assert banned not in css
```

- [ ] **Step 2: run → FAIL.**
- [ ] **Step 3: implement** the contract, roots (`$QUBIT_HOME/office/themes`, then `resource_path("frontend/qubit-web/public/plugins/qubit-office")/themes`, then `.../qubit-dist/...`), cache keyed by manifest mtime, `theme_payload` (trimmed, `?v=<version>` in bases), `token_css` with selector `html body .ui-root` and only allowlisted vars.
- [ ] **Step 4: run** `scripts/run_tests.sh tests/test_office_theme.py -q` → PASS.
- [ ] **Step 5: commit**.

---

### Task 2: Portrait + body atlas builder (deterministic, QA-gated)

**Files:** create `scripts/office_theme_build.py`; test `tests/test_office_theme_build.py`.

- [ ] **Step 1: failing tests** — same slug ⇒ same decoded pixels; different slug ⇒ different; portrait atlas = `cols×rows×size`; body atlas = `cols×frame_w` by `rows×frame_h`; both indexes have every slug in bounds; **the recipe indices are identical for the portrait and the body of one slug**; QA rejects blank/overfull tiles; empty license/credit refuses; manifest has sha+bytes for every file.
- [ ] **Step 2: run → FAIL.**
- [ ] **Step 3: implement** **key-based** deterministic recipes (`sha256(slug + ":" + part)` mod list length) stored in `agents.json`, an explicit z-order in `agent_sheets.json`, compose the portrait (Skins→Eyes→Hairstyles→Accessories, 64 px cells at the 32 tier → `NEAREST` to 32) and the body (`Bodies→Outfits→Eyes→Hairstyles→Accessories`, 32×64 frame), QA each tile (alpha 2–98 %, non-empty bounds, feet inside the frame), pack both atlases (lossless WebP when smaller, PNG otherwise — the server serves both), write `agents.json` + both indexes, manifest sorted by key, and stamp `template_version`.
- [ ] **Step 4: run tests.**
- [ ] **Step 5: commit**.

---

### Task 3: Extraction + reproducible `--check`

**Files:** create `scripts/office_theme_extract.py`; test `tests/test_office_theme_extract.py`.

- [ ] **Step 1: failing tests** — in-memory read of `modernuserinterface-win.zip` yields non-empty skin/eyes/hair/accessory layers; missing zip → typed error; `--check` returns differences when a committed tile changes.
- [ ] **Step 2: run → FAIL.**
- [ ] **Step 3: implement** in-memory zip reads for **both** generators (`Character Pieces/{Bodies,Outfits,Eyes,Hairstyles,Accessories}/{16,32,48}` and `Portrait Pieces/Portrait_Generator - {16,32,48}/{Skins,Eyes,Hairstyles,Accessories}`), the 2×-cell portrait convention (64 px cell at the 32 tier), the 32×64 body frame, and `--calibrate` which writes a labelled frame grid per body sheet. Record the chosen **front-idle frame** and the layer order in `agent_sheets.json` (human-reviewed once), then `--check` compares decoded pixels + sorted JSON.
- [ ] **Step 4: build the real pack**, then `--check`.
- [ ] **Step 5: commit**.

---

### Task 4: Serve theme + seat atlas cells

**Files:** `theme.py`, `agent_panel.py`, `qubit_compat.py`, `webui.py`; test `tests/test_office_theme_serve.py`.

- [ ] **Step 1: failing tests** — route registered; classic payload empty; `?theme=limezu` has bases/tokens/atlas/index but **no file map**; a seat gets `portrait` only when the index lists it; panel still returns 283 seats.
- [ ] **Step 2: run → FAIL.**
- [ ] **Step 3: implement**; payload `{id,label,version,credit,license,assetBase,atlas,index,tokens,slices}` with `?v=<version>`.
- [ ] **Step 4: run** theme + agents tests → PASS.
- [ ] **Step 5: commit**.

---

### Task 5: Atlas sprites in the Agents panel

**Files:** `agents-panel.ts`, `AgentsPanel.tsx`; test `agents-panel.test.ts`.

- [ ] **Step 1: failing tests** — a seat’s cell renders a 32×32 sprite span with `role="img"` + `aria-label="<name> portrait"`; the fallback chain is generated portrait → initials chip (agent name) → emoji, with the initials/emoji visible until the sheet is ready and `aria-hidden="true"` only once the sheet is confirmed; a broken sheet keeps the initials/emoji visible and announced; no per-seat `<img>` requests; keyboard/AT focus order unchanged.
- [ ] **Step 2: run → FAIL.**
- [ ] **Step 3: implement** — preload both atlases once, add `office-portraits-ready` on success, position via `background-position: calc(-1 * var(--cell-x) * 32px) calc(-1 * var(--cell-y) * 32px)`, `image-rendering: pixelated`, fixed 32×32 box (no layout shift). The **body atlas** feeds a **dialogue-style agent card** on row hover/focus: the portrait in a framed box (UI slice from Task 7), the agent name on the tab, and the 32×64 body beside it — the pattern the pack author demonstrates. The card is keyboard-reachable and `aria-hidden` unless focused; without Task 7's slices it renders with a plain border.
- [ ] **Step 4: run** office-app Vitest.
- [ ] **Step 5: commit**.

---

### Task 6: Server-injected iframe theme + host theme

**Files:** `voicebridge/webui.py` (`_serve_app_html`), create `office-theme.ts`, modify `OfficeAppShell.tsx`, `office-menu.ts`; tests `tests/test_office_framing_policy.py`, `src/lib/office-theme.test.ts`.

**Interfaces:** `theme_style_html(cfg, requested=None) -> str` (empty for classic/other apps), `loadOfficeTheme`, `applyHostTheme`, `themeCss`, `officeThemeClass`.

- [ ] **Step 1: failing tests**
  - server: office app document contains exactly one `<style data-office-theme>` only when a theme is active; it appears **after** `</head>`’s stylesheet link; other plugin apps get none; the style contains only allowlisted vars and no `url(`; CSP remains `style-src 'self' 'unsafe-inline'`;
  - host: classic ⇒ no class/link/fetch; active ⇒ one link + `data-office-theme`; idempotent; dispose removes; a parent rerender does not change the iframe `src` or remount the dock;
  - override: `?theme=limezu` themes the host **and** the iframe (the override is appended to the iframe URL and read by `_serve_app_html`); an invalid override falls back to `classic` on both sides;
  - existing office-document contract tests are updated for the extra `<style>` (they must still assert the token/stream globals and CSP);
  - menu: if `.ui-btn-group[aria-label="Panels"]` is missing after a bounded wait, fallback icon buttons render in the host; if the group appears later, injection takes over and the fallback is removed (never both at once).
- [ ] **Step 2: run → FAIL.**
- [ ] **Step 3: implement** server injection (office app only, after the app stylesheet, `html body .ui-root`), host loader (one `no-store` fetch, `?v=`), menu fallback, dispose.
- [ ] **Step 4: run** tests.
- [ ] **Step 5: commit**.

---

### Task 7: Dock/menu chrome from tokens (optional slices)

**Files:** `office-theme.ts`, `OfficeAppShell.tsx`, `office-menu.ts`; test `src/lib/office-theme.test.ts`.

- [ ] **Step 1: failing tests** — docks/buttons/card carry stable theme classes; tokens appear as CSS vars; a declared slice emits `border-image` with `border-image-width` equal to the CSS border; no slice ⇒ no `border-image`; no rule touches layout/pointer/z-index.
- [ ] **Step 2: run → FAIL.**
- [ ] **Step 3: implement** token styles for our elements only; optional validated 9-slice CSS for the **dialogue/agent card**, dock panel, tab and button (normal/hover/pressed), plus the close icon. Author the slice table from the Modern UI contact sheet, using the developer's dialogue-box/character-panel screenshots as the layout reference.
- [ ] **Step 4: run tests.**
- [ ] **Step 5: commit**.

---

### Task 8: `qubit office theme` CLI

**Files:** create `voicebridge/office/theme_cli.py`; extend `voicebridge/cli.py:1322`; test `tests/test_office_theme_cli.py`.

- [ ] **Step 1: failing tests** — validate exit 2 + problems; install into a throwaway `QUBIT_HOME`, refuse overwrite without `--force`; refuse `..`/symlink/duplicate/oversize entries; export re-installs and re-validates.
- [ ] **Step 2: run → FAIL.**
- [ ] **Step 3: implement** bounded atomic install (plain 32-bit zip, declared sizes, temp dir → readback validate → rename).
- [ ] **Step 4: run tests.**
- [ ] **Step 5: commit**.

---

### Task 9: Guards + visual goldens

**Files:** extend `tests/test_office_assets_vendored.py`; create `tests/test_office_visual_regression.py` + fixtures.

- [ ] **Step 1: failing guards**
  - no `.zip`/`.exe` under the office public tree;
  - every committed theme validates;
  - **Habblaud unchanged:** `python3 scripts/vendor_habblaud.py --check` exits 0;
  - the office document’s theme style appears only for `qubit-office`, never another plugin app;
  - active theme ⇒ 283 seats, no file map in the payload, credit present;
  - `LICENSE-ASSETS` exists and the payload carries the credit;
  - **no operator paths** in committed theme JSON (`/home/`, `~/`, `C:\\Users`, `\\\\`);
  - **contrast matrix** passes for text, muted, accent, border, status and level colours against the two surfaces (4.5 text / 3.0 non-text), and disabled states are ≥ 3.0;
  - **allowlist drift report:** `python3 scripts/office_theme_audit.py` lists `.ui-root` variables in the vendored CSS that are not in `ALLOWED_THEME_VARS` (warning only; no build failure).
- [ ] **Step 2: run → FAIL.**
- [ ] **Step 3: implement the visual probe.** `QUBIT_OFFICE_VISUAL=1`; loopback `WebUI`; stub tower/stream/panel; Playwright at DPR 1 and 2; animations off, caret hidden, clock fixed; element screenshots of the Agents panel (including the dialogue-style agent card), the menu group, and one **keyboard-focus** state (focus ring visible and contrast-checked); baselines committed and updated only after review.
- [ ] **Step 4: run guards + probe + `vendor_habblaud.py --check`.**
- [ ] **Step 5: commit**.

---

### Task 10: Share docs, licence records, upgrade playbook

**Files:** `docs/OFFICE-UI-THEMES.md`, `LICENSE-ASSETS`, `docs/ATTRIBUTION-OFFICE.md`, `THIRD_PARTY_NOTICES.md`, `docs/STATUS.md`.

- [ ] **Step 1: `LICENSE-ASSETS`** — MIT code vs LimeZu art; credit link; no resale/redistribution; no NFT; **LimeZu is CC-BY, not CC-BY-SA — no source-sharing obligation**; credit placement (“Credits menu or end of game”); the free pack is non-commercial and not used; CC-BY TASL + changes for Serene Village; a note that the art is sliced/positioned by the build (not used to train or generate art).
- [ ] **Step 2: `docs/OFFICE-UI-THEMES.md`** — contract, install paths, CLI, pack creation (extractor/build/calibration/contact sheet), sharing, allowlisted variables, upgrade playbook. Document that **Character Generator 2.0** (art: LimeZu “official character generator” included with the complete pack; tool not shipped) and **Portrait Generator 1.5.0** (Gray Matter Studios app; art: LimeZu) are used as *art sources only*, the part indices are shared, and the tools themselves are never redistributed.
- [ ] **Step 3: `docs/ATTRIBUTION-OFFICE.md`** — TASL, changes, exact files. Add a **persistent Credits/About** entry (office footer or Settings) so the credit does not depend on a theme being active.
- [ ] **Step 4: notices + STATUS** with the two new packs and this session’s evidence.
- [ ] **Step 5: commit**.

---

## Invariants and verification matrix

| Check | Command | Expected |
|---|---|---|
| Theme unit | `scripts/run_tests.sh tests/test_office_theme.py tests/test_office_theme_build.py tests/test_office_theme_extract.py tests/test_office_theme_serve.py tests/test_office_theme_cli.py -q` | all pass |
| Agents panel | `scripts/run_tests.sh tests/test_office_agents_panel.py -q` | 13 pass |
| Guards | `scripts/run_tests.sh tests/test_office_assets_vendored.py -q` | pass; no raw packs; themes valid |
| Framing/CSP | `scripts/run_tests.sh tests/test_office_framing_policy.py -q` | style only for office app; CSP unchanged |
| **Habblaud untouched** | `python3 scripts/vendor_habblaud.py --check` | exit 0 |
| Office app Vitest | `cd frontend/qubit-web && npx vitest run --config vitest.office-app.config.ts` | all pass |
| Theme DOM | `npx vitest run src/lib/office-theme.test.ts` | pass |
| Visual goldens | `QUBIT_OFFICE_VISUAL=1 scripts/run_tests.sh tests/test_office_visual_regression.py -q` | pass at DPR 1 and 2 |
| Build | `npx vite build --config vite.office-game.config.ts` | succeeds |
| Reproducibility | `python3 scripts/office_theme_extract.py --check` | decoded pixels + JSON identical |
| Clean tree | `scripts/run_tests.sh tests/test_office_theme.py -q -k "operator_path or duplicate"` | pass (no operator path, no Unicode/case duplicate) |
| No paid art committed | `git ls-files frontend/qubit-web/public/plugins/qubit-office/themes` | recipe JSON only — no `.png`/`.webp` atlases |
| Outfit variety | `python3 scripts/office_theme_build.py --check` | two recipes with different outfit files yield different torso hashes |
| Drift report | `python3 scripts/office_theme_audit.py` | lists new unthemed `.ui-root` vars; exit 0 |
| Live classic | `/qubit-office` no theme | identical look, no extra requests, HUD marker 15 s |
| Live themed | `office.theme: limezu` / `?theme=limezu` | atlas = **1** image request, credit visible, menu order unchanged, game interactive |
| Rollback | `QUBIT_OFFICE_THEME=classic` | immediate classic look |

## Rollback

- `office.theme: classic` / `QUBIT_OFFICE_THEME=classic`; remove the pack directory.
- Habblaud is never modified, so nothing to revert there.

## Upgrade playbook

1. **Add, never rename** tokens/variables; old packs keep working.
2. **Schema gate:** loader accepts v1 only; a newer pack is refused with a typed reason. v2 needs a migration step + docs.
3. **New art:** run the extractor with a new `--source`, rebuild, `--check`, review goldens.
4. **New surfaces:** consume `loadOfficeTheme`; no new fetch/format.
5. **Share:** zip, publish sha, `qubit office theme install`.
6. **Vendor upgrade:** if Habblaud is ever updated for security/upstream, re-run the menu-group selector test, `office_theme_audit.py` (new `.ui-root` variables) and the goldens; the fallback buttons cover a changed selector. Never patch Habblaud for a theme — add the new variable to the allowlist instead.
7. **Never commit a personal path.** `source` is a description; the clean-tree guard scans theme JSON.

## Self-review

- **Owner constraint:** Part A has zero Habblaud edits; Part B may extend it additively (ruling 16) with goldens/rollback/provenance; `vendor_habblaud.py --check` remains the drift gate; the upstream `theme` field is not repurposed.
- **Security:** typed tokens (CSS-exfil vectors rejected), no pack CSS, server-side injection, CSP contract test.
- **Performance:** one atlas request, native 32 px, no lazy-flood; palette/contrast validated.
- **Test honesty:** behaviour + artifacts only.
- **Type consistency:** `Theme.tokens/slices/portraits` used across Tasks 1/4/5/6/7; `portrait` is `{x,y} | null`; server style and host link use the same validated payload.

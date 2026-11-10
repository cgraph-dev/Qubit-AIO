# Qubit Office UI themes — creator and operator guide

**Status:** operational guide for the `qubit-office-theme/1` contract shipped
with Qubit Office. This document makes no `VERIFIED`, release, or parity claim.
Every command below was exercised in the theme worktree against the tree that
ships the builder and the committed recipe. Evidence: the office UI theme lane
entry in `docs/STATUS.md`, and the Task 1-10 lane reports under
`.superpowers/sdd/2026-11-09-office-ui-theme-upgrade/`. The strongest single
check is the focused set printed in §10; the vendor and audit gates are in §9.

---

## 1. What a theme is

There are two distinct objects, and they are deliberately not interchangeable:

| Object | What it contains | Where it lives | Shareable? |
|---|---|---|---|
| **Recipe** (`theme.recipe.json`) | Semantic colour tokens, per-agent part indices, part counts, `template_version`, `credit`, `license`, and a sanitised `source` description. **No files, no atlases, no art.** | Committed at `frontend/qubit-web/public/plugins/qubit-office/themes/limezu/theme.recipe.json` (bundled seed) or produced with `--recipe-out`. | **Yes.** This is the sharing format. |
| **Full pack** | The same manifest fields plus `theme.json`, `agents.json`, `agent_sheets.json`, two atlases, two index files, and a sha256/byte `files` map. | `$QUBIT_HOME/office/themes/<id>` on the person who owns the art purchase. Never committed. | **No.** It contains LimeZu art; redistribution is forbidden. |

The repository ships the recipe and the builder. It never ships the purchased
`.zip` archives, the extracted part sheets, or a derived atlas. A local pack is
built only on the machine that holds the purchase, under the user's home, and
is outside the checkout.

The bundled `themes/limezu/theme.recipe.json` is metadata only. `qubit office
theme list` reports it as a `recipe`, and it is not renderable until it is
materialised into a local pack; an active recipe without a pack resolves to
`classic`.

### Pack files written by the builder

A 32 px full pack contains:

```text
<id>/theme.json               qubit-office-theme/1 manifest (files = sha256 + bytes)
<id>/agents.json              schema 1, templateVersion, parts, frozen recipes
<id>/agent_sheets.json        frame calibration, z-order, part counts
<id>/portraits/atlas32.png    one 32x32 tile per persona (WebP when smaller)
<id>/portraits/index.json     {"schema":1,"size":32,"cols":20,"rows":15,"cells":{...}}
<id>/bodies/atlas32.png       one 32x64 frame per persona
<id>/bodies/index.json        same cells, plus frame {w:32,h:64}
```

The `files` map in `theme.json` lists the six payload files; `theme.json` itself
is not served. The default roster has **283 personas**, so the atlas is 20
columns x 15 rows. The measured real build prints
`{"files":6,"id":"limezu","ok":true,"recipes":283}`.

---

## 2. Build a local pack from a purchase

### Prerequisites

- The owner's purchased LimeZu generator archives (see §7), held at the
  owner's local download. They are never copied into the repository.
- Pillow, available in the Qubit runtime environment (the builder is a
  build-time CLI, not a runtime dependency of the office page).

### Inspect the purchase without writing anything

```bash
python3 scripts/office_theme_extract.py --source "<purchase dir>"
python3 scripts/office_theme_extract.py --source "<purchase dir>" --check
```

`--source` reads the generator zips **in memory**, reports the discovered part
counts and a decoded-pixel digest, and writes nothing. `--check` reads a second
time and requires the same counts and digest. `--size {16,32,48}` selects the
tier; the default is 32.

The extractor also supports two authoring/read-only actions:

```bash
# Sample the purchased Modern UI sheet's dominant colours as validated tokens.
python3 scripts/office_theme_extract.py --tokens "<sheet.png|zip>" [--member NAME]

# Write a labelled coordinate grid for authoring 9-slice cells. Never inside the repo.
python3 scripts/office_theme_extract.py --ui-contact-sheet "<sheet.png|zip>" \
  --out /tmp/office-contact [--member NAME] [--scale 2] [--ui-grid 16]
```

### Build

```bash
python3 scripts/office_theme_build.py \
  --source "<purchase dir>" \
  --out "$QUBIT_HOME/office/themes/limezu"
```

`--out` defaults to `$QUBIT_HOME/office/themes/<id>`, where `<id>` is the output
directory name (lowercase, `^[a-z0-9][a-z0-9-]{0,39}$`). A full-pack `--out`
inside the repository checkout is a typed refusal: the pack carries paid art
and must stay outside the tree. `$QUBIT_HOME`, `/tmp`, and other paths outside
the checkout are allowed. The builder stages into a temporary sibling,
validates with the real contract, and only then moves the pack into place.

The one deliberate in-repo output is `--recipe-out`, which writes **only** the
shareable `theme.recipe.json` (no atlas, no art). Use it for the committed seed
recipe; never use a full-pack `--out` for an in-repo build.

Useful modes:

```bash
# Reproduce the pack from the same source and compare it semantically:
python3 scripts/office_theme_build.py --source "<purchase dir>" \
  --out "$QUBIT_HOME/office/themes/limezu" --check

# Review the calibrated frames on the real body-view sheets (read-only on zips).
python3 scripts/office_theme_build.py --source "<purchase dir>" \
  --out /tmp/office-cal --calibrate --calibrate-out /tmp/office-cal.calibrate

# Sample the purchased Modern UI palette instead of the warm defaults:
python3 scripts/office_theme_build.py --source "<purchase dir>" \
  --out "$QUBIT_HOME/office/themes/limezu" --ui-sheet "<modern UI sheet or zip>"

# Emit ONLY the shareable recipe (the one repo-safe output; no atlas, no art):
python3 scripts/office_theme_build.py --source "<purchase dir>" \
  --recipe-out frontend/qubit-web/public/plugins/qubit-office/themes/limezu/theme.recipe.json
```

`--check` compares decoded image pixels and parsed JSON, so a different encoder
or JSON formatting is not a difference. A changed tile or recipe is. Unlike a
byte-for-byte comparison, it is stable across Pillow versions.

### Where the art comes from

The extractor resolves a directory (or a direct path to one of the archives) in
this order:

1. `Character Generator 2.0 Linux Build.zip` — body, outfit, body eyes, body hair.
2. `Portrait_Generator_1.5.0_Linux_Build.zip` — portrait skins, eyes, hair.
3. `modernuserinterface-win.zip` — portrait fallback pieces and the Modern UI
   sheet sampled for the palette.

The portrait generator is preferred for portraits; when it is absent, the
portrait pieces carried inside the Modern UI pack are used. A missing or
invalid archive is a typed error naming the file. The **Character Generator
archive is the build input** for the body-side sheets. The **full Modern
Interiors archive is not a build input** (it is recorded for provenance in
`THIRD_PARTY_NOTICES.md`); the Modern UI archive supplies the sampled palette
and the portrait fallback.

---

## 3. `qubit office theme` CLI

The CLI is intentionally recipe-only: it installs/validates/lists/shares
recipes, and never fetches art.

```bash
qubit office theme list
qubit office theme validate PATH
qubit office theme install PATH [--force] [--dry-run]
qubit office theme export ID OUT.zip [--dry-run]
```

- **`list`** — every theme under the user root then the bundled/source roots
  (most specific first, deduped by id), its `kind` (`pack` or `recipe`),
  `source` (`user` or `bundled`), validity/problems, label, version, and the
  active id. A recipe-only theme shows `active: classic`; that is correct, not
  an error.
- **`validate PATH`** — a recipe directory/`.json`/`.zip` via
  `validate_recipe`, or a full pack directory/`.zip` via `validate_theme`
  (real file existence, sha256 and byte checks). Clean input prints
  `OK <kind> <id> <path>` and exits 0; problems print one `- problem` line
  each and exit 2.
- **`install PATH`** — accepts a recipe, a locally built pack, or a zip of
  either. Input is staged beside `$QUBIT_HOME/office/themes`, validated, moved
  atomically, and read back. An existing theme is refused (`THEME_EXISTS`)
  unless `--force`. A recipe install prints the exact local build command.
- **`export ID OUT.zip`** — writes a deterministic zip with exactly one member,
  `theme.recipe.json`. It never emits an atlas or any image.

Archive input is hostile input: only plain 32-bit zips with regular files and
directories are accepted. Traversal, symlinks, special files, executable bits,
unsupported compression, encryption, ZIP64, duplicate/NFC/case-colliding
names, oversized entries and compression-ratio bombs are refused before
anything is written.

---

## 4. Install paths, activation, precedence, rollback

### Install root

```text
$QUBIT_HOME/office/themes/<id>/theme.json   served pack (user root)
```

`$QUBIT_HOME` defaults to `~/.qubit` on Linux/macOS (platform rules are owned
by `voicebridge.paths.qubit_home`). The source/public tree and the built
dashboard carry a loader-only seed root; **the HTTP asset route serves only the
user root**, and only files declared in that pack's `files` map. A pack that
exists only in a bundled/source root can load its payload but its assets 404.

### Activation

Persistent:

```yaml
# The active profile's config.yaml
office:
  theme: limezu
```

Per page (read-only override):

```text
/qubit-office?theme=limezu
```

Process-wide override / kill switch:

```bash
QUBIT_OFFICE_THEME=classic
```

Precedence is **`QUBIT_OFFICE_THEME` > `?theme=` > `office.theme` > `classic`**.
A malformed id, an unknown pack, or a pack that fails validation resolves to
`classic`; the server and the host shell never disagree about which id is
active. `?theme=` is propagated to the iframe URL and read by the server-side
injection. The query is read once per load; a client-side query edit after load
updates only on reload.

### Rollback

```bash
# Immediate classic look, no page edit:
QUBIT_OFFICE_THEME=classic   # in the process/service environment, then restart

# Or persist classic and remove the local pack:
#   office.theme: classic
rm -rf "$QUBIT_HOME/office/themes/limezu"
```

Habblaud is never modified, so a theme rollback never requires reverting the
vendored client.

---

## 5. The contract (`qubit-office-theme/1`)

Both a recipe and a full pack declare:

| Field | Rule |
|---|---|
| `format` | Exactly `qubit-office-theme`. |
| `schema` | Exactly `1`. A newer schema is refused; see the upgrade playbook. |
| `id` | `^[a-z0-9][a-z0-9-]{0,39}$`; also the directory name. |
| `label` | Non-empty display name. |
| `version` | Safe version string; used as the asset cache-busting `?v=`. |
| `credit` | Non-empty; names the art author and URL. |
| `license` | Non-empty; states the licence boundary. |
| `source` | A **description**, never a filesystem path. `/home/...`, `~/...`, drive paths, UNC paths, and `..` are refused. |
| `template_version` | Positive integer; selecting a new restyle version for recipes. |
| `tokens` | Semantic tokens (below), typed and contrast-checked. |
| `parts` | `{skin,eyes,hair,outfit,accessory}` -> available part count. |
| `agents` | `{slug: {skin,eyes,hair,outfit[,accessory]}}`, each index in bounds. |
| `slices` | Optional 9-slice descriptors; empty in the v1 seed. |

A **full pack** additionally carries `portraits` (`atlas`, `index`, `size`,
`cols`, `rows`), `bodies` (the same plus `frame {w,h}`), a `files` map
(`relative -> {sha256, bytes}`), and `limits`. A **recipe** must not carry
`files`, `portraits`, or `bodies`.

### Allowlisted `.ui-root` variables

A theme may set only these 22 variables. The mapping is Qubit-owned: a pack
names semantic tokens, and Qubit emits the Habblaud variable. An upstream
variable rename is a one-line change in `voicebridge/office/theme.py`, never a
pack migration.

| Variable | Semantic token(s) |
|---|---|
| `--ui-bg` | `color-surface` |
| `--ui-bg-soft` | `color-surface-soft` |
| `--ui-bg-solid` | `color-surface-raised` / `color-surface-solid` |
| `--ui-bg-hover` | `color-surface-hover` |
| `--ui-line` | `color-border` |
| `--ui-line-strong` | `color-border-strong` |
| `--ui-text` | `color-text-primary` |
| `--ui-muted` | `color-text-muted` |
| `--ui-faint` | `color-text-faint` |
| `--ui-accent` | `color-accent` |
| `--ui-accent-soft` | `color-accent-soft` |
| `--radius` | `radius-panel` |
| `--radius-sm` | `radius-control` |
| `--st-working` … `--st-shell` | `color-status-working` … `color-status-shell` (six statuses) |
| `--lv-ok`, `--lv-warn`, `--lv-crit` | `color-level-ok` / `warn` / `crit` |

Token values are typed: a hex colour (`#rgb` / `#rrggbb`) or a length
(`px`/`rem`/`em`). Values carrying CSS exfiltration or injection vectors
(`url(`, `image-set(`, `src(`, `var(`, `attr(`, `expression(`, `javascript:`,
`@`, `\`, `;`, `}`, comments) are refused, not escaped. Contrast is checked
against both `--ui-bg` and `--ui-bg-solid`: text and muted text need 4.5:1;
faint/disabled text, accents, borders, statuses and level colours need 3:1.

### No pack CSS

A pack is data: `.png`, `.jpg`, `.jpeg`, `.webp`, `.json`, `.txt`. There is no
CSS, JS, HTML, symlink, or remote URL in a pack. The server injects one
`<style data-office-theme="<id>">` for the office document from the validated
tokens; the host `theme.css` is generated by Qubit from the same payload. A
pack's optional `slices` are data too: Qubit emits the `border-image` rules.
This keeps the vendored Habblaud client's classic behavior untouched (Part B may extend it additively behind the theme flag, ruling 16) and makes a pack unable to
carry script or an outbound request.

---

## 6. Art sources and calibration

v1 uses **two generator applications as art sources only**. Neither tool is
shipped, modified, or redistributed by Qubit.

- **Character Generator 2.0** — art: LimeZu's official character generator,
  included with the complete pack; the tool itself is GPLv3 upstream and is
  not shipped.
- **Portrait Generator 1.5.0** — a Gray Matter Studios application; art:
  LimeZu; the tool is not shipped.

They share one part set, so the same indices produce a matching portrait and
body. The frozen recipe per persona is derived from
`sha256("<slug>:<part>") % len(parts)` (the DiceBear convention), so adding a
part later never reshuffles an existing persona and the chosen recipe is stored
in `agents.json`.

Measured on the purchase: 9 skins, 7 eyes, 29 hairstyles, 33 outfits and 9
bodies. The calibrated frames (row, column) are size-independent:

| Layer | Frame |
|---|---|
| body | row 0, col 0 |
| eyes (body overlay) | row 0, col 0 |
| hair (front idle) | row 3, col 0 |
| outfit bottom | row 0, col 0 |
| outfit base top (shared undershirt) | row 7, col 0 |
| outfit top (outfit colour) | row 8, col 0 |
| shoes | row 14, col 0 |
| portrait source cell | row 0, col 0 — a 64x64 cell at the 32 tier, downscaled to 32 with `Image.NEAREST` |

At the 32 tier a body frame is 32x64; the same row calibration works at the
16 and 48 tiers because rows are frame indices, not pixel offsets. Some outfit
files have empty row 7/8/14; the builder skips an empty outfit row rather than
failing.

Accessories are deliberately **not** part of v1: the portrait generator has
them, but they are not calibrated against the body frame, so a body accessory
would be misaligned. Add them only after a body calibration has been reviewed.

---

## 7. Sharing

Share **recipes only**:

```bash
qubit office theme export limezu limezu.zip
```

The zip contains exactly `theme.recipe.json` — no atlas, no archive, no art.
The recipient installs it and builds from their own purchase:

```bash
qubit office theme install limezu.zip
# then the exact build command the CLI prints:
python3 scripts/office_theme_build.py --source "<their purchase dir>" \
  --out "$QUBIT_HOME/office/themes/limezu"
```

A shared pack must never contain LimeZu art. The theme recipe is the unit of
collaboration; each person's local build is the unit of use. This follows the
purchased-art licence (no resale or redistribution) and the upstream generator
rule ("Never redistribute. Every user must buy their own copy").

---

## 8. Upgrade playbook

Use this order; it is designed so an old pack keeps working while the product
moves.

1. **Add, never rename tokens or variables.** New semantic tokens may be added
   with new variables; an existing token or allowlisted variable keeps its
   meaning. A rename would break every installed pack.
2. **Schema gate.** The loader accepts schema `1` only. A newer pack is refused
   with a typed reason (and the active theme falls back to classic). Schema
   `2` needs a migration step and its own documentation before the loader
   accepts it.
3. **New art.** Run the extractor with the new `--source`, rebuild, run
   `--check`, review the calibration grids and the visual goldens, then keep or
   replace the recipe.
4. **New surfaces.** Consume `loadOfficeTheme` and the existing payload. Do not
   add a second fetch, a second format, or pack CSS. New chrome uses
   `data-office-slice` only when the pack declares that role.
5. **Share.** Export the recipe zip, publish its sha256, and let each person
   install and build.
6. **Vendor upgrade.** If Habblaud is ever updated for security or upstream
   parity, run the checklist in §9. Part B may extend Habblaud additively behind the theme flag (ruling 16) — never regress the classic behavior; add the
   new variable or selector deliberately on the Qubit side.
7. **Never commit a personal path.** `source` is a description. The clean-tree
   guard scans committed theme JSON for `/home/`, `~/`, `C:\Users`, UNC paths,
   and backslashes.

---

## 9. Vendor-upgrade checklist

Run all of these before accepting a Habblaud update:

```bash
python3 scripts/vendor_habblaud.py --check          # vendored tree untouched / hashes match
python3 scripts/office_theme_audit.py               # new .ui-root variables reported (warning-only, exit 0)
QUBIT_OFFICE_VISUAL=1 scripts/run_tests.sh tests/test_office_visual_regression.py -q
cd frontend/qubit-web && npx vitest run --config vitest.office-app.config.ts
cd frontend/qubit-web && npx vite build --config vite.office-game.config.ts
```

- `office_theme_audit.py` lists `.ui-root` variables in the vendored CSS that
  are not yet allowlisted. It is a drift report, not a gate: decide
  deliberately whether each new variable is a theme token.
- The visual goldens pin the Agents dock, the app's Panels group, and the
  focused agent card at DPR 1 and 2. Re-baseline only after reviewing the
  image; the probe compares pixels, not source.
- The host-rendered fallback buttons cover a changed/missing Panels group. If
  the selector changed, the fallback keeps the controls reachable; the fix is a
  selector update on the Qubit side, never an edit inside `habblaud/`.
- `vendor_habblaud.py --check` must stay at exit 0. A non-zero result means the
  vendored client was modified; restore it rather than adapting the theme.

---

## 10. Known limits

- **Visual goldens use synthetic art and system Chrome.** The probe renders a
  deterministic synthetic pack through the production shell and the real host
  routes, but it does not load the 7 MB vendored client; the upstream
  animation is deliberately out of scope. Baselines are pinned to system
  Chrome on this host; a different browser/font/GPU stack can exceed the
  changed-pixel budget. A host without system Chrome skips with a named
  reason instead of faking a pass.
- **Slice coordinates are deferred.** v1 ships tokens plus the validated
  9-slice mechanism; the committed recipe carries no slice coordinates. Use
  `--ui-contact-sheet` for the future visual authoring pass. No coordinates
  are invented.
- **Fractional browser zoom distorts pixel art.** Portrait tiles are native
  32 px shown at exactly 32 CSS px with `image-rendering: pixelated`. Browser
  zoom that lands between integer scale factors is outside the product's
  control.
- **A real pack build needs the owner's purchase.** CI can validate the
  committed recipe and unit-test the builder with synthetic sheets; it cannot
  regenerate the LimeZu atlases. `--check` and `--calibrate` need the purchase.
- **No full matrix and no live restart were performed for this lane.** The
  focused theme suite, the visual probe and the committed recipe are the
  evidence. `VERIFIED` is not claimed.

### Evidence commands

```bash
PYTHONPATH=. TMPDIR=/tmp python3 -m pytest -q \
  tests/test_office_assets_vendored.py tests/test_office_theme.py \
  tests/test_office_theme_build.py tests/test_office_theme_extract.py \
  tests/test_office_theme_serve.py tests/test_office_theme_cli.py \
  tests/test_office_framing_policy.py tests/test_office_agents_panel.py \
  tests/test_office_vendor_habblaud.py
QUBIT_OFFICE_VISUAL=1 PYTHONPATH=. TMPDIR=/tmp python3 -m pytest -q \
  tests/test_office_visual_regression.py
python3 scripts/vendor_habblaud.py --check
python3 scripts/office_theme_audit.py
cd frontend/qubit-web && npx vitest run --config vitest.office-app.config.ts
```

The exact counts and the real-build output are recorded in `docs/STATUS.md`
and in the Task 1-10 lane reports, not guessed here.

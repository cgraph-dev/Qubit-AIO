# Qubit Office UI Theme Upgrade — Implementation Plan (v3, atomic review)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Qubit Office look better with the owner-purchased LimeZu art, and make future UI changes easy to add and share — **without touching Habblaud**.

**Architecture:** A theme is a declarative, data-only folder (`theme.json` + portraits + role tokens; optional 9-slice descriptors). Qubit’s own shell around the iframe consumes it: agent portraits in the Agents panel, token-driven styling for the docks and the injected menu icons, and an **append-only** override of a documented allowlist of the office’s own `.ui-root` CSS variables. The vendored Habblaud tree stays byte-identical. `office.theme` defaults to `classic`, so every change is opt-in and reversible.

**Tech Stack:** Python 3.14 + Pillow (build/validate/serve), TypeScript/React 19 (host-provided React), Vitest (jsdom for DOM), pytest, Playwright (Python, already used by `tests/_probe_agents_panel_browser.py`), the existing static `/plugins/` route.

**Spec:** `docs/DECISIONS.md` rulings 14 and 15, `THIRD_PARTY_NOTICES.md` §"LimeZu — purchased office art", and the 2026-11-09 asset audit of `/home/trick/Desktop/UI Office`.

---

## Owner constraint (the one that shapes everything)

> “dont fuck habblaud we just update ui to make it better and easier to upgrade in the future”

Therefore:
- **Do not edit, patch, rebuild, or re-vendor `frontend/qubit-app/plugins/qubit-office/habblaud/`.**
- Theme work lives in the Qubit-owned shell (React host) and in **runtime, append-only** overrides of the office document:
  - our injected menu buttons (`office-menu.ts`) get theme classes;
  - our docks (Agents/HUD) get token styles;
  - the iframe receives **one `<style>` node** that sets an allowlisted subset of Habblaud’s own `.ui-root` variables (colors/radii only), plus class hooks on the buttons we already injected.
- The office remains fully usable with the theme off and on. No overlays, no `pointer-events` changes, no z-index games, no interception of the office API.
- Verification that we did not touch Habblaud: `python3 scripts/vendor_habblaud.py --check` stays green and the vendored hash list is unchanged after every task.

## Non-goals (explicit)

- No changes to the office engine, simulation, room layout, or canvas art.
- No new network calls at runtime; the theme is local static files.
- No changes to agent behavior, tool availability, writes, approvals, or the stream.
- No new core tool, no plugin, no new config section beyond `office.theme`.
- No pack-supplied CSS/JS/HTML. Shared packs are data only.

---

## Review outcomes (what changed from v2, and why)

| # | Finding | Severity | Fix in v3 |
|---|---|---|---|
| 1 | v2 patched Habblaud’s art layer and re-vendored it | **Blocker** | Removed entirely. Part B deleted; owner ruling 15 recorded; a hash gate added. |
| 2 | Pack-provided CSS is executable content in a shareable artifact | High | Packs are data-only; Qubit generates CSS from validated tokens. |
| 3 | Runtime iframe styling could break the game | High | Strict allowlist: only `.ui-root` color/radius variables + our injected elements; no layout/pointer/z-index; a test enforces the allowlist. |
| 4 | `?theme=` was claimed but not wired | High | `active_theme_id(cfg, requested=None)`; the route reads `?theme=` as a read-only override. |
| 5 | Theme asset updates could be stale for an hour (static route caches 1 h) | Medium | Asset URLs carry `?v=<version>`; a version bump is immediately visible and still cacheable. |
| 6 | 283 portraits could be fetched eagerly | Medium | `loading="lazy"`, `decoding="async"`, panel only mounts when open; payload trimmed to bases, not the file map. |
| 7 | Portrait lookup would `stat` 283 files per request | Medium | Resolve membership from the validated manifest; cache the loaded theme by manifest mtime. |
| 8 | Reproducibility depends on Pillow/zlib versions (raw PNG bytes can differ) | Medium | `--check` compares decoded pixels + the JSON; the manifest sha still pins committed bytes for integrity. |
| 9 | No accessibility gate for token pairs | Medium | WCAG contrast check for text/surface pairs; alt text; reduced-motion untouched. |
| 10 | Visual change had no regression gate | Medium | Opt-in Playwright element goldens at DPR 1 and 2, animations off, fixed clock, stubbed office data. |
| 11 | 9-slice coordinates would be guessed | Medium | v1 ships tokens only; `slices` is optional and validated when present; a contact-sheet step exists for authoring them later. |
| 12 | Plan tests could drift into source-shape assertions | Low | Behaviour tests only; the licence guard asserts the loader’s behaviour + a real `LICENSE-ASSETS` file. |
| 13 | Task 2 mixed build, extraction, and QA | Low | Split into Task 2 (portrait core) and Task 3 (extraction + check). |
| 14 | No migration story for future schema changes | Low | “Upgrade playbook” section: add-only tokens, fail-closed on newer schema, explicit v2 steps. |

## Research (2026-11-09) — the practice this plan follows

1. **Licence (verified from the artist).** Commercial use + credit required; resale/redistribution prohibited. CC-BY (Serene Village only) needs TASL + a changes note. A real precedent ships LimeZu tilesets with `LICENSE-ASSETS` + attribution, keeping code under MIT.
   `https://limezu.itch.io/moderninteriors`, `https://itch.io/post/2185336`,
   `https://github.com/chaitanyagiri/munder-difflin/blob/main/LICENSE-ASSETS`,
   `https://creativecommons.org/licenses/by/4.0/deed.en`,
   `https://wiki.creativecommons.org/wiki/Recommended_practices_for_attribution`.
2. **Portable theme formats are declarative and fail-closed** (versioned manifest, per-file sha+bytes, basename-only paths, reserved-name checks, size caps, atomic install, machine-readable errors; no scripts/CSS/remote resources).
   `https://deepwiki.com/fagemx/slot-asset-pipeline/6.1-atomic-theme-v1-package-format`,
   `https://denframe.com/docs/format`, `https://github.com/Fei-Away/Codex-Dream-Skin/issues/108`.
3. **Theming is role tokens, not brand colors or raw CSS** (`--color-surface`, scoped by a `data-theme` attribute, generated from one source of truth, flag-gated rollback).
   `https://appjet.ai/blog/theme-for-application`,
   `https://www.css-architecture.com/advanced-theming-dark-mode-implementation/runtime-theme-switching/`.
4. **9-slice chrome** uses `border-image` with slices matching the authored border, `border-image-width` equal to the CSS border, `stretch` default, background separate; 2× export for HiDPI.
   `https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Properties/border-image-slice`,
   `https://css-tricks.com/revisiting-css-border-image/`.
5. **Pixel integrity:** `image-rendering: pixelated`, integer scaling, test DPR 1 and 2.
   `https://developer.mozilla.org/en-US/docs/Games/Techniques/Crisp_pixel_art_look`,
   `https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Properties/image-rendering`.
6. **Visual regression:** element-level Playwright goldens, animations/caret disabled, fixed clock, data stubbed, baselines from the same OS/browser, reviewed before update.
   `https://playwright.dev/docs/test-snapshots`, `https://playwright.dev/docs/best-practices`,
   `https://diffy.website/blog/playwright-visual-regression-testing/`.

---

## Global constraints

- **`classic` is the default.** `office.theme` unset ⇒ today’s exact look. `QUBIT_OFFICE_THEME` overrides for one process; `?theme=` overrides for one page (read-only).
- **Habblaud is read-only.** No file in `.../habblaud/` changes. Only runtime, append-only style/class application.
- **Packs are data.** Allowed: `.png`, `.jpg`, `.jpeg`, `.webp`, `.json`, `.txt`. No CSS/JS/HTML/SVG-script, no symlinks, no remote URLs.
- **Credits required.** The pack carries `credit` + `license`; the UI shows the credit when a theme is active. LimeZu: `https://limezu.itch.io`. CC-BY packs: TASL + changes.
- **No raw archives in the tree.** Derived files only, each with sha256 + byte length.
- **Existing owner decision O1/C4:** no celebrity/meme sprite families.
- **No new polling/remounts.** The HUD/Agents docks keep stable component identity (`c5bd8a69b`); a theme switch may swap a `<link>`/`<style>`, never a component type.
- **Pixel integrity:** portraits at 16/32/48; `image-rendering: pixelated`; integer scaling only.
- **No service restart during implementation.** Focused tests only; `VERIFIED` is not claimed.
- **Tests:** `scripts/run_tests.sh tests/...` (Python), `cd frontend/qubit-web && npx vitest run …` (TS). No bare `pytest`. No test reads `.py`/`.ts` source to assert shape.

## Review focus (the five failure modes a user would hit)

1. **Bad pack ⇒ no styling, typed reason, `classic` look.** Never half-applied.
2. **Broken/missing portrait ⇒ emoji fallback**, row keeps name/type/room.
3. **No theme configured ⇒ zero change**: same DOM, same requests, same pixels.
4. **Theme on ⇒ the game still plays and the refresh loop stays fixed** (15 s iframe marker; no repeated panel fetches).
5. **A shared pack cannot execute code or escape its directory**, and install is atomic and bounded.

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
  "source": "owner purchase 2026-11-09",
  "tokens": {
    "color-surface": "#0d0d1a",
    "color-surface-raised": "#161a23",
    "color-text-primary": "#e9edf7",
    "color-text-muted": "#8a93a8",
    "color-border": "#334155",
    "color-accent": "#7cc8ff",
    "radius-control": "4px"
  },
  "slices": {},
  "portraits": { "dir": "portraits", "size": 48, "manifest": "portraits.json" },
  "files": {
    "portraits/ceo.png": { "sha256": "<64 hex>", "bytes": 1456 },
    "portraits.json":    { "sha256": "<64 hex>", "bytes": 123 }
  },
  "limits": { "max_total_bytes": 25000000, "max_files": 400 }
}
```

Validation rules: exact format/schema; id `^[a-z0-9][a-z0-9-]{0,39}$`; non-empty credit/license; every file path relative, `[A-Za-z0-9._/-]`, no `.`/`..` segment, no Windows reserved basename, allowlisted suffix, no symlink, exists, `bytes`/`sha256` match; totals within limits; token keys `^[a-z][a-z0-9-]{1,40}$` with color/length values only; optional slices: 4 positive ints, `width` px, `repeat ∈ stretch|round|repeat`, `src` declared. Case-insensitive duplicate paths are refused.

**Allowlisted Habblaud variables (the only ones a theme may set):**
`--ui-bg`, `--ui-bg-soft`, `--ui-bg-solid`, `--ui-bg-hover`, `--ui-line`, `--ui-line-strong`, `--ui-text`, `--ui-muted`, `--ui-faint`, `--ui-accent`, `--ui-accent-soft`, `--radius`, `--radius-sm`, `--st-working`, `--st-waiting`, `--st-done`, `--st-idle`, `--st-offline`, `--st-shell`, `--lv-ok`, `--lv-warn`, `--lv-crit`.
Everything else in Habblaud’s `.ui-root` block (layout sizes `--side-w`, `--drawer-w`, `--top-h`, `--gap`, `--free-*`, fonts `--font-*`, `--ease`) is **not** themed.

---

## File structure (Part A only — nothing under `habblaud/` changes)

| File | Responsibility |
|---|---|
| `voicebridge/office/theme.py` | Roots, contract validation, active-theme resolution, payload, token→CSS, contrast check, theme cache |
| `voicebridge/office/agent_panel.py` | Optional `portrait` per seat via an injected resolver |
| `voicebridge/qubit_compat.py` | `GET /api/office/theme` (+ `?theme=` override), read authority, panel call |
| `scripts/office_theme_build.py` | Portrait composition + QA + manifest (deterministic) |
| `scripts/office_theme_extract.py` | Read the owner’s zip in memory → portrait layers (48px) + optional slices + contact sheet |
| `frontend/qubit-web/public/plugins/qubit-office/themes/limezu/…` | Generated, committed pack |
| `frontend/qubit-app/plugins/qubit-office/game/app/office-theme.ts` | Fetch theme, build CSS, apply host class/link, inject iframe style (append-only) |
| `AgentsPanel.tsx` / `agents-panel.ts` | Portrait rendering with emoji fallback |
| `OfficeAppShell.tsx` / `office-menu.ts` | Apply theme classes; dispose cleanly |
| `voicebridge/office/theme_cli.py` + `cli.py` | `qubit office theme list/validate/install/export` |
| `tests/test_office_theme*.py`, `tests/test_office_visual_regression.py` | Coverage |
| `docs/OFFICE-UI-THEMES.md`, `LICENSE-ASSETS`, `docs/ATTRIBUTION-OFFICE.md` | Format + creator guide + licence records |
| `THIRD_PARTY_NOTICES.md`, `docs/DECISIONS.md` | Provenance + owner rulings (14, 15) |

---

# Tasks

### Task 1: Theme contract, loader, validator, token CSS

**Files:** create `voicebridge/office/theme.py`; test `tests/test_office_theme.py`.

**Interfaces:** `THEME_FORMAT`, `THEME_SCHEMA = 1`, `ThemeError`, `Theme(...)`, `themes_roots()`, `load_theme(id, roots=None)`, `validate_theme(root)`, `active_theme_id(cfg, requested=None)`, `theme_payload(cfg, requested=None)`, `token_css(tokens, slices, asset_base, version)`, `contrast_ratio(fg, bg)`, `ALLOWED_THEME_VARS`.

- [ ] **Step 1: failing tests** — a valid fixture loads; missing credit/license refused; traversal/reserved/suffix refused; hash/size mismatch refused; case-collision refused; unknown theme → `classic` + reason; env override wins; `?theme=` override works; token CSS contains only allowlisted custom properties and no `@import`/`url(http)`; contrast pair `< 4.5` returns a problem.

```python
def test_token_css_allows_only_the_allowlist():
    css = T.token_css({"color-accent": "#7cc8ff"}, {}, "/plugins/x", "1.0.0")
    assert "--ui-accent:" in css
    assert "display:" not in css and "position:" not in css and "pointer-events" not in css
    assert "@import" not in css and "url(http" not in css

def test_low_contrast_pair_is_reported():
    assert T.contrast_ratio("#777777", "#777777") < 4.5
    problems = T.validate_tokens({"color-text-primary": "#777777", "color-surface": "#777777"})
    assert any("contrast" in p for p in problems)
```

- [ ] **Step 2: run → FAIL.**
- [ ] **Step 3: implement** the contract, `_cfg_get` (config object or dict), roots (`$QUBIT_HOME/office/themes`, then `resource_path("frontend/qubit-web/public/plugins/qubit-office")/themes`, then `.../qubit-dist/...`), a cache keyed by `(root, theme.json mtime_ns, size)`, and the token→CSS generator mapping semantic tokens to the **allowlisted** `.ui-root` variables.
- [ ] **Step 4: run** `scripts/run_tests.sh tests/test_office_theme.py -q` → PASS.
- [ ] **Step 5: commit** `office(theme): fail-closed contract, loader, token CSS, contrast gate`.

---

### Task 2: Portrait composition core (deterministic + QA)

**Files:** create `scripts/office_theme_build.py`; test `tests/test_office_theme_build.py`.

**Interfaces:** `portrait_bytes(slug, layers, size) -> bytes`, `qa_portrait(png_bytes) -> list[str]`, `build_theme(source, out, *, size=48, personas=None, layers=None, ...) -> dict`, `ThemeBuildError`, `DEFAULT_LICENSE`, `DEFAULT_CREDIT`.

- [ ] **Step 1: failing tests** — same slug ⇒ identical decoded pixels; different slug ⇒ different pixels; QA rejects blank (<2% alpha) and over-full; manifest carries sha+bytes for every file; empty license/credit refuses.
- [ ] **Step 2: run → FAIL.**
- [ ] **Step 3: implement** a deterministic recipe (`sha256(slug)` selects skin/eyes/hair/accessory indices from a documented ordered layer table), compose at 48, resize with `Image.NEAREST` to {16,32,48}, QA gate, manifest writer sorted by key, two output trees.
- [ ] **Step 4: run tests.**
- [ ] **Step 5: commit**.

---

### Task 3: Source extraction + reproducible `--check`

**Files:** create `scripts/office_theme_extract.py`; extend the builder; test `tests/test_office_theme_extract.py`.

**Interfaces:** `extract_layers(source_zip, out_dir, size=48) -> dict[str, Path]`, `contact_sheet(sheet_png, out_png, grid=(16,16))`, `check_reproducible(pack_dir) -> list[str]`.

- [ ] **Step 1: failing tests** — reading `modernuserinterface-win.zip` in memory yields non-empty skin/eyes/hair/accessory layers for 48 px; a missing zip raises a typed error; `check_reproducible` returns differences when a committed portrait is replaced.
- [ ] **Step 2: run → FAIL.**
- [ ] **Step 3: implement** in-memory zip reads (`zipfile.ZipFile(io.BytesIO(...))`), the 48 px folder mapping, optional `--contact-sheet` output for authoring slices later, and `--check` comparing **decoded pixels** plus the sorted JSON manifest (documented: raw PNG bytes may differ across Pillow/zlib versions; the committed manifest sha still pins integrity).
- [ ] **Step 4: build the real pack** (`--source "/home/trick/Desktop/UI Office"`), then `--check`.
- [ ] **Step 5: commit** (builder, extractor, generated pack, tests).

---

### Task 4: Serve the theme and seat portraits

**Files:** `voicebridge/office/theme.py` (`portrait_rel`, cache), `agent_panel.py` (`panel(root=None, *, portrait=None)`), `qubit_compat.py` (`h_office_theme` + route + panel call), `webui.py` (`QUBIT_READ_AUTHORITY`); test `tests/test_office_theme_serve.py`.

**Interfaces:** `portrait_rel(cfg, slug) -> str | None` (membership from `Theme.files`, no per-request `stat` storm); `GET /api/office/theme?theme=<id>`; seat `"portrait"`.

- [ ] **Step 1: failing tests** — route registered/handler; classic payload `assets == {}`; `?theme=limezu` payload has bases/tokens but **no file map**; a seat gets `portrait` only when the manifest lists it; the panel header/counts are unchanged (283 seats).
- [ ] **Step 2: run → FAIL.**
- [ ] **Step 3: implement**; trim the payload to `{id,label,version,credit,license,assetBase,portraitsBase,tokens,slices}` with `?v=<version>` baked into the bases; `portrait_rel` uses the cached theme.
- [ ] **Step 4: run** `tests/test_office_theme_serve.py tests/test_office_agents_panel.py` → PASS.
- [ ] **Step 5: commit**.

---

### Task 5: Portraits in the Agents panel

**Files:** `agents-panel.ts`, `AgentsPanel.tsx`; test `agents-panel.test.ts`.

- [ ] **Step 1: failing tests** — no portrait ⇒ emoji, no `<img>`; portrait ⇒ `<img loading="lazy" decoding="async" alt="<name> portrait">`; a simulated `onError` swaps back to the emoji without dropping the row.
- [ ] **Step 2: run → FAIL.**
- [ ] **Step 3: implement** (`image-rendering: pixelated` on the class; no layout shift: fixed 32×32 box).
- [ ] **Step 4: run** `npx vitest run --config vitest.office-app.config.ts`.
- [ ] **Step 5: commit**.

---

### Task 6: Host theme application + allowlisted iframe override

**Files:** create `office-theme.ts`; modify `OfficeAppShell.tsx`, `office-menu.ts`; test `frontend/qubit-web/src/lib/office-theme.test.ts`.

**Interfaces:** `loadOfficeTheme(fetchImpl, base)`, `applyHostTheme(doc, theme) -> () => void`, `injectIframeTheme(win, css) -> () => void`, `themeCss(theme)`, `officeThemeClass(id)`.

- [ ] **Step 1: failing tests** (jsdom) — classic ⇒ no class/link/style; active ⇒ one link + `data-office-theme`, tokens present; idempotent; generated CSS contains only allowlisted variables; dispose removes the link/style; a parent rerender does not change the iframe `src` or remount the dock.
- [ ] **Step 2: run → FAIL.**
- [ ] **Step 3: implement** — one `no-store` fetch with `?v=`; apply host `<link>` + class; on iframe load inject **one** `<style data-office-theme>` containing only the allowlisted variable overrides scoped to `.ui-root`; dispose beside `menuRef`.
- [ ] **Step 4: run** office-app Vitest + `src/lib/office-theme.test.ts`.
- [ ] **Step 5: commit**.

---

### Task 7: Dock/menu chrome from tokens (and optional slices)

**Files:** `office-theme.ts`, `OfficeAppShell.tsx`, `office-menu.ts`; test `src/lib/office-theme.test.ts`.

- [ ] **Step 1: failing tests** — docks/buttons carry stable theme classes; token values appear as CSS custom properties; when `slices.panel` exists, the generated rule uses `border-image` with the declared slice and `border-image-width`; when it does not, no `border-image` rule is emitted; no rule touches layout/pointer/z-index.
- [ ] **Step 2: run → FAIL.**
- [ ] **Step 3: implement** token-driven styling on **our** elements only (docks, dock headers, the two injected buttons); optional 9-slice CSS generated from validated descriptors.
- [ ] **Step 4: run tests.**
- [ ] **Step 5: commit**.

---

### Task 8: `qubit office theme` CLI (list / validate / install / export)

**Files:** create `voicebridge/office/theme_cli.py`; extend the existing `office` parser in `voicebridge/cli.py:1322`; test `tests/test_office_theme_cli.py`.

- [ ] **Step 1: failing tests** — `validate` exits 2 and prints problems; `install` copies into a throwaway `QUBIT_HOME`, refuses overwrite without `--force`, refuses a zip with `..`/symlink/duplicate entry/oversize; `export` produces a zip that re-installs and re-validates.
- [ ] **Step 2: run → FAIL.**
- [ ] **Step 3: implement** bounded atomic install (plain 32-bit zip, declared sizes, temp dir → rename, readback validate), list (installed/bundled/active), export.
- [ ] **Step 4: run tests.**
- [ ] **Step 5: commit**.

---

### Task 9: Guards + visual goldens

**Files:** `tests/test_office_assets_vendored.py` (extend), new `tests/test_office_visual_regression.py`, `tests/fixtures/office_visual/*`.

- [ ] **Step 1: failing guards**
  - no `.zip`/`.exe` under the office public tree;
  - every committed theme validates;
  - **Habblaud unchanged:** a test runs `scripts/vendor_habblaud.py --check` and asserts exit 0 (skip with a clear reason when the tree is absent in a clean clone; it is tracked, so it must run in this repo);
  - theme active ⇒ panel payload still has 283 seats and `h_office_theme` never exposes the file map;
  - `LICENSE-ASSETS` exists and the active payload carries the credit (behaviour, not source text).
- [ ] **Step 2: run → FAIL.**
- [ ] **Step 3: implement the visual probe.** Opt-in `QUBIT_OFFICE_VISUAL=1`; loopback `WebUI`; stub `/agent/api/office/tower`, `/api/stream`, and the agents panel with fixed JSON; Playwright at `deviceScaleFactor` 1 and 2; `animations: "disabled"`, `caret: "hide"`, fixed clock; **element** screenshots of the Agents panel and the menu group compared with `maxDiffPixels` for antialiasing. Baselines are committed and updated only after a reviewed diff (documented in the task).
- [ ] **Step 4: run the guards + probe.** Include `python3 scripts/vendor_habblaud.py --check`.
- [ ] **Step 5: commit**.

---

### Task 10: Share docs, licence records, upgrade playbook

**Files:** `docs/OFFICE-UI-THEMES.md`, `LICENSE-ASSETS`, `docs/ATTRIBUTION-OFFICE.md`, `THIRD_PARTY_NOTICES.md` (add `modernuserinterface-win.zip` + `Portrait_Generator_1.5.0`), `docs/STATUS.md`.

- [ ] **Step 1: write `LICENSE-ASSETS`** — MIT code vs LimeZu art; credit link; no resale/redistribution; no NFT; Serene Village TASL + changes.
- [ ] **Step 2: write `docs/OFFICE-UI-THEMES.md`** — the contract, install paths, CLI, how to create a pack (extractor + builder + contact sheet), how to share (zip + sha + install), the allowlisted variables, and the **upgrade playbook** (add-only tokens; loader refuses newer schema; v2 migration steps; never rename tokens).
- [ ] **Step 3: `docs/ATTRIBUTION-OFFICE.md`** — TASL for Serene Village, changes note, and the exact files used.
- [ ] **Step 4: update `THIRD_PARTY_NOTICES.md` + `docs/STATUS.md`** with this session’s evidence.
- [ ] **Step 5: commit**.

---

## Invariants and verification matrix

| Check | Command | Expected |
|---|---|---|
| Theme unit | `scripts/run_tests.sh tests/test_office_theme.py tests/test_office_theme_build.py tests/test_office_theme_extract.py tests/test_office_theme_serve.py tests/test_office_theme_cli.py -q` | all pass |
| Agents panel | `scripts/run_tests.sh tests/test_office_agents_panel.py -q` | 13 pass |
| Guards | `scripts/run_tests.sh tests/test_office_assets_vendored.py -q` | pass; no raw packs; all themes valid |
| **Habblaud untouched** | `python3 scripts/vendor_habblaud.py --check` | exit 0, tree identical |
| Office app Vitest | `cd frontend/qubit-web && npx vitest run --config vitest.office-app.config.ts` | all pass |
| Theme DOM | `npx vitest run src/lib/office-theme.test.ts` | pass |
| Visual goldens | `QUBIT_OFFICE_VISUAL=1 scripts/run_tests.sh tests/test_office_visual_regression.py -q` | pass at DPR 1 and 2 |
| Build | `npx vite build --config vite.office-game.config.ts` | succeeds |
| Reproducibility | `python3 scripts/office_theme_extract.py --check` | decoded pixels + JSON identical |
| Live classic | `/qubit-office`, no theme | identical look, 0 new requests, HUD marker survives 15 s |
| Live themed | `office.theme: limezu` / `?theme=limezu` | portraits show, credit visible, menu order unchanged, game interactive |
| Rollback | `QUBIT_OFFICE_THEME=classic` | immediate classic look, no rebuild |

## Rollback

- Config: `office.theme: classic` or `QUBIT_OFFICE_THEME=classic` (no rebuild, no restart of assets).
- Files: remove the theme directory from `$QUBIT_HOME/office/themes` or the committed pack; the shell falls back to `classic`.
- Habblaud is never modified, so there is nothing to revert there.

## Future upgrade playbook (this is the “easier to upgrade later” part)

1. **Add, never rename.** New tokens/variables are additive; old packs keep working.
2. **Schema gate.** The loader supports `schema: 1` only; a newer pack is refused with a typed reason. Bump to 2 only with a migration note and a `migrate` step in the CLI.
3. **New art** → run the extractor with a new `--source`, author optional slices from the contact sheet, rebuild, run `--check`.
4. **New surfaces** (e.g. a future panel) consume tokens from `loadOfficeTheme`; no new fetch, no new theme file format.
5. **Sharing** → zip the pack, publish the sha, `qubit office theme install`.

## Self-review (completed)

- **Owner constraint:** no Habblaud edits anywhere; a hash check is a gate; room art explicitly deferred.
- **Spec coverage:** licence → Tasks 3, 9, 10; contract/safety → Task 1; portraits → Tasks 2, 3, 5; chrome → Tasks 6, 7; sharing → Task 8, 10; future upgrades → playbook.
- **No placeholders:** every step has real code or a runnable command; extraction and `--check` contracts are explicit.
- **Type consistency:** `Theme.tokens/slices` used by Tasks 1/4/6/7; `portrait` is `string | null`; the disposer from `injectIframeTheme` is used beside `menuRef`.
- **Test honesty:** behaviour + artifacts, no `.py`/`.ts` source-shape assertions.

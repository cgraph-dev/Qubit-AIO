# Qubit Office UI Theme Upgrade — Implementation Plan (v4, internet-verified)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Qubit Office look better with the owner-purchased LimeZu art, and make every future UI change easy to add and share — **without touching Habblaud**.

**Architecture:** A theme is a declarative, data-only folder: `theme.json` + one portrait **atlas** + a JSON index + role tokens (+ optional 9-slice descriptors). Qubit’s own shell consumes it (host docks, injected menu icons), and the **server** injects one generated `<style>` into the office app document that overrides a documented allowlist of Habblaud’s own `.ui-root` variables. No client-side CSS injection, no pack CSS, no Habblaud edits. `office.theme` defaults to `classic`.

**Tech Stack:** Python 3.14 + Pillow (build/validate/serve), TypeScript/React 19 (host-provided React), Vitest (jsdom), pytest, Playwright (Python), the existing `/plugins/` static route and `_serve_app_html` document injector.

**Spec:** `docs/DECISIONS.md` rulings 14 and 15; `THIRD_PARTY_NOTICES.md` §"LimeZu — purchased office art"; the 2026-11-09 asset audit of `/home/trick/Desktop/UI Office`.

---

## Owner constraint

> “dont fuck habblaud we just update ui to make it better and easier to upgrade in the future”

- `frontend/qubit-app/plugins/qubit-office/habblaud/` is **read-only**: no edits, no rebuild, no re-vendor for UI.
- The only runtime interaction is a **server-injected `<style>`** (document head, after the app’s own stylesheet) that sets an allowlisted subset of `.ui-root` variables, plus class hooks/DOM on **elements we already inject** (the two menu buttons).
- The game stays fully interactive and unchanged in behavior; the theme is off by default and reversible by config.

## Non-goals

- No changes to the office engine, simulation, room layout, canvas art, stream, tools, writes, or approvals.
- **Do not repurpose Habblaud’s own `theme` field** (`none|halloween|christmas`, upstream-owned seasonal art — verified in `docs/world/privacidade.md` and the world payload). Room art is deferred and needs a new owner decision.
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

---

## Global constraints

- **`classic` default.** `office.theme` unset ⇒ today’s look. `QUBIT_OFFICE_THEME` = process override; `?theme=` = one-page read-only override.
- **Habblaud read-only.** `python3 scripts/vendor_habblaud.py --check` must stay green; no file under `habblaud/` changes.
- **Packs are data.** `.png`, `.jpg`, `.jpeg`, `.webp`, `.json`, `.txt` only; no CSS/JS/HTML/symlinks/remote URLs.
- **Credits required.** Pack carries `credit` + `license`; the office surfaces the credit when a theme is active.
- **No raw archives in the tree.** Derived files only, each sha256 + byte length.
- **Owner decision O1/C4:** no celebrity/meme sprite families.
- **No new polling/remounts.** Docks keep stable identity (`c5bd8a69b`).
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
  "portraits": {
    "atlas": "portraits/atlas32.png",
    "index": "portraits/index.json",
    "size": 32, "cols": 20, "rows": 15
  },
  "slices": {},
  "files": {
    "portraits/atlas32.png": { "sha256": "<64 hex>", "bytes": 48123 },
    "portraits/index.json":  { "sha256": "<64 hex>", "bytes": 9123 }
  },
  "limits": { "max_total_bytes": 25000000, "max_files": 16 }
}
```

`index.json`: `{"schema":1,"size":32,"cols":20,"cells":{"ceo":[0,0],"lead-quality":[1,0]}}`.

Validation:
- exact format/schema; id `^[a-z0-9][a-z0-9-]{0,39}$`; non-empty credit/license.
- files: relative, `[A-Za-z0-9._/-]`, no `.`/`..`, no Windows reserved basename, allowlisted suffix, no symlink, exists, `bytes`/`sha256` match; case-insensitive duplicates refused; totals within limits.
- tokens: key `^[a-z][a-z0-9-]{1,40}$`; **typed values only** — colors `^#[0-9a-fA-F]{3}([0-9a-fA-F]{3})?$`, lengths `^(0|[1-9][0-9]{0,3})(px|rem|em)$`; reject `url(`, `image-set(`, `src(`, `var(`, `attr(`, `@`, `\`, `;`, `}` anywhere.
- portraits: atlas/index declared; size ∈ {16,32,48}; index schema matches; every slug cell inside `cols×rows`.
- slices (optional): 4 positive ints, `width` px, `repeat ∈ stretch|round|repeat`, `src` declared.
- contrast: `color-text-primary` vs `color-surface` ≥ 4.5; `color-text-muted` vs `color-surface` ≥ 3.0.

**Allowlisted Habblaud variables** (the only ones a theme may set):
`--ui-bg`, `--ui-bg-soft`, `--ui-bg-solid`, `--ui-bg-hover`, `--ui-line`, `--ui-line-strong`, `--ui-text`, `--ui-muted`, `--ui-faint`, `--ui-accent`, `--ui-accent-soft`, `--radius`, `--radius-sm`, `--st-working`, `--st-waiting`, `--st-done`, `--st-idle`, `--st-offline`, `--st-shell`, `--lv-ok`, `--lv-warn`, `--lv-crit`.
Not themed: `--side-w`, `--drawer-w`, `--top-h`, `--gap`, `--free-*`, `--font-*`, `--ease`.

---

## File structure (nothing under `habblaud/` changes)

| File | Responsibility |
|---|---|
| `voicebridge/office/theme.py` | Roots, contract, active theme, payload, token CSS, contrast, cache, per-token value validation |
| `voicebridge/office/agent_panel.py` | Optional `portrait` field (atlas cell) via injected resolver |
| `voicebridge/webui.py` | `_serve_app_html`: inject the generated office theme `<style>`; read authority for `/api/office/theme` |
| `voicebridge/qubit_compat.py` | `GET /api/office/theme` (+ `?theme=`), panel call |
| `scripts/office_theme_build.py` | Atlas + index + manifest + QA (deterministic) |
| `scripts/office_theme_extract.py` | Read owner packs in memory → portrait layers (32/48) + contact sheet + `--check` |
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

### Task 2: Atlas builder (deterministic, QA-gated)

**Files:** create `scripts/office_theme_build.py`; test `tests/test_office_theme_build.py`.

- [ ] **Step 1: failing tests** — same slug ⇒ same decoded pixels; different slug ⇒ different; atlas dimensions = `cols×rows×size`; index has every slug in bounds; QA rejects blank/overfull tiles; empty license/credit refuses; manifest has sha+bytes for every file.
- [ ] **Step 2: run → FAIL.**
- [ ] **Step 3: implement** deterministic recipes, compose at 48, downscale `Image.NEAREST` to 32 for the atlas, QA each tile (alpha 2–98 %, non-empty bounds), pack into one RGBA PNG, write `index.json`, manifest sorted by key.
- [ ] **Step 4: run tests.**
- [ ] **Step 5: commit**.

---

### Task 3: Extraction + reproducible `--check`

**Files:** create `scripts/office_theme_extract.py`; test `tests/test_office_theme_extract.py`.

- [ ] **Step 1: failing tests** — in-memory read of `modernuserinterface-win.zip` yields non-empty skin/eyes/hair/accessory layers; missing zip → typed error; `--check` returns differences when a committed tile changes.
- [ ] **Step 2: run → FAIL.**
- [ ] **Step 3: implement** in-memory zip reads, 48 px folder mapping, optional contact sheet for future slices, `--check` comparing **decoded pixels** (atlas + individual tiles) and sorted JSON (raw PNG bytes may differ across Pillow/zlib; the manifest sha pins committed bytes).
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

- [ ] **Step 1: failing tests** — a seat’s cell renders a 32×32 sprite span with `role="img"` + `aria-label`; the emoji remains in the DOM as fallback until the sheet is ready; a broken sheet leaves emojis visible (preload failure path); no per-seat `<img>` requests.
- [ ] **Step 2: run → FAIL.**
- [ ] **Step 3: implement** — preload `atlas` once, add `office-portraits-ready` to the panel root on success, position via `background-position: calc(-1 * var(--cell-x) * 32px) calc(-1 * var(--cell-y) * 32px)`, `image-rendering: pixelated`, fixed 32×32 box (no layout shift).
- [ ] **Step 4: run** office-app Vitest.
- [ ] **Step 5: commit**.

---

### Task 6: Server-injected iframe theme + host theme

**Files:** `voicebridge/webui.py` (`_serve_app_html`), create `office-theme.ts`, modify `OfficeAppShell.tsx`, `office-menu.ts`; tests `tests/test_office_framing_policy.py`, `src/lib/office-theme.test.ts`.

**Interfaces:** `theme_style_html(cfg, requested=None) -> str` (empty for classic/other apps), `loadOfficeTheme`, `applyHostTheme`, `themeCss`, `officeThemeClass`.

- [ ] **Step 1: failing tests**
  - server: office app document contains exactly one `<style data-office-theme>` only when a theme is active; it appears **after** `</head>`’s stylesheet link; other plugin apps get none; the style contains only allowlisted vars and no `url(`; CSP remains `style-src 'self' 'unsafe-inline'`;
  - host: classic ⇒ no class/link/fetch; active ⇒ one link + `data-office-theme`; idempotent; dispose removes; a parent rerender does not change the iframe `src` or remount the dock;
  - menu: if `.ui-btn-group[aria-label="Panels"]` is missing, the fallback icon buttons render in the host so the feature never disappears after an upstream change.
- [ ] **Step 2: run → FAIL.**
- [ ] **Step 3: implement** server injection (office app only, after the app stylesheet, `html body .ui-root`), host loader (one `no-store` fetch, `?v=`), menu fallback, dispose.
- [ ] **Step 4: run** tests.
- [ ] **Step 5: commit**.

---

### Task 7: Dock/menu chrome from tokens (optional slices)

**Files:** `office-theme.ts`, `OfficeAppShell.tsx`, `office-menu.ts`; test `src/lib/office-theme.test.ts`.

- [ ] **Step 1: failing tests** — docks/buttons carry stable theme classes; tokens appear as CSS vars; a declared slice emits `border-image` with `border-image-width` equal to the CSS border; no slice ⇒ no `border-image`; no rule touches layout/pointer/z-index.
- [ ] **Step 2: run → FAIL.**
- [ ] **Step 3: implement** token styles for our elements only; optional validated 9-slice CSS.
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
  - `LICENSE-ASSETS` exists and the payload carries the credit.
- [ ] **Step 2: run → FAIL.**
- [ ] **Step 3: implement the visual probe.** `QUBIT_OFFICE_VISUAL=1`; loopback `WebUI`; stub tower/stream/panel; Playwright at DPR 1 and 2; animations off, caret hidden, clock fixed; element screenshots of the Agents panel and the menu group; baselines committed and updated only after review.
- [ ] **Step 4: run guards + probe + `vendor_habblaud.py --check`.**
- [ ] **Step 5: commit**.

---

### Task 10: Share docs, licence records, upgrade playbook

**Files:** `docs/OFFICE-UI-THEMES.md`, `LICENSE-ASSETS`, `docs/ATTRIBUTION-OFFICE.md`, `THIRD_PARTY_NOTICES.md`, `docs/STATUS.md`.

- [ ] **Step 1: `LICENSE-ASSETS`** — MIT code vs LimeZu art; credit link; no resale/redistribution; no NFT; CC-BY TASL + changes for Serene Village.
- [ ] **Step 2: `docs/OFFICE-UI-THEMES.md`** — contract, install paths, CLI, pack creation (extractor/build/contact sheet), sharing, allowlisted variables, upgrade playbook.
- [ ] **Step 3: `docs/ATTRIBUTION-OFFICE.md`** — TASL, changes, exact files.
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
6. **Vendor upgrade:** if Habblaud is ever updated for security/upstream, re-run the menu-group selector test and the theme goldens; the fallback buttons cover a changed selector.

## Self-review

- **Owner constraint:** zero Habblaud edits; `vendor_habblaud.py --check` is a gate; room art deferred; the upstream `theme` field is not repurposed.
- **Security:** typed tokens (CSS-exfil vectors rejected), no pack CSS, server-side injection, CSP contract test.
- **Performance:** one atlas request, native 32 px, no lazy-flood; palette/contrast validated.
- **Test honesty:** behaviour + artifacts only.
- **Type consistency:** `Theme.tokens/slices/portraits` used across Tasks 1/4/5/6/7; `portrait` is `{x,y} | null`; server style and host link use the same validated payload.

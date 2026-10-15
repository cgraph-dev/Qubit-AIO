# Habblaud → Qubit Office — rebrand surface inventory and plan

Audit date: 2026-10-14. Baseline commit `c4ed3414b` (worktree dirty; see §7).
Scope: `frontend/qubit-app/plugins/qubit-office/habblaud/` (vendored build output)
and the source checkout at `/tmp/habblaud-full` (pinned `4ccce2537472fe5a8d432f06974494d2e778ff15`, v0.11.0).

This is **reconnaissance with numbers, not a rebrand**. Nothing in the vendored
tree or the source checkout was edited to produce this document.

Companion docs: `HABBLAUD-PROVENANCE.md` (licence position),
`HABBLAUD-IFRAME-ISOLATION.md` (storage/globals), `scripts/vendor_habblaud.py`
(the generator).

---

## 1. What is actually in the tree

### 1.1 Size

```
$ find <vendored> -type f | wc -l
78
$ du -sb <vendored>
2160740	/home/trick/Projects/Repos/qubit-voicebridge/frontend/qubit-app/plugins/qubit-office/habblaud
$ du -sh <vendored>
2.3M	/home/trick/Projects/Repos/qubit-voicebridge/frontend/qubit-app/plugins/qubit-office/habblaud
$ du -sb <vendored>/assets <vendored>/bundle
194794	/home/trick/Projects/Repos/qubit-voicebridge/frontend/qubit-app/plugins/qubit-office/habblaud/assets
1947704	/home/trick/Projects/Repos/qubit-voicebridge/frontend/qubit-app/plugins/qubit-office/habblaud/bundle
```

File count is **78**, not 77: `PROVENANCE.json` excludes itself from its own
`files` list (see `vendor_habblaud.py:170`), so 78 on disk = 77 recorded + 1
manifest. `PROVENANCE.json` records `total_bytes: 2147057` over those 77 files;
the extra 13,683 bytes to the 2,160,740 tree total is `PROVENANCE.json` itself.

### 1.2 Extension split (bytes, whole tree)

```
ext	count	bytes
.js	7	1658521
.png	40	189279
.css	2	132459
.woff	12	90128
.woff2	12	66596
.json	2	19198
.html	1	2832
(none)	1	1071
.webmanifest	1	656
TOTAL	78	2160740
```

### 1.3 The largest files

```
698086  bundle/app-U4Pwh2XA.js            <- the app bundle
334656  bundle/ptypane-DxPqTDKo.js        <- xterm.js (third-party, no brand)
203518  bundle/fr-Bhd3CCOg.js              <- locale chunk FR
195336  bundle/es-BlnHV64J.js              <- locale chunk ES
186018  bundle/en-2E7tqGEw.js              <- locale chunk EN
128520  bundle/app-BW61bUik.css            <- stylesheet (+ inlined font-face)
 83603  assets/illustrations/empty-office.png
 76799  assets/og-image.png                <- brand text baked into the raster
 36986  bundle/daystats-CUw1NUBG.js
 13683  PROVENANCE.json
```

The eight `pixelify-sans-*.woff{,2}` families (24 files, 156,724 bytes total)
are **vendored webfonts**, not brand surface.

### 1.4 The three "byte classes" that matter for a rebrand

| Class | Bytes | Rebrandable? |
|---|---|---|
| Minified JS bundles | 1,658,521 | Text strings + identifiers yes; **filenames are content hashes** |
| CSS (font URL rewrites) | 132,459 | Text yes; filename is a hash |
| PNG art/brand | 189,279 | **Binary** — text edits impossible. `signage.png`, `signage@4x.png`, `og-image.png` have "Habblaud" baked into pixels |
| Fonts + JSON + HTML + manifest | ~180 KB | text/`.woff` untouched |

---

## 2. Brand occurrence counts (per file, split by category)

All counts from a byte-level regex walk of the tree. Three tokens measured:
`Habblaud` (user-visible name), `habblaud` (identifiers/paths/keys),
`HABBLAUD_` (env var names).

| File | Kind | `Habblaud` | `habblaud` | `HABBLAUD_` | total |
|---|---|---|---|---|---|
| `bundle/en-2E7tqGEw.js` | locale EN | 244 | 40 | 120 | **404** |
| `bundle/es-BlnHV64J.js` | locale ES | 232 | 40 | 120 | **392** |
| `bundle/fr-Bhd3CCOg.js` | locale FR | 232 | 40 | 120 | **392** |
| `bundle/app-U4Pwh2XA.js` | app bundle | 53 | 19 | 5 | **77** |
| `bundle/app-BW61bUik.css` | stylesheet | 0 | 24 | 0 | **24** |
| `bundle/main-DND_b4_E.js` | boot module | 0 | 1 | 2 | **3** |
| `bundle/daystats-CUw1NUBG.js` | day stats | 2 | 0 | 0 | **2** |
| `index.html` | entry HTML | 6 | 7 | 0 | **13** |
| `manifest.webmanifest` | PWA manifest | 2 | 0 | 0 | **2** |
| `PROVENANCE.json` | provenance | 1 | 3 | 0 | **4** |
| **Total** | | **772** | **172** | **367** | **1313** |

`bundle/ptypane-DxPqTDKo.js` (xterm.js) contains **zero** brand tokens — it is
pure third-party. `bundle/ptypane-BrP-ENHg.css` likewise.

### 2.1 Split by category

**(a) User-visible strings — 772 `Habblaud` + 186 `HABBLAUD_` in locale values**

Localised strings live in the three locale chunks. Each chunk translates a
2,042-entry pt-BR dictionary:

```
en: total entries = 2042; entries mentioning brand = 164; brand tokens in VALUES = 198
es: total entries = 2042; entries mentioning brand = 152; brand tokens in VALUES = 186
fr: total entries = 2042; entries mentioning brand = 152; brand tokens in VALUES = 186
```

The pt-BR **base** strings are NOT in a locale file. They are inline in the app
bundle as `tr('…')` call sites: `app-U4Pwh2XA.js` holds **59 unique pt-BR string
literals carrying the brand** (e.g. `'Aprovado no Habblaud'`,
`'O servidor do Habblaud não responde…'`, `'Habblaud — precisa de você'`).
`client/src/app.ts:18` and `shared/locales/*.ts` confirm the pattern: the pt-BR
sentence is the lookup KEY, and each locale file maps it.

**(b) Identifiers — 172 `habblaud` + the `HABBLAUD_` env set**

| Kind | Count | Examples |
|---|---|---|
| localStorage keys | 10 | `habblaud:prefs`, `habblaud:locale`, `habblaud:update-seen`, `habblaud.wallets.v1`, `habblaud.deliveries.seen`, `habblaud.tabs.order`, `habblaud.rooms.order`, `habblaud.spawn.folder`, `habblaud.move.drawer`, `habblaud.move.term` |
| legacy-migration prefix | 1 | `var JS='codetown', YS=['codetown:','codetown.'], XS='habblaud'` — the CodeTown→Habblaud key migration in `app-U4Pwh2XA.js`. **This is a deliberate legacy alias, not the brand.** |
| window global | 1 | `Object.assign(window, {habblaud:{store:e, world:n}})` in `app-U4Pwh2XA.js` |
| env var names | 46 distinct | `HABBLAUD_PORT`, `HABBLAUD_LANG`, `HABBLAUD_TERMINAL`, `HABBLAUD_REMOTE_*`, `HABBLAUD_CLAUDE_DIRS`, `HABBLAUD_CODEX_*`, `HABBLAUD_TIMELINE`, `HABBLAUD_UPDATE_CHECK`, `HABBLAUD_USAGE_DIR`, `HABBLAUD_MENSAGENS`, `HABBLAUD_DICTATION_LANGS`, `HABBLAUD_WHISPER_*`, `HABBLAUD_FFMPEG_BIN`, `HABBLAUD_ATTACHMENTS_DIR`, `HABBLAUD_DATA_DIR`, `HABBLAUD_IN_DOCKER`, `HABBLAUD_HOOK_DEBUG`, `HABBLAUD_DEMO`, `HABBLAUD_ACCOUNTS`, `HABBLAUD_HOST`, `HABBLAUD_BIND`, `HABBLAUD_ALLOWED_HOSTS`, `HABBLAUD_PTY`, `HABBLAUD_CODEX_PERMISSION_TIMEOUT`, `HABBLAUD_PERMISSION_TIMEOUT` |
| upstream plugin package names | 4 | `habblaud-mensagens` (32×), `habblaud-permissoes` (7×), `habblaud-remote` (6×), `habblaud-data` (6×) |
| CSS class prefixes | 1 | `.habblaud` — 24 hits in `app-BW61bUik.css`, all of them **the plugin path inside `url(/plugin-apps/qubit-office/habblaud/…)` font references**, not a class namespace. The real CSS namespace is `ui-*` (~600 classes: `.ui-root`, `.ui-topbar`, `.ui-brand`, `.ui-day__*`). |

The app has exactly **one** `window.*` global — `window.habblaud`. There is no
`data-habblaud` attribute and no `habblaud:*` custom event beyond
`habblaud:permission-focus`.

**(c) URLs / paths — small**

- Upstream repo URL: `https://github.com/marmottajr/habblaud` — 1 occurrence
  in `PROVENANCE.json` (`"source"`). **Do not touch** (see §4).
- Served prefix: `/plugin-apps/qubit-office/habblaud` — the Qubit-owned path,
  appears in `index.html` (5×), `app-BW61bUik.css` (24×), `main-DND_b4_E.js`,
  `PROVENANCE.json`. Renaming this path means touching `vendor_habblaud.py`,
  `voicebridge/webui.py`, `game/app/office-app.ts`, `tests/*`.
- No locale chunk, cache key, or asset URL contains the brand other than the
  served prefix and the upstream repo URL.

---

## 3. The locale problem — a rebrand MUST be applied per locale

Locale chunks are per-language dictionaries; the app loads only the active one
(`vite.config.ts` aliases `./locales/all` → `locales/none.ts` in the browser).

| Chunk | Language | Bytes | Entries | Entries w/ brand | Brand tokens in values |
|---|---|---|---|---|---|
| `bundle/en-2E7tqGEw.js` | English | 186,018 | 2,042 | 164 | 198 |
| `bundle/es-BlnHV64J.js` | Spanish | 195,336 | 2,042 | 152 | 186 |
| `bundle/fr-Bhd3CCOg.js` | French | 203,518 | 2,042 | 152 | 186 |
| (pt-BR base) | Portuguese | — | — | — | inline in `app-U4Pwh2XA.js` (59 unique strings) |

**Same string, four places.** Verified in the head of each chunk — the pt-BR key
is identical, only the value differs:

```
"Sem conexão com o Habblaud." :  en="No connection to Habblaud."
                                 es="Sin conexión con Habblaud."
                                 fr="Pas de connexion avec Habblaud."
"Habblaud — precisa de você"  :  en="Habblaud — needs you"
                                 fr="Habblaud — on a besoin de vous"
"🏢 Habblaud {0}{1}{2} em http://{3}:{4}" : en="🏢 Habblaud {0}{1}{2} at …"
```

Two consequences a rebrand must respect:

1. **The pt-BR base is not a locale file.** Changing `'Aprovado no Habblaud'` in
   the app bundle without changing the corresponding KEY in `en/es/fr.ts` breaks
   every translation lookup — the value falls back to the pt-BR string. The
   rebrand must rename base string AND all three locale keys in lockstep.
2. **`i18n:check` exists** (`scripts/i18n-check.ts`, `npm run i18n:check`) — it
   validates that every key is translated. Run it after any string change.

The locale chunks are **hash-named** (`en-2E7tqGEw.js`) and imported dynamically
from `main-DND_b4_E.js`; editing a locale chunk in place renames it, so the
import specifier in the boot module must be rewritten too.

---

## 4. What a rebrand CANNOT safely change — and the correct mechanism

### 4.1 Hash-named artifacts

Every file under `bundle/` carries a Vite content hash:

```
app-U4Pwh2XA.js  app-BW61bUik.css  main-DND_b4_E.js  daystats-CUw1NUBG.js
en-2E7tqGEw.js   es-BlnHV64J.js    fr-Bhd3CCOg.js    ptypane-DxPqTDKo.js  ptypane-BrP-ENHg.css
```

and the hash is referenced from `index.html`:

```
<script type="module" crossorigin src="/plugin-apps/qubit-office/habblaud/bundle/main-DND_b4_E.js"></script>
```

Cross-chunk imports also name hashes: `ptypane-DxPqTDKo.js` imports
`"./main-DND_b4_E.js"` and `"./app-U4Pwh2XA.js"`; the boot module dynamically
imports the locale chunk matching the active language.

**So an in-place `sed` of the built tree is not sustainable:**

- Change one byte of `app-U4Pwh2XA.js` → its content hash changes → its name
  should change → every importer of that name (the boot module, `ptypane`,
  the dynamic locale import map) must be rewritten → those files' hashes change
  → `index.html` must be rewritten → repeat transitively.
- `PROVENANCE.json` records a sha256 **and byte size** for all 77 files, and
  `tests/test_office_vendor_habblaud.py` asserts both
  (`test_recorded_files_exist_and_match_hashes`,
  `test_recorded_byte_sizes_agree_with_disk`,
  `test_total_bytes_matches_sum_of_recorded_files`). Any hand-edit of the tree
  turns the test suite red.
- `tests/test_office_vendor_habblaud.py` also asserts no file is present that
  `PROVENANCE.json` does not name, and that every `/bundle/…` and `/assets/…`
  reference in `index.html` resolves to a file that actually ships
  (`test_index_html_references_resolve_within_the_vendored_tree`). A rename
  without a matching reference update fails that test.

### 4.2 The correct mechanism

**The rebrand belongs in the SOURCE build step, not in the vendored tree.**

The chain is already in place:

```
/tmp/habblaud-full  (source checkout, pinned commit)
   └─ npm run build:client   (Node 22 + vite 8, node_modules present)
        └─ dist/client/
             └─ scripts/vendor_habblaud.py --src /tmp/habblaud-full
                  ├─ build_client()      runs the upstream vite build
                  ├─ rewrite_index()     /-absolute → plugin prefix
                  ├─ rewrite_css()       url(/bundle/…) → plugin prefix
                  ├─ copies LICENSE verbatim
                  └─ writes PROVENANCE.json (sha256 + bytes for every file)
                       └─ frontend/qubit-app/plugins/qubit-office/habblaud/
```

The rebrand is applied as **string transforms in the source** (or as a
post-build transform inside `vendor_habblaud.py` before hashing), then the whole
tree is regenerated and `PROVENANCE.json` is rewritten by the script. Hashes
change, `index.html` is regenerated by the script's rewrite step, the test suite
validates the new record, and `git diff` shows a clean regeneration rather than
a hand-patched artifact.

### 4.3 Recommendation

**Do the rebrand in the source layer at `/tmp/habblaud-full` and regenerate via
`scripts/vendor_habblaud.py`.** Specifically:

1. Rebrand the TypeScript source (`client/src/`, `shared/locales/`,
   `client/index.html`, `client/public/manifest.webmanifest`).
2. Add a small, documented addition to `vendor_habblaud.py` **only if** you want
   the transform to be reproducible without owning a patched checkout (e.g. a
   `--rebrand` flag applying the same string map before hashing). Otherwise keep
   `vendor_habblaud.py` verbatim and let the source patch carry the change.
3. Regenerate the tree: `scripts/vendor_habblaud.py --src /tmp/habblaud-full`.
4. Run `pytest tests/test_office_vendor_habblaud.py`.

**Why not in-place sed:**
- It produces a tree whose filenames no longer match their content hashes —
  a lie that compounds on the next regeneration (a future `--check` would flag
  every edited file as STALE, and a future `--skip-build` regenerate would
  silently revert the rebrand).
- It breaks the five integrity invariants the vendoring tests enforce.
- It cannot touch `PROVENANCE.json`'s contract (source commit, licence) without
  either lying about provenance or adopting a patch-ledger this repo does not
  have (`HABBLAUD-PROVENANCE.md` §8.4 explicitly flags that no per-file patch
  ledger exists).
- It is not reproducible: the next person to run the vendor script wipes it.

**Why not "just rebrand the manifest and index.html":** that covers 15 of
1,313 brand tokens (1.1%) and leaves the entire UI — every dialog, error,
permission prompt, update banner and locale string — saying "Habblaud".

---

## 5. What must NOT change (explicit do-not-touch list)

| # | Item | File | Context | Why |
|---|---|---|---|---|
| 1 | MIT copyright notice | `habblaud/LICENSE` (1071 B, sha256 `5de137c2…e9a`) | `MIT License\n\nCopyright (c) 2026 Márcio Junior` | MIT condition: "The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software." `vendor_habblaud.py:166-167` copies it verbatim, and `tests/test_office_vendor_habblaud.py` asserts it travels. |
| 2 | Upstream repo URL | `habblaud/PROVENANCE.json` → `"source": "https://github.com/marmottajr/habblaud"` | also `commit`, `licence: "MIT"` | Provenance contract; `tests/test_office_vendor_habblaud.py:181-195` asserts a github URL, `"habblaud"` in it, a full 40-hex commit, and `licence == "MIT"`. Editing these breaks both provenance and the test. |
| 3 | Upstream repo URL in source | `/tmp/habblaud-full/package.json` → `repository.url: git+https://github.com/marmottajr/habblaud.git`, `homepage`; `README*` | — | Same reason. |
| 4 | Anthropic/Claude disclaimer (EN) | `/tmp/habblaud-full/README.md:1091` | `Habblaud is an independent project, **not affiliated with, sponsored or endorsed by Anthropic**. "Claude" and "Claude Code" are trademarks of Anthropic, used here only to describe what the project works with.` | Trademark/attribution requirement. **Must stay, and must stay accurate.** |
| 5 | Anthropic/Claude disclaimer (pt-BR) | `/tmp/habblaud-full/README.pt-BR.md:1089` | `O Habblaud é um projeto independente, **não afiliado, patrocinado nem endossado pela Anthropic**. "Claude" e …` | Same. |
| 6 | "Claude Code" product references in UI copy | All four locales + app bundle: e.g. `"Escritório virtual em pixel art que mostra, em tempo real, o que seus agentes do Claude Code estão fazendo."` (used in `index.html` meta description/twitter, `client/src/app.ts:18`, all locale files) | — | Describes what the app works with; the disclaimer covers the trademark use. Rename the **product** ("Habblaud"), not "Claude Code". |

**Note on #4/#5:** the disclaimer text is in the READMEs *only*. It is **not**
in `client/src/` — `grep -rn 'Anthropic' client server shared scripts mod .claude-plugin`
returns nothing, and the disclaimer is absent from `index.html` and all bundles.
The shipped UI never shows it. If the rebrand removes or relocates the READMEs
(they are not vendored — see `HABBLAUD-PROVENANCE.md` §3), the disclaimer must be
**re-emitted somewhere that ships**, otherwise the trademark disclaimer is lost.

**Also preserve (third-party, not brand):**
- `bundle/ptypane-DxPqTDKo.js` + `ptypane-BrP-ENHg.css` — xterm.js. Contains no
  brand tokens; it is a bundled third-party dependency. There is currently **no
  per-dependency notice** in the vendored tree; only the upstream MIT `LICENSE`
  travels. Flagged in §8.
- `/Users/dev/projetos`, `/Users/voce`, `/Users/you`, `/Users/tu`, `/Users/vous`
  — upstream demo/i18n fixture constants pinned by
  `test_upstream_demo_path_is_the_only_users_reference`.

---

## 6. Ordered rebrand plan

Diff surface estimated in **files and occurrences**, not adjectives.

### Layer map

| Layer | Artifact | Files | Occurrences | Effort |
|---|---|---|---|---|
| L1 Source strings | `shared/locales/{en,es,fr}.ts` | 3 | 708 `Habblaud` + 120 `habblaud` + 360 `HABBLAUD_` | mechanical, then review |
| L2 Source strings (pt-BR base) | `client/src/**` `tr()` call sites | ~25 | 59 unique brand strings (in `app.ts`, `ui/*.ts`, `settings.ts`, `version.ts`, `help.ts`, `permission.ts`, …) | mechanical |
| L3 Source identifiers | `client/src/**` storage keys, `window.habblaud`, plugin names | ~14 | 10 storage keys + 1 global + 4 plugin names | mechanical **+ migration decision** |
| L4 Source brand assets | `client/public/assets/brand/*.png`, `assets/og-image.png`, `client/src/ui/icons.ts:333` `WORDMARK_ROWS` | 10 PNG + 1 TS | 2 PNG carry rasterised "Habblaud"; 1 TS holds a hand-drawn pixel wordmark | **artwork regeneration required** |
| L5 Source HTML/manifest | `client/index.html`, `client/public/manifest.webmanifest` | 2 | 6 + 7 in index, 2 in manifest | mechanical |
| L6 Env vars | `server/config.ts` + `shared/` + all `HABBLAUD_*` | ~30 source files | 46 distinct names, 882 total in source | **breaking; needs alias window** |
| L7 Build/regen | `scripts/vendor_habblaud.py` (+ optional `--rebrand` flag) | 1 | — | small |
| L8 Regenerated artifact | whole `habblaud/` tree + `PROVENANCE.json` | 78 | 1,313 (or 0 after rebrand) | **git diff only, never hand-edited** |

### Build-side vs client-side split

The **client (this vendored tree) only ships L1–L5**. L6 (env vars) and the
upstream `server/`/`mod/`/Docker surface are **NOT vendored** — only the built
client is. So the vendored rebrand is 4 JS chunks + 1 CSS + index.html +
manifest + ~10 PNGs. The env var names appear *inside* the shipped JS only as
**displayed text** (e.g. `'O terminal não está liberado no acesso remoto
(HABBLAUD_REMOTE_ALLOW).'`, `'… seguem HABBLAUD_LANG …'`), 120 per locale chunk
+ 5 in the app bundle = **365 displayed occurrences**. They are shown to the
user, so a UI rebrand must decide: keep the names (they refer to the real
upstream env vars and would be wrong to rename in display) or rebrand them too
(which requires the server side, not vendored). **Recommendation: L6 is out of
scope for this phase** — leave `HABBLAUD_*` names alone, or the UI will tell the
user to set an env var that does not exist.

### Order (dependency-correct)

1. **Unblock the licence gate first.** `HABBLAUD-PROVENANCE.md` §8 ends
   `BLOCKED — LICENSE REVIEW REQUIRED` for any rebrand of the vendored bundle
   until the §6 notice is recorded in `THIRD_PARTY_NOTICES.md` (currently absent
   — `grep -in habblaud THIRD_PARTY_NOTICES.md` is empty). **Nothing else should
   start before this**, and it is a human decision, not a code change.
2. Decide **L3 identity semantics**: do we keep the `habblaud:` storage prefix
   and `window.habblaud` (safe, zero migration) or rename them (needs a
   migration shim mirroring the existing `codetown`→`habblaud` migrate function
   at `app-U4Pwh2XA.js` `ZS()`)? The existing code already demonstrates the
   pattern: `var JS='codetown', YS=['codetown:','codetown.'], XS='habblaud'` —
   a rename can reuse it. **Recommendation: keep the storage keys and the
   `window.habblaud` global unchanged** (they are invisible to users and the
   isolation audit in `HABBLAUD-IFRAME-ISOLATION.md` §1 relies on them being
   disjoint from host keys).
3. **L4 brand artwork**: regenerate `signage.png`, `signage@4x.png`,
   `og-image.png` and the `WORDMARK_ROWS` pixel bitmap. This is the only step
   that cannot be a string replace — `signage@4x.png` (256×64) renders
   "Habblaud" in pixel type, and `og-image.png` (1200×630) has "Habblaud" plus
   `Seus agentes do Claude Code, ao vivo, num escritório em pixel art.` Upstream
   generates these with `scripts/assets/pixelart.py` + `specs.json`; that tool
   is **not vendored**.
4. **L5** (`index.html`, `manifest.webmanifest`) — 15 occurrences, string only.
5. **L1 + L2 in lockstep** — rename the pt-BR base strings AND the three locale
   keys together (708 `Habblaud` occurrences over 3 files + 59 unique base
   strings), then run `npm run i18n:check`. Doing one without the other
   silently falls back to pt-BR in every language.
6. **L7** — record the transform in `vendor_habblaud.py` (either as a source
   patch applied to `/tmp/habblaud-full`, or a documented `--rebrand` pass), so
   the change is reproducible.
7. **L8** — regenerate the tree, `pytest tests/test_office_vendor_habblaud.py`,
   commit the regenerated artifact + the updated `PROVENANCE.json`.
8. **Post-check**: visual — load `/plugin-apps/qubit-office/habblaud/`, switch
   language EN/ES/FR/pt-BR, confirm no mixed-language leak and no residual
   "Habblaud" in any dialog.

### Estimated diff surface

| Scope | Files changed | Occurrences changed |
|---|---|---|
| Minimal viable (L1+L2+L4+L5, strings + artwork) | ~45 (3 locale + ~25 TS + 2 config + ~11 assets + regen of 5 bundle files) | ~1,090 |
| Full rebrand incl. identifiers (L3) | ~60 | ~1,210 |
| Full incl. env vars (L6) | +~30 non-vendored source files | +882 |

**Not changed by any option:** `LICENSE` (1), `PROVENANCE.json` `source`/`commit`/
`licence` (3), the two README disclaimers (2), the xterm bundle (2 files, 0 brand
tokens), the 24 webfonts.

---

## 7. Baseline and reproducibility

```
$ git rev-parse --short HEAD
c4ed3414b
$ git status --porcelain | head
 M .gitignore
 M docs/OPEN_THREADS.md
 M docs/game-office/README.md
 M docs/game-office/frontend-game/60-IMPLEMENTATION-PLAN.md
 M docs/game-office/frontend-game/README.md
 M frontend/qubit-app/plugins/qubit-office/game/app/OfficeAppShell.tsx
 M frontend/qubit-app/plugins/qubit-office/game/app/office-api.ts
 M frontend/qubit-app/plugins/qubit-office/game/app/office-app.ts
 M frontend/qubit-app/plugins/qubit-office/game/entry.ts
 M frontend/qubit-app/plugins/qubit-office/habblaud/PROVENANCE.json
```

The worktree was already dirty at audit time, including `PROVENANCE.json`
(mtime 21:40). Measured sizes: on-disk 2,160,740 B; `PROVENANCE.json`'s
recorded total 2,147,057 B. The gap is exactly `PROVENANCE.json` itself
(13,683 B), consistent with the script's self-exclusion rule — but the dirty
`PROVENANCE.json` means the recorded hashes were **not re-verified against the
tree during this audit**. Re-run
`.venv/bin/python scripts/vendor_habblaud.py --src /tmp/habblaud-full --check`
before acting on the record.

---

## 8. What could not be measured

1. **Whether `signage.png` / `icon-192.png` / `icon-512.png` / `logo-mark*.png` / `logo-detailed.png` / `apple-touch-icon.png` / `favicon-*.png` contain glyphs spelling the brand.** Only `signage@4x.png` ("Habblaud"), `og-image.png` ("Habblaud" + a pt-BR tagline) and `illustrations/empty-office.png` (no text) were inspected visually. No OCR tool (`tesseract`) is installed, and the tiny icons (16×16–512×512) were not individually inspected. **The raster brand surface is therefore a lower bound at 2 confirmed images.**
2. **The exact set of pt-BR base strings needing a rename.** The 59 unique brand-carrying literals were extracted from backticks/single quotes in `app-U4Pwh2XA.js`; a minified bundle cannot distinguish a UI string from a non-UI literal by regex alone. A source-side pass over `client/src/**` `tr()` calls is the authoritative count and was **not** run (would require parsing TS).
3. **Cross-check that every source `HABBLAUD_*` name is unique/original.** `grep` found 46 distinct names in `client/src shared server scripts`; whether any are aliases of a differently-named upstream variable was not verified.
4. **Whether `dist/client/` on disk at `/tmp/habblaud-full` is byte-identical to the vendored tree after the path rewrites.** `diff` shows `index.html` differs only in the prefix (confirmed, §4.1 of `HABBLAUD-PROVENANCE.md`), but a full `diff -r` was not run in this audit.
5. **Total bytes of the vendored tree as recorded vs. as on disk** — reconciled arithmetically (§7) but not by re-running the hash check, because the worktree is dirty.
6. **Per-dependency licence notices inside the minified bundles.** xterm.js (MIT) is bundled at `bundle/ptypane-*` with no separately preserved notice; whether other third-party code is inlined into `app-U4Pwh2XA.js` was not determined.

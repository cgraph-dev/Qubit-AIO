# 40 — Asset pipeline

How art gets from `/refs` into the served plugin bundle, and why each step exists.

---

## 1. The shape

```
/refs/<repo>/...                        (staging — never served, never imported)
        │  vendored by a script, once
        ▼
frontend/qubit-app/plugins/qubit-office/assets/
        │  copied into the built dist by the existing build
        ▼
frontend/qubit-dist/plugins/qubit-office/   (served)
        │  <script src> / <img> by the plugin
        ▼
   /dashboard-plugins/qubit-office/<file>   (browser)
```

Rule **M5**: `/refs` is a staging area. Shipped code never references it. The reasons are
concrete, not stylistic:

1. `resolve_plugin_asset` (`voicebridge/qubit_compat.py:10965`) rejects any path that resolves
   outside the plugin's own directory — `PLUGIN_PATH_BLOCKED`. A `/refs` path would be blocked.
2. A served product must not depend on a scratch folder a user may delete.
3. `frontend/qubit-app/plugins/qubit-office/` is the plugin's own directory, already the
   served source of truth (`_serve_qubit_plugin`, `webui.py:1610`).

---

## 2. Vendoring script

**Create:** `scripts/vendor_office_assets.py`

- Reads from `/home/trick/Projects/Repos/refs` (path overridable with `--refs`).
- Writes to `frontend/qubit-app/plugins/qubit-office/assets/`.
- **Idempotent**: byte-identical output on re-run (same hashing discipline as
  `scripts/build_office_bundle.py`). `--check` exits non-zero if the vendored tree is stale.
- **Allowlist-driven**: it copies only the paths enumerated in `30-SOURCE-INVENTORY.md`, not
  whole folders. Copying `starnet/` wholesale would drag in 3.5 GB.

Selection to implement (exact, from the inventory):

```
starnet/frontend/assets/sprites/approved_*/  -> assets/sprites/approved_*/
starnet/frontend/assets/furniture/*.png      -> assets/furniture/
starnet/frontend/assets/fonts/vt323.woff2    -> assets/fonts/          (M10)
starnet/frontend/assets/sfx/*.wav            -> assets/sfx/            (M9, optional)
agent-quest/client/public/assets/themes/tiny-swords-cc0/Terrain/**        -> assets/tiles/terrain/
agent-quest/client/public/assets/themes/tiny-swords-cc0/BuildingsCustom/** -> assets/tiles/buildings/
agent-quest/client/public/assets/themes/tiny-swords-cc0/UI/{Buttons,Icons,Pointers,Ribbons,Banners}/** -> assets/ui/
```

---

## 3. Serving constraints the pipeline must respect

Read from the running code, not assumed (`60-WIRING-TRAPS.md` has the full list):

| fact | source | consequence |
|---|---|---|
| `.png .jpg .jpeg .gif .webp .ico .woff2 .woff .ttf .otf` are servable | `_PLUGIN_ASSET_CONTENT_TYPES`, `dashboard_ui.py:379` | sprite/furniture/tile/font assets serve **today** |
| **`.wav` is NOT in the dict** | same | the 12 UI sounds 404 unless one line is added (**M9**, open item) |
| plugin assets are served unauthenticated by suffix allowlist | same file, docstring | only allowlisted suffixes leave the box — do not add `.gd`, `.py` etc. |
| path is confined to the plugin dir | `qubit_compat.py:10965` | assets must live under the plugin directory |
| bundle is built by `build_office_bundle.py` | `scripts/build_office_bundle.py` | a new JS module must be added to its `ORDER` list to be concatenated |

---

## 4. Reproducibility rule

Vendored assets are **checked in** (the plugin is served from `qubit-app/plugins` and copied to
`qubit-dist`), not fetched at runtime. Consequence: the merge must be reproducible from a
clean checkout without network — a test asserts the vendored tree matches its manifest hashes,
so a manual edit to a staged PNG fails CI rather than silently diverging.

**Create:** `tests/test_office_assets_vendored.py`
- every path in `assets/MANIFEST.json` exists and hashes as recorded;
- no file under `assets/` is absent from the manifest (catches an unnoticed drop);
- the `.gitignore` does **not** exclude `assets/`.

---

## 5. Size budget

| set | files | approx size |
|---|---|---|
| starnet sprites | ~750 (50 sets × ~15 poses) | to be measured at vendoring |
| starnet furniture | 48 | to be measured |
| agent-quest tiles/UI | ~120 | to be measured |
| font | 1 | ~40 KB |
| sfx (optional) | 12 | to be measured |

**Not estimated here on purpose.** The inventory counts files; it does not invent byte sizes.
The vendoring script prints the real total, and **R7** gates on it: if the vendored tree
exceeds a threshold the owner sets, the pipeline drops `sfx` first, then unused UI frames.

**Intent:** the whole asset tree stays well under the plugin's existing bundle (188 KB) plus a
few MB of PNGs — acceptable for a local-first dashboard served over loopback.

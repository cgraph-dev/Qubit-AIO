# Qubit Office UI Theme Upgrade — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the owner-purchased LimeZu art as an opt-in, portable “Office theme pack” that restyles Qubit Office (agent portraits, menu/dock chrome, and later room sprites) without changing the default look and without breaking a single existing surface.

**Architecture:** A theme pack is a declarative, data-only directory (`theme.json` + derived PNGs + `portraits.json` + role tokens + 9-slice descriptors; **no CSS/JS inside a pack**). A reproducible build script slices the owner’s local source packs into a committed, hash-pinned theme; a Python loader validates and serves it; the Qubit-owned React shell renders tokens into CSS and consumes portraits first (low risk), then the vendored Habblaud document receives an append-only stylesheet built from those validated tokens and, in the final phase, an opt-in image-sprite provider. `office.theme` defaults to `classic`, so every phase is additive and reversible by config alone.

**Tech Stack:** Python 3.14 (`voicebridge`, stdlib `zipfile`, Pillow), TypeScript/React 19 (host-provided React in `qubit-app`), Vitest (jsdom for DOM tests), pytest, Playwright (Python sync API, already used by `tests/_probe_agents_panel_browser.py`), Vite (`vite.office-game.config.ts`), existing `scripts/vendor_office_assets.py` / `scripts/vendor_habblaud.py`.

**Spec:** `docs/DECISIONS.md` ruling 14, `THIRD_PARTY_NOTICES.md` §"LimeZu — purchased office art", and the asset audit from the 2026-11-09 session (source packs at `/home/trick/Desktop/UI Office`).

---

## Research (2026-11-09) — what “professional” looks like here

Findings that changed this plan. Each line names the source that supports it.

1. **License (verified against the artist’s own page and comments).** Commercial use is allowed; credit is required (“Credits required (this link)”); the only prohibition is reselling or redistributing the asset itself. A real project ships LimeZu tilesets with a separate `LICENSE-ASSETS` + `ATTRIBUTION.md`, keeps code under MIT, and credits on every surface — and deliberately draws its *office cast* procedurally instead of reusing LimeZu characters.
   Sources: `https://limezu.itch.io/moderninteriors`, `https://itch.io/post/2185336`,
   `https://github.com/chaitanyagiri/munder-difflin/blob/main/src/renderer/src/assets/ATTRIBUTION.md`,
   `https://github.com/chaitanyagiri/munder-difflin/blob/main/LICENSE-ASSETS`.
2. **CC-BY (Serene Village only) requires TASL** — Title, Author, Source, License — a license link, and an indication of changes.
   Sources: `https://creativecommons.org/licenses/by/4.0/deed.en`,
   `https://wiki.creativecommons.org/wiki/Recommended_practices_for_attribution`.
3. **Portable theme formats are declarative and fail-closed.** Production skin/theme pipelines ship a versioned manifest with per-file sha256 + byte length, basename-only paths, reserved-name checks, size caps, atomic staging, and machine-readable errors — and they explicitly refuse scripts, CSS, and remote resources inside a shared package.
   Sources: `https://deepwiki.com/fagemx/slot-asset-pipeline/6.1-atomic-theme-v1-package-format`,
   `https://denframe.com/docs/format`,
   `https://github.com/Fei-Away/Codex-Dream-Skin/issues/108`,
   `https://learn.microsoft.com/en-us/minecraft/creator/documents/packagingaskinpack`.
4. **Theming is role tokens, not brand colors or raw CSS.** Semantic tokens (`--color-surface`, `--color-text-primary`) scoped by a `data-theme` attribute, generated from one source of truth; ship behind a flag so rollback needs no redeploy.
   Sources: `https://appjet.ai/blog/theme-for-application`,
   `https://www.css-architecture.com/advanced-theming-dark-mode-implementation/runtime-theme-switching/`,
   `https://blog.michaelsam94.com/css-custom-properties-theming/`.
5. **9-slice chrome uses `border-image`** with slice values matching the authored border, `border-image-width` equal to the CSS border width, `stretch` as the safe default, `round` only for repeating motifs, and the panel background kept separate from the frame. Export at 2× for HiDPI.
   Sources: `https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Properties/border-image-slice`,
   `https://css-tricks.com/revisiting-css-border-image/`,
   `https://guide.coherent-labs.com/layout-assets-and-styling/graphics--shapes/advanced-ui-shapes/`,
   `https://felixrl.me/2026/04/28/pixelweb`.
6. **Pixel art must render at integer scales.** `image-rendering: pixelated` preserves edges, but non-integer `devicePixelRatio` still produces uneven pixels; draw sprites at integer multiples and test DPR 1 and 2.
   Sources: `https://developer.mozilla.org/en-US/docs/Games/Techniques/Crisp_pixel_art_look`,
   `https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Properties/image-rendering`.
7. **Visual regression is the gate for a UI upgrade.** Playwright `toHaveScreenshot`, element-level captures, `animations: 'disabled'`, `caret: 'hide'`, fixed clock, live data stubbed or masked, baselines generated in the same OS/browser as the run and committed; update baselines only when a human reviewed the diff.
   Sources: `https://playwright.dev/docs/test-snapshots`,
   `https://playwright.dev/docs/best-practices`,
   `https://diffy.website/blog/playwright-visual-regression-testing/`,
   `https://qaskills.sh/blog/playwright-screenshot-animation-caret-disable`.
8. **If a tileset ever goes in-engine, pack it as an atlas** with JSON metadata, padding + edge extrude to prevent bleeding, and consistent naming.
   Sources: `https://dev.to/code280fox/how-to-build-sprite-sheets-for-2d-games-a-practical-guide-for-indie-devs-46ib`,
   `https://quartzondev.itch.io/restrike`.

Consequences baked into the tasks below: packs contain **data, not code**; tokens drive CSS that *our* code generates; the licence record is a separate `LICENSE-ASSETS` file plus per-surface credit; visual goldens and integer-scale tests are part of the gate, not an afterthought.

---

## Global Constraints

- **Default is `classic`.** `office.theme` unset ⇒ today’s exact procedural look. `QUBIT_OFFICE_THEME=classic` is the runtime kill switch. `?theme=<id>` is a read-only dev override.
- **Packs are data, never code.** Allowed files: `.png`, `.jpg`, `.webp`, `.json`, `.txt`. No `.css`, `.js`, `.html`, `.svg` with scripts, symlinks, or remote references. The host generates CSS from validated tokens and 9-slice descriptors.
- **Credits required.** Any surface that ships LimeZu art shows `LimeZu — https://limezu.itch.io`; the pack must carry `credit` and `license` or the loader refuses it. Serene Village (CC-BY 4.0) additionally carries TASL and a changes note.
- **No raw packs in the tree.** Never commit `.zip`/`.exe`; only chosen, derived files with per-file sha256 + byte length.
- **Existing owner decision O1/C4 stands:** no celebrity/meme sprite families.
- **Never hand-edit vendored `habblaud/`.** Regenerate via `scripts/vendor_habblaud.py`; verify with `scripts/verify_habblaud_revendor.py`.
- **No new polling or remounts.** The HUD/Agents docks keep their current stable identity (commit `c5bd8a69b`); a theme switch may replace a `<link>`/`<style>` node, never a component type. A 15 s marker test guards this.
- **No runtime network calls.** Theme assets and tokens are local static files.
- **Pixel integrity:** avatar sizes are 16/32/48; `image-rendering: pixelated`; sprite/portrait scaling is integer-only; tests run at DPR 1 and 2.
- **English docs.** Update `docs/STATUS.md`; never claim `VERIFIED`.
- **Commits per task.** No service restart during implementation; focused tests only (repo cadence).
- **Tests:** `scripts/run_tests.sh tests/...` for Python, `cd frontend/qubit-web && npx vitest run …` for TS.

## Review Focus

1. **A malformed or unlicensed pack must never style the office.** Missing credit/license, hash/size mismatch, path traversal, reserved name, symlink, or any executable/remote content falls back to `classic` with a typed reason — never half-applied.
2. **A missing/broken portrait must not blank the Agents panel.** Each seat row falls back to its emoji and keeps name/type/room.
3. **The default user sees zero change.** With no theme configured, DOM, requests, and pixels of `/qubit-office` are identical to today (no extra fetch, no style node, no class).
4. **A theme switch must not resurrect the refresh loop.** Opening the HUD, switching theme, waiting 15 s keeps the same iframe `window` and issues no repeated panel fetches.
5. **Sharing a pack must not become an arbitrary-code path.** A pack is declarative only; install validates before publishing, rejects archive traversal/bombs, and installs atomically.

---

## Theme Pack Contract v1 (`qubit-office-theme/1`)

```json
{
  "format": "qubit-office-theme",
  "schema": 1,
  "id": "limezu",
  "label": "LimeZu Modern",
  "version": "1.0.0",
  "license": "LimeZu Modern Interiors / Modern UI — commercial and non-commercial use; redistribution prohibited; NFT minting prohibited; credit required",
  "credit": "LimeZu — https://limezu.itch.io",
  "source": "owner purchase 2026-11-09, /home/trick/Desktop/UI Office",
  "tokens": {
    "color-surface": "#0d0d1a",
    "color-surface-raised": "#161a23",
    "color-text-primary": "#e9edf7",
    "color-text-muted": "#8a93a8",
    "color-border": "#334155",
    "color-accent": "#7cc8ff",
    "radius-control": "4px",
    "space-control-x": "10px"
  },
  "slices": {
    "panel": { "src": "ui/panel.png", "slice": [8, 8, 8, 8], "width": "8px", "repeat": "stretch" },
    "button": { "src": "ui/button.png", "slice": [4, 4, 4, 4], "width": "4px", "repeat": "stretch" }
  },
  "portraits": { "dir": "portraits", "size": 48, "manifest": "portraits.json" },
  "files": {
    "ui/panel.png":   { "sha256": "<64 hex>", "bytes": 1234 },
    "portraits/ceo.png": { "sha256": "<64 hex>", "bytes": 1456 },
    "portraits.json": { "sha256": "<64 hex>", "bytes": 123 }
  },
  "limits": { "max_total_bytes": 25000000, "max_files": 400 }
}
```

Validation rules (enforced by `validate_theme`, refused by `load_theme`):
- `format`/`schema` exact; `id` matches `^[a-z0-9][a-z0-9-]{0,39}$`; required strings non-empty.
- Every `files` key: relative, no `.`/`..` segments, `[A-Za-z0-9._/-]` only, basename not a Windows reserved name, suffix in the allowlist, no symlink, exists, `bytes` matches, `sha256` (lowercase) matches.
- Totals within `limits`; `slices[*].src` must be a declared file; `slice` is 4 positive integers; `width` a positive px length; `repeat` ∈ `stretch|round|repeat`.
- `tokens` keys match `^[a-z][a-z0-9-]{1,40}$`; values are `#rgb`/`#rrggbb` or a bounded number/unit string; unknown keys are rejected.
- `portraits.dir` is `portraits`, `size` ∈ {16,32,48}, manifest declared.

Installation (shared packs) is bounded and atomic: plain 32-bit ZIP, ≤ `max_total_bytes`, no ZIP64/encryption/symlinks/absolute paths, declared uncompressed sizes checked, extract into a temp dir, validate, then rename into place; refuse to overwrite without `--force`.

---

## File Structure (Part A — foundation, ships first)

| File | Responsibility |
|---|---|
| `voicebridge/office/theme.py` | Roots, manifest dataclass, contract validation, active-theme resolution, payload, token→CSS generation |
| `voicebridge/office/agent_panel.py` | Optional `portrait` field per seat (injected resolver kept pure) |
| `voicebridge/qubit_compat.py` | `GET /api/office/theme` handler + route + read authority |
| `scripts/office_theme_build.py` | Reproducible slicer: personas → portraits (with QA gate), UI slices, manifest, two output trees |
| `frontend/qubit-web/public/plugins/qubit-office/themes/limezu/…` | Generated pack (committed) |
| `frontend/qubit-app/plugins/qubit-office/game/app/office-theme.ts` | Fetch theme, resolve asset URLs, generate+apply host CSS from tokens/slices |
| `frontend/qubit-app/plugins/qubit-office/game/app/AgentsPanel.tsx` | Render portraits with emoji fallback |
| `frontend/qubit-app/plugins/qubit-office/game/app/OfficeAppShell.tsx` | Inject generated theme CSS into the iframe (append-only) |
| `frontend/qubit-app/plugins/qubit-office/game/app/office-menu.ts` | Theme the injected menu buttons via stable classes |
| `frontend/qubit-web/src/lib/office-theme.test.ts` | DOM/Vitest coverage for loader + token CSS + injection |
| `voicebridge/office/theme_cli.py` + `voicebridge/cli.py` | `qubit office theme list/validate/install/export` |
| `tests/test_office_visual_regression.py` + `tests/fixtures/office_visual/*.png` | Playwright element goldens (opt-in env gate), DPR 1/2 |
| `docs/OFFICE-UI-THEMES.md`, `LICENSE-ASSETS`, `docs/ATTRIBUTION-OFFICE.md` | Format, creator guide, sharing, licence/credit records |
| `tests/test_office_theme.py`, `tests/test_office_theme_build.py`, `tests/test_office_theme_serve.py`, `tests/test_office_theme_cli.py` | Python coverage |
| `tests/test_office_assets_vendored.py` | No-raw-pack + every committed theme valid |

## File Structure (Part B — Habblaud sprite provider, separate worktree)

| File | Responsibility |
|---|---|
| `client/src/art/theme-provider.ts` (Habblaud source) | Load a padding/extrude atlas (JSON hash), expose `BufSprite` |
| `client/src/art/furniture/index.ts` (Habblaud source) | Consult the provider before the procedural builder |
| `client/src/art/theme-map.json` (Habblaud source) | `kind/variant → atlas frame + anchor`, integer-scale only |
| `frontend/qubit-app/plugins/qubit-office/habblaud/` | Re-vendored build (generated, hash-pinned) |
| `voicebridge/office/theme.py` | Injects `window.__QUBIT_OFFICE_THEME__` into the app document (non-classic only) |

---

# Part A — Theme foundation (no vendored edits)

### Task 1: Theme contract, loader, and validator

**Files:**
- Create: `voicebridge/office/theme.py`
- Test: `tests/test_office_theme.py`

**Interfaces:**
- Produces: `THEME_FORMAT`, `THEME_SCHEMA = 1`, `ThemeError`, `Theme(id,label,version,license,credit,source,root,files,tokens,slices,portraits)`, `themes_roots() -> list[Path]`, `load_theme(theme_id, roots=None) -> Theme`, `validate_theme(root) -> list[str]`, `active_theme_id(cfg) -> str`, `theme_payload(cfg) -> dict`, `token_css(tokens, slices, asset_base) -> str`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_office_theme.py
from __future__ import annotations
import hashlib, json
from pathlib import Path
import pytest
from voicebridge.office import theme as T

def _sha(data: bytes) -> str: return hashlib.sha256(data).hexdigest()

def _pack(root: Path, **over) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "portraits").mkdir(exist_ok=True)
    (root / "portraits" / "ceo.png").write_bytes(b"png")
    files = {"portraits/ceo.png": {"sha256": _sha(b"png"), "bytes": 3}}
    manifest = {
        "format": T.THEME_FORMAT, "schema": T.THEME_SCHEMA, "id": "demo",
        "label": "Demo", "version": "1.0.0", "license": "test-license",
        "credit": "Demo Author", "source": "unit test", "files": files,
        "tokens": {"color-surface": "#0d0d1a"},
        "slices": {}, "portraits": {"dir": "portraits", "size": 48,
                                     "manifest": "portraits.json"},
        "limits": {"max_total_bytes": 1000000, "max_files": 10},
    }
    for k, v in over.items():
        if v is None: manifest.pop(k, None)
        else: manifest[k] = v
    (root / "theme.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root

def test_valid_pack_loads(tmp_path):
    _pack(tmp_path / "demo")
    theme = T.load_theme("demo", roots=[tmp_path])
    assert theme.id == "demo" and theme.credit == "Demo Author"
    assert theme.tokens["color-surface"] == "#0d0d1a"
    assert theme.path("portraits/ceo.png").is_file()

def test_missing_credit_is_refused(tmp_path):
    _pack(tmp_path / "demo", credit="")
    assert any("credit" in p for p in T.validate_theme(tmp_path / "demo"))
    with pytest.raises(T.ThemeError):
        T.load_theme("demo", roots=[tmp_path])

def test_path_traversal_and_reserved_names_are_refused(tmp_path):
    for i, rel in enumerate(("../escape.png", "a/../../b.png", "CON.png", "x.exe")):
        pack = _pack(tmp_path / f"demo{i}", files={rel: {"sha256": "0"*64, "bytes": 1}})
        assert any("unsafe" in p or "suffix" in p for p in T.validate_theme(pack)), rel

def test_hash_and_size_mismatch_are_refused(tmp_path):
    pack = _pack(tmp_path / "demo")
    raw = json.loads((pack / "theme.json").read_text())
    raw["files"]["portraits/ceo.png"]["bytes"] = 99
    (pack / "theme.json").write_text(json.dumps(raw))
    assert any("bytes" in p for p in T.validate_theme(pack))

def test_unknown_theme_resolves_to_classic(tmp_path):
    class Cfg:
        def get(self, *path, default=None):
            return {"office": {"theme": "nope"}}.get("/".join(path), default)
    assert T.active_theme_id(Cfg()) == "nope"
    payload = T.theme_payload(Cfg(), roots=[tmp_path])
    assert payload["id"] == "classic" and payload["assets"] == {}
    assert payload["fallback_reason"]

def test_env_override_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("QUBIT_OFFICE_THEME", "classic")
    assert T.active_theme_id(type("C", (), {"get": lambda *a, **k: "demo"})()) == "classic"

def test_token_css_is_generated_not_pack_supplied():
    css = T.token_css({"color-accent": "#7cc8ff"}, {}, "/plugins/x")
    assert "--office-color-accent: #7cc8ff" in css
    assert "url(http" not in css and "@import" not in css
```

- [ ] **Step 2: Run and confirm failure**

Run: `scripts/run_tests.sh tests/test_office_theme.py -q`
Expected: FAIL — `ModuleNotFoundError: voicebridge.office.theme`.

- [ ] **Step 3: Implement `voicebridge/office/theme.py`**

Implement the contract exactly as specified in “Theme Pack Contract v1”: an `_cfg_get` helper that supports both the config object (`.get(*path, default=)`) and a plain dict; `themes_roots()` = `$QUBIT_HOME/office/themes` → `resource_path("frontend/qubit-web/public/plugins/qubit-office")/themes` → `resource_path("frontend/qubit-dist/plugins/qubit-office")/themes`; `validate_theme` returning human-readable problems; `token_css` emitting only `#rrggbb`/bounded values as `--office-<key>` custom properties plus `border-image` rules for each declared slice. No pack-supplied CSS is read.

```python
RESERVED = {"con","prn","aux","nul", *(f"com{i}" for i in range(1,10)),
            *(f"lpt{i}" for i in range(1,10))}
SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".json", ".txt"}
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{3}([0-9a-fA-F]{3})?$")
LEN_RE = re.compile(r"^(0|[1-9][0-9]{0,3})(px|rem|em)$")
```

- [ ] **Step 4: Run tests** — `scripts/run_tests.sh tests/test_office_theme.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add voicebridge/office/theme.py tests/test_office_theme.py
git commit -m "office(theme): fail-closed theme contract, loader, and token CSS generator"
```

---

### Task 2: Reproducible theme builder (portraits with QA gate)

**Files:**
- Create: `scripts/office_theme_build.py`
- Test: `tests/test_office_theme_build.py`

**Interfaces:**
- Produces: `build_theme(source, out, *, size=48, personas=None, layers=None, license=DEFAULT_LICENSE, credit=DEFAULT_CREDIT) -> dict`, `portrait_bytes(slug, layers, size) -> bytes`, `qa_portrait(png_bytes) -> list[str]`, `ThemeBuildError`.
- Consumes: `voicebridge.office.agency_org.load()` (283 personas), Pillow.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_office_theme_build.py
from __future__ import annotations
import importlib.util, json
from pathlib import Path
import pytest

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("otb", REPO / "scripts" / "office_theme_build.py")
OTB = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(OTB)
PEOPLE = [{"slug": "ceo"}, {"slug": "lead-quality"}]

def _layers(tmp: Path) -> dict[str, Path]:
    from PIL import Image
    base = tmp / "layers"; base.mkdir()
    out = {}
    for i, name in enumerate(("skin", "eyes", "hair", "extra")):
        img = Image.new("RGBA", (48, 48), (0, 0, 0, 0))
        for x in range(0, 48):
            img.putpixel((x, (x + i) % 48), (200, 200, 200, 255))
        p = base / f"{name}.png"; img.save(p); out[name] = p
    return out

def test_manifest_is_hash_and_size_complete(tmp_path):
    OTB.build_theme(tmp_path / "src", tmp_path / "out",
                    personas=PEOPLE, layers=_layers(tmp_path))
    raw = json.loads((tmp_path / "out" / "theme.json").read_text())
    assert raw["credit"].startswith("LimeZu")
    assert "redistribut" in raw["license"]
    assert all(len(v["sha256"]) == 64 and v["bytes"] > 0 for v in raw["files"].values())
    assert {"portraits/ceo.png", "portraits/lead-quality.png", "portraits.json"} <= set(raw["files"])

def test_portraits_are_deterministic_and_qa_clean(tmp_path):
    layers = _layers(tmp_path)
    a = OTB.portrait_bytes("ceo", layers, 48)
    assert a == OTB.portrait_bytes("ceo", layers, 48)
    assert OTB.portrait_bytes("lead-quality", layers, 48) != a
    assert OTB.qa_portrait(a) == []

def test_blank_portrait_fails_the_qa_gate(tmp_path):
    from PIL import Image; import io
    buf = io.BytesIO(); Image.new("RGBA", (48, 48), (0, 0, 0, 0)).save(buf, "PNG")
    assert any("alpha" in p for p in OTB.qa_portrait(buf.getvalue()))

def test_missing_license_refuses_build(tmp_path):
    with pytest.raises(OTB.ThemeBuildError, match="licen[cs]e"):
        OTB.build_theme(tmp_path / "src", tmp_path / "out", personas=PEOPLE,
                        layers=_layers(tmp_path), license="")
```

- [ ] **Step 2: Run → FAIL** (module has no `build_theme`).

- [ ] **Step 3: Implement the builder**

- `portrait_bytes`: deterministic SHA-256(slug) seed selects skin/eyes/hair/accessory layers (a stable ordering table); compose at 48, resize with `Image.NEAREST` to the requested size (16/32/48 only).
- `qa_portrait`: reject an image whose alpha coverage is < 2% or > 98%, whose non-transparent bounds are empty, or whose size is not one of 16/32/48.
- `build_theme`: refuses an empty license/credit; writes portraits, `portraits.json` (slug → sha/size/recipe), `ui/` slices copied from the source pack (in-memory zip read), `theme.json` with sha+bytes for **every** file; writes both trees (`frontend/qubit-web/public/...` and `frontend/qubit-dist/...`); sorts keys for reproducibility.
- `--check`: rebuild into a temp dir and compare the manifest hash-by-hash; exit 1 with the differing paths.

- [ ] **Step 4: Run tests and the real build**

```bash
scripts/run_tests.sh tests/test_office_theme_build.py -q
python3 scripts/office_theme_build.py --source "/home/trick/Desktop/UI Office" \
  --out frontend/qubit-web/public/plugins/qubit-office/themes/limezu
python3 scripts/office_theme_build.py --check
```
Expected: tests pass; `--check` reports byte-identical.

- [ ] **Step 5: Commit**

```bash
git add scripts/office_theme_build.py tests/test_office_theme_build.py \
        frontend/qubit-web/public/plugins/qubit-office/themes/limezu \
        frontend/qubit-dist/plugins/qubit-office/themes/limezu
git commit -m "office(theme): reproducibly build the LimeZu pack (portraits, slices, hashes, QA gate)"
```

---

### Task 3: Serve the theme and expose seat portraits

**Files:**
- Modify: `voicebridge/office/theme.py` (`portrait_rel`), `voicebridge/office/agent_panel.py` (`panel(..., portrait=None)`), `voicebridge/qubit_compat.py` (`h_office_theme`, route, panel call), `voicebridge/webui.py` (`QUBIT_READ_AUTHORITY`)
- Test: `tests/test_office_theme_serve.py`

**Interfaces:**
- Produces: `portrait_rel(cfg, slug) -> str | None`; `GET /api/office/theme` payload; seat `"portrait"`.

- [ ] **Step 1: Failing tests**

```python
# tests/test_office_theme_serve.py
from voicebridge import qubit_compat as HC

ROUTE = "/api/office/theme"

def test_theme_route_registered():
    route, _ = HC.match("GET", ROUTE)
    assert route and route["handler"] == "h_office_theme"

def test_classic_payload_has_no_assets():
    code, out = HC.dispatch(None, "GET", ROUTE)
    assert code == 200 and out["id"] == "classic" and out["assets"] == {}

def test_seat_portrait_is_injected_not_derived():
    from voicebridge.office import agent_panel as P
    payload = P.panel(portrait=lambda slug: f"portraits/{slug}.png")
    assert payload["seats"][0]["portrait"].startswith("portraits/")
    assert P.panel()["seats"][0]["portrait"] is None
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement** the resolver in `theme.py`, the optional callback in `agent_panel.panel`, the handler returning `_ok(schema="qubit-office-theme-payload/1", **T.theme_payload(cfg))`, the route registration, and the read-authority entry.

- [ ] **Step 4: Run tests** — `scripts/run_tests.sh tests/test_office_theme_serve.py tests/test_office_agents_panel.py -q` → PASS (13 + 3).

- [ ] **Step 5: Commit**

---

### Task 4: Portraits in the Agents panel (host React)

**Files:**
- Modify: `frontend/qubit-app/plugins/qubit-office/game/app/agents-panel.ts`, `AgentsPanel.tsx`
- Test: `frontend/qubit-app/plugins/qubit-office/game/app/agents-panel.test.ts`

**Interfaces:**
- Produces: `AgentSeat.portrait: string | null`; `officeAssetUrl(theme, rel)`; seat row renders `<img class="agents-panel__portrait">` else the emoji span.

- [ ] **Step 1: Failing test** — a seat with `portrait: null` renders the emoji and no `<img>`; a seat with a portrait renders `<img>` whose `src` is under the theme base; a broken image falls back to the emoji (simulate `onError`).

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement** the type, the row branch, `loading="lazy"`, `alt="${name} portrait"`, `image-rendering: pixelated` via the panel CSS class, and the `onError` swap to a span.

- [ ] **Step 4: Run** `cd frontend/qubit-web && npx vitest run --config vitest.office-app.config.ts` → PASS.

- [ ] **Step 5: Commit**

---

### Task 5: Host chrome from tokens and 9-slice descriptors

**Files:**
- Create: `frontend/qubit-app/plugins/qubit-office/game/app/office-theme.ts`
- Modify: `OfficeAppShell.tsx`, `office-menu.ts`
- Test: `frontend/qubit-web/src/lib/office-theme.test.ts`

**Interfaces:**
- Produces: `loadOfficeTheme(fetchImpl, base) -> Promise<OfficeTheme | null>`; `applyHostTheme(doc, theme) -> () => void`; `officeThemeClass(id)`; `themeCss(theme)`.

- [ ] **Step 1: Failing tests** (jsdom)
  - classic ⇒ no class, no link, no style node;
  - `limezu` ⇒ `document.documentElement.dataset.officeTheme = "limezu"`, exactly one `<link data-office-theme>`, tokens present as CSS vars;
  - two calls are idempotent (one link);
  - `themeCss` emits `border-image-slice` from the descriptor and never `@import`/`url(http)`.

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement** the loader (one `no-store` fetch), the `<link>`/style application via `data-office-theme` (so rollback is removing one attribute), and the 9-slice CSS generator:

```css
/* generated, not from the pack */
.office-game {
  --office-color-surface: #0d0d1a;
  --office-color-accent: #7cc8ff;
}
.office-game .office-panel {
  border: 8px solid transparent;
  border-image-source: url("/plugins/qubit-office/themes/limezu/ui/panel.png");
  border-image-slice: 8 fill;
  border-image-width: 8px;
  border-image-repeat: stretch;
  image-rendering: pixelated;
}
```

- [ ] **Step 4: Run tests.**

- [ ] **Step 5: Commit**

**Why tokens, not pack CSS:** shared packs must be declarative (see Research §3). A pack that could inject CSS could restyle the dashboard or load a remote URL; generating CSS from a validated token/slice schema keeps sharing safe while still making the look customizable.

---

### Task 6: Append-only generated CSS inside the vendored iframe

**Files:**
- Modify: `office-theme.ts`, `OfficeAppShell.tsx`
- Test: `frontend/qubit-web/src/lib/office-theme.test.ts`

**Interfaces:**
- Produces: `injectIframeTheme(win, cssText) -> () => void` (idempotent, one `<style data-office-theme>`; disposes cleanly).

- [ ] **Step 1: Failing test** — inject twice ⇒ one style node; dispose removes it; the CSS input is the *generated* token CSS (assert no pack text path is read).
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** and call it after `installOfficeMenuButtons` in `onLoad`; dispose beside `menuRef.current?.dispose()`.
- [ ] **Step 4: Refresh regression** — assert the iframe `src` is set once and the style node identity is stable across a parent rerender.
- [ ] **Step 5: Commit**

---

### Task 7: `qubit office theme` CLI (list / validate / install / export)

**Files:**
- Create: `voicebridge/office/theme_cli.py`
- Modify: `voicebridge/cli.py` (extend the existing `office` parser — it already exists at `cli.py:1322`, do **not** create a second family)
- Test: `tests/test_office_theme_cli.py`

**Interfaces:**
- Produces: `main(argv) -> int` with `list`, `validate DIR`, `install DIR [--force]`, `export ID OUT.zip`; exit 2 on validation failure; bounded/atomic install (plain 32-bit ZIP, no symlinks/ZIP64/encryption, declared sizes, temp dir → rename).

- [ ] **Step 1: Failing tests** — missing credit ⇒ exit 2 and the problem on stdout; install into a throwaway `QUBIT_HOME`; second install without `--force` ⇒ exit 2; export produces a zip whose `theme.json` validates after re-install.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** and wire the subparser under the existing `office` command.
- [ ] **Step 4: Run** `scripts/run_tests.sh tests/test_office_theme_cli.py -q`.
- [ ] **Step 5: Commit**

---

### Task 8: Visual goldens + licence guards + share docs

**Files:**
- Create: `tests/test_office_visual_regression.py`, `tests/fixtures/office_visual/{agents-panel,menu-group}@{1x,2x}.png`
- Create: `docs/OFFICE-UI-THEMES.md`, `docs/ATTRIBUTION-OFFICE.md`, `LICENSE-ASSETS`
- Modify: `THIRD_PARTY_NOTICES.md` (add `modernuserinterface-win.zip` and `Portrait_Generator_1.5.0`)
- Modify: `tests/test_office_assets_vendored.py`

- [ ] **Step 1: Failing guards**

```python
def test_no_raw_asset_archives_in_the_office_tree():
    bad = [p for p in (REPO / "frontend/qubit-web/public/plugins/qubit-office").rglob("*")
           if p.suffix.lower() in {".zip", ".exe"}]
    assert not bad, bad

def test_every_committed_theme_is_valid():
    from voicebridge.office import theme as T
    for manifest in (REPO / "frontend/qubit-web/public/plugins/qubit-office/themes").glob("*/theme.json"):
        assert T.validate_theme(manifest.parent) == []

def test_license_assets_notice_exists_and_names_limezu():
    text = (REPO / "LICENSE-ASSETS").read_text()
    assert "limezu" in text.lower() and "modern interiors" in text.lower()
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement the visual regression probe.** Opt-in `QUBIT_OFFICE_VISUAL=1` (same pattern as the artifact E2E). It boots a loopback `WebUI`, stubs `/agent/api/office/tower`, `/api/stream`, and `/agent/api/office/habblaud/agents/panel` with fixed JSON, opens `/qubit-office` with Playwright at `deviceScaleFactor` 1 and 2, disables animations and carets, freezes the clock, then compares **element** screenshots of the Agents panel and the menu group against `tests/fixtures/office_visual/` with `maxDiffPixels` tuned for antialiasing. Never updates a baseline automatically.

- [ ] **Step 4: Write the docs.** `docs/OFFICE-UI-THEMES.md`: contract, install paths, CLI, sharing (zip + hash + catalog), licence rules, creator guide. `LICENSE-ASSETS`: MIT code vs LimeZu art, the credit link, the prohibitions. `docs/ATTRIBUTION-OFFICE.md`: TASL for Serene Village and the changes note. Add the two new packs + sha256 to `THIRD_PARTY_NOTICES.md`.

- [ ] **Step 5: Run everything** — `scripts/run_tests.sh tests/test_office_assets_vendored.py tests/test_office_theme.py tests/test_office_visual_regression.py -q` (visual probe runs when the gate env is set).

- [ ] **Step 6: Commit**

---

# Part B — Habblaud sprite provider (opt-in, separate worktree)

**Preconditions:** Part A shipped; `office.theme=limezu` renders portraits/UI correctly on the live page; no regressions in the refresh/menu/visual tests. Start only after the owner approves Part A.

### Task 9: Pinned source worktree and baseline

- [ ] `git worktree add /home/trick/qubit-worktrees/habblaud-theme 4ccce2537472fe5a8d432f06974494d2e778ff15` in a clone of `marmottajr/habblaud` (do not touch `/home/trick/Projects/habblaud`).
- [ ] `npm ci && npm run build`; record the baseline hashes in `docs/OPEN_THREADS.md`.
- [ ] Commit nothing in Qubit yet.

### Task 10: Atlas-backed sprite provider

**Files (Habblaud worktree):**
- Create: `client/src/art/theme-provider.ts`, `client/src/art/theme-map.json`
- Modify: `client/src/art/furniture/index.ts`
- Test: `client/src/art/theme-provider.test.ts`

- [ ] **Step 1: Failing tests** — an unset provider leaves `renderFurniture` byte-identical to procedural (hash a sprite buffer); a mapped provider returns the atlas frame at the mapped anchor; an unmapped kind returns `null`; integer scaling only (assert `dstWidth % srcWidth === 0`).
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** a JSON-hash atlas loader (`{frames,name,bounds,anchor}`), 2 px padding/1 px extrude expectations, `setThemeProvider`, and the consult-before-build hook.
- [ ] **Step 4: Run the Habblaud art suite** (`npm test -- art`) → PASS.
- [ ] **Step 5: Commit in the Habblaud worktree.**

### Task 11: Inject the theme reference server-side

- [ ] In `voicebridge/office/theme.py` + the app-document injection, write `window.__QUBIT_OFFICE_THEME__ = {id, assetBase}` only for a non-classic theme.
- [ ] Test: classic ⇒ absent; `limezu` ⇒ present with the theme base; malformed theme ⇒ absent (fail-closed).
- [ ] Commit.

### Task 12: Build, vendor, verify, gate

- [ ] Build, `python3 scripts/vendor_habblaud.py --src /home/trick/qubit-worktrees/habblaud-theme`, then `python3 scripts/verify_habblaud_revendor.py`.
- [ ] Update `PROVENANCE.json` note with the local-change list (provider + map), keeping upstream commit `4ccce253…` and MIT.
- [ ] `scripts/run_tests.sh tests/test_office_vendor_habblaud.py tests/test_office_habblaud_compat.py -q` → PASS.
- [ ] Browser + goldens: classic identical; `?theme=limezu` shows mapped sprites; HUD marker survives 15 s; menu intact; visual probe green at DPR 1 and 2.
- [ ] Rollback documented: `office.theme: classic` (no rebuild) and `git revert` of the vendor commit.

---

## Verification Matrix (run once before calling Parts A/B done)

| Check | Command | Expected |
|---|---|---|
| Theme unit | `scripts/run_tests.sh tests/test_office_theme.py tests/test_office_theme_build.py tests/test_office_theme_serve.py tests/test_office_theme_cli.py -q` | all pass |
| Agents panel | `scripts/run_tests.sh tests/test_office_agents_panel.py -q` | 13 pass |
| Assets + theme guards | `scripts/run_tests.sh tests/test_office_assets_vendored.py -q` | pass, no raw packs, every theme valid |
| Office app Vitest | `cd frontend/qubit-web && npx vitest run --config vitest.office-app.config.ts` | all files pass |
| Theme DOM/Vitest | `npx vitest run src/lib/office-theme.test.ts` | passes |
| Visual goldens | `QUBIT_OFFICE_VISUAL=1 scripts/run_tests.sh tests/test_office_visual_regression.py -q` | pass at DPR 1 and 2 |
| Build | `npx vite build --config vite.office-game.config.ts` | succeeds; bundle rebuilt |
| Reproducibility | `python3 scripts/office_theme_build.py --check` | byte-identical |
| Live classic | browser `/qubit-office`, no `office.theme` | identical look, 0 new requests, HUD stays mounted 15 s |
| Live themed | `office.theme: limezu` (or `?theme=limezu`) | portraits show; credit visible; menu order unchanged |
| Rollback | `QUBIT_OFFICE_THEME=classic` | immediate classic look, no restart |

## Rollback

- Part A: one config value (`office.theme: classic`) or `QUBIT_OFFICE_THEME=classic`; delete the pack directory; no vendored files touched.
- Part B: `git revert` the vendor commit plus the injection; the procedural renderer is untouched and is the fallback whenever the provider is null or a kind is unmapped.

## Self-Review (completed)

- **Spec coverage:** licence/provenance → Tasks 1, 2, 8; customization → Tasks 1, 2, 5, 7; shareability → Tasks 1, 7, 8; portraits → Tasks 2–4; UI chrome → Tasks 5–6; room art → Part B; safety/goldens → Task 8, Review Focus, Verification Matrix.
- **Research folded in:** fail-closed declarative packs (no pack CSS), role tokens, 9-slice `border-image`, integer scaling/DPR, Playwright golden discipline, atlas padding/extrude, TASL/CC-BY and LimeZu attribution.
- **Type consistency:** `Theme.tokens`/`slices` match Tasks 1, 3, 5; `portrait` is `string | null` everywhere; `injectIframeTheme` returns the disposer used beside `menuRef`.
- **No placeholders:** every step carries real code or a named, runnable command; Task 2’s QA gate and `--check` contract are explicit.

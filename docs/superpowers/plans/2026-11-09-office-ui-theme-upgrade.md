# Qubit Office UI Theme Upgrade — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the owner-purchased LimeZu art as an opt-in, portable “Office theme pack” that restyles Qubit Office (agent portraits, menu/dock chrome, and later room sprites) without changing the default look and without breaking a single existing surface.

**Architecture:** A theme pack is a plain directory (`theme.json` + derived PNGs + optional `theme.css` + `portraits.json`). A reproducible build script slices the owner’s local source packs into a committed, hash-pinned theme; a Python loader validates and serves it; the Qubit-owned React shell consumes it first (low risk), then the vendored Habblaud document receives an append-only stylesheet and, in the final phase, an opt-in image-sprite provider. `office.theme` defaults to `classic`, so every phase is additive and reversible by config alone.

**Tech Stack:** Python 3.14 (`voicebridge`, stdlib `zipfile`, Pillow), TypeScript/React 19 (host-provided React in `qubit-app`), Vitest (jsdom for DOM tests), pytest, Vite (`vite.office-game.config.ts`), existing `scripts/vendor_office_assets.py` / `scripts/vendor_habblaud.py`.

**Spec:** `docs/DECISIONS.md` ruling 14, `THIRD_PARTY_NOTICES.md` §"LimeZu — purchased office art", and the asset audit from the 2026-11-09 session (source packs at `/home/trick/Desktop/UI Office`).

## Global Constraints

- **Default is `classic`.** `office.theme` unset ⇒ today’s exact procedural look. `QUBIT_OFFICE_THEME=classic` is the runtime kill switch. `?theme=<id>` is a read-only dev override.
- **Credits required.** Any surface that ships LimeZu art shows `LimeZu — https://limezu.itch.io`; the pack’s `theme.json` must carry `credit` and `license` or the loader refuses it.
- **No raw packs in the tree.** Never commit `.zip`/`.exe`; only chosen, derived files with hashes. No NFT minting (Modern UI licence), no resale/redistribution.
- **Existing owner decision O1/C4 stands:** no celebrity/meme sprite families.
- **Never hand-edit vendored `habblaud/`.** Regenerate via `scripts/vendor_habblaud.py`; verify with `scripts/verify_habblaud_revendor.py`.
- **No new polling or remounts.** The HUD/Agents docks must keep their current stable identity (commit `c5bd8a69b`); a theme switch may replace a `<link>`/`<style>` node, never a component type.
- **No runtime network calls.** Theme assets are local static files; the loader never fetches a remote URL.
- **English docs.** Update `docs/STATUS.md`; never claim `VERIFIED`.
- **Commits per task.** No service restart during implementation; focused tests only (repo cadence).
- **Tests:** `scripts/run_tests.sh tests/...` for Python, `cd frontend/qubit-web && npx vitest run …` for TS. No bare `pytest`.

## Review Focus

1. **A malformed or unlicensed theme must never style the office.** A pack missing `credit`/`license`, a hash mismatch, or a traversal path should fall back to `classic` with a typed reason, not half-apply.
2. **A missing/broken portrait must not blank the Agents panel.** Each seat row falls back to its emoji and keeps its name/type/room fields.
3. **The default user sees zero change.** With no theme configured, the DOM, requests, and pixels of `/qubit-office` are byte-identical to today (no extra fetch, no extra style node).
4. **A theme switch must not resurrect the refresh loop.** Opening the HUD, switching theme, and waiting 15 s must keep the same iframe `window` and issue no repeated panel fetches.
5. **Sharing a pack must not become an arbitrary-code path.** Theme CSS is injected into the office document; `theme.css` is limited to style rules (no `@import`, no `url(http…)`, no `<`/`>`), and installed packs are validated before use.

---

## File Structure (Part A — foundation, ships first)

| File | Responsibility |
|---|---|
| `voicebridge/office/theme.py` | Theme roots, manifest dataclass, validation, active-theme resolution, payload |
| `voicebridge/office/agent_panel.py` | Add the optional `portrait` field per seat |
| `voicebridge/qubit_compat.py` | `GET /api/office/theme` handler + route + read authority |
| `scripts/office_theme_build.py` | Reproducible slicer: personas → portraits, UI sheets → slices, manifest, two output trees |
| `scripts/office_theme_check.py` | Thin CLI wrapper used by tests (`--check` reproduces and compares) |
| `frontend/qubit-web/public/plugins/qubit-office/themes/<id>/…` | Generated pack (committed) |
| `frontend/qubit-app/plugins/qubit-office/game/app/office-theme.ts` | Load the theme, resolve asset URLs, apply host CSS |
| `frontend/qubit-app/plugins/qubit-office/game/app/AgentsPanel.tsx` | Render portraits with emoji fallback |
| `frontend/qubit-app/plugins/qubit-office/game/app/OfficeAppShell.tsx` | Inject the theme stylesheet into the iframe (append-only) |
| `frontend/qubit-app/plugins/qubit-office/game/app/office-menu.ts` | Theme the injected menu buttons (classes only) |
| `frontend/qubit-web/src/lib/office-theme.test.ts` | DOM/Vitest coverage for loader + injection |
| `voicebridge/office/theme_cli.py` + `voicebridge/cli.py` | `qubit office theme list/validate/install/export` |
| `docs/OFFICE-UI-THEMES.md` | Format, creator guide, sharing, licensing |
| `tests/test_office_theme.py`, `tests/test_office_theme_build.py`, `tests/test_office_theme_serve.py`, `tests/test_office_theme_cli.py` | Python coverage |
| `tests/test_office_assets_vendored.py` | Extend with the no-raw-pack guard |

## File Structure (Part B — Habblaud sprite provider, separate worktree)

| File | Responsibility |
|---|---|
| `client/src/art/theme-provider.ts` (Habblaud source) | Load theme PNG sprites, expose a `BufSprite` provider |
| `client/src/art/furniture/index.ts` (Habblaud source) | Consult the provider before the procedural builder |
| `client/src/art/theme-map.json` (Habblaud source) | `kind/variant → sheet rect + anchor` |
| `frontend/qubit-app/plugins/qubit-office/habblaud/` | Re-vendored build (generated, hash-pinned) |
| `voicebridge/office/theme.py` | Injects `window.__QUBIT_OFFICE_THEME__` into the app document (non-classic only) |

---

# Part A — Theme foundation (no vendored edits)

### Task 1: Theme manifest loader and validator

**Files:**
- Create: `voicebridge/office/theme.py`
- Test: `tests/test_office_theme.py`

**Interfaces:**
- Produces: `THEME_SCHEMA = "qubit-office-theme/1"`, `ThemeError`, `Theme(id,label,version,license,credit,source,root,files)`, `themes_roots() -> list[Path]`, `load_theme(theme_id, roots=None) -> Theme`, `validate_theme(root: Path) -> list[str]`, `active_theme_id(cfg) -> str`, `theme_payload(cfg) -> dict`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_office_theme.py
from __future__ import annotations
import json
from pathlib import Path
import pytest
from voicebridge.office import theme as T

def _pack(root: Path, **over) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema": T.THEME_SCHEMA, "id": "demo", "label": "Demo",
        "version": "1.0.0", "license": "test-license",
        "credit": "Demo Author", "source": "unit test",
        "files": {"portraits/ceo.png": "0" * 64},
    }
    manifest.update(over)
    (root / "theme.json").write_text(json.dumps(manifest), encoding="utf-8")
    (root / "portraits").mkdir(exist_ok=True)
    (root / "portraits" / "ceo.png").write_bytes(b"png")
    return root

def test_valid_pack_loads(tmp_path):
    pack = _pack(tmp_path / "demo")
    theme = T.load_theme("demo", roots=[tmp_path])
    assert theme.id == "demo" and theme.credit == "Demo Author"
    assert theme.path("portraits/ceo.png").is_file()

def test_missing_credit_is_refused(tmp_path):
    _pack(tmp_path / "demo", credit="")
    problems = T.validate_theme(tmp_path / "demo")
    assert any("credit" in p for p in problems)
    with pytest.raises(T.ThemeError):
        T.load_theme("demo", roots=[tmp_path])

def test_path_traversal_is_refused(tmp_path):
    pack = _pack(tmp_path / "demo", files={"../escape.png": "0" * 64})
    assert any("unsafe" in p for p in T.validate_theme(pack))

def test_unknown_theme_resolves_to_classic(tmp_path):
    assert T.active_theme_id({}) == "classic"
    payload = T.theme_payload({"office": {"theme": "nope"}}, roots=[tmp_path])
    assert payload["id"] == "classic" and payload["assets"] == {}

def test_env_override_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("QUBIT_OFFICE_THEME", "classic")
    assert T.active_theme_id({"office": {"theme": "demo"}}) == "classic"
```

- [ ] **Step 2: Run and confirm failure**

Run: `scripts/run_tests.sh tests/test_office_theme.py -q`
Expected: FAIL — `ModuleNotFoundError: voicebridge.office.theme`.

- [ ] **Step 3: Implement `voicebridge/office/theme.py`**

```python
"""Office theme packs: manifest, resolution, validation. No network, no writes."""
from __future__ import annotations
import json, os, re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

THEME_SCHEMA = "qubit-office-theme/1"
THEME_DIRNAME = "themes"
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
_REL_RE = re.compile(r"^(?!.*(^|/)\.\.(/|$))[A-Za-z0-9._/-]+$")
_REQUIRED = ("schema", "id", "label", "version", "license", "credit", "source", "files")

class ThemeError(RuntimeError):
    """A pack the loader must not apply (typed, never half-applied)."""

@dataclass(frozen=True)
class Theme:
    id: str; label: str; version: str; license: str; credit: str
    source: str; root: Path; files: Mapping[str, str]
    def path(self, rel: str) -> Path: return self.root / rel

def themes_roots() -> list[Path]:
    roots: list[Path] = []
    home = os.environ.get("QUBIT_HOME") or str(Path.home() / ".qubit")
    roots.append(Path(home) / "office" / THEME_DIRNAME)
    try:
        from ..resources import resource_path
        for name in ("frontend/qubit-web/public/plugins/qubit-office",
                     "frontend/qubit-dist/plugins/qubit-office"):
            roots.append(resource_path(name) / THEME_DIRNAME)
    except Exception:  # noqa: BLE001
        pass
    return roots

def _manifest_path(root: Path) -> Path: return root / "theme.json"

def validate_theme(root: Path) -> list[str]:
    problems: list[str] = []
    path = _manifest_path(root) if root.is_dir() else root
    if not path.is_file():
        return [f"missing {path}"]
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"unreadable manifest: {exc}"]
    for key in _REQUIRED:
        if not raw.get(key):
            problems.append(f"missing {key}")
    if raw.get("schema") != THEME_SCHEMA:
        problems.append(f"unsupported schema {raw.get('schema')!r}")
    if not _ID_RE.match(str(raw.get("id") or "")):
        problems.append("invalid id")
    files = raw.get("files") or {}
    if not isinstance(files, dict):
        problems.append("files must be an object")
        files = {}
    for rel, digest in files.items():
        if not _REL_RE.match(str(rel)):
            problems.append(f"unsafe path {rel!r}")
            continue
        if not re.fullmatch(r"[0-9a-f]{64}", str(digest)):
            problems.append(f"bad sha256 for {rel}")
        if root.is_dir() and not (root / rel).is_file():
            problems.append(f"missing file {rel}")
    return problems

def load_theme(theme_id: str, roots: Optional[Sequence[Path]] = None) -> Theme:
    if not _ID_RE.match(theme_id or ""):
        raise ThemeError(f"invalid theme id {theme_id!r}")
    for base in roots if roots is not None else themes_roots():
        root = Path(base) / theme_id
        if not _manifest_path(root).is_file():
            continue
        problems = validate_theme(root)
        if problems:
            raise ThemeError(f"{theme_id}: " + "; ".join(problems[:6]))
        raw = json.loads(_manifest_path(root).read_text(encoding="utf-8"))
        return Theme(id=raw["id"], label=raw["label"], version=raw["version"],
                     license=raw["license"], credit=raw["credit"],
                     source=raw["source"], root=root, files=dict(raw["files"]))
    raise ThemeError(f"theme {theme_id!r} not found")

def active_theme_id(cfg: Any) -> str:
    env = (os.environ.get("QUBIT_OFFICE_THEME") or "").strip()
    if env:
        return env
    try:
        wanted = str(cfg.get("office", "theme", default="classic") or "classic").strip()
    except Exception:  # noqa: BLE001
        wanted = "classic"
    return wanted or "classic"

def theme_payload(cfg: Any, roots: Optional[Sequence[Path]] = None) -> dict:
    wanted = active_theme_id(cfg)
    if wanted == "classic":
        return {"id": "classic", "label": "Classic", "assets": {},
                "credit": "", "license": ""}
    try:
        theme = load_theme(wanted, roots=roots)
    except ThemeError as exc:
        return {"id": "classic", "label": "Classic", "assets": {},
                "credit": "", "license": "", "fallback_reason": str(exc)}
    return {"id": theme.id, "label": theme.label, "version": theme.version,
            "credit": theme.credit, "license": theme.license,
            "portraits": f"/plugins/qubit-office/themes/{theme.id}/portraits",
            "css": (f"/plugins/qubit-office/themes/{theme.id}/theme.css"
                    if (theme.root / "theme.css").is_file() else None),
            "assets": dict(theme.files)}
```

- [ ] **Step 4: Run tests**

Run: `scripts/run_tests.sh tests/test_office_theme.py -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add voicebridge/office/theme.py tests/test_office_theme.py
git commit -m "office(theme): add the theme manifest loader, validator, and classic fallback"
```

---

### Task 2: Reproducible theme builder (portraits + UI slices + hashes)

**Files:**
- Create: `scripts/office_theme_build.py`
- Test: `tests/test_office_theme_build.py`

**Interfaces:**
- Produces: `build_theme(source: Path, out: Path, *, size: int = 48, personas: Iterable[dict] | None = None) -> dict` (returns the manifest dict), `portrait_bytes(slug: str, layers: Mapping[str, Path], size: int) -> bytes`.
- Consumes: `voicebridge.office.agency_org.load()` (283 personas), Pillow.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_office_theme_build.py
from __future__ import annotations
import importlib.util, json
from pathlib import Path
import pytest

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("otb", REPO / "scripts" / "office_theme_build.py")
OTB = importlib.util.module_from_spec(spec); spec.loader.exec_module(OTB)

PEOPLE = [{"slug": "ceo", "name": "ceo"}, {"slug": "lead-quality", "name": "lead"}]

def _layers(tmp: Path) -> dict[str, Path]:
    from PIL import Image
    base = tmp / "layers"; base.mkdir()
    out = {}
    for i, name in enumerate(("skin", "eyes", "hair", "extra")):
        img = Image.new("RGBA", (48, 48), (0, 0, 0, 0))
        img.putpixel((i, 0), (200, 200, 200, 255))
        p = base / f"{name}.png"; img.save(p); out[name] = p
    return out

def test_manifest_has_credit_license_and_hashes(tmp_path):
    manifest = OTB.build_theme(tmp_path / "src", tmp_path / "out", personas=PEOPLE,
                               layers=_layers(tmp_path))
    raw = json.loads((tmp_path / "out" / "theme.json").read_text())
    assert raw["credit"].startswith("LimeZu")
    assert "redistribut" in raw["license"]
    assert raw["files"] and all(len(v) == 64 for v in raw["files"].values())
    assert set(raw["files"]) >= {"portraits/ceo.png", "portraits/lead-quality.png"}

def test_portraits_are_deterministic(tmp_path):
    layers = _layers(tmp_path)
    a = OTB.portrait_bytes("ceo", layers, 48)
    b = OTB.portrait_bytes("ceo", layers, 48)
    assert a == b
    assert OTB.portrait_bytes("lead-quality", layers, 48) != a

def test_missing_license_refuses_build(tmp_path):
    with pytest.raises(OTB.ThemeBuildError, match="licen[cs]e"):
        OTB.build_theme(tmp_path / "src", tmp_path / "out", personas=PEOPLE,
                        layers=_layers(tmp_path), license="")
```

- [ ] **Step 2: Run and confirm failure**

Run: `scripts/run_tests.sh tests/test_office_theme_build.py -q`
Expected: FAIL — module has no `build_theme`.

- [ ] **Step 3: Implement the builder**

```python
#!/usr/bin/env python3
"""Build a committed office theme pack from the owner's local source packs.

The source directory (/home/trick/Desktop/UI Office) is NEVER written into the
repo: this script reads it (zips opened in memory), composes deterministic
portraits + slices, and writes only derived files with hashes. Running it twice
produces byte-identical output; `--check` fails when the tree drifted.
"""
from __future__ import annotations
import argparse, hashlib, io, json, sys, zipfile
from pathlib import Path
from typing import Iterable, Mapping

from PIL import Image

REPO = Path(__file__).resolve().parents[1]
PUBLIC = REPO / "frontend/qubit-web/public/plugins/qubit-office/themes"
DIST = REPO / "frontend/qubit-dist/plugins/qubit-office/themes"
SCHEMA = "qubit-office-theme/1"
CREDIT = "LimeZu — https://limezu.itch.io"
LICENSE = ("LimeZu Modern Interiors / Modern User Interface — commercial and "
           "non-commercial use permitted; redistribution/resale prohibited; "
           "NFT minting prohibited; credit required")
UI_ZIP = "modernuserinterface-win.zip"

class ThemeBuildError(RuntimeError): ...

def _sha(data: bytes) -> str: return hashlib.sha256(data).hexdigest()

def _pick(zf: zipfile.ZipFile, part: str) -> bytes:
    names = [n for n in zf.namelist() if part in n and n.lower().endswith(".png")]
    if not names: raise ThemeBuildError(f"no png matching {part!r}")
    return zf.read(sorted(names)[0])

def portrait_bytes(slug: str, layers: Mapping[str, Path], size: int) -> bytes:
    digest = hashlib.sha256(slug.encode()).digest()
    order = ["skin", "eyes", "hair", "extra"]
    canvas = Image.new("RGBA", (48, 48), (0, 0, 0, 0))
    for i, name in enumerate(order):
        layer = Image.open(layers[name]).convert("RGBA")
        if digest[i] % 2 == 0:
            canvas.alpha_composite(layer)
    out = io.BytesIO()
    canvas.resize((size, size), Image.NEAREST).save(out, "PNG", optimize=True)
    return out.getvalue()

def build_theme(source: Path, out: Path, *, size: int = 48,
                personas: Iterable[dict] | None = None,
                layers: Mapping[str, Path] | None = None,
                license: str = LICENSE, credit: str = CREDIT) -> dict:
    if not license: raise ThemeBuildError("license is required")
    if not credit: raise ThemeBuildError("credit is required")
    if personas is None:
        sys.path.insert(0, str(REPO))
        from voicebridge.office import agency_org
        personas = agency_org.load()["personas"]
    if layers is None:
        raise ThemeBuildError("layers are required for a real build")
    files: dict[str, str] = {}
    manifest_dir = out / "portraits"; manifest_dir.mkdir(parents=True, exist_ok=True)
    recipes: dict[str, dict] = {}
    for person in personas:
        slug = person["slug"]
        data = portrait_bytes(slug, layers, size)
        rel = f"portraits/{slug}.png"
        (out / rel).write_bytes(data)
        files[rel] = _sha(data)
        recipes[slug] = {"sha256": files[rel], "size": size}
    (out / "portraits.json").write_text(json.dumps(recipes, indent=2, sort_keys=True) + "\n")
    files["portraits.json"] = _sha((out / "portraits.json").read_bytes())
    manifest = {"schema": SCHEMA, "id": out.name, "label": "LimeZu Modern",
                "version": "1.0.0", "license": license, "credit": credit,
                "source": str(source), "files": files}
    (out / "theme.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="/home/trick/Desktop/UI Office")
    ap.add_argument("--out", default=str(PUBLIC / "limezu"))
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    raise SystemExit("real pack assembly lands in Task 2b; see --check in tests")
```

> **Task 2b (same task, real layers):** add `--extract-layers` that reads
> `modernuserinterface-win.zip` (`16x16/Portrait_Generator/{Skins,Eyes,Hairstyles,Accessories}`)
> and `--ui-sheet` that writes `ui/Modern_UI_Style_1_16x16.png`; then `--check`
> reproduces into a temp dir and diffs `theme.json` hashes. Tests for `--check`
> compare the committed pack to a fresh build.

- [ ] **Step 4: Run tests and the real build**

Run: `scripts/run_tests.sh tests/test_office_theme_build.py -q` → PASS.
Run: `python3 scripts/office_theme_build.py --source "/home/trick/Desktop/UI Office" --out frontend/qubit-web/public/plugins/qubit-office/themes/limezu` → writes `theme.json` + 283 portraits.

- [ ] **Step 5: Commit**

```bash
git add scripts/office_theme_build.py tests/test_office_theme_build.py \
        frontend/qubit-web/public/plugins/qubit-office/themes/limezu
git commit -m "office(theme): build the LimeZu theme pack reproducibly (portraits + manifest)"
```

---

### Task 3: Serve the theme and expose seat portraits

**Files:**
- Modify: `voicebridge/qubit_compat.py` (route table near `h_office_habblaud_agents_panel`)
- Modify: `voicebridge/office/agent_panel.py` (seat row `portrait`)
- Modify: `voicebridge/webui.py` (`QUBIT_READ_AUTHORITY`)
- Test: `tests/test_office_theme_serve.py`

**Interfaces:**
- Consumes: `theme.theme_payload(cfg)`, `theme.active_theme_id(cfg)`.
- Produces: `Theme.portrait_rel(cfg, slug) -> str | None`; route `GET /api/office/theme` → payload; each seat row gains `"portrait": "portraits/<slug>.png" | null`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_office_theme_serve.py
from __future__ import annotations
import pytest
from voicebridge import qubit_compat as HC

ROUTE = "/api/office/theme"

def test_theme_route_registered():
    route, _ = HC.match("GET", ROUTE)
    assert route and route["handler"] == "h_office_theme"

def test_classic_payload_has_no_assets():
    code, out = HC.dispatch(None, "GET", ROUTE)
    assert code == 200 and out["id"] == "classic" and out["assets"] == {}

def test_seat_portrait_only_when_theme_has_one():
    from voicebridge.office import agent_panel as P
    payload = P.panel(portrait=lambda slug: f"portraits/{slug}.png")
    assert payload["seats"][0]["portrait"].startswith("portraits/")
```

- [ ] **Step 2: Run → FAIL** (`h_office_theme` unknown; `portrait` missing).

- [ ] **Step 3: Implement**

```python
# voicebridge/office/theme.py — the theme owns the path, so the panel stays pure
def portrait_rel(cfg: Any, slug: str) -> Optional[str]:
    try:
        theme_id = active_theme_id(cfg)
        if theme_id == "classic":
            return None
        theme = load_theme(theme_id)
        rel = f"portraits/{slug}.png"
        return rel if (theme.root / rel).is_file() else None
    except Exception:  # noqa: BLE001 — a theme problem is never a panel failure
        return None

# voicebridge/office/agent_panel.py — inject the resolver, keep the default pure
def panel(root=None, *, portrait=None):
    ...
    # in the seat row:
    "portrait": (portrait(seat.get("slug") or "") if portrait else None),
```

```python
# voicebridge/qubit_compat.py
def h_office_theme(cfg, q, b, p):
    from .office import theme as T
    out = T.theme_payload(cfg)
    return _ok(schema="qubit-office-theme-payload/1", **out)

# in h_office_habblaud_agents_panel:
from .office import theme as T
out = PANEL.panel(portrait=lambda slug: T.portrait_rel(cfg, slug))

_r("GET", r"/api/office/theme", "h_office_theme")
```

Add `"/api/office/theme"` to `QUBIT_READ_AUTHORITY` in `voicebridge/webui.py`.

- [ ] **Step 4: Run tests + panel regression**

Run: `scripts/run_tests.sh tests/test_office_theme_serve.py tests/test_office_agents_panel.py -q` → PASS (13+3).

- [ ] **Step 5: Commit**

```bash
git add voicebridge/office/theme.py voicebridge/office/agent_panel.py \
        voicebridge/qubit_compat.py voicebridge/webui.py tests/test_office_theme_serve.py
git commit -m "office(theme): serve the active theme and per-seat portrait paths"
```

---

### Task 4: Portraits in the Agents panel (host React)

**Files:**
- Modify: `frontend/qubit-app/plugins/qubit-office/game/app/agents-panel.ts` (types + fetch)
- Modify: `frontend/qubit-app/plugins/qubit-office/game/app/AgentsPanel.tsx`
- Test: `frontend/qubit-app/plugins/qubit-office/game/app/agents-panel.test.ts`
- Test: `frontend/qubit-web/src/lib/office-theme.test.ts` (jsdom)

**Interfaces:**
- Consumes: `seat.portrait`, `GET /agent/api/office/theme` payload.
- Produces: `officeThemeUrl(base, id)`; seat row renders `<img class="agents-panel__portrait">` when `portrait` is set.

- [ ] **Step 1: Failing test**

```ts
// agents-panel.test.ts (added case)
it("keeps the emoji when a seat has no portrait", () => {
  const el = seatRowFor({ slug: "ceo", name: "ceo", seat_type: "ceo",
    supervisor: null, room: "boardroom", emoji: "🙂", color: "",
    division: "boardroom", persona_slug: "ceo", portrait: null });
  expect(el.textContent).toContain("ceo");
  expect(el.querySelector("img")).toBeNull();
});
```

- [ ] **Step 2: Run → FAIL** (`portrait` not in the type; no img branch).

- [ ] **Step 3: Implement**

```tsx
// AgentsPanel seatRow: portrait first, emoji fallback
const portrait = seat.portrait
  ? `${themeBase(props.base)}/${seat.portrait}`
  : null;
R.createElement(portrait ? "img" : "span", {
  key: "e",
  className: portrait ? "agents-panel__portrait" : "agents-panel__emoji",
  alt: portrait ? `${seat.name} portrait` : "",
  src: portrait ?? undefined,
  "aria-hidden": portrait ? undefined : "true",
  onError: (e: { currentTarget: HTMLImageElement }) => {
    e.currentTarget.replaceWith(Object.assign(document.createElement("span"),
      { className: "agents-panel__emoji", textContent: seat.emoji || "•" }));
  },
}, portrait ? undefined : (seat.emoji || "•"))
```

- [ ] **Step 4: Run tests**

Run: `cd frontend/qubit-web && npx vitest run --config vitest.office-app.config.ts` → PASS (all files).
Run: `npx vitest run src/lib/office-theme.test.ts` → PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/qubit-app/plugins/qubit-office/game/app/AgentsPanel.tsx \
        frontend/qubit-app/plugins/qubit-office/game/app/agents-panel.ts \
        frontend/qubit-app/plugins/qubit-office/game/app/*.test.ts \
        frontend/qubit-web/src/lib/office-theme.test.ts
git commit -m "office(theme): render agent portraits with an emoji fallback"
```

---

### Task 5: Host chrome (docks + injected menu buttons) from `theme.css`

**Files:**
- Modify: `frontend/qubit-app/plugins/qubit-office/game/app/office-menu.ts` (add `office-theme-btn` class)
- Create: `frontend/qubit-app/plugins/qubit-office/game/app/office-theme.ts`
- Modify: `OfficeAppShell.tsx` (apply host CSS class + link)
- Test: `frontend/qubit-web/src/lib/office-theme.test.ts`

**Interfaces:**
- Produces: `loadOfficeTheme(fetchImpl, base) -> Promise<OfficeTheme | null>`, `officeThemeClass(id) -> "office-theme-<id>"`, `applyHostTheme(doc, theme)`.

- [ ] **Step 1: Failing test**

```ts
it("adds the theme class and stylesheet only for a non-classic theme", async () => {
  document.head.innerHTML = "";
  await applyHostTheme(document, { id: "limezu", css: "/x/theme.css" } as never);
  expect(document.documentElement.classList.contains("office-theme-limezu")).toBe(true);
  expect(document.querySelector('link[data-office-theme="limezu"]')).not.toBeNull();
});
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement** the loader (one fetch, `cache: "no-store"`), the class application, and a `<link rel="stylesheet" data-office-theme>` appended once (removed on unmount). In `OfficeAppShell`, wrap the shell root with `className={["office-game", themeClass].join(" ")}` and pass the theme to `AgentsPanel`.

- [ ] **Step 4: Run tests** (`npx vitest run --config vitest.office-app.config.ts` + `src/lib/office-theme.test.ts`).

- [ ] **Step 5: Commit**

---

### Task 6: Append-only theme CSS inside the vendored iframe

**Files:**
- Modify: `frontend/qubit-app/plugins/qubit-office/game/app/office-theme.ts`
- Modify: `OfficeAppShell.tsx` (on iframe load, after menu install)
- Test: `frontend/qubit-web/src/lib/office-theme.test.ts`

**Interfaces:**
- Produces: `injectIframeTheme(win, cssText) -> () => void` (idempotent, returns disposer).

- [ ] **Step 1: Failing test** with jsdom: inject twice, assert exactly one `<style data-office-theme>`; dispose removes it; no `<`/`>` in the accepted CSS (guard strips `@import` and absolute `url(http...)`).

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement** the sanitiser + injection; call it after `installOfficeMenuButtons` in `onLoad`; dispose next to `menuRef.current?.dispose()`.

- [ ] **Step 4: Regression test for the refresh fix:** with a fake parent rerender, assert the iframe `src` is set once and the style node identity is stable.

- [ ] **Step 5: Commit**

---

### Task 7: `qubit office theme` CLI (list / validate / install / export)

**Files:**
- Create: `voicebridge/office/theme_cli.py`
- Modify: `voicebridge/cli.py` (register `office` family; voice alias optional)
- Test: `tests/test_office_theme_cli.py`

**Interfaces:**
- Produces: `cmd_theme(args) -> int`; subcommands `list`, `validate DIR`, `install DIR [--force]`, `export ID OUT.zip`; exit 2 on validation failure.

- [ ] **Step 1: Failing tests**

```python
def test_validate_reports_missing_credit(tmp_path, capsys):
    from voicebridge.office.theme_cli import main
    _pack(tmp_path / "demo", credit="")
    assert main(["validate", str(tmp_path / "demo")]) == 2
    assert "credit" in capsys.readouterr().out

def test_install_copies_into_the_user_home(tmp_path, monkeypatch):
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "home"))
    assert main(["install", str(_pack(tmp_path / "demo"))]) == 0
    assert (tmp_path / "home/office/themes/demo/theme.json").is_file()
    assert main(["install", str(_pack(tmp_path / "demo"))]) == 2  # exists, no --force
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement** the CLI (no network; `install --force` overwrites; `export` zips with `theme.json` first) and wire `qubit office ...` in `cli.py` following the existing family pattern.

- [ ] **Step 4: Run** `scripts/run_tests.sh tests/test_office_theme_cli.py -q` → PASS.

- [ ] **Step 5: Commit**

---

### Task 8: Share docs, catalog entry, and licence guards

**Files:**
- Create: `docs/OFFICE-UI-THEMES.md`
- Modify: `THIRD_PARTY_NOTICES.md` (add the two new packs: `modernuserinterface-win.zip`, `Portrait_Generator_1.5.0`)
- Modify: `tests/test_office_assets_vendored.py` (no raw packs; every committed theme has credit+license)
- Create: `qubit_runtime/plugin-catalog/office-theme-limezu.yaml` (catalog row, `requires_env: []`)

- [ ] **Step 1: Failing guard test**

```python
def test_no_raw_asset_archives_in_the_office_tree():
    bad = [p for p in (REPO / "frontend/qubit-web/public/plugins/qubit-office").rglob("*")
           if p.suffix.lower() in {".zip", ".exe"}]
    assert not bad, bad

def test_every_committed_theme_has_credit_and_license():
    from voicebridge.office import theme as T
    for manifest in (REPO / "frontend/qubit-web/public/plugins/qubit-office/themes").glob("*/theme.json"):
        problems = T.validate_theme(manifest.parent)
        assert not problems, problems
```

- [ ] **Step 2: Run → FAIL** (new packs unrecorded / guard absent).

- [ ] **Step 3: Write `/docs/OFFICE-UI-THEMES.md`** with: the folder format, the two install locations (`$QUBIT_HOME/office/themes`, committed pack), `qubit office theme` usage, the licence rules (credit, no redistribution, no NFT), how to build (`scripts/office_theme_build.py`), how to share (zip the folder, hash it, add a catalog row), and the `office.theme` / `QUBIT_OFFICE_THEME` switches. Add the two new pack entries + sha256 to `THIRD_PARTY_NOTICES.md` and the catalog row.

- [ ] **Step 4: Run** `scripts/run_tests.sh tests/test_office_assets_vendored.py tests/test_office_theme.py -q` → PASS.

- [ ] **Step 5: Commit**

---

# Part B — Habblaud sprite provider (opt-in, separate worktree)

**Preconditions:** Part A shipped; `office.theme=limezu` renders portraits/UI correctly on the live page; no regressions in the refresh/menu tests. Start only after the owner approves Part A.

### Task 9: Prepare the pinned Habblaud source worktree

- [ ] `git worktree add /home/trick/qubit-worktrees/habblaud-theme 4ccce2537472fe5a8d432f06974494d2e778ff15` in a clone of `marmottajr/habblaud` (do not touch `/home/trick/Projects/habblaud`).
- [ ] `npm ci && npm run build` → baseline `dist/` matches the currently vendored hashes (`scripts/verify_habblaud_revendor.py` against the worktree).
- [ ] Commit nothing in Qubit yet; record the baseline hash in `docs/OPEN_THREADS.md`.

### Task 10: Image sprite provider in the Habblaud art layer

**Files (in the Habblaud worktree):**
- Create: `client/src/art/theme-provider.ts`
- Modify: `client/src/art/furniture/index.ts` (consult provider; fall back to procedural)
- Create: `client/src/art/theme-map.json` (v1: `desk`, `office_chair`, `bookshelf`, `plant_tall`, `sofa`, `coffee_table`)

- [ ] **Step 1: Failing unit test** (`client/src/art/theme-provider.test.ts`, vitest): with a fake `Image`, `providerSprite("desk","wood")` returns a `BufSprite` whose `buf` is the PNG pixels at the mapped anchor; an unmapped kind returns `null` so `renderFurniture` stays procedural.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** `setThemeProvider(provider | null)`, lazy `Image` decode → `PixelBuf`, mapping loader from `theme-map.json`, and a provider hook in `renderFurniture` before `build()`.
- [ ] **Step 4: Run the Habblaud art suite** (`npm test -- art`) → PASS, including an explicit "no provider ⇒ byte-identical procedural sprites" test.
- [ ] **Step 5: Commit in the Habblaud worktree.**

### Task 11: Inject the theme into the app document (server)

- [ ] Modify `voicebridge/office/theme.py::theme_payload` consumers: the Habblaud document injection (`voicebridge/webui.py` / `voicebridge/qubit_compat.py::_serve_office_client_api`) writes `window.__QUBIT_OFFICE_THEME__ = {id, assetBase}` only when the active theme is non-classic.
- [ ] Test: classic ⇒ the global is absent; `limezu` ⇒ it is present and points at `/plugins/qubit-office/themes/limezu`.
- [ ] Commit.

### Task 12: Build, vendor, verify, and gate

- [ ] Build the patched Habblaud (`npm run build`), run `python3 scripts/vendor_habblaud.py --src /home/trick/qubit-worktrees/habblaud-theme`, then `python3 scripts/verify_habblaud_revendor.py`.
- [ ] Update `PROVENANCE.json` note with the local-change list (provider + map), keeping upstream commit `4ccce253…` and MIT.
- [ ] Vendored tests: `scripts/run_tests.sh tests/test_office_vendor_habblaud.py tests/test_office_habblaud_compat.py -q` → PASS.
- [ ] Browser: classic before/after screenshots identical; `?theme=limezu` shows mapped sprites; HUD marker survives 15 s; menu intact.
- [ ] Document the rollback: `office.theme: classic` (no rebuild needed) and `git revert` of the vendor commit.

---

## Verification Matrix (run once before calling Parts A/B done)

| Check | Command | Expected |
|---|---|---|
| Theme unit | `scripts/run_tests.sh tests/test_office_theme.py tests/test_office_theme_build.py tests/test_office_theme_serve.py tests/test_office_theme_cli.py -q` | all pass |
| Agents panel (existing) | `scripts/run_tests.sh tests/test_office_agents_panel.py -q` | 13 pass |
| Assets guard | `scripts/run_tests.sh tests/test_office_assets_vendored.py -q` | pass, no raw packs |
| Office app Vitest | `cd frontend/qubit-web && npx vitest run --config vitest.office-app.config.ts` | all files pass |
| Theme DOM/Vitest | `npx vitest run src/lib/office-theme.test.ts` | passes |
| Build | `npx vite build --config vite.office-game.config.ts` | succeeds; bundle rebuilt |
| Reproducibility | `python3 scripts/office_theme_build.py --check` | byte-identical |
| Live classic | browser `/qubit-office`, no `office.theme` | identical look, 0 new requests, HUD stays mounted 15 s |
| Live themed | `office.theme: limezu` (or `?theme=limezu`) | portraits show; credit visible; menu order unchanged |
| Rollback | set `QUBIT_OFFICE_THEME=classic` | immediate classic look, no restart of assets |

## Rollback

- Part A: one config value (`office.theme: classic`) or `QUBIT_OFFICE_THEME=classic`; delete the pack directory; no vendored files were touched.
- Part B: `git revert` the vendor commit plus the injection; the procedural renderer is untouched and is the fallback whenever the provider is null or a kind is unmapped.

## Self-Review (completed)

- **Spec coverage:** licence/provenance → Tasks 1, 8; customization → Tasks 1–2, 7; shareability → Tasks 7–8; portraits → Tasks 2–4; UI chrome → Tasks 5–6; room art → Part B; no-break defaults → Global Constraints + Review Focus + Verification Matrix.
- **Placeholders:** every code step carries real code or a real command; Task 2b names the exact source folders and the `--check` contract.
- **Type consistency:** `Theme`/`theme_payload` fields match across Tasks 1, 3, 5; `portrait` is a string-or-null everywhere; `injectIframeTheme` returns a disposer used in the same task.
- **Review Focus tests:** malformed pack (Task 1), portrait fallback (Task 4), zero-change default (Tasks 5–6 tests), no refresh regression (Task 6 step 4 + matrix), CSS sanitiser (Task 6).

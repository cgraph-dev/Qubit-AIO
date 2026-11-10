"""A1 — integrity of the vendored Office art tree.

The vendored tree is written to TWO locations (see `scripts/vendor_office_assets.py`):

* `frontend/qubit-web/public/plugins/qubit-office/assets/` — the durable,
  git-tracked source the dashboard build copies into dist; and
* `frontend/qubit-dist/plugins/qubit-office/assets/` — the served copy, reachable
  by the browser at `/plugins/qubit-office/assets/<path>`
  (`90-CORRECTIONS.md` §C1: the app tree 404s a PNG; the dist tree serves it).

These tests assert BOTH trees are exactly what the vendoring script produces, so a
manual edit to a staged PNG fails CI instead of silently diverging:

* every path in each `MANIFEST.json` exists and hashes as recorded;
* no file under `assets/` is absent from the manifest;
* the run is reproducible (the script's plan == what is on disk);
* no celebrity/meme sprite family is present (owner decision O1/C4);
* the vendored tree is under the 100 MB cap.

Task 9 extends this module with the theme/asset guards: no archives or theme
atlases are committed, every committed ``theme.recipe.json`` validates, the
vendored Habblaud tree still matches ``PROVENANCE.json`` (``--check``), the
served plugin bundle carries no operator/build path, ``LICENSE-ASSETS`` names
the art credit, the active theme payload keeps credit and drops the file map,
the theme style is office-only, the ``.ui-root`` drift report is warning-only,
and the contrast matrix clears both office surfaces.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

# Import the vendoring module's plan so the test and the script cannot drift.
_VENDOR_PATH = REPO / "scripts" / "vendor_office_assets.py"


def _load_vendor():
    spec = importlib.util.spec_from_file_location("vendor_office_assets", _VENDOR_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


VENDOR = _load_vendor()

#: Both written trees: (label, assets root).
TREES = [(r.relative_to(REPO).as_posix(), r) for r in VENDOR.TARGETS]

#: The served tree the browser actually hits.
SERVED_ROOT = VENDOR.ASSET_ROOT

#: Families that must NEVER appear — celebrity/meme cosplay sets (C4/O1).
FORBIDDEN_FAMILIES = [
    "approved_elonmusk",
    "approved_harrypotter",
    "approved_heisenberg",
    "approved_xenomorph",
    "approved_samaltman",
    "approved_tonymontana",
    "approved_tylerdurden",
    "approved_ricksanchez",
    "approved_pepe",
    "approved_masterchief",
    "approved_morpheus",
    "approved_catwoman",
    "approved_ghostface",
    "approved_robocop",
    "approved_ultron",
]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@pytest.fixture(scope="module")
def served_manifest() -> dict:
    # Prefer the served dist copy; fall back to the git-tracked durable copy
    # when dist has not been built yet (a clean checkout has no dist/).
    for _label, root in reversed(TREES):
        mp = root / "MANIFEST.json"
        if mp.is_file():
            return json.loads(mp.read_text(encoding="utf-8"))
    pytest.fail("no vendored Office asset manifest found — run "
                "scripts/vendor_office_assets.py")


def test_both_trees_exist():
    durable = VENDOR.TARGETS[0]
    assert durable.is_dir(), (
        f"durable vendored asset tree absent: "
        f"{durable.relative_to(REPO).as_posix()}")
    for label, root in TREES:
        if root.is_dir():
            continue
        # The served dist tree is a regenerable build output; a clean clone
        # legitimately has no dist/ until build_dashboard.sh runs.
        assert root == VENDOR.TARGETS[-1], (
            f"vendored asset tree absent: {label}")


@pytest.mark.parametrize("label,root", TREES, ids=[t[0] for t in TREES])
def test_manifest_schema(label, root):
    if not root.is_dir():
        pytest.skip(f"{label} absent (clean checkout / unbuilt dashboard)")
    m = json.loads((root / "MANIFEST.json").read_text(encoding="utf-8"))
    assert m["schema"] == "qubit-office-assets/1"
    assert m["asset_base_url"] == "/plugins/qubit-office"
    assert m["file_count"] == len(m["files"])
    assert m["file_count"] > 0


@pytest.mark.parametrize("label,root", TREES, ids=[t[0] for t in TREES])
def test_every_manifest_file_exists_and_hashes(label, root):
    """The core contract: recorded path exists and matches its recorded sha256."""
    if not root.is_dir():
        pytest.skip(f"{label} absent (clean checkout / unbuilt dashboard)")
    m = json.loads((root / "MANIFEST.json").read_text(encoding="utf-8"))
    bad = []
    for entry in m["files"]:
        f = root / entry["path"]
        if not f.is_file():
            bad.append(f"missing: {entry['path']}")
            continue
        if _sha256(f) != entry["sha256"]:
            bad.append(f"hash mismatch: {entry['path']}")
        elif f.stat().st_size != entry["bytes"]:
            bad.append(f"size mismatch: {entry['path']}")
    assert not bad, f"{label} diverges from manifest:\n  " + "\n  ".join(bad)


@pytest.mark.parametrize("label,root", TREES, ids=[t[0] for t in TREES])
def test_no_unlisted_files_present(label, root):
    """Any file under assets/ not in the manifest is a drop or an unnoticed add."""
    if not root.is_dir():
        pytest.skip(f"{label} absent (clean checkout / unbuilt dashboard)")
    m = json.loads((root / "MANIFEST.json").read_text(encoding="utf-8"))
    listed = {e["path"] for e in m["files"]}
    on_disk = {
        f.relative_to(root).as_posix()
        for f in root.rglob("*")
        if f.is_file() and f.name != "MANIFEST.json"
    }
    extra = on_disk - listed
    assert not extra, f"{label}: unlisted files: {sorted(extra)[:20]}"


def test_reproducible_from_script_plan(served_manifest):
    """Each on-disk tree equals exactly what the vendoring script would produce."""
    refs = VENDOR.DEFAULT_REFS
    if not refs.is_dir():
        pytest.skip(f"refs staging absent: {refs}")
    plan = VENDOR.build_plan(refs)
    expected = {dest for _, dest in plan}
    expected_bytes = sum(s.stat().st_size for s, _ in plan)
    for label, root in TREES:
        if not root.is_dir():
            continue
        actual = {
            f.relative_to(root).as_posix()
            for f in root.rglob("*")
            if f.is_file() and f.name != "MANIFEST.json"
        }
        assert actual == expected, (
            f"{label}: only in plan: {sorted(expected - actual)[:20]}; "
            f"only on disk: {sorted(actual - expected)[:20]}"
        )
    assert served_manifest["total_bytes"] == expected_bytes


def test_no_celebrity_families_present(served_manifest):
    """Owner decision O1/C4 — neutral families only, no meme/cosplay skins."""
    paths = " ".join(e["path"] for e in served_manifest["files"])
    for family in FORBIDDEN_FAMILIES:
        assert family not in paths, f"forbidden sprite family vendored: {family}"


def test_only_approved_neutral_sprite_families(served_manifest):
    """Every vendored sprite dir is on the neutral allowlist."""
    allowed = set(VENDOR.SPRITE_FAMILIES)
    seen = set()
    for entry in served_manifest["files"]:
        if entry["path"].startswith("sprites/"):
            seen.add(entry["path"].split("/")[1])
    assert seen, "no sprite families vendored at all"
    unexpected = seen - allowed
    assert not unexpected, f"unexpected sprite families: {sorted(unexpected)}"
    assert seen == allowed, f"missing families: {sorted(allowed - seen)}"


def test_under_size_cap(served_manifest):
    """Owner decision: the vendored tree stays under the 100 MB cap."""
    assert served_manifest["total_bytes"] <= VENDOR.MAX_TOTAL_BYTES
    assert served_manifest["total_bytes"] < 50 * 1024 * 1024


def test_durable_public_tree_not_gitignored():
    """The durable tree must be committed so a rebuild does not drop the art.

    `frontend/qubit-dist/` is a regenerable build output (gitignored), so the
    git-tracked `frontend/qubit-web/public/plugins/...` tree is what survives; a
    rebuild copies it into dist. If THAT tree were ignored, the vendored art
    would vanish on the next `build_dashboard.sh`.
    """
    import subprocess

    durable = VENDOR.TARGETS[0]  # public/plugins/qubit-office/assets
    rel = durable.relative_to(REPO).as_posix()
    proc = subprocess.run(
        ["git", "check-ignore", "-q", f"{rel}/MANIFEST.json"],
        cwd=str(REPO),
        capture_output=True,
    )
    # git check-ignore -q exits 1 when NOT ignored (good), 0 when ignored (bad).
    assert proc.returncode != 0, f"{rel}/ is excluded by .gitignore"


# ==========================================================================
# Task 9 — theme/asset guards (no art committed, no drift, no build paths)
# ==========================================================================

OFFICE_TREE_ROOTS = (
    REPO / "frontend" / "qubit-web" / "public" / "plugins" / "qubit-office",
    REPO / "frontend" / "qubit-app" / "plugins" / "qubit-office",
)
SERVED_PLUGIN_ARTIFACTS = (
    REPO / "frontend" / "qubit-app" / "plugins" / "qubit-office"
    / "qubit-office.game.js",
    REPO / "frontend" / "qubit-app" / "plugins" / "qubit-office"
    / "qubit-office.game.css",
)
THEME_ART_SUFFIXES = {".png", ".webp", ".jpg", ".jpeg", ".gif"}
OPERATOR_PATH_NEEDLES = ("/home/", "Projects/Repos/", "C:\\Users")


def _git_files() -> list[str]:
    """Every committed path, NUL-split so spaces/newlines stay intact."""
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=str(REPO), capture_output=True, check=True,
    ).stdout
    return [p for p in out.decode("utf-8", "replace").split("\0") if p]


def _is_theme_json(path: str) -> bool:
    p = Path(path)
    return (
        p.name in ("theme.json", "theme.recipe.json")
        or ("themes" in p.parts and p.suffix == ".json")
    )


def _write_theme_pack(home: Path) -> Path:
    """Write a complete, valid local pack under the throwaway home.

    Bytes are arbitrary: the loader validates size/sha256, not pixels, so no
    art or Pillow is needed here.
    """
    root = home / "office" / "themes" / "demo"
    root.mkdir(parents=True, exist_ok=True)

    def _write(relative: str, data: bytes) -> dict:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}

    files = {
        "portraits/atlas32.png": _write("portraits/atlas32.png", b"PNG-PORTRAITS"),
        "bodies/atlas32.png": _write("bodies/atlas32.png", b"PNG-BODIES"),
        "portraits/index.json": _write(
            "portraits/index.json",
            json.dumps({"schema": 1, "size": 32, "cols": 20, "rows": 15,
                        "cells": {"ceo": [0, 0]}}).encode()),
        "bodies/index.json": _write(
            "bodies/index.json",
            json.dumps({"schema": 1, "size": 32, "frame": {"w": 32, "h": 64},
                        "cols": 20, "rows": 15,
                        "cells": {"ceo": [0, 0]}}).encode()),
        "agents.json": _write(
            "agents.json",
            json.dumps({"schema": 1, "templateVersion": 1,
                        "recipes": {"ceo": {"skin": 2, "eyes": 1, "hair": 1,
                                            "outfit": 1, "accessory": 1}}}).encode()),
    }
    manifest = {
        "format": "qubit-office-theme",
        "schema": 1,
        "id": "demo",
        "label": "Demo",
        "version": "1.0.0",
        "credit": "LimeZu — https://limezu.itch.io",
        "license": "LimeZu licence; redistribution prohibited",
        "source": "owner purchase, 2026-11-09",
        "template_version": 1,
        "tokens": {
            "color-surface": "#0d0d1a",
            "color-surface-raised": "#161a23",
            "color-surface-hover": "#1d2230",
            "color-text-primary": "#e9edf7",
            "color-text-muted": "#8a93a8",
            "color-border": "#64748b",
            "color-accent": "#7cc8ff",
            "radius-control": "4px",
        },
        "slices": {},
        "files": files,
        "limits": {"max_total_bytes": 10_000_000, "max_files": 20},
        "portraits": {"atlas": "portraits/atlas32.png",
                      "index": "portraits/index.json",
                      "size": 32, "cols": 20, "rows": 15},
        "bodies": {"atlas": "bodies/atlas32.png",
                   "index": "bodies/index.json",
                   "size": 32, "frame": {"w": 32, "h": 64},
                   "cols": 20, "rows": 15},
        "parts": {"skin": 9, "eyes": 7, "hair": 200, "outfit": 42,
                  "accessory": 85},
    }
    (root / "theme.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


def test_no_archives_or_executables_under_the_office_public_tree():
    """A purchased pack is never committed, and no installer rides with it."""
    bad: list[str] = []
    for root in OFFICE_TREE_ROOTS:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.suffix.lower() in {".zip", ".exe"}:
                bad.append(path.relative_to(REPO).as_posix())
    assert not bad, f"archives/executables under the office tree: {bad}"


def test_no_theme_art_under_any_committed_themes_directory():
    """Themes ship recipes, never atlases: art stays on the owner's machine."""
    bad = [
        rel for rel in _git_files()
        if "themes" in Path(rel).parts
        and Path(rel).suffix.lower() in THEME_ART_SUFFIXES
    ]
    assert not bad, f"committed theme art (recipes only): {bad}"


def test_every_committed_theme_recipe_validates():
    from voicebridge.office import theme as T

    problems: dict[str, list[str]] = {}
    for rel in _git_files():
        if Path(rel).name != "theme.recipe.json":
            continue
        document = json.loads((REPO / rel).read_text(encoding="utf-8"))
        found = T.validate_recipe(document)
        if found:
            problems[rel] = found
    assert not problems, f"invalid committed theme recipes: {problems}"


def test_committed_theme_json_has_no_operator_paths():
    """A recipe is shareable; an operator's home path is not part of it."""
    bad: dict[str, list[str]] = {}
    for rel in _git_files():
        if not _is_theme_json(rel):
            continue
        text = (REPO / rel).read_text(encoding="utf-8", errors="replace")
        hits = [needle for needle in (*OPERATOR_PATH_NEEDLES, "~/")
                if needle in text]
        if "\\\\" in text:
            hits.append("\\\\")
        if hits:
            bad[rel] = hits
    assert not bad, f"operator/build paths in committed theme JSON: {bad}"


def test_vendor_habblaud_check_exits_zero():
    """Habblaud is read-only: the vendored tree must match its provenance."""
    script = REPO / "scripts" / "vendor_habblaud.py"
    tree = (REPO / "frontend" / "qubit-app" / "plugins" / "qubit-office"
            / "habblaud")
    if not script.is_file():
        pytest.skip(f"vendor script absent: {script.relative_to(REPO)}")
    if not (tree / "PROVENANCE.json").is_file():
        pytest.skip("vendored Habblaud tree absent (not vendored in this tree)")
    proc = subprocess.run(
        [sys.executable, str(script), "--check"],
        cwd=str(REPO), capture_output=True, text=True, timeout=180,
    )
    assert proc.returncode == 0, (
        "scripts/vendor_habblaud.py --check failed:\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")


def test_served_plugin_artifacts_have_no_operator_or_build_paths():
    """The committed entry is portable: no builder home, no module comment."""
    bad: dict[str, list[str]] = {}
    for path in SERVED_PLUGIN_ARTIFACTS:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        hits = [needle for needle in OPERATOR_PATH_NEEDLES if needle in text]
        if re.search(r"(?m)^\s*//[^\n]*(?:\.\./){2,}", text):
            hits.append("leading ../.. module comment")
        if hits:
            bad[path.relative_to(REPO).as_posix()] = hits
    assert not bad, f"build/operator paths in served plugin artifacts: {bad}"


def test_license_assets_exists_and_names_the_art_credit():
    path = REPO / "LICENSE-ASSETS"
    assert path.is_file(), "LICENSE-ASSETS is missing"
    text = path.read_text(encoding="utf-8")
    assert "LimeZu" in text
    assert "https://limezu.itch.io" in text
    assert "redistribut" in text.lower()
    for needle in OPERATOR_PATH_NEEDLES:
        assert needle not in text, f"operator path in LICENSE-ASSETS: {needle}"


def test_active_theme_payload_keeps_credit_and_hides_the_file_map(
        tmp_path, monkeypatch):
    """The payload carries credit/licence, never the pack's file/hash map."""
    from voicebridge.office import agent_panel as PANEL
    from voicebridge.office import theme as T

    home = tmp_path / "home"
    monkeypatch.setenv("QUBIT_HOME", str(home))
    monkeypatch.delenv("QUBIT_OFFICE_THEME", raising=False)
    _write_theme_pack(home)

    payload = T.theme_payload({}, requested="demo")
    assert payload["active"] is True
    assert payload["id"] == "demo"
    assert payload["credit"] == "LimeZu — https://limezu.itch.io"
    assert payload["license"]
    assert "files" not in payload
    assert payload["atlas"].startswith("/plugins/qubit-office/themes/demo/")

    panel = PANEL.panel()
    assert panel["ok"] is True, panel.get("detail")
    assert panel["counts"]["headcount"] == 283


# --------------------------------------------------------------------------
# The theme style is the office's, never another plugin app's
# --------------------------------------------------------------------------
def _make_plugin_app_handler(monkeypatch, tmp_path: Path, sub: str):
    from voicebridge import resources
    from voicebridge import webui as W

    plugin_root = tmp_path / "plugins"
    for name in ("qubit-office", "other"):
        document = (
            "<!doctype html><html><head>"
            '<link rel="stylesheet" href="/assets/app.css">'
            "</head><body>app</body></html>"
        )
        (plugin_root / name / "habblaud").mkdir(parents=True, exist_ok=True)
        (plugin_root / name / "habblaud" / "index.html").write_text(
            document, encoding="utf-8")
    (plugin_root / "other" / "app").mkdir(parents=True)
    (plugin_root / "other" / "app" / "index.html").write_text(
        "<html><head></head><body>other</body></html>", encoding="utf-8")

    real = resources.resource_path
    monkeypatch.setattr(
        resources, "resource_path",
        lambda name: plugin_root if name == "frontend/qubit-app/plugins"
        else real(name))

    handler = object.__new__(W.WebUIHandler)
    handler.path = "/plugin-apps/" + sub
    handler.headers = {"Host": "127.0.0.1"}
    handler.client_address = ("127.0.0.1", 51234)
    handler._head_only = False
    captured: dict = {}

    class _App:
        api_token = ""
        allowed_origins: tuple = ()
        _office_nonces: dict = {}
        orch = type("Orch", (), {"cfg": None})()

    def _send(code, body, ctype="text/plain", extra_headers=None,
              cache_control="no-store"):
        captured.update(code=code, body=body, ctype=ctype)

    handler.server = type("S", (), {"app": _App()})()
    handler._send = _send  # type: ignore[method-assign]
    handler._serve_plugin_app(sub)
    return captured


def test_theme_style_is_scoped_to_the_office_plugin_app(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("QUBIT_HOME", str(home))
    monkeypatch.delenv("QUBIT_OFFICE_THEME", raising=False)
    _write_theme_pack(home)

    office = _make_plugin_app_handler(
        monkeypatch, tmp_path / "office", "qubit-office/habblaud/?theme=demo")
    assert office["code"] == 200
    assert b"data-office-theme" in office["body"]

    other = _make_plugin_app_handler(
        monkeypatch, tmp_path / "other", "other/app/?theme=demo")
    assert other["code"] == 200
    assert b"data-office-theme" not in other["body"]


# --------------------------------------------------------------------------
# Drift audit: every .ui-root variable not in ALLOWED_THEME_VARS is reported
# --------------------------------------------------------------------------
def _load_audit_module():
    path = REPO / "scripts" / "office_theme_audit.py"
    spec = importlib.util.spec_from_file_location("office_theme_audit", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_drift_audit_reports_unallowlisted_ui_root_variables():
    audit = _load_audit_module()
    css = """
    .ui-root { --ui-bg: #000; --mystery-layout: 12px; --font-px: monospace; }
    .ui-root * { --ignored-child: 1; }
    .ui-root.has-usage-row { --ignored-state: 2; }
    :where(.ui-root) { --ui-text: #fff; }
    """
    report = audit.collect_drift([css])
    assert report["unallowlisted"] == ["--font-px", "--mystery-layout"]
    assert "--ui-bg" in report["allowed"]
    assert "--ui-text" in report["allowed"]
    assert "--ignored-child" not in report["declared"]
    assert "--ignored-state" not in report["declared"]


def test_drift_audit_on_the_vendored_bundle_is_warning_only(capsys):
    audit = _load_audit_module()
    assert audit.main([]) == 0
    out = capsys.readouterr().out
    assert "warning only" in out or "every .ui-root variable" in out
    # The measured extras are layout tokens, not missing theme colours; naming
    # at least one proves the report actually parsed the bundle.
    assert "--font-px" in out or "--top-h" in out


# --------------------------------------------------------------------------
# Contrast matrix — both office surfaces, text 4.5 / non-text 3.0
# --------------------------------------------------------------------------
MATRIX_TOKENS = {
    "color-surface": "#0d0d1a",
    "color-surface-soft": "#111522",
    "color-surface-raised": "#161a23",
    "color-surface-solid": "#161a23",
    "color-surface-hover": "#1d2230",
    "color-border": "#64748b",
    "color-border-strong": "#94a3b8",
    "color-text-primary": "#e9edf7",
    "color-text-muted": "#8a93a8",
    "color-text-faint": "#7b8598",  # disabled text
    "color-accent": "#7cc8ff",
    "color-accent-soft": "#7cc8ff",
    "radius-panel": "8px",
    "radius-control": "4px",
    "color-status-working": "#38bdf8",
    "color-status-waiting": "#fbbf24",
    "color-status-done": "#4ade80",
    "color-status-idle": "#94a3b8",
    "color-status-offline": "#ef4444",
    "color-status-shell": "#a78bfa",
    "color-level-ok": "#22c55e",
    "color-level-warn": "#f59e0b",
    "color-level-crit": "#ef4444",
}

#: semantic token -> required ratio. Text needs 4.5:1; accents, borders,
#: status/level dots and disabled text are non-text/large and need 3:1.
CONTRAST_FLOORS = {
    "color-text-primary": 4.5,
    "color-text-muted": 3.0,
    "color-text-faint": 3.0,
    "color-accent": 3.0,
    "color-accent-soft": 3.0,
    "color-border": 3.0,
    "color-border-strong": 3.0,
    **{name: 3.0 for name in MATRIX_TOKENS if name.startswith("color-status-")},
    **{name: 3.0 for name in MATRIX_TOKENS if name.startswith("color-level-")},
}


def test_contrast_matrix_passes_on_both_office_surfaces():
    from voicebridge.office import theme as T

    surfaces = {
        "surface": MATRIX_TOKENS["color-surface"],
        "raised": MATRIX_TOKENS["color-surface-raised"],
    }
    failures: list[str] = []
    for name, floor in CONTRAST_FLOORS.items():
        value = MATRIX_TOKENS[name]
        for surface_name, surface in surfaces.items():
            ratio = T.contrast_ratio(value, surface)
            if ratio + 1e-9 < floor:
                failures.append(
                    f"{name} ({value}) vs {surface_name} ({surface}) = "
                    f"{ratio:.2f}:1 < {floor}:1")
    assert not failures, "\n".join(failures)


def test_contrast_matrix_detects_a_low_contrast_token():
    from voicebridge.office import theme as T

    ratio = T.contrast_ratio("#1a1a1a", MATRIX_TOKENS["color-surface"])
    assert ratio < CONTRAST_FLOORS["color-accent"]

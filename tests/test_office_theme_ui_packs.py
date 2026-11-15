"""UI-pack extraction, licence gate and payload gating.

The owner's packs are local art; these tests build tiny synthetic stand-ins
with the same paths/sizes so the selection and failure modes are hermetic.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

import office_theme_ui_packs as U  # noqa: E402
from voicebridge.office import theme as T  # noqa: E402


def _png(path: Path, size: tuple[int, int], color=(40, 80, 120, 255)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", size, color).save(path)


def make_source(root: Path) -> Path:
    """Create the exact pack paths/sizes the extractor expects."""
    _png(root / "DarkAgesUi_v1.0" / "32x32-Tilesheet.png", (384, 352))
    dragon = root / "dragon-huds" / "dragon-huds"
    _png(dragon / "dialogue-box" / "dialogue-box-transparent.png", (184, 80))
    _png(dragon / "line-bars" / "line-bar-health.png", (176, 46))
    _png(dragon / "rounded-bars" / "avatar-frame.png", (80, 80))
    for name in ("quest", "options", "home", "map", "confirm", "close"):
        _png(dragon / "icons" / f"{name}-icon.png", (64, 64))
    _png(root / "Free_Medieval_Fantasy_UI_Pack" / "UI Icons 2" / "icon20.png",
         (32, 32))
    raven = (root / "Free - Raven Fantasy Icons" / "Free - Raven Fantasy Icons"
             / "Separated Files" / "64x64")
    _png(raven / "fc100.png", (64, 64))
    _png(raven / "fc10.png", (64, 64))
    _png(root / "uı-fonts" / "uı-fonts" / "uı-1" / "uı-1-yellow" / "uı-1.png",
         (256, 256))
    spx = (root / "Super Pixel Effects Gigapack (Free Version) v3.2.0"
           / "Super Pixel Effects Gigapack (Free Version)" / "spritesheet"
           / "Symbols" / "symbol_success_001"
           / "symbol_success_001_small_green" / "spritesheet.png")
    _png(spx, (2400, 40))
    return root


def test_selection_maps_each_role_to_the_intended_pack(tmp_path):
    source = make_source(tmp_path)
    selected = U.plan_ui_assets(source)
    assert selected["slices"]["panel"]["pack"] == "darkages"
    assert selected["slices"]["dialog"]["pack"] == "darkages"
    assert selected["slices"]["input"]["ink"] == "dark"
    assert selected["slices"]["panel"]["ink"] == "light"
    assert selected["icons"]["talk"]["pack"] == "medieval"
    assert selected["icons"]["timeline"]["pack"] == "raven"
    assert selected["font"]["pack"] == "fonts"
    assert selected["effects"]["success"]["pack"] == "spx"
    assert selected["packs"] and {p["id"] for p in selected["packs"]} == {
        "darkages", "dragon", "fonts", "medieval", "raven", "spx"}


def test_build_returns_additive_ui_block_and_files(tmp_path):
    source = make_source(tmp_path)
    payloads, ui, roles = U.build_ui_assets(source)
    assert "ui/packs/slices/panel.png" in payloads
    assert "ui/packs/icons/talk.png" in payloads
    assert "ui/packs/fonts/ui-font.png" in payloads
    assert "ui/packs/effects/success.png" in payloads
    assert ui["slices"]["dialog"]["ink"] == "light"
    assert ui["icons"]["talk"]["license_ok"] is True
    assert ui["font"]["cell"] == 32
    assert ui["effects"]["success"]["frames"] == 60
    assert ui["effects"]["success"]["width"] == 40
    assert roles["icon:talk"] == "medieval"
    assert roles["effect:success"] == "spx"


def test_licence_gate_skips_a_blocked_pack(tmp_path):
    source = make_source(tmp_path)
    terms = {key: dict(value) for key, value in U.LICENSES.items()}
    terms["medieval"]["allowed"] = False
    terms["raven"]["allowed"] = False
    selected = U.plan_ui_assets(source, licenses=terms)
    assert "talk" not in selected["icons"]
    assert "timeline" not in selected["icons"]
    assert "warroom" not in selected["icons"]
    assert any(item.startswith("icon:talk") for item in selected["skipped"])
    assert "medieval" not in {p["id"] for p in selected["packs"]}
    assert "raven" not in {p["id"] for p in selected["packs"]}


def test_missing_declared_asset_fails_closed(tmp_path):
    source = make_source(tmp_path)
    (source / "dragon-huds" / "dragon-huds" / "rounded-bars"
     / "avatar-frame.png").unlink()
    with pytest.raises(U.UIPackError, match="asset missing"):
        U.plan_ui_assets(source)


def test_a_missing_pack_is_optional_not_a_dangling_reference(tmp_path):
    source = make_source(tmp_path)
    # Remove the whole Raven pack; the icon roles are skipped, not referenced.
    import shutil
    shutil.rmtree(source / "Free - Raven Fantasy Icons")
    payloads, ui, _roles = U.build_ui_assets(source)
    assert "warroom" not in ui["icons"]
    assert "timeline" not in ui["icons"]
    assert all("icons/warroom" not in name for name in payloads)


def test_merge_is_additive_and_keeps_existing_slices(tmp_path):
    source = make_source(tmp_path)
    payloads, ui, _roles = U.build_ui_assets(source)
    manifest = {
        "id": "limezu",
        "files": {"portraits/atlas.png": {"sha256": "x", "bytes": 1}},
        "ui": {"slices": {"custom": {"src": "custom.png"}}},
        "limits": {"max_files": 4, "max_total_bytes": 10},
    }
    merged = U.merge_manifest(manifest, payloads, ui)
    assert merged["ui"]["slices"]["custom"] == {"src": "custom.png"}
    assert merged["ui"]["slices"]["panel"]["src"].endswith("panel.png")
    assert "ui/packs/slices/panel.png" in merged["files"]
    assert merged["files"]["portraits/atlas.png"] == {"sha256": "x", "bytes": 1}
    assert merged["limits"]["max_files"] >= len(merged["files"])


def test_augment_refuses_an_in_repo_output(tmp_path):
    source = make_source(tmp_path)
    theme_dir = tmp_path / "theme"
    (theme_dir).mkdir()
    (theme_dir / "theme.json").write_text("{}", encoding="utf-8")
    with pytest.raises(U.UIPackError, match="inside the repository"):
        U.augment_theme(theme_dir, source, ROOT / "bad-theme", validate=False)


# ---------------------------------------------------------------------------
# Server payload / validator gate
# ---------------------------------------------------------------------------

def _theme_with_ui() -> T.Theme:
    return T.Theme(
        root=Path("."), id="demo", label="Demo", version="1.0.0",
        credit="c", license="l", source="", template_version=1,
        ui={
            "slices": {
                "dialog": {
                    "src": "ui/packs/slices/dialog.png",
                    "slice": [20, 20, 20, 20], "width": "10px",
                    "repeat": "stretch", "ink": "light", "pack": "dragon",
                },
            },
            "icons": {
                "talk": {
                    "src": "ui/packs/icons/talk.png", "width": 32,
                    "height": 32, "license_ok": True, "credit": "c",
                    "license": "l", "pack": "medieval",
                },
                "blocked": {
                    "src": "ui/packs/icons/blocked.png", "width": 32,
                    "height": 32, "license_ok": False, "credit": "c",
                    "license": "l",
                },
            },
            "font": {
                "src": "ui/packs/fonts/ui-font.png", "cell": 32, "cols": 8,
                "rows": 8, "glyphs": "AB", "license_ok": True,
                "credit": "c", "license": "l",
            },
            "effects": {
                "success": {
                    "src": "ui/packs/effects/success.png", "frames": 60,
                    "fps": 15, "width": 40, "height": 40,
                    "frames_per_row": 60, "license_ok": True,
                    "credit": "c", "license": "l",
                },
            },
            "packs": [{
                "id": "dragon", "label": "Dragon", "author": "Kibyra",
                "credit": "c", "license": "l", "license_ok": True,
            }],
        },
    )


def test_payload_resolves_urls_and_omits_blocked_assets():
    payload = T._ui_payload(_theme_with_ui())
    assert payload["slices"]["dialog"]["ink"] == "light"
    assert payload["slices"]["dialog"]["src"].startswith(
        "/plugins/qubit-office/themes/demo/ui/packs/slices/dialog.png?v=")
    assert payload["icons"]["talk"]["licenseOk"] is True
    assert "blocked" not in payload["icons"]
    assert payload["font"]["src"].startswith("/plugins/")
    assert payload["effects"]["success"]["framesPerRow"] == 60
    assert payload["packs"][0]["licenseOk"] is True


def test_validator_accepts_declared_files_and_rejects_a_missing_one():
    files = {
        "ui/packs/slices/dialog.png",
        "ui/packs/icons/talk.png",
        "ui/packs/icons/blocked.png",
        "ui/packs/fonts/ui-font.png",
        "ui/packs/effects/success.png",
    }
    theme = _theme_with_ui()
    assert T._validate_ui_full(theme.ui, files) == []
    broken = set(files)
    broken.discard("ui/packs/icons/talk.png")
    problems = T._validate_ui_full(theme.ui, broken)
    assert any("not a declared file" in problem for problem in problems)


def test_validator_requires_licence_metadata_and_valid_ink():
    theme = _theme_with_ui()
    ui = dict(theme.ui)
    icons = dict(ui["icons"])
    icons["talk"] = {key: value for key, value in icons["talk"].items()
                     if key != "license"}
    ui["icons"] = icons
    slices = dict(ui["slices"])
    slices["dialog"] = {**slices["dialog"], "ink": "neon"}
    ui["slices"] = slices
    problems = T._validate_ui_full(ui, None)
    assert any("ui.icons.talk.license" in problem for problem in problems)
    assert any("ink must be light or dark" in problem for problem in problems)

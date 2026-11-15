"""Full-pack room blueprints and character animation atlases.

These pin the additive half of the LimeZu theme: the builder emits room
tile/window files and one animation atlas per clip (row per persona), and the
shareable recipe carries coordinates only. Hermetic: synthetic sheets, no paid
art, no network.
"""
from __future__ import annotations

import pytest
from PIL import Image, ImageDraw

from scripts import office_theme_build as B
from voicebridge.office import theme as T

pytestmark = pytest.mark.unit


def _filled_sheet(w: int, h: int, rgba: tuple[int, int, int, int]) -> Image.Image:
    """A non-empty sheet with transparent padding (the QA coverage band)."""
    image = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle([2, 2, w - 3, h - 3], fill=rgba)
    return image


def _full_layers(size: int = 16) -> dict:
    """One sheet per part with every shared-grid cell non-empty."""
    w = 56 * size
    h = 20 * size * 2
    return {
        "body": [_filled_sheet(w, h, (200, 160, 120, 255))],
        "outfit": [_filled_sheet(w, h, (20, 80, 160, 255))],
        "eyes": [_filled_sheet(w, h, (10, 10, 10, 255))],
        "hair": [_filled_sheet(w, h, (90, 40, 20, 255))],
        "skin": [_filled_sheet(64, 64, (210, 170, 130, 255))],
    }


def test_animation_atlases_cover_every_clip_and_persona(tmp_path):
    out = tmp_path / "pack" / "demo"
    layers = _full_layers()
    manifest = B.build_theme(B.DEFAULT_SOURCE, out, size=16,
                             personas=["alpha", "bravo"], layers=layers)
    animations = manifest["characters"]["animations"]
    assert set(animations["clips"]) == set(B.ANIMATIONS)
    assert animations["cells"] == {"alpha": 0, "bravo": 1}
    assert animations["directions"] == ["left", "up", "right", "down"]
    for name, spec in B.ANIMATIONS.items():
        clip = animations["clips"][name]
        assert clip["frames"] == spec["frames"]
        assert clip["atlas"] in manifest["files"]
        atlas = out / clip["atlas"]
        with Image.open(atlas) as image:
            assert image.width == 4 * spec["frames"] * 16
            assert image.height == 2 * 32
    assert T.validate_theme(out) == []


def test_animations_can_be_disabled_for_a_static_pack(tmp_path):
    out = tmp_path / "pack" / "demo"
    manifest = B.build_theme(B.DEFAULT_SOURCE, out, size=16,
                             personas=["alpha"], layers=_full_layers(),
                             animations=False)
    assert "animations" not in manifest["characters"]
    assert not any(name.startswith("characters/") for name in manifest["files"])


def test_prepare_outside_and_fx_write_their_payloads():
    png = B._encode_png(Image.new("RGBA", (32, 32), (1, 2, 3, 255)))
    outside = {
        "grass": {"data": png, "sheet": "Green.png", "x": 128, "y": 0, "w": 16, "h": 16},
        "tree": {"data": png, "ax": 16, "ay": 32, "scale": 0.75,
                 "sheet": "Green.png", "x": 0, "y": 80, "w": 32, "h": 32},
        "tree_orange": {"data": png, "ax": 16, "ay": 32, "scale": 0.75,
                        "sheet": "Green.png", "x": 80, "y": 80, "w": 32, "h": 32},
    }
    payloads, manifest, recipe = B._prepare_outside(outside)
    assert "outside/grass.png" not in payloads
    assert set(payloads) == {"outside/tree.png", "outside/tree_orange.png"}
    assert manifest["props"]["tree"]["scale"] == 0.75
    assert manifest["props"]["pine"] == manifest["props"]["tree_orange"]
    assert recipe["tiles"]["tree"]["x"] == 0

    fx = {"complete": {"data": png, "frame": {"w": 40, "h": 40},
                       "frames": 60, "fps": 15, "sheet": "fx.png"}}
    payloads, manifest, recipe = B._prepare_fx(fx)
    assert payloads == {"fx/complete.png": png}
    assert manifest["clips"]["complete"]["frames"] == 60
    assert recipe["clips"]["complete"]["sheet"] == "fx.png"


def test_prepare_extras_writes_toolbar_icons():
    png = B._encode_png(Image.new("RGBA", (32, 32), (9, 9, 9, 255)))
    payloads, manifest, recipe = B._prepare_extras(
        {"ui_icons": {"settings": {"data": png, "sheet": "Icons/icon4.png"}}})
    assert payloads["ui/icons/settings.png"]
    assert manifest["ui"]["icons"]["settings"]["w"] == 32
    assert recipe["ui"]["icons"]["settings"]["sheet"] == "Icons/icon4.png"


def test_prepare_rooms_writes_tiles_window_and_recipe_coordinates():
    png = B._encode_png(Image.new("RGBA", (16, 32), (1, 2, 3, 255)))
    rooms = {
        "wall_face": {"data": png, "sheet": "Walls.png", "x": 0, "y": 0, "w": 16, "h": 32},
        "door_face": {"data": png, "sheet": "Door.png", "x": 0, "y": 16, "w": 32, "h": 32},
        "window": {"data": png, "sheet": "Walls3d.png", "x": 304, "y": 112, "w": 32, "h": 48},
    }
    payloads, manifest, recipe = B._prepare_rooms(rooms)
    assert set(payloads) == {"rooms/wall_face.png", "rooms/door_face.png", "rooms/window.png"}
    assert manifest["wall"]["face"] == "rooms/wall_face.png"
    assert manifest["door"]["face"] == "rooms/door_face.png"
    assert manifest["window"]["src"] == "rooms/window.png"
    assert manifest["window"]["rects"]["glass"]["w"] == 16
    assert recipe["tiles"]["wall_face"] == {
        "sheet": "Walls.png", "x": 0, "y": 0, "w": 16, "h": 32,
    }

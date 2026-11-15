"""Augment a local office theme pack with the owner-supplied UI packs.

WHY THIS IS A SEPARATE, LOCAL BUILD STEP
----------------------------------------
The UI packs (Dark Ages UI, Dragon HUDs, Free Medieval Fantasy UI, Raven
Fantasy Icons, uı-fonts, Super Pixel Effects) are paid/free art that must
never be committed. This module reads them from the owner's purchase folder,
extracts the exact regions/roles the Qubit Office shell consumes, and merges
them into a LOCAL theme pack under ``$QUBIT_HOME/office/themes/<id>`` (or a
caller-provided path outside the checkout). The repo ships only this code and
the metadata (roles, crop coordinates, licence/credit text); every image is
written to the local pack at build time.

The payload contract is additive: the existing ``ui.slices`` block is
untouched and the new ``ui.icons`` / ``ui.frames`` / ``ui.font`` /
``ui.effects`` / ``ui.ornament`` / ``ui.packs`` blocks are optional. The
server validates every referenced file and the shell treats a missing or
licence-blocked asset as absent (fallback), never as a broken request.

FAIL CLOSED
-----------
* A pack that is absent is skipped (the shell keeps its built-in fallback).
* A pack that is present but missing a declared file raises
  :class:`UIPackError`; the manifest is never written with a dangling src.
* A pack whose licence says ``allowed: False`` is skipped.
* Entries carry ``credit``/``license``/``license_ok`` so the shell can gate
  loading a second time.

Usage::

    python scripts/office_theme_ui_packs.py \
      --source "/home/trick/Desktop/UI Office" \
      --theme "$QUBIT_HOME/office/themes/limezu" \
      --out /tmp/limezu-with-ui-packs

Run with ``--check`` to validate the source and print the selected roles
without writing anything.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

try:  # Pillow is a build-time dependency only, as in the theme builder.
    from PIL import Image
except Exception as exc:  # pragma: no cover - environment guard
    raise SystemExit(f"Pillow is required to build UI packs: {exc}") from exc

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Where each pack lives inside the owner's purchase folder. A path may be a
#: file or a directory; a missing pack is optional (skipped).
PACK_DIRS: Dict[str, str] = {
    "darkages": "DarkAgesUi_v1.0",
    "dragon": "dragon-huds/dragon-huds",
    "medieval": "Free_Medieval_Fantasy_UI_Pack",
    "raven": "Free - Raven Fantasy Icons/Free - Raven Fantasy Icons/"
             "Separated Files/64x64",
    "fonts": "uı-fonts/uı-fonts",
    "spx": "Super Pixel Effects Gigapack (Free Version) v3.2.0/"
           "Super Pixel Effects Gigapack (Free Version)",
}

#: Licence + attribution for every pack this module knows how to read. These
#: strings are recorded in the payload and shown in the local theme's credits.
#: ``allowed`` is the hard gate: a pack set to False is never extracted.
LICENSES: Dict[str, Dict[str, Any]] = {
    "darkages": {
        "label": "Dark Ages UI v1.0",
        "author": "Hypnobius",
        "credit": "Dark Ages UI — Hypnobius (https://hypnobius.itch.io/)",
        "license": (
            "Dark Ages UI Pixel Art Asset Pack v1.0 (Hypnobius): commercial "
            "and personal use allowed; modification allowed; resale, "
            "redistribution as an asset pack, NFTs/blockchain and AI training "
            "prohibited."
        ),
        "url": "https://hypnobius.itch.io/",
        "allowed": True,
    },
    "dragon": {
        "label": "Dragon HUDs",
        "author": "Kibyra",
        "credit": "Dragon HUDs — Kibyra",
        "license": (
            "Dragon HUDs (Kibyra): personal and commercial use allowed; "
            "redistribution/resale of the original files prohibited."
        ),
        "url": "",
        "allowed": True,
    },
    "medieval": {
        "label": "Free Medieval Fantasy UI Pack",
        "author": "Kibyra",
        "credit": "Free Medieval Fantasy UI Pack — Kibyra",
        "license": (
            "Free Medieval Fantasy UI Pack (Kibyra): free for personal and "
            "commercial use; credit appreciated; redistribution/resale of the "
            "files as-is prohibited."
        ),
        "url": "",
        "allowed": True,
    },
    "raven": {
        "label": "Free - Raven Fantasy Icons",
        "author": "Clockwork Raven Studios (Caio)",
        "credit": "Raven Fantasy Icons — Clockwork Raven Studios (Caio)",
        "license": (
            "Raven Fantasy Icons (Clockwork Raven Studios / Caio): owner-"
            "supplied free pack. The archive ships no separate licence text; "
            "use is limited to this project and the art must not be "
            "redistributed. Recorded by owner direction."
        ),
        "url": "https://www.patreon.com/clockworkravenstudios",
        "allowed": True,
    },
    "fonts": {
        "label": "UI Fonts",
        "author": "Kibyra",
        "credit": "UI Fonts — Kibyra",
        "license": (
            "UI Fonts (Kibyra): personal and commercial use allowed; "
            "redistribution/resale of the original files prohibited."
        ),
        "url": "",
        "allowed": True,
    },
    "spx": {
        "label": "Super Pixel Effects Gigapack (Free Version)",
        "author": "Will Tice / unTied Games",
        "credit": "Super Pixel Effects Gigapack — Will Tice / unTied Games",
        "license": (
            "Super Pixel Effects Gigapack (Free Version) — Will Tice / "
            "unTied Games: commercial/non-commercial use allowed with "
            "attribution; redistribution of the asset pack prohibited; AI "
            "training prohibited. Full terms: "
            "http://untiedgames.com/files/license.txt"
        ),
        "url": "https://untiedgames.itch.io/",
        "allowed": True,
    },
}

#: 9-slice roles. ``crop`` is ``(left, top, right, bottom)`` in the source
#: image; ``slice`` is the CSS border-image-slice order (top,right,bottom,left).
SLICE_SPECS: Dict[str, Dict[str, Any]] = {
    "panel": {
        "pack": "darkages", "path": "32x32-Tilesheet.png",
        "crop": (0, 0, 96, 96), "slice": [24, 24, 24, 24],
        "width": "12px", "repeat": "stretch",
        "ink": "light",
    },
    "dialog": {
        # The dragon dialogue-box has its dragon on the left and transparent
        # margins; a full-height 9-slice stretches that ornament into the
        # header. The Dark Ages ornate frame is a clean, symmetric dialog
        # frame, so the Talk panel uses it; the dragon pack still drives the
        # HUD avatar frame and the status-bar slice below.
        "pack": "darkages", "path": "32x32-Tilesheet.png",
        "crop": (0, 0, 96, 96), "slice": [24, 24, 24, 24],
        "width": "12px", "repeat": "stretch", "ink": "light",
    },
    "button": {
        "pack": "darkages", "path": "32x32-Tilesheet.png",
        "crop": (128, 96, 192, 128), "slice": [12, 12, 12, 12],
        "width": "8px", "repeat": "stretch",
        "ink": "light",
    },
    "button-secondary": {
        "pack": "darkages", "path": "32x32-Tilesheet.png",
        "crop": (0, 96, 64, 128), "slice": [12, 12, 12, 12],
        "width": "8px", "repeat": "stretch",
        "ink": "light",
    },
    "icon-button": {
        "pack": "darkages", "path": "32x32-Tilesheet.png",
        "crop": (256, 96, 288, 128), "slice": [8, 8, 8, 8],
        "width": "6px", "repeat": "stretch",
        "ink": "light",
    },
    "input": {
        "pack": "darkages", "path": "32x32-Tilesheet.png",
        "crop": (96, 0, 192, 96), "slice": [16, 16, 16, 16],
        "width": "10px", "repeat": "stretch",
        "ink": "dark",
    },
    "tab": {
        "pack": "darkages", "path": "32x32-Tilesheet.png",
        "crop": (128, 96, 192, 128), "slice": [12, 12, 12, 12],
        "width": "6px", "repeat": "stretch",
        "ink": "light",
    },
    "hud": {
        "pack": "dragon", "path": "line-bars/line-bar-health.png",
        "slice": [16, 16, 16, 16], "width": "8px", "repeat": "stretch",
        "ink": "light",
    },
}

#: Non-slice HUD frame / ornament images (background images, not border-image).
FRAME_SPECS: Dict[str, Dict[str, Any]] = {
    "avatar": {
        "pack": "dragon", "path": "rounded-bars/avatar-frame.png",
        "width": 80, "height": 80,
    },
    "ornament": {
        "pack": "darkages", "path": "32x32-Tilesheet.png",
        "crop": (192, 192, 384, 256), "width": 192, "height": 64,
    },
}

#: Toolbar/panel icons. Names are the shell vocabulary, not source filenames.
ICON_SPECS: Dict[str, Dict[str, Any]] = {
    "talk": {"pack": "medieval", "path": "UI Icons 2/icon20.png"},
    "agents": {"pack": "dragon", "path": "icons/quest-icon.png"},
    "warroom": {"pack": "raven", "path": "fc100.png"},
    "timeline": {"pack": "raven", "path": "fc10.png"},
    "studio": {"pack": "dragon", "path": "icons/options-icon.png"},
    "campus": {"pack": "dragon", "path": "icons/home-icon.png"},
    "command-center": {"pack": "dragon", "path": "icons/map-icon.png"},
    "approvals": {"pack": "dragon", "path": "icons/confirm-icon.png"},
    "close": {"pack": "dragon", "path": "icons/close-icon.png"},
    "send": {"pack": "dragon", "path": "icons/confirm-icon.png"},
}

#: The bitmap font sheet is 8x8 cells of 32x32. The glyph order is the pack's
#: own ordering (verified from u0..u63 separations: digits, A-Z, a-z, ñ, Ç).
FONT_SPEC: Dict[str, Any] = {
    "pack": "fonts", "path": "uı-1/uı-1-yellow/uı-1.png",
    "cell": 32, "cols": 8, "rows": 8,
    "glyphs": (
        "0123456789"
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        "abcdefghijklmnopqrstuvwxyz"
        "ñÇ"
    ),
}

#: A real success sparkle, shown only after a successful chat send.
EFFECT_SPECS: Dict[str, Dict[str, Any]] = {
    "success": {
        "pack": "spx",
        "path": "spritesheet/Symbols/symbol_success_001/"
                "symbol_success_001_small_green/spritesheet.png",
        "frames": 60, "fps": 15, "width": 40, "height": 40,
        "frames_per_row": 60,
    },
}

NEW_FILE_PREFIX = "ui/packs"


class UIPackError(Exception):
    """A declared asset is missing or unusable. The build must not continue."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _pack_root(source: Path, key: str) -> Optional[Path]:
    relative = PACK_DIRS.get(key)
    if not relative:
        return None
    path = source / relative
    return path if path.exists() else None


def _license_entry(key: str,
                   licenses: Mapping[str, Mapping[str, Any]]
                   ) -> Optional[Dict[str, Any]]:
    spec = licenses.get(key)
    if not isinstance(spec, Mapping):
        return None
    if not spec.get("allowed"):
        return None
    license_text = str(spec.get("license") or "").strip()
    credit = str(spec.get("credit") or "").strip()
    if not license_text or not credit:
        # A pack without recorded terms is not loadable.
        return None
    return {
        "id": key,
        "label": str(spec.get("label") or key),
        "author": str(spec.get("author") or ""),
        "credit": credit,
        "license": license_text,
        "url": str(spec.get("url") or ""),
        "license_ok": True,
    }


def _read_image(path: Path) -> Image.Image:
    if not path.is_file():
        raise UIPackError(f"asset missing: {path}")
    try:
        image = Image.open(path)
        image.load()
        return image.convert("RGBA")
    except Exception as exc:  # noqa: BLE001 - a bad asset is a build failure
        raise UIPackError(f"asset unreadable: {path}: {exc}") from exc


def _png_bytes(image: Image.Image) -> bytes:
    import io

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _crop(image: Image.Image, crop: Any) -> Image.Image:
    if not crop:
        return image
    left, top, right, bottom = (int(value) for value in crop)
    if left < 0 or top < 0 or right <= left or bottom <= top:
        raise UIPackError(f"invalid crop {crop!r}")
    if right > image.width or bottom > image.height:
        raise UIPackError(
            f"crop {crop!r} exceeds {image.width}x{image.height} source")
    return image.crop((left, top, right, bottom))


def _asset(
    source: Path,
    spec: Mapping[str, Any],
    licenses: Mapping[str, Mapping[str, Any]],
) -> Optional[Tuple[Path, Image.Image, Dict[str, Any]]]:
    key = str(spec.get("pack") or "")
    license_entry = _license_entry(key, licenses)
    if license_entry is None:
        return None
    root = _pack_root(source, key)
    if root is None:
        return None
    image = _read_image(root / str(spec["path"]))
    image = _crop(image, spec.get("crop"))
    return root, image, license_entry


def plan_ui_assets(
    source: str | Path,
    *,
    licenses: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    """Return the roles/files this source would produce, without writing.

    This is the selection seam tests pin: which pack drives which role, and
    which entries are omitted by the licence gate or a missing pack.
    """
    source_path = Path(source).expanduser()
    if not source_path.is_dir():
        raise UIPackError(f"source is not a directory: {source_path}")
    terms = licenses if licenses is not None else LICENSES
    selected: Dict[str, Any] = {
        "slices": {}, "frames": {}, "icons": {}, "effects": {},
        "font": None, "ornament": None, "packs": [], "skipped": [],
    }
    used_packs: Dict[str, Dict[str, Any]] = {}
    for role, spec in SLICE_SPECS.items():
        asset = _asset(source_path, spec, terms)
        if asset is None:
            selected["skipped"].append(f"slice:{role}")
            continue
        _root, image, license_entry = asset
        selected["slices"][role] = {
            "pack": spec["pack"], "image": image,
            "slice": list(spec["slice"]), "width": spec["width"],
            "repeat": spec["repeat"], "ink": spec.get("ink", "dark"),
            "license": license_entry,
        }
        used_packs[spec["pack"]] = license_entry
    for name, spec in FRAME_SPECS.items():
        asset = _asset(source_path, spec, terms)
        if asset is None:
            selected["skipped"].append(f"frame:{name}")
            continue
        _root, image, license_entry = asset
        entry = {
            "pack": spec["pack"], "image": image,
            "width": int(spec.get("width") or image.width),
            "height": int(spec.get("height") or image.height),
            "license": license_entry,
        }
        if name == "ornament":
            selected["ornament"] = entry
        else:
            selected["frames"][name] = entry
        used_packs[spec["pack"]] = license_entry
    for name, spec in ICON_SPECS.items():
        asset = _asset(source_path, spec, terms)
        if asset is None:
            selected["skipped"].append(f"icon:{name}")
            continue
        _root, image, license_entry = asset
        selected["icons"][name] = {
            "pack": spec["pack"], "image": image,
            "width": image.width, "height": image.height,
            "license": license_entry,
        }
        used_packs[spec["pack"]] = license_entry
    font_asset = _asset(source_path, FONT_SPEC, terms)
    if font_asset is not None:
        _root, image, license_entry = font_asset
        selected["font"] = {
            "pack": FONT_SPEC["pack"], "image": image,
            "cell": FONT_SPEC["cell"], "cols": FONT_SPEC["cols"],
            "rows": FONT_SPEC["rows"], "glyphs": FONT_SPEC["glyphs"],
            "license": license_entry,
        }
        used_packs[FONT_SPEC["pack"]] = license_entry
    else:
        selected["skipped"].append("font")
    for name, spec in EFFECT_SPECS.items():
        asset = _asset(source_path, spec, terms)
        if asset is None:
            selected["skipped"].append(f"effect:{name}")
            continue
        _root, image, license_entry = asset
        expected = int(spec["frames"]) * int(spec["width"])
        if image.width < expected:
            raise UIPackError(
                f"effect {name}: sheet is {image.width}px wide but "
                f"{spec['frames']} frames of {spec['width']}px need {expected}px")
        selected["effects"][name] = {
            "pack": spec["pack"], "image": image,
            "frames": int(spec["frames"]), "fps": int(spec["fps"]),
            "width": int(spec["width"]), "height": int(spec["height"]),
            "frames_per_row": int(spec.get("frames_per_row") or spec["frames"]),
            "license": license_entry,
        }
        used_packs[spec["pack"]] = license_entry
    selected["packs"] = [
        used_packs[key] for key in sorted(used_packs)
    ]
    return selected


def _describe(selected: Mapping[str, Any]) -> Dict[str, str]:
    """A human-readable pack -> role map (for --check and docs)."""
    out: Dict[str, str] = {}
    for role, entry in selected.get("slices", {}).items():
        out[f"slice:{role}"] = entry["pack"]
    for name, entry in selected.get("frames", {}).items():
        out[f"frame:{name}"] = entry["pack"]
    if selected.get("ornament"):
        out["ornament"] = selected["ornament"]["pack"]
    for name, entry in selected.get("icons", {}).items():
        out[f"icon:{name}"] = entry["pack"]
    if selected.get("font"):
        out["font"] = selected["font"]["pack"]
    for name, entry in selected.get("effects", {}).items():
        out[f"effect:{name}"] = entry["pack"]
    return out


def build_ui_assets(
    source: str | Path,
    *,
    licenses: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> Tuple[Dict[str, bytes], Dict[str, Any], Dict[str, str]]:
    """Extract the assets into ``{relative_path: bytes}`` + the ui block."""
    selected = plan_ui_assets(source, licenses=licenses)
    payloads: Dict[str, bytes] = {}
    ui: Dict[str, Any] = {
        "slices": {}, "frames": {}, "icons": {}, "effects": {},
        "packs": list(selected["packs"]), "skipped": list(selected["skipped"]),
    }
    for role, entry in selected["slices"].items():
        relative = f"{NEW_FILE_PREFIX}/slices/{role}.png"
        payloads[relative] = _png_bytes(entry["image"])
        ui["slices"][role] = {
            "src": relative,
            "slice": entry["slice"],
            "width": entry["width"],
            "repeat": entry["repeat"],
            "ink": entry.get("ink", "dark"),
            "pack": entry["pack"],
        }
    for name, entry in selected["frames"].items():
        relative = f"{NEW_FILE_PREFIX}/frames/{name}.png"
        payloads[relative] = _png_bytes(entry["image"])
        ui["frames"][name] = {
            "src": relative, "width": entry["width"], "height": entry["height"],
            "pack": entry["pack"],
            **{key: entry["license"][key]
               for key in ("credit", "license", "license_ok")},
        }
    if selected.get("ornament"):
        entry = selected["ornament"]
        relative = f"{NEW_FILE_PREFIX}/ornament.png"
        payloads[relative] = _png_bytes(entry["image"])
        ui["ornament"] = {
            "src": relative, "width": entry["width"], "height": entry["height"],
            "pack": entry["pack"],
            **{key: entry["license"][key]
               for key in ("credit", "license", "license_ok")},
        }
    for name, entry in selected["icons"].items():
        relative = f"{NEW_FILE_PREFIX}/icons/{name}.png"
        payloads[relative] = _png_bytes(entry["image"])
        ui["icons"][name] = {
            "src": relative, "width": entry["width"], "height": entry["height"],
            "pack": entry["pack"],
            **{key: entry["license"][key]
               for key in ("credit", "license", "license_ok")},
        }
    if selected.get("font"):
        entry = selected["font"]
        relative = f"{NEW_FILE_PREFIX}/fonts/ui-font.png"
        payloads[relative] = _png_bytes(entry["image"])
        ui["font"] = {
            "src": relative, "cell": entry["cell"], "cols": entry["cols"],
            "rows": entry["rows"], "glyphs": entry["glyphs"],
            "pack": entry["pack"],
            **{key: entry["license"][key]
               for key in ("credit", "license", "license_ok")},
        }
    for name, entry in selected["effects"].items():
        relative = f"{NEW_FILE_PREFIX}/effects/{name}.png"
        payloads[relative] = _png_bytes(entry["image"])
        ui["effects"][name] = {
            "src": relative, "frames": entry["frames"], "fps": entry["fps"],
            "width": entry["width"], "height": entry["height"],
            "frames_per_row": entry["frames_per_row"],
            "pack": entry["pack"],
            **{key: entry["license"][key]
               for key in ("credit", "license", "license_ok")},
        }
    return payloads, ui, _describe(selected)


def merge_manifest(
    manifest: Mapping[str, Any],
    payloads: Mapping[str, bytes],
    ui: Mapping[str, Any],
) -> Dict[str, Any]:
    """Merge the new files/ui block into an existing theme manifest."""
    merged = json.loads(json.dumps(manifest))
    files = merged.get("files")
    if not isinstance(files, dict):
        files = {}
    for relative, data in payloads.items():
        files[relative] = {"sha256": _sha256(data), "bytes": len(data)}
    merged["files"] = files
    current = merged.get("ui")
    if not isinstance(current, dict):
        current = {}
    for key in ("slices", "frames", "icons", "effects"):
        bucket = current.get(key)
        if not isinstance(bucket, dict):
            bucket = {}
        bucket.update(ui.get(key) or {})
        current[key] = bucket
    for key in ("font", "ornament"):
        if ui.get(key):
            current[key] = ui[key]
    if ui.get("packs"):
        current["packs"] = list(ui["packs"])
    if ui.get("skipped"):
        current["skipped"] = list(ui["skipped"])
    merged["ui"] = current
    total = sum(int(entry.get("bytes", 0)) for entry in files.values())
    limits = merged.get("limits")
    if not isinstance(limits, dict):
        limits = {}
    limits["max_total_bytes"] = max(
        int(limits.get("max_total_bytes") or 0),
        total + max(1024 * 1024, total // 10),
    )
    limits["max_files"] = max(
        int(limits.get("max_files") or 0), len(files) + 8)
    merged["limits"] = limits
    return merged


def _inside_repository(path: Path) -> bool:
    try:
        path.resolve().relative_to(REPO_ROOT)
        return True
    except ValueError:
        return False


def augment_theme(
    theme_dir: str | Path,
    source: str | Path,
    out: Optional[str | Path] = None,
    *,
    licenses: Optional[Mapping[str, Mapping[str, Any]]] = None,
    validate: bool = True,
) -> Dict[str, Any]:
    """Copy ``theme_dir``, add the UI pack assets, and validate the result."""
    theme_path = Path(theme_dir).expanduser()
    if not (theme_path / "theme.json").is_file():
        raise UIPackError(f"theme.json missing in {theme_path}")
    out_path = Path(out).expanduser() if out else theme_path
    if _inside_repository(out_path):
        raise UIPackError(
            f"refusing to write a theme pack inside the repository ({out_path})")
    payloads, ui, roles = build_ui_assets(source, licenses=licenses)
    manifest = json.loads((theme_path / "theme.json").read_text(encoding="utf-8"))
    merged = merge_manifest(manifest, payloads, ui)

    if out_path != theme_path:
        if out_path.exists():
            shutil.rmtree(out_path)
        shutil.copytree(theme_path, out_path)
    for relative, data in payloads.items():
        target = out_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    (out_path / "theme.json").write_text(
        json.dumps(merged, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if validate:
        # Import the real contract, not a copy: the same validator the server
        # uses must accept the augmented manifest and every referenced file.
        sys.path.insert(0, str(REPO_ROOT))
        try:
            from voicebridge.office import theme as theme_contract
        finally:
            try:
                sys.path.remove(str(REPO_ROOT))
            except ValueError:
                pass
        problems = theme_contract.validate_theme(out_path)
        if problems:
            raise UIPackError(
                "augmented theme failed validation: " + "; ".join(problems))
    return {
        "ok": True,
        "theme": str(out_path),
        "files": sorted(payloads),
        "roles": roles,
        "skipped": list(ui.get("skipped") or []),
        "packs": [pack["id"] for pack in ui.get("packs") or []],
    }


def _main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True,
                        help="owner purchase folder (e.g. 'UI Office')")
    parser.add_argument("--theme", required=True,
                        help="existing local theme pack to augment")
    parser.add_argument("--out", default="",
                        help="output pack directory (default: in place)")
    parser.add_argument("--check", action="store_true",
                        help="report the selected roles and write nothing")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        if args.check:
            selected = plan_ui_assets(args.source)
            report = {
                "ok": True,
                "roles": _describe(selected),
                "packs": [pack["id"] for pack in selected.get("packs") or []],
                "skipped": selected.get("skipped") or [],
            }
        else:
            report = augment_theme(
                args.theme, args.source, args.out or None)
    except UIPackError as exc:
        report = {"ok": False, "error": str(exc)}
        print(json.dumps(report) if args.json else f"FAILED: {exc}")
        return 2
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"ok: {report.get('theme', '(check only)')}")
        for role, pack in sorted((report.get("roles") or {}).items()):
            print(f"  {role:24s} <- {pack}")
        if report.get("skipped"):
            print("  skipped: " + ", ".join(report["skipped"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())

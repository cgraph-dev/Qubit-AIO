"""``qubit office theme`` installs, validates, lists, and exports recipes.

WHAT THIS PINS
--------------
Task 8 of the office UI theme upgrade. The CLI is the owner-facing seam onto
the theme contract, and it must never turn a theme into a code-execution or
archive-bomb path. The rules below are the ones that make it safe:

  * ``validate`` reports every contract problem on stdout and exits 2;
  * ``install`` copies a recipe or a locally built pack atomically into
    ``$QUBIT_HOME/office/themes/<id>`` and refuses to overwrite without
    ``--force``;
  * a recipe install prints the exact builder next step (art stays local);
  * archived input is a *plain 32-bit zip*: traversal, symlinks, duplicate /
    NFC-colliding names, encryption, ZIP64, unsupported compression,
    oversized and ratio-bomb entries, and executable content are refused with
    typed errors before anything is written;
  * ``export`` emits the shareable recipe only -- never atlases or art -- and
    the result re-installs and re-validates;
  * ``list`` reports installed/bundled themes, their kind, and the active id.

Hermetic: throwaway ``QUBIT_HOME`` roots only, no network, no service.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import warnings
import zipfile
from pathlib import Path

import pytest

from voicebridge.office import theme as T
from voicebridge.office import theme_cli
from voicebridge.cli import _cmd_office, build_parser

pytestmark = pytest.mark.unit


# ----------------------------------------------------------------- helpers
class _Cfg:
    """A config object with the same ``.get(*path, default=)`` shape."""

    def __init__(self, data=None):
        self.data = data or {}

    def get(self, *path, default=None):
        node = self.data
        for key in path:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node


def _tokens():
    return {
        "color-surface": "#0d0d1a",
        "color-surface-raised": "#161a23",
        "color-surface-hover": "#1d2230",
        "color-text-primary": "#e9edf7",
        "color-text-muted": "#8a93a8",
        "color-border": "#334155",
        "color-accent": "#7cc8ff",
        "radius-control": "4px",
    }


def _recipe(theme_id="demo", **overrides):
    recipe = {
        "format": T.FORMAT,
        "schema": T.SCHEMA,
        "id": theme_id,
        "label": "Demo",
        "version": "1.0.0",
        "credit": "LimeZu — https://limezu.itch.io",
        "license": "LimeZu licence; redistribution prohibited",
        "source": "owner purchase, 2026-11-09",
        "template_version": 1,
        "tokens": _tokens(),
        "slices": {},
        "parts": {"skin": 9, "eyes": 7, "hair": 200, "outfit": 42, "accessory": 85},
        "agents": {"ceo": {"skin": 2, "eyes": 1, "hair": 1,
                           "outfit": 1, "accessory": 1}},
    }
    recipe.update(overrides)
    return recipe


def _write_json(path: Path, document) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _recipe_dir(root: Path, theme_id="demo") -> Path:
    folder = root / f"recipe-{theme_id}"
    return _write_json(folder / "theme.recipe.json", _recipe(theme_id)).parent


def _write_file(root: Path, relative: str, data: bytes):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def _pack_dir(root: Path, theme_id="limezu") -> Path:
    """Write a minimal complete local pack (fake atlas bytes)."""
    pack = root / f"pack-{theme_id}"
    pack.mkdir(parents=True, exist_ok=True)
    files = {}
    files["portraits/atlas32.png"] = _write_file(pack, "portraits/atlas32.png", b"PNG-P")
    files["bodies/atlas32.png"] = _write_file(pack, "bodies/atlas32.png", b"PNG-B")
    portrait_index = {"schema": 1, "size": 32, "cols": 20, "rows": 15,
                      "cells": {"ceo": [0, 0]}}
    body_index = {"schema": 1, "size": 32, "frame": {"w": 32, "h": 64},
                  "cols": 20, "rows": 15, "cells": {"ceo": [0, 0]}}
    files["portraits/index.json"] = _write_file(
        pack, "portraits/index.json", json.dumps(portrait_index).encode())
    files["bodies/index.json"] = _write_file(
        pack, "bodies/index.json", json.dumps(body_index).encode())
    agents = {"schema": 1, "templateVersion": 1,
              "recipes": {"ceo": {"skin": 2, "eyes": 1, "hair": 1,
                                   "outfit": 1, "accessory": 1}}}
    files["agents.json"] = _write_file(pack, "agents.json",
                                       json.dumps(agents).encode())
    manifest = {
        "format": T.FORMAT,
        "schema": T.SCHEMA,
        "id": theme_id,
        "label": "LimeZu",
        "version": "1.0.0",
        "credit": "LimeZu — https://limezu.itch.io",
        "license": "LimeZu licence; redistribution prohibited",
        "source": "owner purchase, 2026-11-09",
        "template_version": 1,
        "tokens": _tokens(),
        "slices": {},
        "files": files,
        "limits": {"max_total_bytes": 64 * 1024 * 1024, "max_files": 32},
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
    (pack / "theme.json").write_text(json.dumps(manifest), encoding="utf-8")
    return pack


def _run(cfg, argv, capsys):
    parser = build_parser()
    args = parser.parse_args(["office", "theme", *argv])
    rc = _cmd_office(cfg, args)
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


def _zip(path: Path, entries) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries:
            archive.writestr(name, data)
    return path


def _recipe_zip(path: Path, theme_id="demo") -> Path:
    return _zip(path, [("theme.recipe.json",
                        json.dumps(_recipe(theme_id)))])


# --------------------------------------------------------------- validate
def test_validate_clean_recipe_exits_0(tmp_path, capsys):
    folder = _recipe_dir(tmp_path)
    rc, out, _ = _run(_Cfg(), ["validate", str(folder)], capsys)
    assert rc == 0
    assert "demo" in out


def test_validate_missing_credit_exits_2_and_prints_the_problem(tmp_path,
                                                               capsys):
    folder = tmp_path / "broken"
    folder.mkdir()
    doc = _recipe()
    doc.pop("credit")
    _write_json(folder / "theme.recipe.json", doc)
    rc, out, _ = _run(_Cfg(), ["validate", str(folder)], capsys)
    assert rc == 2
    assert "credit" in out


def test_validate_missing_path_exits_2(tmp_path, capsys):
    rc, out, _ = _run(_Cfg(), ["validate", str(tmp_path / "nope")], capsys)
    assert rc == 2
    assert "THEME_SOURCE_MISSING" in out


def test_validate_full_pack(tmp_path, capsys):
    pack = _pack_dir(tmp_path)
    rc, out, _ = _run(_Cfg(), ["validate", str(pack)], capsys)
    assert rc == 0
    assert "limezu" in out


# ---------------------------------------------------------------- install
def test_install_recipe_into_throwaway_home(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    monkeypatch.setenv("QUBIT_HOME", str(home))
    folder = _recipe_dir(tmp_path)
    rc, out, _ = _run(_Cfg(), ["install", str(folder)], capsys)
    assert rc == 0
    target = home / "office" / "themes" / "demo"
    assert (target / "theme.recipe.json").is_file()
    assert "scripts/office_theme_build.py" in out
    assert "--source" in out and "--out" in out


def test_install_refuses_overwrite_without_force(tmp_path, monkeypatch,
                                                 capsys):
    home = tmp_path / "home"
    monkeypatch.setenv("QUBIT_HOME", str(home))
    folder = _recipe_dir(tmp_path)
    assert _run(_Cfg(), ["install", str(folder)], capsys)[0] == 0
    rc, out, _ = _run(_Cfg(), ["install", str(folder)], capsys)
    assert rc == 2
    assert "THEME_EXISTS" in out


def test_install_force_replaces_an_existing_theme(tmp_path, monkeypatch,
                                                  capsys):
    home = tmp_path / "home"
    monkeypatch.setenv("QUBIT_HOME", str(home))
    folder = _recipe_dir(tmp_path)
    assert _run(_Cfg(), ["install", str(folder)], capsys)[0] == 0
    changed = _write_json(folder / "theme.recipe.json",
                          _recipe(label="Demo v2"))
    rc, out, _ = _run(_Cfg(), ["install", str(changed), "--force"], capsys)
    assert rc == 0
    installed = json.loads(
        (home / "office/themes/demo/theme.recipe.json").read_text())
    assert installed["label"] == "Demo v2"


def test_install_full_pack_loads_via_contract(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    monkeypatch.setenv("QUBIT_HOME", str(home))
    pack = _pack_dir(tmp_path)
    rc, out, _ = _run(_Cfg(), ["install", str(pack)], capsys)
    assert rc == 0, out
    installed = home / "office" / "themes" / "limezu"
    assert T.validate_theme(installed) == []
    loaded = T.load_theme(installed)
    assert loaded.id == "limezu"
    assert loaded.portraits["atlas"] == "portraits/atlas32.png"


# ----------------------------------------------------------------- export
def test_export_recipe_contains_no_art_and_reinstalls(tmp_path, monkeypatch,
                                                      capsys):
    home = tmp_path / "home"
    monkeypatch.setenv("QUBIT_HOME", str(home))
    folder = _recipe_dir(tmp_path)
    assert _run(_Cfg(), ["install", str(folder)], capsys)[0] == 0

    out_zip = tmp_path / "export" / "demo.zip"
    rc, out, _ = _run(_Cfg(), ["export", "demo", str(out_zip)], capsys)
    assert rc == 0, out
    with zipfile.ZipFile(out_zip) as archive:
        names = archive.namelist()
    assert names == ["theme.recipe.json"]
    assert not any(name.lower().endswith((".png", ".webp", ".jpg", ".jpeg"))
                   for name in names)

    rc, out, _ = _run(_Cfg(), ["validate", str(out_zip)], capsys)
    assert rc == 0, out

    second_home = tmp_path / "home2"
    monkeypatch.setenv("QUBIT_HOME", str(second_home))
    rc, out, _ = _run(_Cfg(), ["install", str(out_zip)], capsys)
    assert rc == 0, out
    assert (second_home / "office/themes/demo/theme.recipe.json").is_file()


def test_export_from_a_full_pack_drops_the_atlases(tmp_path, monkeypatch,
                                                   capsys):
    home = tmp_path / "home"
    monkeypatch.setenv("QUBIT_HOME", str(home))
    pack = _pack_dir(tmp_path)
    assert _run(_Cfg(), ["install", str(pack)], capsys)[0] == 0

    out_zip = tmp_path / "limezu.zip"
    rc, out, _ = _run(_Cfg(), ["export", "limezu", str(out_zip)], capsys)
    assert rc == 0, out
    with zipfile.ZipFile(out_zip) as archive:
        assert archive.namelist() == ["theme.recipe.json"]
        recipe = json.loads(archive.read("theme.recipe.json"))
    assert recipe["id"] == "limezu"
    assert "files" not in recipe
    assert T.validate_recipe(recipe) == []


def test_export_unknown_theme_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "home"))
    rc, out, _ = _run(_Cfg(), ["export", "nope", str(tmp_path / "x.zip")],
                      capsys)
    assert rc == 2
    assert "THEME_NOT_FOUND" in out


# ------------------------------------------------------------------- list
def test_list_shows_active_and_installed_kinds(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    monkeypatch.setenv("QUBIT_HOME", str(home))
    assert _run(_Cfg(), ["install", str(_recipe_dir(tmp_path))], capsys)[0] == 0
    assert _run(_Cfg(), ["install", str(_pack_dir(tmp_path))], capsys)[0] == 0

    # A recipe-only entry cannot be active (there is no art to load); the
    # full pack is what the loader can activate.
    cfg = _Cfg({"office": {"theme": "limezu"}})
    rc, out, _ = _run(cfg, ["list"], capsys)
    assert rc == 0
    payload = json.loads(out)
    assert payload["active"] == "limezu"
    by_id = {entry["id"]: entry for entry in payload["themes"]}
    assert by_id["demo"]["kind"] == "recipe"
    assert by_id["demo"]["active"] is False
    assert by_id["limezu"]["kind"] == "pack"
    assert by_id["limezu"]["active"] is True
    assert by_id["demo"]["valid"] is True and by_id["limezu"]["valid"] is True


# ------------------------------------------------------------ hostile zips
def _hostile_install(tmp_path, monkeypatch, capsys, archive: Path):
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "home"))
    return _run(_Cfg(), ["install", str(archive)], capsys)


def test_zip_path_traversal_is_refused(tmp_path, monkeypatch, capsys):
    archive = _zip(tmp_path / "evil.zip", [
        ("theme.recipe.json", json.dumps(_recipe())),
        ("../escape.json", b"{}"),
    ])
    rc, out, _ = _hostile_install(tmp_path, monkeypatch, capsys, archive)
    assert rc == 2
    assert "THEME_ZIP_UNSAFE_PATH" in out
    assert not (tmp_path / "home" / "office" / "themes").exists()


def test_zip_symlink_entry_is_refused(tmp_path, monkeypatch, capsys):
    archive = tmp_path / "symlink.zip"
    info = zipfile.ZipInfo("theme.recipe.json")
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    info.compress_type = zipfile.ZIP_STORED
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(info, b"target")
    rc, out, _ = _hostile_install(tmp_path, monkeypatch, capsys, archive)
    assert rc == 2
    assert "THEME_ZIP_SYMLINK" in out


def test_zip_duplicate_entry_is_refused(tmp_path, monkeypatch, capsys):
    archive = tmp_path / "dup.zip"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("theme.recipe.json", json.dumps(_recipe()))
            zf.writestr("theme.recipe.json", json.dumps(_recipe(label="other")))
    rc, out, _ = _hostile_install(tmp_path, monkeypatch, capsys, archive)
    assert rc == 2
    assert "THEME_ZIP_DUPLICATE" in out


def test_zip_nfc_collision_is_refused(tmp_path, monkeypatch, capsys):
    archive = tmp_path / "nfc.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("theme.recipe.json", json.dumps(_recipe()))
        zf.writestr("Theme.Recipe.json", json.dumps(_recipe()))
    rc, out, _ = _hostile_install(tmp_path, monkeypatch, capsys, archive)
    assert rc == 2
    assert "THEME_ZIP_DUPLICATE" in out


def test_zip_oversize_is_refused(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(theme_cli, "MAX_TOTAL_BYTES", 64)
    archive = _recipe_zip(tmp_path / "big.zip")
    rc, out, _ = _hostile_install(tmp_path, monkeypatch, capsys, archive)
    assert rc == 2
    assert "THEME_ZIP_OVERSIZE" in out


def test_zip_ratio_bomb_is_refused(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(theme_cli, "MAX_FILE_BYTES", 4 * 1024 * 1024)
    monkeypatch.setattr(theme_cli, "MAX_TOTAL_BYTES", 8 * 1024 * 1024)
    monkeypatch.setattr(theme_cli, "MAX_COMPRESSION_RATIO", 10)
    monkeypatch.setattr(theme_cli, "MAX_RATIO_MIN_BYTES", 1024)
    archive = _zip(tmp_path / "bomb.zip", [
        ("theme.recipe.json", json.dumps(_recipe())),
        ("padding.txt", b"\0" * 200_000),
    ])
    rc, out, _ = _hostile_install(tmp_path, monkeypatch, capsys, archive)
    assert rc == 2
    assert "THEME_ZIP_RATIO" in out


def test_zip_executable_entry_is_refused(tmp_path, monkeypatch, capsys):
    archive = tmp_path / "exec.zip"
    info = zipfile.ZipInfo("theme.recipe.json")
    info.external_attr = (stat.S_IFREG | 0o755) << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(info, json.dumps(_recipe()))
    rc, out, _ = _hostile_install(tmp_path, monkeypatch, capsys, archive)
    assert rc == 2
    assert "THEME_ZIP_EXECUTABLE" in out


def test_zip_unsupported_suffix_is_refused(tmp_path, monkeypatch, capsys):
    archive = _zip(tmp_path / "suffix.zip", [
        ("theme.recipe.json", json.dumps(_recipe())),
        ("run.sh", b"#!/bin/sh\n"),
    ])
    rc, out, _ = _hostile_install(tmp_path, monkeypatch, capsys, archive)
    assert rc == 2
    assert "THEME_ZIP_SUFFIX" in out


def test_zip64_locator_is_detected():
    blob = b"PK\x05\x06" + b"\0" * 18 + b"PK\x06\x07" + b"\0" * 20
    assert theme_cli._has_zip64_locator(blob) is True
    assert theme_cli._has_zip64_locator(b"PK\x05\x06" + b"\0" * 40) is False


def test_encrypted_entry_flag_is_refused(tmp_path, monkeypatch, capsys):
    archive = _recipe_zip(tmp_path / "enc.zip")
    # Flip the "encrypted" flag in the local + central headers (a real
    # encrypted archive would be unreadable; the flag check is what runs).
    raw = bytearray(archive.read_bytes())
    offset = raw.find(b"PK\x03\x04")
    raw[offset + 6] |= 0x01
    central = raw.find(b"PK\x01\x02")
    raw[central + 8] |= 0x01
    archive.write_bytes(bytes(raw))
    rc, out, _ = _hostile_install(tmp_path, monkeypatch, capsys, archive)
    assert rc == 2
    assert "THEME_ZIP_ENCRYPTED" in out or "THEME_ZIP_CORRUPT" in out


# ------------------------------------------------------- safe zip controls
def test_a_clean_zip_recipe_installs(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "home"))
    archive = _recipe_zip(tmp_path / "clean.zip")
    rc, out, _ = _run(_Cfg(), ["install", str(archive)], capsys)
    assert rc == 0, out
    assert (tmp_path / "home/office/themes/demo/theme.recipe.json").is_file()


def test_a_wrapped_zip_pack_installs(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "home"))
    pack = _pack_dir(tmp_path)
    archive = tmp_path / "wrapped.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for path in sorted(pack.rglob("*")):
            if path.is_file():
                zf.write(path, f"limezu/{path.relative_to(pack)}")
    rc, out, _ = _run(_Cfg(), ["install", str(archive)], capsys)
    assert rc == 0, out
    assert T.validate_theme(tmp_path / "home/office/themes/limezu") == []


# --------------------------------------------------------- dry-run + source
def test_install_dry_run_writes_nothing(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    monkeypatch.setenv("QUBIT_HOME", str(home))
    folder = _recipe_dir(tmp_path)
    rc, out, _ = _run(_Cfg(), ["install", str(folder), "--dry-run"], capsys)
    assert rc == 0
    assert "preview" in out
    assert not (home / "office" / "themes").exists()


def test_export_dry_run_writes_nothing(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    monkeypatch.setenv("QUBIT_HOME", str(home))
    assert _run(_Cfg(), ["install", str(_recipe_dir(tmp_path))], capsys)[0] == 0
    out_zip = tmp_path / "dry" / "demo.zip"
    rc, out, _ = _run(_Cfg(), ["export", "demo", str(out_zip), "--dry-run"],
                      capsys)
    assert rc == 0
    assert "preview" in out
    assert not out_zip.exists()


def test_directory_source_with_a_symlink_is_refused(tmp_path, monkeypatch,
                                                    capsys):
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "home"))
    folder = _recipe_dir(tmp_path)
    os.symlink(folder / "theme.recipe.json", folder / "link.json")
    rc, out, _ = _run(_Cfg(), ["install", str(folder)], capsys)
    assert rc == 2
    assert "THEME_SOURCE_UNSAFE" in out


def test_zip_unsupported_compression_is_refused(tmp_path, monkeypatch, capsys):
    archive = tmp_path / "bzip.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_BZIP2) as zf:
        zf.writestr("theme.recipe.json", json.dumps(_recipe()))
    rc, out, _ = _hostile_install(tmp_path, monkeypatch, capsys, archive)
    assert rc == 2
    assert "THEME_ZIP_COMPRESSION" in out

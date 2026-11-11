"""The LimeZu catalog extractor reads names, never art.

Synthetic fixtures pin the measured pack layouts:

  * ``Theme_Sorter_MV/Office_01.png`` -> ``theme_sorter`` sheet, 16x16;
  * ``16x16/Animated/...`` -> ``animated`` decor sheet;
  * ``16x16/Walls/...`` -> wall surface;
  * ``32x32|48x48/Portrait_Generator_*/...`` -> portrait close-up;
  * a 32x32 non-portrait sheet falls back out of the 16x16 world catalog;
  * ``shadowless`` / ``shadow`` names set the shadow policy;
  * unsafe zip entries, hidden files, ``__MACOSX`` and non-images are skipped;
  * the source can be a directory or a zip, and extraction never writes art.

Hermetic: temp directories only, no network.
"""
from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from pathlib import Path

import jsonschema
import pytest

from voicebridge.office.world import catalog as C
from voicebridge.office.world import style as S

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA = json.loads(
    (Path(S.__file__).resolve().parent / "schema"
     / "asset_catalog.schema.json").read_text(encoding="utf-8"))

#: Deliberately invalid image bytes: the extractor must not open them.
_PACK = {
    "16x16/Theme_Sorter_MV/Office_01.png": b"not-an-image",
    "16x16/Theme_Sorter_MV/Office_02_shadowless.png": b"x",
    "16x16/Animated/PC_01.png": b"x",
    "16x16/Walls/Wall_01_shadow.png": b"x",
    "32x32/Portrait_Generator_32x32/Skins/Skin 1.png": b"x",
    "48x48/Portrait_Generator_48x48/Hairstyles/Hairstyle 01 1.png": b"x",
    "32x32/Animated_32x32/PC_02.png": b"x",
    "notes.txt": "not an image",
    ".DS_Store": b"x",
    "__MACOSX/16x16/Theme_Sorter_MV/Office_01.png": b"x",
}


def _make_pack(tmp_path: Path) -> Path:
    root = tmp_path / "pack"
    for name, data in _PACK.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data.encode() if isinstance(data, str) else data)
    return root


def _make_zip(tmp_path: Path) -> Path:
    archive = tmp_path / "pack.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for name, data in _PACK.items():
            zf.writestr(name, data)
        zf.writestr("../evil.png", b"x")
        zf.writestr("/absolute.png", b"x")
    return archive


def _by_category(entries, category):
    return [entry for entry in entries if entry.category == category]


# --- directory extraction ---------------------------------------------------

def test_directory_extraction_reads_names_and_categories(tmp_path):
    pack = _make_pack(tmp_path)
    before = sorted(p.name for p in pack.iterdir())
    entries = C.extract_catalog(pack)
    assert len(entries) == 6  # the 32x32 non-portrait sheet falls back out
    ids = [entry.id for entry in entries]
    assert len(set(ids)) == len(ids)
    for entry in entries:
        assert entry.theme == "limezu"
        assert entry.size in (16, 32, 48)
        jsonschema.validate([entry.to_dict()], SCHEMA)

    themes = _by_category(entries, "theme_sorter")
    assert {e.size for e in themes} == {16}
    assert {e.surface for e in themes} == {"furniture"}
    assert {e.shadow_variant for e in themes} == {"shadowless"}

    animated = _by_category(entries, "animated")
    assert len(animated) == 1
    assert animated[0].animation == "animated"
    assert animated[0].surface == "decor"

    wall = _by_category(entries, "wall")
    assert wall[0].shadow_variant == "black_shadow"
    assert wall[0].surface == "wall"

    portraits = _by_category(entries, "portrait")
    assert {e.size for e in portraits} == {32, 48}
    assert {e.surface for e in portraits} == {"portrait"}
    # Spaces in the real pack's names are legal and survive into ``file``.
    assert any("Skin 1.png" in e.file for e in portraits)

    assert all("__MACOSX" not in e.file for e in entries)
    assert all(not e.file.lower().endswith(".txt") for e in entries)
    # No art or output was created beside the source.
    assert sorted(p.name for p in pack.iterdir()) == before


# --- zip extraction ---------------------------------------------------------

def test_zip_extraction_matches_the_directory_and_skips_unsafe_entries(tmp_path):
    from_dir = C.extract_catalog(_make_pack(tmp_path))
    archive = _make_zip(tmp_path)
    from_zip = C.extract_catalog(archive)
    assert [e.to_dict() for e in from_zip] == [e.to_dict() for e in from_dir]
    assert all("../" not in e.file and not e.file.startswith("/")
               for e in from_zip)
    # Extraction did not unpack the zip next to it.
    assert sorted(p.name for p in tmp_path.iterdir()) == ["pack", "pack.zip"]


# --- refusals ---------------------------------------------------------------

def test_bad_source_is_refused(tmp_path):
    source = tmp_path / "not-a-pack.txt"
    source.write_text("hello", encoding="utf-8")
    with pytest.raises(C.CatalogError):
        C.extract_catalog(source)
    with pytest.raises(C.CatalogError):
        C.extract_catalog(tmp_path / "missing")


def test_entry_cap_is_refused(tmp_path):
    with pytest.raises(C.CatalogError):
        C.extract_catalog(_make_pack(tmp_path), max_entries=2)


def test_corrupt_zip_is_refused(tmp_path):
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip")
    with pytest.raises(C.CatalogError):
        C.extract_catalog(bad)


# --- summary and CLI --------------------------------------------------------

def test_summary_counts(tmp_path):
    entries = C.extract_catalog(_make_pack(tmp_path))
    summary = C.catalog_summary(entries)
    assert summary["total"] == len(entries) == 6
    assert summary["by_theme"] == {"limezu": 6}
    assert summary["by_size"]["16x16"] == 4
    assert summary["by_size"]["32x32"] == 1
    assert summary["by_size"]["48x48"] == 1
    assert summary["by_category"]["portrait"] == 2


def test_cli_emits_metadata_and_refuses_bad_source(tmp_path):
    pack = _make_pack(tmp_path)
    ok = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "office_asset_catalog.py"),
         "--source", str(pack), "--summary"],
        capture_output=True, text=True, cwd=REPO_ROOT, check=False,
    )
    assert ok.returncode == 0, ok.stderr
    payload = json.loads(ok.stdout)
    assert payload["total"] == 6
    assert payload["by_category"]["theme_sorter"] == 2

    bad = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "office_asset_catalog.py"),
         "--source", str(tmp_path / "missing")],
        capture_output=True, text=True, cwd=REPO_ROOT, check=False,
    )
    assert bad.returncode == 2
    assert "office_asset_catalog:" in bad.stderr


def test_out_file_is_metadata_only(tmp_path):
    pack = _make_pack(tmp_path)
    out = tmp_path / "catalog.json"
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "office_asset_catalog.py"),
         "--source", str(pack), "--out", str(out), "--pretty"],
        capture_output=True, text=True, cwd=REPO_ROOT, check=False,
    )
    assert result.returncode == 0, result.stderr
    rows = json.loads(out.read_text(encoding="utf-8"))
    assert len(rows) == 6
    assert all(row["file"].endswith((".png", ".webp")) for row in rows)
    # The only new file is the JSON metadata the caller explicitly asked for.
    assert sorted(p.name for p in pack.iterdir()) == [
        ".DS_Store", "16x16", "32x32", "48x48", "__MACOSX", "notes.txt"]

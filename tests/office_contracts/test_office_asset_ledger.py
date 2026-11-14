"""P0-03 — the per-file asset ledger reads metadata only and fails closed.

The ledger is generated from an operator-owned paid-art root and must stay
outside the repository. These tests build a tiny synthetic tree (no real art)
and exercise the CLI: rows, sha256, image dimensions, the in-repo refusal and
the exit codes.
"""
from __future__ import annotations

import hashlib
import json
import struct
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "office_asset_ledger.py"


def _png_header(width: int, height: int) -> bytes:
    """A minimal header-only PNG (signature + IHDR chunk) for dimension parsing."""
    ihdr = struct.pack(">II", width, height) + b"\x08\x06\x00\x00\x00"
    chunk = struct.pack(">I", len(ihdr)) + b"IHDR" + ihdr + b"\x00\x00\x00\x00"
    return b"\x89PNG\r\n\x1a\n" + chunk


def _run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    env = {"PYTHONPATH": str(REPO_ROOT), "PATH": "/usr/bin:/bin"}
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=str(cwd or REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )


def _build_tree(root: Path) -> None:
    (root / "PackA" / "floors").mkdir(parents=True)
    (root / "PackA" / "floors" / "floor_red.png").write_bytes(_png_header(16, 16))
    (root / "PackA" / "wide.png").write_bytes(_png_header(48, 32))
    (root / "PackA" / "notes.txt").write_text("hello metadata\n", encoding="utf-8")


def test_ledger_rows_sha_and_dimensions(tmp_path: Path) -> None:
    source = tmp_path / "art"
    _build_tree(source)
    out = tmp_path / "out" / "manifest.json"
    result = _run("--source", str(source), "--out", str(out), "--check")
    assert result.returncode == 0, result.stderr
    assert out.is_file()

    ledger = json.loads(out.read_text(encoding="utf-8"))
    rows = {row["path"]: row for row in ledger["files"]}
    assert len(rows) == 3
    assert ledger["summary"]["files"] == 3

    red = rows["PackA/floors/floor_red.png"]
    assert red["width"] == 16 and red["height"] == 16
    assert red["pack"] == "PackA"
    assert red["extension"] == ".png"
    assert red["paid_local_only"] is True
    assert red["intended_use"] in {"room", "style", "character", "portrait",
                                   "ui", "outside", "fx", "unknown"}
    expected = hashlib.sha256(
        (source / "PackA" / "floors" / "floor_red.png").read_bytes()).hexdigest()
    assert red["sha256"] == expected
    assert red["bytes"] == (source / "PackA" / "floors" / "floor_red.png").stat().st_size

    wide = rows["PackA/wide.png"]
    assert (wide["width"], wide["height"]) == (48, 32)

    notes = rows["PackA/notes.txt"]
    assert notes["width"] is None and notes["height"] is None
    assert notes["sha256"]


def test_ledger_records_license_file_honestly(tmp_path: Path) -> None:
    # No license/readme in the pack: rows stay "unknown"; the ledger never
    # infers a license from a filename.
    no_license = tmp_path / "no-license"
    _build_tree(no_license)
    out = tmp_path / "manifest.json"
    assert _run("--source", str(no_license), "--out", str(out), "--check").returncode == 0
    ledger = json.loads(out.read_text(encoding="utf-8"))
    rows = {row["path"]: row for row in ledger["files"]}
    assert rows["PackA/wide.png"]["license_status"] == "unknown"
    assert rows["PackA/wide.png"]["license_source"] is None

    # A pack-level LICENSE file is recorded as "license_file_present" (the
    # name only, not parsed as terms).
    with_license = tmp_path / "with-license"
    _build_tree(with_license)
    (with_license / "PackA" / "LICENSE.txt").write_text(
        "license text placeholder\n", encoding="utf-8")
    out2 = tmp_path / "manifest2.json"
    assert _run("--source", str(with_license), "--out", str(out2), "--check").returncode == 0
    ledger2 = json.loads(out2.read_text(encoding="utf-8"))
    rows2 = {row["path"]: row for row in ledger2["files"]}
    assert rows2["PackA/wide.png"]["license_status"] == "license_file_present"
    assert rows2["PackA/wide.png"]["license_source"] == "LICENSE.txt"


def test_ledger_refuses_to_write_inside_repo(tmp_path: Path) -> None:
    source = tmp_path / "art"
    _build_tree(source)
    inside = REPO_ROOT / "tmp" / "should-not-exist-ledger.json"
    if inside.exists():
        inside.unlink()
    result = _run("--source", str(source), "--out", str(inside))
    assert result.returncode == 2
    assert "refusing to write inside the repository" in result.stderr
    assert not inside.exists()
    # No stray file was created either.
    assert not (REPO_ROOT / "tmp" / "should-not-exist-ledger.jsonl").exists()


def test_ledger_check_fails_on_empty_source(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    out = tmp_path / "manifest.json"
    result = _run("--source", str(empty), "--out", str(out), "--check")
    assert result.returncode == 1
    assert "no files" in result.stderr


def test_ledger_missing_source_is_usage_error(tmp_path: Path) -> None:
    result = _run("--source", str(tmp_path / "does-not-exist"))
    assert result.returncode == 2


def test_ledger_limit_and_jsonl(tmp_path: Path) -> None:
    source = tmp_path / "art"
    _build_tree(source)
    out = tmp_path / "manifest.jsonl"
    result = _run("--source", str(source), "--out", str(out), "--limit", "2", "--check")
    assert result.returncode == 0, result.stderr
    lines = [line for line in out.read_text(encoding="utf-8").splitlines() if line]
    assert len(lines) == 3  # summary line + 2 limited rows
    summary = json.loads(lines[0])
    assert summary["summary"]["files"] == 2
    assert summary["summary"]["limit"] == 2

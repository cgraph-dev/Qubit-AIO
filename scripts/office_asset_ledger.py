#!/usr/bin/env python3
"""Gather a per-file metadata ledger for a local paid-art pack (Phase 0 / P0-03).

This script reads a *local* operator-owned art root and records metadata for
every file: relative path, byte size, sha256, image dimensions, top-level pack,
extension, a candidate intended use, and an honest license status.

It never copies, decodes, re-encodes or redistributes art. It only:

* walks the tree with :func:`os.walk` (names/sizes/mtimes + a streaming sha256);
* parses PNG/JPEG/WebP *headers* for width/height with a small pure-Python
  reader (no PIL, no image decode);
* writes a metadata JSON/JSONL file **outside the repository**.

The repository must never carry paid-art metadata with operator paths, so
``--out`` is refused when it resolves inside this checkout. Paid art stays
local-only.

    python scripts/office_asset_ledger.py \\
        --source "/path/to/UI Office" \\
        --out /tmp/qoffice-assets/manifest.json --check

Exit codes:
    0  ok
    1  ``--check`` found an incomplete/inconsistent ledger
    2  usage error, unreadable source, or an ``--out`` inside the repository
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

SCHEMA = "qubit-office-asset-ledger/1"

REPO_ROOT = Path(__file__).resolve().parent.parent

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
_LICENSE_NAMES = (
    "license", "license.txt", "license.md", "licence", "licence.txt",
    "licence.md", "copying", "readme", "readme.txt", "readme.md",
)

#: Candidate use set, widest first-match wins.
_USE_RULES: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("ui", ("userinterface", "user_interface", "user-interface", "modernui",
            "modern_user_interface", "gui", "hud", "button")),
    ("portrait", ("portrait",)),
    ("character", ("character", "generator", "sprite_generator")),
    ("outside", ("serene", "village", "outside", "exterior", "outdoor",
                 "terrain")),
    ("fx", ("fx", "vfx", "effect", "particle", "shader")),
    ("style", ("theme_sorter", "theme", "style")),
    ("room", ("interior", "room", "floor", "wall", "furniture", "tile",
              "desk", "door", "window", "prop")),
)

INTENDED_USE_VALUES = ("room", "style", "character", "portrait", "ui",
                       "outside", "fx", "unknown")


class LedgerError(Exception):
    """A usage/Source problem. Carries a process exit code."""

    def __init__(self, message: str, code: int = 2):
        self.code = code
        super().__init__(message)


# --------------------------------------------------------------- image sizes
def _read_exact(f, count: int) -> bytes:
    data = f.read(count)
    return data if len(data) == count else b""


def _png_size(f) -> Optional[Tuple[int, int]]:
    f.seek(0)
    head = _read_exact(f, 24)
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        return None
    width = int.from_bytes(head[16:20], "big")
    height = int.from_bytes(head[20:24], "big")
    if width <= 0 or height <= 0:
        return None
    return width, height


_JPEG_SOF_MARKERS = {
    0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
    0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
}


def _jpeg_size(f) -> Optional[Tuple[int, int]]:
    f.seek(0)
    if _read_exact(f, 2) != b"\xff\xd8":
        return None
    # Bound the marker walk so a corrupt file cannot spin forever.
    for _ in range(4096):
        byte = f.read(1)
        while byte and byte != b"\xff":
            byte = f.read(1)
        if not byte:
            return None
        marker = f.read(1)
        while marker == b"\xff":
            marker = f.read(1)
        if not marker:
            return None
        code = marker[0]
        if code in (0xD8, 0xD9, 0x01) or 0xD0 <= code <= 0xD7:
            continue
        len_bytes = _read_exact(f, 2)
        if not len_bytes:
            return None
        seg_len = int.from_bytes(len_bytes, "big")
        if seg_len < 2:
            return None
        if code in _JPEG_SOF_MARKERS:
            payload = _read_exact(f, seg_len - 2)
            if len(payload) < 5:
                return None
            height = int.from_bytes(payload[1:3], "big")
            width = int.from_bytes(payload[3:5], "big")
            if width <= 0 or height <= 0:
                return None
            return width, height
        f.seek(seg_len - 2, os.SEEK_CUR)
    return None


def _webp_size(f) -> Optional[Tuple[int, int]]:
    f.seek(0)
    header = _read_exact(f, 30)
    if len(header) < 30 or header[:4] != b"RIFF" or header[8:12] != b"WEBP":
        return None
    chunk = header[12:16]
    if chunk == b"VP8X":
        payload = header[20:30]
        if len(payload) < 10:
            return None
        width = 1 + int.from_bytes(payload[0:3], "little")
        height = 1 + int.from_bytes(payload[3:6], "little")
        return width, height
    if chunk == b"VP8 ":
        body = header[20:30]
        if len(body) < 10 or body[3:6] != b"\x9d\x01\x2a":
            return None
        width = int.from_bytes(body[6:8], "little") & 0x3FFF
        height = int.from_bytes(body[8:10], "little") & 0x3FFF
        return (width, height) if width and height else None
    if chunk == b"VP8L":
        body = header[20:25]
        if len(body) < 5 or body[0] != 0x2F:
            return None
        b = body
        width = 1 + (((b[2] & 0x3F) << 8) | b[1])
        height = 1 + (((b[4] & 0x0F) << 10) | (b[3] << 2) | ((b[2] & 0xC0) >> 6))
        return width, height
    return None


def image_dimensions(path: Path) -> Optional[Tuple[int, int]]:
    """Return ``(width, height)`` for PNG/JPEG/WebP, else ``None``.

    Only header bytes are parsed; the pixel data is never decoded.
    """
    suffix = path.suffix.lower()
    if suffix not in _IMAGE_SUFFIXES:
        return None
    try:
        with path.open("rb") as handle:
            if suffix == ".png":
                return _png_size(handle)
            if suffix in (".jpg", ".jpeg"):
                return _jpeg_size(handle)
            return _webp_size(handle)
    except OSError:
        return None


# ------------------------------------------------------------------- helpers
def sha256_file(path: Path, chunk: int = 1024 * 1024) -> Optional[str]:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while True:
                block = handle.read(chunk)
                if not block:
                    break
                digest.update(block)
    except OSError:
        return None
    return digest.hexdigest()


def _is_inside_repo(path: Path) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path.absolute()
    try:
        resolved.relative_to(REPO_ROOT)
        return True
    except ValueError:
        return False


def _find_license_text(directory: Path) -> Optional[str]:
    if not directory.is_dir():
        return None
    try:
        for entry in sorted(directory.iterdir()):
            if not entry.is_file():
                continue
            if entry.name.lower() in _LICENSE_NAMES:
                return entry.name
    except OSError:
        return None
    return None


def classify_use(relative: str) -> str:
    needle = relative.replace(os.sep, "/").lower()
    for use, tokens in _USE_RULES:
        if any(token in needle for token in tokens):
            return use
    return "unknown"


def top_level_pack(relative: str) -> str:
    parts = Path(relative).parts
    return parts[0] if parts else "(root)"


def _iter_files(source: Path) -> Iterable[Path]:
    for root, dirs, files in os.walk(source):
        dirs.sort()
        for name in sorted(files):
            candidate = Path(root) / name
            if candidate.is_file():
                yield candidate


# ---------------------------------------------------------------------- main
def build_ledger(source: Path, *, limit: Optional[int] = None) -> Dict[str, Any]:
    if not source.exists() or not source.is_dir():
        raise LedgerError(f"source is not a readable directory: {source}")

    license_by_pack: Dict[str, Optional[str]] = {}
    rows: List[Dict[str, Any]] = []
    total_bytes = 0
    walked = 0

    for path in _iter_files(source):
        if limit is not None and len(rows) >= limit:
            break
        walked += 1
        relative = path.relative_to(source).as_posix()
        pack = top_level_pack(relative)
        try:
            stat = path.stat()
            size = int(stat.st_size)
            mtime = int(stat.st_mtime)
        except OSError:
            size = 0
            mtime = 0
        digest = sha256_file(path)
        dims = image_dimensions(path)
        if pack not in license_by_pack:
            license_by_pack[pack] = _find_license_text(source / pack)
        license_name = license_by_pack[pack]
        rows.append({
            "path": relative,
            "bytes": size,
            "sha256": digest,
            "width": dims[0] if dims else None,
            "height": dims[1] if dims else None,
            "pack": pack,
            "extension": path.suffix.lower(),
            "license_status": "license_file_present" if license_name else "unknown",
            "license_source": license_name,
            "intended_use": classify_use(relative),
            "paid_local_only": True,
            "mtime": mtime,
        })
        total_bytes += size

    wal = len(rows)
    summary = {
        "files": wal,
        "bytes": total_bytes,
        "packs": len({row["pack"] for row in rows}),
        "images": sum(1 for row in rows if row["width"] is not None),
        "with_sha256": sum(1 for row in rows if row["sha256"]),
        "with_dimensions": sum(1 for row in rows if row["width"] is not None),
        "license_unknown": sum(1 for row in rows if row["license_status"] == "unknown"),
        "limit": limit,
    }
    return {
        "schema": SCHEMA,
        "generated_at": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "summary": summary,
        "files": rows,
    }


def check_ledger(ledger: Dict[str, Any]) -> List[str]:
    """Return every invariant failure; an empty list means the ledger is sound."""
    problems: List[str] = []
    rows = ledger.get("files") or []
    summary = ledger.get("summary") or {}
    if not rows:
        problems.append("ledger has no files")
    if int(summary.get("files", -1)) != len(rows):
        problems.append(
            f"summary files {summary.get('files')} != emitted rows {len(rows)}")
    for index, row in enumerate(rows):
        label = row.get("path") or f"row {index}"
        if not row.get("sha256"):
            problems.append(f"{label}: missing sha256")
        if row.get("bytes") is None or int(row["bytes"]) < 0:
            problems.append(f"{label}: missing bytes")
        if not row.get("pack"):
            problems.append(f"{label}: missing pack")
        for key in ("width", "height"):
            value = row.get(key)
            if value is not None and int(value) <= 0:
                problems.append(f"{label}: non-positive {key} {value}")
        if row.get("intended_use") not in INTENDED_USE_VALUES:
            problems.append(f"{label}: invalid intended_use {row.get('intended_use')!r}")
        if row.get("paid_local_only") is not True:
            problems.append(f"{label}: paid_local_only must be true")
    return problems


def _write_ledger(out: Path, ledger: Dict[str, Any], *, summary_only: bool) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema": ledger["schema"],
               "generated_at": ledger["generated_at"],
               "summary": ledger["summary"]}
    is_jsonl = out.suffix.lower() in (".jsonl", ".ndjson")
    if summary_only or is_jsonl:
        if summary_only:
            out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")
            return
        with out.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")
            for row in ledger["files"]:
                handle.write(json.dumps(row, sort_keys=True) + "\n")
        return
    out.write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Per-file metadata ledger for a local paid-art pack.")
    parser.add_argument("--source", help="local art root (read for metadata only)")
    parser.add_argument("--out", help="write metadata JSON/JSONL outside the repo")
    parser.add_argument("--check", action="store_true",
                        help="verify the ledger is complete and consistent")
    parser.add_argument("--limit", type=int, default=None,
                        help="process at most N files (tests)")
    parser.add_argument("--summary", action="store_true",
                        help="write/print summary only, not per-file rows")
    args = parser.parse_args(argv)

    if not args.source:
        parser.error("--source is required")
    source = Path(args.source).expanduser()
    out: Optional[Path] = None
    if args.out:
        out = Path(args.out).expanduser()
        if _is_inside_repo(out):
            print(f"office_asset_ledger: refusing to write inside the repository:"
                  f" {out}", file=sys.stderr)
            return 2

    try:
        ledger = build_ledger(source, limit=args.limit)
    except LedgerError as exc:
        print(f"office_asset_ledger: {exc}", file=sys.stderr)
        return exc.code

    problems = check_ledger(ledger)
    if args.check and problems:
        for problem in problems:
            print(f"office_asset_ledger: CHECK FAIL: {problem}", file=sys.stderr)
        return 1

    if out is not None:
        try:
            _write_ledger(out, ledger, summary_only=args.summary)
        except OSError as exc:
            print(f"office_asset_ledger: cannot write {out}: {exc}", file=sys.stderr)
            return 2
        print(f"wrote {ledger['summary']['files']} rows to {out}")
    else:
        sys.stdout.write(json.dumps(
            {"schema": ledger["schema"],
             "generated_at": ledger["generated_at"],
             "summary": ledger["summary"]}, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

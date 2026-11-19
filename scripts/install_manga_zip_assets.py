#!/usr/bin/env python3
"""Install the original 89 SVG/PNG Manga asset pairs into the Qubit web UI.

This does NOT replace the repository's existing Manga gate, React components,
yellow selected-page animation, or the generated cat artwork. ZIP assets are
isolated under public/manga-ui/zip to avoid filename collisions.

Usage:
    python3 scripts/install_manga_zip_assets.py /path/to/qubit_manga_web_install.zip
    python3 scripts/install_manga_zip_assets.py /path/to/zip --verify
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from zipfile import ZipFile, BadZipFile

ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / "frontend/qubit-web/public/manga-ui/zip"
PREFIX = "qubit_manga_web_install/"
ASSET_PREFIX = PREFIX + "public/manga-ui/"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def install(archive: Path, *, verify: bool = False) -> dict:
    with ZipFile(archive) as zf:
        names = set(zf.namelist())
        manifest_name = PREFIX + "asset-manifest.json"
        palette_name = PREFIX + "palette.json"
        for name in (manifest_name, palette_name):
            if name not in names:
                raise ValueError(f"Missing ZIP metadata: {name}")
        manifest = json.loads(zf.read(manifest_name))
        palette = json.loads(zf.read(palette_name))
        if not isinstance(manifest, list) or len(manifest) != 89:
            raise ValueError(f"Expected 89 named Manga assets, got {len(manifest)}")
        found = set()
        hashes = {}
        for entry in manifest:
            base = entry.get("name")
            if not isinstance(base, str) or not base.replace("-", "").isalnum():
                raise ValueError(f"Unsafe or invalid asset name: {base!r}")
            if base in found:
                raise ValueError(f"Duplicate asset: {base}")
            found.add(base)
            for ext in (".svg", ".png"):
                filename = base + ext
                member = ASSET_PREFIX + filename
                if member not in names:
                    raise ValueError(f"Missing original asset: {member}")
                payload = zf.read(member)
                if ext == ".svg" and (b"<svg" not in payload or b"<script" in payload.lower()):
                    raise ValueError(f"Invalid/suspicious SVG: {member}")
                hashes[filename] = sha256(payload)
                target = DEST / filename
                if verify:
                    if not target.is_file() or sha256(target.read_bytes()) != hashes[filename]:
                        raise ValueError(f"Missing or modified installed asset: {target}")
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(payload)
        registered = {p[len(ASSET_PREFIX):] for p in names if p.startswith(ASSET_PREFIX) and p.endswith((".svg", ".png"))}
        if registered != set(hashes):
            extras = sorted(registered - set(hashes))
            missing = sorted(set(hashes) - registered)
            raise ValueError(f"Manifest mismatch, extras={extras}, missing={missing}")
        report = {"source": archive.name, "families": len(found),
                  "assets": len(hashes), "hashes": dict(sorted(hashes.items()))}
        if not verify:
            (DEST / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            (DEST / "palette.json").write_text(json.dumps(palette, indent=2) + "\n", encoding="utf-8")
            (DEST / "checksum-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("zip_file", type=Path)
    parser.add_argument("--verify", action="store_true", help="Compare installed assets against the original ZIP")
    args = parser.parse_args()
    try:
        report = install(args.zip_file, verify=args.verify)
        print(f"{'Verified' if args.verify else 'Installed'} {report['families']} families / {report['assets']} exact source assets at {DEST}")
        return 0
    except (BadZipFile, OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"Manga asset install failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

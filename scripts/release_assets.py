#!/usr/bin/env python3
"""The canonical, complete production release asset set.

A production release is a set of assets, not just ``wheel``/``sdist``.  A
partial upload (for example a workflow dispatch that rewrites ``SHA256SUMS``
over only the wheel and sdist) silently destroys the checksum manifest for an
existing release and leaves the bootstraps, installers and manifests behind.

This module is the single source of truth for that set.  Both
``scripts/prepare_production_release.py`` (which generates the assets) and
``.github/workflows/release.yml`` (which stages and uploads them) validate
against it, so a future workflow change cannot quietly drop an asset.

Usage::

    python3 scripts/release_assets.py --source OUT --dest ASSETS \\
        --manifest ASSETS_MANIFEST.json
    python3 scripts/release_assets.py --check ASSETS --manifest ASSETS_MANIFEST.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

SCHEMA = "qubit.release-asset-set/1"

#: Fixed-name assets every production release must publish.  ``SHA256SUMS`` is
#: the checksum file itself; it must cover every other member of this set plus
#: the versioned build artifacts below.
REQUIRED_ASSETS = (
    "bootstrap.sh",
    "bootstrap.ps1",
    "install.sh",
    "install.ps1",
    "qubit-release.tar.gz",
    "qubit-release.zip",
    "component-manifest.json",
    "release-notes.md",
    "release-gate-report.json",
    "release-summary.json",
    "SHA256SUMS",
)

#: Build artifacts whose exact names carry the version.  Exactly one of each
#: must be present; a release cannot be certified without all three.
DYNAMIC_ASSETS = {
    "wheel": ("", ".whl"),
    "sdist": ("qubit_voicebridge-", ".tar.gz"),
    "source": ("Qubit-", "-source.tar.gz"),
}


class ReleaseError(SystemExit):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_dynamic(kind: str, name: str) -> bool:
    prefix, suffix = DYNAMIC_ASSETS[kind]
    return name.startswith(prefix) and name.endswith(suffix)


def _release_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        raise ReleaseError("release asset directory does not exist: %s" % directory)
    return [path for path in sorted(directory.iterdir()) if path.is_file()]


def collect_assets(directory: Path) -> list[Path]:
    """Return the complete asset set in *directory* or refuse a partial one."""
    directory = Path(directory)
    files = _release_files(directory)
    names = {path.name for path in files}
    selected = [path for path in files if path.name in REQUIRED_ASSETS]
    missing = [name for name in REQUIRED_ASSETS if name not in names]
    for kind in DYNAMIC_ASSETS:
        matches = [path for path in files if _is_dynamic(kind, path.name)]
        if matches:
            selected.extend(matches)
            continue
        prefix, suffix = DYNAMIC_ASSETS[kind]
        missing.append("%s*%s" % (prefix, suffix))
    if missing:
        raise ReleaseError(
            "incomplete release asset set in %s; missing: %s"
            % (directory, ", ".join(missing))
        )
    return sorted(set(selected), key=lambda path: path.name)


def asset_records(assets: list[Path]) -> list[dict]:
    return [
        {"name": path.name, "sha256": sha256(path), "size_bytes": path.stat().st_size}
        for path in assets
    ]


def write_manifest(assets: list[Path], target: Path) -> dict:
    data = {
        "schema": SCHEMA,
        "required": list(REQUIRED_ASSETS),
        "dynamic": {kind: list(parts) for kind, parts in DYNAMIC_ASSETS.items()},
        "assets": asset_records(assets),
    }
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return data


def _checksum_entries(path: Path) -> dict[str, str]:
    entries: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        digest, _, name = line.partition("  ")
        if not digest or not name:
            raise ReleaseError("malformed SHA256SUMS line: %r" % line)
        entries[name.strip()] = digest
    return entries


def _verify_sha256sums(directory: Path, assets: list[Path]) -> None:
    sums = directory / "SHA256SUMS"
    if not sums.is_file():
        raise ReleaseError("missing SHA256SUMS in %s" % directory)
    entries = _checksum_entries(sums)
    uncovered = [
        path.name for path in assets
        if path.name != "SHA256SUMS" and path.name not in entries
    ]
    if uncovered:
        raise ReleaseError(
            "SHA256SUMS does not cover: %s" % ", ".join(sorted(uncovered))
        )
    for name, digest in entries.items():
        path = directory / name
        if not path.is_file():
            raise ReleaseError("SHA256SUMS names a missing file: %s" % name)
        if sha256(path) != digest:
            raise ReleaseError("SHA256SUMS mismatch for %s" % name)


def check_assets(directory: Path, manifest_path: Path | None = None) -> list[Path]:
    """Verify a complete set, its checksum coverage, and an optional manifest."""
    directory = Path(directory)
    assets = collect_assets(directory)
    _verify_sha256sums(directory, assets)
    if manifest_path is not None and Path(manifest_path).is_file():
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        expected = {
            entry["name"]: entry["sha256"]
            for entry in manifest.get("assets", [])
        }
        actual = {path.name: sha256(path) for path in assets}
        missing = sorted(set(expected) - set(actual))
        unexpected = sorted(set(actual) - set(expected))
        mismatched = sorted(
            name for name, digest in expected.items()
            if actual.get(name) != digest
        )
        if missing or unexpected or mismatched:
            raise ReleaseError(
                "staged asset set does not match the manifest "
                "(missing=%s unexpected=%s mismatched=%s)"
                % (missing, unexpected, mismatched)
            )
    return assets


def stage_assets(source: Path, dest: Path,
                 manifest_path: Path | None = None) -> list[Path]:
    """Copy the complete set from *source* into *dest*; refuse a partial set."""
    assets = collect_assets(Path(source))
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    staged = []
    for path in assets:
        target = dest / path.name
        shutil.copy2(path, target)
        staged.append(target)
    if manifest_path is not None:
        write_manifest(staged, Path(manifest_path))
    return staged


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Stage and verify the complete production release asset set."
    )
    parser.add_argument("--source", type=Path,
                        help="directory produced by prepare_production_release.py")
    parser.add_argument("--dest", type=Path,
                        help="staging directory for the complete asset set")
    parser.add_argument("--manifest", type=Path,
                        help="manifest path to write (stage) or verify against (check)")
    parser.add_argument("--check", type=Path,
                        help="verify an already staged asset directory")
    args = parser.parse_args(argv)

    try:
        if args.check is not None:
            assets = check_assets(args.check, args.manifest)
            print("release-assets: OK (%d assets)" % len(assets))
            for path in assets:
                print("  %s  %s" % (sha256(path), path.name))
            return 0
        if args.source is None or args.dest is None:
            parser.error("--source and --dest are required unless --check is used")
        staged = stage_assets(args.source, args.dest, args.manifest)
        print("release-assets: staged %d assets into %s" % (len(staged), args.dest))
        return 0
    except ReleaseError as exc:
        print("release-assets: FAILED: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

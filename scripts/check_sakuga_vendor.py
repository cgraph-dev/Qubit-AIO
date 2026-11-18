#!/usr/bin/env python3
"""Verify the vendored Sakuga VFX files have not drifted.

The engine and components are copied byte-identical from
https://github.com/finktheartist/sakuga-vfx (MIT, pin c3dabb173928). Placeholder
edits to a vendored file are the failure mode this gate exists to catch: a
drawing change belongs upstream or in a documented local patch, never as a
silent edit here.

Two copies ship (dashboard public dir + Electron public dir). Both must match
the recorded hashes and each other.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
COPIES = [
    REPO / "frontend" / "qubit-web" / "public" / "sakuga-vfx",
    REPO / "qubit_runtime" / "apps" / "desktop" / "public" / "sakuga-vfx",
]
MANIFEST = "VENDOR.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(root: Path) -> list[str]:
    manifest_path = root / MANIFEST
    if not manifest_path.is_file():
        return [f"{root}: missing {MANIFEST}"]
    manifest = json.loads(manifest_path.read_text())
    expected: dict[str, str] = manifest.get("files") or {}
    if not expected:
        return [f"{root}: empty file manifest"]
    problems: list[str] = []
    on_disk = {
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.is_file() and path.name != MANIFEST
    }
    for rel, digest in sorted(expected.items()):
        path = root / rel
        if not path.is_file():
            problems.append(f"{root.name}: missing {rel}")
        elif sha256(path) != digest:
            problems.append(f"{root.name}: drifted {rel}")
    for rel in sorted(on_disk - set(expected)):
        problems.append(f"{root.name}: unexpected {rel}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify (default)")
    args = parser.parse_args(argv)
    del args
    problems: list[str] = []
    for root in COPIES:
        problems.extend(verify(root))
    first, second = COPIES
    if first.is_dir() and second.is_dir():
        for path in sorted(first.rglob("*")):
            if not path.is_file() or path.name == MANIFEST:
                continue
            rel = path.relative_to(first)
            other = second / rel
            if not other.is_file() or sha256(path) != sha256(other):
                problems.append(f"copies differ: {rel}")
    if problems:
        print("sakuga vendor: DRIFT")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"sakuga vendor: OK ({len(COPIES)} copies)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

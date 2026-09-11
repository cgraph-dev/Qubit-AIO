#!/usr/bin/env python3
"""Generate the per-file provenance manifest for the vendored Hermes fork.

The Qubit product executes a Qubit-owned fork of the pinned Hermes source
(see docs/QUBIT_OWNER_AMENDMENT_2026-09-11_HERMES_RUNTIME.md). This script
records, for every file in the local fork, the upstream commit blob hash and
the local sha256, and flags any local patch. It is deterministic and
re-runnable; the output is docs/QUBIT_HERMES_VENDOR_MANIFEST.json.

Usage:
    scripts/hermes_vendor_manifest.py [--upstream /tmp/opencode/pinned/hermes-agent]
                                      [--local qubit_hermes]
                                      [--out docs/QUBIT_HERMES_VENDOR_MANIFEST.json]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PIN = "2237be355906fbe6065ce1815711eee52b2d646e"
UPSTREAM_REPO = "https://github.com/NousResearch/hermes-agent"

_SKIP_DIR_PARTS = {"__pycache__", ".git", ".pytest_cache"}
_SKIP_SUFFIX = (".pyc", ".pyo")


def _git(args, cwd):
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True, capture_output=True, text=True,
    ).stdout


def _is_skipped(rel: str) -> bool:
    parts = set(Path(rel).parts)
    if parts & _SKIP_DIR_PARTS:
        return True
    return rel.endswith(_SKIP_SUFFIX)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--upstream", default="/tmp/opencode/pinned/hermes-agent")
    ap.add_argument("--local", default="qubit_hermes")
    ap.add_argument("--out", default="docs/QUBIT_HERMES_VENDOR_MANIFEST.json")
    args = ap.parse_args(argv)

    upstream = Path(args.upstream)
    local = (REPO / args.local).resolve()
    out = (REPO / args.out).resolve()

    if not upstream.is_dir():
        print(f"upstream pin not found: {upstream}", file=sys.stderr)
        return 2
    rev = _git(["rev-parse", "HEAD"], upstream).strip()
    if rev != PIN:
        print(
            f"REFUSING: upstream revision {rev} != pinned {PIN}",
            file=sys.stderr,
        )
        return 2

    tree: dict[str, str] = {}
    for line in _git(["ls-tree", "-r", "HEAD"], upstream).splitlines():
        meta, path = line.split("\t", 1)
        mode, otype, blob = meta.split()
        if otype != "blob" or _is_skipped(path):
            continue
        tree[path] = blob

    files: dict[str, dict] = {}
    local_seen: set[str] = set()
    for path in sorted(local.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(local).as_posix()
        if _is_skipped(rel):
            continue
        data = path.read_bytes()
        upstream_blob = tree.get(rel)
        local_sha = hashlib.sha256(data).hexdigest()
        # Compare against the upstream WORKING-TREE bytes, not the committed
        # blob, so line-ending/attribute normalization in the pinned checkout
        # is not reported as a local patch. The committed blob hash is still
        # recorded as the provenance anchor.
        upstream_sha = None
        up_file = upstream / rel
        if up_file.is_file():
            upstream_sha = hashlib.sha256(up_file.read_bytes()).hexdigest()
        entry = {
            "sha256": local_sha,
            "upstream_blob": upstream_blob,
            "upstream_sha256": upstream_sha,
            "patched": upstream_sha is not None and upstream_sha != local_sha,
        }
        if upstream_blob is not None:
            local_seen.add(rel)
        files[rel] = entry

    missing = sorted(set(tree) - local_seen)
    extra = sorted(rel for rel, e in files.items() if e["upstream_blob"] is None)
    patched = sorted(rel for rel, e in files.items() if e["patched"])

    manifest = {
        "upstream_repo": UPSTREAM_REPO,
        "upstream_revision": rev,
        "upstream_tag": "v2026.9.7",
        "upstream_license": "MIT License, Copyright (c) 2025 Nous Research",
        "local_root": args.local,
        "file_count": len(files),
        "identical": sum(1 for e in files.values() if not e["patched"]),
        "patched": patched,
        "extra_local": extra,
        "missing_from_local": missing,
        "files": files,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n",
                   encoding="utf-8")
    print(f"wrote {out.relative_to(REPO)}")
    print(f"  total={len(files)} identical={manifest['identical']} "
          f"patched={len(patched)} extra={len(extra)} missing={len(missing)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

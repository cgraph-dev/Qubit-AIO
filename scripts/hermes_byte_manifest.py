#!/usr/bin/env python3
"""Regenerate the pinned pristine Hermes byte manifest.

Owner decision 2026-09-23 (#5): the pinned byte manifest is regenerated
mechanically from the **real** pinned source. Hashes are never hand-written or
guessed; if the pinned source is unavailable the generator stops with
``BLOCKED — PINNED SOURCE REQUIRED`` naming the exact prerequisite.

This manifest backs the differential runner's fail-closed ``byte_parity`` gate
(`scripts/differential_parity.py::_byte_parity_gate`), which consumes the
in-repo copy at
``docs/differential/byte_manifest/hermes/HERMES_BYTE_MANIFEST.json``.

It records, for every file tracked at the pinned upstream revision, the git blob
id, the file's SHA-256, and a byte classification:

* ``IDENTICAL``       — bytes reproduce the pinned git blob (an EOL-only
  difference caused by the upstream CRLF checkout attribute normalizes to the
  same blob and is recorded as ``eol_normalized`` metadata, still IDENTICAL)
* ``PATCHED``         — an actionable, unexplained byte difference (fails the gate)
* ``MISSING``         — tracked at the pin but absent from the baseline checkout
* ``NOT_APPLICABLE``  — untracked generated/runtime artifact (gate-exempt)

The generator reads the pinned baseline's working tree and verifies each file
against its pinned git blob (recomputing the git blob hash locally, no per-file
subprocess). Output is deterministic: paths sorted, no timestamps.

Usage:
    scripts/hermes_byte_manifest.py
    scripts/hermes_byte_manifest.py --check
    scripts/hermes_byte_manifest.py --upstream /tmp/opencode/pinned/hermes-agent \\
        --pin 69948c005791d4d0e51670ca015238c2c7663749 \\
        --out docs/differential/byte_manifest/hermes/HERMES_BYTE_MANIFEST.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# The vendored pin (owner amendment 2026-09-25, DECISIONS #25). The read-only
# reference install is the source of record for this revision's exact blobs.
PIN = "69948c005791d4d0e51670ca015238c2c7663749"
UPSTREAM_TAG = "v0.21.5"
UPSTREAM_REPO = "https://github.com/NousResearch/hermes-agent"
UPSTREAM_LICENSE = "MIT License, Copyright (c) 2025 Nous Research"

DEFAULT_OUT = (
    REPO / "docs" / "differential" / "byte_manifest" / "hermes"
    / "HERMES_BYTE_MANIFEST.json"
)
#: Read-only candidate pinned trees, in preference order, so ``--check`` works
#: on a fresh checkout without an operator-supplied ephemeral path: the first
#: candidate whose HEAD equals the pin is selected (mirrors
#: ``scripts/source_inventory.py::_resolve_pin``). Env overrides
#: ``QUBIT_PINNED_HERMES`` / ``QUBIT_REFERENCE_HOME`` are tried first; if no
#: candidate matches, the first existing one is used and a loud warning names
#: the mismatch (the caller's revision check then fails closed).
#:
#: The durable reference install is listed first because the committed manifest
#: embeds ``source.path``; selecting it keeps ``--check`` reproducible and a
#: write byte-identical to the frozen artifact. The pristine ``/tmp`` clones are
#: the fallbacks for a checkout with no reference install present.
DEFAULT_UPSTREAM_CANDIDATES = (
    Path.home() / ".hermes" / "hermes-agent",
    Path(f"/tmp/opencode/pinned/hermes-agent-{PIN[:10]}"),
    Path(f"/tmp/qubit-pin-{PIN[:10]}"),
    Path("/tmp/opencode/pinned/hermes-agent"),
)

ENV_CANDIDATE_VARS = ("QUBIT_PINNED_HERMES", "QUBIT_REFERENCE_HOME")

ARTIFACT = "HERMES_BYTE_MANIFEST"
SCHEMA_VERSION = 1

# Untracked generated/runtime artifacts that are exempt from the gate.
DISPOSABLE_PARTS = {
    "__pycache__", ".pytest_cache", ".pytest-cache", ".mypy_cache",
    "node_modules", ".cache", ".venv", "venv", "build",
}
DISPOSABLE_SUFFIX = (".pyc", ".pyo")

IDENTICAL = "IDENTICAL"
PATCHED = "PATCHED"
MISSING = "MISSING"
NOT_APPLICABLE = "NOT_APPLICABLE"

BLOCKED = "BLOCKED — PINNED SOURCE REQUIRED"


class Blocked(Exception):
    """Raised when the pinned source prerequisite is unavailable."""


def _run(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True, capture_output=True, text=True,
    ).stdout


def _git_blob_sha(data: bytes) -> str:
    header = b"blob " + str(len(data)).encode("ascii") + b"\0"
    return hashlib.sha1(header + data).hexdigest()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _candidate_paths() -> tuple[Path, ...]:
    """Env overrides first, then the ephemeral-then-durable default candidates."""
    env: list[Path] = []
    for var in ENV_CANDIDATE_VARS:
        value = os.environ.get(var)
        if value:
            env.append(Path(value).expanduser())
    return tuple(env) + DEFAULT_UPSTREAM_CANDIDATES


def discover_upstream(explicit: str | None, pin: str = PIN) -> Path:
    """Resolve the pinned clone: explicit path, else a candidate whose HEAD == pin.

    Falls back to the first existing candidate with a loud warning so the
    caller's revision check fails closed on a mismatch rather than silently
    reading a wrong tree.
    """
    if explicit:
        return Path(explicit).expanduser()
    candidates = _candidate_paths()
    first_existing: Path | None = None
    for candidate in candidates:
        if not candidate.is_dir():
            continue
        if first_existing is None:
            first_existing = candidate
        try:
            rev = _run(candidate, "rev-parse", "HEAD").strip()
        except Exception:  # noqa: BLE001 - not a git checkout; try the next
            continue
        if rev == pin:
            return candidate
    if first_existing is not None:
        print(
            f"WARNING: no candidate clone at pin {pin}; using first existing "
            f"{first_existing}. Pass --upstream or set QUBIT_PINNED_HERMES.",
            file=sys.stderr,
        )
        return first_existing
    raise Blocked(
        "a read-only git clone of the pinned Hermes source at "
        f"{pin} ({UPSTREAM_TAG}); checked {[str(p) for p in candidates]}. "
        "Set QUBIT_PINNED_HERMES/QUBIT_REFERENCE_HOME or pass --upstream."
    )


def _require_revision(upstream: Path, pin: str) -> str:
    try:
        _run(upstream, "cat-file", "-t", pin)
    except subprocess.CalledProcessError as exc:  # noqa: PERF203
        raise Blocked(
            f"revision {pin} is absent from the pinned clone at {upstream}: "
            f"{exc.stderr.strip()}"
        ) from exc
    return pin


def _tracked_blobs(upstream: Path, rev: str) -> list[tuple[str, str, str]]:
    """Return sorted (mode, blob_sha, path) for every tracked entry at ``rev``."""
    out = _run(upstream, "ls-tree", "-r", "-z", rev)
    entries: list[tuple[str, str, str]] = []
    for record in out.split("\0"):
        if not record:
            continue
        meta, path = record.split("\t", 1)
        mode, otype, blob = meta.split()
        if otype not in ("blob",):
            continue
        entries.append((mode, blob, path))
    entries.sort(key=lambda item: item[2])
    return entries


def _classify(path: Path, mode: str, blob: str) -> dict:
    """Compare one baseline file against its pinned git blob."""
    if mode == "120000":
        if not path.is_symlink():
            return {"classification": MISSING}
        target = path.readlink().as_posix().encode("utf-8")
        sha = _sha256(target)
        if _git_blob_sha(target) == blob:
            return {"classification": IDENTICAL, "sha256": sha,
                    "checkout_sha256": sha, "eol_normalized": False}
        return {"classification": PATCHED, "sha256": sha,
                "checkout_sha256": sha, "eol_normalized": False}
    if not path.is_file():
        return {"classification": MISSING}
    data = path.read_bytes()
    checkout_sha = _sha256(data)
    if _git_blob_sha(data) == blob:
        return {"classification": IDENTICAL, "sha256": checkout_sha,
                "checkout_sha256": checkout_sha, "eol_normalized": False}
    # Explained difference: the upstream CRLF checkout attribute. Normalizing
    # CRLF->LF reproduces the pinned git blob exactly; ``sha256`` is then the
    # canonical pinned (blob) content and ``checkout_sha256`` the as-read bytes.
    normalized = data.replace(b"\r\n", b"\n")
    if normalized != data and _git_blob_sha(normalized) == blob:
        return {"classification": IDENTICAL, "sha256": _sha256(normalized),
                "checkout_sha256": checkout_sha, "eol_normalized": True}
    return {"classification": PATCHED, "sha256": checkout_sha,
            "checkout_sha256": checkout_sha, "eol_normalized": False}


def _not_applicable(upstream: Path) -> list[str]:
    """Untracked generated/runtime artifacts present in the baseline checkout."""
    try:
        out = _run(upstream, "ls-files", "--others", "--exclude-standard", "-z")
    except subprocess.CalledProcessError:
        return []
    paths = []
    for record in out.split("\0"):
        if not record:
            continue
        parts = set(Path(record).parts)
        if parts & DISPOSABLE_PARTS or record.endswith(DISPOSABLE_SUFFIX):
            paths.append(record)
    return sorted(paths)


def build_manifest(upstream: Path, pin: str) -> tuple[dict, bytes]:
    rev = _require_revision(upstream, pin)
    head = _run(upstream, "rev-parse", "HEAD").strip()
    entries: list[dict] = []
    counts = {
        "total": 0, "identical": 0, "patched": 0, "missing": 0,
        "not_applicable": 0, "eol_normalized": 0,
    }
    for mode, blob, rel in _tracked_blobs(upstream, rev):
        result = _classify(upstream / rel, mode, blob)
        classification = result["classification"]
        counts["total"] += 1
        counts[classification.lower()] += 1
        if result.get("eol_normalized"):
            counts["eol_normalized"] += 1
        entries.append({
            "path": rel,
            "mode": mode,
            "blob": blob,
            "sha256": result.get("sha256"),
            "checkout_sha256": result.get("checkout_sha256"),
            "eol_normalized": bool(result.get("eol_normalized")),
            "classification": classification,
        })
    for rel in _not_applicable(upstream):
        counts["not_applicable"] += 1
        entries.append({
            "path": rel,
            "mode": None,
            "blob": None,
            "sha256": None,
            "checkout_sha256": None,
            "eol_normalized": False,
            "classification": NOT_APPLICABLE,
        })
    entries.sort(key=lambda item: item["path"])

    doc = {
        "artifact": ARTIFACT,
        "schema_version": SCHEMA_VERSION,
        "contract": {
            "project": "hermes",
            "repo": UPSTREAM_REPO,
            "tag": UPSTREAM_TAG,
            "pinned_commit": rev,
            "license": UPSTREAM_LICENSE,
        },
        "source": {
            "path": str(upstream),
            "head": head,
            "checkout_matches_pin": head == rev,
            "generator": "scripts/hermes_byte_manifest.py",
        },
        "counts": counts,
        "manifest": entries,
    }
    payload = json.dumps(doc, indent=1, sort_keys=False).encode("utf-8") + b"\n"
    return doc, payload


def _hash_line(payload: bytes, name: str) -> str:
    return f"{hashlib.sha256(payload).hexdigest()}  {name}\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", help="pinned Hermes git clone")
    parser.add_argument("--pin", default=PIN, help="pinned revision")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--check", action="store_true",
                        help="verify the on-disk manifest is up to date")
    args = parser.parse_args(argv)

    out = Path(args.out)
    try:
        upstream = discover_upstream(args.upstream, args.pin)
        doc, payload = build_manifest(upstream, args.pin)
    except Blocked as exc:
        print(f"{BLOCKED}: {exc}", file=sys.stderr)
        return 3

    if args.check:
        if not out.is_file():
            print(f"{BLOCKED}: {out} is absent", file=sys.stderr)
            return 3
        existing = out.read_bytes()
        if existing != payload:
            print(f"OUT OF DATE: {out}", file=sys.stderr)
            return 1
        hash_file = out.parent / "manifest.sha256"
        expected = _hash_line(payload, out.name)
        if not hash_file.is_file() or hash_file.read_text() != expected:
            print(f"OUT OF DATE: {hash_file}", file=sys.stderr)
            return 1
        print(f"OK: {out} ({doc['counts']['total']} tracked rows)")
        return 0

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(payload)
    (out.parent / "manifest.sha256").write_text(_hash_line(payload, out.name))
    print(f"wrote {out}")
    print(f"manifest sha256: {hashlib.sha256(payload).hexdigest()}")
    print(f"rows: {doc['counts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

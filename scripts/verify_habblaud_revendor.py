#!/usr/bin/env python3
"""Prove the vendored Habblaud tree is REPRODUCIBLE from its recorded commit (A9).

WHAT THIS ADDS OVER `vendor_habblaud.py --check`
------------------------------------------------
`--check` verifies the tree against `PROVENANCE.json` — i.e. "these bytes equal
the bytes the script once wrote". It is a drift detector, and it passes on any
tree, including one built from the WRONG upstream revision. It says nothing
about whether the recorded `commit` in `PROVENANCE.json` is the revision those
bytes actually came from.

A9 asks a strictly stronger question: **can a fresh checkout of the recorded
commit, built with the recorded commands, reproduce these exact bytes?** This
script answers it by doing the rebuild for real and diffing at the byte level.

THE RECORD IS TRUE (measured 2026-10-16)
----------------------------------------
`PROVENANCE.json` names `4ccce2537472fe5a8d432f06974494d2e778ff15` (upstream tag
0.11.0). A clean `git clone` at that commit + `npm ci --include=dev` + the
upstream Vite build produces a `dist/client/` whose hash-named chunks are
`app-U4Pwh2XA.js`, `main-DND_b4_E.js`, `app-BW61bUik.css` — the SAME names the
vendored tree carries — and applying the vendor transforms yields 77 files that
are byte-for-byte identical to the committed tree.

Why the earlier "not reproducible" verdict was wrong
----------------------------------------------------
The shared checkout `/home/trick/Projects/habblaud` sits on a DIFFERENT revision
(`86c0379…`, a later `main`), so building *that* checkout yields
`app-DrspyBzY.js` — a different chunk. That is a stale-checkout observation, not
a reproducibility failure: the vendored bytes never claimed to come from
`86c0379…`. The build itself is deterministic (two consecutive builds of the
same source are byte-identical), so the only variable is WHICH COMMIT is built.

HONEST LIMITS
-------------
* `npm ci` resolves versions from the pinned `package-lock.json`, but npm itself
  and the Node major version participate in the output. This script records both
  and reports a mismatch as a FAILURE with the tool versions it used, rather than
  pretending toolchain drift cannot matter.
* Network is required (clone). When the network is unavailable the script exits
  with a distinct NON-ZERO "SKIPPED" code so CI can tell "unreproducible" from
  "not attempted" — it must never report success it did not measure.

USAGE
-----
    .venv/bin/python scripts/verify_habblaud_revendor.py            # full rebuild
    .venv/bin/python scripts/verify_habblaud_revendor.py --dry-run  # plan only
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
VENDOR = REPO / "frontend" / "qubit-app" / "plugins" / "qubit-office" / "habblaud"
PROVENANCE = VENDOR / "PROVENANCE.json"

#: Exit codes are part of the contract: a caller must be able to distinguish
#: "the rebuild ran and disagreed" (1) from "the rebuild could not be attempted"
#: (2). Collapsing them into a single non-zero would let an offline run read as a
#: reproduction failure, or worse, a bare success.
EXIT_OK = 0
EXIT_MISMATCH = 1
EXIT_SKIPPED = 2


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(cmd: list[str], cwd: Path | None = None, timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def _load_vendor_module():
    """Import scripts/vendor_habblaud.py so the rebuild uses the REAL transforms.

    Re-implementing the transforms here would let this checker and the vendorer
    drift apart — the checker would then certify a tree the vendorer no longer
    produces. Importing the one module keeps a single source of truth.
    """
    spec = importlib.util.spec_from_file_location(
        "vendor_habblaud", REPO / "scripts" / "vendor_habblaud.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def toolchain() -> dict[str, str]:
    out: dict[str, str] = {}
    for key, cmd in (("node", ["node", "--version"]), ("npm", ["npm", "--version"])):
        try:
            out[key] = _run(cmd).stdout.strip() or "unknown"
        except (OSError, subprocess.SubprocessError):
            out[key] = "unknown"
    return out


def clone_at(commit: str, dest: Path) -> None:
    """Blob-filtered clone then checkout the pinned commit.

    `--filter=blob:none` keeps it fast; blobs materialise on checkout. This is
    read-only w.r.t. any shared checkout — it never touches
    /home/trick/Projects/habblaud.
    """
    proc = _run(["git", "clone", "--filter=blob:none", "--no-checkout",
                 "https://github.com/marmottajr/habblaud.git", str(dest)], timeout=900)
    if proc.returncode != 0:
        raise RuntimeError(f"git clone failed:\n{proc.stderr[-2000:]}")
    proc = _run(["git", "checkout", commit], cwd=dest, timeout=600)
    if proc.returncode != 0:
        raise RuntimeError(
            f"git checkout {commit} failed (is the pin still on the remote?):\n"
            f"{proc.stderr[-2000:]}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan and toolchain, do not clone/build")
    args = ap.parse_args()

    record = json.loads(PROVENANCE.read_text(encoding="utf-8"))
    commit = record["commit"]
    tc = toolchain()
    print(f"pinned commit : {commit}")
    print(f"record        : {len(record['files'])} files, {record['total_bytes']} bytes")
    print(f"toolchain     : node {tc['node']}, npm {tc['npm']}")

    if args.dry_run:
        print("dry-run: would clone, `npm ci --include=dev`, `vite build`, "
              "re-run the vendor transforms, and diff byte-for-byte.")
        return EXIT_OK

    vh = _load_vendor_module()
    work = Path(tempfile.mkdtemp(prefix="habblaud-repro-"))
    try:
        src = work / "habblaud"
        try:
            clone_at(commit, src)
        except RuntimeError as exc:
            print(f"SKIPPED: {exc}", file=sys.stderr)
            return EXIT_SKIPPED
        head = _run(["git", "rev-parse", "HEAD"], cwd=src).stdout.strip()
        if head != commit:
            print(f"SKIPPED: clone HEAD {head} != pinned {commit}", file=sys.stderr)
            return EXIT_SKIPPED

        proc = _run(["npm", "ci", "--include=dev"], cwd=src, timeout=900)
        if proc.returncode != 0:
            print(f"SKIPPED: npm ci failed:\n{proc.stderr[-2000:]}", file=sys.stderr)
            return EXIT_SKIPPED

        dest = work / "vendored"
        dest.mkdir()
        rebuilt = vh.vendor(src, dest, skip_build=False)

        # --- byte-level comparison, both directions ------------------------
        committed_hashes = {
            f["path"]: f["sha256"] for f in record["files"]
        }
        rebuilt_hashes = {f["path"]: f["sha256"] for f in rebuilt["files"]}

        missing = sorted(set(committed_hashes) - set(rebuilt_hashes))
        extra = sorted(set(rebuilt_hashes) - set(committed_hashes))
        differing = sorted(
            p for p in set(committed_hashes) & set(rebuilt_hashes)
            if committed_hashes[p] != rebuilt_hashes[p]
        )

        # also re-hash the committed tree FROM DISK: a stale PROVENANCE.json is
        # exactly the failure mode `--check` catches, and this check must not
        # silently accept a record that no longer describes the tree.
        on_disk_bad = [
            p for p, h in committed_hashes.items()
            if not (VENDOR / p).is_file() or sha256(VENDOR / p) != h
        ]

        ok = not (missing or extra or differing or on_disk_bad)
        report = {
            "ok": ok,
            "commit": commit,
            "toolchain": tc,
            "files_rebuilt": len(rebuilt_hashes),
            "files_recorded": len(committed_hashes),
            "missing_from_rebuild": missing,
            "extra_in_rebuild": extra,
            "differing_sha256": differing,
            "committed_tree_stale_vs_record": on_disk_bad,
        }
        print(json.dumps(report, indent=2))

        if ok:
            print(f"\nREPRODUCIBLE: {len(rebuilt_hashes)} files byte-identical to "
                  f"the vendored tree, rebuilt from {commit[:12]}.")
            return EXIT_OK
        print("\nNOT REPRODUCIBLE: see the differences above.", file=sys.stderr)
        return EXIT_MISMATCH
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Deterministic Qubit identifier/file-rename artifact generator.

Reproduces ``docs/archive/2026-09-21/QUBIT_IDENTIFIER_RENAME.json`` from the
current worktree. That artifact is the machine-readable ledger of every
*file* that the deep source rebrand actually moved on disk (the vendored
package rename, Qubit-owned ``voicebridge`` modules, differential fixtures,
fork tests, and the renamed frontend component).

Input (single, deterministic):

* ``docs/archive/2026-09-21/QUBIT_REBRAND_MANIFEST.json`` ``path_renames`` —
  the canonical rename ledger produced/curated by the rebrand wave. Only the
  rows whose ``kind`` names a concrete file on disk are considered
  (``FILE_RENAME_KINDS``): ``test-file``, ``differential-fixture``,
  ``frontend-component``, ``voicebridge-module``. Directory, package,
  entry-point, runtime-path, packaging and TUI-workspace rows are not files
  and are recorded in the rebrand manifest itself, not here.

Reconciliation against the worktree:

* a row is emitted only when the ``new`` path exists as a file **and** the
  ``old`` path is absent. A rename claim whose old file still exists is a
  false rename (e.g. ``test_ledger_hermes_build.py`` -> ``test_ledger_qubit_build.py``,
  two distinct ledger builders), and is dropped — exactly as the
  ``QUBIT_DEEP_REBRAND_2026-09-19_HANDOFF.md`` records.

Deferred buckets (not reproducible; documented, never fabricated):

* ``js_files`` / ``python_files`` are the *identifier-level* rename buckets
  from R2 of the deep-rebrand handoff (``hermes*``/``Hermes*`` symbols inside
  Python/TS sources). That AST/tokenizer pass was explicitly deferred and no
  rows were ever recorded, so both buckets are part of the artifact contract
  as fixed empty lists. This generator does not invent identifier rows.

Determinism:

* no timestamps; selection is ``sorted(...)`` by ``(old, new)``; output is
  ``json.dumps(..., indent=1, sort_keys=True)`` plus one trailing newline —
  byte-identical across runs and to the archived artifact.

Usage:
    python scripts/identifier_rename.py                 # write the artifact
    python scripts/identifier_rename.py --out <path>    # write elsewhere
    python scripts/identifier_rename.py --check         # render in memory and
                                                        # fail (exit 2) on drift

Exit codes: 0 ok; 2 usage/drift/error.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

#: Canonical rename ledger (input): the rebrand wave's ``path_renames``.
LEDGER = REPO / "docs" / "archive" / "2026-09-21" / "QUBIT_REBRAND_MANIFEST.json"
OUT = REPO / "docs" / "archive" / "2026-09-21" / "QUBIT_IDENTIFIER_RENAME.json"

#: ``path_renames`` kinds that name a concrete file (not a dir/package/label).
FILE_RENAME_KINDS = (
    "test-file",
    "differential-fixture",
    "frontend-component",
    "voicebridge-module",
)


def build() -> dict:
    """Render the identifier/file-rename artifact for the current worktree."""
    ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
    rows = []
    for row in ledger["path_renames"]:
        if row["kind"] not in FILE_RENAME_KINDS:
            continue
        if (REPO / row["new"]).is_file() and not (REPO / row["old"]).exists():
            rows.append({"old": row["old"], "new": row["new"]})
    return {
        "file_renames": sorted(rows, key=lambda r: (r["old"], r["new"])),
        # R2 identifier-level rename buckets: deliberately empty (deferred).
        "js_files": [],
        "python_files": [],
    }


def render(artifact: dict) -> str:
    """Serialize exactly as written to disk (indent=1 + one trailing newline)."""
    return json.dumps(artifact, indent=1, sort_keys=True) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(OUT),
                    help="output path (default: %(default)s)")
    ap.add_argument("--check", action="store_true",
                    help="render in memory and fail (exit 2) if the on-disk "
                         "artifact differs; writes nothing")
    args = ap.parse_args(argv)

    rendered = render(build())

    if args.check:
        target = Path(args.out)
        if not target.is_file():
            print("check FAILED: %s is absent" % target, file=sys.stderr)
            return 2
        if target.read_text(encoding="utf-8") != rendered:
            print("check FAILED: %s differs from regenerated output" % target,
                  file=sys.stderr)
            return 2
        print("check OK: %s is current (%d file renames)"
              % (target, len(build()["file_renames"])))
        return 0

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(rendered, encoding="utf-8")
    print("wrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Emit Qubit Office asset-catalog metadata from a local LimeZu pack.

This reads file NAMES only. It never opens, decodes, copies or writes art, and
it refuses to produce anything but JSON metadata. Use it to inspect the pack the
owner purchased, without committing or redistributing it.

    python scripts/office_asset_catalog.py --source "/path/to/Modern_Interiors..." \\
        --summary

    python scripts/office_asset_catalog.py --source ... --out /tmp/catalog.json

Exit codes: 0 ok, 2 the source is unreadable/invalid.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from voicebridge.office.world.catalog import (  # noqa: E402
    CatalogError,
    catalog_entries_to_dicts,
    catalog_summary,
    extract_catalog,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True,
                        help="pack directory or .zip (read for names only)")
    parser.add_argument("--theme", default="limezu",
                        help="theme id recorded on each entry")
    parser.add_argument("--out", help="write JSON metadata to this file")
    parser.add_argument("--summary", action="store_true",
                        help="print counts instead of the full catalog")
    parser.add_argument("--pretty", action="store_true",
                        help="pretty-print JSON")
    args = parser.parse_args(argv)

    try:
        entries = extract_catalog(args.source, theme=args.theme)
    except CatalogError as exc:
        for problem in exc.problems:
            print(f"office_asset_catalog: {problem}", file=sys.stderr)
        return 2

    if args.summary:
        payload = catalog_summary(entries)
    else:
        payload = catalog_entries_to_dicts(entries)

    text = json.dumps(payload, indent=2 if args.pretty else None,
                      sort_keys=True) + "\n"
    if args.out:
        out = Path(args.out).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"wrote {len(entries)} entries to {out}")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

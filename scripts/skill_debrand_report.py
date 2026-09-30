#!/usr/bin/env python3
"""Deterministic skill de-brand scanner (source-level, read-only).

Walks the three Qubit skill roots and classifies every occurrence of a legacy
brand token with the *same* transform the W-BRAND skills migration applies
(``agent.skill_utils.debrand_skill_text``), so the report reflects exactly what
:func:`agent.skill_utils.debrand_skill_tree` would rewrite and what it would
intentionally preserve.

Roots:
  * repo built-in skills (``qubit_runtime/skills``)
  * repo optional skills (``qubit_runtime/optional-skills``)
  * the live QUBIT_HOME skills dir (``runtime/skills``)

Every occurrence is bucketed:

  * ``rewritten``  — the prose transform removes it (branding).
  * ``preserved``  — a load-bearing identifier the transform leaves verbatim
    (wire/provenance/legal/path/identifier). These are reported with a sample
    so an operator can certify that no *rendered* surface is left branded.

This is a scan, not a gate: it never writes to the skills tree. ``--counts``
prints before/after legacy-file counts for the roots.

Deterministic: no timestamps; all paths sorted; JSON output is
``json.dump(..., indent=2, sort_keys=True)`` + one trailing newline.

Usage:
    python scripts/skill_debrand_report.py                 # summary to stdout
    python scripts/skill_debrand_report.py --json OUT      # write the report
    python scripts/skill_debrand_report.py --counts        # before/after files

Exit codes: 0 ok; 2 usage/error.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "qubit_runtime"))

from agent.skill_utils import (  # noqa: E402
    _DEBRAND_DOC_SUFFIXES,
    debrand_skill_text,
    iter_skill_text_files,
)

SKILL_ROOTS = (
    REPO / "qubit_runtime" / "skills",
    REPO / "qubit_runtime" / "optional-skills",
    Path(os.path.expanduser("~/.local/state/qubit-voicebridge/runtime/skills")),
)

_BRAND = re.compile(
    r"[A-Za-z0-9_./@~<>:-]*(?:hermes|Hermes|HERMES|nous|Nous|NOUS"
    r"|openclaw|OpenClaw|OPENCLAW)[A-Za-z0-9_./@~<>:-]*")
_LEGAL_LINE = re.compile(r"(?:Copyright|©|\(c\)\s*\d{4})[^\n]*")
#: Suffixes whose prose is a shipped/user-facing surface; other suffixes are
#: code/state and count only toward the file tally.
_PROSE_SUFFIXES = _DEBRAND_DOC_SUFFIXES


def _tok_class(tok: str) -> str:
    low = tok.lower()
    if "nousresearch" in low:
        return "provenance_org"
    if "/" in tok or tok.startswith((".", "~")) or tok.endswith((".py", ".md")):
        return "path_or_identifier"
    if "_" in tok or "(".join(tok.split("(")[:1]) == tok:
        return "identifier"
    return "prose"


def scan_root(root: Path) -> Dict[str, Any]:
    files = changed_files = 0
    rewritten = preserved = 0
    preserved_samples: List[str] = []
    rewritten_samples: List[str] = []
    if not root.is_dir():
        return {"root": str(root), "present": False}
    for p in sorted(iter_skill_text_files(root)):
        files += 1
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        is_doc = p.suffix.lower() in _PROSE_SUFFIXES
        new = debrand_skill_text(text, is_doc=is_doc)
        if new != text:
            changed_files += 1
        rel = p.relative_to(root).as_posix()
        new_lines = new.splitlines()
        old_lines = text.splitlines()
        for lineno, line in enumerate(old_lines, 1):
            if _LEGAL_LINE.search(line):
                continue
            new_line = new_lines[lineno - 1] if lineno <= len(new_lines) else ""
            for m in _BRAND.finditer(line):
                tok = m.group(0)
                # A token is rewritten when the transform removed it from this
                # line (prose word replaced / docs host collapsed); it is
                # preserved when it survives verbatim.
                if tok not in new_line:
                    rewritten += 1
                    if len(rewritten_samples) < 12:
                        rewritten_samples.append(f"{rel}:{lineno} {tok}")
                else:
                    preserved += 1
                    if len(preserved_samples) < 12:
                        preserved_samples.append(f"{rel}:{lineno} {tok}")
    return {
        "root": str(root), "present": True, "files": files,
        "files_changed": changed_files, "tokens_rewritten": rewritten,
        "tokens_preserved": preserved,
        "rewritten_samples": rewritten_samples[:12],
        "preserved_samples": preserved_samples[:12],
    }


def legacy_file_counts(root: Path) -> Dict[str, Any]:
    total = legacy = 0
    if not root.is_dir():
        return {"root": str(root), "present": False}
    for p in sorted(iter_skill_text_files(root)):
        total += 1
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if _BRAND.search(text):
            legacy += 1
    return {"root": str(root), "present": True,
            "files": total, "legacy_files": legacy}


def build(report_counts: bool = False) -> Dict[str, Any]:
    roots = []
    for r in SKILL_ROOTS:
        entry = scan_root(r)
        if report_counts and entry.get("present"):
            entry["counts"] = legacy_file_counts(r)
        roots.append(entry)
    return {
        "schema": "qubit-skill-debrand-report/1",
        "roots": roots,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", type=Path, default=None)
    ap.add_argument("--counts", action="store_true",
                    help="include before/after legacy-file counts per root")
    args = ap.parse_args(argv)
    report = build(report_counts=args.counts)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        print(f"wrote {args.json}")
    for e in report["roots"]:
        if not e.get("present"):
            print(f"ABSENT  {e['root']}")
            continue
        c = e.get("counts", {})
        print(f"ROOT {e['root']}")
        print(f"  files={e['files']} files_changed={e['files_changed']} "
              f"tokens_rewritten={e['tokens_rewritten']} "
              f"tokens_preserved={e['tokens_preserved']}")
        if c:
            print(f"  legacy_files={c['legacy_files']}/{c['files']}")
        for s in e["rewritten_samples"][:5]:
            print(f"  rewrite: {s}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

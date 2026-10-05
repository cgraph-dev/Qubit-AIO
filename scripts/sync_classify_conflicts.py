#!/usr/bin/env python3
"""Classify the slice's 90 conflicts: genuine vs translation-artifact.

A conflict is SPURIOUS when Qubit's file differs from the translated upstream
base only by identifier renames (i.e. the local change IS the rename itself),
while upstream changed an unrelated region. Those can be re-merged with a
whitespace/identifier-insensitive pass. A GENUINE conflict is overlapping real
edits needing a human.

No writes: re-runs the merge per file and characterises the divergence.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "scripts")
import sync_upstream as S  # noqa: E402

IDENT_RE = re.compile(r"\b(?:HERMES_|QUBIT_|hermes_|qubit_|Hermes|Qubit)")


def normalize(text: str) -> str:
    """Collapse every brand identifier to a single token so a pure rename
    difference disappears — what remains is real divergence."""
    text = re.sub(r"\b(?:HERMES_|QUBIT_)([A-Z0-9_]+)", r"X_\1", text)
    text = re.sub(r"\b(?:hermes_|qubit_)([a-z0-9_]+)", r"x_\1", text)
    text = re.sub(r"\b(?:Hermes|Qubit)(?=[A-Z_])", "X", text)
    return text


def main() -> None:
    results = json.load(open("/tmp/slice_cli.json"))
    conflicts = [r["upstream"] for r in results if r["status"] == "CONFLICT"]
    print(f"classifying {len(conflicts)} conflicts\n")

    genuine, spurious = [], []
    for up in conflicts:
        qpath = S.route(up)
        cur = (S.QR / qpath).read_text(encoding="utf-8", errors="replace")
        base = S.git_show(S.PIN_SHA, up)
        new = S.git_show(S.BASE_SHA, up)
        if base is None or new is None:
            continue
        t_base, _ = S.translate(base)
        t_new, _ = S.translate(new)

        # Is the local file just the translated base modulo renames?
        same_modulo_rename = normalize(cur) == normalize(t_base)

        # Retry the merge with both sides normalized: if it becomes clean, the
        # only bar was the rename itself.
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "cur").write_text(normalize(cur))
            (d / "base").write_text(normalize(t_base))
            (d / "new").write_text(normalize(t_new))
            r = subprocess.run(
                ["git", "merge-file", "-p", str(d / "cur"), str(d / "base"), str(d / "new")],
                capture_output=True, text=True, check=False)

        if r.returncode == 0:
            spurious.append(up)
        else:
            genuine.append(up)

    print(f"SPURIOUS (rename-only, auto-mergeable): {len(spurious)}")
    for p in spurious[:15]:
        print(f"  {p}")
    print(f"\nGENUINE (overlapping real edits, need review): {len(genuine)}")
    for p in genuine[:15]:
        print(f"  {p}")

    Path("/tmp/slice_conflicts.json").write_text(json.dumps(
        {"spurious": spurious, "genuine": genuine}, indent=2))
    print("\n-> /tmp/slice_conflicts.json")


if __name__ == "__main__":
    main()

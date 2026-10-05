#!/usr/bin/env python3
"""Dry-run ONE upstream file through the full sync pipeline. No writes.

Proves the translation + 3-way merge works before it is scaled to 2,892 files.
Picks a file that is (a) modified upstream in the 4,128 commits, (b) renamed in
Qubit, and (c) has a Qubit counterpart — the hardest case.

Pipeline per file:
  upstream base @ pin   +   upstream new @ bd0affe5   +   qubit current
        -> translate upstream paths/identifiers -> qubit vocabulary
        -> git merge-file (3-way) -> merged result
        -> report: clean / conflicts / how many identifiers translated
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

PIN = Path("/home/trick/.cache/qubit-pins/hermes-agent")
REPO = Path("/home/trick/Projects/Repos/qubit-voicebridge")
QR = REPO / "qubit_runtime"
PIN_SHA = "69948c0057"
BASE_SHA = "bd0affe5"

# Derived from scripts/sync_derive_rename_map.py (40 prefix rules); the ones that
# actually matter for source files.
PREFIX_RULES = {
    "hermes_cli": "qubit_cli",
    "hermes_platform": "qubit_platform",
    "hermes_state.py": "qubit_state.py",
    "hermes_constants.py": "qubit_constants.py",
    "hermes_logging.py": "qubit_logging.py",
    "hermes_bootstrap.py": "qubit_bootstrap.py",
    "hermes_time.py": "qubit_time.py",
    "hermes_yaml.py": "qubit_yaml.py",
    "hermes_startup_watchdog.py": "qubit_startup_watchdog.py",
    "hermes_constants_scratch.py": "qubit_constants_scratch.py",
}
for _s in ("common", "compression", "dbfile", "errors", "fts", "gateway", "guard",
           "health", "holders", "ids", "lockguard", "lockowners", "maintenance",
           "messages", "portability", "profile_repair", "readpool", "registry",
           "repair", "rewind", "schema", "search", "sessions", "telegram",
           "timeline", "titles", "usage", "user_copy", "wal"):
    PREFIX_RULES[f"hermes_state_{_s}.py"] = f"qubit_state_{_s}.py"


def route(path: str) -> str:
    for up in sorted(PREFIX_RULES, key=len, reverse=True):
        if path == up:
            return PREFIX_RULES[up]
        if path.startswith(up + "/"):
            return PREFIX_RULES[up] + path[len(up):]
    return path


# Identifier translation. ORDER MATTERS: longest/most specific first.
IDENT_RULES = [
    (re.compile(r"\bHERMES_"), "QUBIT_"),
    (re.compile(r"\bhermes_"), "qubit_"),
    (re.compile(r"\bHermes(?=[A-Z_])"), "Qubit"),
    (re.compile(r"\bhermes-agent\b"), "qubit"),
    (re.compile(r"\bhermes\b"), "qubit"),
    (re.compile(r"\.hermes\b"), ".qubit"),
]


def translate(text: str) -> tuple[str, int]:
    n = 0
    for rx, repl in IDENT_RULES:
        text, k = rx.subn(repl, text)
        n += k
    return text, n


def git_show(sha: str, path: str) -> str | None:
    r = subprocess.run(["git", "-C", str(PIN), "show", f"{sha}:{path}"],
                       capture_output=True, text=True, check=False)
    return r.stdout if r.returncode == 0 else None


def main() -> None:
    # The hardest case: modified upstream, renamed in Qubit, counterpart exists.
    cands = subprocess.run(
        ["git", "-C", str(PIN), "diff", "--name-only", f"{PIN_SHA}..{BASE_SHA}", "hermes_cli"],
        capture_output=True, text=True, check=False).stdout.splitlines()
    cands = [c for c in cands if c.endswith(".py")]

    picked = None
    for c in cands:
        qp = route(c)
        if (QR / qp).exists():
            picked = c
            break
    if not picked:
        print("no candidate found")
        sys.exit(1)

    qpath = route(picked)
    print(f"upstream file : {picked}")
    print(f"qubit path    : {qpath}")
    print()

    base = git_show(PIN_SHA, picked)
    new = git_show(BASE_SHA, picked)
    cur = (QR / qpath).read_text(encoding="utf-8", errors="replace")
    if base is None or new is None:
        print("could not read upstream blob")
        sys.exit(1)

    t_base, nb = translate(base)
    t_new, nn = translate(new)
    print(f"identifiers translated: base={nb} new={nn}")

    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "cur").write_text(cur)
        (d / "base").write_text(t_base)
        (d / "new").write_text(t_new)
        r = subprocess.run(["git", "merge-file", "-p", "--diff3",
                            str(d / "cur"), str(d / "base"), str(d / "new")],
                           capture_output=True, text=True, check=False)
        merged, rc = r.stdout, r.returncode
        conflicts = merged.count("<<<<<<<")
        print(f"merge rc      : {rc}  ({'CLEAN' if rc == 0 else f'{conflicts} conflict(s)'})")
        print(f"merged bytes  : {len(merged)} (qubit current was {len(cur)})")
        if conflicts:
            print("\n--- first conflict ---")
            i = merged.find("<<<<<<<")
            print(merged[i:i + 600])


if __name__ == "__main__":
    main()

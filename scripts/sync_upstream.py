#!/usr/bin/env python3
"""Apply upstream's 4,128 commits (pin 69948c0057 -> bd0affe5) onto Qubit.

Files, not git: qubit_runtime/ is a plain directory in Qubit's repo with no .git,
so upstream's history cannot be merged. Instead each touched upstream file is
routed through Qubit's rename map and applied as a 3-WAY MERGE, so Qubit's own
divergence survives and upstream's changes land.

Per file:
    upstream base @ PIN  +  upstream new @ BASE  +  qubit current
        -> translate identifiers (HERMES_->QUBIT_, hermes_->qubit_, ...)
        -> git merge-file -> merged
        -> write, or report conflict

Modes
  --plan      classify every touched path (no writes)   [default]
  --apply     perform the merge, writing into qubit_runtime/
  --subtree X restrict to one upstream prefix (e.g. hermes_cli)

Safety: --apply refuses to run on a dirty git tree, and every written file is
recorded so the run can be reverted with `git checkout`.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

PIN = Path("/home/trick/.cache/qubit-pins/hermes-agent")
# Derive the repo root from this script's location so the tool works correctly
# inside a `git worktree` checkout. A hardcoded absolute path made every
# worktree agent write into the MAIN tree, destroying the isolation that
# worktrees exist to provide.
REPO = Path(__file__).resolve().parent.parent
QR = REPO / "qubit_runtime"
PIN_SHA = "69948c0057"
BASE_SHA = "bd0affe5"

# --- rename map (derived empirically by scripts/sync_derive_rename_map.py) ----
PREFIX_RULES: dict[str, str] = {
    "hermes_cli": "qubit_cli",
    "hermes_platform": "qubit_platform",
}
_STATE_SIBLINGS = (
    "common", "compression", "dbfile", "errors", "fts", "gateway", "guard",
    "health", "holders", "ids", "lockguard", "lockowners", "maintenance",
    "messages", "portability", "profile_repair", "readpool", "registry",
    "repair", "rewind", "schema", "search", "sessions", "telegram",
    "timeline", "titles", "usage", "user_copy", "wal",
)
for _s in _STATE_SIBLINGS:
    PREFIX_RULES[f"hermes_state_{_s}.py"] = f"qubit_state_{_s}.py"
for _flat in ("state.py", "constants.py", "logging.py", "bootstrap.py", "time.py",
              "yaml.py", "startup_watchdog.py", "constants_scratch.py"):
    PREFIX_RULES[f"hermes_{_flat}"] = f"qubit_{_flat}"

IDENT_RULES: list[tuple[re.Pattern[str], str]] = [
    # Order matters: most specific first. NOTE the identifier rules must NOT use
    # \b before `hermes_`: it appears as a SUBSTRING inside compound names
    # (get_hermes_home, is_hermes_config, _hermes_home_key), where \b after the
    # preceding word char does not fire and the name silently survives the
    # translation. Match the literal token anywhere, then guard the tail.
    (re.compile(r"HERMES_"), "QUBIT_"),
    (re.compile(r"hermes_"), "qubit_"),
    (re.compile(r"Hermes(?=[A-Z_])"), "Qubit"),
    (re.compile(r"\bhermes-agent\b"), "qubit"),
    (re.compile(r"\.hermes\b"), ".qubit"),
    (re.compile(r"\bhermes\b"), "qubit"),
]

# Paths deliberately NOT synced (Qubit-owned or upstream-only scaffolding).
SKIP_PREFIXES = (
    ".github/", "contributors/", "website/", "plugin-catalog/",
    "node_modules/", ".git/",
)


def route(path: str) -> str:
    """Map an upstream path to its Qubit path.

    Handles both prefix rules (hermes_cli/ -> qubit_cli/) and bare filenames
    carrying the identifier (hermes.shared_metrics.v3.schema.json ->
    qubit.shared_metrics.v3.schema.json), since the rename was applied to file
    NAMES as well as directories.
    """
    for up in sorted(PREFIX_RULES, key=len, reverse=True):
        if path == up:
            return PREFIX_RULES[up]
        if path.startswith(up + "/"):
            return PREFIX_RULES[up] + path[len(up):]
    # bare filename / nested component identifier rename
    head, sep, tail = path.rpartition("/")
    if tail.startswith("hermes"):
        tail = "qubit" + tail[len("hermes"):]
        return f"{head}{sep}{tail}"
    return path


def translate(text: str) -> tuple[str, int]:
    n = 0
    for rx, repl in IDENT_RULES:
        text, k = rx.subn(repl, text)
        n += k
    return text, n


def skip(path: str) -> bool:
    return any(path.startswith(p) for p in SKIP_PREFIXES)


def git_out(*args: str) -> str:
    return subprocess.run(["git", "-C", str(PIN), *args],
                          capture_output=True, text=True, check=False).stdout


def git_show(sha: str, path: str) -> str | None:
    r = subprocess.run(["git", "-C", str(PIN), "show", f"{sha}:{path}"],
                       capture_output=True, text=True, check=False)
    return r.stdout if r.returncode == 0 else None


@dataclass
class Plan:
    modified: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.modified) + len(self.added) + len(self.deleted)


def build_plan(subtree: str | None) -> Plan:
    args = ["diff", "--name-status", f"{PIN_SHA}..{BASE_SHA}"]
    if subtree:
        args.append(subtree)
    plan = Plan()
    for line in git_out(*args).splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        st, path = parts[0][0], parts[1]
        if skip(path):
            plan.skipped.append(path)
            continue
        if st == "M":
            plan.modified.append(path)
        elif st == "A":
            plan.added.append(path)
        elif st == "D":
            plan.deleted.append(path)
    return plan


def merge_one(up_path: str) -> tuple[str, str, int]:
    """Return (status, detail, translated_count)."""
    qpath = route(up_path)
    target = QR / qpath
    base = git_show(PIN_SHA, up_path)
    new = git_show(BASE_SHA, up_path)
    if new is None:
        return "SKIP", "upstream blob unreadable", 0

    t_base, n1 = translate(base if base is not None else "")
    t_new, n2 = translate(new)
    translated = n1 + n2

    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(t_new, encoding="utf-8")
        return "ADDED", f"{qpath} (new, {translated} idents)", translated

    cur = target.read_text(encoding="utf-8", errors="replace")
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "cur").write_text(cur)
        (d / "base").write_text(t_base)
        (d / "new").write_text(t_new)
        r = subprocess.run(
            ["git", "merge-file", "-p", "--diff3",
             str(d / "cur"), str(d / "base"), str(d / "new")],
            capture_output=True, text=True, check=False)
    merged = r.stdout
    if r.returncode == 0:
        target.write_text(merged, encoding="utf-8")
        return "MERGED", f"{qpath} ({translated} idents)", translated
    # conflict -> leave the file untouched, report for review
    return "CONFLICT", f"{qpath} ({merged.count('<<<<<<<')} hunks)", translated


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--subtree", default=None)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args()

    plan = build_plan(args.subtree)
    print(f"plan: {len(plan.modified)} M, {len(plan.added)} A, "
          f"{len(plan.deleted)} D, {len(plan.skipped)} skipped  (total {plan.total})")

    if not args.apply:
        for p in plan.modified[:10]:
            print(f"  M {p}  ->  {route(p)}")
        for p in plan.added[:10]:
            print(f"  A {p}  ->  {route(p)}")
        print("(dry run; pass --apply to merge)")
        return

    # safety: refuse on a dirty tree
    dirty = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain"],
                           capture_output=True, text=True, check=False).stdout.strip()
    if dirty:
        print(f"REFUSING: worktree dirty ({len(dirty.splitlines())} entries). "
              "Commit or stash first so this run is revertable.")
        sys.exit(2)

    results: list[dict[str, str | int]] = []
    todo = plan.modified + plan.added
    if args.limit:
        todo = todo[:args.limit]
    for i, up in enumerate(todo, 1):
        status, detail, n = merge_one(up)
        results.append({"upstream": up, "status": status, "detail": detail, "idents": n})
        if status == "CONFLICT":
            print(f"  [{i}/{len(todo)}] CONFLICT {detail}")
        elif i % 50 == 0:
            print(f"  [{i}/{len(todo)}] ...")

    tally: dict[str, int] = {}
    for r in results:
        tally[str(r["status"])] = tally.get(str(r["status"]), 0) + 1
    print("\n=== result ===")
    for k, v in sorted(tally.items(), key=lambda kv: -kv[1]):
        print(f"  {k:10} {v}")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(results, indent=2))
        print(f"  detail -> {args.json_out}")

    conflicts = [r["upstream"] for r in results if r["status"] == "CONFLICT"]
    if conflicts:
        print(f"\n{len(conflicts)} conflicts need review:")
        for c in conflicts[:20]:
            print(f"  {c}")


if __name__ == "__main__":
    main()

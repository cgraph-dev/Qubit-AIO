#!/usr/bin/env python3
"""Per-file staleness audit: is what we merged the CURRENT upstream revision?

WHY THIS EXISTS
---------------
The campaign reported "95% present" from a file-PRESENCE test: does a path exist in
our tree for each upstream path? Existence is not currency. A file can be present and
still be an OLD revision of a file upstream has since reworked.

The evidence that motivated this: `qubit_cli/update_lock.py` exists in our tree with
**10** top-level defs, while upstream at the same revision it was ported from had grown
to **70**. Four sibling modules in the same feature (`update_pause_record.py`,
`update_cmd_commit.py`, `update_custody.py`, `anon_challenge.py`) are missing outright.
So "present" hid a file that stopped at a much earlier revision.

This script measures, per upstream file:
  * EXISTS   -- is the routed path in our tree at all?
  * STALE    -- present, but upstream changed it AFTER the revision we have
  * CURRENT  -- present and matches the upstream revision we synced from

and reports staleness in SYMBOL terms, not bytes: how many top-level defs upstream has
that we do not. A byte diff is dominated by rebranding churn (hermes_*/qubit_*), which
is why this counts symbols.

SCOPE: upstream `a036b13793` (our baseline) is the "we synced from here" reference.
A file whose upstream content changed between the OLD pin and `a036b13793` but which
we ported from an even earlier revision is a SEPARATE problem (a short-port). This
script flags both: `short_port` (we trail our own baseline) and `behind_head` (we
trail current upstream).

THE REBRAND SUBTRACTION (both branches)
---------------------------------------
This audit compares SYMBOL NAMES. Upstream's symbols are Hermes-named; ours are
Qubit-named for the same role. A naive name compare therefore re-reports every
rebranded symbol as "missing". Measured damage, twice:

  * the `short_port` branch was the worst offender -- it did NOT apply the rename
    subtraction at all, so ALL 32 flagged production symbols across 20 files came
    back as missing. Every one was a false positive (28 by rebrand spelling, 4 by
    documented semantic rename / supersession / move). ZERO genuine.
  * the top-45 `behind_head` sample listed 396 symbols; 145 (37%) were rebrand
    ghosts -- we already had `_qubit_holder_subcommand` for `_hermes_holder_subcommand`,
    `_get_real_qubit_home` for `_get_real_hermes_home`, `QUBIT_HERO` for
    `HERMES_CADUCEUS`, `QubitTokenStorage` for `HermesTokenStorage`, etc.

So the audit's headline numbers overstated the gap by roughly a third. This version
applies the SAME rename subtraction to BOTH branches, using the rename engine that
actually did the rebrand (`scripts/qubit_rebrand.py` -- MODULE_TOKENS, CLASS_TOKENS,
IDENTIFIER_TOKENS, ENV_TOKENS) rather than a hand-rolled prefix guess. It counts a
symbol PRESENT when:

  1. its own name is a top-level def in our routed file, OR
  2. its hermes->qubit twin (from the engine's token map, longest-match first) is a
     top-level def in our routed file, OR
  3. its twin (or its own name) is a top-level def ANYWHERE in our tree -- this
     catches symbols that MOVED to a sibling module during the sync, OR
  4. it appears in SEMANTIC_RENAMES -- a small, evidence-backed table of renames the
     mechanical token map cannot express (`HERMES_CADUCEUS`->`QUBIT_HERO`,
     `_SCRIPT_REL`->`_CANONICAL_SCRIPT_REL`/`_LEGACY_SCRIPT_REL`, ...). Every entry
     cites where it was proven.

FALSE NEGATIVES ARE WORSE THAN FALSE POSITIVES
----------------------------------------------
Subtracting a symbol that is genuinely missing HIDES real work. So a twin is only
subtracted when it is PROVEN to resolve as an AST definition (in our file or
tree-wide) -- never on a bare string match, which would credit a name that appears
only in a comment. When the rule is unsure, the symbol stays counted MISSING. Cases
that resolve by neither the twin map nor SEMANTIC_RENAMES are reported explicitly so
a human can judge them, rather than silently dropped.

`preserved_wire_identifiers` (the frozen manifest's 14 wire tokens -- `hermes_flow`,
`X-Hermes-Session-Token`, `hermes-kanban`, ...) are deliberately NOT renamed and are
never credited by the twin pass.

Usage:
    python3 scripts/audit_sync_completeness.py [--json out.json] [--top 40]
    python3 scripts/audit_sync_completeness.py --rev ad12263a5ed4 --show-symbols
"""
from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Refuse to run from outside the repo. `__file__` is the only correct anchor, but a
# copy of this script placed in /tmp resolves REPO to /tmp and then reports EVERY
# upstream file as absent (8,161 absent / 0 present) -- a plausible-looking result
# that is pure artifact. That happened once. Fail loudly instead.
if not (REPO / "qubit_runtime").is_dir() or not (REPO / "scripts").is_dir():
    raise SystemExit(
        f"REFUSING: computed repo root {REPO} is not a Qubit checkout.\n"
        "Run this script from the repository: "
        "`.venv/bin/python scripts/audit_sync_completeness.py` "
        "(do not copy it elsewhere -- it resolves paths from __file__)."
    )
def _upstream_clone() -> Path:
    """Locate the upstream clone, tolerating the hermes home being retired.

    The clone lived at ~/.hermes/hermes-agent; the hermes home has since been moved aside as part
    of retirement, so the same tree now sits at ~/.hermes.pre-retirement/hermes-agent. Prefer the
    live location, fall back to the retired one, and fail LOUDLY rather than silently measuring an
    empty directory -- a silent miss here would make every audit grade the wrong thing.
    """
    for candidate in (
        Path.home() / "upstream" / "hermes-agent",
        Path.home() / ".hermes" / "hermes-agent",
        Path.home() / ".hermes.pre-retirement" / "hermes-agent",
    ):
        if (candidate / ".git").exists():
            return candidate
    raise SystemExit(
        "cannot locate the upstream clone (tried ~/.hermes/hermes-agent and "
        "~/.hermes.pre-retirement/hermes-agent); every audit would measure nothing"
    )


UP = _upstream_clone()


def _frozen_rev() -> str:
    """The frozen pin, read from its single declared home (one home, no copies).

    The short-port branch measures "we never even reached our own baseline" --
    where "our own baseline" is the revision we should have synced from, i.e.
    the frozen pin, NOT an arbitrary waypoint. This constant used to be a
    hardcoded ``a036b13793`` (the 2026-10-06 sync waypoint, 1783 commits behind
    the frozen pin). Measured against a stale base, every symbol upstream added
    between that waypoint and the pin was credited to us, so a file we never
    ported at the current revision was mis-classified as ``behind_head``
    instead of ``short_port`` -- the same under-reporting defect the sibling
    audits (audit_capability_gaps, audit_missing_symbols) were fixed for.
    Read it from frozen_target_progress.FROZEN_REV so every drift-measuring
    site agrees on one value.
    """
    src = Path(__file__).resolve().parent / "frozen_target_progress.py"
    if src.is_file():
        for line in src.read_text().splitlines():
            if line.startswith("FROZEN_REV"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit(
        f"cannot read the frozen pin from {src}; refusing to measure short-port "
        "against an unknown revision."
    )


BASELINE = _frozen_rev()
HEAD = "origin/main"

# upstream prefix -> our prefix
ROUTES = {
    "hermes_cli": "qubit_cli",
    "hermes_platform": "qubit_platform",
    "hermes_state": "qubit_state",
}
# Renamed DIRECTORIES (not the top-level ``hermes_<x>`` module prefixes above, and
# not the last-segment rule either). Upstream ships `plugins/hermes-achievements/`;
# we renamed the plugin dir to `qubit-achievements/`. Without this, `route()` never
# maps it and the entire plugin subtree reads as ABSENT (261 absent files, of which
# plugin_api.py alone hid 45 top-level defs behind one false "missing file").
DIR_ROUTES = {
    "hermes-achievements": "qubit-achievements",
}
RUNTIME = REPO / "qubit_runtime"


# ---------------------------------------------------------------------------
# Rebrand map -- loaded from the engine that performed the rebrand.
# ---------------------------------------------------------------------------
def _load_rebrand_tokens() -> dict[str, str]:
    """Merge the rebrand engine's exact token maps into one lookup.

    Importing the engine (rather than re-deriving) means the audit and the actual
    rebrand can never drift: if a token is added to the map, this audit picks it
    up on the next run. The module is import-safe (its only side effect is behind
    a ``__main__`` guard).
    """
    engine = REPO / "scripts" / "qubit_rebrand.py"
    if not engine.is_file():
        return {}
    spec = importlib.util.spec_from_file_location("_qubit_rebrand_engine", engine)
    if spec is None or spec.loader is None:
        return {}
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception:                                     # noqa: BLE001
        return {}
    tokens: dict[str, str] = {}
    for attr in ("MODULE_TOKENS", "CLASS_TOKENS", "IDENTIFIER_TOKENS", "ENV_TOKENS"):
        mapping = getattr(mod, attr, None)
        if isinstance(mapping, dict):
            tokens.update(mapping)
    return tokens


REBRAND_TOKENS = _load_rebrand_tokens()

# Wire tokens that are deliberately NOT renamed (frozen manifest
# `preserved_wire_identifiers`). A symbol naming one of these is never credited
# by the twin pass even if a mechanical transform would produce a twin.
PRESERVED_WIRE = {
    "__HERMES_BASE_PATH__", "__HERMES_SESSION_TOKEN__", "__HERMES_AUTH_REQUIRED__",
    "__HERMES_PLUGINS__", "__HERMES_PLUGIN_SDK__", "X-Hermes-Session-Token",
    "hermes_flow", "hermes.tokenReloadAttempted", "hermes-kanban", "hermes-flow",
    "hermes_achievements", "hermes-achievements", "HERMES_QWEN_", "qubit-hermes",
}

# Renames the mechanical token map cannot express: a different NAME for the same
# role (not a spelling transform). Each entry is evidence-backed; the comment
# names where it was proven. Keep this table TINY and cited -- it is the one place
# a mistake becomes a false negative.
SEMANTIC_RENAMES: dict[str, str] = {
    # docs/PRODUCTION_SHORT_PORT_CLOSED_2026-10-07.md: banner.py defines
    # QUBIT_HERO (:80) where upstream defined HERMES_CADUCEUS (:80); used same
    # way at the same call site (`banner_hero or <name>`).
    "HERMES_CADUCEUS": "QUBIT_HERO",
    # docs/PRODUCTION_SHORT_PORT_CLOSED_2026-10-07.md + owner decision #21
    # (2026-09-23), documented in-code at claw.py:24-33: the single _SCRIPT_REL
    # was split into _CANONICAL_SCRIPT_REL + _LEGACY_SCRIPT_REL.
    "_SCRIPT_REL": "_CANONICAL_SCRIPT_REL",
    # docs/PRODUCTION_SHORT_PORT_CLOSED_2026-10-07.md: the nous provider plugin
    # renamed its module-level INSTANCE `nous` -> `qubit` (:69, `qubit =
    # NousProfile(...)`), preserving `name="nous"` and `register_provider(qubit)`.
    # `rebrand("nous")` returns "nous" unchanged (no "hermes" substring), which is
    # why the mechanical pass cannot see it.
    "nous": "qubit",
    # docs/PRODUCTION_SHORT_PORT_CLOSED_2026-10-07.md + REBRAND_AUDIT.md: the
    # fleet marker dropped the "legacy" qualifier; ours defines the same marker
    # as `_write_fleet_restart_pending_marker` (hermes_cli/update_cmd_fleet.py:63
    # -> qubit_cli/update_cmd_fleet.py:63).
    "_write_legacy_fleet_restart_pending_marker": "_write_fleet_restart_pending_marker",
}


def rebrand(sym: str) -> str:
    """Upstream symbol name -> the name Qubit's rename gives it. Identity if none.

    Resolution order mirrors ``qubit_rebrand._rewrite_text``:
      1. exact token from the engine's map (module / class / identifier / env),
      2. documented semantic rename,
      3. longest-first prefix transform (``_hermes_`` before ``hermes_``),
      4. bare ``hermes``->``qubit`` for embedded occurrences.

    Returns ``sym`` unchanged when nothing applies, so callers can detect "no
    rename" by ``rebrand(sym) == sym``.
    """
    if sym in PRESERVED_WIRE:
        return sym
    if sym in REBRAND_TOKENS:
        return REBRAND_TOKENS[sym]
    if sym in SEMANTIC_RENAMES:
        return SEMANTIC_RENAMES[sym]
    for up, qb in (("_HERMES_", "_QUBIT_"), ("HERMES_", "QUBIT_"),
                   ("_hermes_", "_qubit_"), ("hermes_", "qubit_"),
                   ("Hermes", "Qubit")):
        if sym.startswith(up):
            return qb + sym[len(up):]
    # Leading-underscore private helper (`_hermes_repo_root`, `_hermes_home`).
    if sym.startswith("_hermes"):
        return "_qubit" + sym[len("_hermes"):]
    # Embedded occurrences anywhere in the name. UPPERCASE first so an identifier
    # like `_ZIP_HERMES_ROOT_ARTIFACTS` -> `_ZIP_QUBIT_ROOT_ARTIFACTS` (upstream
    # ->ours at qubit_cli/update_cmd_zip.py:29/327, IDENTICAL members). The
    # lowercase pass below would otherwise never fire on the uppercase token, so
    # the symbol re-reported as missing by the short_port branch every wave.
    if "HERMES" in sym:
        return sym.replace("HERMES", "QUBIT")
    if "hermes" in sym:
        return sym.replace("hermes", "qubit")
    if "Hermes" in sym:
        return sym.replace("Hermes", "Qubit")
    return sym


def top_defs(src: str) -> set[str]:
    """Top-level def/class/assigned names. Empty set when unparseable."""
    if not src:
        return set()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return set()
    out: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out.add(t.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out.add(node.target.id)
    return out


def sh(args: list[str], cwd: Path | None = None, env: dict | None = None,
       timeout: int = 600) -> str:
    try:
        p = subprocess.run(args, cwd=str(cwd) if cwd else None,
                           capture_output=True, text=True, timeout=timeout, env=env)
        return p.stdout
    except Exception:
        return ""


def route(path: str) -> str:
    """Upstream path -> our path, applying the rename map under tests/ too.

    Three transforms, applied to every segment (not just the last):
      * top-level module prefix   (`hermes_cli/...` -> `qubit_cli/...`)
      * renamed directory         (`plugins/hermes-achievements/` -> `.../qubit-achievements/`)
      * a ``hermes``-prefixed segment, e.g. a module: last-segment `_hermes_home.py`
        -> `_qubit_home.py` (ours ships that exact file under both
        skills/{research/grounded-citations,productivity/google-workspace}/scripts/).
        The old rule only fired when the LAST segment *started* with ``hermes``,
        so a leading underscore or an interior directory escaped it.
    """
    for prefix in ("tests/", ""):
        if prefix and not path.startswith(prefix):
            continue
        rest = path[len(prefix):] if prefix else path
        segs = rest.split("/")
        if segs and segs[0] in ROUTES:
            segs[0] = ROUTES[segs[0]]
        for i, seg in enumerate(segs):
            if seg in DIR_ROUTES:
                segs[i] = DIR_ROUTES[seg]
            elif seg.startswith("hermes"):
                # conservative: only the leading token, so a `hermes_flow.json`
                # data file (a frozen wire name) is not renamed into a missing path.
                segs[i] = "qubit" + seg[len("hermes"):]
            elif seg.lstrip("_").startswith("hermes"):
                # leading-underscore module: `_hermes_home.py` -> `_qubit_home.py`
                # (ours ships exactly that under both skills/*/scripts dirs). The
                # underscores are preserved so the private-module spelling matches.
                stripped = seg.lstrip("_")
                segs[i] = seg[:len(seg) - len(stripped)] + "qubit" + stripped[len("hermes"):]
        return prefix + "/".join(segs)
    return path


def blob(rev: str, path: str) -> str:
    """Read one file's content at a revision.

    The vendored clone is a PARTIAL clone (`partialclonefilter = tree:0`,
    `promisor = true`), so a blob it has not cached triggers an on-demand network
    fetch to GitHub. The audit calls this once per upstream file (~9,500), so an
    uncached tree turns a local walk into thousands of network round-trips and can
    block indefinitely on a stalled SSH session.

    Two guards, both needed (the first alone was not enough -- observed a run still
    hung at 0% CPU for 6 minutes with GIT_NO_LAZY_FETCH already set, because the
    parent blocked in do_wait on a git child that never returned):

      1. `GIT_NO_LAZY_FETCH=1` makes a MISSING blob fail fast instead of reaching for
         the network.
      2. A short per-call TIMEOUT bounds a git that hangs anyway, so the walk
         degrades to "file unreadable" instead of stalling the whole run. Without a
         timeout the default 600s per call x many calls is effectively unbounded.

    An unreadable file returns '' and is reported, never silently treated as absent.
    Prime the cache with `git fetch --filter=blob:none origin main` for a full run.
    """
    env = dict(os.environ, GIT_NO_LAZY_FETCH="1")
    return sh(["git", "-C", str(UP), "show", f"{rev}:{path}"], env=env, timeout=20)


_SKIP_PARTS = {"node_modules", "__pycache__", ".venv", "venv", "dist", "build",
               ".git", ".mypy_cache", ".pytest_cache", ".ruff_cache"}


def build_tree_def_index() -> dict[str, set[str]]:
    """name -> set of our files defining it, tree-wide.

    Built once. Needed to credit symbols that MOVED to a sibling module during
    the sync (e.g. `_codex_compaction_cooldown_remaining`, imported by
    agent/conversation_compression.py from agent.conversation_compression_codex).
    A same-file-only compare reports such a symbol as missing forever.
    """
    index: dict[str, set[str]] = defaultdict(set)
    for p in RUNTIME.rglob("*.py"):
        if any(part in _SKIP_PARTS for part in p.parts):
            continue
        try:
            src = p.read_text(errors="replace")
        except OSError:
            continue
        for name in top_defs(src):
            index[name].add(str(p.relative_to(REPO)))
    return index


def resolve(sym: str, our_defs: set[str], tree_defs: dict[str, set[str]]) -> str | None:
    """How `sym` is present in our tree, or None if genuinely missing.

    Returns a short provenance tag ('def', 'rebrand', 'move') for the report.
    Order: same-file def, then rebranded twin in the same file, then the twin (or
    the name itself) as a def anywhere in the tree (a move).
    """
    if sym in our_defs:
        return "def"
    twin = rebrand(sym)
    if twin != sym:
        if twin in our_defs:
            return "rebrand"
        if tree_defs.get(twin):
            return "move"
    # A symbol whose own name moved (no rename involved).
    if tree_defs.get(sym) and sym not in our_defs:
        return "move"
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json")
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--rev", default=HEAD,
                    help=f"upstream revision to audit against (default {HEAD})")
    ap.add_argument("--show-symbols", action="store_true",
                    help="emit the counted (missing) symbol names into the JSON, "
                         "per file -- the JSON otherwise carries only counts, which "
                         "forces every reader to write a scratch twin to debug it")
    args = ap.parse_args()

    # Every upstream .py file at the reference revision.
    files = [p for p in sh(["git", "-C", str(UP), "ls-tree", "-r", "--name-only",
                            args.rev]).split("\n") if p.endswith(".py")]
    print(f"# sync completeness audit -- upstream {args.rev} vs our tree")
    print(f"# upstream .py files: {len(files)}")
    print(f"# rebrand token map: {len(REBRAND_TOKENS)} engine tokens, "
          f"{len(SEMANTIC_RENAMES)} semantic renames, "
          f"{len(PRESERVED_WIRE)} preserved wire ids")

    # One tree-wide def index; used to credit moved symbols. ~20s for the tree.
    tree_defs = build_tree_def_index()

    exists = 0
    absent: list[str] = []
    short_port: list[tuple[str, int, int]] = []   # present but trails our own baseline
    behind_head: list[tuple[str, int, int]] = []
    # path -> {missing_symbols, by: {sym: provenance_that_would_credit_it}}
    detail: dict[str, dict] = {}
    current = 0

    for up_path in files:
        our_path = route(up_path)
        ours = RUNTIME / our_path
        if not ours.exists():
            # The routed path is authoritative: a missing routed file means the
            # work is not here, exactly where it should be. Do NOT fall back to
            # searching for a same-named file elsewhere -- that would hide a file
            # left at the wrong path (a real gap) and is the shape of false
            # negative this audit exists to avoid.
            absent.append(up_path)
            continue
        exists += 1
        try:
            our_src = ours.read_text(errors="replace")
        except OSError:
            our_src = ""
        our_syms = top_defs(our_src)
        head_syms = top_defs(blob(args.rev, up_path))

        # --- the rename subtraction, applied to EVERY comparison below ---
        # A head symbol is "present" if it resolves in our tree by any credited
        # route (same-file def / rebranded twin / move).
        missing_head = sorted(
            s for s in (head_syms - our_syms)
            if resolve(s, our_syms, tree_defs) is None)
        if not missing_head:
            current += 1
            continue

        base_syms = top_defs(blob(BASELINE, up_path))
        missing_base = sorted(
            s for s in (base_syms - our_syms)
            if resolve(s, our_syms, tree_defs) is None)

        if missing_base:
            short_port.append((up_path, len(missing_base), len(base_syms)))
            detail[up_path] = {"missing": missing_base, "branch": "short_port"}
        else:
            behind_head.append((up_path, len(missing_head), len(head_syms)))
            detail[up_path] = {"missing": missing_head, "branch": "behind_head"}

    total_missing_syms = sum(n for _, n, _ in short_port) + sum(n for _, n, _ in behind_head)
    print(f"# EXISTS: {exists}   ABSENT: {len(absent)}   CURRENT: {current}")
    print(f"# SHORT-PORT (trails our own baseline): {len(short_port)} files, "
          f"{sum(n for _, n, _ in short_port)} missing symbols")
    print(f"# BEHIND-HEAD (upstream moved on):      {len(behind_head)} files, "
          f"{sum(n for _, n, _ in behind_head)} missing symbols")
    print(f"# total missing symbols across present files: {total_missing_syms}")

    if short_port:
        print(f"\n## WORST SHORT-PORTS (we never even reached our own baseline)")
        for p, n, tot in sorted(short_port, key=lambda x: -x[1])[:15]:
            print(f"  {n:5} /{tot:5} missing   {p}")

    if absent:
        print(f"\n## ABSENT files by subsystem")
        c = Counter(p.split("/")[0] for p in absent)
        for k, v in c.most_common(15):
            print(f"  {v:5}  {k}")

    if args.json:
        payload = {
            "rev": args.rev, "upstream_py": len(files), "exists": exists,
            "absent": len(absent), "current": current,
            "short_port_files": len(short_port),
            "short_port_symbols": sum(n for _, n, _ in short_port),
            "behind_head_files": len(behind_head),
            "behind_head_symbols": sum(n for _, n, _ in behind_head),
            "worst_short_ports": [
                {"path": p, "missing": n, "baseline_defs": t}
                for p, n, t in sorted(short_port, key=lambda x: -x[1])],
            "absent_paths": absent[:2000],
        }
        if args.show_symbols:
            payload["symbols"] = detail
        Path(args.json).write_text(json.dumps(payload, indent=2))
        print(f"\nJSON written: {args.json}"
              + ("  (incl. per-file symbols via --show-symbols)" if args.show_symbols else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())

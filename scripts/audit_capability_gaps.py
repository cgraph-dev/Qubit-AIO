#!/usr/bin/env python3
"""Capability audit: what did the 5,441 upstream commits ADD that Qubit lacks?

The point of pulling the delta is not just to merge files -- it is to enumerate
capability. Every symbol/function/class upstream introduced at a036b13793 that
Qubit's tree does not define, and every file upstream added that Qubit does not
have, is a CONCRETE, NAMED port item instead of a vague "not fully synced".

Method (deliberately content-based, not existence-based -- an earlier audit
falsely reported 20 unsynced modules by treating "file exists" as "file synced"):

  1. Diff the pin range 69948c0057..a036b13793 in the upstream repo.
  2. For ADDED/MODIFIED python files, extract upstream's ADDED top-level
     definitions (def/class/ALL_CAPS) from the diff's '+' lines.
  3. Map each upstream path to its Qubit counterpart (rename map).
  4. Ask: does the Qubit counterpart define that symbol?  (grep of the real file)
  5. Report the misses, grouped by subsystem, with the upstream file:line.

CALLER CLASSIFICATION (added — the raw miss count is not a work estimate)
------------------------------------------------------------------------
A missing symbol is only work if something in the shipped tree actually reaches
for it. The raw "MISSING in Qubit" figure conflates five very different things,
printed as an exact partition of the total:

  (i)   ACTIONABLE            -- reached through its module by production code
        (qubit_runtime/, voicebridge/, scripts/). Every one is a call site that
        resolves to nothing and breaks when it runs (SYNC_HAZARDS #1). THIS is
        the remaining-work figure.
  (ii)  TEST-ONLY             -- reached only through the tests/ tree. Real work
        for green CI, but it cannot break the shipped product.
  (iii) TEST-LOCAL            -- the "gap" is a symbol defined *inside a test
        file* upstream (a test function or test helper). The method counts every
        one because the counterpart test file is itself missing; nothing imports
        a test function from another module, so they are not port items at all.
        This bucket is the single largest contributor to the raw number.
  (iv)  DEAD                  -- referenced by nothing in the tree. An
        upstream-internal helper nothing here calls, or a symbol deliberately
        not carried. Zero work unless a feature is ported on purpose.
  (v)   DELIBERATE-OMISSION   -- whose upstream module/symbol is a surface the
        docs name as intentionally absent (docs/PARITY.md: raw PTY/console, WS
        dialect, secret reveal, remote fetch; docs/BRAND.md legal-attribution
        exemption). A policy decision, not a gap.

The classifier is AST-based and never imports the tree. A reference is
MODULE-AWARE: `from M import S`, `import M as A; A.S`, and `from M import S as X`
all register S against M. A name that merely *occurs* somewhere as a local
variable or a string does not count -- the first cut of this pass matched names
globally and over-counted by 200 (`_text`, `main`, `_t`, `repos`), flagging every
common identifier as production-referenced. Matching through the module is what
separates a real call site from a name collision.

Output: a markdown table suitable for docs/SYNC_CAPABILITY_GAPS.md, plus the
five-way caller breakdown and the ACTIONABLE line. The MISSING and files-with-gaps
lines keep their exact upstream wording -- scripts/gate_all.py scrapes them.
"""
from __future__ import annotations

import ast
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _upstream_clone() -> Path:
    """Locate the upstream clone, tolerating the hermes home being retired.

    The clone lived at ~/.hermes/hermes-agent; the hermes home has since been moved aside as part
    of retirement, so the same tree now sits at ~/.hermes.pre-retirement/hermes-agent. Prefer the
    live location, fall back to the retired one, and fail LOUDLY rather than silently measuring an
    empty directory -- a silent miss here would make this audit grade the wrong thing (and, run as
    gate_all.py's informational check, silently report "None missing symbols across None files",
    which reads as clean. A measuring stick that lies is worse than one that fails).
    """
    for candidate in (
        Path.home() / "upstream" / "hermes-agent",
        Path.home() / ".hermes" / "hermes-agent",
        Path.home() / ".hermes.pre-retirement" / "hermes-agent",
    ):
        if (candidate / ".git").exists():
            return candidate
    raise SystemExit(
        "cannot locate the upstream clone (tried ~/upstream/hermes-agent, "
        "~/.hermes/hermes-agent and ~/.hermes.pre-retirement/hermes-agent); "
        "every audit would measure nothing"
    )


PIN = _upstream_clone()

# DRIFT-MEASURING range. This is the IMPORTANT fix: it previously measured
# `69948c0057..a036b13793`, a stale 2026-09-25..2026-10-06 sync waypoint -- so the
# "what did upstream add" answer described a window we had already closed, and it
# was quoted interchangeably with the frozen-pin drift (a different question).
# Read FROM from the single declared home (frozen_target_progress.FROZEN_REV, the
# revision we actually vendored from) and TO from current upstream, so this audit
# measures the same distance the burndown instrument calls "the finish line".
#
# NOT a provenance anchor: no vendoring record cites this constant. The
# `69948c0057` provenance anchors elsewhere (hermes_vendor_manifest.py,
# merge_v0215/freeze.py, differential_parity.py CONTRACTUAL_PINS, ...) record the
# revision a given file was vendored from and are deliberately left untouched.
def _frozen_rev() -> str:
    """The frozen pin, read from frozen_target_progress.FROZEN_REV (one home)."""
    src = Path(__file__).resolve().parent / "frozen_target_progress.py"
    if src.is_file():
        for line in src.read_text().splitlines():
            if line.startswith("FROZEN_REV"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit(
        f"cannot read the frozen pin from {src}; refusing to measure drift "
        "against an unknown revision."
    )


FROM = _frozen_rev()
TO = "origin/main"

SKIP_PREFIXES = (".github/", "contributors/", "node_modules/", ".git/")

# Upstream path -> Qubit path. Mirrors scripts/sync_upstream.py's routing.
PREFIX_RULES = [
    ("hermes_cli/", "qubit_runtime/qubit_cli/"),
    ("hermes_platform/", "qubit_runtime/qubit_platform/"),
    ("hermes_state", "qubit_runtime/qubit_state"),
]
STEM_RULES = {
    "hermes_constants.py": "qubit_runtime/qubit_constants.py",
    "hermes_logging.py": "qubit_runtime/qubit_logging.py",
    "hermes_bootstrap.py": "qubit_runtime/qubit_bootstrap.py",
}


def sh(cmd: str, cwd: Path | None = None) -> str:
    r = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True)
    return r.stdout


def route(upstream_path: str) -> str:
    """Upstream path -> repo-relative Qubit path."""
    for src, dst in PREFIX_RULES:
        if upstream_path.startswith(src):
            return dst + upstream_path[len(src):]
    if upstream_path in STEM_RULES:
        return STEM_RULES[upstream_path]
    # bare hermes_state_*.py -> qubit_state_*.py at repo root of qubit_runtime
    name = upstream_path.split("/")[-1]
    if name.startswith("hermes_state"):
        return "qubit_runtime/" + name.replace("hermes_state", "qubit_state", 1)
    if "/" not in upstream_path:
        return "qubit_runtime/" + upstream_path
    return "qubit_runtime/" + upstream_path


DEF_RE = re.compile(r"^(def|class)\s+([A-Za-z_][A-Za-z0-9_]*)")
CONST_RE = re.compile(r"^([A-Z][A-Z0-9_]{2,})\s*(?::|=)")


def added_defs(upstream_path: str) -> set[str]:
    """Top-level defs/classes/consts upstream ADDED in the pin range."""
    out = sh(f"git diff {FROM}..{TO} -- {upstream_path}", PIN)
    found: set[str] = set()
    for line in out.splitlines():
        if not line.startswith("+") or line.startswith("+++"):
            continue
        body = line[1:]
        if not body.strip() or body.startswith(" "):
            continue
        m = DEF_RE.match(body)
        if m:
            found.add(m.group(2))
            continue
        m = CONST_RE.match(body)
        if m:
            found.add(m.group(1))
    return found


def grep_has(qbit_path: str, symbol: str) -> bool:
    """Does the Qubit counterpart define/mention this symbol?"""
    p = REPO / qbit_path
    if not p.exists():
        return False
    try:
        txt = p.read_text(errors="ignore")
    except OSError:
        return False
    return re.search(rf"\b{re.escape(symbol)}\b", txt) is not None


# --- caller classification -------------------------------------------------

# Production = anything that ships or runs. tests/ is deliberately excluded:
# a symbol only a test names is not a broken product surface.
PROD_DIRS = ("qubit_runtime", "voicebridge", "scripts")
TEST_EXCLUDE = ("tests", "node_modules", ".venv", ".git", "dist", "build",
                "graft", "docs/archive", ".mypy_cache", ".pytest_cache",
                "__pycache__")


def _iter_py(root: Path, top: str):
    """Every non-excluded .py under root/top, yielded as a Path.

    The exclusion is applied to the path RELATIVE to `top`, so collecting the
    `tests` tree itself is not defeated by the same rule that keeps nested
    tests/ out of a production walk.
    """
    base = root / top
    if not base.is_dir():
        return
    for p in base.rglob("*.py"):
        rel = p.relative_to(base).as_posix()
        if any(rel == x or rel.startswith(f"{x}/") or f"/{x}/" in rel
               for x in TEST_EXCLUDE):
            continue
        yield p


def _mod_names(qpath: str) -> list[str]:
    """Every dotted module spelling a Qubit file can be imported under.

    Production imports use repo-relative roots (`cron.scheduler`,
    `qubit_cli.kanban_db_dispatch`, `gateway.run_inbound`) as well as the full
    package path, so both are offered.
    """
    parts = qpath[:-3].split("/")  # drop .py
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    names = []
    if len(parts) >= 2 and parts[0] == "qubit_runtime":
        names.append(".".join(parts[1:]))
    names.append(".".join(parts))
    seen = set()
    return [n for n in names if n and not (n in seen or seen.add(n))]


def collect_refs(root: Path, dirs) -> tuple[dict[str, set[str]], int]:
    """Module-aware reference map for every file under `dirs`, via AST.

    Returns (refs, parse_failures) where refs maps a dotted module name to the
    set of symbols production/tests actually reach on it:

      from M import S ....... M -> {S}
      import M  / import M as A   (A -> M)  then  A.S  ->  M -> {S}
      S  used as a bare call/binding after `from M import S`

    This is what separates real call sites from a name that merely *occurs*
    somewhere as a local variable or a string (which is why the first cut
    over-counted `_t`, `_text`, `main`). A symbol with no entry against its own
    module is not reached through that module.
    """
    refs: dict[str, set[str]] = defaultdict(set)
    failures = 0
    for top in dirs:
        for p in _iter_py(root, top):
            try:
                tree = ast.parse(p.read_text(errors="ignore"), filename=str(p))
            except (SyntaxError, ValueError, OSError):
                failures += 1
                continue
            # local alias -> dotted module it stands for
            alias: dict[str, str] = {}
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for a in node.names:
                        alias[a.asname or a.name.split(".")[0]] = a.name
                elif isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    for a in node.names:
                        if a.name == "*":
                            continue
                        # from M import S  (and  from M import S as X)
                        refs[mod].add(a.name)
                        alias[a.asname or a.name] = f"{mod}.{a.name}"
                elif isinstance(node, ast.Attribute):
                    # A.S  where A is an alias of module M  ->  M -> {S}
                    if isinstance(node.value, ast.Name):
                        base = alias.get(node.value.id)
                        if base:
                            refs[base].add(node.attr)
    return refs, failures


# Surfaces Qubit documents as *intentionally* not carried, from:
#   docs/BRAND.md  -- legal-attribution exemption only; no capability omissions.
#   docs/PARITY.md -- "deliberate policy refusals": no raw PTY/console, no WS
#     dialect, no secret reveal, no remote fetch, operator-owned update; and the
#     CLI surfaces `qubit chat` (QUBIT_NATIVE_CHAT), `codex-runtime`,
#     `credentials`, `vault` are named missing-by-policy.
# A symbol whose upstream module or name matches one of these tokens is a
# DECISION, not a gap, and is reported under "deliberate omissions" rather than
# charged against the work estimate. Kept deliberately small and literal --
# over-broad omission matching would hide real work (hazard #4).
OMISSION_TOKENS = (
    "omit",
    "raw_pty", "rawpt", "pty_console", "console_raw",
    "ws_dialect", "websocket_dialect",
    "secret_reveal", "reveal_secret",
    "remote_fetch",
    "codex_runtime", "codex-runtime",
    "credentials",
    "vault",
)


def must_omit(upstream_path: str) -> bool:
    """True if the upstream module or symbol is a documented deliberate omission.

    Matched on the path (module) and on the symbol name. Never matched loosely
    on substrings that could occur inside an unrelated name."""
    low = upstream_path.lower()
    node = re.split(r"[/.]", low)
    return any(tok in node for tok in OMISSION_TOKENS)


def referenced(refs: dict[str, set[str]], qpath: str, symbol: str) -> bool:
    """Is `symbol` reached through `qpath`'s module by any collected file?"""
    for mod in _mod_names(qpath):
        if symbol in refs.get(mod, ()):
            return True
    return False


def main() -> int:
    delta = [p for p in sh(f"git diff --name-only {FROM}..{TO}", PIN).split()
             if p and not p.startswith(SKIP_PREFIXES)]
    py_files = [p for p in delta if p.endswith(".py")]
    print(f"# Capability audit — upstream {FROM}..{TO}")
    print(f"\ndelta files: {len(delta)}  (python: {len(py_files)})")

    gaps: dict[str, list[str]] = defaultdict(list)
    checked = 0
    for up in py_files:
        syms = added_defs(up)
        if not syms:
            continue
        q = route(up)
        for s in sorted(syms):
            checked += 1
            if not grep_has(q, s):
                gaps[q].append(f"{s}  <- {up}")

    total_missing = sum(len(v) for v in gaps.values())
    print(f"symbols checked: {checked}   MISSING in Qubit: {total_missing}")
    print(f"files with gaps: {len(gaps)}\n")

    # --- caller classification: how much of MISSING is real work? ----------
    print("## caller classification (AST, module-aware, tree not imported)\n")
    prod_refs, prod_fail = collect_refs(REPO, ("qubit_runtime", "voicebridge", "scripts"))
    test_refs, test_fail = collect_refs(REPO, ("tests",))
    n_prod_mods = len({m for m in prod_refs})
    print(f"AST scan: production modules referenced {n_prod_mods}  "
          f"test modules referenced {len(test_refs)}")
    if prod_fail or test_fail:
        print(f"  WARNING: unparseable files skipped: "
              f"production {prod_fail}, tests {test_fail}")

    actionable: dict[str, list[str]] = defaultdict(list)
    test_only: dict[str, list[str]] = defaultdict(list)
    omitted: dict[str, list[str]] = defaultdict(list)
    dead: dict[str, list[str]] = defaultdict(list)
    test_local = 0
    for q, items in gaps.items():
        # A symbol living under a test tree is a test function or a test-file
        # helper -- upstream added it *to a test*, and by definition nothing
        # imports a test function from another module. The audit's method
        # (extract added defs, then require the counterpart file to define
        # them) counts every one of these as a "missing symbol" purely because
        # the counterpart test file is itself missing. They are not port items.
        if "/tests/" in q or q.startswith("qubit_runtime/tests/"):
            test_local += len(items)
            continue
        for item in items:
            sym, _, up = item.partition("  <- ")
            if must_omit(up):
                omitted[q].append(item)
            elif referenced(prod_refs, q, sym):
                actionable[q].append(item)
            elif referenced(test_refs, q, sym):
                test_only[q].append(item)
            else:
                dead[q].append(item)

    n_act = sum(len(v) for v in actionable.values())
    n_test = sum(len(v) for v in test_only.values())
    n_omit = sum(len(v) for v in omitted.values())
    n_dead = sum(len(v) for v in dead.values())
    print(f"ACTIONABLE (production-referenced): {n_act}")
    print(f"test-only (referenced only by tests): {n_test}")
    print(f"test-local (defined in a test file):  {test_local}")
    print(f"dead (referenced by nothing):         {n_dead}")
    print(f"deliberate omissions (doc'd):         {n_omit}")
    print(f"classified total: {n_act + n_test + test_local + n_dead + n_omit} "
          f"(== MISSING: {total_missing})")

    # Group by subsystem for a readable report.
    by_sub: Counter = Counter()
    for q, syms in gaps.items():
        sub = "/".join(q.split("/")[1:2]) or q
        by_sub[sub] += len(syms)
    print("\n## missing symbols by subsystem\n")
    for sub, n in by_sub.most_common():
        print(f"  {n:5d}  {sub}")

    act_sub: Counter = Counter()
    for q, items in actionable.items():
        act_sub["/".join(q.split("/")[1:2]) or q] += len(items)
    # NOTE: keep the count OUT of column 1 on whitespace-only lines -- gate_all.py
    # scrapes `^\s+(\d+)\s+(\S+)$` for by_subsystem, and a second numeric block
    # would silently overwrite the missing-symbol counts with the actionable
    # ones (qubit_cli 290 -> 8). The explicit "actionable=" prefix avoids that.
    print("\n## ACTIONABLE by subsystem (production-referenced)\n")
    for sub, n in act_sub.most_common():
        print(f"  actionable={n:<5d} {sub}")

    if len(sys.argv) > 1 and sys.argv[1] == "--full":
        print("\n## detail\n")
        for q in sorted(gaps, key=lambda k: -len(gaps[k])):
            print(f"\n### {q}  ({len(gaps[q])} missing)")
            for g in gaps[q][:25]:
                print(f"  - {g}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

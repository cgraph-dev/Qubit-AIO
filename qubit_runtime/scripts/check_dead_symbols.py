#!/usr/bin/env python3
"""DEAD-SYMBOL DETECTOR: defined in our tree, never called, called upstream.

WHY THIS EXISTS
---------------
The fork hit `AT TARGET` on a symbol-parity audit. That audit asked "does this
upstream symbol EXIST in our tree?" -- and 14 of them existed as *definitions*
with ZERO call sites while upstream called each one. Presence is not execution.
A helper defined and never invoked satisfies a name-presence audit and
contributes nothing to behaviour, which is how a tree reports parity while
silently not doing what upstream does.

This script asks the orthogonal question, per top-level symbol our tree DEFINES:

    does anything CALL it, or is it a dead definition that upstream uses?

A symbol is DEAD when all of the following hold:
  1. it is a top-level def / class / constant assignment in a file we own,
  2. it has zero use sites (see USE SITE) anywhere in our tree, and
  3. the pinned upstream tree READS the counterpart symbol -- upstream's copy of
     the same code, or any other upstream file, uses it beyond its own
     definition. That asymmetry is what name-parity cannot see.

USE SITE -- what counts as a call, and what does NOT
----------------------------------------------------
An AST pass over every owned .py file, never a text grep. A prior manual check
was fooled by a symbol mentioned inside a test COMMENT; a name in a comment or a
docstring is not a call. Counted as ONE use each: Load-context names (calls,
attribute bases, decorators, defaults, annotations), `global`/`nonlocal`,
`import`/`from ... import` bindings (importing IS a use: a module imported for
side effects, or a symbol imported so a test can patch it, is live), class bases,
and STRING-BOUND references (`getattr`/`setattr`/`delattr`/`patch` with a string
naming the symbol). Docstrings -- any bare string expression statement, anywhere
-- are removed before the walk, so a backticked `_name` mention in prose never
counts. A definition itself contributes ZERO uses (only Load names are
recorded), so "uses > 0" means something other than the definition refers to it.

STILL-REPORTED, NOT EXCUSED: TEST-RELEASE SEAMS
-----------------------------------------------
Two classes of definition have zero name-level call sites because pytest or the
stdlib binds them BY NAME. They stay in the dead list, tagged `seam_class`,
because they are exactly the wiring a port drops silently: nothing imports them
and no call exists to grep for.
  * `fixture`      -- `@pytest.fixture` / `@pytest_asyncio.fixture`, or an
    undecorated same-named def in a file where a function requests that name as
    a parameter (the repo ships both spellings; pytest collects either).
  * `http-handler` -- `do_GET`/`do_POST`/.../`log_message` on an
    `http.server.BaseHTTPRequestHandler` subclass with no `super().name(...)`.

EXCLUDED BENIGN (each printed with its reason; nothing is silently dropped)
--------------------------------------------------------------------------
  * `exported`   -- in `__all__`, imported by a module that star-imports this
    one, or imported under another name / with `# noqa: F401` (the repo's
    re-export convention).
  * `compat`     -- listed in qubit_runtime/compat_manifest.json: a deliberate
    plugin-compat layer whose documented consumer is OUTSIDE the repo.
  * `stub`       -- `@overload` / `@abstractmethod` / body only a docstring plus
    `pass` / `raise` / bare `return` (= a delegating stub, not the behaviour).
  * `entrypoint` -- `register`/`setup`/`main`/`pytest_*`/`cli_*`: a framework or
    manifest wires these by name.
  * `superclass` -- called as `super().name(...)` in the same file: we hold an
    override of a framework method upstream also defines.

UPSTREAM RESOLUTION
-------------------
Upstream is read at the frozen pin (from scripts/frozen_target_progress.py, the
pin's single declared home) with one `git ls-tree` + one `git cat-file --batch`
against the upstream clone. No worktree, no per-file process, no network: an
uncached partial-clone blob fails fast (GIT_NO_LAZY_FETCH=1) and is REPORTED as
unreadable, never silently read as "upstream does not use it".

Deterministic output: every list sorted, no timestamps, no absolute paths except
the two roots. Exit codes: 0 clean, 1 dead symbols found (CI gate), 2 the
instrument is broken -- never a clean verdict.

Usage:
    .venv/bin/python scripts/check_dead_symbols.py
    .venv/bin/python scripts/check_dead_symbols.py --json out.json -v
    .venv/bin/python scripts/check_dead_symbols.py --root /tmp/scratch-copy
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
import warnings
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

# The detector's own modules live beside this file under _dead_symbols/ so the
# script stays reviewable; scripts/ is not a package, so put the dir on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent / "_dead_symbols"))

from _cache import DIGEST, FactCache, default_dir  # noqa: E402
from _consts import ENTRYPOINT_NAMES, ENTRYPOINT_PREFIXES, PKG  # noqa: E402
from _facts import Definition, FileFacts, from_payload, parse_tree, to_payload  # noqa: E402
from _upstream import find_upstream, frozen_rev, read_pin  # noqa: E402
from _walk import module_name, owned_files  # noqa: E402

def _locate_repo() -> Path | None:
    """The checkout root: the ancestor that contains `qubit_runtime/`.

    NOT simply `parent.parent`: this file lives at
    `<root>/qubit_runtime/scripts/`, so the root is two levels up when the script
    sits inside the package and one level up for a root-level `scripts/` layout.
    Walking up (bounded) keeps the script relocatable and makes `--root`
    unnecessary for the common case.
    """
    here = Path(__file__).resolve().parent
    for cand in (here.parent, here.parent.parent):
        if (cand / PKG).is_dir():
            return cand
    return here.parent


REPO = _locate_repo()
# Resolve the audited root from --root when given (a scratch copy is the whole
# point of the negative control), else from this file's location. A script that
# does not live inside a Qubit checkout must not vouch for its own parent, so
# the hint only counts when it actually carries the package.
_hint = REPO
for _a in sys.argv[1:]:
    if _a.startswith("--root="):
        _hint = Path(_a.split("=", 1)[1]).expanduser()
    elif _a == "--root" and sys.argv.index(_a) + 1 < len(sys.argv):
        _hint = Path(sys.argv[sys.argv.index(_a) + 1]).expanduser()
if not (_hint / PKG).is_dir():
    raise SystemExit(f"REFUSING: {_hint} is not a Qubit checkout (no {PKG}/).\n"
                     "Run it from the repository, or pass --root <checkout>.")


@dataclass
class Hit:
    name: str
    module: str
    kind: str
    lineno: int
    upstream_uses: int = 0
    upstream_files: list[str] = field(default_factory=list)
    seam_class: str | None = None
    reason: str = ""

    def as_dict(self) -> dict:
        d = {"name": self.name, "module": self.module, "kind": self.kind,
             "line": self.lineno, "upstream_uses": self.upstream_uses,
             "upstream_files": sorted(self.upstream_files)}
        if self.seam_class:
            d["seam_class"] = self.seam_class
        if self.reason:
            d["exclusion"] = self.reason
        return d

    def sort_key(self) -> tuple:
        return (self.module, self.lineno, self.name)


def load_compat(root: Path) -> set[tuple[str, str]]:
    """(module_path, name) pairs the plugin-compat layer deliberately re-exports."""
    manifest = root / PKG / "compat_manifest.json"
    if not manifest.is_file():
        return set()
    try:
        data = json.loads(manifest.read_text())
    except (ValueError, OSError):
        return set()
    out = set()
    for entry in data.get("entries", []):
        facade = str(entry.get("facade", "")).replace(".", "/") + ".py"
        name = entry.get("name")
        if name:
            out.add((facade, name))
    return out


def build_owned_facts(root: Path, files: list[Path],
                      cache: FactCache | None = None) -> tuple[dict[str, FileFacts], list[str]]:
    facts: dict[str, FileFacts] = {}
    unparsed: list[str] = []
    for path in files:
        mod = module_name(root, path)
        try:
            raw = path.read_bytes()
        except OSError:
            unparsed.append(mod)
            continue
        cached = cache.get(raw) if cache else None
        if cached is not None:
            v = from_payload(cached)
            for d in v.defs + v.class_defs:
                d.module = mod
            facts[mod] = v
            continue
        try:
            src = raw.decode("utf-8", "replace")
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", SyntaxWarning)
                tree = parse_tree(src, str(path))
        except (SyntaxError, ValueError, OSError):
            unparsed.append(mod)
            continue
        v = FileFacts()
        v.visit(tree)
        for d in v.defs + v.class_defs:
            d.module = mod
        if cache:
            cache.put(raw, to_payload(v))
        facts[mod] = v
    return facts, unparsed


def build_upstream_index(blobs: dict[str, str],
                         cache: FactCache | None = None) -> dict[str, dict]:
    """name -> {"uses": n, "files": set(path)}, counted the same way we count ours."""
    index: dict[str, dict] = {}
    for path in sorted(blobs):
        src = blobs[path]
        raw = src.encode("utf-8", "replace")
        payload = cache.get(raw) if cache else None
        if payload is not None:
            v = from_payload(payload)
        else:
            try:
                tree = parse_tree(src, path)
            except (SyntaxError, ValueError):
                continue
            v = FileFacts()
            v.visit(tree)
            if cache:
                cache.put(raw, to_payload(v))
        for name, lines in v.uses.items():
            e = index.setdefault(name, {"uses": 0, "files": set()})
            e["uses"] += len(lines)
            e["files"].add(path)
    return index


def main() -> int:
    ap = argparse.ArgumentParser(description="dead-symbol detector")
    ap.add_argument("--root", default=str(REPO), help="checkout to audit (a copy is fine)")
    ap.add_argument("--upstream", default=None, help="upstream clone (default ~/.upstream/hermes-agent)")
    ap.add_argument("--rev", default=None, help="upstream pin (default: FROZEN_REV)")
    ap.add_argument("--json", dest="json_path", default=None, help="JSON report path ('-' = stdout)")
    ap.add_argument("--seams", choices=("report", "exclude"), default="report",
                    help="'report' keeps test-release seams in the dead list (default)")
    ap.add_argument("-v", "--verbose", action="store_true", help="list exclusions with reasons")
    ap.add_argument("--no-cache", action="store_true",
                    help="ignore and refresh the per-file fact cache")
    ap.add_argument("--cache-dir", default=None,
                    help="where the fact cache lives (default: <root>/.dead_symbols_cache)")
    args = ap.parse_args()

    root = Path(args.root).expanduser().resolve()
    if not (root / PKG).is_dir():
        print(f"FATAL: {root} has no {PKG}/ -- nothing to audit", file=sys.stderr)
        return 2
    # The pin comes from scripts/frozen_target_progress.py (its single declared
    # home), located relative to THIS script -- never from a scratch path.
    rev = args.rev or frozen_rev()
    if not rev:
        print("FATAL: no pin (pass --rev)", file=sys.stderr)
        return 2
    up = find_upstream(args.upstream)
    try:
        blobs = read_pin(up, rev)
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return 2

    cache = FactCache(Path(args.cache_dir) if args.cache_dir else default_dir(root),
                      enabled=not args.no_cache)
    files = owned_files(root)
    facts, unparsed = build_owned_facts(root, files, cache)
    if not facts:
        print("FATAL: parsed 0 owned files -- the instrument is wrong", file=sys.stderr)
        return 2
    compat = load_compat(root)

    # name -> definitions; AND name -> uses across the whole owned tree.
    defs_by_name: dict[str, list[Definition]] = defaultdict(list)
    for mod in sorted(facts):
        for d in facts[mod].defs + facts[mod].class_defs:
            defs_by_name[d.name].append(d)
    total_uses: dict[str, int] = defaultdict(int)
    assigned_by_mod: dict[str, set[str]] = defaultdict(set)
    star_imported_by: set[str] = set()
    all_exported: set[str] = set()
    reexported: set[str] = set()
    param_names: set[str] = set()
    fixture_defs: set[tuple[str, str]] = set()
    handler_defs: set[tuple[str, str]] = set()
    super_calls: dict[str, set[str]] = defaultdict(set)
    for mod in sorted(facts):
        f = facts[mod]
        for name, lines in f.uses.items():
            total_uses[name] += len(lines)
        assigned_by_mod[mod] |= {u for u, ls in f.uses.items() if len(ls) > 1}
        star_imported_by.add(mod[:-3].replace("/", ".") if mod.endswith(".py") else mod)
        all_exported |= f.all_names
        reexported |= f.reexport
        param_names |= f.param_names
        fixture_defs |= {(mod, n) for n in f.fixture_defs}
        handler_defs |= {(mod, n) for n in f.http_handler_defs}
        super_calls[mod] |= f.super_calls

    # Names ASSIGNED without a module-level `def` -- `X = ...`, `X: T = ...`,
    # `X = Y = ...`, and `for X in ...:` targets -- are definitions too (they are
    # just written as assignments). A name whose only occurrences are its own
    # assignments is a dead constant. Names that also have a def entry are
    # already covered above. Module-level `import x` / `from y import x` are
    # EXCLUDED: a bare `import foo` produces one Load and no use line, and
    # reporting a symbol imported for its side effects would be a false positive.
    for mod in sorted(facts):
        f = facts[mod]
        extra = assigned_by_mod[mod] - set(f.def_names) - f.import_bound
        for name in sorted(extra):
            defs_by_name[name].append(Definition(mod, name, "constant", f.first_line[name]))
    # Modules imported by `from X import *` export their whole surface.
    starred_modules = {m for f in facts.values() for m in f.star_imports}
    star_exported: set[str] = set()
    for mod, f in facts.items():
        dotted = mod[:-3].replace("/", ".") if mod.endswith(".py") else mod
        if dotted in starred_modules or dotted.replace(".", "/") + ".py" in starred_modules:
            star_exported |= {d.name for d in f.defs + f.class_defs}

    up_index = build_upstream_index(blobs, cache)

    dead: list[Hit] = []
    excluded: list[Hit] = []
    no_upstream = 0
    for name in sorted(defs_by_name):
        if total_uses.get(name, 0):
            continue                              # live: something refers to it
        entry = up_index.get(name)
        if not entry:
            # upstream never reads it either: newly invented here, or renamed
            # during the port. Not this detector's target -- named as such.
            no_upstream += len(defs_by_name[name])
            continue
        for d in defs_by_name[name]:
            hit = Hit(name, d.module, d.kind, d.lineno,
                      upstream_uses=entry["uses"], upstream_files=sorted(entry["files"]))
            hit.seam_class = _seam(d, fixture_defs, handler_defs, param_names)
            if hit.seam_class and args.seams == "exclude":
                hit.reason = f"seam:{hit.seam_class}"
                excluded.append(hit)
                continue
            reason = _exclusion(d, all_exported | star_exported | reexported, compat, super_calls)
            if reason:
                hit.reason = reason
                excluded.append(hit)
            else:
                dead.append(hit)

    dead.sort(key=lambda h: h.sort_key())
    excluded.sort(key=lambda h: h.sort_key())
    seams = sum(1 for h in dead if h.seam_class)

    report = {
        "tool": "check_dead_symbols",
        "pin": rev,
        "root": str(root),
        "cache": {"dir": str(cache.dir), "digest": DIGEST, "enabled": cache.enabled,
                  "hits": cache.hits, "misses": cache.misses},
        "owned_files_parsed": len(facts),
        "owned_files_unparsed": sorted(unparsed),
        "upstream_files_read": len(blobs),
        "upstream_files_unreadable": sorted(getattr(read_pin, "last_missing", [])),
        "definitions_examined": sum(len(v) for v in defs_by_name.values()),
        "defs_upstream_never_reads": no_upstream,
        "dead_count": len(dead),
        "dead_of_which_test_seams": seams,
        "dead_symbols": [h.as_dict() for h in dead],
        "excluded_count": len(excluded),
        "excluded_benign": [h.as_dict() for h in excluded],
    }

    print(f"dead-symbol check  pin={rev[:12]}  root={root}")
    print(f"  owned .py parsed     : {len(facts)}"
          + (f"   UNPARSED {len(unparsed)}" if unparsed else ""))
    print(f"  upstream .py at pin  : {len(blobs)}"
          + (f"   UNREADABLE {len(report['upstream_files_unreadable'])}"
             if report["upstream_files_unreadable"] else ""))
    print(f"  defs examined        : {report['definitions_examined']}")
    print(f"  defs upstream never  : {no_upstream}  (invented here / renamed -- out of scope)")
    print(f"  DEAD (upstream reads): {len(dead)}  ({seams} are test-release seams)")
    if dead:
        print()
        print(f"  {'symbol':38} {'module':44} reads  line")
        for h in dead:
            mark = f"  [{h.seam_class}]" if h.seam_class else ""
            print(f"  {h.name:38} {h.module:44} {h.upstream_uses:5} {h.lineno:5}{mark}")
    if excluded and args.verbose:
        print(f"\n  EXCLUDED as benign ({len(excluded)}), each with its reason:")
        for h in excluded:
            print(f"    {h.name:38} {h.module:44} {h.reason}")

    if args.json_path:
        payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
        if args.json_path == "-":
            sys.stdout.write(payload)
        else:
            Path(args.json_path).write_text(payload)
            print(f"\n  JSON -> {args.json_path}")

    if unparsed:
        print(f"\nFATAL: {len(unparsed)} owned files unparsable; result is incomplete, "
              f"not clean: {sorted(unparsed)[:5]}", file=sys.stderr)
        return 2
    return 1 if dead else 0


def _seam(d: Definition, fixtures: set[tuple[str, str]], handlers: set[tuple[str, str]],
          params: set[str]) -> str | None:
    """Test-release seam class for a dead definition, or None.

    Both classes are wired by name only, so a static call counter always shows
    zero -- which is exactly why they must be REPORTED, not silently excused.
    """
    key = (d.module, d.name)
    if key in handlers:
        return "http-handler"
    if key in fixtures:
        return "fixture"
    if d.kind == "function" and d.name in params and _in_tests(d.module):
        return "fixture (undecorated; requested as a parameter)"
    return None


def _in_tests(module: str) -> bool:
    segs = module.split("/")
    return "tests" in segs or segs[-1].startswith("test_") or segs[-1] == "conftest.py"


def _exclusion(d: Definition, exported: set[str], compat: set[tuple[str, str]],
               super_calls: dict[str, set[str]]) -> str | None:
    if (d.module, d.name) in compat:
        return "compat: plugin-compat re-export (compat_manifest.json)"
    if d.name in exported:
        return "exported: __all__ / star-import / noqa re-export"
    if d.stub:
        return "stub: overload/abstract/protocol body"
    if d.name in ENTRYPOINT_NAMES or d.name.startswith(ENTRYPOINT_PREFIXES):
        return "entrypoint: framework wires this by name"
    if d.name in super_calls.get(d.module, set()):
        return "superclass: called via super().name in this file"
    return None


if __name__ == "__main__":
    sys.exit(main())

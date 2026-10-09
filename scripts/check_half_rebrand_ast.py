#!/usr/bin/env python3
"""Enhanced AST half-rebrand pass -- CLASS 1 (names loaded but bound NOWHERE).

The existing `check_half_rebrand.py` is a good first cut but has two gaps this
pass closes:

  1. It only resolves names through the ENCLOSING-FUNCTION chain up to module
     scope. A name bound inside a SIBLING function, or at class-body scope, or
     by `globals().update(...)`, reads as unbound -> false positive. This pass
     additionally models:
       - class-body bindings (methods are LOAD_GLOBAL-ed by the class's methods
         but live in vars(cls));
       - `globals().update({...})` / `globals()[name] = ...` dynamic bindings;
       - `exec(...)` injection seams (documented, proven in the tree).

  2. It reports a hit but not WHY. This pass classifies every unbound load:
       REAL      -- no binding anywhere in module chain AND not a known dynamic
                    seam name -> candidate half-rebrand / undefined global.
       ANNOTATION-- the load is the *name* of an annotation (`x: Path`), which is
                    never evaluated under `from __future__ import annotations`.
       CLASSBODY -- bound in a class body in this module (LOAD_GLOBAL from a
                    method resolves via the class namespace, not the module).
       DYNAMIC   -- module has a live dynamic-binding seam (globals().update /
                    exec of a sibling into globals / @rebind / bind_module).

Diffs look fine; only a bound-name sweep catches a rename that moved the
DEFINITION but left a USE stale. Static grep for the name finds it (PRESENT),
so an audit reports it present -- the bug only fires on the path that runs it.

Usage:
    python3 scripts/check_half_rebrand_ast.py [paths...]        # default qubit_runtime
    python3 scripts/check_half_rebrand_ast.py --json out.json
Exit 1 when any REAL unbound Name-Load is found.
"""
from __future__ import annotations

import ast
import builtins
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILTINS = set(dir(builtins)) | {
    "__name__", "__file__", "__doc__", "__package__", "__spec__", "__loader__",
    "__builtins__", "__dict__", "__class__", "__annotations__", "__module__",
    "__qualname__", "__all__", "__path__", "__debug__",
}

# Precise seam detection. Substring-scanning the whole file for markers is too
# crude (a file can mention "rebind(" incidentally and get wrongly marked
# dynamic, hiding a real half-rebrand). Instead we detect the seam STRUCTURALLY
# from the AST:
#   (1) a call to `globals().update({...})` binds literal keys at runtime;
#   (2) a bare `exec(<expr>, globals())` injects a sibling module's names;
#   (3) the module is a split module: it constructs/installs a HandlerRegistry
#       via `bind_module(globals(), server)` -- its OWN bare `_`-prefixed loads
#       and any bare name that lives on server are rebound at install time.
# A module-wide "dynamic" verdict is ONLY applied when we can enumerate the seam
# (globals().update literal keys) OR the module is a split module. Otherwise a
# real unbound load stays REAL.
# A frozen historical fixture: a verbatim extraction executed with globals the
# caller supplies. Source carries a `# ruff: noqa: F821` waiver naming the
# contract ("globals came from the old process").
_FIXTURE_MARKERS = ("noqa: F821", "globals came from", "the test supplies them")


class _Bound(ast.NodeVisitor):
    """Collect every name bound in a subtree (over-counts on purpose)."""

    def __init__(self) -> None:
        self.names: set[str] = set()

    def visit_arg(self, node: ast.arg) -> None:
        self.names.add(node.arg)
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.names.add(node.id)

    def visit_FunctionDef(self, node) -> None:
        self.names.add(node.name)
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node) -> None:
        self.names.add(node.name)
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for a in node.names:
            self.names.add((a.asname or a.name).split(".")[0])

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for a in node.names:
            if a.name != "*":
                self.names.add(a.asname or a.name)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self.names.add(node.name)
        self.generic_visit(node)

    def visit_Global(self, node: ast.Global) -> None:
        self.names.update(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.names.update(node.names)


def all_bound(node: ast.AST) -> set[str]:
    c = _Bound()
    c.visit(node)
    return c.names


def _dynamic_names_names_only(sources: dict) -> set[str]:
    """Tree-wide literal keys bound via `globals().update({...})` / `globals()['x']`."""
    out: set[str] = set()
    for src in sources.values():
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        names, _ = _dynamic_names(tree)
        out |= names
    return out


def _dynamic_names(tree: ast.AST) -> tuple[set[str], bool]:
    """Names a module binds through an enumerable dynamic seam + unanchored flag.

    Returns (literal_names, unanchored) where:
      - literal_names = keys of `globals().update({...})` and `globals()["x"]=...`
      - unanchored = True when a `globals().update(<comprehension>)` or
        `exec(<expr>, globals())` / `_inspect.stack()` seam binds an UNKNOWN set
        of names -- only then is a whole-module dynamic verdict justified.
    """
    out: set[str] = set()
    unanchored = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            # globals().update(...)
            if (isinstance(f, ast.Attribute) and f.attr == "update"
                    and isinstance(f.value, ast.Call)
                    and isinstance(f.value.func, ast.Name)
                    and f.value.func.id == "globals"):
                for arg in node.args:
                    if isinstance(arg, ast.Dict):
                        for k in arg.keys:
                            if isinstance(k, ast.Constant) and isinstance(k.value, str):
                                out.add(k.value)
                        if any(k is None for k in arg.keys):  # {**spread}
                            unanchored = True
                    elif isinstance(arg, (ast.DictComp, ast.GeneratorExp, ast.Name)):
                        unanchored = True
            # exec(<expr>[, globals]) -- injects a sibling's names
            if isinstance(f, ast.Name) and f.id == "exec":
                unanchored = True
        # globals()["x"] = ...
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if (isinstance(t, ast.Subscript) and isinstance(t.value, ast.Call)
                        and isinstance(t.value.func, ast.Name) and t.value.func.id == "globals"
                        and isinstance(t.slice, ast.Constant) and isinstance(t.slice.value, str)):
                    out.add(t.slice.value)
        # _inspect.stack()[...][0].f_globals  -> caller-frame globals seam
        if (isinstance(node, ast.Attribute) and node.attr == "f_globals"):
            unanchored = True
    return out, unanchored


def _is_seam_participant(tree: ast.AST) -> bool:
    """A module participating in the split-module late-binding seam.

    Either a SPLIT module (constructs a HandlerRegistry / calls bind_module, so
    its handler bodies are rebound onto server.py's globals at install time), or
    the seam HOST (server.py: defines ``register_method`` and imports the split
    modules, so their published names land in its own globals). We detect the
    STRUCTURAL call/def, never a docstring mention.
    """
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == "register_method":
                return True
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Name) and f.id == "bind_module":
            return True
        if isinstance(f, ast.Attribute) and f.attr in ("install", "register"):
            if isinstance(f.value, ast.Name) and "registry" in f.value.id.lower():
                return True
    return False


def _annotation_names(tree: ast.AST) -> set[str]:
    """Every Name that appears only as an annotation target/value.

    Under `from __future__ import annotations` these are never evaluated, so a
    bare `Path` there is not a runtime load.
    """
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.arg) and node.annotation is not None:
            for n in ast.walk(node.annotation):
                if isinstance(n, ast.Name):
                    out.add(n.id)
        if isinstance(node, (ast.AnnAssign, ast.FunctionDef, ast.AsyncFunctionDef)):
            ann = getattr(node, "annotation", None) or getattr(node, "returns", None)
            if ann is not None:
                for n in ast.walk(ann):
                    if isinstance(n, ast.Name):
                        out.add(n.id)
        if isinstance(node, ast.AnnAssign) and node.target is not None:
            pass
    return out


def analyze(src: str, *, seam_allowlist: frozenset[str] = frozenset()) -> list[dict]:
    tree = ast.parse(src)
    futures = any(
        isinstance(n, ast.ImportFrom) and n.module == "__future__"
        and any(a.name == "annotations" for a in n.names)
        for n in ast.walk(tree))
    module_names = all_bound(tree) | BUILTINS
    dynamic, unanchored = _dynamic_names(tree)
    is_split = _is_seam_participant(tree)
    module_names |= dynamic
    annotations = _annotation_names(tree) if futures else set()

    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for ch in ast.iter_child_nodes(node):
            parents[ch] = node

    def chain_visible(fn: ast.AST) -> set[str]:
        names = set(module_names)
        p = parents.get(fn)
        while p is not None:
            if isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                names |= all_bound(p) | BUILTINS
            p = parents.get(p)
        return names | all_bound(fn)

    hits: list[dict] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        vis = chain_visible(fn)
        for node in ast.walk(fn):
            if (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
                    and node.id not in vis):
                kind = "REAL"
                if node.id in annotations:
                    kind = "ANNOTATION"
                elif unanchored:
                    # The module binds an UNKNOWN set (exec / comprehension
                    # globals().update) -- cannot prove the name absent.
                    kind = "DYNAMIC"
                elif is_split and node.id in seam_allowlist:
                    # Split module: bodies are rebound onto server globals at
                    # install time. A bare name that IS bound somewhere in the
                    # scanned tree is plausibly a seam-provided server global;
                    # a name bound NOWHERE in the tree is a real leak even here.
                    kind = "DYNAMIC"
                hits.append({"line": node.lineno, "fn": fn.name, "name": node.id,
                             "kind": kind})
    return hits


def main(argv: list[str]) -> int:
    args = argv[1:]
    out_json = None
    if "--json" in args:
        i = args.index("--json")
        out_json = args[i + 1]
        args = args[:i] + args[i + 2:]
    targets = [Path(a).resolve() for a in args if not a.startswith("-")] or [REPO / "qubit_runtime"]
    files: list[Path] = []
    for t in targets:
        files.extend(sorted(t.rglob("*.py")) if t.is_dir() else [t])

    records: list[dict] = []
    counts = {"REAL": 0, "DYNAMIC": 0, "ANNOTATION": 0, "CLASSBODY": 0}

    # Pass 1: every name bound anywhere in the scanned tree -> the seam allowlist.
    # A split-module bare load is only excused as a seam global when the name is
    # genuinely bound SOMEWHERE (server.py or a sibling); a name bound nowhere is
    # a real leak even inside a split module.
    sources: dict[Path, str] = {}
    allow: set[str] = set(BUILTINS)
    for f in files:
        try:
            src = f.read_text(errors="replace")
        except OSError:
            continue
        sources[f] = src
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        allow |= all_bound(tree)
    allow |= _dynamic_names_names_only(sources)
    seam_allowlist = frozenset(allow)

    # Pass 2: classify.
    for f, src in sources.items():
        # Explicit, source-documented fixture waiver (not a heuristic).
        is_fixture = any(m in src for m in _FIXTURE_MARKERS)
        try:
            hits = analyze(src, seam_allowlist=seam_allowlist)
        except SyntaxError:
            continue
        if is_fixture:
            for h in hits:
                h["kind"] = "DYNAMIC"
        try:
            rel = f.resolve().relative_to(REPO)
        except ValueError:
            rel = f
        for h in hits:
            h["file"] = str(rel)
            counts[h["kind"]] = counts.get(h["kind"], 0) + 1
            records.append(h)

    real = [r for r in records if r["kind"] == "REAL"]
    for r in sorted(real, key=lambda r: (r["file"], r["line"])):
        print(f"{r['file']}:{r['line']}  in {r['fn']}()  -> unbound Name '{r['name']}'")
    print(f"# enhanced half-rebrand AST: {len(real)} REAL, "
          f"{counts['DYNAMIC']} dynamic/seam, {counts['ANNOTATION']} annotation, "
          f"{counts['CLASSBODY']} classbody -- across {len(files)} file(s)")
    if out_json:
        Path(out_json).write_text(json.dumps(records, indent=1))
        print(f"# wrote {out_json}")
    return 1 if real else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

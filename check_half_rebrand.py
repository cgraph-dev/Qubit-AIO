#!/usr/bin/env python3
"""Scope-aware AST half-rebrand detector.

WHY: a rename that touched a helper's PARAMETER but not its CALL SITE leaves a
Name-Load bound nowhere -> NameError that takes out a whole feature. This is the
bug class that killed the model picker (`name 'qubit_id' is not defined`).
Diffs look correct; only a bound-name sweep catches it.

SCOPE LIMIT -- READ THIS OR YOU WILL GET 3000 FALSE HITS:
  tui_gateway/*.py (and any module that calls `method_ctx.bind_module`) deliberately
  reference server.py globals BARE -- their bodies are rebound onto server.py's
  namespace at install time. The naive detector reads every such reference as
  unbound. Those files are therefore SKIPPED by default. Do not "fix" a hit there
  without first checking whether the name lives in the bind source's globals.

The naive per-function collector also reports ~135 false hits on ordinary files
(every nested closure whose name lives in the ENCLOSING scope). This variant
resolves names through the full enclosing-function chain + module scope +
builtins, so it reports only real unbound loads on conventional modules.

Usage:
    python3 scripts/check_half_rebrand.py [paths...]     # default: qubit_runtime
    python3 scripts/check_half_rebrand.py --include-rebound   # also scan bind_module files
Exit 1 when any unbound Name-Load is found.
"""
from __future__ import annotations
import ast, builtins, sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILTINS = set(dir(builtins)) | {
    "__name__", "__file__", "__doc__", "__package__", "__spec__", "__loader__",
    "__builtins__", "__dict__", "__class__", "__annotations__", "__module__",
    "__qualname__", "__all__", "__path__",
}
# Modules whose bodies are rebound onto a host module's globals at install time.
REBOUND_MARKERS = ("bind_module(",)


class Collector(ast.NodeVisitor):
    """Every name bound anywhere in a subtree (deliberately over-counts)."""
    def __init__(self) -> None:
        self.names: set[str] = set()

    def visit_arg(self, node): self.names.add(node.arg)

    def visit_Name(self, node):
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.names.add(node.id)

    def visit_FunctionDef(self, node):
        self.names.add(node.name); self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):
        self.names.add(node.name); self.generic_visit(node)

    def visit_Import(self, node):
        for a in node.names:
            self.names.add((a.asname or a.name).split(".")[0])

    def visit_ImportFrom(self, node):
        for a in node.names:
            if a.name != "*":
                self.names.add(a.asname or a.name)

    def visit_ExceptHandler(self, node):
        if node.name:
            self.names.add(node.name)
        self.generic_visit(node)

    def visit_Global(self, node): self.names.update(node.names)
    def visit_Nonlocal(self, node): self.names.update(node.names)


def all_bound(node) -> set[str]:
    c = Collector(); c.visit(node); return c.names


def check(src: str) -> list[tuple[int, str, str]]:
    tree = ast.parse(src)
    module_names = all_bound(tree) | BUILTINS
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for ch in ast.iter_child_nodes(node):
            parents[ch] = node

    def visible_in(fn) -> set[str]:
        names = set(module_names)
        p = parents.get(fn)
        while p is not None:
            if isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                names |= all_bound(p) | BUILTINS
            p = parents.get(p)
        return names | all_bound(fn)

    hits: list[tuple[int, str, str]] = []
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            vis = visible_in(fn)
            for node in ast.walk(fn):
                if (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
                        and node.id not in vis):
                    hits.append((node.lineno, fn.name, node.id))
    return hits


def main(argv: list[str]) -> int:
    args = argv[1:]
    include_rebound = "--include-rebound" in args
    targets = [Path(a).resolve() for a in args if not a.startswith("-")] or [REPO / "qubit_runtime"]
    files: list[Path] = []
    for t in targets:
        files.extend(t.rglob("*.py") if t.is_dir() else [t])

    bad = scanned = skipped = 0
    for f in files:
        try:
            src = f.read_text(errors="replace")
        except OSError:
            continue
        if not include_rebound and any(m in src for m in REBOUND_MARKERS):
            skipped += 1
            continue
        try:
            hits = check(src)
        except SyntaxError:
            continue
        scanned += 1
        try:
            rel = f.resolve().relative_to(REPO)
        except ValueError:
            rel = f
        for ln, fn, name in hits:
            print(f"{rel}:{ln}  in {fn}()  -> unbound Name '{name}'")
            bad += 1
    print(f"# half-rebrand check: {bad} unbound Name-Load(s) across {scanned} file(s) "
          f"({skipped} bind_module file(s) skipped)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

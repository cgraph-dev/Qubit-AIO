#!/usr/bin/env python3
"""Half-rebrand detector for observability/*.py.

Reports every Name-Load (`ast.Name` with Load ctx) bound NOWHERE in the scopes
visible at that point: module bindings, ancestor function/class locals, builtins.

Each scope (module / function / lambda / class) is handled separately:
  * a scope's bound names come from a FLAT scan of its own statements (never
    descending into nested def/class bodies)
  * a nested FunctionDef/ClassDef contributes only its NAME plus its
    decorators/defaults (evaluated in the enclosing scope) to the parent
  * loads are collected only from a scope's own statements

Handles: args (posonly/args/kwonly/vararg/kwarg), assigned Names, import
aliases, except-as, for/comprehension targets, lambda params, with-as, walrus,
match captures, global/nonlocal, del. Conservative (over-binds => no added
false positives); verifiable against a known-bad self-test.

Usage: ast_half_rebrand_check.py <dir-or-file> [...]
"""
import ast
import builtins
import sys
from pathlib import Path

BUILTINS = set(dir(builtins)) | {
    "__file__", "__name__", "__doc__", "__package__", "__spec__", "__loader__",
    "__builtins__", "__path__", "__class__",
}
DEF_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def target_names(node, out):
    if isinstance(node, ast.Name):
        out.add(node.id)
    elif isinstance(node, (ast.Tuple, ast.List)):
        for e in node.elts:
            target_names(e, out)
    elif isinstance(node, ast.Starred):
        target_names(node.value, out)


class BindCollector(ast.NodeVisitor):
    """Collect names bound by a FLAT sequence of statements. Nested def/class
    bodies are separate scopes and are NOT descended into; only the def NAME and
    its decorator/default expressions (which live in this scope) are captured."""

    def __init__(self):
        self.names = set()
        self.stars = False

    def collect(self, stmts):
        for s in stmts:
            self.visit(s)
        return self

    def _flat(self, node):
        for child in ast.iter_child_nodes(node):
            self.visit(child)

    def visit_FunctionDef(self, node):
        self.names.add(node.name)
        for d in node.decorator_list:
            self.visit(d)
        a = node.args
        for d in list(a.defaults) + [x for x in a.kw_defaults if x]:
            self.visit(d)

    def visit_AsyncFunctionDef(self, node):
        self.visit_FunctionDef(node)

    def visit_ClassDef(self, node):
        self.names.add(node.name)
        for d in node.decorator_list:
            self.visit(d)
        for b in node.bases:
            self.visit(b)
        for kw in node.keywords:
            self.visit(kw)

    def visit_Name(self, node):
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.names.add(node.id)

    def visit_arg(self, node):
        self.names.add(node.arg)

    def visit_Import(self, node):
        for a in node.names:
            self.names.add((a.asname or a.name).split(".")[0])

    def visit_ImportFrom(self, node):
        for a in node.names:
            if a.name == "*":
                self.stars = True
            else:
                self.names.add(a.asname or a.name)

    def visit_ExceptHandler(self, node):
        if node.name:
            self.names.add(node.name)
        self._flat(node)

    def visit_Global(self, node):
        self.names.update(node.names)

    def visit_Nonlocal(self, node):
        self.names.update(node.names)

    def visit_MatchAs(self, node):
        if node.name:
            self.names.add(node.name)
        self._flat(node)

    def visit_MatchStar(self, node):
        if node.name:
            self.names.add(node.name)
        self._flat(node)

    def visit_MatchMapping(self, node):
        if node.rest:
            self.names.add(node.rest)
        self._flat(node)

    def visit_Lambda(self, node):
        for a in node.args.posonlyargs + node.args.args + node.args.kwonlyargs:
            self.names.add(a.arg)
        if node.args.vararg:
            self.names.add(node.args.vararg.arg)
        if node.args.kwarg:
            self.names.add(node.args.kwarg.arg)
        for d in list(node.args.defaults) + [x for x in node.args.kw_defaults if x]:
            self.visit(d)

    def _comp(self, node):
        # over-approximate: comp targets treated as bound in the enclosing scope
        for gen in node.generators:
            target_names(gen.target, self.names)
            self.visit(gen.iter)
            for c in gen.ifs:
                self.visit(c)
        for field in ("elt", "key", "value"):
            v = getattr(node, field, None)
            if v is not None:
                self.visit(v)

    def visit_ListComp(self, node):
        self._comp(node)

    def visit_SetComp(self, node):
        self._comp(node)

    def visit_DictComp(self, node):
        self._comp(node)

    def visit_GeneratorExp(self, node):
        self._comp(node)

    def visit_With(self, node):
        for item in node.items:
            if item.optional_vars:
                target_names(item.optional_vars, self.names)
        self._flat(node)

    def visit_AsyncWith(self, node):
        self.visit_With(node)

    def visit_For(self, node):
        target_names(node.target, self.names)
        self._flat(node)

    def visit_AsyncFor(self, node):
        self.visit_For(node)

    def visit_comprehension(self, node):
        target_names(node.target, self.names)
        self._flat(node)


def loads_in_own_scope(stmts):
    """Yield Name-Load nodes belonging to THIS scope.

    For a bare FunctionDef/ClassDef statement, only its decorators and argument
    defaults belong to this scope; its body is a nested scope (handled by
    child_scopes). For every other statement, walk it fully but stop at nested
    def/class/lambda nodes (their bodies are their own scopes)."""
    for stmt in stmts:
        if isinstance(stmt, DEF_NODES):
            header = []
            header.extend(stmt.decorator_list)
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                a = stmt.args
                header.extend(a.defaults)
                header.extend(x for x in a.kw_defaults if x)
            elif isinstance(stmt, ast.ClassDef):
                header.extend(stmt.bases)
                header.extend(stmt.keywords)
            for h in header:
                for n in _walk_stopping_at_scopes(h):
                    if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
                        yield n
            continue
        for n in _walk_stopping_at_scopes(stmt):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
                yield n


def _walk_stopping_at_scopes(node):
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        for child in ast.iter_child_nodes(n):
            if isinstance(child, (DEF_NODES + (ast.Lambda,))):
                continue
            stack.append(child)


def child_scopes(stmts):
    """Yield nested def/class/lambda scopes among these statements, without
    descending into another nested def's body. A top-level def statement IS a
    child scope of the enclosing scope."""
    for stmt in stmts:
        if isinstance(stmt, (DEF_NODES + (ast.Lambda,))):
            yield stmt
            continue
        stack = [stmt]
        while stack:
            n = stack.pop()
            for child in ast.iter_child_nodes(n):
                if isinstance(child, (DEF_NODES + (ast.Lambda,))):
                    yield child
                else:
                    stack.append(child)


def _args_of(fn):
    a = fn.args
    names = {x.arg for x in a.posonlyargs + a.args + a.kwonlyargs}
    if a.vararg:
        names.add(a.vararg.arg)
    if a.kwarg:
        names.add(a.kwarg.arg)
    return names


def walk_scope(stmts, inherited, stars_seen, hits, extra_bound=frozenset()):
    binding = BindCollector().collect(stmts)
    bound = set(inherited) | binding.names | set(extra_bound) | BUILTINS
    if binding.stars or stars_seen:
        bound.add("+STAR+")
    for load in loads_in_own_scope(stmts):
        if load.id not in bound:
            hits.append((load.lineno, load.id))
    for scope in child_scopes(stmts):
        if isinstance(scope, ast.Lambda):
            walk_scope([scope.body], bound, binding.stars or stars_seen, hits,
                       extra_bound=frozenset(_args_of(scope)))
        else:
            # def/class node: bind its name + (for functions) its args, then walk body
            fn_args = frozenset(_args_of(scope)) if isinstance(
                scope, (ast.FunctionDef, ast.AsyncFunctionDef)) else frozenset()
            walk_scope(scope.body, bound | {scope.name}, binding.stars or stars_seen,
                       hits, extra_bound=fn_args)


def check_file(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    hits = []
    walk_scope(tree.body, set(), False, hits)
    out, seen = [], set()
    for ln, nm in sorted(hits):
        if (ln, nm) in seen:
            continue
        seen.add((ln, nm)); out.append((ln, nm))
    return out


def iter_files(args):
    for a in args:
        p = Path(a)
        if p.is_dir():
            yield from sorted(p.glob("*.py"))
        elif p.is_file():
            yield p


def main():
    args = sys.argv[1:] or ["qubit_runtime/qubit_cli/observability"]
    files = list(iter_files(args))
    total = 0
    for f in files:
        hits = check_file(f)
        if hits:
            total += len(hits)
            print(f"\n{f}")
            for ln, nm in hits:
                print(f"  {f}:{ln}: name '{nm}' is not defined")
    print(f"\n=== {total} unbounded Name-Load(s) across {len(files)} file(s) ===")


if __name__ == "__main__":
    main()

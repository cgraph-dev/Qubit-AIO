"""AST facts: definitions, use sites, fixture/entrypoint/export markers."""
from __future__ import annotations

import ast
from collections import defaultdict
from dataclasses import dataclass

from _consts import (ENTRYPOINT_NAMES, ENTRYPOINT_PREFIXES, FIXTURE_DECORATORS,
                     HTTP_HANDLER_BASES, HTTP_HANDLER_METHODS, LAZY_LOOKUP_CALLS,
                     PYTEST_CLASS_PREFIX, ROUTE_DECORATORS, STRING_BOUND_CALLS,
                     STUB_DECORATORS, UNITTEST_BASES)


@dataclass
class Definition:
    module: str
    name: str
    kind: str            # function | class | constant | method-stub
    lineno: int
    stub: bool = False
    body_calls: tuple[str, ...] = ()
    class_names: tuple[str, ...] = ()


def _strip_docstrings(tree: ast.AST) -> None:
    """Blank every bare-string statement so prose never counts as a use.

    This is what makes a docstring mention of `_name` invisible to the use
    counter -- a prior manual audit was fooled by exactly that (a symbol that
    was "used" only in a test comment).
    """
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list):
            continue
        node.body = [s for s in body
                     if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant)
                             and isinstance(s.value.value, str))]


def _annotate_parents(tree: ast.AST) -> None:
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            child._parent = parent  # type: ignore[attr-defined]


def _decorator_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for dec in getattr(node, "decorator_list", []):
        target = dec.func if isinstance(dec, ast.Call) else dec
        while isinstance(target, ast.Attribute):
            names.add(target.attr)
            target = target.value
        if isinstance(target, ast.Name):
            names.add(target.id)
    return names


def _string_bound(node: ast.Call, out: list[str], lineno: int) -> None:
    """A string naming a symbol at a dynamic lookup site.

    ``patch("mod.name")``, ``getattr(mod, "name")``: the handler is bound by
    string, not by a Name/Attribute Load, so without this a live helper looks
    dead. Dot-separated strings contribute each part (the module path is
    harmless; the final identifier is the reference).
    """
    fn = node.func
    fname = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
    if fname not in STRING_BOUND_CALLS:
        return
    for arg in node.args:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            for part in arg.value.split("."):
                if part.isidentifier():
                    out.append(part)


def _lazy_lookup(node: ast.Call) -> list[str]:
    """``_lazy("module", "name")``: the late-bound callee is named by string."""
    fn = node.func
    fname = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
    if fname not in LAZY_LOOKUP_CALLS:
        return []
    out: list[str] = []
    for arg in node.args:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            for part in arg.value.split("."):
                if part.isidentifier():
                    out.append(part)
    return out


def _dispatch_strings(value: ast.AST) -> list[str]:
    """Identifier-shaped strings in the VALUES of a module-level dict literal.

    A dispatch table (``_DURABLE_CLAIM_OPS = {"drop": ("drop_completion_delivery", ...)}``)
    selects the callee by this string; the function name never appears as a
    Load. Only dict values bound at module level qualify -- dict keys are
    command names, and a stray string in arbitrary runtime data is not a
    dispatch key.
    """
    if not isinstance(value, ast.Dict):
        return []
    out: list[str] = []
    for val in value.values:
        for sub in ast.walk(val):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                for part in sub.value.split("."):
                    if part.isidentifier():
                        out.append(part)
    return out


def _route_decorated(node: ast.AST) -> bool:
    """True for ``@router.post("/api/...")``-style framework registration.

    Requires BOTH a route verb and a path-shaped string, so ``@cache.delete(key)``
    or ``@something.get()`` is not mistaken for a route. Keyword paths
    (``rule=``/``path=``) are accepted because Flask/Quart use them.
    """
    for dec in getattr(node, "decorator_list", []):
        if not isinstance(dec, ast.Call):
            continue
        fn = dec.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
        if name not in ROUTE_DECORATORS:
            continue
        paths = [a.value for a in dec.args
                 if isinstance(a, ast.Constant) and isinstance(a.value, str)]
        paths += [kw.value.value for kw in dec.keywords
                  if kw.arg in ("path", "rule") and isinstance(kw.value, ast.Constant)
                  and isinstance(kw.value.value, str)]
        if any(p.startswith("/") for p in paths):
            return True
    return False


def _pytest_collected_class(node: ast.ClassDef) -> bool:
    """A class pytest collects by name or unittest base.

    pytest rejects a class that defines ``__init__`` (it cannot instantiate it
    with fixtures), so a ``Test*`` name alone is not enough -- the construct
    must also be instantiable the way pytest does it.
    """
    for base in node.bases:
        name = base.attr if isinstance(base, ast.Attribute) else getattr(base, "id", "")
        if name in UNITTEST_BASES:
            return True
    if not node.name.startswith(PYTEST_CLASS_PREFIX):
        return False
    for stmt in node.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)) and stmt.name == "__init__":
            return False
    return True


class FileFacts(ast.NodeVisitor):
    def __init__(self) -> None:
        self.defs: list[Definition] = []
        self.class_defs: list[Definition] = []
        self.uses: dict[str, list[int]] = defaultdict(list)
        self.dispatch_refs: set[str] = set()
        self.all_names: set[str] = set()
        self.star_imports: list[str] = []
        self.reexport: set[str] = set()
        self.fixture_defs: set[str] = set()
        self.param_names: set[str] = set()
        self.http_handler_defs: set[str] = set()
        self.route_defs: set[str] = set()
        self.pytest_classes: set[str] = set()
        self.super_calls: set[str] = set()
        self._top_calls: list[str] = []
        self._depth = 0
        self._cls: list[str] = []
        self.def_names: set[str] = set()
        self._bound: set[str] = set()
        self.import_bound: set[str] = set()
        self.first_line: dict[str, int] = {}

    # ---- uses ------------------------------------------------------------
    def _use(self, name: str, lineno: int) -> None:
        self.uses[name].append(lineno)
        self.first_line.setdefault(name, lineno)
        if self._depth == 0:
            self._top_calls.append(name)

    def _bind(self, name: str, lineno: int) -> None:
        """Record an ASSIGNMENT binding of `name` (a Store context).

        `X = ...`, `X: T = ...`, `X = Y = ...`, `a, X = ...`, `for X in ...`,
        `with x() as X` -- every one is a definition of a module-level name in
        the same way `def X` is. We record ONE use for the binding itself so that
        a name bound and never read has uses == 1 (the binding), while a name
        read anywhere has >= 2. What we must never do is mistake a bare
        `import foo` (also a Store) for a definition, so imports are tracked
        separately in `import_bound` and never count as constants.
        """
        self.uses[name].append(lineno)
        self.first_line.setdefault(name, lineno)
        self._bound.add(name)

    def visit_Assign(self, node: ast.Assign) -> None:
        all_target = False
        for t in node.targets:
            if isinstance(t, ast.Name) and t.id == "__all__":
                all_target = True
                self._collect_all(node.value)
            elif isinstance(t, ast.Name) and self._depth == 0:
                self._bind(t.id, node.lineno)
            elif isinstance(t, (ast.Tuple, ast.List)) and self._depth == 0:
                for elt in t.elts:
                    if isinstance(elt, ast.Name):
                        self._bind(elt.id, node.lineno)
        if self._depth == 0 and not all_target:
            for name in _dispatch_strings(node.value):
                self.dispatch_refs.add(name)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        t = node.target
        if isinstance(t, ast.Name) and t.id != "__all__" and self._depth == 0 and node.value:
            self._bind(t.id, node.lineno)
            for name in _dispatch_strings(node.value):
                self.dispatch_refs.add(name)
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        t = node.target
        if isinstance(t, ast.Name) and t.id == "__all__":
            self._collect_all(node.value)
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        if self._depth == 0 and isinstance(node.target, ast.Name):
            self._bind(node.target.id, node.lineno)
        self.generic_visit(node)

    visit_AsyncFor = visit_For  # noqa: N815

    def visit_With(self, node: ast.With) -> None:
        for item in node.items:
            if self._depth == 0 and isinstance(item.optional_vars, ast.Name):
                self._bind(item.optional_vars.id, node.lineno)
        self.generic_visit(node)

    visit_AsyncWith = visit_With  # noqa: N815

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            self._use(node.id, node.lineno)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name) \
                and node.value.func.id == "super":
            self.super_calls.add(node.attr)
        self._use(node.attr, node.lineno)
        self.generic_visit(node)

    def visit_Global(self, node: ast.Global) -> None:
        for n in node.names:
            self._use(n, node.lineno)

    visit_Nonlocal = visit_Global  # noqa: N815

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            bound = alias.asname or alias.name.split(".")[0]
            self.import_bound.add(bound)
            if alias.asname:
                self._use(alias.asname, node.lineno)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        noqa = _has_noqa(node)
        for alias in node.names:
            if alias.name == "*":
                if node.module:
                    self.star_imports.append(node.module)
                continue
            bound = alias.asname or alias.name
            self.import_bound.add(bound)
            self._use(bound, node.lineno)
            if noqa or (alias.asname and alias.asname != alias.name):
                self.reexport.add(bound)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        strings: list[str] = []
        _string_bound(node, strings, node.lineno)
        for s in strings:
            self._use(s, node.lineno)
        self.dispatch_refs.update(_lazy_lookup(node))
        self.generic_visit(node)

    # ---- definitions -----------------------------------------------------
    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        if self._depth == 0:
            self.def_names.add(node.name)
            self.class_defs.append(Definition(
                "", node.name, "class", node.lineno,
                stub=bool(_decorator_names(node) & STUB_DECORATORS)))
            if _pytest_collected_class(node):
                self.pytest_classes.add(node.name)
        self._cls.append(node.name)
        self._depth += 1
        self.generic_visit(node)
        self._depth -= 1
        self._cls.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        dec = _decorator_names(node)
        stub = bool(dec & STUB_DECORATORS) or _is_stub_body(node)
        top = self._depth == 0
        if top:
            if dec & FIXTURE_DECORATORS:
                self.fixture_defs.add(node.name)
            if _in_http_handler(node):
                self.http_handler_defs.add(node.name)
            if _route_decorated(node):
                self.route_defs.add(node.name)
        self._note_args(node)
        self._depth += 1
        before = len(self._top_calls)
        self.generic_visit(node)
        body_calls = tuple(sorted(set(self._top_calls[before:])))
        self._depth -= 1
        if top:
            self.def_names.add(node.name)
            self.defs.append(Definition(
                "", node.name, "function", node.lineno, stub=stub,
                class_names=tuple(self._cls), body_calls=body_calls))

    visit_AsyncFunctionDef = visit_FunctionDef  # noqa: N815

    def _note_args(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        a = node.args
        for arg in (*a.posonlyargs, *a.args, *a.kwonlyargs, a.vararg, a.kwarg):
            if arg is not None and arg.arg != "self":
                self.param_names.add(arg.arg)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs,
                    node.args.vararg, node.args.kwarg):
            if arg is not None and arg.arg != "self":
                self.param_names.add(arg.arg)
        self.generic_visit(node)

    def _collect_all(self, value: ast.AST) -> None:
        for sub in ast.walk(value):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                self.all_names.add(sub.value)


def _has_noqa(node: ast.AST) -> bool:
    return bool(getattr(node, "_noqa", False))


def _is_stub_body(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """A body that delegates rather than implements: empty, `pass`, `raise`, bare return.

    A one-line `return expr` is NOT a stub -- `def _x(k): return lambda cfg: ...`
    is real behaviour, and classifying it as a stub silently excused a genuinely
    dead helper (_needs_extra) during development. Only `raise` counts as a
    deliberate protocol/ABC placeholder.
    """
    stmts = [s for s in node.body
             if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
    if not stmts:
        return True
    if len(stmts) == 1:
        s = stmts[0]
        if isinstance(s, (ast.Pass, ast.Raise)):
            return True
        if isinstance(s, ast.Return) and s.value is None:
            return True
    return False


def _in_http_handler(node: ast.AST) -> bool:
    cur = getattr(node, "_parent", None)
    while cur is not None:
        if isinstance(cur, ast.ClassDef):
            for base in cur.bases:
                names = set()
                if isinstance(base, ast.Attribute):
                    names.add(base.attr)
                elif isinstance(base, ast.Name):
                    names.add(base.id)
                if names & HTTP_HANDLER_BASES:
                    return True
        cur = getattr(cur, "_parent", None)
    return False


def parse_tree(src: str, filename: str) -> ast.AST:
    tree = ast.parse(src, filename=filename)
    _annotate_parents(tree)
    _mark_noqa(tree, src)
    _strip_docstrings(tree)
    return tree


def _mark_noqa(tree: ast.AST, src: str) -> None:
    """Attach `# noqa` on an import's own line to the ImportFrom node."""
    lines = src.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            lineno = node.lineno
            if 1 <= lineno <= len(lines) and "noqa" in lines[lineno - 1]:
                node._noqa = True  # type: ignore[attr-defined]


# ---- cache payload (the expensive pass, serialized) ------------------------
# Everything the caller needs from one parsed file, flattened to JSON. Kept here
# beside the visitor so a new field is added in one place; `from_payload`
# reconstructs the SAME object `FileFacts.visit` would have produced, which is
# what lets a warm cache run print numbers identical to a cold one.

def to_payload(v: FileFacts) -> dict:
    return {
        "v": 2,
        "defs": [[d.module, d.name, d.kind, d.lineno, d.stub,
                  list(d.body_calls), list(d.class_names)] for d in v.defs],
        "class_defs": [[d.module, d.name, d.kind, d.lineno, d.stub, [], []] for d in v.class_defs],
        "uses": {k: sorted(vs) for k, vs in v.uses.items()},
        "dispatch_refs": sorted(v.dispatch_refs),
        "all_names": sorted(v.all_names),
        "star_imports": sorted(v.star_imports),
        "reexport": sorted(v.reexport),
        "fixture_defs": sorted(v.fixture_defs),
        "param_names": sorted(v.param_names),
        "http_handler_defs": sorted(v.http_handler_defs),
        "route_defs": sorted(v.route_defs),
        "pytest_classes": sorted(v.pytest_classes),
        "super_calls": sorted(v.super_calls),
        "def_names": sorted(v.def_names),
        "import_bound": sorted(v.import_bound),
        "first_line": v.first_line,
        # `_top_calls` is recorded not as the sequence but as the per-definition
        # slice; `assigned_by_mod` downstream only asks "was this name used more
        # than once in this module", which `uses` already answers.
        "top_calls": sorted(set(v._top_calls)),
    }


def from_payload(payload: dict) -> FileFacts:
    v = FileFacts()
    v.defs = [Definition(*row[:4], stub=row[4], body_calls=tuple(row[5]),
                         class_names=tuple(row[6])) for row in payload["defs"]]
    v.class_defs = [Definition(*row[:4], stub=row[4]) for row in payload["class_defs"]]
    v.uses = defaultdict(list, {k: list(vs) for k, vs in payload["uses"].items()})
    v.dispatch_refs = set(payload.get("dispatch_refs", []))
    v.all_names = set(payload["all_names"])
    v.star_imports = list(payload["star_imports"])
    v.reexport = set(payload["reexport"])
    v.fixture_defs = set(payload["fixture_defs"])
    v.param_names = set(payload["param_names"])
    v.http_handler_defs = set(payload["http_handler_defs"])
    v.route_defs = set(payload.get("route_defs", []))
    v.pytest_classes = set(payload.get("pytest_classes", []))
    v.super_calls = set(payload["super_calls"])
    v.def_names = set(payload["def_names"])
    v.import_bound = set(payload["import_bound"])
    v.first_line = dict(payload["first_line"])
    v._top_calls = list(payload["top_calls"])
    return v

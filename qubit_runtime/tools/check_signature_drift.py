#!/usr/bin/env python3
"""check_signature_drift.py -- find caller/callee SIGNATURE disagreements.

Symbol-name audits (grep the def, grep the name) are blind to a function whose
NAME is present but whose SIGNATURE drifted. `qubit_runtime/qubit_cli/
update_cmd_git.py`'s `_discard_lockfile_churn` was exactly that: present, but
missing a `checkout_move=None` kwarg its call site already passed -> a live
TypeError. This checker finds that class.

HOW IT WORKS
------------
1. AST-parse every .py file under the given roots.
2. Collect every module-level / nested `FunctionDef` / `AsyncFunctionDef` by
   *qualified* name (module path + optional Class.method + function), recording
   its parameter shape: positional names, keyword-only names, whether it has
   `*args`, `**kwargs`, and defaults.
3. Walk every `Call` node. For a call whose callee text (`foo`, `self.foo`,
   `mod.foo`) resolves to a collected definition, compare:
     - every keyword argument must be a declared parameter (or absorbable by
       `**kwargs`);
     - the positional-argument count must not exceed the callee's arity
       (count of declared positional params, or "unbounded" with `*args`).
   A mismatch is a REAL disagreement (the call would raise at runtime).
4. Anything that cannot be resolved to a local definition, or whose callee is
   dynamic (getattr, call-on-result, subscript, lambda), is reported as
   UNRESOLVED and NOT flagged -- a false positive that edits a signature is
   worse than leaving a real one.

DELIBERATELY NOT FLAGGED (documented, so the report is honest):
  - callees with `**kwargs` (accept any keyword by construction)
  - callees with `*args` (accept any positional count)
  - calls to names defined in more than one scope with DIFFERENT shapes
    (ambiguous; reported separately under "AMBIGUOUS", still not flagged)
  - decorator-wrapped names whose wrapper is not AST-visible (the decorated
    def is the one we see; a wrapper that changes the signature is invisible
    to pure AST -- call this out in the report)

USAGE
-----
    python check_signature_drift.py <root> [<root> ...] [--json] [--quiet]

Exit code is 0 when there are no REAL disagreements, 1 otherwise.
"""

from __future__ import annotations

import argparse
import ast
import builtins
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

_BUILTINS = frozenset(dir(builtins))


@dataclass
class ParamShape:
    pos_names: list[str] = field(default_factory=list)      # positional-or-keyword + positional-only
    kwonly_names: list[str] = field(default_factory=list)   # declared after *
    has_varargs: bool = False                               # *args
    has_varkw: bool = False                                 # **kwargs
    n_required_pos: int = 0                                 # positional params without a default

    @property
    def max_pos(self) -> int | None:
        """Max positional args accepted; None = unbounded (*args)."""
        if self.has_varargs:
            return None
        return len(self.pos_names)

    def accepts_keyword(self, name: str) -> bool:
        if self.has_varkw:
            return True
        return name in self.pos_names or name in self.kwonly_names

    def describe(self) -> str:
        parts = list(self.pos_names)
        if self.has_varargs:
            parts.append("*args")
        if self.kwonly_names:
            parts.append("*")
            parts.extend(self.kwonly_names)
        if self.has_varkw:
            parts.append("**kwargs")
        return "(" + ", ".join(parts) + ")"


@dataclass
class FuncDef:
    qualname: str          # e.g. qubit_cli.update_cmd_git._discard_lockfile_churn
    short: str             # _discard_lockfile_churn
    dotted: str            # _discard_lockfile_churn OR ClassName.method
    shape: ParamShape
    file: str
    lineno: int


def _shape(args: ast.arguments) -> ParamShape:
    pos = [a.arg for a in list(args.posonlyargs) + list(args.args)]
    kwonly = [a.arg for a in args.kwonlyargs]
    n_required = 0
    defaults = list(args.defaults)
    # rightmost `len(defaults)` positional params have defaults
    n_pos_default = len(defaults)
    n_pos = len(pos)
    n_required = n_pos - n_pos_default
    # kwonly args without defaults require the caller to pass them, but they are
    # never a positional/arity question; keep n_required_pos positional-only.
    return ParamShape(
        pos_names=pos,
        kwonly_names=kwonly,
        has_varargs=args.vararg is not None,
        has_varkw=args.kwarg is not None,
        n_required_pos=max(n_required, 0),
    )


def _collect_defs(tree: ast.AST, file: str, module: str) -> list[FuncDef]:
    out: list[FuncDef] = []

    class V(ast.NodeVisitor):
        def __init__(self):
            self.stack: list[str] = []

        def visit_ClassDef(self, node: ast.ClassDef):
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        def _fn(self, node):
            short = node.name
            dotted = ".".join(self.stack + [short]) if self.stack else short
            qual = (module + "." + dotted) if module else dotted
            out.append(FuncDef(
                qualname=qual, short=short, dotted=dotted,
                shape=_shape(node.args), file=file, lineno=node.lineno,
            ))
            self.stack.append(short)
            self.generic_visit(node)
            self.stack.pop()

        visit_FunctionDef = _fn
        visit_AsyncFunctionDef = _fn

    V().visit(tree)
    return out


def _resolve_receivers(tree: ast.AST, module: str, local_prefixes: frozenset[str]) -> tuple[set[str], dict[str, str]]:
    """Names that can be attributed to a LOCAL module.

    Returns (imported_module_aliases, {local_bare_name: source_module_dotted}).
    A from-import bound to a LOCAL module maps the bare name to that module's
    dotted path, so the caller can resolve the definition by its qualified name
    instead of guessing a same-short-name def elsewhere (the false positive that
    edits a real signature). Non-local from-imports (stdlib etc.) are dropped, so
    their names never resolve to our defs. `local_prefixes` are the top-level
    package names our roots define."""
    imported_modules: set[str] = set()
    local_from_names: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                top = (a.name.split(".")[0])
                if top in local_prefixes:
                    imported_modules.add(a.asname or a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            src = node.module or ""
            src_top = src.split(".")[0]
            if src_top in local_prefixes:
                for a in node.names:
                    if a.name != "*":
                        local_from_names[a.asname or a.name] = f"{src}.{a.name}" if src else a.name
    return imported_modules, local_from_names


def _call_name(node: ast.Call) -> tuple[str | None, str, str]:
    """Return (resolvable_name, KIND, receiver).

    KIND is one of: 'name' (bare call `foo()`), 'self' (`self.foo()`),
    'module' (`alias.foo()`), or None (unresolvable -> skip).

    Only these three kinds are name-resolvable. A method call on an arbitrary
    receiver cannot be attributed by pure AST, so it is skipped rather than
    guessed -- a false positive that edits a signature is worse than a miss.
    """
    f = node.func
    if isinstance(f, ast.Name):
        return f.id, "name", ""
    if isinstance(f, ast.Attribute):
        base = f.value
        if isinstance(base, ast.Name) and base.id in ("self", "cls"):
            return f.attr, "self", base.id
        if isinstance(base, ast.Name):
            return f.attr, "module", base.id
        if isinstance(base, ast.Attribute) and isinstance(base.value, ast.Name):
            return f.attr, "module", base.value.id
    return None, "", ""


def _module_name(path: Path, root: Path) -> str:
    rel = path.relative_to(root)
    parts = list(rel.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def scan(root: Path) -> tuple[list[FuncDef], list[dict], list[dict], int]:
    defs: list[FuncDef] = []
    real: list[dict] = []
    ambiguous: list[dict] = []
    unresolved = 0

    py_files = sorted(p for p in root.rglob("*.py")
                      if "__pycache__" not in p.parts)
    # top-level package names this root defines -> "local" for import resolution
    local_prefixes = frozenset(
        p.name for p in root.iterdir() if p.is_dir() and (p / "__init__.py").exists()
    ) | frozenset({root.name})
    for path in py_files:
        try:
            src = path.read_text(encoding="utf-8")
            tree = ast.parse(src, filename=str(path))
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        mod = _module_name(path.resolve(), root.resolve())
        defs.extend(_collect_defs(tree, str(path), mod))

    # index by short name and by qualified name (module.path.func), plus a
    # normalized key using the module's trailing segments so an import like
    # `from qubit_cli.update_cmd_git import X` resolves even when the scanned
    # root's own module names are shallower (root = .../qubit_cli).
    by_short: dict[str, list[FuncDef]] = {}
    by_qual: dict[str, list[FuncDef]] = {}
    by_tail: dict[str, list[FuncDef]] = {}
    for d in defs:
        by_short.setdefault(d.short, []).append(d)
        by_qual.setdefault(d.qualname, []).append(d)
        parts = d.qualname.split(".")
        for k in (1, 2, 3):          # last 1..3 segments: func / mod.func / pkg.mod.func
            if len(parts) >= k:
                by_tail.setdefault(".".join(parts[-k:]), []).append(d)

    for path in py_files:
        try:
            src = path.read_text(encoding="utf-8")
            tree = ast.parse(src, filename=str(path))
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        this_module = _module_name(path.resolve(), root.resolve())
        imported_modules, from_modules = _resolve_receivers(tree, this_module, local_prefixes)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name, kind, receiver = _call_name(node)
            if name is None:
                unresolved += 1
                continue
            # Resolution gates -- each term below removes a real false-positive
            # class (stdlib/builtin/arbitrary-object method sharing a short name):
            #   module alias  : only a known LOCAL import alias
            #   builtin       : print / open / str.replace-flavoured names are not ours
            #   bare name     : only a def in THIS module, or a LOCAL from-import
            if kind == "module" and receiver not in imported_modules:
                unresolved += 1
                continue
            if name in _BUILTINS:
                unresolved += 1
                continue
            cands = by_short.get(name)
            if not cands:
                unresolved += 1
                continue
            if kind == "self":
                # self.X / cls.X can only be a method (dotted name has a class segment)
                cands = [c for c in cands if "." in c.dotted]
            elif name in from_modules:
                # bare `X()` bound by a local `from M import X [as Y]`: resolve
                # the DEFINITION whose qualified name is `M.X`. Match on the
                # trailing segments too, so an import `from qubit_cli.mod import
                # X` resolves a def scanned as `mod.X`. A re-exported name (no
                # def at any matching tail) stays unresolved -- never guessed.
                src = from_modules[name]
                cands = by_qual.get(src) or by_tail.get(src) or []
                if not cands:
                    # try shorter/longer tails of `M.X` (drop a leading segment)
                    for cut in (src.split(".")[1:], src.split(".")[-2:]):
                        key = ".".join(cut)
                        if key in by_tail:
                            cands = by_tail[key]
                            break
            elif kind == "module":
                # alias.X(...) where alias is a local module import
                cands = [c for c in cands if c.dotted == name]
            else:
                # bare `X()` -> a def in THIS module
                cands = [c for c in cands if "." not in c.dotted and c.file == str(path)]
            if not cands:
                unresolved += 1
                continue
            # de-dup identical shapes (same def re-imported) -- if more than one
            # DISTINCT shape exists under this short name, it is ambiguous.
            shapes = {c.shape.describe(): c for c in cands}
            if len(shapes) > 1:
                # Not flagged: name collision across scopes. Report for the human.
                kw = [k.arg for k in node.keywords if k.arg is not None]
                ambiguous.append({
                    "file": str(path), "lineno": node.lineno, "callee": name,
                    "kwargs": kw,
                    "candidates": [f"{c.file}:{c.lineno} {c.shape.describe()}" for c in cands],
                })
                continue
            callee = next(iter(shapes.values()))
            sh = callee.shape
            problems: list[str] = []
            # keyword check
            for k in node.keywords:
                if k.arg is None:      # **expr passthrough
                    continue
                if not sh.accepts_keyword(k.arg):
                    problems.append(f"kwarg '{k.arg}' not in {sh.describe()}")
            # positional arity check
            maxpos = sh.max_pos
            n_pos = len(node.args)
            if n_pos and any(isinstance(a, ast.Starred) for a in node.args):
                pass  # *expr unpacking -> arity unknown, skip
            elif maxpos is not None and n_pos > maxpos:
                problems.append(f"{n_pos} positional args > max {maxpos} {sh.describe()}")
            if problems:
                real.append({
                    "file": str(path), "lineno": node.lineno, "callee": name,
                    "callee_def": f"{callee.file}:{callee.lineno}",
                    "callee_shape": sh.describe(), "problems": problems,
                })
    return defs, real, ambiguous, unresolved


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("roots", nargs="+")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--quiet", action="store_true", help="only REAL findings")
    args = ap.parse_args(argv)

    all_real: list[dict] = []
    all_amb: list[dict] = []
    total_defs = 0
    total_unresolved = 0
    for r in args.roots:
        root = Path(r)
        if not root.exists():
            print(f"skip (missing): {r}", file=sys.stderr)
            continue
        defs, real, amb, unres = scan(root)
        total_defs += len(defs)
        total_unresolved += unres
        all_real.extend(real)
        all_amb.extend(amb)

    if args.json:
        print(json.dumps({"real": all_real, "ambiguous": all_amb,
                          "defs": total_defs, "unresolved_calls": total_unresolved},
                         indent=2))
    else:
        print(f"# signature-drift check over {len(args.roots)} root(s): "
              f"{total_defs} defs, {total_unresolved} unresolved calls")
        if all_real:
            print(f"\n## REAL disagreements ({len(all_real)}) -- a call that would raise:")
            for f in all_real:
                print(f"  {f['file']}:{f['lineno']}  {f['callee']}(...)")
                print(f"      callee {f['callee_def']}  {f['callee_shape']}")
                for p in f["problems"]:
                    print(f"      ! {p}")
        else:
            print("\n## REAL disagreements: none")
        if not args.quiet:
            if all_amb:
                print(f"\n## AMBIGUOUS ({len(all_amb)}) -- callee name maps to >1 shape; NOT flagged:")
                for f in all_amb[:40]:
                    print(f"  {f['file']}:{f['lineno']}  {f['callee']}  kwargs={f['kwargs']}")
                    for c in f["candidates"]:
                        print(f"      candidate {c}")
                if len(all_amb) > 40:
                    print(f"  ... and {len(all_amb) - 40} more")
    return 1 if all_real else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

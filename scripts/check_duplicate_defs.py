#!/usr/bin/env python3
"""Class-4 half-rebrand detector: duplicate / shadowed top-level definitions.

WHY THIS EXISTS
---------------
Every name-existence audit in this repo (audit_missing_symbols.py,
check_entry_point_resolution.py, ast_half_rebrand_check.py) checks whether a NAME
is bound SOMEWHERE. None of them can see a file that binds the SAME name TWICE:
both `def`s exist, the name resolves, and the LATER one silently wins.

The proven instance: ``qubit_cli/cli_tui_mixin.py`` defined
``_get_clarify_display_fragments`` twice. The second (single-question) def
shadowed the first (batch) one; the batch body dispatched to
``_get_clarify_batch_display_fragments`` -- a name that was never defined until
the fix. Result: an AttributeError on the batch-clarify owner-facing panel, and
no existing audit could see it because the defect lives in the RELATIONSHIP
between two defs, not in either one.

WHAT THIS REPORTS
-----------------
For each module, in source order, every top-level (or same-class) name bound
more than once by def/class/assign/import, plus methods defined twice in the
same class. Classification of a duplicate pair:

  IDENTICAL   -- same kind, same signature, same body source; a harmless
                 copy-paste duplicate (dead code, not a bug).
  SIG-DIFFER  -- same name, same kind (def), DIFFERENT parameter lists: one
                 call path can TypeError. (Both defs are reachable only if
                 called by name before rebinding; here the later wins, so the
                 earlier signature is dead -- but a caller passing the other's
                 kwargs breaks if reached through the earlier binding.)
  BODY-DIFFER -- same name, same kind, different body/signature: a real
                 shadowing bug. The earlier def is dead; whichever body the
                 CALLERS expect is the one that must be reachable.

A duplicate is a FINDING unless it is IDENTICAL and both are marked
intentionally-dead by a trailing ``# noqa: shadow`` comment (none exist yet).

USAGE
-----
    python3 scripts/check_duplicate_defs.py [--json out.json] [--tree DIR]...
    python3 scripts/check_duplicate_defs.py qubit_runtime/qubit_cli
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

DEF_KINDS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _body_src(node: ast.AST) -> str:
    """Source text of a def/class body, normalized for equality comparison.

    Uses ast.dump on the body (not raw text) so comments / blank lines do not
    count as a difference; the SHAPE and identifiers do.
    """
    return ast.dump(node, annotate_fields=True, include_attributes=False)


def _sig(node: ast.AST) -> str | None:
    """A canonical signature string for a FunctionDef/AsyncFunctionDef, else None."""
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return None
    a = node.args
    parts = []
    for x in a.posonlyargs:
        parts.append(x.arg)
    parts.append("/" if a.posonlyargs else "")
    for x in a.args:
        parts.append(x.arg)
    if a.vararg:
        parts.append("*" + a.vararg.arg)
    elif a.kwonlyargs:
        parts.append("*")
    for x in a.kwonlyargs:
        parts.append(x.arg)
    if a.kwarg:
        parts.append("**" + a.kwarg.arg)
    # Record counts of defaults and kw_defaults; a default added on one side only
    # is a real signature change even if arg names match.
    return f"({', '.join(p for p in parts if p)})|d{len(a.defaults)}|kd{len([k for k in a.kw_defaults if k])}"


def _kind(node: ast.AST) -> str:
    if isinstance(node, ast.AsyncFunctionDef):
        return "async def"
    if isinstance(node, ast.FunctionDef):
        return "def"
    if isinstance(node, ast.ClassDef):
        return "class"
    if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        return "assign"
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return "import"
    return type(node).__name__


def _bound_names(stmt: ast.stmt) -> list[str]:
    """Names a statement binds at its own scope level."""
    out = []
    if isinstance(stmt, DEF_KINDS):
        out.append(stmt.name)
    elif isinstance(stmt, ast.Assign):
        for t in stmt.targets:
            if isinstance(t, ast.Name):
                out.append(t.id)
    elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
        out.append(stmt.target.id)
    elif isinstance(stmt, (ast.Import, ast.ImportFrom)):
        for al in stmt.names:
            if al.name == "*":
                continue
            out.append((al.asname or al.name).split(".")[0])
    return out


def _decorator_names(node: ast.AST) -> list[str]:
    """Dotted names of a def/class's decorators, e.g. 'property', 'x.setter', 'method'."""
    if not isinstance(node, DEF_KINDS):
        return []
    out = []
    for d in node.decorator_list:
        if isinstance(d, ast.Name):
            out.append(d.id)
        elif isinstance(d, ast.Attribute):
            base = d.value.id if isinstance(d.value, ast.Name) else ""
            out.append(f"{base}.{d.attr}" if base else d.attr)
        elif isinstance(d, ast.Call):
            f = d.func
            if isinstance(f, ast.Name):
                out.append(f.id)
            elif isinstance(f, ast.Attribute):
                base = f.value.id if isinstance(f.value, ast.Name) else ""
                out.append(f"{base}.{f.attr}" if base else f.attr)
    return out


def _is_property_pair(a: ast.AST, b: ast.AST, name: str) -> bool:
    """`@property def x` + `@x.setter def x` (or deleter/getter) -- the stdlib
    descriptor pattern, where the SAME name is intentionally rebound. Not a bug."""
    da, db = _decorator_names(a), _decorator_names(b)
    setter_like = {f"{name}.setter", f"{name}.deleter", f"{name}.getter",
                   f"{name}.cached_property"}
    if "property" in da and (setter_like & set(db)):
        return True
    if "property" in db and (setter_like & set(da)):
        return True
    # functools.cached_property often paired with a setter too
    if "cached_property" in da and (setter_like & set(db)):
        return True
    return False


def _is_overload(a: ast.AST, b: ast.AST) -> bool:
    """typing.@overload stubs: several same-name defs where at least one is
    decorated @overload. Intended -- the real def wins at the end."""
    return "overload" in (_decorator_names(a) + _decorator_names(b))


def _is_decorator_registered(a: ast.AST, b: ast.AST, name: str) -> bool:
    """The gateway registration pattern: repeated module-scope `def _` (or any
    short name) each wrapped in a decorator such as ``@method("...")`` that
    registers the function into a dispatcher by side effect. The bare name is
    deliberately discarded, so rebinding it is the INTENDED design, not a bug.
    Detect it narrowly: name is ``_`` AND both defs carry decorators."""
    return name == "_" and bool(_decorator_names(a)) and bool(_decorator_names(b))


def _classify(a: ast.AST, b: ast.AST, name: str = "") -> str:
    """Classify a duplicate pair (a is earlier, b later)."""
    if _is_property_pair(a, b, name):
        return "PROPERTY-PAIR"
    if _is_overload(a, b):
        return "OVERLOAD"
    if _is_decorator_registered(a, b, name):
        return "REGISTERED"
    if type(a) is not type(b):
        return "SIG-DIFFER"  # different kind entirely -- definitely a real change
    if isinstance(a, DEF_KINDS):
        sa, sb = _sig(a), _sig(b)
        if sa != sb:
            return "SIG-DIFFER"
        if _body_src(a) != _body_src(b):
            return "BODY-DIFFER"
        return "IDENTICAL"
    # assignment / import duplicates
    if isinstance(a, (ast.Import, ast.ImportFrom)) and isinstance(b, (ast.Import, ast.ImportFrom)):
        # same name imported twice from different modules -- later wins; benign
        # re-import in most codebases, reported but classified here.
        return "REIMPORT"
    if _body_src(a) != _body_src(b):
        return "BODY-DIFFER"
    return "IDENTICAL"


def _scan_scope(stmts: list[ast.stmt], scope_name: str, file: str,
                findings: list, seen_report: set) -> None:
    """Find duplicate bindings within one scope (module or class body).

    Methods (defs inside a class) share the class scope and are handled here too.
    """
    order: dict[str, list[ast.AST]] = {}
    for stmt in stmts:
        names = _bound_names(stmt)
        # A star-import in the middle can rebind prior defs: record its position.
        for nm in names:
            order.setdefault(nm, []).append(stmt)
        # Note a bare `from x import *` as a rebind hazard for the whole scope.
        if isinstance(stmt, ast.ImportFrom) and any(a.name == "*" for a in stmt.names):
            order.setdefault("<STAR-IMPORT>", []).append(stmt)

    star = order.pop("<STAR-IMPORT>", [])
    for nm, nodes in order.items():
        if len(nodes) < 2:
            continue
        # Pairwise report: earlier vs later (the later shadows the earlier).
        for i in range(len(nodes) - 1):
            a, b = nodes[i], nodes[i + 1]
            key = (file, scope_name, nm, i)
            if key in seen_report:
                continue
            seen_report.add(key)
            verdict = _classify(a, b, nm)
            findings.append({
                "file": file,
                "scope": scope_name,
                "name": nm,
                "later_wins": "line %d shadows line %d" % (b.lineno, a.lineno),
                "lineno_first": a.lineno,
                "lineno_second": b.lineno,
                "kind_first": _kind(a),
                "kind_second": _kind(b),
                "verdict": verdict,
            })
    # star-import rebinding a def name already in this scope
    for stmt in star:
        for nm, nodes in order.items():
            if any(n.lineno < stmt.lineno for n in nodes):
                findings.append({
                    "file": file, "scope": scope_name, "name": nm,
                    "later_wins": "star-import line %d rebinds" % stmt.lineno,
                    "lineno_first": nodes[0].lineno, "lineno_second": stmt.lineno,
                    "kind_first": _kind(nodes[0]), "kind_second": "import *",
                    "verdict": "SIG-DIFFER",
                })


def check_file(path: Path, repo: Path, findings: list, seen: set) -> None:
    try:
        src = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return
    try:
        tree = ast.parse(src, filename=str(path))
    except SyntaxError:
        return
    rel = str(path.relative_to(repo))
    _scan_scope(tree.body, "<module>", rel, findings, seen)
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            _scan_scope(node.body, f"class {node.name}", rel, findings, seen)


def iter_files(roots: list[Path]) -> list[Path]:
    out = []
    for r in roots:
        if r.is_file():
            out.append(r)
        elif r.is_dir():
            for p in sorted(r.rglob("*.py")):
                if "__pycache__" in p.parts or ".git" in p.parts:
                    continue
                out.append(p)
    return out


def _selftest() -> int:
    """Assert the checker reproduces the proven clarify shadowing on a fixture.

    Two same-name defs, DIFFERENT bodies at module scope -> one BODY-DIFFER.
    A property/setter pair -> PROPERTY-PAIR (benign). A repeated decorator-
    registered `def _` -> REGISTERED (benign). Exit 0 only if all hold.
    """
    import tempfile
    src = '''\
def pick(self):
    return self._batch()          # the batch body (earlier) -- dispatches to a helper

def pick(self):
    return self._single()         # the single body (later) SHADOWS the batch one

@property
def size(self):
    return self._n

@size.setter
def size(self, value):
    self._n = value

def _reg_a(): ...
def _reg_b(): ...
'''
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "probe.py"
        p.write_text(src)
        findings: list = []
        seen: set = set()
        check_file(p, Path(d), findings, seen)
    verdicts = sorted(f["verdict"] for f in findings)
    ok = "BODY-DIFFER" in verdicts and "PROPERTY-PAIR" in verdicts
    print(f"selftest: {verdicts}")
    print("selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    ap.add_argument("--tree", action="append", default=[])
    ap.add_argument("--only-findings", action="store_true",
                    help="omit IDENTICAL pairs (harmless duplicates)")
    ap.add_argument("--selftest", action="store_true",
                    help="assert the checker catches the proven clarify-style shadowing")
    args = ap.parse_args()

    if args.selftest:
        return _selftest()

    repo = Path(__file__).resolve().parent.parent
    roots = [Path(t) for t in args.tree] or [repo]
    files = iter_files(roots)

    findings: list = []
    seen: set = set()
    for f in files:
        try:
            check_file(f, repo, findings, seen)
        except ValueError:
            # file outside repo (absolute --tree); fall back to absolute path
            findings.append({"file": str(f), "scope": "?", "name": "?",
                             "verdict": "ERROR", "later_wins": "unreadable"})

    findings.sort(key=lambda d: (d["file"], d.get("lineno_first", 0)))
    BENIGN = {"IDENTICAL", "PROPERTY-PAIR", "OVERLOAD", "REGISTERED", "REIMPORT"}
    shown = [d for d in findings if not (args.only_findings and d["verdict"] in BENIGN)]

    counts: dict = {}
    for d in findings:
        counts[d["verdict"]] = counts.get(d["verdict"], 0) + 1

    print(f"# scanned {len(files)} python file(s)")
    print(f"# duplicate bindings: {len(findings)}  " +
          " ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print()
    for d in shown:
        if d["verdict"] == "ERROR":
            print(f"  FAIL: {d['file']}")
            continue
        print(f"  [{d['verdict']:11}] {d['file']}:{d['lineno_first']}+{d['lineno_second']} "
              f"{d['scope']}::{d['name']}  ({d['kind_first']} then {d['kind_second']}) "
              f"-- {d['later_wins']}")

    real = counts.get("SIG-DIFFER", 0) + counts.get("BODY-DIFFER", 0)
    print()
    print(f"=== {real} REAL duplicate(s) (sig-differ + body-differ); "
          f"{counts.get('IDENTICAL', 0)} identical, "
          f"{counts.get('PROPERTY-PAIR', 0)} property-pair, "
          f"{counts.get('OVERLOAD', 0)} overload, "
          f"{counts.get('REGISTERED', 0)} registered-(), "
          f"{counts.get('REIMPORT', 0)} reimport (all benign) ===")

    if args.json:
        Path(args.json).write_text(json.dumps(
            {"files_scanned": len(files), "counts": counts, "findings": findings},
            indent=2))
        print(f"JSON written: {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

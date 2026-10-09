#!/usr/bin/env python3
"""Class-4 half-rebrand detector: duplicate / shadowed definitions.

WHY THIS EXISTS
---------------
Every name-existence audit in this repo (audit_missing_symbols.py,
check_entry_point_resolution.py, check_half_rebrand.py, ast_half_rebrand_check.py)
checks whether a NAME is bound SOMEWHERE. None of them can see a file that binds
the SAME name TWICE: both `def`s exist, the name resolves, and the LATER one
silently wins. This is the fourth shipped half-rebrand class -- a shadowed
definition -- and it is invisible to all four existing gate checks.

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
                 call path can TypeError. The later wins, so the earlier
                 signature is dead.
  BODY-DIFFER -- same name, same kind, different body/signature: a real
                 shadowing bug.
  PROPERTY-PAIR / OVERLOAD / REGISTERED / REIMPORT -- legitimate, deliberate
                 redefinitions (descriptor setters, typing.@overload stubs, the
                 gateway ``@method(...)`` registration pattern, re-imports).
                 Reported but BENIGN.

A duplicate is a FINDING unless it is classified benign.

USAGE
-----
    python3 scripts/check_duplicate_defs.py [--json out.json] [--tree DIR]...
    python3 scripts/check_duplicate_defs.py --only-findings qubit_runtime
Exit 1 when any REAL (SIG-DIFFER / BODY-DIFFER) duplicate is found.
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve()
REPO = SCRIPT.parent.parent

DEF_KINDS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)

# Verdicts that are NOT defects: deliberate redefinition patterns.
BENIGN = {"IDENTICAL", "PROPERTY-PAIR", "OVERLOAD", "REGISTERED", "REIMPORT"}


def _body_src(node: ast.AST) -> str:
    """AST-dump of a node body; comments / blank lines do not count."""
    return ast.dump(node, annotate_fields=True, include_attributes=False)


def _sig(node: ast.AST) -> str | None:
    """Canonical signature string for a def, else None."""
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
    """Dotted names of a def/class's decorators."""
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
    """`@property def x` + `@x.setter def x`: the stdlib descriptor pattern."""
    da, db = _decorator_names(a), _decorator_names(b)
    setter_like = {f"{name}.setter", f"{name}.deleter", f"{name}.getter",
                   f"{name}.cached_property"}
    if "property" in da and (setter_like & set(db)):
        return True
    if "property" in db and (setter_like & set(da)):
        return True
    if "cached_property" in da and (setter_like & set(db)):
        return True
    return False


def _is_overload(a: ast.AST, b: ast.AST) -> bool:
    """typing.@overload stubs: several same-name defs, one @overload."""
    return "overload" in (_decorator_names(a) + _decorator_names(b))


def _is_decorator_registered(a: ast.AST, b: ast.AST, name: str) -> bool:
    """The gateway registration pattern: repeated module-scope `def _` wrapped
    in a registering decorator; the bare name is deliberately discarded."""
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
        return "SIG-DIFFER"
    if isinstance(a, DEF_KINDS):
        sa, sb = _sig(a), _sig(b)
        if sa != sb:
            return "SIG-DIFFER"
        if _body_src(a) != _body_src(b):
            return "BODY-DIFFER"
        return "IDENTICAL"
    if isinstance(a, (ast.Import, ast.ImportFrom)) and isinstance(b, (ast.Import, ast.ImportFrom)):
        return "REIMPORT"
    if _body_src(a) != _body_src(b):
        return "BODY-DIFFER"
    return "IDENTICAL"


def _scan_scope(stmts: list[ast.stmt], scope_name: str, file: str,
                findings: list, seen_report: set) -> None:
    """Find duplicate bindings within one scope (module or class body)."""
    order: dict[str, list[ast.AST]] = {}
    for stmt in stmts:
        for nm in _bound_names(stmt):
            order.setdefault(nm, []).append(stmt)
        if isinstance(stmt, ast.ImportFrom) and any(a.name == "*" for a in stmt.names):
            order.setdefault("<STAR-IMPORT>", []).append(stmt)

    star = order.pop("<STAR-IMPORT>", [])
    for nm, nodes in order.items():
        if len(nodes) < 2:
            continue
        for i in range(len(nodes) - 1):
            a, b = nodes[i], nodes[i + 1]
            key = (file, scope_name, nm, i)
            if key in seen_report:
                continue
            seen_report.add(key)
            findings.append({
                "file": file,
                "scope": scope_name,
                "name": nm,
                "later_wins": "line %d shadows line %d" % (b.lineno, a.lineno),
                "lineno_first": a.lineno,
                "lineno_second": b.lineno,
                "kind_first": _kind(a),
                "kind_second": _kind(b),
                "verdict": _classify(a, b, nm),
            })
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
    # Normalize to an absolute path first: callers may pass a relative --tree, and
    # Path.relative_to() raises ValueError on a relative path (reported as an
    # ERROR for every file -- the exact bug that made the first draft useless).
    ap = path.resolve()
    try:
        rel = str(ap.relative_to(repo.resolve()))
    except ValueError:
        rel = str(ap)
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


def scan_roots(roots: list[Path]) -> tuple[list[Path], list[dict]]:
    """Return (files_scanned, findings). Findings exclude benign verdicts only
    when the caller filters; this returns ALL findings for transparency."""
    files = iter_files(roots)
    findings: list = []
    seen: set = set()
    for f in files:
        check_file(f, REPO, findings, seen)
    findings.sort(key=lambda d: (d["file"], d.get("lineno_first", 0)))
    return files, findings


def _key(d: dict) -> str:
    """Stable identity of a duplicate finding, for baseline comparison.

    Uses file + name + verdict. Line numbers move with unrelated edits, so they
    are deliberately excluded -- the ratchet must survive churn above the def.
    """
    return f"{d['file']}::{d['scope']}::{d['name']}::{d['verdict']}"


def _self_test() -> int:
    """Prove the detector FIRES on the known-bad shape (not vacuously clean).

    A shadowed method is the shipped class-4 bug; if this self-test ever passes
    by finding nothing, the check is blind and must not be trusted as a gate.
    """
    bad = (
        "class C:\n"
        "    def _get_display_fragments(self):\n"
        "        return self._get_batch_fragments()\n"
        "    def _get_display_fragments(self, state):\n"   # shadows the first
        "        return state\n"
    )
    findings: list = []
    _scan_scope(ast.parse(bad).body[0].body, "class C", "self-test", findings, set())
    hit = [d for d in findings
           if d["name"] == "_get_display_fragments"
           and d["verdict"] in ("SIG-DIFFER", "BODY-DIFFER")]
    if not hit:
        print("SELF-TEST FAILED: a shadowed method was NOT detected")
        return 1
    # And it must NOT flag a legitimate @property + @x.setter pair.
    ok = (
        "class C:\n"
        "    @property\n"
        "    def x(self):\n        return 1\n"
        "    @x.setter\n"
        "    def x(self, v):\n        pass\n"
    )
    findings2: list = []
    _scan_scope(ast.parse(ok).body[0].body, "class C", "self-test", findings2, set())
    if any(d["verdict"] in ("SIG-DIFFER", "BODY-DIFFER") for d in findings2):
        print("SELF-TEST FAILED: a @property/@x.setter pair was flagged as a bug")
        return 1
    print("SELF-TEST OK: shadowed def detected; property/setter pair allowed")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true",
                    help="prove the detector fires on a known shadowed def")
    ap.add_argument("--json")
    ap.add_argument("--tree", action="append", default=[],
                    help="root to scan (repeatable); default: whole repo")
    ap.add_argument("--only-findings", action="store_true",
                    help="omit benign duplicates")
    ap.add_argument("--baseline",
                    help="JSON file of KNOWN real duplicates; only NEW ones fail")
    ap.add_argument("--write-baseline",
                    help="write the current real duplicates as a baseline and exit 0")
    args = ap.parse_args()

    if args.self_test:
        return _self_test()

    roots = [Path(t) for t in args.tree] or [REPO]
    files, findings = scan_roots(roots)

    counts: dict = {}
    for d in findings:
        counts[d["verdict"]] = counts.get(d["verdict"], 0) + 1

    print(f"# scanned {len(files)} python file(s)")
    print(f"# duplicate bindings: {len(findings)}  " +
          " ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print()

    shown = [d for d in findings]
    if args.only_findings:
        shown = [d for d in findings if d["verdict"] not in BENIGN]
    for d in shown:
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

    real_findings = [d for d in findings
                     if d["verdict"] in ("SIG-DIFFER", "BODY-DIFFER")]

    if args.write_baseline:
        Path(args.write_baseline).write_text(json.dumps(
            {"keys": sorted(_key(d) for d in real_findings)}, indent=2))
        print(f"# baseline written: {args.write_baseline} "
              f"({len(real_findings)} known real duplicate(s))")
        return 0

    # Ratchet: when a baseline is supplied, only duplicates NOT in it are
    # failures. An existing known duplicate is reported but does not red the
    # gate; a NEW one (the class this check exists to stop) does.
    failing = real_findings
    known = new = 0
    if args.baseline:
        try:
            base = set(json.loads(Path(args.baseline).read_text()).get("keys", []))
        except (OSError, ValueError) as e:
            print(f"# baseline unreadable ({e}); treating every real duplicate as NEW")
            base = set()
        failing = [d for d in real_findings if _key(d) not in base]
        known = len(real_findings) - len(failing)
        new = len(failing)
        for d in failing:
            print(f"  NEW-DUP: {d['file']}:{d['lineno_first']}+{d['lineno_second']} "
                  f"{d['scope']}::{d['name']} ({d['verdict']}) -- {d['later_wins']}")
        print(f"# ratchet: {known} known (baselined), {new} NEW")

    print(f"# duplicate-def check: {len(failing)} real duplicate(s)"
          + (" new" if args.baseline else ""))

    if args.json:
        Path(args.json).write_text(json.dumps(
            {"files_scanned": len(files), "counts": counts, "findings": findings,
             "new": new if args.baseline else None}, indent=2))
        print(f"JSON written: {args.json}")
    return 1 if failing else 0


if __name__ == "__main__":
    sys.exit(main())

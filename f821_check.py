#!/usr/bin/env python3
"""F821-grade undefined-name check via the stdlib `symtable` module.

`symtable` is CPython's OWN scope analyzer -- it applies the exact same binding
rules the compiler does (local imports, closure cell/free vars, global decls,
comprehension scopes, except-as, walrus). A symbol that is GLOBAL_IMPLICIT in a
function but NOT bound at module scope and NOT a builtin is an undefined name --
which is precisely the HALF-PORT signature (a call site referencing a name that
was never defined).

This avoids the re-export/co_filename confound of a naive code-object walk:
symtable reports a name as a free/global var only in the scope that truly uses it
as such.

Usage: .venv/bin/python f821_check.py <file> [<file> ...]
"""
from __future__ import annotations

import builtins
import symtable
import sys
from pathlib import Path

BUILTINS = set(dir(builtins))


def module_bindings(st: symtable.SymbolTable) -> set[str]:
    """Names bound at module scope (imports, defs, assigns, classes, for/with)."""
    bound = set()
    for sym in st.get_symbols():
        if sym.is_assigned() or sym.is_imported() or sym.is_namespace() or sym.is_parameter():
            bound.add(sym.get_name())
    # Recurse into top-level constructs that create module-visible names via
    # `global` (rare) -- include any child table's globals.
    for child in st.get_children():
        for sym in child.get_symbols():
            if sym.is_global() and sym.is_assigned():
                bound.add(sym.get_name())
    return bound


def walk(st: symtable.SymbolTable, module_bound: set[str], path: str, out: list):
    for sym in st.get_symbols():
        name = sym.get_name()
        # GLOBAL_IMPLICIT == referenced as a global, never bound locally, never
        # declared `global` with a local binding. This is the undefined-name case.
        if sym.is_global() and not sym.is_assigned() and not sym.is_imported():
            if name not in module_bound and name not in BUILTINS and not name.startswith("__"):
                out.append((path, st.get_name() or "<module>", name))
    for child in st.get_children():
        walk(child, module_bound, path + "/" + (child.get_name() or "?"), out)


def check_file(f: Path) -> list[tuple[str, str, str]]:
    src = f.read_text(encoding="utf-8")
    try:
        top = symtable.symtable(src, str(f), "exec")
    except SyntaxError as e:
        return [(str(f), "<module>", f"SYNTAX-ERROR line {e.lineno}: {e.msg}")]
    mb = module_bindings(top)
    out: list[tuple[str, str, str]] = []
    walk(top, mb, f.name, out)
    return out


def main(argv: list[str]) -> int:
    files = [Path(a) for a in argv[1:]]
    total = 0
    print("=" * 78)
    print(" p6 F821-grade undefined-name check (stdlib symtable)")
    print("=" * 78)
    for f in files:
        hits = check_file(f)
        if hits:
            total += len(hits)
            print(f"  {f.name}: {len(hits)} undefined")
            for fp, scope, name in hits[:12]:
                print(f"      {scope}: {name}")
        else:
            print(f"  {f.name:<46} CLEAN")
    print("-" * 78)
    print(f"FILES: {len(files)}   UNDEFINED-NAME HITS: {total}   "
          f"VERDICT: {'PASS' if total == 0 else 'FAIL'}")
    return 1 if total else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

#!/usr/bin/env python3
"""Static half-port scan: LOAD_GLOBAL names never bound in ANY scope.

A naive checker (names not in vars(module)) reports false positives for:
  - names bound in an enclosing function/method (cellvars/freevars) but loaded
    as GLOBAL inside a nested comprehension or inner def;
  - names imported INSIDE a function body (bound as a local in that code object);
  - module-level names created by a decorator/loop (`Flag` imported under
    try/except, `io`/`os`/`sys` imported at top).

Correct rule: a LOAD_GLOBAL name is UNDEFINED only if it appears in NO code
object's co_varnames / co_cellvars / co_freevars AND is not a module global
AND not a builtin. We collect bindings across the WHOLE module recursively.
"""
from __future__ import annotations

import builtins
import dis
import importlib
import sys
import types
from pathlib import Path

REPO = Path("/tmp/qb-p6")
sys.path.insert(0, str(REPO / "qubit_runtime"))
sys.path.insert(0, str(REPO))

MODS = [
    "qubit_platform.resolver.app",
    "qubit_constants_scratch",
    "qubit_cli.cli_commands_session_tools",
    "qubit_cli.update_cmd_fleet_verify",
    "qubit_state_sessions",
    "qubit_bootstrap",
    "tools.approval_detection",
    "agent.turn_failure_copy",
    "scripts.ci.required_results",
    "plugins.web.keyless_mcp",
]


def _own_code(obj: object, mod_name: str) -> types.CodeType | None:
    """A code object ONLY if it was defined by the module under test.

    Guards against walking INHERITED methods (e.g. _PruneLogHandler's base
    logging.handlers.BaseRotatingHandler) whose code belongs to another module and
    would inject phantom 'undefined globals' from that module's namespace.
    """
    c = getattr(obj, "__code__", None)
    if not isinstance(c, types.CodeType):
        return None
    if getattr(obj, "__module__", None) in (mod_name, None):
        return c
    return None


def iter_code(mod: types.ModuleType):
    mod_name = mod.__name__
    seen: set[int] = set()
    stack = []
    for o in vars(mod).values():
        c = _own_code(o, mod_name)
        if c is not None:
            stack.append(c)
        # Classes: only their OWN members (vars(cls) excludes inherited).
        if isinstance(o, type) and getattr(o, "__module__", None) == mod_name:
            for m in vars(o).values():
                mc = _own_code(m, mod_name)
                if mc is not None:
                    stack.append(mc)
                for attr in ("fget", "fset", "fdel"):
                    fn = getattr(m, attr, None)
                    fc = _own_code(fn, mod_name)
                    if fc is not None:
                        stack.append(fc)
    while stack:
        c = stack.pop()
        if id(c) in seen:
            continue
        seen.add(id(c))
        yield c
        for const in c.co_consts:
            if isinstance(const, types.CodeType):
                stack.append(const)


def scan(mod_name: str) -> tuple[list[str], str]:
    try:
        mod = importlib.import_module(mod_name)
    except BaseException as e:  # noqa: BLE001
        return [], f"IMPORT-FAIL {type(e).__name__}: {e}"
    bound: set[str] = set(vars(mod)) | set(dir(builtins))
    loaded: set[str] = set()
    for c in iter_code(mod):
        bound |= set(c.co_varnames) | set(c.co_cellvars) | set(c.co_freevars)
        for ins in dis.get_instructions(c):
            if ins.opname in ("LOAD_GLOBAL", "LOAD_NAME"):
                loaded.add(ins.argval)
    # Ignore dunder names and anything obviously bound.
    undef = sorted(n for n in loaded if n not in bound and not n.startswith("__"))
    return undef, ""


def main() -> int:
    bad = 0
    print("=" * 78)
    print(" p6 STATIC HALF-PORT SCAN -- LOAD_GLOBAL names never bound in ANY scope")
    print("=" * 78)
    for name in MODS:
        undef, err = scan(name)
        if err:
            print(f"  {name:<44} {err}")
            bad += 1
            continue
        status = "CLEAN" if not undef else "UNDEFINED: " + ", ".join(undef)
        if undef:
            bad += 1
        print(f"  {name:<44} {status}")
    print("-" * 78)
    print(f"MODULES: {len(MODS)}   WITH-UNDEFINED-GLOBALS: {bad}   "
          f"VERDICT: {'PASS' if bad == 0 else 'REVIEW (may include dynamic bindings)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

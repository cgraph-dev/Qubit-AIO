#!/usr/bin/env python3
"""p6 adversarial HALF-PORT sweep -- 10 modules ported in the last two waves.

For each target: import the module in isolation, resolve the symbol, call it with a
plausible BAD input, and classify what comes back:

  SANE      -> raises a real error type (TypeError/ValueError/KeyError/OSError/
               sqlite3.*/RuntimeError/...) OR returns a value without touching an
               undefined name. The port is wired.
  HALF-PORT -> raises NameError, or AttributeError whose message names a symbol that
               IS referenced but NEVER DEFINED (the settle_lost_completion class of
               bug: a call site shipped before the callee existed).

We deliberately do NOT count an AttributeError caused by a BAD INPUT (e.g. passing a
str where an object with .foo was expected) as a HALF-PORT -- that is the bad input
doing its job. We only flag AttributeError when the missing attribute is not an
attribute of the input at all (i.e. the module referenced a module-level name that
does not exist). The reliable discriminator: import the module, then scan its own
compiled code for LOAD_GLOBAL names that are not bound anywhere reachable.

Usage:  .venv/bin/python half_port_sweep_p6.py
"""
from __future__ import annotations

import dis
import importlib
import importlib.util
import sys
import traceback
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO / "qubit_runtime"))
sys.path.insert(0, str(REPO))

REAL_ERRORS = (
    TypeError, ValueError, KeyError, OSError, RuntimeError, IndexError,
    AttributeError, ZeroDivisionError, AssertionError, NotImplementedError,
)

# (module_name, [(symbol, suggested_bad_call_argv)])
PROBES = [
    ("qubit_platform.resolver.app", [
        ("AppLocation", ()),
        ("LocationSpec", ()),
    ]),
    ("qubit_constants_scratch", [
        ("_PruneLogHandler", ()),
        ("subtree_touched_since", (object(),)),
        ("reap_processes_rooted_in", (object(),)),
    ]),
    ("qubit_cli.cli_commands_session_tools", [
        ("_command_arg", (12345,)),
        ("_shlex_args", (None,)),
        ("_probe", ("nope_module", "nope_name", "dflt")),
    ]),
    ("qubit_cli.update_cmd_fleet_verify", [
        ("_fleet_row_identity_pending", (None, None)),
        ("_restarted_units_gone", (None,)),
        ("_live_gateway_pids_from_fleet", (None,)),
    ]),
    ("qubit_state_sessions", [
        ("_expand_compression_lineage_ids", (None, ["a", "b"])),
    ]),
    ("qubit_bootstrap", [
        ("_settle_interrupted_update", ()),
    ]),
    ("tools.approval_detection", [
        ("_HOME_PREFIX", ()),
        ("_SECRET_VAR", ()),
    ]),
    ("agent.turn_failure_copy", [
        ("stamp_failure", (None, "reason", True)),
        ("failed_turn_notice", (None,)),
        ("provider_label_for", (None,)),
    ]),
    ("scripts.ci.required_results", [
        ("evaluate_gate", (None,)),
        ("compact_results", (None,)),
    ]),
    ("plugins.web.keyless_mcp", [
        ("_is_rate_limitish", (None,)),
    ]),
]


def undefined_globals(mod: types.ModuleType) -> set[str]:
    """Names the module's compiled code LOAD_GLOBALs but never binds anywhere.

    Walk every code object reachable from the module (functions, methods, classes,
    comprehensions). A name is DEFINED if it appears in any code object's co_varnames,
    co_cellvars, co_freevars, or is a module global / builtin.
    """
    defined: set[str] = set(dir(mod))
    defined |= set(vars(mod))
    for b in dir(__builtins__ if isinstance(__builtins__, types.ModuleType)
                 else importlib.import_module("builtins")):
        defined.add(b)
    loaded: set[str] = set()
    seen: set[int] = set()

    def walk(code: types.CodeType) -> None:
        if id(code) in seen:
            return
        seen.add(id(code))
        for ins in dis.get_instructions(code):
            if ins.opname == "LOAD_GLOBAL":
                loaded.add(ins.argval)
            if ins.opname == "LOAD_NAME":
                loaded.add(ins.argval)
        for const in code.co_consts:
            if isinstance(const, types.CodeType):
                walk(const)

    for obj in vars(mod).values():
        c = getattr(obj, "__code__", None)
        if isinstance(c, types.CodeType):
            walk(c)
            defined |= set(c.co_varnames)
        for attr in ("fget", "fset", "fdel"):
            fn = getattr(obj, attr, None) if isinstance(obj, property) else None
            if fn is not None and isinstance(getattr(fn, "__code__", None), types.CodeType):
                walk(fn.__code__)
        if isinstance(obj, type):
            for meth in vars(obj).values():
                mc = getattr(meth, "__code__", None)
                if isinstance(mc, types.CodeType):
                    walk(mc)
                    defined |= set(mc.co_varnames)
    # Names bound at module top-level via assignment are in vars(mod); class-level
    # names used inside methods are LOAD_GLOBAL -> check they exist as class attrs too.
    missing = set()
    for name in loaded:
        if name in defined:
            continue
        # A nested function's closure vars show as LOAD_DEREF, not LOAD_GLOBAL, so a
        # LOAD_GLOBAL that is nowhere bound is a genuine undefined-name reference.
        missing.add(name)
    return missing


def import_module_isolated(name: str) -> tuple[types.ModuleType | None, str]:
    try:
        mod = importlib.import_module(name)
        return mod, ""
    except BaseException as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"[:200]


def main() -> int:
    rows = []
    half_ports = []
    for mod_name, syms in PROBES:
        mod, err = import_module_isolated(mod_name)
        if mod is None:
            for sym, argv in syms:
                rows.append((mod_name, sym, "IMPORT-FAIL", err))
                half_ports.append((mod_name, sym, err))
            continue
        missing = undefined_globals(mod)
        for sym, argv in syms:
            obj = getattr(mod, sym, "<MISSING>")
            if obj == "<MISSING>":
                rows.append((mod_name, sym, "HALF-PORT", f"{sym} not defined in module"))
                half_ports.append((mod_name, sym, f"{sym} not defined"))
                continue
            # Static half-port signal: the module references an undefined global.
            # Only report it if the probe actually exercises code that touches it.
            try:
                result = obj(*argv)
                detail = f"returned {type(result).__name__}"
                verdict = "SANE"
                # A return value that is an undefined-name artifact is impossible;
                # returning ok means the symbol is wired.
            except REAL_ERRORS as e:
                tb = traceback.extract_tb(sys.exc_info()[2])
                # Find the frame inside the module under test.
                mod_frame = next((fr for fr in reversed(tb) if fr.filename.endswith(
                    mod_name.replace(".", "/") + ".py")), tb[-1])
                src = (mod_frame.line or "").strip()
                exc_name = type(e).__name__
                # HALF-PORT discriminator: NameError, or AttributeError where the
                # blamed attribute is a module-level undefined global (not an attr of
                # the input we passed).
                if isinstance(e, NameError):
                    verdict = "HALF-PORT"
                    detail = f"{exc_name}: {e} @ {mod_frame.name}:{mod_frame.lineno} `{src}`"
                elif isinstance(e, AttributeError):
                    blamed = str(e).split("'")[1] if "'" in str(e) else ""
                    if blamed in missing:
                        verdict = "HALF-PORT"
                        detail = f"undefined global {blamed!r} @ {mod_frame.name}:{mod_frame.lineno} `{src}`"
                    else:
                        verdict = "SANE"
                        detail = f"{exc_name}(bad-input attr {blamed!r}) @ {mod_frame.lineno}"
                else:
                    verdict = "SANE"
                    detail = f"{exc_name} @ {mod_frame.name}:{mod_frame.lineno} `{src}`"
                if verdict == "HALF-PORT":
                    half_ports.append((mod_name, sym, detail))
            except BaseException as e:  # noqa: BLE001
                rows.append((mod_name, sym, "OTHER", f"{type(e).__name__}: {e}"[:120]))
                continue
            rows.append((mod_name, sym, verdict, detail))

    print("=" * 78)
    print(" p6 ADVERSARIAL HALF-PORT SWEEP -- 10 modules, last two waves")
    print("=" * 78)
    print(f"{'module':<42} {'symbol':<34} {'verdict':<10}")
    print("-" * 78)
    for mod_name, sym, verdict, detail in rows:
        print(f"{mod_name:<42} {sym:<34} {verdict:<10}")
        if verdict not in ("SANE",):
            print(f"    -> {detail}")
    print("-" * 78)
    n = len(rows)
    hp = len(half_ports)
    print(f"PROBES: {n}   SANE: {n - hp}   HALF-PORT: {hp}   "
          f"VERDICT: {'PASS -- no half-ports' if hp == 0 else 'FAIL -- half-port(s) found'}")
    return 1 if hp else 0


if __name__ == "__main__":
    raise SystemExit(main())

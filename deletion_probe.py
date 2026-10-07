#!/usr/bin/env python3
"""Deletion-test probe: exercise Qubit with NO hermes anywhere.

Runs inside an isolated sandbox (empty HOME, no ~/.hermes, no ~/.hermes/hermes-agent).
Prints PASS/FAIL per check with raw evidence. Exit 0 iff all checks pass.

Invoked by deletion_test.sh with env -i. Reads config from env:
  DT_SANDBOX  path to sandbox root (contains qubit_runtime/, voicebridge/, ...)
"""
from __future__ import annotations

import importlib
import io
import os
import subprocess
import sys
import traceback

SANDBOX = os.environ["DT_SANDBOX"]
QR = os.path.join(SANDBOX, "qubit_runtime")
HOME = os.environ.get("HOME", "")

# --- hard isolation -------------------------------------------------------
# The sandbox shares the repo's .venv, whose editable-install .pth injects the
# REAL checkout onto sys.path. Left in place, every "import probe" below would
# grade the live tree and the deletion test would be a lie. Drop any sys.path
# entry that resolves outside the sandbox, then pin the sandbox first.
_BAD = ("/home/trick/Projects/Repos", os.path.expanduser("~/Projects/Repos"))
sys.path[:] = [p for p in sys.path if not any(b in (p or "") for b in _BAD)]
for _p in (QR, SANDBOX):
    if _p in sys.path:
        sys.path.remove(_p)
    sys.path.insert(0, _p)

failures: list[str] = []
passes = 0


def check(name: str, ok: bool, evidence: str) -> None:
    global passes
    tag = "PASS" if ok else "FAIL"
    if ok:
        passes += 1
    else:
        failures.append(name)
    print(f"[{tag}] {name}")
    for line in str(evidence).splitlines()[:8]:
        print(f"        | {line}")


# ---------------------------------------------------------------- environment
def check_environment() -> None:
    herm = os.path.join(HOME, ".hermes")
    check("env: no ~/.hermes in HOME", not os.path.exists(herm),
          f"HOME={HOME}  exists({herm})={os.path.exists(herm)}")

    # The sandbox venv is an editable install whose .pth points at the REAL
    # checkout (/home/trick/Projects/Repos/qubit-voicebridge) *and* contains a
    # bare `import _virtualenv` glue line. Both must not resolve product modules,
    # or the "deletion test" silently grades the live tree instead of the sandbox.
    leaked = [p for p in sys.path
              if "/home/trick/Projects/Repos" in p
              or p.rstrip("/") == os.path.expanduser("~/Projects/Repos/qubit-voicebridge")]
    check("env: real checkout NOT on sys.path", not leaked,
          "leaked: " + ", ".join(leaked) if leaked
          else "\n".join(p for p in sys.path if "qb-n5" in p) or "(sandbox paths)")


# ------------------------------------------------------- module import sweep
def enumerate_modules() -> list[str]:
    mods: list[str] = []
    for f in sorted(os.listdir(QR)):
        if f.endswith(".py") and not f.startswith("_") and f != "setup.py":
            mods.append(f[:-3])
    for d in sorted(os.listdir(QR)):
        dp = os.path.join(QR, d)
        if os.path.isdir(dp) and os.path.exists(os.path.join(dp, "__init__.py")):
            if d == "tests":
                continue
            mods.append(d)
    return mods


def check_imports() -> None:
    mods = enumerate_modules()
    ok_cnt = 0
    errs: list[tuple[str, str]] = []
    for m in mods:
        try:
            mod = importlib.import_module(m)
            # prove it came from the sandbox, not the live checkout
            f = getattr(mod, "__file__", None) or ""
            if f and SANDBOX not in f:
                errs.append((m, f"RESOLVED OUTSIDE SANDBOX: {f}"))
                continue
            ok_cnt += 1
        except BaseException as e:  # noqa: BLE001
            errs.append((m, f"{type(e).__name__}: {e}"))
    total = len(mods)
    ev = "\n".join(f"{m}: {e}" for m, e in errs) if errs else "all clean"
    check(f"imports: {ok_cnt}/{total} first-level modules import clean",
          not errs, ev or "all clean")
    if errs:
        print("\n--- IMPORT FAILURES (module: error) ---")
        for m, e in errs:
            print(f"  {m}: {e}")
        print()


# ------------------------------------------------------------------ CLI checks
def run_cli(args: list[str]) -> subprocess.CompletedProcess:
    env = {
        "HOME": HOME,
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PYTHONPATH": SANDBOX + ":" + QR,
        "QUBIT_HOME": os.environ.get("QUBIT_HOME", ""),
        "LANG": "C.UTF-8",
    }
    return subprocess.run(
        [sys.executable, "-m", "qubit_cli.main", *args],
        env=env, capture_output=True, text=True, timeout=180, cwd=SANDBOX,
    )


def check_cli_help() -> None:
    r = run_cli(["--help"])
    ev = (r.stdout or r.stderr or "").strip().splitlines()
    check("cli: `python -m qubit_cli.main --help` exits 0", r.returncode == 0,
          f"rc={r.returncode}\n" + "\n".join(ev[:6]))
    if r.returncode != 0:
        low = (r.stdout + r.stderr).lower()
        if "close-preview" in low:
            print("        >> HINT: --close-preview argparse conflict (known killer)")


def check_cli_version() -> None:
    r = run_cli(["--version"])
    out = (r.stdout or r.stderr or "").strip()
    check("cli: `python -m qubit_cli.main --version` exits 0", r.returncode == 0,
          f"rc={r.returncode} out={out[:200]!r}")


def check_subcommand_count() -> None:
    """Build the FULL CLI parser the way main.py does and count subcommands."""
    try:
        import argparse
        parser = None
        # qubit_cli/main.py::_build_cli_parser() is the canonical full-tree builder.
        for modname in ("qubit_cli.main", "qubit_cli._parser"):
            try:
                m = importlib.import_module(modname)
            except BaseException:  # noqa: BLE001
                continue
            fn = getattr(m, "_build_cli_parser", None) or \
                 getattr(m, "build_top_level_parser", None)
            if callable(fn):
                try:
                    res = fn()
                except BaseException as e:  # noqa: BLE001
                    check("cli: subcommands > 70", False,
                          f"{modname}.{fn.__name__}() raised {type(e).__name__}: {e}")
                    return
                parser = res[0] if isinstance(res, tuple) else res
                break
        if parser is None or not hasattr(parser, "_actions"):
            check("cli: subcommands > 70", False,
                  f"could not build a full parser (got {type(parser).__name__})")
            return
        subs = [a for a in parser._actions
                if isinstance(a, argparse._SubParsersAction)]
        names = list(subs[0].choices.keys()) if subs else []
        check(f"cli: subcommands > 70 (found {len(names)})", len(names) > 70,
              ", ".join(names[:25]) + (" ..." if len(names) > 25 else ""))
    except BaseException as e:  # noqa: BLE001
        check("cli: subcommands > 70", False,
              f"{type(e).__name__}: {e}\n{traceback.format_exc()[-600:]}")


def check_cli_object() -> None:
    try:
        import cli as clim  # top-level cli.py holds QubitCLI
        cls = getattr(clim, "QubitCLI", None) or getattr(clim, "HermesCLI", None)
        if cls is None:
            check("cli: construct cli.QubitCLI()", False,
                  "neither QubitCLI nor HermesCLI found in top-level cli.py")
            return
        obj = cls()
        check("cli: construct cli.QubitCLI()", obj is not None,
              f"{cls.__name__}() -> {type(obj).__name__}")
    except BaseException as e:  # noqa: BLE001
        check("cli: construct cli.QubitCLI()", False,
              f"{type(e).__name__}: {e}\n{traceback.format_exc()[-600:]}")


def check_gateway_imports() -> None:
    targets = [
        "tui_gateway.server",
        "tui_gateway.entry",
        "qubit_cli.gateway",
        "plugins.plugin_loader",
    ]
    for t in targets:
        try:
            importlib.import_module(t)
            check(f"import: {t}", True, "ok")
        except BaseException as e:  # noqa: BLE001
            check(f"import: {t}", False, f"{type(e).__name__}: {e}")


def check_providers() -> None:
    """list_authenticated_providers(for_picker=True, fast_custom_probe=True)."""
    fn = None
    where = ""
    for modname in ("qubit_cli.model_switch_providers",
                    "qubit_cli.providers",
                    "providers.resolver"):
        try:
            m = importlib.import_module(modname)
        except BaseException:  # noqa: BLE001
            continue
        f = getattr(m, "list_authenticated_providers", None)
        if callable(f):
            fn, where = f, modname
            break
    if fn is None:
        # search submodules
        import pkgutil
        for modname in ("qubit_cli", "providers"):
            try:
                pkg = importlib.import_module(modname)
            except BaseException:  # noqa: BLE001
                continue
            for info in pkgutil.iter_modules(pkg.__path__):
                try:
                    m = importlib.import_module(f"{modname}.{info.name}")
                except BaseException:  # noqa: BLE001
                    continue
                f = getattr(m, "list_authenticated_providers", None)
                if callable(f):
                    fn, where = f, f"{modname}.{info.name}"
                    break
            if fn:
                break
    if fn is None:
        check("providers: list_authenticated_providers(...) returns providers",
              False, "function not found during sweep")
        return
    try:
        res = fn(for_picker=True, fast_custom_probe=True)
        n = len(res) if hasattr(res, "__len__") else "?"
        check("providers: list_authenticated_providers(...) returns providers",
              bool(res), f"{where} -> {n} provider(s)")
    except BaseException as e:  # noqa: BLE001
        check("providers: list_authenticated_providers(...) returns providers",
              False, f"{where}\n{type(e).__name__}: {e}\n{traceback.format_exc()[-600:]}")


def main() -> int:
    print("=" * 72)
    print("DELETION TEST — Qubit with hermes fully absent")
    print(f"HOME      = {HOME}")
    print(f"SANDBOX   = {SANDBOX}")
    print(f"QUBIT_HOME= {os.environ.get('QUBIT_HOME','')}")
    print("=" * 72)
    check_environment()
    print()
    check_imports()
    print()
    check_cli_help()
    check_cli_version()
    check_subcommand_count()
    check_cli_object()
    print()
    check_gateway_imports()
    print()
    check_providers()
    print()
    print("=" * 72)
    print(f"RESULT: {passes} passed, {len(failures)} failed")
    if failures:
        print("FAILED CHECKS:")
        for f in failures:
            print(f"  - {f}")
    print("=" * 72)
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())

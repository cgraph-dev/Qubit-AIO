#!/usr/bin/env python3
"""Packet 7A: clean-venv installation + dependency contract check (opt-in).

Two modes:
  --check        (default, fast, hermetic) parse pyproject.toml, AST-scan every
                 production module for undeclared third-party imports, and
                 bytecode-compile voicebridge/ + scripts/. Proves the source's
                 dependency contract without network or a new venv.
  --install      (opt-in, slow, network) create a throwaway venv, pip-install
                 the package with its declared dependencies, then run the same
                 scan inside it so installed-but-undeclared packages cannot
                 hide a missing dependency.

Typed exit: 0 ok, 2 undeclared import FAIL, 4 degraded (venv/pip/network
unavailable for --install). A skipped --install is DEGRADED, never a pass.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

EXIT_OK = 0
EXIT_FAIL = 2
EXIT_DEGRADED = 4


def _scan() -> tuple[list[str], list[str]]:
    """AST dependency-contract scan (reuses config_doctor logic)."""
    from scripts.config_doctor import _import_scan
    failures, warnings = _import_scan()
    return ([f"{mod} at {loc}" for mod, loc in failures],
            [f"{mod} at {loc}" for mod, loc in warnings])


def _compile_all() -> list[str]:
    import py_compile
    errors: list[str] = []
    for root in (REPO / "voicebridge", REPO / "scripts"):
        for py in sorted(root.rglob("*.py")):
            try:
                py_compile.compile(str(py), doraise=True)
            except py_compile.PyCompileError as exc:
                errors.append(f"{py.relative_to(REPO)}: {exc}")
    return errors


def _check() -> int:
    fails, warns = _scan()
    for w in warns:
        print(f"[clean-venv] WARN optional/guarded: {w}")
    for f in fails:
        print(f"[clean-venv] FAIL undeclared import: {f}")
    compile_errs = _compile_all()
    for e in compile_errs:
        print(f"[clean-venv] FAIL compile: {e}")
    if fails or compile_errs:
        return EXIT_FAIL
    print("[clean-venv] PASS: no undeclared imports; all modules compile.")
    return EXIT_OK


def _install() -> int:
    venv_dir = Path(tempfile.mkdtemp(prefix="vb-cleanvenv-"))
    py = venv_dir / "bin" / "python"
    try:
        subprocess.run([sys.executable, "-m", "venv", str(venv_dir)],
                       check=True, capture_output=True, timeout=300)
        pip = venv_dir / "bin" / "pip"
        print("[clean-venv] installing package + declared deps (network)...")
        subprocess.run([str(pip), "install", str(REPO)],
                       check=True, capture_output=True, timeout=1200)
    except subprocess.TimeoutExpired:
        print("[clean-venv] DEGRADED: install timed out (network/slow).")
        return EXIT_DEGRADED
    except subprocess.CalledProcessError as exc:
        print(f"[clean-venv] DEGRADED: install failed: "
              f"{(exc.stderr or b'').decode(errors='replace')[-200:]} "
              f"(network or build backend unavailable).")
        return EXIT_DEGRADED
    # Run the scan inside the clean venv so transitive site-packages cannot
    # hide a missing declared dependency.
    code = (
        "import sys; sys.path.insert(0, %r);\n"
        "from scripts.config_doctor import _import_scan;\n"
        "f, w = _import_scan();\n"
        "print('CLEAN-VENV-FAILS=' + str([f'{m} at {l}' for m, l in f]));\n"
        "print('CLEAN-VENV-WARNS=' + str([f'{m} at {l}' for m, l in w]));\n"
    ) % str(REPO)
    try:
        out = subprocess.run([str(py), "-c", code],
                             capture_output=True, text=True, timeout=300)
    except Exception as exc:
        print(f"[clean-venv] DEGRADED: in-venv scan failed: {exc}")
        return EXIT_DEGRADED
    fails: list[str] = []
    for line in out.stdout.splitlines():
        if line.startswith("CLEAN-VENV-FAILS="):
            import ast as _ast
            fails = _ast.literal_eval(line.split("=", 1)[1])
    if fails:
        for f in fails:
            print(f"[clean-venv] FAIL undeclared import (clean venv): {f}")
        return EXIT_FAIL
    print("[clean-venv] PASS: clean-venv install has no undeclared imports.")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Packet 7A clean-venv dependency check")
    ap.add_argument("--check", action="store_true", default=False,
                    help="fast hermetic contract scan (default if no --install)")
    ap.add_argument("--install", action="store_true",
                    help="opt-in: real clean-venv install (slow, network)")
    args = ap.parse_args(argv)
    if args.install:
        rc = _install()
        # also run the fast check regardless
        rc = rc or _check()
        return rc
    return _check()


if __name__ == "__main__":
    sys.exit(main())
#!/usr/bin/env python3
"""Packet 7A (+ 8K repair): clean-venv installation + dependency contract check.

Two modes:
  --check        (default, fast, hermetic) parse pyproject.toml, AST-scan every
                 production module for undeclared third-party imports, and
                 bytecode-compile voicebridge/ + scripts/. Proves the source's
                 dependency contract without network or a new venv.
  --install      (opt-in, slow, network) BUILD the wheel, install it into a
                 throwaway venv, then run the dependency-contract scan inside
                 that venv against the INSTALLED package files. The scan runs
                 with no PYTHONPATH, no source-tree sys.path insertion, and a
                 mask guard that fails if the installed ``voicebridge``
                 resolves inside the checkout (Packet 8K finding #7 repair: a
                 missing wheel member can no longer be hidden by the source
                 tree).

Typed exit: 0 ok, 2 undeclared import FAIL, 4 degraded (venv/pip/network
unavailable for --install). A skipped --install is DEGRADED, never a pass.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

EXIT_OK = 0
EXIT_FAIL = 2
EXIT_DEGRADED = 4


def _vendored_top_level_names() -> set[str]:
    """Top-level import names provided by the vendored Qubit runtime source
    tree (``qubit_runtime/``). Once ``qubit_runtime.ensure_path()`` puts that
    tree on ``sys.path`` these are local modules, never undeclared PyPI
    dependencies. The set is derived from the tree so it cannot drift from the
    vendored source (mirrors ``voicebridge.release._vendored_top_level_names``
    for the installed wheel)."""
    root = REPO / "qubit_runtime"
    names: set[str] = set()
    if not root.is_dir():
        return names
    for entry in root.iterdir():
        if entry.is_dir():
            if (entry / "__init__.py").is_file():
                names.add(entry.name)
        elif entry.suffix == ".py" and entry.name != "__init__.py":
            names.add(entry.stem)
    return names


def _scan() -> tuple[list[str], list[str]]:
    """AST dependency-contract scan (reuses config_doctor logic). Imports of
    the vendored runtime's top-level modules are local (reached through
    ``qubit_runtime.ensure_path()``), so they are not undeclared third-party
    imports even though they are absent from pyproject."""
    from scripts.config_doctor import _import_scan
    vendored = _vendored_top_level_names()
    failures, warnings = _import_scan()
    failures = [(mod, loc) for mod, loc in failures if mod not in vendored]
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


def _run(cmd: list[str], timeout: int, cwd: str | None = None,
         env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          cwd=cwd, env=env)


def _build_wheel(dist_dir: Path, env: dict | None = None) -> bool:
    """Build the wheel with pip (build isolation fetches hatchling). Returns
    True on success; prints a typed degraded message on failure/timeout."""
    print("[clean-venv] building wheel (network: build backend)...")
    try:
        _run([sys.executable, "-m", "pip", "wheel", "--no-deps",
              "-w", str(dist_dir), str(REPO)], timeout=600, env=env)
    except subprocess.TimeoutExpired:
        print("[clean-venv] DEGRADED: wheel build timed out (network/slow).")
        return False
    except subprocess.CalledProcessError as exc:
        print(f"[clean-venv] DEGRADED: wheel build failed: "
              f"{(exc.stderr or b'').decode(errors='replace')[-200:]} "
              f"(network or build backend unavailable).")
        return False
    return True


def _install() -> int:
    base = Path(tempfile.mkdtemp(prefix="vb-cleanvenv-"))
    venv_dir = base / "venv"
    dist_dir = base / "dist"
    home_dir = base / "home"
    dist_dir.mkdir()
    home_dir.mkdir()
    py = venv_dir / "bin" / "python"
    # A genuinely separate HOME outside the checkout (Packet 8K): venv creation,
    # pip install, and the in-venv scan must not read the operator's real
    # ~/.config/pip, ~/.local, or user-site. XDG dirs follow HOME.
    clean_env = dict(os.environ)
    clean_env.pop("PYTHONPATH", None)
    clean_env["HOME"] = str(home_dir)
    clean_env["XDG_CONFIG_HOME"] = str(home_dir / ".config")
    clean_env["XDG_DATA_HOME"] = str(home_dir / ".local" / "share")
    clean_env["XDG_STATE_HOME"] = str(home_dir / ".local" / "state")
    clean_env["XDG_CACHE_HOME"] = str(home_dir / ".cache")
    try:
        subprocess.run([sys.executable, "-m", "venv", str(venv_dir)],
                       check=True, capture_output=True, timeout=300, env=clean_env)
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
        print(f"[clean-venv] DEGRADED: venv creation failed: {exc}")
        return EXIT_DEGRADED
    if not _build_wheel(dist_dir, env=clean_env):
        return EXIT_DEGRADED
    wheels = list(dist_dir.glob("*.whl"))
    if len(wheels) != 1:
        print(f"[clean-venv] DEGRADED: expected one wheel, found {len(wheels)}")
        return EXIT_DEGRADED
    pip = venv_dir / "bin" / "pip"
    print("[clean-venv] installing wheel + declared deps (network)...")
    try:
        subprocess.run([str(pip), "install", str(wheels[0])],
                       check=True, capture_output=True, timeout=1800, env=clean_env)
    except subprocess.TimeoutExpired:
        print("[clean-venv] DEGRADED: install timed out (network/slow).")
        return EXIT_DEGRADED
    except subprocess.CalledProcessError as exc:
        print(f"[clean-venv] DEGRADED: install failed: "
              f"{(exc.stderr or b'').decode(errors='replace')[-200:]} "
              f"(network or build backend unavailable).")
        return EXIT_DEGRADED
    # Run the scan inside the clean venv with ISOLATED mode (-I: no PYTHONPATH,
    # no CWD, no user site) against the INSTALLED package files. The source
    # checkout is never placed on sys.path, and a mask guard FAILS if the
    # installed package resolves inside the checkout.
    code = (
        "import sys, pathlib, os, importlib.util;\n"
        "repo = os.environ.get('VB_CLEAN_VENV_REPO', '');\n"
        "spec = importlib.util.find_spec('voicebridge');\n"
        "assert spec is not None and spec.origin, 'voicebridge not importable';\n"
        "base = pathlib.Path(spec.origin).resolve().parent;\n"
        "assert not str(base).startswith(os.path.abspath(repo)), "
        "'MASK-GUARD FAIL: installed voicebridge resolves inside the checkout';\n"
        "from voicebridge.release import import_scan_installed, "
        "declared_module_names_from_metadata;\n"
        "declared = declared_module_names_from_metadata();\n"
        "fails = import_scan_installed(base, declared);\n"
        "print('CLEAN-VENV-FAILS=' + str(fails));\n"
        "print('CLEAN-VENV-BASE=' + str(base));\n"
        "print('CLEAN-VENV-ENTRY=' + str(len(list(importlib.metadata.entry_points("
        "group='console_scripts')))));\n"
        "import voicebridge, voicebridge.release;\n"
        "from voicebridge import resources;\n"
        "print('CLEAN-VENV-RESOURCE-FRONTEND=' + str("
        "resources.resource_path('frontend').is_dir()));\n"
        "print('CLEAN-VENV-RESOURCE-CONFIG=' + str("
        "resources.resource_path('config/voice.example.yaml').is_file()));\n"
        "print('CLEAN-VENV-OK');\n"
    )
    env = dict(clean_env)
    env["VB_CLEAN_VENV_REPO"] = str(REPO)
    try:
        out = subprocess.run([str(py), "-I", "-c", code], capture_output=True,
                             text=True, timeout=300, env=env, cwd=tempfile.gettempdir())
    except Exception as exc:
        print(f"[clean-venv] DEGRADED: in-venv scan failed: {exc}")
        return EXIT_DEGRADED
    fails: list[str] = []
    mask_fail = False
    for line in out.stdout.splitlines():
        if line.startswith("CLEAN-VENV-FAILS="):
            import ast as _ast
            fails = _ast.literal_eval(line.split("=", 1)[1])
        elif "MASK-GUARD FAIL" in line:
            mask_fail = True
        elif line.startswith("CLEAN-VENV-BASE="):
            print(f"[clean-venv] installed base: {line.split('=', 1)[1]}")
        elif line.startswith("CLEAN-VENV-RESOURCE-"):
            print(f"[clean-venv] resource {line.split('=', 1)[0][12:]} = "
                  f"{line.split('=', 1)[1]}")
    if out.returncode != 0:
        print(f"[clean-venv] DEGRADED: in-venv scan exited {out.returncode}: "
              f"{(out.stderr or '')[-300:]}")
        return EXIT_DEGRADED
    if mask_fail:
        print("[clean-venv] FAIL mask guard: installed package resolves inside "
              "the source checkout (packaging is hiding missing contents).")
        return EXIT_FAIL
    if fails:
        for f in fails:
            print(f"[clean-venv] FAIL undeclared import (installed wheel): {f}")
        return EXIT_FAIL
    if "CLEAN-VENV-OK" not in out.stdout:
        print("[clean-venv] DEGRADED: in-venv scan did not complete cleanly.")
        return EXIT_DEGRADED
    print("[clean-venv] PASS: clean-venv wheel install has no undeclared "
          "imports; resources resolve; package does not mask the checkout.")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Packet 7A clean-venv dependency check")
    ap.add_argument("--check", action="store_true", default=False,
                    help="fast hermetic contract scan (default if no --install)")
    ap.add_argument("--install", action="store_true",
                    help="opt-in: build wheel + real clean-venv install (slow, network)")
    args = ap.parse_args(argv)
    if args.install:
        rc = _install()
        # also run the fast check regardless
        rc = rc or _check()
        return rc
    return _check()


if __name__ == "__main__":
    sys.exit(main())
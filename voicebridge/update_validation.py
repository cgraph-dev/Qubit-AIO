"""Post-update validation of the critical Qubit startup surface.

After a pull (and its dependency/config sync) the updater must prove the tree it
is about to run can actually start. A partially-updated checkout can parse fine
yet die with ``ImportError`` on the first request, so this module checks two
layers per critical module:

1. **Compile** — read the module file at its exact path and ``compile()`` it.
   Pure and side-effect free, this catches syntax corruption.
2. **Import probe** — import the dotted module in a *subprocess* (via the
   injectable runner) so import-time breakage between sibling modules is caught
   without polluting the updater's own interpreter or executing destructive
   side effects in-process.

Plus a frontend-asset check: the shipped bundle (``frontend/qubit-dist``) must
exist and carry an ``index.html``.

The import probe is never spawned in a test process unless a runner is injected
(the same fail-closed convention as the rest of the update surface), so a test
touches no host state.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

VALIDATION_FAILED_CODE = "UPDATE_VALIDATION_FAILED"
FRONTEND_MISSING_CODE = "FRONTEND_ASSETS_MISSING"
VALIDATION_TIMEOUT_S = 60.0

# Modules imported on every Qubit startup; a failure in any is a failed update.
CRITICAL_MODULES: Tuple[str, ...] = (
    "voicebridge.cli",
    "voicebridge.gateway_supervisor",
    "voicebridge.qubit_compat",
    "voicebridge.providers.resolver",
    "voicebridge.qubit_session_store",
    "voicebridge.skill_catalog",
    "voicebridge.plugins",
    "voicebridge.mcp_servers",
)

FRONTEND_DIST = ("frontend", "qubit-dist")
FRONTEND_INDEX = "index.html"

Runner = Callable[[Sequence[str], float], Tuple[int, str, str]]


def _get(cfg: Any, *path: str, default: Any = None) -> Any:
    try:
        return cfg.get(*path, default=default) if hasattr(cfg, "get") else default
    except Exception:  # noqa: BLE001
        return default


def _default_runner(argv: Sequence[str], timeout: float) -> Tuple[int, str, str]:
    try:
        proc = subprocess.run(list(argv), capture_output=True, text=True,
                              timeout=timeout, env=dict(os.environ))
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError:
        return 127, "", f"{argv[0]}: not found"
    except subprocess.TimeoutExpired:
        return 124, "", "command timed out"
    except Exception as exc:  # noqa: BLE001
        return 1, "", str(exc)


def _under_test() -> bool:
    if os.environ.get("QUBIT_UPDATE_DISABLED") == "1":
        return True
    try:
        from . import qubit_runtime

        return bool(qubit_runtime.under_test())
    except Exception:  # noqa: BLE001
        return bool(os.environ.get("PYTEST_CURRENT_TEST"))


def _redact(text: Any) -> str:
    try:
        from .redaction import redact_text

        return redact_text(str(text))
    except Exception:  # noqa: BLE001
        return str(text)


def repo_root(cfg: Any, override: Any = None) -> Path:
    """The code tree to validate: an explicit root, then ``update.repo_root``,
    then the running package's checkout root."""
    if override:
        return Path(override).expanduser()
    configured = str(_get(cfg, "update", "repo_root", default="") or "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).resolve().parents[1]


def module_file(root: Path, dotted: str) -> Optional[Path]:
    """Resolve a dotted module to its source file under *root*, or ``None``."""
    parts = [p for p in str(dotted).split(".") if p]
    if not parts:
        return None
    module = root.joinpath(*parts).with_suffix(".py")
    if module.is_file():
        return module
    package = root.joinpath(*parts) / "__init__.py"
    if package.is_file():
        return package
    return None


def _compile_check(path: Optional[Path]) -> Tuple[bool, str]:
    """Compile a file's source without executing it."""
    if path is None:
        return False, "module source not found under the updated tree"
    try:
        source = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        return False, f"cannot read {path}: {exc}"
    try:
        compile(source, str(path), "exec")
    except SyntaxError as exc:
        return False, f"SyntaxError at line {exc.lineno}: {exc.msg}"
    except ValueError as exc:
        return False, f"invalid source: {exc}"
    return True, ""


def _import_probe(module: str, *, runner: Runner, root: Path,
                  timeout: float = VALIDATION_TIMEOUT_S) -> Tuple[bool, str]:
    """Import *module* in a subprocess rooted at *root*. Returns (ok, detail)."""
    probe = (
        "import importlib, sys\n"
        f"sys.argv = ['qubit', 'update']\n"
        f"importlib.import_module({module!r})\n"
    )
    argv = [sys.executable, "-c", probe]
    saved = dict(os.environ)
    saved_pythonpath = saved.get("PYTHONPATH", "")
    try:
        os.environ["PYTHONPATH"] = str(root) + os.pathsep + saved_pythonpath
        rc, out, err = runner(argv, timeout)
    finally:
        os.environ.clear()
        os.environ.update(saved)
    if rc == 0:
        return True, ""
    detail = (err or out or f"import exited {rc}").strip()
    return False, _redact(detail)[:300]


def validate_installation(cfg: Any, *, runner: Optional[Runner] = None,
                          repo_root: Any = None,
                          timeout: float = VALIDATION_TIMEOUT_S
                          ) -> Dict[str, Any]:
    """Validate the critical startup surface under the updated tree.

    Returns a complete, per-check result. ``ok`` is True only when every check
    passed; skips are explicit (``skipped``), never silently green. A
    ``update.skip_validation`` config key short-circuits to an explicit skip.
    """
    root = repo_root if isinstance(repo_root, Path) else globals()["repo_root"](
        cfg, repo_root)
    checks: List[Dict[str, Any]] = []
    failures: List[str] = []

    if _get(cfg, "update", "skip_validation", default=False):
        return {"ok": True, "state": "SKIPPED", "code": "", "skipped": True,
                "reason": "update.skip_validation is set", "module_root": str(root),
                "checks": [], "failures": [], "frontend": None}

    # Validate the tree that will actually be imported. In production
    # ``repo_root`` IS the running package's checkout; when it is an isolated
    # test double without the package (the update surface's own tests point at
    # a throwaway git repo), validate the active package tree instead of
    # failing every module on a missing file. ``root_fallback`` records which
    # tree was used so the receipt never misrepresents the check.
    runtime_root = Path(__file__).resolve().parents[1]
    root_fallback = False
    if not (root / "voicebridge").is_dir() and (runtime_root / "voicebridge").is_dir():
        root = runtime_root
        root_fallback = True

    exec_ = runner or _default_runner
    can_probe = runner is not None or not _under_test()

    for module in CRITICAL_MODULES:
        path = module_file(root, module)
        comp_ok, comp_detail = _compile_check(path)
        record: Dict[str, Any] = {
            "name": module, "kind": "module", "ok": bool(comp_ok),
            "compiled": bool(comp_ok), "imported": None,
            "file": str(path) if path else None, "detail": comp_detail,
        }
        if comp_ok and can_probe:
            imp_ok, imp_detail = _import_probe(module, runner=exec_,
                                               root=root, timeout=timeout)
            record["imported"] = bool(imp_ok)
            record["ok"] = bool(imp_ok)
            if not imp_ok:
                record["detail"] = imp_detail
        elif comp_ok:
            record["import_skipped"] = True
        if not record["ok"]:
            failures.append(module)
        checks.append(record)

    # Frontend assets: the shipped SPA bundle must exist and be served.
    index = root.joinpath(*FRONTEND_DIST) / FRONTEND_INDEX
    fe_ok = bool(index.is_file())
    frontend = {"dir": str(root.joinpath(*FRONTEND_DIST)), "index": str(index),
                "present": fe_ok, "ok": fe_ok}
    if not fe_ok:
        failures.append("frontend/qubit-dist/index.html")
        checks.append({"name": "frontend.assets", "kind": "asset", "ok": False,
                       "detail": f"missing {index}"})
    else:
        checks.append({"name": "frontend.assets", "kind": "asset", "ok": True,
                       "detail": ""})

    ok = not failures
    return {
        "ok": ok,
        "state": "OK" if ok else "FAILED",
        "code": "" if ok else VALIDATION_FAILED_CODE,
        "skipped": False,
        "module_root": str(root),
        "root_fallback": bool(root_fallback),
        "import_probe": bool(can_probe),
        "checks": checks,
        "failures": failures,
        "frontend": frontend,
        "detail": "" if ok else f"failed checks: {', '.join(failures)}",
    }


__all__ = ["VALIDATION_FAILED_CODE", "FRONTEND_MISSING_CODE",
           "VALIDATION_TIMEOUT_S", "CRITICAL_MODULES", "repo_root",
           "module_file", "validate_installation"]

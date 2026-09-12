"""Qubit-owned Hermes runtime bootstrap.

Per `docs/QUBIT_OWNER_AMENDMENT_2026-09-11_HERMES_RUNTIME.md`, the product's
agent loop, CLI, dashboard, sessions, tools, skills, models, memory, tasks and
runtime execute from the vendored fork under ``qubit_hermes/`` (a byte-identical
fork of the pinned upstream `2237be35`, MIT, Copyright (c) 2025 Nous Research).
No external Hermes installation is imported or supervised.

This module is the single entry point that puts the fork on ``sys.path`` and
invokes its CLI. Qubit's own launcher and dashboard call through here so the
same runtime backs CLI, dashboard and voice.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

_FORK_NAME = "qubit_hermes"
_REPO_ROOT = Path(__file__).resolve().parents[1]


def runtime_root() -> Path:
    """Resolve the vendored fork root (installed package data first, then the
    source checkout). Never raises; callers decide how to degrade."""
    try:
        from . import resources as _res
        pkg = _res.package_data_root() / _FORK_NAME
        if pkg.is_dir():
            return pkg
        return _res.resource_path(_FORK_NAME)
    except Exception:
        return _REPO_ROOT / _FORK_NAME


def manifest_path() -> Path:
    """Per-file provenance manifest for the fork (checkout path)."""
    return _REPO_ROOT / "docs" / "QUBIT_HERMES_VENDOR_MANIFEST.json"


def _cache_repo_tests_namespace() -> None:
    """Cache the Qubit checkout's namespace ``tests`` package.

    The fork ships a *regular* ``tests/__init__.py`` package; the Qubit
    checkout's ``tests/`` has no ``__init__.py`` (a PEP 420 namespace package).
    A regular package wins over a namespace portion regardless of ``sys.path``
    order, so without this the fork's ``tests`` would shadow ``tests.<module>``
    imports (e.g. ``from tests.test_webui_reports import _start``). Cache the
    Qubit namespace before the fork root becomes importable.
    """
    import types
    if "tests" in sys.modules:
        return
    repo_tests = _REPO_ROOT / "tests"
    if not repo_tests.is_dir():
        return
    mod = types.ModuleType("tests")
    mod.__path__ = [str(repo_tests)]  # type: ignore[attr-defined]
    mod.__package__ = "tests"
    sys.modules["tests"] = mod


def ensure_path() -> Path:
    """Idempotently put the fork root on ``sys.path`` and return it.

    The fork's modules are top-level (``hermes_cli``, ``agent``, ``tools``,
    ``hermes_state`` …), so the fork root itself is the import root. The fork
    root also contains top-level ``docs/``, ``scripts/``, ``skills/`` and
    ``tests/`` directories that collide with the Qubit checkout; the Qubit repo
    root stays *ahead* of the fork root so Qubit's own packages win, and the
    Qubit ``tests`` namespace is cached first (the fork ships a regular package
    of the same name).
    NOTE: callers that only probe or run one command should use
    :func:`run`/:func:`status`, which restore ``sys.path`` afterwards.
    """
    root = runtime_root()
    s = str(root)
    if s in sys.path:
        sys.path.remove(s)
    repo = str(_REPO_ROOT)
    if repo in sys.path:
        sys.path.insert(sys.path.index(repo) + 1, s)
    else:
        sys.path.insert(0, s)
    _cache_repo_tests_namespace()
    return root


def run(argv: Optional[list] = None) -> int:
    """Run the vendored Hermes CLI in-process with ``argv``.

    ``argv`` is passed verbatim (e.g. ``["status"]`` or ``["gateway", "run"]``).
    The fork reads ``sys.argv`` internally, so it is set before import; some
    argv-dependent behavior is decided at import time. ``sys.path``/``sys.argv``
    are restored afterwards so a one-shot dispatch cannot shadow Qubit's own
    top-level packages (the fork ships a ``tests/`` and ``tools/`` tree).
    """
    args = list(argv or [])
    saved_path = list(sys.path)
    saved_env = dict(os.environ)
    saved = sys.argv
    sys.argv = ["hermes", *args]
    try:
        ensure_path()
        from hermes_cli.main import main as _hermes_main
        result = _hermes_main()
    finally:
        sys.argv = saved
        sys.path[:] = saved_path
        os.environ.clear()
        os.environ.update(saved_env)
    return int(result) if isinstance(result, int) else 0


def status() -> dict:
    """Read-only runtime status: fork presence, provenance pin, importability.

    Never runs the runtime; safe for dashboard/voice/status surfaces."""
    import json
    root = runtime_root()
    out = {
        "runtime": "qubit-hermes-fork",
        "root": str(root),
        "present": root.is_dir(),
        "importable": False,
        "manifest": str(manifest_path()),
        "upstream_revision": None,
        "file_count": 0,
        "patched": 0,
    }
    if not root.is_dir():
        return out
    saved_path = list(sys.path)
    saved_env = dict(os.environ)
    try:
        ensure_path()
        import hermes_cli.main  # noqa: F401
        out["importable"] = True
    except Exception as exc:  # pragma: no cover - degraded env only
        out["import_error"] = f"{type(exc).__name__}: {exc}"[:200]
    finally:
        sys.path[:] = saved_path
        # The runtime's import loads dotenv into os.environ; a read-only status
        # probe must not leak provider credentials into the rest of the process.
        os.environ.clear()
        os.environ.update(saved_env)
    mf = manifest_path()
    if mf.is_file():
        try:
            m = json.loads(mf.read_text(encoding="utf-8"))
            out["upstream_revision"] = m.get("upstream_revision")
            out["file_count"] = m.get("file_count", 0)
            out["patched"] = len(m.get("patched", []))
        except Exception:
            pass
    return out


def entry() -> None:
    """Console-script entry point for the standalone ``qubit-hermes`` launcher."""
    raise SystemExit(run(sys.argv[1:]))


__all__ = ["runtime_root", "manifest_path", "ensure_path", "run", "status",
           "entry"]

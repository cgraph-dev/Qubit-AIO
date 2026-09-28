"""Shared fixtures for the PM suite."""
import functools
import subprocess
import sys
from pathlib import Path

import pytest

# Modules that drive the REAL ``pm.worker`` subprocess with the *test* interpreter
# as the worker interpreter (``pm.runtime.runtime_python -> sys.executable``).
# The worker cold-imports PM's declared runtime deps under ``-I`` (isolated):
# PYTHONPATH/user-site overlays cannot reach it, so the test interpreter itself
# must carry them. Gate those modules on that contract so a stale/under-declared
# venv yields an explicit OPERATOR skip instead of ``worker exited without a
# result``.
_WORKER_MODULES = frozenset({"test_security_consumers.py"})
_WORKER_IMPORT = "import packaging, tomli_w, truststore; from ruamel.yaml import YAML"


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "real_machine_home: keep Path.home() intact (a subprocess or stage_runtime needs the real one)")
    config.addinivalue_line(
        "markers",
        "pm_isolated_worker_deps: drives the real pm.worker under -I; the test "
        "interpreter must carry PM's declared runtime deps (packaging, tomli_w, "
        "truststore, ruamel.yaml)",
    )


@functools.lru_cache(maxsize=1)
def _isolated_worker_dep_error() -> str | None:
    """Reason ``python -I`` cannot import the worker's declared deps, or None."""
    try:
        proc = subprocess.run(
            [sys.executable, "-I", "-B", "-c", _WORKER_IMPORT],
            capture_output=True, text=True, timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"probe failed: {exc}"
    if proc.returncode == 0:
        return None
    return (proc.stderr.strip().splitlines() or [f"exit {proc.returncode}"])[-1]


def pytest_collection_modifyitems(config, items):
    if not any(item.path.name in _WORKER_MODULES for item in items):
        return
    error = _isolated_worker_dep_error()
    if error is None:
        return
    marker = pytest.mark.pm_isolated_worker_deps
    skip = pytest.mark.skip(reason=(
        "OPERATOR: the isolated pm.worker cannot import PM's declared runtime deps "
        f"under `python -I` (interpreter {sys.executable}: {error}). The worker runs "
        "the real product boundary; its only stub is the supplied interpreter, and "
        "`-I` strips PYTHONPATH/user-site overlays, so no repo-side fix can inject "
        "them. `pm/worker.py` is byte-identical to the pinned upstream "
        "(69948c0057). Rebuild the test environment (install the declared [project] "
        "deps) or run the PM suite under the PM-managed test env."
    ))
    for item in items:
        if item.path.name in _WORKER_MODULES:
            item.add_marker(marker)
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def isolated_machine_home(request, tmp_path, monkeypatch):
    """Machine-scoped PM state (store, caches, profiles root) lands under tmp_path.

    The root conftest already sandboxes QUBIT_HOME; PM additionally keys its machine cache
    off the home directory, so that moves too — in this process (Path.home) and in any child
    (HOME / USERPROFILE). Opt out with ``@pytest.mark.real_machine_home``.
    """
    if request.node.get_closest_marker("real_machine_home"):
        return
    home = tmp_path / ".hermes"
    home.mkdir(exist_ok=True)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("QUBIT_HOME", str(home))

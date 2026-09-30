"""Regression test for dashboard audit D-1: the ``/chat`` slash worker must be able to
import ``tui_gateway`` regardless of the caller's cwd.

``tui_gateway.server._SlashWorker`` spawns ``python -m tui_gateway.slash_worker`` with
the caller's cwd and a sanitized env whose Qubit-owned PYTHONPATH entries have been
stripped. Upstream keeps ``tui_gateway/`` at the repo root, so cwd supplied the module;
Qubit nests the whole runtime under ``qubit_runtime/``, so after the strip there is no
path that makes the package importable and every slash command needing the worker died
with ``ModuleNotFoundError: No module named 'tui_gateway'``.

``tui_gateway.server._slash_worker_env`` must re-seed the vendored package root on the
child's PYTHONPATH — cwd-independent, with no ``~/.hermes`` fallback.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = REPO_ROOT / "qubit_runtime"
sys.path.insert(0, str(RUNTIME_ROOT))

# importing tui_gateway.server reassigns sys.stdout (JSON-RPC transport owns real stdout);
# restore it so the top-level hermetic gate keeps capturing test output.
_stdout = sys.stdout
from tui_gateway import server as tui_server  # noqa: E402

sys.stdout = _stdout


def _entries(env: dict) -> list[str]:
    return [p for p in (env.get("PYTHONPATH") or "").split(os.pathsep) if p]


def test_worker_env_seeds_vendored_package_root():
    env = tui_server._slash_worker_env()
    root = str(tui_server._vendored_src_root())
    entries = _entries(env)

    assert root == str(RUNTIME_ROOT)
    assert entries and entries[0] == root, "vendored package root must lead PYTHONPATH"
    assert env["HERMES_PYTHON_SRC_ROOT"] == root
    assert env["QUBIT_PYTHON_SRC_ROOT"] == root
    assert not any("/.hermes" in p for p in entries), "no reference-home fallback"


def test_seed_preserves_existing_entries_and_is_idempotent():
    user_lib = os.pathsep.join(["/opt/userlibs", "/opt/other"])
    env = {"PYTHONPATH": user_lib}
    tui_server._seed_vendored_src_root_pythonpath(env)

    root = str(tui_server._vendored_src_root())
    assert _entries(env) == [root, "/opt/userlibs", "/opt/other"]

    tui_server._seed_vendored_src_root_pythonpath(env)
    assert _entries(env) == [root, "/opt/userlibs", "/opt/other"], "must not duplicate"


def test_slash_worker_imports_from_the_spawn_env(tmp_path):
    """End-to-end: the production worker env resolves ``tui_gateway.slash_worker``
    from a cwd unrelated to the runtime; the sanitizer-stripped env does not."""
    env = tui_server._slash_worker_env()
    env["QUBIT_HOME"] = str(tmp_path / "home")

    broken = {k: v for k, v in env.items() if k not in (
        "PYTHONPATH", "HERMES_PYTHON_SRC_ROOT", "QUBIT_PYTHON_SRC_ROOT")}
    bad = subprocess.run(
        [sys.executable, "-c", "import tui_gateway.slash_worker"],
        cwd=tmp_path, env=broken, capture_output=True, text=True, timeout=300,
    )
    assert bad.returncode != 0, "without the seed the worker must fail to import"
    assert "ModuleNotFoundError" in bad.stderr

    good = subprocess.run(
        [sys.executable, "-c", "import tui_gateway.slash_worker"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=300,
    )
    assert good.returncode == 0, good.stderr

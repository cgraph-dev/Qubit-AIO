"""Shipped updaters import ``qubit_cli.local_runtime.processes`` from the new tree mid-update.

v2026.9.24's ``_subprocess_compat.bounded_probe_run`` lazily imports
``spawn_server`` after the pull, inside a process that still holds the old
``qubit_platform`` and other first-party modules. Importing that one
submodule must not run the rest of the package, or any name added to an
already-loaded module since the release crashes ``qubit update``.
"""

import subprocess
import sys

import pytest

_PROBE = (
    "import sys\n"
    "import qubit_cli.local_runtime.processes\n"
    "loaded = sorted(m for m in sys.modules if m.startswith(('qubit_platform', 'qubit_cli.local_runtime.')))\n"
    "print('\\n'.join(loaded))\n"
)


# PRODUCT GAP (Qubit fork): ``qubit_cli/local_runtime/__init__.py`` replaced
# upstream's lazy PEP-562 re-export (``hermes_cli/local_runtime/__init__.py`` in
# pin cbe5e53e defines ``_EXPORTS`` + ``__getattr__`` and imports nothing up
# front) with EAGER ``from …local_runtime.<mod> import …`` lines. Importing
# ``qubit_cli.local_runtime.processes`` now drags in every sibling submodule,
# which is exactly the failure the lazy design exists to prevent: a shipped
# updater imports ``…processes`` from the NEW checkout while its process still
# holds stale first-party modules, so ``qubit update`` can crash on any name
# added since the release. Do NOT fix product here.
_LAZY_IMPORT_GAP = pytest.mark.xfail(
    strict=False,
    reason="PRODUCT GAP: qubit_cli/local_runtime/__init__.py eagerly imports every "
    "submodule where upstream pin cbe5e53e uses a lazy __getattr__ re-export; "
    "importing …processes runs the whole package (breaks qubit update).",
)


@_LAZY_IMPORT_GAP
def test_processes_import_runs_no_other_local_runtime_module():
    out = subprocess.run([sys.executable, "-c", _PROBE], capture_output=True, text=True, check=True).stdout
    assert out.split() == ["qubit_cli.local_runtime.processes"]


def test_package_exports_still_resolve():
    import qubit_cli.local_runtime as local_runtime
    from qubit_cli.local_runtime.hardware import probe_budget

    assert local_runtime.probe_budget is probe_budget

"""Shipped updaters import ``qubit_cli.local_runtime.processes`` from the new tree mid-update.

v2026.9.24's ``_subprocess_compat.bounded_probe_run`` lazily imports
``spawn_server`` after the pull, inside a process that still holds the old
``qubit_platform`` and other first-party modules. Importing that one
submodule must not run the rest of the package, or any name added to an
already-loaded module since the release crashes ``qubit update``.
"""

import subprocess
import sys


_PROBE = (
    "import sys\n"
    "import qubit_cli.local_runtime.processes\n"
    "loaded = sorted(m for m in sys.modules if m.startswith(('qubit_platform', 'qubit_cli.local_runtime.')))\n"
    "print('\\n'.join(loaded))\n"
)


# The lazy PEP-562 re-export (``_EXPORTS`` + ``__getattr__``, importing nothing
# up front) is now in place: importing ``qubit_cli.local_runtime.processes`` no
# longer drags in every sibling submodule, so a shipped updater that imports it
# from the NEW checkout while its process still holds stale first-party modules
# no longer crashes ``qubit update`` on any name added since the release.
def test_processes_import_runs_no_other_local_runtime_module():
    out = subprocess.run([sys.executable, "-c", _PROBE], capture_output=True, text=True, check=True).stdout
    assert out.split() == ["qubit_cli.local_runtime.processes"]


def test_package_exports_still_resolve():
    import qubit_cli.local_runtime as local_runtime
    from qubit_cli.local_runtime.hardware import probe_budget

    assert local_runtime.probe_budget is probe_budget

"""Import-path setup for the ported ``tests/hermes_cli`` tree.

The ported files import the fork's modules by bare name (``qubit_cli.*``,
``gateway.*`` …), which live under ``qubit_runtime/``. ``ensure_path()`` is the
one sanctioned way to put that root ahead of the repo ``tests/`` directory on
``sys.path`` (same pattern as ``tests/agent/conftest.py``).
"""
from voicebridge import qubit_runtime as _qubit_runtime

_qubit_runtime.ensure_path()


def pytest_configure(config):
    # starlette's TestClient imports the deprecated anyio.abc.BlockingPortal
    # alias at module import; the repo's ``filterwarnings = error`` would turn
    # that third-party deprecation into a collection error before a module-level
    # mark can apply. Scoped to this tree.
    config.addinivalue_line(
        "filterwarnings",
        "ignore:The anyio.abc.BlockingPortal alias is deprecated:DeprecationWarning",
    )

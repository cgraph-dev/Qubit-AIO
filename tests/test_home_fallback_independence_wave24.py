"""Wave 24 — the vendored runtime has no ``~/.hermes`` default-home fallback.

Qubit must never materialize or read the external reference home
(``docs/DECISIONS.md`` #9/#17, ``docs/MIGRATION.md``): with ``QUBIT_HOME`` unset
and ``HOME`` redirected to a throwaway operator identity, every home resolver in
the rebased runtime must fall back to the Qubit-owned default (``~/.qubit`` /
the platform equivalent), never ``~/.hermes``.

Two proofs:

* in-process — each module's default-home resolver returns a Qubit path, and an
  existing legacy ``~/.hermes`` is not adopted as the default;
* subprocess — importing the whole set with a scratch ``HOME`` and no
  ``QUBIT_HOME`` neither creates nor touches ``.hermes``.

Hermetic: isolated scratch ``HOME``/``XDG_*``; the operator's real ``~/.hermes``
is never read, written, renamed, or moved.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
RUNTIME = REPO / "qubit_runtime"

_MODULES = (
    "qubit_constants",
    "tools.mcp_tool_config",
    "tools.bot_mode_dm",
    "tools.bot_mode_probe",
    "mcp_serve",
    "qubit_startup_watchdog",
    "qubit_cli.env_loader",
)

_QUBIT_HOME_NAMES = (".qubit", "qubit")


def _is_legacy_home(path) -> bool:
    return Path(str(path)).name in (".hermes", "hermes")


def _is_qubit_home(path) -> bool:
    return Path(str(path)).name in _QUBIT_HOME_NAMES


@pytest.fixture
def runtime_on_path(tmp_path, monkeypatch):
    """Import the runtime from its own tree under an isolated state root."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
    monkeypatch.syspath_prepend(str(RUNTIME))
    yield


@pytest.fixture
def scratch_home(tmp_path, monkeypatch):
    """Redirect the operator identity so ``~/.hermes`` is a scratch path.

    This is what makes the no-fallback proof non-destructive: the test names
    ``~/.hermes`` but resolves it under ``tmp_path``; the real reference home is
    never touched.
    """
    home = tmp_path / "operator"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.delenv("QUBIT_HOME", raising=False)
    return home


def test_platform_default_home_is_qubit_owned(scratch_home, runtime_on_path):
    import qubit_constants as qc

    default = qc._get_platform_default_qubit_home()
    assert not _is_legacy_home(default)
    assert _is_qubit_home(default)
    assert qc.get_process_qubit_home() == default


def test_get_default_qubit_root_ignores_existing_legacy_home(
        scratch_home, runtime_on_path):
    """An existing legacy ``~/.hermes`` must not become the default root."""
    legacy = scratch_home / ".hermes"
    legacy.mkdir()
    (legacy / "config.yaml").write_text("legacy: true\n", encoding="utf-8")

    import qubit_constants as qc

    root = qc.get_default_qubit_root()
    assert not _is_legacy_home(root)
    assert _is_qubit_home(root)
    assert not _is_legacy_home(qc.get_process_qubit_home())


def test_module_default_homes_are_qubit_owned(scratch_home, runtime_on_path):
    import mcp_serve
    import qubit_startup_watchdog as watchdog
    from qubit_cli import env_loader
    from tools import bot_mode_dm, bot_mode_probe

    resolvers = {
        "startup_watchdog._process_qubit_home": watchdog._process_qubit_home,
        "bot_mode_dm._default_home": bot_mode_dm._default_home,
        "bot_mode_probe._default_home": bot_mode_probe._default_home,
        "mcp_serve._qubit_home": mcp_serve._qubit_home,
        "env_loader._process_qubit_home": env_loader._process_qubit_home,
    }
    for label, resolver in resolvers.items():
        resolved = resolver()
        assert not _is_legacy_home(resolved), f"{label} fell back to ~/.hermes"


def test_importing_runtime_does_not_materialize_hermes(tmp_path, monkeypatch):
    """A fresh scratch HOME + no QUBIT_HOME must not create ``~/.hermes``."""
    scratch = tmp_path / "operator"
    scratch.mkdir()
    env = {
        "HOME": str(scratch),
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(RUNTIME),
        "LANG": "C.UTF-8",
        "TZ": "UTC",
    }
    script = textwrap.dedent(
        f"""
        import importlib
        import os
        from pathlib import Path

        for name in {_MODULES!r}:
            importlib.import_module(name)

        home = Path(os.environ["HOME"])
        print("HERMES_EXISTS", (home / ".hermes").exists())

        import qubit_constants as qc
        print("DEFAULT_HOME", qc.get_process_qubit_home())
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert "HERMES_EXISTS False" in proc.stdout, proc.stdout
    assert "DEFAULT_HOME" in proc.stdout
    default_line = next(
        line for line in proc.stdout.splitlines() if line.startswith("DEFAULT_HOME")
    )
    assert not _is_legacy_home(default_line.split(" ", 1)[1])
    assert not (scratch / ".hermes").exists()

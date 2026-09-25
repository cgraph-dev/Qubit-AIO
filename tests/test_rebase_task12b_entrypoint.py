"""Task 12b (v0.21.5 rebase) — the shipped ``qubit-agent`` script honors argv.

Task 12 ported the vendored ``agent.legacy_cli`` argv parser, but the *shipped*
``qubit-agent`` console script still pointed at the full ``qubit`` CLI
(``voicebridge.qubit_runtime:entry``): it rejects the legacy flags
(``--query`` -> argparse exit 2) and has no legacy single-query runner. This
pins the product entry point to ``voicebridge.qubit_runtime:legacy_entry`` and
proves, in a subprocess with a throwaway ``HOME``/``QUBIT_HOME``, that
``qubit-agent --help`` prints usage and ``qubit-agent --version`` prints a
version — no network, no model, no reinstall.

Hermetic: the subprocess is the current interpreter with ``PYTHONPATH`` pinned
to this checkout, so the in-tree ``voicebridge``/``qubit_runtime`` are imported
even when another copy is installed in the surrounding venv.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RUNTIME = REPO / "qubit_runtime"
PRODUCT_PYPROJECT = REPO / "pyproject.toml"
VENDORED_PYPROJECT = RUNTIME / "pyproject.toml"

# An installed console script calls its entry point with no arguments; this
# launcher reproduces that call shape for a throwaway interpreter.
_LAUNCHER = """\
#!{python}
from voicebridge.qubit_runtime import legacy_entry

legacy_entry()
"""


def _toml(path: Path) -> dict:
    with path.open("rb") as fh:
        return tomllib.load(fh)


def _run_agent_script(tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    """Run a throwaway ``qubit-agent`` console-script stand-in hermetically."""
    home = tmp_path / "home"
    qubit_home = tmp_path / "qubit-home"
    for base in (home, qubit_home, tmp_path / "xdg-state",
                 tmp_path / "xdg-config", tmp_path / "xdg-cache"):
        base.mkdir(parents=True, exist_ok=True)
    launcher = tmp_path / "qubit-agent"
    launcher.write_text(_LAUNCHER.format(python=sys.executable), encoding="utf-8")
    launcher.chmod(0o755)
    env = {
        "HOME": str(home),
        "QUBIT_HOME": str(qubit_home),
        "XDG_STATE_HOME": str(tmp_path / "xdg-state"),
        "XDG_CONFIG_HOME": str(tmp_path / "xdg-config"),
        "XDG_CACHE_HOME": str(tmp_path / "xdg-cache"),
        # Pin the in-tree checkout ahead of any installed copy.
        "PYTHONPATH": str(REPO),
        "PATH": os.environ.get("PATH", ""),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    return subprocess.run(
        [sys.executable, str(launcher), *args],
        capture_output=True, text=True, env=env, cwd=str(REPO), timeout=180,
    )


def test_entry_targets_are_the_legacy_runner():
    product = _toml(PRODUCT_PYPROJECT)["project"]["scripts"]
    vendored = _toml(VENDORED_PYPROJECT)["project"]["scripts"]
    # Shipped script -> product legacy dispatch; vendored -> upstream legacy CLI.
    assert product["qubit-agent"] == "voicebridge.qubit_runtime:legacy_entry"
    assert vendored["qubit-agent"] == "agent.legacy_cli:main"
    # The other public scripts stay on their existing full-CLI targets.
    assert product["qubit"] == "voicebridge.cli:main"
    assert product["voicebridge"] == "voicebridge.cli:main"
    assert product["qubit-dev"] == "voicebridge.terminal:main"


def test_help_prints_usage(tmp_path):
    proc = _run_agent_script(tmp_path, "--help")
    assert proc.returncode == 0, proc.stderr
    assert "usage: qubit-agent" in proc.stdout, proc.stdout
    assert "--query" in proc.stdout and "--list-tools" in proc.stdout


def test_version_prints_a_version(tmp_path):
    proc = _run_agent_script(tmp_path, "--version")
    assert proc.returncode == 0, proc.stderr
    assert re.search(r"Qubit Agent v\d+\.\d+\.\d+", proc.stdout), proc.stdout


def test_bare_invocation_prints_help_and_never_runs_a_query(tmp_path):
    proc = _run_agent_script(tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "usage: qubit-agent" in proc.stdout, proc.stdout
    assert "No query given" in proc.stdout, proc.stdout

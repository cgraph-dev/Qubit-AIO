"""Clean source-install rehearsal regressions.

These pin the defects a real clean source install exposed:

* a ``--prefix`` install must make its own launcher use the prefix tree
  (INSTALL.md documents ``--prefix`` as equivalent to ``QUBIT_HOME=DIR``);
* the launcher render must not use GNU-only ``sed -i`` (macOS is supported);
* the dashboard must be built BEFORE the package is installed, otherwise the
  non-editable wheel is built without ``frontend/qubit-dist`` and the served
  dashboard is STALE/BROKEN until a second install run.

Hermetic: temp copies of the installer, fake ``uv``/``node``/``npm``, throwaway
HOME/prefix. No network, no real venv, no writes outside pytest's tmp dir.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
INSTALL = REPO / "scripts" / "install.sh"
BASH = shutil.which("bash") or "/bin/bash"
REAL_SED = shutil.which("sed") or "/usr/bin/sed"

_CLEARED_ENV = (
    "QUBIT_HOME",
    "QUBIT_PREFIX",
    "XDG_DATA_HOME",
    "XDG_CONFIG_HOME",
    "XDG_STATE_HOME",
    "XDG_CACHE_HOME",
    "XDG_BIN_HOME",
)


def _env(home: Path, path: str = "/usr/bin:/bin", **extra: str) -> dict[str, str]:
    env = os.environ.copy()
    for key in _CLEARED_ENV:
        env.pop(key, None)
    env["HOME"] = str(home)
    env["PATH"] = path
    env.update(extra)
    return env


def _launcher_repo(tmp_path: Path) -> Path:
    """A minimal checkout the installer can render a launcher for."""
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy2(INSTALL, repo / "scripts" / "install.sh")
    # The layout resolver imports this dependency-free module when present.
    (repo / "voicebridge").mkdir()
    shutil.copy2(REPO / "voicebridge" / "paths.py", repo / "voicebridge" / "paths.py")
    stub = repo / ".venv" / "bin" / "qubit"
    stub.parent.mkdir(parents=True)
    stub.write_text(
        "#!/usr/bin/env bash\n"
        "printf 'QUBIT_HOME=%s\\n' \"${QUBIT_HOME:-<unset>}\"\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return repo


def _run_launcher_only(repo: Path, home: Path, *args: str, env=None):
    return subprocess.run(
        [BASH, str(repo / "scripts" / "install.sh"), "--launcher-only", *args],
        capture_output=True,
        text=True,
        env=env or _env(home),
        cwd=str(repo),
        timeout=60,
    )


def _launcher_home(launcher: Path, home: Path, env=None) -> str:
    r = subprocess.run(
        [str(launcher)],
        capture_output=True,
        text=True,
        env=env or _env(home),
        timeout=30,
    )
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert "QUBIT_HOME=" in r.stdout
    return r.stdout.split("QUBIT_HOME=", 1)[1].splitlines()[0]


# ---------------------------------------------------------------------------
# --prefix launcher must use the prefix tree
# ---------------------------------------------------------------------------
def test_prefix_launcher_pins_the_prefix_qubit_home(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    repo = _launcher_repo(tmp_path)
    prefix = tmp_path / "portable"

    r = _run_launcher_only(repo, home, "--prefix", str(prefix))
    assert r.returncode == 0, (r.stdout, r.stderr)

    launcher = prefix / "bin" / "qubit"
    assert launcher.is_file()
    # No QUBIT_HOME in the environment: the launcher must resolve the prefix,
    # not the real HOME's ~/.qubit.
    assert _launcher_home(launcher, home) == str(prefix)
    assert not (home / ".qubit").exists()

    # An explicit QUBIT_HOME still wins over the baked default.
    assert _launcher_home(
        launcher, home, env=_env(home, QUBIT_HOME=str(tmp_path / "custom"))
    ) == str(tmp_path / "custom")


def test_explicit_qubit_home_is_pinned_without_prefix(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    repo = _launcher_repo(tmp_path)
    qh = tmp_path / "runtime-home"

    r = _run_launcher_only(repo, home, env=_env(home, QUBIT_HOME=str(qh)))
    assert r.returncode == 0, (r.stdout, r.stderr)

    launcher = home / ".local" / "bin" / "qubit"
    assert launcher.is_file()
    assert _launcher_home(launcher, home) == str(qh)


def test_default_launcher_leaves_qubit_home_unpinned(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    repo = _launcher_repo(tmp_path)

    r = _run_launcher_only(repo, home)
    assert r.returncode == 0, (r.stdout, r.stderr)

    launcher = home / ".local" / "bin" / "qubit"
    assert launcher.is_file()
    # A default install keeps resolving ~/.qubit at run time (moving HOME
    # still works); it must not bake the install-time HOME into the shim.
    assert _launcher_home(launcher, home) == "<unset>"


# ---------------------------------------------------------------------------
# macOS / BSD sed compatibility
# ---------------------------------------------------------------------------
def _bsd_sed_bindir(tmp_path: Path) -> tuple[Path, Path]:
    bindir = tmp_path / "bsd-bin"
    bindir.mkdir()
    log = tmp_path / "sed.log"
    shim = bindir / "sed"
    shim.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$*\" >> '{log}'\n"
        "case \"$1\" in\n"
        "  -i*) echo 'sed: illegal option -- i' >&2; exit 1 ;;\n"
        "esac\n"
        f"exec '{REAL_SED}' \"$@\"\n",
        encoding="utf-8",
    )
    shim.chmod(0o755)
    return bindir, log


def test_launcher_render_works_with_bsd_style_sed(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    repo = _launcher_repo(tmp_path)
    bindir, log = _bsd_sed_bindir(tmp_path)
    prefix = tmp_path / "portable"

    r = _run_launcher_only(
        repo,
        home,
        "--prefix",
        str(prefix),
        env=_env(home, path=f"{bindir}:/usr/bin:/bin"),
    )

    assert r.returncode == 0, (r.stdout, r.stderr)
    assert (prefix / "bin" / "qubit").is_file()
    # Prove the shim (which rejects GNU in-place editing) was actually used.
    assert log.is_file()
    calls = log.read_text(encoding="utf-8").splitlines()
    assert calls, "launcher render did not invoke sed"
    assert not any(call.startswith("-i") for call in calls)


# ---------------------------------------------------------------------------
# Dashboard must be built before the package install
# ---------------------------------------------------------------------------
def _fake_installer_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy2(INSTALL, repo / "scripts" / "install.sh")
    (repo / "config").mkdir()
    (repo / "config" / "voice.example.yaml").write_text("tts: {}\n", encoding="utf-8")

    build = repo / "scripts" / "build_dashboard.sh"
    build.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "root=\"$(cd \"$(dirname \"$0\")/..\" && pwd)\"\n"
        "mkdir -p \"$root/frontend/qubit-dist\"\n"
        "printf '<html></html>\\n' > \"$root/frontend/qubit-dist/index.html\"\n"
        "printf 'dashboard-built\\n' >> \"$FAKE_LOG\"\n",
        encoding="utf-8",
    )
    build.chmod(0o755)
    return repo


def test_dashboard_is_built_before_the_package_is_installed(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    repo = _fake_installer_repo(tmp_path)
    prefix = tmp_path / "prefix"
    log = tmp_path / "order.log"

    bindir = tmp_path / "bin"
    bindir.mkdir()
    for tool in ("node", "npm"):
        p = bindir / tool
        p.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        p.chmod(0o755)
    uv = bindir / "uv"
    uv.write_text(
        "#!/bin/sh\n"
        "set -e\n"
        "printf 'uv %s\\n' \"$*\" >> \"$FAKE_LOG\"\n"
        "if [ \"$1\" = venv ]; then\n"
        "  mkdir -p .venv/bin\n"
        "  printf '#!/bin/sh\\nexit 0\\n' > .venv/bin/python\n"
        "  chmod +x .venv/bin/python\n"
        "elif [ \"$1\" = pip ]; then\n"
        "  if [ -f frontend/qubit-dist/index.html ]; then\n"
        "    printf 'pip-saw-dashboard=yes\\n' >> \"$FAKE_LOG\"\n"
        "  else\n"
        "    printf 'pip-saw-dashboard=no\\n' >> \"$FAKE_LOG\"\n"
        "  fi\n"
        "  mkdir -p .venv/bin\n"
        "  printf '#!/bin/sh\\nexit 0\\n' > .venv/bin/qubit\n"
        "  chmod +x .venv/bin/qubit\n"
        "fi\n",
        encoding="utf-8",
    )
    uv.chmod(0o755)

    r = subprocess.run(
        [BASH, str(repo / "scripts" / "install.sh"), "--prefix", str(prefix), "--without-models"],
        capture_output=True,
        text=True,
        env=_env(home, path=f"{bindir}:/usr/bin:/bin", FAKE_LOG=str(log)),
        cwd=str(repo),
        timeout=120,
    )

    assert r.returncode == 0, (r.stdout, r.stderr)
    lines = log.read_text(encoding="utf-8").splitlines()
    assert "dashboard-built" in lines
    assert "pip-saw-dashboard=yes" in lines
    assert lines.index("dashboard-built") < lines.index("pip-saw-dashboard=yes")
    # The wheel-installed package is only built after the bundle exists.
    assert (repo / "frontend" / "qubit-dist" / "index.html").is_file()
    assert (prefix / "voice" / "config" / "voice.yaml").is_file()

"""The vendored installers clone Qubit, never the Hermes upstream.

The default repository a fresh install clones, the stable-release lookup and the
``is_official_repository`` / ``official_origin_url`` checks all named the Hermes
upstream. A default install therefore installed Hermes. These tests pin an
official Qubit source as the default; ``QUBIT_REPO_URL`` is canonical and
``HERMES_REPO_URL`` remains a read-old override.
"""
from pathlib import Path
import os
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
INSTALL_SH = ROOT / "scripts" / "install.sh"
INSTALL_PS1 = ROOT / "scripts" / "install.ps1"
QUBIT = "https://github.com/cgraph-dev/Qubit.git"
HERMES = "https://github.com/NousResearch/hermes-agent.git"


def test_installer_defaults_name_qubit_not_hermes():
    sh = INSTALL_SH.read_text(encoding="utf-8")
    ps1 = INSTALL_PS1.read_text(encoding="utf-8")
    sh_default = next(line for line in sh.splitlines() if line.startswith("REPO_URL="))
    ps1_default = next(line for line in ps1.splitlines() if line.startswith("$RepoUrl ="))
    assert "cgraph-dev/Qubit.git" in sh_default
    assert "cgraph-dev/Qubit.git" in ps1_default
    assert "hermes-agent" not in sh_default
    assert "hermes-agent" not in ps1_default


def _fake_git(bin_dir: Path) -> Path:
    log = bin_dir / "git.log"
    git = bin_dir / "git"
    git.write_text(
        "#!/bin/sh\n"
        'printf \'%s\\n\' "$*" >> "$GIT_LOG"\n'
        'if [ "$1" = clone ]; then\n'
        '  for arg in "$@"; do dest="$arg"; done\n'
        '  mkdir -p "$dest"\n'
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    git.chmod(0o755)
    return log


@pytest.mark.platforms("posix")
@pytest.mark.parametrize("env_override,expected", [
    ({}, QUBIT),
    ({"QUBIT_REPO_URL": "https://github.com/cgraph-dev/Qubit-AIO.git"},
     "https://github.com/cgraph-dev/Qubit-AIO.git"),
    ({"HERMES_REPO_URL": "https://example.invalid/legacy.git"},
     "https://example.invalid/legacy.git"),
])
def test_posix_default_clone_url_is_qubit(tmp_path, env_override, expected):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = _fake_git(bin_dir)
    install = tmp_path / "home" / "qubit"
    env = {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
           "HOME": str(tmp_path / "home"), "NO_COLOR": "1",
           "GIT_LOG": str(log), "QUBIT_HOME": str(tmp_path / "home" / ".qubit"),
           **env_override}
    result = subprocess.run(
        ["bash", str(INSTALL_SH), "--stage", "repository", "--channel", "main",
         "--dir", str(install), "-QubitHome", env["QUBIT_HOME"], "--non-interactive"],
        check=False, capture_output=True, text=True, stdin=subprocess.DEVNULL,
        timeout=120, env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    clones = [line for line in log.read_text(encoding="utf-8").splitlines()
              if line.startswith("clone ")]
    assert clones, "the installer did not clone"
    for line in clones:
        assert expected in line
        assert "hermes-agent" not in line


@pytest.mark.platforms("posix")
@pytest.mark.parametrize("url,official", [
    ("https://github.com/cgraph-dev/Qubit.git", True),
    ("https://github.com/cgraph-dev/Qubit-AIO.git", True),
    ("https://github.com/cgraph-dev/qubit-aio.git", True),
    ("git@github.com:cgraph-dev/Qubit.git", True),
    (HERMES, False),
    ("git@github.com:NousResearch/hermes-agent.git", False),
    ("https://github.com/NousResearch/hermes-agent", False),
])
def test_qubit_is_the_only_official_source(url, official):
    script = (
        'set -u; source "$SCRIPT"; REPO_URL="$URL"; '
        'if is_official_repository; then printf official; else printf fork; fi; '
        'if official_origin_url "$URL"; then printf " url-official"; else printf " url-fork"; fi'
    )
    result = subprocess.run(
        ["bash", "-c", script],
        check=True, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60,
        env={**os.environ, "NO_COLOR": "1", "SCRIPT": str(INSTALL_SH), "URL": url},
    )
    expected = "official url-official" if official else "fork url-fork"
    assert result.stdout == expected, result.stdout

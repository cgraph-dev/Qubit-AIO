"""Release path contract for the Electron data root and the systemd user unit.

The canonical roots are ``~/.qubit`` (Linux/macOS) and ``%LOCALAPPDATA%\\qubit``
(Windows); ``~/.hermes`` / ``%LOCALAPPDATA%\\hermes`` stay readable read-old.
The systemd user unit directory follows ``$XDG_CONFIG_HOME/systemd/user``.
No test here writes to a real home, starts a service, or migrates data.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="node is required for the Electron data-path module")
def test_electron_data_paths_use_qubit_and_read_legacy_only(tmp_path):
    module = REPO / "qubit_runtime" / "apps" / "desktop" / "electron" / "data-paths.mjs"
    script = f"""
import {{ platformDefaultHermesHome, resolveDesktopHermesHome }} from {json.dumps(module.as_uri())}
const posixHome = '/home/test'
const winHome = 'C:\\\\Users\\\\test'
const winLocal = 'C:\\\\Users\\\\test\\\\AppData\\\\Local'
const out = {{
  posix: platformDefaultHermesHome(posixHome, {{}}, 'linux'),
  win: platformDefaultHermesHome(winHome, {{ LOCALAPPDATA: winLocal }}, 'win32'),
  legacyPosix: resolveDesktopHermesHome({{
    home: posixHome, env: {{}}, platform: 'linux',
    directoryExists: directory => directory === '/home/test/.hermes'
  }}),
  canonicalWins: resolveDesktopHermesHome({{
    home: posixHome, env: {{}}, platform: 'linux',
    directoryExists: () => true
  }}),
  legacyWin: resolveDesktopHermesHome({{
    home: winHome, env: {{ LOCALAPPDATA: winLocal }}, platform: 'win32',
    directoryExists: directory => directory === winLocal + '\\\\hermes'
  }}),
  suffixed: resolveDesktopHermesHome({{
    home: posixHome, env: {{ HERMES_DATA_DIR_SUFFIX: 'magic-test' }}, platform: 'linux',
    directoryExists: directory => directory === '/home/test/.hermesmagic-test'
  }})
}}
console.log(JSON.stringify(out))
"""
    result = subprocess.run(
        [NODE, "--input-type=module", "-e", script],
        capture_output=True, text=True, cwd=tmp_path, check=True,
    )
    out = json.loads(result.stdout)

    assert out["posix"] == "/home/test/.qubit"
    assert out["win"] == "C:\\Users\\test\\AppData\\Local\\qubit"
    assert out["legacyPosix"] == "/home/test/.hermes"
    assert out["canonicalWins"] == "/home/test/.qubit"
    assert out["legacyWin"] == "C:\\Users\\test\\AppData\\Local\\hermes"
    assert out["suffixed"] == "/home/test/.qubitmagic-test"


def test_install_systemd_unit_honours_xdg_config_home(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    xdg = tmp_path / "xdg-config"
    env = dict(os.environ, HOME=str(home), XDG_CONFIG_HOME=str(xdg))
    env.pop("SYSTEMD_UNIT_DIR", None)

    result = subprocess.run(
        ["sh", str(REPO / "scripts" / "install_systemd_unit.sh"), str(REPO)],
        capture_output=True, text=True, env=env, timeout=60,
    )

    assert result.returncode == 0, result.stderr
    unit = xdg / "systemd" / "user" / "qubit-voicebridge.service"
    assert unit.is_file()
    assert not (home / ".config" / "systemd" / "user" / "qubit-voicebridge.service").exists()
    text = unit.read_text(encoding="utf-8")
    assert "__PROJECT_DIR__" not in text
    assert f'ExecStart="{REPO}/.venv/bin/python" -m voicebridge run' in text
    assert f"WorkingDirectory={REPO}" in text


def test_install_systemd_unit_falls_back_to_home_config(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    env = dict(os.environ, HOME=str(home), XDG_CONFIG_HOME="relative/not/allowed")
    env.pop("SYSTEMD_UNIT_DIR", None)

    result = subprocess.run(
        ["sh", str(REPO / "scripts" / "install_systemd_unit.sh"), str(REPO)],
        capture_output=True, text=True, env=env, timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert (home / ".config" / "systemd" / "user" / "qubit-voicebridge.service").is_file()


@pytest.mark.skipif(platform.system() != "Linux", reason="systemd user units are Linux-only")
def test_product_service_owner_uses_xdg_config_home(monkeypatch, tmp_path):
    from voicebridge import product

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))

    state = product.service_owner()

    assert state["unit_path"] == str(
        tmp_path / "xdg-config" / "systemd" / "user" / "qubit-voicebridge.service"
    )

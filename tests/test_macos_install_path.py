"""macOS install path — explicit core support without claiming voice parity.

Hermetic: dry-run only, a fake ``uname``/``sw_vers``/``launchctl`` PATH, and a
throwaway HOME. No network, no writes outside pytest's tmp dir, no launchd.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
ROOT_INSTALL = REPO / "scripts" / "install.sh"
LAUNCHD_INSTALL = REPO / "scripts" / "install_launchd_unit.sh"
SYSTEMD_INSTALL = REPO / "scripts" / "install_systemd_unit.sh"
BASH = shutil.which("bash") or "/bin/bash"
SH = shutil.which("sh") or "/bin/sh"

_CLEARED_ENV = (
    "QUBIT_HOME",
    "QUBIT_PREFIX",
    "XDG_DATA_HOME",
    "XDG_CONFIG_HOME",
    "XDG_STATE_HOME",
    "XDG_CACHE_HOME",
    "XDG_BIN_HOME",
    "QUBIT_LAUNCH_AGENTS_DIR",
)

needs_uv = pytest.mark.skipif(
    not (shutil.which("curl") and shutil.which("uv")),
    reason="installer dry-run requires curl and uv on PATH",
)


def _env(home: Path, path: str | None = None) -> dict[str, str]:
    env = os.environ.copy()
    for key in _CLEARED_ENV:
        env.pop(key, None)
    env["HOME"] = str(home)
    if path is not None:
        env["PATH"] = path
    return env


def _fake_path(tmp_path: Path, tools: tuple[str, ...]) -> Path:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for tool in tools:
        real = shutil.which(tool)
        assert real, f"host is missing {tool}"
        (bindir / tool).symlink_to(real)
    return bindir


def _script(bindir: Path, name: str, body: str) -> None:
    path = bindir / name
    path.write_text("#!/bin/sh\n" + body + "\n")
    path.chmod(0o755)


def _darwin_path(tmp_path: Path) -> Path:
    bindir = _fake_path(
        tmp_path, ("bash", "dirname", "curl", "uv")
    )
    _script(
        bindir,
        "uname",
        'case "$1" in -m) echo arm64 ;; *) echo Darwin ;; esac',
    )
    _script(bindir, "sw_vers", "echo 14.5")
    _script(bindir, "launchctl", "exit 0")
    return bindir


# ---------------------------------------------------------------------------
# Syntax / static support boundary
# ---------------------------------------------------------------------------
def test_macos_install_scripts_are_syntax_clean():
    for script in (ROOT_INSTALL, LAUNCHD_INSTALL, SYSTEMD_INSTALL):
        shell = BASH if script == ROOT_INSTALL else SH
        r = subprocess.run(
            [shell, "-n", str(script)], capture_output=True, text=True
        )
        assert r.returncode == 0, (script, r.stderr)


def test_launchd_installer_targets_only_the_core_gateway():
    src = LAUNCHD_INSTALL.read_text(encoding="utf-8")
    assert "ai.qubit.gateway" in src
    assert "Library/LaunchAgents" in src
    assert "ai.hermes" in src and "left in place" in src
    assert "UNSUPPORTED" in src  # voice bridge/orb/typing boundary
    # Voice units are never installed by the macOS path.
    for unit in (
        "qubit-voicebridge.service",
        "qubit-voice-orb.service",
        "qubit-voice-typing.service",
    ):
        assert unit not in src
    # Read-old legacy agents are reported, never deleted.
    assert "\nrm " not in src
    assert "bootout" not in src


def test_root_installer_documents_macos_boundary():
    src = ROOT_INSTALL.read_text(encoding="utf-8")
    assert "Darwin" in src
    assert "Library/LaunchAgents" in src
    assert "ai.qubit.gateway.plist" in src
    assert "--with-launchd" in src
    assert "UNSUPPORTED on macOS" in src
    assert "ai.hermes" in src  # read-old legacy agents are named, not deleted


def test_runtime_installer_names_macos_core_boundary():
    src = (REPO / "qubit_runtime" / "scripts" / "install.sh").read_text(
        encoding="utf-8"
    )
    assert "Darwin" in src
    assert "ai.qubit.gateway" in src
    assert "UNSUPPORTED on macOS" in src


def test_read_old_launchd_cleanup_survives():
    gateway = (REPO / "qubit_runtime" / "qubit_cli" / "gateway.py").read_text(
        encoding="utf-8"
    )
    assert "ai.hermes.gateway" in gateway
    assert "legacy_launchd_labels_for_install" in gateway


def test_macos_launchd_dry_run_prints_core_only_plan(tmp_path):
    if sys.platform == "darwin":
        pytest.skip("typed non-Darwin refusal is the branch under test")
    home = tmp_path / "home"
    home.mkdir()
    path = _darwin_path(tmp_path)
    r = subprocess.run(
        [SH, str(LAUNCHD_INSTALL), str(REPO), "--dry-run"],
        capture_output=True,
        text=True,
        env=_env(home, str(path)),
        cwd=str(REPO),
    )
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert f"{home}/Library/LaunchAgents/ai.qubit.gateway.plist" in r.stdout
    assert "UNSUPPORTED on macOS" in r.stdout
    assert "read-old" in r.stdout
    assert not (home / "Library").exists()


@needs_uv
def test_root_macos_dry_run_uses_launchagent_and_skips_voice_models(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    path = _darwin_path(tmp_path)
    r = subprocess.run(
        [
            BASH,
            str(ROOT_INSTALL),
            "--dry-run",
            "--with-launchd",
            "--without-dashboard",
        ],
        capture_output=True,
        text=True,
        env=_env(home, str(path)),
        cwd=str(REPO),
    )
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert "platform  : macOS" in r.stdout
    assert f"{home}/Library/LaunchAgents/ai.qubit.gateway.plist" in r.stdout
    assert "voice     : UNSUPPORTED on macOS" in r.stdout
    assert "would install the gateway LaunchAgent" in r.stdout
    assert "voice-model assets are Linux-only" in r.stdout
    assert "dry-run complete" in r.stdout
    # Dry-run writes nothing anywhere.
    assert not (home / "Library").exists()
    assert not (home / ".config").exists()
    assert not (home / ".local").exists()
    assert not (home / ".qubit").exists()


@needs_uv
def test_with_systemd_on_macos_maps_to_the_gateway_launchagent(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    path = _darwin_path(tmp_path)
    r = subprocess.run(
        [
            BASH,
            str(ROOT_INSTALL),
            "--dry-run",
            "--with-systemd",
            "--without-models",
            "--without-dashboard",
        ],
        capture_output=True,
        text=True,
        env=_env(home, str(path)),
        cwd=str(REPO),
    )
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert "systemd is Linux-only; installing the macOS gateway LaunchAgent" in r.stdout
    assert "would install the gateway LaunchAgent" in r.stdout
    assert not (home / "Library").exists()


def test_with_launchd_is_refused_on_linux(tmp_path):
    if sys.platform == "darwin":
        pytest.skip("Linux refusal is the branch under test")
    home = tmp_path / "home"
    home.mkdir()
    r = subprocess.run(
        [BASH, str(ROOT_INSTALL), "--dry-run", "--with-launchd"],
        capture_output=True,
        text=True,
        env=_env(home),
        cwd=str(REPO),
    )
    assert r.returncode == 2
    assert "UNSUPPORTED_PLATFORM" in r.stderr
    assert "--with-launchd is macOS-only" in r.stderr


def test_systemd_installer_refuses_darwin(tmp_path):
    if sys.platform == "darwin":
        pytest.skip("non-Darwin branch is the one under test")
    home = tmp_path / "home"
    home.mkdir()
    path = _darwin_path(tmp_path)
    r = subprocess.run(
        [SH, str(SYSTEMD_INSTALL), str(REPO)],
        capture_output=True,
        text=True,
        env=_env(home, str(path)),
        cwd=str(REPO),
    )
    assert r.returncode == 2
    assert "UNSUPPORTED_PLATFORM" in r.stderr
    assert "install_launchd_unit.sh" in r.stderr


# ---------------------------------------------------------------------------
# voicebridge.product platform gate
# ---------------------------------------------------------------------------
def test_product_platform_macos_is_explicit_partial(monkeypatch):
    from voicebridge import product as P

    monkeypatch.setattr(P.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(P.platform, "machine", lambda: "arm64")
    st = P.platform_supported()
    assert st["state"] == "PARTIAL"
    assert st["core_supported"] is True
    assert st["voice_supported"] is False
    assert st["gateway_manager"] == "launchd"
    assert "launchd" in st["detail"] and "UNSUPPORTED" in st["detail"]

    # The voice product installer must not stage a voice install on macOS.
    steps = P.install_steps(dry_run=True)
    assert steps[0]["step"] == "platform"
    assert steps[0]["state"] == "UNAVAILABLE"
    assert "voice" in steps[0]["detail"]


def test_product_service_owner_macos_names_launchd(monkeypatch):
    from voicebridge import product as P

    monkeypatch.setattr(P.platform, "system", lambda: "Darwin")
    out = P.service_owner()
    assert out["state"] == "UNAVAILABLE"
    assert out["manager"] == "launchd"
    assert "ai.qubit.gateway" in out["detail"]
    assert "systemd" in out["detail"]

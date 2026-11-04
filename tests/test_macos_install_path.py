"""macOS install path — explicit core support without claiming voice parity.

Hermetic: dry-run only, a fake ``uname``/``sw_vers``/``launchctl`` PATH, and a
throwaway HOME. No network, no writes outside pytest's tmp dir, no launchd.
"""
from __future__ import annotations

import os
import pwd
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
ACCOUNT_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)
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
    # The plist path is resolved through the runtime bootstrap, which puts
    # the vendored qubit_cli on sys.path (a wheel install packages only
    # voicebridge; a bare `from qubit_cli` would miss the home-scoped label).
    assert "qubit_runtime.ensure_path()" in src
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
    assert "qubit_runtime.ensure_path()" in src
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
    assert f"{ACCOUNT_HOME}/Library/LaunchAgents" in r.stdout and "ai.qubit.gateway" in r.stdout
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
    assert f"{ACCOUNT_HOME}/Library/LaunchAgents" in r.stdout and "ai.qubit.gateway" in r.stdout
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
def test_root_macos_dry_run_plans_core_install_and_never_downloads_voice(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    path = _darwin_path(tmp_path)
    # No --without-models: the platform boundary alone must skip the Linux-only
    # voice-model assets while still planning the core venv/CLI/dashboard.
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
    assert f"QUBIT_HOME: {home / '.qubit'}" in r.stdout
    assert f"venv      : {REPO / '.venv'}" in r.stdout
    assert f"launcher  : {home / '.local/bin/qubit'}" in r.stdout
    assert ("would create venv (uv) and install package" in r.stdout or
            "venv already present" in r.stdout)
    assert "would install the user launcher" in r.stdout
    assert "voice-model assets skipped on macOS" in r.stdout
    assert "would install the gateway LaunchAgent" in r.stdout
    assert "voice bridge/orb/typing: UNSUPPORTED on macOS" in r.stdout
    # No model/voice download is even attempted in a macOS dry-run.
    assert "downloading" not in r.stdout
    assert not (home / "Library").exists()
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


# ---------------------------------------------------------------------------
# Real (hermetic) launchd install path
# ---------------------------------------------------------------------------
def _fake_launchd_repo(tmp_path: Path) -> tuple[Path, Path]:
    """A temp checkout whose ``.venv/bin/python`` is a recording stub.

    The stub answers the script's runtime path query (``-c ...``) from
    ``FAKE_PLIST`` and records the ``gateway install``/``gateway start``
    invocations. It deliberately does not execute the heredoc bodies.
    """
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / ".venv" / "bin").mkdir(parents=True)
    shutil.copy2(LAUNCHD_INSTALL, repo / "scripts" / "install_launchd_unit.sh")
    fake = repo / ".venv" / "bin" / "python"
    fake.write_text(
        "#!/bin/sh\n"
        'echo "argv=$* QUBIT_HOME=${QUBIT_HOME:-}" >> "$FAKE_LOG"\n'
        'if [ "$1" = "-c" ]; then\n'
        '  [ -n "${FAKE_PLIST:-}" ] && printf "%s\\n" "$FAKE_PLIST"\n'
        "  exit 0\n"
        "fi\n"
        'if [ "$1" = "-" ] && [ -n "${FAKE_PLIST:-}" ] && [ "${FAKE_WRITE:-0}" = "1" ]; then\n'
        '  mkdir -p "$(dirname "$FAKE_PLIST")"\n'
        '  printf "<?xml version=\\"1.0\\"?><plist><dict><key>Label</key><string>ai.qubit.gateway</string></dict></plist>\\n" > "$FAKE_PLIST"\n'
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    return repo, fake


def _run_launchd_install(tmp_path: Path, *args: str, extra_env: dict[str, str] | None = None):
    home = tmp_path / "home"
    home.mkdir()
    repo, _ = _fake_launchd_repo(tmp_path)
    bindir = _fake_path(tmp_path, ("sh", "dirname", "mkdir", "basename", "id"))
    _script(bindir, "uname", 'case "$1" in -m) echo arm64 ;; *) echo Darwin ;; esac')
    _script(bindir, "launchctl", "exit 0")
    env = _env(home, str(bindir))
    env.update(extra_env or {})
    return subprocess.run(
        [SH, str(repo / "scripts" / "install_launchd_unit.sh"), str(repo), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(repo),
    ), home


def test_launchd_install_fails_when_the_runtime_writes_no_plist(tmp_path):
    """A zero runtime exit is not proof of installation (the old false success)."""
    log = tmp_path / "log.txt"
    r, home = _run_launchd_install(
        tmp_path,
        extra_env={"FAKE_LOG": str(log), "FAKE_WRITE": "0"},
    )

    assert r.returncode == 1, (r.stdout, r.stderr)
    assert "LAUNCHD_PLIST_MISSING" in r.stderr
    assert "NOT installed" in r.stderr
    assert not (home / "Library" / "LaunchAgents").exists()


def test_launchd_install_verifies_and_reports_the_runtime_resolved_plist(tmp_path):
    """A profile/prefix install must report the home-scoped plist, not the default."""
    log = tmp_path / "log.txt"
    plist = tmp_path / "account" / "Library" / "LaunchAgents" / "ai.qubit.gateway-work.plist"
    r, _ = _run_launchd_install(
        tmp_path,
        extra_env={
            "FAKE_LOG": str(log),
            "FAKE_PLIST": str(plist),
            "FAKE_WRITE": "1",
            "QUBIT_HOME": str(tmp_path / "profile-home"),
        },
    )

    assert r.returncode == 0, (r.stdout, r.stderr)
    assert f"gateway LaunchAgent installed: {plist}" in r.stdout
    assert "status: launchctl print gui/" in r.stdout
    assert "ai.qubit.gateway-work" in r.stdout
    assert "ai.qubit.gateway.plist" not in r.stdout
    assert plist.is_file()

    calls = log.read_text(encoding="utf-8").splitlines()
    assert any("argv=- if-missing" in line for line in calls)
    assert any(line.startswith("argv=- ") for line in calls)
    assert all(f"QUBIT_HOME={tmp_path / 'profile-home'}" in line for line in calls)


def test_launchd_install_force_replaces_if_missing(tmp_path):
    log = tmp_path / "log.txt"
    plist = tmp_path / "account" / "Library" / "LaunchAgents" / "ai.qubit.gateway.plist"
    r, _ = _run_launchd_install(
        tmp_path,
        "--force",
        extra_env={
            "FAKE_LOG": str(log),
            "FAKE_PLIST": str(plist),
            "FAKE_WRITE": "1",
        },
    )

    assert r.returncode == 0, (r.stdout, r.stderr)
    assert "argv=- force" in log.read_text(encoding="utf-8")
    assert "would install with --force" not in r.stdout  # real, not dry-run


@needs_uv
def test_macos_prefix_dry_run_does_not_claim_the_default_plist(tmp_path):
    """A portable --prefix install is home-scoped; the plan must say so."""
    home = tmp_path / "home"
    home.mkdir()
    path = _darwin_path(tmp_path)
    prefix = tmp_path / "prefix"
    r = subprocess.run(
        [
            BASH,
            str(ROOT_INSTALL),
            "--dry-run",
            "--prefix",
            str(prefix),
            "--with-launchd",
            "--without-dashboard",
        ],
        capture_output=True,
        text=True,
        env=_env(home, str(path)),
        cwd=str(REPO),
    )

    assert r.returncode == 0, (r.stdout, r.stderr)
    assert f"QUBIT_HOME: {prefix}" in r.stdout
    assert f"ai.qubit.gateway-" in r.stdout and f"{ACCOUNT_HOME}/Library/LaunchAgents" in r.stdout
    assert f"{home}/Library/LaunchAgents/ai.qubit.gateway.plist" not in r.stdout
    assert "the gateway label is home-scoped" in r.stdout
    assert not (home / "Library").exists()
    assert not prefix.exists()

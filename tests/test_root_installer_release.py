"""Root installer (``scripts/install.sh``) release-hardening tests.

Hermetic: only the argument, ``--help`` and ``--dry-run`` paths run. No network
is touched and ``--dry-run`` must create nothing.
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

_CLEARED_ENV = (
    "QUBIT_HOME",
    "QUBIT_PREFIX",
    "XDG_DATA_HOME",
    "XDG_CONFIG_HOME",
    "XDG_STATE_HOME",
    "XDG_CACHE_HOME",
    "XDG_BIN_HOME",
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


def _run(args: list[str], env: dict[str, str], timeout: int = 120):
    return subprocess.run(
        [BASH, str(INSTALL), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO),
        timeout=timeout,
    )


def _fake_path(tmp_path: Path, tools: tuple[str, ...]) -> str:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for tool in tools:
        real = shutil.which(tool)
        assert real, f"host is missing {tool}"
        (bindir / tool).symlink_to(real)
    return str(bindir)


def test_installer_is_syntax_clean():
    r = subprocess.run([BASH, "-n", str(INSTALL)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_help_lists_every_supported_option(tmp_path: Path):
    r = _run(["--help"], _env(tmp_path))
    assert r.returncode == 0, r.stderr
    for flag in (
        "-h, --help",
        "-s, --with-systemd",
        "--with-launchd",
        "--dry-run",
        "--without-models",
        "--without-dashboard",
        "--prefix DIR",
        "-y, --yes",
    ):
        assert flag in r.stdout, f"{flag} missing from --help"
    assert "Linux and macOS" in r.stdout
    assert "UNSUPPORTED" in r.stdout


def test_unknown_option_exits_2_with_guidance(tmp_path: Path):
    r = _run(["--definitely-not-an-option"], _env(tmp_path))
    assert r.returncode == 2
    assert "unknown option" in r.stderr
    assert "--help" in r.stderr


def test_positional_argument_is_rejected(tmp_path: Path):
    r = _run(["unexpected"], _env(tmp_path))
    assert r.returncode == 2
    assert "unexpected argument" in r.stderr


def test_prefix_without_value_exits_2(tmp_path: Path):
    r = _run(["--prefix"], _env(tmp_path))
    assert r.returncode == 2
    assert "--prefix" in r.stderr


@needs_uv
def test_dry_run_prints_layout_and_writes_nothing(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    prefix = tmp_path / "opt"
    venv_before = (REPO / ".venv").exists()

    r = _run(
        ["--dry-run", "--prefix", str(prefix), "--without-models", "--without-dashboard"],
        _env(home),
    )

    assert r.returncode == 0, (r.stdout, r.stderr)
    assert "Qubit Agent install layout" in r.stdout
    assert "QUBIT_HOME:" in r.stdout
    assert str(prefix) in r.stdout
    assert "models    : skipped (--without-models)" in r.stdout
    assert "dashboard : skipped (--without-dashboard)" in r.stdout
    assert "dry-run complete" in r.stdout
    # No writes anywhere: not under the prefix, HOME, or the checkout.
    assert not prefix.exists()
    assert not (home / ".config").exists()
    assert not (home / ".local").exists()
    assert not (home / ".qubit").exists()
    assert (REPO / ".venv").exists() == venv_before


@needs_uv
def test_dry_run_uses_qbit_home_and_prefix_consistently(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    prefix = tmp_path / "portable"
    r = _run(["--dry-run", "--prefix", str(prefix), "--without-models", "--without-dashboard"], _env(home))
    assert r.returncode == 0, (r.stdout, r.stderr)
    # With --prefix and no explicit QUBIT_HOME, the runtime home follows the prefix.
    assert f"QUBIT_HOME: {prefix}" in r.stdout
    assert f"data      : {prefix}/share/qubit-voicebridge" in r.stdout
    assert f"config    : {prefix}/config/qubit-voicebridge/voice.yaml" in r.stdout
    assert f"launcher  : {prefix}/bin/qubit" in r.stdout


@needs_uv
def test_dry_run_explicit_qbit_home_is_printed_and_not_overwritten(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    prefix = tmp_path / "opt"
    qh = tmp_path / "runtime-home"
    env = _env(home)
    env["QUBIT_HOME"] = str(qh)
    r = _run(["--dry-run", "--prefix", str(prefix), "--without-models", "--without-dashboard"], env)
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert f"QUBIT_HOME: {qh}" in r.stdout
    assert "differs from the prefix" in r.stdout


@needs_uv
def test_dry_run_with_systemd_plans_enable_now(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    r = _run(
        ["--dry-run", "--with-systemd", "--without-models", "--without-dashboard"],
        _env(home),
    )
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert "systemctl --user enable --now" in r.stdout
    assert "qubit-voicebridge.service" in r.stdout
    assert "qubit-gateway.service" in r.stdout


@needs_uv
def test_missing_optional_tools_are_reported_as_degraded(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    path = _fake_path(tmp_path, ("bash", "dirname", "curl", "uv", "tar", "sha256sum"))
    r = _run(
        ["--dry-run", "--without-dashboard"],
        _env(home, path=path),
    )
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert "DEGRADED ollama" in r.stdout


def test_missing_required_tools_fail_typed_preflight(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    path = _fake_path(tmp_path, ("bash", "dirname"))
    r = _run(
        ["--dry-run", "--without-models", "--without-dashboard", "--prefix", str(tmp_path / "p")],
        _env(home, path=path),
    )
    assert r.returncode == 1
    assert "MISSING" in r.stdout
    assert "curl" in r.stdout
    assert "uv" in r.stdout
    assert "preflight failed" in r.stderr
    assert not (tmp_path / "p").exists()


@needs_uv
def test_prefix_env_and_equals_form_are_accepted(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    equals_prefix = tmp_path / "equals"
    r = _run(
        ["--dry-run", f"--prefix={equals_prefix}", "--without-models", "--without-dashboard"],
        _env(home),
    )
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert f"prefix    : {equals_prefix}" in r.stdout

    env_prefix = tmp_path / "env-prefix"
    env = _env(home)
    env["QUBIT_PREFIX"] = str(env_prefix)
    r = _run(["--dry-run", "--without-models", "--without-dashboard"], env)
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert f"prefix    : {env_prefix}" in r.stdout


def test_with_systemd_really_enables_and_starts_units():
    """Source guard: --with-systemd must act, not just render unit files."""
    src = INSTALL.read_text(encoding="utf-8")
    assert "systemctl --user enable --now" in src
    assert "install_systemd_unit.sh" in src
    assert "install_gateway_unit.sh" in src

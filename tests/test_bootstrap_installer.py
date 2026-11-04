"""Hermetic tests for ``scripts/bootstrap.sh`` (release one-line installer).

Every test uses a local ``file://`` or directory release base. No network is
touched. The downloaded artifact is a fake ``install.sh`` that records how it
was invoked, so the tests prove that only the verified copy runs.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
BOOTSTRAP = REPO / "scripts" / "bootstrap.sh"
BASH = shutil.which("bash") or "/bin/bash"

_FAKE_INSTALLER = """#!/usr/bin/env bash
set -euo pipefail
{
  echo "invoked=1"
  echo "profile=${QUBIT_PROFILE:-}"
  echo "args=$*"
} > "${BOOTSTRAP_TEST_MARKER:?}"
exit "${BOOTSTRAP_TEST_EXIT:-0}"
"""

_CLEARED_ENV = ("QUBIT_HOME", "QUBIT_PREFIX", "QUBIT_PROFILE")


def _env(home: Path, extra: dict[str, str] | None = None, path: str | None = None) -> dict[str, str]:
    env = os.environ.copy()
    for key in _CLEARED_ENV:
        env.pop(key, None)
    env["HOME"] = str(home)
    env["TMPDIR"] = str(home)
    if path is not None:
        env["PATH"] = path
    if extra:
        env.update(extra)
    return env


def _run(
    args: list[str],
    env: dict[str, str],
    cwd: Path,
    timeout: int = 60,
):
    return subprocess.run(
        [BASH, str(BOOTSTRAP), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cwd),
        timeout=timeout,
    )


def _release(tmp_path: Path, installer: str = _FAKE_INSTALLER) -> Path:
    release = tmp_path / "release"
    release.mkdir()
    asset = release / "install.sh"
    asset.write_text(installer, encoding="utf-8")
    digest = hashlib.sha256(asset.read_bytes()).hexdigest()
    (release / "SHA256SUMS").write_text(f"{digest}  install.sh\n", encoding="utf-8")
    return release


def _marker(tmp_path: Path) -> Path:
    return tmp_path / "invocation.txt"


def test_bootstrap_is_syntax_clean():
    r = subprocess.run([BASH, "-n", str(BOOTSTRAP)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_help_lists_options_and_linux_scope(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    r = _run(["--help"], _env(home), tmp_path)
    assert r.returncode == 0, r.stderr
    for flag in (
        "-h, --help",
        "--dry-run",
        "--profile NAME",
        "--prefix DIR",
        "--checksum FILE",
    ):
        assert flag in r.stdout, f"{flag} missing from --help"
    assert "Supported host: Linux" in r.stdout
    assert "macOS and Windows are typed refusals" in r.stdout


def test_unknown_option_exits_2_with_guidance(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    r = _run(["--definitely-not-an-option"], _env(home), tmp_path)
    assert r.returncode == 2
    assert "unknown option" in r.stderr
    assert "--help" in r.stderr


def test_missing_release_base_exits_2(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    r = _run([], _env(home), tmp_path)
    assert r.returncode == 2
    assert "missing release base" in r.stderr


def test_local_release_runs_only_the_verified_installer(tmp_path: Path):
    release = _release(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    marker = _marker(tmp_path)
    prefix = tmp_path / "opt"
    r = _run(
        ["--profile", "dev", "--prefix", str(prefix), str(release)],
        _env(home, {"BOOTSTRAP_TEST_MARKER": str(marker)}),
        tmp_path,
    )
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert "verified install.sh sha256=" in r.stdout
    assert marker.is_file(), "the verified installer did not run"
    body = marker.read_text(encoding="utf-8")
    assert "profile=dev" in body
    assert f"args=--prefix {prefix}" in body
    assert not prefix.exists(), "bootstrap must not install anything itself"


def test_file_url_release_base_is_supported(tmp_path: Path):
    release = _release(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    marker = _marker(tmp_path)
    r = _run(
        [f"file://{release}"],
        _env(home, {"BOOTSTRAP_TEST_MARKER": str(marker)}),
        tmp_path,
    )
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert marker.is_file()


def test_checksum_mismatch_refuses_to_execute(tmp_path: Path):
    release = _release(tmp_path)
    (release / "SHA256SUMS").write_text(
        "0" * 64 + "  install.sh\n", encoding="utf-8"
    )
    home = tmp_path / "home"
    home.mkdir()
    marker = _marker(tmp_path)
    r = _run(
        [str(release)],
        _env(home, {"BOOTSTRAP_TEST_MARKER": str(marker)}),
        tmp_path,
    )
    assert r.returncode == 6
    assert "BOOTSTRAP_CHECKSUM_MISMATCH" in r.stderr
    assert not marker.exists(), "a checksum failure must not execute the asset"


def test_missing_checksum_fails_closed(tmp_path: Path):
    release = _release(tmp_path)
    (release / "SHA256SUMS").unlink()
    home = tmp_path / "home"
    home.mkdir()
    marker = _marker(tmp_path)
    r = _run(
        [str(release)],
        _env(home, {"BOOTSTRAP_TEST_MARKER": str(marker)}),
        tmp_path,
    )
    assert r.returncode == 7
    assert "BOOTSTRAP_CHECKSUM_UNAVAILABLE" in r.stderr
    assert not marker.exists()


def test_missing_asset_fails_closed(tmp_path: Path):
    release = _release(tmp_path)
    (release / "install.sh").unlink()
    home = tmp_path / "home"
    home.mkdir()
    marker = _marker(tmp_path)
    r = _run(
        [str(release)],
        _env(home, {"BOOTSTRAP_TEST_MARKER": str(marker)}),
        tmp_path,
    )
    assert r.returncode == 5
    assert "BOOTSTRAP_DOWNLOAD_FAILED" in r.stderr
    assert not marker.exists()


def test_explicit_checksum_file_is_used(tmp_path: Path):
    release = _release(tmp_path)
    (release / "SHA256SUMS").unlink()
    asset = release / "install.sh"
    digest = hashlib.sha256(asset.read_bytes()).hexdigest()
    custom = tmp_path / "custom.sha256"
    custom.write_text(f"{digest} *install.sh\n", encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    marker = _marker(tmp_path)
    r = _run(
        ["--checksum", str(custom), str(release)],
        _env(home, {"BOOTSTRAP_TEST_MARKER": str(marker)}),
        tmp_path,
    )
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert marker.is_file()


def test_dry_run_makes_no_download_execution_or_home_writes(tmp_path: Path):
    release = _release(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    (home / "tmp").mkdir()
    marker = _marker(tmp_path)
    prefix = tmp_path / "prefix"
    r = _run(
        ["--dry-run", "--profile", "dev", "--prefix", str(prefix), str(release)],
        _env(home, {"BOOTSTRAP_TEST_MARKER": str(marker)}),
        tmp_path,
    )
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert "QUBIT_HOME : untouched" in r.stdout
    assert "dry-run complete" in r.stdout
    assert not marker.exists(), "dry-run must not execute the installer"
    assert not prefix.exists(), "dry-run must not create the prefix"
    assert not (home / ".qubit").exists(), "dry-run must not touch QUBIT_HOME"
    assert not (home / ".config").exists()
    assert not (home / ".local").exists()


def test_dry_run_still_fails_closed_for_missing_local_checksum(tmp_path: Path):
    release = _release(tmp_path)
    (release / "SHA256SUMS").unlink()
    home = tmp_path / "home"
    home.mkdir()
    r = _run(["--dry-run", str(release)], _env(home), tmp_path)
    assert r.returncode == 7
    assert "BOOTSTRAP_CHECKSUM_UNAVAILABLE" in r.stderr


def test_runs_from_an_isolated_copy_without_a_checkout(tmp_path: Path):
    isolated = tmp_path / "isolated"
    isolated.mkdir()
    copy = isolated / "bootstrap.sh"
    copy.write_bytes(BOOTSTRAP.read_bytes())
    release = _release(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    marker = _marker(tmp_path)
    r = subprocess.run(
        [BASH, str(copy), str(release)],
        capture_output=True,
        text=True,
        env=_env(home, {"BOOTSTRAP_TEST_MARKER": str(marker)}),
        cwd=str(isolated),
        timeout=60,
    )
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert marker.is_file()


@pytest.mark.parametrize(
    ("uname", "needle"),
    [
        ("Darwin", "macOS"),
        ("MINGW64_NT-10.0", "Windows"),
        ("FreeBSD", "FreeBSD"),
    ],
)
def test_unsupported_hosts_are_typed_refusals(tmp_path: Path, uname: str, needle: str):
    release = _release(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    marker = _marker(tmp_path)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake_uname = bindir / "uname"
    fake_uname.write_text(f"#!/bin/sh\necho {uname}\n", encoding="utf-8")
    fake_uname.chmod(0o755)
    r = subprocess.run(
        [BASH, str(BOOTSTRAP), str(release)],
        capture_output=True,
        text=True,
        env=_env(home, {"BOOTSTRAP_TEST_MARKER": str(marker)}, path=str(bindir)),
        cwd=str(tmp_path),
        timeout=60,
    )
    assert r.returncode == 3
    assert "BOOTSTRAP_UNSUPPORTED_HOST" in r.stderr
    assert needle in r.stderr
    assert not marker.exists()


def test_missing_curl_for_remote_base_fails_typed(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for tool in ("bash", "uname", "mktemp", "rm"):
        real = shutil.which(tool)
        assert real, f"host is missing {tool}"
        (bindir / tool).symlink_to(real)
    r = subprocess.run(
        [
            BASH,
            str(BOOTSTRAP),
            "https://releases.invalid/qubit/v0.0.1",
        ],
        capture_output=True,
        text=True,
        env=_env(home, path=str(bindir)),
        cwd=str(tmp_path),
        timeout=60,
    )
    assert r.returncode == 4
    assert "BOOTSTRAP_MISSING_TOOL" in r.stderr
    assert "curl" in r.stderr

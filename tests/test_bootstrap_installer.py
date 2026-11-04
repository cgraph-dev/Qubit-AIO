"""Hermetic tests for ``scripts/bootstrap.sh`` (release one-line installer).

Every test uses a local ``file://``, directory, or loopback-HTTP release base.
No external network is touched. The downloaded artifact is a fake ``install.sh``
that records how it was invoked, so the tests prove that only the verified copy
runs.
"""
from __future__ import annotations

import functools
import hashlib
import http.server
import os
import shutil
import subprocess
import threading
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

_FAKE_SELF_REPORTING_INSTALLER = """#!/usr/bin/env bash
set -euo pipefail
cp -- "$0" "${BOOTSTRAP_TEST_SELF_COPY:?}"
{
  echo "self=$0"
  echo "profile=${QUBIT_PROFILE:-}"
  echo "args=$*"
} > "${BOOTSTRAP_TEST_MARKER:?}"
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


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, _format: str, *args: object) -> None:
        pass


@pytest.fixture
def http_release(tmp_path: Path):
    """Serve a local release over loopback HTTP (no external network)."""
    release = _release(tmp_path)
    handler = functools.partial(_QuietHandler, directory=str(release))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


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


def test_end_to_end_executes_only_a_temp_verified_copy(tmp_path: Path):
    """The full path: copy, verify, run a temp copy, clean up, don't install."""
    release = _release(tmp_path, _FAKE_SELF_REPORTING_INSTALLER)
    asset = release / "install.sh"
    home = tmp_path / "home"
    home.mkdir()
    marker = _marker(tmp_path)
    self_copy = tmp_path / "executed-install.sh"
    prefix = tmp_path / "opt"
    r = _run(
        ["--profile", "release", "--prefix", str(prefix), str(release)],
        _env(
            home,
            {
                "BOOTSTRAP_TEST_MARKER": str(marker),
                "BOOTSTRAP_TEST_SELF_COPY": str(self_copy),
            },
        ),
        tmp_path,
    )
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert "verified install.sh sha256=" in r.stdout
    assert not prefix.exists(), "bootstrap itself must not install anything"

    body = marker.read_text(encoding="utf-8")
    self_line = next(l for l in body.splitlines() if l.startswith("self="))
    executed = Path(self_line.split("=", 1)[1])
    assert executed != asset, "the release asset itself must never be executed"
    assert executed.name == "install.sh"
    assert home in executed.parents, f"temporary copy not under TMPDIR: {executed}"
    assert not executed.exists(), "the bootstrap temp dir must be cleaned up"
    assert "profile=release" in body
    assert f"args=--prefix {prefix}" in body

    assert self_copy.is_file()
    assert hashlib.sha256(self_copy.read_bytes()).hexdigest() == hashlib.sha256(
        asset.read_bytes()
    ).hexdigest(), "the executed copy must be byte-identical to the verified asset"
    assert asset.is_file(), "the release asset must be left untouched"


@pytest.mark.skipif(
    shutil.which("curl") is None, reason="curl is required for the download path"
)
def test_loopback_http_release_is_downloaded_then_executed(
    tmp_path: Path, http_release: str
):
    """Exercise the remote/curl branch against a loopback HTTP server."""
    home = tmp_path / "home"
    home.mkdir()
    marker = _marker(tmp_path)
    r = _run(
        [http_release],
        _env(home, {"BOOTSTRAP_TEST_MARKER": str(marker)}),
        tmp_path,
    )
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert f"downloading install.sh from {http_release}" in r.stdout
    assert "verified install.sh sha256=" in r.stdout
    assert marker.is_file(), "the downloaded and verified installer did not run"


def test_installer_failure_exit_status_is_propagated(tmp_path: Path):
    release = _release(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    marker = _marker(tmp_path)
    r = _run(
        [str(release)],
        _env(
            home,
            {
                "BOOTSTRAP_TEST_MARKER": str(marker),
                "BOOTSTRAP_TEST_EXIT": "9",
            },
        ),
        tmp_path,
    )
    assert r.returncode == 9
    assert marker.is_file(), "the installer must run before its status propagates"


def test_checksum_naming_another_asset_is_rejected(tmp_path: Path):
    release = _release(tmp_path)
    digest = hashlib.sha256((release / "install.sh").read_bytes()).hexdigest()
    (release / "SHA256SUMS").write_text(f"{digest}  other.sh\n", encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    marker = _marker(tmp_path)
    r = _run(
        [str(release)],
        _env(home, {"BOOTSTRAP_TEST_MARKER": str(marker)}),
        tmp_path,
    )
    assert r.returncode == 7
    assert "BOOTSTRAP_CHECKSUM_INVALID" in r.stderr
    assert not marker.exists()


def test_dry_run_missing_asset_fails_closed(tmp_path: Path):
    release = _release(tmp_path)
    (release / "install.sh").unlink()
    home = tmp_path / "home"
    home.mkdir()
    marker = _marker(tmp_path)
    r = _run(
        ["--dry-run", str(release)],
        _env(home, {"BOOTSTRAP_TEST_MARKER": str(marker)}),
        tmp_path,
    )
    assert r.returncode == 5
    assert "BOOTSTRAP_DOWNLOAD_FAILED" in r.stderr
    assert not marker.exists()

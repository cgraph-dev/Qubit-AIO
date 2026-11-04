"""Hermetic tests for ``scripts/bootstrap.sh`` (release one-line installer).

Every test uses a local ``file://``, directory, or loopback-HTTP release base.
No external network is touched. Each release fixture publishes the three
contract assets:

    install.sh              the downloaded, checksum-verified installer copy
    qubit-release.tar.gz    a source archive carrying scripts/install.sh
    SHA256SUMS              hashes for both, named

The installer is a fake that records how it was invoked and reads a marker from
the archive payload, so the tests prove that the extracted tree (not a source
checkout, and not the downloaded copy) is what actually runs.
"""
from __future__ import annotations

import functools
import hashlib
import http.server
import io
import os
import shutil
import subprocess
import tarfile
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
BOOTSTRAP = REPO / "scripts" / "bootstrap.sh"
BASH = shutil.which("bash") or "/bin/bash"
ARCHIVE_NAME = "qubit-release.tar.gz"
PAYLOAD = "archive-payload-ok"

_FAKE_INSTALLER = """#!/usr/bin/env bash
set -euo pipefail
script_dir="$(cd "$(dirname "$0")" && pwd)"
tree_root="$(cd "$script_dir/.." && pwd)"
{
  echo "invoked=1"
  echo "profile=${QUBIT_PROFILE:-}"
  echo "args=$*"
  echo "self=$0"
  echo "tree_root=$tree_root"
  echo "payload=$(cat "$tree_root/payload/archive-marker.txt" 2>/dev/null || echo missing)"
} > "${BOOTSTRAP_TEST_MARKER:?}"
exit "${BOOTSTRAP_TEST_EXIT:-0}"
"""

_FAKE_SELF_REPORTING_INSTALLER = """#!/usr/bin/env bash
set -euo pipefail
cp -- "$0" "${BOOTSTRAP_TEST_SELF_COPY:?}"
script_dir="$(cd "$(dirname "$0")" && pwd)"
tree_root="$(cd "$script_dir/.." && pwd)"
{
  echo "self=$0"
  echo "tree_root=$tree_root"
  echo "payload=$(cat "$tree_root/payload/archive-marker.txt" 2>/dev/null || echo missing)"
  echo "profile=${QUBIT_PROFILE:-}"
  echo "args=$*"
} > "${BOOTSTRAP_TEST_MARKER:?}"
"""

_CLEARED_ENV = ("QUBIT_HOME", "QUBIT_PREFIX", "QUBIT_PROFILE", "QUBIT_RELEASE_ARCHIVE")


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


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_archive(stage: Path, archive: Path, installer: str, prefixed: bool) -> None:
    root = stage / "Qubit-0.0.1" if prefixed else stage
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    (root / "scripts" / "install.sh").write_text(installer, encoding="utf-8")
    (root / "payload").mkdir(parents=True, exist_ok=True)
    (root / "payload" / "archive-marker.txt").write_text(PAYLOAD + "\n", encoding="utf-8")
    with tarfile.open(archive, "w:gz") as tar:
        if prefixed:
            tar.add(root, arcname=root.name)
        else:
            for child in sorted(root.iterdir()):
                tar.add(child, arcname=child.name)


def _release(
    tmp_path: Path,
    installer: str = _FAKE_INSTALLER,
    *,
    prefixed: bool = True,
    archive_installer: str | None = None,
    checksum_lines: list[str] | None = None,
) -> Path:
    release = tmp_path / "release"
    release.mkdir()
    asset = release / "install.sh"
    asset.write_text(installer, encoding="utf-8")

    stage = tmp_path / "stage"
    stage.mkdir()
    archive = release / ARCHIVE_NAME
    _write_archive(
        stage, archive, archive_installer if archive_installer is not None else installer, prefixed
    )

    if checksum_lines is None:
        checksum_lines = [
            f"{_sha(asset)}  install.sh",
            f"{_sha(archive)}  {ARCHIVE_NAME}",
        ]
    (release / "SHA256SUMS").write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")
    return release


def _marker(tmp_path: Path) -> Path:
    return tmp_path / "invocation.txt"


def _marker_body(path: Path) -> dict[str, str]:
    body = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            body[key] = value
    return body


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
        "--archive NAME",
        "--checksum FILE",
    ):
        assert flag in r.stdout, f"{flag} missing from --help"
    assert ARCHIVE_NAME in r.stdout
    assert "SHA256SUMS" in r.stdout
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


def test_local_release_runs_the_installer_from_the_extracted_tree(tmp_path: Path):
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
    assert f"verified {ARCHIVE_NAME} sha256=" in r.stdout
    assert "verified extracted scripts/install.sh sha256=" in r.stdout
    assert marker.is_file(), "the extracted installer did not run"
    body = _marker_body(marker)
    assert body["profile"] == "dev"
    assert body["args"] == f"--prefix {prefix}"
    assert body["payload"] == PAYLOAD
    assert home in Path(body["tree_root"]).parents, "installer did not run from TMPDIR"
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
    assert _marker_body(marker)["payload"] == PAYLOAD


def test_flat_archive_layout_is_supported(tmp_path: Path):
    release = _release(tmp_path, prefixed=False)
    home = tmp_path / "home"
    home.mkdir()
    marker = _marker(tmp_path)
    r = _run(
        [str(release)],
        _env(home, {"BOOTSTRAP_TEST_MARKER": str(marker)}),
        tmp_path,
    )
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert _marker_body(marker)["payload"] == PAYLOAD


def test_custom_archive_name_is_supported(tmp_path: Path):
    release = _release(tmp_path)
    (release / ARCHIVE_NAME).rename(release / "qubit-src.tgz")
    asset = release / "install.sh"
    archive = release / "qubit-src.tgz"
    (release / "SHA256SUMS").write_text(
        f"{_sha(asset)}  install.sh\n{_sha(archive)}  qubit-src.tgz\n",
        encoding="utf-8",
    )
    home = tmp_path / "home"
    home.mkdir()
    marker = _marker(tmp_path)
    r = _run(
        ["--archive", "qubit-src.tgz", str(release)],
        _env(home, {"BOOTSTRAP_TEST_MARKER": str(marker)}),
        tmp_path,
    )
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert _marker_body(marker)["payload"] == PAYLOAD


def test_install_checksum_mismatch_refuses_to_execute(tmp_path: Path):
    release = _release(tmp_path)
    lines = (release / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    lines[0] = "0" * 64 + "  install.sh"
    (release / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")
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


def test_archive_checksum_mismatch_refuses_to_execute(tmp_path: Path):
    release = _release(tmp_path)
    lines = (release / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    lines[1] = "0" * 64 + f"  {ARCHIVE_NAME}"
    (release / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")
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
    assert ARCHIVE_NAME in r.stderr
    assert not marker.exists(), "an archive checksum failure must not execute anything"


def test_archive_installer_content_mismatch_refuses_to_execute(tmp_path: Path):
    release = _release(
        tmp_path,
        archive_installer="#!/usr/bin/env bash\necho 'tampered archive installer'\n",
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
    assert "extracted scripts/install.sh" in r.stderr
    assert not marker.exists()


def test_missing_archive_fails_closed(tmp_path: Path):
    release = _release(tmp_path)
    (release / ARCHIVE_NAME).unlink()
    home = tmp_path / "home"
    home.mkdir()
    marker = _marker(tmp_path)
    r = _run(
        [str(release)],
        _env(home, {"BOOTSTRAP_TEST_MARKER": str(marker)}),
        tmp_path,
    )
    assert r.returncode == 5
    assert "BOOTSTRAP_ARCHIVE_MISSING" in r.stderr
    assert not marker.exists()


def test_archive_not_named_in_checksum_fails_closed(tmp_path: Path):
    release = _release(tmp_path)
    asset = release / "install.sh"
    (release / "SHA256SUMS").write_text(f"{_sha(asset)}  install.sh\n", encoding="utf-8")
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
    assert ARCHIVE_NAME in r.stderr
    assert not marker.exists()


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


def test_missing_installer_fails_closed(tmp_path: Path):
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
    archive = release / ARCHIVE_NAME
    custom = tmp_path / "custom.sha256"
    custom.write_text(
        f"{_sha(asset)} *install.sh\n{_sha(archive)} *{ARCHIVE_NAME}\n",
        encoding="utf-8",
    )
    home = tmp_path / "home"
    home.mkdir()
    marker = _marker(tmp_path)
    r = _run(
        ["--checksum", str(custom), str(release)],
        _env(home, {"BOOTSTRAP_TEST_MARKER": str(marker)}),
        tmp_path,
    )
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert _marker_body(marker)["payload"] == PAYLOAD


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
    assert ARCHIVE_NAME in r.stdout
    assert "extract" in r.stdout
    assert "dry-run complete" in r.stdout
    assert not marker.exists(), "dry-run must not execute the installer"
    assert not prefix.exists(), "dry-run must not create the prefix"
    assert not (home / ".qubit").exists(), "dry-run must not touch QUBIT_HOME"
    assert not (home / ".config").exists()
    assert not (home / ".local").exists()
    assert not list(home.glob("qubit-bootstrap.*")), "dry-run must not extract anything"


def test_dry_run_still_fails_closed_for_missing_local_checksum(tmp_path: Path):
    release = _release(tmp_path)
    (release / "SHA256SUMS").unlink()
    home = tmp_path / "home"
    home.mkdir()
    r = _run(["--dry-run", str(release)], _env(home), tmp_path)
    assert r.returncode == 7
    assert "BOOTSTRAP_CHECKSUM_UNAVAILABLE" in r.stderr


def test_dry_run_missing_archive_fails_closed(tmp_path: Path):
    release = _release(tmp_path)
    (release / ARCHIVE_NAME).unlink()
    home = tmp_path / "home"
    home.mkdir()
    marker = _marker(tmp_path)
    r = _run(
        ["--dry-run", str(release)],
        _env(home, {"BOOTSTRAP_TEST_MARKER": str(marker)}),
        tmp_path,
    )
    assert r.returncode == 5
    assert "BOOTSTRAP_ARCHIVE_MISSING" in r.stderr
    assert not marker.exists()


def test_dry_run_missing_installer_fails_closed(tmp_path: Path):
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
    body = _marker_body(marker)
    assert body["payload"] == PAYLOAD
    assert home in Path(body["tree_root"]).parents
    assert not (isolated / "scripts").exists(), "no source tree was read or created"


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
    hash_tool = next(
        (tool for tool in ("sha256sum", "shasum", "openssl") if shutil.which(tool)),
        None,
    )
    assert hash_tool, "host is missing a SHA-256 tool"
    for tool in ("bash", "uname", "mktemp", "rm", hash_tool):
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


def test_end_to_end_executes_only_a_temp_extracted_verified_copy(tmp_path: Path):
    """The full path: download, verify, extract, run the extracted copy, clean up."""
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

    body = _marker_body(marker)
    executed = Path(body["self"])
    assert executed != asset, "the release asset itself must never be executed"
    assert executed.name == "install.sh"
    assert home in executed.parents, f"extracted copy not under TMPDIR: {executed}"
    assert "release" in executed.parts, f"installer not run from the extracted tree: {executed}"
    assert body["payload"] == PAYLOAD
    assert not executed.exists(), "the bootstrap temp dir must be cleaned up"
    assert body["profile"] == "release"
    assert body["args"] == f"--prefix {prefix}"

    assert self_copy.is_file()
    assert _sha(self_copy) == _sha(asset), (
        "the executed copy must be byte-identical to the verified asset"
    )
    assert asset.is_file(), "the release asset must be left untouched"


def test_unsafe_archive_path_is_rejected(tmp_path: Path):
    release = _release(tmp_path)
    archive = release / ARCHIVE_NAME
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo("../escape.txt")
        payload = b"escape"
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    asset = release / "install.sh"
    (release / "SHA256SUMS").write_text(
        f"{_sha(asset)}  install.sh\n{_sha(archive)}  {ARCHIVE_NAME}\n",
        encoding="utf-8",
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
    assert "BOOTSTRAP_ARCHIVE_INVALID" in r.stderr
    assert not marker.exists()
    assert not (home / "escape.txt").exists()
    assert not (tmp_path / "escape.txt").exists()


def test_archive_without_installer_is_rejected(tmp_path: Path):
    release = _release(tmp_path)
    archive = release / ARCHIVE_NAME
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo("Qubit-0.0.1/README.md")
        payload = b"no installer here"
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    asset = release / "install.sh"
    (release / "SHA256SUMS").write_text(
        f"{_sha(asset)}  install.sh\n{_sha(archive)}  {ARCHIVE_NAME}\n",
        encoding="utf-8",
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
    assert "BOOTSTRAP_ARCHIVE_INVALID" in r.stderr
    assert "scripts/install.sh" in r.stderr
    assert not marker.exists()


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
    assert f"downloading {ARCHIVE_NAME} from {http_release}" in r.stdout
    assert "verified install.sh sha256=" in r.stdout
    assert f"verified {ARCHIVE_NAME} sha256=" in r.stdout
    assert _marker_body(marker)["payload"] == PAYLOAD


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
    digest = _sha(release / "install.sh")
    (release / "SHA256SUMS").write_text(
        f"{digest}  other.sh\n{_sha(release / ARCHIVE_NAME)}  {ARCHIVE_NAME}\n",
        encoding="utf-8",
    )
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

"""Hermetic tests for ``scripts/bootstrap.ps1`` (Windows release installer).

Every test uses a local directory, ``file://`` URL, or loopback HTTP release
base. No external network is touched. Each fixture publishes the Windows
contract:

    install.ps1            the downloaded, checksum-verified installer copy
    qubit-release.zip      a source archive carrying scripts/install.ps1
    SHA256SUMS             hashes for both, named

The installer is a fake that records how it was invoked and reads a marker
from the archive payload, so the tests prove that the extracted tree (not a
source checkout, and not the downloaded copy) is what actually runs.

These run on Linux under PowerShell 7. The real Windows installer still
enforces Windows itself; no Windows 5.1 host or real install is claimed here.
"""
from __future__ import annotations

import functools
import hashlib
import http.server
import os
import shutil
import subprocess
import threading
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
BOOTSTRAP = REPO / "scripts" / "bootstrap.ps1"
CMD = REPO / "scripts" / "bootstrap.cmd"
ARCHIVE_NAME = "qubit-release.zip"
ASSET_NAME = "install.ps1"
PAYLOAD = "archive-payload-ok"

_FAKE_INSTALLER = r"""
$scriptDir = Split-Path -Parent $PSCommandPath
$treeRoot = Split-Path -Parent $scriptDir
$payload = ''
$markerPath = Join-Path $treeRoot 'payload/archive-marker.txt'
if (Test-Path -LiteralPath $markerPath) {
    $payload = (Get-Content -Raw -LiteralPath $markerPath).Trim()
}
$lines = @(
    'invoked=1',
    ('profile=' + $env:QUBIT_PROFILE),
    ('args=' + ($args -join '|')),
    ('self=' + $PSCommandPath),
    ('tree_root=' + $treeRoot),
    ('payload=' + $payload)
)
Set-Content -LiteralPath $env:BOOTSTRAP_TEST_MARKER -Value $lines
if ($env:BOOTSTRAP_TEST_EXIT) { exit [int]$env:BOOTSTRAP_TEST_EXIT }
exit 0
"""

_FAKE_SELF_REPORTING_INSTALLER = r"""
Copy-Item -LiteralPath $PSCommandPath -Destination $env:BOOTSTRAP_TEST_SELF_COPY -Force
$scriptDir = Split-Path -Parent $PSCommandPath
$treeRoot = Split-Path -Parent $scriptDir
$payload = ''
$markerPath = Join-Path $treeRoot 'payload/archive-marker.txt'
if (Test-Path -LiteralPath $markerPath) {
    $payload = (Get-Content -Raw -LiteralPath $markerPath).Trim()
}
$lines = @(
    ('self=' + $PSCommandPath),
    ('tree_root=' + $treeRoot),
    ('payload=' + $payload),
    ('profile=' + $env:QUBIT_PROFILE),
    ('args=' + ($args -join '|'))
)
Set-Content -LiteralPath $env:BOOTSTRAP_TEST_MARKER -Value $lines
exit 0
"""

_CLEARED_ENV = (
    "QUBIT_HOME",
    "QUBIT_PREFIX",
    "QUBIT_PROFILE",
    "QUBIT_RELEASE_ARCHIVE",
    "QUBIT_RELEASE_ASSET",
    "QUBIT_BOOTSTRAP",
    "BOOTSTRAP_TEST_MARKER",
    "BOOTSTRAP_TEST_SELF_COPY",
    "BOOTSTRAP_TEST_EXIT",
)


def _powershell_host() -> str | None:
    """Return a working PowerShell host, or None so the module skips.

    A mise shim can exist while having no configured version; treating that
    as a host would turn an environment problem into a false failure.
    """
    for candidate in (shutil.which("pwsh"), shutil.which("powershell")):
        if not candidate:
            continue
        try:
            probe = subprocess.run(
                [candidate, "-NoProfile", "-Command", "exit 0"],
                capture_output=True,
                text=True,
                timeout=30,
                stdin=subprocess.DEVNULL,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if probe.returncode == 0:
            return candidate
    return None


HOST = _powershell_host()
pytestmark = pytest.mark.skipif(
    HOST is None, reason="no working PowerShell host (pwsh/powershell) on this runner"
)


def _env(home: Path, extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    for key in _CLEARED_ENV:
        env.pop(key, None)
    env["HOME"] = str(home)
    env["TMPDIR"] = str(home)
    if extra:
        env.update(extra)
    return env


def _run(args: list[str], env: dict[str, str], cwd: Path, timeout: int = 120):
    assert HOST is not None
    return subprocess.run(
        [
            HOST,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(BOOTSTRAP),
            *args,
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cwd),
        timeout=timeout,
        stdin=subprocess.DEVNULL,
    )


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_archive(stage: Path, archive: Path, installer: str, prefixed: bool) -> None:
    root = stage / "Qubit-0.0.1" if prefixed else stage
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    (root / "scripts" / "install.ps1").write_text(installer, encoding="utf-8")
    (root / "payload").mkdir(parents=True, exist_ok=True)
    (root / "payload" / "archive-marker.txt").write_text(PAYLOAD + "\n", encoding="utf-8")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            if prefixed:
                arcname = path.relative_to(stage).as_posix()
            else:
                arcname = path.relative_to(root).as_posix()
            bundle.write(path, arcname)


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
    asset = release / ASSET_NAME
    asset.write_text(installer, encoding="utf-8")

    stage = tmp_path / "stage"
    stage.mkdir()
    archive = release / ARCHIVE_NAME
    _write_archive(
        stage,
        archive,
        archive_installer if archive_installer is not None else installer,
        prefixed,
    )

    if checksum_lines is None:
        checksum_lines = [
            f"{_sha(asset)}  {ASSET_NAME}",
            f"{_sha(archive)}  {ARCHIVE_NAME}",
        ]
    (release / "SHA256SUMS").write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")
    return release


def _rewrite_archive(archive: Path, installer: str) -> None:
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("Qubit-0.0.1/scripts/install.ps1", installer)
        bundle.writestr("Qubit-0.0.1/README.md", "no installer marker")


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


def test_bootstrap_ps1_is_safe_to_fetch_as_a_string():
    raw = BOOTSTRAP.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), (
        "bootstrap.ps1 starts with a UTF-8 BOM; irm | iex cannot parse it on "
        "Windows PowerShell 5.1"
    )
    assert all(byte < 0x80 for byte in raw), (
        "bootstrap.ps1 must stay ASCII-only: a BOM-less 5.1 script is decoded "
        "with the ANSI code page"
    )
    src = BOOTSTRAP.read_text(encoding="utf-8")
    first_statement = next(
        line.strip()
        for line in src.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    assert first_statement.startswith("[CmdletBinding"), first_statement


def test_bootstrap_ps1_parses_with_the_real_powershell_parser(tmp_path: Path):
    harness = tmp_path / "parse-bootstrap.ps1"
    harness.write_text(
        "param([string]$Path)\n"
        "$tokens = $null\n"
        "$errors = $null\n"
        "[System.Management.Automation.Language.Parser]::ParseFile("
        "$Path, [ref]$tokens, [ref]$errors) | Out-Null\n"
        "if ($errors.Count) {\n"
        "  $errors | Select-Object -First 8 | ForEach-Object {\n"
        "    Write-Output ('  {0}: {1}' -f $_.Extent.StartLineNumber, $_.Message)\n"
        "  }\n"
        "  exit 1\n"
        "}\n"
        "Write-Output 'PS SYNTAX OK'\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            HOST,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(harness),
            str(BOOTSTRAP),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        stdin=subprocess.DEVNULL,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PS SYNTAX OK" in result.stdout


def test_help_lists_the_windows_contract(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    r = _run(["-Help"], _env(home), tmp_path)
    assert r.returncode == 0, r.stderr
    for flag in (
        "-Help",
        "-DryRun",
        "-Profile NAME",
        "-Prefix DIR",
        "-QubitHome DIR",
        "-NoModels",
        "-NoDashboard",
        "-Yes",
        "-WithSystemd",
        "-Archive NAME",
        "-ChecksumFile FILE",
    ):
        assert flag in r.stdout, f"{flag} missing from -Help"
    assert ARCHIVE_NAME in r.stdout
    assert "SHA256SUMS" in r.stdout
    assert "qubit-release.zip" in r.stdout


def test_unknown_option_exits_2_with_guidance(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    r = _run(["-DefinitelyNotAnOption"], _env(home), tmp_path)
    assert r.returncode == 2
    assert "BOOTSTRAP_USAGE" in r.stderr
    assert "-Help" in r.stderr


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
        ["-Profile", "dev", "-Prefix", str(prefix), str(release)],
        _env(home, {"BOOTSTRAP_TEST_MARKER": str(marker)}),
        tmp_path,
    )
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert f"verified {ASSET_NAME} sha256=" in r.stdout
    assert f"verified {ARCHIVE_NAME} sha256=" in r.stdout
    assert "verified extracted scripts/install.ps1 sha256=" in r.stdout
    assert marker.is_file(), "the extracted installer did not run"
    body = _marker_body(marker)
    assert body["profile"] == "dev"
    assert body["args"] == f"-QubitHome|{prefix}"
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
    (release / ARCHIVE_NAME).rename(release / "qubit-src.zip")
    asset = release / ASSET_NAME
    archive = release / "qubit-src.zip"
    (release / "SHA256SUMS").write_text(
        f"{_sha(asset)}  {ASSET_NAME}\n{_sha(archive)}  qubit-src.zip\n",
        encoding="utf-8",
    )
    home = tmp_path / "home"
    home.mkdir()
    marker = _marker(tmp_path)
    r = _run(
        ["-Archive", "qubit-src.zip", str(release)],
        _env(home, {"BOOTSTRAP_TEST_MARKER": str(marker)}),
        tmp_path,
    )
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert _marker_body(marker)["payload"] == PAYLOAD


def test_install_checksum_mismatch_refuses_to_execute(tmp_path: Path):
    release = _release(tmp_path)
    lines = (release / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    lines[0] = "0" * 64 + f"  {ASSET_NAME}"
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
        archive_installer="Write-Output 'tampered archive installer'\nexit 0\n",
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
    assert "extracted scripts/install.ps1" in r.stderr
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
    asset = release / ASSET_NAME
    (release / "SHA256SUMS").write_text(f"{_sha(asset)}  {ASSET_NAME}\n", encoding="utf-8")
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
    (release / ASSET_NAME).unlink()
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
    asset = release / ASSET_NAME
    archive = release / ARCHIVE_NAME
    custom = tmp_path / "custom.sha256"
    custom.write_text(
        f"{_sha(asset)} *{ASSET_NAME}\n{_sha(archive)} *{ARCHIVE_NAME}\n",
        encoding="utf-8",
    )
    home = tmp_path / "home"
    home.mkdir()
    marker = _marker(tmp_path)
    r = _run(
        ["-ChecksumFile", str(custom), str(release)],
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
        ["-DryRun", "-Profile", "dev", "-Prefix", str(prefix), str(release)],
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
    assert not list(home.glob("qubit-bootstrap.*")), "dry-run must not extract anything"


def test_dry_run_still_fails_closed_for_missing_local_checksum(tmp_path: Path):
    release = _release(tmp_path)
    (release / "SHA256SUMS").unlink()
    home = tmp_path / "home"
    home.mkdir()
    r = _run(["-DryRun", str(release)], _env(home), tmp_path)
    assert r.returncode == 7
    assert "BOOTSTRAP_CHECKSUM_UNAVAILABLE" in r.stderr


def test_dry_run_missing_archive_fails_closed(tmp_path: Path):
    release = _release(tmp_path)
    (release / ARCHIVE_NAME).unlink()
    home = tmp_path / "home"
    home.mkdir()
    marker = _marker(tmp_path)
    r = _run(
        ["-DryRun", str(release)],
        _env(home, {"BOOTSTRAP_TEST_MARKER": str(marker)}),
        tmp_path,
    )
    assert r.returncode == 5
    assert "BOOTSTRAP_ARCHIVE_MISSING" in r.stderr
    assert not marker.exists()


def test_dry_run_missing_installer_fails_closed(tmp_path: Path):
    release = _release(tmp_path)
    (release / ASSET_NAME).unlink()
    home = tmp_path / "home"
    home.mkdir()
    marker = _marker(tmp_path)
    r = _run(
        ["-DryRun", str(release)],
        _env(home, {"BOOTSTRAP_TEST_MARKER": str(marker)}),
        tmp_path,
    )
    assert r.returncode == 5
    assert "BOOTSTRAP_DOWNLOAD_FAILED" in r.stderr
    assert not marker.exists()


def test_runs_from_an_isolated_copy_without_a_checkout(tmp_path: Path):
    isolated = tmp_path / "isolated"
    isolated.mkdir()
    copy = isolated / "bootstrap.ps1"
    copy.write_bytes(BOOTSTRAP.read_bytes())
    release = _release(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    marker = _marker(tmp_path)
    r = subprocess.run(
        [
            HOST,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(copy),
            str(release),
        ],
        capture_output=True,
        text=True,
        env=_env(home, {"BOOTSTRAP_TEST_MARKER": str(marker)}),
        cwd=str(isolated),
        timeout=120,
        stdin=subprocess.DEVNULL,
    )
    assert r.returncode == 0, (r.stdout, r.stderr)
    body = _marker_body(marker)
    assert body["payload"] == PAYLOAD
    assert home in Path(body["tree_root"]).parents
    assert not (isolated / "scripts").exists(), "no source tree was read or created"


def test_end_to_end_executes_only_a_temp_extracted_verified_copy(tmp_path: Path):
    release = _release(tmp_path, _FAKE_SELF_REPORTING_INSTALLER)
    asset = release / ASSET_NAME
    home = tmp_path / "home"
    home.mkdir()
    marker = _marker(tmp_path)
    self_copy = tmp_path / "executed-install.ps1"
    prefix = tmp_path / "opt"
    r = _run(
        ["-Profile", "release", "-Prefix", str(prefix), str(release)],
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
    assert f"verified {ASSET_NAME} sha256=" in r.stdout
    assert not prefix.exists(), "bootstrap itself must not install anything"

    body = _marker_body(marker)
    executed = Path(body["self"])
    assert executed != asset, "the release asset itself must never be executed"
    assert executed.name == ASSET_NAME
    assert home in executed.parents, f"extracted copy not under TMPDIR: {executed}"
    assert "release" in executed.parts, f"installer not run from the extracted tree: {executed}"
    assert body["payload"] == PAYLOAD
    assert not executed.exists(), "the bootstrap temp dir must be cleaned up"
    assert body["profile"] == "release"
    assert body["args"] == f"-QubitHome|{prefix}"

    assert self_copy.is_file()
    assert _sha(self_copy) == _sha(asset), (
        "the executed copy must be byte-identical to the verified asset"
    )
    assert asset.is_file(), "the release asset must be left untouched"


def test_unsafe_archive_path_is_rejected(tmp_path: Path):
    release = _release(tmp_path)
    archive = release / ARCHIVE_NAME
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("Qubit-0.0.1/scripts/install.ps1", _FAKE_INSTALLER)
        bundle.writestr("../escape.txt", "escape")
    asset = release / ASSET_NAME
    (release / "SHA256SUMS").write_text(
        f"{_sha(asset)}  {ASSET_NAME}\n{_sha(archive)}  {ARCHIVE_NAME}\n",
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
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("Qubit-0.0.1/README.md", "no installer here")
    asset = release / ASSET_NAME
    (release / "SHA256SUMS").write_text(
        f"{_sha(asset)}  {ASSET_NAME}\n{_sha(archive)}  {ARCHIVE_NAME}\n",
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
    assert "scripts/install.ps1" in r.stderr
    assert not marker.exists()


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
    digest = _sha(release / ASSET_NAME)
    (release / "SHA256SUMS").write_text(
        f"{digest}  other.ps1\n{_sha(release / ARCHIVE_NAME)}  {ARCHIVE_NAME}\n",
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


def test_prefix_and_qubit_home_must_agree(tmp_path: Path):
    release = _release(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    r = _run(
        [
            "-Prefix",
            str(tmp_path / "one"),
            "-QubitHome",
            str(tmp_path / "two"),
            str(release),
        ],
        _env(home),
        tmp_path,
    )
    assert r.returncode == 2
    assert "BOOTSTRAP_USAGE" in r.stderr
    assert "different roots" in r.stderr


def test_with_systemd_is_refused_with_a_typed_error(tmp_path: Path):
    release = _release(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    r = _run(["-WithSystemd", str(release)], _env(home), tmp_path)
    assert r.returncode == 2
    assert "BOOTSTRAP_OPTION_UNSUPPORTED" in r.stderr
    assert "systemd" in r.stderr


def test_cmd_wrapper_forwards_to_the_powershell_bootstrap():
    text = CMD.read_text(encoding="utf-8")
    assert "bootstrap.ps1" in text
    assert "Qubit" in text
    assert "cgraph-dev/Qubit" in text
    assert "Hermes Agent Installer" not in text
    assert "NousResearch" not in text
    assert "%*" in text, "the CMD wrapper must forward bootstrap flags"
    assert "EnableDelayedExpansion" in text, (
        "the CMD wrapper must read !ERRORLEVEL! inside its if-block"
    )


@pytest.mark.skipif(
    HOST is None, reason="PowerShell is required for the download path"
)
def test_loopback_http_release_is_downloaded_then_executed(
    tmp_path: Path, http_release: str
):
    """Exercise the remote/Invoke-WebRequest branch against loopback HTTP."""
    home = tmp_path / "home"
    home.mkdir()
    marker = _marker(tmp_path)
    r = _run(
        [http_release],
        _env(home, {"BOOTSTRAP_TEST_MARKER": str(marker)}),
        tmp_path,
    )
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert f"downloading {ASSET_NAME} from {http_release}" in r.stdout
    assert f"downloading {ARCHIVE_NAME} from {http_release}" in r.stdout
    assert f"verified {ASSET_NAME} sha256=" in r.stdout
    assert f"verified {ARCHIVE_NAME} sha256=" in r.stdout
    assert _marker_body(marker)["payload"] == PAYLOAD


@pytest.mark.parametrize(
    "entry",
    [
        "/absolute.txt",
        "C:/drive.txt",
        "Qubit-0.0.1/scripts/../escape.txt",
        "Qubit-0.0.1/evil:stream",
    ],
)
def test_unsafe_zip_entry_names_are_rejected(tmp_path: Path, entry: str):
    release = _release(tmp_path)
    archive = release / ARCHIVE_NAME
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("Qubit-0.0.1/scripts/install.ps1", _FAKE_INSTALLER)
        bundle.writestr(entry, "escape")
    asset = release / ASSET_NAME
    (release / "SHA256SUMS").write_text(
        f"{_sha(asset)}  {ASSET_NAME}\n{_sha(archive)}  {ARCHIVE_NAME}\n",
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


def test_scriptblock_form_does_not_exit_the_callers_shell(tmp_path: Path):
    """The fetched form must not close the host shell on success."""
    release = _release(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    marker = _marker(tmp_path)
    harness = tmp_path / "iex-harness.ps1"
    harness.write_text(
        "param([string]$BootPath, [string]$BasePath)\n"
        "$code = Get-Content -Raw -LiteralPath $BootPath\n"
        "$block = [scriptblock]::Create($code)\n"
        "& $block -ReleaseBase $BasePath\n"
        "Write-Output ('BOOTSTRAP_LASTEXITCODE=' + $global:LASTEXITCODE)\n"
        "Write-Output 'HARNESS_CONTINUED'\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            HOST,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(harness),
            "-BootPath",
            str(BOOTSTRAP),
            "-BasePath",
            str(release),
        ],
        capture_output=True,
        text=True,
        env=_env(home, {"BOOTSTRAP_TEST_MARKER": str(marker)}),
        cwd=str(tmp_path),
        timeout=120,
        stdin=subprocess.DEVNULL,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "HARNESS_CONTINUED" in result.stdout
    assert "BOOTSTRAP_LASTEXITCODE=0" in result.stdout
    assert marker.is_file()
    assert _marker_body(marker)["payload"] == PAYLOAD


def test_bootstrap_declares_the_documented_flags_and_aliases():
    src = BOOTSTRAP.read_text(encoding="utf-8")
    assert "PositionalBinding = $false" in src
    assert "[Parameter(Position = 0)]" in src
    for name in ("$ReleaseBase", "$DryRun", "$Prefix", "$QubitHome", "$ProfileName",
                 "$NoModels", "$NoDashboard", "$Yes", "$WithSystemd",
                 "$ArchiveName", "$AssetName", "$ChecksumFile"):
        assert name in src, f"{name} missing from the param block"
    for alias in (
        "Alias('WithoutModels', 'SkipModels')",
        "Alias('WithoutDashboard', 'SkipDashboard')",
        "Alias('Profile')",
        "Alias('y')",
    ):
        assert alias in src, f"alias missing: {alias}"
    assert "QUBIT_RELEASE_ARCHIVE" in src
    assert "QUBIT_RELEASE_ASSET" in src


def test_symlink_zip_entry_is_rejected(tmp_path: Path):
    release = _release(tmp_path)
    archive = release / ARCHIVE_NAME
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("Qubit-0.0.1/scripts/install.ps1", _FAKE_INSTALLER)
        link = zipfile.ZipInfo("Qubit-0.0.1/link.ps1")
        link.external_attr = 0o120777 << 16
        bundle.writestr(link, "install.ps1")
    asset = release / ASSET_NAME
    (release / "SHA256SUMS").write_text(
        f"{_sha(asset)}  {ASSET_NAME}\n{_sha(archive)}  {ARCHIVE_NAME}\n",
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
    assert "symlink" in r.stderr
    assert not marker.exists()


def test_windows_file_url_drive_path_is_normalized(tmp_path: Path):
    harness = tmp_path / "dot-source.ps1"
    harness.write_text(
        "param([string]$BootPath)\n"
        ". $BootPath\n"
        "Write-Output (Convert-FileUrlToPath 'file:///C:/releases/v1')\n"
        "Write-Output (Convert-FileUrlToPath 'file:///tmp/releases/v1')\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            HOST,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(harness),
            "-BootPath",
            str(BOOTSTRAP),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        stdin=subprocess.DEVNULL,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    lines = result.stdout.splitlines()
    assert "C:/releases/v1" in lines
    assert "/tmp/releases/v1" in lines

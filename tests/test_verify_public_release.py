"""Hermetic tests for ``scripts/verify_public_release.py``.

Every release fixture is a local directory (optionally served over loopback
HTTP or paired with a loopback GitHub-API stub).  No test contacts GitHub or
any other external network.
"""
from __future__ import annotations

import functools
import hashlib
import http.server
import importlib.util
import io
import json
import sys
import tarfile
import threading
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "verify_public_release.py"
VERSION = "0.0.2"
TAG = "v0.0.2"
SOURCE_COMMIT = "a" * 40
INSTALLER = b"#!/usr/bin/env bash\nexit 0\n"


def _load():
    spec = importlib.util.spec_from_file_location("verify_public_release", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tar_bytes(files: dict[str, bytes], prefix: str | None) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for rel, data in sorted(files.items()):
            name = "%s/%s" % (prefix, rel) if prefix else rel
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mtime = 0
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _wheel_bytes(version: str) -> bytes:
    buffer = io.BytesIO()
    dist = "qubit_voicebridge-%s.dist-info" % version
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            dist + "/METADATA",
            "Metadata-Version: 2.1\nName: qubit-voicebridge\nVersion: %s\n" % version,
        )
    return buffer.getvalue()


def _sdist_bytes(version: str) -> bytes:
    root = "qubit_voicebridge-%s" % version
    return _tar_bytes(
        {
            "PKG-INFO": (
                "Metadata-Version: 2.1\nName: qubit-voicebridge\nVersion: %s\n"
                % version
            ).encode("utf-8")
        },
        root,
    )


def _source_files(
    version: str,
    installer: bytes,
    source_commit: str,
    extra: dict[str, bytes] | None,
) -> dict[str, bytes]:
    files = {
        "scripts/install.sh": installer,
        "VERSION": (version + "\n").encode("utf-8"),
        "PUBLIC_TREE_MANIFEST.md": (
            "# Public tree export manifest\n\n"
            "## Source\n\n"
            "- Commit: `%s`\n"
            "- Tree: `%s`\n" % (source_commit, "b" * 40)
        ).encode("utf-8"),
        "README.md": b"# Qubit\n",
    }
    if extra:
        files.update(extra)
    return files


def _build_release(
    tmp_path: Path,
    *,
    version: str = VERSION,
    installer: bytes = INSTALLER,
    source_installer: bytes | None = None,
    release_installer: bytes | None = None,
    wheel_version: str | None = None,
    sdist_version: str | None = None,
    source_commit: str = SOURCE_COMMIT,
    source_extra: dict[str, bytes] | None = None,
    omit: set[str] | None = None,
) -> Path:
    release = tmp_path / "release"
    release.mkdir()
    omit = omit or set()
    wheel_name = "qubit_voicebridge-%s-py3-none-any.whl" % version
    sdist_name = "qubit_voicebridge-%s.tar.gz" % version
    source_name = "Qubit-%s-source.tar.gz" % version
    archive_name = "qubit-release.tar.gz"

    payloads: dict[str, bytes] = {
        "bootstrap.sh": b"#!/usr/bin/env bash\nexit 0\n",
        "install.sh": installer,
        wheel_name: _wheel_bytes(wheel_version or version),
        sdist_name: _sdist_bytes(sdist_version or version),
        archive_name: _tar_bytes(
            _source_files(
                version,
                release_installer if release_installer is not None else installer,
                source_commit,
                None,
            ),
            "Qubit-%s" % version,
        ),
        source_name: _tar_bytes(
            _source_files(
                version,
                source_installer if source_installer is not None else installer,
                source_commit,
                source_extra,
            ),
            "Qubit-%s" % version,
        ),
        "component-manifest.json": json.dumps(
            {
                "schema": "qubit-component-manifest/1",
                "product": {
                    "name": "Qubit",
                    "distribution": "qubit-voicebridge",
                    "version": version,
                    "tag": "v" + version,
                },
                "source": {"commit": source_commit},
            },
            sort_keys=True,
        ).encode("utf-8"),
        "release-notes.md": b"# Qubit\n",
    }
    for name, data in payloads.items():
        if name in omit:
            continue
        (release / name).write_bytes(data)

    lines = []
    for path in sorted(release.iterdir()):
        if path.name == "SHA256SUMS" or path.name in omit:
            continue
        lines.append("%s  %s" % (_sha_file(path), path.name))
    (release / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return release


def _zip_bytes(files: dict[str, bytes], prefix: str | None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for rel, data in sorted(files.items()):
            name = "%s/%s" % (prefix, rel) if prefix else rel
            info = zipfile.ZipInfo(name, date_time=(2000, 1, 1, 0, 0, 0))
            archive.writestr(info, data)
    return buffer.getvalue()


def _add_platform_assets(release: Path, version: str, installer: bytes) -> None:
    (release / "bootstrap.ps1").write_bytes(b"# windows bootstrap\n")
    (release / "install.ps1").write_bytes(installer)
    (release / "qubit-release.zip").write_bytes(
        _zip_bytes(
            {
                "scripts/install.ps1": installer,
                "VERSION": (version + "\n").encode("utf-8"),
            },
            "Qubit-%s" % version,
        )
    )
    lines = []
    for path in sorted(release.iterdir()):
        if path.name == "SHA256SUMS":
            continue
        lines.append("%s  %s" % (_sha_file(path), path.name))
    (release / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _checks(report: dict) -> dict[str, dict]:
    return {check["name"]: check for check in report["checks"]}


def _verify(release: Path, extra: list[str] | None = None) -> tuple[int, dict]:
    module = _load()
    args = ["--release-base", str(release), "--tag", TAG, "--json"]
    if extra:
        args.extend(extra)
    import contextlib
    import io as _io

    buffer = _io.StringIO()
    with contextlib.redirect_stdout(buffer):
        rc = module.main(args)
    text = buffer.getvalue()
    return rc, json.loads(text)


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, _format: str, *args: object) -> None:
        pass


@pytest.fixture
def http_release(tmp_path: Path):
    release = _build_release(tmp_path)
    handler = functools.partial(_QuietHandler, directory=str(release))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:%d/" % server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


class _ApiHandler(http.server.BaseHTTPRequestHandler):
    payload: dict = {}
    status = 200

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
        if self.status != 200:
            self.send_response(self.status)
            self.end_headers()
            return
        body = json.dumps(self.payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *args: object) -> None:
        pass


def _serve_api(payload: dict, status: int = 200):
    handler = type(
        "ApiHandler",
        (_ApiHandler,),
        {"payload": payload, "status": status},
    )
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_local_release_passes_and_writes_nothing(tmp_path: Path):
    release = _build_release(tmp_path)
    before = {
        path.name: (path.stat().st_size, path.stat().st_mtime_ns, _sha_file(path))
        for path in release.iterdir()
    }
    rc, report = _verify(release)
    assert rc == 0
    assert report["verdict"] == "PASS"
    assert report["version"] == VERSION
    assert report["source_commit"] == SOURCE_COMMIT
    after = {
        path.name: (path.stat().st_size, path.stat().st_mtime_ns, _sha_file(path))
        for path in release.iterdir()
    }
    assert after == before


def test_release_passes_over_loopback_http(http_release: str):
    module = _load()
    args = ["--release-base", http_release, "--tag", TAG, "--json"]
    import contextlib
    import io as _io

    buffer = _io.StringIO()
    with contextlib.redirect_stdout(buffer):
        rc = module.main(args)
    report = json.loads(buffer.getvalue())
    assert rc == 0, report
    assert report["verdict"] == "PASS"
    assert _checks(report)["release_base_reachable"]["ok"]


def test_checksum_mismatch_fails(tmp_path: Path):
    release = _build_release(tmp_path)
    wheel = release / ("qubit_voicebridge-%s-py3-none-any.whl" % VERSION)
    wheel.write_bytes(wheel.read_bytes() + b"tampered")
    rc, report = _verify(release)
    assert rc == 1
    assert report["verdict"] == "FAIL"
    failed = [name for name, check in _checks(report).items() if not check["ok"]]
    assert any(name.startswith("checksum_") for name in failed)


def test_missing_required_asset_fails(tmp_path: Path):
    release = _build_release(tmp_path, omit={"bootstrap.sh"})
    rc, report = _verify(release)
    assert rc == 1
    checks = _checks(report)
    assert checks["asset_bootstrap.sh"]["ok"] is False
    assert checks["checksums_cover_required_assets"]["ok"] is False


def test_archive_installer_must_match_the_installer_asset(tmp_path: Path):
    release = _build_release(tmp_path, release_installer=b"#!/bin/sh\ndifferent\n")
    rc, report = _verify(release)
    assert rc == 1
    failed = [name for name, check in _checks(report).items() if not check["ok"]]
    assert any(name.startswith("archive_installer_") for name in failed)


def test_source_archive_rejects_operator_paths(tmp_path: Path):
    release = _build_release(
        tmp_path, source_extra={"leak.txt": b"see /home/trick/private\n"}
    )
    rc, report = _verify(release)
    assert rc == 1
    assert _checks(report)["source_archive_content"]["ok"] is False


def test_source_archive_rejects_node_modules(tmp_path: Path):
    release = _build_release(
        tmp_path, source_extra={"node_modules/pkg/index.js": b"module.exports = 1\n"}
    )
    rc, report = _verify(release)
    assert rc == 1
    assert _checks(report)["source_archive_metadata"]["ok"] is False


def test_source_archive_rejects_secret_files(tmp_path: Path):
    release = _build_release(tmp_path, source_extra={".env": b"SECRET=1\n"})
    rc, report = _verify(release)
    assert rc == 1
    assert _checks(report)["source_archive_secret_files"]["ok"] is False


def test_source_archive_rejects_secret_content(tmp_path: Path):
    release = _build_release(
        tmp_path, source_extra={"token.txt": b"ghp_" + b"a" * 36 + b"\n"}
    )
    rc, report = _verify(release)
    assert rc == 1
    assert _checks(report)["source_archive_content"]["ok"] is False



def test_source_archive_rejects_unscanned_large_file(tmp_path: Path):
    release = _build_release(
        tmp_path,
        source_extra={"large.dat": b"x" * (512 * 1024 + 1)},
    )
    rc, report = _verify(release)
    assert rc == 1
    coverage = _checks(report)["source_archive_scan_coverage"]
    assert coverage["ok"] is False
    assert "large.dat" in coverage["detail"]


def test_source_archive_rejects_exhausted_scan_budget(tmp_path: Path):
    module = _load()
    archive = tmp_path / "source.tar.gz"
    archive.write_bytes(_tar_bytes({"a.txt": b"abcd", "b.txt": b"efgh"}, "source"))
    original = module.MAX_SCAN_TOTAL_BYTES
    try:
        module.MAX_SCAN_TOTAL_BYTES = 5
        result = module.scan_archive(archive)
    finally:
        module.MAX_SCAN_TOTAL_BYTES = original
    assert result["scanned_files"] == 1
    assert result["skipped_files"] == ["source/b.txt"]
    assert result["scanned_bytes"] <= 5



def test_source_archive_rejects_secret_hidden_in_large_file(tmp_path: Path):
    payload = b"x" * (512 * 1024) + b"ghp_" + b"a" * 36
    release = _build_release(tmp_path, source_extra={"large-secret.txt": payload})
    rc, report = _verify(release)
    assert rc == 1
    checks = _checks(report)
    # Even when the secret matcher cannot inspect the file, verification must
    # not report a safe release on the strength of an incomplete scan.
    assert checks["source_archive_scan_coverage"]["ok"] is False


def test_source_archive_complete_scan_reports_coverage(tmp_path: Path):
    release = _build_release(tmp_path)
    rc, report = _verify(release)
    assert rc == 0, report
    assert _checks(report)["source_archive_scan_coverage"]["ok"] is True

def test_source_archive_allows_documented_placeholders(tmp_path: Path):
    release = _build_release(
        tmp_path,
        source_extra={
            "doc.md": (
                b"# GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx\n"
                b"# SLACK_BOT_TOKEN=xoxb-...\n"
                b"setter.call(input, \"xoxb-raw-secret-value\");\n"
            )
        },
    )
    rc, report = _verify(release)
    assert rc == 0, report
    assert _checks(report)["source_archive_content"]["ok"] is True


def test_wheel_and_sdist_versions_are_checked(tmp_path: Path):
    release = _build_release(tmp_path, wheel_version="9.9.9", sdist_version="9.9.9")
    rc, report = _verify(release)
    assert rc == 1
    checks = _checks(report)
    assert checks["wheel_metadata"]["ok"] is False
    assert checks["sdist_metadata"]["ok"] is False


def test_expected_source_commit_mismatch_fails(tmp_path: Path):
    release = _build_release(tmp_path)
    rc, report = _verify(release, ["--expect-source-commit", "c" * 40])
    assert rc == 1
    checks = _checks(report)
    assert checks["source_commit_identity"]["ok"] is False
    assert checks["component_manifest_source"]["ok"] is False


def test_github_api_mode_checks_release_identity(tmp_path: Path):
    release = _build_release(tmp_path)
    payload = {
        "tag_name": TAG,
        "draft": False,
        "prerelease": False,
        "assets": [{"name": path.name} for path in release.iterdir()],
    }
    server, thread = _serve_api(payload)
    try:
        module = _load()
        args = [
            "--repo",
            "acme/qubit",
            "--tag",
            TAG,
            "--api-base",
            "http://127.0.0.1:%d" % server.server_address[1],
            "--release-base",
            str(release),
            "--json",
        ]
        import contextlib
        import io as _io

        buffer = _io.StringIO()
        with contextlib.redirect_stdout(buffer):
            rc = module.main(args)
        report = json.loads(buffer.getvalue())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)
    assert rc == 0, report
    checks = _checks(report)
    assert checks["release_exists"]["ok"] is True
    assert checks["release_tag_identity"]["ok"] is True
    assert checks["release_published"]["ok"] is True


def test_github_api_missing_release_fails(tmp_path: Path):
    release = _build_release(tmp_path)
    server, thread = _serve_api({}, status=404)
    try:
        module = _load()
        args = [
            "--repo",
            "acme/qubit",
            "--tag",
            TAG,
            "--api-base",
            "http://127.0.0.1:%d" % server.server_address[1],
            "--release-base",
            str(release),
            "--json",
        ]
        import contextlib
        import io as _io

        buffer = _io.StringIO()
        with contextlib.redirect_stdout(buffer):
            rc = module.main(args)
        report = json.loads(buffer.getvalue())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)
    assert rc == 1
    assert _checks(report)["release_exists"]["ok"] is False


def test_release_page_url_is_normalized_to_download_base():
    module = _load()
    assert (
        module.normalize_release_base(
            "https://github.com/acme/qubit/releases/tag/v0.0.2"
        )
        == "https://github.com/acme/qubit/releases/download/v0.0.2"
    )
    assert (
        module.normalize_release_base(
            "https://github.com/acme/qubit/releases/latest"
        )
        == "https://github.com/acme/qubit/releases/latest/download"
    )


def test_platform_assets_are_optional_by_default(tmp_path: Path):
    release = _build_release(tmp_path)
    rc, report = _verify(release)
    assert rc == 0, report
    assert report["verdict"] == "PASS"


def test_platform_assets_can_be_required(tmp_path: Path):
    release = _build_release(tmp_path)
    rc, report = _verify(release, ["--require-platform-assets"])
    checks = _checks(report)
    assert rc == 1
    assert checks["asset_bootstrap.ps1"]["ok"] is False
    assert checks["asset_install.ps1"]["ok"] is False
    assert checks["asset_qubit-release.zip"]["ok"] is False


def test_platform_assets_pass_when_present_and_consistent(tmp_path: Path):
    release = _build_release(tmp_path)
    _add_platform_assets(release, VERSION, b"# windows installer\nexit 0\n")
    rc, report = _verify(release, ["--require-platform-assets"])
    checks = _checks(report)
    assert rc == 0, report
    assert checks["asset_bootstrap.ps1"]["ok"] is True
    assert checks["asset_install.ps1"]["ok"] is True
    assert checks["asset_qubit-release.zip"]["ok"] is True
    assert checks["archive_installer_windows"]["ok"] is True
    assert checks["archive_version_windows"]["ok"] is True


def test_windows_archive_installer_must_match_the_asset(tmp_path: Path):
    release = _build_release(tmp_path)
    _add_platform_assets(release, VERSION, b"# windows installer\nexit 0\n")
    (release / "install.ps1").write_bytes(b"# different installer\n")
    lines = []
    for path in sorted(release.iterdir()):
        if path.name == "SHA256SUMS":
            continue
        lines.append("%s  %s" % (_sha_file(path), path.name))
    (release / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")
    rc, report = _verify(release, ["--require-platform-assets"])
    checks = _checks(report)
    assert rc == 1
    assert checks["archive_installer_windows"]["ok"] is False


def test_infer_version_accepts_a_pep440_respelt_of_one_version():
    """One release, two legal spellings: the archive keeps the tag's shape, the
    wheel carries PEP 440's normalised form of the same version."""
    module = _load()
    assert module.infer_version({"Qubit-0.21.4-2-source.tar.gz"}) == "0.21.4-2"
    assert module.infer_version({"qubit_voicebridge-0.21.4.post2-py3-none-any.whl"}) == "0.21.4-2"
    assert module.infer_version({
        "Qubit-0.21.4-2-source.tar.gz",
        "qubit_voicebridge-0.21.4.post2-py3-none-any.whl",
    }) == "0.21.4-2"
    assert module.infer_version({"qubit_voicebridge-1.0rc1-py3-none-any.whl"}) == "1.0-rc1"
    # Two genuinely different versions is still ambiguous, never a silent pick.
    assert module.infer_version({
        "Qubit-0.21.4-source.tar.gz",
        "qubit_voicebridge-0.21.5-py3-none-any.whl",
    }) is None


@pytest.mark.parametrize(
    "name,linkname",
    [
        ("../escaped.txt", None),
        ("/absolute.txt", None),
        (r"folder\\escaped.txt", None),
        ("safe/link", "../../outside"),
        ("safe/link", "/etc/passwd"),
        ("safe/link", r"..\\outside"),
    ],
)
def test_archive_scan_rejects_escaping_paths_and_links(tmp_path, name, linkname):
    verifier = _load()
    archive = tmp_path / "malicious.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        entry = tarfile.TarInfo(name)
        if linkname is None:
            entry.size = 4
            tar.addfile(entry, io.BytesIO(b"safe"))
        else:
            entry.type = tarfile.SYMTYPE
            entry.linkname = linkname
            tar.addfile(entry)
    report = verifier.scan_archive(archive)
    assert report["metadata"], (name, linkname)


@pytest.mark.parametrize("entry_type", [tarfile.CHRTYPE, tarfile.BLKTYPE, tarfile.FIFOTYPE])
def test_archive_scan_rejects_device_and_fifo_entries(tmp_path, entry_type):
    verifier = _load()
    archive = tmp_path / "special-entry.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        entry = tarfile.TarInfo("release/unsafe-device")
        entry.type = entry_type
        entry.devmajor = 1
        entry.devminor = 3
        tar.addfile(entry)
    report = verifier.scan_archive(archive)
    assert any("unsupported archive entry type" in item for item in report["metadata"])


@pytest.mark.parametrize("second_name", ["release/config.txt", "release/./config.txt"])
def test_archive_scan_rejects_duplicate_normalized_paths(tmp_path, second_name):
    verifier = _load()
    archive = tmp_path / "duplicate.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for name, payload in [("release/config.txt", b"first"), (second_name, b"second")]:
            entry = tarfile.TarInfo(name)
            entry.size = len(payload)
            tar.addfile(entry, io.BytesIO(payload))
    report = verifier.scan_archive(archive)
    assert any("duplicate archive path" in item for item in report["metadata"])


def test_archive_scan_rejects_member_beneath_symlink_even_if_link_comes_later(tmp_path):
    verifier = _load()
    archive = tmp_path / "linked-parent.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        child = tarfile.TarInfo("release/alias/overwritten.txt")
        child.size = 7
        tar.addfile(child, io.BytesIO(b"payload"))
        link = tarfile.TarInfo("release/alias")
        link.type = tarfile.SYMTYPE
        link.linkname = "real-directory"
        tar.addfile(link)
    report = verifier.scan_archive(archive)
    assert any("archive path traverses a link" in item for item in report["metadata"])


def test_archive_scan_rejects_file_used_as_parent_directory(tmp_path):
    verifier = _load()
    archive = tmp_path / "file-parent.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for name in ("release/config", "release/config/settings.json"):
            payload = b"data"
            entry = tarfile.TarInfo(name)
            entry.size = len(payload)
            tar.addfile(entry, io.BytesIO(payload))
    report = verifier.scan_archive(archive)
    assert any("non-directory parent" in item for item in report["metadata"])

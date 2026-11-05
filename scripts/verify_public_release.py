#!/usr/bin/env python3
"""Verify a published Qubit release without changing anything.

This is the read-only consumer-side check for the release assets produced by
``scripts/prepare_production_release.py`` and the public promotion flow.  It
can verify either a GitHub release (``--repo`` + ``--tag``) or any release base
that carries the published assets (an HTTPS URL, ``file://`` URL, or local
directory).

It checks, in order:

* the release exists and the tag is published (not a draft/prerelease);
* the tag/version identity agrees across the release, wheel, sdist, source
  archive and (when present) ``component-manifest.json``;
* every required asset exists: ``bootstrap.sh``, ``install.sh``,
  ``qubit-release.tar.gz``, ``SHA256SUMS``, a wheel, an sdist and the source
  archive;
* ``SHA256SUMS`` names every required asset and every listed checksum matches;
* the source archives carry ``scripts/install.sh`` byte-identical to the
  ``install.sh`` asset, plus a matching ``VERSION``;
* the source archive metadata/sample contains no operator home path,
  ``node_modules``/build directories, or secret files/content.

It downloads assets into a temporary directory and deletes that directory
afterwards.  It never writes to the release source, a Git checkout, a live
service, or a remote repository.

Usage::

    python3 scripts/verify_public_release.py --repo cgraph-dev/Qubit --tag v0.0.9
    python3 scripts/verify_public_release.py --repo cgraph-dev/Qubit --latest
    python3 scripts/verify_public_release.py \
        --release-base https://github.com/cgraph-dev/Qubit/releases/download/v0.0.9

Exit code is 0 only when every check passes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import posixpath
import re
import shutil
import sys
import tarfile
import tempfile
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Iterable

SCHEMA = "qubit-public-release-verification/1"
DEFAULT_API_BASE = "https://api.github.com"
USER_AGENT = "qubit-verify-public-release/1"
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[.-][0-9A-Za-z.-]+)?$")
TAG_RE = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+(?:[.-][0-9A-Za-z.-]+)?$")
CHECKSUM_RE = re.compile(r"^([0-9a-fA-F]{64}) [ *](.+)$")
PERSONAL_MARKER = b"/home/" + b"trick"
MAX_SCAN_FILE_BYTES = 512 * 1024
MAX_SCAN_TOTAL_BYTES = 64 * 1024 * 1024

FORBIDDEN_DIR_SEGMENTS = frozenset(
    {
        ".git",
        ".venv",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "__pycache__",
        "node_modules",
    }
)
FORBIDDEN_BASENAMES = frozenset(
    {
        ".env",
        "auth.json",
        "credentials.json",
        "id_ed25519",
        "id_rsa",
        "state.db",
    }
)
FORBIDDEN_SUFFIXES = (
    ".jks",
    ".key",
    ".p12",
    ".pem",
    ".secret",
    ".sqlite",
    ".sqlite3",
)
SECRET_PATTERNS = (
    re.compile(rb"-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----"),
    re.compile(rb"AKIA[0-9A-Z]{16}"),
    re.compile(rb"gh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(rb"github_pat_[A-Za-z0-9_]{40,}"),
    re.compile(rb"xox[baprs]-[A-Za-z0-9-]{20,}"),
)

EXACT_ASSETS = ("bootstrap.sh", "install.sh", "qubit-release.tar.gz", "SHA256SUMS")
PLATFORM_ASSETS = ("bootstrap.ps1", "install.ps1", "qubit-release.zip")


class VerifyError(SystemExit):
    """Typed refusal. The message is printed verbatim."""


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class Report:
    repo: str | None = None
    tag: str | None = None
    version: str | None = None
    source_commit: str | None = None
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append(Check(name=name, ok=bool(ok), detail=detail))

    @property
    def failed(self) -> list[Check]:
        return [check for check in self.checks if not check.ok]

    @property
    def verdict(self) -> str:
        return "FAIL" if self.failed else "PASS"

    def as_dict(self) -> dict:
        return {
            "schema": SCHEMA,
            "verdict": self.verdict,
            "repo": self.repo,
            "tag": self.tag,
            "version": self.version,
            "source_commit": self.source_commit,
            "checks": [
                {"name": check.name, "ok": check.ok, "detail": check.detail}
                for check in self.checks
            ],
        }


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative(name: str) -> str:
    """Reject absolute paths and traversal in an asset/checksum name."""
    if not name or "\x00" in name:
        raise VerifyError("PUBLIC_RELEASE_UNSAFE_NAME: %r" % name)
    pure = PurePosixPath(name)
    if pure.is_absolute() or any(part in ("", ".", "..") for part in pure.parts):
        raise VerifyError("PUBLIC_RELEASE_UNSAFE_NAME: %r" % name)
    return pure.as_posix()


def _join_url(base: str, name: str) -> str:
    return base.rstrip("/") + "/" + urllib.parse.quote(name, safe="")


def _close_quietly(exc: BaseException) -> None:
    """Close an HTTP error/response so Python does not warn on finalization."""
    close = getattr(exc, "close", None)
    if callable(close):
        try:
            close()
        except Exception:  # noqa: BLE001 - best-effort cleanup
            pass


def normalize_release_base(base: str) -> str:
    """Turn a release page/latest URL into an asset download base."""
    parsed = urllib.parse.urlparse(base)
    if parsed.scheme in ("http", "https"):
        path = parsed.path.rstrip("/")
        match = re.search(r"/releases/tag/([^/]+)$", path)
        if match:
            path = path[: match.start()] + "/releases/download/" + match.group(1)
        elif path.endswith("/releases/latest"):
            path = path + "/download"
        return urllib.parse.urlunparse(parsed._replace(path=path))
    return base


class Fetcher:
    """Read assets from a local directory, file:// URL, or HTTP(S) base."""

    def __init__(self, base: str, timeout: float = 60.0) -> None:
        self.base = normalize_release_base(base)
        self.timeout = timeout
        parsed = urllib.parse.urlparse(self.base)
        self.scheme = parsed.scheme
        self.parsed = parsed

    def _local_root(self) -> Path | None:
        if self.scheme == "file":
            return Path(urllib.request.url2pathname(self.parsed.path))
        if self.scheme == "":
            return Path(self.base).expanduser()
        return None

    def local_names(self) -> set[str]:
        root = self._local_root()
        if root is None or not root.is_dir():
            return set()
        return {child.name for child in root.iterdir() if child.is_file()}

    def read_bytes(self, name: str) -> bytes:
        _safe_relative(name)
        root = self._local_root()
        if root is not None:
            path = root / name
            if not path.is_file():
                raise VerifyError("PUBLIC_RELEASE_ASSET_MISSING: %s" % name)
            return path.read_bytes()
        if self.scheme in ("http", "https"):
            url = _join_url(self.base, name)
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    status = getattr(response, "status", 200)
                    if status != 200:
                        raise VerifyError(
                            "PUBLIC_RELEASE_FETCH_FAILED: %s HTTP %s" % (name, status)
                        )
                    return response.read()
            except VerifyError:
                raise
            except Exception as exc:  # noqa: BLE001 - typed network refusal
                _close_quietly(exc)
                raise VerifyError(
                    "PUBLIC_RELEASE_FETCH_FAILED: %s: %s" % (name, exc)
                ) from exc
        raise VerifyError("PUBLIC_RELEASE_UNSUPPORTED_BASE: %s" % self.base)

    def download(self, name: str, dest: Path) -> Path:
        _safe_relative(name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        root = self._local_root()
        if root is not None:
            source = root / name
            if not source.is_file():
                raise VerifyError("PUBLIC_RELEASE_ASSET_MISSING: %s" % name)
            shutil.copyfile(source, dest)
            return dest
        if self.scheme in ("http", "https"):
            url = _join_url(self.base, name)
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    status = getattr(response, "status", 200)
                    if status != 200:
                        raise VerifyError(
                            "PUBLIC_RELEASE_FETCH_FAILED: %s HTTP %s" % (name, status)
                        )
                    with dest.open("wb") as handle:
                        shutil.copyfileobj(response, handle, length=1024 * 1024)
            except VerifyError:
                raise
            except Exception as exc:  # noqa: BLE001 - typed network refusal
                _close_quietly(exc)
                raise VerifyError(
                    "PUBLIC_RELEASE_FETCH_FAILED: %s: %s" % (name, exc)
                ) from exc
            return dest
        raise VerifyError("PUBLIC_RELEASE_UNSUPPORTED_BASE: %s" % self.base)


def _api_json(url: str, timeout: float) -> dict:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - typed API refusal
        _close_quietly(exc)
        raise VerifyError("PUBLIC_RELEASE_API_FAILED: %s: %s" % (url, exc)) from exc


def parse_checksums(text: str) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = CHECKSUM_RE.match(line)
        if not match:
            raise VerifyError("PUBLIC_RELEASE_BAD_CHECKSUM_LINE: %r" % raw)
        digest, name = match.group(1).lower(), match.group(2).strip()
        _safe_relative(name)
        if name in seen:
            raise VerifyError("PUBLIC_RELEASE_DUPLICATE_CHECKSUM: %s" % name)
        seen.add(name)
        entries.append((name, digest))
    if not entries:
        raise VerifyError("PUBLIC_RELEASE_EMPTY_SHA256SUMS")
    return entries


def _single(candidates: Iterable[str], label: str) -> str | None:
    names = sorted(set(candidates))
    if len(names) > 1:
        raise VerifyError(
            "PUBLIC_RELEASE_AMBIGUOUS_ASSET: %s matches %s"
            % (label, ", ".join(names))
        )
    return names[0] if names else None


def find_assets(names: set[str], version: str,
                require_platform_assets: bool = False) -> dict[str, str | None]:
    assets = {name: (name if name in names else None) for name in EXACT_ASSETS}
    if require_platform_assets:
        for name in PLATFORM_ASSETS:
            assets[name] = name if name in names else None
    assets["wheel"] = _single(
        (
            name
            for name in names
            if re.fullmatch(
                r"qubit[_-]voicebridge-%s-.*\.whl" % re.escape(version), name
            )
        ),
        "wheel",
    ) or _single((name for name in names if name.endswith(".whl")), "wheel")
    assets["sdist"] = _single(
        (
            name
            for name in names
            if re.fullmatch(
                r"qubit[_-]voicebridge-%s\.tar\.gz" % re.escape(version), name
            )
        ),
        "sdist",
    )
    assets["source"] = _single(
        (name for name in names if name == "Qubit-%s-source.tar.gz" % version),
        "source",
    )
    return assets


def infer_version(names: Iterable[str]) -> str | None:
    found: set[str] = set()
    for name in names:
        for pattern in (
            r"^Qubit-(.+)-source\.tar\.gz$",
            r"^qubit[_-]voicebridge-(.+)\.tar\.gz$",
            r"^qubit[_-]voicebridge-(.+)-py3-none-any\.whl$",
        ):
            match = re.match(pattern, name)
            if match and VERSION_RE.match(match.group(1)):
                found.add(match.group(1))
    return next(iter(found)) if len(found) == 1 else None


def _metadata_field(text: str, key: str) -> str | None:
    match = re.search(r"^%s:\s*(.+)$" % re.escape(key), text, re.MULTILINE | re.IGNORECASE)
    return match.group(1).strip() if match else None


def _find_tar_member(tar: tarfile.TarFile, suffix: str) -> tarfile.TarInfo | None:
    matches = [
        member
        for member in tar.getmembers()
        if member.name == suffix or member.name.endswith("/" + suffix)
    ]
    if not matches:
        return None
    return sorted(matches, key=lambda member: (len(member.name), member.name))[0]


def _read_tar_member(tar: tarfile.TarFile, member: tarfile.TarInfo) -> bytes:
    handle = tar.extractfile(member)
    if handle is None:
        raise VerifyError("PUBLIC_RELEASE_ARCHIVE_MEMBER_UNREADABLE: %s" % member.name)
    return handle.read()


def _archive_info(path: Path, version: str, install_sha: str) -> dict:
    info: dict = {"install_sha": None, "version": None, "source_commit": None}
    with tarfile.open(path, "r:gz") as tar:
        install = _find_tar_member(tar, "scripts/install.sh")
        if install is None:
            raise VerifyError(
                "PUBLIC_RELEASE_ARCHIVE_INSTALLER_MISSING: %s" % path.name
            )
        info["install_sha"] = _sha256_bytes(_read_tar_member(tar, install))

        version_member = _find_tar_member(tar, "VERSION")
        if version_member is None:
            raise VerifyError(
                "PUBLIC_RELEASE_ARCHIVE_VERSION_MISSING: %s" % path.name
            )
        info["version"] = _read_tar_member(tar, version_member).decode(
            "utf-8", "replace"
        ).strip()

        manifest_member = _find_tar_member(tar, "PUBLIC_TREE_MANIFEST.md")
        if manifest_member is not None:
            text = _read_tar_member(tar, manifest_member).decode("utf-8", "replace")
            commit = re.search(
                r"^-\s+(?:Source\s+)?Commit:\s+`([0-9a-fA-F]{7,40})`",
                text,
                re.MULTILINE,
            )
            if commit:
                info["source_commit"] = commit.group(1)
    return info


def _archive_zip_info(path: Path, install_name: str, install_sha: str) -> dict:
    """Read the Windows ZIP installer and VERSION the same way as the tar helper."""
    with zipfile.ZipFile(path) as bundle:
        matches = [name for name in bundle.namelist()
                   if name == "scripts/" + install_name
                   or name.endswith("/scripts/" + install_name)]
        if not matches:
            raise VerifyError(
                "PUBLIC_RELEASE_ARCHIVE_INSTALLER_MISSING: %s" % path.name
            )
        name = sorted(matches, key=lambda item: (len(item), item))[0]
        install_sha = _sha256_bytes(bundle.read(name))
        version_matches = [name for name in bundle.namelist()
                           if name == "VERSION" or name.endswith("/VERSION")]
        version = None
        if version_matches:
            version = bundle.read(
                sorted(version_matches, key=lambda item: (len(item), item))[0]
            ).decode("utf-8", "replace").strip()
    return {"install_sha": install_sha, "version": version,
            "source_commit": None}


def scan_archive(path: Path) -> dict:
    """Scan names, links and a bounded content sample for unsafe material."""
    metadata: list[str] = []
    secret_files: list[str] = []
    content: list[str] = []
    scanned_files = 0
    scanned_bytes = 0

    with tarfile.open(path, "r:gz") as tar:
        members = tar.getmembers()
        for member in members:
            parts = PurePosixPath(member.name).parts
            if any(part in FORBIDDEN_DIR_SEGMENTS for part in parts):
                metadata.append(member.name)
            if PERSONAL_MARKER.decode("utf-8", "replace") in member.name:
                metadata.append(member.name)
            if member.linkname and PERSONAL_MARKER.decode("utf-8", "replace") in member.linkname:
                metadata.append("%s -> %s" % (member.name, member.linkname))
            base = posixpath.basename(member.name)
            if base in FORBIDDEN_BASENAMES or base.endswith(FORBIDDEN_SUFFIXES):
                secret_files.append(member.name)

        for member in sorted(members, key=lambda item: item.name):
            if not member.isfile() or member.size > MAX_SCAN_FILE_BYTES:
                continue
            if scanned_bytes >= MAX_SCAN_TOTAL_BYTES:
                break
            handle = tar.extractfile(member)
            if handle is None:
                continue
            data = handle.read(MAX_SCAN_FILE_BYTES)
            scanned_files += 1
            scanned_bytes += len(data)
            if PERSONAL_MARKER in data:
                content.append("%s (operator path)" % member.name)
            for pattern in SECRET_PATTERNS:
                if pattern.search(data):
                    content.append("%s (secret pattern)" % member.name)
                    break

    return {
        "metadata": metadata,
        "secret_files": secret_files,
        "content": content,
        "scanned_files": scanned_files,
        "scanned_bytes": scanned_bytes,
    }


def _verify_wheel(path: Path, report: Report, version: str) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            metadata = [
                name
                for name in archive.namelist()
                if name.endswith(".dist-info/METADATA")
            ]
            if not metadata:
                raise VerifyError(
                    "PUBLIC_RELEASE_WHEEL_METADATA_MISSING: %s" % path.name
                )
            text = archive.read(metadata[0]).decode("utf-8", "replace")
    except VerifyError:
        raise
    except Exception as exc:  # noqa: BLE001 - typed archive refusal
        raise VerifyError(
            "PUBLIC_RELEASE_WHEEL_UNREADABLE: %s: %s" % (path.name, exc)
        ) from exc
    name = _metadata_field(text, "Name")
    wheel_version = _metadata_field(text, "Version")
    report.add(
        "wheel_metadata",
        wheel_version == version,
        "name=%s version=%s expected=%s" % (name, wheel_version, version),
    )


def _verify_sdist(path: Path, report: Report, version: str) -> None:
    try:
        with tarfile.open(path, "r:gz") as tar:
            member = _find_tar_member(tar, "PKG-INFO")
            if member is None:
                raise VerifyError(
                    "PUBLIC_RELEASE_SDIST_METADATA_MISSING: %s" % path.name
                )
            text = _read_tar_member(tar, member).decode("utf-8", "replace")
    except VerifyError:
        raise
    except Exception as exc:  # noqa: BLE001 - typed archive refusal
        raise VerifyError(
            "PUBLIC_RELEASE_SDIST_UNREADABLE: %s: %s" % (path.name, exc)
        ) from exc
    name = _metadata_field(text, "Name")
    sdist_version = _metadata_field(text, "Version")
    report.add(
        "sdist_metadata",
        sdist_version == version,
        "name=%s version=%s expected=%s" % (name, sdist_version, version),
    )


def _github_release(
    repo: str, tag: str | None, latest: bool, api_base: str, timeout: float
) -> dict:
    if latest:
        url = "%s/repos/%s/releases/latest" % (api_base.rstrip("/"), repo)
    else:
        url = "%s/repos/%s/releases/tags/%s" % (
            api_base.rstrip("/"),
            repo,
            urllib.parse.quote(tag or "", safe=""),
        )
    return _api_json(url, timeout)


def verify(
    *,
    repo: str | None,
    tag: str | None,
    latest: bool,
    release_base: str | None,
    api_base: str,
    version: str | None,
    expect_source_commit: str | None,
    timeout: float,
    require_platform_assets: bool = False,
) -> Report:
    report = Report(repo=repo, tag=tag)
    release: dict | None = None
    api_asset_names: set[str] = set()

    if repo:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            raise VerifyError("PUBLIC_RELEASE_BAD_REPO: %s" % repo)
        if not latest and not tag:
            raise VerifyError("PUBLIC_RELEASE_TAG_REQUIRED: --repo needs --tag or --latest")
        try:
            release = _github_release(repo, tag, latest, api_base, timeout)
        except VerifyError as exc:
            report.add("release_exists", False, str(exc))
            return report
        report.add("release_exists", True, "repo=%s" % repo)
        resolved_tag = str(release.get("tag_name") or tag or "")
        report.tag = resolved_tag or tag
        if tag:
            report.add(
                "release_tag_identity",
                resolved_tag == tag,
                "expected=%s observed=%s" % (tag, resolved_tag),
            )
        report.add(
            "release_published",
            not bool(release.get("draft")) and not bool(release.get("prerelease")),
            "draft=%s prerelease=%s" % (release.get("draft"), release.get("prerelease")),
        )
        api_asset_names = {
            str(asset.get("name"))
            for asset in (release.get("assets") or [])
            if asset.get("name")
        }
        if release_base is None:
            if latest:
                release_base = "https://github.com/%s/releases/latest/download" % repo
            else:
                release_base = "https://github.com/%s/releases/download/%s" % (
                    repo,
                    urllib.parse.quote(report.tag or "", safe=""),
                )

    if not release_base:
        raise VerifyError("PUBLIC_RELEASE_BASE_REQUIRED: pass --repo or --release-base")

    fetcher = Fetcher(release_base, timeout=timeout)
    try:
        checksum_text = fetcher.read_bytes("SHA256SUMS").decode("utf-8", "replace")
        checksums = parse_checksums(checksum_text)
    except VerifyError as exc:
        report.add("release_base_reachable", False, str(exc))
        return report
    report.add("release_base_reachable", True, release_base)

    checksum_names = {name for name, _ in checksums}
    names = set(checksum_names) | api_asset_names | fetcher.local_names()
    names.add("SHA256SUMS")
    if report.tag and not version:
        version = report.tag[1:] if report.tag.startswith("v") else report.tag
    version = version or infer_version(names)
    if not version or not VERSION_RE.match(version):
        raise VerifyError(
            "PUBLIC_RELEASE_VERSION_UNKNOWN: pass --version or --tag"
        )
    report.version = version
    if report.tag:
        report.add(
            "tag_version_identity",
            report.tag == "v" + version,
            "tag=%s version=%s" % (report.tag, version),
        )

    assets = find_assets(names, version, require_platform_assets=require_platform_assets)
    for label, name in assets.items():
        report.add(
            "asset_%s" % label,
            bool(name),
            name or "missing (expected %s)" % label,
        )

    required_checksum_names = {
        name for name in assets.values() if name and name != "SHA256SUMS"
    }
    missing_checksums = sorted(required_checksum_names - checksum_names)
    missing_required = sorted(
        label
        for label, name in assets.items()
        if name is None and label != "SHA256SUMS"
    )
    detail_parts = []
    if missing_required:
        detail_parts.append("missing=%s" % ", ".join(missing_required))
    if missing_checksums:
        detail_parts.append("unnamed=%s" % ", ".join(missing_checksums))
    report.add(
        "checksums_cover_required_assets",
        not missing_checksums and not missing_required,
        "; ".join(detail_parts) if detail_parts else "all required assets named",
    )
    if api_asset_names and assets:
        missing_api = sorted(
            name for name in assets.values() if name and name not in api_asset_names
        )
        report.add(
            "release_assets_api",
            not missing_api,
            ", ".join(missing_api) if missing_api else "all required assets published",
        )

    with tempfile.TemporaryDirectory(prefix="qubit-verify-release-") as tmp:
        root = Path(tmp)
        downloaded: dict[str, Path] = {}
        for name, expected in checksums:
            dest = root / name
            try:
                fetcher.download(name, dest)
            except VerifyError as exc:
                report.add("checksum_asset_%s" % name, False, str(exc))
                continue
            downloaded[name] = dest
            actual = _sha256_file(dest)
            report.add(
                "checksum_%s" % name,
                actual == expected,
                "sha256=%s expected=%s" % (actual, expected),
            )

        for label, name in assets.items():
            if label == "SHA256SUMS":
                continue
            report.add(
                "asset_exists_%s" % label,
                bool(name) and name in downloaded,
                name or "missing",
            )

        install_name = assets.get("install.sh")
        install_path = downloaded.get(install_name) if install_name else None
        if install_path is not None:
            install_sha = _sha256_file(install_path)
            for label in ("qubit-release.tar.gz", "source"):
                asset_name = assets.get(label)
                archive_path = downloaded.get(asset_name) if asset_name else None
                if archive_path is None:
                    report.add("archive_%s" % label, False, "archive not downloaded")
                    continue
                try:
                    info = _archive_info(archive_path, version, install_sha)
                    report.add(
                        "archive_installer_%s" % label,
                        info["install_sha"] == install_sha,
                        "%s scripts/install.sh sha256=%s asset=%s"
                        % (asset_name, info["install_sha"], install_sha),
                    )
                    report.add(
                        "archive_version_%s" % label,
                        info["version"] == version,
                        "%s VERSION=%s expected=%s"
                        % (asset_name, info["version"], version),
                    )
                    if info.get("source_commit"):
                        report.source_commit = info["source_commit"]
                        report.add(
                            "archive_source_identity_%s" % label,
                            True,
                            "%s source_commit=%s"
                            % (asset_name, info["source_commit"]),
                        )
                except VerifyError as exc:
                    report.add("archive_%s" % label, False, str(exc))
                except Exception as exc:  # noqa: BLE001 - typed archive refusal
                    report.add(
                        "archive_%s" % label,
                        False,
                        "%s: %s" % (asset_name, exc),
                    )

        source_name = assets.get("source")
        source_path = downloaded.get(source_name) if source_name else None
        if source_path is not None:
            try:
                scan = scan_archive(source_path)
                report.add(
                    "source_archive_metadata",
                    not scan["metadata"],
                    "forbidden=%s" % (", ".join(scan["metadata"][:10]) or "none"),
                )
                report.add(
                    "source_archive_secret_files",
                    not scan["secret_files"],
                    "files=%s" % (", ".join(scan["secret_files"][:10]) or "none"),
                )
                report.add(
                    "source_archive_content",
                    not scan["content"],
                    "issues=%s scanned_files=%d scanned_bytes=%d"
                    % (
                        ", ".join(scan["content"][:10]) or "none",
                        scan["scanned_files"],
                        scan["scanned_bytes"],
                    ),
                )
            except Exception as exc:  # noqa: BLE001 - typed archive refusal
                report.add("source_archive_scan", False, str(exc))

        windows_archive_name = assets.get("qubit-release.zip")
        windows_archive_path = (downloaded.get(windows_archive_name)
                                if windows_archive_name else None)
        windows_installer_name = assets.get("install.ps1")
        windows_installer_path = (downloaded.get(windows_installer_name)
                                  if windows_installer_name else None)
        if windows_archive_path is not None and windows_installer_path is not None:
            try:
                info = _archive_zip_info(
                    windows_archive_path, "install.ps1",
                    _sha256_file(windows_installer_path))
                report.add(
                    "archive_installer_windows",
                    info["install_sha"] == _sha256_file(windows_installer_path),
                    "%s scripts/install.ps1 sha256=%s asset=%s"
                    % (windows_archive_name, info["install_sha"],
                       _sha256_file(windows_installer_path)),
                )
                report.add(
                    "archive_version_windows",
                    info["version"] == version,
                    "%s VERSION=%s expected=%s"
                    % (windows_archive_name, info["version"], version),
                )
            except VerifyError as exc:
                report.add("archive_windows", False, str(exc))
            except Exception as exc:  # noqa: BLE001 - typed archive refusal
                report.add("archive_windows", False, str(exc))

        wheel_name = assets.get("wheel")
        if wheel_name and wheel_name in downloaded:
            try:
                _verify_wheel(downloaded[wheel_name], report, version)
            except VerifyError as exc:
                report.add("wheel_metadata", False, str(exc))

        sdist_name = assets.get("sdist")
        if sdist_name and sdist_name in downloaded:
            try:
                _verify_sdist(downloaded[sdist_name], report, version)
            except VerifyError as exc:
                report.add("sdist_metadata", False, str(exc))

        manifest_path = downloaded.get("component-manifest.json")
        if manifest_path is not None:
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                product = manifest.get("product") or {}
                source = manifest.get("source") or {}
                manifest_commit = source.get("commit")
                expected_tag = report.tag or ("v" + version)
                report.add(
                    "component_manifest_identity",
                    product.get("tag") == expected_tag
                    and product.get("version") == version,
                    "tag=%s expected_tag=%s version=%s"
                    % (product.get("tag"), expected_tag, product.get("version")),
                )
                if report.tag is None and product.get("tag"):
                    report.tag = str(product.get("tag"))
                if expect_source_commit and manifest_commit:
                    report.add(
                        "component_manifest_source",
                        manifest_commit == expect_source_commit,
                        "commit=%s expected=%s"
                        % (manifest_commit, expect_source_commit),
                    )
                if manifest_commit:
                    report.source_commit = report.source_commit or manifest_commit
            except Exception as exc:  # noqa: BLE001 - typed manifest refusal
                report.add("component_manifest_identity", False, str(exc))

    if expect_source_commit:
        report.add(
            "source_commit_identity",
            report.source_commit == expect_source_commit,
            "observed=%s expected=%s"
            % (report.source_commit, expect_source_commit),
        )
    return report


def _print_report(report: Report, as_json: bool) -> None:
    if as_json:
        print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
        return
    for check in report.checks:
        line = "%s %s" % ("PASS" if check.ok else "FAIL", check.name)
        if check.detail:
            line += " " + check.detail
        print(line)
    failures = report.failed
    identity = "repo=%s tag=%s version=%s" % (
        report.repo or "-",
        report.tag or "-",
        report.version or "-",
    )
    if failures:
        print("FAIL qubit-public-release %s failures=%d" % (identity, len(failures)))
    else:
        print("PASS qubit-public-release %s" % identity)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify a published Qubit release (read-only)."
    )
    parser.add_argument("--repo", default=None, help="GitHub repo, owner/name")
    parser.add_argument("--tag", default=None, help="release tag, e.g. v0.0.9")
    parser.add_argument("--latest", action="store_true", help="use the latest release")
    parser.add_argument(
        "--release-base",
        default=None,
        help="release asset base (HTTPS, file://, or local directory)",
    )
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    parser.add_argument("--version", default=None)
    parser.add_argument("--expect-source-commit", default=None)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--require-platform-assets", action="store_true",
                        help="require the Windows bootstrap assets too "
                             "(bootstrap.ps1, install.ps1, qubit-release.zip)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if not args.repo and not args.release_base:
        parser.error("pass --repo or --release-base")
    if args.repo and args.tag and args.latest:
        parser.error("--tag and --latest are mutually exclusive")
    if args.tag and not TAG_RE.match(args.tag):
        raise VerifyError("PUBLIC_RELEASE_BAD_TAG: %s" % args.tag)
    version = args.version.lstrip("v") if args.version else None
    if version and not VERSION_RE.match(version):
        raise VerifyError("PUBLIC_RELEASE_BAD_VERSION: %s" % args.version)

    try:
        report = verify(
            repo=args.repo,
            tag=args.tag,
            latest=args.latest,
            release_base=args.release_base,
            api_base=args.api_base,
            version=version,
            expect_source_commit=args.expect_source_commit,
            timeout=args.timeout,
            require_platform_assets=args.require_platform_assets,
        )
    except VerifyError as exc:
        print("FAIL qubit-public-release %s" % exc, file=sys.stderr)
        return 2

    _print_report(report, args.json)
    return 0 if not report.failed else 1


if __name__ == "__main__":
    raise SystemExit(main())

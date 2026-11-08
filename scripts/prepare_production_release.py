#!/usr/bin/env python3
"""Prepare the Qubit production release assets without publishing anything.

This is the operator-side companion to ``scripts/release_qualify.py``.  It
builds the canonical wheel + sdist, a sanitized public source archive, a
component manifest, ``SHA256SUMS`` and a draft GitHub release body, all under
one output directory.  It never creates or moves a Git tag, never pushes, and
never creates, edits or publishes a GitHub release.

The release version is the root ``pyproject.toml`` ``[project].version``.  A
release tag is the ``v<version>`` form and must be either absent locally (the
normal pre-tag case) or already pointing at ``HEAD``.  An existing tag is never
moved.

Usage::

    python3 scripts/prepare_production_release.py --out /tmp/qubit-production-release
    python3 scripts/prepare_production_release.py --out /tmp/qubit-production-release \\
        --tag v0.0.9 --verify

``--verify`` runs the full release qualifier (``--build --install
--reproduce --unsigned-ok``) and fails unless it returns PASS.  Without it the
script still builds the wheel/sdist and checks their content, but skips the
qualifier's clean-HOME install and reproducibility steps.

Independently of ``--verify``, the script always publishes the POSIX + Windows
bootstrap payloads and runs ``scripts/release_fresh_install_gate.py`` with
``--fresh-install --require-fresh-install``.  A DEGRADED or FAIL gate result
refuses the release: a prepared release cannot exist without a throwaway fresh
install that creates a root ``config.yaml`` with the default Ollama provider and
without the packaged dashboard/TUI assets.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]

SCHEMA = "qubit-component-manifest/1"
SOURCE_ARCHIVE_PREFIX = "Qubit-%s/"
DETERMINISTIC_MTIME = 946684800  # 2000-01-01T00:00:00Z, same as the exporter


class ReleaseError(SystemExit):
    pass


def _load_module(name: str, relpath: str):
    path = REPO / relpath
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ReleaseError("cannot load %s" % path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise ReleaseError(
            "git %s failed: %s" % (" ".join(args), proc.stderr.strip())
        )
    return proc.stdout.strip()


def _toml(path: Path) -> dict:
    import tomllib

    with open(path, "rb") as fh:
        return tomllib.load(fh)


def _product_version(repo: Path) -> str:
    data = _toml(repo / "pyproject.toml")
    version = str((data.get("project") or {}).get("version") or "").strip()
    if not version:
        raise ReleaseError("root pyproject.toml has no [project].version")
    return version


def resolve_release(repo: Path, version: str | None = None,
                    tag: str | None = None) -> dict:
    """Bind version/tag together and verify they agree with the package."""
    declared = _product_version(repo)
    version = (version or declared).lstrip("v")
    if version != declared:
        raise ReleaseError(
            "version %r does not match root pyproject version %r"
            % (version, declared)
        )
    tag = (tag or ("v" + version)).strip()
    if not tag.startswith("v") or len(tag) <= 1:
        raise ReleaseError("release tag %r is not the v<version> form" % tag)
    if tag[1:] != version:
        raise ReleaseError(
            "release tag %r does not match version %r" % (tag, version)
        )

    # voicebridge.release is the single source of truth for identity. Import
    # it from the repo being prepared, not from whatever is installed on the
    # host.
    repo_str = str(Path(repo).resolve())
    if repo_str not in sys.path:
        sys.path.insert(0, repo_str)
    from voicebridge import __version__ as package_version
    from voicebridge import release as R

    identity = R.version_identity(repo, tag, package_version=package_version)
    if identity.get("state") != "OK":
        raise ReleaseError("release identity failed: %s" % identity.get("detail"))
    return {
        "version": version,
        "tag": tag,
        "identity": identity,
    }


def check_tree(repo: Path) -> dict:
    status = _git(repo, "status", "--porcelain")
    if status:
        raise ReleaseError(
            "working tree is dirty; commit the release before building:\n" + status
        )
    head = _git(repo, "rev-parse", "HEAD")
    branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    return {"head": head, "branch": branch, "clean": True}


def check_tag(repo: Path, tag: str) -> dict:
    """Refuse to move an existing tag.

    An absent tag is the normal pre-tag state for local preparation.  An
    existing tag is allowed only when it already points at ``HEAD`` (the CI
    checkout of a tag).
    """
    proc = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", tag],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return {"tag": tag, "exists": False, "state": "ABSENT"}
    tagged = proc.stdout.strip()
    head = _git(repo, "rev-parse", "HEAD")
    if tagged != head:
        raise ReleaseError(
            "tag %s already exists at %s, not HEAD %s; refusing to move it"
            % (tag, tagged, head)
        )
    return {"tag": tag, "exists": True, "state": "AT_HEAD", "commit": tagged}


def _run(cmd: list[str], timeout: int = 3600, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(cwd) if cwd else None,
    )


def _ensure_dashboard(repo: Path, log: dict) -> None:
    index = repo / "frontend" / "qubit-dist" / "index.html"
    if index.is_file():
        log["dashboard"] = "present"
    else:
        script = repo / "scripts" / "build_dashboard.sh"
        proc = _run(["bash", str(script)], timeout=3600, cwd=repo)
        if proc.returncode != 0 or not index.is_file():
            raise ReleaseError(
                "dashboard build failed (rc=%s): %s"
                % (proc.returncode, (proc.stderr or proc.stdout)[-2000:])
            )
        log["dashboard"] = "built"

    # Record which commit produced the dashboard. This is part of the
    # component manifest, so a stale bundle is visible rather than guessed.
    info_script = repo / "frontend" / "qubit-web" / "scripts" / "write-build-info.mjs"
    if info_script.is_file():
        proc = _run(["node", str(info_script)], timeout=120, cwd=repo)
        if proc.returncode != 0:
            raise ReleaseError(
                "dashboard build-info failed: %s"
                % (proc.stderr or proc.stdout)[-1000:]
            )


def _release_qualify(repo: Path, out: Path, tag: str, verify: bool) -> dict:
    """Run the canonical qualifier and return its JSON report.

    ``--verify`` runs build+install+reproduce; otherwise only build.  Artifacts
    always land in ``out/qualify/build1``.
    """
    out.mkdir(parents=True, exist_ok=True)
    report_path = out / "release-report.json"
    cmd = [
        sys.executable,
        str(repo / "scripts" / "release_qualify.py"),
        "--build",
        "--unsigned-ok",
        "--tag", tag,
        "--json",
        "--out", str(out),
    ]
    if verify:
        cmd.insert(2, "--install")
        cmd.insert(3, "--reproduce")
    proc = _run(cmd, timeout=7200, cwd=repo)
    text = proc.stdout or ""
    try:
        report = json.loads(text)
    except json.JSONDecodeError:
        # Keep the raw output for the operator; never guess a verdict.
        (out / "release-report.raw.txt").write_text(text + "\n" + (proc.stderr or ""))
        raise ReleaseError(
            "release qualifier did not return JSON (rc=%s): %s"
            % (proc.returncode, (proc.stderr or text)[-2000:])
        )
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    if proc.returncode != 0 or report.get("verdict") != "PASS":
        raise ReleaseError(
            "release qualifier did not PASS: verdict=%s rc=%s"
            % (report.get("verdict"), proc.returncode)
        )
    return report


def _copy_artifacts(qualify_dir: Path, assets: Path) -> dict:
    build = qualify_dir / "build1"
    wheels = sorted(build.glob("*.whl"))
    sdists = sorted(build.glob("*.tar.gz"))
    if not wheels or not sdists:
        raise ReleaseError("release qualifier produced no wheel/sdist in %s" % build)
    assets.mkdir(parents=True, exist_ok=True)
    wheel = assets / wheels[0].name
    sdist = assets / sdists[0].name
    shutil.copy2(wheels[0], wheel)
    shutil.copy2(sdists[0], sdist)
    return {"wheel": wheel, "sdist": sdist}


def _export_source(repo: Path, version: str) -> tuple[Path, dict]:
    """Export the public tree into a temporary directory and return it."""
    exporter = _load_module("qubit_export_public_tree", "scripts/export_public_tree.py")
    tmp = Path(tempfile.mkdtemp(prefix="qubit-source-export-"))
    dest = tmp / "tree"
    commit, tree = exporter._source_identity(repo)
    tracked = exporter._tracked_paths(repo)
    personal = exporter._personal_path_files(repo, exporter.PERSONAL_PATH_MARKERS)
    symlinks = exporter._symlink_targets(repo)
    included, excluded, counts = exporter._select(tracked, personal, symlinks)
    report = exporter._build_report(
        repo, commit, tree, tracked, included, excluded, counts, personal, symlinks
    )
    exporter._ensure_dest(repo, dest)
    exporter._export(repo, dest, included)
    manifest = dest / "PUBLIC_TREE_MANIFEST.md"
    manifest.write_text(exporter._public_manifest(report), encoding="utf-8")
    os.utime(manifest, (exporter.DETERMINISTIC_MTIME, exporter.DETERMINISTIC_MTIME))
    return dest, report


def _tar_source(tree: Path, version: str, target: Path) -> None:
    """Write a deterministic tar.gz of an exported tree."""
    prefix = SOURCE_ARCHIVE_PREFIX % version
    with open(target, "wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw,
                           mtime=DETERMINISTIC_MTIME) as gz:
            with tarfile.open(fileobj=gz, mode="w",
                              format=tarfile.PAX_FORMAT) as tar:
                for path in sorted(tree.rglob("*")):
                    _add_member(tar, tree, path, prefix)


def _add_member(tar: tarfile.TarFile, tree: Path, path: Path,
                prefix: str) -> None:
    rel = path.relative_to(tree).as_posix()
    info_name = prefix + rel
    if path.is_symlink():
        info = tarfile.TarInfo(info_name)
        info.type = tarfile.SYMTYPE
        info.linkname = os.readlink(path)
        info.mtime = DETERMINISTIC_MTIME
        info.mode = 0o777
        info.uid = 0
        info.gid = 0
        info.uname = ""
        info.gname = ""
        tar.addfile(info)
        return
    info = tar.gettarinfo(str(path), arcname=info_name)
    info.mtime = DETERMINISTIC_MTIME
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    if info.isdir():
        info.mode = 0o755
        tar.addfile(info)
    elif info.isfile():
        info.mode = 0o755 if os.access(path, os.X_OK) else 0o644
        with open(path, "rb") as fh:
            tar.addfile(info, fh)


def _zip_source(tree: Path, version: str, target: Path) -> None:
    """Write the deterministic Windows ZIP release archive from the exported tree.

    Windows' bootstrap expects ``qubit-release.zip`` and refuses symlink
    entries, so tracked symlinks are omitted (the POSIX tar keeps them).
    """
    prefix = SOURCE_ARCHIVE_PREFIX % version
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(tree.rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            rel = path.relative_to(tree).as_posix()
            info = zipfile.ZipInfo(prefix + rel, date_time=(2000, 1, 1, 0, 0, 0))
            mode = 0o755 if os.access(path, os.X_OK) else 0o644
            info.external_attr = (mode & 0xFFFF) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            bundle.writestr(info, path.read_bytes())


def _build_release_payloads(repo: Path, source_tree: Path, source_archive: Path,
                            version: str, out: Path) -> list[Path]:
    """Publish the POSIX and Windows bootstrap asset pairs into *out*.

    Both bootstraps consume a source archive whose ``scripts/install.*`` is
    byte-identical to the published installer. The source tree used here is the
    same sanitized export used for the source archive, so the archive and the
    asset are guaranteed to agree.
    """
    payloads: list[Path] = []
    for name in ("bootstrap.sh", "bootstrap.ps1", "install.sh", "install.ps1"):
        source = repo / "scripts" / name
        if not source.is_file():
            raise ReleaseError("release asset source is missing: %s" % source)
        target = out / name
        shutil.copy2(source, target)
        payloads.append(target)

    posix_archive = out / "qubit-release.tar.gz"
    shutil.copy2(source_archive, posix_archive)
    payloads.append(posix_archive)

    windows_archive = out / "qubit-release.zip"
    _zip_source(source_tree, version, windows_archive)
    payloads.append(windows_archive)

    # The bootstrap verifies the extracted installer against the published
    # asset before executing it; catch a mismatch at preparation time too.
    import zipfile as _zipfile
    import tarfile as _tarfile

    with _tarfile.open(posix_archive, "r:gz") as bundle:
        members = [m for m in bundle.getmembers()
                   if m.name == "scripts/install.sh"
                   or m.name.endswith("/scripts/install.sh")]
        if not members:
            raise ReleaseError("qubit-release.tar.gz has no scripts/install.sh")
        member = sorted(members, key=lambda m: (len(m.name), m.name))[0]
        handle = bundle.extractfile(member)
        embedded = hashlib.sha256(handle.read()).hexdigest() if handle else ""
    if embedded != sha256(out / "install.sh"):
        raise ReleaseError("qubit-release.tar.gz installer does not match install.sh")

    with _zipfile.ZipFile(windows_archive) as bundle:
        members = [name for name in bundle.namelist()
                   if name == "scripts/install.ps1"
                   or name.endswith("/scripts/install.ps1")]
        if not members:
            raise ReleaseError("qubit-release.zip has no scripts/install.ps1")
        member = sorted(members, key=lambda name: (len(name), name))[0]
        embedded = hashlib.sha256(bundle.read(member)).hexdigest()
    if embedded != sha256(out / "install.ps1"):
        raise ReleaseError("qubit-release.zip installer does not match install.ps1")
    return payloads


def _run_release_gate(repo: Path, out: Path) -> dict:
    """Run the fresh-install gate against the built release; refuse on anything but PASS."""
    cmd = [
        sys.executable,
        str(repo / "scripts" / "release_fresh_install_gate.py"),
        "--repo", str(repo),
        "--release-dir", str(out),
        "--fresh-install",
        "--require-fresh-install",
        "--json",
    ]
    proc = _run(cmd, timeout=7200, cwd=repo)
    text = proc.stdout or ""
    try:
        report = json.loads(text)
    except json.JSONDecodeError:
        (out / "release-gate.raw.txt").write_text(
            text + "\n" + (proc.stderr or ""), encoding="utf-8"
        )
        raise ReleaseError(
            "release gate did not return JSON (rc=%s): %s"
            % (proc.returncode, (proc.stderr or text)[-2000:])
        )
    (out / "release-gate-report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if proc.returncode != 0 or report.get("verdict") != "PASS":
        failed = [
            check for check in report.get("checks", [])
            if check.get("state") != "OK"
        ]
        detail = "; ".join(
            "%s=%s %s" % (check.get("name"), check.get("state"),
                          check.get("detail", ""))
            for check in failed[:10]
        )
        raise ReleaseError(
            "release gate did not PASS: verdict=%s rc=%s %s"
            % (report.get("verdict"), proc.returncode, detail)
        )
    return report


def _component_manifest(repo: Path, release: dict, source: dict,
                        artifacts: list[dict], qualify: dict,
                        tree: dict) -> dict:
    runtime_pyproject = _toml(repo / "qubit_runtime" / "pyproject.toml")
    tui_package = json.loads(
        (repo / "qubit_runtime" / "ui-tui" / "package.json").read_text()
    )
    ink_package = json.loads(
        (repo / "qubit_runtime" / "ui-tui" / "packages" / "qubit-ink" / "package.json")
        .read_text()
    )
    build_info_path = repo / "frontend" / "qubit-dist" / "build-info.json"
    dashboard = {}
    if build_info_path.is_file():
        dashboard = json.loads(build_info_path.read_text())
    catalog = sorted((repo / "qubit_runtime" / "plugin-catalog").glob("*.yaml"))
    skills = sorted((repo / "skills").rglob("SKILL.md"))

    signing = {}
    build = (qualify.get("steps") or {}).get("build") or {}
    signing = build.get("signing") or {"state": "NOT_SIGNED"}

    return {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "product": {
            "name": "Qubit",
            "distribution": "qubit-voicebridge",
            "version": release["version"],
            "tag": release["tag"],
            "requires_python": (_toml(repo / "pyproject.toml").get("project") or {})
            .get("requires-python"),
        },
        "source": {
            "repository": "https://github.com/cgraph-dev/Qubit",
            "commit": tree["head"],
            "branch": tree["branch"],
            "commit_date": _git(repo, "show", "-s", "--format=%cI", "HEAD"),
            "public_export": {
                "tracked_files": source["source"]["tracked_files"],
                "included_files": source["included_files"],
                "excluded_files": source["excluded_files"],
                "included_by_top_level": source["included_by_top_level"],
                "excluded_by_reason": source["excluded_by_reason"],
            },
        },
        "components": {
            "python_distribution": {
                "name": "qubit-voicebridge",
                "version": release["version"],
            },
            "vendored_runtime": {
                "name": (runtime_pyproject.get("project") or {}).get("name"),
                "version": (runtime_pyproject.get("project") or {}).get("version"),
            },
            "dashboard": {
                "schema": dashboard.get("schema"),
                "source_head": dashboard.get("source_head"),
                "source_dirty": dashboard.get("source_dirty"),
                "built_at": dashboard.get("built_at"),
            },
            "tui": {
                "name": tui_package.get("name"),
                "version": tui_package.get("version"),
                "ink": {"name": ink_package.get("name"),
                        "version": ink_package.get("version")},
            },
            "plugin_catalog": {"yaml_entries": len(catalog)},
            "built_in_skills": {"skill_md_files": len(skills)},
        },
        "artifacts": artifacts,
        "verification": {
            "release_qualify": {
                "verdict": qualify.get("verdict"),
                "version_identity": qualify.get("version_identity"),
                "signing": signing,
            }
        },
        "publish": {
            "state": "NOT_PUBLISHED",
            "policy": "draft-only; a human creates/pushes the tag and promotes the draft",
        },
    }


def _release_notes(release: dict, tree: dict, artifacts: list[dict]) -> str:
    version = release["version"]
    tag = release["tag"]
    lines = [
        "# Qubit %s" % version,
        "",
        "Production release candidate prepared from `%s` on `%s`."
        % (tree["head"], tree["branch"]),
        "",
        "## Assets",
        "",
    ]
    for art in artifacts:
        lines.append(
            "- `%s` — sha256 `%s` (%d bytes)"
            % (art["name"], art["sha256"], art["size_bytes"])
        )
    lines += [
        "",
        "## Verify",
        "",
        "```bash",
        "sha256sum -c SHA256SUMS",
        "python3 -m pip install qubit_voicebridge-%s-py3-none-any.whl" % version,
        "qubit --version",
        "```",
        "",
        "## Notes",
        "",
        "- The wheel/sdist are unsigned (no detached signature; explicit",
        "  `--unsigned-ok` policy). Verify `SHA256SUMS` before installing.",
        "- The source archive is the sanitized public export; internal reports,",
        "  operator paths and local build artifacts are excluded.",
        "- This draft build is not a behavioral release verdict; owner release",
        "  approval remains a separate step.",
        "- Publishing is manual and must never be performed by automation.",
        "",
        "Tag: `%s`" % tag,
    ]
    return "\n".join(lines) + "\n"


def _asset_records(assets: list[Path]) -> list[dict]:
    return [
        {"name": path.name, "sha256": sha256(path), "size_bytes": path.stat().st_size}
        for path in assets
    ]


def _write_sha256sums(assets: list[Path], target: Path) -> list[dict]:
    records = _asset_records(assets)
    target.write_text(
        "\n".join("%s  %s" % (record["sha256"], record["name"])
                  for record in records) + "\n",
        encoding="utf-8",
    )
    return records


def _final_asset_paths(artifacts: dict[str, Path], source_archive: Path,
                       release_payloads: list[Path], component_path: Path,
                       notes_path: Path, gate_report_path: Path,
                       summary_path: Path) -> list[Path]:
    """Every asset that must be published, including the gate report and summary.

    ``SHA256SUMS`` is the checksum file over this list, so it cannot include
    itself.  The complete set is validated by ``scripts/release_assets.py``.
    """
    return [
        artifacts["wheel"], artifacts["sdist"], source_archive,
        *release_payloads, component_path, notes_path,
        gate_report_path, summary_path,
    ]


def _verify_sha256sums(assets_dir: Path) -> None:
    proc = _run(["sha256sum", "-c", "SHA256SUMS"], cwd=assets_dir)
    if proc.returncode != 0:
        raise ReleaseError("SHA256SUMS verification failed: %s" % (proc.stderr or proc.stdout))


def prepare(repo: Path, out: Path, version: str | None = None,
            tag: str | None = None, verify: bool = False) -> dict:
    repo = repo.resolve()
    out = out.resolve()
    if out == repo or repo in out.parents:
        raise ReleaseError("--out must be outside the checkout (%s)" % repo)
    out.mkdir(parents=True, exist_ok=True)

    release = resolve_release(repo, version=version, tag=tag)
    tree = check_tree(repo)
    tag_state = check_tag(repo, release["tag"])

    log: dict[str, Any] = {}
    _ensure_dashboard(repo, log)
    qualify_dir = out / "qualify"
    qualify = _release_qualify(repo, qualify_dir, release["tag"], verify)
    artifacts = _copy_artifacts(qualify_dir, out)

    source_tree, source_report = _export_source(repo, release["version"])
    try:
        source_archive = out / ("Qubit-%s-source.tar.gz" % release["version"])
        _tar_source(source_tree, release["version"], source_archive)
        release_payloads = _build_release_payloads(
            repo, source_tree, source_archive, release["version"], out
        )
    finally:
        shutil.rmtree(source_tree.parent, ignore_errors=True)

    manifest_artifacts = []
    for kind, path in (("wheel", artifacts["wheel"]), ("sdist", artifacts["sdist"]),
                       ("source", source_archive)):
        manifest_artifacts.append({
            "kind": kind,
            "name": path.name,
            "sha256": sha256(path),
            "size_bytes": path.stat().st_size,
        })
    for path in release_payloads:
        manifest_artifacts.append({
            "kind": "release-asset",
            "name": path.name,
            "sha256": sha256(path),
            "size_bytes": path.stat().st_size,
        })

    component = _component_manifest(
        repo, release, source_report, manifest_artifacts, qualify, tree
    )
    component_path = out / "component-manifest.json"
    component_path.write_text(
        json.dumps(component, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    notes_path = out / "release-notes.md"
    notes_path.write_text(
        _release_notes(release, tree, manifest_artifacts), encoding="utf-8"
    )

    # The gate reads SHA256SUMS and the built assets, so publish the initial
    # manifest/checksums first, then run it. The final checksum set is rewritten
    # afterwards to include the gate report and release summary themselves.
    sums = out / "SHA256SUMS"
    _write_sha256sums(
        [artifacts["wheel"], artifacts["sdist"], source_archive,
         *release_payloads, component_path, notes_path],
        sums,
    )
    gate_report = _run_release_gate(repo, out)

    component["verification"]["release_fresh_install_gate"] = {
        "verdict": gate_report.get("verdict"),
        "schema": gate_report.get("schema"),
        "checks": gate_report.get("checks", []),
    }
    component_path.write_text(
        json.dumps(component, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    gate_report_path = out / "release-gate-report.json"
    summary_path = out / "release-summary.json"

    asset_paths = _final_asset_paths(
        artifacts, source_archive, release_payloads, component_path,
        notes_path, gate_report_path, summary_path,
    )
    # The summary cannot record its own digest; every other published asset is
    # listed and, with SHA256SUMS itself excluded, checksummed below.
    records = _asset_records(
        [path for path in asset_paths if path != summary_path]
    )
    summary = {
        "state": "OK",
        "version": release["version"],
        "tag": release["tag"],
        "tag_state": tag_state,
        "source": {"commit": tree["head"], "branch": tree["branch"]},
        "verification": {"release_qualify": qualify.get("verdict"),
                         "release_fresh_install_gate": gate_report.get("verdict"),
                         "sha256sums": "OK"},
        "assets": records,
        "paths": {
            "out": str(out),
            "wheel": str(artifacts["wheel"]),
            "sdist": str(artifacts["sdist"]),
            "source": str(source_archive),
            "sha256sums": str(sums),
            "component_manifest": str(component_path),
            "release_notes": str(notes_path),
            "release_gate_report": str(gate_report_path),
            "release_summary": str(summary_path),
            "release_report": str(qualify_dir / "release-report.json"),
        },
        "next": [
            "Review component-manifest.json, release-notes.md and release-gate-report.json.",
            "Owner: create/push the tag only after the release commit lands on production.",
            "Owner: dispatch .github/workflows/promote-release.yml with that tag; it "
            "creates/updates a DRAFT release only.",
            "Owner: verify the draft assets, then publish manually.",
        ],
    }
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    # Final checksum set covers every published asset, including the gate
    # report and this summary. Then refuse if the canonical set is incomplete.
    _write_sha256sums(asset_paths, sums)
    _verify_sha256sums(out)
    _load_module("release_assets", "scripts/release_assets.py").check_assets(out)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Prepare Qubit production release assets without publishing."
    )
    parser.add_argument("--repo", type=Path, default=REPO)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--version", default=None,
                        help="defaults to the root pyproject [project].version")
    parser.add_argument("--tag", default=None, help="defaults to v<version>")
    parser.add_argument("--verify", action="store_true",
                        help="run the full release qualifier (build+install+reproduce)")
    args = parser.parse_args(argv)

    try:
        summary = prepare(
            args.repo, args.out, version=args.version, tag=args.tag,
            verify=args.verify,
        )
    except ReleaseError as exc:
        print("RELEASE_PREP_FAILED: %s" % exc, file=sys.stderr)
        return 2
    print("RELEASE_PREP_OK version=%s tag=%s out=%s"
          % (summary["version"], summary["tag"], summary["paths"]["out"]))
    for record in summary["assets"]:
        print("  %s  %s" % (record["sha256"], record["name"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Build a clean, installable Qubit release-candidate seed.

The seed is exported from tracked Git objects with ``export_public_tree.py``
(never from the working tree), then:

* build metadata that referenced excluded internal docs is pruned so the seed
  still builds a wheel;
* a ``VERSION`` file records the release version;
* the exported tree is initialized as a brand-new Git repository with exactly
  one commit and one ``vX.Y.Z`` tag;
* the seed is verified for leaked history/state/models/internal docs;
* a wheel is built from the seed and a local ``bootstrap.sh``-style install is
  exercised from it;
* a concise manifest with counts, commit/tag and checksums is written outside
  the seed.

Nothing is pushed, no private repository is touched, and no live service or
configuration is modified.

Usage::

    python3 scripts/prepare_release_seed.py --dest /tmp/qubit-seed
    python3 scripts/prepare_release_seed.py --dest /tmp/qubit-seed --skip-install-check
"""
from __future__ import annotations

import argparse
import ast
import glob
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

REPO = Path(__file__).resolve().parents[1]
DEFAULT_VERSION = None
DEFAULT_TAG = None

TABLE_RE = re.compile(r"^\s*\[([^\]]+)\]\s*$")
FORCE_INCLUDE_LINE_RE = re.compile(r'^(\s*)"([^"]+)"\s*=\s*"([^"]*)"\s*$')
SINGLE_ARTIFACT_RE = re.compile(r"^(\s*)artifacts\s*=\s*\[(.*)\]\s*$")
MULTI_ARTIFACT_OPEN_RE = re.compile(r"^(\s*)artifacts\s*=\s*\[\s*$")
MULTI_ARTIFACT_ITEM_RE = re.compile(r'^(\s*"([^"]+)"\s*,?\s*)$')
MULTI_ARTIFACT_CLOSE_RE = re.compile(r"^\s*\]\s*$")

PERSONAL_MARKER = os.path.join("/home", "trick").encode()

FORBIDDEN_DIR_SEGMENTS = frozenset({
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    "graft",
    "output",
    "sessions",
    "tmp",
})

FORBIDDEN_BASENAMES = frozenset({
    ".env",
    "auth.json",
    "credentials.json",
    "id_ed25519",
    "id_rsa",
    "state.db",
})
FORBIDDEN_SUFFIXES = (
    ".jks",
    ".key",
    ".p12",
    ".pem",
    ".secret",
    ".sqlite",
    ".sqlite3",
)
MODEL_SUFFIXES = (
    ".ckpt",
    ".gguf",
    ".h5",
    ".model",
    ".npy",
    ".npz",
    ".onnx",
    ".pth",
    ".safetensors",
)


def _root_version(repo: Path) -> str | None:
    """Read the product version from the root ``pyproject.toml``."""
    try:
        import tomllib

        with open(repo / "pyproject.toml", "rb") as fh:
            return str((tomllib.load(fh).get("project") or {}).get("version") or "")
    except Exception:
        return None


class SeedError(SystemExit):
    pass


@dataclass
class Checks:
    ok: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    details: dict[str, str] = field(default_factory=dict)

    def require(self, name: str, condition: bool, detail: str = "") -> None:
        if condition:
            self.ok.append(name)
        else:
            self.failed.append(name)
            if detail:
                self.details[name] = detail

    def finish(self) -> None:
        if not self.failed:
            return
        detail = "\n".join(
            "%s: %s" % (name, self.details.get(name, "failed"))
            for name in self.failed
        )
        raise SeedError("SEED_VERIFY_FAILED:\n" + detail)


def _run(cmd: list[str], cwd: Path | None = None, timeout: int = 600) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout,
    )
    return proc


def _require_ok(proc: subprocess.CompletedProcess, label: str) -> None:
    if proc.returncode != 0:
        raise SeedError(
            "SEED_COMMAND_FAILED: %s (exit %d)\n%s"
            % (label, proc.returncode, proc.stdout.strip())
        )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_exporter():
    path = REPO / "scripts" / "export_public_tree.py"
    spec = importlib.util.spec_from_file_location("export_public_tree", path)
    if spec is None or spec.loader is None:
        raise SeedError("cannot load %s" % path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def export_tree(repo: Path, dest: Path, force: bool) -> dict:
    if dest.exists():
        if not force:
            raise SeedError("REFUSING: --dest already exists (use --force): %s" % dest)
        shutil.rmtree(dest)
    exporter = _load_exporter()
    commit, tree = exporter._source_identity(repo)
    tracked = exporter._tracked_paths(repo)
    personal = exporter._personal_path_files(repo)
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
    report["_exporter"] = exporter
    return report


def _path_exists(dest: Path, pattern: str) -> bool:
    if glob.has_magic(pattern):
        return bool(glob.glob(str(dest / pattern), recursive=True))
    return (dest / pattern).exists()


def prune_missing_build_refs(pyproject: Path, dest: Path) -> list[str]:
    """Drop build references whose source was excluded from the seed."""
    lines = pyproject.read_text(encoding="utf-8").splitlines(keepends=True)
    out: list[str] = []
    removed: list[str] = []
    table = ""
    index = 0
    while index < len(lines):
        line = lines[index]
        header = TABLE_RE.match(line)
        if header:
            table = header.group(1).strip()
            out.append(line)
            index += 1
            continue

        if table.endswith(".force-include"):
            match = FORCE_INCLUDE_LINE_RE.match(line.rstrip("\n"))
            if match and not _path_exists(dest, match.group(2)):
                removed.append(match.group(2))
                index += 1
                continue

        if table == "tool.hatch.build.targets.wheel":
            match = SINGLE_ARTIFACT_RE.match(line.rstrip("\n"))
            if match:
                try:
                    values = ast.literal_eval("[" + match.group(2) + "]")
                except (SyntaxError, ValueError):
                    values = []
                kept = [value for value in values if _path_exists(dest, value)]
                removed.extend(value for value in values if value not in kept)
                if kept:
                    out.append(
                        "%sartifacts = [%s]\n"
                        % (match.group(1), ", ".join(json.dumps(v) for v in kept))
                    )
                index += 1
                continue

        if table == "tool.hatch.build.targets.sdist":
            match = MULTI_ARTIFACT_OPEN_RE.match(line.rstrip("\n"))
            if match:
                indent = match.group(1)
                entries: list[str] = []
                comments: list[str] = []
                index += 1
                while index < len(lines) and not MULTI_ARTIFACT_CLOSE_RE.match(lines[index]):
                    item = MULTI_ARTIFACT_ITEM_RE.match(lines[index].rstrip("\n"))
                    if item:
                        value = item.group(2)
                        if _path_exists(dest, value):
                            entries.append('  "%s",\n' % value)
                        else:
                            removed.append(value)
                    else:
                        comments.append(lines[index])
                    index += 1
                index += 1  # closing bracket
                out.extend(comments)
                if entries:
                    out.append("%sartifacts = [\n" % indent)
                    out.extend(entries)
                    out.append("%s]\n" % indent)
                continue

        out.append(line)
        index += 1

    pyproject.write_text("".join(out), encoding="utf-8")
    return removed


def missing_build_refs(pyproject: Path, dest: Path) -> list[str]:
    """Return force-include/artifact sources that do not exist in the seed."""
    import tomllib

    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    targets = data["tool"]["hatch"]["build"]["targets"]
    missing: list[str] = []
    for target in ("wheel", "sdist"):
        table = targets[target]
        for source in table.get("force-include", {}):
            if not _path_exists(dest, source):
                missing.append("%s force-include %s" % (target, source))
        for artifact in table.get("artifacts", []):
            if not _path_exists(dest, artifact):
                missing.append("%s artifact %s" % (target, artifact))
    return missing


def write_version(dest: Path, version: str) -> None:
    (dest / "VERSION").write_text(version + "\n", encoding="utf-8")


def scan_forbidden(dest: Path, public_docs: set[str]) -> tuple[list[str], list[str]]:
    """Return ``(forbidden_paths, personal_paths)`` found in the exported tree."""
    forbidden: list[str] = []
    personal: list[str] = []
    for root, dirs, files in os.walk(dest):
        dirs[:] = [name for name in dirs if name != ".git"]
        root_path = Path(root)
        rel_root = root_path.relative_to(dest)
        for name in dirs:
            if name in FORBIDDEN_DIR_SEGMENTS:
                forbidden.append(str(rel_root / name) + "/")
        for name in files:
            path = root_path / name
            rel = str(path.relative_to(dest))
            parts = PurePosixPath(rel).parts
            if any(segment in FORBIDDEN_DIR_SEGMENTS for segment in parts):
                forbidden.append(rel)
                continue
            lowered = name.lower()
            if name == ".env" or (name.startswith(".env.") and not name.endswith(".example")):
                forbidden.append(rel)
            elif name in FORBIDDEN_BASENAMES:
                forbidden.append(rel)
            elif lowered.endswith(FORBIDDEN_SUFFIXES):
                forbidden.append(rel)
            elif lowered.endswith(MODEL_SUFFIXES):
                forbidden.append(rel)
            elif name == "Modelfile.qubit":
                forbidden.append(rel)
            if parts and parts[0] == "docs" and rel not in public_docs:
                forbidden.append(rel)
            if not path.is_symlink() and path.is_file():
                try:
                    if PERSONAL_MARKER in path.read_bytes():
                        personal.append(rel)
                except OSError:
                    pass
    return sorted(set(forbidden)), sorted(set(personal))


def expected_seed_file_count(report: dict) -> int:
    """Exported files plus the seed's manifest and VERSION (unless exported)."""
    extra = 1  # PUBLIC_TREE_MANIFEST.md
    if "VERSION" not in report.get("public_root_files", ()):
        extra += 1
    return int(report["included_files"]) + extra


def verify_export(dest: Path, report: dict, version: str, public_docs: set[str], checks: Checks) -> None:
    checks.require("dest is a directory", dest.is_dir(), str(dest))
    checks.require(
        "source tree has no .git directory",
        not (dest / ".git").exists(),
        ".git was copied from the source checkout",
    )
    forbidden, personal = scan_forbidden(dest, public_docs)
    checks.require("no forbidden local state/models", not forbidden, ", ".join(forbidden[:20]))
    checks.require("no operator path markers", not personal, ", ".join(personal[:20]))
    docs = sorted(
        str(path.relative_to(dest)) for path in (dest / "docs").rglob("*") if path.is_file()
    ) if (dest / "docs").is_dir() else []
    checks.require("only public docs exported", docs == sorted(public_docs), ", ".join(docs))
    exported_files = sum(
        1 for path in dest.rglob("*") if path.is_file() or path.is_symlink()
    )
    checks.require(
        "included count matches export report",
        exported_files == expected_seed_file_count(report),
        "expected %d (included + manifest + VERSION when not exported), found %d"
        % (expected_seed_file_count(report), exported_files),
    )
    pyproject = dest / "pyproject.toml"
    import tomllib

    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    checks.require(
        "pyproject version matches",
        data["project"]["version"] == version,
        str(data["project"].get("version")),
    )
    checks.require("VERSION file matches", (dest / "VERSION").read_text().strip() == version)
    checks.require(
        "voicebridge __version__ matches",
        '__version__ = "%s"' % version in (dest / "voicebridge" / "__init__.py").read_text(),
    )


def init_git(
    dest: Path, source_commit: str, tag: str, checks: Checks, expected_tracked: int
) -> str:
    proc = _run(["git", "init", "-q", "-b", "main"], cwd=dest)
    _require_ok(proc, "git init")
    _run(["git", "config", "user.name", "Qubit Release Seed"], cwd=dest)
    _run(["git", "config", "user.email", "release@qubit.local"], cwd=dest)
    # The public export deliberately includes a few files the product's
    # .gitignore ignores (e.g. the committed TUI dist). Force-add so the seed
    # repository carries every exported file, not the .gitignore subset.
    proc = _run(["git", "add", "-A", "-f"], cwd=dest)
    _require_ok(proc, "git add")
    message = (
        "release: Qubit %s public seed\n\n"
        "Exported from tracked Git objects at %s.\n"
        "Fresh history: no source .git directory, branches, or remotes were copied.\n"
        % (tag.lstrip("v"), source_commit)
    )
    proc = _run(["git", "commit", "-q", "-m", message], cwd=dest)
    _require_ok(proc, "git commit")
    proc = _run(["git", "tag", "-a", tag, "-m", "Qubit %s release-candidate seed" % tag.lstrip("v")], cwd=dest)
    _require_ok(proc, "git tag")
    commit = _run(["git", "rev-parse", "HEAD"], cwd=dest).stdout.strip()
    tracked = len(_run(["git", "ls-files"], cwd=dest).stdout.splitlines())
    dirty = _run(["git", "status", "--porcelain"], cwd=dest).stdout.strip()
    count = int(_run(["git", "rev-list", "--count", "HEAD"], cwd=dest).stdout.strip())
    remotes = _run(["git", "remote"], cwd=dest).stdout.strip()
    tag_commit = _run(["git", "rev-parse", tag + "^{}"], cwd=dest).stdout.strip()
    checks.require("seed has exactly one commit", count == 1, "commits=%d" % count)
    checks.require(
        "seed commit carries every exported file",
        tracked == expected_tracked,
        "tracked=%d expected=%d" % (tracked, expected_tracked),
    )
    checks.require("seed commit is clean", dirty == "", dirty[:1000])
    checks.require("seed has no remotes", remotes == "", remotes)
    checks.require("tag points at seed commit", tag_commit == commit, "%s != %s" % (tag_commit, commit))
    checks.require(
        "seed history is independent",
        _run(["git", "rev-list", "--all", "--count"], cwd=dest).stdout.strip() == "1",
        "more than one commit reachable",
    )
    return commit


def build_wheel(dest: Path, out_dir: Path, checks: Checks) -> Path | None:
    out_dir.mkdir(parents=True, exist_ok=True)
    proc = _run(["uv", "build", "--wheel", "--out-dir", str(out_dir)], cwd=dest, timeout=900)
    if proc.returncode != 0:
        checks.require("wheel builds from seed", False, proc.stdout.strip()[-2000:])
        return None
    wheels = sorted(out_dir.glob("*.whl"))
    checks.require("wheel builds from seed", bool(wheels), proc.stdout.strip()[-1000:])
    return wheels[-1] if wheels else None


def make_release_base(
    dest: Path,
    base: Path,
    archive: Path | None = None,
    zip_archive: Path | None = None,
) -> tuple[Path, Path]:
    """Publish the bootstrap release assets for both host families.

    The POSIX bootstrap consumes ``install.sh`` + ``qubit-release.tar.gz``.
    The Windows bootstrap consumes ``install.ps1`` + ``qubit-release.zip``.
    Both are named in one ``SHA256SUMS``; each bootstrap reads only the two
    entries it needs. A caller may publish just one family by passing only the
    matching archive argument.
    """
    if base.exists():
        shutil.rmtree(base)
    base.mkdir(parents=True)
    assets = [("install.sh", dest / "scripts" / "install.sh")]
    if archive is not None:
        assets.append(("qubit-release.tar.gz", archive))
    windows_installer = dest / "scripts" / "install.ps1"
    if windows_installer.is_file():
        assets.append(("install.ps1", windows_installer))
    if zip_archive is not None:
        assets.append(("qubit-release.zip", zip_archive))
    lines = []
    for name, source in assets:
        shutil.copy2(source, base / name)
        lines.append("%s  %s" % (_sha256(base / name), name))
    asset = base / "install.sh"
    checksums = base / "SHA256SUMS"
    checksums.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return asset, checksums


def verify_installs(dest: Path, work: Path, release_base: Path, checks: Checks,
                   version: str) -> dict:
    results: dict[str, str] = {}
    for label, cmd in (
        ("installer --help", [str(dest / "scripts" / "install.sh"), "--help"]),
        ("bootstrap --help", [str(dest / "scripts" / "bootstrap.sh"), "--help"]),
    ):
        proc = _run(["bash", *cmd], cwd=dest, timeout=120)
        checks.require(label, proc.returncode == 0, proc.stdout.strip()[-1000:])
        results[label] = "exit 0" if proc.returncode == 0 else "exit %d" % proc.returncode

    proc = _run(
        ["bash", str(dest / "scripts" / "bootstrap.sh"), "--dry-run", str(release_base)],
        cwd=dest,
        timeout=120,
    )
    checks.require("bootstrap local dry-run", proc.returncode == 0, proc.stdout.strip()[-2000:])
    results["bootstrap local dry-run"] = "exit 0" if proc.returncode == 0 else "exit %d" % proc.returncode
    results["bootstrap dry-run output"] = proc.stdout.strip()

    source_copy = work / "install-src"
    if source_copy.exists():
        shutil.rmtree(source_copy)
    shutil.copytree(dest, source_copy, ignore=shutil.ignore_patterns(".git"))
    prefix = work / "install-prefix"
    env = os.environ.copy()
    env.update({
        "HOME": str(work / "home"),
        "QUBIT_HOME": str(prefix),
        "QUBIT_NO_LAUNCHER": "0",
        "XDG_DATA_HOME": str(work / "xdg" / "data"),
        "XDG_CONFIG_HOME": str(work / "xdg" / "config"),
        "XDG_STATE_HOME": str(work / "xdg" / "state"),
        "XDG_CACHE_HOME": str(work / "xdg" / "cache"),
    })
    (work / "home").mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            "bash",
            str(source_copy / "scripts" / "install.sh"),
            "--prefix",
            str(prefix),
            "--without-models",
            "--without-dashboard",
            "--yes",
        ],
        cwd=str(source_copy),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
        timeout=2400,
    )
    results["install log"] = proc.stdout.strip()[-5000:]
    checks.require("local install exits 0", proc.returncode == 0, proc.stdout.strip()[-3000:])
    if proc.returncode == 0:
        qubit = source_copy / ".venv" / "bin" / "qubit"
        version_proc = subprocess.run(
            [str(qubit), "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            timeout=120,
        )
        checks.require(
            "installed qubit --version reports %s" % version,
            version_proc.returncode == 0 and version in version_proc.stdout,
            version_proc.stdout.strip(),
        )
        results["qubit --version"] = version_proc.stdout.strip()
        help_proc = subprocess.run(
            [str(qubit), "--help"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            timeout=120,
        )
        checks.require("installed qubit --help exits 0", help_proc.returncode == 0, help_proc.stdout.strip()[-1000:])
        results["installed qubit --help"] = "exit 0" if help_proc.returncode == 0 else "exit %d" % help_proc.returncode
    return results


def archive_seed(dest: Path, tag: str, version: str, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    archive = out_dir / ("Qubit-%s-seed.tar.gz" % version)
    tar_path = out_dir / ("Qubit-%s-seed.tar" % version)
    # Use a binary subprocess: a tar stream is not UTF-8 text.
    with tar_path.open("wb") as handle:
        binary = subprocess.run(
            ["git", "archive", "--format=tar", "--prefix=Qubit-%s/" % version, tag],
            cwd=str(dest),
            stdout=handle,
            stderr=subprocess.PIPE,
            timeout=300,
        )
    if binary.returncode != 0:
        raise SeedError("git archive failed: %s" % binary.stderr.decode("utf-8", "replace"))
    with tar_path.open("rb") as source, archive.open("wb") as raw:
        raw_handle = raw
        import gzip

        with gzip.GzipFile(fileobj=raw_handle, mode="wb", mtime=0) as gz:
            shutil.copyfileobj(source, gz, length=1024 * 1024)
    tar_path.unlink()
    return archive


def _tracked_symlinks(dest: Path, tag: str) -> list[str]:
    """Return the paths Git records as symlinks (mode 120000) at ``tag``."""
    proc = subprocess.run(
        ["git", "ls-tree", "-r", "-z", tag],
        cwd=str(dest),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=300,
    )
    if proc.returncode != 0:
        raise SeedError(
            "git ls-tree failed: %s" % proc.stderr.decode("utf-8", "replace")
        )
    paths: list[str] = []
    for entry in proc.stdout.split(b"\0"):
        if not entry:
            continue
        meta, _, path = entry.partition(b"\t")
        if not path:
            continue
        if meta.split(b" ", 1)[0] == b"120000":
            paths.append(path.decode("utf-8", "surrogateescape"))
    return paths


def archive_seed_zip(dest: Path, tag: str, version: str, out_dir: Path) -> Path:
    """Write the Windows release archive (ZIP) from the same seed tag.

    ``git archive --format=zip`` is deterministic for a given commit and keeps
    the one-top-level-directory layout the bootstrap expects
    (``Qubit-<version>/scripts/install.ps1``).

    Tracked symlinks are excluded: the Windows extractor deliberately refuses
    symlink entries, and Windows extraction of them would otherwise need
    elevated privileges. The POSIX tar archive keeps them.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    archive = out_dir / ("Qubit-%s-seed.zip" % version)
    command = [
        "git",
        "archive",
        "--format=zip",
        "--prefix=Qubit-%s/" % version,
        "--output",
        str(archive),
        tag,
        "--",
        ".",
    ]
    for link in _tracked_symlinks(dest, tag):
        command.append(":(exclude,literal)%s" % link)
    binary = subprocess.run(
        command,
        cwd=str(dest),
        stderr=subprocess.PIPE,
        timeout=300,
    )
    if binary.returncode != 0:
        raise SeedError(
            "git archive --format=zip failed: %s"
            % binary.stderr.decode("utf-8", "replace")
        )
    return archive


def write_manifest(
    path: Path,
    *,
    report: dict,
    version: str,
    tag: str,
    seed_commit: str,
    archive: Path,
    zip_archive: Path | None,
    wheel: Path | None,
    release_base: Path,
    removed_refs: list[str],
    install_results: dict,
    checks: Checks,
) -> None:
    commit = report["source"]["commit"]
    tree = report["source"]["tree"]
    lines = [
        "# Qubit release-candidate seed manifest",
        "",
        "Generated by `scripts/prepare_release_seed.py`.",
        "",
        "## Source",
        "",
        "- Source commit: `%s`" % commit,
        "- Source tree: `%s`" % tree,
        "- Source tracked files: %d" % report["source"]["tracked_files"],
        "",
        "## Seed repository",
        "",
        "- Version: `%s`" % version,
        "- Tag: `%s`" % tag,
        "- Seed commit: `%s`" % seed_commit,
        "- History: one commit, no remotes, no source `.git` objects",
        "",
        "## Export counts",
        "",
        "- Included files: %d" % report["included_files"],
        "- Excluded files: %d" % report["excluded_files"],
        "",
        "### Included by top level",
        "",
    ]
    for top, count in report["included_by_top_level"].items():
        lines.append("- `%s`: %d" % (top, count))
    lines += ["", "### Excluded by reason", ""]
    for reason, count in report["excluded_by_reason"].items():
        lines.append("- %s: %d" % (reason, count))
    lines += [
        "",
        "### Build metadata pruned for the seed",
        "",
        "These pyproject build references pointed at excluded internal docs or",
        "unbuilt artifacts and were removed so the seed builds standalone:",
        "",
    ]
    if removed_refs:
        for ref in removed_refs:
            lines.append("- `%s`" % ref)
    else:
        lines.append("- none")
    lines += ["", "## Checksums (SHA-256)", ""]
    lines.append("- Seed archive `%s`: `%s`" % (archive.name, _sha256(archive)))
    if zip_archive is not None:
        lines.append("- Seed archive `%s`: `%s`" % (zip_archive.name, _sha256(zip_archive)))
    asset = release_base / "install.sh"
    release_archive = release_base / "qubit-release.tar.gz"
    windows_installer = release_base / "install.ps1"
    windows_archive = release_base / "qubit-release.zip"
    if asset.is_file():
        lines.append("- Release asset `install.sh`: `%s`" % _sha256(asset))
        if release_archive.is_file():
            lines.append(
                "- Release archive `qubit-release.tar.gz`: `%s`"
                % _sha256(release_archive)
            )
    if windows_installer.is_file():
        lines.append("- Release asset `install.ps1`: `%s`" % _sha256(windows_installer))
        if windows_archive.is_file():
            lines.append(
                "- Release archive `qubit-release.zip`: `%s`"
                % _sha256(windows_archive)
            )
    if asset.is_file() or windows_installer.is_file():
        lines.append(
            "- Release `SHA256SUMS`: `%s`"
            % _sha256(release_base / "SHA256SUMS")
        )
    if wheel is not None:
        lines.append("- Wheel `%s`: `%s`" % (wheel.name, _sha256(wheel)))
    lines += [
        "",
        "## Verification",
        "",
    ]
    for name in checks.ok:
        lines.append("- PASS `%s`" % name)
    for name in checks.failed:
        lines.append("- FAIL `%s`" % name)
    if install_results:
        lines += ["", "### Install evidence", ""]
        for name in ("installer --help", "bootstrap --help", "bootstrap local dry-run", "qubit --version", "installed qubit --help"):
            if name in install_results:
                lines.append("- `%s`: %s" % (name, install_results[name]))
        if "bootstrap dry-run output" in install_results:
            lines += ["", "```", install_results["bootstrap dry-run output"], "```"]
        if "qubit --version" in install_results:
            lines += ["", "```", "qubit --version: " + install_results["qubit --version"], "```"]
    lines += [
        "",
        "## Owner steps to seed cgraph-dev/Qubit later",
        "",
        "These commands are intentionally NOT run by this lane. Run them only",
        "after reviewing the seed and confirming the private repository's state.",
        "",
        "```bash",
        "cd /tmp/qubit-seed",
        "git status --short          # must be empty",
        "git log --oneline --all     # exactly one seed commit",
        "git remote -v               # must be empty",
        "",
        "git remote add origin git@github.com:cgraph-dev/Qubit.git",
        "git push -u origin main",
        "git push origin %s" % tag,
        "```",
        "",
        "If `cgraph-dev/Qubit` is not empty, do NOT force-push. Fetch it first and",
        "decide with the owner whether the seed becomes `main` or a new orphan",
        "release branch/tag; the seed has no shared history with the source repo.",
        "",
        "## Known release-asset caveat",
        "",
        "`scripts/bootstrap.sh` consumes an `install.sh` asset and",
        "`qubit-release.tar.gz`; `scripts/bootstrap.ps1` consumes `install.ps1` and",
        "`qubit-release.zip`. Both are published above with one shared",
        "`SHA256SUMS`. The seed's installers are project-relative (`PROJECT_DIR` is",
        "the extracted release tree), so a public one-line install needs either a",
        "standalone installer asset or a release base that contains the full tree.",
        "The local dry-run above proves the POSIX bootstrap checksum/asset path;",
        "the actual local install above is run from the seed tree itself. The",
        "Windows bootstrap path is exercised hermetically under PowerShell 7; no",
        "real Windows host or Windows PowerShell 5.1 execution is claimed.",
        "",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a clean Qubit release-candidate seed.")
    parser.add_argument("--repo", type=Path, default=REPO)
    parser.add_argument("--dest", type=Path, required=True)
    parser.add_argument(
        "--version",
        default=DEFAULT_VERSION,
        help="release version; defaults to the root pyproject [project].version",
    )
    parser.add_argument("--tag", default=None)
    parser.add_argument("--manifest", type=Path, default=None)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--skip-install-check", action="store_true")
    args = parser.parse_args(argv)

    repo = args.repo.resolve()
    dest = args.dest.resolve()
    version = (args.version or _root_version(repo)).lstrip("v")
    if not version:
        raise SeedError("no version: pass --version or set [project].version")
    tag = args.tag or ("v" + version)
    manifest_path = args.manifest or Path(str(dest) + "-manifest.md")
    work = Path(str(dest) + "-verify")
    release_base = Path(str(dest) + "-release")
    artifact_dir = work / "artifacts"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)

    if not (repo / ".git").exists():
        raise SeedError("REFUSING: --repo is not a Git checkout: %s" % repo)

    checks = Checks()
    report = export_tree(repo, dest, args.force)
    exporter = report.pop("_exporter")
    write_version(dest, version)
    removed_refs = prune_missing_build_refs(dest / "pyproject.toml", dest)
    missing = missing_build_refs(dest / "pyproject.toml", dest)
    checks.require("seed build metadata resolves", not missing, ", ".join(missing))
    verify_export(dest, report, version, exporter.PUBLIC_DOC_FILES, checks)
    checks.finish()

    seed_commit = init_git(
        dest,
        report["source"]["commit"],
        tag,
        checks,
        expected_seed_file_count(report),
    )
    checks.finish()

    wheel = None
    if not args.skip_build:
        wheel = build_wheel(dest, artifact_dir, checks)
        checks.finish()

    archive = archive_seed(dest, tag, version, artifact_dir)
    zip_archive = archive_seed_zip(dest, tag, version, artifact_dir)

    make_release_base(dest, release_base, archive, zip_archive)

    install_results: dict[str, str] = {}
    if not args.skip_install_check:
        install_results = verify_installs(dest, work, release_base, checks, version)
        checks.finish()
    else:
        # Still prove the two help surfaces even when the heavy install is skipped.
        for label, cmd in (
            ("installer --help", [str(dest / "scripts" / "install.sh"), "--help"]),
            ("bootstrap --help", [str(dest / "scripts" / "bootstrap.sh"), "--help"]),
        ):
            proc = _run(["bash", *cmd], cwd=dest, timeout=120)
            checks.require(label, proc.returncode == 0, proc.stdout.strip()[-1000:])
            install_results[label] = "exit 0" if proc.returncode == 0 else "exit %d" % proc.returncode
        checks.finish()

    write_manifest(
        manifest_path,
        report=report,
        version=version,
        tag=tag,
        seed_commit=seed_commit,
        archive=archive,
        zip_archive=zip_archive,
        wheel=wheel,
        release_base=release_base,
        removed_refs=removed_refs,
        install_results=install_results,
        checks=checks,
    )
    print(
        "SEED_OK version=%s tag=%s commit=%s included=%d excluded=%d manifest=%s"
        % (
            version,
            tag,
            seed_commit,
            report["included_files"],
            report["excluded_files"],
            manifest_path,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

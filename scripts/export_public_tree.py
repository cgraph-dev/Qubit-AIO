#!/usr/bin/env python3
"""Export a deterministic, public-safe Qubit source snapshot.

The export is built from Git objects, never from the working tree:

* ``git ls-tree -r HEAD`` supplies the tracked-file allowlist;
* ``git archive HEAD`` supplies the bytes, with a fixed mtime;
* every path is classified before it can be written.

The audit finding this tool closes is the public-packaging leak: an sdist
built from this checkout carried docs with operator absolute paths.  This
exporter refuses to publish any tracked file whose HEAD content contains an
operator path marker, and reports the file instead of silently dropping it.

Usage::

    scripts/export_public_tree.py --check
    scripts/export_public_tree.py --check --json
    scripts/export_public_tree.py --dest /tmp/qubit-public
    scripts/export_public_tree.py --dest /tmp/qubit-public --fail-on-risk

``--check`` is a dry run: it writes nothing anywhere.  Without ``--check``,
``--dest`` must name a missing or empty directory outside this checkout.
No service, private repository, or network is touched.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path, PurePosixPath

REPO = Path(__file__).resolve().parents[1]

#: Fixed timestamp for every exported file (2000-01-01T00:00:00Z).  A second
#: export of the same commit is byte-identical; no wall-clock value is used.
DETERMINISTIC_MTIME = 946684800
ARCHIVE_MTIME = "2000-01-01T00:00:00Z"

#: Operator-home marker from the audit finding.  Any tracked file whose HEAD
#: blob contains one of these is excluded and reported, never published.
PERSONAL_PATH_MARKERS = (os.path.join("/home", "trick"),)

#: Top-level product trees.  A tracked path is a candidate only when its first
#: segment is one of these, or when it is an explicitly allowed root file.
ALLOWED_TOP_LEVEL_DIRS = frozenset({
    "adapters",
    "benchmarks",
    ".github",
    "config",
    "docs",
    "frontend",
    "qubit_runtime",
    "scripts",
    "skills",
    "systemd",
    "templates",
    "tests",
    "voicebridge",
})

#: Root files that are part of a public source release.  Everything else at
#: the root (internal reports, PORTED/baseline ledgers, probes, local model
#: files, personal agent config) is intentionally not allowlisted.
ALLOWED_ROOT_FILES = frozenset({
    ".env.example",
    ".gitignore",
    "CONTRIBUTING.md",
    "INSTALL.md",
    "LICENSE",
    "Modelfile.qubit.example",
    "README.md",
    "SECURITY.md",
    "THIRD_PARTY_NOTICES.md",
    "TROUBLESHOOTING.md",
    "pyproject.toml",
    "pytest.ini",
    "VERSION",
})

#: The complete public docs set under ``docs/``: agreed feature docs only.
#: Everything else there is an internal report, audit, plan, handoff, generated
#: ledger, frozen history, or process/meta doc.  This is an allowlist on
#: purpose: new internal docs must not leak by default.
PUBLIC_DOC_FILES = frozenset({
    "docs/OFFICE-CLI-COMPLETENESS.md",
    "docs/OFFICE-DEPLOYMENT-CONTRACT.md",
    "docs/ORB_FX_SPEC.md",
    "docs/QUBIT-TV.md",
    "docs/UPDATE.md",
})

#: Tracked product build output that MUST ship even though it lives under a
#: ``dist`` segment.  These are vendored/committed runtime assets the product
#: serves (the Ink TUI entry, the pinned dashboard plugin bundles), not
#: regenerable local build output.  ``dist`` stays excluded everywhere else.
PRODUCT_DIST_SUBTREES = frozenset({
    "frontend/qubit-web/public/plugins/qubit-achievements/dist",
    "frontend/qubit-web/public/plugins/kanban/dist",
    "qubit_runtime/plugins/qubit-achievements/dashboard/dist",
    "qubit_runtime/plugins/kanban/dashboard/dist",
    "qubit_runtime/ui-tui/dist",
})

#: Directory names that are build output, runtime state, local dependencies,
#: editor/agent state, or transient capture output.  Matching any path segment
#: excludes the whole subtree.
EXCLUDED_DIR_SEGMENTS = frozenset({
    ".dead_symbols_cache",
    ".frontend-build",
    ".git",
    ".mypy_cache",
    ".playwright-cli",
    ".playwright-mcp",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "_private_backup",
    "browser_auto_state",
    "browser_profiles",
    "captures",
    "coverage",
    "dist",
    "_dead_symbols",
    "errs",
    "graft",
    "graphify-out",
    "htmlcov",
    "logs",
    "node_modules",
    "output",
    "qubit-dist",
    "screens",
    "screenshots",
    "sessions",
    "tmp",
    "venv",
})

#: Doc subdirectories that are frozen history, generated data, evidence
#: captures, or internal design/plan material.  They are rejected before the
#: explicit ``PUBLIC_DOC_FILES`` allowlist so the report keeps a clear reason.
EXCLUDED_DOC_DIRS = frozenset({
    "archive",
    "e2e",
    "evidence",
    "game-office",
    "generated",
    "superpowers",
    "sync-w24",
})

#: Secret-bearing filenames.  ``.env.example`` and other ``*.example.*``
#: templates are allowed; real env files, credentials, keys, and local DBs are
#: excluded and reported.
SECRET_BASENAMES = frozenset({
    ".env",
    "auth.json",
    "credentials.json",
    "id_ed25519",
    "id_rsa",
    "state.db",
})
SECRET_SUFFIXES = (
    ".jks",
    ".key",
    ".p12",
    ".pem",
    ".secret",
    ".sqlite",
    ".sqlite3",
)

#: Local model/runtime files that are never part of a source release.
LOCAL_MODEL_PREFIXES = ("Modelfile.",)
MODEL_WEIGHT_SUFFIXES = (
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

#: Screenshot/capture filename fragments.  Product assets (icons, backgrounds,
#: plugin artwork) are kept; ad-hoc captures are not.
SCREENSHOT_PREFIXES = ("e2e-", "screen-shot", "screenshot")
SCREENSHOT_SUFFIXES = ("-e2e.png", "-capture.png", "-screenshot.png")

#: Product surfaces the public export must retain.  ``prefix`` is a directory
#: or a single file; the summary reports presence and file count.
PRODUCT_SURFACES = (
    ("runtime", "qubit_runtime"),
    ("voicebridge", "voicebridge"),
    ("frontend_source", "frontend/qubit-web/src"),
    ("tui_dist", "qubit_runtime/ui-tui/dist"),
    ("plugin_catalog", "qubit_runtime/plugin-catalog"),
    (
        "published_plugin_catalog",
        "qubit_runtime/website/static/api/plugin-catalog.json",
    ),
    ("systemd", "systemd"),
    ("official_skills", "skills"),
    ("optional_skills", "qubit_runtime/optional-skills"),
)


def _run_git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and proc.returncode != 0:
        raise SystemExit(
            "git command failed: git -C %s %s\n%s"
            % (repo, " ".join(args), proc.stderr.decode("utf-8", "replace").strip())
        )
    return proc


def _tracked_paths(repo: Path) -> list[str]:
    proc = _run_git(repo, "ls-tree", "-r", "--name-only", "-z", "HEAD")
    return sorted(
        part.decode("utf-8", "surrogateescape")
        for part in proc.stdout.split(b"\0")
        if part
    )


def _source_identity(repo: Path) -> tuple[str, str]:
    commit = _run_git(repo, "rev-parse", "HEAD").stdout.decode().strip()
    tree = _run_git(repo, "rev-parse", "HEAD^{tree}").stdout.decode().strip()
    return commit, tree


def _personal_path_files(repo: Path, markers: tuple[str, ...] = PERSONAL_PATH_MARKERS) -> set[str]:
    """Return tracked HEAD files whose text contains an operator path marker."""
    found: set[str] = set()
    for marker in markers:
        proc = _run_git(
            repo,
            "grep",
            "-I",
            "-l",
            "-z",
            "-F",
            marker,
            "HEAD",
            check=False,
        )
        if proc.returncode not in (0, 1):
            raise SystemExit(
                "git grep failed while scanning for operator paths: %s"
                % proc.stderr.decode("utf-8", "replace").strip()
            )
        for raw in proc.stdout.split(b"\0"):
            if not raw:
                continue
            name = raw.decode("utf-8", "surrogateescape")
            if name.startswith("HEAD:"):
                name = name[len("HEAD:"):]
            found.add(name)
    return found


def _symlink_targets(repo: Path) -> dict[str, str]:
    """Return tracked symlink path -> link target (mode 120000)."""
    proc = _run_git(repo, "ls-tree", "-r", "-z", "HEAD")
    links: dict[str, str] = {}
    for entry in proc.stdout.split(b"\0"):
        if not entry:
            continue
        meta, _, raw_path = entry.partition(b"\t")
        fields = meta.split()
        if len(fields) != 3:
            continue
        mode, obj_type, sha = (f.decode() for f in fields)
        path = raw_path.decode("utf-8", "surrogateescape")
        if mode == "120000" and obj_type == "blob":
            links[path] = _run_git(repo, "cat-file", "blob", sha).stdout.decode(
                "utf-8", "surrogateescape"
            )
    return links


def _is_secret_like(name: str) -> bool:
    if name.endswith(".example") or ".example." in name:
        return False
    if name in SECRET_BASENAMES:
        return True
    if name.startswith(".env."):
        return True
    return name.endswith(SECRET_SUFFIXES)


def _is_screenshot(name: str) -> bool:
    lowered = name.lower()
    return lowered.startswith(SCREENSHOT_PREFIXES) or lowered.endswith(SCREENSHOT_SUFFIXES)


def _config_allowed(path: str) -> bool:
    parts = PurePosixPath(path).parts
    if len(parts) != 2:
        return False
    name = parts[1]
    return name == "dialogs.md" or ".example." in name


def _docs_allowed(path: str) -> bool:
    """True only for the explicit public docs allowlist."""
    parts = PurePosixPath(path).parts
    if len(parts) < 2:
        return False
    if parts[1] in EXCLUDED_DOC_DIRS:
        return False
    return path in PUBLIC_DOC_FILES


def _is_product_dist(path: str) -> bool:
    """True for a committed product build asset (see PRODUCT_DIST_SUBTREES)."""
    return any(
        path == subtree or path.startswith(subtree + "/")
        for subtree in PRODUCT_DIST_SUBTREES
    )


def _classify(path: str) -> tuple[bool, str]:
    """Return ``(allowed, reason)`` for a tracked path."""
    parts = PurePosixPath(path).parts
    if not parts:
        return False, "invalid_path"
    top = parts[0]
    name = parts[-1]

    if len(parts) == 1:
        if path not in ALLOWED_ROOT_FILES:
            return False, "not_allowlisted"
    else:
        if top not in ALLOWED_TOP_LEVEL_DIRS:
            return False, "not_allowlisted"

    if path.startswith((".github/agents/", ".github/skills/", ".github/hooks/")):
        return False, "excluded_dir"
    if path == ".github/copilot-instructions.md":
        return False, "excluded_file"

    for segment in parts[1:]:
        if segment not in EXCLUDED_DIR_SEGMENTS:
            continue
        if segment == "dist" and _is_product_dist(path):
            continue
        return False, "excluded_dir"

    if top == "config" and not _config_allowed(path):
        return False, "excluded_file"

    if top == "docs" and not _docs_allowed(path):
        return False, "excluded_dir" if (
            len(parts) > 1 and parts[1] in EXCLUDED_DOC_DIRS
        ) else "excluded_file"

    if name.startswith(LOCAL_MODEL_PREFIXES) and name not in ALLOWED_ROOT_FILES:
        return False, "excluded_file"

    if name.endswith(MODEL_WEIGHT_SUFFIXES):
        return False, "local_model_weight"

    if _is_secret_like(name):
        return False, "secret_like_path"

    if _is_screenshot(name):
        return False, "excluded_file"

    return True, "included"


def _safe_link_target(path: str, target: str) -> bool:
    """True when a relative symlink target stays inside the export root."""
    if not target or target.startswith("/"):
        return False
    if PurePosixPath(target).is_absolute():
        return False
    parts: list[str] = []
    for part in (PurePosixPath(path).parent / target).parts:
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                return False
            parts.pop()
        else:
            parts.append(part)
    return bool(parts)


def _safe_member_name(name: str) -> bool:
    path = PurePosixPath(name)
    return bool(path.parts) and not path.is_absolute() and ".." not in path.parts


def _select(
    tracked: list[str],
    personal: set[str],
    symlinks: dict[str, str],
) -> tuple[list[str], dict[str, list[str]], dict[str, int]]:
    included: list[str] = []
    excluded: dict[str, list[str]] = {}
    counts: dict[str, int] = {}
    for path in tracked:
        allowed, reason = _classify(path)
        if allowed and path in symlinks and not _safe_link_target(path, symlinks[path]):
            allowed, reason = False, "unsafe_symlink"
        if allowed and path in personal:
            allowed, reason = False, "personal_path"
        if allowed:
            included.append(path)
            counts["included"] = counts.get("included", 0) + 1
        else:
            excluded.setdefault(reason, []).append(path)
            counts[reason] = counts.get(reason, 0) + 1
    return included, excluded, counts


def selected_paths(
    repo: Path,
) -> tuple[list[str], list[str], dict[str, list[str]], dict[str, int], set[str]]:
    """Return ``(tracked, included, excluded, counts, personal)``.

    Public seam for the feature-parity gate: it must assert against the same
    rules ``--check``/``--dest`` use, not a second hand-copied allowlist.
    """
    tracked = _tracked_paths(repo)
    personal = _personal_path_files(repo)
    symlinks = _symlink_targets(repo)
    included, excluded, counts = _select(tracked, personal, symlinks)
    return tracked, included, excluded, counts, personal


def _build_report(
    repo: Path,
    commit: str,
    tree: str,
    tracked: list[str],
    included: list[str],
    excluded: dict[str, list[str]],
    counts: dict[str, int],
    personal_all: set[str],
    symlinks: dict[str, str],
) -> dict:
    by_top: dict[str, int] = {}
    for path in included:
        top = path.split("/", 1)[0] if "/" in path else "(root)"
        by_top[top] = by_top.get(top, 0) + 1

    personal = sorted(personal_all)
    unsafe_links = sorted(
        p for p, target in symlinks.items() if not _safe_link_target(p, target)
    )
    secret_like = sorted(
        p for p in tracked if _is_secret_like(PurePosixPath(p).name)
    )
    model_weights = sorted(
        p for p in tracked
        if p not in ALLOWED_ROOT_FILES
        and (
            PurePosixPath(p).name.endswith(MODEL_WEIGHT_SUFFIXES)
            or PurePosixPath(p).name.startswith(LOCAL_MODEL_PREFIXES)
        )
    )
    contributor_emails = [
        p for p in included if p.startswith("qubit_runtime/contributors/emails/")
    ]

    included_set = set(included)
    public_docs = sorted(p for p in included if p in PUBLIC_DOC_FILES)
    public_root_files = sorted(p for p in included if "/" not in p)
    surfaces = {}
    for label, prefix in PRODUCT_SURFACES:
        if prefix in included_set:
            count = 1
        else:
            count = sum(1 for p in included if p.startswith(prefix + "/"))
        surfaces[label] = {"path": prefix, "files": count, "present": count > 0}
    surfaces["public_docs"] = {
        "path": "docs/",
        "files": len(public_docs),
        "present": bool(public_docs),
    }

    risks: list[dict] = []
    if personal:
        risks.append({
            "kind": "personal_absolute_path",
            "count": len(personal),
            "files": personal,
            "action": "excluded from the export; never publish these paths",
        })
    if secret_like:
        risks.append({
            "kind": "secret_like_path",
            "count": len(secret_like),
            "files": secret_like,
            "action": "excluded from the export",
        })
    if unsafe_links:
        risks.append({
            "kind": "unsafe_symlink",
            "count": len(unsafe_links),
            "files": unsafe_links,
            "action": "excluded from the export",
        })
    if model_weights:
        risks.append({
            "kind": "local_model_weight",
            "count": len(model_weights),
            "files": model_weights,
            "action": "excluded from the export",
        })
    if contributor_emails:
        risks.append({
            "kind": "upstream_contributor_emails",
            "count": len(contributor_emails),
            "note": (
                "upstream contributor email -> GitHub login mappings are included "
                "as public attribution data; they are not operator state"
            ),
        })

    return {
        "schema": "qubit.public_tree_export.v1",
        "source": {
            "commit": commit,
            "tree": tree,
            "tracked_files": len(tracked),
        },
        "included_files": len(included),
        "excluded_files": len(tracked) - len(included),
        "included_by_top_level": dict(sorted(by_top.items())),
        "excluded_by_reason": {
            reason: count
            for reason, count in sorted(counts.items())
            if reason != "included"
        },
        "excluded_examples": {
            reason: paths[:10] for reason, paths in sorted(excluded.items())
        },
        "residual_risks": risks,
        "public_docs": public_docs,
        "public_root_files": public_root_files,
        "product_surfaces": surfaces,
        "included_paths": included,
    }


def _public_manifest(report: dict) -> str:
    """Small public manifest.  It intentionally omits operator file names."""
    lines = [
        "# Public tree export manifest",
        "",
        "Generated by `scripts/export_public_tree.py` from tracked Git objects.",
        "",
        "## Source",
        "",
        f"- Commit: `{report['source']['commit']}`",
        f"- Tree: `{report['source']['tree']}`",
        f"- Tracked files considered: {report['source']['tracked_files']}",
        "",
        "## Counts",
        "",
        f"- Included files: {report['included_files']}",
        f"- Excluded files: {report['excluded_files']}",
        "",
        "### Included by top level",
        "",
    ]
    for top, count in report["included_by_top_level"].items():
        lines.append(f"- `{top}`: {count}")
    lines += ["", "### Excluded by reason", ""]
    for reason, count in report["excluded_by_reason"].items():
        lines.append(f"- {reason}: {count}")
    lines += ["", "## Public docs", ""]
    for path in report.get("public_docs", []):
        lines.append(f"- `{path}`")
    lines += ["", "## Root files", ""]
    for path in report.get("public_root_files", []):
        lines.append(f"- `{path}`")
    lines += ["", "## Product surfaces", ""]
    for label, surface in report.get("product_surfaces", {}).items():
        state = "present" if surface["present"] else "MISSING"
        lines.append(
            f"- `{label}`: {state} ({surface['files']} file(s) under "
            f"`{surface['path']}`)"
        )
    lines += ["", "## Local model recipe", ""]
    if "Modelfile.qubit.example" in report.get("included_paths", []):
        lines.append(
            "- `Modelfile.qubit.example` ships as the sanitized public recipe. "
            "The operator-specific `Modelfile.qubit` is excluded."
        )
    else:
        lines.append(
            "- No public model recipe is included; local model setup is not "
            "part of this export."
        )
    lines += [
        "",
        "## Packaging",
        "",
        "The full product wheel force-includes a few internal docs and",
        "generated/archive provenance records that this public snapshot omits",
        "on purpose. `scripts/public_export_packaging.py` drops only those known",
        "sources when the exported manifest marker is present; any other missing",
        "force-include source still fails the build. A wheel built here therefore",
        "ships the public docs and product surfaces without the internal set.",
        "",
        "Some exported product assets (the committed TUI and plugin `dist`",
        "bundles) are force-tracked in the origin despite the exported",
        "`.gitignore`, which still matches them. If you initialise a Git",
        "repository from this snapshot, stage with `git add -A -f`; a plain",
        "`git add -A` would silently drop those assets.",
    ]
    lines += ["", "## Residual risks", ""]
    if not report["residual_risks"]:
        lines.append("- None recorded.")
    for risk in report["residual_risks"]:
        if risk["kind"] == "upstream_contributor_emails":
            lines.append(f"- {risk['kind']}: {risk['count']} — {risk['note']}")
        else:
            lines.append(
                f"- {risk['kind']}: {risk['count']} file(s) {risk['action']}"
            )
    lines += [
        "",
        "Operator path names and excluded internal file names are deliberately",
        "not listed in this public manifest; run `--check` for the full report.",
        "",
    ]
    return "\n".join(lines)


def _ensure_dest(repo: Path, dest: Path) -> None:
    resolved_repo = repo.resolve()
    resolved_dest = dest.resolve()
    if resolved_dest == resolved_repo or resolved_repo in resolved_dest.parents:
        raise SystemExit(
            "REFUSING: --dest must be outside the source checkout (%s)" % resolved_repo
        )
    if dest.exists():
        if not dest.is_dir():
            raise SystemExit("REFUSING: --dest exists and is not a directory")
        if any(dest.iterdir()):
            raise SystemExit("REFUSING: --dest exists and is not empty")
    else:
        dest.mkdir(parents=True)


def _ensure_parent_dirs(dest_root: Path, rel_parent: PurePosixPath) -> Path:
    current = dest_root
    for part in rel_parent.parts:
        current = current / part
        if current.is_symlink():
            raise SystemExit("REFUSING: refusing to write through symlink %s" % current)
        if current.exists() and not current.is_dir():
            raise SystemExit("REFUSING: parent path is not a directory: %s" % current)
        current.mkdir(exist_ok=True)
    return current


def _write_member(dest_root: Path, tar: tarfile.TarFile, member: tarfile.TarInfo) -> None:
    name = member.name
    if not _safe_member_name(name):
        raise SystemExit("REFUSING: unsafe archive member path %r" % name)
    rel = PurePosixPath(name)
    parent = _ensure_parent_dirs(dest_root, rel.parent)
    out = parent / rel.name
    if out.exists() or out.is_symlink():
        raise SystemExit("REFUSING: destination already exists: %s" % out)
    if member.issym():
        os.symlink(member.linkname, out)
        os.utime(out, (DETERMINISTIC_MTIME, DETERMINISTIC_MTIME), follow_symlinks=False)
        return
    if not member.isfile():
        return
    source = tar.extractfile(member)
    if source is None:
        raise SystemExit("REFUSING: archive member has no data: %s" % name)
    with open(out, "wb") as handle:
        shutil.copyfileobj(source, handle, length=1024 * 1024)
    os.chmod(out, member.mode & 0o777)
    os.utime(out, (DETERMINISTIC_MTIME, DETERMINISTIC_MTIME))


def _export(repo: Path, dest: Path, selected: list[str]) -> None:
    wanted = set(selected)
    proc = subprocess.Popen(
        [
            "git",
            "-C",
            str(repo),
            "archive",
            "--format=tar",
            "--mtime=%s" % ARCHIVE_MTIME,
            "HEAD",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert proc.stdout is not None
    try:
        with tarfile.open(fileobj=proc.stdout, mode="r|") as tar:
            for member in tar:
                if member.name in wanted:
                    _write_member(dest, tar, member)
    finally:
        proc.stdout.close()
        stderr = proc.stderr.read() if proc.stderr is not None else b""
        if proc.stderr is not None:
            proc.stderr.close()
        returncode = proc.wait()
    if returncode != 0:
        raise SystemExit(
            "git archive failed: %s" % stderr.decode("utf-8", "replace").strip()
        )


def _summary(report: dict) -> str:
    lines = [
        "Qubit public tree export",
        "  source commit : %s" % report["source"]["commit"],
        "  tracked files : %d" % report["source"]["tracked_files"],
        "  included      : %d" % report["included_files"],
        "  excluded      : %d" % report["excluded_files"],
        "",
        "Excluded by reason:",
    ]
    for reason, count in report["excluded_by_reason"].items():
        if reason == "included":
            continue
        lines.append("  %-20s %d" % (reason, count))
    lines.append("")
    lines.append("Residual risks:")
    if not report["residual_risks"]:
        lines.append("  none")
    for risk in report["residual_risks"]:
        if risk["kind"] == "upstream_contributor_emails":
            lines.append("  %s: %d (%s)" % (risk["kind"], risk["count"], risk["note"]))
            continue
        lines.append("  %s: %d file(s) — %s" % (
            risk["kind"], risk["count"], risk["action"]
        ))
        for path in risk["files"]:
            lines.append("    - %s" % path)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export a deterministic public-safe Qubit source snapshot.",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=REPO,
        help="checkout to export (default: this script's repository)",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--check",
        action="store_true",
        help="dry run: report the manifest without writing anything",
    )
    mode.add_argument(
        "--dest",
        type=Path,
        help="empty or missing directory to receive the exported tree",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit the full machine-readable manifest to stdout",
    )
    parser.add_argument(
        "--fail-on-risk",
        action="store_true",
        help="exit 1 when personal paths, secret-like paths, or unsafe symlinks are found",
    )
    parser.add_argument(
        "--personal-path-marker",
        action="append",
        default=None,
        metavar="TEXT",
        help=(
            "operator path marker to exclude/report (repeatable; default: "
            + ", ".join(PERSONAL_PATH_MARKERS)
            + ")"
        ),
    )
    args = parser.parse_args(argv)

    repo = args.repo.resolve()
    if not (repo / ".git").exists():
        raise SystemExit("REFUSING: --repo is not a Git checkout: %s" % repo)

    commit, tree = _source_identity(repo)
    tracked = _tracked_paths(repo)
    markers = tuple(args.personal_path_marker or PERSONAL_PATH_MARKERS)
    personal = _personal_path_files(repo, markers)
    symlinks = _symlink_targets(repo)
    included, excluded, counts = _select(tracked, personal, symlinks)
    report = _build_report(
        repo, commit, tree, tracked, included, excluded, counts, personal, symlinks
    )

    if args.check:
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(_summary(report))
        risks = [r for r in report["residual_risks"] if r["kind"] != "upstream_contributor_emails"]
        if args.fail_on_risk and risks:
            return 1
        return 0

    dest = args.dest
    assert dest is not None
    _ensure_dest(repo, dest)
    _export(repo, dest, included)
    manifest_path = dest / "PUBLIC_TREE_MANIFEST.md"
    manifest_path.write_text(_public_manifest(report), encoding="utf-8")
    os.utime(manifest_path, (DETERMINISTIC_MTIME, DETERMINISTIC_MTIME))

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(_summary(report))
        print()
        print("Exported %d files to %s" % (len(included), dest))
        print("Manifest: %s" % manifest_path)

    risks = [r for r in report["residual_risks"] if r["kind"] != "upstream_contributor_emails"]
    if args.fail_on_risk and risks:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

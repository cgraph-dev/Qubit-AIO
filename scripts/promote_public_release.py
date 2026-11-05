#!/usr/bin/env python3
"""Promote a chosen Qubit-AIO commit into the public ``cgraph-dev/Qubit`` tree.

This tool is the repeatable bridge between the private development repository
and the public production repository. It never exports the working tree: the
snapshot is built from tracked Git objects at an explicit source commit.

The pipeline is:

1. create a temporary detached worktree at ``--source-commit`` so the existing
   exporter sees that commit as ``HEAD``;
2. export the public tree with ``scripts/export_public_tree.py`` (allowlisted
   docs, no operator paths, no models, no local state);
3. prune build metadata whose excluded sources would break a standalone build;
4. verify the seed (counts, docs allowlist, no personal paths/models/secrets);
5. initialize a fresh single-commit repository, write ``VERSION``, and create
   the release tag;
6. compute checksums and, when a remote is supplied, the changed paths between
   the remote ``production`` branch and the new seed commit;
7. only with ``--apply`` push the seed commit to ``production`` using an
   explicit ``--force-with-lease``. ``main`` (and every branch other than
   ``production``) is refused before any network write.

Dry-run is the default. ``--apply`` is the only way to push, and it requires
``--remote-url``. No service or live configuration is touched.

Usage::

    python3 scripts/promote_public_release.py --source-commit HEAD
    python3 scripts/promote_public_release.py \
        --source-commit 8d5de4acf --version 0.0.1 \
        --remote-url https://github.com/cgraph-dev/Qubit.git
    python3 scripts/promote_public_release.py \
        --source-commit 8d5de4acf --version 0.0.1 --apply \
        --remote-url https://github.com/cgraph-dev/Qubit.git
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
DEFAULT_VERSION = "0.0.1"
DEFAULT_BRANCH = "production"

#: The only public branch this tool may write. ``main``/``master`` are listed
#: separately so the refusal message can say exactly why they are rejected.
TARGET_BRANCHES = frozenset({"production"})
PROTECTED_BRANCHES = frozenset({"main", "master", "HEAD"})

VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+([.-][0-9A-Za-z.-]+)?$")
CREDENTIAL_URL_RE = re.compile(r"(?P<scheme>[a-z][a-z0-9+.-]*://)[^/@\s]+@")


class PromoteError(SystemExit):
    """Typed refusal/error. The message is printed verbatim."""


def _redact_url(url: str | None) -> str:
    if not url:
        return ""
    return CREDENTIAL_URL_RE.sub(r"\g<scheme>***@", url)


def _run(cmd: list[str], cwd: Path | None = None, timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout,
    )


def _require_ok(proc: subprocess.CompletedProcess, label: str) -> str:
    if proc.returncode != 0:
        raise PromoteError(
            "PROMOTE_COMMAND_FAILED: %s (exit %d)\n%s"
            % (label, proc.returncode, _redact_url(proc.stdout.strip()))
        )
    return proc.stdout.strip()


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise PromoteError("PROMOTE_IMPORT_FAILED: %s" % path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _seed_module():
    return _load_module("prepare_release_seed", SCRIPTS / "prepare_release_seed.py")


def _git(repo: Path, *args: str, check: bool = True, timeout: int = 600) -> subprocess.CompletedProcess:
    proc = _run(["git", "-C", str(repo), *args], timeout=timeout)
    if check and proc.returncode != 0:
        raise PromoteError(
            "PROMOTE_GIT_FAILED: git %s (exit %d)\n%s"
            % (" ".join(args), proc.returncode, proc.stdout.strip())
        )
    return proc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@contextlib.contextmanager
def fixed_git_dates(repo: Path, ref: str):
    """Pin author/committer dates to the source commit's date.

    The seed commit and its annotated tag then depend only on the source
    commit, version and tag, not on when the promotion happened. Re-running
    the same promotion produces the same seed commit and archive checksum.
    """
    stamp = _git(repo, "show", "-s", "--format=%cI", ref).stdout.strip()
    if not stamp:
        stamp = "2000-01-01T00:00:00+00:00"
    keys = ("GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE")
    previous = {key: os.environ.get(key) for key in keys}
    os.environ["GIT_AUTHOR_DATE"] = stamp
    os.environ["GIT_COMMITTER_DATE"] = stamp
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def resolve_commit(repo: Path, ref: str) -> str:
    proc = _git(repo, "rev-parse", "--verify", "%s^{commit}" % ref, check=False)
    if proc.returncode != 0:
        raise PromoteError("PROMOTE_SOURCE_COMMIT_UNKNOWN: %s" % ref)
    return proc.stdout.strip()


def export_at_commit(repo: Path, commit: str, dest: Path, force: bool, seed) -> dict:
    """Export ``commit`` by attaching a temporary detached worktree at it."""
    worktree_parent = Path(tempfile.mkdtemp(prefix="qubit-promote-source-"))
    worktree = worktree_parent / "src"
    added = False
    try:
        _require_ok(
            _git(repo, "worktree", "add", "--detach", "--force", str(worktree), commit),
            "git worktree add",
        )
        added = True
        report = seed.export_tree(worktree, dest, force)
        return report
    finally:
        if added:
            _git(repo, "worktree", "remove", "--force", str(worktree), check=False)
            _git(repo, "worktree", "prune", check=False)
        shutil.rmtree(worktree_parent, ignore_errors=True)


def remote_branch_sha(url: str, branch: str) -> str | None:
    proc = _run(
        ["git", "ls-remote", "--heads", url, "refs/heads/%s" % branch],
        timeout=300,
    )
    if proc.returncode != 0:
        raise PromoteError(
            "PROMOTE_REMOTE_UNREACHABLE: %s\n%s"
            % (_redact_url(url), proc.stdout.strip()[-1000:])
        )
    for line in proc.stdout.splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[1] == "refs/heads/%s" % branch:
            return fields[0]
    return None


def remote_tag_sha(url: str, tag: str) -> str | None:
    proc = _run(
        ["git", "ls-remote", "--tags", url, "refs/tags/%s" % tag],
        timeout=300,
    )
    if proc.returncode != 0:
        raise PromoteError(
            "PROMOTE_REMOTE_UNREACHABLE: %s\n%s"
            % (_redact_url(url), proc.stdout.strip()[-1000:])
        )
    direct: str | None = None
    peeled: str | None = None
    for line in proc.stdout.splitlines():
        fields = line.split()
        if len(fields) != 2:
            continue
        if fields[1] == "refs/tags/%s" % tag:
            direct = fields[0]
        elif fields[1] == "refs/tags/%s^{}" % tag:
            peeled = fields[0]
    # An annotated tag resolves to a tag object; callers compare commits, so
    # prefer the peeled ``^{}`` line when the remote advertises it.
    return peeled or direct


def _changed_paths(dest: Path, seed_commit: str, url: str, branch: str, base: str | None) -> list[dict]:
    if base is None:
        proc = _git(dest, "ls-tree", "-r", "--name-only", seed_commit)
        return [{"status": "A", "path": line} for line in proc.stdout.splitlines() if line]
    proc = _git(
        dest,
        "fetch",
        "--quiet",
        "--no-tags",
        url,
        "refs/heads/%s" % branch,
        check=False,
        timeout=300,
    )
    if proc.returncode != 0:
        raise PromoteError(
            "PROMOTE_FETCH_FAILED: %s\n%s"
            % (_redact_url(url), proc.stdout.strip()[-1000:])
        )
    fetched = _git(dest, "rev-parse", "FETCH_HEAD").stdout.strip()
    if fetched != base:
        # The branch moved between ls-remote and fetch; report the move rather
        # than silently diffing an object the caller did not expect.
        raise PromoteError(
            "PROMOTE_REMOTE_MOVED: %s was %s, now %s"
            % (branch, base, fetched)
        )
    proc = _git(dest, "diff", "--name-status", "--find-renames", base, seed_commit)
    changed: list[dict] = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0]
        if status.startswith("R") or status.startswith("C"):
            if len(parts) >= 3:
                changed.append({"status": status, "path": parts[2], "from": parts[1]})
            continue
        if len(parts) >= 2:
            changed.append({"status": status, "path": parts[1]})
    return changed


def _apply_push(
    dest: Path,
    seed_commit: str,
    url: str,
    branch: str,
    base: str | None,
    tag: str,
    push_tag: bool,
) -> None:
    if branch in PROTECTED_BRANCHES:
        raise PromoteError("PROMOTE_BRANCH_PROTECTED: refusing to update %s" % branch)
    if branch not in TARGET_BRANCHES:
        raise PromoteError(
            "PROMOTE_BRANCH_NOT_ALLOWED: only %s may be updated, not %s"
            % (", ".join(sorted(TARGET_BRANCHES)), branch)
        )

    if push_tag:
        existing_tag = remote_tag_sha(url, tag)
        if existing_tag is not None and existing_tag != seed_commit:
            raise PromoteError(
                "PROMOTE_TAG_EXISTS: %s points at %s on the remote, not %s; refusing to rewrite it"
                % (tag, existing_tag, seed_commit)
            )
        if existing_tag is None:
            _require_ok(
                _git(dest, "push", url, "refs/tags/%s" % tag),
                "git push tag",
            )

    # An explicit lease pins the exact remote value we measured; an empty
    # expected value means "the branch must not exist yet".
    lease = "--force-with-lease=refs/heads/%s:%s" % (branch, base or "")
    _require_ok(
        _git(dest, "push", lease, url, "%s:refs/heads/%s" % (seed_commit, branch)),
        "git push production",
    )


def _seed_release_gate(dest: Path) -> dict:
    """Run the fresh-install contract gate against the exported seed.

    The seed has no built dashboard (the public tree ships source and builds it
    on install), so the gate runs without a dashboard requirement. A FAIL is a
    promotion blocker; a DEGRADED result means the fresh-install probe was not
    requested here and is covered by the production release-asset gate.
    """
    cmd = [
        sys.executable,
        str(SCRIPTS / "release_fresh_install_gate.py"),
        "--repo", str(dest),
        "--no-dashboard",
        "--json",
    ]
    proc = _run(cmd, timeout=600)
    try:
        report = json.loads(proc.stdout or "")
    except json.JSONDecodeError:
        raise PromoteError(
            "PROMOTE_RELEASE_GATE_FAILED: gate did not return JSON (rc=%s): %s"
            % (proc.returncode, (proc.stderr or proc.stdout or "")[-2000:])
        )
    if report.get("verdict") == "FAIL":
        failed = [
            "%s=%s %s" % (check.get("name"), check.get("state"),
                          check.get("detail", ""))
            for check in report.get("checks", [])
            if check.get("state") == "FAIL"
        ]
        raise PromoteError(
            "PROMOTE_RELEASE_GATE_FAILED: " + "; ".join(failed[:10])
        )
    return report


def _checks(seed_commit: str, seed_tree: str, archive: Path) -> dict:
    return {
        "seed_commit": seed_commit,
        "seed_tree": seed_tree,
        "archive": archive.name,
        "archive_sha256": _sha256(archive),
    }


def _write_report(
    path_md: Path,
    path_json: Path,
    *,
    report: dict,
    version: str,
    tag: str,
    seed_commit: str,
    seed_tree: str,
    checksums: dict,
    remote: dict,
    changed: list[dict],
    applied: bool,
    push_tag: bool,
    removed_refs: list[str],
    release_gate: dict | None = None,
) -> None:
    source = report["source"]
    added = sum(1 for row in changed if row["status"].startswith("A"))
    modified = sum(1 for row in changed if row["status"].startswith(("M", "R", "C")))
    deleted = sum(1 for row in changed if row["status"].startswith("D"))
    payload = {
        "source": {
            "commit": source["commit"],
            "tree": source["tree"],
            "tracked_files": source["tracked_files"],
        },
        "seed": {
            "commit": seed_commit,
            "tree": seed_tree,
            "version": version,
            "tag": tag,
            "tracked_files": report["included_files"] + (
                1 if "VERSION" in report.get("public_root_files", ()) else 2
            ),
        },
        "export": {
            "included_files": report["included_files"],
            "excluded_files": report["excluded_files"],
            "excluded_by_reason": report["excluded_by_reason"],
            "public_docs": report["public_docs"],
            "pruned_build_refs": removed_refs,
        },
        "checksums": checksums,
        "remote": remote,
        "applied": applied,
        "push_tag": push_tag,
        "changed_paths": changed,
        "changed_counts": {"added": added, "modified": modified, "deleted": deleted},
        "release_gate": release_gate,
    }
    path_json.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    lines = [
        "# Qubit public production promotion",
        "",
        "Generated by `scripts/promote_public_release.py`. Dry-run unless `applied` is true.",
        "",
        "## Source (Qubit-AIO)",
        "",
        "- Commit: `%s`" % source["commit"],
        "- Tree: `%s`" % source["tree"],
        "- Tracked files: %d" % source["tracked_files"],
        "",
        "## Seed commit",
        "",
        "- Version: `%s`" % version,
        "- Tag: `%s`" % tag,
        "- Commit: `%s`" % seed_commit,
        "- Tree: `%s`" % seed_tree,
        "- History: one commit, no source history, no remotes in the seed",
        "",
        "## Checksums (SHA-256)",
        "",
        "- Seed commit: `%s`" % checksums["seed_commit"],
        "- Seed tree: `%s`" % checksums["seed_tree"],
        "- Archive `%s`: `%s`" % (checksums["archive"], checksums["archive_sha256"]),
        "",
        "## Export",
        "",
        "- Included files: %d" % report["included_files"],
        "- Excluded files: %d" % report["excluded_files"],
        "- Public docs: %s" % (", ".join(report["public_docs"]) or "(none)"),
        "- Pruned build refs: %s" % (", ".join(removed_refs) or "(none)"),
        "- Release gate: `%s`" % (
            (release_gate or {}).get("verdict", "(not run)")
        ),
        "",
        "## Remote",
        "",
        "- URL: `%s`" % (remote.get("url") or "(not supplied)"),
        "- Branch: `%s`" % remote.get("branch", DEFAULT_BRANCH),
        "- Current remote commit: `%s`" % (remote.get("current_commit") or "(branch absent)"),
        "- Applied: `%s`" % ("true" if applied else "false"),
        "- Force-with-lease: `%s`" % ("true" if applied else "not used (dry-run)"),
        "",
        "## Changed paths",
        "",
        "- Base: `%s`" % (remote.get("current_commit") or "(new branch; all paths added)"),
        "- Added: %d, modified: %d, deleted: %d" % (added, modified, deleted),
        "",
    ]
    limit = 200
    for row in changed[:limit]:
        lines.append("- `%s` `%s`" % (row["status"], row["path"]))
    if len(changed) > limit:
        lines.append("- ... %d more (see the JSON report)" % (len(changed) - limit))
    lines += [
        "",
        "## Next step",
        "",
    ]
    if applied:
        lines.append("The seed commit was pushed to `%s`." % remote.get("branch", DEFAULT_BRANCH))
    else:
        lines += [
            "Review this report, then promote explicitly:",
            "",
            "```bash",
            "python3 scripts/promote_public_release.py \\",
            "  --source-commit %s --version %s \\" % (source["commit"], version),
            "  --apply --remote-url %s" % (remote.get("url") or "<public remote>"),
            "```",
        ]
    path_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Promote a Qubit-AIO commit to the public production branch."
    )
    parser.add_argument("--repo", type=Path, default=REPO, help="Qubit-AIO checkout")
    parser.add_argument("--source-commit", default="HEAD", help="commit/ref to export (default HEAD)")
    parser.add_argument("--dest", type=Path, required=True, help="seed directory to create")
    parser.add_argument("--version", default=DEFAULT_VERSION)
    parser.add_argument("--tag", default=None)
    parser.add_argument("--branch", default=DEFAULT_BRANCH)
    parser.add_argument("--remote-url", default=None)
    parser.add_argument("--apply", action="store_true", help="push to production with force-with-lease")
    parser.add_argument("--push-tag", action="store_true", help="also push the release tag (never forced)")
    parser.add_argument("--force", action="store_true", help="replace an existing --dest")
    parser.add_argument("--report", type=Path, default=None)
    args = parser.parse_args(argv)

    repo = args.repo.resolve()
    dest = args.dest.resolve()
    version = args.version.lstrip("v")
    tag = args.tag or ("v" + version)
    branch = args.branch
    report_md = args.report or Path(str(dest) + "-promote.md")
    report_json = (
        report_md.with_suffix(".json")
        if report_md.suffix
        else report_md.with_name(report_md.name + ".json")
    )

    if not VERSION_RE.match(version):
        raise PromoteError("PROMOTE_BAD_VERSION: %s" % version)
    if not tag or any(ch.isspace() for ch in tag):
        raise PromoteError("PROMOTE_BAD_TAG: %r" % tag)
    if branch in PROTECTED_BRANCHES:
        raise PromoteError("PROMOTE_BRANCH_PROTECTED: refusing to update %s" % branch)
    if branch not in TARGET_BRANCHES:
        raise PromoteError(
            "PROMOTE_BRANCH_NOT_ALLOWED: only %s may be updated, not %s"
            % (", ".join(sorted(TARGET_BRANCHES)), branch)
        )
    if args.apply and not args.remote_url:
        raise PromoteError("PROMOTE_REMOTE_REQUIRED: --apply needs --remote-url")
    if not (repo / ".git").exists():
        raise PromoteError("PROMOTE_NOT_A_REPO: %s" % repo)

    seed = _seed_module()

    source_commit = resolve_commit(repo, args.source_commit)
    work = Path(str(dest) + "-verify")
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)

    report = export_at_commit(repo, source_commit, dest, args.force, seed)
    exporter = report.pop("_exporter")
    seed.write_version(dest, version)
    removed_refs = list(
        dict.fromkeys(seed.prune_missing_build_refs(dest / "pyproject.toml", dest))
    )
    missing = seed.missing_build_refs(dest / "pyproject.toml", dest)

    checks = seed.Checks()
    checks.require("seed build metadata resolves", not missing, ", ".join(missing))
    seed.verify_export(dest, report, version, exporter.PUBLIC_DOC_FILES, checks)
    checks.finish()

    if report["source"]["commit"] != source_commit:
        raise PromoteError(
            "PROMOTE_SOURCE_MISMATCH: exported %s, expected %s"
            % (report["source"]["commit"], source_commit)
        )

    with fixed_git_dates(repo, source_commit):
        seed_commit = seed.init_git(
            dest,
            source_commit,
            tag,
            checks,
            seed.expected_seed_file_count(report),
        )
    checks.finish()

    release_gate = _seed_release_gate(dest)

    archive = seed.archive_seed(dest, tag, version, work / "artifacts")
    seed_tree = _git(dest, "rev-parse", "%s^{tree}" % seed_commit).stdout.strip()
    checksums = _checks(seed_commit, seed_tree, archive)

    current_remote: str | None = None
    changed: list[dict] = []
    if args.remote_url:
        current_remote = remote_branch_sha(args.remote_url, branch)
        changed = _changed_paths(dest, seed_commit, args.remote_url, branch, current_remote)

    remote = {
        "url": _redact_url(args.remote_url),
        "branch": branch,
        "current_commit": current_remote,
        "force_with_lease": False,
    }

    def write(applied: bool) -> None:
        _write_report(
            report_md,
            report_json,
            report=report,
            version=version,
            tag=tag,
            seed_commit=seed_commit,
            seed_tree=seed_tree,
            checksums=checksums,
            remote=remote,
            changed=changed,
            applied=applied,
            push_tag=args.push_tag,
            removed_refs=removed_refs,
            release_gate=release_gate,
        )

    # Write the dry-run report first so a failed push still leaves the exact
    # source/seed/checksum evidence behind for the operator.
    write(applied=False)

    applied = False
    if args.apply:
        _apply_push(
            dest,
            seed_commit,
            args.remote_url,
            branch,
            current_remote,
            tag,
            args.push_tag,
        )
        applied = True
        pushed = remote_branch_sha(args.remote_url, branch)
        if pushed != seed_commit:
            raise PromoteError(
                "PROMOTE_PUSH_UNCONFIRMED: %s is %s, expected %s"
                % (branch, pushed, seed_commit)
            )
        remote["pushed_commit"] = pushed
        remote["force_with_lease"] = True
        write(applied=True)

    print(
        "PROMOTE_%s source=%s seed=%s version=%s tag=%s branch=%s changed=%d applied=%s report=%s"
        % (
            "OK" if not applied else "APPLIED",
            source_commit,
            seed_commit,
            version,
            tag,
            branch,
            len(changed),
            "true" if applied else "false",
            report_md,
        )
    )
    if removed_refs:
        print("PROMOTE_PRUNED_BUILD_REFS=%s" % ",".join(removed_refs), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

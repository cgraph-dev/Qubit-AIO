#!/usr/bin/env python3
"""Machine-checkable guard for the repo's COMMIT-AS-YOU-GO rule (AGENTS.md).

The rule exists because this repo has repeatedly lost agents' work: a process
died mid-run and the only thing that survived was what had already been
committed, or a concurrent session held files uncommitted for over an hour.

This script reports, for the CURRENT repo state, every way the rule is being
violated *right now*.  One finding per line, `file:line`-ish precision, exit 0
when clean and 1 when any violation is present.

Findings are classified:

  VIOLATION  the rule is being breached (dirty tree with no live writer, work
             stranded on a side branch, a staged secret, ...)
  HANDS-OFF  something is dirty but a live writer is mid-write on it.  That is
             NOT a violation - it must be reported so nobody commits over it.
  WARNING    suspicious but not provably a breach (skipped tests, unparsable
             files, ...).  Never causes a non-zero exit.

Deliberate design notes (each one is a trap that was PROVEN in this repo):

* Process liveness is NEVER inferred from an argv substring.  'serve' matches
  'observe' and 'preserve'; a substring match on a command line is not evidence
  that *our* process is alive.  We delegate to the canonical matcher
  ``qubit_runtime.gateway.status.looks_like_gateway_command_line`` when it is
  importable, and only ever use it to *corroborate* - never as the primary
  signal.

* Bare PID existence is NOT evidence either: PIDs are recycled.  The primary
  liveness signal is the *writer probe*: hash every dirty path, wait a real
  interval (>= 40s), hash again.  Unchanged bytes plus a fresh mtime at the
  second sample means a live writer.  A process whose argv matches the
  canonical matcher is used only to shorten the wait.

* We never read another worktree's files to hash them: a dirty file elsewhere
  may be actively written, and hashing it produces false "writer" readings.
  Cross-worktree dirtiness is reported as a violation *with* a note that the
  check did not probe it.

Usage::

    scripts/check_commit_hygiene.py                 # human readable
    scripts/check_commit_hygiene.py --json          # machine readable
    scripts/check_commit_hygiene.py --repo PATH     # check a specific repo
    scripts/check_commit_hygiene.py --stale-minutes 30 --probe-seconds 40
    scripts/check_commit_hygiene.py --no-cross-worktree   # this worktree only
    scripts/check_commit_hygiene.py --report PATH   # also write a markdown report

Exit codes: 0 = clean, 1 = at least one violation, 2 = the check could not run.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

# --------------------------------------------------------------------------
# Finding model
# --------------------------------------------------------------------------

VIOLATION = "VIOLATION"
HANDS_OFF = "HANDS_OFF"
WARNING = "WARNING"

_EXIT_FOR_KIND = {VIOLATION: 1, HANDS_OFF: 0, WARNING: 0}


@dataclass
class Finding:
    kind: str            # VIOLATION | HANDS_OFF | WARNING
    check: str           # stable machine id, e.g. "dirty_worktree_no_writer"
    location: str        # path or ref, with :line where a line is meaningful
    detail: str
    remedy: str = ""

    def format(self) -> str:
        label = {VIOLATION: "VIOLATION", HANDS_OFF: "HANDS-OFF", WARNING: "WARNING"}[self.kind]
        base = f"{label} [{self.check}] {self.location}: {self.detail}"
        return f"{base} -- {self.remedy}" if self.remedy else base

    def to_dict(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "check": self.check,
            "location": self.location,
            "detail": self.detail,
            "remedy": self.remedy,
            "line": self.format(),
        }


@dataclass
class DirtyPath:
    """One uncommitted path in one worktree."""
    relpath: str
    status: str          # two-char porcelain code, e.g. " M", "??", "A "
    abspath: Optional[Path]  # None when we refuse to touch another worktree
    mtime: Optional[float]
    size: Optional[int]
    staged: bool
    contains_secret: Optional[bool] = None

    @property
    def path_identity(self) -> str:
        return f"{self.relpath}:{self.status.strip() or '??'}"


# --------------------------------------------------------------------------
# git plumbing (shelling out, never shell=True)
# --------------------------------------------------------------------------

class GitError(RuntimeError):
    pass


def git(repo: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        errors="replace",
    )
    if check and proc.returncode != 0:
        raise GitError(
            f"git {' '.join(args)} failed ({proc.returncode}) in {repo}: {proc.stderr.strip()}"
        )
    return proc.stdout


def git_ok(repo: Path, *args: str) -> str:
    return git(repo, *args, check=False)


def assert_is_git_repo(repo: Path) -> None:
    """Fail loudly when ``repo`` is not a git worktree.

    A guard that reports "clean" because it could not ask git is worse than no
    guard: it is the exact failure mode this exercise exists to prevent.
    """
    if not repo.is_dir():
        raise GitError(f"{repo} is not a directory")
    proc = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--is-inside-work-tree"],
        capture_output=True, text=True, errors="replace",
    )
    if proc.returncode != 0 or proc.stdout.strip() != "true":
        raise GitError(f"{repo} is not a git worktree: {proc.stderr.strip() or proc.stdout.strip()}")


def parse_porcelain(raw: str) -> list[tuple[str, str]]:
    """Parse ``git status --porcelain=v1 -z`` into (status, path) pairs.

    Renames/copies emit two NUL-separated fields; the *destination* path is the
    interesting one for hygiene purposes.
    """
    fields = raw.split("\0")
    out: list[tuple[str, str]] = []
    i = 0
    while i < len(fields):
        entry = fields[i]
        if not entry:
            i += 1
            continue
        if len(entry) < 4:
            i += 1
            continue
        status, path = entry[:2], entry[3:]
        if status[0] in ("R", "C") and i + 1 < len(fields):
            # next field is the source path; keep the destination
            i += 1
        out.append((status, path))
        i += 1
    return out


# --------------------------------------------------------------------------
# Writer detection - the honest part
# --------------------------------------------------------------------------

# Basenames that are secrets if they ever get staged.  Anchored, because
# ".env.example" is a template and must not trip the guard.
_SECRET_BASENAMES = (
    ".env",
    ".env.local",
    ".env.production",
    ".env.prod",
    ".env.development",
    ".env.dev",
    ".env.test",
    ".envrc",
    "credentials",
    "credentials.json",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
)
_SECRET_PATTERNS = (
    re.compile(r"(^|/)\.env\.[A-Za-z0-9_.-]+$"),          # .env.<anything>
    re.compile(r"(^|/)[^/]*(secret|credential)[^/]*$", re.I),
    re.compile(r"(^|/)\.netrc$"),
    re.compile(r"(^|/)\.pem$"),
    re.compile(r"\.pem$"),
    re.compile(r"^.*\.key$"),
)
_SECRET_ALLOWLIST = re.compile(r"\.(example|sample|template|dist)$", re.I)


def looks_like_secret(relpath: str) -> bool:
    name = Path(relpath).name
    if _SECRET_ALLOWLIST.search(name):
        return False
    if name in _SECRET_BASENAMES:
        return True
    return any(p.search(relpath) for p in _SECRET_PATTERNS)


def hash_file(path: Path) -> Optional[str]:
    try:
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 16), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def snapshot_paths(paths: Iterable[Path]) -> dict[str, str]:
    """Map abspath -> sha256 ('' for unreadable/deleted)."""
    out: dict[str, str] = {}
    for p in paths:
        out[str(p)] = hash_file(p) or ""
    return out


def stat_path(p: Path) -> tuple[Optional[float], Optional[int]]:
    try:
        st = p.lstat()
        return st.st_mtime, st.st_size
    except OSError:
        return None, None


def canonical_gateway_matcher():
    """Return the repo's canonical command-line matcher, or None.

    Liveness must not be inferred from an argv substring, so we do not write
    our own.  If the canonical module is importable we use it verbatim; if not
    (e.g. a temp repo in a test), there is simply no process-corroboration
    signal and the writer probe stands alone.

    The repo's ``qubit_runtime/`` directory itself must be on ``sys.path``:
    ``gateway.status`` imports ``qubit_constants`` as a *top-level* module
    (``qubit_runtime/qubit_constants.py``), so running this script by path
    cannot import it without that extra entry.
    """
    repo_root = Path(__file__).resolve().parents[1]
    for entry in (str(repo_root / "qubit_runtime"), str(repo_root)):
        if entry not in sys.path:
            sys.path.insert(0, entry)
    try:
        from qubit_runtime.gateway.status import (  # type: ignore
            looks_like_gateway_command_line,
        )
    except Exception:
        try:
            from gateway.status import (  # type: ignore
                looks_like_gateway_command_line,
            )
        except Exception:
            return None
    return looks_like_gateway_command_line


def live_gateway_writers() -> list[tuple[int, str]]:
    """Live PIDs whose command line the CANONICAL matcher accepts.

    Returns [(pid, cmdline)].  An empty list means "no corroboration found",
    never "nothing is running": this is deliberately a weak, additive signal.
    """
    matcher = canonical_gateway_matcher()
    if matcher is None:
        return []
    out: list[tuple[int, str]] = []
    proc_root = Path("/proc")
    if not proc_root.is_dir():
        return out
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        if not raw:
            continue
        cmdline = raw.replace(b"\0", b" ").decode("utf-8", errors="ignore").strip()
        try:
            if matcher(cmdline):
                out.append((pid, cmdline))
        except Exception:
            continue
    return out


class WriterProbe:
    """Decides which dirty paths have a live writer behind them.

    Primary evidence is byte-stability over a real interval (>=40s): a path
    whose bytes changed AND whose mtime is newer than ``start`` is being
    written right now.  A path that changed is retried (up to ``rounds`` probe
    rounds) to catch writers that pause between saves.

    Never trusts: mtime alone, bare PID existence, argv substrings.
    """

    def __init__(
        self,
        probe_seconds: float = 40.0,
        rounds: int = 1,
        sleep=time.sleep,
    ) -> None:
        if probe_seconds < 40:
            raise ValueError("probe_seconds must be >= 40: a shorter window cannot outrun a save")
        self.probe_seconds = float(probe_seconds)
        self.rounds = max(1, int(rounds))
        self.sleep = sleep
        self.writers: dict[str, str] = {}         # path -> reason
        self.volatile: set[str] = set()           # changed at least once, no writer proven
        self.probe_ran = False
        self.seconds_waited = 0.0

    def run(self, dirty: list[DirtyPath]) -> None:
        candidates = [d for d in dirty if d.abspath is not None]
        if not candidates:
            return
        self.probe_ran = True
        remaining = list(candidates)
        for attempt in range(self.rounds):
            paths = [d.abspath for d in remaining if d.abspath is not None]
            assert all(p is not None for p in paths)
            before = snapshot_paths(paths)  # type: ignore[arg-type]
            time_before = time.time()
            self.sleep(self.probe_seconds)
            self.seconds_waited += self.probe_seconds
            after = snapshot_paths(paths)
            still: list[DirtyPath] = []
            for d in remaining:
                key = str(d.abspath)
                b, a = before.get(key), after.get(key)
                if b is None or b == a:
                    continue  # byte-stable: no writer observed
                self.volatile.add(key)
                mtime, _ = stat_path(d.abspath)  # type: ignore[arg-type]
                # A change is not by itself proof of a *live* writer: a commit
                # that landed during the window changes bytes too.  Require the
                # mtime to be inside the sampling window as well.
                if mtime is not None and mtime >= time_before - 1.0:
                    self.writers[key] = (
                        f"file bytes changed during a {self.probe_seconds:.0f}s probe "
                        f"and mtime is inside the window"
                    )
                else:
                    still.append(d)
            remaining = still
            if not remaining:
                break


# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------

def _untracked_or_modified(dirty: list[DirtyPath]) -> list[DirtyPath]:
    return [d for d in dirty if d.status != "!!"]


def check_dirty_worktree(
    worktree: Path,
    dirty: list[DirtyPath],
    probe: "WriterProbe",
    stale_minutes: float,
    is_primary: bool,
) -> list[Finding]:
    """Dirty tree with no owning live writer, or dirty tree still being written."""
    findings: list[Finding] = []
    if not dirty:
        return findings

    now = time.time()
    stale_cutoff = now - stale_minutes * 60.0

    writing: list[DirtyPath] = []
    quiet: list[DirtyPath] = []
    for d in dirty:
        key = str(d.abspath) if d.abspath is not None else None
        if key is not None and probe is not None and key in probe.writers:
            writing.append(d)
        else:
            quiet.append(d)

    prefix = "" if is_primary else f"[worktree {worktree}] "

    if writing:
        oldest = min(
            (d.mtime for d in writing if d.mtime is not None),
            default=None,
        )
        age = f"{max(0.0, (now - oldest) / 60.0):.1f}min old" if oldest else "age unknown"
        for d in writing[:1]:
            findings.append(Finding(
                HANDS_OFF,
                "dirty_worktree_live_writer",
                f"{prefix}{d.relpath}",
                f"uncommitted and bytes changed during the writer probe ({age}); "
                f"{len(writing)} path(s) still being written in {worktree}",
                "do not commit over a live writer; wait for it to stop, then "
                "re-hash over an interval before committing as theirs",
            ))
        if len(writing) > 1:
            for d in writing[1:]:
                findings.append(Finding(
                    HANDS_OFF,
                    "dirty_worktree_live_writer",
                    f"{prefix}{d.relpath}",
                    "uncommitted; bytes changed during the writer probe",
                    "hands off while the writer is live",
                ))
        return findings

    if probe is not None and not probe.probe_ran:
        # Everything dirty lives outside this worktree (or every path was
        # unreadable).  We cannot honestly claim "no writer".
        findings.append(Finding(
            VIOLATION,
            "dirty_worktree_unprobed",
            f"{prefix}{dirty[0].relpath}",
            f"{len(dirty)} uncommitted path(s) in {worktree}; the writer probe "
            f"could not read them, so absence of a writer is unproven",
            "inspect by hand, or re-run the check inside that worktree",
        ))
        return findings

    oldest_mtime = min((d.mtime for d in quiet if d.mtime is not None), default=None)
    oldest_age_min = (now - oldest_mtime) / 60.0 if oldest_mtime else None

    if oldest_age_min is None or oldest_age_min >= stale_minutes:
        for d in quiet:
            age = (
                f"{(now - d.mtime) / 60.0:.1f}min" if d.mtime is not None else "age unknown"
            )
            findings.append(Finding(
                VIOLATION,
                "dirty_worktree_no_writer",
                f"{prefix}{d.relpath}",
                f"uncommitted {d.status!r} for {age}, byte-stable across a "
                f"{probe.probe_seconds:.0f}s probe and no live writer found",
                "commit it now (as wip: if unfinished) - a dirty tree is not "
                "revertable, not attributable, and not visible to the next agent",
            ))
    else:
        phase = "stale pending probe" if probe is None or not probe.probe_ran else "recent"
        findings.append(Finding(
            HANDS_OFF,
            "dirty_worktree_fresh",
            f"{prefix}{quiet[0].relpath}",
            f"{len(quiet)} uncommitted path(s), newest {oldest_age_min:.1f}min old "
            f"(< {stale_minutes:.0f}min) - {phase}; byte-stable but within the "
            f"freshness window",
            "hands off: treat as an active writer, re-run the check later",
        ))
    return findings


def check_staged_secrets(dirty: list[DirtyPath], worktree: Path) -> list[Finding]:
    """Staged files that look like secrets."""
    findings: list[Finding] = []
    for d in dirty:
        if not d.staged:
            continue
        if looks_like_secret(d.relpath):
            findings.append(Finding(
                VIOLATION,
                "staged_secret",
                f"{worktree}/{d.relpath}",
                f"staged ({d.status!r}) and looks like a secret",
                "unstage it (git restore --staged) and add the path to .gitignore; "
                "COMMIT-AS-YOU-GO does not licence committing credentials",
            ))
        elif d.contains_secret:
            findings.append(Finding(
                VIOLATION,
                "staged_secret_content",
                f"{worktree}/{d.relpath}",
                "staged file content matches a private-key header",
                "unstage it and rotate the key if it was ever pushed",
            ))
    return findings


SECRET_CONTENT_MARKERS = (
    b"-----BEGIN RSA PRIVATE KEY-----",
    b"-----BEGIN OPENSSH PRIVATE KEY-----",
    b"-----BEGIN EC PRIVATE KEY-----",
    b"-----BEGIN PRIVATE KEY-----",
    b"aws_secret_access_key",
)

_SECRET_SCAN_MAX_BYTES = 512 * 1024


def staged_file_contains_secret(worktree: Path, relpath: str) -> bool:
    """Read the STAGED blob (not the worktree file) and look for key material."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(worktree), "show", f":{relpath}"],
            capture_output=True,
        )
    except OSError:
        return False
    if proc.returncode != 0:
        return False
    blob = proc.stdout[:_SECRET_SCAN_MAX_BYTES]
    return any(marker in blob for marker in SECRET_CONTENT_MARKERS)


def check_stranded_branch(worktree: Path, branch: str, trunk: str) -> list[Finding]:
    """Branch tip holds commits the main line cannot reach."""
    findings: list[Finding] = []
    tip = git_ok(worktree, "rev-parse", "HEAD").strip()
    if not tip:
        return findings

    ahead_raw = git_ok(worktree, "rev-list", "--count", f"{trunk}..HEAD").strip()
    ahead = int(ahead_raw) if ahead_raw.isdigit() else 0
    behind_raw = git_ok(worktree, "rev-list", "--count", f"HEAD..{trunk}").strip()
    behind = int(behind_raw) if behind_raw.isdigit() else 0

    if ahead == 0:
        # HEAD is fully contained in the trunk.  If the trunk is nevertheless
        # ahead of us, this branch is simply out of date, not stranded - that is
        # a WARNING (nobody is losing work), and saying "stranded" here would be
        # a false positive.
        if behind > 0:
            findings.append(Finding(
                WARNING,
                "branch_behind_trunk",
                f"{worktree}@{tip[:12]}",
                f"branch {branch!r} is {behind} commit(s) behind {trunk}; no work "
                f"is stranded, the branch is just stale",
                f"rebase or merge {trunk} when convenient",
            ))
        return findings

    dirty_note = ""
    porcelain = git_ok(worktree, "status", "--porcelain=v1", "-z")
    if parse_porcelain(porcelain):
        dirty_note = " (and the tree is dirty on top of it)"

    diverged = f" and {behind} behind" if behind else ""
    findings.append(Finding(
        VIOLATION,
        "stranded_branch_commits",
        f"{worktree}@{tip[:12]}",
        f"branch {branch!r} is {ahead} commit(s) ahead{diverged} of {trunk}"
        f"{dirty_note}; unreachable from the main line",
        f"push/merge {branch} or rebase it onto {trunk}; stranded commits are one "
        f"worktree prune away from being lost",
    ))

    # Detached HEAD: work not on any branch at all.
    if branch in ("", "HEAD"):
        findings.append(Finding(
            VIOLATION,
            "detached_head_work",
            f"{worktree}@{tip[:12]}",
            f"HEAD is detached at {tip[:12]} with {ahead} commit(s) off {trunk}",
            "create a branch (git switch -c <name>) so the commits are reachable",
        ))
    return findings


def processes_with_cwd_under(root: Path) -> list[tuple[int, str]]:
    """PIDs whose working directory is inside ``root`` (read-only, no hashing).

    Used for FOREIGN worktrees: we must not hash another agent's live files, so
    the strongest non-invasive signal is "a process is *working in that tree*
    right now".  ``/proc/<pid>/cwd`` is read-only and cannot perturb a writer.
    """
    root = root.resolve()
    out: list[tuple[int, str]] = []
    proc_root = Path("/proc")
    if not proc_root.is_dir():
        return out
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        try:
            cwd = Path(os.readlink(entry / "cwd"))
        except OSError:
            continue
        try:
            cwd.relative_to(root)
        except ValueError:
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            raw = b""
        cmd = raw.replace(b"\0", b" ").decode("utf-8", errors="ignore").strip()[:160]
        out.append((pid, cmd))
    return out


def check_other_worktrees(
    repo: Path,
    primary: Path,
    probe: "WriterProbe",
    stale_minutes: float,
    trunk: str,
    include_cross: bool,
) -> tuple[list[Finding], list[Finding]]:
    """Inspect every other worktree of the same repo.

    Returns (violations, hands_off).  We do NOT hash another worktree's files:
    a live writer there is exactly the case we must not disturb, and hashing it
    would manufacture a false "writer" reading.  Dirtiness is reported as a
    violation with the un-probed caveat spelled out.
    """
    findings: list[Finding] = []
    hands_off: list[Finding] = []
    if not include_cross:
        return findings, hands_off

    raw = git_ok(repo, "worktree", "list", "--porcelain")
    if not raw.strip():
        return findings, hands_off

    entries: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in raw.splitlines():
        if not line.strip():
            if current:
                entries.append(current)
                current = {}
            continue
        key, _, value = line.partition(" ")
        current[key] = value
    if current:
        entries.append(current)

    for entry in entries:
        wt = Path(entry.get("worktree", "")).resolve()
        if not wt.exists() or wt == primary.resolve():
            continue
        ref = entry.get("branch") or "DETACHED"
        porcelain = git_ok(wt, "status", "--porcelain=v1", "-z")
        dirty = parse_porcelain(porcelain)
        if not dirty:
            continue
        # Determine liveness FIRST. A dirty worktree with a live writer is
        # HANDS-OFF, not a violation (see the module docstring): the rule says
        # that work belongs to its owning agent, and reporting it as a violation
        # too is the cry-wolf failure -- the same worktree counted twice, once
        # as a breach and once as hands-off, driving a non-zero exit for work
        # the guard itself says it must not touch. So emit EITHER the
        # hands-off finding OR the violations, never both.
        live = processes_with_cwd_under(wt)
        if live:
            pids = ", ".join(str(p) for p, _ in live[:4])
            hands_off.append(Finding(
                HANDS_OFF,
                "foreign_worktree_live_agent",
                f"{wt}",
                f"a process is working in this worktree right now (pid {pids}); "
                f"{len(dirty)} uncommitted path(s) on {ref} belong to it",
                "hands off: do not commit or rebase this worktree while its "
                "writer is live",
            ))
            continue
        findings.append(Finding(
            VIOLATION,
            "foreign_worktree_dirty",
            f"{wt}",
            f"{len(dirty)} uncommitted path(s) on {ref}; NOT hashed (we do not "
            f"touch another agent's worktree)",
            "the owning agent commits it, or a human freezes it by re-hashing "
            "over >=40s and commits it attributed",
        ))
        for status, path in dirty[:5]:
            findings.append(Finding(
                VIOLATION,
                "foreign_worktree_dirty_path",
                f"{wt}/{path}:1",
                f"uncommitted {status!r} on {ref}",
                "commit as wip: if the owner is gone",
            ))
    return findings, hands_off


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

@dataclass
class HygieneReport:
    repo: Path
    branch: str
    head: str
    trunk: str
    trunk_source: str
    findings: list[Finding] = field(default_factory=list)
    dirty: list[DirtyPath] = field(default_factory=list)
    probe_seconds: float = 40.0
    probe_seconds_waited: float = 0.0
    writer_corroboration: list[tuple[int, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def violations(self) -> list[Finding]:
        return [f for f in self.findings if f.kind == VIOLATION]

    @property
    def hands_off(self) -> list[Finding]:
        return [f for f in self.findings if f.kind == HANDS_OFF]

    def exit_code(self) -> int:
        return 1 if self.violations else 0


def resolve_trunk(repo: Path, override: Optional[str]) -> tuple[str, str]:
    """Pick the main line to compare against, with the reason for the choice.

    Trunk selection is a real false-positive source.  In this repo the local
    ``main`` is *stale* (months behind) while the true remote trunk is e.g.
    ``canonical/main``; comparing against the stale local branch reports a huge
    bogus "stranded" count.  So we prefer, in order:

      1. an explicit ``--trunk``;
      2. a remote-tracking trunk, picking whichever remote branch named
         ``main``/``master`` is *freshest* (most recent committer date);
      3. a local ``main``/``master``.

    And unresolved ambiguity is surfaced through the returned reason string so
    the caller can tell the reader which comparison was made.
    """
    if override:
        return override, "explicit --trunk"

    remotes = [
        r.strip() for r in git_ok(repo, "remote").splitlines() if r.strip()
    ]
    remote_candidates: list[tuple[str, int]] = []
    for remote in remotes:
        for name in ("main", "master"):
            ref = f"{remote}/{name}"
            if not git_ok(repo, "rev-parse", "--verify", ref).strip():
                continue
            ts = git_ok(repo, "log", "-1", "--format=%ct", ref).strip()
            remote_candidates.append((ref, int(ts) if ts.isdigit() else 0))
    if remote_candidates:
        ref, _ = max(remote_candidates, key=lambda item: item[1])
        note = "remote-tracking"
        if len(remote_candidates) > 1:
            others = ", ".join(r for r, _ in remote_candidates if r != ref)
            note = f"remote-tracking (freshest of: {others})"
        return ref, note

    for candidate in ("main", "master"):
        if git_ok(repo, "rev-parse", "--verify", candidate).strip():
            return candidate, "local branch (no remote-tracking trunk found)"
    return "HEAD", "fallback (no trunk found; stranded-commit check disabled)"


def collect_dirty(worktree: Path, allow_read: bool, scan_secret_content: bool = True) -> list[DirtyPath]:
    raw = git_ok(worktree, "status", "--porcelain=v1", "-z")
    out: list[DirtyPath] = []
    for status, path in parse_porcelain(raw):
        abspath = (worktree / path) if allow_read else None
        mtime = size = None
        if abspath is not None and abspath.exists() and not abspath.is_dir():
            mtime, size = stat_path(abspath)
        staged = status[0] not in (" ", "?")
        contains_secret = None
        if staged and scan_secret_content and not looks_like_secret(path):
            contains_secret = staged_file_contains_secret(worktree, path)
        out.append(DirtyPath(
            relpath=path,
            status=status,
            abspath=abspath,
            mtime=mtime,
            size=size,
            staged=staged,
            contains_secret=contains_secret,
        ))
    return out


def run_checks(
    repo: Path,
    stale_minutes: float = 30.0,
    probe_seconds: float = 40.0,
    probe_rounds: int = 1,
    include_cross: bool = True,
    trunk_override: Optional[str] = None,
    probe: Optional[WriterProbe] = None,
) -> HygieneReport:
    repo = repo.resolve()
    assert_is_git_repo(repo)
    branch_raw = git_ok(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
    head = git_ok(repo, "rev-parse", "HEAD").strip()
    trunk, trunk_source = resolve_trunk(repo, trunk_override)

    dirty = collect_dirty(repo, allow_read=True)

    if probe is None:
        probe = WriterProbe(probe_seconds=probe_seconds, rounds=probe_rounds)
    if dirty:
        probe.run(dirty)

    report = HygieneReport(
        repo=repo,
        branch=branch_raw,
        head=head,
        trunk=trunk,
        trunk_source=trunk_source,
        dirty=dirty,
        probe_seconds=probe.probe_seconds,
        probe_seconds_waited=probe.seconds_waited,
        writer_corroboration=live_gateway_writers(),
    )

    report.findings.extend(
        check_staged_secrets(dirty, repo)
    )
    report.findings.extend(
        check_dirty_worktree(repo, dirty, probe, stale_minutes, is_primary=True)
    )
    if trunk_source != "fallback (no trunk found; stranded-commit check disabled)":
        report.findings.extend(check_stranded_branch(repo, branch_raw, trunk))
    cross, cross_hands_off = check_other_worktrees(repo, repo, probe, stale_minutes, trunk, include_cross)
    report.findings.extend(cross)
    report.findings.extend(cross_hands_off)

    if not report.writer_corroboration and dirty:
        report.warnings.append(
            "no live gateway process matched the canonical "
            "qubit_runtime.gateway.status.looks_like_gateway_command_line matcher; "
            "liveness rests on the byte-stability probe alone"
        )
    return report


def render_text(report: HygieneReport) -> str:
    lines: list[str] = []
    lines.append(f"commit-hygiene: {report.repo}")
    lines.append(
        f"  branch={report.branch} head={report.head[:12]} "
        f"trunk={report.trunk} ({report.trunk_source})"
    )
    lines.append(
        f"  probe={report.probe_seconds:.0f}s waited={report.probe_seconds_waited:.0f}s "
        f"dirty={len(report.dirty)}"
    )
    if report.findings:
        for f in report.findings:
            lines.append(f.format())
    else:
        lines.append("clean: no COMMIT-AS-YOU-GO violations found")
    lines.append(
        f"  summary: {len(report.violations)} violation(s), "
        f"{len(report.hands_off)} hands-off, "
        f"{len([f for f in report.findings if f.kind == WARNING])} warning(s)"
    )
    for w in report.warnings:
        lines.append(f"  note: {w}")
    return "\n".join(lines)


def render_markdown(report: HygieneReport) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out = [
        "# COMMIT-AS-YOU-GO hygiene report",
        "",
        f"- generated: {now}",
        f"- repo: `{report.repo}`",
        f"- branch: `{report.branch}` @ `{report.head[:12]}`",
        f"- trunk: `{report.trunk}` ({report.trunk_source})",
        f"- writer probe: {report.probe_seconds:.0f}s window, "
        f"{report.probe_seconds_waited:.0f}s waited",
        f"- verdict: **{len(report.violations)} violation(s)**, "
        f"{len(report.hands_off)} hands-off",
        "",
        "## Violations",
        "",
    ]
    if report.violations:
        for f in report.violations:
            out.append(f"- `{f.check}` **{f.location}** — {f.detail} → {f.remedy}")
    else:
        out.append("- none")
    out += ["", "## Hands off (live writer — NOT a violation)", ""]
    if report.hands_off:
        for f in report.hands_off:
            out.append(f"- `{f.check}` **{f.location}** — {f.detail} → {f.remedy}")
    else:
        out.append("- none")
    out += ["", "## Warnings", ""]
    warnings = [f for f in report.findings if f.kind == WARNING] + [
        Finding(WARNING, "note", "environment", w) for w in report.warnings
    ]
    if warnings:
        for f in warnings:
            out.append(f"- {f.detail}")
    else:
        out.append("- none")
    out.append("")
    return "\n".join(out)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Report COMMIT-AS-YOU-GO violations.")
    ap.add_argument("--repo", default=".", help="repository path (default: cwd)")
    ap.add_argument("--trunk", default=None, help="branch to treat as the main line")
    ap.add_argument("--stale-minutes", type=float, default=30.0,
                    help="a dirty file older than this with no live writer is a violation")
    ap.add_argument("--probe-seconds", type=float, default=40.0,
                    help="writer-probe window; must be >= 40")
    ap.add_argument("--probe-rounds", type=int, default=1,
                    help="extra probe rounds for paths that changed (writer pauses between saves)")
    ap.add_argument("--no-cross-worktree", action="store_true",
                    help="check only the given worktree, not siblings")
    ap.add_argument("--json", action="store_true", help="emit JSON")
    ap.add_argument("--report", default=None, help="also write a markdown report here")
    args = ap.parse_args(argv)

    try:
        report = run_checks(
            repo=Path(args.repo),
            stale_minutes=args.stale_minutes,
            probe_seconds=args.probe_seconds,
            probe_rounds=args.probe_rounds,
            include_cross=not args.no_cross_worktree,
            trunk_override=args.trunk,
        )
    except GitError as exc:
        print(f"commit-hygiene: cannot run: {exc}", file=sys.stderr)
        return 2

    if args.report:
        Path(args.report).write_text(render_markdown(report), encoding="utf-8")
    if args.json:
        import json
        print(json.dumps({
            "repo": str(report.repo),
            "branch": report.branch,
            "head": report.head,
            "trunk": report.trunk,
            "trunk_source": report.trunk_source,
            "probe_seconds": report.probe_seconds,
            "probe_seconds_waited": report.probe_seconds_waited,
            "dirty_count": len(report.dirty),
            "violations": len(report.violations),
            "hands_off": len(report.hands_off),
            "findings": [f.to_dict() for f in report.findings],
            "warnings": report.warnings,
        }, indent=2))
    else:
        print(render_text(report))
    return report.exit_code()


if __name__ == "__main__":
    raise SystemExit(main())

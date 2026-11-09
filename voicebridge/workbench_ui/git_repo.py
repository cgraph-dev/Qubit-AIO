"""One git working set per confined workspace: the shared lock and identity.

WHY A PROCESS-WIDE LOCK PER REPOSITORY
---------------------------------------------------------------------------
``git index.lock`` already makes two concurrent ``git add`` fail — with a
confusing "Unable to create '<repo>/.git/index.lock': File exists" that reads to
the user like a stale lock they have to delete. Two dashboard tabs, or a
double-clicked stage button, produce exactly that. One ``RLock`` per resolved
work-tree top level serialises them inside this process (git's own lock still
protects against OTHER processes), and the second caller simply waits its turn
instead of failing.

The lock is per PROCESS, keyed by the real path, so two repos run in parallel and
one repo never runs two mutations at once. It is never held across a network call
longer than the network timeout, and it is an ``RLock`` so a handler that takes
it and then calls a helper which takes it again does not deadlock.

BLOCKING CALLS
---------------------------------------------------------------------------
Every helper in here runs a subprocess. On the listener's thread pool that is
fine (each request owns its thread); from an event loop it is not. Callers that
are async must push these through ``run_in_executor`` — see
``git_family.run_off_loop`` — which is the same pattern the vendored dashboard's
git routes use (``qubit_cli/web_routers/git.py::_git_op``).
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

from . import cmdrun

_LOCKS: Dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()

#: Identity used ONLY when the repository has no identity of its own. A commit
#: made by the dashboard must be attributable; ``git commit`` with no identity
#: fails across the board, so a fixed, obviously-machine identity is the honest
#: choice rather than borrowing the operator's.
FALLBACK_NAME = "Qubit Agent"
FALLBACK_EMAIL = "agent@qubit.local"

TRUNK_NAMES = ("main", "master")


def repo_lock(repo: Path) -> threading.RLock:
    """The RLock guarding mutations of ``repo`` inside this process."""
    key = str(repo)
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _LOCKS[key] = lock
        return lock


@contextmanager
def locked(repo: Path) -> Iterator[None]:
    with repo_lock(repo):
        yield


# --------------------------------------------------------------- repo helpers
def is_inside_work_tree(repo: Path) -> bool:
    """Whether ``repo`` is a git work tree (``git rev-parse --is-inside-work-tree``)."""
    return cmdrun.line(repo, ["rev-parse", "--is-inside-work-tree"]) == "true"


def has_head(repo: Path) -> bool:
    """Whether HEAD resolves — an empty repository (no commits yet) does not."""
    code, _out, _err = cmdrun.run(repo, ["rev-parse", "--verify", "-q", "HEAD"],
                                 check=False)
    return code == 0


def current_branch(repo: Path) -> str:
    """The checked-out branch name, or ``""`` on a detached HEAD."""
    return cmdrun.line(repo, ["rev-parse", "--abbrev-ref", "HEAD"])


def origin_head(repo: Path) -> str:
    """``main`` when ``refs/remotes/origin/HEAD`` resolves, else ``""``."""
    text = cmdrun.line(repo, ["symbolic-ref", "--quiet", "--short",
                              "refs/remotes/origin/HEAD"])
    return text.split("/", 1)[-1] if text else ""


def ref_exists(repo: Path, ref: str) -> bool:
    code, _out, _err = cmdrun.run(repo, ["rev-parse", "--verify", "-q", ref],
                                 check=False)
    return code == 0


def default_branch(repo: Path) -> str:
    """The trunk name: origin's default when it has one, else main/master."""
    head = origin_head(repo)
    if head and head != "HEAD":
        return head
    for ref in ("refs/heads/main", "refs/heads/master",
                "refs/remotes/origin/main", "refs/remotes/origin/master"):
        if ref_exists(repo, ref):
            return ref.split("/")[-1]
    return ""


def has_remote(repo: Path) -> bool:
    return bool(cmdrun.line(repo, ["remote"]))


def upstream_of(repo: Path) -> str:
    """``<remote>/<branch>`` of the current branch's upstream, or ``""``."""
    return cmdrun.line(repo, ["rev-parse", "--abbrev-ref", "--symbolic-full-name",
                              "@{u}"])


def remotes(repo: Path) -> list:
    return [ln.strip() for ln in cmdrun.ok_or_empty(repo, ["remote"]).splitlines()
            if ln.strip()]


def head_sha(repo: Path) -> str:
    return cmdrun.line(repo, ["rev-parse", "HEAD"])


def commit_identity_env(repo: Path) -> Optional[Dict[str, str]]:
    """Env carrying the repository's own commit identity, or ``None``.

    Qubit's git environment deliberately blanks global/system config, which
    also blanks ``user.name``/``user.email``. Rather than re-enabling the user's
    whole config (it could carry hooks, pagers, credential helpers), the two
    identity values are read with ``git var`` — which DOES see the original
    config — and passed to the commit as ``GIT_AUTHOR_*``/``GIT_COMMITTER_*``.
    ``None`` means the repo has no identity and the caller uses the fixed
    fallback, never the ambient environment.
    """
    env: Dict[str, str] = {}
    import os
    import subprocess

    # ``git var`` needs a config it can READ to answer, so it runs with the
    # caller's GIT_CONFIG_* dropped but the global/system files still in place.
    # Only two scalar strings come back; nothing else from that config is used.
    base = {k: v for k, v in os.environ.items() if not k.startswith("GIT_CONFIG_")}
    for role in ("AUTHOR", "COMMITTER"):
        try:
            proc = subprocess.run(
                ["git", "--no-pager", "-C", str(repo), "var",
                 f"GIT_{role}_IDENT"],
                capture_output=True, text=True, timeout=10,
                stdin=subprocess.DEVNULL, env=base)
        except (OSError, subprocess.SubprocessError):
            return None
        text = proc.stdout.strip() if proc.returncode == 0 else ""
        if not text:
            return None
        # "Name <email> <ts> <tz>" — the last two tokens are the date.
        head = text.rsplit(" ", 2)[0]
        if "<" not in head or not head.endswith(">"):
            return None
        name, _, rest = head.partition(" <")
        env[f"GIT_{role}_NAME"] = name.strip()
        env[f"GIT_{role}_EMAIL"] = rest[:-1].strip()
    return env or None


def status_raw(repo: Path) -> str:
    """``git status --porcelain=v2 -z`` verbatim (the one status authority)."""
    return cmdrun.ok_or_empty(repo, ["status", "--porcelain=v2", "-z",
                                     "--untracked-files=all"])


def status_headers(text: str) -> Dict[str, Any]:
    """The ``# branch.*`` headers of porcelain v2, parsed.

    Under ``-z`` the headers are NUL terminated too (measured:
    ``# branch.head main\\0# branch.ab +1 -0\\0``), while the line format ends
    them with ``\\n``; both are normalised before splitting. Parsing stops at
    the first non-header record so an entry path can never be read as a header.
    """
    out: Dict[str, Any] = {"branch": None, "detached": False, "ahead": 0,
                           "behind": 0, "head": "", "upstream": ""}
    for line in str(text or "").replace("\x00", "\n").split("\n"):
        if not line.startswith("# "):
            if line.strip():
                break
            continue
        if line.startswith("# branch.oid "):
            value = line[len("# branch.oid "):].strip()
            out["head"] = "" if value == "(initial)" else value
        elif line.startswith("# branch.head "):
            head = line[len("# branch.head "):].strip()
            out["detached"] = head == "(detached)"
            if out["detached"] or head == "(initial)":
                out["branch"] = None
            else:
                out["branch"] = head
        elif line.startswith("# branch.ab "):
            for tok in line[len("# branch.ab "):].split():
                try:
                    if tok.startswith("+"):
                        out["ahead"] = int(tok[1:] or 0)
                    elif tok.startswith("-"):
                        out["behind"] = int(tok[1:] or 0)
                except ValueError:
                    continue
        elif line.startswith("# branch.upstream "):
            out["upstream"] = line[len("# branch.upstream "):].strip()
    return out


def strip_headers(raw: str) -> str:
    """Drop the ``# ...`` header records that precede the entries.

    Measured (git 2.4x): under ``-z`` the headers are NUL terminated too
    (``# branch.head main\\0? a.txt\\0``), while under the line format they end
    in ``\\n``. Both are handled, so the FIRST entry is never glued onto the
    last header — which made every real file vanish from the pane.
    """
    text = str(raw or "")
    pos = 0
    while pos < len(text) and text.startswith("# ", pos):
        nul = text.find("\x00", pos)
        nl = text.find("\n", pos)
        if nul < 0 and nl < 0:
            return ""
        if nul < 0 or (nl >= 0 and nl < nul):
            pos = nl + 1
        else:
            pos = nul + 1
    return text[pos:]


def walk_entries(raw: str):
    """Yield ``(tag, xy, path)`` from ``--porcelain=v2 -z`` entry records.

    Rename/copy records (``2``) are followed by the ORIGINAL path as its own
    NUL-terminated field, which is consumed here so it is never mistaken for a
    separate entry. The path yielded for them is the NEW one. Header lines are
    dropped by :func:`strip_headers` first.
    """
    records = iter(strip_headers(raw).split("\0"))
    for rec in records:
        if not rec:
            continue
        tag = rec[0]
        if tag == "?":
            yield "?", "??", rec[2:]
        elif tag == "!":
            continue
        elif tag == "u":
            parts = rec.split(" ")
            yield "u", parts[1] if len(parts) > 1 else "..", parts[-1]
        elif tag in ("1", "2"):
            parts = rec.split(" ")
            xy = parts[1] if len(parts) > 1 else ".."
            if tag == "1":
                path = rec.split(" ", 8)[-1]
            else:
                path = rec.split(" ", 9)[-1]
                next(records, None)  # the rename source
            yield tag, xy, path

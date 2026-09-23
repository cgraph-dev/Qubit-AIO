"""Real, operator-owned Qubit update check + non-destructive in-place update.

The dashboard "Update Qubit" card previously received a mostly-static status and
a blanket ``UPDATE_OPERATOR_OWNED`` typed refusal on apply. This module makes
both paths real while keeping the operation safe:

* **check** detects the install method (a git checkout when ``.git`` exists,
  otherwise a package install), reads the current version and the bounded local
  revision, and — only when a git checkout is present — probes the configured
  remote with a bounded ``git ls-remote``. A failed probe is a typed DEGRADED
  state, never a fabricated "up to date".
* **apply** refuses without an explicit confirm, refuses a dirty worktree
  (Qubit never discards an operator's uncommitted work), refuses a non-git
  install with the exact operator command, requires the single e-stop authority
  to be clear, and then runs exactly one **non-destructive** command — by default
  ``git pull --ff-only``. It never runs reset/checkout/stash/clean/force. The
  receipt carries the pre- and post-update revisions and a named operator
  rollback command that is described, not executed.
* **snapshot** writes a timestamped backup directory (a ``manifest.json`` plus a
  ``code.bundle`` produced by ``git bundle create``) so an update is reversible
  before it starts.
* **rollback** (Owner Decision #11) restores the recorded pre-apply revision from
  the most recent update snapshot. It writes a ``pre-rollback`` snapshot *first*
  (so the rollback is itself reversible), refuses a dirty worktree, and only
  then runs ``git reset --hard <recorded-revision>`` — a destructive verb used
  **only** by rollback, never by apply.

Every command is injected through ``runner``/``git`` callables so this logic is
deterministically unit-testable without touching git, the network, or the host
checkout. A fail-closed test guard mirrors
:func:`voicebridge.gateway_supervisor._real_control_disabled`: when a test
process (or an operator who sets ``QUBIT_UPDATE_DISABLED=1``) runs this code
with no injected executor, nothing is executed and a typed UNAVAILABLE is
returned.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Sequence, Tuple

TIMEOUT_S = 30.0

CONFIRM_CODE = "CONFIRM_REQUIRED"
WORKTREE_DIRTY_CODE = "WORKTREE_DIRTY"
ESTOP_CODE = "UPDATE_ESTOP"
UNAVAILABLE_CODE = "UPDATE_UNAVAILABLE"
OPERATOR_OWNED_CODE = "UPDATE_OPERATOR_OWNED"
FAILED_CODE = "UPDATE_FAILED"
DEGRADED_CODE = "UPDATE_CHECK_DEGRADED"
NO_BACKUP_CODE = "UPDATE_NO_BACKUP"
BACKUP_INVALID_CODE = "UPDATE_BACKUP_INVALID"

UPDATE_COMMAND = "git pull --ff-only"
OPERATOR_COMMAND = "scripts/install.sh"
ROLLBACK_TEMPLATE = "git -C {repo} reset --hard {revision}"
SNAPSHOT_MANIFEST = "manifest.json"
SNAPSHOT_BUNDLE = "code.bundle"

Runner = Callable[[Sequence[str], float], Tuple[int, str, str]]


def _get(cfg: Any, *path: str, default: Any = None) -> Any:
    try:
        return cfg.get(*path, default=default) if hasattr(cfg, "get") else default
    except Exception:  # noqa: BLE001
        return default


def repo_root(cfg: Any) -> Path:
    """The checkout to inspect. ``update.repo_root`` overrides the module root."""
    override = str(_get(cfg, "update", "repo_root", default="") or "").strip()
    if override:
        return Path(override).expanduser()
    return Path(__file__).resolve().parents[1]


def _current_version() -> str:
    try:
        from . import distribution as D

        return str(getattr(D, "PACKAGE_VERSION", "") or "1.0.0")
    except Exception:  # noqa: BLE001
        return "1.0.0"


def _default_runner(argv: Sequence[str], timeout: float) -> Tuple[int, str, str]:
    try:
        proc = subprocess.run(list(argv), capture_output=True, text=True,
                              timeout=timeout)
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError:
        return 127, "", f"{argv[0]}: not found"
    except subprocess.TimeoutExpired:
        return 124, "", "command timed out"
    except Exception as exc:  # noqa: BLE001
        return 1, "", str(exc)


def _redact(text: Any) -> str:
    try:
        from .redaction import redact_text

        return redact_text(str(text))
    except Exception:  # noqa: BLE001
        return str(text)


def _audit(op: str, **detail: Any) -> None:
    try:
        from .logging_setup import log_event

        log_event(f"qubit.update.{op}", **detail)
    except Exception:  # noqa: BLE001
        pass


def _estop(cfg: Any) -> bool:
    """Fail closed on the single e-stop authority (plugins, then broker)."""
    try:
        from .plugins import estop_engaged

        return bool(estop_engaged(cfg))
    except Exception:  # noqa: BLE001
        try:
            from .broker import estop_engaged

            return bool(estop_engaged(cfg))
        except Exception:  # noqa: BLE001
            return True


def _real_control_disabled() -> bool:
    """True when a real update command must NOT be run.

    A test process (or an operator who sets the guard) must never mutate the
    host checkout as a side effect of exercising this code. Injected
    ``runner``/``git`` bypass this check, so the behavior remains testable.
    """
    if os.environ.get("QUBIT_UPDATE_DISABLED") == "1":
        return True
    try:
        from . import qubit_runtime

        return bool(qubit_runtime.under_test())
    except Exception:  # noqa: BLE001
        return bool(os.environ.get("PYTEST_CURRENT_TEST"))


def _git_argv(repo: Path, args: Sequence[str]) -> list:
    return ["git", "-C", str(repo), *args]


def _sha(text: Any) -> Optional[str]:
    """A bounded hex revision from a ``git rev-parse`` line, or None."""
    tok = (str(text or "").strip().split() or [""])[0]
    if 7 <= len(tok) <= 64 and all(c in "0123456789abcdef" for c in tok.lower()):
        return tok
    return None


def _is_clean(exec_: Runner, repo: Path) -> Tuple[Optional[bool], str]:
    """True/False porcelain status, or (None, stderr) when it cannot be read."""
    rc, out, err = exec_(_git_argv(repo, ["status", "--porcelain"]), TIMEOUT_S)
    if rc != 0:
        return None, err
    return (out.strip() == ""), ""


def _remote_head(exec_: Runner, repo: Path, remote: str,
                 branch: str) -> Tuple[Optional[str], str, str]:
    """Resolve ``remote/branch`` to a bounded revision.

    Returns ``(sha, kind, detail)`` where ``kind`` is one of:

    * ``"ok"``             — the branch head resolved to ``sha``;
    * ``"branch_missing"`` — the remote answered, but has no such branch
      (an empty ``ls-remote`` is a real, distinguishable answer, not a
      network failure — reporting it as "couldn't reach" would be false);
    * ``"unreachable"``    — ``git ls-remote`` failed (network/remote missing);
    * ``"unresolved"``     — the remote answered but the head was not a
      bounded revision.
    """
    rc, out, err = exec_(_git_argv(
        repo, ["ls-remote", "--heads", remote, branch]), TIMEOUT_S)
    if rc != 0:
        return None, "unreachable", err
    lines = [ln for ln in (out or "").strip().splitlines() if ln.strip()]
    if not lines:
        return None, "branch_missing", f"{remote}/{branch} has no head"
    sha = _sha(lines[0])
    if sha is None:
        return None, "unresolved", f"unparseable head for {remote}/{branch}"
    return sha, "ok", ""


def check(cfg: Any, *, runner: Optional[Runner] = None,
          git: Optional[Runner] = None,
          source: Optional[str] = None) -> Dict[str, Any]:
    """Read-only update status. Never raises; never fabricates "up to date".

    ``source`` overrides ``update.remote`` for this call (a git remote name or a
    local repository/bundle path). It never changes the checkout.
    """
    repo = repo_root(cfg)
    is_checkout = (repo / ".git").exists()
    install_method = "git-checkout" if is_checkout else "package"
    # ``exec_`` is ``git or runner or _default_runner``; availability must
    # consider every injected executor, not just the ``git`` alias, or a
    # host with no ``git`` binary but an injected ``runner`` would be reported
    # DEGRADED even though the check can run (mirrors apply()).
    git_available = (shutil.which("git") is not None or git is not None
                     or runner is not None)
    out: Dict[str, Any] = {
        "state": "OK",
        "install_method": install_method,
        "current_version": _current_version(),
        "current_revision": None,
        "remote_revision": None,
        "behind": None,
        "update_available": False,
        "can_apply": False,
        "clean": None,
        "remote": None,
        "branch": None,
        "remote_reachable": None,
        "remote_branch_found": None,
        "update_command": OPERATOR_COMMAND,
        "message": None,
        "detail": "",
    }
    if not is_checkout:
        out["message"] = ("Qubit was installed from a package, not a git "
                          "checkout; reinstall the package to update. The "
                          "dashboard never self-updates in place.")
        return out

    out["update_command"] = UPDATE_COMMAND
    if not git_available:
        out["state"] = "DEGRADED"
        out["message"] = ("git is not available on this host; cannot check or "
                          "apply updates")
        return out
    if runner is None and git is None and _real_control_disabled():
        out["state"] = "UNAVAILABLE"
        out["message"] = ("update checks are disabled in this environment "
                          "(test guard); no git or network command was run")
        return out

    exec_ = git or runner or _default_runner
    rc, stdout, stderr = exec_(_git_argv(repo, ["rev-parse", "HEAD"]), TIMEOUT_S)
    if rc != 0:
        out["state"] = "DEGRADED"
        out["message"] = "could not read the local revision"
        out["detail"] = _redact(stderr)[:300]
        return out
    out["current_revision"] = _sha(stdout)

    clean, cerr = _is_clean(exec_, repo)
    if clean is None:
        out["state"] = "DEGRADED"
        out["message"] = "could not read the worktree status"
        out["detail"] = _redact(cerr)[:300]
        return out
    out["clean"] = clean

    remote = str(source or _get(cfg, "update", "remote", default="origin")
                 or "origin")
    branch = str(_get(cfg, "update", "branch", default="main") or "main")
    out["remote"] = remote
    out["branch"] = branch
    remote_sha, rkind, rerr = _remote_head(exec_, repo, remote, branch)
    if remote_sha is None:
        out["state"] = "DEGRADED"
        out["remote_reachable"] = rkind != "unreachable"
        out["remote_branch_found"] = rkind == "unresolved"
        if rkind == "branch_missing":
            out["message"] = (f"the update source has no branch "
                              f"{remote}/{branch}; check update.remote and "
                              f"update.branch — not reporting up to date")
        else:
            out["message"] = (f"couldn't reach the update source "
                              f"({remote}/{branch}); not reporting up to date")
        out["detail"] = _redact(rerr)[:300]
    else:
        out["remote_reachable"] = True
        out["remote_branch_found"] = True
        out["remote_revision"] = remote_sha
        if out["current_revision"] and remote_sha == out["current_revision"]:
            out["behind"] = 0
            out["message"] = "You're on the latest version."
        else:
            # Without a fetched ref we cannot count commits; -1 means "behind by
            # an unknown count" (never a fabricated exact number).
            out["behind"] = -1
            out["update_available"] = True
            out["message"] = (f"an update is available on {remote}/{branch} "
                              "(behind by an unknown count)")

    # ``can_apply`` means an ``apply`` turn is expected to be able to run: a
    # clean checkout with git AND a resolvable configured update source. An
    # unreachable/missing branch is not applicable, so it is not advertised.
    out["can_apply"] = bool(is_checkout and clean and git_available
                            and remote_sha is not None)
    return out


def apply(cfg: Any, *, confirm: bool = False,
          runner: Optional[Runner] = None, git: Optional[Runner] = None,
          actor: str = "dashboard:user",
          source: Optional[str] = None) -> Dict[str, Any]:
    """Perform one confirmed non-destructive ``git pull --ff-only``.

    Order: confirm -> e-stop -> test guard -> checkout -> clean worktree ->
    pull. Refuses (never executes) on any earlier failure. ``source`` overrides
    ``update.remote`` for this call (a git remote name or a local repository/
    bundle path).
    """
    if not confirm:
        return {"ok": False, "state": "CONFIRM_REQUIRED", "code": CONFIRM_CODE,
                "detail": ("updating Qubit modifies the checkout; re-issue with "
                           "confirm=true (dashboard Update-now dialog / CLI "
                           "--confirm)")}
    if _estop(cfg):
        return {"ok": False, "state": "UNAVAILABLE", "code": ESTOP_CODE,
                "detail": "emergency stop is latched; update refused"}
    if runner is None and git is None and _real_control_disabled():
        return {"ok": False, "state": "UNAVAILABLE", "code": UNAVAILABLE_CODE,
                "detail": ("real update execution is disabled in this "
                           "environment (test guard); nothing was pulled")}

    repo = repo_root(cfg)
    if not (repo / ".git").exists():
        _audit("refused", actor=actor, reason="not_a_checkout")
        return {"ok": False, "state": "UNAVAILABLE", "code": OPERATOR_OWNED_CODE,
                "detail": ("Qubit is not a git checkout; update it with the "
                           f"operator command: {OPERATOR_COMMAND}"),
                "update_command": OPERATOR_COMMAND,
                "qubit_counterpart": OPERATOR_COMMAND,
                "rollback": "nothing changed; nothing to roll back"}

    exec_ = git or runner or _default_runner
    clean, cerr = _is_clean(exec_, repo)
    if clean is None:
        _audit("refused", actor=actor, reason="status_unreadable")
        return {"ok": False, "state": "FAILED", "code": FAILED_CODE,
                "detail": _redact(cerr or "could not read the worktree status")[:300],
                "rollback_plan": "nothing changed; nothing to roll back"}
    if not clean:
        _audit("refused", actor=actor, reason="worktree_dirty")
        return {"ok": False, "state": "CONFLICT", "code": WORKTREE_DIRTY_CODE,
                "detail": ("the worktree has uncommitted changes; commit or "
                           "stash them before updating (Qubit will not discard "
                           "your work)"),
                "rollback_plan": "nothing changed; nothing to roll back"}

    rc, stdout, stderr = exec_(_git_argv(repo, ["rev-parse", "HEAD"]), TIMEOUT_S)
    if rc != 0:
        _audit("refused", actor=actor, reason="revision_unreadable")
        return {"ok": False, "state": "FAILED", "code": FAILED_CODE,
                "detail": _redact(stderr or "could not read the local revision")[:300],
                "rollback_plan": "nothing changed; nothing to roll back"}
    pre_revision = _sha(stdout)

    # Pull the CONFIGURED remote/branch explicitly. A bare ``git pull`` follows
    # the checkout's tracking upstream, which may differ from ``update.remote``/
    # ``update.branch`` (so check() could report an update that apply() never
    # fetched). Naming both is still fast-forward-only and non-destructive.
    remote = str(source or _get(cfg, "update", "remote", default="origin")
                 or "origin")
    branch = str(_get(cfg, "update", "branch", default="main") or "main")
    command = f"git pull --ff-only {remote} {branch}"

    rc, pulled_out, pulled_err = exec_(
        _git_argv(repo, ["pull", "--ff-only", remote, branch]), TIMEOUT_S)
    rc_post, post_out, _post_err = exec_(
        _git_argv(repo, ["rev-parse", "HEAD"]), TIMEOUT_S)
    post_revision = _sha(post_out) if rc_post == 0 else pre_revision
    rollback_plan = ROLLBACK_TEMPLATE.format(repo=repo, revision=pre_revision)
    # `apply` itself never runs a destructive rollback: it reports the exact
    # revision to restore. The sanctioned destructive restore is the separate
    # `qubit update --rollback` path (`rollback()` below), which snapshots
    # first so it is itself reversible.
    rollback = {
        "kind": "operator",
        "command": f"qubit update --rollback  # {rollback_plan}",
        "revision": pre_revision,
        "executed": False,
        "note": ("apply does not run a destructive rollback; use "
                 "`qubit update --rollback` (which snapshots first) to restore "
                 "the pre-update revision"),
    }

    if rc != 0:
        _audit("apply_failed", actor=actor, command=command,
               pre_revision=pre_revision, post_revision=post_revision,
               rc=rc)
        return {"ok": False, "state": "FAILED", "code": FAILED_CODE,
                "command": command, "install_method": "git-checkout",
                "remote": remote, "branch": branch,
                "pre_revision": pre_revision, "post_revision": post_revision,
                "detail": _redact((pulled_err or pulled_out
                                   or "git pull failed").strip())[:300],
                "rollback_plan": rollback_plan, "rollback": rollback}

    # Confirm the checked-out revision now matches the configured remote branch
    # (when the remote head is resolvable). A mismatch is reported, not hidden.
    remote_sha, rkind, rerr = _remote_head(exec_, repo, remote, branch)
    if remote_sha is not None and post_revision and remote_sha != post_revision:
        _audit("apply_mismatch", actor=actor, command=command,
               expected=remote_sha, actual=post_revision)
        return {"ok": False, "state": "FAILED", "code": FAILED_CODE,
                "command": command, "install_method": "git-checkout",
                "remote": remote, "branch": branch,
                "pre_revision": pre_revision, "post_revision": post_revision,
                "remote_verified": False,
                "detail": (f"pulled revision {post_revision} does not match "
                           f"{remote}/{branch} {remote_sha}"),
                "rollback_plan": rollback_plan, "rollback": rollback}

    changed = pre_revision != post_revision
    remote_verified = remote_sha is not None
    _audit("apply", actor=actor, command=command,
           pre_revision=pre_revision, post_revision=post_revision,
           changed=bool(changed), remote_verified=bool(remote_verified))
    receipt = {"ok": True, "state": "OK", "code": "",
               "command": command, "install_method": "git-checkout",
               "remote": remote, "branch": branch,
               "pre_revision": pre_revision, "post_revision": post_revision,
               "changed": bool(changed), "update_available": bool(changed),
               "remote_verified": bool(remote_verified),
               "detail": _redact((pulled_out or "").strip())[:300],
               "rollback_plan": rollback_plan, "rollback": rollback}
    if not remote_verified:
        # The pull returned success but the update source could not be re-read
        # to prove post == remote head. Say so; never imply a verified match.
        receipt["warning"] = (
            f"pulled, but {remote}/{branch} could not be re-read to confirm "
            f"the revision ({rkind}); verify with "
            f"'git -C {repo} rev-parse HEAD'")
        receipt["remote_check_detail"] = _redact(rerr)[:300]
    return receipt


def estop_engaged(cfg: Any) -> bool:
    """Public probe of the single e-stop authority (fail-closed).

    Owner Decision #11 sanctions rollback, which is a destructive verb, so the
    CLI probes e-stop before it *and* the manager probes it inside ``rollback``.
    """
    return _estop(cfg)


def _utc_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _backup_root(cfg: Any) -> Path:
    """The Qubit backup root: ``update.backup_dir`` or the product default."""
    override = str(_get(cfg, "update", "backup_dir", default="") or "").strip()
    if override:
        return Path(override).expanduser()
    try:
        from . import product as P

        return Path(P.default_paths().backup_dir)
    except Exception:  # noqa: BLE001
        try:
            from . import qubit_runtime

            state = Path(qubit_runtime.default_home())
        except Exception:  # noqa: BLE001
            state = Path.home() / ".local" / "state" / "qubit-voicebridge"
        return state / "backups"


def update_backup_root(cfg: Any, override: Any = None) -> Path:
    """The directory that holds timestamped ``qubit update`` snapshots."""
    if override:
        return Path(override).expanduser()
    return _backup_root(cfg) / "update"


def _newest_snapshot(root: Path) -> Optional[Path]:
    """Most recent timestamped snapshot under *root* (or *root* itself)."""
    if (root / SNAPSHOT_MANIFEST).exists():
        return root
    if not root.is_dir():
        return None
    cands = [c for c in root.iterdir()
             if c.is_dir() and (c / SNAPSHOT_MANIFEST).exists()]
    if not cands:
        return None
    return sorted(cands, key=lambda p: p.name, reverse=True)[0]


def _load_snapshot_manifest(bdir: Path) -> Dict[str, Any]:
    try:
        data = json.loads((bdir / SNAPSHOT_MANIFEST).read_text())
    except Exception:  # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


def snapshot(cfg: Any, *, runner: Optional[Runner] = None,
             git: Optional[Runner] = None, reason: str = "pre-apply",
             source: Optional[str] = None, backup_dir: Any = None,
             actor: str = "cli:update") -> Dict[str, Any]:
    """Write a timestamped update backup: ``manifest.json`` + ``code.bundle``.

    ``git bundle create HEAD`` captures the current revision (the pre-apply
    revision when called before ``apply``) as a durable, restorable artifact.
    Read-only with respect to the checkout; a non-git install still gets a
    manifest (with no bundle) so the operator has a record.
    """
    if runner is None and git is None and _real_control_disabled():
        return {"ok": False, "state": "UNAVAILABLE", "code": UNAVAILABLE_CODE,
                "detail": ("real update backups are disabled in this environment "
                           "(test guard); nothing was written")}

    repo = repo_root(cfg)
    is_checkout = (repo / ".git").exists()
    exec_ = git or runner or _default_runner
    dest_root = update_backup_root(cfg, backup_dir)
    ts = _utc_ts()
    dest = dest_root / ts
    n = 0
    while dest.exists():
        n += 1
        dest = dest_root / f"{ts}-{n}"
    try:
        dest.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        return {"ok": False, "state": "FAILED", "code": FAILED_CODE,
                "detail": f"cannot create backup dir {dest}: {exc}",
                "backup_dir": str(dest)}

    install_method = "git-checkout" if is_checkout else "package"
    pre_revision: Optional[str] = None
    bundle_name = ""
    if is_checkout:
        rc, out, err = exec_(_git_argv(repo, ["rev-parse", "HEAD"]), TIMEOUT_S)
        if rc != 0:
            return {"ok": False, "state": "FAILED", "code": FAILED_CODE,
                    "detail": _redact(err or "could not read the local revision")[:300],
                    "backup_dir": str(dest)}
        pre_revision = _sha(out)
        bundle = dest / SNAPSHOT_BUNDLE
        rc, bout, berr = exec_(
            _git_argv(repo, ["bundle", "create", str(bundle), "HEAD"]),
            TIMEOUT_S)
        if rc != 0 or not bundle.exists():
            return {"ok": False, "state": "FAILED", "code": FAILED_CODE,
                    "detail": _redact(berr or bout
                                      or "git bundle create failed")[:300],
                    "backup_dir": str(dest)}
        bundle_name = SNAPSHOT_BUNDLE

    remote = str(source or _get(cfg, "update", "remote", default="origin")
                 or "origin")
    branch = str(_get(cfg, "update", "branch", default="main") or "main")
    manifest = {
        "schema": 1,
        "kind": "qubit-update",
        "reason": str(reason),
        "created_at": _utc_iso(),
        "product_version": _current_version(),
        "install_method": install_method,
        "pre_revision": pre_revision,
        "remote": remote,
        "branch": branch,
        "bundle": bundle_name,
        "actor": str(actor),
    }
    try:
        (dest / SNAPSHOT_MANIFEST).write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    except OSError as exc:
        return {"ok": False, "state": "FAILED", "code": FAILED_CODE,
                "detail": f"cannot write backup manifest: {exc}",
                "backup_dir": str(dest)}

    _audit("snapshot", actor=actor, reason=str(reason),
           backup_dir=str(dest), pre_revision=pre_revision,
           is_checkout=bool(is_checkout))
    return {"ok": True, "state": "OK", "code": "",
            "backup_dir": str(dest),
            "pre_revision": pre_revision,
            "install_method": install_method,
            "bundle": str(dest / bundle_name) if bundle_name else "",
            "detail": (f"timestamped update backup written to {dest}"
                       if is_checkout
                       else "package install: a record was written, no bundle")}


def rollback(cfg: Any, *, backup_dir: Any = None,
             runner: Optional[Runner] = None, git: Optional[Runner] = None,
             actor: str = "cli:update") -> Dict[str, Any]:
    """Restore the recorded pre-apply revision from the latest update backup.

    Order: e-stop -> test guard -> locate backup -> checkout -> clean worktree ->
    pre-rollback snapshot -> ``git reset --hard <recorded revision>``. The
    pre-rollback snapshot makes the rollback itself reversible. Never discards a
    dirty worktree; never runs if no backup exists.
    """
    if _estop(cfg):
        return {"ok": False, "state": "UNAVAILABLE", "code": ESTOP_CODE,
                "detail": "emergency stop is latched; rollback refused"}
    if runner is None and git is None and _real_control_disabled():
        return {"ok": False, "state": "UNAVAILABLE", "code": UNAVAILABLE_CODE,
                "detail": ("real rollback execution is disabled in this "
                           "environment (test guard); nothing was restored")}

    if backup_dir:
        src = _newest_snapshot(Path(backup_dir).expanduser())
        root = Path(backup_dir).expanduser()
    else:
        root = update_backup_root(cfg)
        src = _newest_snapshot(root)
    if src is None:
        _audit("rollback_refused", actor=actor, reason="no_backup",
               backup_dir=str(root))
        return {"ok": False, "state": "MISSING", "code": NO_BACKUP_CODE,
                "detail": (f"no Qubit update backup found at {root}; run "
                           "`qubit update --apply` first (nothing to roll back)"),
                "backup_dir": str(root)}

    manifest = _load_snapshot_manifest(src)
    if not manifest or manifest.get("kind") != "qubit-update":
        _audit("rollback_refused", actor=actor, reason="invalid_backup",
               backup_dir=str(src))
        return {"ok": False, "state": "FAILED", "code": BACKUP_INVALID_CODE,
                "detail": (f"backup at {src} is not a readable Qubit update "
                           "backup"),
                "backup_dir": str(src)}
    revision = _sha(manifest.get("pre_revision"))
    if revision is None:
        _audit("rollback_refused", actor=actor, reason="no_revision",
               backup_dir=str(src))
        return {"ok": False, "state": "FAILED", "code": BACKUP_INVALID_CODE,
                "detail": f"backup at {src} records no pre-apply revision",
                "backup_dir": str(src)}

    repo = repo_root(cfg)
    if not (repo / ".git").exists():
        _audit("rollback_refused", actor=actor, reason="not_a_checkout",
               backup_dir=str(src))
        return {"ok": False, "state": "UNAVAILABLE", "code": OPERATOR_OWNED_CODE,
                "detail": ("Qubit is not a git checkout; restore it with the "
                           f"operator command: {OPERATOR_COMMAND}"),
                "update_command": OPERATOR_COMMAND,
                "qubit_counterpart": OPERATOR_COMMAND,
                "backup_dir": str(src)}

    exec_ = git or runner or _default_runner
    clean, cerr = _is_clean(exec_, repo)
    if clean is None:
        return {"ok": False, "state": "FAILED", "code": FAILED_CODE,
                "detail": _redact(cerr or "could not read the worktree status")[:300],
                "backup_dir": str(src)}
    if not clean:
        _audit("rollback_refused", actor=actor, reason="worktree_dirty")
        return {"ok": False, "state": "CONFLICT", "code": WORKTREE_DIRTY_CODE,
                "detail": ("the worktree has uncommitted changes; commit or "
                           "stash them before rolling back (Qubit will not "
                           "discard your work)"),
                "rollback_plan": "nothing changed; nothing to roll back",
                "backup_dir": str(src)}

    rc, _, _ = exec_(
        _git_argv(repo, ["cat-file", "-e", f"{revision}^{{commit}}"]), TIMEOUT_S)
    if rc != 0:
        _audit("rollback_refused", actor=actor, reason="revision_missing",
               revision=revision)
        return {"ok": False, "state": "FAILED", "code": FAILED_CODE,
                "detail": (f"recorded revision {revision} is not present in "
                           f"{repo}; restore it from the backup bundle at "
                           f"{src / SNAPSHOT_BUNDLE}"),
                "backup_dir": str(src)}

    # Reversible-first: record the current revision before changing anything.
    pre = snapshot(cfg, runner=runner, git=git, reason="pre-rollback",
                   actor=actor)
    if not pre.get("ok"):
        return {"ok": False, "state": "FAILED", "code": FAILED_CODE,
                "detail": ("refusing to roll back: could not write a reversible "
                           f"pre-rollback snapshot ({pre.get('detail')})"),
                "backup_dir": str(src)}

    cur_rc, cur_out, _ = exec_(_git_argv(repo, ["rev-parse", "HEAD"]), TIMEOUT_S)
    from_revision = _sha(cur_out) if cur_rc == 0 else None
    rc, rout, rerr = exec_(
        _git_argv(repo, ["reset", "--hard", revision]), TIMEOUT_S)
    post_rc, post_out, _ = exec_(_git_argv(repo, ["rev-parse", "HEAD"]),
                                 TIMEOUT_S)
    post_revision = _sha(post_out) if post_rc == 0 else None
    if rc != 0 or post_revision != revision:
        _audit("rollback_failed", actor=actor, revision=revision, rc=rc,
               post_revision=post_revision)
        return {"ok": False, "state": "FAILED", "code": FAILED_CODE,
                "detail": _redact((rerr or rout
                                   or "git reset failed").strip())[:300],
                "restored_revision": post_revision,
                "pre_rollback_snapshot": pre.get("backup_dir"),
                "backup_dir": str(src)}

    _audit("rollback", actor=actor, revision=revision,
           from_revision=from_revision, backup_dir=str(src),
           pre_rollback_snapshot=pre.get("backup_dir"))
    return {"ok": True, "state": "OK", "code": "",
            "restored_revision": revision,
            "from_revision": from_revision,
            "backup_dir": str(src),
            "pre_rollback_snapshot": pre.get("backup_dir"),
            "reversible": True,
            "detail": (f"restored the recorded pre-apply revision {revision} "
                       f"from {src}")}


__all__ = ["TIMEOUT_S", "CONFIRM_CODE", "WORKTREE_DIRTY_CODE", "ESTOP_CODE",
           "UNAVAILABLE_CODE", "OPERATOR_OWNED_CODE", "FAILED_CODE",
           "DEGRADED_CODE", "NO_BACKUP_CODE", "BACKUP_INVALID_CODE",
           "UPDATE_COMMAND", "OPERATOR_COMMAND", "ROLLBACK_TEMPLATE",
           "SNAPSHOT_MANIFEST", "SNAPSHOT_BUNDLE", "repo_root", "check",
           "apply", "snapshot", "rollback", "estop_engaged",
           "update_backup_root"]

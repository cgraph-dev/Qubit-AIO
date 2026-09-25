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
import shlex
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Sequence, Tuple

TIMEOUT_S = 30.0
DEPS_TIMEOUT_S = 300.0

CONFIRM_CODE = "CONFIRM_REQUIRED"
WORKTREE_DIRTY_CODE = "WORKTREE_DIRTY"
ESTOP_CODE = "UPDATE_ESTOP"
UNAVAILABLE_CODE = "UPDATE_UNAVAILABLE"
OPERATOR_OWNED_CODE = "UPDATE_OPERATOR_OWNED"
FAILED_CODE = "UPDATE_FAILED"
DEGRADED_CODE = "UPDATE_CHECK_DEGRADED"
NO_BACKUP_CODE = "UPDATE_NO_BACKUP"
BACKUP_INVALID_CODE = "UPDATE_BACKUP_INVALID"

# Full-lifecycle (``full_apply``) typed codes. Every branch names its own
# failure so an operator/dashboard can act on the exact step that stopped.
LOCK_BUSY_CODE = "UPDATE_LOCK_BUSY"
DEPENDENCY_FAILED_CODE = "UPDATE_DEPENDENCY_FAILED"
MIGRATION_FAILED_CODE = "UPDATE_MIGRATION_FAILED"
VALIDATION_FAILED_CODE = "UPDATE_VALIDATION_FAILED"
SERVICE_FAILED_CODE = "UPDATE_SERVICE_FAILED"
SERVICE_FORBIDDEN_CODE = "UPDATE_SERVICE_FORBIDDEN"
HEALTH_FAILED_CODE = "UPDATE_HEALTH_FAILED"
ROLLBACK_FAILED_CODE = "UPDATE_ROLLBACK_FAILED"
ROLLBACK_OK_CODE = "UPDATE_ROLLED_BACK"
HANDOFF_CODE = "UPDATE_HANDOFF"

# Install methods reported by ``detect_install_method``.
INSTALL_METHODS = ("git-checkout", "package", "container", "immutable", "unknown")

# Only these units may be restarted by the updater; the reference dashboard
# (``hermes-dashboard.service``) and any foreign unit are refused by name.
OWNED_SERVICE_UNITS = ("qubit-voicebridge.service", "qubit-gateway.service")

UPDATE_COMMAND = "git pull --ff-only"
OPERATOR_COMMAND = "scripts/install.sh"
ROLLBACK_TEMPLATE = "git -C {repo} reset --hard {revision}"
SNAPSHOT_MANIFEST = "manifest.json"
SNAPSHOT_BUNDLE = "code.bundle"

# Non-git install methods carry the exact Qubit user command instead of a pull.
_UPDATE_COMMAND_BY_METHOD = {
    "package": OPERATOR_COMMAND,
    "container": "docker pull qubit-agent:latest",
    "immutable": OPERATOR_COMMAND,
    "unknown": OPERATOR_COMMAND,
}

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


def _resolve_source_policy(cfg: Any, source: Optional[str],
                           exec_: Runner, repo: Path
                           ) -> Tuple[Optional[str], str, Optional[Dict[str, Any]]]:
    """Resolve the effective update remote/branch via the canonical resolver.

    Returns ``(remote, branch, policy)``. ``remote`` is exactly what the git
    verbs should use: an explicit ``source`` verbatim, a configured remote
    name, or the canonical Qubit repository URL — a full URL is accepted by
    ``git ls-remote``/``git pull`` (:func:`_git_argv` just prepends
    ``git -C``). ``remote`` is ``None`` only when ``voicebridge.update_channel``
    refused a legacy/invalid *explicit URL*; ``policy`` then carries the typed
    ``code``/``policy`` text, and the caller turns it into a refusal.

    ``voicebridge.update_channel`` is the single authority. It is asked to
    prefer the canonical Qubit URL when no explicit Qubit source is configured
    or when the clone's origin is legacy-branded (its actual URL is read with
    an injectable, shell-free ``git remote get-url``). An explicitly configured
    ``update.remote`` *name* is honored so a user's own mirror/remote keeps
    working; a legacy *name* is never refused (only explicit URLs are), it is
    overridden by the canonical URL. ``policy`` is the raw resolver dict (or
    ``None`` when the optional resolver is unavailable) so callers can surface
    ``source_kind``/``url``. Never raises.
    """
    branch_cfg = str(_get(cfg, "update", "branch", default="main") or "main")

    def _fallback_remote() -> str:
        return str(source or _get(cfg, "update", "remote", default="origin")
                   or "origin")

    try:
        from . import update_channel as UC
    except Exception:  # noqa: BLE001 — optional module; fall back to config
        return _fallback_remote(), branch_cfg, None

    looked_up: Dict[str, Optional[str]] = {}

    def _lookup(name: str) -> Optional[str]:
        try:
            rc, out, _err = exec_(
                _git_argv(repo, ["remote", "get-url", str(name)]), TIMEOUT_S)
        except Exception:  # noqa: BLE001 — a failed probe is "unknown"
            looked_up[str(name)] = None
            return None
        if rc != 0:
            looked_up[str(name)] = None
            return None
        text = (out or "").strip()
        url = text.splitlines()[0].strip() if text else None
        looked_up[str(name)] = url or None
        return url or None

    try:
        policy = UC.resolve_source(cfg, source, remote_url_lookup=_lookup,
                                   repo=repo)
    except Exception:  # noqa: BLE001 — resolver must never break an update
        policy = None

    if policy is not None and not policy.get("ok", True):
        return None, branch_cfg, policy

    if (policy is not None and policy.get("source_kind") == "canonical"
            and not any(looked_up.values())):
        # The canonical override is only applied when a remote URL was actually
        # read and proven legacy/foreign. If no remote URL could be read at all
        # (e.g. a minimal injected runner), keep the configured/default name so
        # existing `git pull <name> <branch>` behavior is preserved.
        return _fallback_remote(), branch_cfg, policy

    remote = ""
    branch = branch_cfg
    if policy is not None:
        remote = str(policy.get("remote") or "").strip()
        branch = str(policy.get("branch") or branch_cfg) or branch_cfg
    if not remote:
        remote = _fallback_remote()
    return remote, branch, policy


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
        "code": "",
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
        "source_kind": None,
        "source_url": "",
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

    remote, branch, policy = _resolve_source_policy(cfg, source, exec_, repo)
    if remote is None:
        code = str((policy or {}).get("code") or DEGRADED_CODE)
        out["state"] = "DEGRADED"
        out["code"] = code
        out["message"] = str((policy or {}).get("detail")
                             or "the configured update source was refused")
        out["detail"] = str((policy or {}).get("policy") or "")
        return out
    out["remote"] = remote
    out["branch"] = branch
    if policy is not None:
        out["source_kind"] = policy.get("source_kind")
        out["source_url"] = str(policy.get("url") or "")
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
    remote, branch, policy = _resolve_source_policy(cfg, source, exec_, repo)
    if remote is None:
        _audit("refused", actor=actor, reason="source_refused")
        return {"ok": False, "state": "FAILED",
                "code": str((policy or {}).get("code") or FAILED_CODE),
                "detail": str((policy or {}).get("detail")
                              or "the configured update source was refused"),
                "rollback_plan": "nothing changed; nothing to roll back"}
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


def _under_test() -> bool:
    """True when this process must not touch the host (pytest / explicit guard).

    Deliberately independent of :func:`_real_control_disabled` so the lifecycle
    steps that have no injectable executor (service restart, hand-off) can never
    be accidentally exercised in a test that lifted the outer guard.
    """
    if os.environ.get("QUBIT_UPDATE_DISABLED") == "1":
        return True
    try:
        from . import qubit_runtime

        return bool(qubit_runtime.under_test())
    except Exception:  # noqa: BLE001
        return bool(os.environ.get("PYTEST_CURRENT_TEST"))


def _run_exec(run: Runner, argv: Sequence[str], timeout: float) -> Tuple[int, str, str]:
    """Call a runner, converting a raised exception to a typed ``(rc, out, err)``."""
    try:
        rc, out, err = run(list(argv), timeout)
        return int(rc), str(out or ""), str(err or "")
    except Exception as exc:  # noqa: BLE001
        timed_out = ("timed out" in str(exc).lower()
                     or isinstance(exc, (TimeoutError, subprocess.TimeoutExpired)))
        return (124 if timed_out else 1), "", str(exc)[:300]


# --------------------------------------------------------------------------
# Install-method detection (Owner Decision #11 lifecycle)
# --------------------------------------------------------------------------
def _is_container() -> bool:
    if str(os.environ.get("QUBIT_CONTAINER", "") or "").strip():
        return True
    for candidate in ("/.dockerenv", "/run/.containerenv"):
        try:
            if os.path.exists(candidate):
                return True
        except OSError:
            pass
    return False


def _is_immutable(repo: Path) -> bool:
    if str(os.environ.get("QUBIT_IMMUTABLE", "") or "").strip():
        return True
    if not repo.exists():
        return False
    try:
        return not os.access(repo, os.W_OK)
    except OSError:
        return False


def _package_installed() -> bool:
    try:
        import importlib.metadata as md

        md.version("qubit-agent")
        return True
    except Exception:  # noqa: BLE001
        return False


def detect_install_method(cfg: Any) -> Dict[str, Any]:
    """Classify this Qubit install and name the Qubit command that updates it.

    Methods: ``git-checkout`` (an in-place updatable checkout), ``package``,
    ``container``, ``immutable`` (read-only tree), or ``unknown``. Detection is
    read-only and never imports the vendored runtime.

    Precedence: an explicit ``QUBIT_IMMUTABLE``/``QUBIT_CONTAINER`` env or a
    read-only repo root wins (a container/immutable tree must never be treated
    as an in-place git install); then a ``.git`` checkout; then container
    markers; then importable package metadata.
    """
    repo = repo_root(cfg)
    is_checkout = (repo / ".git").exists()
    immutable = _is_immutable(repo)
    container = _is_container()
    if str(os.environ.get("QUBIT_IMMUTABLE", "") or "").strip() or (
            immutable and not is_checkout):
        method, reason = "immutable", "the code tree is not writable in place"
    elif str(os.environ.get("QUBIT_CONTAINER", "") or "").strip():
        method, reason = "container", "QUBIT_CONTAINER is set"
    elif is_checkout:
        method, reason = "git-checkout", "a git checkout is present"
    elif immutable:
        method, reason = "immutable", "the code tree is not writable in place"
    elif container:
        method, reason = "container", "a container marker is present"
    elif _package_installed():
        method, reason = "package", "installed as a package (no .git)"
    else:
        method, reason = "unknown", "no git checkout or package metadata found"
    if method == "git-checkout":
        command = UPDATE_COMMAND
    else:
        command = _UPDATE_COMMAND_BY_METHOD.get(method, OPERATOR_COMMAND)
    return {
        "method": method,
        "repo_root": str(repo),
        "is_checkout": bool(is_checkout),
        "container": bool(container),
        "immutable": bool(immutable),
        "update_command": command,
        "reason": reason,
        "supported_in_place": method == "git-checkout",
    }


# --------------------------------------------------------------------------
# Lifecycle steps: dependency sync, service restart, health
# --------------------------------------------------------------------------
def sync_dependencies(cfg: Any, *, runner: Optional[Runner] = None,
                      repo_root: Optional[Any] = None,
                      timeout: Optional[float] = None) -> Dict[str, Any]:
    """Non-interactive dependency sync for the updated checkout.

    Default command is ``python -m pip install -e <root> --no-deps`` (or the
    same with ``uv pip`` when uv is present); ``update.deps_command`` overrides
    it and ``update.skip_deps`` disables the step. Never installs from a test
    process unless a runner is injected.
    """
    root = Path(repo_root) if repo_root is not None else globals()["repo_root"](cfg)
    result: Dict[str, Any] = {"ok": True, "state": "OK", "code": "",
                              "skipped": False, "command": [], "rc": None,
                              "detail": ""}
    if _get(cfg, "update", "skip_deps", default=False):
        result.update({"skipped": True, "state": "SKIPPED",
                       "reason": "update.skip_deps is set"})
        return result
    if not (root / "pyproject.toml").is_file():
        result.update({"skipped": True, "state": "SKIPPED",
                       "reason": "no pyproject.toml in the updated tree"})
        return result

    override = _get(cfg, "update", "deps_command")
    if isinstance(override, str) and override.strip():
        argv = shlex.split(override)
    elif isinstance(override, (list, tuple)) and override:
        argv = [str(a) for a in override]
    elif shutil.which("uv") is not None:
        argv = ["uv", "pip", "install", "-e", str(root), "--no-deps"]
    else:
        argv = [sys.executable, "-m", "pip", "install", "-e", str(root),
                "--no-deps"]
    result["command"] = argv

    if runner is None and _under_test():
        result.update({"ok": False, "state": "UNAVAILABLE",
                       "code": UNAVAILABLE_CODE, "skipped": True,
                       "reason": ("dependency install requires an injected "
                                  "runner in this environment (test guard)")})
        return result

    try:
        t = float(timeout if timeout is not None else (
            _get(cfg, "update", "deps_timeout_s", default=DEPS_TIMEOUT_S)
            or DEPS_TIMEOUT_S))
    except (TypeError, ValueError):
        t = DEPS_TIMEOUT_S
    rc, out, err = _run_exec(runner or _default_runner, argv, t)
    ok = rc == 0
    result.update({"ok": ok, "state": "OK" if ok else "FAILED",
                   "code": "" if ok else DEPENDENCY_FAILED_CODE, "rc": rc,
                   "detail": "" if ok else _redact(err or out
                                                   or "dependency install failed")[:300]})
    _audit("deps", ok=ok, rc=rc, skipped=bool(result.get("skipped")))
    return result


def _restart_service(cfg: Any, *, actor: str,
                     runner: Optional[Runner] = None) -> Dict[str, Any]:
    """Safely restart the Qubit unit this process owns.

    Only ``qubit-voicebridge.service`` / ``qubit-gateway.service`` (or another
    ``qubit-*`` unit) may be restarted; the reference ``hermes-dashboard.service``
    and any foreign unit are refused by name. The transition goes through the
    one authority path in :mod:`voicebridge.gateway_supervisor` (e-stop ->
    confirmation -> audit). In a test process the step is explicitly skipped.
    """
    try:
        from . import gateway_supervisor as GS
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "state": "FAILED", "code": SERVICE_FAILED_CODE,
                "detail": f"gateway supervisor unavailable: {exc}"}
    configured = str(_get(cfg, "update", "service_unit", default="") or "").strip()
    unit = configured or GS.daemon_service_unit(cfg)
    if not (unit in OWNED_SERVICE_UNITS or str(unit).startswith("qubit-")):
        return {"ok": False, "state": "REFUSED", "code": SERVICE_FORBIDDEN_CODE,
                "unit": unit,
                "detail": (f"refusing to restart non-Qubit unit {unit}; only "
                           "Qubit-owned units are restarted")}
    if _under_test():
        return {"ok": True, "state": "SKIPPED", "skipped": True, "unit": unit,
                "reason": ("real service control is disabled in this "
                           "environment (test guard)")}
    # The gateway-control capability is MUTATING: the policy engine only
    # accepts a user-originated request as the carrier of its confirmation
    # (``capabilities.PolicyEngine.decide`` -> source must be "user"), and the
    # dispatch guard rejects any other source value outright. The update itself
    # was explicitly operator-confirmed (``full_apply(confirm=True)``), so the
    # restart is the user's confirmed action; ``actor`` records the update
    # provenance ("cli:update"/"dashboard:user"). Passing a custom source such
    # as "update" made every real service restart fail closed.
    try:
        out = GS.control(cfg, "restart", confirmed=True, source="user",
                         actor=actor, runner=runner)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "state": "FAILED", "code": SERVICE_FAILED_CODE,
                "unit": unit, "detail": _redact(str(exc))[:300]}
    out = dict(out) if isinstance(out, dict) else {
        "ok": False, "state": "FAILED", "code": SERVICE_FAILED_CODE}
    out.setdefault("unit", unit)
    _audit("service_restart", ok=bool(out.get("ok")), unit=unit,
           state=out.get("state"))
    return out


def _health_check(cfg: Any, *, pull: Dict[str, Any],
                  runner: Optional[Runner] = None,
                  probe: Optional[Callable[[str, int], bool]] = None
                  ) -> Dict[str, Any]:
    """Verify the service is live and the listener answers after the restart.

    Version match is taken from the pull receipt (``remote_verified``); the
    listener is a real bounded TCP probe via the supervisor. In a test process
    this is explicitly skipped rather than fabricated.
    """
    remote_verified = pull.get("remote_verified")
    if _under_test():
        return {"ok": True, "state": "SKIPPED", "skipped": True,
                "version_match": (None if remote_verified is None
                                  else bool(remote_verified)),
                "listener": None,
                "detail": "live health probe disabled in this environment "
                          "(test guard)"}
    try:
        from . import gateway_supervisor as GS

        st = GS.status(cfg, runner=runner, probe=probe)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "state": "FAILED", "code": HEALTH_FAILED_CODE,
                "detail": _redact(str(exc))[:300]}
    running = bool(st.get("running"))
    listener = bool(st.get("port_in_use"))
    ok = running or listener
    detail = ""
    if not ok:
        detail = (st.get("detail") or st.get("health_detail")
                  or "the service is not active and the listener is down")
    result = {
        "ok": ok, "state": "OK" if ok else "FAILED",
        "code": "" if ok else HEALTH_FAILED_CODE,
        "version_match": (None if remote_verified is None
                          else bool(remote_verified)),
        "service_running": running, "listener": listener,
        "health": st.get("health"), "unit": st.get("service_unit"),
        "detail": _redact(detail)[:300] if detail else "",
    }
    _audit("health", ok=ok, running=running, listener=listener)
    return result


# --------------------------------------------------------------------------
# Full lifecycle orchestrator
# --------------------------------------------------------------------------
def run_rollback(cfg: Any, *, backup_dir: Any = None,
                 runner: Optional[Runner] = None, git: Optional[Runner] = None,
                 actor: str = "cli:update") -> Dict[str, Any]:
    """Public alias for the full rollback (delegates to :func:`rollback`)."""
    return rollback(cfg, backup_dir=backup_dir, runner=runner, git=git,
                    actor=actor)


def _finalize_receipt(cfg: Any, receipt: Dict[str, Any], outcome: str) -> Dict[str, Any]:
    """Stamp the outcome, persist the receipt (best-effort), return it."""
    from . import update_receipt as UR

    receipt["outcome"] = outcome
    receipt["finished_at"] = UR.utc_now_iso()
    try:
        UR.write_receipt(cfg, receipt)
    except Exception:  # noqa: BLE001
        pass
    return receipt


def _rollback_after_failure(cfg: Any, receipt: Dict[str, Any], *,
                            step: str, failure_code: str, detail: str,
                            backup_dir: Optional[str], runner: Optional[Runner],
                            git: Optional[Runner], actor: str) -> Dict[str, Any]:
    """Restore the pre-apply revision after a post-pull step failed.

    Uses the existing :func:`rollback` path (which itself writes a pre-rollback
    snapshot). Records ``rolled_back`` truthfully: a failed rollback is never
    reported as recovered.
    """
    receipt.update({"ok": False, "state": "FAILED", "code": failure_code,
                    "failure_step": str(step), "detail": _redact(detail)[:500]})
    if not backup_dir:
        receipt["rolled_back"] = False
        receipt["rollback_info"] = {
            "ok": False, "code": NO_BACKUP_CODE,
            "detail": "no pre-apply backup is available to restore"}
        return _finalize_receipt(cfg, receipt, "failed")
    rb = rollback(cfg, backup_dir=backup_dir, runner=runner, git=git,
                  actor=actor)
    receipt["rollback_info"] = rb
    rolled = bool(rb.get("ok"))
    receipt["rolled_back"] = rolled
    if rolled:
        receipt["state"] = "ROLLED_BACK"
        receipt["rollback_code"] = ROLLBACK_OK_CODE
        receipt["detail"] = (
            f"{step} failed ({failure_code}); restored the pre-update revision "
            f"{rb.get('restored_revision')}")
        return _finalize_receipt(cfg, receipt, "rolled_back")
    receipt["rolled_back"] = False
    receipt["code"] = ROLLBACK_FAILED_CODE
    receipt["rollback_failed"] = True
    receipt["detail"] = (
        f"{step} failed ({failure_code}); automatic rollback also failed: "
        f"{rb.get('detail') or rb.get('code')}")
    return _finalize_receipt(cfg, receipt, "failed")


def full_apply(cfg: Any, *, confirm: bool = False,
               runner: Optional[Runner] = None, git: Optional[Runner] = None,
               source: Optional[str] = None, channel: Optional[str] = None,
               actor: str = "cli:update",
               resume_token: Optional[str] = None,
               tag: Optional[str] = None) -> Dict[str, Any]:
    """Run the whole update lifecycle and return ONE complete receipt.

    Ordered: confirm -> e-stop -> fail-closed test guard -> install-method
    detection -> update lock (typed busy) -> dirty-worktree refusal ->
    snapshot -> fast-forward pull -> re-exec/hand-off -> dependency sync ->
    config/schema migration -> compile/import validation -> service restart ->
    health/version check -> receipt -> release lock.

    Any failure after the pull automatically restores the previous revision via
    the existing :func:`rollback` path and records ``rolled_back``. Success is
    never claimed when a step failed.

    When this process is a post-pull hand-off child (``resume_token`` is set or
    :data:`update_handoff.RESUME_ENV` is in the environment) steps 6/7/8 — dirty
    check, snapshot, and pull — are skipped, because the parent already ran them
    and handed over the resulting revisions/backup. The child re-acquires real
    ownership of the update lock (the parent transferred the marker to it) and
    runs only the post-pull steps, so a resume never pulls or snapshots twice.
    """
    from . import update_receipt as UR
    from . import update_lock as UL
    from . import update_validation as UV
    from . import update_config_migration as UCM
    from . import update_handoff as UH

    repo = repo_root(cfg)
    exec_ = git or runner or _default_runner
    ch = str(channel or _get(cfg, "update", "channel", default="stable")
             or "stable")
    resolved_resume = str(resume_token or os.environ.get(UH.RESUME_ENV, "")
                          or "").strip() or None
    resumed = bool(resolved_resume)
    receipt = UR.new_receipt(actor, channel=ch)
    receipt["tag"] = str(tag).strip() or None

    def refuse(code: str, state: str, detail: str, **extra: Any) -> Dict[str, Any]:
        receipt.update({"ok": False, "state": state, "code": code,
                        "detail": detail})
        receipt.update(extra)
        return _finalize_receipt(cfg, receipt, "refused")

    # 1. confirmation gate
    if not confirm:
        return refuse(CONFIRM_CODE, "CONFIRM_REQUIRED",
                      "updating Qubit modifies the checkout; re-issue with "
                      "confirm=true (dashboard Update-now dialog / CLI --confirm)")
    # 2. e-stop gate (public probe so a caller/monkeypatched seam is honored)
    if estop_engaged(cfg):
        return refuse(ESTOP_CODE, "UNAVAILABLE",
                      "emergency stop is latched; update refused")
    # 3. fail-closed test guard. Delegate to the primitive ``apply`` (which
    #    enforces the same guard) so the documented snapshot/apply seams stay
    #    the single backend; nothing runs while the guard is set.
    if runner is None and git is None and _real_control_disabled():
        return apply(cfg, confirm=confirm, source=source, actor=actor)

    # 4. install-method detection
    method = detect_install_method(cfg)
    receipt["install_method"] = method["method"]
    receipt["update_command"] = method.get("update_command")
    if method["method"] != "git-checkout":
        return refuse(
            OPERATOR_OWNED_CODE, "UNAVAILABLE",
            f"Qubit is a {method['method']} install, not an in-place git "
            f"checkout; update it with: {method.get('update_command')}",
            update_command=method.get("update_command"),
            qubit_counterpart=method.get("update_command"))

    # 5. update lock
    lock = UL.UpdateLock(path=UL.lock_path(cfg))
    if not lock.acquire():
        holder = lock.holder
        return refuse(
            LOCK_BUSY_CODE, "CONFLICT",
            UL.describe_holder(holder) + "; wait for it to finish and retry",
            lock_holder=(holder.pid if holder is not None else None))
    receipt["lock_acquired"] = True
    try:
        if resumed:
            # DEFECT 1 (resume idempotency): the parent already refused a dirty
            # worktree, snapshotted, and fast-forwarded the checkout, then
            # handed this child the result. Re-acquire real ownership of the
            # marker it transferred to this pid (a foreign live holder is still
            # a typed busy refusal) and go straight to the post-pull lifecycle.
            # Never re-run the dirty check, snapshot, or pull: a second pull on
            # the freshly-pulled tree is exactly the old/new-code mix this
            # hand-off exists to avoid.
            if not lock.acquire():
                holder = lock.holder
                return refuse(
                    LOCK_BUSY_CODE, "CONFLICT",
                    UL.describe_holder(holder) + "; wait for it to finish and retry",
                    lock_holder=(holder.pid if holder is not None else None),
                    resumed=True)
            receipt["resumed"] = True
            backup_dir = UH.resume_backup_dir()
            pre = UH.resume_pre_revision()
            post = UH.resume_new_revision()
            changed = (pre != post) if (pre and post) else None
            pull = {"ok": True, "state": "RESUMED", "code": "",
                    "remote_verified": None,
                    "pre_revision": pre, "post_revision": post,
                    "changed": changed}
            receipt["backup_dir"] = backup_dir
            receipt["previous_revision"] = pre
            receipt["new_revision"] = post
            receipt["pre_revision"] = pre
            receipt["post_revision"] = post
            receipt["changed"] = changed
        else:
            # 6. dirty-worktree refusal (before any snapshot)
            clean, cerr = _is_clean(exec_, repo)
            if clean is None:
                return refuse(FAILED_CODE, "FAILED",
                              _redact(cerr or "could not read the worktree status")[:300],
                              rollback_plan="nothing changed; nothing to roll back")
            if not clean:
                return refuse(
                    WORKTREE_DIRTY_CODE, "CONFLICT",
                    "the worktree has uncommitted changes; commit or stash them "
                    "before updating (Qubit will not discard your work)",
                    rollback_plan="nothing changed; nothing to roll back")

            # 7. snapshot / backup
            snap = snapshot(cfg, runner=runner, git=git, reason="pre-apply",
                            actor=actor, source=source)
            receipt["backup_dir"] = snap.get("backup_dir")
            receipt["previous_revision"] = snap.get("pre_revision")
            if not snap.get("ok"):
                return refuse(snap.get("code") or FAILED_CODE, "FAILED",
                              snap.get("detail") or "snapshot failed")
            backup_dir = snap.get("backup_dir")

            # 8. fast-forward pull ONLY (non-interactive, timeout-bounded)
            pull = apply(cfg, confirm=True, runner=runner, git=git, source=source,
                         actor=actor)
            pre = pull.get("pre_revision") or receipt.get("previous_revision")
            post = pull.get("post_revision")
            receipt["previous_revision"] = pre or receipt.get("previous_revision")
            receipt["new_revision"] = post
            receipt["remote"] = pull.get("remote")
            receipt["branch"] = pull.get("branch")
            receipt["pull_result"] = {k: pull.get(k) for k in (
                "ok", "state", "code", "command", "changed", "remote_verified",
                "detail")}
            # Compatibility aliases the dashboard/CLI read from an apply receipt.
            receipt["pre_revision"] = receipt.get("previous_revision")
            receipt["post_revision"] = receipt.get("new_revision")
            receipt["changed"] = bool(pull.get("changed"))
            receipt["remote_verified"] = pull.get("remote_verified")
            if pull.get("rollback_plan"):
                receipt["rollback_plan"] = pull.get("rollback_plan")
            if pull.get("rollback"):
                receipt["rollback"] = pull.get("rollback")
            if not pull.get("ok"):
                if post and pre and post != pre:
                    return _rollback_after_failure(
                        cfg, receipt, step="pull",
                        failure_code=pull.get("code") or FAILED_CODE,
                        detail=pull.get("detail") or "the fast-forward pull failed",
                        backup_dir=backup_dir, runner=runner, git=git, actor=actor)
                return refuse(pull.get("code") or FAILED_CODE,
                              pull.get("state") or "FAILED",
                              pull.get("detail") or "the fast-forward pull failed")
            changed = bool(pull.get("changed"))

            # 9. re-exec / hand-off onto the newly pulled code
            handoff = UH.attempt_handoff(cfg, resume_token=resolved_resume,
                                         runner=runner, repo_root=repo,
                                         backup_dir=backup_dir,
                                         pre_revision=pre, new_revision=post)
            receipt["handoff_result"] = handoff
            if handoff.get("spawned"):
                # DEFECT 2 (clean ownership transfer): rewrite the marker to the
                # child pid and make this process's release a no-op, so a third
                # updater can never start in the gap. If the pid is unknown or
                # the transfer fails, keep the current behavior (this process
                # still owns the marker and releases it) and record it.
                child_pid = handoff.get("pid")
                transferred = lock.transfer_to(
                    child_pid if child_pid is not None else -1)
                receipt["lock_transfer"] = "ok" if transferred else "failed"
                receipt.update({"ok": True, "state": "HANDOFF", "code": HANDOFF_CODE,
                                "detail": "post-pull lifecycle handed to a fresh "
                                          "interpreter on the new code"})
                return _finalize_receipt(cfg, receipt, "handoff")
            if handoff.get("state") == "FAILED":
                return _rollback_after_failure(
                    cfg, receipt, step="handoff",
                    failure_code=handoff.get("code") or HANDOFF_CODE,
                    detail=handoff.get("detail") or "hand-off failed",
                    backup_dir=backup_dir, runner=runner, git=git, actor=actor)

        # 10. sync dependencies
        deps = sync_dependencies(cfg, runner=runner, repo_root=repo)
        receipt["dependency_result"] = deps
        if not deps.get("ok"):
            return _rollback_after_failure(
                cfg, receipt, step="dependencies",
                failure_code=DEPENDENCY_FAILED_CODE,
                detail=deps.get("detail") or "dependency sync failed",
                backup_dir=backup_dir, runner=runner, git=git, actor=actor)

        # 11. config / schema migration
        migration = UCM.migrate_config(cfg, dry_run=False)
        receipt["config_migration_result"] = migration
        if not migration.get("ok"):
            return _rollback_after_failure(
                cfg, receipt, step="config_migration",
                failure_code=MIGRATION_FAILED_CODE,
                detail=migration.get("detail") or "config migration failed",
                backup_dir=backup_dir, runner=runner, git=git, actor=actor)

        # 12/13. compile/import validation + frontend assets
        validation = UV.validate_installation(cfg, runner=runner, repo_root=repo)
        receipt["validation_result"] = validation
        if not validation.get("ok"):
            return _rollback_after_failure(
                cfg, receipt, step="validation",
                failure_code=VALIDATION_FAILED_CODE,
                detail=validation.get("detail") or "validation failed",
                backup_dir=backup_dir, runner=runner, git=git, actor=actor)

        # 14. restart the owning Qubit service/gateway safely
        service = _restart_service(cfg, actor=actor, runner=runner)
        receipt["service_restart_result"] = service
        if not service.get("ok"):
            return _rollback_after_failure(
                cfg, receipt, step="service_restart",
                failure_code=service.get("code") or SERVICE_FAILED_CODE,
                detail=service.get("detail") or "service restart failed",
                backup_dir=backup_dir, runner=runner, git=git, actor=actor)

        # 15. health + listener + version match
        health = _health_check(cfg, pull=pull, runner=runner)
        receipt["health_result"] = health
        if not health.get("ok"):
            return _rollback_after_failure(
                cfg, receipt, step="health",
                failure_code=HEALTH_FAILED_CODE,
                detail=health.get("detail") or "post-update health check failed",
                backup_dir=backup_dir, runner=runner, git=git, actor=actor)

        # 16. success receipt
        if changed is None:
            detail = "updated Qubit (resumed the post-pull lifecycle)"
        elif changed:
            detail = f"updated Qubit ({pre} -> {post})"
        else:
            detail = "updated Qubit (already up to date)"
        receipt.update({"ok": True, "state": "OK", "code": "", "detail": detail})
        _audit("full_apply", actor=actor, pre=pre, post=post,
               changed=changed, backup_dir=backup_dir, resumed=resumed)
        return _finalize_receipt(cfg, receipt, "success")
    finally:
        # 16. release lock
        lock.release()


__all__ = ["TIMEOUT_S", "DEPS_TIMEOUT_S", "CONFIRM_CODE", "WORKTREE_DIRTY_CODE",
           "ESTOP_CODE", "UNAVAILABLE_CODE", "OPERATOR_OWNED_CODE",
           "FAILED_CODE", "DEGRADED_CODE", "NO_BACKUP_CODE",
           "BACKUP_INVALID_CODE", "LOCK_BUSY_CODE", "DEPENDENCY_FAILED_CODE",
           "MIGRATION_FAILED_CODE", "VALIDATION_FAILED_CODE",
           "SERVICE_FAILED_CODE", "SERVICE_FORBIDDEN_CODE", "HEALTH_FAILED_CODE",
           "ROLLBACK_FAILED_CODE", "ROLLBACK_OK_CODE", "HANDOFF_CODE",
           "INSTALL_METHODS",
           "OWNED_SERVICE_UNITS", "UPDATE_COMMAND", "OPERATOR_COMMAND",
           "ROLLBACK_TEMPLATE", "SNAPSHOT_MANIFEST", "SNAPSHOT_BUNDLE",
           "repo_root", "check", "apply", "snapshot", "rollback",
           "estop_engaged", "update_backup_root", "detect_install_method",
           "sync_dependencies", "run_rollback", "full_apply"]

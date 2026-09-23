"""Owner Decision #11 — `qubit update` is the sanctioned native update surface.

Covers the native family end-to-end: `--check` (read-only, truthful DEGRADED,
reports product version + install method + revision + pinned runtime-image
state), `--apply` (timestamped `git bundle` backup then a fast-forward),
`--rollback` (restore the recorded pre-apply revision; the rollback is itself
reversible via a pre-rollback snapshot), the verb/refusal/exit-code contract,
that the family is never fork-forwarded, and that its output carries no legacy
brand.

Every git command runs through an injected runner bound to a *real* temp
checkout built under ``tmp_path`` (a local bare remote, a source repo, and an
install clone one commit behind). Nothing here touches the product worktree,
the network, ``~/.hermes``, or the live service.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from voicebridge import cli as CLI  # noqa: E402
from voicebridge import cli_qubit_forward as HF  # noqa: E402
from voicebridge import update_manager as UM  # noqa: E402

LEGACY_TOKENS = ("Hermes", "OpenClaw", "NousPortal", "NousResearch",
                 "Nous Research")
FORBIDDEN_VERBS = {"reset", "checkout", "stash", "clean", "rebase", "merge",
                   "rm", "filter-branch", "prune", "gc"}
FORBIDDEN_FLAGS = {"--force", "-f", "--hard", "--all", "--prune"}


# ---------------------------------------------------------------------------
# temp-repo scaffolding (mirrors tests/test_update_qualification9u.py)
# ---------------------------------------------------------------------------
class _RecordingRunner:
    """Injected ``runner``: records argv, then really executes it in a temp env."""

    def __init__(self, env):
        self.env = env
        self.calls = []

    def __call__(self, argv, timeout):
        argv = list(argv)
        self.calls.append(argv)
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout, env=self.env)
        return proc.returncode, proc.stdout, proc.stderr


def _git_env(tmp_path: Path) -> dict:
    empty = tmp_path / "gitconfig-empty"
    empty.write_text("")
    env = dict(os.environ)
    env.update({
        "GIT_AUTHOR_NAME": "Qubit Update Test",
        "GIT_AUTHOR_EMAIL": "update@example.invalid",
        "GIT_COMMITTER_NAME": "Qubit Update Test",
        "GIT_COMMITTER_EMAIL": "update@example.invalid",
        "GIT_CONFIG_GLOBAL": str(empty),
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
    })
    return env


def _git(args, cwd: Path, env: dict) -> str:
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          text=True, env=env)
    assert proc.returncode == 0, (
        f"git {' '.join(args)} failed ({proc.returncode}): "
        f"{proc.stdout}{proc.stderr}")
    return proc.stdout.strip()


def _head(repo: Path, env: dict) -> str:
    return _git(["rev-parse", "HEAD"], repo, env)


def _commit_all(repo: Path, env: dict, message: str) -> None:
    _git(["add", "-A"], repo, env)
    _git(["commit", "-m", message], repo, env)


@pytest.fixture
def install(tmp_path):
    """A temp install one commit behind its bare remote."""
    env = _git_env(tmp_path)
    bare = tmp_path / "remote.git"
    source = tmp_path / "source"
    checkout = tmp_path / "install"

    _git(["init", "--bare", "-b", "main", str(bare)], tmp_path, env)
    _git(["init", "-b", "main", str(source)], tmp_path, env)
    (source / "VERSION").write_text("1\n")
    _commit_all(source, env, "first")
    _git(["remote", "add", "origin", str(bare)], source, env)
    _git(["push", "-u", "origin", "main"], source, env)

    _git(["clone", str(bare), str(checkout)], tmp_path, env)
    first = _head(checkout, env)

    (source / "VERSION").write_text("2\n")
    _commit_all(source, env, "second")
    _git(["push", "origin", "main"], source, env)
    second = _head(source, env)

    assert first != second
    assert _head(checkout, env) == first
    assert _git(["status", "--porcelain"], checkout, env) == ""
    return SimpleNamespace(checkout=checkout, source=source, bare=bare,
                           env=env, first=first, second=second)


def _wire(monkeypatch, install):
    """Lift the fail-closed guard and bind the default runner to the temp repo."""
    monkeypatch.setattr(UM, "_real_control_disabled", lambda: False)
    monkeypatch.setattr(UM, "_estop", lambda _cfg: False)
    runner = _RecordingRunner(install.env)
    monkeypatch.setattr(UM, "_default_runner", runner)
    return runner


def _hermetic(tmp_path, monkeypatch, update=None):
    """A hermetic config/state tree; returns the voice.yaml path."""
    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME",
                "XDG_CACHE_HOME"):
        d = tmp_path / var.lower()
        d.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(var, str(d))
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("QUBIT_HOME", str(home / "qubit-runtime"))
    section: dict = {"backup_dir": str(tmp_path / "backups")}
    section.update({k: str(v) for k, v in (update or {}).items()})
    cfg = tmp_path / "voice.yaml"
    cfg.write_text(yaml.safe_dump(
        {"logging": {"jsonl": False}, "update": section}, sort_keys=True))
    return cfg


def _run(argv):
    buf = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(err):
        rc = CLI.main(list(argv))
    return rc, buf.getvalue(), err.getvalue()


def _ns(**kw):
    base = {"check": False, "apply": False, "rollback": False, "json": True,
            "source": "", "backup_dir": None}
    base.update(kw)
    return argparse.Namespace(**base)


# ---------------------------------------------------------------------------
# check — read-only, truthful, reports the real surface
# ---------------------------------------------------------------------------
def test_cmd_update_check_reports_product_and_runtime_state(monkeypatch):
    def _fake_check(cfg, source=None):
        return {"state": "OK", "install_method": "git-checkout",
                "current_version": "1.0.0", "current_revision": "a" * 40,
                "remote": "origin", "branch": "main",
                "remote_revision": "b" * 40, "message": "an update is available"}

    monkeypatch.setattr(UM, "check", _fake_check)
    monkeypatch.setattr(CLI, "_update_runtime_state", lambda cfg: {
        "state": "READY", "daemon": "none-required", "images": []})
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = CLI._cmd_update(object(), _ns(check=True))
    out = json.loads(buf.getvalue())
    assert rc == 0
    assert out["state"] == "OK"
    assert out["current_version"] == "1.0.0"
    assert out["install_method"] == "git-checkout"
    assert out["current_revision"] == "a" * 40
    assert out["runtime_image"]["state"] == "READY"


def test_cmd_update_check_degraded_exits_2_not_up_to_date(monkeypatch):
    monkeypatch.setattr(UM, "check", lambda cfg, source=None: {
        "state": "DEGRADED", "behind": None, "update_available": False,
        "message": "couldn't reach the update source (origin/main); not "
                   "reporting up to date"})
    monkeypatch.setattr(CLI, "_update_runtime_state", lambda cfg: None)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = CLI._cmd_update(object(), _ns(check=True))
    out = json.loads(buf.getvalue())
    assert rc == 2
    assert out["state"] == "DEGRADED"
    assert out["behind"] is None
    assert "latest" not in (out["message"] or "").lower()


def test_cmd_update_check_forwards_source_override(monkeypatch):
    seen = {}

    def _fake_check(cfg, source=None):
        seen["source"] = source
        return {"state": "OK", "message": "ok"}

    monkeypatch.setattr(UM, "check", _fake_check)
    monkeypatch.setattr(CLI, "_update_runtime_state", lambda cfg: None)
    with redirect_stdout(io.StringIO()):
        CLI._cmd_update(object(), _ns(check=True, source="/tmp/some-bare.git"))
    assert seen["source"] == "/tmp/some-bare.git"


# ---------------------------------------------------------------------------
# apply — timestamped backup then fast-forward; dirty tree refused
# ---------------------------------------------------------------------------
def test_cli_update_apply_writes_backup_then_fast_forwards(install, tmp_path,
                                                           monkeypatch):
    runner = _wire(monkeypatch, install)
    cfg = _hermetic(tmp_path, monkeypatch, update={
        "repo_root": install.checkout, "remote": "origin", "branch": "main"})

    rc, out_text, _ = _run(["update", "--apply", "--json",
                            "--config", str(cfg)])
    out = json.loads(out_text)
    assert rc == 0, out
    assert out["ok"] is True and out["post_revision"] == install.second
    assert _head(install.checkout, install.env) == install.second

    bdir = Path(out["backup_dir"])
    assert bdir.is_dir() and (bdir / "manifest.json").is_file()
    assert (bdir / "code.bundle").is_file()
    manifest = json.loads((bdir / "manifest.json").read_text())
    assert manifest["kind"] == "qubit-update"
    assert manifest["reason"] == "pre-apply"
    assert manifest["pre_revision"] == install.first
    assert manifest["install_method"] == "git-checkout"

    # Exactly one pull, naming the configured remote/branch; no destructive
    # verb/flag is ever executed by apply.
    pulls = [c for c in runner.calls if c[3:4] == ["pull"]]
    assert pulls == [["git", "-C", str(install.checkout), "pull", "--ff-only",
                      "origin", "main"]]
    for call in runner.calls:
        args = call[3:]
        assert args[0] not in FORBIDDEN_VERBS, call
        assert not (set(args) & FORBIDDEN_FLAGS), call


def test_cli_update_apply_dirty_worktree_refused_tree_untouched(
        install, tmp_path, monkeypatch):
    _wire(monkeypatch, install)
    cfg = _hermetic(tmp_path, monkeypatch, update={
        "repo_root": install.checkout, "remote": "origin", "branch": "main"})
    (install.checkout / "VERSION").write_text("locally edited\n")
    before = _head(install.checkout, install.env)

    rc, out_text, _ = _run(["update", "--apply", "--json",
                            "--config", str(cfg)])
    out = json.loads(out_text)
    assert rc == 2, out
    assert out["state"] == "CONFLICT" and out["code"] == UM.WORKTREE_DIRTY_CODE
    assert _head(install.checkout, install.env) == before
    assert (install.checkout / "VERSION").read_text() == "locally edited\n"


# ---------------------------------------------------------------------------
# e-stop, non-checkout, verb contract
# ---------------------------------------------------------------------------
def test_cli_update_apply_estop_is_rc3(tmp_path, monkeypatch):
    monkeypatch.setattr(UM, "_real_control_disabled", lambda: False)
    monkeypatch.setattr(UM, "_estop", lambda _cfg: True)
    cfg = _hermetic(tmp_path, monkeypatch, update={"repo_root": tmp_path})
    rc, out_text, _ = _run(["update", "--apply", "--json",
                            "--config", str(cfg)])
    out = json.loads(out_text)
    assert rc == 3, out
    assert out["code"] == UM.ESTOP_CODE
    assert out["state"] == "denied"


def test_cli_update_apply_non_checkout_names_install_sh(tmp_path, monkeypatch):
    monkeypatch.setattr(UM, "_real_control_disabled", lambda: False)
    monkeypatch.setattr(UM, "_estop", lambda _cfg: False)
    notgit = tmp_path / "notgit"
    notgit.mkdir()
    cfg = _hermetic(tmp_path, monkeypatch, update={"repo_root": notgit})
    rc, out_text, _ = _run(["update", "--apply", "--json",
                            "--config", str(cfg)])
    out = json.loads(out_text)
    assert rc == 2, out
    assert out["code"] == UM.OPERATOR_OWNED_CODE
    assert "scripts/install.sh" in out["detail"]


def test_cli_update_rejects_multiple_verbs(tmp_path, monkeypatch):
    cfg = _hermetic(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as exc:
        with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
            CLI.main(["update", "--check", "--apply", "--config", str(cfg)])
    assert exc.value.code == 2


def test_cmd_update_apply_dry_run_previews_without_mutation(monkeypatch):
    monkeypatch.setattr(UM, "estop_engaged", lambda cfg: False)
    called = {"snap": 0, "apply": 0}

    def _snap(cfg, **kw):
        called["snap"] += 1
        return {"ok": True}

    def _apply(cfg, **kw):
        called["apply"] += 1
        return {"ok": True}

    monkeypatch.setattr(UM, "snapshot", _snap)
    monkeypatch.setattr(UM, "apply", _apply)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = CLI._cmd_update(object(), _ns(apply=True, dry_run=True))
    out = json.loads(buf.getvalue())
    assert rc == 0 and out["state"] == "preview"
    assert called == {"snap": 0, "apply": 0}


def test_cmd_update_verb_conflict_guard_is_typed():
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = CLI._cmd_update(object(), _ns(check=True, apply=True))
    out = json.loads(buf.getvalue())
    assert rc == 2
    assert out["code"] == "UPDATE_VERB_CONFLICT"


def test_cmd_update_apply_artifact_mismatch_is_rc2(monkeypatch):
    monkeypatch.setattr(UM, "estop_engaged", lambda cfg: False)
    monkeypatch.setattr(UM, "snapshot", lambda cfg, **kw: {
        "ok": True, "state": "OK", "backup_dir": "/tmp/qback",
        "bundle": "/tmp/qback/code.bundle"})
    monkeypatch.setattr(UM, "apply", lambda cfg, **kw: {
        "ok": False, "state": "FAILED", "code": UM.FAILED_CODE,
        "remote_verified": False,
        "detail": "pulled revision bbbb does not match origin/main aaaa"})
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = CLI._cmd_update(object(), _ns(apply=True))
    out = json.loads(buf.getvalue())
    assert rc == 2, out
    assert out["state"] == "FAILED" and out["remote_verified"] is False


# ---------------------------------------------------------------------------
# rollback — restores the recorded revision; no backup refused
# ---------------------------------------------------------------------------
def test_cli_update_rollback_restores_recorded_revision(install, tmp_path,
                                                        monkeypatch):
    _wire(monkeypatch, install)
    cfg = _hermetic(tmp_path, monkeypatch, update={
        "repo_root": install.checkout, "remote": "origin", "branch": "main"})

    rc, _, _ = _run(["update", "--apply", "--json", "--config", str(cfg)])
    assert rc == 0
    assert _head(install.checkout, install.env) == install.second

    rc, out_text, _ = _run(["update", "--rollback", "--json",
                            "--config", str(cfg)])
    out = json.loads(out_text)
    assert rc == 0, out
    assert out["restored_revision"] == install.first
    assert out["reversible"] is True
    assert _head(install.checkout, install.env) == install.first
    assert (install.checkout / "VERSION").read_text() == "1\n"
    # The rollback wrote its own pre-rollback snapshot first.
    assert Path(out["pre_rollback_snapshot"]).is_dir()


def test_cli_update_rollback_without_backup_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(UM, "_real_control_disabled", lambda: False)
    monkeypatch.setattr(UM, "_estop", lambda _cfg: False)
    cfg = _hermetic(tmp_path, monkeypatch, update={"repo_root": tmp_path})
    rc, out_text, _ = _run(["update", "--rollback", "--json",
                            "--config", str(cfg)])
    out = json.loads(out_text)
    assert rc == 2, out
    assert out["code"] == UM.NO_BACKUP_CODE


# ---------------------------------------------------------------------------
# never fork-forwarded; no legacy brand in output
# ---------------------------------------------------------------------------
def test_cli_update_is_never_fork_forwarded(tmp_path, monkeypatch):
    from voicebridge import qubit_runtime

    calls = []
    monkeypatch.setattr(qubit_runtime, "run",
                        lambda argv=None: calls.append(list(argv or [])) or 0)
    assert HF.route(["update"]) is None
    assert HF.route(["update", "--check"]) is None
    cfg = _hermetic(tmp_path, monkeypatch)
    _run(["update", "--check", "--config", str(cfg)])
    assert calls == []


def test_cli_update_output_has_no_legacy_brand(tmp_path, monkeypatch):
    cfg = _hermetic(tmp_path, monkeypatch)
    helper = io.StringIO()
    with pytest.raises(SystemExit):
        with redirect_stdout(helper):
            CLI.main(["update", "--help"])
    help_text = helper.getvalue()
    assert "Qubit" in help_text
    for token in LEGACY_TOKENS:
        assert token not in help_text, token

    _rc, check_text, _err = _run(["update", "--check", "--config", str(cfg)])
    assert "Qubit update" in check_text
    for token in LEGACY_TOKENS:
        assert token not in check_text, token


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))

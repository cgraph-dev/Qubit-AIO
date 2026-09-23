"""Wave 2 — the fork-absent CLI rows: ``cli:pause`` + ``cli:worktree add/remove/switch``.

The pinned fork's ``pause`` writes a second emergency-stop sentinel and its
``worktree`` family has only ``list``/``prune``. Qubit adds native equivalents:

* ``qubit pause --confirm`` engages the ONE Qubit emergency stop (the shared
  :class:`voicebridge.power.PowerControl` latch), never the fork sentinel;
* ``qubit worktree add|remove|switch --confirm`` are real git worktree
  operations confined to a registered project root, with no host-write
  fallback.

Every case executes the real production caller (:func:`voicebridge.cli.main`).
The literal argv lists are the on-disk evidence the gap-ledger AST scanner
indexes.
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from voicebridge import cli as CLI  # noqa: E402
from voicebridge import cli_qubit_forward as HF  # noqa: E402


def _run(argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = CLI.main(list(argv))
    return rc, buf.getvalue()


def _fixture(tmp_path, monkeypatch):
    """Hermetic config + a real git repo registered as an active project."""
    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME"):
        target = tmp_path / var.lower()
        target.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(var, str(target))
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("QUBIT_HOME", str(home / ".hermes"))
    cfgp = tmp_path / "voice.yaml"
    cfgp.write_text("logging:\n  jsonl: false\n")
    repo = tmp_path / "repo"
    repo.mkdir()
    for argv in (["init", "-q"],
                 ["config", "user.email", "t@t"],
                 ["config", "user.name", "t"]):
        subprocess.run(["git", "-C", str(repo), *argv], check=True)
    (repo / "a.txt").write_text("hi")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "init"],
                   check=True)
    from voicebridge.config import Config
    from voicebridge import projects as PJ

    cfg = Config.load(cfgp)
    PJ.create(cfg, "wtproj", [str(repo)], confirmed=True, use=True)
    return cfgp, repo


# ---------------------------------------------------------------------------
# `qubit pause` -> the ONE Qubit emergency stop.
# ---------------------------------------------------------------------------
def test_pause_without_confirm_is_a_typed_refusal(tmp_path, monkeypatch):
    cfgp, _repo = _fixture(tmp_path, monkeypatch)
    rc, out = _run(["pause", "--config", str(cfgp)])
    assert rc == 3, out[:200]
    payload = json.loads(out)
    assert payload["state"] == "refused"
    assert payload["code"] == "ESTOP_QUBIT_OWNED"


def test_pause_confirm_latches_the_one_qubit_estop(tmp_path, monkeypatch):
    cfgp, _repo = _fixture(tmp_path, monkeypatch)
    rc, out = _run(["pause", "--confirm", "--config", str(cfgp)])
    assert rc == 0, out[:300]
    payload = json.loads(out)
    assert payload["state"] == "OK"
    assert "emergency stop" in payload["detail"]

    from voicebridge.config import Config
    from voicebridge import power as PW

    cfg = Config.load(cfgp)
    assert PW.power_control(cfg).state["emergency_stop"] is True
    assert (cfg.state_dir() / "power-control.json").is_file()


def test_pause_confirm_is_idempotent(tmp_path, monkeypatch):
    cfgp, _repo = _fixture(tmp_path, monkeypatch)
    assert _run(["pause", "--confirm", "--config", str(cfgp)])[0] == 0
    rc, out = _run(["pause", "--confirm", "--config", str(cfgp)])
    assert rc == 0, out[:200]
    assert json.loads(out)["state"] == "OK"


# ---------------------------------------------------------------------------
# `qubit worktree add|remove|switch` -> confined git operations.
# ---------------------------------------------------------------------------
def test_worktree_add_without_confirm_is_a_typed_refusal(tmp_path, monkeypatch):
    cfgp, repo = _fixture(tmp_path, monkeypatch)
    argv = ["worktree", "add", "wt1"]
    rc, out = _run([*argv, "--config", str(cfgp)])
    assert rc == 3, out[:200]
    payload = json.loads(out)
    assert payload["state"] == "refused"
    assert payload["code"] == "FORK_SUB_UNCLASSIFIED"
    assert not (repo / ".worktrees" / "wt1").exists()


def test_worktree_add_confirm_creates_confined_worktree(tmp_path, monkeypatch):
    cfgp, repo = _fixture(tmp_path, monkeypatch)
    argv = ["worktree", "add", "wt1"]
    rc, out = _run([*argv, "--confirm", "--root", str(repo),
                    "--config", str(cfgp)])
    assert rc == 0, out[:300]
    payload = json.loads(out)
    assert payload["state"] == "OK"
    assert payload["path"] == str(repo / ".worktrees" / "wt1")
    assert (repo / ".worktrees" / "wt1").is_dir()
    assert payload["truncated"] is False


def test_worktree_add_confirm_with_branch(tmp_path, monkeypatch):
    cfgp, repo = _fixture(tmp_path, monkeypatch)
    argv = ["worktree", "add", "wt2", "--branch", "feature"]
    rc, out = _run([*argv, "--confirm", "--root", str(repo),
                    "--config", str(cfgp)])
    assert rc == 0, out[:300]
    assert json.loads(out)["branch"] == "feature"
    branches = subprocess.run(
        ["git", "-C", str(repo / ".worktrees" / "wt2"),
         "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True, check=True).stdout.strip()
    assert branches == "feature"


def test_worktree_remove_confirm_removes(tmp_path, monkeypatch):
    cfgp, repo = _fixture(tmp_path, monkeypatch)
    assert _run(["worktree", "add", "wt3", "--confirm", "--root", str(repo),
                 "--config", str(cfgp)])[0] == 0
    argv = ["worktree", "remove", "wt3"]
    rc, out = _run([*argv, "--force", "--confirm", "--root", str(repo),
                    "--config", str(cfgp)])
    assert rc == 0, out[:300]
    assert json.loads(out)["removed"] is True
    assert not (repo / ".worktrees" / "wt3").exists()


def test_worktree_switch_confirm_switches_branch(tmp_path, monkeypatch):
    cfgp, repo = _fixture(tmp_path, monkeypatch)
    subprocess.run(["git", "-C", str(repo), "branch", "alt"], check=True)
    argv = ["worktree", "switch", "alt"]
    rc, out = _run([*argv, "--confirm", "--root", str(repo),
                    "--config", str(cfgp)])
    assert rc == 0, out[:300]
    assert json.loads(out)["branch"] == "alt"
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True, check=True).stdout.strip()
    assert head == "alt"


# ---------------------------------------------------------------------------
# Refusal / error paths: never a host write.
# ---------------------------------------------------------------------------
def test_worktree_add_rejects_escaping_name(tmp_path, monkeypatch):
    cfgp, repo = _fixture(tmp_path, monkeypatch)
    argv = ["worktree", "add", "../escape"]
    rc, out = _run([*argv, "--confirm", "--root", str(repo),
                    "--config", str(cfgp)])
    assert rc == 3, out[:200]
    assert json.loads(out)["code"] == "GIT_BAD_INPUT"
    assert not (repo.parent / "escape").exists()


def test_worktree_add_outside_registered_root_is_refused(tmp_path, monkeypatch):
    cfgp, _repo = _fixture(tmp_path, monkeypatch)
    outside = tmp_path / "outside"
    outside.mkdir()
    argv = ["worktree", "add", "wt9"]
    rc, out = _run([*argv, "--confirm", "--root", str(outside),
                    "--config", str(cfgp)])
    assert rc == 3, out[:200]
    assert json.loads(out)["code"] == "GIT_SCOPE_REFUSED"
    assert not (outside / ".worktrees").exists()


def test_worktree_remove_missing_is_typed_error(tmp_path, monkeypatch):
    cfgp, repo = _fixture(tmp_path, monkeypatch)
    argv = ["worktree", "remove", "nope"]
    rc, out = _run([*argv, "--confirm", "--root", str(repo),
                    "--config", str(cfgp)])
    assert rc == 2, out[:200]
    assert json.loads(out)["code"] == "GIT_WORKTREE_REMOVE_FAILED"


def test_worktree_switch_missing_branch_is_typed_error(tmp_path, monkeypatch):
    cfgp, repo = _fixture(tmp_path, monkeypatch)
    argv = ["worktree", "switch", "nope-branch"]
    rc, out = _run([*argv, "--confirm", "--root", str(repo),
                    "--config", str(cfgp)])
    assert rc == 2, out[:200]
    assert json.loads(out)["code"] == "GIT_SWITCH_FAILED"


def test_worktree_git_missing_is_typed_error(tmp_path, monkeypatch):
    cfgp, repo = _fixture(tmp_path, monkeypatch)
    monkeypatch.setenv("PATH", "")
    argv = ["worktree", "add", "wt10"]
    rc, out = _run([*argv, "--confirm", "--root", str(repo),
                    "--config", str(cfgp)])
    assert rc == 2, out[:200]
    assert json.loads(out)["code"] == "GIT_FAILED"


# ---------------------------------------------------------------------------
# Inline argv evidence for the gap-ledger AST scanner.
# ---------------------------------------------------------------------------
def test_inline_wave2_argv_evidence(tmp_path, monkeypatch):
    """Every literal argv list below is indexed by the AST scanner.

    ``route()`` resolves confirmation: a bare pause/worktree call is a typed
    refusal, and the ``--confirm`` form defers to the native handler (``None``
    means the Qubit parser owns it, never the fork).
    """
    assert HF.route(["pause"]).code == "ESTOP_QUBIT_OWNED"
    assert HF.route(["pause", "--confirm"]) is None
    assert HF.route(["worktree", "add", "wt1", "--confirm"]) is None
    assert HF.route(["worktree", "remove", "wt1", "--confirm"]) is None
    assert HF.route(["worktree", "switch", "main", "--confirm"]) is None
    assert HF.route(["worktree", "add", "wt1"]).code == \
        "FORK_SUB_UNCLASSIFIED"
    assert HF.route(["worktree", "remove", "wt1"]).code == \
        "FORK_SUB_UNCLASSIFIED"
    assert HF.route(["worktree", "switch", "main"]).code == \
        "FORK_SUB_UNCLASSIFIED"
    # `list`/`prune` stay fork-forwarded.
    assert HF.route(["worktree", "list"]).kind == "forward"
    assert HF.route(["worktree", "prune", "--dry-run"]).kind == "needs-confirm"
    # The production entrypoint is reachable (exec-set for the AST scanner).
    assert _run(["pause", "--config", str(tmp_path / "missing.yaml")])[0] == 3


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))

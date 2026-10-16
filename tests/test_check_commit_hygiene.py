"""Falsification tests for ``scripts/check_commit_hygiene.py``.

These tests PROVE the guard fires.  Each one builds the violation from scratch
inside a throwaway git repo (``tmp_path``), runs the check, and asserts it
exits non-zero with the expected finding.  One test asserts it stays quiet on a
clean tree, so a check that always fires is also caught.

The guard's whole purpose is catching the specific failure modes this repo has
already suffered - so the tests are written against those incidents, not
against a hypothetical:
  * a dirty tree with no live writer (work that died with its process)
  * work stranded on a branch the main line cannot reach
  * a staged secret
  * a genuinely live writer (must be HANDS-OFF, exit 0, never a violation)
  * a substring-matching command line ('observe' contains 'serve') must not
    be mistaken for a gateway

The writer probe needs a real >=40s window in production, which is far too slow
for a test suite: these tests inject a fake ``sleep`` and back-date mtimes, so
"a real interval elapsed" is simulated without spending the wall time.  The
guard's own gate (``probe_seconds < 40`` raises) is tested directly.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "check_commit_hygiene.py"

sys.path.insert(0, str(REPO_ROOT / "scripts"))
import check_commit_hygiene as hygiene  # noqa: E402


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.update({
        "GIT_AUTHOR_NAME": "hygiene-test",
        "GIT_AUTHOR_EMAIL": "hygiene@test.invalid",
        "GIT_COMMITTER_NAME": "hygiene-test",
        "GIT_COMMITTER_EMAIL": "hygiene@test.invalid",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
    })
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, env=env,
    )


def _init_repo(path: Path, branch: str = "main") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", branch)
    _git(path, "config", "user.name", "hygiene-test")
    _git(path, "config", "user.email", "hygiene@test.invalid")
    (path / "README.md").write_text("base\n", encoding="utf-8")
    _git(path, "add", "README.md")
    _git(path, "commit", "-q", "-m", "base")
    return path


def _run(repo: Path, probe: hygiene.WriterProbe, **kw) -> hygiene.HygieneReport:
    """Run the checks with an injected probe and no cross-worktree scanning."""
    return hygiene.run_checks(
        repo=repo,
        probe=probe,
        include_cross=kw.pop("include_cross", False),
        **kw,
    )


def _no_sleep_probe(**kw) -> hygiene.WriterProbe:
    """A probe whose 'wait' is instantaneous; byte changes still observed."""
    return hygiene.WriterProbe(sleep=lambda _s: None, **kw)


def _age(path: Path, minutes: float) -> None:
    old = time.time() - minutes * 60.0
    os.utime(path, (old, old))


def _checks(report: hygiene.HygieneReport) -> set[str]:
    return {f.check for f in report.findings}


# --------------------------------------------------------------------------
# 0. the check can be quiet  (a guard that always fires is worthless)
# --------------------------------------------------------------------------

def test_clean_tree_passes(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "clean")
    report = _run(repo, _no_sleep_probe())
    assert report.findings == [], [f.format() for f in report.findings]
    assert report.exit_code() == 0


def test_clean_tree_script_exit_zero(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "clean2")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--repo", str(repo), "--no-cross-worktree",
         "--stale-minutes", "0", "--probe-seconds", "40"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "clean" in proc.stdout


# --------------------------------------------------------------------------
# 1. dirty tree with no live writer  -> VIOLATION
# --------------------------------------------------------------------------

def test_dirty_tree_without_writer_fires(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "dirty")
    dirty = repo / "work.py"
    dirty.write_text("print(1)\n", encoding="utf-8")
    _age(dirty, minutes=200)          # older than the stale threshold
    report = _run(repo, _no_sleep_probe(), stale_minutes=30)
    assert report.exit_code() == 1
    assert "dirty_worktree_no_writer" in _checks(report)
    finding = next(f for f in report.findings if f.check == "dirty_worktree_no_writer")
    assert finding.kind == hygiene.VIOLATION
    assert "work.py" in finding.location


def test_dirty_untracked_binary_also_fires(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "dirtybin")
    blob = repo / "artifact.bin"
    blob.write_bytes(b"\x00\x01\x02")
    _age(blob, minutes=90)
    report = _run(repo, _no_sleep_probe(), stale_minutes=30)
    assert report.exit_code() == 1
    assert "dirty_worktree_no_writer" in _checks(report)


def test_fresh_dirty_tree_is_hands_off_not_violation(tmp_path: Path) -> None:
    """A file touched seconds ago is treated as an active writer: exit 0."""
    repo = _init_repo(tmp_path / "fresh")
    (repo / "just_touched.py").write_text("x\n", encoding="utf-8")
    _age(repo / "just_touched.py", minutes=1)
    report = _run(repo, _no_sleep_probe(), stale_minutes=30)
    assert report.exit_code() == 0
    assert "dirty_worktree_no_writer" not in _checks(report)
    assert _checks(report) == {"dirty_worktree_fresh"}


# --------------------------------------------------------------------------
# 2. live writer  -> HANDS-OFF, exit 0 (NOT a violation)
# --------------------------------------------------------------------------

def test_live_writer_is_hands_off_not_violation(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "livewriter")
    target = repo / "stream.py"
    target.write_text("a\n", encoding="utf-8")
    _age(target, minutes=5)

    def writing_during_probe(_seconds: float) -> None:
        # the "writer" saves while the probe's interval elapses
        target.write_text("a\nb\n", encoding="utf-8")

    probe = hygiene.WriterProbe(sleep=writing_during_probe)
    report = _run(repo, probe, stale_minutes=30)

    assert report.exit_code() == 0, [f.format() for f in report.findings]
    assert "dirty_worktree_no_writer" not in _checks(report)
    live = [f for f in report.findings if f.check == "dirty_worktree_live_writer"]
    assert live, [f.format() for f in report.findings]
    assert all(f.kind == hygiene.HANDS_OFF for f in live)


def test_foreign_worktree_dirty_is_reported(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "wtroot")
    other = tmp_path / "wtother"
    res = _git(repo, "worktree", "add", "-q", "-b", "agent/x", str(other))
    assert res.returncode == 0, res.stderr
    (other / "left_behind.py").write_text("uncommitted\n", encoding="utf-8")

    report = hygiene.run_checks(
        repo=repo, probe=_no_sleep_probe(), include_cross=True, stale_minutes=30,
    )
    assert report.exit_code() == 1
    assert "foreign_worktree_dirty" in _checks(report)


def test_foreign_worktree_with_live_agent_is_hands_off(tmp_path: Path, monkeypatch) -> None:
    """A peer's dirty worktree with a process inside it is hands-off, not a violation.

    The guard must not hash another agent's live files; the read-only signal is
    ``/proc/<pid>/cwd``.  We stub that lookup so the test does not depend on a
    real concurrent process existing.
    """
    repo = _init_repo(tmp_path / "wtroot2")
    other = tmp_path / "wtother2"
    res = _git(repo, "worktree", "add", "-q", "-b", "agent/y", str(other))
    assert res.returncode == 0, res.stderr
    (other / "mid_edit.py").write_text("uncommitted\n", encoding="utf-8")

    monkeypatch.setattr(
        hygiene, "processes_with_cwd_under",
        lambda root: [(4242, "python -m pytest")],
    )
    report = hygiene.run_checks(
        repo=repo, probe=_no_sleep_probe(), include_cross=True, stale_minutes=30,
    )
    assert "foreign_worktree_live_agent" in _checks(report)
    live = next(f for f in report.findings if f.check == "foreign_worktree_live_agent")
    assert live.kind == hygiene.HANDS_OFF
    assert "4242" in live.detail
    # The docstring says "hands-off, NOT a violation": a peer's live worktree
    # must not ALSO be counted as a violation, or the guard drives exit 1 for
    # work the rule expressly says belongs to its owner. Reporting the same
    # worktree twice (violation + hands-off) is the cry-wolf failure mode.
    assert "foreign_worktree_dirty" not in _checks(report), [
        f.format() for f in report.findings]
    assert "foreign_worktree_dirty_path" not in _checks(report), [
        f.format() for f in report.findings]
    assert report.exit_code() == 0, [f.format() for f in report.findings]


# --------------------------------------------------------------------------
# 3. stranded commits on a side branch  -> VIOLATION
# --------------------------------------------------------------------------

def test_stranded_branch_commits_fire(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "stranded")
    _git(repo, "switch", "-q", "-c", "agent/stranded")
    (repo / "feature.py").write_text("feature\n", encoding="utf-8")
    _git(repo, "add", "feature.py")
    _git(repo, "commit", "-q", "-m", "work that never landed")

    report = _run(repo, _no_sleep_probe(), stale_minutes=30)
    assert report.exit_code() == 1
    assert "stranded_branch_commits" in _checks(report)
    finding = next(f for f in report.findings if f.check == "stranded_branch_commits")
    assert "1 commit" in finding.detail or "1 commit(s)" in finding.detail


def test_clean_side_branch_without_commits_stays_quiet(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "nocommits")
    _git(repo, "switch", "-q", "-c", "agent/empty")
    report = _run(repo, _no_sleep_probe(), stale_minutes=30)
    assert report.exit_code() == 0, [f.format() for f in report.findings]


def test_stale_trunk_prefers_freshest_remote(tmp_path: Path) -> None:
    """A stale local `main` must not be used as the trunk when a newer remote exists.

    This is the real false positive in this repo: local `main` is months old and
    the true trunk is a remote-tracking branch.  Comparing against the stale one
    reports thousands of bogus stranded commits.
    """
    repo = _init_repo(tmp_path / "freshtrunk")
    # Simulate a remote whose main is newer than the local, stale one.
    remote = tmp_path / "freshtrunk-remote"
    _git(repo, "clone", "-q", str(repo), str(remote))
    _git(repo, "remote", "add", "upstream", str(remote))
    _git(repo, "fetch", "-q", "upstream")
    # advance upstream/main beyond the local main
    (remote / "extra.py").write_text("x\n", encoding="utf-8")
    _git(remote, "add", "extra.py")
    _git(remote, "commit", "-q", "-m", "remote moves ahead")
    _git(repo, "fetch", "-q", "upstream")

    trunk, source = hygiene.resolve_trunk(repo, None)
    assert trunk == "upstream/main", (trunk, source)
    assert "remote-tracking" in source


def test_behind_trunk_is_warning_not_violation(tmp_path: Path) -> None:
    """A branch merely behind the trunk has stranded nothing: WARNING, exit 0."""
    repo = _init_repo(tmp_path / "behindonly")
    _git(repo, "checkout", "-q", "-b", "trunkline")
    (repo / "advance.py").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "advance.py")
    _git(repo, "commit", "-q", "-m", "trunk moves ahead")
    _git(repo, "checkout", "-q", "main")   # back to the older tip

    report = _run(repo, _no_sleep_probe(), stale_minutes=30, trunk_override="trunkline")
    assert report.exit_code() == 0, [f.format() for f in report.findings]
    assert "stranded_branch_commits" not in _checks(report)
    assert "branch_behind_trunk" in _checks(report)


def test_detached_head_with_work_fires(tmp_path: Path) -> None:
    """Commits made while detached live on no branch at all."""
    repo = _init_repo(tmp_path / "detached")
    _git(repo, "checkout", "-q", "--detach", "HEAD")
    (repo / "offbranch.py").write_text("offbranch\n", encoding="utf-8")
    _git(repo, "add", "offbranch.py")
    _git(repo, "commit", "-q", "-m", "committed while detached")

    report = _run(repo, _no_sleep_probe(), stale_minutes=30)
    assert report.exit_code() == 1, [f.format() for f in report.findings]
    assert "stranded_branch_commits" in _checks(report)
    assert "detached_head_work" in _checks(report)


# --------------------------------------------------------------------------
# 4. staged secrets  -> VIOLATION
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name", [".env", ".env.production", "credentials.json", "id_rsa", "srv.key"])
def test_staged_secret_fires(tmp_path: Path, name: str) -> None:
    repo = _init_repo(tmp_path / f"secret-{name.replace('.', '_')}")
    secret = repo / name
    secret.write_text("TOKEN=abc\n", encoding="utf-8")
    _git(repo, "add", "-f", name)

    report = _run(repo, _no_sleep_probe(), stale_minutes=30)
    assert report.exit_code() == 1, [f.format() for f in report.findings]
    assert "staged_secret" in _checks(report)


def test_env_template_is_not_a_secret(tmp_path: Path) -> None:
    """`.env.example` is a template; the guard must not cry wolf."""
    repo = _init_repo(tmp_path / "template")
    (repo / ".env.example").write_text("TOKEN=\n", encoding="utf-8")
    _git(repo, "add", ".env.example")
    report = _run(repo, _no_sleep_probe(), stale_minutes=30)
    assert "staged_secret" not in _checks(report)


def test_staged_private_key_content_fires(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "keycontent")
    key = repo / "deploy_material.txt"
    key.write_text(
        "-----BEGIN OPENSSH PRIVATE KEY-----\nnot-a-real-key\n"
        "-----END OPENSSH PRIVATE KEY-----\n",
        encoding="utf-8",
    )
    _git(repo, "add", "deploy_material.txt")
    report = _run(repo, _no_sleep_probe(), stale_minutes=30)
    assert report.exit_code() == 1
    assert "staged_secret_content" in _checks(report)


def test_unstaged_secret_is_not_reported_as_staged(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "unstaged")
    (repo / ".env").write_text("TOKEN=abc\n", encoding="utf-8")
    _age(repo / ".env", minutes=120)
    report = _run(repo, _no_sleep_probe(), stale_minutes=30)
    assert "staged_secret" not in _checks(report)
    # but the dirty tree itself is a violation
    assert report.exit_code() == 1


# --------------------------------------------------------------------------
# 5. false-positive traps PROVEN in this repo
# --------------------------------------------------------------------------

def test_only_the_canonical_matcher_is_used_for_process_liveness() -> None:
    """No argv substring logic of our own: concepts like 'serve' must not count.

    The guard must expose exactly the canonical matcher and never scan argv
    itself.  If the canonical module is importable (it is, in this repo), the
    matcher must reject 'observe'/'preserve' - the documented false positive.
    """
    matcher = hygiene.canonical_gateway_matcher()
    if matcher is None:
        pytest.skip("canonical matcher not importable in this environment")
    assert matcher is not None  # narrows for the type checker
    # The canonical matcher keys on a real entrypoint basename + `gateway` token.
    assert matcher("qubit gateway run") is True
    assert matcher("/usr/bin/voicebridge gateway run --profile default") is True
    assert matcher("qubit gateway observe") is False
    assert matcher("some preserve daemon") is False
    assert matcher("serve --port 9119") is False
    assert matcher("python -m http.server 9119") is False


def test_probe_rejects_short_window() -> None:
    """A sub-40s window cannot outrun a save; the guard refuses to pretend."""
    with pytest.raises(ValueError):
        hygiene.WriterProbe(probe_seconds=5)


def test_changed_bytes_with_stale_mtime_is_not_a_live_writer(tmp_path: Path) -> None:
    """A commit landing during the window changes bytes too - not proof of a writer."""
    repo = _init_repo(tmp_path / "committedmidprobe")
    target = repo / "tracked.py"
    target.write_text("v1\n", encoding="utf-8")
    _git(repo, "add", "tracked.py")
    _git(repo, "commit", "-q", "-m", "v1")
    _age(target, minutes=90)

    def commit_during_probe(_seconds: float) -> None:
        target.write_text("v2\n", encoding="utf-8")
        _git(repo, "add", "tracked.py")
        _git(repo, "commit", "-q", "-m", "v2")
        _age(target, minutes=90)      # the working file is not being written now

    probe = hygiene.WriterProbe(sleep=commit_during_probe)
    report = _run(repo, probe, stale_minutes=30)
    assert "dirty_worktree_live_writer" not in _checks(report)


# --------------------------------------------------------------------------
# 6. output contract
# --------------------------------------------------------------------------

def test_json_output_is_machine_readable(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "jsonmode")
    (repo / "dirty.py").write_text("x\n", encoding="utf-8")
    _age(repo / "dirty.py", minutes=200)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--repo", str(repo), "--json",
         "--no-cross-worktree", "--stale-minutes", "30", "--probe-seconds", "40"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["violations"] >= 1
    assert any(f["check"] == "dirty_worktree_no_writer" for f in payload["findings"])
    finding = next(f for f in payload["findings"] if f["check"] == "dirty_worktree_no_writer")
    assert "dirty.py" in finding["line"]


def test_markdown_report_renders_both_sections(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "md")
    (repo / "dirty.py").write_text("x\n", encoding="utf-8")
    _age(repo / "dirty.py", minutes=200)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--repo", str(repo), "--no-cross-worktree",
         "--stale-minutes", "30", "--probe-seconds", "40"],
        capture_output=True, text=True,
    )
    report = _run(repo, _no_sleep_probe(), stale_minutes=30)
    md = hygiene.render_markdown(report)
    assert "## Violations" in md
    assert "## Hands off" in md
    assert "dirty.py" in md


def test_exit_code_two_when_not_a_repo(tmp_path: Path) -> None:
    not_a_repo = tmp_path / "notrepo"
    not_a_repo.mkdir()
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--repo", str(not_a_repo)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 2, proc.stdout + proc.stderr

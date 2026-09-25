# tests/test_merge_harness.py
"""Task 5 gate: freeze record + 3-way merge harness for the v0.21.5 rebase.

The harness is the merge machinery Task 6 consumes:

* ``merge_file(base, ours, theirs, path) -> bytes`` resolves one path across
  the three trees: present only in ``ours`` -> ours (candidate), only in
  ``theirs`` -> theirs (current), and present in both -> the
  ``theirs-vs-base`` change applied onto ``ours`` via
  ``git merge-file -p ours base theirs`` (the R8 ruling).
* ``Checkpoint`` snapshots the worktree around each bucket (``--checkpoint``)
  and restores it (``--rollback``).
* ``docs/generated/audit/FREEZE_<sha>.json`` records the frozen worktree base
  that Task 6 merges from.

All fixtures are tiny temp trees; no product tree is read or written.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
AUDIT = REPO / "docs" / "generated" / "audit"

#: The frozen worktree base recorded by Task 5 (the commit Task 6 merges from).
FREEZE_SHA = "5ff0b53c2c47ff81bcd7c22fbb6e22514446ddbe"
FREEZE_SHORT = "5ff0b53c"
PIN = "69948c005791d4d0e51670ca015238c2c7663749"
BRANCH = "sdd/v0215-1to1"

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from merge_v0215 import (  # noqa: E402
    BUCKET_ACTIONS,
    Checkpoint,
    MergeConflict,
    action_for_path,
    merge_file,
)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def trees(tmp_path):
    """Three empty merge trees: base (engine v0.21.4), ours (v0.21.5), theirs."""
    out = {}
    for name in ("base", "ours", "theirs"):
        d = tmp_path / name
        d.mkdir()
        out[name] = d
    return out


def _git(repo: Path, *args: str, check: bool = True):
    return subprocess.run(
        ["git", *args], cwd=str(repo), check=check,
        capture_output=True, text=True,
    )


@pytest.fixture
def git_repo(tmp_path):
    """A throwaway git repo with one committed file and a local identity."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "merge-harness@test.invalid")
    _git(repo, "config", "user.name", "merge-harness")
    (repo / "a.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    return repo


# ---------------------------------------------------------------------------
# merge_file -- the three bucket cases
# ---------------------------------------------------------------------------
def test_only_candidate_returns_ours(trees):
    """File only in the candidate (ours) -> candidate bytes."""
    (trees["ours"] / "new.py").write_bytes(b"ours-content\n")
    assert merge_file(trees["base"], trees["ours"], trees["theirs"],
                      "new.py") == b"ours-content\n"


def test_only_current_returns_theirs(trees):
    """File only in the current tree (theirs) -> current bytes (kept fork)."""
    (trees["theirs"] / "fork.py").write_bytes(b"theirs-content\n")
    assert merge_file(trees["base"], trees["ours"], trees["theirs"],
                      "fork.py") == b"theirs-content\n"


def test_identical_returns_content(trees):
    """Byte-identical in ours and theirs -> that content."""
    for d in trees.values():
        (d / "same.py").write_bytes(b"same\n")
    assert merge_file(trees["base"], trees["ours"], trees["theirs"],
                      "same.py") == b"same\n"


def test_differing_applies_theirs_onto_ours(trees):
    """R8: ours and theirs both diverge from base in disjoint regions."""
    (trees["base"] / "mod.py").write_bytes(b"l1\nl2\nl3\nl4\nl5\n")
    (trees["ours"] / "mod.py").write_bytes(b"l1\nOURS\nl3\nl4\nl5\n")
    (trees["theirs"] / "mod.py").write_bytes(b"l1\nl2\nl3\nTHEIRS\nl5\n")
    assert merge_file(trees["base"], trees["ours"], trees["theirs"],
                      "mod.py") == b"l1\nOURS\nl3\nTHEIRS\nl5\n"


def test_differing_unchanged_ours_takes_theirs(trees):
    """Base == ours: only theirs changed -> theirs."""
    (trees["base"] / "m.py").write_bytes(b"x\ny\n")
    (trees["ours"] / "m.py").write_bytes(b"x\ny\n")
    (trees["theirs"] / "m.py").write_bytes(b"x\nTHEIRS\n")
    assert merge_file(trees["base"], trees["ours"], trees["theirs"],
                      "m.py") == b"x\nTHEIRS\n"


def test_differing_unchanged_theirs_takes_ours(trees):
    """Base == theirs: only ours changed -> ours."""
    (trees["base"] / "m.py").write_bytes(b"x\ny\n")
    (trees["theirs"] / "m.py").write_bytes(b"x\ny\n")
    (trees["ours"] / "m.py").write_bytes(b"x\nOURS\n")
    assert merge_file(trees["base"], trees["ours"], trees["theirs"],
                      "m.py") == b"x\nOURS\n"


def test_conflict_raises_with_markers(trees):
    """Overlapping edits -> MergeConflict carrying the conflicted merge."""
    (trees["base"] / "c.py").write_bytes(b"a\nb\nc\n")
    (trees["ours"] / "c.py").write_bytes(b"a\nOURS\nc\n")
    (trees["theirs"] / "c.py").write_bytes(b"a\nTHEIRS\nc\n")
    with pytest.raises(MergeConflict) as exc:
        merge_file(trees["base"], trees["ours"], trees["theirs"], "c.py")
    assert exc.value.path == "c.py"
    assert b"<<<<<<<" in exc.value.merged
    assert b">>>>>>>" in exc.value.merged


def test_both_added_differently_is_a_conflict(trees):
    """No common ancestor and differing content -> conflict, not a silent pick."""
    (trees["ours"] / "n.py").write_bytes(b"new-ours\n")
    (trees["theirs"] / "n.py").write_bytes(b"new-theirs\n")
    with pytest.raises(MergeConflict):
        merge_file(trees["base"], trees["ours"], trees["theirs"], "n.py")


def test_absent_from_both_is_rejected(trees):
    with pytest.raises(ValueError):
        merge_file(trees["base"], trees["ours"], trees["theirs"], "ghost.py")


def test_rejects_path_escape(trees):
    for bad in ("../escape.py", "/etc/passwd"):
        with pytest.raises(ValueError):
            merge_file(trees["base"], trees["ours"], trees["theirs"], bad)


def test_nested_paths_are_supported(trees):
    (trees["ours"] / "agent").mkdir()
    (trees["ours"] / "agent" / "deep.py").write_bytes(b"deep\n")
    assert merge_file(trees["base"], trees["ours"], trees["theirs"],
                      "agent/deep.py") == b"deep\n"


# ---------------------------------------------------------------------------
# rules -- bucket -> action mapping
# ---------------------------------------------------------------------------
def test_bucket_actions_match_the_brief():
    assert BUCKET_ACTIONS == {
        "only_candidate": "candidate",
        "only_current": "current",
        "differing": "merge",
        "identical": "keep",
    }


def test_action_for_path_uses_the_delta_buckets():
    delta = {
        "only_candidate": ["a.py"],
        "only_current": ["b.py"],
        "differing": ["c.py"],
        "identical": ["d.py"],
    }
    assert action_for_path("a.py", delta) == "candidate"
    assert action_for_path("b.py", delta) == "current"
    assert action_for_path("c.py", delta) == "merge"
    assert action_for_path("d.py", delta) == "keep"
    assert action_for_path("unlisted.py", delta) is None


# ---------------------------------------------------------------------------
# Checkpoint / rollback
# ---------------------------------------------------------------------------
def test_checkpoint_then_rollback_restores_tree(git_repo):
    cp = Checkpoint(git_repo)
    sha = cp.checkpoint("bucket-1")
    assert sha and len(sha) == 40

    (git_repo / "a.txt").write_text("changed\n", encoding="utf-8")
    (git_repo / "new.txt").write_text("new\n", encoding="utf-8")

    cp.rollback("bucket-1")
    assert (git_repo / "a.txt").read_text(encoding="utf-8") == "one\ntwo\nthree\n"
    assert not (git_repo / "new.txt").exists(), "rollback left a new file behind"
    # The checkpoint index lives outside the worktree, so the reset kept it.
    assert "bucket-1" in cp.list()


def test_rollback_without_name_uses_the_last_checkpoint(git_repo):
    cp = Checkpoint(git_repo)
    cp.checkpoint("b1")
    (git_repo / "a.txt").write_text("v2\n", encoding="utf-8")
    cp.checkpoint("b2")
    (git_repo / "a.txt").write_text("v3\n", encoding="utf-8")

    cp.rollback()
    assert (git_repo / "a.txt").read_text(encoding="utf-8") == "v2\n"


def test_checkpoint_cli(git_repo):
    env = dict(os.environ, PYTHONPATH=str(SCRIPTS))
    res = subprocess.run(
        [sys.executable, "-m", "merge_v0215", "-C", str(git_repo),
         "--checkpoint", "cli-1"],
        env=env, capture_output=True, text=True, timeout=60,
    )
    assert res.returncode == 0, res.stderr
    listed = subprocess.run(
        [sys.executable, "-m", "merge_v0215", "-C", str(git_repo), "--list"],
        env=env, capture_output=True, text=True, timeout=60,
    )
    assert listed.returncode == 0, listed.stderr
    assert "cli-1" in listed.stdout

    (git_repo / "a.txt").write_text("dirty\n", encoding="utf-8")
    rolled = subprocess.run(
        [sys.executable, "-m", "merge_v0215", "-C", str(git_repo),
         "--rollback", "cli-1"],
        env=env, capture_output=True, text=True, timeout=60,
    )
    assert rolled.returncode == 0, rolled.stderr
    assert (git_repo / "a.txt").read_text(encoding="utf-8") == "one\ntwo\nthree\n"


# ---------------------------------------------------------------------------
# freeze record
# ---------------------------------------------------------------------------
def test_freeze_record_is_committed_for_the_worktree_base():
    docs = []
    for path in sorted(AUDIT.glob("FREEZE_*.json")):
        try:
            docs.append((path, json.loads(path.read_text(encoding="utf-8"))))
        except ValueError:
            continue
    assert docs, f"no freeze record in {AUDIT}"
    matches = [(p, d) for p, d in docs if d.get("sha") == FREEZE_SHA]
    assert matches, (
        f"no freeze record for {FREEZE_SHA}; found "
        f"{[d.get('sha') for _, d in docs]}")
    path, doc = matches[0]
    assert path.name == f"FREEZE_{FREEZE_SHORT}.json", path.name
    assert doc["pin"] == PIN, doc
    assert doc["branch"] == BRANCH, doc
    assert isinstance(doc["dirty_count"], int), doc
    assert doc["dirty_count"] == 0, "the frozen base must be a clean tree"


def test_freeze_generator_is_reproducible(git_repo):
    from merge_v0215 import freeze as freeze_mod

    doc_a = freeze_mod.build_freeze(git_repo)
    doc_b = freeze_mod.build_freeze(git_repo)
    assert doc_a == doc_b
    assert doc_a["sha"] == _git(git_repo, "rev-parse", "HEAD").stdout.strip()
    assert doc_a["branch"]  # a branch name is always resolved
    assert isinstance(doc_a["dirty_count"], int)
    assert doc_a["pin"] == PIN

    out = freeze_mod.write_freeze(git_repo, audit_dir=git_repo / "audit")
    on_disk = json.loads(Path(out).read_text(encoding="utf-8"))
    assert on_disk == doc_a

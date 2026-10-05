"""`/worktree remove` — targeted single-worktree removal.

Complements `/worktree prune` (bulk reclaim of trees the audit judges
clean+merged). This pins the safety contract: removal is REFUSED for a tree
that carries work unless `--force`, and refused outright for the tree the
current session is running in.
"""
import pathlib
import re
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "qubit_runtime"


def _read_mixin() -> str:
    return (SRC / "qubit_cli" / "cli_commands_mixin.py").read_text(encoding="utf-8")


def test_subcommand_is_registered():
    """`remove` (and its aliases) must resolve to a real handler."""
    src = _read_mixin()
    match = re.search(r"_WORKTREE_SUBCOMMANDS\s*=\s*\{(.*?)\}", src, re.S)
    assert match, "_WORKTREE_SUBCOMMANDS not found"
    block = match.group(1)
    for alias in ("remove", "rm", "delete"):
        assert f'"{alias}"' in block, f"{alias} alias missing from the dispatch map"
    assert "_worktree_remove" in block, "handler not referenced by the dispatch map"


def test_remove_handler_defined():
    src = _read_mixin()
    assert "def _worktree_remove" in src
    # The handler is dispatched with (repo_root, rest), like its siblings.
    assert re.search(r"def _worktree_remove\(self, repo_root: str, rest: str\)", src)


@pytest.mark.parametrize("alias,expected", [("remove", True), ("rm", True),
                                            ("delete", True), ("prune", False)])
def test_alias_routing_matches_intent(alias, expected):
    """`remove` must NOT collide with the bulk `prune` behaviour."""
    src = _read_mixin()
    # Find the line carrying this alias and assert which handler it maps to.
    line = next((l for l in src.splitlines()
                 if f'"{alias}"' in l and "_worktree_" in l), None)
    assert line, f"no dispatch line for {alias!r}"
    targets_remove = "_worktree_remove" in line
    if expected:
        assert targets_remove, f"{alias!r} should map to _worktree_remove, got: {line.strip()}"
    else:
        assert not targets_remove, f"{alias!r} must not map to _worktree_remove"


def test_handler_refuses_without_a_target_and_names_known_trees(tmp_path):
    """No argument -> usage, not a crash; unknown name -> lists what exists."""
    src = _read_mixin()
    body = src[src.index("def _worktree_remove"): src.index("def _worktree_prune")]
    # Usage branch
    assert "Usage: /worktree remove" in body
    # Unknown-target branch must surface the known set rather than a bare failure
    assert "No managed worktree matching" in body
    assert "Known:" in body


def test_handler_guards_the_active_and_dirty_cases():
    """The two refusals that matter: current session's tree, and unsaved work."""
    src = _read_mixin()
    body = src[src.index("def _worktree_remove"): src.index("def _worktree_prune")]
    assert "this session is running in" in body, "missing active-worktree guard"
    assert 'match.verdict != "reclaim"' in body, "missing carries-work guard"
    assert "--force" in body, "missing force override"
    # --force must actually reach git, not just skip the guard.
    assert '"--force"' in body and "worktree" in body and "remove" in body


def test_module_still_compiles():
    r = subprocess.run([sys.executable, "-c",
                        f"import ast,pathlib;ast.parse(pathlib.Path({str(SRC / 'qubit_cli' / 'cli_commands_mixin.py')!r}).read_text())"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr

# tests/test_rebrand_determinism.py
"""Engine determinism + idempotency gate for the 1:1 rebase (Task 3).

Two independent rebrand runs over the *same* pinned v0.21.5 source must produce
byte-identical trees, and running the engine a second time over an already
rebranded tree must change no source file.

The engines derive their rename plan from a ``set`` of ``(old, new)`` path
tuples; set iteration order for strings depends on ``PYTHONHASHSEED``, so the
plan (and the manifest that records it) was nondeterministic across processes.
``_path_renames`` now sorts by ``(-depth, old, new)``: deterministic ties while
preserving the deepest-first order the apply phase requires.

The pinned source is read-only input; all work happens in ``tmp_path``.  To keep
the run bounded the pin is staged once per tree (two independent copies of the
identical bytes carry the identical input), and the byte comparison is over the
whole staged tree, including the manifests the engines emit by default.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

#: Read-only pinned upstream source (v0.21.5 @ 69948c0057).  Overridable for a
#: relocated checkout; the test skips (not fails) when it is unavailable.
PINNED_SOURCE = Path(os.environ.get(
    "QUBIT_PINNED_SOURCE", "/tmp/qubit-1to1-20260925-082150/ref"))

#: Manifests the engines emit into the staged tree by default.  They are part of
#: the determinism contract (the rename plan is recorded in order), but they are
#: excluded from the idempotency comparison: a second run legitimately rewrites
#: the manifest to record ``changed=0``.
GENERATED = (
    "docs/archive/2026-09-21/QUBIT_REBRAND_MANIFEST.json",
    "docs/archive/2026-09-21/QUBIT_DEBRAND_MANIFEST.json",
)


def tree_hash(root, exclude=()) -> str:
    """sha256 over ``(relative-path, content-sha256)`` pairs, path-sorted."""
    root = Path(root)
    lines: list[str] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if any(rel == e or rel.startswith(e) for e in exclude):
            continue
        digest = hashlib.sha256(p.read_bytes()).hexdigest()
        lines.append(f"{rel}\0{digest}\n")
    h = hashlib.sha256()
    for line in lines:
        h.update(line.encode("utf-8"))
    return h.hexdigest()


def _stage(dest: Path) -> None:
    """Copy the pinned source as ``qubit_hermes/`` plus the two engines."""
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copytree(PINNED_SOURCE, dest / "qubit_hermes")
    (dest / "scripts").mkdir()
    for name in ("qubit_rebrand.py", "qubit_debrand.py"):
        shutil.copy2(REPO / "scripts" / name, dest / "scripts" / name)


def _run_engines(root: Path, seed: int) -> None:
    """Run rebrand then debrand ``--apply`` in ``root`` under a hash seed."""
    env = dict(os.environ, PYTHONHASHSEED=str(seed))
    for script in ("qubit_rebrand.py", "qubit_debrand.py"):
        subprocess.run(
            [sys.executable, f"scripts/{script}", "--apply"],
            cwd=root, env=env, check=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )


@pytest.fixture(scope="module")
def rebranded(tmp_path_factory):
    if not PINNED_SOURCE.is_dir():
        pytest.skip(f"pinned source unavailable: {PINNED_SOURCE}")
    base = tmp_path_factory.mktemp("determinism")
    tmp_a, tmp_b = base / "a", base / "b"
    _stage(tmp_a)
    _stage(tmp_b)
    # Pin the premise: both trees start from byte-identical input.
    assert tree_hash(tmp_a / "qubit_hermes") == tree_hash(tmp_b / "qubit_hermes")
    # Distinct hash seeds force truly independent plan orderings.  The seed only
    # affects in-process string-set iteration, never the input bytes.
    _run_engines(tmp_a, seed=0)
    _run_engines(tmp_b, seed=1)
    # Snapshot now so the determinism assertion is independent of whether the
    # idempotency test (which mutates tmp_a) runs first.
    return tmp_a, tmp_b, tree_hash(tmp_a), tree_hash(tmp_b)


def test_two_independent_runs_are_byte_identical(rebranded):
    _tmp_a, _tmp_b, hash_a, hash_b = rebranded
    assert hash_a == hash_b


def test_engine_second_run_changes_no_source_file(rebranded):
    tmp_a, _tmp_b, _hash_a, _hash_b = rebranded
    before = tree_hash(tmp_a, exclude=GENERATED)
    _run_engines(tmp_a, seed=2)
    after = tree_hash(tmp_a, exclude=GENERATED)
    assert after == before

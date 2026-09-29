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

The gate is non-vacuous: it asserts the pin is the expected upstream revision
and that the transform actually changed files and renamed paths before it
asserts the two runs agree.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

#: Expected upstream revision of the pinned source (v0.21.5 @ 69948c0057).
EXPECTED_REVISION = "69948c005791d4d0e51670ca015238c2c7663749"

def _first_existing(*candidates: str) -> Path:
    """First candidate path that exists, else the first (for the error text)."""
    paths = [Path(c) for c in candidates]
    for path in paths:
        if path.is_dir():
            return path
    return paths[0]


#: Read-only pinned upstream source.  Overridable for a relocated checkout.
#: The durable restoration lives under ``.cache/qubit-pins``; the ephemeral
#: ``/tmp`` path is retained only as a legacy fallback.
PINNED_SOURCE = Path(os.environ["QUBIT_PINNED_SOURCE"]) if os.environ.get(
    "QUBIT_PINNED_SOURCE") else _first_existing(
        "/home/trick/.cache/qubit-pins/hermes-agent",
        "/tmp/qubit-1to1-20260925-082150/ref",
        "/tmp/opencode/pinned/hermes-agent")

#: Read-only source of record for the pin (a git checkout at EXPECTED_REVISION).
REFERENCE_HOME = Path(os.environ["QUBIT_REFERENCE_HOME"]) if os.environ.get(
    "QUBIT_REFERENCE_HOME") else _first_existing(
        "/home/trick/.cache/qubit-pins/hermes-agent",
        "/home/trick/.hermes/hermes-agent")

#: Representative pin files checked blob-for-blob against EXPECTED_REVISION.
REVISION_SAMPLE = ("pyproject.toml", "cli.py", "agent/__init__.py", "README.md")

#: Manifests the engines emit into the staged tree by default.  They are part of
#: the determinism contract (the rename plan is recorded in order), but they are
#: excluded from the idempotency comparison: a second run legitimately rewrites
#: the manifest to record ``changed=0``.
GENERATED = (
    "docs/archive/2026-09-21/QUBIT_REBRAND_MANIFEST.json",
    "docs/archive/2026-09-21/QUBIT_DEBRAND_MANIFEST.json",
)
REBRAND_MANIFEST = GENERATED[0]


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


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True,
    ).stdout.strip()


def _assert_pinned_revision() -> None:
    """Hard-fail unless the pin is the expected upstream revision.

    The pin directory is not a git checkout, so the read-only reference install
    (the source of record, at ``EXPECTED_REVISION``) is used to check a sample
    of the pin's blobs.  A missing or unknown pin fails the gate rather than
    skipping it: a skip would let this gate pass without exercising anything.
    """
    if not PINNED_SOURCE.is_dir():
        pytest.fail(f"pinned source unavailable: {PINNED_SOURCE}")
    if not REFERENCE_HOME.is_dir():
        pytest.fail("cannot verify pin revision: reference install unavailable "
                    f"at {REFERENCE_HOME} (set QUBIT_REFERENCE_HOME)")
    head = _git("-C", str(REFERENCE_HOME), "rev-parse", "HEAD")
    assert head == EXPECTED_REVISION, (
        f"reference revision is {head}, expected {EXPECTED_REVISION}")
    for rel in REVISION_SAMPLE:
        pin_blob = _git("hash-object", str(PINNED_SOURCE / rel))
        ref_blob = _git("-C", str(REFERENCE_HOME), "rev-parse", f"HEAD:{rel}")
        assert pin_blob == ref_blob, (
            f"pinned {rel} does not match {EXPECTED_REVISION[:10]} "
            f"(pin={pin_blob} ref={ref_blob})")


def _stage(dest: Path) -> None:
    """Copy the pinned source as ``qubit_hermes/`` plus the two engines.

    A git checkout pin (the durable ``.cache/qubit-pins`` restoration) carries a
    ~1 GB ``.git`` directory that is irrelevant to the engine run and must not be
    copied twice; only the working tree is staged.
    """
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copytree(PINNED_SOURCE, dest / "qubit_hermes",
                    ignore=shutil.ignore_patterns(".git"))
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
    _assert_pinned_revision()
    base = tmp_path_factory.mktemp("determinism")
    tmp_a, tmp_b = base / "a", base / "b"
    _stage(tmp_a)
    _stage(tmp_b)
    # Pin the premise: both trees start from byte-identical input.
    input_hash = tree_hash(tmp_a / "qubit_hermes")
    assert input_hash == tree_hash(tmp_b / "qubit_hermes")
    # Distinct hash seeds force truly independent plan orderings.  The seed only
    # affects in-process string-set iteration, never the input bytes.
    _run_engines(tmp_a, seed=0)
    _run_engines(tmp_b, seed=1)
    # Snapshot now so the determinism assertion is independent of whether the
    # idempotency test (which mutates tmp_a) runs first.
    results = {}
    for name, root in (("a", tmp_a), ("b", tmp_b)):
        manifest = json.loads((root / REBRAND_MANIFEST).read_text(encoding="utf-8"))
        results[name] = {
            "root": root,
            "hash": tree_hash(root),
            "input_hash": input_hash,
            "content_files_changed": manifest["content_files_changed"],
            "path_renames": len(manifest["path_renames"]),
        }
    return results


def test_transform_did_real_work(rebranded):
    """Non-vacuity: the gate must not pass when the transform is a no-op."""
    for name in ("a", "b"):
        r = rebranded[name]
        assert r["path_renames"] > 0, f"run {name}: no paths renamed"
        assert r["content_files_changed"] > 0, f"run {name}: no files changed"
        assert r["hash"] != r["input_hash"], f"run {name}: tree unchanged"


def test_two_independent_runs_are_byte_identical(rebranded):
    assert rebranded["a"]["hash"] == rebranded["b"]["hash"]


def test_engine_second_run_changes_no_source_file(rebranded):
    tmp_a = rebranded["a"]["root"]
    before = tree_hash(tmp_a, exclude=GENERATED)
    _run_engines(tmp_a, seed=2)
    after = tree_hash(tmp_a, exclude=GENERATED)
    assert after == before

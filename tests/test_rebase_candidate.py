# tests/test_rebase_candidate.py
"""Candidate-generation gate for the v0.21.5 1:1 rebase (Task 4).

``scripts/rebase_v0215.sh`` must stage the pinned upstream (v0.21.5 @
``69948c0057``), run the two deterministic engines (``qubit_rebrand.py`` +
``qubit_debrand.py``), emit a delta manifest with the four bucket keys, and
never write the live product tree.

The candidate is generated under a temp root (``QUBIT_CANDIDATE_ROOT``); the
read-only pinned source is input only.  Set ``QUBIT_TEST_CANDIDATE_ROOT`` to a
persistent path to reuse a previously generated candidate while iterating.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "rebase_v0215.sh"

#: Required delta buckets (the Task 4 interface).
DELTA_KEYS = ("identical", "differing", "only_candidate", "only_current")

#: The new upstream module the extension engine maps (Task 1): its presence in
#: the candidate is the non-vacuity anchor for "the pin really is v0.21.5".
NEW_MODULE = "qubit_state_health.py"

EXPECTED_REVISION = "69948c005791d4d0e51670ca015238c2c7663749"

PINNED_SOURCE = Path(os.environ.get(
    "QUBIT_PINNED_SOURCE", "/tmp/qubit-1to1-20260925-082150/ref"))
REFERENCE_HOME = Path(os.environ.get(
    "QUBIT_REFERENCE_HOME", "/home/trick/.hermes/hermes-agent"))


def _tree_hash(root: Path, exclude=()) -> str:
    """sha256 over ``(relative-path, content-sha256)`` pairs, path-sorted."""
    lines: list[str] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if "__pycache__" in rel or any(rel == e for e in exclude):
            continue
        digest = hashlib.sha256(p.read_bytes()).hexdigest()
        lines.append(f"{rel}\0{digest}\n")
    h = hashlib.sha256()
    for line in lines:
        h.update(line.encode("utf-8"))
    return h.hexdigest()


def _run_check(candidate_root: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["QUBIT_CANDIDATE_ROOT"] = str(candidate_root)
    env["QUBIT_DELTA_OUT"] = str(candidate_root / "V0_21_5_DELTA.json")
    return subprocess.run(
        ["bash", str(SCRIPT), "--check"],
        cwd=str(REPO), env=env, capture_output=True, text=True, timeout=1800,
    )


@pytest.fixture(scope="module")
def candidate(tmp_path_factory):
    if not SCRIPT.is_file():
        pytest.fail(f"missing {SCRIPT}")
    # A persistent override lets an implementer reuse a built candidate.
    override = os.environ.get("QUBIT_TEST_CANDIDATE_ROOT")
    root = Path(override) if override else (tmp_path_factory.mktemp("cand") / "v0215")
    res = _run_check(root)
    return {"root": root, "res": res}


def test_check_exits_zero(candidate):
    res = candidate["res"]
    assert res.returncode == 0, (
        f"--check failed rc={res.returncode}\nSTDOUT:\n{res.stdout}\n"
        f"STDERR:\n{res.stderr}")


def test_candidate_has_new_upstream_module(candidate):
    module = candidate["root"] / "qubit_runtime" / NEW_MODULE
    assert module.is_file(), f"missing candidate module {module}"


def test_candidate_imports_resolve(candidate):
    pkg = candidate["root"] / "qubit_runtime"
    home = candidate["root"] / "_throwaway_home"
    home.mkdir(exist_ok=True)
    code = (
        "import sys;"
        f"sys.path.insert(0, {str(pkg)!r});"
        "import qubit_state_health, qubit_yaml;"
        "print('IMPORT_OK')"
    )
    # Throwaway HOME/QUBIT_HOME: importing the vendored runtime must never
    # create or read the operator's real home.  ``-B`` keeps __pycache__ out of
    # the candidate tree so it stays byte-reproducible.
    env = {
        "HOME": str(home),
        "QUBIT_HOME": str(home / "qh"),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    res = subprocess.run(
        [sys.executable, "-B", "-c", code],
        cwd=str(candidate["root"]), env=env, capture_output=True, text=True,
        timeout=300,
    )
    assert res.returncode == 0, f"import smoke failed:\n{res.stderr}"
    assert "IMPORT_OK" in res.stdout
    assert not (Path(env["HOME"]) / ".hermes").exists(), (
        "candidate import created the read-only reference home")


def test_delta_manifest_shape(candidate):
    delta_path = candidate["root"] / "V0_21_5_DELTA.json"
    assert delta_path.is_file(), "delta manifest was not written"
    delta = json.loads(delta_path.read_text(encoding="utf-8"))
    for key in DELTA_KEYS:
        assert key in delta, f"delta missing bucket {key!r}"
        assert isinstance(delta[key], list), f"bucket {key!r} must be a list"
    # Non-vacuity: the v0.21.5-only module is a candidate-only file vs current.
    assert NEW_MODULE in delta["only_candidate"], (
        f"{NEW_MODULE} should be candidate-only; got "
        f"{delta['only_candidate'][:5]}")
    assert delta["identical"], "expected some byte-identical files"
    assert delta["differing"], "expected some differing files"
    assert delta.get("pin") == EXPECTED_REVISION


def test_check_is_idempotent(candidate):
    root = candidate["root"]
    before = _tree_hash(root, exclude=("V0_21_5_DELTA.json",))
    res = _run_check(root)
    assert res.returncode == 0, res.stderr
    after = _tree_hash(root, exclude=("V0_21_5_DELTA.json",))
    assert after == before, "re-running --check changed the candidate tree"
    # The delta itself must be reproducible (no volatile timestamps/paths).
    first = (root / "V0_21_5_DELTA.json").read_bytes()
    _run_check(root)
    assert (root / "V0_21_5_DELTA.json").read_bytes() == first, (
        "delta manifest is not reproducible across runs")

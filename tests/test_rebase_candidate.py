# tests/test_rebase_candidate.py
"""Candidate-generation gate for the v0.21.5 1:1 rebase (Task 4).

``scripts/rebase_v0215.sh`` must stage the pinned upstream (v0.21.5 @
``69948c0057``), run the two deterministic engines (``qubit_rebrand.py`` +
``qubit_debrand.py``), emit a delta manifest with the four bucket keys, and
never write the live product tree.

The candidate is generated under a temp root (``QUBIT_CANDIDATE_ROOT``); the
pinned upstream is read-only input.  Portability: the pin/reference paths are
overridable via ``QUBIT_PINNED_SOURCE`` / ``QUBIT_REFERENCE_HOME`` and the
build-dependent tests **skip** with an explicit reason when neither source is
present, so the suite is not tied to one machine.  Set
``QUBIT_TEST_CANDIDATE_ROOT`` to a persistent path to reuse a built candidate.
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

#: Still-valid non-vacuity anchor for the delta shape.  The deterministic engine
#: leaves the upstream-named plugin tree untouched, so the current tree (which
#: ships it rebranded as ``qubit-achievements``) makes this path candidate-only.
#: The former anchor ``qubit_state_health.py`` now exists in both trees and is no
#: longer candidate-only.
CANDIDATE_ONLY_ANCHOR = "plugins/hermes-achievements/README.md"

EXPECTED_REVISION = "69948c005791d4d0e51670ca015238c2c7663749"

PINNED_SOURCE = Path(os.environ.get(
    "QUBIT_PINNED_SOURCE", "/tmp/qubit-1to1-20260925-082150/ref"))
REFERENCE_HOME = Path(os.environ.get(
    "QUBIT_REFERENCE_HOME", "/home/trick/.hermes/hermes-agent"))
LIVE_ROOT = Path(os.environ.get(
    "QUBIT_LIVE_ROOT", "/home/trick/Projects/Repos/qubit-voicebridge"))

MARKER_NAME = ".rebase_v0215_state.json"


def _sources_available() -> bool:
    """True when a pinned source can be staged (extracted tree or reference git)."""
    if PINNED_SOURCE.is_dir():
        return True
    return (REFERENCE_HOME / ".git").exists() or (REFERENCE_HOME / "HEAD").exists()


def _skip_reason() -> str:
    return (f"pinned source unavailable: set QUBIT_PINNED_SOURCE (tried "
            f"{PINNED_SOURCE}) or QUBIT_REFERENCE_HOME (tried {REFERENCE_HOME})")


def _tree_hash(root: Path, exclude_names=()) -> str:
    """sha256 over ``(relative-path, content-sha256)`` pairs, path-sorted."""
    lines: list[str] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if "__pycache__" in rel or p.name in exclude_names:
            continue
        digest = hashlib.sha256(p.read_bytes()).hexdigest()
        lines.append(f"{rel}\0{digest}\n")
    h = hashlib.sha256()
    for line in lines:
        h.update(line.encode("utf-8"))
    return h.hexdigest()


def _count_files(root: Path) -> int:
    return sum(1 for p in root.rglob("*")
               if p.is_file() and "__pycache__" not in p.relative_to(root).as_posix())


def _env(root: Path, extra=None) -> dict:
    env = dict(os.environ)
    env["QUBIT_CANDIDATE_ROOT"] = str(root)
    env["QUBIT_DELTA_OUT"] = str(root / "V0_21_5_DELTA.json")
    if extra:
        env.update(extra)
    return env


def _run(root: Path, flag: str, extra=None, timeout=1800):
    return subprocess.run(
        ["bash", str(SCRIPT), flag],
        cwd=str(REPO), env=_env(root, extra), capture_output=True, text=True,
        timeout=timeout,
    )


def test_help_starts_with_usage():
    res = subprocess.run(
        ["bash", str(SCRIPT), "--help"], cwd=str(REPO),
        capture_output=True, text=True, timeout=60,
    )
    assert res.returncode == 0, res.stderr
    first = res.stdout.splitlines()[0]
    assert not first.startswith("!"), f"--help leaked the shebang: {first!r}"
    assert "rebase_v0215.sh" in first, first


@pytest.fixture(scope="module")
def candidate(tmp_path_factory):
    if not SCRIPT.is_file():
        pytest.fail(f"missing {SCRIPT}")
    if not _sources_available():
        pytest.skip(_skip_reason())
    base = tmp_path_factory.mktemp("cand")
    a, b = base / "a", base / "b"
    a_res = _run(a, "--force")
    b_res = _run(b, "--force")
    results = {"a_root": a, "b_root": b, "a_res": a_res, "b_res": b_res}
    for key, root in (("a", a), ("b", b)):
        rt = root / "qubit_runtime"
        results[f"hash_{key}"] = _tree_hash(rt) if rt.is_dir() else ""
        results[f"files_{key}"] = _count_files(rt) if rt.is_dir() else 0
    return results


def test_build_exits_zero(candidate):
    for key in ("a", "b"):
        res = candidate[f"{key}_res"]
        assert res.returncode == 0, (
            f"build {key} failed rc={res.returncode}\nSTDOUT:\n{res.stdout}\n"
            f"STDERR:\n{res.stderr}")


def test_candidate_has_new_upstream_module(candidate):
    module = candidate["a_root"] / "qubit_runtime" / NEW_MODULE
    assert module.is_file(), f"missing candidate module {module}"


def test_candidate_imports_resolve(candidate):
    pkg = candidate["a_root"] / "qubit_runtime"
    home = candidate["a_root"] / "_throwaway_home"
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
        cwd=str(candidate["a_root"]), env=env, capture_output=True, text=True,
        timeout=300,
    )
    assert res.returncode == 0, f"import smoke failed:\n{res.stderr}"
    assert "IMPORT_OK" in res.stdout
    assert not (Path(env["HOME"]) / ".hermes").exists(), (
        "candidate import created the read-only reference home")


def test_delta_manifest_shape(candidate):
    delta_path = candidate["a_root"] / "V0_21_5_DELTA.json"
    assert delta_path.is_file(), "delta manifest was not written"
    delta = json.loads(delta_path.read_text(encoding="utf-8"))
    for key in DELTA_KEYS:
        assert key in delta, f"delta missing bucket {key!r}"
        assert isinstance(delta[key], list), f"bucket {key!r} must be a list"
    # Non-vacuity: the candidate keeps the upstream-named plugin tree, which the
    # current tree ships rebranded, so this path is candidate-only vs current.
    assert CANDIDATE_ONLY_ANCHOR in delta["only_candidate"], (
        f"{CANDIDATE_ONLY_ANCHOR} should be candidate-only; got "
        f"{delta['only_candidate'][:5]}")
    assert delta["identical"], "expected some byte-identical files"
    assert delta["differing"], "expected some differing files"
    assert delta.get("pin") == EXPECTED_REVISION


def test_two_independent_builds_are_byte_identical(candidate):
    """Finding 4: two separate builds of the same pin must agree byte-for-byte."""
    assert candidate["files_a"] > 0 and candidate["files_b"] > 0, (
        "a build produced no files")
    assert candidate["files_a"] == candidate["files_b"], (
        f"file count differs: {candidate['files_a']} != {candidate['files_b']}")
    assert candidate["hash_a"] == candidate["hash_b"], (
        f"candidate trees differ:\n  a={candidate['hash_a']}\n  b={candidate['hash_b']}")


def test_check_reuses_valid_candidate_and_is_idempotent(candidate):
    root = candidate["a_root"]
    before = _tree_hash(root, exclude_names=("V0_21_5_DELTA.json",))
    delta_path = root / "V0_21_5_DELTA.json"
    delta_before = delta_path.read_bytes()
    res = _run(root, "--check")
    assert res.returncode == 0, res.stderr
    assert "reusing" in res.stdout, f"--check did not take the reuse path:\n{res.stdout}"
    assert _tree_hash(root, exclude_names=("V0_21_5_DELTA.json",)) == before, (
        "re-running --check changed the candidate tree")
    assert delta_path.read_bytes() == delta_before, (
        "delta manifest is not reproducible across runs")


def test_check_rebuilds_stale_or_partial_candidate(candidate):
    """Finding 1: a marker-less/corrupt candidate is rebuilt, not reused."""
    root = candidate["b_root"]
    target = root / "qubit_runtime" / NEW_MODULE
    good = candidate["a_root"] / "qubit_runtime" / NEW_MODULE
    # Simulate a crash after rebrand but before debrand/validation: content is
    # wrong and the completion marker is absent.  Both conditions must force a
    # rebuild rather than a silent reuse.
    target.write_bytes(b"# partial build, no marker\n")
    (root / MARKER_NAME).unlink(missing_ok=True)
    res = _run(root, "--check")
    assert res.returncode == 0, res.stderr
    assert "reusing" not in res.stdout, f"stale candidate was reused:\n{res.stdout}"
    assert target.read_bytes() == good.read_bytes(), (
        "stale candidate was not rebuilt to the correct content")
    marker = json.loads((root / MARKER_NAME).read_text(encoding="utf-8"))
    assert marker["stages"] == ["rebrand", "debrand", "validated"]
    assert marker["engine_fingerprint"]
    assert marker["runtime_sha256"]


def test_refuses_to_run_from_live_tree(tmp_path):
    """Finding 2: the live-root guard refuses (no build, no writes)."""
    res = _run(tmp_path / "cand", "--check",
               extra={"QUBIT_LIVE_ROOT": str(REPO)})
    assert res.returncode != 0
    assert "live product tree" in res.stderr, res.stderr


def test_refuses_candidate_root_inside_live_tree(tmp_path):
    """Finding 2: a candidate root inside the live tree is refused."""
    if not LIVE_ROOT.is_dir():
        pytest.skip(f"no live root at {LIVE_ROOT}")
    probe = LIVE_ROOT / ".rebase-guard-probe"
    res = _run(probe, "--check")
    assert res.returncode != 0
    assert "inside the live product tree" in res.stderr, res.stderr
    assert not probe.exists(), "guard created a directory inside the live tree"


def test_refuses_non_tmp_candidate_root(tmp_path):
    """Finding 2: a candidate root outside /tmp is refused (no writes)."""
    home = os.environ.get("HOME")
    if not home:
        pytest.skip("HOME unset; cannot build a non-tmp probe path")
    home_real = str(Path(home).resolve())
    if home_real.startswith(("/tmp", "/var/tmp")):
        pytest.skip(f"HOME is under a tmp dir ({home_real}); no non-tmp probe")
    probe = Path(home) / ".rebase-non-tmp-probe"
    res = _run(probe, "--check",
               extra={"QUBIT_DELTA_OUT": str(tmp_path / "delta.json")})
    assert res.returncode != 0
    assert "must live under /tmp" in res.stderr, res.stderr
    assert not probe.exists(), "guard created a non-tmp candidate root"


def test_candidate_never_written_to_live_tree():
    """Finding 2: the v0.21.5-only module must not appear in the live runtime."""
    if not LIVE_ROOT.is_dir():
        pytest.skip(f"no live root at {LIVE_ROOT}")
    assert not (LIVE_ROOT / "qubit_runtime" / NEW_MODULE).exists(), (
        "the candidate module leaked into the live product tree")


def test_declared_live_root_untouched_on_reuse(candidate, tmp_path):
    """Finding 2: a normal run writes only the candidate root."""
    sandbox = tmp_path / "sandbox_live"
    (sandbox / "qubit_runtime").mkdir(parents=True)
    sentinel = sandbox / "qubit_runtime" / "SENTINEL"
    sentinel.write_text("untouched", encoding="utf-8")
    res = _run(candidate["a_root"], "--check",
               extra={"QUBIT_LIVE_ROOT": str(sandbox)})
    assert res.returncode == 0, res.stderr
    assert sentinel.read_text(encoding="utf-8") == "untouched"
    assert sorted(p.name for p in (sandbox / "qubit_runtime").iterdir()) == ["SENTINEL"]
